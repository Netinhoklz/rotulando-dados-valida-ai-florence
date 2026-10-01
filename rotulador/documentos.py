"""Leitura de documentos: imagens (jpg, png, tif, bmp, webp, heic) e PDF.

Tudo vira um array RGB uint8 (altura x largura x 3) em sRGB, já com a rotação do
EXIF aplicada, sem transparência (fundo branco) e sem metadados. Arquivos com
várias páginas (PDF, TIFF multipágina) viram um item por página.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps

try:  # HEIC/HEIF de celular
    import pillow_heif

    pillow_heif.register_heif_opener()
except ModuleNotFoundError:  # pragma: no cover - opcional
    pillow_heif = None

try:
    import pymupdf
except ModuleNotFoundError:  # pragma: no cover - opcional
    pymupdf = None

Image.MAX_IMAGE_PIXELS = 300_000_000  # scans de A4 a 600 DPI passam do limite padrão

EXT_IMAGEM = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
EXT_PDF = {".pdf"}
EXTENSOES = EXT_IMAGEM | EXT_PDF

DPI_PADRAO = 200
_SRGB = ImageCms.createProfile("sRGB")


def eh_documento(caminho: Path) -> bool:
    return caminho.suffix.lower() in EXTENSOES and not caminho.name.startswith(".")


def contar_paginas(caminho: Path) -> int:
    if caminho.suffix.lower() in EXT_PDF:
        if pymupdf is None:
            raise RuntimeError("instale pymupdf para ler PDF")
        with pymupdf.open(caminho) as doc:
            return doc.page_count
    with Image.open(caminho) as img:
        return getattr(img, "n_frames", 1)


def carregar(caminho: Path, pagina: int = 0, dpi: int = DPI_PADRAO) -> np.ndarray:
    """Devolve a página ``pagina`` (0 = primeira) como RGB uint8 contíguo."""
    caminho = Path(caminho)
    if caminho.suffix.lower() in EXT_PDF:
        return _carregar_pdf(caminho, pagina, dpi)
    with Image.open(caminho) as img:
        if pagina:
            img.seek(pagina)
        return imagem_para_rgb(img)


def imagem_para_rgb(img: Image.Image) -> np.ndarray:
    """Normaliza qualquer imagem do Pillow para RGB uint8 sRGB sem transparência."""
    img = ImageOps.exif_transpose(img)
    icc = img.info.get("icc_profile")
    if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        a = np.asarray(img, dtype=np.float64)
        topo = 65535.0 if img.mode.startswith("I;16") or a.max() > 255 else 255.0
        a = np.clip(a / topo * 255.0 + 0.5, 0, 255).astype(np.uint8)
        img = Image.fromarray(a, "L")
    if img.mode == "CMYK" and not icc:
        img = img.convert("RGB")
    if icc:
        try:
            origem = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            modo_saida = "RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB"
            if img.mode not in ("RGB", "RGBA", "CMYK", "L", "LA"):
                img = img.convert("RGBA" if "A" in img.mode else "RGB")
            if img.mode in ("L", "LA"):
                img = img.convert("RGBA" if img.mode == "LA" else "RGB")
            img = ImageCms.profileToProfile(img, origem, _SRGB, outputMode=modo_saida)
        except (ImageCms.PyCMSError, OSError, ValueError):
            pass  # perfil quebrado: usa os valores como estão
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        fundo = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(fundo, rgba)
    return np.ascontiguousarray(np.asarray(img.convert("RGB"), dtype=np.uint8))


def _carregar_pdf(caminho: Path, pagina: int, dpi: int) -> np.ndarray:
    if pymupdf is None:
        raise RuntimeError("instale pymupdf para ler PDF")
    with pymupdf.open(caminho) as doc:
        pix = doc[pagina].get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)
        a = a[:, : pix.width * 3].reshape(pix.height, pix.width, 3)
        return np.ascontiguousarray(a)
