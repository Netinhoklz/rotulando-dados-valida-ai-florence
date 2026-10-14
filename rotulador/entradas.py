"""Variedade dos documentos de ENTRADA (dados/<split>/): o que o dataset tem e o que falta, antes de gerar.

O limite de qualidade que nenhuma técnica corrige é a origem: com poucas contas, de poucos emissores e só de um tipo
(todas PDF, ou todas fotos), o modelo aprende o layout e o papel daquelas contas em vez da adulteração. Este relatório
conta, por split, os documentos, os arquivos, como cada um chegou (PDF digital, foto de celular, scan, imagem sem
compressão) e quantos modelos de conta distintos há (dHash da 1ª página, como ``qualidade``), e diz em português o
que juntar a seguir.

    variedade(registro) -> {"splits": {...}, "total": {...}, "alertas": [...], "sugestoes": [...]}
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from PIL import Image

from . import qualidade
from .registro import SPLITS

# como o arquivo chegou, pelo formato e pelos metadados da câmera (EXIF Make/Model)
TIPOS = {"pdf": "PDF digital", "foto": "foto de celular/câmera", "jpeg": "JPEG (scan ou imagem recebida)",
         "imagem": "imagem sem compressão (PNG, print de tela)"}


def tipo_do_arquivo(caminho: Path) -> str:
    suf = caminho.suffix.lower()
    if suf == ".pdf":
        return "pdf"
    try:
        with Image.open(caminho) as im:
            exif = im.getexif() if hasattr(im, "getexif") else {}
            camera = bool(exif.get(0x010F) or exif.get(0x0110))  # Make / Model
            if im.format in ("JPEG", "MPO"):
                return "foto" if camera else "jpeg"
    except OSError:
        pass
    return "imagem"


def variedade(registro, hashes: bool = True) -> dict:
    """O relatório. ``hashes=False`` pula os modelos de conta (precisa abrir cada 1ª página; é o passo lento)."""
    por_split, hs, onde = {}, {}, {}
    for sp in SPLITS:
        itens = list(registro.por_split.get(sp, []))
        arquivos = {it.caminho for it in itens}
        tipos = Counter(tipo_do_arquivo(Path(c)) for c in arquivos)
        por_split[sp] = {"documentos": len(itens), "arquivos": len(arquivos),
                         "tipos": {TIPOS[k]: v for k, v in tipos.most_common()}}
        if hashes:
            for it in itens:
                if it.pagina:
                    continue
                try:
                    hs[it.doc] = qualidade.dhash(registro.carregar(it.doc))
                    onde[it.doc] = sp
                except Exception:  # noqa: BLE001 - arquivo ilegível fica fora da contagem
                    continue
    grupos = qualidade.grupos_de_modelo(hs) if hashes else []
    total_docs = sum(v["documentos"] for v in por_split.values())
    total_tipos = Counter()
    for v in por_split.values():
        total_tipos.update(v["tipos"])
    alertas, sugestoes = [], []
    for sp in ("teste", "validacao"):
        if not por_split[sp]["documentos"]:
            alertas.append(f"{sp} sem nenhum documento: não há como medir o modelo em papel que ele não viu")
            sugestoes.append(f"coloque contas em dados/{sp}/ (de emissores e modelos que NÃO estão no treino)")
    if total_docs < qualidade.MIN_DOCUMENTOS:
        alertas.append(f"só {total_docs} documento(s) de origem (o mínimo útil é {qualidade.MIN_DOCUMENTOS})")
        sugestoes.append("junte mais contas de verdade (luz, água, gás, telefone, internet), mesmo poucas de cada "
                         "tipo; anonimize depois")
    if hashes and hs and len(grupos) < qualidade.MIN_MODELOS:
        alertas.append(f"só {len(grupos)} modelo(s) de conta distinto(s): o detector pode decorar o layout")
        sugestoes.append("prefira contas de OUTRAS concessionárias e de outros estados a mais páginas da mesma conta")
    entre = []
    for g in grupos:
        sps = sorted({onde[d] for d in g}, key=SPLITS.index)
        if len(sps) > 1:
            entre.append(sps)
    if entre:
        alertas.append(f"{len(entre)} modelo(s) de conta aparece(m) em mais de um split: o teste mede um layout "
                       "que o treino já viu")
        sugestoes.append("deixe cada modelo de conta (o mesmo emissor e layout) num split só; "
                         "`auditar_vazamento.py --sugerir-split` propõe a divisão")
    if total_docs:
        if not total_tipos.get(TIPOS["foto"]) and not total_tipos.get(TIPOS["jpeg"]):
            sugestoes.append("nenhuma foto nem scan: adicione contas fotografadas com celular e escaneadas (o detector "
                             "precisa ver papel de verdade, não só PDF)")
        if not total_tipos.get(TIPOS["pdf"]):
            sugestoes.append("nenhum PDF digital: adicione segundas vias baixadas do site/app das concessionárias")
    return {"splits": por_split, "total": {"documentos": total_docs, "tipos": dict(total_tipos),
                                            "modelos": len(grupos) if hashes else None},
            "modelos_entre_splits": entre, "alertas": alertas, "sugestoes": sugestoes}
