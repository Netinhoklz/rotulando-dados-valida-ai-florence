"""O motor de edição: o PhotoCraft (github.com/storytold/photocraft — o Photoshop refeito em Rust, Apache-2.0/MIT),
rodando escondido (``photocraft-cli serve``: JSON por linha no stdio) e comandado como uma pessoa usaria o editor.

O Python decide O QUÊ e MEDE (o campo, o valor novo, a fonte identificada, os hábitos do operador, o rótulo, a
conferência); o PIXEL sai das ferramentas do PhotoCraft:

- a **seleção** do texto antigo: só a tinta (quem trabalha com cuidado) ou a da pessoa (letreiro, laço, pincel ou
  intervalo de cores, com o gesto de ``rotulador.gesto``), mandada como polígonos do Laço;
- a **ferramenta de apagar**: preenchimento sensível ao conteúdo com a amostra pintada só no papel, Remendo arrastado
  até papel limpo, Carimbo e Pincel de Recuperação em passadas com pressão, a ferramenta Remover ou a cor do papel;
- a **camada de texto**: família e estilo da fonte identificada, tamanho em pt, escala horizontal, entreletra,
  suavização (nenhuma, nítida, precisa, forte, suave), cor e rotação;
- o **acabamento de editor**: rasterizar, Desfoque Gaussiano, Mínimo/Máximo numa cópia misturada pela opacidade (o peso
  do traço em fração de pixel), e os retoques da pessoa (Máscara de Nitidez, Desfoque de Superfície, Adicionar Ruído).

Cada edição manda só um RECORTE em volta do campo (gravado sem resolução: 72 ppi, 1 pt = 1 px), com instantâneos do
histórico: o texto vai primeiro nítido, para medir o alinhamento, o desfoque, o tom e o peso; a camada final volta ao
instantâneo do papel apagado e entra com o que a medida pediu. O executável vem de ``PHOTOCRAFT_CLI``, de
``config/photocraft.json`` ("cli") ou do PATH; sem ele, ``disponivel()`` é falso e a técnica sai do sorteio."""

from __future__ import annotations

import atexit
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

import cv2
import numpy as np

from . import gesto, glifos, ocr
from .pericia import _nitidez
from .selecao import mascara_tinta
from .sessao import ErroOperacao, Resultado

CONFIG = Path(__file__).resolve().parent.parent / "config" / "photocraft.json"
TEMPO_MAXIMO = 90.0  # s por pedido: o motor travado é encerrado e recomeça no pedido seguinte
_LUMA = np.float32([0.299, 0.587, 0.114])
# a ferramenta de apagar do operador humano (rotulador.humano.APAGAR) → a do PhotoCraft que faz o mesmo
DO_HUMANO = {"patchmatch": "preenchimento", "chapado": "cor", "fundo_clonado": "remendo", "carimbo": "carimbo",
             "recuperar": "recuperacao"}
APAGADORES = ("preenchimento", "remendo", "carimbo", "recuperacao", "remover", "cor")
DIZ_APAGADOR = {"preenchimento": "preenchimento sensível ao conteúdo (amostra só no papel)",
                "remendo": "Remendo arrastado até papel limpo", "carimbo": "Carimbo em passadas",
                "recuperacao": "Pincel de Recuperação em passadas", "remover": "ferramenta Remover",
                "cor": "a cor do papel (conta-gotas e Preencher)"}
# suavização do texto do operador humano (humano.RASTERIZADOR) → a do PhotoCraft; quem trabalha com cuidado: "smooth"
SUAVIZACAO = {"ps_nitida": "sharp", "ps_suave": "smooth", "ps_precisa": "crisp", "ps_forte": "strong", "aliasado": "none"}
PESO_ACERTAR = 0.06  # diferença de espessura do traço (fração) acima da qual o peso é acertado pela mistura
# a adaptação de cor do preenchimento sensível ao conteúdo: a "default" do motor é uma mistura de Poisson que alisa o
# papel (80 dígitos de verdade: grão 0,53 do papel em volta, os 10% piores 0,14, tom até +4); "none": 0,57, os piores
# 0,49, tom ±0,3
ADAPTACAO_CAF = "none"
# níveis de luma: o papel refeito mais claro ou escuro que o de volta é mancha (a do Pincel de Recuperação, +3 num papel
# de 250, aparecia a olho), e um degrau na borda da seleção é costura; a pessoa vê e refaz
TOM_MAXIMO = 2.0
COSTURA_MAXIMA = 3.0
GRAO_MINIMO = 0.3  # fração do grão do papel em volta: abaixo, o papel refeito ficou liso (a cura do motor falhou)


# ============================================================================ o processo
def caminho_cli() -> Path | None:
    """O ``photocraft-cli``: ``PHOTOCRAFT_CLI``, ``config/photocraft.json`` ("cli") ou o PATH."""
    cands = [os.environ.get("PHOTOCRAFT_CLI")]
    try:
        cands.append(json.loads(CONFIG.read_text(encoding="utf-8")).get("cli"))
    except (OSError, ValueError, AttributeError):
        pass
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    c = shutil.which("photocraft-cli")  # só sem os dois de cima: varrer o PATH custa ~13 ms e isto roda a cada técnica
    return Path(c) if c else None


def disponivel() -> bool:
    return caminho_cli() is not None


def caminho_web() -> Path | None:
    """A versão web do PhotoCraft (a pasta com o ``index.html``), que o estúdio serve em /photocraft/:
    ``PHOTOCRAFT_WEB`` ou ``config/photocraft.json`` ("web")."""
    cands = [os.environ.get("PHOTOCRAFT_WEB")]
    try:
        cands.append(json.loads(CONFIG.read_text(encoding="utf-8")).get("web"))
    except (OSError, ValueError, AttributeError):
        pass
    for c in cands:
        if c and (Path(c) / "index.html").is_file():
            return Path(c)
    return None


