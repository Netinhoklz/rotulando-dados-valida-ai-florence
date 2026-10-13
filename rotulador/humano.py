"""O operador humano: como uma pessoa troca um campo num editor de imagem, e não como um algoritmo.

O perfil profissional saía "perfeito demais": só a tinta apagada (nenhuma ferramenta seleciona assim), o texto novo
com a posição em 1/4 de pixel, o tamanho medido, a cor calibrada pelo p10 da tinta, o desfoque igual ao do scan, o
grão com o espectro do papel e a edição requantizada com as tabelas da origem. Um detector treinado só nisso aprende
o gerador, não a pessoa (RTM: IoU 57,6 nas automáticas x 29,9 nas manuais; FSTS: F1 em fraude real 0,198 → 0,477
treinando com operações e parâmetros medidos em gente).

Aqui, por VERSÃO (a mesma pessoa repete os próprios hábitos — FSTS, recorrência individual), sorteia-se o que ela
faz, nas frequências que o FSTS (NeurIPS 2025, 67 pessoas, tabela 12: "Replacement") mediu:

- **seleção** do texto antigo: retângulo (letreiro), laço, pincel ou "intervalo de cores" (só a tinta);
- **ferramenta de apagar**: preenchimento com reconhecimento de conteúdo 61,7%, cor sólida 9,6%, fundo clonado
  9,5%, carimbo 10,4%, pincel de recuperação 8,8%;
- **texto**: suavização do Photoshop (nenhuma, nítida, precisa, forte, suave), cor pelo conta-gotas (88,4%) ou uma
  cor escura "segura" (11,6%), tamanho e posição ajustados no olho (inteiro de pixel, ± 1 px; ±5% de escala);
- **nitidez**: a pessoa desfoca o texto até parecer o do scan, deixa nítido como digitou, ou passa um Desfoque
  Gaussiano na seleção inteira (11,9%; o FSTS mediu 0,5–1,2 px em fotos de cena; num documento o raio acompanha o
  desfoque dele, com o excesso de cada pessoa, até 1 px);
- **retoques** fixos na versão: aguçar 12,7%, ruído 10,5% (Adicionar Ruído: gaussiano/uniforme, monocromático ou
  não), desfoque de superfície 7,6%. Ficam de fora o desfoque de movimento (1–20 px), o brilho externo, o
  traçado e a sombra projetada, efeitos de texto de cartaz que num campo de conta ficariam visíveis à toa;
- **salvar**: JPEG do editor (tabelas do Photoshop, do GIMP, do Paint, de app), PNG, ou as mesmas tabelas da
  origem. O JPEG do editor vale para o PAR (a original é aberta e salva do mesmo jeito: negativo benigno), então a
  pista que sobra é local — a área editada comprimida uma vez, o resto duas (o sinal que CAT-Net, DTD e ADCD-Net
  usam) — e não "a imagem foi regravada".

Nada disso deixa a edição feia: o texto continua na fonte identificada, a conferência por OCR continua valendo, e as
proporções podem ser mudadas na receita (``humano``)."""

from __future__ import annotations

import random

import cv2
import numpy as np

# por versão: (valor, peso). Comentário = de onde veio o peso
SELECAO = (("retangulo", 0.55), ("laco", 0.15), ("pincel", 0.15), ("tinta", 0.15))  # estimado: letreiro é o padrão
# FSTS, tabela 12 (Replacement): content-aware 61,7, cor sólida 9,6, fundo clonado 9,5, carimbo 10,4, recuperação 8,8
APAGAR = (("patchmatch", 0.617), ("chapado", 0.096), ("fundo_clonado", 0.095), ("carimbo", 0.104), ("recuperar", 0.088))
# o apagador do catálogo que faz o mesmo (o fundo clonado é um retalho de papel colado por cima: o carimbo daqui)
APAGADOR = {"patchmatch": "patchmatch", "chapado": "chapado", "fundo_clonado": "carimbo", "carimbo": "carimbo",
            "recuperar": "recuperar"}
# suavização do texto no Photoshop (FSTS: uma das cinco em 100% das inserções; o peso é estimado, "nítida" é o
# padrão das versões novas e "suave" o das antigas)
ANTIALIAS = (("nitida", 0.36), ("suave", 0.22), ("precisa", 0.22), ("forte", 0.16), ("nenhuma", 0.04))
RASTERIZADOR = {"nitida": "ps_nitida", "suave": "ps_suave", "precisa": "ps_precisa", "forte": "ps_forte",
                "nenhuma": "aliasado"}
