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

    def borracha(self, pts, tamanho=30):
        p = {"pontos": pts, "tamanho": tamanho, "dureza": 1}
        res = executar("borracha", self.s.atual, p, selecao.vazia(*self.img.shape[:2]), base=self.s.base)
        return self.s.aplicar("borracha", None, "", p, res)

    def test_borracha_revela_camada_de_baixo(self):
        """Com camadas que a pessoa criou, a borracha tira da camada ativa e revela a de baixo."""
        self.s.acao_camada("criar", categoria="valor")
        self.pintar([[100, 100]], "#ff0000", "valor", tamanho=40)
        self.s.acao_camada("criar", categoria="nome")
        self.pintar([[100, 100]], "#0000ff", "nome", tamanho=20)
        self.borracha([[100, 100]])
        self.assertEqual(tuple(self.s.atual[100, 100]), (255, 0, 0))

    # ---------------------------------------------------------------- edição direto no Fundo (como no Photoshop)
    def test_edicao_vai_direto_no_fundo(self):
        """Sem camada da pessoa ativa, a edição vai para a imagem: as camadas internas (uma por tipo, para a
        máscara de classes) são ``auto`` e o painel não as mostra; a camada que a pessoa cria fica acima delas."""
        self.pintar([[100, 100]], "#ff0000", "valor")
        self.pintar([[160, 160]], "#0000ff", "nome")
        self.assertTrue(all(c["auto"] for c in self.s.lista_camadas()))
        self.s.acao_camada("ativar", "fundo")
        self.s.acao_camada("criar", categoria="nome")
        cams = self.s.lista_camadas()  # de cima para baixo
        self.assertEqual([c["auto"] for c in cams], [False, True, True])
        self.assertTrue(cams[0]["ativa"])

    def test_no_fundo_a_ultima_edicao_aparece(self):
        """Vermelho (valor), azul (nome) por cima e vermelho de novo: o último vermelho aparece e o pixel é 'valor'."""
        self.pintar([[100, 100]], "#ff0000", "valor")
        self.pintar([[100, 100]], "#0000ff", "nome")
        self.pintar([[100, 100]], "#ff0000", "valor", tamanho=10)
        self.assertEqual(tuple(self.s.atual[100, 100]), (255, 0, 0))
        self.assertEqual(self.s.mascaras()[2][100, 100], 9)
        self.s.desfazer()  # o azul volta
        self.assertEqual(tuple(self.s.atual[100, 100]), (0, 0, 255))

    def test_borracha_no_fundo_devolve_o_original(self):
        self.pintar([[100, 100]], "#ff0000", "valor", tamanho=40)
        self.pintar([[100, 100]], "#0000ff", "nome", tamanho=20)
        self.s.acao_camada("ativar", "fundo")
        self.borracha([[100, 100]], tamanho=60)
        np.testing.assert_array_equal(self.s.atual[80:120, 80:120], self.s.base[80:120, 80:120])
        self.s.desfazer()
        self.assertEqual(tuple(self.s.atual[100, 100]), (0, 0, 255))

    def test_historico_ir_para_um_passo(self):
        """O clique num estado do Histórico: volta até ele, os passos à frente ficam e somem na próxima edição."""
        self.pintar([[60, 60]], "#ff0000", "valor")
        depois_do_1 = self.s.atual.copy()
        self.pintar([[120, 60]], "#00ff00", "valor")
        self.pintar([[180, 60]], "#0000ff", "nome")
        final = self.s.atual.copy()
        self.s.ir_para(1)
        np.testing.assert_array_equal(self.s.atual, depois_do_1)
        self.assertEqual((len(self.s.historico_), len(self.s.estado()["refazer"])), (1, 2))
        self.s.ir_para(3)
        np.testing.assert_array_equal(self.s.atual, final)
        self.s.ir_para(0)
        np.testing.assert_array_equal(self.s.atual, self.s.base)
        self.s.ir_para(1)
        self.pintar([[60, 160]], "#00ffff", "nome")  # edição nova: os passos à frente somem
        self.assertEqual((len(self.s.historico_), self.s.estado()["refazer"]), (2, []))

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
        sem_versao = lambda cams: [{k: v for k, v in c.items() if k != "versao"} for c in cams]  # contador de cache
        self.assertEqual(sem_versao(nova.lista_camadas()), sem_versao(self.s.lista_camadas()))
        self.assertEqual(len(nova.historico_), 4)
        while nova.desfazer():
            pass
        np.testing.assert_array_equal(nova.atual, nova.base)
        self.assertEqual(nova.ordem, [])


