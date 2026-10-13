"""campo.letras_do_documento: o valor novo montado com as letras recortadas do próprio documento.

O documento sintético é escrito na Trebuchet MS — fora das 10 fontes mais usadas, então a "mesma fonte" só sai
aproximada —, com rótulos cinza em negrito (outro estilo, que não pode doar letra), luz desigual no papel, grão,
desfoque e JPEG. As linhas do OCR têm o texto exato (o teste não depende do motor de OCR; com o RapidOCR instalado, o
resultado é relido)."""
import io
import types
import unittest

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import catalogo, glifos, ocr
from rotulador.ops import tecnicas
from rotulador.selecao import mascara_tinta, vazia
from rotulador.sessao import ErroOperacao

try:
    import rapidocr_onnxruntime  # noqa: F401
    TEM_OCR = True
except ImportError:
    TEM_OCR = False

LUMA = np.float32([0.299, 0.587, 0.114])
CRU = {"pericia": "cru"}  # sem acabamento: a tinta colada sai como foi transferida
PADRAO = {"ruido": "auto", "disfarce": "nunca", "jpeg": 0}  # o do modo exato
LINHAS = [("PAGADOR", "MARIA DA SILVA SANTOS"), ("ENDERECO", "RUA DAS PALMEIRAS, 1234"), ("BAIRRO", "JARDIM BOTANICO"),
          ("CIDADE", "CURITIBA PR 80530-140"), ("VENCIMENTO", "10/09/2026")]
X_VALOR = 250


