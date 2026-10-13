"""Datas e números falsos realistas (rotulador/ops/geradores.py e rotulador/coerencia.py): datas em todos os formatos
de conta e com o papel de cada uma (vencimento em dia útil, emissão, leitura, próxima leitura, referência) coerentes
entre si e perto do ``hoje`` do documento; valores no formato e na ordem de grandeza do original, por tipo de conta e
coerentes com o consumo; telefone com DDD da cidade; CPF mascarado, RG, e-mail; identificadores com dígito
verificador; código de barras que acompanha o total. As distribuições são medidas em milhares de sorteios com semente
fixa."""
import collections
import datetime as dt
import math
import random
import re
import time
import unittest

from rotulador import coerencia
from rotulador.ops import codigos, geradores as g

HOJE = dt.date(2026, 10, 3)
N = 3000


def _forma(s: str) -> str:
    """O formato do texto: dígitos viram 9 (o resto fica)."""
    return re.sub(r"\d", "9", s)


def _sorteios(categoria, antigo, n=N, semente=1, **kw):
    with g.usando(random.Random(semente), hoje=HOJE):
        return [g.parecido(categoria, antigo, **kw) for _ in range(n)]


def _entropia(xs) -> float:
    c = collections.Counter(xs)
    return -sum(v / len(xs) * math.log2(v / len(xs)) for v in c.values())


class TestCalendario(unittest.TestCase):
    """Feriados nacionais fixos e móveis (Carnaval, Sexta-feira Santa, Corpus Christi) e dia útil."""

    def test_feriados(self):
        f26 = g.feriados(2026)
        for d in ((1, 1), (2, 16), (2, 17), (4, 3), (4, 21), (5, 1), (6, 4), (9, 7), (10, 12), (11, 2), (11, 15),
                  (11, 20), (12, 25)):
            self.assertIn(dt.date(2026, *d), f26, d)
        f25 = g.feriados(2025)
        for d in ((3, 3), (3, 4), (4, 18), (6, 19)):
            self.assertIn(dt.date(2025, *d), f25, d)
        self.assertNotIn(dt.date(2023, 11, 20), g.feriados(2023))  # Consciência Negra só a partir de 2024
        self.assertFalse(g.dia_util(dt.date(2026, 10, 3)))  # sábado
        self.assertFalse(g.dia_util(dt.date(2026, 10, 12)))  # Aparecida (segunda)
        self.assertTrue(g.dia_util(dt.date(2026, 10, 13)))
        self.assertEqual(g.proximo_dia_util(dt.date(2026, 10, 10)), dt.date(2026, 10, 13))  # sáb → (seg feriado) → ter