class TestCamadaVia(unittest.TestCase):
    """Ctrl+J / Ctrl+Shift+J do Photoshop: camada via cópia / via recorte."""

    setUp, tearDown, nova, pintar = (TestCamadas.__dict__[k] for k in ("setUp", "tearDown", "nova", "pintar"))
    RET = {"formas": [{"tipo": "ret", "x0": 40, "y0": 60, "x1": 160, "y1": 120}]}

    def sel(self):
        return selecao.rasterizar(self.RET, self.s.atual)

    def test_copia_do_fundo_parada_nao_e_edicao_e_movida_e(self):
        self.s.camada_via(self.sel(), categoria="valor")
        cam = self.s.camadas[self.s.ativa]
        self.assertFalse(cam.auto)
        self.assertEqual(cam.caixa(), (60, 120, 40, 160))
        np.testing.assert_array_equal(cam.rgba[60:120, 40:160, :3], self.s.base[60:120, 40:160])
        np.testing.assert_array_equal(self.s.atual, self.s.base)
        alterados, regiao, classes = self.s.mascaras()
        self.assertFalse(regiao.any() or classes.any())  # a cópia parada no lugar não é edição
        self.assertEqual(self.s.historico()[-1]["rotulo"], "Camada via cópia")
        self.s.deslocar_camada(None, 0, 70)  # o Mover leva a camada: copiar e colar
        alterados, regiao, classes = self.s.mascaras()
        self.assertTrue(alterados[130:190, 40:160].any())
        self.assertFalse(regiao[:125].any())  # o trecho de origem continua autêntico
        self.assertTrue((classes[regiao] == IDS["valor"]).all())
        self.assertTrue(self.s.pode_salvar()[0])

    def test_fundo_copia_inteiro_so_marca_o_que_for_editado_nele(self):
        self.s.camada_via(None, categoria="valor")
        cam = self.s.camadas[self.s.ativa]
        self.assertEqual(cam.nome, "Fundo cópia")
        self.assertTrue((cam.rgba[..., 3] == 255).all())
        self.pintar([[100, 100]], "#ff0000", "valor")  # na camada ativa (mesmo tipo)
        self.assertEqual(len(self.s.ordem), 1)
        _, regiao, _ = self.s.mascaras()
        self.assertTrue(regiao[100, 100])
        self.assertLess(regiao.sum(), 2000)  # só a pincelada, não a página inteira

    def test_recorte_do_fundo_preenche_o_buraco_num_passo_so(self):
        sel = self.sel()
        res = executar("preencher", self.s.atual, {"semente": 1}, sel, base=self.s.base)
        n = len(self.s.historico())
        self.s.camada_via(sel, recortar=True, categoria="valor", preenchimento=res, params={"semente": 1})
        self.assertEqual(len(self.s.historico()), n + 1)
        self.assertEqual(self.s.historico()[-1]["rotulo"], "Camada via recorte")
        cam = self.s.camadas[self.s.ativa]
        np.testing.assert_array_equal(self.s.atual, self.s.base)  # a camada nova cobre o buraco
        self.s.deslocar_camada(None, 0, 80)
        self.assertFalse(np.array_equal(self.s.atual[60:120, 40:160], self.s.base[60:120, 40:160]))  # o buraco aparece
        self.s.desfazer()
        self.s.desfazer()  # um Ctrl+Z desfaz o recorte inteiro (camada e preenchimento)
        np.testing.assert_array_equal(self.s.atual, self.s.base)
        self.assertNotIn(cam.id, self.s.ordem)
        self.assertEqual(self.s.historico(), [])

    def test_recorte_de_camada_tira_da_origem(self):
        self.s.acao_camada("criar", categoria="valor")  # uma camada da pessoa (Ctrl+Shift+N)
        origem = self.s.ativa
        self.pintar([[100, 90]], "#ff0000", "valor", tamanho=40, camada=origem)
        self.s.camada_via(self.sel(), recortar=True)
        nova = self.s.camadas[self.s.ativa]
        self.assertEqual(nova.categoria, "valor")
        self.assertEqual(self.s.ordem.index(nova.id), self.s.ordem.index(origem) + 1)
        self.assertTrue((self.s.camadas[origem].rgba[60:120, 40:160, 3] == 0).all())
        self.assertTrue((nova.rgba[60:120, 40:160, 3] > 0).any())

    def test_erros(self):
        with self.assertRaises(ErroOperacao):
            self.s.camada_via(None, recortar=True, categoria="valor")  # recortar pede seleção
        with self.assertRaises(ErroOperacao):
            self.s.camada_via(self.sel(), categoria=None)  # do Fundo, o tipo da edição
        self.assertEqual(self.s.ordem, [])

    def test_rascunho_guarda_a_copia_sem_virar_edicao(self):
        self.s.camada_via(self.sel(), categoria="valor")
        self.s = self.nova()
        self.assertEqual(len(self.s.ordem), 1)
        self.assertFalse(self.s.mascaras()[1].any())


if __name__ == "__main__":
    unittest.main()
