"""Valores falsos realistas (rotulador/ops/geradores.py e rotulador/coerencia.py): núcleo comum a todos os splits,
nomes pela frequência e com gênero coerente, razão social, acentos, endereço no molde do original, comprimento parecido,
regras de coerência de endereço e os acertos menores (cidade da mesma UF, zeros do identificador, filial do CNPJ, sufixo
do CEP, OCR com espaços). As distribuições são medidas em milhares de sorteios com semente fixa."""
import collections
import random
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rotulador import coerencia
from rotulador.ops import geradores as g
from tests.test_integracao_vocabulario import sem_bases

SPLITS = ("treino", "teste", "validacao")
SUFIXOS = {"FILHO", "JUNIOR", "NETO", "SOBRINHO"}
PARTICULAS = {"DA", "DE", "DO", "DAS", "DOS"}


def _nomes(split=None, n=5000, semente=11, **kw):
    with g.usando(random.Random(semente), split, **kw):
        return [g.gerar("nome")["texto"] for _ in range(n)]


def _dobrar(s):
    return g._normalizar(s)


class TestNucleoComum(unittest.TestCase):
    """Item 1: os nomes mais comuns do país em todos os splits; nome completo do teste nunca é falso no treino."""

    @classmethod
    def setUpClass(cls):
        cls.por_split = {sp: _nomes(sp) for sp in SPLITS}

    def test_nomes_comuns_em_todos_os_splits(self):
        for palavra in ("MARIA", "JOSE", "JOAO", "ANA", "SILVA", "SANTOS", "OLIVEIRA"):
            for sp, nomes in self.por_split.items():
                vezes = sum(palavra in _dobrar(n).split() for n in nomes)
                self.assertGreater(vezes, 50, (palavra, sp, vezes))  # antes: MARIA/JOSE/JOAO só na validação

    def test_nome_completo_nao_cruza_splits(self):
        conj = {sp: set(map(_dobrar, v)) for sp, v in self.por_split.items()}
        self.assertEqual(conj["treino"] & conj["teste"], set())
        self.assertEqual(conj["treino"] & conj["validacao"], set())
        self.assertEqual(conj["teste"] & conj["validacao"], set())
        for sp in SPLITS:  # o hash da combinação manda: cada nome falso é do split em que saiu
            self.assertTrue(all(g._fatia(n) == sp for n in conj[sp]), sp)

    def test_cauda_continua_repartida(self):
        cauda = {sp: {g._chave(x) for k in ("nomes", "meios", "sobrenomes") for x in g._PARTES[k][sp]} for sp in SPLITS}
        for sp, nomes in self.por_split.items():
            palavras = {w for n in nomes for w in _dobrar(n).split()}
            for outro in SPLITS:
                if outro != sp:
                    self.assertEqual(palavras & cauda[outro], set(), (sp, outro))

    def test_capitais_e_bairros_genericos_em_todos_os_splits(self):
        for sp in SPLITS:
            with g.usando(split=sp):
                cidades = {_dobrar(c) for c, _ in g._lista("cidades")}
                bairros = {_dobrar(b) for b in g._lista("bairros")}
            self.assertLessEqual({"SAO PAULO", "RIO DE JANEIRO", "BELO HORIZONTE", "SALVADOR", "FORTALEZA", "RECIFE",
                                  "BRASILIA", "CURITIBA"}, cidades, sp)
            self.assertLessEqual({"CENTRO", "JARDIM AMERICA", "VILA NOVA", "BOA VISTA"}, bairros, sp)
        with g.usando(random.Random(4), "teste"):
            cidades = collections.Counter(re.split(r" - |/", g.gerar("cidade/uf")["texto"])[0] for _ in range(3000))
        self.assertEqual(cidades.most_common(1)[0][0], "SAO PAULO")  # a cidade mais populosa, não ARARAQUARA


