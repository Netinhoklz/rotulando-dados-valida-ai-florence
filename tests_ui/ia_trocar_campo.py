"""Ponta a ponta do "Trocar com IA": OCR acha nome e endereço, a IA escreve o valor novo na região.

Precisa do rotulador e do servidor de IA rodando. Uso:
    python tests_ui/ia_trocar_campo.py <pasta_saida_dos_recortes> [--url http://127.0.0.1:5010] [--modelos 2511,21uc]
Não salva nada: descarta a sessão no fim.
"""
import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
ap = argparse.ArgumentParser()
ap.add_argument("saida", type=Path)
ap.add_argument("--url", default="http://127.0.0.1:5010")
ap.add_argument("--modelos", default="2511,21uc")
ap.add_argument("--doc", default="conta_01.png")
a = ap.parse_args()
a.saida.mkdir(parents=True, exist_ok=True)
NOVOS = {"nome": "MARIANA COSTA FERREIRA", "endereço": "RUA DAS ACACIAS, 1290 - APTO 32"}


def api(rota, corpo=None, timeout=60):
    req = urllib.request.Request(a.url + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        return {"erro_http": e.code, **json.loads(e.read())}


def imagem(doc, qual):
    return np.asarray(Image.open(io.BytesIO(urllib.request.urlopen(f"{a.url}/api/img/{doc}/{qual}.png").read())).convert("RGB"))


st = api("/api/ia/status")
print("IA online:", st.get("online"), "| modelos:", [m["id"] for m in st.get("modelos", [])])
doc = next(d["id"] for d in api("/api/documentos?split=treino") if d["nome"] == a.doc)
campos = api(f"/api/campos/{doc}", timeout=300)["campos"]
alvos = {cat: next((c for c in campos if c.get("categoria") == cat and c["tipo"] == "texto"), None) for cat in NOVOS}
for cat, c in alvos.items():
    print(f"campo {cat!r}:", c and (c["texto"], c["caixa"]))
falhas = 0
for modelo in a.modelos.split(","):
    api("/api/descartar", {"id": doc})
    api("/api/abrir", {"id": doc})
    for cat, c in alvos.items():
        if c is None:
            print(f"[{modelo}] {cat}: o OCR não achou o campo"); falhas += 1
            continue
        t0 = time.time()
        r = api("/api/op", {"id": doc, "tipo": "trocar_campo_ia", "categoria": cat,
                            "params": {"caixa": c["caixa"], "texto": NOVOS[cat], "texto_antigo": c["texto"],
                                       "campo": c["id"], "modelo": modelo, "semente": 7}}, timeout=3600)
        if "erro" in r or "erro_http" in r:
            print(f"[{modelo}] {cat}: ERRO {r.get('erro')}"); falhas += 1
            continue
        meta = r["estado"]["historico"][-1].get("meta", {})
        print(f"[{modelo}] {cat}: {round(time.time() - t0)} s | difusão {meta.get('segundos_difusao')} s | "
              f"pico VRAM {meta.get('pico_vram_gb')} GB")
    atual, orig = imagem(doc, "atual"), imagem(doc, "original")
    mudou = (atual != orig).any(2)
    for cat, c in alvos.items():
        if c is None:
            continue
        x0, y0, x1, y1 = c["caixa"]
        h = y1 - y0
        cy0, cy1, cx0, cx1 = max(0, y0 - 2 * h), y1 + 2 * h, max(0, x0 - 2 * h), min(orig.shape[1], x1 + 12 * h)
        Image.fromarray(np.vstack([orig[cy0:cy1, cx0:cx1], atual[cy0:cy1, cx0:cx1]])).save(
            a.saida / f"{modelo}_{cat.replace('ç', 'c')}.png")
    # nada pode mudar longe dos campos trocados (a região da IA tem folga e alarga para texto mais longo)
    permitido = np.zeros_like(mudou)
    for c in filter(None, alvos.values()):
        x0, y0, x1, y1 = c["caixa"]
        f = max(4, int(0.3 * (y1 - y0))) + 1
        permitido[max(0, y0 - f):y1 + f, max(0, x0 - f):] = True
    fora = int((mudou & ~permitido).sum())
    print(f"[{modelo}] pixels alterados: {int(mudou.sum())} | fora das regiões: {fora}")
    falhas += fora > 0
api("/api/descartar", {"id": doc})
print("FALHAS:", falhas)
sys.exit(1 if falhas else 0)
