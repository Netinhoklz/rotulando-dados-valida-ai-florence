"""Diagnóstico do rotulador NESTE computador: o que o FSTS "como uma pessoa" precisa e o que cada documento tem.

    python ferramentas/diagnostico.py [--dados PASTA] [--saida PASTA] [--max N] [--linhas N]

Mostra o Python e os pacotes, o OCR (o motor que lê e os modelos), as fontes das 10 mais usadas que estão
instaladas, as bases de nomes e lugares (config/vocabulario) e, para cada documento da pasta de imagens (a escolhida
na tela, como o app faz), quantas linhas o OCR leu, os campos que o detector achou e se o FSTS acha o nome e o
endereço do cliente; quando não acha, as primeiras linhas lidas, com o campo que o detector deu a cada uma.
O relatório vai também para diagnostico.txt, na raiz do projeto. Ele tem os textos lidos dos documentos (dados
pessoais): confira antes de mandar para alguém."""

import argparse
import importlib.metadata
import json
import locale
import platform
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

relatorio: list[str] = []


def p(texto: str = "") -> None:
    print(texto, flush=True)
    relatorio.append(texto)


def versao(pacote: str) -> str:
    try:
        return importlib.metadata.version(pacote)
    except importlib.metadata.PackageNotFoundError:
        return "não instalado"


