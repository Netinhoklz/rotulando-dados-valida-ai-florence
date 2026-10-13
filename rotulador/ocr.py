"""Leitura do texto do documento (OCR): detector PP-OCRv6 small + reconhecedor latino PP-OCRv5 (ONNX, CPU).

Os modelos são os ONNX oficiais da PaddlePaddle no Hugging Face, baixados na primeira leitura para ``MODELOS``
(~18 MB) e rodados pelo RapidOCR do projeto (só o detector e o reconhecedor são trocados). Lê acentos, cedilha e
espaços ("JOSÉ CARLOS", "MÊS REFERÊNCIA") e as caixas cobrem a tinta toda (com o acento). Sem rede ou sem o
modelo, cai no OCR antigo (``MOTOR_RESERVA``: o PP-OCRv3 chinês que vem no rapidocr_onnxruntime), que come os
espaços ("MARIADOSSANTOS01", recuperados pelos vãos de tinta em ``recuperar_espacos``) e tira acentos.
Benchmark nos documentos do projeto (sintéticos com verdade conhecida e a conta real): ver o README.

O resultado fica em cache em disco por documento (hash dos pixels), com o nome do motor que leu: cada página
só é lida uma vez, inclusive entre execuções do servidor, e a leitura de outro motor é refeita.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .selecao import mascara_tinta

_lock = threading.Lock()  # o motor ONNX não é garantido seguro entre threads
_lock_carga = threading.Lock()
_motor = None
_nome_motor: str | None = None

# Modelos ONNX oficiais (PaddlePaddle no Hugging Face), num commit fixo e conferidos pelo sha256. Ficam na pasta do
# projeto (fora do git): vão junto se o projeto mudar de disco e somem com ele; os testes usam os mesmos.
MODELOS = Path(__file__).resolve().parents[1] / "modelos" / "ocr"
_HF = "https://huggingface.co/PaddlePaddle/{repo}/resolve/{rev}/{arq}"
_DET = {"repo": "PP-OCRv6_small_det_onnx", "rev": "28fe5895c24fd108c19eb3e8479f4ab385fbfc62",
        "sha256": "d73e0058b7a8086bbd57f3d10b8bcd4ff95363f67e06e2762b5e814fe9c9410e", "mb": 10}
_REC = {"repo": "latin_PP-OCRv5_mobile_rec_onnx", "rev": "89d3a50e2c27e2e7cceeab0e944c25c807d5db4f",
        "sha256": "7888113072263cb471b93f66dd5e2ad70548dc526fa1ace760d0d973dd121498", "mb": 8}
# nome gravado no cache: muda quando o modelo ou o pré/pós-processamento muda (a leitura antiga é refeita)
MOTOR_PADRAO = "PP-OCRv6_small_det+latin_PP-OCRv5_mobile_rec/2"
MOTOR_RESERVA = "rapidocr PP-OCRv3 ch"  # o cache de antes (lista pura) é dele


class OCRIndisponivel(RuntimeError):
    pass


def _baixar(modelo: dict) -> Path:
    """Pasta com o inference.onnx e o inference.yml do modelo; baixa do Hugging Face na primeira vez."""
    pasta = MODELOS / modelo["repo"]
    if (pasta / "inference.onnx").is_file() and (pasta / "inference.yml").is_file():
        return pasta
    import urllib.request
    pasta.mkdir(parents=True, exist_ok=True)
    print(f"OCR: baixando {modelo['repo']} (~{modelo['mb']} MB, só na primeira vez) do Hugging Face para {pasta}",
          file=sys.stderr, flush=True)
    for arq in ("inference.yml", "inference.onnx"):  # o .onnx por último: ele presente = download completo
        url = _HF.format(repo=modelo["repo"], rev=modelo["rev"], arq=arq)
        tmp = pasta / f"{arq}.part"
        try:
            h = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                while bloco := r.read(1 << 20):
                    h.update(bloco)
                    f.write(bloco)
            if arq == "inference.onnx" and h.hexdigest() != modelo["sha256"]:
                raise OSError(f"{url}: o arquivo baixado não confere (sha256)")
            os.replace(tmp, pasta / arq)
        finally:
            tmp.unlink(missing_ok=True)
    return pasta


def _config(pasta: Path) -> dict:
    import yaml  # dependência do rapidocr_onnxruntime
    with open(pasta / "inference.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _sessao(caminho: Path):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 4  # o ONNX exportado do Paddle avisa de inicializadores sobrando
    so.enable_cpu_mem_arena = False  # como o RapidOCR: a memória volta depois de cada página
    return ort.InferenceSession(str(caminho), so, providers=["CPUExecutionProvider"])


def _detector(pasta: Path):
    """Detector DB do RapidOCR com o modelo e o pós-processamento do inference.yml (sem dilatação, como na
    PaddleOCR 3). A imagem vai na resolução original; só a menor que 64 px é ampliada (como na PaddleOCR 3: o
    mínimo de 736 px do PP-OCRv3 ampliava um recorte de uma linha 5x e o PP-OCRv6 partia a linha em pedaços)."""
    from rapidocr_onnxruntime.ch_ppocr_v3_det.text_detect import TextDetector
    pp = _config(pasta)["PostProcess"]
    return TextDetector({"model_path": str(pasta / "inference.onnx"), "use_cuda": False, "limit_side_len": 64,
                         "limit_type": "min", "thresh": pp["thresh"], "box_thresh": pp["box_thresh"],
                         "unclip_ratio": pp["unclip_ratio"], "max_candidates": pp.get("max_candidates", 1000),
                         "use_dilation": False, "score_mode": "fast"})


class _Reconhecedor:
    """Reconhecedor CTC como na PaddleOCR 3: recorte com 48 px de altura, lote da largura do mais largo (no mínimo
    320 px), (x/255 - 0.5)/0.5; dicionário do inference.yml + espaço. Mesma interface do RapidOCR:
    ``rec(lista_bgr) -> ([(texto, confiança)], segundos)``. O decodificador é o do RapidOCR, com a confiança dele
    (média com um termo a mais: uma linha de 1 caractere fica abaixo de 0.5 e cai no filtro, como no motor antigo)."""

    def __init__(self, pasta: Path, lote: int = 6):
        from rapidocr_onnxruntime.ch_ppocr_v3_rec.utils import CTCLabelDecode
        chars = [str(c) for c in _config(pasta)["PostProcess"]["character_dict"]]
        self.sessao = _sessao(pasta / "inference.onnx")
        self.entrada = self.sessao.get_inputs()[0].name
        classes = self.sessao.get_outputs()[0].shape[-1]
        if classes != len(chars) + 2:  # vazio do CTC + dicionário + espaço
            raise ValueError(f"{pasta.name}: dicionário com {len(chars)} letras para {classes} classes")
        self.decodificar = CTCLabelDecode(chars)
        self.lote = lote

    @staticmethod
    def _normalizar(img: np.ndarray, proporcao: float) -> np.ndarray:
        h, w = img.shape[:2]
        largura = int(48 * proporcao)
        lw = min(largura, int(math.ceil(48 * w / max(1, h))))
        r = cv2.resize(img, (max(1, lw), 48)).astype(np.float32).transpose(2, 0, 1) / 255.0
        saida = np.zeros((3, 48, largura), np.float32)
        saida[:, :, :lw] = (r - 0.5) / 0.5
        return saida

    def __call__(self, imgs):
        import time
        if isinstance(imgs, np.ndarray):
            imgs = [imgs]
        proporcoes = [im.shape[1] / max(1.0, float(im.shape[0])) for im in imgs]
        ordem = np.argsort(proporcoes)  # larguras parecidas no mesmo lote
        res = [("", 0.0)] * len(imgs)
        t = time.time()
        for i in range(0, len(imgs), self.lote):
            idx = ordem[i:i + self.lote]
            prop = max([320 / 48] + [proporcoes[j] for j in idx])
            lote = np.stack([self._normalizar(imgs[j], prop) for j in idx])
            for j, (texto, conf) in zip(idx, self.decodificar(self.sessao.run(None, {self.entrada: lote})[0])):
                res[j] = (texto, float(conf))
        return res, time.time() - t


def _carregar():
    """O motor de OCR (carregado uma vez): o padrão, ou o de reserva se o padrão não pôde ser baixado/aberto."""
    global _motor, _nome_motor
    with _lock_carga:
        if _motor is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as e:  # pragma: no cover - depende do ambiente
                raise OCRIndisponivel("instale o OCR: pip install --no-deps rapidocr_onnxruntime==1.2.3 "
                                      "&& pip install onnxruntime pyclipper shapely pyyaml six") from e
            motor = RapidOCR()  # o fluxo det -> ordem -> recorte -> rec -> filtro de confiança do RapidOCR fica
            try:
                motor.text_detector = _detector(_baixar(_DET))
                motor.text_recognizer = _Reconhecedor(_baixar(_REC))
                # sem o classificador 0/180° (do PP-OCRv2): a página já vem de pé (EXIF aplicado) e ele virava a linha
                # digitável (só dígitos) de cabeça para baixo: 6 de 19 contas sintéticas liam "0-00000000000 8-..."
                motor.use_angle_cls = False
                nome = MOTOR_PADRAO
            except Exception as e:  # noqa: BLE001 - sem rede, disco cheio, arquivo estragado: o OCR antigo ainda lê
                print(f"OCR: sem o {MOTOR_PADRAO} ({type(e).__name__}: {e}); lendo com o {MOTOR_RESERVA} "
                      "(sem acentos; os espaços são deduzidos da tinta)", file=sys.stderr, flush=True)
                motor, nome = RapidOCR(), MOTOR_RESERVA
            _motor, _nome_motor = motor, nome
    return _motor


def motor() -> str:
    """Nome do motor de OCR que lê neste processo (carrega o motor)."""
    _carregar()
    return _nome_motor


def _le_espacos(nome: str | None) -> bool:
    """O motor separa as palavras sozinho; o de reserva come os espaços e eles vêm da tinta."""
    return nome == MOTOR_PADRAO


def _do_cache(cache: Path) -> tuple[list[dict] | None, str | None]:
    try:
        dados = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if isinstance(dados, list):  # formato de antes do nome do motor: só o PP-OCRv3 gravava assim
        return dados, MOTOR_RESERVA
    return dados.get("linhas"), dados.get("motor")


def gravar_cache(cache: Path, linhas: list[dict], motor: str | None = None) -> None:
    """Grava a leitura de um documento com o nome do motor que leu (padrão: o deste processo). Os testes gravam assim
    as linhas "como se o OCR tivesse lido"."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps({"motor": motor or _nome_motor or MOTOR_PADRAO, "linhas": linhas}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, cache)


