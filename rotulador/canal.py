"""Canal de envio: o caminho que o comprovante faz até quem vai verificá-lo.

Fraude de verdade chega ao verificador depois de impressa e escaneada, fotografada com o celular,
fotografada na tela ou mandada pelo WhatsApp. Treinar só com a imagem "limpa" ensina o modelo a
achar a edição num caso que quase não existe na prática:
  - DocTamper treina e testa com JPEG 75–100 aplicado de 1 a 3 vezes;
  - RTM redimensiona (lado 512–2000) e comprime (75–100);
  - Wu et al. (CVPR 2022) modelam o que redes sociais fazem;
  - FantasyID e SIDTD imprimem e fotografam.
Ver docs/tecnicas.md ("Canal de envio").

A regra que evita atalho: o MESMO canal passa na original e na editada de cada par (os mesmos
parâmetros e os mesmos campos de ruído, luz e sombra, gerados uma vez só), e as máscaras passam
pela mesma geometria. Fora da vizinhança da edição as duas saídas continuam iguais; o reamostrar
e os blocos do JPEG espalham a diferença alguns pixels, e o WEBP com perda espalha diferenças
pequenas pela página (o mesmo codificador nas duas: não é atalho; ver ``tem_webp``).

Duas fases: quem edita no celular fotografa ANTES e desenha por cima da foto; quem usa o Paint num
scan escaneou antes. Cada passo traz ``fase`` ("antes" ou "depois" da edição): scanner, celular e
app de scanner sem impressão vêm antes; impressão, tela, captura e transmissões vêm depois. Passos
sem a chave (registros antigos) contam como "depois".

    theta = sortear(rng, {"perfil": "misturado"})   # registro (JSON) de tudo que foi sorteado
    (orig, ed), (alterados, regiao), (classes, mapa) = aplicar([orig, ed], [alterados, regiao], [classes, mapa], theta)
    (base,), _, _, pts = aplicar([base], [], [], theta, fase="antes", pontos=[(x, y)])  # só a fase de antes
"""

from __future__ import annotations

import io
import math
import random

import cv2
import numpy as np
from PIL import Image

# Probabilidades de cada etapa por perfil. "captura" = print de tela ou PDF exportado (nenhuma
# degradação de aquisição). Valores do "misturado" seguem a síntese da literatura (ver docs).
PERFIS: dict[str, dict] = {
    "misturado": {"impressao": 0.2, "aquisicao": {"scanner": 0.28, "celular": 0.32, "app_scanner": 0.15, "tela": 0.10,
                                                  "captura": 0.15}, "transmissao": 0.6},
    "scanner": {"impressao": 0.0, "aquisicao": {"scanner": 1.0}, "transmissao": 0.3},
    "celular": {"impressao": 0.0, "aquisicao": {"celular": 1.0}, "transmissao": 0.7},
    "app_scanner": {"impressao": 0.0, "aquisicao": {"app_scanner": 1.0}, "transmissao": 0.6},
    "tela": {"impressao": 0.0, "aquisicao": {"tela": 1.0}, "transmissao": 0.5},
    "impressao": {"impressao": 1.0, "aquisicao": {"scanner": 0.5, "celular": 0.5}, "transmissao": 0.4},
    "whatsapp": {"impressao": 0.0, "aquisicao": {"captura": 1.0}, "transmissao": 1.0},
    # só o envio do arquivo (WhatsApp, e-mail, rede social, app): sem foto, sem scanner e sem cortar a página
    "envio": {"impressao": 0.0, "aquisicao": {"captura": 1.0}, "transmissao": 1.0, "recorte": 0.0},
}
NOMES = {"misturado": "Misturado (realista)", "scanner": "Scanner", "celular": "Foto de celular",
         "app_scanner": "Aplicativo de scanner (CamScanner, Adobe Scan)", "tela": "Tela fotografada",
         "impressao": "Impressão e nova captura", "whatsapp": "Enviado pelo WhatsApp",
         "envio": "Só o envio (WhatsApp, e-mail, rede social), sem foto"}
TRANSMISSOES = {"whatsapp": 0.36, "whatsapp_web": 0.108, "rede_social": 0.108, "email": 0.144, "app_edicao": 0.09,
                "webp": 0.09, "whatsapp_documento": 0.10}
CAPTURAS = ("auto", "antes", "depois")  # quando a captura física acontece em relação à edição
FISICAS = ("impressao", "scanner", "celular", "app_scanner", "tela")  # passos em que o par passa por papel ou tela
LADO_MAXIMO = 4200  # ampliação de app não passa disto (memória)
# máscara reamostrada: o pixel conta como editado a partir de 15% de cobertura. Com 40%, um traço de
# 1–2 px reduzido pelo canal sumia da máscara e a imagem continuava mostrando a mudança.
LIMIAR = 0.15
MENOR_LADO = 16  # cortes e reduções do canal nunca deixam a imagem menor que isto
_LUMA = np.float32([0.299, 0.587, 0.114])


def normalizar(cfg) -> dict | None:
    """``{"perfil": nome, "prob": fração das versões, "captura": auto|antes|depois}`` válido, ou None
    (sem canal). ``captura``: a captura física (scanner, celular, app) acontece antes ou depois da
    edição; "auto" segue a regra de ``sortear``."""
    if not cfg:
        return None
    if isinstance(cfg, str):
        cfg = {"perfil": cfg}
    if not isinstance(cfg, dict):
        raise ValueError(f"canal de envio inválido: {cfg!r}")
    perfil = str(cfg.get("perfil") or "nenhum")
    if perfil == "nenhum":
        return None
    if perfil not in PERFIS:
        raise ValueError(f"canal de envio desconhecido: {perfil}")
    captura = str(cfg.get("captura") or "auto")
    if captura not in CAPTURAS:
        raise ValueError(f"captura do canal desconhecida: {captura}")
    return {"perfil": perfil, "prob": max(0.0, min(1.0, float(cfg.get("prob", 1.0)))), "captura": captura}


def _escolher(rng: random.Random, pesos: dict[str, float]) -> str:
    chaves = [k for k, v in pesos.items() if v > 0]
    return rng.choices(chaves, weights=[pesos[k] for k in chaves])[0]


def _u(rng: random.Random, a: float, b: float, casas: int = 3) -> float:
    return round(rng.uniform(a, b), casas)


_PADRAO_Q: dict[int, dict] = {}


def tabela_padrao(q: int) -> dict:
    """Tabelas de quantização do libjpeg na qualidade ``q`` (as que o Pillow usa)."""
    if q not in _PADRAO_Q:
        buf = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buf, "JPEG", quality=int(q))
        t = Image.open(io.BytesIO(buf.getvalue())).quantization
        _PADRAO_Q[q] = {"luma": list(t[0]), "croma": list(t[1] if 1 in t else t[0])}
    return _PADRAO_Q[q]


def tabelas_de(caminhos) -> list[dict]:
    """Tabelas de quantização dos JPEG de entrada (as de scanners e celulares de verdade; DocQT
    mostrou que variar a TABELA, e não só a qualidade, é o que generaliza). Repetidas saem."""
    vistas, saida = set(), []
    for c in caminhos:
        try:
            with Image.open(c) as im:
                if im.format not in ("JPEG", "MPO"):  # MPO: foto de celular com miniatura
                    continue
                t = im.quantization
        except OSError:
            continue
        reg = {"luma": list(t[0]), "croma": list(t[1] if 1 in t else t[0])}
        chave = (tuple(reg["luma"]), tuple(reg["croma"]))
        if chave not in vistas:
            vistas.add(chave)
            saida.append(reg)
    return saida


def _tabela(rng: random.Random, q: int, tabelas) -> dict | None:
    """Tabela de um aparelho (das entradas), a padrão perturbada, ou None (a padrão do libjpeg)."""
    x = rng.random()
    if tabelas and x < 0.5:
        return rng.choice(tabelas)
    if x < 0.75:
        t = tabela_padrao(q)
        return {k: [max(1, min(255, round(v * rng.uniform(0.85, 1.15)))) for v in t[k]] for k in ("luma", "croma")}
    return None


