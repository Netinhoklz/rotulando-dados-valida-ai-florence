"""Valores falsos plausíveis por categoria.

Servem para o operador não digitar sempre os mesmos textos falsos: se "JOÃO DA
SILVA" aparece em todas as fraudes do treino e do teste, o modelo aprende o nome,
não a adulteração. Os sorteios usam o gerador do sistema (não reprodutível de
propósito).

Pistas que um fraudador cuidadoso não deixa (e que um modelo que lê o texto usaria como atalho) ficam
fora dos valores: a data de referência é a do documento (``usando(hoje=...)``), não a de hoje; o CEP
é um CEP que a cidade usa (``usando(cidade=...)``, ``cep_de``); o código de barras novo pode
repetir empresa, segmento e banco do original (``partes_da_linha`` → ``par_codigo``); e o vocabulário
é o do país inteiro (as bases oficiais de ``config/vocabulario``, ver os LEIA-ME; sem elas, as listas embutidas).

Os valores imitam a população, não uma lista: prenomes e sobrenomes pesam pela frequência real (MARIA e SILVA
muito mais que IARA e TAKAHASHI: pessoas no Censo, ocorrências no TSE), o gênero é coerente (nada de "ALAN
BEATRIZ"), nomes compostos e devocionais, partículas (DA SILVA, DOS SANTOS) e FILHO/JÚNIOR saem na taxa de
verdade; razão social quando o original é empresa; a rua é uma rua de verdade da cidade, com o bairro e o CEP dela,
no molde do original (tipo e título abreviados como estavam, separador, "Nº", número e complemento na distribuição
do porte da cidade); bairro que existe na cidade. O vocabulário guarda a grafia com acento, que sai com
``usando(acentos=True)`` (nos nomes de pessoa, com a frequência do registro civil: JOSÉ em ~48% das vezes).

Datas e números imitam a conta de verdade e saem no formato EXATO do original (10/10/26, 2026-10-10, "10 de outubro
de 2026", OUT/2026, "R$ ***1.234,56", "(85) 99876-5432", "***.456.789-**"): o vencimento cai no dia fixo do cliente e
em dia útil (fim de semana e feriado nacional empurram), emissão, leitura, próxima leitura e referência saem do mesmo
ciclo (``ciclo_de_datas``); o valor fica na ordem de grandeza do antigo e na tarifa do consumo; o telefone tem o DDD
da cidade; o identificador repete o dígito verificador do antigo. Com ``usando(documento=...)`` o que a versão troca
fica coerente entre si (o mesmo ciclo de datas, o mesmo fator de valor, o código de barras com o total novo).
"""

from __future__ import annotations

import bisect
import calendar
import contextlib
import contextvars
import csv
import datetime as dt
import functools
import gzip
import hashlib
import itertools
import json
import math
import random
import re
import sys
import threading
import unicodedata
from array import array
from collections import OrderedDict
from pathlib import Path

from . import codigos

_rng_atual = contextvars.ContextVar("rng_geradores", default=random.SystemRandom())
_split_atual = contextvars.ContextVar("split_geradores", default=None)
_hoje_atual = contextvars.ContextVar("hoje_geradores", default=None)
_cidade_atual = contextvars.ContextVar("cidade_geradores", default=None)
_acentos_atual = contextvars.ContextVar("acentos_geradores", default=False)
_documento_atual = contextvars.ContextVar("documento_geradores", default=None)


class _Sorteio:
    """Repassa ao gerador da vez: o do sistema, ou o de ``usando(rng)`` nesta thread."""

    def __getattr__(self, nome):
        return getattr(_rng_atual.get(), nome)


_rng = _Sorteio()


@contextlib.contextmanager
def usando(rng: random.Random | None = None, split: str | None = None, hoje: dt.date | None = None,
           cidade: tuple[str, str] | None = None, *, acentos: bool | None = None, documento: list | None = None):
    """Sorteios desta thread saem de ``rng`` (as técnicas automáticas passam um gerador com a
    semente da edição: a prévia e a aplicação dão o mesmo valor). A semente em si é sorteada
    pelo sistema, então os valores continuam não reprodutíveis entre execuções.
    ``split``: nomes, ruas, bairros e cidades saem do núcleo comum mais a fatia da cauda deste split.
    ``hoje``: data de referência das datas falsas (a do documento, para o ano não virar rótulo).
    ``cidade``: (nome, UF) do documento; a rua, o bairro e o CEP falsos são dela (sem a base de lugares: o CEP na
    faixa dela e o bairro dela ou genérico).
    ``acentos``: True = nomes, ruas, bairros e cidades com acento (JOSÉ, CONCEIÇÃO, SÃO PAULO, PRAÇA);
    False = sem acento (o padrão).
    ``documento``: os campos do documento (dicts com categoria, texto e rótulo) da versão: a data trocada acha o papel
    dela pelo rótulo do campo e as datas, valores, consumo e números trocados NA MESMA versão ficam coerentes entre si
    (um ciclo de datas, um fator de valor, o mesmo número novo onde o velho se repete, o código com o total novo; a
    rua, o bairro e o CEP trocados saem da mesma linha da base de logradouros, guardada em ``["lugar"]``). A
    mesma lista de novo (as técnicas repassam a do documento) mantém o que a versão já sorteou.
    Cada argumento None MANTÉM o valor em vigor: as técnicas chamam ``usando(rng, split)`` dentro de um
    ``usando(hoje=...)`` externo e o ``hoje`` sobrevive."""
    if documento is not None:
        atual = _documento_atual.get()
        documento = atual if atual is not None and atual["campos"] is documento else {
            "campos": documento, "ciclo": None, "fator": None, "valores": {}, "trocas": {}}
    pares = ((_rng_atual, rng), (_split_atual, split), (_hoje_atual, hoje), (_cidade_atual, cidade),
             (_acentos_atual, acentos), (_documento_atual, documento))
    tokens = [(var, var.set(valor)) for var, valor in pares if valor is not None]
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


def hoje() -> dt.date:
    """Data de referência dos sorteios: a de ``usando(hoje=...)`` ou a de hoje."""
    return _hoje_atual.get() or dt.date.today()


def cidade_atual() -> tuple[str, str] | None:
    """(nome, UF) de ``usando(cidade=...)``, ou None."""
    return _cidade_atual.get()


def _sem_acento(texto: str) -> str:
    """Sem acento letra a letra, com a caixa e o comprimento de antes: "São José" → "Sao Jose"."""
    return "".join(unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode() or c for c in texto)


def _grafia(texto: str) -> str:
    """Valor do vocabulário (guardado com acento) na grafia que ``usando(acentos=...)`` pede."""
    return texto if _acentos_atual.get() else _sem_acento(texto)


# Vocabulário com a grafia de verdade (com acento) e, em nomes e sobrenomes, na ordem da frequência no país (IBGE,
# Censo 2010): o sorteio pesa pela posição, então MARIA sai muito mais que IARA e SILVA muito mais que TAKAHASHI.
_PRENOMES_F = (
    "MARIA ANA FRANCISCA ANTÔNIA ADRIANA JULIANA MÁRCIA FERNANDA PATRÍCIA ALINE SANDRA CAMILA AMANDA BRUNA "
    "JÉSSICA LETÍCIA JÚLIA LUCIANA VANESSA MARIANA GABRIELA VERA VITÓRIA LARISSA CLÁUDIA BEATRIZ LUANA RITA SÔNIA "
    "RENATA ELIANE JOSEFA SIMONE NATÁLIA CRISTIANE CARLA DÉBORA ROSÂNGELA JAQUELINE ROSA DANIELA APARECIDA "
    "MARLENE TEREZINHA RAIMUNDA ANDREIA FABIANA LÚCIA RAQUEL ÂNGELA RAFAELA JOANA LUZIA TATIANE ROBERTA KARINA "
    "PRISCILA GISELE ELAINE ISABELA SOFIA LAURA ALICE MANUELA VALENTINA CLARA HELENA CECÍLIA LORENA TEREZA SÍLVIA "
    "REGINA MÔNICA SUELI PAULA "
    "ANDREA CLEIDE CLEUSA CONCEIÇÃO DENISE DIRCE EDNA ELISÂNGELA ELZA EUNICE FÁTIMA FLÁVIA GEOVANA GLÓRIA GRAZIELE "
    "IARA INÊS IRENE IVONE IZABEL JANAÍNA JOSIANE JOYCE KELLY LAÍS LEILA LÍDIA LILIAN LÍVIA LOURDES MAGDA "
    "MARGARIDA MARILENE MARINA MARTA MICHELE MILENA MIRIAM NÁDIA NAIR NEIDE NEUSA NILZA NOÊMIA OLGA PÂMELA ROSANA "
    "ROSELI ROSEMARY RUTE SABRINA SELMA SHEILA SOLANGE SUZANA TAÍS TÂNIA THAÍS VALÉRIA VILMA VIVIANE YASMIN ZILDA "
    "ZÉLIA ADÉLIA ALANA ALESSANDRA ANDRESSA ANGÉLICA BÁRBARA BIANCA BRENDA CAROLINA CAROLINE CÍNTIA DAIANE DAYANE "
    "DIANA ELISA ELIZABETE EMILY ÉRICA EVELYN FRANCIELE GLÁUCIA HELOÍSA INGRID ISABELLA ISADORA IVANA KAREN KÁTIA "
    "LAVÍNIA LEONOR LÍGIA LILIANE LUIZA MAITÊ MARCELA MARIANE MARISA MELISSA MICAELA MIRELA NICOLE PALOMA PIETRA "
    "REBECA ROSILENE ROSIMEIRE SAMARA SARA SARAH SILVANA STEFANI TAMIRES TATIANA VÂNIA WANDA MARÍLIA ANTONIETA "
    "BENEDITA CLARICE DALVA EDILEUZA ELIETE ELIZETE GENI IRACEMA IVANILDE JANDIRA JUREMA LINDALVA MARLI MARLUCE "
    "NILDA ODETE SEBASTIANA SEVERINA VALDIRENE ZENAIDE ZULMIRA").split()
_PRENOMES_M = (
    "JOSÉ JOÃO ANTÔNIO FRANCISCO CARLOS PAULO PEDRO LUCAS LUIZ MARCOS LUÍS GABRIEL RAFAEL DANIEL MARCELO BRUNO "
    "EDUARDO FELIPE RAIMUNDO RODRIGO MANOEL MATEUS ANDRÉ FERNANDO FÁBIO LEONARDO GUSTAVO GUILHERME LEANDRO TIAGO "
    "ANDERSON RICARDO MÁRCIO JORGE SEBASTIÃO ALEXANDRE ROBERTO EDSON DIEGO VITOR SÉRGIO CLÁUDIO MATHEUS THIAGO "
    "GERALDO ADRIANO LUCIANO JÚLIO RENATO ALEX VINÍCIUS ROGÉRIO SAMUEL RONALDO MÁRIO FLÁVIO IGOR DOUGLAS DAVI "
    "MANUEL JEFERSON CÍCERO VICTOR MIGUEL ROBSON MAURÍCIO DANILO HENRIQUE CAIO REGINALDO JOAQUIM CRISTIANO ARTHUR "
    "HEITOR BERNARDO ENZO NICOLAS BENEDITO OTÁVIO WAGNER EVERTON NELSON VALDIR OSVALDO "
    "ABEL ADÃO ADEMIR ADILSON AGNALDO AILTON ALAN ALBERTO ALCIDES ALFREDO ÁLVARO AMARO AMILTON ANÍSIO ANTENOR ARI "
    "ARLINDO ARMANDO ARNALDO BENTO BRENO CAETANO CELSO CÉSAR CLEBER CLEITON DAMIÃO DÁRIO DAVID DÉCIO DEIVID DENIS "
    "DIOGO DIRCEU DJALMA DOMINGOS EDGAR EDILSON EDMILSON EDMUNDO EDVALDO ELIAS ELISEU ELTON EMANUEL EMERSON ERIC "
    "ERNESTO EUCLIDES EUGÊNIO EVANDRO EXPEDITO EZEQUIEL FABRÍCIO FREDERICO GENIVAL GERSON GILBERTO GILMAR GILSON "
    "GIOVANI HAMILTON HÉLIO HERBERT HERMES HUGO HUMBERTO ISAAC ISRAEL ÍTALO IVAN IVO JACKSON JAIME JAIR JAIRO JEAN "
    "JEFFERSON JONAS JÔNATAS JONATHAN JOSUÉ KAUÃ KAUÊ KEVIN LAÉRCIO LAURO LÁZARO LEVI LINDOMAR LOURIVAL LÚCIO "
    "MARLON MARTINHO MAURO MICHEL MILTON MOACIR MOISÉS MURILO NATAN NATANAEL NEWTON NICOLAU NILSON NILTON NIVALDO "
    "NOEL ODAIR ORLANDO OSCAR OSMAR OSWALDO OTACÍLIO PABLO PATRICK PAULINO PIETRO RAUL RAVI REINALDO RENAN ROMÁRIO "
    "RÔMULO RONALD ROQUE RUBENS RUI SALVADOR SANDRO SAULO SEVERINO SIDNEI SILAS SÍLVIO SIMÃO THEO TOMÁS UBIRATAN "
    "ULISSES VALDEMAR VALDECIR VALMIR VANDERLEI VICENTE VLADIMIR WALTER WANDERSON WASHINGTON WELLINGTON WESLEY "
    "WILLIAM WILSON YAGO YURI ZACARIAS").split()
_NOMES = _PRENOMES_F + _PRENOMES_M
# segundo nome (ANA PAULA, JOSÉ CARLOS), do mesmo gênero do primeiro
_MEIOS_F = ("APARECIDA CRISTINA HELENA LÚCIA BEATRIZ EDUARDA VITÓRIA FERNANDA LUÍSA CLARA CAROLINA PAULA JÚLIA "
            "GABRIELA ALICE ISABEL TEREZA CECÍLIA LUIZA REGINA ROSA SOFIA RITA ELISA AMÉLIA LETÍCIA VALENTINA HELOÍSA "
            "MARIA").split()
_MEIOS_M = ("CARLOS HENRIQUE AUGUSTO EDUARDO ANTÔNIO ROBERTO PAULO VINÍCIUS MIGUEL FERNANDO RICARDO GABRIEL VITOR "
            "LUIZ CÉSAR ALBERTO FELIPE LUCAS PEDRO JOSÉ FRANCISCO GUILHERME OTÁVIO RAFAEL HUGO ALEXANDRE MATEUS "
            "DANIEL RENATO").split()
_MEIOS = _MEIOS_F + _MEIOS_M
# nomes compostos de devoção (MARIA DE FÁTIMA, FRANCISCO DAS CHAGAS, JOÃO BATISTA), pelo primeiro nome sem acento
_DEVOCOES = {"MARIA": ("DE FÁTIMA", "DAS GRAÇAS", "DE LOURDES", "DO CARMO", "DA CONCEIÇÃO", "DO SOCORRO", "DAS DORES",
                       "DA PENHA", "JOSÉ", "AUXILIADORA", "DAS NEVES", "DO ROSÁRIO"),
             "FRANCISCO": ("DAS CHAGAS", "DE ASSIS"), "JOAO": ("BATISTA", "DE DEUS"), "JOSE": ("MARIA",)}
# chance de nome composto: alta nos que quase sempre vêm compostos (MARIA, ANA, JOSÉ, JOÃO), baixa nos outros
_P_MEIO = {"MARIA": 0.7, "ANA": 0.5, "JOSE": 0.45, "JOAO": 0.45, "LUIZ": 0.35, "PEDRO": 0.35, "FRANCISCO": 0.3,
           "ANTONIO": 0.3, "PAULO": 0.3, "CARLOS": 0.25}
_SUFIXOS_NOME = (("FILHO", 45), ("JÚNIOR", 35), ("NETO", 15), ("SOBRINHO", 5))
_SOBRENOMES = (
    "SILVA SANTOS OLIVEIRA SOUZA RODRIGUES FERREIRA ALVES PEREIRA LIMA GOMES COSTA RIBEIRO MARTINS CARVALHO "
    "ALMEIDA LOPES SOARES FERNANDES VIEIRA BARBOSA ROCHA DIAS NASCIMENTO ANDRADE MOREIRA NUNES MARQUES MACHADO "
    "MENDES FREITAS CARDOSO RAMOS GONÇALVES SANTANA TEIXEIRA ARAÚJO JESUS SOUSA MOURA CAVALCANTI CONCEIÇÃO PINTO "
    "CAMPOS BATISTA MONTEIRO CORREIA BEZERRA MELO NOGUEIRA BARROS FARIAS CUNHA REIS CASTRO PIRES SAMPAIO "
    "GUIMARÃES MACEDO AZEVEDO BRITO MOTA SIQUEIRA TAVARES XAVIER QUEIROZ PRADO VASCONCELOS FONSECA PAIVA CHAVES "
    "MATOS ARRUDA LACERDA DUARTE MORAES "
    "ABREU AGUIAR ALENCAR AMARAL AMORIM ANTUNES ARANTES ASSIS ASSUNÇÃO ÁVILA BANDEIRA BAPTISTA BARRETO BARROSO "
    "BASTOS BELO BERNARDES BITTENCOURT BORGES BOTELHO BRAGA BRANCO BRANDÃO BUENO CABRAL CALDAS CALDEIRA CÂMARA "
    "CAMARGO CÂNDIDO CARNEIRO CASTILHO CAVALCANTE CERQUEIRA CHAGAS COELHO CORDEIRO CORRÊA COUTINHO COUTO CRUZ "
    "DAMASCENO DANTAS DOMINGUES DORNELES DUTRA ESPÍNDOLA ESTEVES FALCÃO FARIA FÉLIX FERRAZ FIGUEIREDO FIGUEIRA "
    "FLORES FOGAÇA FONTES FORTES FRAGA FRANÇA FRANCO FREIRE FROTA FURTADO GALVÃO GARCIA GODOI GODOY GOUVEIA GUEDES "
    "GUERRA GUERREIRO HOLANDA JUNQUEIRA LEAL LEÃO LEITÃO LEITE LEMOS LESSA LINHARES LINS LIRA LOBATO LOBO LOURENÇO "
    "LUCENA LUZ MACIEL MAGALHÃES MAIA MALTA MARINHO MASCARENHAS MATTOS MEDEIROS MEIRELES MELLO MENDONÇA MENEZES "
    "MESQUITA MIRANDA MONTENEGRO MORAIS MOTTA MUNIZ NEGREIROS NERY NEVES NÓBREGA NOVAES PACHECO PADILHA PAES "
    "PAIXÃO PARENTE PASSOS PAULA PAZ PEDROSA PEDROSO PEIXOTO PENA PENHA PESSOA PIMENTA PIMENTEL PINHEIRO PINHO "
    "PONTES PORTELA PORTO QUADROS QUEIROGA RAMALHO RANGEL REGO RESENDE REZENDE RIBAS RIOS SACRAMENTO SALDANHA "
    "SALES SALGADO SANCHES SANTIAGO SARAIVA SEABRA SENA SERRA SILVEIRA SIMÕES SOUTO TELES TELLES TOLEDO TORRES "
    "TRINDADE VALADARES VALE VALENTE VARELA VARGAS VASQUES VAZ VELOSO VENTURA VERAS VIANA VIDAL VIEGAS VILELA "
    "WANDERLEY BARCELOS BRASIL CAMPELO CARMO CAVALHEIRO CORTES DELGADO DOURADO FIALHO FIUZA GAMA GARCEZ GÓIS GUSMÃO "
    "LANDIM MAFRA MARCONDES MATIAS MOURÃO NOVAIS PRATES ROLIM TEODORO ANJOS "
    "BIANCHI FERRARI MORETTI ROSSI SCHMIDT SCHNEIDER MÜLLER WEBER BECKER HOFFMANN SCHULZ KLEIN BRAUN FISCHER BAUER "
    "KOCH NAKAMURA TANAKA SATO YAMAMOTO SUZUKI HASHIMOTO YOSHIDA WATANABE TAKAHASHI").split()
# Tipos de logradouro: nome, peso (a proporção no país: RUA domina, AVENIDA bem menos, o resto é raro) e abreviação.
_TIPOS = (("RUA", 72, "R"), ("AVENIDA", 13, "AV"), ("TRAVESSA", 5, "TV"), ("ESTRADA", 3, "EST"), ("ALAMEDA", 2, "AL"),
          ("PRAÇA", 2, "PÇA"), ("RODOVIA", 1, "ROD"), ("LADEIRA", 1, "LD"), ("LARGO", 1, "LGO"))
# o tipo como aparece escrito (sem acento) → o nome canônico
_TIPO_DE = {"R": "RUA", "RUA": "RUA", "AV": "AVENIDA", "AVEN": "AVENIDA", "AVENIDA": "AVENIDA", "TV": "TRAVESSA",
            "TRAV": "TRAVESSA", "TRAVESSA": "TRAVESSA", "EST": "ESTRADA", "ESTR": "ESTRADA", "ESTRADA": "ESTRADA",
            "AL": "ALAMEDA", "ALAMEDA": "ALAMEDA", "PC": "PRAÇA", "PCA": "PRAÇA", "PRACA": "PRAÇA", "ROD": "RODOVIA",
            "RODOVIA": "RODOVIA", "LD": "LADEIRA", "LAD": "LADEIRA", "LADEIRA": "LADEIRA", "LGO": "LARGO",
            "LARGO": "LARGO", "BECO": "BECO", "VIELA": "VIELA"}
_NOMES_RUA = ("DAS FLORES", "SÃO JOSÉ", "SETE DE SETEMBRO", "QUINZE DE NOVEMBRO", "TIRADENTES",
              "BRASIL", "DOM PEDRO II", "DOS ANDRADAS", "SANTOS DUMONT", "DAS PALMEIRAS", "DOS IPÊS",
              "BARÃO DO RIO BRANCO", "GETÚLIO VARGAS", "CASTELO BRANCO", "JOSÉ BONIFÁCIO", "DOS PINHEIROS",
              "PRESIDENTE KENNEDY", "AMAZONAS", "PARANÁ", "BAHIA", "MINAS GERAIS", "DAS ACÁCIAS", "BELA VISTA",
              "SANTA RITA", "SÃO PAULO", "RIO GRANDE DO SUL", "MARECHAL DEODORO", "CORONEL FONSECA",
              "RUI BARBOSA", "OSWALDO CRUZ", "MACHADO DE ASSIS", "CASTRO ALVES", "PRINCESA ISABEL",
              "VISCONDE DE MAUÁ", "DUQUE DE CAXIAS", "MARECHAL RONDON", "DAS OLIVEIRAS", "DOS EUCALIPTOS",
              "DAS GAIVOTAS", "PARAÍBA", "GOIÁS", "PERNAMBUCO", "CEARÁ", "ALAGOAS", "PIAUÍ",
              "ESPÍRITO SANTO", "SÃO BENEDITO", "NOSSA SENHORA APARECIDA", "SÃO JOÃO", "DOS BANDEIRANTES",
              # santos
              "SÃO PEDRO", "SÃO FRANCISCO", "SÃO MIGUEL", "SÃO JORGE", "SÃO LUIZ", "SÃO VICENTE", "SÃO MATEUS",
              "SÃO LUCAS", "SÃO MARCOS", "SÃO GERALDO", "SÃO DOMINGOS", "SÃO CRISTÓVÃO", "SÃO JUDAS TADEU",
              "SÃO SEBASTIÃO", "SÃO LOURENÇO", "SÃO CAETANO", "SÃO GABRIEL", "SÃO RAFAEL", "SÃO ROQUE", "SÃO TOMÁS",
              "SANTA CATARINA", "SANTA MARIA", "SANTA LUZIA", "SANTA CLARA", "SANTA TEREZA", "SANTA HELENA",
              "SANTA ISABEL", "SANTA CECÍLIA", "SANTA EFIGÊNIA", "SANTA MÔNICA", "SANTA ROSA", "SANTA BÁRBARA",
              "SANTA INÊS", "SANTO ANTÔNIO", "SANTO AMARO", "SANTO AGOSTINHO", "NOSSA SENHORA DE FÁTIMA",
              "NOSSA SENHORA DAS GRAÇAS", "NOSSA SENHORA DO CARMO", "NOSSA SENHORA DA CONCEIÇÃO",
              "NOSSA SENHORA DA PENHA", "NOSSA SENHORA DE LOURDES", "BOM JESUS", "DIVINO ESPÍRITO SANTO",
              "PADRE ANCHIETA", "PADRE CÍCERO", "FREI CANECA",
              # datas
              "PRIMEIRO DE MAIO", "VINTE E UM DE ABRIL", "TREZE DE MAIO", "NOVE DE JULHO", "DOZE DE OUTUBRO",
              "DOIS DE JULHO", "TRÊS DE MAIO", "VINTE E CINCO DE DEZEMBRO", "PRIMEIRO DE JANEIRO",
              "VINTE E NOVE DE JUNHO", "DEZ DE MAIO", "SEIS DE MARÇO", "ONZE DE JUNHO", "DEZESSETE DE AGOSTO",
              "TRINTA E UM DE MARÇO", "OITO DE DEZEMBRO", "VINTE DE SETEMBRO", "QUATORZE DE JULHO",
              "VINTE E TRÊS DE MAIO", "VINTE E QUATRO DE OUTUBRO",
              # presidentes, militares, nobres e escritores
              "PRESIDENTE VARGAS", "PRESIDENTE DUTRA", "PRESIDENTE JUSCELINO KUBITSCHEK", "PRESIDENTE COSTA E SILVA",
              "PRESIDENTE MÉDICI", "PRESIDENTE WASHINGTON LUÍS", "PRESIDENTE WENCESLAU BRAZ",
              "PRESIDENTE EPITÁCIO PESSOA", "PRESIDENTE ARTHUR BERNARDES", "PRESIDENTE ROOSEVELT",
              "PRESIDENTE TANCREDO NEVES", "JUSCELINO KUBITSCHEK", "FLORIANO PEIXOTO", "PRUDENTE DE MORAES",
              "CAMPOS SALES", "RODRIGUES ALVES", "AFONSO PENA", "NILO PEÇANHA", "HERMES DA FONSECA",
              "DELFIM MOREIRA", "DEODORO DA FONSECA", "MARECHAL FLORIANO", "MARECHAL HERMES", "GENERAL OSÓRIO",
              "GENERAL CARNEIRO", "GENERAL CÂMARA", "BRIGADEIRO FARIA LIMA", "BRIGADEIRO LUÍS ANTÔNIO",
              "ALMIRANTE BARROSO", "ALMIRANTE TAMANDARÉ", "VISCONDE DO RIO BRANCO", "SENADOR VERGUEIRO",
              "SENADOR PINHEIRO MACHADO", "CONSELHEIRO NÉBIAS", "JOÃO PESSOA", "ANITA GARIBALDI", "CARLOS GOMES",
              "GONÇALVES DIAS", "JOSÉ DE ALENCAR", "OLAVO BILAC", "MONTEIRO LOBATO", "CECÍLIA MEIRELES",
              "MANUEL BANDEIRA", "CARLOS DRUMMOND DE ANDRADE", "GRACILIANO RAMOS", "JORGE AMADO",
              "EUCLIDES DA CUNHA", "VINÍCIUS DE MORAES", "ALUÍSIO AZEVEDO", "VISCONDE DE TAUNAY", "BARÃO DE MAUÁ",
              "BARÃO DE COTEGIPE", "BARÃO DE ITAPETININGA", "BARÃO DE JAGUARA", "BARÃO DO TRIUNFO",
              "MARQUÊS DE POMBAL", "MARQUÊS DE SÃO VICENTE", "MARQUÊS DE HERVAL", "CONDE DA BOA VISTA",
              "VISCONDE DE OURO PRETO", "VISCONDE DE INHAÚMA", "VISCONDE DE PIRAJÁ", "IMPERATRIZ LEOPOLDINA",
              "DOM PEDRO I", "DOM JOÃO VI", "ZUMBI DOS PALMARES", "OSVALDO ARANHA", "LAURO MÜLLER",
              "JÚLIO DE CASTILHOS", "ASSIS BRASIL", "BENTO GONÇALVES", "SILVA JARDIM", "SILVEIRA MARTINS",
              "VOLUNTÁRIOS DA PÁTRIA", "PEDRO ÁLVARES CABRAL", "FERNÃO DIAS", "BARTOLOMEU BUENO", "BORBA GATO",
              "ANHANGUERA", "ANCHIETA", "MEM DE SÁ", "TOMÉ DE SOUSA", "MARTIM AFONSO", "REGENTE FEIJÓ",
              "JOÃO RAMALHO", "TIBIRIÇÁ", "ARARIBOIA",
              # profissões e patentes com nomes genéricos
              "PROFESSOR JOÃO SILVA", "PROFESSORA MARIA JOSÉ", "DOUTOR JOSÉ ANTÔNIO", "DOUTOR CARLOS CHAGAS",
              "DOUTOR ARNALDO", "DOUTOR PEDRO ALVES", "ENGENHEIRO JOSÉ SANTOS", "ENGENHEIRO LUÍS CARLOS BERRINI",
              "CORONEL JOÃO PEREIRA", "CORONEL ANTÔNIO LIMA", "CAPITÃO JOSÉ ALVES", "MAJOR JOÃO GOMES",
              "TENENTE PEDRO SOUZA", "SARGENTO ANTÔNIO SILVA", "VEREADOR JOSÉ RIBEIRO", "PREFEITO ANTÔNIO COSTA",
              "PADRE JOSÉ CARVALHO", "FREI ANTÔNIO", "DESEMBARGADOR JOÃO MOREIRA", "DOUTOR ANTÔNIO CARLOS",
              "PROFESSOR ANTÔNIO RAMOS", "PROFESSORA ANA ROCHA",
              # estados e países
              "SERGIPE", "MARANHÃO", "PARÁ", "MATO GROSSO", "RIO DE JANEIRO", "RIO GRANDE DO NORTE", "TOCANTINS",
              "RONDÔNIA", "RORAIMA", "ACRE", "AMAPÁ", "ARGENTINA", "URUGUAI", "PARAGUAI", "CHILE", "PORTUGAL",
              "ITÁLIA", "FRANÇA", "ALEMANHA", "JAPÃO", "ESPANHA", "INGLATERRA", "ESTADOS UNIDOS", "CANADÁ",
              "MÉXICO", "PERU", "BOLÍVIA", "COLÔMBIA", "VENEZUELA",
              # árvores, flores e bichos
              "DAS HORTÊNSIAS", "DAS MARGARIDAS", "DAS ORQUÍDEAS", "DAS ROSAS", "DAS AZALEIAS", "DAS VIOLETAS",
              "DAS TULIPAS", "DAS BROMÉLIAS", "DAS AROEIRAS", "DAS FIGUEIRAS", "DAS MANGUEIRAS", "DAS LARANJEIRAS",
              "DAS GOIABEIRAS", "DOS JACARANDÁS", "DOS JATOBÁS", "DOS CEDROS", "DOS CARVALHOS", "DOS ANGICOS",
              "DOS COQUEIROS", "DOS JEQUITIBÁS", "DAS PEROBAS", "DAS CEREJEIRAS", "DAS CASTANHEIRAS",
              "DAS AMENDOEIRAS", "DAS SAMAMBAIAS", "DOS GIRASSÓIS", "DOS LÍRIOS", "DOS CRAVOS", "DAS PAINEIRAS",
              "DOS MANACÁS", "DAS SERINGUEIRAS", "DAS BEGÔNIAS", "DAS DÁLIAS", "DAS ANDORINHAS", "DOS CANÁRIOS",
              "DOS SABIÁS", "DAS GARÇAS", "DOS TUCANOS", "DAS ARARAS", "DOS PAPAGAIOS", "DOS CISNES",
              "DOS FLAMINGOS", "DAS CODORNAS", "DOS PARDAIS", "DAS PERDIZES",
              # outros
              "DA INDEPENDÊNCIA", "DA REPÚBLICA", "DA LIBERDADE", "DA PAZ", "DA SAUDADE", "DA GLÓRIA",
              "DA CONSOLAÇÃO", "DO COMÉRCIO", "DA MATRIZ", "DA IGREJA", "DO ROSÁRIO", "DA ESTAÇÃO", "DA PRAIA",
              "DO PORTO", "DA CONCÓRDIA", "DA UNIÃO", "DA HARMONIA", "DO PROGRESSO", "DA ALEGRIA", "DA ESPERANÇA",
              "DA VITÓRIA", "DO BOSQUE", "DO LAGO", "DA LAGOA", "DA MATA", "DO CAMPO", "DA SERRA", "DO SOL",
              "DA LUZ", "DAS NAÇÕES", "DOS EXPEDICIONÁRIOS", "DOS ESTUDANTES", "DOS TRABALHADORES", "DOS OPERÁRIOS",
              "DOS FERROVIÁRIOS", "DOS IMIGRANTES", "DOS PIONEIROS", "DOS COLONOS", "DOS TROPEIROS",
              "DOS INCONFIDENTES", "DOS ARTISTAS", "DOS BOMBEIROS", "DAS MISSÕES", "DAS INDÚSTRIAS", "DO CONTORNO",
              "BOA VISTA", "BOA ESPERANÇA", "BOM RETIRO", "BOM SUCESSO", "BOM PASTOR", "MONTE ALEGRE",
              "MONTE CASTELO", "MONTE SINAI", "SERRA NEGRA", "CAMPO GRANDE", "CAMPO BELO", "VALE VERDE",
              "RIO VERDE", "RIO NEGRO", "RIO BRANCO", "RIO PARDO", "PORTO SEGURO", "LAGOA SANTA", "PONTE NOVA",
              "VILA RICA", "ALTO DA BOA VISTA")
