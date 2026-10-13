"""Recall proxy do detector de campos (rotulador.campos) em contas REAIS, de qualquer layout.

    python tests_ui/benchmark_campos.py PASTA SAIDA

Para cada documento de PASTA (imagem ou PDF, todas as páginas, subpastas incluídas) roda o OCR e a
detecção de campos (``campos_do_documento``, com o OCR em cache em SAIDA/_cache/ocr, como no rotulador)
e grava:
  SAIDA/campos.csv         um campo por linha: doc, categoria, texto, conf, caixa (x0 y0 x1 y1)
  SAIDA/resumo.md          por categoria, em quantos documentos foi achada; por documento, quais
                           categorias do conjunto essencial (nome, cpf/cnpj, endereço, cep, data, valor,
                           codigo barras) faltam
  SAIDA/folhas/<doc>.png   o documento (máx. 1600 px no lado maior) com as caixas desenhadas e a
                           categoria escrita, para revisão humana
Não há verdade de campo: o "recall" é por presença das categorias que toda conta tem (proxy) e por
inspeção das folhas. Nada é gravado em PASTA.
"""

from __future__ import annotations

import csv
import re
import sys
import zlib
from collections import Counter
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rotulador import automatico, documentos  # noqa: E402
from rotulador.registro import hash_pixels  # noqa: E402

ESSENCIAIS = ("nome", "cpf/cnpj", "endereço", "cep", "data", "valor", "codigo barras")
PALETA = [(220, 40, 40), (30, 110, 230), (20, 150, 70), (200, 120, 0), (150, 40, 180), (0, 140, 150),
          (120, 80, 20), (230, 60, 150)]
MAX_LADO = 1600


def _fonte(tam: int):
    try:
        return ImageFont.truetype("arial.ttf", tam)
    except OSError:
        return ImageFont.load_default(size=tam)


def folha(img, campos: list[dict], caminho: Path) -> None:
    """O documento reduzido com uma caixa por campo e a categoria escrita em cima."""
    esc = min(1.0, MAX_LADO / max(img.shape[:2]))
    if esc < 1:
        tam = (max(1, int(img.shape[1] * esc)), max(1, int(img.shape[0] * esc)))
        img = cv2.resize(img, tam, interpolation=cv2.INTER_AREA)
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    f = _fonte(13)
    for c in campos:
        cat = c.get("categoria") or "?"
        cor = PALETA[zlib.crc32(cat.encode()) % len(PALETA)]
        x0, y0, x1, y1 = (int(round(v * esc)) for v in c["caixa"])
        d.rectangle([x0, y0, x1, y1], outline=cor, width=2)
        d.text((x0, max(0, y0 - 15)), cat, fill=cor, font=f)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    im.save(caminho)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pasta, saida = Path(sys.argv[1]), Path(sys.argv[2])
    arquivos = sorted(p for p in pasta.rglob("*") if p.is_file() and documentos.eh_documento(p))
    if not arquivos:
        sys.exit(f"nenhum documento em {pasta}")
    saida.mkdir(parents=True, exist_ok=True)
    linhas, achadas, faltam, n_campos = [], Counter(), {}, {}
    for p in arquivos:
        paginas = documentos.contar_paginas(p)
        for pg in range(paginas):
            sufixo = f"#p{pg + 1}" if paginas > 1 else ""
            nome = p.relative_to(pasta).as_posix() + sufixo
            img = documentos.carregar(p, pg)
            campos = automatico.campos_do_documento(img, saida, hash_pixels(img))
            cats = {c.get("categoria") for c in campos} - {None}
            achadas.update(cats)
            faltam[nome] = [c for c in ESSENCIAIS if c not in cats]
            n_campos[nome] = len(campos)
            linhas += [{"doc": nome, "categoria": c.get("categoria") or "", "texto": c["texto"], "conf": c["conf"],
                        "caixa": " ".join(str(v) for v in c["caixa"])} for c in campos]
            base = re.sub(r"[^\w.-]+", "_", p.relative_to(pasta).with_suffix("").as_posix() + sufixo)
            destino = saida / "folhas" / f"{base}.png"
            if destino.exists():  # x.png e x.jpg na mesma pasta
                destino = saida / "folhas" / f"{base}_{p.suffix[1:]}.png"
            folha(img, campos, destino)
            print(f"{nome}: {len(campos)} campo(s)" + (f"; faltam {', '.join(faltam[nome])}" if faltam[nome] else ""))
    with open(saida / "campos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["doc", "categoria", "texto", "conf", "caixa"])
        w.writeheader()
        w.writerows(linhas)
    n = len(faltam)
    md = [f"# Campos detectados em {n} documento(s) de `{pasta}`", "",
          "Recall proxy: sem verdade de campo, conta-se em quantos documentos cada categoria foi achada e quais "
          f"categorias essenciais ({', '.join(ESSENCIAIS)}) faltam em cada um. Confira as folhas em `folhas/`.", "",
          "## Por categoria", "", "| categoria | documentos | % |", "|---|---:|---:|"]
    md += [f"| {cat} | {k} | {100 * k / n:.0f} |" for cat, k in achadas.most_common()]
    md += ["", "## Por documento: categorias essenciais que faltam", "",
           "| documento | campos | faltam |", "|---|---:|---|"]
    md += [f"| {nome} | {n_campos[nome]} | {', '.join(f) or '–'} |" for nome, f in faltam.items()]
    completos = sum(1 for f in faltam.values() if not f)
    md += ["", f"Documentos com todas as essenciais: {completos}/{n}.", ""]
    (saida / "resumo.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n{len(linhas)} campo(s) em {n} documento(s); {completos} com todas as essenciais. "
          f"Saída: {saida / 'campos.csv'}, {saida / 'resumo.md'}, {saida / 'folhas'}")


if __name__ == "__main__":
    main()
