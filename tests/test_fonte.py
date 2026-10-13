"""Identificador de fonte: acerta a fonte de linhas degradadas como um scan (inclusive fontes que a
lista fixa antiga não tinha), ignora fontes sem as letras do texto, usa o voto do documento e a API
devolve o ranking com o texto desenhado em cada fonte para comparar a olho."""
import base64
import io
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app import RAIZ, carregar_categorias, criar_app
from rotulador import identificar_fonte as idf
from rotulador.ops import auto, texto
from rotulador.registro import SPLITS, Registro
from rotulador.selecao import mascara_tinta
from tests.util import conta, salvar

# Georgia, Book Antiqua, Century Gothic e Consolas não estavam nas 24 candidatas fixas de antes
FONTES = ["Arial", "Arial Bold", "Verdana", "Times New Roman", "Courier New", "Georgia", "Tahoma", "Trebuchet MS",
          "Calibri", "Book Antiqua", "Century Gothic", "Consolas"]
TEXTOS = ["MARIA DA SILVA SANTOS", "Rua das Flores, 123 - Apto 45", "CPF 123.456.789-09", "Total a pagar"]


def linha_escaneada(nome: str, txt: str, rng: random.Random, cap: float | None = None):
    """``txt`` na fonte ``nome`` como sai de um scan: anti-serrilhado, desfoque, ruído e JPEG."""
    caminho, idx = texto.listar_fontes()[nome]
    cap = cap or rng.uniform(14, 26)  # altura de maiúscula (scan de 150 a 300 dpi)
    _, t, _, _ = ImageFont.truetype(caminho, 100, index=idx).getbbox("H", anchor="ls")
    S = 3
    f = ImageFont.truetype(caminho, cap / (-t / 100) * S, index=idx)
    l, t, r, b = f.getbbox(txt, anchor="ls")
    W, H = int((r - l) / S) + 60, int((b - t) / S) + 50
    a = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(a).text((30 * S - l, 25 * S - t), txt, fill=255, font=f, anchor="ls")
    a = cv2.GaussianBlur(cv2.resize(np.asarray(a, np.float32) / 255, (W, H), interpolation=cv2.INTER_AREA),
                         (0, 0), rng.uniform(0.35, 1.0))
    papel, tinta = rng.uniform(235, 252), rng.uniform(10, 60)
    img = papel * (1 - a[..., None]) + tinta * a[..., None] + np.random.default_rng(rng.randint(0, 999)).normal(0, 3.5, (H, W, 3))
    buf = io.BytesIO()
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=rng.randint(75, 92))
    img = np.asarray(Image.open(buf).convert("RGB")).copy()
    m = mascara_tinta(img).astype(bool)
    ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
    return img, [int(xs[0]) - 2, int(ys[0]) - 2, int(xs[-1]) + 3, int(ys[-1]) + 3]


def acertou(r: dict, nome: str, k: int = 1) -> bool:
    return any(nome == c["fonte"] or nome in c["parentes"] for c in r["ranking"][:k])


