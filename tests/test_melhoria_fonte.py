"""Edição mais parecida com o documento original (auditoria de realismo): apaga só a tinta do texto antigo (a máscara
deixa de ser um retângulo), fundo local que segue a luz, texto sem hinting (borda e posição de subpixel), cor calibrada
pela tinta antiga, tamanho medido sem acento/cedilha, linha inclinada medida reta e desenhada inclinada, semelhança
absoluta com limiar, largura pelo espaçamento, negrito isotrópico, alinhamento pela tinta nítida, documento com ou sem
acento e o arquivo da fonte no rótulo."""
import random
import re
import unittest

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import identificar_fonte as idf
from rotulador import ocr, selecao
from rotulador.ops import auto, texto
from tests.test_fonte import linha_escaneada

PAPEL = (248, 247, 244)


def _f(nome, tam):
    return ImageFont.truetype(f"C:/Windows/Fonts/{nome}", tam)


def linha(txt, tam=40, ang=0.0, arquivo="arial.ttf", W=1300, H=240, tinta=(25, 25, 25), espaco=0.0, desfoque=0.0):
    """Uma linha de texto (anti-serrilhada) e a caixa que o detector de campos daria (ocr.caixa_da_linha)."""
    S = 4
    im = Image.new("L", (W * S, H * S), 0)
    d, f = ImageDraw.Draw(im), _f(arquivo, tam * S)
    for k, c in enumerate(txt if espaco else [txt]):  # com espaço extra, letra a letra (o kerning fica)
        d.text((60 * S + f.getlength(txt[:k]) + k * espaco * S, 140 * S), c, font=f, fill=255, anchor="ls")
    a = cv2.resize(np.asarray(im, np.float32) / 255, (W, H), interpolation=cv2.INTER_AREA)
    if ang:
        a = cv2.warpAffine(a, cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1), (W, H), flags=cv2.INTER_LINEAR)
    if desfoque:
        a = cv2.GaussianBlur(a, (0, 0), desfoque)
    img = np.float32(PAPEL) * (1 - a[..., None]) + np.float32(tinta) * a[..., None]
    img = np.clip(img + np.random.default_rng(3).normal(0, 2.0, img.shape) + 0.5, 0, 255).astype(np.uint8)
    m = ocr.mascara_tinta(img).astype(bool)
    ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
    return img, ocr.caixa_da_linha(img, [int(xs[0]) - 4, int(ys[0]) - 4, int(xs[-1]) + 5, int(ys[-1]) + 5])


def aplicar(img, res):
    out = img.copy()
    sub = out[res.y0:res.y1, res.x0:res.x1]
    sub[:] = np.where(res.pegada[..., None], res.recorte, sub)
    return out


def pegada_completa(img, res):
    m = np.zeros(img.shape[:2], bool)
    m[res.y0:res.y1, res.x0:res.x1] = res.pegada
    return m


def trocar(img, caixa, antigo, novo, **p):
    q = {"caixa": caixa, "texto": novo, "texto_antigo": antigo, "semente": 2, "igualar_ruido": False,
         "fontes": "top10", **p}
    res = auto.trocar_campo(img, q, selecao.vazia(*img.shape[:2]))
    return res, aplicar(img, res)


def lum(a):
    return a.astype(np.float32) @ np.float32([0.299, 0.587, 0.114])


