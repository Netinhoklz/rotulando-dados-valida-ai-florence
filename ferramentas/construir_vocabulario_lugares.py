"""Constrói a base de LUGARES do vocabulário (municípios, faixas de CEP, logradouros, bairros e estatísticas de
endereço) a partir de dados oficiais e abertos do IBGE, para o gerador trocar endereço, bairro, cidade e CEP por
valores reais e coerentes entre si (rua que existe na cidade, com o bairro e o CEP dela).

Fontes (todas baixadas para o diretório de cache, nunca para o projeto):
  * IBGE, API de localidades: nome oficial e código de cada município.
  * IBGE, SIDRA tabela 4709 (Censo 2022), variável 93: população residente por município.
  * kelvins/municipios-brasileiros (MIT): DDD e capital de cada município.
  * IBGE, Censo 2022, Agregados por Setores Censitários (básico): setor -> bairro oficial (com acento).
  * IBGE, CNEFE 2022 (Cadastro Nacional de Endereços para Fins Estatísticos): um zip por UF, ~3,6 GB no total.
  * FrequencyWords pt_br (CC BY-SA 4.0) e LibreOffice/dictionaries pt_BR (VERO, LGPLv3/MPL): só para restaurar
    os acentos dos nomes, que o CNEFE grava sem acento.

O CNEFE é lido em fluxo, direto do zip, uma UF por vez; o zip é apagado depois de agregado (a menos de
``--manter-zips``) e o agregado da UF fica no cache (``agregado_<NN>_<UF>.pkl.gz``). Rodar de novo reaproveita o
que já está no cache (downloads e agregados); ``--refazer`` agrega tudo de novo. A saída é determinística (semente
fixa, gzip sem data): rodar duas vezes com os mesmos dados dá os mesmos bytes.

Uso (na raiz do projeto):
    .venv\\Scripts\\python.exe ferramentas\\construir_vocabulario_lugares.py [--cache DIR] [--saida DIR]
        [--ufs RO,DF] [--manter-zips] [--refazer]

O formato de cada arquivo está em config/vocabulario/LEIA-ME_lugares.md.
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import heapq
import io
import json
import pickle
import random
import re
import sys
import tempfile
import time
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAIDA_PADRAO = RAIZ / "config" / "vocabulario"
CACHE_PADRAO = Path(tempfile.gettempdir()) / "rotulador_lugares"
VERSAO_AGREGADO = 2
SEMENTE = "lugares-cnefe-2022"

URL_MUNICIPIOS = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
URL_POPULACAO = "https://apisidra.ibge.gov.br/values/t/4709/n6/all/v/93/p/2022"
URL_DDD = "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv"
URL_SETORES = ("https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/"
               "Agregados_por_Setor_csv/Agregados_por_setores_basico_BR_20260520.zip")
URL_CNEFE = ("https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/"
             "Arquivos_CNEFE/CSV/UF/{cod}_{uf}.zip")
URL_DICIONARIO = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/pt_BR.dic"
URL_FREQUENCIA = ("https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/pt_br/"
                  "pt_br_full.txt")

UFS = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA", "22": "PI",
       "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
       "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}
# Faixas de CEP dos Correios por UF (prefixo de 5 dígitos, inclusive). CEP fora da faixa da UF é erro de cadastro.
FAIXAS_UF = {"SP": ((1000, 19999),), "RJ": ((20000, 28999),), "ES": ((29000, 29999),), "MG": ((30000, 39999),),
             "BA": ((40000, 48999),), "SE": ((49000, 49999),), "PE": ((50000, 56999),), "AL": ((57000, 57999),),
             "PB": ((58000, 58999),), "RN": ((59000, 59999),), "CE": ((60000, 63999),), "PI": ((64000, 64999),),
             "MA": ((65000, 65999),), "PA": ((66000, 68899),), "AP": ((68900, 68999),),
             "AM": ((69000, 69299), (69400, 69899)), "RR": ((69300, 69399),), "AC": ((69900, 69999),),
             "DF": ((70000, 72799), (73000, 73699)), "GO": ((72800, 72999), (73700, 76799)),
             "RO": ((76800, 76999),), "TO": ((77000, 77999),), "MT": ((78000, 78899),), "MS": ((79000, 79999),),
             "PR": ((80000, 87999),), "SC": ((88000, 89999),), "RS": ((90000, 99999),)}
# Porte do município pela população de 2022.
PORTES = (("ate_20mil", 0), ("20_a_100mil", 20_000), ("100_a_500mil", 100_000), ("500mil_ou_mais", 500_000))
# COD_ESPECIE do CNEFE 2022.
ESPECIES = {"1": "domicilio_particular", "2": "domicilio_coletivo", "3": "estabelecimento_agropecuario",
            "4": "estabelecimento_ensino", "5": "estabelecimento_saude", "6": "estabelecimento_outras_finalidades",
            "7": "edificacao_em_construcao", "8": "estabelecimento_religioso"}
# Amostra de logradouros: K por município (ver amostra_k).
K_MIN, K_MAX, K_DIVISOR = 20, 2500, 800
K_CAPITAL_MIN, K_CAPITAL_MAX, K_CAPITAL_DIVISOR = 3000, 8000, 400
RUAS_GRANDES = 20        # 1/20 da amostra são as ruas com mais endereços da cidade (entram sempre)
RE_ALFANUM = re.compile(r"\d+[A-Z]|[A-Z]\d+")
RE_DE_MARCO = re.compile(r"\b(\d+|UM|DOIS|TRÊS|QUATRO|CINCO|SEIS|SETE|OITO|NOVE|DEZ|ONZE|DOZE|TREZE|QUATORZE|CATORZE|"
                         r"QUINZE|DEZESSEIS|DEZESSETE|DEZOITO|DEZENOVE|VINTE|TRINTA|PRIMEIRO|1º|1O) DE MARCO\b")
# bairros "oficiais" que são divisões de planejamento (SETOR SOCIOECONOMICO 31, RP 6-29, REGIAO URBANA HOMOGENEA XV)
RE_ADMIN = re.compile(r"^(SUBSETOR|SETOR SOCIOECONOMICO|REGIAO (URBANA|ADMINISTRATIVA)|RP ?\d|AR ?\d|"
                      r"AREA (NAO URBANIZ|\d)|ZONA \d+$|\d+$)")
RE_LETRAS_PT = re.compile(r"[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ]+")
# marcadores de rua sem nome do recenseamento ("SEM DENOMINACAO 3", "SEM NOME", "DESCONHECIDO")
RE_SEM_NOME = re.compile(r"\bSEM ?(DENOM|NOME\b|IDENTIFICA)|\bDESCONHECID|"
                         r"\bNAO (INFORMAD|IDENTIFICAD|DENOMINAD|CONSTA)|\bIGNORAD|\bINEXISTENTE")
BAIRRO_MIN = 20         # bairros com pelo menos isso de endereços
CEP_PREFIXO_MIN = 3      # prefixos com menos endereços que isso são ruído de digitação
VALOR_MIN = 50           # valores de complemento com menos ocorrências que isso não saem nas estatísticas


def avisar(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def normalizar(texto: str) -> str:
    """Chave de comparação: sem acento, maiúsculas, só letras e dígitos separados por um espaço."""
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", sem_acento(texto).upper()).split())


def porte_de(pop: int) -> str:
    nome = PORTES[0][0]
    for rotulo, minimo in PORTES:
        if pop >= minimo:
            nome = rotulo
    return nome


def amostra_k(pop: int, capital: bool) -> int:
    """Quantos trechos de logradouro guardar do município: proporcional à população, com piso e teto; as capitais
    ganham mais (pelo menos K_CAPITAL_MIN)."""
    k = min(K_MAX, max(K_MIN, pop // K_DIVISOR))
    if capital:
        k = max(k, min(K_CAPITAL_MAX, max(K_CAPITAL_MIN, pop // K_CAPITAL_DIVISOR)))
    return k


def cep_na_uf(cep: str, uf: str) -> bool:
    if len(cep) != 8 or not cep.isdigit():
        return False
    p = int(cep[:5])
    return any(a <= p <= b for a, b in FAIXAS_UF[uf])


# ----------------------------------------------------------------------------------------------------------------
# download
# ----------------------------------------------------------------------------------------------------------------

def baixar(url: str, destino: Path, tentativas: int = 4) -> Path:
    """Baixa ``url`` para ``destino`` (pula se já existe e, sendo zip, está íntegro). Grava em .parcial e renomeia."""
    if destino.is_file() and destino.stat().st_size > 0:
        if destino.suffix != ".zip" or zipfile.is_zipfile(destino):
            return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    for tentativa in range(1, tentativas + 1):
        try:
            pedido = urllib.request.Request(url, headers={"User-Agent": "rotulador-vocabulario-lugares/1.0"})
            with urllib.request.urlopen(pedido, timeout=120) as resp, open(parcial, "wb") as saida:
                total = int(resp.headers.get("Content-Length") or 0)
                feito, proximo, t0 = 0, 0.25, time.time()
                while True:
                    bloco = resp.read(1 << 20)
                    if not bloco:
                        break
                    saida.write(bloco)
                    feito += len(bloco)
                    if total > 50 << 20 and feito / total >= proximo:
                        avisar(f"  {destino.name}: {feito / total:.0%} de {total / 2**20:.0f} MB "
                               f"({feito / 2**20 / max(time.time() - t0, 1e-3):.1f} MB/s)")
                        proximo += 0.25
            if total and parcial.stat().st_size != total:
                raise IOError(f"tamanho {parcial.stat().st_size} != {total}")
            if destino.suffix == ".zip" and not zipfile.is_zipfile(parcial):
                raise IOError("zip corrompido")
            parcial.replace(destino)
            return destino
        except Exception as erro:  # noqa: BLE001 - rede: tenta de novo
            avisar(f"  falha ao baixar {url} (tentativa {tentativa}/{tentativas}): {erro}")
            time.sleep(3 * tentativa)
    raise SystemExit(f"não consegui baixar {url}")


# ----------------------------------------------------------------------------------------------------------------
# municípios, população, DDD, setores
# ----------------------------------------------------------------------------------------------------------------

def carregar_municipios(cache: Path) -> dict[str, dict]:
    """cod_ibge -> {nome, uf, pop, ddd, capital}."""
    lista = json.loads(baixar(URL_MUNICIPIOS, cache / "municipios_ibge.json").read_text("utf-8"))
    sidra = json.loads(baixar(URL_POPULACAO, cache / "sidra_4709.json").read_text("utf-8"))
    ddd_csv = baixar(URL_DDD, cache / "kelvins_municipios.csv").read_text("utf-8-sig")
    muns = {}
    for m in lista:
        cod = str(m["id"])
        muns[cod] = {"nome": m["nome"], "uf": UFS[cod[:2]], "pop": 0, "ddd": "", "capital": 0}
    for linha in sidra[1:]:
        cod, valor = linha["D1C"], linha["V"]
        if cod in muns and valor.isdigit():
            muns[cod]["pop"] = int(valor)
    for linha in csv.DictReader(io.StringIO(ddd_csv)):
        m = muns.get(linha["codigo_ibge"])
        if m:
            m["ddd"] = linha["ddd"].strip()
            m["capital"] = 1 if linha["capital"].strip() == "1" else 0
    sem_pop = [f"{m['nome']}/{m['uf']}" for m in muns.values() if not m["pop"]]
    sem_ddd = [f"{m['nome']}/{m['uf']}" for m in muns.values() if not m["ddd"]]
    avisar(f"municípios: {len(muns)}; sem população 2022: {sem_pop or 'nenhum'}; sem DDD: {sem_ddd or 'nenhum'}; "
           f"capitais: {sum(m['capital'] for m in muns.values())}")
    return muns


def carregar_setores(cache: Path):
    """Da tabela básica do Censo 2022 por setor: setor (15 dígitos) -> "cod_bairro|situação" (U urbana, R rural;
    cod_bairro vazio se o setor não tem bairro oficial); cod_bairro -> nome oficial (com acento); e os nomes de lugares
    (bairros, distritos, municípios...) para o dicionário de acentos."""
    caminho = baixar(URL_SETORES, cache / "setores_basico.zip")
    setor_info, nome_bairro, nomes_lugares = {}, {}, set()
    colunas_nomes = ("NM_MUN", "NM_DIST", "NM_SUBDIST", "NM_BAIRRO", "NM_NU", "NM_FCU", "NM_AGLOM", "NM_RGINT",
                     "NM_RGI", "NM_CONCURB")
    with zipfile.ZipFile(caminho) as z, z.open(z.infolist()[0]) as fh:
        leitor = csv.reader(io.TextIOWrapper(fh, encoding="cp1252", errors="replace", newline=""), delimiter=";")
        cab = next(leitor)
        i_setor, i_cdb, i_nmb = cab.index("CD_SETOR"), cab.index("CD_BAIRRO"), cab.index("NM_BAIRRO")
        i_sit = cab.index("SITUACAO")
        i_nomes = [cab.index(c) for c in colunas_nomes]
        for linha in leitor:
            cdb = linha[i_cdb]
            if not (cdb and cdb != "." and linha[i_nmb]):
                cdb = ""
            else:
                nome_bairro[cdb] = " ".join(linha[i_nmb].replace("’", "'").split())
            setor_info[linha[i_setor]] = f"{cdb}|{'U' if linha[i_sit].startswith('U') else 'R'}"
            for i in i_nomes:
                if linha[i] and linha[i] != ".":
                    nomes_lugares.add(linha[i].strip())
    avisar(f"setores: {len(setor_info)} ({sum(1 for v in setor_info.values() if v[0] != '|')} com bairro oficial); "
           f"bairros oficiais: {len(nome_bairro)} em {len({c[:7] for c in nome_bairro})} municípios")
    return setor_info, nome_bairro, nomes_lugares


# ----------------------------------------------------------------------------------------------------------------
# acentos: o CNEFE grava os nomes sem acento ("NILO PECANHA"). A grafia com acento sai de três evidências:
#   1. nomes oficiais do IBGE com acento (bairros, distritos, municípios...), contados por nome distinto;
#   2. frequência das palavras no português do Brasil (FrequencyWords, legendas do OpenSubtitles);
#   3. radicais do dicionário pt_BR do LibreOffice (VERO), só para palavras raras (PEÇANHA).
# Na dúvida, a palavra fica sem acento (é como o CNEFE e muitas contas imprimem).
# ----------------------------------------------------------------------------------------------------------------

class Acentuador:
    # palavras de ligação ou ambíguas: nunca recebem acento
    PARADAS = {"DAS", "DOS", "NAS", "NOS", "AOS", "POR", "COM", "SEM", "SOB", "QUE", "PELO", "PELA", "ESTA",
               "ESTE", "AVO", "PAIS", "MAIS"}
    # palavras de 1 e 2 letras não recebem acento (siglas: PA, PE, MA...), menos estas
    CURTAS = {"FE": "FÉ", "SA": "SÁ"}

    def __init__(self) -> None:
        self.ibge: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.freq: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.dic: dict[str, set] = collections.defaultdict(set)
        self._cache: dict[str, str] = {}
        self._palavra: dict[str, str] = {}

    @staticmethod
    def _base(palavra: str) -> str | None:
        p = palavra.upper()
        if len(p) != len(palavra) or not p.isalpha() or not RE_LETRAS_PT.fullmatch(p):
            return None  # só os acentos do português (nada de PIÑAS, LUMIÈRE, MÜLLER)
        b = sem_acento(p)
        return b if len(b) == len(p) and b.isascii() else None

    def nome_ibge(self, nome: str) -> None:
        for tok in set(re.findall(r"[^\W\d_]+", nome.upper())):
            b = self._base(tok)
            if b:
                self.ibge[b][tok] += 1

    def frequencia(self, palavra: str, n: int) -> None:
        b = self._base(palavra)
        if b:
            self.freq[b][palavra.upper()] += n

    def radical(self, palavra: str) -> None:
        b = self._base(palavra)
        if b:
            self.dic[b].add(palavra.upper())

    @staticmethod
    def _acentuada(cont) -> tuple[str | None, int]:
        melhor = max(((n, v) for v, n in cont.items() if v != sem_acento(v)), default=(0, None))
        return melhor[1], melhor[0]

    def palavra(self, base: str) -> str:
        """A grafia de uma palavra em maiúsculas sem acento."""
        r = self._palavra.get(base)
        if r is not None:
            return r
        r = base
        if len(base) <= 2:
            r = self.CURTAS.get(base, base)
        elif base not in self.PARADAS:
            ib, fq, dc = self.ibge.get(base, {}), self.freq.get(base, {}), self.dic.get(base, set())
            vi, ni = self._acentuada(ib)
            pi = ib.get(base, 0)
            vf, nf = self._acentuada(fq)
            pf = fq.get(base, 0)
            acentuadas_dic = {v for v in dc if v != base}
            if vi and ni >= 3 and ni >= 2 * pi and (ni >= 5 or fq.get(vi, 0) * 10 >= pf):
                r = vi                                           # nomes de lugar: forte com acento (SABIÁ)
            elif pi >= 3 and pi >= 4 * ni:                       # nomes de lugar: forte sem acento
                r = base
            elif set(base) & set("KWY"):                         # grafia estrangeira (WOLNEY): sem acento
                r = base
            elif (vi and pi == 0 and (ni >= 2 or len(base) > 4)
                  and (not fq or fq.get(vi, 0) * 10 >= pf)):     # lugar só com acento (MÁRCIA)
                r = vi
            elif pf >= 3 and pf * 2 >= nf:                       # palavra comum sem acento (SALA, OVIEDO)
                r = base
            elif vf and nf >= 10:                                # palavra comum com acento (TÉRREO, PRAÇA)
                r = vf
            elif (len(base) >= 4 and pi == 0 and pf == 0 and len(acentuadas_dic) == 1 and base not in dc
                  and not base.endswith(("SON", "TON"))):        # EDILSON fica sem acento, como no registro
                r = next(iter(acentuadas_dic))                   # rara: o dicionário decide (PEÇANHA)
        self._palavra[base] = r
        return r

    def __call__(self, texto: str) -> str:
        r = self._cache.get(texto)
        if r is None:
            r = re.sub(r"[A-Z]+", lambda m: self.palavra(m.group(0)), texto)
            r = self._cache[texto] = RE_DE_MARCO.sub(r"\1 DE MARÇO", r)  # 31 DE MARCO: o mês, não o nome
        return r


def montar_acentuador(cache: Path, nomes_lugares: set[str], muns: dict) -> Acentuador:
    ac = Acentuador()
    for nome in nomes_lugares | {m["nome"] for m in muns.values()}:
        ac.nome_ibge(nome)
    with open(baixar(URL_FREQUENCIA, cache / "pt_br_full.txt"), encoding="utf-8", errors="replace") as f:
        for linha in f:
            partes = linha.split()
            if len(partes) == 2 and partes[1].isdigit():
                ac.frequencia(partes[0], int(partes[1]))
    dic = baixar(URL_DICIONARIO, cache / "pt_BR.dic").read_text("utf-8", errors="replace").splitlines()
    for linha in dic[1:]:
        ac.radical(linha.split("/", 1)[0].strip())
    avisar(f"acentos: {len(ac.ibge)} palavras de nomes do IBGE, {len(ac.freq)} de frequência, {len(ac.dic)} do "
           f"dicionário")
    return ac


# ----------------------------------------------------------------------------------------------------------------
# CNEFE: agregado por UF
# ----------------------------------------------------------------------------------------------------------------

def agregar_uf(caminho_zip: Path, uf: str, setor_info: dict, porte_mun: dict) -> dict:
    """Lê o zip do CNEFE da UF em fluxo e devolve o agregado:
    segs: "mun\\ttipo\\ttitulo\\tnome\\tlocalidade\\tcep\\tcod_bairro|situação" -> [n_enderecos, n_domicilios]
    (cod_bairro|situação do setor; "|" quando o setor do CNEFE não está na tabela final de setores)
    mun: cod -> [n_enderecos, n_domicilios]; especies: Counter; st: contadores das estatísticas (domicílios
    particulares)."""
    segs: dict[str, list] = {}
    mun_n: dict[str, list] = {}
    especies = collections.Counter()
    C = collections.Counter
    st = {"tipo": C(), "titulo": C(), "numero": C(), "modificador": C(), "elemento": C(), "combinacao": C(),
          "valor": C(), "tipo_valor": C()}
    st_tipo, st_tit, st_num, st_mod = st["tipo"], st["titulo"], st["numero"], st["modificador"]
    st_el, st_comb, st_val, st_tval = st["elemento"], st["combinacao"], st["valor"], st["tipo_valor"]
    n = 0
    t0 = time.time()
    with zipfile.ZipFile(caminho_zip) as z:
        for info in z.infolist():
            if not info.filename.lower().endswith(".csv"):
                continue
            with z.open(info) as fh:
                leitor = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""), delimiter=";")
                cab = next(leitor)
                if cab[:13] != ["COD_UNICO_ENDERECO", "COD_UF", "COD_MUNICIPIO", "COD_DISTRITO", "COD_SUBDISTRITO",
                                "COD_SETOR", "NUM_QUADRA", "NUM_FACE", "CEP", "DSC_LOCALIDADE", "NOM_TIPO_SEGLOGR",
                                "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR"] or cab[28] != "COD_ESPECIE":
                    raise SystemExit(f"{info.filename}: cabeçalho inesperado: {cab}")
                for row in leitor:
                    n += 1
                    if n % 5_000_000 == 0:
                        avisar(f"  {uf}: {n / 1e6:.0f} milhões de linhas ({n / (time.time() - t0) / 1e3:.0f} mil/s)")
                    mun, esp = row[2], row[28]
                    especies[esp] += 1
                    sinfo = setor_info.get(row[5][:15], "|")
                    chave = f"{mun}\t{row[10]}\t{row[11]}\t{row[12]}\t{row[9]}\t{row[8]}\t{sinfo}"
                    v = segs.get(chave)
                    if v is None:
                        segs[chave] = v = [0, 0]
                    v[0] += 1
                    m = mun_n.get(mun)
                    if m is None:
                        mun_n[mun] = m = [0, 0]
                    m[0] += 1
                    if esp != "1":
                        continue
                    v[1] += 1
                    m[1] += 1
                    p = porte_mun.get(mun, "ate_20mil")
                    st_tipo[(p, row[10])] += 1
                    st_tit[(p, row[11])] += 1
                    num = row[13].lstrip("0")
                    st_num[(p, len(num), num[:1])] += 1
                    st_mod[(p, row[14])] += 1
                    if row[15]:
                        nomes = []
                        for k in (15, 17, 19, 21, 23):
                            nm = row[k]
                            if not nm:
                                continue
                            val = row[k + 1]
                            nomes.append(nm)
                            st_el[(p, nm)] += 1
                            st_val[(nm, val)] += 1
                            if not val:
                                tv = "vazio"
                            elif val.isdigit():
                                tv = "numero"
                            elif len(val) == 1 and val.isalpha():
                                tv = "letra"
                            elif RE_ALFANUM.fullmatch(val):
                                tv = "alfanumerico"
                            else:
                                tv = "texto"
                            st_tval[(p, nm, tv)] += 1
                        st_comb[(p, "+".join(nomes))] += 1
                    else:
                        st_comb[(p, "")] += 1
    avisar(f"  {uf}: {n} linhas, {len(segs)} trechos distintos, {len(mun_n)} municípios "
           f"({time.time() - t0:.0f} s)")
    return {"versao": VERSAO_AGREGADO, "uf": uf, "segs": segs, "mun": mun_n, "especies": especies, "st": st}


def agregado_da_uf(cod: str, uf: str, args, setor_info: dict, porte_mun: dict) -> Path:
    destino = args.cache / f"agregado_{cod}_{uf}.pkl.gz"
    if destino.is_file() and not args.refazer:
        return destino
    caminho_zip = args.cache / f"{cod}_{uf}.zip"
    avisar(f"{uf}: baixando o CNEFE")
    baixar(URL_CNEFE.format(cod=cod, uf=uf), caminho_zip)
    avisar(f"{uf}: agregando {caminho_zip.stat().st_size / 2**20:.0f} MB")
    ag = agregar_uf(caminho_zip, uf, setor_info, porte_mun)
    parcial = destino.with_name(destino.name + ".parcial")
    with gzip.open(parcial, "wb", compresslevel=1) as f:
        pickle.dump(ag, f, protocol=pickle.HIGHEST_PROTOCOL)
    parcial.replace(destino)
    if not args.manter_zips:
        caminho_zip.unlink()
    return destino


# ----------------------------------------------------------------------------------------------------------------
# saída
# ----------------------------------------------------------------------------------------------------------------

class TSV:
    """Escritor de TSV (utf-8, \\n, com cabeçalho), em gzip determinístico quando o nome termina em .gz."""

    def __init__(self, caminho: Path, colunas: list[str]):
        self.caminho = caminho
        self.parcial = caminho.with_name(caminho.name + ".parcial")
        self._bruto = open(self.parcial, "wb")
        if caminho.suffix == ".gz":
            self._gz = gzip.GzipFile(filename="", mode="wb", fileobj=self._bruto, compresslevel=9, mtime=0)
            self._f = io.TextIOWrapper(self._gz, encoding="utf-8", newline="\n")
        else:
            self._gz = None
            self._f = io.TextIOWrapper(self._bruto, encoding="utf-8", newline="\n")
        self.linhas = 0
        self.escrever(colunas)
        self.linhas = 0

    def escrever(self, valores) -> None:
        self._f.write("\t".join(str(v).replace("\t", " ").replace("\n", " ") for v in valores) + "\n")
        self.linhas += 1

    def fechar(self) -> None:
        self._f.flush()
        self._f.detach()
        if self._gz:
            self._gz.close()
        self._bruto.close()
        self.parcial.replace(self.caminho)


def amostrar(itens: list, k: int, semente: str) -> list:
    """k itens sem reposição, com probabilidade proporcional ao peso (Efraimidis-Spirakis); itens = [(peso, x)]."""
    if len(itens) <= k:
        return [x for _, x in itens]
    rng = random.Random(semente)
    chaves = ((rng.random() ** (1.0 / peso), i) for i, (peso, _) in enumerate(itens))
    return [itens[i][1] for _, i in heapq.nlargest(k, chaves)]


def _maior(cont) -> str:
    """A chave de maior contagem (desempate pela maior chave, para ser determinístico); "" se vazio."""
    return max(cont.items(), key=lambda kv: (kv[1], kv[0]))[0] if cont else ""


def processar_saidas(args, muns: dict, nome_bairro: dict, acentuar: Acentuador, ufs: list[tuple[str, str]]) -> dict:
    saida: Path = args.saida
    saida.mkdir(parents=True, exist_ok=True)
    f_log = TSV(saida / "logradouros.tsv.gz",
                ["cod_ibge", "tipo", "titulo", "nome", "bairro", "cep", "urbano", "n_enderecos", "n_domicilios"])
    f_bai = TSV(saida / "bairros.tsv.gz", ["cod_ibge", "bairro", "urbano", "n_enderecos", "n_domicilios",
                                           "prefixos_cep", "oficial", "bairro_ibge"])
    f_cep = TSV(saida / "ceps_municipio.tsv.gz",
                ["cod_ibge", "prefixo", "n_enderecos", "n_ceps", "cep_min", "cep_max", "fracao"])
    # nome oficial do bairro por município (normalizado -> grafia oficial)
    oficiais_mun: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for cdb, nome in nome_bairro.items():
        oficiais_mun[cdb[:7]].setdefault(normalizar(nome), nome.upper())
    nome_mun = {cod: normalizar(m["nome"]) for cod, m in muns.items()}

    C = collections.Counter
    st_tot = {k: C() for k in ("tipo", "titulo", "numero", "modificador", "elemento", "combinacao", "valor",
                               "tipo_valor")}
    st_tipo_uf: dict[str, C] = {}
    cep_sufixo = C()
    especies = C()
    rel = C()  # contagens do relatório
    for cod_uf, uf in ufs:
        caminho = args.cache / f"agregado_{cod_uf}_{uf}.pkl.gz"
        avisar(f"{uf}: gerando as saídas")
        with gzip.open(caminho, "rb") as f:
            ag = pickle.load(f)
        if ag.get("versao") != VERSAO_AGREGADO:
            raise SystemExit(f"{caminho}: versão antiga do agregado; rode com --refazer")
        for nome, cont in ag["st"].items():
            st_tot[nome].update(cont)
        st_tipo_uf[uf] = C()
        for (p, tipo), n in ag["st"]["tipo"].items():
            st_tipo_uf[uf][tipo] += n
        especies.update(ag["especies"])
        for cod, (n_tot, n_dom) in ag["mun"].items():
            if cod in muns:
                muns[cod]["n_enderecos"] = n_tot
                muns[cod]["n_domicilios"] = n_dom
            else:
                rel["municipio_desconhecido"] += n_tot

        # 1) trecho = (mun, tipo, título, nome, localidade, CEP); bairro oficial e situação (urbana/rural) = os do
        #    setor conhecido com mais endereços do trecho ("|" se nenhum setor do trecho está na tabela final)
        seg6: dict[tuple, list] = {}
        for chave, (t, d) in ag["segs"].items():
            *k6, sinfo = chave.split("\t")
            k6 = tuple(k6)
            v = seg6.get(k6)
            if v is None:
                seg6[k6] = v = [0, 0, "|", 0]
            v[0] += t
            v[1] += d
            if sinfo != "|":
                rel["setor_conhecido"] += t
                if t > v[3] or (t == v[3] and sinfo > v[2]):
                    v[2], v[3] = sinfo, t
            else:
                rel["setor_desconhecido"] += t
        del ag

        # 2) CEP válido (8 dígitos, na faixa da UF) e dono do CEP (município com mais endereços nele)
        cep_mun = C()
        for (mun, _, _, _, _, cep), (t, *_) in seg6.items():
            if cep_na_uf(cep, uf):
                cep_mun[(cep, mun)] += t
            else:
                rel["cep_invalido"] += t
        dono: dict[str, tuple[int, str]] = {}
        for (cep, mun), t in cep_mun.items():
            atual = dono.get(cep)
            if atual is None or (t, -int(mun)) > (atual[0], -int(atual[1])):
                dono[cep] = (t, mun)
        del cep_mun

        # 3) localidade (DSC_LOCALIDADE) -> bairro exibido, bairro oficial onde ela fica e situação
        loc_ag: dict[tuple, list] = {}
        for (mun, _, _, _, loc, cep), (t, d, sinfo, _) in seg6.items():
            v = loc_ag.get((mun, loc))
            if v is None:
                loc_ag[(mun, loc)] = v = [0, 0, C(), C(), C()]
            v[0] += t
            v[1] += d
            if sinfo != "|":
                cdb, sit = sinfo.split("|")
                v[2][cdb] += t
                v[4][sit] += t
            if dono.get(cep, (0, None))[1] == mun:
                v[3][cep[:5]] += t
        exibir: dict[tuple, tuple] = {}  # (mun, loc) -> (bairro, bairro_ibge, oficial, urbano)
        for (mun, loc), (t, d, cdbs, _, sits) in loc_ag.items():
            ibge = nome_bairro.get(_maior(cdbs), "").upper()
            if RE_SEM_NOME.search(normalizar(ibge)):
                ibge = ""
            urbano = {"U": "1", "R": "0"}.get(_maior(sits), "")
            chave = normalizar(loc)
            if not chave or RE_SEM_NOME.search(chave) or chave == nome_mun.get(mun):
                # sem localidade de verdade (vazia, "SEM DENOMINACAO" ou o nome da própria cidade): não é bairro;
                # o trecho fica com o bairro oficial do setor dele, se houver (passo 6)
                e = ("", ibge, 0, urbano)
                rel["loc_generica"] += t
            elif ibge and normalizar(ibge) == chave:
                e = (ibge, ibge, 1, urbano)
            elif chave in oficiais_mun.get(mun, {}):
                e = (oficiais_mun[mun][chave], ibge, 1, urbano)
            else:
                e = (acentuar(" ".join(loc.split())), ibge, 0, urbano)
            exibir[(mun, loc)] = e
            rel["loc_oficial" if e[2] else "loc_nao_oficial"] += t
            if ibge:
                rel["loc_em_setor_com_bairro"] += t

        # 4) bairros (por município e grafia exibida)
        bai: dict[tuple, list] = {}
        for (mun, loc), (t, d, _, prefs, _) in loc_ag.items():
            nome, ibge, ofi, urbano = exibir[(mun, loc)]
            if not nome:
                continue
            v = bai.get((mun, nome))
            if v is None:
                bai[(mun, nome)] = v = [0, 0, C(), ofi, C(), C()]
            v[0] += t
            v[1] += d
            v[2].update(prefs)
            v[3] = max(v[3], ofi)
            if ibge:
                v[4][ibge] += t
            if urbano:
                v[5][urbano] += t
        for (mun, nome) in sorted(bai):
            t, d, prefs, ofi, ibges, urbs = bai[(mun, nome)]
            if t < BAIRRO_MIN:
                continue
            top = ",".join(p for p, _ in sorted(prefs.items(), key=lambda kv: (-kv[1], kv[0]))[:3])
            f_bai.escrever([mun, nome, _maior(urbs), t, d, top, ofi, _maior(ibges)])
            rel["bairros"] += 1
            rel["bairros_oficiais"] += ofi
        del loc_ag, bai

        # 5) CEPs por município (só os CEPs de que o município é dono)
        ceps_mun: dict[str, C] = collections.defaultdict(C)
        for (mun, _, _, _, _, cep), (t, d, *_) in seg6.items():
            if dono.get(cep, (0, None))[1] == mun:
                ceps_mun[mun][cep] += t
                cep_sufixo[("000" if cep.endswith("000") else "outro",)] += d
                if not cep.endswith("000"):
                    cep_sufixo[("ultimo_digito", cep[7])] += d
                    cep_sufixo[("centena", cep[5])] += d
                    cep_sufixo[("dezena_unidade_00", cep.endswith("00"))] += d
            elif cep_na_uf(cep, uf):
                rel["cep_de_outro_municipio"] += t
        for mun in sorted(ceps_mun):
            ceps = ceps_mun[mun]
            total = sum(ceps.values())
            por_pref: dict[str, list] = {}
            for cep, t in ceps.items():
                v = por_pref.get(cep[:5])
                if v is None:
                    por_pref[cep[:5]] = v = [0, 0, cep, cep]
                v[0] += t
                v[1] += 1
                v[2] = min(v[2], cep)
                v[3] = max(v[3], cep)
            for pref in sorted(por_pref):
                t, nc, cmin, cmax = por_pref[pref]
                if t < CEP_PREFIXO_MIN:
                    continue
                f_cep.escrever([mun, pref, t, nc, cmin, cmax, f"{t / total:.6f}"])
                rel["prefixos"] += 1
            if mun in muns:
                m = muns[mun]
                n000 = sum(t for cep, t in ceps.items() if cep.endswith("000"))
                geral = max(((t, cep) for cep, t in ceps.items() if cep.endswith("000")), default=(0, ""))
                m["cep_unico"] = 1 if total and n000 / total >= 0.9 else 0
                m["cep_geral"] = geral[1]
                m["n_ceps"] = len(ceps)
                m["cep_min"], m["cep_max"] = min(ceps), max(ceps)

        # 6) amostra de logradouros por município, com peso pelos domicílios
        por_mun: dict[str, list] = collections.defaultdict(list)
        for k6, (t, d, sinfo, _) in seg6.items():
            mun, tipo, tit, nome, loc, cep = k6
            if not nome.strip() or not tipo.strip() or dono.get(cep, (0, None))[1] != mun:
                continue
            if RE_SEM_NOME.search(nome):  # "SEM DENOMINACAO 3": rua sem nome, marcador do recenseamento
                rel["trechos_sem_nome"] += t
                continue
            por_mun[mun].append((d + 0.2 * (t - d), k6, t, d, sinfo))
        for mun in sorted(por_mun):
            itens = sorted(por_mun[mun], key=lambda x: x[1])
            pop = muns.get(mun, {}).get("pop", 0)
            k = amostra_k(pop, bool(muns.get(mun, {}).get("capital")))
            # as ruas grandes da cidade (k/RUAS_GRANDES pares rua+localidade com mais endereços; AV PAULISTA,
            # AV AFONSO PENA) entram sempre, pelo trecho de mais domicílios; o resto é sorteado com peso pelos
            # domicílios. Por localidade, para "RUA A" espalhada pela cidade não passar por rua grande.
            ruas: dict[tuple, list] = {}
            for it in itens:
                r = ruas.setdefault(it[1][1:5], [0, it])
                r[0] += it[2]
                if (it[0], it[1]) > (r[1][0], r[1][1]):
                    r[1] = it
            grandes = [r[1] for r in heapq.nlargest(min(len(ruas), -(-k // RUAS_GRANDES), k), ruas.values(),
                                                    key=lambda r: (r[0], r[1][1]))]
            ja = {it[1] for it in grandes}
            resto = [(peso, (k6, t, d, sinfo)) for peso, k6, t, d, sinfo in itens if k6 not in ja]
            escolhidos = ([(k6, t, d, sinfo) for _, k6, t, d, sinfo in grandes]
                          + amostrar(resto, k - len(grandes), f"{SEMENTE}:{mun}"))
            linhas: dict[tuple, list] = {}  # localidades diferentes podem dar o mesmo bairro: soma os trechos
            for (m_, tipo, tit, nome, loc, cep), t, d, sinfo in escolhidos:
                bairro, _, _, urbano = exibir[(m_, loc)]
                if sinfo != "|":
                    urbano = "1" if sinfo.endswith("U") else "0"
                    if not bairro:  # localidade genérica: o bairro oficial do setor do trecho
                        bairro = nome_bairro.get(sinfo.split("|")[0], "").upper()
                        if "(" in bairro or RE_SEM_NOME.search(normalizar(bairro)) or RE_ADMIN.search(
                                normalizar(bairro)):
                            bairro = ""  # "SUBSETOR OESTE - 7 (O-7)" é divisão de planejamento, não sai na conta
                chave = (mun, acentuar(" ".join(tipo.split())), acentuar(" ".join(tit.split())),
                         acentuar(" ".join(nome.split())), bairro, cep)
                v = linhas.get(chave)
                if v is None:
                    linhas[chave] = [urbano, t, d, t]
                else:
                    if t > v[3]:
                        v[0], v[3] = urbano, t
                    v[1] += t
                    v[2] += d
            for chave in sorted(linhas):
                urbano, t, d, _ = linhas[chave]
                f_log.escrever([*chave, urbano, t, d])
            rel["logradouros"] += len(linhas)
        del seg6, por_mun, dono

    f_log.fechar()
    f_bai.fechar()
    f_cep.fechar()
    return {"st": st_tot, "st_tipo_uf": st_tipo_uf, "cep_sufixo": cep_sufixo, "especies": especies, "rel": rel}


def escrever_municipios(caminho: Path, muns: dict) -> None:
    f = TSV(caminho, ["cod_ibge", "nome", "uf", "populacao_2022", "ddd", "capital", "porte", "n_enderecos_cnefe",
                      "n_domicilios_cnefe", "cep_unico", "cep_geral", "n_ceps", "cep_min", "cep_max"])
    for cod in sorted(muns):
        m = muns[cod]
        f.escrever([cod, m["nome"], m["uf"], m["pop"], m["ddd"], m["capital"], porte_de(m["pop"]),
                    m.get("n_enderecos", 0), m.get("n_domicilios", 0), m.get("cep_unico", 0), m.get("cep_geral", ""),
                    m.get("n_ceps", 0), m.get("cep_min", ""), m.get("cep_max", "")])
    f.fechar()


# ----------------------------------------------------------------------------------------------------------------
# estatísticas
# ----------------------------------------------------------------------------------------------------------------

def _ordenado(cont, limite=None, minimo=1) -> dict:
    itens = sorted(((k, v) for k, v in cont.items() if v >= minimo), key=lambda kv: (-kv[1], str(kv[0])))
    return dict(itens[:limite] if limite else itens)


def _por_porte(cont, limite=None, minimo=1) -> dict:
    """Counter[(porte, chave)] -> {porte: {chave: n}} na ordem dos portes."""
    saida = {}
    for porte, _ in PORTES:
        sub = collections.Counter({k[1] if len(k) == 2 else k[1:]: v for k, v in cont.items() if k[0] == porte})
        saida[porte] = _ordenado(sub, limite, minimo)
    return saida


def _soma(cont, idx=1) -> collections.Counter:
    s = collections.Counter()
    for k, v in cont.items():
        s[k[idx]] += v
    return s


def montar_estatisticas(r: dict, acentuar: Acentuador) -> dict:
    st = r["st"]
    total = sum(st["tipo"].values())
    por_porte_total = {p: sum(v for k, v in st["tipo"].items() if k[0] == p) for p, _ in PORTES}

    def ac_chaves(d: dict) -> dict:
        return {acentuar(k) if isinstance(k, str) else k: v for k, v in d.items()}

    tipos_nac = _soma(st["tipo"])
    tit_nac = _soma(st["titulo"])
    sem_tit = tit_nac.pop("", 0)
    tit_porte = {p: ac_chaves(_ordenado(collections.Counter(
        {k[1]: v for k, v in st["titulo"].items() if k[0] == p and k[1]}), 60)) for p, _ in PORTES}

    # número: dígitos e primeiro dígito
    def numero(filtro) -> dict:
        dig, prim = collections.Counter(), collections.Counter()
        for (p, nd, fd), v in st["numero"].items():
            if filtro(p):
                dig[nd] += v
                if nd:
                    prim[(nd, fd)] += v
        n = sum(dig.values())
        return {"n": n, "fracao_sem_numero": round(dig[0] / n, 6) if n else 0,
                "digitos": {str(k): dig[k] for k in sorted(dig)},
                "primeiro_digito_por_digitos": {str(nd): {fd: prim[(nd, fd)] for fd in "123456789"
                                                          if prim[(nd, fd)]}
                                                for nd in sorted({k[0] for k in prim})}}

    mods = _soma(st["modificador"])
    sem_mod = mods.pop("", 0)

    # complementos
    el_nac = _soma(st["elemento"])
    comb_nac = _soma(st["combinacao"])
    sem_comp = comb_nac.pop("", 0)
    valores: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for (nm, val), v in st["valor"].items():
        valores[nm][val] += v
    tval = collections.defaultdict(collections.Counter)
    for (p, nm, tv), v in st["tipo_valor"].items():
        tval[nm][tv] += v
    elementos = {}
    for nm, n in _ordenado(el_nac, minimo=VALOR_MIN).items():
        vals = valores[nm]
        elementos[acentuar(nm)] = {
            "n": n, "tipo_valor": dict(sorted(tval[nm].items(), key=lambda kv: -kv[1])),
            "valores_top": {acentuar(k) if k else "": v
                            for k, v in _ordenado(vals, 40, VALOR_MIN).items()}}

    # apartamentos: andar e unidade a partir do número de 3 ou 4 dígitos (101 = 1º andar, unidade 01)
    andar, unidade, dig_ap = collections.Counter(), collections.Counter(), collections.Counter()
    for val, v in valores.get("APARTAMENTO", {}).items():
        if val.isdigit():
            dig_ap[len(val.lstrip("0") or "0")] += v
            if len(val) in (3, 4) and val[0] != "0":
                andar[int(val[:-2])] += v
                unidade[val[-2:]] += v
    ap_top = _ordenado(collections.Counter({k: v for k, v in valores.get("APARTAMENTO", {}).items()
                                            if k.isdigit()}), 200, VALOR_MIN)

    comb_porte = {}
    for p, _ in PORTES:
        sub = collections.Counter({k[1]: v for k, v in st["combinacao"].items() if k[0] == p})
        nsub = sum(sub.values())
        sem = sub.pop("", 0)
        el_p = collections.Counter({k[1]: v for k, v in st["elemento"].items() if k[0] == p})
        comb_porte[p] = {"n_domicilios": nsub, "fracao_com_complemento": round(1 - sem / nsub, 6) if nsub else 0,
                         "elementos": ac_chaves(_ordenado(el_p, 40, VALOR_MIN)),
                         "combinacoes": ac_chaves(_ordenado(sub, 40, VALOR_MIN))}

    cs = r["cep_sufixo"]
    n_cep = cs[("000",)] + cs[("outro",)]
    n_outro = cs[("outro",)]
    return {
        "_descricao": {
            "fonte": "IBGE, CNEFE 2022 (Cadastro Nacional de Endereços para Fins Estatísticos), todos os 27 "
                     "arquivos por UF; contagens só dos domicílios particulares (COD_ESPECIE = 1), menos "
                     "'especies' e 'cep_sufixo' (este pondera por domicílio os CEPs válidos).",
            "portes": {p: f"população 2022 >= {m}" for p, m in PORTES},
            "acentos": "tipos, títulos, elementos e valores de texto com acento restaurado (o CNEFE grava sem "
                       "acento); tirar o acento devolve a grafia do CNEFE.",
            "chaves": "ver config/vocabulario/LEIA-ME_lugares.md",
        },
        "n_domicilios": total,
        "n_domicilios_por_porte": por_porte_total,
        "especies": {ESPECIES.get(k, k): v for k, v in _ordenado(r["especies"]).items()},
        "tipos_logradouro": {
            "nacional": ac_chaves(_ordenado(tipos_nac, minimo=VALOR_MIN)),
            "por_porte": {p: ac_chaves(d) for p, d in _por_porte(st["tipo"], 40, VALOR_MIN).items()},
            "por_uf": {uf: ac_chaves(_ordenado(c, 40, VALOR_MIN)) for uf, c in sorted(r["st_tipo_uf"].items())},
        },
        "titulos": {
            "fracao_com_titulo": round(1 - sem_tit / total, 6) if total else 0,
            "nacional": ac_chaves(_ordenado(tit_nac, 150, VALOR_MIN)),
            "por_porte": tit_porte,
        },
        "numero": {
            "nacional": numero(lambda p: True),
            "por_porte": {p: numero(lambda q, p=p: q == p) for p, _ in PORTES},
        },
        "modificadores": {
            "fracao_com_modificador": round(1 - sem_mod / total, 6) if total else 0,
            "nacional": ac_chaves(_ordenado(mods, 150, VALOR_MIN)),
            "por_porte": {p: ac_chaves(d) for p, d in _por_porte(
                collections.Counter({k: v for k, v in st["modificador"].items() if k[1]}), 40, VALOR_MIN).items()},
        },
        "complementos": {
            "fracao_com_complemento": round(1 - sem_comp / total, 6) if total else 0,
            "n_elementos_por_domicilio": _n_elementos(comb_nac, sem_comp),
            "elementos": elementos,
            "combinacoes": ac_chaves(_ordenado(comb_nac, 150, VALOR_MIN)),
            "por_porte": comb_porte,
        },
        "apartamentos": {
            "digitos": {str(k): v for k, v in sorted(dig_ap.items())},
            "andar": {str(k): v for k, v in sorted(andar.items()) if v >= VALOR_MIN},
            "unidade": {k: v for k, v in sorted(unidade.items()) if v >= VALOR_MIN},
            "valores_top": ap_top,
        },
        "cep_sufixo": {
            "fracao_000": round(cs[("000",)] / n_cep, 6) if n_cep else 0,
            "ultimo_digito": {d: cs[("ultimo_digito", d)] for d in "0123456789"},
            "centena": {d: cs[("centena", d)] for d in "0123456789"},
            "fracao_terminado_em_00": round(cs[("dezena_unidade_00", True)] / n_outro, 6) if n_outro else 0,
        },
    }


def _n_elementos(comb_nac: collections.Counter, sem_comp: int) -> dict:
    n = collections.Counter({0: sem_comp})
    for c, v in comb_nac.items():
        n[c.count("+") + 1] += v
    return {str(k): v for k, v in sorted(n.items())}


# ----------------------------------------------------------------------------------------------------------------

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, default=CACHE_PADRAO, help="onde baixar e guardar os agregados")
    ap.add_argument("--saida", type=Path, default=SAIDA_PADRAO, help="onde gravar os arquivos (config/vocabulario)")
    ap.add_argument("--ufs", default="", help="só estas UFs (ex.: RO,DF), para teste; a saída fica parcial")
    ap.add_argument("--manter-zips", action="store_true", help="não apaga os zips do CNEFE depois de agregar")
    ap.add_argument("--refazer", action="store_true", help="agrega o CNEFE de novo mesmo com agregado no cache")
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)
    escolhidas = {u.strip().upper() for u in args.ufs.split(",") if u.strip()}
    if escolhidas - set(UFS.values()):
        raise SystemExit(f"UF desconhecida: {sorted(escolhidas - set(UFS.values()))}")
    ufs = [(c, u) for c, u in sorted(UFS.items()) if not escolhidas or u in escolhidas]
    t0 = time.time()
    avisar(f"cache: {args.cache}; saída: {args.saida}; UFs: {', '.join(u for _, u in ufs)}")

    muns = carregar_municipios(args.cache)
    porte_mun = {cod: porte_de(m["pop"]) for cod, m in muns.items()}
    setor_info, nome_bairro, nomes_lugares = carregar_setores(args.cache)
    acentuar = montar_acentuador(args.cache, nomes_lugares, muns)
    del nomes_lugares

    for cod, uf in ufs:
        agregado_da_uf(cod, uf, args, setor_info, porte_mun)
    del setor_info

    r = processar_saidas(args, muns, nome_bairro, acentuar, ufs)
    escrever_municipios(args.saida / "municipios.tsv", muns)
    est = montar_estatisticas(r, acentuar)
    caminho = args.saida / "enderecos_estatisticas.json"
    caminho.write_text(json.dumps(est, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    rel = r["rel"]
    n_mun = sum(1 for m in muns.values() if m.get("n_enderecos"))
    unicos = sum(1 for m in muns.values() if m.get("cep_unico"))
    avisar(f"pronto em {(time.time() - t0) / 60:.1f} min: {n_mun} municípios com endereços, "
           f"{rel['logradouros']} logradouros, {rel['bairros']} bairros ({rel['bairros_oficiais']} com grafia "
           f"oficial), {rel['prefixos']} prefixos de CEP; CEP único em {unicos} municípios "
           f"({unicos / max(n_mun, 1):.1%}); endereços com CEP inválido: {rel['cep_invalido']}, com CEP de outro "
           f"município: {rel['cep_de_outro_municipio']}, em rua sem nome: {rel['trechos_sem_nome']}; setor "
           f"conhecido em {rel['setor_conhecido'] / max(rel['setor_conhecido'] + rel['setor_desconhecido'], 1):.1%} "
           f"dos endereços; localidade com grafia oficial em "
           f"{rel['loc_oficial'] / max(rel['loc_oficial'] + rel['loc_nao_oficial'], 1):.1%}")
    for nome in ("municipios.tsv", "logradouros.tsv.gz", "bairros.tsv.gz", "ceps_municipio.tsv.gz",
                 "enderecos_estatisticas.json"):
        avisar(f"  {nome}: {(args.saida / nome).stat().st_size / 2**20:.2f} MB")


if __name__ == "__main__":
    sys.exit(main())