class TestNomes(unittest.TestCase):
    """Item 2: peso pela frequência, gênero coerente, compostos, partículas, sufixos e razão social."""

    @classmethod
    def setUpClass(cls):
        cls.nomes = [_dobrar(n).split() for n in _nomes(None, 6000, 21)]
        sobrenomes = {_dobrar(x) for x in g._SOBRENOMES}  # CONCEIÇÃO, PAULA: prenome e sobrenome
        b = g._nomes_base()
        if b is not None:  # com a base: ROSA, CRUZ, PAULO são sobrenome de verdade; BEATRIZ, ANTONIO não (prenome forte)
            sobrenomes |= set(b["sob"]) - b["so_do_genero"]["F"] - b["so_do_genero"]["M"]
        cls.f = {_dobrar(x) for x in g._PRENOMES_F + g._MEIOS_F} - sobrenomes
        cls.m = {_dobrar(x) for x in g._PRENOMES_M + g._MEIOS_M} - sobrenomes

    def test_frequencia(self):
        primeiro = collections.Counter(n[0] for n in self.nomes)
        self.assertGreater(primeiro["MARIA"] / len(self.nomes), 0.03)  # antes 0,6% (uniforme: MARIA = IARA)
        self.assertGreater(primeiro["MARIA"], 20 * max(1, primeiro["IARA"]))
        sobrenomes = collections.Counter(w for n in self.nomes for w in n[1:])
        self.assertGreater(sobrenomes["SILVA"], 20 * max(1, sobrenomes["TAKAHASHI"]))
        self.assertGreater(sobrenomes["SILVA"] / len(self.nomes), 0.08)

    def test_genero_coerente(self):
        # MARIA JOSÉ, JOSÉ MARIA e, com a base de nomes, os pares reais de prenomes do TSE (JOÃO MARIA é nome de homem)
        devocoes = {"MARIA JOSE", "JOSE MARIA"} | ({f"{p1} {p2}" for gen in "FM"
                                                   for p1, (ps, _, _) in g._nomes_base()["pares"][gen].items()
                                                   for p2 in ps} if g._nomes_base() else set())
        mistos = [n for n in self.nomes if len(n) > 2 and " ".join(n[:2]) not in devocoes
                  and ((n[0] in self.f - self.m and n[1] in self.m - self.f)
                       or (n[0] in self.m - self.f and n[1] in self.f - self.m))]
        self.assertEqual(mistos, [])  # antes ~20% ("ALAN BEATRIZ VILELA")
        do_treino = [_dobrar(n).split() for n in _nomes("treino", 3000, 5)]
        meios_treino = [n[1] for n in do_treino if len(n) > 2 and n[1] in self.f | self.m]
        femininos = sum(w in self.f - self.m for w in meios_treino) / max(1, len(meios_treino))
        self.assertTrue(0.3 < femininos < 0.7, femininos)  # antes: os do meio do treino eram quase todos femininos

    def test_compostos_particulas_sufixos(self):
        compostos = sum(len(n) > 2 and n[1] in self.f | self.m for n in self.nomes)  # ANA PAULA, JOSÉ CARLOS
        self.assertTrue(0.15 < compostos / len(self.nomes) < 0.45, compostos / len(self.nomes))
        devocoes = [f"{k} {_dobrar(d)} " for k, ds in g._DEVOCOES.items() for d in ds]  # MARIA DE FÁTIMA, JOÃO BATISTA
        self.assertGreater(sum(any((" ".join(n) + " ").startswith(d) for d in devocoes) for n in self.nomes), 40)
        com_particula = sum(bool(set(n) & PARTICULAS) for n in self.nomes) / len(self.nomes)
        self.assertGreater(com_particula, 0.35)  # antes 3%
        homens = [n for n in self.nomes if n[0] in self.m - self.f]
        com_sufixo = sum(n[-1] in SUFIXOS for n in homens) / len(homens)
        # antes: nenhum FILHO/JUNIOR/NETO; a base de nomes dá a taxa real (5,35% dos homens no TSE), o embutido ~3%
        self.assertTrue(0.015 < com_sufixo < 0.08, com_sufixo)
        mulheres = [n for n in self.nomes if n[0] in self.f - self.m]
        self.assertFalse([n for n in mulheres if n[-1] in SUFIXOS])
        self.assertFalse([n for n in self.nomes if re.search(r"\bDOS (SILVA|SOUZA|COSTA)\b|\bDA (SANTOS|SOUZA)\b",
                                                             " ".join(n))])

    def test_razao_social(self):
        ramos = {w for r in g._RAMOS for w in _dobrar(r).split()}
        with g.usando(random.Random(8), "treino"):
            for _ in range(200):
                n = g.parecido("nome", "M R C FILHO FOTOS")
                ps = _dobrar(n).split()
                self.assertTrue(all(len(p) == 1 for p in ps[:3]) and len(ps[3]) > 1, n)  # as iniciais, como no original
                self.assertTrue(ps[-1] in ramos or len(ps) == 4, n)  # termina no ramo ("FOTOS" → "CALÇADOS")
                for antigo, fim in (("SANTOS COMERCIO DE ALIMENTOS LTDA", "LTDA"), ("J R SILVA ME", "ME"),
                                    ("Mercadinho Sao Jose Eireli", "Eireli"), ("PADARIA PAO DE OURO S/A", "S/A")):
                    novo = g.parecido("nome", antigo)
                    self.assertTrue(novo.endswith(" " + fim), (antigo, novo))
                    self.assertNotEqual(_dobrar(novo), _dobrar(antigo))
                self.assertFalse(g._empresa(g.parecido("nome", "MARIA DA SILVA")))  # pessoa continua pessoa

    def test_nome_do_meio_abreviado_como_no_original(self):
        with g.usando(random.Random(9)):
            novos = [g.parecido("nome", "MARIA A. SOUZA") for _ in range(300)]
        com_inicial = sum(bool(re.fullmatch(r"\S+ [A-Z]\. \S+", n)) for n in novos) / len(novos)
        self.assertGreater(com_inicial, 0.9, novos[:5])  # antes: só abreviava com 2+ iniciais na frente


