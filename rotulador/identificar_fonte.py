"""Identificador de fonte: qual das fontes instaladas desenha o texto do documento do jeito mais parecido.

O texto é conhecido (OCR ou digitado), então não é preciso reconhecer letras soltas: desenha-se o
MESMO texto em cada fonte e compara-se com a tinta do scan.

1. triagem: todas as fontes de texto instaladas (centenas; as da pasta ``fontes/`` entram sozinhas).
   Fica de fora quem não tem todas as letras do texto ou precisaria ser esticada/encolhida demais
   (documento não estica fonte); nota = correlação da linha desenhada (levemente desfocada) com a
   intensidade da tinta. Com silhueta binária (IoU) a tinta limiarizada do scan, mais grossa que a
   do desenho, fazia a negrito ganhar e a regular certa nem chegava à comparação fina.
2. comparação fina: as melhores da triagem, desenhadas na caixa da tinta e desfocadas como o scan,
   comparadas com a INTENSIDADE da tinta por correlação normalizada (não depende do contraste nem do
   limiar de binarização, que engrossa ou afina o traço).
3. documento: um documento usa poucas fontes; o voto das linhas longas desempata os campos curtos
   (um "12" quase não diz qual é a fonte).

Fontes que, para ESTE texto, desenham praticamente igual (Arial, Helvetica, Swis721) vão juntas no
resultado como ``parentes``: para imitar o documento, qualquer uma serve.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import glifos, ocr
from .selecao import mascara_tinta
from .sessao import ErroOperacao
from .ops import texto as mod_texto

VERSAO = 3  # muda quando o método muda: invalida o voto guardado em disco (2: faixa central, linha endireitada;
#             3: linhas do OCR novo, com acentos e caixas que cobrem a tinta toda)
FORA = re.compile(r"Italic|Oblique|Wingdings|Webdings|Symbol|Marlett|MT Extra|MDL2|Emoji|Specialty|Bookshelf", re.I)
TOFU = "\U0010FFFD"  # nunca existe: desenha o glifo de "letra faltando" da fonte
TAMANHO_TRIAGEM = 48
FINAIS = 16  # quantas passam da triagem para a comparação fina
LARGURA_MAX = 1.25
SIGMAS = (0.0, 0.5, 0.9, 1.3, 1.8)
PARENTE = 0.97  # correlação entre dois desenhos do mesmo texto acima da qual as fontes são "a mesma"
# semelhança (absoluta) a partir da qual a fonte identificada é a do documento, e não só a mais parecida. Medido com
# ajustar_fonte (TOP10) em linhas de letras: nos documentos do fluxo (variado: Calibri, Courier, Times, Verdana, Tahoma,
# foto de celular), 97% das 98 linhas na fonte certa passam; em linhas limpas e degradadas (desfoque 0,5-0,9, ruído,
# JPEG) das fontes do TOP10, 96%; das de fora (Bahnschrift, Consolas, Trebuchet MS, Candara, Corbel, Cambria, regular e
# negrito, limpas e degradadas), 7% (Candara e Trebuchet MS Bold, que nessa resolução são quase a Segoe UI). Quem fica
# abaixo na fonte certa: letra de 14-16 px em Times/Georgia e texto que o OCR leu diferente da tinta
LIMIAR_MESMA_FONTE = 0.90
DESFOQUE_SEMELHANCA = 0.04  # desfoque extra da semelhança absoluta, em fração da altura da faixa (mínimo 0,6 px)
PESO_DOCUMENTO = 0.03
# desempate entre fontes quase iguais: fica a que de fato aparece em contas brasileiras
PREFERENCIA = {"Arial": 0.02, "Arial Bold": 0.02, "Helvetica": 0.01, "Calibri": 0.01, "Calibri Bold": 0.01,
               "Verdana": 0.005, "Tahoma": 0.005}
# fontes feitas para outras escritas (asiáticas, indianas, africanas...) ou para fórmulas: as letras
# latinas delas são cópias de outras (YaHei UI = Segoe UI, Cambria Math = Cambria) e documento brasileiro
# não as usa; perdem o empate para a original
OUTRA_ESCRITA = re.compile(r"YaHei|JhengHei|MingLiU|SimSun|MS (P|UI )?Gothic|Yu Gothic|Malgun|^DF|Noto Sans (JP|KR|SC|TC)"
                           r"|PhagsPa|Ebrima|Nirmala|Leelawadee|Gadugi|Javanese|Myanmar|Mongolian|Himalaya|Tai Le"
                           r"|New Tai Lue|Yi Baiti|Uighur|Historic|Sylfaen|Arial Unicode|Noto Sans (Arabic|Hebrew)|Math")
PENALIDADE_OUTRA_ESCRITA = 0.03
SIGMA_TRIAGEM = 0.7
# As 10 famílias mais usadas em contas, boletos e documentos (regular e negrito de cada): a técnica "mesma fonte"
# só escolhe entre elas. Comparar com centenas de fontes instaladas achava, às vezes, uma parecida e exótica
# ("Lucida Sans Unicode", "Geometr212") que nenhum emissor usa. Helvetica e Arial são quase o mesmo desenho.
TOP10 = ("Arial", "Helvetica", "Times New Roman", "Calibri", "Verdana", "Tahoma", "Segoe UI", "Courier New",
         "Georgia", "Arial Narrow")


def fontes_top10() -> list[str]:
    """As fontes do TOP10 instaladas, regular e negrito."""
    instaladas = mod_texto.listar_fontes()
    return [n for f in TOP10 for n in (f, f"{f} Bold") if n in instaladas]


@lru_cache(maxsize=1024)
def _f(nome: str, tamanho: float = TAMANHO_TRIAGEM) -> ImageFont.FreeTypeFont:
    caminho, idx = mod_texto.listar_fontes()[nome]
    return ImageFont.truetype(caminho, tamanho, index=idx)


def _desenho(f: ImageFont.FreeTypeFont, conteudo: str, traco: float = 0.0) -> np.ndarray | None:
    """Tinta suave (0..1) do texto, recortada justa. ``traco``: negrito isotrópico (contorno de ``traco`` px em volta
    de cada traço, como o texto.renderizar desenha o negrito)."""
    l, t, r, b = f.getbbox(conteudo, anchor="ls", stroke_width=traco)
    tela = Image.new("L", (int(math.ceil(r - l)) + 4, int(math.ceil(b - t)) + 4), 0)
    ImageDraw.Draw(tela).text((2 - l, 2 - t), conteudo, fill=255, font=f, anchor="ls", stroke_width=traco,
                              stroke_fill=255)
    a = np.asarray(tela, np.float32) / 255.0
    nz = a > 0.5
    if not nz.any():
        return None
    ys, xs = np.flatnonzero(nz.any(1)), np.flatnonzero(nz.any(0))
    return a[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]


def _na_faixa(a: np.ndarray | None, conteudo: str) -> np.ndarray | None:
    """O desenho ``a`` só na faixa central da linha (ocr.faixa_da_linha: do alto das letras à linha de base), na
    largura da tinta dela e sem os componentes soltos (o acento do texto certo): a mesma régua que mede o scan, onde
    acento e cedilha que o OCR não leu ficam de fora."""
    if a is None:
        return None
    m = ocr.so_a_linha(a > 0.5)
    if not m.any():
        return None
    topo, base = ocr.faixa_da_linha(m, conteudo)
    xs = np.flatnonzero(m[topo:base + 1].any(0))
    a = a * (cv2.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)  # como o "perto" do scan
    return a[topo:base + 1, xs[0]:xs[-1] + 1]


@lru_cache(maxsize=None)
def _tofu(nome: str):
    f = _f(nome)
    return f.getlength(TOFU), f.getbbox(TOFU), _desenho(f, TOFU)


@lru_cache(maxsize=None)
def _tem(nome: str, c: str) -> bool:
    """A fonte tem a letra ``c`` (e não desenha o quadradinho de letra faltando)?"""
    f = _f(nome)
    comp, caixa, b = _tofu(nome)
    if f.getlength(c) != comp or f.getbbox(c) != caixa:
        return True
    a = _desenho(f, c)
    return a is None or b is None or a.shape != b.shape or not np.array_equal(a, b)


def candidatas(conteudo: str, fontes=None) -> list[str]:
    """Fontes instaladas que servem para texto e têm todas as letras de ``conteudo`` (só entre ``fontes``, se dada)."""
    letras = sorted({c for c in conteudo if not c.isspace()})
    saida = []
    for nome in (fontes if fontes is not None else mod_texto.listar_fontes()):
        if FORA.search(nome):
            continue
        try:
            if all(_tem(nome, c) for c in letras):
                saida.append(nome)
        except (OSError, ValueError):  # arquivo de fonte corrompido, ou letra que o raqm não molda nessa fonte
            continue
    return saida


def _alfa(nome: str, conteudo: str, w: int, h: int, pad: int, negrito: float = 0.0) -> np.ndarray | None:
    """Tinta suave da faixa central do texto na fonte (tamanho 100; ``negrito`` em px desse tamanho, isotrópico),
    esticada para w x h."""
    a = _na_faixa(_desenho(_f(nome, 100.0), conteudo, negrito / 2), conteudo)
    if a is None:
        return None
    return np.pad(cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA), pad)


def _correlacao(a: np.ndarray, v2: np.ndarray, n2: float, sigmas=SIGMAS) -> tuple[float, float]:
    """Melhor correlação normalizada de ``a`` desfocado (sigmas do scan) com o alvo centrado ``v2``."""
    best = (-1.0, 0.0)
    for sg in sigmas:
        b = cv2.GaussianBlur(a, (0, 0), sg) if sg else a
        v1 = b - b.mean()
        best = max(best, (float((v1 * v2).sum() / math.sqrt(float((v1 * v1).sum()) * n2 + 1e-9)), sg))
    return best


def _massa(d: np.ndarray) -> float:
    """Área de tinta "equivalente" (px) de um mapa de intensidade: a soma da intensidade dividida pela intensidade
    do miolo do traço. O desfoque espalha a tinta mas não muda a soma; o limiar (Otsu) engordava o traço."""
    d = np.asarray(d, np.float32)
    pico = float(np.percentile(d[d > 0], 99)) if (d > 0).any() else 0.0
    return float(d.sum()) / pico if pico > 0 else 0.0


def _peso_pelo_traco(grupos: list[dict], d: np.ndarray, conteudo: str, w: int, h: int, sigma: float = 0.0) -> None:
    """Regular ou negrito da MESMA família: a correlação quase não separa os dois, a quantidade de tinta separa.
    Se a outra variante da família vencedora desenha a mesma área de tinta que o documento, ela sobe para o
    primeiro lugar (com o tamanho e a largura medidos para ela)."""
    if not grupos:
        return
    nome = grupos[0]["fonte"]
    familia = nome[:-5] if nome.endswith(" Bold") else nome
    variantes = {g["fonte"]: g for g in grupos if g["fonte"] in (familia, f"{familia} Bold")}
    if len(variantes) < 2:
        return
    alvo = _massa(d)
    melhor, erro = None, None
    for v, g in variantes.items():
        a = _alfa(v, conteudo, w, h, 0)
        if a is None:
            continue
        if sigma > 0:  # desfocada como o scan, a massa da candidata é medida do mesmo jeito que a da tinta
            a = cv2.GaussianBlur(a, (0, 0), sigma)
        e = abs(math.log(max(_massa(a), 1.0) / max(alvo, 1.0)))
        if erro is None or e < erro:
            melhor, erro = g, e
    if melhor is not None and melhor is not grupos[0]:
        grupos.remove(melhor)
        grupos.insert(0, melhor)


def identificar(img: np.ndarray, caixa, conteudo: str, prior: dict | None = None, top: int = 5,
                negrito: bool = True, fontes=None) -> dict:
    """Fontes que mais se parecem com ``conteudo`` escrito na ``caixa`` da imagem, da melhor para a pior.

    ``prior``: {fonte: parte do voto do documento (0..1)}, de ``votos_documento``. Cada item do ranking
    traz a fonte, a nota, o tamanho (px) e a largura (1 = sem esticar) que encaixam na tinta, o desfoque
    do scan e as ``parentes`` (fontes que desenham este texto praticamente igual). ``fontes``: só estas
    concorrem (ex.: ``fontes_top10()``); com a lista curta, a largura pode esticar um pouco mais (a fonte
    do emissor pode não estar entre elas, e a mais parecida ainda tem de caber no lugar)."""
    largura_max = LARGURA_MAX if fontes is None else 1.6
    conteudo = str(conteudo or "").strip()
    if not conteudo:
        raise ErroOperacao("sem o texto antigo não dá para comparar fontes")
    H, W = img.shape[:2]
    x0, y0, x1, y1 = (int(round(float(v))) for v in caixa)
    m = 3
    rx0, ry0, rx1, ry1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
    rec = img[ry0:ry1, rx0:rx1]
    if rec.size == 0:
        raise ErroOperacao("caixa fora da imagem")
    # só a linha principal: a margem de 3 px pegava o rótulo de cima e o fio da célula do boleto, a altura
    # medida dobrava e a fonte "vencedora" saía com o dobro do tamanho
    tinta = ocr.tinta_da_linha(rec)
    if not tinta.any():
        raise ErroOperacao("não há texto na caixa")
    ys, xs = np.flatnonzero(tinta.any(1)), np.flatnonzero(tinta.any(0))
    caixa_tinta = [int(rx0 + xs[0]), int(ry0 + ys[0]), int(rx0 + xs[-1] + 1), int(ry0 + ys[-1] + 1)]
    # linha inclinada (foto, scan torto): medida reta. Inclinada, a caixa inchava e o ajuste errava tamanho, largura
    # e negrito (1,2° fez Arial 44 virar Arial Narrow 62); o recorte é girado em volta do centro e medido de novo
    rotacao = ocr.inclinacao(tinta)
    centro = ((rx0 + rx1) / 2.0, (ry0 + ry1) / 2.0)
    if rotacao:
        hh, ww = rec.shape[:2]
        papel = np.median(np.concatenate([rec[0], rec[-1], rec[:, 0], rec[:, -1]]), axis=0)
        M = cv2.getRotationMatrix2D((ww / 2 - 0.5, hh / 2 - 0.5), -rotacao, 1.0)
        rec = cv2.warpAffine(rec, M, (ww, hh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                             borderValue=tuple(float(v) for v in papel))
        tinta = ocr.tinta_da_linha(rec)
        if not tinta.any():
            raise ErroOperacao("não há texto na caixa")
    # só a faixa central (linha das maiúsculas até a de base): o OCR lê sem acento ("CONCEICAO") e a cedilha, o til
    # grudado ou o acento do texto certo inchavam a altura medida (tamanho +9..+28%, ou a família trocada)
    topo, base = ocr.faixa_da_linha(tinta, conteudo)
    xs = np.flatnonzero(tinta[topo:base + 1].any(0))
    alvo = tinta[topo:base + 1, xs[0]:xs[-1] + 1]
    h, w = alvo.shape
    if h < 4 or w < 4:
        raise ErroOperacao("texto pequeno demais para identificar a fonte")
    caixa_faixa = [int(rx0 + xs[0]), int(ry0 + topo), int(rx0 + xs[-1] + 1), int(ry0 + base + 1)]
    prior = prior or {}
    pad = 3
    f = rec.astype(np.float32)
    borda = np.concatenate([f[0], f[-1], f[:, 0], f[:, -1]])
    perto = cv2.dilate(tinta.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)  # tinta de fora da linha não conta
    d = (np.abs(f - np.median(borda, axis=0)).max(axis=2) * perto)[topo:base + 1, xs[0]:xs[-1] + 1]
    t2 = d - d.mean()
    tn = float((t2 * t2).sum())
    yt = np.flatnonzero(tinta.any(1))
    wt, ht = caixa_tinta[2] - caixa_tinta[0], max(1, yt[-1] - yt[0] + 1)

    # 1. triagem: proporção da linha e correlação com a intensidade da tinta
    triagem = []
    for nome in candidatas(conteudo, fontes):
        fo = _f(nome)
        l, t, r, b = fo.getbbox(conteudo, anchor="ls")  # caixa pelas métricas: barato, sem desenhar
        if r - l <= 0 or b - t <= 0 or not 1 / (1.2 * largura_max) <= (wt / ht) / ((r - l) / (b - t)) <= 1.2 * largura_max:
            continue
        s = _na_faixa(_desenho(fo, conteudo), conteudo)  # sem cache: centenas de linhas por texto encheriam a memória
        if s is None:
            continue
        sh, sw = s.shape
        largura = (w / h) / (sw / sh)
        if not 1 / largura_max <= largura <= largura_max:
            continue
        r = cv2.GaussianBlur(cv2.resize(s, (w, h), interpolation=cv2.INTER_AREA), (0, 0), SIGMA_TRIAGEM)
        v1 = r - r.mean()
        sc = float((v1 * t2).sum() / math.sqrt(float((v1 * v1).sum()) * tn + 1e-9))
        triagem.append((sc - 0.5 * abs(math.log(largura)), nome, TAMANHO_TRIAGEM * h / sh, largura))
    if not triagem:
        raise ErroOperacao("nenhuma fonte instalada desenha esse texto com essa proporção")
    triagem.sort(reverse=True)

    # 2. comparação fina: desenho na caixa da tinta, desfocado como o scan. Borda de papel (zero) dos dois lados: a
    # faixa corta o pé da vírgula e do "g", e replicar a borda arrastava esse corte para fora só no scan
    d = cv2.copyMakeBorder(d, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    v2 = d - d.mean()
    n2 = float((v2 * v2).sum())
    # desfoque do scan medido UMA vez, pela nitidez das bordas (não depende da grossura do traço):
    # com cada fonte escolhendo o próprio desfoque, a negrito pouco desfocada se passava pela regular
    sigmas = SIGMAS
    a0 = _alfa(triagem[0][1], conteudo, w, h, 0)
    if a0 is not None:
        est = glifos.estimar_desfoque(img, caixa_tinta, a0)
        sigmas = tuple(sorted({max(0.0, round(est + k, 2)) for k in (-0.2, 0.0, 0.2)}))
    finas = []
    for _n, nome, _t, _l in triagem[:FINAIS]:
        s100 = _na_faixa(_desenho(_f(nome, 100.0), conteudo), conteudo)
        if s100 is None:
            continue
        # tamanho e largura medidos no desenho grande (no da triagem a dica de pixels erra ~2%)
        sh, sw = s100.shape
        tamanho, largura = 100.0 * h / sh, (w / h) / (sw / sh)
        a = np.pad(cv2.resize(s100, (w, h), interpolation=cv2.INTER_AREA), pad)
        sc, sg = _correlacao(a, v2, n2, sigmas)
        r = cv2.resize((s100 > 0.5).astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_AREA) > 127
        iou = float((r & alvo).sum() / max(1, (r | alvo).sum()))
        nota = (sc - 0.5 * abs(math.log(largura)) + PREFERENCIA.get(nome, 0.0)
                - (PENALIDADE_OUTRA_ESCRITA if OUTRA_ESCRITA.search(nome) else 0.0)
                + PESO_DOCUMENTO * float(prior.get(nome, 0.0)))
        finas.append({"nota": nota, "fonte": nome, "tamanho": tamanho, "largura": largura, "iou": iou,
                      "semelhanca": sc, "desfoque": sg, "_a": a})
    if not finas:
        raise ErroOperacao("não consegui desenhar o texto em nenhuma fonte")
    finas.sort(key=lambda c: -c["nota"])

    # parentes: desenhos (sem desfoque) quase idênticos para este texto vão para o mesmo grupo
    grupos: list[dict] = []
    for c in finas:
        va = c["_a"] - c["_a"].mean()
        for g in grupos:
            vb = g["_a"] - g["_a"].mean()
            if abs(math.log(c["largura"] / g["largura"])) < 0.03 and float(
                    (va * vb).sum() / math.sqrt(float((va * va).sum() * (vb * vb).sum()) + 1e-9)) >= PARENTE:
                g["parentes"].append(c["fonte"])
                break
        else:
            grupos.append({**c, "parentes": []})

    if fontes is not None:
        _peso_pelo_traco(grupos, d[pad:-pad, pad:-pad], conteudo, w, h, float(grupos[0]["desfoque"] or 0.0))
    melhor = grupos[0]
    # negrito sintético só se o traço mais grosso parecer MAIS com a tinta (e não por densidade)
    melhor["negrito_rel"] = 0.0
    if negrito:
        for rel in (0.01, 0.02, 0.03):
            a = _alfa(melhor["fonte"], conteudo, w, h, pad, negrito=rel * 100)
            if a is None:
                break
            sc2, sg2 = _correlacao(a, v2, n2, sigmas)
            if sc2 <= melhor["semelhanca"] + 0.01:
                break
            melhor.update(semelhanca=sc2, desfoque=sg2, negrito_rel=rel)
        if melhor["negrito_rel"]:  # o contorno engorda a letra nos dois eixos: tamanho e largura medidos com ele
            sb = _na_faixa(_desenho(_f(melhor["fonte"], 100.0), conteudo, melhor["negrito_rel"] * 50), conteudo)
            if sb is not None:
                melhor.update(tamanho=100.0 * h / sb.shape[0], largura=(w / h) / (sb.shape[1] / sb.shape[0]))
    margem = grupos[0]["nota"] - grupos[1]["nota"] if len(grupos) > 1 else 1.0
    alvo_d = d[pad:-pad, pad:-pad]

    def absoluta(g: dict) -> float:
        """Semelhança ABSOLUTA (0..1): correlação da tinta com o desenho (desfocado como o scan), os dois desfocados
        mais um tanto proporcional à altura da letra — compara a forma das letras, não a fase do pixel (um Arial
        limpo, com o hinting do programa que o gerou, ficava em 0,8-0,9) —, menos o quanto a fonte foi esticada."""
        a = _alfa(g["fonte"], conteudo, w, h, 0, negrito=float(g.get("negrito_rel", 0.0)) * 100)
        if a is None:
            return 0.0
        s0 = max(0.6, DESFOQUE_SEMELHANCA * h)
        m = int(math.ceil(3 * math.hypot(s0, float(g["desfoque"] or 0.0)))) + 1
        b = cv2.GaussianBlur(np.pad(a, m), (0, 0), math.hypot(s0, float(g["desfoque"] or 0.0)))
        e = cv2.GaussianBlur(np.pad(alvo_d, m), (0, 0), s0)
        b, e = b - b.mean(), e - e.mean()
        c = float((b * e).sum() / math.sqrt(float((b * b).sum() * (e * e).sum()) + 1e-9))
        return float(np.clip(c - 0.5 * abs(math.log(g["largura"])), 0.0, 1.0))

    # a "confiança" só compara o 1º com o 2º (Bahnschrift virava Arial com confiança "alta"); a semelhança é absoluta
    ranking = [{"fonte": g["fonte"], "nota": round(float(g["nota"]), 4), "tamanho": round(float(g["tamanho"]), 2),
                "largura": round(float(g["largura"]), 4), "iou": round(float(g["iou"]), 3),
                "semelhanca": round(absoluta(g), 3), "desfoque": g["desfoque"],
                "negrito": round(float(g.get("negrito_rel", 0.0)) * float(g["tamanho"]), 2),
                "parentes": g["parentes"]} for g in grupos[:top]]
    # caixa_faixa: a faixa central medida, nas coordenadas da linha já desinclinada (girada -rotacao em volta do centro)
    return {"texto": conteudo, "ranking": ranking, "margem": round(float(margem), 4),
            "confianca": ("baixa" if ranking[0]["semelhanca"] < LIMIAR_MESMA_FONTE else  # parecida, mas não é ela
                          "alta" if margem >= 0.03 else "média" if margem >= 0.01 else "baixa"),
            "avaliadas": len(triagem), "caixa_tinta": caixa_tinta, "caixa_faixa": caixa_faixa,
            "rotacao": rotacao, "centro": [round(centro[0], 2), round(centro[1], 2)]}


# ---------------------------------------------------------------------------- voto do documento
_lock = threading.Lock()
_votos: dict[str, dict] = {}


def votos_documento(img: np.ndarray, linhas: list[dict], maximo: int = 10) -> dict[str, float]:
    """{fonte: parte das linhas do documento em que ela (ou uma parente) venceu}. Linhas longas e bem
    lidas pelo OCR votam; as curtas e as duvidosas não (o voto existe justamente para ajudá-las)."""
    boas = [l for l in linhas if len(re.sub(r"\W", "", str(l.get("texto", "")))) >= 5 and l.get("conf", 1) >= 0.8]
    boas.sort(key=lambda l: -len(l["texto"]))
    votos: Counter = Counter()
    n = 0
    for l in boas[:maximo]:
        try:
            r = identificar(img, l["caixa"], l["texto"], top=1, negrito=False)
        except ErroOperacao:
            continue
        n += 1
        for nome in [r["ranking"][0]["fonte"], *r["ranking"][0]["parentes"]]:
            votos[nome] += 1
    return {k: round(v / n, 3) for k, v in votos.most_common()} if n else {}


def votos_contexto(ctx) -> dict[str, float]:
    """Voto do documento de um ``contexto.Contexto`` (memória + disco, pelo hash dos pixels)."""
    if getattr(ctx, "item", None) is None or not hasattr(ctx, "linhas"):
        return {}  # contexto sem o OCR do documento: não há linhas para votar
    chave = ctx.item.sha_pixels
    with _lock:
        if chave in _votos:
            return _votos[chave]
    arq = Path(ctx.raiz_saida) / "_cache" / "fontes" / f"{chave[:32]}.json"
    n_fontes = len(mod_texto.listar_fontes())
    try:
        dados = json.loads(arq.read_text(encoding="utf-8"))
        if dados.get("versao") == VERSAO and dados.get("fontes_instaladas") == n_fontes:
            votos = dados["votos"]
        else:
            votos = None
    except (OSError, ValueError, KeyError):
        votos = None
    if votos is None:
        votos = votos_documento(ctx.img, ctx.linhas)
        try:
            arq.parent.mkdir(parents=True, exist_ok=True)
            arq.write_text(json.dumps({"versao": VERSAO, "fontes_instaladas": n_fontes, "votos": votos},
                                      ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # cache é só conveniência
    with _lock:
        if len(_votos) > 500:
            _votos.clear()
        _votos[chave] = votos
    return votos


# ---------------------------------------------------------------------------- conferência a olho
def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(float(v))) for v in c)


def amostras(img: np.ndarray, r: dict) -> dict:
    """O texto antigo desenhado em cada fonte do ranking (``identificar``) no lugar, no tamanho e na cor
    da tinta, sobre a cor do papel, do mesmo tamanho do recorte original: para comparar a olho."""
    from .ops import auto

    H, W = img.shape[:2]
    x0, y0, x1, y1 = r["caixa_tinta"]
    m = max(3, int(round(0.35 * (y1 - y0))))
    X0, Y0, X1, Y1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
    rec = img[Y0:Y1, X0:X1]
    tinta = mascara_tinta(rec).astype(bool)
    f32 = rec.astype(np.float32)
    papel = np.median(f32[~tinta], axis=0) if (~tinta).any() else np.full(3, 255.0, np.float32)
    lum = f32 @ np.float32([0.299, 0.587, 0.114])
    forte = tinta & (lum <= np.percentile(lum[tinta], 50)) if tinta.any() else tinta
    cor = np.median(f32[forte], axis=0) if forte.any() else np.zeros(3, np.float32)
    itens = []
    for c in r["ranking"]:
        x, y = auto.ancora(c["fonte"], r["texto"], c["tamanho"], c["largura"], c["negrito"], r["caixa_tinta"])
        p = {"texto": r["texto"], "fonte": c["fonte"], "tamanho": c["tamanho"], "largura": c["largura"] * 100,
             "negrito": c["negrito"], "desfoque": c["desfoque"], "x": x, "y": y}
        ty0, ty1, tx0, tx1, a = mod_texto.renderizar(p, H, W)
        tela = np.zeros(rec.shape[:2], np.float32)
        iy0, iy1, ix0, ix1 = max(ty0, Y0), min(ty1, Y1), max(tx0, X0), min(tx1, X1)
        if iy1 > iy0 and ix1 > ix0:
            tela[iy0 - Y0:iy1 - Y0, ix0 - X0:ix1 - X0] = a[iy0 - ty0:iy1 - ty0, ix0 - tx0:ix1 - tx0]
        desenho = papel * (1 - tela[..., None]) + cor * tela[..., None]
        itens.append({**c, "x": round(float(x), 2), "y": round(float(y), 2),
                      "imagem": np.clip(desenho + 0.5, 0, 255).astype(np.uint8)})
    return {"original": np.ascontiguousarray(rec), "itens": itens, "cor": _hex(cor), "papel": _hex(papel)}
