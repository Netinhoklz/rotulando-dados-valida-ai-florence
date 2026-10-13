"""Trabalho em equipe: trava por documento, atribuição, revisão humana, exportação filtrada, metas,
pré-leitura do OCR e progresso/cancelamento da IA."""
import csv
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

from app import RAIZ, carregar_categorias, criar_app
from rotulador.equipe import Travas
from rotulador.registro import SPLITS, Registro
from tests.util import conta, salvar

PINCEL = {"pontos": [[100, 300], [200, 300]], "tamanho": 12, "cor": "#ff0000"}


def cab(nome):
    return {"X-Operador": urllib.parse.quote(nome)}


class TestTravas(unittest.TestCase):
    def test_pegar_conflito_vencer_assumir_soltar(self):
        agora = [1000.0]
        t = Travas(validade_s=60, relogio=lambda: agora[0])
        self.assertIsNone(t.pegar("d", "Ana"))
        self.assertEqual(t.pegar("d", "Bia")["operador"], "Ana")  # Ana está com ele
        self.assertIsNone(t.conflito("d", "Ana"))
        self.assertEqual(t.conflito("d", "Bia")["operador"], "Ana")
        agora[0] += 61  # sem sinal: vence
        self.assertIsNone(t.pegar("d", "Bia"))
        self.assertIsNone(t.pegar("d", "Ana", forcar=True))  # assumir
        self.assertEqual(t.dono("d")["assumida_de"], "Bia")
        t.soltar("d", "Bia")  # só o dono solta
        self.assertEqual(t.dono("d")["operador"], "Ana")
        t.soltar("d", "Ana")
        self.assertIsNone(t.dono("d"))

    def test_sem_nome_nao_pega_nem_e_barrado(self):
        from rotulador.equipe import ANONIMO
        t = Travas(validade_s=60, relogio=lambda: 1000.0)
        self.assertIsNone(t.conflito("d", ANONIMO))  # script de apoio editando: não prende o documento
        self.assertIsNone(t.dono("d"))
        self.assertIsNone(t.pegar("d", "Ana"))
        self.assertIsNone(t.conflito("d", ANONIMO))  # nem é barrado por quem está na tela
        self.assertEqual(t.dono("d")["operador"], "Ana")


class TestEquipeAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            salvar(conta(70 + i), self.dados / sp / f"a_{sp}.png")
            salvar(conta(80 + i), self.dados / sp / f"b_{sp}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()
        self.doc = self.reg.por_split["treino"][0].doc

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, rota, quem="Ana", **corpo):
        return self.c.post(rota, json=corpo, headers=cab(quem))

    def editar_e_salvar(self, doc, quem="Ana"):
        self.assertEqual(self.post("/api/abrir", quem, id=doc).status_code, 200)
        r = self.post("/api/op", quem, id=doc, tipo="pincel", params=PINCEL, categoria="valor")
        self.assertEqual(r.status_code, 200, r.get_json())
        r = self.post("/api/salvar", quem, id=doc)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["salvo"]

    def test_trava_entre_duas_pessoas(self):
        r = self.post("/api/abrir", "Ana", id=self.doc).get_json()
        self.assertIsNone(r["travado_por"])
        r = self.post("/api/abrir", "Bia Souza", id=self.doc).get_json()
        self.assertEqual(r["travado_por"]["operador"], "Ana")  # abre só para olhar
        r = self.post("/api/op", "Bia Souza", id=self.doc, tipo="pincel", params=PINCEL, categoria="valor")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Ana", r.get_json()["erro"])
        # a prévia não mexe na sessão: quem olha pode ver
        lista = self.c.get("/api/documentos?split=treino", headers=cab("Bia Souza")).get_json()
        self.assertEqual(next(d for d in lista if d["id"] == self.doc)["em_edicao_por"], "Ana")
        lista = self.c.get("/api/documentos?split=treino", headers=cab("Ana")).get_json()
        self.assertIsNone(next(d for d in lista if d["id"] == self.doc)["em_edicao_por"])  # é ela mesma
        # Bia assume; agora é a Ana quem não edita
        self.assertIsNone(self.post("/api/abrir", "Bia Souza", id=self.doc, forcar=True).get_json()["travado_por"])
        self.assertEqual(self.post("/api/op", "Bia Souza", id=self.doc, tipo="pincel", params=PINCEL,
                                   categoria="valor").status_code, 200)
        self.assertEqual(self.post("/api/desfazer", "Ana", id=self.doc).status_code, 409)
        self.assertEqual(self.post("/api/salvar", "Ana", id=self.doc).status_code, 409)
        self.assertFalse(self.post("/api/trava/ping", "Ana", id=self.doc).get_json()["ok"])
        self.assertTrue(self.post("/api/trava/ping", "Bia Souza", id=self.doc).get_json()["ok"])
        self.post("/api/trava/soltar", "Bia Souza", id=self.doc)
        self.assertEqual(self.post("/api/desfazer", "Ana", id=self.doc).status_code, 200)  # livre de novo

    def test_atribuicao_e_painel_da_equipe(self):
        ids = [it.doc for it in self.reg.por_split["teste"]]
        r = self.post("/api/equipe/atribuir", "Ana", ids=ids, operador="Bia")
        self.assertEqual(r.status_code, 200)
        lista = self.c.get("/api/documentos?split=teste").get_json()
        self.assertTrue(all(d["responsavel"] == "Bia" for d in lista))
        self.editar_e_salvar(self.doc, "Ana")
        eq = self.c.get("/api/equipe", headers=cab("Ana")).get_json()
        self.assertIn("Bia", eq["operadores"])
        self.assertEqual(eq["por_operador"]["Ana"]["versoes"], 1)
        self.assertEqual(sum(1 for d in eq["documentos"] if d["responsavel"] == "Bia"), 2)
        self.post("/api/equipe/atribuir", "Ana", ids=ids, operador="")  # tira
        self.assertTrue(all(d["responsavel"] is None for d in self.c.get("/api/documentos?split=teste").get_json()))
        self.assertTrue((self.saida / "_equipe" / "atribuicoes.json").is_file())

    def test_revisao_fila_filtros_e_exportacao(self):
        v1 = self.editar_e_salvar(self.doc, "Ana")
        outro = self.reg.por_split["treino"][1].doc
        v2 = self.editar_e_salvar(outro, "Ana")
        fila = self.c.get("/api/revisao/fila").get_json()
        self.assertEqual(fila["total"], 2)
        self.assertIn("artefato", fila["motivos"])
        nome1 = f"{self.doc}__v{v1['versao']:02d}"
        nome2 = f"{outro}__v{v2['versao']:02d}"
        r = self.post("/api/revisao", "Bia", itens=[{"split": "treino", "nome": nome1}], estado="reprovada")
        self.assertEqual(r.status_code, 400)  # reprovar pede motivo
        self.assertEqual(self.post("/api/revisao", "Bia", itens=[{"split": "treino", "nome": nome1}], estado="aprovada")
                         .status_code, 200)
        self.assertEqual(self.post("/api/revisao", "Bia", itens=[{"split": "treino", "nome": nome2}], estado="reprovada",
                                   motivos=["artefato"], comentario="caixa visível").status_code, 200)
        a = json.loads((self.saida / "treino" / "anotacoes" / f"{nome2}.json").read_text(encoding="utf-8"))
        self.assertEqual((a["revisao"]["estado"], a["revisao"]["por"], a["revisao"]["motivos"]), ("reprovada", "Bia", ["artefato"]))
        self.assertEqual(self.c.get("/api/revisao/fila").get_json()["total"], 0)
        self.assertEqual(self.c.get("/api/revisao/fila?revisao=todas").get_json()["contagem"],
                         {"aprovada": 1, "reprovada": 1})
        itens = self.c.get("/api/catalogo/itens?revisao=aprovada").get_json()["itens"]
        self.assertEqual([v["nome"] for v in itens], [nome1])
        with open(self.saida / "treino" / "manifesto.csv", encoding="utf-8") as f:
            self.assertEqual(sorted(l["revisao"] for l in csv.DictReader(f)), ["aprovada", "reprovada"])
        eq = self.c.get("/api/equipe").get_json()
        self.assertEqual(eq["por_operador"]["Bia"]["revisoes"], 2)
        # exportação só das aprovadas
        r = self.post("/api/catalogo/exportar", "Ana", nome="so aprovadas", filtros={"revisao": "aprovada"}).get_json()
        self.assertEqual(r["versoes"], 1)
        resp = self.c.get("/api/catalogo/exportacao/so_aprovadas/manifesto.csv")
        csv_txt = resp.get_data(as_text=True)
        resp.close()
        linhas = list(csv.DictReader(io.StringIO(csv_txt)))
        self.assertEqual(len(linhas), 1)
        self.assertTrue(linhas[0]["editada"].startswith("treino/editadas/"))
        self.assertTrue((self.saida / linhas[0]["editada"]).is_file())
        self.assertEqual(self.post("/api/catalogo/exportar", "Ana", nome="nada", filtros={"revisao": "pendente"}).status_code, 400)
        # voltar para pendente
        self.post("/api/revisao", "Bia", itens=[{"split": "treino", "nome": nome1}], estado="pendente")
        self.assertEqual(self.c.get("/api/revisao/fila").get_json()["total"], 1)

    def test_filtros_canal_e_exceto_tecnica(self):
        self.editar_e_salvar(self.doc, "Ana")
        self.assertEqual(self.c.get("/api/catalogo/itens?canal=com").get_json()["total"], 0)
        self.assertEqual(self.c.get("/api/catalogo/itens?canal=sem").get_json()["total"], 1)
        tec = self.c.get("/api/catalogo/itens").get_json()["itens"][0]["edicoes"][0]["tecnica"]
        self.assertEqual(self.c.get(f"/api/catalogo/itens?exceto_tecnica={tec}").get_json()["total"], 0)

    def test_metas_e_progresso(self):
        r = self.post("/api/metas", "Ana", classes={"treino": {"valor": 3, "data": 2}, "teste": {"valor": 1}})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.editar_e_salvar(self.doc, "Ana")  # uma edição de valor no treino
        p = self.c.get("/api/metas").get_json()
        self.assertEqual(p["feito"]["treino"]["valor"], 1)
        self.assertEqual(p["falta"]["treino"], {"valor": 2, "data": 2})
        self.assertEqual(p["falta"]["teste"], {"valor": 1})
        self.assertEqual(self.post("/api/metas", "Ana", classes={"treino": {"inexistente": 1}}).status_code, 400)
        # só aprovadas contam
        self.post("/api/metas", "Ana", classes={"treino": {"valor": 3}}, so_aprovadas=True)
        self.assertEqual(self.c.get("/api/metas").get_json()["falta"]["treino"]["valor"], 3)

    def test_preleitura_do_ocr(self):
        from tests.test_auto import TEM_OCR
        if not TEM_OCR:
            self.skipTest("OCR não instalado")
        prox = self.reg.por_split["treino"][1]
        r = self.post("/api/campos/preler", "Ana", ids=[prox.doc]).get_json()
        self.assertEqual(r["lendo"], [prox.doc])
        cache = self.saida / "_cache" / "ocr" / f"{prox.sha_pixels[:32]}.json"
        for _ in range(100):
            if cache.is_file():
                break
            time.sleep(0.2)
        self.assertTrue(cache.is_file())


