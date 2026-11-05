"""A pasta do treino do modelo (saida/_treinamento/<split>/): a cada versão salva, a editada e a máscara de onde foi
editado, com o mesmo nome; excluir a versão tira as duas; sincronizar põe as que faltam e tira as órfãs."""
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app import RAIZ, carregar_categorias, criar_app
from rotulador import exportar
from rotulador.ops import executar
from rotulador.registro import SPLITS, Registro
from rotulador.selecao import vazia
from rotulador.sessao import Sessao
from tests.util import conta, salvar

PINCEL = {"pontos": [[100, 300], [200, 300]], "tamanho": 12, "cor": "#ff0000"}


class TestPastaTreinamento(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            salvar(conta(50 + i), self.dados / sp / f"a_{sp}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.cats = carregar_categorias(RAIZ / "config" / "categorias.json")
        self.it = self.reg.por_split["treino"][0]
        self.treino = self.saida / "_treinamento" / "treino"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sessao(self):
        s = Sessao(self.it, self.reg.carregar(self.it.doc), self.tmp / "rasc" / str(random.random()),
                   {c["chave"]: c["id"] for c in self.cats})
        res = executar("pincel", s.atual, PINCEL, vazia(s.H, s.W))
        s.aplicar("pincel", "valor", "", PINCEL, res)
        return s

    def test_salvar_grava_a_editada_e_a_mascara_com_o_mesmo_nome(self):
        r = exportar.salvar(self.sessao(), self.saida)
        nome = f"{self.it.doc}__v01"
        img, masc = self.treino / "imagens" / f"{nome}.png", self.treino / "mascaras" / f"{nome}.png"
        self.assertEqual(r["treinamento"], {"imagem": f"_treinamento/treino/imagens/{nome}.png",
                                            "mascara": f"_treinamento/treino/mascaras/{nome}.png"})
        self.assertEqual(img.read_bytes(), (self.saida / "treino" / r["arquivos"]["editada"]).read_bytes())
        self.assertEqual(masc.read_bytes(), (self.saida / "treino" / r["arquivos"]["mascara"]).read_bytes())
        m = cv2.imread(str(masc), cv2.IMREAD_UNCHANGED)
        self.assertEqual(set(np.unique(m).tolist()), {0, 1})
        self.assertEqual(m[300, 150], 1)  # sob o traço do pincel
        self.assertEqual(m[20, 20], 0)
        self.assertEqual(sorted(p.name for p in self.treino.rglob("*") if p.is_file()), [nome + ".png"] * 2)

    def test_com_perda_o_nome_nao_leva_a_qualidade(self):
        exportar.salvar(self.sessao(), self.saida, "jpeg", 90)
        nome = f"{self.it.doc}__v01"
        self.assertTrue((self.treino / "imagens" / f"{nome}.jpg").is_file())
        self.assertTrue((self.treino / "mascaras" / f"{nome}.png").is_file())

    def test_excluir_a_versao_tira_as_duas(self):
        exportar.salvar(self.sessao(), self.saida)
        exportar.salvar(self.sessao(), self.saida)
        exportar.excluir_versao(self.saida, "treino", f"{self.it.doc}__v01")
        for sub in ("imagens", "mascaras"):
            self.assertEqual([p.name for p in (self.treino / sub).iterdir()], [f"{self.it.doc}__v02.png"])

    def test_sincronizar_poe_as_que_faltam_e_tira_as_orfas(self):
        exportar.salvar(self.sessao(), self.saida)
        exportar.salvar(self.sessao(), self.saida)
        shutil.rmtree(self.saida / "_treinamento")  # versões salvas antes de a pasta existir
        orfa = self.treino / "imagens" / "ffffffff__v01.png"
        orfa.parent.mkdir(parents=True)
        orfa.write_bytes(b"x")
        self.assertEqual(exportar.sincronizar_treinamento(self.saida), 5)
        for sub in ("imagens", "mascaras"):
            self.assertEqual(sorted(p.name for p in (self.treino / sub).iterdir()),
                             [f"{self.it.doc}__v01.png", f"{self.it.doc}__v02.png"])
        self.assertEqual(exportar.sincronizar_treinamento(self.saida), 0)

    def test_salvar_pelo_app_tambem(self):
        c = criar_app(self.reg, self.saida, self.cats).test_client()
        sid = self.it.doc
        r = c.post("/api/op", json={"id": sid, "tipo": "pincel", "params": PINCEL, "categoria": "valor"})
        self.assertEqual(r.status_code, 200, r.get_json())
        r = c.post("/api/salvar", json={"id": sid})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue((self.saida / r.get_json()["salvo"]["treinamento"]["mascara"]).is_file())


if __name__ == "__main__":
    unittest.main()
