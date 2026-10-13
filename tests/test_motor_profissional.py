"""Perfil profissional do gerador (o motor da tela de um clique): só técnicas profissionais, sorteadas por
granularidade (campo inteiro, trecho, caractere, acréscimo) nos pesos de ``PERFIL_PROFISSIONAL``; classes extras às
vezes (``GRUPOS_EXTRAS``, na taxa de ``EXTRAS_PROB``); o mesmo dado repetido com o MESMO valor novo em todo lugar,
mesmo quando a primeira edição foi parcial ou falhou; a receita ``{"perfil": "profissional"}`` expandida; tudo
determinístico pela semente da versão. Documentos sintéticos (nenhum dado real)."""
import json
import random
import shutil
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PIL import Image

from rotulador import catalogo, gerador
from rotulador.ops import geradores
from rotulador.sessao import ErroOperacao

try:
    import rapidocr_onnxruntime  # noqa: F401
    TEM_OCR = True
except ImportError:
    TEM_OCR = False

IDS = {"nome": 1, "cpf/cnpj": 2, "endereço": 3, "bairro": 4, "cep": 5, "cidade/uf": 6, "data": 7,
       "mês de referência": 8, "valor": 9, "consumo/leitura": 10, "codigo barras": 11, "linha digitável": 12,
       "identificador documento": 13, "emissor/logotipo": 14, "qr code/pix": 15, "outro": 16}
RAIZ = Path(__file__).resolve().parents[1]
AMADORAS = {"campo.amador", "campo.corretivo", "campo.ocultar", "campo.rabiscar", "campo.borrar",
            "caractere.acrescentar_tracos", "caractere.raspar_tracos", "trecho.jitter", "campo.copiar_interno",
            "campo.reescrever", "caractere.glifo_externo", "campo.colagem_externa", "bloco.colagem_externa",
            "caractere.apagar", "ia.caractere", "ia.remover", "pagina.recriar"}


def _c(i, cat, txt, y, x=10, tipo="texto"):
    return {"id": f"t{i}", "categoria": cat, "texto": txt, "caixa": [x, y, x + 300, y + 20], "tipo": tipo}


def documento():
    """Uma conta com o nome em dois lugares e um campo de cada classe que o perfil pode editar."""
    return [_c(0, "nome", "MARIA DA SILVA PRADO", 10), _c(1, "endereço", "RUA DAS FLORES, 105 APTO 12", 40),
            _c(2, "bairro", "JARDIM AMERICA", 70), _c(3, "cep", "60750-720", 100), _c(4, "cidade/uf", "FORTALEZA - CE", 130),
            _c(5, "cpf/cnpj", "123.456.789-09", 160), _c(6, "data", "10/09/2026", 190), _c(7, "mês de referência", "AGO/2026", 220),
            _c(8, "valor", "R$ 151,37", 250), _c(9, "consumo/leitura", "282 kWh", 280),
            _c(10, "codigo barras", "", 400, tipo="barras"), _c(11, "nome", "Maria da Silva Prado", 500)]


class _Ctx:
    def __init__(self, lista):
        self.campos, self.linhas, self.split, self.doc, self.vetorial = lista, [], "treino", "x", False


class _Sessao:
    def __init__(self):
        self.ids_categoria, self.base = IDS, np.zeros((600, 800, 3), np.uint8)
        self.atual = self.base
        self.historico_, self.lock = [], threading.RLock()

    def desfazer(self):
        if self.historico_:
            self.historico_.pop()


def _novo(p):
    """O texto que a técnica (falsa) escreveria: o imposto, ou um valor que mostra o tamanho da troca."""
    antigo, tid = p["campo"]["texto"], p["tecnica"]
    if p.get("texto"):
        return p["texto"]
    if tid == "trecho.trocar":
        return antigo.rsplit(" ", 1)[0] + " COSTA" if not antigo[-1].isdigit() else antigo[:-1] + "9"
    if tid.startswith("caractere."):
        return antigo.replace(next(ch for ch in antigo if ch.isdigit()), "7", 1)
    if tid == "campo.acrescentar":
        return antigo + " JUNIOR"
    if tid == "campo.remover":
        return antigo.rsplit(" ", 2)[0]
    return geradores.como_o_antigo(antigo, "BRUNO LIMA") if p["campo"]["categoria"] == "nome" else "NOVO " + antigo


