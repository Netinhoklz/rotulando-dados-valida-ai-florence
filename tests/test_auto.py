import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rotulador import campos, selecao
from rotulador.ops import auto, executar, geradores
from rotulador.registro import Item
from rotulador.sessao import Sessao

try:
    import rapidocr_onnxruntime  # noqa: F401
    TEM_OCR = True
except ImportError:
    TEM_OCR = False

IDS = {"nome": 1, "cpf/cnpj": 2, "endereço": 3, "bairro": 4, "cep": 5, "cidade/uf": 6, "data": 7,
       "mês de referência": 8, "valor": 9, "consumo/leitura": 10, "codigo barras": 11, "linha digitável": 12,
       "identificador documento": 13, "emissor/logotipo": 14, "qr code/pix": 15, "outro": 16}


def F(nome, tam):
    return ImageFont.truetype(f"C:/Windows/Fonts/{nome}", tam)


def conta(i=1, sem=()) -> np.ndarray:
    """Conta sintética com Arial (como um comprovante real), a ~150 dpi. ``sem``: rótulos que não entram."""
    im = Image.new("RGB", (1240, 1000), (252, 252, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1240, 120], fill=(0, 84, 147))
    d.text((60, 40), "COMPANHIA DE ENERGIA FICTICIA", font=F("arialbd.ttf", 40), fill="white")
    y = 170
    for rot, val in [("NOME", f"JOAO PEREIRA LIMA {i:02d}"), ("CPF", "123.456.789-09"), ("CEP", "30123-456"),
                     ("VENCIMENTO", "10/10/2026"), ("CONSUMO", f"{180 + i} kWh")]:
        if rot in sem:
            continue
        d.text((60, y), rot, font=F("arialbd.ttf", 22), fill=(90, 90, 90))
        d.text((330, y), val, font=F("arial.ttf", 26), fill=(20, 20, 20))
        y += 60
    d.text((90, y + 40), "TOTAL A PAGAR", font=F("arialbd.ttf", 30), fill=(0, 84, 147))
    d.text((800, y + 35), f"R$ {150 + i},37", font=F("arialbd.ttf", 44), fill=(10, 10, 10))
    a = np.asarray(im).astype(np.float32) + np.random.default_rng(i).normal(0, 3, (1000, 1240, 1))
    return np.clip(a, 0, 255).astype(np.uint8)


class TestClassificacao(unittest.TestCase):
    def test_padroes(self):
        casos = {"123.456.789-09": "cpf/cnpj", "00.000.000/0001-91": "cpf/cnpj", "30123-456": "cep",
                 "10/10/2026": "data", "SET/2026": "mês de referência", "09/2026": "mês de referência",
                 "R$ 151,37": "valor", "181 kWh": "consumo/leitura", "RUA DAS FLORES, 12": "endereço",
                 "83660000001-2 50370048000-6 12345678901-2 34567890123-4": "linha digitável", "CENTRO": None}
        for t, cat in casos.items():
            self.assertEqual(campos.categoria_por_texto(t), cat, t)

    def test_rotulos_tolerantes_e_colados(self):
        self.assertEqual(campos.rotulo_de("CIDADEIUF"), "CIDADE/UF")  # '/' lido como 'I'
        self.assertEqual(campos.rotulo_de("Vencimento:"), "VENCIMENTO")
        self.assertIsNone(campos.rotulo_de("MARIA DOS SANTOS"))
        self.assertEqual(campos.separar_rotulo("CNPJ00.000.000/0001-91"), ("CNPJ", "00.000.000/0001-91"))
        self.assertEqual(campos.separar_rotulo("NOME: MARIA SILVA")[1], "MARIA SILVA")
        self.assertEqual(campos.separar_rotulo("MARIA SILVA"), (None, "MARIA SILVA"))

    def test_linha_digitavel_remontada(self):
        self.assertEqual(campos.formatar_linha_digitavel("836600000012503700480006123456789012345678901234"),
                         "83660000001-2 50370048000-6 12345678901-2 34567890123-4")


