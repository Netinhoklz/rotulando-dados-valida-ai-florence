"""Técnica pagina.recriar (documento refeito do zero): todas as linhas de texto da página apagadas e
re-tipografadas com o MESMO texto, logotipo e códigos mantidos. Documentos sintéticos e OCR em cache como em
test_tecnicas; quando o RapidOCR está instalado, as linhas refeitas são relidas."""
import unittest

import numpy as np

from rotulador import catalogo, ocr
from rotulador.contexto import Contexto
from rotulador.ops import executar
from rotulador.ops.tecnicas import aplicavel, lido_igual
from rotulador.selecao import vazia
from rotulador.sessao import ErroOperacao
from tests.test_tecnicas import IDS, TEM_OCR, Base

T = catalogo.TECNICAS["pagina.recriar"]
ESCRITORIO = ("Arial", "Calibri", "Times New Roman", "Verdana")
SEM_ACABAMENTO = {"disfarce": "nunca", "ruido": "nunca"}  # só a técnica: nada muda fora das caixas das linhas


def pagina(img):
    """O pseudo-campo com que o gerador chama a técnica."""
    H, W = img.shape[:2]
    return {"id": "pagina", "caixa": [0, 0, W, H], "texto": "", "categoria": "outro", "tipo": "pagina"}


class TestRecriar(Base):
    def rodar_pagina(self, ctx, semente=7, **opcoes):
        p = {"tecnica": "pagina.recriar", "campo": pagina(self.img), "semente": semente, "opcoes": opcoes,
             "acabamento": SEM_ACABAMENTO}
        return executar("tecnica", self.img, p, vazia(*self.img.shape[:2]), base=self.img, documento=ctx)

    def recriar(self, semente=7, **opcoes):
        return self.rodar_pagina(self.ctx, semente, **opcoes)

    def uniao(self, res):
        m = np.zeros(self.img.shape[:2], bool)
        for x0, y0, x1, y1 in res.meta["caixas"]:
            m[y0:y1, x0:x1] = True
        return m

    def linhas_refeitas(self, res):
        """(caixa, texto original) de cada linha refeita: cada caixa do rótulo é a de uma linha do OCR."""
        por_caixa = {tuple(l["caixa"]): l["texto"] for l in self.ctx.linhas}
        return [(cx, por_caixa[tuple(cx)]) for cx in res.meta["caixas"]]

    def test_cobre_a_pagina_e_a_pegada_e_a_uniao_das_caixas(self):
        res = self.recriar(fonte_editor="substituta")
        H, W = self.img.shape[:2]
        self.assertEqual((res.y0, res.y1, res.x0, res.x1), (0, H, 0, W))
        self.assertEqual(res.recorte.shape, self.img.shape)
        self.assertTrue(np.array_equal(res.pegada, self.uniao(res)))
        self.assertGreaterEqual(res.meta["linhas"], 0.8 * len(self.ctx.linhas), res.meta["descricao"])
        self.assertEqual(res.meta["linhas"] + res.meta["puladas"], len(self.ctx.linhas))
        self.assertEqual(len(self.linhas_refeitas(res)), res.meta["linhas"])

    def test_fora_das_caixas_nada_muda(self):
        for fe in ("imitada", "substituta"):
            res = self.recriar(fonte_editor=fe)
            mudou = (self.aplicado(res) != self.img).any(2)
            self.assertFalse((mudou & ~self.uniao(res)).any(), fe)
            # dentro, o texto antigo saiu e o novo entrou: (quase) toda caixa mudou de verdade
            mexidas = sum(mudou[y0:y1, x0:x1].mean() > 0.05 for x0, y0, x1, y1 in res.meta["caixas"])
            self.assertGreaterEqual(mexidas, 0.9 * res.meta["linhas"], fe)

    def test_rotulo_opcoes_e_fornecedores(self):
        res = self.recriar(fonte_editor="substituta", rasterizador="freetype", apagar="telea")
        m = res.meta
        self.assertEqual((m["tecnica"], m["tecnica_num"], m["familia"], m["nivel"], m["granularidade"]),
                         ("pagina.recriar", T.num, "recriacao", "profissional", "pagina"))
        self.assertEqual((m["texto_antigo"], m["texto_novo"], m["categoria"], m["campo"]), (None, None, "outro", "pagina"))
        self.assertEqual(m["opcoes"], {"fonte_editor": "substituta", "rasterizador": "freetype", "apagar": "telea"})
        self.assertEqual({k: m["fornecedores"][k] for k in ("apagar", "texto")}, {"apagar": "telea", "texto": "freetype"})
        self.assertIn(m["fornecedores"]["fonte"], ESCRITORIO)
        self.assertEqual(set(m["fontes"]), {m["fornecedores"]["fonte"]})  # uma fonte só para a página toda
        self.assertEqual(m["desfoque"], 0.0)  # nítido, como um PDF exportado
        self.assertIn("Documento refeito", m["descricao"])

    def test_imitada_usa_a_fonte_de_cada_linha(self):
        res = self.recriar(fonte_editor="imitada", rasterizador="freetype")
        m = res.meta
        self.assertEqual(m["opcoes"]["fonte_editor"], "imitada")
        self.assertEqual(sum(m["fontes"].values()), m["linhas"])
        self.assertIn(m["fornecedores"]["fonte"], m["fontes"])
        self.assertEqual(m["fornecedores"]["texto"], "freetype")
        self.assertIn(m["fornecedores"]["apagar"], [v for v, _ in catalogo.APAGAR["valores"]])

    def test_mesma_semente_mesmo_resultado(self):
        for fe in ("imitada", "substituta"):
            r1, r2 = self.recriar(11, fonte_editor=fe), self.recriar(11, fonte_editor=fe)
            self.assertTrue(np.array_equal(r1.recorte, r2.recorte), fe)
            self.assertEqual((r1.meta["caixas"], r1.meta["fontes"], r1.meta["opcoes"]),
                             (r2.meta["caixas"], r2.meta["fontes"], r2.meta["opcoes"]), fe)

    @unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
    def test_linhas_continuam_legiveis(self):
        """Pelo menos 60% das linhas refeitas são relidas pelo OCR com o mesmo texto (entre as que o OCR já lê
        certo no original: controle), e a conferência interna da técnica passou."""
        H, W = self.img.shape[:2]
        for fe in ("imitada", "substituta"):
            res = self.recriar(fonte_editor=fe, rasterizador="freetype")
            self.assertEqual(res.meta.get("conferido_ocr"), True, fe)
            out = self.aplicado(res)
            ok = julgadas = 0
            for (x0, y0, x1, y1), texto in self.linhas_refeitas(res):
                m = max(3, int(0.2 * (y1 - y0)))
                jan = (slice(max(0, y0 - m), min(H, y1 + m)), slice(max(0, x0 - m), min(W, x1 + m)))
                if not lido_igual(ocr.reconhecer(self.img[jan]), texto):
                    continue
                julgadas += 1
                ok += lido_igual(ocr.reconhecer(out[jan]), texto)
            self.assertGreaterEqual(julgadas, 5, fe)
            self.assertGreaterEqual(ok, 0.6 * julgadas, (fe, ok, julgadas))

    def test_so_o_pseudo_campo_da_pagina(self):
        self.assertTrue(aplicavel(T, pagina(self.img)))
        self.assertFalse(aplicavel(T, self.campo("nome")))
        self.assertFalse(aplicavel(catalogo.TECNICAS["campo.reescrever"], pagina(self.img)))
        self.assertFalse(any(catalogo.compativel(T, cat) for cat in IDS))  # nunca oferecida a um campo normal
        with self.assertRaises(ErroOperacao):
            self.rodar("pagina.recriar", self.campo("nome"))

    def test_precisa_de_documento_com_5_linhas(self):
        with self.assertRaises(ErroOperacao):
            self.rodar_pagina(None)
        pouco = Contexto(self.it, self.img, self.saida, self.reg)
        pouco._linhas = self.ctx.linhas[:4]
        with self.assertRaises(ErroOperacao):
            self.rodar_pagina(pouco)

    def test_pula_linhas_de_codigo_de_barras_e_qr(self):
        """Uma linha do OCR que cruza um campo de código (barras/QR) não é re-tipografada: o código fica."""
        alvo = self.ctx.linhas[2]
        ctx = Contexto(self.it, self.img, self.saida, self.reg)
        ctx._linhas = self.ctx.linhas
        ctx._campos = self.ctx.campos + [{"id": "barras1", "tipo": "barras", "categoria": "codigo barras",
                                          "texto": "", "caixa": list(alvo["caixa"])}]
        res = self.rodar_pagina(ctx, fonte_editor="substituta")
        self.assertNotIn(list(alvo["caixa"]), res.meta["caixas"])
        x0, y0, x1, y1 = alvo["caixa"]
        self.assertFalse((self.aplicado(res) != self.img).any(2)[y0:y1, x0:x1].any())
        self.assertEqual(res.meta["linhas"] + res.meta["puladas"], len(self.ctx.linhas) - 1)


