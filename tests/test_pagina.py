"""Técnica de página inteira no gerador (`pagina.recriar`): o plano a oferece com o pseudo-campo da página
(categoria "outro") só quando sabe o tamanho da página; ela vem como PRIMEIRA edição da versão, com as trocas de
campo por cima; máscaras, classes e índices saem coerentes."""
import json
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from rotulador import catalogo
from rotulador.gerador import Plano, Receita
from tests.util import conta

CAMPOS = [{"id": "c1", "caixa": [14, 60, 200, 80], "texto": "CLIENTE 001", "categoria": "nome", "tipo": "texto"},
          {"id": "c2", "caixa": [14, 94, 220, 114], "texto": "RUA DAS FLORES, 123", "categoria": "endereço", "tipo": "texto"}]


class TestPlanoComPagina(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias
        self.ids = {c["chave"]: int(c["id"]) for c in carregar_categorias(RAIZ / "config" / "categorias.json")}

    def test_pseudo_campo_so_com_forma(self):
        r = Receita({"tecnicas": {"pagina.recriar": 1, "campo.amador": 1}, "categorias": {"nome": 1, "endereço": 1}}, self.ids)
        pl = Plano(r, random.Random(1))
        sem = pl.candidatos(CAMPOS)
        self.assertFalse(any(f.get("tipo") == "pagina" for _, f in sem))
        com = pl.candidatos(CAMPOS, forma=(420, 320))
        pag = [(t, f) for t, f in com if f.get("tipo") == "pagina"]
        self.assertEqual(len(pag), 1)
        self.assertEqual((pag[0][0].id, pag[0][1]["categoria"], pag[0][1]["caixa"]), ("pagina.recriar", "outro", [0, 0, 320, 420]))
        self.assertEqual(sum(1 for t, _ in com if t.id == "campo.amador"), 2)  # as normais continuam
        # a página concorre no plano equilibrado sem peso de classe ("outro" não está na receita)
        escolhidos = {pl.escolher(com)[1].get("tipo", "texto") for _ in range(20)}
        self.assertIn("pagina", escolhidos)

    def test_catalogo(self):
        t = catalogo.TECNICAS["pagina.recriar"]
        self.assertEqual((t.familia, t.granularidade, t.tipos), ("recriacao", "pagina", ("pagina",)))
        self.assertFalse(catalogo.compativel(t, "nome"))
        self.assertIn("recriacao", catalogo.FAMILIAS)


class TestLoteComPagina(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import Registro
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        (self.dados / "validacao").mkdir(parents=True)
        Image.fromarray(conta(62)).save(self.dados / "validacao" / "c.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pagina_vem_primeiro_e_os_rotulos_batem(self):
        rec = {"documentos": {"splits": ["validacao"]}, "variacoes": 3, "edicoes": [2, 2],
               "tecnicas": {"pagina.recriar": 50, "campo.amador": 1}, "categorias": {"nome": 1, "endereço": 1},
               "acabamento": {"ruido": "nunca", "disfarce": "nunca"}, "falsificadores": {"prob": 0}}
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(400):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertGreaterEqual(st["feitas"], 1, st)
        anots = [json.loads(a.read_text(encoding="utf-8")) for a in (self.saida / "validacao" / "anotacoes").glob("*.json")]
        com_pagina = [a for a in anots if any(e["tecnica"] == "pagina.recriar" for e in a["edicoes"])]
        self.assertTrue(com_pagina, [a["tecnicas"] for a in anots])
        raiz = self.saida / "validacao"
        for a in com_pagina:
            eds = a["edicoes"]
            self.assertEqual(eds[0]["tecnica"], "pagina.recriar")  # sempre a primeira
            self.assertEqual((eds[0]["categoria"], eds[0]["familia"]), ("outro", "recriacao"))
            x0, y0, x1, y1 = eds[0]["bbox"]  # a caixa é a das linhas re-tipografadas: boa parte da página
            for e in eds[1:]:  # a troca de campo por cima pode tapar uma linha refeita inteira (a caixa só tem o que
                # aparece); com o OCR novo a caixa do campo cobre a tinta toda e a linha mais larga some da caixa da página
                x0, y0 = min(x0, e["bbox"][0]), min(y0, e["bbox"][1])
                x1, y1 = max(x1, e["bbox"][2]), max(y1, e["bbox"][3])
            self.assertTrue(x1 - x0 >= 0.5 * a["largura"] and y1 - y0 >= 0.3 * a["altura"], eds[0]["bbox"])
            self.assertTrue(all(e["tecnica"] != "pagina.recriar" for e in eds[1:]))
            tec = np.asarray(Image.open(raiz / a["arquivos"]["mascara_tecnicas"]))
            cla = np.asarray(Image.open(raiz / a["arquivos"]["mascara_classes"]))
            self.assertIn(catalogo.TECNICAS["pagina.recriar"].num, set(np.unique(tec).tolist()))
            self.assertIn(a["ids_categoria"]["outro"], set(np.unique(cla).tolist()))
            self.assertIn("recriacao", a["familias"])
            if len(eds) > 1:  # a troca de campo por cima vence na máscara de técnicas
                self.assertIn(eds[1]["tecnica_num"], set(np.unique(tec).tolist()))
        d = self.c.get("/api/catalogo?familia=recriacao").get_json() if False else None  # (o catálogo é testado em test_tecnicas)


if __name__ == "__main__":
    unittest.main()
