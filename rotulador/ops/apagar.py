"""Apagadores: como cada ferramenta real reconstrói o papel onde havia texto.

Um fornecedor só (o PatchMatch) vira a impressão digital do gerador: o detector decora a textura do
apagamento em vez da fraude (OSTF). Cada ferramenta apaga de um jeito e deixa um rastro próprio:

  patchmatch  preenchimento inteligente (Photoshop Content-Aware Fill, GIMP Resynthesizer): copia
              retalhos do papel vizinho; textura nítida, retalhos repetidos;
  telea / ns  inpainting por difusão do OpenCV (borracha de app de celular, "remover objeto" simples):
              papel liso, sem grão, com a cor difundida das bordas;
  chapado     balde do Paint: a cor mediana do papel, lisa;
  borrao      pincel de recuperação simples / dedo: média ponderada do entorno;
  carimbo     carimbo de clonagem: um retalho de papel vizinho copiado inteiro (grão duplicado);
  recuperar   pincel de recuperação (Photoshop, GIMP "Heal"): a TEXTURA de um retalho vizinho com a cor e a luz
              do entorno do buraco (a borda não aparece, o grão vem de outro lugar).

``reconstruir(rec, buraco, modo, semente, altura)`` devolve o recorte com o buraco preenchido. O
chamador passa como buraco TODA a tinta do recorte (dilatada) mais o que quer apagar, e só aproveita
os pixels que pediu, então as letras vizinhas nunca "vazam" para dentro em nenhum modo.
"""

from __future__ import annotations

import random

import cv2
import numpy as np

from ..selecao import mascara_tinta
from .preenchimento import preencher

MODOS = ("patchmatch", "telea", "ns", "chapado", "borrao", "carimbo", "recuperar")


def reconstruir(rec: np.ndarray, buraco: np.ndarray, modo: str, semente: int, altura: float,
                alvo: np.ndarray | None = None) -> np.ndarray:
    """``rec`` RGB uint8; ``buraco`` bool (tudo que não pode ser copiado); ``altura`` = altura da letra (px);
    ``alvo`` (bool, opcional): o que o chamador vai aproveitar (o carimbo só clona aí; sem ele, todo o buraco)."""
    if modo not in MODOS:
        modo = "patchmatch"
    rec = np.ascontiguousarray(rec)
    if modo in ("telea", "ns"):
        # a difusão puxa a cor da borda do buraco para dentro: com o halo cinza do antialias logo ali, sobravam
        # manchinhas no miolo das letras redondas ("O", "M"). A folga de 2 px tira o halo da borda
        m = cv2.dilate(buraco.astype(np.uint8), np.ones((5, 5), np.uint8)) * 255
        raio = max(2, int(round(0.15 * altura)))
        return cv2.inpaint(rec, m, raio, cv2.INPAINT_TELEA if modo == "telea" else cv2.INPAINT_NS)
    if modo == "chapado":
        cor = np.median(rec[~buraco].reshape(-1, 3), axis=0)
        out = rec.copy()
        out[buraco] = np.clip(cor + 0.5, 0, 255).astype(np.uint8)
        return out
    if modo == "borrao":
        s = max(1.5, 0.35 * altura)
        k = (~buraco).astype(np.float32)
        num = cv2.GaussianBlur(rec.astype(np.float32) * k[..., None], (0, 0), s)
        den = cv2.GaussianBlur(k, (0, 0), s)
        suave = num / np.maximum(den, 1e-3)[..., None]
        cor = np.median(rec[~buraco].reshape(-1, 3), axis=0)  # fundo de um buraco largo demais: a cor do papel
        suave[den < 0.02] = cor
        out = rec.copy()
        out[buraco] = np.clip(suave[buraco] + 0.5, 0, 255).astype(np.uint8)
        return out
    if modo in ("carimbo", "recuperar"):
        fonte = _carimbo_por_passadas(rec, buraco, semente, buraco if alvo is None else alvo & buraco, altura)
        return fonte if modo == "carimbo" else _recuperar(rec, buraco, fonte, altura)
    return preencher(rec, buraco, tamanho_patch=7, margem=None, semente=semente)