# Bairros genéricos: existem em quase toda cidade (CENTRO, JARDIM X, VILA Y, SANTA Z); servem em qualquer documento.
_BAIRROS = ("CENTRO", "JARDIM AMÉRICA", "VILA NOVA", "SANTA CRUZ", "BOA VISTA", "SÃO JOSÉ", "PLANALTO", "CIDADE NOVA",
            "SANTO ANTÔNIO", "INDUSTRIAL", "BELA VISTA", "PARQUE DAS NAÇÕES", "JARDIM EUROPA", "NOVA ESPERANÇA",
            "SÃO FRANCISCO", "JARDIM DAS FLORES", "VILA ESPERANÇA", "PARQUE INDUSTRIAL", "JARDIM PRIMAVERA",
            "SANTA TEREZINHA", "LIBERDADE", "ALVORADA", "SÃO CRISTÓVÃO", "JARDIM SÃO PAULO", "NOVO HORIZONTE",
            "VILA RICA", "COHAB", "JARDIM PAULISTA", "SÃO JOÃO", "SANTA LUZIA",
            "ALTO DA SERRA", "JARDIM BOTÂNICO", "VILA SÃO JORGE", "PONTE ALTA", "MORADA DO SOL", "JARDIM IPANEMA",
            "AEROPORTO", "ÁGUA BRANCA", "ALTO ALEGRE", "ALTO DA GLÓRIA", "AREAL", "BARRA", "BOA ESPERANÇA", "BOM JESUS",
            "BOM RETIRO", "BONFIM", "BOSQUE", "CABRAL", "CACHOEIRINHA", "CAMPO BELO", "CARMO", "CIDADE ALTA",
            "CIDADE BAIXA", "CIDADE JARDIM", "CIDADE UNIVERSITÁRIA", "COQUEIROS", "CRUZEIRO", "CRISTO REDENTOR",
            "CRISTO REI", "DOM BOSCO", "DOM PEDRO", "ELDORADO", "ESTRELA", "FÁTIMA", "FLORESTA", "GLÓRIA", "GUANABARA",
            "HORTO", "INDEPENDÊNCIA", "IPIRANGA", "ITAPOÃ", "JARDIM ATLÂNTICO", "JARDIM BRASIL", "JARDIM CALIFÓRNIA",
            "JARDIM DAS OLIVEIRAS", "JARDIM DAS PALMEIRAS", "JARDIM DO LAGO", "JARDIM ESPERANÇA", "JARDIM GUANABARA",
            "JARDIM IMPERIAL", "JARDIM INDEPENDÊNCIA", "JARDIM ITÁLIA", "JARDIM LIMOEIRO", "JARDIM NOVA ESPERANÇA",
            "JARDIM NOVO HORIZONTE", "JARDIM PANORAMA", "JARDIM PARAÍSO", "JARDIM PLANALTO", "JARDIM SANTA CLARA",
            "JARDIM SANTA RITA", "JARDIM SANTO ANTÔNIO", "JARDIM SÃO JOSÉ", "JARDIM SÃO PEDRO", "JARDIM SOCIAL",
            "JARDIM TROPICAL", "JARDIM VITÓRIA", "JARDIM AEROPORTO", "LAGOA", "LAGOA NOVA", "LAGOINHA", "LAPA",
            "LARANJEIRAS", "LOURDES", "MERCÊS", "MONTE ALEGRE", "MONTE CASTELO", "MONTE VERDE", "NAZARÉ",
            "NOSSA SENHORA DAS GRAÇAS", "NOSSA SENHORA DE FÁTIMA", "NOVA CIDADE", "NOVO MUNDO", "PARAÍSO",
            "PARQUE DAS ÁRVORES", "PARQUE RESIDENCIAL", "PARQUE SÃO JORGE", "PARQUE SÃO LUCAS", "PARQUE VERDE", "PENHA",
            "PIRATININGA", "PROGRESSO", "RECREIO", "SANTA AMÉLIA", "SANTA CECÍLIA", "SANTA EFIGÊNIA", "SANTA INÊS",
            "SANTA LÚCIA", "SANTA MÔNICA", "SANTA ROSA", "SANTA TEREZA", "SANTANA", "SANTO AMARO", "SÃO BENTO",
            "SÃO BERNARDO", "SÃO CAETANO", "SÃO DIMAS", "SÃO GERALDO", "SÃO JORGE", "SÃO JUDAS", "SÃO LUCAS",
            "SÃO LUIZ", "SÃO MARCOS", "SÃO MATEUS", "SÃO MIGUEL", "SÃO PEDRO", "SÃO SEBASTIÃO", "SÃO VICENTE",
            "SERRARIA", "TIRADENTES", "TRINDADE", "UNIÃO", "VÁRZEA", "VILA INDUSTRIAL", "VILA IPIRANGA", "VILA MARIA",
            "VILA OPERÁRIA", "VILA REAL", "VILA SANTA CATARINA", "VILA SÃO PEDRO", "VISTA ALEGRE", "IGUATEMI",
            "CONJUNTO HABITACIONAL", "JARDIM DAS ACÁCIAS", "PARQUE DOS IPÊS", "VILA DAS FLORES", "NOVA BRASÍLIA")
# Prefixos dos bairros genéricos compostos ("JARDIM" + um nome): com a base do vocabulário de ruas, o bairro novo
# chega a qualquer comprimento (cabe onde estava "PREFEITO JOSÉ WALTER").
_PREFIXOS_BAIRRO = (("JARDIM", 40), ("VILA", 25), ("PARQUE", 15), ("CONJUNTO", 6), ("RESIDENCIAL", 6), ("CIDADE", 3),
                    ("ALTO", 3), ("LOTEAMENTO", 2))
# Bairros que só existem na cidade deles: PARANOÁ com FEIRA DE SANTANA/BA é a pista que a regra bairro × cidade acha.
_BAIRROS_DA_CIDADE = {
    ("SÃO PAULO", "SP"): ("MOOCA", "MORUMBI", "PERDIZES", "PINHEIROS", "TATUAPÉ", "SACOMÃ", "VILA MARIANA",
                          "BARRA FUNDA",
                          "CASA VERDE", "CAMPO LIMPO", "VILA ANDRADE", "VILA CARRÃO", "VILA FORMOSA", "VILA GUILHERME",
                          "VILA LEOPOLDINA", "VILA MATILDE", "VILA PRUDENTE", "VILA ROMANA", "VILA SÔNIA", "CONSOLAÇÃO",
                          "ITAQUERA", "BUTANTÃ", "JABAQUARA", "MOEMA", "HIGIENÓPOLIS", "BRÁS", "CAMBUCI", "ACLIMAÇÃO"),
    ("RIO DE JANEIRO", "RJ"): ("BOTAFOGO", "COPACABANA", "JACAREPAGUÁ", "MARACANÃ", "TIJUCA", "GRAJAÚ", "HUMAITÁ",
                               "SÃO CONRADO", "VILA ISABEL", "QUINTINO BOCAIÚVA", "IPANEMA", "LEBLON",
                               "BARRA DA TIJUCA",
                               "MADUREIRA", "BANGU", "FLAMENGO", "MÉIER", "ANDARAÍ", "BENFICA"),
    ("BELO HORIZONTE", "MG"): ("BARREIRO", "FUNCIONÁRIOS", "MANGABEIRAS", "PADRE EUSTÁQUIO", "SAVASSI", "PAMPULHA",
                               "BURITIS", "SAGRADA FAMÍLIA", "CARLOS PRATES", "VENDA NOVA", "SION"),
    ("CURITIBA", "PR"): ("CAJURU", "CAPÃO RASO", "PILARZINHO", "PORTÃO", "SÍTIO CERCADO", "SANTA FELICIDADE",
                         "BOQUEIRÃO", "ÁGUA VERDE", "BATEL", "REBOUÇAS", "BACACHERI", "BIGORRILHO", "XAXIM"),
    ("PORTO ALEGRE", "RS"): ("PASSO DA AREIA", "MENINO DEUS", "MOINHOS DE VENTO", "SARANDI", "TRISTEZA", "RESTINGA",
                             "PARTENON", "AZENHA", "BOM FIM"),
    ("PELOTAS", "RS"): ("TRÊS VENDAS", "FRAGATA"),
    ("SALVADOR", "BA"): ("PITUBA", "BROTAS", "FEDERAÇÃO", "ITAPUÃ", "PERIPERI", "CABULA", "ONDINA", "RIO VERMELHO",
                         "IMBUÍ", "PIRAJÁ", "BARRIS", "CANELA", "ITAIGARA", "PAU DA LIMA"),
    ("FORTALEZA", "CE"): ("ALDEOTA", "PAPICU", "BENFICA", "MONTESE", "PARANGABA", "MESSEJANA", "MEIRELES",
                          "PREFEITO JOSÉ WALTER", "MONDUBIM", "MUCURIPE", "COCÓ", "VARJOTA", "ANTÔNIO BEZERRA",
                          "CONJUNTO CEARÁ", "JANGURUSSU", "PARQUELÂNDIA", "DIONÍSIO TORRES"),
    ("RECIFE", "PE"): ("BOA VIAGEM", "CASA AMARELA", "MADALENA", "ESPINHEIRO", "AFLITOS", "DERBY", "IBURA",
                       "CASA FORTE", "PINA", "IMBIRIBEIRA", "JAQUEIRA"),
    ("NATAL", "RN"): ("CANDELÁRIA", "CAPIM MACIO", "TIROL", "PONTA NEGRA", "ALECRIM", "NEÓPOLIS", "POTENGI"),
    ("MACEIÓ", "AL"): ("FAROL", "JATIÚCA", "PONTA VERDE", "PAJUÇARA", "CRUZ DAS ALMAS", "JACINTINHO"),
    ("BELÉM", "PA"): ("BATISTA CAMPOS", "UMARIZAL", "MARCO", "PEDREIRA", "GUAMÁ", "JURUNAS", "SACRAMENTA",
                      "TELÉGRAFO", "MARAMBAIA"),
    ("MANAUS", "AM"): ("ADRIANÓPOLIS", "ALEIXO", "PARQUE DEZ DE NOVEMBRO", "COMPENSA", "PONTA NEGRA", "TARUMÃ",
                       "COROADO", "JAPIIM"),
    ("BRASÍLIA", "DF"): ("ASA NORTE", "ASA SUL", "TAGUATINGA", "CEILÂNDIA", "GAMA", "GUARÁ", "SOBRADINHO",
                         "PLANALTINA", "SAMAMBAIA", "ÁGUAS CLARAS", "LAGO SUL", "LAGO NORTE", "PARANOÁ", "SUDOESTE",
                         "NÚCLEO BANDEIRANTE", "RECANTO DAS EMAS", "RIACHO FUNDO", "VICENTE PIRES"),
    ("GOIÂNIA", "GO"): ("SETOR BUENO", "SETOR OESTE", "SETOR SUL", "SETOR MARISTA", "SETOR UNIVERSITÁRIO",
                        "SETOR PEDRO LUDOVICO", "JARDIM GOIÁS"),
    ("VITÓRIA", "ES"): ("PRAIA DO CANTO", "JARDIM CAMBURI", "JARDIM DA PENHA", "BENTO FERREIRA", "ENSEADA DO SUÁ",
                        "MARUÍPE"),
    ("JOÃO PESSOA", "PB"): ("MANAÍRA", "TAMBAÚ", "CABO BRANCO", "BESSA", "BANCÁRIOS", "MANGABEIRA"),
    ("SÃO LUÍS", "MA"): ("RENASCENÇA", "COHAMA", "TURU", "CALHAU", "ANIL"),
    ("TERESINA", "PI"): ("JÓQUEI", "DIRCEU ARCOVERDE", "ININGA", "MOCAMBINHO"),
    ("ARACAJU", "SE"): ("ATALAIA", "GRAGERU"),
    ("FLORIANÓPOLIS", "SC"): ("INGLESES", "CANASVIEIRAS", "LAGOA DA CONCEIÇÃO", "CAMPECHE", "SACO DOS LIMÕES",
                              "ITACORUBI"),
}
_BAIRROS_CIDADE = tuple((b, c, uf) for (c, uf), bs in _BAIRROS_DA_CIDADE.items() for b in bs)
# Cidades na ordem da população (IBGE, Censo 2022; o sorteio pesa pela posição, como nos nomes): capitais e cidades
# grandes (o núcleo, valem em todos os splits), depois as outras.
_CAPITAIS_E_GRANDES = (
    ("SÃO PAULO", "SP"), ("RIO DE JANEIRO", "RJ"), ("BRASÍLIA", "DF"), ("FORTALEZA", "CE"), ("SALVADOR", "BA"),
    ("BELO HORIZONTE", "MG"), ("MANAUS", "AM"), ("CURITIBA", "PR"), ("RECIFE", "PE"), ("GOIÂNIA", "GO"),
    ("PORTO ALEGRE", "RS"), ("BELÉM", "PA"), ("GUARULHOS", "SP"), ("CAMPINAS", "SP"), ("SÃO LUÍS", "MA"),
    ("MACEIÓ", "AL"), ("CAMPO GRANDE", "MS"), ("SÃO GONÇALO", "RJ"), ("TERESINA", "PI"), ("JOÃO PESSOA", "PB"),
    ("SÃO BERNARDO DO CAMPO", "SP"), ("DUQUE DE CAXIAS", "RJ"), ("NOVA IGUAÇU", "RJ"), ("NATAL", "RN"),
    ("SANTO ANDRÉ", "SP"), ("OSASCO", "SP"), ("SOROCABA", "SP"), ("UBERLÂNDIA", "MG"), ("RIBEIRÃO PRETO", "SP"),
    ("SÃO JOSÉ DOS CAMPOS", "SP"), ("CUIABÁ", "MT"), ("JABOATÃO DOS GUARARAPES", "PE"), ("CONTAGEM", "MG"),
    ("JOINVILLE", "SC"), ("FEIRA DE SANTANA", "BA"), ("ARACAJU", "SE"), ("LONDRINA", "PR"), ("FLORIANÓPOLIS", "SC"),
    ("JUIZ DE FORA", "MG"), ("APARECIDA DE GOIÂNIA", "GO"), ("SERRA", "ES"), ("CAMPOS DOS GOYTACAZES", "RJ"),
    ("ANANINDEUA", "PA"), ("NITERÓI", "RJ"), ("VILA VELHA", "ES"), ("CAXIAS DO SUL", "RS"), ("PORTO VELHO", "RO"),
    ("MOGI DAS CRUZES", "SP"), ("MACAPÁ", "AP"), ("SANTOS", "SP"), ("BOA VISTA", "RR"), ("RIO BRANCO", "AC"),
    ("VITÓRIA", "ES"), ("PALMAS", "TO"))
_CIDADES = _CAPITAIS_E_GRANDES + (
    ("SÃO JOSÉ DO RIO PRETO", "SP"), ("JUNDIAÍ", "SP"), ("PIRACICABA", "SP"), ("CAMPINA GRANDE", "PB"),
    ("MONTES CLAROS", "MG"), ("MARINGÁ", "PR"), ("BETIM", "MG"), ("ANÁPOLIS", "GO"), ("PETROLINA", "PE"),
    ("BAURU", "SP"),
    ("CARUARU", "PE"), ("VITÓRIA DA CONQUISTA", "BA"), ("BLUMENAU", "SC"), ("PONTA GROSSA", "PR"), ("CAUCAIA", "CE"),
    ("FRANCA", "SP"), ("OLINDA", "PE"), ("CASCAVEL", "PR"), ("CANOAS", "RS"), ("UBERABA", "MG"), ("SANTARÉM", "PA"),
    ("SÃO JOSÉ DOS PINHAIS", "PR"), ("PELOTAS", "RS"), ("CAMAÇARI", "BA"), ("VÁRZEA GRANDE", "MT"), ("LIMEIRA", "SP"),
    ("JUAZEIRO DO NORTE", "CE"), ("TAUBATÉ", "SP"), ("FOZ DO IGUAÇU", "PR"), ("PETRÓPOLIS", "RJ"),
    ("IMPERATRIZ", "MA"), ("SANTA MARIA", "RS"), ("MARABÁ", "PA"), ("GRAVATAÍ", "RS"), ("MOSSORÓ", "RN"),
    ("VOLTA REDONDA", "RJ"), ("GOVERNADOR VALADARES", "MG"), ("CHAPECÓ", "SC"), ("MACAÉ", "RJ"), ("RONDONÓPOLIS", "MT"),
    ("DOURADOS", "MS"), ("ARARAQUARA", "SP"), ("JUAZEIRO", "BA"), ("MARÍLIA", "SP"), ("ARAPIRACA", "AL"),
    ("DIVINÓPOLIS", "MG"), ("NOVO HAMBURGO", "RS"), ("IPATINGA", "MG"), ("PRESIDENTE PRUDENTE", "SP"),
    ("RIO VERDE", "GO"), ("CRICIÚMA", "SC"), ("PASSO FUNDO", "RS"), ("SOBRAL", "CE"), ("SINOP", "MT"),
    ("ITABUNA", "BA"), ("CACHOEIRO DE ITAPEMIRIM", "ES"), ("GUARAPUAVA", "PR"), ("ILHÉUS", "BA"), ("LINHARES", "ES"),
    ("LAGES", "SC"), ("POÇOS DE CALDAS", "MG"), ("PARNAÍBA", "PI"), ("ITAJAÍ", "SC"), ("TRÊS LAGOAS", "MS"))
_MESES = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
# partícula de cada sobrenome (sem acento): "DA SILVA", "DOS SANTOS", "DE SOUZA" (nunca "DOS SILVA")
_PARTICULA = {**dict.fromkeys(("SILVA", "COSTA", "ROCHA", "CUNHA", "CRUZ", "LUZ", "PAZ", "FONSECA", "SILVEIRA",
                               "PAIXAO", "CONCEICAO", "MOTA", "MOTTA", "GAMA", "NOBREGA", "ASSUNCAO", "TRINDADE",
                               "PENHA"), "DA"),
              **dict.fromkeys(("SANTOS", "REIS", "PASSOS", "ANJOS"), "DOS"),
              **dict.fromkeys(("NASCIMENTO", "CARMO", "AMARAL", "PRADO", "VALE", "SACRAMENTO"), "DO"),
              **dict.fromkeys(("NEVES", "CHAGAS"), "DAS"),
              **dict.fromkeys(("SOUZA", "SOUSA", "OLIVEIRA", "ALMEIDA", "LIMA", "ARAUJO", "FREITAS", "ANDRADE", "MOURA",
                               "CARVALHO", "MORAES", "MORAIS", "JESUS", "MELO", "MELLO", "CASTRO", "BARROS", "MATOS",
                               "MATTOS", "ASSIS", "ABREU", "AGUIAR", "AZEVEDO", "BRITO", "MEDEIROS", "MENEZES",
                               "MIRANDA", "QUEIROZ", "MACEDO", "SIQUEIRA", "VASCONCELOS", "FARIAS", "FARIA", "PAULA",
                               "MAGALHAES", "MENDONCA", "SALES", "LUCENA", "CAMPOS", "FIGUEIREDO", "SENA", "LACERDA",
                               "ALENCAR", "HOLANDA", "LIRA", "MESQUITA", "PAIVA", "GOIS"), "DE")}
# os que quase nunca vêm sem a partícula ("DE JESUS", "DA CONCEIÇÃO", "DOS ANJOS")
_PARTICULA_QUASE_SEMPRE = {"JESUS", "CONCEICAO", "ANJOS", "CARMO", "PAIXAO", "LUZ", "PAZ", "CRUZ", "ASSUNCAO",
                           "TRINDADE", "PENHA", "ASSIS", "PASSOS", "CHAGAS", "SACRAMENTO"}
_PARTICULAS = {"DA", "DE", "DO", "DAS", "DOS", "E"}
# razão social: ramo de atividade e produto ("SANTOS COMÉRCIO DE ALIMENTOS LTDA", "M R C FILHO FOTOS")
_RAMOS = ("FOTOS", "CALÇADOS", "MODAS", "CONFECÇÕES", "ALIMENTOS", "BEBIDAS", "MATERIAIS DE CONSTRUÇÃO", "AUTO PEÇAS",
          "PAPELARIA", "FERRAGENS", "MÓVEIS", "ELETRÔNICOS", "INFORMÁTICA", "TRANSPORTES", "CONSTRUÇÕES", "ENGENHARIA",
          "SERVIÇOS", "DISTRIBUIDORA", "REPRESENTAÇÕES", "CONTABILIDADE", "ÓTICA", "LANCHONETE", "PANIFICADORA",
          "TECIDOS", "VIDROS", "TINTAS", "COSMÉTICOS", "CONSULTORIA", "IMÓVEIS", "VEÍCULOS", "EVENTOS", "GRÁFICA",
          "TURISMO", "PRESENTES", "ACESSÓRIOS", "EMBALAGENS", "AGROPECUÁRIA", "MATERIAIS ELÉTRICOS")
_PRODUTOS = ("ALIMENTOS", "BEBIDAS", "CONFECÇÕES", "CALÇADOS", "MATERIAIS DE CONSTRUÇÃO", "PEÇAS", "MÓVEIS",
             "ELETRODOMÉSTICOS", "COMBUSTÍVEIS", "MEDICAMENTOS", "CEREAIS", "FRUTAS", "GÁS", "MATERIAIS ELÉTRICOS",
             "EMBALAGENS", "PRODUTOS ALIMENTÍCIOS", "VARIEDADES")

# Faixas de CEP dos Correios (início, fim; inteiros de 8 dígitos), aproximadas: por cidade e, de reserva, por UF.
# Um CEP fora da faixa da cidade impressa é pista que um fraudador cuidadoso não deixa.
FAIXAS_UF = {"SP": (1000000, 19999999), "RJ": (20000000, 28999999), "ES": (29000000, 29999999),
             "MG": (30000000, 39999999), "BA": (40000000, 48999999), "SE": (49000000, 49999999),
             "PE": (50000000, 56999999), "AL": (57000000, 57999999), "PB": (58000000, 58999999),
             "RN": (59000000, 59999999), "CE": (60000000, 63999999), "PI": (64000000, 64999999),
             "MA": (65000000, 65999999), "PA": (66000000, 68899999), "AP": (68900000, 68999999),
             "AM": (69000000, 69299999), "RR": (69300000, 69399999), "AC": (69900000, 69999999),
             "DF": (70000000, 73699999), "GO": (73700000, 76799999), "RO": (76800000, 76999999),
             "TO": (77000000, 77999999), "MT": (78000000, 78899999), "MS": (79000000, 79999999),
             "PR": (80000000, 87999999), "SC": (88000000, 89999999), "RS": (90000000, 99999999)}
FAIXAS_CEP = {
    "SAO PAULO": (1000000, 5999999), "GUARULHOS": (7000000, 7399999), "OSASCO": (6000000, 6299999),
    "SANTO ANDRE": (9000000, 9299999), "SAO BERNARDO DO CAMPO": (9600000, 9899999),
    "MOGI DAS CRUZES": (8700000, 8899999), "SANTOS": (11000000, 11099999), "TAUBATE": (12010000, 12099999),
    "SAO JOSE DOS CAMPOS": (12200000, 12249999), "CAMPINAS": (13000000, 13139999), "JUNDIAI": (13200000, 13219999),
    "PIRACICABA": (13400000, 13429999), "LIMEIRA": (13480000, 13489999), "RIBEIRAO PRETO": (14000000, 14109999),
    "FRANCA": (14400000, 14414999), "ARARAQUARA": (14800000, 14814999), "SAO JOSE DO RIO PRETO": (15000000, 15104999),
    "BAURU": (17000000, 17109999), "MARILIA": (17500000, 17539999), "SOROCABA": (18000000, 18109999),
    "PRESIDENTE PRUDENTE": (19000000, 19109999), "RIO DE JANEIRO": (20000000, 23799999),
    "NITEROI": (24000000, 24399999), "SAO GONCALO": (24400000, 24799999), "DUQUE DE CAXIAS": (25000000, 25099999),
    "PETROPOLIS": (25600000, 25699999), "NOVA IGUACU": (26000000, 26299999), "VOLTA REDONDA": (27200000, 27299999),
    "MACAE": (27900000, 27999999), "CAMPOS DOS GOYTACAZES": (28000000, 28099999), "VITORIA": (29000000, 29099999),
    "VILA VELHA": (29100000, 29129999), "SERRA": (29160000, 29179999), "CACHOEIRO DE ITAPEMIRIM": (29300000, 29319999),
    "LINHARES": (29900000, 29909999), "BELO HORIZONTE": (30000000, 31999999), "CONTAGEM": (32000000, 32399999),
    "BETIM": (32500000, 32699999), "GOVERNADOR VALADARES": (35000000, 35099999), "IPATINGA": (35160000, 35169999),
    "DIVINOPOLIS": (35500000, 35509999), "JUIZ DE FORA": (36000000, 36199999), "POCOS DE CALDAS": (37700000, 37719999),
    "UBERABA": (38000000, 38099999), "UBERLANDIA": (38400000, 38499999), "MONTES CLAROS": (39400000, 39409999),
    "SALVADOR": (40000000, 42599999), "CAMACARI": (42800000, 42849999), "FEIRA DE SANTANA": (44000000, 44109999),
    "VITORIA DA CONQUISTA": (45000000, 45109999), "ITABUNA": (45600000, 45619999), "ILHEUS": (45650000, 45669999),
    "JUAZEIRO": (48900000, 48999999), "ARACAJU": (49000000, 49099999), "RECIFE": (50000000, 52999999),
    "OLINDA": (53000000, 53399999), "JABOATAO DOS GUARARAPES": (54000000, 54499999), "CARUARU": (55000000, 55049999),
    "PETROLINA": (56300000, 56399999), "MACEIO": (57000000, 57099999), "ARAPIRACA": (57300000, 57319999),
    "JOAO PESSOA": (58000000, 58099999), "CAMPINA GRANDE": (58400000, 58439999), "NATAL": (59000000, 59139999),
    "MOSSORO": (59600000, 59639999), "FORTALEZA": (60000000, 61599999), "CAUCAIA": (61600000, 61699999),
    "SOBRAL": (62000000, 62099999), "JUAZEIRO DO NORTE": (63000000, 63049999), "TERESINA": (64000000, 64099999),
    "PARNAIBA": (64200000, 64219999), "SAO LUIS": (65000000, 65109999), "IMPERATRIZ": (65900000, 65939999),
    "BELEM": (66000000, 66999999), "ANANINDEUA": (67000000, 67199999), "SANTAREM": (68000000, 68099999),
    "MARABA": (68500000, 68519999), "MACAPA": (68900000, 68919999), "MANAUS": (69000000, 69099999),
    "BOA VISTA": (69300000, 69339999), "RIO BRANCO": (69900000, 69923999), "BRASILIA": (70000000, 73699999),
    "GOIANIA": (74000000, 74899999), "APARECIDA DE GOIANIA": (74900000, 74999999), "ANAPOLIS": (75000000, 75149999),
    "RIO VERDE": (75900000, 75919999), "PORTO VELHO": (76800000, 76849999), "PALMAS": (77000000, 77299999),
    "CUIABA": (78000000, 78109999), "VARZEA GRANDE": (78110000, 78159999), "SINOP": (78550000, 78559999),
    "RONDONOPOLIS": (78700000, 78749999), "CAMPO GRANDE": (79000000, 79129999), "TRES LAGOAS": (79600000, 79649999),
    "DOURADOS": (79800000, 79849999), "CURITIBA": (80000000, 82999999), "SAO JOSE DOS PINHAIS": (83000000, 83099999),
    "PONTA GROSSA": (84000000, 84099999), "GUARAPUAVA": (85000000, 85099999), "CASCAVEL": (85800000, 85824999),
    "FOZ DO IGUACU": (85850000, 85899999), "LONDRINA": (86000000, 86199999), "MARINGA": (87000000, 87099999),
    "FLORIANOPOLIS": (88000000, 88099999), "ITAJAI": (88300000, 88319999), "LAGES": (88500000, 88529999),
    "CRICIUMA": (88800000, 88819999), "BLUMENAU": (89000000, 89099999), "JOINVILLE": (89200000, 89239999),
    "CHAPECO": (89800000, 89899999), "PORTO ALEGRE": (90000000, 91999999), "CANOAS": (92000000, 92499999),
    "NOVO HAMBURGO": (93300000, 93549999), "GRAVATAI": (94000000, 94199999), "CAXIAS DO SUL": (95000000, 95129999),
    "PELOTAS": (96000000, 96099999), "SANTA MARIA": (97000000, 97119999), "PASSO FUNDO": (99000000, 99099999),
}


def _normalizar(texto: str) -> str:
    """Sem acento, em maiúsculas e com espaços simples: "São José dos Campos" → "SAO JOSE DOS CAMPOS"."""
    return " ".join(unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().upper().split())


# Vocabulário externo: config/vocabulario/{prenomes,sobrenomes,ruas,bairros,cidades}.txt, uma entrada por linha
# (cidades como "NOME;UF"), na grafia com acento e, em prenomes e sobrenomes, do mais comum para o mais raro (o
# sorteio pesa pela posição), SUBSTITUI a lista embutida correspondente (prenomes.txt vale para os dois gêneros).
# Lido uma vez, na importação. As bases oficiais da mesma pasta (abaixo), quando existem, valem mais que os dois.
_VOCABULARIO = Path(__file__).resolve().parents[2] / "config" / "vocabulario"


def _do_arquivo(nome: str):
    caminho = _VOCABULARIO / f"{nome}.txt"
    if not caminho.is_file():
        return None
    entradas = []
    for linha in caminho.read_text("utf-8-sig").splitlines():
        linha = " ".join(linha.upper().split())
        if not linha:
            continue
        if nome != "cidades":
            entradas.append(linha)
            continue
        partes = tuple(p.strip() for p in linha.split(";"))
        if len(partes) != 2 or _normalizar(partes[1]) not in FAIXAS_UF:
            raise ValueError(f"{caminho}: esperava NOME;UF, veio {linha!r}")
        entradas.append((partes[0], _normalizar(partes[1])))
    return tuple(entradas) or None


# Vocabulário aberto (prenomes, sobrenomes, ruas, bairros, cidades): um NÚCLEO das entradas mais comuns (os
# primeiros prenomes e sobrenomes, as ruas e bairros que existem em toda cidade, capitais e cidades grandes) vale em
# todos os splits; a cauda é repartida por um hash estável de cada palavra sem acento (70/15/15). Sem o núcleo,
# MARIA, JOSÉ e SILVA caíam num split só e, no treino, os nomes mais comuns do país só apareciam nos originais: o
# modelo aprendia "MARIA ⇒ genuíno". Com a cauda repartida e o nome completo sorteado por um hash da COMBINAÇÃO
# (``_na_fatia``), nenhum nome falso do teste aparece como falso no treino: um modelo que lê o texto (OCR + layout,
# VLM) não acerta o teste decorando a lista. Meses, UF e o tipo de logradouro não identificam ninguém e ficam inteiros.
# As ruas de verdade (base de logradouros) são repartidas pela rua inteira (município, tipo, título e nome).
_FATIAS = (("treino", 70), ("teste", 85), ("validacao", 100))
_SPLITS = tuple(sp for sp, _ in _FATIAS)


def _chave(entrada) -> str:
    """A palavra da entrada sem acento (cidade e bairro da cidade são tuplas: conta o nome); JOSÉ = JOSE."""
    return _normalizar(entrada[0] if isinstance(entrada, tuple) else entrada)


def _fatia(entrada) -> str:
    """Split da entrada pelo hash da PALAVRA. Assim "SANTOS" cidade e "SANTOS" sobrenome caem no mesmo split."""
    b = int(hashlib.sha1(repr(_chave(entrada)).encode()).hexdigest(), 16) % 100
    return next(sp for sp, limite in _FATIAS if b < limite)


# o vocabulário embutido (ou o dos .txt): o de reserva, quando falta a base oficial
_EMBUTIDO = {"nomes": _do_arquivo("prenomes") or _NOMES, "meios": _MEIOS,
             "sobrenomes": _do_arquivo("sobrenomes") or _SOBRENOMES, "ruas": _do_arquivo("ruas") or _NOMES_RUA,
             "bairros": _do_arquivo("bairros") or _BAIRROS, "bairros_cidade": _BAIRROS_CIDADE,
             "cidades": _do_arquivo("cidades") or _CIDADES}