class TestSoTinta(unittest.TestCase):
    """A: com ``so_tinta`` a máscara é a tinta (velha ∪ nova), não a caixa; nada fora dela muda."""

    def setUp(self):
        im = Image.new("RGB", (1100, 260), PAPEL)
        d = ImageDraw.Draw(im)
        d.text((60, 92), "Pagador", font=_f("arialbd.ttf", 13), fill=(40, 40, 40), anchor="ls")  # rótulo encostado
        d.text((60, 140), "SÃO JOSÉ DA CONCEIÇÃO", font=_f("arial.ttf", 34), fill=(30, 30, 30), anchor="ls")
        d.line([(30, 162), (1070, 162)], fill=(20, 20, 20), width=2)  # fio da tabela
        self.img = np.asarray(im).copy()
        self.caixa = ocr.caixa_da_linha(self.img, [50, 96, 600, 160])

    def test_mascara_e_a_tinta_e_o_resto_fica(self):
        x0, y0, x1, y1 = self.caixa
        aj = auto.ajustar_fonte(self.img, self.caixa, "SAO JOSE DA CONCEICAO", fontes=idf.fontes_top10())
        res, out = trocar(self.img, self.caixa, "SAO JOSE DA CONCEICAO", "PAULO HENRIQUE MOTA", so_tinta=True,
                          fonte_params=aj)
        self.assertTrue(res.meta["so_tinta"])
        self.assertEqual(res.meta["apagar"], "local")  # com so_tinta e sem apagador pedido: o fundo local
        peg = pegada_completa(self.img, res)
        self.assertFalse(((out != self.img).any(2) & ~peg).any())  # fora da pegada, nada muda
        self.assertLess(peg[y0:y1, x0:x1].mean(), 0.7)  # não é mais o retângulo
        res0, _ = trocar(self.img, self.caixa, "SAO JOSE DA CONCEICAO", "PAULO HENRIQUE MOTA", apagar="patchmatch",
                         fonte_params=aj)
        self.assertFalse(res0.meta["so_tinta"])
        self.assertGreater(pegada_completa(self.img, res0)[y0:y1, x0:x1].mean(), 0.85)  # sem so_tinta, como antes
        # a pegada é exatamente o buraco (tinta velha com halo) ∪ o alfa do texto novo
        ty0, ty1, tx0, tx1, a = texto.renderizar({**res.meta["ajuste"], "texto": "PAULO HENRIQUE MOTA",
                                                  "desfoque": 0}, *self.img.shape[:2])
        novo = np.zeros(self.img.shape[:2], bool)
        novo[ty0:ty1, tx0:tx1] = a > 0
        buraco = auto._buraco_tinta(self.img, x0, y0, x1, y1, aj["desfoque_scan"])
        self.assertTrue(np.array_equal(peg, buraco | novo))
        # rótulo e fio intactos; acentos, til e cedilha do texto antigo saíram junto (nada sobra em cima da linha)
        self.assertTrue(np.array_equal(out[75:96, 55:130], self.img[75:96, 55:130]))
        self.assertTrue(np.array_equal(out[160:165, 30:1070], self.img[160:165, 30:1070]))
        acima = out[y0 - 14:y0, x0:x1]
        self.assertLess(np.abs(lum(acima) - lum(np.full((1, 1, 3), PAPEL, np.uint8))[0, 0]).max(), 12)
        sobra = out[y0:y1 + 8, x0:x1][buraco[y0:y1 + 8, x0:x1] & ~novo[y0:y1 + 8, x0:x1]]
        self.assertGreater(lum(sobra).min(), 230)  # o papel no lugar da tinta velha, sem fantasma


class TestFundoLocal(unittest.TestCase):
    """B: o "local" segue a luz que varia no papel (foto); o chapado deixava degrau na borda do buraco."""

    def test_sem_degrau_num_gradiente(self):
        H, W = 120, 400
        yy, xx = np.mgrid[0:H, 0:W]
        fundo = 150 + 0.25 * xx + 0.15 * yy  # luz caindo na foto
        img = np.clip(np.repeat(fundo[..., None], 3, 2) + np.random.default_rng(1).normal(0, 2, (H, W, 3)), 0, 255)
        img = img.astype(np.uint8)
        buraco = np.zeros((H, W), bool)
        buraco[50:62, 60:340] = True  # um traço de texto apagado
        degrau = {}
        for modo in ("local", "chapado"):
            out = texto.apagar(img, selecao.de_mascara(buraco), 7, 1, None, modo)
            dentro = lum(out)[50:62, 60:340] - fundo[50:62, 60:340]
            degrau[modo] = float(np.abs(dentro).mean())
            self.assertTrue(np.array_equal(out[~buraco], img[~buraco]))
        self.assertLess(degrau["local"], 1.5)
        self.assertGreater(degrau["chapado"], 4 * degrau["local"])


