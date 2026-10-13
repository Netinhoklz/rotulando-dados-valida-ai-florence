"""Detecção automática dos campos de um comprovante: lê o texto (OCR), descobre a
categoria de cada linha e acha código de barras e QR code pela imagem.

Categoria de uma linha, nesta ordem:
  1. padrão do próprio texto (CPF, CNPJ, CEP, data, mês/ano, R$, kWh/m³, linha digitável); a linha que começa
     com tipo de logradouro é endereço mesmo com o CEP no fim (o CEP vira um subcampo, ``parte_de`` a linha);
  2. rótulo na mesma linha à esquerda ("NOME", "VENCIMENTO"...) ou logo acima;
  3. palavras típicas (RUA, AV., BAIRRO...);
  4. bloco de endereçamento sem rótulos (o envelope: nome em cima da rua, bairro embaixo), menos o do EMISSOR
     (razão social com LTDA/S.A., CNPJ perto, faixa do cabeçalho/rodapé);
  5. topo da página sem outra categoria -> emissor (nome da concessionária).
Linhas que SÃO rótulos não viram campo (não faz sentido falsificar "NOME"); "CPF: 905.214.638-71" não é
rótulo, é rótulo e valor (o campo é só o valor).
"""

from __future__ import annotations

import difflib
import re
import unicodedata

import cv2
import numpy as np

from . import gabarito, ocr

# rótulo (sem acento, maiúsculo, sem espaços) -> categoria do valor que vem ao lado/abaixo
ROTULOS = {
    "NOME": "nome", "TITULAR": "nome", "CLIENTE": "nome", "NOMEDOCLIENTE": "nome", "DESTINATARIO": "nome",
    "CPF": "cpf/cnpj", "CNPJ": "cpf/cnpj", "CPF/CNPJ": "cpf/cnpj", "CPFCNPJ": "cpf/cnpj",
    "ENDERECO": "endereço", "LOGRADOURO": "endereço", "ENDERECODEENTREGA": "endereço",
    "BAIRRO": "bairro", "CEP": "cep", "CIDADE": "cidade/uf", "CIDADE/UF": "cidade/uf", "MUNICIPIO": "cidade/uf",
    "UF": "cidade/uf", "VENCIMENTO": "data", "DATADEVENCIMENTO": "data", "EMISSAO": "data",
    "DATADEEMISSAO": "data", "LEITURA": "data", "DATADALEITURA": "data", "PROXIMALEITURA": "data",
    "MESREFERENCIA": "mês de referência", "MESDEREFERENCIA": "mês de referência", "REFERENCIA": "mês de referência",
    "COMPETENCIA": "mês de referência", "CONSUMO": "consumo/leitura", "CONSUMOKWH": "consumo/leitura",
    "LEITURAATUAL": "consumo/leitura", "LEITURAANTERIOR": "consumo/leitura", "TOTALAPAGAR": "valor",
    "VALOR": "valor", "VALORAPAGAR": "valor", "VALORTOTAL": "valor", "TOTAL": "valor",
    "NINSTALACAO": "identificador documento", "INSTALACAO": "identificador documento",
    "NDAINSTALACAO": "identificador documento", "UNIDADECONSUMIDORA": "identificador documento",
    "UC": "identificador documento", "MATRICULA": "identificador documento", "CODIGODOCLIENTE": "identificador documento",
    "NCLIENTE": "identificador documento", "NDOCLIENTE": "identificador documento", "NOTAFISCAL": "identificador documento",
    "NFATURA": "identificador documento", "CONTA": "identificador documento",
    "MULTA": "valor", "JUROS": "valor", "MULTAEJUROS": "valor", "DESCONTO": "valor", "TAXA": "valor",
    # boleto (ficha de compensação): quem paga é o cliente; o beneficiário/cedente é o emissor
    "PAGADOR": "nome", "SACADO": "nome", "NOMEDOPAGADOR": "nome", "NOMEDOTITULAR": "nome", "TITULARDACONTA": "nome",
    "CONSUMIDOR": "nome", "BENEFICIARIO": "emissor/logotipo", "BENEFICIARIOFINAL": "emissor/logotipo",
    "CEDENTE": "emissor/logotipo", "SACADORAVALISTA": "emissor/logotipo",
    "DATADODOCUMENTO": "data", "DATAPROCESSAMENTO": "data", "DATADEPROCESSAMENTO": "data", "DATADEVENCIMENTO/": "data",
    "NOSSONUMERO": "identificador documento", "NRDODOCUMENTO": "identificador documento",
    "NUMERODODOCUMENTO": "identificador documento", "NUMERODOCLIENTE": "identificador documento",
    "NUMERODAFATURA": "identificador documento", "CODIGODOBENEFICIARIO": "identificador documento",
    "AGENCIA/CODIGODOBENEFICIARIO": "identificador documento", "AGENCIA/CODIGOCEDENTE": "identificador documento",
    "VALORDODOCUMENTO": "valor", "DESCONTO/ABATIMENTO": "valor", "JUROS/MULTA": "valor", "VALORPAGO": "valor",
    "VALORCOBRADO": "valor", "OUTRASDEDUCOES": "valor", "OUTROSACRESCIMOS": "valor",
    "PAGAR": "valor", "PAGARRS": "valor", "PAGARR$": "valor", "TOTALAPAGARRS": "valor", "VALORRS": "valor",
    "VALORAPAGARRS": "valor",
    # rótulos sem campo falsificável ao lado: só para não virarem "valor" de outro rótulo
    "LOCALDEPAGAMENTO": None, "USODOBANCO": None, "CARTEIRA": None, "ESPECIE": None, "ESPECIEDOC": None,
    "ESPECIER$": None, "ACEITE": None, "QUANTIDADE": None, "AUTENTICACAOMECANICA": None, "INSTRUCOES": None,
}
PARTES = {"BENEFICIARIO": "emissor", "BENEFICIARIOFINAL": "emissor", "CEDENTE": "emissor", "SACADORAVALISTA": "emissor",
          "PAGADOR": "cliente", "SACADO": "cliente"}  # blocos do boleto: de quem é o endereço logo abaixo
