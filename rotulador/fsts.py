"""O método de geração do FSTS (Yu et al., *Toward Real-world Text Image Forgery Localization: Structured and
Interpretable Data Synthesis*, NeurIPS 2025 D&B — "Fourier Series-based Tampering Synthesis").

O artigo registrou 16.750 adulterações de 67 pessoas no Photoshop (vídeo, PSD e histórico) em cinco TIPOS — cópia
interna (copy-move), colagem de outra imagem (splicing), remoção, inserção e substituição — e mediu, para cada tipo, as
operações do processamento principal e do pós-processamento (o "disfarce") com os parâmetros e a frequência de cada uma
(apêndice B, tabelas 8 a 12). A modelagem é hierárquica (seção 3.3): cada pessoa repete as próprias escolhas
(eq. 3: frequências e uma configuração representativa por tipo, com o limiar de 2%), e a população é a soma delas
(eq. 5: só fica a configuração que pelo menos 5% das pessoas têm). Esse é o caminho do artigo dos dados reais até as
TABELAS; a geração (eq. 8, fig. 4) sorteia da POPULAÇÃO: em cada imagem, de 1 a 12 regiões de texto, de 1 a 20
caracteres cada, um tipo por região, cada operação pela frequência da tabela e cada parâmetro dentro da faixa dela; a
máscara é a diferença binarizada entre a adulterada e a original (1 onde mudou). As configurações das 67 pessoas não
foram publicadas (o repositório dos autores traz só o conjunto de dados e vídeos): o que existe para gerar são as
tabelas da população, e é delas que se sorteia aqui.

- ``TABELAS``: as tabelas 8–12 como estão no artigo. As frequências do pós-processamento JÁ vêm multiplicadas por 0,3
  (nota 2 do apêndice: a figura 2 traz as cruas — desfoque gaussiano 39,7%, ruído 34,9% — e a tabela 12 as de síntese,
  11,91% e 10,47%); passos com o mesmo índice são alternativas exclusivas (o resto da probabilidade é "nenhuma");
- ``configurar``: a configuração de UMA região (tipo, operações principais, pós-processamento, parâmetros);
- ``habito_humano``: as escolhas do processamento principal que o operador humano (``rotulador.humano``) executa
  (ferramenta de apagar, suavização do texto, conta-gotas ou cor segura, escala ±5%, rotação);
- ``pos_processar``: o pós-processamento no PhotoCraft (o artigo roda um roteiro em JavaScript no Photoshop): os estilos
  de camada (Traçado, Sombra projetada, Brilho externo) numa camada com a tinta nova (Ctrl+J) e os filtros e ajustes na
  área editada.

O que o artigo diz e não dá para reproduzir à risca está anotado em ``docs/falsificadores.md`` (seção 4d)."""

from __future__ import annotations

import random

import cv2
import numpy as np

from . import gesto, photocraft
from .selecao import mascara_tinta
from .sessao import Resultado

ARTIGO = ("FSTS — Yu et al., Toward Real-world Text Image Forgery Localization: Structured and Interpretable Data "
          "Synthesis, NeurIPS 2025 (Datasets and Benchmarks)")
ESCALA_POS = 0.3  # nota 2 do apêndice B (as tabelas já trazem as frequências multiplicadas por ela)
REGIOES = (1, 12)  # "Region Quantity: 1–12 zones"
COMPRIMENTO = (1, 20)  # "Text Length: 1–20 characters"
MARGEM_POS = 40  # px em volta da edição que o pós-processamento pode tocar (sombra até 7 + 17 px, traçado, brilho)

TIPOS = {
    "copy_move": {"rotulo": "Cópia interna (copy-move)", "tabela": 8},
    "splicing": {"rotulo": "Colagem de outra imagem (splicing)", "tabela": 9},
    "removal": {"rotulo": "Remoção", "tabela": 10},
    "insertion": {"rotulo": "Inserção", "tabela": 11},
    "replacement": {"rotulo": "Substituição", "tabela": 12},
}

