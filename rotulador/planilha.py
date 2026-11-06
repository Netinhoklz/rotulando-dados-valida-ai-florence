"""O dataset numa planilha do Excel: ``saida/dataset.xlsx``, refeita a partir dos índices de cada split (manifesto.csv
e catalogo.csv, que vêm dos JSON de anotação) a cada versão salva no editor e no fim de cada lote do gerador.

Abas:
  - **Resumo**: por split (treino, teste, validação), os documentos de origem, as versões, as edições e a fração editada;
    as pastas de cada split e o que vale cada máscara (0 = pixel não editado, 1 = pixel editado);
  - **Versões**: uma linha por imagem editada, com o caminho (e o link) da original, da editada, das máscaras e da
    anotação, o que foi editado, as técnicas, os pixels editados, a origem, quem fez e quando;
  - **Edições**: uma linha por edição: o campo, o texto antigo e o novo, a técnica, as ferramentas, a caixa, o
    falsificador (quando o gerador sorteou um) e, nas versões do método FSTS, o tipo do artigo;
  - **Documentos**: os documentos de entrada de cada split, com quantas versões cada um já tem;
  - **Legenda**: o valor de cada pixel nas máscaras binárias, na de classes e na de técnicas.

Os caminhos são relativos à pasta da planilha (a pasta de saída), então os links abrem os arquivos direto do Excel.
No Windows o Excel aberto trava o arquivo: aí a planilha nova vai para ``dataset (nova).xlsx`` e ``escrever`` avisa."""

from __future__ import annotations

import csv
import json
import os
import threading
import time
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import catalogo, exportar, fsts
from .registro import SPLITS

NOME = "dataset.xlsx"
NOME_TRAVADO = "dataset (nova).xlsx"
NOMES_SPLIT = {"treino": "Treino", "teste": "Teste", "validacao": "Validação"}
PASTAS = [("originais/", "o documento original (sem edição), o par de cada versão"),
          ("editadas/", "as imagens editadas (fraude), uma por versão: <doc>__v01.png, <doc>__v02.png..."),
          ("mascaras/", "máscara da edição, mesmo nome da editada: 255 (branco) = pixel editado, 0 = não editado; as binárias salvas até 06/11/2026 usam 1 no lugar de 255: leia pixel > 0"),
          ("mascaras_regiao/", "área editada (a pegada das ferramentas): 255 = dentro, 0 = fora"),
          ("mascaras_classes/", "o QUÊ foi editado: o número da classe do campo em cada pixel (aba Legenda); 0 = não editado"),
          ("mascaras_tecnicas/", "o COMO: o número da técnica em cada pixel (aba Legenda); 0 = não editado"),
          ("mascaras_ignorar/", "255 = rótulo ambíguo (1 px em volta da edição, o que o codec espalhou): fora da perda e da métrica"),
          ("anotacoes/", "o JSON de cada versão: campos, textos antigo e novo, técnica, ferramentas, caixas, laudo"),
          ("manifesto.csv", "uma linha por versão (a aba Versões)"),
          ("catalogo.csv", "uma linha por edição (a aba Edições)")]
_CABECALHO = Font(bold=True, color="FFFFFF")
_FUNDO = PatternFill("solid", fgColor="2F3B4C")
_FUNDO_SPLIT = {"treino": PatternFill("solid", fgColor="E3F0FF"), "teste": PatternFill("solid", fgColor="FFF1DC"),
                "validacao": PatternFill("solid", fgColor="E6F6E8")}
_LINK = Font(color="1F5FBF", underline="single")
_lock = threading.Lock()
_pendente = threading.Event()
_fila: dict = {}