_MESES = "JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ"
_MESES_EXT = "JANEIRO|FEVEREIRO|MAR[CÇ]O|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO"
_NOME_MES = rf"(?<![A-Z])(?:{_MESES_EXT}|{_MESES})(?![A-Z])"
_DIA, _MES_NUM = r"(?:0?[1-9]|[12]\d|3[01])", r"(?:0?[1-9]|1[0-2])"
# a data com dia, mês e ano nos formatos de conta (os mesmos que o gerador escreve: geradores.formatar_data):
# 10/10/2026 e 10/10/26 (o OCR cola no rótulo: "Emissaoem25/01/2026", por isso sem \b na frente), 10.10.2026,
# 10-10-2026, 2026-10-10, "05 OUT 2026", "05/OUT/2026" e "10 de outubro de 2026"; dia e mês válidos
_DATA_RX = (rf"(?<!\d){_DIA}/{_MES_NUM}/(?:\d{{4}}|\d{{2}})(?![\d/])"
            rf"|(?<![\d.\-,])(?:0[1-9]|[12]\d|3[01])(?P<s>[.\-])(?:0[1-9]|1[0-2])(?P=s)\d{{4}}(?![\d.\-])"
            rf"|(?<!\d)\d{{4}}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)"
            rf"|(?<!\d){_DIA}(?:\s*[/.\-]\s*|\s+DE\s+|\s*){_NOME_MES}\.?(?:\s*[/.\-,]\s*|\s+DE\s+|\s+)\d{{4}}(?!\d)")
# mês de referência: SET/2026, SET/26, JUL26, "Set 2026" (o histórico de consumo), "SETEMBRO DE 2026", 09/2026
_MES_RX = (rf"{_NOME_MES}\.?(?:\s*[/\-]\s*\d{{2,4}}|\s*(?:DE\s+)?\d{{4}}|\d{{2}})(?!\d)"
           r"|(?<![\d/])(?:0[1-9]|1[0-2])/\d{4}(?!\d)")