def ler(img: np.ndarray, cache: Path | None = None) -> list[dict]:
    """Linhas de texto: [{"caixa": [x0, y0, x1, y1], "texto": str, "bruto": str, "conf": float}], de cima para
    baixo. ``bruto`` é o que o motor leu; ``texto``, com os espaços que o motor de reserva comeu."""
    linhas = nome = None
    if cache is not None and cache.is_file():
        linhas, nome = _do_cache(cache)
        if linhas is not None and nome != (_nome_motor or MOTOR_PADRAO):
            try:  # leitura de outro motor: vale só se é o que este processo consegue usar
                _carregar()
            except OCRIndisponivel:
                pass
            if _nome_motor is not None and nome != _nome_motor:
                linhas = None
    if linhas is not None:  # os espaços são refeitos do texto cru: o cache sobrevive a melhorias dessa etapa
        espacos = _le_espacos(nome)
        return _juntar_pedacos([{**l, "texto": l.get("bruto", l["texto"]) if espacos else
                                 recuperar_espacos(l.get("bruto", l["texto"]), img, l["caixa"])} for l in linhas],
                               img, recuperar=not espacos)
    with _lock:
        resultado, _ = _carregar()(np.ascontiguousarray(img[..., ::-1]))
        nome = _nome_motor
    espacos = _le_espacos(nome)
    linhas = []
    for poligono, texto, conf in resultado or []:
        xs, ys = [p[0] for p in poligono], [p[1] for p in poligono]
        caixa = [int(max(0, min(xs))), int(max(0, min(ys))), int(min(img.shape[1], max(xs) + 1)),
                 int(min(img.shape[0], max(ys) + 1))]
        if caixa[2] - caixa[0] < 3 or caixa[3] - caixa[1] < 3:
            continue
        bruto = str(texto)
        linhas.append({"caixa": caixa, "texto": bruto if espacos else recuperar_espacos(bruto, img, caixa),
                       "bruto": bruto, "conf": float(conf)})
    linhas.sort(key=lambda l: (l["caixa"][1], l["caixa"][0]))
    if cache is not None:
        gravar_cache(cache, linhas, nome)
    return _juntar_pedacos(linhas, img, recuperar=not espacos)


