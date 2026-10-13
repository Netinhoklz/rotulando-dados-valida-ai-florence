"""Acento solto e trecho: o que o lote do perfil profissional achou nas técnicas parciais.

1. ``glifos.segmentar`` deixava o acento fora da letra (a margem era menor que a altura dele na Tahoma): quem apagava a
   letra deixava o acento ("ARAÚJO" → "SIMÓES") e quem movia não o levava. As letras do campo agora vêm com o sinal
   solto delas (``glifos.com_sinais``).
2. ``_novo_trecho`` aceitava um prenome que só muda o acento (JOSÉ → JOSE).
3. ``trecho.trocar`` punha o dígito novo "no lugar" do antigo achando que todo dígito tem a mesma largura; na
   Bahnschrift o 1 é estreito e o "4" de "04" caía em cima da "/"."""
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import glifos, ocr
from rotulador.ops import geradores, tecnicas
from rotulador.selecao import mascara_tinta, vazia


def _linha(texto, fonte="tahoma.ttf", tam=30, W=760, H=90):
    """(imagem, campo) de uma linha só, com papel levemente granulado."""
    rng = np.random.default_rng(3)
    papel = np.clip(244 + rng.normal(0, 1.5, (H, W)), 0, 255).astype(np.uint8)
    im = Image.fromarray(np.repeat(papel[..., None], 3, 2))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(f"C:/Windows/Fonts/{fonte}", tam)
    d.text((30, 30), texto, font=f, fill=(25, 25, 25))
    img = np.asarray(im).copy()
    x0, y0, x1, y1 = d.textbbox((30, 30), texto, font=f)
    caixa = ocr.caixa_da_linha(img, [x0 - 3, y0 - 3, x1 + 3, y1 + 3])
    return img, caixa


def _rodar(img, caixa, antigo, categoria, tecnica, semente, **p):
    campo = {"id": "t1", "caixa": caixa, "texto": antigo, "categoria": categoria, "tipo": "texto"}
    q = {"tecnica": tecnica, "campo": campo, "semente": semente, "conferir_ocr": False,
         "acabamento": {"pericia": "cru"}, **p}
    return tecnicas.tecnica(img, q, vazia(*img.shape[:2]), base=img)


def _aplicado(img, res):
    out = img.copy()
    sub = out[res.y0:res.y1, res.x0:res.x1]
    sub[:] = np.where(res.pegada[..., None], res.recorte, sub)
    return out


def _faixa_acima(img, caixa_maiusculas, x0, x1):
    """Tinta na faixa logo acima das maiúsculas (onde ficam os acentos), entre as colunas x0 e x1."""
    topo = caixa_maiusculas[1]
    h = caixa_maiusculas[3] - caixa_maiusculas[1]
    a = max(0, topo - int(0.6 * h))
    rec = img[a - 10:topo + 10, x0 - 10:x1 + 10]
    return mascara_tinta(rec).astype(bool)[10:-10 - 2, 10:-10]


