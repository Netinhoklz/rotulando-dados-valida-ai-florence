import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rotulador import selecao
from rotulador.ops import executar
from rotulador.registro import Item
from rotulador.sessao import ErroOperacao, Sessao
from tests.util import conta

IDS = {"nome": 1, "valor": 9, "data": 7}


class TestCamadas(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.img = conta(3)
        self.item = Item(doc="abc", split="treino", caminho=self.tmp / "x.png", rel="x.png", pagina=0, paginas=1,
                         sha_arquivo="f", sha_pixels="p", altura=self.img.shape[0], largura=self.img.shape[1])
        self.s = self.nova()

    def nova(self):
        return Sessao(self.item, self.img.copy(), self.tmp / "rasc", IDS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pintar(self, pts, cor, categoria, tamanho=20, camada=None):
        p = {"pontos": pts, "tamanho": tamanho, "dureza": 1, "cor": cor}
        res = executar("pincel", self.s.atual, p, selecao.vazia(*self.img.shape[:2]), base=self.s.base)
        return self.s.aplicar("pincel", categoria, "", p, res, camada=camada)

    def test_cada_categoria_ganha_sua_camada_e_a_de_cima_vence(self):
        self.pintar([[100, 100]], "#ff0000", "valor")
        self.pintar([[105, 100]], "#0000ff", "nome")  # sobrepõe parte
        self.assertEqual([c["categoria"] for c in self.s.lista_camadas()], ["nome", "valor"])  # cima -> baixo
        _, _, classes = self.s.mascaras()
        self.assertEqual(classes[100, 112], 1)  # só a de cima (nome)
        self.assertEqual(classes[100, 92], 9)  # só a de baixo (valor)
        # inverter a ordem muda quem vence na sobreposição
        cid_valor = next(c["id"] for c in self.s.lista_camadas() if c["categoria"] == "valor")
        self.s.acao_camada("ordem", cid_valor, posicao=1)
        _, _, classes = self.s.mascaras()
        self.assertEqual(classes[100, 104], 9)
        self.assertEqual(tuple(self.s.atual[100, 104]), (255, 0, 0))

    def test_ocultar_camada_tira_da_imagem_e_dos_rotulos(self):
        self.pintar([[100, 100]], "#ff0000", "valor")
        cid = self.s.ativa
        self.s.acao_camada("props", cid, visivel=False)
        np.testing.assert_array_equal(self.s.atual, self.s.base)
        self.assertEqual(self.s.categorias(), [])
        self.assertFalse(self.s.pode_salvar()[0])
        with self.assertRaises(ErroOperacao):  # não edita camada oculta
            self.pintar([[50, 50]], "#00ff00", "valor", camada=cid)
        self.s.desfazer()  # desfaz o ocultar
        self.assertTrue(self.s.pode_salvar()[0])

    def test_opacidade_mistura(self):
        self.pintar([[100, 100]], "#000000", "valor")
        antes = self.s.base[100, 100].astype(int)
        self.s.acao_camada("props", self.s.ativa, opacidade=0.5)
        esperado = np.round(antes * 0.5).astype(int)
        np.testing.assert_allclose(self.s.atual[100, 100].astype(int), esperado, atol=1)

    def test_excluir_mesclar_duplicar_com_desfazer(self):
        self.pintar([[100, 100]], "#ff0000", "valor")
        self.pintar([[160, 100]], "#0000ff", "nome")
        editada = self.s.atual.copy()
        cima = self.s.ativa
        self.s.acao_camada("excluir", cima)
        self.assertEqual(len(self.s.ordem), 1)
        self.assertFalse((self.s.atual[100, 160] == [0, 0, 255]).all())
        self.s.desfazer()
        np.testing.assert_array_equal(self.s.atual, editada)
        self.s.acao_camada("mesclar_abaixo", cima)
        self.assertEqual(len(self.s.ordem), 1)
        np.testing.assert_array_equal(self.s.atual, editada)  # mesclar não muda a imagem
        self.s.desfazer()
        self.assertEqual(len(self.s.ordem), 2)
        np.testing.assert_array_equal(self.s.atual, editada)
        self.s.acao_camada("duplicar", cima)
        self.assertEqual(len(self.s.ordem), 3)
        self.s.desfazer()
        self.assertEqual(len(self.s.ordem), 2)

    def test_borracha_revela_camada_de_baixo(self):
        self.pintar([[100, 100]], "#ff0000", "valor", tamanho=40)
        self.pintar([[100, 100]], "#0000ff", "nome", tamanho=20)
        p = {"pontos": [[100, 100]], "tamanho": 30, "dureza": 1}
        res = executar("borracha", self.s.atual, p, selecao.vazia(*self.img.shape[:2]), base=self.s.base)
        self.s.aplicar("borracha", None, "", p, res)
        self.assertEqual(tuple(self.s.atual[100, 100]), (255, 0, 0))

    def test_mover_camada(self):
        self.pintar([[100, 100]], "#ff0000", "valor", tamanho=10)
        self.s.deslocar_camada(None, 50, 20)
        self.assertEqual(tuple(self.s.atual[120, 150]), (255, 0, 0))
        np.testing.assert_array_equal(self.s.atual[100, 100], self.s.base[100, 100])
        self.s.desfazer()
        self.assertEqual(tuple(self.s.atual[100, 100]), (255, 0, 0))

    def test_um_ctrl_z_desfaz_camada_criada_e_pintura(self):
        self.pintar([[100, 100]], "#ff0000", "valor")
        self.assertEqual(len(self.s.historico_), 1)
        self.s.desfazer()
        self.assertEqual(self.s.ordem, [])
        np.testing.assert_array_equal(self.s.atual, self.s.base)

    def test_rascunho_guarda_camadas_e_historico(self):
        self.pintar([[100, 100]], "#ff0000", "valor")
        self.pintar([[160, 100]], "#0000ff", "nome")
        self.s.acao_camada("props", self.s.ativa, nome="meu nome", opacidade=0.7)
        self.s.acao_camada("props", self.s.ordem[0], visivel=False)
        editada = self.s.atual.copy()
        nova = self.nova()
        np.testing.assert_array_equal(nova.atual, editada)
        self.assertEqual(nova.lista_camadas(), self.s.lista_camadas())
        self.assertEqual(len(nova.historico_), 4)
        while nova.desfazer():
            pass
        np.testing.assert_array_equal(nova.atual, nova.base)
        self.assertEqual(nova.ordem, [])


if __name__ == "__main__":
    unittest.main()
