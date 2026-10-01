"""Ajustes de imagem aplicados dentro da seleção."""

from __future__ import annotations

import cv2
import numpy as np

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado
from .base import exigir_selecao, inteiro, num, resultado_selecao

AJUSTES = ("brilho_contraste", "niveis", "matiz_saturacao", "desfoque", "nitidez", "ruido", "jpeg", "cinza")


def _u8(a: np.ndarray) -> np.ndarray:
    return np.clip(a + 0.5, 0, 255).astype(np.uint8)


def ajuste(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    exigir_selecao(sel)
    nome = p.get("ajuste")
    if nome not in AJUSTES:
        raise ErroOperacao(f"ajuste desconhecido: {nome}")
    H, W = img.shape[:2]
    y0, y1, x0, x1 = sel.bbox
    # margem para filtros que olham a vizinhança (desfoque, nitidez) e grade do JPEG
    m = 0
    if nome in ("desfoque", "nitidez"):
        m = int(np.ceil(3 * num(p, "raio" if nome == "nitidez" else "sigma", 2, 0.1, 50))) + 2
    py0, py1, px0, px1 = max(0, y0 - m), min(H, y1 + m), max(0, x0 - m), min(W, x1 + m)
    if nome == "jpeg":  # alinha com a grade 8x8 da imagem inteira, como numa recompressão real
        py0, px0 = (y0 // 8) * 8, (x0 // 8) * 8
        py1, px1 = min(H, -(-y1 // 8) * 8), min(W, -(-x1 // 8) * 8)
    rec = img[py0:py1, px0:px1]
    f = rec.astype(np.float32)

    if nome == "brilho_contraste":
        b, c = num(p, "brilho", 0, -100, 100), num(p, "contraste", 0, -100, 100)
        out = _u8((f - 127.5) * (1 + c / 100.0) + 127.5 + b * 2.55)
    elif nome == "niveis":
        preto, branco = num(p, "preto", 0, 0, 254), num(p, "branco", 255, 1, 255)
        if branco <= preto:
            raise ErroOperacao("nível de branco precisa ser maior que o de preto")
        gama = num(p, "gama", 1.0, 0.1, 10)
        out = _u8(np.clip((f - preto) / (branco - preto), 0, 1) ** (1 / gama) * 255)
    elif nome == "matiz_saturacao":
        hsv = cv2.cvtColor(rec, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
        hsv[..., 0] = (hsv[..., 0] + num(p, "matiz", 0, -180, 180) / 360 * 256) % 256
        hsv[..., 1] *= 1 + num(p, "saturacao", 0, -100, 100) / 100
        hsv[..., 2] *= 1 + num(p, "luminosidade", 0, -100, 100) / 100
        out = cv2.cvtColor(_u8(np.clip(hsv, 0, 255)), cv2.COLOR_HSV2RGB_FULL)
    elif nome == "desfoque":
        out = cv2.GaussianBlur(rec, (0, 0), num(p, "sigma", 1, 0.1, 50))
    elif nome == "nitidez":
        borrado = cv2.GaussianBlur(f, (0, 0), num(p, "raio", 1, 0.1, 20))
        out = _u8(f + (f - borrado) * num(p, "quantidade", 100, 0, 500) / 100)
    elif nome == "ruido":
        rng = np.random.default_rng(inteiro(p, "semente", 0, 0, 2**31 - 1))
        sigma = num(p, "sigma", 5, 0, 60)
        forma = rec.shape[:2] + ((1,) if p.get("monocromatico", True) else (3,))
        out = _u8(f + rng.normal(0, sigma, forma).astype(np.float32))
    elif nome == "cinza":
        g = f @ np.float32([0.299, 0.587, 0.114])
        out = _u8(np.repeat(g[..., None], 3, 2))
    else:  # jpeg
        q = inteiro(p, "qualidade", 75, 1, 100)
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rec, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
        if not ok:
            raise ErroOperacao("falha ao recomprimir")
        out = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)

    novo = out[y0 - py0:y1 - py0, x0 - px0:x1 - px0]
    return resultado_selecao(img, sel, novo)
