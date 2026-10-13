"""O operador humano (rotulador.humano): os hábitos sorteados por versão nas frequências do FSTS, a seleção que uma
pessoa faz (retângulo, laço, pincel, só a tinta) sem pegar a tinta vizinha, a suavização do Photoshop, o pincel de
recuperação, o conta-gotas, o acabamento sem a perícia automática e o "Salvar como" do editor aplicado ao PAR."""
import random
import unittest
from collections import Counter

import numpy as np

from rotulador import canal, gerador, humano
from rotulador.ops import executar, texto
from rotulador.ops.apagar import reconstruir
from rotulador.selecao import vazia
from rotulador.sessao import ErroOperacao
from tests.test_auto import conta

CAIXA = [330, 290, 560, 318]  # "30123-456" (CEP) na conta sintética de tests.test_auto
CAMPO = {"id": "t2", "caixa": CAIXA, "texto": "30123-456", "categoria": "cep", "tipo": "texto"}
IDS = {"nome": 1, "cpf/cnpj": 2, "endereço": 3, "bairro": 4, "cep": 5, "cidade/uf": 6, "data": 7,
       "mês de referência": 8, "valor": 9, "consumo/leitura": 10, "codigo barras": 11, "linha digitável": 12,
       "identificador documento": 13, "emissor/logotipo": 14, "qr code/pix": 15, "outro": 16}


def habito(**forca):
    h = humano.sortear(random.Random(3), {"prob": 1.0, "salvar": "png"}, True)
    h.update(forca)
    return h


def aplicar(img, tecnica, hab=None, semente=1):
    p = {"tecnica": tecnica, "campo": CAMPO, "semente": semente, "opcoes": {}, "conferir_ocr": False,
         "acabamento": {"ruido": "auto", "disfarce": "nunca", "jpeg": 0}}
    if hab is not None:
        p["habitos"] = {"humano": hab}
    return executar("tecnica", img, p, vazia(*img.shape[:2]), base=img)


class TestSorteio(unittest.TestCase):
    def test_deterministico_e_desligavel(self):
        a = humano.sortear(random.Random(9), {"prob": 1.0}, True)
        self.assertEqual(a, humano.sortear(random.Random(9), {"prob": 1.0}, True))
        self.assertIsNone(humano.sortear(random.Random(9), None, True))
        self.assertIsNone(humano.sortear(random.Random(9), {"prob": 0}, True))
        self.assertIsNone(humano.normalizar(False))
        with self.assertRaises(ValueError):
            humano.normalizar("sim")

    def test_frequencias_do_fsts(self):
        hs = [humano.sortear(random.Random(i), {"prob": 1.0}, True) for i in range(3000)]
        ap = Counter(h["apagar"] for h in hs)
        self.assertAlmostEqual(ap["patchmatch"] / 3000, 0.617, delta=0.04)  # Content-Aware Fill
        self.assertAlmostEqual(Counter(h["cor"] for h in hs)["conta_gotas"] / 3000, 0.884, delta=0.03)
        self.assertAlmostEqual(sum("ruido" in h["retoques"] for h in hs) / 3000, 0.105, delta=0.03)
        # origem sem JPEG: nunca "as mesmas tabelas" (não há tabela de origem)
        outros = Counter(humano.sortear(random.Random(i), {"prob": 1.0}, False)["salvar"] for i in range(500))
        self.assertNotIn("mesmas", outros)
        self.assertTrue(all("gravacao" in h for h in hs if h["salvar"] == "jpeg_editor"))


