"""Constrói a base de NOMES de pessoas do vocabulário (prenomes, sobrenomes, estrutura do nome completo e mapa de
acentos) a partir de dados oficiais e abertos, para o gerador trocar nomes por valores com a forma e a frequência
dos nomes reais do país.

Fontes (baixadas para o diretório de cache, nunca para o projeto):
  * IBGE, Censo Demográfico 2010, prenomes por sexo (via brasil.io, dataset genero-nomes, nomes.csv.gz).
  * TSE, dados abertos de candidaturas (consulta_cand, todas as eleições de 2004 a 2024): o nome civil completo de
    ~1,9 milhão de pessoas. Usado SÓ para estatística agregada: frequência de sobrenomes e da partícula que os
    precede, estrutura do nome (prenome composto, devoção, sufixo) e uso de acento. Privacidade: nenhum nome
    completo é gravado (nem no cache: os zips são lidos em fluxo e só contagens ficam em memória), sobrenome visto
    menos de 5 vezes é descartado e nenhuma combinação prenome+sobrenome real é gravada.

Saída (config/vocabulario/): prenomes.tsv.gz, sobrenomes.tsv.gz, acentos.tsv.gz e nomes_estrutura.json. O
formato de cada arquivo está em config/vocabulario/LEIA-ME_nomes.md. A saída é determinística (gzip sem data,
ordem fixa): rodar duas vezes com os mesmos dados dá os mesmos bytes. Os zips do TSE (~610 MB) são apagados no
fim, a menos de ``--manter-zips``; rodar de novo baixa outra vez.

Uso (na raiz do projeto):
    .venv\\Scripts\\python.exe ferramentas\\construir_vocabulario_nomes.py [--cache DIR] [--saida DIR] [--manter-zips]
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAIDA_PADRAO = RAIZ / "config" / "vocabulario"
CACHE_PADRAO = Path(tempfile.gettempdir()) / "rotulador_nomes"

URL_CENSO = "https://data.brasil.io/dataset/genero-nomes/nomes.csv.gz"
URL_TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_{}.zip"
# Todas as eleições de 2004 a 2024 (municipais trazem ~450 mil candidatos cada; gerais ~25 mil), para a cauda dos
# sobrenomes passar de 5 ocorrências. O uso de acento é o mesmo em todos os anos (15–20% dos nomes). A ordem
# importa: quem se candidatou em mais de um ano conta uma vez, com a grafia do ano mais recente.
ANOS_TSE = (2024, 2022, 2020, 2018, 2016, 2014, 2012, 2010, 2008, 2006, 2004)

MIN_PRENOME = 10       # prenome do Censo com ao menos 10 pessoas (o IBGE já corta abaixo de 20: entram todos)
MIN_SOBRENOME = 5      # privacidade: sobrenome visto menos de 5 vezes no TSE não é gravado
MIN_PAR = 5            # idem para pares de prenomes (ANA PAULA), devoções e listas da estrutura
MIN_ACENTO_N = 3       # palavra do mapa de acentos: vista ao menos 3 vezes no TSE
LIMIAR_ACENTO = 0.10   # a forma acentuada vale se ao menos 10% das ocorrências têm acento (ver _canonica)
DOMINANCIA = 0.60      # ... e se uma grafia acentuada tem ao menos 60% das ocorrências acentuadas
R_PRENOME = 0.5        # 2º token é prenome se aparece mais como 1º prenome que como último sobrenome
TOP_LISTA = 400        # tamanho das listas da estrutura (compostos, 2º prenome...)

PARTICULAS = ("DA", "DE", "DO", "DOS", "DAS", "E")
COLUNAS_PARTICULA = ("nenhuma",) + PARTICULAS
SUFIXOS = ("FILHO", "FILHA", "JUNIOR", "JR", "NETO", "NETA", "NETTO", "SOBRINHO", "SOBRINHA", "SEGUNDO",
           "TERCEIRO", "II", "III")
# O TSE grava D'ÁVILA, SANT'ANA e DALL'AGNOL como "D AVILA", "SANT ANA", "DALL AGNOL": junta de volta com apóstrofo.
ELISAO = {"D", "SANT", "DALL", "DELL"}
# Prefixos que não são sobrenome sozinhos: colam no seguinte (VILAS BOAS, ESPÍRITO SANTO, SÃO PEDRO, SANTA ROSA,
# BOA MORTE, DAL MOLIN, DI DOMENICO, VON MUHLEN, VAN DER BERG, ORO NAO).
PREFIXOS = {"SAO", "SANTA", "VILA", "VILAS", "VILLA", "VILLAS", "BOA", "BOM", "ESPIRITO", "DAL", "DALLA", "DELLA",
            "DEL", "DI", "VON", "VAN", "DER", "DEN", "TEN", "LA", "LE", "MAC", "MC", "ORO", "MONT"}
# Complementos devocionais do prenome (MARIA DE FÁTIMA, RITA DE CÁSSIA, JOSÉ DE RIBAMAR): logo depois do prenome e
# com sobrenome depois, quase nunca são o último sobrenome no TSE (DE FÁTIMA: 4216 vezes no meio, 35 no fim).
DEVOCOES = ("DE FATIMA", "DO SOCORRO", "DO PERPETUO SOCORRO", "DE CASSIA", "DE CACIA", "DE KASSIA", "DE LOURDES",
            "DE LURDES", "DAS GRACAS", "DA GRACA", "DAS CHAGAS", "DAS DORES", "DA PENHA", "DE NAZARE", "DE NAZARETH",
            "DE RIBAMAR", "DA GLORIA", "DO ESPIRITO SANTO", "DA GUIA", "DO ROCIO", "DE PADUA", "DAS MERCES",
            "DE ARIMATEIA", "DE ARIMATEA", "DO LIVRAMENTO", "DO AMPARO", "DA CONSOLACAO", "DE TARSO", "DOS REMEDIOS",
            "DA PIEDADE", "DOS MILAGRES", "DE ANCHIETA", "DAS VITORIAS", "DO RAMO", "DA SOLIDADE", "DA SOLEDADE",
            "DO CEU", "DO DESTERRO", "DOS NAVEGANTES", "DE LELIS", "DA APARECIDA", "D'ARC")
# ... e os que também são sobrenome comum (DA CONCEIÇÃO, DE ASSIS, BATISTA): devoção só depois destes prenomes.
# "DE JESUS" fica como sobrenome (no meio e no fim do nome, quase na mesma proporção); a estrutura dá a taxa à parte.
DEVOCOES_DO_PRENOME = {
    "MARIA": ("DA CONCEICAO", "DO CARMO", "DO ROSARIO", "DA PAZ", "DA LUZ", "DAS NEVES", "DOS ANJOS"),
    "FRANCISCO": ("DE ASSIS", "DE PAULA"), "FRANCISCA": ("DE ASSIS",), "VICENTE": ("DE PAULA", "DE PAULO"),
    "JOAO": ("DE DEUS", "BATISTA", "BOSCO", "EVANGELISTA"), "RAIMUNDO": ("NONATO",), "LUIZ": ("GONZAGA",),
    "LUIS": ("GONZAGA",)}
GENEROS = {"FEMININO": "F", "MASCULINO": "M"}

# Palavras comuns em endereço (ruas, bairros, cidades) que não são (só) nome de pessoa, na grafia oficial, para o
# mapa de acentos: o CNEFE traz os nomes sem acento. Só palavras sem ambiguidade num nome de logradouro.
ACENTOS_ENDERECO = """
PRAÇA ESTAÇÃO CHÁCARA CHÁCARAS SÍTIO SÍTIOS CÓRREGO RIBEIRÃO NÚCLEO ÁREA CONDOMÍNIO EDIFÍCIO PRÉDIO TÉRREO
BALNEÁRIO ESTÂNCIA PORTÃO PÁTIO VÁRZEA MÓDULO SEÇÃO BOQUEIRÃO SERTÃO CAPÃO CHAPADÃO ROTATÓRIA VIÁRIO TÚNEL
TRÊS
RODOVIÁRIA FERROVIÁRIA METRÔ CÍVICO UNIVERSITÁRIO UNIVERSITÁRIA OLÍMPICO OLÍMPICA HÍPICA AERONÁUTICA MARÍTIMA
MARÍTIMO AGRÍCOLA OPERÁRIO OPERÁRIA OPERÁRIOS ECOLÓGICO ECOLÓGICA TURÍSTICO BOTÂNICO ZOOLÓGICO MARÉ CÉU
AMAPÁ CEARÁ ESPÍRITO GOIÁS MARANHÃO PARÁ PARAÍBA PARANÁ PIAUÍ RONDÔNIA SÃO AMAZÔNIA BRASÍLIA ATLÂNTICO
ATLÂNTICA PACÍFICO
BELÉM MACEIÓ MACAPÁ VITÓRIA GOIÂNIA LUÍS CUIABÁ JOÃO FLORIANÓPOLIS NITERÓI PETRÓPOLIS TERESÓPOLIS ANÁPOLIS
UBERLÂNDIA MARINGÁ JUNDIAÍ JACAREÍ TAUBATÉ GUARUJÁ JAÚ ITAJAÍ CAMAÇARI PARANAGUÁ IGUAÇU ARAÇATUBA TIETÊ
ICARAÍ TATUAPÉ BUTANTÃ JAÇANÃ MAUÁ GUAÍBA CAMBORIÚ CRICIÚMA CHAPECÓ IJUÍ GRAVATAÍ SANTARÉM MARABÁ ARAGUAÍNA
PARNAÍBA MOSSORÓ ILHÉUS VALENÇA CANAÃ POÇOS TEÓFILO DIVINÓPOLIS MACAÉ GONÇALO MARÍLIA SUMARÉ HORTOLÂNDIA
BRAGANÇA GUARATINGUETÁ CUBATÃO PERUÍBE ITANHAÉM PARANAVAÍ TUBARÃO JARAGUÁ PALHOÇA BAGÉ VIAMÃO LUZIÂNIA
VALPARAÍSO CATALÃO JATAÍ CORUMBÁ PORÃ RONDONÓPOLIS CAMETÁ CODÓ MARACANAÚ CAICÓ JABOATÃO ANTÃO JEQUIÉ
EUNÁPOLIS IBIRITÉ MURIAÉ ARAXÁ CARAPICUÍBA TABOÃO SIMÕES ITAÚNA PIRAÍ ITAGUAÍ MARICÁ ITABORAÍ MAGÉ NILÓPOLIS
SEROPÉDICA GRAJAÚ ANDARAÍ MARACANÃ IRAJÁ JACAREPAGUÁ GÁVEA BRÁS ACLIMAÇÃO SAÚDE LIMÃO
FÁTIMA GRAÇAS NAZARÉ ROSÁRIO CONCEIÇÃO ASSUNÇÃO ANUNCIAÇÃO RESSURREIÇÃO REDENÇÃO SALVAÇÃO ASCENSÃO CONSOLAÇÃO
MISERICÓRDIA CANDELÁRIA CALVÁRIO PARAÍSO GLÓRIA JERUSALÉM SIÃO JORDÃO BETÂNIA SANTÍSSIMA SEBASTIÃO CRISTÓVÃO
JERÔNIMO LÁZARO TOMÉ SIMÃO ANTÔNIO JOSÉ INÁCIO EFIGÊNIA LÚCIA CECÍLIA MÔNICA BÁRBARA INÊS ÚRSULA ÁGUEDA ROMÃO
DAMIÃO
GETÚLIO PEÇANHA EPITÁCIO BONIFÁCIO ÁLVARES PATROCÍNIO ANÍSIO OSÓRIO TAMANDARÉ HUMAITÁ CAMÕES NÓBREGA TIBIRIÇÁ
FERNÃO CAPITÃO BARÃO CÔNEGO IRMÃO PRÍNCIPE ACADÊMICO EXPEDICIONÁRIO EXPEDICIONÁRIOS VOLUNTÁRIOS
PÁTRIA FERROVIÁRIOS COMERCIÁRIOS BANCÁRIOS FUNCIONÁRIOS INDUSTRIÁRIOS PORTUÁRIOS RODOVIÁRIOS MARÍTIMOS
ESPERANÇA UNIÃO CONCÓRDIA LIBERTAÇÃO ABOLIÇÃO INDEPENDÊNCIA REPÚBLICA CONSTITUIÇÃO PROCLAMAÇÃO
INCONFIDÊNCIA CONFEDERAÇÃO FEDERAÇÃO NAÇÕES AMÉRICA AMÉRICAS ÁFRICA ÁSIA ITÁLIA JAPÃO LÍBANO SÍRIA GRÉCIA
ÁUSTRIA BÉLGICA SUÍÇA MÉXICO CANADÁ PANAMÁ BOLÍVIA COLÔMBIA ÍNDIA RÚSSIA FRANÇA POLÔNIA SUÉCIA FINLÂNDIA
ESCÓCIA MOÇAMBIQUE GUINÉ
IPÊ IPÊS ACÁCIA ACÁCIAS JATOBÁ JATOBÁS JACARANDÁ JACARANDÁS JEQUITIBÁ JEQUITIBÁS MARACUJÁ ARAÇÁ CAJÁ BABAÇU
AÇAÍ CAFÉ ALGODÃO MAMÃO MAÇÃ HORTÊNSIA HORTÊNSIAS ORQUÍDEA ORQUÍDEAS BEGÔNIA BEGÔNIAS MAGNÓLIA MAGNÓLIAS
GARDÊNIA GARDÊNIAS CRISÂNTEMO CRISÂNTEMOS GIRASSÓIS LÍRIO LÍRIOS GERÂNIO GERÂNIOS CAMÉLIA CAMÉLIAS DÁLIA
DÁLIAS PETÚNIA PETÚNIAS ÍRIS RÉGIA SABIÁ SABIÁS CANÁRIO CANÁRIOS PAVÃO GAVIÃO FALCÃO ÁGUIA ÁGUIAS TUIUIÚ
CARCARÁ LEÃO PÁSSARO PÁSSAROS PLANÍCIE TRÓPICO SATÉLITE
"""
# Ficam de fora do mapa (dependem do contexto, o integrador decide): MARCO/MARÇO (MARCO POLO × 25 DE MARÇO),
# MARQUES/MARQUÊS (sobrenome × MARQUÊS DE POMBAL), ROMA/ROMÃ, IRMA/IRMÃ, MEIER/MÉIER, SE/SÉ (SÉ × setor SE/UF).
# Palavras comuns (conferidas no dicionário pt_BR do LibreOffice) cuja forma acentuada no TSE vem de poucos nomes ou
# de grafia antiga: num endereço (RUA CONDE DE..., BAIRRO DA LAGOA, RUA DA LUA) ficam sem acento.
COMUNS_SEM_ACENTO = set("""
LUA PRA IRA CEO CONDE CABO CONTO CAMPANHA RENO NOIA CIRCO PLACA POUCO GIRO MOTE DONDE PEDO BORO GENE PATE GARE
JARRO BILE BOTOS PINOS LARICA BRUCO PERO CADO DORE SANITA CHALO LAGOA JAVA CHISTE
""".split())
SIGLAS_UF = set("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())

_SO_LETRAS = re.compile(r"[A-ZÀ-ÖØ-Ý]+(?: [A-ZÀ-ÖØ-Ý]+)*")
_APOSTROFOS = re.compile(r"[´`'’‘]")
TONICAS = set("ÁÉÍÓÚÂÊÔ")
GRAVES = set("ÀÈÌÒÙ")
_VOGAIS = re.compile(r"[AEIOUYÁÉÍÓÚÂÊÔÃÕÀÈÌÒÙÜÖÄËÏ]+")


# --------------------------------------------------------------------------- texto

def _ascii(texto: str) -> str:
    """Sem acento (JOSÉ → JOSE, GONÇALVES → GONCALVES, D'ÁVILA → D'AVILA)."""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def _limpar(nome: str) -> str | None:
    """Nome do TSE em maiúsculas, com acento, apóstrofo virando espaço (como o próprio TSE grava) e espaços
    simples; None se sobra algo que não é letra (dígito, hífen, vírgula: ~100 nomes)."""
    nome = unicodedata.normalize("NFC", nome.replace("\xa0", " ")).upper()
    nome = " ".join(_APOSTROFOS.sub(" ", nome).split())
    return nome if _SO_LETRAS.fullmatch(nome) else None


def _tokens(nome: str) -> list[str]:
    """Palavras do nome, com D/SANT/DALL + palavra juntados por apóstrofo (D'ÁVILA, SANT'ANA); nunca o 1º token."""
    bruto, saida, k = nome.split(), [], 0
    while k < len(bruto):
        if 0 < k < len(bruto) - 1 and _ascii(bruto[k]) in ELISAO and _ascii(bruto[k + 1]) not in PARTICULAS:
            saida.append(bruto[k] + "'" + bruto[k + 1])
            k += 2
        else:
            saida.append(bruto[k])
            k += 1
    return saida


def _sem_sufixo(a: list[str]) -> tuple[list[str], str | None]:
    """(nome sem o sufixo FILHO/JÚNIOR/NETO..., o sufixo); só no fim de um nome com 3 palavras ou mais."""
    if len(a) >= 3 and a[-1] in SUFIXOS:
        return a[:-1], a[-1]
    return a, None


def _tem_sobrenome(a: list[str], j: int) -> bool:
    return any(w not in PARTICULAS and len(w) >= 2 for w in a[j:])


def _grafia_valida(palavra: str) -> bool:
    """Grafia acentuada plausível: sem acento grave (JOSÈ é erro de digitação), no máximo um acento tônico e sem o
    acento diferencial anterior a 1971 (MACÊDO, LÔBO, CÔRTES, IÊDA: tônico na penúltima sílaba de paroxítona
    terminada em A/E/O(S), que a grafia atual não acentua; o acento de hiato, RAÍSSA, SAÚDE, ARAÚJO, continua)."""
    if any(c in GRAVES for c in palavra) or sum(c in TONICAS for c in palavra) > 1:
        return False
    grupos = list(_VOGAIS.finditer(palavra))
    for k, grupo in enumerate(grupos):
        tonica = [c for c in grupo.group() if c in TONICAS]
        if tonica and tonica[0] not in "ÍÚ" and k == len(grupos) - 2:
            ultimo = grupos[-1]
            if ultimo.group() in ("A", "E", "O") and palavra[ultimo.end():] in ("", "S"):
                return False
    return True


def _canonica(ascii_: str, formas: collections.Counter) -> tuple[str, float]:
    """(grafia canônica, fração das ocorrências escritas nela). A grafia é a acentuada mais comum quando o acento
    é de verdade e não erro de digitação: ao menos LIMIAR_ACENTO das ocorrências com acento válido, ao menos 2
    delas, e uma grafia com DOMINANCIA das acentuadas. O cartório e o cadastro eleitoral muitas vezes omitem o
    acento (JOSÉ sai acentuado em cerca de metade dos registros, ANTÔNIO em 1 de cada 5, ARAÚJO em 1 de cada 3),
    enquanto o erro fica abaixo de 2% (MARÍA 0,02%, JÉSUS 0,2%); daí 10% e não maioria. Sem acento: (a própria
    palavra, 0.0)."""
    n = sum(formas.values())
    validas = collections.Counter({f: c for f, c in formas.items()
                                   if f != ascii_ and all(_grafia_valida(p) for p in re.split(r"[ ']", f))})
    acentuadas = sum(validas.values())
    if n and acentuadas >= 2 and acentuadas >= LIMIAR_ACENTO * n:
        forma, c = min(validas.items(), key=lambda x: (-x[1], x[0]))
        if c >= DOMINANCIA * acentuadas:
            return forma, c / n
    return ascii_, 0.0


def _regras_sufixo(por_palavra: dict) -> dict:
    """Regra conservadora para o prenome do Censo que o TSE não viu (ou viu menos de 3 vezes): o final da palavra
    (3 a 6 letras) decide, somando as ocorrências de todas as palavras do TSE (vistas 3+ vezes) com esse final,
    com o mesmo critério de ``_canonica`` (ao menos 10% das ocorrências com o final acentuado, 60% das acentuadas
    na mesma grafia) e, para não generalizar demais, se o final acentuado aparece (em 3%+ das ocorrências) em 80%
    dessas palavras e elas são ao menos 10 (ACAO → AÇÃO, ELIO → ÉLIO, ENCIA → ÊNCIA). Vale o final MAIS LONGO com
    dados: se ele diz "sem acento", fica sem (LIA: JÚLIA tem o acento antes do final). Validado na construção
    desta base (eleições 2020–2024), metade das palavras do TSE contra a outra metade: 97% de acerto quando dispara.
    {final: (final acentuado ou None, fração de uso do acento somada)}"""
    soma = collections.defaultdict(collections.Counter)
    por_final = collections.defaultdict(list)
    for w, formas in por_palavra.items():
        n = sum(formas.values())
        if n < MIN_ACENTO_N or not w.isalpha():
            continue
        for k in range(3, 7):
            if len(w) <= k:
                break
            finais = collections.Counter()
            for forma, c in formas.items():
                if len(forma) == len(w) and _grafia_valida(forma):
                    finais[forma[-k:]] += c
            soma[w[-k:]].update(finais)
            por_final[w[-k:]].append((finais, n))
    regras = {}
    for final, cont in soma.items():
        palavras = por_final[final]
        if len(palavras) < 10:
            continue
        total = sum(n for _, n in palavras)
        acentuadas = collections.Counter({f: c for f, c in cont.items() if f != final})
        regras[final] = (None, 0.0)
        if acentuadas:
            forma, c = min(acentuadas.items(), key=lambda x: (-x[1], x[0]))
            coerente = sum(1 for finais, n in palavras if finais[forma] >= 0.03 * n) / len(palavras)
            if c >= LIMIAR_ACENTO * total and c >= DOMINANCIA * sum(acentuadas.values()) and coerente >= 0.8:
                regras[final] = (forma, c / total)
    return regras


# Romanização japonesa/chinesa (HISAO, MASAO, TADAO, ZHAO, XIAO, JIAO): o final -AO não vira -ÃO.
_ASIATICO = re.compile(r"ZH|XI|Q(?!U)|TS|SH|[AEIOU][SD]AO$|CHAO$|^[HJ]?IAO$")


def _por_regra(w: str, regras: dict) -> tuple[str, float] | None:
    if _ASIATICO.search(w):
        return None
    for k in range(6, 2, -1):
        if len(w) > k and w[-k:] in regras:
            final, frac = regras[w[-k:]]
            return (w[:-k] + final, frac) if final else None
    return None


# --------------------------------------------------------------------------- fontes

def _baixar(url: str, destino: Path) -> None:
    if destino.exists():
        print(f"  já no cache: {destino.name}")
        return
    print(f"  baixando {url} ...", flush=True)
    parcial = destino.with_name(destino.name + ".part")
    pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (rotulador; vocabulario de nomes)"})
    with urllib.request.urlopen(pedido, timeout=300) as resposta, open(parcial, "wb") as arquivo:
        shutil.copyfileobj(resposta, arquivo, 1 << 20)
    if destino.suffix == ".zip" and not zipfile.is_zipfile(parcial):
        parcial.unlink()
        raise SystemExit(f"download corrompido (não é zip): {url}")
    os.replace(parcial, destino)
    print(f"  ok: {destino.name} ({destino.stat().st_size / 1e6:.1f} MB)")


def _censo(caminho: Path) -> dict[str, tuple[int, int, int]]:
    """{prenome ASCII: (mulheres, homens, total)} do Censo 2010."""
    censo = {}
    with gzip.open(caminho, "rt", encoding="utf-8", newline="") as arquivo:
        for linha in csv.DictReader(arquivo):
            censo[linha["first_name"]] = (int(linha["frequency_female"] or 0), int(linha["frequency_male"] or 0),
                                          int(linha["frequency_total"]))
    return censo


def _pessoas(cache: Path):
    """(nome limpo, gênero F/M/N) de cada pessoa das candidaturas, uma vez só: quem aparece em mais de um ano ou
    turno (mesmo nome sem acento e mesma data de nascimento) conta uma vez. Para reconhecer a repetição guarda só
    um hash de 8 bytes, nunca o nome."""
    vistos = set()
    for ano in ANOS_TSE:
        with zipfile.ZipFile(cache / f"consulta_cand_{ano}.zip") as z:
            csvs = [n for n in z.namelist() if n.upper().endswith("_BRASIL.CSV")] or \
                   [n for n in z.namelist() if n.lower().endswith(".csv")]
            for nome_csv in csvs:
                with z.open(nome_csv) as bruto:
                    texto = io.TextIOWrapper(bruto, encoding="latin-1", newline="")
                    for linha in csv.DictReader(texto, delimiter=";"):
                        nome = _limpar(linha["NM_CANDIDATO"])
                        if not nome:
                            continue
                        chave = hashlib.blake2b(f"{_ascii(nome)}|{linha['DT_NASCIMENTO']}".encode(),
                                                digest_size=8).digest()
                        if chave in vistos:
                            continue
                        vistos.add(chave)
                        yield nome, GENEROS.get(linha["DS_GENERO"], "N")


# --------------------------------------------------------------------------- análise

def _passo1(cache: Path):
    """Grafias de cada palavra (para o acento) e quantas vezes cada palavra é o 1º prenome ou o último sobrenome
    sem partícula antes (para separar prenome de sobrenome no passo 2)."""
    formas, primeiro, ultimo = collections.Counter(), collections.Counter(), collections.Counter()
    for nome, _ in _pessoas(cache):
        formas.update(nome.split())
        a, _ = _sem_sufixo([_ascii(t) for t in _tokens(nome)])
        primeiro[a[0]] += 1
        if len(a) >= 2 and a[-2] not in PARTICULAS:
            ultimo[a[-1]] += 1
    return formas, primeiro, ultimo


def _devocao(a: list[str], i: int, prenome: str) -> int:
    """Quantas palavras tem o complemento devocional que começa em a[i] (0 se não há); precisa sobrar sobrenome."""
    for comp in _SEQ_DEVOCOES + _SEQ_DO_PRENOME.get(prenome, ()):
        k = len(comp)
        if tuple(a[i:i + k]) == comp and _tem_sobrenome(a, i + k):
            return k
    return 0


_SEQ_DEVOCOES = tuple(sorted((tuple(d.split()) for d in DEVOCOES), key=len, reverse=True))
_SEQ_DO_PRENOME = {p: tuple(sorted((tuple(d.split()) for d in ds), key=len, reverse=True))
                   for p, ds in DEVOCOES_DO_PRENOME.items()}


def _composto(w: str) -> bool:
    """Sobrenome de mais de uma palavra (VILAS BOAS) ou com apóstrofo (D'ÁVILA): tem grafias próprias."""
    return " " in w or "'" in w


def _unidades(formas: list[str], a: list[str]) -> list[tuple[str, str, str]]:
    """Sobrenomes depois do prenome: [(partícula ou "", grafia, ASCII)], com os prefixos colados (VILAS BOAS)."""
    saida, particula, j = [], "", 0
    while j < len(a):
        if a[j] in PARTICULAS:
            particula, j = a[j], j + 1
            continue
        fs, ws = [formas[j]], [a[j]]
        while ws[-1] in PREFIXOS and j + 1 < len(a) and a[j + 1] not in PARTICULAS:
            j += 1
            fs.append(formas[j])
            ws.append(a[j])
        if len(ws[-1]) >= 2:
            saida.append((particula, " ".join(fs), " ".join(ws)))
        particula, j = "", j + 1
    return saida


def _passo2(cache: Path, censo: dict, primeiro, ultimo, acentuaveis: set[str]):
    """Classifica cada nome (prenome simples/composto/devocional, sobrenomes com partícula, sufixo) e acumula só
    contagens."""
    C = collections.Counter
    dd = collections.defaultdict
    E = {"pessoas": C(), "palavras": dd(C), "tipo": dd(C), "compostos": dd(C), "segundo": dd(C),
         "por_primeiro": dd(C), "devocoes": dd(C), "n_sobrenomes": dd(C), "n_sob_tipo": dd(C),
         "part_pos": dd(C), "sufixo": dd(C), "de_jesus": dd(C), "e_entre": C(), "e_antes": C(), "e_depois": C(),
         "acento": dd(C), "estilo": C()}
    sob_n, sob_part, sob_formas = C(), dd(C), dd(C)

    def segundo_prenome(a, g):
        if len(a) < 3:
            return False
        w = a[1]
        if w in PARTICULAS or w not in censo or not _tem_sobrenome(a, 2):
            return False
        p, u = primeiro[w], ultimo[w]
        if p == 0 or p < R_PRENOME * (p + u):
            return False
        f, m, _ = censo[w]
        return {"F": f >= m or w == "JOSE", "M": m >= f or w == "MARIA"}.get(g, True)  # MARIA JOSÉ, JOSÉ MARIA

    for nome, g in _pessoas(cache):
        palavras = nome.split()
        toks = _tokens(nome)
        a = [_ascii(t) for t in toks]
        E["pessoas"][g] += 1
        E["palavras"][g][len(toks)] += 1
        a, suf = _sem_sufixo(a)
        toks = toks[:len(a)]
        E["sufixo"][g][suf or "nenhum"] += 1

        i, tipo, dev = 1, "simples", None
        k = _devocao(a, 1, a[0])
        if k:
            i, tipo, dev = 1 + k, "devocional", " ".join(a[1:1 + k])
        elif segundo_prenome(a, g):
            i, tipo = 2, "composto"
            E["compostos"][g][(a[0], a[1])] += 1
            E["segundo"][g][a[1]] += 1
            k = _devocao(a, 2, a[1])
            if k:
                i, tipo, dev = 2 + k, "composto_devocional", " ".join(a[2:2 + k])
        if dev:
            E["devocoes"][g][(dev, a[0])] += 1
        E["tipo"][g][tipo] += 1
        E["por_primeiro"][g][(a[0], tipo)] += 1

        unidades = _unidades(toks[i:], a[i:])
        n = len(unidades)
        E["n_sobrenomes"][g][min(n, 5)] += 1
        E["n_sob_tipo"][tipo][min(n, 5)] += 1
        if 1 <= n <= 4:
            for j, (p, _, _) in enumerate(unidades):
                E["part_pos"][(n, j)][p or "nenhuma"] += 1
        for p, forma, w in unidades:
            sob_n[w] += 1
            sob_part[w][p or "nenhuma"] += 1
            if _composto(w):
                sob_formas[w][forma] += 1
        if any(u[0] == "E" for u in unidades[1:]):
            E["e_entre"][g] += 1
            for j in range(1, n):
                if unidades[j][0] == "E":
                    E["e_antes"][unidades[j - 1][2]] += 1
                    E["e_depois"][unidades[j][2]] += 1
        jesus = [j for j, u in enumerate(unidades) if u[0] == "DE" and u[2] == "JESUS"]
        E["de_jesus"][g]["qualquer"] += bool(jesus)
        E["de_jesus"][g]["logo_apos_prenome"] += bool(jesus) and jesus[0] == 0
        E["de_jesus"][g]["ultimo"] += bool(jesus) and jesus[-1] == n - 1

        acento = E["acento"][g]
        acento["com_acento"] += any(p != _ascii(p) for p in palavras)
        acento["til"] += any(c in "ÃÕ" for c in nome)
        acento["cedilha"] += "Ç" in nome
        acento["agudo"] += any(c in "ÁÉÍÓÚ" for c in nome)
        acento["circunflexo"] += any(c in "ÂÊÔ" for c in nome)
        acento["trema"] += any(c in "ÜÖÄËÏ" for c in nome)
        pode = [p for p in palavras if _ascii(p) in acentuaveis]
        if pode:
            feitas = sum(p != _ascii(p) for p in pode)
            acento["nomes_acentuaveis"] += 1
            acento["nomes_acentuaveis_com_acento"] += feitas > 0
            acento["palavras_acentuaveis"] += len(pode)
            acento["palavras_acentuaveis_com_acento"] += feitas
            if len(pode) >= 2:
                E["estilo"]["nenhuma" if not feitas else "todas" if feitas == len(pode) else "parte"] += 1
    return E, sob_n, sob_part, sob_formas


# --------------------------------------------------------------------------- saída

def _gravar(caminho: Path, dados: bytes) -> None:
    temporario = caminho.with_name(caminho.name + ".tmp")
    temporario.write_bytes(dados)
    os.replace(temporario, caminho)


def _gravar_tsv(caminho: Path, cabecalho: tuple, linhas) -> int:
    buffer, n = io.BytesIO(), 0
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, compresslevel=9, mtime=0) as gz:
        gz.write(("\t".join(cabecalho) + "\n").encode("utf-8"))
        for linha in linhas:
            gz.write(("\t".join(map(str, linha)) + "\n").encode("utf-8"))
            n += 1
    _gravar(caminho, buffer.getvalue())
    return n


