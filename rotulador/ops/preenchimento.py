"""Preenchimento sensível ao conteúdo, no estilo do "Content-Aware Fill" do Photoshop.

Como funciona
-------------
A região marcada é reconstruída copiando *patches* (quadradinhos de 7x7 pixels)
parecidos do entorno:

1. Monta-se uma pirâmide (cada nível com metade da resolução). Ao reduzir, a
   borda do buraco é preenchida pela média dos vizinhos conhecidos, então o
   buraco encolhe a cada nível até sumir.
2. Do nível mais grosseiro para o mais fino, repete-se algumas vezes:
   - PatchMatch (Barnes et al., 2009): para cada patch que toca o buraco, acha o
     patch mais parecido fora dele (propagação entre vizinhos + busca aleatória);
   - votação (Wexler, Shechtman e Irani, 2007): cada pixel do buraco recebe as
     cores que os patches escolhidos propõem para ele e fica com a moda
     (mean-shift), o que mantém a textura nítida em vez de borrada.
3. A última votação de cada nível já escreve no nível seguinte usando os pixels
   da resolução fina, e as correspondências sobem junto como ponto de partida.

Tudo é vetorizado em numpy, sem nenhuma outra dependência.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np

_BLOCO = 4096  # patches/pixels processados por vez (limita o pico de memória)
_THREADS = min(8, os.cpu_count() or 1)  # o numpy solta o GIL nas operações pesadas
_H2 = np.float32(2 * 20.0 ** 2)  # largura do mean-shift: ~20 níveis de cor
# peso do voto em função da distância normalizada (tabela do PatchMatch de younesse)
_SIMILARIDADE = np.array([1.0, 0.99, 0.96, 0.83, 0.38, 0.11, 0.02, 0.005, 0.0006, 0.0001, 0.0])
# nos níveis que herdam correspondências do nível anterior só é preciso ajuste local
_RAIO_HERDADO = 32

_pool: ThreadPoolExecutor | None = None


def _em_blocos(funcao, n: int) -> None:
    """Chama funcao(a, b) para cada bloco [a, b) de 0..n, em paralelo quando compensa."""
    global _pool
    inicios = range(0, n, _BLOCO)
    if n <= _BLOCO or _THREADS <= 1:
        for a in inicios:
            funcao(a, min(n, a + _BLOCO))
        return
    if _pool is None:
        _pool = ThreadPoolExecutor(_THREADS, thread_name_prefix="preenchimento")
    list(_pool.map(lambda a: funcao(a, min(n, a + _BLOCO)), inicios))


# ---------------------------------------------------------------------------
# máscaras e pirâmide
# ---------------------------------------------------------------------------

def _dilatar_linhas(m: np.ndarray, r: int) -> np.ndarray:
    """Dilatação ao longo do eixo 0 por [-r, r], com O(log r) passadas de OR."""
    m = m.copy()
    alcance = 0
    while alcance < r:
        passo = min(alcance + 1, r - alcance)  # nunca abre buraco: cobre [-(alcance+passo), +]
        antes = m.copy()
        m[passo:] |= antes[:-passo]
        m[:-passo] |= antes[passo:]
        alcance += passo
    return m


def dilatar(mascara: np.ndarray, r: int) -> np.ndarray:
    """Dilatação por um quadrado de lado 2r+1."""
    m = np.asarray(mascara, bool)
    if r <= 0:
        return m.copy()
    return _dilatar_linhas(_dilatar_linhas(m, r).T, r).T.copy()


def _filtrar_pares(a: np.ndarray, eixo: int) -> np.ndarray:
    """Filtro [1 5 10 10 5 1] ao longo de um eixo, calculado só nas posições pares.

    Equivale a filtrar tudo e depois pegar [::2], sem gastar memória com o que seria jogado fora.
    """
    a = np.moveaxis(a, eixo, 0)
    n = a.shape[0]
    m = (n + 1) // 2
    saida = np.zeros((m,) + a.shape[1:], np.float32)
    for i, peso in enumerate((1, 5, 10, 10, 5, 1)):
        d = i - 2  # saida[t] += peso * a[2t + d], quando 0 <= 2t + d < n
        t0, t1 = (1 - d) // 2 if d < 0 else 0, min(m, (n - 1 - d) // 2 + 1)
        if t1 > t0:
            saida[t0:t1] += peso * a[2 * t0 + d: 2 * t1 + d - 1: 2]
    return np.ascontiguousarray(np.moveaxis(saida, 0, eixo))


def _reduzir(img: np.ndarray, buraco: np.ndarray):
    """Metade da resolução usando só pixels conhecidos; o buraco encolhe.

    Zera os pixels do buraco em ``img`` (ninguém usa esses valores: eles sempre são
    reescritos antes de serem lidos), o que evita uma cópia da imagem inteira.
    """
    img[buraco] = 0
    k = (~buraco).astype(np.float32)
    num = _filtrar_pares(_filtrar_pares(img, 0), 1)
    den = _filtrar_pares(_filtrar_pares(k, 0), 1)
    return num / np.maximum(den, 1e-6)[..., None], den == 0


def _preencher_suave(img: np.ndarray, buraco: np.ndarray) -> np.ndarray:
    """Interpolação push-pull: espalha as cores da borda para dentro do buraco."""
    if not buraco.any():
        return img
    if buraco.all():
        return np.full_like(img, 127.5)
    menor, buraco_menor = _reduzir(img, buraco)
    menor = _preencher_suave(menor, buraco_menor)
    subido = menor.repeat(2, 0).repeat(2, 1)[: img.shape[0], : img.shape[1]]
    saida = img.copy()
    saida[buraco] = subido[buraco]
    return saida


def _indices_int32(mascara: np.ndarray, bloco: int = 1 << 22) -> np.ndarray:
    """np.flatnonzero em int32, por partes (evita o vetor int64 temporário inteiro)."""
    plano = mascara.ravel()
    partes = [np.flatnonzero(plano[a:a + bloco]).astype(np.int32) + np.int32(a)
              for a in range(0, plano.size, bloco)]
    return np.concatenate(partes) if partes else np.zeros(0, np.int32)


class _Nivel:
    """Um nível da pirâmide: imagem, buraco, alvos (patches a resolver) e fontes."""

    def __init__(self, img: np.ndarray, buraco: np.ndarray, r: int):
        # contígua: o resolvedor escreve em img.reshape(-1, 3), que só é visão nesse caso
        assert img.flags.c_contiguous, "imagem do nível precisa ser contígua"
        self.img, self.buraco, self.r = img, buraco, r
        self.H, self.W = H, W = buraco.shape
        interior = np.zeros_like(buraco)
        interior[r:H - r, r:W - r] = True
        toca = dilatar(buraco, r)
        # índices em int32 (cabem: a imagem tem bem menos de 2 bilhões de pixels)
        self.alvos = _indices_int32(toca & interior)  # centros de patches que tocam o buraco
        self.ty, self.tx = np.divmod(self.alvos, np.int32(W))
        self.eh_fonte = (interior & ~toca).ravel()   # patches inteiramente conhecidos
        self.fontes = _indices_int32(self.eh_fonte)

        dy, dx = (d.ravel().astype(np.int32) for d in np.mgrid[-r:r + 1, -r:r + 1])
        self.off = dy * W + dx  # deslocamentos do patch em índice plano
        # a distância usa só as casas "pretas" do xadrez: metade do custo, mesma qualidade
        self.off_dist = self.off[(dy + dx) % 2 == 0]
        # índice do alvo por posição, com moldura de r pixels para não sair da grade
        self.Wm = W + 2 * r
        self.off_m = dy * self.Wm + dx
        self.idx_alvo = np.full((H + 2 * r) * self.Wm, -1, np.int32)
        self.idx_alvo[(self.ty + r) * self.Wm + self.tx + r] = np.arange(self.alvos.size)


# ---------------------------------------------------------------------------
# PatchMatch + votação
# ---------------------------------------------------------------------------

class _Resolvedor:
    def __init__(self, nv: _Nivel, rng: np.random.Generator):
        self.nv, self.rng = nv, rng
        self.pixels = nv.img.reshape(-1, 3)  # visão: escrever aqui altera nv.img

    def distancias(self, k: np.ndarray, s: np.ndarray) -> np.ndarray:
        """Soma das diferenças ao quadrado entre o patch do alvo k e o da fonte s."""
        nv, px, off = self.nv, self.pixels, self.nv.off_dist
        saida = np.empty(k.size, np.float32)

        def bloco(a, b):
            dif = px.take(nv.alvos[k[a:b], None] + off, axis=0)
            dif -= px.take(s[a:b, None] + off, axis=0)
            saida[a:b] = np.einsum("ijk,ijk->i", dif, dif)

        _em_blocos(bloco, k.size)
        return saida

    def _tentar(self, k, cy, cx, s, D):
        """Troca a correspondência dos alvos k pelo candidato (cy, cx) quando melhora."""
        nv, r = self.nv, self.nv.r
        c = np.clip(cy, r, nv.H - r - 1) * nv.W + np.clip(cx, r, nv.W - r - 1)
        ok = nv.eh_fonte[c] & (c != s[k])
        k, c = k[ok], c[ok]
        if k.size:
            d = self.distancias(k, c)
            melhor = d < D[k]
            s[k[melhor]], D[k[melhor]] = c[melhor], d[melhor]

    def patchmatch(self, s, D, iteracoes: int, raio_max: int, palpite_global: bool = True):
        nv, rng, r = self.nv, self.rng, self.nv.r
        n = s.size
        todos = np.arange(n)
        for it in range(iteracoes):
            # propagação: o vizinho que casou bem sugere o mesmo deslocamento
            direcoes = ((0, 1), (1, 0), (0, -1), (-1, 0))
            for dy, dx in direcoes if it % 2 == 0 else direcoes[::-1]:
                viz = nv.idx_alvo[(nv.ty - dy + r) * nv.Wm + nv.tx - dx + r]
                k = np.flatnonzero(viz >= 0)
                sy, sx = np.divmod(s[viz[k]], nv.W)
                self._tentar(k, sy + dy, sx + dx, s, D)
            # busca aleatória em raios cada vez menores
            raio = raio_max
            while raio >= 1:
                sy, sx = np.divmod(s, nv.W)
                self._tentar(todos, sy + rng.integers(-raio, raio + 1, n),
                             sx + rng.integers(-raio, raio + 1, n), s, D)
                raio //= 2
            # um palpite em qualquer lugar ajuda a sair de mínimos locais
            if palpite_global:
                sy, sx = np.divmod(rng.choice(nv.fontes, n), nv.W)
                self._tentar(todos, sy, sx, s, D)

    def votar(self, s, D, fino: _Nivel | None = None):
        """Reconstrói o buraco a partir dos patches escolhidos.

        Com ``fino``, escreve no nível de resolução dobrada usando os pixels dele.
        """
        nv = self.nv
        t = D / np.float32(nv.off_dist.size * 3 * 255.0 ** 2)
        peso = np.interp(t * 100, np.arange(11), _SIMILARIDADE, right=0.0).astype(np.float32) + 1e-6
        destino = nv if fino is None else fino
        px = destino.img.reshape(-1, 3)
        conhecido = ~destino.buraco.ravel()
        pixels_buraco = np.flatnonzero(destino.buraco.ravel())

        # blocos independentes: cada um escreve só nos seus pixels e lê só pixels conhecidos
        def bloco(a, b):
            q = pixels_buraco[a:b]
            qy, qx = np.divmod(q, destino.W)
            if fino is not None:
                Qy, Qx = qy, qx
                qy, qx = np.minimum(Qy // 2, nv.H - 1), np.minimum(Qx // 2, nv.W - 1)
            # para cada pixel, os patches que o cobrem: (n, tamanho do patch)
            k = nv.idx_alvo[((qy + nv.r) * nv.Wm + qx + nv.r)[:, None] - nv.off_m]
            valido = k >= 0
            k = np.where(valido, k, 0)
            origem = s[k] + nv.off
            if fino is not None:  # mesmo ponto na resolução fina
                sy, sx = np.divmod(origem, nv.W)
                sy, sx = 2 * sy + (Qy - 2 * qy)[:, None], 2 * sx + (Qx - 2 * qx)[:, None]
                valido &= (sy < fino.H) & (sx < fino.W)
                origem = np.minimum(sy, fino.H - 1) * fino.W + np.minimum(sx, fino.W - 1)
                valido &= conhecido[origem]
            cores = px.take(origem, axis=0)
            w = np.where(valido, peso[k], np.float32(0))
            soma = w.sum(1)
            tem = soma > 0
            m = np.einsum("ijk,ij->ik", cores, w) / np.maximum(soma, 1e-9)[:, None]
            for _ in range(2):  # mean-shift: puxa para a cor dominante, não para a média
                dif = cores - m[:, None]
                ww = w * np.exp(-np.einsum("ijk,ijk->ij", dif, dif) / _H2)
                sw = ww.sum(1)
                ok = sw > 1e-9
                m[ok] = np.einsum("ijk,ij->ik", cores[ok], ww[ok]) / sw[ok, None]
            px[q[tem]] = m[tem]

        _em_blocos(bloco, pixels_buraco.size)


def _subir_correspondencias(nv: _Nivel, s: np.ndarray, fino: _Nivel, rng) -> np.ndarray:
    """Leva o mapa de correspondências para o nível de resolução dobrada."""
    r = fino.r
    cy = np.clip(fino.ty // 2, r, nv.H - r - 1)
    cx = np.clip(fino.tx // 2, r, nv.W - r - 1)
    k = nv.idx_alvo[(cy + r) * nv.Wm + cx + r]
    novo = rng.choice(fino.fontes, fino.alvos.size)
    tem = np.flatnonzero(k >= 0)
    sy, sx = np.divmod(s[k[tem]], nv.W)
    sy = np.clip(2 * sy + fino.ty[tem] - 2 * cy[tem], r, fino.H - r - 1)
    sx = np.clip(2 * sx + fino.tx[tem] - 2 * cx[tem], r, fino.W - r - 1)
    c = sy * fino.W + sx
    ok = fino.eh_fonte[c]
    novo[tem[ok]] = c[ok]
    return novo


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def preencher(
    imagem: np.ndarray,
    mascara: np.ndarray,
    tamanho_patch: int = 7,
    margem: int | None = -1,
    expandir: int = 0,
    semente: int = 0,
) -> np.ndarray:
    """Preenche a região ``mascara`` de ``imagem`` com conteúdo parecido do entorno.

    imagem         array HxWx3 uint8 (RGB).
    mascara        array HxW bool; True marca o que deve ser preenchido.
    tamanho_patch  lado do patch (ímpar). Maior = segue mais a estrutura, menos detalhe.
    margem         área de amostragem, em pixels ao redor da seleção.
                   -1 = automática (proporcional à seleção); None = imagem inteira.
    expandir       dilata a seleção antes (pega o contorno/halo do objeto).
    semente        muda a sorte da busca aleatória: outra semente, outra variação.
    """
    imagem = np.asarray(imagem)
    if imagem.ndim != 3 or imagem.shape[2] != 3:
        raise ValueError("a imagem precisa ser RGB (altura x largura x 3)")
    mascara = np.asarray(mascara, bool)
    if mascara.shape != imagem.shape[:2]:
        raise ValueError("a máscara precisa ter o mesmo tamanho da imagem")
    mascara = dilatar(mascara, int(expandir))
    if not mascara.any():
        return imagem.copy()
    if mascara.all():
        raise ValueError("a seleção cobre a imagem inteira; não sobra nada para copiar")

    r = max(1, int(tamanho_patch) // 2)
    p = 2 * r + 1

    # recorte: a área de amostragem ao redor da seleção
    H, W = mascara.shape
    linhas, colunas = np.flatnonzero(mascara.any(1)), np.flatnonzero(mascara.any(0))
    y0, y1, x0, x1 = linhas[0], linhas[-1] + 1, colunas[0], colunas[-1] + 1
    if margem is None:
        y0, y1, x0, x1 = 0, H, 0, W
    else:
        m = int(max(4 * p, 1.5 * max(y1 - y0, x1 - x0))) if margem < 0 else int(margem) + r
        y0, y1, x0, x1 = max(0, y0 - m), min(H, y1 + m), max(0, x0 - m), min(W, x1 + m)
    img = imagem[y0:y1, x0:x1].astype(np.float32)
    buraco = mascara[y0:y1, x0:x1]
    saida = imagem.copy()

    niveis = [_Nivel(img, buraco, r)]
    if niveis[0].fontes.size == 0:  # nada inteiro para copiar: só interpola
        final = _preencher_suave(img, buraco)
    else:
        # pirâmide: reduz até o buraco sumir, a imagem ficar pequena ou faltar fonte
        while min(niveis[-1].H, niveis[-1].W) // 2 >= 2 * p + 1:
            img_menor, buraco_menor = _reduzir(niveis[-1].img, niveis[-1].buraco)
            if not buraco_menor.any():
                break
            prox = _Nivel(img_menor, buraco_menor, r)
            if prox.fontes.size < max(p * p, prox.alvos.size // 4):
                break
            niveis.append(prox)

        rng = np.random.default_rng(semente)
        topo = len(niveis) - 1
        nv = niveis[topo]
        nv.img[nv.buraco] = _preencher_suave(nv.img, nv.buraco)[nv.buraco]
        s = rng.choice(nv.fontes, nv.alvos.size)
        for lv in range(topo, -1, -1):
            nv = niveis[lv]
            res = _Resolvedor(nv, rng)
            # é nos níveis intermediários que a textura "escolhe a fase" (onde cada tijolo
            # começa): iterar mais ali evita emendas; no nível mais fino, uma basta
            n_em = 1 if lv == 0 else min(15, 2 + 4 * lv)
            n_pm = min(7, 1 + lv)
            if lv == topo:
                raio = max(nv.H, nv.W)
            else:
                raio = max(2, min(max(nv.H, nv.W) // 4, _RAIO_HERDADO))
            for em in range(n_em):
                D = res.distancias(np.arange(s.size), s)
                res.patchmatch(s, D, n_pm, raio, palpite_global=lv == topo)
                if lv == 0 or em < n_em - 1:
                    res.votar(s, D)
            if lv > 0:
                fino = niveis[lv - 1]
                subido = nv.img.repeat(2, 0).repeat(2, 1)[: fino.H, : fino.W]
                fino.img[fino.buraco] = subido[fino.buraco]
                res.votar(s, D, fino=fino)
                s = _subir_correspondencias(nv, s, fino, rng)
        final = niveis[0].img

    saida[y0:y1, x0:x1][buraco] = np.clip(final[buraco] + 0.5, 0, 255).astype(np.uint8)
    return saida
