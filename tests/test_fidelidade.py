"""O texto novo igual ao antigo em tamanho e grossura (rotulador.fidelidade) e o que o gerador deixou de sortear de
propósito (escala no olho, escala do FSTS, letra esticada)."""
import random
import unittest

import numpy as np

from rotulador import fidelidade, fsts, humano
from rotulador.ops import texto


def linha(conteudo: str, tamanho: float = 30.0, negrito: float = 0.0, W: int = 700, H: int = 80) -> np.ndarray:
    """O texto em Arial, preto no papel branco, com a linha de base em y = 55."""
    img = np.full((H, W, 3), 250, np.uint8)
    y0, y1, x0, x1, a = texto.renderizar({"texto": conteudo, "fonte": texto.fonte_padrao(), "tamanho": tamanho,
                                          "negrito": negrito, "x": 20, "y": 55}, H, W)
    sub = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = np.clip(sub * (1 - a[..., None]) + 20 * a[..., None] + 0.5, 0, 255).astype(np.uint8)
    return img


CAIXA = (10, 10, 690, 70)


class TestFidelidade(unittest.TestCase):
    def test_o_mesmo_tamanho_e_traco_passa(self):
        m = fidelidade.comparar(linha("MARIA DOS SANTOS"), linha("JOSE PEREIRA LIMA"), CAIXA, CAIXA,
                                "MARIA DOS SANTOS", "JOSE PEREIRA LIMA")
        self.assertTrue(m["ok"], m)
        self.assertAlmostEqual(m["altura"], 1.0, delta=0.04)
        self.assertAlmostEqual(m["grossura"], 1.0, delta=0.08)

    def test_maior_ou_mais_grosso_nao_passa(self):
        antes = linha("MARIA DOS SANTOS")
        m = fidelidade.comparar(antes, linha("JOSE PEREIRA LIMA", tamanho=33), CAIXA, CAIXA, "MARIA DOS SANTOS",
                                "JOSE PEREIRA LIMA")
        self.assertIn("altura", m["fora"])
        m = fidelidade.comparar(antes, linha("JOSE PEREIRA LIMA", negrito=1.2), CAIXA, CAIXA, "MARIA DOS SANTOS",
                                "JOSE PEREIRA LIMA")
        self.assertIn("grossura", m["fora"])
        self.assertGreater(m["grossura"], 1.1)

    def test_maiuscula_contra_minuscula_nao_compara_a_altura(self):
        m = fidelidade.comparar(linha("RUA DAS FLORES"), linha("rua casa nova"), CAIXA, CAIXA, "RUA DAS FLORES",
                                "rua casa nova")
        self.assertIsNone(m["altura"])
        self.assertNotIn("altura", m["fora"])


class TestSemSorteioDeTamanho(unittest.TestCase):
    def test_operador_humano_escreve_no_tamanho_do_antigo(self):
        for s in range(30):
            rng = random.Random(s)
            h = humano.sortear(rng, {"prob": 1.0}, origem_jpeg=False)
            self.assertEqual(humano.texto(h, rng, 0.6, 20.0)["escala"], 1.0)

    def test_fsts_ajusta_a_escala_a_regiao(self):
        for s in range(60):
            rng = random.Random(s)
            cfg = fsts.configurar("replacement", rng)
            self.assertEqual(fsts.habito_humano(cfg, (0, 0, 200, 20), rng)["escala"], 1.0)


if __name__ == "__main__":
    unittest.main()