# quantas entradas do começo de cada lista são núcleo; com a base de nomes, um pouco mais (os 40 prenomes mais comuns
# de cada gênero, os 50 sobrenomes); com a de municípios, as capitais e as 50 cidades mais populosas
_NUCLEO_TAM = {"nomes_f": 30, "nomes_m": 30, "meios_f": 8, "meios_m": 8, "sobrenomes": 40, "ruas": 20, "bairros": 30,
               "cidades": len(_CAPITAIS_E_GRANDES)}
_NUCLEO_TAM_BASE = {"nomes_f": 40, "nomes_m": 40, "meios_f": 15, "meios_m": 15, "sobrenomes": 50, "cidades": 50}
# palavras de estrutura do nome (partícula, sufixo): não identificam ninguém e valem em todos os splits (JUNIOR e
# NETO também são prenome; DOS e DAS aparecem como prenome no Censo, por erro de cadastro)
_ESTRUTURAIS = frozenset("DA DE DO DAS DOS E FILHO FILHA JUNIOR JR NETO NETA NETTO SOBRINHO SOBRINHA SEGUNDO TERCEIRO "
                         "II III".split())
# listas embutidas na ordem da frequência (pesam pela posição) e, nas outras, quanto pesa uma entrada do núcleo; as
# da base pesam pela frequência real (pessoas no Censo, ocorrências no TSE, população)
_ZIPF = {"nomes_f", "nomes_m", "meios_f", "meios_m", "sobrenomes", "bairros", "cidades"}
_PESO_NUCLEO = {"ruas": 4.0}
_LISTAS_DE_NOME = frozenset(("nomes_f", "nomes_m", "meios_f", "meios_m", "sobrenomes"))


# ---------------------------------------------------------------------------
# bases oficiais (config/vocabulario; o formato está em LEIA-ME_nomes.md e LEIA-ME_lugares.md)
# ---------------------------------------------------------------------------

# Cada base é lida na primeira vez que alguém precisa dela (importar o módulo continua rápido) e fica em memória no
# processo; sem o arquivo, None, e o sorteio cai no vocabulário embutido. Ruas e bairros ficam como o texto do
# arquivo e o trecho de cada município; as linhas de uma cidade só são separadas quando ela é usada, num cache das
# últimas ``_CIDADES_EM_CACHE``.
_BASES: dict = {}
_BASES_LOCK = threading.RLock()
_CIDADES_EM_CACHE = 32
_PARTICULAS_NOME = ("DA", "DE", "DO", "DOS", "DAS", "E")  # na ordem das colunas p_da ... p_e de sobrenomes.tsv.gz
_SUFIXOS = frozenset("FILHO FILHA JUNIOR JR NETO NETA NETTO SOBRINHO SOBRINHA SEGUNDO TERCEIRO II III".split())


def _base(nome: str, ler):
    """A base ``nome``, lida por ``ler()`` uma vez só (mesmo com várias threads)."""
    if nome not in _BASES:
        with _BASES_LOCK:
            if nome not in _BASES:
                _BASES[nome] = ler()
    return _BASES[nome]


def _tsv(nome: str):
    """As linhas (listas de colunas, sem o cabeçalho) de config/vocabulario/<nome>; None sem o arquivo."""
    caminho = _VOCABULARIO / nome
    if not caminho.is_file():
        return None
    with (gzip.open if nome.endswith(".gz") else open)(caminho, "rt", encoding="utf-8") as f:
        return [linha.rstrip("\n").split("\t") for linha in f][1:]


def _ler_nomes():
    """A base de nomes pronta para o sorteio (``_nome_da_base``): prenomes por gênero com o peso do Censo, 2º prenome,
    pares e devoções, sobrenomes com o peso do TSE e a partícula de cada um, nº de sobrenomes, sufixos e o mapa de
    acentos; None sem os arquivos."""
    pre, sob = _tsv("prenomes.tsv.gz"), _tsv("sobrenomes.tsv.gz")
    caminho = _VOCABULARIO / "nomes_estrutura.json"
    if not (pre and sob and caminho.is_file()):
        return None
    est = json.loads(caminho.read_text("utf-8"))
    # palavra sem acento → (grafia com acento, fração das vezes que ela sai assim no TSE)
    acento = {p: (a, float(f)) for p, a, f, *_ in _tsv("acentos.tsv.gz") or ()}
    # cada prenome vai para o gênero da maioria no Censo (empatado, para os dois), com o peso de lá
    nomes = {"F": [], "M": []}
    for nome, acentuado, frac, _n, _o, f, m in pre:
        if nome in _PARTICULAS:  # DOS, DAS: erro de cadastro do Censo
            continue
        for g, n, outro in (("F", int(f), int(m)), ("M", int(m), int(f))):
            if n >= outro:
                nomes[g].append((n, nome))
        if acentuado != nome:
            acento.setdefault(nome, (acentuado, float(frac)))
    for g in nomes:
        nomes[g].sort(key=lambda x: -x[0])
    b = {"genero": {g: frozenset(k for _, k in v) for g, v in nomes.items()},
         "p_f": sum(int(r[5]) for r in pre) / sum(int(r[5]) + int(r[6]) for r in pre)}
    listas = {f"nomes_{g.lower()}": ([k for _, k in v], [n for n, _ in v]) for g, v in nomes.items()}
    # o 2º prenome de um composto fora da lista de pares: do mesmo gênero (MARIA JOSÉ e JOSÉ MARIA só pelos pares)
    for g, outro in (("F", "M"), ("M", "F")):
        de_fora = b["genero"][outro] - b["genero"][g]
        meios = {}
        for nome, n in est["segundo_prenome"][g]:
            k = _normalizar(nome)
            if k not in de_fora and k not in _PARTICULAS:
                meios[k] = meios.get(k, 0) + n
        listas[f"meios_{g.lower()}"] = (list(meios), list(meios.values()))
    # sobrenomes (sufixo não é sobrenome: FILHO, NETO no meio do nome são artefato): nº de palavras, chance de NÃO ter
    # partícula e a distribuição das partículas (DA, DE, DO, DOS, DAS, E)
    b["sob"], ks, ns = {}, [], []
    for s, k, n, frac, p0, *ps in sob:
        if k in _SUFIXOS:
            continue
        ks.append(k)
        ns.append(int(n))
        ps = tuple(map(float, ps))  # partícula vista em menos de 0,2% dos casos (DA SOUZA, DO SANTOS): erro de cadastro
        b["sob"][k] = (len(k.split()), float(p0) + sum(p for p in ps if p < 0.002),
                       tuple(p if p >= 0.002 else 0.0 for p in ps))
        for w, kw in zip(s.split(), k.split()):  # VILAS BÔAS, D'ÁVILA: o acento de cada palavra
            if w != kw:
                acento.setdefault(kw, (w, float(frac)))
    listas["sobrenomes"] = (ks, ns)
    b["listas"], b["acento"] = listas, acento
    # prenome forte de um gênero (20 mil pessoas ou mais no Censo e quase nunca sobrenome no TSE): como 1º sobrenome,
    # logo depois do prenome do outro gênero, pareceria prenome trocado (LORENA ANTONIO); SILVA e JESUS também
    # aparecem como prenome no Censo, mas são sobrenome
    n_sob = dict(zip(ks, ns))
    b["so_do_genero"] = {g: frozenset(k for n, k in nomes[g] if n >= 20000 and n_sob.get(k, 0) < n / 100) for g in "FM"}
    # tipo do prenome: P(composto sem devoção), P(devocional) e quantos com 2º prenome, por 1º prenome (os 400 mais
    # comuns) e, nos outros, a taxa do resto (a geral tem o peso de MARIA e ANA, compostas 63% e 93% das vezes);
    # P(composto | devocional) é pequena (MARIA APARECIDA DE FÁTIMA)
    tp = est["tipo_prenome"]
    b["p_cd"] = {g: tp[g]["composto_devocional"] / max(1, tp[g]["devocional"] + tp[g]["composto_devocional"])
                 for g in "FM"}
    b["por_primeiro"] = {g: {k: (max(0.0, v["composto"] - v["devocional"] * b["p_cd"][g]), v["devocional"],
                                 v["n"] * v["composto"]) for k, v in est["por_primeiro_prenome"][g].items()}
                         for g in "FM"}
    b["geral"] = {}
    for g in "FM":
        pp, cd = est["por_primeiro_prenome"][g].values(), tp[g]["composto_devocional"]
        resto = max(1, sum(tp[g].values()) - sum(v["n"] for v in pp))
        composto = max(0.0, tp[g]["composto"] + cd - sum(v["n"] * v["composto"] for v in pp)) / resto
        devocional = max(0.0, tp[g]["devocional"] + cd - sum(v["n"] * v["devocional"] for v in pp)) / resto
        b["geral"][g] = (max(0.0, composto - devocional * b["p_cd"][g]), devocional, None)
    b["pares"] = {}
    for g in "FM":  # 1º prenome → ([2º prenome], [pessoas], soma)
        pares = {}
        for par, n in est["compostos"][g]:
            p1, p2 = _normalizar(par).split(" ", 1)
            pares.setdefault(p1, ([], []))
            pares[p1][0].append(p2)
            pares[p1][1].append(n)
        b["pares"][g] = {p1: (s, n, sum(n)) for p1, (s, n) in pares.items()}
    b["devocoes"] = {}  # 1º prenome → ([complemento], [pessoas]): DE FÁTIMA com MARIA, BATISTA com JOÃO
    for d in est["devocoes"]:
        for p, n in d["primeiros"].items():
            par = b["devocoes"].setdefault(_normalizar(p), ([], []))
            par[0].append(_normalizar(d["complemento"]))
            par[1].append(n)
    # nº de sobrenomes (1 a 4+) por gênero e tipo de prenome: as duas tabelas da base combinadas (P(k|g)·P(k|t)/P(k))
    def dist(contagem):
        v = [0] * 4
        for k, n in contagem.items():
            if int(k) >= 1:
                v[min(int(k), 4) - 1] += n
        return [x / sum(v) for x in v]
    pt = est["sobrenomes_por_tipo_prenome"]
    geral = dist({k: sum(pt[t].get(k, 0) for t in pt) for k in "12345"})
    b["k"] = {(g, t): ((1, 2, 3, 4), [a * c / e if e else 0 for a, c, e in zip(dist(est["sobrenomes_por_nome"][g]),
                                                                              dist(pt[t]), geral)])
              for g in "FM" for t in pt}
    # partícula por posição: o último sobrenome leva mais que o primeiro (com 2: 18% e 39%) e o DA (de SILVA) vem
    # mais no fim; em cada posição, cada partícula (e a falta dela) ganha um fator, ajustado em 6 voltas até a fração
    # dela ali, sobre os 600 sobrenomes mais comuns e o resto (como um só, com a média dele), bater com a do TSE
    # (``_pesos_particula``)
    comuns, b["fator"] = [(n, b["sob"][k][1], b["sob"][k][2]) for k, n in zip(ks[:600], ns[:600])], {}
    resto = sum(ns[600:]) or 1
    comuns.append((resto, sum(n * b["sob"][k][1] for k, n in zip(ks[600:], ns[600:])) / resto,
                   tuple(sum(n * b["sob"][k][2][i] for k, n in zip(ks[600:], ns[600:])) / resto
                         for i in range(len(_PARTICULAS_NOME)))))
    for k, posicoes in est["particula_por_posicao"].items():
        for j, d in enumerate(posicoes):
            alvo = [d["nenhuma"] / sum(d.values())] + [d[p] / sum(d.values()) for p in _PARTICULAS_NOME]
            fator = [1.0] * len(alvo)
            for _ in range(6):
                obtido = [0.0] * len(alvo)
                for n, p0, ps in comuns:
                    w = [p0 * fator[0]] + [p * x for p, x in zip(ps, fator[1:])]
                    w[-1] *= j > 0  # o E não abre a lista
                    for i, x in enumerate(w):
                        obtido[i] += n * x / (sum(w) or 1.0)
                total = sum(obtido) or 1.0
                fator = [f * a / (o / total) if o else f for f, a, o in zip(fator, alvo, obtido)]
            b["fator"][int(k), j] = tuple(fator)
    suf = {k: n for k, n in est["sufixos"]["M"].items() if k != "nenhum"}
    b["p_sufixo"] = sum(suf.values()) / (sum(suf.values()) + est["sufixos"]["M"]["nenhum"])
    b["sufixos"] = (list(suf), list(suf.values()))
    # acento: num nome, as palavras andam juntas (``_acentuar_nome``). Com o sorteio comum de peso rho, um par de
    # palavras acentuáveis sai todo acentuado com chance A + rho²·(B − A), A = E[p1·p2] e B = E[min(p1, p2)] (p: a
    # fração de cada palavra); o par é um prenome e um sobrenome acentuáveis pelo peso, e rho faz a chance bater com
    # a dos nomes do TSE com tudo acentuado (acento.estilo)
    sorteio, pares = random.Random(0), []
    for lista, pesos in (listas["nomes_f"], listas["nomes_m"], listas["sobrenomes"]):
        ac = [(acento[k][1], n) for k, n in zip(lista, pesos) if k in acento and acento[k][0] != k]
        pares.append(sorteio.choices([p for p, _ in ac], [n for _, n in ac], k=3000))
    pares = list(zip(pares[0] + pares[1], pares[2] + pares[2]))
    a_, b_ = (sum(p * q for p, q in pares) / len(pares), sum(min(p, q) for p, q in pares) / len(pares))
    todas = est["acento"]["estilo"]["todas"] / sum(est["acento"]["estilo"].values())
    b["rho"] = math.sqrt(min(1.0, max(0.0, (todas - a_) / (b_ - a_)))) if b_ > a_ else 0.0
    return b


def _nomes_base():
    """A base de nomes (prenomes.tsv.gz, sobrenomes.tsv.gz, nomes_estrutura.json, acentos.tsv.gz); None sem ela."""
    return _base("nomes", _ler_nomes)


def _ler_lugares():
    """Municípios (municipios.tsv) e as faixas reais de CEP de cada um (ceps_municipio.tsv.gz); None sem eles."""
    mun = _tsv("municipios.tsv")
    if not mun:
        return None
    L = {"mun": {}, "por_nome": {}, "por_norm": {}, "juntas": {}, "pop": {}, "norm": {}, "ceps": {}, "dono": {}}
    for cod, nome, uf, pop, ddd, capital, porte, _e, _d, unico, geral, *_ in mun:
        nome_m, k, pop = nome.upper(), _normalizar(nome), int(pop or 0)  # "São Paulo" → "SÃO PAULO"
        L["mun"][cod] = (nome_m, uf, pop, ddd, capital == "1", porte, unico == "1", geral)
        L["por_nome"][(k, uf)] = cod
        L["por_norm"].setdefault(k, []).append(cod)
        L["juntas"][(re.sub(r"[^A-Z]", "", k), uf)] = k  # "ALTA FLORESTA D'OESTE" = "ALTA FLORESTA D OESTE"
        L["pop"][(nome_m, uf)], L["norm"][(nome_m, uf)] = pop, k
    for cod, prefixo, n, _nc, cmin, cmax, _f in _tsv("ceps_municipio.tsv.gz") or ():
        L["ceps"].setdefault(cod, []).append((prefixo, int(n), cmin, cmax))
        L["dono"].setdefault(prefixo, []).append(cod)
    L["prefixos"] = sorted(L["dono"])
    return L


def _lugares():
    """Municípios e CEPs da base de lugares (``_ler_lugares``); None sem ela."""
    return _base("lugares", _ler_lugares)


def _ler_por_cidade(nome: str):
    """O texto de um .tsv.gz da base de lugares (ordenado por município) e onde começa e acaba cada município."""
    L, caminho = _lugares(), _VOCABULARIO / nome
    if L is None or not caminho.is_file():
        return None
    with gzip.open(caminho, "rt", encoding="utf-8") as f:
        texto = f.read()
    inicios, pos = [], 0
    for cod in sorted(L["mun"]):
        i = texto.find("\n" + cod + "\t", pos)
        if i >= 0:
            inicios.append((cod, i + 1))
            pos = i + 1
    fins = [i for _, i in inicios[1:]] + [len(texto)]
    return {"texto": texto, "idx": {cod: (i, f) for (cod, i), f in zip(inicios, fins)}, "cache": OrderedDict()}


def _da_cidade(base: dict, cod: str, montar):
    """As linhas do município ``cod`` preparadas por ``montar(linhas)`` (None sem linhas), num cache dos últimos."""
    cache = base["cache"]
    with _BASES_LOCK:
        if cod in cache:
            cache.move_to_end(cod)
            return cache[cod]
    ini, fim = base["idx"].get(cod, (0, 0))
    v = montar([linha.split("\t") for linha in base["texto"][ini:fim].split("\n") if linha]) if fim > ini else None
    with _BASES_LOCK:
        cache[cod] = v
        while len(cache) > _CIDADES_EM_CACHE:
            cache.popitem(last=False)
    return v


def _ruas(cod: str | None):
    """As ruas de verdade do município (logradouros.tsv.gz): {"linhas": [(tipo, título, nome, bairro, CEP, urbano)],
    "nome" e "bairro": os de cada linha sem acento e pontuação (o bairro por ``_chave_bairro``), "fatia": o split de cada linha (pelo
    hash da rua: município, tipo, título e nome; os trechos da mesma rua caem juntos), "cands": as candidatas já
    filtradas}; None sem a base."""
    base = _base("ruas", lambda: _ler_por_cidade("logradouros.tsv.gz"))
    if base is None or cod is None:
        return None

    def montar(linhas):
        rs = [(sys.intern(t), sys.intern(ti), n, sys.intern(b), c, sys.intern(u)) for _, t, ti, n, b, c, u, *_ in linhas]
        nomes = [re.sub(r"[^A-Z0-9 ]", " ", _normalizar(r[2])) for r in rs]
        return {"linhas": rs, "nome": [r[2] if n == r[2] else n for r, n in zip(rs, nomes)],
                "bairro": [sys.intern(_chave_bairro(r[3])) for r in rs],
                "fatia": [_fatia(f"{cod} {r[0]} {r[1]} {r[2]}") for r in rs], "cands": {}}
    return _da_cidade(base, cod, montar)


def _bairros(cod: str | None):
    """Os bairros do município (bairros.tsv.gz): {"nomes", "urbano", "dom" (domicílios), "chaves" (sem acento, com o
    bairro oficial do IBGE), "tabela" (o sorteio pelos domicílios, preparado na primeira vez)}; None sem a base."""
    base = _base("bairros", lambda: _ler_por_cidade("bairros.tsv.gz"))
    if base is None or cod is None:
        return None

    def montar(linhas):
        return {"nomes": [ln[1] for ln in linhas], "urbano": [ln[2] == "1" for ln in linhas],
                "dom": [int(ln[4] or 0) for ln in linhas],
                "chaves": {_chave_bairro(x) for ln in linhas for x in (ln[1], ln[7]) if x}}
    return _da_cidade(base, cod, montar)


def _estatisticas():
    """enderecos_estatisticas.json (tipos, números e complementos de endereço do CNEFE); None sem ele."""
    def ler():
        caminho = _VOCABULARIO / "enderecos_estatisticas.json"
        return json.loads(caminho.read_text("utf-8")) if caminho.is_file() else None
    return _base("estatisticas", ler)


def _vocab() -> dict:
    """O vocabulário em uso, montado na primeira necessidade: prenomes, 2ºs prenomes, sobrenomes e cidades da base
    oficial (com o peso real), ou os embutidos sem ela; ruas e bairros genéricos são sempre os embutidos (a rua de
    verdade sai da cidade: ``_ruas``). {"listas", "pesos", "fatias" (o split de cada entrada; None = núcleo),
    "nucleo", "partes" (a cauda de cada lista por split), "vocab", "femininos", "masculinos", "todas" (as palavras
    de alguma lista), "sorteio" (as tabelas já montadas)}."""
    return _base("vocab", _montar_vocab)


def _montar_vocab() -> dict:
    b, L = _nomes_base(), _lugares()
    vocab = dict(_EMBUTIDO)
    listas = {"nomes_f": _PRENOMES_F if vocab["nomes"] is _NOMES else vocab["nomes"],
              "nomes_m": _PRENOMES_M if vocab["nomes"] is _NOMES else vocab["nomes"],
              "meios_f": _MEIOS_F, "meios_m": _MEIOS_M,
              **{k: vocab[k] for k in ("sobrenomes", "ruas", "bairros", "bairros_cidade", "cidades")}}
    tam, pesos = dict(_NUCLEO_TAM), {}
    if b is not None:
        for k in ("nomes_f", "nomes_m", "meios_f", "meios_m", "sobrenomes"):
            listas[k], pesos[k] = b["listas"][k]
        vocab["nomes"] = list(dict.fromkeys(listas["nomes_f"] + listas["nomes_m"]))
        vocab["meios"] = list(dict.fromkeys(listas["meios_f"] + listas["meios_m"]))
        vocab["sobrenomes"] = listas["sobrenomes"]
        tam.update({k: n for k, n in _NUCLEO_TAM_BASE.items() if k != "cidades"})
    capitais = set()
    if L is not None:  # cidades pela população (Boa Esperança do Norte, sem dado, fica de fora); as capitais são núcleo
        cidades = sorted(((m[0], m[1]) for m in L["mun"].values() if m[2] > 0), key=lambda c: -L["pop"][c])
        listas["cidades"] = vocab["cidades"] = cidades
        pesos["cidades"] = [L["pop"][c] for c in cidades]
        tam["cidades"] = _NUCLEO_TAM_BASE["cidades"]
        capitais = {_normalizar(m[0]) for m in L["mun"].values() if m[4]}
    nucleo = {_chave(x) for k, n in tam.items() for x in listas[k][:n]} | _ESTRUTURAIS | capitais
    for k, lista in listas.items():
        if k not in pesos:
            pesos[k] = [1 / (i + 2) if k in _ZIPF else _PESO_NUCLEO.get(k, 1.0) if _chave(x) in nucleo else 1.0
                        for i, x in enumerate(lista)]
    memo = {}

    def chave(x):  # a da base já é a chave (sem acento): sem cópia
        k = _chave(x)
        return x if k == x else k

    def fatia(x):  # None = núcleo
        k = chave(x)
        if k in nucleo:
            return None
        if k not in memo:
            memo[k] = _fatia(k)
        return memo[k]
    fatias = {k: [fatia(x) for x in lista] for k, lista in listas.items()}
    partes = {k: {sp: [x for x in v if fatia(x) == sp] for sp in _SPLITS} for k, v in vocab.items()}
    return {"listas": listas, "pesos": pesos, "fatias": fatias, "nucleo": frozenset(nucleo), "partes": partes,
            "vocab": vocab, "femininos": frozenset(map(chave, (*listas["nomes_f"], *listas["meios_f"]))),
            "masculinos": frozenset(map(chave, (*listas["nomes_m"], *listas["meios_m"]))),
            "todas": frozenset(memo) | nucleo, "sorteio": {}}


# os nomes de antes (testes, tecnicas.py): o vocabulário só é montado quando alguém pede
_ANTIGOS = {"_VOCAB": "vocab", "_LISTAS": "listas", "_NUCLEO": "nucleo", "_PARTES": "partes", "_FEMININOS": "femininos",
            "_MASCULINOS": "masculinos"}


def __getattr__(nome: str):
    if nome in _ANTIGOS:
        return _vocab()[_ANTIGOS[nome]]
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")


def _tabela(nome: str, split: str | None):
    """(entradas, pesos acumulados) do sorteio: núcleo + a cauda do split. Os prenomes, sobrenomes e cidades da base
    pesam pela frequência real; os embutidos, pela posição (Zipf: o 1º pesa 1/2, o 10º 1/11), e nas ruas o núcleo
    pesa 4; a cauda do split é reescalada para ter a massa da cauda inteira, então núcleo e cauda pesam o mesmo em
    todo split (o teste não sai mais "comum" que o treino). Nos nomes de pessoa, a entrada de duas palavras (VILA
    NOVA, SÃO JOSÉ: sobrenomes; o núcleo dos bairros também os tem) só entra no split em que as duas palavras também
    podem sair. Montada uma vez por lista e split."""
    v = _vocab()
    split = split if split in _SPLITS else None
    t = v["sorteio"].get((nome, split))
    if t is None:
        lista, pesos, fatias = v["listas"][nome], v["pesos"][nome], v["fatias"][nome]
        de_pessoa = split is not None and nome in _LISTAS_DE_NOME

        def pode(i):  # as palavras da entrada de nome de pessoa (VILA NOVA) também podem sair no split
            return not de_pessoa or " " not in lista[i] or all(_da_fatia(w, split) for w in lista[i].split())
        nucleo = [i for i, f in enumerate(fatias) if f is None and pode(i)]
        cauda = [i for i, f in enumerate(fatias) if f is not None]
        da_fatia = cauda if split is None else [i for i in cauda if fatias[i] == split and pode(i)]
        escala = sum(pesos[i] for i in cauda) / (sum(pesos[i] for i in da_fatia) or 1)
        idx = nucleo + da_fatia
        t = v["sorteio"].setdefault((nome, split), ([lista[i] for i in idx], array("d", itertools.accumulate(
            [pesos[i] for i in nucleo] + [pesos[i] * escala for i in da_fatia]))))
    return t


def _sortear(nome: str):
    """Uma entrada da lista ``nome`` pelo peso, do núcleo ou da cauda do split da vez."""
    entradas, acumulado = _tabela(nome, _split_atual.get())
    return _rng.choices(entradas, cum_weights=acumulado)[0]


def _lista(nome: str):
    """As entradas que o split da vez usa (``usando(split=...)``): núcleo + a sua cauda; fora de um split, a lista
    inteira."""
    return _tabela(nome, _split_atual.get())[0]


def _da_fatia(palavra: str, split: str | None) -> bool:
    """A palavra pode sair no ``split``? Do núcleo, de fora de todas as listas ou da cauda dele, sim ("JOÃO BATISTA":
    BATISTA é sobrenome da cauda e só vale no split dele)."""
    if split not in _SPLITS:
        return True
    v, k = _vocab(), _normalizar(palavra)
    return k in v["nucleo"] or k not in v["todas"] or _fatia(k) == split


def _na_fatia(sortear):
    """Repete ``sortear()`` até o valor INTEIRO cair no split da vez pelo hash (o nome completo "MARIA DA SILVA" é
    de um split só, mesmo feito só de palavras do núcleo); fora de um split, o primeiro."""
    split = _split_atual.get()
    for _ in range(100):
        valor = sortear()
        if split not in _SPLITS or _fatia(valor) == split:
            return valor
    return valor


def _digitos(n: int) -> str:
    return "".join(str(_rng.randrange(10)) for _ in range(n))


def palavra(tipo: str, antigo: str | None = None) -> str:
    """Uma palavra solta para trocar só um trecho: prenome, sobrenome, rua, bairro, cidade, mes. ``antigo``: a
    palavra que sai (o prenome novo fica do gênero dela). Rua e bairro são de verdade, da cidade da versão."""
    if tipo == "prenome":
        v, k = _vocab(), _normalizar(antigo or "")
        f, m = k in v["femininos"], k in v["masculinos"]
        genero = "f" if f and not m else "m" if m and not f else _rng.choice("fm")
        return _grafia(_acentuar_nome(_sortear("nomes_" + genero)))
    if tipo == "sobrenome":
        return _grafia(_acentuar_nome(_sortear("sobrenomes")))
    if tipo == "rua":
        linha = _rua_qualquer(_cod_da_versao())
        return _grafia_lugar(" ".join(filter(None, linha[1:3]))) if linha else _grafia(_sortear("ruas"))
    if tipo == "bairro":
        return _grafia_lugar(_bairro_da_cidade(_cod_da_versao()) or _sortear("bairros"))
    if tipo == "cidade":
        return _grafia(_sortear("cidades")[0])
    if tipo == "mes":
        return _rng.choice(_MESES)
    if tipo == "uf":
        return _sortear("cidades")[1]
    raise ValueError(f"tipo de palavra desconhecido: {tipo}")


# região fiscal do CPF (o 9º dígito): onde ele foi emitido
_REGIAO_CPF = {"RS": 0, "DF": 1, "GO": 1, "MS": 1, "MT": 1, "TO": 1, "AC": 2, "AM": 2, "AP": 2, "PA": 2, "RO": 2,
               "RR": 2, "CE": 3, "MA": 3, "PI": 3, "AL": 4, "PB": 4, "PE": 4, "RN": 4, "BA": 5, "SE": 5, "MG": 6,
               "ES": 7, "RJ": 7, "SP": 8, "PR": 9, "SC": 9}


def cpf(regiao: int | None = None) -> str:
    """CPF válido. O 9º dígito é a região fiscal em que ele foi emitido (8 = SP, 3 = CE/MA/PI): ``regiao``, senão a
    da UF de ``usando(cidade=...)`` em 80% das vezes (quem mora na cidade quase sempre tirou o CPF no estado)."""
    base = [_rng.randrange(10) for _ in range(9)]
    if regiao is None:
        uf = _normalizar((cidade_atual() or (None, None))[1] or "")
        if uf in _REGIAO_CPF and _rng.random() < 0.8:
            regiao = _REGIAO_CPF[uf]
    if regiao is not None:
        base[8] = regiao
    if len(set(base)) == 1:
        base[0] = (base[0] + 1) % 10
    for n in (10, 11):
        s = sum(d * p for d, p in zip(base, range(n, 1, -1)))
        base.append((s * 10 % 11) % 10)
    d = "".join(map(str, base))
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def cnpj(filial: int | None = None) -> str:
    """CNPJ válido; ``filial``: a ordem do estabelecimento (/0001 é a matriz); None = quase sempre a matriz."""
    if filial is None:
        filial = 1 if _rng.random() < 0.95 else _rng.randint(2, 20)
    base = [_rng.randrange(10) for _ in range(8)] + [int(c) for c in f"{filial:04d}"]
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


# ---------------------------------------------------------------------------
# cidade e CEP: o município do texto e um CEP que ele usa
# ---------------------------------------------------------------------------

_RE_CIDADE_UF = re.compile(r"([A-Z][A-Z ]*?)\s*(?:[/,\-]\s*)?\b(" + "|".join(FAIXAS_UF) + r")\b")
_RE_UF = re.compile(r"\b(" + "|".join(FAIXAS_UF) + r")\b")
# cidades conhecidas pelo nome só com as letras e a UF: "CURI T I BA" do OCR junta em "CURITIBA"; com a base de
# lugares, os 5.570 municípios (``_lugares()["juntas"]``)
_CIDADE_JUNTA = {(re.sub(r"[^A-Z]", "", _normalizar(c)), uf): _normalizar(c) for c, uf in _CIDADES + _EMBUTIDO["cidades"]}


def cidade_do_texto(texto: str) -> tuple[str, str] | None:
    """("CURITIBA", "PR") de "Curitiba/PR", "CURITIBA - PR", "Curitiba, PR" ou "Curitiba PR" (acento e caixa
    não importam); None se não há uma sigla de UF válida. Uma cidade conhecida (com a base de lugares, qualquer um
    dos 5.570 municípios; o homônimo pela UF: BOM JESUS - PI, BOM JESUS - RS) vale mais que a primeira sigla:
    no OCR "CURI T I BA / PR" o "BA" do meio não é a UF."""
    t = _normalizar(texto)
    L = _lugares()
    for u in _RE_UF.finditer(t):
        antes = re.search(r"[A-Z][A-Z '\-]*$", re.sub(r"[\s/,\-]+$", "", t[:u.start()]))
        palavras = [p for p in re.split(r"[\s\-]+", antes.group()) if p] if antes else []
        for i in range(len(palavras)):  # "CTCE SALVADOR BA": a cidade é o fim do trecho
            junta = (re.sub(r"[^A-Z]", "", "".join(palavras[i:])), u.group(1))
            nome = (L["juntas"].get(junta) if L else None) or _CIDADE_JUNTA.get(junta)
            if nome:
                return nome, u.group(1)
    so = re.sub(r"[^A-Z]", "", t)  # "C U RITIBAPR": o OCR perdeu a barra e a UF ficou grudada na cidade
    junta = (so[:-2], so[-2:])
    nome = (L["juntas"].get(junta) if L else None) or _CIDADE_JUNTA.get(junta) if len(so) > 4 else None
    if nome:
        return nome, junta[1]
    m = _RE_CIDADE_UF.search(t)
    return (m.group(1).strip(), m.group(2)) if m else None


