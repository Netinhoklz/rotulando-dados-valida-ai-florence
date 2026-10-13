"""Tipos de edição (modo fácil do gerador): cada tipo junta um QUÊ (classes) e um COMO (técnicas);
só entram as combinações de algum tipo, o peso de cada tipo se divide entre as combinações dele, e
os jeitos de editar cobrem todas as técnicas automáticas."""
import json
import random
import shutil
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

from PIL import Image

from rotulador import catalogo
from rotulador.gerador import Plano, Receita
from rotulador.sessao import ErroOperacao
from tests.test_auto import IDS, TEM_OCR, conta

VALOR_CARACTERE = {"nome": "valor por caractere", "categorias": ["valor"],
                   "tecnicas": ["caractere.redesenhar", "caractere.transpor"], "peso": 4}
NOME_REESCRITO = {"nome": "nome reescrito", "categorias": ["nome", "endereço"], "tecnicas": ["campo.reescrever"], "peso": 1}


def campo(i, cat, texto, tipo="texto"):
    return {"id": f"t{i}", "categoria": cat, "texto": texto, "tipo": tipo, "caixa": [0, 40 * i, 200, 40 * i + 20]}


CAMPOS = [campo(0, "valor", "R$ 171,37"), campo(1, "nome", "JOAO LIMA"), campo(2, "endereço", "RUA A, 12"),
          campo(3, "data", "10/09/2026"), campo(4, "codigo barras", "(código)", "barras")]


class TestJeitos(unittest.TestCase):
    def test_cada_tecnica_automatica_num_jeito(self):
        ids = [tid for j in catalogo.JEITOS for tid in j["tecnicas"]]
        self.assertEqual(sorted(ids), sorted(t.id for t in catalogo.TECNICAS.values() if t.automatica))
        self.assertTrue(all(j["rotulo"] and j["icone"] and len(j["exemplo"]) == 2 for j in catalogo.JEITOS))

    def test_compativel(self):
        T = catalogo.TECNICAS
        self.assertTrue(catalogo.compativel(T["codigo.regenerar"], "codigo barras"))
        self.assertFalse(catalogo.compativel(T["codigo.regenerar"], "valor"))
        self.assertFalse(catalogo.compativel(T["campo.reescrever"], "codigo barras"))  # campo de imagem
        self.assertTrue(catalogo.compativel(T["campo.reescrever"], "linha digitável"))  # é texto
        self.assertFalse(catalogo.compativel(T["campo.acrescentar"], "valor"))  # restrito a nome, endereço...


class TestReceitaComTipos(unittest.TestCase):
    def test_pares_e_pesos_derivados(self):
        r = Receita({"tipos": [VALOR_CARACTERE, NOME_REESCRITO, {**NOME_REESCRITO, "ativo": False, "nome": "x"}],
                     "tecnicas": {"campo.amador": 1}}, IDS)  # com tipos, tecnicas/categorias vêm deles
        self.assertEqual(r.pares, {("caractere.redesenhar", "valor"): 2.0, ("caractere.transpor", "valor"): 2.0,
                                   ("campo.reescrever", "nome"): 0.5, ("campo.reescrever", "endereço"): 0.5})
        self.assertEqual(r.tecnicas, {"caractere.redesenhar": 2.0, "caractere.transpor": 2.0, "campo.reescrever": 1.0})
        self.assertEqual(set(r.categorias), {"valor", "nome", "endereço"})
        self.assertEqual([t["nome"] for t in r.resumo()["tipos"]], ["valor por caractere", "nome reescrito"])

    def test_combinacoes_impossiveis_ficam_de_fora(self):
        r = Receita({"tipos": [{"nome": "códigos", "categorias": ["valor", "codigo barras"],
                                "tecnicas": ["codigo.regenerar", "campo.reescrever"], "peso": 2}]}, IDS)
        self.assertEqual(r.pares, {("codigo.regenerar", "codigo barras"): 1.0, ("campo.reescrever", "valor"): 1.0})

    def test_erros(self):
        casos = [
            ([{"nome": "só código", "categorias": ["valor"], "tecnicas": ["codigo.regenerar"]}], "combinação possível"),
            ([{**NOME_REESCRITO, "ativo": False}], "ligue pelo menos um"),
            ([{"nome": "x", "categorias": ["valor"], "tecnicas": ["manual.pintura"]}], "técnica desconhecida"),
            ([{"nome": "x", "categorias": ["outro"], "tecnicas": ["campo.reescrever"]}], "categoria inválida"),
            ([{"nome": "vazio", "categorias": [], "tecnicas": ["campo.reescrever"]}], "pelo menos uma classe"),
            ({"nome": "não é lista"}, "use uma lista"),
        ]
        for tipos, msg in casos:
            with self.assertRaises(ErroOperacao) as cm:
                Receita({"tipos": tipos}, IDS)
            self.assertIn(msg, str(cm.exception), tipos)


