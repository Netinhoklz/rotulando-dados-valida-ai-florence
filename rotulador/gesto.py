"""O gesto de quem edita: como a mão move o mouse, e o que isso deixa na imagem.

O movimento em si (velocidade, pausa) não aparece nos pixels; aparece a GEOMETRIA que ele produz: o contorno do laço,
as passadas do pincel e do carimbo (toques espaçados, pressão que sobe e desce, ida e volta), a origem do Alt+clique,
o texto posto com um clique e ajustado nas setas. Aqui isso é gerado como uma pessoa faria, sem tela e sem mouse:

  movimento   de um ponto a outro: perfil de mínimo esforço (Flash & Hogan 1985: velocidade em sino), caminho
              levemente curvo, duração pela lei de Fitts, às vezes passa do alvo e corrige (submovimento), e o tremor
              fisiológico de 8–12 Hz; amostrado a 125 Hz como os eventos do navegador;
  laco        a volta em torno do texto, canto a canto (o contorno não fecha certinho);
  pinceladas  passadas ao longo da linha (às vezes ida e volta), com pressão que sobe no começo e cai no fim;
  toques      o pincel de verdade não pinta uma linha: carimba discos espaçados ao longo do caminho (espaçamento de
              25% do diâmetro, o padrão do Photoshop), cada um com o tamanho da pressão naquele ponto;
  setas       o texto posto com um clique (erro de alguns pixels de tela) e empurrado pelas setas (1 px; 10 px com
              Shift) até parecer alinhado.

Tudo em pixels da IMAGEM: ``escala`` = 1 / zoom da tela (no zoom de 200% um tremor de 0,5 px de tela vale 0,25 px).

Biblioteca de gestos REAIS (``usar_biblioteca``): os laços e pinceladas gravados pelo editor manual da equipe (a
anotação guarda os pontos, e agora os ``tempos`` e o ``zoom``), normalizados pela própria caixa. Com ela, parte das
seleções automáticas usa um traço que uma pessoa fez de verdade, encaixado na caixa do campo.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import cv2
import numpy as np

HZ = 125  # eventos de mouse por segundo (pointermove do navegador)
ZOOMS = ((0.5, 0.08), (0.67, 0.12), (1.0, 0.35), (1.5, 0.15), (2.0, 0.18), (3.0, 0.12))  # zoom em que se trabalha
PROB_REAL = 0.7  # com a biblioteca, a fração das seleções que usa um traço real


def sortear_zoom(rng: random.Random) -> float:
    return rng.choices([z for z, _ in ZOOMS], weights=[p for _, p in ZOOMS])[0]


def _min_jerk(n: int) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    return 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5


def movimento(p0, p1, rng: random.Random, escala: float = 1.0, alvo_px: float = 8.0, t0: float = 0.0) -> np.ndarray:
    """Pontos (x, y, t em ms) de um movimento da mão de ``p0`` a ``p1`` (pixels da imagem). ``alvo_px``: o tamanho do
    alvo em pixels de TELA (lei de Fitts: alvo pequeno e longe, movimento mais lento)."""
    p0, p1 = np.float64(p0), np.float64(p1)
    d = float(np.hypot(*(p1 - p0)))
    if d < 1e-6:
        return np.array([[p0[0], p0[1], t0]])
    tela = d / max(escala, 1e-6)
    dur = 0.08 + 0.11 * math.log2(tela / alvo_px + 1)  # s
    u = (p1 - p0) / d
    perp = np.array([-u[1], u[0]])
    passa = rng.random() < 0.35 and d > 6 * escala
    fim = p1 + (u * d * rng.uniform(0.02, 0.07) + perp * d * rng.gauss(0, 0.015)) if passa else p1
    n = max(3, int(dur * HZ))
    s = _min_jerk(n)[:, None]
    ctrl = (p0 + fim) / 2 + perp * rng.gauss(0, 0.06) * d  # caminho levemente curvo (o braço gira no cotovelo)
    pts = (1 - s) ** 2 * p0 + 2 * (1 - s) * s * ctrl + s ** 2 * fim
    t = t0 + np.linspace(0, dur * 1000, n)
    if passa:  # correção: um submovimento curto de volta ao alvo
        dur2 = rng.uniform(0.12, 0.25)
        m = max(3, int(dur2 * HZ))
        s2 = _min_jerk(m)[1:, None]
        pts = np.vstack([pts, fim + s2 * (p1 - fim)])
        t = np.concatenate([t, t[-1] + np.linspace(0, dur2 * 1000, m)[1:]])
    # tremor fisiológico (8–12 Hz) e ruído do sensor, em pixels de tela convertidos para a imagem
    f, a = rng.uniform(8, 12), rng.uniform(0.2, 0.6) * escala
    fx, fy = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
    ts = t / 1000.0
    pts = pts + np.stack([a * np.sin(2 * math.pi * f * ts + fx), a * np.sin(2 * math.pi * f * ts + fy)], 1)
    nr = np.random.default_rng(rng.randrange(2 ** 31))
    pts = pts + nr.normal(0, 0.15 * escala, pts.shape)
    return np.column_stack([pts, t])


def _encadear(pontos, rng: random.Random, escala: float) -> np.ndarray:
    """Movimentos seguidos pelos ``pontos`` (cada um começa onde o anterior parou, com uma pausa curta)."""
    out, t = [], 0.0
    for a, b in zip(pontos, pontos[1:]):
        m = movimento(a, b, rng, escala, t0=t)
        out.append(m if not out else m[1:])
        t = float(m[-1, 2]) + rng.uniform(15, 60)
    return np.vstack(out)


def laco(x0: float, y0: float, x1: float, y1: float, rng: random.Random, escala: float = 1.0) -> np.ndarray:
    """O contorno à mão em volta da caixa (já com a folga): parte de um canto, dá a volta (horário ou anti-horário),
    cortando um pouco os cantos, e volta perto do começo. Pontos (x, y, t)."""
    h = max(2.0, y1 - y0)
    j = 0.05 * h + 1.0 * escala

    def canto(x, y):
        return (x + rng.gauss(0, j), y + rng.gauss(0, j))
    cantos = [canto(x0, y0), canto(x1, y0), canto(x1, y1), canto(x0, y1)]
    k = rng.randrange(4)
    cantos = cantos[k:] + cantos[:k]
    if rng.random() < 0.5:
        cantos = [cantos[0]] + cantos[1:][::-1]
    pontos = []
    for a, b in zip(cantos, cantos[1:] + cantos[:1]):
        pontos.append(a)
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 6 * h:  # lado comprido: a mão para no meio
            m = rng.uniform(0.35, 0.65)
            pontos.append((a[0] + m * (b[0] - a[0]) + rng.gauss(0, j), a[1] + m * (b[1] - a[1]) + rng.gauss(0, j)))
    pontos.append((cantos[0][0] + rng.gauss(0, j), cantos[0][1] + rng.gauss(0, j)))  # fecha perto, não no ponto
    return _encadear(pontos, rng, escala)


def pinceladas(x0: float, y0: float, x1: float, y1: float, raio: float, rng: random.Random,
               escala: float = 1.0) -> list[np.ndarray]:
    """Passadas do pincel ao longo da linha da caixa: uma (60%), duas (30%) ou ida e volta (10%), cada uma começando
    um pouco antes e terminando um pouco depois, com a altura variando. Lista de (x, y, t)."""
    cy, h = (y0 + y1) / 2, max(2.0, y1 - y0)
    n = rng.choices((1, 2, 3), weights=(0.6, 0.3, 0.1))[0]
    out = []
    for i in range(n):
        dy = rng.gauss(0, 0.12 * h) if n > 1 else rng.gauss(0, 0.06 * h)
        a = (x0 - rng.uniform(0, raio), cy + dy)
        b = (x1 + rng.uniform(0, raio), cy + dy + rng.gauss(0, 0.08 * h))
        if n == 3 and i % 2 == 1:  # ida e volta
            a, b = b, a
        meio = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + rng.gauss(0, 0.1 * h))  # a passada ondula
        out.append(_encadear([a, meio, b], rng, escala))
    return out


def pressao(n: int, rng: random.Random) -> np.ndarray:
    """Pressão (0..1) ao longo de uma passada: sobe nos primeiros ~12%, oscila, cai nos últimos ~15%."""
    s = np.linspace(0, 1, max(2, n))
    sobe = np.clip(s / max(1e-3, rng.uniform(0.08, 0.16)), 0, 1)
    cai = np.clip((1 - s) / max(1e-3, rng.uniform(0.1, 0.2)), 0, 1)
    osc = 1 + 0.05 * np.sin(2 * math.pi * rng.uniform(1, 3) * s + rng.uniform(0, 6))
    return np.clip((0.55 + 0.45 * np.minimum(sobe, cai)) * osc, 0.3, 1.0)


def toques(traco: np.ndarray, raio: float, rng: random.Random, espacamento: float = 0.25,
           com_pressao: bool = True) -> np.ndarray:
    """Os discos que o pincel carimba ao longo do ``traco``: (x, y, raio efetivo), a cada ``espacamento`` × diâmetro
    do caminho; com pressão, o raio vai de ~0,8 a 1 do nominal."""
    xy = traco[:, :2]
    seg = np.hypot(*np.diff(xy, axis=0).T) if len(xy) > 1 else np.zeros(0)
    acum = np.concatenate([[0.0], np.cumsum(seg)])
    passo = max(1.0, espacamento * 2 * raio)
    s = np.arange(0, acum[-1] + 1e-6, passo) if acum[-1] > 0 else np.zeros(1)
    x, y = np.interp(s, acum, xy[:, 0]), np.interp(s, acum, xy[:, 1])
    p = pressao(len(s), rng) if com_pressao else np.ones(len(s))
    return np.column_stack([x, y, raio * (0.8 + 0.2 * p)])


def alfa_toques(forma: tuple[int, int], tq: np.ndarray, dureza: float = 0.8) -> np.ndarray:
    """Cobertura (0..1) dos toques numa imagem ``forma`` (H, W): cada disco é 1 até dureza×raio e cai linear até o
    raio (a borda macia do pincel); toques sobrepostos somam como tinta (1 − Π(1 − a))."""
    H, W = forma
    falta = np.ones((H, W), np.float32)
    for x, y, r in tq:
        r = max(0.75, float(r))
        a0, a1 = max(0, int(x - r - 1)), min(W, int(x + r + 2))
        b0, b1 = max(0, int(y - r - 1)), min(H, int(y + r + 2))
        if a0 >= a1 or b0 >= b1:
            continue
        yy, xx = np.mgrid[b0:b1, a0:a1].astype(np.float32)
        d = np.hypot(xx - x, yy - y)
        a = np.clip((r - d) / max(0.5, r * (1 - dureza)), 0, 1)
        falta[b0:b1, a0:a1] *= 1 - a
    return 1 - falta


def setas(erro_px: float, rng: random.Random, escala: float = 1.0, capricho: float = 1.0) -> tuple[int, int]:
    """O texto posto com um clique ``erro_px`` (pixels da imagem) fora do lugar e empurrado pelas setas (1 px; 10 px
    com Shift quando está longe) até a pessoa achar que alinhou: (erro que sobra, toques de seta). A tolerância
    depende do zoom (no 200% ela vê meio pixel) e do capricho."""
    e = int(round(erro_px))
    tol = int(round(max(0.0, rng.uniform(0.3, 1.2) * capricho * escala)))
    n = 0
    while abs(e) > tol and n < 40:
        passo = 10 if abs(e) >= 10 and rng.random() < 0.8 else 1
        e -= passo if e > 0 else -passo
        n += 1
    return e, n


# ------------------------------------------------------------------------------------------- gestos reais
_BIB: dict = {"laco": [], "pincel": []}


def _normalizar(pts, tempos=None, tamanho=None) -> dict | None:
    a = np.asarray(pts, np.float64)
    if a.ndim != 2 or len(a) < 8:
        return None
    x0, y0 = a.min(0)
    w, h = np.ptp(a[:, 0]), np.ptp(a[:, 1])
    if w < 4 or h < 2:
        return None
    g = {"u": np.round((a[:, 0] - x0) / w, 4).tolist(), "v": np.round((a[:, 1] - y0) / h, 4).tolist(),
         "w": round(float(w), 1), "h": round(float(h), 1)}
    if tempos and len(tempos) == len(a):
        g["t"] = [int(t) for t in tempos]
    if tamanho:
        g["raio_rel"] = round(float(tamanho) / 2 / max(h, 1.0), 3)  # o raio do pincel pela altura da passada
    return g


def extrair(raiz_saida: Path) -> dict:
    """Os laços e pinceladas das edições MANUAIS salvas (anotação sem ``gerador``): ``{"laco": [...], "pincel":
    [...]}``, cada um normalizado pela própria caixa (u, v em 0..1), com os tempos quando o editor os gravou."""
    bib = {"laco": [], "pincel": []}
    for arq in sorted(Path(raiz_saida).glob("*/anotacoes/*.json")):
        try:
            a = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if a.get("gerador"):
            continue
        for op in a.get("operacoes") or []:
            p = op.get("params") or {}
            formas = ((p.get("selecao") or {}).get("formas") or []) if isinstance(p.get("selecao"), dict) else []
            for f in formas:
                if f.get("tipo") == "poligono":
                    g = _normalizar(f.get("pontos"), f.get("tempos"))
                    if g:
                        bib["laco"].append(g)
                elif f.get("tipo") == "traco":
                    g = _normalizar(f.get("pontos"), f.get("tempos"), f.get("tamanho"))
                    if g:
                        bib["pincel"].append(g)
            if op.get("tipo") in ("pincel", "borracha", "carimbo", "recuperacao", "corretivo") and p.get("pontos"):
                g = _normalizar(p.get("pontos"), p.get("tempos"), p.get("tamanho"))
                if g:
                    bib["pincel"].append(g)
    return bib


def usar_biblioteca(raiz_saida: Path | None) -> dict:
    """Carrega (refaz) a biblioteca de gestos reais a partir das anotações manuais de ``raiz_saida``; None esvazia.
    Grava uma cópia em ``raiz_saida/_cache/gestos_reais.json``. Devolve as contagens."""
    global _BIB
    if raiz_saida is None:
        _BIB = {"laco": [], "pincel": []}
        return {"laco": 0, "pincel": 0}
    _BIB = extrair(raiz_saida)
    try:
        c = Path(raiz_saida) / "_cache"
        c.mkdir(parents=True, exist_ok=True)
        (c / "gestos_reais.json").write_text(json.dumps(_BIB), encoding="utf-8")
    except OSError:
        pass
    return {k: len(v) for k, v in _BIB.items()}


def real(tipo: str, rng: random.Random) -> dict | None:
    """Um gesto real do ``tipo`` ("laco" ou "pincel"), sorteado, ou None (biblioteca vazia ou o sorteio disse não)."""
    lista = _BIB.get(tipo) or []
    if not lista or rng.random() >= PROB_REAL:
        return None
    return rng.choice(lista)


def encaixar(g: dict, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """Os pontos do gesto real esticados para a caixa (x0, y0, x1, y1)."""
    u, v = np.asarray(g["u"]), np.asarray(g["v"])
    return np.column_stack([x0 + u * (x1 - x0), y0 + v * (y1 - y0)])


def rasterizar_laco(forma: tuple[int, int], pts: np.ndarray) -> np.ndarray:
    m = np.zeros(forma, np.uint8)
    cv2.fillPoly(m, [np.round(pts[:, :2]).astype(np.int32)], 1)
    return m.astype(bool)
