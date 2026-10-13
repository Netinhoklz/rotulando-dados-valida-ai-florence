"""Edição exata e limpa (o pedido de "editar só as originais, focado em nome e endereço"):
espaços recuperados com fio de tabela, caixa justa da linha, detector (plausibilidade, bloco de endereçamento,
blocos do boleto), fonte entre as 10 mais usadas (família e peso), técnica campo.mesma_fonte, apagamento que
preserva fios de tabela, edição exata coerente (o mesmo nome/endereço/cidade em todo lugar), API e receita limpa."""
import json
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import campos, catalogo, identificar_fonte, ocr
from rotulador.ops import executar, geradores, texto
from rotulador.registro import Item
from rotulador.selecao import de_mascara, vazia
from rotulador.sessao import Sessao

try:
    import rapidocr_onnxruntime  # noqa: F401
    TEM_OCR = True
except ImportError:
    TEM_OCR = False

IDS = {"nome": 1, "cpf/cnpj": 2, "endereço": 3, "bairro": 4, "cep": 5, "cidade/uf": 6, "data": 7,
       "mês de referência": 8, "valor": 9, "consumo/leitura": 10, "codigo barras": 11, "linha digitável": 12,
       "identificador documento": 13, "emissor/logotipo": 14, "qr code/pix": 15, "outro": 16}


def F(nome, tam):
    return ImageFont.truetype(f"C:/Windows/Fonts/{nome}", tam)


def boleto() -> tuple[np.ndarray, dict]:
    """Documento sintético com o mesmo cliente em dois lugares: o envelope (negrito, sem rótulo) e o bloco do
    pagador do boleto (rótulo miúdo encostado em cima, fios de tabela em volta). Devolve a imagem e as caixas."""
    im = Image.new("RGB", (1100, 620), (250, 250, 248))
    d = ImageDraw.Draw(im)
    cx = {}

    def escreve(chave, xy, txt, f, cor=(25, 25, 25)):
        d.text(xy, txt, font=f, fill=cor, anchor="ls")
        l, t, r, b = d.textbbox(xy, txt, font=f, anchor="ls")
        cx[chave] = [l, t, r, b]
    bold, reg, mini = F("arialbd.ttf", 26), F("calibri.ttf", 28), F("arialbd.ttf", 13)
    escreve("nome1", (60, 80), "ANA PAULA FERREIRA", bold)
    escreve("rua1", (60, 112), "RUA DAS FLORES, 1234", bold)
    escreve("cidade1", (60, 144), "BELO HORIZONTE - MG", bold)
    for y in (300, 360, 420):  # fios da tabela do boleto
        d.line([(30, y), (1070, y)], fill=(20, 20, 20), width=2)
    d.line([(30, 300), (30, 420)], fill=(20, 20, 20), width=2)
    escreve("rot_pagador", (40, 314), "Pagador", mini)
    escreve("nome2", (60, 350), "Ana Paula Ferreira", reg)
    escreve("rot_end", (40, 374), "Endereço", mini)
    escreve("rua2", (60, 410), "Rua das Flores, 1234", reg)
    return np.asarray(im).copy(), cx


