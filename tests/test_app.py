import base64
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from app import RAIZ, carregar_categorias, criar_app
from rotulador.registro import SPLITS, Registro
from tests.util import conta, salvar

RET = {"formas": [{"tipo": "ret", "x0": 14, "y0": 192, "x1": 200, "y1": 214}]}
PINCEL = {"pontos": [[100, 300], [200, 300]], "tamanho": 12, "cor": "#ff0000"}


class TestApp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            salvar(conta(10 + i), self.dados / sp / f"doc_{sp}.png")
            salvar(conta(20 + i), self.dados / sp / f"outro_{sp}.jpg", quality=92)
        self.novo_app()

    def novo_app(self):
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.app = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json"))
        self.c = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def doc(self, split, i=0):
        return self.reg.por_split[split][i].doc

    def post(self, rota, **dados):
        return self.c.post(rota, data=json.dumps(dados), content_type="application/json")

    def op(self, doc, tipo="pincel", params=PINCEL, categoria="valor", **kw):
        return self.post("/api/op", id=doc, tipo=tipo, params=params, categoria=categoria, **kw)

    def arquivos(self, split):
        base = self.saida / split
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()
                      and "_rascunhos" not in p.parts) if base.exists() else []

    # ------------------------------------------------------------------
    def test_estado_e_listas(self):
        e = self.c.get("/api/estado").get_json()
        self.assertEqual(e["splits"]["treino"]["total"], 2)
        self.assertIn("valor", [c["chave"] for c in e["categorias"]])
        lista = self.c.get("/api/documentos?split=teste").get_json()
        self.assertEqual({d["id"] for d in lista}, {i.doc for i in self.reg.por_split["teste"]})
        self.assertEqual(self.c.get("/api/documentos?split=xyz").status_code, 400)

    def test_fluxo_completo_salva_so_no_split(self):
        doc = self.doc("teste")
        self.assertEqual(self.post("/api/abrir", id=doc).status_code, 200)
        r = self.op(doc, categoria=None)
        self.assertEqual(r.status_code, 400)  # sem categoria não aplica
        self.assertEqual(self.post("/api/salvar", id=doc).status_code, 400)  # nada editado
        r = self.op(doc)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["estado"]["pode_salvar"])
        r = self.op(doc, tipo="substituir_texto", selecao=RET,
                    params={"texto": "VENC 01/01/2027", "x": 14, "y": 210, "tamanho": 16}, categoria="data")
        self.assertEqual(r.status_code, 200, r.get_json())
        r = self.post("/api/salvar", id=doc, formato="png", operador="teste")
        self.assertEqual(r.status_code, 200, r.get_json())
        salvo = r.get_json()["salvo"]
        self.assertEqual(salvo["split"], "teste")
        self.assertEqual(set(salvo["categorias"]), {"valor", "data"})
        # nada fora de saida/teste (só o cache de miniaturas/registro na raiz)
        self.assertEqual(self.arquivos("treino"), [])
        self.assertEqual(self.arquivos("validacao"), [])
        arqs = self.arquivos("teste")
        self.assertIn(f"originais/{doc}.png", arqs)
        self.assertIn(f"editadas/{doc}__v01.png", arqs)
        self.assertIn("manifesto.csv", arqs)
        base = self.saida / "teste"
        orig = np.asarray(Image.open(base / f"originais/{doc}.png"))
        edit = np.asarray(Image.open(base / f"editadas/{doc}__v01.png"))
        masc = np.asarray(Image.open(base / f"mascaras/{doc}__v01.png"))
        cls = np.asarray(Image.open(base / f"mascaras_classes/{doc}__v01.png"))
        np.testing.assert_array_equal(orig, self.reg.carregar(doc))
        np.testing.assert_array_equal(masc > 0, (orig != edit).any(2))  # PNG: diff exato
        self.assertEqual(set(np.unique(cls)) - {0}, {7, 9})  # data, valor
        anot = json.loads((base / f"anotacoes/{doc}__v01.json").read_text(encoding="utf-8"))
        self.assertEqual(anot["split"], "teste")
        self.assertEqual(len(anot["operacoes"]), 2)
        self.assertEqual(anot["operacoes"][1]["params"]["texto"], "VENC 01/01/2027")
        with open(base / "manifesto.csv", encoding="utf-8") as f:
            linhas = list(csv.DictReader(f))
        self.assertEqual(linhas[0]["doc"], doc)
        # depois de salvar, recomeça do original e a próxima é v02
        e = self.post("/api/abrir", id=doc).get_json()
        self.assertEqual(e["versoes"], 1)
        self.assertEqual(e["historico"], [])
        self.op(doc)
        self.assertEqual(self.post("/api/salvar", id=doc).get_json()["salvo"]["versao"], 2)

    def test_doador_de_outro_split_proibido(self):
        doc, doador_mesmo, doador_outro = self.doc("treino"), self.doc("treino", 1), self.doc("teste")
        lista = self.c.get(f"/api/doadores?id={doc}").get_json()
        self.assertEqual([d["id"] for d in lista], [doador_mesmo])
        p = {"origem": {"x0": 10, "y0": 50, "x1": 110, "y1": 80},
             "destino": [[20, 300], [120, 300], [120, 330], [20, 330]]}
        r = self.op(doc, tipo="colar", params={**p, "doador": doador_outro}, categoria="nome")
        self.assertEqual(r.status_code, 400)
        self.assertIn("proibido", r.get_json()["erro"])
        r = self.op(doc, tipo="colar", params={**p, "doador": doador_mesmo}, categoria="nome")
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_previa_nao_altera(self):
        doc = self.doc("treino")
        r = self.op(doc, tipo="texto", params={"texto": "X", "x": 50, "y": 300, "tamanho": 20}, previa=True)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("png", r.get_json())
        self.assertEqual(self.post("/api/abrir", id=doc).get_json()["historico"], [])
        self.assertEqual(self.op(doc, previa=True).status_code, 400)  # pincel não tem prévia

    def test_desfazer_refazer_e_rascunho(self):
        doc = self.doc("validacao")
        self.op(doc)
        self.op(doc, categoria="nome", params={**PINCEL, "pontos": [[50, 350]]})
        r = self.post("/api/desfazer", id=doc).get_json()
        self.assertEqual(len(r["estado"]["historico"]), 1)
        self.assertTrue(r["estado"]["pode_refazer"])
        self.novo_app()  # "queda" do servidor
        e = self.post("/api/abrir", id=doc).get_json()
        self.assertEqual(len(e["historico"]), 1)
        self.assertTrue(e["pode_refazer"])
        lista = self.c.get("/api/documentos?split=validacao").get_json()
        self.assertTrue(next(d for d in lista if d["id"] == doc)["rascunho"])
        self.post("/api/descartar", id=doc)
        self.assertEqual(self.post("/api/abrir", id=doc).get_json()["historico"], [])

    def test_formatos_de_saida_par_identico(self):
        doc = self.doc("treino")
        for fmt, ext in (("jpeg", ".jpg"), ("webp", ".webp"), ("tiff", ".tif"), ("pdf", ".pdf")):
            self.op(doc)
            r = self.post("/api/salvar", id=doc, formato=fmt, qualidade=80)
            self.assertEqual(r.status_code, 200, r.get_json())
            arqs = r.get_json()["salvo"]["arquivos"]
            self.assertTrue(arqs["original"].endswith(ext) and arqs["editada"].endswith(ext))
            if fmt != "tiff":  # com perda: original e editada na MESMA qualidade
                self.assertIn("__q80", arqs["original"])
                self.assertIn("__q80", arqs["editada"])
            caminho = self.saida / "treino" / arqs["editada"]
            if fmt != "pdf":
                with Image.open(caminho) as im:
                    self.assertEqual(im.size, (self.reg.item(doc).largura, self.reg.item(doc).altura))
                    self.assertNotIn("exif", im.info)
                    self.assertNotIn("icc_profile", im.info)

    def test_selecao_conta_gotas_estimativa_mascara(self):
        doc = self.doc("treino")
        r = self.post("/api/selecao", id=doc, selecao=RET).get_json()
        self.assertEqual(r["bbox"], [14, 192, 200, 214])
        m = np.asarray(Image.open(__import__("io").BytesIO(base64.b64decode(r["png"]))))
        self.assertEqual(m.shape, (22, 186))
        self.assertEqual(self.post("/api/conta_gotas", id=doc, x=5, y=5).get_json()["cor"][:3], "#14")
        est = self.post("/api/estimar_texto", id=doc, selecao=RET).get_json()
        self.assertTrue(est["achou_texto"])
        self.op(doc)
        self.assertEqual(self.c.get(f"/api/mascara/{doc}.png").status_code, 200)
        self.assertEqual(self.c.get(f"/api/img/{doc}/atual.png").status_code, 200)
        r = self.c.get(f"/api/miniatura/{doc}.jpg")
        self.assertEqual(r.status_code, 200)
        r.close()

    def test_api_de_camadas(self):
        doc = self.doc("treino")
        self.op(doc)  # cria a camada 'valor'
        e = self.post("/api/abrir", id=doc).get_json()
        cam = e["camadas"][0]
        self.assertEqual((cam["categoria"], cam["ativa"], cam["vazia"]), ("valor", True, False))
        r = self.c.get(f"/api/camada_mini/{doc}/{cam['id']}.png")
        self.assertEqual(r.status_code, 200)
        r.close()
        r = self.post("/api/camada", id=doc, acao="props", camada=cam["id"], visivel=False).get_json()
        self.assertIn("png", r)  # devolve o trecho da composição que mudou
        self.assertFalse(r["estado"]["pode_salvar"])
        self.assertEqual(r["estado"]["ocultas"], [cam["nome"]])
        r = self.post("/api/camada", id=doc, acao="props", camada=cam["id"], visivel=True).get_json()
        r = self.post("/api/camada", id=doc, acao="mover", camada=cam["id"], dx=5, dy=-3).get_json()
        self.assertEqual(r["estado"]["historico"][-1]["tipo"], "mover_camada")
        r = self.post("/api/camada", id=doc, acao="criar", categoria="data").get_json()
        self.assertEqual([c["categoria"] for c in r["estado"]["camadas"]], ["data", "valor"])
        self.assertEqual(self.post("/api/camada", id=doc, acao="criar", categoria="xyz").status_code, 400)
        self.assertEqual(self.post("/api/camada", id=doc, acao="voar").status_code, 400)
        # borracha apaga da camada indicada
        r = self.op(doc, tipo="borracha", params={**PINCEL, "tamanho": 40}, categoria=None, camada=cam["id"])
        self.assertEqual(r.status_code, 200, r.get_json())
        # salvar grava a lista de camadas no JSON
        self.op(doc, params={**PINCEL, "pontos": [[50, 380]]})
        s = self.post("/api/salvar", id=doc).get_json()["salvo"]
        anot = json.loads((self.saida / "treino" / s["arquivos"]["anotacao"]).read_text(encoding="utf-8"))
        self.assertTrue(any(c["categoria"] == "valor" for c in anot["camadas"]))

    def test_id_inexistente(self):
        self.assertEqual(self.post("/api/abrir", id="../../etc").status_code, 400)
        self.assertEqual(self.op("nao-existe").status_code, 400)


if __name__ == "__main__":
    unittest.main()
