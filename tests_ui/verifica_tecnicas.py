"""Verificação de TODAS as técnicas automáticas em documentos de verdade (não sintéticos de teste).

    python tests_ui/verifica_tecnicas.py DADOS SAIDA [--ia] [--humano] [--tecnicas=id1,id2] [--docs=nome1,nome2]

``--humano``: cada edição com um operador humano sorteado (rotulador.humano; o "Salvar como" fica de fora: aqui não
há par para regravar). Além das conferências, cada edição ganha duas medidas de qualidade: a nota do perito
(0 = invisível, 1 = óbvia) e a diferença do p10 da luminância da tinta da linha antes x depois (texto novo mais claro
ou mais escuro que o antigo), resumidas por técnica no relatório.

Para cada documento × técnica, aplica a técnica em cada campo em que ela serve e confere:
  - exceção inesperada (bug) x recusa com motivo (a técnica não serve para aquele campo);
  - rótulo coerente (técnica, família, texto antigo ≠ novo, descrição);
  - nada mudou longe do campo;
  - o OCR da PÁGINA INTEIRA editada contém o valor novo e não contém mais o antigo (controle: o
    OCR do original contém o antigo); código de barras decodificado; QR lido pelo OpenCV.
Gera folhas de contato (antes/depois) por técnica em SAIDA/folhas para revisão visual e um
relatório em SAIDA/relatorio.json.
"""
import json
import random
import sys
import time
import traceback
import unicodedata
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from rotulador import catalogo, humano, ocr, perito  # noqa: E402
from rotulador.contexto import Contexto  # noqa: E402
from rotulador.ops import codigos, executar  # noqa: E402
from rotulador.ops.tecnicas import aplicavel, lido_igual  # noqa: E402
from rotulador.registro import SPLITS, Registro  # noqa: E402
from rotulador.selecao import vazia  # noqa: E402
from rotulador.sessao import ErroOperacao  # noqa: E402

DADOS, SAIDA = Path(sys.argv[1]), Path(sys.argv[2])
ARGS = sys.argv[3:]
COM_IA = "--ia" in ARGS
COM_HUMANO = "--humano" in ARGS
SO_TEC = next((a.split("=", 1)[1] for a in ARGS if a.startswith("--tecnicas=")), None)
SO_DOC = next((a.split("=", 1)[1] for a in ARGS if a.startswith("--docs=")), None)
FOLHAS = SAIDA / "folhas"
FOLHAS.mkdir(parents=True, exist_ok=True)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    return "".join(ch for ch in s if ch.isalnum())


def texto_na_regiao(linhas, caixa):
    x0, y0, x1, y1 = caixa
    sel = []
    for l in linhas:
        lx0, ly0, lx1, ly1 = l["caixa"]
        vert = min(y1, ly1) - max(y0, ly0)
        if vert > 0.5 * min(y1 - y0, ly1 - ly0) and min(x1, lx1) > max(x0, lx0):
            sel.append(l)
    return norm("".join(l["texto"] for l in ocr.em_ordem_de_leitura(sel)))