class TestRenderizacao(unittest.TestCase):
    """C/H: sem hinting (posição em 1/4 de pixel nos dois eixos), negrito isotrópico, GDI como antes."""

    def _centro(self, p):
        y0, y1, x0, x1, a = texto.renderizar(p, 300, 1200)
        ys, xs = np.mgrid[y0:y1, x0:x1]
        return float((a * xs).sum() / a.sum()), float((a * ys).sum() / a.sum())

    def test_posicao_de_subpixel(self):
        p = {"texto": "HAMBURGO 2207", "fonte": "Arial", "tamanho": 40, "x": 100, "y": 150}
        cx, cy = self._centro(p)
        for d in (0.25, 0.5, 0.75):
            self.assertAlmostEqual(self._centro({**p, "y": 150 + d})[1] - cy, d, delta=0.05)
            self.assertAlmostEqual(self._centro({**p, "x": 100 + d})[0] - cx, d, delta=0.05)
        # o GDI continua preso no pixel inteiro (imita a ferramenta)
        g = {**p, "rasterizador": "gdi"}
        self.assertTrue(np.array_equal(texto.renderizar(g, 300, 1200)[4], texto.renderizar({**g, "y": 150.25}, 300, 1200)[4]))

    def test_borda_horizontal_nao_mais_nitida_que_a_vertical(self):
        """Com hinting, as bordas horizontais saíam mais nítidas que as verticais (h/v 1,07-1,09 nestas fontes)."""
        def nitidez(g, o, m):  # Σg²/Σg nas bordas predominantemente nessa direção
            v = g[m & (g > 0.025) & (g > 1.5 * o)]
            return float((v * v).sum() / v.sum())
        for fonte, tam in (("Calibri", 41.7), ("Courier New", 35.2), ("Verdana", 27.4)):
            hs, vs = [], []
            for dy in (0.0, 0.25, 0.5, 0.75):
                *_, a = texto.renderizar({"texto": "MARIA DOS SANTOS, RUA DAS FLORES 107", "fonte": fonte,
                                          "tamanho": tam, "x": 20.3, "y": 60 + dy}, 200, 1400)
                m = cv2.dilate((a > 0.3).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
                gy = np.abs(cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)) / 4
                gx = np.abs(cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)) / 4
                hs.append(nitidez(gy, gx, m))
                vs.append(nitidez(gx, gy, m))
            self.assertLess(np.mean(hs) / np.mean(vs), 1.04, fonte)

    def test_negrito_isotropico(self):
        p = {"texto": "MARIA", "fonte": "Arial", "tamanho": 40, "x": 50, "y": 100}
        y0, y1, x0, x1, a = texto.renderizar(p, 200, 400)
        b = texto.renderizar({**p, "negrito": 2.0}, 200, 400)
        alt = lambda r: np.flatnonzero((r[4] > 0.5).any(1)).size  # noqa: E731
        larg = lambda r: np.flatnonzero((r[4] > 0.5).any(0)).size  # noqa: E731
        self.assertGreaterEqual(alt(b) - alt((y0, y1, x0, x1, a)), 1.5)  # engorda na vertical também
        self.assertGreaterEqual(larg(b) - larg((y0, y1, x0, x1, a)), 1.5)

    def test_espacamento_mantem_o_kerning(self):
        p = {"texto": "AVATAR", "fonte": "Arial", "tamanho": 40, "x": 50, "y": 100}
        f = ImageFont.truetype(texto.listar_fontes()["Arial"][0], 160)
        com_kerning = f.getlength("AVATAR") / 4  # o "AV" e o "TA" se encaixam
        _, _, x0, x1, _ = texto.renderizar({**p, "espacamento": 2}, 200, 600)
        self.assertAlmostEqual(x1 - x0, com_kerning + 5 * 2, delta=4)


