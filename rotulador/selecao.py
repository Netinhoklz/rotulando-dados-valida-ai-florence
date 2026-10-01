"""Seleção: a descrição vetorial que vem do navegador vira uma máscara.

Formato (coordenadas em pixels da imagem original):
    {"formas": [
        {"tipo": "ret",      "x0":.., "y0":.., "x1":.., "y1":.., "modo": "novo"},
        {"tipo": "elipse",   "x0":.., "y0":.., "x1":.., "y1":.., "modo": "somar"},
        {"tipo": "poligono", "pontos": [[x, y], ...],             "modo": "subtrair"},
        {"tipo": "varinha",  "x":.., "y":.., "tolerancia": 20, "contigua": true, "modo": "intersectar"},
     ],
     "inverter": false, "expandir": 0 (negativo = contrair), "suavizar": 0 (sigma em px)}

A seleção é sempre recalculada no servidor a partir da descrição; nada de máscara
pintada no navegador entra nos dados.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MODOS = ("novo", "somar", "subtrair", "intersectar")


class ErroSelecao(ValueError):
    pass


@dataclass
class Selecao:
    """Máscara recortada: ``alpha`` (0..1) vale dentro de [y0:y1, x0:x1]."""

    alpha: np.ndarray
    y0: int
    y1: int
    x0: int
    x1: int
    altura: int
    largura: int

    @property
    def vazia(self) -> bool:
        return self.alpha.size == 0 or not (self.alpha > 0).any()

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.y0, self.y1, self.x0, self.x1

    def booleana(self) -> np.ndarray:
        return self.alpha > 0

    def completa(self) -> np.ndarray:
        m = np.zeros((self.altura, self.largura), np.float32)
        m[self.y0:self.y1, self.x0:self.x1] = self.alpha
        return m

    def completa_bool(self) -> np.ndarray:
        m = np.zeros((self.altura, self.largura), bool)
        m[self.y0:self.y1, self.x0:self.x1] = self.alpha > 0
        return m


def vazia(altura: int, largura: int) -> Selecao:
    return Selecao(np.zeros((0, 0), np.float32), 0, 0, 0, 0, altura, largura)


def de_mascara(m: np.ndarray, alpha: np.ndarray | None = None) -> Selecao:
    """Selecao a partir de uma máscara completa (bool) e, opcionalmente, alpha completo."""
    H, W = m.shape
    if alpha is None:
        alpha = m.astype(np.float32)
    nz = alpha > 0
    if not nz.any():
        return vazia(H, W)
    linhas, colunas = np.flatnonzero(nz.any(1)), np.flatnonzero(nz.any(0))
    y0, y1, x0, x1 = int(linhas[0]), int(linhas[-1]) + 1, int(colunas[0]), int(colunas[-1]) + 1
    return Selecao(np.ascontiguousarray(alpha[y0:y1, x0:x1], np.float32), y0, y1, x0, x1, H, W)


def rasterizar(spec: dict | None, img: np.ndarray) -> Selecao:
    H, W = img.shape[:2]
    if not spec or not spec.get("formas"):
        return vazia(H, W)
    m = np.zeros((H, W), bool)
    for i, forma in enumerate(spec["formas"]):
        modo = forma.get("modo", "novo" if i == 0 else "somar")
        if modo not in MODOS:
            raise ErroSelecao(f"modo de seleção inválido: {modo}")
        f = _forma(forma, img)
        if modo == "novo":
            m = f
        elif modo == "somar":
            m |= f
        elif modo == "subtrair":
            m &= ~f
        else:
            m &= f
    if spec.get("inverter"):
        m = ~m
    ex = int(round(float(spec.get("expandir", 0) or 0)))
    if ex:
        ex = max(-200, min(200, ex))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * abs(ex) + 1, 2 * abs(ex) + 1))
        u8 = m.astype(np.uint8)
        m = (cv2.dilate(u8, k) if ex > 0 else cv2.erode(u8, k, borderType=cv2.BORDER_CONSTANT, borderValue=0)).astype(bool)
    sigma = float(spec.get("suavizar", 0) or 0)
    if sigma > 0 and m.any():
        sigma = min(sigma, 100.0)
        alpha = cv2.GaussianBlur(m.astype(np.float32), (0, 0), sigma)
        alpha[alpha < 1e-3] = 0
        return de_mascara(alpha > 0, alpha)
    return de_mascara(m)


def _num(forma: dict, chave: str) -> float:
    try:
        v = float(forma[chave])
    except (KeyError, TypeError, ValueError):
        raise ErroSelecao(f"coordenada '{chave}' ausente ou inválida")
    if not np.isfinite(v):
        raise ErroSelecao(f"coordenada '{chave}' inválida")
    return v


def _forma(forma: dict, img: np.ndarray) -> np.ndarray:
    H, W = img.shape[:2]
    tipo = forma.get("tipo")
    m = np.zeros((H, W), np.uint8)
    if tipo in ("ret", "elipse"):
        x0, x1 = sorted((_num(forma, "x0"), _num(forma, "x1")))
        y0, y1 = sorted((_num(forma, "y0"), _num(forma, "y1")))
        x0, x1 = int(np.clip(round(x0), 0, W)), int(np.clip(round(x1), 0, W))
        y0, y1 = int(np.clip(round(y0), 0, H)), int(np.clip(round(y1), 0, H))
        if x1 <= x0 or y1 <= y0:
            return m.astype(bool)
        if tipo == "ret":
            m[y0:y1, x0:x1] = 1
        else:
            yy, xx = np.ogrid[y0:y1, x0:x1]
            cy, cx = (y0 + y1 - 1) / 2, (x0 + x1 - 1) / 2
            ry, rx = max((y1 - y0) / 2, 0.5), max((x1 - x0) / 2, 0.5)
            m[y0:y1, x0:x1] = (((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2) <= 1.0
        return m.astype(bool)
    if tipo == "poligono":
        pts = forma.get("pontos") or []
        if len(pts) < 3:
            return m.astype(bool)
        a = np.asarray(pts, np.float64)
        if a.ndim != 2 or a.shape[1] != 2 or not np.isfinite(a).all():
            raise ErroSelecao("pontos do polígono inválidos")
        # subpixel (shift=4) para o contorno seguir o traço do mouse
        cv2.fillPoly(m, [np.round(a * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
        return m.astype(bool)
    if tipo in ("varinha", "cor"):
        x, y = int(_num(forma, "x")), int(_num(forma, "y"))
        if not (0 <= x < W and 0 <= y < H):
            return m.astype(bool)
        tol = int(np.clip(float(forma.get("tolerancia", 20)), 0, 255))
        contigua = bool(forma.get("contigua", tipo == "varinha"))
        if contigua:
            flood = np.zeros((H + 2, W + 2), np.uint8)
            flags = 4 | cv2.FLOODFILL_FIXED_RANGE | cv2.FLOODFILL_MASK_ONLY | (1 << 8)
            cv2.floodFill(np.ascontiguousarray(img), flood, (x, y), 0, (tol,) * 3, (tol,) * 3, flags)
            return flood[1:-1, 1:-1].astype(bool)
        semente = img[y, x].astype(np.int16)
        return (np.abs(img.astype(np.int16) - semente) <= tol).all(axis=2)
    if tipo == "traco":  # pincel de seleção (máscara rápida)
        pts = forma.get("pontos") or []
        try:
            a = np.asarray(pts, np.float64).reshape(-1, 2)
        except ValueError:
            raise ErroSelecao("pontos do pincel de seleção inválidos")
        if not len(a) or not np.isfinite(a).all():
            raise ErroSelecao("pontos do pincel de seleção inválidos")
        r = max(1, int(round(_num(forma, "tamanho") / 2)))
        q = np.round(a).astype(np.int64)
        for i in range(len(q)):
            cv2.circle(m, (int(q[i, 0]), int(q[i, 1])), r, 1, -1)
            if i:
                cv2.line(m, (int(q[i - 1, 0]), int(q[i - 1, 1])), (int(q[i, 0]), int(q[i, 1])), 1, 2 * r)
        return m.astype(bool)
    if tipo == "tinta":  # só os traços de texto/tinta dentro do retângulo
        x0, x1 = sorted((_num(forma, "x0"), _num(forma, "x1")))
        y0, y1 = sorted((_num(forma, "y0"), _num(forma, "y1")))
        x0, x1 = int(np.clip(round(x0), 0, W)), int(np.clip(round(x1), 0, W))
        y0, y1 = int(np.clip(round(y0), 0, H)), int(np.clip(round(y1), 0, H))
        if x1 - x0 < 3 or y1 - y0 < 3:
            return m.astype(bool)
        m[y0:y1, x0:x1] = mascara_tinta(img[y0:y1, x0:x1])
        ex = int(np.clip(float(forma.get("folga", 1)), 0, 20))
        if ex:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * ex + 1, 2 * ex + 1))
            m = cv2.dilate(m, k)
        return m.astype(bool)
    raise ErroSelecao(f"tipo de seleção desconhecido: {tipo}")


def mascara_tinta(rec: np.ndarray) -> np.ndarray:
    """Pixels de tinta (texto, linhas) num recorte: longe da cor do papel (Otsu)."""
    f = rec.astype(np.float32)
    borda = np.concatenate([f[0], f[-1], f[:, 0], f[:, -1]])
    fundo = np.median(borda, axis=0)
    d = np.abs(f - fundo).max(axis=2)
    u8 = np.clip(d, 0, 255).astype(np.uint8)
    otsu, _ = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return (d > max(30.0, float(otsu))).astype(np.uint8)