def _sortear_foto(rng: random.Random) -> dict:
    """Parâmetros da foto de celular (geometria, luz, sensor), comuns ao celular e ao app de scanner."""
    return {"escala": _u(rng, 0.45, 0.85), "margem": _u(rng, 0.02, 0.08),
            "cantos": [_u(rng, -0.05, 0.05) for _ in range(8)],
            "fundo": [rng.randint(40, 200)] * 3 if rng.random() < 0.5 else
            [rng.randint(60, 160), rng.randint(40, 120), rng.randint(20, 90)],
            "luz": _u(rng, 0.05, 0.3), "luz_angulo": _u(rng, 0, 360, 1), "vinheta": _u(rng, 0.0, 0.25),
            "sombra": _u(rng, 0.6, 0.85) if rng.random() < 0.35 else None,
            "balanco": [_u(rng, 0.95, 1.05) for _ in range(3)], "desfoque": _u(rng, 0.4, 1.6),
            "movimento": [rng.choice((3, 5, 7)), _u(rng, 0, 180, 1)] if rng.random() < 0.2 else None,
            "ruido_a": _u(rng, 0.005, 0.05, 4), "ruido_b": _u(rng, 0.5, 4.0), "nitidez": _u(rng, 0.2, 0.8),
            # reflexo da luz (ou do flash) no papel brilhante: mancha clara que estoura
            "brilho": {"x": _u(rng, 0.1, 0.9), "y": _u(rng, 0.1, 0.9), "rx": _u(rng, 0.1, 0.35), "ry": _u(rng, 0.1, 0.35),
                       "forca": _u(rng, 0.15, 0.5)} if rng.random() < 0.25 else None,
            # o que há em volta do papel: mesa lisa, madeira, tecido, outra folha, granito/piso
            "textura": rng.choices(("lisa", "madeira", "tecido", "papel", "granito"), weights=(0.4, 0.25, 0.1, 0.15, 0.1))[0],
            "textura_angulo": _u(rng, 0, 180, 1), "textura_periodo": _u(rng, 4, 40, 1), "textura_claro": _u(rng, 40, 110, 1)}


def _sortear_recorte(rng: random.Random) -> dict:
    """Recorte parcial: a pessoa manda só um pedaço da página (o cabeçalho com nome e endereço, a metade de
    cima). Fração da largura e da altura e onde a janela ancora (0 = topo/esquerda); a parte de cima pesa mais."""
    return {"tipo": "recorte", "fx": _u(rng, 0.5, 1.0), "fy": _u(rng, 0.3, 0.9),
            "ancora": [_u(rng, 0, 1), round(rng.random() ** 2, 3)]}


def _sortear_desgaste(rng: random.Random) -> dict | None:
    """Desgaste físico da impressão (o que o Augraphy simula): faixas de tinta fraca (toner acabando), rolos
    sujos (riscos verticais claros), sangria da tinta (toner espalhado além do traço) e cópia de cópia
    (fotocópia ruim: mais contraste, desfoque e poeira de toner). None = impressão limpa."""
    d = {}
    if rng.random() < 0.35:
        d["tinta_fraca"] = {"periodo": _u(rng, 6, 40, 1), "fase": _u(rng, 0, 1), "forca": _u(rng, 0.2, 0.6),
                            "largura": _u(rng, 0.2, 0.6)}
    if rng.random() < 0.3:
        d["rolos"] = {"posicoes": [_u(rng, 0, 1) for _ in range(rng.randint(1, 4))], "largura": _u(rng, 2, 12, 1),
                      "forca": _u(rng, 0.15, 0.5)}
    if rng.random() < 0.4:
        d["sangria"] = _u(rng, 0.5, 2.0, 2)
    if rng.random() < 0.25:
        d["fotocopia"] = {"gama": _u(rng, 0.55, 0.85), "poeira": _u(rng, 0.0005, 0.004, 5), "desfoque": _u(rng, 0.3, 0.8)}
    return d or None


def _sortear_sujeira(rng: random.Random) -> dict:
    """Sujeira no vidro do scanner: pontos de poeira e, às vezes, um fio."""
    return {"pontos": rng.randint(5, 60), "fios": rng.randint(0, 2), "forca": _u(rng, 0.3, 0.8)}


def _sortear_curvatura(rng: random.Random) -> dict:
    """Papel que não está plano: 1–2 ondas suaves (deslocamento ao longo de ``eixo`` em função da
    outra coordenada; amplitude em fração da altura, frequência em ciclos por página, fase em
    voltas) e, às vezes, um vinco (dobra reta em ``posicao``: a inclinação muda dali para a frente)."""
    ondas = [[rng.choice(("x", "y")), _u(rng, 0.003, 0.02, 4), _u(rng, 0.5, 1.5, 2), _u(rng, 0, 1)]
             for _ in range(rng.choice((1, 2)))]
    vinco = [rng.choice(("x", "y")), _u(rng, 0.25, 0.75, 2), round(rng.choice((-1, 1)) * rng.uniform(0.005, 0.02), 4)] \
        if rng.random() < 0.3 else None
    return {"ondas": ondas, "vinco": vinco}


def _sortear_lente(rng: random.Random) -> dict:
    """Distorção radial (k1 > 0 barril, < 0 almofada) e aberração cromática (px entre R e B no canto)."""
    return {"k1": round(rng.choice((-1, 1)) * rng.uniform(0.03, 0.12), 3), "cromatica": _u(rng, 0, 1.5, 2)}


def _sortear_reticula(rng: random.Random) -> dict:
    """Retícula de meio-tom: período em px e um ângulo por canal (laser/jato a cores) ou um só."""
    angulos = rng.sample([15, 45, 75], 3) if rng.random() < 0.5 else [rng.choice((15, 45, 75))]
    return {"periodo": _u(rng, 3, 6, 2), "angulos": angulos, "forca": _u(rng, 0.4, 0.9, 2)}