class TestAcentos(unittest.TestCase):
    """Item 3: contrato ``usando(acentos=...)``, grafia com acento, partição pela chave sem acento, caixa mista."""

    def test_contrato(self):
        with g.usando(random.Random(1), "treino", acentos=True):
            com = [g.gerar("nome")["texto"] for _ in range(1000)] + [g.gerar("endereço")["texto"] for _ in range(1000)]
            with g.usando(random.Random(2)):  # None mantém o de fora
                self.assertTrue(g._acentos_atual.get())
        self.assertGreater(sum(not x.isascii() for x in com) / len(com), 0.25)  # antes 0%
        self.assertTrue(any("JOSÉ" in x.split() or "JOÃO" in x.split() for x in com))
        for kw in ({}, {"acentos": False}, {"acentos": None}):
            with g.usando(random.Random(1), "treino", **kw):
                sem = [g.gerar(c)["texto"] for c in ("nome", "endereço") for _ in range(500)]
            self.assertTrue(all(x.isascii() for x in sem), kw)
        with g.usando(acentos=True):
            self.assertTrue(any(not g.palavra("cidade").isascii() for _ in range(300)))
            self.assertTrue(any(not g.cidade_mais_parecida("CAMPINAS - SP", random.Random(i))[0].isascii()
                                for i in range(30)))  # SÃO PAULO, RIBEIRÃO PRETO, JUNDIAÍ...
        self.assertTrue(all(g.cidade_mais_parecida("CAMPINAS - SP", random.Random(i))[0].isascii() for i in range(30)))

    def test_acento_nao_muda_o_sorteio(self):
        for categoria, antigo in (("nome", "MARIA DA SILVA"), ("endereço", "RUA DAS FLORES, 123"), ("bairro", "CENTRO"),
                                  ("cidade/uf", "FORTALEZA - CE")):
            with g.usando(random.Random(5), "teste", acentos=True):
                com = [g.parecido(categoria, antigo) for _ in range(30)]
            with g.usando(random.Random(5), "teste"):
                sem = [g.parecido(categoria, antigo) for _ in range(30)]
            self.assertEqual([g._sem_acento(x) for x in com], sem, categoria)

    def test_reparticao_pela_chave_sem_acento(self):
        self.assertEqual(g._fatia("JOSÉ"), g._fatia("JOSE"))
        self.assertEqual(g._fatia("CONCEIÇÃO"), g._fatia("CONCEICAO"))
        self.assertEqual(g._fatia(("SÃO PAULO", "SP")), g._fatia("SAO PAULO"))

    def test_caixa_mista(self):
        self.assertEqual(g.como_o_antigo("Maria da SILVA", "JOSÉ CARLOS DOS SANTOS ROCHA"),
                         "José Carlos dos SANTOS ROCHA")
        self.assertEqual(g.como_o_antigo("MARIA SILVA", "José Lima"), "JOSÉ LIMA")
        self.assertEqual(g.como_o_antigo("Maria Souza", "JOÃO DE ARAÚJO"), "João de Araújo")
        with g.usando(random.Random(6)):
            for _ in range(50):
                n = g.parecido("nome", "Maria da SILVA")
                ps = n.split()
                # antes: TUDO MAIÚSCULO
                self.assertTrue(ps[-1].isupper() and ps[0][0].isupper() and ps[0][1:].islower(), n)

    def test_vocabulario_externo_guarda_o_acento(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "prenomes.txt").write_text("José\n  maria  da  glória \n", "utf-8")
            (Path(d) / "cidades.txt").write_text("São Paulo;sp\n", "utf-8")
            with mock.patch.object(g, "_VOCABULARIO", Path(d)):
                self.assertEqual(g._do_arquivo("prenomes"), ("JOSÉ", "MARIA DA GLÓRIA"))
                self.assertEqual(g._do_arquivo("cidades"), (("SÃO PAULO", "SP"),))


