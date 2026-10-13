"""Auditoria de vazamento do dataset rotulado. Rode ANTES de treinar:

    python auditar_vazamento.py                 (usa ./dados e ./saida)
    python auditar_vazamento.py --dados D --saida S --limiar 12
    python auditar_vazamento.py --sem-clientes   (pula o OCR dos documentos de entrada)
    python auditar_vazamento.py --sugerir-split  (grava saida/sugestao_split.json: cada emissor num split só)

ERRO (código de saída 1):
  - o mesmo conteúdo de entrada em dois splits (bytes ou pixels);
  - anotação cujo split, documento de origem ou doador de colagem é de outro split (colagem manual
    e técnicas automáticas que trazem letra ou campo de outro documento);
  - o mesmo arquivo de saída (hash) em dois splits; o mesmo documento rotulado em dois splits;
  - par original/editada em formatos diferentes, pelo nome e pelo arquivo de fato (formato, tamanho,
    modo, tabelas do JPEG); máscara ou editada com tamanho diferente do anotado;
  - arquivo citado na anotação que não existe;
  - o mesmo MODELO de conta (dHash das entradas a até --limiar bits, 10 por padrão) em splits
    diferentes: o teste mediria um layout que o treino já viu (contas do mesmo modelo diferem em
    menos de 0,05% dos pixels); também pega o mesmo papel recapturado (outro mês, foto e scan);
  - o mesmo cliente (lido pelo OCR) em documentos de splits diferentes: CPF, CPF mascarado
    (***.456.789-**), nome, nº de instalação/cliente ou linha digitável/código de barras (a mesma
    conta); o modelo decoraria o cliente em vez de aprender a fraude.
AVISO (revisar à mão):
  - textos digitados (valores falsos) repetidos em splits diferentes, e nomes/endereços falsos das
    técnicas automáticas repetidos (o vocabulário dos falsos é repartido por split desde a v2.1);
  - o mesmo endereço em splits diferentes (OCR; endereço impresso em mais de 20% dos documentos é do
    emissor e fica de fora);
  - o mesmo CNPJ (emissor) em documentos de splits diferentes, com a contagem por split, por menor que
    seja o emissor: não é vazamento do cliente, mas a generalização por emissor não está sendo medida.
    ``--sugerir-split`` imprime e grava em saida/sugestao_split.json uma atribuição de documentos a
    splits que mantém cada emissor e cada modelo de conta num split só, perto de 70/15/15 por número de
    documentos (greedy: grupos do maior para o menor, cada um vai para o split mais abaixo da cota;
    documentos sem emissor nem modelo repetido completam, um a um). Não move arquivo nenhum: é uma
    sugestão para montar as pastas.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import re
import unicodedata

from PIL import Image

from rotulador.qualidade import dhash, grupos_de_modelo
from rotulador.registro import SPLITS, ErroVazamento, Registro, hash_arquivo

RAIZ = Path(__file__).resolve().parent
CAMPOS_TEXTO = ("texto", "digitos", "conteudo", "prompt")
# texto novo das técnicas automáticas que entra na conferência de repetidos: só vocabulário aberto
# (data, valor e CEP falsos se repetem por acaso) e só o que foi inventado (cópia traz texto real)
CATEGORIAS_VOCABULARIO = ("nome", "endereço", "bairro", "cidade/uf")
FAMILIAS_COPIA = ("copia_interna", "colagem_externa")
EMISSOR = 0.2  # endereço em mais que esta fração dos documentos é do emissor, não do cliente
# o que identifica o cliente: repetido em splits diferentes é ERRO (endereço só avisa: pode ser do emissor)
CLIENTE = ("CPF", "CPF mascarado", "nome", "nº instalação/cliente", "linha digitável")
_CPF = re.compile(r"(?<![\d./-])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\d./-])")
_CPF_MASCARADO = re.compile(r"(?<![\w*•])[\d*xX•]{3}\.[\d*xX•]{3}\.[\d*xX•]{3}-[\d*xX•]{2}(?![\w*•])")
_CNPJ = re.compile(r"(?<![\d./-])\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?![\d./-])")


def _chave(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def ler_identificadores(reg: Registro, saida: Path, log=print) -> dict[tuple[str, str], set]:
    """{(tipo, valor): documentos} lidos pelo OCR dos documentos de entrada (cache em saida/_cache/ocr): CPF e
    CNPJ com dígito verificador válido, CPF mascarado (os dígitos que aparecem), linha digitável ou número do
    código de barras (44+ dígitos numa linha), nº de instalação/cliente, nome e endereço."""
    from rotulador import automatico
    from rotulador.ops.geradores import valida_cnpj, valida_cpf

    onde: dict[tuple[str, str], set] = defaultdict(set)
    sem_cache = [it for it in reg.itens.values()
                 if not (saida / "_cache" / "ocr" / f"{it.sha_pixels[:32]}.json").is_file()]
    if sem_cache:
        log(f"lendo (OCR) {len(sem_cache)} documento(s) sem cache para procurar o mesmo cliente em splits diferentes...")
    for it in reg.itens.values():
        try:
            campos = automatico.campos_do_documento(reg.carregar(it.doc), saida, it.sha_pixels)
        except (ImportError, RuntimeError):
            raise  # sem OCR: quem chama avisa uma vez
        except Exception as ex:  # noqa: BLE001 - um documento ilegível não derruba a auditoria
            log(f"não consegui ler {it.split}/{it.nome} pelo OCR: {ex}")
            continue
        for c in campos:
            cat, txt = c.get("categoria"), c.get("texto", "")
            if c.get("tipo", "texto") != "texto":
                continue
            # CPF/CNPJ em qualquer linha (o OCR junta "CPF ... RG ..."), só os de dígito verificador
            # válido (000.000.000-00 de modelo não é cliente nenhum)
            for m in _CPF.finditer(txt):
                dig = re.sub(r"\D", "", m.group())
                if valida_cpf(dig):
                    onde[("CPF", dig)].add(it)
            for m in _CNPJ.finditer(txt):
                dig = re.sub(r"\D", "", m.group())
                if valida_cnpj(dig):
                    onde[("CNPJ", dig)].add(it)
            for m in _CPF_MASCARADO.finditer(txt):  # LGPD: ***.456.789-** (os 6 do meio ainda identificam)
                if re.search(r"[*xX•]", m.group()) and sum(ch.isdigit() for ch in m.group()) >= 5:
                    onde[("CPF mascarado", re.sub(r"[xX•]", "*", m.group()))].add(it)
            digitos = re.sub(r"\D", "", txt)
            if len(digitos) >= 44:  # linha digitável (47/48) ou código de barras (44): a mesma conta
                onde[("linha digitável", digitos)].add(it)
            if cat == "identificador documento":
                k = re.sub(r"[^0-9A-Z]", "", _chave(txt))
                if len(k) >= 6 and sum(ch.isdigit() for ch in k) >= 5:
                    onde[("nº instalação/cliente", k)].add(it)
            elif cat in ("nome", "endereço"):
                k = _chave(txt)
                if len(k.split()) >= 2 and len(k) >= 8:
                    onde[(cat, k)].add(it)
    return onde


def mesmo_cliente(reg: Registro, saida: Path, log=print, onde=None) -> tuple[list[str], list[str]]:
    """Campos que identificam o cliente (CLIENTE: erro; endereço: aviso) lidos pelo OCR (``onde`` =
    ler_identificadores, lido aqui se não vier) e repetidos em splits diferentes. O CNPJ é do emissor: quem
    avisa é ``auditar``. Devolve (erros, avisos)."""
    if onde is None:
        onde = ler_identificadores(reg, saida, log)
    erros, avisos = [], []
    n = max(1, len(reg.itens))
    for (tipo, valor), itens in sorted(onde.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        splits = sorted({i.split for i in itens}, key=SPLITS.index)
        if len(splits) < 2:
            continue
        if tipo == "CNPJ" or (tipo == "endereço" and len(itens) > max(3, EMISSOR * n)):  # do emissor (base pequena: >3)
            continue
        por = ", ".join(f"{sp}: {sum(i.split == sp for i in itens)}" for sp in splits)
        ex = "; ".join(sorted(f"{i.split}/{i.nome}" for i in itens)[:4])
        msg = f"{'mesma' if tipo == 'linha digitável' else 'mesmo'} {tipo} '{valor}' em splits diferentes ({por}) -> " \
              f"mesmo cliente? ex.: {ex}"
        (erros if tipo in CLIENTE else avisos).append(msg)
    return erros, avisos


def emissor_por_documento(onde: dict) -> dict[str, str]:
    """{doc: CNPJ do emissor}: todo CNPJ lido (sem limiar de frequência: o emissor com poucos documentos também
    conta); documento com mais de um fica com o mais frequente."""
    emissores = sorted(((valor, itens) for (tipo, valor), itens in onde.items() if tipo == "CNPJ"),
                       key=lambda kv: (-len(kv[1]), kv[0]))
    por_doc: dict[str, str] = {}
    for cnpj, itens in emissores:
        for it in itens:
            por_doc.setdefault(it.doc, cnpj)
    return por_doc


def sugerir_split(reg: Registro, emissor_por_doc: dict[str, str], modelo_por_doc: dict[str, str] | None = None) -> dict:
    """Atribuição sugerida de documentos a splits que mantém cada emissor e cada modelo de conta (``modelo_por_doc``,
    de ``auditar``) num split só, perto de 70/15/15 por número de documentos: documentos que dividem emissor ou
    modelo formam um grupo; greedy, grupos do maior para o menor (documentos soltos por último, um a um), cada um
    vai para o split mais abaixo da cota. Não move nada."""
    cota = {"treino": 0.70, "teste": 0.15, "validacao": 0.15}
    modelo_por_doc = modelo_por_doc or {}
    pai: dict[str, str] = {}

    def raiz(k):
        pai.setdefault(k, k)
        while pai[k] != k:
            k = pai[k]
        return k
    for it in reg.itens.values():
        for chave in (emissor_por_doc.get(it.doc), modelo_por_doc.get(it.doc)):
            if chave:
                pai[raiz(f"doc:{it.doc}")] = raiz(chave)
    grupo_de = {it.doc: raiz(f"doc:{it.doc}") for it in reg.itens.values()}
    grupos: dict[str, list] = defaultdict(list)
    for it in reg.itens.values():
        grupos[grupo_de[it.doc]].append(it)
    total = len(reg.itens)
    atribuido = {sp: 0 for sp in SPLITS}
    destino: dict[str, str] = {}
    for chave, itens in sorted(grupos.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        sp = max(SPLITS, key=lambda s: cota[s] * total - atribuido[s])  # empate: ordem de SPLITS
        atribuido[sp] += len(itens)
        destino[chave] = sp
    docs = [{"doc": it.doc, "arquivo": f"{it.split}/{it.nome}", "split_atual": it.split,
             "split_sugerido": destino[grupo_de[it.doc]], "emissor": emissor_por_doc.get(it.doc),
             "modelo": modelo_por_doc.get(it.doc)} for sp in SPLITS for it in reg.por_split[sp]]
    return {"cota": cota, "por_split": atribuido, "emissores_conhecidos": len(set(emissor_por_doc.values())),
            "emissores": {cnpj: destino[grupo_de[doc]] for doc, cnpj in sorted(emissor_por_doc.items())
                          if doc in grupo_de},
            "mudancas": sum(d["split_atual"] != d["split_sugerido"] for d in docs), "documentos": docs}


def _assinatura(caminho: Path) -> dict | None:
    """Como o arquivo é de fato (não pelo nome): formato, tamanho, modo e, no JPEG, as tabelas de quantização (a
    qualidade). None se o Pillow não abre (PDF)."""
    try:
        with Image.open(caminho) as im:
            q = getattr(im, "quantization", None)
            return {"formato": im.format, "tamanho": im.size, "modo": im.mode,
                    "qualidade": sorted((k, list(v)) for k, v in q.items()) if q else None}
    except (OSError, ValueError):
        return None


def auditar(dados: Path, saida: Path, limiar: int = 10, dpi: int = 200, log=print, clientes: bool = True) -> dict:
    erros: list[str] = []
    avisos: list[str] = []

    # 1) entradas
    reg = None
    try:
        reg = Registro(dados, saida, dpi=dpi, log=lambda *_: None)
        avisos += reg.avisos
    except ErroVazamento as e:
        erros += [f"entrada: {c}" for c in e.conflitos]
    itens = reg.itens if reg else {}

    # 2) saídas
    dono_hash: dict[str, set[str]] = defaultdict(set)
    dono_doc: dict[str, set[str]] = defaultdict(set)
    textos: dict[str, set[str]] = defaultdict(set)
    n_anot = 0
    for sp in SPLITS:
        base = saida / sp
        for sub in ("originais", "editadas", "mascaras", "mascaras_regiao", "mascaras_classes"):
            pasta = base / sub
            if pasta.is_dir():
                for arq in pasta.iterdir():
                    if arq.is_file() and not arq.name.endswith(".tmp"):
                        if sub in ("originais", "editadas"):
                            dono_hash[hash_arquivo(arq)].add(f"{sp}/{sub}/{arq.name}")
        pasta_anot = base / "anotacoes"
        if not pasta_anot.is_dir():
            continue
        for arq in sorted(pasta_anot.glob("*.json")):
            n_anot += 1
            nome = f"{sp}/anotacoes/{arq.name}"
            try:
                a = json.loads(arq.read_text(encoding="utf-8"))
            except ValueError:
                erros.append(f"{nome}: JSON ilegível")
                continue
            if a.get("split") != sp:
                erros.append(f"{nome}: diz split '{a.get('split')}' mas está na pasta '{sp}'")
            doc = a.get("doc", "")
            dono_doc[doc].add(sp)
            fonte = a.get("fonte", {})
            if reg is not None:
                it = itens.get(doc)
                if it is None:
                    avisos.append(f"{nome}: documento de origem não está mais em dados/ (removido?)")
                elif it.split != sp:
                    erros.append(f"{nome}: a origem '{it.rel}' está hoje no split '{it.split}'")
                elif it.sha_pixels != fonte.get("sha_pixels"):
                    erros.append(f"{nome}: hash da origem não confere (arquivo de entrada mudou)")
            arqs = a.get("arquivos", {})
            for chave, rel in arqs.items():
                if not (base / rel).is_file():
                    erros.append(f"{nome}: arquivo '{rel}' não existe")
            o, e = arqs.get("original", ""), arqs.get("editada", "")
            if Path(o).suffix != Path(e).suffix or ("__q" in o) != ("__q" in e) or \
                    (("__q" in o) and o.split("__q")[-1] != e.split("__q")[-1]):
                erros.append(f"{nome}: original e editada em formatos/qualidades diferentes ({o} x {e})")
            if o and e and (base / o).is_file() and (base / e).is_file():
                ao, ae = _assinatura(base / o), _assinatura(base / e)
                if ao and ae:
                    dif = [k for k in ao if ao[k] != ae[k]]
                    if dif:
                        erros.append(f"{nome}: original e editada diferentes no arquivo (não só no nome): " + ", ".join(
                            "qualidade (tabelas do JPEG)" if k == "qualidade" else f"{k} {ao[k]} x {ae[k]}" for k in dif))
                    anotado = (a.get("largura"), a.get("altura"))
                    if ae["tamanho"] != anotado:
                        erros.append(f"{nome}: editada {ae['tamanho']} != imagem anotada {anotado}")
            try:
                tam = (a["largura"], a["altura"])
                for chave in ("mascara", "mascara_regiao", "mascara_classes", "mascara_tecnicas", "mascara_ignorar"):
                    if chave in arqs and (base / arqs[chave]).is_file():
                        with Image.open(base / arqs[chave]) as m:
                            if m.size != tuple(tam):
                                erros.append(f"{nome}: {chave} {m.size} != imagem {tam}")
            except KeyError:
                erros.append(f"{nome}: sem largura/altura")
            for op in a.get("operacoes", []):
                meta = op.get("meta") or {}
                doadores = []
                if op.get("tipo") == "colar":
                    doadores.append(meta.get("doador") or op.get("params", {}).get("doador"))
                for chave in ("origem_glifo", "origem_campo"):  # técnicas automáticas (catálogo)
                    if isinstance(meta.get(chave), dict) and meta[chave].get("doc"):
                        doadores.append(meta[chave]["doc"])
                for doador in doadores:
                    it = itens.get(doador) if reg is not None else None
                    if reg is not None and it is None:
                        avisos.append(f"{nome}: doador {doador} não está mais em dados/")
                    elif it is not None and it.split != sp:
                        erros.append(f"{nome}: colagem com doador do split '{it.split}' ({it.rel})")
                for campo in CAMPOS_TEXTO:
                    v = op.get("params", {}).get(campo)
                    if campo != "prompt" and isinstance(v, str) and v.strip():
                        textos[v.strip().upper()].add(sp)
            for ed in a.get("edicoes", []):
                novo = (ed.get("texto_novo") or "").strip()
                if novo and ed.get("categoria") in CATEGORIAS_VOCABULARIO and ed.get("familia") not in FAMILIAS_COPIA \
                        and novo != (ed.get("texto_antigo") or "").strip():  # retoque sem mudar o texto
                    textos[novo.upper()].add(sp)

    for h, onde in dono_hash.items():
        splits = {o.split("/")[0] for o in onde}
        if len(splits) > 1:
            erros.append(f"mesmo arquivo de saída em splits diferentes: {sorted(onde)}")
    for doc, splits in dono_doc.items():
        if len(splits) > 1:
            erros.append(f"documento {doc} rotulado em mais de um split: {sorted(splits)}")
    repetidos = sorted((t, s) for t, s in textos.items() if len(s) > 1)
    for t, s in repetidos[:100]:
        avisos.append(f"texto digitado repetido em {sorted(s)}: '{t[:60]}'")
    if len(repetidos) > 100:
        avisos.append(f"... e mais {len(repetidos) - 100} textos repetidos entre splits")

    # 3) o mesmo cliente em splits diferentes e o mesmo emissor em mais de um split (OCR dos documentos de entrada)
    emissor_por_doc: dict[str, str] = {}
    por_emissor: dict[str, dict[str, int]] = {}
    if reg is not None and clientes:
        onde = {}
        try:
            onde = ler_identificadores(reg, saida, log)
            e_cli, a_cli = mesmo_cliente(reg, saida, log, onde)
            erros += e_cli
            avisos += a_cli
            emissor_por_doc = emissor_por_documento(onde)
        except (ImportError, RuntimeError) as ex:  # ocr.OCRIndisponivel é RuntimeError
            avisos.append(f"OCR indisponível ({ex}): não procurei o mesmo cliente em splits diferentes")
        for doc, cnpj in emissor_por_doc.items():
            cont = por_emissor.setdefault(cnpj, {})
            cont[itens[doc].split] = cont.get(itens[doc].split, 0) + 1
        for (tipo, cnpj), docs in sorted(onde.items()):
            splits = sorted({i.split for i in docs}, key=SPLITS.index)
            if tipo == "CNPJ" and len(splits) > 1:  # todo CNPJ, por menor que seja o emissor
                por = ", ".join(f"{sp}: {sum(i.split == sp for i in docs)}" for sp in splits)
                avisos.append(f"emissor {cnpj} em {', '.join(splits[:-1])} e {splits[-1]}: a generalização por emissor "
                              f"não está sendo medida ({por}; --sugerir-split propõe uma divisão por emissor)")

    # 4) o mesmo modelo de conta em splits diferentes (dHash): o teste mediria um layout que o treino já viu
    perto, modelo_por_doc = 0, {}
    if reg is not None and limiar > 0:
        hashes = {}
        for it in itens.values():
            try:
                hashes[it.doc] = dhash(reg.carregar(it.doc))
            except Exception as ex:  # noqa: BLE001 - auditoria não para por um arquivo
                avisos.append(f"não consegui ler {it.split}/{it.nome}: {ex}")
        for i, g in enumerate((g for g in grupos_de_modelo(hashes, limiar) if len(g) > 1), 1):
            modelo_por_doc.update({d: f"modelo {i}" for d in g})
            splits = sorted({itens[d].split for d in g}, key=SPLITS.index)
            if len(splits) < 2:
                continue
            perto += sum(1 for x, y in combinations(g, 2) if itens[x].split != itens[y].split
                         and int((hashes[x] != hashes[y]).sum()) <= limiar)
            por = ", ".join(f"{sp}: {sum(itens[d].split == sp for d in g)}" for sp in splits)
            ex = "; ".join(sorted(f"{itens[d].split}/{itens[d].nome}" for d in g)[:6])
            erros.append(f"mesmo modelo de conta em splits diferentes (dHash <= {limiar}/256; {por}): o teste mediria "
                         f"um layout que o treino já viu (também pega o mesmo papel recapturado). ex.: {ex}")

    rel = {"erros": erros, "avisos": avisos, "anotacoes": n_anot,
           "entradas": {sp: len(reg.por_split[sp]) for sp in SPLITS} if reg else None,
           "quase_duplicatas": perto, "emissores": por_emissor, "emissor_por_doc": emissor_por_doc,
           "modelo_por_doc": modelo_por_doc}
    return rel


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Auditoria de vazamento entre treino/teste/validação")
    ap.add_argument("--dados", default=str(RAIZ / "dados"))
    ap.add_argument("--saida", default=str(RAIZ / "saida"))
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--limiar", type=int, default=10,
                    help="distância dHash (de 256) até onde duas entradas são o mesmo modelo de conta (erro entre splits); 0 desliga")
    ap.add_argument("--sem-clientes", action="store_true", help="não lê (OCR) os documentos procurando o mesmo cliente")
    ap.add_argument("--sugerir-split", action="store_true",
                    help="grava saida/sugestao_split.json: cada emissor e modelo num split só, perto de 70/15/15 (não move nada)")
    a = ap.parse_args()
    saida = Path(a.saida)
    rel = auditar(Path(a.dados), saida, a.limiar, a.dpi, clientes=not a.sem_clientes)
    linhas = [f"anotações auditadas: {rel['anotacoes']}   entradas: {rel['entradas']}", ""]
    linhas += [f"ERRO   {e}" for e in rel["erros"]] or ["nenhum erro de vazamento encontrado"]
    linhas += [""] + [f"AVISO  {w}" for w in rel["avisos"]]
    texto = "\n".join(linhas)
    print(texto)
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "relatorio_vazamento.txt").write_text(texto + "\n", encoding="utf-8")
    (saida / "relatorio_vazamento.json").write_text(json.dumps(rel, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.sugerir_split:
        try:
            sug = sugerir_split(Registro(Path(a.dados), saida, dpi=a.dpi, log=lambda *_: None), rel["emissor_por_doc"],
                                rel["modelo_por_doc"])
        except ErroVazamento:
            print("\nsugestão de split: resolva primeiro o vazamento na entrada")
        else:
            (saida / "sugestao_split.json").write_text(json.dumps(sug, ensure_ascii=False, indent=1), encoding="utf-8")
            por = ", ".join(f"{sp}: {n}" for sp, n in sug["por_split"].items())
            print(f"\nsugestão de split (cada emissor e modelo num split só; {sug['emissores_conhecidos']} emissor(es) lido(s) "
                  f"pelo OCR): {por}; {sug['mudancas']} documento(s) mudariam de split")
            for d in sug["documentos"]:
                if d["split_atual"] != d["split_sugerido"]:
                    emissor = f" (emissor {d['emissor']})" if d["emissor"] else ""
                    modelo = f" ({d['modelo']})" if d["modelo"] else ""
                    print(f"  {d['arquivo']} -> {d['split_sugerido']}{emissor}{modelo}")
            print(f"gravada em {saida / 'sugestao_split.json'} (nenhum arquivo foi movido)")
    if rel["anotacoes"]:  # o datasheet mostra o resultado desta auditoria
        try:
            from rotulador import datasheet
            datasheet.escrever(saida)
        except Exception as e:  # noqa: BLE001 - a auditoria vale mesmo se o datasheet não puder ser refeito
            print(f"(não consegui refazer o datasheet: {e})")
    print(f"\nrelatório salvo em {saida / 'relatorio_vazamento.txt'}")
    sys.exit(1 if rel["erros"] else 0)


if __name__ == "__main__":
    main()