def _municipio(cidade) -> str | None:
    """O código IBGE do município (nome, UF) na base de lugares (acento, caixa e espaço do OCR não importam; sem a
    UF, o mais populoso dos homônimos); None se não o conhece ou sem a base."""
    L = _lugares()
    if L is None or not cidade or not cidade[0]:
        return None
    k, uf = _normalizar(cidade[0]), _normalizar((cidade[1] if len(cidade) > 1 else None) or "")
    cod = L["por_nome"].get((k, uf))
    if cod is None and uf:
        junta = L["juntas"].get((re.sub(r"[^A-Z]", "", k), uf))
        cod = L["por_nome"].get((junta, uf)) if junta else None
    if cod is None and not uf and k in L["por_norm"]:
        cod = max(L["por_norm"][k], key=lambda c: L["mun"][c][2])
    return cod


def _cidade_da_uf(uf: str) -> str | None:
    """Um município da UF, pela população, entre as cidades do split da vez."""
    L = _lugares()
    cidades = [c for c in _lista("cidades") if c[1] == uf]
    return _municipio(_rng.choices(cidades, [L["pop"].get(c, 1) for c in cidades])[0]) if cidades else None


def _dono_do_cep(cep: str) -> str | None:
    """O município de um CEP: o que usa o prefixo de 5 dígitos (prefixo que ninguém usa: o do prefixo mais perto);
    None sem a base."""
    L, d = _lugares(), re.sub(r"\D", "", cep or "")[:5]
    if L is None or len(d) < 5 or not L["prefixos"]:
        return None
    if d not in L["dono"]:
        i = bisect.bisect_left(L["prefixos"], d)
        d = min(L["prefixos"][max(0, i - 1):i + 1], key=lambda p: abs(int(p) - int(d)))
    return max(L["dono"][d], key=lambda c: L["mun"][c][2])


def cep_na_cidade(cep: str, cidade) -> bool | None:
    """O CEP é do município (nome, UF)? Pela base de lugares: o prefixo de 5 dígitos é um dos que a cidade usa e, na
    cidade de CEP único, o CEP é de localidade (final 000: o geral ou o de um distrito). None se a base não conhece a
    cidade."""
    cod = _municipio(cidade)
    if cod is None:
        return None
    L, d = _lugares(), re.sub(r"\D", "", cep or "")
    if len(d) != 8 or d[:5] not in {p[0] for p in L["ceps"].get(cod, ())}:
        return False
    return d.endswith("000") or not L["mun"][cod][6]


def _cep_tipico(cep: str, unico: bool) -> bool:
    """CEP de endereço residencial: sem os finais 899 (zona rural) e 9xx (agência, caixa postal, grande usuário); na
    cidade de CEP único, só o de localidade (000)."""
    return cep.endswith("000") if unico else not (cep.endswith("899") or cep[5:6] == "9")


def _cep_da_cidade(cod: str, evitar: str | None = None, bairro: str | None = None, rua: str | None = None,
                   letras: int | None = None):
    """Um CEP que o município usa: o de uma rua de verdade dele (do split da vez, urbana, típica; na rua ``rua`` ou
    no ``bairro`` (``_chave_bairro``), quando dá; com ``letras``, uma rua de nome com esse comprimento, que sirva
    para a rua trocada depois) ou, sem rua, da faixa real (o geral na cidade de CEP único; senão um prefixo pelo nº
    de endereços e o final dentro do que se viu nele). (CEP de 8 dígitos, a linha da rua ou None); ``evitar``: o CEP
    que sai."""
    e = _ruas(cod)
    if e is not None:
        linhas = e["linhas"]
        pool = [i for i in _candidatas(cod, e, "cep") if linhas[i][4] != evitar]
        palavras = _palavras_rua(rua) if rua else None
        de_nome = set(_candidatas(cod, e, "extenso")) if letras else ()
        for sub in ([i for i in pool if palavras and _mesma_rua(palavras, linhas[i], e["nome"][i])],
                    [i for i in pool if bairro and e["bairro"][i] == bairro], [i for i in pool if i in de_nome], pool):
            if sub:
                amostra = _rng.sample(sub, min(48, len(sub)))
                i = min(amostra, key=lambda i: abs(len(" ".join(filter(None, linhas[i][:3]))) - letras)) if letras \
                    else amostra[0]
                return linhas[i][4], linhas[i]
    L = _lugares()
    m = L["mun"][cod]
    faixas = L["ceps"].get(cod) or [(m[7][:5] or "00000", 1, m[7] or "00000000", m[7] or "00000000")]
    if m[6]:  # CEP único: o geral ou o de um distrito
        geral = [f"{f[0]}000" for f in faixas if f[2].endswith("000") and f"{f[0]}000" != evitar]
        if geral:
            return (m[7] if m[7] in geral else _rng.choice(geral)), None
    cep = evitar
    for _ in range(20):
        p, _n, cmin, cmax = _rng.choices(faixas, [f[1] for f in faixas])[0]
        lo, hi = int(cmin[5:] or 0), int(cmax[5:] or 0)
        cep = f"{p}{_rng.randint(lo, max(lo, min(hi, 899))):03d}"
        if cep != evitar:
            break
    return cep, None


def _faixa_cep(cidade: str | None, uf: str | None) -> tuple[int, int] | None:
    """Faixa da cidade (se conhecida e coerente com a UF), senão da UF; None sem nenhuma das duas (as faixas escritas
    à mão: só sem a base de lugares)."""
    da_uf = FAIXAS_UF.get(_normalizar(uf or ""))
    da_cidade = FAIXAS_CEP.get(_normalizar(cidade or ""))
    if da_cidade and da_uf and not (da_uf[0] <= da_cidade[0] and da_cidade[1] <= da_uf[1]):
        da_cidade = None  # homônima de outra UF
    return da_cidade or da_uf


def _faixa_do_cep(cep: str) -> tuple[int, int] | None:
    """A faixa (da cidade, senão da UF) em que ``cep`` cai; None se não cai em nenhuma."""
    n = int(re.sub(r"\D", "", cep) or 0)
    for tabela in (FAIXAS_CEP, FAIXAS_UF):
        for ini, fim in tabela.values():
            if ini <= n <= fim:
                return ini, fim
    return None


def _sortear_cep(faixa: tuple[int, int]) -> str:
    """CEP de logradouro na faixa: o sufixo fica abaixo de 900 (900–999 são de caixa postal, grandes usuários e
    unidades dos Correios, não de rua)."""
    n = _rng.randint(*faixa)
    if n % 1000 >= 900:
        n = max(faixa[0], n - n % 1000 + _rng.randrange(900))
    return f"{n // 1000:05d}-{n % 1000:03d}"


def cep_de(cidade: str | None = None, uf: str | None = None) -> str:
    """CEP (00000-000) que a cidade usa (o de uma rua de verdade dela, pela base de lugares); cidade desconhecida, o
    de uma cidade da UF (pela população); sem cidade nem UF, de uma cidade do split da vez. Sem a base, na faixa dos
    Correios da cidade (ou da UF)."""
    if _lugares() is None:
        return _sortear_cep(_faixa_cep(cidade, uf) or _faixa_cep(*_rng.choice(_lista("cidades"))))
    cod = _municipio((cidade, uf)) if cidade else None
    if cod is None and _normalizar(uf or "") in FAIXAS_UF:
        cod = _cidade_da_uf(_normalizar(uf))
    cep = _cep_da_cidade(cod or _municipio(_sortear("cidades")))[0]
    return f"{cep[:5]}-{cep[5:]}"


# ---------------------------------------------------------------------------
# calendário: feriados nacionais e dia útil
# ---------------------------------------------------------------------------

