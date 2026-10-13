"""Técnicas automáticas do catálogo, gerador e rótulos (máscara de técnicas, catalogo.csv).

Os documentos são sintéticos (Arial, como uma conta real) e o OCR deles é gravado direto no
cache (o mesmo que o rotulador usa), então os testes não dependem do motor de OCR. Quando o
RapidOCR está instalado, o texto editado é RELIDO pelo OCR: a edição precisa ser legível como
o valor novo que o rótulo diz.
"""
import csv
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import catalogo, exportar, glifos, ocr
from rotulador.contexto import Contexto, cache_ocr
from rotulador.gerador import Plano, Receita, aplicar_receita
from rotulador.ops import executar
from rotulador.ops.tecnicas import plausivel
from rotulador.registro import Item
from rotulador.selecao import mascara_tinta, vazia
from rotulador.sessao import ErroOperacao, Sessao

try:
    import rapidocr_onnxruntime  # noqa: F401
    TEM_OCR = True
except ImportError:
    TEM_OCR = False

IDS = {"nome": 1, "cpf/cnpj": 2, "endereço": 3, "bairro": 4, "cep": 5, "cidade/uf": 6, "data": 7,
       "mês de referência": 8, "valor": 9, "consumo/leitura": 10, "codigo barras": 11, "linha digitável": 12,
       "identificador documento": 13, "emissor/logotipo": 14, "qr code/pix": 15, "outro": 16}


def F(nome, tam):
    return ImageFont.truetype(f"C:/Windows/Fonts/{nome}", tam)


def documento(nome="MARIA DOS SANTOS SILVA", valor="R$ 158,37", consumo="183 kWh", semente=1):
    """(imagem, linhas do OCR) de uma conta sintética; as linhas têm o texto exato."""
    im = Image.new("RGB", (1240, 760), (252, 252, 250))
    d = ImageDraw.Draw(im)
    linhas = []

    def escrever(x, y, texto, fonte):
        d.text((x, y), texto, font=fonte, fill=(20, 20, 20))
        x0, y0, x1, y1 = d.textbbox((x, y), texto, font=fonte)
        linhas.append({"caixa": [x0 - 3, y0 - 4, x1 + 3, y1 + 4], "texto": texto, "bruto": texto, "conf": 0.99})

    rot, val = F("arialbd.ttf", 22), F("arial.ttf", 30)
    y = 80
    for r, v in [("NOME", nome), ("CPF", "123.456.789-09"), ("ENDERECO", "RUA DAS FLORES, 101"),
                 ("CEP", "30123-456"), ("VENCIMENTO", "10/10/2026"), ("LEITURA", "05/09/2026"), ("CONSUMO", consumo)]:
        escrever(60, y + 4, r, rot)
        escrever(330, y, v, val)
        y += 70
    escrever(90, y + 50, "TOTAL A PAGAR", F("arialbd.ttf", 30))
    escrever(800, y + 40, valor, F("arialbd.ttf", 44))
    a = np.asarray(im).astype(np.float32) + np.random.default_rng(semente).normal(0, 2.5, (760, 1240, 1))
    return np.clip(a, 0, 255).astype(np.uint8), linhas


def item(doc, split, img):
    return Item(doc=doc, split=split, caminho=Path(f"{doc}.png"), rel=f"{doc}.png", pagina=0, paginas=1,
                sha_arquivo=doc * 4, sha_pixels=(doc * 40)[:40], altura=img.shape[0], largura=img.shape[1])


