"""Base de LUGARES do vocabulário (config/vocabulario: municipios.tsv, ceps_municipio.tsv.gz, logradouros.tsv.gz,
bairros.tsv.gz, enderecos_estatisticas.json), feita por ferramentas/construir_vocabulario_lugares.py a partir do IBGE
(CNEFE 2022, Censo 2022). Confere formato e sanidade dos arquivos e as regras do construtor. Sem rede."""
import collections
import csv
import gzip
import importlib.util
import json
import random
import re
import unicodedata
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
VOCAB = RAIZ / "config" / "vocabulario"
_spec = importlib.util.spec_from_file_location("construir_vocabulario_lugares",
                                               RAIZ / "ferramentas" / "construir_vocabulario_lugares.py")
construtor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(construtor)

ARQUIVOS = ("municipios.tsv", "ceps_municipio.tsv.gz", "logradouros.tsv.gz", "bairros.tsv.gz",
            "enderecos_estatisticas.json")
TEM_BASE = all((VOCAB / a).is_file() for a in ARQUIVOS)
SAO_PAULO, BELO_HORIZONTE, RIO = "3550308", "3106200", "3304557"


def _ler(nome: str) -> list[dict]:
    caminho = VOCAB / nome
    abrir = gzip.open if nome.endswith(".gz") else open
    with abrir(caminho, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def _cabecalho(nome: str) -> list[str]:
    abrir = gzip.open if nome.endswith(".gz") else open
    with abrir(VOCAB / nome, "rt", encoding="utf-8", newline="") as f:
        return f.readline().rstrip("\n").split("\t")


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


class TestRegras(unittest.TestCase):
    """Funções do construtor, com dados de brinquedo."""

    def test_faixa_de_cep_da_uf(self):
        self.assertTrue(construtor.cep_na_uf("01310100", "SP"))
        self.assertTrue(construtor.cep_na_uf("73700000", "GO"))
        self.assertTrue(construtor.cep_na_uf("72800000", "GO"))
        self.assertFalse(construtor.cep_na_uf("72800000", "DF"))
        self.assertFalse(construtor.cep_na_uf("30130000", "SP"))
        self.assertFalse(construtor.cep_na_uf("0131010", "SP"))
        self.assertFalse(construtor.cep_na_uf("0131010A", "SP"))

    def test_porte_e_tamanho_da_amostra(self):
        self.assertEqual(construtor.porte_de(4_000), "ate_20mil")
        self.assertEqual(construtor.porte_de(20_000), "20_a_100mil")
        self.assertEqual(construtor.porte_de(499_999), "100_a_500mil")
        self.assertEqual(construtor.porte_de(11_451_999), "500mil_ou_mais")
        self.assertEqual(construtor.amostra_k(1_000, False), construtor.K_MIN)
        self.assertEqual(construtor.amostra_k(50_000_000, False), construtor.K_MAX)
        self.assertGreaterEqual(construtor.amostra_k(300_000, True), construtor.K_CAPITAL_MIN)
        self.assertLessEqual(construtor.amostra_k(50_000_000, True), construtor.K_CAPITAL_MAX)

    def test_amostra_deterministica_e_sem_repeticao(self):
        itens = [(i % 7 + 1, f"x{i}") for i in range(500)]
        a = construtor.amostrar(itens, 50, "semente")
        self.assertEqual(a, construtor.amostrar(itens, 50, "semente"))
        self.assertEqual(len(set(a)), 50)
        self.assertNotEqual(a, construtor.amostrar(itens, 50, "outra"))
        self.assertEqual(sorted(construtor.amostrar(itens[:10], 50, "s")), sorted(x for _, x in itens[:10]))

    def test_amostra_pesa_pelo_peso(self):
        itens = [(100.0, "pesado")] + [(1.0, f"leve{i}") for i in range(99)]
        vezes = sum("pesado" in construtor.amostrar(itens, 5, f"s{i}") for i in range(200))
        self.assertGreater(vezes, 150)

    def test_normalizar(self):
        self.assertEqual(construtor.normalizar("Agenor M. de Carvalho"), "AGENOR M DE CARVALHO")
        self.assertEqual(construtor.normalizar("  São  João d'Aliança "), "SAO JOAO D ALIANCA")

    def test_acentuador(self):
        ac = construtor.Acentuador()
        for nome in ("São José", "Bairro São José", "Vila São João", "Jardim São Paulo", "Sabiá", "Morro do Sabiá", "Rua Sabiá", "Vila Sabiá", "Alto do Sabiá",
                     "Arenã", "Aldeia Arenã", "Arenã II", "Márcia"):
            ac.nome_ibge(nome)
        for palavra, n in (("sala", 80000), ("salá", 4), ("térreo", 1000), ("terreo", 6), ("sabia", 232000),
                           ("sabiá", 93), ("arena", 2900), ("marcia", 1100), ("márcia", 392), ("oviedo", 31)):
            ac.frequencia(palavra, n)
        for radical in ("Peçanha", "Edílson", "adê"):
            ac.radical(radical)
        casos = {"RUA SAO JOSE": "RUA SÃO JOSÉ", "NILO PECANHA": "NILO PEÇANHA", "SALA": "SALA",
                 "TERREO": "TÉRREO", "RUA DO SABIA": "RUA DO SABIÁ", "ARENA": "ARENA", "MARCIA": "MÁRCIA",
                 "OVIEDO": "OVIEDO", "EDILSON": "EDILSON", "ADE CONJUNTO 21": "ADE CONJUNTO 21",
                 "RUA 31 DE MARCO": "RUA 31 DE MARÇO", "MARCO POLO": "MARCO POLO", "MEM DE SA": "MEM DE SÁ",
                 "RODOVIA PE 060": "RODOVIA PE 060", "QNM 32": "QNM 32"}
        for entrada, esperado in casos.items():
            self.assertEqual(ac(entrada), esperado, entrada)
            self.assertEqual(_sem_acento(ac(entrada)), entrada)  # só acrescenta acento

    def test_tsv_gzip_deterministico(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for i in range(2):
                f = construtor.TSV(Path(d) / f"t{i}.tsv.gz", ["a", "b"])
                f.escrever(["x\ty", 1])
                f.fechar()
            b0, b1 = ((Path(d) / f"t{i}.tsv.gz").read_bytes() for i in range(2))
            self.assertEqual(b0, b1)
            self.assertEqual(gzip.decompress(b0).decode(), "a\tb\nx y\t1\n")


@unittest.skipUnless(TEM_BASE, "base de lugares não gerada (rode ferramentas/construir_vocabulario_lugares.py)")
class TestArquivos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.muns = {m["cod_ibge"]: m for m in _ler("municipios.tsv")}
        cls.ceps = _ler("ceps_municipio.tsv.gz")
        cls.logs = _ler("logradouros.tsv.gz")
        cls.bairros = _ler("bairros.tsv.gz")
        cls.est = json.loads((VOCAB / "enderecos_estatisticas.json").read_text("utf-8"))
        cls.prefixos = collections.defaultdict(set)
        for c in cls.ceps:
            cls.prefixos[c["cod_ibge"]].add(c["prefixo"])

    def test_cabecalhos(self):
        self.assertEqual(_cabecalho("municipios.tsv"),
                         ["cod_ibge", "nome", "uf", "populacao_2022", "ddd", "capital", "porte", "n_enderecos_cnefe",
                          "n_domicilios_cnefe", "cep_unico", "cep_geral", "n_ceps", "cep_min", "cep_max"])
        self.assertEqual(_cabecalho("ceps_municipio.tsv.gz"),
                         ["cod_ibge", "prefixo", "n_enderecos", "n_ceps", "cep_min", "cep_max", "fracao"])
        self.assertEqual(_cabecalho("logradouros.tsv.gz"),
                         ["cod_ibge", "tipo", "titulo", "nome", "bairro", "cep", "urbano", "n_enderecos",
                          "n_domicilios"])
        self.assertEqual(_cabecalho("bairros.tsv.gz"),
                         ["cod_ibge", "bairro", "urbano", "n_enderecos", "n_domicilios", "prefixos_cep", "oficial",
                          "bairro_ibge"])

    def test_municipios(self):
        self.assertGreaterEqual(len(self.muns), 5570)
        ufs = set(construtor.UFS.values())
        for cod, m in self.muns.items():
            self.assertRegex(cod, r"^\d{7}$")
            self.assertEqual(m["uf"], construtor.UFS[cod[:2]])
            self.assertIn(m["uf"], ufs)
            self.assertRegex(m["ddd"], r"^[1-9]\d$")
            self.assertEqual(m["porte"], construtor.porte_de(int(m["populacao_2022"])))
            self.assertIn(m["cep_unico"], ("0", "1"))
        self.assertEqual(sum(int(m["capital"]) for m in self.muns.values()), 27)
        pop = sum(int(m["populacao_2022"]) for m in self.muns.values())
        self.assertTrue(200_000_000 < pop < 210_000_000, pop)
        sp = self.muns[SAO_PAULO]
        self.assertEqual((sp["nome"], sp["uf"], sp["capital"], sp["ddd"]), ("São Paulo", "SP", "1", "11"))
        self.assertEqual(self.muns[BELO_HORIZONTE]["ddd"], "31")
        com_enderecos = sum(1 for m in self.muns.values() if int(m["n_enderecos_cnefe"]) > 0)
        self.assertGreaterEqual(com_enderecos, 5560)
        for cod, m in self.muns.items():
            if m["cep_unico"] == "1":
                self.assertRegex(m["cep_geral"], r"^\d{5}000$", cod)
        self.assertEqual(self.muns[SAO_PAULO]["cep_unico"], "0")

    def test_ceps_na_faixa_da_uf(self):
        fracoes = collections.Counter()
        for c in self.ceps:
            uf = self.muns[c["cod_ibge"]]["uf"]
            self.assertRegex(c["prefixo"], r"^\d{5}$")
            self.assertTrue(construtor.cep_na_uf(c["prefixo"] + "000", uf), c)
            self.assertTrue(c["cep_min"].startswith(c["prefixo"]) and c["cep_max"].startswith(c["prefixo"]))
            self.assertGreaterEqual(int(c["n_enderecos"]), construtor.CEP_PREFIXO_MIN)
            fracoes[c["cod_ibge"]] += float(c["fracao"])
        self.assertTrue(all(f <= 1.005 for f in fracoes.values()))  # arredondamento de 6 casas
        self.assertIn("01310", self.prefixos[SAO_PAULO])     # Av. Paulista
        self.assertIn("30130", self.prefixos[BELO_HORIZONTE])
        self.assertIn("22240", self.prefixos[RIO])           # Laranjeiras
        self.assertTrue(all(p[:1] in "012345" or p[:2] == "08" for p in self.prefixos[SAO_PAULO]))

    def test_logradouros(self):
        self.assertTrue(150_000 <= len(self.logs) <= 450_000, len(self.logs))
        vistos = set()
        por_mun = collections.Counter()
        fora = 0
        for r in self.logs:
            chave = (r["cod_ibge"], r["tipo"], r["titulo"], r["nome"], r["bairro"], r["cep"])
            self.assertNotIn(chave, vistos)
            vistos.add(chave)
            self.assertIn(r["cod_ibge"], self.muns)
            self.assertRegex(r["cep"], r"^\d{8}$")
            self.assertTrue(r["tipo"] and r["nome"], r)
            self.assertIn(r["urbano"], ("0", "1", ""))
            self.assertGreaterEqual(int(r["n_enderecos"]), int(r["n_domicilios"]))
            for col in ("tipo", "titulo", "nome", "bairro"):
                self.assertEqual(r[col], r[col].upper())
                self.assertTrue(_sem_acento(r[col]).isascii())
            fora += r["cep"][:5] not in self.prefixos[r["cod_ibge"]]
            por_mun[r["cod_ibge"]] += 1
        self.assertLess(fora / len(self.logs), 0.001)  # o CEP da rua é um CEP da cidade
        for cod, m in self.muns.items():
            if m["capital"] == "1":
                self.assertGreaterEqual(por_mun[cod], 2500, m["nome"])
        self.assertGreaterEqual(len(por_mun), 5560)
        tipos = collections.Counter(r["tipo"] for r in self.logs)
        self.assertEqual(tipos.most_common(1)[0][0], "RUA")

    def test_bairros(self):
        self.assertGreater(len(self.bairros), 50_000)
        nomes = collections.defaultdict(set)
        for b in self.bairros:
            self.assertIn(b["cod_ibge"], self.muns)
            self.assertGreaterEqual(int(b["n_enderecos"]), construtor.BAIRRO_MIN)
            self.assertIn(b["oficial"], ("0", "1"))
            self.assertIn(b["urbano"], ("0", "1", ""))
            self.assertRegex(b["prefixos_cep"], r"^(\d{5}(,\d{5}){0,2})?$")
            self.assertEqual(b["bairro"], b["bairro"].upper())
            nomes[b["cod_ibge"]].add(b["bairro"])
        self.assertIn("LARANJEIRAS", nomes[RIO])
        self.assertIn("BELA VISTA", nomes[SAO_PAULO])
        self.assertIn("SAVASSI", nomes[BELO_HORIZONTE])
        # o bairro da rua (urbana) costuma estar na lista de bairros da cidade
        pares = [(r["cod_ibge"], r["bairro"]) for r in self.logs if r["bairro"] and r["urbano"] == "1"]
        achados = sum(b in nomes[c] for c, b in pares)
        self.assertGreater(achados / len(pares), 0.97)

    def test_ruas_famosas(self):
        def tem(cod, tipo, nome, prefixo=""):
            return any(r["cod_ibge"] == cod and r["tipo"] == tipo and r["nome"] == nome and r["cep"].startswith(prefixo)
                       for r in self.logs)
        self.assertTrue(tem(SAO_PAULO, "AVENIDA", "PAULISTA", "013"))
        self.assertTrue(tem(BELO_HORIZONTE, "AVENIDA", "AFONSO PENA", "30"))
        self.assertTrue(tem(RIO, "RUA", "DAS LARANJEIRAS", "222"))

    def test_estatisticas(self):
        e = self.est
        for chave in ("n_domicilios", "tipos_logradouro", "titulos", "numero", "modificadores", "complementos",
                      "apartamentos", "cep_sufixo", "especies"):
            self.assertIn(chave, e)
        self.assertGreater(e["n_domicilios"], 80_000_000)
        self.assertEqual(next(iter(e["tipos_logradouro"]["nacional"])), "RUA")
        self.assertEqual(set(e["tipos_logradouro"]["por_porte"]), {p for p, _ in construtor.PORTES})
        self.assertEqual(len(e["tipos_logradouro"]["por_uf"]), 27)
        for chave in ("fracao_com_titulo",):
            self.assertTrue(0 < e["titulos"][chave] < 1)
        self.assertTrue(0 < e["numero"]["nacional"]["fracao_sem_numero"] < 1)
        self.assertTrue(0 < e["complementos"]["fracao_com_complemento"] < 1)
        for nome in ("APARTAMENTO", "BLOCO", "CASA", "LOTE", "QUADRA"):
            self.assertIn(nome, e["complementos"]["elementos"])
        self.assertIn("BLOCO+APARTAMENTO", e["complementos"]["combinacoes"])
        self.assertIn("101", e["apartamentos"]["valores_top"])
        self.assertTrue(all(k.isdigit() for k in e["apartamentos"]["andar"]))

    def test_sem_dado_pessoal(self):
        """Nada de número de casa ligado a rua, coordenada ou nome de estabelecimento."""
        proibidas = {"num_endereco", "numero", "latitude", "longitude", "dsc_estabelecimento", "complemento"}
        for nome in ARQUIVOS[:4]:
            self.assertFalse(proibidas & set(_cabecalho(nome)), nome)

    def test_tamanho(self):
        total = sum((VOCAB / a).stat().st_size for a in ARQUIVOS + ("LEIA-ME_lugares.md",))
        self.assertLess(total, 16 * 2**20)


if __name__ == "__main__":
    unittest.main()