def _pascoa(ano: int) -> dt.date:
    """Domingo de Páscoa (algoritmo gregoriano anônimo de Meeus/Jones/Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = divmod(b, 4)
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    mes, dia = divmod(h + el - 7 * m + 114, 31)
    return dt.date(ano, mes, dia + 1)


@functools.lru_cache(maxsize=None)
def feriados(ano: int) -> frozenset:
    """Os feriados nacionais do ano, sem expediente bancário: os fixos (Confraternização, Tiradentes, Trabalho,
    Independência, Aparecida, Finados, República, Natal e, desde 2024, Consciência Negra) e os móveis (Carnaval na
    segunda e na terça, Sexta-feira Santa e Corpus Christi)."""
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]
    if ano >= 2024:
        fixos.append((11, 20))
    p = _pascoa(ano)
    return frozenset([dt.date(ano, m, d) for m, d in fixos] + [p + dt.timedelta(days=n) for n in (-48, -47, -2, 60)])


def dia_util(d: dt.date) -> bool:
    """Segunda a sexta, fora dos feriados nacionais."""
    return d.weekday() < 5 and d not in feriados(d.year)


def proximo_dia_util(d: dt.date) -> dt.date:
    """``d`` ou, se não é dia útil, o primeiro dia útil depois (o vencimento no sábado vai para a segunda)."""
    while not dia_util(d):
        d += dt.timedelta(days=1)
    return d


def _dia_de_leitura(d: dt.date) -> bool:
    """O leiturista trabalha de segunda a sábado, fora dos feriados."""
    return d.weekday() < 6 and d not in feriados(d.year)


# ---------------------------------------------------------------------------
# datas da conta: um ciclo (vencimento, emissão, leitura, referência) perto do hoje do documento
# ---------------------------------------------------------------------------

# dias de vencimento que as concessionárias oferecem (o cliente escolhe um): 5, 10, 15, 20 e 25 dominam
_PESO_DIA_VENCIMENTO = {5: 14, 10: 16, 15: 14, 20: 12, 25: 10, 7: 6, 12: 5, 8: 3, 18: 3, 22: 3, 28: 3, 3: 2, 13: 2,
                        17: 2, 27: 2}
_DIAS_VENCIMENTO = (tuple(range(1, 29)), tuple(_PESO_DIA_VENCIMENTO.get(d, 1) for d in range(1, 29)))
_PAPEIS = ("vencimento", "emissao", "leitura", "proxima_leitura", "leitura_anterior", "pagamento")


def _meses(d: dt.date) -> int:
    return d.year * 12 + d.month - 1


def _vencimento_novo(anterior: dt.date | None = None, futuro: bool = True) -> dt.date:
    """Vencimento perto de ``hoje()``: em volta de um dia-alvo (70% nos últimos 85 dias, a conta "recente" da regra
    dos 90 dias; 18% de 3 a 13 meses atrás; com ``futuro``, 12% nos próximos 10 dias: a conta que ainda não venceu),
    no dia fixo do cliente (o de ``anterior`` em 70% das vezes; senão um dos dias que as concessionárias oferecem),
    empurrado para o próximo dia útil e nunca igual a ``anterior``."""
    h = hoje()
    faixas = (((-10, -1), 12), ((0, 85), 70), ((86, 400), 18))[0 if futuro else 1:]
    lo, hi = _rng.choices(*zip(*faixas))[0]
    alvo = h - dt.timedelta(days=_rng.randint(lo, hi))
    dia = anterior.day if anterior and _rng.random() < 0.7 else _rng.choices(*_DIAS_VENCIMENTO)[0]
    limite = h + dt.timedelta(days=10 if futuro else 0)
    cands = []
    for k in (-2, -1, 0, 1):  # o dia fixo nos meses em volta do alvo: fica o mais perto dele
        ano, mes = divmod(_meses(alvo) + k, 12)
        d = proximo_dia_util(dt.date(ano, mes + 1, min(dia, calendar.monthrange(ano, mes + 1)[1])))
        if d <= limite and d != anterior:
            cands.append(d)
    return min(cands, key=lambda d: abs((d - alvo).days))


def _dia_entre(ref: dt.date, lo: int, hi: int, valido, fixo: int | None = None, ate: dt.date | None = None) -> dt.date:
    """Um dia de ``ref + lo`` a ``ref + hi`` (dias) que passe em ``valido`` (dia útil, dia de leitura) e não passe de
    ``ate``; com ``fixo`` (a distância no documento original), o dia válido mais perto dela."""
    ns = list(range(lo, hi + 1))
    _rng.shuffle(ns)
    if fixo is not None:
        ns = [n for n in sorted(range(fixo - 2, fixo + 3), key=lambda n: abs(n - fixo))
              if min(lo, fixo) <= n <= max(hi, fixo)] + ns
    for n in ns:
        d = ref + dt.timedelta(days=n)
        if (ate is None or d <= ate) and valido(d):
            return d
    d = ref + dt.timedelta(days=lo)
    return min(d, ate) if ate else d


def ciclo_de_datas(vencimento: dt.date | None = None, base: dict | None = None) -> dict:
    """As datas de UMA conta, coerentes entre si e perto de ``hoje()``: o vencimento (``vencimento``, senão sorteado em
    dia útil no dia fixo do cliente), a emissão de 3 a 15 dias antes (dia útil), a leitura de 3 a 10 dias antes da
    emissão e a próxima de 28 a 33 dias depois (de segunda a sábado, fora dos feriados), a leitura anterior de 28 a 33
    dias antes, o pagamento até 5 dias antes do vencimento (emissão, leitura e pagamento nunca depois de hoje) e a
    referência (dia 1º do mês) = o mês da leitura (70%) ou o anterior. ``base``: as datas do documento original por
    papel (e "referencia"): o vencimento novo fica no dia do antigo (o dia que o cliente escolheu) e as distâncias entre
    as datas e a referência repetem as do original quando plausíveis (quem emite no dia da leitura continua emitindo no
    dia da leitura). "deslocamento": vencimento novo − antigo (None sem o antigo)."""
    base = base or {}
    h = hoje()
    venc = vencimento or _vencimento_novo(base.get("vencimento"))

    def no_original(de, ate, lo, hi):  # dias de ``de`` até ``ate`` no original, se estão na faixa plausível
        if de in base and ate in base and lo <= (base[ate] - base[de]).days <= hi:
            return (base[ate] - base[de]).days
        return None

    em = _dia_entre(venc, -15, -3, dia_util, no_original("vencimento", "emissao", -30, 0), ate=h)
    le = _dia_entre(em, -10, -3, _dia_de_leitura, no_original("emissao", "leitura", -20, 0), ate=h)
    pr = _dia_entre(le, 28, 33, _dia_de_leitura, no_original("leitura", "proxima_leitura", 25, 40))
    an = _dia_entre(le, -33, -28, _dia_de_leitura, no_original("leitura", "leitura_anterior", -40, -25))
    pg = _dia_entre(venc, -5, 0, dia_util, ate=h)
    papel, atras = "leitura", 0 if _rng.random() < 0.7 else 1
    if "referencia" in base:  # quantos meses a referência fica antes da leitura (ou do vencimento) no original
        for p in ("leitura", "vencimento"):
            if p in base and 0 <= _meses(base[p]) - _meses(base["referencia"]) <= (1 if p == "leitura" else 2):
                papel, atras = p, _meses(base[p]) - _meses(base["referencia"])
                break
    n = min(_meses(venc), max(_meses(venc) - 2, _meses(le if papel == "leitura" else venc) - atras))
    return {"vencimento": venc, "emissao": em, "leitura": le, "proxima_leitura": pr, "leitura_anterior": an,
            "pagamento": pg, "referencia": dt.date(n // 12, n % 12 + 1, 1),
            "deslocamento": venc - base["vencimento"] if "vencimento" in base else None}


def _papel_da_data(texto: str, rotulo: str | None = None) -> str:
    """vencimento, emissao, leitura, leitura_anterior, proxima_leitura, pagamento, nascimento ou outra: pelo ``rotulo``,
    senão pelo rótulo do campo de mesmo texto em ``usando(documento=...)``, senão pelo próprio texto ("Venc: ...")."""
    from ..coerencia import tipo_da_data

    doc = _documento_atual.get()
    if not rotulo and doc is not None:
        t = " ".join(texto.split())
        rotulo = next((str(f.get("rotulo") or "") for f in doc["campos"] if f.get("categoria") == "data"
                       and " ".join(str(f.get("texto", "")).split()) == t), "")
    for r in (rotulo or "", texto):
        papel = tipo_da_data(r)
        if papel != "outra":
            return "leitura_anterior" if papel == "leitura" and re.search(r"\bANT", _normalizar(r)) else papel
    return "outra"


def _datas_do_documento() -> dict:
    """As datas impressas no documento de ``usando(documento=...)``, por papel (e o mês de referência, no dia 1º)."""
    doc = _documento_atual.get()
    if doc is None:
        return {}
    if "datas" not in doc:
        datas, outras = {}, []
        for f in doc["campos"]:
            texto = str(f.get("texto", ""))
            if f.get("categoria") == "data" and ler_data(texto):
                papel = _papel_da_data(texto, str(f.get("rotulo") or ""))
                if papel == "outra":
                    outras.append(ler_data(texto))
                datas.setdefault(papel, ler_data(texto))
            elif f.get("categoria") == "mês de referência" and ler_mes(texto):
                # o mais recente: os outros meses são o histórico de consumo (Set, Out, Nov... da mesma conta)
                datas["referencia"] = max(datas.get("referencia", dt.date.min), dt.date(*ler_mes(texto), 1))
        if "vencimento" not in datas and outras:
            # sem rótulo legível: a data mais recente faz as vezes do vencimento, e as outras andam o mesmo tanto que
            # ele (as distâncias entre as datas ficam as do original)
            datas["vencimento"] = max(outras)
        doc["datas"] = {k: v for k, v in datas.items() if k in (*_PAPEIS, "referencia")}
    return dict(doc["datas"])


def _ciclo_da_versao(papel: str | None = None, d0: dt.date | None = None) -> dict:
    """O ciclo de datas novo: um só por versão com ``usando(documento=...)`` (as datas trocadas ficam coerentes entre
    si), a partir das datas do documento e da antiga (``d0``, no ``papel`` dela)."""
    doc = _documento_atual.get()
    if doc is not None and doc["ciclo"]:
        return doc["ciclo"]
    base = _datas_do_documento()
    if d0 and papel in _PAPEIS:
        base.setdefault(papel, d0)
    ciclo = ciclo_de_datas(base=base)
    if doc is not None:
        doc["ciclo"] = ciclo
    return ciclo


def _data_nova(papel: str, d0: dt.date | None) -> dt.date:
    """A data nova no ``papel``: a do ciclo; a de nascimento, de um adulto (18 a 90 anos, mais perto dos 40); outra
    data qualquer, a antiga deslocada como o vencimento (ou num mês recente, no mesmo dia, em dia útil)."""
    if papel == "nascimento":
        return hoje() - dt.timedelta(days=round(_rng.triangular(18, 90, 38) * 365.25))
    ciclo = _ciclo_da_versao(papel, d0)
    if papel in ciclo:
        return ciclo[papel]
    if d0 and ciclo["deslocamento"] is not None:
        return min(proximo_dia_util(d0 + ciclo["deslocamento"]), hoje())
    return _vencimento_novo(d0, futuro=False)


# ---------------------------------------------------------------------------
# valores da conta e códigos
# ---------------------------------------------------------------------------

# plano de telefone/internet/TV: o valor de tabela termina em ,90 (às vezes ,99); os do meio são os mais vendidos
_PLANOS = ((4990, 3), (5990, 4), (6990, 5), (7990, 7), (8990, 8), (9990, 10), (10990, 9), (11990, 9), (12990, 8),
           (13990, 7), (14990, 7), (15990, 5), (16990, 4), (17990, 3), (19990, 4), (21990, 2), (24990, 2), (29990, 2),
           (34990, 1))
_TIPOS_CONTA = (("luz", 45), ("agua", 28), ("gas", 5), ("telefone", 22))


def conta(tipo: str | None = None) -> dict:
    """Uma conta de consumo realista, {"tipo", "centavos", "consumo", "unidade"} (sem consumo no telefone): luz =
    consumo residencial log-normal (mediana 165 kWh; 80–600 na maioria) × tarifa com impostos de R$ 0,75 a 1,15/kWh
    + iluminação pública; água = tarifa mínima (os primeiros 10 m³) + faixas progressivas, com esgoto em 3 de 4; gás =
    parte fixa + m³; telefone/internet = o plano (,90/,99), às vezes com um serviço avulso."""
    tipo = tipo or _rng.choices(*zip(*_TIPOS_CONTA))[0]
    if tipo == "luz":
        kwh = min(2000, max(30, round(_rng.lognormvariate(math.log(165), 0.5))))
        reais = kwh * _rng.uniform(0.75, 1.15) + _rng.uniform(4, 45)
        return {"tipo": tipo, "centavos": round(reais * 100), "consumo": kwh, "unidade": "kWh"}
    if tipo == "agua":
        m3 = min(80, round(_rng.lognormvariate(math.log(12), 0.55)))
        reais, ini = _rng.uniform(35, 80), 10  # a tarifa mínima cobre os primeiros 10 m³
        for fim, faixa in ((20, (5.5, 9.0)), (30, (9.0, 15.0)), (50, (13.0, 20.0)), (80, (18.0, 26.0))):
            if m3 > ini:
                reais += (min(m3, fim) - ini) * _rng.uniform(*faixa)
            ini = fim
        if _rng.random() < 0.75:
            reais *= 1 + _rng.uniform(0.8, 1.0)  # esgoto: de 80% a 100% da água
        return {"tipo": tipo, "centavos": round(reais * 100), "consumo": m3, "unidade": "m³"}
    if tipo == "gas":
        m3 = max(1, min(60, round(_rng.lognormvariate(math.log(9), 0.6))))
        reais = _rng.uniform(8, 30) + m3 * _rng.uniform(8, 16)
        return {"tipo": tipo, "centavos": round(reais * 100), "consumo": m3, "unidade": "m³"}
    cent = _rng.choices(*zip(*_PLANOS))[0] + (9 if _rng.random() < 0.15 else 0)
    if _rng.random() < 0.3:  # serviço avulso, parcela de aparelho, juros da conta anterior
        cent += _rng.randint(100, 4000)
    return {"tipo": "telefone", "centavos": cent, "consumo": None, "unidade": None}


def _reais(centavos: int) -> str:
    inteiro, cent = divmod(centavos, 100)
    return f"R$ {inteiro:,}".replace(",", ".") + f",{cent:02d}"


_BANCOS = ("001", "033", "104", "237", "341", "756", "748", "077")


def par_codigo(arrecadacao: bool | None = None, centavos: int | None = None, empresa: str | None = None,
               segmento: str | None = None, banco: str | None = None, ref: str | None = None,
               vencimento: dt.date | None = None) -> tuple[str, str, int]:
    """(44 dígitos do código de barras, linha digitável correspondente, valor em centavos).
    ``arrecadacao``: True = conta de consumo (começa com 8), False = boleto, None = deduz das partes informadas
    ou sorteia. As partes informadas entram no código (valor em centavos, empresa de 4 dígitos, segmento 1-9,
    banco de 3 dígitos, identificação de valor 6-9: ``partes_da_linha`` tira as do original; o ``vencimento`` do
    boleto); as outras são sorteadas."""
    cent = conta()["centavos"] if centavos is None else int(centavos)
    if not 1 <= cent < 10 ** 11:
        raise ValueError(f"valor em centavos fora do intervalo: {centavos!r}")
    for nome, valor, padrao in (("empresa", empresa, r"\d{4}"), ("segmento", segmento, "[1-9]"),
                                ("banco", banco, r"\d{3}"), ("ref", ref, "[6-9]")):
        if valor is not None and not re.fullmatch(padrao, str(valor)):
            raise ValueError(f"{nome} inválido: {valor!r}")
    if arrecadacao is None:
        arrecadacao = banco is None and (empresa is not None or segmento is not None or ref is not None
                                         or _rng.random() < 0.75)
    if arrecadacao:
        cod = codigos.arrecadacao(cent, segmento=segmento or _rng.choice("2343"), empresa=empresa or _digitos(4),
                                  livre=_digitos(25), ref=ref or _rng.choice("6688"))
        return cod, codigos.linha_arrecadacao(cod), cent
    venc = vencimento or hoje() + dt.timedelta(days=_rng.randrange(-60, 60))
    cod = codigos.boleto(banco or _rng.choice(_BANCOS), cent, venc, _digitos(25))
    return cod, codigos.linha_boleto(cod), cent


def partes_da_linha(linha_digitavel: str) -> dict | None:
    """As partes fixas do código impresso no documento (linha digitável ou os 44 dígitos), para o código novo
    repetir empresa, segmento e banco do original: {"arrecadacao": True, "segmento", "ref", "empresa"} ou
    {"arrecadacao": False, "banco"}; None se não reconhece."""
    dig = re.sub(r"\D", "", linha_digitavel or "")
    if dig[:1] == "8" and len(dig) == 48:  # linha de arrecadação: 4 blocos de 11 dígitos + 1 DV cada
        dig = "".join(dig[i:i + 11] for i in range(0, 48, 12))
    if dig[:1] == "8" and len(dig) == 44:  # produto, segmento, ref, DV geral, valor (11), empresa (4), livre (25)
        return {"arrecadacao": True, "segmento": dig[1], "ref": dig[2], "empresa": dig[15:19]}
    if len(dig) in (44, 47):  # boleto: o banco são os 3 primeiros dígitos, no código e na linha
        return {"arrecadacao": False, "banco": dig[:3]}
    return None


def codigo_da_linha(texto: str) -> str | None:
    """Os 44 dígitos do código a partir da linha digitável (arrecadação: 4 blocos de 11 + DV; boleto: 47 dígitos) ou
    do próprio código; None se não reconhece."""
    dig = re.sub(r"\D", "", str(texto or ""))
    if len(dig) == 44:
        return dig
    if len(dig) == 48 and dig[0] == "8":
        return "".join(dig[i:i + 11] for i in range(0, 48, 12))
    if len(dig) == 47:  # campo1 (9+DV) campo2 (10+DV) campo3 (10+DV) DV geral campo5 (fator + valor)
        c1, c2, c3, dv, c5 = dig[:9], dig[10:20], dig[21:31], dig[32], dig[33:47]
        return c1[:4] + dv + c5 + c1[4:9] + c2 + c3
    return None


def _venc_do_fator(fator: int) -> dt.date | None:
    """O vencimento do fator do boleto (dias desde 07/10/1997, que recomeçam em 1000 a cada 9000 dias desde
    22/02/2025): a data possível mais perto de hoje; None sem vencimento (fator 0)."""
    if not fator:
        return None
    cands = [dt.date(1997, 10, 7) + dt.timedelta(days=fator)]
    if fator >= 1000:
        cands += [dt.date(2025, 2, 22) + dt.timedelta(days=fator - 1000 + 9000 * k) for k in range(2)]
    return min(cands, key=lambda d: abs((d - hoje()).days))


def _nos_digitos_de(antigo: str, novo: str) -> str:
    """Os dígitos de ``novo`` na pontuação de ``antigo`` (se os dois têm o mesmo número de dígitos); senão ``novo``."""
    dig = [c for c in novo if c.isdigit()]
    if len(dig) != sum(c.isdigit() for c in antigo):
        return novo
    it = iter(dig)
    return "".join(next(it) if c.isdigit() else c for c in antigo)


def _codigo_parecido(categoria: str, antigo: str) -> str:
    """Código de barras (ou linha digitável) novo no lugar do antigo, na pontuação dele: a mesma empresa e segmento (ou
    o banco), o mesmo valor codificado (o total novo, se a versão trocou o total: ``usando(documento=...)``) e, no
    boleto, o mesmo vencimento (o novo, se a versão trocou as datas)."""
    dig = re.sub(r"\D", "", antigo)
    kw = partes_da_linha(antigo) or {"arrecadacao": (dig[:1] == "8") if dig else None}
    cod = codigo_da_linha(antigo)
    doc = _documento_atual.get()
    if cod:
        valor = int(cod[4:15]) if cod[0] == "8" else int(cod[9:19])
        if valor:
            kw["centavos"] = doc["valores"].get(valor, valor) if doc is not None else valor
        if cod[0] != "8":
            kw["vencimento"] = doc["ciclo"]["vencimento"] if doc is not None and doc["ciclo"] else \
                _venc_do_fator(int(cod[5:9]))
    novo, linha, _ = par_codigo(**kw)
    return _nos_digitos_de(antigo, linha if categoria == "linha digitável" else novo)


def valor_em_centavos(texto: str) -> int | None:
    """"R$ 1.384,57" → 138457 (o código de barras novo bate com o valor impresso); None sem valor positivo."""
    v = _numero_br(texto)
    if v is None or v <= 0:
        return None
    return int(round(v * 100))


# ---------------------------------------------------------------------------
# telefone, e-mail, RG e identificadores
# ---------------------------------------------------------------------------

# DDDs de cada UF, do mais populoso (o da capital) para o menos
DDDS_UF = {"SP": ("11", "19", "12", "13", "15", "16", "17", "14", "18"), "RJ": ("21", "24", "22"), "ES": ("27", "28"),
           "MG": ("31", "34", "32", "35", "37", "38", "33"), "PR": ("41", "43", "44", "45", "42", "46"),
           "SC": ("47", "48", "49"), "RS": ("51", "54", "53", "55"), "DF": ("61",), "GO": ("62", "64"), "TO": ("63",),
           "MT": ("65", "66"), "MS": ("67",), "AC": ("68",), "RO": ("69",), "BA": ("71", "75", "73", "77", "74"),
           "SE": ("79",), "PE": ("81", "87"), "AL": ("82",), "PB": ("83",), "RN": ("84",), "CE": ("85", "88"),
           "PI": ("86", "89"), "MA": ("98", "99"), "PA": ("91", "93", "94"), "AP": ("96",), "AM": ("92", "97"),
           "RR": ("95",)}
_UF_DO_DDD = {d: uf for uf, ds in DDDS_UF.items() for d in ds}
# o DDD das cidades embutidas (sem acento); a base de lugares (config/vocabulario/municipios.tsv, colunas nome, uf e
# ddd) traz o de todos os municípios; cidade fora das duas sai com um DDD da UF
_DDD_CIDADES = {
    "11": "SAO PAULO;GUARULHOS;SAO BERNARDO DO CAMPO;SANTO ANDRE;OSASCO;MOGI DAS CRUZES;JUNDIAI",
    "12": "SAO JOSE DOS CAMPOS;TAUBATE", "13": "SANTOS", "14": "BAURU;MARILIA", "15": "SOROCABA",
    "16": "RIBEIRAO PRETO;FRANCA;ARARAQUARA", "17": "SAO JOSE DO RIO PRETO", "18": "PRESIDENTE PRUDENTE",
    "19": "CAMPINAS;PIRACICABA;LIMEIRA", "21": "RIO DE JANEIRO;SAO GONCALO;DUQUE DE CAXIAS;NOVA IGUACU;NITEROI",
    "22": "CAMPOS DOS GOYTACAZES;MACAE", "24": "PETROPOLIS;VOLTA REDONDA", "27": "VITORIA;VILA VELHA;SERRA;LINHARES",
    "28": "CACHOEIRO DE ITAPEMIRIM", "31": "BELO HORIZONTE;CONTAGEM;BETIM;IPATINGA", "32": "JUIZ DE FORA",
    "33": "GOVERNADOR VALADARES", "34": "UBERLANDIA;UBERABA", "35": "POCOS DE CALDAS", "37": "DIVINOPOLIS",
    "38": "MONTES CLAROS", "41": "CURITIBA;SAO JOSE DOS PINHAIS", "42": "PONTA GROSSA;GUARAPUAVA", "43": "LONDRINA",
    "44": "MARINGA", "45": "CASCAVEL;FOZ DO IGUACU", "47": "JOINVILLE;BLUMENAU;ITAJAI", "48": "FLORIANOPOLIS;CRICIUMA",
    "49": "CHAPECO;LAGES", "51": "PORTO ALEGRE;CANOAS;NOVO HAMBURGO;GRAVATAI", "53": "PELOTAS",
    "54": "CAXIAS DO SUL;PASSO FUNDO", "55": "SANTA MARIA", "61": "BRASILIA",
    "62": "GOIANIA;APARECIDA DE GOIANIA;ANAPOLIS", "63": "PALMAS", "64": "RIO VERDE", "65": "CUIABA;VARZEA GRANDE",
    "66": "RONDONOPOLIS;SINOP", "67": "CAMPO GRANDE;DOURADOS;TRES LAGOAS", "68": "RIO BRANCO", "69": "PORTO VELHO",
    "71": "SALVADOR;CAMACARI", "73": "ITABUNA;ILHEUS", "74": "JUAZEIRO", "75": "FEIRA DE SANTANA",
    "77": "VITORIA DA CONQUISTA", "79": "ARACAJU", "81": "RECIFE;JABOATAO DOS GUARARAPES;OLINDA;CARUARU",
    "82": "MACEIO;ARAPIRACA", "83": "JOAO PESSOA;CAMPINA GRANDE", "84": "NATAL;MOSSORO", "85": "FORTALEZA;CAUCAIA",
    "86": "TERESINA;PARNAIBA", "87": "PETROLINA", "88": "JUAZEIRO DO NORTE;SOBRAL", "91": "BELEM;ANANINDEUA",
    "92": "MANAUS", "93": "SANTAREM", "94": "MARABA", "95": "BOA VISTA", "96": "MACAPA", "98": "SAO LUIS",
    "99": "IMPERATRIZ"}


def _ddd_do_arquivo() -> dict:
    """{(CIDADE, UF): DDD} da base de lugares (config/vocabulario/municipios.tsv); {} sem o arquivo."""
    caminho = _VOCABULARIO / "municipios.tsv"
    if not caminho.is_file():
        return {}
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        return {(_normalizar(r["nome"]), _normalizar(r["uf"])): r["ddd"] for r in csv.DictReader(f, delimiter="\t")
                if r.get("nome") and r.get("uf") and re.fullmatch(r"\d{2}", r.get("ddd") or "")}


_DDD_CIDADE = {**{(c, _UF_DO_DDD[d]): d for d, cs in _DDD_CIDADES.items() for c in cs.split(";")}, **_ddd_do_arquivo()}
for (_c, _uf), _d in _DDD_CIDADE.items():  # município de divisa com o DDD da UF vizinha (o 61 no entorno de Brasília)
    if _uf in DDDS_UF and _d not in DDDS_UF[_uf]:
        DDDS_UF[_uf] += (_d,)


def ddd_de(cidade: str | None = None, uf: str | None = None) -> str | None:
    """O DDD da cidade (tabela por município); cidade desconhecida: um DDD da UF, pelo peso (o da capital primeiro);
    None sem UF conhecida."""
    k = (_normalizar(cidade or ""), _normalizar(uf or ""))
    if k in _DDD_CIDADE:
        return _DDD_CIDADE[k]
    ddds = DDDS_UF.get(k[1])
    return _rng.choices(ddds, [1 / (i + 1) for i in range(len(ddds))])[0] if ddds else None


_RE_TELEFONE = re.compile(
    r"(?<![\w+])(?:0[3589]00[\s.\-]?\d{3}[\s.\-]?\d{4}"  # 0800 727 0196
    r"|[34]00\d[\s.\-]\d{4}"  # 4004-0001, 3003 1234 (número único nacional)
    r"|(?:\+\s?55[\s.\-]?)?(?:\(\s?0?\d{2}\s?\)|0?\d{2}[\s.\-])\s?9?\d{4}[\s.\-]?\d{4})(?!\d)")  # (85) 99876-5432


def _partes_telefone(texto: str) -> dict | None:
    """O telefone em ``texto``: {"m": o trecho, "sac": 0800/4004..., "pais", "zero", "ddd", "numero"}; None sem
    telefone."""
    m = _RE_TELEFONE.search(texto or "")
    if not m:
        return None
    dig = re.sub(r"\D", "", m.group())
    if (len(dig) == 11 and dig[0] == "0" and dig[2:4] == "00") or (len(dig) == 8 and dig[:3] in ("400", "300")):
        return {"m": m, "sac": True}
    pais = "55" if "+" in m.group() or (len(dig) in (12, 13) and dig[:2] == "55") else ""
    dig = dig[len(pais):]
    zero = "0" if dig[:1] == "0" else ""
    dig = dig[len(zero):]
    if len(dig) not in (10, 11):
        return None
    return {"m": m, "sac": False, "pais": pais, "zero": zero, "ddd": dig[:2], "numero": dig[2:]}


def ddd_do_telefone(texto: str) -> str | None:
    """O DDD do telefone em ``texto`` ("(85) 99876-5432" → "85"); None sem telefone com DDD (0800, 4004...)."""
    p = _partes_telefone(texto)
    return p["ddd"] if p and not p["sac"] else None


def _numero_de_telefone(velho: str) -> str:
    """Número novo do tipo do velho: celular (9 dígitos: 9 + 9[6-9]...), celular antigo de 8 (começa com 6–9) ou fixo
    (começa com 2–5: o prefixo da região, o do velho em 85% das vezes; senão o 3, o mais comum)."""
    if len(velho) == 9:
        return "9" + _rng.choices("9876", (40, 30, 18, 12))[0] + _digitos(7)
    if velho[0] in "6789":
        return _rng.choice("6789") + _digitos(7)
    inicio = velho[0] if velho[0] in "2345" and _rng.random() < 0.85 else _rng.choices("2345", (30, 45, 15, 10))[0]
    return inicio + _digitos(7)


def _ddd_novo(velho: str, cidade: tuple | None) -> str:
    """O DDD do telefone novo: o da ``cidade`` do documento (fica o velho, em 85% das vezes, se já é da UF dela); sem a
    cidade, o velho (85%) ou outro da UF dele."""
    uf = _normalizar((cidade or (None, None))[1] or "")
    if uf not in DDDS_UF:
        cidade, uf = None, _UF_DO_DDD.get(velho)
    if uf is None:
        return ddd_de(*_sortear("cidades"))
    if velho in DDDS_UF[uf] and _rng.random() < 0.85:
        return velho
    return ddd_de(cidade[0] if cidade else None, uf)


def _telefone_no_molde(antigo: str, cidade: tuple | None = None) -> str | None:
    """Telefone novo no molde do antigo ("(85) 99876-5432", "85 99876-5432", "(85)3456-7890", "+55 85 ...", "0800 727
    0196"): celular continua celular, fixo continua fixo, 0800/4004/3003 mantêm o prefixo, o DDD é o da ``cidade`` (ou
    a de ``usando(cidade=...)``). None se o antigo não tem telefone."""
    p = _partes_telefone(antigo)
    if p is None:
        return None
    m, cidade = p["m"], cidade or cidade_atual()
    for _ in range(5):
        if p["sac"]:
            dig = re.sub(r"\D", "", m.group())
            novo = dig[:4] + str(_rng.randint(1, 9)) + _digitos(len(dig) - 5)
        else:
            novo = p["pais"] + p["zero"] + _ddd_novo(p["ddd"], cidade) + _numero_de_telefone(p["numero"])
        texto = antigo[:m.start()] + _nos_digitos_de(m.group(), novo) + antigo[m.end():]
        if texto != antigo:
            break
    return texto


def telefone() -> str:
    """Telefone "(DD) 9XXXX-XXXX" (celular, 70%) ou "(DD) XXXX-XXXX" (fixo) com o DDD da cidade de
    ``usando(cidade=...)`` (sem ela, o de uma cidade do split da vez)."""
    cid = cidade_atual()
    ddd = (ddd_de(*cid) if cid else None) or ddd_de(*_sortear("cidades"))
    numero = _numero_de_telefone("9" * 9 if _rng.random() < 0.7 else "3" * 8)
    return f"({ddd}) {numero[:-4]}-{numero[-4:]}"


# provedores na proporção do país: gmail muito à frente, depois hotmail/outlook, yahoo e os nacionais
_PROVEDORES = (("gmail.com", 62), ("hotmail.com", 14), ("outlook.com", 6), ("yahoo.com.br", 4), ("bol.com.br", 3),
               ("uol.com.br", 3), ("yahoo.com", 2), ("live.com", 1.5), ("icloud.com", 1.5), ("terra.com.br", 1.5),
               ("ig.com.br", 1), ("hotmail.com.br", 0.5))
_WEBMAIL = frozenset(p for p, _ in _PROVEDORES) | {"msn.com", "globo.com", "globomail.com", "r7.com", "oi.com.br",
                                                   "outlook.com.br", "live.com.br", "zipmail.com.br"}
# o usuário: N = prenome, S = sobrenome, i/s = a inicial deles, A = ano de nascimento, # = um dígito
_MOLDES_EMAIL = (("N.S", 22), ("NS", 10), ("N_S", 5), ("N.S##", 15), ("NA", 12), ("iS", 2), ("iS##", 5), ("NS##", 10),
                 ("S.N", 3), ("N.S.S", 6), ("N.SA", 6), ("N###", 4))
_RE_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")


def _minusculo(texto: str) -> str:
    return re.sub(r"[^a-z]", "", _sem_acento(texto).lower())


def _usuario_email(molde: str) -> str:
    """O usuário do e-mail no ``molde`` (como em ``_MOLDES_EMAIL``), com nomes do vocabulário do split."""
    nome = _minusculo(_sortear(_rng.choice(("nomes_f", "nomes_m"))))
    sobrenomes = [_minusculo(_sortear("sobrenomes")) for _ in range(sum(c in "Ss" for c in molde))]
    out = []
    for c in molde:
        if c in "Ss":
            s = sobrenomes.pop()
            out.append(s if c == "S" else s[0])
        else:
            out.append({"N": nome, "i": nome[0], "A": str(_rng.randint(1955, 2006)),
                        "#": str(_rng.randrange(10))}.get(c, c))
    return "".join(out)


def _molde_do_usuario(usuario: str) -> str:
    """O molde de um usuário de e-mail ("maria.silva85" → "N.S##", "joao_souza1985" → "N_SA", "mariasilva" → "NS")."""
    out, palavras = [], 0
    for t in re.findall(r"[^\W\d_]+|\d+|.", usuario):
        if t.isdigit():
            out.append("A" if len(t) == 4 and t[:2] in ("19", "20") else "#" * len(t))
        elif t.isalpha():
            out.append(("N" if not palavras else "S") if len(t) > 1 else ("i" if not palavras else "s"))
            palavras += 1
        else:
            out.append(t)
    molde = "".join(out)
    return molde.replace("N", "NS") if palavras == 1 and sum(c.isalpha() for c in usuario) >= 9 else molde


def email() -> str:
    """E-mail de pessoa como os de verdade: o usuário num molde comum (maria.silva, mariasilva, maria.silva85,
    maria1987, msilva...) com nomes do vocabulário; o provedor na proporção do país e, raramente (3%), o domínio de
    uma empresa."""
    usuario = _usuario_email(_rng.choices(*zip(*_MOLDES_EMAIL))[0])
    if _rng.random() < 0.03:
        return f"{usuario}@{_minusculo(_sortear('sobrenomes'))}{_minusculo(_rng.choice(_RAMOS))}.com.br"
    return f"{usuario}@{_rng.choices(*zip(*_PROVEDORES))[0]}"


def _email_no_molde(antigo: str) -> str | None:
    """E-mail novo no molde do antigo (o mesmo desenho do usuário, a caixa); webmail vira outro webmail na proporção do
    país, o domínio de uma empresa fica. None se o antigo não tem e-mail."""
    m = _RE_EMAIL.search(antigo)
    if not m:
        return None
    usuario, dominio = m.group().rsplit("@", 1)
    for _ in range(5):
        novo_dom = _rng.choices(*zip(*_PROVEDORES))[0] if dominio.lower() in _WEBMAIL else dominio.lower()
        novo = _caixa(m.group(), f"{_usuario_email(_molde_do_usuario(usuario))}@{novo_dom}")
        if novo.lower() != m.group().lower():
            break
    return antigo[:m.start()] + novo + antigo[m.end():]


_RE_RG = re.compile(r"(?<!\d)\d[\d.]{4,10}\d(?:\s?-\s?[\dXx])?(?![\dA-Za-z])")
_RE_ORGAO = re.compile(r"\b(?:RG|SSP|SDS|SESP|SESDC|IFP|DETRAN|DGPC|SSPDS|PC|SJS|IGP)\b")


def _dv_rg_sp(corpo: str) -> str:
    """Dígito verificador do RG de SP: os 8 dígitos × 2..9, o resto da divisão por 11 (10 vira X)."""
    r = sum(int(c) * p for c, p in zip(corpo, range(2, 10))) % 11
    return "X" if r == 10 else str(r)


def _rg_no_molde(antigo: str) -> str | None:
    """RG novo no molde do antigo: os mesmos dígitos, pontos e zeros; o órgão e a UF ("SSP/SP", "MG-") como estavam;
    o dígito verificador pelo cálculo de SP quando o antigo confere com ele (senão, sorteado). None sem número."""
    m = _RE_RG.search(antigo)
    if not m:
        return None
    numero, d = m.group(), re.search(r"\s?-\s?([\dXx])$", m.group())
    if d:
        numero = numero[:d.start()]
    for _ in range(5):
        novo = re.sub(r"\d+", lambda x: _digitos_como(x.group()), numero)
        if d:
            velho = re.sub(r"\D", "", numero)
            dv = (_dv_rg_sp(re.sub(r"\D", "", novo)) if len(velho) == 8 and _dv_rg_sp(velho) == d.group(1).upper()
                  else str(_rng.randrange(10)))
            novo += d.group()[:-1] + (dv.lower() if d.group(1).islower() else dv)
        if novo != m.group():
            break
    return antigo[:m.start()] + novo + antigo[m.end():]


def _dv_mod11x(corpo: str) -> str:
    r = 11 - sum(int(c) * (2 + i % 8) for i, c in enumerate(reversed(corpo))) % 11
    return "X" if r == 10 else "0" if r == 11 else str(r)


# como se calcula o dígito verificador de nº de instalação, cliente, medidor, protocolo e nota fiscal: módulo 10,
# módulo 11 (10 e 11 viram 0) e módulo 11 com X (10 vira X)
_DVS = (lambda c: str(codigos.mod10(c)), lambda c: str(codigos.mod11_arrecadacao(c)), _dv_mod11x)
_RE_COM_DV = re.compile(r"(?P<corpo>\d[\d.]*\d|\d)-(?P<dv>[\dXx])(?![\dA-Za-z])|\d+")


def _identificador_no_molde(antigo: str) -> str:
    """Número novo com os dígitos, os zeros à esquerda e os separadores do antigo; com dígito verificador depois do
    hífen ("1234567-8"), o novo é calculado do jeito que o antigo confere (módulo 10 ou 11; senão, sorteado)."""
    def trocar(m):
        if m["corpo"] is None:
            return _digitos_como(m.group())
        velho = re.sub(r"\D", "", m["corpo"])
        calc = next((f for f in _DVS if f(velho) == m["dv"].upper()), None)
        corpo = re.sub(r"\d+", lambda x: _digitos_como(x.group()), m["corpo"])
        dv = calc(re.sub(r"\D", "", corpo)) if calc else str(_rng.randrange(10))
        return f"{corpo}-{dv.lower() if m['dv'].islower() else dv}"
    return _RE_COM_DV.sub(trocar, antigo)


def _identificador_gerado() -> str:
    """Nº de instalação/cliente/fatura sem original: de 6 a 13 dígitos, às vezes com zeros à esquerda ou com dígito
    verificador depois do hífen."""
    n = _rng.choices((6, 7, 8, 9, 10, 12, 13), (6, 14, 20, 16, 22, 12, 10))[0]
    corpo = str(_rng.randint(1, 9)) + _digitos(n - 1)
    if _rng.random() < 0.2:
        zeros = _rng.randint(1, n - 4)
        corpo = "0" * zeros + corpo[zeros:]
    if _rng.random() < 0.35:
        return f"{corpo[:-1]}-{_rng.choice(_DVS)(corpo[:-1])}"
    return corpo


_RE_DOCUMENTO = re.compile(r"(?<![A-Za-z])[\d*•#Xx](?:[\d*•#Xx./\- ]*[\d*•#Xx])?(?![A-Za-z])")
_POSICAO = re.compile(r"[\d*•#Xx]")


def _documento_no_molde(antigo: str) -> str:
    """CPF ou CNPJ novo e válido no molde do antigo (a pontuação; os dígitos escondidos de "***.456.789-**" continuam
    escondidos; a região fiscal do CPF, na maioria das vezes; a matriz continua matriz); outra quantidade de dígitos é
    RG."""
    regioes = [m for m in _RE_DOCUMENTO.finditer(antigo) if any(c.isdigit() for c in m.group())]
    if not regioes:
        return cpf()
    m = max(regioes, key=lambda r: len(_POSICAO.findall(r.group())))
    posicoes = [i for i, c in enumerate(m.group()) if _POSICAO.fullmatch(c)]
    if len(posicoes) not in (11, 14):
        return _rg_no_molde(antigo) or _trocar_digitos(antigo)
    velho = "".join(m.group()[i] for i in posicoes)
    if len(posicoes) == 14:  # a matriz (/0001) continua matriz; uma filial às vezes vira a matriz, às vezes outra
        ordem = int(velho[8:12]) if velho[8:12].isdigit() else 1
        dig = cnpj(1 if ordem <= 1 or _rng.random() < 0.5 else _rng.randint(2, min(9999, max(9, 2 * ordem))))
    else:
        dig = cpf(int(velho[8]) if velho[8].isdigit() and _rng.random() < 0.75 else None)
    dig = re.sub(r"\D", "", dig)
    texto = list(m.group())
    for k, i in enumerate(posicoes):
        if texto[i].isdigit():
            texto[i] = dig[k]
    return antigo[:m.start()] + "".join(texto) + antigo[m.end():]


def _numero_da_versao(categoria: str, antigo: str, novo) -> str:
    """``novo()`` (o número novo no lugar de ``antigo``); na mesma versão (``usando(documento=...)``), o mesmo número
    velho vira o mesmo número novo em todo lugar, na pontuação de cada lugar."""
    doc = _documento_atual.get()
    chave = (categoria, re.sub(r"\D", "", antigo))
    if doc is not None and chave in doc["trocas"]:
        return _nos_digitos_de(antigo, doc["trocas"][chave])
    texto = novo()
    if doc is not None:
        doc["trocas"][chave] = re.sub(r"\D", "", texto)
    return texto


# ---------------------------------------------------------------------------
# nome de pessoa e razão social
# ---------------------------------------------------------------------------

def _nome_pessoa(palavras: int | None = None, letras: int | None = None) -> str:
    """Nome de pessoa como os do país: pela base de nomes (``_nome_da_base``) ou, sem ela, pelo vocabulário embutido
    (``_nome_embutido``). ``palavras``: com quantas palavras (o nome novo cabe onde estava o antigo); None = na
    proporção da população. ``letras``: o comprimento do antigo (com a base, o novo fica a ±15% dele quando dá)."""
    b = _nomes_base()
    return _nome_da_base(b, palavras, letras) if b is not None else _nome_embutido(palavras)


def _nome_da_base(b: dict, palavras: int | None = None, letras: int | None = None) -> str:
    """Nome de pessoa pela estrutura real (nomes_estrutura.json): gênero pelo Censo (51% F); 1º prenome pela frequência
    real do gênero; composto (MARIA APARECIDA, JOSÉ CARLOS) ou devocional (MARIA DE FÁTIMA, JOÃO BATISTA, RAIMUNDO
    NONATO) na taxa daquele prenome; o nº de sobrenomes pelo gênero e pelo tipo do prenome (composto puxa menos);
    cada sobrenome pelo peso, sem repetir, com a partícula que ele costuma ter (DA SILVA, DOS SANTOS, DE JESUS, PEREIRA
    sem nada, COSTA E SILVA; o último leva mais que o primeiro); FILHO, JÚNIOR, NETO só em homem (5%); o acento como no
    registro civil (``_acentuar_nome``). Com ``palavras``, o nome tem esse nº de palavras (com partículas e sufixo): só
    sai a forma que cabe, na chance que ela tem. No split da vez, o nome completo cai no split pelo hash e, com
    ``letras``, fica a ±15% desse comprimento quando dá (o último sobrenome é sorteado de novo até servir: "ANA LIMA"
    vira "JOÃO REIS", não "JOÃO ALVES"; só 1% dos nomes de duas palavras cabe em 8 letras)."""
    split, nome = _split_atual.get(), None
    for _ in range(60):
        g = "F" if _rng.random() < b["p_f"] else "M"
        p1 = _sortear("nomes_" + g.lower())
        so_composto, devocional, _n = b["por_primeiro"][g].get(p1) or b["geral"][g]
        r, meio, devocao = _rng.random(), None, None
        if r < devocional:
            devocao = _devocao(b, p1, split)
            if devocao and _rng.random() < b["p_cd"][g]:
                meio = _segundo_prenome(b, g, p1, split)
        elif r < devocional + so_composto:
            meio = _segundo_prenome(b, g, p1, split)
        tipo = ("composto_devocional" if meio and devocao else "devocional" if devocao else "composto" if meio
                else "simples")
        frente = [p1, *([meio] if meio else []), *(devocao.split() if devocao else [])]
        sufixo = _rng.choices(*b["sufixos"])[0] if g == "M" and _rng.random() < b["p_sufixo"] else None
        ks, pesos = b["k"][g, tipo]
        resto = None if palavras is None else palavras - len(frente) - (sufixo is not None)
        if resto is not None:  # k sobrenomes ocupam de k a 2k palavras
            ks, pesos = [k for k in ks if (resto + 1) // 2 <= k <= resto], [w for k, w in zip(ks, pesos)
                                                                           if (resto + 1) // 2 <= k <= resto]
            if not ks or not sum(pesos):
                continue
        k = _rng.choices(ks, pesos)[0]
        vistos, sobrenomes = set(frente), []
        outro = b["so_do_genero"]["M" if g == "F" else "F"]  # LORENA ANTONIO: parece prenome trocado
        for _ in range(4 * k):
            s = _sortear("sobrenomes")
            if s not in vistos and (sobrenomes or s not in outro):
                vistos.add(s)
                sobrenomes.append(s)
                if len(sobrenomes) == k:
                    break
        q = [_q_particula(b, s, len(sobrenomes), j) for j, s in enumerate(sobrenomes)]
        if resto is None:
            com = [_rng.random() < x for x in q]
        else:
            com = _particulas_exatas(q, resto - sum(b["sob"][s][0] for s in sobrenomes))
            if com is None:
                continue
        pts = [_particula(b, s, len(sobrenomes), j) if c else None for j, (s, c) in enumerate(zip(sobrenomes, com))]

        def montar():
            return " ".join([*frente, *(w for s, p in zip(sobrenomes, pts) for w in (p, s) if w),
                             *([sufixo] if sufixo else [])])
        nome = montar()

        def serve(n, comprimento):  # no split e, com ``comprimento``, do tamanho do antigo
            return ((split not in _SPLITS or _fatia(n) == split)
                    and (not comprimento or abs(len(n) - letras) <= max(1.0, 0.15 * letras)))
        if sobrenomes and not serve(nome, letras):  # outro último sobrenome até servir (sem caber, só no split)
            j = len(sobrenomes) - 1
            for tentativa in range(140):
                if serve(nome, letras and tentativa < 60):
                    break
                s = _sortear("sobrenomes")
                x = _q_particula(b, s, len(sobrenomes), j)
                if (s in vistos or (j == 0 and s in outro) or b["sob"][s][0] != b["sob"][sobrenomes[j]][0]
                        or _rng.random() >= (x if pts[j] else 1 - x)):  # a partícula decidida tem de servir para ele
                    continue
                vistos.add(s)
                sobrenomes[j], pts[j] = s, _particula(b, s, len(sobrenomes), j) if pts[j] else None
                nome = montar()
            else:
                continue
        return _acentuar_nome(nome)
    return _acentuar_nome(nome) if nome else _nome_embutido(palavras)


def _segundo_prenome(b: dict, g: str, p1: str, split: str | None) -> str | None:
    """O 2º prenome de um composto: um par da lista (MARIA APARECIDA, JOSÉ CARLOS, JOSÉ MARIA) na proporção em que os
    pares conhecidos cobrem os compostos daquele prenome, senão um 2º prenome do gênero, pelo peso; do split da vez."""
    pares = b["pares"][g].get(p1)
    compostos = (b["por_primeiro"][g].get(p1) or (0, 0, None))[2]
    if pares and _rng.random() < (min(1.0, pares[2] / compostos) if compostos else 1.0):
        ok = b.setdefault("pares_split", {}).get((g, p1, split))
        if ok is None:
            ok = b["pares_split"][g, p1, split] = [(s, n) for s, n in zip(pares[0], pares[1])
                                                   if s != p1 and all(_da_fatia(w, split) for w in s.split())]
        if ok:
            return _rng.choices(*zip(*ok))[0]
    for _ in range(10):
        meio = _sortear("meios_" + g.lower())
        if meio != p1:
            return meio
    return None


def _devocao(b: dict, p1: str, split: str | None) -> str | None:
    """O complemento devocional do prenome (DE FÁTIMA com MARIA, BATISTA com JOÃO, NONATO com RAIMUNDO), pelo nº de
    pessoas; só os do split da vez (BATISTA é sobrenome da cauda); None se o prenome não tem devoção."""
    ok = b.setdefault("devocoes_split", {}).get((p1, split))
    if ok is None:
        ok = b["devocoes_split"][p1, split] = [
            (d, n) for d, n in zip(*b["devocoes"].get(p1, ((), ())))
            if all(_da_fatia(w, split) for w in d.split() if w not in _PARTICULAS)]
    return _rng.choices(*zip(*ok))[0] if ok else None


def _pesos_particula(b: dict, s: str, k: int, j: int) -> tuple[float, list]:
    """(peso de vir sem partícula, pesos de DA, DE, DO, DOS, DAS, E) do ``j``-ésimo de ``k`` sobrenomes: os do
    sobrenome (SANTOS: DOS 0,72; PEREIRA: nenhuma) vezes o fator da posição (``_ler_nomes``); o E não abre a lista."""
    _np, p0, ps = b["sob"][s]
    f = b["fator"].get((min(k, 4), min(j, 3))) or (1.0,) * (len(ps) + 1)
    pesos = [p * x for p, x in zip(ps, f[1:])]
    if j == 0:
        pesos[-1] = 0.0
    return p0 * f[0], pesos


def _q_particula(b: dict, s: str, k: int, j: int) -> float:
    """Chance de o ``j``-ésimo de ``k`` sobrenomes vir com partícula (com 2 sobrenomes, 18% no 1º e 39% no 2º)."""
    sem, com = _pesos_particula(b, s, k, j)
    return sum(com) / (sum(com) + sem) if sum(com) + sem > 0 else 0.0


def _particula(b: dict, s: str, k: int, j: int) -> str:
    """A partícula do ``j``-ésimo de ``k`` sobrenomes pela distribuição dele na posição (SANTOS → DOS, SILVA → DA; o
    DA vem mais no último, e o E só entre sobrenomes: COSTA E SILVA)."""
    pesos = _pesos_particula(b, s, k, j)[1]
    return _rng.choices(_PARTICULAS_NOME, pesos)[0] if sum(pesos) > 0 else "DE"


def _particulas_exatas(q: list, m: int) -> list | None:
    """Quais sobrenomes levam partícula, sabendo que são exatamente ``m`` (o nome tem de ter o nº de palavras do
    antigo): aceita com a chance de serem ``m`` e escolhe quais pela chance de cada um; None = recusado (outro nome)."""
    if not 0 <= m <= len(q):
        return None
    combos = list(itertools.combinations(range(len(q)), m))
    pesos = [math.prod(x if i in c else 1 - x for i, x in enumerate(q)) for c in combos]
    if _rng.random() >= sum(pesos):
        return None
    c = _rng.choices(combos, pesos)[0]
    return [i in c for i in range(len(q))]


def _acentuar_nome(nome: str) -> str:
    """O nome (sem acento) como no registro civil: cada palavra acentuável (JOSE → JOSÉ, ARAUJO → ARAÚJO) sai com
    acento na fração das vezes em que sai assim no TSE (JOSÉ 48%, ANTÔNIO 15%), e as palavras de um mesmo nome andam
    juntas (quem escreve JOSÉ tende a escrever ARAÚJO: um sorteio comum a todas, com peso ``rho``, reproduz a fração
    de nomes com tudo acentuado). Sorteado sempre: o acento só aparece no texto com ``usando(acentos=True)``, e os
    dois contratos tiram os mesmos números do gerador. Sem a base de nomes, como veio."""
    b = _nomes_base()
    if b is None:
        return nome
    comum, out = None, []
    for w in nome.split(" "):
        a = b["acento"].get(w)
        if a is not None and a[0] != w:
            if comum is None:
                comum = _rng.random()
            w = a[0] if (comum if _rng.random() < b["rho"] else _rng.random()) < a[1] else w
        out.append(w)
    return " ".join(out)


def _nome_embutido(palavras: int | None = None) -> str:
    """Nome de pessoa pelo vocabulário embutido (sem a base de nomes): prenome pela frequência, gênero sorteado; às
    vezes composto do mesmo gênero (ANA PAULA, JOSÉ CARLOS, MARIA DE FÁTIMA); um a três sobrenomes, com a partícula de
    cada um (DA SILVA, DOS SANTOS); FILHO/JÚNIOR/NETO em ~3% dos homens."""
    genero = "f" if _rng.random() < 0.51 else "m"
    primeiro = _sortear("nomes_" + genero)
    nome = [primeiro]
    k = _normalizar(primeiro)

    def cabe(n: int) -> bool:  # ainda cabem n palavras, deixando uma para o sobrenome?
        return palavras is None or len(" ".join(nome).split()) + n + 1 <= palavras

    if cabe(1) and _rng.random() < _P_MEIO.get(k, 0.18):
        split = _split_atual.get()  # "JOÃO BATISTA": BATISTA é sobrenome da cauda e só vale no split dele
        devocoes = [d for d in _DEVOCOES.get(k, ()) if cabe(len(d.split()))
                    and all(_da_fatia(w, split) for w in _normalizar(d).split())]
        if devocoes and _rng.random() < 0.35:
            nome.append(_rng.choice(devocoes))
        else:
            meio = _sortear("meios_" + genero)
            if _normalizar(meio) != k:
                nome.append(meio)
    sufixo = genero == "m" and cabe(1) and _rng.random() < 0.03
    vistos = set(_normalizar(" ".join(nome)).split())
    quantos = _rng.choices((1, 2, 3), (25, 55, 20))[0] if palavras is None else None
    restam = None if palavras is None else max(1, palavras - len(" ".join(nome).split()) - sufixo)
    sobrenomes = []  # com as partículas
    for _ in range(20):
        if (len(sobrenomes) >= restam) if restam else (sum(s not in _PARTICULAS for s in sobrenomes) >= quantos):
            break
        s = _sortear("sobrenomes")
        ks = _normalizar(s)
        if ks in vistos:
            continue
        vistos.add(ks)
        particula = _PARTICULA.get(ks)
        if particula and (not restam or len(sobrenomes) + 2 <= restam) and \
                _rng.random() < (0.9 if ks in _PARTICULA_QUASE_SEMPRE else 0.55):
            sobrenomes.append(particula)
        sobrenomes.append(s)
    nome += sobrenomes
    if sufixo:
        nome.append(_rng.choices(*zip(*_SUFIXOS_NOME))[0])
    return " ".join(nome)


def _abreviar(nome: str, posicoes: list[int], ponto: bool) -> str:
    """Abrevia o nome novo onde o antigo tinha iniciais ("J F A SILVA", "MARIA A. SOUZA"): a última palavra (o
    sobrenome) fica inteira e partícula não vira inicial."""
    ps = nome.split()
    for i in posicoes:
        if i < len(ps) - 1 and _normalizar(ps[i]) not in _PARTICULAS:
            ps[i] = ps[i][0] + ("." if ponto else "")
    return " ".join(ps)


_SOCIEDADES = frozenset("LTDA ME EPP EIRELI MEI S/A S.A SA CIA".split())
_RE_SOCIEDADE = re.compile(r"(?:^|\s)(?:LTDA\.?|EIRELI|EPP|ME|MEI|S/A|S\.\s?A\.?|CIA\.?)$")
_PALAVRAS_EMPRESA = frozenset(w for r in _RAMOS + _PRODUTOS for w in _normalizar(r).split() if w not in _PARTICULAS) | {
    "COMERCIO", "COMERCIAL", "INDUSTRIA", "INDUSTRIAL", "INDUSTRIAS", "IMPORTADORA", "EXPORTADORA", "IMPORTACAO",
    "EXPORTACAO", "CONSTRUTORA", "EMPREENDIMENTOS", "PARTICIPACOES", "ASSOCIACAO", "CONDOMINIO", "COOPERATIVA",
    "SUPERMERCADO", "MERCADINHO", "MERCADO", "FARMACIA", "DROGARIA", "RESTAURANTE", "HOTEL", "POUSADA", "CLINICA",
    "LABORATORIO", "ESCOLA", "COLEGIO", "ACADEMIA", "SALAO", "TECNOLOGIA", "SOLUCOES", "ADVOGADOS", "ASSOCIADOS",
    "GRUPO", "HOLDING", "COMUNICACAO", "TELECOM", "PADARIA", "ACOUGUE", "OFICINA", "LOJA", "LOJAS", "EMPRESA",
    "INSTITUTO", "IGREJA", "FUNDACAO"}


def _empresa(texto: str) -> bool:
    """O nome é de empresa? LTDA, ME, EIRELI, S/A no fim, ou uma palavra de ramo (COMÉRCIO, FOTOS, MODAS)."""
    t = _normalizar(texto)
    return bool(_RE_SOCIEDADE.search(t)) or bool(set(re.findall(r"[A-Z]+", t)) & _PALAVRAS_EMPRESA)


def _razao_social(antigo: str) -> str:
    """Razão social no molde de ``antigo``: as mesmas iniciais na frente ("M R C FILHO FOTOS" → "M R C COSTA
    CALÇADOS"), o mesmo tipo de sociedade no fim (LTDA, ME, EIRELI, S/A, como estava escrito) e, no meio,
    "<SOBRENOME> <RAMO>", "<SOBRENOME> COMÉRCIO DE <PRODUTO>", "<SOBRENOME> & <SOBRENOME>" ou
    "<NOME> <SOBRENOME> <RAMO>"."""
    palavras = antigo.split()
    sufixo = []
    while palavras and (_normalizar(palavras[-1]).rstrip(".") in _SOCIEDADES or palavras[-1] == "-"):
        sufixo.insert(0, palavras.pop())
    iniciais = 0
    while iniciais < len(palavras) - 1 and len(palavras[iniciais].rstrip(".")) == 1 and palavras[iniciais][0].isalpha():
        iniciais += 1
    if iniciais:
        ponto = palavras[0].endswith(".")
        partes = [_sortear(_rng.choice(("nomes_f", "nomes_m")))[0] + ("." if ponto else "") for _ in range(iniciais)]
        partes.append(_acentuar_nome(_sortear("sobrenomes")) if _rng.random() < 0.8
                      else _rng.choice(("FILHO", "JÚNIOR", "NETO")))
        if set(_normalizar(" ".join(palavras)).split()) & _PALAVRAS_EMPRESA:
            partes.append(_rng.choice(_RAMOS))
    else:
        modelo = _rng.choices(("ramo", "comercio", "socios", "dono"), (40, 25, 20, 15))[0]
        s = _acentuar_nome(_sortear("sobrenomes"))
        if modelo == "ramo":
            partes = [s, _rng.choice(_RAMOS)]
        elif modelo == "comercio":
            partes = [s, "COMÉRCIO DE", _rng.choice(_PRODUTOS)]
        elif modelo == "socios":
            partes = [s, "&", _acentuar_nome(_sortear("sobrenomes"))]
        else:
            partes = [_acentuar_nome(_sortear(_rng.choice(("nomes_f", "nomes_m")))), s, _rng.choice(_RAMOS)]
    return " ".join(partes + sufixo)


# ---------------------------------------------------------------------------
# endereço e bairro
# ---------------------------------------------------------------------------

_PESOS_TIPOS = [p for _, p, _ in _TIPOS]
_BENFORD = [math.log10(1 + 1 / d) for d in range(1, 10)]
# palavras (sem acento) que marcam número ou complemento: o nome do logradouro acaba nelas
_MARCAS = frozenset("N NO NUM NUMERO NRO AP APTO APT APART APARTAMENTO CASA CS BL BLOCO BLC LOJA LJ SALA SL QD QUADRA "
                    "LT LOTE CJ CONJ CONJUNTO KM FUNDOS FUNDO FDS FRENTE ANDAR TORRE ED EDIF EDIFICIO COND CONDOMINIO "
                    "BOX GALPAO SOBRADO TERREO SN".split())
_MARCAS_APTO = frozenset("AP APTO APT APART APARTAMENTO SALA SL".split())  # andar + unidade: 302, 1104
_RE_MARCA_COM_DIGITO = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z]*[01])[A-Za-z01]{2,}(?![A-Za-z0-9])")


def _marca_do_ocr(texto: str) -> str:
    """A marca de complemento que o OCR leu com dígito no lugar da letra ("APT0 38" → "APTO 38", "BL0C0" → "BLOCO",
    "L0TE" → "LOTE"): sem isso o 0 virava número e o endereço novo saía "APT3 94". Só troca quando o resultado é
    uma marca conhecida (BR101 e afins ficam como estão)."""
    def troca(m):
        s = m.group()
        t = s.upper().translate(_CONFUSOES_OCR)
        return "".join(c if c.isalpha() else (t[j].lower() if any(x.islower() for x in s) else t[j])
                       for j, c in enumerate(s)) if t in _MARCAS else s
    return _RE_MARCA_COM_DIGITO.sub(troca, texto)
_MARCAS_LETRA = frozenset("BL BLOCO BLC TORRE QD QUADRA LT LOTE CJ CONJ CASA SALA LOJA ED EDIFICIO".split())
_SEM_VALOR = frozenset("FUNDOS FUNDO FDS FRENTE TERREO SOBRADO".split())
_RE_TOKEN = re.compile(r"\d+|[^\W\d_]+|\s+|.")
# Rua de verdade (base de logradouros) no molde do original: o tipo vem por extenso no CNEFE e sai abreviado como o
# original estava (só os tipos que têm abreviação: RUA → R, AVENIDA → AV...; BECO e VIELA, só por extenso); os tipos
# rurais (FAZENDA, LINHA, POVOADO, SÍTIO) e os códigos (RUA 34, RUA A, PROJETADA) só quando o original é assim
_ABREV_TIPO = {t: a for t, _, a in _TIPOS}
_TIPOS_URBANOS = frozenset(_ABREV_TIPO) | {"BECO", "VIELA"}
# o título do CNEFE (DOUTOR, PRESIDENTE, SANTA...) abreviado como nas contas; o original diz se abrevia
_ABREV_TITULO = {"DOUTOR": "DR", "DOUTORA": "DRA", "PROFESSOR": "PROF", "PROFESSORA": "PROFA", "PRESIDENTE": "PRES",
                 "CORONEL": "CEL", "SANTA": "STA", "SANTO": "STO", "PADRE": "PE", "GENERAL": "GAL", "MARECHAL": "MAL",
                 "CAPITÃO": "CAP", "TENENTE": "TEN", "SARGENTO": "SGT", "VEREADOR": "VER", "ENGENHEIRO": "ENG",
                 "DEPUTADO": "DEP", "DESEMBARGADOR": "DES", "SENADOR": "SEN", "GOVERNADOR": "GOV", "MONSENHOR": "MONS",
                 "MAJOR": "MAJ", "ALMIRANTE": "ALM", "BRIGADEIRO": "BRIG", "PREFEITO": "PREF", "COMENDADOR": "COM",
                 "CONSELHEIRO": "CONS", "MINISTRO": "MIN", "COMANDANTE": "CMTE", "NOSSA SENHORA": "N SRA",
                 "DONA": "D", "DOM": "D", "FREI": "FR"}
_TITULO_ABREVIADO = frozenset(_normalizar(a).split()[0] for a in _ABREV_TITULO.values()) - {"D", "N"}
_TITULO_EXTENSO = frozenset(_normalizar(t).split()[0] for t in _ABREV_TITULO) | {"SAO", "BARAO", "VISCONDE", "DUQUE"}
# a marca antes do número → o elemento de complemento do CNEFE cujos valores ele imita (QD 12, LT 5, CASA 2, BL 3)
_ELEMENTO_DA_MARCA = {"QD": "QUADRA", "QUADRA": "QUADRA", "LT": "LOTE", "LOTE": "LOTE", "CASA": "CASA", "CS": "CASA",
                      "BL": "BLOCO", "BLOCO": "BLOCO", "BLC": "BLOCO", "TORRE": "TORRE", "LOJA": "LOJA", "LJ": "LOJA"}
# como cada elemento de complemento sai impresso numa conta (o mais comum primeiro); os outros do CNEFE (SÍTIO,
# PORÃO, BARRACÃO...) não saem num endereço de cidade
_IMPRESSO = {"APARTAMENTO": (("APTO", 45), ("AP", 35), ("APT", 12), ("APARTAMENTO", 8)),
             "CASA": (("CASA", 85), ("CS", 15)), "BLOCO": (("BL", 55), ("BLOCO", 45)), "LOTE": (("LT", 60), ("LOTE", 40)),
             "QUADRA": (("QD", 65), ("QUADRA", 35)), "FUNDOS": (("FUNDOS", 80), ("FDS", 20)),
             "FRENTE": (("FRENTE", 1),), "SOBRADO": (("SOBRADO", 1),), "TÉRREO": (("TÉRREO", 1),),
             "ANDAR": (("ANDAR", 1),), "TORRE": (("TORRE", 70), ("TR", 30)),
             "CONJUNTO": (("CJ", 50), ("CONJ", 25), ("CONJUNTO", 25)), "SALA": (("SALA", 70), ("SL", 30)),
             "LOJA": (("LOJA", 70), ("LJ", 30))}
# o que vem junto do número (123-A, 45 FUNDOS); SMS, FNS, FUNASA, COELBA... (número pintado por agente de saúde ou
# pela distribuidora) e KM não saem numa conta
_MODIFICADORES = frozenset("A B C D F G H FUNDOS FRENTE SOBRADO ALTOS TÉRREO".split()) | {f"CASA {n}" for n in "1234"}
_DF = "5300108"  # Brasília: o endereço é o código da quadra ("SQN 211 BLOCO K", tipo EDF no CNEFE)
_UNIDADES = frozenset("AP APTO APT APART APARTAMENTO CASA CS LOTE LT LOJA LJ SALA SL".split())


def _benford() -> int:
    """Primeiro dígito de número de casa: o 1 sai mais que o 9 (lei de Benford)."""
    return _rng.choices(range(1, 10), weights=_BENFORD)[0]


def _andar(alto: bool = True) -> int:
    """Andar de apartamento: quase sempre baixo (1–4), às vezes médio, raramente acima do 10º (``alto``)."""
    faixas = ((1, 4), (5, 10), (11, 20)) if alto else ((1, 4), (5, 9))
    return _rng.randint(*_rng.choices(faixas, (55, 33, 12)[:len(faixas)])[0])


def _numero_novo(velho: str, marca: str = "", depois: str = "") -> str:
    """Número novo com a quantidade de dígitos do velho (cabe no lugar) e a cara do que é: apartamento é andar +
    unidade (302, 1104), andar é pequeno, número de casa começa mais pelo 1 que pelo 9. Com as estatísticas de
    endereço do CNEFE, pela distribuição real: andar e unidade do apartamento, os valores de quadra, lote, casa e
    bloco, e o primeiro dígito do número da casa no porte da cidade da versão."""
    n = len(velho)
    est = _estatisticas()
    novo = velho
    for _ in range(8):
        if marca in _MARCAS_APTO and est and 1 <= n <= 4:
            novo = _apartamento(est, n)
        elif marca in _MARCAS_APTO and n == 2:
            novo = f"{_rng.randint(1, 9)}{_rng.randint(1, 8)}"
        elif marca in _MARCAS_APTO and n in (3, 4):  # 4 dígitos: o antigo era acima do 10º andar
            novo = f"{_andar(alto=False) if n == 3 else _rng.randint(10, 20)}{_rng.randint(1, 8):02d}"
        elif depois == "ANDAR":
            novo = str(_rng.randint(1, 9) if n == 1 else _rng.randint(10, 25))
        elif est and marca in _ELEMENTO_DA_MARCA and not velho.startswith("0") and (
                valor := _valor_do_elemento(est, _ELEMENTO_DA_MARCA[marca], n)):
            novo = valor
        elif velho.startswith("0"):
            novo = _digitos(n)  # "012": continua com o zero na frente
        else:
            novo = str(_primeiro_digito(est, n)) + _digitos(n - 1)
        if novo != velho:
            break
    return novo


def _porte_da_versao() -> str | None:
    """O porte (ate_20mil, 20_a_100mil, 100_a_500mil, 500mil_ou_mais) do município da versão; None sem ele."""
    cod = _cod_da_versao()
    return _lugares()["mun"][cod][5] if cod else None


def _primeiro_digito(est: dict | None, n: int, porte: str | None = None) -> int:
    """O primeiro dígito do número da casa de ``n`` dígitos como no CNEFE, no porte da cidade (``porte``, senão o da
    versão; o 1 em ~40% dos de 3 dígitos, ~60% dos de 4); sem as estatísticas, Benford."""
    if est is not None:
        tab = est["numero"]["por_porte"].get(porte or _porte_da_versao() or "", est["numero"]["nacional"])
        d = tab["primeiro_digito_por_digitos"].get(str(n)) or est["numero"]["nacional"]["primeiro_digito_por_digitos"].get(str(n))
        if d:
            return int(_rng.choices(*zip(*d.items()))[0])
    return _benford()


def _valor_do_elemento(est: dict, elemento: str, n: int) -> str | None:
    """Um valor de ``n`` dígitos do elemento de complemento (QUADRA, LOTE, CASA, BLOCO...) pela frequência no CNEFE
    (CASA 1 e 2 muito mais que CASA 9); None se o elemento não tem valor desse tamanho."""
    vs = _base(f"valores:{elemento}:{n}", lambda: [
        (v, c) for v, c in est["complementos"]["elementos"].get(elemento, {}).get("valores_top", {}).items()
        if v.isdigit() and len(v) == n and v[0] != "0"])
    return _rng.choices(*zip(*vs))[0] if vs else None


def _apartamento(est: dict, n: int) -> str:
    """Número de apartamento de ``n`` dígitos como os de verdade (enderecos_estatisticas.json): com 1 e 2 dígitos, os
    mais comuns (1, 2, 11, 12, 21...); com 3, andar de 1 a 9 e a unidade (01, 02... o 01 a 04 na maioria); com 4, andar
    10 ou mais e a unidade."""
    a = est["apartamentos"]
    if n <= 2:
        vs = _base(f"apartamento:{n}", lambda: [(v, c) for v, c in a["valores_top"].items()
                                               if v.isdigit() and len(v) == n and v[0] != "0"])
        return _rng.choices(*zip(*vs))[0] if vs else str(_rng.randint(1, 9)) + _digitos(n - 1)
    andares = _base(f"andares:{n}", lambda: [(k, c) for k, c in a["andar"].items() if (len(k) == 1) == (n == 3)])
    return _rng.choices(*zip(*andares))[0] + _rng.choices(*zip(*a["unidade"].items()))[0]


def _letra_nova(velha: str) -> str:
    nova = _rng.choice([c for c in "ABCDEFGH" if c != velha.upper()])
    return nova if velha.isupper() else nova.lower()


def _espacar(texto: str) -> str:
    """Põe de volta os espaços que o OCR comeu: "RCorreiaVasques,69,3Andar" → "R Correia Vasques,69,3 Andar"."""
    t = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])|(?<=[A-ZÀ-Þ])(?=[A-ZÀ-Þ][a-zà-ÿ])", " ", texto)
    return re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", t)


def _pecas_endereco(texto: str) -> list[list[str]]:
    """O endereço em pedaços [tipo, texto, ...] que, juntos, dão o texto: "tipo" (RUA, R., Av.), "nome" (do
    logradouro; "nome_num"/"nome_letra" na RUA 58 e na RUA A), "num" [texto, marca antes, palavra depois], "letra"
    (o A de BL A) e "fixo" (o que fica como está: separadores, Nº, APTO, CJ PREFEITO JOSÉ WALTER, o CEP colado)."""
    toks = [(m.group(), m.start()) for m in _RE_TOKEN.finditer(texto)]
    dobra = [_normalizar(t) for t, _ in toks]
    ceps = [m.span() for m in re.finditer(r"(?<!\d)\d{5}-?\d{3}(?!\d)", texto)]
    pecas = []

    def juntar(a, b):
        return "".join(t for t, _ in toks[a:b])

    def espacos(i):  # os espaços a partir de i viram um pedaço fixo
        j = i
        while j < len(toks) and toks[j][0].isspace():
            j += 1
        if j > i:
            pecas.append(["fixo", juntar(i, j)])
        return j

    def valor_depois(i):  # depois da marca vem um valor (APTO 302, BL A, Nº 45)?
        j = i + 1
        while j < len(toks) and (toks[j][0].isspace() or toks[j][0] in ".:°º"):
            j += 1
        return j < len(toks) and (toks[j][0].isdigit() or (len(toks[j][0]) == 1 and toks[j][0].isalpha()))

    i = espacos(0)
    tipo = False
    if i < len(toks) and dobra[i] in _TIPO_DE:
        k = i + 1 + (i + 1 < len(toks) and toks[i + 1][0] == ".")
        if k >= len(toks) or toks[k][0].isspace() or toks[k][0] in ",-" or toks[k][0].isdigit():
            pecas.append(["tipo", juntar(i, k)])
            i, tipo = espacos(k), True
    ini = fim = i
    while i < len(toks):  # o nome: palavras até a vírgula, o hífen, um número ou uma marca com valor
        t = toks[i][0]
        if t.isspace():
            i += 1
            continue
        if not (t.isalpha() or t in ".'") or (dobra[i] in _MARCAS and (valor_depois(i) or (
                dobra[i] in _SEM_VALOR and fim > ini))) or juntar(i, i + 3).upper() == "S/N":
            break
        i = fim = i + 1
    if fim > ini:
        nome = juntar(ini, fim)
        if tipo and len(nome) == 1:  # RUA A: outra letra
            pecas.append(["nome_letra", nome])
        else:  # RODOVIA BR 101, ROD CE 040: a sigla fica
            pecas.append(["fixo" if _normalizar(nome) in FAIXAS_UF or _normalizar(nome) == "BR" else "nome", nome])
    elif tipo and fim < len(toks) and not any(a <= toks[fim][1] < b for a, b in ceps) and (
            toks[fim][0].isdigit() or (len(toks[fim][0]) == 1 and toks[fim][0].isalpha()
                                       and not juntar(fim, fim + 3).upper() == "S/N")):
        pecas.append(["nome_num" if toks[fim][0].isdigit() else "nome_letra", toks[fim][0]])
        fim += 1
    marca = ""
    for k in range(fim, len(toks)):
        t, pos = toks[k]
        if any(a <= pos < b for a, b in ceps):
            pecas.append(["fixo", t])
        elif t.isdigit():
            depois = next((dobra[j] for j in range(k + 1, len(toks)) if toks[j][0].isalpha()
                           and dobra[j] not in ("O", "A")), "")
            pecas.append(["num", t, marca, depois])
            marca = ""
        elif len(t) == 1 and t.isalpha() and marca in _MARCAS_LETRA:
            pecas.append(["letra", t])
            marca = ""
        else:
            pecas.append(["fixo", t])
            if t.isalpha():
                marca = dobra[k]
    return pecas


def _tipo_no_estilo(velho: str, endereco: str) -> str:
    """Tipo de logradouro sorteado pela proporção do país, escrito como o velho: abreviado ou não, com ou sem ponto,
    na mesma caixa ("R." → "AV.", "Rua" → "Travessa"; "R." de uma letra só segue a caixa do ``endereco``)."""
    tipo, _, abrev = _rng.choices(_TIPOS, weights=_PESOS_TIPOS)[0]
    sem_ponto = velho.rstrip(".")
    abreviado = _normalizar(sem_ponto) != _normalizar(_TIPO_DE[_normalizar(sem_ponto)])
    novo = abrev + ("." if velho.endswith(".") else "") if abreviado else tipo
    return _caixa(velho if len(sem_ponto) > 1 else endereco, _grafia(novo))


def _endereco_no_molde(antigo: str) -> str:
    """Endereço novo no MOLDE do antigo: o tipo no mesmo estilo (RUA/R./Av.; RUA 58 e RUA A continuam RUA), outro
    nome de comprimento parecido, números com os mesmos dígitos, letra de bloco trocada; separadores, "Nº", APTO,
    CJ e o CEP ficam como estavam. Com a base de lugares, a rua é de verdade, da cidade da versão (sem ela, de uma
    cidade sorteada pela população), com o tipo e o título abreviados como no original (``_rua_no_molde``); no DF, o
    endereço de quadra ("SQN 211 BLOCO K APTO 302") vira outro de quadra. Sem a base, um nome do vocabulário."""
    a = _marca_do_ocr(antigo.strip())
    if " " not in a and len(a) > 8:
        a = _espacar(a)
    pecas = _pecas_endereco(a)
    tipo_velho = next((p[1] for p in pecas if p[0] == "tipo"), "")
    nome_velho = next((p[1] for p in pecas if p[0] == "nome"), None)
    tipo_novo, nome_novo = tipo_velho, None
    rua = cod = None
    if _lugares() is not None and any(p[0] in ("nome", "nome_num", "nome_letra") for p in pecas):
        cod = _cod_da_versao() or _municipio(_sortear("cidades"))
        rua = _rua_no_molde(a, pecas, cod)
    if rua and "df" in rua:  # o endereço de quadra novo e o resto (APTO 302, CASA 10) com outros números
        return rua["df"] + "".join(_peca_nova(p) for p in _pecas_endereco(a[rua["fim"]:]))
    if rua and "nome" in rua and len(rua["tipo"] + rua["nome"]) > 1.25 * len(tipo_velho + (nome_velho or "")) + 2:
        rua = None  # nenhuma rua da cidade (do split) cabe no lugar ("RUA SEIS"): um nome do vocabulário embutido
        if _lugar_da_versao() is not None:
            _lugar_da_versao()["linha"] = None
    if rua and "nome" in rua:
        tipo_novo = _caixa(tipo_velho if len(tipo_velho.rstrip(".")) > 1 else a, _grafia(rua["tipo"])) if rua["tipo"] else ""
        nome_novo = _caixa(nome_velho, _grafia_lugar(rua["nome"]))
    elif nome_velho is not None:
        tipo_novo = _tipo_no_estilo(tipo_velho, a) if tipo_velho else ""

        def falta(nome, tipo):  # quanto o endereço novo fica mais curto (ou mais comprido) que o antigo
            return abs(len(nome) + len(tipo) - len(nome_velho) - len(tipo_velho))
        cands = []
        for _ in range(4):  # rodadas de 12 até um nome caber (±15% do endereço inteiro): nome comprido é raro
            cands += [_grafia(_sortear("ruas")) for _ in range(12)]
            if min(falta(r, tipo_novo) for r in cands) <= max(1.0, 0.15 * len(a)):
                break
        else:  # AVENIDA PRESIDENTE JUSCELINO KUBITSCHEK: com RUA não há nome que encha; fica o tipo antigo
            tipo_novo = tipo_velho
        nome_novo = _caixa(nome_velho, min(cands, key=lambda r: falta(r, tipo_novo)))
    if nome_novo is not None:
        primeira = nome_novo.split(" ")[0]
        if tipo_velho and primeira.lower() in _PARTICULAS_MINUSCULAS and _estilo(nome_velho) not in ("A", "a"):
            nome_novo = primeira.lower() + nome_novo[len(primeira):]  # "Rua das Flores", não "Rua Das Flores"
    # só com a cidade da versão conhecida: sem ela, o bairro escrito é o do próprio documento e fica
    bairro_entre = _bairro_entre(pecas, cod) if cod is not None and cod == _cod_da_versao() else None
    out = []
    for j, p in enumerate(pecas):
        if bairro_entre and j in bairro_entre:
            out.append(bairro_entre[j])
        elif p[0] == "tipo":
            out.append(tipo_novo)
        elif p[0] == "nome":
            out.append(nome_novo)
        elif rua and p[0] in rua:  # RUA 58 → RUA 34, RUA A → RUA C: a rua de número (ou letra) de verdade
            out.append(rua[p[0]])
        else:
            out.append(_peca_nova(p))
    return "".join(out).lstrip()  # no DF, a rua de quadra entra sem o tipo


_RE_MARCA_BAIRRO = re.compile(r"(\s*,?\s*(?:CJ|CONJ|CONJUNTO|JD|JARDIM|VL|VILA|BAIRRO|LOTEAMENTO|RES|RESIDENCIAL|PQ|PARQUE)"
                              r"\.?\s+)(.*?[A-Za-zÀ-ÿ]{3}.*?)(\s*,?\s*)$", re.I)


def _bairro_entre(pecas: list, cod: str | None) -> dict | None:
    """O bairro escrito entre a rua e o número ("R 47 CJ PREFEITO JOSE WALTER 312") quando ele NÃO existe na cidade
    ``cod`` da versão (a cidade mudou): {índice do pedaço: texto novo}, com o bairro da rua nova depois da mesma
    marca (CJ, JD, VILA...) e os outros pedaços do trecho vazios. None se não há bairro escrito ou se ele é da cidade."""
    i = next((i for i, p in enumerate(pecas) if p[0] in ("nome", "nome_num", "nome_letra")), None)
    if i is None or cod is None:
        return None
    entre = list(itertools.takewhile(lambda j: pecas[j][0] != "num", range(i + 1, len(pecas))))
    m = _RE_MARCA_BAIRRO.match("".join(pecas[j][1] for j in entre))
    if not m or _chave_bairro(m.group(2)) in (_bairros(cod) or {}).get("chaves", ()):
        return None
    linha = (_lugar_da_versao() or {}).get("linha")  # o bairro da rua nova; sem ela, um bairro da cidade
    novo = _grafia_lugar(linha[3]) if linha and linha[3] else _bairro_da_cidade(cod)
    if not novo:
        return None
    marca, primeira = _chave_bairro(m.group(1)), _normalizar(novo).split(" ")[0]
    antes = m.group(1)
    if marca and primeira == marca and " " in novo:  # "CJ" + "CONJUNTO CEARÁ" → "CJ CEARÁ"
        novo = novo.split(" ", 1)[1]
    elif primeira in _MARCAS_DE_BAIRRO:  # o bairro novo já diz o que é ("VILA JARAGUÁ", "SETOR OESTE"): sai o CJ
        antes = re.match(r"\s*,?\s*", antes).group()
    return {j: (antes + _caixa(m.group(2), novo) + m.group(3)) if k == 0 else "" for k, j in enumerate(entre)}


_MARCAS_DE_BAIRRO = frozenset("VILA SETOR JARDIM PARQUE CONJUNTO RESIDENCIAL LOTEAMENTO BAIRRO NUCLEO CONDOMINIO "
                              "CHACARA SITIO ALTO BAIXO VALE".split())


def _peca_nova(p: list) -> str:
    """O pedaço (de ``_pecas_endereco``) no endereço novo: número e letra trocados, o resto como estava."""
    if p[0] in ("nome_num", "num"):
        return _numero_novo(*p[1:])
    if p[0] in ("nome_letra", "letra"):
        return _letra_nova(p[1])
    return p[1]


def _rua_no_molde(a: str, pecas: list, cod: str | None) -> dict | None:
    """A rua nova, de verdade, da cidade ``cod`` (``_ruas``), para o molde ``pecas`` do endereço ``a``: {"tipo",
    "nome"} impressos no estilo do original (tipo abreviado como estava, com ou sem ponto; o título — DR., PROF.,
    PRES., STA. — abreviado se o original tinha título abreviado; nome sem número se o original não tinha), e de
    comprimento parecido; {"nome_num"/"nome_letra"} na RUA 58 e na RUA A (outra rua de número, ou de letra, do mesmo
    tipo); {"df", "fim"} no endereço de quadra do DF (outro endereço de quadra do mesmo feitio e onde acaba o antigo).
    A rua combina com o resto da versão (``_escolher_rua``) e fica guardada em ``["lugar"]``: o bairro e o CEP
    trocados depois saem da mesma linha; na mesma versão, o mesmo endereço antigo dá a mesma rua (se ela cabe: a que
    não cabe é sorteada de novo a cada chamada, e quem chama escolhe pela largura). None se a cidade não tem rua que
    sirva (fica o vocabulário embutido)."""
    e = _ruas(cod)
    if e is None:
        return None
    lugar, chave = _lugar_na_cidade(cod), logradouro_de(a)
    if lugar is not None and chave in lugar["ruas"] and lugar["ruas"][chave][0] == cod:
        lugar["linha"] = lugar["ruas"][chave][1]
        return lugar["ruas"][chave][2]
    tipo_velho = next((p[1] for p in pecas if p[0] == "tipo"), "")
    i, p = next((i, p) for i, p in enumerate(pecas) if p[0] in ("nome", "nome_num", "nome_letra"))
    seguinte = next((q for q in pecas[i + 1:] if q[1].strip()), ["", ""])
    # o bairro escrito depois da rua ("R 47 CJ PREFEITO JOSE WALTER 312"): a rua nova sai dele, quando dá
    escrito = _chave_bairro("".join(q[1] for q in itertools.takewhile(lambda q: q[0] != "num", pecas[i + 1:])))
    escrito = escrito if escrito and escrito in (_bairros(cod) or {}).get("chaves", ()) else None
    df = not tipo_velho and p[0] == "nome" and re.fullmatch(r"[A-Z]{2,5}", p[1]) and seguinte[0] == "num"
    if df and cod == _DF:  # SQN 211 BLOCO K | APTO 302: o endereço de quadra vai até a unidade
        fim = len(a)
        for m in _RE_TOKEN.finditer(a):
            if m.start() > 0 and _normalizar(m.group()) in _UNIDADES:
                fim = m.start()
                break
        prefixo = a[:fim].rstrip(" ,-")
        feitio = {w for w in _normalizar(prefixo).split() if not w.isdigit() and len(w) > 1} - {p[1]}
        tolerancia = max(1.0, 0.15 * len(prefixo))

        def medida(r):  # o comprimento e o feitio (BLOCO, CONJUNTO) do endereço de quadra antigo
            return abs(len(r[2]) - len(prefixo)) + 4 * len(
                feitio ^ ({w for w in r[2].split() if not w.isdigit() and len(w) > 1} - {r[2].split()[0]}))
        linha = _escolher_rua(cod, e, _candidatas(cod, e, "edf"), medida,
                              lambda r: _normalizar(r[2]) == _normalizar(prefixo), tolerancia)
        res = linha and {"df": _caixa(prefixo, _grafia_lugar(linha[2])), "fim": len(prefixo)}
    elif p[0] != "nome":  # RUA 58, RUA A: outra rua de número (de letra) do mesmo tipo
        tipo = _normalizar(_TIPO_DE[_normalizar(tipo_velho.rstrip("."))])
        forma = "num" if p[0] == "nome_num" else "letra"
        tolerancia, medida = 0, (lambda r: 0)  # o número (a letra) tem o tamanho do antigo
        linha = _escolher_rua(cod, e, _candidatas(cod, e, forma, tipo, len(p[1])), medida,
                              lambda r: r[2] == p[1].upper(), tolerancia, escrito)
        res = linha and {p[0]: linha[2] if forma == "num" else _caixa(p[1], linha[2])}
    else:
        sem_ponto = tipo_velho.rstrip(".") or ("R" if df else "")  # quadra fora do DF: entra o tipo, abreviado
        abrev = bool(sem_ponto) and _normalizar(sem_ponto) != _normalizar(_TIPO_DE[_normalizar(sem_ponto)])
        ponto = tipo_velho.endswith(".")
        titulo_abrev, titulo_ponto = _estilo_titulo(p[1], abrev, ponto)
        digitos = any(c.isdigit() for c in p[1])
        cands = _candidatas(cod, e, "abrev" if abrev else "extenso", digitos=digitos)
        if not cands and cod == _DF:  # rua de nome quase não há no DF: entra o endereço de quadra, sem o tipo
            cands = _candidatas(cod, e, "edf")

        def impresso(r):  # (tipo, título + nome) no estilo do original; sem tipo no original, sem tipo no novo
            if r[0] == "EDF":
                return "", r[2]
            t = (_ABREV_TIPO[r[0]] + ("." if ponto else "")) if abrev else r[0] if sem_ponto else ""
            ti = r[1]
            if ti and titulo_abrev and ti in _ABREV_TITULO:
                ti = " ".join(w + "." for w in _ABREV_TITULO[ti].split()) if titulo_ponto else _ABREV_TITULO[ti]
            nome = " ".join(filter(None, (ti, r[2])))
            return ("", f"{t} {nome}") if df else (t, nome)
        # a rua antiga (o mesmo nome e o mesmo tipo: a TRAVESSA ÁLVARO CORREIA não é a AVENIDA ÁLVARO CORREIA)
        velho = _normalizar(p[1]).replace(".", "")
        tipo_antigo = _normalizar(_TIPO_DE[_normalizar(sem_ponto)]) if tipo_velho else None
        tolerancia = max(1.0, 0.15 * len(a))

        def medida(r):  # a diferença de comprimento (±15% do endereço); além disso, a mais comprida pesa 3 vezes:
            d = sum(map(len, impresso(r))) - len(p[1]) - len(tipo_velho)  # a mais curta ainda cabe no lugar
            return abs(d) if d <= tolerancia else tolerancia + 3 * (d - tolerancia)
        linha = _escolher_rua(cod, e, cands, medida,
                              lambda r: velho in (_normalizar(r[2]), _normalizar(f"{r[1]} {r[2]}")) and (
                                  tipo_antigo is None or _normalizar(r[0]) == tipo_antigo), tolerancia, escrito)
        res = linha and dict(zip(("tipo", "nome"), impresso(linha)))
    if not res:
        return None
    if lugar is not None:  # a rua que não cabe não fica presa à versão: a próxima chamada sorteia outra
        if medida(linha) <= tolerancia:
            lugar["ruas"][chave] = (cod, linha, res)
        lugar["linha"] = linha
    return res


def logradouro_de(texto: str) -> str:
    """O logradouro do endereço, sem número nem complemento, para comparar ("R. das Flores, 123 ap 4" → "RUA DAS
    FLORES", "RUA 47 CJ ... 312" → "RUA 47"); sem logradouro reconhecível, o texto inteiro sem acento."""
    a = str(texto or "").strip()
    if " " not in a and len(a) > 8:
        a = _espacar(a)
    pecas = _pecas_endereco(a)
    nome = next((p[1] for p in pecas if p[0] in ("nome", "nome_num", "nome_letra")), "")
    if not nome:
        return _normalizar(a)
    tipo = next((_TIPO_DE[_normalizar(p[1].rstrip("."))] for p in pecas if p[0] == "tipo"), "")
    return _normalizar(f"{tipo} {nome}")


def _complemento(porte: str | None = None) -> str:
    """Complemento como nas contas: na maioria nenhum; apartamento (andar + unidade: 302, 1104), casa, bloco,
    fundos, loja, sala, quadra e lote. Com as estatísticas do CNEFE, pela distribuição do ``porte`` da cidade: a
    fração com complemento (22% até 20 mil habitantes, 65% acima de 500 mil), a combinação na ordem de lá (CASA,
    APARTAMENTO, BLOCO + APARTAMENTO, QUADRA + LOTE, FUNDOS...), escrita como nas contas (APTO/AP, BL, QD, LT), e o
    valor de cada elemento (CASA 1 e 2, BL A e B, o andar e a unidade do apartamento)."""
    est = _estatisticas()
    if est is not None:
        c = est["complementos"]["por_porte"].get(porte or "") or est["complementos"]
        if _rng.random() >= c["fracao_com_complemento"]:
            return ""
        combos = _base(f"combinacoes:{porte}", lambda: [(k, n) for k, n in c["combinacoes"].items()
                                                        if all(el in _IMPRESSO for el in k.split("+"))])
        partes = []
        for el in _rng.choices(*zip(*combos))[0].split("+"):
            marca, valor = _rng.choices(*zip(*_IMPRESSO[el]))[0], _valor_complemento(est, el)
            partes.append(f"{valor} {marca}" if el == "ANDAR" and valor else f"{marca} {valor}".strip())
        return _rng.choices((" ", " - ", ", "), (55, 30, 15))[0] + _grafia(" ".join(partes))
    tipo = _rng.choices(("", "apto", "casa", "bloco", "fundos", "loja", "sala", "quadra"),
                        (60, 12, 7, 4, 3, 2, 2, 3))[0]
    if not tipo:
        return ""
    unidade = f"{_andar()}{_rng.randint(1, 8):02d}"
    if tipo == "apto":
        texto = f"{_rng.choice(('APTO', 'AP', 'APT'))} {unidade}"
    elif tipo == "casa":
        texto = f"CASA {_rng.randint(1, 6) if _rng.random() < 0.8 else _rng.choice('ABC')}"
    elif tipo == "bloco":
        texto = f"{_rng.choice(('BL', 'BLOCO'))} {_rng.choice('ABCDEFGH')} {_rng.choice(('AP', 'APTO'))} {unidade}"
    elif tipo == "fundos":
        texto = "FUNDOS"
    elif tipo == "loja":
        texto = f"LOJA {_rng.randint(1, 20)}"
    elif tipo == "sala":
        texto = f"SALA {unidade}"
    else:
        texto = f"QD {_rng.randint(1, 99)} LT {_rng.randint(1, 40)}"
    return _rng.choices((" ", " - ", ", "), (55, 30, 15))[0] + texto


def _valor_complemento(est: dict, elemento: str) -> str:
    """O valor de um elemento de complemento pela frequência no CNEFE: o número do apartamento (dígitos e andar +
    unidade), o andar, e nos outros os valores curtos mais vistos (CASA 1, CASA A, BL B, QD 12; "" = só a marca:
    CASA, FUNDOS)."""
    if elemento == "APARTAMENTO":
        n = int(_rng.choices(*zip(*((k, c) for k, c in est["apartamentos"]["digitos"].items() if int(k) <= 4)))[0])
        return _apartamento(est, n)
    vs = _base(f"valores:{elemento}", lambda: [
        (v, c) for v, c in est["complementos"]["elementos"][elemento]["valores_top"].items()
        if re.fullmatch(r"[1-9]\d{0,2}|[A-Z]|", v) and not (elemento == "ANDAR" and v == "")])
    return _rng.choices(*zip(*vs))[0] if vs else ""


def _numero_da_casa(est: dict, porte: str | None) -> str:
    """O número da casa como no CNEFE do porte da cidade: S/N na fração de lá (36% até 20 mil habitantes, 13% acima
    de 500 mil), a quantidade de dígitos e o primeiro dígito de lá e, às vezes, o que vem junto (123-A, 45 FUNDOS)."""
    num = est["numero"]["por_porte"].get(porte or "") or est["numero"]["nacional"]
    d = int(_rng.choices(*zip(*((k, c) for k, c in num["digitos"].items() if int(k) <= 5)))[0])
    if not d:
        return _rng.choices(("S/N", "SN"), (80, 20))[0]
    numero = str(_primeiro_digito(est, d, porte)) + _digitos(d - 1)
    mods = est["modificadores"]["por_porte"].get(porte or "") or est["modificadores"]["nacional"]
    juntos = _base(f"modificadores:{porte}", lambda: [(m, c) for m, c in mods.items() if m in _MODIFICADORES])
    if juntos and _rng.random() < sum(c for _, c in juntos) / num["n"]:
        m = _rng.choices(*zip(*juntos))[0]
        numero += _rng.choices(("-", " ", ""), (50, 30, 20))[0] + m if len(m) == 1 else " " + m
    return _grafia(numero)


def _endereco_novo() -> str:
    """Endereço inteiro, sem original para imitar. Com a base de lugares: uma rua de verdade da cidade da versão (sem
    ela, de uma cidade sorteada pela população; no DF, o endereço de quadra), o tipo por extenso (AVENIDA às vezes AV),
    o número e o complemento pela distribuição do porte da cidade (``_numero_da_casa``, ``_complemento``). Sem a base:
    tipo pela proporção do país, número de 2–3 dígitos na maioria (começando mais pelo 1), complemento como nas
    contas."""
    cod = _cod_da_versao() or (_municipio(_sortear("cidades")) if _lugares() is not None else None)
    e, est = _ruas(cod), _estatisticas()
    if e is not None and est is not None:
        cands = _candidatas(cod, e, "extenso", digitos=True) or _candidatas(cod, e, "edf")
        if cands:
            tipo, titulo, nome = e["linhas"][_rng.choice(cands)][:3]
            if tipo == "AVENIDA" and _rng.random() < 0.4:
                tipo = "AV"
            porte = _lugares()["mun"][cod][5]
            rua = nome if tipo == "EDF" else " ".join(filter(None, (tipo, titulo, nome)))
            return f"{_grafia_lugar(rua)}, {_numero_da_casa(est, porte)}{_complemento(porte)}"
    tipo, _, abrev = _rng.choices(_TIPOS, weights=_PESOS_TIPOS)[0]
    if tipo == "AVENIDA" and _rng.random() < 0.4:
        tipo = abrev
    digitos = _rng.choices((1, 2, 3, 4, 5), (8, 27, 40, 23, 2))[0]
    numero = "S/N" if _rng.random() < 0.03 else str(_benford()) + _digitos(digitos - 1)
    return f"{tipo} {_sortear('ruas')}, {numero}{_complemento()}"


_PREFIXOS_GENERICOS = frozenset(p for p, _ in _PREFIXOS_BAIRRO)
_GENERICOS = frozenset(map(_normalizar, _EMBUTIDO["bairros"]))
_DONOS_BAIRRO: dict[str, set[tuple[str, str]]] = {}
for _b, _c, _uf in _BAIRROS_CIDADE:
    _DONOS_BAIRRO.setdefault(_normalizar(_b), set()).add((_normalizar(_c), _uf))


def _proprios(split: str | None) -> dict:
    """Os bairros próprios de cada cidade (embutidos) que o split usa: {(CIDADE, UF): [bairros]}."""
    def montar():
        d = {}
        for b, c, uf in _tabela("bairros_cidade", split)[0]:
            d.setdefault((_normalizar(c), uf), []).append(b)
        return d
    return _base(f"proprios:{split if split in _SPLITS else None}", montar)


def cidades_do_bairro(bairro: str) -> set[tuple[str, str]] | None:
    """Onde o bairro existe: {(CIDADE, UF)} sem acento para os bairros próprios de uma cidade (PARANOÁ →
    {("BRASILIA", "DF")}); vazio para os genéricos (CENTRO, JARDIM X, VILA Y: há em toda cidade); None se não
    conhece."""
    k = _normalizar(bairro or "")
    if k in _DONOS_BAIRRO:
        return set(_DONOS_BAIRRO[k])
    if k in _GENERICOS or k.split()[:1] and k.split()[0] in _PREFIXOS_GENERICOS:
        return set()
    return None


def _bairro_novo(cidade: tuple[str, str] | None = None) -> str:
    """Bairro novo: com a base de lugares, um bairro de verdade da cidade, pelos domicílios (``_bairro_da_cidade``);
    sem ela (ou sem cidade), um dos próprios da cidade (ALDEOTA só em Fortaleza) ou um genérico, da lista (CENTRO,
    BELA VISTA) ou composto (JARDIM/VILA/PARQUE + um nome), que serve em qualquer cidade."""
    novo = _bairro_da_cidade(_municipio(cidade)) if cidade else None
    if novo:
        return novo
    proprios = None
    if cidade:
        proprios = _proprios(_split_atual.get()).get((_normalizar(cidade[0]), _normalizar(cidade[1] or "")))
    r = _rng.random()
    if proprios and r < 0.35:
        return _rng.choice(proprios)
    if r < 0.7:
        return _sortear("bairros")
    return _composto(lambda: _sortear("ruas"))


def _composto(base) -> str:
    """Bairro genérico composto: um prefixo (JARDIM, VILA, PARQUE...) e ``base()``; nunca um bairro próprio de alguma
    cidade ("JARDIM GOIÁS" é de Goiânia)."""
    for _ in range(20):
        bairro = f"{_rng.choices(*zip(*_PREFIXOS_BAIRRO))[0]} {base()}"
        if _normalizar(bairro) not in _DONOS_BAIRRO:
            break
    return bairro


# ---------------------------------------------------------------------------
# rua, bairro e CEP de verdade, coerentes na versão (base de lugares)
# ---------------------------------------------------------------------------

def _lugar_da_versao() -> dict | None:
    """O que a versão já escolheu de endereço (``usando(documento=...)``): {"cod": o município, "linha": a linha da
    rua (tipo, título, nome, bairro, CEP, urbano), "cep" e "bairro" trocados, "ruas": {logradouro antigo: (cod,
    linha, rua impressa)}}; None fora de uma versão."""
    doc = _documento_atual.get()
    if doc is None:
        return None
    return doc.setdefault("lugar", {"cod": None, "linha": None, "cep": None, "bairro": None, "ruas": {}})


def _lugar_na_cidade(cod: str | None) -> dict | None:
    """O ``["lugar"]`` da versão no município ``cod`` (se a versão passou a outra cidade, a linha, o CEP e o bairro de
    antes não valem mais); None fora de uma versão."""
    lugar = _lugar_da_versao()
    if lugar is not None and lugar["cod"] != cod:
        lugar.update(cod=cod, linha=None, cep=None, bairro=None)
    return lugar


def _do_documento(categoria: str) -> str:
    """O texto do 1º campo da ``categoria`` no documento da versão; "" sem ele (ou fora de uma versão)."""
    doc = _documento_atual.get()
    return next((str(f.get("texto", "")) for f in (doc["campos"] if doc else ()) if f.get("categoria") == categoria
                 and str(f.get("texto", "")).strip()), "")


def _cep_do_documento() -> str | None:
    """O CEP impresso no documento da versão (8 dígitos): o do campo de CEP ou o colado no endereço, no bairro ou na
    cidade; None sem ele."""
    for categoria in ("cep", "endereço", "bairro", "cidade/uf"):
        m = re.search(r"(?<!\d)\d{5}-?\d{3}(?!\d)", _do_documento(categoria))
        if m:
            return re.sub(r"\D", "", m.group())
    return None


def _cod_da_versao() -> str | None:
    """O município da versão na base de lugares: o de ``usando(cidade=...)``, senão o impresso no documento (o campo
    de cidade/UF ou, sem ele, o dono do CEP impresso); None sem nenhum (ou sem a base)."""
    if _lugares() is None:
        return None
    c = cidade_atual()
    cod = _municipio(c) if c else None
    doc = _documento_atual.get()
    if cod is None and doc is not None:
        if "cod_doc" not in doc:
            cidade = cidade_do_texto(_do_documento("cidade/uf"))
            doc["cod_doc"] = (_municipio(cidade) if cidade else None) or _dono_do_cep(_cep_do_documento())
        cod = doc["cod_doc"]
    return cod


def _candidatas(cod: str, e: dict, forma: str, tipo: str = "", n: int = 0, digitos: bool = False) -> list:
    """As linhas de ``e = _ruas(cod)`` que servem (índices), do split da vez, de CEP típico (``_cep_tipico``), só as
    urbanas se houver: "cep" (qualquer rua), "extenso"/"abrev" (rua com nome de um tipo de cidade que se escreve por
    extenso / que tem abreviação; sem código — RUA 34, RUA A, PROJETADA — e, sem ``digitos``, sem número no nome),
    "edf" (endereço de quadra do DF: "SQN 211 BLOCO K"), "num"/"letra" (do ``tipo`` sem acento, com nome de ``n``
    dígitos / de uma letra). Guardadas na entrada da cidade."""
    split = _split_atual.get() if _split_atual.get() in _SPLITS else None
    chave = (split, forma, tipo, n, digitos)
    if chave not in e["cands"]:
        unico = _lugares()["mun"][cod][6]

        def serve(r):
            t, _ti, nome, _b, cep, _u = r
            if not _cep_tipico(cep, unico):
                return False
            if forma == "cep":
                return True
            if forma == "edf":
                return t == "EDF" and bool(re.match(r"[A-Z]{2,5} \d", nome))
            if forma in ("num", "letra"):
                return _normalizar(t) == tipo and (nome.isdigit() and len(nome) == n if forma == "num"
                                                   else len(nome) == 1 and nome.isalpha())
            return (t in (_ABREV_TIPO if forma == "abrev" else _TIPOS_URBANOS) and len(nome) > 2
                    and (digitos or not any(c.isdigit() for c in nome)) and not nome.startswith(("PROJETAD", "SEM ")))
        idx = [i for i, r in enumerate(e["linhas"]) if (split is None or e["fatia"][i] == split) and serve(r)]
        e["cands"][chave] = [i for i in idx if e["linhas"][i][5] == "1"] or idx
    return e["cands"][chave]


def _escolher_rua(cod: str, e: dict, cands: list, medida, evitar, tolerancia: float,
                  bairro_escrito: str | None = None) -> tuple | None:
    """A linha da rua nova entre ``cands`` (índices de ``e["linhas"]``), na ordem de preferência da versão: a linha
    que ela já escolheu (a do CEP trocado antes); as ruas do CEP e as do bairro que ela já trocou; as do CEP impresso
    no documento (se ele não for trocado — o padrão "nome e endereço" — a rua fica coerente com ele; na cidade de CEP
    único, qualquer rua) e as do bairro impresso (ou ``bairro_escrito`` no próprio endereço); as do mesmo prefixo de
    CEP; a cidade toda. Em cada nível, entre até 48 sorteadas, uma (ao acaso) das de ``medida`` (a diferença de
    comprimento para o antigo) ≤ ``tolerancia``; sem nenhuma, o nível seguinte e, no fim, a de medida menor de todas.
    Nunca uma de ``evitar(linha)`` (a rua antiga). None sem candidata."""
    linhas, lugar = e["linhas"], _lugar_na_cidade(cod)
    ja, cep_v, bairro_v, cep_d, bairro_d = None, None, None, None, None
    if lugar is not None:
        ja, cep_v = lugar["linha"], lugar["cep"]
        bairro_v = _chave_bairro(lugar["bairro"]) if lugar["bairro"] else None
        cep_d, bairro_d = _cep_do_documento(), _chave_bairro(_do_documento("bairro")) or None
    bairro_d, prefixo = bairro_d or bairro_escrito, (cep_v or cep_d or "")[:5]
    filtros = [f for f in (ja and (lambda i: linhas[i] == ja), cep_v and (lambda i: linhas[i][4] == cep_v),
                           bairro_v and (lambda i: e["bairro"][i] == bairro_v),
                           cep_d and (lambda i: linhas[i][4] == cep_d),
                           bairro_d and (lambda i: e["bairro"][i] == bairro_d),
                           prefixo and (lambda i: linhas[i][4][:5] == prefixo)) if f]
    melhor = None
    for f in (*filtros, None):
        sub = cands if f is None else [i for i in cands if f(i)]
        amostra = [i for i in _rng.sample(sub, min(48, len(sub))) if not evitar(linhas[i])]
        if not amostra:
            continue
        cabem = [i for i in amostra if medida(linhas[i]) <= tolerancia]  # a amostra já é ao acaso: a 1ª que cabe
        if cabem:
            return linhas[cabem[0]]
        i = min(amostra, key=lambda i: medida(linhas[i]))
        if melhor is None or medida(linhas[i]) < medida(linhas[melhor]):
            melhor = i
    return linhas[melhor] if melhor is not None else None


def _estilo_titulo(nome_velho: str, abrev: bool, ponto: bool) -> tuple[bool, bool]:
    """(abrevia?, com ponto?) o título da rua nova (DOUTOR, PRESIDENTE, SANTA): como o do original (DR. → abreviado
    com ponto, DOUTOR → por extenso); sem título no original, abreviado em metade das vezes se o tipo era abreviado."""
    w = (nome_velho.split() or [""])[0]
    k = _normalizar(w).rstrip(".")
    if k in _TITULO_ABREVIADO:
        return True, w.endswith(".")
    if k in _TITULO_EXTENSO:
        return False, False
    return abrev and _rng.random() < 0.5, ponto


def _rua_qualquer(cod: str | None = None) -> tuple | None:
    """Uma rua de verdade (a linha da base) da cidade ``cod`` (sem ela, de uma cidade sorteada pela população), do
    split da vez, com nome (sem código nem número); None sem a base ou sem rua que sirva."""
    if _lugares() is None:
        return None
    cod = cod or _municipio(_sortear("cidades"))
    e = _ruas(cod)
    cands = _candidatas(cod, e, "extenso") if e else None
    return e["linhas"][_rng.choice(cands)] if cands else None


def _acentos_lugar() -> dict:
    """Palavra sem acento → com acento, para rua e bairro (acentos.tsv.gz: só as de 20+ ocorrências no TSE e as de
    endereço postas à mão; abaixo disso há grafia de família: FALÇONI)."""
    return _base("acentos_lugar", lambda: {p: a for p, a, _f, n, o in _tsv("acentos.tsv.gz") or ()
                                           if int(n or 0) >= 20 or o == "manual"})


def _grafia_lugar(texto: str) -> str:
    """Rua ou bairro da base (já com acento) na grafia que ``usando(acentos=...)`` pede; com acento, a palavra que
    ficou sem (LUIS, ALVARO) ganha o do mapa (``_acentos_lugar``)."""
    if not _acentos_atual.get():
        return _sem_acento(texto)
    mapa = _acentos_lugar()
    return " ".join(mapa.get(w, w) if w.isascii() else w for w in texto.split(" "))


# as palavras que podem vir entre o tipo e o nome da rua: o título, por extenso ou abreviado (DOUTOR, DR, N SRA)
_PALAVRAS_DE_TITULO = (frozenset(w for t in (*_ABREV_TITULO, *_ABREV_TITULO.values()) for w in _normalizar(t).split())
                       | _TITULO_EXTENSO)


def _palavras_rua(endereco: str) -> list:
    """O endereço em palavras sem acento e pontuação, para ``_mesma_rua``."""
    return re.sub(r"[^A-Z0-9 ]", " ", _normalizar(endereco)).split()


def _mesma_rua(ws: list, linha: tuple, nome: str) -> bool:
    """O endereço (``_palavras_rua``) é o da ``linha`` da base (``nome``: o dela sem acento, ``_ruas()["nome"]``)? O
    nome vem no começo, depois do tipo (o mesmo da linha, por extenso ou abreviado) e do título (que pode vir
    abreviado: "AV DR ARNALDO" = AVENIDA DOUTOR ARNALDO), e depois dele vem o número, uma marca (APTO, CJ, QD), S/N ou
    nada ("RUA SÃO JOSÉ DOS CAMPOS" não é a RUA SÃO JOSÉ); no DF (EDF), o endereço começa pelo nome inteiro ("SQN 211
    BLOCO K")."""
    nome = nome.split()
    if not nome:
        return False
    if linha[0] == "EDF":
        return ws[:len(nome)] == nome
    for i in range(min(5, len(ws) - len(nome) + 1)):
        seguinte = ws[i + len(nome)] if i + len(nome) < len(ws) else ""
        if ws[i:i + len(nome)] == nome and (not seguinte or len(seguinte) == 1 or seguinte in _MARCAS
                                            or any(c.isdigit() for c in seguinte)):
            antes = ws[:i]
            tipo = _TIPO_DE.get(antes[0]) if antes else None
            titulos = antes[1:] if tipo else antes
            return ((tipo is None or _normalizar(tipo) == _normalizar(linha[0]))
                    and all(w in _PALAVRAS_DE_TITULO or w in _normalizar(linha[1]).split() for w in titulos))
    return False


def rua_com_cep(endereco: str, cep: str, cidade=None) -> bool | None:
    """A rua do endereço e o CEP existem juntos na base de logradouros (uma linha da rua com esse CEP)? Pelas ruas
    com esse CEP no município (``cidade``, senão o dono do CEP): True se o endereço é uma delas, False se não; None
    se a base não tem rua com esse CEP (a amostra não cobre todas as ruas) ou sem a base."""
    d = re.sub(r"\D", "", cep or "")
    if len(d) != 8:
        return None
    cod = (_municipio(cidade) if cidade else None) or _dono_do_cep(d)
    e = _ruas(cod)
    idx = [i for i, r in enumerate(e["linhas"]) if r[4] == d] if e else []
    if not idx:
        return None
    palavras = _palavras_rua(endereco)
    return any(_mesma_rua(palavras, e["linhas"][i], e["nome"][i]) for i in idx)


# abreviações de bairro (JD AMÉRICA = JARDIM AMÉRICA) e os prefixos que o nome às vezes traz e às vezes não
_ABREV_BAIRRO = {"JD": "JARDIM", "JDM": "JARDIM", "JARD": "JARDIM", "VL": "VILA", "PQ": "PARQUE", "PQE": "PARQUE",
                 "PRQ": "PARQUE", "CJ": "CONJUNTO", "CONJ": "CONJUNTO", "RES": "RESIDENCIAL", "RESID": "RESIDENCIAL",
                 "ST": "SETOR", "LOT": "LOTEAMENTO", "CID": "CIDADE", "NSA": "NOSSA", "SRA": "SENHORA", "STA": "SANTA",
                 "STO": "SANTO", "DR": "DOUTOR", "PE": "PADRE", "PREF": "PREFEITO", "PRES": "PRESIDENTE",
                 "GOV": "GOVERNADOR", "ENG": "ENGENHEIRO", "PROF": "PROFESSOR", "CEL": "CORONEL", "HAB": "HABITACIONAL",
                 "NUC": "NUCLEO", "COND": "CONDOMINIO"}
_PREFIXOS_BAIRRO_OPCIONAIS = ("CONJUNTO", "BAIRRO", "LOTEAMENTO", "RESIDENCIAL", "CONDOMINIO", "NUCLEO", "HABITACIONAL")


def _chave_bairro(bairro: str) -> str:
    """O bairro para comparar: sem acento e pontuação, com as abreviações por extenso (JD → JARDIM, CJ → CONJUNTO,
    PREF → PREFEITO) e sem o prefixo que às vezes vem e às vezes não ("CJ PREFEITO JOSÉ WALTER" = "PREFEITO JOSÉ
    WALTER"); OLHO D'ÁGUA = OLHO DÁGUA."""
    ws = [_ABREV_BAIRRO.get(w, w) for w in re.sub(r"[^A-Z0-9 ]", " ", _normalizar(bairro or "").replace("'", "")).split()]
    while len(ws) > 1 and ws[0] in _PREFIXOS_BAIRRO_OPCIONAIS:
        ws = ws[1:]
    return " ".join(ws)


def _bairro_da_cidade(cod: str | None, evitar: str | None = None) -> str | None:
    """Um bairro de verdade do município (bairros.tsv.gz), pelo nº de domicílios, entre os urbanos (se houver); None
    sem a base. ``evitar``: a chave (``_chave_bairro``) do bairro que sai."""
    bs = _bairros(cod)
    if not bs:
        return None
    if "tabela" not in bs:
        idx = ([i for i, u in enumerate(bs["urbano"]) if u and bs["dom"][i]] or
               [i for i, n in enumerate(bs["dom"]) if n] or list(range(len(bs["nomes"]))))
        bs["tabela"] = (idx, list(itertools.accumulate(bs["dom"][i] or 1 for i in idx)))
    idx, acumulado = bs["tabela"]
    novo = None
    for _ in range(10):
        novo = bs["nomes"][_rng.choices(idx, cum_weights=acumulado)[0]]
        if evitar is None or _chave_bairro(novo) != evitar:
            break
    return novo


def bairro_na_cidade(bairro: str, cidade) -> bool | None:
    """O bairro existe no município (nome, UF)? Pela base de bairros (as localidades do CNEFE e os bairros oficiais do
    IBGE, comparados por ``_chave_bairro``): True se existe nele, False se só existe em outros municípios
    (``onde_ha_bairro``), None se a base não conhece a cidade ou o bairro."""
    bs, k = _bairros(_municipio(cidade)), _chave_bairro(bairro)
    if bs is None or not k:
        return None
    if k in bs["chaves"]:
        return True
    return False if onde_ha_bairro(bairro) else None


def onde_ha_bairro(bairro: str) -> list[tuple[str, str]]:
    """Os municípios que têm o bairro, (NOME, UF) sem acento, os mais populosos primeiro; [] se nenhum (ou sem a
    base). Procura a chave do bairro (``_chave_bairro``) no texto da base de bairros sem acento (copiado na primeira
    vez: um índice de todos os bairros do país gastava 30 MB) e confere a coluna do bairro, ou a do bairro oficial do
    IBGE, de cada linha achada."""
    base, L, k = _base("bairros", lambda: _ler_por_cidade("bairros.tsv.gz")), _lugares(), _chave_bairro(bairro)
    if base is None or not k:
        return []
    texto = _base("bairros_sem_acento", lambda: unicodedata.normalize("NFKD", base["texto"]).encode(
        "ascii", "ignore").decode().replace("'", ""))
    cods = set()
    for m in re.finditer(re.escape(k) + r"(?=[\t\n])", texto):
        fim = texto.find("\n", m.start())
        c = texto[texto.rfind("\n", 0, m.start()) + 1:fim if fim >= 0 else len(texto)].split("\t")
        if len(c) > 7 and k in (_chave_bairro(c[1]), _chave_bairro(c[7])):
            cods.add(c[0])
    return [(_normalizar(L["mun"][c][0]), L["mun"][c][1]) for c in sorted(cods, key=lambda c: -L["mun"][c][2])]


def _bairro_parecido(antigo: str, cod: str) -> str:
    """O bairro novo da versão no município ``cod``: o da rua que a versão já escolheu (se ela é do bairro antigo, o de
    outro trecho dela; se cabe: até 25% mais comprido ou 60% mais curto que o antigo), o que ela já trocou, senão um
    bairro de verdade da cidade, pelos domicílios, de comprimento parecido (±15%), que fica guardado para a rua trocada
    depois ser dele."""
    lugar, k, alvo = _lugar_na_cidade(cod), _chave_bairro(antigo), len(antigo)
    linha = lugar["linha"] if lugar is not None else None
    # a rua nova é do bairro antigo: outro trecho dela, em outro bairro (a RUA 47 de outro bairro é outra rua)
    if linha and _chave_bairro(linha[3]) == k and len(linha[2]) > 2 and not any(c.isdigit() for c in linha[2]):
        e = _ruas(cod)
        outros = [r for r, b in zip(e["linhas"], e["bairro"]) if r[:3] == linha[:3] and b and b != k]
        if outros:
            linha = lugar["linha"] = _rng.choice(outros)
    if linha and linha[3] and _chave_bairro(linha[3]) != k and 0.4 * alvo <= len(linha[3]) <= 1.25 * alvo + 1:
        novo = linha[3]
    elif lugar is not None and lugar["bairro"]:
        return lugar["bairro"]
    else:
        cands = [_bairro_da_cidade(cod, k) for _ in range(24)]
        novo = next((b for b in cands if abs(len(b) - alvo) <= max(1, 0.15 * alvo)),
                    min(cands, key=lambda b: abs(len(b) - alvo)))
    if lugar is not None:
        lugar["bairro"] = novo
    return novo


def _cep_novo(velho: str, cidade=None) -> str | None:
    """O CEP novo da versão (8 dígitos) no lugar de ``velho``: da ``cidade`` (ou a da versão; sem nenhuma, a do CEP
    antigo: a cidade impressa não mudou); o da rua que a versão já escolheu, se ela trocou a rua; senão o de uma rua de
    verdade da cidade (no bairro que a versão trocou ou, se a rua do documento fica, outro trecho dela, quando há),
    que fica guardada para a rua trocada depois sair dela. None sem a base de lugares."""
    d = re.sub(r"\D", "", velho)
    cod = ((_municipio(cidade) if cidade else None) or _cod_da_versao() or _dono_do_cep(d)) if _lugares() else None
    if cod is None:
        return None
    lugar = _lugar_na_cidade(cod)
    if lugar is not None and lugar["linha"] and lugar["linha"][4] != d:
        cep = lugar["linha"][4]
    else:  # a rua impressa na versão (a trocada, ou a do documento): de preferência outro trecho dela
        rua = (None if lugar is None else _do_documento("endereço") if not lugar["ruas"]
               else (lugar["linha"][2] if lugar["linha"][0] == "EDF" else " ".join(filter(None, lugar["linha"][:3])))
               if lugar["linha"] else None)
        bairro = _chave_bairro(lugar["bairro"]) if lugar is not None and lugar["bairro"] else None
        # a rua do documento ainda não trocada: a linha do CEP novo é uma rua que caberia no lugar dela
        letras = len(logradouro_de(rua)) if rua and lugar is not None and not lugar["ruas"] else None
        cep, linha = _cep_da_cidade(cod, evitar=d, bairro=bairro, rua=rua, letras=letras)
        if lugar is not None and linha is not None:
            lugar["linha"] = linha
    if lugar is not None:
        lugar["cep"] = cep
    return cep


def gerar(categoria: str) -> dict:
    """{"texto": valor sugerido, ...extras} para a categoria."""
    if categoria == "nome":
        return {"texto": _grafia(_na_fatia(_nome_pessoa))}
    if categoria == "cpf/cnpj":
        return {"texto": cpf() if _rng.random() < 0.8 else cnpj()}
    if categoria == "endereço":
        return {"texto": _grafia(_endereco_novo())}
    if categoria == "bairro":
        return {"texto": _grafia_lugar(_bairro_novo(cidade_atual()))}
    if categoria == "cep":
        return {"texto": cep_de(*(cidade_atual() or (None, None)))}
    if categoria == "cidade/uf":
        c, uf = _sortear("cidades")
        c = _grafia(c)
        return {"texto": f"{c} - {uf}" if _rng.random() < 0.5 else f"{c}/{uf}"}
    if categoria == "data":  # uma data da conta (o papel pelo peso), perto do hoje do documento
        papel = _rng.choices(("vencimento", "emissao", "leitura", "proxima_leitura"), (45, 20, 25, 10))[0]
        return {"texto": _data_nova(papel, None).strftime("%d/%m/%Y")}
    if categoria == "mês de referência":
        d = _ciclo_da_versao()["referencia"]
        return {"texto": f"{_MESES[d.month - 1]}/{d.year}" if _rng.random() < 0.6 else d.strftime("%m/%Y")}
    if categoria == "valor":
        return {"texto": _reais(conta()["centavos"])}
    if categoria == "consumo/leitura":
        c = conta(_rng.choices(("luz", "agua", "gas"), (60, 33, 7))[0])
        return {"texto": f"{c['consumo']} {c['unidade']}"}
    if categoria in ("codigo barras", "linha digitável"):
        cod, linha, cent = par_codigo()
        return {"texto": linha if categoria == "linha digitável" else cod, "digitos": cod, "linha": linha,
                "valor": _reais(cent)}
    if categoria == "identificador documento":
        return {"texto": _identificador_gerado()}
    if categoria == "telefone":
        return {"texto": telefone()}
    if categoria == "e-mail":
        return {"texto": email()}
    if categoria == "qr code/pix":  # o payload do PIX é ASCII
        c = _sortear("cidades")[0]
        payload = codigos.pix(chave=cnpj().translate({ord(ch): None for ch in "./-"}),
                              nome="EMPRESA " + _sem_acento(_sortear("sobrenomes")), cidade=_sem_acento(c),
                              valor=f"{conta()['centavos'] / 100:.2f}", txid=_digitos(20))
        return {"texto": payload}
    return {"texto": ""}


# ---------------------------------------------------------------------------
# valor novo NO MESMO FORMATO do antigo (fraude automática)
# ---------------------------------------------------------------------------

def _caixa(antigo: str, novo: str) -> str:
    letras = [c for c in antigo if c.isalpha()]
    if letras and all(c.isupper() for c in letras):
        return novo.upper()
    if letras and all(c.islower() for c in letras):
        return novo.lower()
    if antigo.istitle() or (letras and antigo[:1].isupper() and any(c.islower() for c in letras)
                            and not any(c.isupper() for p in antigo.split()[1:] for c in p[1:])):
        # "Praca Trinta e Um de Marco", não "Praca Trinta E Um De Marco": partículas em minúsculas no meio
        palavras = novo.title().split(" ")
        return " ".join(p.lower() if i and p.lower() in _PARTICULAS_MINUSCULAS else p for i, p in enumerate(palavras))
    if not letras:
        return novo
    # caixa mista ("Maria da SILVA"): palavra a palavra, cada palavra nova com a caixa da palavra antiga na mesma
    # posição relativa; as partículas com a caixa das partículas do antigo
    velhas = [(p, _estilo(p)) for p in antigo.split()]
    estilos = [e for p, e in velhas if e and p.lower() not in _PARTICULAS_MINUSCULAS] or ["Aa"]
    da_particula = next((e for p, e in velhas if e and p.lower() in _PARTICULAS_MINUSCULAS), "a")
    palavras = novo.split(" ")
    cheias = [i for i, p in enumerate(palavras) if p and p.lower() not in _PARTICULAS_MINUSCULAS]
    for j, i in enumerate(cheias):
        e = estilos[round(j * (len(estilos) - 1) / (len(cheias) - 1)) if len(cheias) > 1 else 0]
        palavras[i] = _com_estilo(palavras[i], e)
    return " ".join(_com_estilo(p, da_particula) if p.lower() in _PARTICULAS_MINUSCULAS else p for p in palavras)


_PARTICULAS_MINUSCULAS = {"de", "da", "do", "das", "dos", "e"}


def _estilo(palavra: str) -> str | None:
    """A caixa de uma palavra: "A" (MAIÚSCULAS), "a" (minúsculas), "Aa" (Título); None sem letras ou misturada."""
    letras = [c for c in palavra if c.isalpha()]
    if not letras:
        return None
    if all(c.isupper() for c in letras):
        return "A"
    if all(c.islower() for c in letras):
        return "a"
    return "Aa" if letras[0].isupper() and all(c.islower() for c in letras[1:]) else None


def _com_estilo(palavra: str, estilo: str) -> str:
    return palavra.upper() if estilo == "A" else palavra.lower() if estilo == "a" else palavra.capitalize()


def _trocar_digitos(antigo: str, diferente: bool = True) -> str:
    """Troca cada dígito por outro, mantendo pontuação e tamanho (último recurso)."""
    out = []
    for c in antigo:
        if c.isdigit():
            n = _rng.randrange(10)
            if diferente and str(n) == c:
                n = (n + 1 + _rng.randrange(9)) % 10
            out.append(str(n))
        else:
            out.append(c)
    return "".join(out)


def _digitos_como(bloco: str) -> str:
    """Dígitos novos no lugar de ``bloco``, com os mesmos zeros à esquerda (o número tem a mesma grandeza)."""
    zeros = len(bloco) - len(bloco.lstrip("0"))
    if zeros == len(bloco):
        return bloco
    return "0" * zeros + str(_rng.randint(1, 9)) + _digitos(len(bloco) - zeros - 1)


def _numero_br(texto: str) -> float | None:
    """O primeiro número do texto, no jeito brasileiro: "1.234,56" → 1234.56, "1.234" → 1234 (ponto de milhar),
    "45,6" → 45.6; "85.00" (ponto decimal, do OCR) → 85.0."""
    m = re.search(r"-?\d[\d.]*(?:,\d+)?", texto)
    if not m:
        return None
    s = m.group().rstrip(".")
    if "," in s or re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


_RE_REAIS = re.compile(r"(?P<ast>\*+)?(?P<int>\d{1,3}(?:\.\d{3})+|\d+),(?P<cent>\d{2})(?!\d)")
_CENTAVOS_DE_TABELA = frozenset((90, 99, 89, 49, 95, 97))  # preço de plano ("R$ 99,90")


def _escrever_reais(antigo: str, m, centavos: int) -> str:
    """``centavos`` no lugar do valor achado (``m``) em ``antigo``, no formato dele: com ou sem ponto de milhar
    ("1234,56" continua sem) e o preenchimento de asteriscos ("R$ ***1.234,56") com a mesma largura."""
    inteiro, cent = divmod(centavos, 100)
    num = f"{inteiro:,}".replace(",", "." if "." in m["int"] or len(m["int"]) <= 3 else "")
    ast = "*" * max(0, len(m["ast"]) + len(m["int"]) - len(num)) if m["ast"] else ""
    return antigo[:m.start()] + ast + num + f",{cent:02d}" + antigo[m.end():]


def _tarifa_ok(centavos: int, consumo: float, unidade: str) -> bool:
    from .. import coerencia

    return coerencia.tarifa_ok(centavos, consumo, unidade)


def _total_e_consumo(doc: dict):
    """(total em centavos, (consumo, unidade)) do documento de ``usando(documento=...)``; None no que falta."""
    from .. import coerencia

    return coerencia._total(doc["campos"]), coerencia._consumo(doc["campos"])


def _fator_sorteado(v0: float, ok=None) -> float:
    """Fator novo/antigo log-normal em volta de 1 (95% entre 0,55 e 1,8; nunca quase 1): o número novo fica na ordem
    de grandeza do antigo (o mesmo nº de dígitos inteiros em ~95% das vezes) e passa em ``ok`` (a tarifa com o
    consumo do documento), quando dá."""
    digitos = len(str(int(abs(v0))))
    for _ in range(40):
        f = min(2.2, max(0.45, _rng.lognormvariate(0, 0.3)))
        if abs(f - 1) < 0.03 or (len(str(int(abs(v0) * f))) != digitos and _rng.random() < 0.85):
            continue
        if ok is None or ok(v0 * f):
            return f
    return f


def _centavos_no_estilo(novo: int, cent0: int) -> int:
    """Os centavos do valor novo no estilo do antigo: redondo continua redondo (70%: taxa fixa, "R$ 85,00"), preço de
    tabela continua de tabela (75%: ,90/,99 do plano de telefone), o resto fica como saiu da conta."""
    c0, r = cent0 % 100, _rng.random()
    if c0 == 0 and r < 0.7:
        novo = max(100, round(novo / 100) * 100)
    elif c0 in _CENTAVOS_DE_TABELA and r < 0.75:
        novo = novo // 100 * 100 + _rng.choices((90, 99, c0), (50, 25, 25))[0]
    return novo + 100 if novo == cent0 else novo


def _valor_novo(cent0: int, fator: float | None = None) -> int:
    """O valor novo (centavos) no lugar de ``cent0``: ``fator`` força a proporção; sem ele, um fator perto de 1, na
    ordem de grandeza do antigo, com os centavos no estilo dele. Na mesma versão (``usando(documento=...)``): um fator
    só para os valores e o consumo, o mesmo valor velho vira o mesmo valor novo em todo lugar (o total do canhoto, o
    do código de barras) e o total novo fica numa tarifa plausível com o consumo do documento."""
    if fator is not None:
        return max(1, round(cent0 * fator))
    doc = _documento_atual.get()
    if doc is not None and cent0 in doc["valores"]:
        return doc["valores"][cent0]
    f = doc["fator"] if doc is not None else None
    if f is None:
        total, cons = _total_e_consumo(doc) if doc is not None else (None, None)

        def ok(v):
            return not cons or total != cent0 or _tarifa_ok(round(v * 100), *cons)
        f = _fator_sorteado(cent0 / 100, ok)
        if doc is not None:
            doc["fator"] = f
    novo = _centavos_no_estilo(max(1, round(cent0 * f)), cent0)
    if doc is not None:
        doc["valores"][cent0] = novo
    return novo


_RE_NUMERO = re.compile(r"\d[\d.,]*\d|\d")
_RE_UNIDADE = re.compile(r"k\s?wh|m3|m³", re.I)


def _escrever_numero(v: float, molde: str) -> str:
    """``v`` escrito como o número ``molde``: as mesmas casas decimais (com vírgula), o ponto de milhar se o molde tem e
    os zeros à esquerda (a leitura do medidor "04512")."""
    inteiro, _, decimais = molde.partition(",")
    casas = len(decimais)
    s = f"{max(v, 10 ** -casas):,.{casas}f}".replace(",", "§").replace(".", ",")
    s = s.replace("§", "." if "." in inteiro else "")
    if "." not in inteiro and inteiro.startswith("0") and len(inteiro) > 1:
        cabeca, virgula, cauda = s.partition(",")
        s = cabeca.zfill(len(inteiro)) + virgula + cauda
    return s


def _consumo_novo(antigo: str, fator: float | None = None) -> str | None:
    """Consumo novo no formato do antigo (casas decimais, ponto de milhar, zeros à esquerda): o número junto da
    unidade (kWh, m³) ou, sem unidade, o último que não é parte de um mês ("AGO/26 183" muda o 183). ``fator`` força a
    proporção; sem ele, o da versão (``usando(documento=...)``: o consumo acompanha o valor) ou um perto de 1, na ordem
    de grandeza do antigo e numa tarifa plausível com o total do documento. None sem número."""
    nums = list(_RE_NUMERO.finditer(antigo))
    u = _RE_UNIDADE.search(antigo)
    m = next((n for n in nums if u and n.end() <= u.start() and not antigo[n.end():u.start()].strip()), None)
    if m is None:
        m = next((n for n in reversed(nums) if "/" not in antigo[max(0, n.start() - 1):n.end() + 1]), None)
    if m is None:
        return None
    v0 = _numero_br(m.group()) or 1
    f = fator
    if f is None:
        doc = _documento_atual.get()
        f = doc["fator"] if doc is not None else None
        if f is None:
            total, cons = _total_e_consumo(doc) if doc is not None else (None, None)

            def ok(v):
                return not (total and cons) or _tarifa_ok(total, v, cons[1])
            f = _fator_sorteado(v0, ok)
            if doc is not None:
                doc["fator"] = f
    texto = _escrever_numero(v0 * f, m.group())
    if texto == m.group() and fator is None:
        texto = _escrever_numero(v0 * f + (1 if "," not in m.group() else 0.1), m.group())
    resto = antigo[m.end():]
    if re.fullmatch(r"[mM]", resto):  # "205m": o OCR perdeu o espaço e o ³ de "205 m³" (consumo em metro é m³)
        resto = " " + resto + "³"
    return antigo[:m.start()] + texto + resto


# meses por extenso (os abreviados são ``_MESES``) e o número de cada nome, sem acento
_MESES_EXTENSO = ("JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO",
                  "NOVEMBRO", "DEZEMBRO")
_NUM_MES = {_sem_acento(m): i + 1 for nomes in (_MESES, _MESES_EXTENSO) for i, m in enumerate(nomes)}
_LETRA = "A-Za-zÀ-ÖØ-öø-ÿ"
_NOME_MES = (rf"(?<![{_LETRA}])(?:"
             + "|".join(sorted({*_MESES, *_MESES_EXTENSO, *map(_sem_acento, _MESES_EXTENSO)}, key=len, reverse=True))
             + rf")\.?(?![{_LETRA}])")
_RE_DATAS = (  # (formato, regex) da data com dia, do mais específico para o mais solto
    ("iso", re.compile(r"(?<!\d)(?P<a>\d{4})(?P<s>[/.\-])(?P<m>\d{1,2})(?P=s)(?P<d>\d{1,2})(?!\d)")),
    ("num", re.compile(r"(?<!\d)(?P<d>\d{1,2})(?P<s>[/.\-])(?P<m>\d{1,2})(?P=s)(?P<a>\d{4}|\d{2})(?!\d)")),
    ("nome", re.compile(rf"(?<!\d)(?P<d>\d{{1,2}})(?P<s>\s*[/.\-]\s*|\s+de\s+|\s*)(?P<m>{_NOME_MES})"
                        rf"(?P<t>\s*[/.\-,]\s*|\s+de\s+|\s*)(?P<a>\d{{4}}|\d{{2}})(?!\d)", re.I)),
    ("dm", re.compile(r"(?<![\d/.\-])(?P<d>\d{1,2})(?P<s>/)(?P<m>\d{1,2})(?![\d/.\-])")),
)
_RE_MESES = (  # o mês de referência, sem dia
    ("nome", re.compile(rf"(?P<m>{_NOME_MES})(?P<s>\s*[/.\-]\s*|\s+de\s+|\s*)(?P<a>\d{{4}}|\d{{2}})(?!\d)", re.I)),
    ("num", re.compile(r"(?<![\d/.\-])(?P<m>\d{1,2})(?P<s>[/.\-])(?P<a>\d{4}|\d{2})(?![\d/.\-])")),
    ("iso", re.compile(r"(?<![\d/.\-])(?P<a>\d{4})(?P<s>[/.\-])(?P<m>\d{1,2})(?![\d/.\-])")),
    ("nome", re.compile(rf"(?P<m>{_NOME_MES})(?P<s>)(?P<a>)", re.I)),  # só o nome ("OUTUBRO")
)


def _achar_data(texto: str, padroes=_RE_DATAS):
    """(formato, match, ano, mês, dia) da primeira data (ou mês, com ``_RE_MESES``) válida em ``texto``; ano None
    quando não está escrito; None se não há."""
    for formato, rx in padroes:
        for m in rx.finditer(texto):
            g = m.groupdict()
            mes = int(g["m"]) if g["m"].isdigit() else _NUM_MES[_normalizar(g["m"]).rstrip(".")]
            dia = int(g.get("d") or 1)
            ano = (int(g["a"]) + (2000 if len(g["a"]) == 2 else 0)) if g.get("a") else None
            try:
                dt.date(ano or 2000, mes, dia)
            except ValueError:
                continue
            return formato, m, ano, mes, dia
    return None


def ler_data(texto: str) -> dt.date | None:
    """A data (com dia e ano) escrita em ``texto``, em qualquer dos formatos de conta (10/10/2026, 1/2/26, 2026-10-10,
    "10 de outubro de 2026", "10 OUT 2026"); None se não há."""
    achado = _achar_data(str(texto or ""))
    return dt.date(*achado[2:]) if achado and achado[2] else None


def ler_mes(texto: str) -> tuple[int, int] | None:
    """(ano, mês) do mês de referência escrito em ``texto`` (SET/2026, set/26, "Setembro de 2026", 09/2026, 9/26,
    2026-09; com a sigla lida errado pelo OCR, "AG0/2026"); None se não há."""
    achado = _achar_data(mes_do_ocr(str(texto or "")), _RE_MESES)
    return (achado[2], achado[3]) if achado and achado[2] else None


_CONFUSOES_OCR = str.maketrans("0158", "OISB")
_RE_SIGLA_COM_DIGITO = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]{3}(?![A-Za-z0-9]))(?=[0-9]*[A-Za-z])(?=[A-Za-z]*[0-9])"
                                  r"[A-Za-z0-9]{3}")