def _f(x: float) -> str:
    return f"{x:.4f}"


def _taxa(a: int, b: int) -> float:
    return round(a / b, 6) if b else 0.0


DESCRICAO_CHAVES = {
    "fontes": "de onde vieram os dados; quantas pessoas do TSE entraram (deduplicadas por nome e nascimento); "
              "sobrenomes distintos no TSE, quantos foram gravados (5+ ocorrências) e a fração das ocorrências de "
              "sobrenome que os gravados cobrem (o resto é a cauda rara, que não sai do TSE por privacidade)",
    "parametros": "limiares usados na construção (ver LEIA-ME_nomes.md)",
    "pessoas": "pessoas do TSE por gênero (F, M, N = não divulgado); base das taxas",
    "palavras_por_nome": "por gênero: nº de palavras do nome civil (contando partículas e sufixo; D'ÁVILA conta 1) "
                         "-> nº de pessoas",
    "tipo_prenome": "por gênero: simples (MARIA), composto (MARIA EDUARDA), devocional (MARIA DE FÁTIMA), "
                    "composto_devocional (MARIA APARECIDA DE FÁTIMA) -> nº de pessoas; taxa = valor / pessoas",
    "compostos": "por gênero: [par de prenomes com acento, nº de pessoas], do mais comum, contagem >= 5; só os dois "
                 "prenomes, nunca sobrenome",
    "segundo_prenome": "por gênero: [2º prenome dos compostos, nº], para formar compostos fora da lista",
    "por_primeiro_prenome": "por gênero: 1º prenome (ASCII, como em prenomes.tsv.gz) -> {n, composto, devocional} "
                            "= nº de pessoas com esse 1º prenome e a fração delas com 2º prenome / com devoção "
                            "(inclui composto_devocional nas duas); os 400 mais comuns",
    "devocoes": "lista de complementos devocionais (DE FÁTIMA, DAS CHAGAS, BATISTA...) com total, por gênero e os "
                "1ºs prenomes a que se ligam (contagem >= 5); taxa_F/taxa_M = fração das pessoas do gênero",
    "sobrenomes_por_nome": "por gênero: nº de sobrenomes (sem contar partícula e sufixo; 5 = 5 ou mais) -> nº",
    "sobrenomes_por_tipo_prenome": "o mesmo, por tipo de prenome (devocional tende a ter menos sobrenomes)",
    "particula_por_posicao": "para nomes com k = 1..4 sobrenomes: lista de k distribuições (1º, 2º... sobrenome) "
                             "partícula -> nº (nenhuma, DA, DE, DO, DOS, DAS, E)",
    "sufixos": "por gênero: sufixo (FILHO, JUNIOR, NETO, SOBRINHO...) -> nº; 'nenhum' = sem sufixo; JUNIOR vem "
               "sem acento como chave (a grafia JÚNIOR está em acentos.tsv.gz)",
    "de_jesus": "por gênero: fração das pessoas com DE JESUS em qualquer posição, logo depois do prenome e como "
                "último sobrenome (DE JESUS fica em sobrenomes.tsv.gz, não é tratado como devoção)",
    "e_entre_sobrenomes": "taxa por gênero de nomes com E entre sobrenomes (COSTA E SILVA) e os sobrenomes mais "
                          "comuns antes e depois do E (contagem >= 5)",
    "acento": "uso de acento no nome civil: fração dos nomes com algum acento e por tipo (til, cedilha, agudo, "
              "circunflexo, trema); entre os nomes com palavra acentuável (forma canônica acentuada), a fração "
              "com algum acento e a fração das palavras acentuáveis escritas com acento; 'estilo' = nos nomes "
              "com 2+ palavras acentuáveis, quantos acentuam todas, parte ou nenhuma",
}


