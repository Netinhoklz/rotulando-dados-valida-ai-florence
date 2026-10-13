"""Melhorias do modo exato e da detecção de campos (auditoria de 03/10/2026):

1. subconjuntos: cada versão edita um subconjunto das classes marcadas (às vezes só o nome, às vezes só o
   endereço), sem repetir o subconjunto entre as versões do mesmo documento; a anotação diz quais;
2. ocorrências: o mesmo dado em OUTRAS linhas (sem categoria, ou dentro de uma linha maior) também é trocado, e
   o que sobrou com o valor antigo vai para a anotação (``ocorrencias_antigas``);
3. classificação: "rótulo: valor" numérico vira campo (só o valor), endereço com CEP, bloco do emissor não é o do
   cliente, bloco do cliente no alto da página, datas coladas;
4. marcas benignas podem ficar por cima da edição (iguais no par);
5. edição sem medida válida do perito fica sem nota (None), não com 0.

Tudo com documentos sintéticos (nenhum dado real)."""
import json
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import campos, gerador, marcas
from rotulador.registro import Item
from rotulador.sessao import ErroOperacao, Sessao

try:
    import rapidocr_onnxruntime  # noqa: F401
    TEM_OCR = True
except ImportError:
    TEM_OCR = False

IDS = {"nome": 1, "cpf/cnpj": 2, "endereço": 3, "bairro": 4, "cep": 5, "cidade/uf": 6, "data": 7,
       "mês de referência": 8, "valor": 9, "consumo/leitura": 10, "codigo barras": 11, "linha digitável": 12,
       "identificador documento": 13, "emissor/logotipo": 14, "qr code/pix": 15, "outro": 16}
RAIZ = Path(__file__).resolve().parents[1]


def F(nome, tam):
    return ImageFont.truetype(f"C:/Windows/Fonts/{nome}", tam)


def pagina(linhas, H=1200, W=1240):
    """(imagem, linhas do OCR com o texto exato) de uma página com ``linhas`` = [(x, y, texto, fonte)] ou
    [(x, y, texto, fonte, lido)]: ``lido`` é o que o OCR leu (ex.: sem o espaço que a tinta tem)."""
    im = Image.new("RGB", (W, H), (252, 252, 250))
    d = ImageDraw.Draw(im)
    saida = []
    for x, y, texto, fonte, *lido in linhas:
        d.text((x, y), texto, font=fonte, fill=(20, 20, 20))
        x0, y0, x1, y1 = d.textbbox((x, y), texto, font=fonte)
        t = lido[0] if lido else texto
        saida.append({"caixa": [x0 - 3, y0 - 4, x1 + 3, y1 + 4], "texto": t, "bruto": t, "conf": 0.99})
    a = np.asarray(im).astype(np.float32) + np.random.default_rng(1).normal(0, 2.0, (H, W, 1))
    return np.clip(a, 0, 255).astype(np.uint8), saida


def por_texto(lista):
    return {c["texto"]: c for c in lista}


def _c(i, cat, txt, caixa, rotulo=""):
    return {"id": f"t{i}", "categoria": cat, "texto": txt, "caixa": caixa, "caixa_ocr": caixa, "rotulo": rotulo,
            "conf": 0.9, "tipo": "texto"}


