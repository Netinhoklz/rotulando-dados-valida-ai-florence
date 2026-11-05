"""Sessão de edição de um documento, com CAMADAS.

O documento é o original (fundo, imutável) mais uma pilha de camadas RGBA do
tamanho da imagem. Cada camada tem uma CATEGORIA (valor, data, nome...), um nome,
visibilidade e opacidade. A imagem editada é a composição das camadas visíveis.

Operações de pixel são calculadas sobre a composição (como o "amostrar todas as
camadas" do Photoshop) e gravadas na camada ativa: onde a operação mexe (pegada),
a camada recebe o resultado com alfa 255. A borracha tira alfa da camada ativa.

Histórico: cada entrada é uma lista de passos (pixels de uma camada, ou estrutura
da pilha), desfeitos em ordem inversa. Rascunho em disco = estado das camadas +
histórico, então uma queda do servidor não perde nada.

Rótulos: pixels alterados = composição != original; região editada = alfa das
camadas visíveis; classe por pixel = categoria da camada visível mais alta.
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

_VERSAO_RASCUNHO = 2


class ErroOperacao(ValueError):
    pass


@dataclass
class Resultado:
    """O que uma operação devolve: o novo conteúdo da caixa e a pegada."""

    y0: int
    y1: int
    x0: int
    x1: int
    recorte: np.ndarray  # (y1-y0, x1-x0, 3) uint8 — composição desejada na caixa
    pegada: np.ndarray  # (y1-y0, x1-x0) bool
    restaura: bool = False  # borracha: tira da camada em vez de pintar
    meta: dict = field(default_factory=dict)
    alfa_borracha: np.ndarray | None = None  # (h, w) 0..1, só para restaura


@dataclass
class Camada:
    id: str
    nome: str
    categoria: str | None
    descricao: str = ""
    visivel: bool = True
    opacidade: float = 1.0
    rgba: np.ndarray | None = None  # (H, W, 4) uint8
    # auto: camada interna, criada pela edição feita direto na imagem (o "Fundo"), uma por tipo da edição, só para a
    # máscara de classes. O painel não a mostra: a pessoa vê a edição no Fundo, como no Photoshop
    auto: bool = False
    versao: int = 0  # sobe a cada mudança de pixels (cache das estatísticas e das miniaturas)
    _cache: tuple | None = field(default=None, repr=False, compare=False)

    def estatistica(self):
        """(caixa, pixels) do conteúdo; recalculado só quando a camada muda."""
        if self._cache is None or self._cache[0] != self.versao:
            a = self.rgba[..., 3] > 0
            linhas = np.flatnonzero(a.any(1))
            if not len(linhas):
                self._cache = (self.versao, None, 0)
            else:
                colunas = np.flatnonzero(a.any(0))
                caixa = (int(linhas[0]), int(linhas[-1]) + 1, int(colunas[0]), int(colunas[-1]) + 1)
                self._cache = (self.versao, caixa, int(a[caixa[0]:caixa[1], caixa[2]:caixa[3]].sum()))
        return self._cache[1], self._cache[2]

    def props(self) -> dict:
        return {"id": self.id, "nome": self.nome, "categoria": self.categoria, "descricao": self.descricao,
                "visivel": self.visivel, "opacidade": self.opacidade, "auto": self.auto}

    def caixa(self):
        return self.estatistica()[0]


@dataclass
class Entrada:
    """Uma linha do histórico."""

    id: str
    rotulo: str
    tipo: str
    passos: list  # ("pixels", camada_id, (y0,y1,x0,x1), antes, depois) | ("estrutura", antes, depois)
    info: dict
    criado: str

    def resumo(self) -> dict:
        if not hasattr(self, "_px"):
            self._px = sum(int((p[3] != p[4]).any(2).sum()) for p in self.passos if p[0] == "pixels")
        px = self._px
        return {"id": self.id, "rotulo": self.rotulo, "tipo": self.tipo, "pixels_alterados": px,
                "criado": self.criado, **self.info}


def _bbox_uniao(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3])


class Sessao:
    def __init__(self, item: Item, base: np.ndarray, pasta_rascunho: Path, ids_categoria: dict[str, int]):
        self.item = item
        self.base = base
        self.base.setflags(write=False)
        self.H, self.W = base.shape[:2]
        self.atual = base.copy()
        self.pasta = Path(pasta_rascunho)
        self.ids_categoria = ids_categoria
        self.camadas: dict[str, Camada] = {}  # todas (inclusive excluídas citadas no histórico)
        self.ordem: list[str] = []  # pilha atual, de baixo para cima
        self.ativa: str | None = None
        self.historico_: list[Entrada] = []
        self.refazer_pilha: list[Entrada] = []
        self.lock = threading.RLock()
        self.versao = 0
        self._sujas: set[str] = set()
        self._carregar_rascunho()

    # ------------------------------------------------------------------ composição
    def _composto(self, bbox, ids) -> np.ndarray:
        """A base com as camadas ``ids`` por cima (as visíveis, de baixo para cima), na caixa."""
        y0, y1, x0, x1 = bbox
        c = self.base[y0:y1, x0:x1].astype(np.float32)
        for cid in ids:
            cam = self.camadas[cid]
            if not cam.visivel or cam.opacidade <= 0:
                continue
            r = cam.rgba[y0:y1, x0:x1]
            a = r[..., 3:4].astype(np.float32) * (cam.opacidade / 255.0)
            if not a.any():
                continue
            c = c * (1 - a) + r[..., :3].astype(np.float32) * a
        return np.clip(c + 0.5, 0, 255).astype(np.uint8)

    def _compor(self, bbox=None) -> None:
        y0, y1, x0, x1 = bbox if bbox else (0, self.H, 0, self.W)
        if y1 <= y0 or x1 <= x0:
            return
        self.atual[y0:y1, x0:x1] = self._composto((y0, y1, x0, x1), self.ordem)

    # ------------------------------------------------------------------ camadas
    def _tocou(self, cid: str) -> None:
        """Pixels da camada mudaram: vai para o rascunho e invalida o cache."""
        self._sujas.add(cid)
        if cid in self.camadas:
            self.camadas[cid].versao += 1

    def _nova_camada(self, categoria: str | None, nome: str = "", descricao: str = "") -> Camada:
        if categoria is not None and categoria not in self.ids_categoria:
            raise ErroOperacao(f"categoria inválida: {categoria}")
        n = 1 + sum(1 for c in self.camadas.values() if c.categoria == categoria)
        cam = Camada(id=uuid.uuid4().hex[:8], nome=nome or f"{categoria or 'camada'} {n}", categoria=categoria,
                     descricao=descricao, rgba=np.zeros((self.H, self.W, 4), np.uint8))
        self.camadas[cam.id] = cam
        self._tocou(cam.id)
        return cam

    def _estrutura(self) -> dict:
        return {"ordem": list(self.ordem), "ativa": self.ativa,
                "props": {cid: self.camadas[cid].props() for cid in self.camadas}}

    def _restaurar_estrutura(self, e: dict) -> None:
        self.ordem = list(e["ordem"])
        self.ativa = e["ativa"]
        for cid, p in e["props"].items():
            cam = self.camadas[cid]
            cam.nome, cam.categoria, cam.descricao = p["nome"], p["categoria"], p.get("descricao", "")
            cam.visivel, cam.opacidade = p["visivel"], p["opacidade"]
            cam.auto = bool(p.get("auto", False))

    def _n_auto(self) -> int:
        """Quantas camadas internas há embaixo da pilha (as da pessoa vêm sempre acima delas)."""
        n = 0
        while n < len(self.ordem) and self.camadas[self.ordem[n]].auto:
            n += 1
        return n

    def _caixa_camadas(self, ids) -> tuple | None:
        bb = None
        for cid in ids:
            if cid in self.camadas:
                bb = _bbox_uniao(bb, self.camadas[cid].caixa())
        return bb

    def acao_camada(self, acao: str, camada: str | None = None, **kw) -> tuple | None:
        """Mudanças na pilha (entram no histórico). Devolve a caixa da imagem que mudou."""
        with self.lock:
            antes = self._estrutura()
            alvo = self.camadas.get(camada) if camada else (self.camadas.get(self.ativa) if self.ativa else None)
            passos_px = []
            afetadas = []
            rotulo = acao
            if acao == "criar":
                cat = kw.get("categoria")
                cam = self._nova_camada(cat, str(kw.get("nome") or ""), str(kw.get("descricao") or ""))
                cam.auto = bool(kw.get("auto"))
                at = self.camadas.get(self.ativa) if self.ativa in self.ordem else None
                if cam.auto or at is None or at.auto:
                    # edição direto na imagem, ou camada nova com o Fundo ativo: logo acima do fundo (e das internas)
                    pos = self._n_auto()
                else:
                    pos = self.ordem.index(self.ativa) + 1
                self.ordem.insert(pos, cam.id)
                self.ativa = cam.id
                rotulo = f"nova camada '{cam.nome}'"
            elif acao == "ativar" and camada == "fundo":  # o Fundo (a imagem) volta a receber as edições
                self.ativa = None
                self._gravar_estado()
                return None
            elif acao == "ativar":
                if alvo is None or alvo.id not in self.ordem:
                    raise ErroOperacao("camada não encontrada")
                self.ativa = alvo.id
                self._gravar_estado()
                return None  # só seleção: não entra no histórico
            else:
                if alvo is None or alvo.id not in self.ordem:
                    raise ErroOperacao("camada não encontrada")
                afetadas = [alvo.id]
                if acao == "excluir":
                    i = self.ordem.index(alvo.id)
                    self.ordem.remove(alvo.id)
                    self.ativa = self.ordem[min(i, len(self.ordem) - 1)] if self.ordem else None
                    rotulo = f"excluir '{alvo.nome}'"
                elif acao == "props":
                    if "nome" in kw and str(kw["nome"]).strip():
                        alvo.nome = str(kw["nome"]).strip()[:60]
                    if "categoria" in kw:
                        if kw["categoria"] not in self.ids_categoria:
                            raise ErroOperacao("categoria inválida")
                        alvo.categoria = kw["categoria"]
                    if "descricao" in kw:
                        alvo.descricao = str(kw["descricao"])[:200]
                    if "visivel" in kw:
                        alvo.visivel = bool(kw["visivel"])
                    if "opacidade" in kw:
                        alvo.opacidade = float(min(1.0, max(0.0, float(kw["opacidade"]))))
                    rotulo = f"propriedades de '{alvo.nome}'"
                elif acao == "ordem":
                    pos = int(kw.get("posicao", 0))
                    self.ordem.remove(alvo.id)
                    minimo = 0 if alvo.auto else self._n_auto()  # nenhuma camada da pessoa embaixo do Fundo
                    self.ordem.insert(max(minimo, min(len(self.ordem), pos)), alvo.id)
                    rotulo = f"mover '{alvo.nome}' na pilha"
                elif acao == "duplicar":
                    nova = self._nova_camada(alvo.categoria, f"{alvo.nome} cópia", alvo.descricao)
                    nova.rgba[:] = alvo.rgba
                    nova.visivel, nova.opacidade = alvo.visivel, alvo.opacidade
                    self.ordem.insert(self._n_auto() if alvo.auto else self.ordem.index(alvo.id) + 1, nova.id)
                    self.ativa = nova.id
                    afetadas.append(nova.id)
                    rotulo = f"duplicar '{alvo.nome}'"
                elif acao == "mesclar_abaixo":
                    i = self.ordem.index(alvo.id)
                    if i == 0:
                        raise ErroOperacao("não há camada abaixo para mesclar")
                    baixo = self.camadas[self.ordem[i - 1]]
                    bb = alvo.caixa()
                    if bb:
                        y0, y1, x0, x1 = bb
                        ab = baixo.rgba[y0:y1, x0:x1].copy()
                        a = alvo.rgba[y0:y1, x0:x1, 3:4].astype(np.float32) / 255 * alvo.opacidade * alvo.visivel
                        rgb = ab[..., :3] * (1 - a) + alvo.rgba[y0:y1, x0:x1, :3] * a
                        alfa = ab[..., 3:4].astype(np.float32) / 255
                        alfa = alfa + a * (1 - alfa)
                        dep = np.concatenate([np.clip(rgb + .5, 0, 255), np.clip(alfa * 255 + .5, 0, 255)], 2).astype(np.uint8)
                        passos_px.append(("pixels", baixo.id, bb, ab, dep))
                        baixo.rgba[y0:y1, x0:x1] = dep
                        self._tocou(baixo.id)
                    self.ordem.remove(alvo.id)
                    self.ativa = baixo.id
                    rotulo = f"mesclar '{alvo.nome}' em '{baixo.nome}'"
                else:
                    raise ErroOperacao(f"ação de camada desconhecida: {acao}")
            depois = self._estrutura()
            ent = Entrada(uuid.uuid4().hex[:10], rotulo, "camada", passos_px + [("estrutura", antes, depois)],
                          {"acao": acao}, time.strftime("%Y-%m-%dT%H:%M:%S"))
            self._registrar(ent)
            bb = self._caixa_camadas(afetadas + [p[1] for p in passos_px])
            self._compor(bb) if bb else None
            return bb

    def camada_via(self, sel, recortar: bool = False, categoria: str | None = None,
                   preenchimento: Resultado | None = None, params: dict | None = None) -> tuple | None:
        """Ctrl+J / Ctrl+Shift+J do Photoshop ("Camada via cópia" / "Camada via recorte"): o que a seleção pega da
        camada ativa vai para uma camada nova, no mesmo lugar, logo acima dela, que passa a ser a ativa. Com o Fundo
        ativo, a imagem sem as camadas da pessoa (sem seleção: o Fundo inteiro, "Fundo cópia"), na ``categoria``.
        Recortar tira os pixels da camada de origem; no Fundo, o buraco recebe ``preenchimento`` (o apagar com
        preenchimento), no mesmo passo do histórico. A cópia em si não é edição: ver ``mascaras``."""
        with self.lock:
            at = self.camadas.get(self.ativa) if self.ativa in self.ordem else None
            no_fundo = at is None or at.auto
            com_sel = sel is not None and not sel.vazia
            if recortar and not com_sel:
                raise ErroOperacao("selecione o trecho que vai para a camada nova")
            if no_fundo and categoria not in self.ids_categoria:
                raise ErroOperacao("escolha o tipo da edição antes")
            a = sel.completa() if com_sel else np.ones((self.H, self.W), np.float32)
            if no_fundo:
                fundo = self._composto((0, self.H, 0, self.W), [c for c in self.ordem if self.camadas[c].auto])
                rgba = np.dstack([fundo, (a * 255 + 0.5).astype(np.uint8)])
            else:
                rgba = at.rgba.copy()
                rgba[..., 3] = (rgba[..., 3] * a + 0.5).astype(np.uint8)
            rgba[rgba[..., 3] == 0] = 0
            if not rgba[..., 3].any():
                raise ErroOperacao("a área selecionada está vazia nesta camada")
            n_antes = len(self.historico_)
            if recortar and no_fundo:  # o buraco no Fundo, por baixo da camada nova (aparece quando ela sair dali)
                if preenchimento is None:
                    raise ErroOperacao("falta o preenchimento do buraco")
                self.ativa = None
                self.aplicar("preencher", categoria, "", dict(params or {}), preenchimento)
            antes = self._estrutura()
            nome = "Fundo cópia" if no_fundo and not com_sel else "" if no_fundo or com_sel else f"{at.nome} cópia"
            cam = self._nova_camada(categoria if no_fundo else at.categoria, nome, "" if no_fundo else at.descricao)
            cam.rgba[:] = rgba
            if not no_fundo:
                cam.opacidade = at.opacidade
            self.ordem.insert(self._n_auto() if no_fundo else self.ordem.index(at.id) + 1, cam.id)
            passos_px = []
            if recortar and not no_fundo:
                y0, y1, x0, x1 = sel.y0, sel.y1, sel.x0, sel.x1
                ant = at.rgba[y0:y1, x0:x1].copy()
                dep = ant.copy()
                dep[..., 3] = (ant[..., 3] * (1 - sel.alpha) + 0.5).astype(np.uint8)
                dep[dep[..., 3] == 0] = 0
                at.rgba[y0:y1, x0:x1] = dep
                self._tocou(at.id)
                passos_px.append(("pixels", at.id, (y0, y1, x0, x1), ant, dep))
            self.ativa = cam.id
            passos = [("estrutura", antes, self._estrutura())] + passos_px
            tipo, info = "camada", {"acao": "via_recorte" if recortar else "via_copia"}
            rotulo = "Camada via recorte" if recortar else "Fundo cópia" if nome == "Fundo cópia" else "Camada via cópia"
            if len(self.historico_) > n_antes:  # o preenchimento do buraco e a camada nova: um passo só
                prev = self.historico_.pop()
                self._apagar_entrada(prev)
                passos = prev.passos + passos
                tipo, info = prev.tipo, {**prev.info, **info}
            self._registrar(Entrada(uuid.uuid4().hex[:10], rotulo, tipo, passos, info,
                                    time.strftime("%Y-%m-%dT%H:%M:%S")))
            bb = cam.caixa()
            for p in passos:
                if p[0] == "pixels":
                    bb = _bbox_uniao(bb, p[2])
            self._compor(bb)
            return bb

    def garantir_camada(self, categoria: str | None, descricao: str = "") -> Camada:
        """Camada para receber a edição: a ativa, se for da categoria. Com o Fundo ativo (a imagem), a camada interna
        daquela categoria, criada na primeira edição (``auto``: não aparece no painel). Com uma camada da pessoa
        ativa, mas de outra categoria, a mais alta dessa categoria."""
        if categoria not in self.ids_categoria:
            raise ErroOperacao("escolha a categoria do que está sendo editado antes de aplicar")
        if categoria == "outro" and not descricao.strip() and not (
                self.ativa and self.camadas[self.ativa].categoria == "outro" and self.camadas[self.ativa].descricao):
            raise ErroOperacao("a categoria 'outro' exige uma descrição")
        at = self.camadas.get(self.ativa) if self.ativa in self.ordem else None
        if at is not None and at.categoria == categoria:
            return at
        da_pessoa = at is not None and not at.auto
        for cid in reversed(self.ordem):
            c = self.camadas[cid]
            if c.categoria == categoria and (da_pessoa or c.auto):
                self.ativa = cid
                return c
        self.acao_camada("criar", categoria=categoria, descricao=descricao, auto=True, nome=categoria)
        return self.camadas[self.ativa]

    # ------------------------------------------------------------------ edição
    def aplicar(self, tipo: str, categoria: str | None, descricao: str, params: dict, res: Resultado,
                camada: str | None = None) -> Entrada:
        with self.lock:
            y0, y1, x0, x1 = (int(v) for v in (res.y0, res.y1, res.x0, res.x1))
            if not (0 <= y0 < y1 <= self.H and 0 <= x0 < x1 <= self.W):
                raise ErroOperacao("a operação caiu fora da imagem")
            criou = []
            if camada:
                if camada not in self.ordem:
                    raise ErroOperacao("camada não encontrada")
                cam = self.camadas[camada]
                self.ativa = camada
                if not res.restaura and cam.categoria is None:
                    raise ErroOperacao("defina a categoria da camada antes de editar")
            elif res.restaura:
                cam = self.camadas.get(self.ativa) if self.ativa in self.ordem else None
                if cam is None or cam.auto:  # Fundo ativo: a borracha devolve o original na imagem
                    return self._apagar_do_fundo(tipo, params, res)
            else:
                n_antes = len(self.historico_)
                cam = self.garantir_camada(categoria, descricao)
                criou = self.historico_[n_antes:]  # camada criada automaticamente: vira um passo só com a edição
            if not cam.visivel:
                raise ErroOperacao(f"a camada '{cam.nome}' está oculta; mostre-a para editar")
            if descricao.strip() and cam.categoria == "outro" and not cam.descricao:
                cam.descricao = descricao.strip()
            antes = cam.rgba[y0:y1, x0:x1].copy()
            pegada = np.asarray(res.pegada, bool)
            if pegada.shape != antes.shape[:2]:
                raise ErroOperacao("resultado da operação com tamanho errado")
            depois = antes.copy()
            if res.restaura:
                a = np.asarray(res.alfa_borracha if res.alfa_borracha is not None else pegada, np.float32)
                depois[..., 3] = np.clip(antes[..., 3] * (1 - a) + 0.5, 0, 255).astype(np.uint8)
                depois[depois[..., 3] == 0] = 0
            else:
                recorte = np.asarray(res.recorte, np.uint8)
                if recorte.shape != antes.shape[:2] + (3,):
                    raise ErroOperacao("resultado da operação com tamanho errado")
                if np.array_equal(recorte[pegada], self.atual[y0:y1, x0:x1][pegada]):
                    if criou:
                        self.historico_.pop()
                        self._desfazer_passos(criou[0], inverso=True)
                        self._gravar_estado()
                    raise ErroOperacao("a operação não alterou nenhum pixel")
                depois[pegada, :3] = recorte[pegada]
                depois[pegada, 3] = 255
            if np.array_equal(depois, antes):
                if criou:  # nada mudou: desfaz a camada criada à toa
                    self.historico_.pop()
                    self._desfazer_passos(criou[0], inverso=True)
                    self._gravar_estado()
                raise ErroOperacao("a operação não alterou nenhum pixel")
            cam.rgba[y0:y1, x0:x1] = depois
            self._tocou(cam.id)
            # no Fundo (camadas internas, uma por tipo) a última edição aparece sempre, como numa imagem sem camadas:
            # os mesmos pixels saem das internas que estão acima desta (o recorte já é a composição desejada)
            por_cima = []
            if cam.auto and not res.restaura:
                for cid in self.ordem[self.ordem.index(cam.id) + 1:]:
                    outra = self.camadas[cid]
                    if not outra.auto:
                        break
                    o_antes = outra.rgba[y0:y1, x0:x1].copy()
                    o_depois = o_antes.copy()
                    o_depois[pegada] = 0
                    if not np.array_equal(o_depois, o_antes):
                        outra.rgba[y0:y1, x0:x1] = o_depois
                        self._tocou(cid)
                        por_cima.append(("pixels", cid, (y0, y1, x0, x1), o_antes, o_depois))
            passos_criacao = []
            if criou:
                passos_criacao = self.historico_.pop().passos
            ent = Entrada(uuid.uuid4().hex[:10], str(res.meta.get("rotulo") or tipo), tipo,
                          passos_criacao + [("pixels", cam.id, (y0, y1, x0, x1), antes, depois)] + por_cima,
                          {"camada": cam.id, "nome_camada": cam.nome, "categoria": cam.categoria, "auto": cam.auto,
                           "descricao": cam.descricao, "params": params, "meta": res.meta, "restaura": res.restaura,
                           "bbox": [x0, y0, x1, y1]},
                          time.strftime("%Y-%m-%dT%H:%M:%S"))
            self._registrar(ent)
            self._compor((y0, y1, x0, x1))
            return ent

    def _apagar_do_fundo(self, tipo: str, params: dict, res: Resultado) -> Entrada:
        """Borracha no Fundo: tira a edição de todas as camadas internas (o original volta), num passo só."""
        y0, y1, x0, x1 = (int(v) for v in (res.y0, res.y1, res.x0, res.x1))
        a = np.asarray(res.alfa_borracha if res.alfa_borracha is not None else res.pegada, np.float32)
        passos = []
        for cid in self.ordem:
            cam = self.camadas[cid]
            if not cam.auto or not cam.visivel:
                continue
            antes = cam.rgba[y0:y1, x0:x1].copy()
            depois = antes.copy()
            depois[..., 3] = np.clip(antes[..., 3] * (1 - a) + 0.5, 0, 255).astype(np.uint8)
            depois[depois[..., 3] == 0] = 0
            if not np.array_equal(depois, antes):
                cam.rgba[y0:y1, x0:x1] = depois
                self._tocou(cid)
                passos.append(("pixels", cid, (y0, y1, x0, x1), antes, depois))
        if not passos:
            raise ErroOperacao("não há edição para apagar aqui")
        ent = Entrada(uuid.uuid4().hex[:10], str(res.meta.get("rotulo") or tipo), tipo, passos,
                      {"camada": None, "nome_camada": "Fundo", "categoria": None, "auto": True, "descricao": "",
                       "params": params, "meta": res.meta, "restaura": True, "bbox": [x0, y0, x1, y1]},
                      time.strftime("%Y-%m-%dT%H:%M:%S"))
        self._registrar(ent)
        self._compor((y0, y1, x0, x1))
        return ent

    def deslocar_camada(self, camada: str | None, dx, dy) -> tuple | None:
        """Move todo o conteúdo da camada (ferramenta Mover sem seleção)."""
        with self.lock:
            cid = camada or self.ativa
            if cid not in self.ordem:
                raise ErroOperacao("selecione uma camada")
            cam = self.camadas[cid]
            try:
                dx, dy = int(round(float(dx))), int(round(float(dy)))
            except (TypeError, ValueError):
                raise ErroOperacao("deslocamento inválido")
            bb = cam.caixa()
            if bb is None or (dx == 0 and dy == 0):
                return None
            y0, y1, x0, x1 = bb
            ny0, ny1, nx0, nx1 = max(0, y0 + dy), min(self.H, y1 + dy), max(0, x0 + dx), min(self.W, x1 + dx)
            uy0, uy1, ux0, ux1 = min(y0, ny0), max(y1, ny1), min(x0, nx0), max(x1, nx1)
            antes = cam.rgba[uy0:uy1, ux0:ux1].copy()
            conteudo = cam.rgba[y0:y1, x0:x1].copy()
            cam.rgba[y0:y1, x0:x1] = 0
            if ny1 > ny0 and nx1 > nx0:
                cam.rgba[ny0:ny1, nx0:nx1] = conteudo[ny0 - y0 - dy:ny1 - y0 - dy, nx0 - x0 - dx:nx1 - x0 - dx]
            depois = cam.rgba[uy0:uy1, ux0:ux1].copy()
            self._tocou(cid)
            caixa = (uy0, uy1, ux0, ux1)
            ent = Entrada(uuid.uuid4().hex[:10], f"mover '{cam.nome}'", "mover_camada",
                          [("pixels", cid, caixa, antes, depois)],
                          {"camada": cid, "nome_camada": cam.nome, "categoria": cam.categoria,
                           "params": {"dx": dx, "dy": dy}, "bbox": [ux0, uy0, ux1, uy1]},
                          time.strftime("%Y-%m-%dT%H:%M:%S"))
            self._registrar(ent)
            self._compor(caixa)
            return caixa

    def _registrar(self, ent: Entrada) -> None:
        descartadas, self.refazer_pilha = self.refazer_pilha, []
        self.historico_.append(ent)
        self.versao += 1
        self._gravar_entrada(ent)
        self._gravar_estado()
        for d in descartadas:
            self._apagar_entrada(d)

    def _desfazer_passos(self, ent: Entrada, inverso: bool) -> tuple | None:
        bb = None
        passos = list(reversed(ent.passos)) if inverso else ent.passos
        for p in passos:
            if p[0] == "pixels":
                _, cid, (y0, y1, x0, x1), antes, depois = p
                self.camadas[cid].rgba[y0:y1, x0:x1] = antes if inverso else depois
                self._tocou(cid)
                bb = _bbox_uniao(bb, (y0, y1, x0, x1))
            else:
                _, e_antes, e_depois = p
                mexidas = set(e_antes["ordem"]) ^ set(e_depois["ordem"])
                mexidas |= {c for c in e_antes["props"] if e_antes["props"][c] != e_depois["props"].get(c)}
                self._restaurar_estrutura(e_antes if inverso else e_depois)
                bb = _bbox_uniao(bb, self._caixa_camadas(mexidas))
                if e_antes["ordem"] != e_depois["ordem"]:
                    bb = _bbox_uniao(bb, self._caixa_camadas(set(e_antes["ordem"]) | set(e_depois["ordem"])))
        return bb

    def desfazer(self):
        with self.lock:
            if not self.historico_:
                return None
            ent = self.historico_.pop()
            bb = self._desfazer_passos(ent, inverso=True)
            self.refazer_pilha.append(ent)
            self.versao += 1
            if bb:
                self._compor(bb)
            self._gravar_estado()
            return bb or (0, 0, 0, 0)

    def refazer(self):
        with self.lock:
            if not self.refazer_pilha:
                return None
            ent = self.refazer_pilha.pop()
            bb = self._desfazer_passos(ent, inverso=False)
            self.historico_.append(ent)
            self.versao += 1
            if bb:
                self._compor(bb)
            self._gravar_estado()
            return bb or (0, 0, 0, 0)

    def ir_para(self, n: int):
        """Deixa ``n`` passos aplicados: volta (desfaz) ou avança (refaz) até lá, como clicar num estado do painel
        Histórico do Photoshop. Os passos à frente continuam lá até a próxima edição."""
        with self.lock:
            n = max(0, min(int(n), len(self.historico_) + len(self.refazer_pilha)))
            bb = None
            while len(self.historico_) > n:
                ent = self.historico_.pop()
                bb = _bbox_uniao(bb, self._desfazer_passos(ent, inverso=True))
                self.refazer_pilha.append(ent)
            while len(self.historico_) < n and self.refazer_pilha:
                ent = self.refazer_pilha.pop()
                bb = _bbox_uniao(bb, self._desfazer_passos(ent, inverso=False))
                self.historico_.append(ent)
            self.versao += 1
            if bb:
                self._compor(bb)
            self._gravar_estado()
            return bb or (0, 0, 0, 0)

    def recomecar(self) -> None:
        """Volta ao original e apaga o rascunho (usado ao descartar e depois de salvar)."""
        with self.lock:
            self.atual = self.base.copy()
            self.camadas, self.ordem, self.ativa = {}, [], None
            self.historico_, self.refazer_pilha = [], []
            self.versao += 1
            shutil.rmtree(self.pasta, ignore_errors=True)

    # ------------------------------------------------------------------ rótulos
    def camadas_visiveis(self) -> list[Camada]:
        return [self.camadas[c] for c in self.ordem if self.camadas[c].visivel and self.camadas[c].opacidade > 0]

    def _tocados(self) -> dict[str, np.ndarray]:
        """Por camada, os pixels que algum passo do histórico mudou nela (edição, mover, mesclar, borracha). O resto do
        conteúdo dela veio de uma cópia (Ctrl+J, duplicar)."""
        out: dict[str, np.ndarray] = {}
        for e in self.historico_:
            for p in e.passos:
                if p[0] == "pixels":
                    _, cid, (y0, y1, x0, x1), antes, depois = p
                    if cid not in out:
                        out[cid] = np.zeros((self.H, self.W), bool)
                    out[cid][y0:y1, x0:x1] |= (antes != depois).any(2)
        return out

    def _muda_a_imagem(self, cam: Camada, onde: np.ndarray) -> np.ndarray:
        """Os pixels de ``onde`` em que a camada muda o que está embaixo dela."""
        ys, xs = np.nonzero(onde)
        bb = (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
        i = self.ordem.index(cam.id)
        out = np.zeros((self.H, self.W), bool)
        y0, y1, x0, x1 = bb
        out[y0:y1, x0:x1] = ((self._composto(bb, self.ordem[:i]) != self._composto(bb, self.ordem[:i + 1])).any(2)
                             & onde[y0:y1, x0:x1])
        return out

    def mascaras(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(alterados, regiao, classes): pixels que mudaram, área editada e categoria por pixel. O conteúdo de camada
        que só foi copiado (Ctrl+J, duplicar), sem edição nem movimento ali, só conta onde muda a imagem: uma cópia
        parada no mesmo lugar não é edição."""
        with self.lock:
            alterados = (self.atual != self.base).any(axis=2)
            regiao = np.zeros((self.H, self.W), bool)
            classes = np.zeros((self.H, self.W), np.uint8)
            tocados = self._tocados()
            for cam in self.camadas_visiveis():
                m = cam.rgba[..., 3] > 0
                copia = m & ~tocados[cam.id] if cam.id in tocados else m
                if copia.any():
                    m = m & (~copia | self._muda_a_imagem(cam, copia))
                regiao |= m
                if cam.categoria in self.ids_categoria:
                    classes[m] = self.ids_categoria[cam.categoria]
            regiao |= alterados
            return alterados, regiao, classes

    def mapa_edicoes(self) -> tuple[np.ndarray, list[Entrada]]:
        """(mapa, edições): por pixel, o número (1..n) da edição do histórico que aparece ali
        (0 = nenhuma). Mesma regra da máscara de classes: vale a camada visível mais alta, e
        dentro dela a última edição que mexeu no pixel. Conteúdo sem edição própria na camada
        (veio de mesclar/duplicar) fica com a última edição que mexeu naquele pixel."""
        with self.lock:
            ents = [e for e in self.historico_ if e.tipo != "camada" and not e.info.get("restaura")]
            final = np.zeros((self.H, self.W), np.uint16)
            if not ents:
                return final, ents

            def pintar(mapa, n, passo):
                _, _cid, (y0, y1, x0, x1), antes, depois = passo
                mudou = (antes != depois).any(2)
                mapa[y0:y1, x0:x1][mudou] = n

            geral = np.zeros_like(final)
            for n, e in enumerate(ents, 1):
                for p in e.passos:
                    if p[0] == "pixels":
                        pintar(geral, n, p)
            for cam in self.camadas_visiveis():  # de baixo para cima: a de cima sobrescreve
                vis = cam.rgba[..., 3] > 0
                if not vis.any():
                    continue
                mapa = np.zeros_like(final)
                for n, e in enumerate(ents, 1):
                    for p in e.passos:
                        if p[0] == "pixels" and p[1] == cam.id:
                            pintar(mapa, n, p)
                final[vis] = np.where(mapa[vis] > 0, mapa[vis], geral[vis])
            return final, ents

    def categorias(self, exato: bool = False) -> list[str]:
        """Categorias presentes. ``exato`` olha pixel a pixel (uma camada inteiramente coberta
        por outra some); o modo rápido usa o cache das camadas visíveis não vazias."""
        if exato:
            _, regiao, classes = self.mascaras()
            presentes = set(np.unique(classes[regiao]).tolist()) - {0}
        else:
            presentes = {self.ids_categoria[c.categoria] for c in self.camadas_visiveis()
                         if c.categoria in self.ids_categoria and c.estatistica()[1] > 0}
        return [c for c, i in self.ids_categoria.items() if i in presentes]

    def pode_salvar(self) -> tuple[bool, str]:
        with self.lock:
            vis = [c for c in self.camadas_visiveis() if c.estatistica()[1] > 0]
            if not vis:
                return False, "nenhuma edição feita"
            sem = [c.nome for c in vis if c.categoria not in self.ids_categoria]
            if sem:
                return False, f"camada sem categoria: {', '.join(sem)}"
            if not (self.atual != self.base).any():
                return False, "a imagem está igual ao original"
            if not self.categorias():
                return False, "marque o que foi editado"
            return True, ""

    def historico(self) -> list[dict]:
        return [e.resumo() for e in self.historico_]

    def lista_camadas(self) -> list[dict]:
        out = []
        for cid in reversed(self.ordem):  # de cima para baixo, como no painel
            c = self.camadas[cid]
            bb, px = c.estatistica()
            out.append({**c.props(), "ativa": cid == self.ativa, "vazia": bb is None, "versao": c.versao,
                        "bbox": None if bb is None else [bb[2], bb[0], bb[3], bb[1]], "pixels": px})
        return out

    def estado(self) -> dict:
        ok, motivo = self.pode_salvar()
        ocultas = [self.camadas[c].nome for c in self.ordem if not self.camadas[c].visivel]
        return {"versao": self.versao, "historico": self.historico(), "camadas": self.lista_camadas(),
                "refazer": [e.resumo() for e in reversed(self.refazer_pilha)],  # os passos à frente, do próximo em diante
                "ativa": self.ativa, "pode_refazer": bool(self.refazer_pilha), "categorias": self.categorias(),
                "pode_salvar": ok, "motivo": motivo, "ocultas": ocultas}

    # ------------------------------------------------------------------ rascunho
    def _gravar_entrada(self, ent: Entrada) -> None:
        arrays = {}
        for i, p in enumerate(ent.passos):
            if p[0] == "pixels":
                arrays[f"a{i}"], arrays[f"d{i}"] = p[3], p[4]
        if arrays:
            self.pasta.mkdir(parents=True, exist_ok=True)
            tmp = self.pasta / f"h_{ent.id}.tmp.npz"
            np.savez_compressed(tmp, **arrays)
            os.replace(tmp, self.pasta / f"h_{ent.id}.npz")

    def _apagar_entrada(self, ent: Entrada) -> None:
        (self.pasta / f"h_{ent.id}.npz").unlink(missing_ok=True)

    @staticmethod
    def _meta_entrada(ent: Entrada) -> dict:
        passos = []
        for p in ent.passos:
            passos.append({"t": "pixels", "camada": p[1], "bbox": list(p[2])} if p[0] == "pixels"
                          else {"t": "estrutura", "antes": p[1], "depois": p[2]})
        return {"id": ent.id, "rotulo": ent.rotulo, "tipo": ent.tipo, "passos": passos, "info": ent.info,
                "criado": ent.criado}

    def _gravar_estado(self) -> None:
        if not self.historico_ and not self.refazer_pilha and not self.ordem:
            shutil.rmtree(self.pasta, ignore_errors=True)
            self._sujas.clear()
            return
        self.pasta.mkdir(parents=True, exist_ok=True)
        for cid in list(self._sujas):
            if cid in self.camadas:
                tmp = self.pasta / f"c_{cid}.tmp.npz"
                np.savez_compressed(tmp, rgba=self.camadas[cid].rgba)
                os.replace(tmp, self.pasta / f"c_{cid}.npz")
        self._sujas.clear()
        estado = {"versao": _VERSAO_RASCUNHO, "sha_pixels": self.item.sha_pixels, "split": self.item.split,
                  "estrutura": self._estrutura(), "historico": [self._meta_entrada(e) for e in self.historico_],
                  "refazer": [self._meta_entrada(e) for e in self.refazer_pilha]}
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
        if estado.get("versao") != _VERSAO_RASCUNHO:
            raise ErroOperacao(f"rascunho de {self.item.nome} é de uma versão antiga do rotulador; descarte-o")
        e = estado["estrutura"]
        for cid, p in e["props"].items():
            with np.load(self.pasta / f"c_{cid}.npz") as z:
                rgba = z["rgba"]
            self.camadas[cid] = Camada(id=cid, nome=p["nome"], categoria=p["categoria"],
                                       descricao=p.get("descricao", ""), visivel=p["visivel"],
                                       opacidade=p["opacidade"], rgba=rgba, auto=bool(p.get("auto", False)))
        self._restaurar_estrutura(e)

        def ler(m: dict) -> Entrada:
            passos = []
            z = np.load(self.pasta / f"h_{m['id']}.npz") if any(p["t"] == "pixels" for p in m["passos"]) else None
            for i, p in enumerate(m["passos"]):
                if p["t"] == "pixels":
                    passos.append(("pixels", p["camada"], tuple(p["bbox"]), z[f"a{i}"], z[f"d{i}"]))
                else:
                    passos.append(("estrutura", p["antes"], p["depois"]))
            if z is not None:
                z.close()
            return Entrada(m["id"], m["rotulo"], m["tipo"], passos, m["info"], m["criado"])

        self.historico_ = [ler(m) for m in estado.get("historico", [])]
        self.refazer_pilha = [ler(m) for m in estado.get("refazer", [])]
        self._compor()
