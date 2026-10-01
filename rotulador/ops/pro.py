"""Ferramentas de retoque profissionais.

carimbo        clonagem com fonte (imagem atual ou ORIGINAL), escala, rotação e modo de mescla
recuperacao    pincel de recuperação: textura da fonte, cor/iluminação do destino (Poisson)
remendo        ferramenta Remendo: preenche a seleção com outra área, mesclada (Poisson)
retoque        pincel de desfocar, nitidez, borrar, clarear, escurecer, saturar, dessaturar
forma          retângulo, elipse ou linha (preenchimento e contorno)

Fonte da clonagem: o ponto ``origem`` (Alt+clique) corresponde ao ``inicio`` do
traço; cada ponto q do destino copia de  origem + R(rotacao)·escala·(q − inicio).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado
from .base import alpha_selecao, caixa, compor, cor, exigir_selecao, num, pontos, recortar_selecao, traco

MODOS_MESCLA = ("normal", "escurecer", "clarear", "multiplicar", "tela")


# ---------------------------------------------------------------------------
# apoio
# ---------------------------------------------------------------------------

def poisson(destino: np.ndarray, fonte: np.ndarray, mascara: np.ndarray) -> np.ndarray:
    """Mescla Poisson (gradientes da fonte, borda do destino) só dentro de ``mascara``."""
    if not mascara.any():
        return destino.copy()
    pad = 4
    d = cv2.copyMakeBorder(cv2.cvtColor(destino, cv2.COLOR_RGB2BGR), pad, pad, pad, pad, cv2.BORDER_REFLECT)
    f = cv2.copyMakeBorder(cv2.cvtColor(fonte, cv2.COLOR_RGB2BGR), pad, pad, pad, pad, cv2.BORDER_REFLECT)
    # o seamlessClone mantém do destino a 1ª linha de pixels DENTRO da máscara (borda fixa):
    # resolve numa máscara 2 px maior e só a área pedida é usada no fim
    m = cv2.dilate(np.pad(mascara, pad).astype(np.uint8) * 255, np.ones((5, 5), np.uint8))
    m[:2], m[-2:], m[:, :2], m[:, -2:] = 0, 0, 0, 0
    x, y, w, h = cv2.boundingRect(m)
    # o OpenCV centraliza a caixa da máscara em ``centro``: escolhido para cair no mesmo lugar
    centro = (x + w // 2, y + h // 2)
    out = cv2.seamlessClone(f, d, m, centro, cv2.NORMAL_CLONE)
    out = cv2.cvtColor(out[pad:-pad, pad:-pad], cv2.COLOR_BGR2RGB)
    return np.where(mascara[..., None], out, destino)


def mesclar(destino: np.ndarray, fonte: np.ndarray, modo: str) -> np.ndarray:
    a, b = destino.astype(np.float32), fonte.astype(np.float32)
    if modo == "escurecer":
        r = np.minimum(a, b)
    elif modo == "clarear":
        r = np.maximum(a, b)
    elif modo == "multiplicar":
        r = a * b / 255.0
    elif modo == "tela":
        r = 255.0 - (255.0 - a) * (255.0 - b) / 255.0
    else:
        r = b
    return np.clip(r + 0.5, 0, 255).astype(np.uint8)


def amostrar_fonte(img: np.ndarray, bbox, p: dict):
    """Imagem de onde o traço copia, já transformada para a caixa ``bbox`` do destino."""
    y0, y1, x0, x1 = bbox
    H, W = img.shape[:2]
    if "origem_x" in p:
        ox, oy = num(p, "origem_x", 0, -W, 2 * W), num(p, "origem_y", 0, -H, 2 * H)
        ix, iy = num(p, "inicio_x", 0, -W, 2 * W), num(p, "inicio_y", 0, -H, 2 * H)
    else:  # compatível com o formato antigo (deslocamento puro)
        ix = iy = 0.0
        ox, oy = num(p, "dx", 0, -W, W), num(p, "dy", 0, -H, H)
    if ox == ix and oy == iy:
        raise ErroOperacao("defina a origem (Alt+clique) longe do ponto onde vai pintar")
    esc = num(p, "escala", 100, 10, 400) / 100.0
    ang = math.radians(num(p, "rotacao", 0, -180, 180))
    A = esc * np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    # destino (centro de pixel do recorte) -> fonte (centro de pixel do cv2)
    t = A @ np.array([x0 + 0.5 - ix, y0 + 0.5 - iy]) + np.array([ox - 0.5, oy - 0.5])
    M = np.hstack([A, t[:, None]])
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    fonte = cv2.warpAffine(img, M, (x1 - x0, y1 - y0), flags=flags, borderMode=cv2.BORDER_REFLECT)
    valido = cv2.warpAffine(np.ones(img.shape[:2], np.float32), M, (x1 - x0, y1 - y0), flags=flags,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return fonte, (valido > 0.999).astype(np.float32)


def _traco_e_alpha(img, p, sel, tam_padrao=20, dur_padrao=0.8):
    H, W = img.shape[:2]
    y0, y1, x0, x1, alpha = traco(pontos(p), num(p, "tamanho", tam_padrao, 1, 1000),
                                  num(p, "dureza", dur_padrao, 0, 1), H, W)
    alpha = recortar_selecao(alpha, (y0, y1, x0, x1), sel) * num(p, "opacidade", 1.0, 0, 1)
    return (y0, y1, x0, x1), alpha


# ---------------------------------------------------------------------------
# ferramentas
# ---------------------------------------------------------------------------

def carimbo(img: np.ndarray, p: dict, sel: Selecao, base: np.ndarray = None, **_) -> Resultado:
    bbox, alpha = _traco_e_alpha(img, p, sel)
    y0, y1, x0, x1 = bbox
    origem_img = base if (p.get("fonte") == "original" and base is not None) else img
    fonte, valido = amostrar_fonte(origem_img, bbox, p)
    alpha = alpha * valido
    antes = img[y0:y1, x0:x1]
    modo = p.get("modo_mescla", "normal")
    if modo not in MODOS_MESCLA:
        raise ErroOperacao(f"modo de mescla inválido: {modo}")
    novo = compor(antes, mesclar(antes, fonte, modo), alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0, meta={"fonte": p.get("fonte", "atual")})


def recuperacao(img: np.ndarray, p: dict, sel: Selecao, base: np.ndarray = None, **_) -> Resultado:
    """Pincel de recuperação: copia a textura da fonte e casa cor/luz com o entorno do destino."""
    H, W = img.shape[:2]
    bbox0, alpha0 = _traco_e_alpha(img, p, sel, dur_padrao=1.0)
    m = 6  # margem para a borda do Poisson
    y0, y1, x0, x1 = caixa(bbox0[0] - m, bbox0[1] + m, bbox0[2] - m, bbox0[3] + m, H, W)
    alpha = np.zeros((y1 - y0, x1 - x0), np.float32)
    alpha[bbox0[0] - y0:bbox0[1] - y0, bbox0[2] - x0:bbox0[3] - x0] = alpha0
    origem_img = base if (p.get("fonte") == "original" and base is not None) else img
    fonte, valido = amostrar_fonte(origem_img, (y0, y1, x0, x1), p)
    alpha *= valido
    antes = img[y0:y1, x0:x1]
    mesclado = poisson(antes, fonte, alpha > 0.5)
    novo = compor(antes, mesclado, alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0, meta={"mescla": "poisson"})


def remendo(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    """Remendo: 'origem' = a seleção recebe a textura da área arrastada;
    'destino' = a seleção é copiada para onde foi arrastada. Mescla Poisson."""
    exigir_selecao(sel)
    H, W = img.shape[:2]
    dx, dy = int(round(num(p, "dx", 0, -W, W))), int(round(num(p, "dy", 0, -H, H)))
    if dx == 0 and dy == 0:
        raise ErroOperacao("arraste a seleção até a área que servirá de remendo")
    modo = p.get("modo", "origem")
    sy0, sy1, sx0, sx1 = sel.bbox
    if modo == "origem":
        alvo = (sy0, sy1, sx0, sx1)
        fy0, fx0 = sy0 + dy, sx0 + dx
    elif modo == "destino":
        alvo = (sy0 + dy, sy1 + dy, sx0 + dx, sx1 + dx)
        fy0, fx0 = sy0, sx0
    else:
        raise ErroOperacao(f"modo inválido: {modo}")
    ty0, ty1, tx0, tx1 = alvo
    if not (0 <= fy0 and fy0 + (sy1 - sy0) <= H and 0 <= fx0 and fx0 + (sx1 - sx0) <= W
            and 0 <= ty0 and ty1 <= H and 0 <= tx0 and tx1 <= W):
        raise ErroOperacao("o remendo saiu da imagem")
    fonte = img[fy0:fy0 + (sy1 - sy0), fx0:fx0 + (sx1 - sx0)]
    antes = img[ty0:ty1, tx0:tx1]
    alpha = sel.alpha
    if p.get("mesclar", True):
        resultado = poisson(antes, fonte, alpha > 0.5)
    else:
        resultado = fonte
    novo = compor(antes, resultado, alpha)
    return Resultado(ty0, ty1, tx0, tx1, novo, alpha > 0, meta={"modo": modo, "dx": dx, "dy": dy})


def retoque(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    H, W = img.shape[:2]
    modo = p.get("modo", "desfocar")
    forca = num(p, "forca", 0.5, 0.01, 1)
    tamanho = num(p, "tamanho", 20, 1, 1000)
    (y0, y1, x0, x1), alpha = _traco_e_alpha(img, p, sel, dur_padrao=0.6)
    m = int(math.ceil(tamanho)) + 4
    py0, py1, px0, px1 = max(0, y0 - m), min(H, y1 + m), max(0, x0 - m), min(W, x1 + m)
    rec = img[py0:py1, px0:px1]
    f = rec.astype(np.float32)
    if modo == "desfocar":
        out = cv2.GaussianBlur(f, (0, 0), 0.5 + 3.5 * forca)
    elif modo == "nitidez":
        out = f + (f - cv2.GaussianBlur(f, (0, 0), 1.0)) * (2.5 * forca)
    elif modo == "clarear":
        out = 255 - (255 - f) * (1 - 0.6 * forca)
    elif modo == "escurecer":
        out = f * (1 - 0.6 * forca)
    elif modo in ("saturar", "dessaturar"):
        hsv = cv2.cvtColor(rec, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
        hsv[..., 1] *= (1 + forca) if modo == "saturar" else (1 - forca)
        out = cv2.cvtColor(np.clip(hsv + 0.5, 0, 255).astype(np.uint8), cv2.COLOR_HSV2RGB_FULL).astype(np.float32)
    elif modo == "borrar":
        out = _borrar(f, pontos(p) - [px0, py0], tamanho, forca)
    else:
        raise ErroOperacao(f"modo de retoque inválido: {modo}")
    out = np.clip(out + 0.5, 0, 255).astype(np.uint8)[y0 - py0:y1 - py0, x0 - px0:x1 - px0]
    novo = compor(img[y0:y1, x0:x1], out, alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0, meta={"modo": modo})


def _borrar(f: np.ndarray, pts: np.ndarray, tamanho: float, forca: float) -> np.ndarray:
    """Dedo (smudge): arrasta a cor pegada no início do traço, misturando ao longo do caminho."""
    r = max(1, int(round(tamanho / 2)))
    trab = cv2.copyMakeBorder(f, r, r, r, r, cv2.BORDER_REFLECT)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disco = np.clip(r + 0.5 - np.hypot(yy, xx), 0, 1)[..., None].astype(np.float32)
    passo = max(1.0, tamanho * 0.15)
    amostras = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.hypot(*(b - a)) / passo))
        amostras += [a + (b - a) * k / n for k in range(1, n + 1)]
    carregada = None
    for q in amostras:
        cx, cy = int(round(q[0])) + r, int(round(q[1])) + r
        if not (r <= cx < trab.shape[1] - r and r <= cy < trab.shape[0] - r):
            continue
        reg = trab[cy - r:cy + r + 1, cx - r:cx + r + 1]
        if carregada is None:
            carregada = reg.copy()
            continue
        novo = reg * (1 - disco * forca) + carregada * (disco * forca)
        trab[cy - r:cy + r + 1, cx - r:cx + r + 1] = novo
        carregada = novo
    return trab[r:-r, r:-r]


def forma(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    H, W = img.shape[:2]
    tipo = p.get("tipo", "ret")
    if tipo not in ("ret", "elipse", "linha"):
        raise ErroOperacao(f"forma inválida: {tipo}")
    ax, ay, bx, by = (num(p, k, 0, -W if k[0] == "x" else -H, 2 * W if k[0] == "x" else 2 * H)
                      for k in ("x0", "y0", "x1", "y1"))
    espessura = num(p, "espessura", 2, 0, 200)
    preencher = bool(p.get("preencher", True)) and tipo != "linha"
    if tipo == "linha" and espessura <= 0:
        raise ErroOperacao("a linha precisa de espessura")
    m = espessura + 3
    y0, y1, x0, x1 = caixa(min(ay, by) - m, max(ay, by) + m, min(ax, bx) - m, max(ax, bx) + m, H, W)
    S = 4

    def q(x, y):
        return int(round(((x - x0) * S - 0.5) * 16)), int(round(((y - y0) * S - 0.5) * 16))

    def tela():
        return np.zeros(((y1 - y0) * S, (x1 - x0) * S), np.uint8)

    def reduzir(t):
        return cv2.resize(t, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA).astype(np.float32) / 255

    esp = max(1, int(round(espessura * S)))
    cheio, borda = tela(), tela()
    if tipo == "linha":
        cv2.line(borda, q(ax, ay), q(bx, by), 255, esp, cv2.LINE_8, shift=4)
    else:
        p1, p2 = q(min(ax, bx), min(ay, by)), q(max(ax, bx), max(ay, by))
        if tipo == "ret":
            if preencher:
                cv2.rectangle(cheio, p1, p2, 255, -1, cv2.LINE_8, shift=4)
            if espessura > 0:
                cv2.rectangle(borda, p1, p2, 255, esp, cv2.LINE_8, shift=4)
        else:
            c = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)
            eixos = (abs(p2[0] - p1[0]) // 2, abs(p2[1] - p1[1]) // 2)
            if preencher:
                cv2.ellipse(cheio, c, eixos, 0, 0, 360, 255, -1, cv2.LINE_8, shift=4)
            if espessura > 0:
                cv2.ellipse(borda, c, eixos, 0, 0, 360, 255, esp, cv2.LINE_8, shift=4)
    a_cheio = reduzir(cheio) * num(p, "opacidade", 1, 0, 1)
    a_borda = reduzir(borda) * num(p, "opacidade", 1, 0, 1)
    if not sel.vazia and p.get("limitar_selecao"):
        s = alpha_selecao(sel, (y0, y1, x0, x1))
        a_cheio, a_borda = a_cheio * s, a_borda * s
    novo = compor(img[y0:y1, x0:x1], cor(p), a_cheio)
    novo = compor(novo, cor(p, "cor_contorno", (0, 0, 0)), a_borda)
    pegada = (a_cheio > 0) | (a_borda > 0)
    if not pegada.any():
        raise ErroOperacao("a forma ficou vazia")
    return Resultado(y0, y1, x0, x1, novo, pegada, meta={"tipo": tipo})
