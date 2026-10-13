"""Perito (rotulador/perito.py): as pistas fáceis que uma edição deixa — grão, espectro, nitidez, tom e grade
JPEG do interior da região contra o papel em volta —, a nota 0..1, o mapa de calor para o operador, o
avaliador (interno ou serviço externo), as rotas da API e a seção do datasheet. Também a auditoria por
emissor (aviso e sugestão de split), que entrou na mesma entrega."""
import base64
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image

from rotulador import perito


def papel(semente=0, h=360, w=480, grao=6.0):
    """Papel com grão branco e quatro linhas de texto (tinta para a nitidez ter o que medir)."""
    rng = np.random.default_rng(semente)
    img = np.clip(236 + rng.normal(0, grao, (h, w, 1)), 0, 255).astype(np.uint8).repeat(3, 2)
    for i, t in enumerate(("NOME: MARIA DA SILVA 123", "RUA DAS FLORES, 45", "CEP 30123-456", "TOTAL R$ 151,37")):
        cv2.putText(img, t, (20, 80 + 70 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (25, 25, 25), 2, cv2.LINE_AA)
    return img


def retangulo(y0, y1, x0, x1, h=360, w=480):
    m = np.zeros((h, w), bool)
    m[y0:y1, x0:x1] = True
    return m


class TestMedidas(unittest.TestCase):
    def setUp(self):
        self.orig = papel()
        self.reg = retangulo(130, 180, 20, 260)  # papel entre duas linhas de texto
        self.chapado = self.orig.copy()
        self.chapado[130:180, 20:260] = 236  # retângulo liso na cor média do papel: sem grão

    def test_retangulo_chapado_e_pista_forte(self):
        m = perito.medir(self.orig, self.chapado, self.reg)
        self.assertLess(m["grao"], 0.3)
        self.assertGreater(perito.nota(m), 0.5)
        self.assertGreaterEqual(m["n_papel_dentro"], 40)
        json.dumps(m)  # tudo nativo

    def test_copia_sem_alteracao_nao_tem_pista(self):
        reg = retangulo(50, 100, 20, 300)  # pega texto e papel: todas as medidas valem
        m = perito.medir(self.orig, self.orig.copy(), reg)
        self.assertTrue(all(m[k] is not None for k in perito.MEDIDAS), m)
        self.assertLess(perito.nota(m), 0.1)
        self.assertAlmostEqual(m["grao"], 1.0, delta=0.1)
        self.assertAlmostEqual(m["nitidez"], 1.0, delta=0.1)
        self.assertLess(m["tom"], 1.0)

    def test_regiao_minuscula_da_none(self):
        m = perito.medir(self.orig, self.chapado, retangulo(200, 203, 200, 203))
        self.assertTrue(all(m[k] is None for k in perito.MEDIDAS), m)
        self.assertIsNone(perito.nota(m))  # não mediu não é "invisível" (0,0 virava dificuldade 1,0)
        self.assertIsNone(perito.nota({}))
        vazio = perito.medir(self.orig, self.chapado, np.zeros((360, 480), bool))
        self.assertTrue(all(vazio[k] is None for k in perito.MEDIDAS))

    def test_texto_novo_nitido_demais(self):
        """Texto nítido reescrito numa página borrada como scan: a nitidez denuncia."""
        borrado = cv2.GaussianBlur(self.orig, (0, 0), 1.2)
        ed = borrado.copy()
        ed[50:100, 20:300] = 236
        cv2.putText(ed, "NOME: JOSE PEREIRA 999", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (25, 25, 25), 2, cv2.LINE_AA)
        m = perito.medir(borrado, ed, retangulo(50, 100, 20, 300))
        self.assertGreater(m["nitidez"], 1.3)
        self.assertGreater(perito.nota(m), 0.5)

    def test_mapa(self):
        m = perito.mapa(self.orig, self.chapado, self.reg)
        self.assertEqual((m.shape, m.dtype), ((360, 480), np.float32))
        self.assertGreaterEqual(float(m.min()), 0.0)
        self.assertLessEqual(float(m.max()), 1.0)
        self.assertGreater(float(m[self.reg].mean()), float(m[~self.reg].mean()) + 0.4)
        sem = perito.mapa(None, self.chapado)  # sem original nem região: só as estatísticas da página
        self.assertEqual(sem.shape, (360, 480))
        self.assertGreater(float(sem[self.reg].mean()), float(sem[~self.reg].mean()) + 0.4)
        self.assertEqual(perito.colorir(m).shape, (360, 480, 4))

    def test_avaliar_interno_e_externo_caido(self):
        r = perito.avaliar(self.orig, self.chapado, self.reg)
        self.assertEqual(r["fonte"], "interno")
        self.assertNotIn("erro", r)
        self.assertEqual(r["nota"], perito.nota(r["medidas"]))
        r2 = perito.avaliar(self.orig, self.chapado, self.reg, {"url": "nada://perito", "timeout": 1})
        self.assertEqual((r2["fonte"], r2["nota"]), ("interno", r["nota"]))
        self.assertIn("erro", r2)

    def test_avaliar_externo_responde(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b'{"nota": 0.42, "medidas": {"x": 1}}'
        with mock.patch("urllib.request.urlopen", return_value=resp) as urlopen:
            r = perito.avaliar(self.orig, self.chapado, self.reg, {"url": "http://perito.local/nota"})
        self.assertEqual(r, {"nota": 0.42, "medidas": {"x": 1}, "fonte": "externo"})
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://perito.local/nota")
        corpo = json.loads(req.data)
        self.assertEqual(set(corpo), {"original", "editada", "regiao"})
        with Image.open(io.BytesIO(base64.b64decode(corpo["regiao"]))) as im:
            self.assertEqual(im.size, (480, 360))


class TestAPIPerito(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import SPLITS, Registro
        from tests.test_auto import conta
        self.tmp = Path(tempfile.mkdtemp())
        self.saida = self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            (self.tmp / "dados" / sp).mkdir(parents=True)
            Image.fromarray(conta(50 + i)).save(self.tmp / "dados" / sp / f"c{i}.png")
        self.reg = Registro(self.tmp / "dados", self.saida, log=lambda *_: None)
        self.c = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()
        self.doc = self.reg.por_split["treino"][0].doc

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sessao_versao_e_datasheet(self):
        r = self.c.post("/api/perito/sessao", json={"doc": self.doc})
        self.assertEqual(r.status_code, 400)
        self.assertIn("nenhuma edição", r.get_json()["erro"])
        # pincelada chapada na cor do papel por cima do CEP: a pista mais fácil que existe
        r = self.c.post("/api/op", json={"id": self.doc, "tipo": "pincel", "categoria": "cep",
                                         "params": {"pontos": [[330, 304], [560, 304]], "tamanho": 36, "cor": "#fcfcfa"}})
        self.assertEqual(r.status_code, 200, r.get_json())
        r = self.c.post("/api/perito/sessao", json={"doc": self.doc})
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertEqual(j["fonte"], "interno")
        self.assertGreater(j["nota"], 0.5)
        self.assertEqual((len(j["medidas"]), j["medidas"][0]["categoria"]), (1, "cep"))
        self.assertEqual(set(j["medidas"][0]) >= {"tecnica", "nota", "medidas"}, True)
        self.assertEqual((j["largura"], j["altura"]), (1240, 1000))
        with Image.open(io.BytesIO(base64.b64decode(j["mapa"]))) as im:
            self.assertEqual((im.size, im.mode), ((1240, 1000), "RGBA"))  # tamanho exato: desenhado em cima do documento
            alfa = np.asarray(im)[..., 3]
        self.assertGreater(int(alfa[304, 445]), 60)  # no meio da pincelada chapada
        self.assertLessEqual(int(alfa.max()), 170)
        self.assertEqual(int(alfa[900:, :].max()), 0)  # papel intacto longe da edição: transparente
        # versão salva
        self.assertEqual(self.c.post("/api/salvar", json={"id": self.doc}).status_code, 200)
        nome = f"{self.doc}__v01"
        r = self.c.get(f"/api/perito/treino/{nome}")
        self.assertEqual(r.status_code, 200, r.get_json())
        eds = r.get_json()["edicoes"]
        self.assertEqual((len(eds), eds[0]["categoria"], eds[0]["pericia"]), (1, "cep", "auto"))
        self.assertGreater(eds[0]["nota"], 0.5)
        self.assertEqual(self.c.get(f"/api/perito/teste/{nome}").status_code, 400)
        r = self.c.get(f"/api/perito/treino/{nome}/mapa.png")
        self.assertEqual(r.status_code, 200)
        with Image.open(io.BytesIO(r.data)) as im:
            self.assertEqual((im.size, im.mode), ((1240, 1000), "RGBA"))  # sem ?max=, o tamanho exato da editada
        with Image.open(io.BytesIO(self.c.get(f"/api/perito/treino/{nome}/mapa.png?max=300").data)) as im:
            self.assertEqual(max(im.size), 300)
        # datasheet: pistas por técnica e nível, fornecedores
        from rotulador import datasheet
        d = datasheet.coletar(self.saida)
        p = d["splits"]["treino"]["pistas"]
        self.assertEqual((p["edicoes"], p["amostra"], p["falhas"]), (1, 1, 0))
        self.assertEqual(p["por_tecnica"][eds[0]["tecnica"]]["n"], 1)
        self.assertGreater(p["por_pericia"]["auto"]["nota"]["mediana"], 0.5)
        self.assertEqual(d["splits"]["treino"]["fornecedores"], {})
        md = datasheet.markdown(d)
        self.assertIn("Pistas forenses", md)
        self.assertIn("não registrado", md)
        arq = self.saida / "treino" / "anotacoes" / f"{nome}.json"
        a = json.loads(arq.read_text(encoding="utf-8"))
        a["edicoes"][0]["fornecedores"] = {"apagar": "patchmatch", "fonte": "Arial"}
        arq.write_text(json.dumps(a), encoding="utf-8")
        d = datasheet.coletar(self.saida)
        self.assertEqual(d["splits"]["treino"]["fornecedores"], {"apagar=patchmatch": 1, "fonte=Arial": 1})
        self.assertIn("apagar=patchmatch", datasheet.markdown(d))
        # versão com arquivo faltando: sai da amostra sem derrubar o datasheet
        (self.saida / "treino" / a["arquivos"]["editada"]).unlink()
        p = datasheet.coletar(self.saida)["splits"]["treino"]["pistas"]
        self.assertEqual((p["amostra"], p["falhas"], p["por_tecnica"]), (1, 1, {}))


class TestEmissores(unittest.TestCase):
    """auditar_vazamento: emissor (CNPJ impresso, por menos documentos que tenha) em mais de um split avisa; a
    sugestão de split mantém cada emissor num split só, perto de 70/15/15."""

    def setUp(self):
        from rotulador.registro import SPLITS, Registro
        from tests.util import conta, salvar
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            salvar(conta(60 + i), self.dados / sp / f"a_{sp}.png")
            salvar(conta(70 + i), self.dados / sp / f"b_{sp}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_emissor_em_mais_de_um_split_avisa_e_sugestao_separa(self):
        from auditar_vazamento import auditar, sugerir_split
        nomes = {it.sha_pixels: it.rel for it in self.reg.itens.values()}

        def campos(img, saida, sha):  # 4 contas do emissor A (CNPJ válido), 2 do B (poucas para ser emissor)
            do_a = nomes[sha].startswith("a_") or nomes[sha] == "b_treino.png"
            cnpj = "00.000.000/0001-91" if do_a else "11.444.777/0001-61"
            return [{"tipo": "texto", "categoria": "emissor/logotipo", "texto": f"CNPJ {cnpj}"},
                    {"tipo": "texto", "categoria": "cpf/cnpj", "texto": "000.000.000-00"}]
        with mock.patch("rotulador.automatico.campos_do_documento", side_effect=campos):
            rel = auditar(self.dados, self.saida, limiar=0)
        self.assertEqual(rel["erros"], [])
        avisos = [w for w in rel["avisos"] if "generalização por emissor" in w]
        # sem o limiar de 20%: o emissor B (2 documentos) também avisa, com a contagem
        self.assertEqual(len(avisos), 2, rel["avisos"])
        self.assertIn("00000000000191 em treino, teste e validacao", avisos[0])
        self.assertIn("11444777000161 em teste e validacao", avisos[1])
        self.assertIn("teste: 1, validacao: 1", avisos[1])
        self.assertEqual(rel["emissores"], {"00000000000191": {"treino": 2, "teste": 1, "validacao": 1},
                                            "11444777000161": {"teste": 1, "validacao": 1}})
        self.assertEqual(len(rel["emissor_por_doc"]), 6)

        sug = sugerir_split(self.reg, rel["emissor_por_doc"])
        self.assertEqual(sug["por_split"], {"treino": 4, "teste": 2, "validacao": 0})
        self.assertEqual(sug["emissores"], {"00000000000191": "treino", "11444777000161": "teste"})
        docs = sug["documentos"]
        self.assertEqual(len(docs), 6)
        self.assertEqual({d["split_sugerido"] for d in docs if d["emissor"] == "00000000000191"}, {"treino"})
        self.assertEqual(sug["mudancas"], sum(d["split_atual"] != d["split_sugerido"] for d in docs))
        self.assertGreaterEqual(sug["mudancas"], 2)  # a_teste e a_validacao vão para o treino
        json.dumps(sug)


if __name__ == "__main__":
    unittest.main()
