"""Canal: recorte parcial da página (contém a edição depois dela; janela livre antes), scanner em tons de cinza e
preto e branco, textura do fundo na foto de celular; o sorteio e a descrição gravam tudo."""
import random
import unittest

import numpy as np

from rotulador import canal
from tests.util import conta


def _par():
    orig = conta(3)
    ed = orig.copy()
    ed[150:175, 40:140] = (30, 30, 30)
    regiao = np.zeros(orig.shape[:2], bool)
    regiao[150:175, 40:140] = True
    return orig, ed, regiao


def _scanner(**kw):
    return {"tipo": "scanner", "angulo": 0.0, "escala": 1.0, "desfoque": 0.3, "gama": 1.0, "contraste": 1.0, "brilho": 0.0,
            "ruido": 1.0, "jpeg": None, "fase": "depois", **kw}


class TestRecorte(unittest.TestCase):
    def test_depois_da_edicao_a_janela_contem_a_edicao(self):
        orig, ed, regiao = _par()
        for ancora in ([1.0, 1.0], [0.0, 0.0], [0.5, 0.2]):
            th = {"perfil": "t", "semente": 1, "passos": [{"tipo": "recorte", "fx": 0.4, "fy": 0.3, "ancora": ancora, "fase": "depois"}]}
            (o2, e2), (r2,), _ = canal.aplicar([orig, ed], [regiao], [], th)
            self.assertEqual(int(r2.sum()), 2500, ancora)  # a edição inteira continua na janela
            self.assertLess(o2.shape[0], 420)
            self.assertLess(o2.shape[1], 320)
            self.assertIsNone(canal.conferir_par(o2, e2, r2, th))
            self.assertTrue(np.array_equal(e2[r2], np.full((2500, 3), 30, np.uint8)))

    def test_edicao_maior_que_a_janela_alarga_a_janela(self):
        orig, ed, regiao = _par()
        th = {"perfil": "t", "semente": 1, "passos": [{"tipo": "recorte", "fx": 0.1, "fy": 0.05, "ancora": [0.0, 0.0], "fase": "depois"}]}
        (o2, e2), (r2,), _ = canal.aplicar([orig, ed], [regiao], [], th)
        self.assertEqual(int(r2.sum()), 2500)
        self.assertGreaterEqual(o2.shape[1], 100)

    def test_antes_da_edicao_a_janela_e_livre_e_os_pontos_acompanham(self):
        orig = conta(3)
        th = {"perfil": "t", "semente": 1, "passos": [{"tipo": "recorte", "fx": 0.5, "fy": 0.4, "ancora": [1.0, 1.0], "fase": "antes"}]}
        (o2,), _, _, pts = canal.aplicar([orig], [], [], th, fase="antes", pontos=[(319, 419), (0, 0)])
        self.assertEqual(o2.shape[:2], (168, 160))
        self.assertEqual([tuple(round(v) for v in p) for p in pts], [(159, 167), (-160, -252)])
        self.assertTrue(np.array_equal(o2, orig[252:, 160:]))
        self.assertIn("recortada (50% × 40% da página)", canal.descrever(th))

    def test_scanner_cinza_e_pb(self):
        orig, ed, regiao = _par()
        th = {"perfil": "t", "semente": 2, "passos": [_scanner(modo="cinza")]}
        (o2, e2), (r2,), _ = canal.aplicar([orig, ed], [regiao], [], th)
        self.assertTrue(np.array_equal(o2[..., 0], o2[..., 1]) and np.array_equal(o2[..., 1], o2[..., 2]))
        self.assertIsNone(canal.conferir_par(o2, e2, r2, th))
        self.assertIn("tons de cinza", canal.descrever(th))
        th = {"perfil": "t", "semente": 2, "passos": [_scanner(modo="pb", limiar=0.35, suavidade=3.0)]}
        (o2, e2), (r2,), _ = canal.aplicar([orig, ed], [regiao], [], th)
        extremos = ((o2[..., 0] < 20) | (o2[..., 0] > 235)).mean()
        self.assertGreater(float(extremos), 0.9)  # quase tudo preto ou branco
        self.assertTrue(r2.any() and (e2[r2][:, 0] < 20).mean() > 0.9)  # a edição escura virou tinta preta
        self.assertIsNone(canal.conferir_par(o2, e2, r2, th))
        self.assertIn("preto e branco", canal.descrever(th))

    def test_textura_do_fundo_na_foto(self):
        orig, ed, regiao = _par()
        base = canal.sortear(random.Random(4), {"perfil": "celular", "prob": 1.0, "captura": "depois"})
        passo = {**next(p for p in base["passos"] if p["tipo"] == "celular"), "jpeg": None, "lente": None, "curvatura": None}
        saidas = {}
        for textura in ("lisa", "madeira", "tecido", "papel", "granito"):
            th = {"perfil": "t", "semente": 9, "passos": [{**passo, "textura": textura, "textura_angulo": 30.0,
                                                         "textura_periodo": 20.0, "textura_claro": 80.0, "fase": "depois"}]}
            (o2, e2), (r2,), _ = canal.aplicar([orig, ed], [regiao], [], th)
            self.assertIsNone(canal.conferir_par(o2, e2, r2, th), textura)
            self.assertTrue(r2.any(), textura)
            saidas[textura] = o2
            if textura != "lisa":
                self.assertFalse(np.array_equal(saidas["lisa"], o2), textura)
                self.assertIn(f"sobre {textura}", canal.descrever(th))

    def test_sorteio_grava_recorte_modos_e_texturas(self):
        vistos = set()
        for sem in range(120):
            th = canal.sortear(random.Random(sem), {"perfil": "misturado", "prob": 1.0})
            for p in th["passos"]:
                if p["tipo"] == "recorte":
                    vistos.add("recorte:" + p["fase"])
                    self.assertTrue(0.5 <= p["fx"] <= 1.0 and 0.3 <= p["fy"] <= 0.9)
                if p["tipo"] == "scanner":
                    vistos.add("scanner:" + p["modo"])
                if p["tipo"] == "celular":
                    vistos.add("textura:" + p["textura"])
            canal.descrever(th)
        self.assertTrue({"recorte:antes", "recorte:depois", "scanner:cor", "scanner:cinza", "scanner:pb"} <= vistos, vistos)
        self.assertTrue({"textura:lisa", "textura:madeira", "textura:papel"} <= vistos, vistos)


if __name__ == "__main__":
    unittest.main()