def mes_do_ocr(texto: str) -> str:
    """A sigla do mês que o OCR leu com um dígito no lugar da letra ("AG0/2026" → "AGO/2026", "0UT" → "OUT"), na caixa
    das letras vizinhas; o resto do texto como está."""
    def troca(m):
        s = m.group()
        t = s.upper().translate(_CONFUSOES_OCR)
        if t not in _MESES:
            return s
        minusc = any(c.islower() for c in s)
        return "".join(c if c.isalpha() else (t[j].lower() if minusc and j else t[j]) for j, c in enumerate(s))
    return _RE_SIGLA_COM_DIGITO.sub(troca, texto)


def _escrever_mes(token: str, mes: int, texto: str) -> str:
    """O mês ``mes`` escrito como ``token`` (do ``texto`` antigo): por extenso ou abreviado, na caixa dele, com o ponto
    da abreviação; MARÇO com cedilha se o antigo era MARÇO, se o texto tem acento ou com ``usando(acentos=True)``."""
    nome = token.rstrip(".")
    extenso = len(nome) > 3
    if extenso and _NUM_MES[_normalizar(nome)] == 3:
        acento = not nome.isascii()
    else:
        acento = not texto.isascii() or _acentos_atual.get()
    novo = (_MESES_EXTENSO if extenso else _MESES)[mes - 1]
    return _caixa(nome, novo if acento else _sem_acento(novo)) + token[len(nome):]


