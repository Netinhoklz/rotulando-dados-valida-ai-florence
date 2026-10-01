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
    cheio = preencher(img, mascara, tamanho_patch=_patch(p), semente=inteiro(p, "semente", 0, 0, 2**31 - 1))
    y0, y1, x0, x1 = sel.bbox
    return resultado_selecao(img, sel, cheio[y0:y1, x0:x1], meta={"algoritmo": "patchmatch"})


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


def carimbo(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    """Carimbo de clonagem: pinta com a imagem deslocada de (dx, dy) (origem = destino + d)."""
    H, W = img.shape[:2]
    dx, dy = int(round(num(p, "dx", 0, -W, W))), int(round(num(p, "dy", 0, -H, H)))
    if dx == 0 and dy == 0:
        raise ErroOperacao("defina a origem do carimbo (Alt+clique)")
    y0, y1, x0, x1, alpha = traco(pontos(p), num(p, "tamanho", 20, 1, 1000), num(p, "dureza", 0.8, 0, 1), H, W)
    alpha = recortar_selecao(alpha, (y0, y1, x0, x1), sel) * num(p, "opacidade", 1.0, 0, 1)
    fonte = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
    valido = np.zeros((y1 - y0, x1 - x0), np.float32)
    sy0, sy1, sx0, sx1 = max(0, y0 + dy), min(H, y1 + dy), max(0, x0 + dx), min(W, x1 + dx)
    if sy1 > sy0 and sx1 > sx0:
        fonte[sy0 - y0 - dy:sy1 - y0 - dy, sx0 - x0 - dx:sx1 - x0 - dx] = img[sy0:sy1, sx0:sx1]
        valido[sy0 - y0 - dy:sy1 - y0 - dy, sx0 - x0 - dx:sx1 - x0 - dx] = 1
    alpha = alpha * valido
    novo = compor(img[y0:y1, x0:x1], fonte, alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0)
