"""Quem falsifica e por quê: o gerador imita uma PESSOA fazendo a fraude, não um sorteio uniforme.

Por versão, sorteia-se um FALSIFICADOR (a ferramenta que ele usa e o capricho que tem) e um MOTIVO
(o que ele quer com a fraude). Disso saem, de uma vez só:
  - QUAIS campos mexer, de forma coerente (quem quer "outro titular" troca o nome e, às vezes, o
    CPF; quem quer a conta "recente" muda o vencimento e, às vezes, o mês de referência);
  - QUE valores pôr (data dentro dos últimos 90 dias; o mesmo nome nos dois lugares em que ele
    aparece; valor às vezes redondo);
  - COMO: as técnicas que aquela ferramenta permite, nos pesos com que gente de verdade as usa;
  - os HÁBITOS: a mesma fonte, o mesmo retângulo, a mesma colagem e o mesmo retoque em todas as
    edições da versão (falsificador repete os próprios parâmetros);
  - o CAMINHO do documento depois (foto da tela e WhatsApp para quem edita no celular; nada para
    quem edita o PDF).

O que a literatura mediu (docs/falsificadores.md tem as fontes):
  - Find it again! (ICDAR 2023; 19 voluntários, 163 recibos): ferramentas Paint 70, GIMP 65, Preview
    15, Paint 3D 10, KolourPaint 3; tipos: copiar-colar interno 353 (78%), imitação 36, apagar 36,
    pixel 33, colar de outro documento 10; alvos: total/pagamento 234, produto 95, data/hora 82.
  - FSTS (NeurIPS 2025; 67 pessoas no Photoshop): na substituição, Content-Aware Fill 61,7%, desfoque
    39,7%, ruído 34,9%; cada pessoa repete os próprios parâmetros ("recorrência individual").
  - RTM (Pattern Recognition 2024; 25 profissionais): alinham o texto, escolhem referência limpa, igualam
    cor, fonte e fundo.
  - Comprovantes de residência no Brasil (dynadok, 2026): parte-se de um documento real; a DATA é o
    campo mais alterado (regra dos 90 dias); nome e endereço em seguida; editor de PDF troca o texto
    mantendo fonte e logotipo; a captura de tela destrói os metadados; IA em 1 de 50.
  - Detectores comerciais (Resistant AI, Inscribe): fontes misturadas, negrito errático, seções
    desalinhadas, totais que não batem, valores redondos.
As proporções entre falsificadores (quantos editam no celular, no Paint, no PDF...) NÃO foram medidas
por ninguém: são estimativas, ajustáveis na receita (``falsificadores.pesos``).
"""

from __future__ import annotations

import datetime as dt
import random

from . import catalogo
from .ops import geradores

HOJE = dt.date.today  # função (os testes fixam a data)
JANELA_RECENTE = (6, 85)  # dias atrás: dentro da regra dos 90 dias, com folga (o dia útil adianta até 3 dias)

# técnicas que escrevem o VALOR INTEIRO pedido (p["texto"]); as outras escolhem o que mudar sozinhas
ACEITAM_TEXTO = frozenset({"campo.reescrever", "campo.amador", "ia.reescrever", "campo.mesma_fonte",
                           "campo.letras_do_documento"})

MOTIVOS: dict[str, dict] = {
    "recencia": {"rotulo": "Deixar a conta recente", "icone": "📅", "peso": 0.35,
                 "dica": "a conta é velha e o cadastro exige 90 dias: muda o vencimento e, às vezes, o mês de referência",
                 "campos": [("data", 1.0), ("mês de referência", 0.5)]},
    "identidade": {"rotulo": "Pôr outro titular", "icone": "👤", "peso": 0.25,
                   "dica": "a conta é de outra pessoa: troca o nome e, às vezes, esquece o CPF",
                   "campos": [("nome", 1.0), ("cpf/cnpj", 0.6)]},
    "endereco": {"rotulo": "Provar outro endereço", "icone": "🏠", "peso": 0.20,
                 "dica": "troca a rua e, às vezes, CEP, bairro e cidade (nem sempre tudo); o CEP combina com a cidade",
                 # a cidade vem antes do CEP: o CEP novo é sorteado na faixa da cidade (nova ou a do documento)
                 "campos": [("endereço", 1.0), ("cidade/uf", 0.3), ("bairro", 0.4), ("cep", 0.5)]},
    "valor": {"rotulo": "Mudar o valor", "icone": "💰", "peso": 0.12,
              "dica": "aumenta ou diminui o total (às vezes um valor redondo) e, raramente, o consumo; o código de barras fica velho",
              "campos": [("valor", 1.0), ("consumo/leitura", 0.3)]},
    "apagar": {"rotulo": "Sumir com uma informação", "icone": "🧽", "peso": 0.05,
               "dica": "apaga um número de instalação, um complemento ou um nome",
               "campos": [("identificador documento", 1.0), ("endereço", 0.5), ("nome", 0.4), ("cpf/cnpj", 0.4)],
               "jeito": "apagar"},
    "misto": {"rotulo": "Sem motivo definido", "icone": "🎲", "peso": 0.03,
              "dica": "mexe em qualquer campo permitido (o sorteio clássico)", "campos": None},
}

