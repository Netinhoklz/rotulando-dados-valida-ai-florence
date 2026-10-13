"""Ferramentas do editor manual revistas em 12/10/2026 (bateria em E:/rotulador/ferr/bateria_editor.py): o balde e a
varinha aceitam imagem somente leitura, a mescla da recuperação/remendo não puxa a cor da tinta nem dos fios da borda,
o "igualar ruído" põe grão com o espectro do papel (não branco), e o código regenerado sai na cor do original e é
recusado quando não cabe legível."""
import unittest

import cv2
import numpy as np

from rotulador import selecao
from rotulador.ops import executar, pro
from rotulador.ops.tecnicas import MODULO_MINIMO_PX
from rotulador.selecao import vazia
from rotulador.sessao import ErroOperacao


def papel_correlacionado(h, w, semente=0):
    g = np.random.default_rng(semente)
    p = 228 + cv2.GaussianBlur(g.normal(0, 7, (h, w)).astype(np.float32), (0, 0), 1.0)
    return np.repeat(np.clip(p, 0, 255)[..., None], 3, 2).astype(np.uint8)


class TestSomenteLeitura(unittest.TestCase):
    def test_balde_e_varinha(self):
        img = np.full((60, 80, 3), 240, np.uint8)
        img[20:40, 20:40] = 10
        img.flags.writeable = False  # a original da sessão é travada
        r = executar("balde", img, {"x": 5, "y": 5, "cor": "#ff0000"}, vazia(60, 80))
        self.assertEqual(int(r.pegada.sum()), 60 * 80 - 400)
        s = selecao.rasterizar({"formas": [{"tipo": "varinha", "x": 5, "y": 5, "tolerancia": 20}]}, img)
        self.assertFalse(s.vazia)


class TestMesclaDeMembrana(unittest.TestCase):
    def test_tinta_e_fio_da_borda_nao_entram(self):
        dst = np.full((80, 120, 3), 235, np.uint8)
        dst[60:62, :] = (0, 84, 147)  # fio azul logo abaixo da área
        dst[30:42, 30:90] = 20  # o texto a cobrir
        fonte = np.full((80, 120, 3), 235, np.uint8)
        m = np.zeros((80, 120), bool)
        m[26:60, 24:96] = True  # a borda de baixo encosta no fio
        out = pro.poisson(dst, fonte, m)
        miolo = out[28:58, 28:92].astype(np.float32)
        self.assertLess(np.abs(miolo - 235).max(), 4)  # nem fantasma do texto nem azul/laranja do fio
        self.assertTrue((out[~m] == dst[~m]).all())

    def test_tom_do_destino(self):
        dst = np.full((60, 60, 3), 210, np.uint8)
        fonte = papel_correlacionado(60, 60)
        m = np.zeros((60, 60), bool)
        m[15:45, 15:45] = True
        out = pro.poisson(dst, fonte, m)
        self.assertLess(abs(float(out[m].mean()) - 210), 3)
        self.assertGreater(float(out[20:40, 20:40].std()), 0.8 * float(fonte[20:40, 20:40].std()))  # a textura fica


class TestIgualarRuidoEspectral(unittest.TestCase):
    def test_grao_com_a_correlacao_do_papel(self):
        base = papel_correlacionado(240, 320, 1)
        img = base.copy()
        img[100:140, 80:240] = 228  # área lisa (um retângulo da cor do papel)
        sel = selecao.rasterizar({"formas": [{"tipo": "ret", "x0": 80, "y0": 100, "x1": 240, "y1": 140}]}, img)
        hp = lambda a: (a.astype(np.float32).mean(2) - cv2.GaussianBlur(a.astype(np.float32).mean(2), (0, 0), 2))  # noqa: E731
        lag = lambda r: float(np.corrcoef(r[:, :-1].ravel(), r[:, 1:].ravel())[0, 1])  # noqa: E731
        ref = hp(base)[10:80, 20:300]
        for espectro, lag_min in (("papel", 0.15), ("branco", -1)):
            r = executar("ajuste", img, {"ajuste": "igualar_ruido", "espectro": espectro}, sel, base=base)
            out = img.copy()
            s = out[r.y0:r.y1, r.x0:r.x1]
            s[:] = np.where(r.pegada[..., None], r.recorte, s)
            d = hp(out)[104:136, 84:236]
            if espectro == "papel":
                self.assertGreater(lag(d), lag_min)
                self.assertGreater(np.std(d) / np.std(ref), 0.85)
            else:
                self.assertLess(lag(d), lag(ref) - 0.15)  # o branco não tem a correlação do papel


class TestCodigoComoOOriginal(unittest.TestCase):
    def test_cor_e_recusa(self):
        from rotulador.ops import auto, geradores
        img = np.full((120, 900, 3), 240, np.uint8)
        img[20:100, 30:860:6] = 90  # "código" cinza do scan
        cod = geradores.par_codigo(True)[0]
        r = auto.trocar_campo(img, {"caixa": [30, 20, 860, 100], "texto": cod, "tipo": "barras", "igualar_ruido": False},
                              vazia(120, 900))
        self.assertGreater(float(np.percentile(r.recorte.astype(np.float32).mean(2), 5)), 60)  # cinza, não #000
        estreito = {"tecnica": "codigo.regenerar", "campo": {"id": "b0", "caixa": [30, 20, 30 + int(405 * (MODULO_MINIMO_PX - 0.2)), 100],
                                                             "texto": "(código de barras)", "categoria": "codigo barras",
                                                             "tipo": "barras"}, "semente": 1}
        with self.assertRaisesRegex(ErroOperacao, "não cabe legível"):
            executar("tecnica", img, estreito, vazia(120, 900), base=img)


if __name__ == "__main__":
    unittest.main()
