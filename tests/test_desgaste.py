"""Desgaste físico no canal (tinta fraca, rolos, sangria, fotocópia, verso aparecendo, sujeira no vidro, reflexo
na foto, tela PenTile com faixa do refresh): o par continua igual fora da edição (sem codecs, exatamente), as
máscaras acompanham, e o sorteio grava os parâmetros."""
import random
import unittest

import numpy as np

from rotulador import canal
from tests.util import conta


def _par():
    orig = conta(3)
    ed = orig.copy()
    ed[150:175, 40:140] = (30, 30, 30)  # a "edição": um retângulo escuro
    regiao = np.zeros(orig.shape[:2], bool)
    regiao[150:175, 40:140] = True
    return orig, ed, regiao


class TestDesgaste(unittest.TestCase):
    def _confere(self, passos, nome):
        orig, ed, regiao = _par()
        th = {"perfil": "teste", "semente": 7, "passos": passos}
        (o2, e2), (r2,), _ = canal.aplicar([orig, ed], [regiao], [], th)
        self.assertEqual(o2.shape, e2.shape, nome)
        self.assertIsNone(canal.conferir_par(o2, e2, r2, th), nome)
        self.assertTrue(r2.any(), nome)
        self.assertFalse(np.array_equal(o2, orig), f"{nome}: o passo não mudou nada")
        return o2, e2, r2

    def test_impressao_com_cada_desgaste(self):
        base = {"tipo": "impressao", "espalhamento": 0.3, "toner": 0.08, "papel": 2.0, "reticula": None, "fase": "depois"}
        for nome, d in (("tinta_fraca", {"tinta_fraca": {"periodo": 12.0, "fase": 0.2, "forca": 0.5, "largura": 0.4}}),
                        ("rolos", {"rolos": {"posicoes": [0.3, 0.7], "largura": 5.0, "forca": 0.4}}),
                        ("sangria", {"sangria": 1.5}),
                        ("fotocopia", {"fotocopia": {"gama": 0.6, "poeira": 0.003, "desfoque": 0.6}})):
            o2, e2, r2 = self._confere([{**base, "desgaste": d}], nome)
            if nome == "tinta_fraca":  # faixas: a tinta fica mais clara em algumas linhas e não em outras
                esc = (255 - o2.astype(int)).max(2)
                tinta = (255 - conta(3).astype(int)).max(2) > 100
                self.assertGreater(np.ptp(np.where(tinta, esc, 0).max(1)[tinta.any(1)]), 20)

    def test_scanner_com_verso_e_sujeira(self):
        p = {"tipo": "scanner", "angulo": 0.0, "escala": 1.0, "desfoque": 0.3, "gama": 1.0, "contraste": 1.0, "brilho": 0.0,
             "ruido": 0.0, "jpeg": None, "verso": 0.15, "sujeira": {"pontos": 30, "fios": 2, "forca": 0.6}, "fase": "depois"}
        o2, e2, r2 = self._confere([p], "scanner")
        # o verso é espelhado da ORIGINAL: na editada, o espelho do retângulo editado NÃO aparece (o par é igual lá)
        H, W = o2.shape[:2]
        esp = (slice(150, 175), slice(W - 140, W - 40))
        self.assertTrue(np.array_equal(o2[esp], e2[esp]))

    def test_celular_com_reflexo(self):
        th = canal.sortear(random.Random(11), {"perfil": "celular", "prob": 1.0, "captura": "depois"})
        p = {**th["passos"][0], "jpeg": None, "lente": None, "curvatura": None, "movimento": None,
             "brilho": {"x": 0.5, "y": 0.5, "rx": 0.25, "ry": 0.2, "forca": 0.4}}
        self._confere([p], "celular")

    def test_tela_pentile_com_faixa(self):
        th = canal.sortear(random.Random(5), {"perfil": "tela", "prob": 1.0})
        p = {**next(x for x in th["passos"] if x["tipo"] == "tela"), "jpeg": None, "lente": None, "subpixel": "pentile",
             "balanco": [0.96, 1.0, 1.06], "banda": [0.4, 0.03, 0.12]}
        o2, e2, r2 = self._confere([p], "tela")
        self.assertGreater(float(o2[..., 2].mean()) - float(o2[..., 0].mean()), 0)  # azulada

    def test_sorteio_grava_e_descreve(self):
        vistos = set()
        for sem in range(80):
            for perfil in ("misturado", "tela"):
                th = canal.sortear(random.Random(sem), {"perfil": perfil, "prob": 1.0})
                for p in th["passos"]:
                    vistos |= {k for k in ("desgaste", "verso", "sujeira", "brilho", "banda") if p.get(k)}
                    if p.get("subpixel") == "pentile":
                        vistos.add("pentile")
                canal.descrever(th)
        self.assertEqual(vistos, {"desgaste", "verso", "sujeira", "brilho", "banda", "pentile"})
        th = {"perfil": "t", "semente": 1, "passos": [{"tipo": "impressao", "espalhamento": 0.3, "toner": 0.08, "papel": 2.0,
                                                       "desgaste": {"sangria": 2.0}, "fase": "depois"}]}
        self.assertIn("desgaste: sangria", canal.descrever(th))
        self.assertGreater(canal.raio(th), canal.raio({**th, "passos": [{**th["passos"][0], "desgaste": None}]}))


if __name__ == "__main__":
    unittest.main()
