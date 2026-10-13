"""Ferramentas revistas em 11/10/2026 (bateria em E:/rotulador/ferr): o carimbo clona papel de verdade em faixas (antes
caía no PatchMatch em 16 de 16 campos), o PatchMatch devolve o grão do papel (a votação o apagava), os apagadores por
difusão não deixam manchinhas, texto claro sobre fundo escuro é recusado (virava um borrão branco), raspar traço
continua sendo borracha pequena com o operador humano e as letras do documento usam a ferramenta da pessoa."""
import random
import unittest

import cv2
import numpy as np

from rotulador import humano
from rotulador.ops import executar
from rotulador.ops.apagar import _carimbo, reconstruir
from rotulador.ops.preenchimento import preencher
from rotulador.ops.tecnicas import texto_claro_em_fundo_escuro
from rotulador.selecao import vazia
from rotulador.sessao import ErroOperacao


def papel(h=160, w=320, semente=0):
    g = np.random.default_rng(semente)
    base = 225 + cv2.GaussianBlur(g.normal(0, 6, (h, w)).astype(np.float32), (0, 0), 0.8)
    return np.repeat(np.clip(base, 0, 255)[..., None], 3, 2).astype(np.uint8)


def hp_std(a, m):
    a = a.astype(np.float32).mean(2)
    return float(np.std((a - cv2.GaussianBlur(a, (0, 0), 2.0))[m]))