def pasta_de_dados(a) -> tuple[Path, Path]:
    """A pasta de imagens e a de saída que o app usaria (a escolhida na tela, ou dados/ e saida/ do projeto)."""
    if a.dados or a.saida:
        return Path(a.dados or RAIZ / "dados"), Path(a.saida or RAIZ / "saida")
    try:
        m = json.loads((RAIZ / "config" / "pasta_escolhida.json").read_text(encoding="utf-8"))
        if m.get("dados") and Path(m["dados"]).is_dir() and m.get("saida"):
            return Path(m["dados"]), Path(m["saida"])
    except (OSError, ValueError):
        pass
    return RAIZ / "dados", RAIZ / "saida"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dados")
    ap.add_argument("--saida")
    ap.add_argument("--max", type=int, default=0, help="no máximo N documentos (0 = todos)")
    ap.add_argument("--linhas", type=int, default=25, help="linhas lidas mostradas quando o nome/endereço não aparece")
    a = ap.parse_args()

    p("== Ambiente")
    p(f"Python {platform.python_version()} · {platform.platform()} · projeto em {RAIZ}")
    p(f"codificação: arquivos {sys.getfilesystemencoding()}, padrão {locale.getpreferredencoding(False)}")
    for pac in ("paddleocr", "paddlex", "paddlepaddle", "opencv-contrib-python", "opencv-python",
                "opencv-python-headless", "numpy", "pymupdf", "pillow", "rapidocr_onnxruntime"):
        p(f"  {pac:<24} {versao(pac)}")

    p("\n== OCR")
    from rotulador import ocr
    p(f"paddleocr instalado: {ocr.instalado()}")
    for m in (ocr._DET, ocr._REC):
        pasta = ocr.MODELOS / m["repo"]
        faltam = [arq for arq in m["sha256"] if not (pasta / arq).is_file()]
        p(f"  modelo {m['repo']}: " + ("presente" if not faltam else f"FALTAM {', '.join(faltam)} em {pasta}"))
    try:
        p(f"motor que lê: {ocr.motor()}")
        ocr_ok = True
    except ocr.OCRIndisponivel as e:
        p(f"OCR INDISPONÍVEL: {e}")
        ocr_ok = False

    p("\n== Fontes (as 10 mais usadas em documentos)")
    from rotulador import identificar_fonte
    from rotulador.ops import texto
    inst = identificar_fonte.fontes_top10()
    p(f"{len(texto.listar_fontes())} fontes instaladas; das 10: {len(inst)} arquivos (regular e negrito)")
    falta = [f for f in identificar_fonte.TOP10 if f not in inst]
    if falta:
        p(f"  FALTAM: {', '.join(falta)} (o texto nessas fontes não é identificado: o FSTS usa as letras do documento)")

    p("\n== Bases de nomes e lugares (config/vocabulario)")
    voc = RAIZ / "config" / "vocabulario"
    for nome in ("prenomes.tsv.gz", "sobrenomes.tsv.gz", "nomes_por_uf.tsv.gz", "logradouros.tsv.gz", "bairros.tsv.gz",
                 "ceps_municipio.tsv.gz", "municipios.tsv"):
        arq = voc / nome
        tam = arq.stat().st_size if arq.is_file() else None
        p(f"  {nome:<22} " + ("NÃO ENCONTRADO" if tam is None else f"{tam / 1e6:.1f} MB" if tam >= 1e5 else f"{tam / 1e3:.0f} KB"))
    from rotulador.ops import geradores
    try:
        with geradores.usando(regiao="CE"):
            p(f"  amostra do Ceará: {geradores.gerar('nome')['texto']} · {geradores.gerar('endereço')['texto']}")
    except Exception as e:  # noqa: BLE001 - o diagnóstico mostra o erro, seja qual for
        p(f"  ERRO ao gerar nome/endereço do Ceará: {type(e).__name__}: {e}")

    dados, saida = pasta_de_dados(a)
    p(f"\n== Documentos em {dados} (saída e cache do OCR em {saida})")
    from rotulador.contexto import Contexto
    from rotulador.registro import SPLITS, Registro
    try:
        reg = Registro(dados, saida, log=lambda *_: None)
    except Exception as e:  # noqa: BLE001
        p(f"ERRO ao abrir a pasta: {type(e).__name__}: {e}")
        return salvar()
    cats_validas = {c["chave"] for c in json.loads((RAIZ / "config" / "categorias.json").read_text(
        encoding="utf-8"))["categorias"]}
    total = sum(len(reg.por_split[sp]) for sp in SPLITS)
    p(f"{total} documento(s): " + ", ".join(f"{sp} {len(reg.por_split[sp])}" for sp in SPLITS))
    if not ocr_ok:
        p("sem OCR não há campos: resolva o OCR acima primeiro")
        return salvar()
    sem, n = 0, 0
    for sp in SPLITS:
        for it in reg.por_split[sp]:
            if a.max and n >= a.max:
                break
            n += 1
            try:
                img = reg.carregar(it.doc)
                ctx = Contexto(it, img, saida, reg)
                linhas, campos = ctx.linhas, ctx.campos
            except Exception as e:  # noqa: BLE001
                p(f"\n[{sp}] {it.nome}: ERRO ao ler: {type(e).__name__}: {e}")
                sem += 1
                continue
            validos = [f for f in campos if f.get("categoria") in cats_validas and str(f.get("texto") or "").strip()]
            cats = Counter(f["categoria"] for f in validos)
            nome = [f["texto"] for f in validos if f["categoria"] == "nome"]
            end = [f["texto"] for f in validos if f["categoria"] == "endereço"]
            ok = bool(nome or end)
            sem += not ok
            p(f"\n[{sp}] {it.nome}: {img.shape[1]}x{img.shape[0]} px, {len(linhas)} linhas lidas")
            p(f"  campos: {', '.join(f'{c} {k}' for c, k in cats.most_common()) or 'nenhum'}")
            p(f"  FSTS: {'acha' if ok else 'NÃO ACHA o nome nem o endereço'}"
              + (f" · nome: {nome[:2]}" if nome else "") + (f" · endereço: {end[:2]}" if end else ""))
            if not ok:
                cat_da_linha = {}
                for f in campos:  # a categoria que o detector deu à linha (pela caixa)
                    for i, l in enumerate(linhas):
                        cx = l["caixa"]
                        if not (f["caixa"][2] <= cx[0] or f["caixa"][0] >= cx[2] or f["caixa"][3] <= cx[1]
                                or f["caixa"][1] >= cx[3]):
                            cat_da_linha.setdefault(i, f.get("categoria"))
                p("  linhas lidas (as primeiras), com o campo que o detector deu:")
                for i, l in enumerate(linhas[:a.linhas]):
                    p(f"    {str(cat_da_linha.get(i) or '-'):<20} {l['texto'][:90]}")
    p(f"\n== Resumo: {n} documento(s) olhado(s); o FSTS não acha o nome nem o endereço em {sem}")
    salvar()


def salvar() -> None:
    arq = RAIZ / "diagnostico.txt"
    arq.write_text("\n".join(relatorio) + "\n", encoding="utf-8")
    print(f"\n(relatório gravado em {arq})")


if __name__ == "__main__":
    main()
