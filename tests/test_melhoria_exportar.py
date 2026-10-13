"""Melhorias da auditoria do dataset (exportação, treino e auditorias): cada teste mostra um ponto fraco achado.

1 pesos/equilíbrio do par; 2 vazamento (modelo, cliente, emissor, par pelo arquivo real); 3 fornecedor de fora;
4 dificuldade (a edição mais visível decide; perito sem medida = None); 5 revisão, alertas, conferência do OCR,
origem e dificuldade no editor; 6 saída com perda sem canal; 7 canal com Δ pequeno; 8 caixas por ocorrência;
9 alertas do dataset; 10 nota de atalho no datasheet."""
import csv
import json
import random
import shutil
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from app import RAIZ, carregar_categorias, criar_app
from rotulador import canal, datasheet, documentos, exportar, perito, qualidade
from rotulador.ops import executar
from rotulador.registro import SPLITS, Registro
from rotulador.selecao import vazia
from rotulador.sessao import Sessao
from rotulador.treino import avaliar, dados, robustez
from tests.test_treino import _split
from tests.util import conta, salvar

PINCEL = {"pontos": [[100, 300], [200, 300]], "tamanho": 12, "cor": "#ff0000"}
CATS = carregar_categorias(RAIZ / "config" / "categorias.json")
IDS = {c["chave"]: c["id"] for c in CATS}


def _anot(raiz: Path, nome: str, split: str = "treino") -> tuple[Path, dict]:
    arq = raiz / split / "anotacoes" / f"{nome}.json"
    return arq, json.loads(arq.read_text(encoding="utf-8"))


def _mudar(raiz: Path, nome: str, **troca) -> None:
    arq, a = _anot(raiz, nome)
    a.update(troca)
    arq.write_text(json.dumps(a), encoding="utf-8")


def _ler(raiz_split: Path, a: dict, k: str) -> np.ndarray:
    return documentos.carregar(raiz_split / a["arquivos"][k], 0, 200)


def _sem_rotulo(raiz_split: Path, a: dict) -> int:
    """Pixels em que a original e a editada GRAVADAS diferem e que ficaram rotulados como negativo (fora da máscara
    de mudança e da faixa de ignorar)."""
    o, e = _ler(raiz_split, a, "original"), _ler(raiz_split, a, "editada")
    dif = np.abs(o.astype(np.int16) - e.astype(np.int16)).max(2) > 0
    alt, ign = _ler(raiz_split, a, "mascara")[..., 0] > 127, _ler(raiz_split, a, "mascara_ignorar")[..., 0] > 127
    return int((dif & ~alt & ~ign).sum())