def _origem(buraco_f: np.ndarray, sub: np.ndarray, y0: int, x0: int, preferido, rng: random.Random):
    """Deslocamento (dy, dx) que leva o bloco ``sub`` (canto em y0, x0) para papel livre: nenhum pixel dele cai no
    buraco. O mais perto do ``preferido`` (o deslocamento da pincelada anterior: o carimbo "alinhado" do Photoshop
    mantém a origem andando junto) ou, sem ele, um dos mais perto do lugar, sorteado. None se não há."""
    h, w = sub.shape
    H, W = buraco_f.shape
    if h > H or w > W:
        return None
    soma = cv2.matchTemplate(buraco_f, sub.astype(np.float32), cv2.TM_CCORR)  # tinta/buraco sob o bloco em cada lugar
    ys, xs = np.nonzero(soma < 0.5)
    if not ys.size:
        return None
    dy, dx = ys - y0, xs - x0
    if preferido is not None:
        k = int(np.argmin((dy - preferido[0]) ** 2 + (dx - preferido[1]) ** 2))
    else:
        d = dy.astype(np.float64) ** 2 + dx.astype(np.float64) ** 2
        perto = np.argsort(d)[:8]
        k = int(perto[rng.randrange(len(perto))])
    return int(dy[k]), int(dx[k])


def _carimbo(rec: np.ndarray, buraco: np.ndarray, semente: int, alvo: np.ndarray | None = None) -> np.ndarray:
    """Carimbo (clone): o papel de outro lugar copiado TAL QUAL sobre o que se apaga, em pinceladas — faixas verticais
    ao longo do ``alvo`` (largura de ~0,6 da altura dele), cada uma de um lugar de papel livre (sem tinta nem buraco),
    mantendo o deslocamento da faixa anterior enquanto ele servir. Faixa sem origem é dividida ao meio (até 6 px) e,
    no fim, o que sobrar vai para o PatchMatch. O grão fica o do papel de verdade, repetido (o rastro do carimbo).
    Antes era um retalho só do tamanho de TODO o buraco (que inclui a tinta do recorte inteiro) e nunca cabia: o
    carimbo virava PatchMatch em 16 de 16 campos."""
    alvo = buraco if alvo is None else alvo
    ys, xs = np.nonzero(alvo)
    out = rec.copy()
    if not ys.size:
        return out
    rng = random.Random(semente)
    bf = buraco.astype(np.float32)
    by0, by1, bx0, bx1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    sw = max(6, int(round(0.6 * (by1 - by0))))
    falta = np.zeros_like(alvo)
    pilha = [(by0, by1, x, min(bx1, x + sw)) for x in range(bx0, bx1, sw)]
    pref = None
    while pilha:
        y0, y1, x0, x1 = pilha.pop(0)
        sub = alvo[y0:y1, x0:x1]
        if not sub.any():
            continue
        o = _origem(bf, sub, y0, x0, pref, rng)
        if o is None:
            if max(y1 - y0, x1 - x0) > 6:  # divide no lado maior e tenta as metades
                if y1 - y0 >= x1 - x0:
                    m = (y0 + y1) // 2
                    pilha[:0] = [(y0, m, x0, x1), (m, y1, x0, x1)]
                else:
                    m = (x0 + x1) // 2
                    pilha[:0] = [(y0, y1, x0, m), (y0, y1, m, x1)]
            else:
                falta[y0:y1, x0:x1] |= sub
            continue
        pref = o
        dy, dx = o
        out[y0:y1, x0:x1][sub] = rec[y0 + dy:y1 + dy, x0 + dx:x1 + dx][sub]
    if falta.any():
        out = np.where(falta[..., None], preencher(rec, buraco, tamanho_patch=7, margem=None, semente=semente), out)
    return out


