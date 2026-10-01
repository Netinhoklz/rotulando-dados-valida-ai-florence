"""Utilitários comuns das operações: leitura de parâmetros, traços e composição."""

from __future__ import annotations

import cv2
import numpy as np

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado


def num(p: dict, chave: str, padrao: float, minimo: float, maximo: float) -> float:
    v = p.get(chave, padrao)
    try:
        v = float(padrao if v is None or v == "" else v)
    except (TypeError, ValueError):
        raise ErroOperacao(f"parâmetro '{chave}' inválido")
    if not np.isfinite(v):
        raise ErroOperacao(f"parâmetro '{chave}' inválido")
    return float(min(maximo, max(minimo, v)))


def inteiro(p: dict, chave: str, padrao: int, minimo: int, maximo: int) -> int:
    return int(round(num(p, chave, padrao, minimo, maximo)))


def cor(p: dict, chave: str = "cor", padrao=(0, 0, 0)) -> np.ndarray:
    v = p.get(chave, padrao)
    if isinstance(v, str):
        s = v.lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        try:
            v = [int(s[i:i + 2], 16) for i in (0, 2, 4)]
        except ValueError:
            raise ErroOperacao(f"cor '{chave}' inválida")
    try:
        a = np.asarray(v, np.float64).reshape(3)
    except ValueError:
        raise ErroOperacao(f"cor '{chave}' inválida")
    return np.clip(a, 0, 255).astype(np.float32)


def pontos(p: dict, chave: str = "pontos") -> np.ndarray:
    pts = p.get(chave)
    try:
        a = np.asarray(pts, np.float64)
    except (TypeError, ValueError):
        raise ErroOperacao("pontos inválidos")
    if a.ndim != 2 or a.shape[1] != 2 or len(a) == 0 or not np.isfinite(a).all():
        raise ErroOperacao("pontos inválidos")
    if len(a) > 20000:
        raise ErroOperacao("traço longo demais")
    return a


def exigir_selecao(sel: Selecao) -> None:
    if sel.vazia:
        raise ErroOperacao("faça uma seleção antes")


def caixa(y0, y1, x0, x1, H, W):
    y0, y1 = max(0, int(np.floor(y0))), min(H, int(np.ceil(y1)))
    x0, x1 = max(0, int(np.floor(x0))), min(W, int(np.ceil(x1)))
    if y1 <= y0 or x1 <= x0:
        raise ErroOperacao("a operação caiu fora da imagem")
    return y0, y1, x0, x1


def traco(pts: np.ndarray, tamanho: float, dureza: float, H: int, W: int):
    """Máscara (0..1) de um traço de pincel redondo. Devolve (y0, y1, x0, x1, alpha)."""
    r = tamanho / 2.0
    sigma = (1.0 - dureza) * tamanho / 4.0
    m = r + 3 * sigma + 2
    y0, y1, x0, x1 = caixa(pts[:, 1].min() - m, pts[:, 1].max() + m + 1,
                           pts[:, 0].min() - m, pts[:, 0].max() + m + 1, H, W)
    S = 4  # desenha em 4x com subpixel e reduz: borda suave e fiel ao tamanho
    tela = np.zeros(((y1 - y0) * S, (x1 - x0) * S), np.uint8)
    # coordenadas contínuas (o pixel j cobre [j, j+1)); o cv2 põe o centro do pixel em j
    q = np.round(((pts - [x0, y0]) * S - 0.5) * 16).astype(np.int64)
    raio = max(1, int(round(r * S * 16)))
    for i in range(len(q)):
        cv2.circle(tela, (int(q[i, 0]), int(q[i, 1])), raio, 255, -1, cv2.LINE_8, shift=4)
        if i:
            cv2.line(tela, (int(q[i - 1, 0]), int(q[i - 1, 1])), (int(q[i, 0]), int(q[i, 1])), 255,
                     max(1, int(round(2 * r * S))), cv2.LINE_8, shift=4)
    alpha = cv2.resize(tela, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    if sigma > 0.3:
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigma)
    alpha[alpha < 1.0 / 512] = 0
    return y0, y1, x0, x1, alpha


def recortar_selecao(alpha: np.ndarray, bbox, sel: Selecao) -> np.ndarray:
    """Limita ``alpha`` (da caixa ``bbox``) à seleção ativa, se houver."""
    if sel.vazia:
        return alpha
    return alpha * alpha_selecao(sel, bbox)


def alpha_selecao(sel: Selecao, bbox) -> np.ndarray:
    """Alpha da seleção recortado na caixa ``bbox`` (zeros onde não há seleção)."""
    y0, y1, x0, x1 = bbox
    s = np.zeros((y1 - y0, x1 - x0), np.float32)
    iy0, iy1 = max(y0, sel.y0), min(y1, sel.y1)
    ix0, ix1 = max(x0, sel.x0), min(x1, sel.x1)
    if iy1 > iy0 and ix1 > ix0:
        s[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = sel.alpha[iy0 - sel.y0:iy1 - sel.y0, ix0 - sel.x0:ix1 - sel.x0]
    return s


def compor(fundo: np.ndarray, frente, alpha: np.ndarray) -> np.ndarray:
    """fundo*(1-a) + frente*a, arredondado. ``frente`` pode ser cor (3,) ou imagem."""
    a = alpha[..., None].astype(np.float32)
    out = fundo.astype(np.float32) * (1 - a) + np.asarray(frente, np.float32) * a
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def resultado(img: np.ndarray, bbox, novo: np.ndarray, alpha: np.ndarray, **kw) -> Resultado:
    y0, y1, x0, x1 = bbox
    return Resultado(y0, y1, x0, x1, novo, alpha > 0, **kw)


def resultado_selecao(img: np.ndarray, sel: Selecao, novo_recorte: np.ndarray, **kw) -> Resultado:
    """Compõe ``novo_recorte`` (da caixa da seleção) usando o alpha da seleção."""
    y0, y1, x0, x1 = sel.bbox
    antes = img[y0:y1, x0:x1]
    return Resultado(y0, y1, x0, x1, compor(antes, novo_recorte, sel.alpha), sel.alpha > 0, **kw)