class TestEndereco(unittest.TestCase):
    """Item 4: endereço no molde do original, tipos e números na proporção do país, complementos variados, bairro da
    cidade."""

    def test_distribuicoes(self):
        with g.usando(random.Random(7)):
            ends = [g.gerar("endereço")["texto"] for _ in range(5000)]
        tipos = collections.Counter(e.split()[0] for e in ends)
        self.assertGreater(tipos["RUA"] / 5000, 0.6)  # antes 33%
        self.assertTrue(0.08 < (tipos["AVENIDA"] + tipos["AV"]) / 5000 < 0.2)
        for raro in ("ALAMEDA", "ESTRADA", "TRAVESSA", "PRACA"):
            self.assertLess(tipos[raro] / 5000, 0.07, raro)  # antes ~11% cada
        nums = [m.group(1) for e in ends for m in [re.search(r", (\d+)", e)] if m]
        dois_tres = sum(len(n) in (2, 3) for n in nums) / len(nums)
        self.assertGreater(dois_tres, 0.5)  # antes 32% (67% com 4 dígitos)
        self.assertLess(sum(len(n) == 4 for n in nums) / len(nums), 0.35)
        self.assertGreater(sum(n[0] == "1" for n in nums) / len(nums), 0.2)  # Benford: o 1 na frente em ~30%
        aptos = [m.group(1) for e in ends for m in [re.search(r"\bAPT?O? (\d+)$", e)] if m]
        self.assertGreater(len(aptos), 300)
        self.assertLess(sum(len(a) == 4 for a in aptos) / len(aptos), 0.2)  # antes 76% acima do 10º andar
        # unidade 01–08 na grande maioria (no CNEFE, 93%; prédio grande tem 09, 10, 11...)
        tres = [a for a in aptos if len(a) >= 3]
        self.assertGreater(sum(1 <= int(a[-2:]) <= 8 for a in tres) / len(tres), 0.85)
        tipos_comp = {k for e in ends for k in ("APTO", "AP", "CASA", "BL", "BLOCO", "FUNDOS", "LOJA", "SALA", "QD",
                                                "S/N") if re.search(rf"(?<![A-Z]){re.escape(k)}(?![A-Z])", e)}
        # antes só APTO, CASA, BLOCO; com as estatísticas do CNEFE, LOJA e SALA quase não aparecem em domicílio
        self.assertGreaterEqual(len(tipos_comp), 8, tipos_comp)

    def test_molde_do_original(self):
        moldes = {
            "RUA 47 CJ PREFEITO JOSE WALTER 312": r"RUA \d{2} CJ PREFEITO JOSE WALTER \d{3}",
            "R. das Flores, 123 - Apto 302": r"[A-Z][a-z]{0,2}\. [a-z ]*[A-Z][^,]*, \d{3} - Apto [1-9]\d\d",
            "AV BRASIL 1500 BL A AP 12": r"[A-Z]{1,3} [A-Z ]+ \d{4} BL [B-H] AP [1-9][1-8]",
            "QD 12 LT 5": r"QD \d{2} LT \d",
            "RUA A, 10": r"RUA [B-Z], \d{2}",  # com a base de lugares, uma RUA de letra de verdade da cidade
            "TRAVESSA SAO JOSE, Nº 45, CASA 2": r"[A-Z]+ [A-Z ]+, Nº \d{2}, CASA \d",
            "Rua Sete de Setembro, 1234 - CEP 60750-720": r"[A-Z][a-z]+ [^,]+, \d{4} - CEP 60750-720",
            "RCorreiaVasques,69,3Andar": r"[A-Z][a-z]{0,2} [^,]+,\d{2},\d Andar",
            "AVENIDA BRASIL, S/N": r"[A-Z]+ [A-Z ]+, S/N",
        }
        with g.usando(random.Random(12), "teste"):
            for antigo, padrao in moldes.items():
                for _ in range(100):
                    novo = g.parecido("endereço", antigo)
                    self.assertRegex(novo, "^" + padrao + "$", antigo)
                    self.assertNotEqual(novo, antigo)
        with g.usando(random.Random(13)):  # o tipo segue o estilo: abreviado continua abreviado, por extenso continua
            curtos = {g.parecido("endereço", "R. DAS FLORES, 10").split()[0] for _ in range(300)}
            longos = {g.parecido("endereço", "RUA DAS FLORES, 10").split()[0] for _ in range(300)}
        self.assertTrue(all(t.endswith(".") and len(t) <= 4 for t in curtos), curtos)
        self.assertTrue({"RUA", "AVENIDA"} <= longos and all(len(t) >= 3 and "." not in t for t in longos), longos)

    def test_bairro_da_cidade(self):
        # com a base de lugares: um bairro de verdade da cidade (pelos domicílios), que a regra bairro × cidade confirma
        for semente, cidade in ((14, ("FEIRA DE SANTANA", "BA")), (15, ("FORTALEZA", "CE"))):
            with g.usando(random.Random(semente), "treino", cidade=cidade):
                bairros = [g.gerar("bairro")["texto"] for _ in range(2000)]
            self.assertTrue(all(g.bairro_na_cidade(b, cidade) for b in bairros), cidade)  # antes: PARANOA em Feira
            self.assertGreater(len(set(bairros)), 60, cidade)
        self.assertIn("ALDEOTA", bairros)  # os grandes de Fortaleza aparecem
        with g.usando(random.Random(16), "treino"):  # sem cidade: a do antigo (ALDEOTA é de Fortaleza) ou genérico
            self.assertTrue(all(g.bairro_na_cidade(g.parecido("bairro", "ALDEOTA"), ("FORTALEZA", "CE"))
                                for _ in range(300)))
            self.assertTrue(all(g.cidades_do_bairro(g.parecido("bairro", "CENTRO")) == set() for _ in range(300)))

        def fora(bairros, cidade):  # bairro próprio de OUTRA cidade (BENFICA é de Fortaleza e do Rio)
            return [b for b in bairros if g.cidades_do_bairro(b) and cidade not in g.cidades_do_bairro(b)]
        with sem_bases():  # o vocabulário embutido: os próprios da cidade ou genéricos
            with g.usando(random.Random(14), "treino", cidade=("FEIRA DE SANTANA", "BA")):
                bairros = [g.gerar("bairro")["texto"] for _ in range(2000)]
            self.assertEqual(fora(bairros, ("FEIRA DE SANTANA", "BA")), [])
            self.assertTrue(all(g.cidades_do_bairro(b) is not None for b in bairros))
            with g.usando(random.Random(15), "treino", cidade=("FORTALEZA", "CE")):
                bairros = [g.gerar("bairro")["texto"] for _ in range(2000)]
            self.assertEqual(fora(bairros, ("FORTALEZA", "CE")), [])
            self.assertGreater(sum(bool(g.cidades_do_bairro(b)) for b in bairros), 300)
            with g.usando(random.Random(16), "treino"):
                self.assertEqual(fora([g.parecido("bairro", "ALDEOTA") for _ in range(500)], ("FORTALEZA", "CE")), [])
                self.assertEqual(fora([g.parecido("bairro", "CENTRO") for _ in range(500)], None), [])
        self.assertFalse(g._GENERICOS & set(g._DONOS_BAIRRO))  # genérico ou próprio de cidade, nunca os dois
        cidades = {(_dobrar(c), uf) for c, uf in g._CIDADES}
        self.assertTrue(all(donos <= cidades for donos in g._DONOS_BAIRRO.values()))
        self.assertEqual(g.cidades_do_bairro("PARANOÁ"), {("BRASILIA", "DF")})
        self.assertEqual(g.cidades_do_bairro("Centro"), set())
        self.assertEqual(g.cidades_do_bairro("JARDIM QUALQUER"), set())
        self.assertIsNone(g.cidades_do_bairro("XPTO"))

    def test_logradouro_de(self):
        self.assertEqual(g.logradouro_de("R. das Flores, 123 ap 4"), "RUA DAS FLORES")
        self.assertEqual(g.logradouro_de("RUA DAS FLORES 999 - CASA 2"), "RUA DAS FLORES")
        self.assertEqual(g.logradouro_de("RUA 47 CJ PREFEITO JOSE WALTER 312"), "RUA 47")
        self.assertEqual(g.logradouro_de("Av. São João, Nº 10"), "AVENIDA SAO JOAO")


