"""A pasta das imagens escolhida na tela (subpastas treino, teste e validação, com ou sem acento) e o "Apagar a última
edição" do estúdio (a versão sai do dataset; a imagem de entrada fica)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import app as app_mod
from app import RAIZ
from rotulador import exportar
from rotulador.registro import SPLITS, Registro
from rotulador.sessao import ErroOperacao
from tests.test_estudio import NOMES, BaseEstudio, png_bytes
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
        img, _ = documento(nome="RITA DE CASSIA", semente=9)
        novo = reg.adicionar("nova.png", png_bytes(img))[0]  # o "Abrir" do estúdio: vai para a subpasta que existe
        self.assertTrue(novo.caminho.is_relative_to(reg.pastas[novo.split]))
        reg2 = Registro(self.dados, self.dados / "saida", log=lambda *_: None)  # e na partida seguinte continua lá
        self.assertIn(novo.doc, reg2.itens)

    def test_split_repetido_vira_aviso(self):
        (self.dados / "validacao").mkdir()
        reg = Registro(self.dados, self.dados / "saida", log=lambda *_: None)
        self.assertEqual(reg.pastas["validacao"].name, "validacao")  # a primeira em ordem de nome
        self.assertTrue(any("validação" in a and "repete" in a for a in reg.avisos), reg.avisos)


class TestApagarUltimaEdicao(BaseEstudio):
    def test_apaga_a_ultima_e_a_entrada_fica(self):
        t, v = self.doc("teste"), self.doc("treino")
        entrada = v.caminho.read_bytes()
        a = self.est.abrir(t.doc)
        r1 = self.est.salvar(a["arquivo"], png_bytes(self.apagar_trecho(t)[1]))
        b = self.est.abrir(v.doc)
        r2 = self.est.salvar(b["arquivo"], png_bytes(self.apagar_trecho(v)[1]))
        u = self.est.ultima()
        self.assertEqual((u["nome"], u["doc"], u["split"], u["versao"]), (r2["nome"], v.doc, "treino", 1))
        d = self.est.desfazer(u["nome"])
        self.assertEqual((d["doc"], d["documento"]), (v.doc, v.nome))  # a página pode voltar a essa imagem
        for rel in r2["arquivos"].values():  # editada, máscaras, anotação (e a cópia da original, que ficou sem versão)
            self.assertFalse((self.saida / "treino" / rel).exists(), rel)
        self.assertEqual(v.caminho.read_bytes(), entrada)  # a imagem de entrada, na pasta de dados, intacta
        self.assertTrue((self.saida / "teste" / r1["arquivos"]["mascara"]).is_file())  # a anterior fica
        self.assertEqual((d["ultima"]["nome"], self.est.ultima()["nome"]), (r1["nome"], r1["nome"]))
        nomes = {l["nome"] for l in exportar.ler_catalogo(self.saida)}
        self.assertEqual((r1["nome"] in nomes, r2["nome"] in nomes), (True, False))
        with self.assertRaises(ErroOperacao):  # já saiu
            self.est.desfazer(r2["nome"])
        r3 = self.est.salvar(b["arquivo"], png_bytes(self.apagar_trecho(v)[1]))  # a mesma abertura, salva de novo
        self.assertIsNone(r3["substituiu"])
        self.assertEqual(self.est.ultima()["nome"], r3["nome"])

    def test_rotas_e_o_que_ja_foi_feito_em_cada_imagem(self):
        it = self.doc("validacao")
        a = self.est.abrir(it.doc)
        r = self.est.salvar(a["arquivo"], png_bytes(self.apagar_trecho(it)[1]))
        docs = self.c.get("/api/gerador/documentos").get_json()
        d = next(x for x in docs["validacao"] if x["id"] == it.doc)
        self.assertEqual((d["versoes"], d["tipos"], d["campos"]), (1, ["Manual (editor)"], ["nome"]))
        self.assertEqual((docs["teste"][0]["tipos"], docs["teste"][0]["campos"]), ([], []))
        self.assertEqual(self.c.get("/api/estudio/ultima").get_json()["ultima"]["nome"], r["nome"])
        self.assertEqual(self.c.post("/api/estudio/desfazer", json={"nome": "0123456789abcdef__v01"}).status_code, 400)
        x = self.c.post("/api/estudio/desfazer", json={"nome": r["nome"]})
        self.assertEqual(x.status_code, 200, x.get_json())
        self.assertIsNone(x.get_json()["ultima"])
        d = next(x for x in self.c.get("/api/gerador/documentos").get_json()["validacao"] if x["id"] == it.doc)
        self.assertEqual((d["versoes"], d["tipos"]), (0, []))


class TestEscolherPasta(BaseEstudio):
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
