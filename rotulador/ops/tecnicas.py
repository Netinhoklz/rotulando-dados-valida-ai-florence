"""Técnicas automáticas de adulteração (o catálogo está em rotulador/catalogo.py).

Todas entram pela operação "tecnica" (p["tecnica"] = id no catálogo) sobre um CAMPO
detectado (p["campo"] = {caixa, texto, categoria, tipo, id}) e devolvem um Resultado
com o RÓTULO completo em ``meta``: técnica, família, nível, granularidade, texto antes e
depois, quais caracteres mudaram e uma descrição legível.

As escolhas (qual letra, qual valor, como colar) saem de random.Random(p["semente"]):
os mesmos parâmetros dão o mesmo resultado, então a prévia é igual ao que é aplicado.

``documento`` (rotulador.contexto.Contexto) dá campos e linhas do OCR, o inventário de
letras e os doadores do MESMO split, usados pelas técnicas de cópia e de colagem.

Peças comuns:
  - fundo: a tinta a apagar vira buraco num PatchMatch em que TODA a tinta em volta também
    é buraco, então só papel é copiado (letras vizinhas não "vazam" para dentro);
  - texto desenhado: fonte, tamanho, largura e negrito imitados (auto.ajustar_fonte), pé na
    linha de base medida nos glifos e desfoque igual ao do scan (glifos.estimar_desfoque);
  - glifo copiado: alfa suave (projeção na reta papel->tinta) e posição subpixel.
"""

from __future__ import annotations

import copy
import difflib
import inspect
import io
import math
import random
import re
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from .. import catalogo, glifos, humano, identificar_fonte, ocr, pericia, selecao, vetorial
from ..selecao import mascara_tinta
from ..sessao import ErroOperacao, Resultado
from . import ajustes, auto, codigos, geradores, ia, texto
from .apagar import reconstruir
from .base import inteiro
from .pro import poisson

_EL = {r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)) for r in (1, 2, 3, 4)}
_MESES = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")


def _dilatar(m: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return m.astype(bool)
    if r not in _EL:  # raios maiores (o furo do campo.remover chega a 8) entram sob demanda
        _EL[r] = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(m.astype(np.uint8), _EL[r]).astype(bool)


def _longe(m: np.ndarray, de: np.ndarray, d: float) -> np.ndarray:
    """Pixels de ``m`` a mais de ``d`` px de qualquer pixel de ``de``."""
    if not de.any():
        return m.copy()
    dist = cv2.distanceTransform((~de).astype(np.uint8), cv2.DIST_L2, 3)
    return m & (dist > d)


def _uniao(*caixas):
    cs = [c for c in caixas if c is not None]
    return min(c[0] for c in cs), min(c[1] for c in cs), max(c[2] for c in cs), max(c[3] for c in cs)


# categorias em que o SORTEIO pode trocar letra. Vazio: trocar uma letra sem lista de palavras
# vira erro de grafia (JOAA, PAREIRA, CARITIBA) que OCR/VLM acusa sem olhar pixel. Fraude real de
# nome/cidade troca a palavra inteira (técnicas de campo e trecho). Letra só quando pedida.
LETRA_PLAUSIVEL: frozenset = frozenset()
# técnicas que identificam a fonte só entre as 10 mais usadas em documentos e põem o grão espectral do papel
_TOP10 = frozenset({"campo.mesma_fonte", "campo.letras_do_documento"})


def _classe(ch: str) -> str:
    return "digito" if ch.isdigit() else "letra" if ch.isalpha() else "outro"


def _alt(i: int, de: str, para: str, tipo: str = "troca") -> dict:
    return {"i": int(i), "de": de, "para": para, "tipo": tipo}


def plausivel(cat: str | None, antigo: str, novo: str) -> bool:
    """O valor novo ainda é um valor possível da categoria? (data existe, mês 1-12, ano perto do
    original, número sem zero à esquerda). Fraude com data 35/14/4026 não engana ninguém."""
    import datetime as dt

    def ano_perto(a_novo: str, a_velho: str) -> bool:
        return abs(int(a_novo) - int(a_velho)) <= 3

    if cat == "data":
        m_n = re.search(r"(\d{2})[/.\-](\d{2})[/.\-](\d{2,4})", novo)
        m_v = re.search(r"(\d{2})[/.\-](\d{2})[/.\-](\d{2,4})", antigo)
        if m_n and m_v:
            d, mth, a = (int(x) for x in m_n.groups())
            try:
                dt.date(a if a > 99 else 2000 + a, mth, d)
            except ValueError:
                return False
            return ano_perto(m_n.group(3), m_v.group(3))
    if cat == "mês de referência":
        m_n, m_v = re.search(r"(\d{2,4})\s*$", novo), re.search(r"(\d{2,4})\s*$", antigo)
        if m_n and m_v and not ano_perto(m_n.group(1), m_v.group(1)):
            return False
        m = re.search(r"\b(\d{2})/\d{4}\b", novo)
        if m and not 1 <= int(m.group(1)) <= 12:
            return False
    if cat in ("valor", "consumo/leitura"):
        m = re.search(r"\d[\d.]*", novo)
        if m and len(m.group()) > 1 and m.group()[0] == "0" and not m.group().startswith("0,"):
            return False
    if cat == "valor":
        n_novo, n_velho = re.search(r"[\d.]*\d,\d{2}", novo), re.search(r"[\d.]*\d,\d{2}", antigo)
        if n_velho and not n_novo:
            return False
        if n_novo:
            num = n_novo.group()
            if num.startswith(".") or re.search(r"\.(?!\d{3}(?:[.,]))", num):
                return False  # ponto de milhar fora do lugar (R$ 1.23,56; R$ .234,56)
            inteiro = num.split(",")[0].replace(".", "")
            if n_velho and "." in n_velho.group() and len(inteiro) > 3 and "." not in num:
                return False  # o original agrupa os milhares, o novo também tem de agrupar
            if "." in num and not re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{3})+,\d{2}", num):
                return False
    return True


def _mesma_caixa(antigo: str, novo: str) -> str:
    letras = [ch for ch in antigo if ch.isalpha()]
    if letras and all(ch.isupper() for ch in letras):
        return novo.upper()
    if letras and all(ch.islower() for ch in letras):
        return novo.lower()
    if antigo[:1].isupper() and antigo[1:].islower():
        return novo.capitalize()
    return novo


