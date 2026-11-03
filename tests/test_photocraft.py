"""O motor PhotoCraft (rotulador.photocraft) e a técnica campo.photocraft.

A parte que só monta passos (seleção, papel livre, amostra, texto, peso) roda sempre; a edição de verdade só com o
``photocraft-cli`` instalado (``PHOTOCRAFT_CLI`` ou ``config/photocraft.json``)."""
import math
import random
import unittest
from unittest import mock

import cv2
import numpy as np

from rotulador import catalogo, falsificador, gerador, humano, photocraft
from tests.test_tecnicas import IDS, Base


class TestSelecao(unittest.TestCase):
    def test_poligonos_cobrem_a_mascara(self):
        m = np.zeros((60, 120), bool)
        m[10:30, 10:40] = True
        m[35:50, 70:110] = True
        m[20, 60] = True  # um pixel solto (o pingo do i)
        passos = photocraft.passos_selecao(m)
        self.assertEqual([p[1]["mode"] for p in passos], ["replace", "add", "add"])
        tela = np.zeros(m.shape, np.uint8)
        for _, p in passos:
            cv2.fillPoly(tela, [np.floor(np.array(p["points"])).astype(np.int32)], 1)
        self.assertTrue(tela[m].all(), "a seleção não cobre a máscara inteira")
        self.assertLess(tela.sum(), 1.5 * m.sum() + 200)  # e não vai muito além (1 px de folga)

    def test_selecao_vazia(self):
        with self.assertRaises(Exception):
            photocraft.passos_selecao(np.zeros((10, 10), bool))


class TestPapelLivre(unittest.TestCase):
    def setUp(self):
        self.proibido = np.zeros((200, 300), bool)
        self.proibido[90:110, 50:250] = True  # a linha de texto
        self.proibido[20:40, :] = True  # a linha de cima
        self.alvo = np.zeros((200, 300), bool)
        self.alvo[92:108, 60:240] = True

    def test_origem_cai_em_papel_livre(self):
        for s in range(8):
            dx, dy = photocraft.origem_limpa(self.proibido, self.alvo, random.Random(s))
            movido = np.roll(np.roll(self.alvo, dy, 0), dx, 1)
            self.assertFalse((movido & self.proibido).any(), (dx, dy))
            self.assertLessEqual(abs(dx), 12)  # de preferência na vertical

    def test_sem_papel(self):
        tudo = np.ones((200, 300), bool)
        self.assertIsNone(photocraft.origem_limpa(tudo, self.alvo, random.Random(1)))

    def test_amostra_so_no_papel(self):
        tracos = photocraft.faixas_de_amostra(self.proibido, self.alvo, 18.0)
        self.assertEqual(len(tracos), 2)  # acima e abaixo
        for t in tracos:
            (xa, ya), (xb, yb) = t["points"]
            meia = t["size"] / 2
            linhas = slice(int(math.floor(ya - meia)), int(math.ceil(ya + meia)))
            colunas = slice(int(xa - meia), int(xb + meia))
            self.assertFalse(self.proibido[linhas, colunas].any(), t)


class TestPassosTexto(unittest.TestCase):
    def test_tracking_escala_e_rotacao(self):
        ps = photocraft.passos_texto("Arial", "Bold", "R$ 10,00", 10.5, 40.25, 20.0, "#101010", largura=96.0,
                                     entreletra=0.5, suavizacao="crisp", rotacao=1.5)
        cmd, p = ps[0]
        self.assertEqual(cmd, "type.create")
        self.assertEqual((p["font"], p["fontStyle"], p["size"], p["horizontalScale"]), ("Arial", "Bold", 20.0, 96.0))
        self.assertAlmostEqual(p["tracking"], 25.0)  # 0,5 px num corpo de 20 px = 25 milésimos de eme
        self.assertEqual(ps[1][0], "type.antiAlias.crisp")
        a, b, c, d, e, f = ps[2][1]["transform"]
        self.assertAlmostEqual(math.degrees(math.atan2(b, a)), -1.5)
        self.assertEqual((e, f), (10.5, 40.25))

    def test_sem_extras(self):
        ps = photocraft.passos_texto("Arial", "Regular", "X", 0, 0, 12, "#000000")
        self.assertNotIn("horizontalScale", ps[0][1])
        self.assertNotIn("tracking", ps[0][1])
        self.assertEqual(len(ps), 2)

    def test_peso_pela_mistura(self):
        self.assertEqual(photocraft.passos_peso(0.01, 3), [])
        afinar = photocraft.passos_peso(-0.4, 3)
        self.assertEqual([p[0] for p in afinar], ["layer.duplicate", "filter.other.minimum", "layer.setProps"])
        self.assertEqual(afinar[2][1], {"layer": 3, "opacity": 0.6})  # a original embaixo, mais transparente
        engrossar = photocraft.passos_peso(0.25, 3)
        self.assertEqual(engrossar[1][0], "filter.other.maximum")
        self.assertEqual(engrossar[2][1], {"opacity": 0.25})  # a cópia engrossada por cima


