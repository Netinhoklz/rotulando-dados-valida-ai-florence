"""Coerência semântica: as regras que um verificador (ou um modelo que lê o texto) aplica à conta.

A fronteira forense de 2026 é semântica: os recibos gerados por IA são visualmente perfeitos e caem
pela aritmética (GPT4o-Receipt: o subtotal não soma, o imposto não bate); a edição por difusão apaga
as descontinuidades de pixel (GPT-Image-2) e o que sobra é a plausibilidade do texto; detectores
comerciais de comprovante conferem CEP com cidade, código de barras com o total, datas em ordem e
dentro dos 90 dias. Um gerador que deixa essas incoerências por descuido dá ao modelo um atalho que a
fraude cuidadosa não dá; um que as rotula deixa medir a ponta "sem pista semântica".

``avaliar(campos)`` roda as regras nos campos detectados (ou nos campos com os textos novos) e
``comparar(antes, depois)`` diz quais violações a edição CRIOU (``novas_violacoes``) e se a versão
ficou sem nenhuma pista semântica nova (``sem_pista_semantica``). Cada regra devolve ``ok`` True,
False ou None (não se aplica a este documento). Regras:

  cpf_cnpj           dígitos verificadores válidos;
  cep_cidade         o CEP é da cidade/UF impressa: pela base de lugares, o prefixo de 5 dígitos é um dos que o
                     município usa e, na cidade de CEP único, o CEP é o de localidade (final 000); sem a base (ou
                     cidade que ela não conhece), cai na faixa dos Correios da cidade/UF;
  bairro_cidade      pela base de bairros: o bairro existe na cidade impressa (ok), só em outras (violação: PARANOÁ
                     em Feira de Santana) ou em nenhuma (não se aplica); sem a base, bairro que só existe numa
                     cidade (PARANOÁ, ALDEOTA) é dessa cidade e o genérico (CENTRO, JARDIM X) serve em qualquer uma;
  datas_ordenadas    leitura <= emissão <= vencimento, próxima leitura depois da leitura, vencimento
                     no máximo 90 dias depois da emissão (datas em qualquer formato de conta: 10/10/26,
                     2026-10-10, "10 de outubro de 2026");
  mes_referencia     o mês de referência é o do vencimento ou um dos dois anteriores;
  vencimento_util    o vencimento cai em dia útil (sábado, domingo e feriado nacional, fixo ou móvel, não);
  linha_digitavel    os dígitos verificadores da linha/código conferem;
  codigo_total       o valor codificado no código de barras é o total impresso;
  consumo_valor      R$/kWh (ou R$/m³) dentro de uma faixa plausível de tarifa (com a tarifa mínima: 30 kWh e
                     a iluminação pública na luz, 10 m³ na água);
  telefone_ddd       o DDD do telefone (campo "outro" ou com rótulo de telefone) é da UF da cidade impressa (os DDDs
                     de cada UF saem da coluna ddd da base de municípios, com o da divisa: o 61 no entorno do DF);
  valor_redondo      informativo (não é violação): algum valor termina em ,00.

Só em ``comparar`` (precisa do antes e do depois):

  endereco_cep       o logradouro (ou o bairro) mudou e o CEP ficou o antigo: o CEP aponta a rua nos Correios, e
                     quem confere o CEP na base acha a rua nova com o CEP de outra (CEP geral do município, final
                     -000, serve para qualquer rua; a rua nova que tem o CEP antigo na base de logradouros, também).
"""

from __future__ import annotations

import re
import unicodedata

from .ops import codigos, geradores

REGRAS = ("cpf_cnpj", "cep_cidade", "bairro_cidade", "datas_ordenadas", "mes_referencia", "vencimento_util",
          "linha_digitavel", "codigo_total", "consumo_valor", "telefone_ddd")