# ============================================================================ contexto de uma chamada
class _C:
    def __init__(self, img, p, base, documento, carregar_doador, chave_cache, editor):
        self.img, self.p, self.base = img, p, base
        self.H, self.W = img.shape[:2]
        self.documento, self.carregar_doador, self.editor = documento, carregar_doador, editor
        self.tec = catalogo.TECNICAS.get(str(p.get("tecnica", "")))
        if self.tec is None or not self.tec.automatica:
            raise ErroOperacao(f"técnica desconhecida: {p.get('tecnica')}")
        self.semente = inteiro(p, "semente", 0, 0, 2**31 - 1)
        self.rng = random.Random(self.semente)
        self.escolhas: dict = {}
        campo = p.get("campo") if isinstance(p.get("campo"), dict) else {}
        try:
            x0, y0, x1, y1 = (int(round(float(v))) for v in (campo.get("caixa") or p.get("caixa")))
        except (TypeError, ValueError):
            raise ErroOperacao("técnica sem campo (caixa)")
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.W, x1), min(self.H, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise ErroOperacao("caixa do campo inválida")
        if p.get("recalcular_caixa"):  # campo já editado: a caixa da tinta mudou
            x0, y0, x1, y1 = ocr.caixa_tinta(img, (x0, y0, x1, y1), 0)
        self.caixa = (x0, y0, x1, y1)
        self.texto = str(campo.get("texto") or p.get("texto_antigo") or "").strip()
        self.categoria = campo.get("categoria") or p.get("categoria")
        self.tipo = campo.get("tipo") or p.get("tipo") or "texto"
        self.campo_id = campo.get("id")
        self.dado = str(p.get("texto") or "").strip()  # valor novo pedido pela pessoa (opcional)
        # hábitos do falsificador sorteado para a versão (rotulador.falsificador): a mesma fonte, o mesmo
        # retângulo, o mesmo retoque em todas as edições; "capricho" > 1 = mais desleixo
        self.habitos = p.get("habitos") if isinstance(p.get("habitos"), dict) else {}
        # o operador humano da versão (rotulador.humano): seleção, ferramenta, suavização, conta-gotas, retoques
        self.humano = self.habitos.get("humano") if isinstance(self.habitos.get("humano"), dict) else None
        self._hum_txt = self._hum_cor = None
        self.chave = documento.doc if documento is not None else chave_cache
        self.acabamento = p.get("acabamento") if isinstance(p.get("acabamento"), dict) else {}
        # quem forneceu cada peça da edição (apagador, rasterizador, fonte, grão): vai para o rótulo, para o
        # treino poder deixar um fornecedor de fora e medir se o detector decorou a ferramenta
        self.fornecedores: dict = {}
        self._aj = self._gs = self._desf = self._base = self._vestigio = self._tinta = None
        # só as 10 fontes mais usadas em documentos (técnica "mesma fonte", ou ``fontes: "top10"`` nos parâmetros)
        self.fontes = identificar_fonte.fontes_top10() if (p.get("fontes") == "top10" or (self.tec and self.tec.id in _TOP10)) else None

    # ------------------------------------------------------------------ opções
    def opcao(self, nome: str):
        spec = self.tec.opcoes.get(nome)
        if spec is None:
            return None
        validos = [v for v, _ in spec["valores"]]
        v = (self.p.get("opcoes") or {}).get(nome, "aleatorio")
        if v not in validos:
            v = self.rng.choice(validos)
        self.escolhas[nome] = v
        return v

    # ------------------------------------------------------------------ fonte, glifos, linha de base
    @property
    def aj(self) -> dict:
        if self._aj is None:
            if not self.texto:
                raise ErroOperacao("o campo não tem texto lido")
            origem = getattr(self.documento, "origem", None)
            caixa_limpa = (getattr(self.documento, "caixas_origem", None) or {}).get(self.campo_id)
            if origem is not None and caixa_limpa is not None:
                self._aj = self._aj_derivado(origem, caixa_limpa)
            else:
                prior = identificar_fonte.votos_contexto(self.documento) if self.documento is not None else None
                self._aj = auto.ajustar_fonte_cache(self.img, self.caixa, self.texto, self.chave, prior, self.fontes)
        return self._aj

    def _aj_derivado(self, origem, caixa_limpa) -> dict:
        """Base que é uma captura (foto/scan) do documento: a fonte, o negrito e a largura vêm do documento
        LIMPO (numa foto binarizada a negrito vira regular); tamanho, posição e cor são medidos aqui."""
        try:
            base = auto.ajustar_fonte_cache(origem.img, caixa_limpa, self.texto, origem.doc,
                                            identificar_fonte.votos_contexto(origem), self.fontes)
        except ErroOperacao:
            return auto.ajustar_fonte_cache(self.img, self.caixa, self.texto, self.chave, None, self.fontes)
        caixa_tinta = ocr.caixa_tinta(self.img, self.caixa, 0)
        esc = max(1, caixa_tinta[3] - caixa_tinta[1]) / max(1.0, float(caixa_limpa[3] - caixa_limpa[1]))
        tamanho, negrito = float(base["tamanho"]) * esc, float(base.get("negrito", 0)) * esc
        largura = float(base.get("largura", 100)) / 100.0
        x, y = auto.ancora(base["fonte"], self.texto, tamanho, largura, negrito, caixa_tinta)
        x0, y0, x1, y1 = caixa_tinta
        tinta = mascara_tinta(self.img[y0:y1, x0:x1]).astype(bool)
        cor = glifos.cor_tinta(self.img, tinta, caixa_tinta) if tinta.any() else np.zeros(3, np.float32)
        return {**base, "tamanho": round(tamanho, 2), "negrito": round(negrito, 2), "x": float(x), "y": float(y),
                "cor": "#%02x%02x%02x" % tuple(int(round(float(v))) for v in cor), "metodo": "documento limpo"}

    @property
    def glifos(self) -> list[glifos.Glifo]:
        if self._gs is None:
            # com o acento, o til e a cedilha soltos (glifos.com_sinais): a técnica que apaga ou move a letra leva o
            # sinal junto ("ARAÚJO" → "SIMOES" deixava o agudo: "SIMÓES")
            self._gs = glifos.com_sinais(self.img, glifos.segmentar(self.img, self.caixa, self.texto, self.aj["fonte"]))
        return self._gs

    @property
    def linha_base(self) -> float:
        """y do pé das letras (sem descendentes) — a linha de base real do campo."""
        if self._base is None:
            try:
                pes = [g.y1 for g in self.glifos if g.ch.isalnum() and g.ch not in glifos.DESCENDENTES
                   and glifos.decompor(g.ch)[1] != glifos.CEDILHA]
            except ErroOperacao:
                pes = []
            self._base = float(np.median(pes)) if pes else float(self.caixa[3])
        return self._base

    def params_fonte(self, s: str, x: float, y: float, desfoque: float | None = None) -> dict:
        aj = self.aj
        return {"texto": s, "fonte": aj["fonte"], "tamanho": aj["tamanho"], "largura": aj.get("largura", 100),
                "negrito": aj.get("negrito", 0), "rotacao": aj.get("rotacao", 0),
                "espacamento": aj.get("espacamento", 0), "x": x, "y": y,
                "desfoque": self.desfoque if desfoque is None else desfoque}

    @property
    def desfoque(self) -> float:
        if self._desf is None:
            try:
                *_, a = texto.renderizar(self.params_fonte(self.texto, self.aj["x"], self.aj["y"], 0), self.H, self.W)
                self._desf = glifos.estimar_desfoque(self.img, self.caixa, a)
            except ErroOperacao:
                self._desf = 0.0
        return self._desf

    def medir(self, s: str) -> tuple[float, float, float, float]:
        """Caixa da tinta nítida (alfa > 0,5) de ``s`` desenhado com âncora em (0, 0)."""
        P = 10000.0
        y0, y1, x0, x1, a = texto.renderizar(self.params_fonte(s, P, P, 0), 10**5, 10**5)
        m = a > 0.5
        if not m.any():
            m = a > 0
        ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
        return x0 + xs[0] - P, y0 + ys[0] - P, x0 + xs[-1] + 1 - P, y0 + ys[-1] + 1 - P

    def ancora(self, g: glifos.Glifo) -> float:
        """x da âncora (início da célula) do glifo ``g`` na fonte do campo."""
        return g.x0 - self.medir(g.ch)[0]

    @property
    def hum_texto(self) -> dict | None:
        """Como o operador humano ajusta o texto novo neste campo (``humano.texto``), ou None."""
        if self.humano is None:
            return None
        if self._hum_txt is None:
            self._hum_txt = humano.texto(self.humano, random.Random(self.semente ^ 0x4A11), self.desfoque,
                                         float(self.caixa[3] - self.caixa[1]))
            self.fornecedores.update(texto=self._hum_txt["rasterizador"], nitidez=self.humano.get("nitidez"))
        return self._hum_txt

    def cor_humana(self) -> np.ndarray | None:
        """A cor que o conta-gotas do operador humano pegou na tinta antiga do campo (uma vez por edição)."""
        if self.humano is None:
            return None
        if self._hum_cor is None:
            x0, y0, x1, y1 = self.caixa
            rec = self.img[max(0, y0 - 3):y1 + 3, max(0, x0 - 3):x1 + 3]
            cor = humano.cor_conta_gotas(rec, ocr.tinta_da_linha(rec), self.humano,
                                         random.Random(self.semente ^ 0xC0B))
            self._hum_cor = False if cor is None else np.asarray(cor, np.float32)
            self.fornecedores["cor"] = self.humano.get("cor") if cor is not None else "medida"
        return None if self._hum_cor is False else self._hum_cor

    def desenhar(self, s: str, esquerda=None, centro=None, direita=None, ancora=None, humano_: bool = False):
        """(caixa, alfa) de ``s`` na fonte do campo com o pé na linha de base do campo. ``humano_``: o texto NOVO
        posto pelo operador humano (escala no olho, camada no pixel inteiro ± 1 px, a suavização e o desfoque dele)."""
        dx0, _, dx1, _ = self.medir(s)
        ref = "".join(ch for ch in s if ch.isalnum() and ch not in glifos.DESCENDENTES) or s
        dyb = self.medir(ref)[3]
        if ancora is not None:
            X = ancora
        elif esquerda is not None:
            X = esquerda - dx0
        elif centro is not None:
            X = centro - (dx0 + dx1) / 2
        else:
            X = direita - dx1
        t = self.hum_texto if humano_ else None
        if t is None:
            y0, y1, x0, x1, a = texto.renderizar(self.params_fonte(s, X, self.linha_base - dyb), self.H, self.W)
            return (x0, y0, x1, y1), a
        p = self.params_fonte(s, float(round(X) + t["dx"]), float(round(self.linha_base - dyb) + t["dy"]), t["desfoque"])
        p.update(tamanho=p["tamanho"] * t["escala"], rasterizador=t["rasterizador"])
        y0, y1, x0, x1, a = texto.renderizar(p, self.H, self.W)
        return (x0, y0, x1, y1), a

    # ------------------------------------------------------------------ escolha de letras
    def alvo(self) -> str:
        """Classe de caractere a editar. Em campo numérico (valor, data, CPF...) só dígito; no sorteio,
        dígito é o mais comum (é o que se frauda) e só entra o que o campo tem."""
        if self.categoria in catalogo.NUMERICAS:
            v = "digito"
        else:
            pedido = (self.p.get("opcoes") or {}).get("alvo", "aleatorio")
            if pedido in ("digito", "letra", "qualquer"):
                v = pedido
            else:
                tem_d, tem_l = any(ch.isdigit() for ch in self.texto), any(ch.isalpha() for ch in self.texto)
                opcoes = ([("digito", 0.7)] if tem_d else []) + [("qualquer", 0.2)] + ([("letra", 0.1)] if tem_l else [])
                v = self.rng.choices([o for o, _ in opcoes], weights=[w for _, w in opcoes])[0]
                # no sorteio, só dígito (ver LETRA_PLAUSIVEL); letra só quando pedida (opção "alvo")
                if v != "digito" and self.categoria not in LETRA_PLAUSIVEL:
                    if not tem_d:
                        raise ErroOperacao("trocar uma letra aqui viraria erro de grafia (peça 'só letras' para forçar)")
                    v = "digito"
        if "alvo" in self.tec.opcoes:
            self.escolhas["alvo"] = v
        return v

    def alvos(self, alvo: str) -> list[glifos.Glifo]:
        """Glifos que podem ser editados, em ordem sorteada (ou só o pedido em p["indice"])."""
        gs = self.glifos
        if self.categoria in catalogo.NUMERICAS:
            alvo = "digito"
        if self.p.get("indice") not in (None, ""):
            escolhido = [g for g in gs if g.i == int(self.p["indice"])]
            if not escolhido:
                raise ErroOperacao("índice de caractere inválido")
            return escolhido

        def serve(g):
            k = _classe(g.ch)
            return k == alvo if alvo in ("digito", "letra") else k in ("digito", "letra")

        fonte, negrito = self.aj["fonte"], self.aj.get("negrito", 0)
        # dígitos têm todos a mesma altura: um bem menor é expoente (m³) ou sujeira lida como dígito
        alturas = [g.altura for g in gs if g.ch.isdigit()]
        ref = float(np.median(alturas)) if alturas else 0.0
        cands = [g for g in gs if serve(g) and glifos.semelhanca(g.tinta, g.ch, fonte, negrito) >= 0.35
                 and not (g.ch.isdigit() and g.altura < 0.8 * ref)]
        self.rng.shuffle(cands)
        if not cands:
            raise ErroOperacao({"digito": "o campo não tem dígito editável", "letra": "o campo não tem letra editável"}
                               .get(alvo, "o campo não tem caractere editável"))
        return cands

    def substitutos(self, ch: str) -> list[str]:
        """Caracteres que podem ocupar o lugar de ``ch`` (mesma classe e largura parecida), sorteados."""
        dado = str(self.p.get("novo_caractere") or "")
        if dado:
            return [dado[0]]
        if ch.isdigit():
            out = [d for d in "0123456789" if d != ch]
        elif ch.isalpha():  # vogal por vogal, consoante por consoante, de largura parecida
            f = glifos.fonte_pil(self.aj["fonte"])
            larg = f.getlength(ch)
            letras = "AEIOU" if ch.upper() in "AEIOU" else "BCDFGHJKLMNPQRSTVXZ"
            letras = letras if ch.isupper() else letras.lower()
            out = [l for l in letras if l != ch and abs(f.getlength(l) - larg) <= 0.15 * larg]
        else:
            out = []
        self.rng.shuffle(out)
        return out

    # ------------------------------------------------------------------ fundo
    def apagador(self) -> str:
        """Quem reconstrói o papel: a opção "apagar" da técnica (o hábito do falsificador entra por ela),
        PatchMatch quando a técnica não tem a opção."""
        modo = self.opcao("apagar") if "apagar" in self.tec.opcoes else "patchmatch"
        self.fornecedores["apagar"] = modo
        return modo

    def vestigio(self) -> float:
        """Fração do texto antigo que SOBRA depois de apagar (apagado malfeito, com fantasma): 0 = limpo.
        Pela opção da técnica (o hábito do falsificador entra por ela); no sorteio clássico, 15% das vezes."""
        if self._vestigio is None:
            v = "nenhum"
            if "vestigio" in self.tec.opcoes:
                v = (self.p.get("opcoes") or {}).get("vestigio")
                if v not in ("nenhum", "leve"):
                    v = "leve" if self.rng.random() < 0.15 else "nenhum"
                self.escolhas["vestigio"] = v
            self._vestigio = float(self.rng.uniform(0.08, 0.2)) if v == "leve" else 0.0
            if self._vestigio:
                self.fornecedores["vestigio"] = round(self._vestigio, 3)
        return self._vestigio

    def papel(self, regiao, furo: np.ndarray) -> np.ndarray:
        """Recorte da ``regiao`` (x0, y0, x1, y1) com o ``furo`` reconstruído só com papel."""
        x0, y0, x1, y1 = regiao
        h = max(8, self.caixa[3] - self.caixa[1])
        modo = self.apagador()
        M = int(max(16, (3.0 if modo in ("carimbo", "recuperar") else 1.5) * h))  # o carimbo precisa de papel livre
        ax0, ay0, ax1, ay1 = max(0, x0 - M), max(0, y0 - M), min(self.W, x1 + M), min(self.H, y1 + M)
        rec = self.img[ay0:ay1, ax0:ax1]
        buraco = _dilatar(mascara_tinta(rec), 2)
        alvo = np.zeros(buraco.shape, bool)
        alvo[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = furo
        buraco |= alvo
        if buraco.all():
            raise ErroOperacao("não há papel em volta para reconstruir o fundo")
        cheio = reconstruir(rec, buraco, modo, self.semente, float(h), alvo=alvo)
        return cheio[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0]

    def livre(self, caixa) -> bool:
        """Não há tinta na caixa (dá para escrever ali sem cobrir nada)."""
        x0, y0, x1, y1 = (int(round(v)) for v in caixa)
        if x0 < 0 or y0 < 0 or x1 > self.W or y1 > self.H:
            return False
        h = max(4, self.caixa[3] - self.caixa[1])
        ax0, ax1 = max(0, x0 - h), min(self.W, x1 + h)
        rec = self.img[y0:y1, ax0:ax1]
        if rec.size == 0:
            return False
        return not mascara_tinta(rec)[:, x0 - ax0:x1 - ax0].any()

    # ------------------------------------------------------------------ rótulo
    def meta(self, novo: str, alteracoes: list, descricao: str, **extra) -> dict:
        t = self.tec
        return {"rotulo": t.nome, "tecnica": t.id, "tecnica_num": t.num, "familia": t.familia, "nivel": t.nivel,
                "granularidade": t.granularidade, "literatura": t.literatura, "categoria": self.categoria,
                "campo": self.campo_id, "texto_antigo": self.texto, "texto_novo": novo, "alteracoes": alteracoes,
                "descricao": descricao, "opcoes": dict(self.escolhas), "semente": self.semente,
                "fornecedores": dict(self.fornecedores), **extra}


# ============================================================================ tela de trabalho
_BORRACHA_PEQUENA = frozenset({"caractere.raspar_tracos"})  # apaga só o traço, mesmo com o operador humano


class _Tela:
    """Recorte editável da imagem: apaga (com papel), compõe tinta, cola, e vira um Resultado."""

    def __init__(self, c: _C, caixa):
        x0, y0, x1, y1 = caixa
        x0, y0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
        x1, y1 = min(c.W, int(math.ceil(x1))), min(c.H, int(math.ceil(y1)))
        if x1 <= x0 or y1 <= y0:
            raise ErroOperacao("a edição caiu fora da imagem")
        self.c, self.caixa = c, (x0, y0, x1, y1)
        self.px = c.img[y0:y1, x0:x1].astype(np.float32)
        self.pegada = np.zeros((y1 - y0, x1 - x0), bool)

    def _local(self, caixa, forma):
        x0, y0, x1, y1 = (int(v) for v in caixa)
        X0, Y0, X1, Y1 = self.caixa
        ix0, iy0, ix1, iy1 = max(x0, X0), max(y0, Y0), min(x1, X1), min(y1, Y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return None
        return ((slice(iy0 - Y0, iy1 - Y0), slice(ix0 - X0, ix1 - X0)),
                (slice(iy0 - y0, iy1 - y0), slice(ix0 - x0, ix1 - x0)))

    def mascara(self, caixa, m: np.ndarray) -> np.ndarray:
        out = np.zeros(self.pegada.shape, bool)
        r = self._local(caixa, m.shape)
        if r:
            out[r[0]] = m[r[1]]
        return out

    def apagar(self, furo: np.ndarray) -> None:
        if not furo.any():
            return
        h = self.c.humano
        # a pessoa seleciona em volta (sem pegar a letra vizinha); raspar um traço de um dígito é borracha pequena:
        # com o letreiro em volta, o dígito inteiro saía junto
        if h is not None and h.get("selecao") != "tinta" and self.c.tec.id not in _BORRACHA_PEQUENA:
            x0, y0, x1, y1 = self.caixa
            tinta = mascara_tinta(self.c.img[y0:y1, x0:x1]).astype(bool)
            furo = humano.selecionar(furo, tinta, h["selecao"], h.get("margem") or (0.15,) * 4,
                                     float(self.c.caixa[3] - self.c.caixa[1]),
                                     random.Random(self.c.semente ^ 0x5E1), estrito=True)
            self.c.fornecedores["selecao"] = h["selecao"]
        fundo = self.c.papel(self.caixa, furo).astype(np.float32)
        v = self.c.vestigio()
        if v > 0:  # apagado malfeito: sobra um fantasma fraco da tinta antiga
            self.px[furo] = fundo[furo] * (1 - v) + self.px[furo] * v
        else:
            self.px[furo] = fundo[furo]
        self.pegada |= furo

    def tinta(self, caixa, alfa: np.ndarray, cor) -> None:
        """Compõe ``cor`` (3,) ou imagem (h, w, 3) com ``alfa`` (h, w) — ou (h, w, 3), um alfa por canal, como
        o ClearType —, ambos posicionados em ``caixa``."""
        r = self._local(caixa, alfa.shape[:2])
        if not r:
            return
        a = alfa[r[1]].astype(np.float32)
        a = a[..., None] if a.ndim == 2 else a
        frente = np.asarray(cor, np.float32)
        if frente.ndim == 3:
            frente = frente[r[1]]
        self.px[r[0]] = self.px[r[0]] * (1 - a) + frente * a
        self.pegada[r[0]] |= a.max(-1) > 0

    def opaco(self, caixa, rec: np.ndarray) -> None:
        r = self._local(caixa, rec.shape[:2])
        if r:
            self.px[r[0]] = np.asarray(rec, np.float32)[r[1]]
            self.pegada[r[0]] = True

    def poisson(self, caixa, rec: np.ndarray, m: np.ndarray) -> None:
        r = self._local(caixa, m.shape)
        if not r:
            return
        destino = np.clip(self.px + 0.5, 0, 255).astype(np.uint8)
        fonte = destino.copy()
        fonte[r[0]] = np.clip(np.asarray(rec, np.float32)[r[1]] + 0.5, 0, 255).astype(np.uint8)
        mascara = np.zeros(self.pegada.shape, bool)
        mascara[r[0]] = m[r[1]]
        self.px = poisson(destino, fonte, mascara).astype(np.float32)
        self.pegada |= mascara

    def resultado(self, meta: dict) -> Resultado:
        x0, y0, x1, y1 = self.caixa
        if not self.pegada.any():
            raise ErroOperacao("a técnica não mexeu em nada")
        novo = np.clip(self.px + 0.5, 0, 255).astype(np.uint8)
        return Resultado(y0, y1, x0, x1, novo, self.pegada.copy(), meta=meta)


def _posicionar(rec: np.ndarray, a: np.ndarray, X: float, Y: float, inteiro: bool = False):
    """Translada (rec, alfa) para o canto (X, Y) com precisão subpixel: (caixa, rec, alfa). ``inteiro``: no pixel
    inteiro mais perto, sem reamostrar — como cola o Paint/Photoshop (Ctrl+V e arrastar). A interpolação bilinear
    deixava o recorte mais macio que as letras vizinhas, que nunca foram reamostradas."""
    if inteiro:
        X, Y = float(round(X)), float(round(Y))
    ix, iy = math.floor(X), math.floor(Y)
    fx, fy = X - ix, Y - iy
    h, w = a.shape
    if fx > 1e-3 or fy > 1e-3:
        M = np.float32([[1, 0, fx], [0, 1, fy]])
        rec = cv2.warpAffine(np.asarray(rec, np.float32), M, (w + 1, h + 1), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
        a = cv2.warpAffine(a.astype(np.float32), M, (w + 1, h + 1), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return (ix, iy, ix + a.shape[1], iy + a.shape[0]), rec, a


def _glifo_movel(img_src: np.ndarray, g: glifos.Glifo, escala: float = 1.0, cor_alvo=None, escala_x: float = 1.0):
    """(rec, alfa, caixa_da_tinta_no_rec) de um glifo, reescalado e com a cor da tinta trocada.
    Tinta de OUTRA letra que caiu na margem do recorte vira papel (no modo retângulo ela apareceria).
    ``escala_x``: esticamento horizontal a mais (quem cola no Paint arrasta o canto e deforma a letra)."""
    rec, a, (sx0, sy0, _sx1, _sy1) = glifos.alfa_glifo(img_src, g, margem=3)
    rec = rec.astype(np.float32)
    dono = np.zeros(a.shape, np.uint8)
    dono[g.y0 - sy0:g.y1 - sy0, g.x0 - sx0:g.x1 - sx0] = g.tinta
    alheia = mascara_tinta(rec.astype(np.uint8)).astype(bool) & ~_dilatar(dono.astype(bool), 2)
    if alheia.any():
        rec[_dilatar(alheia, 1)] = glifos.cor_papel(img_src, g.caixa)
    if cor_alvo is not None:
        rec = rec + (np.asarray(cor_alvo, np.float32) - glifos.cor_tinta(img_src, g)) * a[..., None]
    gx0, gy0, gx1, gy1 = g.x0 - sx0, g.y0 - sy0, g.x1 - sx0, g.y1 - sy0
    ex = escala * escala_x
    if abs(escala - 1) > 0.02 or abs(ex - 1) > 0.02:
        h, w = a.shape
        nw, nh = max(1, int(round(w * ex))), max(1, int(round(h * escala)))
        rec = cv2.resize(rec, (nw, nh), interpolation=cv2.INTER_CUBIC)
        a = np.clip(cv2.resize(a, (nw, nh), interpolation=cv2.INTER_LINEAR), 0, 1)
        gx0, gy0, gx1, gy1 = gx0 * ex, gy0 * escala, gx1 * ex, gy1 * escala
    return rec, a, (gx0, gy0, gx1, gy1)


def _colar(tela: _Tela, caixa, rec, a, modo: str) -> None:
    if modo == "retangulo":
        tela.opaco(caixa, rec)
    elif modo == "papel":  # recorte de papel colado por cima e fotografado: opaco, borda escura e sombra em dois lados
        tela.opaco(caixa, rec)
        r = tela._local(caixa, np.asarray(rec).shape[:2])
        if r:
            ys, xs = r[0]
            sub = tela.px[ys, xs]
            borda = np.zeros(sub.shape[:2], bool)
            borda[[0, -1], :] = True
            borda[:, [0, -1]] = True
            sub[borda] *= 0.86
            tela.px[ys, xs] = sub
            X0, Y0 = tela.caixa[0], tela.caixa[1]
            for dy, dx, f in ((1, 1, 0.80), (2, 2, 0.90)):  # sombra: embaixo e à direita
                y0s, x0s = ys.start + dy, xs.start + dx
                y1s, x1s = min(tela.px.shape[0], ys.stop + dy), min(tela.px.shape[1], xs.stop + dx)
                if y1s > y0s and x1s > x0s:
                    faixa = np.zeros(tela.px.shape[:2], bool)
                    faixa[y0s:y1s, x0s:x1s] = True
                    faixa[ys, xs] = False
                    tela.px[faixa] *= f
                    tela.pegada |= faixa
            _ = (X0, Y0)
    elif modo == "poisson":
        tela.poisson(caixa, rec, _dilatar(a > 0.05, 2))
    else:
        tela.tinta(caixa, a, rec)


def _furo_glifos(tela: _Tela, gs) -> np.ndarray:
    furo = np.zeros(tela.pegada.shape, bool)
    for g in gs:
        furo |= tela.mascara(g.caixa, g.tinta)
    try:  # texto borrado (foto): o halo da letra passa de 2 px e sobraria um fantasma
        extra = 1 if tela.c.desfoque >= 1.0 else 0
    except ErroOperacao:
        extra = 0
    return _dilatar(furo, 2 + extra)


def _cor_nova(c: _C, ref: glifos.Glifo) -> np.ndarray:
    """Cor da tinta do traço novo: a que, desenhada na fonte do campo com o desfoque do scan, dá o núcleo que as
    letras do campo têm (glifos.calibrar_cor, mediana de até 5 letras). Com a cor medida direto (``cor_tinta``) a
    letra nova saía bem mais clara que as vizinhas (núcleo com luma 128 x 92 num Times escaneado): o traço
    desfocado não chega a alfa 1. Cada letra nova sai tão clara ou escura quanto a espessura do traço dela pede,
    como no scan. Sem letra que dê para desenhar, a cor de ``ref``."""
    if c._tinta is None:
        papel = glifos.cor_papel(c.img, c.caixa)
        letras = [g for g in c.glifos if g.ch.isalnum()]
        cores = []
        for g in letras[::max(1, len(letras) // 5)][:5]:
            try:
                _, a = c.desenhar(g.ch, ancora=c.ancora(g))
            except ErroOperacao:
                continue
            cores.append(glifos.calibrar_cor(a, papel, glifos.cor_tinta(c.img, g)))
        c._tinta = np.median(cores, axis=0) if cores else False
    return c._tinta if c._tinta is not False else glifos.cor_tinta(c.img, ref)


def _cor_escrita(c: _C, ref: glifos.Glifo) -> np.ndarray:
    """A cor do texto novo: a do conta-gotas do operador humano, ou a calibrada (``_cor_nova``)."""
    cor = c.cor_humana()
    return cor if cor is not None else _cor_nova(c, ref)


def _caixa_glifo(g: glifos.Glifo, m: int = 3):
    return g.x0 - m, g.y0 - m, g.x1 + m, g.y1 + m


# ============================================================================ técnicas: imitação
def _largura(c: _C, s: str) -> float:
    dx0, _, dx1, _ = c.medir(s)
    return dx1 - dx0


def _novo_valor(c: _C, maximo: float = 1.25, largura=None) -> str:
    """Valor falso no mesmo formato e com largura parecida com a do antigo: um nome muito mais comprido passaria
    por cima do rótulo ou sairia da página, e um muito mais curto deixava espaço em branco (bairros com 0,78 da
    largura). Entre até 60 candidatos fica o de largura mais perto da antiga (distância log: em [0,85; 1,15]
    quando houver), nunca acima de ``maximo`` (quem frauda escolhe o que cabe). Original curto ('QD 12 LT 5')
    não é mais descartado por não ter candidato em [0,7; máximo]: fica o mais perto que houver. ``largura``: a régua
    (texto -> px); sem ela, o texto desenhado na fonte do campo."""
    if c.dado:
        return c.dado
    medir = largura or (lambda s: _largura(c, s))
    try:
        antiga = medir(c.texto)
    except ErroOperacao:
        antiga = None
    melhor, vistos = None, set()
    for _ in range(60):
        novo = geradores.parecido(c.categoria, c.texto)
        if not novo or novo == c.texto or novo in vistos:
            continue
        vistos.add(novo)
        if not antiga:
            return novo
        r = medir(novo) / antiga
        if r > maximo:
            continue
        if melhor is None or abs(math.log(r)) < melhor[0]:
            melhor = (abs(math.log(r)), novo)
        if melhor[0] <= 0.03:  # já é praticamente a mesma largura
            break
    if melhor is None:
        raise ErroOperacao("nenhum valor falso plausível cabe no espaço deste campo" if vistos else
                           "não consegui gerar um valor novo para este campo")
    return melhor[1]


def _papel_livre(c: _C, lado: str, limite: int, folga: int) -> int:
    """Quantos px de papel sem tinta há do ``lado`` ("esquerda"/"direita") do campo, até ``limite``, deixando
    ``folga`` px (um espaço da fonte) antes da tinta vizinha (o CPF logo depois do nome)."""
    x0, y0, x1, y1 = c.caixa
    h = max(4, y1 - y0)
    a0, a1 = (x1 + 1, min(c.W, x1 + 1 + limite)) if lado == "direita" else (max(0, x0 - 1 - limite), x0 - 1)
    if a1 <= a0:
        return 0
    b0, b1 = max(0, a0 - h), min(c.W, a1 + h)  # a máscara de tinta precisa de papel em volta para achar o papel
    cols = np.flatnonzero(mascara_tinta(c.img[y0:y1, b0:b1])[:, a0 - b0:a1 - b0].any(0))
    if not cols.size:
        return a1 - a0
    return max(0, int(cols[0] if lado == "direita" else (a1 - a0) - 1 - cols[-1]) - folga)


def _condensar(c: _C, novo: str, fonte_params: dict, espacamento: float, alin: str, nova: float,
               antiga: float, folga: int) -> float:
    """Espaçamento que faz ``novo`` (largura ``nova``) caber no campo mais o papel livre do lado para onde ele
    cresce (sem chegar a menos de ``folga`` da tinta vizinha): o valor é imposto (o mesmo nome repetido no
    documento), então se condensa até 8% — nunca por cima do vizinho. Não cabendo, ErroOperacao."""
    limite = int(math.ceil(nova - antiga)) + folga + 4
    if alin == "centro":
        livre = 2 * min(_papel_livre(c, "esquerda", limite, folga), _papel_livre(c, "direita", limite, folga))
    else:
        livre = _papel_livre(c, "direita" if alin == "esquerda" else "esquerda", limite, folga)
    falta = nova - (antiga + livre)
    if falta <= 0:
        return espacamento
    if falta > 0.08 * nova or len(novo) < 2:
        raise ErroOperacao(f"'{novo}' não cabe no espaço do campo: precisaria condensar {100 * falta / nova:.0f}% "
                           f"(até 8%) ou passar por cima do que está ao lado")
    return round(espacamento - falta / (len(novo) - 1) / (float(fonte_params.get("largura", 100)) / 100.0), 3)


# Fontes que os editores de PDF oferecem (Acrobat sem a fonte embutida cai no Adobe Sans/Serif MM; os editores
# on-line — iLovePDF, Smallpdf, Sejda, PDFescape — têm só estas). O texto novo sai nelas, na mais parecida.
_SUBSTITUTAS = ("Arial", "Helvetica", "Times New Roman", "Courier New", "Verdana")


def _fonte_substituta(c: _C) -> str | None:
    disponiveis = [f for f in _SUBSTITUTAS if f in texto.listar_fontes()]
    if not disponiveis:
        return None
    try:
        r = identificar_fonte.identificar(c.img, c.caixa, c.texto, top=60, negrito=False)
    except ErroOperacao:
        return disponiveis[0]
    for cand in r["ranking"]:
        for nome in [cand["fonte"], *cand["parentes"]]:
            if nome in disponiveis:
                return nome
    return disponiveis[0]


def _t_campo_reescrever(c: _C, extra_p: dict | None = None) -> Resultado:
    """``extra_p``: parâmetros a mais para ``auto.trocar_campo`` que não são opções do catálogo (a mesma fonte
    passa o apagador só-tinta, o fundo local e o alinhamento lido no documento)."""
    extra_p = dict(extra_p or {})
    novo = _novo_valor(c)
    alin = extra_p.pop("alinhar", None) or c.opcao("alinhamento")
    c.escolhas["alinhamento"] = alin
    fonte_editor = c.opcao("fonte_editor")
    fonte_params = dict(c.aj)  # a identificação de ``aj`` (no documento limpo, quando a base é uma captura)
    if fonte_editor == "substituta":
        sub = _fonte_substituta(c)
        if sub and sub != c.aj["fonte"]:
            fonte_params = {**c.aj, "fonte": sub, "negrito": 0}
        else:
            fonte_editor = "imitada"
    x0, y0, x1, y1 = c.caixa
    # desleixo (capricho > 1): a linha de base escorrega e o espaçamento entre letras muda um pouco
    k = float(c.habitos.get("capricho", 1.0))
    desleixo = ""
    espacamento = 0.0
    if k > 1.0:
        dy = c.rng.uniform(-0.06, 0.06) * (c.caixa[3] - c.caixa[1]) * k
        espacamento = round(c.rng.uniform(-0.02, 0.06) * c.aj["tamanho"] * (k - 0.8), 2)
        fonte_params = {**(fonte_params or c.aj), "y": (fonte_params or c.aj)["y"] + dy}
        desleixo = f"; linha de base {dy:+.1f} px e espaçamento {espacamento:+.1f} px (desleixo)"
    antiga, nova = _largura(c, c.texto), _largura(c, novo)
    extra = int(math.ceil(nova - antiga))
    condensado = ""
    if extra > 2:  # o texto novo é mais largo: só cresce para o lado com papel livre (e um espaço até o vizinho)
        f = max(4, int(round(glifos.fonte_pil(fonte_params["fonte"], fonte_params["tamanho"]).getlength(" ")
                             * float(fonte_params.get("largura", 100)) / 100)))
        meio = extra // 2 + 1
        cabe = {"esquerda": c.livre((x1 + 1, y0, x1 + extra + f, y1)),
                "direita": c.livre((x0 - extra - f, y0, x0 - 1, y1)),
                "centro": c.livre((x1 + 1, y0, x1 + meio + f, y1)) and c.livre((x0 - meio - f, y0, x0 - 1, y1))}
        if not cabe.get(alin):
            alin = next((a for a in ("esquerda", "direita") if cabe[a]), alin)
        if not cabe.get(alin):
            if c.dado:  # valor imposto (repetido no documento): condensa em vez de invadir a tinta vizinha
                espacamento = _condensar(c, novo, fonte_params, espacamento, alin, nova, antiga, f)
                condensado = f"; condensado ({espacamento:+.2f} px entre as letras) para caber"
            else:
                novo = _novo_valor(c, maximo=1.0)
        c.escolhas["alinhamento"] = alin
    # editor de PDF: o texto novo sai nítido (vetorial), sem o desfoque do scan do resto da página
    desf = 0.0 if c.habitos.get("nitido") else c.desfoque
    t = c.hum_texto
    if t is not None:  # o operador humano: o tamanho no olho, a camada no pixel, a suavização, o desfoque e a cor dele
        desf = t["desfoque"]
        fonte_params = {**fonte_params, "tamanho": round(float(fonte_params["tamanho"]) * t["escala"], 2)}
        extra_p.update(no_olho=[t["dx"], t["dy"]], rasterizador=t["rasterizador"])
        cor = c.cor_humana()
        if cor is not None:
            extra_p["cor"] = "#%02x%02x%02x" % tuple(int(round(float(v))) for v in np.clip(cor, 0, 255))
    apagar = extra_p.pop("apagar", None) or c.apagador()
    p = {"caixa": list(c.caixa), "texto": novo, "texto_antigo": c.texto, "tipo": "texto", "semente": c.semente,
         "alinhar": alin, "igualar_ruido": False, "desfoque": desf, "espacamento": espacamento,
         "apagar": apagar, "fonte_params": fonte_params, **extra_p}
    res = auto.trocar_campo(c.img, p, selecao.vazia(c.H, c.W), base=c.base, chave_cache=c.chave)
    c.fornecedores["apagar"] = res.meta.get("apagar") or apagar  # o que o apagador de fato usou
    v = c.vestigio()
    if v > 0:  # apagado malfeito: a tinta antiga fica como um fantasma fraco debaixo do texto novo
        sub = c.img[res.y0:res.y1, res.x0:res.x1]
        antiga = _dilatar(mascara_tinta(sub), 1) & res.pegada
        rec = res.recorte.astype(np.float32)
        rec[antiga] = rec[antiga] * (1 - v) + sub[antiga].astype(np.float32) * v
        res.recorte = np.clip(rec + 0.5, 0, 255).astype(np.uint8)
    fonte_usada = fonte_params["fonte"]
    c.fornecedores.update(texto=t["rasterizador"] if t is not None else "freetype", fonte=fonte_usada)
    res.meta = {**res.meta, **c.meta(novo, [_alt(0, c.texto, novo, "campo")],
                                     f"Campo reescrito: '{c.texto}' → '{novo}' na fonte {fonte_usada} "
                                     f"(tamanho {c.aj['tamanho']}, desfoque {desf}), alinhado pela {alin}"
                                     f"{' (texto nítido, como sai de um editor de PDF)' if c.habitos.get('nitido') else ''}"
                                     f"{f'; fonte substituída pelo editor de PDF (a original era {c.aj[chr(102) + chr(111) + chr(110) + chr(116) + chr(101)]})' if fonte_editor == 'substituta' else ''}"
                                     f"{desleixo}{condensado}; apagado com {c.fornecedores['apagar']}.",
                                     fonte=fonte_usada, **({"condensado_px": espacamento} if condensado else {}))}
    return res


def _alinhamento_no_documento(c: _C) -> str | None:
    """Como o campo está alinhado na página: a borda esquerda da tinta cai na mesma coluna (± max(2 px, 0,15 da
    altura)) que a das linhas logo acima/abaixo → esquerda; a direita → direita; o centro → centro. Um nome
    centralizado num quadro, escrito pela esquerda, saía torto. None sem documento ou sem vizinha que confirme."""
    doc = c.documento
    if doc is None:
        return None
    x0, y0, x1, y1 = c.caixa
    h = max(1, y1 - y0)
    tol = max(2.0, 0.15 * h)
    caixas = [f["caixa"] for f in getattr(doc, "campos", None) or [] if f.get("tipo", "texto") == "texto"]
    caixas += [ocr.caixa_da_linha(doc.img, l["caixa"]) for l in getattr(doc, "linhas", None) or []
               if y0 - 4 * h <= l["caixa"][1] <= y1 + 4 * h]
    votos = {"esquerda": 0, "direita": 0, "centro": 0}
    for b in caixas:
        bx0, by0, bx1, by1 = (int(v) for v in b)
        mesma_linha = min(y1, by1) - max(y0, by0) > 0.5 * min(h, by1 - by0)
        perto = -0.3 * h <= max(by0 - y1, y0 - by1) <= 2.5 * h  # logo acima ou logo abaixo
        if mesma_linha or not perto or min(x1, bx1) <= max(x0, bx0):  # e na mesma coluna
            continue
        votos["esquerda"] += abs(bx0 - x0) <= tol
        votos["direita"] += abs(bx1 - x1) <= tol
        votos["centro"] += abs((bx0 + bx1) - (x0 + x1)) / 2 <= tol
    melhor = max(votos, key=lambda k: votos[k])  # empate: esquerda, direita, centro
    return melhor if votos[melhor] else None


def _t_campo_mesma_fonte(c: _C) -> Resultado:
    """O texto novo na MESMA fonte do campo, identificada entre as 10 mais usadas em documentos (``c.fontes``):
    o reescrever cuidadoso, sem os sorteios que estragam a imagem (fonte substituta, vestígio do texto antigo,
    apagador borrado, alinhamento trocado). Apaga SÓ a tinta da linha antiga (a pegada fica com a forma do texto e
    o papel em volta, intacto) e reconstrói o fundo local, que segue o gradiente de luz. O alinhamento é o que o
    campo tem na página (esquerda, direita ou centro, pelas linhas vizinhas); sem como saber, nome e endereço saem
    pela esquerda e valor e consumo pela direita. O rótulo diz se a fonte foi mesmo identificada (semelhança no
    limiar), só aproximada, ou estimada sem identificação; o campo é editado do mesmo jeito."""
    opcoes = c.p.get("opcoes") or {}
    alin = opcoes.get("alinhamento")
    if alin not in ("esquerda", "direita"):
        alin = (_alinhamento_no_documento(c)
                or ("direita" if c.categoria in ("valor", "consumo/leitura") else "esquerda"))
    extra_p = {"alinhar": alin, "so_tinta": True}
    if opcoes.get("apagar") not in ("patchmatch", "chapado", "telea", "ns", "borrao", "carimbo", "recuperar"):
        extra_p["apagar"] = "local"
    h = c.humano
    if h is not None:  # a pessoa seleciona o texto antigo (letreiro, laço, pincel ou intervalo de cores) e apaga
        extra_p = {"alinhar": alin, "selecao_humana": {"forma": h["selecao"], "margem": h.get("margem")},
                   "apagar": humano.APAGADOR.get(h.get("apagar"), "patchmatch")}
        c.fornecedores["selecao"] = h["selecao"]
    res = _t_campo_reescrever(c, extra_p)
    fonte, aj = c.aj["fonte"], c.aj
    px = res.meta.get("condensado_px")
    condensado = f"; condensado ({px:+.2f} px entre as letras) para caber" if px is not None else ""
    sem = aj.get("semelhanca")
    limiar = float(getattr(identificar_fonte, "LIMIAR_MESMA_FONTE", 0.75))
    if aj.get("metodo") == "estimativa":
        ident, como = "estimada", "fonte NÃO identificada: estimada pela tinta"
    elif sem is not None and float(sem) >= limiar:
        ident, como = "top10", f"identificada entre as 10 mais usadas em documentos, semelhança {float(sem):.2f}"
    else:
        ident = "aproximada"
        como = (f"a mais parecida entre as 10 mais usadas, semelhança {float(sem):.2f} abaixo do limiar {limiar:.2f}"
                if sem is not None else "a mais parecida entre as 10 mais usadas, sem medida de semelhança")
    res.meta["descricao"] = (f"Campo reescrito na mesma fonte: '{c.texto}' → '{res.meta.get('texto_novo')}' em {fonte} "
                             f"({como}; tamanho {aj['tamanho']}, alinhado pela {c.escolhas.get('alinhamento', alin)}"
                             f"{condensado}); apagado com {c.fornecedores.get('apagar')}.")
    res.meta["semelhanca_fonte"] = None if sem is None else round(float(sem), 3)
    c.fornecedores["fonte_identificada"] = ident
    return res


# ============================================================================ letras do próprio documento
_tinta_fonte: dict = {}


def _tinta_caractere(fonte: str, ch: str) -> tuple[float, float]:
    """(esquerda, direita) da TINTA de ``ch`` em relação ao início do avanço, tamanho 100 (o getbbox do Pillow dá a
    célula do avanço, não a tinta: na Courier New toda letra ia de 0 a 60)."""
    k = (fonte, ch)
    if k not in _tinta_fonte:
        m, desloc = auto._mascara_texto(fonte, ch, 100.0)
        _tinta_fonte[k] = (0.0, 1.0) if m is None else (float(desloc[0]), float(desloc[0]) + m.shape[1])
    return _tinta_fonte[k]


def _faixas_gabarito(aj: dict, s: str) -> dict[int, tuple[float, float]]:
    """Faixa (x0, x1) da tinta de cada caractere não branco de ``s`` na fonte do gabarito (``aj``: fonte, tamanho,
    largura, espaçamento e negrito medidos no campo), com a âncora em 0: avanço, kerning do par e espaço da fonte."""
    tam = float(aj["tamanho"])
    f = glifos.fonte_pil(aj["fonte"], tam)
    larg = float(aj.get("largura", 100) or 100) / 100.0
    esp = float(aj.get("espacamento") or 0.0)
    neg = float(aj.get("negrito") or 0.0) / 2
    out = {}
    for k, ch in enumerate(s):
        if ch.isspace():
            continue
        adv = f.getlength(s[:k + 1]) - f.getlength(ch) + k * esp  # com o kerning do par (s[k-1], s[k])
        l, r = _tinta_caractere(aj["fonte"], glifos.decompor(ch)[0])  # o corpo: o agudo do Í passa da haste
        out[k] = ((adv + l * tam / 100 - neg) * larg, (adv + r * tam / 100 + neg) * larg)
    return out


@dataclass(eq=False)
class _Doador:
    g: glifos.Glifo
    img: np.ndarray
    origem: str  # "campo" | "linha" (a mesma linha do campo) | "documento" (outra linha no mesmo estilo)
    dy: float  # pé da letra menos a linha de base dela (vírgula e descendentes descem; hífen fica acima)
    escala: float  # 1 = colada pixel a pixel; senão reamostrada (só se a altura difere mais de 3%)
    nota: float  # menor = melhor (origem, letra inteira, altura, cor)
    T: np.ndarray  # transmitância da tinta (cor / papel), por canal
    vizinhos: tuple = ()  # as letras ao lado na linha de origem: a tinta delas nunca vem junto


@dataclass(eq=False)
class _Peca:
    k: int  # posição no texto novo
    ch: str
    tipo: str  # "copiada" | "composta" (letra + sinal de outra letra) | "acento_desenhado" | "desenhada"
    doador: _Doador | None = None
    sinal: tuple | None = None  # (doador do sinal, máscara do sinal na caixa dele)
    largura: float = 0.0
    altura: float = 0.0
    dy: float = 0.0
    desenho: tuple | None = None  # (alfa, (x0, y0) do alfa e (x0, y0, x1, y1) da tinta, com a âncora em 0)
    dx_corpo: float = 0.0  # do canto da caixa da letra doada até o corpo dela (o acento pode passar do corpo)
    destino: tuple | None = None  # caixa da tinta da letra na imagem editada (para o rótulo)


class _NaoCabe(ErroOperacao):
    pass


_LUMA = np.float32([0.299, 0.587, 0.114])
# caracteres feitos de pedaços soltos empilhados: nada se tira deles
_VARIOS = frozenset(':;%"!?=$&\'')


def _corpo_x(g: glifos.Glifo) -> tuple[int, int]:
    """(x0, x1) do corpo da letra na imagem, sem acento nem cedilha."""
    corpo = glifos.partes(g.tinta)[0]
    xs = np.flatnonzero(corpo.any(0))
    return (g.x0 + int(xs[0]), g.x0 + int(xs[-1]) + 1) if xs.size else (g.x0, g.x1)


class _Letras:
    """As letras do documento no estilo do campo (doadores por caractere), o gabarito de posição e a montagem.

    O estilo é medido nas letras do próprio campo: tamanho (altura de cada letra na régua da fonte do gabarito),
    transmitância da tinta (cor ÷ papel local: a mesma tinta numa parte mais escura da foto continua a mesma),
    espessura do traço e nitidez da borda. Servem as letras do campo e as das linhas do documento nesse mesmo
    estilo: rótulo cinza, cabeçalho branco, negrito ou corpo menor ficam de fora."""

    TOL_ALTURA = 0.06  # altura da letra doadora contra a esperada para aquele caractere
    TOL_TAMANHO = 0.10  # tamanho da linha doadora contra o do campo (régua do gabarito)
    TOL_COR = 0.10  # transmitância da tinta, por canal
    TOL_PESO = 0.20  # espessura do traço
    TOL_NITIDEZ = 0.25

    def __init__(self, c: _C):
        self.c = c
        self.aj, self.entre = self._gabarito(c)
        self.fonte = self.aj["fonte"]
        self.rot = float(self.aj.get("rotacao") or 0.0)
        # as letras do campo, separadas pela métrica do gabarito; a das 10 e a da Arial se ela não separar (o I de
        # serifa da Tahoma num I sem serifa)
        self.gs = self._segmentar(c.img, c.caixa, c.texto, dict.fromkeys((self.fonte, c.aj["fonte"], None)))
        c._gs = self.gs
        if not self.gs:
            raise ErroOperacao("o campo não tem letras")
        doc = c.documento
        self.inv = doc.inventario() if doc is not None and hasattr(doc, "inventario") else None
        self.sigma = max(float(c.desfoque or 0.0), float(self.aj.get("desfoque_scan") or 0.0))
        self._med: dict = {}
        est = self._estilo(c.img, self.gs)
        if est is None:
            raise ErroOperacao("não deu para medir o estilo das letras do campo")
        self.est = est
        self.base = glifos.linha_de_base(self.gs, self.rot) or (float(c.caixa[3]), -math.tan(math.radians(self.rot)))
        self._lin = None
        self._cands: dict = {}
        self._sinais: dict = {}
        self._vaos = None
        self._neg = None
        self.aperto = 0.0  # px tirados de cada vão entre letras para o valor imposto caber
        self._cor = None

    @staticmethod
    def _gabarito(c: _C) -> tuple[dict, str]:
        """(ajuste, "todas" | "top10"): a fonte mais parecida entre TODAS as instaladas quando ela casa com a tinta
        (semelhança no limiar e acima da das 10 mais usadas: a própria Trebuchet, a Bahnschrift), senão a das 10
        (``c.aj``). Ela dá a posição das letras e desenha só as que o documento não tem."""
        aj = c.aj
        if getattr(c.documento, "origem", None) is not None or c.p.get("fonte_params"):
            return aj, "top10"
        if c.caixa[3] - c.caixa[1] < 18:  # letra miúda: entre centenas de fontes, alguma casa por acaso (Helvetica
            return aj, "top10"            # Rounded numa data de 13 px)
        try:
            prior = identificar_fonte.votos_contexto(c.documento) if c.documento is not None else None
            todas = auto.ajustar_fonte_cache(c.img, c.caixa, c.texto, c.chave, prior, None)
        except ErroOperacao:
            return aj, "top10"
        limiar = float(getattr(identificar_fonte, "LIMIAR_MESMA_FONTE", 0.9))
        s_t, s_10 = float(todas.get("semelhanca") or 0.0), float(aj.get("semelhanca") or 0.0)
        if todas.get("fonte") != aj.get("fonte") and s_t >= limiar and s_t >= s_10 + 0.02:
            return todas, "todas"
        return aj, "top10"

    # ------------------------------------------------------------------ medidas
    def _medir(self, img, g) -> dict:
        k = (id(img), g.caixa, g.ch)
        m = self._med.get(k)
        if m is None:
            _rec, a, _cx = glifos.alfa_glifo(img, g)
            papel = np.maximum(glifos.cor_papel(img, g.caixa), 1.0)
            m = {"T": np.clip(glifos.cor_tinta(img, g) / papel, 0, 1.2).astype(np.float32),
                 "peso": glifos.peso_traco(a), "nitidez": glifos.nitidez_alfa(a)}
            self._med[k] = m
        return m

    def _h_fonte(self, ch: str) -> float | None:
        m = glifos.mascara_caractere(ch, self.fonte)
        return None if m is None else float(m.shape[0])

    def _plana(self, g) -> bool:
        """A letra é o caractere que o OCR leu, sem sinal a mais (o OCR lê "JOAO" num "JOÃO")."""
        base, sinal = glifos.decompor(g.ch)
        if not base.isalpha():
            return True
        _corpo, acima, abaixo = glifos.partes(g.tinta)
        if sinal is not None:
            return bool(abaixo if sinal == glifos.CEDILHA else acima) or sinal == glifos.CEDILHA
        if base in "ij":
            return len(acima) <= 1 and not abaixo
        return not acima and not (abaixo and base in "Cc")

    def _estilo(self, img, gs) -> dict | None:
        letras = [g for g in gs if g.ch.isalnum() and self._plana(g) and self._h_fonte(g.ch)]
        if not letras:
            return None
        tam = float(np.median([g.altura / self._h_fonte(g.ch) for g in letras]))
        amostra = letras[::max(1, len(letras) // 10)][:12]
        ms = [self._medir(img, g) for g in amostra]
        return {"tam": tam, "T": np.median([m["T"] for m in ms], axis=0), "peso": float(np.median([m["peso"] for m in ms])),
                "nitidez": float(np.median([m["nitidez"] for m in ms])),
                "altura": float(np.median([g.altura for g in letras]))}

    def _mesmo_estilo(self, e: dict) -> bool:
        a = self.est
        if abs(e["tam"] / a["tam"] - 1) > self.TOL_TAMANHO or np.abs(e["T"] - a["T"]).max() > self.TOL_COR:
            return False
        for k, tol in (("peso", self.TOL_PESO), ("nitidez", self.TOL_NITIDEZ)):
            if a[k] > 0 and e[k] > 0 and abs(math.log(e[k] / a[k])) > math.log(1 + tol):
                return False
        return True

    @staticmethod
    def _segmentar(img, caixa, texto_: str, fontes) -> list:
        """glifos.segmentar sem os fios de tabela: o j que desce até o fio do quadro levava junto o pedaço do fio
        debaixo de cada letra da linha (e a altura de todas). Os fios viram papel só para separar as letras; a tinta
        copiada continua a da imagem. ``fontes``: as métricas tentadas em ordem. Margem de 0,45 da altura: o acento
        solto entra na letra (é por ele que se sabe que o "A" lido é um "Ã")."""
        H, W = img.shape[:2]
        x0, y0, x1, y1 = (int(v) for v in caixa)
        h = max(1, y1 - y0)
        rx0, ry0, rx1, ry1 = max(0, x0 - 2 * h), max(0, y0 - h), min(W, x1 + 2 * h), min(H, y1 + h)
        fios = texto._fios_da_tabela(img, y0, y1, x0, x1)[ry0:ry1, rx0:rx1]
        rec, dx, dy = img, 0, 0
        if fios.any():
            rec = img[ry0:ry1, rx0:rx1].copy()
            rec[fios] = np.round(glifos.cor_papel(img, caixa)).astype(np.uint8)
            dx, dy = rx0, ry0
        erro = ErroOperacao("não deu para separar as letras")
        for fonte in fontes:
            try:
                gs = glifos.segmentar(rec, (x0 - dx, y0 - dy, x1 - dx, y1 - dy), texto_, fonte, margem=0.45)
            except ErroOperacao as e:
                erro = e
                continue
            gs = _Letras._sem_soltos(_Letras._refinar(
                [glifos.Glifo(g.i, g.ch, g.x0 + dx, g.y0 + dy, g.x1 + dx, g.y1 + dy, g.tinta) for g in gs]))
            if fios.any():  # a letra que encosta no fio (o pé do "/" na linha da célula) levaria o fio junto
                perto = _dilatar(fios, 3)
                for g in gs:
                    a0, b0 = g.y0 - ry0, g.x0 - rx0
                    janela = perto[max(0, a0):a0 + g.altura, max(0, b0):b0 + g.largura]
                    g.fio = bool(janela.size and (janela & g.tinta[max(0, -a0):max(0, -a0) + janela.shape[0],
                                                                   max(0, -b0):max(0, -b0) + janela.shape[1]]).any())
            return gs
        raise erro

    @staticmethod
    def _sem_soltos(gs: list) -> list:
        """Cada letra só com o que é dela: o corpo, o acento se o caractere tem (ou o pingo do i e do j) e a cedilha
        do Ç. A margem da segmentação junta à letra o pontinho, o pé da linha de cima ou o acento da de baixo que caem
        perto dela, e eles saíam colados com a letra no texto novo ("LOP.ES", o tique solto no "GAMA")."""
        out = []
        for g in gs:
            if g.ch in _VARIOS or not g.ch.strip():
                out.append(g)
                continue
            corpo, acima, abaixo = glifos.partes(g.tinta)
            if not acima and not abaixo:
                out.append(g)
                continue
            base, sinal = glifos.decompor(g.ch)
            m = corpo.copy()
            if acima and ((sinal is not None and sinal != glifos.CEDILHA) or base in "ij"):
                m |= np.logical_or.reduce(acima)
            if abaixo and sinal == glifos.CEDILHA:
                m |= np.logical_or.reduce(abaixo)
            ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
            novo = glifos.Glifo(g.i, g.ch, g.x0 + int(xs[0]), g.y0 + int(ys[0]), g.x0 + int(xs[-1]) + 1,
                                g.y0 + int(ys[-1]) + 1, np.ascontiguousarray(m[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]))
            novo.grudada = getattr(g, "grudada", False)
            out.append(novo)
        return out

    @staticmethod
    def _refinar(gs: list) -> list:
        """Cada pedaço de tinta inteiro com a letra que tem a maior parte dele: o corte da segmentação é por coluna, e
        no "TO" com kerning a ponta da barra do T caía nas colunas do O — o O recortado levava um tique da barra (e o T
        saía sem a ponta). O pedaço só muda de dono se for uma ponta (até 25% da tinta da letra que o perde) de um
        pedaço que é quase todo (70%) da outra. Letras grudadas de verdade ficam como estão e são marcadas ``grudada``: o
        corte entre elas é arbitrário, e a letra recortada levaria a ponta da vizinha (o pé do 5 no "/" de "25/01") ou
        ficaria sem um pedaço; não servem de doadoras."""
        if len(gs) < 2:
            return gs
        X0, Y0 = min(g.x0 for g in gs), min(g.y0 for g in gs)
        X1, Y1 = max(g.x1 for g in gs), max(g.y1 for g in gs)
        dono = np.full((Y1 - Y0, X1 - X0), -1, np.int32)
        for j, g in enumerate(gs):
            sub = dono[g.y0 - Y0:g.y1 - Y0, g.x0 - X0:g.x1 - X0]
            sub[g.tinta] = j
        total = np.bincount(dono[dono >= 0].ravel(), minlength=len(gs))
        n, lab = cv2.connectedComponents((dono >= 0).astype(np.uint8), connectivity=8)
        novo, mudou, grudadas = dono.copy(), False, set()
        for comp in range(1, n):
            sel = lab == comp
            js, cnt = np.unique(dono[sel], return_counts=True)
            if len(js) < 2:
                continue
            k = int(cnt.argmax())
            pontas = all(c <= 0.25 * total[j] for i, (j, c) in enumerate(zip(js, cnt)) if i != k)
            if cnt[k] >= 0.7 * cnt.sum() and pontas:
                novo[sel] = js[k]
                mudou = True
            else:
                grudadas |= {int(j) for j in js}
        out = gs
        if mudou:
            out = []
            for j, g in enumerate(gs):
                m = novo == j
                ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
                if not ys.size:
                    out = gs
                    break
                out.append(glifos.Glifo(g.i, g.ch, X0 + int(xs[0]), Y0 + int(ys[0]), X0 + int(xs[-1]) + 1,
                                        Y0 + int(ys[-1]) + 1, np.ascontiguousarray(m[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1])))
        for j in grudadas:
            out[j].grudada = True
        return out

    def _glifos_linha(self, i: int) -> list:
        """Glifos da linha ``i`` do OCR, sem fio de tabela, pela métrica da Arial (como o inventário) e, se ela não
        separar (fonte monoespaçada, "/"), pela do gabarito do campo. [] se nenhuma separa."""
        chave = ("glifos", i, self.fonte)
        if chave not in self.inv.memo:
            l = self.inv.linhas[i]
            try:
                gs = self._segmentar(self.inv.img, ocr.caixa_da_linha(self.inv.img, l["caixa"]), l.get("texto", ""),
                                     dict.fromkeys((None, self.fonte)))
            except ErroOperacao:
                gs = []
            self.inv.memo[chave] = gs
        return self.inv.memo[chave]

    def _escala_linha(self, gs) -> tuple[float | None, int]:
        """(tamanho da linha ÷ o do campo pelas letras que as duas têm, quantas são), sem régua de fonte nenhuma; None
        sem caractere em comum. A régua do gabarito erra de classe para classe (os dígitos da Courier New são mais altos
        que as maiúsculas; os do documento, não): uma linha só de dígitos saía 7% "menor" do que é."""
        def alturas(lista):
            d = {}
            for g in lista:
                if g.ch.isalnum() and self._plana(g):
                    d.setdefault(g.ch, []).append(g.altura)
            return {k: float(np.median(v)) for k, v in d.items()}
        a, b = alturas(self.gs), alturas(gs)
        comum = [b[k] / a[k] for k in sorted(a.keys() & b.keys())]
        return (float(np.median(comum)) if comum else None), len(comum)

    def _linhas(self) -> list:
        """(índice, mesma linha do campo?, estilo, escala pelas letras em comum ou None) das linhas do OCR no estilo do
        campo. O corpo tem de ser o mesmo: pelas letras em comum (duas ou mais), até max(4%, 0,6 px por altura); com
        uma só ou nenhuma, também pela régua do gabarito (6% e 5%). A tabela de histórico em corpo 12 numa data de
        corpo 13 passava nos 10% de antes e o 8 colado saía menor e mais baixo."""
        if self._lin is None:
            self._lin = []
            if self.inv is not None:
                x0, y0, x1, y1 = self.c.caixa
                h = max(1, y1 - y0)
                for i, l in enumerate(self.inv.linhas):
                    lx0, ly0, lx1, ly1 = l["caixa"]
                    if not 0.6 * h <= ly1 - ly0 <= 2.6 * h:
                        continue
                    chave = ("estilo", i, self.fonte, round(self.rot, 2))
                    e = self.inv.memo.get(chave)
                    if e is None:
                        gs = self._glifos_linha(i)
                        e = self._estilo(self.inv.img, gs) if gs else None
                        if e is not None:
                            e["base"] = glifos.linha_de_base(gs, self.rot, ajustar=True)
                        self.inv.memo[chave] = e or False
                    if not (e and e.get("base") and self._mesmo_estilo(e)):
                        continue
                    r, n = self._escala_linha(self._glifos_linha(i))
                    tam = abs(e["tam"] / self.est["tam"] - 1)
                    tol = max(0.04, 0.6 / max(1.0, self.est["altura"]))
                    if (n and abs(r - 1) > tol) or (n == 1 and tam > 0.06) or (not n and tam > 0.05):
                        continue
                    r = r if n >= 2 else None
                    mesma = min(y1, ly1) - max(y0, ly0) > 0.5 * min(h, ly1 - ly0)
                    self._lin.append((i, mesma, e, r))
        return self._lin

    # ------------------------------------------------------------------ doadores
    def _altura_esperada(self, ch: str, brutos) -> float:
        """A altura do caractere no campo, da evidência mais direta para a menos: as letras iguais do campo; as das
        linhas ligadas ao campo por letras em comum (na escala delas); as letras do campo da mesma classe pela proporção
        da fonte do gabarito; as das outras linhas no mesmo estilo (perto da régua); a régua do gabarito."""
        hs = [b[0].altura for b in brutos if b[2] == "campo" and b[0].ch == ch and self._plana(b[0])]
        if hs:
            return float(np.median(hs))
        hs = [b[0].altura / b[4] for b in brutos if b[2] != "campo" and b[4] and b[0].ch == ch and self._plana(b[0])]
        if hs:  # o C da Courier do documento não tem a sobra das letras redondas que a Courier New tem
            return float(np.median(hs))
        hf = self._h_fonte(ch)
        # as letras do campo da mesma classe (dígito, maiúscula, minúscula), pela proporção da fonte do gabarito: dentro
        # da classe a régua acerta (o 9 tem a altura do 5); entre classes, não
        classe = (str.isdigit, str.isupper, str.islower)
        k = next((f for f in classe if f(ch)), None)
        if hf and k is not None and glifos.decompor(ch)[1] is None:
            hs = [g.altura * hf / self._h_fonte(g.ch) for g in self.gs
                  if k(g.ch) and self._plana(g) and glifos.decompor(g.ch)[1] is None and self._h_fonte(g.ch)]
            if hs:
                return float(np.median(hs))
        ref = self.est["tam"] * hf if hf else self.est["altura"]
        hs = [b[0].altura / (b[4] or 1.0) for b in brutos if b[0].ch == ch and self._plana(b[0])
              and abs(b[0].altura / (b[4] or 1.0) / ref - 1) <= 0.15]
        return float(np.median(hs)) if hs else ref

    def doadores(self, ch: str) -> list[_Doador]:
        if ch in self._cands:
            return self._cands[ch]
        c = self.c
        base_ch, sinal = glifos.decompor(ch)
        def viz(lista, j):
            return tuple(lista[k] for k in (j - 1, j + 1) if 0 <= k < len(lista))

        brutos = [(g, c.img, "campo", self.base, 1.0, viz(self.gs, j)) for j, g in enumerate(self.gs)
                  if g.ch in (ch, base_ch)]
        cx0, cy0, cx1, cy1 = c.caixa
        if base_ch.strip():
            for i, mesma, e, r in self._linhas():
                txt = self.inv.linhas[i].get("texto", "")
                if ch not in txt and base_ch not in txt:
                    continue
                lista = self._glifos_linha(i)
                for j, g in enumerate(lista):
                    if g.ch not in (ch, base_ch):
                        continue
                    if not (g.x1 <= cx0 - 2 or g.x0 >= cx1 + 2 or g.y1 <= cy0 - 2 or g.y0 >= cy1 + 2):
                        continue  # o próprio campo (no documento original)
                    brutos.append((g, self.inv.img, "linha" if mesma else "documento", e["base"], r, viz(lista, j)))
        h_esp = self._altura_esperada(ch, brutos)
        neg = float(self.aj.get("negrito") or 0.0) * 100.0 / max(1.0, float(self.aj["tamanho"]))
        out = []
        for g, img, origem, base, r, vizinhos in brutos:
            H, W = img.shape[:2]
            m = 3 + int(math.ceil(2 * self.sigma))
            if g.x0 - m < 0 or g.y0 - m < 0 or g.x1 + m > W or g.y1 + m > H:
                continue
            if getattr(g, "grudada", False) or getattr(g, "fio", False):  # levaria a ponta da vizinha ou o fio
                continue
            if g.ch != ch:  # a letra base lida pelo OCR: serve para o caractere acentuado se o sinal na tinta bate
                if self._sinal_de(g, img) != sinal:
                    continue
            elif not self._plana(g):
                continue
            if abs(g.altura / (r or 1.0) / h_esp - 1) > self.TOL_ALTURA:
                continue
            if g.ch.isalnum() and glifos.semelhanca(g.tinta, ch, self.fonte, neg) < 0.35:
                continue  # OCR errado ou letra de outra fonte
            med = self._medir(img, g)
            dc = float(np.abs(med["T"] - self.est["T"]).max())
            # letra fora do padrão da própria linha (borrão, carimbo por cima); a vírgula e o ponto são pequenos
            # demais para a medida (o núcleo não chega à tinta cheia): vale o estilo da linha
            if g.ch.isalnum() and (dc > 1.5 * self.TOL_COR or (
                    self.est["peso"] > 0 and med["peso"] > 0
                    and abs(math.log(med["peso"] / self.est["peso"])) > math.log(1.4))):
                continue
            # até 3% (ou 1 px, na letra miúda a altura inteira oscila 1 px com a fase da linha) cola sem reamostrar
            escala = h_esp / g.altura if abs(h_esp - g.altura) > max(0.03 * h_esp, 1.0) else 1.0
            if abs(escala - 1) > 0.05:  # ampliar ou reduzir mais que isso borra a letra (o 6 da tabela, 14% maior)
                continue
            nota = ({"campo": 0, "linha": 10, "documento": 20}[origem] + (0 if glifos.isolado(img, g) else 5)
                    + (5 if escala != 1 else 0) + 30 * abs(g.altura / h_esp - 1) + 10 * dc)
            out.append(_Doador(g, img, origem, float(g.y1 - (base[0] + base[1] * g.cx)), escala, nota, med["T"],
                               vizinhos))
        out.sort(key=lambda d: d.nota)
        self._cands[ch] = out
        return out

    def _sinal_de(self, g, img) -> str | None:
        k = (id(img), g.caixa)
        if k not in self._sinais:
            base = glifos.decompor(g.ch)[0]
            self._sinais[k] = glifos.sinal_do_glifo(g.tinta, base, self.fonte)[0] if base.isalpha() else None
        return self._sinais[k]

    def doadores_de_sinal(self, base_ch: str, sinal: str) -> list:
        """(doador, máscara do sinal na caixa dele) de letras de mesma caixa (maiúscula/minúscula) com o ``sinal``."""
        out = []
        fontes = [(g, self.c.img, 0) for g in self.gs]
        for i, mesma, _e, _r in self._linhas():
            fontes += [(g, self.inv.img, 10 if mesma else 20) for g in self._glifos_linha(i)]
        m = 3 + int(math.ceil(2 * self.sigma))
        for g, img, nota in fontes:
            b = glifos.decompor(g.ch)[0]
            if not b.isalpha() or b.isupper() != base_ch.isupper() or self._sinal_de(g, img) != sinal:
                continue
            if g.x0 - m < 0 or g.y0 - m < 0 or g.x1 + m > img.shape[1] or g.y1 + m > img.shape[0]:
                continue
            _corpo, acima, abaixo = glifos.partes(g.tinta)
            partes_ = abaixo if sinal == glifos.CEDILHA else acima
            if not partes_:
                continue
            med = self._medir(img, g)
            out.append((nota + 10 * float(np.abs(med["T"] - self.est["T"]).max()),
                        _Doador(g, img, "campo" if nota == 0 else "linha" if nota == 10 else "documento", 0.0, 1.0,
                                nota, med["T"]), np.logical_or.reduce(partes_)))
        out.sort(key=lambda t: t[0])
        return [(d, s) for _n, d, s in out]

    # ------------------------------------------------------------------ peças e gabarito
    def _escolher(self, cands: list[_Doador], usos: Counter | None) -> _Doador:
        if usos is None:  # só para medir largura: a melhor, sem sorteio
            return cands[0]
        # a mesma letra repetida no texto novo sai de instâncias diferentes enquanto houver: duas cópias idênticas na
        # mesma linha são a pista do copy-move, pior que a letra de outra linha ou encostada na vizinha
        d = min(cands, key=lambda d: d.nota + 40 * usos[id(d)] + self.c.rng.random())
        usos[id(d)] += 1
        return d

    def peca(self, k: int, ch: str, usos: Counter | None = None) -> _Peca:
        cands = self.doadores(ch)
        if cands:
            return self._peca_doada(k, ch, "copiada", self._escolher(cands, usos))
        base_ch, sinal = glifos.decompor(ch)
        if sinal is not None and self.doadores(base_ch):
            p = self._peca_doada(k, ch, "acento_desenhado", self._escolher(self.doadores(base_ch), usos))
            sinais = self.doadores_de_sinal(base_ch, sinal)
            if sinais:
                p.tipo, p.sinal = "composta", sinais[0] if usos is None else sinais[int(self.c.rng.random() * min(3, len(sinais)))]
                return p
            try:  # a fonte desenha o sinal solto nesse tamanho? (no Tahoma Bold a 150 dpi o agudo gruda no E)
                self._sinal_desenhado(p, 0, 0)
                return p
            except ErroOperacao:
                pass
        return self._desenhada(k, ch, medir_so=usos is None)

    @staticmethod
    def _peca_doada(k: int, ch: str, tipo: str, d: _Doador) -> _Peca:
        """A peça de uma letra doada, medida pelo corpo (largura e canto): o vão do documento é entre corpos."""
        c0, c1 = _corpo_x(d.g)
        return _Peca(k, ch, tipo, d, largura=(c1 - c0) * d.escala, altura=d.g.altura * d.escala, dy=d.dy * d.escala,
                     dx_corpo=(c0 - d.g.x0) * d.escala)

    def _desenhada(self, k: int, ch: str, medir_so: bool = False) -> _Peca:
        """A letra que o documento não tem, desenhada na fonte do gabarito na altura que ela teria no campo."""
        hf = self._h_fonte(ch)
        h_esp = self._altura_esperada(ch, [(g, self.c.img, "campo", self.base, 1.0) for g in self.gs if g.ch == ch])
        if not hf:
            raise ErroOperacao(f"a fonte do gabarito não desenha '{ch}'")
        tam = h_esp * 100.0 / hf
        larg = float(self.aj.get("largura", 100) or 100) / 100.0
        if medir_so:
            l, r = _tinta_caractere(self.fonte, ch)
            return _Peca(k, ch, "desenhada", largura=(r - l) * tam / 100 * larg, altura=h_esp, dy=0.0)
        a, (ax0, ay0), (ix0, iy0, ix1, iy1) = self._render(ch, tam)
        return _Peca(k, ch, "desenhada", largura=float(ix1 - ix0), altura=float(iy1 - iy0), dy=float(iy1),
                     desenho=(a, (ax0, ay0), (ix0, iy0, ix1, iy1)))

    def _negrito(self) -> float:
        """Negrito (px no tamanho do gabarito) com que as letras desenhadas saem com a espessura de traço das do campo:
        a fonte parecida na forma pode ser mais leve (a Swis721 regular num Helvetica Bold de PDF) e o V desenhado saía
        fino no meio das letras grossas. Medido como nas letras do campo (glifos.peso_traco) e corrigido uma vez."""
        if self._neg is None:
            base = float(self.aj.get("negrito") or 0.0)
            letras = [g for g in self.gs if g.ch.isalnum() and self._plana(g) and self._h_fonte(g.ch)]
            letras = letras[::max(1, len(letras) // 6)][:6]
            if not letras or self.est["peso"] <= 0:
                self._neg = base
                return base

            def peso(neg):
                ps = []
                for g in letras:
                    a = self._render(g.ch, g.altura * 100.0 / self._h_fonte(g.ch), neg)[0]
                    ps.append(glifos.peso_traco(a * _dilatar(a > 0.35, 1)))
                return float(np.median(ps))
            self._neg = base
            p0 = peso(base)
            if p0 > 0 and abs(self.est["peso"] / p0 - 1) > 0.06:
                self._neg = float(np.clip(base + (self.est["peso"] - p0), 0.0, 10.0))
        return self._neg

    def _render(self, s: str, tam: float, negrito: float | None = None):
        """(alfa, canto do alfa, caixa da tinta nítida) de ``s`` na fonte do gabarito, âncora (0, 0), com o desfoque do
        scan, a inclinação da linha e o peso do traço do campo."""
        P = 10000
        neg = (self._negrito() if negrito is None else negrito) * tam / max(1.0, float(self.aj["tamanho"]))
        p = {"texto": s, "fonte": self.fonte, "tamanho": tam, "largura": self.aj.get("largura", 100), "negrito": neg,
             "rotacao": self.rot, "x": P, "y": P, "desfoque": self.c.desfoque}
        y0, _y1, x0, _x1, a = texto.renderizar(p, 10**5, 10**5)
        m = a > 0.5 if (a > 0.5).any() else a > 0
        ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
        return a, (x0 - P, y0 - P), (int(x0 + xs[0] - P), int(y0 + ys[0] - P), int(x0 + xs[-1] + 1 - P),
                                     int(y0 + ys[-1] + 1 - P))

    def _calibrar_vaos(self):
        """Vão entre letras e entre palavras do documento contra o previsto pelo gabarito. O vão medido de um par que
        existe no campo ou numa linha no mesmo estilo vale direto; os outros são o previsto + o resíduo típico (mediana)
        + a correção do lado de cada letra: a fonte do gabarito erra o espaço lateral letra a letra (o 1 da Calibri
        tem mais pé que o da Bahnschrift: "1141 APTO" saía com o espaço apertado). O espaço entre palavras sai do
        mesmo jeito, nunca menor que o vão típico entre letras + 0,3 da altura ("JMCSilvaFotos")."""
        if self._vaos is not None:
            return self._vaos
        h = self.est["altura"]
        fontes = [(self.gs, self.c.texto, 1.0, True)]
        fontes += [(self._glifos_linha(i), self.inv.linhas[i].get("texto", ""), r or e["tam"] / self.est["tam"], False)
                   for i, _m, e, r in self._linhas()]
        fonte = glifos.fonte_pil(self.fonte, float(self.aj["tamanho"]))
        adv_espaco = (fonte.getlength(" ") + float(self.aj.get("espacamento") or 0.0)) * float(
            self.aj.get("largura", 100) or 100) / 100.0
        letras, espacos, pares, tipicos = [], [], {}, []  # letras: (do campo?, char esq, char dir, real - previsto)
        for gs, txt, r, do_campo in fontes:
            gab = _faixas_gabarito(self.aj, txt)
            reais, esp = [], []
            for a, b in zip(gs, gs[1:]):
                if a.i not in gab or b.i not in gab:
                    continue
                entre = txt[a.i + 1:b.i]
                real, pred = (_corpo_x(b)[0] - _corpo_x(a)[1]) / r, gab[b.i][0] - gab[a.i][1]
                if entre == "":
                    reais.append((a, b, real, pred))
                elif entre.isspace():
                    esp.append((do_campo, a.ch, b.ch, real, pred))
            tipico = float(np.median([x[2] for x in reais])) if reais else 0.0
            if reais:
                tipicos.append((do_campo, tipico))
            for a, b, real, pred in reais:
                # o OCR juntou duas palavras ("MARECHALDEODORO"): o vão passa do previsto para as duas letras juntas em
                # mais de meio espaço. Pelo previsto, e não pelo vão típico: na Courier o I tem vão de meia célula
                if real - pred > 0.5 * adv_espaco:
                    espacos.append((do_campo, a.ch, b.ch, real - (pred + adv_espaco)))
                    continue
                letras.append((do_campo, a.ch, b.ch, real - pred))
                if self._plana(a) and self._plana(b):  # o acento do Ê avança sobre o vão do par
                    pares.setdefault((a.ch, b.ch), []).append(real)
            # espaço de verdade é bem mais largo que o vão entre letras ("PO RTAO": o OCR partiu a palavra)
            espacos += [(f, ca, cb, real - pred) for f, ca, cb, real, pred in esp if real > tipico + 0.12 * h]
        so = [x[3] for x in letras if x[0]]
        r_letra = float(np.median(so if len(so) >= 3 else [x[3] for x in letras])) if letras else 0.0
        # o lado de cada letra: a mediana do resíduo dos pares em que ela está à esquerda (direita), menos o típico
        lim = 0.15 * h
        dir_, esq = {}, {}
        for _f, ca, cb, x in letras:
            dir_.setdefault(ca, []).append(x)
            esq.setdefault(cb, []).append(x)
        lado_dir = {k: float(np.clip(np.median(v) - r_letra, -lim, lim)) for k, v in dir_.items() if len(v) >= 2}
        lado_esq = {k: float(np.clip(np.median(v) - r_letra, -lim, lim)) for k, v in esq.items() if len(v) >= 2}
        espacos = [(f, x - lado_dir.get(ca, 0.0) - lado_esq.get(cb, 0.0)) for f, ca, cb, x in espacos]
        so = [x for f, x in espacos if f]
        r_esp = float(np.median(so if len(so) >= 2 else [x for _f, x in espacos])) if espacos else r_letra
        so = [x for f, x in tipicos if f] or [x for _f, x in tipicos] or [0.0]
        minimo = float(np.median(so)) + 0.3 * h
        self._vaos = (r_letra, r_esp, {k: float(np.median(v)) for k, v in pares.items()}, minimo, lado_dir, lado_esq)
        return self._vaos

    def _vao(self, s: str, gab: dict, a: _Peca, b: _Peca) -> float:
        r_letra, r_esp, pares, minimo, lado_dir, lado_esq = self._calibrar_vaos()
        pred = gab[b.k][0] - gab[a.k][1]
        lados = lado_dir.get(a.ch, 0.0) + lado_esq.get(b.ch, 0.0)
        if s[a.k + 1:b.k].strip() == "" and b.k > a.k + 1:
            return max(pred + r_esp + lados, minimo)
        par = pares.get((a.ch, b.ch))
        return par if par is not None else pred + r_letra + lados

    def montar(self, s: str, pecas: list[_Peca], aperto: float = 0.0) -> tuple[list[float], float]:
        """Posição (ao longo da linha de base, a partir da tinta da primeira letra) de cada peça e a largura total."""
        gab = _faixas_gabarito(self.aj, s)
        us, u = [], 0.0
        for j, p in enumerate(pecas):
            if j:
                vao = self._vao(s, gab, pecas[j - 1], p)
                u += vao - (aperto if s[pecas[j - 1].k + 1:p.k] == "" else 0.0)
            us.append(u)
            u += p.largura
        return us, u

    def largura(self, s: str) -> float:
        """Largura da tinta de ``s`` montado com as letras do documento (a régua do ``_novo_valor``)."""
        if s == self.c.texto:
            return float(_corpo_x(self.gs[-1])[1] - _corpo_x(self.gs[0])[0])
        pecas = [self.peca(k, ch) for k, ch in enumerate(s) if not ch.isspace()]
        return self.montar(s, pecas)[1]

    def inicio(self, s: str, largura: float, alin: str) -> float:
        """x da tinta da primeira letra: a mesma margem (esquerda), o mesmo fim (direita) ou o mesmo centro do texto
        antigo; a diferença do espaço lateral (bearing) da primeira e da última letra vem do gabarito."""
        old0, old1 = float(_corpo_x(self.gs[0])[0]), float(_corpo_x(self.gs[-1])[1])
        ga, gn = _faixas_gabarito(self.aj, self.c.texto), _faixas_gabarito(self.aj, s)
        ka0, kn0 = min(ga), min(gn)
        ka1, kn1 = max(ga), max(gn)
        cos = math.cos(math.radians(self.rot))
        if alin == "direita":
            f = glifos.fonte_pil(self.fonte, float(self.aj["tamanho"]))
            larg = float(self.aj.get("largura", 100) or 100) / 100.0
            esp = float(self.aj.get("espacamento") or 0.0)
            fim_a = (f.getlength(self.c.texto) + (len(self.c.texto) - 1) * esp) * larg - ga[ka1][1]
            fim_n = (f.getlength(s) + (len(s) - 1) * esp) * larg - gn[kn1][1]
            return old1 + fim_a - fim_n - largura * cos
        if alin == "centro":
            return (old0 + old1) / 2 - largura * cos / 2
        return old0 + gn[kn0][0] - ga[ka0][0]

    def plano(self, s: str, alin: str) -> tuple[list[_Peca], list[tuple[int, int]], float]:
        """Peças de ``s`` (com o sorteio das instâncias) e o canto (x, y do pé) de cada uma na imagem. Levanta _NaoCabe
        se o texto passaria por cima da tinta vizinha mesmo apertando o vão entre as letras até 8% da largura."""
        c = self.c
        usos = Counter()
        pecas = [self.peca(k, ch, usos) for k, ch in enumerate(s) if not ch.isspace()]
        if not pecas:
            raise ErroOperacao("texto novo vazio")
        us, total = self.montar(s, pecas)
        x0, y0, x1, y1 = c.caixa
        h = max(4, y1 - y0)
        r_esp = self._calibrar_vaos()[1]
        folga = max(4, int(round(r_esp + 0.25 * h)))
        X = self.inicio(s, total, alin)
        nx0, nx1 = X, X + total
        old0, old1 = _corpo_x(self.gs[0])[0], _corpo_x(self.gs[-1])[1]
        falta = 0.0
        if nx1 > old1 + 2:
            falta += max(0.0, nx1 - old1 - _papel_livre(c, "direita", int(math.ceil(nx1 - old1)) + folga + 4, folga))
        if nx0 < old0 - 2:
            falta += max(0.0, old0 - nx0 - _papel_livre(c, "esquerda", int(math.ceil(old0 - nx0)) + folga + 4, folga))
        if falta > 0:
            juntas = sum(1 for a, b in zip(pecas, pecas[1:]) if b.k == a.k + 1)
            if not juntas or falta > 0.08 * total:
                raise _NaoCabe(f"'{s}' não cabe no espaço do campo: precisaria apertar {100 * falta / total:.0f}% "
                               f"(até 8%) ou passar por cima do que está ao lado")
            aperto = (falta + 1) / juntas
            us, total2 = self.montar(s, pecas, aperto)
            X = self.inicio(s, total2, alin) if alin != "esquerda" else X
            self.aperto = aperto
        cos, b0, b1 = math.cos(math.radians(self.rot)), self.base[0], self.base[1]
        cantos = []
        for p, u in zip(pecas, us):
            xl = X + u * cos
            pe = b0 + b1 * (xl + p.largura * cos / 2) + p.dy  # pé da tinta: linha de base no centro da letra + dy
            cantos.append((int(round(xl)), int(round(pe))))
        return pecas, cantos, total

    # ------------------------------------------------------------------ composição
    def _doada(self, d: _Doador, dono: np.ndarray):
        """(T, w, m) da tinta ``dono`` (bool na caixa de d.g) do doador: transmitância (pixel ÷ papel local do doador,
        por canal; leva o antialias, o grão da tinta e o desfoque) e peso 0..1 (a tinta, o halo até ~1,5σ e nada que
        esteja mais perto de outra tinta: letra vizinha, linha de cima), no recorte caixa de g + m px."""
        g, img = d.g, d.img
        h = g.altura
        m = 3 + int(math.ceil(2 * self.sigma))
        M = max(8, h)
        H, W = img.shape[:2]
        bx0, by0, bx1, by1 = max(0, g.x0 - m - M), max(0, g.y0 - m - M), min(W, g.x1 + m + M), min(H, g.y1 + m + M)
        big = img[by0:by1, bx0:bx1]
        tinta = mascara_tinta(big).astype(bool)
        papel = texto._fundo_local(big, _dilatar(tinta, 2), float(h)).astype(np.float32)
        sl = (slice(g.y0 - m - by0, g.y1 + m - by0), slice(g.x0 - m - bx0, g.x1 + m - bx0))
        T = np.clip(big[sl].astype(np.float32) / np.maximum(papel[sl], 1.0), 0.0, 1.5)
        meu = np.zeros(T.shape[:2], bool)
        meu[m:m + g.altura, m:m + g.largura] = dono
        outra = tinta[sl] & ~_dilatar(meu, 1)
        if dono is not g.tinta:  # só o sinal: o corpo da letra doadora é "outra" tinta
            outra |= np.pad(g.tinta & ~dono, m)
        for v in d.vizinhos:  # letra encostada (AV com kerning): a tinta da vizinha sai exata, pelo corte da segmentação
            r = self._local(v.caixa, (g.x0 - m, g.y0 - m, g.x1 + m, g.y1 + m))
            if r:
                outra[r[0]] |= v.tinta[r[1]]
        d_meu = cv2.distanceTransform((~meu).astype(np.uint8), cv2.DIST_L2, 3)
        w = np.clip(2.0 + 1.5 * self.sigma - d_meu, 0.0, 1.0)
        if outra.any():
            w[cv2.distanceTransform((~outra).astype(np.uint8), cv2.DIST_L2, 3) < d_meu] = 0.0
        return T, w.astype(np.float32), m

    def compor(self, pecas: list[_Peca], cantos: list[tuple[int, int]]) -> Resultado:
        c = self.c
        x0, y0, x1, y1 = c.caixa
        buraco = auto._buraco_tinta(c.img, x0, y0, x1, y1, self.sigma)
        if not buraco.any():
            raise ErroOperacao("não achei a tinta do campo para apagar")
        # o que entra: (T, w, canto) da tinta de cada peça, e o alfa das partes desenhadas
        itens, desenhos = [], []
        for p, (xl, pe) in zip(pecas, cantos):
            if p.doador is not None:
                d = p.doador
                T, w, m = self._doada(d, d.g.tinta)
                T = self._tom(T, d)
                if d.escala != 1.0:
                    T, w = self._escalar(T, w, d.escala)
                mx = m * d.escala
                topo = pe - int(round(d.g.altura * d.escala))
                xc = int(round(xl - p.dx_corpo))  # canto da caixa da letra (o corpo começa em xl)
                itens.append((T, w, (int(round(xc - mx)), int(round(topo - mx)))))
                p.destino = (xc, int(topo), xc + int(round(d.g.largura * d.escala)), int(pe))
                if p.tipo == "composta":
                    itens.append(self._sinal_colado(p, xc, topo))
                elif p.tipo == "acento_desenhado":
                    desenhos.append(self._sinal_desenhado(p, xc, topo))
            else:
                a, (ax0, ay0), (ix0, iy0, ix1, iy1) = p.desenho
                desenhos.append((a, (int(round(xl - ix0 + ax0)), int(round(pe - iy1 + ay0)))))
                p.destino = (int(xl), int(round(pe - (iy1 - iy0))), int(round(xl + ix1 - ix0)), int(pe))
        caixas = [(cx, cy, cx + T.shape[1], cy + T.shape[0]) for T, _w, (cx, cy) in itens]
        caixas += [(cx, cy, cx + a.shape[1], cy + a.shape[0]) for a, (cx, cy) in desenhos]
        # o que se apaga: só a tinta, com o fundo local; o operador humano seleciona em volta (retângulo, laço,
        # pincel) e apaga com a ferramenta dele, a mesma das outras edições da versão
        sel_apagar, self.apagador = selecao.de_mascara(buraco), "local"
        h_ = c.humano
        if h_ is not None:
            self.apagador = humano.APAGADOR.get(h_.get("apagar"), "patchmatch")
            if h_.get("selecao") != "tinta":
                sel_apagar = auto._sel_humana(c.img, buraco, c.caixa, {"forma": h_["selecao"], "margem": h_.get("margem")},
                                              c.semente)
                c.fornecedores["selecao"] = h_["selecao"]
            buraco = sel_apagar.completa_bool()
        ys, xs = np.nonzero(buraco)
        caixas.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
        X0, Y0 = max(0, min(b[0] for b in caixas)), max(0, min(b[1] for b in caixas))
        X1, Y1 = min(c.W, max(b[2] for b in caixas)), min(c.H, max(b[3] for b in caixas))
        h = max(8, y1 - y0)
        M = int(max(16, 1.5 * h))
        ax0, ay0, ax1, ay1 = max(0, X0 - M), max(0, Y0 - M), min(c.W, X1 + M), min(c.H, Y1 + M)
        # papel: a tinta antiga apagada pelo fundo local (como a mesma fonte) e, para compor, o papel liso por baixo
        limpo = texto.apagar(c.img, sel_apagar, 7, c.semente, None, self.apagador,
                             so_papel=self.apagador != "local")[ay0:ay1, ax0:ax1]
        tinta_viz = _dilatar(mascara_tinta(limpo), 1)
        papel = texto._fundo_local(limpo, _dilatar(tinta_viz, 3), float(h)).astype(np.float32)
        base = np.where(tinta_viz[..., None], limpo.astype(np.float32), papel)  # tinta vizinha (fio, rótulo) fica
        L = np.ones(limpo.shape, np.float32)
        Wc = np.zeros(limpo.shape[:2], np.float32)
        for T, w, (cx, cy) in itens:
            r = self._local((cx, cy, cx + w.shape[1], cy + w.shape[0]), (ax0, ay0, ax1, ay1))
            if r:
                (dy_, dx_), (sy, sx) = r
                L[dy_, dx_] *= 1.0 - w[sy, sx, None] * (1.0 - T[sy, sx])
                Wc[dy_, dx_] = np.maximum(Wc[dy_, dx_], w[sy, sx])
        cor = self._cor_desenho() if desenhos else None
        for a, (cx, cy) in desenhos:
            r = self._local((cx, cy, cx + a.shape[1], cy + a.shape[0]), (ax0, ay0, ax1, ay1))
            if r:
                (dy_, dx_), (sy, sx) = r
                ref = np.maximum(papel[dy_, dx_], 1.0)
                L[dy_, dx_] *= 1.0 - a[sy, sx, None] * (1.0 - np.clip(cor / ref, 0, 1))
                Wc[dy_, dx_] = np.maximum(Wc[dy_, dx_], (a[sy, sx] > 0).astype(np.float32))
        out = limpo.astype(np.float32) * (1 - Wc[..., None]) + base * L * Wc[..., None]
        pegada = (Wc > 0) | buraco[ay0:ay1, ax0:ax1]
        sl = (slice(Y0 - ay0, Y1 - ay0), slice(X0 - ax0, X1 - ax0))
        rec = np.where(pegada[sl][..., None], np.clip(out[sl] + 0.5, 0, 255), c.img[Y0:Y1, X0:X1]).astype(np.uint8)
        return Resultado(Y0, Y1, X0, X1, rec, pegada[sl].copy())

    @staticmethod
    def _local(caixa, regiao):
        x0, y0, x1, y1 = caixa
        X0, Y0, X1, Y1 = regiao
        ix0, iy0, ix1, iy1 = max(x0, X0), max(y0, Y0), min(x1, X1), min(y1, Y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return None
        return ((slice(iy0 - Y0, iy1 - Y0), slice(ix0 - X0, ix1 - X0)),
                (slice(iy0 - y0, iy1 - y0), slice(ix0 - x0, ix1 - x0)))

    def _tom(self, T: np.ndarray, d: _Doador) -> np.ndarray:
        """A tinta de outra linha com a escuridão da do campo (a impressão varia um pouco de linha para linha): a
        profundidade 1 − T escalada por canal, até ±15%; a textura da tinta fica."""
        if d.origem == "campo":
            return T
        k = np.clip((1 - self.est["T"]) / np.maximum(1 - d.T, 1e-3), 0.85, 1.18).astype(np.float32)
        return 1.0 - (1.0 - T) * k

    @staticmethod
    def _escalar(T, w, e):
        h, wd = w.shape
        nw, nh = max(1, int(round(wd * e))), max(1, int(round(h * e)))
        return (np.clip(cv2.resize(T, (nw, nh), interpolation=cv2.INTER_CUBIC), 0, 1.5),
                np.clip(cv2.resize(w, (nw, nh), interpolation=cv2.INTER_LINEAR), 0, 1))

    def _sinal_colado(self, p: _Peca, xl: int, topo: int):
        """O sinal (acento, til, cedilha) recortado de outra letra do documento, na posição que a fonte dá para ele
        sobre esta letra (centro) e na distância medida na letra doadora (altura)."""
        ds, mascara = p.sinal
        T, w, m = self._doada(ds, mascara)
        T = self._tom(T, ds)
        corpo = glifos.partes(ds.g.tinta)[0]
        base_ch, sinal = glifos.decompor(p.ch)
        _s, dx_doador, gap = glifos._relativo(corpo, mascara, sinal == glifos.CEDILHA)
        ref = glifos.sinal_da_fonte(base_ch, sinal, self.fonte)
        corpo_b = glifos.partes(p.doador.g.tinta)[0]
        hb = (np.flatnonzero(corpo_b.any(1))[-1] + 1 - np.flatnonzero(corpo_b.any(1))[0]) * p.doador.escala
        hf = self._h_fonte(base_ch) or hb
        dx = ref[1] * hb / hf if ref is not None else dx_doador
        ys, xs = np.flatnonzero(mascara.any(1)), np.flatnonzero(mascara.any(0))
        sx0, sy0, sx1, sy1 = xs[0], ys[0], xs[-1] + 1, ys[-1] + 1  # sinal na caixa da letra doadora
        cyb = np.flatnonzero(corpo_b.any(1))
        cxb = np.flatnonzero(corpo_b.any(0))
        e = p.doador.escala
        centro = xl + (cxb[0] + cxb[-1] + 1) / 2 * e + dx
        if sinal == glifos.CEDILHA:
            topo_s = topo + (cyb[-1] + 1) * e + gap
        else:
            topo_s = topo + cyb[0] * e - gap - (sy1 - sy0)
        X = centro - (sx0 + sx1) / 2 - m
        Y = topo_s - sy0 - m
        return T, w, (int(round(X)), int(round(Y)))

    def _sinal_desenhado(self, p: _Peca, xl: int, topo: int):
        """O sinal que o documento não tem, desenhado na fonte do gabarito sobre a letra doadora: a letra acentuada
        menos a letra base, desenhadas na mesma âncora (serve também para a cedilha grudada no C), com o corpo
        desenhado casado com o corpo doador (centro; topo para acento, pé para cedilha)."""
        base_ch, sinal = glifos.decompor(p.ch)
        e = p.doador.escala
        corpo_b = glifos.partes(p.doador.g.tinta)[0]
        cyb, cxb = np.flatnonzero(corpo_b.any(1)), np.flatnonzero(corpo_b.any(0))
        hb = (cyb[-1] + 1 - cyb[0]) * e
        hf = self._h_fonte(base_ch) or hb
        tam = hb * 100.0 / hf
        a_s, (sx0, sy0), _ink = self._render(p.ch, tam)
        a_b, (bx0, by0), (ix0, iy0, ix1, iy1) = self._render(base_ch, tam)
        X0, Y0 = min(sx0, bx0), min(sy0, by0)
        X1, Y1 = max(sx0 + a_s.shape[1], bx0 + a_b.shape[1]), max(sy0 + a_s.shape[0], by0 + a_b.shape[0])
        A, B = np.zeros((Y1 - Y0, X1 - X0), np.float32), np.zeros((Y1 - Y0, X1 - X0), np.float32)
        A[sy0 - Y0:sy0 - Y0 + a_s.shape[0], sx0 - X0:sx0 - X0 + a_s.shape[1]] = a_s
        B[by0 - Y0:by0 - Y0 + a_b.shape[0], bx0 - X0:bx0 - X0 + a_b.shape[1]] = a_b
        d = np.clip(A - B, 0.0, 1.0)
        solido = d > 0.35
        if solido.sum() < 3:
            raise ErroOperacao(f"não deu para desenhar o sinal de '{p.ch}'")
        d = d * _dilatar(solido, 1 + int(math.ceil(2 * self.c.desfoque)))
        dxp = xl + (cxb[0] + cxb[-1] + 1) / 2 * e - ((ix0 + ix1) / 2 - X0)
        if sinal == glifos.CEDILHA:
            dyp = topo + (cyb[-1] + 1) * e - (iy1 - Y0)
        else:
            dyp = topo + cyb[0] * e - (iy0 - Y0)
        return d, (int(round(dxp)), int(round(dyp)))

    def _cor_desenho(self) -> np.ndarray:
        """Cor das partes desenhadas: a que, com o desfoque e o traço do campo, dá à letra desenhada o p10 de
        luminância de uma letra do campo (a mediana, letra a letra; texto._calibrar_cor). Pelo núcleo (_cor_nova), e
        mesmo pelo p10 da linha toda, o M desenhado saía ~10 níveis mais escuro que as letras batidas da Courier, de
        tinta manchada: a letra uniforme não tem os pontos claros que as outras têm."""
        if self._cor is None:
            c = self.c
            cor = np.asarray(_cor_nova(c, self.gs[0]), np.float32)
            p10 = []
            for g in [g for g in self.gs if g.ch.isalnum()][:12]:
                y0, x0 = max(0, g.y0 - 2), max(0, g.x0 - 2)
                lum = cv2.GaussianBlur(c.img[y0:g.y1 + 2, x0:g.x1 + 2].astype(np.float32) @ _LUMA, (0, 0), 0.8)
                p10.append(float(np.percentile(lum[g.y0 - y0:g.y0 - y0 + g.altura, g.x0 - x0:g.x0 - x0 + g.largura][g.tinta],
                                               10)))
            alvo = float(np.median(p10)) if p10 else auto._tinta_antiga(c.img, *c.caixa)
            if alvo is not None:
                try:
                    a = self._render(c.texto, float(self.aj["tamanho"]))[0]
                    papel = np.asarray(glifos.cor_papel(c.img, c.caixa), np.float32)
                    cor = np.asarray(texto._calibrar_cor(np.broadcast_to(papel, a.shape + (3,)), a, cor, alvo), np.float32)
                except ErroOperacao:
                    pass
            self._cor = cor
        return self._cor


def _t_campo_letras_do_documento(c: _C) -> Resultado:
    """O texto novo MONTADO com as letras recortadas do próprio documento — do campo e das linhas no mesmo estilo
    (tamanho, cor, peso do traço, nitidez) —, como faz um falsificador profissional: a fonte identificada serve só de
    gabarito (avanço, linha de base, inclinação); o vão entre as letras e entre as palavras é o do documento. A letra
    que o documento não tem sai desenhada na fonte (rótulo "misto"); faltando mais de 25%, a técnica desiste."""
    L = _Letras(c)
    opcoes = c.p.get("opcoes") or {}
    alin = opcoes.get("alinhamento")
    if alin not in ("esquerda", "direita"):
        alin = (_alinhamento_no_documento(c)
                or ("direita" if c.categoria in ("valor", "consumo/leitura") else "esquerda"))
    c.escolhas["alinhamento"] = alin
    novo = _novo_valor(c, largura=L.largura)
    try:
        pecas, cantos, _total = L.plano(novo, alin)
    except _NaoCabe:
        if c.dado:
            raise
        novo = _novo_valor(c, maximo=1.0, largura=L.largura)
        pecas, cantos, _total = L.plano(novo, alin)
    n = len(pecas)
    desenhadas = [p.ch for p in pecas if p.tipo == "desenhada"]
    if len(desenhadas) > 0.25 * n:
        raise ErroOperacao(f"o documento não tem as letras de '{novo}' no estilo do campo: faltam "
                           f"{len(desenhadas)} de {n} ({''.join(desenhadas)})")
    res = L.compor(pecas, cantos)
    acentos = [p.ch for p in pecas if p.tipo == "acento_desenhado"]
    compostas = [p.ch for p in pecas if p.tipo == "composta"]
    reamostrado = any(p.doador is not None and p.doador.escala != 1.0 for p in pecas)
    misto = bool(desenhadas or acentos)
    c.fornecedores.update(apagar=L.apagador, texto="misto" if misto else "glifos_do_documento", gabarito=L.fonte)
    if misto:
        c.fornecedores["fonte"] = L.fonte
    origens = Counter(p.doador.origem for p in pecas if p.doador is not None)
    frac = (n - len(desenhadas)) / n
    doadores = [{"caractere": p.ch, "tipo": p.tipo, "origem": p.doador.origem, "caixa": list(p.doador.g.caixa),
                 "para": list(p.destino), "escala": round(p.doador.escala, 3)} for p in pecas if p.doador is not None]
    partes_desc = [f"{frac:.0%} das letras recortadas do documento ({', '.join(f'{k}: {v}' for k, v in origens.most_common())})"]
    if compostas:
        partes_desc.append(f"sinal recortado de outra letra em {''.join(compostas)}")
    if acentos:
        partes_desc.append(f"sinal desenhado em {''.join(acentos)}")
    if desenhadas:
        partes_desc.append(f"desenhadas em {L.fonte}: {''.join(desenhadas)}")
    if L.aperto:
        partes_desc.append(f"vão entre as letras apertado {L.aperto:.1f} px para caber")
    res.meta = c.meta(novo, [_alt(0, c.texto, novo, "campo")],
                      f"Campo remontado com as letras do próprio documento: '{c.texto}' → '{novo}' ("
                      + "; ".join(partes_desc) + f"; gabarito de posição {L.fonte}, alinhado pela {alin}); "
                      + ("apagado só a tinta, fundo local." if L.apagador == "local" else
                         f"apagado com {L.apagador}" + (f" na seleção {c.fornecedores['selecao']}"
                                                        if c.fornecedores.get("selecao") else "") + "."),
                      letras_copiadas=round(frac, 3), desenhados=desenhadas, acentos_desenhados=acentos,
                      caixas_desenhadas=[list(p.destino) for p in pecas if p.tipo == "desenhada"],
                      sinais_colados=compostas, doadores=doadores, reamostrado=reamostrado, gabarito=L.fonte,
                      gabarito_entre=L.entre, semelhanca_gabarito=L.aj.get("semelhanca"),
                      apertado_px=round(L.aperto, 2))
    return res


def _t_campo_amador(c: _C) -> Resultado:
    novo = _novo_valor(c, maximo=1.1)
    fundo = c.opcao("fundo")
    # a fonte do sistema de quem edita (hábito do falsificador: a mesma em toda a versão) ou a sorteada
    fonte = c.habitos.get("fonte") if c.habitos.get("fonte") in texto.listar_fontes() else c.opcao("fonte")
    if fonte not in texto.listar_fontes():
        fonte = texto.fonte_padrao()
    c.escolhas["fonte"] = fonte
    x0, y0, x1, y1 = c.caixa
    h = y1 - y0
    m = max(2, int(round(0.25 * h)))
    est = texto.estimar(c.img, auto._sel_ret(c.img, x0 - 1, y0 - 1, x1 + 1, y1 + 1), fonte)
    k = float(c.habitos.get("capricho", 1.0))  # desleixo: desloca e erra o tamanho mais ou menos
    dx, dy = c.rng.uniform(-0.15, 0.15) * h * k, c.rng.uniform(-0.08, 0.08) * h * k
    preto = c.habitos["preto"] if "preto" in c.habitos else c.rng.random() < 0.5
    raster = c.opcao("rasterizador")  # cada programa desenha a letra de um jeito (GDI, ClearType, Skia...)
    params = {"texto": novo, "fonte": fonte, "tamanho": est["tamanho"] * (1 + c.rng.uniform(-0.08, 0.08) * k),
              "x": est["x"] + dx, "y": est["y"] + dy, "cor": "#000000" if preto else est["cor"],
              "espacamento": round(c.rng.uniform(-0.02, 0.06) * est["tamanho"] * max(0.0, k - 0.8), 2),
              "rasterizador": raster}
    if raster == "cleartype":
        ty0, ty1, tx0, tx1, a = texto.renderizar_cleartype(params, c.H, c.W)
    else:
        ty0, ty1, tx0, tx1, a = texto.renderizar(params, c.H, c.W)
    c.fornecedores.update(texto=raster, fonte=fonte)
    cor_fundo = np.array([255, 255, 255], np.float32) if fundo == "branco" else glifos.cor_papel(c.img, c.caixa)
    ret = _uniao((x0 - m, y0 - m, x1 + m, y1 + m), (tx0 - 2, ty0 - 2, tx1 + 2, ty1 + 2))
    if ret[2] > x1 + m and not c.livre((x1 + m, y0, ret[2], y1)):
        raise ErroOperacao("o texto novo cobriria o que está à direita do campo")
    if ret[0] < x0 - m and not c.livre((ret[0], y0, x0 - m, y1)):
        raise ErroOperacao("o texto novo cobriria o que está à esquerda do campo")
    tela = _Tela(c, ret)
    tela.opaco(tela.caixa, np.broadcast_to(cor_fundo, tela.px.shape))
    cor_texto = np.zeros(3, np.float32) if preto else np.array(
        [int(est["cor"][i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    tela.tinta((tx0, ty0, tx1, ty1), a, cor_texto)
    return tela.resultado(c.meta(novo, [_alt(0, c.texto, novo, "campo")],
                                 f"Estilo amador: retângulo {'branco' if fundo == 'branco' else 'da cor do papel'} sobre "
                                 f"'{c.texto}' e '{novo}' digitado em {fonte} "
                                 f"{'preto' if preto else 'na cor estimada'} (rasterizador {raster}), sem imitar a fonte "
                                 f"nem o grão."))


_SUFIXOS = {  # (rng, texto do campo) -> complemento; FILHO/JUNIOR/NETO só em nome de homem
    "nome": lambda r, t: " " + (geradores.palavra("sobrenome")
                                if geradores._normalizar((t.split() or [""])[0]) in geradores._FEMININOS - geradores._MASCULINOS
                                else r.choice(["JUNIOR", "FILHO", "NETO", geradores.palavra("sobrenome")])),
    "endereço": lambda r, t: r.choice([f" APTO {r.randrange(1, 30)}{r.randrange(1, 9):02d}", f" CASA {r.randrange(2, 9)}",
                                       f" BLOCO {r.choice('ABCDE')}", " FUNDOS", f" SALA {r.randrange(1, 40)}"]),
    "bairro": lambda r, t: " " + r.choice(["II", "NORTE", "SUL", "NOVO", "LESTE"]),
    "identificador documento": lambda r, t: str(r.randrange(10)),
}


def _t_campo_acrescentar(c: _C) -> Resultado:
    if c.categoria not in _SUFIXOS:
        raise ErroOperacao("não há complemento plausível para esta categoria")
    gs = c.glifos
    sufixo = _mesma_caixa(c.texto, _SUFIXOS[c.categoria](c.rng, c.texto)) if not c.dado else c.dado
    ultimo = gs[-1]
    if sufixo.startswith(" "):
        f = glifos.fonte_pil(c.aj["fonte"], c.aj["tamanho"])
        X = c.ancora(ultimo) + f.getlength(ultimo.ch + " ") * c.aj.get("largura", 100) / 100
    else:  # dígito colado: mesma distância entre as letras do fim
        vaos = [b.x0 - a.x1 for a, b in zip(gs, gs[1:]) if b.i == a.i + 1]
        X = None
        esquerda = ultimo.x1 + (float(np.median(vaos)) if vaos else 2.0)
    s = sufixo.strip()
    caixa_r, a = (c.desenhar(s, ancora=X, humano_=True) if X is not None
                  else c.desenhar(s, esquerda=esquerda, humano_=True))
    if not c.livre((caixa_r[0] - 2, c.caixa[1], caixa_r[2] + 3, c.caixa[3])):
        raise ErroOperacao("não há espaço livre depois do campo")
    tela = _Tela(c, caixa_r)
    tela.tinta(caixa_r, a, _cor_escrita(c, ultimo))
    novo = c.texto + sufixo
    return tela.resultado(c.meta(novo, [_alt(len(c.texto), "", sufixo, "insercao")],
                                 f"Acrescentado '{sufixo.strip()}' no fim de '{c.texto}' na fonte {c.aj['fonte']}: "
                                 f"'{novo}'."))


def _trechos(cat: str | None, s: str) -> list[tuple[int, int, str]]:
    """Partes do campo que fazem sentido trocar sozinhas: (início, fim, tipo)."""
    out = []

    def add(m, g, tipo):
        if m and m.group(g):
            out.append((m.start(g), m.end(g), tipo))

    if cat == "data":
        m = re.search(r"(\d{2})([/.\-])(\d{2})\2(\d{2,4})", s)
        add(m, 1, "dia"), add(m, 3, "mes_num"), add(m, 4, "ano")
    elif cat == "valor":
        m = re.search(r"(\d[\d.]*),(\d{2})", s)
        add(m, 2, "digitos")
        if m and len(re.sub(r"\D", "", m.group(1))) >= 1:
            add(m, 1, "digitos")
    elif cat == "cpf/cnpj":
        add(re.search(r"-(\d{2})\s*$", s), 1, "digitos")
        grupos = list(re.finditer(r"\d{3}", s))
        if len(grupos) >= 2:
            out.append((grupos[1].start(), grupos[1].end(), "digitos"))
    elif cat == "cep":
        m = re.search(r"(\d{5})-?(\d{3})", s)
        add(m, 2, "digitos"), add(m, 1, "digitos")
    elif cat == "endereço":
        add(re.search(r",\s*(\d+)", s), 1, "digitos")
        add(re.search(r"\b(?:APTO|AP|CASA|BLOCO|BL|SALA|LOTE|QD|QUADRA)\.?\s*(\d+)", s, re.I), 1, "digitos")
        add(re.match(r"^\s*\S+\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ ]*?)\s*(?:,|\d|$)", s), 1, "rua")
    elif cat == "nome":
        ws = list(re.finditer(r"[A-Za-zÀ-ÿ]{3,}", s))
        if len(ws) >= 2:
            out.append((ws[-1].start(), ws[-1].end(), "sobrenome"))
            out.append((ws[0].start(), ws[0].end(), "prenome"))
    elif cat == "mês de referência":
        m = re.search(r"([A-Za-z]{3})(/?)(\d{2,4})", s)
        add(m, 1, "mes_nome"), add(m, 3, "ano")
        m = re.search(r"\b(\d{2})/(\d{4})\b", s)
        add(m, 1, "mes_num"), add(m, 2, "ano")
    elif cat == "bairro":
        for m in re.finditer(r"[A-Za-zÀ-ÿ]{4,}", s):
            out.append((m.start(), m.end(), "bairro"))
    if not out:  # genérico: um pedaço de 2 a 4 dígitos
        for m in re.finditer(r"\d{2,}", s):
            n = min(len(m.group()), 4)
            out.append((m.end() - n, m.end(), "digitos"))
    return out


def _novo_trecho(tipo: str, sub: str, rng: random.Random) -> str:
    if tipo == "dia":
        return f"{rng.choice([d for d in range(1, 29) if d != int(sub)]):02d}"
    if tipo == "mes_num":
        return f"{rng.choice([m for m in range(1, 13) if m != int(sub)]):02d}"
    if tipo == "ano":
        v = int(sub) + rng.choice([-2, -1, 1, 1, 2])
        return f"{v % 100:02d}" if len(sub) == 2 else str(v)
    if tipo == "digitos":  # só os dígitos mudam; ponto de milhar e hífen ficam
        for _ in range(20):
            novo = "".join(str(rng.randrange(10)) if ch.isdigit() else ch for ch in sub)
            if sub[0] not in "0." and novo[0] == "0":
                novo = str(rng.randrange(1, 10)) + novo[1:]
            if novo != sub:
                return novo
        return sub
    if tipo == "mes_nome":
        return _mesma_caixa(sub, rng.choice([m for m in _MESES if m != sub.upper()]))
    palavra = {"sobrenome": "sobrenome", "prenome": "prenome", "rua": "rua", "bairro": "bairro",
               "cidade": "cidade", "uf": "uf"}.get(tipo)
    if palavra:
        for _ in range(20):
            novo = geradores.palavra(palavra, antigo=sub)  # prenome novo do gênero do antigo
            if _sem_acento(novo) != _sem_acento(sub):  # JOSÉ → JOSE não é outro nome (e o acento ficava)
                return _mesma_caixa(sub, novo)
    return sub


def _sem_acento(s: str) -> str:
    return ocr.sem_acento(s).upper()


def _t_trecho_trocar(c: _C) -> Resultado:
    gs = c.glifos
    trechos = _trechos(c.categoria, c.texto)
    c.rng.shuffle(trechos)
    for i0, i1, tipo in trechos:
        sub = c.texto[i0:i1]
        novo_sub = c.dado or _novo_trecho(tipo, sub, c.rng)
        if not c.dado and not sub.replace(".", "").isdigit():  # palavra: escolhe uma de largura parecida
            larg = _largura(c, sub)
            opcoes = [novo_sub] + [_novo_trecho(tipo, sub, c.rng) for _ in range(12)]
            novo_sub = min(opcoes, key=lambda o: abs(math.log(max(_largura(c, o), 1.0) / max(larg, 1.0))))
        dentro = [g for g in gs if i0 <= g.i < i1]
        if not dentro or _sem_acento(novo_sub) == _sem_acento(sub) or not plausivel(
                c.categoria, c.texto, c.texto[:i0] + novo_sub + c.texto[i1:]):
            continue
        cauda = [g for g in gs if g.i >= i1]
        X = dentro[0].x0 - c.medir(sub)[0]  # âncora do trecho antigo
        # dígitos têm a mesma largura: se só eles mudam, o trecho novo cabe exatamente no lugar
        no_lugar = len(novo_sub) == len(sub) and all((a.isdigit() and b.isdigit()) or a == b
                                                      for a, b in zip(sub, novo_sub))
        if no_lugar and cauda:
            # nem toda fonte tem dígitos da mesma largura (o 1 da Bahnschrift é estreito): o trecho novo tem de terminar
            # onde o antigo terminava, senão o "4" de "04" caía em cima da "/" que ficou; aí o resto é reescrito junto
            fim_novo = X + c.medir(novo_sub)[2]
            no_lugar = fim_novo <= max(g.x1 for g in dentro) + max(1.0, 0.04 * (c.caixa[3] - c.caixa[1]))
        if no_lugar or not cauda:
            apagar, escrever = dentro, novo_sub
        else:  # largura muda: o resto da linha é reescrito junto
            apagar, escrever = dentro + cauda, novo_sub + c.texto[i1:]
        caixa_r, a = c.desenhar(escrever.rstrip(), ancora=X, humano_=True)
        if caixa_r[2] > c.caixa[2] + 2 and not c.livre((c.caixa[2] + 1, c.caixa[1], caixa_r[2] + 3, c.caixa[3])):
            continue  # o trecho novo, mais largo, passaria por cima do que está à direita
        tela = _Tela(c, _uniao(caixa_r, *[_caixa_glifo(g) for g in apagar]))
        tela.apagar(_furo_glifos(tela, apagar))
        tela.tinta(caixa_r, a, _cor_escrita(c, dentro[0]))
        novo = c.texto[:i0] + novo_sub + c.texto[i1:]
        extra = "" if escrever == novo_sub else " (o resto da linha foi reescrito junto)"
        return tela.resultado(c.meta(novo, [_alt(i0, sub, novo_sub, "trecho")],
                                     f"Só o trecho '{sub}' ({tipo}) trocado por '{novo_sub}': '{c.texto}' → '{novo}'"
                                     f"{extra}.", trecho=tipo))
    raise ErroOperacao("não achei um trecho trocável neste campo")


def _troca_plausivel(c: _C, alvo: str):
    """(glifo, caractere novo) cuja troca deixa o campo com um valor possível."""
    for g in c.alvos(alvo):
        for ch in c.substitutos(g.ch):
            if plausivel(c.categoria, c.texto, c.texto[:g.i] + ch + c.texto[g.i + 1:]):
                return g, ch
    raise ErroOperacao("nenhum caractere trocável neste campo (sem virar valor impossível)")


def _t_caractere_redesenhar(c: _C) -> Resultado:
    g, novo_ch = _troca_plausivel(c, c.alvo())
    if g.ch.isdigit() and novo_ch.isdigit():  # dígitos têm a mesma largura: mesma célula
        caixa_r, a = c.desenhar(novo_ch, ancora=c.ancora(g), humano_=True)
    else:
        caixa_r, a = c.desenhar(novo_ch, centro=g.cx, humano_=True)
    tela = _Tela(c, _uniao(_caixa_glifo(g), caixa_r))
    tela.apagar(_furo_glifos(tela, [g]))
    tela.tinta(caixa_r, a, _cor_escrita(c, g))
    novo = c.texto[:g.i] + novo_ch + c.texto[g.i + 1:]
    return tela.resultado(c.meta(novo, [_alt(g.i, g.ch, novo_ch)],
                                 f"'{g.ch}' (posição {g.i + 1}) apagado e '{novo_ch}' desenhado no lugar na fonte "
                                 f"{c.aj['fonte']}: '{c.texto}' → '{novo}'."))


def _t_caractere_inserir(c: _C) -> Resultado:
    gs = c.glifos
    virgula = c.texto.find(",") if c.categoria == "valor" else -1
    posicoes = [j for j in range(1, len(gs)) if gs[j].i == gs[j - 1].i + 1 and gs[j].ch.isdigit()
                and gs[j - 1].ch.isdigit() and (virgula < 0 or gs[j].i < virgula)]
    c.rng.shuffle(posicoes)
    f = glifos.fonte_pil(c.aj["fonte"], c.aj["tamanho"])
    for j in posicoes:
        a_, b = gs[j - 1], gs[j]
        ch = str(c.rng.randrange(10))
        if not plausivel(c.categoria, c.texto, c.texto[:b.i] + ch + c.texto[b.i:]):
            continue
        d = int(round(f.getlength(ch) * c.aj.get("largura", 100) / 100))
        x0, y0, x1, y1 = c.caixa
        if c.livre((x1 + 1, y0, x1 + d + 3, y1)):
            movidos, dx, X = gs[j:], d, c.ancora(b)
        elif c.livre((x0 - d - 3, y0, x0 - 1, y1)):
            movidos, dx, X = gs[:j], -d, c.ancora(b) - d
        else:
            continue
        caixa_r, alfa_novo = c.desenhar(ch, ancora=X, humano_=True)
        caixas = [_caixa_glifo(g) for g in movidos] + [
            (g.x0 + dx - 3, g.y0 - 3, g.x1 + dx + 3, g.y1 + 3) for g in movidos]
        tela = _Tela(c, _uniao(caixa_r, *caixas))
        tela.apagar(_furo_glifos(tela, movidos))
        for g in movidos:
            rec, al, (gx0, gy0, gx1, gy1) = glifos.alfa_glifo(c.img, g, margem=2)
            tela.tinta((gx0 + dx, gy0, gx1 + dx, gy1), al, rec.astype(np.float32))
        tela.tinta(caixa_r, alfa_novo, _cor_escrita(c, a_))
        novo = c.texto[:b.i] + ch + c.texto[b.i:]
        lado = "o resto do texto foi empurrado para a direita" if dx > 0 else "o começo foi empurrado para a esquerda"
        return tela.resultado(c.meta(novo, [_alt(b.i, "", ch, "insercao")],
                                     f"'{ch}' inserido entre '{a_.ch}' e '{b.ch}' ({lado}): '{c.texto}' → '{novo}'."))
    raise ErroOperacao("não há sequência de dígitos com espaço livre para inserir um dígito")


# ============================================================================ cópia interna / colagem externa
def _fontes_de_glifos(c: _C, externo: bool):
    """Lista de (rótulo da origem, imagem, inventário, caixa a evitar) — mesmo documento ou doadores."""
    if c.documento is None:
        raise ErroOperacao("esta técnica precisa do contexto do documento (campos e OCR)")
    if not externo:
        return [("este documento", c.documento.img, c.documento.inventario(), c.caixa)]
    fontes = [(d.doc, d.img, d.inventario(), None) for d in c.documento.doadores(c.rng, limite=3)]
    if not fontes:
        raise ErroOperacao("não há outro documento no mesmo split para doar letras")
    return fontes


def _altura_esperada(c: _C, de: str, para: str, altura: int) -> float:
    """Altura que ``para`` teria no lugar de ``de`` (letras minúsculas e dígitos têm alturas diferentes)."""
    a, b = glifos.mascara_caractere(de, c.aj["fonte"]), glifos.mascara_caractere(para, c.aj["fonte"])
    if a is None or b is None:
        return float(altura)
    return altura * b.shape[0] / a.shape[0]


def _t_copiar_glifo(c: _C, externo: bool = False) -> Resultado:
    modo = c.opcao("colagem")
    alvo = c.alvo()
    fontes = _fontes_de_glifos(c, externo)
    fonte_nome = c.aj["fonte"]
    for g in c.alvos(alvo):
        cor_alvo = glifos.cor_tinta(c.img, g)
        for ch in c.substitutos(g.ch):
            if not plausivel(c.categoria, c.texto, c.texto[:g.i] + ch + c.texto[g.i + 1:]):
                continue
            h_esp = _altura_esperada(c, g.ch, ch, g.altura)
            achados = []
            for origem, img_src, inv, evitar in fontes:
                for s in inv.candidatos(ch, int(round(h_esp)), tolerancia=0.15, evitar=evitar):
                    if np.abs(glifos.cor_tinta(img_src, s) - cor_alvo).sum() > 90:
                        continue  # tinta de outra cor (rótulo cinza, cabeçalho branco)
                    if glifos.semelhanca(s.tinta, ch, fonte_nome) < 0.45:
                        continue  # OCR errado ou letra de outra fonte
                    achados.append((abs(s.altura - h_esp) + c.rng.random(), origem, img_src, s))
            if not achados:
                continue
            _, origem, img_src, s = min(achados, key=lambda t: t[0])
            escala = h_esp / s.altura if abs(h_esp / s.altura - 1) > 0.03 else 1.0
            # quem cola no Paint arrasta o canto e deforma a letra na horizontal (desleixo)
            escala_x = c.rng.uniform(0.88, 1.12) if modo == "retangulo" and float(c.habitos.get("capricho", 1.0)) > 1.0 else 1.0
            c.fornecedores["colagem"] = modo
            rec, a, (gx0, gy0, gx1, gy1) = _glifo_movel(img_src, s, escala, None if modo in ("retangulo", "papel") else cor_alvo,
                                                        escala_x)
            if modo == "retangulo":  # retângulo amador, mas no tom do papel e da tinta daqui (não caixa branca)
                papel_src = np.asarray(glifos.cor_papel(img_src, s.caixa), np.float32)
                papel_dst = np.asarray(glifos.cor_papel(c.img, g.caixa), np.float32)
                rec = (np.asarray(rec, np.float32) + (cor_alvo - glifos.cor_tinta(img_src, s)) * a[..., None]
                       + (papel_dst - papel_src) * (1 - a)[..., None])
            Y = (c.linha_base - gy1) if ch not in glifos.DESCENDENTES else (g.y0 - gy0)
            reamostrado = escala != 1 or escala_x != 1  # sem escala, a cópia é colada em pixel inteiro
            caixa_p, rec, a = _posicionar(rec, a, g.cx - (gx0 + gx1) / 2, Y, inteiro=not reamostrado)
            tela = _Tela(c, _uniao(_caixa_glifo(g), caixa_p))
            tela.apagar(_furo_glifos(tela, [g]))
            _colar(tela, caixa_p, rec, a, modo)
            novo = c.texto[:g.i] + ch + c.texto[g.i + 1:]
            de_onde = f"do documento {origem}" if externo else "de outro ponto do documento"
            return tela.resultado(c.meta(
                novo, [_alt(g.i, g.ch, ch)],
                f"'{g.ch}' (posição {g.i + 1}) trocado por '{ch}' recortado {de_onde} (colagem: {modo}"
                f"{f', escala {escala:.2f}' if escala != 1 else ''}"
                f"{f', esticado {escala_x:.2f} na horizontal' if escala_x != 1 else ''}): '{c.texto}' → '{novo}'.",
                origem_glifo={"doc": origem if externo else (c.documento.doc if c.documento else None),
                              "caixa": list(s.caixa), "caractere": ch}, escala=round(escala, 3),
                reamostrado=reamostrado))
    raise ErroOperacao("não há letra igual (mesma altura e cor) para copiar" + (" nos doadores" if externo else ""))


def _t_caractere_transpor(c: _C) -> Resultado:
    gs = c.glifos
    pares = [(a, b) for a, b in zip(gs, gs[1:]) if b.i == a.i + 1 and a.ch != b.ch and a.ch.isdigit() and b.ch.isdigit()
             and plausivel(c.categoria, c.texto, c.texto[:a.i] + b.ch + a.ch + c.texto[b.i + 1:])]
    if not pares:
        raise ErroOperacao("não há dois dígitos vizinhos diferentes para inverter")
    a, b = c.rng.choice(pares)
    tela = _Tela(c, _uniao(_caixa_glifo(a, 4), _caixa_glifo(b, 4)))
    tela.apagar(_furo_glifos(tela, [a, b]))
    for de, para in ((a, b), (b, a)):
        rec, al, (gx0, gy0, gx1, gy1) = _glifo_movel(c.img, de)
        caixa_p, rec, al = _posicionar(rec, al, para.cx - (gx0 + gx1) / 2, de.y0 - gy0, inteiro=True)
        tela.tinta(caixa_p, al, rec)
    novo = c.texto[:a.i] + b.ch + a.ch + c.texto[b.i + 1:]
    return tela.resultado(c.meta(novo, [_alt(a.i, a.ch + b.ch, b.ch + a.ch, "transposicao")],
                                 f"'{a.ch}' e '{b.ch}' (posições {a.i + 1}-{b.i + 1}) trocaram de lugar, movendo os "
                                 f"próprios glifos: '{c.texto}' → '{novo}'.", reamostrado=False))


def _altura_maiuscula(img, campo: dict, fonte: str | None = None) -> tuple[float, float]:
    """(altura das letras sem descendentes, linha de base) de um campo; cai para a caixa se não segmentar."""
    x0, y0, x1, y1 = campo["caixa"]
    try:
        gs = glifos.segmentar(img, campo["caixa"], campo["texto"], fonte)
        bons = [g for g in gs if g.ch.isalnum() and g.ch not in glifos.DESCENDENTES]
        if bons:
            return float(np.median([g.altura for g in bons])), float(np.median([g.y1 for g in bons]))
    except ErroOperacao:
        pass
    return float(y1 - y0), float(y1)


def _t_copiar_campo(c: _C, externo: bool = False) -> Resultado:
    modo = c.opcao("colagem")
    if c.documento is None:
        raise ErroOperacao("esta técnica precisa do contexto do documento (campos e OCR)")
    alt_alvo, _ = _altura_maiuscula(c.img, {"caixa": c.caixa, "texto": c.texto}, c.aj["fonte"])
    cands, outro_estilo = [], False
    origens = list(c.documento.doadores(c.rng, limite=5)) if externo else [c.documento]
    if externo and not origens:
        raise ErroOperacao("não há outro documento no mesmo split para doar o campo")
    for d in origens:
        for f in d.campos:
            if f.get("tipo") != "texto" or f.get("categoria") != c.categoria:
                continue
            if f["texto"].strip() == c.texto or not f["texto"].strip():
                continue
            fx0, fy0, fx1, fy1 = f["caixa"]
            if not externo and not (fx1 <= c.caixa[0] or fx0 >= c.caixa[2] or fy1 <= c.caixa[1] or fy0 >= c.caixa[3]):
                continue  # é o próprio campo
            if not externo and _caixa_das_letras(f["texto"]) != _caixa_das_letras(c.texto):
                outro_estilo = True
                continue  # nome em caixa mista colado numa linha em maiúsculas
            razao = alt_alvo / max(1.0, (fy1 - fy0))
            if not 0.6 <= razao <= 1.6:
                continue
            nota = abs(len(f["texto"]) - len(c.texto)) + 8 * abs(1 - razao) + c.rng.random()
            cands.append((nota, d, f))
    if not cands:
        raise ErroOperacao("nenhum campo do documento com o mesmo estilo (caixa das letras) para copiar" if outro_estilo
                           else "não há outro campo da mesma categoria para copiar" + (" nos doadores" if externo else ""))
    cands.sort(key=lambda t: t[0])
    cor_alvo = glifos.cor_tinta(c.img, mascara_tinta(c.img[c.caixa[1]:c.caixa[3], c.caixa[0]:c.caixa[2]]).astype(bool),
                                c.caixa)
    papel_alvo = np.asarray(glifos.cor_papel(c.img, c.caixa), np.float32)
    nitidez_alvo = glifos.nitidez_tinta(c.img, c.caixa)
    for _, d, f in cands if not externo else cands[:6]:
        alt_src, base_src = _altura_maiuscula(d.img, f)
        escala = alt_alvo / alt_src if abs(alt_alvo / alt_src - 1) > 0.03 else 1.0
        if not 0.75 <= escala <= 1.33:
            continue
        fx0, fy0, fx1, fy1 = f["caixa"]
        cor_doador = glifos.cor_tinta(d.img, mascara_tinta(d.img[fy0:fy1, fx0:fx1]).astype(bool), f["caixa"])
        if not externo and not _mesmo_estilo(escala, cor_doador, cor_alvo, glifos.nitidez_tinta(d.img, f["caixa"]),
                                             nitidez_alvo):
            continue
        sx0, sy0, sx1, sy1 = f["caixa"]
        m = 3
        rx0, ry0 = max(0, sx0 - m), max(0, sy0 - m)
        rx1, ry1 = min(d.img.shape[1], sx1 + m), min(d.img.shape[0], sy1 + m)
        rec = d.img[ry0:ry1, rx0:rx1].astype(np.float32)
        papel_src = glifos.cor_papel(d.img, f["caixa"])
        tinta_src = mascara_tinta(d.img[ry0:ry1, rx0:rx1]).astype(bool)
        if not tinta_src.any():
            continue
        tinta_cor = glifos.cor_tinta(d.img, tinta_src, (rx0, ry0, rx1, ry1))
        if (tinta_cor.mean() > papel_src.mean()) != (cor_alvo.mean() > papel_alvo.mean()):
            continue  # contraste invertido (texto claro no cabeçalho escuro): viraria texto laranja no branco
        a = glifos.alfa(rec, papel_src, tinta_cor) * _dilatar(tinta_src, 1)
        rec = rec + (cor_alvo - tinta_cor) * a[..., None]
        if modo == "retangulo":  # o retângulo é amador, mas com o tom do papel de destino (não cinza no branco)
            rec = rec + (papel_alvo - papel_src) * (1 - a)[..., None]
        if escala != 1.0:
            h, w = a.shape
            nw, nh = max(1, int(round(w * escala))), max(1, int(round(h * escala)))
            rec = cv2.resize(rec, (nw, nh), interpolation=cv2.INTER_CUBIC)
            a = np.clip(cv2.resize(a, (nw, nh), interpolation=cv2.INTER_LINEAR), 0, 1)
        tinta_x0 = (sx0 - rx0) * escala
        tinta_x1 = (sx1 - rx0) * escala
        if c.categoria == "valor":  # totais costumam ser alinhados pela direita
            X = c.caixa[2] - tinta_x1
        else:
            X = c.caixa[0] - tinta_x0
        Y = c.linha_base - (base_src - ry0) * escala
        caixa_p, rec, a = _posicionar(rec, a, X, Y, inteiro=escala == 1.0)
        # o que sobra para fora do campo antigo precisa cair em papel livre
        px0, px1 = caixa_p[0] + int(tinta_x0), caixa_p[0] + int(math.ceil(tinta_x1))
        if px0 < c.caixa[0] - 2 and not c.livre((px0, c.caixa[1], c.caixa[0] - 1, c.caixa[3])):
            continue
        if px1 > c.caixa[2] + 2 and not c.livre((c.caixa[2] + 1, c.caixa[1], px1, c.caixa[3])):
            continue
        x0, y0, x1, y1 = c.caixa
        tela = _Tela(c, _uniao((x0 - 4, y0 - 4, x1 + 4, y1 + 4), caixa_p))
        furo = tela.mascara(c.caixa, mascara_tinta(c.img[y0:y1, x0:x1]).astype(bool))
        tela.apagar(_dilatar(furo, 2))
        _colar(tela, caixa_p, rec, a, modo)
        novo = f["texto"]
        de_onde = f"do documento {d.doc} (mesmo split)" if externo else "de outro campo do mesmo documento"
        return tela.resultado(c.meta(
            novo, [_alt(0, c.texto, novo, "campo")],
            f"'{c.texto}' apagado e coberto pela imagem de '{novo}' recortada {de_onde} (colagem: {modo}"
            f"{f', escala {escala:.2f}' if escala != 1 else ''}).",
            origem_campo={"doc": d.doc, "caixa": list(f["caixa"]), "texto": novo}, escala=round(escala, 3),
            reamostrado=escala != 1.0))
    raise ErroOperacao("nenhum campo doador coube no lugar (altura ou espaço livre)" if externo else
                       "nenhum campo do documento com o mesmo estilo (altura da maiúscula, tinta, caixa das letras e "
                       "nitidez) coube no lugar")


def _caixa_das_letras(s: str) -> str:
    letras = [ch for ch in s if ch.isalpha()]
    return "sem letras" if not letras else "maiusculas" if all(ch.isupper() for ch in letras) else "mista"


def _mesmo_estilo(escala: float, cor_doador, cor_alvo, nitidez_doador: float, nitidez_alvo: float) -> bool:
    """O campo doador da cópia interna tem o estilo do alvo: altura da maiúscula a ±12%, escuridão da tinta a ±15
    níveis e nitidez parecida (±25%). Colar um campo cinza e borrado numa linha escura e nítida, ou um bem maior
    reescalado, era a própria pista."""
    luma = np.float32([0.299, 0.587, 0.114])
    escuridao = abs(float(np.asarray(cor_doador, np.float32) @ luma) - float(np.asarray(cor_alvo, np.float32) @ luma))
    if abs(escala - 1) > 0.12 or escuridao > 15:
        return False
    if nitidez_doador > 0 and nitidez_alvo > 0 and not 0.8 <= nitidez_doador / nitidez_alvo <= 1.25:
        return False
    return True


_BLOCO = ("endereço", "bairro", "cep", "cidade/uf")


def _bloco_de(campos: list[dict], alvo_id, h_ref: float) -> list[dict]:
    """O bloco de endereço em volta do campo ``alvo_id``: campos de endereço/bairro/CEP/cidade em linhas
    vizinhas (vão vertical pequeno, alinhados pela esquerda), de cima para baixo."""
    cands = sorted((f for f in campos if f.get("categoria") in _BLOCO and f.get("tipo", "texto") == "texto"
                    and str(f.get("texto", "")).strip()), key=lambda f: f["caixa"][1])
    i = next((k for k, f in enumerate(cands) if f.get("id") == alvo_id), None)
    if i is None:
        return []

    def vizinho(a, b):  # b logo abaixo de a, na mesma coluna
        return -0.3 * h_ref <= b["caixa"][1] - a["caixa"][3] <= 2.5 * h_ref and abs(b["caixa"][0] - a["caixa"][0]) <= 3 * h_ref

    j0 = i
    while j0 > 0 and vizinho(cands[j0 - 1], cands[j0]):
        j0 -= 1
    j1 = i
    while j1 + 1 < len(cands) and vizinho(cands[j1], cands[j1 + 1]):
        j1 += 1
    return cands[j0:j1 + 1]


def _t_bloco_colagem_externa(c: _C) -> Resultado:
    """Várias linhas de uma vez: o bloco de endereço de outro documento colado por cima do daqui."""
    modo = c.opcao("colagem")
    if c.documento is None:
        raise ErroOperacao("esta técnica precisa do contexto do documento (campos e OCR)")
    h_ref = float(c.caixa[3] - c.caixa[1])
    bloco = _bloco_de(c.documento.campos, c.campo_id, h_ref)
    if len(bloco) < 2:
        raise ErroOperacao("o campo não faz parte de um bloco de endereço (precisa de 2+ linhas vizinhas)")
    cats = [f["categoria"] for f in bloco]
    u = _uniao(*[f["caixa"] for f in bloco])
    alt_alvo = float(u[3] - u[1])
    cands = []
    for d in c.documento.doadores(c.rng, limite=5):
        vistos = set()
        for f in d.campos:
            if f.get("categoria") not in cats or f.get("id") in vistos:
                continue
            bd = _bloco_de(d.campos, f.get("id"), float(f["caixa"][3] - f["caixa"][1]))
            vistos |= {g.get("id") for g in bd}
            if len(bd) < 2 or [g["categoria"] for g in bd] != cats:
                continue
            ud = _uniao(*[g["caixa"] for g in bd])
            razao = alt_alvo / max(1.0, ud[3] - ud[1])
            if 0.75 <= razao <= 1.33:
                cands.append((abs(1 - razao) + c.rng.random() * 0.1, d, bd, ud))
    if not cands:
        raise ErroOperacao("nenhum documento do mesmo split tem um bloco de endereço com as mesmas linhas")
    cands.sort(key=lambda t: t[0])
    papel_alvo = np.asarray(glifos.cor_papel(c.img, u), np.float32)
    tinta_alvo = mascara_tinta(c.img[u[1]:u[3], u[0]:u[2]]).astype(bool)
    cor_alvo = glifos.cor_tinta(c.img, tinta_alvo, u) if tinta_alvo.any() else np.zeros(3, np.float32)
    for _, d, bd, ud in cands[:4]:
        m = 3
        rx0, ry0 = max(0, ud[0] - m), max(0, ud[1] - m)
        rx1, ry1 = min(d.img.shape[1], ud[2] + m), min(d.img.shape[0], ud[3] + m)
        rec = d.img[ry0:ry1, rx0:rx1].astype(np.float32)
        papel_src = glifos.cor_papel(d.img, ud)
        tinta_src = mascara_tinta(d.img[ry0:ry1, rx0:rx1]).astype(bool)
        if not tinta_src.any():
            continue
        tinta_cor = glifos.cor_tinta(d.img, tinta_src, (rx0, ry0, rx1, ry1))
        a = glifos.alfa(rec, papel_src, tinta_cor) * _dilatar(tinta_src, 1)
        rec = rec + (cor_alvo - tinta_cor) * a[..., None]
        if modo in ("retangulo", "papel"):
            rec = rec + (papel_alvo - papel_src) * (1 - a)[..., None]
        escala = alt_alvo / max(1.0, ud[3] - ud[1])
        escala = escala if abs(escala - 1) > 0.03 else 1.0
        if escala != 1.0:
            h, w = a.shape
            nw, nh = max(1, int(round(w * escala))), max(1, int(round(h * escala)))
            rec = cv2.resize(rec, (nw, nh), interpolation=cv2.INTER_CUBIC)
            a = np.clip(cv2.resize(a, (nw, nh), interpolation=cv2.INTER_LINEAR), 0, 1)
        X = u[0] - (ud[0] - rx0) * escala
        Y = u[1] - (ud[1] - ry0) * escala
        caixa_p, rec, a = _posicionar(rec, a, X, Y, inteiro=escala == 1.0)
        if caixa_p[2] > u[2] + 2 and not c.livre((u[2] + 1, u[1], caixa_p[2], u[3])):
            continue  # o bloco colado é mais largo e cobriria o que está à direita
        if caixa_p[3] > u[3] + 2 and not c.livre((u[0], u[3] + 1, u[2], caixa_p[3])):
            continue  # mais alto: cobriria a linha de baixo
        tela = _Tela(c, _uniao((u[0] - 4, u[1] - 4, u[2] + 4, u[3] + 4), caixa_p))
        tela.apagar(_dilatar(tela.mascara(u, tinta_alvo), 2))
        _colar(tela, caixa_p, rec, a, modo)
        c.fornecedores["colagem"] = modo
        antigo = " | ".join(f["texto"] for f in bloco)
        novo = " | ".join(g["texto"] for g in bd)
        return tela.resultado(c.meta(
            novo, [_alt(0, antigo, novo, "bloco")],
            f"Bloco de endereço ({', '.join(cats)}) apagado e coberto pelo bloco inteiro do documento {d.doc} "
            f"(mesmo split; colagem: {modo}{f', escala {escala:.2f}' if escala != 1 else ''}): '{antigo}' → '{novo}'.",
            texto_antigo=antigo, origem_campo={"doc": d.doc, "caixa": list(ud), "texto": novo}, escala=round(escala, 3),
            linhas=len(bloco), reamostrado=escala != 1.0))
    raise ErroOperacao("nenhum bloco doador coube no lugar (altura ou espaço livre)")


# ============================================================================ remoção
def _t_caractere_apagar(c: _C) -> Resultado:
    modo = c.opcao("vao")
    alvo = c.alvo()
    gs = c.glifos
    virgula = c.texto.find(",") if c.categoria == "valor" else -1
    cands = [g for g in c.alvos(alvo) if (virgula < 0 or g.i < virgula)
             and plausivel(c.categoria, c.texto, (c.texto[:g.i] + c.texto[g.i + 1:]).strip())]
    if not cands:
        raise ErroOperacao("não há dígito apagável na parte inteira do valor")
    g = cands[0]
    j = gs.index(g)
    esq, dir_ = gs[:j], gs[j + 1:]
    novo = (c.texto[:g.i] + c.texto[g.i + 1:]).strip()
    if modo == "deixar" or not (esq or dir_):
        tela = _Tela(c, _caixa_glifo(g))
        tela.apagar(_furo_glifos(tela, [g]))
        return tela.resultado(c.meta(novo, [_alt(g.i, g.ch, "", "remocao")],
                                     f"'{g.ch}' (posição {g.i + 1}) apagado e o vão deixado: '{c.texto}' → '{novo}'."))
    # fecha o vão movendo o lado com menos tinta; mas se a letra apagada encosta num espaço entre
    # palavras ("10[4] APTO"), move o lado de DENTRO da palavra, senão o espaço some ("10APTO")
    espaco = max(3, 0.3 * (c.caixa[3] - c.caixa[1]))
    antes_espaco = bool(dir_) and dir_[0].x0 - g.x1 > espaco
    depois_espaco = bool(esq) and g.x0 - esq[-1].x1 > espaco
    if antes_espaco and esq:
        movidos, dx = esq, g.x1 - esq[-1].x1
    elif depois_espaco and dir_:
        movidos, dx = dir_, -(dir_[0].x0 - g.x0)
    elif dir_ and (not esq or sum(x.largura for x in dir_) <= sum(x.largura for x in esq)):
        movidos, dx = dir_, -(dir_[0].x0 - g.x0)
    else:
        movidos, dx = esq, g.x1 - esq[-1].x1
    caixas = [_caixa_glifo(x) for x in movidos + [g]] + [
        (x.x0 + dx - 3, x.y0 - 3, x.x1 + dx + 3, x.y1 + 3) for x in movidos]
    tela = _Tela(c, _uniao(*caixas))
    tela.apagar(_furo_glifos(tela, movidos + [g]))
    for x in movidos:
        rec, al, (gx0, gy0, gx1, gy1) = glifos.alfa_glifo(c.img, x, margem=2)
        tela.tinta((gx0 + dx, gy0, gx1 + dx, gy1), al, rec.astype(np.float32))
    lado = "o resto do texto foi puxado para a esquerda" if dx < 0 else "o começo foi empurrado para a direita"
    return tela.resultado(c.meta(novo, [_alt(g.i, g.ch, "", "remocao")],
                                 f"'{g.ch}' (posição {g.i + 1}) apagado e o vão fechado ({lado}): "
                                 f"'{c.texto}' → '{novo}'."))


def _inicio_parte_final(cat: str | None, s: str) -> int | None:
    """Onde começa a parte final que dá para apagar sem deixar o resto sem sentido: o complemento do
    endereço (APTO 12, CASA 2...) ou o último sobrenome de um nome com 3+ palavras."""
    if cat == "endereço":
        m = re.search(r"\s+(?:APTO|AP|CASA|BLOCO|BL|SALA|LOTE|FUNDOS)\b.*$", s, re.I)
        return m.start() if m else None
    palavras = list(re.finditer(r"\S+", s))
    if cat == "nome" and len(palavras) >= 3:
        return palavras[-1].start()
    return None


def _t_campo_remover(c: _C) -> Resultado:
    parte = c.opcao("parte")
    x0, y0, x1, y1 = c.caixa
    ini = _inicio_parte_final(c.categoria, c.texto) if parte == "final" else None
    if ini is not None:
        gs = [g for g in c.glifos if g.i >= ini]
        tela = _Tela(c, _uniao(*[_caixa_glifo(g) for g in gs]))
        tela.apagar(_furo_glifos(tela, gs))
        novo = c.texto[:ini].rstrip(" ,-")
        tirado = c.texto[ini:].strip()
        return tela.resultado(c.meta(novo, [_alt(ini, tirado, "", "remocao")],
                                     f"Parte final '{tirado}' apagada com preenchimento inteligente: "
                                     f"'{c.texto}' → '{novo}'."))
    c.escolhas["parte"] = "campo"
    # a caixa do OCR corta serifa e acento, e o limiar da tinta deixa o halo do antialias (o nome
    # ficava legível como fantasma): procura tinta numa caixa maior, só os pedaços que tocam o campo,
    # e alarga o furo pela altura da letra
    h = y1 - y0
    py, px = max(2, int(round(0.25 * h))), max(2, int(round(0.1 * h)))
    X0, Y0, X1, Y1 = max(0, x0 - px), max(0, y0 - py), min(c.W, x1 + px), min(c.H, y1 + py)
    try:  # as letras com o acento solto (glifos.com_sinais): o agudo do Ú fica longe da caixa e não a toca
        gs = c.glifos
    except ErroOperacao:
        gs = []
    if gs:
        X0, Y0 = max(0, min([X0] + [g.x0 - 2 for g in gs])), max(0, min([Y0] + [g.y0 - 2 for g in gs]))
        X1, Y1 = min(c.W, max([X1] + [g.x1 + 2 for g in gs])), min(c.H, max([Y1] + [g.y1 + 2 for g in gs]))
    rec = c.img[Y0:Y1, X0:X1]
    _, rot = cv2.connectedComponents(mascara_tinta(rec))
    tocam = np.unique(rot[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0])
    tinta = np.isin(rot, tocam[tocam > 0])
    for g in gs:
        tinta[g.y0 - Y0:g.y1 - Y0, g.x0 - X0:g.x1 - X0] |= g.tinta
    # numa foto/scan borrado o halo da letra fica abaixo do limiar da tinta e sobrava um fantasma: a tinta
    # FRACA (mais de 12 níveis longe do papel) encostada na forte entra no furo, e o furo cresce com o desfoque
    try:
        desf = c.desfoque
    except ErroOperacao:
        desf = 0.0
    papel = np.asarray(glifos.cor_papel(c.img, c.caixa), np.float32)
    fraca = (np.abs(rec.astype(np.float32) - papel).max(2) > 12) & _dilatar(tinta, 3)
    tinta = tinta | fraca
    r = max(2, min(8, int(round(0.1 * h)) + int(round(desf))))
    tela = _Tela(c, (X0 - r - 2, Y0 - r - 2, X1 + r + 2, Y1 + r + 2))
    furo = tela.mascara((X0, Y0, X1, Y1), tinta)
    tela.apagar(_dilatar(furo, r))
    return tela.resultado(c.meta("", [_alt(0, c.texto, "", "remocao")],
                                 f"Campo '{c.texto}' apagado por inteiro com preenchimento inteligente (PatchMatch)."))


def _t_campo_corretivo(c: _C) -> Resultado:
    fundo = c.opcao("fundo")
    forma = c.opcao("forma")
    x0, y0, x1, y1 = c.caixa
    h = y1 - y0
    m = max(2, int(round(0.2 * h)))
    j = [c.rng.randint(0, m) for _ in range(4)]
    cor = np.array([255, 255, 255], np.float32) if fundo == "branco" else glifos.cor_papel(c.img, c.caixa)
    if forma == "retangulo":
        tela = _Tela(c, (x0 - m - j[0], y0 - m - j[1], x1 + m + j[2], y1 + m + j[3]))
        tela.opaco(tela.caixa, np.broadcast_to(cor, tela.px.shape))
        return tela.resultado(c.meta("", [_alt(0, c.texto, "", "remocao")],
                                     f"Campo '{c.texto}' coberto por um retângulo liso "
                                     f"{'branco' if fundo == 'branco' else 'da cor do papel'} (corretivo, sem textura)."))
    M = int(math.ceil(1.1 * h))
    tela = _Tela(c, (x0 - M, y0 - M, x1 + M, y1 + M))
    H_, W_ = tela.pegada.shape
    ys, xs = np.mgrid[0:H_, 0:W_].astype(np.float32)
    ox, oy = tela.caixa[0], tela.caixa[1]
    alfa = np.zeros((H_, W_), np.float32)
    if forma == "pincelada":  # pinceladas redondas do app, lado a lado, com a borda suave do antialias
        r = h * c.rng.uniform(0.55, 0.75)
        passo = 0.45 * h
        n = int(math.ceil((x1 - x0) / passo)) + 2
        for i in range(n):
            cx = x0 - 0.3 * h + i * passo + c.rng.uniform(-0.1, 0.1) * h - ox
            cy = (y0 + y1) / 2 + c.rng.uniform(-0.15, 0.15) * h - oy
            d = np.hypot(xs + 0.5 - cx, ys + 0.5 - cy) - r
            alfa = np.maximum(alfa, np.clip(0.5 - d / 0.9, 0, 1))
        cor_px = np.broadcast_to(cor, tela.px.shape).astype(np.float32)
        tela.tinta(tela.caixa, alfa, cor_px)
        return tela.resultado(c.meta("", [_alt(0, c.texto, "", "remocao")],
                                     f"Campo '{c.texto}' coberto por pinceladas {'brancas' if fundo == 'branco' else 'da cor do papel'} "
                                     "do app (borda irregular, sem textura)."))
    # corretivo líquido: 2 a 4 pinceladas alongadas, tinta quase branca com textura, relevo (sombra embaixo/direita)
    for _ in range(c.rng.randint(2, 4)):
        cx = (x0 + x1) / 2 + c.rng.uniform(-0.15, 0.15) * (x1 - x0) - ox
        cy = (y0 + y1) / 2 + c.rng.uniform(-0.25, 0.25) * h - oy
        rx, ry = (x1 - x0) / 2 + h * c.rng.uniform(0.3, 0.6), h * c.rng.uniform(0.65, 0.95)
        ang = math.radians(c.rng.uniform(-4, 4))
        px, py = xs + 0.5 - cx, ys + 0.5 - cy
        qx, qy = math.cos(ang) * px + math.sin(ang) * py, -math.sin(ang) * px + math.cos(ang) * py
        d = (np.hypot(qx / rx, qy / ry) - 1) * min(rx, ry)
        alfa = np.maximum(alfa, np.clip(0.5 - d / 1.3, 0, 1))
    rng_np = np.random.default_rng(c.semente)
    base = np.array([252, 251, 247], np.float32) + rng_np.uniform(-4, 3, 3)
    # textura: o grão do papel em volta (o corretivo foi escaneado/fotografado junto com o papel) e o relevo leve
    # da camada, que seca desigual. A textura antiga (ruído sem o espectro do papel, somado a uma base quase 255)
    # era cortada no branco: o corretivo saía liso, com variância quase zero
    relevo = cv2.GaussianBlur(rng_np.normal(0, 1, (H_, W_)).astype(np.float32), (0, 0), max(1.0, 0.3 * h))
    relevo *= 1.2 / max(float(relevo.std()), 1e-3)
    textura = _grao_do_papel(c, tela.caixa) + relevo
    base = np.minimum(base, 255.0 - 3 * float(textura.std()))  # o grão não é cortado em 255
    cor_px = np.clip(base[None, None, :] + textura[..., None], 0, 255)
    sombra = np.clip(np.roll(np.roll(alfa, 2, 0), 2, 1) - alfa, 0, 1)  # relevo: a borda de baixo/direita faz sombra
    sombra = cv2.GaussianBlur(sombra, (0, 0), 1.2) * 0.35
    tela.px *= (1 - sombra[..., None])
    tela.pegada |= sombra > 0.01
    tela.tinta(tela.caixa, alfa, cor_px)
    return tela.resultado(c.meta("", [_alt(0, c.texto, "", "remocao")],
                                 f"Campo '{c.texto}' coberto com corretivo líquido no papel (pinceladas quase brancas com "
                                 "o grão do papel e relevo) e fotografado de novo."))


def _grao_do_papel(c: _C, caixa, minimo: float = 0.8) -> np.ndarray:
    """Grão (luma) do tamanho de ``caixa`` com o espectro e o desvio do papel em volta dela (pericia), nunca abaixo
    de ``minimo``: um objeto físico capturado junto com o papel leva o mesmo grão da captura."""
    x0, y0, x1, y1 = caixa
    m = 30
    X0, Y0, X1, Y1 = max(0, x0 - m), max(0, y0 - m), min(c.W, x1 + m), min(c.H, y1 + m)
    rec = c.img[Y0:Y1, X0:X1]
    peg = np.zeros(rec.shape[:2], bool)
    peg[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = True
    anel = pericia._anel_papel(rec, peg, m)
    s_ref, ruido = 0.0, np.zeros(peg.shape, np.float32)
    if anel.sum() >= 80:
        s_ref, residuo = pericia._grao(rec.astype(np.float32) @ np.float32([0.299, 0.587, 0.114]), anel)
        ruido = pericia._sintetizar(residuo, anel, c.semente)
    s_r = pericia._grao(ruido, np.ones(peg.shape, bool))[0]
    if s_r < 1e-3:  # papel liso (PDF): um grão fino qualquer, no mínimo
        ruido = np.random.default_rng(c.semente).normal(0, 1, peg.shape).astype(np.float32)
        ruido = cv2.GaussianBlur(ruido, (0, 0), 0.7)
        s_r = pericia._grao(ruido, np.ones(peg.shape, bool))[0]
    return (ruido * (max(s_ref, minimo) / s_r))[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0]


# ============================================================================ esconder, rabiscar, borrar
_PELES = ((224, 172, 138), (198, 134, 96), (160, 102, 70), (120, 72, 48), (240, 196, 170), (90, 55, 40))


def _sdf_ret(xs, ys, cx, cy, w, h, raio, ang):
    """Distância assinada a um retângulo de cantos redondos, girado ``ang`` radianos."""
    px, py = xs + 0.5 - cx, ys + 0.5 - cy
    qx, qy = math.cos(ang) * px + math.sin(ang) * py, -math.sin(ang) * px + math.cos(ang) * py
    dx, dy = np.abs(qx) - (w / 2 - raio), np.abs(qy) - (h / 2 - raio)
    return np.hypot(np.maximum(dx, 0), np.maximum(dy, 0)) + np.minimum(np.maximum(dx, dy), 0) - raio


def _t_campo_ocultar(c: _C) -> Resultado:
    objeto = c.opcao("objeto")
    x0, y0, x1, y1 = c.caixa
    h, w = y1 - y0, x1 - x0
    rng = c.rng
    rng_np = np.random.default_rng(c.semente)
    if objeto == "dedo":
        # a ponta cobre o campo; o corpo do dedo sai para a borda mais perto (quem segura o papel ou o celular)
        larg = h * rng.uniform(1.8, 2.8)
        dist = {"baixo": c.H - y1, "esquerda": x0, "direita": c.W - x1}
        if w > 1.1 * larg:  # campo comprido: o dedo (polegar) deitado ao longo dele, entrando pelo lado mais perto
            dist.pop("baixo")
        lado = min(dist, key=dist.get)
        # a ponta passa um pouco do fim do campo; de baixo, a ponta fica no meio do campo
        cx = x0 + w * rng.uniform(0.35, 0.65) if lado == "baixo" else (x0 - 0.35 * larg if lado == "direita" else x1 + 0.35 * larg)
        cy = (y0 + y1) / 2 + rng.uniform(-0.15, 0.15) * h
        if lado == "baixo":
            caixa_t = (cx - larg, cy - 0.8 * larg, cx + larg, c.H)
        elif lado == "direita":
            caixa_t = (cx - 0.8 * larg, cy - larg, c.W, cy + larg)
        else:
            caixa_t = (0, cy - larg, cx + 0.8 * larg, cy + larg)
    else:
        mx, my = {"adesivo": (0.35, 0.3), "postit": (0.9, 0.9), "fita": (1.2, 0.45)}[objeto]
        mx, my = h * mx * rng.uniform(0.8, 1.3), h * my * rng.uniform(0.8, 1.3)
        caixa_t = (x0 - mx - 4, y0 - my - 4, x1 + mx + 4, y1 + my + 4)
    tela = _Tela(c, caixa_t)
    H_, W_ = tela.pegada.shape
    ys, xs = np.mgrid[0:H_, 0:W_].astype(np.float32)
    ox, oy = tela.caixa[0], tela.caixa[1]
    if objeto == "dedo":
        cxl, cyl = cx - ox, cy - oy
        if lado == "baixo":
            corpo = _sdf_ret(xs, ys, cxl, cyl + H_ / 2, larg, H_ + larg, larg / 2, 0.0)
        elif lado == "direita":
            corpo = _sdf_ret(xs, ys, cxl + W_ / 2, cyl, W_ + larg, larg * 0.9, larg * 0.45, 0.0)
        else:
            corpo = _sdf_ret(xs, ys, cxl - W_ / 2, cyl, W_ + larg, larg * 0.9, larg * 0.45, 0.0)
        alfa = np.clip(0.5 - corpo / 1.6, 0, 1)
        pele = np.array(rng.choice(_PELES), np.float32) * rng.uniform(0.9, 1.08)
        # sombreado: mais claro no meio do dedo, mais escuro nas laterais; textura fina
        eixo = (xs - cxl) / (larg / 2) if lado == "baixo" else (ys - cyl) / (larg * 0.45)
        sombreado = 1.0 - 0.28 * np.clip(np.abs(eixo), 0, 1) ** 2
        cor_px = np.clip(pele[None, None, :] * sombreado[..., None] + rng_np.normal(0, 2.5, (H_, W_, 1)), 0, 255)
        nome = "um dedo"
    else:
        ang = math.radians(rng.uniform(-3, 3) if objeto != "postit" else rng.uniform(-9, 9))
        cxl, cyl = (x0 + x1) / 2 - ox + rng.uniform(-0.1, 0.1) * h, (y0 + y1) / 2 - oy + rng.uniform(-0.1, 0.1) * h
        if objeto == "adesivo":
            cores = ((250, 250, 248), (250, 250, 248), (255, 236, 90), (235, 70, 60), (70, 130, 230))
            cor_b = np.array(rng.choice(cores), np.float32)
            d = _sdf_ret(xs, ys, cxl, cyl, w + 2 * (tela.caixa[2] - x1 - 4) * 0.9, h + 2 * (y0 - tela.caixa[1] - 4) * 0.9, 0.3 * h, ang)
            alfa = np.clip(0.5 - d / 0.8, 0, 1)
            cor_px = np.clip(cor_b[None, None, :] + rng_np.normal(0, 1.5, (H_, W_, 1)), 0, 255)
            nome = "um adesivo"
        elif objeto == "postit":
            cores = ((255, 241, 118), (255, 241, 118), (255, 183, 197), (178, 235, 160), (160, 210, 255))
            cor_b = np.array(rng.choice(cores), np.float32)
            d = _sdf_ret(xs, ys, cxl, cyl, W_ - 8, H_ - 8, 2.0, ang)
            alfa = np.clip(0.5 - d / 0.9, 0, 1)
            grad = 1.0 - 0.06 * (ys / max(1, H_))  # papel levemente mais escuro embaixo
            textura = cv2.GaussianBlur(rng_np.normal(0, 4, (H_, W_)).astype(np.float32), (0, 0), 1.2)
            cor_px = np.clip(cor_b[None, None, :] * grad[..., None] + textura[..., None], 0, 255)
            nome = "um post-it"
        else:  # fita adesiva de papel (crepe): opaca, bege, com fibras
            cor_b = np.array((226, 214, 186), np.float32) * rng.uniform(0.95, 1.05)
            d = _sdf_ret(xs, ys, cxl, cyl, W_ - 8, h + 2 * (y0 - tela.caixa[1] - 4) * 0.95, 1.0, ang)
            alfa = np.clip(0.5 - d / 0.7, 0, 1)
            fibras = cv2.GaussianBlur(rng_np.normal(0, 7, (H_, W_)).astype(np.float32), (0, 0), sigmaX=2.5, sigmaY=0.6)
            cor_px = np.clip(cor_b[None, None, :] + fibras[..., None], 0, 255)
            nome = "fita adesiva"
    # sombra do objeto sobre o papel (deslocada para baixo/direita), fora do objeto
    sombra = cv2.GaussianBlur(np.roll(np.roll(alfa, 3, 0), 2, 1), (0, 0), 1.8) * (1 - alfa) * 0.4
    tela.px *= (1 - sombra[..., None])
    tela.pegada |= sombra > 0.01
    tela.tinta(tela.caixa, alfa, cor_px)
    return tela.resultado(c.meta("", [_alt(0, c.texto, "", "ocultacao")],
                                 f"Campo '{c.texto}' escondido por {nome} na hora da foto (sem edição digital)."))


def _t_campo_rabiscar(c: _C) -> Resultado:
    tinta = c.opcao("tinta")
    x0, y0, x1, y1 = c.caixa
    h, w = y1 - y0, x1 - x0
    rng = c.rng
    m = int(math.ceil(0.45 * h)) + 2
    tela = _Tela(c, (x0 - m, y0 - m, x1 + m, y1 + m))
    H_, W_ = tela.pegada.shape
    ox, oy = tela.caixa[0], tela.caixa[1]
    S = 3  # desenha ampliado e reduz: borda suave
    tela_s = np.zeros((H_ * S, W_ * S), np.uint8)
    if tinta == "caneta":  # rabisco denso: muitas idas e vindas finas, até não dar para ler
        esp, n, opac = max(1, h * rng.uniform(0.07, 0.11)), rng.randint(10, 16), 0.95
        cor = np.array(rng.choice(((28, 42, 140), (28, 42, 140), (22, 22, 30), (60, 30, 110))), np.float32)
    elif tinta == "marcador":  # passadas lado a lado até cobrir a altura toda; o marcador é opaco sobre o toner
        esp, n, opac = h * rng.uniform(0.55, 0.8), rng.randint(3, 4), 1.0
        cor = np.array(rng.choice(((18, 18, 22), (18, 18, 22), (30, 50, 150))), np.float32)
    else:  # pincel do app: cor chapada, traço redondo, em faixas
        esp, n, opac = h * rng.uniform(0.3, 0.45), rng.randint(4, 5), 1.0
        cor = np.array(rng.choice(((0, 0, 0), (230, 30, 30), (20, 90, 240), (0, 0, 0))), np.float32)
    for i in range(n):
        pontos = []
        xa, xb = (x0 - 0.2 * h - ox, x1 + 0.2 * h - ox) if i % 2 == 0 else (x1 + 0.2 * h - ox, x0 - 0.2 * h - ox)
        k = rng.randint(4, 9) if tinta != "caneta" else rng.randint(8, 14)
        for j in range(k):
            t = j / (k - 1)
            px = xa + (xb - xa) * t + rng.uniform(-0.08, 0.08) * h
            if tinta == "caneta":  # ziguezague pela altura toda, cada passada numa faixa diferente
                base = (i % 4) / 4
                py = (y0 - oy) + h * (0.05 + 0.9 * (base + 0.25 * (j % 2)) % 1.0 if i % 2 else 0.05 + 0.9 * ((j % 2) * 0.5 + base * 0.5)) + rng.uniform(-0.1, 0.1) * h
            elif tinta == "marcador":
                py = (y0 - oy) + h * (0.15 + 0.7 * i / max(1, n - 1)) + rng.uniform(-0.08, 0.08) * h
            else:
                py = (y0 - oy) + h * (0.15 + 0.7 * i / max(1, n - 1)) + rng.uniform(-0.12, 0.12) * h
            pontos.append([int(round(px * S)), int(round(py * S))])
        cv2.polylines(tela_s, [np.array(pontos, np.int32)], False, 255, max(1, int(round(esp * S))), cv2.LINE_AA)
    alfa = cv2.resize(tela_s, (W_, H_), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0 * opac
    if tinta == "caneta":  # pressão varia: falhas finas na tinta
        rng_np = np.random.default_rng(c.semente)
        alfa *= np.clip(0.7 + 0.3 * cv2.GaussianBlur(rng_np.uniform(0, 1, (H_, W_)).astype(np.float32), (0, 0), 0.8), 0, 1)
    if not (alfa > 0.05).any():
        raise ErroOperacao("o rabisco não cobriu o campo")
    tela.tinta(tela.caixa, alfa, np.broadcast_to(cor, tela.px.shape).astype(np.float32))
    nome = {"caneta": "caneta esferográfica", "marcador": "marcador grosso", "pincel_app": "pincel de rabisco do app"}[tinta]
    return tela.resultado(c.meta("", [_alt(0, c.texto, "", "ocultacao")],
                                 f"Campo '{c.texto}' rabiscado com {nome} até ficar ilegível."))


def _t_campo_borrar(c: _C) -> Resultado:
    modo = c.opcao("modo")
    x0, y0, x1, y1 = c.caixa
    h = y1 - y0
    rng = c.rng
    m = int(math.ceil(0.25 * h)) + 1
    tela = _Tela(c, (x0 - m, y0 - m, x1 + m, y1 + m))
    H_, W_ = tela.pegada.shape
    if modo == "mosaico":
        bloco = max(3, int(round(h * rng.uniform(0.3, 0.5))))
        peq = cv2.resize(tela.px, (max(1, W_ // bloco), max(1, H_ // bloco)), interpolation=cv2.INTER_AREA)
        novo = cv2.resize(peq, (W_, H_), interpolation=cv2.INTER_NEAREST)
        tela.px = novo.astype(np.float32)
        tela.pegada[:] = True
        desc = f"pixelizado em blocos de {bloco} px"
    else:
        sigma = h * rng.uniform(0.35, 0.6)
        borrado = cv2.GaussianBlur(tela.px, (0, 0), sigma)
        ys, xs = np.mgrid[0:H_, 0:W_].astype(np.float32)
        d = _sdf_ret(xs, ys, W_ / 2, H_ / 2, W_ - 2, H_ - 2, 0.3 * h, 0.0)
        alfa = np.clip(0.5 - d / 2.0, 0, 1)
        tela.px = tela.px * (1 - alfa[..., None]) + borrado * alfa[..., None]
        tela.pegada |= alfa > 0.02
        desc = f"desfocado (σ={sigma:.1f} px)"
    return tela.resultado(c.meta("", [_alt(0, c.texto, "", "ocultacao")],
                                 f"Campo '{c.texto}' {desc} com a ferramenta de borrar do app."))


# ============================================================================ retoque sem mudar o texto
_RETOQUES = ("desfocar", "reamostrar", "tremido", "nitidez", "jpeg", "tamanho")


def _retocar(rec: np.ndarray, op: str, h: float, rng, limite: float = 0.05) -> tuple[np.ndarray, str, float]:
    """Um retoque de textura no recorte (float32), com força proporcional à altura do texto ``h``
    (texto pequeno com retoque forte vira atalho fácil, ver OSTF). Devolve (recorte, descrição,
    escala usada). ``limite``: maior mudança de tamanho que cabe na folga da área de trabalho."""
    k = min(1.5, max(0.6, h / 30))
    if op == "desfocar":
        s = rng.uniform(0.4, 1.0) * k
        return cv2.GaussianBlur(rec, (0, 0), s), f"desfoque σ={s:.2f}", 0.0
    if op == "reamostrar":
        f = rng.uniform(0.5, 0.8)
        H, W = rec.shape[:2]
        peq = cv2.resize(rec, (max(2, round(W * f)), max(2, round(H * f))), interpolation=cv2.INTER_AREA)
        return cv2.resize(peq, (W, H), interpolation=rng.choice((cv2.INTER_LINEAR, cv2.INTER_CUBIC))), \
            f"reduzido a {f:.2f} e ampliado", 0.0
    if op == "tremido":
        n = rng.choice((3, 3, 5))
        ang = rng.uniform(0, 180)
        ker = np.zeros((n, n), np.float32)
        ker[n // 2, :] = 1
        ker = cv2.warpAffine(ker, cv2.getRotationMatrix2D(((n - 1) / 2, (n - 1) / 2), ang, 1.0), (n, n))
        return cv2.filter2D(rec, -1, ker / ker.sum()), f"tremido {n}px a {ang:.0f}°", 0.0
    if op == "nitidez":
        a = rng.uniform(0.4, 1.2)
        return rec + a * (rec - cv2.GaussianBlur(rec, (0, 0), 1.0)), f"aguçado ({a:.2f})", 0.0
    if op == "jpeg":
        q = rng.randint(40, 85)
        buf = io.BytesIO()
        Image.fromarray(np.clip(rec + 0.5, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=q)
        return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), np.float32), f"JPEG {q}", 0.0
    e = rng.choice((-1, 1)) * rng.uniform(min(0.01, limite), min(0.05, limite))  # tamanho, dentro da folga
    H, W = rec.shape[:2]
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, 1 + e)
    return cv2.warpAffine(rec, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE), \
        f"tamanho {100 * e:+.1f}%", e


def _t_trecho_jitter(c: _C) -> Resultado:
    """Texture Jitter (OSTF): o mesmo texto, reprocessado só num trecho e colado com borda suave."""
    op = c.opcao("operacao")
    x0, y0, x1, y1 = c.caixa
    h = y1 - y0
    # trecho: palavras inteiras (vãos de papel maiores que ~1/3 da altura separam palavras)
    tinta = mascara_tinta(c.img[y0:y1, x0:x1]).any(0)
    cols = np.flatnonzero(tinta)
    if cols.size < 2:
        raise ErroOperacao("campo sem tinta para retocar")
    quebras = [i for i in range(1, cols.size) if cols[i] - cols[i - 1] > max(3, h / 3)]
    pedacos = np.split(cols, quebras)
    i = c.rng.randrange(len(pedacos))
    j = min(len(pedacos), i + c.rng.choice((1, 1, 2, 3)))
    tx0, tx1 = int(pedacos[i][0]) + x0, int(pedacos[j - 1][-1]) + 1 + x0
    m = max(2, int(round(0.25 * h)))
    tela = _Tela(c, (tx0 - m, y0 - m, tx1 + m, y1 + m))
    X0, Y0, X1, Y1 = tela.caixa
    pena = min(3.0, max(1.0, h / 15))  # borda suave de 1 a 3 px, pela altura da letra
    b = int(math.ceil(pena))
    # mudar o tamanho só o que cabe na folga (a borda suave não pode pegar a ponta da letra)
    folga = max(0, min(tx0 - X0, X1 - tx1) - b - 1)
    limite = 2 * folga / max(1, tx1 - tx0)
    ops = c.rng.sample(_RETOQUES, 2) if op == "combinado" else [op]
    if limite < 0.005:
        ops = [o if o != "tamanho" else c.rng.choice(_RETOQUES[:-1]) for o in ops]
    rec, feitos, escala = tela.px.copy(), [], 0.0
    for o in ops:
        rec, d, e = _retocar(rec, o, h, c.rng, limite)
        feitos.append(d)
        escala = escala or e
    alfa = np.zeros(rec.shape[:2], np.float32)
    alfa[b:-b or None, b:-b or None] = 1
    alfa = cv2.GaussianBlur(alfa, (0, 0), pena / 2)
    novo = tela.px * (1 - alfa[..., None]) + rec * alfa[..., None]
    mudou = np.abs(novo - tela.px).max(2) > 0.5
    if mudou.sum() < 0.02 * mudou.size:
        raise ErroOperacao("o retoque quase não mudou o trecho")
    tela.px = novo
    tela.pegada |= mudou
    trecho = c.texto if len(pedacos) == 1 else f"parte de '{c.texto}'"
    return tela.resultado(c.meta(
        c.texto, [_alt(0, c.texto, c.texto, "retoque")],
        f"Trecho {trecho!r} reprocessado sem mudar o texto ({', '.join(feitos)}) e colado de volta com borda "
        f"suave de {pena:.1f}px (Texture Jitter).", retoques=feitos, escala_pct=round(100 * escala, 2),
        largura_trecho=int(tx1 - tx0), folga_px=int(folga)))


# ============================================================================ traços (PIX)
def _melhor_desvio(A: np.ndarray, B: np.ndarray, r: int) -> int:
    """Deslocamento horizontal de B (em px) que mais sobrepõe A."""
    melhor, nota = 0, -1
    for dx in range(-r, r + 1):
        s = int((A & np.roll(B, dx, axis=1)).sum())
        if s > nota:
            melhor, nota = dx, s
    return melhor


def _janela(ab, caixa_b, janela):
    """Alfa desenhado em ``caixa_b`` recortado na ``janela`` (bool > 0,5)."""
    x0, y0, x1, y1 = janela
    B = np.zeros((y1 - y0, x1 - x0), bool)
    bx0, by0, bx1, by1 = caixa_b
    ix0, iy0, ix1, iy1 = max(bx0, x0), max(by0, y0), min(bx1, x1), min(by1, y1)
    if ix1 > ix0 and iy1 > iy0:
        B[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = ab[iy0 - by0:iy1 - by0, ix0 - bx0:ix1 - bx0] > 0.5
    return B


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = _dilatar(a, 1), _dilatar(b, 1)  # 1 px de folga para o desalinhamento
    return float((a & b).sum() / max(1, (a | b).sum()))


def _lido_como(m: np.ndarray, modelos: dict, r: int) -> list[tuple[float, str]]:
    """Leitura por gabarito: semelhança de ``m`` com cada dígito desenhado na fonte (melhor encaixe)."""
    out = []
    for d, T in modelos.items():
        dx = _melhor_desvio(m, T, r)
        out.append((_iou(m, np.roll(T, dx, axis=1)), d))
    return sorted(out, reverse=True)


def _pares_traco(c: _C, acrescentar: bool):
    """(g, novo, caixa, alfa_novo, nota) dos dígitos que viram outro só acrescentando/raspando tinta.

    Dois filtros: (1) quase nada da letra velha pode sobrar fora da nova (ou faltar da nova dentro
    da velha), medido por distância; (2) o RESULTADO precisa ser lido como o dígito novo, com folga
    sobre o antigo, comparando com os 10 dígitos desenhados na fonte do campo. Sem o (2), um 0 com
    a cintura do 8 passava (vira um θ, não um 8)."""
    for g in c.alvos("digito"):  # trabalha numa janela pequena em volta do glifo
        x0, y0, x1, y1 = _caixa_glifo(g, max(4, g.altura // 3))
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(c.W, x1), min(c.H, y1)
        A = np.zeros((y1 - y0, x1 - x0), bool)
        A[g.y0 - y0:g.y1 - y0, g.x0 - x0:g.x1 - x0] = g.tinta
        r = max(1, g.largura // 3)
        modelos = {}
        for d in "0123456789":
            caixa_d, a_d = c.desenhar(d, centro=g.cx)
            modelos[d] = _janela(a_d, caixa_d, (x0, y0, x1, y1))
        bons = []
        for ch in c.substitutos(g.ch):
            if not plausivel(c.categoria, c.texto, c.texto[:g.i] + ch + c.texto[g.i + 1:]):
                continue
            caixa_b, ab = c.desenhar(ch, centro=g.cx)
            B = _janela(ab, caixa_b, (x0, y0, x1, y1))
            if not B.any():
                continue
            dx = _melhor_desvio(A, B, max(1, g.largura // 3))
            if dx:  # redesenha no encaixe de maior sobreposição com a letra antiga
                caixa_b, ab = c.desenhar(ch, centro=g.cx + dx)
                B = _janela(ab, caixa_b, (x0, y0, x1, y1))
            # o que SOBRA visível: tinta a mais de 1,5 px de qualquer traço da outra letra. A tolerância
            # cobre o desalinhamento (o desenho na fonte nunca cai exatamente sobre a tinta do scan);
            # um traço fora do lugar fica longe e conta inteiro
            if acrescentar:  # a letra velha precisa sumir dentro da nova
                sobra = _longe(A, B, 1.5).sum() / max(1, A.sum())
                ganho = (B & ~_dilatar(A, 1)).sum() / max(1, B.sum())
                if sobra > 0.03 or ganho < 0.06:
                    continue
                resultado = A | B
            else:  # a letra nova precisa estar inteira dentro da velha
                falta = _longe(B, A, 1.5).sum() / max(1, B.sum())
                perda = (A & ~_dilatar(B, 2)).sum() / max(1, A.sum())
                if falta > 0.03 or perda < 0.12:
                    continue
                resultado = A & _dilatar(B, 1)
            leitura = _lido_como(resultado, modelos, r)
            nota = dict((d, v) for v, d in leitura)
            if leitura[0][1] != ch or nota[ch] - nota[g.ch] < 0.08:
                continue  # o resultado não "lê" como o dígito novo
            bons.append((g, ch, caixa_b, ab, nota[ch], (x0, y0, x1, y1), A, B))
        if bons:
            return bons
    return []


def _t_acrescentar_tracos(c: _C) -> Resultado:
    corop = c.opcao("cor")
    tinta = c.opcao("tinta")
    bons = _pares_traco(c, acrescentar=True)
    if not bons:
        raise ErroOperacao("nenhum dígito vira outro só acrescentando traços (ex.: 3→8, 1→4)")
    g, ch, caixa_b, ab, leitura, janela, A, B = c.rng.choices(bons, weights=[b[4] for b in bons])[0]
    cor = _cor_nova(c, g)
    if tinta == "caneta":  # esferográfica no papel: tinta azul ou preta, nunca igual à impressão; traço com falhas
        cor = np.array(c.rng.choice(((28, 42, 140), (28, 42, 140), (24, 24, 32))), np.float32) * c.rng.uniform(0.9, 1.1)
        corop = "caneta"
        rng_np = np.random.default_rng(c.semente)
        ab = np.clip(ab * np.clip(0.65 + 0.35 * cv2.GaussianBlur(rng_np.uniform(0, 1, ab.shape).astype(np.float32), (0, 0), 0.7), 0, 1)
                     + 0.15 * (cv2.GaussianBlur(ab, (0, 0), 0.8) > 0.2), 0, 1)  # um pouco mais grossa e irregular
    elif corop == "diferente":  # digital, mas um pouco mais azulada ou mais clara/escura
        cor = np.clip(cor + np.array([c.rng.uniform(-25, 10), c.rng.uniform(-25, 10), c.rng.uniform(5, 45)]), 0, 255)
    # não repinta a tinta antiga: só onde ela não está (alfa suave da letra original)
    rec, a_orig, caixa_o = glifos.alfa_glifo(c.img, g, margem=3)
    tela = _Tela(c, _uniao(caixa_b, caixa_o))
    ja = np.zeros(tela.pegada.shape, np.float32)
    r = tela._local(caixa_o, a_orig.shape)
    if r:
        ja[r[0]] = a_orig[r[1]]
    novo_alfa = np.zeros(tela.pegada.shape, np.float32)
    r = tela._local(caixa_b, ab.shape)
    if r:
        novo_alfa[r[0]] = ab[r[1]]
    tela.tinta(tela.caixa, novo_alfa * (1 - ja), cor)
    novo = c.texto[:g.i] + ch + c.texto[g.i + 1:]
    return tela.resultado(c.meta(novo, [_alt(g.i, g.ch, ch, "traco")],
                                 f"'{g.ch}' virou '{ch}' só com traços acrescentados "
                                 f"({'mesma cor' if corop == 'mesma' else 'caneta esferográfica no papel' if corop == 'caneta' else 'cor de caneta um pouco diferente'}; "
                                 f"o resultado lê como '{ch}' com {leitura:.0%} de semelhança): '{c.texto}' → '{novo}'."))


def _t_raspar_tracos(c: _C) -> Resultado:
    bons = _pares_traco(c, acrescentar=False)
    if not bons:
        raise ErroOperacao("nenhum dígito vira outro só apagando traços (ex.: 4→1)")
    g, ch, caixa_b, ab, leitura, (x0, y0, x1, y1), A, B = c.rng.choices(bons, weights=[b[4] for b in bons])[0]
    furo = _dilatar(A, 1) & ~_dilatar(B, 1)
    tela = _Tela(c, (x0, y0, x1, y1))
    tela.apagar(furo)
    novo = c.texto[:g.i] + ch + c.texto[g.i + 1:]
    return tela.resultado(c.meta(novo, [_alt(g.i, g.ch, ch, "traco")],
                                 f"'{g.ch}' virou '{ch}' raspando os traços que sobravam e reconstruindo o papel: "
                                 f"'{c.texto}' → '{novo}'."))


# ============================================================================ códigos
def _total_documento(campos: list[dict]) -> int | None:
    """O total impresso, em centavos: o valor com rótulo TOTAL; senão o maior valor do documento."""
    valores = []
    for f in campos:
        if f.get("categoria") != "valor":
            continue
        cent = geradores.valor_em_centavos(str(f.get("texto", "")))
        if cent:
            valores.append(("TOTAL" in str(f.get("rotulo", "")).upper(), cent))
    if not valores:
        return None
    return max(valores)[1]


MODULO_MINIMO_PX = 1.4  # abaixo disto o ITF regenerado não é lido (medido de 1,2 a 2,0 px; boleto a 150 dpi tem ~1,5)


def _t_codigo_regenerar(c: _C) -> Resultado:
    """Código novo AMARRADO ao documento: o valor é o total impresso (ou o total novo da versão, quando o
    falsificador refaz o código para combinar) e a empresa/segmento (ou o banco) são os da linha original.
    Um código com valor e empresa aleatórios se contradiz com o resto da conta e vira pista para quem lê o texto."""
    partes, total = None, None
    if c.documento is not None:
        linha = next((f["texto"] for f in c.documento.campos if f.get("categoria") == "linha digitável"), "")
        partes = geradores.partes_da_linha(linha) if linha else None
        total = _total_documento(c.documento.campos)
    pedido = str(c.p.get("valor") or "").strip()
    centavos = geradores.valor_em_centavos(pedido) if pedido else total
    kw = {"centavos": centavos}
    if partes:
        kw["arrecadacao"] = partes.get("arrecadacao")
        for k in ("empresa", "segmento", "banco", "ref"):
            if partes.get(k):
                kw[k] = partes[k]
    cod, linha, cent = geradores.par_codigo(**kw)
    # o ITF de 44 dígitos precisa de 1,4 px por módulo para ser lido; numa caixa estreita (o código do topo da conta
    # NIO, de outro tipo, com 494 px) ele saía com módulos de 1,2 px — ilegível e de outra simbologia
    px_modulo = (c.caixa[2] - c.caixa[0]) / sum(w for _, w in codigos.modulos_itf(cod, 3.0))
    if px_modulo < MODULO_MINIMO_PX:
        raise ErroOperacao(f"o código de barras do boleto ({len(cod)} dígitos) não cabe legível nesta caixa: "
                           f"{px_modulo:.1f} px por módulo (mínimo {MODULO_MINIMO_PX})")
    res = auto.trocar_campo(c.img, {"caixa": list(c.caixa), "texto": cod, "tipo": "barras", "semente": c.semente,
                                    "igualar_ruido": False}, selecao.vazia(c.H, c.W), base=c.base)
    origem_valor = "o total novo da versão" if pedido and centavos else "o total impresso no documento" if centavos else "um valor sorteado"
    res.meta = {**res.meta, **c.meta(cod, [_alt(0, c.texto, cod, "campo")],
                                     f"Código de barras ITF-25 novo e válido ({cod[:6]}…) com {origem_valor} "
                                     f"(R$ {cent / 100:,.2f}) e {'a mesma empresa/segmento da linha original' if partes else 'empresa sorteada'}; "
                                     f"a linha digitável correspondente é {linha}.", linha_digitavel=linha, digitos=cod,
                                     valor_centavos=int(cent))}
    return res


def _t_codigo_qr(c: _C) -> Resultado:
    payload = c.dado or geradores.gerar("qr code/pix")["texto"]
    res = auto.trocar_campo(c.img, {"caixa": list(c.caixa), "texto": payload, "tipo": "qr", "semente": c.semente,
                                    "igualar_ruido": False}, selecao.vazia(c.H, c.W), base=c.base)
    res.meta = {**res.meta, **c.meta(payload, [_alt(0, c.texto, payload, "campo")],
                                     "QR code PIX novo (payload com CRC válido) no lugar do original.")}
    return res


# ============================================================================ IA
PROMPT_REMOVER = ('Remove the text "{antigo}" completely, leaving only the blank background with exactly the same '
                  'color, texture and lines. Do not change anything else.')


def _ia_regiao(c: _C, caixa, prompt: str):
    x0, y0, x1, y1 = (int(round(v)) for v in caixa)
    regiao = auto._sel_ret(c.img, x0, y0, x1, y1)
    pedido = {"prompt": prompt, "semente": c.semente, "passos": c.p.get("passos"), "modelo": c.p.get("modelo"),
              "contexto": 0.6, "megapixels": 1.0, "colagem": c.opcao("colagem_ia") or "selecao",
              "pena": c.rng.randint(1, 4)}
    return ia.editar(c.img, pedido, regiao, editor=c.editor)


def _t_ia_reescrever(c: _C) -> Resultado:
    novo = _novo_valor(c, maximo=1.15)
    res = auto.trocar_campo_ia(c.img, {"caixa": list(c.caixa), "texto": novo, "texto_antigo": c.texto,
                                       "semente": c.semente, "modelo": c.p.get("modelo"), "passos": c.p.get("passos"),
                                       "igualar_ruido": False, "colagem": c.opcao("colagem_ia"),
                                       "pena": c.rng.randint(1, 4)}, selecao.vazia(c.H, c.W), base=c.base,
                               editor=c.editor)
    res.meta = {**res.meta, **c.meta(novo, [_alt(0, c.texto, novo, "campo")],
                                     f"A IA ({res.meta.get('modelo') or res.meta.get('modelo_id') or 'Qwen'}) reescreveu "
                                     f"'{c.texto}' como '{novo}' na região do campo.")}
    return res


def _letra_da_ia(c: _C, g: glifos.Glifo, ch: str, ia_rec: np.ndarray, origem):
    """Da saída da IA para o campo inteiro, aproveita SÓ a letra trocada.

    O modelo redesenha o campo todo levemente deslocado: recortar a região da letra direto pega
    pedaços das vizinhas. Então: (1) alinha a saída ao documento pela tinta das letras que NÃO
    mudaram; (2) recorta a letra até o meio do vão para as vizinhas; (3) confere, comparando com os
    caracteres desenhados na fonte do campo, que o recorte lê como ``ch``. Se não lê, descarta.
    Devolve (caixa, recorte, alfa com borda suave, desvio (dx, dy), semelhança)."""
    rx0, ry0 = origem
    h, w = ia_rec.shape[:2]
    outras = np.zeros((h, w), bool)
    for o in c.glifos:
        if o.i == g.i:
            continue
        ox0, oy0 = o.x0 - rx0, o.y0 - ry0
        if ox0 < 0 or oy0 < 0 or ox0 + o.largura > w or oy0 + o.altura > h:
            continue
        outras[oy0:oy0 + o.altura, ox0:ox0 + o.largura] |= o.tinta
    tinta_ia = mascara_tinta(ia_rec).astype(bool)
    r = max(2, int(0.35 * g.altura))
    melhor, nota_al = (0, 0), -1
    for dy in range(-(r // 2), r // 2 + 1):
        linha = np.roll(tinta_ia, dy, axis=0)
        for dx in range(-r, r + 1):
            n = int((np.roll(linha, dx, axis=1) & outras).sum())
            if n > nota_al:
                melhor, nota_al = (dx, dy), n
    if outras.any() and nota_al < 0.4 * outras.sum():
        raise ErroOperacao("a saída da IA não se alinha ao documento (resultado descartado)")
    dx, dy = melhor
    alinhado = cv2.warpAffine(ia_rec, np.float32([[1, 0, dx], [0, 1, dy]]), (w, h), borderMode=cv2.BORDER_REPLICATE)
    # recorte da letra: até o meio do vão para as vizinhas, na altura do campo
    ant = next((o for o in c.glifos if o.i == g.i - 1), None)
    prox = next((o for o in c.glifos if o.i == g.i + 1), None)
    folga = max(2, g.largura // 4)
    jx0 = (ant.x1 + g.x0) // 2 if ant else g.x0 - folga
    jx1 = (g.x1 + prox.x0 + 1) // 2 if prox else g.x1 + folga
    jy0, jy1 = max(ry0, g.y0 - folga), min(ry0 + h, g.y1 + folga)
    jx0, jx1 = max(rx0, jx0), min(rx0 + w, jx1)
    patch = alinhado[jy0 - ry0:jy1 - ry0, jx0 - rx0:jx1 - rx0]
    janela = (jx0, jy0, jx1, jy1)
    letras = "0123456789" if ch.isdigit() else ("ABCDEFGHIJKLMNOPQRSTUVWXYZ" if ch.isupper() else "abcdefghijklmnopqrstuvwxyz")
    modelos = {}
    for d in letras:
        caixa_d, a_d = c.desenhar(d, centro=g.cx)
        modelos[d] = _janela(a_d, caixa_d, janela)
    tinta_patch = mascara_tinta(patch).astype(bool)
    leitura = _lido_como(tinta_patch, modelos, max(1, g.largura // 3))
    nota = dict((d, v) for v, d in leitura)
    if leitura[0][1] != ch or nota[ch] - nota.get(g.ch, 0.0) < 0.08:
        raise ErroOperacao(f"a IA não desenhou '{ch}' no lugar de '{g.ch}' (leu-se '{leitura[0][1]}'; descartado)")
    ph, pw = patch.shape[:2]  # borda suave de 2 px para não marcar o retângulo
    yy = np.minimum(np.arange(ph), np.arange(ph)[::-1])[:, None]
    xx = np.minimum(np.arange(pw), np.arange(pw)[::-1])[None, :]
    alfa = np.clip(np.minimum(yy, xx) / 2.0, 0, 1).astype(np.float32)
    return janela, patch.astype(np.float32), alfa, (dx, dy), nota[ch]


def _t_ia_caractere(c: _C) -> Resultado:
    g, ch = _troca_plausivel(c, c.alvo())
    novo = c.texto[:g.i] + ch + c.texto[g.i + 1:]
    x0, y0, x1, y1 = c.caixa
    m = max(4, int(round(0.35 * (y1 - y0))))
    res_ia = _ia_regiao(c, (x0 - m, y0 - m, x1 + m, y1 + m), auto.PROMPT_TROCA_IA.format(antigo=c.texto, novo=novo))
    janela, patch, alfa, desvio, nota = _letra_da_ia(c, g, ch, res_ia.recorte, (res_ia.x0, res_ia.y0))
    tela = _Tela(c, janela)
    tela.tinta(janela, alfa, patch)
    res = tela.resultado(c.meta(novo, [_alt(g.i, g.ch, ch)],
                                f"A IA reescreveu o campo com '{g.ch}'→'{ch}' (posição {g.i + 1}); a saída foi alinhada ao "
                                f"documento (desvio {desvio[0]}, {desvio[1]} px) e só a letra trocada foi aproveitada, "
                                f"lida como '{ch}' ({nota:.0%} de semelhança): '{c.texto}' → '{novo}'.",
                                alinhamento=list(desvio)))
    res.meta = {**res_ia.meta, **res.meta}  # tempo, modelo e prompt da IA + o rótulo da técnica
    return res


def _t_ia_remover(c: _C) -> Resultado:
    x0, y0, x1, y1 = c.caixa
    m = max(4, int(round(0.3 * (y1 - y0))))
    res = _ia_regiao(c, (x0 - m, y0 - m, x1 + m, y1 + m), PROMPT_REMOVER.format(antigo=c.texto))
    res.meta = {**res.meta, **c.meta("", [_alt(0, c.texto, "", "remocao")],
                                     f"A IA apagou '{c.texto}' e reconstruiu o fundo na região do campo.")}
    return res


# ============================================================================ recriação (documento refeito)
# fontes de escritório de quem refaz a conta inteira no Word/Canva: uma só para a página toda
_ESCRITORIO = ("Arial", "Calibri", "Times New Roman", "Verdana")


def _cruza(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _como_campo(c: _C, linha: dict, apagar: str) -> _C:
    """O contexto da técnica com UMA linha no lugar do campo: ``aj``, ``desfoque`` e ``papel`` saem pelo mesmo
    caminho de qualquer técnica de campo (identificação em cache, apagador registrado em ``fornecedores``). O
    apagador já sorteado para a página entra como opção fixa, para cada linha não sortear o seu."""
    sub = copy.copy(c)
    sub.caixa = tuple(int(v) for v in linha["caixa"])
    sub.texto, sub.campo_id = str(linha["texto"]).strip(), None
    sub.p = {**c.p, "opcoes": {**(c.p.get("opcoes") or {}), "apagar": apagar}}
    sub._aj = sub._gs = sub._desf = sub._base = sub._tinta = None
    return sub


def _caixas_limpas(c: _C, linhas: list[dict]) -> list:
    """Base que é uma captura (foto/scan): a caixa de cada linha no documento LIMPO, onde a fonte é identificada
    (``_C._aj_derivado``). O derivado copia texto e confiança da linha original: é por eles que se pareia."""
    origem = getattr(c.documento, "origem", None)
    if origem is None:
        return [None] * len(linhas)
    sobras = list(origem.linhas)
    out = []
    for l in linhas:
        j = next((k for k, o in enumerate(sobras) if o["texto"] == l["texto"] and o.get("conf") == l.get("conf")), None)
        out.append(sobras.pop(j)["caixa"] if j is not None else None)
    return out


def _aj_linha(c: _C, sub: _C, limpa) -> dict:
    """Fonte, tamanho, cor e âncora da linha, como ``_C.aj`` (no documento limpo quando a base é uma captura)."""
    if limpa is not None and sub._aj is None:
        sub._aj = sub._aj_derivado(c.documento.origem, limpa)
    return sub.aj


def _alinhada_a_direita(linhas: list[dict], l: dict) -> bool:
    """Heurística simples: a linha está numa coluna alinhada pela direita quando mais linhas da página terminam
    no mesmo x do que começam no mesmo x (totais, valores de tabela); senão, alinha pela esquerda."""
    x0, y0, x1, y1 = l["caixa"]
    tol = max(3.0, 0.5 * (y1 - y0))
    esq = sum(abs(o["caixa"][0] - x0) <= tol for o in linhas if o is not l)
    dir_ = sum(abs(o["caixa"][2] - x1) <= tol for o in linhas if o is not l)
    return dir_ > esq


def _rgb(hexa: str) -> np.ndarray:
    return np.array([int(hexa[i:i + 2], 16) for i in (1, 3, 5)], np.float32)


def _furo_linha(sub: _C, caixa, desf: float) -> np.ndarray:
    """Toda a tinta da linha, com o halo fraco do antialias/desfoque, dilatada pela altura da letra (como em
    ``_t_campo_remover``): nada do texto antigo pode sobrar como fantasma. Um traço fino que atravessa a caixa
    de lado a lado é linha de tabela: fica."""
    x0, y0, x1, y1 = caixa
    rec = sub.img[y0:y1, x0:x1]
    tinta = mascara_tinta(rec)
    h, w = tinta.shape
    n, rot, est, _ = cv2.connectedComponentsWithStats(tinta)
    regua = np.isin(rot, [k for k in range(1, n) if est[k, cv2.CC_STAT_WIDTH] >= 0.9 * w
                          and est[k, cv2.CC_STAT_HEIGHT] <= max(3, 0.12 * h)])
    tinta = tinta.astype(bool) & ~regua
    if not tinta.any():
        raise ErroOperacao("linha sem tinta")
    papel = np.asarray(glifos.cor_papel(sub.img, caixa), np.float32)
    fraca = (np.abs(rec.astype(np.float32) - papel).max(2) > 12) & _dilatar(tinta, 3)
    r = max(2, min(4, int(round(0.1 * h)) + int(round(desf))))
    return _dilatar(tinta | fraca, r) & ~regua


def _render(p: dict, raster: str, H: int, W: int):
    """(caixa, alfa) do texto no rasterizador pedido (o ClearType devolve um alfa por canal)."""
    if raster == "cleartype":
        ty0, ty1, tx0, tx1, a = texto.renderizar_cleartype(p, H, W)
    else:
        ty0, ty1, tx0, tx1, a = texto.renderizar(p, H, W)
    return (tx0, ty0, tx1, ty1), a


def _params_substituta(sub: _C, fonte: str, direita: bool) -> dict:
    """A linha na fonte de escritório: tamanho pela altura das maiúsculas e cor da tinta medidos na linha
    original; o que não cabe na caixa é encolhido (quem refaz ajusta o tamanho até caber na coluna); alinhada
    pela esquerda ou, numa coluna de valores, pela direita."""
    x0, y0, x1, y1 = sub.caixa
    est = texto.estimar(sub.img, auto._sel_ret(sub.img, x0, y0, x1, y1), fonte)
    if not est.get("achou_texto"):
        raise ErroOperacao("linha sem tinta para medir")
    tx0, _, tx1, _ = est["caixa_tinta"]
    p = {"texto": sub.texto, "fonte": fonte, "tamanho": float(est["tamanho"]), "x": float(est["x"]),
         "y": float(est["y"]), "cor": est["cor"], "desfoque": 0.0}
    larg = glifos.fonte_pil(fonte, p["tamanho"]).getlength(sub.texto)
    # da tinta original até o lado oposto da caixa, menos a folga da borda: o que passar disso é encolhido
    cabe = (tx1 - x0 if direita else x1 - tx0) - 4
    if larg > cabe:
        p["tamanho"] = math.floor(p["tamanho"] * cabe / larg * 4) / 4  # o rasterizador arredonda a 1/4 de ponto
        if p["tamanho"] < 6:
            raise ErroOperacao("a linha não cabe na caixa nessa fonte")
        larg = glifos.fonte_pil(fonte, p["tamanho"]).getlength(sub.texto)
    if direita:
        p["x"] += (tx1 - tx0) - larg  # termina onde a tinta original terminava
    return p


def _bate(lido: str, esperado: str) -> bool:
    """Comparação tolerante de uma linha inteira relida pelo OCR: as confusões de ``lido_igual`` ou, numa
    linha longa, até 20% das letras diferentes — mas os dígitos têm de ser os mesmos."""
    if lido_igual(lido, esperado):
        return True
    a, b = _normal(lido), _normal(esperado)
    if "".join(ch for ch in a if ch.isdigit()) != "".join(ch for ch in b if ch.isdigit()):
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _conferir_pagina(c: _C, out: np.ndarray, feitas: list[dict]):
    """Relê até 6 linhas refeitas com o reconhecedor do OCR, entre as que ele lê certo no original (controle),
    e exige que pelo menos metade continue legível com o mesmo texto. None = não deu para julgar."""
    if c.p.get("conferir_ocr") is False:
        return None
    amostra = feitas[::max(1, len(feitas) // 6)][:6]
    ok = julgadas = 0
    try:
        for f in amostra:
            x0, y0, x1, y1 = f["caixa"]
            m = max(3, int(0.2 * (y1 - y0)))
            jan = (slice(max(0, y0 - m), min(c.H, y1 + m)), slice(max(0, x0 - m), min(c.W, x1 + m)))
            if not _bate(ocr.reconhecer(c.img[jan]), f["texto"]):
                continue
            julgadas += 1
            ok += _bate(ocr.reconhecer(out[jan]), f["texto"])
    except ocr.OCRIndisponivel:
        return None
    if not julgadas:
        return None
    if 2 * ok < julgadas:
        raise ErroOperacao(f"recriação ilegível: só {ok} de {julgadas} linhas relidas batem com o texto original")
    return True


def _t_pagina_recriar(c: _C) -> Resultado:
    """Documento refeito do zero: cada linha de texto da página é apagada e digitada de novo com o MESMO texto,
    como quem recria a conta inteira no Word/Canva com o mesmo layout. Logotipo, códigos de barras/QR e linhas
    de tabela ficam; linhas sobre fundo colorido (faixa do cabeçalho) também — ali o texto é parte do gráfico.
    Os campos com valor trocado vêm depois, por outras técnicas, por cima desta. Chamada só com o pseudo-campo
    da página (tipo "pagina"): a caixa é a página inteira e a pegada, a união das caixas das linhas refeitas."""
    if c.documento is None:
        raise ErroOperacao("esta técnica precisa do contexto do documento (linhas do OCR)")
    fonte_editor, raster, apagar = c.opcao("fonte_editor"), c.opcao("rasterizador"), c.apagador()
    codigos = [f["caixa"] for f in c.documento.campos if f.get("tipo") in ("barras", "qr")]
    linhas = [l for l in c.documento.linhas if str(l.get("texto", "")).strip()
              and not any(_cruza(l["caixa"], k) for k in codigos)]
    if len(linhas) < 5:
        raise ErroOperacao("a página tem menos de 5 linhas de texto para refazer")
    fonte_sub = None
    if fonte_editor == "substituta":  # a fonte do editor de quem refaz (hábito do falsificador) ou uma de escritório
        instaladas = texto.listar_fontes()
        disponiveis = [f for f in _ESCRITORIO if f in instaladas]
        habito = c.habitos.get("fonte")
        fonte_sub = habito if habito in instaladas else c.rng.choice(disponiveis) if disponiveis else None
        if fonte_sub is None:
            fonte_editor = c.escolhas["fonte_editor"] = "imitada"
    # fundo colorido: a cor do papel da linha contra a da maioria das linhas
    papeis = np.array([glifos.cor_papel(c.img, l["caixa"]) for l in linhas], np.float32)
    papel = np.median(papeis, axis=0)
    itens = [(l, _como_campo(c, l, apagar), limpa) for l, limpa, pap in zip(linhas, _caixas_limpas(c, linhas), papeis)
             if np.abs(pap - papel).max() <= 40]
    # desfoque do scan: mediana das primeiras linhas (a página foi capturada uma vez só)
    desfs = []
    for _, sub, limpa in itens[:3]:
        try:
            _aj_linha(c, sub, limpa)
            desfs.append(sub.desfoque)
        except ErroOperacao:
            pass
    desf_doc = float(np.median(desfs)) if desfs else 0.0
    # substituta: nítido, como um PDF exportado do editor; imitada: com o desfoque do documento
    desf = 0.0 if fonte_sub or c.habitos.get("nitido") else desf_doc
    out, pegada = c.img.copy(), np.zeros((c.H, c.W), bool)
    feitas, fontes = [], Counter()
    for l, sub, limpa in itens:
        try:
            if fonte_sub:
                p = _params_substituta(sub, fonte_sub, _alinhada_a_direita(linhas, l))
            else:
                aj = _aj_linha(c, sub, limpa)
                p = {**sub.params_fonte(sub.texto, aj["x"], aj["y"], desf), "cor": aj["cor"]}
            p["rasterizador"] = raster
            caixa_r, a = _render(p, raster, c.H, c.W)
            tela = _Tela(sub, sub.caixa)
            tela.apagar(_furo_linha(sub, tela.caixa, desf_doc))
            tela.tinta(caixa_r, a, _rgb(p["cor"]))
        except ErroOperacao:
            continue  # a linha fica como estava (sem tinta, fonte não identificada, sem papel em volta)
        x0, y0, x1, y1 = tela.caixa
        out[y0:y1, x0:x1] = np.clip(tela.px + 0.5, 0, 255).astype(np.uint8)
        pegada[y0:y1, x0:x1] = True
        feitas.append({"caixa": [x0, y0, x1, y1], "texto": sub.texto})
        fontes[p["fonte"]] += 1
    if len(feitas) < 5:
        raise ErroOperacao(f"só {len(feitas)} linhas puderam ser refeitas (precisa de 5)")
    conferido = _conferir_pagina(c, out, feitas)
    c.fornecedores.update(texto=raster, fonte=fonte_sub or fontes.most_common(1)[0][0])
    puladas = len(linhas) - len(feitas)
    como = (f"fonte substituta {fonte_sub}, nítido" if fonte_sub else
            f"fonte imitada linha a linha ({', '.join(f'{n} {f}' for f, n in fontes.most_common(3))}), desfoque {desf:.2f}")
    return Resultado(0, c.H, 0, c.W, out, pegada, meta=c.meta(
        None, [_alt(0, "", "", "recriacao")],
        f"Documento refeito: {len(feitas)} linhas re-tipografadas ({como}; rasterizador {raster}; logotipo e códigos "
        f"mantidos{f'; {puladas} linhas deixadas como estavam' if puladas else ''}); apagado com {apagar}.",
        texto_antigo=None, linhas=len(feitas), puladas=puladas, caixas=[f["caixa"] for f in feitas],
        fontes=dict(fontes), desfoque=round(desf, 3), conferido_ocr=conferido))


_IMPL = {
    "campo.reescrever": _t_campo_reescrever,
    "campo.mesma_fonte": _t_campo_mesma_fonte,
    "campo.letras_do_documento": _t_campo_letras_do_documento,
    "campo.amador": _t_campo_amador,
    "campo.acrescentar": _t_campo_acrescentar,
    "trecho.trocar": _t_trecho_trocar,
    "caractere.redesenhar": _t_caractere_redesenhar,
    "caractere.inserir": _t_caractere_inserir,
    "caractere.copiar_glifo": lambda c: _t_copiar_glifo(c, externo=False),
    "caractere.transpor": _t_caractere_transpor,
    "campo.copiar_interno": lambda c: _t_copiar_campo(c, externo=False),
    "caractere.glifo_externo": lambda c: _t_copiar_glifo(c, externo=True),
    "campo.colagem_externa": lambda c: _t_copiar_campo(c, externo=True),
    "caractere.apagar": _t_caractere_apagar,
    "campo.remover": _t_campo_remover,
    "campo.corretivo": _t_campo_corretivo,
    "caractere.acrescentar_tracos": _t_acrescentar_tracos,
    "caractere.raspar_tracos": _t_raspar_tracos,
    "trecho.jitter": _t_trecho_jitter,
    "codigo.regenerar": _t_codigo_regenerar,
    "codigo.qr": _t_codigo_qr,
    "ia.reescrever": _t_ia_reescrever,
    "ia.caractere": _t_ia_caractere,
    "ia.remover": _t_ia_remover,
    "campo.ocultar": _t_campo_ocultar, "campo.rabiscar": _t_campo_rabiscar, "campo.borrar": _t_campo_borrar,
    "bloco.colagem_externa": _t_bloco_colagem_externa,
    "campo.reescrever_pdf": lambda c: vetorial.reescrever(c, _novo_valor(c)),
    "pagina.recriar": _t_pagina_recriar,
}
assert set(_IMPL) == {t.id for t in catalogo.TECNICAS.values() if t.automatica}, "técnica sem implementação"
# o texto novo do rótulo não é o que o OCR lê na caixa do campo (bloco de várias linhas; a página inteira, que
# confere as próprias linhas em _conferir_pagina): sem a conferência genérica
_SEM_CONFERENCIA = {"bloco.colagem_externa", "pagina.recriar"}


# ============================================================================ acabamento
def _jpeg_local(c: _C, res: Resultado, q: int) -> Resultado:
    tmp = c.img.copy()
    caixa = tmp[res.y0:res.y1, res.x0:res.x1]
    caixa[:] = np.where(res.pegada[..., None], res.recorte, caixa)
    m = np.zeros((c.H, c.W), bool)
    m[res.y0:res.y1, res.x0:res.x1] = res.pegada
    r2 = ajustes.ajuste(tmp, {"ajuste": "jpeg", "qualidade": q}, selecao.de_mascara(m))
    novo = res.recorte.copy()
    sub = novo[r2.y0 - res.y0:r2.y1 - res.y0, r2.x0 - res.x0:r2.x1 - res.x0]
    sub[r2.pegada] = r2.recorte[r2.pegada]
    res.recorte = novo
    return res


# orientação EXIF: a imagem exibida é o arquivo armazenado transformado (documentos.imagem_para_rgb aplica
# exif_transpose); a grade 8x8/MCU do JPEG começa em (0, 0) no ARMAZENADO
_EXIBIR = {1: lambda s: s, 2: lambda s: s[:, ::-1], 3: lambda s: s[::-1, ::-1], 4: lambda s: s[::-1],
           5: lambda s: s.swapaxes(0, 1), 6: lambda s: np.rot90(s, -1), 7: lambda s: np.rot90(s, 2).swapaxes(0, 1),
           8: lambda s: np.rot90(s, 1)}
_ARMAZENAR = {**_EXIBIR, 6: lambda d: np.rot90(d, 1), 8: lambda d: np.rot90(d, -1)}


def _no_armazenado(ori: int, Hs: int, Ws: int, i: int, j: int) -> tuple[int, int]:
    """(linha, coluna) no arquivo armazenado do pixel (i, j) da imagem exibida."""
    return {1: (i, j), 2: (i, Ws - 1 - j), 3: (Hs - 1 - i, Ws - 1 - j), 4: (Hs - 1 - i, j), 5: (j, i),
            6: (Hs - 1 - j, i), 7: (Hs - 1 - j, Ws - 1 - i), 8: (j, Ws - 1 - i)}[ori]


def _na_exibida(ori: int, Hs: int, Ws: int, r: int, q: int) -> tuple[int, int]:
    """(linha, coluna) na imagem exibida do pixel (r, q) do arquivo armazenado."""
    return {1: (r, q), 2: (r, Ws - 1 - q), 3: (Hs - 1 - r, Ws - 1 - q), 4: (Hs - 1 - r, q), 5: (q, r),
            6: (q, Hs - 1 - r), 7: (Ws - 1 - q, Hs - 1 - r), 8: (Ws - 1 - q, r)}[ori]


def _caixa_mapeada(mapa, y0: int, x0: int, y1: int, x1: int) -> tuple[int, int, int, int]:
    (a, b), (c2, d) = mapa(y0, x0), mapa(y1 - 1, x1 - 1)
    return min(a, c2), min(b, d), max(a, c2) + 1, max(b, d) + 1


def _requantizar(c: _C, res: Resultado) -> str | None:
    """A edição volta para a compressão do arquivo de origem: o ÚLTIMO passo do acabamento.

    Fora da edição, a página salva é o JPEG decodificado: cada bloco 8x8 cai na grade da tabela de quantização
    da fonte e o croma é subamostrado (4:2:0: um Cb/Cr por 2x2 px). A edição saía lisa disso — a única área da
    página fora da grade (distância dos coeficientes DCT aos múltiplos da tabela 0,12-0,24 dentro x 0,01-0,05
    fora) e com o croma do texto novo nítido demais. Recodifica com as MESMAS tabelas e a MESMA subamostragem o recorte
    alinhado à grade de MCU do arquivo armazenado (16x16 em 4:2:0), com uma MCU de margem para o croma ser
    interpolado com os vizinhos de verdade, e devolve só as MCUs que a pegada toca; a pegada cresce para os pixels
    que mudaram. Devolve a descrição para o rótulo, ou None (a fonte não é JPEG, ``acabamento.requantizar`` =
    "nunca")."""
    info = getattr(c.documento, "jpeg", None) if c.documento is not None else None
    if not info or c.acabamento.get("requantizar", "auto") == "nunca" or not res.pegada.any():
        return None
    cinza, sub = info["modo"] == "L", info["subsampling"]
    if not cinza and sub not in (0, 1, 2):
        return "JPEG de origem com subamostragem que o codificador não reproduz: não requantizado"
    mw, mh = (8, 8) if cinza else {0: (8, 8), 1: (16, 8), 2: (16, 16)}[sub]
    ori, (Ws, Hs) = info["orientacao"], info["tamanho"]
    ys, xs = np.nonzero(res.pegada)
    fy0, fy1, fx0, fx1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1  # a pegada no recorte
    sy0, sx0, sy1, sx1 = _caixa_mapeada(lambda i, j: _no_armazenado(ori, Hs, Ws, i, j),
                                        res.y0 + fy0, res.x0 + fx0, res.y0 + fy1, res.x0 + fx1)
    ey0, ex0 = max(0, sy0 // mh * mh - mh), max(0, sx0 // mw * mw - mw)  # na grade, com uma MCU de margem
    ey1, ex1 = min(Hs, -(-sy1 // mh) * mh + mh), min(Ws, -(-sx1 // mw) * mw + mw)
    Y0, X0, Y1, X1 = _caixa_mapeada(lambda r, q: _na_exibida(ori, Hs, Ws, r, q), ey0, ex0, ey1, ex1)
    antes = c.img[Y0:Y1, X0:X1]
    atual = antes.copy()
    peg = np.zeros(atual.shape[:2], bool)
    dy, dx = res.y0 + fy0 - Y0, res.x0 + fx0 - X0
    peg[dy:dy + fy1 - fy0, dx:dx + fx1 - fx0] = res.pegada[fy0:fy1, fx0:fx1]
    sub_atual = atual[dy:dy + fy1 - fy0, dx:dx + fx1 - fx0]
    sub_atual[:] = np.where(res.pegada[fy0:fy1, fx0:fx1, None], res.recorte[fy0:fy1, fx0:fx1], sub_atual)
    arm = np.ascontiguousarray(_ARMAZENAR[ori](atual))
    buf = io.BytesIO()
    if cinza:
        luma = np.clip(arm.astype(np.float32) @ np.float32([0.299, 0.587, 0.114]) + 0.5, 0, 255).astype(np.uint8)
        Image.fromarray(luma).save(buf, "JPEG", qtables=info["qtables"])
        dec = np.repeat(np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("L"))[..., None], 3, 2)
    else:
        Image.fromarray(arm).save(buf, "JPEG", qtables=info["qtables"], subsampling=sub)
        dec = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    # as MCUs que a pegada toca (o recorte começa na grade: a MCU k ocupa [k*mh, (k+1)*mh))
    p_arm = _ARMAZENAR[ori](peg)
    hh, ww = p_arm.shape
    t = np.zeros((-(-hh // mh) * mh, -(-ww // mw) * mw), bool)
    t[:hh, :ww] = p_arm
    mcu = t.reshape(t.shape[0] // mh, mh, t.shape[1] // mw, mw).any((1, 3))
    m = _EXIBIR[ori](np.repeat(np.repeat(mcu, mh, 0), mw, 1)[:hh, :ww])
    novo = np.where(m[..., None], _EXIBIR[ori](dec), atual)
    pegada = peg | (novo != antes).any(2)
    ys, xs = np.nonzero(pegada)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    res.y0, res.y1, res.x0, res.x1 = Y0 + y0, Y0 + y1, X0 + x0, X0 + x1
    res.recorte = np.ascontiguousarray(novo[y0:y1, x0:x1])
    res.pegada = pegada[y0:y1, x0:x1]
    c.fornecedores["jpeg"] = "tabelas_da_fonte"
    sub_txt = "cinza" if cinza else {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}[sub]
    return f"requantizado com as tabelas do JPEG de origem ({sub_txt}, grade {mw}x{mh} do arquivo)"


# Disfarce depois da edição, nas frequências que falsificadores reais usaram no Photoshop (FSTS,
# NeurIPS 2025 D&B: desfoque 12–17%, aguçar 13%, curvas 8–17%, cor 10%). Treinar com esses retoques
# levou o F1 em falsificações reais de 0,20 para 0,48 (TruFor). Amador usa metade.
_DISFARCES = (("desfocar", 0.15), ("nitidez", 0.13), ("curvas", 0.12), ("cor", 0.10))


# retoques que só o operador humano usa (rotulador.humano): Adicionar Ruído e Desfoque de Superfície
_RETOQUES_HUMANOS = ("ruido", "superficie")


def _disfarcar(c: _C, res: Resultado, sempre: bool, fixo: dict | None = None) -> tuple[Resultado, list[str]]:
    """Retoca a região da edição. ``fixo`` ({ops, parâmetros}): os retoques e os parâmetros de quem edita (o
    operador humano); sem ele, o hábito do falsificador, ou o sorteio nas frequências de ``_DISFARCES``."""
    if fixo is None:
        fixo = c.habitos.get("disfarce") if isinstance(c.habitos.get("disfarce"), dict) else None
    if fixo is not None:  # o falsificador repete o próprio retoque (FSTS: recorrência individual)
        ops = [n for n in fixo.get("ops", []) if n in {x for x, _ in _DISFARCES} or n in _RETOQUES_HUMANOS]
    else:
        fator = 0.5 if c.tec.nivel == "simples" else 1.0
        ops = [n for n, p in _DISFARCES if c.rng.random() < p * fator]
    if sempre and not ops:
        ops = [c.rng.choice([n for n, _ in _DISFARCES])]
    if not ops:
        return res, []
    fixo = fixo or {}
    pad = 3
    X0, Y0 = max(0, res.x0 - pad), max(0, res.y0 - pad)
    X1, Y1 = min(c.W, res.x1 + pad), min(c.H, res.y1 + pad)
    antes = c.img[Y0:Y1, X0:X1].astype(np.float32)
    atual = antes.copy()
    sub = atual[res.y0 - Y0:res.y1 - Y0, res.x0 - X0:res.x1 - X0]
    sub[:] = np.where(res.pegada[..., None], res.recorte.astype(np.float32), sub)
    m = np.zeros(atual.shape[:2], np.float32)
    m[res.y0 - Y0:res.y1 - Y0, res.x0 - X0:res.x1 - X0] = res.pegada
    alfa = np.maximum(cv2.GaussianBlur(cv2.dilate(m, np.ones((3, 3), np.uint8)), (0, 0), 1.0), m)
    proc, feitos = atual, []
    for op in ops:
        if op == "desfocar":
            s = float(fixo.get("sigma") or c.rng.uniform(0.5, 1.2))
            proc = cv2.GaussianBlur(proc, (0, 0), s)
            feitos.append(f"desfoque σ={s:.2f}")
        elif op == "nitidez":
            a = float(fixo.get("nitidez") or c.rng.uniform(0.3, 0.8))
            proc = proc + a * (proc - cv2.GaussianBlur(proc, (0, 0), 1.0))
            feitos.append(f"aguçado ({a:.2f})")
        elif op == "curvas":
            g, k = float(fixo.get("gama") or c.rng.uniform(0.85, 1.15)), float(fixo.get("contraste") or c.rng.uniform(0.92, 1.08))
            proc = 255.0 * np.power(np.clip((proc - 128) * k + 128, 0, 255) / 255.0, g)
            feitos.append(f"curvas (gama {g:.2f}, contraste {k:.2f})")
        elif op == "ruido":
            r = fixo.get("ruido") or {}
            proc = humano.ruido(proc, r, c.semente)
            feitos.append(f"ruído {r.get('quantidade', 2.0)}% ({r.get('distribuicao', 'gaussiana')}"
                          f"{', monocromático' if r.get('monocromatico') else ''})")
        elif op == "superficie":
            r = fixo.get("superficie") or {}
            proc = humano.superficie(proc, r)
            feitos.append(f"desfoque de superfície (raio {r.get('raio', 2)}, limiar {r.get('limiar', 15)})")
        else:
            ganho = np.float32(fixo.get("cor") or [c.rng.uniform(0.95, 1.05) for _ in range(3)])
            proc = 255.0 - (255.0 - proc) * ganho  # muda a tinta; o papel (perto de 255) fica
            feitos.append("cor ajustada (" + "/".join(f"{v:.2f}" for v in ganho) + ")")
    novo = atual * (1 - alfa[..., None]) + proc * alfa[..., None]
    pegada = (np.abs(novo - antes).max(2) > 0.5) | (m > 0)
    novo = np.clip(novo + 0.5, 0, 255).astype(np.uint8)
    return Resultado(Y0, Y1, X0, X1, novo, pegada, meta=res.meta), feitos


def _nivel_pericia(c: _C) -> str:
    """Nível de acabamento forense: explícito na receita, ou do hábito do falsificador, ou 'auto'
    (sem os passos novos — o comportamento de sempre)."""
    p = c.acabamento.get("pericia")
    if p in pericia.NIVEIS:
        return p
    h = c.habitos.get("pericia")
    if h in pericia.NIVEIS:
        return h
    return "auto"


def _grao_de(c: _C, ac: dict, niv: str) -> str:
    """Que grão entra na região: "espectral" (o mesmo espectro do papel, rotulador.pericia) ou "branco" (o Add
    Noise do Photoshop: só a variância). A receita (``acabamento.grao``) manda; a edição padrão (mesma fonte) é
    sempre espectral (o branco, sem correlação espacial — lag-1 0,02 x 0,26 no papel —, fica como diversidade
    das técnicas antigas); senão o hábito do falsificador; senão 70% espectral (o ruído branco virava a pista
    nº 1 das edições profissionais)."""
    if niv == "forense":
        return "espectral"
    pedido = ac.get("grao", "auto")
    if pedido in ("espectral", "branco"):
        return pedido
    if c.tec.id in _TOP10:
        return "espectral"
    habito = c.habitos.get("grao")
    if habito in ("espectral", "branco"):
        return habito
    return "espectral" if c.rng.random() < 0.7 else "branco"


def _acabar_humano(c: _C, res: Resultado) -> Resultado:
    """O acabamento do operador humano: os retoques DELE na seleção (o Desfoque Gaussiano de quem desfoca à mão, aguçar,
    Desfoque de Superfície, Adicionar Ruído), nada da perícia automática (grão com o espectro do papel, luz e nitidez
    casadas: ninguém faz isso no editor) e a requantização só quando ele salva com as tabelas da origem; o JPEG do
    editor é aplicado ao par inteiro na gravação (gerador)."""
    h = c.humano
    r = h.get("retoques") or {}
    ops, fixo = [], {}
    if h.get("nitidez") == "manual":  # Desfoque Gaussiano na seleção até parecer o documento (e um pouco além)
        ops.append("desfocar")
        fixo["sigma"] = round(float(np.clip(c.desfoque * float(h.get("excesso", 1.2)) + 0.05, 0.3, 1.0)), 2)
    for op in ("nitidez", "superficie", "ruido"):  # o ruído por último: vai por cima de tudo
        if op in r:
            ops.append(op)
            fixo[op] = r[op]
    feitos = []
    if ops:
        res, d = _disfarcar(c, res, False, {"ops": ops, **fixo})
        feitos += [f"retoque: {x}" for x in d]
    c.fornecedores["grao"] = "ruido_do_editor" if "ruido" in r else "nenhum"
    if h.get("salvar") == "mesmas":
        requant = _requantizar(c, res)
        if requant:
            feitos.append(requant)
    c.fornecedores["salvar"] = h.get("salvar")
    res.meta = {**res.meta, "acabamento": feitos, "pericia": _nivel_pericia(c), "humano": humano.descrever(h),
                "fornecedores": {**res.meta.get("fornecedores", {}), **c.fornecedores}}
    return res


def _acabar(c: _C, res: Resultado) -> Resultado:
    if c.humano is not None and c.tec.id != "trecho.jitter":
        return _acabar_humano(c, res)
    ac = c.acabamento
    feitos = []
    niv = _nivel_pericia(c)
    if niv == "cru":  # edição limpa, sem acabamento nenhum (a ponta fácil, rotulada como tal)
        res.meta = {**res.meta, "acabamento": feitos, "pericia": niv,
                    "fornecedores": {**res.meta.get("fornecedores", {}), **c.fornecedores}}
        return res
    forense = niv in ("profissional", "forense")  # esconde como um profissional: nada de retoque aleatório
    disfarce = ac.get("disfarce", "auto")
    if not forense and disfarce != "nunca" and c.tec.nivel in ("profissional", "simples") and c.tec.id != "trecho.jitter":
        res, d = _disfarcar(c, res, disfarce == "sempre")
        feitos += [f"disfarce: {x}" for x in d]
    ruido = ac.get("ruido", "auto")
    quer_grao = (c.tec.nivel in ("profissional", "ia")) if ruido == "auto" else ruido == "sempre"
    grao = grao_feito = None
    if quer_grao or niv == "forense":
        grao = "branco" if c.tec.id == "trecho.jitter" else _grao_de(c, ac, niv)  # o jitter já é retoque de textura
    if grao == "branco":
        antes = res.recorte
        res = auto._com_ruido(c.img, c.base, res, c.semente)
        if res.recorte is not antes:  # o rótulo diz o que entrou: "o grão já é igual" não acrescenta nada
            grao_feito = "branco"
            feitos.append("ruído branco igualado ao papel")
    # papel reconstruído liso (balde, difusão) não segue o gradiente de luz da foto: degrau de 4-7 níveis na borda.
    # O apagador "local" já segue o gradiente; PatchMatch e carimbo copiam papel de verdade
    ilum = c.fornecedores.get("apagar") in ("chapado", "telea", "ns")
    if (forense or grao == "espectral" or ilum) and c.tec.id != "trecho.jitter":
        # perícia: grão com o espectro do papel e, do profissional para cima, iluminação e nitidez casadas
        rec = res.recorte.astype(np.float32)
        rec2, sinais = pericia.aplicar(rec, res, c, niv if forense else "padrao", c.semente,
                                       grao="espectral" if grao == "espectral" else None, iluminacao=ilum)
        if sinais:
            dif = np.abs(rec2 - rec).max(2) > 0.5
            res.recorte = np.clip(rec2 + 0.5, 0, 255).astype(np.uint8)
            res.pegada = res.pegada | dif
            feitos += [f"perícia: {x}" for x in sinais]
        if grao == "espectral" and "grão espectral do papel" in sinais:
            grao_feito = "espectral"
    if grao:
        c.fornecedores["grao"] = grao_feito or "nenhum"
    try:
        p_jpeg = float(ac.get("jpeg", 0) or 0)
    except (TypeError, ValueError):
        p_jpeg = 0.0
    if p_jpeg > 0 and c.rng.random() < p_jpeg:
        q = c.rng.randint(60, 90)
        res = _jpeg_local(c, res, q)
        feitos.append(f"recompressão JPEG local q{q}")
    requant = _requantizar(c, res)  # por último: o que sai é o que o JPEG de origem teria guardado
    if requant:
        feitos.append(requant)
    res.meta = {**res.meta, "acabamento": feitos, "pericia": niv,
                "fornecedores": {**res.meta.get("fornecedores", {}), **c.fornecedores}}
    return res


_CONFUSOES = str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "|": "1"})


def _normal(s: str) -> str:
    """Texto comparável entre o esperado e o lido pelo OCR: só letras e dígitos, sem acento, e as
    confusões clássicas do OCR resolvidas igual dos dois lados (O/0, I/L/1, "R$" lido como "RS")."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    s = re.sub(r"^R\s*[$S]+\s*(?=\d)", "R", s.strip())
    return "".join(ch for ch in s if ch.isalnum()).translate(_CONFUSOES)


def lido_igual(lido: str, esperado: str) -> bool:
    """O OCR leu o esperado? Aceita só as confusões que não mudam um caractere por outro de verdade:
    as de _normal e um separador ("/", "-") lido como "1"/"I" a mais. Um dígito trocado continua
    sendo diferença (é justamente o que a edição muda)."""
    a, b = _normal(lido), _normal(esperado)
    if a == b:
        return True
    if len(a) == len(b) + 1:
        return any(a[:k] + a[k + 1:] == b for k in range(len(a)) if a[k] == "1")
    return False


def _conferir(c: _C, res: Resultado) -> Resultado:
    """Relê o campo editado com o reconhecedor do OCR: o resultado tem de ser lido como o valor novo.
    Só vale quando o OCR lê o campo ORIGINAL certo (controle); senão, não há como julgar."""
    novo = res.meta.get("texto_novo")
    if c.tipo != "texto" or not novo or c.p.get("conferir_ocr") is False or c.tec.id in _SEM_CONFERENCIA:
        return res
    leitor = ocr.reconhecer
    x0, y0, x1, y1 = c.caixa
    h = y1 - y0
    m, m2 = max(3, int(0.2 * h)), max(4, int(0.45 * h))
    ry0, ry1 = max(0, y0 - m), min(c.H, y1 + m)
    try:
        if not lido_igual(leitor(c.img[ry0:ry1, max(0, x0 - m):min(c.W, x1 + m)]), c.texto):
            res.meta["conferido_ocr"] = None
            return res
        # na horizontal, a caixa do campo mais a TINTA nova (não a região inteira do apagado, que passa da caixa): com
        # a região, a leitura pegava o fim da palavra vizinha ("Emissão em 25/01/2026" lia "n27/07/2026", o "m" do
        # "em") e a edição certa era descartada
        cinza = res.recorte.mean(axis=2) if res.recorte.ndim == 3 else res.recorte.astype(float)
        fundo = float(np.median(cinza[res.pegada])) if res.pegada.any() else 255.0
        # tinta NOVA: escura e mudada (a seleção com margem do operador humano leva o "m" vizinho intacto na região)
        mudou = np.abs(res.recorte.astype(int) - c.img[res.y0:res.y1, res.x0:res.x1].astype(int)).max(axis=-1) > 30
        cols = np.flatnonzero((res.pegada & mudou & (cinza < fundo - 40)).any(axis=0))
        ix0, ix1 = (res.x0 + int(cols[0]), res.x0 + int(cols[-1]) + 1) if len(cols) else (x0, x1)
        cx0, cx1 = max(0, min(x0, res.x0) - m), min(c.W, max(x1, res.x1) + m)
        cy0, cy1 = min(max(0, y0 - m2), res.y0), max(min(c.H, y1 + m2), res.y1)
        rec = c.img[cy0:cy1, cx0:cx1].copy()
        sub = rec[res.y0 - cy0:res.y1 - cy0, res.x0 - cx0:res.x1 - cx0]
        sub[:] = np.where(res.pegada[..., None], res.recorte, sub)
        lx0, lx1 = max(cx0, min(x0, ix0) - m) - cx0, min(cx1, max(x1, ix1) + m) - cx0
        # duas leituras com recortes diferentes: letra ambígua (um 6 que quase é 8) lê diferente em cada uma. A 2ª muda
        # a ESCALA (1,5x, margem lateral maior), não a altura: mais alta, ela pegava a linha de cima (o rótulo da
        # célula do boleto) e lia "00/2026" num "23/02/2026" bem escrito; e só julga se o original lê certo nela
        lidos = [leitor(rec[ry0 - cy0:ry1 - cy0, lx0:lx1])]
        bx0, bx1 = max(cx0, min(x0, ix0) - m2) - cx0, min(cx1, max(x1, ix1) + m2) - cx0

        def ampliar(a):
            return cv2.resize(a, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        if lido_igual(leitor(ampliar(c.img[ry0:ry1, max(0, x0 - m2):min(c.W, x1 + m2)])), c.texto):
            lidos.append(leitor(ampliar(rec[ry0 - cy0:ry1 - cy0, bx0:bx1])))
    except ocr.OCRIndisponivel:
        return res
    for lido in lidos:
        if not lido_igual(lido, novo):
            raise ErroOperacao(f"conferência por OCR: o resultado lê '{lido}', não '{novo}' (descartado)")
    res.meta["conferido_ocr"] = True
    return res


def texto_claro_em_fundo_escuro(img: np.ndarray, caixa) -> bool:
    """O campo é texto CLARO sobre fundo escuro/colorido (o CNPJ branco na faixa azul do cabeçalho)? Os apagadores e
    a cor da tinta supõem tinta escura sobre papel claro: com o recorte passando da faixa, o papel branco de fora vira
    o "papel" e o azul vira "tinta" — o texto antigo engordava num borrão branco. Pelo anel em volta da caixa: fundo
    escuro (luma < 120) e a parte clara de dentro bem mais clara que ele."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in caixa)
    m = max(2, (y1 - y0) // 6)
    X0, Y0, X1, Y1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
    lum = img[Y0:Y1, X0:X1].astype(np.float32) @ _LUMA
    anel = np.ones(lum.shape, bool)
    anel[y0 - Y0 + 1:y1 - Y0 - 1, x0 - X0 + 1:x1 - X0 - 1] = False
    if not anel.any() or anel.all():
        return False
    fundo = float(np.median(lum[anel]))
    dentro = lum[~anel]
    return fundo < 120 and float(np.percentile(dentro, 95)) > fundo + 60


# técnicas que só trocam o código (desenham o código inteiro de novo) não dependem da cor da tinta
_SEM_TINTA = frozenset({"codigo.regenerar", "codigo.qr"})


def tecnica(img: np.ndarray, p: dict, sel, base: np.ndarray = None, carregar_doador=None, chave_cache=None,
            documento=None, editor=None, **_) -> Resultado:
    c = _C(img, p, base, documento, carregar_doador, chave_cache, editor)
    t = c.tec
    if c.tipo not in t.tipos:
        raise ErroOperacao(f"'{t.nome}' não serve para campo do tipo {c.tipo}")
    if c.tipo == "texto" and t.id not in _SEM_TINTA and not t.precisa_ia and texto_claro_em_fundo_escuro(img, c.caixa):
        raise ErroOperacao("texto claro sobre fundo escuro (faixa colorida): as técnicas de tinta escura sobre papel "
                           "deixariam um borrão")
    if t.categorias is not None and c.categoria not in t.categorias:
        raise ErroOperacao(f"'{t.nome}' não serve para a categoria {c.categoria}")
    kw = {}
    if c.categoria in _COM_ACENTO and "acentos" in inspect.signature(geradores.usando).parameters:
        acentos = _acentos(c)
        if acentos is not None:
            kw["acentos"] = acentos
    # os campos do documento da versão (só se já lidos: não dispara OCR): datas, valores e números trocados ficam
    # coerentes entre si na versão inteira
    with geradores.usando(c.rng, getattr(documento, "split", None), documento=getattr(documento, "_campos", None), **kw):
        res = _IMPL[t.id](c)
        return _conferir(c, _acabar(c, res))


_COM_ACENTO = frozenset({"nome", "endereço", "bairro", "cidade/uf"})  # categorias cujos valores falsos têm acento
_acentos_doc: OrderedDict = OrderedDict()
_acentos_lock = threading.Lock()


def _acentos(c: _C) -> bool | None:
    """O documento escreve com acento (JOÃO, CONCEIÇÃO) ou sem (JOAO, CONCEICAO)? O valor falso segue: um nome
    acentuado num documento todo sem acento (ou o contrário) é pista para quem só lê o texto. Uma vez por
    documento (``ocr.usa_acentos``, quando existe); sem o contexto do documento, pelo recorte do campo. None =
    não dá para saber."""
    f = getattr(ocr, "usa_acentos", None)
    if f is None:
        return None
    doc = c.documento
    if doc is not None:
        with _acentos_lock:
            if c.chave in _acentos_doc:
                return _acentos_doc[c.chave]
    try:
        v = f(doc.img, linhas=doc.linhas) if doc is not None else f(c.img, caixa=c.caixa)
    except (ErroOperacao, ocr.OCRIndisponivel):
        v = None
    if doc is not None:
        with _acentos_lock:
            _acentos_doc[c.chave] = v
            while len(_acentos_doc) > 64:
                _acentos_doc.popitem(last=False)
    return v


def aplicavel(t: catalogo.Tecnica, campo: dict) -> bool:
    """Filtro rápido (sem olhar pixels): a técnica pode ser tentada neste campo? O pseudo-campo da página
    (tipo "pagina", só do ``pagina.recriar``) passa pelo mesmo filtro de tipos."""
    if campo.get("tipo", "texto") not in t.tipos:
        return False
    if t.categorias is not None and campo.get("categoria") not in t.categorias:
        return False
    if campo.get("tipo", "texto") != "texto":
        return True
    s = str(campo.get("texto", ""))
    if t.granularidade == "caractere" and not any(ch.isdigit() for ch in s) and campo.get("categoria") not in LETRA_PLAUSIVEL:
        return False  # no sorteio, letra não é trocada (erro de grafia); ver _C.alvo
    if t.granularidade == "caractere" or t.id == "trecho.trocar":
        return sum(ch.isalnum() for ch in s) >= 2
    return bool(s.strip())