# técnicas e pesos: Find it again! para quem usa Paint/GIMP; FSTS e RTM para o Photoshop; dynadok e os
# detectores comerciais para o editor de PDF e o celular. "canal" é o caminho depois da edição.
# "captura": quem edita no celular ou no Paint parte de uma FOTO ou de um SCAN (a captura vem ANTES da edição e
# o retângulo fica liso sobre o papel fotografado); quem altera o papel ou edita o PDF captura DEPOIS.
# "apagar"/"rasterizador"/"vestigio": as peças que cada ferramenta usa (rotulador.ops.apagar, texto.renderizar).
FALSIFICADORES: dict[str, dict] = {
    "celular": {
        "rotulo": "No celular, com app de edição", "icone": "📱", "peso": 0.27,
        "dica": "retângulo ou pincelada, fonte do sistema, letra copiada com o fundo, rabisco, borrão; manda a foto da tela pelo WhatsApp",
        "tecnicas": {"campo.amador": 4, "campo.corretivo": 3, "caractere.copiar_glifo": 3, "campo.rabiscar": 1.5,
                     "campo.borrar": 1.5, "campo.ocultar": 1.5, "campo.copiar_interno": 1, "caractere.acrescentar_tracos": 1,
                     "caractere.apagar": 1, "bloco.colagem_externa": 0.3},
        "fontes": ["Roboto", "Segoe UI", "Arial", "Calibri"], "colagem": ["retangulo"], "forma": ["pincelada", "retangulo"],
        "tinta": ["marcador", "pincel_app", "digital"], "objeto": ["dedo", "adesivo", "postit"], "modo": ["desfoque", "mosaico"],
        "apagar": ["telea", "telea", "chapado", "borrao"], "rasterizador": ["freetype"], "vestigio": 0.35,
        "acabamento": {"ruido": "nunca", "disfarce": "nunca"}, "capricho": (0.9, 1.6), "captura": "antes",
        "canal": {"whatsapp": 0.45, "tela": 0.30, "celular": 0.15, "nenhum": 0.10},
    },
    "paint": {
        "rotulo": "Paint ou editor simples no computador", "icone": "🖌️", "peso": 0.13,
        "dica": "copia e cola dígitos do próprio documento (78% no Find it again!), retângulo branco, traço a mais",
        "tecnicas": {"caractere.copiar_glifo": 5, "caractere.transpor": 2, "campo.copiar_interno": 2, "campo.amador": 1,
                     "campo.corretivo": 0.6, "caractere.apagar": 0.6, "caractere.acrescentar_tracos": 0.6,
                     "campo.rabiscar": 0.5, "caractere.raspar_tracos": 0.4, "campo.borrar": 0.3, "campo.colagem_externa": 0.2,
                     "bloco.colagem_externa": 0.3},
        "fontes": ["Arial", "Calibri", "Segoe UI", "Times New Roman"], "colagem": ["retangulo", "tinta"], "forma": ["retangulo"],
        "tinta": ["pincel_app", "digital"], "modo": ["mosaico", "desfoque"],
        "apagar": ["chapado", "chapado", "carimbo", "telea"], "rasterizador": ["gdi", "gdi", "cleartype", "aliasado"],
        "vestigio": 0.30,
        "acabamento": {"ruido": "nunca", "disfarce": "nunca"}, "capricho": (0.7, 1.3), "captura": "antes",
        "canal": {"nenhum": 0.40, "scanner": 0.20, "impressao": 0.20, "tela": 0.20},
    },
    "papel": {
        "rotulo": "No papel: corretivo, caneta, recorte, e nova foto", "icone": "📄", "peso": 0.08,
        "dica": "altera o papel impresso (corretivo líquido, caneta, recorte colado, post-it) e fotografa ou escaneia de novo",
        "tecnicas": {"campo.corretivo": 3, "caractere.acrescentar_tracos": 2, "campo.rabiscar": 2, "campo.ocultar": 1.5,
                     "caractere.copiar_glifo": 1, "campo.copiar_interno": 1, "campo.colagem_externa": 0.5},
        "colagem": ["papel"], "forma": ["liquido"], "tinta": ["caneta"], "objeto": ["dedo", "postit", "fita"],
        "apagar": ["patchmatch"],
        "acabamento": {"ruido": "nunca", "disfarce": "nunca"}, "capricho": (0.8, 1.4), "captura": "depois",
        "canal": {"celular": 0.6, "scanner": 0.3, "impressao": 0.1}, "canal_obrigatorio": True,
    },
    "editor_pdf": {
        "rotulo": "Editor de PDF ou de texto", "icone": "📝", "peso": 0.27,
        "dica": "troca o texto mantendo a fonte e o logotipo (na camada de texto do PDF, quando há); o texto novo sai nítido, sem o grão do papel",
        "tecnicas": {"campo.reescrever": 5, "campo.reescrever_pdf": 5, "trecho.trocar": 3, "caractere.redesenhar": 1,
                     "campo.acrescentar": 1, "campo.remover": 0.5,
                     "pagina.recriar": 1.5},  # refaz a conta inteira no editor e troca os campos por cima
        "alinhamento": ["esquerda", "direita"], "nitido": True, "fonte_editor": ["imitada", "substituta"],
        "fonte_pdf": ["embutida", "substituta"], "apagar": ["patchmatch", "chapado"], "rasterizador": ["freetype"],
        "pericia": ["cru", "cru", "profissional"],  # metade deixa o texto nítido (o tell); metade esconde
        "acabamento": {"ruido": "nunca", "disfarce": "nunca"}, "capricho": (0.3, 0.7), "captura": "depois",
        "coerente": 0.3,  # às vezes refaz o código de barras para bater com o total novo
        "canal": {"nenhum": 0.50, "tela": 0.25, "whatsapp": 0.15, "impressao": 0.10},
    },
    "photoshop": {
        "rotulo": "Photoshop ou GIMP, com cuidado", "icone": "🎯", "peso": 0.22,
        "dica": "preenchimento inteligente, fonte imitada, grão igualado, letra colada com alfa; desfoca e aguça depois",
        "tecnicas": {"campo.reescrever": 3, "caractere.copiar_glifo": 3, "campo.remover": 2, "campo.copiar_interno": 2,
                     "caractere.redesenhar": 2, "trecho.trocar": 2, "campo.colagem_externa": 1, "caractere.transpor": 1,
                     "caractere.inserir": 1, "caractere.apagar": 1, "caractere.glifo_externo": 0.5, "trecho.jitter": 0.5,
                     "codigo.regenerar": 0.5, "codigo.qr": 0.3, "campo.borrar": 0.3, "bloco.colagem_externa": 0.5,
                     "pagina.recriar": 0.5},
        "colagem": ["tinta", "poisson"], "alinhamento": ["esquerda", "direita", "centro"], "forma": ["retangulo", "pincelada"],
        "modo": ["mosaico", "desfoque"], "tinta": ["digital"], "pericia": ["profissional", "forense", "forense"],
        "fonte_editor": ["imitada"],  # quem usa Photoshop imita a fonte; a "substituta" é coisa de editor de PDF
        "apagar": ["patchmatch", "patchmatch", "carimbo", "ns"], "rasterizador": ["freetype"], "vestigio": 0.05,
        "grao": ["espectral", "espectral", "branco"],  # o Add Noise (branco) do Photoshop também existe (FSTS: 35%)
        "acabamento": {"ruido": "sempre", "disfarce": "auto"},
        # FSTS, tipo "substituição": desfoque 39,7%; aguçar/curvas/cor como a média da população
        "disfarce": {"desfocar": 0.40, "nitidez": 0.13, "curvas": 0.12, "cor": 0.10}, "capricho": (0.3, 0.8),
        "coerente": 0.5, "captura": "antes",
        "canal": {"misturado": 1.0},
    },
    "ia": {
        "rotulo": "IA generativa", "icone": "🤖", "peso": 0.03,
        "dica": "um modelo de difusão reescreve a região (1 em 50 fraudes, e crescendo)",
        "tecnicas": {"ia.reescrever": 3, "ia.remover": 1, "ia.caractere": 1}, "precisa_ia": True, "pericia": ["profissional"],
        "grao": ["espectral"], "fonte_editor": ["imitada"],
        "acabamento": {"ruido": "auto", "disfarce": "nunca"}, "capricho": (0.5, 1.0), "captura": "antes",
        "canal": {"misturado": 0.7, "nenhum": 0.3},
    },
}
for _f in FALSIFICADORES.values():
    for _tid in _f["tecnicas"]:
        assert _tid in catalogo.TECNICAS and catalogo.TECNICAS[_tid].automatica, f"técnica fora do catálogo: {_tid}"


