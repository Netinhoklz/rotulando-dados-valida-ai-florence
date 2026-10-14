"""Escrever texto e substituir texto existente (apaga com PatchMatch e reescreve).

Coordenadas: (x, y) é o início da LINHA DE BASE do texto (âncora "ls" do Pillow),
em coordenadas contínuas da imagem.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado
from .base import compor, cor, exigir_selecao, inteiro, num
from .preenchimento import preencher

PASTA_PROJETO = Path(__file__).resolve().parents[2] / "fontes"
_PREFERIDAS = ["Arial", "Arial Bold", "Calibri", "Calibri Bold", "Verdana", "Tahoma", "Segoe UI",
               "Helvetica", "Times New Roman", "Courier New", "Consolas", "Lucida Console", "OCR-B", "OCR A Extended"]
_lock_fontes = threading.Lock()
_fontes: dict[str, tuple[str, int]] | None = None


def _pastas_fontes() -> list[Path]:
    pastas = [PASTA_PROJETO]
    windir = os.environ.get("WINDIR", r"C:\Windows")
    pastas.append(Path(windir) / "Fonts")
    if os.environ.get("LOCALAPPDATA"):
        pastas.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    pastas += [Path("/usr/share/fonts"), Path.home() / ".fonts", Path("/Library/Fonts")]
    return [p for p in pastas if p.is_dir()]


def listar_fontes() -> dict[str, tuple[str, int]]:
    """{nome legível: (caminho, índice na coleção)}; fontes da pasta do projeto vencem."""
    global _fontes
    with _lock_fontes:
        if _fontes is not None:
            return _fontes
        achadas: dict[str, tuple[str, int]] = {}
        for pasta in _pastas_fontes():
            for arq in sorted(pasta.rglob("*")):
                if arq.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                    continue
                for idx in range(16 if arq.suffix.lower() == ".ttc" else 1):
                    try:
                        familia, estilo = ImageFont.truetype(str(arq), 12, index=idx).getname()
                    except OSError:
                        break
                    nome = familia if estilo in ("Regular", "Normal", "Book", None) else f"{familia} {estilo}"
                    achadas.setdefault(nome, (str(arq), idx))
        ordem = [n for n in _PREFERIDAS if n in achadas] + sorted(n for n in achadas if n not in _PREFERIDAS)
        _fontes = {n: achadas[n] for n in ordem}
        return _fontes


@lru_cache(maxsize=64)
def _fonte(nome: str, tamanho: float) -> ImageFont.FreeTypeFont:
    fontes = listar_fontes()
    if nome not in fontes:
        raise ErroOperacao(f"fonte não encontrada: {nome}")
    caminho, idx = fontes[nome]
    return ImageFont.truetype(caminho, tamanho, index=idx)


def fonte_padrao() -> str:
    fontes = listar_fontes()
    if not fontes:
        raise ErroOperacao("nenhuma fonte encontrada; coloque arquivos .ttf na pasta 'fontes'")
    return "Arial" if "Arial" in fontes else next(iter(fontes))


@lru_cache(maxsize=256)
def arquivo_fonte(nome: str) -> str | None:
    """Qual ARQUIVO desenha a fonte ``nome``: "arquivo.ttf:sha1[:12]" ("#índice" numa coleção .ttc). A mesma família
    pode vir de arquivos diferentes (a "Helvetica" da pasta do usuário, versões do Windows): o rótulo diz qual foi."""
    if nome not in listar_fontes():
        return None
    caminho, idx = listar_fontes()[nome]
    try:
        sha = hashlib.sha1(Path(caminho).read_bytes()).hexdigest()[:12]
    except OSError:
        return None
    return f"{Path(caminho).name}{f'#{idx}' if idx else ''}:{sha}"


def razao_maiuscula(nome: str) -> float:
    """Altura de maiúscula/dígito dividida pelo tamanho da fonte."""
    l, t, r, b = _fonte(nome, 100.0).getbbox("H0", anchor="ls")
    return max(0.2, -t / 100.0)


# ---------------------------------------------------------------------------
# renderização
# ---------------------------------------------------------------------------

def renderizar(p: dict, H: int, W: int):
    """Desenha o texto descrito em ``p``. Devolve (y0, y1, x0, x1, alpha) recortado na imagem."""
    texto = str(p.get("texto", ""))
    if not texto.strip():
        raise ErroOperacao("digite o texto")
    if len(texto) > 500 or "\n" in texto:
        raise ErroOperacao("texto de uma linha só, até 500 caracteres")
    nome = str(p.get("fonte") or fonte_padrao())
    tamanho = num(p, "tamanho", 24, 2, 1000)
    x, y = num(p, "x", 0, -W, 2 * W), num(p, "y", 0, -H, 2 * H)
    espacamento = num(p, "espacamento", 0, -50, 200)
    largura = num(p, "largura", 100, 30, 300) / 100.0
    negrito = num(p, "negrito", 0, -3, 10)  # negativo afina o traço (a fonte parecida é mais pesada que a do papel)
    rotacao = num(p, "rotacao", 0, -180, 180)
    desfoque = num(p, "desfoque", 0, 0, 10)
    antialias = bool(p.get("antialias", True))
    # rasterizador: cada programa desenha a letra de um jeito (a impressão digital do texto novo).
    # "gdi" imita o Windows sem ClearType: hinting (linha de base em pixel inteiro) e poucos tons de cinza.
    # "freetype" (padrão) é desenhado com superamostragem, sem hinting; "nativo" é o FreeType direto, com hinting
    # (o ClearType usa: no Windows ele também prende a vertical na grade).
    rasterizador = str(p.get("rasterizador") or "freetype")
    if rasterizador == "aliasado":
        antialias = False
    elif rasterizador == "gdi":
        x, y = float(round(x)), float(round(y))
    if antialias and rasterizador not in ("gdi", "nativo"):
        alpha, lx0, ly0 = _desenhar_super(nome, texto, tamanho, x, y, espacamento, largura, negrito, rotacao,
                                          int(3 * desfoque) + 2)
    else:
        alpha, lx0, ly0 = _desenhar_nativo(nome, texto, tamanho, x, y, espacamento, largura, negrito, rotacao,
                                           desfoque, antialias)
    if rasterizador in _SUAVIZACAO_PS:  # a suavização escolhida na camada de texto do Photoshop
        alpha = _SUAVIZACAO_PS[rasterizador](alpha)
    if desfoque > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), desfoque)
    if not antialias:
        alpha = (alpha >= 0.5).astype(np.float32)
    elif rasterizador == "gdi" and desfoque == 0:  # 4 tons de cinza e traço um pouco mais escuro, como o GDI
        alpha = np.round(np.clip(alpha, 0, 1) ** 0.8 * 4) / 4
    alpha[alpha < 1.0 / 512] = 0
    alpha *= num(p, "opacidade", 1.0, 0, 1)
    nz = alpha > 0
    if not nz.any():
        raise ErroOperacao("o texto ficou vazio")
    linhas, colunas = np.flatnonzero(nz.any(1)), np.flatnonzero(nz.any(0))
    y0, y1 = max(0, ly0 + int(linhas[0])), min(H, ly0 + int(linhas[-1]) + 1)
    x0, x1 = max(0, lx0 + int(colunas[0])), min(W, lx0 + int(colunas[-1]) + 1)
    if y1 <= y0 or x1 <= x0:
        raise ErroOperacao("o texto ficou fora da imagem")
    return y0, y1, x0, x1, np.ascontiguousarray(alpha[y0 - ly0:y1 - ly0, x0 - lx0:x1 - lx0])


SUPER = 4  # superamostragem do rasterizador padrão


def _afinar(a: np.ndarray, r: float) -> np.ndarray:
    """O traço do alfa (0..1) afinado ``r`` px de cada lado: erosão em tons de cinza com disco, e o r fracionário pela
    mistura das erosões inteiras vizinhas. O avesso do negrito isotrópico (o contorno de negrito/2 px)."""
    if r <= 0:
        return a

    def erodir(k: int) -> np.ndarray:
        return a if k <= 0 else cv2.erode(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    k0 = int(math.floor(r))
    f = r - k0
    return erodir(k0) if f < 1e-3 else (1 - f) * erodir(k0) + f * erodir(k0 + 1)

# Suavização da camada de texto do Photoshop (Nítida, Precisa, Forte, Suave; "Nenhuma" é o "aliasado"): a mesma
# cobertura superamostrada com outra curva de borda. Nítida e Precisa endurecem a transição papel→tinta, Forte
# engorda o traço, Suave espalha a borda. Cada pessoa escolhe uma, e ela fica no texto que digitou
_SUAVIZACAO_PS = {
    "ps_nitida": lambda a: np.clip((a - 0.5) * 1.35 + 0.5, 0, 1) * (a > 0),
    "ps_precisa": lambda a: np.clip((a - 0.5) * 1.18 + 0.5, 0, 1) * (a > 0),
    "ps_forte": lambda a: 1.0 - (1.0 - np.clip(a, 0, 1)) ** 1.6,
    "ps_suave": lambda a: np.clip(cv2.GaussianBlur(a, (0, 0), 0.35), 0, 1),
}


def _desenhar_super(nome: str, texto: str, tamanho: float, x: float, y: float, espacamento: float, largura: float,
                    negrito: float, rotacao: float, margem: int):
    """Texto desenhado SUPER vezes maior e reduzido pela média da área. Direto no tamanho final, o hinting do FreeType
    prendia as bordas horizontais na grade (o texto novo saía 1,4-1,7x mais nítido na horizontal que o do scan, que é
    igual nas duas direções) e o Pillow arredonda a âncora para o pixel inteiro; aqui a posição vale em 1/SUPER de pixel.
    Negrito isotrópico: um contorno de negrito/2 px em volta de cada traço (deslocar só na horizontal engrossava as
    hastes e deixava as barras finas). Negrito negativo afina: erosão de -negrito/2 px na resolução grande. Largura e
    rotação também na resolução grande. Devolve (alpha, x0, y0)."""
    S = SUPER
    fonte = _fonte(nome, round(tamanho * S * 4) / 4)
    traco = max(0.0, negrito) * S / 2
    if espacamento:
        # cada letra no avanço do texto até ela (o kerning continua) mais o espaçamento extra
        pos = [fonte.getlength(texto[:k]) + k * espacamento * S for k in range(len(texto))]
        caixas = [fonte.getbbox(c, anchor="ls", stroke_width=traco) for c in texto]
        l, r = min(px + c[0] for px, c in zip(pos, caixas)), max(px + c[2] for px, c in zip(pos, caixas))
        t, b = min(c[1] for c in caixas), max(c[3] for c in caixas)
    else:
        pos = None
        l, t, r, b = fonte.getbbox(texto, anchor="ls", stroke_width=traco)
    m = 2 * S + int(math.ceil(traco))
    l, t, r, b = math.floor(l) - m, math.floor(t) - m, math.ceil(r) + m, math.ceil(b) + m

    def desenhar(w: int, h: int, ax: int, ay: int) -> np.ndarray:
        tela = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(tela)
        for c, px in ([(texto, 0.0)] if pos is None else zip(texto, pos)):
            d.text((ax + px, ay), c, fill=255, font=fonte, anchor="ls", stroke_width=traco, stroke_fill=255)
        return np.asarray(tela, np.float32) / 255.0

    if largura == 1.0 and not rotacao:
        qx, qy = round(x * S), round(y * S)  # âncora na grade grande
        lx0, ly0 = (qx + l) // S, (qy + t) // S
        lx1, ly1 = -(-(qx + r) // S), -(-(qy + b) // S)
        grande = desenhar(S * (lx1 - lx0), S * (ly1 - ly0), qx - S * lx0, qy - S * ly0)
    else:
        # reto, com a âncora em (-l, -t), e depois esticado/girado em torno dela (contínuo -> centro de pixel do cv2)
        a = math.radians(-rotacao)
        L = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]) @ np.diag([largura, 1.0])
        cantos = L @ np.array([[l, r, l, r], [t, t, b, b]], float)
        lx0, ly0 = math.floor(x + cantos[0].min() / S), math.floor(y + cantos[1].min() / S)
        lx1, ly1 = math.ceil(x + cantos[0].max() / S), math.ceil(y + cantos[1].max() / S)
        reta = desenhar(r - l, b - t, -l, -t)
        Q = np.array([S * (x - lx0), S * (y - ly0)])
        M = np.hstack([L, (Q - 0.5 - L @ (np.array([-l, -t], float) - 0.5))[:, None]])
        grande = cv2.warpAffine(reta, M, (S * (lx1 - lx0), S * (ly1 - ly0)), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if negrito < 0:
        grande = _afinar(grande, -negrito * S / 2)
    alpha = cv2.resize(grande, (lx1 - lx0, ly1 - ly0), interpolation=cv2.INTER_AREA)
    return np.pad(alpha, margem), lx0 - margem, ly0 - margem


def _desenhar_nativo(nome: str, texto: str, tamanho: float, x: float, y: float, espacamento: float, largura: float,
                     negrito: float, rotacao: float, desfoque: float, antialias: bool):
    """O FreeType direto no tamanho final (hinting, âncora no pixel inteiro, negrito deslocado na horizontal, como o
    negrito sintético do Windows): GDI, aliasado e ClearType. Devolve (alpha, x0, y0) da camada."""
    fonte = _fonte(nome, round(tamanho * 4) / 4)

    # caixa do texto relativa à âncora (linha de base, início)
    if espacamento:
        avancos = [fonte.getlength(c) + espacamento for c in texto]
        comprimento = sum(avancos) - espacamento
    else:
        avancos = None
        comprimento = fonte.getlength(texto)
    l, t, r, b = fonte.getbbox(texto, anchor="ls")
    l, r = min(l, 0), max(r, comprimento)
    # o texto é desenhado SEM a escala horizontal e só depois comprimido/esticado: a camada precisa
    # caber o desenho original (com largura < 100% o fim do texto era cortado)
    alcance = max(abs(l), abs(r), abs(r * largura))
    R = int(math.ceil(math.hypot(alcance, max(abs(t), abs(b))))) + int(max(0.0, negrito)) + int(3 * desfoque) + 4
    lx0, ly0 = math.floor(x) - R, math.floor(y) - R
    ax, ay = x - lx0, y - ly0  # âncora dentro da camada
    camada = Image.new("L", (2 * R + 2, 2 * R + 2), 0)
    d = ImageDraw.Draw(camada)
    if not antialias:
        d.fontmode = "1"
    passos = [0.0] if negrito <= 0 else list(np.linspace(0, negrito, max(2, int(math.ceil(negrito * 2)) + 1)))
    for off in passos:
        if avancos is None:
            d.text((ax + off, ay), texto, fill=255, font=fonte, anchor="ls")
        else:
            px = ax + off
            for c, av in zip(texto, avancos):
                d.text((px, ay), c, fill=255, font=fonte, anchor="ls")
                px += av
    alpha = np.asarray(camada, np.float32) / 255.0
    if negrito < 0:
        alpha = _afinar(alpha, -negrito / 2)
    if largura != 1.0 or rotacao:
        # escala horizontal e rotação em torno da âncora (contínuo -> centro de pixel do cv2)
        cx, cy = ax - 0.5, ay - 0.5
        a = math.radians(-rotacao)
        S = np.array([[largura, 0, 0], [0, 1, 0], [0, 0, 1]])
        Rm = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
        T1 = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]])
        T2 = np.array([[1, 0, cx], [0, 1, cy], [0, 0, 1]])
        M = (T2 @ Rm @ S @ T1)[:2]
        alpha = cv2.warpAffine(alpha, M, (alpha.shape[1], alpha.shape[0]), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return alpha, lx0, ly0


def renderizar_cleartype(p: dict, H: int, W: int):
    """Como ``renderizar``, mas imitando o ClearType do Windows: o texto é desenhado com 3x a resolução
    horizontal e cada subpixel (R, G, B) recebe uma fração diferente da tinta, filtrada em 5 subpixels
    como o Windows faz. Devolve (y0, y1, x0, x1, alfa (h, w, 3)): as franjas coloridas nas bordas são
    a marca de texto digitado num programa do Windows e capturado da tela."""
    q = {k: v for k, v in p.items() if k not in ("rasterizador", "desfoque", "antialias")}
    q["rasterizador"] = "nativo"
    q["x"] = num(p, "x", 0, -W, 2 * W) * 3
    q["largura"] = num(p, "largura", 100, 30, 300) * 3
    q["rotacao"] = 0
    ay0, ay1, ax0, ax1, a = renderizar(q, H, 3 * W)
    alto = np.zeros((ay1 - ay0, 3 * W + 6), np.float32)
    alto[:, ax0 + 3:ax1 + 3] = a
    k = np.float32([1, 2, 3, 2, 1]) / 9.0  # filtro do ClearType sobre os subpixels
    alto = cv2.filter2D(alto, -1, k.reshape(1, -1), borderType=cv2.BORDER_CONSTANT)
    alto = alto[:, 3:3 + 3 * W]
    rgb = np.stack([alto[:, 0::3], alto[:, 1::3], alto[:, 2::3]], -1)  # (h, W, 3)
    rgb *= num(p, "opacidade", 1.0, 0, 1)
    rgb[rgb < 1.0 / 512] = 0
    nz = rgb.max(-1) > 0
    if not nz.any():
        raise ErroOperacao("o texto ficou vazio")
    colunas = np.flatnonzero(nz.any(0))
    x0, x1 = int(colunas[0]), int(colunas[-1]) + 1
    return ay0, ay1, x0, x1, np.ascontiguousarray(rgb[:, x0:x1])


def texto(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    H, W = img.shape[:2]
    y0, y1, x0, x1, alpha = renderizar(p, H, W)
    novo = compor(img[y0:y1, x0:x1], cor(p), alpha)
    return Resultado(y0, y1, x0, x1, novo, alpha > 0)


# ---------------------------------------------------------------------------
# substituir texto
# ---------------------------------------------------------------------------

class _CacheApagado:
    """Guarda o último 'apagado' para a prévia do texto novo não refazer o PatchMatch."""

    def __init__(self):
        self.lock = threading.Lock()
        self.chave = None
        self.valor = None

    def obter(self, chave, calcular):
        with self.lock:
            if self.chave == chave:
                return self.valor
        valor = calcular()
        with self.lock:
            self.chave, self.valor = chave, valor
        return valor


_cache_apagado = _CacheApagado()


def _fios_da_tabela(img: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> np.ndarray:
    """Máscara (H×W) dos fios de tabela perto da caixa [y0:y1, x0:x1]: traços horizontais mais compridos que
    3 alturas da caixa (e 30 px) e verticais mais altos que 1,5 altura (e 20 px), com 1 px de antialias."""
    from ..selecao import mascara_tinta

    H, W = img.shape[:2]
    h = max(1, y1 - y0)
    M = max(8, h)
    ay0, ay1, ax0, ax1 = max(0, y0 - M), min(H, y1 + M), max(0, x0 - 3 * M), min(W, x1 + 3 * M)
    t = mascara_tinta(img[ay0:ay1, ax0:ax1])
    reg = np.zeros(t.shape, bool)
    kh, kv = max(30, 3 * h), max(20, int(1.5 * h))
    if t.shape[1] > kh:
        reg |= cv2.morphologyEx(t, cv2.MORPH_OPEN, np.ones((1, kh), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0) > 0
    if t.shape[0] > kv:
        reg |= cv2.morphologyEx(t, cv2.MORPH_OPEN, np.ones((kv, 1), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0) > 0
    out = np.zeros((H, W), bool)
    if reg.any():
        out[ay0:ay1, ax0:ax1] = cv2.dilate(reg.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    return out


def _fundo_local(rec: np.ndarray, buraco: np.ndarray, altura: float) -> np.ndarray:
    """O fundo no ``buraco`` estimado pelo papel em volta: convolução normalizada (média gaussiana só dos pixels
    válidos) da escala mais fina que alcança cada ponto. Segue a luz que varia no papel (foto de celular), sem o
    degrau que a cor única do "chapado" deixava na borda, e sem grão (quem põe o grão é o acabamento)."""
    f = rec.astype(np.float32)
    v = (~buraco).astype(np.float32)
    out = f.copy()
    falta = buraco.copy()
    s = 2.0
    while falta.any() and s <= 4 * max(8.0, altura):
        den = cv2.GaussianBlur(v, (0, 0), s)
        num_ = cv2.GaussianBlur(f * v[..., None], (0, 0), s)
        ok = falta & (den >= 0.15)
        out[ok] = num_[ok] / den[ok][:, None]
        falta &= ~ok
        s *= 2
    if falta.any():
        out[falta] = np.median(f[~buraco], axis=0)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def apagar(img: np.ndarray, sel: Selecao, patch: int, semente: int, chave=None, modo: str = "patchmatch",
           so_papel: bool = False) -> np.ndarray:
    """Imagem inteira com a seleção apagada (só a caixa da seleção muda). ``modo``: o apagador
    (rotulador.ops.apagar.MODOS, ou "local": ``_fundo_local``); fora do PatchMatch, só a tinta do recorte em volta vira
    buraco também, para nenhuma letra vizinha ser copiada para dentro. ``so_papel``: o PatchMatch também (a seleção
    larga de uma pessoa encosta no rótulo, e o PatchMatch da imagem inteira colava pedaço dele no buraco)."""
    def calcular():
        mascara = sel.completa_bool()
        if mascara.all():
            raise ErroOperacao("a seleção cobre a imagem inteira")
        y0, y1, x0, x1 = sel.bbox
        # fios de tabela que passam pela seleção (célula de boleto): não são texto, ficam como estão. Reconstruídos,
        # viravam um tracejado debaixo do texto novo
        fios = _fios_da_tabela(img, y0, y1, x0, x1)
        mascara = mascara & ~fios
        if modo in (None, "patchmatch") and not so_papel:
            cheio = preencher(img, mascara, tamanho_patch=patch, semente=semente)
        else:
            from ..selecao import mascara_tinta
            from .apagar import reconstruir
            H, W = img.shape[:2]
            # o carimbo procura papel livre do tamanho da pincelada: entre as linhas não cabe, precisa de mais área
            M = int(max(16, (4.0 if modo in ("carimbo", "recuperar") else 1.5) * (y1 - y0)))
            ay0, ay1, ax0, ax1 = max(0, y0 - M), min(H, y1 + M), max(0, x0 - M), min(W, x1 + M)
            rec = img[ay0:ay1, ax0:ax1]
            if modo == "local":  # a média só pode ver papel: toda a tinta do recorte (fios inclusive) e o halo dela saem
                buraco = cv2.dilate(mascara_tinta(rec), np.ones((7, 7), np.uint8)).astype(bool) | mascara[ay0:ay1, ax0:ax1]
            else:
                buraco = (cv2.dilate(mascara_tinta(rec), np.ones((5, 5), np.uint8)).astype(bool)
                          | mascara[ay0:ay1, ax0:ax1]) & ~fios[ay0:ay1, ax0:ax1]
            if buraco.all():
                raise ErroOperacao("não há papel em volta para reconstruir o fundo")
            cheio = img.copy()
            cheio[ay0:ay1, ax0:ax1] = (_fundo_local(rec, buraco, float(y1 - y0)) if modo == "local"
                                       else reconstruir(rec, buraco, modo or "patchmatch", semente, float(y1 - y0),
                                        alvo=mascara[ay0:ay1, ax0:ax1]))
        cheio[fios] = img[fios]
        return compor(img[y0:y1, x0:x1], cheio[y0:y1, x0:x1], sel.alpha)

    recorte = _cache_apagado.obter(chave, calcular) if chave is not None else calcular()
    out = img.copy()
    y0, y1, x0, x1 = sel.bbox
    out[y0:y1, x0:x1] = recorte
    return out


def _calibrar_cor(fundo: np.ndarray, a: np.ndarray, c: np.ndarray, alvo: float) -> np.ndarray:
    """Cor que deixa o p10 da luminância (suavizada 0,8 px: o grão quase não pesa) da tinta nova (alfa > 0,5),
    desenhada com o alfa ``a`` sobre o ``fundo`` limpo, igual a ``alvo``. O contraste papel→tinta é escalado (o matiz
    da tinta fica); a 2ª volta corrige o corte em 0..255."""
    m = a > 0.5
    if not m.any():
        m = a >= 0.5 * float(a.max())
    if not m.any():
        return c
    f = fundo.astype(np.float32)
    lum = np.float32([0.299, 0.587, 0.114])
    papel = np.median(f[m], axis=0)
    c = np.asarray(c, np.float32)
    for _ in range(2):
        L = cv2.GaussianBlur((f * (1 - a[..., None]) + c * a[..., None]) @ lum, (0, 0), 0.8)
        med, pl = float(np.percentile(L[m], 10)), float(papel @ lum)
        if abs(pl - med) < 1:
            break
        c = np.clip(papel + (c - papel) * float(np.clip((pl - alvo) / (pl - med), 0.25, 4.0)), 0, 255)
    return c


def substituir_texto(img: np.ndarray, p: dict, sel: Selecao, chave_cache=None, tinta_alvo: float | None = None,
                     **_) -> Resultado:
    """Apaga a seleção e escreve o texto de ``p``. ``tinta_alvo``: p10 da luminância da tinta antiga; a cor é
    calibrada para a tinta nova (com o desfoque) ter o mesmo (``_calibrar_cor``) e vai para ``meta["cor"]``."""
    exigir_selecao(sel)
    H, W = img.shape[:2]
    patch = inteiro(p, "patch", 7, 5, 11)
    semente = inteiro(p, "semente", 0, 0, 2**31 - 1)
    modo = str(p.get("apagar") or "patchmatch")
    chave = None if chave_cache is None else (chave_cache, patch, semente, modo)
    limpo = apagar(img, sel, patch, semente, chave, modo, so_papel=bool(p.get("so_papel")))
    ty0, ty1, tx0, tx1, alpha = renderizar(p, H, W)
    sy0, sy1, sx0, sx1 = sel.bbox
    y0, y1, x0, x1 = min(sy0, ty0), max(sy1, ty1), min(sx0, tx0), max(sx1, tx1)
    novo = limpo[y0:y1, x0:x1].copy()
    a = np.zeros((y1 - y0, x1 - x0), np.float32)
    a[ty0 - y0:ty1 - y0, tx0 - x0:tx1 - x0] = alpha
    c, meta = cor(p), {"apagar": modo}
    if tinta_alvo is not None:
        c = np.round(_calibrar_cor(novo, a, c, float(tinta_alvo)))  # inteira: a cor do rótulo é a desenhada
        meta["cor"] = "#%02x%02x%02x" % tuple(int(round(float(v))) for v in c)
    novo = compor(novo, c, a)
    pegada = a > 0
    pegada[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] |= sel.alpha > 0
    return Resultado(y0, y1, x0, x1, novo, pegada, meta=meta)


def estimar(img: np.ndarray, sel: Selecao, fonte: str | None = None) -> dict:
    """Sugere posição, tamanho e cores do texto que está dentro da seleção."""
    exigir_selecao(sel)
    y0, y1, x0, x1 = sel.bbox
    rec = img[y0:y1, x0:x1].astype(np.float32)
    dentro = sel.alpha > 0.5
    borda = np.zeros_like(dentro)
    borda[[0, -1], :] = True
    borda[:, [0, -1]] = True
    fundo = np.median(rec[borda & dentro] if (borda & dentro).any() else rec.reshape(-1, 3), axis=0)
    lum = rec @ np.float32([0.299, 0.587, 0.114])
    lum_fundo = float(fundo @ np.float32([0.299, 0.587, 0.114]))
    d = np.abs(lum - lum_fundo)
    d[~dentro] = 0
    nome = fonte or fonte_padrao()
    padrao = {"x": float(x0 + 2), "y": float(y1 - 2), "tamanho": float(max(6, (y1 - y0) * 0.7)),
              "cor": "#000000", "cor_fundo": "#%02x%02x%02x" % tuple(int(v) for v in fundo), "fonte": nome,
              "achou_texto": False}
    if d.max() < 25:
        return padrao
    u8 = np.clip(d, 0, 255).astype(np.uint8)
    otsu, _ = cv2.threshold(u8[dentro], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    tinta = d > max(25.0, float(otsu))
    if tinta.sum() < 4:
        return padrao
    forte = tinta & (d >= np.percentile(d[tinta], 60))
    cor_texto = np.median(rec[forte], axis=0)
    densidade = tinta.sum(1)
    linhas = np.flatnonzero(densidade > 0)
    topo = int(linhas[0])
    # linha de base: última linha "densa" (descendentes como g, p, j são ralos)
    base = int(np.flatnonzero(densidade >= 0.2 * densidade.max())[-1]) + 1
    colunas = np.flatnonzero(tinta.any(0))
    altura_maiuscula = max(2, base - topo)
    tamanho = altura_maiuscula / razao_maiuscula(nome)
    l, _, _, _ = _fonte(nome, round(tamanho * 4) / 4).getbbox("H", anchor="ls")
    return {"x": float(x0 + colunas[0] - max(0, l)), "y": float(y0 + base), "tamanho": round(float(tamanho), 2),
            "cor": "#%02x%02x%02x" % tuple(int(round(v)) for v in cor_texto),
            "cor_fundo": "#%02x%02x%02x" % tuple(int(round(v)) for v in fundo),
            "fonte": nome, "achou_texto": True,
            "caixa_tinta": [int(x0 + colunas[0]), int(y0 + topo), int(x0 + colunas[-1] + 1), int(y0 + linhas[-1] + 1)]}
