"""Gabarito do Brasil para ACHAR os campos: o que é nome de pessoa, município, bairro, tipo de logradouro e emissor.

Usa as bases locais de config/vocabulario (ver os LEIA-ME de lá): 100 mil prenomes do Censo 2010, 20 mil
sobrenomes do TSE, os 5.570 municípios do IBGE, os 278 mil bairros/localidades do CNEFE 2022 (com os prefixos de CEP
de cada um), os tipos de logradouro do CNEFE com as abreviações dos Correios e as siglas do DF, e uma lista de
concessionárias (``ferramentas/construir_gabarito.py``). Tudo carregado preguiçoso, uma vez por processo; sem os
arquivos, cada consulta responde "não sei" (None / 0) e o detector fica como antes.

As comparações são sem acento, em maiúsculas, com a pontuação virando espaço ("Estr." = "ESTR"; "S/N" = "S N").
"""
from __future__ import annotations

import csv
import gzip
import re
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path

VOCAB = Path(__file__).resolve().parents[1] / "config" / "vocabulario"
_lock = threading.Lock()

PARTICULAS = frozenset({"DA", "DE", "DO", "DAS", "DOS", "E", "D"})
SUFIXOS = frozenset({"FILHO", "FILHA", "JUNIOR", "JR", "NETO", "NETA", "SOBRINHO", "SEGUNDO", "TERCEIRO"})
# fim de razão social: o nome é de empresa (cliente pessoa jurídica ou o próprio emissor)
_EMPRESA = re.compile(r"(?:^|\s)(LTDA|ME|EPP|EIRELI|MEI|S A|SA|S/A|CIA|COMPANHIA|SOCIEDADE ANONIMA)$")
# complemento sozinho numa linha (debaixo da rua, no envelope): "BLOCO C APTO 204", "CASA 2", "LOTE 14 QD 3"
_COMPLEMENTO = re.compile(r"^(BLOCO|BL|APTO|AP|APT|APARTAMENTO|CASA|CS|LOTE|LT|QUADRA|QD|SALA|SL|LOJA|LJ|FUNDOS|"
                          r"TERREO|ANDAR|CONJ|CJ|EDIFICIO|ED|TORRE|UNIDADE|BOX|GALPAO|KM)\b")
FREQ_MINIMA = 50  # prenome com menos gente que isso no Censo não conta como prova de nome de pessoa
# tipo ambíguo raro no país (CENTRAL: 69 domicílios) não faz da linha um endereço ("Central de atendimento: 0800...")
AMBIGUO_MINIMO = 2000
# telefone não é número de casa: 0800, (85) 3333-4444, 3333-4444, 9 9999-9999
_TELEFONE = re.compile(r"\b0[358]00\b[\d ]*|\(?\b\d{2}\)?\s*9?\s?\d{4}\s?-?\s?\d{4}\b")
# marca de endereço no texto: "JOSÉ DE ALENCAR S/N" não é gente (sem número, as iniciais S e N davam nome)
_MARCA_ENDERECO = re.compile(r"\bS\s*/\s*N\b|\bN\s*[º°]|\bKM\b", re.I)
# títulos de homenagem abreviados (os por extenso vêm da coluna "titulo" dos logradouros do CNEFE)
TITULOS_ABREV = frozenset({"DR", "DRA", "PE", "PDE", "CEL", "GAL", "GEN", "MAL", "PROF", "PROFA", "DEP", "SEN", "VER",
                           "MAJ", "CAP", "CAPT", "TEN", "SGT", "ALM", "BRIG", "ENG", "DES", "MIN", "CMTE", "CONS",
                           "MONS", "PREF", "GOV", "PRES", "STA", "STO", "NSA"})
TITULO_MINIMO = 50  # o título aparece em tantas ruas do CNEFE (DOUTOR: 4.675; REVERENDO: 58)
PRENOME_NAO_TITULO = 1000  # SANTA (22 mil pessoas no Censo), SANTO, IRMA, MARQUES, PIO: prenome, não só título
# rua com o nome em tantos municípios é homenagem famosa (RUI BARBOSA: 336, GETÚLIO VARGAS: 447, JOSÉ DE ALENCAR: 81);
# nome de cliente quase nunca (JOSÉ DA SILVA: 15, MARIA DAS GRAÇAS PEREIRA: 0) — contado na amostra do CNEFE
HOMENAGEM_MINIMA = 40
# "RUI BARBOSA 455", "BARÃO DO RIO BRANCO, 1020", "JOSÉ DE ALENCAR S/N": rua conhecida sem o tipo, com o número
_SEM_TIPO = re.compile(r"^(?P<nome>[A-Z]+(?: [A-Z]+)+?) (?:N |NO |NUMERO )?(?:\d{1,5}[A-Z]?|S N|SN)(?: .*)?$")