def _ler_csv(caminho: Path) -> list[dict]:
    if not caminho.is_file():
        return []
    with open(caminho, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _indices(raiz_saida: Path, split: str) -> tuple[list[dict], list[dict]]:
    """(manifesto, catálogo) do split; refaz os dois dos JSON se faltarem."""
    r = raiz_saida / split
    if (r / "anotacoes").is_dir() and any((r / "anotacoes").glob("*.json")) and not (
            (r / "manifesto.csv").is_file() and (r / "catalogo.csv").is_file()):
        exportar.reconstruir_indices(raiz_saida, split)
    return _ler_csv(r / "manifesto.csv"), _ler_csv(r / "catalogo.csv")


def _int(v, padrao=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return padrao


def _num(v, padrao=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


def _texto(v):
    """Texto que o Excel aceita: sem caractere de controle (o OCR às vezes devolve) e sem virar fórmula ("=...")."""
    if not isinstance(v, str):
        return v
    v = ILLEGAL_CHARACTERS_RE.sub("", v)
    return " " + v if v.startswith("=") else v


def _aba(wb, titulo: str, colunas: list[tuple[str, int]], linhas: list[list], splits: list[str] | None = None,
         links: tuple[int, ...] = ()):
    """Uma aba com cabeçalho escuro, filtro, painel congelado e largura das colunas; ``splits`` pinta a linha pela cor
    do split; ``links`` (índices de coluna) viram hiperlinks relativos à pasta da planilha."""
    ws = wb.create_sheet(titulo)
    ws.append([c for c, _ in colunas])
    for j, (_, larg) in enumerate(colunas, 1):
        cel = ws.cell(row=1, column=j)
        cel.font, cel.fill = _CABECALHO, _FUNDO
        cel.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(j)].width = larg
    for i, linha in enumerate(linhas, 2):
        ws.append([_texto(v) for v in linha])
        if splits:
            for j in range(1, len(colunas) + 1):
                ws.cell(row=i, column=j).fill = _FUNDO_SPLIT.get(splits[i - 2], PatternFill())
        for j in links:
            cel = ws.cell(row=i, column=j + 1)
            if cel.value:
                cel.hyperlink = str(cel.value)
                cel.font = _LINK
    ws.freeze_panes = "B2" if len(colunas) > 1 else "A2"
    if linhas:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{len(linhas) + 1}"
    ws.row_dimensions[1].height = 30
    return ws


def _legenda_categorias(raiz_saida: Path, ids_categoria: dict | None) -> dict:
    """As classes da legenda da saída (catalogo.json: as de campo e as 3 do editor) mais as pedidas."""
    try:
        gravadas = json.loads((raiz_saida / "catalogo.json").read_text(encoding="utf-8")).get("categorias") or {}
    except (OSError, ValueError):
        gravadas = {}
    return {**gravadas, **(ids_categoria or {})}


def montar(raiz_saida: Path, registro=None, ids_categoria: dict | None = None) -> Workbook:
    raiz_saida = Path(raiz_saida)
    wb = Workbook()
    wb.remove(wb.active)
    manifestos, catalogos = {}, {}
    for sp in SPLITS:
        manifestos[sp], catalogos[sp] = _indices(raiz_saida, sp)

    # ------------------------------------------------------------ Resumo
    ws = wb.create_sheet("Resumo")
    ws["A1"] = "Dataset de fraude em comprovantes de endereço"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Atualizado em {time.strftime('%d/%m/%Y %H:%M')} · pasta: {raiz_saida.resolve()}"
    ws["A3"] = ("Cada split fica na sua pasta (treino/, teste/, validacao/). Nada que nasce de um documento de um split "
                "vai para outro. Máscaras: 1 = pixel editado, 0 = não editado.")
    cab = ["Split", "Pasta", "Documentos de origem", "Documentos com versão", "Versões editadas", "Edições",
           "% média da imagem editada"]
    ws.append([])
    ws.append(cab)
    lin_cab = ws.max_row
    tot = [0, 0, 0, 0]
    for sp in SPLITS:
        man = manifestos[sp]
        docs_entrada = len(registro.por_split[sp]) if registro is not None else len({l.get("doc") for l in man})
        com_versao = len({l.get("doc") for l in man})
        fr = [_num(l.get("fracao_alterada"), 0.0) for l in man]
        ws.append([NOMES_SPLIT[sp], f"{sp}/", docs_entrada, com_versao, len(man), len(catalogos[sp]),
                   round(100 * sum(fr) / len(fr), 3) if fr else 0])
        for j in range(1, len(cab) + 1):
            ws.cell(row=ws.max_row, column=j).fill = _FUNDO_SPLIT[sp]
        tot = [tot[0] + docs_entrada, tot[1] + com_versao, tot[2] + len(man), tot[3] + len(catalogos[sp])]
    ws.append(["Total", "", *tot, ""])
    for j in range(1, len(cab) + 1):
        ws.cell(row=lin_cab, column=j).font, ws.cell(row=lin_cab, column=j).fill = _CABECALHO, _FUNDO
        ws.cell(row=ws.max_row, column=j).font = Font(bold=True)
    ws.append([])
    ws.append(["Dentro de cada split", "O que tem"])
    for j in (1, 2):
        ws.cell(row=ws.max_row, column=j).font, ws.cell(row=ws.max_row, column=j).fill = _CABECALHO, _FUNDO
    for p, desc in PASTAS:
        ws.append([p, desc])
    for col, larg in zip("ABCDEFG", (24, 90, 20, 22, 18, 12, 24)):
        ws.column_dimensions[col].width = larg

    # ------------------------------------------------------------ Versões
    cols = [("Split", 11), ("Versão", 26), ("Documento de origem", 34), ("Página", 8), ("Original", 34),
            ("Editada", 34), ("Máscara (1 = editado)", 34), ("Máscara da região", 34), ("Máscara de classes", 34),
            ("Máscara de técnicas", 34), ("Máscara ignorar", 34), ("Anotação (JSON)", 34), ("O que foi editado", 28),
            ("Técnicas", 40), ("Nº de edições", 10), ("Pixels editados", 12), ("% da imagem editada", 12),
            ("Largura", 9), ("Altura", 9), ("Origem", 12), ("Feita por", 18), ("Data", 19), ("Revisão", 11),
            ("Dificuldade", 11)]
    linhas, sps = [], []
    for sp in SPLITS:
        for l in manifestos[sp]:
            caminho = (lambda k: f"{sp}/{l[k]}" if l.get(k) else "")
            pg = _int(l.get("pagina"))
            linhas.append([NOMES_SPLIT[sp], f"{l.get('doc')}__v{_int(l.get('versao'), 0):02d}", l.get("fonte", ""),
                           pg + 1 if pg is not None else "", caminho("original"), caminho("editada"),
                           caminho("mascara"), caminho("mascara_regiao"), caminho("mascara_classes"),
                           caminho("mascara_tecnicas"), caminho("mascara_ignorar"), caminho("anotacao"),
                           (l.get("categorias") or "").replace(";", "; "), (l.get("tecnicas") or "").replace(";", "; "),
                           _int(l.get("n_edicoes")), _int(l.get("pixels_alterados")),
                           round(100 * _num(l.get("fracao_alterada"), 0.0), 4), _int(l.get("largura")),
                           _int(l.get("altura")), l.get("origem", ""), l.get("operador", ""), l.get("data", ""),
                           l.get("revisao", ""), _num(l.get("dificuldade"))])
            sps.append(sp)
    _aba(wb, "Versões", cols, linhas, sps, links=tuple(range(4, 12)))

    # ------------------------------------------------------------ Edições
    cols = [("Split", 11), ("Versão", 26), ("Nº", 5), ("Campo editado", 18), ("Texto antigo", 36), ("Texto novo", 36),
            ("Técnica", 40), ("Código da técnica", 24), ("Família", 16), ("Nível", 13), ("Granularidade", 13),
            ("Pixels", 10), ("Caixa (x0 y0 x1 y1)", 22), ("Ferramentas", 50), ("Descrição", 60), ("Origem", 12),
            ("Editada", 34), ("Tipo FSTS", 16), ("Falsificador", 16)]
    linhas, sps = [], []
    for sp in SPLITS:
        for e in catalogos[sp]:
            forn = dict(x.split("=", 1) for x in (e.get("fornecedores") or "").split(";") if "=" in x)
            linhas.append([NOMES_SPLIT[sp], e.get("nome", ""), _int(e.get("ordem")), e.get("categoria", ""),
                           e.get("texto_antigo", ""), e.get("texto_novo", ""), e.get("nome_tecnica", ""),
                           e.get("tecnica", ""), e.get("familia", ""), e.get("nivel", ""), e.get("granularidade", ""),
                           _int(e.get("pixels")), e.get("bbox", ""), (e.get("fornecedores") or "").replace(";", "; "),
                           e.get("descricao", ""), e.get("origem", ""),
                           f"{sp}/{e['editada']}" if e.get("editada") else "",
                           fsts.TIPOS.get(forn.get("fsts"), {}).get("rotulo", ""), e.get("falsificador", "")])
            sps.append(sp)
    _aba(wb, "Edições", cols, linhas, sps, links=(16,))

    # ------------------------------------------------------------ Documentos
    cols = [("Split", 11), ("Documento (id)", 20), ("Arquivo de entrada", 50), ("Página", 8), ("Largura", 9),
            ("Altura", 9), ("Versões salvas", 12)]
    linhas, sps = [], []
    for sp in SPLITS:
        n_versoes: dict[str, int] = {}
        for l in manifestos[sp]:
            n_versoes[l.get("doc")] = n_versoes.get(l.get("doc"), 0) + 1
        if registro is not None:
            for it in registro.por_split[sp]:
                linhas.append([NOMES_SPLIT[sp], it.doc, it.rel, it.pagina + 1, it.largura, it.altura,
                               n_versoes.get(it.doc, 0)])
                sps.append(sp)
        else:
            vistos = {}
            for l in manifestos[sp]:
                vistos.setdefault(l.get("doc"), l)
            for doc, l in vistos.items():
                pg = _int(l.get("pagina"))
                linhas.append([NOMES_SPLIT[sp], doc, l.get("fonte", ""), pg + 1 if pg is not None else "",
                               _int(l.get("largura")), _int(l.get("altura")), n_versoes.get(doc, 0)])
                sps.append(sp)
    _aba(wb, "Documentos", cols, linhas, sps)

    # ------------------------------------------------------------ Legenda
    linhas = [["mascaras/", 0, "pixel não editado"], ["mascaras/", 255, "pixel editado (o valor mudou)"],
              ["mascaras_regiao/", 0, "fora da área editada"], ["mascaras_regiao/", 255, "dentro da área editada"],
              ["mascaras_ignorar/", 255, "rótulo ambíguo: fora da perda e da métrica"],
              ["mascaras_classes/", 0, "não editado"]]
    for nome, i in sorted(_legenda_categorias(raiz_saida, ids_categoria).items(), key=lambda x: int(x[1])):
        linhas.append(["mascaras_classes/", int(i), f"classe: {nome}"])
    linhas.append(["mascaras_tecnicas/", 0, "não editado"])
    for t in sorted(catalogo.TECNICAS.values(), key=lambda t: t.num):
        linhas.append(["mascaras_tecnicas/", t.num, f"{t.nome} ({t.id})"])
    linhas.append(["mascaras_tecnicas/", catalogo.DESCONHECIDA, "edição sem técnica conhecida"])
    _aba(wb, "Legenda", [("Máscara", 22), ("Valor do pixel", 14), ("Significa", 80)], linhas)
    return wb


def escrever(raiz_saida: Path, registro=None, ids_categoria: dict | None = None) -> dict:
    """Refaz ``saida/dataset.xlsx``. Devolve ``{"arquivo", "versoes", "aviso"}`` (aviso: o Excel estava com a planilha
    aberta e a nova foi para ``dataset (nova).xlsx``)."""
    raiz_saida = Path(raiz_saida)
    with _lock:
        wb = montar(raiz_saida, registro, ids_categoria)
        n = max(0, wb["Versões"].max_row - 1)
        destino = raiz_saida / NOME
        tmp = raiz_saida / f".{NOME}.tmp"
        raiz_saida.mkdir(parents=True, exist_ok=True)  # saída nova, ainda sem versão: a planilha sai com os documentos
        wb.save(tmp)
        try:
            os.replace(tmp, destino)
            return {"arquivo": str(destino), "versoes": n, "aviso": None}
        except PermissionError:
            alt = raiz_saida / NOME_TRAVADO
            try:
                os.replace(tmp, alt)
            except PermissionError:
                tmp.unlink(missing_ok=True)
                return {"arquivo": str(destino), "versoes": n,
                        "aviso": f"feche o {NOME} e o {NOME_TRAVADO} no Excel para a planilha ser atualizada"}
            return {"arquivo": str(alt), "versoes": n,
                    "aviso": f"o {NOME} está aberto no Excel: a planilha atualizada foi para '{NOME_TRAVADO}'"}


def agendar(raiz_saida: Path, registro=None, ids_categoria: dict | None = None) -> None:
    """Refaz a planilha em segundo plano (várias versões salvas em seguida viram uma escrita só)."""
    _fila.update(args=(Path(raiz_saida), registro, ids_categoria))
    if _pendente.is_set():
        return
    _pendente.set()

    def rodar():
        try:
            time.sleep(0.5)
            _pendente.clear()
            raiz = _fila["args"][0]
            if raiz.is_dir():  # a saída sumiu (pasta temporária dos testes): nada a fazer
                escrever(*_fila["args"])
        except Exception as e:  # noqa: BLE001 - a planilha é um índice: os JSON continuam valendo
            print(f"aviso: a planilha do dataset não foi atualizada ({type(e).__name__}: {e})")
        finally:
            _pendente.clear()
    threading.Thread(target=rodar, daemon=True).start()