class _Falso:
    """No lugar de ``_executar_edicao``: grava os parâmetros e devolve o texto de ``_novo`` (ou falha nas técnicas
    de ``falha``: ids, ou (id, categoria))."""

    def __init__(self, falha=(), novo=_novo):
        self.falha, self.chamadas, self.novo = set(falha), [], novo

    def __call__(self, sessao, ctx, p, rng, r, editor):
        self.chamadas.append(p)
        if p["tecnica"] in self.falha or (p["tecnica"], p["campo"]["categoria"]) in self.falha:
            raise ErroOperacao("conferência por OCR: descartado")
        t, antigo, novo = catalogo.TECNICAS[p["tecnica"]], p["campo"]["texto"], self.novo(p)
        i = next((k for k, (a, b) in enumerate(zip(antigo, novo)) if a != b), min(len(antigo), len(novo)))
        res = SimpleNamespace(meta={"texto_novo": novo, "tecnica": t.id, "campo": p["campo"]["id"], "texto_antigo": antigo,
                                    "rotulo": t.nome, "alteracoes": [{"i": i, "de": antigo[i:], "para": novo[i:]}]},
                              x0=0, y0=0, x1=1, y1=1)
        ent = SimpleNamespace(info={"categoria": p["campo"]["categoria"]})
        sessao.historico_.append(ent)
        return res, ent


def _aplicar(rec, sem, falso=None, lista=None):
    falso = falso or _Falso()
    r = gerador.Receita(rec, IDS)
    s, info = _Sessao(), {}
    with mock.patch.object(gerador, "_executar_edicao", side_effect=falso), \
            mock.patch.object(gerador, "_fonte_identificada", return_value=True):
        feitos = gerador.aplicar_receita(s, _Ctx(lista or documento()), r, None, random.Random(sem), info=info)
    return feitos, info, falso, s


class TestSoProfissionais(unittest.TestCase):
    def test_receita_sem_amadoras(self):
        for com_ia in (False, True):
            rec = gerador.receita_profissional(IDS, com_ia=com_ia)
            self.assertFalse(set(rec["tecnicas"]) & AMADORAS, rec["tecnicas"])
            self.assertTrue(all(catalogo.TECNICAS[t].nivel in ("profissional", "ia") for t in rec["tecnicas"]))
            self.assertEqual("ia.reescrever" in rec["tecnicas"], com_ia)
        self.assertFalse(set(gerador.GRUPO_DA_TECNICA) & AMADORAS)
        # quem manda técnica junto com o perfil não religa a amadora
        r = gerador.Receita({"perfil": "profissional", "tecnicas": {"campo.amador": 5, "campo.corretivo": 5}}, IDS)
        self.assertFalse(set(r.tecnicas) & AMADORAS)

    def test_sorteio_nunca_tira_amadora(self):
        r = gerador.Receita({"perfil": "profissional"}, IDS)
        rng, vistas = random.Random(1), Counter()
        for f in documento():
            for _ in range(150):
                for t in gerador._tentativas(f, r, rng, False, vetorial=True, ia_online=True):
                    vistas[t.id] += 1
        self.assertFalse(set(vistas) & AMADORAS, vistas)
        self.assertNotIn("ia.reescrever", vistas)  # a IA só com com_ia
        self.assertEqual(vistas["codigo.regenerar"], 0)  # código só se marcado (base)

    def test_versoes_so_com_profissionais(self):
        for sem in range(40):
            feitos, info, falso, _ = _aplicar({"perfil": "profissional"}, sem)
            self.assertFalse({p["tecnica"] for p in falso.chamadas} & AMADORAS)
            for p in falso.chamadas:  # o como profissional: sem vestígio, sem balde/borrão, sem retângulo
                op = p["opcoes"]
                if "apagar" in op:  # só quem copia a textura do papel (o campo inteiro segue com o apagador local)
                    self.assertIn(op["apagar"], ("patchmatch", "carimbo"))
                self.assertNotIn(op.get("colagem"), ("retangulo", "papel"))
                self.assertIn(op.get("vestigio", "nenhum"), ("nenhum",))
                if p["tecnica"] == "campo.remover":
                    self.assertEqual(op["parte"], "final")