class TestCorDaTinta(unittest.TestCase):
    """D: traço fino num scan borrado: a cor medida, borrada de novo, deixava o texto novo mais claro."""

    def test_tinta_nova_tao_escura_quanto_a_velha(self):
        img, caixa = linha("PEDRO HENRIQUE SOUZA", tam=34, arquivo="cour.ttf", tinta=(70, 70, 70), desfoque=0.9)
        aj = auto.ajustar_fonte(img, caixa, "PEDRO HENRIQUE SOUZA", fontes=idf.fontes_top10())
        res, out = trocar(img, caixa, "PEDRO HENRIQUE SOUZA", "MARCOS VINICIUS LIMA", so_tinta=True,
                          desfoque=aj["desfoque_scan"], fonte_params=aj)
        self.assertTrue(res.meta["cor_calibrada"])
        self.assertEqual(res.meta["ajuste"]["cor"], res.meta["cor"])

        def p10(im):
            L = cv2.GaussianBlur(lum(im), (0, 0), 0.8)
            return float(np.percentile(L[ocr.tinta_da_linha(im)], 10))
        x0, y0, x1, y1 = caixa
        velho = p10(img[y0 - 3:y1 + 3, x0 - 3:x1 + 3])
        novo = p10(out[y0 - 3:y1 + 3, x0 - 3:x0 + 3 + int(0.9 * (x1 - x0))])
        self.assertLess(abs(novo - velho), 4, (velho, novo))
        # a cor dada à mão não é mexida
        res2, _ = trocar(img, caixa, "PEDRO HENRIQUE SOUZA", "MARCOS VINICIUS LIMA", so_tinta=True, cor="#404040",
                         fonte_params=aj)
        self.assertFalse(res2.meta["cor_calibrada"])
        self.assertEqual(res2.meta["cor"], "#404040")


class TestAcentoECedilha(unittest.TestCase):
    """E: o OCR lê sem acento; a cedilha (e o acento do texto certo) não podem inflar o tamanho medido."""

    def test_tamanho_sem_diacriticos(self):
        for desenhado, lido in (("MARIA DA ASSUNÇÃO LIMA", "MARIA DA ASSUNCAO LIMA"),
                                ("PRAÇA DA CONCEIÇÃO, 45", "PRACA DA CONCEICAO, 45"),
                                ("SÃO JOSÉ DOS CAMPOS", "SÃO JOSÉ DOS CAMPOS")):
            img, caixa = linha(desenhado, tam=40)
            aj = auto.ajustar_fonte(img, caixa, lido, fontes=idf.fontes_top10())
            self.assertTrue(aj["fonte"] == "Arial" or "Arial" in aj["parentes"], (desenhado, aj["fonte"]))
            self.assertAlmostEqual(aj["tamanho"], 40, delta=2.0, msg=desenhado)
            self.assertAlmostEqual(aj["largura"], 100, delta=3, msg=desenhado)

    def test_faixa_ignora_cedilha(self):
        img, _ = linha("PRAÇA DA CONCEIÇÃO", tam=40)
        t = ocr.tinta_da_linha(img[60:180, 20:1200])
        topo, base = ocr.faixa_da_linha(t, "PRACA DA CONCEICAO")
        ys = np.flatnonzero(t.any(1))
        self.assertGreater(ys[-1] - base, 5)  # a cedilha passa da faixa
        self.assertAlmostEqual(base - topo + 1, 29, delta=1.5)  # altura da maiúscula da Arial 40


