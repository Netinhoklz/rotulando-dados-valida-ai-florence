"""Registro dos documentos de entrada e auditoria de vazamento entre splits.

O split de um documento vem SÓ da pasta onde ele está (dados/treino, dados/teste,
dados/validacao). O navegador nunca diz o split: manda o id do documento e o
servidor resolve tudo por aqui.

Na partida, cada página é identificada por dois hashes:
  - sha256 dos bytes do arquivo;
  - sha256 dos pixels decodificados (pega o mesmo documento re-salvo em outro
    formato, por exemplo PNG no treino e JPEG sem perdas... ou o mesmo PDF renomeado).
Se um hash aparece em dois splits, ou se um documento já rotulado em um split
agora está em outro, o registro levanta ErroVazamento e o servidor não sobe.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import documentos

SPLITS = ("treino", "teste", "validacao")
_VERSAO_CACHE = 1


class ErroVazamento(Exception):
    def __init__(self, conflitos: list[str]):
        self.conflitos = conflitos
        super().__init__("possível vazamento entre splits:\n  - " + "\n  - ".join(conflitos))


@dataclass(frozen=True)
class Item:
    doc: str  # id neutro (prefixo do hash dos pixels); vira o nome dos arquivos de saída
    split: str
    caminho: Path
    rel: str  # caminho relativo à pasta do split (pode conter nome de cliente: só vai no JSON)
    pagina: int
    paginas: int
    sha_arquivo: str
    sha_pixels: str
    altura: int
    largura: int

    @property
    def nome(self) -> str:
        return self.rel + (f" (pág. {self.pagina + 1}/{self.paginas})" if self.paginas > 1 else "")


def hash_pixels(img: np.ndarray) -> str:
    h = hashlib.sha256(f"{img.shape[0]}x{img.shape[1]}x{img.shape[2]}|".encode())
    h.update(np.ascontiguousarray(img).tobytes())
    return h.hexdigest()


def hash_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


class Registro:
    def __init__(self, raiz_dados: Path, raiz_saida: Path, dpi: int = documentos.DPI_PADRAO,
                 log=print):
        self.raiz_dados = Path(raiz_dados).resolve()
        self.raiz_saida = Path(raiz_saida).resolve()
        self.dpi = int(dpi)
        self.log = log
        self.itens: dict[str, Item] = {}
        self.por_split: dict[str, list[Item]] = {s: [] for s in SPLITS}
        self.avisos: list[str] = []
        self._construir()

    # ------------------------------------------------------------------ consulta
    def item(self, doc: str) -> Item:
        return self.itens[doc]

    def carregar(self, doc: str) -> np.ndarray:
        it = self.itens[doc]
        img = documentos.carregar(it.caminho, it.pagina, self.dpi)
        if hash_pixels(img) != it.sha_pixels:
            raise ErroVazamento([f"{it.split}/{it.nome} mudou desde a partida; reinicie o servidor"])
        return img

    # ------------------------------------------------------------------ construção
    def _construir(self) -> None:
        if not self.raiz_dados.is_dir():
            raise FileNotFoundError(f"pasta de dados não existe: {self.raiz_dados}")
        for p in sorted(self.raiz_dados.iterdir()):
            if p.name.startswith("."):
                continue
            if p.is_dir() and p.name not in SPLITS:
                self.avisos.append(f"pasta '{p.name}' em {self.raiz_dados} não é um split ({', '.join(SPLITS)}); ignorada")
            elif p.is_file():
                self.avisos.append(f"arquivo solto '{p.name}' fora de treino/teste/validacao; ignorado")

        arquivos: list[tuple[str, Path]] = []
        for split in SPLITS:
            pasta = self.raiz_dados / split
            pasta.mkdir(parents=True, exist_ok=True)
            for p in sorted(pasta.rglob("*")):
                if p.is_file() and documentos.eh_documento(p) and not any(
                        parte.startswith(".") for parte in p.relative_to(pasta).parts):
                    arquivos.append((split, p))

        cache_path = self.raiz_saida / "_cache" / "registro.json"
        cache = self._ler_cache(cache_path)
        novo_cache: dict[str, dict] = {}

        def processar(par):
            split, p = par
            st = p.stat()
            chave = f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}|{self.dpi}"
            if chave in cache:
                return split, p, chave, cache[chave], None
            try:
                info = {"sha_arquivo": hash_arquivo(p), "paginas": []}
                n = documentos.contar_paginas(p)
                for pg in range(n):
                    img = documentos.carregar(p, pg, self.dpi)
                    info["paginas"].append({"sha_pixels": hash_pixels(img),
                                            "altura": int(img.shape[0]), "largura": int(img.shape[1])})
                return split, p, chave, info, None
            except Exception as e:  # arquivo corrompido não derruba o registro
                return split, p, chave, None, f"{type(e).__name__}: {e}"

        if arquivos:
            self.log(f"Registrando {len(arquivos)} arquivo(s) de entrada...")
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
            resultados = list(pool.map(processar, arquivos))

        vistos: dict[str, Item] = {}
        for split, p, chave, info, erro in resultados:
            rel = p.relative_to(self.raiz_dados / split).as_posix()
            if erro:
                self.avisos.append(f"não consegui ler {split}/{rel}: {erro}")
                continue
            novo_cache[chave] = info
            n = len(info["paginas"])
            for pg, pinfo in enumerate(info["paginas"]):
                it = Item(doc=pinfo["sha_pixels"][:16], split=split, caminho=p.resolve(), rel=rel,
                          pagina=pg, paginas=n, sha_arquivo=info["sha_arquivo"],
                          sha_pixels=pinfo["sha_pixels"], altura=pinfo["altura"], largura=pinfo["largura"])
                if it.doc in vistos:
                    outro = vistos[it.doc]
                    if outro.split == split:
                        self.avisos.append(f"duplicata dentro de {split}: '{it.nome}' é igual a '{outro.nome}'; usando só a primeira")
                    continue  # conflito entre splits é reportado em _auditar
                vistos[it.doc] = it

        self._auditar(resultados)
        self._gravar_cache(cache_path, novo_cache)

        for it in vistos.values():
            self.itens[it.doc] = it
            self.por_split[it.split].append(it)
        for split in SPLITS:
            self.por_split[split].sort(key=lambda i: (i.rel.lower(), i.pagina))

    def _auditar(self, resultados) -> None:
        conflitos: list[str] = []
        por_hash: dict[str, dict[str, list[str]]] = {}
        for split, p, _, info, erro in resultados:
            if erro:
                continue
            rel = p.relative_to(self.raiz_dados / split).as_posix()
            hashes = {info["sha_arquivo"]} | {pg["sha_pixels"] for pg in info["paginas"]}
            for h in hashes:
                por_hash.setdefault(h, {}).setdefault(split, []).append(rel)
        for h, splits in por_hash.items():
            if len(splits) > 1:
                desc = " | ".join(f"{s}: {', '.join(sorted(set(r)))}" for s, r in sorted(splits.items()))
                conflitos.append(f"mesmo conteúdo em splits diferentes -> {desc}")

        # o que já foi rotulado pertence para sempre ao split onde foi rotulado
        ja_rotulado = self._hashes_ja_rotulados()
        for split, p, _, info, erro in resultados:
            if erro:
                continue
            rel = p.relative_to(self.raiz_dados / split).as_posix()
            hashes = {info["sha_arquivo"]} | {pg["sha_pixels"] for pg in info["paginas"]}
            hashes |= {pg["sha_pixels"][:16] for pg in info["paginas"]}
            for h in hashes:
                outro = ja_rotulado.get(h)
                if outro and outro != split:
                    conflitos.append(f"{split}/{rel} já foi rotulado (ou tem rascunho) em '{outro}'; "
                                     f"não mova documentos entre splits depois de rotular")
                    break
        if conflitos:
            raise ErroVazamento(sorted(set(conflitos)))

    def _hashes_ja_rotulados(self) -> dict[str, str]:
        dono: dict[str, str] = {}
        for split in SPLITS:
            base = self.raiz_saida / split
            manifesto = base / "manifesto.csv"
            if manifesto.is_file():
                with open(manifesto, newline="", encoding="utf-8") as f:
                    for linha in csv.DictReader(f):
                        for campo in ("sha_arquivo", "sha_pixels", "doc"):
                            if linha.get(campo):
                                dono.setdefault(linha[campo], split)
            rasc = base / "_rascunhos"
            if rasc.is_dir():
                for d in rasc.iterdir():
                    if d.is_dir():
                        dono.setdefault(d.name, split)
        return dono

    @staticmethod
    def _ler_cache(caminho: Path) -> dict:
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            return dados["itens"] if dados.get("versao") == _VERSAO_CACHE else {}
        except (OSError, ValueError, KeyError):
            return {}

    @staticmethod
    def _gravar_cache(caminho: Path, itens: dict) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp = caminho.with_suffix(".tmp")
        tmp.write_text(json.dumps({"versao": _VERSAO_CACHE, "itens": itens}), encoding="utf-8")
        os.replace(tmp, caminho)
