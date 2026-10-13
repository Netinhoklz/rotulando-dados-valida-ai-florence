"""Edição VETORIAL do PDF: a fraude sem rastro de pixel.

As contas chegam como PDF com camada de texto, e um editor de PDF (Acrobat, Foxit, iLovePDF, Sejda...)
troca o texto no arquivo: apaga as palavras do campo (redação) e escreve o valor novo com a fonte
embutida do próprio arquivo ou, quando ela não está embutida ou não tem as letras, com a fonte de
reserva do editor (Helvetica, Times, Courier — o "Adobe Sans MM"). Rasterizada de novo, a página não
tem nenhuma pista de pixel além da tipografia (fonte parecida mas não igual, espaçamento, alinhamento).

É a ponta "indetectável": serve para medir o piso de falso negativo de um detector de pixel e para
treinar a cabeça que lê o texto. Sai rotulada como técnica própria (``campo.reescrever_pdf``) e com
``fornecedores.texto = "pdf-vetorial"``, para pesar menos (ou nada) no treino do detector de pixel.

Só vale em documento cuja origem é um PDF com texto (``Contexto.vetorial``) e no documento como foi
rasterizado (nunca depois de uma captura "antes da edição", que já é foto).
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

try:
    import pymupdf
except ModuleNotFoundError:  # pragma: no cover - opcional
    pymupdf = None

from . import documentos, glifos
from .sessao import ErroOperacao, Resultado

_cache_texto: dict[tuple, bool] = {}
_lock = threading.Lock()
# fontes de reserva do editor (Base-14): (normal, negrito) por família
_RESERVA = {"sans": ("helv", "hebo"), "serif": ("tiro", "tibo"), "mono": ("cour", "cobo")}
_NOMES_RESERVA = {"helv": "Helvetica", "hebo": "Helvetica-Bold", "tiro": "Times-Roman", "tibo": "Times-Bold",
                  "cour": "Courier", "cobo": "Courier-Bold"}


def tem_texto(caminho, pagina: int) -> bool:
    """A página do PDF tem camada de texto (não é só uma imagem escaneada)?"""
    if pymupdf is None or Path(caminho).suffix.lower() != ".pdf":
        return False
    chave = (str(caminho), int(pagina))
    with _lock:
        if chave in _cache_texto:
            return _cache_texto[chave]
    try:
        with pymupdf.open(caminho) as doc:
            ok = len(doc[pagina].get_text("words")) >= 5
    except Exception:  # noqa: BLE001 - PDF estranho: não é vetorial
        ok = False
    with _lock:
        _cache_texto[chave] = ok
    return ok


def _cobre(palavra, rect) -> bool:
    inter = pymupdf.Rect(palavra) & rect
    return not inter.is_empty and inter.get_area() >= 0.5 * max(1e-6, pymupdf.Rect(palavra).get_area())


def _span_em(page, rect):
    """O span (fonte, tamanho, cor, origem) com mais área dentro de ``rect``."""
    melhor, area = None, 0.0
    for bloco in page.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for span in linha.get("spans", []):
                inter = pymupdf.Rect(span["bbox"]) & rect
                if not inter.is_empty and inter.get_area() > area:
                    melhor, area = span, inter.get_area()
    return melhor


def _familia(nome: str, flags: int) -> str:
    n = nome.lower()
    if flags & 8 or "cour" in n or "mono" in n or "consol" in n:
        return "mono"
    if flags & 4 or any(s in n for s in ("times", "serif", "georgia", "garamond", "book", "cambria")):
        return "serif"
    return "sans"


def _fonte_embutida(pdf, page, nome: str):
    """Bytes da fonte embutida com esse nome (ignora o prefixo de subconjunto ABCDEF+), ou None."""
    alvo = nome.split("+", 1)[-1].lower()
    for xref, _ext, _tipo, base, *_ in page.get_fonts(full=True):
        if base.split("+", 1)[-1].lower() != alvo:
            continue
        try:
            _, ext, _, conteudo = pdf.extract_font(xref)
        except Exception:  # noqa: BLE001
            continue
        if conteudo and ext in ("ttf", "otf", "cff", "ttc"):
            return bytes(conteudo)
    return None


def _render(page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width * 3]
    return np.ascontiguousarray(a.reshape(pix.height, pix.width, 3))


def reescrever(c, novo: str) -> Resultado:
    """Troca o texto do campo ``c`` (rotulador.ops.tecnicas._C) na camada de texto do PDF e rasteriza."""
    doc = c.documento
    if pymupdf is None:
        raise ErroOperacao("instale pymupdf para editar o PDF")
    if doc is None or not getattr(doc, "vetorial", False):
        raise ErroOperacao("edição vetorial só em documento que é PDF com camada de texto")
    novo = str(novo or "").strip()
    if not novo:
        raise ErroOperacao("sem valor novo para escrever")
    dpi = int(getattr(doc, "dpi", documentos.DPI_PADRAO))
    esc = 72.0 / dpi
    x0, y0, x1, y1 = c.caixa
    rect = pymupdf.Rect(x0 * esc, y0 * esc, x1 * esc, y1 * esc)
    fonte_opcao = c.opcao("fonte_pdf")
    alin = c.opcao("alinhamento")
    base = c.base if c.base is not None else c.img
    with pymupdf.open(doc.item.caminho) as pdf:
        page = pdf[doc.item.pagina]
        palavras = [w for w in page.get_text("words") if _cobre(w[:4], rect)]
        if not palavras:
            raise ErroOperacao("nenhuma palavra da camada de texto do PDF cai na caixa do campo")
        span = _span_em(page, pymupdf.Rect(palavras[0][:4]))
        if span is None:
            raise ErroOperacao("não achei a fonte do texto no PDF")
        tamanho = float(span["size"])
        flags = int(span.get("flags", 0))
        nome_fonte = str(span.get("font", ""))
        cor_int = int(span.get("color", 0))
        cor = ((cor_int >> 16) & 255) / 255.0, ((cor_int >> 8) & 255) / 255.0, (cor_int & 255) / 255.0
        apagar = pymupdf.Rect(palavras[0][:4])
        for w in palavras[1:]:
            apagar |= pymupdf.Rect(w[:4])
        apagar = pymupdf.Rect(apagar.x0 - 0.5, apagar.y0 - 0.5, apagar.x1 + 0.5, apagar.y1 + 0.5)
        papel = tuple(float(v) / 255.0 for v in glifos.cor_papel(c.img, c.caixa))
        page.add_redact_annot(apagar, fill=papel)
        try:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        except TypeError:  # pymupdf antigo
            page.apply_redactions()
        fontname, usada, largura = None, None, 0.0
        if fonte_opcao == "embutida":
            conteudo = _fonte_embutida(pdf, page, nome_fonte)
            if conteudo is not None:
                try:
                    f = pymupdf.Font(fontbuffer=conteudo)
                    if all(f.has_glyph(ord(ch)) for ch in novo if not ch.isspace()):
                        page.insert_font(fontname="FRDV", fontbuffer=conteudo)
                        fontname, usada = "FRDV", nome_fonte.split("+", 1)[-1]
                        largura = f.text_length(novo, fontsize=tamanho)
                except Exception:  # noqa: BLE001 - fonte que o MuPDF não carrega: cai na reserva
                    fontname = None
        if fontname is None:  # o editor sem a fonte: a reserva (Helvetica/Times/Courier), a pista do Acrobat
            negrito = bool(flags & 16) or "bold" in nome_fonte.lower()
            fontname = _RESERVA[_familia(nome_fonte, flags)][1 if negrito else 0]
            usada = _NOMES_RESERVA[fontname]
            largura = pymupdf.get_text_length(novo, fontname=fontname, fontsize=tamanho)
            c.escolhas["fonte_pdf"] = "substituta"
        x = apagar.x1 - 0.5 - largura if alin == "direita" else float(palavras[0][0])
        y_base = float(span["origin"][1])
        page.insert_text(pymupdf.Point(x, y_base), novo, fontsize=tamanho, fontname=fontname, color=cor)
        render = _render(page, dpi)
    if render.shape != base.shape:
        raise ErroOperacao("a página rasterizada não tem o tamanho do documento (DPI diferente?)")
    dif = np.abs(render.astype(np.int16) - base.astype(np.int16)).max(2) > 0
    # só o que mudou perto do campo: a redação e o texto novo (+ folga para o antialias)
    tx0, tx1 = min(apagar.x0, x), max(apagar.x1, x + largura)
    ty0, ty1 = min(apagar.y0, y_base - tamanho), max(apagar.y1, y_base + 0.35 * tamanho)
    H, W = base.shape[:2]
    jx0, jy0 = max(0, int(tx0 / esc) - 4), max(0, int(ty0 / esc) - 4)
    jx1, jy1 = min(W, int(tx1 / esc) + 5), min(H, int(ty1 / esc) + 5)
    janela = np.zeros((H, W), bool)
    janela[jy0:jy1, jx0:jx1] = True
    pegada = dif & janela
    if not pegada.any():
        raise ErroOperacao("a edição vetorial não mudou nenhum pixel")
    if (dif & ~janela).sum() > 0.002 * dif.size:
        raise ErroOperacao("a rasterização mudou pixels longe do campo (o PDF não renderiza igual duas vezes)")
    ys, xs = np.nonzero(pegada)
    ry0, ry1, rx0, rx1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    c.fornecedores.update(texto="pdf-vetorial", fonte=usada, apagar="redacao-pdf")
    meta = c.meta(novo, [{"i": 0, "de": c.texto, "para": novo, "tipo": "campo"}],
                  f"Camada de texto do PDF editada: '{c.texto}' → '{novo}' com a fonte "
                  f"{'embutida ' if fontname == 'FRDV' else 'de reserva do editor '}{usada} (tamanho {tamanho:.1f} pt), "
                  f"alinhado pela {alin}; a página foi rasterizada de novo (sem rastro de pixel).",
                  fonte=usada, vetorial=True)
    return Resultado(ry0, ry1, rx0, rx1, render[ry0:ry1, rx0:rx1].copy(), pegada[ry0:ry1, rx0:rx1].copy(), meta=meta)
