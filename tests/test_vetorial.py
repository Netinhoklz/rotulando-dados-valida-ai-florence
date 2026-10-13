"""Edição vetorial do PDF (rotulador/vetorial.py): a camada de texto é editada e a página rasterizada de novo,
só a região do campo muda, o rótulo diz que não há rastro de pixel, e o gerador só oferece a técnica em PDF
com texto."""
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rotulador import documentos, ocr, vetorial
from rotulador.contexto import Contexto, cache_ocr
from rotulador.gerador import Plano, Receita
from rotulador.ops import executar
from rotulador.registro import Item, hash_pixels
from rotulador.selecao import vazia
from tests.test_tecnicas import IDS, TEM_OCR

DPI = 200
LINHAS = [("NOME", 60, 120, "MARIA DA SILVA", 230), ("VENCIMENTO", 60, 160, "10/09/2026", 230),
          ("CEP", 60, 200, "30123-456", 230), ("TOTAL", 60, 260, "R$ 158,37", 230)]


def pdf_com_texto(caminho: Path, fontfile: str | None = None) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    kw = {"fontname": "F0", "fontfile": fontfile} if fontfile else {"fontname": "helv"}
    for rot, xr, y, val, xv in LINHAS:
        page.insert_text((xr, y), rot, fontsize=11, color=(0.35, 0.35, 0.35), **kw)
        page.insert_text((xv, y), val, fontsize=13, color=(0.08, 0.08, 0.08), **kw)
    doc.save(str(caminho))
    doc.close()


def cache_do_pdf(caminho: Path, img: np.ndarray, saida: Path, sha: str) -> None:
    """Grava o OCR como se tivesse sido lido: uma linha por rótulo e por valor, em pixels."""
    import pymupdf

    esc = DPI / 72.0
    linhas = []
    with pymupdf.open(caminho) as doc:
        for bloco in doc[0].get_text("dict")["blocks"]:
            for l in bloco.get("lines", []):
                texto = "".join(s["text"] for s in l["spans"]).strip()
                x0, y0, x1, y1 = l["bbox"]
                caixa = [int(x0 * esc) - 2, int(y0 * esc) - 2, int(x1 * esc) + 3, int(y1 * esc) + 3]
                caixa = ocr.caixa_tinta(img, caixa, 2)
                linhas.append({"caixa": caixa, "texto": texto, "bruto": texto, "conf": 0.99})
    ocr.gravar_cache(cache_ocr(saida, sha), linhas)  # com o nome do motor: a lista pura é do OCR antigo e seria relida


