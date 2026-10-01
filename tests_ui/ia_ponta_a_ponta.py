"""Ponta a ponta: rotulador (5077) -> servidor de IA (5051) num documento de exemplo."""
import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
S = Path(sys.argv[1])
URL = "http://127.0.0.1:5077"


def api(rota, corpo=None, timeout=60):
    req = urllib.request.Request(URL + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        return {"erro_http": e.code, **json.loads(e.read())}


for _ in range(60):
    st = api("/api/ia/status")
    if st.get("online"):
        break
    time.sleep(1)
print("status IA:", st)
doc = [d for d in api("/api/documentos?split=treino") if d["nome"] == "conta_01.png"][0]["id"]
api("/api/descartar", {"id": doc})
api("/api/abrir", {"id": doc})
sel = {"formas": [{"tipo": "ret", "x0": 790, "y0": 770, "x1": 1080, "y1": 832}]}
t0 = time.time()
r = api("/api/op", {"id": doc, "tipo": "ia", "categoria": "valor", "selecao": sel,
                    "params": {"prompt": 'Troque o texto "R$ 151,37" por "R$ 987,65", mantendo a mesma fonte, tamanho, cor e fundo.',
                               "semente": 7, "passos": 4}}, timeout=1800)
print("tempo:", round(time.time() - t0, 1), "s | erro:", r.get("erro"))
if "png" in r:
    est = r["estado"]
    print("historico:", est["historico"][-1]["rotulo"], "| meta:", {k: v for k, v in est["historico"][-1]["meta"].items() if k != "prompt"})
    img = np.asarray(Image.open(io.BytesIO(urllib.request.urlopen(f"{URL}/api/img/{doc}/atual.png").read())).convert("RGB"))
    orig = np.asarray(Image.open(io.BytesIO(urllib.request.urlopen(f"{URL}/api/img/{doc}/original.png").read())).convert("RGB"))
    mudou = (img != orig).any(2)
    ys, xs = np.nonzero(mudou)
    print("pixels alterados:", int(mudou.sum()), "| caixa alterada:", (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    print("fora da seleção mudou?", bool(mudou[:770].any() or mudou[832:].any() or mudou[:, :790].any() or mudou[:, 1080:].any()))
    Image.fromarray(img[720:880, 700:1150]).save(S / "ia_app_depois.png")
    Image.fromarray(orig[720:880, 700:1150]).save(S / "ia_app_antes.png")
