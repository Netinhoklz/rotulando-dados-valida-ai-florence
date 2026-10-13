"""Protocolo de avaliação de um detector sobre o dataset gerado (DocForge-Bench 2026 + o protocolo do datasheet).

Entrada: uma pasta de predições com um mapa de probabilidade por versão, ``PRED/<nome>.png`` (8 bits: 0 =
íntegro, 255 = adulterado; ou ``<nome>.npy`` float 0..1) do tamanho da editada (outro tamanho é reamostrado,
bilinear) e, para a métrica por imagem, o mapa de cada original em ``PRED/originais/<arquivo da original sem
extensão>.png``.

Métricas (por imagem e depois a média; a faixa de ``mascaras_ignorar`` fica fora de tudo):
  - por pixel: AUC, F1 e IoU no limiar 0,5, F1 no melhor limiar de cada imagem (oráculo: o teto) e F1 com UM
    limiar calibrado em N versões sorteadas e aplicado nas outras (DocForge-Bench: com 0,3–4% de pixel
    adulterado o 0,5 falha; 10 imagens recuperam boa parte do teto);
  - por imagem: AUC entre editadas e originais e TPR com FPR de 1% e 5% (pontuação da imagem = média do 0,5%
    de pixels mais altos do mapa);
  - intervalo de confiança de 95% por bootstrap sobre DOCUMENTOS; recortes por família, técnica, nível, perícia,
    canal, captura, falsificador, pista semântica, faixa de área alterada e fornecedor ("papel=valor", ex.:
    fonte=Arial; com ``dados.listar(..., excluir_fornecedor=...)`` no treino, mede o gerador deixado de fora).
Imagens com mais de ``max_pixels`` (2 MP) são reduzidas junto com a máscara (interpolação por área).

    python -m rotulador.treino.avaliar SAIDA SPLIT PRED [--calibrar 10] [--semente 0] [--json relatorio.json]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from . import dados

FAIXAS = 256  # histograma de pontuação por imagem (positivos e negativos), como o DocForge-Bench guarda
LIMIAR_PADRAO = 0.5
FRACAO_TOPO = 0.005  # pontuação da imagem: média do 0,5% de pixels mais altos (mínimo 16 px)
GRUPOS = ("familias", "tecnicas", "niveis", "pericia", "canal", "captura_antes", "falsificador", "sem_pista_semantica",
          "area_alterada", "fornecedores")
LISTAS = ("familias", "tecnicas", "niveis", "fornecedores")  # grupos em que a versão entra em cada valor da lista
MEDIDAS = ("auc", "f1_05", "iou_05", "f1_oraculo", "f1_calibrado")


def ler_predicao(pasta: Path, nome: str, forma: tuple[int, int]) -> np.ndarray | None:
    """Mapa float32 0..1 do tamanho ``forma`` (H, W) de ``pasta/<nome>.png`` ou ``.npy``; None se não existe."""
    png, npy = Path(pasta) / f"{nome}.png", Path(pasta) / f"{nome}.npy"
    if npy.is_file():
        p = np.load(npy).astype(np.float32)
    elif png.is_file():
        p = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
        if p is None:
            return None
        if p.ndim == 3:
            p = p[..., 0]
        p = p.astype(np.float32) / (65535.0 if p.dtype == np.uint16 else 255.0)
    else:
        return None
    if p.shape[:2] != tuple(forma):
        p = cv2.resize(p, (forma[1], forma[0]), interpolation=cv2.INTER_LINEAR)
    return np.clip(p, 0.0, 1.0)


def reduzir(prob: np.ndarray, gt: np.ndarray, ignorar: np.ndarray, max_pixels: float):
    """Reduz os três juntos (área) até ``max_pixels``; a máscara fica positiva com cobertura ≥ 0,5 e a faixa de
    ignorar, com qualquer cobertura."""
    H, W = gt.shape
    if H * W <= max_pixels:
        return prob, gt, ignorar
    e = float(np.sqrt(max_pixels / (H * W)))
    tam = (max(1, round(W * e)), max(1, round(H * e)))
    return (cv2.resize(prob, tam, interpolation=cv2.INTER_AREA),
            cv2.resize(gt.astype(np.float32), tam, interpolation=cv2.INTER_AREA) >= 0.5,
            cv2.resize(ignorar.astype(np.float32), tam, interpolation=cv2.INTER_AREA) > 0)


def histogramas(prob: np.ndarray, gt: np.ndarray, ignorar: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Contagens de pixels por faixa de pontuação (FAIXAS) nos positivos e nos negativos, fora da faixa de ignorar."""
    v = np.clip(prob * (FAIXAS - 1) + 0.5, 0, FAIXAS - 1).astype(np.int32)
    valido = ~ignorar
    pos = np.bincount(v[valido & gt], minlength=FAIXAS).astype(np.int64)
    neg = np.bincount(v[valido & ~gt], minlength=FAIXAS).astype(np.int64)
    return pos, neg


