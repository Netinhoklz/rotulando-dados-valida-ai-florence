"""Confere a SAÍDA gravada (qualquer origem: gerador ou edição manual), versão por versão.

    python tests_ui/verifica_saida.py SAIDA

Invariantes (com canal de envio, "editada ≠ original exatamente na máscara" vira a conferência
``canal.conferir_par``; a banda de ignorar é conferida contra a borda/folga da região; o "mesmo
formato" compara as extensões):
  - arquivos citados existem; editada e máscaras com o tamanho da anotação;
  - original e editada no mesmo formato/qualidade; em PNG, editada ≠ original exatamente na máscara;
  - pixels alterados ⊆ região; máscara de técnicas e de classes cobrem exatamente a região;
  - números na máscara de técnicas = técnicas das edições, e a contagem de pixels de cada técnica
    bate com a soma dos "pixels" das edições;
  - cada edição: técnica do catálogo, descrição, e a classe na máscara de classes = a categoria dela;
  - manifesto.csv: uma linha por versão; catalogo.csv: uma linha por edição; catalogo.json presente.
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from rotulador import canal, catalogo, documentos  # noqa: E402
from rotulador.registro import SPLITS  # noqa: E402

SAIDA = Path(sys.argv[1])
erros, n_versoes, n_edicoes = [], 0, 0
por_tecnica = Counter()


def abrir(p):
    with Image.open(p) as im:
        return np.array(im)


legenda = SAIDA / "catalogo.json"
if not legenda.is_file():
    erros.append("catalogo.json não existe")
else:
    nums = {t["num"] for t in json.loads(legenda.read_text(encoding="utf-8"))["tecnicas"]}
    if nums != {t.num for t in catalogo.TECNICAS.values()}:
        erros.append("catalogo.json desatualizado")
for sp in SPLITS:
    raiz = SAIDA / sp
    anots = sorted((raiz / "anotacoes").glob("*.json")) if (raiz / "anotacoes").is_dir() else []
    if not anots:
        continue
    total_ed = 0
    for arq in anots:
        a = json.loads(arq.read_text(encoding="utf-8"))
        nome = f"{sp}/{arq.stem}"
        n_versoes += 1
        arqs = {k: raiz / v for k, v in a["arquivos"].items()}
        faltam = [k for k, p in arqs.items() if not p.is_file()]
        if faltam:
            erros.append(f"{nome}: faltam {faltam}")
            continue
        W, H = a["largura"], a["altura"]
        ed = documentos.carregar(arqs["editada"], 0, 200 if a.get("formato") != "pdf" else (a["fonte"].get("dpi_pdf") or 200))
        orig = documentos.carregar(arqs["original"], 0, 200 if a.get("formato") != "pdf" else (a["fonte"].get("dpi_pdf") or 200))
        alt, reg, cla, tec = (abrir(arqs[k]) for k in ("mascara", "mascara_regiao", "mascara_classes", "mascara_tecnicas"))
        ign = abrir(arqs["mascara_ignorar"]) > 0 if "mascara_ignorar" in arqs else None
        for k, m in (("editada", ed), ("mascara", alt), ("regiao", reg), ("classes", cla), ("tecnicas", tec)):
            if m.shape[:2] != (H, W):
                erros.append(f"{nome}: {k} {m.shape[:2]} != {(H, W)}")
        if arqs["original"].suffix != arqs["editada"].suffix:
            erros.append(f"{nome}: original e editada em formatos diferentes")
        alt, reg = alt > 0, reg > 0
        if ign is not None:  # banda de ignorar = 1 px em volta do que mudou + região que não mudou
            k = np.ones((3, 3), np.uint8)  # (+ espalhamento do codec fora da região, com canal)
            esperado = (cv2.dilate(alt.astype(np.uint8), k) > 0) & ~alt | (reg & ~alt)
            # sem canal, exato; com canal, pode haver o espalhamento do codec fora da região
            extra_ok = bool(a.get("canal"))
            if (esperado & ~ign).any() or (ign & ~esperado & (reg if extra_ok else True)).any() or (ign & alt).any():
                erros.append(f"{nome}: máscara de ignorar diferente da borda/folga da região")
        if (alt & ~reg).any():
            erros.append(f"{nome}: pixel alterado fora da região")
        if not np.array_equal(tec > 0, reg):
            erros.append(f"{nome}: máscara de técnicas ≠ região ({int((tec > 0).sum())} x {int(reg.sum())})")
        if not np.array_equal(cla > 0, reg):
            erros.append(f"{nome}: máscara de classes ≠ região")
        if a.get("canal"):  # mesmo canal no par: só pode diferir perto da edição
            motivo = canal.conferir_par(orig, ed, reg, a["canal"], extra=24 if a.get("formato") != "png" else 0,
                                        webp=a.get("formato") == "webp" and (a.get("qualidade") or 100) < 100)
            if motivo:
                erros.append(f"{nome}: original x editada depois do canal: {motivo}")
        elif a.get("formato") == "png" and not np.array_equal((ed != orig).any(2), alt):
            erros.append(f"{nome}: editada x original não bate com a máscara de pixels alterados")
        eds = a.get("edicoes", [])
        n_edicoes += len(eds)
        total_ed += len(eds)
        soma = Counter()
        for e in eds:
            t = catalogo.POR_NUM.get(e["tecnica_num"])
            if t is None or t.id != e["tecnica"] or not e.get("descricao"):
                erros.append(f"{nome}: edição {e.get('ordem')} com técnica/descrição inválida")
                continue
            soma[e["tecnica_num"]] += e["pixels"]
            por_tecnica[e["tecnica"]] += 1
            x0, y0, x1, y1 = e["bbox"]
            sub = tec[y0:y1, x0:x1] == e["tecnica_num"]
            if not sub.any():
                erros.append(f"{nome}: edição {e['ordem']} ({e['tecnica']}) sem pixels na caixa")
                continue
            cats = cla[y0:y1, x0:x1][sub]
            cid = a["ids_categoria"].get(e["categoria"])
            if cid is None or (cats == cid).mean() < 0.99:
                erros.append(f"{nome}: edição {e['ordem']} ({e['tecnica']}) com classe errada na máscara")
        cont = Counter({int(v): int(n) for v, n in zip(*np.unique(tec[tec > 0], return_counts=True))})
        if cont != soma:
            erros.append(f"{nome}: pixels por técnica na máscara {dict(cont)} != edições {dict(soma)}")
    with open(raiz / "manifesto.csv", encoding="utf-8") as f:
        if len(list(csv.DictReader(f))) != len(anots):
            erros.append(f"{sp}: manifesto.csv não tem uma linha por versão")
    with open(raiz / "catalogo.csv", encoding="utf-8") as f:
        if len(list(csv.DictReader(f))) != total_ed:
            erros.append(f"{sp}: catalogo.csv não tem uma linha por edição")
print(f"{n_versoes} versões, {n_edicoes} edições conferidas")
print("por técnica:", dict(por_tecnica.most_common()))
for e in erros:
    print("ERRO", e)
print("ERROS:", len(erros))
sys.exit(1 if erros else 0)
