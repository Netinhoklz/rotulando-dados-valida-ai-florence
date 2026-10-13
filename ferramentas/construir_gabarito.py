"""Constrói as duas bases do gabarito que não vêm prontas do IBGE/TSE (config/vocabulario):

  tipos_logradouro.tsv  os tipos de logradouro do CNEFE 2022 (enderecos_estatisticas.json, os com 50+ domicílios no
                        país) + as abreviações dos Correios + as siglas de endereço do Distrito Federal, com a FORÇA:
                        "forte" = só aparece como rua (RUA, AVENIDA, TRAVESSA...); "ambiguo" = também é nome de bairro
                        ou de lugar (VILA, PARQUE, CENTRO, SÍTIO...): a linha só é endereço com número, S/N ou KM;
                        "df" = sigla de Brasília (SQN, SHIN, QI...): endereço quando vem com número.
  emissores.tsv         concessionárias de energia, água/esgoto, gás e telefonia/internet que emitem comprovante de
                        endereço, com apelidos (sigla antiga, marca). Lista COMPILADA À MÃO em 12/10/2026 — serve de
                        sinal para separar o emissor do cliente, não é cadastro oficial; acrescente o que faltar.

Uso: python ferramentas/construir_gabarito.py   (lê e grava em config/vocabulario)
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

VOCAB = Path(__file__).resolve().parents[1] / "config" / "vocabulario"

# tipos que só existem como via (nunca bairro): a linha que começa com eles é endereço
FORTES = {
    "RUA", "AVENIDA", "TRAVESSA", "ESTRADA", "RODOVIA", "ALAMEDA", "BECO", "VIELA", "PRAÇA", "PASSAGEM", "SERVIDÃO",
    "RAMAL", "VICINAL", "LADEIRA", "LARGO", "ESCADARIA", "BOULEVARD", "MARGINAL", "ACESSO", "CAMINHO", "TRAVESSÃO",
    "CORREDOR", "PASSARELA", "QUADRA", "VIADUTO", "ESTRADA MUNICIPAL", "ESTRADA VICINAL", "ESTRADA VELHA",
    "RUA PROJETADA", "RUA PARTICULAR", "TRILHA", "SUBIDA", "ESCADA", "PROLONGAMENTO", "TRANSVERSAL", "RUELA", "ESCADÃO",
    "VIA DE PEDESTRE", "TRAVESSIA", "ENTRONCAMENTO", "PASSEIO", "PROJETADA",
}
# abreviações dos Correios (tabela de tipos do DNE) e as que aparecem em conta; o ponto final é opcional na leitura.
# "SET" (setor) fica de fora: é o mês no histórico de consumo ("Set 2026")
ABREVIACOES = {
    "RUA": ["R"], "AVENIDA": ["AV", "AVEN", "AVN", "AVDA"], "TRAVESSA": ["TV", "TRAV", "TRV"],
    "ESTRADA": ["EST", "ESTR", "ETR"], "RODOVIA": ["ROD", "RDV"], "ALAMEDA": ["AL", "ALM"],
    "PRAÇA": ["PC", "PCA", "PÇA", "PRC", "PRACA"], "QUADRA": ["QD", "QDA"], "CONJUNTO": ["CJ", "CONJ", "CJT"],
    "CONDOMÍNIO": ["COND", "CND"], "LOTEAMENTO": ["LOT", "LOTEAM"], "RESIDENCIAL": ["RES", "RESID"],
    "SÍTIO": ["SIT"], "FAZENDA": ["FAZ", "FZ"], "CHÁCARA": ["CH", "CHAC"], "VILA": ["VL"], "BECO": ["BC"],
    "LARGO": ["LGO"], "LADEIRA": ["LD", "LAD"], "PARQUE": ["PQ", "PRQ"], "SETOR": [], "NÚCLEO": ["NUC"],
    "POVOADO": ["POV"], "PASSAGEM": ["PSG", "PSGM"], "SERVIDÃO": ["SERV", "SRV"], "RAMAL": ["RML"],
    "VICINAL": ["VIC"], "BOULEVARD": ["BLV", "BLVD"], "MARGINAL": ["MARG"], "ESCADARIA": ["ESC", "ESCD"],
    "VIADUTO": ["VD", "VIAD"], "TRAVESSÃO": ["TRAVESSAO"], "RODOVIA ESTADUAL": [], "AEROPORTO": ["AER"],
    "JARDIM": ["JD", "JARD"],
}
# siglas de endereço de Brasília (o CNEFE junta tudo no tipo "EDF"): vêm seguidas de número (SQN 308, QI 15)
DF = ("SQN SQS SQNW SQSW SHIN SHIS SHCN SHCS SHCES SCLN SCLS CLN CLS CRS CRN SHN SHS SGAN SGAS SEPN SEPS SMPW SMDB "
      "SMLN SHTN SHTQ SHVP SMAS QI QL QE QR QS QSA QSB QSC QSD QSE QSF QNA QNB QNC QND QNE QNF QNG QNH QNJ QNL QNM "
      "QNN QNO QNP QNQ QNR CSB CNB CNA CSA CSG QC QMS SCS SBS SDS SAUS SAUN SRTVS SIG SIA SOF SAAN SMHN SHLN SHLS ADE "
      "QOF QRSW CCSW SRES SHDB SHB SHVP").split()


def sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()


def tipos() -> list[tuple[str, str, str, int]]:
    est = json.loads((VOCAB / "enderecos_estatisticas.json").read_text(encoding="utf-8"))
    nac = est["tipos_logradouro"]["nacional"]
    linhas = []
    for tipo, n in sorted(nac.items(), key=lambda kv: -kv[1]):
        if tipo in ("EDF", "OUTROS"):
            continue
        forca = "forte" if tipo in FORTES else "ambiguo"
        variantes = sorted({sem_acento(tipo)} | {sem_acento(a) for a in ABREVIACOES.get(tipo, [])})
        linhas.append((tipo, forca, "|".join(variantes), int(n)))
    for tipo, abrevs in ABREVIACOES.items():  # tipos de conta que o CNEFE não lista com esse nome
        if tipo not in nac:
            linhas.append((tipo, "forte" if tipo in FORTES else "ambiguo",
                           "|".join(sorted({sem_acento(tipo)} | {sem_acento(a) for a in abrevs})), 0))
    linhas += [(sigla, "df", sigla, 0) for sigla in DF]
    return linhas


# (nome canônico, setor, UFs ou "BR", apelidos separados por |)
EMISSORES = [
    # energia elétrica
    ("CEMIG", "energia", "MG", "CEMIG|CEMIG DISTRIBUICAO"), ("COPEL", "energia", "PR", "COPEL|COPEL DISTRIBUICAO"),
    ("CELESC", "energia", "SC", "CELESC"), ("LIGHT", "energia", "RJ", "LIGHT|LIGHT SERVICOS DE ELETRICIDADE"),
    ("ENEL", "energia", "CE|RJ|SP", "ENEL|ENEL CE|ENEL RJ|ENEL SP|ENEL DISTRIBUICAO|AMPLA|ELETROPAULO|COELCE"),
    ("NEOENERGIA", "energia", "BA|PE|RN|SP|DF", "NEOENERGIA|COELBA|CELPE|COSERN|ELEKTRO|NEOENERGIA BRASILIA|CEB"),
    ("CPFL", "energia", "SP|RS", "CPFL|CPFL PAULISTA|CPFL PIRATININGA|CPFL SANTA CRUZ|RGE|RGE SUL"),
    ("EDP", "energia", "SP|ES", "EDP|EDP SAO PAULO|EDP ESPIRITO SANTO|BANDEIRANTE|ESCELSA"),
    ("ENERGISA", "energia", "MT|MS|TO|SE|PB|MG|RJ|SP|RO|AC", "ENERGISA"),
    ("EQUATORIAL", "energia", "MA|PA|PI|AL|GO|AP|RS",
     "EQUATORIAL|EQUATORIAL ENERGIA|CEMAR|CELPA|CEPISA|CEAL|CELG|CEA|CEEE|CEEE EQUATORIAL"),
    ("AMAZONAS ENERGIA", "energia", "AM", "AMAZONAS ENERGIA"), ("RORAIMA ENERGIA", "energia", "RR", "RORAIMA ENERGIA"),
    ("SULGIPE", "energia", "SE", "SULGIPE"), ("DMED", "energia", "MG", "DMED"), ("COCEL", "energia", "PR", "COCEL"),
    # água e esgoto
    ("SABESP", "saneamento", "SP", "SABESP"), ("COPASA", "saneamento", "MG", "COPASA"),
    ("CEDAE", "saneamento", "RJ", "CEDAE|AGUAS DO RIO|IGUA"), ("SANEPAR", "saneamento", "PR", "SANEPAR"),
    ("CAGECE", "saneamento", "CE", "CAGECE"), ("EMBASA", "saneamento", "BA", "EMBASA"),
    ("COMPESA", "saneamento", "PE", "COMPESA"), ("CORSAN", "saneamento", "RS", "CORSAN"),
    ("CASAN", "saneamento", "SC", "CASAN"), ("CAESB", "saneamento", "DF", "CAESB"),
    ("SANEAGO", "saneamento", "GO", "SANEAGO"), ("CAERN", "saneamento", "RN", "CAERN"),
    ("CAGEPA", "saneamento", "PB", "CAGEPA"), ("DESO", "saneamento", "SE", "DESO"), ("CASAL", "saneamento", "AL", "CASAL"),
    ("COSANPA", "saneamento", "PA", "COSANPA"), ("CAEMA", "saneamento", "MA", "CAEMA"), ("CAERD", "saneamento", "RO", "CAERD"),
    ("SANESUL", "saneamento", "MS", "SANESUL"), ("CESAN", "saneamento", "ES", "CESAN"), ("CAER", "saneamento", "RR", "CAER"),
    ("CAESA", "saneamento", "AP", "CAESA"), ("DEPASA", "saneamento", "AC", "DEPASA"),
    ("AGESPISA", "saneamento", "PI", "AGESPISA|AGUAS DE TERESINA"), ("AGUAS DE MANAUS", "saneamento", "AM", "AGUAS DE MANAUS"),
    ("AGUAS GUARIROBA", "saneamento", "MS", "AGUAS GUARIROBA"), ("BRK", "saneamento", "BR", "BRK|BRK AMBIENTAL"),
    ("AEGEA", "saneamento", "BR", "AEGEA"), ("SAAE", "saneamento", "BR", "SAAE|SEMAE|DMAE|DAE|SANASA"),
    # gás canalizado
    ("COMGAS", "gas", "SP", "COMGAS"), ("NATURGY", "gas", "RJ|SP", "NATURGY|CEG|CEG RIO"), ("GASMIG", "gas", "MG", "GASMIG"),
    ("COMPAGAS", "gas", "PR", "COMPAGAS"), ("SCGAS", "gas", "SC", "SCGAS"), ("SULGAS", "gas", "RS", "SULGAS"),
    ("BAHIAGAS", "gas", "BA", "BAHIAGAS"), ("COPERGAS", "gas", "PE", "COPERGAS"), ("CEGAS", "gas", "CE", "CEGAS"),
    ("PBGAS", "gas", "PB", "PBGAS"), ("POTIGAS", "gas", "RN", "POTIGAS"), ("ALGAS", "gas", "AL", "ALGAS"),
    ("SERGAS", "gas", "SE", "SERGAS"), ("MSGAS", "gas", "MS", "MSGAS"), ("CIGAS", "gas", "AM", "CIGAS"),
    # telefonia, internet e TV
    ("VIVO", "telecom", "BR", "VIVO|TELEFONICA"), ("CLARO", "telecom", "BR", "CLARO|NET|EMBRATEL"),
    ("TIM", "telecom", "BR", "TIM"), ("OI", "telecom", "BR", "OI|OI FIBRA"), ("NIO", "telecom", "BR", "NIO|NIO FIBRA"),
    ("ALGAR", "telecom", "BR", "ALGAR|ALGAR TELECOM"), ("SKY", "telecom", "BR", "SKY"),
    ("BRISANET", "telecom", "BR", "BRISANET"), ("DESKTOP", "telecom", "SP", "DESKTOP"),
    ("UNIFIQUE", "telecom", "SC", "UNIFIQUE"), ("VERO", "telecom", "BR", "VERO"), ("LIGGA", "telecom", "PR", "LIGGA"),
    ("SERCOMTEL", "telecom", "PR", "SERCOMTEL"), ("GIGA MAIS", "telecom", "BR", "GIGA MAIS|GIGA+"),
]


def main() -> None:
    lt = tipos()
    with open(VOCAB / "tipos_logradouro.tsv", "w", encoding="utf-8", newline="\n") as f:
        f.write("tipo\tforca\tvariantes\tn_domicilios\n")
        for t in lt:
            f.write("\t".join(map(str, t)) + "\n")
    with open(VOCAB / "emissores.tsv", "w", encoding="utf-8", newline="\n") as f:
        f.write("nome\tsetor\tufs\tapelidos\n")
        for e in EMISSORES:
            f.write("\t".join(e) + "\n")
    print(f"tipos_logradouro.tsv: {len(lt)} linhas ({sum(1 for t in lt if t[1] == 'forte')} fortes, "
          f"{sum(1 for t in lt if t[1] == 'df')} siglas do DF); emissores.tsv: {len(EMISSORES)} emissores")


if __name__ == "__main__":
    main()
