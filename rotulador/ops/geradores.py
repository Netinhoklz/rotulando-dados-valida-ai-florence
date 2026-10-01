"""Valores falsos plausíveis por categoria.

Servem para o operador não digitar sempre os mesmos textos falsos: se "JOÃO DA
SILVA" aparece em todas as fraudes do treino e do teste, o modelo aprende o nome,
não a adulteração. Os sorteios usam o gerador do sistema (não reprodutível de
propósito).
"""

from __future__ import annotations

import datetime as dt
import random

from . import codigos

_rng = random.SystemRandom()

_NOMES = ("MARIA ANA FRANCISCA ANTONIA ADRIANA JULIANA MARCIA FERNANDA PATRICIA ALINE SANDRA CAMILA "
          "AMANDA BRUNA JESSICA LETICIA JULIA LUCIANA VANESSA MARIANA GABRIELA VERA RAFAELA DANIELA "
          "JOSE JOAO ANTONIO FRANCISCO CARLOS PAULO PEDRO LUCAS LUIZ MARCOS LUIS GABRIEL RAFAEL "
          "DANIEL MARCELO BRUNO EDUARDO FELIPE RAIMUNDO RODRIGO MANOEL MATEUS ANDRE FERNANDO FABIO "
          "LEONARDO GUSTAVO GUILHERME LEANDRO TIAGO ANDERSON RICARDO JORGE SEBASTIAO CLAUDIO").split()
_MEIOS = ("APARECIDA CRISTINA HELENA LUCIA BEATRIZ EDUARDA VITORIA HENRIQUE AUGUSTO CARLOS "
          "VINICIUS MIGUEL").split()
_SOBRENOMES = ("SILVA SANTOS OLIVEIRA SOUZA RODRIGUES FERREIRA ALVES PEREIRA LIMA GOMES COSTA "
               "RIBEIRO MARTINS CARVALHO ALMEIDA LOPES SOARES FERNANDES VIEIRA BARBOSA ROCHA DIAS "
               "NASCIMENTO ANDRADE MOREIRA NUNES MARQUES MACHADO MENDES FREITAS CARDOSO RAMOS "
               "GONCALVES SANTANA TEIXEIRA ARAUJO MOURA CAVALCANTI PINTO CAMPOS BATISTA").split()
_LOGRADOUROS = ("RUA", "RUA", "RUA", "AVENIDA", "AV", "TRAVESSA", "ALAMEDA", "ESTRADA", "PRACA")
_NOMES_RUA = ("DAS FLORES", "SAO JOSE", "SETE DE SETEMBRO", "QUINZE DE NOVEMBRO", "TIRADENTES",
              "BRASIL", "DOM PEDRO II", "DOS ANDRADAS", "SANTOS DUMONT", "DAS PALMEIRAS", "DOS IPES",
              "BARAO DO RIO BRANCO", "GETULIO VARGAS", "CASTELO BRANCO", "JOSE BONIFACIO", "DOS PINHEIROS",
              "PRESIDENTE KENNEDY", "AMAZONAS", "PARANA", "BAHIA", "MINAS GERAIS", "DAS ACACIAS", "BELA VISTA",
              "SANTA RITA", "SAO PAULO", "RIO GRANDE DO SUL", "MARECHAL DEODORO", "CORONEL FONSECA")
_BAIRROS = ("CENTRO", "JARDIM AMERICA", "VILA NOVA", "SANTA CRUZ", "BOA VISTA", "SAO JOSE", "PLANALTO",
            "JARDIM PAULISTA", "VILA MARIANA", "CIDADE NOVA", "SANTO ANTONIO", "INDUSTRIAL", "BELA VISTA",
            "PARQUE DAS NACOES", "JARDIM EUROPA", "NOVA ESPERANCA", "SAO FRANCISCO", "ALTO DA SERRA")
_CIDADES = (("SAO PAULO", "SP"), ("CAMPINAS", "SP"), ("SANTOS", "SP"), ("RIO DE JANEIRO", "RJ"),
            ("NITEROI", "RJ"), ("BELO HORIZONTE", "MG"), ("UBERLANDIA", "MG"), ("CONTAGEM", "MG"),
            ("CURITIBA", "PR"), ("LONDRINA", "PR"), ("PORTO ALEGRE", "RS"), ("CAXIAS DO SUL", "RS"),
            ("FLORIANOPOLIS", "SC"), ("JOINVILLE", "SC"), ("SALVADOR", "BA"), ("FEIRA DE SANTANA", "BA"),
            ("RECIFE", "PE"), ("FORTALEZA", "CE"), ("NATAL", "RN"), ("JOAO PESSOA", "PB"),
            ("MACEIO", "AL"), ("ARACAJU", "SE"), ("TERESINA", "PI"), ("SAO LUIS", "MA"), ("BELEM", "PA"),
            ("MANAUS", "AM"), ("GOIANIA", "GO"), ("BRASILIA", "DF"), ("CUIABA", "MT"), ("CAMPO GRANDE", "MS"),
            ("VITORIA", "ES"), ("PALMAS", "TO"), ("PORTO VELHO", "RO"), ("MACAPA", "AP"), ("BOA VISTA", "RR"))
_MESES = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")


def _digitos(n: int) -> str:
    return "".join(str(_rng.randrange(10)) for _ in range(n))


