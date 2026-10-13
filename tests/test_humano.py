"""Técnicas "do mundo real" que faltavam: esconder com um objeto na foto, rabiscar, borrar/pixelizar,
corretivo em pincelada ou líquido, caneta nos traços e recorte de papel colado. Cada uma mexe só
perto do campo, deixa o texto antigo ilegível (quando é o caso) e está num jeito do modo fácil e nas
ferramentas de algum falsificador."""
import unittest

import numpy as np

from rotulador import catalogo, falsificador as fz
from rotulador.ops import executar
from rotulador.selecao import vazia
from rotulador.sessao import ErroOperacao
from tests.test_auto import TEM_OCR, conta

CAIXA = [330, 290, 560, 318]  # "30123-456" (CEP) na conta sintética de tests.test_auto
CAMPO = {"id": "t2", "caixa": CAIXA, "texto": "30123-456", "categoria": "cep", "tipo": "texto"}


def aplicar(img, tecnica, opcoes=None, semente=1, **extra):
    p = {"tecnica": tecnica, "campo": CAMPO, "semente": semente, "opcoes": opcoes or {}, "acabamento": {"ruido": "nunca", "disfarce": "nunca"},
         "conferir_ocr": False, **extra}
    return executar("tecnica", img, p, vazia(*img.shape[:2]), base=img)


def aplicado(img, res):
    out = img.copy()
    out[res.y0:res.y1, res.x0:res.x1] = np.where(res.pegada[..., None], res.recorte, out[res.y0:res.y1, res.x0:res.x1])
    return out


class TestCatalogo(unittest.TestCase):
    def test_novas_no_catalogo_e_nos_jeitos(self):
        for tid, num in (("campo.ocultar", 33), ("campo.rabiscar", 34), ("campo.borrar", 35)):
            t = catalogo.TECNICAS[tid]
            self.assertEqual((t.num, t.familia, t.nivel, t.automatica), (num, "remocao", "simples", True))
            self.assertEqual(set(t.tipos), {"texto", "barras", "qr"})
        apagar = next(j for j in catalogo.JEITOS if j["id"] == "apagar")
        self.assertLessEqual({"campo.ocultar", "campo.rabiscar", "campo.borrar"}, set(apagar["tecnicas"]))
        self.assertIn("papel", [v for v, _ in catalogo.COLAGEM["valores"]])
        self.assertIn("forma", catalogo.TECNICAS["campo.corretivo"].opcoes)
        self.assertIn("tinta", catalogo.TECNICAS["caractere.acrescentar_tracos"].opcoes)
        self.assertEqual(len([t for t in catalogo.TECNICAS.values() if t.automatica]), 30)  # + campo.letras_do_documento


class TestTecnicas(unittest.TestCase):
    def setUp(self):
        self.img = conta(7)

    def _fora_intacto(self, res, folga_x, folga_y, nome):
        x0, y0, x1, y1 = CAIXA
        H, W = self.img.shape[:2]
        novo = aplicado(self.img, res)
        mudou = np.abs(novo.astype(int) - self.img.astype(int)).max(2) > 0
        longe = np.ones((H, W), bool)
        longe[max(0, y0 - folga_y):min(H, y1 + folga_y), max(0, x0 - folga_x):min(W, x1 + folga_x)] = False
        self.assertFalse((mudou & longe).any(), f"{nome}: mexeu longe do campo")
        self.assertTrue(mudou[y0:y1, x0:x1].mean() > 0.3, f"{nome}: cobriu pouco do campo ({mudou[y0:y1, x0:x1].mean():.2f})")
        return novo

    def test_ocultar_cada_objeto(self):
        h = CAIXA[3] - CAIXA[1]
        for objeto in ("adesivo", "postit", "fita"):
            res = aplicar(self.img, "campo.ocultar", {"objeto": objeto})
            self._fora_intacto(res, int(3 * h), int(3 * h), objeto)
            self.assertEqual(res.meta["texto_novo"], "")
            self.assertIn(objeto if objeto != "fita" else "fita", res.meta["descricao"].replace("post-it", "postit"))
        res = aplicar(self.img, "campo.ocultar", {"objeto": "dedo"})  # o dedo sai para a borda: o corpo é comprido
        novo = aplicado(self.img, res)
        self.assertTrue((np.abs(novo.astype(int) - self.img.astype(int)).max(2) > 0)[CAIXA[1]:CAIXA[3], CAIXA[0]:CAIXA[2]].mean() > 0.4)
        self.assertIn("dedo", res.meta["descricao"])

    def test_rabiscar_e_borrar(self):
        h = CAIXA[3] - CAIXA[1]
        for tinta in ("caneta", "marcador", "pincel_app"):
            res = aplicar(self.img, "campo.rabiscar", {"tinta": tinta})
            self._fora_intacto(res, int(h), int(h), tinta)
        for modo in ("desfoque", "mosaico"):
            res = aplicar(self.img, "campo.borrar", {"modo": modo})
            novo = self._fora_intacto(res, int(h), int(h), modo)
            # o texto perdeu nitidez: o contraste local caiu
            rec_a, rec_b = self.img[CAIXA[1]:CAIXA[3], CAIXA[0]:CAIXA[2]], novo[CAIXA[1]:CAIXA[3], CAIXA[0]:CAIXA[2]]
            self.assertLess(np.abs(np.diff(rec_b.astype(int), axis=1)).mean(), 0.7 * np.abs(np.diff(rec_a.astype(int), axis=1)).mean(), modo)

    def test_corretivo_formas(self):
        h = CAIXA[3] - CAIXA[1]
        for forma in ("retangulo", "pincelada", "liquido"):
            res = aplicar(self.img, "campo.corretivo", {"forma": forma, "fundo": "papel"})
            novo = self._fora_intacto(res, int(1.6 * h), int(1.6 * h), forma)
            rec = novo[CAIXA[1]:CAIXA[3], CAIXA[0]:CAIXA[2]]
            self.assertGreater(rec.mean(), 200, forma)  # ficou claro (o texto sumiu)
            self.assertEqual(res.meta["opcoes"]["forma"], forma)
        liq = aplicar(self.img, "campo.corretivo", {"forma": "liquido", "fundo": "papel"})
        self.assertIn("líquido", liq.meta["descricao"])
        rec = aplicado(self.img, liq)[CAIXA[1]:CAIXA[3], CAIXA[0]:CAIXA[2]].astype(float)
        self.assertGreater(rec.std(), 1.0)  # textura, não liso

    @unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
    def test_texto_fica_ilegivel(self):
        import re
        from rotulador import ocr
        from rotulador.ops.tecnicas import lido_igual
        x0, y0, x1, y1 = CAIXA
        m = 6
        rec = lambda im: im[y0 - m:y1 + m, x0 - m:x1 + m]  # noqa: E731 — o reconhecedor, como em _conferir
        if not lido_igual(ocr.reconhecer(rec(self.img)), "30123-456"):
            self.skipTest("o OCR não lê o campo original desta conta sintética")
        for tid, op in (("campo.ocultar", {"objeto": "adesivo"}), ("campo.rabiscar", {"tinta": "marcador"}), ("campo.borrar", {"modo": "mosaico"})):
            novo = aplicado(self.img, aplicar(self.img, tid, op))
            lido = ocr.reconhecer(rec(novo))
            self.assertFalse(lido_igual(lido, "30123-456"), f"{tid}: ainda lê {lido!r}")
            self.assertNotIn("30123456", re.sub(r"\D", "", lido), f"{tid}: ainda lê {lido!r}")