class TestGranularidade(unittest.TestCase):
    def setUp(self):
        self.r = gerador.Receita({"perfil": "profissional"}, IDS)

    def _distribuicao(self, f, n=4000, **kw):
        servem = gerador._servem_profissional(f, self.r, **kw)
        rng = random.Random(11)
        cont = Counter(gerador._sortear_profissional(servem, rng)[0] for _ in range(n))
        return {g: cont[g] / n for g in cont}, servem

    def test_pesos_no_endereco(self):
        """O endereço com número e complemento aceita todos os grupos: a distribuição é a dos pesos."""
        dist, servem = self._distribuicao(_c(1, "endereço", "RUA DAS FLORES, 105 APTO 12", 40))
        self.assertEqual(set(servem), {"campo", "trecho", "caractere", "acrescimo"})
        for g, (peso, _) in gerador.PERFIL_PROFISSIONAL.items():
            self.assertAlmostEqual(dist.get(g, 0), peso, delta=0.03, msg=(g, dist))

    def test_nome_renormaliza(self):
        """Nome não tem dígito: sem grupo caractere, os outros se renormalizam."""
        dist, servem = self._distribuicao(_c(0, "nome", "MARIA DA SILVA PRADO", 10))
        self.assertNotIn("caractere", servem)
        soma = sum(p for g, (p, _) in gerador.PERFIL_PROFISSIONAL.items() if g != "caractere")
        for g in ("campo", "trecho", "acrescimo"):
            self.assertAlmostEqual(dist.get(g, 0), gerador.PERFIL_PROFISSIONAL[g][0] / soma, delta=0.03, msg=(g, dist))

    def test_tecnicas_que_servem(self):
        r = self.r
        nome = _c(0, "nome", "MARIA", 10)  # uma palavra só: nada de trecho nem de "remover a parte final"
        servem = gerador._servem_profissional(nome, r)
        self.assertNotIn("trecho", servem)
        self.assertEqual([t.id for t in servem.get("acrescimo", [])], ["campo.acrescentar"])
        end = _c(1, "endereço", "RUA DAS FLORES, 105", 40)  # sem complemento: não há parte final para tirar
        self.assertNotIn("campo.remover", [t.id for t in gerador._servem_profissional(end, r).get("acrescimo", [])])
        # o PDF vetorial só em PDF com texto
        ids = lambda **kw: [t.id for t in gerador._servem_profissional(end, r, **kw)["campo"]]  # noqa: E731
        self.assertNotIn("campo.reescrever_pdf", ids())
        self.assertIn("campo.reescrever_pdf", ids(vetorial=True))
        if "campo.letras_do_documento" in catalogo.TECNICAS:  # a técnica nova entra no campo inteiro quando existir
            self.assertIn("campo.letras_do_documento", ids())
        # IA: só com a receita pedindo e o servidor ligado
        com_ia = gerador.Receita({"perfil": "profissional", "com_ia": True}, IDS)
        self.assertNotIn("ia.reescrever", [t.id for t in gerador._servem_profissional(end, com_ia)["campo"]])
        self.assertIn("ia.reescrever", [t.id for t in gerador._servem_profissional(end, com_ia, ia_online=True)["campo"]])

    def test_imposto_e_reserva(self):
        f = _c(1, "endereço", "RUA DAS FLORES, 105 APTO 12", 40)
        rng = random.Random(3)
        mf = catalogo.TECNICAS["campo.mesma_fonte"]
        for _ in range(300):
            ts = gerador._tentativas(f, self.r, rng, imposto=True, vetorial=False, ia_online=False)
            self.assertTrue(all(gerador.GRUPO_DA_TECNICA[t.id] == "campo" for t in ts), ts)
            ts = gerador._tentativas(f, self.r, rng, imposto=False, vetorial=False, ia_online=False)
            self.assertEqual(ts[-1], mf)  # a parcial que falha vira campo inteiro (a reserva é a mesma fonte)
            self.assertEqual(len(ts), 1 if ts[0] is mf else 2)


class TestExtras(unittest.TestCase):
    TODAS = {c for g in gerador.GRUPOS_EXTRAS.values() for c in g}

    def _grupos(self, extras):
        return {g for g, cs in gerador.GRUPOS_EXTRAS.items() if set(cs) & set(extras)}

    def test_taxa(self):
        rng, n = random.Random(5), 6000
        cont = Counter(len(self._grupos(gerador._extras(self.TODAS, ["nome", "endereço"], rng))) for _ in range(n))
        for k, p in enumerate(gerador.EXTRAS_PROB):
            self.assertAlmostEqual(cont[k] / n, p, delta=0.03, msg=cont)

    def test_coerentes_e_presentes(self):
        rng = random.Random(6)
        for _ in range(3000):
            ex = gerador._extras(self.TODAS, ["nome", "endereço"], rng)
            if "cidade/uf" in ex:  # a cidade nova traz CEP e bairro dela
                self.assertTrue({"cep", "bairro"} <= set(ex), ex)
            if "data" in ex or "mês de referência" in ex:
                self.assertTrue({"data", "mês de referência"} <= set(ex), ex)
            self.assertFalse({"nome", "endereço"} & set(ex))
            # só o nome na versão: CEP/bairro/cidade não mudam; só o endereço: o CPF não muda
            self.assertFalse({"cep", "bairro", "cidade/uf"} & set(gerador._extras(self.TODAS, ["nome"], rng)))
            self.assertNotIn("cpf/cnpj", gerador._extras(self.TODAS, ["endereço"], rng))
            # só os grupos presentes no documento
            self.assertTrue(set(gerador._extras({"data", "mês de referência"}, ["nome"], rng)) <= {"data", "mês de referência"})
        self.assertEqual(gerador._extras(set(), ["nome"], rng), [])

    def test_taxa_nas_versoes(self):
        com = sum(bool(_aplicar({"perfil": "profissional"}, sem)[1]["estilo"]["extras"]) for sem in range(300))
        self.assertAlmostEqual(com / 300, 1 - gerador.EXTRAS_PROB[0], delta=0.08)
        sem_extras = [_aplicar({"perfil": "profissional", "extras": False}, sem)[1] for sem in range(30)]
        self.assertTrue(all(i["estilo"]["extras"] == [] for i in sem_extras))
        self.assertTrue(all(set(i["classes"]) <= {"nome", "endereço"} for i in sem_extras))