def normalizar(cfg) -> dict | None:
    """``{"prob": fração das versões, "pesos": {falsificador: peso}, "motivos": {motivo: peso}}`` ou None
    (desligado). Sem ``pesos`` (ou ``motivos``), valem os padrões; com a lista, quem não está nela fica
    de fora. ``prob`` 0 = desligado."""
    if cfg is None or cfg is False:
        return None
    if cfg is True:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ValueError("falsificadores: use um objeto {prob, pesos, motivos}")
    prob = max(0.0, min(1.0, float(cfg.get("prob", 1.0))))
    if prob <= 0:
        return None
    def ler(tabela, pedidos, rotulo):
        if not pedidos:
            return {k: v["peso"] for k, v in tabela.items()}
        if not isinstance(pedidos, dict):
            raise ValueError(f"falsificadores: {rotulo} deve ser um objeto {{nome: peso}}")
        out = {k: 0.0 for k in tabela}
        for k, p in pedidos.items():
            if k not in tabela:
                raise ValueError(f"{rotulo[:-1]} desconhecido: {k}")
            out[k] = max(0.0, float(p or 0))
        return out
    pesos = ler(FALSIFICADORES, cfg.get("pesos"), "pesos")
    motivos = ler(MOTIVOS, cfg.get("motivos"), "motivos")
    if not any(pesos.values()):
        raise ValueError("falsificadores: ligue pelo menos um")
    if not any(motivos.values()):
        raise ValueError("falsificadores: ligue pelo menos um motivo")
    return {"prob": prob, "pesos": pesos, "motivos": motivos}