def formatar_data(antigo: str, d: dt.date) -> str | None:
    """``d`` escrita no formato de ``antigo``: a ordem (dd/mm/aaaa, aaaa-mm-dd, dd/mm), o separador, os zeros à
    esquerda (1/2/2026 continua sem zero), o ano com 2 ou 4 dígitos e o mês por extenso ou abreviado, na caixa e com o
    acento do antigo ("10 de outubro de 2026", "10 OUT 2026", "10/out/26"); None se o antigo não é uma data com dia."""
    a = antigo.strip()
    achado = _achar_data(a)
    if not achado:
        return None
    formato, m = achado[:2]
    g = m.groupdict()
    if formato == "nome":  # "5 de outubro de 2026" (sem zero) e "05 OUT 2026"
        sem_zero = len(g["d"]) == 1 or (g["d"][0] != "0" and "DE" in g["s"].upper())
    else:  # 1/2/2026 sem zero; 01/02/2026 e 10/10/2026 com
        sem_zero = len(g["d"]) == 1 or len(g["m"]) == 1
    dia = str(d.day) if sem_zero else f"{d.day:02d}"
    if formato == "nome":
        mes = _escrever_mes(g["m"], d.month, a)
    else:
        mes = str(d.month) if sem_zero else f"{d.month:02d}"
    ano = "" if formato == "dm" else str(d.year) if len(g["a"]) == 4 else f"{d.year % 100:02d}"
    s = g["s"]
    novo = (f"{ano}{s}{mes}{s}{dia}" if formato == "iso" else f"{dia}{s}{mes}" if formato == "dm"
            else f"{dia}{s}{mes}{g['t']}{ano}" if formato == "nome" else f"{dia}{s}{mes}{s}{ano}")
    return a[:m.start()] + novo + a[m.end():]


