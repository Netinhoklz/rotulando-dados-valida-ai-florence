"""Campos detectados de um documento (OCR em cache por documento em saida/_cache/ocr).

A fraude automática e a geração em lote ficam em rotulador/gerador.py (receitas de técnicas
do catálogo, rotulador/catalogo.py).
"""

from __future__ import annotations

from pathlib import Path

from . import campos as mod_campos
from . import ocr
from .contexto import cache_ocr


def campos_do_documento(img, raiz_saida: Path, sha_pixels: str) -> list[dict]:
    """Campos detectados (o OCR fica em cache: cada página só é lida uma vez)."""
    return mod_campos.detectar(img, ocr.ler(img, cache_ocr(raiz_saida, sha_pixels)))