class TestFormatosDeData(unittest.TestCase):
    """Item A: a data nova sai no formato EXATO da antiga (antes: 1/2/2026 virava 0/0/5259, '10 de outubro de 2026'
    virava '77 de outubro de 3274', 2026-10-10 virava 2017-10-25)."""

    D = dt.date(2026, 9, 4)

    def test_formatar_data(self):
        casos = {"10/10/2026": "04/09/2026", "10/10/26": "04/09/26", "1/2/2026": "4/9/2026", "10.10.2026": "04.09.2026",
                 "10-10-2026": "04-09-2026", "2026-10-10": "2026-09-04",
                 "10 de outubro de 2026": "4 de setembro de 2026", "05 de outubro de 2026": "04 de setembro de 2026",
                 "10 OUT 2026": "04 SET 2026", "10/OUT/2026": "04/SET/2026", "10/out/26": "04/set/26",
                 "10 DE OUTUBRO DE 2026": "4 DE SETEMBRO DE 2026",
                 "Venc: 10/10/2026": "Venc: 04/09/2026", "10-Out-2026": "04-Set-2026", "15/09": "04/09"}
        for antigo, esperado in casos.items():
            self.assertEqual(g.formatar_data(antigo, self.D), esperado, antigo)
        self.assertIsNone(g.formatar_data("SET/2026", self.D))
        self.assertIsNone(g.formatar_data("R$ 10,00", self.D))

    def test_marco_com_e_sem_acento(self):
        marco = dt.date(2026, 3, 9)
        self.assertEqual(g.formatar_data("10 de outubro de 2026", marco), "9 de marco de 2026")  # o padrão é sem acento
        with g.usando(acentos=True):
            self.assertEqual(g.formatar_data("10 de outubro de 2026", marco), "9 de março de 2026")
        self.assertEqual(g.formatar_data("10 de maio de 2026", marco), "9 de marco de 2026")
        self.assertEqual(g.formatar_data("05 de março de 2026", dt.date(2026, 4, 2)), "02 de abril de 2026")
        self.assertEqual(g.formatar_data("05 de abril de 2026", marco), "09 de marco de 2026")
        self.assertEqual(g.formatar_data("05 de Abril de 2026", marco), "09 de Marco de 2026")
        self.assertEqual(g.formatar_data("05 de abril de 2026 às 14h", marco), "09 de março de 2026 às 14h")  # acentua
        self.assertEqual(g.formatar_mes("OUTUBRO/2026", marco), "MARCO/2026")
        self.assertEqual(g.formatar_mes("MARÇO/2026", dt.date(2026, 3, 1)), "MARÇO/2026")
        self.assertEqual(g.formatar_mes("MARCO/2026", dt.date(2026, 3, 1)), "MARCO/2026")  # o antigo não acentua

    def test_formatar_mes(self):
        d = dt.date(2026, 9, 1)
        casos = {"OUT/2026": "SET/2026", "Out/26": "Set/26", "out/2026": "set/2026", "10/2026": "09/2026",
                 "10/26": "09/26", "1/2026": "9/2026", "OUTUBRO/2026": "SETEMBRO/2026", "Outubro/2026": "Setembro/2026",
                 "outubro de 2026": "setembro de 2026", "OUT 2026": "SET 2026", "2026-10": "2026-09",
                 "REF: OUT/2026": "REF: SET/2026", "Outubro": "Setembro", "OUT./26": "SET./26"}
        for antigo, esperado in casos.items():
            self.assertEqual(g.formatar_mes(antigo, d), esperado, antigo)
        self.assertIsNone(g.formatar_mes("R$ 10,00", d))

    def test_ler_data_e_mes(self):
        for texto, d in (("10/10/2026", (2026, 10, 10)), ("1/2/26", (2026, 2, 1)), ("2026-10-09", (2026, 10, 9)),
                         ("9 de março de 2026", (2026, 3, 9)), ("10 OUT 2026", (2026, 10, 10)), ("31/02/2026", None),
                         ("15/09", None), ("SET/2026", None)):
            self.assertEqual(g.ler_data(texto), dt.date(*d) if d else None, texto)
        for texto, m in (("SET/2026", (2026, 9)), ("set/26", (2026, 9)), ("Setembro de 2026", (2026, 9)),
                         ("09/2026", (2026, 9)), ("9/26", (2026, 9)), ("MARÇO/2026", (2026, 3)), ("13/2026", None)):
            self.assertEqual(g.ler_mes(texto), m, texto)

    def test_parecido_mantem_o_formato_e_a_data_existe(self):
        antigos = ("10/10/2026", "10/10/26", "1/2/2026", "10.10.2026", "10-10-2026", "2026-10-10",
                   "10 de outubro de 2026", "10 OUT 2026", "10/OUT/2026", "10/out/2026", "Venc: 10/10/2026",
                   "10 DE OUTUBRO DE 2026")
        rx = {"10 de outubro de 2026": r"^\d{1,2} de [a-z]+ de \d{4}$", "10 OUT 2026": r"^\d{2} [A-Z]{3} \d{4}$",
              "10/OUT/2026": r"^\d{2}/[A-Z]{3}/\d{4}$", "10/out/2026": r"^\d{2}/[a-z]{3}/\d{4}$",
              "10 DE OUTUBRO DE 2026": r"^\d{1,2} DE [A-Z]+ DE \d{4}$", "1/2/2026": r"^\d{1,2}/\d{1,2}/\d{4}$"}
        for antigo in antigos:
            novos = _sorteios("data", antigo, 500)
            for n in novos:
                if antigo in rx:
                    self.assertRegex(n, rx[antigo], antigo)
                else:
                    self.assertEqual(_forma(n), _forma(antigo), (antigo, n))  # antes: 0% válidas por extenso
                d = g.ler_data(n)
                self.assertIsNotNone(d, n)
                self.assertTrue(-10 <= (HOJE - d).days <= 440, (antigo, n))  # perto do hoje do documento
            self.assertNotIn(antigo, novos)

    def test_mes_parecido_mantem_o_formato(self):
        for antigo, rx in (("OUT/2026", r"^[A-Z]{3}/\d{4}$"), ("Out/26", r"^[A-Z][a-z]{2}/\d{2}$"),
                           ("10/2026", r"^\d{2}/\d{4}$"), ("10/26", r"^\d{2}/\d{2}$"),
                           ("OUTUBRO/2026", r"^[A-Z]+/\d{4}$"), ("outubro de 2026", r"^[a-z]+ de \d{4}$")):
            novos = _sorteios("mês de referência", antigo, 500)
            for n in novos:
                self.assertRegex(n, rx, antigo)  # antes: OUTUNOV/2025, 'outubro de 0530', 67/13
                ano, mes = g.ler_mes(n)
                self.assertTrue(0 <= (HOJE.year * 12 + HOJE.month) - (ano * 12 + mes) <= 16, n)
            self.assertNotIn(antigo, novos)


