"""Integração das bases oficiais de vocabulário (config/vocabulario: nomes do Censo/TSE e lugares do IBGE/CNEFE) com o
gerador de valores falsos (rotulador/ops/geradores.py) e com as regras de coerência (rotulador/coerencia.py): carga
preguiçosa, nomes pela estrutura real, ruas/bairros/CEPs de verdade e coerentes na versão, repartição por split e o
vocabulário embutido quando as bases faltam. As distribuições são medidas em milhares de sorteios com semente fixa."""
import collections
import contextlib
import json
import random
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rotulador import coerencia
from rotulador.ops import geradores as g

RAIZ = Path(__file__).resolve().parents[1]
SPLITS = ("treino", "teste", "validacao")
TEM_BASES = g._nomes_base() is not None and g._lugares() is not None and g._ruas("2304400") is not None


@contextlib.contextmanager
def sem_bases():
    """Como quem não tem as bases oficiais (config/vocabulario sem os .tsv.gz e .json): o vocabulário embutido."""
    with tempfile.TemporaryDirectory() as d, mock.patch.object(g, "_VOCABULARIO", Path(d)):
        g._BASES.clear()
        try:
            yield
        finally:
            g._BASES.clear()


def _estrutura():
    return json.loads((g._VOCABULARIO / "nomes_estrutura.json").read_text("utf-8"))


def _documento(**campos):
    """Os campos de um documento (como ``Contexto.campos``) para ``usando(documento=...)``."""
    return [{"id": k, "categoria": c, "texto": t, "rotulo": ""} for k, (c, t) in campos.items()]


@unittest.skipUnless(TEM_BASES, "sem as bases oficiais em config/vocabulario")
class TestCarga(unittest.TestCase):
    def test_importar_nao_le_as_bases(self):
        """Importar continua rápido: nenhuma base é lida antes da primeira necessidade."""
        codigo = ("import time; t = time.perf_counter(); import rotulador.ops.geradores as g; "
                  "print(time.perf_counter() - t, sorted(g._BASES))")
        saida = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=True)
        tempo, bases = saida.stdout.split(" ", 1)
        self.assertEqual(bases.strip(), "[]")

    def test_primeiro_uso_e_cache(self):
        with sem_bases():  # começa do zero
            pass
        t = time.perf_counter()
        with g.usando(random.Random(1), "teste", cidade=("FORTALEZA", "CE")):
            g.parecido("nome", "MARIA DA SILVA")
            g.parecido("endereço", "RUA DAS FLORES, 101")
            g.parecido("bairro", "CENTRO")
            g.parecido("cep", "60750-720")
        self.assertLess(time.perf_counter() - t, 3.0)
        self.assertIs(g._vocab(), g._vocab())  # lida uma vez por processo
        self.assertIs(g._ruas("2304400"), g._ruas("2304400"))


