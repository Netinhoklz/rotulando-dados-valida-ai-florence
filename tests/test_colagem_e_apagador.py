"""Letra movida e letra colada (o laço do Ctrl+J e o modo Multiplicar) e o apagador do PhotoCraft nas técnicas parciais.

O laço e o Multiplicar rodam sempre; o apagador do motor só com o ``photocraft-cli`` instalado."""
import unittest
from unittest import mock

import numpy as np

from rotulador import photocraft
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


class TestAvaliarPapel(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1)
        self.rec = np.clip(220 + rng.normal(0, 3, (80, 120, 1)).repeat(3, 2), 0, 255).astype(np.uint8)
        self.sel = np.zeros((80, 120), bool)
        self.sel[30:50, 40:80] = True
        self.proibido = np.zeros_like(self.sel)
        self.proibido[28:52, 38:82] = True

    def test_papel_bom_passa(self):
        av = photocraft.avaliar_papel(self.rec, self.rec, self.sel, self.proibido, 20)
        self.assertAlmostEqual(av[0], 1.0, delta=0.2)
        self.assertIsNone(photocraft._manchou(av))

    def test_liso_e_mancha(self):
        liso = self.rec.copy()
        liso[self.sel] = 220
        self.assertIn("liso", photocraft._manchou(photocraft.avaliar_papel(liso, self.rec, self.sel, self.proibido, 20)))
        escuro = self.rec.astype(np.int16)
        escuro[self.sel] -= 9
        self.assertIn("escura", photocraft._manchou(photocraft.avaliar_papel(escuro.astype(np.uint8), self.rec, self.sel,
                                                                             self.proibido, 20)))

    def test_costura_num_pedaco_da_borda(self):
        """Um degrau de 6 níveis só no quarto direito da seleção: a média da área fica abaixo do limite do tom, mas a
        borda denuncia (a Recuperação do motor deixava um risco assim)."""
        costura = self.rec.astype(np.int16)
        costura[30:50, 70:80] += 6
        av = photocraft.avaliar_papel(costura.astype(np.uint8), self.rec, self.sel, self.proibido, 10)
        self.assertLess(abs(av[1]), photocraft.TOM_MAXIMO)
        self.assertIn("costura", photocraft._manchou(av))

    def test_papel_digital_nao_tem_grao_a_perder(self):
        branco = np.full((80, 120, 3), 255, np.uint8)
        self.assertIsNone(photocraft._manchou(photocraft.avaliar_papel(branco, branco, self.sel, self.proibido, 20)))


class TestMotorDaVersao(Base):
    def test_sem_o_motor_apaga_no_python(self):
        with mock.patch.object(photocraft, "disponivel", return_value=False):
            res = self.rodar("caractere.transpor", self.campo("valor"), motor="photocraft",
                             opcoes={"apagar": "patchmatch"}, acabamento=CRU, conferir_ocr=False)
        self.assertEqual(res.meta["fornecedores"]["apagar"], "patchmatch")
        self.assertNotIn("motor", res.meta["fornecedores"])


@unittest.skipUnless(photocraft.disponivel(), "photocraft-cli não instalado")
class TestApagadorDoMotor(Base):
    def test_parciais_apagam_no_photocraft(self):
        for tid, opcoes in (("caractere.transpor", {"apagar": "patchmatch"}),
                            ("caractere.redesenhar", {"apagar": "carimbo"}),
                            ("caractere.apagar", {"apagar": "recuperar", "vao": "fechar"})):
            with self.subTest(tecnica=tid):
                res = self.rodar(tid, self.campo("valor"), motor="photocraft", opcoes=opcoes, acabamento=CRU,
                                 conferir_ocr=False)
                f = res.meta["fornecedores"]
                self.assertEqual(f["motor"], "photocraft")
                pedido = photocraft.DO_HUMANO[opcoes["apagar"]]
                self.assertIn(f["apagar"], (f"photocraft.{pedido}", "photocraft.carimbo"))
                self.assertEqual(f["apagar"] == "photocraft.carimbo" and pedido != "carimbo", "refez" in f, f)

    def test_reconstruir_apaga_so_a_selecao(self):
        c = T._C(self.img, {"tecnica": "caractere.transpor", "campo": self.campo("valor"), "semente": 3},
                 self.img, self.ctx, None, self.it.doc, None)
        g = next(g for g in c.glifos if g.ch.isdigit())
        M = 60
        y0, x0 = g.y0 - M, g.x0 - M
        rec = np.ascontiguousarray(self.img[y0:g.y1 + M, x0:g.x1 + M])
        alvo = np.zeros(rec.shape[:2], bool)
        T._pintar(alvo, x0, y0, g)
        alvo = T._dilatar(alvo, 3)
        buraco = T._dilatar(T.mascara_tinta(rec).astype(bool), 2) | alvo
        for ferramenta in photocraft.APAGADORES:
            with self.subTest(ferramenta=ferramenta):
                out, sobre = photocraft.reconstruir(rec, buraco, alvo, ferramenta, 5, float(g.altura))
                lum = out.astype(np.float32) @ photocraft._LUMA
                self.assertFalse((alvo & (lum < 150)).any(), "sobrou tinta")
                longe = ~T._dilatar(alvo, 2)
                self.assertTrue(np.array_equal(out[longe], rec[longe]), "mexeu fora da seleção")
                if ferramenta != "cor":
                    self.assertIsNone(photocraft._manchou(photocraft.avaliar_papel(
                        out, rec, alvo, T._dilatar(buraco, 2), float(g.altura))))

    def test_refaz_com_o_carimbo(self):
        c = T._C(self.img, {"tecnica": "caractere.transpor", "campo": self.campo("valor"), "semente": 3},
                 self.img, self.ctx, None, self.it.doc, None)
        g = next(g for g in c.glifos if g.ch.isdigit())
        y0, x0 = g.y0 - 60, g.x0 - 60
        rec = np.ascontiguousarray(self.img[y0:g.y1 + 60, x0:g.x1 + 60])
        alvo = np.zeros(rec.shape[:2], bool)
        T._pintar(alvo, x0, y0, g)
        alvo = T._dilatar(alvo, 3)
        buraco = T._dilatar(T.mascara_tinta(rec).astype(bool), 2) | alvo
        vistos = []

        def manchou(av):  # a 1ª ferramenta "mancha"; o Carimbo, não
            vistos.append(av)
            return "mancha mais escura (-9.0 níveis)" if len(vistos) == 1 else None
        with mock.patch.object(photocraft, "_manchou", side_effect=manchou):
            _, sobre = photocraft.reconstruir(rec, buraco, alvo, "preenchimento", 5, float(g.altura))
        self.assertEqual(sobre["ferramenta"], "carimbo")
        self.assertEqual(sobre["refez"]["de"], "preenchimento")


if __name__ == "__main__":
    unittest.main()