class TestFace(unittest.TestCase):
    def test_familia_tipografica(self):
        """O motor agrupa a Arial Narrow na família Arial (estilo "Narrow Bold")."""
        m = photocraft._Motor(photocraft.Path("x"))

        def executar(cmd, **p):
            if not p:
                return {"families": ["Arial", "Helvetica", "Calibri"]}
            return {"faces": [{"style": s} for s in ("Regular", "Bold", "Narrow", "Narrow Bold")]}
        with mock.patch.object(m, "executar", side_effect=executar), \
                mock.patch.object(photocraft.glifos, "fonte_pil") as fp:
            fp.return_value.getname.return_value = ("Arial Narrow", "Bold")
            self.assertEqual(m.face("Arial Narrow Bold"), ("Arial", "Narrow Bold"))
            fp.return_value.getname.return_value = ("Arial", "Regular")
            self.assertEqual(m.face("Arial"), ("Arial", "Regular"))
            fp.return_value.getname.return_value = ("Comic Sans MS", "Regular")
            with self.assertRaises(Exception):
                m.face("Comic Sans MS")


class TestNoGerador(unittest.TestCase):
    def test_no_catalogo(self):
        t = catalogo.TECNICAS["campo.photocraft"]
        self.assertEqual((t.num, t.familia, t.nivel, t.granularidade), (14, "imitacao", "profissional", "campo"))
        self.assertEqual({v for v, _ in t.opcoes["ferramenta"]["valores"]} | {"cor"}, set(photocraft.APAGADORES))
        self.assertIn("campo.photocraft", falsificador.ACEITAM_TEXTO)
        self.assertTrue(set(photocraft.DO_HUMANO) >= {v for v, _ in humano.APAGAR})

    def test_sai_do_sorteio_sem_o_motor(self):
        f = {"id": "t0", "categoria": "nome", "texto": "MARIA DA SILVA", "caixa": [10, 10, 200, 40], "tipo": "texto"}
        r = gerador.Receita({"perfil": "profissional", "categorias_base": ["nome"], "extras": False}, IDS)
        for tem in (True, False):
            with mock.patch.object(photocraft, "disponivel", return_value=tem):
                ids = [t.id for t in gerador._servem_profissional(f, r)["campo"]]
            self.assertEqual("campo.photocraft" in ids, tem)


@unittest.skipUnless(photocraft.disponivel(), "photocraft-cli não instalado")
class TestEdicao(Base):
    """A edição de verdade no motor, na conta sintética em Arial."""

    def test_cada_ferramenta(self):
        nome = self.campo("nome")
        for ferramenta in ("preenchimento", "remendo", "carimbo", "recuperacao", "remover"):
            with self.subTest(ferramenta=ferramenta):
                res = self.rodar("campo.photocraft", nome, opcoes={"ferramenta": ferramenta},
                                 acabamento={"pericia": "cru"})
                self.confere_rotulo(res, "campo.photocraft")
                self.so_perto(res, nome["caixa"], esq=40, dir_=200, cima=40, baixo=40)
                self.confere_ocr(res, nome["caixa"], folga_dir=120)
                m = res.meta
                self.assertEqual(m["fornecedores"]["motor"], "photocraft")
                self.assertEqual(m["fornecedores"]["apagar"], f"photocraft.{ferramenta}")
                self.assertLess(abs(m["peso_traco"] - 1), 0.12, m["descricao"])
                # o texto antigo sumiu: nenhuma letra dele fora da tinta nova
                out = self.aplicado(res)
                self.assertNotEqual(m["texto_novo"], m["texto_antigo"])
                self.assertGreater(np.abs(out.astype(int) - self.img.astype(int)).max(), 100)

    def test_operador_humano(self):
        nome = self.campo("nome")
        hab = humano.sortear(random.Random(5), {"prob": 1}, True)
        hab.update(selecao="laco", apagar="carimbo", retoques={"ruido": {"quantidade": 2.0, "distribuicao": "gaussiana",
                                                                        "monocromatico": True}})
        res = self.rodar("campo.photocraft", nome, habitos={"humano": hab})
        m = res.meta
        self.assertEqual(m["fornecedores"]["apagar"], "photocraft.carimbo")
        self.assertEqual(m["fornecedores"]["selecao"], "laco")
        self.assertNotIn("retoques_motor", m)  # o acabamento da pessoa leu e não refez em Python
        self.assertIn("retoque: ruido", m["acabamento"])
        self.assertEqual(m["photocraft"]["retoques"], ["ruido"])