@unittest.skipUnless(TEM_BASES, "sem as bases oficiais em config/vocabulario")
class TestNomes(unittest.TestCase):
    """Nomes pela base real: estrutura de nomes_estrutura.json, frequência real, repartição por split."""

    @classmethod
    def setUpClass(cls):
        with g.usando(random.Random(31)):
            cls.nomes = [g.gerar("nome")["texto"] for _ in range(20000)]
        cls.b = g._nomes_base()

    def test_quase_todos_distintos(self):
        self.assertGreater(len(set(self.nomes)), 19500)

    def test_genero_composto_devocao_sufixo(self):
        b, est = self.b, _estrutura()
        f = m = comp = dev = suf_m = de_jesus = 0
        devocoes = {g._normalizar(d["complemento"]) for d in est["devocoes"]}
        listas = g._vocab()["listas"]
        segundos = ({*listas["meios_f"], *listas["meios_m"]}
                    | {s for gen in "FM" for ps, _, _ in b["pares"][gen].values() for s in ps})
        for n in self.nomes:
            ws = g._normalizar(n).split()
            mulher = ws[0] in b["genero"]["F"] and ws[0] not in b["genero"]["M"]
            f, m = f + mulher, m + (not mulher)
            resto = " ".join(ws[1:])
            dev += any(resto.startswith(d + " ") for d in devocoes)
            comp += len(ws) > 2 and ws[1] in segundos  # ANA PAULA, JOSÉ CARLOS
            suf_m += (not mulher) and ws[-1] in g._SUFIXOS
            de_jesus += "DE JESUS" in " ".join(ws)
        total = len(self.nomes)
        self.assertAlmostEqual(f / total, 0.5107, delta=0.02)  # Censo 2010: 51,07% mulheres
        self.assertAlmostEqual(suf_m / m, 0.0535, delta=0.012)  # FILHO, JÚNIOR, NETO, SOBRINHO
        self.assertGreater(dev / total, 0.015)  # MARIA DE FÁTIMA, JOÃO BATISTA (5,5% F, 1,5% M no TSE)
        self.assertTrue(0.3 < comp / total < 0.47, comp / total)  # 37,7% F, 37,4% M (com os 2ºs nomes que são sobrenome)
        self.assertAlmostEqual(de_jesus / total, 0.0135, delta=0.008)  # 1,6% F, 1,1% M

    def test_frequencia_real(self):
        primeiro = collections.Counter(g._normalizar(n).split()[0] for n in self.nomes)
        self.assertAlmostEqual(primeiro["MARIA"] / len(self.nomes), 0.0645, delta=0.01)  # 11,7 mi de 181 mi
        self.assertGreater(primeiro["JOSE"] / len(self.nomes), 0.02)
        self.assertGreater(len(primeiro), 3000)  # a cauda: ALYNE, HALINE...
        palavras = collections.Counter(w for n in self.nomes for w in g._normalizar(n).split())
        self.assertGreater(palavras["SILVA"] / len(self.nomes), 0.08)
        self.assertGreater(palavras["DOS"] / len(self.nomes), 0.05)  # DOS SANTOS, DOS REIS, DOS ANJOS
        self.assertFalse(re.search(r"\bDOS (SILVA|SOUZA|COSTA)\b|\bDA (SANTOS|SOUZA)\b", "\n".join(self.nomes)))
        primeiros = [g._normalizar(n).split()[0] for n in self.nomes]
        self.assertFalse({"DOS", "DAS", "DA", "DE"} & set(primeiros))  # erro de cadastro do Censo não vira prenome

    def test_acento_como_no_registro(self):
        with g.usando(random.Random(32), acentos=True):
            com = [g.gerar("nome")["texto"] for _ in range(5000)]
        jose = [w for n in com for w in n.split() if g._normalizar(w) == "JOSE"]
        self.assertAlmostEqual(sum(w == "JOSÉ" for w in jose) / len(jose), 0.477, delta=0.06)  # JOSÉ em 48%
        acentuados = sum(not n.isascii() for n in com) / len(com)
        self.assertTrue(0.10 < acentuados < 0.30, acentuados)  # 13,6% F / 18,3% M no TSE
        self.assertTrue(all(n.isascii() for n in self.nomes))  # sem acentos=True, nunca

    def test_reparticao(self):
        """Nome completo falso de um split só; os 30 prenomes e 30 sobrenomes mais comuns em todos; nenhuma palavra
        da cauda de outro split."""
        por_split = {}
        for sp in SPLITS:
            with g.usando(random.Random(33), sp):
                por_split[sp] = [g.gerar("nome")["texto"] for _ in range(6000)]
        conj = {sp: set(map(g._normalizar, v)) for sp, v in por_split.items()}
        for a, b in (("treino", "teste"), ("treino", "validacao"), ("teste", "validacao")):
            self.assertEqual(conj[a] & conj[b], set())
        for sp in SPLITS:
            self.assertTrue(all(g._fatia(n) == sp for n in conj[sp]))
        top_pre = [r[0] for r in g._tsv("prenomes.tsv.gz")[:30]]  # do mais comum (freq_f + freq_m) para o mais raro
        top_sob = g._vocab()["listas"]["sobrenomes"][:30]
        cauda = {sp: {g._chave(x) for k in ("nomes", "meios", "sobrenomes") for x in g._PARTES[k][sp]}
                 for sp in SPLITS}
        for sp, nomes in por_split.items():
            palavras = {w for n in nomes for w in g._normalizar(n).split()}
            self.assertEqual(set(top_pre) - palavras, set(), sp)  # os mais comuns do país em todos os splits
            self.assertEqual(set(top_sob) - palavras, set(), sp)
            for outro in SPLITS:
                if outro != sp:
                    self.assertEqual(palavras & cauda[outro], set(), (sp, outro))

    def test_parecido_imita_o_original(self):
        with g.usando(random.Random(34), "teste"):
            for antigo, regex in (("MARIA DA SILVA", r"\S+ \S+ \S+"), ("José Carlos dos Santos", r"[A-Z]\S* .+"),
                                  ("ANA P. SOUZA", r"\S+ [A-Z]\. \S+"), ("M R C FILHO FOTOS", r"[A-Z] [A-Z] [A-Z] .+")):
                novos = [g.parecido("nome", antigo) for _ in range(60)]
                for n in novos:
                    self.assertRegex(n, "^" + regex + "$", antigo)
                    self.assertNotEqual(g._normalizar(n), g._normalizar(antigo))
                cabem = sum(abs(len(n) - len(antigo)) <= max(1, 0.15 * len(antigo)) for n in novos) / len(novos)
                self.assertGreater(cabem, 0.6, (antigo, novos[:5]))
            mista = [g.parecido("nome", "José Carlos dos Santos") for _ in range(50)]
            self.assertTrue(all(n.split()[0][1:].islower() for n in mista))
            self.assertTrue(any(re.search(r" (de|da|dos|do|das) ", n) for n in mista))  # partícula minúscula
            self.assertTrue(all(g.parecido("nome", "SANTOS COMERCIO DE ALIMENTOS LTDA").endswith(" LTDA")
                                for _ in range(20)))


