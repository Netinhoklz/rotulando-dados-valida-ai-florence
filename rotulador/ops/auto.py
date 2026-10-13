"""Troca automática de um campo: apaga o texto antigo, escreve o novo com a fonte mais
parecida e iguala o grão do papel — tudo numa operação só (um passo no Ctrl+Z).

``ajustar_fonte`` escolhe a fonte com o identificador (rotulador.identificar_fonte): o texto antigo
(lido pelo OCR) desenhado em TODAS as fontes instaladas e comparado com a tinta do scan; depois
calcula tamanho, largura, negrito e a posição que põem o texto novo exatamente onde estava o antigo.
"""

from __future__ import annotations

import math
import threading

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import selecao
from ..selecao import Selecao, mascara_tinta
from ..sessao import ErroOperacao, Resultado
from . import ajustes, codigos, geradores, ia, texto
from .base import compor, inteiro, num

_cache_lock = threading.Lock()
_cache: dict = {}


def _mascara_texto(nome: str, conteudo: str, tamanho: float = 100.0, negrito: float = 0.0):
    """Tinta (bool) do texto desenhado na fonte, recortada justa, e o deslocamento da âncora. Desenhada pelo próprio
    texto.renderizar (superamostrado, negrito isotrópico): a âncora calculada aqui põe a tinta onde ele a desenha."""
    P = 10000.0
    try:
        y0, _y1, x0, _x1, a = texto.renderizar({"texto": conteudo, "fonte": nome, "tamanho": tamanho, "negrito": negrito,
                                                "x": P, "y": P}, 10**5, 10**5)
    except ErroOperacao:
        return None, None
    a = a > 0.5
    if not a.any():
        return None, None
    ys, xs = np.flatnonzero(a.any(1)), np.flatnonzero(a.any(0))
    # posição da tinta em relação à âncora (início da linha de base)
    desloc = (x0 + xs[0] - P, y0 + ys[0] - P)
    return a[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1], desloc


def _alfa_texto(nome: str, conteudo: str, w: int, h: int, pad: int, negrito: float = 0.0):
    """Tinta suave (0..1) do texto na fonte (tamanho 100, ``negrito`` em px desse tamanho), esticada
    para w x h, com ``pad`` px de borda."""
    caminho, idx = texto.listar_fontes()[nome]
    fonte = ImageFont.truetype(caminho, 100, index=idx)
    l, t, r, b = fonte.getbbox(conteudo, anchor="ls")
    m = 4 + int(negrito)
    tela = Image.new("L", (int(r - l) + 2 * m, int(b - t) + 2 * m), 0)
    d = ImageDraw.Draw(tela)
    for off in ([0.0] if negrito <= 0 else np.linspace(0, negrito, max(2, int(math.ceil(negrito * 2)) + 1))):
        d.text((m - l + off, m - t), conteudo, fill=255, font=fonte, anchor="ls")
    a = np.asarray(tela, np.float32) / 255.0
    nz = a > 0.5
    if not nz.any():
        return None
    ys, xs = np.flatnonzero(nz.any(1)), np.flatnonzero(nz.any(0))
    a = cv2.resize(a[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1], (w, h), interpolation=cv2.INTER_AREA)
    return np.pad(a, pad)


ESPACO_MAX = 0.05  # espaçamento entre letras plausível, em fração do tamanho da fonte (±)


def _medir_faixa(params: dict) -> tuple[float, float, float, float]:
    """(x0, topo, x1, base + 1) da faixa central (ocr.faixa_da_linha) do texto de ``params`` desenhado com a âncora em
    (0, 0), reto e nítido: a mesma régua com que o identificador mediu o scan."""
    from .. import ocr

    P = 10000.0
    y0, _y1, x0, _x1, a = texto.renderizar({**params, "x": P, "y": P, "rotacao": 0, "desfoque": 0}, 10**5, 10**5)
    m = ocr.so_a_linha(a > 0.5) if (a > 0.5).any() else a > 0  # letra miúda: nenhum pixel passa da metade
    topo, base = ocr.faixa_da_linha(m, params["texto"])
    xs = np.flatnonzero(m[topo:base + 1].any(0))
    return x0 + xs[0] - P, y0 + topo - P, x0 + xs[-1] + 1 - P, y0 + base + 1 - P