class TestEspacosECaixa(unittest.TestCase):
    def test_espacos_com_fio_de_tabela(self):
        img, cx = boleto()
        x0, y0, x1, y1 = cx["nome2"]
        caixa = [x0 - 4, y0 - 6, x1 + 4, 362]  # a caixa do OCR pega o rótulo de cima e o fio de baixo
        self.assertEqual(ocr.recuperar_espacos("AnaPaulaFerreira", img, caixa), "Ana Paula Ferreira")
        x0, y0, x1, y1 = cx["rua2"]
        self.assertEqual(ocr.recuperar_espacos("RuadasFlores,1234", img, [x0 - 4, y0 - 6, x1 + 4, 422]),
                         "Rua das Flores, 1234")
        # um OCR que já separou certo não é desfeito; dígitos nunca são separados
        self.assertEqual(ocr.recuperar_espacos("Rua das Flores, 1234", img, [x0 - 4, y0 - 6, x1 + 4, 422]),
                         "Rua das Flores, 1234")

    def test_caixa_da_linha_sem_rotulo_e_sem_fio(self):
        img, cx = boleto()
        x0, y0, x1, y1 = cx["nome2"]
        justa = ocr.caixa_da_linha(img, [x0 - 6, 302, x1 + 6, 362])  # da linha de cima à de baixo da célula
        self.assertLessEqual(abs(justa[1] - y0), 2)
        self.assertLessEqual(abs(justa[3] - y1), 2)
        self.assertGreater(justa[1], cx["rot_pagador"][3])  # o rótulo ficou de fora
        self.assertLess(justa[3], 360)  # o fio também

    def test_fios_ficam_ao_apagar(self):
        img, cx = boleto()
        x0, y0, x1, y1 = cx["rua2"]
        m = np.zeros(img.shape[:2], bool)
        m[y0 - 4:424, x0 - 4:x1 + 4] = True  # a seleção pega o fio de baixo (y = 420)
        out = texto.apagar(img, de_mascara(m), 7, 1, None, "patchmatch")
        self.assertTrue(np.array_equal(out[419:422, x0:x1], img[419:422, x0:x1]))  # o fio continua igual
        tinta = out[y0:y1 - 2, x0:x1].min(2) < 120
        self.assertLess(tinta.mean(), 0.01)  # e o texto saiu


class TestDetector(unittest.TestCase):
    def _c(self, i, cat, txt, caixa, rotulo=""):
        return {"id": f"t{i}", "categoria": cat, "texto": txt, "caixa": caixa, "caixa_ocr": caixa, "rotulo": rotulo,
                "conf": 0.9, "tipo": "texto"}

    def test_plausivel(self):
        self.assertFalse(campos.plausivel("endereço", "Data do documento"))
        self.assertFalse(campos.plausivel("endereço", "Beneficiario final"))
        self.assertTrue(campos.plausivel("endereço", "RUA 47 CJ PREFEITO JOSE WALTER 312"))
        self.assertTrue(campos.plausivel("nome", "M R C FILHO FOTOS"))
        self.assertTrue(campos.plausivel("nome", "CLIENTE 061"))
        self.assertFalse(campos.plausivel("nome", "NossoNimero"))
        self.assertFalse(campos.plausivel("cpf/cnpj", "CE"))
        self.assertTrue(campos.categoria_por_texto("RUA58CJPREFEITO") == "endereço")  # OCR colou o número
        self.assertTrue(campos.eh_rotulo("Pagador") and campos.eh_rotulo("Data do documento"))

    def test_bloco_de_enderecamento_e_partes_do_boleto(self):
        lista = [self._c(0, None, "M R C FILHO FOTOS", [416, 424, 625, 442]),
                 self._c(1, "endereço", "RUA 47 CJ PREFEITO JOSE WALTER 312", [417, 449, 857, 468]),
                 self._c(2, None, "PREFEITO JOSE WALTER", [417, 476, 694, 494]),
                 self._c(3, "endereço", "R Correia Vasques, 69, 3 Andar", [191, 1677, 514, 1700], "Endereço"),
                 self._c(4, "endereço", "Rua 47 Cj Prefeito Jose Walter 312", [191, 2006, 556, 2029], "Endereço")]
        campos._bloco_de_enderecamento(lista)
        self.assertEqual([c["categoria"] for c in lista[:3]], ["nome", "endereço", "bairro"])
        rotulos = [{"texto": "Beneficiario", "caixa": [167, 1602, 252, 1618]}, {"texto": "Pagador", "caixa": [165, 1945, 228, 1966]}]
        campos._partes_do_boleto(lista, rotulos)
        self.assertEqual(lista[3]["categoria"], "emissor/logotipo")  # endereço do beneficiário = do emissor
        self.assertEqual(lista[4]["categoria"], "endereço")  # o do pagador continua do cliente


