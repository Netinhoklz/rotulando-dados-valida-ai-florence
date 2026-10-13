"""Letras (glifos) de um campo: onde está cada caractere do texto lido pelo OCR.

O OCR devolve só a caixa da LINHA. Para editar uma letra sozinha é preciso saber a coluna
de cada caractere. O texto é desenhado na fonte do documento (ou na Arial, que tem
proporções parecidas com as fontes de contas): a largura de cada caractere na fonte dá a
posição prevista do corte entre letras, mapeada para a tinta de cada palavra. O corte
final cai na coluna com MENOS tinta perto da posição prevista. Isso separa letras
coladas (que viram um componente só) e mantém juntos acentos e pingos (que são
componentes separados).

Também aqui: inventário de letras de um documento (para copiar um glifo de outro lugar),
cor da tinta e do papel, alfa suave de um glifo e a estimativa de desfoque do scan.
"""

from __future__ import annotations

import math
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import ImageFont

from . import ocr
from .selecao import mascara_tinta
from .sessao import ErroOperacao

# letras que descem abaixo da linha de base (não servem para alinhar pelo pé)
DESCENDENTES = set("gjpqyQ,;()[]{}|@$")


@dataclass
class Glifo:
    i: int  # índice do caractere no texto do campo
    ch: str
    x0: int
    y0: int
    x1: int
    y1: int
    tinta: np.ndarray = field(repr=False)  # bool (y1-y0, x1-x0): a tinta só desta letra

    @property
    def caixa(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.x1, self.y1

    @property
    def largura(self) -> int:
        return self.x1 - self.x0

    @property
    def altura(self) -> int:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    def mascara_imagem(self, H: int, W: int) -> np.ndarray:
        m = np.zeros((H, W), bool)
        m[self.y0:self.y1, self.x0:self.x1] = self.tinta
        return m


# ---------------------------------------------------------------------------- fontes
_fontes_cache: dict = {}


def fonte_pil(nome: str | None, tamanho: float = 100.0) -> ImageFont.FreeTypeFont:
    from .ops import texto  # import tardio: ops importa sessao, que é leve, mas evita ciclo no pacote

    fontes = texto.listar_fontes()
    if not nome or nome not in fontes:
        nome = texto.fonte_padrao()
    chave = (nome, round(float(tamanho), 2))
    f = _fontes_cache.get(chave)
    if f is None:
        caminho, idx = fontes[nome]
        f = ImageFont.truetype(caminho, tamanho, index=idx)
        if len(_fontes_cache) > 300:
            _fontes_cache.clear()
        _fontes_cache[chave] = f
    return f


def faixas_previstas(texto_: str, fonte: ImageFont.FreeTypeFont) -> list[tuple[int, float, float]]:
    """(índice, x0, x1) da tinta de cada caractere não branco, em pixels da ``fonte``."""
    out = []
    for k, c in enumerate(texto_):
        if c.isspace():
            continue
        adv = fonte.getlength(texto_[:k])
        l, _t, r, _b = fonte.getbbox(c, anchor="ls")
        if r <= l:
            r = l + 1
        out.append((k, adv + l, adv + r))
    return out


def _blocos(colunas: np.ndarray, vao_min: int) -> list[tuple[int, int]]:
    """Intervalos [a, b) de colunas com tinta separados por vãos >= vao_min."""
    blocos, ini, vazio = [], None, 0
    for i, c in enumerate(colunas):
        if c:
            if ini is None:
                ini = i
            vazio = 0
        elif ini is not None:
            vazio += 1
            if vazio >= vao_min:
                blocos.append((ini, i - vazio + 1))
                ini, vazio = None, 0
    if ini is not None:
        blocos.append((ini, len(colunas) - vazio))
    return blocos


def _palavras(texto_: str) -> list[list[int]]:
    """Índices dos caracteres de cada palavra (separadas por espaço)."""
    palavras, atual = [], []
    for k, c in enumerate(texto_):
        if c.isspace():
            if atual:
                palavras.append(atual)
                atual = []
        else:
            atual.append(k)
    if atual:
        palavras.append(atual)
    return palavras


def _limpar_pontos(tinta: np.ndarray, altura: int) -> np.ndarray:
    """Tira pontinhos de grão/sujeira (menores que um ponto final)."""
    n, rot, stats, _ = cv2.connectedComponentsWithStats(tinta.astype(np.uint8), connectivity=8)
    if n <= 1:
        return tinta
    minimo = max(3, int(0.004 * altura * altura))
    manter = np.zeros(n, bool)
    manter[1:] = stats[1:, cv2.CC_STAT_AREA] >= minimo
    return manter[rot]


def segmentar(img: np.ndarray, caixa, texto_: str, fonte: str | None = None, margem: float = 0.3) -> list[Glifo]:
    """Glifos (um por caractere não branco de ``texto_``) da linha na ``caixa`` (caixa da tinta).

    ``margem``: faixa (fração da altura) acima e abaixo da caixa onde acento e cedilha soltos ainda entram na letra;
    numa caixa sem os acentos (ocr.caixa_da_linha), o agudo da Tahoma sobe ~0,32 da altura e com 0,3 ficava de fora.
    Levanta ErroOperacao se a tinta não bate com o texto (OCR errado, letras ilegíveis)."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in caixa)
    h = max(1, y1 - y0)
    m = max(2, int(round(margem * h)))
    rx0, ry0, rx1, ry1 = max(0, x0 - 1), max(0, y0 - m), min(W, x1 + 1), min(H, y1 + m)
    rec = img[ry0:ry1, rx0:rx1]
    if rec.size == 0:
        raise ErroOperacao("campo fora da imagem")
    tinta = _na_linha(_limpar_pontos(mascara_tinta(rec).astype(bool), h), y0 - ry0, y1 - ry0, h)
    perfil = tinta.sum(0)
    if not perfil.any():
        raise ErroOperacao("não há tinta no campo")
    chars = [(k, c) for k, c in enumerate(texto_) if not c.isspace()]
    if not chars:
        raise ErroOperacao("campo sem texto")
    f = fonte_pil(fonte)
    prev = {k: (a, b) for k, a, b in faixas_previstas(texto_, f)}
    alt_tinta = int(tinta.any(1).sum())
    blocos = _blocos(perfil > 0, max(2, int(round(0.33 * alt_tinta))))
    palavras = _palavras(texto_)

    # mapeia a previsão (pixels da fonte) para colunas do recorte: palavra a palavra quando a
    # tinta mostra o mesmo número de palavras do texto, senão a linha inteira de uma vez
    mapa: dict[int, tuple[float, float]] = {}
    grupos = list(zip(palavras, blocos)) if len(blocos) == len(palavras) else [
        ([k for p in palavras for k in p], (blocos[0][0], blocos[-1][1]))]
    for ks, (ba, bb) in grupos:
        pa = min(prev[k][0] for k in ks)
        pb = max(prev[k][1] for k in ks)
        escala = (bb - ba) / max(pb - pa, 1e-6)
        for k in ks:
            mapa[k] = (ba + (prev[k][0] - pa) * escala, ba + (prev[k][1] - pa) * escala)

    # cortes entre letras: coluna de menos tinta perto do corte previsto
    cortes: dict[int, int] = {}
    passo = np.mean([mapa[k][1] - mapa[k][0] for k, _ in chars]) or 1.0
    for ks, (ba, bb) in grupos:
        for j in range(len(ks) - 1):
            a, b = ks[j], ks[j + 1]
            previsto = (mapa[a][1] + mapa[b][0]) / 2
            jan = max(1.0, 0.35 * passo)
            lo, hi = int(np.floor(previsto - jan)), int(np.ceil(previsto + jan)) + 1
            lo, hi = max(int(ba), lo), min(int(bb), hi)
            if hi <= lo:
                cortes[a] = int(round(previsto))
                continue
            janela = perfil[lo:hi]
            menor = janela.min()
            cand = np.flatnonzero(janela == menor) + lo
            cortes[a] = int(cand[np.argmin(np.abs(cand + 0.5 - previsto))]) + (1 if menor == 0 else 0)
        cortes[ks[-1]] = int(bb)

    glifos = []
    inicio = {}
    for ks, (ba, _bb) in grupos:
        ant = int(ba)
        for k in ks:
            inicio[k] = ant
            ant = cortes[k]
    for k, c in chars:
        ca, cb = inicio[k], cortes[k]
        if cb <= ca:
            raise ErroOperacao(f"não consegui separar a letra '{c}'")
        pedaco = tinta[:, ca:cb]
        ys = np.flatnonzero(pedaco.any(1))
        xs = np.flatnonzero(pedaco.any(0))
        if not len(ys):
            raise ErroOperacao(f"a letra '{c}' não tem tinta (o OCR leu algo que não está lá?)")
        prevista = mapa[k][1] - mapa[k][0]
        largura = xs[-1] + 1 - xs[0]
        if not 0.3 <= largura / max(prevista, 1.0) <= 3.0 and largura > 3:
            raise ErroOperacao("a tinta não bate com o texto lido (letras coladas ou OCR errado)")
        gx0, gx1 = rx0 + ca + int(xs[0]), rx0 + ca + int(xs[-1]) + 1
        gy0, gy1 = ry0 + int(ys[0]), ry0 + int(ys[-1]) + 1
        glifos.append(Glifo(k, c, gx0, gy0, gx1, gy1,
                            np.ascontiguousarray(pedaco[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1])))
    return glifos


def com_sinais(img: np.ndarray, gs: list[Glifo]) -> list[Glifo]:
    """As letras com o acento, o til e a cedilha soltos que a margem de ``segmentar`` deixou de fora (o agudo da Tahoma
    sobe ~0,32 da altura acima da maiúscula, e a margem é 0,3): pedaço pequeno de tinta, sem encostar na borda da busca,
    logo acima do corpo de uma letra (até 0,45 da altura da linha) com o centro dentro dela, ou logo abaixo de um C
    (até 0,35). Sem isso, quem apaga uma letra deixava o acento dela ("ARAÚJO" → "SIMÓES") e quem move não o levava."""
    if not gs:
        return gs
    H, W = img.shape[:2]
    hl = float(np.median([g.altura for g in gs]))
    my, mx = int(round(0.8 * hl)) + 2, int(round(0.5 * hl)) + 2
    X0, Y0 = max(0, min(g.x0 for g in gs) - mx), max(0, min(g.y0 for g in gs) - my)
    X1, Y1 = min(W, max(g.x1 for g in gs) + mx), min(H, max(g.y1 for g in gs) + my)
    tinta = mascara_tinta(img[Y0:Y1, X0:X1]).astype(bool)
    dono = np.zeros(tinta.shape, bool)
    for g in gs:
        dono[g.y0 - Y0:g.y1 - Y0, g.x0 - X0:g.x1 - X0] |= g.tinta
    outra = tinta & ~cv2.dilate(dono.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    n, lab, est, _ = cv2.connectedComponentsWithStats(outra.astype(np.uint8), connectivity=8)
    extra: dict[int, np.ndarray] = {}
    hh, ww = outra.shape
    for i in range(1, n):
        cx, cy, cw, ch, area = (int(v) for v in est[i])
        if cx == 0 or cy == 0 or cx + cw >= ww or cy + ch >= hh or area < 2:
            continue  # encosta na borda da busca: é outra linha, rótulo, fio
        if ch > 0.45 * hl or cw > 0.9 * hl:
            continue
        centro = X0 + cx + cw / 2
        topo, pe = Y0 + cy, Y0 + cy + ch
        for j, g in enumerate(gs):
            if not g.x0 <= centro < g.x1:
                continue
            acima = pe <= g.y0 and g.y0 - pe <= 0.45 * hl
            abaixo = topo >= g.y1 and topo - g.y1 <= 0.35 * hl and decompor(g.ch)[0] in "Cc"
            if acima or abaixo:
                extra.setdefault(j, np.zeros(outra.shape, bool))
                extra[j] |= lab == i
            break
    if not extra:
        return gs
    out = list(gs)
    for j, m in extra.items():
        g = gs[j]
        m = m.copy()
        m[g.y0 - Y0:g.y1 - Y0, g.x0 - X0:g.x1 - X0] |= g.tinta
        ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
        out[j] = Glifo(g.i, g.ch, X0 + int(xs[0]), Y0 + int(ys[0]), X0 + int(xs[-1]) + 1, Y0 + int(ys[-1]) + 1,
                       np.ascontiguousarray(m[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]))
    return out


def _na_linha(tinta: np.ndarray, ya: int, yb: int, h: int) -> np.ndarray:
    """Só a tinta da linha: componentes que tocam as linhas [ya, yb) do recorte, mais os
    pequenos soltos na margem (acentos, cedilha) que não encostam na borda do recorte. Texto
    da linha de cima/de baixo que entrou na margem fica de fora."""
    n, rot, stats, _ = cv2.connectedComponentsWithStats(tinta.astype(np.uint8), connectivity=8)
    if n <= 1:
        return tinta
    manter = np.zeros(n, bool)
    manter[np.unique(rot[ya:yb])] = True
    topo, base = stats[:, cv2.CC_STAT_TOP], stats[:, cv2.CC_STAT_TOP] + stats[:, cv2.CC_STAT_HEIGHT]
    pequeno = stats[:, cv2.CC_STAT_AREA] < 0.06 * h * h
    solto = (topo > 0) & (base < tinta.shape[0])
    manter |= pequeno & solto
    manter[0] = False
    return manter[rot]


# ---------------------------------------------------------------------------- cores e alfa
def cor_papel(img: np.ndarray, caixa, margem: int | None = None) -> np.ndarray:
    """Cor do papel em volta da caixa (mediana do anel sem tinta)."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in caixa)
    m = margem if margem is not None else max(3, int(0.3 * (y1 - y0)))
    rx0, ry0, rx1, ry1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
    rec = img[ry0:ry1, rx0:rx1].reshape(-1, 3).astype(np.float32)
    tinta = mascara_tinta(img[ry0:ry1, rx0:rx1]).astype(bool).ravel()
    dentro = np.zeros((ry1 - ry0, rx1 - rx0), bool)
    dentro[y0 - ry0:y1 - ry0, x0 - rx0:x1 - rx0] = True
    sel = ~tinta & ~dentro.ravel()
    if sel.sum() < 10:
        sel = ~tinta
    if sel.sum() < 10:
        return np.median(rec, axis=0)
    return np.median(rec[sel], axis=0)


def cor_tinta(img: np.ndarray, glifo_ou_mascara, caixa=None) -> np.ndarray:
    """Cor do núcleo dos traços: mediana da metade mais distante do papel."""
    if isinstance(glifo_ou_mascara, Glifo):
        g = glifo_ou_mascara
        rec = img[g.y0:g.y1, g.x0:g.x1].astype(np.float32)
        m = g.tinta
        papel = cor_papel(img, g.caixa)
    else:
        x0, y0, x1, y1 = caixa
        rec = img[y0:y1, x0:x1].astype(np.float32)
        m = glifo_ou_mascara
        papel = cor_papel(img, caixa)
    px = rec[m]
    if not len(px):
        return np.array([0, 0, 0], np.float32)
    return _nucleo(px, papel)


def _nucleo(px: np.ndarray, papel: np.ndarray) -> np.ndarray:
    d = np.abs(px - papel).sum(1)
    forte = px[d >= np.percentile(d, 50)]
    return np.median(forte, axis=0)


_LUMA = np.float32([0.299, 0.587, 0.114])


def calibrar_cor(alfa: np.ndarray, papel, alvo) -> np.ndarray:
    """Cor a compor com ``alfa`` (o traço novo, já desfocado como o scan) para o núcleo dele, medido como em
    ``cor_tinta``, sair tão escuro quanto ``alvo`` (o ``cor_tinta`` das letras vizinhas). Compor com a própria
    ``alvo`` deixava a letra nova mais clara que as vizinhas (luma 28 x 13 no Times, 53 x 37 numa foto): o traço
    fino desfocado não chega a alfa 1, e o núcleo medido cai na rampa do antialias. A correção é na luma (o tom
    fica) e limitada ao preto."""
    papel, alvo = np.asarray(papel, np.float32), np.asarray(alvo, np.float32)
    a = np.pad(alfa if alfa.ndim == 2 else alfa.mean(-1), 3).astype(np.float32)[..., None]
    cor = alvo.copy()
    for _ in range(2):  # composição linear no alfa: um passo acerta; o segundo corrige a máscara que mudou
        comp = papel + (cor - papel) * a
        m = mascara_tinta(np.clip(comp + 0.5, 0, 255).astype(np.uint8)).astype(bool)
        if not m.any():
            break
        ln, la, lp = float(_nucleo(comp[m], papel) @ _LUMA), float(alvo @ _LUMA), float(papel @ _LUMA)
        if abs(ln - lp) < 1.0:
            break
        cor = np.clip(papel + (cor - papel) * (la - lp) / (ln - lp), 0, 255)
    return cor


def alfa(rec: np.ndarray, papel: np.ndarray, tinta: np.ndarray) -> np.ndarray:
    """Alfa suave (0..1) de cada pixel: quanto ele é tinta, projetando a cor na reta papel->tinta."""
    v = (tinta - papel).astype(np.float32)
    n2 = float(v @ v)
    if n2 < 1:
        return np.zeros(rec.shape[:2], np.float32)
    a = ((rec.astype(np.float32) - papel) @ v) / n2
    return np.clip(a, 0, 1).astype(np.float32)


def alfa_glifo(img: np.ndarray, g: Glifo, margem: int = 2) -> tuple[np.ndarray, np.ndarray, tuple]:
    """(recorte, alfa, caixa) de um glifo com margem: alfa suave limitado à tinta DESTA letra
    (dilatada 1 px para levar a borda antisserrilhada, sem pegar a letra vizinha)."""
    H, W = img.shape[:2]
    x0, y0 = max(0, g.x0 - margem), max(0, g.y0 - margem)
    x1, y1 = min(W, g.x1 + margem), min(H, g.y1 + margem)
    rec = img[y0:y1, x0:x1]
    papel = cor_papel(img, g.caixa)
    a = alfa(rec, papel, cor_tinta(img, g))
    dono = np.zeros((y1 - y0, x1 - x0), np.uint8)
    dono[g.y0 - y0:g.y1 - y0, g.x0 - x0:g.x1 - x0] = g.tinta
    dono = cv2.dilate(dono, np.ones((3, 3), np.uint8)).astype(bool)
    return rec, a * dono, (x0, y0, x1, y1)


# ---------------------------------------------------------------------------- forma
_mascaras: dict = {}


def mascara_caractere(c: str, fonte: str | None, negrito: float = 0.0) -> np.ndarray | None:
    """Tinta (bool) de um caractere desenhado na fonte, recortada justa (tamanho 100). Em cache (não altere)."""
    from .ops.auto import _mascara_texto

    from .ops import texto
    nome = fonte if fonte in texto.listar_fontes() else texto.fonte_padrao()
    chave = (c, nome, round(float(negrito), 2))
    if chave not in _mascaras:
        if len(_mascaras) > 4000:
            _mascaras.clear()
        _mascaras[chave], _ = _mascara_texto(nome, c, 100.0, negrito)
    return _mascaras[chave]


def semelhanca(tinta: np.ndarray, c: str, fonte: str | None, negrito: float = 0.0) -> float:
    """IoU entre a tinta de um glifo e o caractere ``c`` desenhado na fonte, na mesma caixa."""
    ref = mascara_caractere(c, fonte, negrito)
    if ref is None or not tinta.any():
        return 0.0
    h, w = tinta.shape
    r = cv2.resize(ref.astype(np.uint8) * 255, (max(1, w), max(1, h)), interpolation=cv2.INTER_AREA) > 127
    return float((r & tinta).sum() / max(1, (r | tinta).sum()))


# ---------------------------------------------------------------------------- desfoque do scan
def estimar_desfoque(img: np.ndarray, caixa, alfa_renderizado: np.ndarray | None = None) -> float:
    """Sigma (px) do desfoque que o texto renderizado precisa para ficar tão macio quanto a
    tinta do documento. Mede a nitidez das bordas pela razão Σ|∇|²/Σ|∇| (inversa à largura
    da borda, independe do alinhamento) e compara com a do texto renderizado desfocado."""
    x0, y0, x1, y1 = (int(v) for v in caixa)
    H, W = img.shape[:2]
    m = 3
    rec = img[max(0, y0 - m):min(H, y1 + m), max(0, x0 - m):min(W, x1 + m)].astype(np.float32)
    if rec.size == 0:
        return 0.0
    lum = rec @ np.float32([0.299, 0.587, 0.114])
    tinta = mascara_tinta(rec.astype(np.uint8)).astype(bool)
    if tinta.sum() < 10 or (~tinta).sum() < 10:
        return 0.0
    papel, nucleo = np.median(lum[~tinta]), np.percentile(lum[tinta], 25 if lum[tinta].mean() < np.median(lum[~tinta]) else 75)
    contraste = papel - nucleo
    if abs(contraste) < 20:
        return 0.0
    n = (papel - lum) / contraste
    r_doc = _razao_borda(n)
    if alfa_renderizado is None:
        return 0.0
    melhor, dist = 0.0, 1e9
    for s in (0.0, 0.3, 0.5, 0.7, 0.9, 1.1, 1.4, 1.8, 2.3):
        a = cv2.GaussianBlur(alfa_renderizado, (0, 0), s) if s > 0 else alfa_renderizado
        d = abs(_razao_borda(a) - r_doc)
        if d < dist:
            melhor, dist = s, d
    return melhor


def nitidez_tinta(img: np.ndarray, caixa) -> float:
    """Nitidez das bordas da tinta na ``caixa`` (a razão de ``estimar_desfoque`` na luma normalizada papel→tinta):
    alta = nítida, baixa = borrada, sem depender do contraste. 0 quando não há tinta e papel para medir."""
    x0, y0, x1, y1 = (int(v) for v in caixa)
    rec = img[max(0, y0):y1, max(0, x0):x1]
    if rec.size == 0:
        return 0.0
    lum = rec.astype(np.float32) @ _LUMA
    tinta = mascara_tinta(rec).astype(bool)
    if tinta.sum() < 10 or (~tinta).sum() < 10:
        return 0.0
    papel, nucleo = float(np.median(lum[~tinta])), float(np.percentile(lum[tinta], 25))
    if abs(papel - nucleo) < 20:
        return 0.0
    return _razao_borda((papel - lum) / (papel - nucleo))


def _razao_borda(n: np.ndarray) -> float:
    gy, gx = np.gradient(n.astype(np.float32))
    g = np.hypot(gx, gy)
    borda = g > 0.08
    if borda.sum() < 5:
        return 0.0
    v = g[borda]
    return float((v * v).sum() / v.sum())


# ---------------------------------------------------------------------------- estilo de uma letra
def peso_traco(a: np.ndarray) -> float:
    """Espessura média do traço (px) pelo alfa suave (0..1) de uma letra: 2·Σα / Σ|∇α|, o 2·área/perímetro de uma
    máscara sem depender do limiar (o desfoque espalha a borda, mas não muda a soma nem o comprimento do contorno).
    Negrito dá ~1,5-2x o regular do mesmo tamanho."""
    a = np.asarray(a, np.float32)
    gy, gx = np.gradient(a)
    borda = float(np.hypot(gx, gy).sum())
    return 2.0 * float(a.sum()) / borda if borda > 1e-6 else 0.0


def nitidez_alfa(a: np.ndarray) -> float:
    """Nitidez da borda de um alfa (0..1): a razão Σ|∇|²/Σ|∇| de ``estimar_desfoque`` (alta = nítida)."""
    return _razao_borda(np.asarray(a, np.float32))


def isolado(img: np.ndarray, g: Glifo) -> bool:
    """A letra não encosta em outra tinta dos lados: a segmentação não precisou cortar um traço da vizinha (AV, MA
    com kerning, serifas que se tocam), então a letra recortada sai inteira e só ela."""
    H, W = img.shape[:2]
    k = max(3, g.altura // 2)
    x0, y0, x1, y1 = max(0, g.x0 - k), max(0, g.y0 - 3), min(W, g.x1 + k), min(H, g.y1 + 3)
    m = mascara_tinta(img[y0:y1, x0:x1]).astype(bool)[g.y0 - y0:g.y1 - y0]
    esq, dir_ = g.x0 - 1 - x0, g.x1 - x0
    return not ((0 <= esq < m.shape[1] and m[:, esq].any()) or (0 <= dir_ < m.shape[1] and m[:, dir_].any()))


# sinais (acentos) como o NFD escreve: "Ã" = "A" + til combinante
CEDILHA = "\u0327"
SINAIS = {"\u0301": "agudo", "\u0300": "grave", "\u0302": "circunflexo", "\u0303": "til", "\u0308": "trema",
          CEDILHA: "cedilha"}


def decompor(ch: str) -> tuple[str, str | None]:
    """(letra base, sinal combinante ou None): "Ã" -> ("A", "\\u0303"), "Ç" -> ("C", "\\u0327")."""
    d = unicodedata.normalize("NFD", ch)
    if len(d) == 2 and d[1] in SINAIS:
        return d[0], d[1]
    return ch, None


def partes(tinta: np.ndarray) -> tuple[np.ndarray, list[np.ndarray], list[np.ndarray]]:
    """(corpo, acima, abaixo) de uma letra: o corpo (o maior componente e os que dividem as mesmas linhas com ele) e
    as máscaras dos componentes soltos acima (acento, pingo) e abaixo (cedilha). Pontinhos de sujeira saem."""
    m = np.asarray(tinta, np.uint8)
    n, lab, est, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    comps = [i for i in range(1, n) if est[i, cv2.CC_STAT_AREA] >= 2]
    if len(comps) <= 1:
        return m.astype(bool), [], []
    comps.sort(key=lambda i: -est[i, cv2.CC_STAT_AREA])
    corpo = [comps[0]]
    t0, t1 = int(est[comps[0], cv2.CC_STAT_TOP]), int(est[comps[0], cv2.CC_STAT_TOP] + est[comps[0], cv2.CC_STAT_HEIGHT])
    resto, mudou = comps[1:], True
    while mudou:  # pedaço de traço partido (scan gasto) divide as linhas do corpo; acento e cedilha, não
        mudou = False
        for i in list(resto):
            a, b = int(est[i, cv2.CC_STAT_TOP]), int(est[i, cv2.CC_STAT_TOP] + est[i, cv2.CC_STAT_HEIGHT])
            if min(b, t1) - max(a, t0) > 0.3 * min(b - a, t1 - t0):
                corpo.append(i)
                resto.remove(i)
                t0, t1, mudou = min(t0, a), max(t1, b), True
    acima = [lab == i for i in resto if est[i, cv2.CC_STAT_TOP] + est[i, cv2.CC_STAT_HEIGHT] <= (t0 + t1) / 2]
    abaixo = [lab == i for i in resto if est[i, cv2.CC_STAT_TOP] + est[i, cv2.CC_STAT_HEIGHT] > (t0 + t1) / 2]
    return np.isin(lab, corpo), acima, abaixo


_sinais_fonte: dict = {}


def sinal_da_fonte(base: str, sinal: str, fonte: str | None):
    """(máscara do sinal recortada justa, dx, gap) do ``sinal`` sobre ``base`` desenhado na ``fonte`` (tamanho 100):
    dx = centro do sinal − centro do corpo, gap = distância vertical do sinal ao corpo (acima: topo do corpo − pé do
    sinal; abaixo: topo do sinal − pé do corpo). None se a fonte não desenha o sinal solto (cedilha grudada)."""
    chave = (base, sinal, fonte)
    if chave not in _sinais_fonte:
        m = mascara_caractere(unicodedata.normalize("NFC", base + sinal), fonte)
        r = None
        if m is not None:
            corpo, acima, abaixo = partes(m)
            sinais = abaixo if sinal == CEDILHA else acima
            if sinais:
                s = np.logical_or.reduce(sinais)
                r = _relativo(corpo, s, sinal == CEDILHA)
        _sinais_fonte[chave] = r
    return _sinais_fonte[chave]


def _relativo(corpo: np.ndarray, s: np.ndarray, embaixo: bool):
    ys, xs = np.flatnonzero(s.any(1)), np.flatnonzero(s.any(0))
    cys, cxs = np.flatnonzero(corpo.any(1)), np.flatnonzero(corpo.any(0))
    dx = (xs[0] + xs[-1] + 1) / 2 - (cxs[0] + cxs[-1] + 1) / 2
    gap = (ys[0] - (cys[-1] + 1)) if embaixo else (cys[0] - (ys[-1] + 1))
    return s[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1], float(dx), float(gap)


def sinal_do_glifo(tinta: np.ndarray, base: str, fonte: str | None) -> tuple[str | None, tuple | None]:
    """Que sinal a letra recortada tem, comparando o componente solto com os sinais da ``fonte`` sobre ``base`` (o OCR
    lê "JOAO" num "JOÃO": é pela tinta que se sabe que o A tem til). (sinal combinante, (máscara, dx, gap) medidos na
    letra) ou (None, None) sem sinal solto ou sem um parecido."""
    corpo, acima, abaixo = partes(tinta)
    melhor, info = (0.45, None), None
    for sinais, candidatos in ((acima, ("\u0301", "\u0300", "\u0302", "\u0303", "\u0308")), (abaixo, (CEDILHA,))):
        if not sinais:
            continue
        s = np.logical_or.reduce(sinais)
        rel = _relativo(corpo, s, candidatos == (CEDILHA,))
        h, w = rel[0].shape
        if base in "ij" and len(sinais) == 1 and 0.6 <= w / h <= 1.6 and rel[0].mean() >= 0.65:
            continue  # o pingo do i e do j, não um acento
        for sinal in candidatos:
            ref = sinal_da_fonte(base, sinal, fonte)
            if ref is None:
                continue
            rh, rw = ref[0].shape
            if abs(math.log((w / h) / (rw / rh))) > 0.7:
                continue
            r = cv2.resize(ref[0].astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_AREA) > 127
            iou = float((r & rel[0]).sum() / max(1, (r | rel[0]).sum()))
            if iou > melhor[0]:
                melhor, info = (iou, sinal), rel
    return melhor[1] if info is not None else None, info


def linha_de_base(gs: list[Glifo], inclinacao: float = 0.0, ajustar: bool = False) -> tuple[float, float] | None:
    """(y0, b) da linha de base y = y0 + b·x pelo pé das letras e dígitos sem descendente (nem cedilha). ``inclinacao``
    em graus (+ = sobe para a direita); ``ajustar``: a inclinação medida nas próprias letras, se perto da dada (a linha
    de outra altura da foto pode estar um pouco mais torta). None sem letra para medir."""
    pes = [(g.cx, g.y1) for g in gs if g.ch.isalnum() and g.ch not in DESCENDENTES and decompor(g.ch)[1] != CEDILHA]
    if not pes:
        return None
    xs, ys = np.array(pes, np.float64).T
    b = -math.tan(math.radians(inclinacao))
    h = float(np.median([g.altura for g in gs]))
    # numa linha curta ("81.89") o pé inteiro de 4 letras dava 1° de inclinação e a letra copiada saía 1 px fora
    if ajustar and len(pes) >= 6 and np.ptp(xs) > 6 * h:
        bf = float(np.polyfit(xs, ys, 1)[0])
        if abs(bf - b) < 0.035:
            b = bf
    return float(np.median(ys - b * xs)), b


# ---------------------------------------------------------------------------- inventário
class Inventario:
    """Letras de um documento, por caractere, para copiar uma letra de outro lugar.

    As linhas do OCR são separadas em glifos sob demanda (só as de altura parecida com a
    pedida) e guardadas. A segmentação usa a métrica da Arial: aqui não é preciso a fonte
    exata, e cada glifo copiado ainda passa pela verificação de forma na fonte do alvo."""

    def __init__(self, img: np.ndarray, linhas: list[dict]):
        self.img = img
        self.linhas = linhas
        self._seg: dict[int, list[Glifo] | None] = {}
        self._caixas: dict[int, list[int]] = {}
        self.lock = threading.Lock()
        self.memo: dict = {}  # medidas derivadas por linha (estilo, linha de base), reaproveitadas entre edições

    def glifos_da_linha(self, i: int) -> list[Glifo]:
        """Glifos da linha ``i`` do OCR ([] se ela não segmenta)."""
        return self._glifos_linha(i)

    def _glifos_linha(self, i: int) -> list[Glifo]:
        with self.lock:
            if i in self._seg:
                return self._seg[i] or []
        l = self.linhas[i]
        caixa = ocr.caixa_tinta(self.img, l["caixa"], 0)
        try:
            gs = segmentar(self.img, caixa, l.get("texto", ""))
        except ErroOperacao:
            gs = None
        with self.lock:
            self._seg[i] = gs
            self._caixas[i] = caixa
        return gs or []

    def candidatos(self, ch: str, altura: int, tolerancia: float = 0.1, evitar=None) -> list[Glifo]:
        """Glifos do caractere ``ch`` com altura dentro de ``tolerancia`` (fração) de ``altura``.
        ``evitar``: caixa (x0, y0, x1, y1) cujos glifos não servem (o próprio campo)."""
        out = []
        for i, l in enumerate(self.linhas):
            if ch not in l.get("texto", ""):
                continue
            lh = l["caixa"][3] - l["caixa"][1]
            if not 0.6 * altura <= lh <= 2.6 * altura:  # a caixa do OCR é mais alta que a letra
                continue
            for g in self._glifos_linha(i):
                if g.ch != ch or abs(g.altura - altura) > tolerancia * altura:
                    continue
                if evitar is not None and not (g.x1 <= evitar[0] or g.x0 >= evitar[2]
                                                or g.y1 <= evitar[1] or g.y0 >= evitar[3]):
                    continue
                out.append(g)
        return out


_inventarios: OrderedDict[str, Inventario] = OrderedDict()
_inv_lock = threading.Lock()


def inventario(chave: str, img: np.ndarray, linhas: list[dict]) -> Inventario:
    """Inventário em cache por documento (hash dos pixels)."""
    with _inv_lock:
        inv = _inventarios.get(chave)
        if inv is not None:
            _inventarios.move_to_end(chave)
            return inv
        inv = Inventario(img, linhas)
        _inventarios[chave] = inv
        while len(_inventarios) > 12:
            _inventarios.popitem(last=False)
        return inv