# ------------------------------------------------------------------------------- as faixas de parâmetros das tabelas
_VARINHA = {"tolerancia": ("int", 1, 50), "contiguo": ("opcao", (True, False)), "suavizar": ("opcao", (True, False))}
_CANAIS = {"canal": ("fixo", "vermelho"), "entrada": ("int", 130, 237), "saida": ("fixo", [0, 255])}
_ROT_0_5 = {"angulo": ("real", 0.0, 5.0)}
_ROT_5 = {"angulo": ("real", -5.0, 5.0)}
_AGUCAR_FINO = {"quantidade": ("int", 100, 200), "raio": ("real", 1.0, 4.0), "limiar": ("int", 7, 12)}
_AGUCAR_FORTE = {"iteracoes": ("int", 1, 5), "quantidade": ("int", 400, 500), "raio": ("real", 50.0, 60.0),
                 "limiar": ("int", 2, 3)}
_GAUSS_LARGO = {"raio": ("real", 0.1, 3.0)}
_GAUSS_ESTREITO = {"raio": ("real", 0.5, 1.2)}
_MOVIMENTO_15 = {"angulo": ("int", -15, 15), "distancia": ("int", 1, 9)}
_MOVIMENTO_30 = {"angulo": ("int", -30, 30), "distancia": ("int", 1, 20)}
_RADIAL = {"metodo": ("opcao", ("spin", "zoom")), "qualidade": ("opcao", ("best", "draft", "good"))}
# Desfoque Inteligente: os modos da tabela ("Edge Preservation/Normal/Stroke Enhancement") são os três do Photoshop
# (Somente Bordas, Normal, Sobrepor Bordas)
_INTELIGENTE = {"raio": ("real", 0.1, 10.0), "limiar": ("real", 0.1, 10.0), "qualidade": ("opcao", ("high", "medium", "low")),
                "modo": ("opcao", ("edgeOnly", "normal", "overlayEdge"))}
_CONVOLUCAO = {"nucleo": ("nucleo",), "deslocamento": ("int", -5, 5)}
_LENTE = {"raio": ("real", 0.0, 1.0), "forma": ("opcao", ("hexagon", "heptagon", "octagon", "pentagon", "square", "triangle")),
          "curvatura": ("real", 0.0, 1.0), "rotacao": ("real", 0.0, 6.0), "brilho": ("fixo", 100),
          "limiar": ("real", 0.0, 100.0), "ruido": ("real", 0.0, 25.0), "distribuicao": ("opcao", ("gaussian", "uniform"))}
_COR_BAL = {"meios_tons": ("trio", -100, 100)}
# a tabela diz só "realçar claros / baixar escuros": a intensidade (o quanto o ponto da curva sobe ou desce) é nossa
_CURVAS = {"curva": ("opcao", ("realcar_claros", "baixar_escuros")), "intensidade": ("int", 5, 25)}
_MATIZ = {"matiz": ("int", -30, 30), "saturacao": ("int", -20, 20), "luminosidade": ("int", -30, 30)}
_SUPERFICIE = {"raio": ("int", 1, 15), "limiar": ("int", 5, 25)}
_RUIDO = {"quantidade": ("real", 0.1, 35.0), "distribuicao": ("opcao", ("gaussian", "uniform")),
          "monocromatico": ("opcao", (True, False))}
_TRACO = {"tamanho": ("int", 1, 5), "posicao": ("opcao", ("inside", "center", "outside")),
          "mistura": ("opcao", ("normal", "multiply")), "opacidade": ("int", 50, 100), "cor": ("cor",)}
_TRACO_INSERCAO = {**_TRACO, "opacidade": ("fixo", 100)}  # a tabela 11 não traz a opacidade do traçado
_SOMBRA = {"mistura": ("opcao", ("normal", "multiply", "darken")), "cor": ("cor",), "opacidade": ("int", 5, 23),
           "angulo": ("int", -30, 30), "distancia": ("int", 1, 7), "espalhar": ("int", 3, 12), "tamanho": ("int", 1, 17),
           "ruido": ("int", 1, 10)}
_BRILHO = {"cor": ("fixo", "#534f4f"), "opacidade": ("fixo", 17), "ruido": ("int", 35, 45), "tamanho": ("int", 5, 8)}
_FONTE = {"suavizacao": ("opcao", ("nenhuma", "nitida", "precisa", "suave", "forte"))}
_REMOVER = [("preenchimento", {"iteracoes": ("int", 1, 5)}), ("cor_solida", {}), ("fundo_clonado", {}),
            ("carimbo", {"opacidade": ("fixo", 100), "fluxo": ("fixo", 100)}),
            ("recuperacao", {"modo": ("opcao", ("normal", "replace"))})]


