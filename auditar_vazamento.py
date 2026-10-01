"""Auditoria de vazamento do dataset rotulado. Rode ANTES de treinar:

    python auditar_vazamento.py                 (usa ./dados e ./saida)
    python auditar_vazamento.py --dados D --saida S --limiar 12

ERRO (código de saída 1):
  - o mesmo conteúdo de entrada em dois splits (bytes ou pixels);
  - anotação cujo split, documento de origem ou doador de colagem é de outro split;
  - o mesmo arquivo de saída (hash) em dois splits; o mesmo documento rotulado em dois splits;
  - par original/editada em formatos diferentes; máscara com tamanho diferente da imagem;
  - arquivo citado na anotação que não existe.
AVISO (revisar à mão):
  - textos digitados (valores falsos) repetidos em splits diferentes;
  - quase-duplicatas entre splits (dHash): contas da mesma concessionária têm layout
    quase igual, então isto não bloqueia; serve para achar o MESMO cliente em splits
    diferentes (mês diferente da mesma conta, foto e scan do mesmo papel...).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from rotulador.registro import SPLITS, ErroVazamento, Registro, hash_arquivo

RAIZ = Path(__file__).resolve().parent
CAMPOS_TEXTO = ("texto", "digitos", "conteudo", "prompt")


def dhash(img: np.ndarray, lado: int = 16) -> np.ndarray:
    cinza = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    peq = cv2.resize(cinza, (lado + 1, lado), interpolation=cv2.INTER_AREA).astype(np.int16)
    return (peq[:, 1:] > peq[:, :-1]).ravel()


def auditar(dados: Path, saida: Path, limiar: int = 10, dpi: int = 200, log=print) -> dict:
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
            try:
                tam = (a["largura"], a["altura"])
                for chave in ("mascara", "mascara_regiao", "mascara_classes"):
                    if chave in arqs and (base / arqs[chave]).is_file():
                        with Image.open(base / arqs[chave]) as m:
                            if m.size != tuple(tam):
                                erros.append(f"{nome}: {chave} {m.size} != imagem {tam}")
            except KeyError:
                erros.append(f"{nome}: sem largura/altura")
            for op in a.get("operacoes", []):
                if op.get("tipo") == "colar":
                    doador = op.get("meta", {}).get("doador") or op.get("params", {}).get("doador")
                    it = itens.get(doador) if reg is not None else None
                    if reg is not None and it is None:
                        avisos.append(f"{nome}: doador {doador} não está mais em dados/")
                    elif it is not None and it.split != sp:
                        erros.append(f"{nome}: colagem com doador do split '{it.split}' ({it.rel})")
                for campo in CAMPOS_TEXTO:
                    v = op.get("params", {}).get(campo)
                    if campo != "prompt" and isinstance(v, str) and v.strip():
                        textos[v.strip().upper()].add(sp)

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

    # 3) quase-duplicatas entre splits (só aviso)
    perto = []
    if reg is not None and limiar > 0:
        hashes = {}
        for it in itens.values():
            try:
                hashes[it.doc] = (it, dhash(reg.carregar(it.doc)))
            except Exception as ex:  # noqa: BLE001 - auditoria não para por um arquivo
                avisos.append(f"não consegui ler {it.split}/{it.nome}: {ex}")
        for (a1, (i1, h1)), (a2, (i2, h2)) in combinations(hashes.items(), 2):
            if i1.split != i2.split:
                d = int((h1 != h2).sum())
                if d <= limiar:
                    perto.append((d, f"{i1.split}/{i1.nome}", f"{i2.split}/{i2.nome}"))
        perto.sort()
        if perto:
            # medido: dHash não separa "mesmo papel recapturado" de "outro cliente, mesmo modelo
            # de conta"; a lista é só um ponto de partida para olhar à mão
            avisos.append(f"{len(perto)} par(es) parecido(s) entre splits (dHash <= {limiar}/256). Contas do MESMO "
                          f"MODELO também caem aqui; confira os primeiros à mão procurando o MESMO cliente/papel:")
        for d, x, y in perto[:20]:
            avisos.append(f"  parecido (dHash {d}/256): {x}  <->  {y}")

    rel = {"erros": erros, "avisos": avisos, "anotacoes": n_anot,
           "entradas": {sp: len(reg.por_split[sp]) for sp in SPLITS} if reg else None,
           "quase_duplicatas": len(perto)}
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
    ap.add_argument("--limiar", type=int, default=10, help="distância dHash (de 256) para avisar quase-duplicata; 0 desliga")
    a = ap.parse_args()
    saida = Path(a.saida)
    rel = auditar(Path(a.dados), saida, a.limiar, a.dpi)
    linhas = [f"anotações auditadas: {rel['anotacoes']}   entradas: {rel['entradas']}", ""]
    linhas += [f"ERRO   {e}" for e in rel["erros"]] or ["nenhum erro de vazamento encontrado"]
    linhas += [""] + [f"AVISO  {w}" for w in rel["avisos"]]
    texto = "\n".join(linhas)
    print(texto)
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "relatorio_vazamento.txt").write_text(texto + "\n", encoding="utf-8")
    (saida / "relatorio_vazamento.json").write_text(json.dumps(rel, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nrelatório salvo em {saida / 'relatorio_vazamento.txt'}")
    sys.exit(1 if rel["erros"] else 0)


if __name__ == "__main__":
    main()