def sortear(rng: random.Random, cfg, tabelas: list[dict] | None = None, captura: str = "auto",
            entrada_capturada: bool = False) -> dict | None:
    """Sorteia o canal de UM par. None quando a versão fica sem canal (fração ``prob``).
    ``tabelas``: tabelas de quantização dos JPEG de entrada do split (ver ``tabelas_de``).
    ``captura`` ("auto" usa o da cfg): a captura física sem impressão vem "antes" da edição (quem
    edita no celular fotografa primeiro) ou "depois" (comportamento antigo). ``entrada_capturada``:
    o arquivo de entrada já é uma foto/scan em JPEG; no "auto", metade das vezes a captura de antes
    é descartada (a entrada já a carrega) e sobra só a transmissão, ou nada."""
    cfg = normalizar(cfg)
    if cfg is None or rng.random() >= cfg["prob"]:
        return None
    if captura == "auto":
        captura = cfg["captura"]
    perfil = PERFIS[cfg["perfil"]]
    passos = []
    aq = dict(perfil["aquisicao"])
    impressa = rng.random() < perfil["impressao"]
    if impressa:
        passos.append({"tipo": "impressao", "espalhamento": _u(rng, 0.15, 0.55), "toner": _u(rng, 0.04, 0.14),
                       "papel": _u(rng, 1.5, 5.0), "reticula": _sortear_reticula(rng) if rng.random() < 0.5 else None,
                       "desgaste": _sortear_desgaste(rng), "fase": "depois"})
        aq = {k: v for k, v in aq.items() if k in ("scanner", "celular", "app_scanner")} or {"scanner": 0.5, "celular": 0.5}
    tipo = _escolher(rng, aq)
    # a captura física sem impressão é de antes da edição (quem edita no celular fotografa primeiro)
    fase = "antes" if tipo in ("scanner", "celular", "app_scanner") and not impressa and captura != "depois" else "depois"
    if tipo == "scanner":
        modo = rng.choices(("cor", "cinza", "pb"), weights=(0.6, 0.3, 0.1))[0]
        passos.append({"tipo": "scanner", "angulo": _u(rng, -1.5, 1.5), "escala": _u(rng, 0.55, 1.0),
                       "desfoque": _u(rng, 0.3, 1.0), "gama": _u(rng, 0.85, 1.15), "contraste": _u(rng, 0.9, 1.1),
                       "brilho": _u(rng, -8, 8, 1), "ruido": _u(rng, 0.5, 2.5),
                       # modo do driver: colorido, tons de cinza ou "texto"/P&B (binarizado)
                       "modo": modo, **({"limiar": _u(rng, 0.25, 0.45, 2), "suavidade": _u(rng, 2, 8, 1)} if modo == "pb" else {}),
                       # a impressão do verso aparecendo de leve (papel fino) e poeira/fio no vidro
                       "verso": _u(rng, 0.06, 0.2) if rng.random() < 0.3 else None,
                       "sujeira": _sortear_sujeira(rng) if rng.random() < 0.4 else None,
                       "jpeg": rng.randint(75, 95) if rng.random() < 0.7 else None, "fase": fase})
        if passos[-1]["jpeg"]:
            passos[-1]["tabela"] = _tabela(rng, passos[-1]["jpeg"], tabelas)
    elif tipo == "celular":
        passos.append({"tipo": "celular", **_sortear_foto(rng),
                       "curvatura": _sortear_curvatura(rng) if rng.random() < 0.6 else None,
                       "lente": _sortear_lente(rng) if rng.random() < 0.5 else None, "jpeg": rng.randint(85, 95),
                       "fase": fase})
        passos[-1]["tabela"] = _tabela(rng, passos[-1]["jpeg"], tabelas)
    elif tipo == "app_scanner":  # CamScanner, Adobe Scan, Notas do iPhone: foto, recorte/retificação, realce
        modo = rng.choice(("cor", "cinza", "pb"))
        realce = {"limiar": _u(rng, 0.15, 0.3, 2), "suavidade": _u(rng, 4, 10, 1)} if modo == "pb" else \
            {"preto": _u(rng, 15, 50, 1), "realce": _u(rng, 0.8, 1.6, 2) if modo == "cor" else _u(rng, 0.4, 1.0, 2),
             **({"saturacao": _u(rng, 0.1, 0.3, 2)} if modo == "cor" else {})}
        passos.append({"tipo": "app_scanner", **_sortear_foto(rng),
                       "curvatura": _sortear_curvatura(rng) if rng.random() < 0.5 else None,
                       "lente": _sortear_lente(rng) if rng.random() < 0.5 else None,
                       "dewarp": [round(rng.choice((-1, 1)) * rng.uniform(0.003, 0.015), 4) for _ in range(8)],
                       "modo": modo, **realce, "jpeg": rng.randint(60, 85), "fase": fase})
        passos[-1]["tabela"] = _tabela(rng, passos[-1]["jpeg"], tabelas)
    elif tipo == "tela":
        passos.append({"tipo": "tela", "escala_tela": _u(rng, 0.5, 0.8), "periodo": _u(rng, 2.6, 4.0),
                       "angulo_grade": _u(rng, -8, 8, 1), "forca": _u(rng, 0.08, 0.25),
                       # subpixels em listras (LCD) ou PenTile (OLED de celular); ponto branco azulado da tela e, às
                       # vezes, a faixa escura do refresh pega pelo obturador rolante (CMA: a cromaticidade da
                       # recaptura sobrevive à compressão das redes)
                       "subpixel": rng.choice(("listra", "listra", "pentile")),
                       "balanco": [_u(rng, 0.95, 1.0), 1.0, _u(rng, 1.0, 1.08)],
                       "banda": [_u(rng, 0.1, 0.9), _u(rng, 0.01, 0.04, 3), _u(rng, 0.05, 0.15)] if rng.random() < 0.3 else None,
                       "cantos": [_u(rng, -0.03, 0.03) for _ in range(8)], "escala": _u(rng, 0.6, 0.9),
                       "gama": _u(rng, 0.8, 1.0), "desfoque": _u(rng, 0.5, 1.2), "ruido_a": _u(rng, 0.005, 0.03, 4),
                       "ruido_b": _u(rng, 0.5, 3.0), "lente": _sortear_lente(rng) if rng.random() < 0.5 else None,
                       "jpeg": rng.randint(85, 95), "fase": "depois"})
        passos[-1]["tabela"] = _tabela(rng, passos[-1]["jpeg"], tabelas)
    else:
        passos.append({"tipo": "captura", "fase": "depois"})
    if fase == "antes" and captura == "auto" and entrada_capturada and rng.random() < 0.5:
        passos.pop()  # a entrada já é uma foto/scan: a captura de antes já está nela
    if rng.random() < perfil.get("recorte", 0.2):  # só um pedaço da página é enviado (na fase da captura: antes ou depois da edição)
        passos.append({**_sortear_recorte(rng), "fase": passos[-1].get("fase", "depois") if passos else "depois"})
    so_whatsapp = cfg["perfil"] == "whatsapp"
    if rng.random() < perfil["transmissao"]:
        for _ in range(rng.choices((1, 2, 3), weights=(0.6, 0.3, 0.1))[0]):
            t = "whatsapp" if so_whatsapp else _escolher(rng, TRANSMISSOES)
            if t == "whatsapp":
                passo = {"tipo": t, "lado": 1600, "jpeg": rng.randint(70, 80)}
            elif t == "whatsapp_web":
                passo = {"tipo": t, "lado": 1600, "jpeg": rng.randint(88, 94)}
            elif t == "rede_social":
                passo = {"tipo": t, "lado": 2048, "jpeg": rng.randint(71, 95)}
            elif t == "app_edicao":  # app que reamostra (0,7–1,3) e regrava em JPEG ou WEBP
                passo = {"tipo": t, "escala": _u(rng, 0.7, 1.3)}
                passo["jpeg" if rng.random() < 0.6 else "webp"] = rng.randint(70, 95)
            elif t == "webp":
                passo = {"tipo": t, "webp": rng.randint(70, 90)}
            else:  # e-mail ou WhatsApp como documento: nada muda
                passo = {"tipo": t}
            if t not in ("email", "whatsapp_documento") and rng.random() < 0.5:  # corte de borda: tira a grade 8x8 do lugar
                passo["deslocamento"] = [rng.randint(1, 7), rng.randint(1, 7)]
            passo["fase"] = "depois"
            passos.append(passo)
    return {"perfil": cfg["perfil"], "semente": rng.randrange(2**31), "passos": passos}


def _jq(p: dict) -> str:
    """Com tabela própria (de aparelho ou perturbada), a qualidade nominal não descreve o JPEG."""
    if not p.get("jpeg"):
        return "sem JPEG"
    return f"JPEG com tabela própria (~{p['jpeg']})" if p.get("tabela") else f"JPEG {p['jpeg']}"


def descrever(theta: dict | None) -> str:
    if not theta:
        return "sem canal (imagem como está)"
    if not theta["passos"]:
        return "sem passos (a captura já veio na entrada)"
    partes = []
    for p in theta["passos"]:
        t = p["tipo"]
        otica = (", papel curvado" if p.get("curvatura") else "") + (", lente" if p.get("lente") else "")
        if t == "impressao":
            partes.append("impressa" + (" (retícula)" if p.get("reticula") else "")
                          + (" (desgaste: " + ", ".join(p["desgaste"]) + ")" if p.get("desgaste") else ""))
        elif t == "scanner":
            partes.append(f"escaneada ({p['angulo']:+.1f}°, escala {p['escala']:.2f}"
                          + ({"cinza": ", tons de cinza", "pb": ", preto e branco"}.get(p.get("modo"), ""))
                          + (", verso aparecendo" if p.get("verso") else "") + (", sujeira no vidro" if p.get("sujeira") else "")
                          + (f", {_jq(p)})" if p.get("jpeg") else ")"))
        elif t == "celular":
            partes.append(f"fotografada com celular (escala {p['escala']:.2f}"
                          + (f", sobre {p['textura']}" if p.get("textura") and p["textura"] != "lisa" else "")
                          + (", sombra" if p.get("sombra") else "") + (", tremida" if p.get("movimento") else "")
                          + (", reflexo" if p.get("brilho") else "") + otica + f", {_jq(p)})")
        elif t == "recorte":
            partes.append(f"recortada ({p['fx']:.0%} × {p['fy']:.0%} da página)")
        elif t == "app_scanner":
            partes.append(f"digitalizada por app de scanner (modo {p['modo']}, escala {p['escala']:.2f}" + otica
                          + (", reflexo" if p.get("brilho") else "") + f", {_jq(p)})")
        elif t == "tela":
            partes.append(f"mostrada numa tela e fotografada (moiré{', PenTile' if p.get('subpixel') == 'pentile' else ''}"
                          f"{', faixa do refresh' if p.get('banda') else ''}{otica}, {_jq(p)})")
        elif t == "captura":
            partes.append("capturada sem degradação")
        elif t == "email":
            partes.append("enviada por e-mail (sem mudança)")
        elif t == "whatsapp_documento":
            partes.append("enviada pelo WhatsApp como documento (sem mudança)")
        elif t == "app_edicao":
            partes.append(f"regravada num app (escala {p['escala']:.2f}, "
                          + (f"JPEG {p['jpeg']})" if p.get("jpeg") else f"WEBP {p['webp']})"))
        elif t == "webp":
            partes.append(f"convertida para WEBP {p['webp']}")
        elif t == "salvar_editor":
            partes.append(f"salva em JPEG pelo editor ({str(p.get('programa', '')).replace('_', ' ')}, "
                          + (f"preset {p['preset']})" if p.get("preset") else f"qualidade {p.get('qualidade')})"))
        else:
            partes.append(f"enviada por {t.replace('_', ' ')} (lado ≤ {p['lado']}, JPEG {p['jpeg']})")
        if p.get("fase") == "antes":
            partes[-1] += " (antes da edição)"
    return ", depois ".join(partes)


