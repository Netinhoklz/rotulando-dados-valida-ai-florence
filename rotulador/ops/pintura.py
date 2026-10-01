"""Pincel, borracha que devolve o original e balde de tinta."""

from __future__ import annotations

import cv2
import numpy as np

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado
from .base import alpha_selecao, compor, cor, inteiro, num, pontos, recortar_selecao, traco


def _alpha_traco(img, p, sel):
    H, W = img.shape[:2]
    pts = pontos(p)
    tamanho = num(p, "tamanho", 10, 1, 1000)
    dureza = num(p, "dureza", 1.0, 0, 1)
    opacidade = num(p, "opacidade", 1.0, 0, 1)
    y0, y1, x0, x1, alpha = traco(pts, tamanho, dureza, H, W)
    alpha = recortar_selecao(alpha, (y0, y1, x0, x1), sel) * opacidade
    return (y0, y1, x0, x1), alpha


def pincel(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    (y0, y1, x0, x1), alpha = _alpha_traco(img, p, sel)
    novo = compor(img[y0:y1, x0:x1], cor(p), alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0)


def borracha(img: np.ndarray, p: dict, sel: Selecao, base: np.ndarray, **_) -> Resultado:
    """Pincel de histórico: pinta com o ORIGINAL. Não precisa de categoria."""
    (y0, y1, x0, x1), alpha = _alpha_traco(img, p, sel)
    novo = compor(img[y0:y1, x0:x1], base[y0:y1, x0:x1], alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0, restaura=True)


def balde(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    H, W = img.shape[:2]
    x, y = int(num(p, "x", 0, 0, W - 1)), int(num(p, "y", 0, 0, H - 1))
    tol = inteiro(p, "tolerancia", 20, 0, 255)
    opacidade = num(p, "opacidade", 1.0, 0, 1)
    if p.get("contigua", True):
        flood = np.zeros((H + 2, W + 2), np.uint8)
        flags = 4 | cv2.FLOODFILL_FIXED_RANGE | cv2.FLOODFILL_MASK_ONLY | (1 << 8)
        cv2.floodFill(np.ascontiguousarray(img), flood, (x, y), 0, (tol,) * 3, (tol,) * 3, flags)
        m = flood[1:-1, 1:-1].astype(bool)
    else:
        m = (np.abs(img.astype(np.int16) - img[y, x].astype(np.int16)) <= tol).all(2)
    if not sel.vazia:
        m &= sel.completa_bool()
    if not m.any():
        raise ErroOperacao("o balde não alcançou nenhum pixel (clique dentro da seleção)")
    linhas, colunas = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
    y0, y1, x0, x1 = int(linhas[0]), int(linhas[-1]) + 1, int(colunas[0]), int(colunas[-1]) + 1
    alpha = m[y0:y1, x0:x1].astype(np.float32) * opacidade
    if not sel.vazia:
        alpha *= alpha_selecao(sel, (y0, y1, x0, x1))
    novo = compor(img[y0:y1, x0:x1], cor(p), alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0)