class TestCoerencia(unittest.TestCase):
    """O mesmo nome em dois lugares (e um terceiro, em minúsculas): o mesmo valor novo em todo lugar."""

    def setUp(self):
        self.lista = [_c(0, "nome", "MARIA DA SILVA PRADO", 10), _c(1, "nome", "Maria da Silva Prado", 300),
                      _c(2, "nome", "MARIA DA SILVA PRADO", 500)]
        self.r = gerador.Receita({"perfil": "profissional", "categorias_base": ["nome"], "extras": False}, IDS)

    def _editar(self, falso):
        trecho_primeiro = lambda servem, rng: (("trecho", servem["trecho"][0]) if "trecho" in servem  # noqa: E731
                                               else ("campo", servem["campo"][0]))
        falhas = []
        with mock.patch.object(gerador, "_executar_edicao", side_effect=falso), \
                mock.patch.object(gerador, "_sortear_profissional", side_effect=trecho_primeiro), \
                mock.patch.object(gerador, "_fonte_identificada", return_value=True):
            feitos = gerador.editar_campos(_Sessao(), _Ctx(self.lista), self.lista, self.r, None, random.Random(2),
                                           ao_falhar=lambda t, f, e: falhas.append((t, f["id"], e)))
        return feitos, falhas

    def test_primeira_parcial(self):
        falso = _Falso()
        feitos, falhas = self._editar(falso)
        self.assertEqual([f["tecnica"] for f in feitos], ["trecho.trocar", "campo.mesma_fonte", "campo.mesma_fonte"])
        self.assertEqual({f["novo"].upper() for f in feitos}, {"MARIA DA SILVA COSTA"}, feitos)
        self.assertEqual(falso.chamadas[1]["texto"], "Maria da Silva Costa")  # no formato do lugar
        self.assertEqual(falhas, [])

    def test_primeira_parcial_falha(self):
        falso = _Falso(falha={"trecho.trocar"})
        feitos, falhas = self._editar(falso)
        self.assertEqual([f["tecnica"] for f in feitos], ["campo.mesma_fonte"] * 3)
        self.assertEqual(len({f["novo"].upper() for f in feitos}), 1, feitos)
        self.assertEqual(falhas, [])  # a parcial falhou, o campo inteiro coube: o campo não falhou
        self.assertEqual([p["tecnica"] for p in falso.chamadas[:2]], ["trecho.trocar", "campo.mesma_fonte"])
        self.assertNotIn("texto", falso.chamadas[1])

    def test_tudo_falha_no_primeiro(self):
        falso = _Falso(falha={("trecho.trocar", "nome")})
        conta = {"n": 0}
        real = falso.__call__

        def so_o_primeiro(sessao, ctx, p, rng, r, editor):  # o 1º campo recusa as duas técnicas
            if p["campo"]["id"] == "t0":
                conta["n"] += 1
                raise ErroOperacao("não coube")
            return real(sessao, ctx, p, rng, r, editor)
        feitos, falhas = self._editar(so_o_primeiro)
        self.assertEqual(conta["n"], 2)
        self.assertEqual(len(falhas), 1)
        self.assertIn("trecho.trocar", falhas[0][2])
        self.assertIn("campo.mesma_fonte", falhas[0][2])
        self.assertEqual(len(feitos), 2)  # o 2º decidiu o valor e o 3º recebeu o mesmo
        self.assertEqual(len({f["novo"].upper() for f in feitos}), 1, feitos)

    def test_no_documento_inteiro(self):
        """Em versões sorteadas de verdade, os dois nomes sempre saem iguais."""
        for sem in range(60):
            feitos, info, _, _ = _aplicar({"perfil": "profissional"}, sem)
            nomes = {f["novo"].upper() for f in feitos if f["categoria"] == "nome"}
            self.assertLessEqual(len(nomes), 1, (sem, feitos))