# ---------------------------------------------------------------------------------------- aplicação
class _Rotulos:
    """Mapa de rótulos (classe, nº da edição) levado pela geometria como UMA cobertura por valor
    (uint8, 0–255). No fim, cada pixel fica com o valor de maior cobertura. O vizinho mais próximo
    pulava traços finos inteiros ao reduzir, e a edição ficava sem rótulo."""

    def __init__(self, r: np.ndarray):
        r = np.asarray(r)
        self.dtype = r.dtype if r.dtype != bool else np.uint8
        self.valores = [v for v in np.unique(r).tolist() if v != 0]
        self.cob = [(r == v).astype(np.uint8) * 255 for v in self.valores]

    def mapear(self, f) -> None:
        self.cob = [f(c) for c in self.cob]

    def montar(self, forma) -> np.ndarray:
        if not self.cob:
            return np.zeros(forma, self.dtype)
        pilha = np.stack(self.cob)
        melhor = pilha.argmax(0)
        saida = np.asarray(self.valores, self.dtype)[melhor]
        saida[pilha.max(0) == 0] = 0
        return saida


class _Par:
    """As imagens (float32), as máscaras e os pontos (x, y) de um par, transformados sempre juntos."""

    def __init__(self, imgs, binarias, rotulos, pontos=None):
        self.imgs = [np.asarray(i, np.float32) for i in imgs]
        self.bins = [np.asarray(b, np.float32) for b in binarias]
        self.rots = [_Rotulos(r) for r in rotulos]
        self.pontos = None if pontos is None else np.asarray(pontos, np.float64).reshape(-1, 2)

    @property
    def forma(self) -> tuple[int, int]:
        return self.imgs[0].shape[:2]

    def mapear(self, f) -> None:
        self.imgs = [f(i) for i in self.imgs]

    def cor_borda(self) -> tuple[float, float, float]:
        """Cor da borda da ORIGINAL (a mesma para o par inteiro)."""
        im = self.imgs[0]
        b = np.concatenate([im[:4].reshape(-1, 3), im[-4:].reshape(-1, 3), im[:, :4].reshape(-1, 3), im[:, -4:].reshape(-1, 3)])
        return tuple(float(v) for v in np.median(b, axis=0))

    def geometria(self, M, tamanho: tuple[int, int], fundo, perspectiva: bool = False) -> None:
        w, h = tamanho
        warp = cv2.warpPerspective if perspectiva else cv2.warpAffine
        self.imgs = [warp(i, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=fundo)
                     for i in self.imgs]
        self.bins = [warp(b, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                     for b in self.bins]
        for r in self.rots:
            r.mapear(lambda c: warp(c, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0))
        if self.pontos is not None:
            M = np.asarray(M, np.float64)
            if perspectiva:
                h3 = np.c_[self.pontos, np.ones(len(self.pontos))] @ M.T
                self.pontos = h3[:, :2] / h3[:, 2:3]
            else:
                self.pontos = self.pontos @ M[:, :2].T + M[:, 2]

    def redimensionar(self, w: int, h: int) -> None:
        H, W = self.forma
        if (w, h) == (W, H):
            return
        interp = cv2.INTER_AREA if w < W else cv2.INTER_CUBIC
        self.imgs = [cv2.resize(i, (w, h), interpolation=interp) for i in self.imgs]
        im = cv2.INTER_AREA if w < W else cv2.INTER_LINEAR
        self.bins = [cv2.resize(b, (w, h), interpolation=im) for b in self.bins]
        for r in self.rots:
            r.mapear(lambda c: cv2.resize(c, (w, h), interpolation=im))
        if self.pontos is not None:  # centro do pixel, como o cv2.resize
            self.pontos = (self.pontos + 0.5) * np.float64([w / W, h / H]) - 0.5

    def recortar(self, dx: int, dy: int) -> None:
        H, W = self.forma
        dx, dy = max(0, min(int(dx), W - MENOR_LADO)), max(0, min(int(dy), H - MENOR_LADO))
        self.janela(dx, dy, W, H)

    def janela(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Fica só com a janela [x0:x1, y0:y1] (imagens, máscaras, rótulos e pontos juntos)."""
        self.imgs = [i[y0:y1, x0:x1] for i in self.imgs]
        self.bins = [b[y0:y1, x0:x1] for b in self.bins]
        for r in self.rots:
            r.mapear(lambda c: c[y0:y1, x0:x1])
        if self.pontos is not None:
            self.pontos = self.pontos - np.float64([x0, y0])

    def editada(self) -> np.ndarray | None:
        """União das máscaras binárias (onde há edição), ou None sem máscara."""
        if not self.bins:
            return None
        return np.any([b > 0.5 for b in self.bins], axis=0)

    def remap(self, mapx: np.ndarray, mapy: np.ndarray, fundo, cromatica=None) -> None:
        """Campo de deslocamento suave (cv2.remap: saída(x, y) = entrada(mapx, mapy)) nas imagens,
        máscaras, rótulos e pontos. ``cromatica``: mapas próprios dos canais R e B (aberração
        cromática); máscaras e pontos seguem o do verde."""
        H, W = mapx.shape
        kw = dict(interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

        def f(im):
            if cromatica is None:
                return cv2.remap(im, mapx, mapy, borderValue=fundo, **kw)
            (xr, yr), (xb, yb) = cromatica
            r, g, b = cv2.split(im)
            return cv2.merge([cv2.remap(r, xr, yr, borderValue=fundo[0], **kw),
                              cv2.remap(g, mapx, mapy, borderValue=fundo[1], **kw),
                              cv2.remap(b, xb, yb, borderValue=fundo[2], **kw)])
        self.imgs = [f(i) for i in self.imgs]
        self.bins = [cv2.remap(b, mapx, mapy, borderValue=0, **kw) for b in self.bins]
        for r in self.rots:
            r.mapear(lambda c: cv2.remap(c, mapx, mapy, borderValue=0, **kw))
        if self.pontos is not None and len(self.pontos):
            # o ponto p da entrada cai onde o mapa aponta para ele: q = p - d(q), por ponto fixo
            dx = mapx - np.arange(W, dtype=np.float32)
            dy = mapy - np.arange(H, dtype=np.float32)[:, None]
            q = self.pontos
            for _ in range(3):
                xi = np.clip(np.rint(q[:, 0]), 0, W - 1).astype(int)
                yi = np.clip(np.rint(q[:, 1]), 0, H - 1).astype(int)
                q = self.pontos - np.stack([dx[yi, xi], dy[yi, xi]], 1)
            self.pontos = q

    def jpeg(self, q: int, tabela: dict | None = None) -> None:
        extra = {"qtables": [tabela["luma"], tabela["croma"]]} if tabela else {"quality": int(q)}
        self._regravar("JPEG", subsampling=2, **extra)

    def webp(self, q: int) -> None:
        self._regravar("WEBP", quality=int(q), method=4)

    def _regravar(self, formato: str, **kw) -> None:
        def ida_volta(i):
            buf = io.BytesIO()
            Image.fromarray(np.clip(i + 0.5, 0, 255).astype(np.uint8)).save(buf, formato, **kw)
            return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), np.float32)
        self.imgs = [ida_volta(i) for i in self.imgs]


def _ruido(rng: np.random.Generator, forma) -> np.ndarray:
    return rng.standard_normal(forma, dtype=np.float32)


def _reticula(par: _Par, r: dict) -> None:
    """Retícula de meio-tom da impressora: nos tons entre papel e tinta (luminância 10–90%) entra um
    padrão periódico de pontos (um ângulo por canal, como na impressão a cores, ou um só); papel e
    tinta preta ficam como estão. O ponto é suave e preserva o tom médio."""
    H, W = par.forma
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    T = r["periodo"]
    telas = []
    for a in (r["angulos"] if len(r["angulos"]) == 3 else r["angulos"] * 3):
        a = math.radians(a)
        u, v = x * math.cos(a) + y * math.sin(a), y * math.cos(a) - x * math.sin(a)
        telas.append(0.5 + 0.5 * np.cos(2 * np.pi * u / T) * np.cos(2 * np.pi * v / T))
    tela = np.stack(telas, -1).astype(np.float32)
    forca = r["forca"]

    def f(im):
        t = im / 255.0
        lum = t @ _LUMA
        peso = (np.clip(np.minimum(lum - 0.1, 0.9 - lum) / 0.1, 0, 1) * forca)[..., None]
        ponto = np.clip((t - tela) / 0.3 + 0.5, 0, 1)  # 1 = papel, 0 = tinta, borda suave
        return im * (1 - peso) + 255 * ponto * peso
    par.mapear(f)


def _impressao(par: _Par, p: dict, rng: np.random.Generator) -> None:
    """Retícula (às vezes), toner espalhado em volta do traço, textura do toner dentro da tinta e grão
    do papel."""
    if p.get("reticula"):
        _reticula(par, p["reticula"])
    H, W = par.forma
    textura = cv2.GaussianBlur(_ruido(rng, (H, W)), (0, 0), 0.8)
    textura /= textura.std() + 1e-6
    papel = cv2.resize(cv2.GaussianBlur(_ruido(rng, (H // 16 + 1, W // 16 + 1)), (0, 0), 2), (W, H),
                       interpolation=cv2.INTER_CUBIC)
    papel /= papel.std() + 1e-6
    fibra = _ruido(rng, (H, W))
    k = np.ones((3, 3), np.uint8)
    a, toner, grao = p["espalhamento"], p["toner"], p["papel"]

    def f(im):
        im = im * (1 - a) + cv2.erode(im, k) * a
        im = 255 - (255 - im) * (1 + toner * textura[..., None])
        return im + (grao * papel + 0.6 * fibra)[..., None]
    par.mapear(f)
    if p.get("desgaste"):
        _desgaste(par, p["desgaste"], rng)


def _desgaste(par: _Par, d: dict, rng: np.random.Generator) -> None:
    """Desgaste da impressão, sobre a ESCURIDÃO (255 − im): faixas horizontais de tinta fraca, riscos verticais
    claros dos rolos, sangria (tinta espalhada além do traço) e fotocópia ruim (contraste, desfoque, poeira de
    toner). Os campos (faixas, riscos, poeira) são sorteados uma vez e valem para o par; a sangria e o
    contraste agem na tinta de cada imagem (na editada, também na tinta nova, como numa impressão de verdade)."""
    H, W = par.forma
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    f = np.ones((H, W), np.float32)  # fator sobre a escuridão: 1 = tinta como está, < 1 = tinta fraca
    if d.get("tinta_fraca"):
        t = d["tinta_fraca"]
        faixa = 0.5 + 0.5 * np.cos(2 * np.pi * (y / t["periodo"] + t["fase"]))
        f *= 1 - t["forca"] * np.clip((faixa - (1 - t["largura"])) / max(t["largura"], 1e-3), 0, 1)
    if d.get("rolos"):
        r = d["rolos"]
        for pos in r["posicoes"]:
            f *= 1 - r["forca"] * np.exp(-0.5 * ((x - pos * W) / r["largura"]) ** 2)
    c = d.get("fotocopia")
    poeira = None
    if c:
        poeira = cv2.GaussianBlur((rng.random((H, W)) < c["poeira"]).astype(np.float32)
                                  * rng.uniform(60, 160, (H, W)).astype(np.float32), (0, 0), 0.6)

    def aplica(im):
        esc = 255.0 - im
        if d.get("sangria"):
            s = d["sangria"]
            k = int(2 * round(s) + 1)
            dl = cv2.dilate(esc, np.ones((k, k), np.uint8))
            esc = np.maximum(esc, 0.75 * cv2.GaussianBlur(dl, (0, 0), max(0.5, 0.6 * s)))
        esc = esc * f[..., None]
        if c:
            esc = 255.0 * np.power(np.clip(esc / 255.0, 0, 1), c["gama"])
            esc = cv2.GaussianBlur(esc, (0, 0), c["desfoque"]) + poeira[..., None]
        return 255.0 - esc
    par.mapear(aplica)


def _sujeira(par: _Par, s: dict, rng: np.random.Generator) -> None:
    """Poeira e fios no vidro do scanner: pontos escuros pequenos e, às vezes, um fio fino; o mesmo campo no par."""
    H, W = par.forma
    m = np.zeros((H, W), np.float32)
    for _ in range(int(s["pontos"])):
        cx, cy = int(rng.integers(0, W)), int(rng.integers(0, H))
        cv2.circle(m, (cx, cy), int(rng.integers(1, 3)), float(rng.uniform(40, 120)), -1, cv2.LINE_AA)
    for _ in range(int(s["fios"])):
        pts = [(float(rng.uniform(0, W)), float(rng.uniform(0, H)))]
        ang = float(rng.uniform(0, 2 * np.pi))
        for _ in range(int(rng.integers(3, 7))):
            ang += float(rng.uniform(-0.6, 0.6))
            passo = float(rng.uniform(15, 40))
            pts.append((pts[-1][0] + passo * math.cos(ang), pts[-1][1] + passo * math.sin(ang)))
        cv2.polylines(m, [np.int32(np.round(pts))], False, float(rng.uniform(50, 110)), 1, cv2.LINE_AA)
    m = cv2.GaussianBlur(m, (0, 0), 0.5) * s["forca"]
    par.mapear(lambda im: im - m[..., None])


def _tonal(gama: float, contraste: float = 1.0, brilho: float = 0.0):
    def f(im):
        x = np.clip(im * contraste + brilho, 0, 255) / 255.0
        return 255.0 * np.power(x, gama)
    return f


def _sensor(par: _Par, a: float, b: float, rng: np.random.Generator) -> None:
    """Ruído de sensor (Poisson-Gauss): variância a·I + b. O MESMO campo nas duas imagens."""
    z = _ruido(rng, par.forma + (3,))
    par.mapear(lambda im: im + z * np.sqrt(np.maximum(a * im + b, 0)))


def _scanner(par: _Par, p: dict, rng: np.random.Generator) -> None:
    H, W = par.forma
    ang = p["angulo"]
    if abs(ang) > 0.01:  # gira e aumenta a tela para não cortar: aparece a borda do vidro do scanner
        c, s = abs(math.cos(math.radians(ang))), abs(math.sin(math.radians(ang)))
        w2, h2 = int(math.ceil(W * c + H * s)), int(math.ceil(W * s + H * c))
        M = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
        M[0, 2] += (w2 - W) / 2
        M[1, 2] += (h2 - H) / 2
        par.geometria(M, (w2, h2), par.cor_borda())
    H, W = par.forma
    par.redimensionar(max(16, round(W * p["escala"])), max(16, round(H * p["escala"])))
    par.mapear(lambda im: cv2.GaussianBlur(im, (0, 0), p["desfoque"]))
    if p.get("verso"):  # o verso impresso aparece de leve, espelhado; o campo vem da ORIGINAL (o verso não muda com a edição)
        verso = cv2.GaussianBlur((255.0 - par.imgs[0])[:, ::-1], (0, 0), 2.0) * p["verso"]
        par.mapear(lambda im: im - verso)
    if p.get("sujeira"):
        _sujeira(par, p["sujeira"], rng)
    par.mapear(_tonal(p["gama"], p["contraste"], p["brilho"]))
    z = _ruido(rng, par.forma + (3,)) * p["ruido"]
    par.mapear(lambda im: im + z)
    if p.get("modo") == "cinza":  # scanner em tons de cinza (o modo padrão de muita multifuncional)
        par.mapear(lambda im: np.repeat((im @ _LUMA)[..., None], 3, 2))
    elif p.get("modo") == "pb":  # "preto e branco"/"texto": o driver binariza com limiar fixo abaixo do papel
        papel = float(np.percentile(par.imgs[0] @ _LUMA, 90))
        t = papel * (1 - p["limiar"])

        def binarizar(im):
            lum = im @ _LUMA
            pb = 1 / (1 + np.exp(np.clip((t - lum) / p["suavidade"], -30, 30)))
            return np.repeat((255 * pb)[..., None], 3, 2)
        par.mapear(binarizar)
    if p.get("jpeg"):
        par.jpeg(p["jpeg"], p.get("tabela"))


def _textura_fundo(Hc: int, Wc: int, p: dict, rng: np.random.Generator) -> np.ndarray:
    """O que aparece em volta do papel na foto (float32 Hc×Wc, somado à cor ``fundo``): mesa lisa, madeira
    (veios), tecido (trama), outra folha por baixo (mais clara) ou granito/piso (manchas). Determinístico pelo
    gerador do passo: igual nas duas imagens do par."""
    tipo = p.get("textura") or "lisa"
    fino = cv2.GaussianBlur(_ruido(rng, (Hc, Wc)), (0, 0), 1.5) * 6
    if tipo == "lisa":
        return fino
    y, x = np.mgrid[0:Hc, 0:Wc].astype(np.float32)
    if tipo == "madeira":
        ang = math.radians(float(p.get("textura_angulo", 0.0)))
        u = x * math.cos(ang) + y * math.sin(ang)
        T = float(p.get("textura_periodo", 24.0))
        veios = np.sin(2 * np.pi * u / T + 2.5 * np.sin(u / (6 * T)) + 0.4 * np.sin(y / 37.0))
        return fino + 14 * veios + cv2.GaussianBlur(_ruido(rng, (Hc, Wc)), (0, 0), 0.8) * 4
    if tipo == "tecido":
        T = float(p.get("textura_periodo", 5.0))
        return fino + 9 * (np.sin(2 * np.pi * x / T) + np.sin(2 * np.pi * y / T))
    if tipo == "papel":  # outra folha por baixo: mais clara que a mesa, grão fino
        return fino + float(p.get("textura_claro", 70.0))
    # granito/piso: manchas em duas escalas
    return fino + cv2.GaussianBlur(_ruido(rng, (Hc, Wc)), (0, 0), 1.0) * 25 + cv2.GaussianBlur(_ruido(rng, (Hc, Wc)), (0, 0), 5.0) * 18


def _quadro(W: int, H: int, Wc: int, Hc: int, margem: float, cantos) -> np.ndarray:
    m = np.float32([margem * Wc, margem * Hc])
    destino = np.float32([[0, 0], [Wc, 0], [Wc, Hc], [0, Hc]]) * (1 - 2 * margem) + m
    destino += np.float32(cantos).reshape(4, 2) * np.float32([Wc, Hc])
    origem = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    return cv2.getPerspectiveTransform(origem, destino)


def _luz(H: int, W: int, p: dict, rng: np.random.Generator) -> np.ndarray:
    """Campo multiplicativo de iluminação: rampa, vinheta e (às vezes) a sombra de uma mão/celular."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    x, y = x / W * 2 - 1, y / H * 2 - 1
    ang = math.radians(p["luz_angulo"])
    campo = 1 + 0.5 * p["luz"] * (math.cos(ang) * x + math.sin(ang) * y) - p["vinheta"] * (x * x + y * y) / 2
    if p.get("sombra"):
        borda = rng.integers(4)
        t = rng.uniform(0.2, 0.8, 3)
        pts = {0: [(t[0] * W, 0), (t[1] * W, 0), (t[2] * W, rng.uniform(0.3, 0.7) * H)],
               1: [(W, t[0] * H), (W, t[1] * H), (rng.uniform(0.3, 0.7) * W, t[2] * H)],
               2: [(t[0] * W, H), (t[1] * W, H), (t[2] * W, rng.uniform(0.3, 0.7) * H)],
               3: [(0, t[0] * H), (0, t[1] * H), (rng.uniform(0.3, 0.7) * W, t[2] * H)]}[int(borda)]
        m = np.zeros((H, W), np.float32)
        cv2.fillPoly(m, [np.int32(pts)], 1.0)
        m = cv2.GaussianBlur(m, (0, 0), 0.03 * min(H, W))
        campo *= 1 - (1 - p["sombra"]) * m
    if p.get("brilho"):  # reflexo da luz no papel brilhante: mancha clara que estoura
        b = p["brilho"]
        bx, by = b["x"] * 2 - 1, b["y"] * 2 - 1
        campo *= 1 + b["forca"] * np.exp(-(((x - bx) / b["rx"]) ** 2 + ((y - by) / b["ry"]) ** 2))
    return campo


def _campo_curvatura(H: int, W: int, c: dict) -> tuple[np.ndarray, np.ndarray]:
    """Mapas (cv2.remap) do papel curvado: ondas suaves e, se houver, um vinco."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    dx, dy = np.zeros((H, W), np.float32), np.zeros((H, W), np.float32)
    for eixo, amp, freq, fase in c["ondas"]:
        if eixo == "y":  # as linhas horizontais ondulam: deslocamento vertical em função de x
            dy += amp * H * np.sin(2 * np.pi * (freq * x / W + fase))
        else:
            dx += amp * H * np.sin(2 * np.pi * (freq * y / H + fase))
    if c.get("vinco"):
        eixo, pos, incl = c["vinco"]
        if eixo == "y":
            dy += incl * np.maximum(y - pos * H, 0)
        else:
            dx += incl * np.maximum(x - pos * W, 0)
    return (x + dx).astype(np.float32), (y + dy).astype(np.float32)


def _campo_lente(H: int, W: int, l: dict):
    """Mapas da distorção radial (k1 > 0 barril, < 0 almofada; raio normalizado pelo lado maior) e,
    para R e B, deslocados radialmente em sentidos opostos (aberração cromática: ``cromatica`` px
    entre os dois no canto). Devolve (mapx, mapy, cromatica) para ``_Par.remap``."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    r2 = ((x - cx) ** 2 + (y - cy) ** 2) / float(max(W, H)) ** 2
    canto = math.hypot(cx, cy)

    def mapa(extra: float):
        g = 1 + l["k1"] * r2 + extra / canto
        return (cx + (x - cx) * g).astype(np.float32), (cy + (y - cy) * g).astype(np.float32)
    c = l.get("cromatica") or 0
    mapx, mapy = mapa(0)
    return mapx, mapy, (mapa(c / 2), mapa(-c / 2)) if c else None


def _curvar(par: _Par, c: dict) -> np.ndarray:
    """Papel curvado (antes da perspectiva). Devolve onde os 4 cantos da página foram parar."""
    H, W = par.forma
    tinha = par.pontos is not None
    par.pontos = np.vstack([par.pontos if tinha else np.zeros((0, 2)), [[0, 0], [W, 0], [W, H], [0, H]]])
    par.remap(*_campo_curvatura(H, W, c), par.cor_borda())
    cantos = par.pontos[-4:].astype(np.float32)
    par.pontos = par.pontos[:-4] if tinha else None
    return cantos


def _foto(par: _Par, p: dict, rng: np.random.Generator, M: np.ndarray, tamanho: tuple[int, int]) -> None:
    """Foto de celular (tudo menos o JPEG): a página vai pela perspectiva ``M`` para o quadro
    ``tamanho`` (W, H) sobre a mesa; depois lente, luz, desfoque, tremido, sensor e nitidez do
    aparelho, nesse quadro."""
    H, W = par.forma
    Wc, Hc = tamanho
    fundo = tuple(float(v) for v in p["fundo"])
    par.geometria(M, (Wc, Hc), fundo, perspectiva=True)
    # textura da mesa só FORA do papel (dentro, o papel já tem o próprio grão)
    papel = cv2.warpPerspective(np.ones((H, W), np.float32), M, (Wc, Hc), flags=cv2.INTER_LINEAR)
    fundo_textura = _textura_fundo(Hc, Wc, p, rng) * (1 - papel)
    campo = _luz(Hc, Wc, p, rng)
    ganho = np.float32(p["balanco"])
    par.mapear(lambda im: (im + fundo_textura[..., None]) * campo[..., None] * ganho)
    if p.get("lente"):
        mapx, mapy, cromatica = _campo_lente(Hc, Wc, p["lente"])
        par.remap(mapx, mapy, fundo, cromatica)
    par.mapear(lambda im: cv2.GaussianBlur(im, (0, 0), p["desfoque"]))
    if p.get("movimento"):
        n, ang = p["movimento"]
        k = np.zeros((n, n), np.float32)
        k[n // 2, :] = 1
        k = cv2.warpAffine(k, cv2.getRotationMatrix2D(((n - 1) / 2, (n - 1) / 2), ang, 1.0), (n, n))
        k /= k.sum()
        par.mapear(lambda im: cv2.filter2D(im, -1, k))
    _sensor(par, p["ruido_a"], p["ruido_b"], rng)
    par.mapear(lambda im: im + p["nitidez"] * (im - cv2.GaussianBlur(im, (0, 0), 1.0)))


def _celular(par: _Par, p: dict, rng: np.random.Generator) -> None:
    if p.get("curvatura"):
        _curvar(par, p["curvatura"])
    H, W = par.forma
    Wc, Hc = max(16, round(W * p["escala"])), max(16, round(H * p["escala"]))
    _foto(par, p, rng, _quadro(W, H, Wc, Hc, p["margem"], p["cantos"]), (Wc, Hc))
    if p.get("jpeg"):
        par.jpeg(p["jpeg"], p.get("tabela"))


def _realce(par: _Par, p: dict) -> None:
    """O realce do app de scanner. O fundo local (papel) é estimado na ORIGINAL, o mesmo campo nas
    duas imagens, como o resto do canal. "cor": papel a branco, contraste esticado, saturação e
    nitidez fortes; "cinza": o mesmo, dessaturado; "pb": binarização adaptativa suave (sigmoide em
    volta do limiar local): fundo quase branco, tinta quase preta, bordas ainda suaves."""
    H, W = par.forma
    L = par.imgs[0] @ _LUMA
    k = max(5, (min(H, W) // 25) | 1)
    fundo = cv2.GaussianBlur(cv2.dilate(L, np.ones((k, k), np.uint8)), (0, 0), k / 2)
    fundo = np.maximum(fundo, 0.6 * float(np.percentile(L, 90)))  # áreas grandes e escuras não viram papel
    if p["modo"] == "pb":
        t = fundo * (1 - p["limiar"])

        def f(im):
            lum = im @ _LUMA
            pb = 1 / (1 + np.exp(np.clip((t - lum) / p["suavidade"], -30, 30)))
            return np.repeat((255 * (0.9 * pb + 0.1 * np.clip(lum / fundo, 0, 1)))[..., None], 3, 2)
    else:
        ganho = (248.0 / fundo)[..., None]

        def f(im):
            im = np.clip((im * ganho - p["preto"]) * (255.0 / (255.0 - p["preto"])), 0, 255)
            lum = (im @ _LUMA)[..., None]
            im = np.repeat(lum, 3, 2) if p["modo"] == "cinza" else lum + (im - lum) * (1 + p["saturacao"])
            return im + p["realce"] * (im - cv2.GaussianBlur(im, (0, 0), 1.5))
    par.mapear(f)


def _app_scanner(par: _Par, p: dict, rng: np.random.Generator) -> None:
    """Foto de celular que o app recorta e retifica pelos cantos que detectou (erro residual de
    0,3–1,5% da página: sobra uma leve perspectiva/rotação), realça (cor, cinza ou pb) e grava em
    JPEG. A perspectiva da foto e a retificação viram UM warp (cada reamostragem a mais apagava
    traços finos da máscara, e a nitidez do app os trazia de volta na imagem); lente, luz e sensor
    entram já no quadro retificado."""
    H, W = par.forma
    cantos = _curvar(par, p["curvatura"]) if p.get("curvatura") else np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    Wc, Hc = max(16, round(W * p["escala"])), max(16, round(H * p["escala"]))
    M = _quadro(W, H, Wc, Hc, p["margem"], p["cantos"])
    Wo, Ho = max(MENOR_LADO, round(Wc * (1 - 2 * p["margem"]))), max(MENOR_LADO, round(Hc * (1 - 2 * p["margem"])))
    detectados = cv2.perspectiveTransform(cantos.reshape(1, 4, 2), M)[0] + \
        np.float32(p["dewarp"]).reshape(4, 2) * np.float32([Wo, Ho])
    retificar = cv2.getPerspectiveTransform(detectados, np.float32([[0, 0], [Wo, 0], [Wo, Ho], [0, Ho]]))
    _foto(par, p, rng, retificar @ M, (Wo, Ho))
    _realce(par, p)
    if p.get("jpeg"):
        par.jpeg(p["jpeg"], p.get("tabela"))


def _tela(par: _Par, p: dict, rng: np.random.Generator) -> None:
    """O documento aparece numa tela (resolução menor, grade de subpixels RGB) e é fotografado:
    o reamostrar da câmera sobre a grade faz o moiré."""
    H, W = par.forma
    par.redimensionar(max(16, round(W * p["escala_tela"])), max(16, round(H * p["escala_tela"])))
    H, W = par.forma
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    a = math.radians(p["angulo_grade"])
    u = x * math.cos(a) + y * math.sin(a)
    T = p["periodo"]
    grade = np.stack([1 - p["forca"] * (0.5 + 0.5 * np.cos(2 * np.pi * (u - c * T / 3) / T)) for c in range(3)], -1)
    if p.get("subpixel") == "pentile":  # OLED de celular: R e B alternam de linha em linha, só o G é denso
        v = -x * math.sin(a) + y * math.cos(a)
        alt = 0.5 + 0.5 * np.cos(2 * np.pi * v / (2 * T))
        grade[..., 0] *= 1 - 0.5 * p["forca"] * alt
        grade[..., 2] *= 1 - 0.5 * p["forca"] * (1 - alt)
    ganho = np.float32(p.get("balanco") or [1.0, 1.0, 1.0])  # ponto branco azulado da tela
    par.mapear(lambda im: im * grade * ganho)
    if p.get("banda"):  # faixa escura do refresh da tela pega pelo obturador rolante
        yb, larg, forca = p["banda"]
        faixa = np.clip(1 - np.abs((y / H - yb) / max(larg, 1e-3)), 0, 1)[..., None]
        par.mapear(lambda im: im * (1 - forca * faixa))
    Wc, Hc = max(16, round(W * p["escala"])), max(16, round(H * p["escala"]))
    par.geometria(_quadro(W, H, Wc, Hc, 0.02, p["cantos"]), (Wc, Hc), (15.0, 15.0, 18.0), perspectiva=True)
    if p.get("lente"):
        mapx, mapy, cromatica = _campo_lente(Hc, Wc, p["lente"])
        par.remap(mapx, mapy, (15.0, 15.0, 18.0), cromatica)
    par.mapear(_tonal(p["gama"]))
    par.mapear(lambda im: cv2.GaussianBlur(im, (0, 0), p["desfoque"]))
    _sensor(par, p["ruido_a"], p["ruido_b"], rng)
    if p.get("jpeg"):
        par.jpeg(p["jpeg"], p.get("tabela"))


def _recorte(par: _Par, p: dict, _rng) -> None:
    """Só um pedaço da página segue (``fx`` × ``fy`` dela, ancorado em ``ancora``). Depois da edição, a janela
    é empurrada (e, se preciso, alargada) para conter a edição inteira: um recorte que a cortasse viraria um par
    sem diferença. Antes da edição não há máscara: janela livre, e os campos de fora saem do documento."""
    H, W = par.forma
    w, h = max(MENOR_LADO, round(W * p["fx"])), max(MENOR_LADO, round(H * p["fy"]))
    ed = par.editada()
    if ed is not None and ed.any():
        ys, xs = np.nonzero(ed)
        folga = max(4, min(H, W) // 50)
        bx0, by0, bx1, by1 = int(xs.min()) - folga, int(ys.min()) - folga, int(xs.max()) + 1 + folga, int(ys.max()) + 1 + folga
        w, h = min(W, max(w, bx1 - bx0)), min(H, max(h, by1 - by0))
    x0, y0 = round((W - w) * p["ancora"][0]), round((H - h) * p["ancora"][1])
    if ed is not None and ed.any():
        x0, y0 = min(max(x0, bx1 - w), bx0), min(max(y0, by1 - h), by0)
    x0, y0 = max(0, min(x0, W - w)), max(0, min(y0, H - h))
    par.janela(x0, y0, x0 + w, y0 + h)


def _transmissao(par: _Par, p: dict, _rng) -> None:
    if p.get("deslocamento"):
        par.recortar(*p["deslocamento"])
    H, W = par.forma
    lado = p.get("lado")
    if lado and max(H, W) > lado:  # o app reduz o lado maior; nunca amplia
        e = lado / max(H, W)
        par.redimensionar(max(MENOR_LADO, round(W * e)), max(MENOR_LADO, round(H * e)))
    if p.get("escala"):
        e = min(p["escala"], LADO_MAXIMO / max(H, W))
        par.redimensionar(max(16, round(W * e)), max(16, round(H * e)))
    if p.get("jpeg"):
        par.jpeg(p["jpeg"])
    elif p.get("webp"):
        par.webp(p["webp"])


def _salvar_editor(par: _Par, p: dict, _rng) -> None:
    """O "Salvar como" JPEG do programa em que a pessoa editou (rotulador.humano): as tabelas do Photoshop (presets
    do Pillow, os do "Salvar como" e do "Salvar para Web") ou o libjpeg na qualidade e subamostragem do programa."""
    if p.get("preset"):
        par._regravar("JPEG", quality=str(p["preset"]))
    else:
        par._regravar("JPEG", quality=int(p.get("qualidade") or 90), subsampling=int(p.get("subsampling", 2)))


def passo_salvar_editor(gravacao: dict) -> dict:
    """O passo do canal que regrava o PAR como o editor salvaria (fase "depois", antes de qualquer envio)."""
    return {"tipo": "salvar_editor", **gravacao, "jpeg": int(gravacao.get("qualidade") or 90), "fase": "depois"}


_PASSOS = {"impressao": _impressao, "scanner": _scanner, "celular": _celular, "app_scanner": _app_scanner,
           "tela": _tela, "captura": lambda *_: None, "email": lambda *_: None, "recorte": _recorte,
           "whatsapp_documento": lambda *_: None,
           "whatsapp": _transmissao, "whatsapp_web": _transmissao, "rede_social": _transmissao,
           "app_edicao": _transmissao, "webp": _transmissao, "salvar_editor": _salvar_editor}


def _passos(theta: dict, fase: str | None) -> list[dict]:
    """Os passos da ``fase`` (None: todos). Passo sem a chave conta como "depois"."""
    return [p for p in theta["passos"] if fase is None or p.get("fase", "depois") == fase]


def aplicar(imgs, binarias, rotulos, theta: dict, fase: str | None = None, pontos=None):
    """Passa o par pelo canal ``theta``. ``imgs``: RGB uint8 do mesmo tamanho; ``binarias``: máscaras
    bool (reamostradas com cobertura >= LIMIAR); ``rotulos``: mapas inteiros (classe, nº da edição),
    levados como uma cobertura por valor (fica o de maior cobertura). Devolve (imgs uint8, binárias
    bool, rótulos). ``fase``: None aplica todos os passos; "antes"/"depois", só os dessa fase.
    ``pontos``: lista de (x, y) nas coordenadas da entrada; quando dada, a saída ganha um 4º item com
    os pontos levados pela mesma geometria. Cada passo tem o seu gerador (semente, índice): os
    campos de um passo são os mesmos com as fases juntas ou em chamadas separadas."""
    par = _Par(imgs, binarias, rotulos, pontos)
    for i, p in enumerate(theta["passos"]):
        if fase is None or p.get("fase", "depois") == fase:
            _PASSOS[p["tipo"]](par, p, np.random.default_rng([theta["semente"], i]))
    saida = [np.clip(i + 0.5, 0, 255).astype(np.uint8) for i in par.imgs]
    bins, rots = [b >= LIMIAR for b in par.bins], [r.montar(par.forma) for r in par.rots]
    if pontos is None:
        return saida, bins, rots
    return saida, bins, rots, [(float(x), float(y)) for x, y in par.pontos]


def captura_fisica(theta: dict | None, fase: str | None = None) -> bool:
    """O par passa por papel ou tela (impressão, scanner, celular, app, tela)? Marcas benignas só nesse
    caso. ``fase``: só os passos dessa fase (None: todos)."""
    return bool(theta) and any(p["tipo"] in FISICAS for p in _passos(theta, fase))


def raio(theta: dict | None, fase: str | None = None) -> int:
    """Até onde (px, na imagem de saída) a diferença da edição pode se espalhar pelo canal: cada
    passagem de JPEG pode contaminar mais um bloco 16x16 (croma 4:2:0 reamostrado na decodificação),
    e desfoque/reamostragem somam alguns pixels. Para conferir que nada muda LONGE da edição.
    ``fase``: só os passos dessa fase (None: todos)."""
    if not theta:
        return 0
    passos = _passos(theta, fase)
    jpegs = sum(1 for p in passos if p.get("jpeg") or p.get("webp"))
    borrao = max([3 * float(p.get("desfoque", 0)) for p in passos] + [0])
    # curvatura e lente deslocam imagem e máscara juntas (a amplitude em px depende do tamanho da
    # imagem, que o theta não conhece): sobra a pegada da interpolação, mais a cromática (≤ 1,5 px);
    # o app realça a nitidez (σ 1,5)
    suave = sum(4 for p in passos for k in ("curvatura", "lente") if p.get(k))
    suave += sum(6 for p in passos if p["tipo"] == "app_scanner")
    # sangria espalha a tinta nova alguns px; a fotocópia desfoca
    suave += sum(int(2 * round(float((p.get("desgaste") or {}).get("sangria") or 0))) + 4 for p in passos if p.get("desgaste"))
    return int(16 * (jpegs + 1) + borrao + suave + 6)


def tem_webp(theta: dict | None, fase: str | None = None) -> bool:
    """WEBP com perda (VP8) prevê cada bloco pelos vizinhos e escolhe a quantização por segmentos da
    imagem inteira: a edição espalha diferenças PEQUENAS longe dela. Não é atalho (as duas imagens
    passam pelo mesmo codificador), mas a conferência longe da edição passa a ser estatística."""
    return bool(theta) and any(p.get("webp") for p in _passos(theta, fase))


def conferir_par(a: np.ndarray, b: np.ndarray, regiao: np.ndarray, theta: dict | None, extra: int = 0,
                 webp: bool = False) -> str | None:
    """None se o par só difere perto da edição (ou, com WEBP, só um pouco longe dela); senão o motivo.
    ``webp``: a gravação final também foi WEBP com perda (mesma conferência estatística)."""
    if a.shape != b.shape:
        return f"tamanhos diferentes {a.shape} x {b.shape}"
    r = raio(theta) + extra  # extra: px a mais (ex.: a gravação final em JPEG)
    perto = cv2.dilate(regiao.astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8)) > 0 if r else regiao
    dif = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(2)[~perto]
    if not dif.size or not dif.any():
        return None
    if (webp or tem_webp(theta)) and dif.mean() < 2.5 and np.percentile(dif, 99.9) <= 48:
        return None
    return f"{int((dif > 0).sum())} px diferentes longe da edição (máx {int(dif.max())}, média {dif.mean():.2f})"


def ajustar_rotulos(rotulo: np.ndarray, regiao: np.ndarray) -> np.ndarray:
    """Mapa de rótulos coerente com a região reamostrada: fora dela 0; dentro, onde nenhuma
    cobertura chegou (borda de uma edição fina), o rótulo vizinho (dilatação)."""
    r = np.where(regiao, rotulo, 0).astype(rotulo.dtype)
    faltam = regiao & (r == 0)
    k = np.ones((3, 3), np.uint8)
    viz = rotulo
    for _ in range(4):
        if not faltam.any():
            break
        viz = cv2.dilate(viz.astype(np.float32), k).astype(rotulo.dtype)
        r[faltam] = viz[faltam]
        faltam = regiao & (r == 0)
    return r
