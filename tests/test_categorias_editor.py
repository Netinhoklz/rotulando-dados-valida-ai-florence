"""As 3 categorias do editor (copiar e colar, pegar de outro documento, apagar e substituir): o servidor as dá à tela,
a edição feita à mão vai para a máscara de classes com o id delas, e o categorias.csv de cada split diz de qual
categoria é cada imagem salva (as do gerador, pela família da técnica), sempre no split da imagem de origem."""
import csv
import json
import unittest

import numpy as np
from PIL import Image

from rotulador import catalogo, exportar
from tests.test_planilha import BaseApp

IDS = {c["chave"]: c["id"] for c in catalogo.CATEGORIAS_EDITOR}


def ler_csv(caminho):
    return list(csv.DictReader(caminho.read_text(encoding="utf-8").splitlines()))


class TestCategoriasEditor(BaseApp):
    def editar_e_salvar(self, it, categoria):
        """Como a tela faz: abre, passa o pincel com o tipo escolhido e salva."""
        x0, y0, x1, y1 = self.campo(it, "nome")["caixa"]
        self.assertEqual(self.c.post("/api/abrir", json={"id": it.doc}).status_code, 200)
        pincel = {"pontos": [[x0 + 8, (y0 + y1) // 2], [x0 + 70, (y0 + y1) // 2]], "tamanho": 12, "cor": "#fbfbf9"}
        r = self.c.post("/api/op", json={"id": it.doc, "tipo": "pincel", "params": pincel, "categoria": categoria})
        self.assertEqual(r.status_code, 200, r.get_json())
        r = self.c.post("/api/salvar", json={"id": it.doc, "formato": "png"})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["salvo"]

    def test_estado_traz_as_3_fora_dos_ids_de_campo(self):
        e = self.c.get("/api/estado").get_json()
        self.assertEqual([c["chave"] for c in e["categorias_editor"]],
                         ["copiar e colar", "pegar de outro documento", "apagar e substituir"])
        self.assertTrue(set(IDS.values()).isdisjoint(c["id"] for c in e["categorias"]))

    def test_edicao_a_mao_vai_para_a_mascara_e_para_o_csv_do_split(self):
        for sp, cat in (("teste", "copiar e colar"), ("validacao", "apagar e substituir")):
            it = self.doc(sp)
            salvo = self.editar_e_salvar(it, cat)
            self.assertEqual((salvo["split"], salvo["categorias"]), (sp, [cat]))
            classes = np.asarray(Image.open(self.saida / sp / salvo["arquivos"]["mascara_classes"]))
            self.assertEqual(set(np.unique(classes).tolist()) - {0}, {IDS[cat]})
            linhas = ler_csv(self.saida / sp / "categorias.csv")
            self.assertEqual([(l["documento"], l["categoria"], l["origem"]) for l in linhas], [(it.doc, cat, "manual")])
            for k in ("editada", "original", "mascara", "anotacao"):  # os caminhos são relativos à pasta do split
                self.assertTrue((self.saida / sp / linhas[0][k]).is_file(), k)
        self.assertFalse((self.saida / "treino" / "categorias.csv").exists())  # nada do teste vai para o treino
        legenda = json.loads((self.saida / "catalogo.json").read_text(encoding="utf-8"))["categorias"]
        self.assertEqual((legenda["copiar e colar"], legenda["apagar e substituir"], legenda["nome"]), (201, 203, 1))
        # excluir a versão tira a linha do CSV
        nome = ler_csv(self.saida / "teste" / "categorias.csv")[0]["versao"]
        self.assertEqual(self.c.post("/api/catalogo/excluir", json={"split": "teste", "nome": nome}).status_code, 200)
        self.assertEqual(ler_csv(self.saida / "teste" / "categorias.csv"), [])

    def test_legenda_soma_as_classes_de_quem_salva(self):
        self.saida.mkdir(parents=True, exist_ok=True)
        (self.saida / "catalogo.json").write_text(json.dumps({"categorias": {"copiar e colar": 201}}), encoding="utf-8")
        exportar._legenda(self.saida, {"nome": 1})  # o gerador salva só com as de campo
        legenda = json.loads((self.saida / "catalogo.json").read_text(encoding="utf-8"))["categorias"]
        self.assertEqual(legenda, {"copiar e colar": 201, "nome": 1})


class TestCategoriaDaVersao(unittest.TestCase):
    def test_gerador_pela_familia_e_editor_pela_escolha(self):
        anot = {"edicoes": [{"familia": "copia_interna"}, {"familia": "imitacao"}, {"familia": "copia_interna"},
                            {"familia": "colagem_externa"}, {"familia": "manual", "categoria": "nome"},
                            {"familia": "manual", "categoria": "pegar de outro documento"}]}
        self.assertEqual(exportar.categorias_da_versao(anot),
                         ["copiar e colar", "apagar e substituir", "pegar de outro documento"])
        # edição à mão salva antes das 3 categorias (com a classe do campo) não diz qual foi
        self.assertEqual(exportar.categorias_da_versao({"edicoes": [{"familia": "manual", "categoria": "valor"}]}), [])


if __name__ == "__main__":
    unittest.main()