def formatar_mes(antigo: str, d: dt.date) -> str | None:
    """O mês de ``d`` no formato de ``antigo`` (SET/2026, set/26, Setembro/2026, "setembro de 2026", 09/2026, 9/26,
    2026-09, OUTUBRO), na caixa, com os zeros e o acento do antigo; None se não reconhece."""
    a = antigo.strip()
    achado = _achar_data(a, _RE_MESES)
    if not achado:
        return None
    formato, m = achado[:2]
    g = m.groupdict()
    mes = _escrever_mes(g["m"], d.month, a) if formato == "nome" else (
        str(d.month) if len(g["m"]) == 1 else f"{d.month:02d}")
    ano = "" if not g["a"] else str(d.year) if len(g["a"]) == 4 else f"{d.year % 100:02d}"
    novo = f"{ano}{g['s']}{mes}" if formato == "iso" else f"{mes}{g['s']}{ano}"
    return a[:m.start()] + novo + a[m.end():]


def como_o_antigo(antigo: str, novo: str) -> str:
    """``novo`` com a caixa (MAIÚSCULAS, minúsculas, Título) do ``antigo``."""
    return _caixa(antigo, novo)


def arredondar_valor(texto: str) -> str:
    """R$ 187,43 → R$ 187,00 (valor redondo, como em muitas fraudes de verdade)."""
    return re.sub(r"(\d),\d{2}\b", r"\1,00", texto, count=1)


def cidade_no_formato(antigo: str, cidade: str, uf: str) -> str:
    """A cidade nova no formato do campo antigo: só a UF ("CE"), só a cidade ("Fortaleza"), "CIDADE/UF",
    "CIDADE - UF" ou "CIDADE, UF", na caixa do antigo (a da cidade e a da UF: "Fortaleza/CE" → "Caucaia/CE")."""
    a = antigo.strip()
    if re.fullmatch(r"[A-Za-z]{2}", a):
        return _caixa(a, uf)
    m = re.search(r"\s*(/|-|,)\s*[A-Za-z]{2}$", a)
    if m:
        return _caixa(a[:m.start()], cidade) + m.group(0)[:-2] + _caixa(m.group(0)[-2:], uf)
    velha = cidade_do_texto(a)
    m = re.search(r"\s+[A-Za-z]{2}$", a)
    if m and velha and _normalizar(a[-2:]) == velha[1]:
        # "CIDADE UF" só com espaço (o envelope: "BELO HORIZONTE MG"): a UF ficava de fora ("LEOPOLDINA")
        return _caixa(a[:m.start()], cidade) + m.group(0)[:-2] + _caixa(a[-2:], uf)
    if velha and re.search(r"[A-Za-z]{3}$", a) and _normalizar(a[-2:]) == velha[1]:
        # a UF grudada na cidade ("C U RITIBAPR"): o OCR perdeu o separador, que na conta é a barra
        return _caixa(a[:-2], cidade) + "/" + _caixa(a[-2:], uf)
    return _caixa(a, cidade)


def cidade_mais_parecida(antigo: str, rng: random.Random | None = None, tentativas: int = 25) -> tuple[str, str]:
    """(cidade, UF) do vocabulário para trocar a cidade de ``antigo``: de preferência da MESMA UF (quem muda o
    endereço num comprovante raramente muda de estado) e com nome de tamanho parecido (tem de caber onde o antigo
    estava), sorteada entre as que servem (com a base de lugares, entre os municípios do split, pela população);
    antes saía sempre a de tamanho mais próximo (ARAPIRACA no lugar de FORTALEZA em 2 de cada 3 vezes)."""
    r = rng or _rng
    velha = cidade_do_texto(antigo)
    nome = velha[0] if velha else re.sub(r"\s*(/|-|,)\s*[A-Za-z]{2}$", "", antigo.strip())
    alvo, uf, L = len(nome), velha[1] if velha else None, _lugares()
    if uf is None and L is not None and _municipio((nome, "")):  # só a cidade ("Fortaleza"): a UF do município
        uf = L["mun"][_municipio((nome, ""))][1]
    k, norm = _normalizar(nome), L["norm"] if L is not None else {}
    todas = [c for c in _lista("cidades") if (norm.get(c) or _normalizar(c[0])) != k] or list(_lista("cidades"))

    def servem(cidades):
        return [c for c in cidades if abs(len(c[0]) - alvo) <= max(3, round(0.3 * alvo))]
    mesma_uf = [c for c in todas if c[1] == uf] if r.random() < 0.85 else []
    lista = servem(mesma_uf) or servem(todas)
    if lista and L is not None:  # com a base de lugares, pela população (a chance de a conta ser de lá)
        escolha = r.choices(lista, [L["pop"].get(c, 1) for c in lista])[0]
    else:
        escolha = (r.choice(lista or [None])
                   or min(r.sample(todas, min(tentativas, len(todas))), key=lambda c: abs(len(c[0]) - alvo)))
    return _grafia(escolha[0]), escolha[1]


def trocar_cidade_no_texto(texto: str, cidade: str, uf: str) -> str:
    """Troca a cidade e a UF que aparecem DENTRO de um texto ("60750-720 - FORTALEZA - CE" → "... - CURITIBA - PR")."""
    velha = cidade_do_texto(texto)
    if not velha:
        return texto
    norm = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().upper()
    if len(norm) != len(texto):
        return texto
    i = norm.find(velha[0])
    if i < 0:
        return texto
    trecho = texto[i:i + len(velha[0])]
    texto = texto[:i] + _caixa(trecho, cidade) + texto[i + len(velha[0]):]
    m = re.search(r"(?<![A-Za-z])" + velha[1] + r"(?![A-Za-z])", texto[i + len(cidade):], re.I)
    if m:
        j = i + len(cidade) + m.start()
        texto = texto[:j] + _caixa(texto[j:j + 2], uf) + texto[j + 2:]
    return texto


def parecido(categoria: str | None, antigo: str, cidade: tuple[str, str] | str | None = None,
             fator: float | None = None, *, rotulo: str | None = None) -> str:
    """Valor falso plausível para ``categoria`` imitando o formato e o comprimento de ``antigo``.
    ``cidade`` ((nome, UF) ou "NOME/UF") força a cidade do CEP, do bairro e do DDD; sem ela vale a de
    ``usando(cidade=...)`` e, sem nenhuma, o CEP fica na região do antigo (a cidade impressa não mudou) e o bairro novo
    é da cidade do antigo (se ele é de uma cidade só) ou genérico. ``fator`` força
    valor_novo/valor_antigo em "valor" e "consumo/leitura", para o chamador manter consumo e valor coerentes.
    ``rotulo``: o rótulo impresso ao lado da data ("VENCIMENTO", "DATA DA LEITURA"): a data nova tem o papel dele
    (sem ele, o do campo de mesmo texto em ``usando(documento=...)`` ou o do próprio texto, "Venc: 10/10/2026").
    Em "outro" (e numa categoria sem regra própria), e-mail, telefone e RG saem no molde do antigo."""
    a = antigo.strip()
    if categoria == "data":
        d0 = ler_data(a)
        d = _data_nova(_papel_da_data(a, rotulo), d0)
        if d == d0:
            d = _vencimento_novo(d0, futuro=False)
        novo = formatar_data(a, d) or formatar_mes(a, d)
        if novo:
            return novo
    if categoria == "mês de referência":
        a = mes_do_ocr(a)  # "AG0/2026": a sigla escrita na conta é AGO
        ciclo = _ciclo_da_versao()
        ref = ciclo["referencia"]
        velho = _datas_do_documento().get("referencia")  # a referência impressa (o mês mais recente do documento)
        mes = ler_mes(a)
        if ((velho.year, velho.month) if velho else mes) == (ref.year, ref.month):
            # o mesmo mês: o seguinte, se a conta permite, senão o anterior
            n = _meses(ref) + (1 if _meses(ciclo["vencimento"]) - _meses(ref) >= 1 else -1)
            ref = dt.date(n // 12, n % 12 + 1, 1)
        if velho and mes and mes != (velho.year, velho.month):
            # outro mês do documento (o histórico de consumo): anda o mesmo tanto que a referência, e a sequência
            # de meses continua de um em um
            n = _meses(dt.date(*mes, 1)) + _meses(ref) - _meses(velho)
            ref = dt.date(n // 12, n % 12 + 1, 1)
        # mês que não dá para ler fica como está (quem chama desiste do campo): trocar os dígitos dele dava "AG7/1478"
        return formatar_mes(a, ref) or antigo
    if categoria == "valor":
        m = _RE_REAIS.search(a)
        if m and (int(m["int"].replace(".", "")) or int(m["cent"])):
            return _escrever_reais(a, m, _valor_novo(int(m["int"].replace(".", "")) * 100 + int(m["cent"]), fator))
        return _trocar_digitos(a)
    if categoria == "consumo/leitura":
        novo = _consumo_novo(a, fator)
        if novo:
            return novo
    if categoria == "cpf/cnpj":
        return _numero_da_versao(categoria, a, lambda: _documento_no_molde(a))
    if isinstance(cidade, str):
        cidade = cidade_do_texto(cidade) or (cidade, None)
    if categoria == "cep":
        m = re.search(r"\d{5}-?\d{3}", a)
        if m:
            c = cidade or cidade_atual()
            novo = _cep_novo(m.group(), c)  # com a base de lugares: um CEP de rua de verdade, coerente na versão
            if novo:
                novo = f"{novo[:5]}-{novo[5:]}"
            elif c:
                novo = cep_de(*c)
            else:
                novo = _sortear_cep(_faixa_do_cep(m.group()) or _faixa_cep(*_rng.choice(_lista("cidades"))))
            if "-" not in m.group():
                novo = novo.replace("-", "")
            return a[:m.start()] + novo + a[m.end():]
    if categoria in ("codigo barras", "linha digitável"):
        return _codigo_parecido(categoria, a)
    if categoria == "identificador documento":
        if any(c.isdigit() for c in a):  # os zeros à esquerda ficam: 0000004709726 → 0000003185204

            def novo():
                for _ in range(5):
                    n = _identificador_no_molde(a)
                    if n != a:
                        return n
                return _trocar_digitos(a)
            return _numero_da_versao(categoria, a, novo)
        return _caixa(a, gerar(categoria)["texto"])
    if categoria == "cidade/uf":  # no formato do antigo: só a UF, só a cidade, "CIDADE/UF", "CIDADE - UF"
        if re.fullmatch(r"[A-Za-z]{2}", a):
            return cidade_no_formato(a, *_sortear("cidades"))
        return cidade_no_formato(a, *cidade_mais_parecida(a))
    if categoria == "nome":  # mantém o tamanho do nome original (em letras: o novo tem de caber no lugar)
        palavras = a.split()

        def iniciais_de(n):
            return sum(len(p.rstrip(".")) == 1 for p in n.split())

        def nota(n):
            return (abs(len(n) - len(a)) + 2 * abs(len(n.split()) - len(palavras))
                    + 2 * abs(iniciais_de(n) - iniciais_de(a)))
        if _empresa(a):  # razão social no molde: "M R C FILHO FOTOS", "SANTOS COMERCIO DE ALIMENTOS LTDA"
            cands = [_na_fatia(lambda: _razao_social(a)) for _ in range(30)]
        else:  # as iniciais onde o antigo tinha: "J F A SILVA", "MARIA A. SOUZA"
            iniciais = [i for i, p in enumerate(palavras) if len(p.rstrip(".")) == 1 and p[0].isalpha()
                        and _normalizar(p) not in _PARTICULAS]
            ponto = any(palavras[i].endswith(".") for i in iniciais)
            # com a base de nomes, o nome já sai com o nº de palavras e o comprimento do antigo: rodadas de 6 até um
            # caber (±15%); sem ela, 60 candidatos
            base = _nomes_base() is not None
            cands, letras = [], None if iniciais else len(a)  # com iniciais, o nome inteiro é mais comprido
            for _ in range(10 if base else 1):
                cands += [_abreviar(_na_fatia(lambda: _nome_pessoa(max(2, len(palavras)), letras)), iniciais, ponto)
                          for _ in range(6 if base else 60)]
                if base and min(map(nota, cands)) <= max(1.0, 0.15 * len(a)):
                    break
        cands = [n for n in cands if _normalizar(n) != _normalizar(a)] or cands  # o mesmo nome não é fraude
        return _caixa(a, _grafia(min(cands, key=nota)))
    if categoria == "endereço":
        for _ in range(5):  # o molde pode sortear o mesmo endereço (AVENIDA BRASIL, S/N)
            novo = _endereco_no_molde(a)
            if _normalizar(novo) != _normalizar(a):
                return novo
        return _caixa(a, gerar(categoria)["texto"])
    if categoria == "bairro":  # da cidade do documento ou genérico, com o comprimento do antigo (±15%)
        cod = _municipio(cidade) if cidade else (_cod_da_versao() or _municipio(min(cidades_do_bairro(a) or [None])))
        if _bairros(cod):  # base de lugares: bairro de verdade da cidade, o da rua que a versão escolheu
            return _caixa(a, _grafia_lugar(_bairro_parecido(a, cod)))
        c = cidade or cidade_atual() or min(cidades_do_bairro(a) or [None])
        alvo = len(a)
        cands = [_bairro_novo(c) for _ in range(24)]
        for _ in range(6):  # compostos com a base do comprimento que falta ("PREFEITO JOSÉ WALTER")
            cands.append(_composto(lambda: min((_sortear("ruas") for _ in range(8)),
                                               key=lambda r: abs(len(r) + 7 - alvo))))
        cands = [b for b in cands if _normalizar(b) != _normalizar(a)] or cands
        novo = next((b for b in cands if abs(len(b) - alvo) <= max(1, 0.15 * alvo)),
                    min(cands, key=lambda b: abs(len(b) - alvo)))
        return _caixa(a, _grafia(novo))
    if categoria == "emissor/logotipo":
        return _caixa(a, _grafia("COMPANHIA " + _rng.choice(("ENERGÉTICA", "DE SANEAMENTO", "DE GÁS", "DE ÁGUAS"))
                                 + " " + _sortear("sobrenomes")))
    if categoria not in _COM_REGRA:  # "outro": e-mail, telefone e RG no molde do antigo
        novo = (_email_no_molde(a) or _telefone_no_molde(a, cidade)
                or (_rg_no_molde(a) if _RE_ORGAO.search(_normalizar(a)) else None))
        if novo:
            return novo
    if any(c.isdigit() for c in a):
        return _trocar_digitos(a)
    return _caixa(a, gerar("nome")["texto"])


# categorias numéricas com regra própria em ``parecido`` (o que sobra delas não é telefone nem e-mail)
_COM_REGRA = frozenset(("data", "mês de referência", "valor", "consumo/leitura", "cpf/cnpj", "cep", "codigo barras",
                        "linha digitável", "identificador documento"))