class TestCatalogo(unittest.TestCase):
    def test_familia_granularidade_e_jeito(self):
        import rotulador.catalogo as cat  # os asserts do módulo (num único, cada técnica num jeito só) valem
        self.assertEqual((T.num, T.familia, T.nivel, T.granularidade, T.tipos, T.automatica, T.categorias),
                         (112, "recriacao", "profissional", "pagina", ("pagina",), True, None))
        self.assertIs(cat.POR_NUM[T.num], T)
        self.assertEqual(cat.FAMILIAS["recriacao"]["literatura"], "FAB")
        self.assertEqual(cat.GRANULARIDADES["pagina"], "Página inteira")
        jeito = next(j for j in cat.JEITOS if j["id"] == "recriar")
        self.assertEqual(jeito["tecnicas"], ["pagina.recriar"])
        self.assertEqual(set(T.opcoes), {"fonte_editor", "rasterizador", "apagar"})
        self.assertEqual([v for v, _ in T.opcoes["fonte_editor"]["valores"]], ["imitada", "substituta"])
        self.assertEqual(len([t for t in cat.TECNICAS.values() if t.automatica]), 30)  # + campo.letras_do_documento
        self.assertIn("recriacao", cat.legenda()["familias"])
        self.assertEqual(T.para_json()["tipos"], ["pagina"])


if __name__ == "__main__":
    unittest.main()