def achatar(conteudo: bytes, sufixo: str) -> np.ndarray:
    """O arquivo que o PhotoCraft salvou (PSD com camadas, TIFF...) achatado como ele mostra: RGB uint8. É o motor que
    lê o próprio formato (as camadas ocultas não entram, os modos de mistura e as máscaras sim)."""
    m = motor()
    arq = m.pasta / f"{uuid.uuid4().hex}{sufixo}"
    out = m.pasta / f"{uuid.uuid4().hex}_achatado.png"
    with m.lock:
        try:
            m.pasta.mkdir(parents=True, exist_ok=True)
            arq.write_bytes(conteudo)
            m.pedir("doc.open", {"path": str(arq)})
            try:
                m.pedir("doc.save", {"path": str(out)})
            finally:
                try:
                    m.pedir("doc.close")
                except ErroOperacao:
                    pass
            a = cv2.imread(str(out), cv2.IMREAD_COLOR)
        finally:
            arq.unlink(missing_ok=True)
            out.unlink(missing_ok=True)
    if a is None:
        raise ErroOperacao("o PhotoCraft não conseguiu ler o arquivo salvo")
    return cv2.cvtColor(a, cv2.COLOR_BGR2RGB)


class _Motor:
    """Um ``photocraft-cli serve`` reaproveitado entre as edições. O ``lock`` é do DOCUMENTO inteiro (o serve tem um
    documento ativo): quem abre um recorte segura o lock até fechá-lo."""

    def __init__(self, cli: Path):
        self.cli = cli
        self.pasta = Path(tempfile.gettempdir()) / f"rotulador_photocraft_{os.getpid()}"
        self.lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.n = 0
        self._familias: set | None = None
        self._faces: dict = {}

    def _iniciar(self) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [str(self.cli), "serve", "--automation-read-root", str(self.pasta),
             "--automation-write-root", str(self.pasta)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def fechar(self) -> None:
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                p.stdin.close()
                p.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                p.kill()

    def pedir(self, metodo: str, params: dict | None = None):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._iniciar()
            self.n += 1
            p = self.proc
            relogio = threading.Timer(TEMPO_MAXIMO, p.kill)  # travou: a leitura recebe o fim do arquivo
            relogio.start()
            try:
                p.stdin.write(json.dumps({"id": self.n, "method": metodo, "params": params or {}}) + "\n")
                p.stdin.flush()
                while True:
                    linha = p.stdout.readline()
                    if not linha:
                        raise OSError("o processo do motor fechou")
                    r = json.loads(linha)
                    if r.get("id") == self.n:
                        break
            except (OSError, ValueError) as e:
                self.proc = None
                p.kill()
                raise ErroOperacao(f"motor PhotoCraft: {e}") from e
            finally:
                relogio.cancel()
        if not r.get("ok"):
            raise ErroOperacao(f"o PhotoCraft recusou '{metodo}': {r.get('error')}")
        return r.get("result")

    def executar(self, comando: str, **params):
        return self.pedir("engine.execute", {"command": comando, "params": params})

    def face(self, nome: str) -> tuple[str, str]:
        """(família, estilo) do PhotoCraft para a fonte ``nome`` do rotulador ("Arial Narrow Bold" → "Arial",
        "Narrow Bold"): o nome que o arquivo da fonte dá e, se essa família não existe no motor, a família dele que é o
        começo do nome (o motor agrupa pela família tipográfica). Com um documento aberto."""
        if nome in self._faces:
            return self._faces[nome]
        fam, est = glifos.fonte_pil(nome).getname()
        if self._familias is None:
            self._familias = set(self.executar("type.fonts")["families"])
        if fam not in self._familias:
            pref = max((f for f in self._familias if fam.startswith(f + " ")), key=len, default=None)
            if pref is None:
                raise ErroOperacao(f"a fonte {nome} não está no PhotoCraft")
            est = f"{fam[len(pref):].strip()} {'' if est == 'Regular' else est}".strip()
            fam = pref
        if est not in {f["style"] for f in self.executar("type.fonts", family=fam)["faces"]}:
            raise ErroOperacao(f"o PhotoCraft não tem a {fam} {est}")
        self._faces[nome] = (fam, est)
        return fam, est


_motor: _Motor | None = None
_motor_lock = threading.Lock()


def motor() -> _Motor:
    global _motor
    with _motor_lock:
        if _motor is None:
            cli = caminho_cli()
            if cli is None:
                raise ErroOperacao("o motor PhotoCraft não está instalado (PHOTOCRAFT_CLI ou config/photocraft.json)")
            _motor = _Motor(cli)
            atexit.register(_motor.fechar)
        return _motor


class Documento:
    """Um recorte aberto no motor (use com ``with``, que segura o lock do motor até fechar): ``rodar`` passos,
    ``marcar`` um instantâneo, ``voltar`` a ele, ``imagem`` lê o resultado achatado (RGB uint8)."""

    def __init__(self, rec: np.ndarray):
        self.m = motor()
        self.rec = np.ascontiguousarray(rec)
        self.arq = self.m.pasta / f"{uuid.uuid4().hex}.png"

    def __enter__(self):
        self.m.lock.acquire()
        try:
            self.m.pasta.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(self.arq), cv2.cvtColor(self.rec, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 1]):
                raise ErroOperacao("não deu para gravar o recorte para o PhotoCraft")
            self.m.pedir("doc.open", {"path": str(self.arq)})
        except BaseException:
            self.m.lock.release()
            raise
        return self

    def __exit__(self, *_):
        try:
            self.m.pedir("doc.close")
        except ErroOperacao:
            pass
        finally:
            self.arq.unlink(missing_ok=True)
            self.m.lock.release()

    def marcar(self, nome: str) -> int:
        return self.m.executar("history.newSnapshot", name=nome)["snapshot"]

    def voltar(self, instantaneo: int) -> None:
        self.m.executar("history.selectSnapshot", snapshot=instantaneo)

    def rodar(self, passos: list) -> list:
        """Os passos ``[(comando, params), ...]`` num pedido só (``batch``); o primeiro que falha vira ErroOperacao."""
        if not passos:
            return []
        r = self.m.pedir("batch", {"steps": [{"method": "engine.execute", "params": {"command": c, "params": p}}
                                             for c, p in passos], "stopOnError": True})
        res = r.get("results") or []
        for (cmd, _), x in zip(passos, res):
            if not x.get("ok"):
                raise ErroOperacao(f"PhotoCraft: '{cmd}' falhou ({x.get('error')})")
        if r.get("failed") or len(res) < len(passos):
            raise ErroOperacao("PhotoCraft: a sequência parou no meio")
        return [x.get("result") for x in res]

    def imagem(self) -> np.ndarray:
        out = self.m.pasta / f"{uuid.uuid4().hex}_saida.png"
        try:
            self.m.pedir("doc.save", {"path": str(out)})
            a = cv2.imread(str(out), cv2.IMREAD_COLOR)
        finally:
            out.unlink(missing_ok=True)
        if a is None:
            raise ErroOperacao("o PhotoCraft não gravou o resultado")
        return cv2.cvtColor(a, cv2.COLOR_BGR2RGB)