def _juntar_pedacos(linhas: list[dict], img: np.ndarray, recuperar: bool = True) -> list[dict]:
    """Pedaços da MESMA linha que o OCR separou sem vão de coluna ("JOS E" + "CARLOS RIBEIRO": o acento do É virou
    corte) viram uma linha só: mesma faixa vertical, alturas parecidas e vão menor que meia altura de letra. Rótulo
    e valor em colunas (vão grande) continuam separados. Sem isso, só o pedaço de cima do rótulo virava o campo.
    ``recuperar``: refaz os espaços pela tinta (motor que come os espaços)."""
    linhas = [dict(l) for l in linhas]
    juntou = True
    while juntou:
        juntou = False
        for a in linhas:
            ax0, ay0, ax1, ay1 = a["caixa"]
            ha = ay1 - ay0
            for b in linhas:
                if b is a:
                    continue
                bx0, by0, bx1, by1 = b["caixa"]
                hb = by1 - by0
                h = min(ha, hb)
                if (bx0 >= ax0 and bx0 - ax1 <= 0.5 * h and bx1 > ax1 and max(ha, hb) <= 1.3 * h
                        and abs((ay0 + ay1) - (by0 + by1)) / 2 <= 0.3 * h):
                    bruto = f"{a.get('bruto', a['texto'])} {b.get('bruto', b['texto'])}"
                    a["caixa"] = [ax0, min(ay0, by0), bx1, max(ay1, by1)]
                    a.update(bruto=bruto, texto=recuperar_espacos(bruto, img, a["caixa"]) if recuperar else bruto,
                             conf=min(a.get("conf", 1.0), b.get("conf", 1.0)))
                    linhas.remove(b)
                    juntou = True
                    break
            if juntou:
                break
    linhas.sort(key=lambda l: (l["caixa"][1], l["caixa"][0]))
    return linhas


