"""Acabamento forense (rotulador/pericia.py): casa iluminação, grão espectral e nitidez na região
editada, medindo sempre o papel original (a própria região antes da edição, ou o anel em volta); e
integra no gerador como nível controlável e rotulado, sem mudar o comportamento 'auto' de hoje."""
import types
import unittest
from pathlib import Path

import cv2
import numpy as np

from rotulador import pericia
from rotulador.ops import executar
from rotulador.selecao import mascara_tinta, vazia
from tests.test_humano import CAIXA, aplicado, aplicar
from tests.test_auto import conta


def _res(x0, y0, x1, y1, pegada, recorte):
    return types.SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1, pegada=pegada, recorte=recorte, meta={})


def _c(img, base=None):
    return types.SimpleNamespace(img=img, base=base if base is not None else img, semente=7)


def _lag(a, eixo=1):
    """Autocorrelação lag-1 (horizontal; vertical com eixo=0): ~0 é ruído branco, >0 grão correlacionado."""
    a = a - a.mean()
    if eixo == 1:
        return float((a[:, :-1] * a[:, 1:]).mean() / (a.var() + 1e-6))
    return float((a[:-1, :] * a[1:, :]).mean() / (a.var() + 1e-6))


def _papel_com_grao(grao):
    """Papel chapado (235) com o grão dado; devolve (img, caixa da região, rec liso sem grão)."""
    img = np.clip(235 + grao, 0, 255).astype(np.uint8)[..., None].repeat(3, 2)
    x0, y0, x1, y1 = 85, 65, 155, 115
    rec = np.full((y1 - y0, x1 - x0, 3), 235.0, np.float32)
    return img, (x0, y0, x1, y1), rec


def _rampa(H=160, W=220):
    """Papel com gradiente horizontal de luz REALISTA (30 níveis em 220 px): o delta da iluminação é limitado
    a ±6 níveis de propósito, e a região de 70 px pede ±4,8."""
    rampa = np.linspace(200, 230, W).astype(np.float32)
    return np.repeat(np.repeat(rampa[None, :], H, 0)[..., None], 3, 2).astype(np.uint8)