class TestGeradorParecido(unittest.TestCase):
    def test_formatos_preservados(self):
        for _ in range(30):
            self.assertRegex(geradores.parecido("data", "10/10/2026"), r"^\d{2}/\d{2}/\d{4}$")
            self.assertRegex(geradores.parecido("valor", "R$ 151,37"), r"^R\$ [\d.]+,\d{2}$")
            self.assertRegex(geradores.parecido("cep", "30123-456"), r"^\d{5}-\d{3}$")
            self.assertTrue(geradores.valida_cpf(geradores.parecido("cpf/cnpj", "123.456.789-09")))
            self.assertTrue(geradores.valida_cnpj(geradores.parecido("cpf/cnpj", "00.000.000/0001-91")))
            self.assertRegex(geradores.parecido("mês de referência", "SET/2026"), r"^[A-Z]{3}/\d{4}$")
            self.assertRegex(geradores.parecido("consumo/leitura", "181 kWh"), r"^\d+ kWh$")
            self.assertEqual(len(geradores.parecido("identificador documento", "30000001")), 8)
            n = geradores.parecido("nome", "Maria Souza")  # caixa de título, partículas em minúsculas ("Breno de Araujo")
            self.assertTrue(all(p[0].isupper() or p in ("de", "da", "do", "das", "dos", "e") for p in n.split()), n)
            lin = geradores.parecido("linha digitável", "83660000001-2 50370048000-6 12345678901-2 34567890123-4")
            self.assertTrue(lin.startswith("8"))

    def test_vocabulario_dos_falsos_repartido_por_split(self):
        """Os falsos do teste não reusam a CAUDA do vocabulário dos falsos do treino, e um nome completo falso é de um
        split só: um modelo que lê o texto não acerta o teste decorando a lista de valores falsos. O núcleo (MARIA,
        JOSE, SILVA, as capitais) vale em todos os splits: fora do treino, ele ensinava "MARIA ⇒ genuíno"."""
        partes = geradores._PARTES
        for k, p in partes.items():
            self.assertTrue(all(p.values()), k)
        self.assertGreaterEqual(min(len(v) for v in partes["sobrenomes"].values()), 3)  # sample de até 3
        treino = {geradores._chave(x) for k in ("nomes", "meios", "sobrenomes") for x in partes[k]["treino"]}
        with geradores.usando(split="teste"):
            nomes = [geradores.parecido("nome", "JOAO PEREIRA LIMA") for _ in range(300)]
            ruas = [geradores.gerar("endereço")["texto"] for _ in range(100)]
            cidades = [geradores.gerar("cidade/uf")["texto"] for _ in range(100)]
            ruas_do_teste = [geradores._normalizar(r) for r in geradores._lista("ruas")]
            cidades_do_teste = [geradores._normalizar(c) for c, _ in geradores._lista("cidades")]
        palavras = {geradores._normalizar(w) for n in nomes for w in n.split()}
        self.assertFalse(palavras & treino)
        self.assertTrue(all(geradores._fatia(n) == "teste" for n in nomes))
        e = geradores._ruas("2304400")
        if e is None:  # sem a base de lugares: os nomes de rua embutidos do split
            self.assertTrue(all(any(f" {r}," in geradores._normalizar(e) for r in ruas_do_teste) for e in ruas))
        else:  # com ela, a rua é de verdade, da cidade, e do split dela (a rua inteira cai num split pelo hash)
            with geradores.usando(split="teste", cidade=("FORTALEZA", "CE")):
                ruas = [geradores.gerar("endereço")["texto"] for _ in range(100)]
            for end in ruas:
                p = geradores._palavras_rua(end)
                fatias = {f for r, n, f in zip(e["linhas"], e["nome"], e["fatia"]) if geradores._mesma_rua(p, r, n)}
                self.assertIn("teste", fatias, end)
        self.assertTrue(all(any(geradores._normalizar(c).startswith(x) for x in cidades_do_teste) for c in cidades))
        # fora de um split (sem usando), a lista inteira
        self.assertGreater(len({geradores.gerar("nome")["texto"].split()[0] for _ in range(800)}), 60)


