"""Operações de edição. Todas têm a assinatura

    op(img_atual, params, selecao, base=original, **contexto) -> Resultado

e não alteram ``img_atual``: devolvem o novo conteúdo de uma caixa e a pegada.
"""

from __future__ import annotations

from ..sessao import ErroOperacao
from . import ajustes, clonagem, codigos, ia, pintura, pro, retoque, texto

OPERACOES = {
    "pincel": pintura.pincel,
    "borracha": pintura.borracha,
    "balde": pintura.balde,
    "preencher": retoque.preencher_selecao,
    "corretivo": retoque.corretivo,
    "carimbo": pro.carimbo,
    "recuperacao": pro.recuperacao,
    "remendo": pro.remendo,
    "retoque": pro.retoque,
    "forma": pro.forma,
    "texto": texto.texto,
    "substituir_texto": texto.substituir_texto,
    "transformar": clonagem.transformar,
    "colar": clonagem.colar,
    "ajuste": ajustes.ajuste,
    "codigo_barras": codigos.codigo_barras,
    "qrcode": codigos.qrcode,
    "ia": ia.editar,
}

# rápidas o bastante para prévia ao vivo (substituir_texto e transformar reusam o "apagado" em cache)
COM_PREVIA = {"texto", "substituir_texto", "ajuste", "codigo_barras", "qrcode", "transformar", "colar", "balde",
              "preencher", "remendo", "forma"}


def executar(tipo: str, img, params: dict, sel, **contexto):
    if tipo not in OPERACOES:
        raise ErroOperacao(f"operação desconhecida: {tipo}")
    if not isinstance(params, dict):
        raise ErroOperacao("parâmetros inválidos")
    return OPERACOES[tipo](img, params, sel, **contexto)