# "nº 03/2025", "Lei 12/2024", "Resolução 03/2025": número de norma, não mês de referência
_NUMERO_ANTES = re.compile(r"(?:\bN[O°º]?\.?|\bNR\.?|\bNUMERO|\bLEI|\bRESOLUCAO|\bPORTARIA|\bDECRETO|\bPROCESSO)\s*$")
PADROES = [  # (categoria, regex no texto sem acento e maiúsculo)
    # linha digitável antes do CPF/CNPJ: o OCR às vezes corta a linha e um CNPJ "aparecia" no meio dela
    ("linha digitável", re.compile(r"(\d[\d.\- ]{40,}\d)")),
    ("linha digitável", re.compile(r"(?:\d{11}-?\d\s*){2,}")),  # arrecadação: blocos de 11 + DV
    ("linha digitável", re.compile(r"\d{5}\.\d{5}\s*\d{5}\.\d{6}")),  # boleto bancário
    ("cpf/cnpj", re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")),
    ("cpf/cnpj", re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-?\d{2}(?!\d)")),
    ("cep", re.compile(r"\b\d{5}-\d{3}\b")),
    # a data antes do mês: "05/OUT/2026" tem um "OUT/2026" dentro
    ("data", re.compile(_DATA_RX, re.I)),
    ("mês de referência", re.compile(_MES_RX, re.I)),
    ("valor", re.compile(r"R[\$S] ?-?[\d.]*\d[,.]\d{2}\b")),  # o OCR às vezes lê "R$" como "RS"
    ("consumo/leitura", re.compile(r"\b\d[\d.,]*\s?(KWH|M3|M³)\b")),
]
_DATA = next(rx for cat, rx in PADROES if cat == "data")
# tipo de logradouro no começo da linha; o OCR às vezes cola o número ("RUA58"): basta não vir outra letra
_ENDERECO = re.compile(r"^(RUA|R\.|AV\.?|AVENIDA|TRAVESSA|TV\.?|ALAMEDA|AL\.|ESTRADA|ROD\.?|RODOVIA|PRACA|"
                       r"PCA\.?|LARGO|QUADRA|QD\.?|VIELA|BECO|CONJUNTO|VILA)(?![A-Z])")


def _eh_endereco(t: str) -> bool:
    """A linha começa com tipo de logradouro: a regra fixa ou o gabarito (os 324 tipos do CNEFE, as abreviações dos
    Correios — "R ", "ESTR.", "COND." —, as siglas do DF — "SQN 308" —; tipo ambíguo como VILA ou SÍTIO só com
    número, S/N ou KM)."""
    return bool(_ENDERECO.match(t)) or gabarito.tipo_logradouro(t) is not None


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.upper().strip()


def _chave_rotulo(texto: str) -> str:
    return re.sub(r"[^A-Z/]", "", normalizar(texto).replace("º", "").replace("°", "")).rstrip(":")


_TROCAS_OCR = str.maketrans({"I": "/", "1": "I", "0": "O", "5": "S", "8": "B"})


def rotulo_de(texto: str) -> str | None:
    """Chave de ROTULOS que o texto é, tolerando erros típicos do OCR ("CIDADEIUF" = "CIDADE/UF")."""
    k = _chave_rotulo(texto)
    if not k or len(k) > 30:
        return None
    if k in ROTULOS:
        return k
    for chave in ROTULOS:
        if len(chave) >= 5 and abs(len(chave) - len(k)) <= 1 and difflib.SequenceMatcher(None, k, chave).ratio() >= 0.86:
            return chave
        if len(chave) >= 5 and k.translate(_TROCAS_OCR) == chave:
            return chave
    return None


def eh_rotulo(texto: str) -> bool:
    return rotulo_de(texto) is not None


def _so_rotulo(texto: str) -> bool:
    """A linha é SÓ um rótulo. "CPF:905.214.638-71" passava por rótulo (a chave ignora os dígitos) e o valor sumia
    (e virava o rótulo errado da linha de baixo): com valor numérico depois do rótulo, é rótulo e valor."""
    if not eh_rotulo(texto):
        return False
    chave, resto = separar_rotulo(texto)
    return not (chave and re.search(r"\d", resto))


def separar_rotulo(texto: str) -> tuple[str | None, str]:
    """'CNPJ00.000.000/0001-91' -> ('CNPJ', '00.000.000/0001-91'); 'NOME: MARIA' -> ('NOME', 'MARIA')."""
    t = normalizar(texto)
    for chave in sorted(ROTULOS, key=len, reverse=True):
        if len(chave) < 2:
            continue
        m = re.match(r"^" + r"\W*".join(map(re.escape, chave)) + r"\s*[:\-]?\s*", t)
        if m and m.end() < len(t) and (t[m.end() - 1] in ": -" or t[m.end()].isdigit()):
            return chave, texto[m.end():].strip()
    return None, texto


def categoria_por_texto(texto: str) -> str | None:
    t = normalizar(texto)
    digitos = re.sub(r"\D", "", t)
    if len(digitos) >= 40:
        return "linha digitável"
    for cat, rx in PADROES:
        if cat == "mês de referência" and not any(not _NUMERO_ANTES.search(t[:m.start()]) for m in rx.finditer(t)):
            continue
        if rx.search(t):
            # rua com o CEP no fim ("RUA X, 123 - CENTRO - CEP 60000-000") é endereço; o CEP vira subcampo (detectar);
            # e rua com nome de data ("RUA 7 DE SETEMBRO, 1500", "AV. 13 DE MAIO 2020") não é data nem mês
            return "endereço" if cat in ("cep", "data", "mês de referência") and _eh_endereco(t) else cat
    if _eh_endereco(t):
        return "endereço"
    return None


def plausivel(categoria: str | None, texto: str) -> bool:
    """O texto pode mesmo ser um valor desta categoria? Para nome e endereço (o que mais se falsifica num
    comprovante) um rótulo vizinho não basta: "Data do documento" debaixo de "Endereço" não é endereço, e
    escrever uma rua por cima dele estraga a imagem."""
    t = normalizar(texto)
    sem = re.sub(r"\s", "", t)
    if not sem:
        return False
    letras = sum(ch.isalpha() for ch in sem) / len(sem)

    def rotulo() -> bool:  # o texto é um rótulo ("Data do documento"); "CLIENTE 061" tem número, é valor
        return eh_rotulo(texto) and not re.search(r"\d", t)
    if categoria == "nome":  # pessoa ou empresa: duas palavras ou mais, quase só letras ("M R C FILHO FOTOS")
        palavras = [p for p in re.split(r"[\s.]+", t) if p]
        return (letras >= 0.6 and len(palavras) >= 2 and any(sum(ch.isalpha() for ch in p) >= 3 for p in palavras)
                and len(re.findall(r"\d", t)) <= 4 and not rotulo())
    if categoria == "endereço":
        return (_eh_endereco(t) or bool(re.search(r"\d", t)) or "S/N" in t) and letras >= 0.4 and not rotulo()
    if categoria in ("bairro", "cidade/uf"):
        return letras >= 0.8 and not rotulo()
    digitos = len(re.findall(r"\d", t))
    if categoria == "cpf/cnpj":  # "CE" debaixo do rótulo "CPF/CNPJ" não é documento
        return digitos >= 11
    if categoria in ("cep", "data", "valor"):
        return digitos >= 2
    if categoria == "mês de referência":  # o rótulo "MÊS/ANO" debaixo de "REFERÊNCIA" não é o mês
        return digitos >= 2 or bool(re.search(_NOME_MES, t))
    return True


def formatar_linha_digitavel(texto: str) -> str:
    """Remonta a linha digitável a partir dos dígitos (o OCR erra espaços e hífens)."""
    d = re.sub(r"\D", "", texto)
    if len(d) == 48 and d[0] == "8":  # arrecadação: 4 blocos de 11 + DV
        return " ".join(f"{d[i:i + 11]}-{d[i + 11]}" for i in range(0, 48, 12))
    if len(d) == 47:  # boleto bancário
        return f"{d[0:5]}.{d[5:10]} {d[10:15]}.{d[15:21]} {d[21:26]}.{d[26:32]} {d[32]} {d[33:]}"
    return texto


def _sobrepoe_vertical(a, b) -> float:
    inter = min(a[3], b[3]) - max(a[1], b[1])
    return inter / max(1, min(a[3] - a[1], b[3] - b[1]))


def _rotulo_vizinho(linha, rotulos) -> tuple[str | None, str]:
    """Rótulo na mesma linha à esquerda (mais próximo) ou logo acima, alinhado à esquerda."""
    c = linha["caixa"]
    altura = c[3] - c[1]
    melhor, dist = None, 1e9
    for r in rotulos:
        rc = r["caixa"]
        if _sobrepoe_vertical(c, rc) > 0.5 and rc[2] <= c[0] + 4:
            d = c[0] - rc[2]
            if d < dist:
                melhor, dist = r, d
    if melhor is None:  # rótulo em cima do valor (comum em boletos; a caixa do OCR às vezes encosta um pouco nele)
        for r in rotulos:
            rc = r["caixa"]
            if -0.4 * altura <= c[1] - rc[3] <= 1.6 * altura and rc[1] < c[1] and abs(rc[0] - c[0]) <= 3 * altura:
                d = c[1] - rc[3]
                if d < dist:
                    melhor, dist = r, d
    if melhor is None:
        return None, ""
    return ROTULOS.get(rotulo_de(melhor["texto"]) or ""), melhor["texto"]


def detectar_codigo_barras(img: np.ndarray) -> list[list[int]]:
    """Caixas de códigos de barras: muitas barras verticais finas, escuras e alinhadas."""
    H, W = img.shape[:2]
    cinza = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, bw = cv2.threshold(cinza, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    alt_min = max(20, H // 80)
    vertical = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, alt_min)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(vertical)
    barras = [s for s in stats[1:] if s[3] >= alt_min and s[2] <= max(6, W // 150) and s[3] >= 4 * s[2]]
    if len(barras) < 20:
        return []
    # agrupa barras com altura e topo parecidos, próximas na horizontal
    barras.sort(key=lambda s: (s[1] // max(1, alt_min), s[0]))
    grupos, atual = [], [barras[0]]
    for s in barras[1:]:
        u = atual[-1]
        if abs(s[1] - u[1]) <= alt_min // 2 and abs(s[3] - u[3]) <= alt_min and 0 <= s[0] - (u[0] + u[2]) <= 6 * max(
                2, u[2]) + alt_min // 2:
            atual.append(s)
        else:
            grupos.append(atual)
            atual = [s]
    grupos.append(atual)
    caixas = []
    for g in grupos:
        if len(g) >= 20:
            x0 = min(s[0] for s in g)
            y0 = min(s[1] for s in g)
            x1 = max(s[0] + s[2] for s in g)
            y1 = max(s[1] + s[3] for s in g)
            if x1 - x0 > 3 * (y1 - y0) / 4:
                caixas.append([int(x0), int(y0), int(x1), int(y1)])
    return caixas


def _quadrado(p: np.ndarray) -> bool:
    """Os 4 cantos achados formam um QR de verdade: lados parecidos e caixa quase quadrada. O detector do OpenCV
    "acha" QR em letras de texto (um 'O' faz de padrão de canto): saía uma caixa de 570 x 931 por cima do envelope."""
    if len(p) != 4:
        return False
    lados = [float(np.hypot(*(p[(k + 1) % 4] - p[k]))) for k in range(4)]
    w, h = np.ptp(p[:, 0]), np.ptp(p[:, 1])
    return min(lados) > 0 and max(lados) / min(lados) <= 1.35 and 0.75 <= w / max(1.0, h) <= 1.33


def detectar_qr(img: np.ndarray) -> list[list[int]]:
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    ok, pontos = cv2.QRCodeDetector().detect(bgr)
    if ok and pontos is not None and not _quadrado(pontos.reshape(-1, 2)):
        ok = False
    if (not ok or pontos is None) and hasattr(cv2, "QRCodeDetectorAruco"):
        ok, pontos = cv2.QRCodeDetectorAruco().detect(bgr)  # acha QR em papel colorido/JPEG forte
    if not ok or pontos is None or not len(pontos) or not _quadrado(pontos.reshape(-1, 2)):
        return []
    p = pontos.reshape(-1, 2)
    m = 4
    return [[int(max(0, p[:, 0].min() - m)), int(max(0, p[:, 1].min() - m)),
             int(min(img.shape[1], p[:, 0].max() + m)), int(min(img.shape[0], p[:, 1].max() + m))]]


def _sobrescrito(img: np.ndarray, caixa, texto: str) -> str:
    """O OCR lê "m³" como "m3": se o último 3 é bem menor que os outros dígitos, é o expoente."""
    from . import glifos  # import tardio: glifos usa ocr e sessao
    from .sessao import ErroOperacao
    try:
        gs = glifos.segmentar(img, caixa, texto)
    except ErroOperacao:
        return texto
    digitos = [g.altura for g in gs[:-1] if g.ch.isdigit()]
    if digitos and gs[-1].ch == "3" and gs[-1].altura < 0.75 * float(np.median(digitos)):
        return texto[:-1] + "³"
    return texto


def caixa_do_trecho(img: np.ndarray, caixa, texto: str, ini: int, fim: int) -> list[int] | None:
    """Caixa da tinta só dos caracteres ``texto[ini:fim]`` de uma linha (``caixa``: a da linha toda): pelos glifos
    (glifos.segmentar) quando a tinta bate com o texto; senão pela largura proporcional do texto na fonte de
    medida, presa ao começo e ao fim de palavra mais próximos (o trecho começa e termina em vão de palavra)."""
    from . import glifos  # import tardio: glifos usa ocr e sessao
    from .sessao import ErroOperacao
    linha = ocr.caixa_da_linha(img, caixa)
    try:
        gs = [g for g in glifos.segmentar(img, linha, texto) if ini <= g.i < fim]
    except ErroOperacao:
        gs = []
    if gs:
        x0, x1 = min(g.x0 for g in gs), max(g.x1 for g in gs)
    else:
        f = ocr._fonte_medida()
        total = max(1.0, f.getlength(texto))
        x0 = linha[0] + f.getlength(texto[:ini]) / total * (linha[2] - linha[0])
        x1 = linha[0] + f.getlength(texto[:fim]) / total * (linha[2] - linha[0])
        palavras = ocr.palavras_por_tinta(img, linha)
        if palavras:
            x0 = min((p[0] for p in palavras), key=lambda v: abs(v - x0))
            x1 = min((p[1] for p in palavras), key=lambda v: abs(v - x1))
    if x1 - x0 < 2:
        return None
    return ocr.caixa_tinta(img, [int(x0), linha[1], int(x1), linha[3]], 0)


def _vizinho(campos: list[dict], c: dict, acima: bool) -> dict | None:
    """A linha imediatamente acima (ou abaixo) de ``c`` no mesmo bloco: alinhada à esquerda, de altura parecida
    e colada (vão menor que uma altura de linha)."""
    x0, y0, x1, y1 = c["caixa"]
    h = max(1, y1 - y0)
    melhor, dist = None, 1e9
    for o in campos:
        if o is c or o.get("tipo", "texto") != "texto":
            continue
        ox0, oy0, ox1, oy1 = o["caixa"]
        oh = max(1, oy1 - oy0)
        vao = (y0 - oy1) if acima else (oy0 - y1)
        if -0.3 * h <= vao <= 1.0 * h and abs(ox0 - x0) <= 1.5 * h and 0.6 <= oh / h <= 1.6 and vao < dist:
            melhor, dist = o, vao
    return melhor


# razão social e registros de empresa: o bloco com eles é do EMISSOR (o OCR cola as palavras: "NORDESTES.A.")
_EMPRESA = re.compile(r"S\.\s?A(?![A-Z])|S/A(?![A-Z])|(?<![A-Z])CIA(?![A-Z])|COMPANHIA|CNPJ|INSCRICAO")
# LTDA, ME, EIRELI: razão social de empresa pequena — no envelope é o CLIENTE pessoa jurídica ("PADARIA BOM PÃO LTDA");
# o bloco só é do emissor se o gabarito reconhece a concessionária, ou pelo CNPJ perto e pela faixa (_do_emissor)
_CNPJ = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/\d{4}-?\d{2}(?!\d)")
_FAIXA = 0.06  # cabeçalho e rodapé da página: papel timbrado do emissor, não o envelope do cliente


def _do_emissor(campos: list[dict], bloco: list[dict], H: int | None) -> bool:
    """O bloco de endereço sem rótulo é do EMISSOR (rodapé/cabeçalho com a razão social, o endereço e o CNPJ da
    concessionária), não o envelope do cliente: tem marca de empresa (LTDA, S.A., CNPJ...), tem um CNPJ colado em
    cima ou embaixo, ou fica na faixa do cabeçalho/rodapé da página."""
    if any(_EMPRESA.search(normalizar(f"{c['texto']} {c.get('rotulo') or ''}")) or gabarito.emissor(c["texto"])
           for c in bloco):
        return True
    x0 = min(c["caixa"][0] for c in bloco)
    y0 = min(c["caixa"][1] for c in bloco)
    x1 = max(c["caixa"][2] for c in bloco)
    y1 = max(c["caixa"][3] for c in bloco)
    h = max(1, int(np.median([c["caixa"][3] - c["caixa"][1] for c in bloco])))
    for o in campos:
        if o.get("tipo", "texto") != "texto" or any(o is c for c in bloco):
            continue
        ox0, oy0, ox1, oy1 = o["caixa"]
        if oy0 < y1 + 2.5 * h and oy1 > y0 - 2.5 * h and ox0 < x1 + 3 * h and ox1 > x0 - 3 * h \
                and (_CNPJ.search(normalizar(o["texto"])) or "CNPJ" in normalizar(o.get("rotulo") or "")):
            return True
    return bool(H) and (y1 < _FAIXA * H or y0 > (1 - _FAIXA) * H)


def _parece_nome(texto: str) -> bool:
    """O gabarito acha que é nome de pessoa (prenome do Censo + sobrenomes do TSE) ou de empresa (LTDA...): o título
    acima da rua ("INFORMAÇÕES DA INSTALAÇÃO", "AVISO DE DÉBITO PENDENTE") não vira nome. Sem o gabarito, como antes."""
    if not gabarito.disponivel():
        return True
    return gabarito.nome_de_pessoa(texto) >= 0.6 or gabarito.empresa(texto)


def _local_do_documento(campos: list[dict]) -> tuple[str | None, str | None]:
    """(código IBGE da cidade, CEP) que o documento imprime: a cidade/UF reconhecida pelo gabarito e o 1º CEP."""
    cod = cep = None
    for c in campos:
        if c.get("tipo", "texto") != "texto":
            continue
        if cod is None and c["categoria"] == "cidade/uf":
            m = gabarito.municipio(c["texto"])
            cod = m[2] if m else None
        if cep is None and c["categoria"] == "cep":
            d = re.sub(r"\D", "", c["texto"])
            cep = d if len(d) == 8 else None
    return cod, cep


def _sob_a_rua(campos: list[dict], c: dict) -> bool:
    """A linha vem logo abaixo da rua, ou do complemento que está logo abaixo da rua ("APTO 102")."""
    cima = _vizinho(campos, c, acima=True)
    if cima is None:
        return False
    if cima["categoria"] == "endereço":
        return True
    if gabarito.complemento(cima["texto"]):
        cc = _vizinho(campos, cima, acima=True)
        return cc is not None and cc["categoria"] == "endereço"
    return False


def _pessoa(texto: str) -> bool:
    """Nome de pessoa claro pelo gabarito (não empresa, não a concessionária)."""
    return (plausivel("nome", texto) and gabarito.nome_de_pessoa(texto) >= 0.9 and not gabarito.empresa(texto)
            and not gabarito.emissor(texto))


def _pelo_gabarito(campos: list[dict]) -> list[str]:
    """Linhas que nenhuma regra classificou, pelo gabarito do Brasil (rotulador.gabarito):
      - o município inteiro ("BELO HORIZONTE MG", "Fortaleza - CE") -> cidade/uf;
      - rua conhecida sem o tipo, com número ("RUI BARBOSA 455", "JOSÉ DE ALENCAR S/N") -> endereço;
      - complemento logo abaixo de um endereço ("BLOCO C APTO 204") -> endereço;
      - bairro do município ou do CEP do documento (ALDEOTA em Fortaleza), perto do bloco de endereço -> bairro;
      - nome de pessoa claro, em qualquer lugar ("JOSÉ CARLOS RIBEIRO" sozinho, 2ª ocorrência) -> nome.
    Muita rua, bairro e cidade tem nome de gente (GETÚLIO VARGAS, CARLOS PRATES, JOÃO PESSOA, ANTÔNIO CARLOS): quem
    decide é a posição no envelope (nome / rua / bairro / CEP cidade): logo ACIMA da rua é o nome, logo ABAIXO dela
    nunca é; e a linha solta que é homenagem famosa (rua em dezenas de cidades) não vira nome.
    Devolve os ids que classificou (o bloco do emissor desfaz os vizinhos da rua dele)."""
    if not gabarito.disponivel():
        return []
    feitos = []

    def marcar(c, cat):
        c["categoria"] = cat
        feitos.append(c["id"])

    def acima_da_rua(c):
        baixo = _vizinho(campos, c, acima=False)
        return baixo is not None and baixo["categoria"] == "endereço"

    soltas = [c for c in campos if c["categoria"] is None and c.get("tipo", "texto") == "texto"]
    # cidade: com a UF é cidade em qualquer lugar; sem a UF, a que tem nome de gente depende da posição
    for c in soltas:
        if len(c["texto"].split()) <= 6 and plausivel("cidade/uf", c["texto"]) and (m := gabarito.municipio(c["texto"])) \
                and gabarito.norm(c["texto"]).split()[-1] == m[1]:
            marcar(c, "cidade/uf")
    for c in soltas:
        if c["categoria"] is not None or len(c["texto"].split()) > 6 or not plausivel("cidade/uf", c["texto"]) \
                or not gabarito.municipio(c["texto"]):
            continue
        if _pessoa(c["texto"]):
            cima, baixo = _vizinho(campos, c, acima=True), _vizinho(campos, c, acima=False)
            if cima is None and baixo is None:
                continue  # "GETÚLIO VARGAS" sozinho na página: homenagem, não a cidade do cliente
            if acima_da_rua(c):
                continue  # "PAULO AFONSO" acima da rua é o cliente
            if _sob_a_rua(campos, c) and baixo is not None and baixo["categoria"] in ("cidade/uf", "cep"):
                continue  # debaixo da rua e com a cidade embaixo: é o bairro
        elif _sob_a_rua(campos, c) and (b := _vizinho(campos, c, acima=False)) is not None \
                and b["categoria"] in ("cidade/uf", "cep"):
            continue  # "PRESIDENTE KENNEDY" debaixo da rua, "VITÓRIA ES" embaixo: bairro
        marcar(c, "cidade/uf")
    cod, cep = _local_do_documento(campos)
    # rua sem o tipo: da cidade/CEP do documento, ou do país logo abaixo de um nome de pessoa
    for c in soltas:
        if c["categoria"] is not None:
            continue
        r = gabarito.logradouro_sem_tipo(c["texto"], cod, cep)
        cima = _vizinho(campos, c, acima=True)
        if r in ("cidade", "cep") or r == "pais" and cima is not None \
                and (cima["categoria"] == "nome" or _pessoa(cima["texto"]) or gabarito.empresa(cima["texto"])):
            marcar(c, "endereço")
    endereco = ("endereço", "cep", "cidade/uf", "nome", "bairro")
    aceitos = ("cidade", "cep") if cod or cep else ("cidade", "cep", "pais")
    for c in soltas:
        if c["categoria"] is not None:
            continue
        cima, baixo = _vizinho(campos, c, acima=True), _vizinho(campos, c, acima=False)
        perto = any(v is not None and v["categoria"] in endereco for v in (cima, baixo))
        pessoa = _pessoa(c["texto"])
        b = gabarito.bairro(c["texto"], cod, cep) if perto and len(c["texto"].split()) <= 5 \
            and plausivel("bairro", c["texto"]) else ""
        if cima is not None and cima["categoria"] == "endereço" and gabarito.complemento(c["texto"]):
            marcar(c, "endereço")
        elif _sob_a_rua(campos, c):
            # debaixo da rua vem o bairro, nunca o nome: CARLOS PRATES, JOSÉ WALTER (fora da base fica para o bloco)
            if b:
                marcar(c, "bairro")
        elif pessoa and (acima_da_rua(c) or b not in ("cidade", "cep")
                         and gabarito.homenagem(c["texto"]) < gabarito.HOMENAGEM_MINIMA):
            # acima da rua é o nome mesmo se for bairro ("MARIA DOS SANTOS"); solto, o nome claro vence o bairro de
            # outro lugar, mas a homenagem famosa (GETÚLIO VARGAS, RUI BARBOSA) não é cliente
            marcar(c, "nome")
        elif b in aceitos:
            marcar(c, "bairro")
    return feitos


def _separar_nome_documento(img: np.ndarray, campo: dict) -> list[dict]:
    """"MARIA DAS GRAÇAS PEREIRA - CPF: 123.456.789-09": a linha era só cpf/cnpj e o nome se perdia. Com o nome de
    pessoa antes do documento (gabarito), vira dois campos: o nome e o CPF, cada um com a caixa da sua tinta."""
    t = campo["texto"]
    m = re.search(r"(\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\s*$", t)
    if not m or m.start() == 0:
        return [campo]
    antes = re.sub(r"[\s\-–,:]*(CPF|CNPJ|CPF/CNPJ)?[\s\-–,:]*$", "", t[:m.start()], flags=re.I).strip()
    if not antes or gabarito.nome_de_pessoa(antes) < 0.75:
        return [campo]
    a = t.index(antes)
    b = a + len(antes)
    caixa_nome = caixa_do_trecho(img, campo["caixa_ocr"], t, a, b)
    caixa_doc = caixa_do_trecho(img, campo["caixa_ocr"], t, m.start(1), m.end(1))
    if not caixa_nome or not caixa_doc or caixa_nome[2] > caixa_doc[0] + 2:
        return [campo]
    return [{**campo, "id": f"{campo['id']}n", "categoria": "nome", "texto": antes, "caixa": caixa_nome,
             "caixa_ocr": caixa_nome, "parte_de": campo["id"]},
            {**campo, "texto": m.group(1), "caixa": caixa_doc, "caixa_ocr": caixa_doc}]


def _bloco_de_enderecamento(campos: list[dict], H: int | None = None, do_gabarito: list[str] = ()) -> None:
    """Bloco de endereçamento SEM rótulos (a etiqueta do correio, no alto da conta): nome do cliente na linha
    de cima da rua, bairro na de baixo. A rua é achada pelo tipo de logradouro. O bloco do EMISSOR (``_do_emissor``)
    não é do cliente: a rua dele vira emissor e os vizinhos não viram nome/bairro (o modo exato reescreveria o
    endereço da concessionária); os que o gabarito já tinha marcado (``do_gabarito``) voltam a ficar sem categoria.
    ``H``: altura da página (faixa do cabeçalho e do rodapé)."""
    for c in list(campos):
        if c["categoria"] != "endereço" or c.get("rotulo"):
            continue
        cima = _vizinho(campos, c, acima=True)
        baixo = _vizinho(campos, c, acima=False)
        if _do_emissor(campos, [x for x in (cima, c, baixo) if x is not None], H):
            c["categoria"] = "emissor/logotipo"
            for x in (cima, baixo):
                if x is not None and x["id"] in do_gabarito and x["categoria"] in ("nome", "bairro"):
                    x["categoria"] = None
            continue
        if cima is not None and cima["categoria"] is None and plausivel("nome", cima["texto"]) \
                and _parece_nome(cima["texto"]):
            cima["categoria"] = "nome"
        if baixo is not None and baixo["categoria"] is None and gabarito.complemento(baixo["texto"]):
            baixo["categoria"] = "endereço"  # "BLOCO C APTO 204" na linha de baixo é complemento da rua
            baixo = _vizinho(campos, baixo, acima=False)
        if baixo is not None and baixo["categoria"] is None and plausivel("bairro", baixo["texto"]) \
                and not categoria_por_texto(baixo["texto"]) and not gabarito.municipio(baixo["texto"]):
            baixo["categoria"] = "bairro"


def _partes_do_boleto(campos: list[dict], rotulos: list[dict]) -> None:
    """No boleto, o bloco do Beneficiário/Cedente (nome e endereço do EMISSOR) vem antes do bloco do Pagador:
    um endereço ou nome cujo rótulo de parte mais próximo acima é do beneficiário é do emissor."""
    partes = [(r, PARTES[k]) for r in rotulos if (k := rotulo_de(r["texto"])) in PARTES]
    if not partes:
        return
    for c in campos:
        if c["categoria"] not in ("endereço", "nome"):
            continue
        x0, y0, x1, y1 = c["caixa"]
        h = max(1, y1 - y0)
        acima = [(y0 - r["caixa"][3], parte) for r, parte in partes
                 if 0 <= y0 - r["caixa"][3] <= 6 * h and r["caixa"][0] <= x1 and r["caixa"][2] >= x0 - 3 * h]
        if acima and min(acima)[1] == "emissor":
            c["categoria"] = "emissor/logotipo"


# "CEP CIDADE UF" numa linha só (o envelope: "30180-101 BELO HORIZONTE MG", "81500-000 - Curitiba/PR") ou a cidade
# antes ("Fortaleza - CE, 60750-720"): o resto, sem dígito, é a cidade com a UF
_CEP_TXT = r"(?:CEP[:\s]*)?(\d{2}\.?\d{3}-?\d{3})"
_CEP_CIDADE = re.compile(rf"^\s*{_CEP_TXT}\s*[-–,]?\s*([^\d]{{3,}}?)\s*$", re.I)
_CIDADE_CEP = re.compile(rf"^\s*([^\d]{{3,}}?)\s*[-–,]?\s*{_CEP_TXT}\s*$", re.I)


def _separar_cep_cidade(img: np.ndarray, campo: dict) -> list[dict]:
    """O campo de CEP que tem a cidade junto vira dois: o CEP e a cidade/UF, cada um com a caixa da sua tinta. Sem
    isso a linha inteira era "cep" e a cidade nunca era trocada (cidade/uf certa em 8% da bateria de 08/10)."""
    t = campo["texto"]
    m = _CEP_CIDADE.match(t)
    if m:
        (a, b), (c, d) = m.span(1), m.span(2)
    elif (m := _CIDADE_CEP.match(t)):
        (c, d), (a, b) = m.span(1), m.span(2)
    else:
        return [campo]
    cidade = t[c:d].strip(" -–,")
    if sum(ch.isalpha() for ch in cidade) < 3 or ":" in cidade or re.match(r"(?i)^CEP\b", cidade):
        return [campo]
    c += len(t[c:d]) - len(t[c:d].lstrip(" -–,"))
    d -= len(t[c:d]) - len(t[c:d].rstrip(" -–,"))
    caixa_cep = caixa_do_trecho(img, campo["caixa_ocr"], t, a, b)
    caixa_cid = caixa_do_trecho(img, campo["caixa_ocr"], t, c, d)
    if not caixa_cep or not caixa_cid or caixa_cep[2] > caixa_cid[0] + 2 and caixa_cid[2] > caixa_cep[0] + 2:
        return [campo]  # não deu para separar a tinta dos dois (ficam juntos, como antes)
    return [{**campo, "texto": t[a:b], "caixa": caixa_cep, "caixa_ocr": caixa_cep},
            {**campo, "id": f"{campo['id']}u", "categoria": "cidade/uf", "texto": cidade, "caixa": caixa_cid,
             "caixa_ocr": caixa_cid, "parte_de": campo["id"]}]


def detectar(img: np.ndarray, linhas: list[dict]) -> list[dict]:
    """Campos editáveis do documento, de cima para baixo."""
    H, W = img.shape[:2]
    rotulos = [l for l in linhas if _so_rotulo(l["texto"])]
    campos = []
    ceps = []  # (campo do endereço, início e fim do CEP no texto): o CEP no fim da rua vira subcampo
    barras = detectar_codigo_barras(img)
    for i, l in enumerate(linhas):
        if l in rotulos:
            continue
        texto = l["texto"].strip()
        bruto = l.get("bruto", texto)
        if not texto or len(re.sub(r"\W", "", texto)) < 2:
            continue
        caixa_valor = l["caixa"]
        rotulo_colado, resto = separar_rotulo(bruto)
        if rotulo_colado:  # rótulo e valor numa linha só: o campo é só o valor (pelo vão de tinta)
            palavras = ocr.palavras_por_tinta(img, l["caixa"])
            if len(palavras) >= 2:
                # o rótulo pode ter várias palavras ("DATA DA LEITURA:"): o valor começa no bloco de tinta
                # mais perto de onde a proporção do texto (largura do valor / da linha) diz que ele começa
                f = ocr._fonte_medida()
                frac = f.getlength(resto) / max(1.0, f.getlength(bruto))
                inicio = palavras[-1][1] - frac * (palavras[-1][1] - palavras[0][0])
                k = min(range(1, len(palavras)), key=lambda j: abs(palavras[j][0] - inicio))
                caixa_valor = [palavras[k][0], l["caixa"][1], l["caixa"][2], l["caixa"][3]]
            else:  # sem vão de tinta entre os dois ("CPF:905.214.638-71"): pela largura de cada letra
                ini = len(bruto.rstrip()) - len(resto)
                caixa_valor = caixa_do_trecho(img, l["caixa"], bruto, ini, ini + len(resto)) or caixa_valor
            if caixa_valor is not l["caixa"]:
                bruto = resto
                texto = ocr.recuperar_espacos(resto, img, caixa_valor) if " " not in resto else resto
        cat = categoria_por_texto(bruto) or categoria_por_texto(texto)  # o cru não tem espaços inventados
        if rotulo_colado and not cat:
            cat = ROTULOS.get(rotulo_colado)
        if cat == "linha digitável":
            texto = formatar_linha_digitavel(bruto)
        if cat == "valor":  # o OCR lê "R$ 5,48" como "RS5.48": o texto do campo precisa ser o que está na tinta
            texto = re.sub(r"^R[S5]\s*", "R$ ", texto)
            if "," not in texto:
                texto = re.sub(r"\.(\d{2})$", r",\1", texto)
        rotulo_cat, rotulo_txt = _rotulo_vizinho(l, rotulos)
        if cat is None or (rotulo_cat and cat in ("data", "valor", "consumo/leitura") and rotulo_cat in (
                "data", "valor", "consumo/leitura", "mês de referência")):
            cat = rotulo_cat or cat  # o rótulo diz se a data é vencimento ou se o número é consumo
        if not plausivel(cat, texto):
            cat = None
        datas = list(_DATA.finditer(texto)) if cat == "data" else []
        m = datas[0] if datas else None
        # duas datas na linha ("Período de consumo: 29/08/2026 a 28/09/2026", as duas leituras que o OCR juntou numa
        # linha só): um campo para cada, com o rótulo que vem antes dela ("Leitura atual"); antes a 2ª se perdia
        outras = []
        if len(datas) >= 2:
            # cada data medida na linha INTEIRA do OCR: o vão largo entre as duas leituras não está no texto, e a
            # caixa do valor (estimada pela proporção do texto depois do rótulo) punha a 1ª data no lugar errado
            na_linha = list(_DATA.finditer(l["texto"]))
            base_caixa, base_txt = (l["caixa"], l["texto"]) if len(na_linha) == len(datas) else (caixa_valor, texto)
            pos = na_linha if len(na_linha) == len(datas) else datas
            trechos = [caixa_do_trecho(img, base_caixa, base_txt, md.start(), md.end()) for md in pos]
            if all(trechos):
                for k, md in enumerate(datas[1:], 1):
                    entre = texto[datas[k - 1].end():md.start()].strip(" :-–,")
                    outras.append((trechos[k], md.group(0), entre if sum(ch.isalpha() for ch in entre) >= 3 else None))
                caixa_valor, texto, m = trechos[0], datas[0].group(0), None
        # data colada no rótulo ("Emissaoem25/01/2026") ou com o resto dele depois do rótulo separado ("Emissão" +
        # "em 25/01/2026"): só a data. Com o "em" junto, a fonte era identificada na palavra regular e a data nova
        # saía fina numa linha em negrito
        if m and re.search(r"[A-Za-z]", texto[:m.start()]):
            trecho = caixa_do_trecho(img, caixa_valor, texto, m.start(), m.end())
            if trecho:
                caixa_valor, texto = trecho, m.group(0)
        c = caixa_valor
        if any(c[0] >= b[0] - 4 and c[2] <= b[2] + 4 and c[1] >= b[1] - 4 and c[3] <= b[3] + 4 for b in barras):
            continue  # texto "lido" dentro das barras é ruído do OCR
        caixa_tinta = ocr.caixa_da_linha(img, c)
        if cat == "consumo/leitura" and re.search(r"[mM]\s?3$", texto):
            texto = _sobrescrito(img, caixa_tinta, texto)
        campo = {"id": f"t{i}", "categoria": cat, "texto": texto, "caixa": caixa_tinta,
                 "caixa_ocr": c, "rotulo": rotulo_txt or (rotulo_colado or ""), "conf": round(l["conf"], 2), "tipo": "texto"}
        campos += (_separar_cep_cidade(img, campo) if cat == "cep" else
                   _separar_nome_documento(img, campo) if cat == "cpf/cnpj" and gabarito.disponivel() else [campo])
        for k, (trecho, txt, rot) in enumerate(outras, 1):
            campos.append({**campo, "id": f"t{i}d{k}", "texto": txt, "caixa": ocr.caixa_da_linha(img, trecho),
                           "caixa_ocr": trecho, "rotulo": rot or campo["rotulo"]})
        if cat == "endereço" and (m := re.search(r"\b\d{5}-\d{3}\b", texto)):
            ceps.append((campos[-1], m.start(), m.end()))
    # o envelope do cliente antes do "topo da página = emissor": o bloco do cliente nos 12% de cima virava logotipo
    do_gabarito = _pelo_gabarito(campos)  # cidade, complemento, bairro e nome que nenhuma regra pegou
    _bloco_de_enderecamento(campos, H, do_gabarito)
    for c in campos:
        if c["categoria"] is None and c["caixa_ocr"][3] < 0.12 * H:
            c["categoria"] = "emissor/logotipo"
    _partes_do_boleto(campos, rotulos)
    for c, a, b in ceps:  # (a rua do emissor já virou emissor: o CEP dela não entra)
        trecho = caixa_do_trecho(img, c["caixa_ocr"], c["texto"], a, b) if c["categoria"] == "endereço" else None
        if trecho:
            campos.append({**c, "id": f"{c['id']}c", "categoria": "cep", "texto": c["texto"][a:b], "caixa": trecho,
                           "caixa_ocr": trecho, "rotulo": "", "parte_de": c["id"]})
    for j, b in enumerate(barras):
        campos.append({"id": f"b{j}", "categoria": "codigo barras", "texto": "(código de barras)", "caixa": b,
                       "caixa_ocr": b, "rotulo": "", "conf": 1.0, "tipo": "barras"})
    # (só linha de texto de verdade: o lixo que o OCR "lê" nos módulos de um QR real não conta)
    textos = [c["caixa"] for c in campos if c.get("tipo", "texto") == "texto" and c["conf"] >= 0.6
              and len(re.sub(r"\W", "", c["texto"])) >= 4]
    for j, q in enumerate(detectar_qr(img)):
        dentro = sum(q[0] <= (t[0] + t[2]) / 2 <= q[2] and q[1] <= (t[1] + t[3]) / 2 <= q[3] for t in textos)
        if dentro >= 2:
            continue  # QR não tem linhas de texto dentro: é o detector vendo padrão de canto nas letras
        campos.append({"id": f"q{j}", "categoria": "qr code/pix", "texto": "(QR code)", "caixa": q,
                       "caixa_ocr": q, "rotulo": "", "conf": 1.0, "tipo": "qr"})
    campos.sort(key=lambda c: (c["caixa"][1], c["caixa"][0]))
    return campos