def decodifica_itf(img, caixa):
    """Lê os 44 dígitos de um código ITF-25 na caixa (larguras das barras pela linha do meio)."""
    x0, y0, x1, y1 = caixa
    linha = cv2.cvtColor(img[(y0 + y1) // 2 - 2:(y0 + y1) // 2 + 3, x0:x1], cv2.COLOR_RGB2GRAY).mean(0)
    preto = linha < (linha.max() + linha.min()) / 2
    corridas, atual, n = [], preto[0], 0
    for v in preto:
        if v == atual:
            n += 1
        else:
            corridas.append((atual, n))
            atual, n = v, 1
    corridas.append((atual, n))
    while corridas and not corridas[0][0]:
        corridas.pop(0)
    larg = [n for _, n in corridas]
    if len(larg) < 4 + 44 * 5 // 2:
        return None
    estreito = np.median(larg)
    bits = [1 if w > 1.8 * estreito else 0 for w in larg]
    dados = bits[4:]
    inv = {v: k for k, v in codigos._ITF.items()}
    digs = []
    for k in range(0, 22 * 10, 10):
        bloco = dados[k:k + 10]
        if len(bloco) < 10:
            return None
        a = "".join(str(b) for b in bloco[0::2])
        b = "".join(str(b) for b in bloco[1::2])
        if a not in inv or b not in inv:
            return None
        digs += [inv[a], inv[b]]
    return "".join(digs)


def rotular(img, linhas, cor=(0, 0, 0)):
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 15)
    for k, l in enumerate(linhas):
        d.text((4, 2 + 17 * k), l, font=f, fill=cor)
    return np.asarray(im)


reg = Registro(DADOS, SAIDA / "saida_tmp", log=lambda *_: None)
tecnicas = [t for t in catalogo.TECNICAS.values() if t.automatica and (COM_IA or not t.precisa_ia)]
if SO_TEC:
    tecnicas = [t for t in tecnicas if t.id in SO_TEC.split(",")]
if COM_IA and not SO_TEC:
    tecnicas = [t for t in tecnicas if t.precisa_ia]
relatorio = {t.id: {"aplicadas": 0, "recusas": [], "excecoes": [], "ocr_ok": 0, "ocr_divergente": [],
                    "sem_controle": 0, "fora_do_campo": [], "rotulo_ruim": [], "codigo_ok": 0, "tempo": [],
                    "nota": [], "tom": []}
             for t in tecnicas}
folhas = {t.id: [] for t in tecnicas}
t_ini = time.time()
for sp in SPLITS:
    for it in reg.por_split[sp]:
        if SO_DOC and not any(n in it.nome for n in SO_DOC.split(",")):
            continue
        base = reg.carregar(it.doc)
        ctx = Contexto(it, base, SAIDA / "saida_tmp", reg)
        campos = [c for c in ctx.campos if c.get("categoria")]
        linhas_orig = ctx.linhas
        print(f"\n== {sp}/{it.nome}: {len(campos)} campos", flush=True)
        for t in tecnicas:
            R = relatorio[t.id]
            textos = {}
            for f in campos:
                textos.setdefault(f["categoria"], set()).add(f["texto"].strip())
            cands = [f for f in campos if aplicavel(t, f)
                     and not (t.id == "campo.copiar_interno" and len(textos[f["categoria"]]) < 2)]
            if COM_IA:  # IA é lenta: um campo por técnica e documento
                pref = {"ia.reescrever": ("nome", "endereço"), "ia.caractere": ("valor", "cep", "data"), "ia.remover": ("cep", "bairro")}
                cands = [f for f in cands if f["categoria"] in pref[t.id]][:1]
            img = base.copy()
            feitos, usados = [], []
            for k, f in enumerate(cands):
                if any(not (f["caixa"][2] + 8 <= u[0] or u[2] + 8 <= f["caixa"][0] or f["caixa"][3] + 8 <= u[1]
                            or u[3] + 8 <= f["caixa"][1]) for u in usados):
                    continue
                p = {"tecnica": t.id, "campo": {k_: f[k_] for k_ in ("id", "caixa", "texto", "categoria", "tipo")},
                     "semente": random.Random(f"{it.doc}{t.id}{k}").randrange(2**31), "modelo": "2511"}
                if COM_HUMANO:
                    hab = humano.sortear(random.Random(p["semente"]), {"prob": 1.0}, bool(getattr(ctx, "jpeg", None)))
                    if hab["salvar"] == "jpeg_editor":
                        hab = {**hab, "salvar": "png"}
                    p["habitos"] = {"humano": hab}
                t0 = time.time()
                try:
                    res = executar("tecnica", img, p, vazia(*img.shape[:2]), base=base, documento=ctx)
                except ErroOperacao as e:
                    R["recusas"].append(f"{it.nome}/{f['categoria']}: {e}")
                    continue
                except Exception as e:  # bug
                    R["excecoes"].append(f"{it.nome}/{f['categoria']}: {e!r}\n{traceback.format_exc()[-800:]}")
                    print("   EXCEÇÃO", t.id, f["categoria"], repr(e), flush=True)
                    continue
                R["tempo"].append(round(time.time() - t0, 2))
                m = res.meta
                if not (m.get("tecnica") == t.id and m.get("descricao")
                        and (m.get("texto_novo") != m.get("texto_antigo") or t.id == "trecho.jitter")):
                    R["rotulo_ruim"].append(f"{it.nome}/{f['categoria']}: {m.get('tecnica')} {m.get('texto_antigo')!r}->{m.get('texto_novo')!r}")
                x0, y0, x1, y1 = f["caixa"]
                h, w = y1 - y0, x1 - x0
                perm = (x0 - max(3 * h, 0.6 * w), y0 - 1.3 * h, x1 + max(3 * h, 0.6 * w), y1 + 1.3 * h)
                if t.id in ("campo.acrescentar", "campo.colagem_externa", "campo.copiar_interno"):
                    perm = (perm[0] - 1.2 * w, perm[1], perm[2] + 1.2 * w, perm[3])  # crescem na mesma linha
                if res.x0 < perm[0] or res.y0 < perm[1] or res.x1 > perm[2] or res.y1 > perm[3]:
                    R["fora_do_campo"].append(f"{it.nome}/{f['categoria']}: caixa da edição {[res.x0, res.y0, res.x1, res.y1]} longe do campo {f['caixa']}")
                antes = img.copy()
                caixa = img[res.y0:res.y1, res.x0:res.x1]
                caixa[:] = np.where(res.pegada[..., None], res.recorte, caixa)
                if not (img != antes).any():
                    R["rotulo_ruim"].append(f"{it.nome}/{f['categoria']}: nenhum pixel mudou")
                usados.append([min(x0, res.x0), min(y0, res.y0), max(x1, res.x1), max(y1, res.y1)])
                # qualidade: a nota do perito na edição e o tom da tinta da linha antes x depois
                reg_ = np.zeros(img.shape[:2], bool)
                reg_[res.y0:res.y1, res.x0:res.x1] = res.pegada
                mm = 2 * max(16, res.y1 - res.y0)
                Y0, Y1 = max(0, res.y0 - mm), min(img.shape[0], res.y1 + mm)
                X0, X1 = max(0, res.x0 - mm), min(img.shape[1], res.x1 + mm)
                try:
                    med = perito.medir(antes[Y0:Y1, X0:X1], img[Y0:Y1, X0:X1], reg_[Y0:Y1, X0:X1])
                    nota = perito.nota(med)
                    if nota is not None:
                        R["nota"].append(float(nota))
                except Exception:  # noqa: BLE001
                    pass
                if f["tipo"] == "texto":
                    lum = np.float32([0.299, 0.587, 0.114])
                    ra, rb = antes[y0:y1, x0:x1], img[y0:y1, x0:x1]
                    ta, tb = ocr.tinta_da_linha(ra), ocr.tinta_da_linha(rb)
                    if ta.sum() > 20 and tb.sum() > 20:
                        R["tom"].append(float(np.percentile((rb.astype(np.float32) @ lum)[tb], 10)
                                              - np.percentile((ra.astype(np.float32) @ lum)[ta], 10)))
                        R.setdefault("tom_casos", []).append((round(R["tom"][-1], 1), f"{it.nome}/{f['categoria']}: "
                                                              f"{res.meta.get('texto_antigo')!r} -> {res.meta.get('texto_novo')!r}"))
                feitos.append((f, res))
                R["aplicadas"] += 1
            if not feitos:
                continue
            linhas_ed = ocr.ler(img)  # OCR da página editada inteira
            for f, res in feitos:
                m = res.meta
                regiao = [min(f["caixa"][0], res.x0), f["caixa"][1], max(f["caixa"][2], res.x1), f["caixa"][3]]
                lido = ""
                if f["tipo"] == "barras":
                    dig = decodifica_itf(img, f["caixa"])
                    if dig == m.get("digitos") and (codigos.valida_arrecadacao(dig or "") or codigos.valida_boleto(dig or "")):
                        R["codigo_ok"] += 1
                    else:
                        R["ocr_divergente"].append(f"{it.nome}: código lido {dig} != {m.get('digitos')}")
                    lido = f"ITF: {dig}"
                elif f["tipo"] == "qr":
                    x0, y0, x1, y1 = f["caixa"]
                    rec = cv2.cvtColor(img[y0 - 20:y1 + 20, x0 - 20:x1 + 20], cv2.COLOR_RGB2BGR)
                    val, _, _ = cv2.QRCodeDetector().detectAndDecode(rec)
                    if not val:  # o decodificador clássico falha em alguns QR válidos; o Aruco lê
                        val = cv2.QRCodeDetectorAruco().detectAndDecode(rec)[0]
                    if val == m.get("texto_novo"):
                        R["codigo_ok"] += 1
                    else:
                        R["ocr_divergente"].append(f"{it.nome}: QR lido {val[:30]!r}")
                    lido = f"QR: {val[:40]}"
                else:
                    controle = texto_na_regiao(linhas_orig, f["caixa"])
                    lido = texto_na_regiao(linhas_ed, regiao)
                    if not lido and m["texto_novo"]:  # o detector da página largou a linha: lê só o campo
                        x0, y0, x1, y1 = regiao
                        mm = max(3, (y1 - y0) // 5)
                        lido = norm(ocr.reconhecer(img[y0 - mm:y1 + mm, max(0, x0 - mm):x1 + mm]))
                    antigo, novo = norm(m["texto_antigo"]), norm(m["texto_novo"])
                    if antigo not in controle:
                        R["sem_controle"] += 1
                    else:
                        some = (not novo and antigo not in lido) or (novo and (novo in lido or lido_igual(lido, novo)
                                                                              or lido_igual(lido[-len(novo) - 1:], novo))
                                                                     and (antigo in novo or antigo not in lido))
                        if some:
                            R["ocr_ok"] += 1
                        else:
                            R["ocr_divergente"].append(f"{it.nome}/{f['categoria']}: esperado {m['texto_novo']!r}, OCR leu {lido!r} "
                                                       f"({m['descricao'][:120]})")
                # folha de contato
                x0, y0, x1, y1 = regiao
                hh = f["caixa"][3] - f["caixa"][1]
                cx0, cy0 = max(0, x0 - 2 * hh), max(0, y0 - hh)
                cx1, cy1 = min(img.shape[1], x1 + 2 * hh), min(img.shape[0], y1 + hh)
                a, b = base[cy0:cy1, cx0:cx1], img[cy0:cy1, cx0:cx1]
                esc = min(1.0, 900 / a.shape[1], 120 / a.shape[0])
                a = cv2.resize(a, (max(1, int(a.shape[1] * esc)), max(1, int(a.shape[0] * esc))), interpolation=cv2.INTER_AREA)
                b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
                cab = np.full((56, a.shape[1], 3), 255, np.uint8)
                cab = rotular(cab, [f"{it.nome} · {f['categoria']} · {m.get('opcoes')}",
                                    f"{m['texto_antigo']!r} -> {m['texto_novo']!r}"[:110], f"OCR: {lido}"[:110]])
                folhas[t.id].append(np.vstack([cab, a, np.full((3, a.shape[1], 3), 200, np.uint8), b,
                                                np.full((10, a.shape[1], 3), 90, np.uint8)]))
            r = relatorio[t.id]
            print(f"   {t.id:<30} aplicadas {r['aplicadas']:>3}  recusas {len(r['recusas']):>3}  OCR ok {r['ocr_ok']:>3}  "
                  f"divergente {len(r['ocr_divergente']):>2}  exceções {len(r['excecoes'])}", flush=True)

for tid, tiras in folhas.items():
    if not tiras:
        continue
    larg = max(t.shape[1] for t in tiras)
    tiras = [np.pad(t, ((0, 0), (0, larg - t.shape[1]), (0, 0)), constant_values=255) for t in tiras]
    for parte in range(0, len(tiras), 14):
        Image.fromarray(np.vstack(tiras[parte:parte + 14])).save(FOLHAS / f"{tid.replace('.', '_')}_{parte // 14}.png")
for r in relatorio.values():
    r["tempo_medio"] = round(float(np.mean(r["tempo"])), 2) if r["tempo"] else None
    del r["tempo"]
    r["nota_mediana"] = round(float(np.median(r["nota"])), 3) if r["nota"] else None
    r["nota_p90"] = round(float(np.percentile(r["nota"], 90)), 3) if r["nota"] else None
    r["tom_mediano"] = round(float(np.median(r["tom"])), 1) if r["tom"] else None
    r["tom_pior"] = round(float(max(r["tom"], key=abs)), 1) if r["tom"] else None
    r["tom_casos"] = sorted(r.get("tom_casos", []), key=lambda x: -abs(x[0]))[:8]
(SAIDA / ("relatorio_ia.json" if COM_IA else "relatorio.json")).write_text(json.dumps(relatorio, ensure_ascii=False, indent=1),
                                                                          encoding="utf-8")
print(f"\nterminou em {round(time.time() - t_ini)} s")
print(f"{'técnica':<30} {'aplic':>5} {'recus':>5} {'OCR ok':>6} {'diverg':>6} {'s/ctrl':>6} {'cód ok':>6} {'exc':>3} {'fora':>4} {'rót':>3}"
      f"  t(s)  nota(med/p90)  tom(med/pior)")
for tid, r in relatorio.items():
    print(f"{tid:<30} {r['aplicadas']:>5} {len(r['recusas']):>5} {r['ocr_ok']:>6} {len(r['ocr_divergente']):>6} {r['sem_controle']:>6} "
          f"{r['codigo_ok']:>6} {len(r['excecoes']):>3} {len(r['fora_do_campo']):>4} {len(r['rotulo_ruim']):>3}  {r['tempo_medio']}"
          f"  {r['nota_mediana']}/{r['nota_p90']}  {r['tom_mediano']}/{r['tom_pior']}")
