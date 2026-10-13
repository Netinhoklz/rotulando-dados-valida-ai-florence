"""Trabalho em equipe: quem é o operador, quem está editando cada documento, a quem cada documento
está atribuído e as metas de rótulos por split × classe.

  - operador: o nome que a página manda no cabeçalho ``X-Operador`` (sem senha; LGPD fora do escopo);
  - trava por documento: quem abre um documento fica "editando" até sair ou até ``VALIDADE_S`` sem
    sinal; outra pessoa vê "em edição por X" e só edita se assumir. Fica em memória: reiniciar o
    servidor solta tudo (o rascunho continua em disco);
  - atribuições e metas: JSON em ``saida/_equipe/`` (fora dos splits; não entram no dataset).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
from pathlib import Path

from .registro import SPLITS

VALIDADE_S = 10 * 60  # sem sinal por 10 min, a trava vence
ANONIMO = "anônimo"


def operador_da_requisicao(request) -> str:
    """Nome do operador (cabeçalho X-Operador, codificado como URI pela página)."""
    bruto = request.headers.get("X-Operador", "")
    nome = urllib.parse.unquote(bruto).strip()[:80]
    return nome or ANONIMO


class Travas:
    """Quem está editando cada documento (em memória).

    Pedido sem nome (``ANONIMO``) é script ou automação — a interface sempre manda o nome: não pega
    trava nem é barrado por uma, como antes de haver travas (senão um script de apoio prenderia o
    documento de quem está na tela)."""

    def __init__(self, validade_s: float = VALIDADE_S, relogio=time.time):
        self.validade_s, self.relogio = validade_s, relogio
        self._d: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _viva(self, t: dict | None) -> dict | None:
        return t if t and self.relogio() - t["visto"] <= self.validade_s else None

    def dono(self, doc: str) -> dict | None:
        with self._lock:
            return self._viva(self._d.get(doc))

    def pegar(self, doc: str, operador: str, forcar: bool = False) -> dict | None:
        """Pega (ou renova) a trava. Devolve a trava de OUTRA pessoa que impediu, ou None se pegou."""
        if operador == ANONIMO:
            return None
        agora = self.relogio()
        with self._lock:
            t = self._viva(self._d.get(doc))
            if t and t["operador"] != operador and not forcar:
                return dict(t)
            if t and t["operador"] == operador:
                t["visto"] = agora
            else:
                self._d[doc] = {"operador": operador, "desde": agora, "visto": agora,
                                "assumida_de": t["operador"] if t else None}
            return None

    def conflito(self, doc: str, operador: str) -> dict | None:
        """A trava de outra pessoa (para recusar uma edição), renovando a própria se for o caso."""
        if operador == ANONIMO:
            return None
        agora = self.relogio()
        with self._lock:
            t = self._viva(self._d.get(doc))
            if t is None:  # ninguém editando: quem edita passa a ter a trava
                self._d[doc] = {"operador": operador, "desde": agora, "visto": agora, "assumida_de": None}
                return None
            if t["operador"] != operador:
                return dict(t)
            t["visto"] = agora
            return None

    def soltar(self, doc: str, operador: str) -> None:
        with self._lock:
            t = self._d.get(doc)
            if t and t["operador"] == operador:
                del self._d[doc]

    def todas(self) -> dict[str, dict]:
        with self._lock:
            return {d: dict(t) for d, t in self._d.items() if self._viva(t)}


class Equipe:
    """Atribuições (doc → operador) e metas, gravadas em saida/_equipe."""

    def __init__(self, raiz_saida: Path):
        self.pasta = Path(raiz_saida) / "_equipe"
        self._lock = threading.Lock()

    def _ler(self, nome: str, padrao):
        try:
            return json.loads((self.pasta / nome).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return padrao

    def _gravar(self, nome: str, dados) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        tmp = self.pasta / (nome + ".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.pasta / nome)

    # ------------------------------------------------------------------ atribuições
    def atribuicoes(self) -> dict[str, str]:
        return self._ler("atribuicoes.json", {})

    def atribuir(self, docs: list[str], operador: str | None) -> dict[str, str]:
        with self._lock:
            a = self.atribuicoes()
            for d in docs:
                if operador:
                    a[str(d)] = operador[:80]
                else:
                    a.pop(str(d), None)
            self._gravar("atribuicoes.json", a)
            return a

    # ------------------------------------------------------------------ metas
    def metas(self) -> dict:
        """{"classes": {split: {categoria: n}}, "so_aprovadas": bool}"""
        m = self._ler("metas.json", {})
        return {"classes": {sp: dict(m.get("classes", {}).get(sp, {})) for sp in SPLITS},
                "so_aprovadas": bool(m.get("so_aprovadas", False))}

    def definir_metas(self, dados: dict, categorias: set[str]) -> dict:
        classes = {}
        for sp in SPLITS:
            linha = (dados.get("classes") or {}).get(sp) or {}
            if not isinstance(linha, dict):
                raise ValueError(f"metas de {sp}: esperava {{classe: número}}")
            classes[sp] = {}
            for k, v in linha.items():
                if k not in categorias:
                    raise ValueError(f"classe desconhecida: {k}")
                n = int(v)
                if n < 0 or n > 1_000_000:
                    raise ValueError(f"meta fora do intervalo: {k}={v}")
                if n:
                    classes[sp][k] = n
        m = {"classes": classes, "so_aprovadas": bool(dados.get("so_aprovadas", False))}
        with self._lock:
            self._gravar("metas.json", m)
        return m


def progresso(metas: dict, linhas_catalogo: list[dict]) -> dict:
    """Edições feitas por split × classe (do catalogo.csv) contra as metas."""
    feito = {sp: {} for sp in SPLITS}
    for r in linhas_catalogo:
        if metas.get("so_aprovadas") and r.get("revisao") != "aprovada":
            continue
        sp, cat = r.get("split"), r.get("categoria")
        if sp in feito and cat:
            feito[sp][cat] = feito[sp].get(cat, 0) + 1
    falta = {sp: {k: max(0, n - feito[sp].get(k, 0)) for k, n in metas["classes"].get(sp, {}).items()} for sp in SPLITS}
    return {"feito": feito, "falta": falta, "metas": metas}