# faixas plausíveis de tarifa (R$ por unidade): luz em kWh, água/gás em m³
_TARIFA = {"KWH": (0.4, 2.5), "M3": (2.0, 40.0)}
# o mínimo que se cobra (custo de disponibilidade de 30 kWh na luz monofásica, tarifa mínima de 10 m³ na água) e a
# parte fixa que não depende do consumo (iluminação pública, até R$ 60)
_MINIMO = {"KWH": (30, 60.0), "M3": (10, 0.0)}
_DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()


def tipo_da_data(rotulo: str) -> str:
    """Que data é, pelo rótulo impresso ao lado: vencimento, emissao (também a data do documento e a do processamento
    do boleto), leitura, proxima_leitura, pagamento, nascimento ou outra."""
    r = _norm(rotulo).replace(" ", "")  # o OCR às vezes espaça as letras do rótulo: "V E N C I M E N TO"
    if "PROX" in r:
        return "proxima_leitura"
    if "NASC" in r:
        return "nascimento"
    if "VENC" in r:
        return "vencimento"
    if "EMISS" in r or "EMITID" in r or "DOCUMENTO" in r or "PROCESS" in r:
        return "emissao"
    if "LEIT" in r:
        return "leitura"
    if "PAG" in r:
        return "pagamento"
    return "outra"


def _dvs_da_linha(texto: str) -> bool | None:
    """Os DVs de cada bloco/campo da linha conferem? None se não é uma linha digitável."""
    dig = re.sub(r"\D", "", str(texto or ""))
    if len(dig) == 48 and dig[0] == "8":
        ref = dig[2]
        return all(int(dig[i + 11]) == codigos._dv_arrecadacao(dig[i:i + 11], ref) for i in range(0, 48, 12))
    if len(dig) == 47:
        return all(int(dig[j]) == codigos.mod10(dig[i:j]) for i, j in ((0, 9), (10, 20), (21, 31)))
    return None


def _valor_codificado(codigo: str) -> int | None:
    """Valor em centavos codificado: arrecadação nas posições 4..15 (11 dígitos), boleto nas 9..19 (10)."""
    if codigo[0] == "8":
        return int(codigo[4:15])
    return int(codigo[9:19])


def _total(campos: list[dict]) -> int | None:
    valores = []
    for f in campos:
        if f.get("categoria") != "valor":
            continue
        cent = geradores.valor_em_centavos(str(f.get("texto", "")))
        if cent:
            valores.append(("TOTAL" in _norm(f.get("rotulo", "")), cent))
    return max(valores)[1] if valores else None


def tarifa_ok(total_centavos: int, consumo: float, unidade: str) -> bool:
    """O total cabe no consumo? R$ por unidade (KWH ou M3) na faixa de tarifa, contando a tarifa mínima (consumo
    abaixo dela paga o mínimo) e a parte fixa da conta."""
    lo, hi = _TARIFA[unidade]
    minimo, fixo = _MINIMO[unidade]
    c = max(consumo, minimo)
    return lo * c <= total_centavos / 100 <= hi * c + fixo


def _consumo(campos: list[dict]) -> tuple[float, str] | None:
    for f in campos:
        if f.get("categoria") != "consumo/leitura":
            continue
        t = _norm(f.get("texto", "")).replace("³", "3")
        m = re.search(r"(\d[\d.,]*)\s*(KWH|M3)", t)
        if m:
            v = geradores._numero_br(m.group(1))
            if v is not None:  # 0 kWh (casa vazia) também: paga o mínimo
                return float(v), m.group(2)
    return None