class TestGuardas(unittest.TestCase):
    """O que saiu ruim nas parciais do lote de 04/10 vira campo inteiro: o acento da letra apagada que ficava
    ("SIMÓES"), a troca que não mudou o dado (JOSÉ → JOSE), a letra desenhada numa fonte só aproximada (o PDF de
    verdade); e o mesmo dado com outro texto em volta ("em 25/01/2026" e "25/01/2026") recebe o mesmo valor novo."""

    def _editar(self, lista, falso, identificada=True, sorteio="trecho"):
        r = gerador.Receita({"perfil": "profissional", "extras": False,
                             "categorias_base": sorted({f["categoria"] for f in lista})}, IDS)
        primeiro = lambda servem, rng: ((sorteio, servem[sorteio][0]) if sorteio in servem  # noqa: E731
                                        else ("campo", servem["campo"][0]))
        falhas = []
        with mock.patch.object(gerador, "_executar_edicao", side_effect=falso), \
                mock.patch.object(gerador, "_sortear_profissional", side_effect=primeiro), \
                mock.patch.object(gerador, "_fonte_identificada", return_value=identificada):
            feitos = gerador.editar_campos(_Sessao(), _Ctx(lista), lista, r, None, random.Random(1),
                                           ao_falhar=lambda t, f, e: falhas.append(e))
        return feitos, falhas

    def test_acento_que_fica(self):
        def res(tid, i=0, de=""):
            return SimpleNamespace(meta={"tecnica": tid, "alteracoes": [{"i": i, "de": de}]})
        f = {"texto": "MARCOS VINÍCIUS ARAÚJO"}
        self.assertTrue(gerador._acento_que_fica(res("trecho.trocar", f["texto"].index("ARAÚJO")), f))
        self.assertTrue(gerador._acento_que_fica(res("trecho.trocar", 0), f))  # o resto da linha é reescrito junto
        g = {"texto": "ROSÂNGELA DIAS PEREIRA"}
        self.assertFalse(gerador._acento_que_fica(res("trecho.trocar", g["texto"].index("PEREIRA")), g))
        self.assertTrue(gerador._acento_que_fica(res("campo.remover", 0, "ARAÚJO"), f))
        self.assertFalse(gerador._acento_que_fica(res("campo.remover", 0, "APTO 12"), f))
        self.assertTrue(gerador._acento_que_fica(res("caractere.inserir"), {"texto": "RUA JOÃO NEGRÃO, 1897"}))
        self.assertFalse(gerador._acento_que_fica(res("caractere.redesenhar"), f))

    def test_parcial_ruim_vira_campo_inteiro(self):
        for texto, troca in (("MARIA DA SILVA", "Maria da Silva"),  # o dado não mudou (só a caixa)
                             ("MARCOS ARAÚJO", "MARCOS SIMOES")):  # o acento do Ú ficaria em cima do O
            falso = _Falso(novo=lambda p, troca=troca: troca if p["tecnica"] == "trecho.trocar" else _novo(p))
            feitos, falhas = self._editar([_c(0, "nome", texto, 10)], falso)
            self.assertEqual([p["tecnica"] for p in falso.chamadas], ["trecho.trocar", "campo.mesma_fonte"])
            self.assertEqual([f["tecnica"] for f in feitos], ["campo.mesma_fonte"])
            self.assertEqual(falhas, [])

    def test_pdf_com_fonte_de_reserva(self):
        """O editor de PDF sem a fonte embutida escreve em Helvetica/Times/Courier: a fonte padrão fica de fora."""
        falso = _Falso()
        real = falso.__call__

        def com_reserva(sessao, ctx, p, rng, r, editor):
            res, ent = real(sessao, ctx, p, rng, r, editor)
            if p["tecnica"] == "campo.reescrever_pdf":
                res.meta["opcoes"] = {"fonte_pdf": "substituta"}
            return res, ent
        lista = [_c(0, "nome", "MARIA DA SILVA", 10)]
        r = gerador.Receita({"perfil": "profissional", "categorias_base": ["nome"], "extras": False}, IDS)
        ctx = _Ctx(lista)
        ctx.vetorial = True
        def pdf_primeiro(servem, rng):
            return "campo", next(t for t in servem["campo"] if t.id == "campo.reescrever_pdf")
        with mock.patch.object(gerador, "_executar_edicao", side_effect=com_reserva), \
                mock.patch.object(gerador, "_sortear_profissional", side_effect=pdf_primeiro), \
                mock.patch.object(gerador, "_fonte_identificada", return_value=True):
            feitos = gerador.editar_campos(_Sessao(), ctx, lista, r, None, random.Random(1))
        self.assertEqual([p["tecnica"] for p in falso.chamadas], ["campo.reescrever_pdf", "campo.mesma_fonte"])
        self.assertEqual([f["tecnica"] for f in feitos], ["campo.mesma_fonte"])
        self.assertEqual(falso.chamadas[0]["opcoes"]["fonte_pdf"], "embutida")

    def test_fonte_nao_identificada(self):
        """Fonte só aproximada: nada de desenhar letra nova ao lado das originais; mover as do próprio campo pode."""
        end = [_c(1, "endereço", "RUA DAS FLORES, 105", 40)]
        falso = _Falso()
        feitos, _ = self._editar(end, falso, identificada=False)
        self.assertEqual([p["tecnica"] for p in falso.chamadas], ["campo.mesma_fonte"])  # o trecho nem rodou
        falso = _Falso()
        feitos, _ = self._editar(end, falso, identificada=False, sorteio="caractere")
        self.assertEqual(falso.chamadas[0]["tecnica"], "caractere.copiar_glifo")  # a letra é do documento
        self.assertFalse(gerador._DESENHA_NA_FONTE & {"caractere.copiar_glifo", "caractere.transpor", "campo.remover"})
        # antes do sorteio: o grupo fica com as que movem ou copiam as letras do documento (e o peso dele continua)
        chamadas = []
        servem = gerador._servem_profissional(_c(1, "endereço", "RUA DAS FLORES, 105 APTO 12", 40),
                                              gerador.Receita({"perfil": "profissional"}, IDS),
                                              fonte_ok=lambda: chamadas.append(1) or False)
        self.assertEqual(len(chamadas), 1)  # a identificação é consultada uma vez
        ids = {t.id for ts in servem.values() for t in ts}
        self.assertFalse(ids & gerador._DESENHA_NA_FONTE, ids)
        self.assertTrue({"caractere.copiar_glifo", "caractere.transpor", "campo.remover"} <= ids, ids)
        self.assertEqual(set(servem), {"campo", "caractere", "acrescimo"})

    def test_mesmo_dado_outro_texto(self):
        self.assertEqual(gerador._chave_valor("data", "em 25/01/2026"), gerador._chave_valor("data", "25/01/2026"))
        self.assertNotEqual(gerador._chave_valor("data", "25/01/2026"), gerador._chave_valor("data", "26/01/2026"))
        self.assertEqual(gerador._chave_valor("valor", "R$ 10,48"), gerador._chave_valor("valor", "10,48"))
        self.assertEqual(gerador._mesmo_valor("valor", "10,48", "R$ 17,36", "R$ 10,48"), "17,36")  # no formato do lugar
        self.assertEqual(gerador._mesmo_valor("valor", "Total R$ 10,48", "1.017,36", "10,48"), "Total R$ 1.017,36")
        lista = [_c(0, "data", "em 25/01/2026", 10), _c(1, "data", "25/01/2026", 300)]
        falso = _Falso(novo=lambda p: p.get("texto") or p["campo"]["texto"].replace("25/01/2026", "10/03/2026"))
        feitos, _ = self._editar(lista, falso, sorteio="campo")
        self.assertEqual([f["novo"] for f in feitos], ["em 10/03/2026", "10/03/2026"])

    def test_so_o_cpf_do_cliente(self):
        """A conta de verdade tem o CNPJ da operadora no cabeçalho e no quadro do beneficiário: esse não muda."""
        lista = [_c(0, "nome", "MARIA DA SILVA PRADO", 100), _c(1, "cpf/cnpj", "123.456.789-09", 130),
                 _c(2, "cpf/cnpj", "53.420.564/0003-01", 10, x=700), _c(3, "cpf/cnpj", "53.420.564/0001-40", 520, x=700),
                 _c(4, "nome", "Maria da Silva Prado", 400), _c(5, "cpf/cnpj", "123.456.789-09", 430)]
        escolha = gerador._cpf_do_cliente(lista, lista)
        self.assertEqual([f["id"] for f in escolha], ["t0", "t1", "t4", "t5"])
        self.assertEqual(gerador._cpf_do_cliente(lista[:2], lista), lista[:2])  # um só: fica
        for sem in range(20):
            feitos, info, _, _ = _aplicar({"perfil": "profissional", "categorias_base": ["nome"]}, sem, lista=lista)
            self.assertFalse({"t2", "t3"} & {f["campo"] for f in feitos})

    def test_bairro_de_varias_palavras(self):
        r = gerador.Receita({"perfil": "profissional"}, IDS)
        self.assertNotIn("trecho", gerador._servem_profissional(_c(2, "bairro", "JARDIM AMERICA", 70), r))
        self.assertIn("trecho", gerador._servem_profissional(_c(2, "bairro", "BIGORRILHO", 70), r))
        self.assertNotIn("acrescimo", gerador._servem_profissional(_c(2, "bairro", "BATEL", 70), r))  # "BATEL SUL" não