def norm(texto: str) -> str:
    """Sem acento, maiúsculo, pontuação vira espaço (a barra e o hífen também), espaços simples."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", t)).strip()


def _ler(nome: str):
    p = VOCAB / nome
    if not p.is_file():
        return []
    abrir = gzip.open if nome.endswith(".gz") else open
    with abrir(p, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


@lru_cache(maxsize=1)
def _nomes() -> tuple[dict, dict]:
    with _lock:
        pre = {r["nome"]: int(r["freq_f"] or 0) + int(r["freq_m"] or 0) for r in _ler("prenomes.tsv.gz")}
        sob = {norm(r["ascii"] or r["sobrenome"]): int(r["n"] or 0) for r in _ler("sobrenomes.tsv.gz")}
    return pre, sob


@lru_cache(maxsize=1)
def _municipios() -> dict[str, list[tuple[str, str, str]]]:
    """nome normalizado -> [(nome oficial, UF, código IBGE)] (homônimos: BOM JESUS existe em 5 UFs)."""
    out: dict[str, list] = {}
    with _lock:
        for r in _ler("municipios.tsv"):
            out.setdefault(norm(r["nome"]), []).append((r["nome"], r["uf"], r["cod_ibge"]))
    return out


@lru_cache(maxsize=1)
def _ufs() -> frozenset:
    return frozenset(uf for lst in _municipios().values() for _, uf, _ in lst)


@lru_cache(maxsize=1)
def _bairros() -> tuple[dict, dict, dict]:
    """(por município: {cod: {bairro}}, global: {bairro: n municípios}, por prefixo de CEP: {prefixo5: {bairro}})."""
    por_mun: dict[str, set] = {}
    glob: dict[str, int] = {}
    por_cep: dict[str, set] = {}
    with _lock:
        for r in _ler("bairros.tsv.gz"):
            b = norm(r["bairro"])
            if not b:
                continue
            por_mun.setdefault(r["cod_ibge"], set()).add(b)
            glob[b] = glob.get(b, 0) + 1
            for p in (r.get("prefixos_cep") or "").split(","):
                if p.strip():
                    por_cep.setdefault(p.strip(), set()).add(b)
    return por_mun, glob, por_cep


@lru_cache(maxsize=1)
def _tipos() -> list[tuple[tuple[str, ...], str, str]]:
    """[(variante em palavras, tipo canônico, força)], as de mais palavras primeiro ("ESTRADA MUNICIPAL" antes de
    "ESTRADA"). Ambíguo raro (``AMBIGUO_MINIMO``) fica de fora; o acrescentado à mão (n = 0) fica."""
    out = []
    for r in _ler("tipos_logradouro.tsv"):
        n = int(r.get("n_domicilios") or 0)
        if r["forca"] == "ambiguo" and 0 < n < AMBIGUO_MINIMO:
            continue
        for v in r["variantes"].split("|"):
            if v.strip():
                out.append((tuple(norm(v).split()), r["tipo"], r["forca"]))
    return sorted(out, key=lambda t: -len(t[0]))


@lru_cache(maxsize=1)
def _emissores() -> list[tuple[tuple[str, ...], str]]:
    out = []
    for r in _ler("emissores.tsv"):
        for a in r["apelidos"].split("|"):
            if norm(a):
                out.append((tuple(norm(a).split()), r["nome"]))
    return sorted(out, key=lambda t: -len(t[0]))


@lru_cache(maxsize=1)
def _ruas() -> tuple[dict, dict, frozenset]:
    """Dos 342 mil trechos de rua do CNEFE: ({nome da rua: {códigos IBGE}}, {prefixo de CEP: {nomes}}, títulos de
    homenagem). O nome entra sem o tipo, com e sem o título ("RIO BRANCO" e "BARAO DO RIO BRANCO")."""
    por_nome: dict[str, set] = {}
    por_cep: dict[str, set] = {}
    titulos: dict[str, int] = {}
    with _lock:
        for r in _ler("logradouros.tsv.gz"):
            nome, tit = norm(r["nome"]), norm(r["titulo"])
            if not nome:
                continue
            for n in ((nome, f"{tit} {nome}") if tit else (nome,)):
                por_nome.setdefault(n, set()).add(r["cod_ibge"])
                por_cep.setdefault((r["cep"] or "")[:5], set()).add(n)
            if tit:
                titulos[tit.split()[0]] = titulos.get(tit.split()[0], 0) + 1
    pre = _nomes()[0]
    tits = {t for t, n in titulos.items() if n >= TITULO_MINIMO and pre.get(t, 0) < PRENOME_NAO_TITULO}
    return por_nome, por_cep, frozenset(tits | TITULOS_ABREV)


def disponivel() -> bool:
    return bool(_nomes()[0]) and bool(_municipios())


# ---------------------------------------------------------------------------------------------- consultas
def nome_de_pessoa(texto: str) -> float:
    """0..1: quanto o texto parece nome de pessoa. Duas palavras ou mais, sem dígito; a 1ª é prenome do Censo
    (com ``FREQ_MINIMA`` pessoas) ou inicial ("M R C FILHO"); a nota é a fração das palavras que são prenome,
    sobrenome do TSE, partícula (DA, DOS), sufixo (FILHO, JUNIOR) ou inicial. "INFORMAÇÕES DA INSTALAÇÃO" e "AVISO
    DE DÉBITO PENDENTE" dão perto de 0; "JOÃO BATISTA DOS SANTOS", 1. Começar por título de homenagem ("PRESIDENTE
    KENNEDY", "DR. JOSÉ MARIA") ou ter S/N, Nº, KM é lugar: 0. A homenagem sem título ("GETÚLIO VARGAS") dá 1 aqui; quem
    separa é a posição no envelope e ``homenagem``."""
    p = norm(texto).split()
    if len(p) < 2 or any(ch.isdigit() for ch in "".join(p)) or _MARCA_ENDERECO.search(str(texto or "")):
        return 0.0
    if titulo(texto):
        return 0.0
    pre, sob = _nomes()
    if not pre:
        return 0.0

    def conhecida(w: str) -> bool:
        return (pre.get(w, 0) >= FREQ_MINIMA or w in sob or w in PARTICULAS or w in SUFIXOS or len(w) == 1)
    if not (pre.get(p[0], 0) >= FREQ_MINIMA or len(p[0]) == 1):
        return 0.0
    if len(p[0]) > 1 and tipo_logradouro(" ".join(p) + " 1"):  # "RUA DAS FLORES": há gente chamada Rua; é endereço
        return 0.0
    if all(w in PARTICULAS or len(w) == 1 for w in p):
        return 0.0
    return sum(conhecida(w) for w in p) / len(p)


def titulo(texto: str) -> bool:
    """A 1ª palavra é título de homenagem de rua (DOUTOR, PADRE, CORONEL, PRESIDENTE, BARÃO, DR., PE....)."""
    p = norm(texto).split()
    return bool(p) and p[0] in _ruas()[2]


def homenagem(texto: str) -> int:
    """Em quantos municípios há rua com exatamente esse nome (sem o tipo): GETÚLIO VARGAS 447, JOSÉ DA SILVA 15. Com
    ``HOMENAGEM_MINIMA`` ou mais, a linha solta é o lugar, não o cliente."""
    return len(_ruas()[0].get(norm(texto), ()))


def logradouro_sem_tipo(texto: str, cod_ibge: str | None = None, cep: str | None = None) -> str:
    """A linha é rua conhecida SEM o tipo, seguida do número ou S/N ("RUI BARBOSA 455", "JOSÉ DE ALENCAR S/N"):
    "cidade" se a rua existe no município ``cod_ibge``, "cep" se no prefixo do ``cep``, "pais" se existe em algum
    lugar; "" se não. O nome tem 2 palavras ou mais (a rua "BRASIL" sozinha é palavra comum demais)."""
    m = _SEM_TIPO.match(norm(texto))
    if not m or tipo_logradouro(texto) or complemento(texto):
        return ""
    nome = m.group("nome")
    por_nome, por_cep, _ = _ruas()
    cods = por_nome.get(nome)
    if not cods:
        return ""
    if cod_ibge and str(cod_ibge) in cods:
        return "cidade"
    d = re.sub(r"\D", "", cep or "")
    if len(d) >= 5 and nome in por_cep.get(d[:5], ()):
        return "cep"
    return "pais"


def empresa(texto: str) -> bool:
    """Razão social: termina em LTDA, ME, EIRELI, S/A, CIA..."""
    return bool(_EMPRESA.search(norm(texto)))


def complemento(texto: str) -> bool:
    """A linha é um complemento sozinho ("BLOCO C APTO 204", "CASA 2", "QD 3 LT 14")."""
    t = norm(texto)
    return bool(_COMPLEMENTO.match(t)) and bool(re.search(r"\d|\b[A-Z]\b", t.split(" ", 1)[1] if " " in t else ""))


def municipio(texto: str) -> tuple[str, str, str] | None:
    """(nome oficial, UF, código IBGE) se o texto INTEIRO é um município, com ou sem a UF ("FORTALEZA", "Fortaleza -
    CE", "BELO HORIZONTE MG", "Curitiba/PR"). Homônimo sem UF: o primeiro da lista (o IBGE ordena por código)."""
    p = norm(texto).split()
    if not p:
        return None
    uf = p[-1] if len(p) >= 2 and len(p[-1]) == 2 and p[-1] in _ufs() else None
    nome = " ".join(p[:-1] if uf else p)
    achados = _municipios().get(nome)
    if not achados:
        return None
    if uf:
        achados = [a for a in achados if a[1] == uf]
        if not achados:
            return None
    return achados[0]


def bairro(texto: str, cod_ibge: str | None = None, cep: str | None = None) -> str:
    """"cidade" se o texto inteiro é um bairro do município ``cod_ibge``; "cep" se é um bairro que usa o prefixo do
    ``cep`` (5 dígitos); "pais" se é bairro em algum lugar do Brasil (em ``n`` municípios); "" se não é."""
    b = norm(texto)
    if not b or any(ch.isdigit() for ch in b):
        return ""
    por_mun, glob, por_cep = _bairros()
    m = re.match(r"^(DISTRITO|POVOADO|LOCALIDADE|COMUNIDADE|VILA) (DE |DO |DA |DOS |DAS )?(.+)$", b)
    if m and b not in glob and (m.group(3) in glob or m.group(3) in _municipios()):
        return "pais"  # "DISTRITO DE JUÁ": a localidade com o prefixo que a conta escreve
    if cod_ibge and b in por_mun.get(str(cod_ibge), ()):
        return "cidade"
    d = re.sub(r"\D", "", cep or "")
    if len(d) >= 5 and b in por_cep.get(d[:5], ()):
        return "cep"
    if b in glob:
        return "pais"
    if " " in b and len(b.replace(" ", "")) >= 4:  # o OCR inventa espaços ("AL D EOTA"): procura sem eles
        junto = _bairros_juntos().get(b.replace(" ", ""))
        if junto:
            return bairro(junto, cod_ibge, cep)
    return ""


@lru_cache(maxsize=1)
def _bairros_juntos() -> dict[str, str]:
    """bairro sem espaços -> o bairro normalizado ("ALDEOTA" -> "ALDEOTA", "ASANORTE" -> "ASA NORTE")."""
    return {k.replace(" ", ""): k for k in _bairros()[1]}


def municipios_do_bairro(texto: str) -> int:
    """Em quantos municípios existe um bairro com esse nome (CENTRO: quase todos; ALDEOTA: 3)."""
    return _bairros()[1].get(norm(texto), 0)


def tipo_logradouro(texto: str) -> tuple[str, str] | None:
    """(tipo canônico, força) se a linha começa com um tipo de logradouro que FAZ dela um endereço:
    forte (RUA, AV., ESTR., R...) seguido de um nome; ambíguo (VILA, SÍTIO, CONDOMÍNIO...) só com número, S/N ou KM
    na linha; sigla do DF (SQN, QI...) seguida de número. "LINHA DIGITÁVEL", "CENTRO" sozinho: None."""
    p = norm(texto).split()
    if len(p) < 2:
        return None
    sem_tel = _TELEFONE.sub(" ", " ".join(p))
    tem_numero = bool(re.search(r"\d", sem_tel)) or bool(re.search(r"\bS N\b|\bSN\b|\bKM\b", sem_tel))
    for var, tipo, forca in _tipos():
        n = len(var)
        if tuple(p[:n]) != var or len(p) <= n:
            continue
        resto = p[n:]
        if forca == "forte":
            # "R" sozinho é letra solta demais ("R JOAO CORDEIRO 1100" sim, "R" de uma sigla não): exige número
            if len(var) == 1 and len(var[0]) == 1 and not tem_numero:
                continue
            # abreviação curta sem número exige uma palavra de verdade depois: "AL D EOTA" é o OCR quebrando ALDEOTA
            if len(var) == 1 and len(var[0]) <= 3 and not tem_numero and len(resto[0]) < 3:
                continue
            return tipo, forca
        if forca == "df":
            if resto[0].isdigit() or re.match(r"^\d", resto[0]):
                return tipo, forca
            continue
        if tem_numero:
            return tipo, forca
    return None


def emissor(texto: str) -> str | None:
    """A concessionária que o texto nomeia (palavra inteira: "COMPANHIA ENERGETICA DO CEARA - ENEL" → ENEL)."""
    p = norm(texto).split()
    for var, nome in _emissores():
        n = len(var)
        for i in range(len(p) - n + 1):
            if tuple(p[i:i + n]) == var:
                return nome
    return None