class TestFonteTop10(unittest.TestCase):
    def test_lista_e_familia(self):
        top = identificar_fonte.fontes_top10()
        self.assertIn("Arial", top)
        self.assertIn("Times New Roman Bold", top)
        self.assertTrue(all(any(n == f or n == f"{f} Bold" for f in identificar_fonte.TOP10) for n in top))
        img, cx = boleto()
        r = identificar_fonte.identificar(img, cx["nome1"], "ANA PAULA FERREIRA", fontes=top)
        self.assertIn(r["ranking"][0]["fonte"], ("Arial Bold", "Helvetica Bold"))
        r = identificar_fonte.identificar(img, cx["rua2"], "Rua das Flores, 1234", fontes=top)
        self.assertEqual(r["ranking"][0]["fonte"], "Calibri")  # o peso (regular) vem da quantidade de tinta
        self.assertTrue(all(c["fonte"] in top for c in r["ranking"]))

    def test_tecnica_no_catalogo(self):
        t = catalogo.TECNICAS["campo.mesma_fonte"]
        self.assertEqual((t.num, t.familia, t.automatica), (8, "imitacao", True))
        self.assertIn("campo.mesma_fonte", next(j["tecnicas"] for j in catalogo.JEITOS if j["id"] == "reescrever"))


class TestGeradores(unittest.TestCase):
    def test_cidade_e_nome_no_formato(self):
        self.assertEqual(geradores.cidade_no_formato("Fortaleza", "CURITIBA", "PR"), "Curitiba")
        self.assertEqual(geradores.cidade_no_formato("CE", "CURITIBA", "PR"), "PR")
        self.assertEqual(geradores.cidade_no_formato("BELO HORIZONTE - MG", "Curitiba", "PR"), "CURITIBA - PR")
        self.assertEqual(geradores.trocar_cidade_no_texto("60750-720 - FORTALEZA - CE", "SAO JOSE", "SC"),
                         "60750-720 - SAO JOSE - SC")
        for i in range(5):
            self.assertNotEqual(geradores.cidade_mais_parecida("Fortaleza", random.Random(i))[0], "FORTALEZA")
        with geradores.usando(random.Random(3)):
            for _ in range(5):
                n = geradores.parecido("nome", "M R C FILHO FOTOS")
                self.assertLessEqual(abs(len(n) - 17), 6, n)
                self.assertTrue(n.split()[0] and len(n.split()[0]) == 1, n)  # as iniciais continuam iniciais
                self.assertEqual(len(geradores.parecido("cidade/uf", "CE")), 2)


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestEdicaoExata(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.img, self.cx = boleto()
        self.item = Item(doc="bol", split="treino", caminho=self.tmp / "x.png", rel="x.png", pagina=0, paginas=1,
                         sha_arquivo="f", sha_pixels="q" * 40, altura=self.img.shape[0], largura=self.img.shape[1])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _campos(self):
        out = []
        for i, (k, cat, txt) in enumerate((("nome1", "nome", "ANA PAULA FERREIRA"), ("rua1", "endereço", "RUA DAS FLORES, 1234"),
                                          ("cidade1", "cidade/uf", "BELO HORIZONTE - MG"), ("nome2", "nome", "Ana Paula Ferreira"),
                                          ("rua2", "endereço", "Rua das Flores, 1234"))):
            out.append({"id": f"t{i}", "categoria": cat, "texto": txt, "caixa": ocr.caixa_da_linha(self.img, self.cx[k]), "tipo": "texto"})
        return out

    def test_mesma_fonte_um_campo(self):
        c = self._campos()[3]
        p = {"tecnica": "campo.mesma_fonte", "campo": c, "semente": 4, "texto": "Bruno Costa Lima",
             "acabamento": {"ruido": "nunca", "disfarce": "nunca"}}
        res = executar("tecnica", self.img, p, vazia(*self.img.shape[:2]), base=self.img)
        self.assertEqual(res.meta["texto_novo"], "Bruno Costa Lima")
        self.assertIn(res.meta["fornecedores"]["fonte"], identificar_fonte.fontes_top10())
        self.assertEqual(res.meta["fornecedores"].get("fonte_identificada"), "top10")
        out = self.img.copy()
        sub = out[res.y0:res.y1, res.x0:res.x1]
        sub[:] = np.where(res.pegada[..., None], res.recorte, sub)
        x0, y0, x1, y1 = self.cx["rot_pagador"]
        self.assertTrue(np.array_equal(out[y0:y1, x0:x1], self.img[y0:y1, x0:x1]))  # o rótulo ficou intacto
        self.assertTrue(np.array_equal(out[359:362, 30:1070], self.img[359:362, 30:1070]))  # o fio também

    def test_editar_campos_coerente(self):
        from rotulador.contexto import Contexto
        from rotulador.gerador import Receita, editar_campos, receita_limpa
        ctx = Contexto(self.item, self.img, self.tmp / "saida")
        ctx._campos = self._campos()
        r = Receita(receita_limpa(IDS, ("nome", "endereço", "cidade/uf")), IDS)
        self.assertTrue(r.exato)
        s = Sessao(self.item, self.img.copy(), self.tmp / "rasc", IDS)
        falhas = []
        feitos = editar_campos(s, ctx, ctx.campos, r, None, random.Random(5), ao_falhar=lambda t, f, e: falhas.append(e))
        por = {f["antigo"]: f["novo"] for f in feitos}
        self.assertEqual(len(feitos), 5, falhas)
        self.assertEqual(por["ANA PAULA FERREIRA"].upper(), por["Ana Paula Ferreira"].upper())  # o mesmo nome novo
        # na caixa do lugar (partícula minúscula: "Eugenio dos Santos")
        self.assertEqual(por["Ana Paula Ferreira"], geradores.como_o_antigo("Ana Paula Ferreira", por["ANA PAULA FERREIRA"]))
        self.assertEqual(por["RUA DAS FLORES, 1234"].upper(), por["Rua das Flores, 1234"].upper())
        self.assertRegex(por["BELO HORIZONTE - MG"], r"^[A-Z ]+ - [A-Z]{2}$")
        self.assertTrue(all(f["tecnica"] == "campo.mesma_fonte" for f in feitos))
        alt, _reg, _cls = s.mascaras()
        y = self.cx["rot_pagador"]
        self.assertFalse(alt[y[1]:y[3], y[0]:y[2]].any())  # o rótulo não mudou nenhum pixel


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestAPIeLote(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import Registro
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        (self.dados / "treino").mkdir(parents=True)
        Image.fromarray(boleto()[0]).save(self.dados / "treino" / "boleto.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.app = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json"))
        self.c = self.app.test_client()
        self.doc = self.reg.por_split["treino"][0].doc

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fraude_nos_campos_marcados(self):
        lista = self.c.get(f"/api/campos/{self.doc}").get_json()["campos"]
        nomes = [c for c in lista if c["categoria"] == "nome"]
        self.assertGreaterEqual(len(nomes), 1, [(c["categoria"], c["texto"]) for c in lista])
        r = self.c.post("/api/auto/fraude", json={"id": self.doc, "campos": nomes})
        self.assertEqual(r.status_code, 200, r.get_json())
        trocas = r.get_json()["trocas"]
        self.assertEqual({t["categoria"] for t in trocas}, {"nome"})
        self.assertEqual(len(trocas) + len(r.get_json()["falhas"]), len(nomes))
        r = self.c.post("/api/auto/fraude", json={"id": self.doc, "campos": []})
        self.assertEqual(r.status_code, 400)

    def test_lote_limpo_so_muda_a_edicao(self):
        from rotulador.gerador import receita_limpa
        rec = receita_limpa(IDS, ("nome", "endereço"))
        rec.update({"documentos": {"splits": ["treino"]}, "variacoes": 1})
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(400):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertEqual(st["feitas"], 1, st)
        raiz = self.saida / "treino"
        a = json.loads(next((raiz / "anotacoes").glob("*.json")).read_text(encoding="utf-8"))
        self.assertIsNone(a["canal"])
        self.assertEqual(a["marcas_benignas"], [])
        self.assertNotIn("falsificador", a)
        self.assertTrue(a["gerador"]["receita"]["exato"])
        orig = np.asarray(Image.open(raiz / a["arquivos"]["original"]).convert("RGB"))
        ed = np.asarray(Image.open(raiz / a["arquivos"]["editada"]).convert("RGB"))
        mascara = np.asarray(Image.open(raiz / a["arquivos"]["mascara"])) > 0
        self.assertEqual(orig.shape, ed.shape)
        self.assertTrue(np.array_equal(orig, boleto()[0]))  # a original é o documento, sem nada
        self.assertTrue(np.array_equal((orig != ed).any(2), mascara))  # só mudou o que a máscara diz
        self.assertTrue({e["categoria"] for e in a["edicoes"]} <= {"nome", "endereço"})


if __name__ == "__main__":
    unittest.main()