def _remover(freqs):
    return [(op, f, spec) for (op, spec), f in zip(_REMOVER, freqs)]


# (passo, [(operação, frequência em %, faixas)]): mesmo passo = alternativas exclusivas; o resto até 100% é "nenhuma".
# As frequências do pós-processamento já são as de síntese (×0,3)
TABELAS = {
    "copy_move": {  # tabela 8
        "principal": [("2.1", [("varinha", 18.53, _VARINHA), ("canais", 23.74, _CANAIS)]),
                      ("2.2", [("escala", 73.50, {})]),
                      ("2.3", [("rotacao", 13.33, _ROT_0_5)])],
        "pos": [("3.1", [("agucar", 8.90, _AGUCAR_FINO)]),
                ("3.2", [("desfoque", 5.71, {}), ("desfoque_mais", 3.74, {}), ("media", 5.50, {}),
                         ("gaussiano", 12.70, _GAUSS_LARGO), ("movimento", 7.10, _MOVIMENTO_15), ("radial", 3.09, _RADIAL),
                         ("inteligente", 8.78, _INTELIGENTE), ("convolucao", 8.70, _CONVOLUCAO)]),
                ("3.3", [("equilibrio_cor", 4.18, _COR_BAL)]),
                ("3.4", [("curvas", 8.53, _CURVAS)])]},
    "splicing": {  # tabela 9
        "principal": [("2.1", [("varinha", 12.69, _VARINHA), ("canais", 17.94, _CANAIS)]),
                      ("2.2", [("escala", 78.00, {})]),
                      ("2.3", [("rotacao", 19.30, _ROT_0_5)])],
        "pos": [("3.1", [("agucar", 10.04, _AGUCAR_FINO)]),
                ("3.2", [("gaussiano", 18.70, _GAUSS_LARGO), ("lente", 7.03, _LENTE), ("movimento", 5.40, _MOVIMENTO_15),
                         ("radial", 3.11, _RADIAL), ("inteligente", 3.90, _INTELIGENTE), ("convolucao", 2.70, _CONVOLUCAO)]),
                ("3.3", [("curvas", 17.45, _CURVAS)]),
                ("3.4", [("traco", 8.75, _TRACO)]),
                ("3.5", [("sombra", 6.99, _SOMBRA)]),
                ("3.6", [("matiz", 10.49, _MATIZ)])]},
    "removal": {  # tabela 10 (sem pós-processamento)
        "principal": [("2.1", _remover((55.82, 9.76, 11.52, 10.45, 12.45))),
                      ("3.1", [("escala", 88.00, {})]),
                      ("3.2", [("rotacao", 0.68, _ROT_5)])],
        "pos": []},
    "insertion": {  # tabela 11
        "principal": [("2.1", [("fonte", 100.0, _FONTE)]),
                      ("2.2", [("conta_gotas", 86.90, {}), ("cor_segura", 13.10, {})]),
                      ("3.1", [("escala", 77.00, {})]),
                      ("3.2", [("rotacao", 12.03, _ROT_5)])],
        "pos": [("4.1", [("agucar", 12.73, _AGUCAR_FORTE)]),
                ("4.2", [("gaussiano", 16.80, _GAUSS_ESTREITO)]),
                ("4.3", [("brilho", 7.51, _BRILHO)]),
                ("4.4", [("ruido", 16.54, _RUIDO)]),
                ("4.5", [("traco", 15.33, _TRACO_INSERCAO)]),
                ("4.6", [("sombra", 5.26, _SOMBRA)])]},
    "replacement": {  # tabela 12. Cor amostrada (3.3) e cor segura (3.4) somam 100%: uma ou outra
        "principal": [("2.1", _remover((61.70, 9.60, 9.50, 10.40, 8.80))),
                      ("3.1", [("fonte", 100.0, _FONTE)]),
                      ("3.3", [("conta_gotas", 88.40, {}), ("cor_segura", 11.60, {})]),
                      ("4.1", [("escala", 43.50, {}), ("rotacao", 33.33, _ROT_5)])],
        "pos": [("5.1", [("agucar", 12.69, _AGUCAR_FORTE)]),
                ("5.2", [("gaussiano", 11.91, _GAUSS_ESTREITO), ("superficie", 7.60, _SUPERFICIE),
                         ("movimento", 7.63, _MOVIMENTO_30)]),
                ("5.3", [("brilho", 13.68, _BRILHO)]),
                ("5.4", [("ruido", 10.47, _RUIDO)]),
                ("5.5", [("traco", 10.20, _TRACO)]),
                ("5.6", [("sombra", 8.81, _SOMBRA)])]},
}
REMOCAO = tuple(op for op, _ in _REMOVER)
ESTILOS_DE_CAMADA = ("traco", "sombra", "brilho")  # vão numa camada com a tinta nova; o resto, na área editada
# a ferramenta de apagar do artigo → a do operador humano (``humano.APAGAR``)
DO_ARTIGO = {"preenchimento": "patchmatch", "cor_solida": "chapado", "fundo_clonado": "fundo_clonado",
             "carimbo": "carimbo", "recuperacao": "recuperar"}