@unittest.skipUnless(TEM_BASES, "sem as bases oficiais em config/vocabulario")
class TestLugares(unittest.TestCase):
    """Ruas, bairros, CEPs e cidades de verdade, no molde do original e coerentes na versão."""

    def _rua_existe(self, cod, endereco):
        e = g._ruas(cod)
        palavras = g._palavras_rua(endereco)
        return any(g._mesma_rua(palavras, r, n) for r, n in zip(e["linhas"], e["nome"]))

    def test_rua_de_verdade_no_molde(self):
        casos = {("BELO HORIZONTE", "MG"): ("RUA DAS FLORES, 101 APTO 12", r"[A-Z]+ [A-Z' ]+, \d{3} APTO \d{2}"),
                 ("CURITIBA", "PR"): ("AV. BRASIL, 1500", r"[A-Z]{1,3}\. [A-Z.' ]+, \d{4}"),
                 ("FORTALEZA", "CE"): ("R 47 CJ PREFEITO JOSE WALTER 312", r"R \d{2} CJ PREFEITO JOSE WALTER \d{3}"),
                 ("GOIÂNIA", "GO"): ("QD 12 LT 5", r"QD \d{2} LT \d"),
                 ("BRASÍLIA", "DF"): ("SQN 211 BLOCO K APTO 302", r"[A-Z]{2,5} \d+ \S+ \S+ APTO \d{3}")}
        for cidade, (antigo, regex) in casos.items():
            cod = g._municipio(cidade)
            with g.usando(random.Random(35), "teste", cidade=cidade):
                novos = [g.parecido("endereço", antigo) for _ in range(40)]
            for n in novos:
                self.assertRegex(n, "^" + regex + "$", (cidade, n))
                if "QD" not in antigo:
                    self.assertTrue(self._rua_existe(cod, n), (cidade, n))
            self.assertGreater(len(set(novos)), 20, cidade)
        e = g._ruas("2304400")  # RUA 47 do Prefeito José Walter → outra rua de número do mesmo bairro
        with g.usando(random.Random(36), "teste", cidade=("FORTALEZA", "CE")):
            ruas = {re.match(r"R (\d+)", g.parecido("endereço", "R 47 CJ PREFEITO JOSE WALTER 312")).group(1)
                    for _ in range(40)}
        bairros = {r[3] for r in e["linhas"] if r[0] == "RUA" and r[2] in ruas}
        self.assertIn("PREFEITO JOSÉ WALTER", bairros)

    def test_abreviacoes_como_no_original(self):
        with g.usando(random.Random(37), "teste", cidade=("SÃO PAULO", "SP")):
            curtos = [g.parecido("endereço", "AV. PRES. VARGAS, 1500") for _ in range(200)]
            longos = [g.parecido("endereço", "AVENIDA PRESIDENTE VARGAS, 1500") for _ in range(200)]
        self.assertTrue(all(re.match(r"[A-Z]{1,3}\. ", n) for n in curtos))
        self.assertTrue(all(n.split()[0] in {g._sem_acento(t) for t in g._TIPOS_URBANOS} for n in longos))
        titulos_curtos = {w for n in curtos for w in n.split() if w.endswith(".")} - {"AV.", "R.", "TV.", "AL."}
        self.assertTrue(titulos_curtos & {"DR.", "PROF.", "PRES.", "CEL.", "STA.", "STO.", "PE.", "GAL."},
                        titulos_curtos)
        self.assertFalse({w for n in longos for w in n.split()} & {"DR", "DR.", "PROF", "PRES", "CEL"})

    def test_ruas_repartidas_por_split(self):
        cod = g._municipio(("SÃO PAULO", "SP"))
        por_split = {}
        for sp in SPLITS:
            with g.usando(random.Random(38), sp, cidade=("SÃO PAULO", "SP")):
                por_split[sp] = {g.logradouro_de(g.gerar("endereço")["texto"]) for _ in range(400)}
        for a, b in (("treino", "teste"), ("treino", "validacao"), ("teste", "validacao")):
            comuns = por_split[a] & por_split[b]  # o mesmo nome em ruas diferentes (outro tipo) é possível; a mesma rua não
            self.assertLess(len(comuns), 0.05 * len(por_split[b]), (a, b, sorted(comuns)[:5]))
        e = g._ruas(cod)
        self.assertEqual(len({(r[0], r[1], r[2], f) for r, f in zip(e["linhas"], e["fatia"])}),
                         len({(r[0], r[1], r[2]) for r in e["linhas"]}))  # todos os trechos da rua no mesmo split

    def _versao(self, semente, ordem, cidade=("BELO HORIZONTE", "MG")):
        """Troca, na ``ordem``, rua, bairro e CEP de um documento com endereço de verdade da cidade (uma linha da base
        de outro split); devolve {categoria: valor novo}."""
        rng = random.Random(semente)
        e = g._ruas(g._municipio(cidade))
        r = e["linhas"][rng.choice(g._candidatas(g._municipio(cidade), e, "extenso"))]
        antigos = {"endereço": g._sem_acento(" ".join(filter(None, r[:3]))) + ", 101", "bairro": g._sem_acento(r[3]),
                   "cep": f"{r[4][:5]}-{r[4][5:]}"}
        doc = _documento(e=("endereço", antigos["endereço"]), b=("bairro", antigos["bairro"]),
                         c=("cep", antigos["cep"]), u=("cidade/uf", "/".join(cidade)))
        with g.usando(random.Random(semente), "treino", cidade=cidade, documento=doc):
            return {c: g.parecido(c, antigos[c]) for c in ordem}

    def test_rua_bairro_e_cep_da_mesma_linha_na_versao(self):
        """A versão troca rua, bairro e CEP: o CEP sai da linha da rua; o bairro trocado antes puxa a rua para ele."""
        cidade = ("BELO HORIZONTE", "MG")
        rua_cep = rua_bairro = 0
        for s in range(40):
            v = self._versao(s, ("endereço", "bairro", "cep"))
            rua_cep += bool(g.rua_com_cep(v["endereço"], v["cep"], cidade))
            self.assertTrue(g.cep_na_cidade(v["cep"], cidade) and g.bairro_na_cidade(v["bairro"], cidade), v)
            v = self._versao(s, ("bairro", "endereço", "cep"))
            e = g._ruas(g._municipio(cidade))
            palavras = g._palavras_rua(v["endereço"])
            rua_bairro += any(g._mesma_rua(palavras, r, n) and b == g._chave_bairro(v["bairro"])
                              for r, n, b in zip(e["linhas"], e["nome"], e["bairro"]))
        self.assertGreater(rua_cep / 40, 0.9)  # medido: 97%
        self.assertGreater(rua_bairro / 40, 0.5)  # medido: 70% (o bairro pequeno nem sempre tem rua que caiba)

    def test_cep_trocado_antes_da_rua(self):
        """O CEP vem antes na página: a linha do CEP novo é uma rua que caberia no lugar da do documento, e a rua
        trocada depois é ela."""
        ok = 0
        for s in range(30):
            v = self._versao(s, ("cep", "endereço"), ("FORTALEZA", "CE"))
            ok += bool(g.rua_com_cep(v["endereço"], v["cep"], ("FORTALEZA", "CE")))
        self.assertGreater(ok / 30, 0.75)  # medido: 88%

    def test_endereco_cep_com_o_cep_do_documento(self):
        """Só a rua muda (o CEP fica): a rua nova é, quando a base tem, uma rua com o mesmo CEP; na cidade de CEP único,
        qualquer rua. A regra endereco_cep passa a aceitar essas versões."""
        L = g._lugares()
        unicos = [c for c, m in L["mun"].items() if m[6] and m[2] > 30000][:12]
        ok = total = 0
        for cod in unicos:
            nome, uf, *_ = L["mun"][cod]
            cep = L["mun"][cod][7]
            for semente in range(5):
                antes = _documento(e=("endereço", "RUA DAS FLORES, 101"), c=("cep", f"{cep[:5]}-{cep[5:]}"),
                                   u=("cidade/uf", f"{nome}/{uf}"))
                with g.usando(random.Random(semente), "treino", cidade=(nome, uf), documento=antes):
                    rua = g.parecido("endereço", "RUA DAS FLORES, 101")
                depois = coerencia.campos_finais(antes, [{"campo": "e", "novo": rua}])
                r = coerencia.comparar(antes, depois)["editada"]["regras"]["endereco_cep"]
                ok, total = ok + bool(r["ok"]), total + 1
        self.assertEqual(ok, total)
        # cidade grande: CEP quase sempre de uma rua só; quando a base tem outra rua de nome com o mesmo CEP, é ela
        e = g._ruas("2304400")
        extenso = set(g._candidatas("2304400", e, "extenso"))
        por_cep = collections.defaultdict(dict)
        for i in extenso:
            r = e["linhas"][i]
            por_cep[r[4]][" ".join(filter(None, r[:3]))] = r
        pares = [(c, sorted(ruas)) for c, ruas in sorted(por_cep.items()) if c[-3:] != "000" and len(ruas) == 2
                 and abs(len(sorted(ruas)[0]) - len(sorted(ruas)[1])) <= 1]
        self.assertTrue(pares)
        for cep, (velha, nova) in pares[:10]:
            antes = _documento(e=("endereço", g._sem_acento(velha) + ", 101"), c=("cep", f"{cep[:5]}-{cep[5:]}"),
                               u=("cidade/uf", "FORTALEZA/CE"))
            with g.usando(random.Random(5), cidade=("FORTALEZA", "CE"), documento=antes):
                rua = g.parecido("endereço", antes[0]["texto"])
            self.assertEqual(g.logradouro_de(rua), g._normalizar(nova), cep)
            depois = coerencia.campos_finais(antes, [{"campo": "e", "novo": rua}])
            self.assertTrue(coerencia.comparar(antes, depois)["editada"]["regras"]["endereco_cep"]["ok"], rua)

    def test_bairro_da_cidade_e_cep_da_cidade(self):
        with g.usando(random.Random(39), "treino", cidade=("FORTALEZA", "CE")):
            bairros = [g.gerar("bairro")["texto"] for _ in range(300)] + [g.parecido("bairro", "CENTRO")
                                                                         for _ in range(100)]
            ceps = [g.gerar("cep")["texto"] for _ in range(300)] + [g.parecido("cep", "30123-456")
                                                                   for _ in range(100)]
        self.assertTrue(all(g.bairro_na_cidade(b, ("FORTALEZA", "CE")) for b in bairros))
        self.assertTrue(all(g.cep_na_cidade(c, ("FORTALEZA", "CE")) for c in ceps))
        self.assertFalse([c for c in ceps if c.endswith("899") or c[6] == "9"])
        contagem = collections.Counter(bairros)
        self.assertGreater(len(contagem), 80)  # bairros reais da cidade, pelo nº de domicílios
        with g.usando(random.Random(40), "treino"):
            sem_cidade = [g.parecido("cep", "60750-720") for _ in range(50)]  # a cidade do CEP antigo
        self.assertTrue(all(g.cep_na_cidade(c, ("FORTALEZA", "CE")) for c in sem_cidade))

    def test_cidades(self):
        L = g._lugares()
        amostra = random.Random(41).sample(sorted(L["mun"].values()), 600)
        for nome, uf, *_ in amostra:
            for texto in (f"{nome}/{uf}", f"{g._sem_acento(nome).lower()} - {uf.lower()}", f"{nome.title()}, {uf}"):
                self.assertEqual(g.cidade_do_texto(texto), (g._normalizar(nome), uf), texto)
        self.assertEqual(g.cidade_do_texto("BOM JESUS - PI"), ("BOM JESUS", "PI"))
        self.assertEqual(g.cidade_do_texto("BOM JESUS/RS"), ("BOM JESUS", "RS"))
        self.assertEqual(g.cidade_do_texto("ALTA FLORESTA D OESTE - RO"), ("ALTA FLORESTA D'OESTE", "RO"))
        self.assertEqual(g.cidade_do_texto("SAO JOAO DEL REI MG"), ("SAO JOAO DEL REI", "MG"))  # grafia do IBGE
        self.assertEqual(g.cidade_do_texto("EMBU GUACU - SP"), ("EMBU-GUACU", "SP"))  # OCR sem o hífen
        self.assertEqual(g.cidade_do_texto("Santa Bárbara d'Oeste/SP"), ("SANTA BARBARA D'OESTE", "SP"))
        self.assertEqual(g.ddd_de("Juazeiro do Norte", "CE"), "88")
        self.assertEqual(g.ddd_de("Águas Lindas de Goiás", "GO"), "61")  # entorno do DF
        vezes = collections.Counter(g.cidade_mais_parecida("FORTALEZA - CE", random.Random(i)) for i in range(400))
        self.assertGreater(sum(v for (c, uf), v in vezes.items() if uf == "CE") / 400, 0.7)
        self.assertGreater(len(vezes), 25)
        self.assertEqual(g.cidade_no_formato("Belo Horizonte/MG", "CONTAGEM", "MG"), "Contagem/MG")