# ============================================================================ 3. classificação
class TestClassificacao(unittest.TestCase):
    def test_rotulo_com_valor_numerico_vira_campo_so_do_valor(self):
        f = F("times.ttf", 34)
        # como em times.jpg: a tinta tem o espaço depois dos dois-pontos (menos no CPF), o OCR leu tudo colado
        img, linhas = pagina([(120, 300, "CPF:905.214.638-71", f), (120, 380, "CEP: 82318-436", f, "CEP:82318-436"),
                              (120, 460, "MATRICULA: 38215634", f, "MATRICULA:38215634"),
                              (120, 540, "CONSUMO: 205m3", f, "CONSUMO:205m3"),
                              (120, 620, "MES REFERENCIA: AGO/2026", f, "MESREFERENCIA:AGO/2026")])
        lista = por_texto(campos.detectar(img, linhas))
        self.assertEqual(lista.get("905.214.638-71", {}).get("categoria"), "cpf/cnpj", list(lista))
        self.assertEqual(lista.get("82318-436", {}).get("categoria"), "cep", list(lista))
        self.assertEqual(lista.get("38215634", {}).get("categoria"), "identificador documento", list(lista))
        self.assertEqual(next(c["categoria"] for t, c in lista.items() if t.startswith("205")), "consumo/leitura")
        ago = lista.get("AGO/2026")
        self.assertEqual(ago["categoria"], "mês de referência")
        self.assertNotIn("MATRICULA", ago["rotulo"].upper())  # o rótulo dele é o dele, não o valor de cima
        # a caixa do campo cobre só o valor
        fim_rotulo = 120 + f.getlength("CPF:")
        self.assertGreaterEqual(lista["905.214.638-71"]["caixa"][0], fim_rotulo - 2)
        fim_rotulo = 120 + f.getlength("MATRICULA:")
        self.assertGreaterEqual(lista["38215634"]["caixa"][0], fim_rotulo - 2)

    def test_rotulo_sozinho_continua_rotulo(self):
        f = F("arial.ttf", 30)
        img, linhas = pagina([(60, 300, "CPF", f), (330, 300, "123.456.789-09", f), (60, 380, "TOTAL A PAGAR", f),
                              (600, 380, "R$ 151,37", f)])
        lista = por_texto(campos.detectar(img, linhas))
        self.assertNotIn("CPF", lista)
        self.assertNotIn("TOTAL A PAGAR", lista)
        self.assertEqual(lista["123.456.789-09"]["rotulo"], "CPF")

    def test_endereco_com_cep_e_subcampo_do_cep(self):
        t = "RUA DAS FLORES, 123 - CENTRO - FORTALEZA/CE - CEP 60000-000"
        self.assertEqual(campos.categoria_por_texto(t), "endereço")
        self.assertEqual(campos.categoria_por_texto("60000-000 - FORTALEZA - CE"), "cep")  # sem rua: continua CEP
        f = F("arial.ttf", 26)
        img, linhas = pagina([(80, 400, t, f)])
        lista = campos.detectar(img, linhas)
        end = next(c for c in lista if c["categoria"] == "endereço")
        cep = next(c for c in lista if c["categoria"] == "cep")
        self.assertEqual(end["texto"], t)
        self.assertEqual(cep["texto"], "60000-000")
        self.assertEqual(cep.get("parte_de"), end["id"])
        inicio_cep = 80 + f.getlength(t[:t.index("60000")])
        self.assertTrue(inicio_cep - 4 <= cep["caixa"][0] and cep["caixa"][2] <= end["caixa"][2] + 1, cep["caixa"])

    def test_bloco_do_emissor_nao_vira_bloco_do_cliente(self):
        # rodapé com a razão social (S.A.), o endereço e o CNPJ do emissor, no meio da página
        lista = [_c(0, None, "COMPANHIA DE AGUA FICTICIA S.A.", [100, 1800, 600, 1820]),
                 _c(1, "endereço", "RUA PADRE ANTONIO, 150 - CENTRO", [100, 1826, 560, 1846]),
                 _c(2, None, "FORTALEZA - CE", [100, 1852, 300, 1872]),
                 _c(3, "cpf/cnpj", "07.047.251/0001-70", [160, 1878, 400, 1898], "CNPJ")]
        campos._bloco_de_enderecamento(lista, 3000)
        self.assertEqual([c["categoria"] for c in lista], [None, "emissor/logotipo", None, "cpf/cnpj"])
        # sem marca de empresa, mas com o CNPJ logo abaixo
        lista = [_c(0, None, "AGUAS DO NORTE", [100, 1800, 400, 1820]), _c(1, "endereço", "AV BRASIL, 100", [100, 1826, 380, 1846]),
                 _c(2, None, "BELEM PA", [100, 1852, 260, 1872]), _c(3, "cpf/cnpj", "12.345.678/0001-90", [100, 1880, 380, 1900], "CNPJ")]
        campos._bloco_de_enderecamento(lista, 3000)
        self.assertEqual([c["categoria"] for c in lista[:3]], [None, "emissor/logotipo", None])
        # na faixa do rodapé, sem mais nada
        lista = [_c(0, None, "JOSE DA SILVA LIMA", [100, 2900, 400, 2920]), _c(1, "endereço", "RUA DAS FLORES, 12", [100, 2926, 380, 2946]),
                 _c(2, None, "CENTRO", [100, 2952, 220, 2972])]
        campos._bloco_de_enderecamento(lista, 3000)
        self.assertEqual([c["categoria"] for c in lista], [None, "emissor/logotipo", None])
        # o envelope do cliente continua sendo do cliente
        lista = [_c(0, None, "M R C FILHO FOTOS", [416, 424, 625, 442]),
                 _c(1, "endereço", "RUA 47 CJ PREFEITO JOSE WALTER 312", [417, 449, 857, 468]),
                 _c(2, None, "PREFEITO JOSE WALTER", [417, 476, 694, 494])]
        campos._bloco_de_enderecamento(lista, 2337)
        self.assertEqual([c["categoria"] for c in lista], ["nome", "endereço", "bairro"])

    def test_bloco_do_cliente_no_alto_da_pagina(self):
        f = F("arial.ttf", 16)
        img, linhas = pagina([(60, 15, "COMPANHIA FICTICIA", F("arialbd.ttf", 30)), (60, 78, "MARIA DOS SANTOS", f),
                              (60, 98, "RUA DAS FLORES, 105", f), (60, 118, "JARDIM AMERICA", f)], H=1200)
        lista = por_texto(campos.detectar(img, linhas))
        self.assertEqual(lista["COMPANHIA FICTICIA"]["categoria"], "emissor/logotipo")
        self.assertEqual(lista["MARIA DOS SANTOS"]["categoria"], "nome")
        self.assertEqual(lista["RUA DAS FLORES, 105"]["categoria"], "endereço")
        self.assertEqual(lista["JARDIM AMERICA"]["categoria"], "bairro")

    def test_data_colada(self):
        self.assertEqual(campos.categoria_por_texto("Emissaoem25/01/2026"), "data")
        self.assertEqual(campos.categoria_por_texto("Periodode23/2/2025a23/01/2026"), "data")
        self.assertEqual(campos.categoria_por_texto("JAN/2026"), "mês de referência")
        self.assertEqual(campos.categoria_por_texto("09/2026"), "mês de referência")
        self.assertEqual(campos.categoria_por_texto("10/10/2026"), "data")
        f = F("arial.ttf", 24)
        img, linhas = pagina([(700, 500, "Emissaoem25/01/2026", f)])
        c = next(c for c in campos.detectar(img, linhas) if c["categoria"] == "data")
        self.assertEqual(c["texto"], "25/01/2026")  # só a data: o resto da linha não é reescrito
        self.assertGreaterEqual(c["caixa"][0], 700 + f.getlength("Emissaoem") - 3)

    def test_formatos_de_data_e_mes(self):
        """Os formatos que o gerador já escreve (geradores.formatar_data/formatar_mes) agora também são achados; na
        bateria de campos de 12/10/2026 eles eram 0/6 cada."""
        for t in ("10.10.2026", "10-10-2026", "2026-10-10", "05 OUT 2026", "05/OUT/2026", "10 de outubro de 2026",
                  "5 de março de 2026", "1/2/2026"):
            self.assertEqual(campos.categoria_por_texto(t), "data", t)
        for t in ("Set 2026", "AGO 2026", "JUL/26", "Setembro de 2026", "SETEMBRO/2026"):
            self.assertEqual(campos.categoria_por_texto(t), "mês de referência", t)
        # armadilhas: norma, rua com nome de data, data impossível, versão, telefone
        for t, cat in (("Conforme Resolução ANEEL nº 03/2025, a tarifa foi reajustada.", None), ("Lei 12/2024", None),
                       ("RUA 7 DE SETEMBRO, 1500", "endereço"), ("AV. 13 DE MAIO 2020", "endereço"),
                       ("32/13/2026", None), ("Versão 1.2.2026", None), ("(85) 3333-4444", None), ("DEZ 10", None)):
            self.assertEqual(campos.categoria_por_texto(t), cat, t)

    def test_duas_datas_na_linha(self):
        f = F("arial.ttf", 24)
        t = "Leitura anterior: 29/08/2026   Leitura atual: 28/09/2026"
        img, linhas = pagina([(80, 500, t, f), (80, 600, "Período de consumo: 29/08/2026 a 28/09/2026", f)])
        datas = [c for c in campos.detectar(img, linhas) if c["categoria"] == "data"]
        self.assertEqual(sorted(c["texto"] for c in datas), ["28/09/2026"] * 2 + ["29/08/2026"] * 2)
        self.assertEqual(len({c["id"] for c in datas}), 4)
        atual = next(c for c in datas if c["caixa"][1] < 560 and c["texto"] == "28/09/2026")
        self.assertIn("atual", atual["rotulo"].lower())  # o rótulo da 2ª data é o dela, não o da 1ª
        self.assertGreaterEqual(atual["caixa"][0], 80 + f.getlength(t[:t.index("28/09")]) - 4)
        primeira = next(c for c in datas if c["caixa"][1] < 560 and c["texto"] == "29/08/2026")
        self.assertLess(primeira["caixa"][2], atual["caixa"][0])  # caixas separadas: cada data só com a tinta dela

    def test_qr_falso_em_texto(self):
        quadrado = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], float)
        alto = np.array([[0, 0], [570, 0], [570, 931], [0, 931]], float)
        self.assertTrue(campos._quadrado(quadrado))
        self.assertFalse(campos._quadrado(alto))  # o "QR" de 570 x 931 que o OpenCV via nas letras do envelope


