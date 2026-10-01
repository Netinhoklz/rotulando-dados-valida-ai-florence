"""Mover/duplicar a seleção com escala, rotação e perspectiva (copy-move) e colar
um trecho de OUTRO documento do mesmo split (splicing).

O destino é sempre dado pelos 4 cantos (sup-esq, sup-dir, inf-dir, inf-esq) para
onde vão os cantos da caixa de origem, em coordenadas contínuas da imagem.
"""

from __future__ import annotations

import cv2
import numpy as np

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado
from .base import alpha_selecao, caixa, compor, cor, exigir_selecao, inteiro, num
from .texto import apagar

_INTERP = {"bicubica": cv2.INTER_CUBIC, "bilinear": cv2.INTER_LINEAR, "vizinho": cv2.INTER_NEAREST}


def _destino(p: dict) -> np.ndarray:
    try:
        d = np.asarray(p.get("destino"), np.float64)
    except (TypeError, ValueError):
        raise ErroOperacao("destino inválido")
    if d.shape != (4, 2) or not np.isfinite(d).all():
        raise ErroOperacao("destino precisa de 4 cantos [x, y]")
    return d


def _transformar(conteudo: np.ndarray, alpha: np.ndarray, ox: float, oy: float, destino: np.ndarray,
                 H: int, W: int, interp: int):
    """Projeta ``conteudo`` (cujo canto sup-esq está em ox, oy) nos 4 cantos ``destino``.

    Devolve (y0, y1, x0, x1, imagem_projetada, alpha_projetado) já recortados na imagem.
    """
    h, w = alpha.shape
    origem = np.float32([[ox, oy], [ox + w, oy], [ox + w, oy + h], [ox, oy + h]])
    M = cv2.getPerspectiveTransform(origem, destino.astype(np.float32)).astype(np.float64)
    y0, y1, x0, x1 = caixa(destino[:, 1].min() - 2, destino[:, 1].max() + 2,
                           destino[:, 0].min() - 2, destino[:, 0].max() + 2, H, W)

    def T(tx, ty):
        return np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1]], np.float64)

    # contínuo -> centro de pixel do cv2 (desloca 0,5) e origem/destino recortados
    Mc = T(-x0 - 0.5, -y0 - 0.5) @ M @ T(ox + 0.5, oy + 0.5)
    tam = (x1 - x0, y1 - y0)
    img_p = cv2.warpPerspective(conteudo, Mc, tam, flags=interp, borderMode=cv2.BORDER_REPLICATE)
    a_p = cv2.warpPerspective(alpha.astype(np.float32), Mc, tam,
                              flags=cv2.INTER_NEAREST if interp == cv2.INTER_NEAREST else cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    a_p = np.clip(a_p, 0, 1)
    a_p[a_p < 1.0 / 512] = 0
    return y0, y1, x0, x1, img_p, a_p


def _juntar(img, fundo_total, fundo_bbox, y0, y1, x0, x1, img_p, a_p, pegada_extra=None, meta=None):
    """Junta a área de fundo alterada (ex.: buraco preenchido) com o conteúdo projetado."""
    H, W = img.shape[:2]
    by0, by1, bx0, bx1 = (y0, y1, x0, x1) if fundo_bbox is None else (
        min(y0, fundo_bbox[0]), max(y1, fundo_bbox[1]), min(x0, fundo_bbox[2]), max(x1, fundo_bbox[3]))
    novo = fundo_total[by0:by1, bx0:bx1].copy()
    alpha = np.zeros((by1 - by0, bx1 - bx0), np.float32)
    alpha[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0] = a_p
    proj = np.zeros_like(novo)
    proj[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0] = img_p
    novo = compor(novo, proj, alpha)
    pegada = alpha > 0
    if pegada_extra is not None:
        pegada |= pegada_extra[by0:by1, bx0:bx1]
    return Resultado(by0, by1, bx0, bx1, novo, pegada, meta=meta or {})


def transformar(img: np.ndarray, p: dict, sel: Selecao, chave_cache=None, **_) -> Resultado:
    exigir_selecao(sel)
    H, W = img.shape[:2]
    destino = _destino(p)
    interp = _INTERP.get(p.get("interpolacao", "bicubica"), cv2.INTER_CUBIC)
    sy0, sy1, sx0, sx1 = sel.bbox
    y0, y1, x0, x1, img_p, a_p = _transformar(img[sy0:sy1, sx0:sx1], sel.alpha, sx0, sy0, destino, H, W, interp)
    if not (a_p > 0).any():
        raise ErroOperacao("o destino ficou fora da imagem")
    modo = p.get("modo", "mover")
    if modo == "duplicar":
        return _juntar(img, img, None, y0, y1, x0, x1, img_p, a_p)
    if modo != "mover":
        raise ErroOperacao(f"modo inválido: {modo}")
    # mover: o buraco deixado na origem é preenchido antes de colar o conteúdo
    origem = p.get("origem", "preencher")
    furo = sel.completa_bool()
    if origem == "preencher":
        semente = inteiro(p, "semente", 0, 0, 2**31 - 1)
        fundo = apagar(img, sel, 7, semente, None if chave_cache is None else (chave_cache, 7, semente))
    elif origem == "cor":
        fundo = img.copy()
        fundo[sy0:sy1, sx0:sx1] = compor(img[sy0:sy1, sx0:sx1], cor(p, "cor_fundo", (255, 255, 255)), sel.alpha)
    elif origem == "nada":
        fundo = img
    else:
        raise ErroOperacao(f"preenchimento da origem inválido: {origem}")
    return _juntar(img, fundo, sel.bbox, y0, y1, x0, x1, img_p, a_p, pegada_extra=furo,
                   meta={"origem": origem})


def colar(img: np.ndarray, p: dict, sel: Selecao, carregar_doador=None, **_) -> Resultado:
    """Splicing: recorte retangular de outro documento (do MESMO split) projetado no destino."""
    if carregar_doador is None:
        raise ErroOperacao("colagem indisponível")
    doador_id = str(p.get("doador", ""))
    doador = carregar_doador(doador_id)  # levanta erro se for de outro split
    Hd, Wd = doador.shape[:2]
    o = p.get("origem") or {}
    oy0, oy1, ox0, ox1 = caixa(num(o, "y0", 0, 0, Hd), num(o, "y1", 0, 0, Hd), num(o, "x0", 0, 0, Wd),
                               num(o, "x1", 0, 0, Wd), Hd, Wd)
    conteudo = np.ascontiguousarray(doador[oy0:oy1, ox0:ox1])
    alpha = np.ones(conteudo.shape[:2], np.float32)
    borda = num(p, "suavizar_borda", 0, 0, 50)
    if borda > 0:  # suaviza só para dentro: a pegada não cresce
        d = cv2.distanceTransform(np.pad(alpha.astype(np.uint8), 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
        alpha = np.clip(d / borda, 0, 1).astype(np.float32)
    H, W = img.shape[:2]
    interp = _INTERP.get(p.get("interpolacao", "bicubica"), cv2.INTER_CUBIC)
    y0, y1, x0, x1, img_p, a_p = _transformar(conteudo, alpha, ox0, oy0, _destino(p), H, W, interp)
    if not sel.vazia and p.get("limitar_selecao"):
        a_p = a_p * alpha_selecao(sel, (y0, y1, x0, x1))
    if not (a_p > 0).any():
        raise ErroOperacao("o destino ficou fora da imagem")
    return _juntar(img, img, None, y0, y1, x0, x1, img_p, a_p,
                   meta={"doador": doador_id, "origem_doador": [ox0, oy0, ox1, oy1]})