class TestAcentoSolto(unittest.TestCase):
    TEXTO = "MARCOS VINÍCIUS ARAÚJO"

    def setUp(self):
        self.img, self.caixa = _linha(self.TEXTO)

    def _c(self, tecnica="trecho.trocar"):
        campo = {"id": "t1", "caixa": self.caixa, "texto": self.TEXTO, "categoria": "nome", "tipo": "texto"}
        return tecnicas._C(self.img, {"tecnica": tecnica, "campo": campo, "semente": 1}, self.img, None, None, None, None)

    def test_letra_vem_com_o_acento(self):
        c = self._c()
        gs = c.glifos
        u = next(g for g in gs if g.ch == "Ú")
        a = next(g for g in gs if g.ch == "A")
        self.assertLess(u.y0, a.y0 - 3)  # a caixa do Ú sobe até o agudo
        _corpo, acima, _abaixo = glifos.partes(u.tinta)
        self.assertEqual(len(acima), 1)
        # os sinais da linha toda: o Í de VINÍCIUS e o Ú, nada a mais
        self.assertEqual(sum(len(glifos.partes(g.tinta)[1]) for g in gs if g.ch in "ÍÚ"), 2)

    def test_trocar_sobrenome_apaga_o_acento(self):
        """ARAÚJO → PEREIRA: o agudo do Ú sai junto (antes sobrava: "PEREÍRA")."""
        for semente in range(40):
            res = _rodar(self.img, self.caixa, self.TEXTO, "nome", "trecho.trocar", semente, texto="PEREIRA",
                         opcoes={"vestigio": "nenhum", "apagar": "patchmatch"})
            if res.meta["alteracoes"][0]["de"] == "ARAÚJO":
                break
        else:
            self.fail("o sorteio nunca escolheu o sobrenome")
        out = _aplicado(self.img, res)
        u = next(g for g in self._c().glifos if g.ch == "Ú")
        maiusculas = ocr.caixa_da_linha(self.img, self.caixa)
        self.assertTrue(_faixa_acima(self.img, maiusculas, u.x0, u.x1).any())  # o agudo estava lá
        self.assertFalse(_faixa_acima(out, maiusculas, u.x0, u.x1).any())  # e saiu

    def test_remover_o_campo_apaga_os_acentos(self):
        res = _rodar(self.img, self.caixa, self.TEXTO, "nome", "campo.remover", 1, opcoes={"parte": "campo"})
        out = _aplicado(self.img, res)
        x0, y0, x1, y1 = self.caixa
        h = y1 - y0
        self.assertFalse(mascara_tinta(out[max(0, y0 - h):y1 + 5, x0 - 10:x1 + 10]).any())


class TestInserirMoveOAcento(unittest.TestCase):
    def test_o_acento_vai_com_a_letra_empurrada(self):
        texto = "RUA 12 SÃO JOSÉ"
        img, caixa = _linha(texto)
        res = _rodar(img, caixa, texto, "endereço", "caractere.inserir", 2)
        out = _aplicado(img, res)
        maiusculas = ocr.caixa_da_linha(img, caixa)

        def centros(im):
            t = _faixa_acima(im, maiusculas, caixa[0], caixa[2] + 60)
            import cv2
            n, _lab, est, cen = cv2.connectedComponentsWithStats(t.astype(np.uint8), connectivity=8)
            return sorted(float(cen[i][0]) for i in range(1, n) if est[i, cv2.CC_STAT_AREA] >= 4)

        antes, depois = centros(img), centros(out)
        self.assertEqual(len(antes), 2)  # o til e o agudo
        self.assertEqual(len(depois), 2)  # nem sobrou fantasma no lugar antigo, nem a letra perdeu o sinal
        desloc = [b - a for a, b in zip(antes, depois)]
        self.assertGreater(min(desloc), 5)
        self.assertLess(abs(desloc[0] - desloc[1]), 2)  # os dois andaram junto com as letras


class TestPrenomeSoComAcento(unittest.TestCase):
    def test_jose_para_jose_sem_acento_nao_e_troca(self):
        rng = mock.Mock()
        with mock.patch.object(geradores, "palavra", side_effect=["JOSE", "José", "PEDRO"]):
            self.assertEqual(tecnicas._novo_trecho("prenome", "JOSÉ", rng), "PEDRO")


class TestDigitoNovoMaisLargo(unittest.TestCase):
    def test_bahnschrift_04_nao_cai_em_cima_da_barra(self):
        """O 1 da Bahnschrift é estreito: "17" → "04" não cabe no lugar; o resto da linha vem junto."""
        texto = "17/09/2026"
        img, caixa = _linha(texto, "bahnschrift.ttf", 34, W=420)
        for semente in range(40):
            res = _rodar(img, caixa, texto, "data", "trecho.trocar", semente, texto="04",
                         opcoes={"vestigio": "nenhum", "apagar": "patchmatch"})
            if res.meta["texto_novo"] == "04/09/2026":
                break
        else:
            self.fail("o sorteio nunca escolheu o dia")
        out = _aplicado(img, res)
        gs = glifos.segmentar(out, ocr.caixa_tinta(out, caixa, 0), "04/09/2026")
        quatro = next(g for g in gs if g.ch == "4")
        barra = next(g for g in gs if g.ch == "/")
        self.assertLessEqual(quatro.x1, barra.x0 + 1, (quatro.caixa, barra.caixa))
        self.assertIn("reescrito junto", res.meta["descricao"])


if __name__ == "__main__":
    unittest.main()
