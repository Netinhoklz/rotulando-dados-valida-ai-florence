"""Marcas benignas: o que aparece em comprovantes VERDADEIROS e não é fraude.

Autenticação mecânica do banco, carimbo de pago/recebido, anotação à mão, marca-texto, rubrica,
dobra e furos. Sem elas, o modelo vê "alguma coisa mudou no papel" só nas adulteradas e aprende a
acusar qualquer marca (falso positivo em conta paga e carimbada). Negativos difíceis reduzem o falso
positivo (MVSS-Net; GIFL); o "Find it again" mantém as anotações dos recibos autênticos.

Regras:
  - entram nas DUAS imagens do par, iguais (não viram atalho entre as classes);
  - antes do canal de envio, e só quando há canal com captura física (impressão, scanner, celular,
    tela): são marcas no papel, então passam pela mesma captura que o resto do documento;
  - longe da edição, a não ser quando a receita pede ``sobre_edicao`` (fração das versões com marca):
    aí UMA marca de tinta (carimbo, autenticação, anotação, rubrica, marca-texto) fica por cima da
    edição, igual nas duas imagens — num documento de verdade o carimbo cai em cima do nome, e sem isso
    o detector aprende que "onde há carimbo não há fraude". A tinta é multiplicativa: a edição continua
    lá por baixo; onde a marca é opaca o par fica igual, e a máscara tem de dizer o que mudou de fato
    (``sobre_edicao`` no info da marca). A dobra e a marca d'água já cobrem a página toda.

Marcas DIGITAIS (``DIGITAIS``): tarja, borrão e pixelado sobre um campo SENSÍVEL (CPF, código do
cliente, linha digitável, código de barras, QR) — a pessoa esconde o próprio dado antes de mandar o
comprovante — e a marca d'água ("CÓPIA", "2ª VIA") que apps e sites de 2ª via carimbam. Não precisam
de captura física: entram na imagem, depois da edição e antes do envio. Sem elas o detector aprende
que "retângulo preto = fraude" e acusa todo comprovante com o CPF tarjado. A tarja só vai num campo
sensível que NÃO é a edição (as caixas candidatas vêm de ``campos`` em ``sortear``).

    theta = sortear(rng, cfg, campos, fisica)            # o que desenhar (posições saem em aplicar)
    papel, digital = dividir(theta)                      # as de papel antes da captura, as digitais depois
    imgs, info = aplicar([orig, ed], regiao, theta)      # info: tipo e caixa de cada marca
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TIPOS = {"autenticacao": 0.25, "carimbo": 0.2, "manuscrito": 0.2, "marca_texto": 0.12, "rubrica": 0.1,
         "dobra": 0.1, "dedo": 0.1, "furos": 0.03, "caneta": 0.15, "grampo": 0.06, "mancha": 0.06,
         "canto_dobrado": 0.05,
         "tarja": 0.2, "borrao": 0.07, "pixelado": 0.07, "marca_dagua": 0.1}
NOMES = {"autenticacao": "autenticação mecânica", "carimbo": "carimbo", "manuscrito": "anotação à mão",
         "marca_texto": "marca-texto", "rubrica": "rubrica", "dobra": "dobra no papel", "furos": "furos de arquivo",
         "dedo": "dedo segurando o papel na borda", "caneta": "marca de caneta (círculo, sublinhado ou visto)",
         "grampo": "grampo no canto", "mancha": "mancha de copo/água", "canto_dobrado": "canto dobrado",
         "tarja": "tarja sobre dado sensível",
         "borrao": "borrão sobre dado sensível", "pixelado": "pixelado sobre dado sensível",
         "marca_dagua": "marca d'água digital"}
DIGITAIS = frozenset({"tarja", "borrao", "pixelado", "marca_dagua"})  # na imagem, sem captura física
# tinta translúcida (multiplicativa): pode cair em cima da edição sem apagá-la (dedo, furos e tarja apagariam)
SOBRE_EDICAO = ("carimbo", "manuscrito", "autenticacao", "rubrica", "marca_texto", "caneta")
SENSIVEIS = ("cpf/cnpj", "identificador documento", "linha digitável", "codigo barras", "qr code/pix")
_MARCAS_DAGUA = ("CÓPIA", "2ª VIA", "SEGUNDA VIA", "CONFIDENCIAL", "DOCUMENTO SEM VALOR FISCAL", "VIA DO CLIENTE")
_PELES = ((224, 172, 138), (198, 134, 96), (160, 102, 70), (120, 72, 48), (240, 196, 170), (90, 55, 40))
_FONTES = Path("C:/Windows/Fonts")
_MAO = ("Inkfree.ttf", "segoepr.ttf", "Freehand521 BT.ttf", "comic.ttf")
_MONO = ("consola.ttf", "cour.ttf", "lucon.ttf")
_CARIMBO = ("impact.ttf", "arialbd.ttf", "courbd.ttf")
_TINTAS = {"azul": (35, 55, 150), "vermelho": (175, 40, 45), "roxo": (105, 50, 140), "preto": (30, 30, 35)}
_MARCA_TEXTO = {"amarelo": (250, 236, 70), "verde": (150, 235, 100), "rosa": (250, 150, 205)}


def normalizar(cfg) -> dict | None:
    if not cfg:
        return None
    if isinstance(cfg, (bool, str, list)):
        raise ValueError(f"marcas benignas inválidas: {cfg!r}")
    prob = float(cfg.get("prob", 0) if isinstance(cfg, dict) else cfg)
    if prob <= 0:
        return None
    out = {"prob": max(0.0, min(1.0, prob))}
    sobre = float(cfg.get("sobre_edicao", 0) or 0) if isinstance(cfg, dict) else 0.0
    if sobre > 0:  # sem a chave: nunca por cima da edição (receitas antigas)
        out["sobre_edicao"] = min(1.0, sobre)
    return out


def sortear(rng: random.Random, cfg, campos: list[dict] | None = None, fisica: bool = True) -> dict | None:
    """``campos``: os campos do documento (caixa e categoria): os sensíveis viram alvos da tarja/borrão/pixelado.
    ``fisica``: há captura física no canal? Sem ela, só as marcas digitais entram (as de papel não teriam como)."""
    cfg = normalizar(cfg)
    if cfg is None or rng.random() >= cfg["prob"]:
        return None
    pesos = {t: w for t, w in TIPOS.items() if fisica or t in DIGITAIS}
    n = rng.choices((1, 2, 3), weights=(0.55, 0.3, 0.15))[0]
    tipos = rng.choices(list(pesos), weights=list(pesos.values()), k=n)
    alvos = [[int(v) for v in f["caixa"]] for f in (campos or []) if f.get("categoria") in SENSIVEIS and f.get("caixa")]
    rng.shuffle(alvos)
    theta = {"semente": rng.randrange(2**31), "tipos": tipos, "alvos": alvos}
    if cfg.get("sobre_edicao"):  # sorteado por último: o resto do theta sai igual ao das receitas sem a chave
        theta["sobre_edicao"] = rng.random() < cfg["sobre_edicao"]
    return theta


def dividir(theta: dict | None) -> tuple[dict | None, dict | None]:
    """(marcas no papel, marcas digitais): as de papel entram antes da captura física; as digitais, na imagem,
    depois da edição. Cada parte é None quando vazia."""
    if not theta:
        return None, None
    papel = [t for t in theta["tipos"] if t not in DIGITAIS]
    digital = [t for t in theta["tipos"] if t in DIGITAIS]
    return ({**theta, "tipos": papel} if papel else None), ({**theta, "tipos": digital} if digital else None)


def _fonte(nomes, tamanho: int):
    for n in nomes:
        p = _FONTES / n
        if p.is_file():
            return ImageFont.truetype(str(p), max(6, int(tamanho)))
    return ImageFont.load_default(size=max(6, int(tamanho)))


def _texto_alfa(linhas: list[str], fonte, espaco: float = 1.25) -> np.ndarray:
    """Alfa (0..1) de linhas de texto centralizadas."""
    caixas = [fonte.getbbox(t) for t in linhas]
    w = max(b[2] - b[0] for b in caixas) + 8
    alt = max(b[3] - b[1] for b in caixas)
    h = int(alt * espaco * len(linhas)) + 8
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    for i, (t, b) in enumerate(zip(linhas, caixas)):
        d.text(((w - (b[2] - b[0])) / 2 - b[0], 4 + i * alt * espaco - b[1]), t, font=fonte, fill=255)
    return np.asarray(im, np.float32) / 255


def _girar(a: np.ndarray, ang: float) -> np.ndarray:
    h, w = a.shape
    c, s = abs(math.cos(math.radians(ang))), abs(math.sin(math.radians(ang)))
    W2, H2 = int(w * c + h * s) + 2, int(w * s + h * c) + 2
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    M[0, 2] += (W2 - w) / 2
    M[1, 2] += (H2 - h) / 2
    return cv2.warpAffine(a, M, (W2, H2), flags=cv2.INTER_LINEAR)


def _gastar(a: np.ndarray, rng: np.random.Generator, falhas: float) -> np.ndarray:
    """Tinta de carimbo/fita: falhas e intensidade irregular."""
    ruido = cv2.GaussianBlur(rng.standard_normal(a.shape).astype(np.float32), (0, 0), 1.2)
    ruido /= ruido.std() + 1e-6
    return np.clip(a * np.clip(1 - falhas * np.maximum(ruido, 0), 0, 1) * rng.uniform(0.7, 0.95), 0, 1)


def _data(rng) -> str:
    return f"{rng.integers(1, 29):02d}/{rng.integers(1, 13):02d}/{rng.integers(2023, 2027)}"


def _desenho(tipo: str, H: int, W: int, rng: np.random.Generator):
    """(alfa, cor RGB) da marca, no tamanho do documento."""
    u = min(H, W) / 1000  # escala: ~1 em 150 dpi A4 (1240 px de largura)
    if tipo == "autenticacao":
        txt = (f"{rng.integers(1, 999):03d} {rng.integers(1, 9999):04d} {rng.integers(1, 99999):05d} "
               f"{_data(rng)} {rng.integers(7, 18):02d}:{rng.integers(0, 60):02d} R$ "
               f"{rng.integers(20, 900)},{rng.integers(0, 100):02d} AUT {rng.integers(0, 16**6):06X}")
        a = _texto_alfa([txt], _fonte(_MONO, 13 * u * rng.uniform(0.9, 1.3)))
        return _gastar(_girar(a, rng.uniform(-2.5, 2.5)), rng, 0.35), _TINTAS[rng.choice(["roxo", "preto", "azul"])]
    if tipo == "carimbo":
        # "CANCELADO" ficou de fora: num comprovante de endereço não é marca benigna
        linhas = [["PAGO", _data(rng)], ["RECEBIDO", _data(rng)], ["CONFERE COM O ORIGINAL"],
                  ["BANCO", f"AG {rng.integers(1, 9999):04d}", _data(rng)]][rng.integers(4)]
        a = _texto_alfa(linhas, _fonte(_CARIMBO, 26 * u * rng.uniform(0.8, 1.3)))
        h, w = a.shape
        m = int(8 * u) + 4
        borda = np.zeros((h + 2 * m, w + 2 * m), np.uint8)
        t = max(2, int(3 * u))
        if rng.random() < 0.5:
            cv2.rectangle(borda, (t, t), (w + 2 * m - t, h + 2 * m - t), 255, t)
        else:
            cv2.ellipse(borda, ((w + 2 * m) // 2, (h + 2 * m) // 2), ((w + 2 * m) // 2 - t, (h + 2 * m) // 2 - t),
                        0, 0, 360, 255, t)
        borda = borda.astype(np.float32) / 255
        borda[m:m + h, m:m + w] = np.maximum(borda[m:m + h, m:m + w], a)
        return _gastar(_girar(borda, rng.uniform(-25, 25)), rng, 0.6), \
            _TINTAS[rng.choice(["azul", "vermelho", "roxo"])]
    if tipo == "manuscrito":
        txt = ["pago", "ok", f"pago {_data(rng)[:5]}", "conferido", f"R$ {rng.integers(20, 500)},00", "ver",
               "2ª via", _data(rng), "ok!"][rng.integers(9)]
        a = _texto_alfa([txt], _fonte(_MAO, 30 * u * rng.uniform(0.8, 1.4)))
        return np.clip(_girar(a, rng.uniform(-12, 12)) * rng.uniform(0.8, 1.0), 0, 1), \
            _TINTAS[rng.choice(["azul", "azul", "preto"])]
    if tipo == "rubrica":
        w, h = int(rng.uniform(90, 170) * u), int(rng.uniform(35, 70) * u)
        im = np.zeros((h + 10, w + 10), np.uint8)
        pts, x, y = [], 5.0, h / 2 + 5
        for _ in range(int(rng.integers(3, 7))):
            p0, p3 = np.array([x, y]), np.array([min(w + 5, x + rng.uniform(0.15, 0.35) * w), rng.uniform(5, h + 5)])
            p1, p2 = p0 + rng.uniform(-0.6, 0.6, 2) * [w * 0.3, h], p3 + rng.uniform(-0.6, 0.6, 2) * [w * 0.3, h]
            for tt in np.linspace(0, 1, 24):
                pts.append((1 - tt) ** 3 * p0 + 3 * (1 - tt) ** 2 * tt * p1 + 3 * (1 - tt) * tt ** 2 * p2 + tt ** 3 * p3)
            x, y = p3
        cv2.polylines(im, [np.int32(np.clip(pts, 0, [w + 9, h + 9]))], False, 255, max(2, int(2.5 * u)), cv2.LINE_AA)
        return cv2.GaussianBlur(im.astype(np.float32) / 255, (0, 0), 0.6), _TINTAS[rng.choice(["azul", "preto"])]
    if tipo == "caneta":
        # quem confere a conta circula o valor, sublinha a data ou faz um visto do lado: traço de caneta esferográfica
        w, h = int(rng.uniform(110, 230) * u), int(rng.uniform(34, 60) * u)
        t = max(2, int(2.2 * u))
        im = np.zeros((h + 4 * t, w + 4 * t), np.uint8)
        forma = rng.choice(["circulo", "sublinhado", "visto"])
        if forma == "circulo":  # a volta não fecha certinho e passa um pouco do começo
            ang = np.linspace(rng.uniform(-40, 0), 360 + rng.uniform(15, 50), 120)
            rx, ry = w / 2 * rng.uniform(0.9, 1.0), h / 2 * rng.uniform(0.85, 1.0)
            esp = 1 + 0.05 * np.sin(np.radians(ang) * 2 + rng.uniform(0, 6))
            pts = np.stack([w / 2 + 2 * t + rx * esp * np.cos(np.radians(ang)),
                            h / 2 + 2 * t + ry * esp * np.sin(np.radians(ang))], 1)
        elif forma == "sublinhado":
            xs = np.linspace(2 * t, w + 2 * t, 40)
            pts = np.stack([xs, h + t + rng.uniform(-2, 2) * u * np.sin(xs / w * rng.uniform(2, 5))], 1)
        else:
            pts = np.array([[w * 0.35, h * 0.55], [w * 0.45, h * 0.95], [w * 0.65, h * 0.1]]) + 2 * t
        cv2.polylines(im, [np.int32(pts)], False, 255, t, cv2.LINE_AA)
        return cv2.GaussianBlur(im.astype(np.float32) / 255, (0, 0), 0.6) * rng.uniform(0.75, 0.95), \
            _TINTAS[rng.choice(["azul", "azul", "preto", "vermelho"])]
    raise ValueError(tipo)


def _livre(proibido: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    H, W = proibido.shape
    return 0 <= x and 0 <= y and x + w <= W and y + h <= H and not proibido[y:y + h, x:x + w].any()


def _multiplicar(imgs, x, y, alfa, cor) -> None:
    h, w = alfa.shape
    f = 1 - alfa[..., None] * (1 - np.float32(cor) / 255)
    for im in imgs:
        im[y:y + h, x:x + w] *= f


def _marca_texto(imgs, proibido, rng, u, alvo=None) -> list | None:
    """Faixa translúcida sobre uma linha de texto (procura linhas pela projeção de tinta). ``alvo`` (caixa de uma
    linha editada): a faixa passa por cima dela, como quem grifa o próprio endereço."""
    H, W = proibido.shape
    cinza = imgs[0].mean(2)
    for _ in range(30):
        if alvo is not None:
            ax0, ay0, ax1, ay1 = alvo
            x = max(0, ax0 - int((ax1 - ax0) * rng.uniform(0, 0.3)))
            w = min(W, ax1 + int((ax1 - ax0) * rng.uniform(0, 0.3))) - x
            y0, y1 = ay0, ay1 - 1
        else:
            w = int(W * rng.uniform(0.15, 0.45))
            x = int(rng.integers(0, max(1, W - w)))
            perfil = (cinza[:, x:x + w] < 140).mean(1)
            linhas = np.flatnonzero(perfil > 0.03)
            if linhas.size == 0:
                continue
            yc = int(rng.choice(linhas))
            y0, y1 = yc, yc
            while y0 > 0 and perfil[y0 - 1] > 0.01:
                y0 -= 1
            while y1 < H - 1 and perfil[y1 + 1] > 0.01:
                y1 += 1
        hh = y1 - y0 + 1
        if not 6 * u <= hh <= 60 * u:
            if alvo is not None:
                return None
            continue
        m = int(hh * rng.uniform(0.15, 0.3))
        y0, y1 = max(0, y0 - m), min(H, y1 + m + 1)
        if w < 4 or not _livre(proibido, x, y0, w, y1 - y0):
            continue
        alfa = np.ones((y1 - y0, w), np.float32) * rng.uniform(0.35, 0.55)
        borda = np.cumsum(rng.normal(0, 0.6, w))  # borda ondulada de quem passa a caneta à mão
        for i in range(w):
            alfa[: max(0, int(abs(borda[i]) % 3)), i] = 0
        alfa = cv2.GaussianBlur(alfa, (0, 0), 0.8)
        _multiplicar(imgs, x, y0, alfa, _MARCA_TEXTO[rng.choice(list(_MARCA_TEXTO))])
        return [x, y0, x + w, y1]
    return None


def _dedo(imgs, proibido, rng, u) -> list | None:
    """Um dedo entrando por uma borda (quem segura o papel para fotografar). Nos AUTÊNTICOS também: o
    modelo aprende que dedo na borda não é fraude; dedo em cima de um campo (campo.ocultar) é."""
    H, W = proibido.shape
    larg = int(rng.uniform(55, 95) * u)
    comp = int(rng.uniform(70, 160) * u)
    for _ in range(30):
        lado = rng.choice(["baixo", "baixo", "esquerda", "direita", "cima"])
        if lado in ("baixo", "cima"):
            x = int(rng.integers(0, max(1, W - larg)))
            y = H - comp if lado == "baixo" else 0
            w, h = larg, comp
        else:
            y = int(rng.integers(0, max(1, H - larg)))
            x = W - comp if lado == "direita" else 0
            w, h = comp, larg
        if not _livre(proibido, x, y, w, h):
            continue
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        if lado in ("baixo", "cima"):
            cx, cy = w / 2, (h + larg / 2) if lado == "baixo" else (-larg / 2)
            px, py = xs + 0.5 - cx, ys + 0.5 - cy
            dx, dy = np.abs(px) - 0, np.abs(py) - (h + larg) / 2 + larg / 2
            d = np.hypot(np.maximum(dx, 0), np.maximum(dy, 0)) + np.minimum(np.maximum(dx, dy), 0) - larg / 2
            eixo = px / (larg / 2)
        else:
            cx, cy = (w + larg / 2) if lado == "direita" else (-larg / 2), h / 2
            px, py = xs + 0.5 - cx, ys + 0.5 - cy
            dx, dy = np.abs(px) - (w + larg) / 2 + larg / 2, np.abs(py) - 0
            d = np.hypot(np.maximum(dx, 0), np.maximum(dy, 0)) + np.minimum(np.maximum(dx, dy), 0) - larg / 2
            eixo = py / (larg / 2)
        alfa = np.clip(0.5 - d / 1.6, 0, 1)
        pele = np.float32(_PELES[rng.integers(len(_PELES))]) * rng.uniform(0.9, 1.08)
        sombreado = 1.0 - 0.28 * np.clip(np.abs(eixo), 0, 1) ** 2
        cor = np.clip(pele[None, None, :] * sombreado[..., None] + rng.normal(0, 2.5, (h, w, 1)), 0, 255)
        sombra = cv2.GaussianBlur(np.roll(np.roll(alfa, 3, 0), 2, 1), (0, 0), 1.8) * (1 - alfa) * 0.4
        for im in imgs:
            rec = im[y:y + h, x:x + w]
            rec *= (1 - sombra[..., None])
            rec[:] = rec * (1 - alfa[..., None]) + cor * alfa[..., None]
        return [x, y, x + w, y + h]
    return None


def _dobra(imgs, rng) -> list:
    """Vinco: sombra suave de um lado, linha clara e linha escura (luz rasante no scanner)."""
    H, W = imgs[0].shape[:2]
    horizontal = rng.random() < 0.7
    L = H if horizontal else W
    p = int(L * rng.uniform(0.3, 0.7))
    d = np.arange(L, dtype=np.float32) - p
    s = max(2.0, L * 0.004)
    a = rng.uniform(0.03, 0.08)
    perfil = 1 - a * np.exp(-(d / (6 * s)) ** 2) * (d > 0) - 1.5 * a * np.exp(-((d - 1) / 1.2) ** 2) \
        + 0.6 * a * np.exp(-((d + 1.5) / 1.2) ** 2)
    f = perfil[:, None, None] if horizontal else perfil[None, :, None]
    for im in imgs:
        im *= f
    return [0, p - 3, W, p + 3] if horizontal else [p - 3, 0, p + 3, H]


def _ocultar(imgs, proibido, rng, u, alvos, tipo: str) -> list | None:
    """Tarja (retângulo chapado), borrão ou pixelado sobre um campo SENSÍVEL (os ``alvos``, na ordem sorteada),
    como quem esconde o próprio CPF antes de mandar o comprovante. Nunca sobre a edição (``proibido``)."""
    H, W = proibido.shape
    cor = rng.uniform(0, 40) if rng.random() < 0.85 else rng.uniform(248, 255)  # caneta preta do app, ou branca
    for caixa in alvos:
        m = int(rng.integers(2, 7))
        x0, y0 = max(0, int(caixa[0]) - m), max(0, int(caixa[1]) - m)
        x1, y1 = min(W, int(caixa[2]) + m), min(H, int(caixa[3]) + m)
        w, h = x1 - x0, y1 - y0
        if w < 4 or h < 4 or not _livre(proibido, x0, y0, w, h):
            continue
        if tipo == "tarja":
            for im in imgs:
                im[y0:y1, x0:x1] = cor
        elif tipo == "borrao":
            sigma = max(1.5, 0.3 * h)
            for im in imgs:
                im[y0:y1, x0:x1] = cv2.GaussianBlur(im[y0:y1, x0:x1], (0, 0), sigma)
        else:  # pixelado: mosaico de blocos
            b = max(4, h // 4)
            for im in imgs:
                sub = im[y0:y1, x0:x1]
                peq = cv2.resize(sub, (max(1, w // b), max(1, h // b)), interpolation=cv2.INTER_AREA)
                im[y0:y1, x0:x1] = cv2.resize(peq, (w, h), interpolation=cv2.INTER_NEAREST)
        return [x0, y0, x1, y1]
    return None


def _marca_dagua(imgs, rng, u) -> list:
    """Texto grande, translúcido e inclinado pela página ("CÓPIA", "2ª VIA"...), como apps de banco e sites de
    2ª via carimbam digitalmente. Multiplicativo e igual no par: não esconde a edição (como a dobra)."""
    H, W = imgs[0].shape[:2]
    txt = _MARCAS_DAGUA[int(rng.integers(len(_MARCAS_DAGUA)))]
    a = _texto_alfa([txt], _fonte(_CARIMBO, 70 * u * rng.uniform(0.8, 1.4)))
    a = _girar(a, rng.uniform(-45, -20) if rng.random() < 0.8 else 0.0) * rng.uniform(0.12, 0.3)
    h, w = a.shape
    if h >= H - 2 or w >= W - 2:
        e = min((H - 2) / h, (W - 2) / w)
        a = cv2.resize(a, (max(1, int(w * e)), max(1, int(h * e))), interpolation=cv2.INTER_AREA)
        h, w = a.shape
    x = int((W - w) * rng.uniform(0.3, 0.7))
    y = int((H - h) * rng.uniform(0.3, 0.7))
    _multiplicar(imgs, x, y, a, (120, 120, 120) if rng.random() < 0.7 else (190, 40, 40))
    return [x, y, x + w, y + h]


def _furos(imgs, proibido, rng, u) -> list | None:
    H, W = proibido.shape
    r = int(9 * u) + 2
    x = int(W * rng.uniform(0.03, 0.06))
    ys = [int(H * 0.5 - 40 * u), int(H * 0.5 + 40 * u)]
    if any(not _livre(proibido, x - r - 2, y - r - 2, 2 * r + 5, 2 * r + 5) for y in ys):  # + antialias
        return None
    tom = rng.uniform(40, 110)
    for y in ys:
        m = np.zeros((H, W), np.float32)
        cv2.circle(m, (x, y), r, 1.0, -1, cv2.LINE_AA)
        for im in imgs:
            im[:] = im * (1 - m[..., None]) + tom * m[..., None]
    return [x - r - 2, ys[0] - r - 2, x + r + 3, ys[1] + r + 3]


def _grampo(imgs, proibido, rng, u) -> list | None:
    """Grampo num canto de cima: a fita de metal (escura com um brilho) e os dois furos nas pontas."""
    H, W = proibido.shape
    L, e = int(rng.uniform(45, 60) * u), max(2, int(2.5 * u))  # 12 mm x 0,6 mm
    ang = rng.uniform(-50, -25) if rng.random() < 0.5 else rng.uniform(25, 50)
    esquerda = ang < 0
    cx = int(W * rng.uniform(0.05, 0.08)) if esquerda else int(W * rng.uniform(0.92, 0.95))
    cy = int(max(L, H * rng.uniform(0.025, 0.05)))
    dx, dy = math.cos(math.radians(ang)) * L / 2, math.sin(math.radians(ang)) * L / 2
    p0, p1 = (int(cx - dx), int(cy - dy)), (int(cx + dx), int(cy + dy))
    x0, y0 = min(p0[0], p1[0]) - 2 * e, min(p0[1], p1[1]) - 2 * e
    x1, y1 = max(p0[0], p1[0]) + 2 * e, max(p0[1], p1[1]) + 2 * e
    if not _livre(proibido, max(0, x0), max(0, y0), min(W, x1) - max(0, x0), min(H, y1) - max(0, y0)):
        return None
    escuro, brilho = np.zeros((H, W), np.float32), np.zeros((H, W), np.float32)
    cv2.line(escuro, p0, p1, 1.0, e, cv2.LINE_AA)
    cv2.line(brilho, p0, p1, 1.0, max(1, e // 3), cv2.LINE_AA)
    sombra = cv2.GaussianBlur(np.roll(escuro, (max(1, e // 2), max(1, e // 2)), (0, 1)), (0, 0), e)
    for p in (p0, p1):
        cv2.circle(escuro, p, max(1, e // 2 + 1), 1.0, -1, cv2.LINE_AA)
    metal = rng.uniform(90, 140)
    for im in imgs:
        im *= (1 - 0.25 * sombra)[..., None]
        im[:] = im * (1 - escuro[..., None]) + metal * escuro[..., None]
        im[:] = im * (1 - 0.5 * brilho[..., None]) + 215 * 0.5 * brilho[..., None]
    return [max(0, x0), max(0, y0), min(W, x1), min(H, y1)]


def _mancha(imgs, proibido, rng, u) -> list | None:
    """Mancha de copo ou de água: anel irregular, mais escuro na borda (o líquido seca levando o pigmento), e um véu
    claro por dentro; tinta multiplicativa amarronzada, o texto continua legível por baixo."""
    H, W = proibido.shape
    r = int(rng.uniform(60, 120) * u)
    for _ in range(30):
        cx, cy = int(rng.integers(r + 2, W - r - 2)), int(rng.integers(r + 2, H - r - 2))
        if _livre(proibido, cx - r - 4, cy - r - 4, 2 * r + 8, 2 * r + 8):
            break
    else:
        return None
    yy, xx = np.mgrid[-r - 4:r + 5, -r - 4:r + 5].astype(np.float32)
    ang = np.arctan2(yy, xx)
    raio = r * (1 + 0.012 * np.sin(3 * ang + rng.uniform(0, 6)) + 0.008 * np.sin(7 * ang + rng.uniform(0, 6)))
    d = np.hypot(xx, yy) - raio
    anel = np.exp(-(d / max(1.2, 1.8 * u)) ** 2) * rng.uniform(0.3, 0.5)
    # por dentro, um véu fraco que clareia para o centro (o pigmento foi para a borda)
    veu = (d < 0) * rng.uniform(0.02, 0.05) * np.clip(1 + d / max(1.0, r), 0.3, 1)
    alfa = np.clip(anel + veu, 0, 1).astype(np.float32)
    _multiplicar(imgs, cx - r - 4, cy - r - 4, alfa, (150, 110, 70))
    return [cx - r - 4, cy - r - 4, cx + r + 5, cy + r + 5]


def _canto_dobrado(imgs, proibido, rng, u) -> list | None:
    """Canto dobrado (orelha): um triângulo no canto mostra o verso do papel (liso, um pouco mais escuro, sem o
    conteúdo) e uma sombra ao longo do vinco."""
    H, W = proibido.shape
    s = int(rng.uniform(50, 110) * u)
    canto = rng.integers(4)
    x0 = 0 if canto in (0, 2) else W - s
    y0 = 0 if canto in (0, 1) else H - s
    if not _livre(proibido, x0, y0, s, s):
        return None
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    lx = xx if canto in (0, 2) else s - 1 - xx
    ly = yy if canto in (0, 1) else s - 1 - yy
    tri = (lx + ly < s).astype(np.float32)
    vinco = np.exp(-((lx + ly - s) / max(2.0, 3 * u)) ** 2) * (lx + ly >= s)
    # o verso do papel: claro, com um degradê da luz (mais escuro perto do vinco)
    tom = rng.uniform(228, 242) - 14 * np.clip((lx + ly) / s, 0, 1)
    for im in imgs:
        sub = im[y0:y0 + s, x0:x0 + s]
        sub[:] = sub * (1 - tri[..., None]) + (tom * tri)[..., None]
        sub *= (1 - 0.18 * vinco)[..., None]
    return [x0, y0, x0 + s, y0 + s]


def _alvos_da_edicao(regiao: np.ndarray, rng) -> list[list[int]]:
    """Caixas das linhas editadas (a região juntada na horizontal, uma componente por linha), em ordem sorteada."""
    k = max(9, regiao.shape[1] // 60)
    juntas = cv2.dilate(regiao.astype(np.uint8), np.ones((1, k), np.uint8))
    n, _, est, _ = cv2.connectedComponentsWithStats(juntas)
    caixas = [[int(s[0]), int(s[1]), int(s[0] + s[2]), int(s[1] + s[3])] for s in est[1:] if s[2] >= 4 and s[3] >= 3]
    return [caixas[i] for i in rng.permutation(len(caixas))]


def _por_cima(saida, ocupado, rng, u, tipo, alvos, H, W) -> list | None:
    """Uma marca de tinta (``SOBRE_EDICAO``) cruzando uma das linhas editadas, sem cobrir outra marca."""
    if tipo == "marca_texto":
        for alvo in alvos:
            caixa = _marca_texto(saida, ocupado, rng, u, alvo)
            if caixa:
                return caixa
        return None
    alfa, cor = _desenho(tipo, H, W, rng)
    h, w = alfa.shape
    if h >= H or w >= W:
        return None
    for alvo in alvos:
        cx, cy = (alvo[0] + alvo[2]) // 2, (alvo[1] + alvo[3]) // 2
        for _ in range(20):
            x = int(np.clip(cx - rng.integers(w // 5, max(w // 5 + 1, 4 * w // 5)), 0, W - w))
            y = int(np.clip(cy - rng.integers(h // 5, max(h // 5 + 1, 4 * h // 5)), 0, H - h))
            if x < alvo[2] and x + w > alvo[0] and y < alvo[3] and y + h > alvo[1] and _livre(ocupado, x, y, w, h):
                _multiplicar(saida, x, y, alfa, cor)
                return [x, y, x + w, y + h]
    return None


def aplicar(imgs, regiao: np.ndarray, theta: dict) -> tuple[list[np.ndarray], list[dict]]:
    """Desenha as marcas iguais em todas as imagens, longe de ``regiao`` (a edição). Com ``theta["sobre_edicao"]``,
    a primeira marca de tinta (``SOBRE_EDICAO``) fica por cima de uma linha editada (``sobre_edicao`` no info)."""
    rng = np.random.default_rng(theta["semente"])
    saida = [np.asarray(i, np.float32).copy() for i in imgs]
    H, W = regiao.shape
    u = min(H, W) / 1000
    folga = max(8, int(0.015 * min(H, W)))
    proibido = cv2.dilate(regiao.astype(np.uint8), np.ones((2 * folga + 1, 2 * folga + 1), np.uint8)) > 0
    ocupado = np.zeros_like(proibido)  # só as outras marcas (para a que vai por cima da edição)
    sobre = bool(theta.get("sobre_edicao")) and regiao.any()
    info = []
    for tipo in theta["tipos"]:
        caixa = None
        if sobre and tipo in SOBRE_EDICAO:
            sobre = False  # uma só por versão
            caixa = _por_cima(saida, ocupado, rng, u, tipo, _alvos_da_edicao(regiao, rng), H, W)
            if caixa:
                info.append({"tipo": tipo, "nome": NOMES[tipo], "caixa": [int(v) for v in caixa], "sobre_edicao": True})
                x0, y0, x1, y1 = caixa
                proibido[max(0, y0):y1, max(0, x0):x1] = True
                ocupado[max(0, y0):y1, max(0, x0):x1] = True
                continue
        if tipo == "dobra":
            caixa = _dobra(saida, rng)
        elif tipo == "marca_dagua":
            caixa = _marca_dagua(saida, rng, u)
        elif tipo in ("tarja", "borrao", "pixelado"):
            caixa = _ocultar(saida, proibido, rng, u, theta.get("alvos") or [], tipo)
        elif tipo == "dedo":
            caixa = _dedo(saida, proibido, rng, u)
        elif tipo == "furos":
            caixa = _furos(saida, proibido, rng, u)
        elif tipo == "grampo":
            caixa = _grampo(saida, proibido, rng, u)
        elif tipo == "mancha":
            caixa = _mancha(saida, proibido, rng, u)
        elif tipo == "canto_dobrado":
            caixa = _canto_dobrado(saida, proibido, rng, u)
        elif tipo == "marca_texto":
            caixa = _marca_texto(saida, proibido, rng, u)
        else:
            alfa, cor = _desenho(tipo, H, W, rng)
            h, w = alfa.shape
            if h >= H or w >= W:
                continue
            for k in range(120 if tipo == "caneta" else 40):
                x, y = int(rng.integers(0, W - w)), int(rng.integers(0, H - h))
                if not _livre(proibido, x, y, w, h):
                    continue
                # a caneta circula, sublinha ou dá visto em TEXTO: o miolo do desenho precisa ter tinta (nas
                # últimas tentativas, aceita qualquer lugar livre)
                if tipo == "caneta" and k < 100:
                    miolo = saida[0][y + h // 4:y + 3 * h // 4, x + w // 6:x + 5 * w // 6].mean(axis=2)
                    if not miolo.size or (miolo < 140).mean() < 0.04:
                        continue
                _multiplicar(saida, x, y, alfa, cor)
                caixa = [x, y, x + w, y + h]
                break
        if caixa:
            info.append({"tipo": tipo, "nome": NOMES[tipo], "caixa": [int(v) for v in caixa]})
            if tipo not in ("dobra", "marca_dagua"):  # a próxima marca não cobre esta
                x0, y0, x1, y1 = caixa
                proibido[max(0, y0):y1, max(0, x0):x1] = True
                ocupado[max(0, y0):y1, max(0, x0):x1] = True
    return [np.clip(i + 0.5, 0, 255).astype(np.uint8) for i in saida], info


def descrever(info: list[dict] | None) -> str:
    return ", ".join(m["nome"] for m in info) if info else ""