DIZ = {"varinha": "Varinha Mágica na forma do texto", "canais": "Canais e Níveis para tirar o fundo",
       "escala": "escala ajustada à região", "rotacao": "girou", "fonte": "texto com a suavização",
       "conta_gotas": "cor do texto original (conta-gotas)", "cor_segura": "cor escura “segura”",
       "preenchimento": "Preenchimento sensível ao conteúdo", "cor_solida": "preenchimento com cor sólida",
       "fundo_clonado": "fundo limpo clonado por cima", "carimbo": "Carimbo", "recuperacao": "Pincel de Recuperação",
       "agucar": "Máscara de Nitidez", "desfoque": "Desfoque", "desfoque_mais": "Desfoque Mais", "media": "filtro de média",
       "gaussiano": "Desfoque Gaussiano", "movimento": "Desfoque de Movimento", "radial": "Desfoque Radial",
       "inteligente": "Desfoque Inteligente", "convolucao": "filtro Personalizado (convolução)",
       "lente": "Desfoque de Lente", "equilibrio_cor": "Equilíbrio de Cores", "curvas": "Curvas",
       "matiz": "Matiz/Saturação", "superficie": "Desfoque de Superfície", "ruido": "Adicionar Ruído",
       "traco": "Traçado", "sombra": "Sombra projetada", "brilho": "Brilho externo"}


# ------------------------------------------------------------------------------- os parâmetros
def _valor(spec: tuple, g: np.random.Generator):
    tipo = spec[0]
    if tipo == "fixo":
        return spec[1]
    if tipo == "int":
        return int(g.integers(spec[1], spec[2] + 1))
    if tipo == "real":
        return round(float(g.uniform(spec[1], spec[2])), 2)
    if tipo == "opcao":
        return spec[1][int(g.integers(len(spec[1])))]
    if tipo == "cor":
        return "#%02x%02x%02x" % tuple(int(v) for v in g.integers(0, 256, 3))
    if tipo == "trio":
        return [int(v) for v in g.integers(spec[1], spec[2] + 1, 3)]
    if tipo == "nucleo":
        return _nucleo(g)
    raise ValueError(spec)


def _nucleo(g: np.random.Generator) -> dict:
    """O filtro Personalizado 5x5 dentro das faixas da tabela (valores −10..10, escala 1–20): o centro positivo e alguns
    vizinhos, com a soma igual à escala — a imagem não clareia nem escurece no todo (um núcleo qualquer apagaria a
    região, e ninguém guarda um resultado assim)."""
    while True:
        k = np.zeros(25, int)
        k[12] = int(g.integers(1, 11))
        for i in g.choice([i for i in range(25) if i != 12], size=int(g.integers(2, 9)), replace=False):
            k[i] = int(g.integers(-10, 11))
        if 1 <= k.sum() <= 20:
            return {"valores": [int(v) for v in k], "escala": int(k.sum())}


def normalizar(cfg) -> dict:
    """A configuração da receita (``fsts``): {"tipos": {tipo: peso}} — o a_k de cada tipo (eq. 5); o tipo que não vem
    fica de fora. O artigo coletou o mesmo número de cada tipo; sem pesos, os cinco por igual."""
    cfg = cfg if isinstance(cfg, dict) else {}
    pesos = cfg.get("tipos") if isinstance(cfg.get("tipos"), dict) else {}
    tipos = {t: max(0.0, float(pesos.get(t, 0.0) if pesos else 1.0)) for t in TIPOS}
    if not any(tipos.values()):
        raise ValueError("fsts: ligue pelo menos um tipo de adulteração")
    return {"tipos": tipos}


