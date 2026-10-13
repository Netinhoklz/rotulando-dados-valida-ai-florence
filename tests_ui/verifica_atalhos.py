"""Procura ATALHOS na saída: diferenças entre original e editada que não são a fraude em si e que um
modelo aprenderia no lugar dela (o viés de formato/qualidade do CASIA v2 é o exemplo clássico).

    python tests_ui/verifica_atalhos.py SAIDA

ERRO:
  - par original/editada com contêiner diferente: formato, modo, tamanho, chaves de metadados,
    tabelas de quantização ou subamostragem do JPEG, sequência de chunks do PNG;
  - metadado em arquivo de saída (EXIF, XMP, ICC, texto/tempo do PNG, datas do PDF);
  - pixel diferente FORA da região editada (em JPEG, fora dos blocos 8x8/16x16 que a tocam,
    com um bloco de folga por causa da reamostragem do croma no decodificador). Com canal de envio,
    fora do raio ``canal.raio`` (+24 px se a gravação final tem perda); com WEBP com perda (no canal
    ou na gravação), conferência estatística (média < 2,5 e p99,9 ≤ 48 longe da edição).
RELATÓRIO (para olhar):
  - técnicas, categorias e níveis por split;
  - texto falso: quanto das palavras INVENTADAS do teste/validação (as do texto novo que não estavam
    no antigo, fora das técnicas de cópia) já apareceu como inventada no treino; o vocabulário é
    repartido por split, então deve ficar perto de 0 (sobram sufixos como JUNIOR/FILHO/NETO).
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, JpegImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from rotulador import canal, documentos  # noqa: E402
from rotulador.registro import SPLITS  # noqa: E402

SAIDA = Path(sys.argv[1])
METADADOS = (b"Exif", b"<x:xmpmeta", b"ICC_PROFILE", b"tEXt", b"iTXt", b"zTXt", b"tIME", b"iCCP",
             b"/CreationDate", b"/ModDate", b"/Producer", b"/Creator", b"/Author", b"/Title")
erros = []


def chunks_png(dados: bytes) -> list[str]:
    tipos, i = [], 8
    while i + 8 <= len(dados):
        n = int.from_bytes(dados[i:i + 4], "big")
        tipos.append(dados[i + 4:i + 8].decode("latin-1"))
        i += 12 + n
    return [t for k, t in enumerate(tipos) if t != "IDAT" or "IDAT" not in tipos[:k]]  # IDATs contam 1


def conteiner(p: Path) -> dict:
    dados = p.read_bytes()
    if p.suffix == ".pdf":  # o Pillow não abre PDF: decodifica pelo PyMuPDF (o PDF foi gravado a 200 dpi)
        px = documentos.carregar(p, 0, 200)
        return {"formato": "PDF", "tamanho": px.shape[:2], "metadados": sorted(m.decode() for m in METADADOS
                                                                                if m in dados)}, px
    with Image.open(p) as im:
        d = {"formato": im.format, "modo": im.mode, "tamanho": im.size, "info": sorted(im.info)}
        if im.format == "JPEG":
            d["quant"] = {k: list(v) for k, v in im.quantization.items()}
            d["amostragem"] = JpegImagePlugin.get_sampling(im)
            d["app"] = [m for m, _ in im.applist]
        px = np.array(im.convert("RGB"))
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        d["chunks"] = chunks_png(dados)
    corpo = dados[:4096] + dados[-4096:] if p.suffix != ".pdf" else dados
    d["metadados"] = sorted(m.decode() for m in METADADOS if m in corpo)
    return d, px


def tokens(texto: str) -> set[str]:
    return {t for t in re.findall(r"[A-ZÀ-Ú]{3,}", (texto or "").upper())}


por_split = {sp: {"tec": Counter(), "cat": Counter(), "nivel": Counter(), "versoes": 0, "pixels": []} for sp in SPLITS}
falsos = defaultdict(lambda: defaultdict(set))   # split -> categoria -> tokens dos textos novos
autent = defaultdict(lambda: defaultdict(set))   # split -> categoria -> tokens dos textos antigos
textos_novos = defaultdict(set)                  # texto novo -> splits
n_pares = 0
for sp in SPLITS:
    raiz = SAIDA / sp
    for arq in sorted((raiz / "anotacoes").glob("*.json")) if (raiz / "anotacoes").is_dir() else []:
        a = json.loads(arq.read_text(encoding="utf-8"))
        nome = f"{sp}/{arq.stem}"
        r = por_split[sp]
        r["versoes"] += 1
        r["pixels"].append(a.get("fracao_alterada", 0))
        for e in a.get("edicoes", []):
            r["tec"][e["tecnica"]] += 1
            r["cat"][e["categoria"]] += 1
            r["nivel"][e.get("nivel") or "?"] += 1
            if e.get("texto_novo") and e.get("familia") not in ("copia_interna", "colagem_externa"):
                falsos[sp][e["categoria"]] |= tokens(e["texto_novo"]) - tokens(e.get("texto_antigo"))
                textos_novos[e["texto_novo"].strip().upper()].add(sp)
            if e.get("texto_antigo"):
                autent[sp][e["categoria"]] |= tokens(e["texto_antigo"])
        arqs = a["arquivos"]
        po, pe = raiz / arqs["original"], raiz / arqs["editada"]
        co, orig = conteiner(po)
        ce, ed = conteiner(pe)
        n_pares += 1
        for k in sorted(set(co) | set(ce)):
            if k != "metadados" and co.get(k) != ce.get(k):
                erros.append(f"{nome}: contêiner difere em '{k}': original {co.get(k)} x editada {ce.get(k)}")
        for c, qual in ((co, "original"), (ce, "editada")):
            if c["metadados"]:
                erros.append(f"{nome}: {qual} com metadados {c['metadados']}")
        if orig.shape != ed.shape:
            continue
        with Image.open(raiz / arqs["mascara_regiao"]) as m:
            regiao = np.array(m) > 0
        dif = (orig != ed).any(2)
        webp_perda = a["formato"] == "webp" and (a.get("qualidade") or 100) < 100
        if a.get("canal") or webp_perda:  # mesmo canal no par / WEBP: a diferença pode se espalhar
            perda = a["formato"] not in ("png", "tiff")
            motivo = canal.conferir_par(orig, ed, regiao, a.get("canal"), extra=24 if perda else 0, webp=webp_perda)
            if motivo:
                erros.append(f"{nome}: {motivo} ({a.get('canal_descricao')})")
            continue
        if a["formato"] in ("png", "tiff") or (a["formato"] == "webp" and a.get("qualidade") in (None, 100)):
            permitido = regiao
        else:  # com perda: blocos do JPEG que tocam a região, mais um bloco de folga
            mcu = 8 if co.get("amostragem") in (0, None) else 16
            H, W = regiao.shape
            hb, wb = -(-H // mcu), -(-W // mcu)
            pad = np.zeros((hb * mcu, wb * mcu), bool)
            pad[:H, :W] = regiao
            blocos = pad.reshape(hb, mcu, wb, mcu).any((1, 3)).astype(np.uint8)
            blocos = cv2.dilate(blocos, np.ones((3, 3), np.uint8)) > 0
            permitido = np.kron(blocos, np.ones((mcu, mcu), bool))[:H, :W]
        fora = dif & ~permitido
        if fora.any():
            ys, xs = np.nonzero(fora)
            erros.append(f"{nome}: {int(fora.sum())} px diferentes fora da região editada "
                         f"(ex.: x={xs[0]}, y={ys[0]}; formato {a['formato']})")

print(f"{n_pares} pares original/editada conferidos (contêiner, metadados, pixels fora da região)\n")
print("== distribuição por split")
for sp in SPLITS:
    r = por_split[sp]
    if not r["versoes"]:
        continue
    fr = np.array(r["pixels"]) * 100
    print(f"{sp}: {r['versoes']} versões, {sum(r['tec'].values())} edições, área editada mediana {np.median(fr):.3f}% "
          f"(máx {fr.max():.2f}%)")
    print("   níveis:", dict(r["nivel"].most_common()))
    print("   categorias:", dict(r["cat"].most_common()))
tecs = sorted(set().union(*(por_split[s]["tec"] for s in SPLITS)))
so_um = [t for t in tecs if sum(1 for s in SPLITS if por_split[s]["tec"][t]) == 1]
if so_um:
    print("   técnicas que só aparecem em um split:", so_um)

print("\n== texto falso x texto autêntico (tokens de 3+ letras)")
print("   % das palavras de um split que já apareceram no TREINO (inventada: entre as inventadas do treino;"
      " autêntica: entre as autênticas do treino)")
for sp in ("teste", "validacao"):
    for cat in sorted(set(falsos[sp]) | set(autent[sp])):
        f, f_tr = falsos[sp][cat], falsos["treino"][cat]
        au, au_tr = autent[sp][cat], autent["treino"][cat]
        if not f:
            continue
        pf = 100 * len(f & f_tr) / len(f)
        pa = 100 * len(au & au_tr) / len(au) if au else float("nan")
        repet = f"  {sorted(f & f_tr)[:4]}" if f & f_tr else ""
        print(f"   {sp:9s} {cat:24s} inventada {pf:5.1f}% de {len(f):3d}   autêntica {pa:5.1f}% de {len(au):3d}{repet}")
rep = sorted((t, sorted(s)) for t, s in textos_novos.items() if len(s) > 1)
print(f"   textos falsos idênticos em mais de um split: {len(rep)}" + (f" (ex.: {rep[:5]})" if rep else ""))

print()
for e in erros:
    print("ERRO", e)
print("ERROS:", len(erros))
sys.exit(1 if erros else 0)
