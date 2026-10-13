"""Acabamento forense: casar na região editada os sinais que um falsificador PROFISSIONAL elimina e
que um perito (ou um detector) usa para achar a edição. Gerá-los em graus, com a máscara certa,
treina o detector a pegar a ponta difícil — a edição "que ninguém descobriria" a olho.

Três sinais, medidos SEMPRE no papel ORIGINAL (a própria região antes da edição, ou o anel em volta),
nunca dentro da própria edição:

1. iluminação / cor — o campo editado (corretivo, retângulo, recorte colado) tem cor chapada e não
   acompanha o gradiente lento de luz do papel. É o "retângulo liso demais" que os detectores
   comerciais apontam. ``casar_iluminacao`` ajusta o componente de baixa frequência do papel da região
   para cair no plano de iluminação que o papel da MESMA região tinha no original (o anel em volta só
   quando o original ali quase não tem papel).
2. grão / ruído — ``_com_ruido`` (ajustes) casa só a VARIÂNCIA, com ruído branco. O grão de um scan é
   correlacionado (tem espectro). ``casar_grao`` sintetiza ruído com o MESMO espectro do papel
   (resíduo de um passa-alta LINEAR do anel, fase randomizada), não branco: engana o detector de
   resíduo de ruído (PRNU/noiseprint).
3. nitidez / PSF — texto ou recorte novo mais nítido que o texto escaneado ao redor. ``casar_nitidez``
   mede a nitidez da vizinhança e desfoca a região só o suficiente para igualar (nunca inventa
   detalhe).

Níveis (``acabamento.pericia``), em ``aplicar``: ``cru`` nada; ``padrao`` o de hoje (variância do grão,
ruído branco em ``ajustes``) — ou o grão espectral, quando chamado com ``grao="espectral"``, e a iluminação,
com ``iluminacao=True`` (o papel foi reconstruído liso);
``profissional`` + iluminação + nitidez; ``forense`` + grão espectral + micro-ruído na costura. ``auto`` =
pelo nível da técnica e pelos hábitos do falsificador. Cada sinal casado fica na anotação (``pericia`` +
lista).
"""

from __future__ import annotations

import cv2
import numpy as np

from .selecao import mascara_tinta

NIVEIS = ("cru", "padrao", "profissional", "forense")
_LUMA = np.float32([0.299, 0.587, 0.114])
_CB = np.float32([-0.168736, -0.331264, 0.5])
_CR = np.float32([0.5, -0.418688, -0.081312])
_YCC_RGB = np.float32([[1.0, 0.0, 1.402], [1.0, -0.344136, -0.714136], [1.0, 1.772, 0.0]])  # (Y, Cb, Cr) -> RGB
_SIGMA_GRAO = 1.5  # passa-alta linear que separa o grão do fundo do papel: luma − gaussiana(σ)


def _recorte_global(c, res, margem: int):
    """(crop do original em volta da pegada, offset, pegada em coords do crop). Só leitura."""
    H, W = c.img.shape[:2]
    X0, Y0 = max(0, res.x0 - margem), max(0, res.y0 - margem)
    X1, Y1 = min(W, res.x1 + margem), min(H, res.y1 + margem)
    peg = np.zeros((Y1 - Y0, X1 - X0), bool)
    peg[res.y0 - Y0:res.y1 - Y0, res.x0 - X0:res.x1 - X0] = res.pegada
    return (X0, Y0, X1, Y1), peg


def _macio(pegada: np.ndarray, raio: float = 1.2) -> np.ndarray:
    """Máscara suave (0..1): não cria uma borda nova nítida ao casar os sinais (seria outro tell)."""
    m = pegada.astype(np.float32)
    return np.maximum(m, cv2.GaussianBlur(m, (0, 0), raio))


def _tinta_larga(img: np.ndarray) -> np.ndarray:
    """Tinta dilatada 3 px: exclui também o antialias em volta das letras, mais escuro que o papel, que
    puxaria o plano de luz para baixo e entraria no grão."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    return cv2.dilate(mascara_tinta(img), k) > 0


def _anel_papel(img: np.ndarray, pegada: np.ndarray, m: int) -> np.ndarray:
    """Papel (sem tinta nem antialias) num anel em volta da pegada: a referência da vizinhança."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * m + 1, 2 * m + 1))
    anel = (cv2.dilate(pegada.astype(np.uint8), k) > 0) & ~pegada
    return anel & ~_tinta_larga(img)


