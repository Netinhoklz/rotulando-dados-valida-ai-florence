"""Letra movida e letra colada: o laço do Ctrl+J e o modo Multiplicar."""
import unittest

import numpy as np

from rotulador.ops import tecnicas as T
from tests.test_tecnicas import Base

CRU = {"pericia": "cru"}  # sem acabamento: o que sai é só a técnica


class TestLaco(Base):
    def _contexto(self, cat="valor"):
        campo = self.campo(cat)
        c = T._C(self.img, {"tecnica": "caractere.transpor", "campo": campo, "semente": 3,
                            "opcoes": {"apagar": "patchmatch"}}, self.img, self.ctx, None, self.it.doc, None)
        return c, [g for g in c.glifos if g.ch.isdigit()]

    def test_mover_para_o_mesmo_lugar_devolve_a_letra(self):
        """Apagar a letra e trazê-la de volta pelo laço: os pixels dela de novo (o alfa composto outra vez com os pixels
        dela deixava a borda com cobertura a², o traço mais fino e mais claro)."""
        c, ds = self._contexto()
        for g in ds[:3]:
            tela = T._Tela(c, T._caixa_glifo(g, 8))
            tela.apagar(T._furo_glifos(tela, [g]))
            T._mover_glifos(tela, [([g], 0, 0)], c.glifos)
            x0, y0, x1, y1 = tela.caixa
            volta = tela.px[g.y0 - y0:g.y1 - y0, g.x0 - x0:g.x1 - x0]
            orig = self.img[g.y0:g.y1, g.x0:g.x1].astype(np.float32)
            self.assertLess(float(np.abs(volta - orig).mean()), 1.0, g.ch)

    def test_transpor_leva_os_pixels_e_nao_toca_os_vizinhos(self):
        campo = self.campo("valor")
        res = self.rodar("caractere.transpor", campo, acabamento=CRU, conferir_ocr=False)
        c, _ = self._contexto()
        alt = res.meta["alteracoes"][0]
        a = next(g for g in c.glifos if g.i == alt["i"])
        b = next(g for g in c.glifos if g.i == alt["i"] + 1)
        out = self.aplicado(res)
        for de, para in ((a, b), (b, a)):
            dx = int(round(para.cx - de.cx))
            ys, xs = np.nonzero(de.tinta)
            ys, xs = ys + de.y0, xs + de.x0
            self.assertTrue(np.array_equal(out[ys, xs + dx], self.img[ys, xs]), f"'{de.ch}' não chegou inteiro")
        for g in c.glifos:  # as outras letras ficam como estavam
            if g.i in (a.i, b.i):
                continue
            ys, xs = np.nonzero(g.tinta)
            self.assertTrue(np.array_equal(out[ys + g.y0, xs + g.x0], self.img[ys + g.y0, xs + g.x0]), g.ch)

    def test_laco_nao_cobre_a_tinta_que_fica(self):
        c, ds = self._contexto()
        g, viz = ds[0], ds[1]
        dx = viz.x0 - g.x1 - 1  # encosta a letra na vizinha: o papel em volta dela não pode passar por cima da vizinha
        tela = T._Tela(c, T._uniao(T._caixa_glifo(g, 10), T._caixa_glifo(viz, 10)))
        tela.apagar(T._furo_glifos(tela, [g]))
        T._mover_glifos(tela, [([g], dx, 0)], c.glifos)
        x0, y0 = tela.caixa[:2]
        ys, xs = np.nonzero(viz.tinta)
        self.assertTrue(np.array_equal(tela.px[ys + viz.y0 - y0, xs + viz.x0 - x0],
                                       self.img[ys + viz.y0, xs + viz.x0].astype(np.float32)))

    def test_copiar_glifo_em_multiplicar_mantem_o_traco(self):
        campo = self.campo("cpf/cnpj")
        res = self.rodar("caractere.copiar_glifo", campo, opcoes={"colagem": "tinta"}, acabamento=CRU)
        self.confere_rotulo(res, "caractere.copiar_glifo")
        self.confere_ocr(res, campo["caixa"])
        pesos = T._razao_peso(T._C(self.img, {"tecnica": "caractere.copiar_glifo", "campo": campo, "semente": 7},
                                   self.img, self.ctx, None, self.it.doc, None), res)
        self.assertIsNotNone(pesos)
        self.assertLess(abs(pesos[1] / pesos[0] - 1), 0.08, res.meta["descricao"])


class TestMultiplicar(unittest.TestCase):
    def test_cobertura_da_borda_e_a_do_alfa(self):
        papel, tinta = np.float32([200, 200, 200]), np.float32([50, 50, 50])
        a = np.float32([[0.0, 0.5, 1.0]])
        rec = papel * (1 - a[..., None]) + tinta * a[..., None]
        t = T._transmitancia(rec, a, papel)
        np.testing.assert_allclose(t[0, :, 0], [1.0, 0.625, 0.25], atol=1e-6)  # 1 − 0,5·(1 − 0,25): cobertura a
        destino = np.full((1, 3, 3), 180, np.float32)
        np.testing.assert_allclose((destino * t)[0, 2], 180 * 0.25)  # a tinta escurece o papel daqui na proporção dela

    def test_papel_de_la_nao_entra(self):
        papel = np.float32([200, 200, 200])
        rec = np.full((4, 4, 3), 203, np.float32)  # grão do papel de lá, mais claro
        rec[0, 0] = 196  # e um pouco mais escuro
        t = T._transmitancia(rec, np.zeros((4, 4), np.float32), papel)
        self.assertTrue(np.allclose(t, 1.0))


if __name__ == "__main__":
    unittest.main()
