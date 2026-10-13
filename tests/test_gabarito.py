"""Gabarito do Brasil (rotulador.gabarito) e o detector de campos usando ele: nome de pessoa pelos prenomes do Censo e
sobrenomes do TSE, município, bairro (da cidade, do CEP ou do país), tipo de logradouro (CNEFE + Correios + DF),
emissor e complemento. Na bateria com gabarito de E:/rotulador/analise_campos (12/10/2026): nome 56% → 99%, endereço
65% → 100%, bairro 46% → 100%, cidade 91% → 99%."""
import unittest

from rotulador import campos, gabarito as g

TEM_BASES = g.disponivel()


@unittest.skipUnless(TEM_BASES, "bases de config/vocabulario ausentes")
class TestConsultas(unittest.TestCase):
    def test_nome_de_pessoa(self):
        for s in ("JOÃO BATISTA DOS SANTOS", "Paulo Henrique Costa", "SEBASTIÃO RIBEIRO FILHO", "M R C FILHO FOTOS"):
            self.assertGreaterEqual(g.nome_de_pessoa(s), 0.8, s)
        for s in ("INFORMAÇÕES DA INSTALAÇÃO", "AVISO DE DÉBITO PENDENTE", "RUA DAS FLORES", "MARIA 123", "CENTRO"):
            self.assertLess(g.nome_de_pessoa(s), 0.5, s)
        self.assertTrue(g.empresa("PADARIA BOM PÃO LTDA"))
        self.assertFalse(g.empresa("MARIA DA SILVA"))

    def test_tipo_de_logradouro(self):
        for s, tipo in (("R JOAO CORDEIRO 1100", "RUA"), ("SQN 308 BLOCO C APTO 201", "SQN"),
                        ("SÍTIO BOA VISTA, S/N, ZONA RURAL", "SÍTIO"), ("ESTR. DO COCO, KM 12, CASA 4", "ESTRADA"),
                        ("COND. PARQUE DAS ÁRVORES, QD 3 LT 14", "CONDOMÍNIO"), ("AV. BRASIL", "AVENIDA")):
            self.assertEqual((g.tipo_logradouro(s) or ("",))[0], tipo, s)
        for s in ("LINHA DIGITÁVEL", "CENTRO", "VILA NOVA", "Central de atendimento: 0800 123 4567", "Set 2026",
                  "Al d eota", "R"):
            self.assertIsNone(g.tipo_logradouro(s), s)

    def test_lugares(self):
        self.assertEqual(g.municipio("BELO HORIZONTE MG")[:2], ("Belo Horizonte", "MG"))
        self.assertEqual(g.municipio("Fortaleza - CE")[1], "CE")
        self.assertIsNone(g.municipio("FORTALEZA - SP"))  # UF errada
        self.assertEqual(g.bairro("MESSEJANA", "2304400"), "cidade")  # Fortaleza
        self.assertEqual(g.bairro("ALDEOTA", None, "60150-000"), "cep")
        self.assertEqual(g.bairro("ASA NORTE"), "pais")
        self.assertEqual(g.bairro("AL D EOTA"), "pais")  # o OCR inventou espaços
        self.assertEqual(g.bairro("DISTRITO DE JUÁ"), "pais")
        self.assertEqual(g.bairro("INFORMAÇÕES"), "")
        self.assertEqual(g.emissor("COMPANHIA ENERGETICA DO CEARA - ENEL"), "ENEL")
        self.assertTrue(g.complemento("BLOCO C APTO 204"))
        self.assertFalse(g.complemento("BLOCO DE NOTAS"))


def _l(i, texto, y, x=100, h=30):
    return {"texto": texto, "caixa": [x, y, x + 20 * len(texto), y + h], "conf": 0.95}


@unittest.skipUnless(TEM_BASES, "bases de config/vocabulario ausentes")
class TestDetectorComGabarito(unittest.TestCase):
    """Envelopes sem rótulo, como na bateria: o detector classifica pelo gabarito (sem OCR: as linhas vêm prontas)."""

    def _cats(self, linhas):
        import numpy as np
        img = np.full((1200, 1000, 3), 250, np.uint8)
        return {c["texto"]: c["categoria"] for c in campos.detectar(img, linhas)}

    def test_envelope_sem_rotulo(self):
        cats = self._cats([_l(0, "JOÃO BATISTA DOS SANTOS", 400), _l(1, "R JOAO CORDEIRO 1100", 436),
                           _l(2, "ALDEOTA", 472), _l(3, "FORTALEZA CE", 508)])
        self.assertEqual(cats["JOÃO BATISTA DOS SANTOS"], "nome")
        self.assertEqual(cats["R JOAO CORDEIRO 1100"], "endereço")
        self.assertEqual(cats["ALDEOTA"], "bairro")
        self.assertEqual(cats["FORTALEZA CE"], "cidade/uf")

    def test_complemento_e_titulo(self):
        cats = self._cats([_l(0, "INFORMAÇÕES DA INSTALAÇÃO", 360), _l(1, "PAULO HENRIQUE COSTA", 400),
                           _l(2, "SQN 308 BLOCO C", 436), _l(3, "APTO 201", 472), _l(4, "ASA NORTE", 508)])
        self.assertIsNone(cats["INFORMAÇÕES DA INSTALAÇÃO"])
        self.assertEqual(cats["PAULO HENRIQUE COSTA"], "nome")
        self.assertEqual(cats["SQN 308 BLOCO C"], "endereço")
        self.assertEqual(cats["APTO 201"], "endereço")
        self.assertEqual(cats["ASA NORTE"], "bairro")

    def test_cliente_pessoa_juridica_nao_vira_emissor(self):
        cats = self._cats([_l(0, "PADARIA BOM PÃO LTDA", 500), _l(1, "RUA PADRE EUSTÁQUIO, 1200", 536),
                           _l(2, "CARLOS PRATES", 572)])
        self.assertEqual(cats["PADARIA BOM PÃO LTDA"], "nome")
        self.assertEqual(cats["RUA PADRE EUSTÁQUIO, 1200"], "endereço")
        self.assertEqual(cats["CARLOS PRATES"], "bairro")  # sem cidade na página: debaixo da rua não é nome


