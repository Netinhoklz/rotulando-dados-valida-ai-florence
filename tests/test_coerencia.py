"""Coerência semântica (rotulador/coerencia.py), datas coerentes por rótulo no falsificador e o laudo em texto."""
import datetime as dt
import random
import unittest
from unittest import mock

from rotulador import coerencia, falsificador as fz, laudo
from rotulador.ops import codigos, geradores

HOJE = dt.date(2026, 10, 3)


def _campos(**troca):
    cod = codigos.arrecadacao(138457, segmento="2", empresa="3820", livre="1" * 25, ref="8")
    base = {
        "nome": "ANA PAULA FERREIRA COSTA", "cpf": "123.456.789-09", "cidade": "CURITIBA/PR", "cep": "80420-130",
        "bairro": "BATEL",
        "venc": "10/09/2026", "leit": "20/08/2026", "emis": "25/08/2026", "ref": "AGO/2026", "cons": "183 m³",
        "total": "R$ 1.384,57", "multa": "R$ 3,48", "linha": codigos.linha_arrecadacao(cod), "fone": "(41) 99876-5432",
    }
    base.update(troca)
    return [
        {"id": "t1", "categoria": "nome", "texto": base["nome"], "rotulo": "NOME"},
        {"id": "t2", "categoria": "cpf/cnpj", "texto": base["cpf"], "rotulo": "CPF"},
        {"id": "t3", "categoria": "cidade/uf", "texto": base["cidade"], "rotulo": "CIDADE/UF"},
        {"id": "t4", "categoria": "cep", "texto": base["cep"], "rotulo": "CEP"},
        {"id": "t5", "categoria": "data", "texto": base["venc"], "rotulo": "VENCIMENTO"},
        {"id": "t6", "categoria": "data", "texto": base["leit"], "rotulo": "DATA DA LEITURA"},
        {"id": "t7", "categoria": "data", "texto": base["emis"], "rotulo": "EMISSÃO"},
        {"id": "t8", "categoria": "mês de referência", "texto": base["ref"], "rotulo": "MÊS REFERÊNCIA"},
        {"id": "t9", "categoria": "consumo/leitura", "texto": base["cons"], "rotulo": "CONSUMO"},
        {"id": "t10", "categoria": "valor", "texto": base["total"], "rotulo": "TOTAL A PAGAR"},
        {"id": "t11", "categoria": "valor", "texto": base["multa"], "rotulo": "MULTA"},
        {"id": "t13", "categoria": "bairro", "texto": base["bairro"], "rotulo": "BAIRRO"},
        {"id": "t14", "categoria": "outro", "texto": base["fone"], "rotulo": "TELEFONE"},
        {"id": "t12", "categoria": "linha digitável", "texto": base["linha"], "rotulo": ""},  # a última (linha_ruim)
    ]


