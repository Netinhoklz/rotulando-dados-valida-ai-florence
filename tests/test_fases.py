"""Canal em duas fases no gerador: a captura ANTES da edição vira a base da versão (os campos são levados pela
geometria, sem OCR de novo), a original gravada é a captura, as máscaras acompanham a imagem salva, e a data
de envio por documento mantém as datas falsas no ano do original."""
import datetime as dt
import json
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from rotulador import canal, gerador as mod_gerador
from rotulador.ops import geradores
from tests.test_auto import TEM_OCR, conta


class TestDataEnvio(unittest.TestCase):
    def test_depois_da_data_mais_recente_do_documento(self):
        class Ctx:
            campos = [{"categoria": "data", "texto": "10/03/2024"}, {"categoria": "data", "texto": "25/02/2024"},
                      {"categoria": "valor", "texto": "R$ 10,00"}]
        for sem in range(20):
            d = mod_gerador._data_envio(Ctx(), random.Random(sem))
            self.assertTrue(5 <= (d - dt.date(2024, 3, 10)).days <= 400, d)

    def test_sem_data_legivel_e_hoje(self):
        class Ctx:
            campos = [{"categoria": "data", "texto": "99/99/9999"}, {"categoria": "nome", "texto": "X"}]
        self.assertEqual(mod_gerador._data_envio(Ctx(), random.Random(1)), dt.date.today())

    def test_hoje_entra_no_falsificador(self):
        from rotulador import falsificador as fz
        with geradores.usando(hoje=dt.date(2024, 5, 20)):
            v = fz._Valores(random.Random(3), "treino", {})
            texto = v.para("recencia", "data", "10/01/2024")
        d = dt.datetime.strptime(texto, "%d/%m/%Y").date()
        self.assertTrue(3 <= (dt.date(2024, 5, 20) - d).days <= 85, texto)


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestLoteComCapturaAntes(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import SPLITS, Registro
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            (self.dados / sp).mkdir(parents=True)
            Image.fromarray(conta(60 + i)).save(self.dados / sp / f"c{i}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lote(self, receita):
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": receita}).status_code, 200)
        for _ in range(300):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        return st

    def test_captura_antes_vira_a_base_da_versao(self):
        rec = {"documentos": {"splits": ["validacao"]}, "variacoes": 2, "edicoes": [1, 2],
               "tecnicas": {"campo.amador": 1, "campo.corretivo": 1}, "categorias": {"valor": 1, "data": 1, "cep": 1},
               "acabamento": {"ruido": "nunca", "disfarce": "nunca"},
               "canal": {"perfil": "celular", "prob": 1.0, "captura": "antes"}, "falsificadores": {"prob": 0}}
        st = self._lote(rec)
        self.assertEqual(st["feitas"], 2, st)
        limpa = self.reg.carregar(self.reg.por_split["validacao"][0].doc)
        anots = [json.loads(a.read_text(encoding="utf-8")) for a in (self.saida / "validacao" / "anotacoes").glob("*.json")]
        self.assertEqual(len(anots), 2)
        for a in anots:
            passos = a["canal"]["passos"]
            self.assertTrue(any(p["tipo"] == "celular" and p.get("fase") == "antes" for p in passos), passos)
            self.assertIn("captura_antes", a)
            self.assertIn("__v", a["arquivos"]["original"])  # original própria da versão (é a foto)
            raiz = self.saida / "validacao"
            orig = np.asarray(Image.open(raiz / a["arquivos"]["original"]).convert("RGB"))
            ed = np.asarray(Image.open(raiz / a["arquivos"]["editada"]).convert("RGB"))
            self.assertEqual(orig.shape, ed.shape)
            self.assertNotEqual(orig.shape, limpa.shape)  # a foto tem outra geometria que o documento limpo
            for k in ("mascara", "mascara_regiao", "mascara_classes", "mascara_tecnicas", "mascara_ignorar"):
                m = np.asarray(Image.open(raiz / a["arquivos"][k]))
                self.assertEqual(m.shape[:2], ed.shape[:2], k)
            self.assertTrue(a["edicoes"], a.get("canal_descricao"))
            regiao = np.asarray(Image.open(raiz / a["arquivos"]["mascara_regiao"])) > 0
            dif = np.abs(orig.astype(int) - ed.astype(int)).max(2) > 0
            self.assertIsNone(canal.conferir_par(orig, ed, regiao, a["canal"]))  # fora da edição, o par é igual
            self.assertTrue((dif & regiao).any())
            self.assertIn("data_envio", a["gerador"])

    def test_captura_depois_continua_igual(self):
        rec = {"documentos": {"splits": ["validacao"]}, "variacoes": 1, "edicoes": [1, 1],
               "tecnicas": {"campo.amador": 1}, "categorias": {"valor": 1, "data": 1},
               "acabamento": {"ruido": "nunca", "disfarce": "nunca"},
               "canal": {"perfil": "scanner", "prob": 1.0, "captura": "depois"}, "falsificadores": {"prob": 0}}
        st = self._lote(rec)
        self.assertEqual(st["feitas"], 1, st)
        a = next(json.loads(x.read_text(encoding="utf-8")) for x in (self.saida / "validacao" / "anotacoes").glob("*.json"))
        self.assertTrue(all(p.get("fase", "depois") == "depois" for p in a["canal"]["passos"]))
        self.assertNotIn("captura_antes", a)


if __name__ == "__main__":
    unittest.main()