class TestPlanoComTipos(unittest.TestCase):
    def test_so_combinacoes_dos_tipos(self):
        r = Receita({"tipos": [VALOR_CARACTERE, NOME_REESCRITO]}, IDS)
        pares = Plano(r, random.Random(1)).candidatos(CAMPOS)
        vistos = {(t.id, f["categoria"]) for t, f in pares}
        self.assertEqual(vistos, set(r.pares))  # nem data, nem código, nem valor reescrito

    def test_pesos_dos_tipos_valem_nas_duas_distribuicoes(self):
        for dist in ("pesos", "equilibrada"):
            r = Receita({"tipos": [VALOR_CARACTERE, NOME_REESCRITO], "distribuicao": dist}, IDS)
            plano = Plano(r, random.Random(7))
            pares = plano.candidatos(CAMPOS)
            conta_tipo = Counter()
            for _ in range(1000):
                t, f = plano.escolher(list(pares))
                plano.contar(t.id, f["categoria"])
                conta_tipo["valor" if f["categoria"] == "valor" else "nome"] += 1
            self.assertAlmostEqual(conta_tipo["valor"] / 1000, 0.8, delta=0.05, msg=dist)  # pesos 4 : 1


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestAPITipos(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import SPLITS, Registro
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            (self.dados / sp).mkdir(parents=True)
            Image.fromarray(conta(30 + i)).save(self.dados / sp / f"c{i}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_previa_geracao_e_exemplos(self):
        tipos = [{"nome": "cep e data por caractere", "categorias": ["cep", "data"],
                  "tecnicas": ["caractere.redesenhar"], "peso": 2}]
        cat = self.c.get("/api/catalogo").get_json()
        self.assertEqual([j["id"] for j in cat["jeitos"]], [j["id"] for j in catalogo.JEITOS])
        doc = self.reg.por_split["teste"][0].doc
        r = self.c.post("/api/gerador/previa", json={"doc": doc, "receita": {"tipos": tipos, "edicoes": [2, 2]}})
        self.assertEqual(r.status_code, 200, r.get_json())
        eds = r.get_json()["edicoes"]
        self.assertTrue(eds)
        self.assertTrue(all(e["tecnica"] == "caractere.redesenhar" and e["categoria"] in ("cep", "data") for e in eds), eds)
        self.assertEqual(self.c.get("/api/catalogo/exemplos").get_json(), {})  # nada gerado ainda
        rec = {"documentos": {"splits": ["validacao"]}, "variacoes": 1, "edicoes": [1, 2], "tipos": tipos}
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(240):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertEqual(st["feitas"], 1, st)
        anot = json.loads(next((self.saida / "validacao" / "anotacoes").glob("*.json")).read_text(encoding="utf-8"))
        self.assertTrue(all(e["tecnica"] == "caractere.redesenhar" for e in anot["edicoes"]))
        ex = self.c.get("/api/catalogo/exemplos").get_json()
        self.assertEqual(set(ex), {"caractere.redesenhar"})
        self.assertLessEqual({e["categoria"] for e in ex["caractere.redesenhar"]}, {"cep", "data"})
        e = ex["caractere.redesenhar"][0]
        self.assertEqual(e["split"], "validacao")
        self.assertEqual(len(e["bbox"]), 4)
        img = self.c.get(f"/api/catalogo/imagem/{e['split']}/{e['nome']}/editada.png?max=200&x0=0&y0=0&x1=300&y1=300")
        self.assertEqual(img.status_code, 200)
        img.close()


if __name__ == "__main__":
    unittest.main()
