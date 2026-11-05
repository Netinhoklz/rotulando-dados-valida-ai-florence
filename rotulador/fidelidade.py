"""Fidelidade do texto novo ao antigo: a mesma altura de letra e a mesma grossura de traço, medidas do mesmo jeito na
linha antiga (no documento) e na nova (no resultado). É a conferência das técnicas que escrevem texto (ops.tecnicas:
corrige o tamanho e o negrito ou descarta a edição) e o que a tela de revisão mostra em cada edição.

  altura    altura das letras (ocr.faixa_da_linha: do alto das letras até a linha de base, sem acento nem cedilha)
            nova ÷ antiga. Só compara textos da mesma classe: os dois com letra alta (maiúscula, dígito, b d f h k l)
            ou os dois só com letras baixas (x-height); "RUA" contra "rua" não diz nada do tamanho da fonte.
  grossura  espessura média do traço (glifos.peso_traco no alfa da tinta) nova ÷ antiga.
"""

from __future__ import annotations

import cv2
import numpy as np

from . import glifos, ocr

TOL_ALTURA = 0.04  # |nova ÷ antiga − 1| até aqui: a mesma altura
TOL_GROSSURA = 0.10  # |nova ÷ antiga − 1| até aqui: a mesma grossura
_ALTAS = set("bdfhkl")
_LUMA = np.float32([0.299, 0.587, 0.114])


def _classe(texto: str) -> str | None:
    letras = [ch for ch in texto if ch.isalnum()]
    if not letras:
        return None
    return "alta" if any(ch.isupper() or ch.isdigit() or ch in _ALTAS for ch in letras) else "baixa"


def medir(img: np.ndarray, caixa, texto: str) -> dict | None:
    """{"altura": px, "grossura": px} da linha principal da caixa (x0, y0, x1, y1); None sem tinta para medir."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in caixa)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    rec = img[y0:y1, x0:x1]
    t = ocr.so_a_linha(ocr.tinta_da_linha(rec))
    if t.sum() < 30:
        return None
    topo, base = ocr.faixa_da_linha(t, texto)
    lum = rec.astype(np.float32) @ _LUMA
    papel, tinta = float(np.percentile(lum, 90)), float(np.percentile(lum[t], 5))
    if papel - tinta < 25:
        return None
    a = np.clip((papel - lum) / (papel - tinta), 0, 1)
    a *= cv2.dilate(t.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    return {"altura": int(base - topo + 1), "grossura": round(glifos.peso_traco(a), 3)}


def comparar(antes: np.ndarray, depois: np.ndarray, caixa_antiga, caixa_nova, texto_antigo: str,
             texto_novo: str) -> dict | None:
    """{"altura": razão ou None, "grossura": razão, "ok": bool, "fora": [o que passou da tolerância]} do texto novo
    (``caixa_nova`` em ``depois``) contra o antigo (``caixa_antiga`` em ``antes``); None se não dá para medir."""
    a, b = medir(antes, caixa_antiga, texto_antigo), medir(depois, caixa_nova, texto_novo)
    if a is None or b is None or not a["grossura"]:
        return None
    mesma = _classe(texto_antigo) is not None and _classe(texto_antigo) == _classe(texto_novo)
    alt = round(b["altura"] / a["altura"], 3) if mesma else None
    gro = round(b["grossura"] / a["grossura"], 3)
    # 1 px de diferença na altura de uma letra miúda já passa de 4%: a medida é em pixel inteiro, então ±1 px vale
    fora = ([] if alt is None or abs(b["altura"] - a["altura"]) <= max(1.0, TOL_ALTURA * a["altura"]) else ["altura"]) + \
        ([] if abs(gro - 1) <= TOL_GROSSURA else ["grossura"])
    return {"altura": alt, "grossura": gro, "altura_px": [a["altura"], b["altura"]], "ok": not fora, "fora": fora}
