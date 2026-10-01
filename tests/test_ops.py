import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from rotulador import selecao
from rotulador.ops import codigos, executar, geradores, ia, texto
from rotulador.registro import Item
from rotulador.sessao import ErroOperacao, Sessao
from tests.util import conta

IDS = {"nome": 1, "valor": 9, "codigo barras": 11, "qr code/pix": 15, "outro": 16}


def ret(x0, y0, x1, y1, **kw):
    return {"formas": [{"tipo": "ret", "x0": x0, "y0": y0, "x1": x1, "y1": y1}], **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.img = conta(7)
        self.item = Item(doc="abc", split="treino", caminho=self.tmp / "x.png", rel="x.png", pagina=0, paginas=1,
                         sha_arquivo="f", sha_pixels="p", altura=self.img.shape[0], largura=self.img.shape[1])
        self.s = Sessao(self.item, self.img.copy(), self.tmp / "rasc", IDS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def aplicar(self, tipo, params, spec=None, categoria="valor", **ctx):
        sel = selecao.rasterizar(spec, self.s.atual)
        res = executar(tipo, self.s.atual, params, sel, base=self.s.base, **ctx)
        return self.s.aplicar(tipo, categoria, "", params, res), sel

    def checar_invariantes(self):
        alterados, regiao, classes = self.s.mascaras()
        np.testing.assert_array_equal(alterados, (self.s.atual != self.s.base).any(2))
        self.assertFalse((alterados & ~regiao).any(), "pixel alterado fora da região editada")
        self.assertTrue((classes[regiao] > 0).all())
        self.assertFalse((classes[~regiao] > 0).any())


class TestSessao(Base):
    def test_pincel_respeita_selecao_e_desfaz_bit_a_bit(self):
        spec = ret(50, 50, 120, 90)
        self.aplicar("pincel", {"pontos": [[10, 70], [300, 70]], "tamanho": 30, "cor": "#ff0000"}, spec)
        alterados, _, _ = self.s.mascaras()
        fora = np.ones_like(alterados)
        fora[50:90, 50:120] = False
        self.assertTrue(alterados.any())
        self.assertFalse((alterados & fora).any())
        self.checar_invariantes()
        self.s.desfazer()
        np.testing.assert_array_equal(self.s.atual, self.s.base)
        self.s.refazer()
        self.assertTrue((self.s.atual != self.s.base).any())

    def test_sem_categoria_nao_aplica(self):
        with self.assertRaises(ErroOperacao):
            self.aplicar("pincel", {"pontos": [[10, 70]], "tamanho": 9}, categoria=None)
        with self.assertRaises(ErroOperacao):
            self.aplicar("pincel", {"pontos": [[10, 70]], "tamanho": 9}, categoria="inexistente")
        with self.assertRaises(ErroOperacao):  # 'outro' exige descrição
            self.aplicar("pincel", {"pontos": [[10, 70]], "tamanho": 9}, categoria="outro")

    def test_borracha_devolve_original_e_tira_da_regiao(self):
        self.aplicar("pincel", {"pontos": [[100, 200]], "tamanho": 20, "cor": "#00ff00"})
        self.aplicar("borracha", {"pontos": [[100, 200]], "tamanho": 40, "dureza": 1}, categoria=None)
        np.testing.assert_array_equal(self.s.atual, self.s.base)
        alterados, regiao, classes = self.s.mascaras()
        self.assertFalse(regiao.any())
        self.assertFalse(self.s.pode_salvar()[0])

    def test_rascunho_sobrevive_a_queda(self):
        self.aplicar("pincel", {"pontos": [[100, 200]], "tamanho": 20, "cor": "#00ff00"})
        self.aplicar("pincel", {"pontos": [[150, 220]], "tamanho": 10, "cor": "#0000ff"}, categoria="nome")
        self.s.desfazer()
        nova = Sessao(self.item, self.img.copy(), self.tmp / "rasc", IDS)
        np.testing.assert_array_equal(nova.atual, self.s.atual)
        self.assertEqual(len(nova.deltas), 1)
        self.assertEqual(len(nova.refazer_pilha), 1)
        nova.refazer()
        self.assertEqual(nova.categorias(), ["nome", "valor"])

    def test_operacao_sem_efeito_recusada(self):
        with self.assertRaises(ErroOperacao):  # pinta branco puro sobre... nada: borda fora da imagem
            self.aplicar("pincel", {"pontos": [[-500, -500]], "tamanho": 5})


class TestOperacoes(Base):
    def test_balde(self):
        self.aplicar("balde", {"x": 5, "y": 5, "tolerancia": 10, "cor": "#ff8800"})
        self.checar_invariantes()

    def test_preencher_selecao(self):
        self.aplicar("preencher", {"patch": 7}, ret(14, 192, 200, 214))
        alterados, _, _ = self.s.mascaras()
        self.assertTrue(alterados[192:214, 14:200].any())
        self.assertFalse(alterados[:190].any())
        self.checar_invariantes()

    def test_corretivo_e_carimbo(self):
        self.aplicar("corretivo", {"pontos": [[40, 130], [90, 130]], "tamanho": 12})
        self.aplicar("carimbo", {"pontos": [[200, 300]], "tamanho": 20, "dx": 0, "dy": -240})
        self.checar_invariantes()

    def test_carimbo_copia_exata(self):
        self.aplicar("carimbo", {"pontos": [[60, 300], [80, 300]], "tamanho": 10, "dureza": 1, "dx": 0, "dy": -230})
        # no miolo do traço (pincel sólido) o destino é cópia exata da origem
        np.testing.assert_array_equal(self.s.atual[297:304, 60:81], self.s.base[67:74, 60:81])

    def test_texto_e_substituir(self):
        self.aplicar("texto", {"texto": "R$ 999,99", "x": 20, "y": 330, "tamanho": 18, "cor": "#101010"})
        spec = ret(10, 194, 220, 218)
        sel = selecao.rasterizar(spec, self.s.atual)
        est = texto.estimar(self.s.atual, sel)
        self.assertTrue(est["achou_texto"])
        self.assertLess(abs(est["y"] - 210), 6)
        self.assertLess(int(est["cor"][1:3], 16), 90)  # texto escuro
        self.aplicar("substituir_texto", {**est, "texto": "VENC 31/12/2026"}, spec, categoria="nome")
        self.checar_invariantes()

    def test_texto_com_opcoes(self):
        self.aplicar("texto", {"texto": "ABC", "x": 30, "y": 380, "tamanho": 20, "rotacao": 15, "largura": 80,
                               "negrito": 1, "espacamento": 2, "desfoque": 0.5})
        self.aplicar("texto", {"texto": "XYZ", "x": 150, "y": 380, "tamanho": 20, "antialias": False,
                               "cor": "#000000"}, categoria="nome")
        d = self.s.deltas[-1]
        mudou = d.depois[(d.antes != d.depois).any(2)]
        self.assertTrue((mudou == 0).all(), "sem antialias o texto deve ser só preto puro")
        self.checar_invariantes()

    def test_transformar_translacao_inteira_e_exata(self):
        spec = ret(14, 58, 120, 80)
        dest = [[14 + 150, 58 + 250], [120 + 150, 58 + 250], [120 + 150, 80 + 250], [14 + 150, 80 + 250]]
        self.aplicar("transformar", {"destino": dest, "modo": "duplicar"}, spec)
        np.testing.assert_array_equal(self.s.atual[308:330, 164:270], self.s.base[58:80, 14:120])
        self.checar_invariantes()

    def test_transformar_mover_com_perspectiva(self):
        spec = ret(14, 58, 120, 80)
        dest = [[150, 300], [270, 290], [275, 330], [148, 325]]
        self.aplicar("transformar", {"destino": dest, "modo": "mover", "origem": "preencher"}, spec)
        _, regiao, _ = self.s.mascaras()
        self.assertTrue(regiao[58:80, 14:120].all())  # o buraco conta como editado
        self.checar_invariantes()

    def test_colar_de_doador(self):
        doador = conta(99)
        dest = [[20, 300], [120, 300], [120, 330], [20, 330]]
        self.aplicar("colar", {"doador": "d", "origem": {"x0": 14, "y0": 58, "x1": 114, "y1": 88},
                               "destino": dest}, carregar_doador=lambda doc: doador)
        np.testing.assert_array_equal(self.s.atual[300:330, 20:120], doador[58:88, 14:114])
        self.checar_invariantes()

    def test_ajustes(self):
        spec = ret(10, 20, 200, 230)  # inclui a faixa azul: matiz/saturação não mudam cinza
        for p in ({"ajuste": "brilho_contraste", "brilho": 20, "contraste": 10},
                  {"ajuste": "niveis", "preto": 10, "branco": 240, "gama": 1.2},
                  {"ajuste": "matiz_saturacao", "matiz": 30, "saturacao": 20},
                  {"ajuste": "desfoque", "sigma": 1.5}, {"ajuste": "nitidez", "quantidade": 150, "raio": 1},
                  {"ajuste": "ruido", "sigma": 8}, {"ajuste": "jpeg", "qualidade": 30}, {"ajuste": "cinza"}):
            self.aplicar("ajuste", p, spec if p["ajuste"] != "cinza" else ret(0, 0, 320, 40))
        alterados, _, _ = self.s.mascaras()
        self.assertFalse(alterados[240:].any())
        self.checar_invariantes()

    def test_qrcode_legivel(self):
        conteudo = geradores.gerar("qr code/pix")["texto"]
        self.aplicar("qrcode", {"conteudo": conteudo, "borda": 4}, ret(60, 120, 300, 360), categoria="qr code/pix")
        lido, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.cvtColor(self.s.atual, cv2.COLOR_RGB2BGR))
        self.assertEqual(lido, conteudo)

    def test_codigo_barras_decodifica(self):
        larga = np.full((120, 1000, 3), 250, np.uint8)
        self.s = Sessao(self.item, larga, self.tmp / "rasc2", IDS)
        for _ in range(20):
            cod = geradores.gerar("codigo barras")["digitos"]
            self.aplicar("codigo_barras", {"digitos": cod, "margem": 10}, ret(20, 20, 980, 100),
                         categoria="codigo barras")
            self.assertEqual(decodificar_itf(self.s.atual[60, 20:980]), cod)
        with self.assertRaises(ErroOperacao):  # estreito demais: menos de 1 px por módulo
            self.aplicar("codigo_barras", {"digitos": cod}, ret(20, 20, 300, 100), categoria="codigo barras")

    def test_ia_compoe_so_dentro_da_selecao(self):
        def editor_falso(rgb, prompt, semente, passos):
            self.assertEqual(rgb.shape[0] % 32, 0)
            self.assertEqual(rgb.shape[1] % 32, 0)
            return np.random.default_rng(semente).integers(0, 256, rgb.shape, dtype=np.uint8)

        spec = ret(14, 192, 200, 214)
        self.aplicar("ia", {"prompt": "troque a data", "semente": 3}, spec, editor=editor_falso)
        alterados, _, _ = self.s.mascaras()
        fora = np.ones_like(alterados)
        fora[192:214, 14:200] = False
        self.assertTrue(alterados[192:214, 14:200].any())
        self.assertFalse((alterados & fora).any())


def decodificar_itf(linha: np.ndarray) -> str:
    """Decodificador ITF mínimo para conferir o que foi desenhado."""
    escuro = linha.mean(1) < 128
    trocas = np.flatnonzero(np.diff(escuro.astype(int))) + 1
    corridas = np.diff(np.r_[0, trocas, len(escuro)])
    valores = escuro[np.r_[0, trocas]]
    i0 = int(np.argmax(valores))  # primeira barra
    elementos = corridas[i0:]
    if not escuro[-1]:
        elementos = elementos[:-1]
    estreito = np.median(elementos[:4])
    larg = (elementos > 2 * estreito).astype(int)
    corpo = larg[4:-3]
    tabela = {v: k for k, v in codigos._ITF.items()}
    out = []
    for k in range(0, len(corpo), 10):
        bloco = corpo[k:k + 10]
        out.append(tabela["".join(map(str, bloco[0::2]))])
        out.append(tabela["".join(map(str, bloco[1::2]))])
    return "".join(out)


class TestGeradores(unittest.TestCase):
    def test_documentos_validos(self):
        for _ in range(200):
            self.assertTrue(geradores.valida_cpf(geradores.cpf()))
            self.assertTrue(geradores.valida_cnpj(geradores.cnpj()))

    def test_codigos_validos(self):
        for _ in range(200):
            g = geradores.gerar("codigo barras")
            cod = g["digitos"]
            self.assertEqual(len(cod), 44)
            self.assertTrue(codigos.valida_arrecadacao(cod) if cod[0] == "8" else codigos.valida_boleto(cod))

    def test_linha_arrecadacao_conhecida(self):
        # exemplo construído: DV geral por módulo 10 e blocos de 11 + DV
        cod = codigos.arrecadacao(12345, segmento="3", empresa="0048", livre="1" * 25, ref="6")
        self.assertTrue(codigos.valida_arrecadacao(cod))
        linha = codigos.linha_arrecadacao(cod)
        self.assertEqual(linha.replace("-", "").replace(" ", "")[0:11], cod[0:11])
        self.assertEqual(len(linha.split()), 4)

    def test_boleto_linha_tem_47_digitos(self):
        import datetime as dt
        cod = codigos.boleto("341", 18740, dt.date(2026, 10, 10), "1" * 25)
        self.assertTrue(codigos.valida_boleto(cod))
        self.assertEqual(sum(c.isdigit() for c in codigos.linha_boleto(cod)), 47)

    def test_pix_crc(self):
        p = geradores.gerar("qr code/pix")["texto"]
        self.assertEqual(codigos.crc16(p[:-4]), p[-4:])

    def test_todas_categorias_tem_valor(self):
        for c in ("nome", "cpf/cnpj", "endereço", "bairro", "cep", "cidade/uf", "data", "mês de referência",
                  "valor", "consumo/leitura", "codigo barras", "linha digitável", "identificador documento"):
            self.assertTrue(geradores.gerar(c)["texto"], c)


class TestSelecao(unittest.TestCase):
    def test_modos_e_ajustes(self):
        img = conta(1)
        spec = {"formas": [{"tipo": "ret", "x0": 10, "y0": 10, "x1": 50, "y1": 50},
                           {"tipo": "ret", "x0": 30, "y0": 30, "x1": 70, "y1": 70, "modo": "somar"},
                           {"tipo": "elipse", "x0": 20, "y0": 20, "x1": 40, "y1": 40, "modo": "subtrair"}]}
        m = selecao.rasterizar(spec, img).completa_bool()
        self.assertTrue(m[15, 15] and m[60, 60] and not m[30, 30] and not m[5, 5])
        inv = selecao.rasterizar({**spec, "inverter": True}, img).completa_bool()
        np.testing.assert_array_equal(inv, ~m)
        maior = selecao.rasterizar({**spec, "expandir": 3}, img).completa_bool()
        self.assertGreater(maior.sum(), m.sum())
        suave = selecao.rasterizar({**spec, "suavizar": 2}, img)
        self.assertTrue(((suave.alpha > 0) & (suave.alpha < 1)).any())

    def test_varinha_e_poligono(self):
        img = conta(1)
        v = selecao.rasterizar({"formas": [{"tipo": "varinha", "x": 5, "y": 5, "tolerancia": 5}]}, img)
        self.assertTrue(v.completa_bool()[0:40].mean() > 0.8)  # a faixa azul do topo
        self.assertFalse(v.completa_bool()[100:].any())
        p = selecao.rasterizar({"formas": [{"tipo": "poligono", "pontos": [[0, 0], [100, 0], [0, 100]]}]}, img)
        self.assertTrue(p.completa_bool()[10, 10] and not p.completa_bool()[90, 90])

    def test_entrada_invalida(self):
        with self.assertRaises(selecao.ErroSelecao):
            selecao.rasterizar({"formas": [{"tipo": "ret", "x0": "a"}]}, conta(1))
        with self.assertRaises(selecao.ErroSelecao):
            selecao.rasterizar({"formas": [{"tipo": "xyz"}]}, conta(1))


class TestIA(unittest.TestCase):
    def test_tamanho_modelo_multiplo_de_32(self):
        for h, w in ((40, 300), (900, 700), (33, 33)):
            H, W = ia.tamanho_modelo(h, w, 1.0)
            self.assertEqual((H % 32, W % 32), (0, 0))
            self.assertAlmostEqual(H * W / 1e6, 1.0, delta=0.35)


if __name__ == "__main__":
    unittest.main()
