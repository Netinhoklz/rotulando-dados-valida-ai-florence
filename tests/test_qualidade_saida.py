"""Qualidade do dado gerado (rodada de 13/10/2026): o peso do traço do texto novo casa com o do documento, a fonte não
identificada não é usada pela "mesma fonte", e o negrito negativo afina o traço."""
import unittest
from unittest import mock

from rotulador import glifos
from rotulador.ops import auto, tecnicas, texto
from rotulador.sessao import ErroOperacao
from tests.test_tecnicas import Base


class TestAfinar(unittest.TestCase):
    def test_negrito_negativo_afina_nos_dois_rasterizadores(self):
        for ras in ("freetype", "nativo"):
            pesos = []
            for neg in (-1.5, -0.5, 0, 1):
                a = texto.renderizar({"texto": "FEV/2026", "fonte": "Arial Bold", "tamanho": 40, "negrito": neg, "x": 50,
                                      "y": 80, "rasterizador": ras}, 200, 400)[4]
                pesos.append(glifos.peso_traco(a))
            self.assertEqual(pesos, sorted(pesos), ras)  # mais negrito, mais grosso; negativo, mais fino
            self.assertLess(pesos[0], 0.85 * pesos[2], ras)


class TestPesoDoTraco(Base):
    def test_mesma_fonte_registra_o_peso_e_ele_casa(self):
        for cat in ("nome", "valor"):
            c = self.campo(cat)
            res = self.rodar("campo.mesma_fonte", c, acabamento={"ruido": "nunca", "disfarce": "nunca", "jpeg": 0})
            k = res.meta.get("peso_traco")
            self.assertIsNotNone(k, cat)
            self.assertLess(abs(k - 1), tecnicas.PESO_CORRIGIR, cat)

    def test_traco_que_nao_casa_e_corrigido(self):
        """A fonte identificada mais pesada que o papel (negrito a mais no ajuste): a edição sai com o peso certo."""
        c = self.campo("nome")
        original = auto.ajustar_fonte_cache

        def pesada(*a, **k):
            return {**original(*a, **k), "negrito": 3.0}
        with mock.patch.object(tecnicas.auto, "ajustar_fonte_cache", side_effect=pesada) as m:
            res = self.rodar("campo.mesma_fonte", c, semente=5,
                             acabamento={"ruido": "nunca", "disfarce": "nunca", "jpeg": 0})
        self.assertTrue(m.called)
        self.assertLess(abs(res.meta["peso_traco"] - 1), tecnicas.PESO_CORRIGIR)

    def test_fonte_nao_identificada_recusa(self):
        c = self.campo("nome")
        original = auto.ajustar_fonte_cache

        def ruim(*a, **k):
            return {**original(*a, **k), "semelhanca": 0.4}
        with mock.patch.object(tecnicas.auto, "ajustar_fonte_cache", side_effect=ruim) as m:
            with self.assertRaisesRegex(ErroOperacao, "não foi identificada"):
                self.rodar("campo.mesma_fonte", c, semente=6)
        self.assertTrue(m.called)


