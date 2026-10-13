"""O lado do treino (rotulador/treino): carregador com recortes centrados na edição, borda suave, currículo por
dificuldade, pesos pela taxa-base, aumento de canal; protocolo de avaliação (AUC, F1 fixo/oráculo/calibrado,
por imagem, bootstrap); conjunto de robustez; linha de base sem treino."""
import importlib.util
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from rotulador import canal, exportar
from rotulador.treino import avaliar, base, dados, robustez
from tests.util import conta

NOTAS = {(0, 1): 0.9, (0, 2): 0.2, (1, 1): 0.6, (1, 2): None}  # (doc, versão) → nota do perito


def _png(caminho: Path, arr: np.ndarray) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(caminho, "PNG")


def _split(raiz: Path, split: str = "treino") -> list[str]:
    """Dois documentos × duas versões, gravados como o exportador grava (PNG, máscaras, anotação)."""
    nomes = []
    for d in range(2):
        doc = f"{d:016x}"
        orig = conta(d)
        for v in (1, 2):
            ed = orig.copy()
            ed[150:175, 40:140] = (30, 30, 30) if v == 1 else (120, 60, 60)
            mascara = np.zeros(orig.shape[:2], bool)
            mascara[150:175, 40:140] = True
            regiao = cv2.dilate(mascara.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            classes = np.where(regiao, 3, 0).astype(np.uint8)
            tecnicas = np.where(regiao, 5, 0).astype(np.uint8)
            nome = f"{doc}__v{v:02d}"
            r = raiz / split
            _png(r / "originais" / f"{doc}.png", orig)
            _png(r / "editadas" / f"{nome}.png", ed)
            _png(r / "mascaras" / f"{nome}.png", mascara.astype(np.uint8) * 255)
            _png(r / "mascaras_regiao" / f"{nome}.png", regiao.astype(np.uint8) * 255)
            _png(r / "mascaras_ignorar" / f"{nome}.png", exportar.mascara_ignorar(mascara, regiao).astype(np.uint8) * 255)
            _png(r / "mascaras_classes" / f"{nome}.png", classes)
            _png(r / "mascaras_tecnicas" / f"{nome}.png", tecnicas)
            nota = NOTAS[(d, v)]
            anot = {"split": split, "doc": doc, "versao": v, "largura": orig.shape[1], "altura": orig.shape[0],
                    "arquivos": {"original": f"originais/{doc}.png", "editada": f"editadas/{nome}.png",
                                 "mascara": f"mascaras/{nome}.png", "mascara_regiao": f"mascaras_regiao/{nome}.png",
                                 "mascara_ignorar": f"mascaras_ignorar/{nome}.png", "mascara_classes": f"mascaras_classes/{nome}.png",
                                 "mascara_tecnicas": f"mascaras_tecnicas/{nome}.png", "anotacao": f"anotacoes/{nome}.json"},
                    "fonte": {"arquivo": f"doc{d}.png"}, "formato": "png", "categorias": ["valor"], "ids_categoria": {"valor": 3},
                    "edicoes": [{"ordem": 1, "tecnica": "campo.reescrever", "tecnica_num": 5, "nome_tecnica": "Reescrever",
                                 "familia": "imitacao", "nivel": "simples", "categoria": "valor", "bbox": [40, 150, 140, 175],
                                 "pixels": 2500, "texto_antigo": "a", "texto_novo": "b", "descricao": "x",
                                 **({"perito": {"nota": nota}} if nota is not None else {})}],
                    "pixels_alterados": int(mascara.sum()), "fracao_alterada": float(mascara.mean()), "pericia": "cru",
                    "canal": {"perfil": "celular", "passos": [{"tipo": "celular"}]} if d == 0 else None,
                    "captura_antes": "foto" if d == 0 else None, "falsificador": {"nome": "celular"} if d == 0 else None,
                    "coerencia": {"sem_pista_semantica": v == 2}}
            (r / "anotacoes").mkdir(parents=True, exist_ok=True)
            (r / "anotacoes" / f"{nome}.json").write_text(json.dumps(anot), encoding="utf-8")
            nomes.append(nome)
    return nomes


class TestTreino(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.nomes = _split(cls.tmp)
        cls.amostras = dados.listar(cls.tmp, "treino")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _escrever_predicoes(self, pasta: Path, fator: float, pular: str | None = None) -> None:
        for a in self.amostras:
            if a["nome"] == pular:
                continue
            arr = dados.carregar(a)
            _png(pasta / f"{a['nome']}.png", np.clip(arr["mascara"] * fator * 255 + 0.5, 0, 255).astype(np.uint8))
            _png(pasta / "originais" / f"{Path(a['arquivos']['original']).stem}.png", np.zeros(arr["mascara"].shape, np.uint8))

    def test_listar_e_carregar(self):
        self.assertEqual([a["nome"] for a in self.amostras], sorted(self.nomes))
        por_nome = {a["nome"]: a for a in self.amostras}
        self.assertEqual(por_nome[self.nomes[0]]["dificuldade"], 0.1)  # 1 − 0,9
        self.assertEqual(por_nome[self.nomes[1]]["dificuldade"], 0.8)
        self.assertIsNone(por_nome[self.nomes[3]]["dificuldade"])
        a = por_nome[self.nomes[0]]
        self.assertEqual((a["familias"], a["tecnicas"], a["canal"], a["falsificador"]), (["imitacao"], ["campo.reescrever"], "celular", "celular"))
        self.assertEqual(exportar.dificuldade_de({"dificuldade": 0.33, "edicoes": []}), 0.33)
        arr = dados.carregar(a)
        self.assertEqual(arr["original"].shape, arr["editada"].shape)
        for k in ("mascara", "regiao", "ignorar"):
            self.assertEqual(arr[k].dtype, bool)
        self.assertTrue(arr["mascara"].any() and arr["regiao"][arr["mascara"]].all())
        self.assertFalse(arr["ignorar"][arr["mascara"]].any())  # a faixa de ignorar fica por FORA do que mudou
        self.assertEqual((int(arr["classes"].max()), int(arr["tecnicas"].max())), (3, 5))

    def test_borda_e_recorte(self):
        arr = dados.carregar(self.amostras[0])
        b = dados.borda(arr["regiao"], 3)
        self.assertEqual(float(b.max()), 1.0)
        self.assertEqual(float(b[162, 90]), 0.0)  # centro da região: longe da fronteira
        self.assertEqual(float(b[20, 20]), 0.0)
        self.assertGreater(float(b[148, 90]), 0.0)  # a 1 px da fronteira
        rng = random.Random(1)
        for _ in range(20):
            r = dados.recortar(arr, 64, rng, p_edicao=1.0)
            self.assertEqual(r["editada"].shape, (64, 64, 3))
            self.assertTrue(r["mascara"].any())
            x0, y0 = r["janela"]
            self.assertTrue(np.array_equal(r["original"], arr["original"][y0:y0 + 64, x0:x0 + 64]))
        for _ in range(10):
            r = dados.recortar(arr, 64, rng, p_edicao=0.0, p_texto=1.0)
            self.assertTrue(dados.tinta(r["editada"]).any())
        r = dados.recortar(arr, 600, rng)  # maior que a imagem: completa com papel
        self.assertEqual(r["editada"].shape, (600, 600, 3))
        self.assertTrue((r["editada"][500:, 500:] == 255).all() and not r["mascara"][500:, 500:].any())

    def test_curriculo_e_pesos(self):
        facil = dados.curriculo(self.amostras, 0.5)
        self.assertEqual([a["nome"] for a in facil], [self.nomes[0], self.nomes[2]])  # dificuldades 0,1 e 0,4
        self.assertEqual([a["dificuldade"] for a in dados.curriculo(self.amostras, 1.0)], [0.1, 0.4, None, 0.8])
        p = dados.pesos(self.amostras)
        self.assertAlmostEqual(p["taxa_base_editadas"], 2500 / (420 * 320), places=5)
        self.assertAlmostEqual(p["taxa_base"], 2500 / (420 * 320) / 2, places=5)  # o par serve a original também
        self.assertAlmostEqual(p["peso_positivo"], (1 - p["taxa_base"]) / p["taxa_base"], places=1)
        self.assertEqual(p["por_familia"], {"imitacao": 1.0})

    def test_aumentar_mantem_o_par(self):
        arr = dados.carregar(self.amostras[0])
        for perfil in ("whatsapp", "celular"):
            novo, theta = dados.aumentar(arr, random.Random(3), {"perfil": perfil, "prob": 1.0, "captura": "depois"})
            self.assertIsNotNone(theta, perfil)
            self.assertEqual(novo["original"].shape, novo["editada"].shape)
            self.assertEqual(novo["mascara"].shape, novo["editada"].shape[:2])
            self.assertTrue(novo["mascara"].any() and novo["regiao"].any(), perfil)
            self.assertIsNone(canal.conferir_par(novo["original"], novo["editada"], novo["regiao"], theta), perfil)
            self.assertFalse(novo["ignorar"][novo["mascara"]].any())
        mesmo, nada = dados.aumentar(arr, random.Random(3), {"perfil": "whatsapp", "prob": 0.0})
        self.assertIsNone(nada)
        self.assertIs(mesmo, arr)

    def test_avaliar_perfeito(self):
        pred = self.tmp / "pred_perfeita"
        self._escrever_predicoes(pred, 1.0)
        d = avaliar.avaliar(self.tmp, "treino", pred, calibrar_n=0)
        self.assertEqual((d["versoes"], d["faltam"]), (4, []))
        for k in ("auc", "f1_05", "iou_05", "f1_oraculo", "f1_calibrado"):
            self.assertEqual(d["pixel"][k]["media"], 1.0, k)
        self.assertEqual(d["pixel"]["auc"]["ic95"], [1.0, 1.0])
        self.assertEqual((d["imagem"]["auc"], d["imagem"]["tpr_fpr01"], d["imagem"]["n_originais"]), (1.0, 1.0, 2))
        self.assertEqual(d["por"]["familias"]["imitacao"]["n"], 4)
        self.assertEqual(set(d["por"]["sem_pista_semantica"]), {"True", "False"})
        self.assertIn("AUC", avaliar.markdown(d))

    def test_avaliar_calibracao_recupera_o_limiar(self):
        pred = self.tmp / "pred_fraca"
        self._escrever_predicoes(pred, 0.3, pular=self.nomes[3])  # confiança 0,3 na edição: o limiar 0,5 zera o F1
        d = avaliar.avaliar(self.tmp, "treino", pred, calibrar_n=2, semente=1)
        self.assertEqual((d["versoes"], d["faltam"]), (3, [self.nomes[3]]))
        self.assertEqual(d["pixel"]["auc"]["media"], 1.0)
        self.assertEqual(d["pixel"]["f1_05"]["media"], 0.0)
        self.assertEqual(d["pixel"]["f1_oraculo"]["media"], 1.0)
        self.assertTrue(0.0 < d["limiar_calibrado"] <= 0.302, d["limiar_calibrado"])  # entre o papel (0) e a edição (0,3)
        self.assertEqual((d["pixel"]["f1_calibrado"]["media"], d["pixel"]["f1_calibrado"]["n"]), (1.0, 1))  # as outras
        self.assertEqual(d["imagem"]["auc"], 1.0)

    def test_robustez_gera_copias_coerentes(self):
        r = robustez.gerar(self.tmp, "treino", self.tmp / "rob", ["jpeg75", "reduzida"], semente=2, limite=2)
        self.assertEqual({k: v["versoes"] for k, v in r.items()}, {"jpeg75": 2, "reduzida": 2})
        for perfil in ("jpeg75", "reduzida"):
            copias = dados.listar(self.tmp / "rob" / perfil, "treino")
            self.assertEqual(len(copias), 2)
            arr = dados.carregar(copias[0])
            self.assertTrue(arr["mascara"].any() and arr["regiao"][arr["mascara"]].all())
            self.assertEqual(arr["editada"].shape, arr["original"].shape)
            anot = json.loads(Path(copias[0]["arquivos"]["anotacao"]).read_text(encoding="utf-8"))
            self.assertEqual(anot["robustez"]["perfil"], perfil)
            self.assertEqual(anot["edicoes"][0]["pixels"], int(arr["regiao"].sum()))
            self.assertTrue((self.tmp / "rob" / perfil / "treino" / "manifesto.csv").is_file())
        self.assertEqual(dados.carregar(dados.listar(self.tmp / "rob" / "reduzida", "treino")[0])["editada"].shape[1], round(320 * 0.7))
        with self.assertRaises(ValueError):
            robustez.gerar(self.tmp, "treino", self.tmp / "rob", ["inexistente"])

    def test_base_gera_predicoes_e_avalia(self):
        pred = self.tmp / "pred_base"
        r = base.gerar(self.tmp, "treino", pred)
        self.assertEqual((r["versoes"], r["originais"]), (4, 2))
        self.assertTrue((pred / f"{self.nomes[0]}.png").is_file() and len(list((pred / "originais").glob("*.png"))) == 2)
        d = avaliar.avaliar(self.tmp, "treino", pred, calibrar_n=1)
        self.assertEqual(d["versoes"], 4)
        self.assertTrue(0.0 <= d["pixel"]["auc"]["media"] <= 1.0)
        self.assertIsNotNone(d["imagem"])

    @unittest.skipUnless(importlib.util.find_spec("torch"), "sem torch")
    def test_conjunto_torch(self):
        import torch

        torch.manual_seed(0)
        cj = dados.conjunto_torch(self.amostras, lado=64)
        item = cj[0]
        self.assertEqual(tuple(item["imagens"].shape), (2, 3, 64, 64))
        self.assertEqual(float(item["mascara"][0].sum()), 0.0)


if __name__ == "__main__":
    unittest.main()