class TestVetorial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.pdf = cls.tmp / "conta.pdf"
        pdf_com_texto(cls.pdf)
        cls.img = documentos.carregar(cls.pdf, 0, DPI)
        sha = hash_pixels(cls.img)
        cls.it = Item(doc=sha[:16], split="treino", caminho=cls.pdf, rel="conta.pdf", pagina=0, paginas=1,
                      sha_arquivo=sha, sha_pixels=sha, altura=cls.img.shape[0], largura=cls.img.shape[1])
        cache_do_pdf(cls.pdf, cls.img, cls.tmp / "saida", sha)
        cls.ctx = Contexto(cls.it, cls.img, cls.tmp / "saida", None, dpi=DPI)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def campo(self, cat):
        return next(c for c in self.ctx.campos if c.get("categoria") == cat)

    def rodar(self, campo, **opcoes):
        p = {"tecnica": "campo.reescrever_pdf", "campo": campo, "semente": 5, "opcoes": {"alinhamento": "esquerda", **opcoes},
             "acabamento": {"ruido": "nunca", "disfarce": "nunca"}, "conferir_ocr": False}
        return executar("tecnica", self.img, p, vazia(*self.img.shape[:2]), base=self.img, documento=self.ctx)

    def aplicado(self, res):
        out = self.img.copy()
        caixa = out[res.y0:res.y1, res.x0:res.x1]
        caixa[:] = np.where(res.pegada[..., None], res.recorte, caixa)
        return out

    def test_documento_e_vetorial_e_campos_lidos(self):
        self.assertTrue(vetorial.tem_texto(self.pdf, 0))
        self.assertTrue(self.ctx.vetorial)
        self.assertEqual({c["categoria"] for c in self.ctx.campos} & {"nome", "data", "cep", "valor"}, {"nome", "data", "cep", "valor"})

    def test_reescreve_no_pdf_e_so_o_campo_muda(self):
        for cat in ("valor", "data", "nome"):
            c = self.campo(cat)
            res = self.rodar(c, fonte_pdf="substituta")
            m = res.meta
            self.assertEqual(m["tecnica"], "campo.reescrever_pdf")
            self.assertNotEqual(m["texto_novo"], m["texto_antigo"])
            self.assertEqual(m["fornecedores"]["texto"], "pdf-vetorial")
            self.assertIn(m["fornecedores"]["fonte"], ("Helvetica", "Helvetica-Bold"))
            self.assertTrue(m["vetorial"])
            mudou = (self.aplicado(res) != self.img).any(2)
            self.assertTrue(mudou.any())
            x0, y0, x1, y1 = c["caixa"]
            fora = mudou.copy()
            fora[max(0, y0 - 12):y1 + 12, max(0, x0 - 8):x1 + 160] = False
            self.assertFalse(fora.any(), f"{cat}: mudou longe do campo")
            if TEM_OCR:
                lido = ocr.reconhecer(self.aplicado(res)[max(0, y0 - 6):y1 + 6, max(0, x0 - 6):x1 + 160])
                esperado = m["texto_novo"].replace(" ", "").upper()
                self.assertTrue(lido.replace(" ", "").upper()[:6] == esperado[:6] or esperado[:4] in lido.replace(" ", "").upper(),
                                (lido, esperado))

    def test_fonte_embutida_quando_tem_as_letras(self):
        arial = Path("C:/Windows/Fonts/arial.ttf")
        if not arial.is_file():
            self.skipTest("sem arial.ttf")
        pdf = self.tmp / "embutida.pdf"
        pdf_com_texto(pdf, str(arial))
        img = documentos.carregar(pdf, 0, DPI)
        sha = hash_pixels(img)
        it = Item(doc=sha[:16], split="treino", caminho=pdf, rel="embutida.pdf", pagina=0, paginas=1, sha_arquivo=sha,
                  sha_pixels=sha, altura=img.shape[0], largura=img.shape[1])
        cache_do_pdf(pdf, img, self.tmp / "saida", sha)
        ctx = Contexto(it, img, self.tmp / "saida", None, dpi=DPI)
        c = next(x for x in ctx.campos if x.get("categoria") == "data")
        p = {"tecnica": "campo.reescrever_pdf", "campo": c, "semente": 2, "opcoes": {"fonte_pdf": "embutida", "alinhamento": "esquerda"},
             "acabamento": {"ruido": "nunca", "disfarce": "nunca"}, "conferir_ocr": False}
        res = executar("tecnica", img, p, vazia(*img.shape[:2]), base=img, documento=ctx)
        # a fonte do PDF (subconjunto de Arial) ou, se faltou alguma letra, a reserva do editor: os dois são válidos
        self.assertIn(res.meta["fornecedores"]["fonte"], ("Arial", "ArialMT", "Helvetica", "Helvetica-Bold"))
        self.assertNotEqual(res.meta["texto_novo"], res.meta["texto_antigo"])

    def test_gerador_so_oferece_em_pdf_com_texto(self):
        r = Receita({"tecnicas": {"campo.reescrever_pdf": 1, "campo.reescrever": 1}}, IDS)
        campos = [c for c in self.ctx.campos if c.get("categoria") in IDS]
        com = {t.id for t, _ in Plano(r, random.Random(1)).candidatos(campos, vetorial=True)}
        sem = {t.id for t, _ in Plano(r, random.Random(1)).candidatos(campos, vetorial=False)}
        self.assertIn("campo.reescrever_pdf", com)
        self.assertNotIn("campo.reescrever_pdf", sem)
        self.assertIn("campo.reescrever", sem)
        self.assertFalse(self.ctx.derivado(self.img, lambda pts: pts, "x").vetorial)  # depois de uma captura: não


if __name__ == "__main__":
    unittest.main()
