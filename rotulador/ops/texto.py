"""Escrever texto e substituir texto existente (apaga com PatchMatch e reescreve).

Coordenadas: (x, y) é o início da LINHA DE BASE do texto (âncora "ls" do Pillow),
em coordenadas contínuas da imagem.
"""

from __future__ import annotations

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
    negrito = num(p, "negrito", 0, 0, 10)
    rotacao = num(p, "rotacao", 0, -180, 180)
    desfoque = num(p, "desfoque", 0, 0, 10)
    antialias = bool(p.get("antialias", True))
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
    R = int(math.ceil(math.hypot(max(abs(l), abs(r * largura)), max(abs(t), abs(b))))) + int(negrito) + int(3 * desfoque) + 4
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
    if desfoque > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), desfoque)
    if not antialias:
        alpha = (alpha >= 0.5).astype(np.float32)
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


def apagar(img: np.ndarray, sel: Selecao, patch: int, semente: int, chave=None) -> np.ndarray:
    """Imagem inteira com a seleção apagada por PatchMatch (só a caixa da seleção muda)."""
    def calcular():
        mascara = sel.completa_bool()
        if mascara.all():
            raise ErroOperacao("a seleção cobre a imagem inteira")
        cheio = preencher(img, mascara, tamanho_patch=patch, semente=semente)
        y0, y1, x0, x1 = sel.bbox
        return compor(img[y0:y1, x0:x1], cheio[y0:y1, x0:x1], sel.alpha)

    recorte = _cache_apagado.obter(chave, calcular) if chave is not None else calcular()
    out = img.copy()
    y0, y1, x0, x1 = sel.bbox
    out[y0:y1, x0:x1] = recorte
    return out


def substituir_texto(img: np.ndarray, p: dict, sel: Selecao, chave_cache=None, **_) -> Resultado:
    exigir_selecao(sel)
    H, W = img.shape[:2]
    patch = inteiro(p, "patch", 7, 5, 11)
    semente = inteiro(p, "semente", 0, 0, 2**31 - 1)
    chave = None if chave_cache is None else (chave_cache, patch, semente)
    limpo = apagar(img, sel, patch, semente, chave)
    ty0, ty1, tx0, tx1, alpha = renderizar(p, H, W)
    sy0, sy1, sx0, sx1 = sel.bbox
    y0, y1, x0, x1 = min(sy0, ty0), max(sy1, ty1), min(sx0, tx0), max(sx1, tx1)
    novo = limpo[y0:y1, x0:x1].copy()
    a = np.zeros((y1 - y0, x1 - x0), np.float32)
    a[ty0 - y0:ty1 - y0, tx0 - x0:tx1 - x0] = alpha
    novo = compor(novo, cor(p), a)
    pegada = a > 0
    pegada[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] |= sel.alpha > 0
    return Resultado(y0, y1, x0, x1, novo, pegada, meta={"apagar": "patchmatch"})


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
