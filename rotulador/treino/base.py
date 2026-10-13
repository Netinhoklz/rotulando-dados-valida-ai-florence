"""Linha de base sem treino: o mapa de calor do perito (grão do papel e nitidez da tinta contra a própria
página, rotulador.perito.mapa) como "detector". Serve para (1) mostrar o formato de predição que ``avaliar``
espera, (2) medir quanto o dataset entrega de graça a um detector artesanal — AUC alta aqui seria um atalho nos
pares — e (3) dar o piso que um modelo treinado tem de superar.

    python -m rotulador.treino.base SAIDA SPLIT PRED [--limite N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .. import perito
from . import dados


def prever(img: np.ndarray) -> np.ndarray:
    """Mapa float32 0..1 da imagem sozinha (sem a original: o detector não a tem)."""
    return perito.mapa(None, img)


def _gravar(caminho: Path, prob: np.ndarray) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(prob * 255 + 0.5, 0, 255).astype(np.uint8)).save(caminho, "PNG", compress_level=6)


def gerar(raiz_saida: Path, split: str, destino: Path, limite: int | None = None) -> dict:
    """``destino/<nome>.png`` para cada versão e ``destino/originais/<original>.png`` para cada original."""
    amostras = dados.listar(raiz_saida, split)
    if limite:
        amostras = amostras[:int(limite)]
    destino = Path(destino)
    originais = set()
    for a in amostras:
        _gravar(destino / f"{a['nome']}.png", prever(dados._ler(a["arquivos"]["editada"], a["dpi"])))
        orig = Path(a["arquivos"]["original"])
        if orig.stem not in originais:
            originais.add(orig.stem)
            _gravar(destino / "originais" / f"{orig.stem}.png", prever(dados._ler(orig, a["dpi"])))
    return {"versoes": len(amostras), "originais": len(originais), "pasta": str(destino)}


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Predições da linha de base (perito) para um split.")
    ap.add_argument("saida")
    ap.add_argument("split")
    ap.add_argument("predicoes")
    ap.add_argument("--limite", type=int)
    a = ap.parse_args(argv)
    r = gerar(Path(a.saida), a.split, Path(a.predicoes), a.limite)
    print(f"{r['versoes']} versões e {r['originais']} originais em {r['pasta']}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(_main())