def _estrutura(E: dict, canon: dict, n_pessoas: int, sob_n: collections.Counter) -> dict:
    def exibir(w: str) -> str:
        return " ".join(canon.get(p, (p,))[0] for p in w.split())

    def top(contador, limite=TOP_LISTA, minimo=MIN_PAR, chave=lambda k: k):
        itens = sorted(((v, k) for k, v in contador.items() if v >= minimo), key=lambda x: (-x[0], chave(x[1])))
        return [[exibir(chave(k)), v] for v, k in itens[:limite]]

    gens = ("F", "M")
    pessoas = E["pessoas"]
    devocoes = collections.defaultdict(lambda: {"F": 0, "M": 0, "N": 0, "primeiros": collections.Counter()})
    for g, cont in E["devocoes"].items():
        for (dev, prenome), v in cont.items():
            devocoes[dev][g] += v
            devocoes[dev]["primeiros"][prenome] += v
    lista_dev = []
    for dev, d in sorted(devocoes.items(), key=lambda x: (-(x[1]["F"] + x[1]["M"] + x[1]["N"]), x[0])):
        total = d["F"] + d["M"] + d["N"]
        if total < MIN_PAR:
            continue
        lista_dev.append({"complemento": exibir(dev), "total": total, "F": d["F"], "M": d["M"],
                          "taxa_F": _taxa(d["F"], pessoas["F"]), "taxa_M": _taxa(d["M"], pessoas["M"]),
                          "primeiros": {exibir(p): v for p, v in sorted(d["primeiros"].items(),
                                                                        key=lambda x: (-x[1], x[0])) if v >= MIN_PAR}})
    por_primeiro = {}
    for g in gens:
        porp = collections.defaultdict(collections.Counter)
        for (p, tipo), v in E["por_primeiro"][g].items():
            porp[p][tipo] += v
        mais = sorted(porp.items(), key=lambda x: (-sum(x[1].values()), x[0]))[:TOP_LISTA]
        por_primeiro[g] = {p: {"n": sum(c.values()),
                               "composto": _taxa(c["composto"] + c["composto_devocional"], sum(c.values())),
                               "devocional": _taxa(c["devocional"] + c["composto_devocional"], sum(c.values()))}
                           for p, c in mais}
    part_pos = {}
    for k in range(1, 5):
        part_pos[str(k)] = [{p: E["part_pos"][(k, j)][p] for p in COLUNAS_PARTICULA} for j in range(k)]

    def ordenado(contador):
        return {str(k): contador[k] for k in sorted(contador)}

    acento = {}
    for g in gens:
        a, n = E["acento"][g], pessoas[g]
        acento[g] = {"nomes_com_acento": _taxa(a["com_acento"], n),
                     "por_tipo": {t: _taxa(a[t], n) for t in ("til", "cedilha", "agudo", "circunflexo", "trema")},
                     "nomes_acentuaveis_com_acento": _taxa(a["nomes_acentuaveis_com_acento"], a["nomes_acentuaveis"]),
                     "palavras_acentuaveis_com_acento": _taxa(a["palavras_acentuaveis_com_acento"],
                                                              a["palavras_acentuaveis"])}
    acento["estilo"] = {k: E["estilo"][k] for k in ("todas", "parte", "nenhuma")}

    return {
        "_chaves": DESCRICAO_CHAVES,
        "fontes": {"prenomes": "IBGE, Censo 2010 (brasil.io genero-nomes)",
                   "estrutura": f"TSE, candidaturas {', '.join(map(str, ANOS_TSE))} (consulta_cand), nome civil",
                   "pessoas_tse": n_pessoas,
                   "sobrenomes_distintos_tse": len(sob_n),
                   "sobrenomes_gravados": sum(1 for v in sob_n.values() if v >= MIN_SOBRENOME),
                   "cobertura_sobrenomes": _taxa(sum(v for v in sob_n.values() if v >= MIN_SOBRENOME),
                                                 sum(sob_n.values()))},
        "parametros": {"min_sobrenome": MIN_SOBRENOME, "min_par": MIN_PAR, "min_acento_n": MIN_ACENTO_N,
                       "limiar_acento": LIMIAR_ACENTO, "dominancia_acento": DOMINANCIA, "r_prenome": R_PRENOME},
        "pessoas": dict(sorted(pessoas.items())),
        "palavras_por_nome": {g: ordenado(E["palavras"][g]) for g in gens},
        "tipo_prenome": {g: {t: E["tipo"][g][t] for t in ("simples", "composto", "devocional",
                                                          "composto_devocional")} for g in gens},
        "compostos": {g: top(E["compostos"][g], chave=lambda k: " ".join(k)) for g in gens},
        "segundo_prenome": {g: top(E["segundo"][g]) for g in gens},
        "por_primeiro_prenome": por_primeiro,
        "devocoes": lista_dev,
        "sobrenomes_por_nome": {g: ordenado(E["n_sobrenomes"][g]) for g in gens},
        "sobrenomes_por_tipo_prenome": {t: ordenado(E["n_sob_tipo"][t]) for t in ("simples", "composto",
                                                                                 "devocional",
                                                                                 "composto_devocional")},
        "particula_por_posicao": part_pos,
        "sufixos": {g: dict(sorted(E["sufixo"][g].items(), key=lambda x: (-x[1], x[0]))) for g in gens},
        "de_jesus": {g: {k: _taxa(E["de_jesus"][g][k], pessoas[g]) for k in ("qualquer", "logo_apos_prenome",
                                                                             "ultimo")} for g in gens},
        "e_entre_sobrenomes": {"taxa": {g: _taxa(E["e_entre"][g], pessoas[g]) for g in gens},
                               "antes_do_e": top(E["e_antes"], 50), "depois_do_e": top(E["e_depois"], 50)},
        "acento": acento,
    }