class TestReceita(unittest.TestCase):
    def test_expande(self):
        r = gerador.Receita({"perfil": "profissional", "extras": False, "categorias_base": ["nome"], "variacoes": 4,
                             "documentos": {"splits": ["treino"], "ids": ["abc"]}, "nome": "um clique"}, IDS)
        self.assertEqual((r.perfil, r.extras, r.categorias_base, r.variacoes), ("profissional", False, ["nome"], 4))
        self.assertEqual((r.splits, r.ids, r.nome), (["treino"], {"abc"}, "um clique"))
        self.assertEqual(set(r.categorias), {"nome"})
        self.assertTrue(r.exato and r.subconjuntos)
        self.assertEqual(r.acabamento["disfarce"], "nunca")
        self.assertIsNone(r.canal)
        res = r.resumo()
        self.assertEqual((res["perfil"], res["extras"]), ("profissional", False))
        r = gerador.Receita({"perfil": "profissional"}, IDS)  # padrão: nome e endereço, com extras
        self.assertEqual((r.categorias_base, r.extras), (["nome", "endereço"], True))
        self.assertTrue({"cpf/cnpj", "cep", "data", "valor", "consumo/leitura"} <= set(r.categorias))
        self.assertNotIn("codigo barras", r.categorias)

    def test_idempotente_e_json(self):
        rec = gerador.receita_profissional(IDS, ("nome", "endereço", "codigo barras"))
        self.assertEqual(json.loads(json.dumps(rec)), rec)
        a, b = gerador.Receita(rec, IDS), gerador.Receita(json.loads(json.dumps(rec)), IDS)
        self.assertEqual(a.resumo(), b.resumo())
        self.assertIn("codigo.regenerar", a.tecnicas)  # código só quando marcado
        self.assertEqual(rec["mistura"]["granularidade"]["campo"]["peso"], gerador.PERFIL_PROFISSIONAL["campo"][0])

    def test_invalida(self):
        with self.assertRaises(ErroOperacao):
            gerador.Receita({"perfil": "profissional", "categorias_base": 5}, IDS)
        r = gerador.Receita({"perfil": "profissional", "categorias_base": ["inexistente"]}, IDS)
        self.assertEqual(r.categorias_base, ["nome", "endereço"])

    def test_ia_desligada_nao_impede(self):
        g = gerador.Gerador(mock.Mock(), Path(tempfile.gettempdir()), IDS)
        with mock.patch.object(gerador.ia, "status", return_value={"online": False}):
            self.assertEqual(g.receita({"perfil": "profissional", "com_ia": True}).perfil, "profissional")
            with self.assertRaises(ErroOperacao):
                g.receita({"tecnicas": {"ia.reescrever": 1}})