def _faixa(limiar: float) -> int:
    return int(round(float(limiar) * (FAIXAS - 1)))


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Área sob a ROC a partir dos histogramas (empates dentro de uma faixa contam meio, como no Mann-Whitney)."""
    P, N = int(pos.sum()), int(neg.sum())
    if not P or not N:
        return float("nan")
    tpr = np.concatenate([[0.0], np.cumsum(pos[::-1]) / P])
    fpr = np.concatenate([[0.0], np.cumsum(neg[::-1]) / N])
    return float(np.sum((fpr[1:] - fpr[:-1]) * (tpr[1:] + tpr[:-1]) / 2))


def _f1_por_faixa(pos: np.ndarray, neg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """F1 e IoU para cada limiar de faixa b (predito positivo = pontuação ≥ b), b = 0..FAIXAS-1."""
    tp = np.cumsum(pos[::-1])[::-1].astype(np.float64)  # positivos com pontuação ≥ b
    fp = np.cumsum(neg[::-1])[::-1].astype(np.float64)
    fn = float(pos.sum()) - tp
    with np.errstate(invalid="ignore", divide="ignore"):
        f1 = np.where(2 * tp + fp + fn > 0, 2 * tp / (2 * tp + fp + fn), np.nan)
        iou = np.where(tp + fp + fn > 0, tp / (tp + fp + fn), np.nan)
    return f1, iou


def metricas_pixel(pos: np.ndarray, neg: np.ndarray, limiar: float = LIMIAR_PADRAO) -> dict:
    """AUC, F1/IoU no ``limiar``, F1 no melhor limiar da imagem (oráculo) e esse limiar. Sem positivo: tudo NaN
    (a imagem fica fora da média, como no DocForge-Bench)."""
    if not pos.sum():
        return {"auc": float("nan"), "f1_05": float("nan"), "iou_05": float("nan"), "f1_oraculo": float("nan"),
                "limiar_oraculo": float("nan")}
    f1, iou = _f1_por_faixa(pos, neg)
    b = _faixa(limiar)
    melhor = int(np.nanargmax(f1[1:])) + 1  # faixa 0 (tudo positivo) não conta
    return {"auc": auc(pos, neg), "f1_05": float(f1[b]), "iou_05": float(iou[b]), "f1_oraculo": float(f1[melhor]),
            "limiar_oraculo": melhor / (FAIXAS - 1)}


def calibrar(hists: list[tuple[np.ndarray, np.ndarray]]) -> float:
    """UM limiar para todas: o que maximiza a média do F1 por imagem nas imagens de calibração (as de histograma
    com positivos); num platô de máximos, o do meio (mais folga dos dois lados). Devolve o limiar (0..1)."""
    soma = np.zeros(FAIXAS)
    n = 0
    for pos, neg in hists:
        if pos.sum():
            soma += np.nan_to_num(_f1_por_faixa(pos, neg)[0])
            n += 1
    if not n:
        return LIMIAR_PADRAO
    soma[0] = -1.0  # faixa 0 (tudo positivo) não conta
    plato = np.flatnonzero(soma >= soma.max() - 1e-9)
    return int(plato[len(plato) // 2]) / (FAIXAS - 1)


def f1_no_limiar(pos: np.ndarray, neg: np.ndarray, limiar: float) -> float:
    return float(_f1_por_faixa(pos, neg)[0][_faixa(limiar)]) if pos.sum() else float("nan")


def pontuacao(prob: np.ndarray, ignorar: np.ndarray | None = None) -> float:
    """Pontuação da imagem inteira: média do 0,5% de pixels mais altos fora da faixa de ignorar (mínimo 16 px)."""
    v = prob[~ignorar] if ignorar is not None else prob.ravel()
    v = np.sort(v.ravel())
    if not v.size:
        return float("nan")
    k = max(16, int(round(v.size * FRACAO_TOPO)))
    return float(v[-k:].mean())


def metricas_imagem(pos: list[float], neg: list[float]) -> dict | None:
    """AUC entre editadas (``pos``) e originais (``neg``) e TPR com FPR de 1% e 5% (limiar no quantil das originais)."""
    pos, neg = [p for p in pos if np.isfinite(p)], [n for n in neg if np.isfinite(n)]
    if not pos or not neg:
        return None
    p, n = np.asarray(pos), np.asarray(neg)
    todos = np.concatenate([p, n])
    ordem = todos.argsort(kind="mergesort")
    postos = np.empty(todos.size)
    postos[ordem] = np.arange(1, todos.size + 1)
    # empates: posto médio
    _, inv, cont = np.unique(todos, return_inverse=True, return_counts=True)
    soma = np.bincount(inv, weights=postos)
    postos = (soma / cont)[inv]
    a = (postos[:p.size].sum() - p.size * (p.size + 1) / 2) / (p.size * n.size)
    out = {"n_editadas": int(p.size), "n_originais": int(n.size), "auc": float(a)}
    for fpr in (0.01, 0.05):
        lim = float(np.quantile(n, 1 - fpr, method="higher"))
        out[f"tpr_fpr{int(fpr * 100):02d}"] = float((p > lim).mean())
    return out


def bootstrap(valores: list[tuple[str, float]], n: int = 1000, semente: int = 0) -> dict:
    """Média e IC 95% por bootstrap sobre documentos (``valores``: [(doc, valor)]; NaN fica de fora)."""
    por_doc = defaultdict(list)
    for doc, v in valores:
        if v is not None and np.isfinite(v):
            por_doc[doc].append(float(v))
    docs = sorted(por_doc)
    if not docs:
        return {"media": None, "ic95": None, "n": 0}
    todos = [v for d in docs for v in por_doc[d]]
    rng = np.random.default_rng(semente)
    medias = []
    for _ in range(n):
        esc = rng.integers(0, len(docs), len(docs))
        vs = [v for i in esc for v in por_doc[docs[i]]]
        medias.append(float(np.mean(vs)))
    return {"media": round(float(np.mean(todos)), 4), "ic95": [round(float(np.percentile(medias, 2.5)), 4),
                                                           round(float(np.percentile(medias, 97.5)), 4)], "n": len(todos)}


def _faixa_area(fracao: float) -> str:
    return "<0,5%" if fracao < 0.005 else ("0,5–2%" if fracao < 0.02 else ">2%")


def avaliar(raiz_saida: Path, split: str, pasta_pred: Path, calibrar_n: int = 10, semente: int = 0,
            max_pixels: float = 2e6, limiar: float = LIMIAR_PADRAO) -> dict:
    """Roda o protocolo inteiro sobre as predições de ``pasta_pred`` para o ``split``; ver o módulo."""
    pasta_pred = Path(pasta_pred)
    amostras = dados.listar(raiz_saida, split)
    rng = random.Random(semente)
    linhas, hists, faltam = [], {}, []
    pont_orig: dict[str, float] = {}
    for a in amostras:
        arr = dados.carregar(a)
        H, W = arr["mascara"].shape
        prob = ler_predicao(pasta_pred, a["nome"], (H, W))
        if prob is None:
            faltam.append(a["nome"])
            continue
        prob, gt, ign = reduzir(prob, arr["mascara"], arr["ignorar"], max_pixels)
        pos, neg = histogramas(prob, gt, ign)
        hists[a["nome"]] = (pos, neg)
        m = metricas_pixel(pos, neg, limiar)
        linhas.append({"nome": a["nome"], "doc": a["doc"], **m, "pontuacao": pontuacao(prob, ign), "amostra": a})
        orig = Path(a["arquivos"]["original"]).stem
        if orig not in pont_orig:
            po = ler_predicao(pasta_pred / "originais", orig, (H, W))
            if po is not None:
                pont_orig[orig] = pontuacao(po)
    # calibração: N versões sorteadas dão o limiar, as outras recebem a nota
    nomes = [l["nome"] for l in linhas]
    n_cal = min(max(0, int(calibrar_n)), len(nomes))
    escolhidas = set(rng.sample(nomes, n_cal)) if n_cal else set()
    lim_cal = calibrar([hists[n] for n in escolhidas]) if escolhidas else limiar
    for l in linhas:
        l["f1_calibrado"] = f1_no_limiar(*hists[l["nome"]], lim_cal) if l["nome"] not in escolhidas or n_cal == len(nomes) else float("nan")

    def media(ls, k):
        v = [l[k] for l in ls if np.isfinite(l[k])]
        return round(float(np.mean(v)), 4) if v else None

    por = {}
    for g in GRUPOS:
        grupos = defaultdict(list)
        for l in linhas:
            a = l["amostra"]
            chaves = a.get(g) if g in LISTAS else [_faixa_area(a["fracao_alterada"]) if g == "area_alterada" else a[g]]
            for ch in chaves or ["(nenhum)"]:
                grupos[str(ch) if ch not in (None, "") else "(nenhum)"].append(l)
        por[g] = {ch: {"n": len(ls), **{k: media(ls, k) for k in MEDIDAS}} for ch, ls in sorted(grupos.items())}
    return {"split": split, "predicoes": str(pasta_pred), "versoes": len(linhas), "faltam": faltam,
            "taxa_base": round(float(np.mean([l["amostra"]["fracao_alterada"] for l in linhas])), 6) if linhas else None,
            "limiar": limiar, "limiar_calibrado": round(lim_cal, 4), "calibradas_em": n_cal, "max_pixels": max_pixels,
            "pixel": {k: bootstrap([(l["doc"], l[k]) for l in linhas], semente=semente) for k in MEDIDAS},
            "imagem": metricas_imagem([l["pontuacao"] for l in linhas], list(pont_orig.values())),
            "por": por,
            "por_versao": [{k: (round(l[k], 4) if isinstance(l[k], float) and np.isfinite(l[k]) else
                                (None if isinstance(l[k], float) else l[k]))
                            for k in ("nome", "doc", *MEDIDAS, "limiar_oraculo", "pontuacao")} for l in linhas]}


def _fmt(v) -> str:
    return "–" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.3f}"


def markdown(d: dict) -> str:
    L = [f"# Avaliação — split {d['split']}", "",
         f"Predições: `{d['predicoes']}` — {d['versoes']} versões avaliadas" + (f", {len(d['faltam'])} sem predição" if d["faltam"] else "") + ".",
         f"Taxa-base de pixel adulterado: {_fmt(d['taxa_base'])}; limiar fixo {d['limiar']}; limiar calibrado em "
         f"{d['calibradas_em']} versões: {_fmt(d['limiar_calibrado'])}.", "",
         "## Por pixel (média por imagem, IC 95% por bootstrap sobre documentos)", "",
         "| métrica | média | IC 95% | n |", "|---|---:|---|---:|"]
    nomes = {"auc": "AUC", "f1_05": "F1 @ limiar fixo", "iou_05": "IoU @ limiar fixo", "f1_oraculo": "F1 oráculo (melhor limiar por imagem)",
             "f1_calibrado": "F1 @ limiar calibrado"}
    for k in MEDIDAS:
        r = d["pixel"][k]
        ic = f"{_fmt(r['ic95'][0])} – {_fmt(r['ic95'][1])}" if r.get("ic95") else "–"
        L.append(f"| {nomes[k]} | {_fmt(r['media'])} | {ic} | {r['n']} |")
    L += ["", "## Por imagem (editadas × originais)", ""]
    im = d.get("imagem")
    if im:
        L += [f"- {im['n_editadas']} editadas × {im['n_originais']} originais: AUC {_fmt(im['auc'])}, TPR@FPR1% {_fmt(im['tpr_fpr01'])}, "
              f"TPR@FPR5% {_fmt(im['tpr_fpr05'])}."]
    else:
        L += ["- sem predições das originais (`PRED/originais/<original>.png`): métrica por imagem não calculada."]
    for g in GRUPOS:
        if not d["por"].get(g):
            continue
        L += ["", f"## Por {g.replace('_', ' ')}", "", "| grupo | n | AUC | F1 fixo | IoU fixo | F1 oráculo | F1 calibrado |",
              "|---|---:|---:|---:|---:|---:|---:|"]
        for ch, r in d["por"][g].items():
            L.append(f"| {ch} | {r['n']} | " + " | ".join(_fmt(r[k]) for k in MEDIDAS) + " |")
    return "\n".join(L) + "\n"


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Avalia predições de um detector sobre um split do dataset gerado.")
    ap.add_argument("saida")
    ap.add_argument("split")
    ap.add_argument("predicoes")
    ap.add_argument("--calibrar", type=int, default=10, help="versões sorteadas para calibrar o limiar (0 = não calibra)")
    ap.add_argument("--semente", type=int, default=0)
    ap.add_argument("--limiar", type=float, default=LIMIAR_PADRAO)
    ap.add_argument("--max-pixels", type=float, default=2e6)
    ap.add_argument("--json", help="grava o relatório completo neste arquivo")
    ap.add_argument("--md", help="grava o relatório em markdown neste arquivo")
    a = ap.parse_args(argv)
    d = avaliar(Path(a.saida), a.split, Path(a.predicoes), a.calibrar, a.semente, a.max_pixels, a.limiar)
    if a.json:
        Path(a.json).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    texto = markdown(d)
    if a.md:
        Path(a.md).write_text(texto, encoding="utf-8")
    print(texto)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(_main())