class TestIdentificar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fontes = [f for f in FONTES if f in texto.listar_fontes()]
        if len(cls.fontes) < 8:
            raise unittest.SkipTest("fontes do Windows não instaladas")

    def test_acerta_a_fonte_de_linhas_escaneadas(self):
        rng = random.Random(11)
        top1 = top3 = n = 0
        erros = []
        for nome in self.fontes:
            for txt in rng.sample(TEXTOS, 2):
                img, caixa = linha_escaneada(nome, txt, rng)
                r = idf.identificar(img, caixa, txt)
                n += 1
                top1 += acertou(r, nome)
                top3 += acertou(r, nome, 3)
                if not acertou(r, nome):
                    erros.append(f"{nome} -> {r['ranking'][0]['fonte']} ({txt})")
        self.assertGreaterEqual(top1 / n, 0.85, erros)
        self.assertGreaterEqual(top3 / n, 0.95, erros)

    def test_tamanho_e_largura_batem(self):
        im = Image.new("RGB", (900, 140), (250, 250, 248))
        ImageDraw.Draw(im).text((40, 30), "R$ 1.234,56 TOTAL", font=ImageFont.truetype("georgia.ttf", 60),
                                fill=(15, 15, 15))
        r = idf.identificar(np.asarray(im).copy(), [30, 20, 880, 120], "R$ 1.234,56 TOTAL")
        m = r["ranking"][0]
        self.assertEqual(m["fonte"], "Georgia")
        self.assertAlmostEqual(m["tamanho"], 60, delta=1)
        self.assertAlmostEqual(m["largura"], 1.0, delta=0.02)
        self.assertEqual(r["confianca"], "alta")

    def test_ignora_fontes_sem_as_letras(self):
        self.assertIn("Arial", idf.candidatas("Ação 123"))
        tibetano = idf.candidatas("ཀཁ")
        self.assertNotIn("Arial", tibetano)
        self.assertNotIn("Verdana", tibetano)
        self.assertFalse(any(idf.FORA.search(n) for n in idf.candidatas("abc")))  # nada de Wingdings/itálico

    def test_voto_do_documento(self):
        rng = random.Random(5)
        linhas, img = [], np.full((400, 900, 3), 248, np.uint8)
        y = 10
        for txt in TEXTOS:  # um "documento" todo em Georgia
            li, cx = linha_escaneada("Georgia", txt, rng, cap=18)
            h, w = li.shape[:2]
            img[y:y + h, 10:10 + w] = li
            linhas.append({"texto": txt, "caixa": [cx[0] + 10, cx[1] + y, cx[2] + 10, cx[3] + y], "conf": 0.95})
            y += h
        votos = idf.votos_documento(img, linhas)
        self.assertGreaterEqual(votos.get("Georgia", 0), 0.75, votos)
        # o voto soma PESO_DOCUMENTO à nota de quem o documento usa (desempate dos campos curtos)
        sem = idf.identificar(img, linhas[0]["caixa"], TEXTOS[0], top=40)
        segunda = sem["ranking"][1]["fonte"]
        com = idf.identificar(img, linhas[0]["caixa"], TEXTOS[0], prior={segunda: 1.0}, top=40)
        nota = lambda r, f: next(c["nota"] for c in r["ranking"] if c["fonte"] == f)  # noqa: E731
        self.assertAlmostEqual(nota(com, segunda) - nota(sem, segunda), idf.PESO_DOCUMENTO, places=3)

    def test_ajustar_fonte_traz_confianca_e_alternativas(self):
        img, caixa = linha_escaneada("Verdana", "Total a pagar", random.Random(2))
        r = auto.ajustar_fonte(img, caixa, "Total a pagar")
        self.assertEqual(r["fonte"], "Verdana")
        self.assertIn(r["confianca"], ("alta", "média", "baixa"))
        self.assertTrue(r["alternativas"])


class TestAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for i, sp in enumerate(SPLITS):
            salvar(conta(70 + i), self.tmp / "dados" / sp / f"a_{sp}.png")
        reg = Registro(self.tmp / "dados", self.tmp / "saida", log=lambda *_: None)
        self.c = criar_app(reg, self.tmp / "saida", carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()
        self.doc = reg.por_split["treino"][0].doc
        self.assertEqual(self.c.post("/api/abrir", json={"id": self.doc}).status_code, 200)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identificar_com_amostras(self):
        sel = {"formas": [{"tipo": "ret", "x0": 10, "y0": 124, "x1": 170, "y1": 150}]}  # "CEP 30123-456"
        r = self.c.post("/api/fonte/identificar", json={"id": self.doc, "selecao": sel, "texto": "CEP 30123-456"})
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["texto"], "CEP 30123-456")
        self.assertGreater(d["avaliadas"], 50)
        orig = Image.open(io.BytesIO(base64.b64decode(d["original"])))
        for c in d["ranking"]:
            self.assertIn(c["fonte"], texto.listar_fontes())
            am = Image.open(io.BytesIO(base64.b64decode(c["png"])))
            self.assertEqual(am.size, orig.size)  # mesmo recorte: dá para comparar lado a lado
            self.assertLess(np.asarray(am.convert("L")).min(), 120)  # tem texto desenhado
        self.assertEqual(self.c.post("/api/fonte/identificar", json={"id": self.doc, "texto": "x"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