def casar_iluminacao(rec: np.ndarray, res, c, margem: int = 36):
    """Tira a 'mancha chapada': o papel da região volta ao plano de luz que tinha no ORIGINAL.

    Compara o plano (baixa frequência) do papel da própria região no original com o da composição, com a
    MESMA máscara de papel (tinta de cada uma, dilatada, excluída): papel igual dá delta zero. Extrapolar
    o anel da vizinhança criava um retângulo mais claro em volta do texto (o antialias das letras puxava
    o plano do anel para baixo); o anel só serve quando o original ali quase não tem papel. O delta fica
    em ±6 níveis e entra com borda suave; a tinta (alta frequência) não muda. Devolve (rec_novo, casou?)."""
    (X0, Y0, X1, Y1), peg = _recorte_global(c, res, margem)
    orig = (c.base if c.base is not None else c.img)[Y0:Y1, X0:X1]
    cheio = c.img[Y0:Y1, X0:X1].astype(np.float32)  # a composição: ``rec`` por cima da imagem atual
    ry0, ry1 = res.y0 - Y0, res.y1 - Y0
    rx0, rx1 = res.x0 - X0, res.x1 - X0
    cheio[ry0:ry1, rx0:rx1][res.pegada] = rec[res.pegada]
    papel_orig = peg & ~_tinta_larga(orig)
    papel_reg = peg & ~_tinta_larga(np.clip(cheio + 0.5, 0, 255).astype(np.uint8))
    h, w = peg.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base_cols = np.stack([xx, yy, np.ones_like(xx)], -1).reshape(-1, 3)

    def plano(valido: np.ndarray, valores: np.ndarray):
        A = base_cols[valido.ravel()]
        if A.shape[0] < 30:
            return None
        coef, *_ = np.linalg.lstsq(A, valores.reshape(-1, 3)[valido.ravel()], rcond=None)
        return (base_cols @ coef).reshape(h, w, 3)

    if papel_orig.sum() >= 60:
        comum = papel_orig & papel_reg
        alvo, atual = plano(comum, orig.astype(np.float32)), plano(comum, cheio)
    else:  # o original ali quase não tem papel (tudo tinta): extrapola o papel da vizinhança
        alvo = plano(_anel_papel(c.img[Y0:Y1, X0:X1], peg, margem), cheio)
        atual = plano(papel_reg, cheio)
    if alvo is None or atual is None:
        return rec, False
    delta = np.clip((alvo - atual)[ry0:ry1, rx0:rx1], -6.0, 6.0)
    if np.abs(delta).max() < 0.5:  # já no mesmo plano de luz
        return rec, False
    suave = _macio(res.pegada)[..., None]
    return rec + delta * suave, True


def _grao(luma: np.ndarray, papel: np.ndarray) -> tuple[float, np.ndarray]:
    """(desvio do grão, resíduo) do papel: resíduo do passa-alta LINEAR (luma − gaussiana σ 1,5), que
    preserva o espectro do grão (a mediana 3x3 de ``ajustes`` é não linear e deixa o resíduo 'azulado').
    A gaussiana é normalizada pela máscara: a tinta excluída não vaza no fundo do papel vizinho."""
    p = papel.astype(np.float32)
    peso = np.maximum(cv2.GaussianBlur(p, (0, 0), _SIGMA_GRAO), 1e-3)
    residuo = (luma - cv2.GaussianBlur(luma * p, (0, 0), _SIGMA_GRAO) / peso) * p
    v = residuo[papel]
    if v.size < 40:
        return 0.0, residuo
    a = np.abs(v)
    return float(v[a <= np.percentile(a, 98)].std()), residuo


def _grao_robusto(plano: np.ndarray, papel: np.ndarray) -> float:
    """O desvio do ``_grao`` medido pela mediana dos desvios absolutos (na mesma escala: o desvio sem os 2% maiores
    é 0,935 σ num ruído normal). Na região editada sobra o halo do texto novo na máscara do papel — com a pegada
    na forma do texto, ele é boa parte dos pixels e inflava o desvio ("o grão já é parecido")."""
    _, residuo = _grao(plano, papel)
    v = residuo[papel]
    if v.size < 40:
        return 0.0
    return float(1.4826 * 0.935 * np.median(np.abs(v - np.median(v))))


