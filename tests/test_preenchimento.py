import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rotulador.ops.preenchimento import dilatar, preencher  # noqa: E402


def listras(h=160, w=200, periodo=12):
    """Listras verticais coloridas: textura com uma única resposta certa."""
    x = np.arange(w)
    faixa = (x % periodo) < periodo // 2
    img = np.zeros((h, w, 3), np.uint8)
    img[:, faixa] = (200, 60, 40)
    img[:, ~faixa] = (30, 90, 210)
    return img


class TestPreencher(unittest.TestCase):
    def test_nao_altera_fora_da_selecao(self):
        rng = np.random.default_rng(1)
        img = rng.integers(0, 256, (90, 120, 3), dtype=np.uint8)
        mask = np.zeros((90, 120), bool)
        mask[30:60, 40:80] = True
        res = preencher(img, mask, expandir=2)
        fora = ~dilatar(mask, 2)
        self.assertTrue(np.array_equal(res[fora], img[fora]))
        self.assertEqual(res.dtype, np.uint8)
        self.assertEqual(res.shape, img.shape)

    def test_selecao_vazia_devolve_copia(self):
        img = listras()
        res = preencher(img, np.zeros(img.shape[:2], bool))
        self.assertTrue(np.array_equal(res, img))
        self.assertIsNot(res, img)

    def test_cor_lisa_fica_exata(self):
        img = np.full((80, 100, 3), (12, 150, 90), np.uint8)
        mask = np.zeros((80, 100), bool)
        mask[20:60, 30:70] = True
        self.assertTrue(np.array_equal(preencher(img, mask), img))

    def test_reconstroi_textura_regular_sem_borrar(self):
        img = listras()
        mask = np.zeros(img.shape[:2], bool)
        mask[50:110, 60:140] = True
        res = preencher(img, mask)
        # cada pixel preenchido deve ser uma das duas cores, não uma mistura
        cores = res[mask].astype(int)
        dist = np.minimum(np.abs(cores - (200, 60, 40)).sum(1), np.abs(cores - (30, 90, 210)).sum(1))
        self.assertGreater(np.mean(dist < 30), 0.95)
        # e as listras devem continuar alinhadas com o resto da imagem (sem emenda fora de fase)
        fora_de_fase = np.abs(res.astype(int) - img.astype(int)).sum(2)[mask] > 60
        self.assertLess(fora_de_fase.mean(), 0.05)

    def test_selecao_encostada_na_borda_e_no_canto(self):
        img = listras(120, 150)
        mask = np.zeros(img.shape[:2], bool)
        mask[:25, :30] = True
        mask[90:, 130:] = True
        res = preencher(img, mask)
        self.assertLess(np.abs(res[mask].astype(int) - img[mask].astype(int)).mean(), 25)

    def test_tamanhos_impares_e_imagem_inteira(self):
        img = listras(97, 131)
        mask = np.zeros(img.shape[:2], bool)
        mask[40:61, 50:83] = True
        for margem in (-1, None, 10):
            for patch in (5, 7, 9, 11):
                res = preencher(img, mask, tamanho_patch=patch, margem=margem)
                self.assertEqual(res.shape, img.shape)

    def test_imagem_menor_que_o_patch(self):
        img = listras(6, 6, periodo=2)
        mask = np.zeros((6, 6), bool)
        mask[2:4, 2:4] = True
        res = preencher(img, mask, tamanho_patch=7)
        self.assertEqual(res.shape, img.shape)

    def test_quase_tudo_selecionado(self):
        img = listras(60, 60)
        mask = np.ones((60, 60), bool)
        mask[0, 0] = False
        res = preencher(img, mask)
        self.assertTrue(np.array_equal(res[0, 0], img[0, 0]))

    def test_sementes_diferentes_dao_variacoes(self):
        rng = np.random.default_rng(3)
        img = rng.integers(0, 256, (100, 100, 3), dtype=np.uint8)
        mask = np.zeros((100, 100), bool)
        mask[30:70, 30:70] = True
        a = preencher(img, mask, semente=0)
        b = preencher(img, mask, semente=0)
        c = preencher(img, mask, semente=1)
        self.assertTrue(np.array_equal(a, b))
        self.assertFalse(np.array_equal(a, c))

    def test_entradas_invalidas(self):
        img = listras(40, 40)
        with self.assertRaises(ValueError):
            preencher(img[..., 0], np.zeros((40, 40), bool))
        with self.assertRaises(ValueError):
            preencher(img, np.zeros((10, 10), bool))
        with self.assertRaises(ValueError):
            preencher(img, np.ones((40, 40), bool))


if __name__ == "__main__":
    unittest.main()
