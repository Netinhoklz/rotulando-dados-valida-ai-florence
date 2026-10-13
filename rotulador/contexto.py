"""O que as técnicas automáticas sabem de um documento além dos pixels.

  - ``linhas``: OCR do ORIGINAL (em cache em saida/_cache/ocr, como a detecção de campos);
  - ``campos``: campos detectados (rotulador.campos);
  - ``inventario()``: letras do documento, para copiar um glifo de outro lugar;
  - ``jpeg``: a compressão do arquivo de origem (tabelas, subamostragem, orientação), quando é JPEG;
  - ``doadores(rng)``: outros documentos do MESMO split, para colagem (splicing). A regra
    anti-vazamento é verificada aqui também: um doador de outro split levanta erro.
"""

from __future__ import annotations

import math
import random
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image, JpegImagePlugin

from . import campos as mod_campos
from . import documentos, glifos, ocr, vetorial
from .registro import Item, Registro
from .sessao import ErroOperacao

_imagens: OrderedDict[str, np.ndarray] = OrderedDict()
_img_lock = threading.Lock()


def _imagem(registro: Registro, doc: str) -> np.ndarray:
    """Imagem de um doador (as últimas ficam em memória: o lote pede o mesmo doador várias vezes)."""
    with _img_lock:
        if doc in _imagens:
            _imagens.move_to_end(doc)
            return _imagens[doc]
    img = registro.carregar(doc)
    img.setflags(write=False)
    with _img_lock:
        _imagens[doc] = img
        while len(_imagens) > 4:
            _imagens.popitem(last=False)
    return img


def cache_ocr(raiz_saida: Path, sha_pixels: str) -> Path:
    return Path(raiz_saida) / "_cache" / "ocr" / f"{sha_pixels[:32]}.json"


