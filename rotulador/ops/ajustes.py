"""Ajustes de imagem aplicados dentro da seleção."""

from __future__ import annotations

import math

import cv2
import numpy as np

from ..selecao import Selecao, mascara_tinta
from ..sessao import ErroOperacao, Resultado
from .base import exigir_selecao, inteiro, num, resultado_selecao

AJUSTES = ("brilho_contraste", "niveis", "matiz_saturacao", "desfoque", "nitidez", "ruido", "jpeg", "cinza",
           "igualar_ruido")


def _u8(a: np.ndarray) -> np.ndarray:
    return np.clip(a + 0.5, 0, 255).astype(np.uint8)


def ajuste(img: np.ndarray, p: dict, sel: Selecao, base: np.ndarray | None = None, **_) -> Resultado:
    exigir_selecao(sel)
    nome = p.get("ajuste")
    if nome not in AJUSTES:
        raise ErroOperacao(f"ajuste desconhecido: {nome}")
    H, W = img.shape[:2]
    y0, y1, x0, x1 = sel.bbox
    # margem para filtros que olham a vizinhança (desfoque, nitidez) e grade do JPEG
    m = 0
    if nome in ("desfoque", "nitidez"):
        m = int(np.ceil(3 * num(p, "raio" if nome == "nitidez" else "sigma", 2, 0.1, 50))) + 2
    py0, py1, px0, px1 = max(0, y0 - m), min(H, y1 + m), max(0, x0 - m), min(W, x1 + m)
    if nome == "jpeg":  # alinha com a grade 8x8 da imagem inteira, como numa recompressão real
        py0, px0 = (y0 // 8) * 8, (x0 // 8) * 8
        py1, px1 = min(H, -(-y1 // 8) * 8), min(W, -(-x1 // 8) * 8)
    rec = img[py0:py1, px0:px1]
    f = rec.astype(np.float32)

    if nome == "brilho_contraste":
        b, c = num(p, "brilho", 0, -100, 100), num(p, "contraste", 0, -100, 100)
        out = _u8((f - 127.5) * (1 + c / 100.0) + 127.5 + b * 2.55)
    elif nome == "niveis":
        preto, branco = num(p, "preto", 0, 0, 254), num(p, "branco", 255, 1, 255)
        if branco <= preto:
            raise ErroOperacao("nível de branco precisa ser maior que o de preto")
        gama = num(p, "gama", 1.0, 0.1, 10)
        out = _u8(np.clip((f - preto) / (branco - preto), 0, 1) ** (1 / gama) * 255)
    elif nome == "matiz_saturacao":
        hsv = cv2.cvtColor(rec, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
        hsv[..., 0] = (hsv[..., 0] + num(p, "matiz", 0, -180, 180) / 360 * 256) % 256
        hsv[..., 1] *= 1 + num(p, "saturacao", 0, -100, 100) / 100
        hsv[..., 2] *= 1 + num(p, "luminosidade", 0, -100, 100) / 100
        out = cv2.cvtColor(_u8(np.clip(hsv, 0, 255)), cv2.COLOR_HSV2RGB_FULL)
    elif nome == "desfoque":
        out = cv2.GaussianBlur(rec, (0, 0), num(p, "sigma", 1, 0.1, 50))
    elif nome == "nitidez":
        borrado = cv2.GaussianBlur(f, (0, 0), num(p, "raio", 1, 0.1, 20))
        out = _u8(f + (f - borrado) * num(p, "quantidade", 100, 0, 500) / 100)
    elif nome == "ruido":
        rng = np.random.default_rng(inteiro(p, "semente", 0, 0, 2**31 - 1))
        sigma = num(p, "sigma", 5, 0, 60)
        forma = rec.shape[:2] + ((1,) if p.get("monocromatico", True) else (3,))
        out = _u8(f + rng.normal(0, sigma, forma).astype(np.float32))
    elif nome == "igualar_ruido":
        out = _igualar_ruido(img, sel, (py0, py1, px0, px1), p, img if base is None else base)
    elif nome == "cinza":
        g = f @ np.float32([0.299, 0.587, 0.114])
        out = _u8(np.repeat(g[..., None], 3, 2))
    else:  # jpeg
        q = inteiro(p, "qualidade", 75, 1, 100)
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rec, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
        if not ok:
            raise ErroOperacao("falha ao recomprimir")
        out = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)

    novo = out[y0 - py0:y1 - py0, x0 - px0:x1 - px0]
    return resultado_selecao(img, sel, novo)


# medido por simulação (ruído gaussiano em imagem uint8, sigma 1 a 15): o estimador abaixo
# rende 0,90-0,97 do sigma verdadeiro
_FATOR_ESTIMADOR = 0.92


def _ruido(rec: np.ndarray, mascara: np.ndarray) -> float:
    """Grão: desvio do resíduo da mediana 3x3 descartando os 2% maiores (bordas de letras e
    linhas, que inflariam a medida). Contínuo, ao contrário do MAD, que anda em degraus em uint8."""
    residuo = rec.astype(np.float32) - cv2.medianBlur(rec, 3).astype(np.float32)
    v = residuo[mascara].ravel()
    if v.size < 10:
        return 0.0
    a = np.abs(v)
    return float(v[a <= np.percentile(a, 98)].std())


def _so_papel(rec: np.ndarray, m: np.ndarray) -> np.ndarray:
    """``m`` sem a tinta do recorte (dilatada 2 px: a borda antisserrilhada da letra é aresta, não grão); a máscara
    inteira quando quase não sobra papel (a seleção cobre um bloco liso que a tinta toma por inteiro)."""
    papel = m & ~(cv2.dilate(mascara_tinta(rec), np.ones((5, 5), np.uint8)) > 0)
    return papel if papel.sum() >= 10 else m


# croma (Cb, Cr) e a volta de um ruído (Cb, Cr) para RGB
_CROMA = (np.float32([-0.168736, -0.331264, 0.5]), np.float32([0.5, -0.418688, -0.081312]))
_CROMA_RGB = np.float32([[0.0, 1.402], [-0.344136, -0.714136], [1.772, 0.0]])


def _ruido_croma(rec: np.ndarray, mascara: np.ndarray) -> list[float]:
    """Grão do croma (Cb, Cr), pelo mesmo estimador de ``_ruido`` (resíduo da mediana 3x3, sem os 2% maiores)."""
    out = []
    for eixo in _CROMA:
        plano = rec.astype(np.float32) @ eixo
        v = (plano - cv2.medianBlur(plano, 3))[mascara]
        a = np.abs(v)
        out.append(float(v[a <= np.percentile(a, 98)].std()) if v.size >= 10 else 0.0)
    return out


def _grao_espectral(img, caixa, canto, orig, atual, anel, dentro, p: dict) -> np.ndarray:
    """O grão que falta, com o MESMO espectro do papel original em volta (rotulador.pericia: fase randomizada do
    resíduo do papel), em vez de ruído branco. O grão de um scan é correlacionado (o desfoque e o JPEG espalham cada
    grão por alguns pixels); o branco igualava a variância pixel a pixel mas não a textura — medido em 12/10/2026,
    a seleção ficava com 0,81 do grão do papel no passa-alta, e a autocorrelação diferente é a pista nº 1 de um
    detector de resíduo. Luma e, sem ``monocromatico``, o croma (só se o papel tem grão de cor)."""
    from .. import pericia

    py0, py1, px0, px1 = caixa
    ay0, ax0 = canto
    forca = num(p, "forca", 1.0, 0, 2)
    semente = inteiro(p, "semente", 0, 0, 2**31 - 1)
    eixos = (pericia._LUMA,) if p.get("monocromatico") is True else (pericia._LUMA, pericia._CB, pericia._CR)
    of, af = orig.astype(np.float32), atual.astype(np.float32)
    # o papel de referência LONGE da tinta (4 px): num PDF limpo de layout denso, o eco do JPEG em volta das letras e
    # os fios das tabelas entravam como "grão" e o ruído saía 2-2,6x o do papel (conta NIO, 12/10/2026)
    longe = anel & ~(cv2.dilate(mascara_tinta(orig), np.ones((9, 9), np.uint8)) > 0)
    if longe.sum() >= 200:
        anel = longe
    planos = []
    for k, eixo in enumerate(eixos):
        _, residuo = pericia._grao(of @ eixo, anel)
        s_ref = pericia._grao_robusto(of @ eixo, anel)  # mediana dos desvios: o que sobrar de borda não infla
        s_at = pericia._grao_robusto(af @ eixo, dentro)
        falta = math.sqrt(max(0.0, s_ref ** 2 - s_at ** 2)) * forca
        if k == 0 and (s_ref < 0.05 or falta < 0.3 * s_ref):
            raise ErroOperacao(f"o ruído da seleção já é igual ao do papel original (seleção {s_at:.1f}, "
                               f"papel {s_ref:.1f}); nada a igualar")
        if s_ref < 0.05 or falta < 0.05:
            planos.append(np.zeros(anel.shape, np.float32))
            continue
        rz = pericia._sintetizar(residuo, anel, semente + k)
        s_r, _ = pericia._grao(rz, np.ones(rz.shape, bool))
        planos.append(rz * np.float32(falta / s_r) if s_r > 1e-3 else np.zeros(anel.shape, np.float32))
    while len(planos) < 3:
        planos.append(np.zeros(anel.shape, np.float32))
    rgb = np.stack(planos, -1) @ pericia._YCC_RGB.T
    rec = img[py0:py1, px0:px1].astype(np.float32) + rgb[py0 - ay0:py1 - ay0, px0 - ax0:px1 - ax0]
    return _u8(rec)


def _igualar_ruido(img: np.ndarray, sel: Selecao, caixa, p: dict, original: np.ndarray) -> np.ndarray:
    """Acrescenta à seleção o grão que falta para igualar o do DOCUMENTO ORIGINAL ao redor.

    Edição digital (texto novo, preenchimento, formas) sai limpa demais perto do grão do
    scan/foto; esse contraste é um atalho fácil para o detector. A referência vem do
    original (não da imagem editada): selecionar dentro de uma área já editada e lisa
    mediria a própria edição e não acrescentaria nada. Mede só o PAPEL (sem tinta) dos dois
    lados: a borda do texto novo na seleção inflava a medida e dava "já é igual" com o papel
    pela metade do grão. Sem ``monocromatico`` explícito, o croma também é casado com o do papel."""
    py0, py1, px0, px1 = caixa
    H, W = img.shape[:2]
    m = 40
    ay0, ay1, ax0, ax1 = max(0, py0 - m), min(H, py1 + m), max(0, px0 - m), min(W, px1 + m)
    dentro = np.zeros((ay1 - ay0, ax1 - ax0), bool)
    dentro[sel.y0 - ay0:sel.y1 - ay0, sel.x0 - ax0:sel.x1 - ax0] = sel.alpha > 0.5
    if dentro.sum() < 10:
        raise ErroOperacao("seleção pequena demais para medir o ruído")
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * m + 1, 2 * m + 1))
    anel = cv2.dilate(dentro.astype(np.uint8), k).astype(bool) & ~dentro
    orig, atual = original[ay0:ay1, ax0:ax1], img[ay0:ay1, ax0:ax1]
    anel, dentro = _so_papel(orig, anel), _so_papel(atual, dentro)
    if p.get("espectro", "papel") != "branco":
        return _grao_espectral(img, (py0, py1, px0, px1), (ay0, ax0), orig, atual, anel, dentro, p)
    s_ref = _ruido(orig, anel)  # grão do papel ORIGINAL em volta da seleção
    s_atual = _ruido(atual, dentro)
    falta = math.sqrt(max(0.0, s_ref ** 2 - s_atual ** 2)) / _FATOR_ESTIMADOR * num(p, "forca", 1.0, 0, 2)
    if s_ref < 0.05 or falta < 0.3 * s_ref:  # papel liso de verdade, ou diferença dentro da variação de amostragem
        raise ErroOperacao(f"o ruído da seleção já é igual ao do papel original (seleção {s_atual:.1f}, "
                           f"papel {s_ref:.1f}); nada a igualar")
    rng = np.random.default_rng(inteiro(p, "semente", 0, 0, 2**31 - 1))
    rec = img[py0:py1, px0:px1].astype(np.float32)
    mono = p.get("monocromatico")
    forma = rec.shape[:2] + ((1,) if mono is not False else (3,))
    ruido = rng.normal(0, falta, forma).astype(np.float32)
    if mono is None:  # o croma do papel: o de uma foto tem grão colorido; o de um documento cinza, nenhum
        faltas = [math.sqrt(max(0.0, r ** 2 - a ** 2)) / _FATOR_ESTIMADOR * num(p, "forca", 1.0, 0, 2)
                  for r, a in zip(_ruido_croma(orig, anel), _ruido_croma(atual, dentro))]
        if max(faltas) >= 0.05:
            croma = rng.normal(0, 1, rec.shape[:2] + (2,)).astype(np.float32) * np.float32(faltas)
            ruido = ruido + croma @ _CROMA_RGB.T
    return _u8(rec + ruido)