class TestRegras(unittest.TestCase):
    def test_documento_coerente_passa_em_tudo(self):
        r = coerencia.avaliar(_campos())
        self.assertEqual(r["violadas"], [], r["regras"])
        for nome in coerencia.REGRAS:
            self.assertTrue(r["regras"][nome]["ok"], nome)
        self.assertFalse(r["valor_redondo"])

    def test_cada_regra_pega_a_sua_violacao(self):
        casos = {"cep_cidade": {"cep": "01310-100"}, "cpf_cnpj": {"cpf": "123.456.789-00"},
                 "datas_ordenadas": {"leit": "20/09/2026"}, "mes_referencia": {"ref": "MAR/2026"},
                 "codigo_total": {"total": "R$ 2.000,00"}, "consumo_valor": {"cons": "5 m³"},
                 "vencimento_util": {"venc": "12/09/2026"}, "telefone_ddd": {"fone": "(85) 99876-5432"}}
        for regra, troca in casos.items():
            r = coerencia.avaliar(_campos(**troca))
            self.assertIn(regra, r["violadas"], (regra, r["regras"].get(regra)))
        r = coerencia.avaliar(_campos(venc="07/09/2026"))  # Independência (segunda-feira)
        self.assertIn("vencimento_util", r["violadas"])
        self.assertIn("feriado", r["regras"]["vencimento_util"]["detalhe"])
        self.assertIn("telefone_ddd", coerencia.avaliar(_campos(fone="(20) 99876-5432"))["violadas"])  # DDD inexistente
        self.assertIsNone(coerencia.avaliar(_campos(fone="0800 727 0196"))["regras"]["telefone_ddd"]["ok"])
        self.assertTrue(coerencia.avaliar(_campos(total="R$ 2.000,00"))["valor_redondo"])
        linha_ruim = _campos()[-1]["texto"][:-1] + ("0" if _campos()[-1]["texto"][-1] != "0" else "1")
        self.assertIn("linha_digitavel", coerencia.avaliar(_campos(linha=linha_ruim))["violadas"])

    def test_regra_sem_dado_nao_se_aplica(self):
        campos = [c for c in _campos() if c["categoria"] not in ("cep", "linha digitável", "consumo/leitura", "outro")]
        r = coerencia.avaliar(campos)
        for nome in ("cep_cidade", "linha_digitavel", "codigo_total", "consumo_valor", "telefone_ddd"):
            self.assertIsNone(r["regras"][nome]["ok"], nome)
        r = coerencia.avaliar([c for c in _campos() if c["rotulo"] != "VENCIMENTO"])
        self.assertIsNone(r["regras"]["vencimento_util"]["ok"])

    def test_datas_em_outros_formatos(self):
        """Antes só DD/MM/AAAA: "2026-09-10", "10 de setembro de 2026" e "Agosto/2026" ficavam fora das regras."""
        r = coerencia.avaliar(_campos(venc="10 de setembro de 2026", leit="2026-08-20", emis="25.08.26",
                                      ref="Agosto/2026"))
        for nome in ("datas_ordenadas", "mes_referencia", "vencimento_util"):
            self.assertTrue(r["regras"][nome]["ok"], (nome, r["regras"][nome]))
        r = coerencia.avaliar(_campos(venc="10 de setembro de 2026", leit="2026-09-20"))
        self.assertIn("datas_ordenadas", r["violadas"])
        self.assertIn("mes_referencia", coerencia.avaliar(_campos(ref="março de 2026"))["violadas"])
        self.assertEqual(coerencia.tipo_da_data("DATA DE NASCIMENTO"), "nascimento")
        self.assertEqual(coerencia.tipo_da_data("Data do documento"), "emissao")

    def test_tarifa_minima_e_milhar(self):
        """A conta mínima não é incoerente (casa vazia: 1 m³ paga a tarifa de 10 m³; 0 kWh paga o custo de
        disponibilidade e a iluminação pública) e "1.234 kWh" é mil duzentos e trinta e quatro (antes: 1)."""
        for cons, total in (("1 m³", "R$ 65,00"), ("0 kWh", "R$ 45,00"), ("1.234 kWh", "R$ 1.150,30")):
            r = coerencia.avaliar(_campos(cons=cons, total=total))
            self.assertTrue(r["regras"]["consumo_valor"]["ok"], (cons, r["regras"]["consumo_valor"]))
        self.assertTrue(coerencia.tarifa_ok(17122, 183, "KWH"))
        self.assertFalse(coerencia.tarifa_ok(171220, 2, "M3"))  # R$ 1.712,20 por 2 m³

    def test_boleto_tambem(self):
        cod = codigos.boleto("341", 123456, dt.date(2026, 9, 10), "1" * 25)
        campos = _campos(linha=codigos.linha_boleto(cod), total="R$ 1.234,56", cons="900 kWh")
        r = coerencia.avaliar(campos)
        self.assertTrue(r["regras"]["linha_digitavel"]["ok"])
        self.assertTrue(r["regras"]["codigo_total"]["ok"], r["regras"]["codigo_total"])