@unittest.skipUnless(TEM_BASES, "sem as bases oficiais em config/vocabulario")
class TestCoerencia(unittest.TestCase):
    def _campos(self, **troca):
        base = {"rua": "RUA FRANCISCO ROCHA, 123", "cep": "80420-130", "bairro": "BATEL", "cidade": "CURITIBA/PR"}
        base.update(troca)
        return [{"id": "e", "categoria": "endereço", "texto": base["rua"]},
                {"id": "c", "categoria": "cep", "texto": base["cep"]},
                {"id": "b", "categoria": "bairro", "texto": base["bairro"]},
                {"id": "u", "categoria": "cidade/uf", "texto": base["cidade"]}]

    def test_cep_cidade_pela_base(self):
        regra = lambda **t: coerencia.avaliar(self._campos(**t))["regras"]["cep_cidade"]  # noqa: E731
        self.assertTrue(regra()["ok"])
        self.assertFalse(regra(cep="80318-436")["ok"])  # na faixa dos Correios, mas Curitiba não usa o prefixo
        self.assertFalse(regra(cep="01310-100")["ok"])
        self.assertTrue(regra(cep="76954-000", cidade="ALTA FLORESTA D'OESTE/RO")["ok"])  # CEP único: o geral
        self.assertFalse(regra(cep="76954-123", cidade="ALTA FLORESTA D'OESTE/RO")["ok"])

    def test_bairro_cidade_pela_base(self):
        regra = lambda **t: coerencia.avaliar(self._campos(**t))["regras"]["bairro_cidade"]  # noqa: E731
        self.assertTrue(regra()["ok"])
        self.assertTrue(regra(bairro="Jd. das Américas")["ok"])  # abreviação e acento não importam
        r = regra(bairro="ALDEOTA")
        self.assertFalse(r["ok"])
        self.assertIn("FORTALEZA/CE", r["detalhe"])
        self.assertIsNone(regra(bairro="XPTO DO NADA")["ok"])
        self.assertTrue(regra(bairro="CJ PREFEITO JOSE WALTER", cidade="FORTALEZA - CE", cep="60750-720")["ok"])

    def test_endereco_cep_pela_base(self):
        antes = self._campos(rua="RUA DAS FLORES, 123")
        for rua, ok in (("AVENIDA DO BATEL, 45", False), ("RUA FRANCISCO ROCHA, 45", True)):
            depois = coerencia.campos_finais(antes, [{"campo": "e", "novo": rua}])
            self.assertEqual(coerencia.comparar(antes, depois)["editada"]["regras"]["endereco_cep"]["ok"], ok, rua)