COR = (("conta_gotas", 0.884), ("segura", 0.116))  # FSTS: cor amostrada do texto original x cor escura "segura"
NITIDEZ = (("casada", 0.5), ("nitida", 0.3), ("manual", 0.2))  # estimado; "manual" = Desfoque Gaussiano (FSTS 11,9%)
RETOQUES = (("nitidez", 0.127), ("ruido", 0.105), ("superficie", 0.076))  # FSTS, pós-processamento da substituição
# o arquivo salvo. Origem JPEG: o editor regrava; PNG e "as mesmas tabelas" (app que preserva a compressão) também
# acontecem. Origem sem JPEG (PDF, PNG): PNG ou o JPEG do editor
SALVAR_JPEG = (("jpeg_editor", 0.55), ("png", 0.25), ("mesmas", 0.20))
SALVAR_OUTRO = (("png", 0.6), ("jpeg_editor", 0.4))
# programa que salva: (peso, sorteio da gravação). Photoshop pelos presets do Pillow (tabelas do "Salvar como" e do
# "Salvar para Web"), os outros pelo libjpeg na qualidade padrão de cada um
PROGRAMAS = {"photoshop": 0.35, "photoshop_web": 0.10, "gimp": 0.20, "paint": 0.15, "app_celular": 0.12, "photopea": 0.08}
_PRESETS_PS = ("high", "maximum", "medium")
_PRESETS_WEB = ("web_high", "web_very_high", "web_maximum")


def _escolher(rng: random.Random, pares) -> str:
    return rng.choices([v for v, _ in pares], weights=[p for _, p in pares])[0]


def normalizar(cfg) -> dict | None:
    """Configuração da receita (``humano``): None/False desliga; True ou {} liga com as proporções padrão;
    {"prob": 0..1} = fração das versões que saem com o operador humano (o resto, com o acabamento meticuloso de
    antes); {"salvar": "png"|"jpeg_editor"|"mesmas"} força o arquivo salvo."""
    if not cfg:
        return None
    if cfg is True:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ValueError("humano: use {prob, salvar}")
    prob = max(0.0, min(1.0, float(cfg.get("prob", 1.0))))
    salvar = cfg.get("salvar") if cfg.get("salvar") in ("png", "jpeg_editor", "mesmas") else "auto"
    return {"prob": prob, "salvar": salvar} if prob > 0 else None


def _gravacao(rng: random.Random) -> dict:
    prog = rng.choices(list(PROGRAMAS), weights=list(PROGRAMAS.values()))[0]
    if prog == "photoshop":
        return {"programa": prog, "preset": rng.choice(_PRESETS_PS)}
    if prog == "photoshop_web":
        return {"programa": prog, "preset": rng.choice(_PRESETS_WEB)}
    q = {"gimp": (85, 92), "paint": (88, 92), "app_celular": (85, 98), "photopea": (88, 93)}[prog]
    sub = {"gimp": rng.choice((0, 2)), "paint": 2, "app_celular": 2, "photopea": 2}[prog]
    return {"programa": prog, "qualidade": rng.randint(*q), "subsampling": sub}


def sortear(rng: random.Random, cfg: dict | None, origem_jpeg: bool) -> dict | None:
    """Os hábitos da pessoa que edita ESTA versão, ou None (a versão fica com o acabamento meticuloso)."""
    cfg = normalizar(cfg)
    if cfg is None or rng.random() >= cfg["prob"]:
        return None
    retoques = {}
    for op, p in RETOQUES:
        if rng.random() < p:
            if op == "nitidez":
                retoques[op] = round(rng.uniform(0.3, 0.8), 2)
            elif op == "ruido":  # Adicionar Ruído do Photoshop: quantidade em %, num documento fica baixa
                retoques[op] = {"quantidade": round(rng.uniform(1.0, 5.0), 1),
                                "distribuicao": rng.choice(("gaussiana", "uniforme")),
                                "monocromatico": rng.random() < 0.5}
            else:  # Desfoque de Superfície: raio e limiar pequenos (raio grande apaga a letra)
                retoques[op] = {"raio": rng.randint(1, 3), "limiar": rng.randint(5, 25)}
    nitidez = _escolher(rng, NITIDEZ)
    salvar = cfg["salvar"] if cfg["salvar"] != "auto" else _escolher(rng, SALVAR_JPEG if origem_jpeg else SALVAR_OUTRO)
    hab = {"selecao": _escolher(rng, SELECAO), "apagar": _escolher(rng, APAGAR),
           "antialias": _escolher(rng, ANTIALIAS), "cor": _escolher(rng, COR),
           "amostra": rng.choice((1, 3, 5)),  # tamanho da amostra do conta-gotas (ponto, 3x3, 5x5)
           "margem": [round(rng.uniform(0.08, 0.35), 3) for _ in range(4)],  # folga da seleção (fração da altura)
           "capricho": round(rng.uniform(0.4, 1.4), 2),  # quanto erra no olho (posição, tamanho, desfoque)
           "nitidez": nitidez, "retoques": retoques, "salvar": salvar}
    if nitidez == "manual":  # quanto a pessoa passa do ponto ao desfocar a seleção (1 = igual ao documento)
        hab["excesso"] = round(rng.uniform(0.9, 1.4), 2)
    if salvar == "jpeg_editor":
        hab["gravacao"] = _gravacao(rng)
    return hab


