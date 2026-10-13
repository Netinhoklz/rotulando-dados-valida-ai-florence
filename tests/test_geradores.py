"""Geradores de valores falsos (rotulador/ops/geradores.py): data de referência por documento, CEP na faixa da
cidade, vocabulário amplo repartido por split e código de barras amarrado ao documento. Sem OCR, rápidos."""
import datetime as dt
import random
import unittest

from rotulador.ops import codigos, geradores
from tests.test_integracao_vocabulario import sem_bases

HOJE = dt.date(2024, 5, 20)


class TestHoje(unittest.TestCase):
    def test_usando_aninhado_mantem_o_hoje(self):
        self.assertEqual(geradores.hoje(), dt.date.today())
        with geradores.usando(hoje=HOJE):
            self.assertEqual(geradores.hoje(), HOJE)
            with geradores.usando(random.Random(1), "teste"):  # como as técnicas: rng e split por dentro
                self.assertEqual(geradores.hoje(), HOJE)
                self.assertEqual(geradores._split_atual.get(), "teste")
                with geradores.usando(random.Random(2)):  # sem split: mantém o split de fora
                    self.assertEqual(geradores._split_atual.get(), "teste")
                    self.assertEqual(geradores.hoje(), HOJE)
            self.assertIsNone(geradores._split_atual.get())
        self.assertEqual(geradores.hoje(), dt.date.today())

    def test_data_recente_respeita_o_hoje(self):
        with geradores.usando(random.Random(3), hoje=HOJE):
            for _ in range(200):
                d = geradores.ciclo_de_datas()["vencimento"]
                self.assertTrue(-10 <= (HOJE - d).days <= 440, d)
            for _ in range(50):  # o vencimento pode estar uns dias à frente (a conta que ainda não venceu); o resto não
                d = dt.datetime.strptime(geradores.gerar("data")["texto"], "%d/%m/%Y").date()
                self.assertLessEqual(d, HOJE + dt.timedelta(days=10))
                d = dt.datetime.strptime(geradores.parecido("data", "10/10/2026"), "%d/%m/%Y").date()
                self.assertLessEqual(d, HOJE)
                self.assertLessEqual(int(geradores.parecido("mês de referência", "SET/2026")[-4:]), HOJE.year)
                cod, _, _ = geradores.par_codigo(False)  # vencimento do boleto em torno do hoje, não de date.today()
                venc = dt.date(1997, 10, 7) + dt.timedelta(days=int(cod[5:9]))
                self.assertLessEqual(abs((venc - HOJE).days), 60, venc)