# ------------------------------------------------------------------------------- uma região
def _sortear(ops: list, rng: random.Random) -> tuple | None:
    """Uma das alternativas de um passo, pela frequência da tabela (em %), ou None (o resto até 100%)."""
    r, acc = rng.random() * 100.0, 0.0
    for o in ops:
        acc += o[1]
        if r < acc:
            return o
    return None


def configurar(tipo: str, rng: random.Random) -> dict:
    """A configuração de UMA região (eq. 8): em cada passo do processamento principal e do pós-processamento, a operação
    pela frequência da tabela e os parâmetros dela sorteados dentro das faixas; e a ferramenta de apagar (a da remoção
    ou da substituição, quando o tipo tem; senão, pela frequência da substituição: a cópia e a colagem também apagam o
    texto que fica embaixo, e o artigo não diz com quê)."""
    g = np.random.default_rng(rng.randrange(2 ** 32))
    cfg = {"tipo": tipo, "principal": {}, "pos": []}
    for passo, ops in TABELAS[tipo]["principal"]:
        o = _sortear(ops, rng)
        if o:
            cfg["principal"][o[0]] = {k: _valor(v, g) for k, v in o[2].items()}
    for passo, ops in TABELAS[tipo]["pos"]:
        o = _sortear(ops, rng)
        if o:
            cfg["pos"].append({"op": o[0], "passo": passo, **{k: _valor(v, g) for k, v in o[2].items()}})
    op = next((o for o in cfg["principal"] if o in REMOCAO), None)
    if op is None:
        op = (_sortear(TABELAS["replacement"]["principal"][0][1], rng) or ("preenchimento",))[0]
    cfg["apagar"] = DO_ARTIGO[op]
    return cfg


def habito_humano(cfg: dict, caixa, rng: random.Random) -> dict:
    """O processamento principal da região como hábitos do operador humano (``rotulador.humano``), que as técnicas já
    sabem executar: a seleção da região de texto (retângulo, como a caixa do OCR), a ferramenta de apagar, a suavização
    do texto, a cor (conta-gotas ou segura), a escala adaptada à região ±5% e a rotação. O texto sai nítido como foi
    digitado: o desfoque, quando há, é o do pós-processamento. Na remoção, a "escala da região" é a folga da seleção
    (ela nunca fica menor que o texto). O que o artigo não mede e o operador precisa (a amostra do conta-gotas, o
    capricho no olho, o zoom) é sorteado como no operador humano."""
    pr = cfg["principal"]
    x0, y0, x1, y1 = (float(v) for v in caixa)
    h, w = max(1.0, y1 - y0), max(1.0, x1 - x0)
    folga = 0.1
    escala, margem = 1.0, [folga] * 4
    if "escala" in pr:
        escala = round(rng.uniform(0.95, 1.05), 4)
        if cfg["tipo"] == "removal":
            extra = max(0.0, escala - 1.0) / 2
            margem = [folga + extra * w / h, folga + extra, folga + extra * w / h, folga + extra]
            escala = 1.0
    fonte = pr.get("fonte") or {"suavizacao": "nitida"}
    return {"selecao": "retangulo", "apagar": cfg["apagar"], "antialias": fonte["suavizacao"],
            "cor": "segura" if "cor_segura" in pr else "conta_gotas", "amostra": rng.choice((1, 3, 5)),
            "margem": [round(m, 3) for m in margem], "capricho": round(rng.uniform(0.4, 1.0), 2), "nitidez": "nitida",
            "retoques": {}, "salvar": "png", "zoom": gesto.sortear_zoom(rng), "escala": escala,
            "rotacao": float(pr["rotacao"]["angulo"]) if "rotacao" in pr else 0.0}


def escala_da_colagem(habitos: dict | None) -> bool:
    """Na cópia e na colagem do FSTS, a pessoa ajustou a escala do recorte à região (passo 2.2)? Sem o FSTS, sim."""
    cfg = (habitos or {}).get("fsts")
    return not isinstance(cfg, dict) or "escala" in cfg.get("principal", {})


def rotacao_da_colagem(habitos: dict | None) -> float:
    """O ângulo (graus) com que a pessoa girou o recorte colado (passo 2.3), ou 0."""
    cfg = (habitos or {}).get("fsts")
    if not isinstance(cfg, dict) or cfg.get("tipo") not in ("copy_move", "splicing"):
        return 0.0
    return float((cfg.get("principal", {}).get("rotacao") or {}).get("angulo", 0.0))