class TestComparar(unittest.TestCase):
    def test_edicao_cria_violacao_e_campos_finais(self):
        antes = _campos()
        edicoes = [{"campo": "t4", "novo": "01310-100"}, {"campo": "t11", "novo": ""}]  # CEP de SP numa conta de Curitiba; multa apagada
        depois = coerencia.campos_finais(antes, edicoes)
        self.assertEqual(len(depois), len(antes) - 1)
        self.assertEqual(next(c for c in depois if c["id"] == "t4")["texto"], "01310-100")
        c = coerencia.comparar(antes, depois)
        self.assertEqual(c["novas_violacoes"], ["cep_cidade"])
        self.assertFalse(c["sem_pista_semantica"])

    def test_edicao_coerente_fica_sem_pista(self):
        antes = _campos()
        depois = coerencia.campos_finais(antes, [{"campo": "t4", "texto_novo": "82010-520"}, {"campo": "t1", "novo": "JOAO LIMA"}])
        c = coerencia.comparar(antes, depois)
        self.assertTrue(c["sem_pista_semantica"], c["novas_violacoes"])

    def test_violacao_que_ja_existia_nao_conta(self):
        antes = _campos(cep="01310-100")  # o original já era incoerente
        depois = coerencia.campos_finais(antes, [{"campo": "t1", "novo": "JOAO LIMA"}])
        self.assertEqual(coerencia.comparar(antes, depois)["novas_violacoes"], [])


class TestDatasPorRotulo(unittest.TestCase):
    def test_recencia_respeita_a_ordem_das_datas(self):
        with mock.patch.object(fz, "HOJE", lambda: HOJE):
            for sem in range(30):
                v = fz._Valores(random.Random(sem), "treino", {})
                venc = dt.datetime.strptime(v.para("recencia", "data", "10/09/2026", "VENCIMENTO"), "%d/%m/%Y").date()
                leit = dt.datetime.strptime(v.para("recencia", "data", "20/08/2026", "DATA DA LEITURA"), "%d/%m/%Y").date()
                emis = dt.datetime.strptime(v.para("recencia", "data", "25/08/2026", "EMISSÃO"), "%d/%m/%Y").date()
                prox = dt.datetime.strptime(v.para("recencia", "data", "20/09/2026", "PRÓXIMA LEITURA"), "%d/%m/%Y").date()
                self.assertTrue(leit < emis < venc, (leit, emis, venc))
                self.assertTrue(28 <= (prox - leit).days <= 35, (leit, prox))
                self.assertTrue(3 <= (HOJE - venc).days <= 85)
                self.assertEqual(v.para("recencia", "data", "10/09/2026", "VENCIMENTO"), venc.strftime("%d/%m/%Y"))  # a mesma de novo


class TestLaudo(unittest.TestCase):
    def test_laudo_fala_de_cada_edicao_e_da_coerencia(self):
        anot = {"doc": "abc", "split": "treino", "versao": 1, "pixels_alterados": 1234,
                "falsificador": {"rotulo": "No celular", "motivo_rotulo": "Deixar a conta recente", "captura": "antes"},
                "captura_antes": "fotografada com celular", "canal": {"passos": []}, "canal_descricao": "fotografada com celular",
                "edicoes": [{"ordem": 1, "nome_tecnica": "Reescrever estilo amador", "tecnica": "campo.amador", "familia": "imitacao",
                             "nivel": "simples", "categoria": "data", "texto_antigo": "10/09/2026", "texto_novo": "01/10/2026",
                             "fornecedores": {"texto": "gdi", "fonte": "Arial"}, "pericia": "cru", "acabamento": [],
                             "perito": {"nota": 0.83, "medidas": {"grao": 0.1, "nitidez": 1.4, "tom": 3.0}}}],
                "coerencia": {"novas_violacoes": ["datas_ordenadas"], "regras_aplicaveis": ["datas_ordenadas", "cep_cidade"],
                              "sem_pista_semantica": False, "valor_redondo_novo": False}}
        t = laudo.gerar(anot)
        for trecho in ("Edição 1", "Reescrever estilo amador", "'10/09/2026' → '01/10/2026'", "texto=gdi", "nota 0.83",
                       "grão ×0.10", "datas fora de ordem", "ANTES da edição", "no celular"):
            self.assertIn(trecho, t, t)
        anot["coerencia"] = {"novas_violacoes": [], "regras_aplicaveis": ["cep_cidade"], "sem_pista_semantica": True}
        self.assertIn("Sem pista semântica nova", laudo.gerar(anot))


if __name__ == "__main__":
    unittest.main()
