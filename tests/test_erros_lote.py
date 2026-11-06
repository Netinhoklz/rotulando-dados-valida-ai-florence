"""Erros das gerações em lote: a versão em que nada coube diz o motivo mais comum (o documento de texto claro sobre
fundo escuro), e a conferência por OCR lê o texto ACRESCENTADO (antes, quem só acrescentava tinha a leitura presa à
caixa antiga do campo e a edição certa era descartada: 174 de 180 no "acrescentar" do FSTS nas contas realistas)."""
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app import RAIZ, carregar_categorias
from rotulador import ocr
from rotulador.contexto import cache_ocr
from rotulador.gerador import Gerador, motivo_da_recusa
from rotulador.ops import tecnicas as T
from rotulador.registro import Registro
from rotulador.sessao import ErroOperacao, Resultado
from tests.test_auto import TEM_OCR
from tests.test_tecnicas import documento

IDS = {c["chave"]: int(c["id"]) for c in carregar_categorias(RAIZ / "config" / "categorias.json")}


class TestMotivo(unittest.TestCase):
    def test_sem_a_tecnica_nem_os_detalhes(self):
        self.assertEqual(motivo_da_recusa("campo.mesma_fonte: texto claro sobre fundo escuro (faixa colorida): as "
                                          "técnicas de tinta escura sobre papel deixariam um borrão"),
                         "texto claro sobre fundo escuro")
        self.assertEqual(motivo_da_recusa("conferência por OCR: o resultado lê 'X', não 'Y' (descartado)"),
                         "conferência por OCR")
        self.assertEqual(motivo_da_recusa(ErroOperacao("não há outro campo da mesma categoria para copiar")),
                         "não há outro campo da mesma categoria para copiar")


class TestDocumentoEscuro(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_nada_coube_diz_o_motivo(self):
        """Um cartão de texto claro sobre fundo escuro (como um cartão de perfil): o lote diz por que nada coube."""
        img, linhas = documento()
        (self.tmp / "dados" / "treino").mkdir(parents=True)
        Image.fromarray((255 - img).astype(np.uint8)).save(self.tmp / "dados" / "treino" / "escuro.png")
        reg = Registro(self.tmp / "dados", self.tmp / "saida", log=lambda *_: None)
        ocr.gravar_cache(cache_ocr(self.tmp / "saida", reg.por_split["treino"][0].sha_pixels), linhas)
        g = Gerador(reg, self.tmp / "saida", IDS)
        g.iniciar({"perfil": "profissional", "estilo": "recomendado", "extras": False, "variacoes": 1,
                   "documentos": {"splits": ["treino"], "ids": None}})
        g.thread.join(timeout=300)
        st = g.status()
        self.assertEqual(st["feitas"], 0)
        self.assertTrue(st["erros"] and "nenhuma técnica coube" in st["erros"][0], st["erros"])
        self.assertIn("texto claro sobre fundo escuro", st["erros"][0])


@unittest.skipUnless(TEM_OCR, "OCR (paddleocr) não instalado")
class TestConferenciaDoAcrescimo(unittest.TestCase):
    def test_tinta_cinza_acrescentada_entra_na_leitura(self):
        """Só a tinta nova mudou (o "acrescentar"), e ela é cinza como a do escaneado: a leitura tem de ir até ela."""
        f = ImageFont.truetype("arial.ttf", 30)
        im = Image.new("RGB", (520, 80), (248, 247, 243))
        d = ImageDraw.Draw(im)
        d.text((20, 20), "CENTRO", fill=(95, 95, 95), font=f)
        antes = np.asarray(im).copy()
        d.text((20 + int(f.getlength("CENTRO ")), 20), "NORTE", fill=(95, 95, 95), font=f)
        depois = np.asarray(im).copy()
        mudou = (depois != antes).any(2)
        ys, xs = np.nonzero(mudou)
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
        res = Resultado(y0, y1, x0, x1, depois[y0:y1, x0:x1].copy(), mudou[y0:y1, x0:x1].copy(),
                        meta={"texto_novo": "CENTRO NORTE"})
        ys, xs = np.nonzero((antes < 200).any(2))
        caixa = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        c = SimpleNamespace(tipo="texto", p={}, tec=SimpleNamespace(id="campo.acrescentar"), caixa=caixa, img=antes,
                            texto="CENTRO", H=antes.shape[0], W=antes.shape[1])
        self.assertTrue(T._conferir(c, res).meta["conferido_ocr"])


if __name__ == "__main__":
    unittest.main()
