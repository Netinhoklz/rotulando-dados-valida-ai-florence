"""Preenchimento inteligente da seleção, pincel corretivo e carimbo de clonagem."""

from __future__ import annotations

import numpy as np

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado
from .base import compor, exigir_selecao, inteiro, num, pontos, recortar_selecao, resultado_selecao, traco
from .preenchimento import preencher

PATCHES = (5, 7, 9, 11)


def _patch(p: dict) -> int:
    t = inteiro(p, "patch", 7, 5, 11)
    return t if t in PATCHES else 7


def preencher_selecao(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    exigir_selecao(sel)
    mascara = sel.completa_bool()
    if mascara.all():
        raise ErroOperacao("a seleção cobre a imagem inteira")
    amostragem = p.get("amostragem", "auto")
    margem = {"auto": -1, "documento": None}.get(amostragem, None if amostragem not in ("margem",) else
                                                   inteiro(p, "margem", 150, 10, 5000))
    if amostragem not in ("auto", "documento", "margem"):
        raise ErroOperacao(f"amostragem inválida: {amostragem}")
    cheio = preencher(img, mascara, tamanho_patch=_patch(p), margem=margem,
                      semente=inteiro(p, "semente", 0, 0, 2**31 - 1))
    y0, y1, x0, x1 = sel.bbox
    return resultado_selecao(img, sel, cheio[y0:y1, x0:x1], meta={"algoritmo": "patchmatch", "amostragem": amostragem})


def corretivo(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    """Pincel corretivo pontual: o traço é reconstruído a partir do entorno (PatchMatch)."""
    H, W = img.shape[:2]
    y0, y1, x0, x1, alpha = traco(pontos(p), num(p, "tamanho", 20, 1, 1000), num(p, "dureza", 0.8, 0, 1), H, W)
    alpha = recortar_selecao(alpha, (y0, y1, x0, x1), sel) * num(p, "opacidade", 1.0, 0, 1)
    mascara = np.zeros((H, W), bool)
    mascara[y0:y1, x0:x1] = alpha > 0
    if not mascara.any():
        raise ErroOperacao("o traço não alcançou nenhum pixel")
    cheio = preencher(img, mascara, tamanho_patch=_patch(p), semente=inteiro(p, "semente", 0, 0, 2**31 - 1))
    novo = compor(img[y0:y1, x0:x1], cheio[y0:y1, x0:x1], alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0, meta={"algoritmo": "patchmatch"})