class TestCep(unittest.TestCase):
    @staticmethod
    def _n(cep: str) -> int:
        return int(cep.replace("-", ""))

    CASOS = (("CURITIBA", "PR", (80000000, 82999999)), ("SAO PAULO", "SP", (1000000, 5999999)),
             ("MANAUS", "AM", (69000000, 69099999)), ("BELO HORIZONTE", "MG", (30000000, 31999999)),
             ("ARACAJU", "SE", (49000000, 49099999)), ("BRASILIA", "DF", (70000000, 73699999)))

    def test_cep_de_e_um_cep_da_cidade(self):
        """Com a base de lugares, um CEP que a cidade usa (o de uma rua dela): SP também tem 08xxx, que a faixa escrita
        à mão (01000–05999) deixava de fora. Sem a base, a faixa dos Correios da cidade."""
        for cidade, uf, faixa in self.CASOS:
            self.assertEqual(geradores.FAIXAS_CEP[cidade], faixa)
            for _ in range(30):
                cep = geradores.cep_de(cidade, uf)
                self.assertRegex(cep, r"^\d{5}-\d{3}$")
                self.assertTrue(geradores.cep_na_cidade(cep, (cidade, uf)), (cidade, cep))
        self.assertTrue(geradores.cep_na_cidade(geradores.cep_de("Curitiba"), ("CURITIBA", "PR")))  # acento/caixa, sem UF
        with sem_bases():
            for cidade, uf, faixa in self.CASOS:
                for _ in range(30):
                    self.assertTrue(faixa[0] <= self._n(geradores.cep_de(cidade, uf)) <= faixa[1], cidade)
            self.assertTrue(80000000 <= self._n(geradores.cep_de("Curitiba")) <= 82999999)

    def test_cep_so_pela_uf(self):
        lugares = geradores._lugares()
        for uf, (ini, fim) in geradores.FAIXAS_UF.items():
            for cep in (geradores.cep_de(None, uf), geradores.cep_de("LUGAR NENHUM", uf),
                        geradores.cep_de("curitiba", uf)):  # Curitiba só vale no PR; em outra UF, uma cidade da UF
                self.assertEqual(lugares["mun"][geradores._dono_do_cep(cep)][1], uf, cep)
        with sem_bases():
            for uf, (ini, fim) in geradores.FAIXAS_UF.items():
                for cep in (geradores.cep_de(None, uf), geradores.cep_de("curitiba", uf)):
                    self.assertTrue(ini <= self._n(cep) <= fim, (uf, cep))

    def test_toda_cidade_embutida_tem_faixa_na_propria_uf(self):
        for cidade, uf in geradores._CIDADES:
            ini, fim = geradores.FAIXAS_CEP[geradores._normalizar(cidade)]  # o vocabulário tem acento; a faixa, não
            a, b = geradores.FAIXAS_UF[uf]
            self.assertTrue(a <= ini <= fim <= b, cidade)
        lugares = geradores._lugares()
        for _ in range(100):
            cep = geradores.cep_de()  # sem cidade nem UF: o de uma cidade sorteada (pela população)
            cod = geradores._dono_do_cep(cep)
            self.assertTrue(geradores.cep_na_cidade(cep, lugares["mun"][cod][:2]), cep)
        with sem_bases():
            for _ in range(100):
                cep = geradores.cep_de()
                self.assertTrue(any(ini <= self._n(cep) <= fim for ini, fim in geradores.FAIXAS_CEP.values()), cep)

    def test_cidade_do_texto(self):
        for texto in ("CURITIBA/PR", "CURITIBA - PR", "Curitiba PR", "curitiba, pr", " CURITIBA-PR ",
                      "CIDADE: CURITIBA/PR"):
            self.assertEqual(geradores.cidade_do_texto(texto), ("CURITIBA", "PR"), texto)
        self.assertEqual(geradores.cidade_do_texto("São José dos Campos/SP"), ("SAO JOSE DOS CAMPOS", "SP"))
        self.assertEqual(geradores.cidade_do_texto("Santa Maria - RS"), ("SANTA MARIA", "RS"))
        self.assertEqual(geradores.cidade_do_texto("MANAUS AM"), ("MANAUS", "AM"))
        for texto in ("PR", "- PR", "CURITIBA", "CURITIBA/XX", "PASSO DE TORRES", ""):
            self.assertIsNone(geradores.cidade_do_texto(texto), texto)

    def test_gerar_e_parecido_usam_a_cidade_atual(self):
        self.assertIsNone(geradores.cidade_atual())
        with geradores.usando(cidade=("CURITIBA", "PR")):
            self.assertEqual(geradores.cidade_atual(), ("CURITIBA", "PR"))
            with geradores.usando(random.Random(5), "teste"):  # a cidade sobrevive ao usando(rng, split) das técnicas
                self.assertEqual(geradores.cidade_atual(), ("CURITIBA", "PR"))
                for _ in range(30):
                    self.assertRegex(geradores.gerar("cep")["texto"], r"^8[0-2]\d{3}-\d{3}$")
                    self.assertRegex(geradores.parecido("cep", "30123-456"), r"^8[0-2]\d{3}-\d{3}$")
                    self.assertRegex(geradores.parecido("cep", "CEP 30123456"), r"^CEP 8[0-2]\d{6}$")
                    manaus = geradores.parecido("cep", "30123-456", cidade=("MANAUS", "AM"))
                    self.assertRegex(manaus, r"^690\d{2}-\d{3}$")
                    salvador = geradores.parecido("cep", "30123-456", cidade="Salvador/BA")
                    self.assertRegex(salvador, r"^4[0-2]\d{3}-\d{3}$")
        self.assertIsNone(geradores.cidade_atual())

    def test_parecido_cep_sem_cidade_fica_na_regiao_do_antigo(self):
        for _ in range(30):
            self.assertRegex(geradores.parecido("cep", "30123-456"), r"^3[01]\d{3}-\d{3}$")  # Belo Horizonte
            self.assertRegex(geradores.parecido("cep", "36999-000"), r"^3\d{4}-\d{3}$")  # MG, cidade desconhecida


class TestVocabulario(unittest.TestCase):
    def test_tamanhos_minimos(self):
        for lista, minimo in ((geradores._NOMES, 300), (geradores._SOBRENOMES, 200), (geradores._NOMES_RUA, 200),
                              (geradores._BAIRROS, 150), (geradores._CIDADES, 110)):
            self.assertGreaterEqual(len(lista), minimo)
            self.assertEqual(len({geradores._chave(x) for x in lista}), len(lista))  # sem repetição (JOSÉ = JOSE)
            for x in lista:
                x = x[0] if isinstance(x, tuple) else x
                self.assertTrue(x == x.upper() and x == " ".join(x.split()), x)  # grafia com acento: JOSÉ, SÃO PAULO
            self.assertTrue(any(not (x[0] if isinstance(x, tuple) else x).isascii() for x in lista))
        self.assertGreaterEqual(len({uf for _, uf in geradores._CIDADES}), 26)
        self.assertLessEqual({uf for _, uf in geradores._CIDADES}, set(geradores.FAIXAS_UF))

    def test_reparticao_por_split(self):
        splits = [sp for sp, _ in geradores._FATIAS]
        por_palavra = {}
        for nome, partes in geradores._PARTES.items():
            self.assertEqual(list(partes), splits)
            self.assertTrue(all(partes.values()), nome)  # nenhum split fica sem entradas
            juntas = [x for sp in splits for x in partes[sp]]
            nucleo = [x for x in geradores._VOCAB[nome] if geradores._chave(x) in geradores._NUCLEO]
            self.assertFalse({geradores._chave(x) for x in juntas} & geradores._NUCLEO)  # o núcleo não é repartido
            # cada entrada da cauda em um split só
            self.assertEqual(sorted(map(repr, juntas + nucleo)), sorted(map(repr, geradores._VOCAB[nome])))
            for sp in splits:
                for x in partes[sp]:  # "SANTOS" cidade = sobrenome; "FRANÇA" sobrenome = "FRANCA" cidade
                    self.assertEqual(por_palavra.setdefault(geradores._chave(x), sp), sp, x)
        self.assertGreaterEqual(min(len(v) for v in geradores._PARTES["nomes"].values()), 30)
        with geradores.usando(random.Random(7), "teste"):
            do_teste = {geradores._chave(x) for x in geradores._PARTES["nomes"]["teste"]} | geradores._NUCLEO
            self.assertTrue(all(geradores._chave(geradores.palavra("prenome")) in do_teste for _ in range(100)))