def _entra_no_mapa(w: str, n: int) -> bool:
    """Palavra do TSE com acento vai para acentos.tsv.gz? Vista 3+ vezes; palavras curtas, que num endereço são
    letra (RUA O), sigla (BA, CE, PE) ou palavra comum, pedem mais: 2 letras, 50+ vezes e não ser UF; 3 letras, 20+
    vezes; e nada de COMUNS_SEM_ACENTO."""
    if len(w) < 2 or w in COMUNS_SEM_ACENTO or n < MIN_ACENTO_N:
        return False
    if len(w) == 2:
        return n >= 50 and w not in SIGLAS_UF
    return len(w) > 3 or n >= 20


def _acentos_endereco() -> dict[str, str]:
    saida = {}
    for palavra in ACENTOS_ENDERECO.split():
        if palavra != _ascii(palavra):
            saida[_ascii(palavra)] = palavra
    return saida


def construir(cache: Path, saida: Path) -> dict:
    print("1/5 fontes")
    cache.mkdir(parents=True, exist_ok=True)
    saida.mkdir(parents=True, exist_ok=True)
    _baixar(URL_CENSO, cache / "nomes.csv.gz")
    for ano in ANOS_TSE:
        _baixar(URL_TSE.format(ano), cache / f"consulta_cand_{ano}.zip")
    censo = _censo(cache / "nomes.csv.gz")

    print("2/5 TSE, passo 1: grafias e posições", flush=True)
    formas, primeiro, ultimo = _passo1(cache)
    por_palavra = collections.defaultdict(collections.Counter)
    for forma, n in formas.items():
        por_palavra[_ascii(forma)][forma] += n
    canon = {w: (*_canonica(w, fs), sum(fs.values())) for w, fs in por_palavra.items()}
    acentuaveis = {w for w, (forma, _, _) in canon.items() if forma != w}

    print("3/5 TSE, passo 2: estrutura e sobrenomes", flush=True)
    E, sob_n, sob_part, sob_formas = _passo2(cache, censo, primeiro, ultimo, acentuaveis)
    n_pessoas = sum(E["pessoas"].values())

    print("4/5 gravando", flush=True)
    regras = _regras_sufixo(por_palavra)
    linhas_pre = []
    for nome, (f, m, total) in sorted(censo.items(), key=lambda x: (-x[1][2], x[0])):
        if total < MIN_PRENOME:
            continue
        forma, frac, n = canon.get(nome, (nome, 0.0, 0))
        origem = "tse" if forma != nome or n >= MIN_ACENTO_N else "-"
        if forma == nome:
            # O TSE não firmou acento. Se o final da palavra pede acento e o TSE a viu tão pouco que não ver acento
            # é normal (o acento sai só em parte das vezes: (1 - taxa)^n >= 5%), vale a regra do final.
            regra = _por_regra(nome, regras)
            if regra and (1 - regra[1]) ** n >= 0.05:
                forma, frac, origem = *regra, "regra"
        linhas_pre.append((nome, forma, _f(frac), n, origem, f, m))
    _gravar_tsv(saida / "prenomes.tsv.gz",
                ("nome", "acentuado", "frac_acento", "n_tse", "origem_acento", "freq_f", "freq_m"), linhas_pre)

    linhas_sob = []
    for w, n in sorted(sob_n.items(), key=lambda x: (-x[1], x[0])):
        if n < MIN_SOBRENOME:
            continue
        if _composto(w):
            forma, frac = _canonica(w, sob_formas[w])
        else:
            forma, frac, _ = canon.get(w, (w, 0.0, 0))
        part = sob_part[w]
        linhas_sob.append((forma, w, n, _f(frac), *(_f(part[p] / n) for p in COLUNAS_PARTICULA)))
    _gravar_tsv(saida / "sobrenomes.tsv.gz",
                ("sobrenome", "ascii", "n", "frac_acento") + tuple(f"p_{p.lower()}" for p in COLUNAS_PARTICULA),
                linhas_sob)

    manuais = _acentos_endereco()
    linhas_ac = []
    for w, (forma, frac, n) in sorted(canon.items(), key=lambda x: (-x[1][2], x[0])):
        if w in manuais or forma == w or not _entra_no_mapa(w, n):
            continue
        linhas_ac.append((w, forma, _f(frac), n, "tse"))
    # palavra de endereço: com a fração e a contagem do TSE quando ele concorda com a grafia; senão 1 e 0
    manuais_linhas = []
    for w, forma in manuais.items():
        tse = canon.get(w)
        frac, n = (tse[1], tse[2]) if tse and tse[0] == forma else (1.0, 0)
        manuais_linhas.append((w, forma, _f(frac), n, "manual"))
    linhas_ac += sorted(manuais_linhas, key=lambda x: (-x[3], x[0]))
    _gravar_tsv(saida / "acentos.tsv.gz", ("palavra", "acentuado", "frac", "n", "origem"), linhas_ac)

    estrutura = _estrutura(E, canon, n_pessoas, sob_n)
    _gravar(saida / "nomes_estrutura.json",
            (json.dumps(estrutura, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))

    resumo = {"pessoas_tse": n_pessoas, "prenomes": len(linhas_pre), "sobrenomes": len(linhas_sob),
              "acentos": len(linhas_ac), "regras_sufixo": sum(1 for v in regras.values() if v[0])}
    print("5/5 pronto:", ", ".join(f"{k}={v}" for k, v in resumo.items()))
    for nome in ("prenomes.tsv.gz", "sobrenomes.tsv.gz", "acentos.tsv.gz", "nomes_estrutura.json"):
        print(f"  {nome}: {(saida / nome).stat().st_size / 1e3:.0f} kB")
    return resumo


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, default=CACHE_PADRAO, help="onde baixar as fontes (~610 MB)")
    ap.add_argument("--saida", type=Path, default=SAIDA_PADRAO, help="onde gravar os arquivos (config/vocabulario)")
    ap.add_argument("--manter-zips", action="store_true", help="não apaga os zips do TSE no fim")
    args = ap.parse_args(argv)
    construir(args.cache, args.saida)
    if not args.manter_zips:
        for ano in ANOS_TSE:
            (args.cache / f"consulta_cand_{ano}.zip").unlink(missing_ok=True)
        print(f"zips do TSE apagados de {args.cache} (rodar de novo baixa outra vez)")


if __name__ == "__main__":
    main()