def palavras_por_tinta(img: np.ndarray, caixa) -> list[tuple[int, int]]:
    """Intervalos [x0, x1) de cada palavra dentro da caixa, separados por vãos de tinta largos."""
    x0, y0, x1, y1 = caixa
    rec = img[y0:y1, x0:x1]
    if rec.size == 0:
        return []
    tinta = mascara_tinta(rec).astype(bool)
    # fio de tabela atravessando a caixa (boleto: o texto encosta na linha da célula) emendava todas as
    # palavras num bloco só e o OCR ficava sem espaços ("Rua58CjPrefeito..."): traços horizontais mais
    # compridos que 3 alturas de letra saem da conta
    k = max(15, 3 * rec.shape[0])
    if tinta.shape[1] > k:
        fios = cv2.morphologyEx(tinta.astype(np.uint8), cv2.MORPH_OPEN, np.ones((1, k), np.uint8),
                                borderType=cv2.BORDER_CONSTANT, borderValue=0)
        tinta &= ~(cv2.dilate(fios, np.ones((3, 3), np.uint8)) > 0)  # com a franja de antialias do fio
    if not tinta.any():
        return []
    # só a faixa principal da linha conta: a caixa do OCR pega às vezes o rótulo miúdo de cima ou o topo da
    # linha de baixo, que tapavam os vãos entre as palavras e inflavam a altura
    perfil = tinta.mean(1)
    nucleo = np.flatnonzero(perfil >= 0.5 * perfil.max())
    faixa = tinta[nucleo[0]:nucleo[-1] + 1]
    colunas = faixa.any(0)
    altura = max(1, int(tinta.any(1).sum()))
    vao_min = max(2, int(round(0.33 * altura)))  # espaço entre palavras ~ 1/3 da altura da letra
    # em fonte apertada ("M R C Filho") o espaço fica perto de 1/4 da altura: os vãos se dividem em dois
    # grupos (entre letras, entre palavras); se o grupo dos largos é bem mais largo e passa de 1/5 da altura,
    # o corte entre os grupos vale como espaço
    vaos, corrida = [], 0
    for c in colunas:
        if c:
            if corrida:
                vaos.append(corrida)
            corrida = 0
        else:
            corrida += 1
    if len(vaos) >= 3:
        v = np.sort(np.array(vaos, float))
        melhor = None
        for i in range(1, len(v)):
            a, b = v[:i], v[i:]
            sep = len(a) * len(b) * (a.mean() - b.mean()) ** 2
            if melhor is None or sep > melhor[0]:
                melhor = (sep, a, b)
        _sep, a, b = melhor
        if b.mean() >= 1.8 * max(1.0, a.mean()) and b.min() >= max(2.0, 0.2 * len(faixa)) and b.min() > a.max():
            vao_min = int(b.min())
    blocos, ini, vazio = [], None, 0
    for i, c in enumerate(colunas):
        if c:
            if ini is None:
                ini = i
            vazio = 0
        elif ini is not None:
            vazio += 1
            if vazio >= vao_min:
                blocos.append((ini, i - vazio + 1))
                ini, vazio = None, 0
    if ini is not None:
        fim = len(colunas) - vazio
        blocos.append((ini, fim))
    return [(x0 + a, x0 + b) for a, b in blocos]


def recuperar_espacos(texto: str, img: np.ndarray, caixa) -> str:
    """Reinsere os espaços que o OCR comeu, só em texto de LETRAS (nomes, endereços).

    As palavras vêm dos vãos de tinta; o corte no texto usa a largura real de cada letra numa
    fonte (um I é estreito, um M é largo), não uma divisão uniforme. Números (CPF, CEP, datas,
    valores) ficam como o OCR leu: um vão largo entre dígitos não é espaço."""
    if len(texto) < 4:
        return texto
    letras = sum(ch.isalpha() for ch in texto)
    if letras < 0.4 * len(texto.replace(" ", "")):  # "60750-720-FORTALEZA-CE" também (o corte nunca separa dígitos)
        return texto
    palavras = palavras_por_tinta(img, caixa)
    n_ocr = len(texto.split())
    original = texto
    junto = texto.replace(" ", "")
    # espaços que o OCR já pôs (posição no texto sem espaços): o corte novo tem de mantê-los
    espacos_ocr, k = set(), 0
    for p in texto.split()[:-1]:
        k += len(p)
        espacos_ocr.add(k)
    # vãos estreitos demais (vírgula, dígito largo) às vezes passam por espaço: junta o menor vão e tenta de
    # novo, até o corte fazer sentido ou não sobrar palavra a acrescentar
    while len(palavras) > max(1, n_ocr):
        cortes = _cortar(junto, palavras)
        if cortes is not None and _corte_valido(junto, cortes, espacos_ocr):
            pedacos, ini = [], 0
            for k in cortes:
                pedacos.append(junto[ini:k])
                ini = k
            pedacos.append(junto[ini:])
            return " ".join(p for p in pedacos if p)
        vaos = [palavras[i + 1][0] - palavras[i][1] for i in range(len(palavras) - 1)]
        i = int(np.argmin(vaos))
        palavras = palavras[:i] + [(palavras[i][0], palavras[i + 1][1])] + palavras[i + 2:]
    return original


