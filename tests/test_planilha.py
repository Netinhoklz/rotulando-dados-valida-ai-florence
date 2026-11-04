"""A planilha do dataset (saida/dataset.xlsx): as abas, os caminhos com link e o nome alternativo quando o Excel está
com o arquivo aberto. A versão é salva como no editor (uma pincelada de papel sobre o campo, ``exportar.salvar``).

Os documentos são os sintéticos de test_tecnicas, com o OCR gravado no cache (os campos não dependem do motor de OCR)."""
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from openpyxl import load_workbook
from PIL import Image

from app import RAIZ, carregar_categorias, criar_app
from rotulador import catalogo, exportar, ocr, planilha
from rotulador.contexto import Contexto, cache_ocr
from rotulador.ops import executar
from rotulador.registro import SPLITS, Registro
from rotulador.selecao import vazia
from rotulador.sessao import Sessao
from tests.test_tecnicas import documento

NOMES = {"treino": "MARIA DOS SANTOS SILVA", "teste": "LUCAS PINTO GOMES", "validacao": "PEDRO ALVES COSTA"}


class BaseApp(unittest.TestCase):
    """Um documento sintético por split, o OCR no cache e o app."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        linhas = {}
        for i, sp in enumerate(SPLITS):
            img, linhas[sp] = documento(nome=NOMES[sp], semente=i + 1)
            (self.dados / sp).mkdir(parents=True)
            Image.fromarray(img).save(self.dados / sp / f"conta_{sp}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        for sp in SPLITS:
            ocr.gravar_cache(cache_ocr(self.saida, self.reg.por_split[sp][0].sha_pixels), linhas[sp])
        self.cats = carregar_categorias(RAIZ / "config" / "categorias.json")
        self.app = criar_app(self.reg, self.saida, self.cats)
        self.c = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def doc(self, sp="teste"):
        return self.reg.por_split[sp][0]

    def campo(self, it, categoria):
        ctx = Contexto(it, self.reg.carregar(it.doc), self.saida, self.reg)
        return next(f for f in ctx.campos if f.get("categoria") == categoria)

    def salvar_manual(self, it, categoria="nome", onde="nome") -> dict:
        """Uma versão salva como no editor: uma pincelada da cor do papel sobre o começo do campo ``onde``."""
        x0, y0, x1, y1 = self.campo(it, onde)["caixa"]
        s = Sessao(it, self.reg.carregar(it.doc), self.tmp / "rasc" / str(random.random()),
                   {**{c["chave"]: c["id"] for c in self.cats}, **{c["chave"]: c["id"] for c in catalogo.CATEGORIAS_EDITOR}})
        pincel = {"pontos": [[x0 + 8, (y0 + y1) // 2], [x0 + 70, (y0 + y1) // 2]], "tamanho": 12, "cor": "#fbfbf9"}
        s.aplicar("pincel", categoria, "", pincel, executar("pincel", s.atual, pincel, vazia(s.H, s.W)))
        return exportar.salvar(s, self.saida)

    def anotacao(self, r):
        return json.loads((self.saida / r["split"] / r["arquivos"]["anotacao"]).read_text(encoding="utf-8"))

    def mascara(self, r):
        return np.asarray(Image.open(self.saida / r["split"] / r["arquivos"]["mascara"]))


class TestPlanilha(BaseApp):
    def test_abas_e_caminhos(self):
        r = self.salvar_manual(self.doc("teste"))
        info = planilha.escrever(self.saida, self.reg)
        self.assertIsNone(info["aviso"])
        wb = load_workbook(info["arquivo"])
        self.assertEqual(wb.sheetnames, ["Resumo", "Versões", "Edições", "Documentos", "Legenda"])
        ws = wb["Versões"]
        cab = [c.value for c in ws[1]]
        linha = [c.value for c in ws[2]]
        self.assertEqual(ws.max_row, 2)
        self.assertEqual(linha[cab.index("Split")], "Teste")
        col = cab.index("Máscara (1 = editado)")
        self.assertTrue((self.saida / linha[col]).is_file())
        self.assertEqual(ws.cell(row=2, column=col + 1).hyperlink.target, linha[col])
        self.assertTrue(linha[cab.index("Editada")].startswith("teste/editadas/"))
        ed_ws = wb["Edições"]
        cab_e = [c.value for c in ed_ws[1]]
        self.assertEqual([c.value for c in ed_ws[2]][cab_e.index("Campo editado")], "nome")
        self.assertEqual(wb["Documentos"].max_row, 4)  # um documento por split
        resumo = [[c.value for c in row] for row in wb["Resumo"].iter_rows()]
        teste = next(r_ for r_ in resumo if r_[0] == "Teste")
        self.assertEqual(teste[2:6], [1, 1, 1, 1])
        legenda = [[c.value for c in row] for row in wb["Legenda"].iter_rows(min_row=2)]
        self.assertIn(["mascaras/", 1, "pixel editado (o valor mudou)"], legenda)
        wb.close()
        np.testing.assert_array_equal(np.unique(self.mascara(r)), [0, 1])  # máscara binária em 0/1

    def test_planilha_aberta_no_excel_vai_para_outro_nome(self):
        self.saida.mkdir(parents=True, exist_ok=True)
        with open(self.saida / planilha.NOME, "wb") as f:
            f.write(b"travado")
            import msvcrt  # Windows: trava o arquivo como o Excel faz
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            info = planilha.escrever(self.saida, self.reg)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        self.assertTrue(info["aviso"])


if __name__ == "__main__":
    unittest.main()