class TestSelecao(unittest.TestCase):
    def setUp(self):
        self.tinta = np.zeros((60, 200), bool)
        self.velho = np.zeros((60, 200), bool)
        self.velho[25:35, 50:150] = True  # o texto antigo
        self.tinta |= self.velho
        self.tinta[5:12, 50:120] = True  # o rótulo logo acima
        self.tinta[27:33, 156:160] = True  # um "," colado depois do texto

    def test_todas_contem_o_texto_antigo_e_poupam_o_rotulo(self):
        for forma in ("retangulo", "laco", "pincel", "tinta"):
            sel = humano.selecionar(self.velho, self.tinta, forma, (0.3,) * 4, 10.0, random.Random(1))
            self.assertTrue(sel[self.velho].all(), forma)
            self.assertFalse(sel[5:12, 50:120].any(), forma)
        self.assertTrue((humano.selecionar(self.velho, self.tinta, "tinta", (0.3,) * 4, 10.0, random.Random(1))
                         == self.velho).all())

    def test_retangulo_cresce_mas_para_antes_da_vizinha(self):
        sel = humano.selecionar(self.velho, self.tinta, "retangulo", (0.5, 3.0, 0.9, 0.5), 10.0, random.Random(1))
        ys, xs = np.nonzero(sel)
        self.assertLess(ys.min(), 25)  # cresceu para cima...
        self.assertGreater(ys.min(), 12)  # ...sem chegar no rótulo
        self.assertGreater(sel.sum(), self.velho.sum())

    def test_estrito_protege_a_virgula_colada(self):
        sel = humano.selecionar(self.velho, self.tinta, "pincel", (0.9,) * 4, 10.0, random.Random(1), estrito=True)
        self.assertFalse(sel[27:33, 156:160].any())


class TestPecas(unittest.TestCase):
    def test_suavizacao_do_photoshop(self):
        base = {"texto": "RUA 12", "fonte": "Arial", "tamanho": 30, "x": 20, "y": 40}
        alfas = {r: texto.renderizar({**base, "rasterizador": r}, 80, 200)[4] for r in humano.RASTERIZADOR.values()}
        self.assertGreater(alfas["ps_forte"].sum(), alfas["ps_nitida"].sum())  # forte engorda o traço
        meio = {r: int(((a > 0.05) & (a < 0.95)).sum()) for r, a in alfas.items()}
        self.assertGreater(meio["ps_suave"], meio["ps_nitida"])  # suave espalha a borda
        self.assertEqual(meio["aliasado"], 0)

    def test_pincel_de_recuperacao(self):
        rng = np.random.default_rng(0)
        rec = np.clip(200 + rng.normal(0, 6, (80, 120, 1)) + np.linspace(0, 20, 120)[None, :, None], 0, 255)
        rec = np.repeat(rec, 3, 2).astype(np.uint8)
        buraco = np.zeros((80, 120), bool)
        buraco[30:50, 40:80] = True
        out = reconstruir(rec, buraco, "recuperar", 3, 20.0)
        dentro = out[buraco].astype(np.float32)
        self.assertLess(abs(dentro.mean() - rec[~buraco].astype(np.float32).mean()), 6)  # o tom do entorno
        self.assertGreater(dentro.std(), 2.5)  # e textura de papel, não mancha lisa
        self.assertTrue((out[~buraco] == rec[~buraco]).all())

    def test_conta_gotas_pega_o_miolo(self):
        img = np.full((20, 40, 3), 245, np.uint8)
        tinta = np.zeros((20, 40), bool)
        tinta[8:12, 5:35] = True
        img[tinta] = 30
        img[8, 5:35] = 120  # borda clara do antialias
        for s in range(20):
            cor = humano.cor_conta_gotas(img, tinta, habito(cor="conta_gotas", amostra=5), random.Random(s))
            self.assertLess(float(cor.mean()), 40)


