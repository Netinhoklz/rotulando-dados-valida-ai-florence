"""Falsificador humano (rotulador/falsificador.py): motivo → campos coerentes e valores combinados; a
ferramenta → só as técnicas dela, nos pesos dela; hábitos fixos na versão; a receita valida; prévia
e lote gravam quem fez e por quê, e o catálogo filtra por isso."""
import datetime as dt
import json
import random
import shutil
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from PIL import Image

from rotulador import catalogo, falsificador as fz
from rotulador.gerador import Plano, Receita
from rotulador.ops import geradores
from rotulador.sessao import ErroOperacao
from tests.test_auto import IDS, TEM_OCR, conta
from tests.test_tipos import CAMPOS, campo

HOJE = dt.date(2026, 10, 3)


class TestValores(unittest.TestCase):
    def test_formatos(self):
        d = dt.date(2026, 9, 14)
        self.assertEqual(geradores.formatar_data("Venc: 10/10/2026", d), "Venc: 14/09/2026")
        self.assertEqual(geradores.formatar_data("10.10.26", d), "14.09.26")
        self.assertIsNone(geradores.formatar_data("SET/2026", d))
        self.assertEqual(geradores.formatar_mes("SET/2026", d), "SET/2026")
        self.assertEqual(geradores.formatar_mes("set/26", dt.date(2026, 8, 1)), "ago/26")
        self.assertEqual(geradores.formatar_mes("08/2026", dt.date(2026, 1, 1)), "01/2026")
        self.assertEqual(geradores.como_o_antigo("MARIA SILVA", "Joao Lima"), "JOAO LIMA")
        self.assertEqual(geradores.arredondar_valor("R$ 1.187,43"), "R$ 1.187,00")
        self.assertEqual(geradores.arredondar_valor("342 kWh"), "342 kWh")


class TestNormalizar(unittest.TestCase):
    def test_padrao_e_erros(self):
        self.assertIsNone(fz.normalizar(None))
        self.assertIsNone(fz.normalizar({"prob": 0}))
        n = fz.normalizar({"prob": 0.5, "pesos": {"celular": 2}, "motivos": {"recencia": 1, "valor": 0}})
        self.assertEqual(n["prob"], 0.5)
        self.assertEqual(n["pesos"]["celular"], 2)
        self.assertEqual((n["motivos"]["valor"], n["motivos"]["recencia"], n["motivos"]["identidade"]), (0, 1, 0))
        self.assertEqual(n["pesos"]["paint"], 0)  # com a lista, quem não está nela fica de fora
        self.assertEqual(fz.normalizar({})["pesos"]["paint"], fz.FALSIFICADORES["paint"]["peso"])  # sem lista: padrões
        for ruim in ({"pesos": {"gimp": 1}}, {"motivos": {"vinganca": 1}}, {"pesos": {k: 0 for k in fz.FALSIFICADORES}}, "x"):
            with self.assertRaises(ValueError):
                fz.normalizar(ruim)
        with self.assertRaises(ErroOperacao):
            Receita({"tecnicas": {"campo.reescrever": 1}, "falsificadores": {"pesos": {"gimp": 1}}}, IDS)


class TestSortear(unittest.TestCase):
    def test_prob_ia_e_tecnicas_permitidas(self):
        rng = random.Random(1)
        todas = {t.id for t in catalogo.TECNICAS.values() if t.automatica}
        self.assertIsNone(fz.sortear(rng, {"prob": 0}, todas))
        nomes = Counter(fz.sortear(rng, {"prob": 1}, todas, ia_online=False).nome for _ in range(400))
        self.assertNotIn("ia", nomes)  # IA desligada: ninguém "usa IA"
        self.assertGreater(nomes["celular"], nomes["paint"])  # pesos padrão
        so_ia = fz.sortear(rng, {"prob": 1, "pesos": {"ia": 1}}, todas, ia_online=True)
        self.assertEqual(so_ia.nome, "ia")
        # ninguém da lista usa as técnicas permitidas: sorteio clássico
        self.assertIsNone(fz.sortear(rng, {"prob": 1, "pesos": {"editor_pdf": 1}}, {"codigo.qr"}))

    def test_habitos_fixos_e_canal(self):
        p = fz.Falsificador("celular", "recencia", random.Random(3))
        self.assertIn(p.habitos["fonte"], ("Roboto", "Segoe UI", "Arial", "Calibri"))
        self.assertEqual(p.habitos["colagem"], "retangulo")
        self.assertGreaterEqual(p.habitos["capricho"], 0.9)
        op = p.params("caractere.copiar_glifo")["opcoes"]
        self.assertEqual(op["colagem"], "retangulo")
        self.assertIn(op["apagar"], ("telea", "chapado", "borrao"))  # o apagador do app de celular, fixo na versão
        self.assertEqual(p.captura, "antes")  # quem edita no celular parte de uma foto
        self.assertEqual(p.params("campo.amador")["opcoes"]["fundo"], p.habitos["fundo"])
        self.assertEqual(p.params("campo.amador")["habitos"]["fonte"], p.habitos["fonte"])
        self.assertEqual(p.acabamento_de({"ruido": "auto", "disfarce": "auto", "jpeg": 0.2}),
                         {"ruido": "nunca", "disfarce": "nunca", "jpeg": 0.2})
        self.assertIn(p.canal_perfil, ("whatsapp", "tela", "celular", "nenhum"))
        self.assertIsNone(p.canal(None))  # receita sem canal: nada
        ps = fz.Falsificador("photoshop", "valor", random.Random(4))
        self.assertIn("disfarce", ps.habitos)
        self.assertEqual(ps.acabamento["ruido"], "sempre")
        pdf = fz.Falsificador("editor_pdf", "recencia", random.Random(5))
        self.assertTrue(pdf.habitos["nitido"])
        self.assertEqual(pdf.canal({"perfil": "misturado", "prob": 0.7}), None if pdf.canal_perfil == "nenhum"
                         else {"perfil": pdf.canal_perfil, "prob": 1.0})


