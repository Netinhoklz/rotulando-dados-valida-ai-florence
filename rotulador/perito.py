"""Perito: mede as "pistas fáceis" que uma edição deixa — as que um perito (ou um detector de resíduo)
usa para achar a adulteração — comparando o interior da região editada com um anel de papel em volta.

Serve para (1) auditar o dataset (rotulador/datasheet.py: pistas por técnica e nível), (2) mostrar ao
operador onde a edição manual dele ficou óbvia (``mapa``, no editor) e (3) dar uma nota ao gerador para
escolher a variante mais difícil (``avaliar``, interno ou por um serviço externo).

O interior é medido na EDITADA; o anel, na ORIGINAL (o papel como era: outra edição ou o que o canal
espalhou em volta não entram na referência). Cada medida sai None quando não dá para medir (pouco papel
ou pouca tinta no lado correspondente): nunca um número inventado.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from . import documentos, exportar
from .registro import SPLITS
from .selecao import mascara_tinta
from .sessao import ErroOperacao

_LUMA = np.float32([0.299, 0.587, 0.114])
_K7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))  # tinta dilatada 3 px fica fora do "papel"
MIN_PAPEL, MIN_TINTA = 40, 30  # pixels mínimos para uma medida valer
# nota: cada medida vira 0..1 com 1 - exp(-(x/escala)^2) (começa quadrática: 10% de diferença quase não
# conta; satura) e as disponíveis se combinam como OU probabilístico, com o peso = contribuição máxima
ESCALAS = {"grao": 0.5, "espectro": 0.3, "nitidez": 0.4, "tom": 8.0, "blocos": 0.5}
PESOS = {"grao": 0.9, "espectro": 0.6, "nitidez": 0.8, "tom": 0.8, "blocos": 0.5}
MEDIDAS = tuple(ESCALAS)
_NOME_VERSAO = re.compile(r"^[0-9a-f]{8,64}__v\d{2,}$")


# ---------------------------------------------------------------------- medidas de um lado
def _luma(img: np.ndarray) -> np.ndarray:
    return img.astype(np.float32) @ _LUMA


def _residuo(luma: np.ndarray) -> np.ndarray:
    """Passa-alta linear: o grão do papel sem o conteúdo."""
    return luma - cv2.GaussianBlur(luma, (0, 0), 1.5)


def _grao(residuo: np.ndarray, papel: np.ndarray) -> float | None:
    """Desvio do resíduo no papel (sem os 2% mais fortes: poeira, pontos)."""
    v = residuo[papel]
    if v.size < MIN_PAPEL:
        return None
    a = np.abs(v)
    return float(v[a <= np.percentile(a, 98)].std())


def _lag1(residuo: np.ndarray, papel: np.ndarray) -> float | None:
    """Autocorrelação lag-1 do resíduo no papel (média de horizontal e vertical): 0 = ruído branco,
    > 0 = grão correlacionado (scanner, JPEG). Papel chapado não tem espectro: None."""
    v = residuo[papel]
    if v.size < MIN_PAPEL or v.std() < 0.1:  # chapado (grão real tem > 0,5 nível): sem espectro
        return None
    r = residuo - v.mean()
    pares = [(r[:, :-1] * r[:, 1:])[papel[:, :-1] & papel[:, 1:]], (r[:-1] * r[1:])[papel[:-1] & papel[1:]]]
    if min(p.size for p in pares) < MIN_PAPEL // 2:
        return None
    return float(np.mean([p.mean() for p in pares]) / v.var())


def _nitidez(g: np.ndarray, tinta: np.ndarray) -> float | None:
    """Energia de borda (Σ|∇|² / Σ|∇|) nos pixels de tinta com |∇| > 2: alta = nítido. Independe do contraste."""
    if tinta.sum() < MIN_TINTA:
        return None
    v = g[tinta & (g > 2.0)]
    if v.size < 10 or v.sum() <= 0:
        return None
    return float((v * v).sum() / v.sum())


def _grade(luma: np.ndarray, papel: np.ndarray, x_abs: int, y_abs: int) -> float | None:
    """Energia na grade 8x8 do JPEG no papel: diferença média entre vizinhos através das fronteiras de
    bloco / fora delas (1 = sem grade). ``x_abs``/``y_abs``: posição do recorte na página (a grade é da página)."""
    if papel.sum() < MIN_PAPEL:
        return None
    dx, dy = np.abs(np.diff(luma, axis=1)), np.abs(np.diff(luma, axis=0))
    px, py = papel[:, :-1] & papel[:, 1:], papel[:-1] & papel[1:]
    bx = ((np.arange(dx.shape[1]) + x_abs + 1) % 8 == 0)[None, :]  # par (x, x+1) com x+1 no início de um bloco
    by = ((np.arange(dy.shape[0]) + y_abs + 1) % 8 == 0)[:, None]
    fronteira = np.concatenate([dx[px & bx], dy[py & by]])
    resto = np.concatenate([dx[px & ~bx], dy[py & ~by]])
    if fronteira.size < 16 or resto.size < 16 or resto.mean() < 1e-3:  # papel chapado: não há grade que medir
        return None
    return float(fronteira.mean() / resto.mean())


def _lado(img: np.ndarray, m: np.ndarray, faixa: np.ndarray, x_abs: int, y_abs: int) -> dict:
    """Estatísticas de um lado (interior ou anel ``m``) numa imagem recortada. ``faixa``: borda da região,
    fora do papel (o blur do resíduo mistura os dois lados ali)."""
    tinta = mascara_tinta(img).astype(bool)
    papel = m & ~faixa & ~(cv2.dilate(tinta.astype(np.uint8), _K7) > 0)
    tinta &= m
    luma = _luma(img)
    gy, gx = np.gradient(luma)
    residuo = _residuo(luma)
    return {"n_papel": int(papel.sum()), "n_tinta": int(tinta.sum()), "grao": _grao(residuo, papel),
            "lag1": _lag1(residuo, papel), "nitidez": _nitidez(np.hypot(gx, gy), tinta),
            "tom": img[papel].reshape(-1, 3).astype(np.float64).mean(0) if papel.sum() >= MIN_PAPEL else None,
            "grade": _grade(luma, papel, x_abs, y_abs)}


def _razao(a, b) -> float | None:
    return None if a is None or b is None or b <= 1e-6 else float(a / b)


# ---------------------------------------------------------------------- API
def medir(original: np.ndarray, editada: np.ndarray, regiao: np.ndarray, margem: int | None = None) -> dict:
    """Pistas da edição: o interior de ``regiao`` (bool, a pegada da edição) na ``editada`` contra um anel de
    papel em volta na ``original`` (largura ``margem``; padrão 1,5x a altura da região, mínimo 16 px).

    - ``grao``: razão do desvio do resíduo passa-alta (imagem − GaussianBlur σ=1,5, em luminância) no papel
      de dentro / do anel. 1 = mesmo grão; ~0 = chapado; > 1 = mais ruidoso que o papel.
    - ``espectro``: autocorrelação lag-1 desse resíduo dentro − fora. 0 = mesmo espectro (ruído branco colado
      em papel escaneado dá negativo).
    - ``nitidez``: razão da energia de borda (Σ|∇|²/Σ|∇| na tinta com |∇| > 2) dentro / anel. 1 = igual;
      > 1 = texto novo mais nítido que o escaneado.
    - ``tom``: maior diferença, por canal, da média do papel dentro − anel (níveis). 0 = mesma cor.
    - ``blocos``: razão da energia na grade 8x8 do JPEG dentro / anel. 1 = mesma grade.
    - ``n_papel_dentro``/``n_papel_fora``/``n_tinta_dentro``/``n_tinta_fora``: pixels usados; ``margem``.
    Papel = sem tinta (``mascara_tinta``), com a tinta dilatada 3 px excluída e fora de uma faixa de 3 px de
    cada lado da borda da região (o blur mistura os dois lados ali). Menos de 40 px de papel ou 30 de tinta
    no lado correspondente: a medida sai None. Tudo float/int nativo (JSON)."""
    regiao = np.asarray(regiao).astype(bool)
    if original.shape != editada.shape or regiao.shape != editada.shape[:2]:
        raise ValueError("original, editada e regiao precisam ter o mesmo tamanho")
    out = {k: None for k in MEDIDAS} | {"n_papel_dentro": 0, "n_papel_fora": 0, "n_tinta_dentro": 0,
                                        "n_tinta_fora": 0, "margem": 0}
    if not regiao.any():
        return out
    ys, xs = np.flatnonzero(regiao.any(1)), np.flatnonzero(regiao.any(0))
    if margem is None:
        margem = max(16, int(round(1.5 * (ys[-1] + 1 - ys[0]))))
    margem = max(1, int(margem))
    H, W = regiao.shape
    Y0, Y1 = max(0, ys[0] - margem - 2), min(H, ys[-1] + 1 + margem + 2)  # +2: folga para o blur na borda
    X0, X1 = max(0, xs[0] - margem - 2), min(W, xs[-1] + 1 + margem + 2)
    dentro = regiao[Y0:Y1, X0:X1]
    u8 = dentro.astype(np.uint8)
    # o anel até ``margem`` px da região pela transformada de distância (linear no tamanho): a dilatação com um
    # núcleo elíptico de 2·margem+1 px custa margem² por pixel e levava 128 s numa edição grande (uma camada movida
    # no editor) — o "Salvar" ficava ~1 min parado medindo as pistas
    anel = (cv2.distanceTransform((1 - u8).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE) <= margem) & ~dentro
    faixa = (cv2.dilate(u8, _K7) > 0) & ~(cv2.erode(u8, _K7) > 0)  # 3 px de cada lado da borda da região
    d = _lado(editada[Y0:Y1, X0:X1], dentro, faixa, X0, Y0)
    f = _lado(original[Y0:Y1, X0:X1], anel, faixa, X0, Y0)
    out.update({"grao": _razao(d["grao"], f["grao"]),
                "espectro": None if d["lag1"] is None or f["lag1"] is None else float(d["lag1"] - f["lag1"]),
                "nitidez": _razao(d["nitidez"], f["nitidez"]),
                "tom": None if d["tom"] is None or f["tom"] is None else float(np.abs(d["tom"] - f["tom"]).max()),
                "blocos": _razao(d["grade"], f["grade"]),
                "n_papel_dentro": d["n_papel"], "n_papel_fora": f["n_papel"],
                "n_tinta_dentro": d["n_tinta"], "n_tinta_fora": f["n_tinta"], "margem": margem})
    return out


def nota(medidas: dict) -> float | None:
    """0..1: quanto a edição salta aos olhos por essas medidas. ~0 = invisível para elas; > 0,5 = pista forte
    (um retângulo chapado dá > 0,7). Cada medida disponível vira x ≥ 0 (|ln grao|, |espectro|, |ln nitidez|,
    tom, |ln blocos|), satura com 1 − exp(−(x/escala)²) (ESCALAS: grão x1,5 ≈ 0,5 e x2 ≈ 0,85; tom 8 níveis ≈
    0,6; nitidez x1,5 ≈ 0,6) e as disponíveis se combinam como OU probabilístico com PESOS: uma pista forte
    basta, várias fracas somam. None não entra; sem nenhuma medida válida, None (não mediu ≠ invisível)."""
    p, medidas_validas = 1.0, 0
    for k, escala in ESCALAS.items():
        v = medidas.get(k)
        if v is None or not np.isfinite(v):
            continue
        medidas_validas += 1
        x = abs(float(v)) if k in ("espectro", "tom") else abs(float(np.log(min(max(float(v), 1e-3), 1e3))))
        p *= 1.0 - PESOS[k] * (1.0 - float(np.exp(-(x / escala) ** 2)))
    return round(1.0 - p, 4) if medidas_validas else None


def _desvio(v: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """0..1 por bloco: quanto o valor (em log) foge da mediana da página, em MADs (piso de 15%); NaN = 0."""
    lv = np.log(v + 1e-3)
    base = lv[ref & np.isfinite(lv)]
    if base.size < 4:
        base = lv[np.isfinite(lv)]
    if base.size == 0:
        return np.zeros(v.shape, np.float32)
    med = float(np.median(base))
    mad = max(1.4826 * float(np.median(np.abs(base - med))), 0.15)
    s = 1.0 - np.exp(-(np.abs(lv - med) / mad / 3.0) ** 2)
    return np.where(np.isfinite(s), s, 0.0).astype(np.float32)


def mapa(original: np.ndarray | None, editada: np.ndarray, regiao: np.ndarray | None = None,
         bloco: int = 16) -> np.ndarray:
    """Mapa de calor LOCAL (float32 H x W, 0..1) de onde a editada foge das estatísticas da própria página,
    por blocos de ``bloco`` px: grão do papel (resíduo passa-alta) e nitidez da tinta, cada um contra a
    mediana e o MAD da página (com ``regiao``, só os blocos fora dela são a referência), suavizado. Com
    ``original``, soma a diferença absoluta normalizada entre as duas (só para destacar onde se mexeu).
    É para o operador VER onde a edição dele ficou óbvia."""
    H, W = editada.shape[:2]
    bloco = max(4, int(bloco))
    luma = _luma(editada)
    residuo = _residuo(luma)
    tinta = mascara_tinta(editada).astype(bool)
    papel = ~(cv2.dilate(tinta.astype(np.uint8), _K7) > 0)
    gy, gx = np.gradient(luma)
    g = np.hypot(gx, gy)
    borda = tinta & (g > 2.0)
    Hb, Wb = -(-H // bloco), -(-W // bloco)

    def soma(a, m):  # soma de ``a`` nos pixels ``m``, por bloco
        z = np.zeros((Hb * bloco, Wb * bloco), np.float64)
        z[:H, :W] = np.where(m, a, 0.0)
        return z.reshape(Hb, bloco, Wb, bloco).sum((1, 3))

    n_p = soma(1.0, papel)
    with np.errstate(invalid="ignore", divide="ignore"):
        media = soma(residuo, papel) / n_p
        grao_b = np.sqrt(np.maximum(soma(residuo * residuo, papel) / n_p - media ** 2, 0.0))
        grao_b[n_p < bloco * bloco / 4] = np.nan
        nit_b = soma(g * g, borda) / soma(g, borda)
        nit_b[soma(1.0, borda) < 8] = np.nan
    ref = np.ones((Hb, Wb), bool) if regiao is None else soma(1.0, np.asarray(regiao).astype(bool)) == 0
    calor = 1.0 - (1.0 - _desvio(grao_b, ref)) * (1.0 - _desvio(nit_b, ref))
    calor = cv2.resize(calor.astype(np.float32), (Wb * bloco, Hb * bloco), interpolation=cv2.INTER_LINEAR)[:H, :W]
    calor = cv2.GaussianBlur(calor, (0, 0), bloco / 2)
    if original is not None:
        dif = np.abs(editada.astype(np.float32) - original.astype(np.float32)).max(2)
        dif = cv2.GaussianBlur(dif, (0, 0), 2.0)
        if dif.max() > 0:
            calor = calor + 0.3 * dif / dif.max()
    return np.clip(calor, 0.0, 1.0).astype(np.float32)


def colorir(m: np.ndarray) -> np.ndarray:
    """RGBA uint8 do tamanho do mapa, para desenhar por cima do documento (drawImage em 0,0): transparente onde
    não há pista (alfa 0 até 0,1: a variação natural entre blocos do papel) e laranja → vermelho com alfa
    proporcional ao valor (máx. 170) onde há."""
    v = np.clip(m, 0.0, 1.0).astype(np.float32)
    rgb = np.float32([255, 140, 0]) * (1 - v[..., None]) + np.float32([220, 0, 30]) * v[..., None]
    alfa = np.clip((v - 0.1) / 0.9, 0.0, 1.0) * 170.0
    return np.dstack([(rgb + 0.5).astype(np.uint8), (alfa + 0.5).astype(np.uint8)])


def _png_b64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if img.ndim == 3 else img)
    return base64.b64encode(buf.tobytes()).decode()


def _externo(url: str, timeout: float, original, editada, regiao) -> dict:
    corpo = json.dumps({"original": _png_b64(original), "editada": _png_b64(editada),
                        "regiao": _png_b64(np.asarray(regiao).astype(bool).astype(np.uint8) * 255)}).encode()
    req = urllib.request.Request(url, corpo, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return {"medidas": {}, **resp, "nota": float(resp["nota"]), "fonte": "externo"}


def avaliar(original: np.ndarray, editada: np.ndarray, regiao: np.ndarray, cfg: dict | None = None) -> dict:
    """{"nota" (None = nenhuma medida válida), "medidas", "fonte": "interno" | "externo"}. Com ``cfg["url"]``,
    manda POST JSON {"original", "editada", "regiao"} (PNG em base64) e espera {"nota": float, ...}; qualquer
    falha de rede ou de resposta cai no perito interno (``medir`` + ``nota``) com o motivo em ``erro``."""
    cfg = cfg or {}
    erro = None
    if cfg.get("url"):
        try:
            return _externo(str(cfg["url"]), float(cfg.get("timeout", 30)), original, editada, regiao)
        except Exception as e:  # noqa: BLE001 - serviço fora, resposta estranha: o interno responde
            erro = f"{type(e).__name__}: {e}"
    m = medir(original, editada, regiao)
    out = {"nota": nota(m), "medidas": m, "fonte": "interno"}
    if erro:
        out["erro"] = erro
    return out


def carregar_versao(raiz_saida: Path, split: str, nome: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """(original, editada, máscara de região bool, anotação) de uma versão salva."""
    if split not in SPLITS or not _NOME_VERSAO.match(str(nome)):
        raise ErroOperacao("versão inválida")
    raiz_split = (Path(raiz_saida) / split).resolve()
    arq = raiz_split / "anotacoes" / f"{nome}.json"
    if not arq.is_file():
        raise ErroOperacao("versão não encontrada")
    anot = json.loads(arq.read_text(encoding="utf-8"))
    arqs = anot.get("arquivos") or {}
    dpi = (anot.get("fonte") or {}).get("dpi_pdf") or documentos.DPI_PADRAO
    imgs = []
    for k in ("original", "editada", "mascara_regiao"):
        c = (raiz_split / str(arqs.get(k) or "")).resolve()
        if not arqs.get(k) or not c.is_relative_to(raiz_split) or not c.is_file():
            raise ErroOperacao(f"arquivo '{k}' da versão não encontrado")
        imgs.append(documentos.carregar(c, 0, dpi))
    original, editada, regiao = imgs
    regiao = regiao[..., 0] > 127
    if original.shape != editada.shape or regiao.shape != editada.shape[:2]:
        raise ErroOperacao("original, editada e máscara de região com tamanhos diferentes")
    return original, editada, regiao, anot


def medir_versao(raiz_saida: Path, split: str, nome: str) -> list[dict]:
    """Medidas e nota de cada edição de uma versão salva (a caixa ``bbox`` da edição recorta a parte dela na
    máscara de região), com técnica, nível, perícia e categoria para agrupar."""
    original, editada, regiao, anot = carregar_versao(raiz_saida, split, nome)
    out = []
    for e in exportar.edicoes_de_anotacao(anot):
        reg = regiao
        if e.get("bbox"):
            x0, y0, x1, y1 = (max(0, int(v)) for v in e["bbox"])
            reg = np.zeros_like(regiao)
            reg[y0:y1, x0:x1] = regiao[y0:y1, x0:x1]
        m = medir(original, editada, reg)
        out.append({"ordem": e.get("ordem"), "tecnica": e.get("tecnica"), "nivel": e.get("nivel"),
                    "pericia": e.get("pericia") or "auto", "categoria": e.get("categoria"), "bbox": e.get("bbox"),
                    "nota": nota(m), "medidas": m})
    return out
