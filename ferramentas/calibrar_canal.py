"""Calibra o canal de envio com arquivos REAIS: quanto o WhatsApp, o celular, o scanner e os apps comprimem e reduzem.

    python ferramentas/calibrar_canal.py PASTA [--saida config/canal_calibrado.json]

PASTA tem uma subpasta por caminho, com arquivos que passaram MESMO por ele (de preferência comprovantes ou outros
documentos, nunca editados depois):

    PASTA/whatsapp/       imagens recebidas pelo WhatsApp (salvas do celular, não "como documento")
    PASTA/whatsapp_web/   imagens baixadas do WhatsApp Web
    PASTA/rede_social/    imagens baixadas de rede social
    PASTA/celular/        fotos de papel tiradas com o celular (o arquivo da câmera)
    PASTA/scanner/        scans salvos em JPEG
    PASTA/app_scanner/    saídas de CamScanner, Adobe Scan, Notas do iPhone...

Por caminho mede, em cada JPEG: a qualidade (a tabela de quantização comparada com as do libjpeg) e o lado maior. Grava
as faixas (percentis 10 e 90) em ``faixas``, que ``rotulador.canal`` passa a usar no lugar das da literatura; o ruído e
a nitidez saem só no relatório (num arquivo real eles misturam papel, sensor e compressão: não viram parâmetro).
Com menos de 3 arquivos num caminho, ele fica de fora (a faixa padrão continua valendo).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from rotulador import canal  # noqa: E402

CAMINHOS = ("whatsapp", "whatsapp_web", "rede_social", "celular", "scanner", "app_scanner")
EXTENSOES = {".jpg", ".jpeg", ".jfif"}
MINIMO = 3


def medir(arq: Path) -> dict | None:
    """Qualidade JPEG, lado maior, ruído (desvio do resíduo passa-alta no papel) e nitidez (variância do Laplaciano)."""
    try:
        with Image.open(arq) as im:
            if im.format not in ("JPEG", "MPO"):
                return None
            q = canal.qualidade_da_tabela(im.quantization[0])
            lado = max(im.size)
            cinza = np.asarray(im.convert("L"), np.float32)
    except (OSError, KeyError):
        return None
    passa_alta = cinza - cv2.GaussianBlur(cinza, (0, 0), 1.5)
    papel = cinza > np.percentile(cinza, 60)
    return {"jpeg": q, "lado": lado, "ruido": round(float(passa_alta[papel].std()), 3),
            "nitidez": round(float(cv2.Laplacian(cinza, cv2.CV_32F).var()), 1)}


def calibrar(pasta: Path, log=print) -> dict:
    faixas, relatorio = {}, {}
    for caminho in CAMINHOS:
        arqs = sorted(p for p in (pasta / caminho).glob("*") if p.suffix.lower() in EXTENSOES) \
            if (pasta / caminho).is_dir() else []
        medidas = [m for m in (medir(a) for a in arqs) if m]
        if len(medidas) < MINIMO:
            if arqs:
                log(f"{caminho}: só {len(medidas)} JPEG legível(is) (< {MINIMO}): fica a faixa padrão")
            continue
        rel = {}
        for k in ("jpeg", "lado", "ruido", "nitidez"):
            v = np.array([m[k] for m in medidas], float)
            rel[k] = {"p10": round(float(np.percentile(v, 10)), 2), "mediana": round(float(np.median(v)), 2),
                      "p90": round(float(np.percentile(v, 90)), 2)}
        relatorio[caminho] = {"arquivos": len(medidas), **rel}
        faixas[caminho] = {"jpeg": [int(round(rel["jpeg"]["p10"])), int(round(rel["jpeg"]["p90"]))]}
        if caminho in ("whatsapp", "whatsapp_web", "rede_social"):  # o lado maior é o limite que a transmissão impõe
            faixas[caminho]["lado"] = [int(round(rel["lado"]["p10"])), int(round(rel["lado"]["p90"]))]
        log(f"{caminho}: {len(medidas)} arquivos; JPEG {faixas[caminho]['jpeg']}"
            + (f", lado {faixas[caminho]['lado']}" if "lado" in faixas[caminho] else ""))
    return {"data": time.strftime("%Y-%m-%d"), "pasta": str(pasta), "faixas": faixas, "relatorio": relatorio}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pasta", type=Path)
    ap.add_argument("--saida", type=Path, default=RAIZ / "config" / "canal_calibrado.json")
    a = ap.parse_args(argv)
    r = calibrar(a.pasta)
    if not r["faixas"]:
        print("nenhum caminho com arquivos suficientes: nada gravado")
        return 1
    a.saida.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"gravado em {a.saida}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
