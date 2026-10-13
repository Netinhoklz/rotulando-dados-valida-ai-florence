"""Carregador do dataset gerado, para treinar um detector de adulteração (sem depender de framework).

O que a literatura pede do lado do treino (docs/pesquisa_geracao_dados.md, quinta rodada):
  - o par (limpa, falsa) do MESMO documento no mesmo lote (SEED 2026): cada amostra traz original e editada
    no mesmo recorte, e ``conjunto_torch`` devolve as duas juntas;
  - recortes que CONTÊM a edição: com 0,3–4% da página alterada (DocForge-Bench) um recorte ao acaso quase
    nunca tem pixel positivo; ``recortar`` centra na edição, em texto ou ao acaso, por sorteio;
  - alvo de borda suave da região (MVSS-Net, Face X-ray, cabeça de borda do DTD) e máscara de tinta
    (ADCD-Net separa texto de fundo): ``borda`` e ``tinta``;
  - currículo por dificuldade (CLTD/DTD, mineração de casos difíceis): ``curriculo`` ordena pela nota do
    perito gravada em cada versão;
  - pesos pela taxa-base do que é servido (perda ponderada/focal; DocForge-Bench): ``pesos``; a mesma original
    serve N versões, e ``equilibrio`` dá a cada original distinta o mesmo peso no total das autênticas;
  - aumento de canal no treino (JPEG repetido com tabelas de aparelho, reamostragem, desfoque, ruído, tela),
    igual na imagem e nas máscaras, pelo mesmo rotulador.canal: ``aumentar``.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from .. import canal, documentos, exportar, qualidade
from ..selecao import mascara_tinta

CHAVES_MASCARA = (("mascara", "mascara"), ("mascara_regiao", "regiao"), ("mascara_ignorar", "ignorar"))
CHAVES_ROTULO = (("mascara_classes", "classes"), ("mascara_tecnicas", "tecnicas"))


def _bate(fornecedores: list[dict], filtro: dict) -> bool:
    """Alguma edição usou um dos fornecedores do filtro? ``filtro``: {papel: valor ou lista de valores}."""
    return any(f.get(k) in ([v] if isinstance(v, str) else v) for f in fornecedores for k, v in filtro.items())


def listar(raiz_saida: Path, split: str, revisao=None, sem_alertas: bool = False, so_fornecedor: dict | None = None,
           excluir_fornecedor: dict | None = None, sem_ocorrencias_antigas: bool = False) -> list[dict]:
    """Uma entrada por versão do split, lida dos JSON de anotação (a fonte da verdade), com o que serve para
    amostrar, filtrar e agrupar: caminhos (absolutos), famílias, técnicas, níveis, perícia, canal, captura,
    falsificador, fornecedores ("papel=valor"), fração alterada, dificuldade, pista semântica, revisão e alertas de
    qualidade. Anotação ilegível ou sem editada/máscara fica de fora.

    Filtros: ``revisao`` (estados aceitos, ex.: ("aprovada",)); ``sem_alertas`` tira as versões com alerta gravado
    por ``auditar_qualidade.py --gravar`` (as não auditadas passam); ``so_fornecedor``/``excluir_fornecedor``
    ({papel: valor ou lista}, papéis de ``edicoes[].fornecedores``: apagar, texto, fonte, grao...) deixam um gerador
    de fora do treino e só ele na avaliação, ex.: ``excluir_fornecedor={"fonte": "Arial"}``;
    ``sem_ocorrencias_antigas`` tira as versões em que o mesmo dado ficou com o valor antigo em outro lugar da página
    (``ocorrencias_antigas`` do gerador: o documento se contradiz sem que a máscara aponte o lugar)."""
    raiz = (Path(raiz_saida) / split).resolve()
    revisao = {revisao} if isinstance(revisao, str) else set(revisao) if revisao else None
    out = []
    for arq in sorted((raiz / "anotacoes").glob("*.json")) if (raiz / "anotacoes").is_dir() else []:
        try:
            a = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        arqs = a.get("arquivos") or {}
        if not arqs.get("original") or not arqs.get("editada") or not arqs.get("mascara"):
            continue
        eds = exportar.edicoes_de_anotacao(a)
        forn = [e["fornecedores"] for e in eds if isinstance(e.get("fornecedores"), dict)]
        estado = (a.get("revisao") or {}).get("estado", "pendente")
        alertas = qualidade.alertas_de(a)
        if (revisao and estado not in revisao) or (sem_alertas and alertas) or \
                (sem_ocorrencias_antigas and a.get("ocorrencias_antigas")):
            continue
        if (so_fornecedor and not _bate(forn, so_fornecedor)) or (excluir_fornecedor and _bate(forn, excluir_fornecedor)):
            continue
        out.append({"nome": f"{a['doc']}__v{int(a['versao']):02d}", "split": split, "doc": a["doc"], "versao": a["versao"],
                    "arquivos": {k: raiz / v for k, v in arqs.items()},
                    "dpi": (a.get("fonte") or {}).get("dpi_pdf") or documentos.DPI_PADRAO,
                    "familias": sorted({e["familia"] for e in eds if e.get("familia")}),
                    "tecnicas": sorted({e["tecnica"] for e in eds if e.get("tecnica")}),
                    "niveis": sorted({e["nivel"] for e in eds if e.get("nivel")}),
                    "pericia": a.get("pericia") or "auto", "canal": (a.get("canal") or {}).get("perfil") or "",
                    "captura_antes": a.get("captura_antes") or "", "falsificador": (a.get("falsificador") or {}).get("nome") or "",
                    "fornecedores": sorted({f"{k}={v}" for f in forn for k, v in f.items()}),
                    "fracao_alterada": float(a.get("fracao_alterada") or 0.0), "dificuldade": exportar.dificuldade_de(a),
                    "sem_pista_semantica": (a.get("coerencia") or {}).get("sem_pista_semantica"),
                    "revisao": estado, "alertas": alertas, "ocorrencias_antigas": len(a.get("ocorrencias_antigas") or []),
                    "largura": a.get("largura"), "altura": a.get("altura")})
    return out


def _ler(caminho: Path, dpi: int) -> np.ndarray:
    return documentos.carregar(Path(caminho), 0, dpi)


def carregar(amostra: dict) -> dict:
    """Os arrays da versão: ``original``/``editada`` RGB uint8; ``mascara``/``regiao``/``ignorar`` bool;
    ``classes``/``tecnicas`` uint8 (0 = não editado). Máscara que falta (anotação antiga) sai zerada."""
    arqs, dpi = amostra["arquivos"], amostra["dpi"]
    ed = _ler(arqs["editada"], dpi)
    H, W = ed.shape[:2]
    out = {"original": _ler(arqs["original"], dpi), "editada": ed}
    for chave, nome in CHAVES_MASCARA:
        out[nome] = _ler(arqs[chave], dpi)[..., 0] > 127 if arqs.get(chave) else np.zeros((H, W), bool)
    for chave, nome in CHAVES_ROTULO:
        out[nome] = _ler(arqs[chave], dpi)[..., 0].astype(np.uint8) if arqs.get(chave) else np.zeros((H, W), np.uint8)
    ruim = [k for k, v in out.items() if v.shape[:2] != (H, W)]
    if ruim:
        raise ValueError(f"{amostra['nome']}: {', '.join(ruim)} com tamanho diferente da editada")
    return out


def borda(regiao: np.ndarray, raio: int = 3) -> np.ndarray:
    """Alvo de borda suave (float32 0..1) da região editada: 1 na fronteira, caindo a 0 a ``raio`` px dela — a
    supervisão de borda do MVSS-Net/DTD e a "costura" que a face X-ray ensina a ver."""
    u8 = regiao.astype(np.uint8)
    k = np.ones((3, 3), np.uint8)
    fronteira = (cv2.dilate(u8, k) > 0) & ~(cv2.erode(u8, k) > 0)
    if not fronteira.any():
        return np.zeros(regiao.shape, np.float32)
    dist = cv2.distanceTransform((~fronteira).astype(np.uint8), cv2.DIST_L2, 3)
    return np.clip(1.0 - dist / float(max(1, raio)), 0.0, 1.0).astype(np.float32)


def tinta(img: np.ndarray) -> np.ndarray:
    """Máscara bool de tinta (texto e traços) da imagem: recortes densos em texto e o "fundo" do ADCD-Net."""
    return mascara_tinta(img).astype(bool)


def _janela(a: np.ndarray, x0: int, y0: int, lado: int) -> np.ndarray:
    sub = a[y0:y0 + lado, x0:x0 + lado]
    if sub.shape[0] == lado and sub.shape[1] == lado:
        return sub
    cheio = np.full((lado, lado) + a.shape[2:], 255 if a.ndim == 3 else 0, a.dtype)  # imagem menor: papel branco em volta
    cheio[:sub.shape[0], :sub.shape[1]] = sub
    return cheio


def recortar(arrays: dict, lado: int, rng: random.Random, p_edicao: float = 0.6, p_texto: float = 0.3) -> dict:
    """Recorte quadrado de ``lado`` px de todos os arrays (a mesma janela): com probabilidade ``p_edicao``
    centrado num pixel alterado (com um tremor de até lado/4), senão com ``p_texto`` num pixel de tinta, senão
    ao acaso. Imagem menor que o recorte é completada com papel branco (máscaras com 0). Devolve os arrays
    recortados mais ``janela`` = (x0, y0)."""
    H, W = arrays["editada"].shape[:2]
    lado = max(8, int(lado))

    def centro(m):
        ys, xs = np.nonzero(m)
        if not ys.size:
            return None
        i = rng.randrange(ys.size)
        j = lado // 4
        return int(xs[i]) + rng.randint(-j, j), int(ys[i]) + rng.randint(-j, j)

    u = rng.random()
    c = centro(arrays["mascara"]) if u < p_edicao else None
    if c is None and u < p_edicao + p_texto:
        c = centro(tinta(arrays["editada"]))
    if c is None:
        c = (rng.randrange(W), rng.randrange(H))
    x0 = min(max(0, c[0] - lado // 2), max(0, W - lado))
    y0 = min(max(0, c[1] - lado // 2), max(0, H - lado))
    out = {k: _janela(v, x0, y0, lado) for k, v in arrays.items() if isinstance(v, np.ndarray)}
    out["janela"] = (x0, y0)
    return out


def curriculo(amostras: list[dict], fracao: float) -> list[dict]:
    """As ``fracao`` (0..1) versões mais fáceis, do fácil ao difícil (``dificuldade``; sem medida conta 0,5) — o
    currículo do CLTD/DTD: comece pelo óbvio e vá soltando o que o perito não vê. ``fracao`` 1 = todas, ordenadas."""
    ordem = sorted(amostras, key=lambda a: (0.5 if a.get("dificuldade") is None else a["dificuldade"], a["nome"]))
    n = max(1, int(round(len(ordem) * min(max(float(fracao), 0.0), 1.0)))) if ordem else 0
    return ordem[:n]


def pesos(amostras: list[dict]) -> dict:
    """Taxa-base de pixel alterado do que o ``conjunto_torch`` serve (cada item é o par: a original, com máscara
    zero, e a editada; com ``equilibrio`` o total das autênticas pesa o mesmo), o peso do positivo para uma BCE
    ponderada ((1 − r)/r) e pesos de amostragem por família e por técnica (inverso da frequência).
    ``taxa_base_editadas`` = só as editadas. DocForge-Bench: com < 1% de pixel adulterado, o limiar 0,5 sem
    ponderação não funciona."""
    r_ed = float(np.mean([a["fracao_alterada"] for a in amostras])) if amostras else 0.0
    r = r_ed / 2  # metade do que é servido é a original

    def inverso(chave):
        c = Counter(v for a in amostras for v in a.get(chave) or [])
        return {k: round(len(amostras) / (len(c) * n), 4) for k, n in sorted(c.items())}

    return {"versoes": len(amostras), "originais": len({str(a["arquivos"]["original"]) for a in amostras}),
            "taxa_base": round(r, 6), "taxa_base_editadas": round(r_ed, 6),
            "peso_positivo": round((1 - r) / r, 2) if r > 0 else None,
            "por_familia": inverso("familias"), "por_tecnica": inverso("tecnicas")}


def equilibrio(amostras: list[dict]) -> list[float]:
    """Peso da original no par de cada amostra. A mesma original (``originais/<doc>``) serve as N versões do
    documento: sem peso, ela entra N vezes por época e o documento com mais versões domina as autênticas. Com
    este peso cada original distinta soma o mesmo, e o total das autênticas iguala o das adulteradas (peso 1)."""
    cont = Counter(str(a["arquivos"]["original"]) for a in amostras)
    if not cont:
        return []
    por_original = len(amostras) / len(cont)
    return [por_original / cont[str(a["arquivos"]["original"])] for a in amostras]


def aumentar(arrays: dict, rng: random.Random, cfg: dict | None = None, tabelas: list[dict] | None = None):
    """Aumento de canal no treino: sorteia um canal (``cfg`` de rotulador.canal.normalizar; padrão "misturado"
    sempre) e passa original, editada e máscaras pela MESMA geometria e codec; a máscara de ignorar é refeita.
    ``tabelas``: tabelas de quantização de aparelhos (canal.tabelas_de) para o JPEG. Aplique na página inteira,
    antes de recortar (a foto de celular enquadra a página). Devolve (arrays, theta); theta None = sem aumento
    desta vez (a fração ``prob`` da cfg)."""
    theta = canal.sortear(rng, cfg or {"perfil": "misturado", "prob": 1.0, "captura": "depois"}, tabelas, captura="depois")
    if theta is None:
        return arrays, None
    (o, e), (m, r), (c, t) = canal.aplicar([arrays["original"], arrays["editada"]], [arrays["mascara"], arrays["regiao"]],
                                           [arrays["classes"], arrays["tecnicas"]], theta, fase="depois")
    dif = np.abs(o.astype(np.int16) - e.astype(np.int16)).max(2)
    m &= r & (dif > 0)
    out = {**arrays, "original": o, "editada": e, "mascara": m, "regiao": r, "classes": canal.ajustar_rotulos(c, r),
           "tecnicas": canal.ajustar_rotulos(t, r), "ignorar": exportar.mascara_ignorar(m, r, dif > 0)}
    return out, theta


def montar_par(r: dict, raio_borda: int = 3, peso_original: float = 1.0, trocar: bool = False) -> dict:
    """O item do par em numpy a partir do recorte ``r``: "imagens" 2×L×L×3 uint8, "mascara"/"regiao"/"ignorar"/
    "borda" 2×L×L float32 e "classes"/"tecnicas" 2×L×L int64 (rótulos da original zerados), "adulterada" [0, 1] e
    "peso" [peso_original, 1]. ``trocar``: a editada vem primeiro (a posição no par deixa de dizer a classe)."""
    ordem = [1, 0] if trocar else [0, 1]

    def par(m, dtype):
        m = m.astype(dtype)
        return np.stack([np.zeros_like(m), m])[ordem]

    return {"imagens": np.stack([r["original"], r["editada"]])[ordem], "mascara": par(r["mascara"], np.float32),
            "regiao": par(r["regiao"], np.float32), "ignorar": par(r["ignorar"], np.float32),
            "borda": par(borda(r["regiao"], raio_borda), np.float32), "classes": par(r["classes"], np.int64),
            "tecnicas": par(r["tecnicas"], np.int64), "adulterada": np.array([0, 1], np.int64)[ordem],
            "peso": np.array([peso_original, 1.0], np.float32)[ordem]}


def conjunto_torch(amostras: list[dict], lado: int = 512, aumento: dict | None = None, tabelas: list[dict] | None = None,
                   p_edicao: float = 0.6, p_texto: float = 0.3, raio_borda: int = 3, equilibrar: bool = True,
                   ordem_aleatoria: bool = False):
    """Dataset do PyTorch (o torch é importado só aqui). Cada item é o MESMO recorte da original (rótulos zerados)
    e da editada — o lote pareado do SEED: "imagens" 2×3×L×L float 0..1, "mascara"/"regiao"/"ignorar"/"borda"
    2×L×L float, "classes"/"tecnicas" 2×L×L int64, "adulterada" 2 int64 (0 = original), "peso" 2 float (multiplica
    a perda de cada imagem) e "indice". ``equilibrar``: o peso da original vem de ``equilibrio`` (False = 1).
    ``ordem_aleatoria``: sorteia qual das duas vem primeiro (senão original, editada). ``aumento``: cfg do canal
    para ``aumentar`` (None = sem). O sorteio do recorte, da ordem e do canal vem do gerador do torch
    (torch.manual_seed e a semente por worker)."""
    import torch

    pesos_original = equilibrio(amostras) if equilibrar else [1.0] * len(amostras)

    class _Conjunto(torch.utils.data.Dataset):
        def __len__(self):
            return len(amostras)

        def __getitem__(self, i):
            rng = random.Random(int(torch.randint(0, 2**31 - 1, (1,)).item()))
            a = carregar(amostras[i])
            if aumento:
                a, _ = aumentar(a, rng, aumento, tabelas)
            r = recortar(a, lado, rng, p_edicao, p_texto)
            p = montar_par(r, raio_borda, pesos_original[i], trocar=ordem_aleatoria and rng.random() < 0.5)
            out = {k: torch.from_numpy(np.ascontiguousarray(v)) for k, v in p.items()}
            out["imagens"] = out["imagens"].permute(0, 3, 1, 2).float() / 255.0
            return {**out, "indice": i}

    return _Conjunto()