class TestVersao(unittest.TestCase):
    def test_deterministico(self):
        for sem in (3, 17, 99):
            a, b = _aplicar({"perfil": "profissional"}, sem), _aplicar({"perfil": "profissional"}, sem)
            self.assertEqual(a[1], b[1])
            self.assertEqual(a[2].chamadas, b[2].chamadas)
        estilos = {json.dumps(_aplicar({"perfil": "profissional"}, sem)[1]["estilo"], sort_keys=True) for sem in range(20)}
        self.assertGreater(len(estilos), 10)  # versões diferentes, estilos diferentes

    def test_estilo(self):
        vistos = Counter()
        for sem in range(200):
            feitos, info, _, _ = _aplicar({"perfil": "profissional"}, sem)
            est = info["estilo"]
            self.assertEqual(set(est), {"classes", "extras", "tecnicas", "granularidade"} | ({"velhos"} & set(est)))
            self.assertEqual(est["classes"], info["classes"])
            self.assertTrue({"nome", "endereço"} & set(est["classes"]))  # sempre nome e/ou endereço
            self.assertTrue({f["categoria"] for f in feitos} <= set(est["classes"]))
            self.assertEqual(set(est["tecnicas"]), {f["campo"] for f in feitos})
            for cid, tid in est["tecnicas"].items():
                self.assertEqual(est["granularidade"][cid], gerador.GRUPO_DA_TECNICA[tid])
                vistos[est["granularidade"][cid]] += 1
            if any(f["categoria"] == "valor" for f in feitos):  # o código de barras ficou com o valor velho
                self.assertEqual(est["velhos"], ["codigo barras"])
            else:
                self.assertNotIn("velhos", est)
        self.assertEqual(set(vistos), set(gerador.PERFIL_PROFISSIONAL), vistos)

    def test_sempre_nome_ou_endereco(self):
        """Nome e endereço não cabem (só as extras caberiam): a versão é desfeita, nunca fica só com as extras."""
        falso = _Falso(falha={(t, c) for t in gerador.GRUPO_DA_TECNICA for c in ("nome", "endereço")})
        for sem in range(30):
            feitos, info, _, s = _aplicar({"perfil": "profissional"}, sem, falso=falso)
            self.assertEqual(feitos, [])
            self.assertEqual(s.historico_, [])


