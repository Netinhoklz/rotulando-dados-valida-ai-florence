"""O método de geração do FSTS (rotulador.fsts e o perfil "fsts" do gerador): as tabelas do artigo, a configuração de
cada região sorteada da população (eq. 8, sem o pós-processamento) e a versão inteira pelo lote, com a máscara = a
diferença e o tipo e as operações no rótulo."""

import json
import random
import unittest
from collections import Counter

import numpy as np
from openpyxl import load_workbook
from PIL import Image

from app import RAIZ, carregar_categorias
from rotulador import fsts, planilha
from rotulador.contexto import Contexto
from rotulador.gerador import TECNICAS_FSTS, Receita, _sobrepoe
from rotulador.sessao import ErroOperacao
from tests.test_planilha import BaseApp

IDS = {c["chave"]: c["id"] for c in carregar_categorias(RAIZ / "config" / "categorias.json")}


class TestTabelas(unittest.TestCase):
    def test_valores_do_artigo(self):
        """Alguns valores conferidos contra as tabelas 8–12 do apêndice B."""
        def freq(tipo, op):
            return next(p for fase in ("principal", "pos") for _, ops in fsts.TABELAS[tipo][fase]
                        for o, p, _ in ops if o == op)
        self.assertEqual(freq("replacement", "preenchimento"), 61.70)
        self.assertEqual(freq("removal", "recuperacao"), 12.45)
        self.assertEqual(freq("copy_move", "gaussiano"), 12.70)
        self.assertEqual(freq("splicing", "traco"), 8.75)
        self.assertEqual(freq("insertion", "ruido"), 16.54)
        self.assertEqual(freq("replacement", "rotacao"), 33.33)
        self.assertEqual(fsts.TABELAS["removal"]["pos"], [])

    def test_pos_ja_vem_multiplicado_por_0_3(self):
        """A figura 2 traz as frequências cruas da substituição; a tabela 12, as de síntese (×0,3)."""
        fig2 = {"gaussiano": 39.7, "ruido": 34.9, "brilho": 45.6, "agucar": 42.3}
        pos = {o: p for _, ops in fsts.TABELAS["replacement"]["pos"] for o, p, _ in ops}
        for op, cru in fig2.items():
            self.assertAlmostEqual(pos[op] / fsts.ESCALA_POS, cru, delta=0.1)

    def test_alternativas_exclusivas_somam_ate_100(self):
        for tipo, tab in fsts.TABELAS.items():
            for fase in ("principal", "pos"):
                for passo, ops in tab[fase]:
                    self.assertLessEqual(sum(p for _, p, _ in ops), 100.0 + 1e-6, (tipo, passo))
        for tipo in ("removal", "replacement"):  # a ferramenta de apagar: sempre uma das cinco
            self.assertAlmostEqual(sum(p for _, p, _ in fsts.TABELAS[tipo]["principal"][0][1]), 100.0, places=2)

    def test_pos_processamento_nao_e_aplicado(self):
        rng = random.Random(4)
        for tipo in fsts.TABELAS:
            for _ in range(40):
                cfg = fsts.configurar(tipo, rng)
                self.assertEqual(cfg["pos"], [])
                self.assertTrue(fsts.descrever(cfg).endswith("Pós-processamento: nenhum."))


class TestConfigurar(unittest.TestCase):
    """Eq. 8: cada região sorteia da população — a operação pela frequência da tabela, o parâmetro dentro da faixa."""

    def contar(self, tipo, n, semente=9):
        rng, c = random.Random(semente), Counter()
        cfgs = [fsts.configurar(tipo, rng) for _ in range(n)]
        for cfg in cfgs:
            c.update(list(cfg["principal"]))
        return c, cfgs

    def test_frequencias_realizadas_sao_as_da_tabela(self):
        for tipo, tab in fsts.TABELAS.items():
            n = 6000
            c, _ = self.contar(tipo, n)
            for passo, ops in tab["principal"]:
                for op, p, _ in ops:
                    self.assertAlmostEqual(100 * c[op] / n, p, delta=2.0, msg=(tipo, passo, op))

    def test_alternativas_do_mesmo_passo_nunca_juntas(self):
        _, cfgs = self.contar("replacement", 3000)
        self.assertFalse(any({"escala", "rotacao"} <= set(c["principal"]) for c in cfgs))  # 4.1: uma ou outra

    def test_parametros_variam_dentro_das_faixas(self):
        _, cfgs = self.contar("splicing", 3000)
        angulos = [c["principal"]["rotacao"]["angulo"] for c in cfgs if "rotacao" in c["principal"]]
        self.assertTrue(angulos and all(0.0 <= a <= 5.0 for a in angulos))
        self.assertGreater(len(set(angulos)), 100)  # contínuo, não um punhado de valores fixos
        tolerancias = {c["principal"]["varinha"]["tolerancia"] for c in cfgs if "varinha" in c["principal"]}
        self.assertTrue(tolerancias <= set(range(1, 51)) and len(tolerancias) > 30)

    def test_ferramenta_de_apagar(self):
        _, cfgs = self.contar("removal", 2000)
        self.assertTrue(all(c["apagar"] == fsts.DO_ARTIGO[next(o for o in c["principal"] if o in fsts.REMOCAO)]
                            for c in cfgs))
        _, cfgs = self.contar("copy_move", 4000)  # sem passo de apagar no artigo: pela frequência da substituição
        caf = sum(c["apagar"] == "patchmatch" for c in cfgs) / len(cfgs)
        self.assertAlmostEqual(caf, 0.617, delta=0.03)


