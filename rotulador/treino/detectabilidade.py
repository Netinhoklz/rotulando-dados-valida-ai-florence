"""Detectabilidade por técnica: quanto um detector APRENDIDO, simples e barato, separa o trecho editado do mesmo
trecho na original. Mede o que o dataset entrega a um modelo: técnica com AUC perto de 1 aqui deixa um vestígio
fácil (é a primeira a corrigir); perto de 0,5, o vestígio não aparece nestas medidas (o que não quer dizer que um
modelo grande não ache).

    python -m rotulador.treino.detectabilidade SAIDA [--split todos|treino|teste|validacao] [--por-versao 40]

O par é o segredo da medida: o recorte positivo é da EDITADA dentro da região da edição; o negativo é o MESMO
recorte (mesma posição) da ORIGINAL. O conteúdo é o mesmo nos dois (a não ser pelo texto novo), então o detector só
separa pelo vestígio. Medidas por recorte (32 px): ruído (resíduo passa-alta: desvio, curtose e o do papel),
nitidez (Laplaciano), blocos do JPEG, energia de alta frequência, quantidade e peso da tinta, cor do ruído
(correlação entre canais) e tom. O classificador é uma regressão logística (numpy, sem dependência) com validação
cruzada POR DOCUMENTO (o detector nunca vê o papel que vai julgar). Grava SAIDA/relatorio_detectabilidade.json e .md.
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

LADO = 32
MEDIDAS = ("ruido", "curtose", "ruido_papel", "nitidez", "blocos", "alta_freq", "tinta", "peso", "cor_ruido", "tom")
MIN_RECORTES = 12  # por técnica, para a AUC dela sair no relatório


def medidas(rec: np.ndarray) -> np.ndarray:
    """O vetor de ``MEDIDAS`` de um recorte RGB uint8 (LADO x LADO)."""
    f = rec.astype(np.float32)
    g = f @ np.float32([0.299, 0.587, 0.114])
    res = g - cv2.GaussianBlur(g, (0, 0), 1.0)
    sd = float(res.std()) + 1e-6
    curt = float(((res - res.mean()) ** 4).mean() / sd ** 4)
    papel = g > np.percentile(g, 60)
    rp = float(res[papel].std()) if papel.sum() > 20 else sd
    lap = float(cv2.Laplacian(g, cv2.CV_32F).var())
    dx = np.abs(np.diff(g, axis=1))
    borda8 = dx[:, 7::8].mean() if dx[:, 7::8].size else 0.0
    blocos = float(borda8 / (dx.mean() + 1e-6))
    F = np.abs(np.fft.fft2(g - g.mean())) ** 2
    yy, xx = np.meshgrid(np.fft.fftfreq(g.shape[0]), np.fft.fftfreq(g.shape[1]), indexing="ij")
    alta = float(F[np.hypot(yy, xx) > 0.25].sum() / (F.sum() + 1e-6))
    tinta = g < np.percentile(g, 95) - 60
    gy, gx = np.gradient(g / 255.0)
    borda = float(np.hypot(gx, gy)[tinta].sum()) if tinta.any() else 0.0
    peso = 2 * float(tinta.sum()) / borda if borda > 1e-6 else 0.0
    rc = [c - cv2.GaussianBlur(c, (0, 0), 1.0) for c in (f[..., 0], f[..., 1])]
    cor = float(np.corrcoef(rc[0].ravel(), rc[1].ravel())[0, 1]) if rc[0].std() > 1e-3 and rc[1].std() > 1e-3 else 1.0
    return np.array([sd, min(curt, 50.0), rp, np.log1p(lap), blocos, alta, float(tinta.mean()), peso, cor,
                     float(g.mean()) / 255], np.float32)


def recortes(amostras: list[dict], por_versao: int = 40, semente: int = 0) -> tuple[np.ndarray, np.ndarray, list, list]:
    """(X, y, documento de cada linha, técnica de cada linha). Para cada versão, até ``por_versao`` pares: o recorte
    da editada centrado num pixel da edição (y = 1) e o mesmo recorte da original (y = 0)."""
    rng = random.Random(semente)
    X, y, docs, tecs = [], [], [], []
    for a in amostras:
        try:
            arr = dados.carregar(a)
            anot = json.loads(Path(a["arquivos"]["anotacao"]).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - versão ilegível fica fora da medida
            continue
        nome_de = {int(v): k for k, v in (anot.get("ids_tecnica") or {}).items()}
        orig, ed, reg, tec = arr["original"], arr["editada"], arr["regiao"], arr["tecnicas"]
        H, W = reg.shape
        ys, xs = np.nonzero(reg)
        if not len(ys):
            continue
        idx = rng.sample(range(len(ys)), min(len(ys), por_versao * 4))
        feitos = 0
        for i in idx:
            cy, cx = int(ys[i]), int(xs[i])
            y0, x0 = min(max(0, cy - LADO // 2), H - LADO), min(max(0, cx - LADO // 2), W - LADO)
            if y0 < 0 or x0 < 0:
                continue
            a_ed, a_or = ed[y0:y0 + LADO, x0:x0 + LADO], orig[y0:y0 + LADO, x0:x0 + LADO]
            if np.array_equal(a_ed, a_or):
                continue
            t = nome_de.get(int(tec[cy, cx]), "?")
            for rec, rot in ((a_ed, 1), (a_or, 0)):
                X.append(medidas(rec))
                y.append(rot)
                docs.append(a["doc"])
                tecs.append(t)
            feitos += 1
            if feitos >= por_versao:
                break
    return (np.array(X, np.float32).reshape(-1, len(MEDIDAS)), np.array(y, np.int8), docs, tecs)


def _treinar(X: np.ndarray, y: np.ndarray, passos: int = 400, reg: float = 1e-2):
    """Regressão logística por descida de gradiente nas medidas padronizadas. Devolve (média, desvio, pesos, viés)."""
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z = (X - mu) / sd
    w, b = np.zeros(Z.shape[1], np.float64), 0.0
    for _ in range(passos):
        p = 1 / (1 + np.exp(-np.clip(Z @ w + b, -40, 40)))
        g = p - y
        w -= 0.1 * (Z.T @ g / len(y) + reg * w)
        b -= 0.1 * float(g.mean())
    return mu, sd, w, b


def _prob(modelo, X: np.ndarray) -> np.ndarray:
    mu, sd, w, b = modelo
    return 1 / (1 + np.exp(-np.clip(((X - mu) / sd) @ w + b, -40, 40)))


def auc(p: np.ndarray, y: np.ndarray) -> float:
    """AUC pelo posto (Mann-Whitney), empates contam meio."""
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    ordem = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    postos = np.empty(len(ordem))
    postos[ordem] = np.arange(1, len(ordem) + 1)
    todos = np.concatenate([pos, neg])
    for v in np.unique(todos):  # empates: posto médio
        m = todos == v
        if m.sum() > 1:
            postos[m] = postos[m].mean()
    return float((postos[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _validar(X: np.ndarray, y: np.ndarray, docs: list, dobras: int, semente: int) -> np.ndarray | None:
    """Probabilidade de cada recorte por um modelo que não viu o documento dele (dobras por documento)."""
    unicos = sorted(set(docs))
    if len(unicos) < 2:
        return None
    rng = random.Random(semente)
    rng.shuffle(unicos)
    dobra_de = {d: i % min(dobras, len(unicos)) for i, d in enumerate(unicos)}
    dob = np.array([dobra_de[d] for d in docs])
    p = np.zeros(len(y))
    for k in sorted(set(dob)):
        treino = dob != k
        if len(set(y[treino])) < 2:
            return None
        p[dob == k] = _prob(_treinar(X[treino], y[treino].astype(np.float64)), X[dob == k])
    return p


def avaliar(raiz_saida: Path, split: str = "todos", por_versao: int = 40, dobras: int = 5, semente: int = 0) -> dict:
    """Validação cruzada por documento. ``geral``: um modelo para todas as técnicas; ``por_tecnica``: um modelo SÓ
    com os pares de cada técnica (o vestígio de uma vai para o lado oposto do de outra: o modelo único aprendia a
    mais fácil e dava AUC abaixo de 0,5 nas outras). ``split="todos"`` junta os três (é diagnóstico, não treino).
    ``{"recortes", "documentos", "geral": auc, "por_tecnica": {tid: {"auc", "n", "documentos", "pesos"}}, "pesos"}``."""
    from ..registro import SPLITS
    splits = list(SPLITS) if split == "todos" else [split]
    amostras = [a for sp in splits for a in dados.listar(raiz_saida, sp)]
    X, y, docs, tecs = recortes(amostras, por_versao, semente)
    out = {"split": split, "recortes": int(len(y)), "documentos": len(set(docs)), "versoes": len(amostras)}
    p = _validar(X, y, docs, dobras, semente) if len(y) >= 40 else None
    if p is None:
        out["aviso"] = "poucos documentos ou recortes para medir (precisa de 2 documentos e 40 recortes)"
        return out
    out["geral"] = round(auc(p, y), 3)
    por = defaultdict(list)
    for i, t in enumerate(tecs):
        por[t].append(i)
    out["por_tecnica"] = {}
    for t, ix in sorted(por.items()):
        ix = np.array(ix)
        dt = [docs[i] for i in ix]
        if len(ix) // 2 < MIN_RECORTES or len(set(dt)) < 2:
            continue
        pt = _validar(X[ix], y[ix], dt, dobras, semente)
        if pt is None:
            continue
        _mu, _sd, w, _b = _treinar(X[ix], y[ix].astype(np.float64))
        out["por_tecnica"][t] = {"auc": round(auc(pt, y[ix]), 3), "n": int(len(ix) // 2), "documentos": len(set(dt)),
                                 "pesos": {k: round(float(v), 3) for k, v in
                                           sorted(zip(MEDIDAS, w), key=lambda kv: -abs(kv[1]))[:3]}}
    _mu, _sd, w, _b = _treinar(X, y.astype(np.float64))
    out["pesos"] = {k: round(float(v), 3) for k, v in sorted(zip(MEDIDAS, w), key=lambda kv: -abs(kv[1]))}
    return out


def markdown(d: dict) -> str:
    L = [f"# Detectabilidade ({d['split']}): {d.get('versoes', 0)} versões, {d['documentos']} documentos, "
         f"{d['recortes']} recortes", ""]
    if "aviso" in d:
        return "\n".join(L + [d["aviso"], ""])
    L += [f"AUC geral (validação por documento): **{d['geral']}** (0,5 = o detector não separa; 1 = separa sempre)", "",
          "| técnica | AUC (modelo só dela) | pares | documentos | o que mais denuncia |", "|---|---|---|---|---|"]
    for t, v in sorted(d["por_tecnica"].items(), key=lambda kv: -kv[1]["auc"]):
        L.append(f"| {t} | {v['auc']} | {v['n']} | {v['documentos']} | "
                 + ", ".join(f"{k} {w:+.2f}" for k, w in v["pesos"].items()) + " |")
    L += ["", "Medidas que mais pesam (positivo = maior na editada): " +
          ", ".join(f"{k} {v:+.2f}" for k, v in list(d["pesos"].items())[:5]), ""]
    return "\n".join(L)


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("saida", type=Path)
    ap.add_argument("--split", default="todos")
    ap.add_argument("--por-versao", type=int, default=40)
    a = ap.parse_args(argv)
    d = avaliar(a.saida, a.split, a.por_versao)
    (a.saida / "relatorio_detectabilidade.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    (a.saida / "relatorio_detectabilidade.md").write_text(markdown(d), encoding="utf-8")
    print(markdown(d))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(_main())
