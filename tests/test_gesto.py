"""O gesto de quem edita (rotulador.gesto): movimento com velocidade em sino e tremor, pincel por toques com pressão,
texto ajustado nas setas, biblioteca de traços reais do editor manual e o carimbo por passadas."""
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from rotulador import gesto, humano
from rotulador.ops import apagar


class TestMovimento(unittest.TestCase):
    def test_velocidade_em_sino_e_fitts(self):
        m = gesto.movimento((0, 0), (300, 0), random.Random(2), 1.0)
        v = np.hypot(*np.diff(m[:, :2], axis=0).T) / np.diff(m[:, 2])
        pico = int(np.argmax(v)) / len(v)
        self.assertTrue(0.2 < pico < 0.8, pico)  # acelera, chega ao pico no meio e freia
        self.assertLess(v[:3].mean(), 0.5 * v.max())
        curto = gesto.movimento((0, 0), (30, 0), random.Random(2), 1.0)
        self.assertLess(curto[-1, 2], m[-1, 2])  # lei de Fitts: mais perto, mais rápido
        self.assertTrue(np.allclose(m[-1, :2], (300, 0), atol=4))  # chega ao alvo (o sobrepasso é corrigido)

    def test_tremor_menor_com_zoom(self):
        def tremor(escala):
            m = gesto.movimento((0, 0), (0, 400), random.Random(5), escala)
            return float(np.std(m[:, 0]))  # o movimento é vertical: o x é só curva + tremor
        self.assertLess(tremor(1 / 3), tremor(1.0))

    def test_laco_fecha_perto_e_cobre_a_caixa(self):
        pts = gesto.laco(50, 40, 350, 80, random.Random(1), 1.0)
        self.assertLess(np.hypot(*(pts[0, :2] - pts[-1, :2])), 15)
        m = gesto.rasterizar_laco((140, 420), pts)
        self.assertGreater(m[45:75, 60:340].mean(), 0.97)


class TestPincel(unittest.TestCase):
    def test_toques_espacados_com_pressao(self):
        traco = np.column_stack([np.linspace(0, 200, 50), np.full(50, 20.0), np.linspace(0, 400, 50)])
        tq = gesto.toques(traco, 10, random.Random(3), espacamento=0.25)
        passos = np.hypot(*np.diff(tq[:, :2], axis=0).T)
        self.assertAlmostEqual(float(np.median(passos)), 5.0, delta=0.6)  # 25% do diâmetro
        self.assertLess(tq[0, 2], tq[len(tq) // 2, 2])  # a pressão sobe no começo da passada
        a = gesto.alfa_toques((40, 220), tq)
        self.assertGreater(a[20, 20:180].min(), 0.9)

    def test_setas(self):
        e, n = gesto.setas(23.0, random.Random(1), 1.0, 1.0)
        self.assertLessEqual(abs(e), 2)
        self.assertLess(n, 23)  # o Shift anda de 10 em 10
        self.assertEqual(gesto.setas(0.2, random.Random(1)), (0, 0))


class TestSelecaoHumana(unittest.TestCase):
    def setUp(self):
        self.velho = np.zeros((80, 300), bool)
        self.velho[30:50, 40:260] = True
        self.tinta = self.velho.copy()

    def tearDown(self):
        gesto.usar_biblioteca(None)

    def test_modelo(self):
        for forma in ("laco", "pincel"):
            info = {}
            sel = humano.selecionar(self.velho, self.tinta, forma, (0.2,) * 4, 20.0, random.Random(4), zoom=2.0, info=info)
            self.assertTrue((sel | ~self.velho).all(), forma)  # sempre contém o texto antigo
            self.assertEqual(info["gesto"], "modelo")
            self.assertGreater(sel.sum(), self.velho.sum())

    def test_traco_real_da_biblioteca(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "treino" / "anotacoes").mkdir(parents=True)
            ang = np.linspace(0, 2 * np.pi, 40)
            pts = [[float(200 + 120 * np.cos(a)), float(100 + 30 * np.sin(a))] for a in ang]
            anot = {"doc": "x", "versao": 1, "operacoes": [{"tipo": "substituir_texto", "params": {"selecao": {"formas": [
                {"tipo": "poligono", "pontos": pts, "tempos": list(range(0, 400, 10))}]}}}]}
            (tmp / "treino" / "anotacoes" / "x__v01.json").write_text(json.dumps(anot), encoding="utf-8")
            gerada = dict(anot, gerador={"lote": "a"})  # versão do gerador: não entra (não é gesto de gente)
            (tmp / "treino" / "anotacoes" / "x__v02.json").write_text(json.dumps(gerada), encoding="utf-8")
            self.assertEqual(gesto.usar_biblioteca(tmp), {"laco": 1, "pincel": 0})
            self.assertEqual(len(gesto._BIB["laco"][0]["t"]), 40)
            usados = set()
            for s in range(12):
                info = {}
                humano.selecionar(self.velho, self.tinta, "laco", (0.2,) * 4, 20.0, random.Random(s), info=info)
                usados.add(info["gesto"])
            self.assertEqual(usados, {"real", "modelo"})  # ~70% real, o resto do modelo
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_descricao_tem_o_zoom(self):
        h = humano.sortear(random.Random(5), True, False)
        self.assertIn(h["zoom"], [z for z, _ in gesto.ZOOMS])
        self.assertIn("com zoom de", humano.descrever(h))
        self.assertIn("setas", humano.texto(h, random.Random(1), 0.8, 20.0))


class TestCarimboPorPassadas(unittest.TestCase):
    def test_apaga_e_mantem_o_grao(self):
        rng = np.random.default_rng(0)
        papel = np.clip(232 + cv2.GaussianBlur(rng.normal(0, 7, (120, 420)).astype(np.float32), (0, 0), 0.8), 0, 255)
        img = np.repeat(papel[..., None], 3, 2).astype(np.uint8)
        cv2.putText(img, "RUA DAS FLORES 120", (30, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 30, 30), 2)
        tinta = (img.mean(2) < 150)
        alvo = cv2.dilate(tinta.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        for modo in ("carimbo", "recuperar"):
            out = apagar.reconstruir(img, alvo, modo, 3, 24.0, alvo=alvo)
            self.assertLess(float((out.mean(2)[alvo] < 150).mean()), 0.01, modo)  # a tinta saiu
            g = out.astype(np.float32).mean(2)
            r = g - cv2.GaussianBlur(g, (0, 0), 1.0)
            fora = ~cv2.dilate(alvo.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
            self.assertGreater(float(r[alvo].std() / r[fora].std()), 0.6, modo)  # o papel continua com grão


if __name__ == "__main__":
    unittest.main()
