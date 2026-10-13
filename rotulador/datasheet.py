"""Datasheet do dataset gerado (Gebru et al., "Datasheets for Datasets"), refeito dos JSON de anotação.

    python -m rotulador.datasheet SAIDA      (o gerador também reescreve ao terminar cada lote)

Grava SAIDA/DATASHEET.md (para gente) e SAIDA/datasheet.json (para scripts): alertas do dataset inteiro
(rotulador.qualidade.alertas_dataset), composição por split, técnicas, famílias, classes, canais de envio, marcas
benignas, área editada, conferência do OCR, versões da ferramenta, resultado da última auditoria de vazamento, usos
recomendados, protocolo de avaliação e limitações.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

from . import catalogo, exportar, perito, qualidade
from .registro import SPLITS

AMOSTRA_PISTAS = 120  # edições medidas por split (perito.medir_versao carrega as imagens: a amostra limita o tempo)
MEDIDAS_PISTAS = ("nota", "grao", "espectro", "nitidez", "tom")


def _anotacoes(pasta: Path) -> list[dict]:
    out = []
    for arq in sorted((pasta / "anotacoes").glob("*.json")) if (pasta / "anotacoes").is_dir() else []:
        try:
            out.append(json.loads(arq.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def _pistas(raiz_saida: Path, sp: str, anots: list[dict]) -> dict:
    """Pistas forenses (rotulador.perito) de uma amostra determinística de até AMOSTRA_PISTAS edições do split
    (ordenadas por versão e edição, passo fixo), resumidas (mediana e p90) por técnica e por nível de perícia.
    Versão com arquivo faltando ou anotação ilegível só sai da amostra (``falhas``)."""
    pares = []
    for a in anots:
        try:
            nome = f"{a['doc']}__v{int(a['versao']):02d}"
        except (KeyError, TypeError, ValueError):
            continue
        pares += [(nome, i) for i in range(len(exportar.edicoes_de_anotacao(a)))]
    pares.sort()
    amostra = pares[::max(1, -(-len(pares) // AMOSTRA_PISTAS))]
    medidas: dict[str, list | None] = {}
    grupos: dict[str, dict] = {"por_tecnica": {}, "por_pericia": {}}
    falhas = 0
    for nome, i in amostra:
        if nome not in medidas:
            try:
                medidas[nome] = perito.medir_versao(raiz_saida, sp, nome)
            except Exception:  # noqa: BLE001 - arquivo faltando, ilegível ou anotação antiga: a amostra segue sem ela
                medidas[nome] = None
        eds = medidas[nome]
        if not eds or i >= len(eds):
            falhas += 1
            continue
        e = eds[i]
        for chave, g in (("por_tecnica", e.get("tecnica") or "?"), ("por_pericia", e.get("pericia") or "auto")):
            grupo = grupos[chave].setdefault(g, {"n": 0, **{k: [] for k in MEDIDAS_PISTAS}})
            grupo["n"] += 1
            for k in MEDIDAS_PISTAS:
                v = e["nota"] if k == "nota" else e["medidas"].get(k)
                if v is not None:
                    grupo[k].append(float(v))

    def resumo(grupo: dict) -> dict:
        return {"n": grupo["n"], **{k: {"mediana": round(float(np.median(grupo[k])), 3),
                                        "p90": round(float(np.percentile(grupo[k], 90)), 3)} if grupo[k] else None
                                    for k in MEDIDAS_PISTAS}}
    return {"edicoes": len(pares), "amostra": len(amostra), "falhas": falhas,
            **{chave: {g: resumo(v) for g, v in sorted(grupos[chave].items())} for chave in grupos}}


def _entropia(contagens: dict) -> float | None:
    """Diversidade de um eixo (0..1): entropia de Shannon normalizada pelo nº de valores. 1 = tudo por igual,
    ~0 = um valor domina; None sem dado."""
    n = np.array([v for v in contagens.values() if v], float)
    if n.size < 2:
        return None
    p = n / n.sum()
    return round(float(-(p * np.log(p)).sum() / np.log(n.size)), 3)


def _cobertura(eds: list[dict], categorias: list[str]) -> dict:
    """Técnica × classe: quantas edições há de cada combinação compatível e quais compatíveis ainda estão
    vazias (as lacunas que a distribuição "cobertura" do gerador vai preencher)."""
    matriz: dict[str, dict[str, int]] = {}
    for e in eds:
        if e.get("tecnica") and e.get("categoria"):
            matriz.setdefault(e["tecnica"], {}).setdefault(e["categoria"], 0)
            matriz[e["tecnica"]][e["categoria"]] += 1
    faltam = [(t.id, c) for t in catalogo.TECNICAS.values() if t.automatica and not t.precisa_ia
              for c in categorias if catalogo.compativel(t, c) and not matriz.get(t.id, {}).get(c)]
    return {"matriz": matriz, "faltam": [f"{t} × {c}" for t, c in faltam],
            "compativeis": sum(1 for t in catalogo.TECNICAS.values() if t.automatica and not t.precisa_ia
                               for c in categorias if catalogo.compativel(t, c))}


def _coerencia(anots: list[dict]) -> dict:
    """Coerência semântica (rotulador/coerencia.py): quantas versões ficaram sem pista semântica nova (só o pixel
    acusa) e que violações as edições criaram."""
    com = [a["coerencia"] for a in anots if isinstance(a.get("coerencia"), dict)]
    return {"avaliadas": len(com), "sem_pista_semantica": sum(1 for c in com if c.get("sem_pista_semantica")),
            "com_valor_redondo_novo": sum(1 for c in com if c.get("valor_redondo_novo")),
            "novas_violacoes": dict(Counter(n for c in com for n in c.get("novas_violacoes") or []).most_common())}


def _formato(a: dict) -> str:
    q = exportar.qualidade_saida(a)
    return f"{a.get('formato')}{'' if q is None else f' q{q}'}"


def _conferencia_ocr(anots: list[dict]) -> dict:
    """Edições com texto novo e quantas o OCR conferiu (leu o valor novo; ``edicoes[].conferido_ocr``). Não
    conferida = escrita à mão, OCR que não leu nem o original ou sem OCR. Anotações anteriores à 2.3 guardavam a
    conferência só em ``operacoes[].meta``."""
    n = ok = 0
    for a in anots:
        textos = [e for e in exportar.edicoes_de_anotacao(a) if e.get("texto_novo")]
        conf = sum(1 for e in textos if e.get("conferido_ocr") is True)
        if not any("conferido_ocr" in e for e in textos):
            conf = min(len(textos), sum(1 for op in a.get("operacoes") or []
                                        if (op.get("meta") or {}).get("conferido_ocr") is True))
        n, ok = n + len(textos), ok + conf
    return {"edicoes_texto": n, "conferidas": ok, "nao_conferidas": n - ok}


def coletar(raiz_saida: Path) -> dict:
    raiz_saida = Path(raiz_saida)
    por_split, todas = {}, {}
    try:  # as classes do catálogo da saída (todas as que o rotulador conhece, não só as já editadas)
        categorias = [c for c in json.loads((raiz_saida / "catalogo.json").read_text(encoding="utf-8")).get("categorias", {})
                      if c != "outro"]
    except (OSError, ValueError):
        categorias = []
    for sp in SPLITS:
        anots = todas[sp] = _anotacoes(raiz_saida / sp)
        if not anots:
            continue
        eds = [e for a in anots for e in exportar.edicoes_de_anotacao(a)]  # anteriores à 2.0 também
        frac = np.array([a.get("fracao_alterada", 0) for a in anots]) * 100
        dif = np.array([d for d in (exportar.dificuldade_de(a) for a in anots) if d is not None])
        canais = Counter((a.get("canal") or {}).get("perfil", "nenhum") for a in anots)
        passos = Counter(p["tipo"] for a in anots for p in (a.get("canal") or {}).get("passos", []))
        por_split[sp] = {
            "versoes": len(anots), "documentos": len({a["doc"] for a in anots}), "edicoes": len(eds),
            "originais": len([p for p in (raiz_saida / sp / "originais").glob("*") if not p.name.endswith(".tmp")]),
            "por_tecnica": dict(Counter(e["tecnica"] for e in eds).most_common()),
            "por_familia": dict(Counter(e.get("familia") or "?" for e in eds).most_common()),
            "por_nivel": dict(Counter(e.get("nivel") or "?" for e in eds).most_common()),
            "por_categoria": dict(Counter(e["categoria"] for e in eds).most_common()),
            "canal": dict(canais.most_common()), "passos_canal": dict(passos.most_common()),
            "marcas": dict(Counter(m["tipo"] for a in anots for m in a.get("marcas_benignas") or []).most_common()),
            "com_marcas": sum(1 for a in anots if a.get("marcas_benignas")),
            "falsificadores": dict(Counter((a.get("falsificador") or {}).get("nome") or "sorteio clássico/manual"
                                           for a in anots).most_common()),
            "motivos": dict(Counter((a.get("falsificador") or {}).get("motivo") or "nenhum" for a in anots).most_common()),
            "pericia": dict(Counter(a.get("pericia") or "auto" for a in anots).most_common()),
            "formatos": dict(Counter(_formato(a) for a in anots)),
            "origem": dict(Counter(exportar._origem(a) for a in anots)),
            "area_editada_pct": {"mediana": round(float(np.median(frac)), 3), "p90": round(float(np.percentile(frac, 90)), 3),
                                 "max": round(float(frac.max()), 3)},
            "edicoes_por_versao": round(len(eds) / len(anots), 2),
            # 1 − maior nota do perito entre as edições medidas da versão (0 = pista forte, 1 = invisível para ele)
            "dificuldade": {"com_medida": int(dif.size), "mediana": round(float(np.median(dif)), 3) if dif.size else None,
                            "p90": round(float(np.percentile(dif, 90)), 3) if dif.size else None},
            "versoes_ferramenta": sorted({a.get("versao_ferramenta", "?") for a in anots}),
            "conferencia_ocr": _conferencia_ocr(anots),
            "pistas": _pistas(raiz_saida, sp, anots),
            "fornecedores": dict(Counter(f"{k}={v}" for e in eds for k, v in (e.get("fornecedores") or {}).items()
                                         if isinstance(e.get("fornecedores"), dict)).most_common()),
            "coerencia": _coerencia(anots),
            "laudos": sum(1 for a in anots if a.get("laudo")),
            # o mesmo dado mudou num lugar e ficou com o valor antigo em outro (gerador, modo exato)
            "com_ocorrencias_antigas": sum(1 for a in anots if a.get("ocorrencias_antigas")),
            "cobertura": _cobertura(eds, categorias or sorted({e["categoria"] for e in eds if e.get("categoria")})),
            "qualidade": {"com_alerta": sum(1 for a in anots if qualidade.alertas_de(a)),
                          "alertas": dict(Counter(al.split(":")[0] for a in anots
                                                  for al in qualidade.alertas_de(a)).most_common())},
            "datas": [min(a.get("data", "") for a in anots), max(a.get("data", "") for a in anots)],
        }
        s = por_split[sp]  # diversidade de cada eixo (entropia normalizada): onde o dataset ainda é monótono
        s["diversidade"] = {k: _entropia(s[k]) for k in ("por_tecnica", "por_familia", "por_categoria", "canal",
                                                         "passos_canal", "marcas", "falsificadores", "motivos",
                                                         "pericia", "fornecedores", "formatos")}
    auditoria = None
    rel = raiz_saida / "relatorio_vazamento.json"
    if rel.is_file():
        try:
            r = json.loads(rel.read_text(encoding="utf-8"))
            auditoria = {"erros": len(r.get("erros", [])), "avisos": len(r.get("avisos", [])),
                         "data": time.strftime("%Y-%m-%d %H:%M", time.localtime(rel.stat().st_mtime))}
        except ValueError:
            pass
    return {"gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"), "splits": por_split, "auditoria": auditoria,
            "alertas": qualidade.alertas_dataset(raiz_saida, todas),
            "tecnicas_catalogo": {t.id: {"num": t.num, "nome": t.nome, "familia": t.familia, "nivel": t.nivel}
                                  for t in catalogo.TECNICAS.values()}}


def _tabela(titulo: str, chave: str, dados: dict, nomes=None) -> list[str]:
    splits = [sp for sp in SPLITS if sp in dados["splits"]]
    itens = sorted({k for sp in splits for k in dados["splits"][sp][chave]})
    if not itens:
        return []
    linhas = [f"| {titulo} | " + " | ".join(splits) + " |", "|---|" + "---:|" * len(splits)]
    for k in itens:
        nome = (nomes or {}).get(k, k)
        linhas.append(f"| {nome} | " + " | ".join(str(dados["splits"][sp][chave].get(k, 0)) for sp in splits) + " |")
    return linhas + [""]


def _tabela_pistas(titulo: str, chave: str, dados: dict, nomes=None) -> list[str]:
    """Uma linha por (split, grupo): n e mediana / p90 de cada medida de MEDIDAS_PISTAS."""
    def celula(g, k):
        return f"{g[k]['mediana']:.2f} / {g[k]['p90']:.2f}" if g.get(k) else "–"
    linhas = []
    for sp in [s for s in SPLITS if s in dados["splits"]]:
        for k, g in ((dados["splits"][sp].get("pistas") or {}).get(chave) or {}).items():
            linhas.append(f"| {sp} | {(nomes or {}).get(k, k)} | {g['n']} | " +
                          " | ".join(celula(g, m) for m in MEDIDAS_PISTAS) + " |")
    if not linhas:
        return ["- nenhuma edição medida.", ""]
    return [f"| split | {titulo} | n | " + " | ".join(m.replace("grao", "grão") for m in MEDIDAS_PISTAS) + " |",
            "|---|---|---:|" + "---:|" * len(MEDIDAS_PISTAS)] + linhas + [""]


def markdown(d: dict) -> str:
    S = d["splits"]
    sp = [s for s in SPLITS if s in S]
    nomes_tec = {k: f"{v['nome']} (`{k}`)" for k, v in d["tecnicas_catalogo"].items()}
    L = [f"# Datasheet — dataset de adulteração de comprovantes de endereço",
         "", f"Gerado automaticamente em {d['gerado_em']} a partir das anotações (a fonte da verdade).", "",
         "## Alertas do dataset", ""]
    L += [f"- {a}" for a in (d.get("alertas") or {}).get("alertas") or []] or ["- nenhum."]
    L += ["", "## Motivação",
          "Treinar e avaliar detectores e localizadores de adulteração em comprovantes de endereço brasileiros "
          "(contas de consumo). Cada versão é um par: a original e a adulterada, com máscaras por pixel e o rótulo "
          "de cada edição (técnica, família, nível, classe do campo, texto antes e depois).", "",
          "## Composição", "",
          "| | " + " | ".join(sp) + " |", "|---|" + "---:|" * len(sp)]
    for chave, rot in (("versoes", "versões (pares)"), ("documentos", "documentos de origem"),
                       ("originais", "arquivos de original"), ("edicoes", "edições"),
                       ("edicoes_por_versao", "edições por versão")):
        L.append(f"| {rot} | " + " | ".join(str(S[s][chave]) for s in sp) + " |")
    L.append("| área alterada (pixels que mudaram; mediana / p90 / máx, % da página) | " + " | ".join(
        f"{S[s]['area_editada_pct']['mediana']} / {S[s]['area_editada_pct']['p90']} / {S[s]['area_editada_pct']['max']}"
        for s in sp) + " |")
    L.append("| dificuldade (1 − maior nota do perito entre as edições medidas; mediana / p90; versões com medida) | "
             + " | ".join(f"{S[s]['dificuldade']['mediana']} / {S[s]['dificuldade']['p90']} ({S[s]['dificuldade']['com_medida']})"
                          if (S[s].get("dificuldade") or {}).get("com_medida") else "–" for s in sp) + " |")
    L.append("| edições com texto novo não conferidas pelo OCR (de todas com texto novo) | " + " | ".join(
        f"{S[s]['conferencia_ocr']['nao_conferidas']} de {S[s]['conferencia_ocr']['edicoes_texto']}"
        if S[s].get("conferencia_ocr") else "–" for s in sp) + " |")
    L.append("| versões com o valor antigo em outro lugar da página (`ocorrencias_antigas`; "
             "`listar(sem_ocorrencias_antigas=True)` tira) | " + " | ".join(
                 str(S[s].get("com_ocorrencias_antigas", 0)) for s in sp) + " |")
    L.append("")
    L += ["### Técnicas", ""] + _tabela("técnica", "por_tecnica", d, nomes_tec)
    L += ["### Famílias e níveis", ""] + _tabela("família", "por_familia", d) + _tabela("nível", "por_nivel", d)
    L += ["### Classes (campo editado)", ""] + _tabela("classe", "por_categoria", d)
    L += ["### Canal de envio e marcas benignas", "",
          "O canal (impressão, scanner, celular, tela, WhatsApp...) passa IGUAL na original e na editada de cada par; "
          "as marcas benignas (carimbo, autenticação, anotação, dobra...) também, fora da edição.", ""]
    L += _tabela("perfil do canal", "canal", d) + _tabela("passo do canal", "passos_canal", d) + _tabela("marca", "marcas", d)
    L += ["### Falsificadores e motivos", "",
          "Versões feitas \"como uma pessoa faria\" (rotulador/falsificador.py): uma ferramenta com hábitos fixos e um motivo "
          "que escolhe campos coerentes e valores combinados. As proporções entre falsificadores são estimativas da receita, "
          "não medidas de campo.", ""]
    L += _tabela("falsificador", "falsificadores", d) + _tabela("motivo", "motivos", d)
    L += ["", "**Perícia** (acabamento forense casado na região: iluminação, grão espectral, nitidez). "
          "Treine/avalie por nível para medir a detecção na ponta profissional.", ""]
    L += _tabela("nível de perícia", "pericia", d)
    L += ["### Pistas forenses por técnica e nível", "",
          "Medidas de `rotulador/perito.py` numa amostra de até 120 edições por split: o interior da região editada "
          "(na editada) contra um anel de papel em volta (na original). Grão = razão do desvio do grão do papel (1 = "
          "igual, ~0 = chapado); espectro = diferença da autocorrelação lag-1 do grão (0 = igual); nitidez = razão da "
          "energia de borda da tinta (1 = igual); tom = diferença de cor do papel em níveis (0 = igual). Nota 0..1: "
          "~0 = invisível para essas medidas, > 0,5 = pista forte. Células: mediana / p90; – = sem medida válida.", ""]
    L += _tabela_pistas("técnica", "por_tecnica", d, nomes_tec) + _tabela_pistas("nível de perícia", "por_pericia", d)
    L += ["**Fornecedores** (quem fez cada parte da edição, `edicoes[].fornecedores`; ex.: apagar=patchmatch, "
          "texto=freetype, fonte=Arial):", ""]
    L += _tabela("fornecedor", "fornecedores", d) or ["- não registrado.", ""]
    L += ["### Coerência semântica e laudos", "",
          "Regras de verificador (`rotulador/coerencia.py`: CPF/CNPJ, CEP na faixa da cidade, bairro da cidade, datas "
          "em ordem, mês de referência, vencimento em dia útil, DVs da linha digitável, código de barras com o total, "
          "tarifa plausível, DDD do telefone na UF; e, entre original e editada, logradouro trocado com o CEP antigo) "
          "rodadas antes e depois "
          "da edição. Uma versão \"sem pista semântica\" só pode ser pega pelo pixel: é a ponta que um modelo que lê o "
          "texto não resolve. Cada versão também traz um `laudo` em texto (o relatório pericial, para detectores que "
          "explicam).", "",
          "| | " + " | ".join(sp) + " |", "|---|" + "---:|" * len(sp)]
    for chave, rot in (("avaliadas", "versões avaliadas"), ("sem_pista_semantica", "sem pista semântica nova"),
                       ("com_valor_redondo_novo", "valor redondo novo")):
        L.append(f"| {rot} | " + " | ".join(str((S[s].get("coerencia") or {}).get(chave, 0)) for s in sp) + " |")
    L.append("| laudos | " + " | ".join(str(S[s].get("laudos", 0)) for s in sp) + " |")
    L.append("")
    viol = {s: (S[s].get("coerencia") or {}).get("novas_violacoes") or {} for s in sp}
    if any(viol.values()):
        d2 = {"splits": {s: {"viol": viol[s]} for s in sp}}
        L += _tabela("violação criada pela edição", "viol", d2)
    L += ["### Cobertura e diversidade", "",
          "Combinações técnica × classe compatíveis (catálogo) que ainda não têm nenhuma edição: são as lacunas que a "
          "distribuição `cobertura` do gerador preenche primeiro. Diversidade = entropia normalizada de cada eixo "
          "(1 = tudo por igual, perto de 0 = um valor domina).", "",
          "| | " + " | ".join(sp) + " |", "|---|" + "---:|" * len(sp)]
    L.append("| combinações compatíveis com edição / total | " + " | ".join(
        f"{S[s]['cobertura']['compativeis'] - len(S[s]['cobertura']['faltam'])} / {S[s]['cobertura']['compativeis']}"
        for s in sp) + " |")
    for eixo, rot in (("por_tecnica", "técnicas"), ("por_categoria", "classes"), ("canal", "canais"),
                      ("passos_canal", "passos do canal"), ("falsificadores", "falsificadores"), ("motivos", "motivos"),
                      ("pericia", "perícia"), ("marcas", "marcas benignas"), ("fornecedores", "fornecedores")):
        L.append(f"| diversidade: {rot} | " + " | ".join(
            "–" if S[s]["diversidade"].get(eixo) is None else f"{S[s]['diversidade'][eixo]:.2f}" for s in sp) + " |")
    L.append("| versões com alerta de qualidade (`auditar_qualidade.py`) | " + " | ".join(
        str((S[s].get("qualidade") or {}).get("com_alerta", 0)) for s in sp) + " |")
    L.append("")
    for s in sp:
        faltam = S[s]["cobertura"]["faltam"]
        if faltam:
            L.append(f"- Lacunas em {s} ({len(faltam)}): " + ", ".join(faltam[:40]) + (" ..." if len(faltam) > 40 else ""))
    L.append("")
    L += ["### Formatos e origem", ""] + _tabela("formato", "formatos", d) + _tabela("origem", "origem", d)
    L += ["## Como foi gerado",
          "- Edições pelas técnicas do catálogo (`docs/tecnicas.md`), cada uma conferida por OCR (o campo editado "
          "precisa ser lido como o valor novo; se não, a edição é descartada).",
          "- Disfarce depois da edição nas frequências de falsificadores reais (FSTS) e retoque de textura sem "
          "mudar o texto (Texture Jitter, OSTF).",
          "- Valores falsos com vocabulário repartido por split; doadores de colagem só do mesmo split.",
          f"- Versões da ferramenta: {', '.join(sorted({v for s in sp for v in S[s]['versoes_ferramenta']}))}.", "",
          "## Rótulos",
          "- `mascaras/`: pixels alterados; `mascaras_regiao/`: área editada; `mascaras_classes/`: classe por pixel; "
          "`mascaras_tecnicas/`: técnica por pixel (legenda em `catalogo.json`); `mascaras_ignorar/`: borda e "
          "folga de rótulo ambíguo (tirar da perda e da métrica).",
          "- Com canal de envio, as máscaras passam pela mesma geometria das imagens; o que o codec espalhou (canal ou "
          "gravação com perda) fica na faixa de ignorar.",
          "- Treino: não use o caminho, a pasta ou o nome do arquivo como feature (`originais/` × `editadas/`, o "
          "sufixo `__vNN`): entregam a classe. O detector deve ver só os pixels.", "",
          "## Splits e vazamento"]
    a = d.get("auditoria")
    L.append(f"- Última auditoria (`auditar_vazamento.py`, {a['data']}): {a['erros']} erro(s), {a['avisos']} aviso(s)."
             if a else "- Auditoria ainda não rodada: rode `auditar_vazamento.py` antes de treinar.")
    L += ["- O split vem da pasta de origem; o mesmo conteúdo, o mesmo cliente (CPF, CPF mascarado, nome, nº de "
          "instalação, linha digitável), o mesmo modelo de conta ou o mesmo documento em dois splits é erro.", "",
          "## Usos",
          "- Recomendado: treino e avaliação de detecção/localização de adulteração em documentos.",
          "- Não recomendado: medir desempenho em fraude real só com estes dados (são adulterações sintéticas); "
          "identificar pessoas; qualquer uso dos dados pessoais dos documentos de origem.", "",
          "## Protocolo de avaliação sugerido",
          "- Reporte AUC por imagem e TPR com FPR de 1% e 5%; por pixel, F1/IoU com UM limiar escolhido na validação.",
          "- Intervalos de confiança por bootstrap sobre DOCUMENTOS (não pixels); 3+ sementes.",
          "- Testes fora da distribuição: deixar uma técnica/família de fora; um emissor de fora; um canal de fora; "
          "e um conjunto pequeno de adulterações feitas à mão (editor) — o número que mais importa.",
          "- Tudo isso sai de `python -m rotulador.treino.avaliar SAIDA SPLIT PRED --calibrar 10`: AUC/F1/IoU por pixel "
          "com limiar fixo, oráculo e calibrado em N versões; AUC e TPR por imagem; bootstrap por documento; recortes por "
          "técnica, canal, perícia e falsificador. Com taxa-base de pixel adulterado abaixo de 1%, calibre o limiar "
          "(DocForge-Bench). `rotulador.treino.robustez` gera as cópias perturbadas (JPEG, WhatsApp, tela, impressão...) "
          "e `rotulador.treino.base` dá o piso de um detector sem treino.", "",
          "## Limitações",
          "- Adulterações sintéticas; generalização para fraude real precisa ser medida em dados reais.",
          "- IA generativa de uma família só (Qwen); regeneração da página inteira não é gerada.",
          "- O canal de envio é simulado (não houve impressão/foto física).",
          "- Documentos de origem contêm dados pessoais: trate conforme a LGPD.", ""]
    return "\n".join(L)


def escrever(raiz_saida: Path) -> dict:
    d = coletar(raiz_saida)
    raiz_saida = Path(raiz_saida)
    (raiz_saida / "datasheet.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    (raiz_saida / "DATASHEET.md").write_text(markdown(d), encoding="utf-8")
    return d


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    saida = Path(sys.argv[1] if len(sys.argv) > 1 else "saida")
    d = escrever(saida)
    print(f"datasheet: {saida / 'DATASHEET.md'} ({sum(s['versoes'] for s in d['splits'].values())} versões)")