class Falsificador:
    """Uma pessoa falsificando UMA versão: ferramenta, motivo e hábitos fixos."""

    def __init__(self, nome: str, motivo: str, rng: random.Random):
        from .ops import texto as mod_texto

        self.nome, self.motivo, self.rng = nome, motivo, rng
        f = FALSIFICADORES[nome]
        self.tecnicas: dict[str, float] = dict(f["tecnicas"])
        self.acabamento = dict(f["acabamento"])
        instaladas = mod_texto.listar_fontes()
        fontes = [x for x in f.get("fontes", []) if x in instaladas]
        lo, hi = f["capricho"]
        self.habitos: dict = {"capricho": round(rng.uniform(lo, hi), 2)}
        if fontes:
            self.habitos["fonte"] = rng.choice(fontes)
        for chave in ("colagem", "alinhamento", "forma", "tinta", "objeto", "modo", "fonte_editor", "pericia", "apagar",
                      "rasterizador", "grao", "fonte_pdf"):  # a mesma escolha a versão inteira
            if f.get(chave):
                self.habitos[chave] = rng.choice(f[chave])
        if f.get("nitido"):
            self.habitos["nitido"] = True
        self.habitos["fundo"] = rng.choice(["branco", "papel"])
        self.habitos["preto"] = rng.random() < 0.5
        # apagado malfeito (sobra um fantasma do texto antigo) e código de barras refeito para bater com o total
        self.habitos["vestigio"] = "leve" if rng.random() < float(f.get("vestigio", 0)) else "nenhum"
        self.habitos["coerente"] = rng.random() < float(f.get("coerente", 0))
        self.captura = f.get("captura", "auto")  # a captura (foto/scan) vem antes ou depois da edição
        if f.get("disfarce"):  # recorrência individual (FSTS): o mesmo retoque, com os mesmos parâmetros, sempre
            ops = [op for op, p in f["disfarce"].items() if rng.random() < p]
            self.habitos["disfarce"] = {"ops": ops, "sigma": round(rng.uniform(0.5, 1.2), 2),
                                        "nitidez": round(rng.uniform(0.3, 0.8), 2),
                                        "gama": round(rng.uniform(0.85, 1.15), 3), "contraste": round(rng.uniform(0.92, 1.08), 3),
                                        "cor": [round(rng.uniform(0.95, 1.05), 3) for _ in range(3)]}
        canais = f["canal"]
        self.canal_perfil = rng.choices(list(canais), weights=list(canais.values()))[0]
        self.habitos["arredondar"] = rng.random() < 0.3  # valor redondo (Resistant AI)

    # ------------------------------------------------------------------ como
    def pesos_tecnicas(self, permitidas) -> dict[str, float]:
        """Pesos desta pessoa só nas técnicas que a receita permite ({} = ela não faz nada do que foi pedido)."""
        return {t: p for t, p in self.tecnicas.items() if t in permitidas}

    def params(self, tid: str) -> dict:
        """Opções e hábitos a juntar nos parâmetros da técnica."""
        t = catalogo.TECNICAS[tid]
        opcoes = {}
        for nome, spec in t.opcoes.items():
            v = self.habitos.get(nome)
            if v is not None and any(v == x for x, _ in spec["valores"]):  # só se a técnica aceita esse valor
                opcoes[nome] = v
        return {"opcoes": opcoes, "habitos": dict(self.habitos)}

    def acabamento_de(self, receita_acabamento: dict) -> dict:
        """O acabamento da ferramenta (grão, disfarce) por cima do da receita; o JPEG local fica da receita."""
        return {**receita_acabamento, **self.acabamento}

    def canal(self, receita_canal: dict | None) -> dict | None:
        """O caminho do documento desta pessoa (só se a receita usa canal; quem altera o papel sempre fotografa).
        ``captura``: a foto/scan vem antes ou depois da edição (a receita pode forçar; senão é o hábito da pessoa)."""
        if self.canal_perfil == "nenhum" or (receita_canal is None and not FALSIFICADORES[self.nome].get("canal_obrigatorio")):
            return None
        captura = (receita_canal or {}).get("captura", "auto")
        return {"perfil": self.canal_perfil, "prob": 1.0, "captura": self.captura if captura == "auto" else captura}

    # ------------------------------------------------------------------ o quê e com que valores
    def planejar(self, campos: list[dict], pares, rng: random.Random, maximo: int, split: str | None = None,
                 minimo: int = 1) -> list:
        """[(técnica, campo, texto|None)] coerentes com o motivo, só entre os ``pares`` (técnica, campo)
        que a receita permite. Se o motivo não cabe neste documento, tenta os outros. Se a receita pede
        mais edições (``minimo``) do que o motivo precisa, a pessoa tem um segundo motivo (conta recente E
        outro titular é a fraude mais comum de comprovante). [] = sorteio clássico."""
        por_campo: dict[int, list] = {}
        for t, f in pares:
            por_campo.setdefault(id(f), []).append(t)
        ordem = [self.motivo] + [m for m in _ordenar(MOTIVOS, rng, excluir=self.motivo)]
        plano, usados, self.motivos = [], set(), []
        self._valores = _Valores(rng, split, self.habitos, cidade=_cidade_do_documento(campos))
        for motivo in ordem:
            if MOTIVOS[motivo]["campos"] is None or len(plano) >= max(minimo, 1):
                if plano:
                    break
                continue
            livres = {k: v for k, v in por_campo.items() if k not in usados}
            parte = self._planejar_motivo(motivo, campos, livres, rng, maximo - len(plano), split)
            if parte:
                plano += parte
                usados |= {id(f) for _, f, _ in parte}
                self.motivos.append(motivo)
        if not plano:
            self.motivo = "misto"
            return []
        self.motivo = self.motivos[0]
        return plano[:maximo]

    def _planejar_motivo(self, motivo, campos, por_campo, rng, maximo, split) -> list:
        m = MOTIVOS[motivo]
        so_jeito = set(next(j for j in catalogo.JEITOS if j["id"] == m["jeito"])["tecnicas"]) if m.get("jeito") else None
        escolhidos: list[tuple[dict, str | None]] = []
        disponiveis: list[tuple[dict, str]] = []
        valores = getattr(self, "_valores", None) or _Valores(rng, split, self.habitos, cidade=_cidade_do_documento(campos))
        for cat, prob in m["campos"]:
            do_tipo = [f for f in campos if f.get("categoria") == cat and por_campo.get(id(f))]
            rng.shuffle(do_tipo)
            disponiveis += [(f, cat) for f in do_tipo]
            for i, f in enumerate(do_tipo):
                # o primeiro campo do tipo entra com a probabilidade do motivo; os repetidos (duas datas, o
                # titular em dois lugares) com metade: quem frauda esquece o segundo lugar
                if rng.random() >= (prob if i == 0 else prob * 0.5):
                    continue
                texto = None if so_jeito else valores.para(motivo, cat, f.get("texto", ""), f.get("rotulo", ""))
                escolhidos.append((f, texto))
        if not escolhidos and disponiveis:  # o motivo cabe no documento: a pessoa mexe em pelo menos um campo
            f, cat = disponiveis[0]
            escolhidos.append((f, None if so_jeito else valores.para(motivo, cat, f.get("texto", ""), f.get("rotulo", ""))))
        plano = []
        for f, texto in escolhidos[:maximo]:
            tecs = por_campo[id(f)]
            if so_jeito:
                tecs = [t for t in tecs if t.id in so_jeito]
            pesos = self.pesos_tecnicas({t.id for t in tecs})
            cands = [t for t in tecs if t.id in pesos] or tecs  # a pessoa usa o que a ferramenta dela tem; senão, qualquer uma
            if texto is not None:
                com_texto = [t for t in cands if t.id in ACEITAM_TEXTO]
                if com_texto:
                    cands = com_texto
                else:
                    texto = None  # a técnica escolhe o que mudar (a data continua possível; o valor não é o pedido)
            if not cands:
                continue
            t = rng.choices(cands, weights=[pesos.get(x.id, 1.0) for x in cands])[0]
            plano.append((t, f, texto))
        return plano

    def descrever(self) -> dict:
        f, m = FALSIFICADORES[self.nome], MOTIVOS[self.motivo]
        return {"nome": self.nome, "rotulo": f["rotulo"], "motivo": self.motivo, "motivo_rotulo": m["rotulo"],
                "motivos": list(getattr(self, "motivos", []) or [self.motivo]),
                "canal_perfil": self.canal_perfil, "captura": self.captura, "habitos": dict(self.habitos)}

    def frase(self) -> str:
        return f"{FALSIFICADORES[self.nome]['rotulo'].lower()} · {MOTIVOS[self.motivo]['rotulo'].lower()}"