def extracao(cfg: dict) -> str | None:
    """Como a pessoa tirou o fundo do recorte (passo 2.1 da cópia e da colagem), ou None (copiou a região inteira)."""
    return next((o for o in ("varinha", "canais") if o in cfg.get("principal", {})), None)


def _diz_op(op: str, p: dict) -> str:
    nome = DIZ.get(op, op)
    if op == "rotacao":
        return f"{nome} {p.get('angulo')}°"
    if op == "fonte":
        return f"{nome} {p.get('suavizacao')}"
    vals = ", ".join(f"{k} {v['valores'] if isinstance(v, dict) and 'valores' in v else v}" for k, v in p.items()
                     if k not in ("op", "passo"))
    return f"{nome} ({vals})" if vals else nome


def descrever(cfg: dict) -> str:
    """O que foi feito nesta região, numa frase (vai para o rótulo)."""
    pr = [_diz_op(op, p) for op, p in cfg["principal"].items()]
    pos = [_diz_op(o["op"], o) for o in cfg["pos"]]
    s = f"FSTS, {TIPOS[cfg['tipo']]['rotulo'].lower()}"
    if pr:
        s += ": " + "; ".join(pr)
    s += ". Pós-processamento: " + ("; ".join(pos) if pos else "nenhum") + "."
    return s


# ------------------------------------------------------------------------------- o pós-processamento no PhotoCraft
def _curva(o: dict) -> list:
    d = int(o.get("intensidade", 15))
    if o.get("curva") == "baixar_escuros":
        return [[0, 0], [64, max(0, 64 - d)], [255, 255]]
    return [[0, 0], [192, min(255, 192 + d)], [255, 255]]


def passos_op(o: dict, centro: tuple[float, float], semente: int) -> list:
    """Os comandos do PhotoCraft de uma operação do pós-processamento."""
    op = o["op"]
    if op == "agucar":
        return [("filter.sharpen.unsharpMask", {"amount": int(o["quantidade"]), "radius": float(o["raio"]),
                                                "threshold": int(o["limiar"])})] * int(o.get("iteracoes", 1))
    simples = {"desfoque": ("filter.blur.blur", {}), "desfoque_mais": ("filter.blur.blurMore", {}),
               "media": ("filter.blur.boxBlur", {"radius": 1})}
    if op in simples:
        return [simples[op]]
    if op == "gaussiano":
        return [("filter.blur.gaussianBlur", {"radius": float(o["raio"])})]
    if op == "movimento":
        return [("filter.blur.motionBlur", {"angle": int(o["angulo"]), "distance": int(o["distancia"])})]
    if op == "radial":  # a tabela não traz a quantidade: a padrão do Photoshop (10), no centro da área editada
        return [("filter.blur.radialBlur", {"amount": 10, "method": o["metodo"], "quality": o["qualidade"],
                                            "centerX": round(centro[0], 4), "centerY": round(centro[1], 4)})]
    if op == "inteligente":
        return [("filter.blur.smartBlur", {"radius": float(o["raio"]), "threshold": float(o["limiar"]),
                                           "quality": o["qualidade"], "mode": o["modo"]})]
    if op == "convolucao":
        return [("filter.other.custom", {"kernel": o["nucleo"]["valores"], "scale": o["nucleo"]["escala"],
                                         "offset": int(o["deslocamento"])})]
    if op == "lente":
        return [("filter.blur.lensBlur", {"radius": float(o["raio"]), "shape": o["forma"],
                                          "bladeCurvature": int(round(100 * float(o["curvatura"]))),
                                          "rotation": float(o["rotacao"]), "depthMap": "none", "invert": False,
                                          "brightness": int(o["brilho"]), "threshold": int(round(2.55 * float(o["limiar"]))),
                                          "noise": float(o["ruido"]), "distribution": o["distribuicao"],
                                          "seed": int(semente % 2 ** 31)})]
    if op == "equilibrio_cor":
        return [("image.adjustments.colorBalance", {"midtones": list(o["meios_tons"]), "preserveLuminosity": True})]
    if op == "curvas":
        return [("image.adjustments.curves", {"points": _curva(o)})]
    if op == "matiz":
        return [("image.adjustments.hueSaturation", {"hue": int(o["matiz"]), "saturation": int(o["saturacao"]),
                                                      "lightness": int(o["luminosidade"])})]
    if op == "superficie":
        return [("filter.blur.surfaceBlur", {"radius": int(o["raio"]), "threshold": int(o["limiar"])})]
    if op == "ruido":
        return [("filter.noise.addNoise", {"amount": float(o["quantidade"]), "distribution": o["distribuicao"],
                                           "monochromatic": bool(o["monocromatico"]), "seed": int(semente % 2 ** 31)})]
    if op == "traco":
        return [("layer.layerStyle.stroke", {"size": int(o["tamanho"]), "position": o["posicao"], "color": o["cor"],
                                             "opacity": int(o["opacidade"]), "blend": o["mistura"]})]
    if op == "sombra":
        return [("layer.layerStyle.dropShadow", {"color": o["cor"], "opacity": int(o["opacidade"]), "blend": o["mistura"],
                                                 "angle": int(o["angulo"]), "useGlobalLight": False,
                                                 "distance": int(o["distancia"]), "spread": int(o["espalhar"]),
                                                 "size": int(o["tamanho"]), "noise": int(o["ruido"])})]
    if op == "brilho":
        return [("layer.layerStyle.outerGlow", {"color": o["cor"], "opacity": int(o["opacidade"]), "blend": "screen",
                                                "size": int(o["tamanho"]), "noise": int(o["ruido"])})]
    raise ValueError(f"operação do FSTS desconhecida: {op}")


