"""Sessão de edição de um documento: histórico de deltas, desfazer/refazer e rascunho.

Cada operação vira um Delta com o recorte de ANTES e DEPOIS da caixa que ela
alterou e a "pegada" (os pixels que ela pretendia editar). Assim:
  - desfazer/refazer é só colar recortes (vale para PatchMatch e IA sem recomputar);
  - o rascunho em disco é a lista de deltas (queda do servidor não perde trabalho);
  - as máscaras saem dos deltas.

Invariante garantido em ``aplicar``: nenhum pixel fora da pegada muda. Logo, todo
pixel diferente do original está dentro da pegada de alguma operação categorizada.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .registro import Item


class ErroOperacao(ValueError):
    pass


@dataclass
class Resultado:
    """O que uma operação devolve: o novo conteúdo da caixa e a pegada."""

    y0: int
    y1: int
    x0: int
    x1: int
    recorte: np.ndarray  # (y1-y0, x1-x0, 3) uint8
    pegada: np.ndarray  # (y1-y0, x1-x0) bool
    restaura: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class Delta:
    id: str
    tipo: str
    categoria: str | None
    descricao: str
    params: dict
    y0: int
    y1: int
    x0: int
    x1: int
    antes: np.ndarray
    depois: np.ndarray
    pegada: np.ndarray
    restaura: bool
    meta: dict
    criado: str

    def resumo(self) -> dict:
        return {"id": self.id, "tipo": self.tipo, "categoria": self.categoria,
                "descricao": self.descricao, "params": self.params, "meta": self.meta,
                "bbox": [self.x0, self.y0, self.x1, self.y1], "restaura": self.restaura,
                "pixels_pegada": int(self.pegada.sum()),
                "pixels_alterados": int((self.antes != self.depois).any(2).sum()),
                "criado": self.criado}


class Sessao:
    def __init__(self, item: Item, base: np.ndarray, pasta_rascunho: Path, ids_categoria: dict[str, int]):
        self.item = item
        self.base = base
        self.base.setflags(write=False)
        self.atual = base.copy()
        self.pasta = Path(pasta_rascunho)
        self.ids_categoria = ids_categoria
        self.deltas: list[Delta] = []
        self.refazer_pilha: list[Delta] = []
        self.lock = threading.RLock()
        self.versao = 0  # muda a cada alteração (o navegador usa para cache)
        self._carregar_rascunho()

    # ------------------------------------------------------------------ edição
    def aplicar(self, tipo: str, categoria: str | None, descricao: str, params: dict,
                res: Resultado) -> Delta:
        with self.lock:
            if not res.restaura:
                if categoria not in self.ids_categoria:
                    raise ErroOperacao("escolha a categoria do que está sendo editado antes de aplicar")
                if categoria == "outro" and not descricao.strip():
                    raise ErroOperacao("a categoria 'outro' exige uma descrição")
            H, W = self.atual.shape[:2]
            y0, y1, x0, x1 = (int(v) for v in (res.y0, res.y1, res.x0, res.x1))
            if not (0 <= y0 < y1 <= H and 0 <= x0 < x1 <= W):
                raise ErroOperacao("a operação caiu fora da imagem")
            antes = self.atual[y0:y1, x0:x1].copy()
            pegada = np.asarray(res.pegada, bool)
            recorte = np.asarray(res.recorte, np.uint8)
            if recorte.shape != antes.shape or pegada.shape != antes.shape[:2]:
                raise ErroOperacao("resultado da operação com tamanho errado")
            depois = np.where(pegada[..., None], recorte, antes)  # nada fora da pegada muda
            if np.array_equal(depois, antes):
                raise ErroOperacao("a operação não alterou nenhum pixel")
            delta = Delta(id=uuid.uuid4().hex[:10], tipo=tipo,
                          categoria=None if res.restaura else categoria,
                          descricao=descricao.strip(), params=params, y0=y0, y1=y1, x0=x0, x1=x1,
                          antes=antes, depois=depois, pegada=pegada, restaura=res.restaura,
                          meta=res.meta, criado=time.strftime("%Y-%m-%dT%H:%M:%S"))
            self.atual[y0:y1, x0:x1] = depois
            self._gravar_delta(delta)
            descartados, self.refazer_pilha = self.refazer_pilha, []
            self.deltas.append(delta)
            self.versao += 1
            self._gravar_estado()
            for d in descartados:
                self._apagar_delta(d)
            return delta

    def desfazer(self) -> Delta | None:
        with self.lock:
            if not self.deltas:
                return None
            d = self.deltas.pop()
            self.atual[d.y0:d.y1, d.x0:d.x1] = d.antes
            self.refazer_pilha.append(d)
            self.versao += 1
            self._gravar_estado()
            return d

    def refazer(self) -> Delta | None:
        with self.lock:
            if not self.refazer_pilha:
                return None
            d = self.refazer_pilha.pop()
            self.atual[d.y0:d.y1, d.x0:d.x1] = d.depois
            self.deltas.append(d)
            self.versao += 1
            self._gravar_estado()
            return d

    def recomecar(self) -> None:
        """Volta ao original e apaga o rascunho (usado ao descartar e depois de salvar)."""
        with self.lock:
            self.atual = self.base.copy()
            self.deltas, self.refazer_pilha = [], []
            self.versao += 1
            shutil.rmtree(self.pasta, ignore_errors=True)

    # ------------------------------------------------------------------ rótulos
    def mascaras(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(alterados, regiao, classes): pixels que mudaram, área editada e categoria por pixel."""
        with self.lock:
            H, W = self.base.shape[:2]
            alterados = (self.atual != self.base).any(axis=2)
            regiao = np.zeros((H, W), bool)
            restaurado = np.zeros((H, W), bool)
            classes = np.zeros((H, W), np.uint8)
            for d in self.deltas:
                if d.restaura:
                    restaurado[d.y0:d.y1, d.x0:d.x1] |= d.pegada
                    continue
                regiao[d.y0:d.y1, d.x0:d.x1] |= d.pegada
                classes[d.y0:d.y1, d.x0:d.x1][d.pegada] = self.ids_categoria[d.categoria]
            # o que a borracha devolveu ao original deixa de ser área editada
            regiao &= ~(restaurado & ~alterados)
            regiao |= alterados
            classes[~regiao] = 0
            return alterados, regiao, classes

    def categorias(self) -> list[str]:
        _, regiao, classes = self.mascaras()
        presentes = set(np.unique(classes[regiao]).tolist()) - {0}
        return [c for c, i in self.ids_categoria.items() if i in presentes]

    def pode_salvar(self) -> tuple[bool, str]:
        with self.lock:
            if not any(not d.restaura for d in self.deltas):
                return False, "nenhuma edição feita"
            if not (self.atual != self.base).any():
                return False, "a imagem está igual ao original"
            if not self.categorias():
                return False, "marque o que foi editado"
            return True, ""

    def historico(self) -> list[dict]:
        return [d.resumo() for d in self.deltas]

    def estado(self) -> dict:
        ok, motivo = self.pode_salvar()
        return {"versao": self.versao, "historico": self.historico(),
                "pode_refazer": bool(self.refazer_pilha), "categorias": self.categorias(),
                "pode_salvar": ok, "motivo": motivo}

    # ------------------------------------------------------------------ rascunho
    def _gravar_delta(self, d: Delta) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        tmp = self.pasta / f"{d.id}.tmp.npz"
        np.savez_compressed(tmp, antes=d.antes, depois=d.depois, pegada=d.pegada)
        os.replace(tmp, self.pasta / f"{d.id}.npz")

    def _apagar_delta(self, d: Delta) -> None:
        try:
            (self.pasta / f"{d.id}.npz").unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _meta(d: Delta) -> dict:
        return {"id": d.id, "tipo": d.tipo, "categoria": d.categoria, "descricao": d.descricao,
                "params": d.params, "bbox": [d.y0, d.y1, d.x0, d.x1], "restaura": d.restaura,
                "meta": d.meta, "criado": d.criado}

    def _gravar_estado(self) -> None:
        if not self.deltas and not self.refazer_pilha:
            shutil.rmtree(self.pasta, ignore_errors=True)
            return
        self.pasta.mkdir(parents=True, exist_ok=True)
        estado = {"sha_pixels": self.item.sha_pixels, "split": self.item.split,
                  "ativos": [self._meta(d) for d in self.deltas],
                  "refazer": [self._meta(d) for d in self.refazer_pilha]}
        tmp = self.pasta / "estado.tmp"
        tmp.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.pasta / "estado.json")

    def _carregar_rascunho(self) -> None:
        arq = self.pasta / "estado.json"
        if not arq.is_file():
            return
        estado = json.loads(arq.read_text(encoding="utf-8"))
        if estado.get("sha_pixels") != self.item.sha_pixels or estado.get("split") != self.item.split:
            raise ErroOperacao(f"rascunho em {self.pasta} não pertence a este documento")

        def ler(m: dict) -> Delta:
            with np.load(self.pasta / f"{m['id']}.npz") as z:
                y0, y1, x0, x1 = m["bbox"]
                return Delta(id=m["id"], tipo=m["tipo"], categoria=m["categoria"],
                             descricao=m.get("descricao", ""), params=m.get("params", {}),
                             y0=y0, y1=y1, x0=x0, x1=x1, antes=z["antes"], depois=z["depois"],
                             pegada=z["pegada"], restaura=m.get("restaura", False),
                             meta=m.get("meta", {}), criado=m.get("criado", ""))

        for m in estado.get("ativos", []):
            d = ler(m)
            if not np.array_equal(self.atual[d.y0:d.y1, d.x0:d.x1], d.antes):
                raise ErroOperacao(f"rascunho de {self.item.nome} está inconsistente; descarte-o")
            self.atual[d.y0:d.y1, d.x0:d.x1] = d.depois
            self.deltas.append(d)
        self.refazer_pilha = [ler(m) for m in estado.get("refazer", [])]
