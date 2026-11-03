"""O estúdio PhotoCraft: abrir, salvar (a máscara é a diferença EXATA entre o salvo e o aberto, 1 = editado),
salvar de novo substitui, documento novo entra no dataset num split escolhido sozinho, a planilha do dataset e as rotas.

Os documentos são os sintéticos de test_tecnicas, com o OCR gravado no cache (os campos não dependem do motor de OCR).
O PSD e a aba Gerar só rodam com o ``photocraft-cli`` instalado."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from openpyxl import load_workbook
from PIL import Image

from app import RAIZ, carregar_categorias, criar_app
from rotulador import ocr, photocraft, planilha
from rotulador.contexto import Contexto, cache_ocr
from rotulador.registro import SPLITS, Registro
from rotulador.sessao import ErroOperacao
from tests.test_tecnicas import documento

NOMES = {"treino": "MARIA DOS SANTOS SILVA", "teste": "LUCAS PINTO GOMES", "validacao": "PEDRO ALVES COSTA"}


def png_bytes(img: np.ndarray, nivel: int = 6) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG", compress_level=nivel)
    return buf.getvalue()


class BaseEstudio(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        linhas = {}
        for i, sp in enumerate(SPLITS):
            img, linhas[sp] = documento(nome=NOMES[sp], semente=i + 1)
            (self.dados / sp).mkdir(parents=True)
            Image.fromarray(img).save(self.dados / sp / f"conta_{sp}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        for sp in SPLITS:
            ocr.gravar_cache(cache_ocr(self.saida, self.reg.por_split[sp][0].sha_pixels), linhas[sp])
        self.app = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json"))
        self.c = self.app.test_client()
        self.est = self.app.estudio

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def doc(self, sp="teste"):
        return self.reg.por_split[sp][0]

    def campo(self, it, categoria):
        ctx = Contexto(it, self.reg.carregar(it.doc), self.saida, self.reg)
        return next(f for f in ctx.campos if f.get("categoria") == categoria)

    def apagar_trecho(self, it, categoria="nome"):
        """O original com um pedaço do campo coberto de papel (o que a pessoa faria com o pincel)."""
        base = self.reg.carregar(it.doc)
        x0, y0, x1, y1 = self.campo(it, categoria)["caixa"]
        ed = base.copy()
        ed[y0 + 4:y1 - 4, x0 + 8:x0 + 70] = (251, 251, 249)
        return base, ed

    def anotacao(self, r):
        return json.loads((self.saida / r["split"] / r["arquivos"]["anotacao"]).read_text(encoding="utf-8"))

    def mascara(self, r):
        return np.asarray(Image.open(self.saida / r["split"] / r["arquivos"]["mascara"]))


class TestSalvar(BaseEstudio):
    def test_mascara_marca_1_onde_editou(self):
        it = self.doc("teste")
        a = self.est.abrir(it.doc)
        self.assertEqual((a["split"], a["doc"]), ("teste", it.doc))
        self.assertTrue(a["arquivo"].endswith(f"[{a['token']}].png"))
        base, ed = self.apagar_trecho(it)
        r = self.est.salvar(a["arquivo"], png_bytes(ed), [], "ana")
        self.assertEqual((r["split"], r["doc"]), ("teste", it.doc))  # a página passa para o documento seguinte a este
        self.assertEqual(len(r["manuais"]), 1)  # editado à mão: o PhotoCraft vai perguntar se salva ao fechar
        m = self.mascara(r)
        self.assertEqual(r["pixels_mascara"], int(m.sum()))
        self.assertEqual(set(np.unique(m).tolist()), {0, 1})
        np.testing.assert_array_equal(m == 1, (ed != base).any(2))  # exatamente onde o pixel mudou
        editada = np.asarray(Image.open(self.saida / "teste" / r["arquivos"]["editada"]).convert("RGB"))
        np.testing.assert_array_equal(editada, ed)
        regiao = np.asarray(Image.open(self.saida / "teste" / r["arquivos"]["mascara_regiao"]))
        self.assertEqual(set(np.unique(regiao).tolist()), {0, 1})
        anot = self.anotacao(r)
        self.assertEqual([e["tecnica"] for e in anot["edicoes"]], ["manual.photocraft"])
        e = anot["edicoes"][0]
        self.assertEqual((e["categoria"], e["texto_antigo"]), ("nome", NOMES["teste"]))
        self.assertEqual(anot["estudio"]["token"], a["token"])
        self.assertEqual((anot["origem"], anot["operador"]), ("manual", "ana"))
        for sp in ("treino", "validacao"):  # nada nos outros splits
            self.assertFalse((self.saida / sp / "editadas").exists(), sp)

    def test_salvar_de_novo_substitui_e_nova_abertura_e_nova_versao(self):
        it = self.doc("treino")
        a = self.est.abrir(it.doc)
        base, ed = self.apagar_trecho(it)
        r1 = self.est.salvar(a["arquivo"], png_bytes(ed))
        ed2 = ed.copy()
        x0, y0, x1, y1 = self.campo(it, "valor")["caixa"]
        ed2[y0 + 4:y1 - 4, x0 + 6:x0 + 40] = (250, 250, 250)
        r2 = self.est.salvar(a["arquivo"], png_bytes(ed2))
        self.assertEqual(r2["substituiu"], r1["nome"])
        anots = sorted((self.saida / "treino" / "anotacoes").glob("*.json"))
        self.assertEqual(len(anots), 1)
        self.assertEqual({e["categoria"] for e in self.anotacao(r2)["edicoes"]}, {"nome", "valor"})
        np.testing.assert_array_equal(self.mascara(r2) == 1, (ed2 != base).any(2))
        b = self.est.abrir(it.doc)
        self.assertNotEqual(b["token"], a["token"])
        r3 = self.est.salvar(b["arquivo"], png_bytes(ed))
        self.assertIsNone(r3["substituiu"])
        self.assertEqual(len(list((self.saida / "treino" / "anotacoes").glob("*.json"))), 2)

    def test_sem_token_no_nome_acha_pelo_conteudo(self):
        it = self.doc("validacao")
        a = self.est.abrir(it.doc)
        outro = self.est.abrir(self.doc("teste").doc)
        _, ed = self.apagar_trecho(it)
        r = self.est.salvar("Sem título.png", png_bytes(ed), [outro["token"], a["token"]])
        self.assertEqual((r["token"], r["split"]), (a["token"], "validacao"))
        with self.assertRaises(ErroOperacao):  # nada aberto pelo estúdio bate com o arquivo
            self.est.salvar("Sem título.png", png_bytes(ed), [outro["token"]])

    def test_recusas(self):
        it = self.doc("teste")
        a = self.est.abrir(it.doc)
        base, ed = self.apagar_trecho(it)
        with self.assertRaisesRegex(ErroOperacao, "perda"):
            self.est.salvar(a["arquivo"].replace(".png", ".jpg"), png_bytes(ed))
        with self.assertRaisesRegex(ErroOperacao, "tamanho"):
            self.est.salvar(a["arquivo"], png_bytes(ed[:-10]))
        with self.assertRaisesRegex(ErroOperacao, "nada foi editado"):
            self.est.salvar(a["arquivo"], png_bytes(base))
        outro = self.reg.carregar(self.doc("treino").doc)  # outro documento inserido por cima (o mesmo tamanho)
        with self.assertRaisesRegex(ErroOperacao, "da página mudou"):
            self.est.salvar(a["arquivo"], png_bytes(outro))
        self.assertFalse((self.saida / "teste" / "anotacoes").exists())

    def test_rascunho_velho_de_versao_gerada_sai(self):
        velho, novo = self.est.pasta / "0123456789abcdef__a1", self.est.pasta / "0123456789abcdef__a2"
        for p in (velho, novo):
            p.mkdir(parents=True)
            (p / "estado.json").write_text("{}", encoding="utf-8")
        antes = time.time() - 8 * 86400
        os.utime(velho, (antes, antes))
        self.est._limpar()
        self.assertFalse(velho.exists())
        self.assertTrue(novo.exists())

    def test_fora_dos_campos_vira_outro(self):
        it = self.doc("teste")
        a = self.est.abrir(it.doc)
        base = self.reg.carregar(it.doc)
        ed = base.copy()
        ed[700:720, 1150:1200] = 0  # canto sem texto
        r = self.est.salvar(a["arquivo"], png_bytes(ed))
        e = self.anotacao(r)["edicoes"][0]
        self.assertEqual((e["categoria"], e["tecnica"]), ("outro", "manual.photocraft"))
        np.testing.assert_array_equal(self.mascara(r) == 1, (ed != base).any(2))


class TestDocumentoNovo(BaseEstudio):
    def test_entra_no_split_que_falta_e_nao_duplica(self):
        img, _ = documento(nome="ANA LIMA", semente=9)
        a = self.est.registrar_arquivo("Conta Nova.png", png_bytes(img))
        # um arquivo em cada split: o treino é o que está mais abaixo dos 70%
        self.assertTrue(a["novo"])
        self.assertEqual(a["split"], "treino")
        self.assertTrue((self.dados / "treino" / "photocraft" / "Conta Nova.png").is_file())
        self.assertIn(a["doc"], {i.doc for i in self.reg.por_split["treino"]})
        b = self.est.registrar_arquivo("copia.png", png_bytes(img))
        self.assertEqual((b["doc"], b["novo"]), (a["doc"], False))
        # o mesmo documento em outro arquivo (pixels iguais, bytes diferentes): o item que já existe, no split dele
        it = self.doc("validacao")
        c = self.est.registrar_arquivo("x.png", png_bytes(self.reg.carregar(it.doc), nivel=1))
        self.assertEqual((c["doc"], c["split"], c["novo"]), (it.doc, "validacao", False))
        # e o registro novo continua de pé depois de reiniciar (o arquivo está em dados/treino)
        reg2 = Registro(self.dados, self.saida, log=lambda *_: None)
        self.assertEqual(reg2.item(a["doc"]).split, "treino")

    def test_formato_que_nao_e_documento(self):
        with self.assertRaises(ErroOperacao):
            self.est.registrar_arquivo("notas.txt", b"oi")


class TestPlanilha(BaseEstudio):
    def test_abas_e_caminhos(self):
        it = self.doc("teste")
        a = self.est.abrir(it.doc)
        _, ed = self.apagar_trecho(it)
        r = self.est.salvar(a["arquivo"], png_bytes(ed))
        info = planilha.escrever(self.saida, self.reg)
        self.assertIsNone(info["aviso"])
        wb = load_workbook(info["arquivo"])
        self.assertEqual(wb.sheetnames, ["Resumo", "Versões", "Edições", "Documentos", "Legenda"])
        ws = wb["Versões"]
        cab = [c.value for c in ws[1]]
        linha = [c.value for c in ws[2]]
        self.assertEqual(ws.max_row, 2)
        self.assertEqual((linha[cab.index("Split")], linha[cab.index("Versão")]), ("Teste", r["nome"]))
        col = cab.index("Máscara (1 = editado)")
        self.assertTrue((self.saida / linha[col]).is_file())
        self.assertEqual(ws.cell(row=2, column=col + 1).hyperlink.target, linha[col])
        self.assertTrue(linha[cab.index("Editada")].startswith("teste/editadas/"))
        ed_ws = wb["Edições"]
        cab_e = [c.value for c in ed_ws[1]]
        e = [c.value for c in ed_ws[2]]
        self.assertEqual((e[cab_e.index("Campo editado")], e[cab_e.index("Texto antigo")]), ("nome", NOMES["teste"]))
        self.assertEqual(wb["Documentos"].max_row, 4)  # um documento por split
        resumo = [[c.value for c in row] for row in wb["Resumo"].iter_rows()]
        teste = next(r_ for r_ in resumo if r_[0] == "Teste")
        self.assertEqual(teste[2:6], [1, 1, 1, 1])
        legenda = [[c.value for c in row] for row in wb["Legenda"].iter_rows(min_row=2)]
        self.assertIn(["mascaras/", 1, "pixel editado (o valor mudou)"], legenda)
        wb.close()

    def test_planilha_aberta_no_excel_vai_para_outro_nome(self):
        (self.saida).mkdir(parents=True, exist_ok=True)
        with open(self.saida / planilha.NOME, "wb") as f:
            f.write(b"travado")
            import msvcrt  # Windows: trava o arquivo como o Excel faz
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            info = planilha.escrever(self.saida, self.reg)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        self.assertTrue(info["aviso"])


class TestRotas(BaseEstudio):
    def test_pagina_e_api(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"estudio.js", r.data)
        self.assertEqual(self.c.get("/classico").status_code, 200)
        e = self.c.get("/api/estudio/estado").get_json()
        self.assertEqual(e["splits"]["teste"]["documentos"], 1)
        it = self.doc("teste")
        a = self.c.post("/api/estudio/abrir", json={"doc": it.doc}).get_json()
        _, ed = self.apagar_trecho(it)
        r = self.c.post("/api/estudio/salvar", data={"arquivo": (io.BytesIO(png_bytes(ed)), a["arquivo"]),
                                                     "nome": a["arquivo"], "abertos": json.dumps([a["token"]])},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["split"], "teste")
        r = self.c.post("/api/estudio/salvar", data={"arquivo": (io.BytesIO(b"x"), "a.jpg"), "nome": "a.jpg"},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        x = self.c.get("/api/estudio/excel")
        self.assertEqual(x.status_code, 200)
        self.assertEqual(x.data[:2], b"PK")  # xlsx é um zip
        x.close()
        img, _ = documento(nome="RITA", semente=5)
        r = self.c.post("/api/estudio/arquivo", data={"arquivo": (io.BytesIO(png_bytes(img)), "nova.png")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        p = self.c.get(r.get_json()["png"])
        np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(p.data)).convert("RGB")), img)
        p.close()

    @unittest.skipUnless(photocraft.caminho_web(), "PhotoCraft web não instalado")
    def test_photocraft_web_com_os_ganchos(self):
        r = self.c.get("/photocraft/")
        self.assertRegex(r.get_data(as_text=True), r'<head>\s*<script src="/static/estudio_ganchos\.js\?v=\d+"></script>')
        self.assertEqual(r.headers["Cache-Control"], "no-cache")
        web = photocraft.caminho_web()
        wasm = next(web.glob("*.wasm")).name
        r = self.c.get(f"/photocraft/{wasm}")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/wasm"))
        r.close()
        r = self.c.get("/photocraft/app/parts.txt")
        self.assertEqual(r.status_code, 200)
        r.close()
        self.assertEqual(self.c.get("/photocraft/../app.py").status_code, 404)
        m = self.c.get("/photocraft/fonts/manifest.txt").get_data(as_text=True).splitlines()
        self.assertTrue(m and all(len(l.split(" | ")) == 3 for l in m))
        f = self.c.get("/photocraft/" + m[0].split(" | ")[2])
        self.assertEqual(f.status_code, 200)
        f.close()


@unittest.skipUnless(photocraft.disponivel(), "photocraft-cli não instalado")
class TestComOMotor(BaseEstudio):
    def test_psd_salvo_pelo_photocraft(self):
        """O Ctrl+S do PhotoCraft manda um PSD: o motor achata e a máscara continua exata."""
        it = self.doc("teste")
        a = self.est.abrir(it.doc)
        base, ed = self.apagar_trecho(it)
        png = self.tmp / "ed.png"
        psd = self.tmp / "ed.psd"
        Image.fromarray(ed).save(png)
        subprocess.run([str(photocraft.caminho_cli()), "run", str(png), "--out", str(psd)], check=True,
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        r = self.est.salvar(a["arquivo"].replace(".png", ".psd"), psd.read_bytes())
        np.testing.assert_array_equal(self.mascara(r) == 1, (ed != base).any(2))

    def test_gerar_e_salvar_direto(self):
        """O lote da aba (o gerador em segundo plano) grava as versões com o motor PhotoCraft e refaz a planilha."""
        it = self.doc("validacao")
        r = self.c.post("/api/estudio/lote", json={"doc": it.doc, "quantidade": 2, "extras": False})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.app.gerador.thread.join(timeout=600)
        st = self.app.gerador.status()
        self.assertFalse(st["rodando"])
        self.assertGreaterEqual(st["feitas"], 1, st.get("erros"))
        anots = [json.loads(p.read_text(encoding="utf-8")) for p in (self.saida / "validacao" / "anotacoes").glob("*.json")]
        self.assertEqual(len(anots), st["feitas"])
        self.assertTrue(all(a["gerador"]["receita"]["motor"] == "photocraft" and a["doc"] == it.doc for a in anots))
        self.assertEqual(st["planilha"]["versoes"], st["feitas"])
        self.assertTrue((self.saida / planilha.NOME).is_file())

    def test_gerar_revisar_e_salvar(self):
        """A aba Gerar: a versão sai das ferramentas do PhotoCraft, abre para revisar; salva com os rótulos do gerador
        e, salva de novo com um retoque à mão, substitui e ganha a edição manual."""
        it = self.doc("treino")
        g = self.est.gerar(it.doc, {"extras": False})
        self.assertRegex(g["token"], r"__a1$")
        self.assertTrue(g["trocas"])
        base = self.reg.carregar(it.doc)
        img = self.est.imagem_gerada(g["token"])
        self.assertTrue((img != base).any())
        r1 = self.est.salvar(g["arquivo"], png_bytes(img))
        anot = self.anotacao(r1)
        self.assertEqual(anot["gerador"]["receita"]["motor"], "photocraft")
        self.assertTrue(anot["estudio"]["gerada_pela_aba"])
        self.assertNotIn("manual.photocraft", anot["tecnicas"])
        forn = [e.get("fornecedores") or {} for e in anot["edicoes"]]
        # quem apaga, apaga no PhotoCraft (o campo inteiro sempre); nada diz que salvou em JPEG: o estúdio grava o PNG
        self.assertTrue(all(f.get("motor") == "photocraft" for e, f in zip(anot["edicoes"], forn)
                            if "apagar" in f or e["tecnica"] == "campo.photocraft"), forn)
        self.assertFalse(any(f.get("salvar") == "jpeg_editor" for f in forn), forn)
        h = anot["gerador"].get("humano")
        self.assertTrue(h is None or h["salvar"] != "jpeg_editor", h)
        np.testing.assert_array_equal(self.mascara(r1) == 1, (img != base).any(2))
        ed = img.copy()
        ed[700:720, 1150:1200] = 0
        r2 = self.est.salvar(g["arquivo"], png_bytes(ed))
        self.assertEqual(r2["substituiu"], r1["nome"])
        anot2 = self.anotacao(r2)
        self.assertIn("manual.photocraft", anot2["tecnicas"])
        self.assertEqual(len(anot2["edicoes"]), len(anot["edicoes"]) + 1)
        np.testing.assert_array_equal(self.mascara(r2) == 1, (ed != base).any(2))


if __name__ == "__main__":
    unittest.main()
