"""Motor do OCR: PP-OCRv6 (detector) + latino PP-OCRv5 (reconhecedor), baixados do Hugging Face na primeira vez, com
o RapidOCR antigo de reserva. Sem rede: o download é simulado (falha ou arquivo errado)."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import ocr
from tests.test_auto import TEM_OCR

TEM_MODELOS = all((ocr.MODELOS / m["repo"] / "inference.onnx").is_file() for m in (ocr._DET, ocr._REC))


def linha(texto: str, tam: int = 44) -> tuple[np.ndarray, list[int]]:
    """Uma linha de texto em Arial sobre papel claro e a caixa justa da tinta (com os acentos)."""
    f = ImageFont.truetype("arial.ttf", tam)
    im = Image.new("RGB", (60 + int(f.getlength(texto)), tam * 3), (250, 250, 247))
    d = ImageDraw.Draw(im)
    d.text((30, tam), texto, font=f, fill=(20, 20, 20))
    return np.asarray(im).copy(), list(d.textbbox((30, tam), texto, font=f))


class MotorIsolado(unittest.TestCase):
    """Cada teste começa sem motor carregado e devolve o do processo no fim (os outros testes usam o mesmo)."""

    def setUp(self):
        self._salvo = (ocr._motor, ocr._nome_motor)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        ocr._motor, ocr._nome_motor = self._salvo
        self.tmp.cleanup()


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestReserva(MotorIsolado):
    def test_sem_rede_cai_no_rapidocr(self):
        ocr._motor = ocr._nome_motor = None
        vazio = self.dir / "modelos"
        with mock.patch.object(ocr, "MODELOS", vazio), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("sem rede")), \
                mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(ocr.motor(), ocr.MOTOR_RESERVA)
        self.assertIn("sem o", err.getvalue())  # avisa qual motor ficou
        self.assertEqual(list(vazio.rglob("*.onnx*")), [])  # nada pela metade

    def test_arquivo_que_nao_confere_e_descartado(self):
        ocr._motor = ocr._nome_motor = None
        vazio = self.dir / "modelos"
        with mock.patch.object(ocr, "MODELOS", vazio), \
                mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(b"nao e um modelo")), \
                mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(ocr.motor(), ocr.MOTOR_RESERVA)
        self.assertIn("sha256", err.getvalue())
        self.assertEqual(list(vazio.rglob("*.onnx*")), [])

    def test_reserva_le_e_grava_o_nome_no_cache(self):
        ocr._motor = ocr._nome_motor = None
        img, _caixa = linha("CURITIBA 2026")
        cache = self.dir / "c.json"
        with mock.patch.object(ocr, "MODELOS", self.dir / "modelos"), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("sem rede")), \
                mock.patch("sys.stderr", io.StringIO()):
            lido = ocr.ler(img, cache)
        self.assertTrue(lido)
        self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["motor"], ocr.MOTOR_RESERVA)


class TestCache(MotorIsolado):
    def gravar(self, dados) -> Path:
        cache = self.dir / "c.json"
        cache.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        return cache

    def test_leitura_do_mesmo_motor_vem_do_cache(self):
        img, caixa = linha("JOSE CARLOS")
        l = {"caixa": caixa, "texto": "JOSECARLOS", "bruto": "JOSECARLOS", "conf": 0.9}
        ocr._motor, ocr._nome_motor = object(), ocr.MOTOR_PADRAO  # não pode ler de novo (motor falso)
        out = ocr.ler(img, self.gravar({"motor": ocr.MOTOR_PADRAO, "linhas": [l]}))
        self.assertEqual([x["texto"] for x in out], ["JOSECARLOS"])  # os espaços são do motor: nada é inventado
        ocr._motor, ocr._nome_motor = object(), ocr.MOTOR_RESERVA
        out = ocr.ler(img, self.gravar({"motor": ocr.MOTOR_RESERVA, "linhas": [l]}))
        self.assertEqual([x["texto"] for x in out], ["JOSE CARLOS"])  # o de reserva come os espaços: vêm da tinta

    def test_formato_antigo_e_do_motor_de_reserva(self):
        cache = self.gravar([{"caixa": [0, 0, 10, 10], "texto": "X", "bruto": "X", "conf": 0.9}])
        self.assertEqual(ocr._do_cache(cache), (json.loads(cache.read_text(encoding="utf-8")), ocr.MOTOR_RESERVA))

    @unittest.skipUnless(TEM_OCR and TEM_MODELOS, "modelos do OCR não baixados")
    def test_leitura_de_outro_motor_e_refeita(self):
        img, caixa = linha("MÊS REFERÊNCIA")
        cache = self.gravar([{"caixa": caixa, "texto": "LIXO", "bruto": "LIXO", "conf": 0.9}])  # do PP-OCRv3
        ocr._motor = ocr._nome_motor = None
        out = ocr.ler(img, cache)
        self.assertEqual([x["texto"] for x in out], ["MÊS REFERÊNCIA"])
        dados = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(dados["motor"], ocr.MOTOR_PADRAO)
        self.assertEqual(dados["linhas"][0]["bruto"], "MÊS REFERÊNCIA")


@unittest.skipUnless(TEM_OCR and TEM_MODELOS, "modelos do OCR não baixados")
class TestModeloNovo(unittest.TestCase):
    def test_motor_padrao(self):
        self.assertEqual(ocr.motor(), ocr.MOTOR_PADRAO)

    def test_le_acento_cedilha_e_espaco(self):
        for texto in ("JOSÉ CARLOS RIBEIRO", "MÊS REFERÊNCIA: AGO/2026", "Praça da Conceição, 1834"):
            img, (x0, y0, x1, y1) = linha(texto)
            out = ocr.ler(img)
            self.assertEqual([l["texto"] for l in out], [texto])
            c = out[0]["caixa"]  # a caixa cobre a tinta toda, acento em cima e cedilha embaixo
            self.assertLessEqual(c[0], x0 + 1)
            self.assertLessEqual(c[1], y0 + 1)
            self.assertGreaterEqual(c[2], x1 - 1)
            self.assertGreaterEqual(c[3], y1 - 1)
            m = max(3, int(0.2 * (y1 - y0)))  # o recorte da conferência (tecnicas._conferir)
            self.assertEqual(ocr.reconhecer(img[y0 - m:y1 + m, x0 - m:x1 + m]), texto)

    def test_papel_sobrando_no_recorte(self):
        # com 6 alturas de papel à direita o reconhecedor latino lia "R" ou "R1.8": reconhecer() apara antes
        for texto in ("R$ 202,33", "R$ 1.384,57"):
            img, (x0, y0, x1, y1) = linha(texto, 30)
            m = max(3, int(0.2 * (y1 - y0)))
            largo = np.pad(img[y0 - m:y1 + m, x0 - m:x1 + m], ((0, 0), (0, 6 * (y1 - y0)), (0, 0)), mode="edge")
            self.assertEqual(ocr.reconhecer(largo), texto)

    def test_zero_e_letra_o(self):
        img, _ = linha("AGO/2026 OUT/2025 R$ 1.384,57")
        self.assertEqual(ocr.ler_trecho(img, [0, 0, img.shape[1], img.shape[0]]), "AGO/2026 OUT/2025 R$ 1.384,57")


if __name__ == "__main__":
    unittest.main()
