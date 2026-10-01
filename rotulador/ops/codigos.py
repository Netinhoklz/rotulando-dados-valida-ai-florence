"""Código de barras ITF-25 (padrão FEBRABAN), linha digitável e QR code (PIX).

Contas de consumo (luz, água, gás, telefone) usam o código de ARRECADAÇÃO
(começa com 8); boletos usam o código BANCÁRIO (começa com o código do banco).
Os dois têm 44 dígitos e são impressos em ITF-25 (Interleaved 2 of 5).
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado
from .base import compor, cor, exigir_selecao, num

# larguras dos 5 elementos de cada dígito: 0 = estreito, 1 = largo
_ITF = {"0": "00110", "1": "10001", "2": "01001", "3": "11000", "4": "00101",
        "5": "10100", "6": "01100", "7": "00011", "8": "10010", "9": "01010"}


# ---------------------------------------------------------------------------
# dígitos verificadores
# ---------------------------------------------------------------------------

def mod10(digitos: str) -> int:
    total = 0
    for i, c in enumerate(reversed(digitos)):
        v = int(c) * (2 if i % 2 == 0 else 1)
        total += v // 10 + v % 10
    return (10 - total % 10) % 10


def mod11_arrecadacao(digitos: str) -> int:
    total = sum(int(c) * (2 + i % 8) for i, c in enumerate(reversed(digitos)))
    dv = 11 - total % 11
    return 0 if dv >= 10 else dv


def mod11_boleto(digitos: str) -> int:
    total = sum(int(c) * (2 + i % 8) for i, c in enumerate(reversed(digitos)))
    dv = 11 - total % 11
    return 1 if dv in (0, 10, 11) else dv


def _dv_arrecadacao(digitos: str, ref: str) -> int:
    return mod10(digitos) if ref in "67" else mod11_arrecadacao(digitos)


# ---------------------------------------------------------------------------
# arrecadação (contas de consumo)
# ---------------------------------------------------------------------------

def arrecadacao(valor_centavos: int, segmento: str = "3", empresa: str = "0000", livre: str = "",
                ref: str = "6") -> str:
    """Código de barras de arrecadação de 44 dígitos (DV geral na 4ª posição)."""
    if ref not in "6789" or len(ref) != 1:
        raise ErroOperacao("identificação de valor deve ser 6, 7, 8 ou 9")
    valor = f"{int(valor_centavos):011d}"
    if len(valor) != 11:
        raise ErroOperacao("valor grande demais")
    empresa = (empresa or "").zfill(4)[:4]
    livre = (livre or "").ljust(25, "0")[:25]
    sem_dv = f"8{segmento}{ref}" + valor + empresa + livre
    if not sem_dv.isdigit() or len(sem_dv) != 43:
        raise ErroOperacao("dígitos inválidos")
    dv = _dv_arrecadacao(sem_dv, ref)
    return sem_dv[:3] + str(dv) + sem_dv[3:]


def linha_arrecadacao(codigo: str) -> str:
    if len(codigo) != 44 or not codigo.isdigit() or codigo[0] != "8":
        raise ErroOperacao("código de arrecadação precisa de 44 dígitos começando com 8")
    ref = codigo[2]
    blocos = [codigo[i:i + 11] for i in range(0, 44, 11)]
    return " ".join(f"{b}-{_dv_arrecadacao(b, ref)}" for b in blocos)


def valida_arrecadacao(codigo: str) -> bool:
    return (len(codigo) == 44 and codigo.isdigit() and codigo[0] == "8"
            and _dv_arrecadacao(codigo[:3] + codigo[4:], codigo[2]) == int(codigo[3]))


# ---------------------------------------------------------------------------
# boleto bancário
# ---------------------------------------------------------------------------

def fator_vencimento(venc: dt.date) -> int:
    dias = (venc - dt.date(1997, 10, 7)).days
    if dias <= 9999:
        return dias
    return (venc - dt.date(2025, 2, 22)).days % 9000 + 1000  # reinício do fator em 22/02/2025


def boleto(banco: str, valor_centavos: int, vencimento: dt.date, livre: str) -> str:
    banco = banco.zfill(3)[:3]
    corpo = banco + "9" + f"{fator_vencimento(vencimento):04d}" + f"{int(valor_centavos):010d}" + livre.ljust(25, "0")[:25]
    if not corpo.isdigit() or len(corpo) != 43:
        raise ErroOperacao("dígitos inválidos")
    return corpo[:4] + str(mod11_boleto(corpo)) + corpo[4:]


def linha_boleto(codigo: str) -> str:
    if len(codigo) != 44 or not codigo.isdigit():
        raise ErroOperacao("código de boleto precisa de 44 dígitos")
    livre = codigo[19:]
    c1 = codigo[:4] + livre[:5]
    c2, c3 = livre[5:15], livre[15:25]
    c1, c2, c3 = c1 + str(mod10(c1)), c2 + str(mod10(c2)), c3 + str(mod10(c3))
    return f"{c1[:5]}.{c1[5:]} {c2[:5]}.{c2[5:]} {c3[:5]}.{c3[5:]} {codigo[4]} {codigo[5:19]}"


def valida_boleto(codigo: str) -> bool:
    return len(codigo) == 44 and codigo.isdigit() and mod11_boleto(codigo[:4] + codigo[5:]) == int(codigo[4])


# ---------------------------------------------------------------------------
# PIX (BR Code "copia e cola")
# ---------------------------------------------------------------------------

def crc16(dados: str) -> str:
    crc = 0xFFFF
    for b in dados.encode("utf-8"):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def _campo(id_: str, valor: str) -> str:
    return f"{id_}{len(valor):02d}{valor}"


def pix(chave: str, nome: str, cidade: str, valor: str = "", txid: str = "***") -> str:
    conta = _campo("00", "br.gov.bcb.pix") + _campo("01", chave)
    p = (_campo("00", "01") + _campo("26", conta) + _campo("52", "0000") + _campo("53", "986")
         + (_campo("54", valor) if valor else "") + _campo("58", "BR") + _campo("59", nome[:25])
         + _campo("60", cidade[:15]) + _campo("62", _campo("05", txid[:25])) + "6304")
    return p + crc16(p)


# ---------------------------------------------------------------------------
# renderização
# ---------------------------------------------------------------------------

def modulos_itf(digitos: str, largo: float = 3.0) -> list[tuple[bool, float]]:
    """Sequência (é_barra, largura em módulos estreitos) do ITF-25 com início e fim."""
    if not digitos.isdigit() or len(digitos) % 2:
        raise ErroOperacao("o ITF precisa de uma quantidade PAR de dígitos (0-9)")
    seq = [(True, 1), (False, 1), (True, 1), (False, 1)]
    for a, b in zip(digitos[::2], digitos[1::2]):
        for wa, wb in zip(_ITF[a], _ITF[b]):
            seq += [(True, largo if wa == "1" else 1), (False, largo if wb == "1" else 1)]
    seq += [(True, largo), (False, 1), (True, 1)]
    return seq


def perfil_barras(digitos: str, largura_px: int, largo: float = 3.0, margem: float = 0.0) -> np.ndarray:
    """Cobertura (0..1) de barra por coluna, com antisserrilhado por área."""
    seq = modulos_itf(digitos, largo)
    total = sum(w for _, w in seq) + 2 * margem
    escala = largura_px / total
    if escala < 1.0:
        raise ErroOperacao(f"seleção estreita demais para {len(digitos)} dígitos: "
                           f"use ao menos {int(np.ceil(total))} px de largura")
    S = 16
    amostras = (np.arange(largura_px * S) + 0.5) / S  # posição em px
    pos = amostras / escala - margem  # posição em módulos
    bordas = np.cumsum([0.0] + [w for _, w in seq])
    idx = np.searchsorted(bordas, pos, side="right") - 1
    valido = (idx >= 0) & (idx < len(seq))
    barra = np.zeros(pos.shape, np.float32)
    eh_barra = np.array([b for b, _ in seq])
    barra[valido] = eh_barra[idx[valido]]
    return barra.reshape(largura_px, S).mean(1)


def codigo_barras(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    exigir_selecao(sel)
    digitos = "".join(ch for ch in str(p.get("digitos", "")) if ch.isdigit())
    y0, y1, x0, x1 = sel.bbox
    perfil = perfil_barras(digitos, x1 - x0, num(p, "largo", 3.0, 2.0, 3.5), num(p, "margem", 0, 0, 40))
    fundo = compor(img[y0:y1, x0:x1], cor(p, "cor_fundo", (255, 255, 255)), sel.alpha)
    barras = np.broadcast_to(perfil[None, :], (y1 - y0, x1 - x0)) * sel.alpha
    novo = compor(fundo, cor(p), barras)
    return Resultado(y0, y1, x0, x1, novo, sel.alpha > 0, meta={"digitos": digitos})


def qrcode(img: np.ndarray, p: dict, sel: Selecao, **_) -> Resultado:
    exigir_selecao(sel)
    import segno

    conteudo = str(p.get("conteudo", ""))
    if not conteudo:
        raise ErroOperacao("informe o conteúdo do QR code")
    erro = str(p.get("correcao", "M")).upper()
    if erro not in "LMQH" or len(erro) != 1:
        erro = "M"
    borda = int(num(p, "borda", 1, 0, 8))
    qr = segno.make(conteudo, error=erro, micro=False)
    matriz = np.array([list(linha) for linha in qr.matrix], bool)
    if borda:
        matriz = np.pad(matriz, borda)
    n = matriz.shape[0]
    y0, y1, x0, x1 = sel.bbox
    lado = min(y1 - y0, x1 - x0)
    if lado < n:
        raise ErroOperacao(f"seleção pequena demais para o QR ({n} módulos): use ao menos {n}x{n} px")
    oy, ox = y0 + (y1 - y0 - lado) // 2, x0 + (x1 - x0 - lado) // 2
    centros = ((np.arange(lado) + 0.5) * n / lado).astype(int)
    modulo = matriz[np.ix_(centros, centros)].astype(np.float32)
    alpha_sel = sel.alpha[oy - y0:oy - y0 + lado, ox - x0:ox - x0 + lado]
    antes = img[oy:oy + lado, ox:ox + lado]
    fundo = compor(antes, cor(p, "cor_fundo", (255, 255, 255)), alpha_sel)
    novo = compor(fundo, cor(p), modulo * alpha_sel)
    return Resultado(oy, oy + lado, ox, ox + lado, novo, alpha_sel > 0, meta={"modulos": int(n)})
