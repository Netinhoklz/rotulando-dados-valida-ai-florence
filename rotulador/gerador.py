"""Gerador automático de fraudes: uma RECEITA vira documentos editados e rotulados.

Receita (validada em ``Receita``):
  documentos   splits, ids escolhidos (ou todos), só pendentes;
  tecnicas     {id do catálogo: peso} — o COMO;
  categorias   {chave: peso} — o QUÊ (nome, valor, data...);
  tipos        (opcional, modo fácil) [{nome, categorias: [...], tecnicas: [...], peso, ativo}]: cada TIPO
               DE EDIÇÃO junta um quê e um como. Com tipos, só entram as combinações técnica × classe
               de algum tipo, e o peso de cada tipo se divide pelas combinações dele (tecnicas e
               categorias acima passam a ser derivadas dos tipos);
  opcoes       {id da técnica: {opção: valor ou "aleatorio"}} — detalhes do como;
  edicoes      [mín, máx] edições por imagem; variacoes por documento;
  distribuicao "equilibrada" (a combinação técnica x categoria menos usada até agora vai
               primeiro: cobre tudo por igual), "cobertura" (equilibrada, mas contando também o que JÁ
               está salvo no split: lotes sucessivos vão preenchendo as combinações que faltam) ou
               "pesos" (sorteio proporcional aos pesos);
  acabamento   {"ruido": auto|sempre|nunca, "disfarce": auto|sempre|nunca,
               "jpeg": probabilidade de recompressão local};
  canal        {"perfil": misturado|scanner|celular|tela|impressao|whatsapp|nenhum, "prob": fração
               das versões} — o caminho até quem verifica, igual no par (rotulador/canal.py);
  marcas       {"prob": fração das versões com captura física} — carimbo, anotação... (rotulador/marcas.py);
  falsificadores {"prob": fração das versões feitas "como uma pessoa faria", "pesos": {falsificador: peso},
               "motivos": {motivo: peso}} — por versão, uma pessoa (ferramenta, hábitos) e um motivo
               escolhem campos coerentes, valores e técnicas dentro do que a receita permite
               (rotulador/falsificador.py). Ausente = sorteio clássico, independente por edição;
  canal.captura "auto" | "antes" | "depois": a captura (foto, scan) vem ANTES da edição (quem edita no
               celular ou no Paint parte de uma foto: a base da versão é a captura, os campos são levados
               pela geometria e a edição fica lisa sobre o papel fotografado) ou depois (impressão, tela,
               transmissão). "auto" = o hábito do falsificador (celular, Paint, Photoshop e IA antes; papel
               e editor de PDF depois);
  acabamento.grao "auto" | "espectral" | "branco": o grão que entra na região editada (auto = 70% com o
               espectro do papel, 30% ruído branco, ou o hábito do falsificador);
  acabamento.requantizar "auto" | "nunca": documento JPEG → a região editada é recomprimida com as tabelas de
               quantização da própria fonte, na grade dela (sem isso, só a edição fica sem a marca do JPEG);
  dificuldade  {"variantes": 1..6, "prob": fração das edições, "detector": {"url": ...} | null}: cada edição é
               gerada ``variantes`` vezes (sementes diferentes) e fica a que o perito (rotulador/perito.py, ou um
               detector externo) menos acusa — mineração de exemplos difíceis;
  exato        em cada versão, TODOS os campos das classes escolhidas (e as outras ocorrências do mesmo dado no
               documento), em vez de sortear de ``edicoes[0]`` a ``edicoes[1]`` campos;
  subconjuntos (com exato) cada versão edita um subconjunto das classes (às vezes só o nome, às vezes só o
               endereço), sem repetir o subconjunto entre as versões do mesmo documento enquanto der;
  perfil       "profissional": a receita vira a de ``receita_profissional`` (com ``extras`` e ``categorias_base``;
               ``documentos``, ``variacoes`` e o resto continuam valendo): técnica por campo sorteada por granularidade
               só entre as profissionais, classes extras às vezes (``PERFIL_PROFISSIONAL``, ``GRUPOS_EXTRAS``);
  nome, formato, qualidade, operador, modelo_ia.

Cada imagem é uma Sessao temporária: as edições entram no histórico como numa edição
manual (uma camada por categoria) e a gravação é a mesma do botão Salvar, sempre no
split do documento. Doadores de colagem saem do mesmo split (rotulador.contexto).
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import random
import re
import shutil
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from . import campos as mod_campos
from . import canal, catalogo, coerencia, datasheet, exportar, falsificador as mod_falsificador, humano, identificar_fonte, marcas
from .contexto import Contexto
from .ops import auto, executar, geradores, ia
from .ops.tecnicas import _inicio_parte_final, _trechos, aplicavel
from .registro import SPLITS, Registro
from .selecao import mascara_tinta, vazia
from .sessao import ErroOperacao, Sessao

# peso padrão de cada categoria (fraudes de comprovante mexem mais em valor, data e titular)
PESOS_CATEGORIA = {"valor": 3.0, "data": 2.0, "nome": 2.5, "cpf/cnpj": 2.0, "endereço": 2.5, "cep": 1.5,
                   "cidade/uf": 1.0, "bairro": 1.0, "identificador documento": 1.0, "mês de referência": 1.0,
                   "consumo/leitura": 1.0, "linha digitável": 0.8, "codigo barras": 0.8, "qr code/pix": 0.6,
                   "emissor/logotipo": 0.4}
DISTRIBUICOES = ("equilibrada", "cobertura", "pesos")


def _sobrepoe(a, b, folga: int = 4) -> bool:
    return not (a[2] + folga <= b[0] or b[2] + folga <= a[0] or a[3] + folga <= b[1] or b[3] + folga <= a[1])


class Receita:
    def __init__(self, d: dict, ids_categoria: dict[str, int]):
        try:
            self._ler(d if isinstance(d, dict) else {}, ids_categoria)
        except (TypeError, ValueError, AttributeError, KeyError, IndexError) as e:
            if isinstance(e, ErroOperacao):
                raise
            raise ErroOperacao(f"receita inválida: {e}")

    def _ler(self, d: dict, ids_categoria: dict[str, int]) -> None:
        if d.get("perfil") == "profissional":  # a tela de um clique manda só o perfil: vira a receita profissional
            d = _expandir_perfil(d, ids_categoria)
        doc = d.get("documentos") or {}
        self.splits = [s for s in (doc.get("splits") or list(SPLITS)) if s in SPLITS]
        if not self.splits:
            raise ErroOperacao("escolha pelo menos um split")
        ids = doc.get("ids")
        self.ids = None if ids in (None, "todos") else {str(i) for i in ids}
        self.so_pendentes = bool(doc.get("so_pendentes", False))
        self._ler_tipos(d.get("tipos"), ids_categoria)
        if self.pares:
            self.tecnicas, self.categorias = Counter(), Counter()
            for (tid, k), peso in self.pares.items():
                self.tecnicas[tid] += peso
                self.categorias[k] += peso
            self.tecnicas, self.categorias = dict(self.tecnicas), dict(self.categorias)
        else:
            self._ler_pesos(d, ids_categoria)
        self.opcoes = {k: dict(v) for k, v in (d.get("opcoes") or {}).items() if k in self.tecnicas and isinstance(v, dict)}
        self._ler_resto(d)

    def _ler_tipos(self, tipos, ids_categoria: dict[str, int]) -> None:
        self.tipos: list[dict] = []
        self.pares: dict[tuple[str, str], float] = {}  # (técnica, categoria) -> peso
        if not tipos:
            return
        if not isinstance(tipos, list):
            raise ErroOperacao("tipos de edição: use uma lista")
        for i, tp in enumerate(tipos):
            if not isinstance(tp, dict):
                raise ErroOperacao("tipo de edição inválido")
            nome = str(tp.get("nome") or f"tipo {i + 1}")[:80]
            peso = min(100.0, float(tp.get("peso", 1) or 0))
            if not tp.get("ativo", True) or peso <= 0:
                continue
            tecs = [str(x) for x in (tp.get("tecnicas") or [])]
            cats = [str(x) for x in (tp.get("categorias") or [])]
            for tid in tecs:
                if tid not in catalogo.TECNICAS or not catalogo.TECNICAS[tid].automatica:
                    raise ErroOperacao(f"técnica desconhecida: {tid}")
            for k in cats:
                if k not in ids_categoria or k == "outro":
                    raise ErroOperacao(f"categoria inválida: {k}")
            if not tecs or not cats:
                raise ErroOperacao(f"o tipo '{nome}' precisa de pelo menos uma classe e um jeito de editar")
            validos = [(tid, k) for tid in dict.fromkeys(tecs) for k in dict.fromkeys(cats)
                       if catalogo.compativel(catalogo.TECNICAS[tid], k)]
            if not validos:
                raise ErroOperacao(f"o tipo '{nome}' não tem combinação possível: nenhum desses jeitos serve para "
                                   "essas classes (ex.: código novo só vale para código de barras e QR)")
            for par in validos:
                self.pares[par] = self.pares.get(par, 0.0) + peso / len(validos)
            self.tipos.append({"nome": nome, "peso": peso, "tecnicas": tecs, "categorias": cats})
        if not self.pares:
            raise ErroOperacao("ligue pelo menos um tipo de edição")

    def _ler_pesos(self, d: dict, ids_categoria: dict[str, int]) -> None:
        tec = d.get("tecnicas") or {}
        self.tecnicas: dict[str, float] = {}
        for tid, peso in tec.items():
            t = catalogo.TECNICAS.get(tid)
            if t is None or not t.automatica:
                raise ErroOperacao(f"técnica desconhecida: {tid}")
            peso = float(peso or 0)
            if peso > 0:
                self.tecnicas[tid] = min(peso, 100.0)
        if not self.tecnicas:
            raise ErroOperacao("escolha pelo menos uma técnica")
        cats = d.get("categorias")
        if cats is None:  # não informado: todas, com os pesos padrão ({} é "nenhuma" e dá erro abaixo)
            cats = {k: PESOS_CATEGORIA.get(k, 1.0) for k in ids_categoria if k != "outro"}
        self.categorias: dict[str, float] = {}
        for k, peso in cats.items():
            if k not in ids_categoria or k == "outro":
                raise ErroOperacao(f"categoria inválida: {k}")
            peso = float(peso or 0)
            if peso > 0:
                self.categorias[k] = min(peso, 100.0)
        if not self.categorias:
            raise ErroOperacao("escolha pelo menos uma categoria")

    def _ler_resto(self, d: dict) -> None:
        ed = d.get("edicoes") or [1, 3]
        if not isinstance(ed, (list, tuple)) or not ed:
            raise ErroOperacao("edições por imagem: use [mínimo, máximo]")
        self.minimo = max(1, min(10, int(ed[0])))
        self.maximo = max(self.minimo, min(10, int(ed[1] if len(ed) > 1 else ed[0])))
        self.variacoes = max(1, min(50, int(d.get("variacoes", 1))))
        self.distribuicao = d.get("distribuicao", "equilibrada")
        if self.distribuicao not in DISTRIBUICOES:
            raise ErroOperacao("distribuição inválida")
        ac = d.get("acabamento") or {}
        if not isinstance(ac, dict):
            raise ErroOperacao("acabamento inválido")
        self.acabamento = {"ruido": ac.get("ruido", "auto") if ac.get("ruido", "auto") in ("auto", "sempre", "nunca") else "auto",
                           "disfarce": ac.get("disfarce", "auto") if ac.get("disfarce", "auto") in ("auto", "sempre", "nunca")
                           else "auto",
                           "jpeg": max(0.0, min(1.0, float(ac.get("jpeg", 0) or 0))),
                           "pericia": ac.get("pericia") if ac.get("pericia") in
                           ("auto", "cru", "padrao", "profissional", "forense") else "auto",
                           "grao": ac.get("grao") if ac.get("grao") in ("auto", "espectral", "branco") else "auto",
                           "requantizar": "nunca" if ac.get("requantizar") == "nunca" else "auto"}
        dif = d.get("dificuldade") or {}
        if not isinstance(dif, dict):
            raise ErroOperacao("dificuldade inválida: use {variantes, prob, detector}")
        self.dificuldade = {"variantes": max(1, min(6, int(dif.get("variantes", 1) or 1))),
                            "prob": max(0.0, min(1.0, float(dif.get("prob", 1.0) if dif.get("prob") is not None else 1.0))),
                            "detector": dif.get("detector") if isinstance(dif.get("detector"), dict) else None}
        self.formato = str(d.get("formato", "png")).lower()
        if self.formato not in exportar.FORMATOS:
            raise ErroOperacao("formato inválido")
        self.qualidade = max(1, min(100, int(d.get("qualidade", 95))))
        self.operador = str(d.get("operador") or "gerador automático")[:80]
        self.modelo_ia = d.get("modelo_ia") or None
        try:  # canal de envio (impressão, scanner, celular, WhatsApp...) aplicado igual no par
            self.canal = canal.normalizar(d.get("canal"))
            self.marcas = marcas.normalizar(d.get("marcas"))  # carimbo, anotação... (negativos difíceis)
            self.falsificadores = mod_falsificador.normalizar(d.get("falsificadores"))
            # o operador humano (seleção, ferramenta, suavização, conta-gotas, retoques e o "Salvar como" do editor)
            self.humano = humano.normalizar(d.get("humano"))
        except (TypeError, ValueError, AttributeError) as e:
            raise ErroOperacao(str(e))
        self.nome = str(d.get("nome") or "receita")[:60]
        self.usa_ia = any(catalogo.TECNICAS[t].precisa_ia for t in self.tecnicas)
        # exato: em cada versão, TODOS os campos das classes escolhidas que o leitor achou (o mesmo valor novo onde
        # o texto antigo se repete), em vez de sortear de ``edicoes[0]`` a ``edicoes[1]`` campos
        self.exato = bool(d.get("exato"))
        # exato com subconjuntos: cada versão escolhe QUAIS das classes edita (sem a chave: todas, como antes)
        self.subconjuntos = bool(d.get("subconjuntos"))
        # perfil profissional: a técnica de cada campo pela mistura por granularidade; as classes base (nome e/ou
        # endereço) sempre, e as extras (o resto de ``categorias``) às vezes
        self.perfil = "profissional" if d.get("perfil") == "profissional" else None
        self.extras = bool(d.get("extras")) and self.perfil is not None
        self.categorias_base = [c for c in (d.get("categorias_base") or []) if c in self.categorias] or list(self.categorias)
        self.estilo = d.get("estilo") if d.get("estilo") in ESTILOS else None  # o estilo pronto da tela, se veio de um

    def resumo(self) -> dict:
        return {"nome": self.nome, "perfil": self.perfil, "extras": self.extras, "categorias_base": self.categorias_base,
                "exato": self.exato, "subconjuntos": self.subconjuntos, "tecnicas": self.tecnicas,
                "categorias": self.categorias, "tipos": self.tipos,
                "opcoes": self.opcoes,
                "edicoes": [self.minimo, self.maximo], "variacoes": self.variacoes,
                "distribuicao": self.distribuicao, "acabamento": self.acabamento, "canal": self.canal,
                "marcas": self.marcas, "falsificadores": self.falsificadores, "dificuldade": self.dificuldade,
                "humano": self.humano, "estilo": self.estilo}

    def falsificador(self, rng: random.Random, ia_online: bool = False):
        """A pessoa que faz ESTA versão (ou None: sorteio clássico)."""
        return mod_falsificador.sortear(rng, self.falsificadores, set(self.tecnicas), ia_online)


class Plano:
    """Escolhe (técnica, campo) para cada edição. O lote usa um plano por split (a contagem vale
    dentro do split): na distribuição equilibrada, a combinação menos usada vai primeiro."""

    def __init__(self, receita: Receita, rng: random.Random, inicial: Counter | None = None):
        """``inicial``: (técnica, categoria) → edições que o split JÁ tem (distribuição "cobertura")."""
        self.r, self.rng = receita, rng
        self.contagem: Counter = Counter(inicial or {})  # (técnica, categoria) -> edições feitas
        self.por_tecnica: Counter = Counter()
        for (tid, _), n in self.contagem.items():
            self.por_tecnica[tid] += n
        self.lock = threading.Lock()

    def candidatos(self, campos: list[dict], vetorial: bool = False, forma=None) -> list[tuple[catalogo.Tecnica, dict]]:
        """``vetorial``: o documento é um PDF com camada de texto (as técnicas de edição vetorial servem).
        ``forma`` (H, W): com ela, as técnicas de página inteira (``tipos`` com "pagina": o documento refeito do
        zero) entram com um pseudo-campo da página, categoria "outro"."""
        pares = []
        textos = {}  # categoria -> textos diferentes no documento (cópia interna precisa de outro campo)
        for f in campos:
            textos.setdefault(f.get("categoria"), set()).add(f.get("texto", "").strip())
        for tid in self.r.tecnicas:
            t = catalogo.TECNICAS[tid]
            if t.precisa_pdf and not vetorial:
                continue
            if "pagina" in t.tipos:
                if forma is not None and campos and not self.r.pares:
                    pares.append((t, {"id": "pagina", "caixa": [0, 0, int(forma[1]), int(forma[0])], "texto": "",
                                      "categoria": "outro", "tipo": "pagina"}))
                continue
            for f in campos:
                if f.get("categoria") not in self.r.categorias or not aplicavel(t, f):
                    continue
                if self.r.pares and (tid, f.get("categoria")) not in self.r.pares:
                    continue  # tipos de edição: só as combinações de algum tipo
                if tid == "campo.copiar_interno" and len(textos.get(f.get("categoria"), ())) < 2:
                    continue
                pares.append((t, f))
        return pares

    def escolher(self, pares, pesos_tecnica: dict | None = None):
        """``pesos_tecnica``: os pesos do falsificador da versão; só as técnicas dele entram (se alguma couber)."""
        if not pares:
            return None
        if pesos_tecnica:
            so_dele = [(t, f) for t, f in pares if t.id in pesos_tecnica]
            if so_dele:
                pares = so_dele
        r, rng = self.r, self.rng
        if r.pares:  # tipos de edição: o peso é de cada combinação técnica × classe
            if r.distribuicao == "pesos":
                chaves = sorted({(t.id, f["categoria"]) for t, f in pares})
                k = rng.choices(chaves, weights=[r.pares[c] for c in chaves])[0]
                return rng.choice([(t, f) for t, f in pares if (t.id, f["categoria"]) == k])
            with self.lock:
                return min(pares, key=lambda p: self.contagem[(p[0].id, p[1]["categoria"])]
                           / r.pares[(p[0].id, p[1]["categoria"])] + rng.random() * 1e-3)
        if r.distribuicao == "pesos":
            tecs = sorted({t.id for t, _ in pares})
            tid = rng.choices(tecs, weights=[r.tecnicas[x] for x in tecs])[0]
            do_t = [(t, f) for t, f in pares if t.id == tid]
            return rng.choices(do_t, weights=[r.categorias.get(f["categoria"], 1.0) for _, f in do_t])[0]
        with self.lock:  # equilibrada: menor uso relativo ao peso; empate sorteado
            def nota(par):
                t, f = par  # a página inteira ("outro") não tem peso de classe: conta 1
                return (self.contagem[(t.id, f["categoria"])] / r.categorias.get(f["categoria"], 1.0)
                        + self.por_tecnica[t.id] / r.tecnicas[t.id] + rng.random() * 1e-3)
            return min(pares, key=nota)

    def contar(self, tid: str, cat: str, n: int = 1) -> None:
        with self.lock:
            self.contagem[(tid, cat)] += n
            self.por_tecnica[tid] += n


def _ja_salvas(raiz_saida: Path, split: str) -> Counter:
    """(técnica, categoria) → edições já gravadas no split (do catalogo.csv): o ponto de partida da
    distribuição "cobertura", para o lote novo ir atrás das combinações que ainda faltam."""
    c: Counter = Counter()
    for linha in exportar.ler_catalogo(raiz_saida):
        if linha.get("split") == split and linha.get("tecnica") and linha.get("categoria"):
            c[(linha["tecnica"], linha["categoria"])] += 1
    return c


def _params(t, f, rng, r: Receita, pessoa, texto=None, humano_hab: dict | None = None) -> dict:
    p = {"tecnica": t.id, "campo": {k_: f[k_] for k_ in ("id", "caixa", "texto", "categoria", "tipo")},
         "semente": rng.randrange(2**31), "opcoes": r.opcoes.get(t.id, {}), "acabamento": r.acabamento,
         "modelo": r.modelo_ia}
    if pessoa is not None:
        extra = pessoa.params(t.id)
        p["opcoes"] = {**p["opcoes"], **extra["opcoes"]}  # o hábito vence a opção "varia" da receita
        p["habitos"] = extra["habitos"]
        p["acabamento"] = pessoa.acabamento_de(r.acabamento)
    if texto:
        p["texto"] = texto
    if humano_hab:  # o operador humano da versão (rotulador.humano), junto dos hábitos do falsificador, se houver
        p["habitos"] = {**(p.get("habitos") or {}), "humano": humano_hab}
    return p


def _mais_dificil(sessao: Sessao, ctx, p: dict, n: int, rng: random.Random, dif: dict, editor):
    """Mineração de exemplos difíceis: gera a edição ``n`` vezes (sementes diferentes) e fica com a que o
    perito (ou o detector externo da receita) menos acusa. A semente escolhida volta para ``p``, então os
    parâmetros gravados reproduzem a variante aplicada."""
    from . import perito

    melhor, erro = None, None
    for i in range(n):
        pi = {**p, "semente": p["semente"] if i == 0 else rng.randrange(2**31)}
        try:
            res = executar("tecnica", sessao.atual, pi, vazia(sessao.H, sessao.W), base=sessao.base, documento=ctx,
                           chave_cache=ctx.doc, editor=editor)
        except ErroOperacao as e:
            erro = e
            continue
        m = 2 * max(16, res.y1 - res.y0)
        Y0, Y1, X0, X1 = max(0, res.y0 - m), min(sessao.H, res.y1 + m), max(0, res.x0 - m), min(sessao.W, res.x1 + m)
        ed = sessao.atual[Y0:Y1, X0:X1].copy()
        sub = ed[res.y0 - Y0:res.y1 - Y0, res.x0 - X0:res.x1 - X0]
        sub[:] = np.where(res.pegada[..., None], res.recorte, sub)
        reg = np.zeros(ed.shape[:2], bool)
        reg[res.y0 - Y0:res.y1 - Y0, res.x0 - X0:res.x1 - X0] = res.pegada
        try:
            aval = perito.avaliar(sessao.base[Y0:Y1, X0:X1], ed, reg, dif.get("detector"))
            med = aval.get("medidas") or {}
            # sem nenhuma medida válida (região minúscula, papel vazio em volta; nota None ou 0) a nota não quer
            # dizer "invisível": conta como desconhecida, para não vencer só por falta de prova
            nota = float(aval["nota"]) if aval.get("nota") is not None and (aval.get("fonte") == "externo" or any(
                med.get(k) is not None for k in ("grao", "espectro", "nitidez", "tom", "blocos"))) else 0.5
        except Exception:  # noqa: BLE001 - o perito nunca derruba a edição
            nota = 0.5
        if melhor is None or nota < melhor[0]:
            melhor = (nota, res, pi["semente"])
    if melhor is None:
        raise erro or ErroOperacao("nenhuma variante coube")
    nota, res, semente = melhor
    p["semente"] = semente
    res.meta = {**res.meta, "perito": {"nota": round(nota, 3), "variantes": n}}
    return res


def _executar_edicao(sessao: Sessao, ctx, p: dict, rng: random.Random, r: Receita, editor):
    """Roda a técnica (com as variantes da dificuldade, se pedidas) e aplica na sessão: (res, entrada)."""
    dif = getattr(r, "dificuldade", None) or {}
    n = int(dif.get("variantes", 1) or 1)
    with sessao.lock:
        if n > 1 and rng.random() < float(dif.get("prob", 1.0)):
            res = _mais_dificil(sessao, ctx, p, n, rng, dif, editor)
        else:
            res = executar("tecnica", sessao.atual, p, vazia(sessao.H, sessao.W), base=sessao.base,
                           documento=ctx, chave_cache=ctx.doc, editor=editor)
        # a classe "outro" (a página inteira refeita) pede uma descrição: vai a da técnica
        desc = str((res.meta or {}).get("descricao") or p.get("tecnica") or "") if p["campo"]["categoria"] == "outro" else ""
        ent = sessao.aplicar("tecnica", p["campo"]["categoria"], desc, p, res)
    return res, ent


def aplicar_receita(sessao: Sessao, ctx: Contexto, r: Receita, plano: Plano, rng: random.Random,
                    k: int | None = None, editor=None, ao_falhar=None, pessoa=None, info: dict | None = None,
                    ja_sorteados: list | None = None, humano_hab: dict | None = None) -> list[dict]:
    """Aplica de ``r.minimo`` a ``r.maximo`` edições (ou ``k``) na sessão. Devolve o que foi feito.

    ``pessoa`` (rotulador.falsificador.Falsificador): as primeiras edições seguem o plano dela (campos
    coerentes com o motivo, valores combinados, as técnicas da ferramenta dela, os hábitos dela); o que
    faltar para o mínimo sai do sorteio clássico, ainda com as técnicas dela. Uma pessoa "coerente" que
    mudou o total refaz também o código de barras para bater com ele.

    Modo exato: ``info`` recebe as classes da versão (``classes``) e as ocorrências que ficaram com o dado antigo
    (``ocorrencias_antigas``), para a anotação; ``ja_sorteados``: os subconjuntos de classes que as versões
    anteriores do MESMO documento já usaram (``r.subconjuntos``; a lista é atualizada). No perfil profissional o
    subconjunto sai das classes base, as extras da versão entram junto (``_extras``) e ``info`` recebe também o
    ``estilo`` (``_estilo``); versão em que nenhuma classe base coube é desfeita (nunca só as extras).

    ``humano_hab`` (``rotulador.humano.sortear``): os hábitos da pessoa que edita a versão, em todas as edições."""
    campos = [f for f in ctx.campos if f.get("categoria") in sessao.ids_categoria]
    if getattr(r, "exato", False):
        escolhidos = [f for f in campos if f.get("categoria") in r.categorias]
        presentes = {f["categoria"] for f in escolhidos}
        perfil = getattr(r, "perfil", None) == "profissional"
        todas = [c for c in (r.categorias_base if perfil else r.categorias) if c in presentes]
        classes = _subconjunto(todas, rng, ja_sorteados) if getattr(r, "subconjuntos", False) else todas
        resto = [c for c in todas if c not in classes]
        # nada do subconjunto coube: as outras classes, para não perder a versão (a anotação diz quais entraram)
        for classes in ([classes, resto] if resto else [classes]):
            extras = _extras(presentes, classes, rng) if perfil and r.extras else []
            escolha = [f for f in escolhidos if f["categoria"] in classes + extras]
            if perfil:
                escolha = _cpf_do_cliente(escolha, campos)
            antigas: list[dict] = []
            n0 = len(sessao.historico_) if perfil else 0
            feitos = editar_campos(sessao, ctx, escolha + _ocorrencias(ctx, escolha, sessao.base), r, plano, rng,
                                   editor=editor, ao_falhar=ao_falhar, antigas=antigas, humano_hab=humano_hab)
            if perfil and feitos and not any(x["categoria"] in classes for x in feitos):
                with sessao.lock:  # só as extras couberam: sem o nome/endereço não é a fraude pedida
                    while len(sessao.historico_) > n0:
                        sessao.desfazer()
                feitos = []
            if feitos:
                break
        if info is not None:
            info.update(classes=list(classes) + extras if perfil else list(classes), ocorrencias_antigas=antigas)
            if perfil:
                info["estilo"] = _estilo(classes, extras, feitos, campos)
        return feitos
    k = k or rng.randint(r.minimo, r.maximo)
    pares = plano.candidatos(campos, vetorial=bool(getattr(ctx, "vetorial", False)), forma=(sessao.H, sessao.W))
    # documento refeito do zero (técnica de página inteira): só como PRIMEIRA edição, com as trocas de campo
    # por cima; as linhas vêm do OCR do documento, e uma edição anterior seria re-tipografada com o texto antigo
    pagina = [x for x in pares if x[1].get("tipo") == "pagina"]
    pares = [x for x in pares if x[1].get("tipo") != "pagina"]
    todos = list(pares)
    fila = []
    pesos_pessoa = None
    if pessoa is not None:
        fila = pessoa.planejar(campos, pares, rng, r.maximo, getattr(ctx, "split", None), minimo=r.minimo)
        if fila:
            k = min(r.maximo, max(r.minimo, len(fila)))  # o motivo manda em quais campos; a receita, no mínimo
        pesos_pessoa = pessoa.pesos_tecnicas({t.id for t, _ in pares + pagina}) or None
    com_plano = bool(fila)
    feitos, usados = [], []
    if pagina and (pesos_pessoa is None or pagina[0][0].id in pesos_pessoa):
        t, f = plano.escolher(pares + pagina, pesos_pessoa)  # a página concorre com as outras técnicas no plano
        if f.get("tipo") == "pagina":
            try:
                res, ent = _executar_edicao(sessao, ctx, _params(t, f, rng, r, pessoa, None, humano_hab), rng, r, editor)
                plano.contar(t.id, f["categoria"])
                feitos.append(_feito(ent, res))  # a página não entra em ``usados``: os campos continuam editáveis
            except ErroOperacao as e:
                if ao_falhar:
                    ao_falhar(t.id, f, str(e))
    while len(feitos) < k and (pares or fila):
        texto = None
        if fila:
            t, f, texto = fila.pop(0)
            par = next((x for x in pares if x[0] is t and x[1] is f), None)
            if par is None:
                continue
            pares.remove(par)
        else:
            if com_plano and len(feitos) >= r.minimo:
                break  # o plano da pessoa foi cumprido no que coube: nada de edição aleatória fora do motivo
            par = plano.escolher(pares, pesos_pessoa)  # o plano inteiro falhou: sorteio clássico, ainda com as técnicas dela
            pares.remove(par)
            t, f = par
        if any(_sobrepoe(f["caixa"], u) for u in usados):
            continue
        p = _params(t, f, rng, r, pessoa, texto, humano_hab)
        try:
            res, ent = _executar_edicao(sessao, ctx, p, rng, r, editor)
        except ErroOperacao as e:
            if ao_falhar:
                ao_falhar(t.id, f, str(e))
            continue
        usados.append(f["caixa"])
        plano.contar(t.id, f["categoria"])
        feitos.append(_feito(ent, res))
        if t.id == "codigo.regenerar" and res.meta.get("linha_digitavel"):
            par_linha = _reescrever_linha(sessao, ctx, campos, res.meta["linha_digitavel"], usados, rng, r)
            if par_linha:
                feitos.append(par_linha)
            elif any(f2.get("categoria") == "linha digitável" for f2 in campos):
                # há linha digitável e ela não pôde ser refeita: código novo com linha velha se contradiz.
                # Desfaz até sair ESTA edição (não "a última": algo pode ter entrado depois dela)
                with sessao.lock:
                    for _ in range(6):
                        if not any(e is ent for e in sessao.historico_):
                            break
                        sessao.desfazer()
                feitos.pop()
                usados.pop()
                plano.contar(t.id, f["categoria"], -1)
                if ao_falhar:
                    ao_falhar(t.id, f, "a linha digitável não pôde ser refeita para o código novo")
    if pessoa is not None and pessoa.habitos.get("coerente"):
        _codigo_coerente(sessao, ctx, r, plano, rng, pessoa, campos, todos, feitos, usados, ao_falhar)
    return feitos


MAXIMO_EXATO = 30  # campos por versão no modo exato (uma conta tem o nome e o endereço em 2 ou 3 lugares)


def _chave_valor(categoria: str, texto: str) -> str:
    """O mesmo dado escrito de jeitos diferentes ("RUA 47 CJ..." no envelope, "Rua 47 Cj..." no boleto) dá a mesma
    chave; no CEP, só os 8 dígitos."""
    t = re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().upper())
    if categoria == "cep":
        m = re.search(r"\d{8}", t)
        return f"cep:{m.group(0)}" if m else f"cep:{t}"
    # a mesma data ou o mesmo valor com outro texto em volta ("em 25/01/2026" e "25/01/2026", "R$ 10,48" e "10,48"):
    # sem isso cada um recebia uma data nova diferente
    if categoria == "data" and geradores.ler_data(texto):
        return "data:" + geradores.ler_data(texto).strftime("%d%m%Y")
    if categoria == "valor" and re.search(r"\d", t):
        return "valor:" + re.sub(r"\D", "", t)
    return f"{categoria}:{t}"


# o que vai junto quando a classe entra (se foi marcada): a data trocada sem o mês de referência, ou a cidade sem o
# CEP e o bairro, deixava uma pista semântica que não é da fraude escolhida, só do sorteio (CEP ou bairro sozinhos,
# na mesma cidade, continuam podendo sair)
_JUNTO = {"data": ("mês de referência",), "mês de referência": ("data",), "cidade/uf": ("cep", "bairro")}


def _com_o_que_vai_junto(s, classes) -> frozenset:
    s = set(s)
    while True:
        mais = {d for c in s for d in _JUNTO.get(c, ()) if d in classes} - s
        if not mais:
            return frozenset(s)
        s |= mais


def _subconjunto(classes: list[str], rng: random.Random, vistos: list | None) -> list[str]:
    """As classes que ESTA versão edita (receita ``subconjuntos``): todas com probabilidade 0,5 (duas classes) ou
    0,4 (três ou mais); senão um subconjunto não vazio sorteado por igual (com nome e endereço: só o nome 25%, só o
    endereço 25%). Assim as máscaras variam entre as versões do mesmo documento e o modelo vê o nome trocado sem o
    endereço e vice-versa. O que vai junto (``_JUNTO``) entra junto. ``vistos`` (os subconjuntos das versões
    anteriores do documento): não repete enquanto houver subconjunto novo; quando todos saíram, recomeça o ciclo."""
    todos = list(dict.fromkeys(_com_o_que_vai_junto((c for j, c in enumerate(classes) if m >> j & 1), classes)
                               for m in range(1, 2 ** len(classes))))
    if len(todos) <= 1:
        return list(classes)
    cheio = frozenset(classes)
    p_todas = 0.5 if len(todos) == 3 else 0.4
    if vistos is not None and all(s in vistos for s in todos):
        vistos.clear()
    livres = [s for s in todos if vistos is None or s not in vistos]
    s = rng.choices(livres, weights=[p_todas if s == cheio else (1 - p_todas) / (len(todos) - 1) for s in livres])[0]
    if vistos is not None:
        vistos.append(s)
    return [c for c in classes if c in s]


# classes cujo valor se procura nas outras linhas do documento: o dado do cliente que se repete (bairro e cidade
# ficam de fora: "CENTRO" e "FORTALEZA" estão também no endereço do emissor)
_OCORRENCIAS = ("nome", "endereço", "cpf/cnpj", "cep")


def _alnum(texto: str) -> tuple[str, list[int]]:
    """Só letras e dígitos (sem acento, maiúsculos), como em ``_chave_valor``, com a posição de cada um no texto."""
    out, pos = [], []
    for i, ch in enumerate(texto):
        for b in unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode().upper():
            if b.isalnum():
                out.append(b)
                pos.append(i)
    return "".join(out), pos


def _ocorrencias(ctx, escolhidos: list[dict], img: np.ndarray) -> list[dict]:
    """Os OUTROS lugares do documento com o mesmo dado de um campo escolhido (o nome no envelope, no boleto e na
    linha "MARIA DA SILVA CPF 123..."): a linha inteira igual vira um campo da mesma classe; o dado dentro de uma
    linha maior vira um subcampo com a caixa só do trecho (``linha``: a caixa da linha toda). Sem isso o nome novo
    ia para dois lugares e o velho ficava no terceiro, uma contradição sem rótulo."""
    valores = {}  # dado (só letras e dígitos) -> classe
    for f in escolhidos:
        if f.get("tipo", "texto") == "texto" and f.get("categoria") in _OCORRENCIAS:
            nucleo = _chave_valor(f["categoria"], f.get("texto") or "").split(":", 1)[1]
            if len(nucleo) >= 6:
                valores.setdefault(nucleo, f["categoria"])
    if not valores:
        return []
    ids = {f.get("id") for f in escolhidos}
    novos = []
    for o in ctx.campos:  # linha inteira (sem categoria, ou com outra: "M RCFILHO FOTOS" debaixo do CNPJ)
        nucleo = _alnum(o.get("texto") or "")[0]
        if o.get("tipo", "texto") == "texto" and o.get("id") not in ids and nucleo in valores:
            novos.append({**o, "categoria": valores[nucleo], "ocorrencia": True})
    tomados = escolhidos + novos
    for i, l in enumerate(ctx.linhas):  # trecho de uma linha maior
        texto = l.get("texto") or ""
        norm, pos = _alnum(texto)
        for nucleo, cat in valores.items():
            ini = norm.find(nucleo)
            while ini >= 0 and norm != nucleo:
                a, b = pos[ini], pos[ini + len(nucleo) - 1] + 1
                # palavra inteira ("ANA" não está em "MARIANA"), e não o mesmo lugar de um campo já tomado
                if (a == 0 or not texto[a - 1].isalnum()) and (b == len(texto) or not texto[b].isalnum()):
                    caixa = mod_campos.caixa_do_trecho(img, l["caixa"], texto, a, b)
                    if caixa and not any(_sobrepoe(caixa, f["caixa"], 0) and _chave_valor(cat, f.get("texto") or "")
                                         == f"{cat}:{nucleo}" for f in tomados):
                        novos.append({"id": f"o{i}_{a}", "categoria": cat, "texto": texto[a:b], "caixa": caixa,
                                      "caixa_ocr": caixa, "rotulo": "", "conf": l.get("conf", 0), "tipo": "texto",
                                      "ocorrencia": True, "linha": list(l["caixa"])})
                ini = norm.find(nucleo, ini + 1)
    return novos


def _invade_linha(base: np.ndarray, res, f: dict) -> bool:
    """O trecho reescrito (subcampo de uma linha maior) cobriu a tinta do RESTO da linha? O valor novo, mais largo
    que o antigo, passaria por cima do "CPF 123..." que vem depois do nome."""
    lx0, ly0, lx1, ly1 = (int(v) for v in f["linha"])
    tinta = mascara_tinta(base[ly0:ly1, lx0:lx1]).astype(bool)
    h = max(1, f["caixa"][3] - f["caixa"][1])
    tinta[:, max(0, f["caixa"][0] - lx0 - 2):max(0, f["caixa"][2] - lx0 + 2)] = False
    ya, yb, xa, xb = max(ly0, res.y0), min(ly1, res.y1), max(lx0, res.x0), min(lx1, res.x1)
    if ya >= yb or xa >= xb:
        return False
    sob = res.pegada[ya - res.y0:yb - res.y0, xa - res.x0:xb - res.x0] & tinta[ya - ly0:yb - ly0, xa - lx0:xb - lx0]
    return int(sob.sum()) > 0.15 * h * h  # um encostinho da borda do apagado não conta; uma letra coberta, sim


def _mesmo_valor(categoria: str, antigo: str, novo_ref: str, antigo_ref: str) -> str:
    """O valor novo de um dado que já foi trocado em outro lugar, no formato DESTE campo."""
    if categoria == "cep":
        dig = re.sub(r"\D", "", re.search(r"\d{5}-?\d{3}", novo_ref).group(0)) if re.search(r"\d{5}-?\d{3}", novo_ref) else ""
        m = re.search(r"\d{5}-?\d{3}", antigo)
        if dig and m:
            alvo = dig if "-" not in m.group(0) else f"{dig[:5]}-{dig[5:]}"
            return antigo[:m.start()] + alvo + antigo[m.end():]
    # a mesma data (ou mês) escrita de outro jeito ("JAN/2026" e "Jan 2026"): a data nova no formato DESTE lugar
    if categoria == "data" and geradores.ler_data(novo_ref):
        return geradores.formatar_data(antigo, geradores.ler_data(novo_ref)) or novo_ref
    if categoria == "mês de referência" and geradores.ler_mes(novo_ref):
        return geradores.formatar_mes(antigo, dt.date(*geradores.ler_mes(novo_ref), 1)) or novo_ref
    # o mesmo valor com ou sem "R$": só o número muda, o resto do lugar fica
    m_a, m_n = re.search(r"\d[\d.]*,\d{2}", antigo), re.search(r"\d[\d.]*,\d{2}", novo_ref)
    if categoria == "valor" and m_a and m_n:
        return antigo[:m_a.start()] + m_n.group(0) + antigo[m_a.end():]
    return geradores.como_o_antigo(antigo, novo_ref)


# ============================================================================ perfil profissional
# Como um fraudador caprichoso mexe num campo, por GRANULARIDADE: (peso do grupo, {técnica: peso dentro do grupo}).
# Só técnicas profissionais: nada de retângulo, fonte padrão, corretivo, rabisco, borrão, dedo, traço acrescentado ou
# raspado, retoque sem troca, colagem de outro documento, cópia de outro campo (o CNPJ trocado por outro do mesmo
# documento) nem fonte livre (``campo.reescrever``: saiu fina numa linha em negrito). Técnica que não está no
# catálogo (a das letras do documento, enquanto não existir) ou que não serve para o campo sai do sorteio, e os
# grupos que sobram se renormalizam (no nome, sem dígito, não há grupo "caractere").
PERFIL_PROFISSIONAL = {
    # o campo inteiro reescrito (PDF com texto; IA ligada e pedida). As letras do próprio documento com o mesmo peso
    # da mesma fonte: depois de limpar as marcas soltas (ponto, tique, letra grudada, espaço), 0 marcas em 100
    # edições; quando faltam letras (~12%), a edição vai para a mesma fonte
    "campo": (0.55, {"campo.mesma_fonte": 1.0, "campo.letras_do_documento": 1.0,
                     "campo.reescrever_pdf": 1.0, "ia.reescrever": 1.0}),
    "trecho": (0.25, {"trecho.trocar": 1.0}),  # só o número da casa, o sobrenome, o dia, os centavos
    "caractere": (0.15, {"caractere.copiar_glifo": 1.0, "caractere.redesenhar": 1.0, "caractere.inserir": 0.5,
                         "caractere.transpor": 0.5}),  # uma letra ou dígito
    "acrescimo": (0.05, {"campo.acrescentar": 1.0, "campo.remover": 1.0}),  # complemento (APTO, sobrenome) a mais ou a menos
}
GRUPO_DA_TECNICA = {tid: g for g, (_, ts) in PERFIL_PROFISSIONAL.items() for tid in ts}
# a ferramenta da versão nas técnicas parciais (a mesma pessoa apaga sempre do mesmo jeito): só as que copiam a textura
# do papel. O balde do Paint e o borrão deixam mancha lisa; a difusão (telea, ns) também, numa palavra inteira em papel
# granulado (o sobrenome apagado no lote de 04/10). O campo inteiro segue com o apagador dele (só a tinta, fundo local)
APAGAR_PROFISSIONAL = {"patchmatch": 0.65, "carimbo": 0.35}
COLAGEM_PROFISSIONAL = {"tinta": 0.7, "poisson": 0.3}  # a letra copiada: sem retângulo nem papel colado
# extras: grupos de classes que às vezes mudam junto com o nome/endereço, coerentes entre si (versões com 0, 1 e 2)
EXTRAS_PROB = (0.55, 0.35, 0.10)
GRUPOS_EXTRAS = {"documento": ("cpf/cnpj",), "local": ("cep", "bairro", "cidade/uf"),
                 "datas": ("data", "mês de referência"), "valores": ("valor", "consumo/leitura")}
_EXTRA_PEDE = {"documento": "nome", "local": "endereço"}  # o CPF sem o nome, o CEP sem a rua: troca sem motivo
_EXTRA_AS_VEZES = {"cidade/uf": 0.5}  # a cidade nova (que traz CEP e bairro dela, ``_JUNTO``) em metade das vezes
_DEPENDE_DO_VALOR = ("codigo barras", "linha digitável")  # ficam velhos quando o valor muda (a coerência acusa)


def _extras(presentes, classes: list[str], rng: random.Random) -> list[str]:
    """As classes extras da versão: 0, 1 ou 2 grupos (``EXTRAS_PROB``) entre os de ``GRUPOS_EXTRAS`` PRESENTES no
    documento e que fazem sentido com as classes base da versão (``_EXTRA_PEDE``); no grupo, a classe que entra só às
    vezes (``_EXTRA_AS_VEZES``) leva o que vai junto com ela (``_JUNTO``)."""
    grupos = {}
    for g, cs in GRUPOS_EXTRAS.items():
        membros = [c for c in cs if c in presentes and c not in classes]
        if membros and classes and (g not in _EXTRA_PEDE or _EXTRA_PEDE[g] in classes):
            grupos[g] = membros
    n = rng.choices(range(len(EXTRAS_PROB)), weights=EXTRAS_PROB)[0]
    out = []
    for g in rng.sample(sorted(grupos), min(n, len(grupos))):
        membros = grupos[g]
        ficam = [c for c in membros if c not in _EXTRA_AS_VEZES or rng.random() < _EXTRA_AS_VEZES[c]] or membros
        out += [c for c in membros if c in _com_o_que_vai_junto(ficam, membros)]
    return out


def _cpf_do_cliente(escolha: list[dict], campos: list[dict]) -> list[dict]:
    """Só o CPF/CNPJ do cliente fica na ``escolha``: o mais perto de um nome e o mesmo número em outros lugares. O da
    empresa que emite a conta (no cabeçalho, no quadro do beneficiário) não é o dado de quem frauda (a conta de
    verdade tem o CNPJ da operadora em dois lugares)."""
    docs = [f for f in escolha if f.get("categoria") == "cpf/cnpj"]
    nomes = [f for f in campos if f.get("categoria") == "nome"]
    if len(docs) < 2 or not nomes:
        return escolha

    def distancia(d):
        return min(abs(d["caixa"][1] - n["caixa"][1]) + 0.25 * abs(d["caixa"][0] - n["caixa"][0]) for n in nomes)
    chave = _chave_valor("cpf/cnpj", min(docs, key=distancia).get("texto") or "")
    return [f for f in escolha
            if f.get("categoria") != "cpf/cnpj" or _chave_valor("cpf/cnpj", f.get("texto") or "") == chave]


# datas só pelo campo inteiro, que segue o ciclo de datas da versão (vencimento, emissão e leitura andando juntos):
# o dígito ou o trecho trocado escolhia a data sozinho ("Emissão em 25/01/2026" → 2023 com o vencimento indo para junho),
# e quem frauda a data para a conta parecer recente move todas juntas
_SO_CAMPO_INTEIRO = frozenset({"data", "mês de referência"})


def _servem_profissional(f: dict, r: Receita, vetorial: bool = False, ia_online: bool = False,
                         fonte_ok=None) -> dict[str, list]:
    """As técnicas do perfil que servem para o campo, por grupo de granularidade (grupo sem nenhuma fica de fora).
    ``fonte_ok`` (sem argumentos, chamada só se preciso): a fonte do campo foi identificada? Sem ela, nenhuma técnica
    que desenha letra nova (``_DESENHA_NA_FONTE``); as que movem ou copiam as letras do documento continuam."""
    cat, s = f.get("categoria"), str(f.get("texto") or "")
    out: dict[str, list] = {}
    ok = None
    for g, (_, pesos) in PERFIL_PROFISSIONAL.items():
        if cat in _SO_CAMPO_INTEIRO and g != "campo":
            continue
        for tid in pesos:
            t = catalogo.TECNICAS.get(tid)
            if (t is None or tid not in r.tecnicas or (t.precisa_pdf and not vetorial) or (t.precisa_ia and not ia_online)
                    or not aplicavel(t, f) or not catalogo.compativel(t, cat)):
                continue
            if tid == "trecho.trocar" and (not _trechos(cat, s) or (cat == "bairro" and len(s.split()) > 1)):
                continue  # nada que se troque sozinho (nome de uma palavra só); bairro de várias palavras com uma
                # trocada vira um que não existe ("JARDIM AMERICA" → "CENTRO AMERICA")
            if tid == "campo.remover" and _inicio_parte_final(cat, s) is None:
                continue  # sem complemento nem último sobrenome para tirar, apagaria o campo inteiro
            if tid == "campo.acrescentar" and cat not in ("nome", "endereço"):
                continue  # o complemento é APTO/CASA ou sobrenome; no bairro inventava um ("BATEL SUL", "MERCÊS NOVO")
            if tid in _DESENHA_NA_FONTE and fonte_ok is not None:
                ok = bool(fonte_ok()) if ok is None else ok
                if not ok:
                    continue
            out.setdefault(g, []).append(t)
    return out


def _sortear_profissional(servem: dict[str, list], rng: random.Random) -> tuple[str, catalogo.Tecnica]:
    """(grupo, técnica): o grupo pelos pesos de granularidade, entre os que servem; a técnica pelos pesos do grupo."""
    grupos = [g for g in PERFIL_PROFISSIONAL if servem.get(g)]
    g = rng.choices(grupos, weights=[PERFIL_PROFISSIONAL[g][0] for g in grupos])[0]
    return g, rng.choices(servem[g], weights=[PERFIL_PROFISSIONAL[g][1][t.id] for t in servem[g]])[0]


def _tentativas(f: dict, r: Receita, rng: random.Random, imposto: bool, vetorial: bool, ia_online: bool,
                fonte_ok=None) -> list:
    """As técnicas a tentar no campo, em ordem. Perfil profissional: a sorteada e, se não for ela, a "mesma fonte"
    depois (a parcial que falha vira campo inteiro antes de desistir); com o texto ``imposto`` (o dado repetido que
    já mudou em outro lugar, a cidade nova da versão) só técnica de campo inteiro, que escreve o valor completo.
    ``fonte_ok``: ver ``_servem_profissional``. Fora do perfil (ou código de barras/QR): a de ``_tecnica_do_campo``."""
    if getattr(r, "perfil", None) != "profissional" or f.get("tipo", "texto") != "texto":
        t = _tecnica_do_campo(f, r, rng)
        return [t] if t else []
    servem = _servem_profissional(f, r, vetorial, ia_online, None if imposto else fonte_ok)
    if imposto:
        servem = {"campo": servem["campo"]} if servem.get("campo") else {}
    if not servem:
        return []
    _, t = _sortear_profissional(servem, rng)
    reserva = catalogo.TECNICAS["campo.mesma_fonte"]
    return [t] + ([reserva] if t is not reserva and reserva in servem.get("campo", ()) else [])


def _opcoes_profissionais(t: catalogo.Tecnica, f: dict, ferramentas: dict) -> dict:
    """O como no perfil profissional: o apagador e a colagem da versão nas técnicas parciais, sem vestígio do texto
    antigo, a fonte embutida do PDF e o alinhamento do campo; só a parte final no remover. O resto a técnica sorteia."""
    op = {}
    if "apagar" in t.opcoes and GRUPO_DA_TECNICA.get(t.id) != "campo":
        op["apagar"] = ferramentas["apagar"]
    if "colagem" in t.opcoes:
        op["colagem"] = ferramentas["colagem"]
    if "vestigio" in t.opcoes:
        op["vestigio"] = "nenhum"
    if t.id == "campo.reescrever_pdf":
        op.update(fonte_pdf="embutida",
                  alinhamento="direita" if f.get("categoria") in ("valor", "consumo/leitura") else "esquerda")
    if t.id == "campo.remover":
        op["parte"] = "final"
    return op


# as técnicas parciais que DESENHAM letra nova na fonte identificada, ao lado das letras originais do campo, e a
# semelhança que a identificação precisa ter para isso. No lote de 04/10 o pedaço desenhado destoou (mais grosso,
# mais fino, maior, fora da linha) em todos os campos da conta de verdade (0,81 a 0,94: "TIAGO" fino entre "J F A" e
# "FOTOS" em negrito com 0,938); nos sintéticos, que saíram bons, quase todos acima de 0,95
_DESENHA_NA_FONTE = frozenset({"trecho.trocar", "caractere.redesenhar", "caractere.inserir", "campo.acrescentar"})
LIMIAR_PARCIAL = 0.95


def _fonte_identificada(img: np.ndarray, ctx, f: dict) -> bool:
    """A fonte do campo foi identificada (semelhança ``LIMIAR_PARCIAL``)? Senão a letra desenhada destoa das originais
    em volta. Mesma chave de cache da técnica: a identificação não é refeita quando ela roda."""
    H, W = img.shape[:2]
    try:
        x0, y0, x1, y1 = (int(round(float(v))) for v in f["caixa"])
        aj = auto.ajustar_fonte_cache(img, (max(0, x0), max(0, y0), min(W, x1), min(H, y1)),
                                      str(f.get("texto") or "").strip(), getattr(ctx, "doc", None),
                                      identificar_fonte.votos_contexto(ctx), None)
    except ErroOperacao:
        return False
    return aj.get("metodo") != "estimativa" and float(aj.get("semelhanca") or 0) >= LIMIAR_PARCIAL


def _acento_que_fica(res, f: dict) -> bool:
    """A técnica parcial apagou ou moveu letra com acento, til ou cedilha? O sinal fica fora da caixa da letra e não sai
    com ela: ARAÚJO → SIMOES ficava "SIMÓES", e JOSÉ → JOSE continuava escrito JOSÉ."""
    m = res.meta or {}
    alt = (m.get("alteracoes") or [{}])[0]
    s = str(f.get("texto") or "")
    if m.get("tecnica") == "trecho.trocar":  # o trecho e, se a largura mudou, o resto da linha reescrito junto
        parte = s[int(alt.get("i", 0)):]
    elif m.get("tecnica") == "campo.remover":
        parte = str(alt.get("de") or "")
    elif m.get("tecnica") == "caractere.inserir":  # empurra o resto ou o começo do campo
        parte = s
    else:
        return False
    return any(unicodedata.combining(ch) for ch in unicodedata.normalize("NFD", parte))


def _desfazer_ate(sessao: Sessao, ent) -> None:
    """Desfaz até sair ``ent`` do histórico."""
    with sessao.lock:
        for _ in range(6):
            if not any(e is ent for e in sessao.historico_):
                break
            sessao.desfazer()


def _estilo(classes: list[str], extras: list[str], feitos: list[dict], campos: list[dict]) -> dict:
    """O que a versão do perfil profissional fez, para a anotação (``gerador.estilo``): as classes, as extras, a
    técnica e a granularidade de cada campo (pelo id do campo, o ``campo`` de cada edição) e, se o valor mudou, as
    classes que dependem dele e ficaram velhas (``velhos``: a regra ``codigo_total`` da coerência acusa)."""
    est = {"classes": list(classes) + list(extras), "extras": list(extras),
           "tecnicas": {x["campo"]: x["tecnica"] for x in feitos if x.get("campo")},
           "granularidade": {x["campo"]: GRUPO_DA_TECNICA.get(x["tecnica"], "campo") for x in feitos if x.get("campo")}}
    if any(x.get("categoria") == "valor" for x in feitos):
        velhos = sorted({f.get("categoria") for f in campos if f.get("categoria") in _DEPENDE_DO_VALOR}
                        - {x.get("categoria") for x in feitos})
        if velhos:
            est["velhos"] = velhos
    return est


def _tecnica_do_campo(f: dict, r: Receita, rng: random.Random) -> catalogo.Tecnica | None:
    """A técnica do campo no modo exato: a "mesma fonte" se a receita a tem e ela serve; senão, uma das técnicas
    da receita que serve (pelos pesos)."""
    servem = [catalogo.TECNICAS[t] for t in r.tecnicas if aplicavel(catalogo.TECNICAS[t], f)
              and catalogo.compativel(catalogo.TECNICAS[t], f.get("categoria"))]
    if not servem:
        return None
    for t in servem:
        if t.id == "campo.mesma_fonte":
            return t
    return rng.choices(servem, weights=[r.tecnicas[t.id] for t in servem])[0]


def editar_campos(sessao: Sessao, ctx, campos: list[dict], r: Receita, plano, rng: random.Random, editor=None,
                  ao_falhar=None, antigas: list | None = None, humano_hab: dict | None = None) -> list[dict]:
    """Edita EXATAMENTE os ``campos`` dados, de cima para baixo, um de cada vez. O mesmo dado repetido no documento
    (o nome no envelope e no boleto) recebe o mesmo valor novo, no formato de cada lugar. Campo que não dá para
    editar (nenhuma técnica serve, a conferência do OCR recusou) fica como está e vai para ``ao_falhar``.

    As ocorrências achadas em outras linhas (``ocorrencia``, de ``_ocorrencias``) vão depois dos campos, com o valor
    novo que eles já definiram. ``antigas`` (lista) recebe o lugar de cada dado que MUDOU em algum lugar da versão e
    ficou com o valor antigo aqui ({texto, caixa, categoria, motivo}): o rótulo diz onde sobrou dado velho.
    ``humano_hab``: o operador humano da versão (apaga sempre com a mesma ferramenta, seleciona do mesmo jeito)."""
    feitos, usados, novos_em = [], [], []  # novos_em[i]: o texto novo da edição que ocupa usados[i]
    pendentes = []  # (campo, chave, motivo, edição que ocupa o lugar) que ficaram como estavam
    trocados: dict[str, tuple[str, str]] = {}  # chave do dado antigo -> (texto antigo, texto novo)
    # cidade/UF trocada: UMA cidade nova para a versão inteira (cidade, UF e CEP coerentes entre si e com o CEP que
    # traz a cidade escrita junto), com nome de tamanho parecido com o da antiga
    nova_cidade = None
    cid = [f for f in campos if f.get("categoria") == "cidade/uf" and len((f.get("texto") or "").strip()) > 2]
    if any(f.get("categoria") == "cidade/uf" for f in campos):
        with geradores.usando(rng, getattr(ctx, "split", None)):
            nova_cidade = geradores.cidade_mais_parecida(cid[0]["texto"] if cid else "", rng)
    # a cidade da versão (a nova, ou a impressa no documento): bairro e CEP falsos saem dela ou são genéricos
    cidade_versao = nova_cidade or mod_falsificador._cidade_do_documento(getattr(ctx, "campos", None) or campos)
    # perfil profissional: a ferramenta da versão (quem apaga, como cola) e se o PDF tem texto e a IA está ligada
    perfil = getattr(r, "perfil", None) == "profissional"
    vetorial = bool(getattr(ctx, "vetorial", False))
    ia_online = perfil and "ia.reescrever" in r.tecnicas and bool(ia.status().get("online"))
    ferramentas = {"apagar": rng.choices(list(APAGAR_PROFISSIONAL), weights=list(APAGAR_PROFISSIONAL.values()))[0],
                   "colagem": rng.choices(list(COLAGEM_PROFISSIONAL), weights=list(COLAGEM_PROFISSIONAL.values()))[0]
                   } if perfil else None
    if perfil and humano_hab:  # a ferramenta de apagar é a do operador humano (FSTS: content-aware, cor, clone...)
        ferramentas["apagar"] = humano.APAGADOR.get(humano_hab.get("apagar"), ferramentas["apagar"])
    for n, f in enumerate(sorted(campos, key=lambda c: (bool(c.get("ocorrencia")), c["caixa"][1], c["caixa"][0]))):
        chave = _chave_valor(f.get("categoria") or "", f.get("texto") or "")
        if n >= MAXIMO_EXATO:
            pendentes.append((f, chave, f"limite de {MAXIMO_EXATO} campos por versão", None))
            continue
        sob = next((i for i, u in enumerate(usados) if _sobrepoe(f["caixa"], u)), None)
        if sob is not None:
            pendentes.append((f, chave, "dentro de outra edição", novos_em[sob]))
            continue
        texto = None
        if f.get("tipo", "texto") == "texto" and chave in trocados:
            texto = _mesmo_valor(f["categoria"], f["texto"], trocados[chave][1], trocados[chave][0])
        elif nova_cidade and f.get("categoria") == "cidade/uf":
            texto = geradores.cidade_no_formato(f["texto"], *nova_cidade)
        elif nova_cidade and f.get("categoria") == "cep":
            with geradores.usando(rng, getattr(ctx, "split", None)):
                texto = geradores.trocar_cidade_no_texto(geradores.parecido("cep", f["texto"], cidade=nova_cidade), *nova_cidade)
        tentativas = _tentativas(f, r, rng, texto is not None, vetorial, ia_online,
                                 fonte_ok=lambda f=f: _fonte_identificada(sessao.atual, ctx, f))
        if not tentativas:
            if ao_falhar:
                ao_falhar("", f, "nenhuma técnica da receita serve para este campo")
            pendentes.append((f, chave, "nenhuma técnica da receita serve para este campo", None))
            continue
        erros = []  # (técnica, motivo) de cada tentativa que não ficou
        for t in tentativas:
            p = _params(t, f, rng, r, None, texto, humano_hab)
            if perfil:
                p["opcoes"] = {**p["opcoes"], **_opcoes_profissionais(t, f, ferramentas)}
            try:
                with geradores.usando(cidade=cidade_versao):
                    res, ent = _executar_edicao(sessao, ctx, p, rng, r, editor)
            except ErroOperacao as e:
                erros.append((t.id, str(e)))
                continue
            motivo = None
            if f.get("linha") and _invade_linha(sessao.base, res, f):  # o valor novo não coube no trecho: desfaz
                motivo = "o valor novo não coube no trecho da linha"
            elif (perfil and texto is None
                  and _chave_valor(f.get("categoria") or "", res.meta.get("texto_novo") or "") == chave):
                motivo = "o dado não mudou (só acento ou caixa)"
            elif perfil and _acento_que_fica(res, f):
                motivo = "o acento da letra apagada ficaria no lugar"
            elif (perfil and t.id == "campo.reescrever_pdf"
                  and (res.meta.get("opcoes") or {}).get("fonte_pdf") == "substituta"):
                motivo = "a fonte embutida do PDF não serviu: sairia a fonte de reserva do editor (fonte padrão)"
            if motivo:
                _desfazer_ate(sessao, ent)
                erros.append((t.id, motivo))
                continue
            break
        else:
            motivo = erros[0][1] if len(erros) == 1 else "; ".join(f"{tid}: {e}" for tid, e in erros)
            if ao_falhar:
                ao_falhar(t.id, f, motivo)
            pendentes.append((f, chave, motivo, None))
            continue
        usados.append(f["caixa"])
        novos_em.append(res.meta.get("texto_novo") or "")
        if plano is not None:
            plano.contar(t.id, f["categoria"])
        feitos.append(_feito(ent, res))
        novo = res.meta.get("texto_novo")
        if novo and chave not in trocados:
            trocados[chave] = (f.get("texto") or "", novo)
    if antigas is not None:
        for f, chave, motivo, novo_ali in pendentes:
            if chave not in trocados or f.get("tipo", "texto") != "texto":
                continue  # o dado não mudou em lugar nenhum: nada se contradiz
            if novo_ali is not None and chave.split(":", 1)[1] not in _alnum(novo_ali)[0]:
                continue  # a edição que cobre o lugar já tirou o valor antigo (a linha toda foi reescrita)
            antigas.append({"texto": f.get("texto") or "", "caixa": [int(v) for v in f["caixa"]],
                            "categoria": f.get("categoria"), "motivo": motivo})
    return feitos


def _codigo_coerente(sessao, ctx, r, plano, rng, pessoa, campos, todos, feitos, usados, ao_falhar) -> None:
    """O falsificador meticuloso: mudou o total, então refaz o código de barras (e a linha) com o total novo."""
    novo_total = next((x["novo"] for x in feitos if x["categoria"] == "valor" and x["novo"]), None)
    if not novo_total or any(x["tecnica"] == "codigo.regenerar" for x in feitos):
        return
    par = next(((t, f) for t, f in todos if t.id == "codigo.regenerar"
                and not any(_sobrepoe(f["caixa"], u) for u in usados)), None)
    if par is None:
        return
    t, f = par
    p = _params(t, f, rng, r, pessoa)
    p["valor"] = novo_total
    try:
        with sessao.lock:
            res = executar("tecnica", sessao.atual, p, vazia(sessao.H, sessao.W), base=sessao.base, documento=ctx,
                           chave_cache=ctx.doc)
            ent = sessao.aplicar("tecnica", f["categoria"], "", p, res)
    except ErroOperacao as e:
        if ao_falhar:
            ao_falhar(t.id, f, str(e))
        return
    usados.append(f["caixa"])
    plano.contar(t.id, f["categoria"])
    feitos.append(_feito(ent, res))
    if res.meta.get("linha_digitavel"):
        par_linha = _reescrever_linha(sessao, ctx, campos, res.meta["linha_digitavel"], usados, rng, r)
        if par_linha:
            feitos.append(par_linha)


def _feito(ent, res) -> dict:
    m = res.meta
    return {"tecnica": m.get("tecnica"), "nome_tecnica": m.get("rotulo"), "familia": m.get("familia"),
            "nivel": m.get("nivel"), "categoria": ent.info.get("categoria"), "antigo": m.get("texto_antigo"),
            "novo": m.get("texto_novo"), "descricao": m.get("descricao"), "campo": m.get("campo"),
            "bbox": [res.x0, res.y0, res.x1, res.y1]}


def _perito_por_edicao(sessao: Sessao) -> dict:
    """As pistas de pixel que cada edição deixou (rotulador.perito), medidas na sessão: {ordem: {nota, medidas}}."""
    from . import perito

    mapa, ents = sessao.mapa_edicoes()
    out = {}
    for n in range(1, len(ents) + 1):
        reg = mapa == n
        if not reg.any():
            continue
        ys, xs = np.nonzero(reg)
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
        m = 2 * max(16, y1 - y0)
        Y0, Y1, X0, X1 = max(0, y0 - m), min(sessao.H, y1 + m), max(0, x0 - m), min(sessao.W, x1 + m)
        try:
            med = perito.medir(sessao.base[Y0:Y1, X0:X1], sessao.atual[Y0:Y1, X0:X1], reg[Y0:Y1, X0:X1])
            medidas = {k: med.get(k) for k in ("grao", "espectro", "nitidez", "tom", "blocos")}
            # sem nenhuma medida válida a nota fica None (desconhecida), não 0: 0 diria "invisível ao perito" e a
            # dificuldade da versão (1 − maior nota) sairia 1 só por falta de prova
            nota = perito.nota(med) if any(v is not None for v in medidas.values()) else None
            out[n] = {"nota": None if nota is None else float(nota), "medidas": medidas}
        except Exception:  # noqa: BLE001 - o perito nunca derruba o lote
            continue
    return out


def _coerencia(ctx, feitos: list[dict]) -> dict | None:
    """Coerência semântica antes e depois das edições (regras de verificador: CEP, datas, código, consumo)."""
    try:
        campos = list(ctx.campos)
        return coerencia.comparar(campos, coerencia.campos_finais(campos, feitos))
    except Exception:  # noqa: BLE001
        return None


def _reescrever_linha(sessao, ctx, campos, linha, usados, rng, r) -> dict | None:
    """O código de barras novo pede a linha digitável correspondente (senão o par se contradiz)."""
    f = next((c for c in campos if c.get("categoria") == "linha digitável"
              and not any(_sobrepoe(c["caixa"], u) for u in usados)), None)
    if f is None:
        return None
    p = {"tecnica": "campo.reescrever", "campo": {k: f[k] for k in ("id", "caixa", "texto", "categoria", "tipo")},
         "texto": linha, "semente": rng.randrange(2**31), "acabamento": r.acabamento}
    try:
        with sessao.lock:
            res = executar("tecnica", sessao.atual, p, vazia(sessao.H, sessao.W), base=sessao.base, documento=ctx,
                           chave_cache=ctx.doc)
            res.meta["descricao"] = (res.meta.get("descricao", "") +
                                     " Linha digitável refeita para combinar com o código de barras novo.")
            t = catalogo.TECNICAS["codigo.regenerar"]  # é parte da mesma fraude: conta como esta técnica
            res.meta.update({"tecnica": t.id, "tecnica_num": t.num, "rotulo": t.nome, "familia": t.familia,
                             "nivel": t.nivel, "granularidade": t.granularidade, "literatura": t.literatura,
                             "parte": "linha digitável coerente com o código novo"})
            # o exportar lê a técnica dos parâmetros: a linha refeita conta como codigo.regenerar
            ent = sessao.aplicar("tecnica", "linha digitável", "",
                                 {**p, "tecnica": "codigo.regenerar", "tecnica_executada": p["tecnica"]}, res)
    except ErroOperacao:
        return None
    usados.append(f["caixa"])
    return _feito(ent, res)


def _cantos(caixa) -> list[tuple]:
    x0, y0, x1, y1 = caixa
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _entrada_capturada(it) -> bool:
    """O arquivo de entrada já é uma foto ou um scan (JPEG/HEIC): a captura "antes" pode ser dispensada."""
    return Path(it.caminho).suffix.lower() in (".jpg", ".jpeg", ".heic", ".heif")


def _data_envio(ctx, rng: random.Random) -> dt.date:
    """A data em que a versão "é enviada": de 5 a 400 dias depois da data mais recente impressa no documento,
    para as datas falsas (conta recente) ficarem no mesmo ano dos originais e não virarem rótulo, e nunca depois
    de hoje (ninguém envia no futuro: falso com data depois da coleta dos originais também viraria rótulo). Sem
    data legível (ou data absurda do OCR), ou documento recente demais: hoje."""
    hoje = dt.date.today()
    datas = [d for f in ctx.campos if f.get("categoria") == "data" for d in [geradores.ler_data(f.get("texto", ""))] if d]
    base = max((d for d in datas if abs((d - hoje).days) <= 5 * 365), default=None)
    if base is None:
        return hoje
    de, ate = base + dt.timedelta(days=5), min(base + dt.timedelta(days=400), hoje)
    return de + dt.timedelta(days=rng.randint(0, (ate - de).days)) if ate > de else hoje


def _humano_da_versao(r: Receita, ctx, theta: dict | None, rng: random.Random) -> tuple[dict | None, dict | None]:
    """O operador humano da versão (ou None) e o canal com o "Salvar como" JPEG do editor dele, se ele salva assim: o
    passo entra no PAR (a original aberta e salva do mesmo jeito), antes de qualquer envio da fase "depois"."""
    if not getattr(r, "humano", None):
        return None, theta
    hab = humano.sortear(random.Random(rng.randrange(2**31)), r.humano, bool(getattr(ctx, "jpeg", None)))
    if not hab or hab.get("salvar") != "jpeg_editor":
        return hab, theta
    passo = canal.passo_salvar_editor(hab["gravacao"])
    if theta is None:
        return hab, {"perfil": "editor", "semente": rng.randrange(2**31), "passos": [passo]}
    i = next((k for k, q in enumerate(theta["passos"]) if q.get("fase", "depois") == "depois"), len(theta["passos"]))
    return hab, {**theta, "passos": theta["passos"][:i] + [passo] + theta["passos"][i:]}


def humano_da_sessao(sessao: Sessao, r: Receita, ctx, rng: random.Random) -> dict | None:
    """O operador humano de uma sessão do editor (o "Editar os marcados"): sorteado na primeira edição da versão e o
    MESMO nas seguintes (a mesma pessoa até a versão ser salva ou desfeita toda). Aqui não há o "Salvar como" do
    editor no par (quem salva é o botão da versão): quem salvaria em JPEG salva com as tabelas da origem, ou em PNG."""
    if not getattr(r, "humano", None):
        return None
    if sessao.historico_ and getattr(sessao, "humano_hab", None) is not None:
        return sessao.humano_hab or None
    jpeg = bool(getattr(ctx, "jpeg", None))
    hab = humano.sortear(random.Random(rng.randrange(2**31)), r.humano, jpeg)
    if hab and hab.get("salvar") == "jpeg_editor":
        hab = {k: v for k, v in hab.items() if k != "gravacao"} | {"salvar": "mesmas" if jpeg else "png"}
    sessao.humano_hab = hab or False
    return hab


def _png_b64(img: np.ndarray, maximo: int | None = None) -> str:
    im = Image.fromarray(img)
    if maximo and max(im.size) > maximo:
        esc = maximo / max(im.size)
        im = im.resize((max(1, round(im.width * esc)), max(1, round(im.height * esc))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=3)
    return base64.b64encode(buf.getvalue()).decode()


class Gerador:
    """Um lote por vez, em segundo plano. Também faz a prévia de uma receita num documento."""

    def __init__(self, registro: Registro, raiz_saida: Path, ids_categoria: dict[str, int]):
        self.registro, self.raiz_saida, self.ids = registro, Path(raiz_saida), ids_categoria
        self.lock = threading.Lock()
        self.thread = None
        self.parar = threading.Event()
        self.estado: dict = {"rodando": False}

    # ------------------------------------------------------------------ apoio
    def receita(self, d: dict) -> Receita:
        r = Receita(d or {}, self.ids)
        if r.usa_ia and r.perfil is None and not ia.status().get("online"):  # no perfil, a IA só entra se ligada
            raise ErroOperacao("o servidor de IA está desligado: desmarque as técnicas de IA ou ligue o servidor")
        return r

    def documentos(self, r: Receita) -> list:
        itens = []
        for sp in r.splits:
            for it in self.registro.por_split[sp]:
                if r.ids is not None and it.doc not in r.ids:
                    continue
                if r.so_pendentes and exportar.versoes_salvas(self.raiz_saida, sp, it.doc) > 0:
                    continue
                itens.append(it)
        return itens

    def _temp(self) -> Path:
        cache = self.raiz_saida / "_cache"
        cache.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="gerador_", dir=cache))

    # ------------------------------------------------------------------ canal em duas fases
    def _preparar(self, it, base: np.ndarray, ctx: Contexto, r: Receita, rng: random.Random, pessoa, cfg=None):
        """Canal e marcas de UMA versão. Sorteia o canal (``cfg`` força a configuração, na prévia) e, se ele
        tem captura ANTES da edição (foto/scan de quem edita no celular ou no Paint), aplica essa fase na base:
        as marcas benignas entram no papel antes da captura (longe de todo campo editável, menos a que a receita
        põe por cima com ``marcas.sobre_edicao``: a edição vem depois, por cima dela), os campos e as
        linhas do OCR são levados pela geometria (sem OCR de novo) e a versão ganha uma original própria.
        Devolve (base_v, ctx_v, theta, marcas_para_depois, extra_da_anotacao)."""
        if cfg is None:
            cfg = pessoa.canal(r.canal) if pessoa is not None else r.canal
        theta = None
        if cfg:
            theta = canal.sortear(random.Random(rng.randrange(2**31)), cfg, self._tabelas(it.split),
                                  captura=cfg.get("captura", "auto"), entrada_capturada=_entrada_capturada(it))
        # marcas de papel só com captura física; as digitais (tarja no CPF, marca d'água) em qualquer canal
        tm = marcas.sortear(random.Random(rng.randrange(2**31)), r.marcas, campos=ctx.campos,
                            fisica=canal.captura_fisica(theta)) if r.marcas else None
        if not theta or not any(p.get("fase") == "antes" for p in theta["passos"]):
            return base, ctx, theta, tm, {}
        imgs, info_marcas = [base], []
        if tm and canal.captura_fisica(theta, fase="antes"):
            papel, tm = marcas.dividir(tm)  # as de papel entram antes da captura; as digitais, depois da edição
            if papel:
                proibido = np.zeros(base.shape[:2], bool)  # marca no papel, longe de todo campo que a receita pode editar
                for f in ctx.campos:
                    if f.get("categoria") in r.categorias:
                        x0, y0, x1, y1 = f["caixa"]
                        proibido[max(0, y0):y1, max(0, x0):x1] = True
                (marcado,), info_marcas = marcas.aplicar([base], proibido, papel)
                imgs = [marcado]
        pontos: list[tuple] = []
        for cx in (tm or {}).get("alvos") or []:  # os alvos da tarja acompanham a geometria da captura
            pontos += _cantos(cx)
        for l in ctx.linhas:
            pontos += _cantos(l["caixa"])
        for f in ctx.campos:
            pontos += _cantos(f["caixa"]) + _cantos(f.get("caixa_ocr", f["caixa"]))
        for m in info_marcas:
            pontos += _cantos(m["caixa"])
        (base_v,), _, _, mapeados = canal.aplicar(imgs, [], [], theta, fase="antes", pontos=pontos)
        tabela = {p: (float(q[0]), float(q[1])) for p, q in zip(pontos, mapeados)}

        def mapear(pts):
            return [tabela[tuple(p)] for p in pts]

        ctx_v = ctx.derivado(base_v, mapear, str(theta["semente"]))

        def caixa_mapeada(caixa):
            pts = mapear(_cantos(caixa))
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            return [int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1]

        for m in info_marcas:
            m["caixa"] = caixa_mapeada(m["caixa"])
        if tm and tm.get("alvos"):
            tm = {**tm, "alvos": [caixa_mapeada(cx) for cx in tm["alvos"]]}
        extra = {"captura_antes": canal.descrever({**theta, "passos": [p for p in theta["passos"]
                                                                        if p.get("fase") == "antes"]})}
        if info_marcas:
            extra["marcas_benignas"] = info_marcas
        return base_v, ctx_v, theta, tm, extra

    # ------------------------------------------------------------------ prévia
    def previa(self, d: dict, doc: str) -> dict:
        """Roda a receita num documento SEM salvar: imagem editada, original e as edições."""
        r = self.receita(d)
        try:
            it = self.registro.item(doc)
        except KeyError:
            raise ErroOperacao("documento não encontrado")
        base = self.registro.carregar(it.doc)
        temp = self._temp()
        try:
            ctx = Contexto(it, base, self.raiz_saida, self.registro)
            falhas = []
            rng = random.Random()
            pessoa = r.falsificador(rng, ia.status().get("online", False))
            cfg = pessoa.canal(r.canal) if pessoa is not None else r.canal
            cfg = {**cfg, "prob": 1.0} if cfg and cfg["prob"] > 0 else None  # o canal entra sempre, na prévia
            base_v, ctx_v, theta, tm, extra = self._preparar(it, base, ctx, r, rng, pessoa, cfg=cfg)
            hab, theta = _humano_da_versao(r, ctx_v, theta, rng)
            sess = Sessao(it, base_v.copy(), temp / "previa", self.ids)
            info: dict = {}
            with geradores.usando(hoje=_data_envio(ctx, rng), documento=ctx_v.campos):  # um ciclo de datas por versão
                feitos = aplicar_receita(sess, ctx_v, r, Plano(r, random.Random()), rng,
                                         ao_falhar=lambda t, f, e: falhas.append({"tecnica": t, "categoria": f.get("categoria"),
                                                                                  "erro": e}), pessoa=pessoa, info=info,
                                         humano_hab=hab)
            mapa, ents = sess.mapa_edicoes()
            tecn = exportar.mascara_tecnicas(mapa, ents)
            pelo_canal = {}
            if theta and feitos:  # o par pela fase "depois" do canal (a "antes" já está na base da versão)
                _, regiao, _ = sess.mascaras()  # a geometria muda, então as caixas valem só sem canal
                # (não é o ``info`` de aplicar_receita: as classes e o estilo da versão ainda vão para a resposta)
                original, editada, marcas_v = base_v, sess.atual, extra.get("marcas_benignas", [])
                if tm:
                    (original, editada), marcas_v = marcas.aplicar([original, editada], regiao, tm)
                (original, editada), _, _ = canal.aplicar([original, editada], [regiao], [], theta, fase="depois")
                pelo_canal = {"canal_editada": _png_b64(editada, 1400), "canal_original": _png_b64(original, 1400),
                              "canal": canal.descrever(theta), "marcas": marcas.descrever(marcas_v)}
            recortes = []
            for f in feitos:
                # a linha inteira do OCR em volta da edição, não só o pedaço trocado ("PTO 38" não diz onde foi)
                x0, y0, x1, y1 = f["bbox"]
                for l in ctx_v.linhas:
                    if _sobrepoe(l["caixa"], f["bbox"], 0):
                        lx0, ly0, lx1, ly1 = (int(v) for v in l["caixa"])
                        x0, y0, x1, y1 = min(x0, lx0), min(y0, ly0), max(x1, lx1), max(y1, ly1)
                m = max(12, (f["bbox"][3] - f["bbox"][1]))
                cx0, cy0 = max(0, x0 - 2 * m), max(0, y0 - m)
                cx1, cy1 = min(sess.W, x1 + 2 * m), min(sess.H, y1 + m)
                recortes.append({"antes": _png_b64(base_v[cy0:cy1, cx0:cx1], 900),
                                 "depois": _png_b64(sess.atual[cy0:cy1, cx0:cx1], 900)})
            return {"doc": it.doc, "nome": it.nome, "split": it.split, "largura": sess.W, "altura": sess.H,
                    "falsificador": pessoa.descrever() if pessoa is not None and feitos else None,
                    "editada": _png_b64(sess.atual, 1400), "original": _png_b64(base_v, 1400), **pelo_canal,
                    "captura_antes": extra.get("captura_antes"), "coerencia": _coerencia(ctx_v, feitos) if feitos else None,
                    "mascara_tecnicas": _png_b64(exportar.colorir_tecnicas(tecn), 1400),
                    "edicoes": feitos, "recortes": recortes, "falhas": falhas[-20:],
                    "classes": info.get("classes"), "ocorrencias_antigas": info.get("ocorrencias_antigas"),
                    "estilo": info.get("estilo"),
                    "humano": humano.descrever(hab) if hab or r.perfil == "profissional" else None}
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    # ------------------------------------------------------------------ lote
    def iniciar(self, d: dict) -> dict:
        r = self.receita(d)
        itens = self.documentos(r)
        if not itens:
            raise ErroOperacao("nenhum documento selecionado (ou todos já têm versão salva)")
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ErroOperacao("já há uma geração rodando; espere terminar ou pare")
            self.parar.clear()
            self.estado = {"rodando": True, "id": uuid.uuid4().hex[:8], "documentos": len(itens),
                           "total": len(itens) * r.variacoes, "feitas": 0, "falhas": 0, "edicoes": 0, "atual": "",
                           "por_tecnica": {}, "por_categoria": {}, "falhas_tecnica": {}, "erros": [], "recentes": [],
                           "por_falsificador": {}, "por_motivo": {}, "inicio": time.time(), "receita": r.resumo()}
            self.thread = threading.Thread(target=self._rodar, args=(r, itens), daemon=True)
            self.thread.start()
            return self.status()

    def status(self) -> dict:
        e = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
             for k, v in self.estado.items()}
        # versões excluídas pelo catálogo saem da lista de recentes
        e["recentes"] = [v for v in e.get("recentes", [])
                         if (self.raiz_saida / v["split"] / "anotacoes" / f"{v['nome']}.json").is_file()]
        if e.get("rodando") and e.get("feitas"):
            gasto = time.time() - e["inicio"]
            e["restante_s"] = int(gasto / (e["feitas"] + e["falhas"]) * (e["total"] - e["feitas"] - e["falhas"]))
        return e

    def interromper(self) -> dict:
        self.parar.set()
        return self.status()

    def _falhou_tecnica(self, tid: str, _campo, _erro) -> None:
        ft = self.estado["falhas_tecnica"]
        ft[tid] = ft.get(tid, 0) + 1

    def _tabelas(self, split: str) -> list[dict]:
        """Tabelas de quantização dos JPEG de entrada do split (em cache): o canal usa as tabelas dos
        aparelhos de verdade, do mesmo split."""
        cache = getattr(self, "_cache_tabelas", None)
        if cache is None:
            cache = self._cache_tabelas = {}
        if split not in cache:
            cache[split] = canal.tabelas_de(sorted({it.caminho for it in self.registro.por_split[split]}))
        return cache[split]

    def _rodar(self, r: Receita, itens: list) -> None:
        rng = random.SystemRandom()
        ia_online = bool(ia.status().get("online")) if r.falsificadores else False
        # um plano (cota da distribuição) por split: com um só, o split que roda depois fica com as
        # sobras do anterior e cada split sai com uma mistura diferente de técnicas e classes
        planos: dict[str, Plano] = {}
        temp = self._temp()
        est = self.estado
        try:
            for it in itens:
                if self.parar.is_set():
                    break
                if it.split not in planos:
                    planos[it.split] = Plano(r, random.Random(rng.randrange(2**31)),
                                             _ja_salvas(self.raiz_saida, it.split) if r.distribuicao == "cobertura" else None)
                plano = planos[it.split]
                est["atual"] = f"{it.split}/{it.nome}"
                try:
                    base = self.registro.carregar(it.doc)
                    ctx = Contexto(it, base, self.raiz_saida, self.registro)
                    ctx.campos  # OCR uma vez por documento (fica em cache)
                except Exception as e:  # documento ilegível não para o lote
                    est["falhas"] += r.variacoes
                    est["erros"].append(f"{it.nome}: {e}")
                    continue
                sorteados: list = []  # subconjuntos de classes já usados pelas versões deste documento
                for v in range(r.variacoes):
                    if self.parar.is_set():
                        break
                    pasta = temp / f"{it.doc}_{v}"
                    sess = None
                    try:
                        rng_v = random.Random(rng.randrange(2**31))
                        pessoa = r.falsificador(rng_v, ia_online)
                        # canal: a captura "antes" (foto/scan de quem edita no celular ou no Paint) vira a base da
                        # versão; impressão, tela e transmissão ficam para depois, no salvamento, iguais no par;
                        # marcas no papel só com captura física (senão seriam colagem digital "benigna")
                        base_v, ctx_v, theta, tm, extra_v = self._preparar(it, base, ctx, r, rng_v, pessoa)
                        hab, theta = _humano_da_versao(r, ctx_v, theta, rng_v)
                        sess = Sessao(it, base_v.copy(), pasta, self.ids)
                        envio = _data_envio(ctx, rng_v)
                        info: dict = {}
                        with geradores.usando(hoje=envio, documento=ctx_v.campos):  # datas e valores coerentes na versão
                            feitos = aplicar_receita(sess, ctx_v, r, plano, rng_v, ao_falhar=self._falhou_tecnica, pessoa=pessoa,
                                                     info=info, ja_sorteados=sorteados, humano_hab=hab)
                        if not feitos:
                            raise ErroOperacao("nenhuma técnica coube neste documento")
                        extra = {"gerador": {"lote": est["id"], "receita": r.resumo(), "variacao": v + 1,
                                             "data_envio": envio.isoformat()}, **extra_v,
                                 "coerencia": _coerencia(ctx_v, feitos), "perito_por_edicao": _perito_por_edicao(sess)}
                        if "classes" in info:  # modo exato: as classes desta versão e o dado antigo que sobrou (caixas
                            # nas coordenadas da sessão, antes do canal de envio)
                            extra["gerador"]["classes"] = info["classes"]
                            extra["ocorrencias_antigas"] = info["ocorrencias_antigas"]
                        if "estilo" in info:  # perfil profissional: técnica e granularidade de cada campo, extras
                            extra["gerador"]["estilo"] = info["estilo"]
                        # o operador humano da versão (None: acabamento meticuloso, como antes)
                        extra["gerador"]["humano"] = {**hab, "descricao": humano.descrever(hab)} if hab else None
                        if hab:
                            est.setdefault("por_humano", {})
                            chave_h = f"{hab['selecao']}/{hab['apagar']}/{hab['salvar']}"
                            est["por_humano"][chave_h] = est["por_humano"].get(chave_h, 0) + 1
                        if pessoa is not None:
                            extra["falsificador"] = pessoa.descrever()
                            est["por_falsificador"][pessoa.nome] = est["por_falsificador"].get(pessoa.nome, 0) + 1
                            est["por_motivo"][pessoa.motivo] = est["por_motivo"].get(pessoa.motivo, 0) + 1
                        try:
                            salvo = exportar.salvar(sess, self.raiz_saida, r.formato, r.qualidade, r.operador,
                                                    self.registro.dpi, extra=extra, canal_envio=theta,
                                                    marcas_benignas=tm, por_versao=bool(extra_v))
                        except exportar.CanalApagouEdicao:  # ex.: o scanner estourou um retoque claro
                            salvo = exportar.salvar(sess, self.raiz_saida, r.formato, r.qualidade, r.operador,
                                                    self.registro.dpi, extra=extra, por_versao=bool(extra_v))
                        est["feitas"] += 1
                        est["edicoes"] += len(feitos)
                        for f in feitos:
                            est["por_tecnica"][f["tecnica"]] = est["por_tecnica"].get(f["tecnica"], 0) + 1
                            est["por_categoria"][f["categoria"]] = est["por_categoria"].get(f["categoria"], 0) + 1
                        caixas = [{"tecnica": e["tecnica"], "nome_tecnica": e["nome_tecnica"], "familia": e["familia"],
                                   "nivel": e["nivel"], "categoria": e["categoria"], "antigo": e["texto_antigo"],
                                   "novo": e["texto_novo"], "descricao": e["descricao"], "bbox": e["bbox"]}
                                  for e in salvo["lista_edicoes"]]  # nas coordenadas da imagem salva (canal)
                        est["recentes"] = ([{"split": it.split, "nome": f"{it.doc}__v{salvo['versao']:02d}",
                                             "documento": it.nome, "largura": salvo["largura"],
                                             "altura": salvo["altura"], "edicoes": caixas,
                                             "humano": humano.descrever(hab) if hab or r.perfil == "profissional" else None,
                                             "envio": canal.descrever(theta) if theta else None}]
                                           + est["recentes"])[:12]
                    except Exception as e:
                        est["falhas"] += 1
                        est["erros"] = (est["erros"] + [f"{it.nome} v{v + 1}: {e}"])[-50:]
                        if not isinstance(e, (ErroOperacao, exportar.CanalApagouEdicao)):
                            traceback.print_exc()
                    finally:
                        shutil.rmtree(pasta, ignore_errors=True)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
            try:  # o datasheet acompanha o que está gravado
                datasheet.escrever(self.raiz_saida)
            except Exception:  # noqa: BLE001 - documentação não derruba o lote
                traceback.print_exc()
            est["rodando"] = False
            est["atual"] = ""
            est["fim"] = time.time()
            est["interrompido"] = self.parar.is_set()


CATEGORIAS_FOCO = ("nome", "endereço")  # o que mais se falsifica num comprovante de endereço
# fração das versões do perfil profissional editadas como uma pessoa faria (rotulador.humano); o resto fica com o
# acabamento meticuloso (só a tinta, cor calibrada, grão espectral), a ponta difícil que um perfeccionista alcança
HUMANO_PROB = 0.85

# Estilos prontos da tela de um clique: cada um é uma combinação testada de quem edita (operador humano ou acabamento
# perfeito) e do caminho do arquivo depois. A tela mostra ``nome``, ``resumo``, ``faz``, ``bom_para`` e ``aviso``;
# a receita leva ``humano`` e ``canal``. Um só lugar para a tela, a API e a documentação
ESTILOS = {
    "recomendado": {
        "icone": "⭐", "nome": "Como gente de verdade", "humano": HUMANO_PROB, "canal": None,
        "resumo": "A maioria das versões é editada como uma pessoa faria num editor de imagem; algumas saem perfeitas.",
        "faz": ["seleciona em volta do texto e apaga com uma ferramenta de editor (preenchimento inteligente, "
                "carimbo, pincel de recuperação, cor do papel)",
                "escreve na mesma fonte do documento, pega a cor com o conta-gotas e acerta tamanho e posição no olho",
                "às vezes desfoca, aguça ou põe ruído e salva como o programa salvaria (JPEG do Photoshop, do Paint, PNG)",
                f"{round(100 * (1 - HUMANO_PROB))}% das versões saem com acabamento perfeito, o caso mais difícil"],
        "bom_para": "o dia a dia: treinar o detector com fraudes parecidas com as de verdade"},
    "perfeccionista": {
        "icone": "🎯", "nome": "Perfeccionista", "humano": 0.0, "canal": None,
        "resumo": "Nenhum rastro de editor: só as letras mudam, com cor, grão e desfoque iguais aos do documento.",
        "faz": ["apaga só a tinta das letras antigas e reconstrói o papel em volta",
                "escreve na mesma fonte, com a cor e o desfoque medidos no documento",
                "põe no papel editado o mesmo grão do papel original e mantém a compressão do arquivo"],
        "bom_para": "medir o detector no caso mais difícil (um falsificador meticuloso)"},
    "pessoas": {
        "icone": "🧑", "nome": "Só como pessoas", "humano": 1.0, "canal": None,
        "resumo": "Todas as versões editadas como uma pessoa faria; nenhuma perfeita.",
        "faz": ["cada versão tem uma pessoa diferente, com a ferramenta e os hábitos dela",
                "seleção, ferramenta de apagar, conta-gotas, ajuste no olho, retoques e o jeito de salvar variam"],
        "bom_para": "montar um conjunto de teste parecido com fraude real"},
    "atendimento": {
        "icone": "📤", "nome": "Como chega no atendimento", "humano": HUMANO_PROB,
        "canal": {"perfil": "envio", "prob": 0.7},
        "resumo": "Como gente de verdade e, depois, o arquivo é mandado pelo WhatsApp, e-mail ou rede social.",
        "faz": ["a edição é a do estilo ⭐ Como gente de verdade",
                "em 70% das versões o arquivo passa por um envio: o WhatsApp reduz e recomprime, o e-mail não muda nada",
                "a original passa pelo MESMO envio, então a diferença entre as duas continua sendo só a edição"],
        "aviso": "a imagem inteira muda um pouco (tamanho e compressão), igual na original e na falsa; sem foto e sem "
                 "cortar a página",
        "bom_para": "treinar para os arquivos que o atendimento recebe de verdade"},
}
ESTILO_PADRAO = "recomendado"


def estilos() -> list[dict]:
    """Os estilos prontos, na ordem da tela, para a API."""
    return [{"id": k, "padrao": k == ESTILO_PADRAO, **{c: v for c, v in e.items() if c not in ("humano", "canal")},
             "humano": e["humano"], "canal": e["canal"]} for k, e in ESTILOS.items()]


def receita_limpa(ids_categoria: dict[str, int], categorias=CATEGORIAS_FOCO, com_ia: bool = False) -> dict:
    """A receita padrão: edita SÓ a original, exatamente os campos das ``categorias`` (todos que o leitor achar),
    escrevendo o valor novo na mesma fonte identificada entre as 10 mais usadas; sem canal de envio, marcas,
    falsificador nem disfarce (a imagem salva é o documento com a edição, e nada mais). Código de barras e QR, se
    pedidos, são gerados de novo. Cada versão sorteia QUAIS das classes edita (``subconjuntos``; no lote)."""
    cats = [c for c in categorias if c in ids_categoria and c != "outro"] or [c for c in CATEGORIAS_FOCO if c in ids_categoria]
    tecnicas = {"ia.reescrever" if com_ia else "campo.mesma_fonte": 1}
    if "codigo barras" in cats:
        tecnicas["codigo.regenerar"] = 1
    if "qr code/pix" in cats:
        tecnicas["codigo.qr"] = 1
    return {"exato": True, "subconjuntos": True, "tecnicas": tecnicas, "categorias": {c: 1 for c in cats}, "edicoes": [1, 3],
            "distribuicao": "equilibrada", "acabamento": {"ruido": "auto", "disfarce": "nunca", "jpeg": 0}}


def receita_profissional(ids_categoria: dict[str, int], base=CATEGORIAS_FOCO, extras: bool = True,
                         com_ia: bool = False, estilo: str | None = None) -> dict:
    """A receita da tela de um clique (e do "Editar os marcados"): edita SÓ a original, sem canal, marcas,
    falsificador nem disfarce; em cada versão, as classes ``base`` (nome e/ou endereço, pelo sorteio de
    ``_subconjunto``) e, com ``extras``, às vezes também um ou dois grupos coerentes entre si (``GRUPOS_EXTRAS``: o
    CPF; CEP, bairro e cidade; as datas e o mês de referência; o valor e o consumo). Cada campo sai com uma técnica
    PROFISSIONAL sorteada por granularidade (``PERFIL_PROFISSIONAL``): o campo inteiro (na mesma fonte, com as letras
    do documento, na camada de texto do PDF ou pela IA, com ``com_ia`` e o servidor ligado), só um trecho, uma letra
    ou dígito, um complemento a mais ou a menos. ``mistura`` resume os pesos, para a tela mostrar o que vai acontecer.
    Em ``HUMANO_PROB`` das versões a edição é feita como uma PESSOA a faria num editor (``rotulador.humano``: seleção,
    ferramenta, suavização, conta-gotas, retoques e o "Salvar como"); no resto, com o acabamento meticuloso.
    ``estilo`` (``ESTILOS``; padrão ``ESTILO_PADRAO``) decide essa fração e o envio do arquivo depois.
    ``Receita`` expande ``{"perfil": "profissional", "extras": ..., "categorias_base": [...], "estilo": ...}`` nesta
    receita."""
    estilo = estilo if estilo in ESTILOS else ESTILO_PADRAO
    est = ESTILOS[estilo]
    validas = [c for c in ids_categoria if c != "outro"]
    base = [c for c in dict.fromkeys(base or ()) if c in validas] or [c for c in CATEGORIAS_FOCO if c in validas]
    cats = base + ([c for g in GRUPOS_EXTRAS.values() for c in g if c in validas and c not in base] if extras else [])
    tecnicas, mistura = {}, {}
    for g, (peso, ts) in PERFIL_PROFISSIONAL.items():
        ts = {tid: w for tid, w in ts.items() if tid in catalogo.TECNICAS and catalogo.TECNICAS[tid].automatica
              and (com_ia or not catalogo.TECNICAS[tid].precisa_ia)}
        for tid, w in ts.items():
            tecnicas[tid] = round(100 * peso * w / sum(ts.values()), 2)
        mistura[g] = {"peso": peso, "tecnicas": list(ts)}
    if "codigo barras" in base:
        tecnicas["codigo.regenerar"] = 1
    if "qr code/pix" in base:
        tecnicas["codigo.qr"] = 1
    return {"perfil": "profissional", "extras": bool(extras), "categorias_base": base, "com_ia": bool(com_ia),
            "exato": True, "subconjuntos": True, "tecnicas": tecnicas, "categorias": {c: 1 for c in cats},
            "edicoes": [1, 3], "distribuicao": "equilibrada", "acabamento": {"ruido": "auto", "disfarce": "nunca", "jpeg": 0},
            "humano": {"prob": est["humano"]} if est["humano"] > 0 else None, "canal": est["canal"], "estilo": estilo,
            "mistura": {"granularidade": mistura, "humano": est["humano"], "estilo": estilo, "extras": {"prob": list(EXTRAS_PROB) if extras else [1.0],
                                                             "grupos": {g: list(cs) for g, cs in GRUPOS_EXTRAS.items()}}}}


# o que o perfil define (o que vier junto na receita com "perfil" é ignorado); o resto (documentos, variações, nome,
# formato...) continua valendo
_CHAVES_DO_PERFIL = ("perfil", "extras", "categorias_base", "com_ia", "tecnicas", "categorias", "tipos", "exato",
                     "subconjuntos", "mistura", "estilo")


def _expandir_perfil(d: dict, ids_categoria: dict[str, int]) -> dict:
    base = d.get("categorias_base")
    base = [base] if isinstance(base, str) else base
    exp = receita_profissional(ids_categoria, tuple(base) if base else CATEGORIAS_FOCO,
                               extras=bool(d.get("extras", True)), com_ia=bool(d.get("com_ia")), estilo=d.get("estilo"))
    return {**exp, **{k: v for k, v in d.items() if k not in _CHAVES_DO_PERFIL}}


def receita_padrao(ids_categoria: dict[str, int], com_ia: bool = False) -> dict:
    """Todas as técnicas automáticas (IA só se pedido), pesos padrão das categorias."""
    return {"tecnicas": {t.id: 1 for t in catalogo.TECNICAS.values() if t.automatica and (com_ia or not t.precisa_ia)},
            "categorias": {k: PESOS_CATEGORIA.get(k, 1.0) for k in ids_categoria if k != "outro"},
            "distribuicao": "pesos", "edicoes": [1, 3]}