def _texto_nitido(sigma_viz):
    """Vizinhança com texto borrado como scan (σ) e região com texto novo nítido; devolve (img, caixa, rec)."""
    img = np.full((120, 300, 3), 245, np.uint8)
    cv2.putText(img, "30 12 34 56", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 2, cv2.LINE_AA)
    img = cv2.GaussianBlur(img, (0, 0), sigma_viz)
    x0, y0, x1, y1 = 120, 42, 185, 90  # a região cai sobre parte do texto; a vizinhança (margem 24) tem o resto
    recu = np.full((y1 - y0, x1 - x0, 3), 245, np.uint8)
    cv2.putText(recu, "99", (6, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (15, 15, 15), 2, cv2.LINE_AA)
    return img, (x0, y0, x1, y1), recu.astype(np.float32)


class TestEqualizadores(unittest.TestCase):
    def test_iluminacao_faz_o_fundo_seguir_o_gradiente(self):
        img = _rampa()
        x0, y0, x1, y1 = 80, 60, 150, 100
        pegada = np.ones((y1 - y0, x1 - x0), bool)
        rec = np.full((y1 - y0, x1 - x0, 3), 215.0, np.float32)  # região chapada (corretivo da cor do papel)
        novo, ok = pericia.casar_iluminacao(rec, _res(x0, y0, x1, y1, pegada, rec), _c(img))
        self.assertTrue(ok)
        incl = lambda a: float(np.polyfit(np.arange(a.shape[1]), (a @ pericia._LUMA).mean(0), 1)[0])  # noqa: E731
        orig = img[y0:y1, x0:x1].astype(np.float32)
        self.assertLess(incl(rec), 0.05)  # era chapada
        self.assertGreater(incl(novo), 0.5 * incl(orig))  # passou a subir como o papel
        self.assertAlmostEqual(incl(novo), incl(orig), delta=0.02)
        self.assertLessEqual(np.abs(novo - rec).max(), 6.0)  # nunca mais que ±6 níveis

    def test_iluminacao_nao_mexe_em_copia_do_papel(self):
        """A referência é o papel da PRÓPRIA região no original, com a mesma máscara (tinta dilatada fora):
        uma cópia exata do original (gradiente, grão e texto) não muda nada. Extrapolar o anel clareava a
        região (+3,6 níveis) e deixava um retângulo claro em volta do texto."""
        rng = np.random.default_rng(3)
        img = np.clip(_rampa().astype(np.float32) + rng.normal(0, 2.5, (160, 220, 1)), 0, 255).astype(np.uint8)
        cv2.putText(img, "ABC 123", (60, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2, cv2.LINE_AA)
        x0, y0, x1, y1 = 80, 60, 150, 100  # com texto dentro
        pegada = np.ones((y1 - y0, x1 - x0), bool)
        rec = img[y0:y1, x0:x1].astype(np.float32)
        novo, ok = pericia.casar_iluminacao(rec, _res(x0, y0, x1, y1, pegada, rec), _c(img))
        self.assertLess(abs((novo - rec).mean()), 0.3)
        self.assertFalse(ok)  # nada a casar: o sinal não entra na anotação

    def test_iluminacao_cai_para_o_anel_sem_papel_no_original(self):
        """Original todo tinta na região (nada de papel para comparar): vale o papel da vizinhança."""
        img = _rampa()
        x0, y0, x1, y1 = 80, 60, 150, 100
        img[y0:y1, x0:x1] = 20  # bloco preto no original
        pegada = np.ones((y1 - y0, x1 - x0), bool)
        rec = np.full((y1 - y0, x1 - x0, 3), 215.0, np.float32)  # coberto com corretivo chapado
        novo, ok = pericia.casar_iluminacao(rec, _res(x0, y0, x1, y1, pegada, rec), _c(img))
        self.assertTrue(ok)
        incl = lambda a: float(np.polyfit(np.arange(a.shape[1]), (a @ pericia._LUMA).mean(0), 1)[0])  # noqa: E731
        self.assertAlmostEqual(incl(novo), incl(_rampa()[y0:y1, x0:x1].astype(np.float32)), delta=0.02)

    def _confere_grao(self, grao):
        """O grão acrescentado a uma região lisa tem o lag-1 (h e v) do grão CRU do papel (±0,10) e o desvio
        da região fica em 0,85-1,15 do papel, pelo estimador do módulo. Devolve o grão acrescentado."""
        img, (x0, y0, x1, y1), rec = _papel_com_grao(grao)
        pegada = np.ones((y1 - y0, x1 - x0), bool)
        novo, ok = pericia.casar_grao(rec, _res(x0, y0, x1, y1, pegada, rec), _c(img), 7)
        self.assertTrue(ok)
        add = (novo - rec)[..., 0]  # o ruído sintetizado (não há mais nada mexendo em ``rec``)
        self.assertGreater(add.std(), 1.0)  # ganhou grão
        ref = img[y0:y1, x0:x1, 0].astype(np.float32)  # o papel é chapado: o que sobra é o grão cru
        for eixo in (1, 0):
            self.assertAlmostEqual(_lag(add, eixo), _lag(ref, eixo), delta=0.10)  # mesmo espectro do papel
        todos = np.ones(pegada.shape, bool)
        s_reg, _ = pericia._grao(novo @ pericia._LUMA, todos)
        s_ref, _ = pericia._grao(img[y0:y1, x0:x1].astype(np.float32) @ pericia._LUMA, todos)
        self.assertTrue(0.85 <= s_reg / s_ref <= 1.15, s_reg / s_ref)
        return add

    def test_grao_espectral_acrescenta_ruido_correlacionado(self):
        # a referência era o resíduo da mediana 3x3 (não linear, 'azulado': lag-1 0,38 para um grão de 0,76),
        # com tolerância 0,30; agora é o grão cru do papel, com 0,10, nos dois eixos
        rng = np.random.default_rng(1)
        grao = cv2.GaussianBlur(rng.normal(0, 30, (170, 230)).astype(np.float32), (0, 0), 1.0)  # grão correlacionado
        add = self._confere_grao(grao)
        self.assertGreater(_lag(add), 0.5)  # correlacionado como o papel (lag-1 ~0,76), não branco (que daria ~0)

    def test_grao_branco_continua_branco(self):
        rng = np.random.default_rng(1)
        add = self._confere_grao(rng.normal(0, 2.5, (170, 230)).astype(np.float32))
        self.assertLess(abs(_lag(add)), 0.08)  # o resíduo da mediana deixava o grão 'azulado' (lag-1 -0,15)

    def test_nitidez_desfoca_so_o_que_esta_nitido_demais(self):
        img, (x0, y0, x1, y1), rec = _texto_nitido(1.2)
        pegada = np.ones((y1 - y0, x1 - x0), bool)
        antes = pericia._nitidez(rec @ pericia._LUMA, rec.min(2) < 128)
        novo, ok = pericia.casar_nitidez(rec, _res(x0, y0, x1, y1, pegada, rec), _c(img))
        self.assertTrue(ok)
        depois = pericia._nitidez(novo @ pericia._LUMA, novo.min(2) < 128)
        self.assertLess(depois, antes)  # ficou menos nítido, mais perto da vizinhança

    def test_nitidez_fica_perto_do_alvo(self):
        """σ buscado fino (0,3..1,6 passo 0,1) e escolhido pelo que fica MAIS PERTO da vizinhança, nunca abaixo
        de 0,95 do alvo (o primeiro σ de uma lista grosseira passava do ponto: 0,91)."""
        for sigma_viz in (1.2, 0.8):
            img, (x0, y0, x1, y1), rec = _texto_nitido(sigma_viz)
            pegada = np.ones((y1 - y0, x1 - x0), bool)
            novo, ok = pericia.casar_nitidez(rec, _res(x0, y0, x1, y1, pegada, rec), _c(img))
            self.assertTrue(ok, sigma_viz)
            m = 24
            viz = img[y0 - m:y1 + m, x0 - m:x1 + m]
            peg = np.zeros(viz.shape[:2], bool)
            peg[m:-m, m:-m] = True
            alvo = pericia._nitidez(viz @ pericia._LUMA, mascara_tinta(viz).astype(bool) & ~peg)
            tinta = mascara_tinta(rec.astype(np.uint8)).astype(bool)
            depois = pericia._nitidez(novo @ pericia._LUMA, tinta)
            self.assertGreaterEqual(depois, 0.95 * alvo, sigma_viz)
            self.assertLessEqual(depois, 1.08 * alvo, sigma_viz)


class TestAplicar(unittest.TestCase):
    """Contrato de ``aplicar(rec, res, c, nivel, semente, grao=None)``."""

    def setUp(self):
        rng = np.random.default_rng(2)
        self.img, (x0, y0, x1, y1), self.rec = _papel_com_grao(rng.normal(0, 2.5, (170, 230)).astype(np.float32))
        self.res = _res(x0, y0, x1, y1, np.ones((y1 - y0, x1 - x0), bool), self.rec)

    def _aplicar(self, nivel, **kw):
        return pericia.aplicar(self.rec, self.res, _c(self.img), nivel, 7, **kw)

    def test_grao_espectral_no_padrao_so_quando_pedido(self):
        novo, feitos = self._aplicar("padrao", grao="espectral")
        self.assertEqual(feitos, ["grão espectral do papel"])
        self.assertGreater((novo - self.rec).std(), 1.0)
        novo, feitos = self._aplicar("padrao")  # sem ``grao``: o de sempre (o ruído branco fica com ajustes)
        self.assertEqual(feitos, [])
        np.testing.assert_array_equal(novo, self.rec)

    def test_cru_nao_faz_nada(self):
        novo, feitos = self._aplicar("cru", grao="espectral")
        self.assertEqual(feitos, [])
        np.testing.assert_array_equal(novo, self.rec)

    def test_grao_nos_outros_niveis(self):
        self.assertNotIn("grão espectral do papel", self._aplicar("profissional")[1])
        self.assertIn("grão espectral do papel", self._aplicar("profissional", grao="espectral")[1])
        self.assertIn("grão espectral do papel", self._aplicar("forense")[1])


class TestIntegracao(unittest.TestCase):
    def setUp(self):
        self.img = conta(7)

    def _acabar(self, pericia_nivel):
        op = {"fundo": "papel", "forma": "retangulo"}
        return aplicar(self.img, "campo.corretivo", op, acabamento={"ruido": "nunca", "disfarce": "nunca", "pericia": pericia_nivel})

    def test_auto_nao_muda_o_de_hoje(self):
        res = aplicar(self.img, "campo.corretivo", {"fundo": "papel", "forma": "retangulo"})
        self.assertEqual(res.meta.get("pericia"), "auto")
        self.assertFalse(any("perícia" in x for x in res.meta.get("acabamento", [])))

    def test_forense_casa_sinais_e_nao_vaza(self):
        x0, y0, x1, y1 = CAIXA
        base = self._acabar("cru")
        forense = self._acabar("forense")
        self.assertEqual(forense.meta["pericia"], "forense")
        self.assertTrue(any("perícia:" in x for x in forense.meta["acabamento"]), forense.meta["acabamento"])
        # o retângulo chapado do corretivo deixou de ser perfeitamente liso (ganhou luz/grão do papel)
        reg_cru = aplicado(self.img, base)[y0:y1, x0:x1].astype(np.float32)
        reg_for = aplicado(self.img, forense)[y0:y1, x0:x1].astype(np.float32)
        self.assertGreater(reg_for.std(), reg_cru.std() + 0.5)
        # nada mudou fora da caixa do corretivo (+ margem da própria técnica)
        novo = aplicado(self.img, forense)
        mudou = np.abs(novo.astype(int) - self.img.astype(int)).max(2) > 0
        H, W = self.img.shape[:2]
        fora = np.ones((H, W), bool)
        m = (y1 - y0)
        fora[max(0, y0 - m):min(H, y1 + m), max(0, x0 - m):min(W, x1 + m)] = False
        self.assertFalse((mudou & fora).any())


@unittest.skipUnless(__import__("tests.test_auto", fromlist=["TEM_OCR"]).TEM_OCR, "OCR não instalado")
class TestAPIPericia(unittest.TestCase):
    def setUp(self):
        import tempfile
        from app import RAIZ, carregar_categorias, criar_app
        from PIL import Image
        from rotulador.registro import SPLITS, Registro
        self.tmp = Path(tempfile.mkdtemp())
        for i, sp in enumerate(SPLITS):
            (self.tmp / "dados" / sp).mkdir(parents=True)
            Image.fromarray(conta(50 + i)).save(self.tmp / "dados" / sp / f"c{i}.png")
        self.reg = Registro(self.tmp / "dados", self.tmp / "saida", log=lambda *_: None)
        self.c = criar_app(self.reg, self.tmp / "saida", carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_receita_forense_grava_e_filtra(self):
        import json
        import time
        rec = {"documentos": {"splits": ["validacao"]}, "variacoes": 2, "edicoes": [1, 2],
               "tecnicas": {"campo.reescrever": 1, "campo.remover": 1},
               "acabamento": {"ruido": "auto", "disfarce": "nunca", "pericia": "forense"},
               "falsificadores": {"prob": 0}}  # sem falsificador: a perícia vem da receita
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(240):
            if not self.c.get("/api/gerador/status").get_json()["rodando"]:
                break
            time.sleep(0.5)
        anots = [json.loads(a.read_text(encoding="utf-8")) for a in (self.tmp / "saida" / "validacao" / "anotacoes").glob("*.json")]
        self.assertTrue(anots and all(a.get("pericia") == "forense" for a in anots), [a.get("pericia") for a in anots])
        self.assertTrue(any("perícia:" in x for a in anots for e in a["edicoes"] for x in (e.get("acabamento") or [])))
        self.assertEqual(self.c.get("/api/catalogo/itens?pericia=forense").get_json()["total"], len(anots))
        self.assertEqual(self.c.get("/api/catalogo/itens?pericia=auto").get_json()["total"], 0)
        ds = json.loads((self.tmp / "saida" / "datasheet.json").read_text(encoding="utf-8"))
        self.assertEqual(ds["splits"]["validacao"]["pericia"], {"forense": len(anots)})


if __name__ == "__main__":
    unittest.main()