class TestTecnicaComOperador(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.img = conta()

    def test_selecao_e_rotulo(self):
        try:
            met = aplicar(self.img, "campo.mesma_fonte")
            hum = aplicar(self.img, "campo.mesma_fonte", habito(selecao="retangulo", apagar="patchmatch",
                                                                 margem=[0.3] * 4, retoques={}))
        except ErroOperacao as e:
            self.skipTest(f"fonte não identificada aqui: {e}")
        self.assertGreater(hum.pegada.sum(), met.pegada.sum())  # o letreiro, não a forma das letras
        f = hum.meta["fornecedores"]
        self.assertEqual(f["selecao"], "retangulo")
        self.assertEqual(f["grao"], "nenhum")  # ninguém põe grão espectral no editor
        self.assertIn("retângulo", hum.meta["humano"])
        self.assertFalse(any("perícia" in x for x in hum.meta["acabamento"]))

    def test_retoques_da_pessoa(self):
        h = habito(selecao="tinta", nitidez="manual", excesso=1.3,
                   retoques={"ruido": {"quantidade": 3.0, "distribuicao": "gaussiana", "monocromatico": True}})
        try:
            res = aplicar(self.img, "campo.mesma_fonte", h)
        except ErroOperacao as e:
            self.skipTest(f"fonte não identificada aqui: {e}")
        txt = " ".join(res.meta["acabamento"])
        self.assertIn("desfoque", txt)
        self.assertIn("ruído 3.0%", txt)
        self.assertEqual(res.meta["fornecedores"]["grao"], "ruido_do_editor")

    def test_trecho_com_operador(self):
        h = habito(selecao="retangulo", apagar="recuperar", margem=[0.2] * 4)
        try:
            res = aplicar(self.img, "caractere.redesenhar", h, semente=4)
        except ErroOperacao as e:
            self.skipTest(f"sem troca possível aqui: {e}")
        self.assertEqual(res.meta["fornecedores"].get("selecao"), "retangulo")
        self.assertTrue(res.meta["fornecedores"].get("texto", "").startswith(("ps_", "aliasado")))


class TestSalvarComoEditor(unittest.TestCase):
    def test_par_regravado_igual_fora_da_edicao(self):
        rng = np.random.default_rng(1)
        a = np.clip(rng.normal(200, 10, (128, 160, 3)), 0, 255).astype(np.uint8)
        b = a.copy()
        b[40:56, 48:96] = 20  # a edição
        regiao = np.zeros(a.shape[:2], bool)
        regiao[40:56, 48:96] = True
        for grav in ({"programa": "photoshop", "preset": "high"}, {"programa": "gimp", "qualidade": 90, "subsampling": 0}):
            theta = {"perfil": "editor", "semente": 1, "passos": [canal.passo_salvar_editor(grav)]}
            (oa, ob), _, _ = canal.aplicar([a, b], [regiao], [], theta, fase="depois")
            self.assertFalse((oa == a).all())  # a original também foi aberta e salva
            self.assertIsNone(canal.conferir_par(oa, ob, regiao, theta))
            self.assertIn("salva em JPEG pelo editor", canal.descrever(theta))

    def test_passo_entra_antes_do_envio(self):
        r = gerador.Receita({"tecnicas": {"campo.mesma_fonte": 1}, "categorias": {"nome": 1},
                             "humano": {"prob": 1.0, "salvar": "jpeg_editor"}}, IDS)
        ctx = type("C", (), {"jpeg": None})()
        hab, theta = gerador._humano_da_versao(r, ctx, None, random.Random(2))
        self.assertEqual(theta["passos"][0]["tipo"], "salvar_editor")
        envio = {"perfil": "whatsapp", "semente": 3, "passos": [{"tipo": "whatsapp", "lado": 1600, "jpeg": 75,
                                                                  "fase": "depois"}]}
        _, theta = gerador._humano_da_versao(r, ctx, envio, random.Random(2))
        self.assertEqual([p["tipo"] for p in theta["passos"]], ["salvar_editor", "whatsapp"])
        sem = gerador.Receita({"tecnicas": {"campo.mesma_fonte": 1}, "categorias": {"nome": 1}}, IDS)
        self.assertEqual(gerador._humano_da_versao(sem, ctx, None, random.Random(2)), (None, None))

    def test_perfil_profissional_liga_o_operador(self):
        d = gerador.receita_profissional(IDS)
        self.assertEqual(d["humano"], {"prob": gerador.HUMANO_PROB})
        self.assertEqual(gerador.Receita(d, IDS).humano["prob"], gerador.HUMANO_PROB)
        with self.assertRaises(ErroOperacao):
            gerador.Receita({**d, "humano": "sim"}, IDS)


class TestEstilosProntos(unittest.TestCase):
    def test_estilos_e_receitas(self):
        ids = [e["id"] for e in gerador.estilos()]
        self.assertEqual(ids[0], gerador.ESTILO_PADRAO)
        self.assertEqual(sum(e["padrao"] for e in gerador.estilos()), 1)
        for e in gerador.estilos():  # a tela precisa destes textos
            self.assertTrue(e["nome"] and e["resumo"] and e["faz"] and e["bom_para"], e["id"])
        perf = gerador.receita_profissional(IDS, estilo="perfeccionista")
        self.assertIsNone(perf["humano"])
        self.assertIsNone(gerador.Receita(perf, IDS).humano)
        at = gerador.Receita({"perfil": "profissional", "estilo": "atendimento"}, IDS)
        self.assertEqual(at.canal["perfil"], "envio")
        self.assertEqual(at.estilo, "atendimento")
        self.assertEqual(gerador.Receita({"perfil": "profissional", "estilo": "pessoas"}, IDS).humano["prob"], 1.0)
        # estilo desconhecido: o padrão
        self.assertEqual(gerador.receita_profissional(IDS, estilo="xyz")["estilo"], gerador.ESTILO_PADRAO)

    def test_envio_nunca_fotografa_nem_corta(self):
        cfg = canal.normalizar({"perfil": "envio", "prob": 1.0})
        for i in range(300):
            th = canal.sortear(random.Random(i), cfg)
            tipos = {p["tipo"] for p in th["passos"]}
            self.assertFalse(tipos & set(canal.FISICAS) or "recorte" in tipos, tipos)

    def test_descricao_em_portugues(self):
        h = habito(selecao="laco", apagar="recuperar", salvar="jpeg_editor",
                   gravacao={"programa": "photoshop", "preset": "high"}, retoques={"ruido": {"quantidade": 2.0}})
        d = humano.descrever(h)
        self.assertTrue(d.startswith("Contornou o texto com o laço") and d.endswith("."), d)
        self.assertIn("pincel de recuperação", d)
        self.assertIn("Photoshop", d)
        self.assertIn("pôs ruído", d)
        self.assertIn("perfeito", humano.descrever(None))


class TestSessaoDoEditor(unittest.TestCase):
    def test_mesma_pessoa_na_versao_e_sem_regravar(self):
        r = gerador.Receita({"perfil": "profissional", "estilo": "pessoas"}, IDS)
        jpeg = type("C", (), {"jpeg": {"modo": "RGB"}})()
        vistos = set()
        for i in range(60):
            s = type("S", (), {"historico_": []})()
            h = gerador.humano_da_sessao(s, r, jpeg, random.Random(i))
            self.assertNotEqual(h["salvar"], "jpeg_editor")  # sem o "Salvar como" no par aqui
            vistos.add(h["salvar"])
            s.historico_.append(object())  # já editou: a mesma pessoa na próxima edição
            self.assertIs(gerador.humano_da_sessao(s, r, jpeg, random.Random(i + 1000)), h)
        self.assertEqual(vistos, {"png", "mesmas"})
        sem = gerador.Receita({"perfil": "profissional", "estilo": "perfeccionista"}, IDS)
        self.assertIsNone(gerador.humano_da_sessao(type("S", (), {"historico_": []})(), sem, jpeg, random.Random(1)))


class TestDatasSoPeloCampoInteiro(unittest.TestCase):
    def test_data_so_campo_inteiro(self):
        r = gerador.Receita(gerador.receita_profissional(IDS, base=("nome", "endereço")), IDS)
        for cat, txt in (("data", "10/10/2026"), ("mês de referência", "SET/2026")):
            f = {"id": "d", "caixa": [0, 0, 100, 20], "texto": txt, "categoria": cat, "tipo": "texto"}
            servem = gerador._servem_profissional(f, r)
            self.assertEqual(set(servem), {"campo"}, cat)
        f = {"id": "v", "caixa": [0, 0, 100, 20], "texto": "R$ 151,37", "categoria": "valor", "tipo": "texto"}
        self.assertGreater(len(gerador._servem_profissional(f, r)), 1)  # o valor continua com trecho e caractere


if __name__ == "__main__":
    unittest.main()