class TestQrNoDocumento(unittest.TestCase):
    """O QR do PIX no boleto (1,5 px por módulo, numa célula da tabela, com o rótulo em cima) e o QR comum são
    achados; texto, código de barras e o DataMatrix sem os 3 cantos, não. Na conta real (NIO) eram 0 de 2."""

    @staticmethod
    def _qr(texto_qr: str, modulo: float):
        import cv2
        import numpy as np
        m = cv2.QRCodeEncoder.create().encode(texto_qr)  # 1 px por módulo, com a zona quieta
        grande = cv2.resize(m, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
        lado = int(round(m.shape[0] * modulo))
        q = cv2.resize(grande, (lado, lado), interpolation=cv2.INTER_AREA)
        return np.repeat(q[..., None], 3, axis=2)

    def test_qr_miudo_na_celula_e_comum(self):
        import cv2
        import numpy as np

        from rotulador import campos
        img = np.full((1400, 1000, 3), 250, np.uint8)
        pix = self._qr("00020126580014BR.GOV.BCB.PIX0136" + "a" * 36 + "5204000053039865802BR5913FULANO DE TAL6008"
                       "BRASILIA62070503***6304ABCD", 1.5)
        y, x = 300, 400
        img[y:y + pix.shape[0], x:x + pix.shape[1]] = pix
        cv2.rectangle(img, (x - 60, y - 40), (x + pix.shape[1] + 60, y + pix.shape[0] + 30), (30, 30, 30), 2)  # célula
        cv2.putText(img, "PIX", (x + pix.shape[1] // 2 - 20, y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 2)
        comum = self._qr("https://exemplo.com.br/app", 4.0)
        img[900:900 + comum.shape[0], 150:150 + comum.shape[1]] = comum
        for k in range(8):  # um parágrafo de texto
            cv2.putText(img, "Pague sua fatura em dia e evite juros", (520, 900 + 30 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (30, 30, 30), 2)
        for k in range(60):  # código de barras
            img[1250:1330, 100 + 9 * k:100 + 9 * k + (3 if k % 3 else 6)] = 20
        achados = campos.detectar_qr(img)
        self.assertEqual(len(achados), 2, achados)
        alvo_pix = [x, y, x + pix.shape[1], y + pix.shape[0]]
        q = min(achados, key=lambda c: abs(c[1] - y))
        self.assertTrue(all(abs(a - b) <= 6 for a, b in zip(q, alvo_pix)), (q, alvo_pix))  # justo: sem o "PIX"


class TestMarcasNovas(unittest.TestCase):
    """Caneta, grampo, mancha e canto dobrado: negativos difíceis iguais no par e longe da edição."""

    def test_iguais_no_par_e_longe_da_edicao(self):
        import cv2
        import numpy as np

        from rotulador import marcas
        img = np.full((1400, 1000, 3), 248, np.uint8)
        for k in range(30):  # texto para a caneta marcar
            cv2.putText(img, "Consumo do mes 182 kWh R$ 81,89", (60, 60 + 44 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (40, 40, 40), 2)
        regiao = np.zeros((1400, 1000), bool)
        regiao[600:640, 300:700] = True
        for tipo in ("caneta", "grampo", "mancha", "canto_dobrado"):
            self.assertIn(tipo, marcas.TIPOS)
            self.assertIn(tipo, marcas.NOMES)
            feitas = 0
            for s in range(12):
                out, info = marcas.aplicar([img, img.copy()], regiao, {"semente": s, "tipos": [tipo], "alvos": []})
                self.assertTrue(np.array_equal(out[0], out[1]), tipo)  # igual nas duas: não vira atalho
                if not info:
                    continue
                feitas += 1
                x0, y0, x1, y1 = info[0]["caixa"]
                self.assertFalse(regiao[max(0, y0):y1, max(0, x0):x1].any(), (tipo, info))
                self.assertTrue((out[0] != img).any(), tipo)
            self.assertGreater(feitas, 0, tipo)


class TestCalibrarCanal(unittest.TestCase):
    def test_faixas_medidas_viram_as_do_canal(self):
        import json
        import random
        import tempfile
        from pathlib import Path

        import numpy as np
        from PIL import Image

        from ferramentas import calibrar_canal
        from rotulador import canal
        self.assertEqual(canal.qualidade_da_tabela(canal.tabela_padrao(77)["luma"]), 77)
        with tempfile.TemporaryDirectory() as t:
            pasta = Path(t) / "amostras" / "whatsapp"
            pasta.mkdir(parents=True)
            rng = np.random.default_rng(1)
            for k, q in enumerate((60, 62, 64, 66)):
                img = Image.fromarray(rng.integers(150, 255, (900, 1280, 3), dtype=np.uint8))
                img.save(pasta / f"a{k}.jpg", quality=q)
            r = calibrar_canal.calibrar(pasta.parent, log=lambda *_: None)
            self.assertEqual(r["faixas"]["whatsapp"]["lado"], [1280, 1280])
            lo, hi = r["faixas"]["whatsapp"]["jpeg"]
            self.assertTrue(59 <= lo <= hi <= 67, r["faixas"])
            arq = Path(t) / "canal_calibrado.json"
            arq.write_text(json.dumps(r), encoding="utf-8")
            antes = canal.sortear(random.Random(3), {"perfil": "whatsapp"})
            with mock.patch.object(canal, "CALIBRACAO", str(arq)):
                theta = canal.sortear(random.Random(3), {"perfil": "whatsapp"})
            canal._calib.clear()
            passos = [p for p in theta["passos"] if p["tipo"] == "whatsapp"]
            self.assertTrue(passos)
            self.assertTrue(all(lo <= p["jpeg"] <= hi and p["lado"] == 1280 for p in passos), passos)
            self.assertTrue(all(p["lado"] == 1600 for p in antes["passos"] if p["tipo"] == "whatsapp"))  # sem: padrão


class TestDetectabilidade(unittest.TestCase):
    def test_vestigio_obvio_separa_e_sem_vestigio_nao(self):
        import numpy as np

        from rotulador.treino import detectabilidade as d
        rng = np.random.default_rng(0)

        def pares(vestigio: float):
            X, y, docs = [], [], []
            for doc in range(6):
                for _ in range(30):
                    papel = 230 + rng.normal(0, 4, (d.LADO, d.LADO, 1)).repeat(3, 2)
                    orig = np.clip(papel, 0, 255).astype(np.uint8)
                    ed = np.clip(230 + (papel - 230) * (1 - vestigio), 0, 255).astype(np.uint8)  # liso = vestígio
                    for rec, rot in ((ed, 1), (orig, 0)):
                        X.append(d.medidas(rec))
                        y.append(rot)
                        docs.append(f"d{doc}")
            return np.array(X), np.array(y), docs
        X, y, docs = pares(0.7)
        self.assertGreater(d.auc(d._validar(X, y, docs, 3, 0), y), 0.9)
        X, y, docs = pares(0.0)  # editada == original: nada a separar
        self.assertLess(abs(d.auc(d._validar(X, y, docs, 3, 0), y) - 0.5), 0.15)
        self.assertAlmostEqual(d.auc(np.array([0.9, 0.8, 0.1]), np.array([1, 1, 0])), 1.0)


class TestVersaoIncoerente(unittest.TestCase):
    """Perfil profissional: a versão que cria incoerência é refeita; a que insiste, descartada (e contada)."""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        from PIL import Image

        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import SPLITS, Registro
        from tests.test_auto import conta
        self._rm = shutil.rmtree
        self.tmp = Path(tempfile.mkdtemp())
        self.saida = self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            (self.tmp / "dados" / sp).mkdir(parents=True)
            Image.fromarray(conta(10 + i)).save(self.tmp / "dados" / sp / f"c{i}.png")
        reg = Registro(self.tmp / "dados", self.saida, log=lambda *_: None)
        self.c = criar_app(reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        self._rm(self.tmp, ignore_errors=True)

    def _rodar(self, coerencias):
        import time

        from rotulador import gerador
        real = gerador._coerencia
        fila = list(coerencias)

        def falsa(ctx, feitos):
            return fila.pop(0) if fila else real(ctx, feitos)
        rec = {"perfil": "profissional", "documentos": {"splits": ["teste"]}, "variacoes": 1, "extras": False}
        with mock.patch.object(gerador, "_coerencia", side_effect=falsa):
            self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
            for _ in range(480):
                st = self.c.get("/api/gerador/status").get_json()
                if not st["rodando"]:
                    break
                time.sleep(0.5)
        return st

    def test_refeita_uma_vez(self):
        import json
        incoerente = {"novas_violacoes": ["datas_ordenadas"]}
        st = self._rodar([incoerente])
        self.assertEqual((st["feitas"], st["refeitas_incoerentes"], st["descartadas_incoerentes"]), (1, 1, 0), st)
        a = json.loads(next((self.saida / "teste" / "anotacoes").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(a["gerador"]["tentativas"], 2)
        self.assertFalse((a["coerencia"] or {}).get("novas_violacoes"))

    def test_descartada_quando_insiste(self):
        from rotulador import gerador
        incoerente = {"novas_violacoes": ["endereco_cep"]}
        st = self._rodar([incoerente] * (gerador.TENTATIVAS_COERENCIA + 1))
        self.assertEqual((st["feitas"], st["descartadas_incoerentes"]), (0, 1), st)
        self.assertIn("incoerência", st["erros"][-1])


if __name__ == "__main__":
    unittest.main()