class TestInclinacao(unittest.TestCase):
    """F: linha inclinada medida reta (mesma fonte e tamanho) e o texto novo desenhado com a mesma inclinação."""

    def test_girada(self):
        top = idf.fontes_top10()
        txt = "JOSE CARLOS RIBEIRO DA SILVA"
        img, caixa = linha(txt, tam=44)
        reta = auto.ajustar_fonte(img, caixa, txt, fontes=top)
        self.assertEqual(reta["rotacao"], 0.0)
        for ang in (1.2, 3.0):
            img, caixa = linha(txt, tam=44, ang=ang)
            aj = auto.ajustar_fonte(img, caixa, txt, fontes=top)
            self.assertEqual(aj["fonte"], reta["fonte"], ang)
            self.assertAlmostEqual(aj["tamanho"], reta["tamanho"], delta=0.05 * reta["tamanho"], msg=ang)
            self.assertAlmostEqual(aj["largura"], reta["largura"], delta=5, msg=ang)
            self.assertAlmostEqual(aj["rotacao"], ang, delta=0.15)
            res, out = trocar(img, caixa, txt, "MARCOS VINICIUS ALMEIDA", so_tinta=True, fonte_params=aj)
            self.assertEqual(res.meta["ajuste"]["rotacao"], aj["rotacao"])
            self.assertAlmostEqual(ocr.inclinacao(ocr.mascara_tinta(out).astype(bool)), ang, delta=0.15)
            # o texto novo começa onde o antigo começava (pé da 1ª letra na linha de base inclinada)
            antes, depois = ocr.mascara_tinta(img).astype(bool), ocr.mascara_tinta(out).astype(bool)
            col = lambda m: int(np.flatnonzero(m.any(0))[0])  # noqa: E731
            self.assertLessEqual(abs(col(antes) - col(depois)), 2)


class TestSemelhancaAbsoluta(unittest.TestCase):
    """G: semelhança absoluta (0..1) com limiar: a fonte do TOP10 passa, a de fora não (e não tem confiança alta)."""

    def test_limiar(self):
        top = idf.fontes_top10()
        self.assertTrue(0.5 < idf.LIMIAR_MESMA_FONTE < 1.0)
        rng = random.Random(4)
        img, caixa = linha_escaneada("Arial", "RUA DAS FLORES, 123", rng, cap=20)
        aj = auto.ajustar_fonte(img, caixa, "RUA DAS FLORES, 123", fontes=top)
        self.assertGreaterEqual(aj["semelhanca"], idf.LIMIAR_MESMA_FONTE)
        self.assertLessEqual(aj["semelhanca"], 1.0)
        fora = [f for f in ("Bahnschrift", "Consolas") if f in texto.listar_fontes()]
        if not fora:
            self.skipTest("Bahnschrift/Consolas não instaladas")
        for nome in fora:
            img, caixa = linha_escaneada(nome, "MARIA DA SILVA SANTOS", rng, cap=20)
            r = idf.identificar(img, caixa, "MARIA DA SILVA SANTOS", fontes=top)
            self.assertLess(r["ranking"][0]["semelhanca"], idf.LIMIAR_MESMA_FONTE, nome)
            self.assertEqual(r["confianca"], "baixa", nome)