def _corte_valido(texto: str, cortes: list[int], espacos_ocr: set[int]) -> bool:
    """Corte que um texto de verdade teria: nunca entre dois dígitos ("10 8") nem antes de pontuação
    ("FLORES ,"), e sem perder um espaço que o próprio OCR já tinha visto (± 1 caractere)."""
    for k in cortes:
        a, b = texto[k - 1], texto[k]
        if (a.isdigit() and b.isdigit()) or b in ",.;:)":
            return False
    return all(any(abs(k - e) <= 1 for k in cortes) for e in espacos_ocr)


def _cortar(texto: str, palavras: list[tuple[int, int]]) -> list[int] | None:
    """Onde cortar ``texto`` (sem espaços) para que cada pedaço tenha a largura do seu bloco de tinta."""
    n, m = len(texto), len(palavras)
    if m < 2 or m > n:
        return None
    larguras = np.array([b - a for a, b in palavras], float)
    fonte = _fonte_medida()
    acum = np.array([fonte.getlength(texto[:k]) for k in range(n + 1)], float)
    escala = larguras.sum() / max(acum[-1], 1e-6)

    def natural(k: int) -> bool:  # corte onde uma palavra costuma começar: "Cj|Prefeito", "58|Cj", "Vasques,|69"
        a, b = texto[k - 1], texto[k]
        return (a.islower() and b.isupper()) or (a.isdigit() != b.isdigit() and a.isalnum() and b.isalnum()) or a in ",;:"

    # cortes por programação dinâmica: cada pedaço do texto tem de ter a largura do SEU bloco de tinta (o
    # acumulado sozinho errava um caractere no meio da linha: "C JPREFEITO"); corte natural ganha um bônus
    custo = np.full((m + 1, n + 1), np.inf)
    de = np.zeros((m + 1, n + 1), int)
    custo[0, 0] = 0.0
    for b in range(1, m + 1):
        for j in range(b, n - (m - b) + 1):
            for i in range(b - 1, j):
                if not np.isfinite(custo[b - 1, i]):
                    continue
                w = (acum[j] - acum[i]) * escala
                c = custo[b - 1, i] + math.log(max(w, 1e-3) / larguras[b - 1]) ** 2 - (0.02 if 0 < i < n and natural(i) else 0.0)
                if c < custo[b, j]:
                    custo[b, j], de[b, j] = c, i
    if not np.isfinite(custo[m, n]):
        return None
    cortes, j = [], n
    for b in range(m, 0, -1):
        j = de[b, j]
        cortes.append(j)
    return sorted(k for k in cortes if 0 < k < n)


def _fonte_medida():
    global _fonte_cache
    try:
        return _fonte_cache
    except NameError:
        from PIL import ImageFont
        try:
            _fonte_cache = ImageFont.truetype("arial.ttf", 100)
        except OSError:
            _fonte_cache = ImageFont.load_default(size=100)
        return _fonte_cache


def caixa_tinta(img: np.ndarray, caixa, folga: int = 0) -> list[int]:
    """Caixa justa da tinta dentro de ``caixa`` (+ folga), para apagar só o necessário."""
    x0, y0, x1, y1 = caixa
    rec = img[y0:y1, x0:x1]
    m = mascara_tinta(rec).astype(bool) if rec.size else np.zeros((0, 0), bool)
    if not m.any():
        return list(caixa)
    ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
    H, W = img.shape[:2]
    return [max(0, x0 + int(xs[0]) - folga), max(0, y0 + int(ys[0]) - folga),
            min(W, x0 + int(xs[-1]) + 1 + folga), min(H, y0 + int(ys[-1]) + 1 + folga)]