class RegistroFalso:
    def __init__(self, docs):
        self.docs = docs  # doc -> (item, img)
        self.por_split = {"treino": [], "teste": [], "validacao": []}
        for it, _ in docs.values():
            self.por_split[it.split].append(it)

    def carregar(self, doc):
        return self.docs[doc][1]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.saida = cls.tmp / "saida"
        docs = {}
        for doc, split, kw in (("aaaa1111", "treino", {}),
                               ("bbbb2222", "treino", {"nome": "PEDRO ALVES COSTA", "valor": "R$ 942,10", "semente": 2}),
                               ("cccc3333", "teste", {"nome": "LUCAS PINTO GOMES", "valor": "R$ 777,77", "semente": 3})):
            img, linhas = documento(**kw)
            it = item(doc, split, img)
            # com o nome do motor: a lista pura é do OCR antigo e seria relida (as linhas daqui são a verdade)
            ocr.gravar_cache(cache_ocr(cls.saida, it.sha_pixels), linhas)
            docs[doc] = (it, img)
        cls.reg = RegistroFalso(docs)
        cls.it, cls.img = docs["aaaa1111"]
        cls.ctx = Contexto(cls.it, cls.img, cls.saida, cls.reg)
        cls.campos = {c["categoria"] + (str(i) if c["categoria"] == "data" else ""): c
                      for i, c in enumerate(cls.ctx.campos) if c.get("categoria")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def campo(self, cat):
        return next(c for k, c in self.campos.items() if k.startswith(cat))

    def rodar(self, tid, campo, semente=7, **extra):
        p = {"tecnica": tid, "campo": campo, "semente": semente, **extra}
        return executar("tecnica", self.img, p, vazia(*self.img.shape[:2]), base=self.img, documento=self.ctx)

    def aplicado(self, res):
        out = self.img.copy()
        caixa = out[res.y0:res.y1, res.x0:res.x1]
        caixa[:] = np.where(res.pegada[..., None], res.recorte, caixa)
        return out

    def so_perto(self, res, caixa, esq=6, dir_=6, cima=8, baixo=8):
        """Nada mudou longe do campo."""
        mudou = (self.aplicado(res) != self.img).any(2)
        self.assertTrue(mudou.any(), "a técnica não mudou nenhum pixel")
        x0, y0, x1, y1 = caixa
        fora = mudou.copy()
        fora[max(0, y0 - cima):y1 + baixo, max(0, x0 - esq):x1 + dir_] = False
        self.assertFalse(fora.any(), f"mudou fora do campo: {np.argwhere(fora)[:3]}")

    @staticmethod
    def le(img, caixa, folga_dir=0):
        """OCR da página inteira (recorte justo confunde o detector) e o texto das linhas no campo."""
        x0, y0, x1, y1 = caixa
        x1 += folga_dir
        linhas = []
        for l in ocr.ler(img):
            lx0, ly0, lx1, ly1 = l["caixa"]
            vert = min(y1, ly1) - max(y0, ly0)
            if vert > 0.5 * min(y1 - y0, ly1 - ly0) and min(x1, lx1) > max(x0, lx0):
                linhas.append(l)
        return "".join(l["texto"] for l in ocr.em_ordem_de_leitura(linhas)).replace(" ", "").upper()

    def confere_ocr(self, res, caixa, folga_dir=0):
        """O OCR relê o valor novo do rótulo (só quando ele lê o ORIGINAL certo: controle)."""
        if not TEM_OCR:
            return
        controle = self.le(self.img, caixa, folga_dir)
        if controle != res.meta["texto_antigo"].replace(" ", "").upper():
            return
        lido = self.le(self.aplicado(res), caixa, folga_dir)
        self.assertEqual(lido, res.meta["texto_novo"].replace(" ", "").upper(), res.meta["descricao"])

    def confere_rotulo(self, res, tid):
        m = res.meta
        t = catalogo.TECNICAS[tid]
        self.assertEqual((m["tecnica"], m["familia"], m["nivel"], m["granularidade"]),
                         (tid, t.familia, t.nivel, t.granularidade))
        self.assertTrue(m["descricao"])
        self.assertNotEqual(m["texto_novo"], m["texto_antigo"])


class TestCampos(Base):
    def test_ocr_em_cache_vira_campos(self):
        cats = {c["categoria"] for c in self.ctx.campos}
        for cat in ("nome", "cpf/cnpj", "endereço", "cep", "data", "consumo/leitura", "valor"):
            self.assertIn(cat, cats)

    def test_segmenta_cada_letra(self):
        c = self.campo("cpf/cnpj")
        gs = glifos.segmentar(self.img, c["caixa"], c["texto"], "Arial")
        self.assertEqual("".join(g.ch for g in gs), c["texto"].replace(" ", ""))
        self.assertTrue(all(a.x1 <= b.x0 + 1 for a, b in zip(gs, gs[1:])), "glifos fora de ordem")
        self.assertTrue(all(g.tinta.any() for g in gs))


class TestCaractere(Base):
    def test_redesenhar_troca_um_digito_so(self):
        c = self.campo("valor")
        res = self.rodar("caractere.redesenhar", c)
        self.confere_rotulo(res, "caractere.redesenhar")
        a, n = res.meta["texto_antigo"], res.meta["texto_novo"]
        self.assertEqual(len(a), len(n))
        self.assertEqual(sum(x != y for x, y in zip(a, n)), 1)
        self.assertTrue(n[res.meta["alteracoes"][0]["i"]].isdigit())
        self.so_perto(res, c["caixa"])
        self.confere_ocr(res, c["caixa"])

    def test_mesma_semente_mesmo_resultado(self):  # a prévia é igual ao que é aplicado
        c = self.campo("cep")
        r1, r2 = self.rodar("caractere.redesenhar", c, 11), self.rodar("caractere.redesenhar", c, 11)
        self.assertTrue(np.array_equal(r1.recorte, r2.recorte))
        self.assertEqual(r1.meta["texto_novo"], r2.meta["texto_novo"])

    def test_copiar_glifo_do_proprio_documento(self):
        c = self.campo("cpf/cnpj")
        res = self.rodar("caractere.copiar_glifo", c, opcoes={"colagem": "tinta"})
        self.confere_rotulo(res, "caractere.copiar_glifo")
        self.assertEqual(res.meta["origem_glifo"]["doc"], "aaaa1111")
        ox0, oy0, ox1, oy1 = res.meta["origem_glifo"]["caixa"]
        self.assertFalse(c["caixa"][0] <= ox0 and ox1 <= c["caixa"][2] and c["caixa"][1] <= oy0 and oy1 <= c["caixa"][3],
                         "a letra copiada veio do próprio campo")
        self.so_perto(res, c["caixa"])
        self.confere_ocr(res, c["caixa"])

    def test_glifo_externo_so_do_mesmo_split(self):
        c = self.campo("valor")
        for semente in range(4):
            res = self.rodar("caractere.glifo_externo", c, semente, opcoes={"colagem": "poisson"})
            self.assertEqual(res.meta["origem_glifo"]["doc"], "bbbb2222")  # cccc3333 é do teste: nunca
            self.so_perto(res, c["caixa"])

    def test_transpor(self):
        c = self.campo("cep")
        res = self.rodar("caractere.transpor", c)
        self.confere_rotulo(res, "caractere.transpor")
        self.assertEqual(sorted(res.meta["texto_novo"]), sorted(res.meta["texto_antigo"]))
        self.so_perto(res, c["caixa"])
        self.confere_ocr(res, c["caixa"])

    def test_apagar_fecha_o_vao(self):
        c = self.campo("valor")
        res = self.rodar("caractere.apagar", c, opcoes={"vao": "fechar"})
        self.confere_rotulo(res, "caractere.apagar")
        self.assertEqual(len(res.meta["texto_novo"]), len(res.meta["texto_antigo"]) - 1)
        self.assertIn(",37", res.meta["texto_novo"])  # centavos nunca são apagados
        self.so_perto(res, c["caixa"])
        self.confere_ocr(res, c["caixa"])

    def test_inserir_digito(self):
        c = self.campo("valor")
        res = self.rodar("caractere.inserir", c)
        self.confere_rotulo(res, "caractere.inserir")
        self.assertEqual(len(res.meta["texto_novo"]), len(res.meta["texto_antigo"]) + 1)
        larg = res.x1 - c["caixa"][2]
        self.so_perto(res, c["caixa"], dir_=max(6, larg + 2))
        self.confere_ocr(res, c["caixa"], folga_dir=60)

    def test_acrescentar_tracos_so_poe_tinta(self):
        c = self.campo("consumo")
        res = self.rodar("caractere.acrescentar_tracos", c, acabamento={"disfarce": "nunca"})  # só a técnica
        self.confere_rotulo(res, "caractere.acrescentar_tracos")
        antes = self.img[res.y0:res.y1, res.x0:res.x1].astype(int).sum(2)
        depois = res.recorte.astype(int).sum(2)
        self.assertLessEqual((depois - antes).max(), 40, "acrescentar traço não pode clarear o papel")
        self.so_perto(res, c["caixa"])
        self.confere_ocr(res, c["caixa"])

    def test_jitter_retoca_sem_mudar_o_texto(self):
        """Texture Jitter (OSTF): pixels mudam no trecho, o texto lido continua o mesmo."""
        c = self.campo("nome")
        for op in ("desfocar", "reamostrar", "tremido", "nitidez", "jpeg", "tamanho", "combinado"):
            res = self.rodar("trecho.jitter", c, opcoes={"operacao": op})
            m = res.meta
            self.assertEqual((m["tecnica"], m["texto_novo"]), ("trecho.jitter", m["texto_antigo"]), op)
            self.assertTrue(res.pegada.any() and m["retoques"], op)
            self.so_perto(res, c["caixa"])
            self.assertEqual(m.get("conferido_ocr"), True, op)  # continua legível como antes

    def test_disfarce_registrado_e_dentro_da_pegada(self):
        """Disfarce (FSTS): o retoque depois da edição entra no rótulo, e todo pixel que mudou está na
        pegada (senão a máscara mentiria)."""
        c = self.campo("valor")
        res = self.rodar("caractere.redesenhar", c, acabamento={"disfarce": "sempre", "ruido": "nunca"})
        self.assertTrue(any(a.startswith("disfarce:") for a in res.meta["acabamento"]), res.meta["acabamento"])
        depois = self.aplicado(res)
        mudou = (depois != self.img).any(2)
        dentro = np.zeros_like(mudou)
        dentro[res.y0:res.y1, res.x0:res.x1] = res.pegada
        self.assertFalse((mudou & ~dentro).any())

    def test_raspar_tracos_so_tira_tinta(self):
        c = self.campo("cpf/cnpj")  # o 4 vira 1 raspando
        res = self.rodar("caractere.raspar_tracos", c, acabamento={"disfarce": "nunca"})  # só a técnica
        self.confere_rotulo(res, "caractere.raspar_tracos")
        antes = self.img[res.y0:res.y1, res.x0:res.x1].astype(int).sum(2)
        depois = res.recorte.astype(int).sum(2)
        self.assertGreaterEqual((depois - antes).min(), -40, "raspar não pode escurecer")
        self.so_perto(res, c["caixa"])
        self.confere_ocr(res, c["caixa"])


class TestCampoETrecho(Base):
    def test_reescrever(self):
        c = self.campo("cep")
        res = self.rodar("campo.reescrever", c, opcoes={"alinhamento": "esquerda"})
        self.confere_rotulo(res, "campo.reescrever")
        self.so_perto(res, c["caixa"], dir_=40)
        self.confere_ocr(res, c["caixa"], folga_dir=40)

    def test_trecho_so_uma_parte(self):
        c = self.campo("data")
        res = self.rodar("trecho.trocar", c)
        self.confere_rotulo(res, "trecho.trocar")
        a, n = res.meta["texto_antigo"].split("/"), res.meta["texto_novo"].split("/")
        self.assertEqual(sum(x != y for x, y in zip(a, n)), 1, (a, n))  # dia OU mês OU ano
        self.assertTrue(plausivel("data", res.meta["texto_antigo"], res.meta["texto_novo"]))
        self.so_perto(res, c["caixa"])
        self.confere_ocr(res, c["caixa"])

    def test_acrescentar_no_fim(self):
        c = self.campo("endereço")
        res = self.rodar("campo.acrescentar", c)
        self.confere_rotulo(res, "campo.acrescentar")
        self.assertTrue(res.meta["texto_novo"].startswith(res.meta["texto_antigo"]))
        self.assertGreaterEqual(res.x0, c["caixa"][2] - 2)  # nada antes do fim do campo mudou
        self.confere_ocr(res, c["caixa"], folga_dir=res.x1 - c["caixa"][2] + 10)

    def test_copiar_outro_campo_do_documento(self):
        c = next(f for f in self.ctx.campos if f["texto"] == "10/10/2026")
        res = self.rodar("campo.copiar_interno", c, opcoes={"colagem": "tinta"})
        self.confere_rotulo(res, "campo.copiar_interno")
        self.assertEqual(res.meta["texto_novo"], "05/09/2026")
        self.so_perto(res, c["caixa"])
        self.confere_ocr(res, c["caixa"])

    def test_colagem_externa_so_do_mesmo_split(self):
        c = self.campo("nome")
        for semente in range(3):
            res = self.rodar("campo.colagem_externa", c, semente, opcoes={"colagem": "tinta"})
            self.assertEqual(res.meta["origem_campo"]["doc"], "bbbb2222")
            self.assertEqual(res.meta["texto_novo"], "PEDRO ALVES COSTA")

    def test_remover_e_corretivo_apagam_a_tinta(self):
        c = self.campo("cep")
        x0, y0, x1, y1 = c["caixa"]
        for tid in ("campo.remover", "campo.corretivo"):
            res = self.rodar(tid, c, opcoes={"parte": "campo"})
            self.confere_rotulo(res, tid)
            self.assertEqual(res.meta["texto_novo"], "")
            out = self.aplicado(res)
            self.assertLess(mascara_tinta(out[y0 - 10:y1 + 10, x0 - 10:x1 + 10]).mean(), 0.01, tid)
            self.so_perto(res, c["caixa"], esq=12, dir_=12, cima=12, baixo=12)

    def test_remover_so_a_parte_final(self):
        c = self.campo("nome")  # MARIA DOS SANTOS SILVA: sai o último sobrenome
        res = self.rodar("campo.remover", c, opcoes={"parte": "final"})
        self.assertEqual(res.meta["texto_novo"], "MARIA DOS SANTOS")
        self.assertGreater(res.x0, c["caixa"][0] + (c["caixa"][2] - c["caixa"][0]) // 2)  # só o fim mudou
        self.confere_ocr(res, c["caixa"])
        e = self.campo("endereço")  # sem complemento (APTO...): apaga o campo todo
        res = self.rodar("campo.remover", e, opcoes={"parte": "final"})
        self.assertEqual((res.meta["texto_novo"], res.meta["opcoes"]["parte"]), ("", "campo"))

    def test_amador_cobre_com_retangulo(self):
        c = self.campo("data")
        # sem o disfarce sorteado (desfoque/aguçado borram a borda do retângulo): aqui se confere o preenchimento
        res = self.rodar("campo.amador", c, opcoes={"fundo": "branco", "fonte": "Times New Roman"},
                         acabamento={"disfarce": "nunca"})
        self.confere_rotulo(res, "campo.amador")
        self.assertEqual({k: res.meta["opcoes"][k] for k in ("fundo", "fonte")}, {"fundo": "branco", "fonte": "Times New Roman"})
        self.assertIn(res.meta["opcoes"]["rasterizador"], ("freetype", "gdi", "cleartype", "aliasado"))
        self.assertTrue((res.recorte[res.pegada] == 255).all(axis=1).mean() > 0.5)  # fundo branco puro

    def test_categoria_errada_recusada(self):
        with self.assertRaises(ErroOperacao):
            self.rodar("caractere.inserir", self.campo("cep"))  # CEP tem tamanho fixo
        with self.assertRaises(ErroOperacao):
            self.rodar("codigo.qr", self.campo("cep"))  # só serve para QR


class TestIA(Base):
    @staticmethod
    def editor(rec, prompt, semente, passos):
        TestIA.prompts.append(prompt)
        return np.clip(rec.astype(int) + 40, 0, 255).astype(np.uint8)  # "edita" tudo o que recebe

    def setUp(self):
        TestIA.prompts = []

    def test_ia_caractere_descarta_saida_que_nao_le_como_pedido(self):
        c = self.campo("valor")
        p = {"tecnica": "ia.caractere", "campo": c, "semente": 3}
        with self.assertRaises(ErroOperacao):  # o editor falso não troca a letra: nada pode ser aproveitado
            executar("tecnica", self.img, p, vazia(*self.img.shape[:2]), base=self.img, documento=self.ctx,
                     editor=self.editor)
        self.assertIn("R$", TestIA.prompts[0])

    def test_ia_caractere_alinha_a_saida_e_aproveita_so_a_letra(self):
        from rotulador.ops import tecnicas
        c = self.campo("valor")  # R$ 158,37: o "1" vira "5"
        ctx = tecnicas._C(self.img, {"tecnica": "ia.caractere", "campo": c, "semente": 1}, self.img, self.ctx,
                          None, None, None)
        g = next(x for x in ctx.glifos if x.ch == "1")
        outro, _ = documento(valor="R$ 558,37")  # o que a IA "desenhou": o campo todo de novo...
        x0, y0, x1, y1 = c["caixa"]
        rx0, ry0, rx1, ry1 = x0 - 20, y0 - 20, x1 + 20, y1 + 20
        ia_rec = np.roll(outro[ry0:ry1, rx0:rx1], (1, 3), axis=(0, 1))  # ...deslocado 3 px e 1 px
        janela, patch, alfa, desvio, nota = tecnicas._letra_da_ia(ctx, g, "5", ia_rec, (rx0, ry0))
        self.assertEqual(desvio, (-3, -1))
        self.assertGreater(nota, 0.6)
        self.assertLessEqual(janela[2] - janela[0], 2 * g.largura)  # só a região de uma letra

    def test_ia_remover(self):
        c = self.campo("cep")
        p = {"tecnica": "ia.remover", "campo": c, "semente": 3}
        res = executar("tecnica", self.img, p, vazia(*self.img.shape[:2]), base=self.img, documento=self.ctx,
                       editor=self.editor)
        self.assertEqual(res.meta["texto_novo"], "")
        self.assertIn("Remove the text", TestIA.prompts[0])


@unittest.skipUnless(TEM_OCR, "sem RapidOCR: a conferência não roda")
class TestConferenciaEmCelula(unittest.TestCase):
    """A data na célula do boleto (rótulo pequeno logo acima, borda da tabela embaixo): a edição certa passa na
    conferência por OCR. A 2ª leitura era mais alta e pegava o rótulo de cima ("3/02026" num "23/02/2026" bem escrito,
    com a data 3 px abaixo do rótulo); agora ela muda a escala. A data depois de "em" na mesma linha fica de guarda (o
    caso que falhava, "n27/07/2026", é o da conta NIO: E:/rotulador/ferr/sonda_datas_pequenas.py). Na NIO as datas
    pequenas falhavam e a versão saía com o vencimento novo e a emissão velha (datas fora de ordem)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        im = Image.new("RGB", (900, 300), (252, 252, 250))
        d = ImageDraw.Draw(im)
        linhas = []

        def escrever(x, y, texto, fonte):
            d.text((x, y), texto, font=fonte, fill=(25, 25, 25))
            x0, y0, x1, y1 = d.textbbox((x, y), texto, font=fonte)
            linhas.append({"caixa": [x0 - 2, y0 - 2, x1 + 2, y1 + 2], "texto": texto, "bruto": texto, "conf": 0.99})
        escrever(60, 60, "Data Processamento", F("arial.ttf", 14))
        escrever(62, 72, "25/01/2026", F("arial.ttf", 22))  # 3 px abaixo do rótulo (como no boleto da NIO)
        d.rectangle([50, 104, 330, 106], fill=(30, 30, 30))  # a borda da célula, colada embaixo da data
        escrever(400, 80, "Emissão em 25/01/2026", F("arial.ttf", 16))
        a = np.asarray(im).astype(np.float32) + np.random.default_rng(5).normal(0, 2.0, (300, 900, 1))
        cls.img = np.clip(a, 0, 255).astype(np.uint8)
        it = item("dddd4444", "treino", cls.img)
        ocr.gravar_cache(cache_ocr(cls.tmp, it.sha_pixels), linhas)
        cls.ctx = Contexto(it, cls.img, cls.tmp, RegistroFalso({"dddd4444": (it, cls.img)}))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_datas_pequenas_passam(self):
        datas = [c for c in self.ctx.campos if c["categoria"] == "data"]
        self.assertEqual(len(datas), 2, self.ctx.campos)
        for c in datas:
            p = {"tecnica": "campo.mesma_fonte", "campo": c, "semente": 3, "texto": "23/02/2026",
                 "acabamento": {"ruido": "nunca", "disfarce": "nunca", "jpeg": 0}}
            res = executar("tecnica", self.img, p, vazia(*self.img.shape[:2]), base=self.img, documento=self.ctx)
            self.assertIs(res.meta.get("conferido_ocr"), True, c["texto"])  # conferida (o controle lê o original)


class TestCorrecoesDaVerificacao(unittest.TestCase):
    """Problemas achados por tests_ui/verifica_tecnicas.py em documentos variados."""

    def test_texto_comprimido_nao_e_cortado(self):
        from rotulador.ops import texto
        p = {"texto": "JULIANA VITORIA FERREIRA CARVALHO", "fonte": "Courier New", "tamanho": 40, "x": 100.0, "y": 200.0}
        larg = {l: texto.renderizar({**p, "largura": l}, 1000, 3000)[3] - texto.renderizar({**p, "largura": l}, 1000, 3000)[2]
                for l in (100, 70)}
        self.assertAlmostEqual(larg[70] / larg[100], 0.7, delta=0.02)

    def test_rotulo_de_varias_palavras_fora_da_caixa_do_valor(self):
        from rotulador import campos
        im = Image.new("RGB", (900, 80), (250, 250, 250))
        ImageDraw.Draw(im).text((20, 20), "DATA DA LEITURA: 22/08/2026", font=F("times.ttf", 34), fill=(10, 10, 10))
        img = np.asarray(im).copy()
        x0, y0, x1, y1 = ImageDraw.Draw(im).textbbox((20, 20), "DATA DA LEITURA: 22/08/2026", font=F("times.ttf", 34))
        lista = campos.detectar(img, [{"caixa": [x0 - 3, y0 - 3, x1 + 3, y1 + 3], "texto": "DATA DA LEITURA: 22/08/2026",
                                       "bruto": "DATADALEITURA:22/08/2026", "conf": 0.9}])
        data = next(c for c in lista if c["categoria"] == "data")
        x_data = ImageDraw.Draw(im).textbbox((20, 20), "DATA DA LEITURA: ", font=F("times.ttf", 34))[2]
        self.assertGreater(data["caixa"][0], x_data - 12)  # a caixa começa no "22", não no "DA"

    def test_cnpj_dentro_de_linha_digitavel_cortada(self):
        from rotulador import campos
        self.assertEqual(campos.categoria_por_texto("82810000001-178453824444-04444444"), "linha digitável")
        self.assertEqual(campos.categoria_por_texto("00.000.000/0001-91"), "cpf/cnpj")
        self.assertEqual(campos.categoria_por_texto("RS5.48"), "valor")

    def test_conferencia_tolera_confusoes_do_ocr_mas_nao_digito_trocado(self):
        from rotulador.ops.tecnicas import lido_igual
        self.assertTrue(lido_igual("RS 7,96", "R$ 7,96"))
        self.assertTrue(lido_igual("NOVI2025", "NOV/2025"))
        self.assertTrue(lido_igual("0LIVEIRA", "OLIVEIRA"))
        self.assertFalse(lido_igual("R$ 151,37", "R$ 158,37"))
        self.assertFalse(lido_igual("8S318436", "85318-436"))

    def test_tom_da_ia_igualado_pela_borda(self):
        from rotulador.ops.ia import _igualar_tom
        orig = np.full((60, 200, 3), 240, np.uint8)
        orig[25:35, 40:160] = 20
        saida = np.clip(orig.astype(int) + 12, 0, 255).astype(np.uint8)
        corrigida, tom = _igualar_tom(orig, saida, np.ones((60, 200), np.float32))
        self.assertEqual(tom, [-12.0, -12.0, -12.0])
        self.assertTrue((corrigida[:20] == 240).all())

    def test_particula_combina_com_o_sobrenome(self):
        import re
        nomes = [geradores_nome() for _ in range(2000)]
        self.assertFalse([n for n in nomes if re.search(r"\bDOS (SILVA|SOUZA|COSTA)\b|\bDA (SANTOS|SOUZA)\b", n)])


def geradores_nome():
    from rotulador.ops import geradores
    return geradores.gerar("nome")["texto"]


class TestPlausivel(unittest.TestCase):
    def test_regras(self):
        self.assertFalse(plausivel("data", "10/10/2026", "31/02/2026"))
        self.assertFalse(plausivel("data", "10/10/2026", "10/10/4026"))
        self.assertTrue(plausivel("data", "10/10/2026", "18/10/2026"))
        self.assertFalse(plausivel("mês de referência", "SET/2026", "SET/7026"))
        self.assertFalse(plausivel("valor", "R$ 151,37", "R$ 051,37"))
        self.assertTrue(plausivel("valor", "R$ 0,37", "R$ 0,97"))


class TestGeradorERotulos(Base):
    def receita(self, **kw):
        d = {"tecnicas": {"caractere.redesenhar": 1, "trecho.trocar": 1, "campo.corretivo": 1, "campo.amador": 1},
             "categorias": {"valor": 1, "cep": 1, "data": 1, "cpf/cnpj": 1}, "edicoes": [3, 3], **kw}
        return Receita(d, IDS)

    def sessao(self, nome="s"):
        return Sessao(self.it, self.img.copy(), self.tmp / nome, IDS)

    def test_equilibrada_cobre_todas_as_tecnicas(self):
        r = self.receita(distribuicao="equilibrada")
        plano = Plano(r, random.Random(1))
        for i in range(4):
            aplicar_receita(self.sessao(f"eq{i}"), self.ctx, r, plano, random.Random(i))
        usos = [plano.por_tecnica[t] for t in r.tecnicas]
        self.assertTrue(min(usos) >= 1 and max(usos) - min(usos) <= 2, plano.por_tecnica)

    def test_salvar_grava_mascara_de_tecnicas_e_catalogo(self):
        s = self.sessao("salvar")
        r = self.receita()
        feitos = aplicar_receita(s, self.ctx, r, Plano(r, random.Random(5)), random.Random(5))
        self.assertEqual(len(feitos), 3)
        salvo = exportar.salvar(s, self.saida, "png", 95, "teste", extra={"gerador": {"lote": "x"}})
        raiz = self.saida / "treino"
        anot = json.loads((raiz / salvo["arquivos"]["anotacao"]).read_text(encoding="utf-8"))
        self.assertIn("gerador", anot)
        self.assertEqual(len(anot["edicoes"]), 3)
        nums = {e["tecnica_num"] for e in anot["edicoes"]}
        m = np.asarray(Image.open(raiz / salvo["arquivos"]["mascara_tecnicas"]))
        self.assertEqual(set(np.unique(m)) - {0}, nums)
        for e in anot["edicoes"]:  # cada edição tem os seus pixels na máscara, dentro da sua caixa
            x0, y0, x1, y1 = e["bbox"]
            self.assertEqual(int((m[y0:y1, x0:x1] == e["tecnica_num"]).sum()) > 0, True)
            self.assertEqual(int((m == e["tecnica_num"]).sum()), e["pixels"])
        with open(raiz / "catalogo.csv", encoding="utf-8") as f:
            linhas = list(csv.DictReader(f))
        self.assertEqual(len(linhas), 3)
        self.assertEqual({l["tecnica"] for l in linhas}, {e["tecnica"] for e in anot["edicoes"]})
        self.assertTrue((self.saida / "catalogo.json").is_file())
        # índices refeitos dos JSON dão o mesmo resultado; excluir apaga arquivos e linhas
        exportar.reconstruir_indices(self.saida, "treino")
        with open(raiz / "catalogo.csv", encoding="utf-8") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 3)
        nome = Path(salvo["arquivos"]["anotacao"]).stem
        exportar.excluir_versao(self.saida, "treino", nome)
        self.assertFalse((raiz / salvo["arquivos"]["editada"]).exists())
        self.assertFalse((raiz / salvo["arquivos"]["mascara_tecnicas"]).exists())
        self.assertFalse(list((raiz / "originais").glob("*")))  # sem versões, o original sai também
        with open(raiz / "catalogo.csv", encoding="utf-8") as f:
            self.assertEqual(list(csv.DictReader(f)), [])

    def test_mapa_segue_a_camada_de_cima(self):
        s = self.sessao("camadas")
        c = self.campo("cep")
        r1 = executar("tecnica", s.atual, {"tecnica": "campo.corretivo", "campo": c, "semente": 1,
                                           "opcoes": {"fundo": "papel"}}, vazia(s.H, s.W), base=s.base, documento=self.ctx)
        s.aplicar("tecnica", "cep", "", {"tecnica": "campo.corretivo"}, r1)
        cam_cep = s.ativa
        r2 = executar("tecnica", s.atual, {"tecnica": "campo.amador", "campo": c, "semente": 1, "texto": "99999-999"},
                      vazia(s.H, s.W), base=s.base, documento=self.ctx)
        s.aplicar("tecnica", "data", "", {"tecnica": "campo.amador"}, r2)
        mapa, ents = s.mapa_edicoes()
        tec = exportar.mascara_tecnicas(mapa, ents)
        x0, y0, x1, y1 = c["caixa"]
        centro = tec[(y0 + y1) // 2, (x0 + x1) // 2]
        self.assertEqual(centro, catalogo.TECNICAS["campo.amador"].num)  # a camada de cima vale
        s.acao_camada("ordem", camada=cam_cep, posicao=len(s.ordem) - 1)  # cep vai para o topo
        mapa, ents = s.mapa_edicoes()
        tec = exportar.mascara_tecnicas(mapa, ents)
        self.assertEqual(tec[(y0 + y1) // 2, (x0 + x1) // 2], catalogo.TECNICAS["campo.corretivo"].num)


if __name__ == "__main__":
    unittest.main()