class _ComSessao(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            salvar(conta(90 + i), self.dados / sp / f"a_{sp}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.it = self.reg.por_split["treino"][0]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sessao(self, *edicoes):
        """Sessão com as edições [(tipo da entrada, categoria, params do pincel, params gravados)]."""
        s = Sessao(self.it, self.reg.carregar(self.it.doc), self.tmp / "rasc" / str(random.random()), IDS)
        for tipo, cat, p, gravado in edicoes or (("pincel", "valor", PINCEL, PINCEL),):
            s.aplicar(tipo, cat, "", gravado, executar("pincel", s.atual, p, vazia(s.H, s.W)))
        return s


# ---------------------------------------------------------------------- 1. pesos e equilíbrio do par
class TestPesosEPar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        _split(cls.tmp)
        cls.amostras = dados.listar(cls.tmp, "treino")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_taxa_base_conta_as_originais_servidas(self):
        """Metade do que o conjunto_torch serve é a original (máscara zero): a taxa só das editadas dava o peso do
        positivo ~2x baixo."""
        f = 2500 / (420 * 320)
        p = dados.pesos(self.amostras)
        self.assertAlmostEqual(p["taxa_base_editadas"], f, places=6)
        self.assertAlmostEqual(p["taxa_base"], f / 2, places=6)
        self.assertAlmostEqual(p["peso_positivo"], (1 - f / 2) / (f / 2), places=1)
        self.assertEqual(p["originais"], 2)

    def test_equilibrio_da_peso_igual_a_cada_original(self):
        """1 original para N editadas: a mesma original saía N vezes por época. O peso da original no par faz cada
        original distinta pesar igual e o total de autênticas igualar o de adulteradas."""
        am = [{"arquivos": {"original": Path("o/a.png")}}] * 3 + [{"arquivos": {"original": Path("o/b.png")}}]
        w = dados.equilibrio(am)
        self.assertEqual([round(x, 4) for x in w], [0.6667, 0.6667, 0.6667, 2.0])
        self.assertAlmostEqual(sum(w), len(am))
        self.assertAlmostEqual(sum(w[:3]), w[3])

    def test_par_em_ordem_aleatoria_por_opcao(self):
        arr = dados.carregar(self.amostras[0])
        r = dados.recortar(arr, 64, random.Random(1), p_edicao=1.0)
        p = dados.montar_par(r, 3, peso_original=0.5)
        self.assertEqual(p["imagens"].shape, (2, 64, 64, 3))
        self.assertEqual(p["adulterada"].tolist(), [0, 1])
        self.assertEqual(p["peso"].tolist(), [0.5, 1.0])
        self.assertFalse(p["mascara"][0].any())
        t = dados.montar_par(r, 3, peso_original=0.5, trocar=True)
        self.assertEqual(t["adulterada"].tolist(), [1, 0])
        self.assertEqual(t["peso"].tolist(), [1.0, 0.5])
        self.assertTrue(np.array_equal(t["imagens"][0], r["editada"]) and np.array_equal(t["mascara"][0], r["mascara"]))
        self.assertFalse(t["mascara"][1].any())


# ---------------------------------------------------------------------- 2. vazamento
class TestVazamento(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mesmo_modelo_em_splits_diferentes_e_erro(self):
        quase = conta(30)
        quase[60:80, 14:200] = 246  # outro cliente no mesmo modelo de conta
        salvar(conta(30), self.dados / "treino" / "jan.png")
        salvar(quase, self.dados / "teste" / "fev.png")
        outro = np.full((420, 320, 3), 250, np.uint8)
        outro[200:420, :] = (40, 40, 40)  # outro layout
        salvar(outro, self.dados / "validacao" / "outro.png")
        from auditar_vazamento import auditar
        rel = auditar(self.dados, self.saida, limiar=10, clientes=False)
        erros = [e for e in rel["erros"] if "mesmo modelo" in e]
        self.assertEqual(len(erros), 1, rel["erros"])
        self.assertIn("jan.png", erros[0])
        self.assertIn("fev.png", erros[0])
        self.assertNotIn("outro.png", erros[0])
        self.assertEqual(auditar(self.dados, self.saida, limiar=0, clientes=False)["erros"], [])

    def test_mesmo_cliente_por_nome_cpf_mascarado_e_linha_e_erro(self):
        for sp in SPLITS:
            salvar(conta({"treino": 1, "teste": 2, "validacao": 3}[sp]), self.dados / sp / f"{sp}.png")
        reg = Registro(self.dados, self.saida, log=lambda *_: None)
        por_split = {it.sha_pixels: it.split for it in reg.itens.values()}

        def campos(img, saida, sha):
            if por_split[sha] == "validacao":
                return []
            return [{"tipo": "texto", "categoria": "nome", "texto": "JOSE CARLOS RIBEIRO"},
                    {"tipo": "texto", "categoria": "outro", "texto": "CPF ***.264.088-** RG 123"},
                    {"tipo": "texto", "categoria": "linha digitável",
                     "texto": "82870000001-2 58458200006-0 12345678901-2 34567890123-4"}]
        from auditar_vazamento import auditar
        with mock.patch("rotulador.automatico.campos_do_documento", side_effect=campos):
            rel = auditar(self.dados, self.saida, limiar=0)
        texto = "\n".join(rel["erros"])
        self.assertIn("mesmo nome 'JOSE CARLOS RIBEIRO'", texto)
        self.assertIn("mesmo CPF mascarado '***.264.088-**'", texto)
        self.assertIn("mesma linha digitável", texto)

    def test_emissor_pequeno_em_splits_diferentes_avisa_com_contagem(self):
        """O limiar de 20% deixava de fora o CNPJ de um emissor com poucos documentos."""
        from auditar_vazamento import emissor_por_documento
        It = namedtuple("It", "doc split nome")
        itens = [It(f"d{i}", SPLITS[i % 3], f"d{i}.png") for i in range(5)]
        onde = {("CNPJ", "11444777000161"): set(itens[:2]), ("CNPJ", "00000000000191"): set(itens[2:5])}
        self.assertEqual(emissor_por_documento(onde), {"d0": "11444777000161", "d1": "11444777000161",
                                                       "d2": "00000000000191", "d3": "00000000000191",
                                                       "d4": "00000000000191"})
        for sp in SPLITS:
            for k in range(2):
                salvar(conta(10 * k + SPLITS.index(sp)), self.dados / sp / f"{'ab'[k]}_{sp}.png")
        reg = Registro(self.dados, self.saida, log=lambda *_: None)
        nomes = {it.sha_pixels: it.rel for it in reg.itens.values()}

        def campos(img, saida, sha):
            return [{"tipo": "texto", "categoria": "emissor/logotipo", "texto": "CNPJ 11.444.777/0001-61"}] \
                if nomes[sha] in ("a_treino.png", "a_teste.png") else []
        from auditar_vazamento import auditar
        with mock.patch("rotulador.automatico.campos_do_documento", side_effect=campos):
            rel = auditar(self.dados, self.saida, limiar=0)
        self.assertEqual(rel["erros"], [])
        av = [w for w in rel["avisos"] if "11444777000161" in w]
        self.assertEqual(len(av), 1, rel["avisos"])
        self.assertIn("treino: 1, teste: 1", av[0])

    def test_par_conferido_pelo_arquivo_real(self):
        salvar(conta(5), self.dados / "treino" / "a.png")
        reg = Registro(self.dados, self.saida, log=lambda *_: None)
        c = criar_app(reg, self.saida, CATS).test_client()
        doc = reg.por_split["treino"][0].doc
        self.assertEqual(c.post("/api/op", json={"id": doc, "tipo": "pincel", "params": PINCEL, "categoria": "valor"}).status_code, 200)
        salvo = c.post("/api/salvar", json={"id": doc, "formato": "jpeg", "qualidade": 90}).get_json()["salvo"]
        from auditar_vazamento import auditar
        self.assertEqual(auditar(self.dados, self.saida, limiar=0, clientes=False)["erros"], [])
        ed = self.saida / "treino" / salvo["arquivos"]["editada"]
        Image.open(ed).convert("RGB").save(ed, "JPEG", quality=60)  # mesmo nome (__q90), outra qualidade
        erros = auditar(self.dados, self.saida, limiar=0, clientes=False)["erros"]
        self.assertTrue(any("no arquivo" in e and "qualidade" in e for e in erros), erros)
        Image.open(ed).convert("L").resize((300, 400)).save(ed, "JPEG", quality=90)
        erros = auditar(self.dados, self.saida, limiar=0, clientes=False)["erros"]
        self.assertTrue(any("tamanho" in e and "modo" in e for e in erros), erros)


# ---------------------------------------------------------------------- 3. um fornecedor de fora
class TestFornecedores(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.nomes = _split(cls.tmp)
        for nome, forn in ((cls.nomes[0], {"fonte": "Arial", "texto": "freetype"}), (cls.nomes[1], {"fonte": "Calibri"})):
            arq, a = _anot(cls.tmp, nome)
            a["edicoes"][0]["fornecedores"] = forn
            arq.write_text(json.dumps(a), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_listar_deixa_um_fornecedor_de_fora(self):
        todos = dados.listar(self.tmp, "treino")
        self.assertEqual(next(a for a in todos if a["nome"] == self.nomes[0])["fornecedores"], ["fonte=Arial", "texto=freetype"])
        sem = dados.listar(self.tmp, "treino", excluir_fornecedor={"fonte": "Arial"})
        self.assertEqual([a["nome"] for a in sem], self.nomes[1:])
        so = dados.listar(self.tmp, "treino", so_fornecedor={"fonte": "Arial"})
        self.assertEqual([a["nome"] for a in so], [self.nomes[0]])
        self.assertEqual(len(dados.listar(self.tmp, "treino", so_fornecedor={"fonte": ["Arial", "Calibri"]})), 2)

    def test_avaliar_quebra_por_fornecedor(self):
        pred = self.tmp / "pred"
        pred.mkdir(exist_ok=True)
        for a in dados.listar(self.tmp, "treino"):
            Image.fromarray(dados.carregar(a)["mascara"].astype(np.uint8) * 255).save(pred / f"{a['nome']}.png")
        d = avaliar.avaliar(self.tmp, "treino", pred, calibrar_n=0)
        self.assertEqual(d["por"]["fornecedores"]["fonte=Arial"]["n"], 1)
        self.assertEqual(d["por"]["fornecedores"]["(nenhum)"]["n"], 2)
        self.assertIn("Por fornecedores", avaliar.markdown(d))


# ---------------------------------------------------------------------- 4. dificuldade
class TestDificuldade(unittest.TestCase):
    def test_perito_sem_medida_e_none(self):
        self.assertIsNone(perito.nota({}))
        self.assertIsNone(perito.nota({"grao": None, "tom": float("nan")}))
        img = conta(1)
        reg = np.zeros(img.shape[:2], bool)
        reg[200:203, 200:203] = True  # 9 px: não há papel nem tinta que chegue
        self.assertIsNone(perito.avaliar(img, img.copy(), reg)["nota"])

    def test_a_edicao_mais_visivel_decide(self):
        """1 − menor nota crescia com o nº de edições (mais uma edição quase invisível = versão "mais difícil")."""
        uma = {"edicoes": [{"perito": {"nota": 0.9}}]}
        duas = {"edicoes": [{"perito": {"nota": 0.9}}, {"perito": {"nota": 0.05}}]}
        self.assertEqual(exportar.dificuldade_de(uma), exportar.dificuldade_de(duas))
        self.assertEqual(exportar.dificuldade_de(duas), 0.1)
        self.assertEqual(exportar.dificuldade_de({"edicoes": [{"perito": {"nota": None}}, {"perito": {"nota": 0.3}}]}), 0.7)
        self.assertIsNone(exportar.dificuldade_de({"edicoes": [{"perito": {"nota": None}}, {}]}))


# ---------------------------------------------------------------------- 5. revisão, alertas, conferência, origem
class TestRevisaoEOrigem(_ComSessao):
    def test_listar_filtra_revisao_e_alertas(self):
        nomes = _split(self.tmp)
        _mudar(self.tmp, nomes[0], revisao={"estado": "aprovada"})
        _mudar(self.tmp, nomes[1], revisao={"estado": "reprovada"}, qualidade={"alertas": ["reprovada: x"]})
        _mudar(self.tmp, nomes[2], qualidade={"alertas": []})
        self.assertEqual([a["nome"] for a in dados.listar(self.tmp, "treino", revisao=("aprovada",))], [nomes[0]])
        self.assertEqual([a["nome"] for a in dados.listar(self.tmp, "treino", revisao=("aprovada", "pendente"))],
                         [nomes[0], nomes[2], nomes[3]])
        self.assertEqual([a["nome"] for a in dados.listar(self.tmp, "treino", sem_alertas=True)],
                         [nomes[0], nomes[2], nomes[3]])

    def test_alertas_gravados_nao_apagam_a_qualidade_do_jpeg(self):
        """auditar_qualidade --gravar escrevia os alertas em "qualidade", a chave da qualidade do JPEG: o número sumia
        e o datasheet (e o filtro sem_alertas) quebrava em dataset com perda."""
        a = exportar.salvar(self.sessao(), self.saida, formato="jpeg", qualidade=90)
        nome = f"{self.it.doc}__v{a['versao']:02d}"
        qualidade.auditar(self.saida, ["treino"], gravar=True, log=lambda *_: None)
        _, anot = _anot(self.saida, nome)
        self.assertEqual(anot["qualidade"], 90)
        self.assertEqual(anot["auditoria_qualidade"]["alertas"], [])
        datasheet.coletar(self.saida)
        self.assertEqual(len(dados.listar(self.saida, "treino", sem_alertas=True)), 1)
        arq, anot = _anot(self.saida, nome)  # como a 2.2 gravava
        del anot["auditoria_qualidade"]
        anot["qualidade"] = {"alertas": ["invisivel: x"], "em": "2026"}
        arq.write_text(json.dumps(anot), encoding="utf-8")
        self.assertEqual([a["nome"] for a in dados.listar(self.saida, "treino", sem_alertas=True)], [])
        self.assertEqual(datasheet.coletar(self.saida)["splits"]["treino"]["formatos"], {"jpeg q90": 1})
        qualidade.auditar(self.saida, ["treino"], gravar=True, log=lambda *_: None)
        self.assertEqual(_anot(self.saida, nome)[1]["qualidade"], 90)  # volta do nome do arquivo (__q90)

    def test_conferencia_do_ocr_vai_para_a_edicao_e_o_datasheet_conta(self):
        r = {"tipo": "tecnica", "params": {"tecnica": "campo.reescrever"}, "meta": {"conferido_ocr": True, "texto_novo": "B"}}
        self.assertIs(exportar.descrever(1, r, 10, [0, 0, 2, 2])["conferido_ocr"], True)
        self.assertNotIn("conferido_ocr", exportar.descrever(1, {"tipo": "pincel"}, 10, [0, 0, 2, 2]))
        nomes = _split(self.tmp)
        arq, a = _anot(self.tmp, nomes[0])
        a["edicoes"][0]["conferido_ocr"] = True
        arq.write_text(json.dumps(a), encoding="utf-8")
        s = datasheet.coletar(self.tmp)["splits"]["treino"]
        self.assertEqual(s["conferencia_ocr"], {"edicoes_texto": 4, "conferidas": 1, "nao_conferidas": 3})
        self.assertIn("não conferidas pelo OCR", datasheet.markdown(datasheet.coletar(self.tmp)))

    def test_origem_da_versao(self):
        auto = ("tecnica", "valor", PINCEL, {"tecnica": "campo.reescrever", "campo": {"id": "c1"}})
        a = exportar.salvar(self.sessao(auto), self.saida)
        _, anot = _anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")
        self.assertEqual((anot["origem"], anot["edicoes"][0]["origem"]), ("automatico", "automatica"))
        a = exportar.salvar(self.sessao(auto, ("pincel", "nome", {**PINCEL, "pontos": [[60, 380]]}, PINCEL)), self.saida)
        self.assertEqual(_anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")[1]["origem"], "assistido")
        a = exportar.salvar(self.sessao(), self.saida)
        self.assertEqual(_anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")[1]["origem"], "manual")
        a = exportar.salvar(self.sessao(auto), self.saida, extra={"gerador": {"lote": "x"}})
        self.assertEqual(_anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")[1]["origem"], "gerador")
        with open(self.saida / "treino" / "manifesto.csv", encoding="utf-8") as f:
            self.assertEqual([l["origem"] for l in csv.DictReader(f)], ["automatico", "assistido", "manual", "gerador"])

    def test_salvar_pelo_editor_grava_a_dificuldade(self):
        c = criar_app(self.reg, self.saida, CATS).test_client()
        doc = self.it.doc
        sel = {"formas": [{"tipo": "ret", "x0": 14, "y0": 192, "x1": 200, "y1": 214}]}  # região com papel: o perito mede
        r = c.post("/api/op", json={"id": doc, "tipo": "substituir_texto", "selecao": sel, "categoria": "data",
                                    "params": {"texto": "VENC 01/01/2027", "x": 14, "y": 210, "tamanho": 16}})
        self.assertEqual(r.status_code, 200, r.get_json())
        salvo = c.post("/api/salvar", json={"id": doc}).get_json()["salvo"]
        _, anot = _anot(self.saida, f"{doc}__v{salvo['versao']:02d}")
        self.assertIsInstance(anot["edicoes"][0]["perito"]["nota"], float)
        self.assertIsNotNone(anot["dificuldade"])
        self.assertEqual(anot["origem"], "manual")

    def test_editar_os_marcados_nao_empilha_no_mesmo_campo(self):
        """Clicar de novo no "Editar os marcados" editava por cima da edição anterior do mesmo campo (texto_antigo
        errado): o servidor pula o campo já trocado."""
        c = criar_app(self.reg, self.saida, CATS).test_client()
        doc = self.it.doc
        c.post("/api/abrir", json={"id": doc})

        def editar(s, ctx, campos, r, plano, rng, ao_falhar=None, humano_hab=None):
            for f in campos:
                p = {"tecnica": "campo.reescrever", "campo": {"id": f["id"]}}
                ent = s.aplicar("tecnica", f["categoria"], "", p, executar("pincel", s.atual, PINCEL, vazia(s.H, s.W)))
            return [{"bbox": [100, 290, 200, 310], "tecnica": "campo.reescrever", "categoria": "nome"}] if campos else []
        campo = {"id": "c1", "caixa": [14, 60, 200, 80], "texto": "NOME: CLIENTE 090", "categoria": "nome", "tipo": "texto"}
        with mock.patch("app.editar_campos", side_effect=editar) as m:
            r = c.post("/api/auto/fraude", json={"id": doc, "campos": [campo]})
            self.assertEqual(r.status_code, 200, r.get_json())
            r = c.post("/api/auto/fraude", json={"id": doc, "campos": [campo]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Desfazer", r.get_json()["erro"])
        self.assertEqual(m.call_count, 1)


# ---------------------------------------------------------------------- 6 e 7. espalhamento do codec
class TestEspalhamento(_ComSessao):
    def test_saida_com_perda_sem_canal_ignora_o_espalhamento(self):
        for fmt, q in (("jpeg", 95), ("webp", 90), ("jpeg", 70), ("pdf", 80)):
            a = exportar.salvar(self.sessao(), self.saida, formato=fmt, qualidade=q)
            _, anot = _anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")
            self.assertEqual(_sem_rotulo(self.saida / "treino", anot), 0, (fmt, q))
            self.assertEqual(qualidade.avaliar_versao(self.saida / "treino", anot), [], (fmt, q))

    def test_qualidade_confere_o_par_sem_canal(self):
        a = exportar.salvar(self.sessao(), self.saida)
        _, anot = _anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")
        r = self.saida / "treino"
        ed = _ler(r, anot, "editada").copy()
        ed[5:9, 5:9] = 0  # mudou longe da edição e ninguém rotulou
        Image.fromarray(ed).save(r / anot["arquivos"]["editada"])
        al = qualidade.avaliar_versao(r, anot)
        self.assertTrue(any("difere longe da edição" in x for x in al), al)
        self.assertTrue(any("fora da máscara e da faixa de ignorar" in x for x in al), al)

    def test_canal_leva_toda_diferenca_para_ignorar(self):
        """Só Δ > 8 ia para ignorar: o resto do espalhamento do canal ficava rotulado negativo."""
        for semente, perfil in ((1, "whatsapp"), (2, "scanner"), (3, "celular")):
            th = canal.sortear(random.Random(semente), {"perfil": perfil, "prob": 1.0, "captura": "depois"}, captura="depois")
            a = exportar.salvar(self.sessao(), self.saida, canal_envio=th)
            _, anot = _anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")
            self.assertEqual(_sem_rotulo(self.saida / "treino", anot), 0, perfil)
        arr = {"original": conta(1), "editada": conta(1), "mascara": np.zeros((420, 320), bool)}
        arr["editada"][150:175, 40:140] = 30
        arr["mascara"][150:175, 40:140] = True
        arr |= {"regiao": arr["mascara"].copy(), "classes": arr["mascara"].astype(np.uint8) * 3,
                "tecnicas": arr["mascara"].astype(np.uint8) * 5, "ignorar": np.zeros((420, 320), bool)}
        th = canal.sortear(random.Random(4), {"perfil": "whatsapp", "prob": 1.0, "captura": "depois"}, captura="depois")
        for novo in (dados.aumentar(arr, random.Random(4), {"perfil": "whatsapp", "prob": 1.0, "captura": "depois"})[0],
                     robustez.perturbar(arr, th)):
            dif = np.abs(novo["original"].astype(np.int16) - novo["editada"].astype(np.int16)).max(2) > 0
            self.assertFalse((dif & ~novo["mascara"] & ~novo["ignorar"]).any())


# ---------------------------------------------------------------------- 8. uma caixa por ocorrência
class TestCaixas(_ComSessao):
    def test_caixas_por_ocorrencia(self):
        cima, baixo = {**PINCEL, "pontos": [[40, 70], [120, 70]]}, {**PINCEL, "pontos": [[40, 380], [120, 380]]}
        a = exportar.salvar(self.sessao(("pincel", "nome", cima, cima), ("pincel", "nome", baixo, baixo)), self.saida)
        _, anot = _anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")
        uniao = anot["caixas_por_categoria"]["nome"]  # a chave antiga continua: uma caixa com tudo
        self.assertTrue(uniao[1] < 70 and uniao[3] > 380)
        caixas = anot["caixas_ocorrencias_por_categoria"]["nome"]
        self.assertEqual(len(caixas), 2)
        self.assertTrue(all(c[3] - c[1] < 40 for c in caixas), caixas)


# ---------------------------------------------------------------------- pedido do agente "campos": marca sobre a edição
class TestMarcaSobreEdicao(_ComSessao):
    def test_mascara_so_marca_o_que_ainda_difere(self):
        """Uma marca (carimbo translúcido, opaco no miolo) por cima da linha editada: sem canal, a máscara seguia a da
        sessão e marcava pixel que, depois da marca, já não difere."""
        def aplicar(imgs, regiao, theta):
            out = [i.copy() for i in imgs]
            for i in out:
                i[295:306, 120:160] = (20, 40, 160)  # miolo opaco da marca sobre parte do traço
            return out, [{"tipo": "carimbo", "nome": "carimbo", "caixa": [120, 295, 160, 306], "sobre_edicao": True}]
        with mock.patch.object(exportar.marcas, "aplicar", side_effect=aplicar):
            a = exportar.salvar(self.sessao(), self.saida, marcas_benignas={"semente": 1, "tipos": ["carimbo"]})
        _, anot = _anot(self.saida, f"{self.it.doc}__v{a['versao']:02d}")
        r = self.saida / "treino"
        o, e = _ler(r, anot, "original"), _ler(r, anot, "editada")
        alt = _ler(r, anot, "mascara")[..., 0] > 127
        self.assertTrue(np.array_equal(alt, (o != e).any(2)))
        self.assertTrue(alt.any())
        self.assertIn("sobre_edicao", anot["nota_marcas"])

        def tudo(imgs, regiao, theta):  # a marca cobriu a edição inteira: o par não serve
            out = [i.copy() for i in imgs]
            for i in out:
                i[280:320, 80:220] = 0
            return out, [{"tipo": "carimbo", "nome": "carimbo", "caixa": [80, 280, 220, 320], "sobre_edicao": True}]
        with mock.patch.object(exportar.marcas, "aplicar", side_effect=tudo), self.assertRaises(exportar.CanalApagouEdicao):
            exportar.salvar(self.sessao(), self.saida, marcas_benignas={"semente": 1, "tipos": ["carimbo"]})

    def test_ocorrencias_antigas_filtradas_e_contadas(self):
        nomes = _split(self.tmp)
        _mudar(self.tmp, nomes[1], ocorrencias_antigas=[{"texto": "JOSE", "caixa": [1, 2, 3, 4], "categoria": "nome",
                                                         "motivo": "x"}])
        _mudar(self.tmp, nomes[2], ocorrencias_antigas=[])
        self.assertEqual([a["nome"] for a in dados.listar(self.tmp, "treino", sem_ocorrencias_antigas=True)],
                         [nomes[0], nomes[2], nomes[3]])
        self.assertEqual(datasheet.coletar(self.tmp)["splits"]["treino"]["com_ocorrencias_antigas"], 1)


# ---------------------------------------------------------------------- 9 e 10. alertas e notas do datasheet
class TestAlertasDataset(_ComSessao):
    def test_alertas(self):
        exportar.salvar(self.sessao(), self.saida)
        d = datasheet.coletar(self.saida)
        al = "\n".join(d["alertas"]["alertas"])
        for trecho in ("teste vazio", "validacao vazio", "documento(s) de origem", "modelo(s) de conta"):
            self.assertIn(trecho, al)
        md = datasheet.markdown(d)
        self.assertIn("## Alertas do dataset", md)
        self.assertIn("não use o caminho", md)

    def test_mesmo_modelo_em_splits_diferentes(self):
        exportar.salvar(self.sessao(), self.saida)
        quase = conta(90)
        quase[60:80, 14:200] = 246
        salvar(quase, self.dados / "teste" / "quase.png")
        reg = Registro(self.dados, self.saida, log=lambda *_: None)
        it = next(i for i in reg.por_split["teste"] if i.nome == "quase.png")
        s = Sessao(it, reg.carregar(it.doc), self.tmp / "rasc_q", IDS)
        s.aplicar("pincel", "valor", "", PINCEL, executar("pincel", s.atual, PINCEL, vazia(s.H, s.W)))
        exportar.salvar(s, self.saida)
        r = qualidade.alertas_dataset(self.saida)
        self.assertTrue(any("mesmo modelo" in x and "treino" in x and "teste" in x for x in r["alertas"]), r["alertas"])
        self.assertEqual(r["documentos"], 2)


if __name__ == "__main__":
    unittest.main()
