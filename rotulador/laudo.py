"""Laudo: o relatório forense em texto de cada versão, montado a partir da anotação.

Os benchmarks de 2025–2026 de texto adulterado (TFR/TextShield-R1, RealText-V2/SEED) trazem, além da
máscara, um relatório pericial por amostra, usado para treinar e avaliar detectores que explicam
(MLLM). Aqui o laudo sai de graça: a anotação já sabe o que foi feito (técnica, campo, texto antes e
depois, fornecedores, perícia, acabamento, canal), o que o perito mediu (pistas de pixel que sobraram)
e o que a coerência semântica diz (violações que a edição criou). Em português, uma linha por fato.
"""

from __future__ import annotations

_MEDIDAS = (("grao", "grão ×{:.2f}"), ("espectro", "espectro {:+.2f}"), ("nitidez", "nitidez ×{:.2f}"),
            ("tom", "tom {:.1f} níveis"), ("blocos", "grade JPEG ×{:.2f}"))
_REGRAS = {"cpf_cnpj": "CPF/CNPJ com DV inválido", "cep_cidade": "CEP fora da faixa da cidade",
           "datas_ordenadas": "datas fora de ordem", "mes_referencia": "mês de referência incoerente",
           "linha_digitavel": "linha digitável com DV errado", "codigo_total": "código de barras com valor diferente do total",
           "consumo_valor": "consumo incompatível com o valor", "bairro_cidade": "bairro de outra cidade",
           "endereco_cep": "logradouro trocado com o CEP antigo", "vencimento_util": "vencimento fora de dia útil",
           "telefone_ddd": "DDD do telefone de outro estado (ou inexistente)"}


def _edicao(e: dict) -> str:
    partes = [f"Edição {e.get('ordem', '?')}: {e.get('nome_tecnica') or e.get('tecnica')} (família {e.get('familia')}, "
              f"nível {e.get('nivel')}) no campo {e.get('categoria')}"]
    if e.get("texto_antigo") is not None:
        partes.append(f"'{e['texto_antigo']}' → '{e.get('texto_novo') or '(apagado)'}'")
    f = e.get("fornecedores") or {}
    if f:
        partes.append("peças: " + ", ".join(f"{k}={v}" for k, v in f.items()))
    if e.get("pericia") and e["pericia"] != "auto":
        partes.append(f"perícia {e['pericia']}")
    if e.get("acabamento"):
        partes.append("acabamento: " + "; ".join(str(x) for x in e["acabamento"]))
    p = e.get("perito")
    if p and p.get("nota") is not None:
        med = [fmt.format(p["medidas"][k]) for k, fmt in _MEDIDAS if (p.get("medidas") or {}).get(k) is not None]
        partes.append(f"pistas de pixel (perito) nota {float(p['nota']):.2f}" + (": " + ", ".join(med) if med else ""))
    return ". ".join(partes) + "."


def gerar(anot: dict) -> str:
    L = [f"Documento {anot.get('doc')} (split {anot.get('split')}, versão {anot.get('versao')}): "
         f"{len(anot.get('edicoes') or [])} edição(ões), {anot.get('pixels_alterados', 0)} pixels alterados."]
    fz = anot.get("falsificador") or {}
    if fz:
        L.append(f"Feita como {str(fz.get('rotulo', fz.get('nome', ''))).lower()}, motivo: "
                 f"{str(fz.get('motivo_rotulo', fz.get('motivo', ''))).lower()}"
                 + (f", captura {fz['captura']} da edição" if fz.get("captura") else "") + ".")
    if anot.get("captura_antes"):
        L.append(f"A base da versão é uma captura feita ANTES da edição: {anot['captura_antes']}.")
    if anot.get("canal_descricao") and anot.get("canal"):
        L.append(f"Canal de envio: {anot['canal_descricao']}.")
    marcas = anot.get("marcas_benignas") or []
    if marcas:
        L.append("Marcas benignas (não são fraude): " + ", ".join(m.get("nome", m.get("tipo", "")) for m in marcas) + ".")
    for e in anot.get("edicoes") or []:
        L.append(_edicao(e))
    c = anot.get("coerencia")
    if c:
        novas = c.get("novas_violacoes") or []
        if novas:
            L.append("Pistas semânticas criadas pela edição: " + "; ".join(_REGRAS.get(n, n) for n in novas) + ".")
        else:
            L.append("Sem pista semântica nova: o texto continua coerente (" + ", ".join(c.get("regras_aplicaveis") or [])
                     + ") — só o pixel pode acusar esta versão.")
        if c.get("valor_redondo_novo"):
            L.append("O valor novo é redondo (termina em ,00), um sinal fraco que detectores comerciais usam.")
    return "\n".join(L)
