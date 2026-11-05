"""Pesos regionais dos nomes de pessoa (prenomes e sobrenomes) de uma UF: com ``regiao``, o gerador sorteia os nomes
com o jeito de lá (no Ceará: FRANCISCO, RAIMUNDO, FRANCISCA; SOUSA, BEZERRA, HOLANDA, ALENCAR).

Fontes (baixadas para o diretório de cache, nunca para o projeto):
  * IBGE, API de nomes do Censo 2010 (servicodados.ibge.gov.br/api/v2/censos/nomes/<nome>?groupBy=UF): a proporção de
    cada prenome em cada UF (por 100 mil habitantes), para os prenomes mais comuns da base;
  * TSE, candidaturas das eleições municipais de 2024 e 2020 (consulta_cand): os sobrenomes de quem se candidatou em
    cada UF. Só contagens ficam em memória; nenhum nome completo é gravado.

Saída: config/vocabulario/nomes_por_uf.tsv.gz, com as colunas uf, lista (prenome ou sobrenome), nome (sem acento,
como nas outras bases), fator (proporção na UF / proporção no país, suavizada e limitada a 0,05–20) e n_uf (a
frequência na UF). Uma UF nova é acrescentada às que já estão no arquivo.

    .venv\\Scripts\\python.exe ferramentas\\construir_nomes_regiao.py --uf CE [--prenomes 2500] [--cache DIR]
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import csv
import gzip
import hashlib
import io
import json
import sys
import tempfile
import time
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
VOCAB = RAIZ / "config" / "vocabulario"
SAIDA = VOCAB / "nomes_por_uf.tsv.gz"
URL_IBGE = "https://servicodados.ibge.gov.br/api/v2/censos/nomes/{}?groupBy=UF"
URL_TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_{}.zip"
ANOS_TSE = (2024, 2020)  # eleições municipais: todos os municípios, muitos candidatos por UF
PARTICULAS = {"DA", "DE", "DO", "DOS", "DAS", "E", "D"}
SUFIXOS = {"FILHO", "FILHA", "JUNIOR", "JR", "NETO", "NETA", "SOBRINHO", "SOBRINHA", "SEGUNDO", "TERCEIRO"}
FATOR_MIN, FATOR_MAX = 0.05, 20.0


def _ascii(t: str) -> str:
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().upper()


def _tsv(nome: str) -> list[dict]:
    with gzip.open(VOCAB / nome, "rt", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _codigo_uf(uf: str) -> str:
    with open(VOCAB / "municipios.tsv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["uf"] == uf:
                return r["cod_ibge"][:2]
    sys.exit(f"UF desconhecida: {uf}")


def _baixar(url: str, destino: Path) -> Path:
    if destino.is_file():
        return destino
    print(f"baixando {url}", flush=True)
    tmp = destino.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "rotulador-construir-nomes"})
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        while bloco := r.read(1 << 20):
            f.write(bloco)
    tmp.replace(destino)
    return destino


# --------------------------------------------------------------------------- prenomes (IBGE, Censo 2010)
def _prenome_na_uf(nome: str, cod_uf: str, cache: Path) -> tuple[float, float, int] | None:
    """(proporção na UF, proporção no país, frequência na UF), por 100 mil habitantes; None se o IBGE não tem o nome."""
    arq = cache / "ibge" / f"{nome}.json"
    if arq.is_file():
        dados = json.loads(arq.read_text(encoding="utf-8"))
    else:
        for tentativa in range(3):
            try:
                req = urllib.request.Request(URL_IBGE.format(nome.lower()), headers={"User-Agent": "rotulador-construir-nomes"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    dados = json.loads(r.read().decode("utf-8"))
                break
            except (OSError, ValueError):
                time.sleep(1 + tentativa)
        else:
            return None
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(json.dumps(dados), encoding="utf-8")
    if not dados:
        return None
    freq = pop = 0
    uf = None
    for loc in dados:
        r = loc["res"][0]
        freq += r["frequencia"]
        pop += r["populacao"]
        if loc["localidade"] == cod_uf:
            uf = (r["proporcao"], r["frequencia"])
    if uf is None or not pop:
        return None
    return uf[0], freq / pop * 1e5, uf[1]


def prenomes(uf: str, n: int, cache: Path) -> list[tuple[str, float, int]]:
    cod = _codigo_uf(uf)
    base = sorted(_tsv("prenomes.tsv.gz"), key=lambda r: -(int(r["freq_f"]) + int(r["freq_m"])))[:n]
    nomes = [r["nome"] for r in base if r["nome"].isalpha()]
    out = []
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for i, (nome, r) in enumerate(zip(nomes, ex.map(lambda x: _prenome_na_uf(x, cod, cache), nomes)), 1):
            if r:
                p_uf, p_br, f_uf = r
                fator = (p_uf + 5.0) / (p_br + 5.0)  # suavizado: nome raro não vira 20x por acaso
                out.append((nome, min(FATOR_MAX, max(FATOR_MIN, fator)), f_uf))
            if i % 250 == 0:
                print(f"  prenomes: {i}/{len(nomes)}", flush=True)
    return out


# --------------------------------------------------------------------------- sobrenomes (TSE)
def sobrenomes(uf: str, cache: Path) -> list[tuple[str, float, int]]:
    conhecidos = {r["ascii"] for r in _tsv("sobrenomes.tsv.gz") if " " not in r["ascii"]}
    br, na_uf = collections.Counter(), collections.Counter()
    n_br = n_uf = 0
    vistos = set()
    for ano in ANOS_TSE:
        z = zipfile.ZipFile(_baixar(URL_TSE.format(ano), cache / f"consulta_cand_{ano}.zip"))
        csvs = [x for x in z.namelist() if x.upper().endswith("_BRASIL.CSV")] or [x for x in z.namelist() if x.lower().endswith(".csv")]
        for nome_csv in csvs:
            with z.open(nome_csv) as bruto:
                for linha in csv.DictReader(io.TextIOWrapper(bruto, encoding="latin-1", newline=""), delimiter=";"):
                    nome = _ascii(linha.get("NM_CANDIDATO") or "")
                    toks = [t for t in nome.replace(".", " ").split() if t.isalpha()]
                    if len(toks) < 2:
                        continue
                    chave = hashlib.blake2b(f"{nome}|{linha.get('DT_NASCIMENTO')}".encode(), digest_size=8).digest()
                    if chave in vistos:  # a mesma pessoa em dois anos conta uma vez
                        continue
                    vistos.add(chave)
                    sob = {t for t in toks[1:] if t not in PARTICULAS and t not in SUFIXOS and t in conhecidos}
                    br.update(sob)
                    n_br += 1
                    if linha.get("SG_UF") == uf:
                        na_uf.update(sob)
                        n_uf += 1
        z.close()
    print(f"  candidaturas: {n_br} pessoas no país, {n_uf} em {uf}", flush=True)
    out = []
    for s, c_br in br.items():
        if c_br < 30:  # pouca evidência: fica com o peso do país
            continue
        c_uf = na_uf.get(s, 0)
        fator = ((c_uf + 0.5) / max(1, n_uf)) / ((c_br + 0.5) / n_br)
        out.append((s, min(FATOR_MAX, max(FATOR_MIN, fator)), c_uf))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uf", required=True)
    ap.add_argument("--prenomes", type=int, default=2500)
    ap.add_argument("--cache", default=str(Path(tempfile.gettempdir()) / "rotulador_nomes_regiao"))
    a = ap.parse_args()
    uf, cache = a.uf.upper(), Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    pre = prenomes(uf, a.prenomes, cache)
    sob = sobrenomes(uf, cache)
    antigas = []
    if SAIDA.is_file():
        with gzip.open(SAIDA, "rt", encoding="utf-8") as f:
            antigas = [r for r in csv.DictReader(f, delimiter="\t") if r["uf"] != uf]
    with gzip.open(SAIDA, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["uf", "lista", "nome", "fator", "n_uf"])
        for r in antigas:
            w.writerow([r["uf"], r["lista"], r["nome"], r["fator"], r["n_uf"]])
        for lista, linhas in (("prenome", pre), ("sobrenome", sob)):
            for nome, fator, n in sorted(linhas, key=lambda x: -x[1]):
                w.writerow([uf, lista, nome, f"{fator:.4f}", n])
    print(f"{SAIDA}: {uf} com {len(pre)} prenomes e {len(sob)} sobrenomes")
    for lista, linhas in (("prenomes", pre), ("sobrenomes", sob)):
        top = sorted(linhas, key=lambda x: -x[2])[:12]
        print(f"  {lista} mais comuns em {uf}: " + ", ".join(f"{n} ({fator:.2f}x)" for n, fator, _ in top))


if __name__ == "__main__":
    main()