class TestFonteAutomatica(unittest.TestCase):
    def _amostra(self, arquivo):
        im = Image.new("RGB", (900, 140), (250, 250, 248))
        ImageDraw.Draw(im).text((40, 30), "R$ 1.234,56 TOTAL", font=F(arquivo, 60), fill=(15, 15, 15))
        return np.asarray(im).copy()

    def test_distingue_arial_e_arial_bold(self):
        for arquivo, esperado in (("arial.ttf", "Arial"), ("arialbd.ttf", "Arial Bold")):
            img = self._amostra(arquivo)
            r = auto.ajustar_fonte(img, [30, 20, 880, 120], "R$ 1.234,56 TOTAL")
            self.assertEqual(r["fonte"], esperado)
            self.assertGreater(r["iou"], 0.85)
            self.assertAlmostEqual(r["tamanho"], 60, delta=6)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.img = conta()
        self.item = Item(doc="abc", split="treino", caminho=self.tmp / "x.png", rel="x.png", pagina=0, paginas=1,
                         sha_arquivo="f", sha_pixels="p" * 40, altura=self.img.shape[0], largura=self.img.shape[1])
        self.s = Sessao(self.item, self.img.copy(), self.tmp / "rasc", IDS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTrocarCampo(Base):
    def test_troca_so_perto_do_campo_e_imita_a_fonte(self):
        caixa = [330, 290, 560, 318]  # "30123-456" (CEP)
        p = {"caixa": caixa, "texto": "99999-111", "texto_antigo": "30123-456", "semente": 1}
        res = executar("trocar_campo", self.s.atual, p, selecao.vazia(1000, 1240), base=self.s.base)
        self.s.aplicar("trocar_campo", "cep", "", p, res)
        alt, _, cls = self.s.mascaras()
        ys, xs = np.nonzero(alt)
        self.assertTrue(ys.min() >= 280 and ys.max() <= 330 and xs.min() >= 320, (ys.min(), ys.max(), xs.min()))
        self.assertEqual(res.meta["fonte"], "Arial")
        self.assertEqual(set(np.unique(cls)) - {0}, {5})

    def test_trocar_com_ia_so_na_regiao_e_prompt_certo(self):
        pedidos = []

        def editor_falso(rgb, prompt, semente, passos):
            pedidos.append(prompt)
            return np.random.default_rng(semente).integers(0, 256, rgb.shape, dtype=np.uint8)

        caixa = [330, 290, 460, 318]
        p = {"caixa": caixa, "texto": "99999-111 EXTRA LONGO", "texto_antigo": "30123-456", "semente": 2}
        res = executar("trocar_campo_ia", self.s.atual, p, selecao.vazia(1000, 1240), base=self.s.base,
                       editor=editor_falso)
        self.s.aplicar("trocar_campo_ia", "cep", "", p, res)
        self.assertIn('"30123-456"', pedidos[0])
        self.assertIn('"99999-111 EXTRA LONGO"', pedidos[0])
        alt, _, _ = self.s.mascaras()
        ys, xs = np.nonzero(alt)
        self.assertTrue(ys.min() >= 280 and ys.max() <= 330, (ys.min(), ys.max()))
        self.assertGreater(xs.max(), 460 + 100)  # valor mais longo: a região cresceu para a direita
        self.assertEqual(res.meta["auto"], "trocar_campo_ia")

    def test_colagem_da_ia_selecao_suave_e_contexto(self):
        """A saída da IA volta de três jeitos; em todos, a pegada é exatamente o que mudou e nada muda
        fora do recorte de contexto (TGIF/GIFL: treinar só com o recorte exato ensina a costura)."""
        def editor_vae(rgb, prompt, semente, passos):  # imita o VAE: desvio leve na imagem toda
            out = np.clip(rgb.astype(int) + 6, 0, 255).astype(np.uint8)
            h, w = out.shape[:2]
            out[h // 3:2 * h // 3, w // 4:3 * w // 4] = 30
            return out

        caixa = [330, 290, 460, 318]
        fora_sel = {}
        for modo in ("selecao", "suave", "contexto"):
            p = {"caixa": caixa, "texto": "99999-111", "texto_antigo": "30123-456", "semente": 2, "colagem": modo,
                 "pena": 3, "igualar_ruido": False}
            res = executar("trocar_campo_ia", self.s.atual, p, selecao.vazia(1000, 1240), base=self.s.base,
                           editor=editor_vae)
            antes = self.s.atual[res.y0:res.y1, res.x0:res.x1]
            mudou = (np.where(res.pegada[..., None], res.recorte, antes) != antes).any(2)
            self.assertFalse((mudou & ~res.pegada).any(), modo)
            cx0, cy0, cx1, cy1 = res.meta["contexto"]
            self.assertTrue(cx0 <= res.x0 and cy0 <= res.y0 and res.x1 <= cx1 and res.y1 <= cy1, modo)
            fora_sel[modo] = int(res.pegada.sum())
            self.assertEqual(res.meta["colagem"], modo)
        # o recorte exato é o menor; o contexto inteiro, o maior
        self.assertLess(fora_sel["selecao"], fora_sel["suave"])
        self.assertLess(fora_sel["suave"], fora_sel["contexto"])

    def test_codigo_de_barras_automatico(self):
        im = np.full((200, 1100, 3), 250, np.uint8)
        im[50:150, 40:1060:9] = 0
        s = Sessao(self.item, im, self.tmp / "rasc2", IDS)
        p = {"caixa": [40, 50, 1060, 150], "tipo": "barras", "texto": ""}
        res = executar("trocar_campo", s.atual, p, selecao.vazia(200, 1100), base=s.base)
        s.aplicar("trocar_campo", "codigo barras", "", p, res)
        self.assertEqual(len(res.meta["digitos"]), 44)


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestComOCR(Base):
    def test_detecta_campos(self):
        from rotulador import ocr
        lista = campos.detectar(self.img, ocr.ler(self.img))
        por_cat = {c["categoria"]: c["texto"] for c in lista}
        self.assertEqual(por_cat.get("cpf/cnpj"), "123.456.789-09")
        self.assertEqual(por_cat.get("cep"), "30123-456")
        self.assertEqual(por_cat.get("data"), "10/10/2026")
        self.assertIn("R$", por_cat.get("valor", ""))
        self.assertIn("nome", por_cat)
        self.assertNotIn("NOME", [c["texto"] for c in lista])  # rótulo não é campo

    def test_fraude_automatica_e_desfazer(self):
        import random
        from rotulador.contexto import Contexto
        from rotulador.gerador import Plano, Receita, aplicar_receita, receita_padrao
        ctx = Contexto(self.item, self.s.base, self.tmp / "saida")
        r = Receita(receita_padrao(IDS), IDS)
        feitas = aplicar_receita(self.s, ctx, r, Plano(r, random.Random(3)), random.Random(3), k=3)
        self.assertGreaterEqual(len(feitas), 2, feitas)
        self.assertEqual(len(self.s.historico_), len(feitas))
        self.assertEqual(set(self.s.categorias()), {t["categoria"] for t in feitas})
        self.assertTrue(all(t["tecnica"] and t["descricao"] for t in feitas))
        self.s.desfazer()  # desfaz só a última
        self.assertEqual(len(self.s.historico_), len(feitas) - 1)


@unittest.skipUnless(TEM_OCR, "OCR (rapidocr_onnxruntime) não instalado")
class TestAPIAutomatica(unittest.TestCase):
    def setUp(self):
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import SPLITS, Registro
        self.tmp = Path(tempfile.mkdtemp())
        self.dados, self.saida = self.tmp / "dados", self.tmp / "saida"
        for i, sp in enumerate(SPLITS):
            (self.dados / sp).mkdir(parents=True)
            Image.fromarray(conta(10 + i)).save(self.dados / sp / f"c{i}.png")
        self.reg = Registro(self.dados, self.saida, log=lambda *_: None)
        self.app = criar_app(self.reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json"))
        self.c = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_campos_fraude_e_gerador(self):
        doc = self.reg.por_split["teste"][0].doc
        r = self.c.get(f"/api/campos/{doc}").get_json()
        self.assertTrue(any(c["categoria"] == "cep" for c in r["campos"]))
        self.assertTrue((self.saida / "_cache" / "ocr").is_dir())  # OCR em cache
        r = self.c.post("/api/auto/fraude", json={"id": doc, "quantidade": 2})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("png", r.get_json())
        self.assertTrue(r.get_json()["estado"]["pode_salvar"])
        self.assertTrue(all(t["nome_tecnica"] for t in r.get_json()["trocas"]))
        r = self.c.post("/api/auto/sugerir", json={"categoria": "data", "texto": "01/02/2026"}).get_json()
        self.assertRegex(r["texto"], r"^\d{2}/\d{2}/\d{4}$")
        treino = {geradores._chave(x) for k in ("nomes", "meios", "sobrenomes") for x in geradores._PARTES[k]["treino"]}
        for _ in range(20):  # o documento é do teste: o nome sugerido não usa a cauda do vocabulário do treino
            r = self.c.post("/api/auto/sugerir", json={"id": doc, "categoria": "nome", "texto": "JOAO LIMA"}).get_json()
            self.assertFalse(set(geradores._normalizar(r["texto"]).split()) & treino)
            r = self.c.get(f"/api/gerar?categoria=nome&id={doc}").get_json()
            self.assertFalse(set(geradores._normalizar(r["texto"]).split()) & treino)
        # gerador: só no split pedido, cada variação salva com o rótulo das técnicas
        rec = {"documentos": {"splits": ["validacao"]}, "variacoes": 2, "edicoes": [1, 2],
               "tecnicas": {"caractere.redesenhar": 1, "campo.reescrever": 1, "trecho.trocar": 1}}
        r = self.c.post("/api/gerador/iniciar", json={"receita": rec})
        self.assertEqual(r.status_code, 200, r.get_json())
        for _ in range(240):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertEqual(st["feitas"], 2, st)
        anot = sorted((self.saida / "validacao" / "anotacoes").glob("*.json"))
        self.assertEqual(len(anot), 2)
        a = json.loads(anot[0].read_text(encoding="utf-8"))
        self.assertEqual(a["operador"], "gerador automático")
        self.assertTrue(a["edicoes"] and all(e["tecnica"] in rec["tecnicas"] for e in a["edicoes"]))
        self.assertTrue((self.saida / "validacao" / a["arquivos"]["mascara_tecnicas"]).is_file())
        self.assertFalse((self.saida / "treino" / "anotacoes").exists())
        self.assertFalse(list((self.saida / "_cache").glob("gerador_*")))  # temporários apagados
        res = self.c.get("/api/catalogo/resumo").get_json()
        self.assertEqual(res["versoes"], 2)
        nome = anot[0].stem
        r = self.c.post("/api/catalogo/excluir", json={"split": "validacao", "nome": nome})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.c.get("/api/catalogo/resumo").get_json()["versoes"], 1)
        self.assertFalse(anot[0].exists())

    def test_gerador_com_canal_de_envio(self):
        """Com canal, cada versão tem a SUA original (o mesmo canal passou nas duas), e máscaras e
        rótulos acompanham a geometria do canal."""
        rec = {"documentos": {"splits": ["teste"]}, "variacoes": 2, "edicoes": [1, 2],
               "tecnicas": {"campo.reescrever": 1, "caractere.redesenhar": 1}, "canal": {"perfil": "celular", "prob": 1},
               "marcas": {"prob": 1}}
        self.assertEqual(self.c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(480):
            st = self.c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertEqual(st["feitas"], 2, st)
        raiz = self.saida / "teste"
        anots = sorted((raiz / "anotacoes").glob("*.json"))
        for arq in anots:
            a = json.loads(arq.read_text(encoding="utf-8"))
            self.assertEqual(a["canal"]["perfil"], "celular")
            self.assertIn("__v", a["arquivos"]["original"])
            imgs = {k: np.asarray(Image.open(raiz / a["arquivos"][k])) for k in
                    ("original", "editada", "mascara", "mascara_regiao", "mascara_classes", "mascara_tecnicas",
                     "mascara_ignorar")}
            self.assertEqual({v.shape[:2] for v in imgs.values()}, {(a["altura"], a["largura"])})
            self.assertNotEqual((a["altura"], a["largura"]), (1000, 1240))  # o celular reamostrou
            reg = imgs["mascara_regiao"] > 0
            self.assertTrue(reg.any())
            self.assertTrue(np.array_equal(imgs["mascara_tecnicas"] > 0, reg))
            self.assertTrue(np.array_equal(imgs["mascara_classes"] > 0, reg))
            # banda de ignorar: borda/folga da região e, com canal, o espalhamento do codec em volta;
            # nunca longe da edição nem em cima de pixel alterado
            import cv2
            from rotulador import canal as mod_canal
            r = mod_canal.raio(a["canal"]) + 2
            perto = cv2.dilate(reg.astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8)) > 0
            ign = imgs["mascara_ignorar"] > 0
            if not mod_canal.tem_webp(a["canal"]):  # WEBP espalha diferença pela página toda (ver canal.tem_webp)
                self.assertFalse((ign & ~perto).any(), mod_canal.descrever(a["canal"]))
            self.assertFalse((ign & (imgs["mascara"] > 0)).any())
            self.assertTrue(a["edicoes"] and all(e["pixels"] > 0 for e in a["edicoes"]), a["edicoes"])
            # cada pixel da máscara de técnicas pertence a uma edição listada (caixa nas coordenadas da saída)
            self.assertEqual(sum(e["pixels"] for e in a["edicoes"]), int(reg.sum()))
            for e in a["edicoes"]:
                x0, y0, x1, y1 = e["bbox"]
                self.assertTrue((imgs["mascara_tecnicas"][y0:y1, x0:x1] == e["tecnica_num"]).any())
            # marcas benignas (carimbo, anotação...) iguais no par: a diferença continua só perto da edição
            self.assertIsInstance(a["marcas_benignas"], list)
            from rotulador import canal
            self.assertIsNone(canal.conferir_par(imgs["original"], imgs["editada"], reg, a["canal"]))
        self.assertEqual(len(list((raiz / "originais").glob("*"))), 2)
        ds = json.loads((self.saida / "datasheet.json").read_text(encoding="utf-8"))  # refeito no fim do lote
        self.assertEqual(ds["splits"]["teste"]["versoes"], 2)
        self.assertEqual(ds["splits"]["teste"]["canal"], {"celular": 2})
        self.assertIn("## Protocolo de avaliação", (self.saida / "DATASHEET.md").read_text(encoding="utf-8"))
        nome = anots[0].stem
        self.assertEqual(self.c.post("/api/catalogo/excluir", json={"split": "teste", "nome": nome}).status_code, 200)
        self.assertEqual(len(list((raiz / "originais").glob("*"))), 1)  # a original da versão saiu junto

    def test_distribuicao_equilibrada_vale_dentro_de_cada_split(self):
        """Com uma cota só para o lote inteiro, o split que roda depois fica com as sobras do primeiro
        (aqui: o treino só tem valor, e o teste recebia só CEP) e as métricas do teste mediriam outra mistura."""
        from app import RAIZ, carregar_categorias, criar_app
        from rotulador.registro import Registro
        shutil.rmtree(self.tmp)
        (self.dados / "treino").mkdir(parents=True)
        (self.dados / "teste").mkdir(parents=True)
        Image.fromarray(conta(21, sem=("CEP",))).save(self.dados / "treino" / "a.png")
        Image.fromarray(conta(22)).save(self.dados / "teste" / "b.png")
        reg = Registro(self.dados, self.saida, log=lambda *_: None)
        c = criar_app(reg, self.saida, carregar_categorias(RAIZ / "config" / "categorias.json")).test_client()
        rec = {"documentos": {"splits": ["treino", "teste"]}, "variacoes": 3, "edicoes": [1, 1],
               "distribuicao": "equilibrada", "tecnicas": {"campo.reescrever": 1},
               "categorias": {"valor": 1, "cep": 1}}
        self.assertEqual(c.post("/api/gerador/iniciar", json={"receita": rec}).status_code, 200)
        for _ in range(480):
            st = c.get("/api/gerador/status").get_json()
            if not st["rodando"]:
                break
            time.sleep(0.5)
        self.assertEqual(st["feitas"], 6, st)
        cats = [e["categoria"] for a in (self.saida / "teste" / "anotacoes").glob("*.json")
                for e in json.loads(a.read_text(encoding="utf-8"))["edicoes"]]
        self.assertEqual(sorted(set(cats)), ["cep", "valor"], cats)


if __name__ == "__main__":
    unittest.main()