# ============================================================================ 1. subconjuntos
class _CtxFalso:
    def __init__(self, lista, linhas=(), img=None):
        self.campos, self.linhas, self.img = lista, list(linhas), img
        self.split, self.doc = "treino", "x"


class TestSubconjuntos(unittest.TestCase):
    def test_distribuicao_com_duas_classes(self):
        rng = random.Random(7)
        cont = {}
        for _ in range(4000):
            s = tuple(gerador._subconjunto(["nome", "endereço"], rng, None))
            cont[s] = cont.get(s, 0) + 1
        self.assertEqual(set(cont), {("nome", "endereço"), ("nome",), ("endereço",)})
        self.assertAlmostEqual(cont[("nome", "endereço")] / 4000, 0.5, delta=0.04)
        self.assertAlmostEqual(cont[("nome",)] / 4000, 0.25, delta=0.04)

    def test_distribuicao_com_tres_classes(self):
        rng = random.Random(8)
        cls = ["nome", "endereço", "cep"]
        subs = [tuple(gerador._subconjunto(cls, rng, None)) for _ in range(6000)]
        self.assertAlmostEqual(subs.count(tuple(cls)) / 6000, 0.4, delta=0.04)
        outros = {s for s in subs if s != tuple(cls)}
        self.assertEqual(len(outros), 6)  # todo subconjunto não vazio aparece
        self.assertTrue(all(s for s in subs))
        self.assertEqual(gerador._subconjunto(["nome"], rng, None), ["nome"])

    def test_versoes_do_mesmo_documento_nao_repetem(self):
        for sem in range(30):
            rng, vistos = random.Random(sem), []
            subs = [frozenset(gerador._subconjunto(["nome", "endereço"], rng, vistos)) for _ in range(3)]
            self.assertEqual(len(set(subs)), 3)  # as 3 combinações possíveis, uma vez cada
            quarta = frozenset(gerador._subconjunto(["nome", "endereço"], rng, vistos))  # recomeça o ciclo
            self.assertTrue(quarta)

    def test_receita(self):
        r = gerador.Receita(gerador.receita_limpa(IDS), IDS)
        self.assertTrue(r.subconjuntos)
        self.assertTrue(r.resumo()["subconjuntos"])
        antiga = gerador.Receita({"exato": True, "tecnicas": {"campo.mesma_fonte": 1}, "categorias": {"nome": 1, "endereço": 1}}, IDS)
        self.assertFalse(antiga.subconjuntos)

    def _aplicar(self, rec, sem, vistos=None):
        lista = [_c(0, "nome", "MARIA DOS SANTOS", [10, 10, 200, 30]), _c(1, "endereço", "RUA DAS FLORES, 105", [10, 40, 220, 60]),
                 _c(2, "nome", "Maria dos Santos", [10, 300, 200, 320]), _c(3, "data", "10/10/2026", [10, 400, 120, 420])]
        r = gerador.Receita(rec, IDS)
        visto = {}
        info = {}

        def falso(sessao, ctx, cs, r_, plano, rng, editor=None, ao_falhar=None, antigas=None, humano_hab=None):
            visto["cats"] = sorted({c["categoria"] for c in cs})
            return [{"categoria": c["categoria"]} for c in cs]
        with mock.patch.object(gerador, "editar_campos", side_effect=falso):
            sess = mock.Mock(ids_categoria=IDS, base=np.zeros((500, 300, 3), np.uint8))
            gerador.aplicar_receita(sess, _CtxFalso(lista), r, None, random.Random(sem), info=info, ja_sorteados=vistos)
        return visto["cats"], info

    def test_aplicar_receita(self):
        antiga = {"exato": True, "tecnicas": {"campo.mesma_fonte": 1}, "categorias": {"nome": 1, "endereço": 1}}
        for sem in range(10):  # receita sem a chave: sempre as duas classes
            self.assertEqual(self._aplicar(antiga, sem)[0], ["endereço", "nome"])
        vistas = set()
        for sem in range(40):
            cats, info = self._aplicar(gerador.receita_limpa(IDS), sem)
            self.assertEqual(sorted(info["classes"]), cats)
            vistas.add(tuple(cats))
        self.assertEqual(vistas, {("endereço", "nome"), ("nome",), ("endereço",)})
        vistos = []  # o mesmo documento: três versões, três subconjuntos diferentes
        seq = [tuple(self._aplicar(gerador.receita_limpa(IDS), 100 + v, vistos)[0]) for v in range(3)]
        self.assertEqual(len(set(seq)), 3, seq)

    def test_interface(self):
        html = (RAIZ / "templates" / "gerador.html").read_text(encoding="utf-8")
        js = (RAIZ / "static" / "gerador.js").read_text(encoding="utf-8")
        self.assertTrue('id="exato-subconjuntos"' in html and "às vezes só o nome" in html)
        self.assertTrue("subconjuntos:" in js and "exato_subconjuntos" in js)