# ============================================================================ seleção, papel livre, apagadores
def poligonos(m: np.ndarray) -> list[list[list[float]]]:
    """Os contornos externos da máscara (bool) como polígonos do Laço, por centro de pixel (``passos_selecao`` dilata
    1 px antes: a seleção cobre a máscara inteira, com o furo das letras redondas fechado, como o laço da mão)."""
    cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cs:
        if len(c) < 3:
            x, y, w, h = cv2.boundingRect(c)
            out.append([[float(x), float(y)], [float(x + w), float(y)], [float(x + w), float(y + h)],
                        [float(x), float(y + h)]])
        else:
            out.append([[float(p[0][0]) + 0.5, float(p[0][1]) + 0.5] for p in c])
    return out


def passos_selecao(m: np.ndarray) -> list:
    ps = poligonos(cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    if not ps:
        raise ErroOperacao("seleção vazia")
    return [("select.lasso", {"points": p, "antiAlias": False, "mode": "replace" if i == 0 else "add"})
            for i, p in enumerate(ps)]


def origem_limpa(proibido: np.ndarray, alvo: np.ndarray, rng: random.Random, vertical: float = 3.0):
    """Deslocamento (dx, dy) que leva o ``alvo`` (bool) inteiro para papel livre: nenhum pixel dele cai em
    ``proibido`` (a tinta e a própria seleção). Entre os 6 mais perto (a distância na horizontal pesa ``vertical``
    vezes: a mão arrasta para a linha em branco de cima ou de baixo), um sorteado. None se não há."""
    ys, xs = np.nonzero(alvo)
    if not ys.size:
        return None
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    sub = alvo[y0:y1, x0:x1].astype(np.float32)
    H, W = proibido.shape
    if sub.shape[0] >= H or sub.shape[1] >= W:
        return None
    soma = cv2.matchTemplate(proibido.astype(np.float32), sub, cv2.TM_CCORR)
    livres = np.argwhere(soma < 0.5)
    if not len(livres):
        return None
    d = (livres[:, 0] - y0).astype(np.float64) ** 2 + ((livres[:, 1] - x0) * vertical).astype(np.float64) ** 2
    perto = np.argsort(d)[:6]
    ly, lx = livres[perto[rng.randrange(len(perto))]]
    return int(lx) - x0, int(ly) - y0


def faixas_de_amostra(proibido: np.ndarray, sel: np.ndarray, h: float) -> list[dict]:
    """Os traços do Pincel de Amostragem do preenchimento sensível ao conteúdo: a faixa de papel livre logo acima e
    a logo abaixo da seleção, na largura dela com uma altura de folga (a pessoa pinta a amostra longe das letras: com a
    amostra automática, o preenchimento puxava pedaços das letras vizinhas para o buraco)."""
    ys, xs = np.nonzero(sel)
    H, W = proibido.shape
    sy0, sy1 = int(ys.min()), int(ys.max()) + 1
    a0, a1 = max(0, int(xs.min()) - int(h)), min(W, int(xs.max()) + 1 + int(h))
    livre = ~proibido[:, a0:a1].any(1)
    out = []
    for lado in (-1, 1):
        y = sy0 - 1 if lado < 0 else sy1
        while 0 <= y < H and not livre[y]:
            y += lado
        if not 0 <= y < H:
            continue
        ini = y
        while 0 <= y < H and livre[y]:
            y += lado
        a, b = sorted((ini, y - lado))
        alt = b - a + 1
        if alt < max(4, 0.4 * h):
            continue
        tam = int(min(alt, 2 * h))
        cy = (a + b + 1) / 2
        out.append({"mode": "add", "size": tam, "points": [[float(a0 + tam / 2), cy], [float(a1 - tam / 2), cy]]})
    return out


def _hex(cor) -> str:
    return "#%02x%02x%02x" % tuple(int(round(float(v))) for v in np.clip(np.asarray(cor, np.float32), 0, 255))


def apagador(ferramenta: str, rec: np.ndarray, sel: np.ndarray, proibido: np.ndarray, linha, h: float,
             rng: random.Random, escala: float = 1.0) -> tuple[list, dict]:
    """Os passos que apagam a seleção (já feita) com a ``ferramenta`` (``APAGADORES``) e o que vai para o rótulo.
    ``linha`` (x0, y0, x1, y1): a tinta antiga, por onde passam as passadas do pincel (``gesto.pinceladas``, no zoom
    ``1/escala`` da pessoa)."""
    if ferramenta == "cor":  # conta-gotas no papel em volta e Editar › Preencher
        anel = (cv2.dilate(sel.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0) & ~proibido
        cor = np.median((rec[anel] if anel.any() else rec.reshape(-1, 3)).reshape(-1, 3), axis=0)
        return [("edit.fill", {"contents": "color", "color": _hex(cor)})], {"cor_papel": _hex(cor)}
    if ferramenta == "preenchimento":
        tracos = faixas_de_amostra(proibido, sel, h)
        if not tracos:
            raise ErroOperacao("sem papel livre em volta para a amostra do preenchimento sensível ao conteúdo")
        return [("edit.contentAwareFill", {"sampling": "custom", "samplingStrokes": tracos, "output": "current",
                                           "colorAdaptation": ADAPTACAO_CAF, "seed": rng.randrange(2 ** 31)})], \
            {"amostra": len(tracos)}
    if ferramenta == "remendo":
        o = origem_limpa(proibido, sel, rng)
        if o is not None:
            return [("paint.patch", {"offset": list(o), "mode": "source"})], {"origem": list(o)}
        # sem papel do tamanho da linha inteira (linhas apertadas): remenda em pedaços, cada um com a sua origem
        passos, origens = [], []
        for parte in _pedacos(sel, h, rng):
            o = origem_limpa(proibido, parte, rng)
            if o is None:
                raise ErroOperacao("sem papel livre para o Remendo, nem em pedaços")
            passos += passos_selecao(parte) + [("paint.patch", {"offset": list(o), "mode": "source"})]
            origens.append(list(o))
        return passos, {"origem": origens, "pedacos": len(origens)}
    if ferramenta not in ("carimbo", "recuperacao", "remover"):
        raise ErroOperacao(f"ferramenta de apagar desconhecida: {ferramenta}")
    x0, y0, x1, y1 = linha
    if ferramenta == "recuperacao":
        # com o pincel da altura da linha inteira a cura do motor falha e devolve uma cor lisa (grão 0,02 do papel em 80
        # dígitos): pincel de ~1,4 altura em três faixas (em cima, no meio, embaixo), como quem diminui o pincel
        raio, faixas = max(3.0, h * rng.uniform(0.65, 0.75)), [(y0 + k * (y1 - y0) / 3, y0 + (k + 1) * (y1 - y0) / 3)
                                                                for k in range(3)]
    else:
        # o miolo duro do pincel (dureza × raio) cobre a altura da linha com o halo: com raio de 0,6–0,75 da altura, a
        # borda macia passava no pé e no topo das letras e sobrava um fantasma cinza na linha de base
        raio, faixas = max(3.0, h * rng.uniform(0.85, 1.0)), [(y0, y1)]
    dureza = rng.randint(70, 85)
    tracos = []
    for a, b in faixas:
        for t in gesto.pinceladas(x0, a, x1, b, raio, rng, escala):
            pr = gesto.pressao(len(t), rng)
            tracos.append([[round(float(x), 2), round(float(y), 2), round(float(p), 3)] for (x, y, _t), p in zip(t, pr)])
    tam = int(round(2 * raio))
    if ferramenta == "remover":
        return [("paint.remove", {"strokes": tracos, "size": tam, "hardness": dureza, "closeLoops": False})], \
            {"passadas": len(tracos)}
    # carimbo e recuperação: cada trecho da passada com o seu Alt+clique, numa origem de papel livre para o pedaço da
    # seleção que o pincel alcança ali (a pessoa reamostra a cada poucos centímetros, não uma vez para a linha toda)
    cmd = "paint.cloneStamp" if ferramenta == "carimbo" else "paint.healingBrush"
    passos = []
    for t in tracos:
        k, n = 0, len(t)
        while k < n:
            passo = max(2.5, rng.uniform(3.0, 6.0)) * h
            fim = k + 1
            while fim < n and abs(t[fim][0] - t[k][0]) < passo:  # a passada de volta anda para a esquerda
                fim += 1
            passos += _clonar(cmd, t[k:fim + 1], sel, proibido, raio, tam, dureza, rng)
            k = fim
    if not passos:
        raise ErroOperacao("as passadas não passaram pela seleção")
    return passos, {"passadas": len(tracos), "origem": [p["offset"] for _, p in passos]}


def _alcance(sel: np.ndarray, trecho, raio: float) -> np.ndarray:
    """O pedaço da seleção que o pincel alcança ao longo do ``trecho``: as colunas E as linhas por onde o disco passa
    (o que tem de cair em papel livre na origem; o resto da seleção, o pincel nem toca)."""
    H, W = sel.shape
    xs, ys = [p[0] for p in trecho], [p[1] for p in trecho]
    a, b = max(0, int(min(xs) - raio)), min(W, int(math.ceil(max(xs) + raio)) + 1)
    c, d = max(0, int(min(ys) - raio)), min(H, int(math.ceil(max(ys) + raio)) + 1)
    parte = np.zeros_like(sel)
    parte[c:d, a:b] = sel[c:d, a:b]
    return parte


def _clonar(cmd: str, trecho, sel, proibido, raio, tam, dureza, rng, nivel: int = 0) -> list:
    """O trecho da passada com o seu Alt+clique numa origem de papel livre; sem papel do tamanho do trecho (seleção
    larga, linhas apertadas), a pessoa divide a passada ao meio e reamostra para cada metade (até 3 vezes)."""
    parte = _alcance(sel, trecho, raio)
    if not parte.any():
        return []
    o = origem_limpa(proibido, parte, rng)
    if o is not None:
        return [(cmd, {"points": trecho, "size": tam, "hardness": dureza, "offset": list(o), "aligned": True})]
    if nivel >= 3 or len(trecho) < 3:
        raise ErroOperacao("sem papel livre para a origem do carimbo")
    meio = len(trecho) // 2
    return (_clonar(cmd, trecho[:meio + 1], sel, proibido, raio, tam, dureza, rng, nivel + 1)
            + _clonar(cmd, trecho[meio:], sel, proibido, raio, tam, dureza, rng, nivel + 1))


PINCEIS = ("carimbo", "recuperacao", "remover")  # as ferramentas de passada: se sobrou fantasma, a pessoa repassa


def avaliar_papel(out: np.ndarray, rec: np.ndarray, sel: np.ndarray, proibido: np.ndarray,
                  h: float) -> tuple[float, float, float, float] | None:
    """(grão do papel refeito / grão do papel em volta, diferença de tom, grão do papel em volta, costura) na seleção
    ``sel``: o grão é o desvio do resíduo passa-alta (σ 1, o do detector), o tom a luma média contra a mediana do papel,
    a costura o maior degrau na borda da seleção (``_costura``). O papel de
    referência é o LONGE da tinta (mais de ~8 px de ``proibido``), a até uma altura de letra da seleção (ou no recorte
    inteiro, se ali não há): perto da letra o mosquito do JPEG parece grão, e o papel limpo que o Carimbo copia é mais
    liso que ele sem estar errado. None sem papel para comparar."""
    longe = ~(cv2.dilate(proibido.astype(np.uint8), np.ones((11, 11), np.uint8)) > 0)
    k = 2 * int(max(2, h)) + 1
    anel = (cv2.dilate(sel.astype(np.uint8), np.ones((k, k), np.uint8)) > 0) & longe
    if anel.sum() < 50:
        anel = longe
    miolo = cv2.erode(sel.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    if anel.sum() < 50 or miolo.sum() < 10:
        return None
    l0, l1 = rec.astype(np.float32) @ _LUMA, out.astype(np.float32) @ _LUMA
    g0 = float((l0 - cv2.GaussianBlur(l0, (0, 0), 1.0))[anel].std())
    g1 = float((l1 - cv2.GaussianBlur(l1, (0, 0), 1.0))[miolo].std())
    return g1 / max(g0, 1e-3), float(l1[miolo].mean() - np.median(l0[anel])), g0, _costura(l1, rec, sel, h)


def _costura(lum: np.ndarray, rec: np.ndarray, sel: np.ndarray, h: float) -> float:
    """O maior degrau de luma na borda da seleção: a média de 1–3 px dentro contra a de 1–3 px fora (sem tinta), em
    trechos de uma altura de letra na horizontal. A média da área inteira não vê um degrau só num pedaço da borda."""
    s8, k = sel.astype(np.uint8), np.ones((3, 3), np.uint8)
    tinta = cv2.dilate(mascara_tinta(rec), np.ones((7, 7), np.uint8)) > 0
    dentro = sel & ~(cv2.erode(s8, k, iterations=3) > 0) & ~tinta
    fora = (cv2.dilate(s8, k, iterations=3) > 0) & ~sel & ~tinta
    passo, pior = int(max(8, h)), 0.0
    for x in range(0, sel.shape[1], passo):
        d, f = dentro[:, x:x + passo], fora[:, x:x + passo]
        if d.sum() >= 15 and f.sum() >= 15:
            pior = max(pior, abs(float(lum[:, x:x + passo][d].mean() - lum[:, x:x + passo][f].mean())))
    return pior


def _manchou(av) -> str | None:
    """O que a pessoa vê de errado no papel refeito (``avaliar_papel``): mancha mais clara/escura, costura na borda da
    seleção ou papel liso."""
    if av is None:
        return None
    grao, tom, g0, costura = av
    if abs(tom) > TOM_MAXIMO:
        return f"mancha {'mais clara' if tom > 0 else 'mais escura'} ({tom:+.1f} níveis)"
    if costura > COSTURA_MAXIMA:
        return f"costura na borda da seleção (degrau de {costura:.1f} níveis)"
    if g0 >= 0.8 and grao < GRAO_MINIMO:  # papel liso de verdade (PDF) não tem grão a perder
        return f"papel liso (grão {grao:.2f} do papel em volta)"
    return None


def apagar_no_documento(d: Documento, ferramenta: str, rec: np.ndarray, sel: np.ndarray, velho: np.ndarray,
                        proibido: np.ndarray, linha, h: float, rng: random.Random,
                        escala: float = 1.0) -> tuple[np.ndarray, dict]:
    """Apaga a seleção ``sel`` no documento aberto como a pessoa faz, olhando o resultado: a ``ferramenta``; nos pincéis,
    uma repassada se sobrou fantasma da tinta ``velho`` (mais de 25 níveis abaixo do papel); se a ferramenta não deu
    conta (sem papel livre, fantasma) ou o papel refeito ficou manchado ou liso (``_manchou``), volta ao começo e refaz
    com o Carimbo. Devolve (imagem apagada, relatório: a ferramenta que ficou, os passos, o que refez)."""
    inicio = d.marcar("antes de apagar")
    papel = float(np.percentile(rec.astype(np.float32) @ _LUMA, 90))
    tentativas = [ferramenta] + (["carimbo"] if ferramenta not in ("carimbo", "cor") else [])
    refez = None
    for n, ferr in enumerate(tentativas):
        ultima = n == len(tentativas) - 1
        try:
            passos, sobre = apagador(ferr, rec, sel, proibido, linha, h, rng, escala)
            d.rodar(passos_selecao(sel) + passos + [("select.deselect", {})])
            for volta in range(2):
                out = d.imagem()
                resto = velho & ((out.astype(np.float32) @ _LUMA) < papel - 25)
                if resto.sum() <= max(4, 0.005 * velho.sum()):
                    break
                if volta or ferr not in PINCEIS:
                    raise ErroOperacao(f"o apagador ({DIZ_APAGADOR[ferr]}) deixou {int(resto.sum())} px do texto antigo")
                de_novo, _ = apagador(ferr, rec, sel, proibido, linha, h, rng, escala)
                d.rodar(passos_selecao(sel) + de_novo + [("select.deselect", {})])
                sobre["repassou"] = True
        except ErroOperacao as e:
            if ultima:
                raise
            refez = {"de": ferr, "porque": str(e)}
            d.voltar(inicio)
            continue
        problema = None if ferr == "cor" else _manchou(avaliar_papel(out, rec, sel, proibido, h))
        if problema and not ultima:
            refez = {"de": ferr, "porque": problema}
            d.voltar(inicio)
            continue
        if problema:  # nem o Carimbo deixou o papel bom: melhor não editar do que deixar a mancha
            raise ErroOperacao(f"o papel refeito ficou com {problema}")
        sobre = {"ferramenta": ferr, "passos": [passos_selecao(sel)[0][0], passos[0][0]], **sobre}
        if refez:
            sobre["refez"] = refez
        return out, sobre
    raise ErroOperacao("nenhum apagador deu conta")


def reconstruir(rec: np.ndarray, buraco: np.ndarray, alvo: np.ndarray, ferramenta: str, semente: int,
                altura: float) -> tuple[np.ndarray, dict]:
    """O papel refeito no ``alvo`` (bool) com a ``ferramenta`` do PhotoCraft — a mesma conversa do
    ``ops.apagar.reconstruir``: ``buraco`` é tudo que não serve de origem (a tinta do recorte, dilatada, e o alvo).
    Devolve (recorte refeito, relatório: ``apagar_no_documento``)."""
    if ferramenta not in APAGADORES:
        raise ErroOperacao(f"ferramenta de apagar desconhecida: {ferramenta}")
    rec = np.ascontiguousarray(rec)
    ys, xs = np.nonzero(alvo)
    if not ys.size:
        return rec.copy(), {"ferramenta": ferramenta}
    proibido = cv2.dilate((buraco | alvo).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    linha = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    with Documento(rec) as d:
        return apagar_no_documento(d, ferramenta, rec, alvo, alvo & mascara_tinta(rec).astype(bool), proibido, linha,
                                   float(max(4.0, altura)), random.Random(semente ^ 0xA9A6))


def _pedacos(sel: np.ndarray, h: float, rng: random.Random) -> list[np.ndarray]:
    """A seleção cortada em pedaços na horizontal, de 3 a 6 alturas de letra cada (o Remendo feito aos poucos)."""
    xs = np.flatnonzero(sel.any(0))
    x, fim, out = int(xs[0]), int(xs[-1]) + 1, []
    while x < fim:
        b = min(fim, x + int(rng.uniform(3.0, 6.0) * h))
        parte = np.zeros_like(sel)
        parte[:, x:b] = sel[:, x:b]
        if parte.any():
            out.append(parte)
        x = b
    return out


# ============================================================================ texto e medidas
def passos_texto(fam: str, est: str, s: str, x: float, y: float, tam: float, cor: str, largura: float = 100.0,
                 entreletra: float = 0.0, suavizacao: str = "smooth", rotacao: float = 0.0) -> list:
    """A camada de texto: ponto em (``x``, ``y``) na linha de base, ``tam`` em pt (= px a 72 ppi), ``largura`` em %
    (escala horizontal), ``entreletra`` em px (vira o tracking, em milésimos de eme) e ``rotacao`` em graus (a mesma
    convenção do ``ops.texto``: a linha gira de ``-rotacao`` em torno do começo dela)."""
    p = {"x": round(x, 2), "y": round(y, 2), "text": s, "font": fam, "fontStyle": est, "size": round(tam, 2),
         "color": cor}
    if abs(largura - 100.0) > 0.05:
        p["horizontalScale"] = round(largura, 2)
    if abs(entreletra) > 0.01:
        p["tracking"] = round(1000.0 * entreletra / max(1.0, tam), 1)
    passos = [("type.create", p), (f"type.antiAlias.{suavizacao}", {})]
    if abs(rotacao) > 0.01:
        a = math.radians(-rotacao)
        passos.append(("type.edit", {"transform": [math.cos(a), math.sin(a), -math.sin(a), math.cos(a),
                                                   round(x, 2), round(y, 2)]}))
    return passos


def passos_peso(f: float, camada) -> list:
    """O peso do traço em fração de pixel, na camada de texto já rasterizada: uma cópia com Mínimo (afina, ``f`` < 0)
    ou Máximo (engrossa) de 1 px. Engrossar: a cópia por cima com opacidade ``f``. Afinar: a cópia afinada por cima,
    inteira, e a original embaixo com opacidade 1 − |f| (o contorno dela some na proporção)."""
    if abs(f) < 0.03:
        return []
    filtro = "filter.other.maximum" if f > 0 else "filter.other.minimum"
    p = [("layer.duplicate", {}), (filtro, {"radius": 0.5})]
    if f > 0:
        return p + [("layer.setProps", {"opacity": round(min(1.0, f), 3)})]
    return p + [("layer.setProps", {"layer": camada, "opacity": round(max(0.0, 1.0 + f), 3)})]


def _alfa(rec: np.ndarray) -> np.ndarray:
    lum = rec.astype(np.float32) @ _LUMA
    papel, tinta = float(np.percentile(lum, 90)), float(np.percentile(lum, 2))
    return np.clip((papel - lum) / max(1.0, papel - tinta), 0, 1)


def medir_tinta(rec: np.ndarray) -> dict:
    """Nitidez (``pericia._nitidez``) e tom do miolo (luminância mediana onde o alfa passa de 0,8) da tinta do
    recorte."""
    a = _alfa(rec)
    lum = rec.astype(np.float32) @ _LUMA
    miolo = a > 0.8
    return {"nitidez": _nitidez(lum, a > 0.3), "miolo": float(np.median(lum[miolo])) if miolo.any() else float("nan")}


def tinta_nova(depois: np.ndarray, antes: np.ndarray) -> tuple[int, int, int, int] | None:
    """Caixa (x0, y0, x1, y1) da tinta que entrou: escura e diferente do papel apagado."""
    lum = depois.astype(np.float32) @ _LUMA
    papel = float(np.percentile(antes.astype(np.float32) @ _LUMA, 90))
    m = (np.abs(depois.astype(np.int16) - antes.astype(np.int16)).max(2) > 20) & (lum < papel - 40)
    if not m.any():
        return None
    ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def _desfoque_casado(rec_nitido: np.ndarray, alvo: float) -> float:
    """O raio do Desfoque Gaussiano (simulado aqui; o motor aplica o mesmo) que deixa a nitidez da tinta nova mais
    perto da do texto escaneado; 0 se ela já é tão macia quanto ele."""
    agora = medir_tinta(rec_nitido)["nitidez"]
    if alvo <= 0 or agora <= alvo * 1.05:
        return 0.0
    return float(min(np.arange(0.2, 2.01, 0.1),
                     key=lambda r: abs(medir_tinta(cv2.GaussianBlur(rec_nitido, (0, 0), float(r)))["nitidez"] - alvo)))


# ============================================================================ o campo inteiro
def reescrever(c, novo: str, alin: str, ferramenta: str, entreletra: float, medir_peso) -> tuple[Resultado, dict]:
    """Troca o campo de ``c`` (``ops.tecnicas._C``) por ``novo`` no PhotoCraft e devolve (Resultado no recorte,
    relatório). Sem operador humano: seleção só da tinta, a ``ferramenta`` de apagar, o texto alinhado pela ``alin``
    (esquerda, direita, centro) na fonte identificada e o acabamento MEDIDO (desfoque, tom e peso iguais aos do
    texto escaneado). Com o operador humano (``c.humano``): a seleção, a ferramenta, a suavização, a cor do
    conta-gotas, o erro de posição e de escala, o desfoque e os retoques DELE, sem medida nenhuma.
    ``medir_peso(Resultado) -> (antigo, novo) | None``: a espessura do traço (``tecnicas._razao_peso``)."""
    from .ops import auto  # tardio: rotulador.ops importa as técnicas, que importam este módulo

    x0, y0, x1, y1 = c.caixa
    h = float(max(4, y1 - y0))
    aj, hum = c.aj, c.humano
    rng = random.Random(c.semente ^ 0x9C0F7)

    def largura(s):
        a = c.medir(s)
        return a[2] - a[0]

    extra = max(0.0, largura(novo) - largura(c.texto))
    my, mx = int(max(40, 3.5 * h)), int(max(40, 3 * h + extra))
    X0, Y0, X1, Y1 = max(0, x0 - mx), max(0, y0 - my), min(c.W, x1 + mx), min(c.H, y1 + my)
    rec = np.ascontiguousarray(c.img[Y0:Y1, X0:X1])
    velho = auto._buraco_tinta(c.img, x0, y0, x1, y1, c.desfoque)
    if not velho[Y0:Y1, X0:X1].any():
        raise ErroOperacao("não achei a tinta do texto antigo")
    info, forma, escala = {}, "tinta", 1.0
    if hum is not None:
        hsel = {"forma": hum["selecao"], "margem": hum.get("margem"), "zoom": hum.get("zoom")}
        sel = auto._sel_humana(c.img, velho, c.caixa, hsel, c.semente).completa_bool()[Y0:Y1, X0:X1]
        info, forma = hsel.get("_info") or {}, hum["selecao"]
        escala = 1.0 / max(0.25, float(hum.get("zoom") or 1.0))
    else:
        sel = velho[Y0:Y1, X0:X1]
    velho = velho[Y0:Y1, X0:X1]
    tx0, ty0, tx1, ty1 = ocr.caixa_tinta(c.img, c.caixa, 0)
    linha = (tx0 - X0, ty0 - Y0, tx1 - X0, ty1 - Y0)
    proibido = (cv2.dilate(mascara_tinta(rec).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0) | \
               (cv2.dilate(sel.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)

    # o texto: tamanho, cor, suavização e desfoque pelo cuidado (medidos) ou pelos hábitos da pessoa
    tam = float(aj["tamanho"])
    cor = aj["cor"]
    suav, desf, dx_h, dy_h = "smooth", None, 0, 0
    t = c.hum_texto if hum is not None else None
    if t is not None:
        tam *= float(t["escala"])
        suav = SUAVIZACAO.get(t["rasterizador"], "sharp")
        desf, dx_h, dy_h = float(t["desfoque"]), int(t["dx"]), int(t["dy"])
        ch = c.cor_humana()
        if ch is not None:
            cor = _hex(ch)
    larg_pc = float(aj.get("largura", 100) or 100)
    rot = float(aj.get("rotacao") or 0.0) + (float(t.get("rotacao", 0.0)) if t is not None else 0.0)
    xa, ya = float(aj["x"]) - X0, float(aj["y"]) - Y0
    sobre = {"selecao": forma, "passos": []}
    with Documento(rec) as d:
        fam, est = d.m.face(aj["fonte"])
        apagado, sobre_apagar = apagar_no_documento(d, ferramenta, rec, sel, velho, proibido, linha, h, rng, escala)
        sobre["passos"] += sobre_apagar.pop("passos")
        marca = d.marcar("apagado")

        def texto(x, y, cor_, raio, f_peso):
            ps = passos_texto(fam, est, novo, x, y, tam, cor_, larg_pc, entreletra, suav, rot)
            camada = d.rodar(ps)[0]["layer"]
            extra_ps = []
            if raio > 0.05 or abs(f_peso) >= 0.03:
                extra_ps.append(("layer.rasterize.type", {}))
            if raio > 0.05:
                extra_ps.append(("filter.blur.gaussianBlur", {"radius": round(raio, 2)}))
            extra_ps += passos_peso(f_peso, camada)
            d.rodar(extra_ps)
            return [p[0] for p in ps + extra_ps]

        # 1ª camada nítida, para medir onde a tinta nova cai (o alinhamento) e, sem a pessoa, o desfoque e o tom
        texto(xa, ya, cor, 0.0, 0.0)
        nitido = d.imagem()
        caixa_nova = tinta_nova(nitido, apagado)
        if caixa_nova is None:
            raise ErroOperacao("o texto novo não apareceu no PhotoCraft")
        nx0, ny0, nx1, ny1 = caixa_nova
        lx0, _, lx1, _ = linha
        dx = {"esquerda": lx0 - nx0, "direita": lx1 - nx1}.get(alin, (lx0 + lx1 - nx0 - nx1) / 2)
        if hum is not None:  # a pessoa alinha no olho, com a camada no pixel inteiro, e erra do jeito dela
            x, y = float(round(xa + dx) + dx_h), float(round(ya) + dy_h)
            raio, cor_f, f_peso = desf or 0.0, cor, 0.0
        else:
            x, y = xa + dx, ya
            orig = medir_tinta(rec[max(0, linha[1] - 2):linha[3] + 2, max(0, linha[0] - 2):linha[2] + 2])
            reg = (slice(max(0, ny0 - 2), ny1 + 2), slice(max(0, nx0 - 2), nx1 + 2))
            raio = _desfoque_casado(nitido[reg], orig["nitidez"])
            sim = cv2.GaussianBlur(nitido, (0, 0), raio) if raio > 0 else nitido
            novo_m = medir_tinta(sim[reg])
            cor_f = cor
            if np.isfinite(orig["miolo"]) and np.isfinite(novo_m["miolo"]):
                cor_f = _hex(np.array([int(cor[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
                             + 1.3 * (orig["miolo"] - novo_m["miolo"]))
            f_peso = 0.0
        d.voltar(marca)
        sobre["passos"] += texto(x, y, cor_f, raio, f_peso)
        final = d.imagem()
        if hum is None:  # o peso do traço: medido no resultado; fora, uma cópia afinada/engrossada inteira para medir
            # a resposta, e a mistura na fração que acerta
            pesos = medir_peso(_resultado(c, rec, final, X0, Y0))
            r0 = pesos[1] / pesos[0] if pesos else 1.0
            if abs(r0 - 1) > PESO_ACERTAR:
                d.voltar(marca)
                texto(x, y, cor_f, raio, -1.0 if r0 > 1 else 1.0)
                cheio = medir_peso(_resultado(c, rec, d.imagem(), X0, Y0))
                r1 = cheio[1] / cheio[0] if cheio else r0
                if abs(r1 - r0) > 1e-3:
                    f_peso = float(np.clip((1 - r0) / (r1 - r0), 0, 1)) * (-1.0 if r0 > 1 else 1.0)
                    d.voltar(marca)
                    sobre["passos"] = sobre["passos"][:2] + texto(x, y, cor_f, raio, f_peso)
                    final = d.imagem()
        else:
            ret = _passos_retoque(c, hum, sel, caixa_nova, (x - xa, y - ya), rec.shape[:2])
            if ret:
                d.rodar([("layer.flattenImage", {})] + ret[0])
                final = d.imagem()
                sobre["retoques"] = ret[1]
        sobre.update(fonte=f"{fam} {est}", tamanho_pt=round(tam, 2), cor=cor_f, desfoque=round(raio, 2),
                     peso=round(f_peso, 3), suavizacao=suav, posicao=[round(x + X0, 2), round(y + Y0, 2)],
                     **sobre_apagar, **({"gesto": info.get("gesto")} if info.get("gesto") else {}))
    return _resultado(c, rec, final, X0, Y0), sobre


def _passos_retoque(c, hum: dict, sel: np.ndarray, caixa_nova, deslocado, forma) -> tuple[list, list] | None:
    """Os retoques da pessoa na área editada (a seleção dela mais a caixa do texto novo), na imagem achatada, na ordem
    do ``tecnicas._acabar_humano``: o Desfoque Gaussiano de quem desfoca à mão, aguçar, Desfoque de Superfície e, por
    último, Adicionar Ruído."""
    r = hum.get("retoques") or {}
    ps, feitos = [], []
    if hum.get("nitidez") == "manual":
        s = round(float(np.clip(c.desfoque * float(hum.get("excesso", 1.2)) + 0.05, 0.3, 1.0)), 2)
        ps.append(("filter.blur.gaussianBlur", {"radius": s}))
        feitos.append(f"desfocar ({s} px)")
    if "nitidez" in r:
        ps.append(("filter.sharpen.unsharpMask", {"amount": int(round(100 * float(r["nitidez"]))), "radius": 0.8,
                                                  "threshold": 0}))
        feitos.append("nitidez")
    if "superficie" in r:
        ps.append(("filter.blur.surfaceBlur", {"radius": int(r["superficie"]["raio"]),
                                               "threshold": int(r["superficie"]["limiar"])}))
        feitos.append("superficie")
    if "ruido" in r:
        q = r["ruido"]
        ps.append(("filter.noise.addNoise", {"amount": float(q["quantidade"]),
                                             "distribution": "uniform" if q.get("distribuicao") == "uniforme" else "gaussian",
                                             "monochromatic": bool(q.get("monocromatico")),
                                             "seed": int(c.semente % 2 ** 31)}))
        feitos.append("ruido")
    if not ps:
        return None
    area = sel.copy()
    nx0, ny0, nx1, ny1 = caixa_nova
    ddx, ddy = (int(round(v)) for v in deslocado)
    H, W = forma
    area[max(0, ny0 + ddy - 2):min(H, ny1 + ddy + 2), max(0, nx0 + ddx - 2):min(W, nx1 + ddx + 2)] = True
    return passos_selecao(area) + ps + [("select.deselect", {})], feitos


def _resultado(c, rec: np.ndarray, final: np.ndarray, X0: int, Y0: int) -> Resultado:
    """O recorte que o motor devolveu como Resultado, justo no que mudou (com 4 px de folga): a pegada é tudo que
    mudou (o PNG volta sem perda: fora da edição, os pixels são os mesmos). Justo também para medir o peso do traço:
    num recorte grande a tinta passa a ser menos de 3% e a medida (p3 = tinta) via papel."""
    if final.shape != rec.shape:
        raise ErroOperacao("o PhotoCraft devolveu um recorte de outro tamanho")
    pegada = np.abs(final.astype(np.int16) - rec.astype(np.int16)).max(2) > 0
    if not pegada.any():
        raise ErroOperacao("o PhotoCraft não mudou nada")
    H, W = pegada.shape
    ys, xs = np.flatnonzero(pegada.any(1)), np.flatnonzero(pegada.any(0))
    a0, b0 = max(0, int(ys[0]) - 4), min(H, int(ys[-1]) + 5)
    a1, b1 = max(0, int(xs[0]) - 4), min(W, int(xs[-1]) + 5)
    return Resultado(Y0 + a0, Y0 + b0, X0 + a1, X0 + b1, np.ascontiguousarray(final[a0:b0, a1:b1]),
                     pegada[a0:b0, a1:b1].copy(), meta={})
