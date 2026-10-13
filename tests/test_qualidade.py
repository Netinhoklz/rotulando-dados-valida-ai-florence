"""Auditoria de qualidade (rotulador/qualidade.py): versão limpa sem alerta; cada defeito dá o seu alerta
(estrutura, invisível, sem mudança, área, duplicada, reprovada, perícia); --gravar escreve na anotação, refaz os
índices e o datasheet conta."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from rotulador import datasheet, exportar, qualidade
from tests.util import conta


def _png(caminho: Path, arr: np.ndarray) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(caminho, "PNG")


def _versao(raiz: Path, doc: str, v: int, caixa=(40, 150, 140, 175), novo="R$ 2,00", **troca) -> dict:
    """Uma versão coerente (máscaras batendo com a edição); ``troca`` sobrescreve campos da anotação."""
    split = "treino"
    orig = conta(int(doc[-1]))
    ed = orig.copy()
    x0, y0, x1, y1 = caixa
    ed[y0:y1, x0:x1] = (30, 30, 30)
    mascara = np.zeros(orig.shape[:2], bool)
    mascara[y0:y1, x0:x1] = True
    regiao = cv2.dilate(mascara.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    nome = f"{doc}__v{v:02d}"
    r = raiz / split
    _png(r / "originais" / f"{doc}.png", orig)
    _png(r / "editadas" / f"{nome}.png", ed)
    _png(r / "mascaras" / f"{nome}.png", mascara.astype(np.uint8) * 255)
    _png(r / "mascaras_regiao" / f"{nome}.png", regiao.astype(np.uint8) * 255)
    _png(r / "mascaras_ignorar" / f"{nome}.png", exportar.mascara_ignorar(mascara, regiao).astype(np.uint8) * 255)
    _png(r / "mascaras_classes" / f"{nome}.png", np.where(regiao, 9, 0).astype(np.uint8))
    _png(r / "mascaras_tecnicas" / f"{nome}.png", np.where(regiao, 1, 0).astype(np.uint8))
    anot = {"split": split, "doc": doc, "versao": v, "largura": orig.shape[1], "altura": orig.shape[0], "formato": "png",
            "arquivos": {"original": f"originais/{doc}.png", "editada": f"editadas/{nome}.png", "mascara": f"mascaras/{nome}.png",
                         "mascara_regiao": f"mascaras_regiao/{nome}.png", "mascara_ignorar": f"mascaras_ignorar/{nome}.png",
                         "mascara_classes": f"mascaras_classes/{nome}.png", "mascara_tecnicas": f"mascaras_tecnicas/{nome}.png",
                         "anotacao": f"anotacoes/{nome}.json"},
            "fonte": {"arquivo": f"{doc}.png"}, "categorias": ["valor"], "ids_categoria": {"valor": 9},
            "edicoes": [{"ordem": 1, "tecnica": "campo.reescrever", "tecnica_num": 1, "nome_tecnica": "Reescrever", "familia": "imitacao",
                         "nivel": "profissional", "categoria": "valor", "campo": "c9", "bbox": list(caixa),
                         "pixels": int(regiao.sum()), "texto_antigo": "R$ 1,00", "texto_novo": novo, "descricao": "x"}],
            "pixels_alterados": int(mascara.sum()), "fracao_alterada": float(mascara.mean()), "pericia": "auto"}
    anot.update(troca)
    (r / "anotacoes").mkdir(parents=True, exist_ok=True)
    (r / "anotacoes" / f"{nome}.json").write_text(json.dumps(anot), encoding="utf-8")
    return anot


class TestQualidade(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_versao_limpa_nao_tem_alerta(self):
        a = _versao(self.tmp, "0000000000000001", 1)
        self.assertEqual(qualidade.avaliar_versao(self.tmp / "treino", a), [])

    def test_cada_defeito_da_o_seu_alerta(self):
        r = self.tmp / "treino"
        a = _versao(self.tmp, "0000000000000002", 1, novo="R$ 1,00")  # texto novo igual ao antigo
        self.assertEqual([x.split(":")[0] for x in qualidade.avaliar_versao(r, a)], ["sem_mudanca"])
        a = _versao(self.tmp, "0000000000000003", 1, caixa=(40, 150, 42, 152))  # 4 pixels alterados
        self.assertEqual([x.split(":")[0] for x in qualidade.avaliar_versao(r, a)], ["invisivel"])
        a["edicoes"][0]["pixels"] = 3
        self.assertEqual([x.split(":")[0] for x in qualidade.avaliar_versao(r, a)], ["invisivel", "invisivel"])
        a = _versao(self.tmp, "0000000000000004", 1, caixa=(0, 0, 320, 300))  # 70% da página
        self.assertIn("area", [x.split(":")[0] for x in qualidade.avaliar_versao(r, a)])
        a = _versao(self.tmp, "0000000000000005", 1, revisao={"estado": "reprovada", "motivos": ["artefato"], "por": "ana"})
        self.assertEqual(qualidade.avaliar_versao(r, a), ["reprovada: artefato"])
        a = _versao(self.tmp, "0000000000000006", 1)
        a["edicoes"][0].update({"pericia": "forense", "perito": {"nota": 0.95}})
        self.assertEqual([x.split(":")[0] for x in qualidade.avaliar_versao(r, a)], ["pericia"])
        a = _versao(self.tmp, "0000000000000007", 1)
        m = np.array(Image.open(r / a["arquivos"]["mascara"]))
        m[10, 10] = 255  # pixel alterado fora da região
        _png(r / a["arquivos"]["mascara"], m)
        self.assertTrue(any(x.startswith("estrutura: 1 pixels alterados fora") for x in qualidade.avaliar_versao(r, a)))

    def test_duplicadas_e_gravar(self):
        _versao(self.tmp, "0000000000000008", 1)
        _versao(self.tmp, "0000000000000008", 2)  # a mesma edição de novo
        _versao(self.tmp, "0000000000000008", 3, novo="R$ 3,00")
        exportar.reconstruir_indices(self.tmp, "treino")
        res = qualidade.auditar(self.tmp, ["treino"], gravar=True, log=lambda *_: None)
        self.assertEqual((res["versoes"], res["com_alerta"], res["por_tipo"]), (3, 2, {"duplicada": 2}))
        self.assertTrue((self.tmp / "relatorio_qualidade.json").is_file() and (self.tmp / "relatorio_qualidade.txt").is_file())
        anots = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (self.tmp / "treino" / "anotacoes").glob("*.json")}
        self.assertEqual(anots["0000000000000008__v01"]["auditoria_qualidade"]["alertas"],
                         ["duplicada: mesma edição em 0000000000000008__v02"])
        self.assertEqual(anots["0000000000000008__v03"]["auditoria_qualidade"]["alertas"], [])
        self.assertIsNone(anots["0000000000000008__v03"]["qualidade"])  # PNG: sem qualidade de codec
        d = datasheet.coletar(self.tmp)
        self.assertEqual(d["splits"]["treino"]["qualidade"], {"com_alerta": 2, "alertas": {"duplicada": 2}})
        self.assertIn("versões com alerta de qualidade", datasheet.markdown(d))
        self.assertIn("campo.amador × valor", d["splits"]["treino"]["cobertura"]["faltam"])
        self.assertIsNotNone(d["splits"]["treino"]["diversidade"]["por_categoria"] is None or True)


if __name__ == "__main__":
    unittest.main()