class Contexto:
    def __init__(self, item: Item, img: np.ndarray, raiz_saida: Path, registro: Registro | None = None,
                 dpi: int | None = None, derivado: bool = False):
        self.item, self.img = item, img
        self.doc, self.split = item.doc, item.split
        self.raiz_saida = Path(raiz_saida)
        self.registro = registro
        self.dpi = int(dpi if dpi is not None else getattr(registro, "dpi", documentos.DPI_PADRAO))
        self._derivado = derivado  # a imagem já passou por uma captura (foto/scan) antes da edição
        self._chave_inventario = item.sha_pixels
        self._linhas = None
        self._campos = None
        self._vetorial = None
        self._jpeg = False  # False = ainda não lido (None é resposta válida: não é JPEG)

    @property
    def linhas(self) -> list[dict]:
        if self._linhas is None:
            self._linhas = ocr.ler(self.img, cache_ocr(self.raiz_saida, self.item.sha_pixels))
        return self._linhas

    @property
    def campos(self) -> list[dict]:
        if self._campos is None:
            self._campos = mod_campos.detectar(self.img, self.linhas)
        return self._campos

    @property
    def vetorial(self) -> bool:
        """O documento é um PDF com camada de texto, ainda como foi rasterizado (edição vetorial possível)."""
        if self._derivado:
            return False
        if self._vetorial is None:
            self._vetorial = vetorial.tem_texto(self.item.caminho, self.item.pagina)
        return self._vetorial

    @property
    def jpeg(self) -> dict | None:
        """A compressão do arquivo de origem quando ele é JPEG: {"qtables", "subsampling" (0 = 4:4:4, 1 = 4:2:2,
        2 = 4:2:0, -1 = cinza ou outra), "modo" (RGB/L), "orientacao" (EXIF 1-8), "tamanho" (largura, altura
        do arquivo ARMAZENADO, onde a grade 8x8/MCU começa em 0)}. A edição é requantizada com isso
        (tecnicas._requantizar): sem, a região editada era a única área da página fora da grade do JPEG. None
        para PDF/PNG, captura derivada (a imagem já não é a do arquivo) ou arquivo ilegível."""
        if self._jpeg is False:
            self._jpeg = None
            if not self._derivado and self.item.pagina == 0:
                try:
                    with Image.open(self.item.caminho) as im:
                        if im.format == "JPEG" and im.mode in ("RGB", "L") and im.quantization:
                            ori = int(im.getexif().get(0x0112, 1) or 1)
                            larg, alt = im.size
                            H, W = self.img.shape[:2]
                            if (H, W) == ((larg, alt) if ori in (5, 6, 7, 8) else (alt, larg)):
                                self._jpeg = {"qtables": [list(t) for _, t in sorted(im.quantization.items())],
                                              "subsampling": JpegImagePlugin.get_sampling(im), "modo": im.mode,
                                              "orientacao": ori if 1 <= ori <= 8 else 1, "tamanho": (larg, alt)}
                except (OSError, ValueError, SyntaxError):
                    pass
        return self._jpeg

    def inventario(self) -> glifos.Inventario:
        return glifos.inventario(self._chave_inventario, self.img, self.linhas)

    def derivado(self, img: np.ndarray, mapear_pontos, tag: str) -> "Contexto":
        """O mesmo documento depois de uma captura ANTES da edição (foto, scan): o OCR não roda de novo; as
        caixas das linhas e dos campos são levadas pela mesma geometria (``mapear_pontos``: lista de (x, y)
        → lista de (x, y)) e a caixa de cada campo é apertada na tinta da imagem nova."""
        novo = Contexto(self.item, img, self.raiz_saida, self.registro, dpi=self.dpi, derivado=True)
        novo.doc = f"{self.doc}@{tag}"  # chave de cache própria (fonte, apagado): a imagem é outra
        novo._chave_inventario = f"{self.item.sha_pixels}@{tag}"
        # o documento limpo e a caixa de cada campo nele: a fonte é identificada no limpo (na foto, uma
        # negrito binarizada vira regular) e só o tamanho e a posição são medidos na imagem nova
        novo.origem = self
        novo.caixas_origem = {f.get("id"): list(f["caixa"]) for f in self.campos}
        H, W = img.shape[:2]

        def mapear(caixa):
            x0, y0, x1, y1 = caixa
            pts = mapear_pontos([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            cx0, cy0 = max(0, int(math.floor(min(xs)))), max(0, int(math.floor(min(ys))))
            cx1, cy1 = min(W, int(math.ceil(max(xs)))), min(H, int(math.ceil(max(ys))))
            if cx1 - cx0 < 2 or cy1 - cy0 < 2:
                return None
            # caixa cortada pela borda (recorte parcial da página): um campo pela metade não é editável
            inteira = max(1.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))
            return [cx0, cy0, cx1, cy1] if (cx1 - cx0) * (cy1 - cy0) >= 0.9 * inteira else None

        novo._linhas = [{**l, "caixa": cx} for l in self.linhas if (cx := mapear(l["caixa"]))]
        campos = []
        for f in self.campos:
            cx = mapear(f["caixa"])
            if cx is None:
                continue
            if f.get("tipo", "texto") == "texto":
                cx = ocr.caixa_tinta(img, cx, 0)
            campos.append({**f, "caixa": cx, "caixa_ocr": mapear(f.get("caixa_ocr", f["caixa"])) or cx})
        novo._campos = campos
        return novo

    def doadores(self, rng: random.Random, limite: int = 4):
        """Outros documentos do MESMO split, em ordem sorteada (carregados sob demanda)."""
        if self.registro is None:
            return
        itens = [it for it in self.registro.por_split[self.split] if it.doc != self.doc]
        rng.shuffle(itens)
        for it in itens[:limite]:
            if it.split != self.split:  # nunca: o registro separa por split, mas a regra fica explícita
                raise ErroOperacao("doador de outro split: proibido")
            yield Contexto(it, _imagem(self.registro, it.doc), self.raiz_saida, None)