def cpf() -> str:
    base = [_rng.randrange(10) for _ in range(9)]
    if len(set(base)) == 1:
        base[0] = (base[0] + 1) % 10
    for n in (10, 11):
        s = sum(d * p for d, p in zip(base, range(n, 1, -1)))
        base.append((s * 10 % 11) % 10)
    d = "".join(map(str, base))
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def cnpj() -> str:
    base = [_rng.randrange(10) for _ in range(8)] + [0, 0, 0, 1]
    for pesos in ((5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)):
        r = sum(d * p for d, p in zip(base, pesos)) % 11
        base.append(0 if r < 2 else 11 - r)
    d = "".join(map(str, base))
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def valida_cpf(texto: str) -> bool:
    d = [int(c) for c in texto if c.isdigit()]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        s = sum(a * p for a, p in zip(d[:n], range(n + 1, 1, -1)))
        if (s * 10 % 11) % 10 != d[n]:
            return False
    return True


def valida_cnpj(texto: str) -> bool:
    d = [int(c) for c in texto if c.isdigit()]
    if len(d) != 14:
        return False
    for n, pesos in ((12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)), (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))):
        r = sum(a * p for a, p in zip(d[:n], pesos)) % 11
        if (0 if r < 2 else 11 - r) != d[n]:
            return False
    return True


def _data_recente() -> dt.date:
    return dt.date.today() - dt.timedelta(days=_rng.randrange(0, 540))


def _valor_centavos() -> int:
    return int(_rng.triangular(2500, 120000, 18000))


def _reais(centavos: int) -> str:
    inteiro, cent = divmod(centavos, 100)
    return f"R$ {inteiro:,}".replace(",", ".") + f",{cent:02d}"


def gerar(categoria: str) -> dict:
    """{"texto": valor sugerido, ...extras} para a categoria."""
    if categoria == "nome":
        partes = [_rng.choice(_NOMES)]
        if _rng.random() < 0.4:
            partes.append(_rng.choice(_MEIOS))
        partes += _rng.sample(_SOBRENOMES, _rng.choice((1, 2, 2, 3)))
        if _rng.random() < 0.3:
            partes.insert(-1, _rng.choice(("DA", "DOS", "DE")))
        return {"texto": " ".join(partes)}
    if categoria == "cpf/cnpj":
        return {"texto": cpf() if _rng.random() < 0.8 else cnpj()}
    if categoria == "endereço":
        comp = _rng.choice(("", "", "", f" APTO {_rng.randrange(1, 40)}{_rng.randrange(1, 9):02d}",
                            f" CASA {_rng.randrange(1, 5)}", f" BLOCO {_rng.choice('ABCD')}"))
        return {"texto": f"{_rng.choice(_LOGRADOUROS)} {_rng.choice(_NOMES_RUA)}, {_rng.randrange(1, 3000)}{comp}"}
    if categoria == "bairro":
        return {"texto": _rng.choice(_BAIRROS)}
    if categoria == "cep":
        d = f"{_rng.randrange(1000, 99999):05d}{_rng.randrange(0, 1000):03d}"
        return {"texto": f"{d[:5]}-{d[5:]}"}
    if categoria == "cidade/uf":
        c, uf = _rng.choice(_CIDADES)
        return {"texto": f"{c} - {uf}" if _rng.random() < 0.5 else f"{c}/{uf}"}
    if categoria == "data":
        return {"texto": _data_recente().strftime("%d/%m/%Y")}
    if categoria == "mês de referência":
        d = _data_recente()
        return {"texto": f"{_MESES[d.month - 1]}/{d.year}" if _rng.random() < 0.6 else d.strftime("%m/%Y")}
    if categoria == "valor":
        return {"texto": _reais(_valor_centavos())}
    if categoria == "consumo/leitura":
        if _rng.random() < 0.6:
            return {"texto": f"{_rng.randrange(40, 900)} kWh"}
        return {"texto": f"{_rng.randrange(3, 60)} m³"}
    if categoria in ("codigo barras", "linha digitável"):
        cent = _valor_centavos()
        if _rng.random() < 0.75:
            cod = codigos.arrecadacao(cent, segmento=_rng.choice("2343"), empresa=_digitos(4), livre=_digitos(25),
                                      ref=_rng.choice("6688"))
            linha = codigos.linha_arrecadacao(cod)
        else:
            venc = dt.date.today() + dt.timedelta(days=_rng.randrange(-60, 60))
            cod = codigos.boleto(_rng.choice(("001", "033", "104", "237", "341", "756", "748", "077")), cent, venc,
                                 _digitos(25))
            linha = codigos.linha_boleto(cod)
        return {"texto": linha if categoria == "linha digitável" else cod, "digitos": cod, "linha": linha,
                "valor": _reais(cent)}
    if categoria == "identificador documento":
        return {"texto": _digitos(_rng.choice((8, 9, 10, 12)))}
    if categoria == "qr code/pix":
        c, _ = _rng.choice(_CIDADES)
        payload = codigos.pix(chave=cnpj().translate({ord(ch): None for ch in "./-"}),
                              nome="EMPRESA " + _rng.choice(_SOBRENOMES), cidade=c,
                              valor=f"{_valor_centavos() / 100:.2f}", txid=_digitos(20))
        return {"texto": payload}
    return {"texto": ""}
