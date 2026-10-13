"""Diversidade de fornecedores (a impressão digital do gerador): apagadores alternativos, apagado malfeito
com vestígio, rasterizadores do texto amador, bloco de endereço de outro documento, e o fornecedor de cada
peça gravado no rótulo (``fornecedores``)."""
import unittest

import numpy as np

from rotulador import catalogo
from rotulador.ops import apagar
from rotulador.selecao import mascara_tinta
from tests.test_tecnicas import Base

SEM_ACABAMENTO = {"ruido": "nunca", "disfarce": "nunca"}


class TestApagadores(Base):
    def test_cada_apagador_tira_a_tinta_e_fica_no_rotulo(self):
        c = self.campo("nome")
        x0, y0, x1, y1 = c["caixa"]
        for modo in apagar.MODOS:
            res = self.rodar("campo.remover", c, opcoes={"parte": "campo", "apagar": modo, "vestigio": "nenhum"},
                             acabamento=SEM_ACABAMENTO)
            self.assertEqual(res.meta["fornecedores"]["apagar"], modo)
            depois = self.aplicado(res)
            tinta = mascara_tinta(depois[y0:y1, x0:x1]).astype(bool)
            self.assertLess(tinta.mean(), 0.02, f"{modo}: sobrou tinta")
            self.so_perto(res, c["caixa"], esq=12, dir_=12, cima=14, baixo=14)

    def test_reescrever_com_apagador_e_vestigio(self):
        c = self.campo("cep")
        res = self.rodar("campo.reescrever", c, opcoes={"alinhamento": "esquerda", "apagar": "chapado", "vestigio": "nenhum"},
                         acabamento=SEM_ACABAMENTO)
        self.assertEqual(res.meta["fornecedores"]["apagar"], "chapado")
        self.assertEqual(res.meta["fornecedores"]["texto"], "freetype")
        self.assertIn("fonte", res.meta["fornecedores"])
        self.assertNotIn("vestigio", res.meta["fornecedores"])
        self.confere_ocr(res, c["caixa"], folga_dir=40)

    def test_vestigio_deixa_fantasma(self):
        c = self.campo("nome")
        x0, y0, x1, y1 = c["caixa"]
        limpo = self.rodar("campo.remover", c, opcoes={"parte": "campo", "apagar": "patchmatch", "vestigio": "nenhum"},
                           acabamento=SEM_ACABAMENTO)
        fantasma = self.rodar("campo.remover", c, opcoes={"parte": "campo", "apagar": "patchmatch", "vestigio": "leve"},
                              acabamento=SEM_ACABAMENTO)
        self.assertGreater(fantasma.meta["fornecedores"]["vestigio"], 0)
        lum = lambda img: float(img[y0:y1, x0:x1].astype(np.float32).mean())  # noqa: E731
        orig, l_limpo, l_fant = lum(self.img), lum(self.aplicado(limpo)), lum(self.aplicado(fantasma))
        self.assertLess(orig, l_fant)  # o fantasma é mais claro que a tinta original...
        self.assertLess(l_fant, l_limpo - 0.3)  # ...e mais escuro que o apagado limpo


class TestRasterizadores(Base):
    def test_cada_rasterizador_escreve_legivel(self):
        c = self.campo("cep")
        for r in ("freetype", "gdi", "cleartype", "aliasado"):
            res = self.rodar("campo.amador", c, opcoes={"fonte": "Arial", "fundo": "papel", "rasterizador": r},
                             acabamento=SEM_ACABAMENTO)
            self.assertEqual(res.meta["fornecedores"]["texto"], r)
            self.assertEqual(res.meta["fornecedores"]["fonte"], "Arial")
            self.so_perto(res, c["caixa"], esq=14, dir_=40, cima=14, baixo=14)
            self.confere_ocr(res, c["caixa"], folga_dir=40)

    def test_cleartype_tem_franjas_coloridas(self):
        c = self.campo("cep")
        res = self.rodar("campo.amador", c, opcoes={"fonte": "Arial", "fundo": "papel", "rasterizador": "cleartype"},
                         acabamento=SEM_ACABAMENTO)
        rec = res.recorte.astype(int)
        self.assertGreater(np.abs(rec[..., 0] - rec[..., 2]).max(), 40)  # R e B diferentes nas bordas das letras
        res2 = self.rodar("campo.amador", c, opcoes={"fonte": "Arial", "fundo": "papel", "rasterizador": "freetype"},
                          acabamento=SEM_ACABAMENTO)
        rec2 = res2.recorte.astype(int)
        self.assertLess(np.abs(rec2[..., 0] - rec2[..., 2]).max(), 12)  # texto cinza: sem franja

    def test_aliasado_so_tem_dois_tons(self):
        c = self.campo("cep")
        res = self.rodar("campo.amador", c, opcoes={"fonte": "Arial", "fundo": "branco", "rasterizador": "aliasado"},
                         acabamento=SEM_ACABAMENTO)
        lum = res.recorte.astype(np.float32) @ np.float32([0.299, 0.587, 0.114])
        meio = ((lum > 40) & (lum < 215)).mean()  # quase nada entre a tinta e o papel
        self.assertLess(meio, 0.03)


class TestBloco(Base):
    def test_bloco_de_endereco_vem_inteiro_de_outro_documento(self):
        c = self.campo("endereço")
        res = self.rodar("bloco.colagem_externa", c, opcoes={"colagem": "retangulo", "apagar": "patchmatch"},
                         acabamento=SEM_ACABAMENTO)
        m = res.meta
        self.assertEqual(m["tecnica"], "bloco.colagem_externa")
        self.assertEqual(m["linhas"], 2)  # ENDERECO + CEP em linhas vizinhas
        self.assertIn(" | ", m["texto_antigo"])
        self.assertIn(" | ", m["texto_novo"])
        self.assertEqual(m["origem_campo"]["doc"], "bbbb2222")  # só do mesmo split (cccc3333 é do teste)
        self.assertEqual(m["fornecedores"]["colagem"], "retangulo")
        cep = self.campo("cep")
        x0, y0 = min(c["caixa"][0], cep["caixa"][0]), min(c["caixa"][1], cep["caixa"][1])
        x1, y1 = max(c["caixa"][2], cep["caixa"][2]), max(c["caixa"][3], cep["caixa"][3])
        self.so_perto(res, (x0, y0, x1, y1), esq=10, dir_=60, cima=10, baixo=16)

    def test_catalogo_e_jeitos(self):
        t = catalogo.TECNICAS["bloco.colagem_externa"]
        self.assertEqual((t.num, t.familia, t.precisa_doador), (22, "colagem_externa", True))
        colar = next(j for j in catalogo.JEITOS if j["id"] == "colar")
        self.assertIn("bloco.colagem_externa", colar["tecnicas"])
        pdf = catalogo.TECNICAS["campo.reescrever_pdf"]
        self.assertTrue(pdf.precisa_pdf)
        self.assertIn("campo.reescrever_pdf", next(j for j in catalogo.JEITOS if j["id"] == "reescrever")["tecnicas"])
        for tid in ("campo.reescrever", "campo.remover", "caractere.redesenhar", "trecho.trocar", "caractere.copiar_glifo"):
            self.assertIn("apagar", catalogo.TECNICAS[tid].opcoes, tid)


if __name__ == "__main__":
    unittest.main()