def _env(*textos, y0=400):
    return [_l(i, t, y0 + 36 * i) for i, t in enumerate(textos)]


@unittest.skipUnless(TEM_BASES, "bases de config/vocabulario ausentes")
class TestNomeDeGenteNoLugar(unittest.TestCase):
    """Rua, bairro e cidade com nome de gente (GETÚLIO VARGAS, CARLOS PRATES, JOÃO PESSOA) não viram o nome do cliente,
    e o cliente com nome de cidade (PAULO AFONSO) não vira cidade: decide a posição no envelope e a homenagem."""

    def _cats(self, linhas):
        import numpy as np
        img = np.full((1200, 1000, 3), 250, np.uint8)
        achado = {c["texto"]: c["categoria"] for c in campos.detectar(img, linhas)}
        return [achado.get(l["texto"], "—") for l in linhas]

    def test_consultas(self):
        self.assertEqual(g.nome_de_pessoa("JOSÉ DE ALENCAR S/N"), 0.0)  # S e N não são iniciais
        self.assertEqual(g.nome_de_pessoa("PRESIDENTE KENNEDY"), 0.0)  # título de homenagem
        self.assertEqual(g.nome_de_pessoa("DR. JOSÉ MARIA"), 0.0)
        self.assertGreaterEqual(g.nome_de_pessoa("SANTA ALVES DA SILVA"), 0.9)  # SANTA também é prenome
        self.assertGreaterEqual(g.homenagem("GETÚLIO VARGAS"), g.HOMENAGEM_MINIMA)
        self.assertGreaterEqual(g.homenagem("RUI BARBOSA"), g.HOMENAGEM_MINIMA)
        self.assertLess(g.homenagem("JOSÉ DA SILVA"), g.HOMENAGEM_MINIMA)
        self.assertLess(g.homenagem("MARIA DAS GRAÇAS PEREIRA"), g.HOMENAGEM_MINIMA)
        self.assertEqual(g.logradouro_sem_tipo("RUI BARBOSA 455"), "pais")
        self.assertEqual(g.logradouro_sem_tipo("BARÃO DO RIO BRANCO, 1020", "2304400"), "cidade")
        for s in ("CONSUMO MEDIO 150", "LEITURA ATUAL 4521", "CONTA CONTRATO 123456", "LOTE 14 QD 3", "RUA X 10"):
            self.assertEqual(g.logradouro_sem_tipo(s), "", s)

    def test_debaixo_da_rua_e_bairro(self):
        self.assertEqual(self._cats(_env("MARIA APARECIDA SOUZA", "RUA JOSÉ DE ALENCAR, 120", "JOSÉ WALTER")),
                         ["nome", "endereço", "bairro"])
        self.assertEqual(self._cats(_env("LUCIANA RODRIGUES LIMA", "R JOAO CORDEIRO 1100", "GETÚLIO VARGAS",
                                         "FORTALEZA CE")), ["nome", "endereço", "bairro", "cidade/uf"])
        self.assertEqual(self._cats(_env("ANA PAULA SOUSA", "RUA TIRADENTES, 300", "APTO 102", "ANTÔNIO BEZERRA",
                                         "FORTALEZA CE")), ["nome", "endereço", "endereço", "bairro", "cidade/uf"])
        self.assertEqual(self._cats(_env("ROBERTO CARLOS MENDES", "RUA DAS FLORES, 12", "PRESIDENTE KENNEDY",
                                         "VITÓRIA ES")), ["nome", "endereço", "bairro", "cidade/uf"])

    def test_cliente_com_nome_de_cidade(self):
        for cliente in ("PAULO AFONSO", "JOÃO PESSOA", "ANTONIO CARLOS"):
            self.assertEqual(self._cats(_env(cliente, "RUA DAS ACÁCIAS, 10", "CENTRO", "MARIA HELENA PR")),
                             ["nome", "endereço", "bairro", "cidade/uf"], cliente)

    def test_rua_sem_tipo(self):
        self.assertEqual(self._cats(_env("FRANCISCO DAS CHAGAS LIMA", "JOSÉ DE ALENCAR S/N", "CENTRO", "CRATO CE")),
                         ["nome", "endereço", "bairro", "cidade/uf"])
        self.assertEqual(self._cats(_env("JOSÉ CARLOS RIBEIRO", "RUI BARBOSA 455", "CENTRO", "SALVADOR BA")),
                         ["nome", "endereço", "bairro", "cidade/uf"])

    def test_linha_solta(self):
        self.assertEqual(self._cats(_env("GETÚLIO VARGAS", y0=700)), [None])  # homenagem (e município), não cliente
        self.assertEqual(self._cats(_env("MARECHAL DEODORO DA FONSECA", y0=700)), [None])
        self.assertEqual(self._cats(_env("JOSÉ CARLOS RIBEIRO", y0=700)), ["nome"])
        self.assertEqual(self._cats(_env("JOÃO BATISTA", "RUA SÃO JOSÉ, 50")), ["nome", "endereço"])  # acima da rua


if __name__ == "__main__":
    unittest.main()