class TestHabito(unittest.TestCase):
    def test_substituicao_e_remocao(self):
        cfg = {"tipo": "replacement", "pos": [], "apagar": "carimbo",
               "principal": {"carimbo": {}, "fonte": {"suavizacao": "forte"}, "cor_segura": {}, "rotacao": {"angulo": -3.2}}}
        h = fsts.habito_humano(cfg, (10, 10, 210, 40), random.Random(1))
        self.assertEqual((h["apagar"], h["antialias"], h["cor"], h["rotacao"], h["escala"]),
                         ("carimbo", "forte", "segura", -3.2, 1.0))
        cfg2 = {"tipo": "replacement", "pos": [], "apagar": "patchmatch", "principal": {"escala": {}}}
        self.assertTrue(0.95 <= fsts.habito_humano(cfg2, (10, 10, 210, 40), random.Random(2))["escala"] <= 1.05)
        # na remoção a "escala da região" é a folga da seleção, nunca menor que o texto
        cfg3 = {"tipo": "removal", "pos": [], "apagar": "patchmatch", "principal": {"preenchimento": {}, "escala": {}}}
        h3 = fsts.habito_humano(cfg3, (10, 10, 210, 40), random.Random(3))
        self.assertEqual((h3["apagar"], h3["escala"]), ("patchmatch", 1.0))
        self.assertTrue(all(m >= 0.1 for m in h3["margem"]))

    def test_colagem(self):
        self.assertFalse(fsts.escala_da_colagem({"fsts": {"tipo": "copy_move", "principal": {}}}))
        self.assertTrue(fsts.escala_da_colagem({}))
        self.assertEqual(fsts.rotacao_da_colagem({"fsts": {"tipo": "splicing", "principal": {"rotacao": {"angulo": 4.1}}}}), 4.1)
        self.assertEqual(fsts.rotacao_da_colagem({"fsts": {"tipo": "replacement", "principal": {"rotacao": {"angulo": 4.1}}}}), 0.0)
        self.assertEqual(fsts.extracao({"principal": {"canais": {}}}), "canais")


class TestReceita(unittest.TestCase):
    def test_perfil_fsts(self):
        r = Receita({"perfil": "fsts"}, IDS)
        self.assertEqual(r.perfil, "fsts")
        self.assertEqual(set(r.tecnicas), {t for g in TECNICAS_FSTS.values() for ts in g.values() for t in ts})
        self.assertFalse({"codigo barras", "qr code/pix", "outro"} & set(r.categorias))
        self.assertEqual(r.fsts["tipos"], {t: 1.0 for t in fsts.TIPOS})
        self.assertEqual(r.resumo()["fsts"], r.fsts)
        so = Receita({"perfil": "fsts", "fsts": {"tipos": {"removal": 2}}, "categorias": ["nome"], "variacoes": 3}, IDS)
        self.assertEqual((so.fsts["tipos"]["removal"], so.fsts["tipos"]["replacement"]), (2.0, 0.0))
        self.assertEqual((list(so.categorias), so.variacoes), (["nome"], 3))
        with self.assertRaises(ErroOperacao):
            Receita({"perfil": "fsts", "fsts": {"tipos": {"removal": 0}}}, IDS)


class TestVersao(BaseApp):
    def test_lote_com_o_metodo_fsts(self):
        """Versões pelo método do artigo no lote: as regiões têm o tipo, a máscara salva é a diferença, nenhuma região
        cai sobre o que outra edição tocou e o rótulo (anotação e Excel) diz o tipo e as operações."""
        it = self.doc("teste")
        self.app.gerador.iniciar({"perfil": "fsts", "variacoes": 2, "documentos": {"splits": ["teste"], "ids": [it.doc]}})
        self.app.gerador.thread.join(timeout=600)
        st = self.app.gerador.status()
        self.assertGreaterEqual(st["feitas"], 1, st.get("erros"))
        self.assertTrue(st.get("por_tipo_fsts"))
        base = self.reg.carregar(it.doc)
        caixa = {f["id"]: f["caixa"] for f in Contexto(it, base, self.saida, self.reg).campos}
        anots = [json.loads(p.read_text(encoding="utf-8")) for p in (self.saida / "teste" / "anotacoes").glob("*.json")]
        self.assertEqual(len(anots), st["feitas"])
        for anot in anots:
            self.assertEqual(anot["gerador"]["receita"]["perfil"], "fsts")
            arq = {k: self.saida / "teste" / v for k, v in anot["arquivos"].items()}
            orig = np.asarray(Image.open(arq["original"]).convert("RGB"))
            ed = np.asarray(Image.open(arq["editada"]).convert("RGB"))
            np.testing.assert_array_equal(np.asarray(Image.open(arq["mascara"])) == 1, (ed != orig).any(2))
            regs = anot["gerador"]["fsts"]["regioes"]
            self.assertEqual(len(regs), len(anot["edicoes"]))
            self.assertEqual(len({x["campo"] for x in regs}), len(regs))  # um campo, uma edição
            eds = sorted(anot["edicoes"], key=lambda e: e["ordem"])
            for i, e in enumerate(eds):  # o campo de cada região não encosta no que as anteriores tocaram
                self.assertIn(e["fsts"]["tipo"], fsts.TIPOS)
                self.assertEqual(e["fsts"]["pos"], [])
                self.assertIn("FSTS", e["descricao"])
                for ant in eds[:i]:
                    self.assertFalse(_sobrepoe(caixa[e["campo"]], ant["bbox"]), (e["campo"], ant["bbox"]))
        ws = load_workbook(self.saida / planilha.NOME)["Edições"]
        cab = [c.value for c in ws[1]]
        linhas = [dict(zip(cab, [c.value for c in row])) for row in ws.iter_rows(min_row=2)]
        self.assertTrue(linhas and all(l["Tipo FSTS"] for l in linhas))


if __name__ == "__main__":
    unittest.main()