def avaliar(campos: list[dict]) -> dict:
    """``{"regras": {nome: {"ok": bool|None, "detalhe": str}}, "violadas": [...], "valor_redondo": bool}``."""
    campos = [f for f in campos if str(f.get("texto", "")).strip()]
    regras: dict[str, dict] = {}

    def reg(nome, ok, detalhe=""):
        regras[nome] = {"ok": ok, "detalhe": detalhe}

    docs = [f["texto"] for f in campos if f.get("categoria") == "cpf/cnpj"]
    if docs:
        ruins = [d for d in docs if not (geradores.valida_cpf(d) if len(re.sub(r"\D", "", d)) == 11 else geradores.valida_cnpj(d))]
        reg("cpf_cnpj", not ruins, f"DV inválido em {', '.join(ruins)}" if ruins else "DVs conferem")
    else:
        reg("cpf_cnpj", None)

    cidade = next((geradores.cidade_do_texto(f["texto"]) for f in campos if f.get("categoria") == "cidade/uf"), None)
    ceps = [re.sub(r"\D", "", f["texto"]) for f in campos if f.get("categoria") == "cep"]
    confere = [geradores.cep_na_cidade(c, cidade) for c in ceps] if cidade else []
    if confere and None not in confere:  # a base de lugares conhece a cidade: os prefixos que ela usa
        fora = [c for c, ok in zip(ceps, confere) if not ok]
        reg("cep_cidade", not fora, f"CEP {', '.join(fora)} não é de {cidade[0]}/{cidade[1]}" if fora
            else f"CEP de {cidade[0]}/{cidade[1]}")
    elif cidade and ceps:
        faixa = geradores._faixa_cep(*cidade)
        fora = [c for c in ceps if faixa and not (faixa[0] <= int(c or 0) <= faixa[1])]
        reg("cep_cidade", not fora if faixa else None, f"CEP {', '.join(fora)} fora da faixa de {cidade[0]}/{cidade[1]}" if fora
            else f"CEP na faixa de {cidade[0]}/{cidade[1]}")
    else:
        reg("cep_cidade", None)

    bairro = next((f["texto"] for f in campos if f.get("categoria") == "bairro"), None)
    na_base = geradores.bairro_na_cidade(bairro, cidade) if bairro and cidade else None
    donos = geradores.cidades_do_bairro(bairro) if bairro else None
    if na_base is not None or (cidade and geradores._municipio(cidade) and bairro):  # a base conhece a cidade
        onde = [] if na_base is not False else geradores.onde_ha_bairro(bairro)
        reg("bairro_cidade", na_base, f"bairro {bairro} {'é' if na_base else 'não é'} de {cidade[0]}/{cidade[1]}"
            + (f" (é de {', '.join(f'{c}/{u}' for c, u in onde[:5])}{'...' if len(onde) > 5 else ''})" if onde else "")
            if na_base is not None else f"bairro {bairro} não está na base de {cidade[0]}/{cidade[1]}")
    elif cidade and donos is not None:
        ok = not donos or (geradores._normalizar(cidade[0]), cidade[1]) in donos
        reg("bairro_cidade", ok, f"bairro {bairro} serve em qualquer cidade" if not donos else
            f"bairro {bairro} {'é' if ok else 'não é'} de {cidade[0]}/{cidade[1]}"
            + ("" if ok else f" (é de {', '.join(f'{c}/{u}' for c, u in sorted(donos))})"))
    else:
        reg("bairro_cidade", None)

    datas = {}
    for f in campos:
        if f.get("categoria") == "data":
            d = geradores.ler_data(f["texto"])
            if d:
                datas.setdefault(tipo_da_data(f.get("rotulo", "")), d)
    if len(datas) >= 2:
        problemas = []
        le, em, ve, pr = datas.get("leitura"), datas.get("emissao"), datas.get("vencimento"), datas.get("proxima_leitura")
        if le and em and le > em:
            problemas.append("leitura depois da emissão")
        if em and ve and ve < em:
            problemas.append("vencimento antes da emissão")
        if em and ve and (ve - em).days > 90:
            problemas.append("vencimento mais de 90 dias depois da emissão")
        if le and ve and ve < le:
            problemas.append("vencimento antes da leitura")
        if le and pr and pr <= le:
            problemas.append("próxima leitura não é depois da leitura")
        reg("datas_ordenadas", not problemas, "; ".join(problemas) if problemas else "datas em ordem")
    else:
        reg("datas_ordenadas", None)

    ref = next((geradores.ler_mes(f["texto"]) for f in campos if f.get("categoria") == "mês de referência"), None)
    base = datas.get("vencimento") or datas.get("emissao") or next(iter(datas.values()), None)
    if ref and base:
        meses = (base.year - ref[0]) * 12 + (base.month - ref[1])
        reg("mes_referencia", 0 <= meses <= 2, f"referência {ref[1]:02d}/{ref[0]} a {meses} mês(es) do vencimento")
    else:
        reg("mes_referencia", None)

    ve = datas.get("vencimento")
    if ve:
        motivo = "feriado" if ve in geradores.feriados(ve.year) else _DIAS_SEMANA[ve.weekday()]
        reg("vencimento_util", geradores.dia_util(ve), f"vencimento {ve:%d/%m/%Y} ({motivo})")
    else:
        reg("vencimento_util", None)

    linhas = [f["texto"] for f in campos if f.get("categoria") == "linha digitável"]
    if linhas:
        dvs = [_dvs_da_linha(l) for l in linhas]
        cods = [geradores.codigo_da_linha(l) for l in linhas]
        gerais = [(codigos.valida_arrecadacao(c) if c[0] == "8" else codigos.valida_boleto(c)) for c in cods if c]
        ok = all(v is not False for v in dvs) and all(gerais) if gerais else None
        reg("linha_digitavel", ok, "DVs conferem" if ok else "DV da linha digitável não confere")
        total = _total(campos)
        valores = [_valor_codificado(c) for c in cods if c]
        if total and valores:
            bate = any(v == total for v in valores)
            reg("codigo_total", bate, f"código R$ {valores[0] / 100:,.2f} × total R$ {total / 100:,.2f}")
        else:
            reg("codigo_total", None)
    else:
        reg("linha_digitavel", None)
        reg("codigo_total", None)

    cons, total = _consumo(campos), _total(campos)
    if cons and total:
        v, un = cons
        tarifa = total / 100 / max(v, _MINIMO[un][0])  # por unidade cobrada
        reg("consumo_valor", tarifa_ok(total, v, un), f"R$ {tarifa:.2f} por {un.lower()}")
    else:
        reg("consumo_valor", None)

    # o telefone do campo "outro" (ou com rótulo de telefone): o DDD é da UF impressa (DDD que não existe, nunca)
    ddds = [geradores.ddd_do_telefone(str(f["texto"])) for f in campos if f.get("categoria") in ("outro", "telefone")
            or re.search(r"TEL|FONE|CELULAR|WHATS|CONTATO", _norm(f.get("rotulo", "")))]
    ddds = [d for d in ddds if d]
    uf = cidade[1] if cidade else None
    if ddds and (uf in geradores.DDDS_UF or any(d not in geradores._UF_DO_DDD for d in ddds)):
        ruins = [d for d in ddds if d not in geradores.DDDS_UF.get(uf, ())]
        reg("telefone_ddd", not ruins, f"DDD {', '.join(ruins)} não é de {uf or 'nenhuma UF'}" if ruins
            else f"DDD de {uf}")
    else:
        reg("telefone_ddd", None)

    redondo = any(re.search(r"\d,00\b", str(f.get("texto", ""))) for f in campos if f.get("categoria") == "valor")
    return {"regras": regras, "violadas": [n for n, r in regras.items() if r["ok"] is False], "valor_redondo": bool(redondo)}