class TestIAProgresso(unittest.TestCase):
    """Rotas /progresso e /cancelar do servidor de IA, com um motor falso (sem torch)."""

    def test_progresso_e_cancelar(self):
        import base64

        import ia_servidor
        from PIL import Image

        class MotorFalso(ia_servidor.MotorBase):
            id, nome, passos_padrao = "falso", "falso", 4

            def editar(self, imagem, prompt, semente, passos, altura, largura):
                self.ocupado = True
                try:
                    self.progresso.update(etapa="difusão", passo=0, passos=passos, inicio_difusao=time.time())
                    for i in range(passos):
                        time.sleep(0.15)
                        self.progresso.update(passo=i + 1)
                        if self.cancelar.is_set():
                            raise ia_servidor.Cancelado()
                    return imagem, {"modelo": "falso"}
                finally:
                    self.ocupado = False

        motor = MotorFalso()
        gerente = type("G", (), {"atual": motor, "motores": {"falso": motor}, "motor": lambda self, _=None: motor})()
        c = ia_servidor.criar_app(gerente).test_client()
        buf = io.BytesIO()
        Image.new("RGB", (32, 32)).save(buf, "PNG")
        pedido = {"imagem": base64.b64encode(buf.getvalue()).decode(), "prompt": "x", "passos": 20}
        resp = {}
        t = threading.Thread(target=lambda: resp.update(r=c.post("/editar", json=pedido)))
        t.start()
        time.sleep(0.5)
        p = c.get("/progresso").get_json()
        self.assertTrue(p["ocupado"])
        self.assertEqual(p["etapa"], "difusão")
        self.assertGreater(p["passo"], 0)
        self.assertIn("restante_s", p)
        self.assertTrue(c.post("/cancelar").get_json()["ocupado"])
        t.join(10)
        self.assertEqual(resp["r"].status_code, 409)
        self.assertIn("cancelado", resp["r"].get_json()["erro"])
        self.assertFalse(c.get("/progresso").get_json()["ocupado"])
        # um pedido novo não herda o cancelamento
        pedido["passos"] = 2
        self.assertEqual(c.post("/editar", json=pedido).status_code, 200)


if __name__ == "__main__":
    unittest.main()