class TestCodigos(unittest.TestCase):
    def test_partes_da_linha_arrecadacao(self):
        cod, linha, cent = geradores.par_codigo(True, empresa="1234", segmento="2")
        self.assertTrue(codigos.valida_arrecadacao(cod))
        self.assertEqual((cod[1], cod[15:19], int(cod[4:15])), ("2", "1234", cent))
        esperado = {"arrecadacao": True, "segmento": "2", "ref": cod[2], "empresa": "1234"}
        self.assertEqual(geradores.partes_da_linha(linha), esperado)
        self.assertEqual(geradores.partes_da_linha(cod), esperado)
        novo, _, _ = geradores.par_codigo(**esperado, centavos=138457)  # o código novo repete as partes do original
        self.assertEqual((novo[:3], novo[15:19], int(novo[4:15])), (cod[:3], "1234", 138457))
        self.assertNotEqual(novo, cod)

    def test_partes_da_linha_boleto(self):
        cod, linha, cent = geradores.par_codigo(False, banco="341")
        self.assertTrue(codigos.valida_boleto(cod))
        self.assertEqual(sum(c.isdigit() for c in linha), 47)
        self.assertEqual(geradores.partes_da_linha(linha), {"arrecadacao": False, "banco": "341"})
        self.assertEqual(geradores.partes_da_linha(cod), {"arrecadacao": False, "banco": "341"})
        novo, _, _ = geradores.par_codigo(**geradores.partes_da_linha(linha), centavos=138457)
        self.assertEqual((novo[:3], int(novo[9:19])), ("341", 138457))

    def test_par_codigo_valor_e_deducao(self):
        for arrecadacao in (True, False):
            cod, _, cent = geradores.par_codigo(arrecadacao, centavos=138457)
            self.assertEqual(cent, 138457)
            self.assertEqual(int(cod[4:15]) if arrecadacao else int(cod[9:19]), 138457)
        self.assertTrue(geradores.par_codigo(empresa="0048")[0].startswith("8"))  # empresa → arrecadação
        self.assertTrue(geradores.par_codigo(banco="001")[0].startswith("001"))  # banco → boleto
        self.assertEqual(geradores.par_codigo(True, ref="7")[0][2], "7")

    def test_par_codigo_valida_as_partes(self):
        for kw in (dict(empresa="12"), dict(empresa="12a4"), dict(segmento="0"), dict(banco="1"), dict(ref="5"),
                   dict(centavos=0), dict(centavos=10 ** 11)):
            with self.assertRaises(ValueError, msg=kw):
                geradores.par_codigo(**kw)

    def test_partes_da_linha_nao_reconhece(self):
        for texto in ("", "abc", "1234", "8366 0000", None):
            self.assertIsNone(geradores.partes_da_linha(texto))

    def test_valor_em_centavos(self):
        self.assertEqual(geradores.valor_em_centavos("R$ 1.384,57"), 138457)
        self.assertEqual(geradores.valor_em_centavos("R$ 100,00"), 10000)
        self.assertEqual(geradores.valor_em_centavos("TOTAL A PAGAR: R$ 12,30"), 1230)
        self.assertIsNone(geradores.valor_em_centavos("R$ 0,00"))
        self.assertIsNone(geradores.valor_em_centavos("sem valor"))


class TestParecidoFator(unittest.TestCase):
    def test_fator_forca_a_proporcao(self):
        self.assertEqual(geradores.parecido("valor", "R$ 100,00", fator=2.0), "R$ 200,00")
        self.assertEqual(geradores.parecido("valor", "R$ 1.000,00", fator=0.5), "R$ 500,00")
        self.assertEqual(geradores.parecido("valor", "Total R$ 1.384,57", fator=1.0), "Total R$ 1.384,57")
        self.assertEqual(geradores.parecido("consumo/leitura", "200 kWh", fator=1.5), "300 kWh")
        self.assertEqual(geradores.parecido("consumo/leitura", "12 m³", fator=0.25), "3 m³")
        for _ in range(30):  # sem fator, continua sorteado no intervalo de sempre
            v = geradores._numero_br(geradores.parecido("valor", "R$ 100,00"))
            self.assertTrue(35 <= v <= 280, v)


if __name__ == "__main__":
    unittest.main()
