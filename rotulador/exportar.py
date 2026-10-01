"""Gravação do par (original, editada) + máscaras + anotação, sempre no split de origem.

Regras anti-vazamento/anti-atalho aplicadas aqui:
  - o split vem do registro (Item), nunca do navegador; todo caminho escrito é
    conferido contra saida/<split>;
  - original e editada do par saem no MESMO formato e qualidade (o modelo não pode
    separar as classes pela compressão);
  - nenhum metadado (EXIF, ICC, texto) vai para os arquivos;
  - nomes neutros (hash do conteúdo), o nome original do arquivo fica só no JSON;
  - cada arquivo é gravado em .tmp e renomeado; o JSON é o último (marca de "completo").
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .registro import SPLITS
from .sessao import ErroOperacao, Sessao

VERSAO_FERRAMENTA = "1.0"
FORMATOS = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "tiff": ".tif", "pdf": ".pdf"}
COM_PERDA = {"jpeg", "webp", "pdf"}
CAMPOS_MANIFESTO = ["split", "doc", "versao", "original", "editada", "mascara", "mascara_regiao",
                    "mascara_classes", "anotacao", "categorias", "formato", "qualidade", "largura", "altura",
                    "pixels_alterados", "fonte", "pagina", "sha_arquivo", "sha_pixels", "operador", "data"]
_lock = threading.Lock()


def _dentro(caminho: Path, raiz: Path) -> Path:
    c, r = caminho.resolve(), raiz.resolve()
    if not c.is_relative_to(r):
        raise ErroOperacao(f"caminho fora da pasta do split: {c}")
    return c


def codificar(img: np.ndarray, formato: str, qualidade: int, dpi: int = 200) -> bytes:
    """Codifica sem metadados. Usado igualzinho para original e editada."""
    im = Image.fromarray(img) if img.ndim == 3 else Image.fromarray(img, "L")
    buf = io.BytesIO()
    if formato == "png":
        im.save(buf, "PNG", compress_level=6)
    elif formato == "jpeg":
        im.save(buf, "JPEG", quality=qualidade, subsampling=0 if qualidade >= 95 else 2, optimize=False)
    elif formato == "webp":
        im.save(buf, "WEBP", quality=qualidade, lossless=qualidade >= 100, method=4)
    elif formato == "tiff":
        im.save(buf, "TIFF", compression="tiff_lzw")
    elif formato == "pdf":
        im.save(buf, "PDF", resolution=float(dpi), quality=qualidade)
    else:
        raise ErroOperacao(f"formato de saída desconhecido: {formato}")
    return buf.getvalue()


def _gravar(caminho: Path, dados: bytes) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(caminho.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(dados)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, caminho)


def proxima_versao(pasta: Path, doc: str) -> int:
    padrao = re.compile(re.escape(doc) + r"__v(\d+)")
    maior = 0
    for sub in ("editadas", "anotacoes"):
        d = pasta / sub
        if d.is_dir():
            for arq in d.iterdir():
                m = padrao.match(arq.name)
                if m:
                    maior = max(maior, int(m.group(1)))
    return maior + 1


def versoes_salvas(raiz_saida: Path, split: str, doc: str) -> int:
    return proxima_versao(Path(raiz_saida) / split, doc) - 1


def _caixas_por_categoria(classes: np.ndarray, ids: dict[str, int]) -> dict[str, list[int]]:
    out = {}
    for chave, i in ids.items():
        m = classes == i
        if m.any():
            ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
            out[chave] = [int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1]
    return out


def salvar(sessao: Sessao, raiz_saida: Path, formato: str = "png", qualidade: int = 95,
           operador: str = "", dpi: int = 200) -> dict:
    formato = formato.lower()
    if formato not in FORMATOS:
        raise ErroOperacao(f"formato de saída desconhecido: {formato}")
    qualidade = int(max(1, min(100, qualidade)))
    with sessao.lock, _lock:
        ok, motivo = sessao.pode_salvar()
        if not ok:
            raise ErroOperacao(f"não dá para salvar: {motivo}")
        it = sessao.item
        if it.split not in SPLITS:
            raise ErroOperacao("split inválido")
        raiz_split = (Path(raiz_saida) / it.split).resolve()
        raiz_split.mkdir(parents=True, exist_ok=True)
        versao = proxima_versao(raiz_split, it.doc)
        ext = FORMATOS[formato]
        sufixo_q = f"__q{qualidade}" if formato in COM_PERDA else ""
        nome = f"{it.doc}__v{versao:02d}"
        arq = {
            "original": raiz_split / "originais" / f"{it.doc}{sufixo_q}{ext}",
            "editada": raiz_split / "editadas" / f"{nome}{sufixo_q}{ext}",
            "mascara": raiz_split / "mascaras" / f"{nome}.png",
            "mascara_regiao": raiz_split / "mascaras_regiao" / f"{nome}.png",
            "mascara_classes": raiz_split / "mascaras_classes" / f"{nome}.png",
            "anotacao": raiz_split / "anotacoes" / f"{nome}.json",
        }
        for c in arq.values():
            _dentro(c, raiz_split)

        alterados, regiao, classes = sessao.mascaras()
        categorias = sessao.categorias()
        if not arq["original"].exists():
            _gravar(arq["original"], codificar(sessao.base, formato, qualidade, dpi))
        _gravar(arq["editada"], codificar(sessao.atual, formato, qualidade, dpi))
        _gravar(arq["mascara"], codificar(alterados.astype(np.uint8) * 255, "png", 0))
        _gravar(arq["mascara_regiao"], codificar(regiao.astype(np.uint8) * 255, "png", 0))
        _gravar(arq["mascara_classes"], codificar(classes, "png", 0))

        H, W = alterados.shape
        rel = {k: v.relative_to(raiz_split).as_posix() for k, v in arq.items()}
        data = time.strftime("%Y-%m-%dT%H:%M:%S")
        ids_usados = {c: sessao.ids_categoria[c] for c in categorias}
        anotacao = {
            "versao_ferramenta": VERSAO_FERRAMENTA,
            "split": it.split, "doc": it.doc, "versao": versao,
            "fonte": {"arquivo": it.rel, "pagina": it.pagina, "paginas": it.paginas,
                      "sha_arquivo": it.sha_arquivo, "sha_pixels": it.sha_pixels,
                      "dpi_pdf": dpi if it.caminho.suffix.lower() == ".pdf" else None},
            "arquivos": rel, "formato": formato, "qualidade": qualidade if formato in COM_PERDA else None,
            "largura": W, "altura": H,
            "categorias": categorias, "ids_categoria": ids_usados,
            "caixas_por_categoria": _caixas_por_categoria(classes, ids_usados),
            "pixels_alterados": int(alterados.sum()), "pixels_regiao": int(regiao.sum()),
            "fracao_alterada": round(float(alterados.mean()), 6),
            "mascaras": {"mascara": "0/255: pixels cujo valor mudou (antes da compressão de saída)",
                         "mascara_regiao": "0/255: área editada (pegada das operações)",
                         "mascara_classes": "uint8: id da categoria por pixel; 0 = não editado"},
            "operacoes": sessao.historico(),
            "operador": operador, "data": data,
        }
        _gravar(arq["anotacao"], json.dumps(anotacao, ensure_ascii=False, indent=1).encode("utf-8"))

        manifesto = raiz_split / "manifesto.csv"
        novo = not manifesto.exists()
        with open(manifesto, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, CAMPOS_MANIFESTO)
            if novo:
                w.writeheader()
            w.writerow({"split": it.split, "doc": it.doc, "versao": versao, **rel,
                        "categorias": ";".join(categorias), "formato": formato,
                        "qualidade": qualidade if formato in COM_PERDA else "", "largura": W, "altura": H,
                        "pixels_alterados": int(alterados.sum()), "fonte": it.rel, "pagina": it.pagina,
                        "sha_arquivo": it.sha_arquivo, "sha_pixels": it.sha_pixels, "operador": operador,
                        "data": data})
        sessao.recomecar()
        return {"versao": versao, "arquivos": rel, "categorias": categorias, "split": it.split}