def _carimbo_por_passadas(rec: np.ndarray, buraco: np.ndarray, semente: int, alvo: np.ndarray,
                          altura: float) -> np.ndarray:
    """O carimbo como a mão usa: passadas ao longo da linha (``gesto.pinceladas``), cada uma com a SUA origem — o
    Alt+clique num papel livre perto; no modo "alinhado" (40%) a origem anda junto com a passada anterior —, copiando
    disco a disco com a borda macia do pincel (dureza 0,7: as emendas em anel do carimbo de verdade). Onde as
    passadas não cobriram por inteiro, as faixas de ``_carimbo`` (e o PatchMatch, no fim) completam."""
    from .. import gesto

    ys, xs = np.nonzero(alvo)
    out = rec.astype(np.float32)
    if not ys.size:
        return rec.copy()
    rng = random.Random(semente ^ 0xC10E)
    H, W = alvo.shape
    raio = max(3.0, rng.uniform(0.55, 0.8) * max(4.0, float(altura)))
    by0, by1, bx0, bx1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    bf = buraco.astype(np.float32)
    cobre = np.zeros((H, W), np.float32)
    pref = None
    for _rodada in range(2):  # a segunda volta só onde a primeira não cobriu (a pessoa repassa)
        falta = alvo & (cobre < 0.97)
        if not falta.any():
            break
        ys2, xs2 = np.nonzero(falta)
        for traco in gesto.pinceladas(int(xs2.min()), int(ys2.min()), int(xs2.max()) + 1, int(ys2.max()) + 1, raio,
                                      rng):
            tq = gesto.toques(traco, raio, rng, espacamento=0.25)
            a = gesto.alfa_toques((H, W), tq, dureza=0.7)
            pe = a > 0.02
            if not pe.any():
                continue
            py, px = np.nonzero(pe)
            y0, y1, x0, x1 = int(py.min()), int(py.max()) + 1, int(px.min()), int(px.max()) + 1
            o = _origem(bf, pe[y0:y1, x0:x1], y0, x0, pref if rng.random() < 0.4 else None, rng)
            if o is None:
                continue
            pref = o
            dy, dx = o
            sub = a[y0:y1, x0:x1, None]
            out[y0:y1, x0:x1] = out[y0:y1, x0:x1] * (1 - sub) + rec[y0 + dy:y1 + dy, x0 + dx:x1 + dx] * sub
            cobre[y0:y1, x0:x1] = 1 - (1 - cobre[y0:y1, x0:x1]) * (1 - a[y0:y1, x0:x1])
    final = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    resto = alvo & (cobre < 0.97)  # o que a borda macia deixou meio coberto: ainda tem tinta antiga embaixo
    if resto.any():
        final[resto] = _carimbo(rec, buraco, semente, resto)[resto]
    return final


def _recuperar(rec: np.ndarray, buraco: np.ndarray, fonte: np.ndarray, altura: float) -> np.ndarray:
    """Pincel de recuperação: o detalhe fino (passa-alta) do retalho ``fonte`` somado ao tom do entorno do buraco
    (média gaussiana só do papel em volta, a escala que alcança cada ponto)."""
    s = max(2.0, 0.35 * altura)
    f = fonte.astype(np.float32)
    textura = f - cv2.GaussianBlur(f, (0, 0), s)
    # o tom só do PAPEL: o fio de tabela e a tinta em volta não são buraco (não se apagam), mas puxavam o tom para o
    # cinza (mancha embaixo do nome no quadro do boleto)
    papel = ~buraco & ~(cv2.dilate(mascara_tinta(rec), np.ones((5, 5), np.uint8)) > 0)
    if papel.sum() < 0.05 * buraco.size:
        papel = ~buraco
    # do retalho vem só o grão (a amplitude do grão do papel): um traço que estivesse nele não é carregado junto
    g = textura[papel]
    lim = 3.0 * float(g.std()) + 1.0 if g.size else 4.0
    textura = np.clip(textura, -lim, lim)
    v = papel.astype(np.float32)
    tom = rec.astype(np.float32).copy()
    falta = buraco.copy()
    sig = s
    while falta.any() and sig <= 8 * max(8.0, altura):
        den = cv2.GaussianBlur(v, (0, 0), sig)
        num = cv2.GaussianBlur(rec.astype(np.float32) * v[..., None], (0, 0), sig)
        ok = falta & (den >= 0.15)
        tom[ok] = num[ok] / den[ok][:, None]
        falta &= ~ok
        sig *= 2
    if falta.any():
        tom[falta] = np.median(rec[papel].reshape(-1, 3), axis=0)
    out = rec.copy()
    out[buraco] = np.clip(tom[buraco] + textura[buraco] + 0.5, 0, 255).astype(np.uint8)
    return out