class TestComprimento(unittest.TestCase):
    """Item 5: o valor novo tem o comprimento do antigo (±15%), também nos formatos curtos e nos bairros compridos."""

    CASOS = {"nome": ("MARIA DA SILVA", "JOSE CARLOS DE OLIVEIRA SANTOS", "M R C FILHO FOTOS", "Maria Souza",
                      "FRANCISCO DAS CHAGAS RODRIGUES DE SOUZA NETO", "ANA PAULA FERREIRA COSTA", "ANA LIMA"),
             "endereço": ("QD 12 LT 5", "RUA A, 10", "RUA 47 CJ PREFEITO JOSE WALTER 312", "AV BRASIL, 1500",
                          "RUA DAS FLORES, 123 APTO 302", "AVENIDA PRESIDENTE JUSCELINO KUBITSCHEK, 1830 SALA 1204"),
             "bairro": ("CENTRO", "ALDEOTA", "VILA NOVA", "PREFEITO JOSE WALTER", "JARDIM NOVA ESPERANCA DO NORTE")}

    def test_dentro_de_15_por_cento(self):
        dentro = total = 0
        with g.usando(random.Random(17), "treino"):
            for categoria, antigos in self.CASOS.items():
                for antigo in antigos:
                    novos = [g.parecido(categoria, antigo) for _ in range(150)]
                    ok = sum(abs(len(n) - len(antigo)) <= max(1, 0.15 * len(antigo)) for n in novos)
                    self.assertGreater(ok / len(novos), 0.6, (categoria, antigo, novos[:3]))  # antes 0% em QD 12 LT 5
                    dentro, total = dentro + ok, total + len(novos)
        self.assertGreater(dentro / total, 0.9)  # antes ~50%


