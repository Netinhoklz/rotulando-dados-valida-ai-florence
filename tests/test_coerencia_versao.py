"""Coerência dos valores trocados numa mesma versão: o histórico de consumo anda junto com o mês de referência, a
mesma data repetida sai no formato de cada lugar, a cidade com a UF grudada pelo OCR mantém a UF, o bairro escrito
no endereço acompanha a cidade nova, data e mês (cidade e CEP) entram juntos no sorteio de classes e nenhuma data
falsa passa de hoje."""
import datetime as dt
import random
import unittest

from rotulador import gerador as mod_gerador
from rotulador.ops import geradores


class TestMesesDaVersao(unittest.TestCase):
    def test_historico_anda_junto_com_a_referencia(self):
        meses = ("JAN/2026", "Dez 2025", "Nov 2025", "Out 2025", "Set 2025")
        campos = [{"categoria": "mês de referência", "texto": t} for t in meses] + [
            {"categoria": "data", "texto": "09/02/2026"}]
        for sem in range(10):
            with geradores.usando(random.Random(sem), "treino", hoje=dt.date(2026, 3, 20), documento=campos):
                novos = [geradores.ler_mes(geradores.parecido("mês de referência", t)) for t in meses]
            n = [a * 12 + m for a, m in novos]
            self.assertEqual([n[0] - x for x in n], [0, 1, 2, 3, 4], novos)  # a sequência de um em um continua
            self.assertNotEqual(novos[0], (2026, 1))

    def test_sigla_lida_errado_pelo_ocr(self):
        self.assertEqual(geradores.ler_mes("AG0/2026"), (2026, 8))
        self.assertEqual(geradores.mes_do_ocr("Ag0/26"), "Ago/26")
        self.assertEqual(geradores.mes_do_ocr("SALA 101"), "SALA 101")
        campos = [{"categoria": "mês de referência", "texto": "AG0/2026"}, {"categoria": "data", "texto": "14/09/2026"}]
        for sem in range(10):
            with geradores.usando(random.Random(sem), "treino", hoje=dt.date(2026, 9, 30), documento=campos):
                novo = geradores.parecido("mês de referência", "AG0/2026")
            self.assertRegex(novo, r"^[A-Z]{3}/20\d\d$")  # antes: "AG7/1478"
        self.assertEqual(geradores.parecido("mês de referência", "XYZ"), "XYZ")  # ilegível: fica (o campo é pulado)

    def test_mesma_data_no_formato_de_cada_lugar(self):
        self.assertEqual(mod_gerador._mesmo_valor("mês de referência", "Jan 2026", "FEV/2025", "JAN/2026"), "Fev 2025")
        self.assertEqual(mod_gerador._mesmo_valor("data", "09.02.2026", "10/03/2025", "09/02/2026"), "10.03.2025")


    def test_datas_sem_rotulo_andam_juntas(self):
        campos = [{"categoria": "data", "texto": "17/09/2026", "rotulo": ""},
                  {"categoria": "data", "texto": "27/08/2026", "rotulo": ""}]
        for sem in range(8):
            with geradores.usando(random.Random(sem), "treino", hoje=dt.date(2026, 10, 1), documento=campos):
                a, b = (geradores.ler_data(geradores.parecido("data", t)) for t in ("17/09/2026", "27/08/2026"))
            self.assertTrue(18 <= (a - b).days <= 24, (a, b))  # 21 dias no original (± o ajuste do dia útil)

    def test_marca_de_complemento_lida_com_digito(self):
        self.assertEqual(geradores._marca_do_ocr("AV BRASIL, 1834 APT0 38"), "AV BRASIL, 1834 APTO 38")
        self.assertEqual(geradores._marca_do_ocr("RUA X BL0C0 B"), "RUA X BLOCO B")
        self.assertEqual(geradores._marca_do_ocr("BR101"), "BR101")  # não é marca: fica
        with geradores.usando(random.Random(1), "treino", documento=[]):
            self.assertRegex(geradores.parecido("endereço", "AV BRASIL, 1834 APT0 38"), r" APTO \d+$")  # antes "APT3 94"

    def test_consumo_em_metro_e_metro_cubico(self):
        with geradores.usando(random.Random(1)):
            self.assertRegex(geradores.parecido("consumo/leitura", "205m"), r"^\d+ m³$")  # o OCR perdeu " " e "³"
            self.assertRegex(geradores.parecido("consumo/leitura", "183 m³"), r"^\d+ m³$")

    def test_rotulo_com_letras_espacadas(self):
        from rotulador import coerencia
        self.assertEqual(coerencia.tipo_da_data("V E N C I M E N TO"), "vencimento")
        self.assertEqual(coerencia.tipo_da_data("DATADALEITURA"), "leitura")