def _hoje() -> dt.date:
    """A "data de envio" da versão (definida pelo gerador a partir das datas do documento, via
    ``geradores.usando(hoje=...)``), ou o HOJE deste módulo (os testes o fixam)."""
    var = getattr(geradores, "_hoje_atual", None)
    definido = var.get() if var is not None else None
    return definido or HOJE()


def _cidade_do_documento(campos: list[dict]):
    """(cidade, UF) impressa no documento, para o CEP falso cair na faixa certa; None se não há."""
    for f in campos:
        if f.get("categoria") == "cidade/uf":
            c = geradores.cidade_do_texto(str(f.get("texto", "")))
            if c:
                return c
    return None


class _Valores:
    """Valores novos coerentes com o motivo (a mesma pessoa, a mesma data, o CEP na faixa da cidade, o consumo
    na proporção do valor) e no formato do antigo."""

    def __init__(self, rng: random.Random, split: str | None, habitos: dict, cidade=None):
        self.rng, self.split, self.habitos = rng, split, habitos
        self.pessoa: str | None = None
        self.data: dt.date | None = None
        self.datas: dict[str, dt.date] = {}  # por tipo de data (vencimento, emissão, leitura...), coerentes entre si
        self.fator: float | None = None  # valor novo / valor antigo (o consumo acompanha)
        self.cidade = cidade  # (nome, UF) atual: a do documento, ou a nova se o motivo trocou a cidade

    def _data_do_rotulo(self, rotulo: str) -> dt.date:
        """A data recente coerente com o rótulo, de UM ciclo de conta (``geradores.ciclo_de_datas``): vencimento em
        dia útil a partir da data sorteada, emissão, leitura, próxima leitura e pagamento nas distâncias de uma conta
        real. Com todas as datas iguais (o de antes), a regra 'leitura < emissão < vencimento' virava pista."""
        from .coerencia import tipo_da_data

        if not self.datas:
            self.datas = geradores.ciclo_de_datas(vencimento=geradores.proximo_dia_util(self.data))
        return self.datas.get(tipo_da_data(rotulo)) or self.datas["vencimento"]

    def para(self, motivo: str, cat: str, antigo: str, rotulo: str = "") -> str | None:
        with geradores.usando(self.rng, self.split):
            if motivo == "recencia":
                if self.data is None:
                    self.data = _hoje() - dt.timedelta(days=self.rng.randint(*JANELA_RECENTE))
                if cat == "data":
                    return geradores.formatar_data(antigo, self._data_do_rotulo(rotulo))
                if cat == "mês de referência":  # o mês da leitura (ou o anterior), do mesmo ciclo das datas
                    self._data_do_rotulo("")
                    return geradores.formatar_mes(antigo, self.datas["referencia"])
            if motivo == "identidade" and cat == "nome":
                if self.pessoa is None:
                    self.pessoa = geradores.gerar("nome")["texto"]
                return geradores.como_o_antigo(antigo, self.pessoa)
            if motivo == "valor" and cat in ("valor", "consumo/leitura"):
                if self.fator is None:
                    self.fator = self.rng.uniform(0.35, 2.8)
                novo = geradores.parecido(cat, antigo, fator=self.fator)
                if cat == "valor" and self.habitos.get("arredondar"):
                    novo = geradores.arredondar_valor(novo)
                return novo if novo and novo != antigo else None
            if motivo == "endereco" and cat == "cidade/uf":
                novo = geradores.parecido(cat, antigo)
                self.cidade = geradores.cidade_do_texto(novo) or self.cidade
                return novo if novo and novo != antigo else None
            if cat == "cep":
                novo = geradores.parecido("cep", antigo, cidade=self.cidade)
                return novo if novo and novo != antigo else None
            novo = geradores.parecido(cat, antigo)
            return novo if novo and novo != antigo else None


