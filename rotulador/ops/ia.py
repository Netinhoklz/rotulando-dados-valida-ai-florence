"""Edição por IA (Qwen-Image-Edit-2511) da área selecionada.

O modelo roda em outro processo (ia_servidor.py, no python com CUDA). Aqui:
  1. recorta a seleção + uma margem de contexto;
  2. amplia o recorte para ~1 MP com lados múltiplos de 32 (texto pequeno de
     documento fica ilegível para o modelo se não ampliar);
  3. manda para o servidor e recebe a edição;
  4. reduz de volta e compõe SÓ dentro da seleção. O VAE muda cor e posição na
     imagem inteira; fora da seleção nada pode mudar, senão a máscara mente.

Como a saída volta (``colagem``): "selecao" (padrão, recorte exato), "suave" (borda de 1–4 px) ou
"contexto" (um retângulo em volta da seleção, regenerado inteiro). Nos dois últimos a região é a
área colada inteira; a máscara de pixels alterados continua sendo só o que mudou.
Treinar só com o recorte exato ensina o detector a procurar a costura e falha em regiões regeneradas
inteiras (TGIF, GIFL; o AIForge-Doc cola o recorte de contexto inteiro).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

from ..selecao import Selecao
from ..sessao import ErroOperacao, Resultado
from .base import compor, exigir_selecao, inteiro, num

URL = os.environ.get("ROTULADOR_IA_URL", "http://127.0.0.1:5051")
MULTIPLO = 32


def _pedir(rota: str, dados: dict | None = None, timeout: float = 2.0) -> dict:
    corpo = None if dados is None else json.dumps(dados).encode()
    req = urllib.request.Request(URL + rota, data=corpo, method="GET" if dados is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def status() -> dict:
    try:
        return {"online": True, **_pedir("/status", timeout=1.5)}
    except (urllib.error.URLError, OSError, ValueError):
        return {"online": False}


def descarregar() -> dict:
    try:
        return _pedir("/descarregar", {}, timeout=30)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ErroOperacao(f"servidor de IA indisponível: {e}")


def progresso() -> dict:
    """Em que passo está a edição em curso (etapa, passo, passos, segundos, estimativa)."""
    try:
        return {"online": True, **_pedir("/progresso", timeout=1.5)}
    except (urllib.error.URLError, OSError, ValueError):
        return {"online": False}


def cancelar() -> dict:
    """Pede para a edição em curso parar no próximo passo da difusão."""
    try:
        return _pedir("/cancelar", {}, timeout=5)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ErroOperacao(f"servidor de IA indisponível: {e}")


def tamanho_modelo(h: int, w: int, megapixels: float) -> tuple[int, int]:
    """Lados múltiplos de 32 com área ~``megapixels`` mantendo a proporção."""
    escala = (megapixels * 1e6 / (h * w)) ** 0.5
    H = max(MULTIPLO, int(round(h * escala / MULTIPLO)) * MULTIPLO)
    W = max(MULTIPLO, int(round(w * escala / MULTIPLO)) * MULTIPLO)
    return H, W


def caixa_contexto(sel: Selecao, margem: float, H: int, W: int) -> tuple[int, int, int, int]:
    y0, y1, x0, x1 = sel.bbox
    m = int(max(32, margem * max(y1 - y0, x1 - x0)))
    return max(0, y0 - m), min(H, y1 + m), max(0, x0 - m), min(W, x1 + m)


def _igualar_tom(orig: np.ndarray, saida: np.ndarray, alpha: np.ndarray, banda: int | None = None):
    """A IA devolve o papel com um tom um pouco diferente: colada só na seleção, a região vira um
    retângulo claro/escuro visível. Na faixa da borda da seleção (onde é papel, sem tinta) a saída
    deveria ser igual ao original; a diferença mediana é somada à saída inteira. Diferença grande
    demais (> 40 níveis) não é tom: a IA mudou o fundo de propósito, e fica como está."""
    dentro = (alpha > 0.5).astype(np.uint8)
    h, w = dentro.shape
    b = banda or max(2, min(h, w) // 8)
    dist = cv2.distanceTransform(np.pad(dentro, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    from ..selecao import mascara_tinta
    tinta = cv2.dilate(mascara_tinta(orig), np.ones((5, 5), np.uint8)).astype(bool)
    faixa = (dist > 0) & (dist <= b) & ~tinta
    if faixa.sum() < 20:
        return saida, None
    dif = np.median(orig[faixa].astype(np.float32) - saida[faixa].astype(np.float32), axis=0)
    if np.abs(dif).max() > 40:
        return saida, None
    return np.clip(saida.astype(np.float32) + dif + 0.5, 0, 255).astype(np.uint8), [round(float(v), 1) for v in dif]


def editar(img: np.ndarray, p: dict, sel: Selecao, editor=None, **_) -> Resultado:
    """``editor(recorte_rgb, prompt, semente, passos) -> rgb`` substitui o servidor nos testes."""
    exigir_selecao(sel)
    prompt = str(p.get("prompt", "")).strip()
    if not prompt:
        raise ErroOperacao("escreva o que a IA deve fazer na área selecionada")
    H, W = img.shape[:2]
    cy0, cy1, cx0, cx1 = caixa_contexto(sel, num(p, "contexto", 0.5, 0, 3), H, W)
    recorte = img[cy0:cy1, cx0:cx1]
    mh, mw = tamanho_modelo(cy1 - cy0, cx1 - cx0, num(p, "megapixels", 1.0, 0.25, 2.0))
    entrada = cv2.resize(recorte, (mw, mh), interpolation=cv2.INTER_LANCZOS4 if mh > recorte.shape[0] else cv2.INTER_AREA)
    semente = inteiro(p, "semente", int(time.time()) % 100000, 0, 2**31 - 1)
    passos = inteiro(p, "passos", 0, 0, 80) or None  # vazio: o servidor usa o padrão do modelo
    modelo = str(p.get("modelo") or "") or None
    inicio = time.perf_counter()
    if editor is not None:
        saida = editor(entrada, prompt, semente, passos)
        info = {"modelo": "teste"}
    else:
        png = cv2.imencode(".png", cv2.cvtColor(entrada, cv2.COLOR_RGB2BGR))[1].tobytes()
        try:
            r = _pedir("/editar", {"imagem": base64.b64encode(png).decode(), "prompt": prompt, "semente": semente,
                                   "passos": passos, "altura": mh, "largura": mw, "modelo": modelo}, timeout=3600)
        except urllib.error.HTTPError as e:
            corpo = e.read().decode(errors="replace")
            try:
                corpo = json.loads(corpo).get("erro", corpo)
            except ValueError:
                pass
            raise ErroOperacao(f"IA falhou: {corpo[:400]}")
        except (urllib.error.URLError, OSError) as e:
            raise ErroOperacao(f"servidor de IA indisponível ({URL}); rode: .venv-ia\\Scripts\\python.exe ia_servidor.py  [{e}]")
        buf = np.frombuffer(base64.b64decode(r["imagem"]), np.uint8)
        saida = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        info = r.get("info", {})
    saida = np.asarray(saida, np.uint8)
    if saida.ndim != 3 or saida.shape[2] != 3:
        raise ErroOperacao("a IA devolveu uma imagem inválida")
    volta = cv2.resize(saida, (cx1 - cx0, cy1 - cy0),
                       interpolation=cv2.INTER_AREA if saida.shape[0] > (cy1 - cy0) else cv2.INTER_CUBIC)
    y0, y1, x0, x1 = sel.bbox
    colagem = str(p.get("colagem") or "selecao")
    meta = {"prompt": prompt, "semente": semente, "passos": passos, "modelo_id": modelo, "entrada_modelo": [mw, mh],
            "contexto": [cx0, cy0, cx1, cy1], "segundos": round(time.perf_counter() - inicio, 1), "colagem": colagem,
            **info}
    if colagem in ("suave", "contexto"):
        orig = img[cy0:cy1, cx0:cx1]
        a_sel = np.zeros(orig.shape[:2], np.float32)
        a_sel[y0 - cy0:y1 - cy0, x0 - cx0:x1 - cx0] = sel.alpha
        saida, tom = _igualar_tom(orig, volta, a_sel, banda=max(2, min(y1 - y0, x1 - x0) // 8))
        meta["tom_ajustado"] = tom
        if colagem == "suave":
            r = inteiro(p, "pena", 2, 1, 4)
            a = cv2.GaussianBlur(cv2.dilate((a_sel > 0).astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8))
                                 .astype(np.float32), (0, 0), r / 2)
            a = np.maximum(a, a_sel)
            saida = (orig * (1 - a[..., None]) + saida * a[..., None] + 0.5).astype(np.uint8)
            meta["pena"] = r
            # a pegada é a área colada inteira (o que veio da IA); "mascara" continua sendo só o que mudou
            return Resultado(cy0, cy1, cx0, cx1, saida, a > 0.01, meta=meta)
        # contexto: um retângulo em volta da seleção (folga de uma altura, mín. 16 px) vem inteiro da
        # IA (como o AIForge-Doc). O recorte de contexto todo pegaria os campos vizinhos.
        f = max(16, y1 - y0)
        ry0, ry1 = max(cy0, y0 - f), min(cy1, y1 + f)
        rx0, rx1 = max(cx0, x0 - f), min(cx1, x1 + f)
        meta["regiao_colada"] = [rx0, ry0, rx1, ry1]
        rec = saida[ry0 - cy0:ry1 - cy0, rx0 - cx0:rx1 - cx0]
        return Resultado(ry0, ry1, rx0, rx1, rec, np.ones(rec.shape[:2], bool), meta=meta)
    saida_sel, tom = _igualar_tom(img[y0:y1, x0:x1], volta[y0 - cy0:y1 - cy0, x0 - cx0:x1 - cx0], sel.alpha)
    novo = compor(img[y0:y1, x0:x1], saida_sel, sel.alpha)
    meta["tom_ajustado"] = tom
    return Resultado(y0, y1, x0, x1, novo, sel.alpha > 0, meta=meta)
