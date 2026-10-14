"""Acabamento da edição padrão (campo.mesma_fonte) e das técnicas de cópia: o que a auditoria achou de diferente
entre a região editada e o resto do documento original — e que um detector aprenderia como atalho.

Cada teste mostra o defeito (medido como na auditoria) e confere a correção."""
import contextlib
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import documentos, glifos, ocr, pericia
from rotulador.contexto import Contexto
from rotulador.ops import auto, executar, geradores, tecnicas
from rotulador.registro import Item
from rotulador.selecao import mascara_tinta, vazia
from rotulador.sessao import ErroOperacao, Resultado
from tests import test_tecnicas as tt

LUMA = np.float32([0.299, 0.587, 0.114])
CB = np.float32([-0.168736, -0.331264, 0.5])


# tabelas assimétricas (frequência horizontal bem mais grossa que a vertical): usar a tabela sem transpor numa
# orientação EXIF que gira a imagem tira os blocos da grade
ASSIMETRICA = [[2 + 7 * (k % 8) + (k // 8) for k in range(64)], [3 + 8 * (k % 8) + 2 * (k // 8) for k in range(64)]]


def _papel_jpeg(pasta: Path, nome="doc.jpg", orientacao=1, q=80, sub=2, cor=(25, 40, 130), W=331, H=187,
                semente=3, qtables=None) -> Path:
    """Um 'scan' sintético salvo em JPEG (``sub``: 2 = 4:2:0; tamanho que não é múltiplo de 8), com grão, texto
    colorido e, se pedido, a tag EXIF de orientação (o arquivo é o armazenado; quem lê vê a imagem girada)."""
    rng = np.random.default_rng(semente)
    a = np.clip(238 + cv2.GaussianBlur(rng.normal(0, 4, (H, W)).astype(np.float32), (0, 0), 0.8), 0, 255)
    im = Image.fromarray(np.repeat(a[..., None], 3, 2).astype(np.uint8))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)
    for i, t in enumerate(("MARIA DA SILVA", "RUA DAS FLORES 10", "CEP 30123-456", "BELO HORIZONTE")):
        d.text((12, 12 + 40 * i), t, font=f, fill=cor)
    ex = im.getexif()
    if orientacao != 1:
        ex[0x0112] = orientacao
    caminho = pasta / nome
    kw = {"qtables": qtables} if qtables else {"quality": q}
    im.save(caminho, "JPEG", subsampling=sub, exif=ex.tobytes(), **kw)
    return caminho


def _ctx(caminho: Path, img: np.ndarray, saida: Path) -> Contexto:
    it = Item(doc="dddd4444", split="treino", caminho=caminho, rel=caminho.name, pagina=0, paginas=1,
              sha_arquivo="d" * 64, sha_pixels="d" * 64, altura=img.shape[0], largura=img.shape[1])
    return Contexto(it, img, saida)


def _c(img, documento, acabamento=None):
    return types.SimpleNamespace(img=img, base=img, documento=documento, acabamento=acabamento or {}, fornecedores={},
                                 semente=7)


def _edicao(img, caixa, cor=(25, 40, 130), texto="JOSE ALVES"):
    """Edição 'limpa' (sem a compressão do JPEG): papel com grão na caixa e texto novo nítido por cima."""
    x0, y0, x1, y1 = caixa
    grao = np.random.default_rng(5).normal(0, 3, (y1 - y0, x1 - x0, 1))
    rec = np.clip(238 + grao, 0, 255).astype(np.uint8).repeat(3, 2)
    im = Image.fromarray(rec)
    ImageDraw.Draw(im).text((2, 2), texto, font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22), fill=cor)
    return Resultado(y0, y1, x0, x1, np.asarray(im).copy(), np.ones((y1 - y0, x1 - x0), bool), meta={})


def _aplicado(img, res):
    out = img.copy()
    sub = out[res.y0:res.y1, res.x0:res.x1]
    sub[:] = np.where(res.pegada[..., None], res.recorte, sub)
    return out


def _dist_grade(arm: np.ndarray, q: list, dentro: np.ndarray):
    """Distância média dos coeficientes DCT 8x8 (luma, grade do arquivo armazenado) aos múltiplos da tabela:
    (blocos inteiros dentro de ``dentro``, blocos sem nada de ``dentro``)."""
    y = arm.astype(np.float32) @ LUMA - 128
    Q = np.asarray(q, np.float32).reshape(8, 8)
    den, fora = [], []
    for by in range(arm.shape[0] // 8):
        for bx in range(arm.shape[1] // 8):
            m = dentro[by * 8:by * 8 + 8, bx * 8:bx * 8 + 8]
            if not (m.all() or not m.any()):
                continue
            k = cv2.dct(np.ascontiguousarray(y[by * 8:by * 8 + 8, bx * 8:bx * 8 + 8])) / Q
            (den if m.all() else fora).append(float(np.abs(k - np.round(k)).mean()))
    return float(np.mean(den)), float(np.mean(fora))


def _croma_fora_420(img: np.ndarray, m: np.ndarray) -> float:
    """Quanto o Cb foge do 4:2:0 (um valor por 2x2 px) nos pixels ``m``: |Cb − média do bloco 2x2|."""
    H, W = (img.shape[0] // 2) * 2, (img.shape[1] // 2) * 2
    cb = img[:H, :W].astype(np.float32) @ CB
    med = cb.reshape(H // 2, 2, W // 2, 2).mean((1, 3))
    r = np.abs(cb - np.repeat(np.repeat(med, 2, 0), 2, 1))
    return float(r[m[:H, :W]].mean())


class TestRequantizar(unittest.TestCase):
    """Item A: a área editada volta para a grade e a subamostragem do JPEG de origem."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_contexto_le_a_compressao_da_fonte(self):
        cam = _papel_jpeg(self.tmp, orientacao=6)
        img = documentos.carregar(cam)
        info = _ctx(cam, img, self.tmp).jpeg
        with Image.open(cam) as im:
            self.assertEqual(info["qtables"], [list(t) for _, t in sorted(im.quantization.items())])
        self.assertEqual((info["subsampling"], info["modo"], info["orientacao"], info["tamanho"]), (2, "RGB", 6, (331, 187)))
        self.assertEqual(img.shape[:2], (331, 187))  # exibida girada
        png = self.tmp / "doc.png"
        Image.fromarray(img).save(png)
        self.assertIsNone(_ctx(png, img, self.tmp).jpeg)
        self.assertIsNone(_ctx(cam, img, self.tmp).derivado(img, lambda pts: pts, "foto").jpeg)
        self.assertIsNone(_ctx(self.tmp / "nao_existe.jpg", img, self.tmp).jpeg)

    def _requantiza(self, orientacao, caixa_arm=(37, 61, 197, 101), qtables=None):
        cam = _papel_jpeg(self.tmp, f"o{orientacao}.jpg", orientacao=orientacao, qtables=qtables)
        img = documentos.carregar(cam)
        ctx = _ctx(cam, img, self.tmp)
        Hs, Ws = 187, 331
        # a mesma caixa do arquivo armazenado, levada para a imagem exibida
        y0, x0, y1, x1 = tecnicas._caixa_mapeada(lambda r, q: tecnicas._na_exibida(orientacao, Hs, Ws, r, q),
                                                 caixa_arm[1], caixa_arm[0], caixa_arm[3], caixa_arm[2])
        res = _edicao(img, (x0, y0, x1, y1))
        limpo = _aplicado(img, res)
        c = _c(img, ctx)
        txt = tecnicas._requantizar(c, res)
        out = _aplicado(img, res)
        peg = np.zeros(img.shape[:2], bool)
        peg[res.y0:res.y1, res.x0:res.x1] = res.pegada
        dentro = np.zeros(img.shape[:2], bool)
        dentro[y0:y1, x0:x1] = True
        q = ctx.jpeg["qtables"][0]
        arm = lambda a: np.ascontiguousarray(tecnicas._ARMAZENAR[orientacao](a))  # noqa: E731
        return txt, c, arm(limpo), arm(out), arm(dentro), arm(peg), q, (img, res, x0, y0, x1, y1)

    def test_edicao_volta_para_a_grade_do_jpeg(self):
        txt, c, limpo, out, dentro, peg, q, (img, res, x0, y0, x1, y1) = self._requantiza(1)
        d_antes, f_antes = _dist_grade(limpo, q, dentro)
        d_depois, f_depois = _dist_grade(out, q, dentro)
        self.assertGreater(d_antes - f_antes, 0.08, (d_antes, f_antes))  # o defeito: fora da grade
        self.assertLessEqual(abs(d_depois - f_depois), 0.05, (d_depois, f_depois))
        self.assertEqual(c.fornecedores, {"jpeg": "tabelas_da_fonte"})
        self.assertIn("4:2:0", txt)
        # o croma do texto novo colorido ficou 4:2:0 como o do texto em volta
        tinta = (out.astype(np.float32) @ LUMA) < 150
        borda = np.zeros_like(dentro)
        borda[:, :30] = True
        self.assertGreater(_croma_fora_420(limpo, tinta & dentro), 2 * _croma_fora_420(out, tinta & ~dentro & ~borda))
        self.assertLess(_croma_fora_420(out, tinta & dentro), 1.5 * _croma_fora_420(out, tinta & ~dentro & ~borda) + 0.2)
        # a pegada cobre o que mudou (MCUs inteiras em volta da caixa), e só isso
        mudou = (_aplicado(img, res) != img).any(2)
        np.testing.assert_array_equal(mudou & ~res_pegada_global(img, res), np.zeros_like(mudou))
        self.assertTrue(peg[dentro].all())

    def test_todas_as_orientacoes_exif(self):
        """A grade é a do arquivo ARMAZENADO: com rotação/espelhamento EXIF e tamanho que não é múltiplo de 16, a
        origem da grade na imagem exibida muda (calculada, não suposta em 0)."""
        for o in range(1, 9):
            _txt, c, limpo, out, dentro, _peg, q, _ = self._requantiza(o, qtables=ASSIMETRICA)
            self.assertEqual(q, ASSIMETRICA[0])
            d, f = _dist_grade(out, q, dentro)
            self.assertLessEqual(abs(d - f), 0.03, (o, d, f))
            self.assertEqual(c.fornecedores.get("jpeg"), "tabelas_da_fonte", o)

    def test_nunca_desliga_e_png_nao_mexe(self):
        cam = _papel_jpeg(self.tmp)
        img = documentos.carregar(cam)
        res = _edicao(img, (40, 60, 200, 100))
        antes = res.recorte.copy()
        c = _c(img, _ctx(cam, img, self.tmp), {"requantizar": "nunca"})
        self.assertIsNone(tecnicas._requantizar(c, res))
        np.testing.assert_array_equal(res.recorte, antes)
        self.assertEqual(c.fornecedores, {})
        png = self.tmp / "doc.png"
        Image.fromarray(img).save(png)
        self.assertIsNone(tecnicas._requantizar(_c(img, _ctx(png, img, self.tmp)), res))
        self.assertIsNone(tecnicas._requantizar(_c(img, None), res))


def _residuo(img, eixo=LUMA):
    """Resíduo do passa-alta linear de ``pericia`` (plano − gaussiana σ 1,5) de uma imagem inteira."""
    p = img.astype(np.float32) @ eixo
    return p - cv2.GaussianBlur(p, (0, 0), 1.5)


def _papel(H=160, W=260, sigma=3.0, croma=0.0, semente=1, nivel=235.0):
    """Papel com grão correlacionado (σ por canal: ``sigma`` na luma, ``croma`` independente em cada canal)."""
    rng = np.random.default_rng(semente)
    g = cv2.GaussianBlur(rng.normal(0, sigma * 2.2, (H, W)).astype(np.float32), (0, 0), 0.8)[..., None]
    c = cv2.GaussianBlur(rng.normal(0, croma * 2.2 + 1e-9, (H, W, 3)).astype(np.float32), (0, 0), 0.8) if croma else 0
    return np.clip(np.broadcast_to(nivel + g, (H, W, 3)) + c + 0.5, 0, 255).astype(np.uint8)


def _c_tec(img, tecnica="campo.mesma_fonte", acabamento=None, documento=None, base=None):
    """O contexto real de uma técnica (sem rodar a técnica): para testar o acabamento sozinho."""
    p = {"tecnica": tecnica, "campo": {"caixa": [10, 10, 60, 30], "texto": "X", "categoria": "nome", "tipo": "texto"},
         "semente": 7, "acabamento": acabamento or {"ruido": "auto", "disfarce": "nunca", "jpeg": 0}}
    return tecnicas._C(img, p, img if base is None else base, documento, None, None, None)


class TestGrao(unittest.TestCase):
    """Item B: o grão da edição medido e casado no papel (só papel, croma também), com rótulo verdadeiro."""

    def test_branco_mede_so_o_papel(self):
        """Texto novo nítido num retângulo liso: as bordas das letras entravam na medida e o ruído branco concluía
        'já igual' com o papel pela metade do grão (6 de 24 edições sem grão nenhum)."""
        from rotulador.ops import auto
        img = _papel(sigma=3.0)
        x0, y0, x1, y1 = 40, 50, 220, 100
        rec = np.full((y1 - y0, x1 - x0, 3), 235, np.uint8)
        im = Image.fromarray(rec)
        ImageDraw.Draw(im).text((4, 8), "JOSE ALVES 10", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30),
                                fill=(15, 15, 15))
        res = Resultado(y0, y1, x0, x1, np.asarray(im).copy(), np.ones((y1 - y0, x1 - x0), bool), meta={})
        res = auto._com_ruido(img, img, res, 3)
        self.assertEqual(res.meta.get("ruido"), "igualado")
        out = _aplicado(img, res)
        papel = (cv2.dilate(255 - out[..., 0], np.ones((5, 5), np.uint8)) < 60)  # longe da tinta
        r = out.astype(np.float32) - cv2.medianBlur(out, 3).astype(np.float32)
        dentro = np.zeros(papel.shape, bool)
        dentro[y0:y1, x0:x1] = True
        self.assertGreater(r[dentro & papel].std() / r[~dentro & papel].std(), 0.8)

    def test_espectral_em_papel_de_grao_fraco(self):
        """Grão de 0,37: o limite antigo (0,4) deixava o retângulo com variância zero."""
        img = _papel(sigma=0.45, semente=4)
        x0, y0, x1, y1 = 80, 50, 180, 110
        anel = np.ones(img.shape[:2], bool)
        anel[y0 - 5:y1 + 5, x0 - 5:x1 + 5] = False
        s_ref, _ = pericia._grao(img.astype(np.float32) @ LUMA, anel)
        self.assertTrue(0.05 < s_ref < 0.4, s_ref)
        rec = np.full((y1 - y0, x1 - x0, 3), 235.0, np.float32)
        res = types.SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1, pegada=np.ones(rec.shape[:2], bool), recorte=rec)
        novo, ok = pericia.casar_grao(rec, res, types.SimpleNamespace(img=img, base=img), 7)
        self.assertTrue(ok)
        s_reg, _ = pericia._grao(novo @ LUMA, np.ones(rec.shape[:2], bool))
        self.assertTrue(0.7 < s_reg / s_ref < 1.3, (s_reg, s_ref))

    def test_croma_casado_com_o_do_papel(self):
        x0, y0, x1, y1 = 80, 50, 180, 110
        todos = np.ones((y1 - y0, x1 - x0), bool)
        for croma in (1.5, 0.0):
            img = _papel(sigma=2.0, croma=croma, semente=5)
            anel = np.ones(img.shape[:2], bool)
            anel[y0 - 5:y1 + 5, x0 - 5:x1 + 5] = False
            rec = np.full((y1 - y0, x1 - x0, 3), 235.0, np.float32)
            res = types.SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1, pegada=todos, recorte=rec)
            novo, ok = pericia.casar_grao(rec, res, types.SimpleNamespace(img=img, base=img), 7)
            self.assertTrue(ok)
            for eixo in (tecnicas.pericia._CB, tecnicas.pericia._CR):
                s_ref, _ = pericia._grao(img.astype(np.float32) @ eixo, anel)
                s_reg, _ = pericia._grao(novo @ eixo, todos)
                if croma:  # papel colorido: o croma da região ganha o grão do croma do papel
                    self.assertTrue(0.7 < s_reg / s_ref < 1.3, (s_reg, s_ref))
                else:  # papel cinza: nada de croma
                    self.assertLess(s_reg, 0.01)

    def _pegada_texto(self, img, preencher):
        """Pegada com a forma do texto (apagador só-tinta): os buracos das letras antigas reconstruídos por
        ``preencher`` e o resto do retângulo intacto."""
        x0, y0, x1, y1 = 30, 40, 230, 110
        m = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(m).text((4, 10), "MARIA SILVA", font=ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 40), fill=255)
        pegada = cv2.dilate((np.asarray(m) > 60).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        rec = img[y0:y1, x0:x1].astype(np.float32)
        rec[pegada] = preencher(rec)[pegada]
        return types.SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1, pegada=pegada, recorte=rec), rec

    def test_pegada_com_forma_de_texto(self):
        img = _papel(sigma=3.0, semente=6)
        # reconstrução lisa (difusão/local): o grão entra só nos buracos, com o desvio do papel em volta
        res, rec = self._pegada_texto(img, lambda r: np.full(r.shape, 235.0, np.float32))
        novo, ok = pericia.casar_grao(rec, res, types.SimpleNamespace(img=img, base=img), 7)
        self.assertTrue(ok)
        np.testing.assert_array_equal(novo[~res.pegada], rec[~res.pegada])  # nada fora da pegada
        s_ref = pericia._grao(img.astype(np.float32) @ LUMA, np.ones(img.shape[:2], bool))[0]
        s_reg = pericia._grao_robusto(novo @ LUMA, res.pegada)
        self.assertTrue(0.7 < s_reg / s_ref < 1.3, (s_reg, s_ref))
        # reconstrução que já copiou o grão do papel (PatchMatch): quase nada a acrescentar (o estimador antigo, com
        # a tinta dilatada 3 px, não achava papel na pegada estreita e dobrava a variância)
        outro = _papel(sigma=3.0, semente=9)
        res, rec = self._pegada_texto(img, lambda r: outro[40:110, 30:230].astype(np.float32))
        novo, _ = pericia.casar_grao(rec, res, types.SimpleNamespace(img=img, base=img), 7)
        s_reg = pericia._grao_robusto(novo @ LUMA, res.pegada)
        self.assertLess(s_reg / s_ref, 1.2)

    def test_rotulo_diz_o_grao_que_entrou(self):
        x0, y0, x1, y1 = 40, 50, 200, 90
        for img, esperado in ((_papel(sigma=3.0), "espectral"), (np.full((160, 260, 3), 240, np.uint8), "nenhum")):
            c = _c_tec(img)
            rec = img[y0:y1, x0:x1].copy()
            rec[10:30, 10:150] = 240  # papel reconstruído liso
            res = Resultado(y0, y1, x0, x1, rec, np.ones((y1 - y0, x1 - x0), bool), meta={})
            res = tecnicas._acabar(c, res)
            self.assertEqual(res.meta["fornecedores"].get("grao"), esperado)
            self.assertEqual(any("grão" in x for x in res.meta["acabamento"]), esperado != "nenhum")
        # e o branco também: "já igual" não é rótulo de grão
        img = np.full((160, 260, 3), 240, np.uint8)
        c = _c_tec(img, "campo.reescrever", {"ruido": "sempre", "grao": "branco", "disfarce": "nunca"})
        res = tecnicas._acabar(c, Resultado(y0, y1, x0, x1, img[y0:y1, x0:x1].copy(), np.ones((y1 - y0, x1 - x0), bool), meta={}))
        self.assertEqual(res.meta["fornecedores"].get("grao"), "nenhum")

    def test_mesma_fonte_usa_grao_espectral(self):
        for s in range(12):  # o sorteio 70/30 não vale para a edição padrão
            c = _c_tec(_papel(), acabamento={"ruido": "auto"})
            c.rng.seed(s)
            self.assertEqual(tecnicas._grao_de(c, c.acabamento, "auto"), "espectral")
        c = _c_tec(_papel(), acabamento={"grao": "branco"})  # pedido explícito ainda vale
        self.assertEqual(tecnicas._grao_de(c, c.acabamento, "auto"), "branco")


class TestIluminacao(unittest.TestCase):
    """Item C: papel reconstruído chapado sobre o gradiente da foto deixava um degrau na borda."""

    def _foto(self):
        H, W = 200, 320
        luz = np.linspace(200, 230, W, dtype=np.float32)[None, :] + np.linspace(0, 10, H, dtype=np.float32)[:, None]
        rng = np.random.default_rng(2)
        return np.clip(luz[..., None] + rng.normal(0, 1.5, (H, W, 1)) + 0.5, 0, 255).astype(np.uint8).repeat(3, 2)

    def _degrau(self, out, caixa):
        x0, y0, x1, y1 = caixa
        y = out.astype(np.float32) @ LUMA
        return max(abs(float(y[y0:y1, x0:x0 + 3].mean() - y[y0:y1, x0 - 3:x0].mean())),
                   abs(float(y[y0:y1, x1 - 3:x1].mean() - y[y0:y1, x1:x1 + 3].mean())))

    def test_chapado_casa_a_luz_no_padrao(self):
        img = self._foto()
        caixa = (100, 70, 220, 110)
        x0, y0, x1, y1 = caixa
        for apagar, casa in (("chapado", True), ("telea", True), ("local", False), ("patchmatch", False)):
            c = _c_tec(img, acabamento={"ruido": "nunca", "disfarce": "nunca"})
            c.fornecedores["apagar"] = apagar
            cor = np.median(img[y0:y1, x0:x1].reshape(-1, 3), 0)
            rec = np.broadcast_to(cor, (y1 - y0, x1 - x0, 3)).astype(np.uint8).copy()
            res = Resultado(y0, y1, x0, x1, rec, np.ones(rec.shape[:2], bool), meta={})
            antes = self._degrau(_aplicado(img, res), caixa)
            res = tecnicas._acabar(c, res)
            depois = self._degrau(_aplicado(img, res), caixa)
            self.assertGreater(antes, 4.0)
            if casa:
                self.assertIn("perícia: iluminação do papel casada", res.meta["acabamento"], apagar)
                self.assertLess(depois, 2.0, apagar)
            else:
                self.assertFalse(any("iluminação" in x for x in res.meta["acabamento"]), apagar)


SEM = {"ruido": "nunca", "disfarce": "nunca"}


class TestMesmaFonte(tt.Base):
    """Itens D e E: a edição padrão apaga só a tinta com fundo local, e o rótulo diz o que foi usado."""

    def test_apaga_so_a_tinta_com_fundo_local(self):
        c = self.campo("nome")
        chamadas = []

        def espia(img, p, *a, **k):
            res = real(img, p, *a, **k)
            chamadas.append((dict(p), res.meta.get("apagar")))
            return res

        real = auto.trocar_campo
        with mock.patch.object(tecnicas.auto, "trocar_campo", side_effect=espia):
            res = self.rodar("campo.mesma_fonte", c, acabamento=SEM, conferir_ocr=False)
            res2 = self.rodar("campo.mesma_fonte", c, opcoes={"apagar": "chapado"}, acabamento=SEM, conferir_ocr=False)
        (p1, usado1), (p2, usado2) = chamadas
        self.assertEqual((p1["so_tinta"], p1["apagar"]), (True, "local"))
        self.assertEqual(res.meta["fornecedores"]["apagar"], usado1)  # o que o apagador disse que usou
        self.assertEqual((p2["so_tinta"], p2["apagar"]), (True, "chapado"))  # opção explícita vale
        self.assertEqual(res2.meta["fornecedores"]["apagar"], "chapado")

    def test_rotulo_da_fonte_pela_semelhanca(self):
        c = self.campo("nome")
        real = auto.ajustar_fonte_cache
        # abaixo de tecnicas.SEMELHANCA_MINIMA (0,60) a mesma fonte recusa (test_qualidade_saida); entre ela e o
        # limiar de identificação, edita e rotula "aproximada"
        for sem, metodo, esperado in ((0.93, "comparacao", "top10"), (0.7, "comparacao", "aproximada"),
                                      (None, "estimativa", "estimada")):
            def falso(*a, **k):
                return {**real(*a, **k), "semelhanca": sem, "metodo": metodo}

            with mock.patch.object(tecnicas.auto, "ajustar_fonte_cache", side_effect=falso):
                res = self.rodar("campo.mesma_fonte", c, acabamento=SEM, conferir_ocr=False)
            self.assertEqual(res.meta["fornecedores"]["fonte_identificada"], esperado, sem)
            self.assertEqual(res.meta["semelhanca_fonte"], sem)
            self.assertTrue(res.meta["texto_novo"])  # o campo é editado do mesmo jeito

    def test_rotacao_da_fonte_entra_no_desenho(self):
        img = np.full((80, 300, 3), 250, np.uint8)
        c = _c_tec(img)
        c._aj = {"fonte": "Arial", "tamanho": 30.0, "largura": 100, "negrito": 0, "x": 10.0, "y": 50.0, "rotacao": 1.5}
        self.assertEqual(c.params_fonte("ABC", 0, 0, 0)["rotacao"], 1.5)


def _bloco(alinhar, n=3):
    """Bloco de endereço com as linhas alinhadas pela ``alinhar``: (img, linhas do OCR, campos)."""
    im = Image.new("RGB", (900, 400), (250, 250, 250))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 28)
    textos = ["MARIA DA CONCEICAO SOUZA", "RUA DAS FLORES 10", "BAIRRO CENTRO NORTE"][:n]
    linhas = []
    for i, t in enumerate(textos):
        w = d.textlength(t, font=f)
        x = {"esquerda": 100, "direita": 800 - w, "centro": 450 - w / 2}[alinhar]
        d.text((x, 80 + 50 * i), t, font=f, fill=(20, 20, 20))
        b = d.textbbox((x, 80 + 50 * i), t, font=f)
        linhas.append({"caixa": [int(b[0]) - 3, int(b[1]) - 4, int(b[2]) + 3, int(b[3]) + 4], "texto": t})
    img = np.asarray(im).copy()
    campos = [{"id": f"t{i}", "caixa": ocr.caixa_da_linha(img, l["caixa"]), "texto": l["texto"], "tipo": "texto",
               "categoria": "endereço"} for i, l in enumerate(linhas)]
    return img, linhas, campos


class TestAlinhamento(unittest.TestCase):
    """Item F: o alinhamento vem do documento (as linhas vizinhas), não só da categoria."""

    def _c(self, alinhar, n=3, i=1):
        img, linhas, campos = _bloco(alinhar, n)
        doc = types.SimpleNamespace(img=img, linhas=linhas, campos=campos, doc="bloco", split="treino")
        p = {"tecnica": "campo.mesma_fonte", "campo": campos[i], "semente": 1}
        return tecnicas._C(img, p, img, doc, None, None, None)

    def test_le_o_alinhamento_das_vizinhas(self):
        for alinhar in ("esquerda", "direita", "centro"):
            self.assertEqual(tecnicas._alinhamento_no_documento(self._c(alinhar)), alinhar)

    def test_sem_vizinha_cai_na_regra_da_categoria(self):
        self.assertIsNone(tecnicas._alinhamento_no_documento(self._c("centro", n=1, i=0)))
        self.assertIsNone(tecnicas._alinhamento_no_documento(_c_tec(np.full((60, 200, 3), 250, np.uint8))))

    def test_mesma_fonte_passa_o_alinhamento_lido(self):
        c = self._c("centro")
        with mock.patch.object(tecnicas, "_t_campo_reescrever", return_value=Resultado(
                0, 2, 0, 2, np.zeros((2, 2, 3), np.uint8), np.ones((2, 2), bool), meta={})) as r, \
                mock.patch.object(tecnicas._C, "aj", new_callable=mock.PropertyMock,
                                  return_value={"fonte": "Arial", "tamanho": 28, "semelhanca": 0.9}):
            tecnicas._t_campo_mesma_fonte(c)
        self.assertEqual(r.call_args[0][1]["alinhar"], "centro")


def _linha_com_vizinhos(nome="JOSE DA SILVA", folga=24):
    """'NOME:' à esquerda, o nome e, logo depois, o CPF (tinta dos dois lados do campo)."""
    im = Image.new("RGB", (900, 120), (250, 250, 250))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30)
    d.text((20, 40), "NOME:", font=f, fill=(20, 20, 20))
    x_nome = int(d.textbbox((20, 40), "NOME:", font=f)[2]) + folga
    d.text((x_nome, 40), nome, font=f, fill=(20, 20, 20))
    x_cpf = int(d.textbbox((x_nome, 40), nome, font=f)[2]) + folga
    d.text((x_cpf, 40), "CPF 123.456.789-09", font=f, fill=(20, 20, 20))
    img = np.asarray(im).copy()
    caixa = ocr.caixa_tinta(img, [x_nome - 4, 30, x_cpf - 8, 85], 0)
    return img, {"id": "t1", "caixa": caixa, "texto": nome, "categoria": "nome", "tipo": "texto"}, x_cpf


class TestLargura(unittest.TestCase):
    """Item G: largura do valor novo perto da antiga; valor imposto largo demais condensa, nunca invade."""

    def _c(self, texto, categoria="endereço"):
        img = np.full((80, 600, 3), 250, np.uint8)
        p = {"tecnica": "campo.mesma_fonte", "semente": 3,
             "campo": {"caixa": [20, 20, 300, 50], "texto": texto, "categoria": categoria, "tipo": "texto"}}
        c = tecnicas._C(img, p, img, None, None, None, None)
        c._aj = {"fonte": "Arial", "tamanho": 30.0, "largura": 100, "negrito": 0, "x": 20.0, "y": 50.0}
        return c

    def test_fica_o_de_largura_mais_perto(self):
        c = self._c("QD 12 LT 5")
        cands = ["QD 7 LT 3", "QD 123 LT 45 CASA 2", "QD 1 LT 22"]
        with mock.patch.object(geradores, "parecido", side_effect=cands * 30):
            novo = tecnicas._novo_valor(c)
        self.assertEqual(novo, "QD 1 LT 22")  # o primeiro em [0,7; 1,25] era "QD 7 LT 3" (0,86 da largura)

    def test_original_curto_acha_valor_entre_mais_candidatos(self):
        c = self._c("RUA A, 10")
        cands = [f"RUA DOS GIRASSOIS AMARELOS, {i}" for i in range(30)] + ["RUA B, 12"] * 40
        with mock.patch.object(geradores, "parecido", side_effect=cands):
            self.assertEqual(tecnicas._novo_valor(c), "RUA B, 12")  # com 25 tentativas, "nenhum valor cabe"
        with mock.patch.object(geradores, "parecido", side_effect=[f"RUA DOS GIRASSOIS, {i}" for i in range(100)]):
            with self.assertRaises(ErroOperacao):
                tecnicas._novo_valor(c)

    def _rodar(self, img, campo, texto):
        p = {"tecnica": "campo.mesma_fonte", "campo": campo, "semente": 2, "texto": texto, "acabamento": SEM,
             "conferir_ocr": False}
        return executar("tecnica", img, p, vazia(*img.shape[:2]), base=img)

    def test_valor_imposto_condensa_e_nao_invade_o_vizinho(self):
        img, campo, x_cpf = _linha_com_vizinhos(folga=14)
        res = self._rodar(img, campo, "JOSE DE SOUZA")  # ~4% mais largo, sem papel livre dos lados
        mudou = (_aplicado(img, res) != img).any(2)
        self.assertFalse(mudou[:, x_cpf - 2:].any(), "escreveu por cima do CPF")
        self.assertIn("condensado", res.meta["descricao"])
        with self.assertRaises(ErroOperacao) as cm:
            self._rodar(img, campo, "JOSE DA SILVA SAURO NETO")
        self.assertIn("não cabe", str(cm.exception))


class TestAcentos(unittest.TestCase):
    """Item M: o valor falso tem acento quando o documento tem (decidido uma vez por documento)."""

    def setUp(self):
        tecnicas._acentos_doc.clear()
        self.img, linhas, campos = _bloco("esquerda")
        self.doc = types.SimpleNamespace(img=self.img, linhas=linhas, campos=campos, doc="docacento", split="treino")
        self.campo = campos[0]

    def _rodar(self, campo=None, documento=None):
        p = {"tecnica": "campo.corretivo", "campo": {**(campo or self.campo), "categoria": "nome"}, "semente": 1,
             "acabamento": SEM, "opcoes": {"fundo": "papel", "forma": "retangulo"}}
        return tecnicas.tecnica(self.img, p, vazia(*self.img.shape[:2]), base=self.img, documento=documento)

    def test_passa_acentos_para_os_geradores(self):
        pedidos = []

        @contextlib.contextmanager
        def usando(rng=None, split=None, hoje=None, cidade=None, *, acentos=None, documento=None):
            pedidos.append(acentos)
            yield

        with mock.patch.object(geradores, "usando", usando), \
                mock.patch.object(ocr, "usa_acentos", create=True, return_value=True) as usa:
            self._rodar(documento=self.doc)
            self._rodar(documento=self.doc)  # o mesmo documento: não pergunta de novo
            self.assertEqual(usa.call_count, 1)
            self.assertIs(usa.call_args.args[0], self.img)
            self.assertIs(usa.call_args.kwargs["linhas"], self.doc.linhas)
            self._rodar(documento=None)  # sem contexto: pelo recorte do campo
            self.assertEqual(list(usa.call_args.kwargs["caixa"]), list(self.campo["caixa"]))
        self.assertEqual(pedidos, [True, True, True])

    def test_geradores_sem_o_parametro_continuam_funcionando(self):
        @contextlib.contextmanager
        def usando(rng=None, split=None, hoje=None, cidade=None, *, documento=None):  # sem o parâmetro acentos
            yield

        with mock.patch.object(geradores, "usando", usando), \
                mock.patch.object(ocr, "usa_acentos", create=True, return_value=True) as usa:
            self._rodar(documento=self.doc)
        usa.assert_not_called()
        with mock.patch.object(ocr, "usa_acentos", create=True, return_value=None):
            self._rodar(documento=self.doc)  # None: "não sei" não vira pedido


def _linha_escaneada(fonte="times.ttf", sigma=1.1, texto="4821,37"):
    """Um valor escaneado: fonte fina, borrada como scan, com grão; (img, campo)."""
    im = Image.new("RGB", (520, 90), (246, 246, 244))
    ImageDraw.Draw(im).text((20, 25), texto, font=ImageFont.truetype(f"C:/Windows/Fonts/{fonte}", 30), fill=(25, 25, 25))
    a = cv2.GaussianBlur(np.asarray(im).astype(np.float32), (0, 0), sigma)
    img = np.clip(a + np.random.default_rng(1).normal(0, 2, (90, 520, 1)) + 0.5, 0, 255).astype(np.uint8)
    return img, {"id": "t1", "caixa": ocr.caixa_tinta(img, [10, 15, 300, 75], 0), "texto": texto, "categoria": "valor",
                 "tipo": "texto"}


def _nucleos(img, caixa, texto, mudou):
    """(núcleo da letra nova, mediana do núcleo das vizinhas): luma do percentil 10 da tinta de cada letra."""
    out = {}
    for g in glifos.segmentar(img, caixa, texto, None):
        out[g.i] = float(np.percentile(img[g.y0:g.y1, g.x0:g.x1][g.tinta].astype(np.float32) @ LUMA, 10))
    return (float(np.median([v for k, v in out.items() if k in mudou])),
            float(np.median([v for k, v in out.items() if k not in mudou and texto[k].isdigit()])))


class TestCorDaLetraNova(unittest.TestCase):
    """Item H: a letra redesenhada saía bem mais clara que as vizinhas (o traço desfocado não chega a alfa 1)."""

    def test_nucleo_da_letra_nova_igual_ao_das_vizinhas(self):
        for tec in ("caractere.redesenhar", "trecho.trocar"):
            for fonte, sigma in (("times.ttf", 1.1), ("calibri.ttf", 1.4), ("arial.ttf", 0.8)):
                img, campo = _linha_escaneada(fonte, sigma)
                p = {"tecnica": tec, "campo": campo, "semente": 1, "acabamento": SEM, "conferir_ocr": False,
                     "opcoes": {"apagar": "patchmatch"}}
                res = executar("tecnica", img, p, vazia(*img.shape[:2]), base=img)
                novo = res.meta["texto_novo"]
                mudou = {k for k in range(len(novo)) if k >= len(campo["texto"]) or novo[k] != campo["texto"][k]}
                out = _aplicado(img, res)
                n, v = _nucleos(out, ocr.caixa_tinta(out, [10, 15, 300, 75], 0), novo, mudou)
                self.assertLess(abs(n - v), 13, (tec, fonte, n, v))  # antes: 15 a 36 níveis mais clara

    def test_calibrar_cor_compensa_o_desfoque(self):
        a = np.zeros((40, 40), np.float32)
        a[8:32, 18:21] = 1  # traço de 3 px
        a = cv2.GaussianBlur(a, (0, 0), 1.2)
        papel, alvo = np.float32([245, 245, 243]), np.float32([120, 120, 125])
        cor = glifos.calibrar_cor(a, papel, alvo)
        self.assertLess(float(cor @ LUMA), float(alvo @ LUMA))  # mais escura que o alvo: o núcleo não chega a alfa 1
        comp = papel + (cor - papel) * a[..., None]
        m = mascara_tinta(np.clip(comp + 0.5, 0, 255).astype(np.uint8)).astype(bool)
        self.assertAlmostEqual(float(glifos._nucleo(comp[m], papel) @ LUMA), float(alvo @ LUMA), delta=2.0)


class TestFantasma(tt.Base):
    """Item I: onde a letra antiga foi apagada com papel liso (chapado, difusão) entra o grão do papel."""

    def _grao_no_buraco(self, res, campo):
        out = self.aplicado(res)
        x0, y0, x1, y1 = campo["caixa"]
        peg = res_pegada_global(self.img, res)
        tinta_antes = cv2.dilate(mascara_tinta(self.img), np.ones((3, 3), np.uint8)) > 0
        tinta_depois = cv2.dilate(mascara_tinta(out), np.ones((7, 7), np.uint8)) > 0
        buraco = peg & tinta_antes & ~tinta_depois  # onde havia letra e agora é papel
        self.assertGreater(buraco.sum(), 40)
        papel = np.zeros(peg.shape, bool)
        papel[y0 - 30:y1 + 30, x0 - 30:x1 + 30] = True
        papel &= ~peg & ~(cv2.dilate(mascara_tinta(self.img), np.ones((7, 7), np.uint8)) > 0)
        return (pericia._grao_robusto(out.astype(np.float32) @ LUMA, buraco),
                pericia._grao(self.img.astype(np.float32) @ LUMA, papel)[0])

    def test_letra_apagada_ganha_grao(self):
        c = self.campo("valor")
        for apagar in ("chapado", "telea"):
            for grao in ("espectral", "branco"):
                res = self.rodar("caractere.apagar", c, opcoes={"vao": "deixar", "apagar": apagar, "alvo": "digito",
                                                                "vestigio": "nenhum"},
                                 acabamento={"ruido": "auto", "disfarce": "nunca", "grao": grao}, conferir_ocr=False)
                s_buraco, s_papel = self._grao_no_buraco(res, c)
                self.assertGreater(s_buraco / s_papel, 0.7, (apagar, grao, s_buraco, s_papel))
                self.assertEqual(res.meta["fornecedores"]["grao"], grao)


class TestColagemInteira(unittest.TestCase):
    """Item J: cópia sem escala colada em pixel inteiro, como no Paint/Photoshop (sem a bilinear que amaciava)."""

    def test_posicionar_inteiro_nao_reamostra(self):
        rng = np.random.default_rng(0)
        rec = rng.uniform(0, 255, (12, 9, 3)).astype(np.float32)
        a = rng.uniform(0, 1, (12, 9)).astype(np.float32)
        caixa, r2, a2 = tecnicas._posicionar(rec, a, 10.4, 5.6, inteiro=True)
        self.assertEqual(caixa, (10, 6, 19, 18))
        np.testing.assert_array_equal(r2, rec)
        np.testing.assert_array_equal(a2, a)
        caixa, r3, _ = tecnicas._posicionar(rec, a, 10.4, 5.6)  # subpixel: reamostra (o que fica para a escala)
        self.assertEqual(r3.shape[:2], (13, 10))

    def test_tecnicas_de_copia_registram(self):
        img, campo = _linha_escaneada("arial.ttf", 0.8, "4821,37")
        p = {"tecnica": "caractere.transpor", "campo": campo, "semente": 1, "acabamento": SEM, "conferir_ocr": False}
        res = executar("tecnica", img, p, vazia(*img.shape[:2]), base=img)
        self.assertIs(res.meta["reamostrado"], False)


class TestCorretivoLiquido(unittest.TestCase):
    """Item K: o corretivo líquido (objeto físico) leva o grão do papel e relevo; o retângulo digital fica liso."""

    def test_liquido_tem_o_grao_do_papel(self):
        from tests.test_auto import conta
        from tests.test_humano import CAIXA, aplicar, aplicado
        img = conta(7)
        x0, y0, x1, y1 = CAIXA
        dentro = np.zeros(img.shape[:2], bool)
        dentro[y0 + 4:y1 - 4, x0 + 8:x1 - 8] = True
        for s in (1, 2, 3, 4):
            out = aplicado(img, aplicar(img, "campo.corretivo", {"fundo": "papel", "forma": "liquido"}, semente=s))
            mudou = (out != img).any(2)
            anel = np.zeros(img.shape[:2], bool)
            anel[y0 - 60:y1 + 60, x0 - 20:x1 + 20] = True
            anel &= ~mudou & ~pericia._tinta_larga(img)
            s_dentro = pericia._grao(out.astype(np.float32) @ LUMA, dentro)[0]
            s_papel = pericia._grao(img.astype(np.float32) @ LUMA, anel)[0]
            self.assertTrue(0.75 < s_dentro / s_papel < 1.3, (s, s_dentro, s_papel))  # antes: 0,4 a 1,1 do papel
            self.assertLess(float((out[dentro] == 255).all(1).mean()), 0.05)  # o grão não é cortado no branco
        out = aplicado(img, aplicar(img, "campo.corretivo", {"fundo": "papel", "forma": "retangulo"}))
        self.assertTrue((out[dentro] == out[dentro][0]).all())  # a ferramenta de retângulo continua lisa


def _dois_estilos():
    """Documento com o nome em maiúsculas escuras e nítidas e outro nome em caixa mista, cinza e borrado."""
    im = Image.new("RGB", (900, 320), (248, 248, 246))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30)
    d.text((40, 40), "MARIA DOS SANTOS", font=f, fill=(20, 20, 20))
    borrado = Image.new("RGB", (900, 320), (248, 248, 246))
    ImageDraw.Draw(borrado).text((40, 140), "Pedro Alves Costa", font=f, fill=(110, 110, 110))
    a = np.asarray(im).copy()
    b = cv2.GaussianBlur(np.asarray(borrado), (0, 0), 1.6)
    a[120:200] = b[120:200]
    return a


class TestCopiaInterna(unittest.TestCase):
    """Item L: a cópia interna só usa doador do mesmo estilo (altura, tinta, caixa das letras, nitidez)."""

    def _rodar(self, img, textos):
        campos = []
        for i, (y, t) in enumerate(textos):
            campos.append({"id": f"t{i}", "caixa": ocr.caixa_tinta(img, [30, y - 8, 880, y + 45], 0), "texto": t,
                           "categoria": "nome", "tipo": "texto"})
        doc = types.SimpleNamespace(img=img, campos=campos, linhas=[], doc="estilos", split="treino")
        p = {"tecnica": "campo.copiar_interno", "campo": campos[0], "semente": 1, "acabamento": SEM,
             "conferir_ocr": False, "opcoes": {"colagem": "tinta", "apagar": "patchmatch"}}
        return executar("tecnica", img, p, vazia(*img.shape[:2]), base=img, documento=doc)

    def test_recusa_doador_de_outro_estilo(self):
        img = _dois_estilos()
        with self.assertRaises(ErroOperacao) as cm:
            self._rodar(img, [(40, "MARIA DOS SANTOS"), (140, "Pedro Alves Costa")])
        self.assertIn("mesmo estilo", str(cm.exception))

    def test_aceita_doador_do_mesmo_estilo(self):
        img = _dois_estilos()
        ImageDraw.Draw(im := Image.fromarray(img)).text((40, 240), "JOAO PEREIRA LIMA",
                                                         font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30),
                                                         fill=(20, 20, 20))
        img = np.asarray(im).copy()
        res = self._rodar(img, [(40, "MARIA DOS SANTOS"), (140, "Pedro Alves Costa"), (240, "JOAO PEREIRA LIMA")])
        self.assertEqual(res.meta["texto_novo"], "JOAO PEREIRA LIMA")


def res_pegada_global(img, res):
    m = np.zeros(img.shape[:2], bool)
    m[res.y0:res.y1, res.x0:res.x1] = res.pegada
    return m


if __name__ == "__main__":
    unittest.main()
