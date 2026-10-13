"""Regressões achadas nas rodadas de revisão (cada teste reproduz um defeito que existiu)."""
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from app import RAIZ, carregar_categorias, criar_app
from rotulador import canal, datasheet, exportar, ocr
from rotulador.ops import executar
from rotulador.registro import SPLITS, Registro
from rotulador.selecao import vazia
from rotulador.sessao import Sessao
from tests.util import conta, salvar

PINCEL = {"pontos": [[100, 300], [200, 300]], "tamanho": 12, "cor": "#ff0000"}


class TestRevisao(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            salvar(conta(50 + i), self.dados / sp / f"a_{sp}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.cats = carregar_categorias(RAIZ / "config" / "categorias.json")
        self.c = criar_app(self.reg, self.saida, self.cats).test_client()
        self.it = self.reg.por_split["treino"][0]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sessao(self):
        s = Sessao(self.it, self.reg.carregar(self.it.doc), self.tmp / "rasc" / str(random.random()),
                   {c["chave"]: c["id"] for c in self.cats})
        res = executar("pincel", s.atual, PINCEL, vazia(s.H, s.W))
        s.aplicar("pincel", "valor", "", PINCEL, res)
        return s

    def test_receita_estranha_da_400_e_nao_500(self):
        for ruim in ({"canal": 5}, {"canal": True}, {"canal": ["scanner"]}, {"acabamento": "x"},
                     {"acabamento": {"jpeg": "x"}}, {"edicoes": 3}, {"marcas": "abc"}, {"canal": "fax"}):
            r = self.c.post("/api/gerador/previa", json={"doc": self.it.doc, "receita": {
                "tecnicas": {"campo.reescrever": 1}, **ruim}})
            self.assertEqual(r.status_code, 400, (ruim, r.get_json()))

    def test_excluir_versao_nao_deixa_original_orfa(self):
        """v01 usa a original comum; v02 (com canal) tem a sua. Excluir a v01 tira a comum também."""
        exportar.salvar(self.sessao(), self.saida)
        th = canal.sortear(random.Random(1), {"perfil": "scanner"})
        exportar.salvar(self.sessao(), self.saida, canal_envio=th)
        orig = self.saida / "treino" / "originais"
        self.assertEqual(len(list(orig.iterdir())), 2)
        exportar.excluir_versao(self.saida, "treino", f"{self.it.doc}__v01")
        sobra = [p.name for p in orig.iterdir()]
        self.assertEqual(sobra, [f"{self.it.doc}__v02.png"])

    def test_caixas_das_marcas_nas_coordenadas_da_imagem_salva(self):
        th = canal.sortear(random.Random(3), {"perfil": "celular"})
        tm = {"semente": 4, "tipos": ["carimbo", "manuscrito", "autenticacao"]}
        salvo = exportar.salvar(self.sessao(), self.saida, canal_envio=th, marcas_benignas=tm)
        a = json.loads((self.saida / "treino" / salvo["arquivos"]["anotacao"]).read_text(encoding="utf-8"))
        self.assertTrue(a["marcas_benignas"])
        for m in a["marcas_benignas"]:
            x0, y0, x1, y1 = m["caixa"]
            self.assertTrue(0 <= x0 < x1 <= a["largura"] and 0 <= y0 < y1 <= a["altura"], (m, a["largura"], a["altura"]))
        self.assertEqual((salvo["largura"], salvo["altura"]), (a["largura"], a["altura"]))  # "recentes" do gerador

    def test_auditoria_sem_ocr_avisa_e_nao_quebra(self):
        from auditar_vazamento import auditar
        with mock.patch("rotulador.automatico.campos_do_documento", side_effect=ocr.OCRIndisponivel("sem rapidocr")):
            rel = auditar(self.dados, self.saida, limiar=0)
        self.assertEqual(rel["erros"], [])
        self.assertTrue(any("OCR indisponível" in w for w in rel["avisos"]), rel["avisos"])

    def test_tiff_deterministico(self):
        img = conta(9)[:, :-1]  # largura ímpar: o libtiff põe um byte de alinhamento antes do IFD
        primeiro = exportar.codificar(img, "tiff", 90)
        for _ in range(8):
            self.assertEqual(primeiro, exportar.codificar(img, "tiff", 90))

    # ---------------------------------------------------------------- rodada 2
    def test_mesmo_cliente_valida_e_extrai_o_cpf(self):
        """CPF de mentira (000.000.000-00) em clientes diferentes não é o mesmo cliente; CPF na mesma
        linha que o RG continua sendo achado; endereço repetido em base pequena ainda avisa."""
        from auditar_vazamento import mesmo_cliente
        por_split = {it.sha_pixels: it.split for it in self.reg.itens.values()}

        def campos(img, saida, sha):
            sp = por_split[sha]
            if sp == "validacao":
                return []
            return [{"tipo": "texto", "categoria": "cpf/cnpj", "texto": "000.000.000-00"},
                    {"tipo": "texto", "categoria": "outro", "texto": "CPF 123.456.789-09 RG 4567890"},
                    {"tipo": "texto", "categoria": "endereço", "texto": "RUA DAS ACACIAS, 45"}]
        with mock.patch("rotulador.automatico.campos_do_documento", side_effect=campos):
            erros, avisos = mesmo_cliente(self.reg, self.saida, log=lambda *_: None)
        self.assertEqual(len(erros), 1, erros)
        self.assertIn("12345678909", erros[0])
        self.assertTrue(any("RUA DAS ACACIAS" in w for w in avisos), avisos)

    def test_vocabulario_mesma_palavra_mesmo_split(self):
        """'SANTOS' cidade e 'SANTOS' sobrenome caem no mesmo split (o hash é da palavra, não da tupla)."""
        from rotulador.ops import geradores as g
        visto = {}
        for nome, partes in g._PARTES.items():
            for sp, lista in partes.items():
                for x in lista:
                    palavra = x[0] if isinstance(x, tuple) else x
                    self.assertEqual(visto.setdefault(palavra, sp), sp, (nome, palavra))

    def test_jitter_tamanho_nao_corta_letra(self):
        """A mudança de tamanho cabe na folga da área de trabalho (senão a borda suave cortava a letra
        da ponta e o OCR, que ignora espaços, não percebia)."""
        from rotulador.contexto import Contexto
        from tests.test_auto import conta as conta_arial
        img = conta_arial(3)
        campo = {"id": "n", "caixa": [330, 168, 760, 200], "texto": "JOAO PEREIRA LIMA 03", "categoria": "nome", "tipo": "texto"}
        for s in range(12):
            p = {"tecnica": "trecho.jitter", "campo": campo, "semente": s, "opcoes": {"operacao": "tamanho"},
                 "conferir_ocr": False}
            res = executar("tecnica", img, p, vazia(*img.shape[:2]), base=img)
            m = res.meta
            self.assertLessEqual(abs(m["escala_pct"]) / 100 * m["largura_trecho"] / 2, m["folga_px"] + 0.01, m)

    def test_colagem_da_ia_regiao_inteira_e_contexto_curto(self):
        """'contexto' cola um retângulo inteiro perto do campo (não pontilhado, nem a página toda);
        'suave' cola a área da borda suave inteira."""
        import cv2
        from rotulador.ops import auto
        img = conta(60)
        def vae(rgb, prompt, semente, passos):  # VAE realista: borra e desloca o tom
            return np.clip(cv2.GaussianBlur(rgb, (0, 0), 1.2).astype(int) + 4, 0, 255).astype(np.uint8)
        caixa = [14, 192, 200, 214]
        for modo in ("suave", "contexto"):
            p = {"caixa": caixa, "texto": "TOTAL R$ 999,45", "texto_antigo": "TOTAL R$ 150,45", "semente": 1,
                 "colagem": modo, "pena": 3, "igualar_ruido": False}
            res = auto.trocar_campo_ia(img, p, vazia(*img.shape[:2]), base=img, editor=vae)
            if modo == "contexto":
                self.assertTrue(res.pegada.all(), "contexto: retângulo inteiro")
                h = caixa[3] - caixa[1]
                sel_h = h + 2 * max(4, round(0.3 * h))  # a seleção do "trocar com IA" tem folga em volta
                self.assertLessEqual(res.y1 - res.y0, 3 * sel_h + 2)
                cx0, cy0, cx1, cy1 = res.meta["contexto"]
                self.assertLess((res.y1 - res.y0) * (res.x1 - res.x0), (cy1 - cy0) * (cx1 - cx0))
            else:
                n, _ = cv2.connectedComponents(res.pegada.astype(np.uint8))
                self.assertEqual(n - 1, 1, "suave: uma área só")

    def test_previa_sem_classes_e_erro_e_catalogo_tem_o_documento(self):
        r = self.c.post("/api/gerador/previa", json={"doc": self.it.doc, "receita": {
            "tecnicas": {"campo.reescrever": 1}, "categorias": {}}})
        self.assertEqual(r.status_code, 400, r.get_json())  # antes: {} virava "todas as classes"
        exportar.salvar(self.sessao(), self.saida)
        itens = self.c.get("/api/catalogo/itens").get_json()["itens"]
        self.assertEqual(itens[0]["documento"], self.it.nome)

    # ---------------------------------------------------------------- rodada 4 (auditoria dos dados)
    def _doc(self, linhas, fonte="times.ttf", tam=30):
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (900, 70 * len(linhas) + 40), (246, 244, 238))
        d = ImageDraw.Draw(im)
        caixas = []
        for i, t in enumerate(linhas):
            f = ImageFont.truetype(f"C:/Windows/Fonts/{fonte}", tam)
            d.text((40, 30 + 70 * i), t, font=f, fill=(25, 25, 25))
            caixas.append(list(d.textbbox((40, 30 + 70 * i), t, font=f)))
        return np.asarray(im).copy(), caixas

    def test_remover_nao_deixa_fantasma_nem_serifa(self):
        img, (cx,) = self._doc(["JOAQUIM PEDRO FAGUNDES"])[0], self._doc(["JOAQUIM PEDRO FAGUNDES"])[1]
        caixa = [cx[0] + 1, cx[1] + 3, cx[2] - 1, cx[3] - 2]  # caixa de OCR justa: corta serifa
        campo = {"id": "n", "caixa": caixa, "texto": "JOAQUIM PEDRO FAGUNDES", "categoria": "nome", "tipo": "texto"}
        res = executar("tecnica", img, {"tecnica": "campo.remover", "campo": campo, "semente": 1,
                                        "opcoes": {"parte": "campo"}, "conferir_ocr": False,
                                        "acabamento": {"disfarce": "nunca", "ruido": "nunca"}},
                       vazia(*img.shape[:2]), base=img)
        depois = img.copy()
        depois[res.y0:res.y1, res.x0:res.x1] = np.where(res.pegada[..., None], res.recorte, depois[res.y0:res.y1, res.x0:res.x1])
        x0, y0, x1, y1 = cx
        self.assertGreater(int(depois[y0 - 4:y1 + 4, x0 - 4:x1 + 4].min()), 200)  # nada de tinta nem halo

    def test_apagar_caractere_mantem_o_espaco(self):
        from rotulador import glifos
        img, (cx,) = self._doc(["RUA DAS FLORES, 104 APTO 12"], "arial.ttf")
        # o OCR leu "104APTO" (sem o espaço que está na imagem), como no caso da auditoria
        campo = {"id": "e", "caixa": cx, "texto": "RUA DAS FLORES, 104APTO 12", "categoria": "endereço", "tipo": "texto"}
        i4 = campo["texto"].index("4")
        res = executar("tecnica", img, {"tecnica": "caractere.apagar", "campo": campo, "semente": 2, "indice": i4,
                                        "opcoes": {"vao": "fechar", "alvo": "digito"}, "conferir_ocr": False,
                                        "acabamento": {"disfarce": "nunca", "ruido": "nunca"}},
                       vazia(*img.shape[:2]), base=img)
        depois = img.copy()
        depois[res.y0:res.y1, res.x0:res.x1] = np.where(res.pegada[..., None], res.recorte, depois[res.y0:res.y1, res.x0:res.x1])
        def espacos(im):  # quantos vãos do tamanho de um espaço entre palavras há na linha
            from rotulador.selecao import mascara_tinta
            cols = np.flatnonzero(mascara_tinta(im[cx[1]:cx[3], cx[0]:cx[2]]).any(0))
            return int((np.diff(cols) > 0.25 * (cx[3] - cx[1])).sum())
        self.assertEqual(espacos(depois), espacos(img))  # nenhum espaço entre palavras sumiu

    def test_letra_fora_de_nome_no_sorteio_nao_vira_erro_de_grafia(self):
        from rotulador.sessao import ErroOperacao
        img, (cx,) = self._doc(["CURITIBA/PR"], "arial.ttf")
        campo = {"id": "c", "caixa": cx, "texto": "CURITIBA/PR", "categoria": "cidade/uf", "tipo": "texto"}
        for s in range(5):
            with self.assertRaises(ErroOperacao):
                executar("tecnica", img, {"tecnica": "caractere.redesenhar", "campo": campo, "semente": s,
                                          "conferir_ocr": False}, vazia(*img.shape[:2]), base=img)

    def test_disfarce_de_cor_nao_tinge_o_papel(self):
        from rotulador.ops import tecnicas
        img, (cx,) = self._doc(["VALOR 1234"], "arial.ttf")
        campo = {"id": "v", "caixa": cx, "texto": "VALOR 1234", "categoria": "identificador documento", "tipo": "texto"}
        with mock.patch.object(tecnicas, "_DISFARCES", (("cor", 1.0),)):
            res = executar("tecnica", img, {"tecnica": "caractere.redesenhar", "campo": campo, "semente": 3,
                                            "conferir_ocr": False, "acabamento": {"disfarce": "sempre", "ruido": "nunca"}},
                           vazia(*img.shape[:2]), base=img)
        antes = img[res.y0:res.y1, res.x0:res.x1].astype(int)
        papel = (antes.min(2) > 235) & (res.recorte.astype(int).min(2) > 235)
        self.assertLessEqual(int(np.abs(res.recorte.astype(int) - antes)[papel].max(initial=0)), 2)

    def test_traco_fino_nao_some_na_banda_de_ignorar(self):
        alt = np.zeros((40, 60), bool)
        alt[20, 5:55] = True  # traço de 1 px
        ign = exportar.mascara_ignorar(alt, alt.copy())
        self.assertFalse((ign & alt).any())
        self.assertTrue(ign[19, 30] and ign[21, 30])

    def test_codigo_novo_sem_linha_coerente_e_desfeito(self):
        from types import SimpleNamespace
        from rotulador import gerador
        from tests.test_auto import conta as conta_arial
        img = conta_arial(4)
        campos = [{"id": "b", "caixa": [100, 860, 800, 950], "texto": "", "categoria": "codigo barras", "tipo": "barras"},
                  {"id": "l", "caixa": [100, 820, 800, 845], "texto": "83660000001-2 50370048000-6 12345678901-2 34567890123-4",
                   "categoria": "linha digitável", "tipo": "texto"}]
        it = self.it
        s = Sessao(it, img, self.tmp / "rasc_cod", {c["chave"]: c["id"] for c in self.cats})
        ctx = SimpleNamespace(campos=campos, doc=it.doc, split=it.split, img=img)
        r = gerador.Receita({"tecnicas": {"codigo.regenerar": 1}, "categorias": {"codigo barras": 1}},
                            {c["chave"]: c["id"] for c in self.cats})
        falhas = []
        with mock.patch.object(gerador, "_reescrever_linha", return_value=None):
            feitos = gerador.aplicar_receita(s, ctx, r, gerador.Plano(r, random.Random(1)), random.Random(1), k=1,
                                             ao_falhar=lambda *a: falhas.append(a))
        self.assertEqual(feitos, [])
        self.assertFalse((s.atual != s.base).any())  # desfeito
        self.assertTrue(any("linha digitável" in str(f[2]) for f in falhas), falhas)

    # ---------------------------------------------------------------- rodada 5 (revisão final)
    def test_sorteio_nao_troca_letra_nem_em_nome(self):
        from rotulador.ops.tecnicas import aplicavel
        from rotulador.sessao import ErroOperacao
        from rotulador import catalogo
        img, (cx,) = self._doc(["JOAO PEREIRA LIMA"], "arial.ttf")
        campo = {"id": "n", "caixa": cx, "texto": "JOAO PEREIRA LIMA", "categoria": "nome", "tipo": "texto"}
        self.assertFalse(aplicavel(catalogo.TECNICAS["caractere.redesenhar"], campo))
        with self.assertRaises(ErroOperacao):
            executar("tecnica", img, {"tecnica": "caractere.redesenhar", "campo": campo, "semente": 0,
                                      "conferir_ocr": False}, vazia(*img.shape[:2]), base=img)
        res = executar("tecnica", img, {"tecnica": "caractere.redesenhar", "campo": campo, "semente": 0, "conferir_ocr": False,
                                        "opcoes": {"alvo": "letra"}}, vazia(*img.shape[:2]), base=img)
        self.assertNotEqual(res.meta["texto_novo"], campo["texto"])  # pedida explicitamente, continua valendo

    def test_valor_com_milhar_continua_bem_formado(self):
        from rotulador.ops.tecnicas import plausivel
        for ruim in ("R$ 1.23,56", "R$ .234,56", "R$ 1.24,56", "R$ 1234,56", "R$ 1.2234,56"):
            self.assertFalse(plausivel("valor", "R$ 1.234,56", ruim), ruim)
        for bom in ("R$ 11.234,56", "R$ 1.239,56", "R$ 234,56", "R$ 999,00"):
            self.assertTrue(plausivel("valor", "R$ 1.234,56", bom), bom)

    def test_linha_refeita_conta_como_codigo_regenerar_e_contagem_volta(self):
        from types import SimpleNamespace
        from PIL import Image, ImageDraw, ImageFont
        from rotulador import gerador
        from tests.test_auto import conta as conta_arial
        linha = "83660000001-2 50370048000-6 12345678901-2 34567890123-4"
        im = Image.fromarray(conta_arial(4))
        d = ImageDraw.Draw(im)
        f = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)
        d.text((100, 800), linha, font=f, fill=(20, 20, 20))
        img = np.asarray(im).copy()
        cx = list(d.textbbox((100, 800), linha, font=f))
        campos = [{"id": "b", "caixa": [100, 860, 800, 950], "texto": "", "categoria": "codigo barras", "tipo": "barras"},
                  {"id": "l", "caixa": cx, "texto": linha, "categoria": "linha digitável", "tipo": "texto"}]
        ids = {c["chave"]: c["id"] for c in self.cats}
        r = gerador.Receita({"tecnicas": {"codigo.regenerar": 1}, "categorias": {"codigo barras": 1}}, ids)
        ctx = SimpleNamespace(campos=campos, doc=self.it.doc, split=self.it.split, img=img)
        s = Sessao(self.it, img, self.tmp / "rasc_lin", ids)
        plano = gerador.Plano(r, random.Random(1))
        feitos = gerador.aplicar_receita(s, ctx, r, plano, random.Random(2), k=1)
        self.assertEqual(len(feitos), 2, feitos)  # código novo + linha digitável coerente
        mapa, ents = s.mapa_edicoes()  # as duas edições são do codigo.regenerar (também no que é exportado)
        eds = exportar.edicoes_da_sessao(mapa, ents)
        self.assertEqual({e["tecnica"] for e in eds}, {"codigo.regenerar"}, eds)
        # e se a linha não puder ser refeita, a contagem do plano volta
        plano2 = gerador.Plano(r, random.Random(1))
        s2 = Sessao(self.it, img, self.tmp / "rasc_lin2", ids)
        with mock.patch.object(gerador, "_reescrever_linha", return_value=None):
            self.assertEqual(gerador.aplicar_receita(s2, ctx, r, plano2, random.Random(2), k=1), [])
        self.assertEqual(sum(plano2.contagem.values()), 0)

    def test_canal_que_apaga_a_edicao_e_recusado(self):
        from rotulador.exportar import CanalApagouEdicao
        s = Sessao(self.it, self.reg.carregar(self.it.doc), self.tmp / "rasc_claro", {c["chave"]: c["id"] for c in self.cats})
        claro = {**PINCEL, "cor": "#fbfbfb"}  # retoque quase da cor do papel
        s.aplicar("pincel", "valor", "", claro, executar("pincel", s.atual, claro, vazia(s.H, s.W)))
        th = {"perfil": "scanner", "semente": 1, "passos": [{"tipo": "scanner", "angulo": 0.0, "escala": 1.0, "desfoque": 0.3,
                                                             "gama": 1.0, "contraste": 1.08, "brilho": 12, "ruido": 0.0,
                                                             "jpeg": None}]}
        with self.assertRaises(CanalApagouEdicao):
            exportar.salvar(s, self.saida, canal_envio=th)
        self.assertFalse((self.saida / "treino" / "anotacoes").exists() and
                         list((self.saida / "treino" / "anotacoes").glob("*.json")))

    def test_datasheet_conta_anotacoes_antigas(self):
        exportar.salvar(self.sessao(), self.saida)
        arq = next((self.saida / "treino" / "anotacoes").glob("*.json"))
        a = json.loads(arq.read_text(encoding="utf-8"))
        del a["edicoes"]  # formato anterior à 2.0: as edições saem das operações
        arq.write_text(json.dumps(a), encoding="utf-8")
        self.assertEqual(datasheet.coletar(self.saida)["splits"]["treino"]["edicoes"], 1)


if __name__ == "__main__":
    unittest.main()