# ============================================================================ 2. ocorrências
def doc_repetido():
    """O nome do cliente em 3 lugares: com rótulo, solto (sem categoria) e dentro de uma linha com o CPF."""
    f, rot = F("arial.ttf", 28), F("arialbd.ttf", 20)
    return pagina([(60, 200, "NOME", rot), (300, 196, "MARIA DA SILVA PRADO", f),
                   (60, 300, "RUA DAS FLORES, 105", f),
                   (700, 500, "Maria da Silva Prado", f),
                   (60, 700, "MARIA DA SILVA PRADO CPF 123.456.789-09", f)], H=1000)


class TestOcorrencias(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.img, self.linhas = doc_repetido()
        self.item = Item(doc="rep", split="treino", caminho=self.tmp / "x.png", rel="x.png", pagina=0, paginas=1,
                         sha_arquivo="f", sha_pixels="r" * 40, altura=self.img.shape[0], largura=self.img.shape[1])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self):
        from rotulador.contexto import Contexto
        ctx = Contexto(self.item, self.img, self.tmp / "saida")
        ctx._linhas = self.linhas
        ctx._campos = campos.detectar(self.img, self.linhas)
        return ctx

    def test_acha_a_linha_solta_e_o_trecho(self):
        ctx = self._ctx()
        nome = next(c for c in ctx.campos if c["categoria"] == "nome")
        self.assertEqual(nome["texto"], "MARIA DA SILVA PRADO")
        achadas = gerador._ocorrencias(ctx, [nome], self.img)
        textos = sorted(o["texto"] for o in achadas)
        self.assertEqual(textos, ["MARIA DA SILVA PRADO", "Maria da Silva Prado"], achadas)
        self.assertTrue(all(o["categoria"] == "nome" and o["ocorrencia"] for o in achadas))
        # o nome da linha "NOME ... CPF ..." é achado como trecho da linha, ou (com o gabarito) já vem separado pelo
        # detector como campo próprio (parte_de a linha do CPF); nos dois casos a caixa para antes do "CPF"
        trecho = next((o for o in achadas if o.get("linha")), None) or next(
            c for c in ctx.campos if c["categoria"] == "nome" and c.get("parte_de"))
        f = F("arial.ttf", 28)
        self.assertLess(trecho["caixa"][2], 60 + f.getlength("MARIA DA SILVA PRADO C"))  # para antes do "CPF"
        self.assertGreaterEqual(trecho["caixa"][2], 60 + f.getlength("MARIA DA SILVA PRAD"))

    @unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
    def test_edita_todas_e_registra_as_que_sobraram(self):
        ctx = self._ctx()
        r = gerador.Receita(gerador.receita_limpa(IDS, ("nome",)), IDS)
        s = Sessao(self.item, self.img.copy(), self.tmp / "rasc", IDS)
        info = {}
        feitos = gerador.aplicar_receita(s, ctx, r, None, random.Random(4), info=info)
        nomes = [f for f in feitos if f["categoria"] == "nome"]
        self.assertEqual(len(nomes), 3, [(f["antigo"], f["novo"]) for f in feitos])
        self.assertEqual(len({f["novo"].upper() for f in nomes}), 1)  # o mesmo nome novo nos três lugares
        self.assertEqual(info["ocorrencias_antigas"], [])
        alt, _reg, _cls = s.mascaras()
        f = F("arial.ttf", 28)
        x_cpf = int(60 + f.getlength("MARIA DA SILVA PRADO "))
        self.assertFalse(alt[690:740, x_cpf + 4:].any())  # o "CPF 123..." da linha mista ficou intacto
        # uma ocorrência que falha (ex.: a conferência por OCR recusou) fica na anotação com o motivo
        real = gerador._executar_edicao

        def falha_na_solta(sessao, ctx_, p, rng, r_, editor):
            if p["campo"]["texto"] == "Maria da Silva Prado":
                raise ErroOperacao("conferência por OCR: descartado")
            return real(sessao, ctx_, p, rng, r_, editor)
        s2 = Sessao(self.item, self.img.copy(), self.tmp / "rasc2", IDS)
        info = {}
        with mock.patch.object(gerador, "_executar_edicao", side_effect=falha_na_solta):
            gerador.aplicar_receita(s2, ctx, r, None, random.Random(4), info=info)
        self.assertEqual([o["texto"] for o in info["ocorrencias_antigas"]], ["Maria da Silva Prado"])
        o = info["ocorrencias_antigas"][0]
        self.assertIn("conferência", o["motivo"])
        self.assertEqual((o["categoria"], len(o["caixa"])), ("nome", 4))

    def test_dentro_de_outra_edicao_que_manteve_o_valor(self):
        """Ocorrência coberta por outra edição: só é "antiga" se o texto novo daquela edição ainda tem o valor."""
        pend = {"categoria": "cep", "texto": "60000-000", "caixa": [500, 10, 600, 30], "tipo": "texto"}
        r = gerador.Receita(gerador.receita_limpa(IDS, ("endereço", "cep")), IDS)
        cep = {"id": "t9", "categoria": "cep", "texto": "60000-000", "caixa": [10, 100, 120, 120], "tipo": "texto"}
        end = {"id": "t1", "categoria": "endereço", "texto": "RUA X, 1 - CEP 60000-000", "caixa": [10, 10, 600, 30], "tipo": "texto"}
        for novo_end, esperado in (("RUA Y, 2 - CEP 60000-000", 1), ("RUA Y, 2 - CEP 61111-111", 0)):
            def falso(sessao, ctx_, p, rng, r_, editor, novo_end=novo_end):
                novo = {"endereço": novo_end, "cep": "61111-111"}[p["campo"]["categoria"]]
                res = mock.Mock(meta={"texto_novo": novo, "texto_antigo": p["campo"]["texto"]}, x0=0, y0=0, x1=1, y1=1)
                return res, mock.Mock(info={"categoria": p["campo"]["categoria"]})
            antigas = []
            with mock.patch.object(gerador, "_executar_edicao", side_effect=falso), \
                    mock.patch.object(gerador, "_tecnica_do_campo", return_value=mock.Mock(id="campo.mesma_fonte")):
                gerador.editar_campos(mock.Mock(), _CtxFalso([]), [end, cep, {**pend, "id": "s1", "ocorrencia": True}], r,
                                      None, random.Random(1), antigas=antigas)
            self.assertEqual(len(antigas), esperado, (novo_end, antigas))