def ajustar_fonte(img: np.ndarray, caixa, antigo: str, prior: dict | None = None, fontes=None) -> dict:
    """Parâmetros de texto (fonte, tamanho, largura, espaçamento, negrito, rotação, cor, x, y) que imitam o texto da
    caixa, mais a ``semelhanca`` (0..1, absoluta: ver identificar_fonte.LIMIAR_MESMA_FONTE) e o ``arquivo_fonte``.

    ``prior``: voto do documento ({fonte: parte}), que desempata campos curtos. ``fontes``: só estas concorrem
    (ex.: ``identificar_fonte.fontes_top10()``)."""
    from .. import identificar_fonte

    H, W = img.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in caixa)
    f = 3
    rx0, ry0, rx1, ry1 = max(0, x0 - f), max(0, y0 - f), min(W, x1 + f), min(H, y1 + f)
    rec = img[ry0:ry1, rx0:rx1]
    from .. import ocr
    tinta = ocr.tinta_da_linha(rec)  # a cor é a da linha, não a do rótulo ou do fio vizinho
    if not tinta.any() or not antigo.strip():
        est = texto.estimar(img, selecao.rasterizar({"formas": [{"tipo": "ret", "x0": x0, "y0": y0, "x1": x1,
                                                                  "y1": y1}]}, img))
        return {**est, "largura": 100, "negrito": 0, "espacamento": 0.0, "rotacao": 0.0, "semelhanca": 0.0,
                "arquivo_fonte": texto.arquivo_fonte(est["fonte"]), "metodo": "estimativa"}
    # cor: mediana dos pixels mais escuros (núcleo dos traços)
    f32 = rec.astype(np.float32)
    lum = f32 @ np.float32([0.299, 0.587, 0.114])
    # núcleo dos traços: o quinto mais escuro (a metade pegava a borda com antialias e o texto novo saía mais claro)
    forte = tinta & (lum <= np.percentile(lum[tinta], 20))
    cor = np.median(f32[forte if forte.any() else tinta], axis=0)

    try:
        r = identificar_fonte.identificar(img, caixa, antigo, prior=prior, top=3, fontes=fontes)
    except ErroOperacao:
        raise ErroOperacao("não consegui imitar a fonte desse texto")
    m = r["ranking"][0]
    pobre = m["semelhanca"] < identificar_fonte.LIMIAR_MESMA_FONTE
    if pobre and prior:
        # casamento ruim (o texto do OCR não é o impresso, "C U RITIBAPR"; ou a fonte é de fora): a fonte em que o
        # resto do documento está escrito, se concorre, vale mais que a que só encaixou esticando (Arial numa conta
        # toda em Times)
        dona = next((f for f, p in sorted(prior.items(), key=lambda kv: -kv[1])
                     if p >= 0.5 and (fontes is None or f in fontes)), None)
        candidatas = [f for f in (dona, f"{dona} Bold") if f in (fontes if fontes is not None else texto.listar_fontes())]
        if dona and m["fonte"] not in candidatas and candidatas:
            try:
                r = identificar_fonte.identificar(img, caixa, antigo, prior=prior, top=1, fontes=candidatas)
                m = r["ranking"][0]
            except ErroOperacao:
                pass
    nome, tamanho, largura, negrito = m["fonte"], float(m["tamanho"]), float(m["largura"]), m["negrito"]
    # largura: o que falta ou sobra vai primeiro para o espaço entre as letras (dentro de ±ESPACO_MAX do tamanho) e
    # só o resto para a escala horizontal. Tudo na escala deixava as letras esticadas ou espremidas
    base = {"texto": r["texto"], "fonte": nome, "tamanho": tamanho, "negrito": negrito, "largura": 100}
    bx0, by0, bx1, by1 = r["caixa_faixa"]
    esp, n = 0.0, len(r["texto"]) - 1
    if n >= 1:
        f0 = _medir_faixa(base)
        esp = float(np.clip((bx1 - bx0 - (f0[2] - f0[0])) / n, -ESPACO_MAX * tamanho, ESPACO_MAX * tamanho))
        f1 = _medir_faixa({**base, "espacamento": esp})
        largura = (bx1 - bx0) / max(1.0, f1[2] - f1[0])
        if pobre and abs(largura - 1) > 0.10:
            # casamento ruim E nem o espaçamento explica a largura: o texto lido não é o impresso ("205m" de
            # "205 m³"); esticar até a tinta espaçava as letras ("1 1 6m"). Fica a largura natural da fonte
            esp, largura = 0.0, 1.0
    base.update(largura=round(largura * 100, 1), espacamento=round(esp, 2))
    # âncora na linha reta: a faixa central do texto antigo desenhado em cima da medida no scan (esquerda; linha de
    # base pela média do topo e do pé da faixa, que o tamanho igualou), depois girada de volta para a linha inclinada
    fx0, ft, _fx1, fb = _medir_faixa(base)
    x, y = bx0 - fx0, ((by0 - ft) + (by1 - fb)) / 2
    rotacao = float(r.get("rotacao") or 0.0)
    if rotacao:
        cx, cy = r["centro"]
        t = math.radians(rotacao)
        x, y = cx + math.cos(t) * (x - cx) + math.sin(t) * (y - cy), cy - math.sin(t) * (x - cx) + math.cos(t) * (y - cy)
    return {"fonte": nome, "tamanho": round(tamanho, 2), "largura": base["largura"], "espacamento": base["espacamento"],
            "negrito": negrito, "rotacao": rotacao, "x": float(x), "y": float(y),
            "cor": "#%02x%02x%02x" % tuple(int(round(v)) for v in cor),
            "iou": m["iou"], "semelhanca": m["semelhanca"], "desfoque_scan": m["desfoque"], "metodo": "comparacao",
            "confianca": r["confianca"], "parentes": m["parentes"], "arquivo_fonte": texto.arquivo_fonte(nome),
            "alternativas": [c["fonte"] for c in r["ranking"][1:]]}