def _sintetizar(residuo: np.ndarray, anel: np.ndarray, semente: int) -> np.ndarray:
    """Ruído com o espectro do papel: randomiza a FASE do resíduo do anel (preserva o espectro de
    potência, logo a correlação espacial, mas descola do conteúdo). O anel é esparso (zero fora dele):
    a amplitude é normalizada pela cobertura. O passa-alta atenua o grão abaixo de ~0,1 ciclo/px (fator
    1−G da gaussiana); a atenuação é desfeita com ganho limitado a 4x — sem isso o ruído sai 'azulado'
    (lag-1 0,14-0,20 abaixo do papel; com a compensação, < 0,06)."""
    g = residuo * anel
    F = np.fft.rfft2(g)
    fy = np.fft.fftfreq(g.shape[0])[:, None]
    fx = np.fft.rfftfreq(g.shape[1])[None, :]
    G = np.exp(-2 * np.pi ** 2 * _SIGMA_GRAO ** 2 * (fx ** 2 + fy ** 2))
    amp = np.abs(F) / np.maximum(1.0 - G, 0.25) / np.sqrt(anel.mean())
    rng = np.random.default_rng(semente)
    fase = np.exp(1j * rng.uniform(0, 2 * np.pi, F.shape))
    ruido = np.fft.irfft2(amp * fase, s=g.shape).astype(np.float32)
    return ruido - ruido.mean()


def casar_grao(rec: np.ndarray, res, c, semente: int, margem: int = 30):
    """Acrescenta à região o grão que FALTA, com o MESMO espectro do papel original em volta (não ruído branco),
    na luma e no croma: o papel de uma foto/scan colorido tem grão em Cb/Cr também (o de um documento em tons de
    cinza, não, e aí nada entra no croma). Só nos pixels da pegada — o que foi reconstruído ou escrito: com o
    apagador só-tinta a pegada tem a forma do texto e o papel intacto em volta já tem o grão dele. Pula só quando
    o papel em volta é liso de verdade (desvio < 0,05: PDF, papel digital) ou a região já tem o grão dele; o
    limite antigo (0,4) deixava um retângulo de variância zero num papel de grão 0,37. Devolve (rec_novo, casou?)."""
    base = c.base if c.base is not None else c.img
    (X0, Y0, X1, Y1), peg = _recorte_global(c, res, margem)
    orig = base[Y0:Y1, X0:X1].astype(np.float32)
    anel = _anel_papel(base[Y0:Y1, X0:X1], peg, margem)
    if anel.sum() < 80:
        return rec, False
    cheio = c.img[Y0:Y1, X0:X1].astype(np.float32)
    ry0, ry1 = res.y0 - Y0, res.y1 - Y0
    rx0, rx1 = res.x0 - X0, res.x1 - X0
    cheio[ry0:ry1, rx0:rx1][res.pegada] = rec[res.pegada]
    # o papel da região: sem a tinta (1 px de folga); o halo que sobra fica com o estimador robusto
    tinta = cv2.dilate(mascara_tinta(np.clip(cheio + 0.5, 0, 255).astype(np.uint8)), np.ones((3, 3), np.uint8)) > 0
    papel_reg = peg & ~tinta
    ruidos = []
    for k, eixo in enumerate((_LUMA, _CB, _CR)):
        s_ref, residuo = _grao(orig @ eixo, anel)
        falta = float(np.sqrt(max(0.0, s_ref ** 2 - _grao_robusto(cheio @ eixo, papel_reg) ** 2)))
        if s_ref < 0.05 or falta < 0.3 * s_ref:  # papel liso, ou o grão já é parecido
            ruidos.append(None)
            continue
        ruido = _sintetizar(residuo, anel, semente + k)
        s_r, _ = _grao(ruido, np.ones(ruido.shape, bool))  # pelo MESMO estimador: a região fica com s_ref
        ruidos.append(ruido * (falta / s_r) if s_r >= 1e-3 else None)
    if all(r is None for r in ruidos):
        return rec, False
    ycc = np.stack([np.zeros(peg.shape, np.float32) if r is None else r for r in ruidos], -1)
    rr = (ycc @ _YCC_RGB.T)[ry0:ry1, rx0:rx1] * res.pegada[..., None]
    return rec + rr, True


def _nitidez(luma: np.ndarray, mascara: np.ndarray) -> float:
    """Energia de borda média (Σ|∇|² / Σ|∇|): alta = nítido, baixa = borrado. Independe de contraste."""
    gy, gx = np.gradient(luma.astype(np.float32))
    g = np.hypot(gx, gy)
    sel = mascara & (g > 2.0)
    if sel.sum() < 30:
        return 0.0
    v = g[sel]
    return float((v * v).sum() / v.sum())


