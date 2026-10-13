"""Conjunto de robustez: cópias de um split com perturbações FIXAS depois da edição — JPEG de várias qualidades e
repetido, WhatsApp, redução, desfoque, ruído, tela fotografada, impressão e nova captura, foto de celular — com
as máscaras levadas pela mesma geometria (rotulador.canal). É o teste de robustez do DTD/CLTD, do ADCD-Net e do
DocQT: o detector é avaliado em cada perfil com ``rotulador.treino.avaliar`` apontando para ``DESTINO/<perfil>``.
As cópias saem em PNG (a perturbação já inclui o codec); a anotação ganha ``robustez`` (perfil e passos).

    python -m rotulador.treino.robustez SAIDA SPLIT DESTINO [--perfis jpeg75,whatsapp] [--semente 1] [--limite N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .. import canal, exportar
from . import dados


def _scanner(**kw) -> dict:
    """Passo de scanner neutro (sem rotação, escala 1, sem tom, sem JPEG); ``kw`` liga o que o perfil quer."""
    return {"tipo": "scanner", "angulo": 0.0, "escala": 1.0, "desfoque": 0.3, "gama": 1.0, "contraste": 1.0, "brilho": 0.0,
            "ruido": 0.0, "jpeg": None, **kw}


def _sorteado(perfil: str):
    return lambda rng: canal.sortear(rng, {"perfil": perfil, "prob": 1.0, "captura": "depois"}, captura="depois")["passos"]


def _jpeg(q: int) -> dict:
    """Regravação em JPEG sem reamostrar (escala 1)."""
    return {"tipo": "app_edicao", "escala": 1.0, "jpeg": q}


PERFIS = {
    "jpeg90": lambda rng: [_jpeg(90)],
    "jpeg75": lambda rng: [_jpeg(75)],
    "jpeg50": lambda rng: [_jpeg(50)],
    "jpeg_dupla": lambda rng: [_jpeg(95), _jpeg(75)],
    "whatsapp": lambda rng: [{"tipo": "whatsapp", "lado": 1600, "jpeg": 75}],
    "reduzida": lambda rng: [_scanner(escala=0.7)],
    "desfoque": lambda rng: [_scanner(desfoque=1.5)],
    "ruido": lambda rng: [_scanner(ruido=6.0)],
    "tela": _sorteado("tela"),
    "impressao": _sorteado("impressao"),
    "celular": _sorteado("celular"),
}
PADRAO = ("jpeg75", "jpeg_dupla", "whatsapp", "reduzida", "desfoque", "ruido", "tela", "impressao")


def _png(caminho: Path, arr: np.ndarray) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(caminho, "PNG", compress_level=6)


def _caixas(anot: dict, classes: np.ndarray, tecnicas: np.ndarray) -> None:
    """Refaz a caixa de cada edição na geometria nova (classe × técnica; o mapa de edições não é gravado)."""
    ids = anot.get("ids_categoria") or {}
    for e in exportar.edicoes_de_anotacao(anot):
        m = (tecnicas == int(e.get("tecnica_num") or 0))
        if e.get("categoria") in ids:
            m &= classes == ids[e["categoria"]]
        ys, xs = np.nonzero(m)
        e["bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if ys.size else None
        e["pixels"] = int(m.sum())


def perturbar(arr: dict, theta: dict) -> dict | None:
    """Passa o par e as máscaras pelo canal ``theta`` e refaz os rótulos como o exportador faz; None se a edição
    sumiu (par igual)."""
    (o, e), (m, r), (c, t) = canal.aplicar([arr["original"], arr["editada"]], [arr["mascara"], arr["regiao"]],
                                           [arr["classes"], arr["tecnicas"]], theta, fase="depois")
    c, t = canal.ajustar_rotulos(c, r), canal.ajustar_rotulos(t, r)
    r &= (c > 0) & (t > 0)
    c[~r] = 0
    t[~r] = 0
    dif = np.abs(o.astype(np.int16) - e.astype(np.int16)).max(2)
    m &= r & (dif > 0)
    if not m.any():
        return None
    return {"original": o, "editada": e, "mascara": m, "regiao": r, "classes": c, "tecnicas": t,
            "ignorar": exportar.mascara_ignorar(m, r, dif > 0)}


def gerar(raiz_saida: Path, split: str, destino: Path, perfis=None, semente: int = 1, limite: int | None = None) -> dict:
    """Grava ``destino/<perfil>/<split>/`` com a mesma estrutura do split (original por versão, editada, as cinco
    máscaras, anotação e índices). Devolve contagens por perfil."""
    perfis = list(perfis or PADRAO)
    ruins = [p for p in perfis if p not in PERFIS]
    if ruins:
        raise ValueError(f"perfil desconhecido: {', '.join(ruins)} (há: {', '.join(PERFIS)})")
    amostras = dados.listar(raiz_saida, split)
    if limite:
        amostras = amostras[:int(limite)]
    out = {}
    for perfil in perfis:
        raiz = Path(destino) / perfil / split
        feitas = apagadas = 0
        for a in amostras:
            rng = random.Random(f"{semente}:{perfil}:{a['nome']}")
            theta = {"perfil": f"robustez:{perfil}", "semente": rng.randrange(2**31),
                     "passos": [{**p, "fase": "depois"} for p in PERFIS[perfil](rng)]}
            novo = perturbar(dados.carregar(a), theta)
            if novo is None:
                apagadas += 1
                continue
            nome = a["nome"]
            arq = {"original": f"originais/{nome}.png", "editada": f"editadas/{nome}.png", "mascara": f"mascaras/{nome}.png",
                   "mascara_regiao": f"mascaras_regiao/{nome}.png", "mascara_classes": f"mascaras_classes/{nome}.png",
                   "mascara_tecnicas": f"mascaras_tecnicas/{nome}.png", "mascara_ignorar": f"mascaras_ignorar/{nome}.png",
                   "anotacao": f"anotacoes/{nome}.json"}
            for chave, nome_arr in (("original", "original"), ("editada", "editada")):
                _png(raiz / arq[chave], novo[nome_arr])
            for chave, nome_arr in dados.CHAVES_MASCARA:
                _png(raiz / arq[chave], novo[nome_arr].astype(np.uint8) * 255)
            for chave, nome_arr in dados.CHAVES_ROTULO:
                _png(raiz / arq[chave], novo[nome_arr])
            anot = json.loads(Path(a["arquivos"]["anotacao"]).read_text(encoding="utf-8"))
            H, W = novo["mascara"].shape
            _caixas(anot, novo["classes"], novo["tecnicas"])
            anot.update({"arquivos": arq, "formato": "png", "qualidade": None, "largura": W, "altura": H,
                         "pixels_alterados": int(novo["mascara"].sum()), "pixels_regiao": int(novo["regiao"].sum()),
                         "fracao_alterada": round(float(novo["mascara"].mean()), 6),
                         "robustez": {"perfil": perfil, "canal": theta, "descricao": canal.descrever(theta),
                                      "de": str(Path(a["arquivos"]["anotacao"]).relative_to(Path(raiz_saida).resolve())),
                                      "nota": "cópia perturbada depois da edição; caixas e caixas_por_categoria refeitas "
                                              "pelas máscaras; camadas e operacoes seguem nas coordenadas da sessão"}})
            anot["caixas_por_categoria"] = exportar._caixas_por_categoria(novo["classes"], anot.get("ids_categoria") or {})
            if "caixas_ocorrencias_por_categoria" in anot:
                anot["caixas_ocorrencias_por_categoria"] = exportar._caixas_ocorrencias(exportar.edicoes_de_anotacao(anot))
            (raiz / "anotacoes").mkdir(parents=True, exist_ok=True)
            (raiz / arq["anotacao"]).write_text(json.dumps(anot, ensure_ascii=False, indent=1), encoding="utf-8")
            feitas += 1
        if feitas:
            exportar.reconstruir_indices(Path(destino) / perfil, split)
        out[perfil] = {"versoes": feitas, "apagadas": apagadas, "pasta": str(raiz)}
    return out


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gera cópias perturbadas de um split para medir robustez.")
    ap.add_argument("saida")
    ap.add_argument("split")
    ap.add_argument("destino")
    ap.add_argument("--perfis", default=",".join(PADRAO), help="lista separada por vírgula; há: " + ", ".join(PERFIS))
    ap.add_argument("--semente", type=int, default=1)
    ap.add_argument("--limite", type=int)
    a = ap.parse_args(argv)
    r = gerar(Path(a.saida), a.split, Path(a.destino), [p.strip() for p in a.perfis.split(",") if p.strip()], a.semente, a.limite)
    for perfil, c in r.items():
        print(f"{perfil}: {c['versoes']} versões em {c['pasta']}" + (f" ({c['apagadas']} com a edição apagada)" if c["apagadas"] else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(_main())