# como cada hábito se diz em português, para a tela e o rótulo
_DIZ_SELECAO = {"retangulo": "selecionou o texto com um retângulo", "laco": "contornou o texto com o laço",
                "pincel": "pintou por cima do texto com o pincel", "tinta": "selecionou só as letras (intervalo de cores)"}
_DIZ_APAGAR = {"patchmatch": "apagou com o preenchimento inteligente (Content-Aware Fill)",
               "chapado": "pintou por cima com a cor do papel", "fundo_clonado": "colou um pedaço de papel limpo por cima",
               "carimbo": "apagou com o carimbo (clone)", "recuperar": "apagou com o pincel de recuperação"}
_DIZ_ANTIALIAS = {"nitida": "Nítida", "suave": "Suave", "precisa": "Precisa", "forte": "Forte", "nenhuma": "Nenhuma"}
_DIZ_NITIDEZ = {"casada": "desfocou o texto até parecer o do documento", "nitida": "deixou o texto nítido como digitou",
                "manual": "passou um desfoque na área editada"}
_DIZ_RETOQUE = {"nitidez": "aguçou", "ruido": "pôs ruído", "superficie": "suavizou (desfoque de superfície)"}
_DIZ_PROGRAMA = {"photoshop": "Photoshop", "photoshop_web": "Photoshop (Salvar para Web)", "gimp": "GIMP", "paint": "Paint",
                 "app_celular": "um app de celular", "photopea": "Photopea"}


def descrever(h: dict | None) -> str:
    """Quem editou e como, numa frase (vai para a tela e para o rótulo)."""
    if not h:
        return "acabamento perfeito: só a tinta muda, com cor, grão e desfoque iguais aos do documento"
    partes = [_DIZ_SELECAO.get(h["selecao"], h["selecao"]), _DIZ_APAGAR.get(h["apagar"], h["apagar"]),
              f"escreveu com a suavização “{_DIZ_ANTIALIAS.get(h['antialias'], h['antialias'])}”",
              "pegou a cor com o conta-gotas" if h["cor"] == "conta_gotas" else "escreveu num preto qualquer",
              _DIZ_NITIDEZ.get(h["nitidez"], h["nitidez"])]
    if h.get("retoques"):
        partes.append(" e ".join(_DIZ_RETOQUE.get(r, r) for r in h["retoques"]))
    g = h.get("gravacao") or {}
    partes.append({"png": "salvou em PNG", "mesmas": "salvou com a mesma compressão do arquivo original"}.get(
        h["salvar"], f"salvou em JPEG pelo {_DIZ_PROGRAMA.get(g.get('programa'), g.get('programa'))} "
                     f"({'preset ' + str(g['preset']) if g.get('preset') else 'qualidade ' + str(g.get('qualidade'))})"))
    s = ", ".join(partes)
    return s[0].upper() + s[1:] + "."