def _documento(chave: str):
    """(imagem, documento, campo do nome, caixas dos rótulos)."""
    W, H = 900, 400
    rng = np.random.default_rng(11)
    luz = np.linspace(238, 226, W, dtype=np.float32)[None, :] + np.linspace(0, -6, H, dtype=np.float32)[:, None]
    papel = luz + cv2.GaussianBlur(rng.normal(0, 5, (H, W)).astype(np.float32), (0, 0), 0.8)
    im = Image.fromarray(np.clip(np.stack([papel, papel - 2, papel - 7], -1) + 0.5, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    valor = ImageFont.truetype("C:/Windows/Fonts/trebuc.ttf", 30)
    rotulo = ImageFont.truetype("C:/Windows/Fonts/trebucbd.ttf", 19)
    linhas, rotulos, caixas = [], [], []
    y = 40
    for r, v in LINHAS:
        d.text((30, y + 8), r, font=rotulo, fill=(120, 120, 120))
        d.text((X_VALOR, y), v, font=valor, fill=(30, 32, 40))
        for txt, xy, f, lista in ((r, (30, y + 8), rotulo, rotulos), (v, (X_VALOR, y), valor, caixas)):
            x0, y0, x1, y1 = d.textbbox(xy, txt, font=f)
            linhas.append({"caixa": [x0 - 3, y0 - 4, x1 + 3, y1 + 4], "texto": txt, "bruto": txt, "conf": 0.99})
            lista.append([x0 - 3, y0 - 4, x1 + 3, y1 + 4])
        y += 66
    a = cv2.GaussianBlur(np.asarray(im, np.float32), (0, 0), 0.6) + rng.normal(0, 2.0, (H, W, 1)).astype(np.float32)
    buf = io.BytesIO()
    Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=88)
    img = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")).copy()
    campo = {"id": "t1", "caixa": ocr.caixa_tinta(img, caixas[0], 0), "texto": LINHAS[0][1], "categoria": "nome",
             "tipo": "texto"}
    doc = types.SimpleNamespace(img=img, linhas=linhas, campos=[campo], doc=chave, split="treino")
    doc.inventario = lambda: glifos.inventario(chave, img, linhas)
    return img, doc, campo, rotulos


def _rodar(img, doc, campo, texto, semente=3, acabamento=CRU, conferir=False):
    p = {"tecnica": "campo.letras_do_documento", "campo": campo, "semente": semente, "acabamento": acabamento,
         "conferir_ocr": conferir}
    if texto is not None:
        p["texto"] = texto
    return tecnicas.tecnica(img, p, vazia(*img.shape[:2]), base=img, documento=doc)


def _aplicado(img, res):
    out = img.copy()
    sub = out[res.y0:res.y1, res.x0:res.x1]
    sub[:] = np.where(res.pegada[..., None], res.recorte, sub)
    return out


def _estilo(img, caixa):
    """(luma do núcleo da tinta, espessura do traço, nitidez da borda) da linha na caixa."""
    x0, y0, x1, y1 = (int(v) for v in caixa)
    rec = img[y0 - 4:y1 + 4, x0 - 4:x1 + 4]
    t = ocr.tinta_da_linha(rec)
    papel = glifos.cor_papel(img, caixa)
    f = rec.astype(np.float32)
    lum = f @ LUMA
    nucleo = np.median(f[t & (lum <= np.percentile(lum[t], 30))], axis=0)
    a = glifos.alfa(rec, papel, nucleo) * (cv2.dilate(t.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    return float(nucleo @ LUMA), glifos.peso_traco(a), glifos.nitidez_tinta(img, caixa)


class TestLetrasDoDocumento(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.img, cls.doc, cls.campo, cls.rotulos = _documento("letras_doc_teste_1")
        cls.novo = "MARINA DE SOUSA LIMA"  # todas as letras existem no documento (E e U só em outras linhas)
        cls.res = _rodar(cls.img, cls.doc, cls.campo, cls.novo)

    def test_catalogo(self):
        t = catalogo.TECNICAS["campo.letras_do_documento"]
        self.assertEqual((t.num, t.familia, t.nivel, t.granularidade, t.automatica),
                         (13, "copia_interna", "profissional", "campo", True))
        self.assertIs(catalogo.POR_NUM[13], t)
        self.assertIn(t.id, next(j["tecnicas"] for j in catalogo.JEITOS if j["id"] == "reescrever"))
        self.assertIn(t.id, tecnicas._IMPL)

    def test_rotulo_e_texto_imposto(self):
        m = self.res.meta
        self.assertEqual(m["texto_novo"], self.novo)
        self.assertEqual((m["tecnica"], m["tecnica_num"], m["familia"]), ("campo.letras_do_documento", 13, "copia_interna"))
        self.assertEqual(m["fornecedores"]["texto"], "glifos_do_documento")
        self.assertEqual(m["fornecedores"]["apagar"], "local")
        self.assertNotIn("fonte", m["fornecedores"])  # nenhuma letra desenhada
        self.assertEqual(m["letras_copiadas"], 1.0)
        self.assertEqual(m["desenhados"], [])
        self.assertFalse(m["reamostrado"])
        self.assertEqual(len(m["doadores"]), sum(not ch.isspace() for ch in self.novo))
        origens = {d["origem"] for d in m["doadores"]}
        self.assertIn("campo", origens)
        self.assertIn("documento", origens)  # o E e o U não estão no nome
        self.assertIn(m["gabarito"], m["descricao"])

    def test_letras_coladas_sao_as_do_documento(self):
        """Cada letra colada é a doadora pixel a pixel (correlação ~1), na posição inteira, sem reamostrar."""
        out = _aplicado(self.img, self.res)
        for d in self.res.meta["doadores"]:
            x0, y0, x1, y1 = d["caixa"]
            px0, py0, px1, py1 = d["para"]
            self.assertEqual((px1 - px0, py1 - py0), (x1 - x0, y1 - y0), d)
            src = self.img[y0:y1, x0:x1].astype(np.float32) @ LUMA
            dst = out[py0:py1, px0:px1].astype(np.float32) @ LUMA
            # na tinta e no halo da letra (a caixa pode ter a ponta da vizinha: a barra do T por cima do O)
            letra = cv2.dilate(((src < 150) & (dst < 150)).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            corr = np.corrcoef(src[letra], dst[letra])[0, 1]
            self.assertGreater(corr, 0.97, (d["caractere"], corr))

    def test_nenhuma_letra_do_rotulo_cinza(self):
        """Rótulo cinza em negrito é outro estilo: nunca doa letra (o A de PAGADOR, o E de ENDERECO)."""
        for d in self.res.meta["doadores"]:
            self.assertGreaterEqual(d["caixa"][0], X_VALOR - 5, d)

    def test_cor_peso_e_nitidez_batem(self):
        out = _aplicado(self.img, self.res)
        ys, xs = np.nonzero(self.res.pegada)
        caixa = ocr.caixa_tinta(out, [self.res.x0 + xs.min(), self.res.y0 + ys.min(), self.res.x0 + xs.max() + 1,
                                      self.res.y0 + ys.max() + 1], 0)
        l0, p0, n0 = _estilo(self.img, self.campo["caixa"])
        l1, p1, n1 = _estilo(out, caixa)
        self.assertLess(abs(l1 - l0), 5, (l0, l1))
        self.assertLess(abs(p1 / p0 - 1), 0.06, (p0, p1))
        self.assertLess(abs(n1 / n0 - 1), 0.12, (n0, n1))

    def test_pegada_so_na_tinta_e_fora_intacto(self):
        out = _aplicado(self.img, self.res)
        mudou = (out != self.img).any(2)
        peg = np.zeros(mudou.shape, bool)
        peg[self.res.y0:self.res.y1, self.res.x0:self.res.x1] = self.res.pegada
        self.assertFalse((mudou & ~peg).any())
        # a pegada é a tinta antiga apagada e a nova colada (com o halo), não um retângulo
        tinta = np.zeros(mudou.shape, bool)
        for img in (self.img, out):
            x0, y0, x1, y1 = self.res.x0, self.res.y0, self.res.x1, self.res.y1
            tinta[y0:y1, x0:x1] |= mascara_tinta(img[y0 - 6:y1 + 6, x0 - 6:x1 + 6])[6:-6, 6:-6].astype(bool)
        perto = cv2.dilate(tinta.astype(np.uint8), np.ones((11, 11), np.uint8)) > 0
        self.assertFalse((peg & ~perto).any())
        self.assertLess(peg[self.res.y0:self.res.y1, self.res.x0:self.res.x1].mean(), 0.9)
        for x0, y0, x1, y1 in self.rotulos:  # os rótulos e as outras linhas não mudam
            self.assertFalse(mudou[y0:y1, x0:x1].any())
        x0, y0, x1, y1 = self.doc.linhas[3]["caixa"]  # o endereço doou letras, mas fica como está
        self.assertFalse(mudou[y0:y1, x0:x1].any())

    def test_deterministico(self):
        r2 = _rodar(self.img, self.doc, self.campo, self.novo)
        self.assertEqual((r2.y0, r2.y1, r2.x0, r2.x1), (self.res.y0, self.res.y1, self.res.x0, self.res.x1))
        self.assertTrue(np.array_equal(r2.recorte, self.res.recorte))
        self.assertTrue(np.array_equal(r2.pegada, self.res.pegada))
        self.assertEqual(r2.meta["doadores"], self.res.meta["doadores"])

    def test_letra_repetida_varia_a_instancia(self):
        """O texto novo tem 4 A e o documento tem vários: cópias idênticas da mesma letra repetida são pista."""
        doadas = [tuple(d["caixa"]) for d in self.res.meta["doadores"] if d["caractere"] == "A"]
        self.assertEqual(len(doadas), 4)
        self.assertEqual(len(set(doadas)), 4)

    def test_letra_que_falta_sai_desenhada_e_rotulo_misto(self):
        res = _rodar(self.img, self.doc, self.campo, "MARIA DE SOUZA")  # não há Z no documento
        m = res.meta
        self.assertEqual(m["fornecedores"]["texto"], "misto")
        self.assertEqual(m["desenhados"], ["Z"])
        self.assertEqual(m["fornecedores"]["fonte"], m["gabarito"])
        self.assertEqual(len(m["caixas_desenhadas"]), 1)
        self.assertAlmostEqual(m["letras_copiadas"], 11 / 12, places=3)
        self.assertIn("desenhadas", m["descricao"])

    def test_acento_que_o_documento_nao_tem_sai_desenhado_sobre_a_letra_doada(self):
        """Ç e Ã sem nenhum sinal no documento: o C e o A são recortados e só o sinal é desenhado em cima (cedilha
        embaixo do C, til em cima do A)."""
        res = _rodar(self.img, self.doc, self.campo, "MARIA DA CONCEIÇÃO")
        m = res.meta
        self.assertEqual(m["acentos_desenhados"], ["Ç", "Ã"])
        self.assertEqual(m["desenhados"], [])
        self.assertEqual(m["fornecedores"]["texto"], "misto")
        out = _aplicado(self.img, res)
        lum = out.astype(np.float32) @ LUMA
        for d in m["doadores"]:
            if d["caractere"] not in "ÇÃ":
                continue
            x0, y0, x1, y1 = d["para"]
            h = y1 - y0
            faixa = lum[y0 - h // 2:y0, x0:x1] if d["caractere"] == "Ã" else lum[y1:y1 + h // 3, x0:x1]
            self.assertLess(faixa.min(), 120, d)  # o sinal está lá

    def test_faltando_mais_de_um_quarto_desiste(self):
        with self.assertRaises(ErroOperacao) as cm:
            _rodar(self.img, self.doc, self.campo, "WAYNE KYZ")
        self.assertIn("não tem as letras", str(cm.exception))

    def test_largura_e_a_das_letras_do_documento(self):
        c = tecnicas._C(self.img, {"tecnica": "campo.letras_do_documento", "campo": self.campo, "semente": 1},
                        self.img, self.doc, None, None, None)
        L = tecnicas._Letras(c)
        gs = c.glifos
        self.assertEqual(L.largura(self.campo["texto"]), gs[-1].x1 - gs[0].x0)
        # o mesmo texto remontado com as letras do documento tem a largura do original (±1 px por letra)
        self.assertLess(abs(L.largura("MARIA DA SILVA") - (gs[11].x1 - gs[0].x0)), 12)

    def test_ponto_solto_perto_da_doadora_nao_vem_junto(self):
        """Um pontinho (sujeira, o pingo de outra linha) logo abaixo do único P do documento entrava na letra pela margem
        da segmentação e saía colado no texto novo ("LOP.ES"). A letra doada é só o corpo dela."""
        img = self.img.copy()
        x0, _y0, x1, y1 = next(l["caixa"] for l in self.doc.linhas if l["texto"].startswith("RUA DAS"))
        gs = glifos.segmentar(img, ocr.caixa_tinta(img, [x0, _y0, x1, y1], 0), "RUA DAS PALMEIRAS, 1234")
        p = next(g for g in gs if g.ch == "P")
        img[p.y1 + 4:p.y1 + 7, p.x0 + 2:p.x0 + 5] = 40  # o ponto, 4 px abaixo do pé do P
        linhas = self.doc.linhas
        import types
        doc = types.SimpleNamespace(img=img, linhas=linhas, campos=[self.campo], doc="letras_ponto", split="treino")
        doc.inventario = lambda: glifos.inventario("letras_ponto", img, linhas)
        res = _rodar(img, doc, self.campo, "MARIA LOPES")
        self.assertIn("P", [d["caractere"] for d in res.meta["doadores"]])
        out = _aplicado(img, res)
        a, b, c_, d = res.x0, res.y0, res.x1, res.y1
        t = mascara_tinta(out[b - 8:d + 8, a - 8:c_ + 8]).astype(np.uint8)
        n, _lab, est, _ = cv2.connectedComponentsWithStats(t, connectivity=8)
        alturas = [int(est[i, cv2.CC_STAT_HEIGHT]) for i in range(1, n) if est[i, cv2.CC_STAT_AREA] >= 3]
        h = self.campo["caixa"][3] - self.campo["caixa"][1]
        self.assertTrue(all(x >= 0.6 * h for x in alturas), alturas)  # só letras inteiras: nenhum ponto solto

    def test_espaco_entre_palavras_na_fonte_monoespacada(self):
        """Courier: o I tem vão de meia célula para as vizinhas, e contar esse vão como "palavras que o OCR juntou"
        encolhia o espaço para 1/3 ("RUAFRANCISCO", "APTO44"): no campo de letras apertadas, os vãos largos do 1, do C
        e da vírgula eram a maioria das amostras de espaço. O espaço novo tem a largura do original."""
        rng = np.random.default_rng(5)
        W, H = 900, 260
        im = Image.fromarray(np.clip(236 + rng.normal(0, 3, (H, W, 1)), 0, 255).repeat(3, 2).astype(np.uint8))
        d = ImageDraw.Draw(im)
        f = ImageFont.truetype("C:/Windows/Fonts/courbd.ttf", 30)
        linhas, caixas = [], []
        for k, t in enumerate(("RUA MARECHAL DEODORO, 1855 APTO 41", "PEDRO HENRIQUE SOUZA", "CURITIBA PR 80234-140")):
            xy = (40, 30 + 70 * k)
            d.text(xy, t, font=f, fill=(70, 70, 70))
            x0, y0, x1, y1 = d.textbbox(xy, t, font=f)
            linhas.append({"caixa": [x0 - 3, y0 - 4, x1 + 3, y1 + 4], "texto": t, "bruto": t, "conf": 0.99})
            caixas.append([x0 - 3, y0 - 4, x1 + 3, y1 + 4])
        img = np.asarray(im).copy()
        campo = {"id": "t1", "caixa": ocr.caixa_tinta(img, caixas[0], 0), "texto": linhas[0]["texto"],
                 "categoria": "endereço", "tipo": "texto"}
        doc = types.SimpleNamespace(img=img, linhas=linhas, campos=[campo], doc="letras_courier", split="treino")
        doc.inventario = lambda: glifos.inventario("letras_courier", img, linhas)
        res = _rodar(img, doc, campo, "RUA DO PORTO, 1234 APTO 12")
        out = _aplicado(img, res)

        def vaos(im_, caixa):
            m = mascara_tinta(im_[caixa[1]:caixa[3], caixa[0]:caixa[2] + 40]).astype(bool).any(0)
            corridas, n = [], 0
            for v in m:
                if v:
                    if n:
                        corridas.append(n)
                    n = 0
                else:
                    n += 1
            return sorted(corridas)

        antes = vaos(img, campo["caixa"])
        espaco_original = float(np.median([v for v in antes if v > 0.5 * max(antes)]))  # os vãos entre palavras
        novos = [v for v in vaos(out, campo["caixa"]) if v > 0.5 * espaco_original]
        self.assertGreaterEqual(len(novos), 4, (espaco_original, vaos(out, campo["caixa"])))  # RUA_DO_PORTO,_1234_APTO_12
        # sem o maior (o da vírgula, que tem o vão dela a mais): os espaços entre palavras como os do original
        self.assertTrue(all(abs(v / espaco_original - 1) < 0.3 for v in novos[:-1]), (espaco_original, novos))

    @unittest.skipUnless(TEM_OCR, "RapidOCR não instalado")
    def test_ocr_le_o_valor_novo(self):
        res = _rodar(self.img, self.doc, self.campo, "MARINA DE SOUSA LIMA", semente=5, acabamento=PADRAO,
                     conferir=True)
        if res.meta.get("conferido_ocr") is None:
            self.skipTest("o OCR não lê nem o campo original: não há como conferir")
        self.assertIs(res.meta["conferido_ocr"], True)
        self.assertEqual(res.meta["fornecedores"].get("grao"), "espectral")


if __name__ == "__main__":
    unittest.main()