def campos_finais(campos: list[dict], edicoes: list[dict]) -> list[dict]:
    """Os campos com os textos DEPOIS das edições (``edicoes[].campo`` = id do campo, ``novo``/``texto_novo``);
    campo apagado sai da lista."""
    novos = {}
    for e in edicoes:
        cid = e.get("campo")
        if cid is None:
            continue
        novos[cid] = e.get("novo") if "novo" in e else e.get("texto_novo")
    out = []
    for f in campos:
        if f.get("id") in novos:
            t = novos[f["id"]]
            if not str(t or "").strip():
                continue
            out.append({**f, "texto": str(t)})
        else:
            out.append(f)
    return out


def _ceps(campos: list[dict]) -> set[str]:
    """Os CEPs (8 dígitos) impressos: no campo de CEP ou colados no endereço, no bairro, na cidade."""
    return {re.sub(r"\D", "", m) for f in campos if f.get("categoria") in ("cep", "endereço", "bairro", "cidade/uf")
            for m in re.findall(r"(?<!\d)\d{5}-?\d{3}(?!\d)", str(f.get("texto", "")))}


def _endereco_cep(antes: list[dict], depois: list[dict]) -> dict:
    """Logradouro ou bairro trocado com o CEP antigo (o padrão "nome e endereço" troca a rua e deixa CEP, bairro e
    cidade). Só o número mudou não conta (a rua é a mesma); a rua nova que tem o CEP antigo na base de logradouros
    (outra rua do mesmo CEP) também não; CEP novo não dá para conferir sem a base dos Correios."""
    tem_endereco = any(f.get("categoria") in ("endereço", "bairro") for f in depois)
    ceps_a, ceps_d = _ceps(antes), _ceps(depois)
    if not (tem_endereco and ceps_d):
        return {"ok": None, "detalhe": ""}
    velhos = {f.get("id"): f for f in antes if f.get("id") is not None}
    trocados, ruas = set(), []
    for f in depois:
        v = velhos.get(f.get("id"))
        if v is None or f.get("categoria") not in ("endereço", "bairro"):
            continue
        chave = geradores.logradouro_de if f["categoria"] == "endereço" else geradores._normalizar
        if chave(str(v.get("texto", ""))) != chave(str(f.get("texto", ""))):
            trocados.add("logradouro" if f["categoria"] == "endereço" else "bairro")
            if f["categoria"] == "endereço":
                ruas.append(str(f.get("texto", "")))
    if not trocados:
        return {"ok": True, "detalhe": "logradouro e bairro do original"}
    o_que = " e ".join(sorted(trocados, reverse=True)) + (" trocados" if len(trocados) > 1 else " trocado")
    antigos = ceps_d & ceps_a
    if antigos and all(c.endswith("000") for c in antigos):
        return {"ok": True,
                "detalhe": f"{o_que}; CEP geral do município ({', '.join(sorted(antigos))}) serve para qualquer rua"}
    cidade = next((c for f in depois if f.get("categoria") == "cidade/uf"
                   for c in [geradores.cidade_do_texto(str(f.get("texto", "")))] if c), None)
    if antigos and trocados == {"logradouro"} and all(geradores.rua_com_cep(r, c, cidade) for r in ruas for c in antigos):
        return {"ok": True, "detalhe": f"{o_que}; a rua nova tem o CEP antigo ({', '.join(sorted(antigos))}) na base "
                                       "de logradouros"}
    if antigos:
        return {"ok": False, "detalhe": f"{o_que} com o CEP antigo ({', '.join(sorted(antigos))})"}
    return {"ok": None, "detalhe": f"{o_que} e CEP novo: sem a base dos Correios, não dá para conferir"}


def comparar(antes: list[dict], depois: list[dict]) -> dict:
    """Violações que a edição criou (ok no original, violada na editada) e se a versão ficou sem pista semântica.
    Além das regras de ``avaliar``, a ``endereco_cep`` (logradouro ou bairro trocado com o CEP antigo)."""
    a, d = avaliar(antes), avaliar(depois)
    a["regras"]["endereco_cep"] = _endereco_cep(antes, antes)
    d["regras"]["endereco_cep"] = _endereco_cep(antes, depois)
    if d["regras"]["endereco_cep"]["ok"] is False:
        d["violadas"].append("endereco_cep")
    novas = [n for n in d["violadas"] if a["regras"].get(n, {}).get("ok") is not False]
    aplicaveis = [n for n, r in d["regras"].items() if r["ok"] is not None]
    return {"original": a, "editada": d, "novas_violacoes": novas, "regras_aplicaveis": aplicaveis,
            "sem_pista_semantica": not novas, "valor_redondo_novo": d["valor_redondo"] and not a["valor_redondo"]}