def casar_nitidez(rec: np.ndarray, res, c, margem: int = 24):
    """Se a região está mais NÍTIDA que o texto escaneado em volta, desfoca só o suficiente para
    igualar (nunca aguça: não inventa detalhe). Devolve (rec_novo, casou?)."""
    (X0, Y0, X1, Y1), peg = _recorte_global(c, res, margem)
    img = c.img[Y0:Y1, X0:X1]
    tinta_viz = mascara_tinta(img).astype(bool) & ~peg
    if tinta_viz.sum() < 40:
        return rec, False
    alvo = _nitidez(img @ _LUMA, tinta_viz)
    luma_rec = rec @ _LUMA
    tinta_reg = mascara_tinta(np.clip(rec + 0.5, 0, 255).astype(np.uint8)).astype(bool)
    atual = _nitidez(luma_rec, tinta_reg)
    if alvo <= 0 or atual <= alvo * 1.12:  # já tão macio quanto a vizinhança
        return rec, False
    melhor = None  # o σ que deixa a nitidez MAIS PERTO do alvo, sem passar do ponto (nunca abaixo de 0,95·alvo)
    for sg in np.linspace(0.3, 1.6, 14):
        borrado = cv2.GaussianBlur(rec, (0, 0), float(sg))
        n = _nitidez(borrado @ _LUMA, tinta_reg)
        if n >= 0.95 * alvo and (melhor is None or abs(n - alvo) < abs(melhor[1] - alvo)):
            melhor = (borrado, n)
    if melhor is None:
        return rec, False
    suave = _macio(res.pegada, 0.8)[..., None]
    return rec * (1 - suave) + melhor[0] * suave, True


def _borda_micro_ruido(rec: np.ndarray, res, c, semente: int):
    """Micro-ruído na costura: um anel fino em volta da tinta nova recebe grão fraco, para não deixar
    uma borda limpa demais entre o editado e o papel (o detector de costura procura isso)."""
    tinta = mascara_tinta(np.clip(rec + 0.5, 0, 255).astype(np.uint8)).astype(np.uint8)
    anel = (cv2.dilate(tinta, np.ones((3, 3), np.uint8)) - tinta).astype(bool) & res.pegada
    if anel.sum() < 20:
        return rec, False
    rng = np.random.default_rng(semente ^ 0x5bd1e995)
    r = rng.normal(0, 1.1, rec.shape[:2]).astype(np.float32)[..., None]
    out = rec.copy()
    out[anel] = rec[anel] + r[anel]
    return out, True


def aplicar(rec: np.ndarray, res, c, nivel: str, semente: int, grao: str | None = None,
            iluminacao: bool = False) -> tuple[np.ndarray, list[str]]:
    """Casa os sinais do ``nivel`` na composição ``rec`` (float, coords da pegada). Devolve
    (rec_novo, sinais_casados). ``grao="espectral"`` casa o grão espectral já no ``padrao`` (em vez do
    ruído branco de ``ajustes``); sem ele, só o ``forense`` casa. ``iluminacao`` casa a luz já no ``padrao``
    (papel reconstruído liso — chapado, difusão — que não segue o gradiente da foto). Cada passo pode não fazer
    nada (já casado) sem erro, e nenhum derruba a edição."""
    feitos: list[str] = []
    if nivel not in ("padrao", "profissional", "forense"):
        return rec, feitos
    if iluminacao and nivel == "padrao":
        try:
            rec, ok = casar_iluminacao(rec, res, c)
            if ok:
                feitos.append("iluminação do papel casada")
        except Exception:  # noqa: BLE001 - acabamento nunca derruba a edição
            pass
    if nivel in ("profissional", "forense"):
        try:
            rec, ok = casar_iluminacao(rec, res, c)
            if ok:
                feitos.append("iluminação do papel casada")
        except Exception:  # noqa: BLE001 - acabamento nunca derruba a edição
            pass
        try:
            rec, ok = casar_nitidez(rec, res, c)
            if ok:
                feitos.append("nitidez igualada à vizinhança")
        except Exception:  # noqa: BLE001
            pass
    if nivel == "forense" or grao == "espectral":
        try:
            rec, ok = casar_grao(rec, res, c, semente)
            if ok:
                feitos.append("grão espectral do papel")
        except Exception:  # noqa: BLE001
            pass
    if nivel == "forense":
        try:
            rec, ok = _borda_micro_ruido(rec, res, c, semente)
            if ok:
                feitos.append("micro-ruído na costura")
        except Exception:  # noqa: BLE001
            pass
    return rec, feitos