def _pares(receita_dict):
    r = Receita(receita_dict, IDS)
    return r, Plano(r, random.Random(1)).candidatos(CAMPOS)


class TestPlanejar(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(7)
        self.todas = {"tecnicas": {t.id: 1 for t in catalogo.TECNICAS.values() if t.automatica and not t.precisa_ia}}

    def test_recencia_data_recente_e_mes_coerente(self):
        campos = CAMPOS + [campo(5, "mês de referência", "SET/2026"), campo(6, "data", "01/09/2026")]
        r = Receita(self.todas, IDS)
        pares = Plano(r, self.rng).candidatos(campos)
        with mock.patch.object(fz, "HOJE", lambda: HOJE):
            vistos = Counter()
            for sem in range(40):
                p = fz.Falsificador("editor_pdf", "recencia", random.Random(sem))
                plano = p.planejar(campos, pares, random.Random(sem), maximo=4, split="treino")
                self.assertTrue(plano)
                self.assertEqual(p.motivo, "recencia")
                datas = {}
                for t, f, texto in plano:
                    self.assertIn(f["categoria"], ("data", "mês de referência"))
                    vistos[f["categoria"]] += 1
                    if texto is None:
                        self.assertNotIn(t.id, fz.ACEITAM_TEXTO)
                        continue
                    self.assertIn(t.id, fz.ACEITAM_TEXTO)
                    if f["categoria"] == "data":
                        d = dt.datetime.strptime(texto, "%d/%m/%Y").date()
                        self.assertTrue(3 <= (HOJE - d).days <= 85, texto)
                        datas[f["id"]] = d
                    else:
                        datas["mes"] = texto
                if len([k for k in datas if k != "mes"]) == 2:
                    self.assertEqual(len({v for k, v in datas.items() if k != "mes"}), 1)  # a mesma data nos dois campos
                if "mes" in datas and len(datas) > 1:
                    d = next(v for k, v in datas.items() if k != "mes")
                    # o mês da leitura (3 a 25 dias antes do vencimento) ou o anterior: até 2 meses antes do vencimento
                    meses = {(d.replace(day=1) - dt.timedelta(days=31 * k - 15)).replace(day=1) for k in range(3)}
                    self.assertIn(datas["mes"], {geradores.formatar_mes("SET/2026", m) for m in meses})
        self.assertGreater(vistos["data"], vistos["mês de referência"])  # o mês entra em metade das vezes

    def test_identidade_mesmo_nome_e_esquecimento_do_cpf(self):
        campos = CAMPOS + [campo(5, "nome", "JOAO LIMA"), campo(6, "cpf/cnpj", "123.456.789-09")]
        r = Receita(self.todas, IDS)
        pares = Plano(r, self.rng).candidatos(campos)
        com_cpf = 0
        for sem in range(40):
            p = fz.Falsificador("editor_pdf", "identidade", random.Random(sem))
            plano = p.planejar(campos, pares, random.Random(sem), maximo=4, split="treino")
            nomes = {texto for t, f, texto in plano if f["categoria"] == "nome" and texto}
            self.assertLessEqual(len(nomes), 1, nomes)  # o mesmo titular nos dois lugares
            for n in nomes:
                self.assertEqual(n, n.upper())
                self.assertNotEqual(n, "JOAO LIMA")
            com_cpf += any(f["categoria"] == "cpf/cnpj" for _, f, _ in plano)
            self.assertTrue(all(f["categoria"] in ("nome", "cpf/cnpj") for _, f, _ in plano))
        self.assertTrue(8 < com_cpf < 36, com_cpf)  # às vezes esquece o CPF

    def test_ferramenta_restringe_tecnicas_e_motivo_cai_para_outro(self):
        r, pares = _pares(self.todas)
        p = fz.Falsificador("celular", "recencia", random.Random(2))
        plano = p.planejar(CAMPOS, pares, random.Random(2), maximo=3, split="treino")
        self.assertTrue(plano)
        for t, f, texto in plano:  # só as técnicas do celular; a data inteira só sai por quem escreve o campo todo
            self.assertIn(t.id, fz.FALSIFICADORES["celular"]["tecnicas"])
            if texto is not None:
                self.assertIn(t.id, fz.ACEITAM_TEXTO)
        # receita só com código novo: nenhum motivo cabe → sorteio clássico
        r2, pares2 = _pares({"tecnicas": {"codigo.regenerar": 1}, "categorias": {"codigo barras": 1}})
        p2 = fz.Falsificador("paint", "identidade", random.Random(2))
        self.assertEqual(p2.planejar(CAMPOS, pares2, random.Random(2), maximo=3), [])
        self.assertEqual(p2.motivo, "misto")
        # motivo "apagar" só usa o jeito de apagar
        p3 = fz.Falsificador("photoshop", "apagar", random.Random(9))
        plano3 = p3.planejar(CAMPOS, pares, random.Random(9), maximo=3, split="treino")
        self.assertTrue(plano3)
        apagar = set(next(j for j in catalogo.JEITOS if j["id"] == "apagar")["tecnicas"])
        self.assertTrue(all(t.id in apagar and texto is None for t, _, texto in plano3))

    def test_valor_redondo_quando_e_habito(self):
        r, pares = _pares(self.todas)
        redondos = total = 0
        for sem in range(30):
            p = fz.Falsificador("photoshop", "valor", random.Random(sem))
            for t, f, texto in p.planejar(CAMPOS, pares, random.Random(sem), maximo=2, split="treino"):
                if f["categoria"] == "valor" and texto:
                    total += 1
                    redondos += texto.endswith(",00")
                    self.assertEqual(texto.endswith(",00"), p.habitos["arredondar"])
        self.assertTrue(total and 0 < redondos < total)

    def test_pesos_do_plano_so_com_as_tecnicas_dela(self):
        r, pares = _pares({**self.todas, "distribuicao": "pesos"})
        plano = Plano(r, random.Random(1))
        p = fz.Falsificador("paint", "misto", random.Random(1))
        pesos = p.pesos_tecnicas({t.id for t, _ in pares})
        for _ in range(100):
            t, f = plano.escolher(list(pares), pesos)
            self.assertIn(t.id, pesos)


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestAPIFalsificador(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import SPLITS, Registro
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            (self.dados / sp).mkdir(parents=True)
            Image.fromarray(conta(40 + i)).save(self.dados / sp / f"c{i}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_previa_lote_catalogo_e_datasheet(self):
        cat = self.c.get("/api/catalogo").get_json()
        self.assertEqual([f["id"] for f in cat["falsificadores"]], list(fz.FALSIFICADORES))
        self.assertEqual([m["id"] for m in cat["motivos"]], list(fz.MOTIVOS))
        rec = {"falsificadores": {"prob": 1, "pesos": {"editor_pdf": 1}, "motivos": {"recencia": 1}}, "edicoes": [1, 3],
               "tecnicas": {"campo.reescrever": 1, "trecho.trocar": 1, "caractere.redesenhar": 1}}
        doc = self.reg.por_split["teste"][0].doc
        r = self.c.post("/api/gerador/previa", json={"doc": doc, "receita": rec})
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["falsificador"]["nome"], "editor_pdf")
        self.assertEqual(d["falsificador"]["motivo"], "recencia")
        self.assertTrue(d["edicoes"])
        for e in d["edicoes"]:
            self.assertEqual(e["categoria"], "data")
            if e["tecnica"] == "campo.reescrever":
                dia = dt.datetime.strptime(e["novo"], "%d/%m/%Y").date()
                self.assertLessEqual((dt.date.today() - dia).days, 90)
                self.assertIn("nítido", e["descricao"])
        rec = {**rec, "documentos": {"splits": ["validacao"]}, "variacoes": 2}
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(240):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertEqual(st["feitas"], 2, st)
        self.assertEqual(st["por_falsificador"], {"editor_pdf": 2})
        self.assertEqual(st["por_motivo"], {"recencia": 2})
        anots = [json.loads(a.read_text(encoding="utf-8")) for a in (self.saida / "validacao" / "anotacoes").glob("*.json")]
        self.assertTrue(all(a["falsificador"]["nome"] == "editor_pdf" and a["falsificador"]["habitos"]["nitido"] for a in anots))
        itens = self.c.get("/api/catalogo/itens?falsificador=editor_pdf&motivo=recencia").get_json()
        self.assertEqual(itens["total"], 2)
        self.assertEqual(self.c.get("/api/catalogo/itens?falsificador=celular").get_json()["total"], 0)
        ds = json.loads((self.saida / "datasheet.json").read_text(encoding="utf-8"))
        self.assertEqual(ds["splits"]["validacao"]["falsificadores"], {"editor_pdf": 2})
        self.assertIn("### Falsificadores e motivos", (self.saida / "DATASHEET.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