def pos_processar(img: np.ndarray, res: Resultado, cfg: dict, semente: int) -> tuple[Resultado, list[str]]:
    """O pós-processamento da região (``cfg["pos"]``) no PhotoCraft, sobre a imagem com a edição: os estilos de camada
    numa camada com a tinta nova (seleção da tinta + Ctrl+J, como numa camada de texto ou colada), achatada depois; os
    filtros e ajustes, um a um, na área editada (a seleção da edição com 2 px de folga). Devolve o Resultado com tudo
    que mudou (a pegada cresce para a sombra, o traçado e o brilho) e o que foi feito, para o rótulo."""
    ops = cfg.get("pos") or []
    if not ops:
        return res, []
    H, W = img.shape[:2]
    m = MARGEM_POS
    X0, Y0, X1, Y1 = max(0, res.x0 - m), max(0, res.y0 - m), min(W, res.x1 + m), min(H, res.y1 + m)
    antes = np.ascontiguousarray(img[Y0:Y1, X0:X1])
    rec = antes.copy()
    peg = np.zeros(rec.shape[:2], bool)
    sl = (slice(res.y0 - Y0, res.y1 - Y0), slice(res.x0 - X0, res.x1 - X0))
    peg[sl] = res.pegada
    rec[sl] = np.where(res.pegada[..., None], res.recorte, rec[sl])
    nova = peg & mascara_tinta(rec).astype(bool)
    regiao = cv2.dilate(peg.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    ys, xs = np.nonzero(regiao)
    centro = ((float(xs.mean()) + 0.5) / rec.shape[1], (float(ys.mean()) + 0.5) / rec.shape[0])
    estilos = [o for o in ops if o["op"] in ESTILOS_DE_CAMADA]
    passos, feitos = [], []
    if estilos and nova.any():
        passos += photocraft.passos_selecao(nova) + [("layer.new.layerViaCopy", {})]
        for o in estilos:
            passos += passos_op(o, centro, semente)
            feitos.append(_diz_op(o["op"], o))
        passos.append(("layer.flattenImage", {}))
    for o in ops:
        if o["op"] in ESTILOS_DE_CAMADA:
            continue
        passos += photocraft.passos_selecao(regiao) + passos_op(o, centro, semente) + [("select.deselect", {})]
        feitos.append(_diz_op(o["op"], o))
    if not passos:
        return res, []
    with photocraft.Documento(rec) as d:
        d.rodar(passos)
        out = d.imagem()
    pegada = (np.abs(out.astype(np.int16) - antes.astype(np.int16)).max(2) > 0) | peg
    ys, xs = np.nonzero(pegada)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    return Resultado(Y0 + y0, Y0 + y1, X0 + x0, X0 + x1, np.ascontiguousarray(out[y0:y1, x0:x1]),
                     pegada[y0:y1, x0:x1].copy(), meta=res.meta), feitos