def ancora(nome: str, conteudo: str, tamanho: float, largura: float, negrito: float, caixa_tinta) -> tuple[float, float]:
    """Âncora (início da linha de base) que põe a tinta do texto exatamente no canto da ``caixa_tinta``."""
    _m, (dx, dy) = _mascara_texto(nome, conteudo, tamanho, negrito)
    return caixa_tinta[0] - dx * largura, caixa_tinta[1] - dy


def ajustar_fonte_cache(img, caixa, antigo, chave=None, prior: dict | None = None, fontes=None) -> dict:
    k = None if chave is None else (chave, tuple(caixa), antigo, bool(prior), tuple(fontes) if fontes is not None else None)
    if k is not None:
        with _cache_lock:
            if k in _cache:
                return _cache[k]
    r = ajustar_fonte(img, caixa, antigo, prior, fontes)
    if k is not None:
        with _cache_lock:
            if len(_cache) > 200:
                _cache.clear()
            _cache[k] = r
    return r


def _sel_campo(img, x0, y0, x1, y1, folga: int) -> Selecao:
    """O que se apaga para trocar um campo: a caixa com folga, MENOS a tinta de fora da linha que entra pela borda
    (o rótulo miúdo encostado em cima, no boleto; o topo da linha de baixo). Acento e vírgula ficam inteiros
    dentro da caixa e saem junto com o texto; o rótulo continua do lado de fora, então fica."""
    from .. import ocr

    H, W = img.shape[:2]
    ax0, ay0, ax1, ay1 = max(0, x0 - folga), max(0, y0 - folga), min(W, x1 + folga), min(H, y1 + folga)
    m = np.zeros((H, W), bool)
    m[ay0:ay1, ax0:ax1] = True
    rec = img[ay0:ay1, ax0:ax1]
    if rec.size:
        tinta = mascara_tinta(rec).astype(np.uint8)
        linha = cv2.dilate(ocr.tinta_da_linha(rec).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        outra = (tinta > 0) & ~linha
        if outra.any():
            n, lab, est, _c = cv2.connectedComponentsWithStats(outra.astype(np.uint8), connectivity=8)
            h, w = outra.shape
            borda = [i for i in range(1, n) if est[i, cv2.CC_STAT_LEFT] == 0 or est[i, cv2.CC_STAT_TOP] == 0
                     or est[i, cv2.CC_STAT_LEFT] + est[i, cv2.CC_STAT_WIDTH] >= w
                     or est[i, cv2.CC_STAT_TOP] + est[i, cv2.CC_STAT_HEIGHT] >= h]
            if borda:
                prot = cv2.dilate(np.isin(lab, borda).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
                m[ay0:ay1, ax0:ax1] &= ~prot
    return selecao.de_mascara(m)


def _buraco_tinta(img, x0, y0, x1, y1, sigma: float) -> np.ndarray:
    """Máscara (H×W) do que se apaga com ``so_tinta``: só a tinta do texto antigo, não a caixa. É a linha do campo
    (ocr.tinta_da_linha, sem fio de tabela nem rótulo) mais os sinais soltos dela — acento, til, cedilha, vírgula,
    pingo: pequenos, colados em cima ou embaixo da linha, sem encostar na borda do recorte (o rótulo e a linha vizinha
    encostam) —, dilatada para levar o halo do antialias e do desfoque do scan (``sigma``). Apagando a caixa inteira,
    a máscara da edição era um retângulo na linha de texto e o detector aprendia o retângulo."""
    from .. import ocr

    H, W = img.shape[:2]
    h = max(1, y1 - y0)
    folga = max(3, int(round(0.15 * h)))
    my, mx = max(4, int(round(0.7 * h))), max(3, int(round(0.3 * h)))
    ay0, ay1, ax0, ax1 = max(0, y0 - my), min(H, y1 + my), max(0, x0 - mx), min(W, x1 + mx)
    by0, by1, bx0, bx1 = max(0, y0 - folga), min(H, y1 + folga), max(0, x0 - folga), min(W, x1 + folga)
    linha = np.zeros((ay1 - ay0, ax1 - ax0), bool)
    linha[by0 - ay0:by1 - ay0, bx0 - ax0:bx1 - ax0] = ocr.tinta_da_linha(img[by0:by1, bx0:bx1])
    out = np.zeros((H, W), bool)
    if not linha.any():
        return out
    ys, xs = np.flatnonzero(linha.any(1)), np.flatnonzero(linha.any(0))
    hl = ys[-1] - ys[0] + 1
    tinta = mascara_tinta(img[ay0:ay1, ax0:ax1]).astype(bool)
    outra = tinta & ~(cv2.dilate(linha.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    n, lab, est, _c = cv2.connectedComponentsWithStats(outra.astype(np.uint8), connectivity=8)
    hh, ww = outra.shape
    sinais = [i for i in range(1, n)
              if est[i, cv2.CC_STAT_LEFT] > 0 and est[i, cv2.CC_STAT_TOP] > 0
              and est[i, cv2.CC_STAT_LEFT] + est[i, cv2.CC_STAT_WIDTH] < ww
              and est[i, cv2.CC_STAT_TOP] + est[i, cv2.CC_STAT_HEIGHT] < hh
              and est[i, cv2.CC_STAT_HEIGHT] <= 0.45 * hl and est[i, cv2.CC_STAT_WIDTH] <= 0.9 * hl
              and est[i, cv2.CC_STAT_TOP] + est[i, cv2.CC_STAT_HEIGHT] - 1 >= ys[0] - 0.35 * hl
              and est[i, cv2.CC_STAT_TOP] <= ys[-1] + 0.35 * hl
              and xs[0] - 0.2 * hl <= est[i, cv2.CC_STAT_LEFT] + est[i, cv2.CC_STAT_WIDTH] / 2 <= xs[-1] + 0.2 * hl]
    velho = linha | np.isin(lab, sinais)
    r = 1 + int(math.ceil(1.5 * sigma))  # o halo do desfoque vai a ~1,5 sigma além da borda da máscara (Otsu)
    velho = cv2.dilate(velho.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0
    out[ay0:ay1, ax0:ax1] = velho
    return out & ~texto._fios_da_tabela(img, y0, y1, x0, x1)


def _sel_humana(img, velho: np.ndarray, caixa, hsel: dict, semente: int) -> Selecao:
    """A seleção de uma pessoa (``rotulador.humano.selecionar``: retângulo, laço, pincel ou só a tinta) em volta do
    texto antigo ``velho`` (H×W), sem pegar a tinta alheia em volta."""
    import random

    from .. import humano

    H, W = img.shape[:2]
    if not velho.any():
        return selecao.vazia(H, W)
    x0, y0, x1, y1 = caixa
    h = max(4, y1 - y0)
    M = int(max(12, 1.2 * h))
    ay0, ay1, ax0, ax1 = max(0, y0 - M), min(H, y1 + M), max(0, x0 - M), min(W, x1 + M)
    tinta = mascara_tinta(img[ay0:ay1, ax0:ax1]).astype(bool)
    sel = humano.selecionar(velho[ay0:ay1, ax0:ax1], tinta, str(hsel.get("forma") or "retangulo"),
                            hsel.get("margem") or (0.15,) * 4, float(h), random.Random(semente ^ 0x5E1))
    out = np.zeros((H, W), bool)
    out[ay0:ay1, ax0:ax1] = sel
    return selecao.de_mascara(out & ~texto._fios_da_tabela(img, y0, y1, x0, x1))


def _alinhar(params: dict, antigo: str, x0: int, x1: int, modo: str) -> tuple[float, float]:
    """Âncora que põe a tinta NÍTIDA (alfa > 0,5) do texto novo terminando onde a do antigo terminava ("direita") ou
    com o mesmo centro ("centro"), andando ao longo da linha de base (inclinada, se for o caso). Pela cauda do alfa
    desfocado contra a caixa do OCR, o texto novo terminava 1-2 px antes."""
    def extensao(s: str) -> tuple[float, float]:
        P = 10000.0
        _y0, _y1, tx0, _tx1, a = texto.renderizar({**params, "texto": s, "x": P, "y": P, "rotacao": 0, "desfoque": 0},
                                                  10**5, 10**5)
        cols = np.flatnonzero((a > 0.5).any(0))
        if not cols.size:
            cols = np.flatnonzero((a > 0).any(0))
        return tx0 + cols[0] - P, tx0 + cols[-1] + 1 - P

    n0, n1 = extensao(params["texto"])
    # sem o texto antigo: a borda da caixa (o texto reto)
    o0, o1 = extensao(antigo.strip()) if antigo.strip() else (x0 - params["x"], x1 - params["x"])
    d = (o1 - n1) if modo == "direita" else ((o0 + o1) - (n0 + n1)) / 2
    t = math.radians(float(params.get("rotacao") or 0.0))
    return params["x"] + d * math.cos(t), params["y"] - d * math.sin(t)


def _tinta_antiga(img, x0, y0, x1, y1) -> float | None:
    """p10 da luminância (suavizada 0,8 px, o grão quase não pesa) na tinta da linha antiga: o alvo da cor do texto
    novo (texto.substituir_texto ``tinta_alvo``)."""
    from .. import ocr

    H, W = img.shape[:2]
    rec = img[max(0, y0 - 3):min(H, y1 + 3), max(0, x0 - 3):min(W, x1 + 3)]
    t = ocr.tinta_da_linha(rec)
    if t.sum() < 10:
        return None
    L = cv2.GaussianBlur(rec.astype(np.float32) @ np.float32([0.299, 0.587, 0.114]), (0, 0), 0.8)
    return float(np.percentile(L[t], 10))


def _sel_ret(img, x0, y0, x1, y1) -> Selecao:
    return selecao.rasterizar({"formas": [{"tipo": "ret", "x0": x0, "y0": y0, "x1": x1, "y1": y1}]}, img)


def _com_ruido(img, base, res: Resultado, semente: int) -> Resultado:
    """Iguala o grão do papel dentro da pegada do resultado (se faltar grão)."""
    if base is None:
        return res
    H, W = img.shape[:2]
    tmp = img.copy()
    tmp[res.y0:res.y1, res.x0:res.x1] = np.where(res.pegada[..., None], res.recorte, tmp[res.y0:res.y1, res.x0:res.x1])
    m = np.zeros((H, W), bool)
    m[res.y0:res.y1, res.x0:res.x1] = res.pegada
    sel = selecao.de_mascara(m)
    try:
        ruidoso = ajustes._igualar_ruido(tmp, sel, sel.bbox, {"semente": semente}, base)
    except ErroOperacao:
        return res  # o grão já é igual ao do papel
    y0, y1, x0, x1 = sel.bbox
    novo = res.recorte.copy()
    alvo = novo[y0 - res.y0:y1 - res.y0, x0 - res.x0:x1 - res.x0]
    alvo[:] = np.where(sel.alpha[..., None] > 0, ruidoso, alvo)
    res.recorte = novo
    res.meta = {**res.meta, "ruido": "igualado"}
    return res


def trocar_campo(img: np.ndarray, p: dict, sel: Selecao, base: np.ndarray = None, chave_cache=None, documento=None,
                 **_) -> Resultado:
    """Troca o conteúdo de um campo detectado (texto, código de barras ou QR)."""
    H, W = img.shape[:2]
    try:
        x0, y0, x1, y1 = (int(round(float(v))) for v in p["caixa"])
    except (KeyError, TypeError, ValueError):
        raise ErroOperacao("campo sem caixa")
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ErroOperacao("caixa do campo inválida")
    tipo = p.get("tipo", "texto")
    semente = inteiro(p, "semente", 0, 0, 2**31 - 1)
    ruido = bool(p.get("igualar_ruido", True))

    if tipo == "barras":
        digitos = "".join(c for c in str(p.get("texto") or "") if c.isdigit()) or geradores.par_codigo(True)[0]
        res = codigos.codigo_barras(img, {"digitos": digitos, "cor": p.get("cor") or _cor_do_codigo(img, x0, y0, x1, y1),
                                          "cor_fundo": p.get("cor_fundo") or _cor_fundo(img, x0, y0, x1, y1)},
                                    _sel_ret(img, x0, y0, x1, y1))
        res = _nitidez_do_codigo(img, (x0, y0, x1, y1), res)
        res.meta = {**res.meta, "auto": "codigo_barras"}
        return _com_ruido(img, base, res, semente) if ruido else res
    if tipo == "qr":
        conteudo = str(p.get("texto") or "") or geradores.gerar("qr code/pix")["texto"]
        res = codigos.qrcode(img, {"conteudo": conteudo, "borda": 0, "cor": p.get("cor") or _cor_do_codigo(img, x0, y0, x1, y1),
                                   "cor_fundo": p.get("cor_fundo") or _cor_fundo(img, x0, y0, x1, y1)},
                             _sel_ret(img, x0, y0, x1, y1))
        res = _nitidez_do_codigo(img, (x0, y0, x1, y1), res)
        return _com_ruido(img, base, res, semente) if ruido else res

    novo = str(p.get("texto", "")).strip()
    if not novo:
        raise ErroOperacao("digite o valor novo (ou use 🎲)")
    antigo = str(p.get("texto_antigo", ""))
    if p.get("fonte_params"):
        ajuste = dict(p["fonte_params"])
    else:
        from .. import identificar_fonte
        prior = identificar_fonte.votos_contexto(documento) if documento is not None else None
        fontes = identificar_fonte.fontes_top10() if p.get("fontes") == "top10" else None
        ajuste = dict(ajustar_fonte_cache(img, (x0, y0, x1, y1), antigo, chave_cache, prior, fontes))
    if p.get("fonte") not in (None, "", ajuste.get("fonte")):
        ajuste["semelhanca"] = None  # a semelhança medida era a da fonte identificada, não a da escolhida à mão
    for k in ("fonte", "tamanho", "largura", "negrito", "cor"):  # o usuário pode corrigir à mão
        if p.get(k) not in (None, ""):
            ajuste[k] = p[k]
    desfoque = num(p, "desfoque", 0, 0, 5)
    # so_tinta: apaga só a tinta do texto antigo (com o halo do desfoque do SCAN, mesmo quando o texto novo sai
    # nítido) e reconstrói o fundo pelo "local"; sem ele, a caixa inteira com folga, como antes
    so_tinta = bool(p.get("so_tinta"))
    sigma = max(desfoque, float(ajuste.get("desfoque_scan") or 0.0))
    hsel = p.get("selecao_humana") if isinstance(p.get("selecao_humana"), dict) else None
    apagar = None
    if hsel:  # a seleção que uma pessoa faz em volta do texto antigo (rotulador.humano)
        apagar = _sel_humana(img, _buraco_tinta(img, x0, y0, x1, y1, sigma), (x0, y0, x1, y1), hsel, semente)
        so_tinta = hsel.get("forma") == "tinta"
    elif so_tinta:
        apagar = selecao.de_mascara(_buraco_tinta(img, x0, y0, x1, y1, sigma))
    if apagar is None or apagar.vazia:
        so_tinta, hsel = False, None  # sem tinta da linha achada: a caixa (o rótulo diz)
        apagar = _sel_campo(img, x0, y0, x1, y1, max(3, int(round(0.15 * (y1 - y0)))))
    params = {"texto": novo, "fonte": ajuste["fonte"], "tamanho": ajuste["tamanho"], "largura": ajuste.get("largura", 100),
              "negrito": ajuste.get("negrito", 0), "cor": ajuste["cor"], "x": ajuste["x"], "y": ajuste["y"],
              "rotacao": float(ajuste.get("rotacao") or 0.0), "patch": 7, "semente": semente, "desfoque": desfoque,
              "espacamento": float(ajuste.get("espacamento") or 0.0) + num(p, "espacamento", 0, -50, 200),
              "apagar": p.get("apagar") or ("local" if so_tinta else "patchmatch")}
    if p.get("rasterizador"):
        params["rasterizador"] = str(p["rasterizador"])
    if hsel:  # seleção larga: só papel serve de origem para o preenchimento (nem o rótulo nem a linha vizinha)
        params["so_papel"] = True
    alinhar = p.get("alinhar") if p.get("alinhar") in ("direita", "centro") else "esquerda"
    if alinhar != "esquerda":  # o texto novo termina onde o antigo terminava, ou fica centrado nele
        params["x"], params["y"] = _alinhar(params, antigo, x0, x1, alinhar)
    if isinstance(p.get("no_olho"), (list, tuple)):  # posto à mão: a camada no pixel inteiro, ± o erro de quem arrasta
        params["x"] = float(round(params["x"]) + int(p["no_olho"][0]))
        params["y"] = float(round(params["y"]) + int(p["no_olho"][1]))
    # cor: a medida no scan já borrado, borrada de novo, deixava o texto novo mais claro que o antigo; calibrada para a
    # tinta nova, desenhada com o desfoque, ter o mesmo p10 de luminância (a cor dada à mão fica como está)
    alvo = _tinta_antiga(img, x0, y0, x1, y1) if p.get("cor") in (None, "") else None
    chave = None if chave_cache is None else (chave_cache, "auto", x0, y0, x1, y1, so_tinta, round(sigma, 2),
                                              None if hsel is None else (hsel.get("forma"), tuple(hsel.get("margem") or ())))
    res = texto.substituir_texto(img, params, apagar, chave_cache=chave, tinta_alvo=alvo)
    cor_usada = res.meta.get("cor", ajuste["cor"])
    res.meta = {**res.meta, "auto": "trocar_campo", "fonte": ajuste["fonte"], "tamanho": ajuste["tamanho"],
                "largura": ajuste.get("largura"), "iou_fonte": ajuste.get("iou"), "texto_antigo": antigo,
                "fonte_confianca": ajuste.get("confianca"), "semelhanca": ajuste.get("semelhanca"),
                "arquivo_fonte": texto.arquivo_fonte(ajuste["fonte"]),  # o arquivo que desenhou (mesmo trocada à mão)
                "so_tinta": so_tinta, "selecao_humana": hsel.get("forma") if hsel else None, "alinhar": alinhar, "cor": cor_usada, "cor_calibrada": alvo is not None,
                "ajuste": {**{k: ajuste.get(k) for k in ("fonte", "tamanho", "largura", "negrito")}, "cor": cor_usada,
                           "cor_medida": ajuste.get("cor"), "rotacao": params["rotacao"],
                           "espacamento": params["espacamento"], "x": params["x"], "y": params["y"]}}
    return _com_ruido(img, base, res, semente) if ruido else res


def _cor_do_codigo(img, x0, y0, x1, y1) -> str:
    """A cor das barras/módulos do código ORIGINAL: o miolo da tinta (os 15% mais escuros da caixa). O código novo
    saía preto puro (#000) e nítido num scan em que o original é cinza-escuro e borrado — destoava à primeira vista."""
    rec = img[y0:y1, x0:x1].astype(np.float32)
    if rec.size == 0:
        return "#000000"
    lum = rec @ np.float32([0.299, 0.587, 0.114])
    miolo = lum <= np.percentile(lum, 15)
    c = np.median(rec[miolo], axis=0)
    return "#%02x%02x%02x" % tuple(int(round(float(v))) for v in c)


def _nitidez_do_codigo(img, caixa, res: Resultado) -> Resultado:
    """Desfoca o código novo até a borda das barras ficar tão macia quanto a do original (energia de borda por
    contraste, σ de 0 a 2,5 px). Original nítido (PDF): fica como está."""
    x0, y0, x1, y1 = caixa
    L = np.float32([0.299, 0.587, 0.114])

    def macio(lum):
        gy, gx = np.gradient(lum)
        g = np.hypot(gx, gy)
        contraste = float(np.percentile(lum, 95) - np.percentile(lum, 5))
        sel = g > 0.1 * max(contraste, 1.0)
        if sel.sum() < 30 or contraste < 20:
            return None
        return float((g[sel] ** 2).sum() / g[sel].sum()) / contraste  # alto = nítido
    alvo = macio(img[y0:y1, x0:x1].astype(np.float32) @ L)
    rec = res.recorte.astype(np.float32)
    atual = macio(rec @ L)
    if alvo is None or atual is None or atual <= alvo * 1.05:
        return res
    melhor = (abs(atual - alvo), 0.0, rec)
    for s in np.arange(0.3, 2.55, 0.15):
        b = cv2.GaussianBlur(rec, (0, 0), float(s))
        m = macio(b @ L)
        if m is None:
            break
        if abs(m - alvo) < melhor[0]:
            melhor = (abs(m - alvo), float(s), b)
        if m < alvo:
            break
    if melhor[1] > 0:
        res.recorte = np.clip(melhor[2] + 0.5, 0, 255).astype(np.uint8)
        res.meta = {**res.meta, "desfoque_codigo": round(melhor[1], 2)}
    return res


def _cor_fundo(img, x0, y0, x1, y1) -> str:
    """Cor do papel em volta da caixa (para o fundo de códigos)."""
    H, W = img.shape[:2]
    m = 6
    anel = np.concatenate([img[max(0, y0 - m):y0, x0:x1].reshape(-1, 3), img[y1:min(H, y1 + m), x0:x1].reshape(-1, 3)])
    if not len(anel):
        return "#ffffff"
    c = np.median(anel, axis=0)
    return "#%02x%02x%02x" % tuple(int(v) for v in c)


# instrução para a IA (em inglês: os dois modelos seguem melhor; os textos entre aspas ficam como estão)
PROMPT_TROCA_IA = ('Replace the text "{antigo}" with "{novo}". Keep exactly the same font, size, weight, color, '
                   'letter spacing, alignment and background as the original text. Do not change anything else.')


def trocar_campo_ia(img: np.ndarray, p: dict, sel: Selecao, base: np.ndarray = None, editor=None, **_) -> Resultado:
    """Como trocar_campo, mas quem escreve o texto novo é a IA (Qwen) na região do campo."""
    H, W = img.shape[:2]
    try:
        x0, y0, x1, y1 = (int(round(float(v))) for v in p["caixa"])
    except (KeyError, TypeError, ValueError):
        raise ErroOperacao("campo sem caixa")
    novo = str(p.get("texto", "")).strip()
    antigo = str(p.get("texto_antigo", "")).strip()
    if not novo:
        raise ErroOperacao("digite o valor novo (ou use 🎲)")
    if not antigo:
        raise ErroOperacao("a IA precisa do texto antigo do campo (marque o campo de novo)")
    h, w = y1 - y0, x1 - x0
    folga = max(4, int(round(0.3 * h)))
    # valor mais longo precisa de mais espaço à direita (proporção de caracteres). A IA não sabe a
    # largura exata: com a proporção pura ela cortou a última letra de um nome; 20% + uma altura de folga
    largura = int(w * max(1.0, 1.2 * len(novo) / max(1, len(antigo)))) + folga + h
    regiao = _sel_ret(img, x0 - folga, y0 - folga, min(W, x0 + largura), y1 + folga)
    pedido = {"prompt": str(p.get("prompt") or PROMPT_TROCA_IA.format(antigo=antigo, novo=novo)),
              "semente": inteiro(p, "semente", 0, 0, 2**31 - 1), "passos": p.get("passos"),
              "modelo": p.get("modelo"), "contexto": num(p, "contexto", 0.6, 0, 3), "megapixels": 1.0,
              "colagem": p.get("colagem") or "selecao", "pena": p.get("pena", 2)}
    res = ia.editar(img, pedido, regiao, editor=editor)
    res.meta = {**res.meta, "auto": "trocar_campo_ia", "texto_antigo": antigo, "texto_novo": novo}
    return _com_ruido(img, base, res, inteiro(p, "semente", 0, 0, 2**31 - 1)) if p.get("igualar_ruido", True) else res