class TestCoerenciaEndereco(unittest.TestCase):
    """Item 6: logradouro trocado com o CEP antigo e bairro de outra cidade."""

    def _campos(self, rua="RUA DAS FLORES, 123", cep="80318-436", bairro="BATEL", cidade="CURITIBA/PR"):
        return [{"id": "e", "categoria": "endereço", "texto": rua}, {"id": "c", "categoria": "cep", "texto": cep},
                {"id": "b", "categoria": "bairro", "texto": bairro},
                {"id": "u", "categoria": "cidade/uf", "texto": cidade},
                {"id": "n", "categoria": "nome", "texto": "ANA LIMA"}]

    def _edita(self, **troca):
        antes = self._campos()
        ids = {"rua": "e", "cep": "c", "bairro": "b", "cidade": "u"}
        edicoes = [{"campo": ids[k], "novo": v} for k, v in troca.items()]
        return coerencia.comparar(antes, coerencia.campos_finais(antes, edicoes))

    def test_logradouro_trocado_com_cep_antigo(self):
        c = self._edita(rua="RUA SETE DE SETEMBRO, 123")
        self.assertIn("endereco_cep", c["novas_violacoes"])
        self.assertFalse(c["editada"]["regras"]["endereco_cep"]["ok"])
        detalhe = c["editada"]["regras"]["endereco_cep"]["detalhe"]
        self.assertTrue(detalhe.startswith("logradouro trocado com o CEP antigo"), detalhe)
        self.assertTrue(c["original"]["regras"]["endereco_cep"]["ok"])
        self.assertIn("endereco_cep", c["regras_aplicaveis"])
        self.assertEqual(self._edita(rua="R. das Flores, 999 - CASA 2")["novas_violacoes"], [])  # só o número mudou
        self.assertIsNone(self._edita(rua="RUA SETE DE SETEMBRO, 123", cep="80420-130")["editada"]["regras"]
                          ["endereco_cep"]["ok"])  # CEP novo: sem a base dos Correios não dá para conferir
        antes = self._campos(cep="83900-000")  # CEP geral do município serve para qualquer rua
        depois = coerencia.campos_finais(antes, [{"campo": "e", "novo": "RUA SETE DE SETEMBRO, 123"}])
        self.assertTrue(coerencia.comparar(antes, depois)["editada"]["regras"]["endereco_cep"]["ok"])
        c = self._edita(bairro="CENTRO")
        self.assertIn("endereco_cep", c["novas_violacoes"])
        self.assertIn("bairro trocado", c["editada"]["regras"]["endereco_cep"]["detalhe"])

    def test_bairro_de_outra_cidade(self):
        r = coerencia.avaliar(self._campos(bairro="PARANOA", cidade="FEIRA DE SANTANA - BA"))
        self.assertIn("bairro_cidade", r["violadas"])
        self.assertIn("BRASILIA/DF", r["regras"]["bairro_cidade"]["detalhe"])
        r = coerencia.avaliar(self._campos(bairro="Paranoá", cidade="Brasília/DF"))
        self.assertTrue(r["regras"]["bairro_cidade"]["ok"])
        self.assertTrue(coerencia.avaliar(self._campos(bairro="CENTRO"))["regras"]["bairro_cidade"]["ok"])
        self.assertIsNone(coerencia.avaliar(self._campos(bairro="XPTO"))["regras"]["bairro_cidade"]["ok"])
        c = self._edita(bairro="ALDEOTA", cep="80318-436")
        self.assertIn("bairro_cidade", c["novas_violacoes"])


