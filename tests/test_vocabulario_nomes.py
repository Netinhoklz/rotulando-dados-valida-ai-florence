"""Base de nomes de pessoas (config/vocabulario/, feita por ferramentas/construir_vocabulario_nomes.py): formato de
cada arquivo, valores conhecidos (JOSE → JOSÉ, SANTOS quase sempre DOS SANTOS), privacidade (sobrenome visto 5+
vezes, pares só de prenomes) e o teto de tamanho. Não usa a rede."""
import csv
import gzip
import json
import unicodedata
import unittest
from pathlib import Path

PASTA = Path(__file__).resolve().parents[1] / "config" / "vocabulario"
PARTICULAS = {"DA", "DE", "DO", "DOS", "DAS", "E"}


def _ascii(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def _tsv(nome: str) -> list[dict]:
    with gzip.open(PASTA / nome, "rt", encoding="utf-8", newline="") as arquivo:
        return list(csv.DictReader(arquivo, delimiter="\t", quoting=csv.QUOTE_NONE))


class TestPrenomes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.linhas = _tsv("prenomes.tsv.gz")
        cls.por_nome = {l["nome"]: l for l in cls.linhas}

    def test_formato(self):
        self.assertEqual(list(self.linhas[0]), ["nome", "acentuado", "frac_acento", "n_tse", "origem_acento",
                                                "freq_f", "freq_m"])
        self.assertGreaterEqual(len(self.linhas), 100_000)
        self.assertEqual(len(self.por_nome), len(self.linhas), "prenome repetido")
        for l in self.linhas:
            self.assertTrue(l["nome"].isascii() and l["nome"].isalpha() and l["nome"].isupper(), l)
            self.assertEqual(_ascii(l["acentuado"]), l["nome"], l)
            self.assertTrue(0.0 <= float(l["frac_acento"]) <= 1.0, l)
            self.assertIn(l["origem_acento"], {"tse", "regra", "-"}, l)
            self.assertGreaterEqual(int(l["freq_f"]) + int(l["freq_m"]), 10, l)
            self.assertGreaterEqual(int(l["n_tse"]), 0, l)

    def test_ordem_por_frequencia(self):
        totais = [int(l["freq_f"]) + int(l["freq_m"]) for l in self.linhas]
        self.assertEqual(totais, sorted(totais, reverse=True))
        self.assertEqual(self.linhas[0]["nome"], "MARIA")

    def test_acentos_conhecidos(self):
        for nome, forma in (("JOSE", "JOSÉ"), ("JOAO", "JOÃO"), ("ANTONIO", "ANTÔNIO"), ("MARCIA", "MÁRCIA"),
                            ("SEBASTIAO", "SEBASTIÃO"), ("MARIA", "MARIA"), ("ANA", "ANA"), ("CARLOS", "CARLOS")):
            self.assertEqual(self.por_nome[nome]["acentuado"], forma)
        self.assertGreater(float(self.por_nome["JOAO"]["frac_acento"]), 0.5)
        self.assertEqual(float(self.por_nome["MARIA"]["frac_acento"]), 0.0)

    def test_genero(self):
        self.assertGreater(int(self.por_nome["MARIA"]["freq_f"]), 10_000_000)
        self.assertGreater(int(self.por_nome["JOSE"]["freq_m"]), 5_000_000)
        mulheres = sum(int(l["freq_f"]) > int(l["freq_m"]) for l in self.linhas)
        self.assertGreater(mulheres, 40_000)
        self.assertGreater(len(self.linhas) - mulheres, 40_000)

    def test_romanizacao_asiatica_sem_til(self):
        for nome in ("HISAO", "ZHAO", "XIAO", "MASAO"):
            if nome in self.por_nome:
                self.assertNotIn("Ã", self.por_nome[nome]["acentuado"], nome)


class TestSobrenomes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.linhas = _tsv("sobrenomes.tsv.gz")
        cls.por_ascii = {l["ascii"]: l for l in cls.linhas}

    def test_formato(self):
        self.assertEqual(list(self.linhas[0]), ["sobrenome", "ascii", "n", "frac_acento", "p_nenhuma", "p_da", "p_de",
                                                "p_do", "p_dos", "p_das", "p_e"])
        self.assertGreaterEqual(len(self.linhas), 20_000)
        self.assertEqual(len(self.por_ascii), len(self.linhas), "sobrenome repetido")
        for l in self.linhas:
            self.assertEqual(_ascii(l["sobrenome"]), l["ascii"], l)
            self.assertGreaterEqual(int(l["n"]), 5, f"privacidade: {l}")
            self.assertNotIn(l["ascii"], PARTICULAS, l)
            self.assertGreaterEqual(len(l["ascii"]), 2, l)
            self.assertTrue(0.0 <= float(l["frac_acento"]) <= 1.0, l)
            soma = sum(float(l[k]) for k in l if k.startswith("p_"))
            self.assertAlmostEqual(soma, 1.0, delta=0.001, msg=str(l))

    def test_ordem(self):
        ns = [int(l["n"]) for l in self.linhas]
        self.assertEqual(ns, sorted(ns, reverse=True))
        self.assertEqual(self.linhas[0]["ascii"], "SILVA")

    def test_particulas_conhecidas(self):
        s = self.por_ascii
        self.assertGreater(float(s["SANTOS"]["p_dos"]), 0.6)
        self.assertTrue(0.5 < float(s["SILVA"]["p_da"]) < 0.9)
        self.assertGreater(float(s["JESUS"]["p_de"]), 0.9)
        self.assertGreater(float(s["NASCIMENTO"]["p_do"]), 0.5)
        self.assertGreater(float(s["PEREIRA"]["p_nenhuma"]), 0.95)
        self.assertGreater(float(s["SILVA"]["p_e"]), 0.0)  # COSTA E SILVA

    def test_acentos_e_compostos(self):
        s = self.por_ascii
        self.assertEqual(s["GONCALVES"]["sobrenome"], "GONÇALVES")
        self.assertEqual(s["ARAUJO"]["sobrenome"], "ARAÚJO")
        self.assertEqual(s["CONCEICAO"]["sobrenome"], "CONCEIÇÃO")
        self.assertEqual(s["SOUZA"]["sobrenome"], "SOUZA")
        self.assertIn("D'AVILA", s)
        self.assertIn("ESPIRITO SANTO", s)
        # prefixo cola no seguinte: VILAS sozinho (sobrenome galego) é resíduo perto de VILAS BOAS
        vilas = int(s["VILAS"]["n"]) if "VILAS" in s else 0
        self.assertGreater(int(s["VILAS BOAS"]["n"]), 10 * vilas)


class TestAcentos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.linhas = _tsv("acentos.tsv.gz")
        cls.mapa = {l["palavra"]: l for l in cls.linhas}

    def test_formato(self):
        self.assertEqual(list(self.linhas[0]), ["palavra", "acentuado", "frac", "n", "origem"])
        self.assertEqual(len(self.mapa), len(self.linhas), "palavra repetida")
        for l in self.linhas:
            self.assertTrue(l["palavra"].isascii() and l["palavra"].isalpha() and l["palavra"].isupper(), l)
            self.assertEqual(_ascii(l["acentuado"]), l["palavra"], l)
            self.assertNotEqual(l["acentuado"], l["palavra"], l)
            self.assertTrue(0.0 <= float(l["frac"]) <= 1.0, l)
            self.assertIn(l["origem"], {"tse", "manual"}, l)
            if l["origem"] == "tse":
                self.assertGreaterEqual(int(l["n"]), 3, l)

    def test_conhecidos(self):
        for palavra, forma in (("JOSE", "JOSÉ"), ("GONCALVES", "GONÇALVES"), ("JUNIOR", "JÚNIOR"), ("SAO", "SÃO"),
                               ("PARANA", "PARANÁ"), ("CONCEICAO", "CONCEIÇÃO"), ("PRACA", "PRAÇA"),
                               ("RIBEIRAO", "RIBEIRÃO"), ("GETULIO", "GETÚLIO")):
            self.assertEqual(self.mapa[palavra]["acentuado"], forma)

    def test_seguro_para_endereco(self):
        # letra solta (RUA O), sigla de UF, palavra comum e as que dependem do contexto ficam de fora
        for palavra in ("O", "U", "BA", "CE", "PE", "SE", "LUA", "CONDE", "CABO", "LAGOA", "MARCO", "MARQUES",
                        "ROMA", "IRMA", "MARIA", "SILVA", "AVENIDA", "TRAVESSA", "JARDIM", "VILA", "BAHIA"):
            self.assertNotIn(palavra, self.mapa, palavra)


class TestEstrutura(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = json.loads((PASTA / "nomes_estrutura.json").read_text("utf-8"))
        cls.prenomes = {l["nome"] for l in _tsv("prenomes.tsv.gz")}

    def test_chaves_documentadas(self):
        for chave in self.e["_chaves"]:
            self.assertIn(chave, self.e)
        for chave in self.e:
            if chave != "_chaves":
                self.assertIn(chave, self.e["_chaves"], chave)

    def test_totais(self):
        pessoas = self.e["pessoas"]
        self.assertGreater(pessoas["F"], 100_000)
        self.assertGreater(pessoas["M"], 100_000)
        for g in ("F", "M"):
            self.assertEqual(sum(self.e["tipo_prenome"][g].values()), pessoas[g])
            self.assertEqual(sum(self.e["palavras_por_nome"][g].values()), pessoas[g])
            self.assertEqual(sum(self.e["sobrenomes_por_nome"][g].values()), pessoas[g])
            self.assertEqual(sum(self.e["sufixos"][g].values()), pessoas[g])

    def test_compostos_so_prenomes(self):
        for g in ("F", "M"):
            lista = self.e["compostos"][g]
            self.assertGreater(len(lista), 100)
            for par, n in lista:
                palavras = _ascii(par).split()
                self.assertEqual(len(palavras), 2, par)
                self.assertGreaterEqual(n, 5, par)
                self.assertTrue(set(palavras) <= self.prenomes, par)
        self.assertEqual(self.e["compostos"]["F"][0][0], "MARIA APARECIDA")
        self.assertIn("JOSÉ CARLOS", [p for p, _ in self.e["compostos"]["M"][:5]])

    def test_devocoes_e_taxas(self):
        complementos = {d["complemento"]: d for d in self.e["devocoes"]}
        for c in ("DE FÁTIMA", "DAS GRAÇAS", "DAS CHAGAS", "BATISTA", "DE CÁSSIA"):
            self.assertIn(c, complementos)
        self.assertIn("MARIA", complementos["DE FÁTIMA"]["primeiros"])
        self.assertIn("JOÃO", complementos["BATISTA"]["primeiros"])
        for d in self.e["devocoes"]:
            self.assertTrue(0 <= d["taxa_F"] <= 1 and 0 <= d["taxa_M"] <= 1, d)
            self.assertTrue(all(v >= 5 for v in d["primeiros"].values()), d)
        for g in ("F", "M"):
            for v in self.e["de_jesus"][g].values():
                self.assertTrue(0 <= v <= 1)
            self.assertTrue(0 < self.e["acento"][g]["nomes_com_acento"] < 1)
        for k, posicoes in self.e["particula_por_posicao"].items():
            self.assertEqual(len(posicoes), int(k))

    def test_sufixo_e_masculino(self):
        sufixos = self.e["sufixos"]
        homens = 1 - sufixos["M"]["nenhum"] / self.e["pessoas"]["M"]
        mulheres = 1 - sufixos["F"]["nenhum"] / self.e["pessoas"]["F"]
        self.assertGreater(homens, 0.02)
        self.assertLess(mulheres, homens / 10)


class TestTamanho(unittest.TestCase):
    def test_teto(self):
        arquivos = ("prenomes.tsv.gz", "sobrenomes.tsv.gz", "acentos.tsv.gz", "nomes_estrutura.json")
        total = sum((PASTA / a).stat().st_size for a in arquivos)
        self.assertLess(total, 6_000_000)


if __name__ == "__main__":
    unittest.main()