def _ordenar(d: dict, rng: random.Random, excluir: str | None = None) -> list[str]:
    """Chaves em ordem sorteada pelos pesos (sem reposição)."""
    itens = [(k, v["peso"]) for k, v in d.items() if k != excluir and v["peso"] > 0]
    out = []
    while itens:
        k = rng.choices([x for x, _ in itens], weights=[w for _, w in itens])[0]
        out.append(k)
        itens = [(x, w) for x, w in itens if x != k]
    return out


def sortear(rng: random.Random, cfg, tecnicas_permitidas, ia_online: bool = False) -> Falsificador | None:
    """O falsificador de UMA versão (ou None: a versão sai pelo sorteio clássico). Fica de fora quem não
    usa nenhuma técnica permitida pela receita e, com a IA desligada, quem só usa IA."""
    cfg = normalizar(cfg)
    if cfg is None or rng.random() >= cfg["prob"]:
        return None
    permitidas = set(tecnicas_permitidas)
    cands = {k: p for k, p in cfg["pesos"].items() if p > 0 and (ia_online or not FALSIFICADORES[k].get("precisa_ia"))
             and any(t in permitidas for t in FALSIFICADORES[k]["tecnicas"])}
    if not cands:
        return None
    nome = rng.choices(list(cands), weights=list(cands.values()))[0]
    motivos = {k: p for k, p in cfg["motivos"].items() if p > 0}
    motivo = rng.choices(list(motivos), weights=list(motivos.values()))[0]
    return Falsificador(nome, motivo, rng)


def para_json() -> dict:
    return {"falsificadores": [{"id": k, **{x: v[x] for x in ("rotulo", "icone", "dica", "peso")},
                                "tecnicas": list(v["tecnicas"]), "precisa_ia": bool(v.get("precisa_ia")),
                                "pericia": sorted(set(v.get("pericia", [])))}
                               for k, v in FALSIFICADORES.items()],
            "motivos": [{"id": k, **{x: v[x] for x in ("rotulo", "icone", "dica", "peso")},
                         "campos": [c for c, _ in (v["campos"] or [])]} for k, v in MOTIVOS.items()]}
