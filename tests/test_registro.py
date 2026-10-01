import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from rotulador.registro import ErroVazamento, Registro
from tests.util import conta, salvar


class TestRegistro(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        salvar(conta(1), self.dados / "treino" / "a.png")
        salvar(conta(2), self.dados / "treino" / "sub" / "b.jpg", quality=90)
        salvar(conta(3), self.dados / "teste" / "c.png")
        salvar(conta(4), self.dados / "validacao" / "d.tif")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def reg(self):
        return Registro(self.dados, self.saida, log=lambda *_: None)

    def test_split_vem_da_pasta(self):
        r = self.reg()
        self.assertEqual([i.rel for i in r.por_split["treino"]], ["a.png", "sub/b.jpg"])
        self.assertEqual([i.split for i in r.por_split["teste"]], ["teste"])
        self.assertEqual(len(r.itens), 4)
        for it in r.itens.values():
            self.assertEqual(r.item(it.doc).split, it.split)

    def test_mesmo_arquivo_em_dois_splits_recusa(self):
        shutil.copy(self.dados / "treino" / "a.png", self.dados / "teste" / "copia.png")
        with self.assertRaises(ErroVazamento):
            self.reg()

    def test_mesmos_pixels_em_formato_diferente_recusa(self):
        # PNG no treino e TIFF (sem perdas) do mesmo conteúdo na validação
        salvar(conta(1), self.dados / "validacao" / "disfarcado.tif", compression="tiff_lzw")
        with self.assertRaises(ErroVazamento) as cm:
            self.reg()
        self.assertTrue(any("treino" in c and "validacao" in c for c in cm.exception.conflitos))

    def test_duplicata_no_mesmo_split_so_avisa(self):
        shutil.copy(self.dados / "treino" / "a.png", self.dados / "treino" / "a2.png")
        r = self.reg()
        self.assertEqual(len(r.por_split["treino"]), 2)
        self.assertTrue(any("duplicata" in a for a in r.avisos))

    def test_pdf_multipagina(self):
        paginas = [Image.fromarray(conta(10)), Image.fromarray(conta(11))]
        caminho = self.dados / "teste" / "conta.pdf"
        paginas[0].save(caminho, "PDF", save_all=True, append_images=paginas[1:], resolution=100)
        r = self.reg()
        pdf = [i for i in r.por_split["teste"] if i.rel == "conta.pdf"]
        self.assertEqual([i.pagina for i in pdf], [0, 1])
        self.assertTrue(all(i.paginas == 2 for i in pdf))
        img = r.carregar(pdf[1].doc)
        self.assertEqual(img.shape[2], 3)

    def test_documento_rotulado_movido_de_split_recusa(self):
        r = self.reg()
        it = r.por_split["treino"][0]
        (self.saida / "treino").mkdir(parents=True)
        with open(self.saida / "treino" / "manifesto.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, ["doc", "sha_arquivo", "sha_pixels"])
            w.writeheader()
            w.writerow({"doc": it.doc, "sha_arquivo": it.sha_arquivo, "sha_pixels": it.sha_pixels})
        shutil.move(self.dados / "treino" / "a.png", self.dados / "teste" / "a.png")
        with self.assertRaises(ErroVazamento):
            self.reg()

    def test_rascunho_em_outro_split_recusa(self):
        it = self.reg().por_split["teste"][0]
        (self.saida / "validacao" / "_rascunhos" / it.doc).mkdir(parents=True)
        with self.assertRaises(ErroVazamento):
            self.reg()

    def test_cache_nao_muda_resultado(self):
        a = {d: i.sha_pixels for d, i in self.reg().itens.items()}
        b = {d: i.sha_pixels for d, i in self.reg().itens.items()}
        self.assertEqual(a, b)

    def test_arquivo_corrompido_vira_aviso(self):
        (self.dados / "treino" / "quebrado.png").write_bytes(b"nao sou png")
        r = self.reg()
        self.assertTrue(any("quebrado.png" in a for a in r.avisos))


if __name__ == "__main__":
    unittest.main()