class TestSemBases(unittest.TestCase):
    """Sem os arquivos, o gerador cai no vocabulário embutido e tudo continua funcionando."""

    def test_vocabulario_embutido(self):
        with sem_bases():
            self.assertIsNone(g._nomes_base())
            self.assertIsNone(g._lugares())
            with g.usando(random.Random(42), "teste", cidade=("FORTALEZA", "CE")):
                for categoria, antigo in (("nome", "MARIA DA SILVA"), ("endereço", "RUA DAS FLORES, 101 APTO 12"),
                                          ("bairro", "CENTRO"), ("cep", "60750-720"), ("cidade/uf", "FORTALEZA - CE"),
                                          ("nome", "M R C FILHO FOTOS")):
                    self.assertTrue(g.parecido(categoria, antigo))
                    self.assertTrue(g.gerar(categoria)["texto"])
                self.assertRegex(g.cep_de("FORTALEZA", "CE"), r"^6[01]\d{3}-\d{3}$")
                self.assertEqual(g.cidade_do_texto("CURI T I BA / PR"), ("CURITIBA", "PR"))
            self.assertEqual(len(g._PARTES["cidades"]["treino"]) + len(g._PARTES["cidades"]["teste"])
                             + len(g._PARTES["cidades"]["validacao"]) + len(g._CAPITAIS_E_GRANDES),
                             len(g._CIDADES))
            r = coerencia.avaliar([{"categoria": "cep", "texto": "80318-436"},
                                   {"categoria": "cidade/uf", "texto": "CURITIBA/PR"}])
            self.assertTrue(r["regras"]["cep_cidade"]["ok"])  # sem a base, a faixa escrita à mão


if __name__ == "__main__":
    unittest.main()