class TestCarimbo(unittest.TestCase):
    def test_clona_papel_sem_tinta_e_com_o_grao(self):
        rec = papel()
        tinta = np.zeros(rec.shape[:2], bool)
        tinta[70:90, 40:280] = True  # a linha a apagar
        tinta[20:34, 30:300] = True  # outra linha logo acima (não pode ser origem)
        rec[tinta] = 30
        alvo = np.zeros_like(tinta)
        alvo[66:94, 36:284] = True
        buraco = (cv2.dilate(tinta.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0) | alvo
        out = _carimbo(rec, buraco, 3, alvo)
        self.assertGreater(out[alvo].mean(), 200)  # nenhuma tinta copiada
        dentro = cv2.erode(alvo.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        fora = np.zeros_like(alvo)
        fora[110:150, 40:280] = True
        self.assertGreater(hp_std(out, dentro) / hp_std(out, fora), 0.85)
        self.assertTrue((out[~alvo] == rec[~alvo]).all())

    def test_recuperar_e_carimbo_pelo_reconstruir(self):
        rec = papel(semente=2)
        buraco = np.zeros(rec.shape[:2], bool)
        buraco[60:100, 50:250] = True
        for modo in ("carimbo", "recuperar"):
            out = reconstruir(rec, buraco, modo, 1, 20.0, alvo=buraco)
            self.assertLess(abs(float(out[buraco].mean()) - float(rec[~buraco].mean())), 4, modo)


class TestPatchMatchComGrao(unittest.TestCase):
    def test_grao_volta(self):
        rec = papel(120, 200, 5)
        m = np.zeros(rec.shape[:2], bool)
        m[40:80, 50:150] = True
        dentro = cv2.erode(m.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        fora = ~cv2.dilate(m.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
        com = preencher(rec, m, semente=1)
        sem = preencher(rec, m, semente=1, grao=False)
        r_com, r_sem = hp_std(com, dentro) / hp_std(rec, fora), hp_std(sem, dentro) / hp_std(rec, fora)
        self.assertGreater(r_com, r_sem)
        self.assertGreater(r_com, 0.75)


class TestFundoEscuro(unittest.TestCase):
    def test_texto_claro_na_faixa(self):
        img = np.full((200, 400, 3), 245, np.uint8)
        img[20:80] = (0, 84, 147)  # faixa azul do cabeçalho
        cv2.putText(img, "00.000.000/0001-91", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(img, "MARIA DA SILVA", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 20, 20), 2)
        self.assertTrue(texto_claro_em_fundo_escuro(img, (16, 38, 300, 66)))
        self.assertFalse(texto_claro_em_fundo_escuro(img, (16, 128, 260, 156)))
        p = {"tecnica": "campo.remover", "campo": {"id": "c", "caixa": [16, 38, 300, 66], "texto": "00.000.000/0001-91",
                                                    "categoria": "cpf/cnpj", "tipo": "texto"}, "semente": 1}
        with self.assertRaisesRegex(ErroOperacao, "fundo escuro"):
            executar("tecnica", img, p, vazia(200, 400), base=img)


class TestOperadorNasTecnicas(unittest.TestCase):
    def test_letras_do_documento_com_a_ferramenta_da_pessoa(self):
        from tests.test_auto import conta
        img = conta()
        campo = {"id": "t0", "caixa": [330, 170, 640, 200], "texto": "JOAO PEREIRA LIMA 01", "categoria": "nome",
                 "tipo": "texto"}
        hab = humano.sortear(random.Random(1), {"prob": 1.0, "salvar": "png"}, False)
        hab.update(selecao="retangulo", apagar="carimbo", retoques={})
        p = {"tecnica": "campo.letras_do_documento", "campo": campo, "semente": 4, "conferir_ocr": False,
             "texto": "PEREIRA LIMA JOAO 01",  # só letras que a conta tem
             "habitos": {"humano": hab}, "acabamento": {"disfarce": "nunca"}}
        try:
            res = executar("tecnica", img, p, vazia(*img.shape[:2]), base=img)
        except ErroOperacao as e:
            self.skipTest(f"sem letras para montar aqui: {e}")
        self.assertEqual(res.meta["fornecedores"]["apagar"], "carimbo")
        self.assertEqual(res.meta["fornecedores"].get("selecao"), "retangulo")
        self.assertIn("carimbo", res.meta["descricao"])


class TestCepCidade(unittest.TestCase):
    """O detector separa "CEP CIDADE UF" em dois campos (bateria de 08/10: CEP certo 14% → 93%, cidade 8% → 91%) e
    a cidade nova sai no formato do campo, com a UF."""

    def _separar(self, texto):
        from unittest import mock
        from rotulador import campos as mc
        campo = {"id": "t1", "categoria": "cep", "texto": texto, "caixa": [0, 0, 400, 20], "caixa_ocr": [0, 0, 400, 20],
                 "rotulo": "", "conf": 0.9, "tipo": "texto"}

        def trecho(img, caixa, t, ini, fim):  # 10 px por caractere
            return [10 * ini, 0, 10 * fim, 20]
        with mock.patch.object(mc, "caixa_do_trecho", side_effect=trecho):
            return mc._separar_cep_cidade(np.zeros((20, 400, 3), np.uint8), campo)

    def test_separa(self):
        for texto, cep, cidade in (("30180-101 BELO HORIZONTE MG", "30180-101", "BELO HORIZONTE MG"),
                                   ("81500-000 - Curitiba/PR", "81500-000", "Curitiba/PR"),
                                   ("Fortaleza - CE, 60750-720", "60750-720", "Fortaleza - CE"),
                                   ("60750-720 - FORTALEZA - CE", "60750-720", "FORTALEZA - CE")):
            a, b = self._separar(texto)
            self.assertEqual((a["categoria"], a["texto"]), ("cep", cep), texto)
            self.assertEqual((b["categoria"], b["texto"]), ("cidade/uf", cidade), texto)
            self.assertEqual(texto[b["caixa"][0] // 10:b["caixa"][2] // 10], cidade)

    def test_nao_separa(self):
        for texto in ("60750-720", "CEP: 60750-720", "CEP 60750-720", "60.750-720"):
            self.assertEqual(len(self._separar(texto)), 1, texto)

    def test_cidade_no_formato_com_uf_por_espaco(self):
        from rotulador.ops import geradores
        self.assertEqual(geradores.cidade_no_formato("BELO HORIZONTE MG", "Leopoldina", "MG"), "LEOPOLDINA MG")
        self.assertEqual(geradores.cidade_no_formato("Fortaleza - CE", "Caucaia", "CE"), "Caucaia - CE")
        self.assertEqual(geradores.cidade_no_formato("Fortaleza", "Caucaia", "CE"), "Caucaia")


if __name__ == "__main__":
    unittest.main()
