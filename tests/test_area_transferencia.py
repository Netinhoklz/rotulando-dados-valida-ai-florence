"""Copiar, recortar e colar do editor (Ctrl+C / Ctrl+X / Ctrl+V): a colagem de OUTRO documento leva só o formato da
seleção copiada (laço, elipse, varinha) e nunca atravessa splits; puxar a seleção dentro do documento é a
transformação "mover" com o buraco preenchido."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app import RAIZ, carregar_categorias, criar_app
from rotulador.ops import clonagem
from rotulador.registro import SPLITS, Registro
from rotulador.selecao import vazia
from rotulador.sessao import ErroOperacao
from tests.util import conta, salvar

ELIPSE = {"formas": [{"tipo": "elipse", "x0": 20, "y0": 40, "x1": 120, "y1": 100, "modo": "novo"}]}
ORIGEM = {"x0": 20, "y0": 40, "x1": 120, "y1": 100}
DESTINO = [[200, 300], [300, 300], [300, 360], [200, 360]]


class TestFormaNoDoador(unittest.TestCase):
    """Direto na operação: a pegada da colagem segue a elipse, não a caixa."""

    def setUp(self):
        self.img = np.full((500, 400, 3), 245, np.uint8)
        self.doador = np.zeros((500, 400, 3), np.uint8)
        self.doador[..., 0] = 200  # vermelho: dá para ver onde colou

    def colar(self, **p):
        return clonagem.colar(self.img, {"doador": "d", "origem": ORIGEM, "destino": DESTINO, **p},
                              vazia(500, 400), carregar_doador=lambda _: self.doador)

    def test_so_o_formato_da_selecao(self):
        caixa = self.colar()
        forma = self.colar(selecao_doador=ELIPSE)
        self.assertTrue(caixa.pegada.all() or caixa.pegada.mean() > 0.9)
        # a elipse ocupa ~pi/4 da caixa: os cantos ficam de fora, o centro entra
        self.assertLess(forma.pegada.mean(), 0.85)
        self.assertGreater(forma.pegada.mean(), 0.6)
        h, w = forma.pegada.shape
        self.assertFalse(forma.pegada[3, 3])
        self.assertTrue(forma.pegada[h // 2, w // 2])
        self.assertTrue(forma.meta["forma_da_selecao"])
        self.assertFalse(caixa.meta["forma_da_selecao"])

    def test_selecao_que_nao_pega_nada(self):
        longe = {"formas": [{"tipo": "ret", "x0": 300, "y0": 400, "x1": 350, "y1": 450, "modo": "novo"}]}
        with self.assertRaises(ErroOperacao):
            self.colar(selecao_doador=longe)

    def test_borda_suave_dentro_da_forma(self):
        dura = self.colar(selecao_doador=ELIPSE)
        suave = self.colar(selecao_doador=ELIPSE, suavizar_borda=6)
        self.assertLessEqual(suave.pegada.sum(), dura.pegada.sum())  # suaviza para dentro: não cresce


class TestColarPeloServidor(unittest.TestCase):
    """Pelo /api/op, como o Ctrl+V da página manda."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        dados = self.tmp / "dados"
        for i, sp in enumerate(SPLITS):
            salvar(conta(10 + i), dados / sp / f"doc_{sp}.png")
            salvar(conta(20 + i), dados / sp / f"outro_{sp}.png")
        self.reg = Registro(dados, self.tmp / "saida", log=lambda *_: None)
        self.c = criar_app(self.reg, self.tmp / "saida", carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def op(self, doc, **corpo):
        return self.c.post("/api/op", data=json.dumps({"id": doc, **corpo}), content_type="application/json")

    def test_ctrl_v_de_outro_documento_do_mesmo_split(self):
        doc, doador = (it.doc for it in self.reg.por_split["treino"])
        p = {"doador": doador, "origem": ORIGEM, "destino": DESTINO, "selecao_doador": ELIPSE}
        r = self.op(doc, tipo="colar", params=p, categoria="pegar de outro documento")
        self.assertEqual(r.status_code, 200, r.get_json())
        estado = r.get_json()["estado"]
        self.assertEqual([c["categoria"] for c in estado["camadas"]], ["pegar de outro documento"])

    def test_ctrl_v_de_outro_split_e_recusado(self):
        doc, doador = self.reg.por_split["treino"][0].doc, self.reg.por_split["teste"][0].doc
        p = {"doador": doador, "origem": ORIGEM, "destino": DESTINO, "selecao_doador": ELIPSE}
        r = self.op(doc, tipo="colar", params=p, categoria="pegar de outro documento")
        self.assertEqual(r.status_code, 400)
        self.assertIn("proibido", r.get_json()["erro"])

    def test_puxar_a_selecao_preenche_o_buraco(self):
        doc = self.reg.por_split["teste"][0].doc
        sel = {"formas": [{"tipo": "ret", "x0": 14, "y0": 192, "x1": 200, "y1": 214, "modo": "novo"}]}
        destino = [[14, 260], [200, 260], [200, 282], [14, 282]]
        r = self.op(doc, tipo="transformar", selecao=sel, categoria="copiar e colar",
                    params={"modo": "mover", "origem": "preencher", "destino": destino, "semente": 3})
        self.assertEqual(r.status_code, 200, r.get_json())
        x0, y0, x1, y1 = r.get_json()["bbox"]
        self.assertLessEqual(y0, 192)  # a caixa alterada cobre o buraco (origem) e o destino
        self.assertGreaterEqual(y1, 282)


if __name__ == "__main__":
    unittest.main()
