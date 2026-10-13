"""Marcas benignas digitais (tarja, borrão, pixelado sobre dado sensível; marca d'água): entram sem captura
física, só em campo sensível que não é a edição, iguais no par; a divisão papel/digital; o lote grava."""
import json
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from rotulador import canal, marcas
from tests.util import conta

CAMPOS = [{"id": "c1", "caixa": [14, 128, 200, 150], "categoria": "cpf/cnpj", "texto": "123.456.789-09"},
          {"id": "c2", "caixa": [14, 196, 220, 218], "categoria": "valor", "texto": "R$ 100,45"},
          {"id": "c3", "caixa": [14, 60, 200, 80], "categoria": "nome", "texto": "CLIENTE 001"}]


def _par():
    orig = conta(1)
    ed = orig.copy()
    ed[196:218, 14:220] = (30, 30, 30)  # a "edição" no campo valor
    regiao = np.zeros(orig.shape[:2], bool)
    regiao[196:218, 14:220] = True
    return orig, ed, regiao


class TestMarcasDigitais(unittest.TestCase):
    def test_sem_captura_fisica_so_digitais_e_alvos_sensiveis(self):
        for sem in range(40):
            th = marcas.sortear(random.Random(sem), {"prob": 1.0}, campos=CAMPOS, fisica=False)
            self.assertTrue(set(th["tipos"]) <= marcas.DIGITAIS, th["tipos"])
            self.assertEqual(th["alvos"], [[14, 128, 200, 150]])  # só o CPF é sensível
        tipos = {t for sem in range(60) for t in marcas.sortear(random.Random(sem), {"prob": 1.0}, fisica=True)["tipos"]}
        self.assertTrue(tipos & marcas.DIGITAIS and tipos - marcas.DIGITAIS)  # com captura, os dois tipos entram

    def test_dividir(self):
        papel, digital = marcas.dividir({"semente": 1, "tipos": ["carimbo", "tarja", "dobra"], "alvos": []})
        self.assertEqual((papel["tipos"], digital["tipos"]), (["carimbo", "dobra"], ["tarja"]))
        self.assertEqual(marcas.dividir({"semente": 1, "tipos": ["tarja"], "alvos": []})[0], None)
        self.assertEqual(marcas.dividir(None), (None, None))

    def test_tarja_borrao_pixelado_cobrem_o_sensivel_e_nao_a_edicao(self):
        for tipo in ("tarja", "borrao", "pixelado"):
            orig, ed, regiao = _par()
            th = {"semente": 3, "tipos": [tipo], "alvos": [[14, 128, 200, 150]]}
            (o2, e2), info = marcas.aplicar([orig, ed], regiao, th)
            self.assertEqual([m["tipo"] for m in info], [tipo])
            x0, y0, x1, y1 = info[0]["caixa"]
            self.assertTrue(x0 <= 14 and y0 <= 128 and x1 >= 200 and y1 >= 150, info)  # cobre a caixa toda
            self.assertFalse(np.array_equal(o2[y0:y1, x0:x1], orig[y0:y1, x0:x1]), tipo)
            self.assertTrue(np.array_equal(o2[regiao], orig[regiao]) and np.array_equal(e2[regiao], ed[regiao]), tipo)
            self.assertTrue(np.array_equal(o2[~regiao], e2[~regiao]), tipo)  # igual no par fora da edição
            if tipo == "tarja":
                self.assertLess(float(o2[y0:y1, x0:x1].mean()), 45)

    def test_tarja_nao_cobre_alvo_na_edicao(self):
        orig, ed, regiao = _par()
        th = {"semente": 3, "tipos": ["tarja"], "alvos": [[14, 196, 220, 218]]}  # o único alvo É a edição
        (o2, e2), info = marcas.aplicar([orig, ed], regiao, th)
        self.assertEqual(info, [])
        self.assertTrue(np.array_equal(o2, orig))

    def test_marca_dagua_e_igual_no_par(self):
        orig, ed, regiao = _par()
        (o2, e2), info = marcas.aplicar([orig, ed], regiao, {"semente": 5, "tipos": ["marca_dagua"], "alvos": []})
        self.assertEqual(info[0]["tipo"], "marca_dagua")
        self.assertFalse(np.array_equal(o2, orig))
        self.assertTrue(np.array_equal(o2[~regiao], e2[~regiao]))
        self.assertTrue((np.abs(o2.astype(int) - orig.astype(int)) > 0).mean() > 0.01)  # é grande
        self.assertIn("marca d'água", marcas.descrever(info))


class TestLoteComMarcasDigitais(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import Registro
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        (self.dados / "validacao").mkdir(parents=True)
        Image.fromarray(conta(61)).save(self.dados / "validacao" / "c.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_canal_sem_captura_fisica_grava_marca_digital(self):
        rec = {"documentos": {"splits": ["validacao"]}, "variacoes": 3, "edicoes": [1, 1],
               "tecnicas": {"campo.amador": 1}, "categorias": {"nome": 1, "endereço": 1},
               "acabamento": {"ruido": "nunca", "disfarce": "nunca"},
               "canal": {"perfil": "whatsapp", "prob": 1.0}, "marcas": {"prob": 1.0}, "falsificadores": {"prob": 0}}
        # o documento sintético não tem campo sensível (CPF): só a marca d'água cabe; com ela sempre sorteada, o
        # teste não depende da sorte
        with mock.patch.dict(marcas.TIPOS, {k: (1.0 if k == "marca_dagua" else 0.0) for k in marcas.TIPOS}):
            self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
            for _ in range(300):
                st = self.c.get("/api/gerador/status").get_json()
                if not st["rodando"]:
                    break
                time.sleep(0.5)
        self.assertEqual(st["feitas"], 3, st)
        anots = [json.loads(a.read_text(encoding="utf-8")) for a in (self.saida / "validacao" / "anotacoes").glob("*.json")]
        tipos = [m["tipo"] for a in anots for m in a.get("marcas_benignas") or []]
        self.assertTrue(tipos, "nenhuma marca digital gravada")
        self.assertTrue(set(tipos) <= marcas.DIGITAIS, tipos)  # WhatsApp não tem papel: só as digitais
        for a in anots:
            self.assertFalse(canal.captura_fisica(a["canal"]))
            raiz = self.saida / "validacao"
            orig = np.asarray(Image.open(raiz / a["arquivos"]["original"]).convert("RGB"))
            ed = np.asarray(Image.open(raiz / a["arquivos"]["editada"]).convert("RGB"))
            regiao = np.asarray(Image.open(raiz / a["arquivos"]["mascara_regiao"])) > 0
            self.assertIsNone(canal.conferir_par(orig, ed, regiao, a["canal"]))


if __name__ == "__main__":
    unittest.main()
