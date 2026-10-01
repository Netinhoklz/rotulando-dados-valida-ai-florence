"""Documentos sintéticos para os testes (nada de dado real)."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def conta(semente: int = 0, h: int = 420, w: int = 320) -> np.ndarray:
    """Uma 'conta de luz' falsa: fundo levemente texturizado, faixas e linhas de texto."""
    rng = np.random.default_rng(semente)
    base = np.full((h, w, 3), 246, np.uint8)
    base = np.clip(base.astype(int) + rng.integers(-6, 7, (h, w, 1)), 0, 255).astype(np.uint8)
    im = Image.fromarray(base)
    d = ImageDraw.Draw(im)
    fonte = ImageFont.load_default(size=16)
    d.rectangle([0, 0, w, 40], fill=(20, 90, 160))
    d.text((12, 10), f"ENERGIA {semente}", fill=(255, 255, 255), font=fonte)
    linhas = [f"NOME: CLIENTE {semente:03d}", "RUA DAS FLORES, 123", "CEP 30123-456", "VENC 10/09/2026",
              f"TOTAL R$ {100 + semente},45"]
    for i, t in enumerate(linhas):
        d.text((14, 60 + 34 * i), t, fill=(30, 30, 30), font=fonte)
    d.rectangle([14, 360, 300, 400], outline=(0, 0, 0))
    return np.asarray(im).copy()


def salvar(img: np.ndarray, caminho, **kw) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(caminho, **kw)