class TestFonteSubstituta(unittest.TestCase):
    def test_editor_de_pdf_troca_a_fonte_pela_do_editor(self):
        from PIL import Image, ImageDraw, ImageFont
        from rotulador.ops import executar, tecnicas
        # a conta sintética é em Arial (que o editor também tem): nada muda. Num campo em Calibri, o editor
        # escreve na fonte parecida que ele tem (Arial/Helvetica), nítida e alinhada
        im = Image.new("RGB", (420, 90), (250, 250, 248))
        ImageDraw.Draw(im).text((20, 20), "CEP 30123-456", font=ImageFont.truetype("C:/Windows/Fonts/calibri.ttf", 34), fill=(25, 25, 25))
        img = np.asarray(im).copy()
        ys, xs = np.nonzero(img.min(2) < 128)
        campo = {"id": "c", "caixa": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], "texto": "CEP 30123-456",
                 "categoria": "cep", "tipo": "texto"}
        def roda(fonte_editor):
            p = {"tecnica": "campo.reescrever", "campo": campo, "semente": 3, "opcoes": {"fonte_editor": fonte_editor, "alinhamento": "esquerda"},
                 "acabamento": {"ruido": "nunca", "disfarce": "nunca"}, "conferir_ocr": False, "texto": "CEP 30999-111"}
            return executar("tecnica", img, p, vazia(*img.shape[:2]), base=img)
        res = roda("substituta")
        self.assertIn(res.meta["fonte"], tecnicas._SUBSTITUTAS)
        self.assertIn("substituída pelo editor de PDF", res.meta["descricao"])
        self.assertEqual(res.meta["texto_novo"], "CEP 30999-111")
        res2 = roda("imitada")
        self.assertEqual(res2.meta["fonte"], "Calibri")
        self.assertNotIn("substituída", res2.meta["descricao"])
        import random
        p = fz.Falsificador("editor_pdf", "recencia", random.Random(1))
        self.assertIn(p.habitos["fonte_editor"], ("imitada", "substituta"))
        self.assertEqual(p.params("campo.reescrever")["opcoes"]["fonte_editor"], p.habitos["fonte_editor"])
        self.assertNotIn("fonte_editor", p.params("campo.amador")["opcoes"])


class TestFalsificadorPapel(unittest.TestCase):
    def test_papel_sempre_fotografa_e_usa_as_tecnicas_fisicas(self):
        import random
        p = fz.Falsificador("papel", "apagar", random.Random(1))
        self.assertEqual(p.habitos["forma"], "liquido")
        self.assertEqual(p.habitos["tinta"], "caneta")
        self.assertEqual(p.habitos["colagem"], "papel")
        self.assertIsNotNone(p.canal(None))  # altera o papel: tem de fotografar ou escanear
        self.assertEqual(p.params("campo.corretivo")["opcoes"]["forma"], "liquido")
        self.assertEqual(p.params("caractere.acrescentar_tracos")["opcoes"]["tinta"], "caneta")
        self.assertNotIn("tinta", p.params("campo.rabiscar")["opcoes"]) if "caneta" not in [v for v, _ in catalogo.TECNICAS["campo.rabiscar"].opcoes["tinta"]["valores"]] else None
        cel = fz.Falsificador("celular", "apagar", random.Random(2))
        # "marcador" existe no rabisco, mas não nos traços: a opção só vai para quem a aceita
        if cel.habitos["tinta"] == "marcador":
            self.assertNotIn("tinta", cel.params("caractere.acrescentar_tracos")["opcoes"])
            self.assertEqual(cel.params("campo.rabiscar")["opcoes"]["tinta"], "marcador")
        self.assertIsNone(fz.Falsificador("celular", "apagar", random.Random(3)).canal(None))


if __name__ == "__main__":
    unittest.main()