# ------------------------------------------------------------------------------------------- seleção
def _protegida(tinta: np.ndarray, velho: np.ndarray, estrito: bool = False) -> np.ndarray:
    """A tinta que NÃO é do texto antigo (rótulo, linha vizinha, letra ao lado), com folga de 2 px. ``estrito``:
    toda tinta fora de ``velho`` (troca de uma letra ou de um trecho: a vírgula colada no dígito é vizinha)."""
    if estrito:
        return cv2.dilate((tinta & ~velho).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    perto = cv2.dilate(velho.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    n, lab = cv2.connectedComponents((tinta & ~velho).astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros_like(velho)
    # o que fica quase todo no halo do texto antigo é resto dele (antialias); o resto é vizinho
    tam = np.bincount(lab.ravel(), minlength=n)
    dentro = np.bincount(lab[perto].ravel(), minlength=n)
    fora = tam - dentro
    alheias = [i for i in range(1, n) if dentro[i] == 0 or fora[i] > max(20, 2 * dentro[i])]
    if not alheias:
        return np.zeros_like(velho)
    return cv2.dilate(np.isin(lab, alheias).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0


def selecionar(velho: np.ndarray, tinta: np.ndarray, forma: str, margem, h: float, rng: random.Random,
               limite=None, estrito: bool = False) -> np.ndarray:
    """A seleção que a pessoa faz em volta do texto antigo ``velho`` (bool, recorte), sem pegar a tinta alheia
    (``tinta`` = toda a tinta do recorte). ``forma``: retangulo (letreiro: encolhe o lado que encostaria numa vizinha),
    laco (polígono à mão em volta), pincel (pinceladas redondas ao longo da linha) ou tinta (intervalo de cores: o
    próprio ``velho``). ``margem``: folga de cada lado (esquerda, cima, direita, baixo) em fração de ``h``. ``limite``
    (x0, y0, x1, y1): a seleção não passa daí; ``estrito``: ver ``_protegida``. Sempre contém ``velho``."""
    if forma == "tinta" or not velho.any():
        return velho.copy()
    Hr, Wr = velho.shape
    prot = _protegida(tinta, velho, estrito)
    ys, xs = np.nonzero(velho)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    lx0, ly0, lx1, ly1 = limite if limite is not None else (0, 0, Wr, Hr)
    m = [max(1, int(round(float(f) * h))) for f in margem]
    if forma == "retangulo":
        # cada lado cresce até a folga pedida ou até a véspera da tinta alheia (quem seleciona não pega o rótulo)
        def ok(a0, b0, a1, b1):
            return not prot[b0:b1, a0:a1].any()
        a0, b0, a1, b1 = x0, y0, x1, y1
        for _ in range(m[0]):
            if a0 - 1 < lx0 or not ok(a0 - 1, b0, a0, b1):
                break
            a0 -= 1
        for _ in range(m[1]):
            if b0 - 1 < ly0 or not ok(a0, b0 - 1, a1, b0):
                break
            b0 -= 1
        for _ in range(m[2]):
            if a1 + 1 > lx1 or not ok(a1, b0, a1 + 1, b1):
                break
            a1 += 1
        for _ in range(m[3]):
            if b1 + 1 > ly1 or not ok(a0, b1, a1, b1 + 1):
                break
            b1 += 1
        sel = np.zeros_like(velho)
        sel[b0:b1, a0:a1] = True
        return sel | velho
    sel = np.zeros((Hr, Wr), np.uint8)
    if forma == "laco":
        # o contorno feito à mão: pontos em volta da caixa com folga, tremidos
        n = max(8, int((x1 - x0) / max(4.0, 0.8 * h)) * 2)
        topo = [(x0 - m[0] + (x1 - x0 + m[0] + m[2]) * i / (n // 2), y0 - m[1]) for i in range(n // 2 + 1)]
        baixo = [(x1 + m[2] - (x1 - x0 + m[0] + m[2]) * i / (n // 2), y1 + m[3]) for i in range(n // 2 + 1)]
        j = 0.12 * h
        pts = np.array([(x + rng.uniform(-j, j), y + rng.uniform(-j, j)) for x, y in topo + baixo], np.float32)
        cv2.fillPoly(sel, [np.round(pts).astype(np.int32)], 1)
    else:  # pincel: pinceladas horizontais ao longo da linha, raio de meia altura, até sumir com o texto
        r = max(2, int(round((y1 - y0) * rng.uniform(0.55, 0.75))))
        cy = (y0 + y1) / 2
        for _ in range(rng.choice((1, 1, 2))):
            dy = rng.uniform(-0.15, 0.15) * (y1 - y0)
            pts = [(x0 - rng.uniform(0, m[0]), cy + dy)]
            while pts[-1][0] < x1:
                pts.append((pts[-1][0] + rng.uniform(1.5, 3.0) * r, cy + dy + rng.uniform(-0.1, 0.1) * (y1 - y0)))
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                cv2.line(sel, (int(round(ax)), int(round(ay))), (int(round(bx)), int(round(by))), 1, 2 * r)
            for x, y in (pts[0], pts[-1]):
                cv2.circle(sel, (int(round(x)), int(round(y))), r, 1, -1)
        # o que a pincelada não pegou (descendente, acento) leva mais um toque em cima
        sel |= (cv2.dilate(velho.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0)
    sel = sel.astype(bool) & ~prot
    lim = np.zeros_like(velho)
    lim[max(0, ly0):ly1, max(0, lx0):lx1] = True
    return (sel & lim) | velho


# ------------------------------------------------------------------------------------------- texto
def texto(h: dict, rng: random.Random, desfoque_scan: float, altura: float) -> dict:
    """Como a pessoa ajusta o texto novo NESTE campo: escala (no olho), deslocamento da linha de base e do começo
    (inteiros: a camada de texto fica no pixel), suavização e desfoque."""
    k = float(h.get("capricho", 1.0))
    escala = 1.0 + float(np.clip(rng.gauss(0.0, 0.015 * k), -0.05, 0.05))
    dy = rng.choices((-1, 0, 1), weights=(0.2 * k, 1.0, 0.2 * k))[0]
    dx = int(np.clip(round(rng.gauss(0.0, 0.7 * k)), -2, 2))
    nit = h.get("nitidez")
    if nit == "casada":  # desfocou até parecer o do scan: chega perto, não no mesmo valor
        desf = max(0.0, desfoque_scan * rng.uniform(0.75, 1.3))
    else:  # nítida como digitou; no "manual" o desfoque vem depois, na seleção inteira (retoque)
        desf = 0.0
    return {"escala": round(escala, 4), "dx": dx, "dy": dy, "rasterizador": RASTERIZADOR.get(h.get("antialias"), "ps_nitida"),
            "desfoque": round(desf, 2)}


def cor_conta_gotas(img: np.ndarray, tinta: np.ndarray, h: dict, rng: random.Random) -> np.ndarray | None:
    """A cor que o conta-gotas pegou: um ponto (ou a média 3x3/5x5 em volta dele) no núcleo da tinta antiga — perto
    da cor certa, com o ruído e o antialias daquele ponto. Na "segura", um cinza escuro (0–64) se a tinta é neutra."""
    if not tinta.any():
        return None
    f = img.astype(np.float32)
    lum = f @ np.float32([0.299, 0.587, 0.114])
    if h.get("cor") == "segura":
        c = np.percentile(f[tinta], 10, axis=0)
        # tinta preta (neutra e já escura): a pessoa escolhe um preto qualquer. Numa tinta cinza ou colorida o preto
        # destoaria à toa (a linha inteira mais pesada que as vizinhas): ela usa o conta-gotas
        if float(c.max() - c.min()) < 30 and float(c.mean()) < 90:
            m = int(c.mean())
            v = rng.randint(max(0, m - 30), max(0, min(64, m + 10)))
            return np.float32([v, v, v])
    # quem usa o conta-gotas clica no miolo do traço, onde parece mais escuro (não na borda clara do antialias)
    nucleo = tinta & (lum <= np.percentile(lum[tinta], 15))
    ys, xs = np.nonzero(nucleo if nucleo.any() else tinta)
    i = rng.randrange(len(ys))
    r = int(h.get("amostra", 1)) // 2
    y, x = int(ys[i]), int(xs[i])
    # a amostra 3x3/5x5 num traço fino pega papel e o texto saía bem mais claro (+40 a +88 de luminância): quem vê
    # a cor errada clica de novo; fica a média só do miolo dentro da amostra
    jan = (slice(max(0, y - r), y + r + 1), slice(max(0, x - r), x + r + 1))
    dentro = nucleo[jan] if nucleo.any() else tinta[jan]
    return f[jan][dentro].reshape(-1, 3).mean(0)


# ------------------------------------------------------------------------------------------- retoques
def ruido(rec: np.ndarray, p: dict, semente: int) -> np.ndarray:
    """Adicionar Ruído do Photoshop sobre ``rec`` (float32 RGB): quantidade em % de 255."""
    g = np.random.default_rng(semente)
    a = float(p.get("quantidade", 2.0)) * 2.55
    forma = rec.shape[:2] + ((1,) if p.get("monocromatico") else (3,))
    if p.get("distribuicao") == "uniforme":
        n = g.uniform(-a, a, forma)
    else:
        n = g.normal(0.0, a / 2.0, forma)
    return rec + n.astype(np.float32)


def superficie(rec: np.ndarray, p: dict) -> np.ndarray:
    """Desfoque de Superfície (preserva borda): o bilateral com raio e limiar do Photoshop."""
    r = int(p.get("raio", 2))
    return cv2.bilateralFilter(np.clip(rec, 0, 255).astype(np.float32), 2 * r + 1, float(p.get("limiar", 15)), float(r))
