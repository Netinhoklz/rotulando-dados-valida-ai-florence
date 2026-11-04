"""A pasta das imagens (subpastas treino, teste e validação, com ou sem acento), a troca de pasta pelo servidor e o
que já foi feito em cada imagem (os tipos de edição e os campos, na lista de documentos)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import app as app_mod
from app import RAIZ
from rotulador.registro import SPLITS, Registro
from tests.test_planilha import NOMES, BaseApp
from tests.test_tecnicas import documento


class TestSubpastas(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados = self.tmp / "base"
        for i, (nome, sp) in enumerate((("Treino", "treino"), ("teste", "teste"), ("validação", "validacao"))):
            (self.dados / nome).mkdir(parents=True)
            img, _ = documento(nome=NOMES[sp], semente=i + 1)
            Image.fromarray(img).save(self.dados / nome / f"conta_{i}.png")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_validacao_com_acento_e_saida_dentro_da_pasta(self):
        reg = Registro(self.dados, self.dados / "saida", log=lambda *_: None)
        self.assertEqual({sp: len(reg.por_split[sp]) for sp in SPLITS}, {"treino": 1, "teste": 1, "validacao": 1})
        self.assertEqual((reg.pastas["validacao"].name, reg.pastas["treino"].name), ("validação", "Treino"))
        self.assertEqual(reg.por_split["validacao"][0].rel, "conta_2.png")
        self.assertFalse((self.dados / "validacao").exists())  # nenhuma pasta nova sem acento
        self.assertEqual(reg.avisos, [])  # a saida/ dentro da pasta de dados não é aviso

    def test_split_repetido_vira_aviso(self):
        (self.dados / "validacao").mkdir()
        reg = Registro(self.dados, self.dados / "saida", log=lambda *_: None)
        self.assertEqual(reg.pastas["validacao"].name, "validacao")  # a primeira em ordem de nome
        self.assertTrue(any("validação" in a and "repete" in a for a in reg.avisos), reg.avisos)


class TestFeitoEmCadaImagem(BaseApp):
    def test_tipos_e_campos_na_lista_de_documentos(self):
        it = self.doc("validacao")
        r = self.salvar_manual(it)
        docs = self.c.get("/api/gerador/documentos").get_json()
        d = next(x for x in docs["validacao"] if x["id"] == it.doc)
        self.assertEqual((d["versoes"], d["tipos"], d["campos"]), (1, ["Manual (editor)"], ["nome"]))
        self.assertEqual((docs["teste"][0]["tipos"], docs["teste"][0]["campos"]), ([], []))
        x = self.c.post("/api/catalogo/excluir", json={"split": "validacao", "nome": f"{it.doc}__v{r['versao']:02d}"})
        self.assertEqual(x.status_code, 200, x.get_json())
        d = next(x for x in self.c.get("/api/gerador/documentos").get_json()["validacao"] if x["id"] == it.doc)
        self.assertEqual((d["versoes"], d["tipos"]), (0, []))


class TestEscolherPasta(BaseApp):
    def test_listar_e_usar(self):
        p = self.c.get("/api/pasta").get_json()
        self.assertEqual((p["dados"], p["pode_trocar"]), (str(self.dados.resolve()), False))  # criar_app sozinho
        l = self.c.get("/api/pasta/listar", query_string={"caminho": str(self.tmp)}).get_json()
        self.assertIn("dados", [x["nome"] for x in l["pastas"]])
        self.assertEqual(l["splits"], {"treino": None, "teste": None, "validacao": None})
        self.assertEqual(l["pai"], str(self.tmp.resolve().parent))
        l = self.c.get("/api/pasta/listar").get_json()  # sem caminho: a pasta de dados
        self.assertEqual(l["splits"], {"treino": "treino", "teste": "teste", "validacao": "validacao"})
        longe = {"REMOTE_ADDR": "10.0.0.5"}  # de outro computador: não mostra as pastas do servidor
        self.assertEqual(self.c.get("/api/pasta/listar", environ_base=longe).status_code, 400)
        self.assertEqual(self.c.post("/api/pasta/usar", json={"caminho": str(self.dados)}, environ_base=longe).status_code, 400)
        r = self.c.post("/api/pasta/usar", json={"caminho": str(self.dados)})
        self.assertIn("rode pelo app.py", r.get_json()["erro"])
        chamadas = []
        self.app.config["TROCAR_PASTA"] = lambda p: chamadas.append(p) or {"dados": str(p)}
        self.assertTrue(self.c.get("/api/pasta").get_json()["pode_trocar"])
        r = self.c.post("/api/pasta/usar", json={"caminho": str(self.tmp)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("falta: treino, teste, validação", r.get_json()["erro"])
        r = self.c.post("/api/pasta/usar", json={"caminho": str(self.dados)})
        self.assertEqual((r.status_code, chamadas), (200, [self.dados.resolve()]))

    def test_saida_de_cada_pasta_e_a_escolha_lembrada(self):
        t = self.tmp.resolve()
        with mock.patch.object(app_mod, "PASTA_ESCOLHIDA", t / "pasta_escolhida.json"):
            a = mock.Mock(raiz_dados=t / "a", raiz_saida=t / "saida_a")
            self.assertEqual(app_mod.saida_da_pasta(t / "a", a), t / "saida_a")
            self.assertEqual(app_mod.saida_da_pasta(t / "b", a), t / "b" / "saida")  # pasta nova: saida/ dentro dela
            self.assertEqual(app_mod.saida_da_pasta((RAIZ / "dados").resolve(), a), RAIZ / "saida")
            b = mock.Mock(raiz_dados=t / "b", raiz_saida=t / "b" / "saida")
            app_mod.lembrar_pasta(a, b)
            m = app_mod.pasta_lembrada()
            self.assertEqual((m["dados"], m["saida"]), (str(t / "b"), str(t / "b" / "saida")))
            self.assertEqual(app_mod.saida_da_pasta(t / "a", b), t / "saida_a")  # voltar: a saída de antes

    def test_despachante_troca_o_app(self):
        from werkzeug.test import Client
        d = app_mod.Despachante()
        d.app = self.app
        self.app.config["TROCAR_PASTA"] = lambda p: {}
        local = {"REMOTE_ADDR": "127.0.0.1"}
        self.assertTrue(Client(d).get("/api/pasta", environ_base=local).get_json()["pode_trocar"])
        d.app = app_mod.criar_app(self.reg, self.saida, app_mod.carregar_categorias(RAIZ / "config" / "categorias.json"))
        self.assertFalse(Client(d).get("/api/pasta", environ_base=local).get_json()["pode_trocar"])  # já o app novo


if __name__ == "__main__":
    unittest.main()