class TestMenores(unittest.TestCase):
    """Item 7: cidade da mesma UF e variada, zeros do identificador, filial do CNPJ, sufixo do CEP, OCR com espaços."""

    def test_cidade_da_mesma_uf_e_variada(self):
        mesma = total = 0
        for c, uf in g._CIDADES:
            if sum(u == uf for _, u in g._CIDADES) < 2:
                continue
            for i in range(20):
                nova = g.cidade_mais_parecida(f"{c} - {uf}", random.Random(i))
                mesma, total = mesma + (nova[1] == uf), total + 1
                self.assertNotEqual(_dobrar(nova[0]), _dobrar(c))
        self.assertGreater(mesma / total, 0.6)  # antes 6%
        vezes = collections.Counter(g.cidade_mais_parecida("FORTALEZA - CE", random.Random(i)) for i in range(2000))
        self.assertLess(vezes.most_common(1)[0][1] / 2000, 0.6, vezes.most_common(3))  # antes ARAPIRACA em 68%
        self.assertGreater(sum(v for (c, uf), v in vezes.items() if uf == "CE") / 2000, 0.7)

    def test_identificador_mantem_os_zeros(self):
        with g.usando(random.Random(18)):
            for _ in range(300):
                n = g.parecido("identificador documento", "0000004709726")
                self.assertRegex(n, r"^000000[1-9]\d{6}$")  # antes 3416721178403
                self.assertNotEqual(n, "0000004709726")
                self.assertRegex(g.parecido("identificador documento", "AD:00010185"), r"^AD:000[1-9]\d{4}$")

    def test_filial_do_cnpj(self):
        with g.usando(random.Random(19)):
            gerados = [g.cnpj() for _ in range(2000)]
            matriz = [g.parecido("cpf/cnpj", "11.444.777/0001-61") for _ in range(300)]
            filial = [g.parecido("cpf/cnpj", "CNPJ: 11.444.777/0003-23") for _ in range(300)]
        self.assertTrue(all(g.valida_cnpj(c) for c in gerados + matriz + filial))
        self.assertGreater(sum("/0001-" in c for c in gerados) / 2000, 0.9)
        self.assertTrue(all("/0001-" in c for c in matriz))
        outras = sum("/0001-" not in c for c in filial) / 300
        self.assertTrue(0.2 < outras < 0.8, outras)  # antes sempre /0001
        self.assertTrue(all(c.startswith("CNPJ: ") for c in filial))

    def test_sufixo_do_cep_de_logradouro(self):
        with g.usando(random.Random(20)):
            ceps = [g.cep_de("FORTALEZA", "CE") for _ in range(3000)] + [g.cep_de() for _ in range(2000)]
            ceps += [g.parecido("cep", "30123-456") for _ in range(1000)]
        self.assertFalse([c for c in ceps if int(c[-3:]) >= 900])  # antes 10%
        self.assertTrue(all(60000000 <= int(c.replace("-", "")) <= 61599999 for c in ceps[:3000]))

    def test_cidade_do_texto_com_espacos_do_ocr(self):
        self.assertEqual(g.cidade_do_texto("CURI T I BA / PR"), ("CURITIBA", "PR"))  # antes ("CURI T I", "BA")
        self.assertEqual(g.cidade_do_texto("CTCE SALVADOR BA PL8"), ("SALVADOR", "BA"))
        self.assertEqual(g.cidade_do_texto("60750-720-FORTALEZA-CE"), ("FORTALEZA", "CE"))
        self.assertEqual(g.cidade_do_texto("Sao Jo se dos Campos/SP"), ("SAO JOSE DOS CAMPOS", "SP"))


class TestDeterminismo(unittest.TestCase):
    def test_mesma_semente_mesmo_valor(self):
        def rodada():
            with g.usando(random.Random(99), "validacao", acentos=True, cidade=("RECIFE", "PE")):
                return ([g.gerar(c)["texto"] for c in ("nome", "endereço", "bairro", "cidade/uf", "cep")]
                        + [g.parecido(c, a) for c, a in (
                            ("nome", "M R C FILHO FOTOS"), ("nome", "Maria da SILVA"), ("bairro", "BOA VIAGEM"),
                            ("endereço", "R. das Flores, 12 - Ap 302"), ("cidade/uf", "RECIFE/PE"),
                            ("identificador documento", "0012"))]
                        + [g.palavra("prenome", "MARIA"), g.palavra("rua")])
        self.assertEqual(rodada(), rodada())


if __name__ == "__main__":
    unittest.main()
