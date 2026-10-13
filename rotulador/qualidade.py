"""Auditoria de QUALIDADE do dataset gerado: os rótulos batem com as imagens e as edições valem?

    python auditar_qualidade.py SAIDA [--split treino] [--ocr] [--gravar]

Alertas por versão (``prefixo: detalhe``):
  estrutura   arquivo faltando; máscara de tamanho diferente; pixel alterado fora da região; máscara de técnicas ou
              de classes diferente da região; o par difere longe da edição (canal.conferir_par, com ou sem canal);
              pixel em que original e editada diferem rotulado como negativo (fora da máscara e da faixa de ignorar);
  invisivel   menos de 12 pixels alterados na versão, ou uma edição com menos de 6;
  sem_mudanca edição de texto cujo texto novo é igual ao antigo (fora o retoque, que é assim de propósito);
  area        mais de 25% da página alterada sem técnica de página inteira;
  duplicada   outra versão do mesmo documento com a mesma técnica, o mesmo campo e o mesmo texto novo;
  reprovada   a revisão humana reprovou;
  pericia     acabamento forense com nota do perito acima de 0,8 (a perícia não escondeu nada);
  ocr         (só com --ocr, lento) o reconhecedor não lê o campo editado como o texto novo.
Com --gravar, cada anotação ganha ``auditoria_qualidade: {"alertas": [...], "em": data}`` (lista vazia quando está
limpa; até a 2.2 ia em ``qualidade``, por cima da qualidade do JPEG, que agora volta do nome do arquivo) e os
índices do split são refeitos; o datasheet conta as versões com alerta e ``treino.dados.listar(sem_alertas=True)``
as tira. O relatório sai em SAIDA/relatorio_qualidade.json e .txt. Uma versão com alerta não é apagada: quem decide
é quem revisa.

Alertas do dataset inteiro (``alertas_dataset``, também no datasheet): teste ou validação sem versão, poucos
documentos de origem, poucos modelos de conta ou emissores, o mesmo modelo de conta em splits diferentes.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import canal, catalogo, documentos, exportar
from .registro import SPLITS

MINIMO_VERSAO = 12  # pixels alterados abaixo disto: a edição não se vê
MINIMO_EDICAO = 6
AREA_MAXIMA = 0.25
NOTA_PERICIA = 0.8
MIN_DOCUMENTOS = 20  # documentos de origem distintos abaixo disto: o teste mede poucos papéis
MIN_MODELOS = 3  # modelos de conta (layout) ou emissores distintos abaixo disto: o detector decora o layout
LIMIAR_MODELO = 10  # dHash (de 256) até onde duas contas são o mesmo modelo
_RETOQUE = frozenset(t for j in catalogo.JEITOS if j["id"] == "retoque" for t in j["tecnicas"])


def _abrir(caminho: Path) -> np.ndarray:
    with Image.open(caminho) as im:
        return np.array(im)


def _chave_duplicata(doc: str, e: dict):
    if not e.get("texto_novo") or not e.get("tecnica"):
        return None
    return (doc, e["tecnica"], e.get("campo") or e.get("categoria") or "", e["texto_novo"])


def duplicadas(anots: list[dict]) -> dict[str, list[str]]:
    """nome da versão → outras versões do mesmo documento com uma edição igual (técnica, campo, texto novo)."""
    por_chave: dict[tuple, list[str]] = {}
    for a in anots:
        nome = f"{a['doc']}__v{int(a['versao']):02d}"
        for e in exportar.edicoes_de_anotacao(a):
            k = _chave_duplicata(a["doc"], e)
            if k is not None and nome not in por_chave.setdefault(k, []):
                por_chave[k].append(nome)
    out: dict[str, list[str]] = {}
    for nomes in por_chave.values():
        if len(nomes) > 1:
            for n in nomes:
                out.setdefault(n, []).extend(x for x in nomes if x != n)
    return out


def _ler_ocr(editada: np.ndarray, e: dict) -> bool | None:
    """True se o reconhecedor lê o campo editado como o texto novo; None quando não dá para julgar."""
    from . import ocr
    from .ops.tecnicas import lido_igual

    if not e.get("bbox") or not e.get("texto_novo"):
        return None
    t = catalogo.TECNICAS.get(e.get("tecnica") or "")
    if t is None or "texto" not in t.tipos or e["tecnica"] in _RETOQUE:
        return None
    x0, y0, x1, y1 = (int(v) for v in e["bbox"])
    H, W = editada.shape[:2]
    m = max(3, int(0.2 * (y1 - y0)))
    rec = editada[max(0, y0 - m):min(H, y1 + m), max(0, x0 - m):min(W, x1 + m)]
    if rec.size == 0:
        return None
    try:
        return lido_igual(ocr.reconhecer(rec), e["texto_novo"])
    except ocr.OCRIndisponivel:
        return None


def avaliar_versao(raiz_split: Path, anot: dict, ocr: bool = False) -> list[str]:
    """Os alertas de UMA versão (lista vazia = limpa). Não inclui ``duplicada`` (precisa das outras versões)."""
    al: list[str] = []
    arqs = {k: raiz_split / v for k, v in (anot.get("arquivos") or {}).items()}
    faltam = [k for k in ("original", "editada", "mascara", "mascara_regiao", "mascara_classes", "mascara_tecnicas")
              if k not in arqs or not arqs[k].is_file()]
    if faltam:
        return [f"estrutura: faltam {', '.join(faltam)}"]
    dpi = (anot.get("fonte") or {}).get("dpi_pdf") or documentos.DPI_PADRAO
    ed = documentos.carregar(arqs["editada"], 0, dpi)
    orig = documentos.carregar(arqs["original"], 0, dpi)
    H, W = ed.shape[:2]
    alt, reg, cla, tec = (_abrir(arqs[k]) for k in ("mascara", "mascara_regiao", "mascara_classes", "mascara_tecnicas"))
    for k, m in (("original", orig), ("mascara", alt), ("mascara_regiao", reg), ("mascara_classes", cla), ("mascara_tecnicas", tec)):
        if m.shape[:2] != (H, W):
            al.append(f"estrutura: {k} {m.shape[:2]} com tamanho diferente da editada {(H, W)}")
    if al:
        return al
    alt, reg = alt > 0, reg > 0
    if (alt & ~reg).any():
        al.append(f"estrutura: {int((alt & ~reg).sum())} pixels alterados fora da região")
    if not np.array_equal(tec > 0, reg):
        al.append("estrutura: máscara de técnicas diferente da região")
    if not np.array_equal(cla > 0, reg):
        al.append("estrutura: máscara de classes diferente da região")
    # com ou sem canal: o par só pode diferir perto da edição (a gravação com perda espalha até um bloco do JPEG)
    motivo = canal.conferir_par(orig, ed, reg, anot.get("canal"), extra=24 if anot.get("formato") != "png" else 0,
                                webp=anot.get("formato") == "webp" and (exportar.qualidade_saida(anot) or 100) < 100)
    if motivo:
        al.append(f"estrutura: o par difere longe da edição ({motivo})")
    if arqs.get("mascara_ignorar") and arqs["mascara_ignorar"].is_file():
        ign = _abrir(arqs["mascara_ignorar"])
        if ign.shape[:2] == (H, W):
            dif = np.abs(orig.astype(np.int16) - ed.astype(np.int16)).max(2) > 0
            n = int((dif & ~alt & ~(ign > 0)).sum())
            if n:
                al.append(f"estrutura: {n} pixels em que original e editada diferem estão fora da máscara e da faixa de "
                          f"ignorar (rotulados como não editados)")
    n_alt = int(alt.sum())
    if n_alt < MINIMO_VERSAO:
        al.append(f"invisivel: só {n_alt} pixels alterados na versão")
    eds = exportar.edicoes_de_anotacao(anot)
    pagina = any(catalogo.TECNICAS.get(e.get("tecnica") or "") and
                 catalogo.TECNICAS[e["tecnica"]].granularidade == "pagina" for e in eds)
    if n_alt / max(1, H * W) > AREA_MAXIMA and not pagina:
        al.append(f"area: {100 * n_alt / (H * W):.0f}% da página alterada sem técnica de página inteira")
    for e in eds:
        o = e.get("ordem")
        if e.get("pixels") is not None and int(e["pixels"]) < MINIMO_EDICAO:
            al.append(f"invisivel: edição {o} ({e.get('tecnica')}) com {e['pixels']} pixels")
        if e.get("texto_antigo") and e.get("texto_novo") and e["texto_antigo"] == e["texto_novo"] \
                and e.get("tecnica") not in _RETOQUE:
            al.append(f"sem_mudanca: edição {o} ({e.get('tecnica')}) mantém o texto '{e['texto_novo']}'")
        nota = (e.get("perito") or {}).get("nota")
        if (e.get("pericia") == "forense") and nota is not None and nota > NOTA_PERICIA:
            al.append(f"pericia: edição {o} forense com nota do perito {nota:.2f}")
        if ocr:
            leu = _ler_ocr(ed, e)
            if leu is False:
                al.append(f"ocr: edição {o} ({e.get('tecnica')}) não é lida como '{e.get('texto_novo')}'")
    if (anot.get("revisao") or {}).get("estado") == "reprovada":
        r = anot["revisao"]
        al.append("reprovada: " + ", ".join(r.get("motivos") or []) + (f" ({r['comentario']})" if r.get("comentario") else ""))
    return al


def dhash(img: np.ndarray, lado: int = 16) -> np.ndarray:
    """Hash de diferença (lado x lado bits) da página em cinza: contas do mesmo modelo ficam a poucos bits."""
    cinza = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    peq = cv2.resize(cinza, (lado + 1, lado), interpolation=cv2.INTER_AREA).astype(np.int16)
    return (peq[:, 1:] > peq[:, :-1]).ravel()


def grupos_de_modelo(hashes: dict, limiar: int = LIMIAR_MODELO) -> list[list]:
    """Chaves de ``hashes`` agrupadas por modelo de conta: dHash a até ``limiar`` bits, por ligação simples (A~B e
    B~C põem A, B e C no mesmo modelo). Do maior grupo para o menor."""
    pai = {k: k for k in hashes}

    def raiz(k):
        while pai[k] != k:
            pai[k] = pai[pai[k]]
            k = pai[k]
        return k
    for a, b in combinations(hashes, 2):
        if int((hashes[a] != hashes[b]).sum()) <= limiar:
            pai[raiz(a)] = raiz(b)
    grupos = defaultdict(list)
    for k in hashes:
        grupos[raiz(k)].append(k)
    return sorted(grupos.values(), key=lambda g: (-len(g), sorted(g)))


def alertas_dataset(raiz_saida: Path, anots: dict[str, list[dict]] | None = None, limiar: int = LIMIAR_MODELO) -> dict:
    """Alertas do dataset inteiro: teste ou validação sem versão; menos de MIN_DOCUMENTOS documentos de origem
    distintos; menos de MIN_MODELOS modelos de conta (dHash da original de cada documento, como o
    auditar_vazamento.py) ou emissores (CNPJ lido pela última auditoria de vazamento); o mesmo modelo em splits
    diferentes. ``anots``: {split: anotações} já lidas (senão lê)."""
    raiz_saida = Path(raiz_saida)
    if anots is None:
        anots = {sp: exportar._anotacoes(raiz_saida / sp) for sp in SPLITS}
    al = [f"{sp} vazio: nenhuma versão para avaliar" for sp in ("teste", "validacao") if not anots.get(sp)]
    origem: dict[str, tuple] = {}  # doc -> (split, original, dpi); a original comum (sem __v, antes do canal) vence
    for sp in SPLITS:
        for a in anots.get(sp) or []:
            rel = (a.get("arquivos") or {}).get("original")
            if rel and (a["doc"] not in origem or ("__v" in Path(origem[a["doc"]][1]).name and "__v" not in Path(rel).name)):
                origem[a["doc"]] = (sp, rel, (a.get("fonte") or {}).get("dpi_pdf") or documentos.DPI_PADRAO)
    if len(origem) < MIN_DOCUMENTOS:
        al.append(f"só {len(origem)} documento(s) de origem distinto(s) (< {MIN_DOCUMENTOS}): as métricas medem poucos "
                  f"papéis; o intervalo de confiança por documento fica largo")
    hashes = {}
    for doc, (sp, rel, dpi) in origem.items():
        try:
            hashes[doc] = dhash(documentos.carregar(raiz_saida / sp / rel, 0, dpi))
        except Exception:  # noqa: BLE001 - original ilegível: fica fora da contagem de modelos
            continue
    grupos = grupos_de_modelo(hashes, limiar)
    if hashes and len(grupos) < MIN_MODELOS:
        al.append(f"só {len(grupos)} modelo(s) de conta distinto(s) (dHash <= {limiar}/256; < {MIN_MODELOS}): o "
                  f"detector pode decorar o layout em vez de aprender a adulteração")
    entre = []
    for g in grupos:
        cont = Counter(origem[d][0] for d in g)
        if len(cont) > 1:
            por = {sp: cont[sp] for sp in SPLITS if cont[sp]}
            entre.append(por)
            al.append(f"mesmo modelo de conta (dHash <= {limiar}/256) em splits diferentes "
                      f"({', '.join(f'{sp}: {n}' for sp, n in por.items())}): o teste mede um layout que o treino já "
                      f"viu; `auditar_vazamento.py --sugerir-split` propõe outra divisão")
    emissores = None
    try:
        vazamento = json.loads((raiz_saida / "relatorio_vazamento.json").read_text(encoding="utf-8"))
        emissores = len(vazamento.get("emissores") or {})
    except (OSError, ValueError):
        pass
    if emissores and emissores < MIN_MODELOS:  # 0 = nenhum CNPJ lido: não diz nada
        al.append(f"só {emissores} emissor(es) (CNPJ lido pela última auditoria de vazamento; < {MIN_MODELOS})")
    return {"alertas": al, "documentos": len(origem), "modelos": len(grupos), "emissores": emissores,
            "modelos_entre_splits": entre}


def alertas_de(anot: dict) -> list[str]:
    """Alertas gravados por ``auditar --gravar`` (``auditoria_qualidade``; até a 2.2, em ``qualidade``)."""
    for k in ("auditoria_qualidade", "qualidade"):
        if isinstance(anot.get(k), dict):
            return anot[k].get("alertas") or []
    return []


def _gravar_json(caminho: Path, dados: dict) -> None:
    tmp = caminho.with_name(caminho.name + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, caminho)


def auditar(raiz_saida: Path, splits=None, ocr: bool = False, gravar: bool = False, log=print) -> dict:
    """Audita os splits (todos por padrão) e grava SAIDA/relatorio_qualidade.json e .txt."""
    raiz_saida = Path(raiz_saida)
    splits = [s for s in (splits or SPLITS) if s in SPLITS]
    resultado = {"data": time.strftime("%Y-%m-%dT%H:%M:%S"), "ocr": ocr, "versoes": 0, "com_alerta": 0,
                 "por_tipo": Counter(), "alertas": {}}
    for sp in splits:
        raiz_split = raiz_saida / sp
        pasta = raiz_split / "anotacoes"
        anots = []
        for arq in sorted(pasta.glob("*.json")) if pasta.is_dir() else []:
            try:
                anots.append((arq, json.loads(arq.read_text(encoding="utf-8"))))
            except ValueError:
                resultado["alertas"][f"{sp}/{arq.stem}"] = ["estrutura: anotação ilegível"]
                resultado["por_tipo"]["estrutura"] += 1
        dup = duplicadas([a for _, a in anots])
        tocou = False
        for arq, a in anots:
            nome = f"{a['doc']}__v{int(a['versao']):02d}"
            try:
                al = avaliar_versao(raiz_split, a, ocr=ocr)
            except Exception as e:  # noqa: BLE001 - uma versão corrompida não para a auditoria
                al = [f"estrutura: não foi possível conferir ({e})"]
            if nome in dup:
                al.append("duplicada: mesma edição em " + ", ".join(sorted(set(dup[nome]))))
            resultado["versoes"] += 1
            if al:
                resultado["com_alerta"] += 1
                resultado["alertas"][f"{sp}/{nome}"] = al
                for x in al:
                    resultado["por_tipo"][x.split(":")[0]] += 1
            if gravar:
                novo = {"alertas": al, "em": resultado["data"]}
                if a.get("auditoria_qualidade") != novo or isinstance(a.get("qualidade"), dict):
                    a["qualidade"] = exportar.qualidade_saida(a)  # a 2.2 gravava os alertas por cima dela
                    a["auditoria_qualidade"] = novo
                    _gravar_json(arq, a)
                    tocou = True
        if tocou:
            exportar.reconstruir_indices(raiz_saida, sp)
        log(f"{sp}: {len(anots)} versões, {sum(1 for k in resultado['alertas'] if k.startswith(sp + '/'))} com alerta")
    resultado["por_tipo"] = dict(resultado["por_tipo"].most_common())
    resultado["dataset"] = alertas_dataset(raiz_saida)
    raiz_saida.mkdir(parents=True, exist_ok=True)
    (raiz_saida / "relatorio_qualidade.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    (raiz_saida / "relatorio_qualidade.txt").write_text(texto(resultado), encoding="utf-8")
    return resultado


def texto(r: dict) -> str:
    L = [f"Auditoria de qualidade ({r['data']}{', com OCR' if r.get('ocr') else ''}): {r['versoes']} versões, "
         f"{r['com_alerta']} com alerta"]
    if r["por_tipo"]:
        L.append("por tipo: " + ", ".join(f"{k} {v}" for k, v in r["por_tipo"].items()))
    for nome, al in r["alertas"].items():
        L.append(f"- {nome}: " + "; ".join(al))
    for al in (r.get("dataset") or {}).get("alertas") or []:
        L.append(f"- dataset: {al}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Audita a qualidade dos rótulos e das edições de uma saída do rotulador.")
    ap.add_argument("saida")
    ap.add_argument("--split", action="append", help="só este split (repetível)")
    ap.add_argument("--ocr", action="store_true", help="relê cada campo editado pelo OCR (lento)")
    ap.add_argument("--gravar", action="store_true", help="grava os alertas nas anotações e refaz os índices")
    a = ap.parse_args(argv)
    r = auditar(Path(a.saida), a.split, ocr=a.ocr, gravar=a.gravar)
    print(texto(r))
    return 1 if r["com_alerta"] else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
