import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from rotulador import selecao
from rotulador.ops import executar, pro
from rotulador.registro import Item
from rotulador.sessao import ErroOperacao, Sessao
from tests.util import conta

IDS = {"nome": 1, "valor": 9}


def ret(x0, y0, x1, y1, **kw):
    return {"formas": [{"tipo": "ret", "x0": x0, "y0": y0, "x1": x1, "y1": y1}], **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.img = conta(5)
        self.item = Item(doc="abc", split="treino", caminho=self.tmp / "x.png", rel="x.png", pagina=0, paginas=1,
                         sha_arquivo="f", sha_pixels="p", altura=self.img.shape[0], largura=self.img.shape[1])
        self.s = Sessao(self.item, self.img.copy(), self.tmp / "rasc", IDS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def aplicar(self, tipo, params, spec=None, categoria="valor"):
        sel = selecao.rasterizar(spec, self.s.atual)
        res = executar(tipo, self.s.atual, params, sel, base=self.s.base)
        return self.s.aplicar(tipo, categoria, "", params, res)

    def alterados(self):
        return (self.s.atual != self.s.base).any(2)


class TestPoisson(unittest.TestCase):
    def test_identidade(self):
        img = conta(1)[100:220, 10:200]
        m = np.zeros(img.shape[:2], bool)
        m[20:90, 30:150] = True
        out = pro.poisson(img, img, m)
        self.assertLessEqual(int(np.abs(out.astype(int) - img).max()), 2)

    def test_casa_iluminacao_do_destino(self):
        dst = np.full((80, 80, 3), 200, np.uint8)
        fonte = np.full((80, 80, 3), 60, np.uint8)
        fonte[::4] = 90  # textura listrada escura
        m = np.zeros((80, 80), bool)
        m[20:60, 20:60] = True
        out = pro.poisson(dst, fonte, m)
        miolo = out[35:45, 35:45].astype(float)
        self.assertGreater(miolo.mean(), 170)  # a cor vem do destino...
        self.assertGreater(miolo.std(), 3)  # ...a textura vem da fonte


class TestFerramentasPro(Base):
    def test_carimbo_fonte_original_e_transformacoes(self):
        # pinta algo, depois o carimbo com fonte "original" restaura exatamente o original
        self.aplicar("pincel", {"pontos": [[100, 300], [200, 300]], "tamanho": 20, "cor": "#ff0000"})
        p = {"pontos": [[100, 300], [200, 300]], "tamanho": 30, "dureza": 1, "fonte": "original",
             "origem_x": 100, "origem_y": 300, "inicio_x": 100.0001, "inicio_y": 300}
        self.aplicar("carimbo", p, categoria="nome")
        np.testing.assert_allclose(self.s.atual[296:305, 110:190].astype(int),
                                   self.s.base[296:305, 110:190].astype(int), atol=1)
        # escala 2x e rotação rodam e só mexem no traço
        p2 = {"pontos": [[60, 380]], "tamanho": 16, "origem_x": 60, "origem_y": 80, "inicio_x": 60,
              "inicio_y": 380, "escala": 200, "rotacao": 30, "modo_mescla": "escurecer"}
        self.aplicar("carimbo", p2)
        alt = self.alterados()
        self.assertTrue(alt[372:388, 52:68].any())
        self.assertFalse(alt[:250].any())

    def test_recuperacao_mantem_cor_do_destino(self):
        p = {"pontos": [[150, 150], [220, 150]], "tamanho": 14, "origem_x": 150, "origem_y": 20,
             "inicio_x": 150, "inicio_y": 150}  # fonte = faixa azul do topo
        self.aplicar("recuperacao", p)
        miolo = self.s.atual[148:153, 165:205].astype(float)
        self.assertGreater(miolo.mean(), 150)  # não ficou azul-escuro como a fonte
        self.assertFalse(self.alterados()[:120].any())

    def test_remendo_origem_e_destino(self):
        spec = ret(14, 60, 200, 80)  # linha "NOME: ..."
        self.aplicar("remendo", {"dx": 0, "dy": 250, "modo": "origem"}, spec)  # remendo de área vazia
        alt = self.alterados()
        self.assertTrue(alt[60:80, 14:200].any())
        self.assertFalse(alt[100:].any())
        # agora o texto apagado some do lugar (fica parecido com papel)
        self.assertLess(int(np.abs(self.s.atual[65:75, 20:150].astype(int) - 246).max()), 40)
        self.aplicar("remendo", {"dx": 0, "dy": 250, "modo": "destino", "mesclar": False}, ret(14, 94, 200, 112),
                     categoria="nome")
        np.testing.assert_array_equal(self.s.atual[344:362, 14:200], self.s.base[94:112, 14:200])

    def test_retoque_todos_os_modos(self):
        for i, modo in enumerate(("desfocar", "nitidez", "clarear", "escurecer", "saturar", "dessaturar", "borrar")):
            antes = self.s.atual.copy()
            # linhas distintas: traço numa camada de baixo, coberto por outra, não aparece (como no Photoshop)
            y = {"saturar": 6, "dessaturar": 26, "borrar": 196}.get(modo, 60 + 34 * i)
            self.aplicar("retoque", {"pontos": [[20, y + 8], [180, y + 8]], "tamanho": 14, "forca": 0.8, "modo": modo},
                         categoria="nome" if i % 2 else "valor")
            mudou = (self.s.atual != antes).any(2)
            self.assertTrue(mudou.any(), modo)
            ys, xs = np.nonzero(mudou)
            self.assertTrue(ys.min() >= y + 8 - 12 and ys.max() <= y + 8 + 12, modo)

    def test_formas(self):
        self.aplicar("forma", {"tipo": "ret", "x0": 20, "y0": 300, "x1": 120, "y1": 340, "cor": "#ffffff",
                               "preencher": True, "espessura": 0})
        self.assertTrue((self.s.atual[310:330, 30:110] == 255).all())
        self.aplicar("forma", {"tipo": "linha", "x0": 20, "y0": 350, "x1": 300, "y1": 350, "espessura": 2,
                               "cor_contorno": "#000000"}, categoria="nome")
        self.assertTrue((self.s.atual[349:351, 40:280] < 60).all())
        self.aplicar("forma", {"tipo": "elipse", "x0": 150, "y0": 290, "x1": 250, "y1": 340, "cor": "#00ff00",
                               "espessura": 3})
        self.assertEqual(tuple(self.s.atual[315, 200]), (0, 255, 0))

    def test_preencher_amostragens(self):
        for y, amostragem in zip((60, 128, 192), ("auto", "documento", "margem")):
            self.aplicar("preencher", {"amostragem": amostragem, "margem": 60, "semente": 1}, ret(14, y, 200, y + 22))
            self.assertLess(int(np.abs(self.s.atual[y + 4:y + 18, 20:150].astype(int) - 246).max()), 60, amostragem)
        with self.assertRaises(ErroOperacao):
            self.aplicar("preencher", {"amostragem": "xyz"}, ret(14, 192, 200, 214))

    def test_igualar_ruido_calibrado(self):
        rng = np.random.default_rng(0)
        papel = np.clip(200 + rng.normal(0, 6, (300, 300, 1)), 0, 255).repeat(3, 2).astype(np.uint8)
        papel[100:200, 100:200] = 200  # remendo digital "limpo demais"
        self.s = Sessao(self.item, papel, self.tmp / "rasc2", IDS)
        self.aplicar("ajuste", {"ajuste": "igualar_ruido", "semente": 3}, ret(100, 100, 200, 200))
        f = self.s.atual.astype(np.float32)
        res = f - cv2.medianBlur(self.s.atual, 3).astype(np.float32)
        dentro, fora = res[110:190, 110:190].std(), res[10:90, 10:290].std()
        self.assertAlmostEqual(dentro / fora, 1.0, delta=0.12)


class TestSelecaoNova(unittest.TestCase):
    def test_tinta_pega_so_o_texto(self):
        img = conta(2)
        m = selecao.rasterizar({"formas": [{"tipo": "tinta", "x0": 10, "y0": 190, "x1": 220, "y1": 216,
                                            "folga": 0}]}, img).completa_bool()
        self.assertTrue(m.any())
        texto = (img[190:216, 10:220].astype(int).max(2) < 120)
        self.assertGreater((m[190:216, 10:220] & texto).sum() / texto.sum(), 0.9)
        self.assertLess(m.sum(), 0.6 * 26 * 210)  # não pegou o retângulo todo

    def test_pincel_de_selecao(self):
        img = conta(2)
        m = selecao.rasterizar({"formas": [{"tipo": "traco", "pontos": [[50, 50], [150, 50]], "tamanho": 10}]},
                               img).completa_bool()
        self.assertTrue(m[50, 100] and m[46, 60] and not m[60, 100])


if __name__ == "__main__":
    unittest.main()