class TestSemanticaDasDatas(unittest.TestCase):
    """Item A: o papel de cada data (vencimento em dia útil e dia fixo, emissão, leitura, próxima leitura,
    referência) e a coerência entre as datas."""

    def test_vencimento_em_dia_util_e_dia_tipico(self):
        novos = [g.ler_data(x) for x in _sorteios("data", "10/09/2026", rotulo="VENCIMENTO")]
        self.assertEqual([d for d in novos if not g.dia_util(d)], [])  # antes: 28,7% em fim de semana
        dias = collections.Counter(d.day for d in novos)
        mesmo_dia = dias[10] + dias[11] + dias[12] + dias[13]  # o dia fixo do cliente (empurrado para o dia útil)
        self.assertGreater(mesmo_dia / len(novos), 0.6)
        with g.usando(random.Random(4), hoje=HOJE):
            ciclos = [g.ciclo_de_datas() for _ in range(N)]
        dias = collections.Counter(c["vencimento"].day for c in ciclos)
        tipicos = sum(dias[d] + dias[d + 1] + dias[d + 2] for d in (5, 10, 15, 20, 25))  # + o empurrão do sábado
        self.assertGreater(tipicos / N, 0.6)
        self.assertGreater(len(dias), 20)  # mas os outros dias também aparecem
        perto = sum(-10 <= (HOJE - c["vencimento"]).days <= 90 for c in ciclos) / N
        self.assertGreater(perto, 0.7)  # a conta "recente" da regra dos 90 dias domina
        self.assertTrue(all(-10 <= (HOJE - c["vencimento"]).days <= 440 for c in ciclos))

    def test_ciclo_coerente(self):
        with g.usando(random.Random(5), hoje=HOJE):
            ciclos = [g.ciclo_de_datas() for _ in range(N)]
        for c in ciclos:
            ve, em, le, pr = c["vencimento"], c["emissao"], c["leitura"], c["proxima_leitura"]
            self.assertTrue(3 <= (ve - em).days <= 15, c)
            self.assertTrue(3 <= (em - le).days <= 10, c)
            self.assertTrue(28 <= (pr - le).days <= 33, c)
            self.assertTrue(28 <= (le - c["leitura_anterior"]).days <= 33, c)
            self.assertTrue(g.dia_util(ve) and g.dia_util(em), c)
            self.assertTrue(le.weekday() < 6 and pr.weekday() < 6, c)  # leitura de segunda a sábado
            self.assertLessEqual(em, HOJE)
            self.assertLessEqual(c["pagamento"], HOJE)
            self.assertTrue(0 <= (ve - c["pagamento"]).days <= 5 or ve > HOJE, c)
            meses = (le.year * 12 + le.month) - (c["referencia"].year * 12 + c["referencia"].month)
            self.assertIn(meses, (0, 1), c)
            self.assertEqual(c["referencia"].day, 1)
        mesmo_mes = sum(c["referencia"].month == c["leitura"].month for c in ciclos) / N
        self.assertTrue(0.55 < mesmo_mes < 0.85, mesmo_mes)

    def test_ciclo_repete_as_distancias_do_original(self):
        base = {"vencimento": dt.date(2026, 3, 16), "emissao": dt.date(2026, 3, 6), "leitura": dt.date(2026, 3, 6),
                "referencia": dt.date(2026, 2, 1)}  # emite no dia da leitura; a referência é o mês anterior
        with g.usando(random.Random(6), hoje=HOJE):
            ciclos = [g.ciclo_de_datas(base=base) for _ in range(500)]
        self.assertGreater(sum(c["emissao"] == c["leitura"] for c in ciclos) / 500, 0.8)
        self.assertGreater(sum(abs((c["vencimento"] - c["emissao"]).days - 10) <= 2 for c in ciclos) / 500, 0.9)
        self.assertGreater(sum(c["vencimento"].day in (16, 17, 18) for c in ciclos) / 500, 0.6)
        mes_antes = sum(g._meses(c["leitura"]) - g._meses(c["referencia"]) == 1 for c in ciclos)
        self.assertGreater(mes_antes / 500, 0.9)
        self.assertTrue(all(c["deslocamento"] == c["vencimento"] - base["vencimento"] for c in ciclos))

    def test_papel_pelo_rotulo_ou_pelo_texto(self):
        for rotulo, papel in (("VENCIMENTO", "vencimento"), ("DATA DE EMISSÃO", "emissao"),
                              ("DATA DA LEITURA", "leitura"), ("PRÓXIMA LEITURA", "proxima_leitura"),
                              ("Data de nascimento", "nascimento"), ("DATA DO DOCUMENTO", "emissao"),
                              ("Data do pagamento", "pagamento"), ("", "outra")):
            self.assertEqual(coerencia.tipo_da_data(rotulo), papel, rotulo)
        datas = [g.ler_data(x) for x in _sorteios("data", "Venc: 10/09/2026", 500)]
        self.assertFalse([d for d in datas if not g.dia_util(d)])  # "Venc:" no próprio texto
        nasc = [g.ler_data(x) for x in _sorteios("data", "12/05/1980", 1000, rotulo="DATA DE NASCIMENTO")]
        idades = [(HOJE - d).days / 365.25 for d in nasc]
        self.assertTrue(all(17.9 < i < 91 for i in idades))
        self.assertTrue(30 < sorted(idades)[500] < 55)

    def test_emissao_leitura_nunca_depois_de_hoje(self):
        for rotulo in ("EMISSÃO", "LEITURA", ""):
            for n in _sorteios("data", "10/09/2026", 1000, rotulo=rotulo):
                self.assertLessEqual(g.ler_data(n), HOJE, (rotulo, n))

    def test_datas_trocadas_na_versao_ficam_coerentes(self):
        """Com ``usando(documento=...)``: cada data acha o papel pelo rótulo do campo e todas saem do MESMO ciclo novo;
        as datas trocadas ficam coerentes entre si e com o mês de referência (antes: cada uma sorteada à parte)."""
        ruins = 0
        for sem in range(300):
            antes = _campos()
            with g.usando(random.Random(sem), hoje=HOJE, documento=antes):
                novos = {f["id"]: g.parecido(f["categoria"], f["texto"]) for f in antes
                         if f["categoria"] in ("data", "mês de referência")}
                novos2 = {f["id"]: g.parecido(f["categoria"], f["texto"]) for f in antes
                          if f["categoria"] in ("data", "mês de referência")}
            self.assertEqual(novos, novos2)  # a mesma data nova a versão inteira
            depois = coerencia.campos_finais(antes, [{"campo": k, "novo": v} for k, v in novos.items()])
            c = coerencia.comparar(antes, depois)
            ruins += bool(set(c["novas_violacoes"]) & {"datas_ordenadas", "mes_referencia", "vencimento_util"})
            ve = g.ler_data(next(f["texto"] for f in depois if f["id"] == "t5"))
            self.assertNotEqual(ve, dt.date(2026, 9, 10))
        self.assertEqual(ruins, 0)

    def test_uma_data_so_conserva_o_dia_do_cliente(self):
        dias = collections.Counter()
        for sem in range(500):
            antes = _campos(venc="15/09/2026")
            with g.usando(random.Random(sem), hoje=HOJE, documento=antes):
                dias[g.ler_data(g.parecido("data", "15/09/2026")).day] += 1
        self.assertGreater((dias[15] + dias[16] + dias[17]) / 500, 0.6)


