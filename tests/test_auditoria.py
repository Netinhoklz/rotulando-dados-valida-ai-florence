import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app import RAIZ, carregar_categorias, criar_app
from auditar_vazamento import auditar
from rotulador.registro import SPLITS, Registro
from tests.util import conta, salvar

PINCEL = {"pontos": [[100, 300], [200, 300]], "tamanho": 12, "cor": "#ff0000"}


class TestAuditoria(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            salvar(conta(30 + i), self.dados / sp / f"a_{sp}.png")
            salvar(conta(40 + i), self.dados / sp / f"b_{sp}.png")
        reg = Registro(self.dados, self.saida, log=lambda *_: None)
        c = criar_app(reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()
        self.docs = {sp: [i.doc for i in reg.por_split[sp]] for sp in SPLITS}
        for sp in SPLITS:
            doc = self.docs[sp][0]
            for tipo, p, cat in (("pincel", PINCEL, "valor"),
                                 ("texto", {"texto": f"TEXTO {sp}", "x": 20, "y": 380, "tamanho": 16}, "nome")):
                r = c.post("/api/op", json={"id": doc, "tipo": tipo, "params": p, "categoria": cat})
                self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(c.post("/api/salvar", json={"id": doc}).status_code, 200)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dataset_limpo_passa(self):
        rel = auditar(self.dados, self.saida, limiar=0)
        self.assertEqual(rel["erros"], [])
        self.assertEqual(rel["anotacoes"], 3)

    def test_arquivo_copiado_para_outro_split_e_erro(self):
        doc = self.docs["treino"][0]
        destino = self.saida / "teste" / "editadas"
        shutil.copy(self.saida / "treino" / "editadas" / f"{doc}__v01.png", destino / "intruso.png")
        rel = auditar(self.dados, self.saida, limiar=0)
        self.assertTrue(any("mesmo arquivo de saída" in e for e in rel["erros"]), rel["erros"])

    def test_anotacao_em_pasta_errada_e_erro(self):
        doc = self.docs["treino"][0]
        shutil.copy(self.saida / "treino" / "anotacoes" / f"{doc}__v01.json",
                    self.saida / "validacao" / "anotacoes" / f"{doc}__v01.json")
        rel = auditar(self.dados, self.saida, limiar=0)
        self.assertTrue(any("diz split 'treino'" in e for e in rel["erros"]), rel["erros"])
        self.assertTrue(any("mais de um split" in e for e in rel["erros"]), rel["erros"])

    def test_doador_de_outro_split_e_erro(self):
        doc = self.docs["treino"][0]
        arq = self.saida / "treino" / "anotacoes" / f"{doc}__v01.json"
        a = json.loads(arq.read_text(encoding="utf-8"))
        a["operacoes"].append({"tipo": "colar", "params": {"doador": self.docs["teste"][1]}, "meta": {}})
        arq.write_text(json.dumps(a), encoding="utf-8")
        rel = auditar(self.dados, self.saida, limiar=0)
        self.assertTrue(any("doador do split 'teste'" in e for e in rel["erros"]), rel["erros"])

    def test_tecnica_com_letra_de_outro_split_e_erro(self):
        doc = self.docs["treino"][0]
        arq = self.saida / "treino" / "anotacoes" / f"{doc}__v01.json"
        a = json.loads(arq.read_text(encoding="utf-8"))
        a["operacoes"].append({"tipo": "tecnica", "params": {"tecnica": "caractere.glifo_externo"},
                               "meta": {"origem_glifo": {"doc": self.docs["teste"][1], "caixa": [0, 0, 5, 5]}}})
        arq.write_text(json.dumps(a), encoding="utf-8")
        rel = auditar(self.dados, self.saida, limiar=0)
        self.assertTrue(any("doador do split 'teste'" in e for e in rel["erros"]), rel["erros"])

    def test_texto_repetido_entre_splits_avisa(self):
        for sp in ("treino", "teste"):
            arq = self.saida / sp / "anotacoes" / f"{self.docs[sp][0]}__v01.json"
            a = json.loads(arq.read_text(encoding="utf-8"))
            a["operacoes"][1]["params"]["texto"] = "JOAO DA SILVA"
            arq.write_text(json.dumps(a), encoding="utf-8")
        rel = auditar(self.dados, self.saida, limiar=0)
        self.assertEqual(rel["erros"], [])
        self.assertTrue(any("JOAO DA SILVA" in w for w in rel["avisos"]))

    def test_nome_falso_de_tecnica_repetido_entre_splits_avisa(self):
        for sp in ("treino", "teste"):
            arq = self.saida / sp / "anotacoes" / f"{self.docs[sp][0]}__v01.json"
            a = json.loads(arq.read_text(encoding="utf-8"))
            a["edicoes"] = [{"categoria": "nome", "familia": "imitacao", "texto_novo": "LUCAS GOMES"},
                            {"categoria": "nome", "familia": "copia_interna", "texto_novo": "CLIENTE REAL"}]
            arq.write_text(json.dumps(a), encoding="utf-8")
        rel = auditar(self.dados, self.saida, limiar=0, clientes=False)
        self.assertTrue(any("LUCAS GOMES" in w for w in rel["avisos"]), rel["avisos"])
        self.assertFalse(any("CLIENTE REAL" in w for w in rel["avisos"]))  # cópia traz texto do próprio split

    def test_mesmo_cliente_em_splits_diferentes_e_erro(self):
        """O mesmo CPF (lido pelo OCR) em treino e teste é o mesmo cliente: o modelo decoraria o cliente."""
        from tests.test_auto import TEM_OCR, conta as conta_arial
        if not TEM_OCR:
            self.skipTest("OCR (rapidocr_onnxruntime) não instalado")
        salvar(conta_arial(1), self.dados / "treino" / "cliente_jan.png")  # contas de meses diferentes,
        salvar(conta_arial(2), self.dados / "teste" / "cliente_fev.png")   # mesmo CPF
        rel = auditar(self.dados, self.saida, limiar=0)
        self.assertTrue(any("mesmo CPF '12345678909'" in e and "cliente_fev.png" in e for e in rel["erros"]),
                        rel["erros"])
        self.assertEqual(auditar(self.dados, self.saida, limiar=0, clientes=False)["erros"], [])

    def test_quase_duplicata_entre_splits_e_erro(self):
        """O mesmo papel (ou o mesmo modelo de conta) em splits diferentes: o teste mediria o layout que o treino viu.
        Era só aviso; agora é erro."""
        quase = conta(30)
        quase[5:8, 5:8] = 0  # muda uns pixels: hash exato difere, dHash não
        salvar(quase, self.dados / "validacao" / "quase.png")
        rel = auditar(self.dados, self.saida, limiar=10, clientes=False)
        self.assertTrue(any("mesmo modelo de conta" in e and "quase.png" in e and "a_treino.png" in e
                            for e in rel["erros"]), rel["erros"])
        self.assertGreaterEqual(rel["quase_duplicatas"], 1)


if __name__ == "__main__":
    unittest.main()