class TestLarguraEAlinhamento(unittest.TestCase):
    """H: a largura casa primeiro pelo espaço entre as letras; alinhamento pela tinta nítida (direita e centro)."""

    def test_espacamento_antes_da_escala(self):
        txt = "ANA PAULA FERREIRA COSTA"
        img, caixa = linha(txt, tam=36, espaco=2.0)
        aj = auto.ajustar_fonte(img, caixa, txt, fontes=idf.fontes_top10())
        self.assertEqual(aj["fonte"], "Arial")
        self.assertAlmostEqual(aj["espacamento"], 2.0, delta=0.5)
        self.assertAlmostEqual(aj["largura"], 100, delta=2)  # sem esticar as letras

    def test_alinhamento_pela_tinta_nitida(self):
        txt = "RUA DAS FLORES, 1234"
        img, caixa = linha(txt, tam=36, desfoque=0.8)
        aj = auto.ajustar_fonte(img, caixa, txt, fontes=idf.fontes_top10())
        antes = ocr.mascara_tinta(img).astype(bool)
        cols = np.flatnonzero(antes.any(0))
        for modo, novo in (("direita", "AV BRASIL, 77"), ("centro", "TRAVESSA SANTA LUZIA, 1")):
            res, out = trocar(img, caixa, txt, novo, so_tinta=True, alinhar=modo, fonte_params=aj,
                              desfoque=aj["desfoque_scan"])
            self.assertEqual(res.meta["alinhar"], modo)
            c2 = np.flatnonzero(ocr.mascara_tinta(out).astype(bool).any(0))
            if modo == "direita":
                self.assertLessEqual(abs(int(c2[-1]) - int(cols[-1])), 1)
            else:
                self.assertLessEqual(abs((c2[0] + c2[-1]) / 2 - (cols[0] + cols[-1]) / 2), 1.0)


class TestUsaAcentos(unittest.TestCase):
    """I: o documento escreve com acento? (o OCR devolve sem; a tinta diz)."""

    def _doc(self, linhas, tam=30):
        im = Image.new("RGB", (1000, 80 + 60 * len(linhas)), PAPEL)
        d = ImageDraw.Draw(im)
        caixas = []
        for i, t in enumerate(linhas):
            y = 70 + 60 * i
            d.text((40, y), t, font=_f("arial.ttf", tam), fill=(20, 20, 20), anchor="ls")
            l, tt, r, b = d.textbbox((40, y), t, font=_f("arial.ttf", tam), anchor="ls")
            caixas.append({"caixa": [l - 3, tt - 3, r + 3, b + 3], "texto": ocr.sem_acento(t)})  # como o OCR lê
        return np.asarray(im).copy(), caixas

    def test_com_e_sem_acento(self):
        com = ["JOSÉ DA CONCEIÇÃO", "PRAÇA SÃO JOÃO, 10", "BELO HORIZONTE - MG"]
        img, linhas = self._doc(com)
        self.assertIs(ocr.usa_acentos(img, linhas=linhas), True)
        img, linhas = self._doc([ocr.sem_acento(t) for t in com])
        self.assertIs(ocr.usa_acentos(img, linhas=linhas), False)
        img, linhas = self._doc(["MARIA DA SILVA", "RUA DAS FLORES, 10"])  # nada que exija acento: não dá para dizer
        self.assertIsNone(ocr.usa_acentos(img, linhas=linhas))
        img, linhas = self._doc(com, tam=9)  # letra miúda demais para ver o sinal
        self.assertIsNone(ocr.usa_acentos(img, linhas=linhas))


class TestArquivoDaFonte(unittest.TestCase):
    """J: o rótulo diz qual ARQUIVO de fonte desenhou o texto (nome + sha1)."""

    def test_no_ajuste_e_no_meta(self):
        img, caixa = linha("MARIA DOS SANTOS", tam=36)
        aj = auto.ajustar_fonte(img, caixa, "MARIA DOS SANTOS", fontes=idf.fontes_top10())
        arq = aj["arquivo_fonte"]
        self.assertRegex(arq, r"^[^:]+\.(ttf|otf|ttc)(#\d+)?:[0-9a-f]{12}$")
        caminho, _idx = texto.listar_fontes()[aj["fonte"]]
        self.assertTrue(arq.startswith(re.sub(r".*[\\/]", "", caminho)))
        res, _ = trocar(img, caixa, "MARIA DOS SANTOS", "ANA LIMA", fonte_params=aj, so_tinta=True)
        self.assertEqual(res.meta["arquivo_fonte"], arq)


if __name__ == "__main__":
    unittest.main()