def _campos(**troca):
    cod = codigos.arrecadacao(138457, segmento="2", empresa="3820", livre="1" * 25, ref="8")
    base = {"venc": "10/09/2026", "leit": "20/08/2026", "emis": "25/08/2026", "prox": "21/09/2026", "ref": "AGO/2026",
            "cons": "183 m³", "total": "R$ 1.384,57", "linha": codigos.linha_arrecadacao(cod),
            "fone": "(41) 99876-5432", "cidade": "CURITIBA/PR"}
    base.update(troca)
    return [
        {"id": "t1", "categoria": "nome", "texto": "ANA PAULA FERREIRA COSTA", "rotulo": "NOME"},
        {"id": "t3", "categoria": "cidade/uf", "texto": base["cidade"], "rotulo": "CIDADE/UF"},
        {"id": "t5", "categoria": "data", "texto": base["venc"], "rotulo": "VENCIMENTO"},
        {"id": "t6", "categoria": "data", "texto": base["leit"], "rotulo": "DATA DA LEITURA"},
        {"id": "t7", "categoria": "data", "texto": base["emis"], "rotulo": "EMISSÃO"},
        {"id": "t14", "categoria": "data", "texto": base["prox"], "rotulo": "PRÓXIMA LEITURA"},
        {"id": "t8", "categoria": "mês de referência", "texto": base["ref"], "rotulo": "MÊS REFERÊNCIA"},
        {"id": "t9", "categoria": "consumo/leitura", "texto": base["cons"], "rotulo": "CONSUMO"},
        {"id": "t10", "categoria": "valor", "texto": base["total"], "rotulo": "TOTAL A PAGAR"},
        {"id": "t15", "categoria": "outro", "texto": base["fone"], "rotulo": "TELEFONE"},
        {"id": "t12", "categoria": "linha digitável", "texto": base["linha"], "rotulo": ""},
    ]