class TestApp(unittest.TestCase):
    def setUp(self):
        from app import carregar_categorias, criar_app
        from rotulador.registro import Registro
        from tests.util import conta, salvar
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        salvar(conta(41), self.dados / "treino" / "a.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()
        self.doc = self.reg.por_split["treino"][0].doc

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rota_da_receita(self):
        r = self.c.get("/api/gerador/receita_profissional").get_json()
        self.assertEqual((r["perfil"], r["extras"], r["categorias_base"]), ("profissional", True, ["nome", "endereço"]))
        r = self.c.get("/api/gerador/receita_profissional?extras=0&base=nome").get_json()
        self.assertEqual((r["extras"], list(r["categorias"])), (False, ["nome"]))

    def test_fraude_usa_o_perfil(self):
        self.c.post("/api/abrir", json={"id": self.doc})
        visto = {}

        def editar(s, ctx, campos, r, plano, rng, ao_falhar=None, humano_hab=None):
            visto["r"] = r
            return []
        campo = {"id": "c1", "caixa": [14, 60, 200, 80], "texto": "CLIENTE 090", "categoria": "nome", "tipo": "texto"}
        with mock.patch("app.editar_campos", side_effect=editar):
            self.assertEqual(self.c.post("/api/auto/fraude", json={"id": self.doc, "campos": [campo]}).status_code, 400)
        self.assertEqual((visto["r"].perfil, visto["r"].extras, list(visto["r"].categorias)), ("profissional", False, ["nome"]))

        def aplicar(s, ctx, r, plano, rng, k=None, ao_falhar=None, humano_hab=None):
            visto["r"] = r
            return []
        with mock.patch("app.aplicar_receita", side_effect=aplicar):
            self.assertEqual(self.c.post("/api/auto/fraude", json={"id": self.doc}).status_code, 400)
        self.assertEqual((visto["r"].perfil, visto["r"].extras), ("profissional", True))


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestLote(unittest.TestCase):
    def setUp(self):
        from app import carregar_categorias, criar_app
        from rotulador.registro import Registro
        from tests.test_exato import boleto
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        (self.dados / "treino").mkdir(parents=True)
        Image.fromarray(boleto()[0]).save(self.dados / "treino" / "boleto.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lote_profissional(self):
        rec = {"perfil": "profissional", "variacoes": 3, "documentos": {"splits": ["treino"]}}
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(600):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertEqual(st["feitas"], 3, st)
        for arq in (self.saida / "treino" / "anotacoes").glob("*.json"):
            a = json.loads(arq.read_text(encoding="utf-8"))
            est = a["gerador"]["estilo"]
            self.assertEqual(a["gerador"]["receita"]["perfil"], "profissional")
            self.assertTrue({"nome", "endereço"} & set(est["classes"]))
            for e in a["edicoes"]:
                self.assertIn(e["tecnica"], gerador.GRUPO_DA_TECNICA)
                self.assertIn(e["categoria"], est["classes"])
            # sem canal de envio; só o "Salvar como" JPEG do editor do operador humano, aplicado ao par
            self.assertTrue(a["canal"] is None or [p["tipo"] for p in a["canal"]["passos"]] == ["salvar_editor"],
                            a["canal"])

    def test_previa_com_canal(self):
        """A prévia com canal de envio sobrescrevia o ``info`` da versão com a lista de marcas (erro 500)."""
        doc = self.reg.por_split["treino"][0].doc
        rec = {"perfil": "profissional", "canal": {"perfil": "scanner", "prob": 1.0}}
        r = self.c.post("/api/gerador/previa", json={"receita": rec, "doc": doc})
        self.assertEqual(r.status_code, 200, r.get_json())
        p = r.get_json()
        self.assertTrue(p["canal_editada"] and p["canal"])
        self.assertTrue({"nome", "endereço"} & set(p["classes"]))
        self.assertEqual(set(p["estilo"]["tecnicas"]), {e["campo"] for e in p["edicoes"]})

    def test_recorte_da_previa_com_a_linha(self):
        """Troca parcial: o recorte da prévia mostra a linha inteira, não só o "1234" trocado."""
        import base64
        import io
        from tests.test_exato import boleto
        _, cx = boleto()
        x0, y0, x1, y1 = cx["rua1"]
        so_o_numero = [{"bbox": [x1 - 60, y0, x1, y1], "tecnica": "trecho.trocar", "categoria": "endereço",
                        "campo": "t1", "antigo": "RUA DAS FLORES, 1234", "novo": "RUA DAS FLORES, 1734"}]
        with mock.patch.object(gerador, "aplicar_receita", return_value=so_o_numero):
            p = self.c.post("/api/gerador/previa", json={"receita": {"perfil": "profissional"},
                                                         "doc": self.reg.por_split["treino"][0].doc}).get_json()
        largura = Image.open(io.BytesIO(base64.b64decode(p["recortes"][0]["antes"]))).width
        self.assertGreaterEqual(largura, x1 - x0)


if __name__ == "__main__":
    unittest.main()