class TestOcr(unittest.TestCase):
    def test_pedacos_colados_da_mesma_linha_viram_um(self):
        import numpy as np
        from rotulador import ocr
        img = np.full((200, 900, 3), 250, np.uint8)
        linhas = [{"caixa": [120, 427, 275, 484], "texto": "JOS E", "bruto": "JOS E", "conf": 0.9},
                  {"caixa": [267, 433, 725, 479], "texto": "CARLOS RIBEIRO", "bruto": "CARLOS RIBEIRO", "conf": 0.95},
                  {"caixa": [1275, 438, 1691, 478], "texto": "905.019.909-76", "bruto": "905.019.909-76", "conf": 0.9},
                  {"caixa": [120, 386, 224, 418], "texto": "NOME", "bruto": "NOME", "conf": 0.9}]
        out = ocr._juntar_pedacos(linhas, img)
        self.assertEqual(len(out), 3)  # o CPF (outra coluna) e o rótulo (outra linha) ficam separados
        nome = next(l for l in out if "CARLOS" in l["bruto"])
        self.assertEqual(nome["caixa"], [120, 427, 725, 484])
        self.assertEqual(nome["bruto"], "JOS E CARLOS RIBEIRO")


class TestCidade(unittest.TestCase):
    def test_uf_grudada_pelo_ocr(self):
        self.assertEqual(geradores.cidade_do_texto("C U RITIBAPR"), ("CURITIBA", "PR"))
        self.assertEqual(geradores.cidade_no_formato("C U RITIBAPR", "FORTALEZA", "CE"), "FORTALEZA/CE")
        self.assertEqual(geradores.cidade_no_formato("Curitiba/PR", "Fortaleza", "CE"), "Fortaleza/CE")

    def test_so_a_cidade_fica_na_mesma_uf(self):
        if geradores._lugares() is None:
            self.skipTest("sem a base de lugares")
        ufs = {geradores.cidade_mais_parecida("Fortaleza", random.Random(i))[1] for i in range(40)}
        self.assertIn("CE", ufs)
        self.assertGreater(sum(geradores.cidade_mais_parecida("Fortaleza", random.Random(i))[1] == "CE"
                               for i in range(40)), 25)

    def test_bairro_escrito_acompanha_a_cidade_nova(self):
        if geradores._lugares() is None:
            self.skipTest("sem a base de lugares")
        antigo = "RUA 47 CJ PREFEITO JOSE WALTER 312"
        for sem in range(6):
            with geradores.usando(random.Random(sem), "treino", cidade=("JOINVILLE", "SC"), documento=[]):
                novo = geradores.parecido("endereço", antigo)
            self.assertNotIn("WALTER", novo)
            with geradores.usando(random.Random(sem), "treino", cidade=("FORTALEZA", "CE"), documento=[]):
                self.assertIn("PREFEITO JOSE WALTER", geradores.parecido("endereço", antigo))  # a mesma cidade: fica


class TestVersao(unittest.TestCase):
    def test_data_e_mes_juntos_e_cidade_leva_cep_e_bairro(self):
        classes = ["nome", "endereço", "bairro", "data", "mês de referência", "cidade/uf", "cep"]
        rng = random.Random(0)
        vistos = set()
        for _ in range(300):
            s = set(mod_gerador._subconjunto(classes, rng, None))
            vistos.add(frozenset(s))
            self.assertTrue(s)
            self.assertEqual("data" in s, "mês de referência" in s, s)
            if "cidade/uf" in s:
                self.assertTrue({"cep", "bairro"} <= s, s)
        self.assertIn(frozenset({"cep"}), vistos)  # o CEP (ou o bairro) sozinho, na mesma cidade, continua saindo
        self.assertIn(frozenset({"nome"}), vistos)
        rng = random.Random(1)  # nome e endereço: as duas 50%, cada uma sozinha 25%
        n = [tuple(mod_gerador._subconjunto(["nome", "endereço"], rng, None)) for _ in range(2000)]
        self.assertAlmostEqual(n.count(("nome", "endereço")) / 2000, 0.5, delta=0.05)

    def test_envio_nunca_depois_de_hoje(self):
        class Ctx:
            campos = [{"categoria": "data", "texto": (dt.date.today() - dt.timedelta(days=20)).strftime("%d/%m/%Y")}]
        for sem in range(50):
            d = mod_gerador._data_envio(Ctx(), random.Random(sem))
            self.assertLessEqual(d, dt.date.today())


if __name__ == "__main__":
    unittest.main()