class TestValores(unittest.TestCase):
    """Item B: o valor novo no formato exato, na ordem de grandeza e com centavos plausíveis."""

    def test_formatos(self):
        casos = (("R$ 1.234,56", r"^R\$ \d{1,3}(\.\d{3})*,\d{2}$"), ("R$1.234,56", r"^R\$\d{1,3}(\.\d{3})*,\d{2}$"),
                 ("1.234,56", r"^\d{1,3}(\.\d{3})*,\d{2}$"), ("1234,56", r"^\d+,\d{2}$"),
                 ("R$ 85,00", r"^R\$ \d{1,3}(\.\d{3})*,\d{2}$"),
                 ("TOTAL R$ 151,37", r"^TOTAL R\$ \d{1,3}(\.\d{3})*,\d{2}$"),
                 ("-R$ 3,48", r"^-R\$ \d+,\d{2}$"), ("R$ -3,48", r"^R\$ -\d+,\d{2}$"))
        for antigo, rx in casos:
            for n in _sorteios("valor", antigo, 500):
                self.assertRegex(n, rx, antigo)  # antes: "1234,56" → "3.367,19"
                self.assertNotEqual(n, antigo)
        for n in _sorteios("valor", "R$ ***1.234,56", 500):  # o preenchimento de asteriscos mantém a largura
            self.assertEqual(len(n), len("R$ ***1.234,56"), n)
            self.assertRegex(n, r"^R\$ \*+[\d.]+,\d{2}$")

    def test_ordem_de_grandeza(self):
        for antigo in ("R$ 85,00", "R$ 151,37", "R$ 1.234,56", "R$ 23,87"):
            v0 = g.valor_em_centavos(antigo)
            novos = [g.valor_em_centavos(n) for n in _sorteios("valor", antigo)]
            mesmo = sum(len(str(v // 100)) == len(str(v0 // 100)) for v in novos) / len(novos)
            self.assertGreater(mesmo, 0.8, antigo)  # antes: 34% em R$ 85,00
            self.assertTrue(all(0.4 <= v / v0 <= 2.3 for v in novos), antigo)
            self.assertNotIn(v0, novos)

    def test_centavos_no_estilo_do_antigo(self):
        redondos = sum(n.endswith(",00") for n in _sorteios("valor", "R$ 85,00")) / N
        self.assertTrue(0.5 < redondos < 0.9, redondos)  # taxa fixa continua redonda na maioria (antes 1%)
        tabela = sum(n[-3:] in (",90", ",99") for n in _sorteios("valor", "R$ 99,90")) / N
        self.assertGreater(tabela, 0.6)  # plano de telefone continua ,90/,99
        quebrados = sum(n.endswith(",00") for n in _sorteios("valor", "R$ 151,37")) / N
        self.assertLess(quebrados, 0.03)

    def test_fator_continua_exato(self):
        self.assertEqual(g.parecido("valor", "R$ 100,00", fator=2.0), "R$ 200,00")
        self.assertEqual(g.parecido("valor", "R$ 1.000,00", fator=0.5), "R$ 500,00")
        self.assertEqual(g.parecido("valor", "1000,00", fator=2.0), "2000,00")

    def test_conta_por_tipo(self):
        with g.usando(random.Random(7), hoje=HOJE):
            luz = [g.conta("luz") for _ in range(N)]
            agua = [g.conta("agua") for _ in range(N)]
            fone = [g.conta("telefone") for _ in range(N)]
            todas = [g.conta() for _ in range(N)]
        kwh = sorted(c["consumo"] for c in luz)
        self.assertTrue(80 <= kwh[N // 10] and kwh[9 * N // 10] <= 600, (kwh[N // 10], kwh[9 * N // 10]))
        for c in luz:
            self.assertEqual(c["unidade"], "kWh")
            self.assertTrue(coerencia.tarifa_ok(c["centavos"], c["consumo"], "KWH"), c)
        m3 = sorted(c["consumo"] for c in agua)
        self.assertTrue(5 <= m3[N // 10] and m3[9 * N // 10] <= 40, (m3[N // 10], m3[9 * N // 10]))
        for c in agua:
            self.assertTrue(coerencia.tarifa_ok(c["centavos"], c["consumo"], "M3"), c)
            self.assertGreaterEqual(c["centavos"], 3500)  # tarifa mínima
        self.assertGreater(sum(c["centavos"] % 100 in (90, 99) for c in fone) / N, 0.6)
        self.assertLess(sum(c["centavos"] % 100 == 0 for c in todas) / N, 0.05)
        tipos = collections.Counter(c["tipo"] for c in todas)
        self.assertGreater(tipos["luz"], tipos["agua"])
        self.assertGreater(tipos["agua"], tipos["gas"])
        mediana = sorted(c["centavos"] for c in todas)[N // 2] / 100
        self.assertTrue(80 < mediana < 260, mediana)

    def test_total_coerente_com_o_consumo_do_documento(self):
        for cons, total in (("183 kWh", "R$ 171,22"), ("12 m³", "R$ 98,40"), ("1.234 kWh", "R$ 1.150,30")):
            violou = 0
            for sem in range(300):
                antes = _campos(cons=cons, total=total)
                with g.usando(random.Random(sem), hoje=HOJE, documento=antes):
                    novo = g.parecido("valor", total)
                depois = coerencia.campos_finais(antes, [{"campo": "t10", "novo": novo}])
                violou += "consumo_valor" in coerencia.comparar(antes, depois)["novas_violacoes"]
            self.assertEqual(violou, 0, cons)

    def test_valor_e_consumo_da_versao_andam_juntos(self):
        for sem in range(200):
            antes = _campos(cons="183 kWh", total="R$ 171,22")
            with g.usando(random.Random(sem), hoje=HOJE, documento=antes):
                cons = g.parecido("consumo/leitura", "183 kWh")
                total = g.parecido("valor", "R$ 171,22")
                self.assertEqual(g.parecido("valor", "TOTAL: R$ 171,22"), "TOTAL: " + total)  # o mesmo total no canhoto
            r = g.valor_em_centavos(total) / 17122 / (int(cons.split()[0]) / 183)
            self.assertTrue(0.85 < r < 1.15, (cons, total))

    def test_numero_br(self):
        for texto, v in (("1.234", 1234), ("1.234,56", 1234.56), ("45,6", 45.6), ("85.00", 85.0), ("R$ 12,30", 12.3),
                         ("-3,48", -3.48), ("183", 183)):
            self.assertAlmostEqual(g._numero_br(texto), v, msg=texto)


class TestConsumo(unittest.TestCase):
    def test_formatos(self):
        for antigo, rx in (("183 kWh", r"^\d+ kWh$"), ("12 m³", r"^\d+ m³$"), ("1.234 kWh", r"^\d{1,3}(\.\d{3})* kWh$"),
                           ("45,6 m³", r"^\d+,\d m³$"), ("CONSUMO: 0183 kWh", r"^CONSUMO: \d{4} kWh$")):
            novos = _sorteios("consumo/leitura", antigo, 500)
            for n in novos:
                self.assertRegex(n, rx, antigo)  # antes: "1.234 kWh" → "1 kWh"; "45,6 m³" → "37 m³"
            self.assertNotIn(antigo, novos)
        v = [g._numero_br(n) for n in _sorteios("consumo/leitura", "1.234 kWh", 500)]
        self.assertTrue(all(500 < x < 2800 for x in v))
        for n in _sorteios("consumo/leitura", "AGO/26 183", 300):  # histórico: o ano fica, o consumo muda
            self.assertTrue(n.startswith("AGO/26 "), n)
        self.assertEqual(g.parecido("consumo/leitura", "200 kWh", fator=1.5), "300 kWh")

    def test_gerar(self):
        with g.usando(random.Random(8), hoje=HOJE):
            xs = [g.gerar("consumo/leitura")["texto"] for _ in range(N)]
        kwh = [int(x.split()[0]) for x in xs if x.endswith("kWh")]
        m3 = [int(x.split()[0]) for x in xs if x.endswith("m³")]
        self.assertTrue(0.45 < len(kwh) / N < 0.75)
        self.assertTrue(80 <= sorted(kwh)[len(kwh) // 10] and sorted(kwh)[9 * len(kwh) // 10] <= 600)
        self.assertTrue(sorted(m3)[9 * len(m3) // 10] <= 45)


class TestCodigos(unittest.TestCase):
    """Item D: código de barras e linha digitável acompanham o valor (o do original, ou o total novo da versão)."""

    def test_linha_mantem_empresa_valor_e_formato(self):
        cod = codigos.arrecadacao(138457, segmento="2", empresa="3820", livre="1" * 25, ref="8")
        linha = codigos.linha_arrecadacao(cod)
        sem_hifen = linha.replace("-", "")
        for antigo in (linha, sem_hifen, cod):
            for n in _sorteios("linha digitável" if antigo != cod else "codigo barras", antigo, 200):
                self.assertEqual(_forma(n), _forma(antigo))  # a pontuação do original
                c = g.codigo_da_linha(n)
                self.assertTrue(codigos.valida_arrecadacao(c), n)
                # segmento, ref., empresa e valor do original (antes: valor e empresa sorteados)
                self.assertEqual((c[1], c[2], c[15:19], int(c[4:15])), ("2", "8", "3820", 138457))
                self.assertNotEqual(c, cod)
        bol = codigos.boleto("341", 18740, dt.date(2026, 9, 10), "1" * 25)
        for n in _sorteios("linha digitável", codigos.linha_boleto(bol), 200):
            c = g.codigo_da_linha(n)
            self.assertTrue(codigos.valida_boleto(c))
            self.assertEqual((c[:3], int(c[9:19]), c[5:9]), ("341", 18740, bol[5:9]))  # banco, valor e vencimento

    def test_codigo_acompanha_o_total_novo_da_versao(self):
        for sem in range(100):
            antes = _campos(total="R$ 1.384,57")
            with g.usando(random.Random(sem), hoje=HOJE, documento=antes):
                total = g.parecido("valor", "R$ 1.384,57")
                linha = g.parecido("linha digitável", antes[-1]["texto"])
            depois = coerencia.campos_finais(antes, [{"campo": "t10", "novo": total}, {"campo": "t12", "novo": linha}])
            self.assertNotIn("codigo_total", coerencia.comparar(antes, depois)["novas_violacoes"])
            self.assertEqual(int(g.codigo_da_linha(linha)[4:15]), g.valor_em_centavos(total))


class TestTelefone(unittest.TestCase):
    """Item C: DDD da cidade, celular 9 + 9[6-9], fixo começando com 2–5, 0800/4004 com o prefixo, no molde."""

    def test_ddd(self):
        self.assertEqual(g.ddd_de("FORTALEZA", "CE"), "85")
        self.assertEqual(g.ddd_de("Sobral", "CE"), "88")
        self.assertEqual(g.ddd_de("CAMPINAS", "SP"), "19")
        self.assertEqual(g.ddd_de("CAMPO GRANDE", "MS"), "67")
        for c, uf in g._CIDADES:
            self.assertIn(g.ddd_de(c, uf), g.DDDS_UF[uf], c)
        with g.usando(random.Random(9)):
            outros = collections.Counter(g.ddd_de("LUGAR NENHUM", "SP") for _ in range(2000))
        self.assertEqual(outros.most_common(1)[0][0], "11")
        self.assertLessEqual(set(outros), set(g.DDDS_UF["SP"]))
        self.assertIsNone(g.ddd_de("X", "XX"))
        self.assertEqual(len({d for ds in g.DDDS_UF.values() for d in ds}), 67)

    def test_formatos_e_tipo(self):
        for antigo in ("(85) 99876-5432", "85 99876-5432", "(85)3456-7890", "+55 85 99876-5432", "Tel: (085) 3456-7890",
                       "0800 727 0196", "4004-0001", "(85) 998765432"):
            novos = _sorteios("outro", antigo, 500)
            for n in novos:
                self.assertEqual(_forma(n), _forma(antigo), (antigo, n))  # antes: (57) 52311-4280, 7357 379 1971
            self.assertNotIn(antigo, novos)
        for n in _sorteios("outro", "(85) 99876-5432", 1000):
            self.assertRegex(n, r"^\(\d\d\) 9[6-9]\d{3}-\d{4}$")
        for n in _sorteios("outro", "(85)3456-7890", 1000):
            self.assertRegex(n, r"^\(\d\d\)[2-5]\d{3}-\d{4}$")
        self.assertTrue(all(n.startswith("0800 ") for n in _sorteios("outro", "0800 727 0196", 200)))
        self.assertTrue(all(n.startswith("4004-") for n in _sorteios("outro", "4004-0001", 200)))

    def test_ddd_da_cidade_do_documento(self):
        with g.usando(random.Random(10), hoje=HOJE, cidade=("CURITIBA", "PR")):
            ddds = collections.Counter(g.parecido("outro", "(85) 99876-5432")[1:3] for _ in range(1000))
            gerados = collections.Counter(g.gerar("telefone")["texto"][1:3] for _ in range(1000))
        self.assertEqual(set(ddds), {"41"})  # Curitiba: 41 (antes: qualquer número, até DDD inexistente)
        self.assertEqual(set(gerados), {"41"})
        sem_cidade = collections.Counter(n[1:3] for n in _sorteios("outro", "(85) 99876-5432", 1000))
        self.assertGreater(sem_cidade["85"] / 1000, 0.75)  # sem a cidade, o DDD do antigo (a região do documento)
        self.assertLessEqual(set(sem_cidade), set(g.DDDS_UF["CE"]))


class TestDocumentos(unittest.TestCase):
    """Item D: CPF mascarado, região fiscal do CPF, RG com órgão/UF e DV, e-mail realista, identificador com DV."""

    def test_cpf_mascarado_e_formatos(self):
        for antigo in ("***.456.789-**", "123.456.789-09", "12345678909", "CPF: 123.456.789-09", "XXX.456.789-XX"):
            novos = _sorteios("cpf/cnpj", antigo, 300)
            for n in novos:
                self.assertEqual(_forma(n), _forma(antigo), (antigo, n))  # antes: ***.356.754.158-74-**
            self.assertNotIn(antigo, novos)
        for n in _sorteios("cpf/cnpj", "123.456.789-09", 300):
            self.assertTrue(g.valida_cpf(n))

    def test_regiao_fiscal_do_cpf(self):
        with g.usando(random.Random(11), cidade=("FORTALEZA", "CE")):
            regioes = collections.Counter(g.cpf()[10] for _ in range(N))
        self.assertGreater(regioes["3"] / N, 0.7)  # CE/MA/PI = 3 (antes 10%)
        mesmos = sum(n[10] == "8" for n in _sorteios("cpf/cnpj", "123.456.788-09", 1000)) / 1000
        self.assertGreater(mesmos, 0.6)  # a região do original

    def test_rg(self):
        self.assertEqual(g._dv_rg_sp("12345678"), "9")
        for antigo, rx in (("12.345.678-9 SSP/SP", r"^\d{2}\.\d{3}\.\d{3}-[\dX] SSP/SP$"),
                           ("MG-12.345.678", r"^MG-\d{2}\.\d{3}\.\d{3}$"), ("RG: 1234567", r"^RG: \d{7}$")):
            cat = "cpf/cnpj" if antigo != "RG: 1234567" else "outro"
            novos = _sorteios(cat, antigo, 300)
            for n in novos:
                self.assertRegex(n, rx, antigo)  # antes: o RG virava CPF ("542.765.875-06 SSP/SP")
            self.assertNotIn(antigo, novos)
        for n in _sorteios("cpf/cnpj", "12.345.678-9 SSP/SP", 300):  # o DV de SP do antigo confere: o novo também
            corpo = re.sub(r"\D", "", n.split("-")[0])
            self.assertEqual(n.split("-")[1][0], g._dv_rg_sp(corpo), n)

    def test_email(self):
        with g.usando(random.Random(12)):
            emails = [g.email() for _ in range(5000)]
        for e in emails:
            self.assertRegex(e, r"^[a-z0-9._]+@[a-z0-9.]+\.[a-z]{2,3}(\.br)?$")
        dominios = collections.Counter(e.split("@")[1] for e in emails)
        hot = dominios["hotmail.com"] + dominios["outlook.com"] + dominios["live.com"] + dominios["hotmail.com.br"]
        yahoo = dominios["yahoo.com.br"] + dominios["yahoo.com"]
        nacionais = [dominios[d] for d in ("bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br")]
        self.assertGreater(dominios["gmail.com"], 2 * hot)
        self.assertGreater(hot, yahoo)
        self.assertGreater(yahoo, max(nacionais))
        corporativos = sum(d not in g._WEBMAIL for d in dominios.elements()) / 5000
        self.assertTrue(0.005 < corporativos < 0.06, corporativos)
        self.assertGreater(len(set(emails)), 4900)
        for antigo, rx in (("maria.silva@gmail.com", r"^[a-z]+\.[a-z]+@[a-z.]+$"),
                           ("JOAO_SOUZA1985@HOTMAIL.COM", r"^[A-Z]+_[A-Z]+\d{4}@[A-Z.]+$"),
                           ("contato@enel.com.br", r"^[a-z]+@enel\.com\.br$")):
            for n in _sorteios("outro", antigo, 300):
                self.assertRegex(n, rx, antigo)  # antes: o e-mail virava um nome ("caetano silva")

    def test_identificador_com_dv(self):
        mod10, mod11, mod11x = g._DVS  # 1234567-4 só confere por módulo 10; 1234567-9 por módulo 11; 1234561-X só com X
        for antigo, calc in (("1234567-4", mod10), ("1234567-9", mod11), ("1234561-X", mod11x), ("1234561-0", mod11)):
            self.assertEqual(calc(antigo[:7]), antigo[-1])
            for n in _sorteios("identificador documento", antigo, 300):
                c, dv = n.split("-")
                self.assertEqual(len(c), 7)
                self.assertEqual(dv, calc(c), (antigo, n))  # antes: DV sorteado
        for n in _sorteios("identificador documento", "UC 0012.345-6 / 7788", 300):
            self.assertRegex(n, r"^UC 00[1-9]\d\.\d{3}-[\dX] / [1-9]\d{3}$")

    def test_mesmo_numero_na_versao(self):
        antes = _campos()
        with g.usando(random.Random(13), documento=antes):
            a = g.parecido("identificador documento", "0012345678")
            self.assertEqual(g.parecido("identificador documento", "Nº 0012345678"), "Nº " + a)
            c = g.parecido("cpf/cnpj", "123.456.789-09")
            self.assertEqual(g.parecido("cpf/cnpj", "12345678909"), c.replace(".", "").replace("-", ""))


class TestDiversidadeEVelocidade(unittest.TestCase):
    """Item E: variedade (entropia) em milhares de sorteios e o custo de cada sorteio."""

    def test_entropia(self):
        with g.usando(random.Random(14), hoje=HOJE):
            # data: concentrada nos dias úteis dos últimos meses (a regra dos 90 dias), não espalhada por 540 dias
            # corridos como antes (9,0 bits, 29% em fim de semana); a variedade entre documentos vem do hoje de cada um
            # valor: a conta de telefone repete o preço do plano (R$ 99,90), como na vida real
            minimos = {"data": 7.0, "mês de referência": 3.5, "valor": 10.5, "consumo/leitura": 7.5,
                       "identificador documento": 11.5, "telefone": 11.5, "e-mail": 11.5}
            for cat, minimo in minimos.items():
                xs = [g.gerar(cat)["texto"] for _ in range(N)]
                self.assertGreater(_entropia(xs), minimo, cat)

    def test_rapido(self):
        casos = (("data", "10 de outubro de 2026"), ("data", "10/10/2026"), ("mês de referência", "OUT/2026"),
                 ("valor", "R$ 1.234,56"), ("consumo/leitura", "183 kWh"), ("cpf/cnpj", "123.456.789-09"),
                 ("outro", "(85) 99876-5432"), ("outro", "maria.silva@gmail.com"),
                 ("identificador documento", "1234567-8"), ("linha digitável", _campos()[-1]["texto"]))
        with g.usando(random.Random(15), hoje=HOJE, documento=_campos()):
            for cat, antigo in casos:
                t0 = time.perf_counter()
                for _ in range(200):
                    g.parecido(cat, antigo)
                self.assertLess((time.perf_counter() - t0) / 200, 0.003, cat)

    def test_mesma_semente_mesmo_valor(self):
        def rodada():
            with g.usando(random.Random(16), hoje=HOJE, documento=_campos()):
                casos = (("data", "10/09/2026"), ("valor", "R$ 1.384,57"), ("outro", "(85) 99876-5432"),
                         ("outro", "maria.silva@gmail.com"), ("cpf/cnpj", "***.456.789-**"))
                return ([g.parecido(c, a) for c, a in casos]
                        + [g.gerar(c)["texto"] for c in ("data", "valor", "telefone", "e-mail")])
        self.assertEqual(rodada(), rodada())


if __name__ == "__main__":
    unittest.main()