# ============================================================================ 1 e 2 no lote (anotação)
@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestLoteExato(unittest.TestCase):
    def setUp(self):
        from app import carregar_categorias, criar_app
        from rotulador.registro import Registro
        from tests.test_exato import boleto
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        (self.dados / "treino").mkdir(parents=True)
        Image.fromarray(boleto()[0]).save(self.dados / "treino" / "boleto.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_classes_da_versao_na_anotacao(self):
        rec = gerador.receita_limpa(IDS, ("nome", "endereço"))
        rec.update({"documentos": {"splits": ["treino"]}, "variacoes": 3})
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(600):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertEqual(st["feitas"], 3, st)
        anots = [json.loads(a.read_text(encoding="utf-8")) for a in (self.saida / "treino" / "anotacoes").glob("*.json")]
        subs = set()
        for a in anots:
            cls = a["gerador"]["classes"]
            self.assertTrue(cls and set(cls) <= {"nome", "endereço"}, cls)
            self.assertTrue({e["categoria"] for e in a["edicoes"]} <= set(cls), (cls, a["edicoes"]))
            self.assertIsInstance(a["ocorrencias_antigas"], list)
            self.assertTrue(a["gerador"]["receita"]["subconjuntos"])
            subs.add(frozenset(cls))
        self.assertEqual(len(subs), 3)  # três versões do mesmo documento, três subconjuntos


# ============================================================================ 4. marcas sobre a edição
def _par():
    from tests.util import conta
    orig = conta(1, 600, 500)
    ed = orig.copy()
    ed[196:218, 14:220] = np.clip(ed[196:218, 14:220].astype(int) - 90, 0, 255).astype(np.uint8)  # a "edição"
    regiao = np.zeros(orig.shape[:2], bool)
    regiao[196:218, 14:220] = True
    return orig, ed, regiao


class TestMarcasSobreEdicao(unittest.TestCase):
    def test_normalizar_e_sortear(self):
        self.assertEqual(marcas.normalizar({"prob": 0.5}), {"prob": 0.5})  # receita antiga: igual a antes
        self.assertEqual(marcas.normalizar({"prob": 0.5, "sobre_edicao": 0.2}), {"prob": 0.5, "sobre_edicao": 0.2})
        for sem in range(30):
            th = marcas.sortear(random.Random(sem), {"prob": 1.0})
            self.assertNotIn("sobre_edicao", th)
        n = sum(bool(marcas.sortear(random.Random(sem), {"prob": 1.0, "sobre_edicao": 0.2}).get("sobre_edicao"))
                for sem in range(1000))
        self.assertAlmostEqual(n / 1000, 0.2, delta=0.04)
        # o sorteio novo vem DEPOIS dos antigos: o resto do theta é o mesmo de antes
        a = marcas.sortear(random.Random(3), {"prob": 1.0})
        b = marcas.sortear(random.Random(3), {"prob": 1.0, "sobre_edicao": 1.0})
        self.assertEqual({k: v for k, v in b.items() if k != "sobre_edicao"}, a)

    def test_marca_por_cima_da_edicao_igual_no_par(self):
        for tipo in ("carimbo", "manuscrito", "marca_texto", "autenticacao", "rubrica"):
            for sem in range(3):
                orig, ed, regiao = _par()
                th = {"semente": sem, "tipos": [tipo], "alvos": [], "sobre_edicao": True}
                (o2, e2), info = marcas.aplicar([orig, ed], regiao, th)
                self.assertEqual(len(info), 1, (tipo, sem))
                self.assertTrue(info[0].get("sobre_edicao"), info)
                x0, y0, x1, y1 = info[0]["caixa"]
                self.assertTrue(regiao[max(0, y0):y1, max(0, x0):x1].any(), (tipo, info))  # cobre parte da edição
                mudou = (o2 != e2).any(2)
                self.assertFalse((mudou & ~regiao).any(), tipo)  # a marca é igual no par: nada muda fora da edição
                self.assertGreater(mudou.sum(), 0.5 * regiao.sum(), tipo)  # e a edição continua lá

    def test_sem_a_chave_nunca_por_cima(self):
        for sem in range(10):
            orig, ed, regiao = _par()
            (o2, e2), info = marcas.aplicar([orig, ed], regiao, {"semente": sem, "tipos": ["carimbo", "manuscrito"], "alvos": []})
            for m in info:
                x0, y0, x1, y1 = m["caixa"]
                self.assertFalse(regiao[max(0, y0):y1, max(0, x0):x1].any())
                self.assertNotIn("sobre_edicao", m)


# ============================================================================ 5. perito sem medida
class TestPeritoSemMedida(unittest.TestCase):
    def _sessao(self):
        from tests.util import conta
        base = conta(2)  # papel com grão e linhas de texto em volta: o perito tem o que medir
        atual = base.copy()
        atual[94:114, 14:220] = np.clip(atual[94:114, 14:220].astype(int) + 40, 0, 255).astype(np.uint8)
        mapa = np.zeros(base.shape[:2], np.uint16)
        mapa[94:114, 14:220] = 1
        return mock.Mock(base=base, atual=atual, H=base.shape[0], W=base.shape[1], mapa_edicoes=lambda: (mapa, [object()]))

    def test_sem_medida_fica_sem_nota(self):
        from rotulador import perito
        vazio = {k: None for k in ("grao", "espectro", "nitidez", "tom", "blocos")}
        with mock.patch.object(perito, "medir", return_value=vazio):
            out = gerador._perito_por_edicao(self._sessao())
        self.assertIsNone(out[1]["nota"])  # não 0,0: 0 diria "invisível", e é só falta de medida
        with mock.patch.object(perito, "nota", return_value=None):  # o perito novo já devolve None
            out = gerador._perito_por_edicao(self._sessao())
        self.assertIsNone(out[1]["nota"])

    def test_com_medida_tem_nota(self):
        out = gerador._perito_por_edicao(self._sessao())
        self.assertIsInstance(out[1]["nota"], float)


if __name__ == "__main__":
    unittest.main()