def tinta_da_linha(rec: np.ndarray) -> np.ndarray:
    """Máscara bool da tinta da linha de texto PRINCIPAL de um recorte: sem fios de tabela (horizontais mais
    compridos que 3 alturas de linha ou que a célula toda; verticais da altura toda) e sem o que não cruza a
    faixa central da linha (o rótulo miúdo encostado em cima, o topo da linha de baixo)."""
    t = mascara_tinta(rec).astype(np.uint8)
    h, w = t.shape
    if not t.any():
        return t.astype(bool)
    k = max(12, min(3 * h, int(0.85 * w)))  # numa célula estreita ("CE"), o fio ocupa a largura toda
    if w > k:
        fio = cv2.morphologyEx(t, cv2.MORPH_OPEN, np.ones((1, k), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
        t &= (cv2.dilate(fio, np.ones((3, 3), np.uint8)) == 0).astype(np.uint8)  # com a franja de antialias
    if h >= 8:
        fio = cv2.morphologyEx(t, cv2.MORPH_OPEN, np.ones((h, 1), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
        t &= (cv2.dilate(fio, np.ones((3, 3), np.uint8)) == 0).astype(np.uint8)
    if not t.any():
        return t.astype(bool)
    return so_a_linha(t)


def so_a_linha(t: np.ndarray) -> np.ndarray:
    """Só os componentes de tinta que cruzam a faixa central da linha (as linhas com ao menos metade da tinta da mais
    cheia): fora ficam o rótulo encostado, o topo da linha de baixo e o acento solto."""
    t = np.asarray(t, np.uint8)
    if not t.any():
        return t.astype(bool)
    perfil = t.mean(1)
    nucleo = np.flatnonzero(perfil >= 0.5 * perfil.max())
    n0, n1 = int(nucleo[0]), int(nucleo[-1])
    n, lab, est, _c = cv2.connectedComponentsWithStats(t, connectivity=8)
    manter = np.zeros(n, bool)
    for i in range(1, n):
        topo, alt = est[i, cv2.CC_STAT_TOP], est[i, cv2.CC_STAT_HEIGHT]
        manter[i] = est[i, cv2.CC_STAT_AREA] >= 2 and topo <= n1 and topo + alt - 1 >= n0
    return manter[lab]


def sem_acento(s: str) -> str:
    """O texto sem os sinais (acento, til, cedilha): como o OCR de reserva (PP-OCRv3) lê."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


@lru_cache(maxsize=512)
def _fracoes_faixa(conteudo: str) -> tuple[float, float]:
    """(abaixo, base) do texto sem acentos desenhado na Arial: fração das colunas de tinta cujo pé desce abaixo da
    linha de base (g, p, vírgula) e cujo pé está nela."""
    from PIL import Image, ImageDraw

    s = sem_acento(conteudo).strip()
    f = _fonte_medida()
    if not s:
        return 0.0, 1.0
    l, t, r, b = f.getbbox(s, anchor="ls")
    tela = Image.new("L", (int(r - l) + 4, int(b - t) + 4), 0)
    ImageDraw.Draw(tela).text((2 - l, 2 - t), s, fill=255, font=f, anchor="ls")
    m = np.asarray(tela) > 127
    m = m[:, m.any(0)]
    if not m.size:
        return 0.0, 1.0
    topos, pes = m.argmax(0), m.shape[0] - 1 - m[::-1].argmax(0)
    base = 1 - int(t)  # última linha de pixels acima da linha de base
    tol = max(2.0, 0.08 * (base - int(topos.min())))
    return float((pes > base + tol).mean()), float((np.abs(pes - base) <= tol).mean())


def faixa_da_linha(tinta: np.ndarray, conteudo: str | None = None) -> tuple[int, int]:
    """(topo, base): linhas (inclusive) da faixa central de uma linha de texto, do alto das letras até a linha de base,
    SEM o acento em cima e a cedilha embaixo (o OCR de reserva nem os lê: "CONCEICAO"). Com a cedilha dentro da
    caixa medida, o tamanho saía 9-28% maior (ou a família trocada); com o texto certo ("SÃO JOSÉ"), o acento do
    desenho candidato encolhia a fonte.

    ``tinta`` sem os componentes soltos (``so_a_linha``: o acento solto já saiu); topo = a mais alta das letras.
    Base = a linha em que, de baixo para cima, já passaram as colunas que descem de verdade (g, p, vírgula: a fração
    vem do ``conteudo`` desenhado sem acentos) e metade das que pousam na linha de base: a cedilha é de poucas colunas.
    A MESMA regra mede o scan e a fonte candidata: o que sobra de diferença entre os dois é a forma das letras."""
    cols = tinta.any(0)
    if not cols.any():
        return 0, max(0, tinta.shape[0] - 1)
    t = tinta[:, cols]
    pes = t.shape[0] - 1 - t[::-1].argmax(0)
    topo = int(np.flatnonzero(tinta.any(1))[0])
    f_abaixo, f_base = _fracoes_faixa(conteudo or "HAMBURGO")
    n = len(pes)
    k = min(n, max(1, int(round((f_abaixo + 0.5 * f_base) * n))))
    base = int(-np.partition(-pes, k - 1)[k - 1])
    return topo, max(topo, base)


def inclinacao(tinta: np.ndarray, limite: float = 6.0) -> float:
    """Inclinação da linha de texto em graus (+ = sobe para a direita, como a ``rotacao`` de texto.renderizar): o
    ângulo que deixa o perfil horizontal da tinta mais concentrado (pés, meio e topo das letras alinhados). 0 quando
    a linha é curta demais para dizer ou a inclinação desloca menos de 1 px de uma ponta à outra (nada a endireitar)."""
    ys, xs = np.nonzero(tinta)
    if ys.size < 50:
        return 0.0
    largura = xs.max() - xs.min() + 1
    if largura < 3 * (ys.max() - ys.min() + 1):
        return 0.0
    if ys.size > 40000:  # linha enorme: uma amostra fixa basta
        sel = np.linspace(0, ys.size - 1, 40000).astype(int)
        ys, xs = ys[sel], xs[sel]
    ys, xs = ys.astype(np.float64), xs - xs.mean()

    def nota(g: float) -> float:
        yy = ys + xs * math.tan(math.radians(g))
        yy -= yy.min()
        i = yy.astype(np.int64)
        fr = yy - i  # cada ponto dividido entre as duas linhas vizinhas: a nota varia contínua com o ângulo
        p = np.bincount(i, 1 - fr, minlength=i.max() + 2) + np.bincount(i + 1, fr, minlength=i.max() + 2)
        return float((p * p).sum())

    melhor = max(np.arange(-limite, limite + 1e-9, 0.25), key=nota)
    melhor = max(np.arange(melhor - 0.25, melhor + 0.25 + 1e-9, 0.02), key=nota)
    return round(float(melhor), 2) if abs(math.tan(math.radians(melhor))) * largura >= 1.0 else 0.0


# palavras que só existem com acento (o OCR de reserva lê sem): se o documento as escreve e nenhuma linha tem sinal,
# ele não usa
_SEMPRE_ACENTO = re.compile(r"^(SAO|JOAO|JOSE|MES|AGUA|PRACA|ENDERECO|CODIGO|NUMERO|MATRICULA|REFERENCIA|MUNICIPIO"
                            r"|PAGINA|ULTIMO|ULTIMA|ANTONIO|LUCIA|MARCIA|PATRICIA|FATIMA|VITORIA|JOSEFA|GOIANIA|AMAPA"
                            r"|PARANA|CEARA|PIAUI|MARANHAO|BRASILIA|BELEM|MACEIO|SERGIO|FABIO|FLAVIO|VINICIUS)$"
                            r"|CAO$|COES$")
_DESCE = set("gjpqyQJ,;()[]{}|@$/")


def _sinais_da_linha(img: np.ndarray, caixa, texto: str | None = None) -> int | None:
    """Quantos sinais diacríticos a linha mostra: componente pequeno logo acima da linha mais alta das letras, em cima
    de uma letra (acento, til solto) e, numa linha só de letras maiúsculas, pé curto abaixo da linha de base (cedilha,
    solta ou grudada). Os pingos do i e do j de uma linha sem letra alta saem da conta. None se a letra é pequena
    demais para ver o sinal."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in caixa)
    h = max(1, y1 - y0)
    my = max(4, int(round(0.7 * h)))
    ax0, ay0, ax1, ay1 = max(0, x0 - 3), max(0, y0 - my), min(W, x1 + 3), min(H, y1 + my)
    by0, by1 = max(0, y0 - 3), min(H, y1 + 3)
    if ax1 - ax0 < 4 or by1 - by0 < 4:
        return None
    linha = np.zeros((ay1 - ay0, ax1 - ax0), bool)
    linha[by0 - ay0:by1 - ay0] = tinta_da_linha(img[by0:by1, ax0:ax1])
    if not linha.any():
        return None
    topo, base = faixa_da_linha(linha, texto)
    hb = base - topo + 1
    if hb < 8:
        return None
    tinta = mascara_tinta(img[ay0:ay1, ax0:ax1]).astype(bool)
    outra = tinta & ~(cv2.dilate(linha.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    n, _lab, est, _c = cv2.connectedComponentsWithStats(outra.astype(np.uint8), connectivity=8)
    hh, ww = outra.shape
    colunas = np.flatnonzero(linha.any(0))
    so_letras = texto is not None and texto.upper() == texto and not (set(texto) & _DESCE) \
        and not any(c.isdigit() for c in texto)  # dígito de estilo antigo (Georgia) também desce
    acima = abaixo = 0
    for i in range(1, n):
        cx, cy, cw, ch, area = (int(v) for v in est[i])
        if cx == 0 or cy == 0 or cx + cw >= ww or cy + ch >= hh:
            continue  # encosta na borda: rótulo, linha vizinha
        if ch > 0.45 * hb or cw > 0.9 * hb or area < max(4, 0.01 * hb * hb):
            continue
        if not colunas[0] <= cx + cw / 2 <= colunas[-1]:
            continue
        if topo - 0.5 * hb <= cy + ch - 1 <= topo and linha[topo:topo + max(2, hb // 2), cx:cx + cw].any():
            acima += 1  # solto, logo acima de uma letra
        elif so_letras and base < cy <= base + 0.25 * hb and linha[base - hb // 3:base + 1, cx:cx + cw].any():
            abaixo += 1  # solto, logo abaixo de uma letra
    if texto is not None and not any(c.isupper() or c.isdigit() or c in "bdfhklt" for c in sem_acento(texto)):
        acima = max(0, acima - sum(c in "ij" for c in texto))  # linha sem letra alta: o pingo fica acima da faixa
    if so_letras:
        # cedilha grudada: pé curto descendo da linha de base numa linha sem nada que desce
        t = linha[:, colunas]
        fundo = (t.shape[0] - 1 - t[::-1].argmax(0)) > base + 0.12 * hb
        corridas = np.diff(np.r_[0, fundo.astype(np.int8), 0])
        abaixo += sum(1 for a, b in zip(np.flatnonzero(corridas == 1), np.flatnonzero(corridas == -1)) if b - a <= 0.6 * hb)
    return acima + abaixo


def usa_acentos(img: np.ndarray, caixa=None, linhas: list[dict] | None = None) -> bool | None:
    """O documento escreve com acento e cedilha? O OCR de reserva devolve tudo sem ("CONCEICAO") e o padrão às vezes
    perde um sinal, então quem gera o valor falso olha a tinta: um documento que escreve "JOÃO" e ganha um "JOAO"
    falso denuncia a edição (e o contrário).

    Procura sinais nas linhas (``linhas`` do OCR; ``caixa``: só essa; nenhum dos dois: lê o documento). True: achou
    sinal em 2 lugares (1 basta numa caixa só); False: linhas em maiúsculas com palavras que só existem com acento
    (JOSE, SAO, ...CAO) e nenhum sinal no documento; None: não dá para dizer (letra pequena, só minúsculas)."""
    if caixa is not None:
        x0, y0, x1, y1 = (int(v) for v in caixa)
        try:  # o texto diz se há pingo de i/j e se a linha tem letra que desce
            txt = reconhecer(img[max(0, y0 - 4):y1 + 4, max(0, x0 - 4):x1 + 4]) or None
        except OCRIndisponivel:
            txt = None
        return True if _sinais_da_linha(img, caixa, txt) else None
    if linhas is None:
        linhas = ler(img)
    sinais, palavras = 0, 0
    for l in linhas:
        txt = str(l.get("texto", ""))
        letras = [c for c in txt if c.isalpha()]
        if len(letras) < 2:
            continue
        s = _sinais_da_linha(img, l["caixa"], txt)
        if s is None:
            continue
        sinais += s
        if sum(c.isupper() for c in letras) >= 0.8 * len(letras):
            palavras += sum(bool(_SEMPRE_ACENTO.search(w)) for w in re.findall(r"[A-Z]+", sem_acento(txt).upper()))
        if sinais >= 2:
            return True
    if sinais == 0 and palavras >= 2:
        return False
    return None


def caixa_da_linha(img: np.ndarray, caixa) -> list[int]:
    """Caixa justa só da linha de texto PRINCIPAL dentro de ``caixa`` (``tinta_da_linha``): sem o rótulo miúdo
    que encosta em cima (boleto: "Pagador" sobre o nome), sem o topo da linha de baixo e sem os fios da tabela.
    Com tudo isso dentro, o tamanho da fonte saía maior, a fonte identificada errada e o apagamento borrava o
    rótulo vizinho."""
    x0, y0, x1, y1 = (int(v) for v in caixa)
    rec = img[y0:y1, x0:x1]
    if rec.size == 0:
        return list(caixa)
    t = tinta_da_linha(rec)
    if not t.any():
        return caixa_tinta(img, caixa, 0)
    ys, xs = np.flatnonzero(t.any(1)), np.flatnonzero(t.any(0))
    return [x0 + int(xs[0]), y0 + int(ys[0]), x0 + int(xs[-1]) + 1, y0 + int(ys[-1]) + 1]


def ler_trecho(img: np.ndarray, caixa) -> str:
    """OCR de um retângulo marcado à mão (com margem, que o detector precisa de contexto)."""
    x0, y0, x1, y1 = caixa
    m = 12
    H, W = img.shape[:2]
    a, b, c, d = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
    rec = img[b:d, a:c]
    if rec.size == 0:
        return ""
    if rec.shape[0] < 32:  # o detector não acha texto em faixa muito baixa
        rec = cv2.copyMakeBorder(rec, 16, 16, 0, 0, cv2.BORDER_REPLICATE)
    return " ".join(l["texto"] for l in em_ordem_de_leitura(ler(rec)))


def reconhecer(rec: np.ndarray) -> str:
    """Só o RECONHECEDOR (sem detector) num recorte de uma linha: leitura de um campo já localizado.
    Em recortes pequenos o detector parte a linha em pedaços; o reconhecedor lê a linha inteira. O papel em branco
    que sobra dos lados (mais de meia altura do recorte) sai antes: com 160 px de papel à direita, o reconhecedor
    latino lia "R$ 202,33" como "R 202,3" (em 584 linhas com 5 alturas de papel sobrando: 8,7% de erro → 0,5%)."""
    if rec.size == 0:
        return ""
    cols = np.flatnonzero(tinta_da_linha(rec).any(0))
    if cols.size:
        m = max(4, rec.shape[0] // 2)
        rec = rec[:, max(0, int(cols[0]) - m):int(cols[-1]) + 1 + m]
    with _lock:
        res, _ = _carregar().text_recognizer([np.ascontiguousarray(rec[..., ::-1])])
    return str(res[0][0]) if res else ""


def em_ordem_de_leitura(linhas: list[dict]) -> list[dict]:
    """Pedaços na mesma linha (sobreposição vertical > 50%) da esquerda para a direita."""
    filas: list[list[dict]] = []
    for l in sorted(linhas, key=lambda l: (l["caixa"][1] + l["caixa"][3]) / 2):
        c = l["caixa"]
        if filas:
            u = filas[-1][0]["caixa"]
            inter = min(c[3], u[3]) - max(c[1], u[1])
            if inter > 0.5 * min(c[3] - c[1], u[3] - u[1]):
                filas[-1].append(l)
                continue
        filas.append([l])
    return [l for fila in filas for l in sorted(fila, key=lambda l: l["caixa"][0])]
