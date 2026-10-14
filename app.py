"""Rotulador de fraude documental (comprovantes de endereço).

Rode com:  python app.py
  --dados PASTA     entrada com treino/ teste/ validacao/   (padrão: ./dados)
  --saida PASTA     saída, separada por split                (padrão: ./saida)
  --dpi N           resolução da rasterização de PDF         (padrão: 200)
  --porta N         porta HTTP                               (padrão: 5000 ou $PORT)
  --sem-navegador   não abre o navegador

O servidor recusa subir se achar o mesmo documento em dois splits.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import re
import sys
import threading
import time
import webbrowser
from collections import Counter, OrderedDict
from pathlib import Path

try:
    import cv2
    import numpy as np
    from flask import Flask, jsonify, render_template, request, send_file
    from PIL import Image
except ModuleNotFoundError as erro:
    sys.exit(f"Falta a dependência '{erro.name}'. Instale com:  pip install -r requirements.txt")

from rotulador import (automatico, canal, campos as mod_campos, catalogo, documentos, entradas, exportar, falsificador,
                       identificar_fonte, ocr, perito, qualidade, selecao)
from rotulador.contexto import Contexto, cache_ocr
from rotulador.equipe import ANONIMO, Equipe, Travas, operador_da_requisicao, progresso
from rotulador.gerador import (CATEGORIAS_FOCO, PESOS_CATEGORIA, Gerador, Plano, _perito_por_edicao, aplicar_receita,
                               editar_campos, receita_profissional)
from rotulador.gerador import estilos as estilos_prontos, humano_da_sessao
from rotulador.ops import COM_PREVIA, executar, geradores, ia, texto
from rotulador.registro import SPLITS, ErroVazamento, Registro
from rotulador.sessao import ErroOperacao, Sessao

RAIZ = Path(__file__).resolve().parent
MAX_SESSOES = 6


class TravaOcupada(ErroOperacao):
    """Edição num documento que outra pessoa está editando (HTTP 409)."""

    def __init__(self, msg: str, trava: dict):
        super().__init__(msg)
        self.trava = trava


def carregar_categorias(caminho: Path) -> list[dict]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))["categorias"]
    ids, chaves = set(), set()
    for c in dados:
        if not (1 <= int(c["id"]) <= 255) or c["id"] in ids or c["chave"] in chaves:
            raise ValueError(f"categoria inválida ou repetida em {caminho}: {c}")
        ids.add(c["id"])
        chaves.add(c["chave"])
    return dados


def png(img: np.ndarray) -> bytes:
    if img.ndim == 3 and img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    elif img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA)
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return buf.tobytes()


def b64png(img: np.ndarray) -> str:
    return base64.b64encode(png(img)).decode()


def criar_app(registro: Registro, raiz_saida: Path, categorias: list[dict]) -> Flask:
    app = Flask(__name__, template_folder=str(RAIZ / "templates"), static_folder=str(RAIZ / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
    app.config["TEMPLATES_AUTO_RELOAD"] = True  # HTML novo junto com JS novo, sem reiniciar
    raiz_saida = Path(raiz_saida).resolve()
    ids_categoria = {c["chave"]: int(c["id"]) for c in categorias}
    sessoes: OrderedDict[str, Sessao] = OrderedDict()
    lock_sessoes = threading.Lock()
    app.registro = registro
    app.sessoes = sessoes
    gerador = Gerador(registro, raiz_saida, ids_categoria)
    app.gerador = gerador
    travas = Travas()
    equipe = Equipe(raiz_saida)
    app.travas = travas
    lendo_ocr: set[str] = set()  # documentos com OCR em segundo plano (pré-leitura)

    # ------------------------------------------------------------------ apoio
    def item_ou_404(doc: str):
        try:
            return registro.item(str(doc))
        except KeyError:
            raise ErroOperacao("documento não encontrado")

    def split_de(doc) -> str | None:
        it = registro.itens.get(str(doc or ""))
        return it.split if it else None

    def sessao(doc: str) -> Sessao:
        it = item_ou_404(doc)
        with lock_sessoes:
            if it.doc in sessoes:
                sessoes.move_to_end(it.doc)
                return sessoes[it.doc]
        base = registro.carregar(it.doc)
        s = Sessao(it, base, raiz_saida / it.split / "_rascunhos" / it.doc, ids_categoria)
        with lock_sessoes:
            s = sessoes.setdefault(it.doc, s)
            while len(sessoes) > MAX_SESSOES:
                sessoes.popitem(last=False)  # o rascunho já está em disco
        return s

    def contexto(s: Sessao) -> Contexto:
        """Campos, OCR, letras e doadores (mesmo split) do documento: as técnicas automáticas usam."""
        ctx = getattr(s, "contexto", None)
        if ctx is None:
            ctx = s.contexto = Contexto(s.item, s.base, raiz_saida, registro)
        return ctx

    def operador() -> str:
        return operador_da_requisicao(request)

    def exigir_trava(s: Sessao) -> None:
        """Só quem está com o documento edita; quem abre um documento livre fica com ele."""
        t = travas.conflito(s.item.doc, operador())
        if t:
            raise TravaOcupada(f"'{s.item.nome}' está em edição por {t['operador']} (desde "
                               f"{time.strftime('%H:%M', time.localtime(t['desde']))}). Assuma o documento para editar.",
                               {**t, "doc": s.item.doc})

    def corpo() -> dict:
        d = request.get_json(silent=True)
        if not isinstance(d, dict):
            raise ErroOperacao("requisição sem JSON")
        return d

    def estado_sessao(s: Sessao) -> dict:
        return {"id": s.item.doc, "split": s.item.split, "nome": s.item.nome, "largura": s.item.largura,
                "altura": s.item.altura, "versoes": exportar.versoes_salvas(raiz_saida, s.item.split, s.item.doc),
                **s.estado()}

    def carregador_doador(s: Sessao):
        def carregar(doc: str) -> np.ndarray:
            it = item_ou_404(doc)
            if it.split != s.item.split:  # nunca misturar splits, nem por colagem
                raise ErroOperacao(f"doador é do split '{it.split}', o documento é de '{s.item.split}': proibido")
            if it.doc == s.item.doc:
                return s.base
            return registro.carregar(it.doc)
        return carregar

    def contagem_versoes(split: str) -> dict[str, int]:
        cont: dict[str, int] = {}
        pasta = raiz_saida / split / "editadas"
        if pasta.is_dir():
            for arq in pasta.iterdir():
                doc, sep, resto = arq.name.partition("__v")
                if sep:
                    cont[doc] = cont.get(doc, 0) + 1
        return cont

    # ------------------------------------------------------------------ erros
    @app.errorhandler(ErroOperacao)
    @app.errorhandler(selecao.ErroSelecao)
    def erro_operacao(e):
        return jsonify(erro=str(e)), 400

    @app.errorhandler(TravaOcupada)
    def erro_trava(e):
        return jsonify(erro=str(e), travado_por=e.trava), 409

    @app.errorhandler(ocr.OCRIndisponivel)
    def erro_ocr(e):
        return jsonify(erro=str(e)), 503

    @app.errorhandler(ErroVazamento)
    def erro_vazamento(e):
        return jsonify(erro=str(e)), 409

    @app.errorhandler(413)
    def grande(_):
        return jsonify(erro="requisição grande demais"), 413

    # ------------------------------------------------------------------ páginas
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/gerador")
    def pagina_gerador():
        return render_template("gerador.html")

    @app.get("/api/estado")
    def api_estado():
        splits = {}
        for sp in SPLITS:
            cont = contagem_versoes(sp)
            splits[sp] = {"total": len(registro.por_split[sp]),
                          "feitos": sum(1 for it in registro.por_split[sp] if cont.get(it.doc))}
        return jsonify(splits=splits, categorias=categorias, fontes=list(texto.listar_fontes()),
                       fonte_padrao=texto.fonte_padrao(), formatos=list(exportar.FORMATOS),
                       com_previa=sorted(COM_PREVIA), avisos=registro.avisos, ia=ia.status())

    @app.get("/api/documentos")
    def api_documentos():
        sp = request.args.get("split", "")
        if sp not in SPLITS:
            raise ErroOperacao("split inválido")
        cont = contagem_versoes(sp)
        rasc = raiz_saida / sp / "_rascunhos"
        atrib, vivas, eu = equipe.atribuicoes(), travas.todas(), operador()
        return jsonify([{"id": it.doc, "nome": it.nome, "largura": it.largura, "altura": it.altura,
                         "versoes": cont.get(it.doc, 0), "rascunho": (rasc / it.doc / "estado.json").is_file(),
                         "responsavel": atrib.get(it.doc),
                         "em_edicao_por": vivas[it.doc]["operador"] if it.doc in vivas and vivas[it.doc]["operador"] != eu
                         else None}
                        for it in registro.por_split[sp]])

    @app.get("/api/doadores")
    def api_doadores():
        s = sessao(request.args.get("id", ""))
        return jsonify([{"id": it.doc, "nome": it.nome, "largura": it.largura, "altura": it.altura}
                        for it in registro.por_split[s.item.split] if it.doc != s.item.doc])

    @app.post("/api/abrir")
    def api_abrir():
        """Abre e pega a trava do documento. Se outra pessoa está com ele, abre só para olhar e diz quem
        é (``travado_por``); ``forcar`` assume o documento."""
        d = corpo()
        s = sessao(d.get("id", ""))
        t = travas.pegar(s.item.doc, operador(), forcar=bool(d.get("forcar")))
        return jsonify({**estado_sessao(s), "travado_por": t})

    @app.post("/api/trava/ping")
    def api_trava_ping():
        """Sinal de vida da página que está com o documento (a trava vence sem sinal)."""
        it = item_ou_404(corpo().get("id", ""))
        t = travas.pegar(it.doc, operador())
        return jsonify(ok=t is None, travado_por=t)

    @app.post("/api/trava/soltar")
    def api_trava_soltar():
        d = request.get_json(silent=True, force=True) or {}  # sendBeacon manda text/plain
        it = registro.itens.get(str(d.get("id", "")))
        if it:
            travas.soltar(it.doc, operador_da_requisicao(request) if not d.get("operador") else str(d["operador"])[:80])
        return jsonify(ok=True)

    @app.get("/api/img/<doc>/<tipo>.png")
    def api_img(doc, tipo):
        if tipo == "original":
            it = item_ou_404(doc)
            s = sessoes.get(it.doc)
            img = s.base if s else registro.carregar(it.doc)
        elif tipo == "atual":
            s = sessao(doc)
            with s.lock:
                img = s.atual.copy()
        else:
            raise ErroOperacao("tipo de imagem inválido")
        r = send_file(io.BytesIO(png(img)), mimetype="image/png")
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.get("/api/miniatura/<doc>.jpg")
    def api_miniatura(doc):
        it = item_ou_404(doc)
        cache = raiz_saida / "_cache" / "miniaturas" / f"{it.doc}.jpg"
        if not cache.is_file():
            img = registro.carregar(it.doc)
            lado = 360
            esc = lado / max(img.shape[:2])
            peq = cv2.resize(img, (max(1, int(img.shape[1] * esc)), max(1, int(img.shape[0] * esc))),
                             interpolation=cv2.INTER_AREA)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(cache), cv2.cvtColor(peq, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])
        return send_file(cache, mimetype="image/jpeg")

    @app.post("/api/selecao")
    def api_selecao():
        d = corpo()
        s = sessao(d.get("id", ""))
        with s.lock:
            sel = selecao.rasterizar(d.get("selecao"), s.atual)
        if sel.vazia:
            return jsonify(vazia=True)
        a = (sel.alpha * 255 + 0.5).astype(np.uint8)
        return jsonify(vazia=False, bbox=[sel.x0, sel.y0, sel.x1, sel.y1], pixels=int((a > 0).sum()), png=b64png(a))

    @app.post("/api/op")
    def api_op():
        d = corpo()
        s = sessao(d.get("id", ""))
        tipo = str(d.get("tipo", ""))
        previa = bool(d.get("previa"))
        if previa and tipo not in COM_PREVIA:
            raise ErroOperacao("operação sem prévia")
        params = d.get("params") or {}
        if not previa:
            exigir_trava(s)
        with s.lock:
            sel = selecao.rasterizar(d.get("selecao"), s.atual)
            chave = (s.item.doc, s.versao, json.dumps(d.get("selecao"), sort_keys=True))
            with geradores.usando(split=s.item.split):  # valores falsos do vocabulário do split
                res = executar(tipo, s.atual, params, sel, base=s.base, carregar_doador=carregador_doador(s),
                               chave_cache=chave, documento=contexto(s))
            if previa:
                antes = s.atual[res.y0:res.y1, res.x0:res.x1]
                rec = np.where(res.pegada[..., None], res.recorte, antes)
                return jsonify(bbox=[res.x0, res.y0, res.x1, res.y1], png=b64png(rec))
            sel_spec = d.get("selecao")
            registro_params = dict(params)
            if sel_spec and sel_spec.get("formas"):
                registro_params["selecao"] = sel_spec
            s.aplicar(tipo, d.get("categoria"), str(d.get("descricao", "")), registro_params, res,
                      camada=d.get("camada") or None)
            return recorte_composicao(s, (res.y0, res.y1, res.x0, res.x1))

    def recorte_composicao(s: Sessao, bb):
        """Resposta padrão: a caixa da composição que mudou + o estado da sessão."""
        if not bb or bb[1] <= bb[0] or bb[3] <= bb[2]:
            return jsonify(nada=True, estado=estado_sessao(s))
        y0, y1, x0, x1 = bb
        with s.lock:
            rec = s.atual[y0:y1, x0:x1].copy()
        return jsonify(bbox=[x0, y0, x1, y1], png=b64png(rec), estado=estado_sessao(s))

    @app.post("/api/desfazer")
    def api_desfazer():
        s = sessao(corpo().get("id", ""))
        exigir_trava(s)
        return recorte_composicao(s, s.desfazer())

    @app.post("/api/refazer")
    def api_refazer():
        s = sessao(corpo().get("id", ""))
        exigir_trava(s)
        return recorte_composicao(s, s.refazer())

    @app.post("/api/camada")
    def api_camada():
        d = corpo()
        s = sessao(d.get("id", ""))
        exigir_trava(s)
        acao = str(d.get("acao", ""))
        kw = {k: d[k] for k in ("nome", "categoria", "descricao", "visivel", "opacidade", "posicao") if k in d}
        if acao == "mover":  # deslocar o conteúdo da camada (ferramenta Mover sem seleção)
            return recorte_composicao(s, s.deslocar_camada(d.get("camada"), d.get("dx", 0), d.get("dy", 0)))
        return recorte_composicao(s, s.acao_camada(acao, d.get("camada") or None, **kw))

    @app.get("/api/camada_mini/<doc>/<cid>.png")
    def api_camada_mini(doc, cid):
        s = sessao(doc)
        cam = s.camadas.get(cid)
        if cam is None:  # pedido atrasado de uma camada que acabou de sumir (salvar, desfazer): imagem vazia
            r = send_file(io.BytesIO(png(np.zeros((1, 1, 4), np.uint8))), mimetype="image/png")
            r.headers["Cache-Control"] = "no-store"
            return r
        with s.lock:
            bb = cam.caixa()
            rec = np.zeros((1, 1, 4), np.uint8) if bb is None else cam.rgba[bb[0]:bb[1], bb[2]:bb[3]]
            esc = min(1.0, 96 / max(rec.shape[:2]))
            mini = cv2.resize(rec, (max(1, int(rec.shape[1] * esc)), max(1, int(rec.shape[0] * esc))),
                              interpolation=cv2.INTER_AREA)
        r = send_file(io.BytesIO(png(mini)), mimetype="image/png")
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.post("/api/descartar")
    def api_descartar():
        s = sessao(corpo().get("id", ""))
        exigir_trava(s)
        s.recomecar()
        return jsonify(estado_sessao(s))

    @app.post("/api/conta_gotas")
    def api_conta_gotas():
        d = corpo()
        s = sessao(d.get("id", ""))
        H, W = s.atual.shape[:2]
        try:
            x, y, r = int(float(d["x"])), int(float(d["y"])), int(d.get("raio", 1))
        except (KeyError, TypeError, ValueError):
            raise ErroOperacao("coordenadas inválidas")
        if not (0 <= x < W and 0 <= y < H):
            raise ErroOperacao("fora da imagem")
        r = max(0, min(10, r))
        with s.lock:
            area = s.atual[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1].reshape(-1, 3)
            c = np.median(area, axis=0).round().astype(int)
        return jsonify(cor="#%02x%02x%02x" % tuple(c))

    @app.post("/api/estimar_texto")
    def api_estimar_texto():
        d = corpo()
        s = sessao(d.get("id", ""))
        with s.lock:
            sel = selecao.rasterizar(d.get("selecao"), s.atual)
            fonte = d.get("fonte") or None
            return jsonify(texto.estimar(s.atual, sel, fonte))

    @app.post("/api/fonte/identificar")
    def api_fonte_identificar():
        """Fontes instaladas mais parecidas com o texto da seleção (ou da ``caixa``), com o texto antigo
        desenhado em cada uma para comparar a olho. ``texto``: o que está escrito (sem ele, lê com OCR)."""
        d = corpo()
        s = sessao(d.get("id", ""))
        with s.lock:
            img = s.atual.copy()
            if d.get("caixa"):
                try:
                    x0, y0, x1, y1 = (int(round(float(v))) for v in d["caixa"])
                except (TypeError, ValueError):
                    raise ErroOperacao("caixa inválida")
            else:
                sel = selecao.rasterizar(d.get("selecao"), s.atual)
                if sel.vazia:
                    raise ErroOperacao("selecione o texto cuja fonte quer identificar")
                y0, y1, x0, x1 = sel.bbox
        caixa = (max(0, min(x0, x1)), max(0, min(y0, y1)), min(s.W, max(x0, x1)), min(s.H, max(y0, y1)))
        txt = str(d.get("texto") or "").strip()[:200] or ocr.ler_trecho(img, caixa).strip()
        if not txt:
            raise ErroOperacao("não consegui ler o texto da seleção: digite o que está escrito e compare de novo")
        # voto do documento só se o OCR da página já existe (ler a página inteira agora demoraria)
        prior = identificar_fonte.votos_contexto(contexto(s)) if cache_ocr(raiz_saida, s.item.sha_pixels).is_file() else {}
        r = identificar_fonte.identificar(img, caixa, txt, prior=prior, top=6)
        a = identificar_fonte.amostras(img, r)
        itens = [{k: v for k, v in c.items() if k != "imagem"} | {"png": b64png(c["imagem"])} for c in a["itens"]]
        return jsonify(texto=txt, ranking=itens, original=b64png(a["original"]), cor=a["cor"], papel=a["papel"],
                       confianca=r["confianca"], margem=r["margem"], avaliadas=r["avaliadas"],
                       documento=[[k, v] for k, v in list(prior.items())[:5]])

    @app.get("/api/mascara/<doc>.png")
    def api_mascara(doc):
        s = sessao(doc)
        alterados, regiao, classes = s.mascaras()
        m = regiao if request.args.get("tipo") == "regiao" else alterados
        rgba = np.zeros(m.shape + (4,), np.uint8)
        rgba[m] = (255, 0, 80, 150)
        r = send_file(io.BytesIO(png(rgba)), mimetype="image/png")
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.post("/api/salvar")
    def api_salvar():
        d = corpo()
        s = sessao(d.get("id", ""))
        exigir_trava(s)
        try:
            q = int(d.get("qualidade", 95))
        except (TypeError, ValueError):
            raise ErroOperacao("qualidade inválida")
        op = str(d.get("operador") or "").strip() or (operador() if operador() != ANONIMO else "")
        with s.lock:  # as pistas de cada edição (perito), medidas como o gerador mede: dão a dificuldade da versão
            perito_eds = _perito_por_edicao(s)
        r = exportar.salvar(s, raiz_saida, str(d.get("formato", "png")), q, op[:80], registro.dpi,
                            extra={"perito_por_edicao": perito_eds})
        return jsonify(salvo=r, estado=estado_sessao(s))

    @app.get("/api/gerar")
    def api_gerar():
        with geradores.usando(split=split_de(request.args.get("id"))):
            return jsonify(geradores.gerar(request.args.get("categoria", "")))

    # ------------------------------------------------------------------ automático
    @app.get("/api/campos/<doc>")
    def api_campos(doc):
        it = item_ou_404(doc)
        s = sessoes.get(it.doc)
        img = s.base if s else registro.carregar(it.doc)
        t0 = time.perf_counter()
        lista = automatico.campos_do_documento(img, raiz_saida, it.sha_pixels)
        return jsonify(campos=lista, segundos=round(time.perf_counter() - t0, 2))

    @app.post("/api/campos/preler")
    def api_campos_preler():
        """Lê (OCR) os próximos documentos em segundo plano: quando a pessoa chegar neles, já está pronto."""
        ids = [str(i) for i in (corpo().get("ids") or [])][:3]

        def ler(docs):
            for doc in docs:
                it = registro.itens.get(doc)
                if it is None or cache_ocr(raiz_saida, it.sha_pixels).is_file():
                    continue
                try:
                    automatico.campos_do_documento(registro.carregar(it.doc), raiz_saida, it.sha_pixels)
                except Exception:  # noqa: BLE001 - pré-leitura é só conveniência
                    pass
                finally:
                    lendo_ocr.discard(doc)
        novos = [d for d in ids if d not in lendo_ocr]
        lendo_ocr.update(novos)
        if novos:
            threading.Thread(target=ler, args=(novos,), daemon=True).start()
        return jsonify(lendo=novos)

    @app.post("/api/campos/ler")
    def api_campos_ler():
        """Campo marcado à mão: lê o texto do retângulo e sugere a categoria."""
        d = corpo()
        s = sessao(d.get("id", ""))
        try:
            x0, y0, x1, y1 = (int(round(float(v))) for v in d["caixa"])
        except (KeyError, TypeError, ValueError):
            raise ErroOperacao("retângulo inválido")
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        txt = ocr.ler_trecho(s.base, (x0, y0, x1, y1))
        caixa = ocr.caixa_tinta(s.base, (max(0, x0), max(0, y0), min(s.W, x1), min(s.H, y1)), 0)
        return jsonify(texto=txt, categoria=mod_campos.categoria_por_texto(txt), caixa=caixa)

    @app.post("/api/auto/sugerir")
    def api_auto_sugerir():
        d = corpo()
        cat = d.get("categoria") or None
        if d.get("tipo") in ("barras", "qr"):
            return jsonify(texto="")
        with geradores.usando(split=split_de(d.get("id"))):
            return jsonify(texto=geradores.parecido(cat, str(d.get("texto", ""))))

    @app.post("/api/auto/fraude")
    def api_auto_fraude():
        """Fraude automática no documento aberto (modo simples). Com ``campos`` (os campos marcados na lista):
        edita EXATAMENTE esses, cada um com uma técnica profissional sorteada (o mesmo valor novo onde o dado se
        repete). Sem eles: o perfil profissional (nome e/ou endereço, às vezes mais um grupo; ``categorias`` troca a
        base); com ``receita``, k edições pela receita dada."""
        d = corpo()
        s = sessao(d.get("id", ""))
        exigir_trava(s)
        if d.get("campos") is not None:
            return _fraude_nos_campos(s, d)
        k = max(1, min(10, int(d.get("quantidade", 3) or 3)))
        rec = d.get("receita")
        if not rec:
            rec = receita_profissional(ids_categoria, tuple(d.get("categorias") or CATEGORIAS_FOCO),
                                       com_ia=bool(d.get("usar_ia")))
        elif d.get("categorias"):
            rec["categorias"] = {c: 1 for c in d["categorias"]}
        if d.get("modelo"):
            rec["modelo_ia"] = d["modelo"]
        r = gerador.receita(rec)
        erros = []
        hab = humano_da_sessao(s, r, contexto(s), random.Random())  # a mesma pessoa em toda a versão
        feitos = aplicar_receita(s, contexto(s), r, Plano(r, random.Random()), random.Random(), k=k,
                                 ao_falhar=lambda t, f, e: erros.append(f"{t}: {e}"), humano_hab=hab)
        if not feitos:
            raise ErroOperacao("nenhuma técnica coube neste documento" + (f" ({erros[-1]})" if erros else ""))
        x0 = min(t["bbox"][0] for t in feitos); y0 = min(t["bbox"][1] for t in feitos)
        x1 = max(t["bbox"][2] for t in feitos); y1 = max(t["bbox"][3] for t in feitos)
        r_ = recorte_composicao(s, (y0, y1, x0, x1)).get_json()
        return jsonify({**r_, "trocas": feitos})

    def _fraude_nos_campos(s, d):
        campos = []
        for c in d.get("campos") or []:
            try:
                caixa = [int(round(float(v))) for v in c["caixa"]][:4]
            except (KeyError, TypeError, ValueError):
                continue
            cat, tipo = c.get("categoria"), c.get("tipo") or "texto"
            if len(caixa) != 4 or cat not in ids_categoria or cat == "outro" or tipo not in ("texto", "barras", "qr"):
                continue
            campos.append({"id": str(c.get("id") or "")[:40], "caixa": caixa, "texto": str(c.get("texto") or "")[:500],
                           "categoria": cat, "tipo": tipo})
        if not campos:
            raise ErroOperacao("marque na lista os campos que quer editar (com o tipo de cada um)")
        # campo já trocado nesta versão fica como está: editar por cima empilhava duas edições no mesmo campo e o
        # rótulo dizia "texto antigo" = o valor falso da primeira
        def campo_da(h):  # técnica: params.campo.id; troca do modo simples: params.campo
            c = (h.get("params") or {}).get("campo")
            return c.get("id") if isinstance(c, dict) else c
        trocados = {campo_da(h) for h in s.historico()} - {None, ""}
        ja = [c for c in campos if c["id"] in trocados]
        campos = [c for c in campos if c["id"] not in trocados]
        if not campos:
            raise ErroOperacao("os campos marcados já foram trocados nesta versão: clique em Desfazer para trocar de novo")
        r = gerador.receita(receita_profissional(ids_categoria, sorted({c["categoria"] for c in campos}), extras=False,
                                                 com_ia=bool(d.get("usar_ia")))
                            | ({"modelo_ia": d["modelo"]} if d.get("modelo") else {}))
        erros = [f"{c['texto'][:30]}: já trocado (Desfazer para trocar de novo)" for c in ja]
        feitos = editar_campos(s, contexto(s), campos, r, None, random.Random(),
                               ao_falhar=lambda t, f, e: erros.append(f"{f.get('texto', '')[:30]}: {e}"),
                               humano_hab=humano_da_sessao(s, r, contexto(s), random.Random()))
        if not feitos:
            raise ErroOperacao("nenhum campo marcado pôde ser editado" + (f" ({erros[-1]})" if erros else ""))
        x0 = min(t["bbox"][0] for t in feitos); y0 = min(t["bbox"][1] for t in feitos)
        x1 = max(t["bbox"][2] for t in feitos); y1 = max(t["bbox"][3] for t in feitos)
        r_ = recorte_composicao(s, (y0, y1, x0, x1)).get_json()
        return jsonify({**r_, "trocas": feitos, "falhas": erros})

    # ------------------------------------------------------------------ gerador automático
    @app.get("/api/catalogo")
    def api_catalogo():
        return jsonify({**catalogo.para_json(), "categorias": categorias,
                        "pesos_categoria": dict(PESOS_CATEGORIA), "motivos_revisao": exportar.MOTIVOS_REVISAO,
                        **falsificador.para_json(),
                        "formatos": list(exportar.FORMATOS),
                        "canais": [["nenhum", "Nenhum (imagem como está)"]] + [[k, v] for k, v in canal.NOMES.items()]})

    @app.get("/api/gerador/documentos")
    def api_gerador_documentos():
        out = {}
        for sp in SPLITS:
            cont = contagem_versoes(sp)
            out[sp] = [{"id": it.doc, "nome": it.nome, "versoes": cont.get(it.doc, 0),
                        "lido": (raiz_saida / "_cache" / "ocr" / f"{it.sha_pixels[:32]}.json").is_file()}
                       for it in registro.por_split[sp]]
        return jsonify(out)

    @app.get("/api/gerador/receita_profissional")
    def api_gerador_receita_profissional():
        """A receita do perfil profissional expandida (o que o clique único vai fazer): ``extras`` (1/0, padrão 1),
        ``base`` (classes separadas por vírgula, padrão nome e endereço) e ``com_ia`` (1/0)."""
        a = request.args
        base = tuple(c for c in (a.get("base") or "").split(",") if c) or CATEGORIAS_FOCO
        return jsonify(receita_profissional(ids_categoria, base, extras=a.get("extras", "1") not in ("0", "false"),
                                            com_ia=a.get("com_ia", "0") in ("1", "true"), estilo=a.get("estilo")))

    @app.get("/api/gerador/estilos")
    def api_gerador_estilos():
        """Os estilos prontos da tela de um clique (o que cada um faz, para que serve, o que muda na imagem)."""
        return jsonify(estilos_prontos())

    @app.post("/api/gerador/previa")
    def api_gerador_previa():
        d = corpo()
        return jsonify(gerador.previa(d.get("receita") or {}, str(d.get("doc", ""))))

    @app.post("/api/gerador/iniciar")
    def api_gerador_iniciar():
        return jsonify(gerador.iniciar(corpo().get("receita") or {}))

    @app.get("/api/gerador/status")
    def api_gerador_status():
        return jsonify(gerador.status())

    @app.post("/api/gerador/parar")
    def api_gerador_parar():
        return jsonify(gerador.interromper())

    # ------------------------------------------------------------------ qualidade do dataset (aba 📊 Qualidade)
    tarefas_q: dict[str, dict] = {}  # "auditoria" / "detectabilidade" -> {"rodando", "inicio", "erro"}
    cache_entradas: dict = {}

    def _ler_json(nome: str) -> dict | None:
        try:
            arq = raiz_saida / nome
            return {**json.loads(arq.read_text(encoding="utf-8")), "_quando": arq.stat().st_mtime}
        except (OSError, ValueError):
            return None

    @app.get("/api/qualidade/resumo")
    def api_qualidade_resumo():
        """O que a aba Qualidade mostra: as entradas (variedade e o que falta), a última geração (refeitas,
        descartadas, por tamanho do texto), a última auditoria e a última medida de detectabilidade."""
        chave = tuple(sorted(registro.itens))
        if request.args.get("atualizar") or cache_entradas.get("chave") != chave:
            cache_entradas.update(chave=chave, dados=entradas.variedade(registro))
        st = gerador.status()
        geracao = {k: st.get(k) for k in ("rodando", "total", "feitas", "falhas", "edicoes", "refeitas_incoerentes",
                                          "descartadas_incoerentes", "por_tamanho", "falhas_tecnica", "inicio", "fim")}
        return jsonify(entradas=cache_entradas["dados"], geracao=geracao,
                       auditoria=_ler_json("relatorio_qualidade.json"),
                       detectabilidade=_ler_json("relatorio_detectabilidade.json"),
                       tarefas={k: {kk: vv for kk, vv in v.items() if kk != "thread"} for k, v in tarefas_q.items()})

    @app.post("/api/qualidade/rodar")
    def api_qualidade_rodar():
        """Roda a auditoria de qualidade ou a medida de detectabilidade em segundo plano (podem levar minutos)."""
        o_que = str(corpo().get("o_que") or "")
        if o_que not in ("auditoria", "detectabilidade"):
            raise ErroOperacao("o_que deve ser 'auditoria' ou 'detectabilidade'")
        if (tarefas_q.get(o_que) or {}).get("rodando"):
            raise ErroOperacao(f"{o_que} já está rodando")

        def rodar():
            try:
                if o_que == "auditoria":
                    qualidade.auditar(raiz_saida, log=lambda *_: None)
                else:
                    from rotulador.treino import detectabilidade
                    d = detectabilidade.avaliar(raiz_saida)
                    (raiz_saida / "relatorio_detectabilidade.json").write_text(
                        json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
                    (raiz_saida / "relatorio_detectabilidade.md").write_text(detectabilidade.markdown(d),
                                                                            encoding="utf-8")
                tarefas_q[o_que].update(rodando=False, fim=time.time())
            except Exception as e:  # noqa: BLE001 - o erro vai para a tela
                tarefas_q[o_que].update(rodando=False, fim=time.time(), erro=str(e))
        tarefas_q[o_que] = {"rodando": True, "inicio": time.time(), "erro": None}
        threading.Thread(target=rodar, daemon=True).start()
        return jsonify(ok=True)

    # ------------------------------------------------------------------ catálogo do que foi gerado
    def _versoes(linhas: list[dict]) -> list[dict]:
        por = {}
        for l in linhas:
            v = por.setdefault((l["split"], l["nome"]), {
                "split": l["split"], "nome": l["nome"], "doc": l["doc"], "versao": int(l["versao"]),
                "largura": int(l.get("largura") or 0), "altura": int(l.get("altura") or 0),
                "origem": l["origem"], "operador": l["operador"], "data": l["data"], "canal": l.get("canal", ""),
                "revisao": l.get("revisao") or "pendente", "revisor": l.get("revisor", ""),
                "falsificador": l.get("falsificador", ""), "motivo": l.get("motivo", ""),
                "pericia": l.get("pericia") or "auto", "edicoes": []})
            v["edicoes"].append({k: l[k] for k in ("ordem", "categoria", "tecnica", "nome_tecnica", "familia", "nivel",
                                                  "granularidade", "texto_antigo", "texto_novo", "descricao",
                                                  "pixels", "bbox")})
        return sorted(por.values(), key=lambda v: (v["data"], v["nome"]), reverse=True)

    @app.get("/api/catalogo/resumo")
    def api_catalogo_resumo():
        linhas = exportar.ler_catalogo(raiz_saida)
        cont = lambda chave: dict(sorted(Counter(l[chave] for l in linhas).items(), key=lambda kv: -kv[1]))
        matriz = Counter((l["tecnica"], l["categoria"]) for l in linhas)
        versoes = {(l["split"], l["nome"]) for l in linhas}
        return jsonify(edicoes=len(linhas), versoes=len(versoes),
                       por_split={sp: len({n for s_, n in versoes if s_ == sp}) for sp in SPLITS},
                       por_tecnica=cont("tecnica"), por_categoria=cont("categoria"), por_nivel=cont("nivel"),
                       por_familia=cont("familia"), por_origem=cont("origem"),
                       matriz=[{"tecnica": t, "categoria": c, "n": n} for (t, c), n in matriz.items()])

    FILTROS = ("split", "tecnica", "categoria", "nivel", "familia", "origem", "revisao", "canal", "exceto_tecnica",
               "operador", "falsificador", "motivo", "pericia")

    def filtrar(versoes: list[dict], a) -> tuple[list[dict], dict]:
        """Filtros do catálogo (também usados pela fila de revisão e pela exportação)."""
        filtros = {k: str(a.get(k)) for k in FILTROS if a.get(k)}
        q = str(a.get("q") or "").strip().lower()
        if q:
            filtros["q"] = q

        def serve(v):
            for k in ("split", "origem", "revisao", "operador", "falsificador", "motivo", "pericia"):
                if k in filtros and (v.get(k) or ("nenhum" if k in ("falsificador", "motivo") else "auto" if k == "pericia" else "")) != filtros[k]:
                    return False
            if "canal" in filtros:
                c, f = v.get("canal") or "", filtros["canal"]
                if (f == "sem" and c) or (f == "com" and not c) or (f not in ("com", "sem") and not c.startswith(f + ":")):
                    return False
            if "exceto_tecnica" in filtros and any(e["tecnica"] == filtros["exceto_tecnica"] for e in v["edicoes"]):
                return False
            eds = v["edicoes"]
            for k in ("tecnica", "categoria", "nivel", "familia"):
                if k in filtros:
                    eds = [e for e in eds if e[k] == filtros[k]]
            if q:
                eds = [e for e in eds if q in f"{e['texto_antigo']} {e['texto_novo']} {e['descricao']}".lower()
                       or q in v["nome"]]
            return bool(eds)
        return ([v for v in versoes if serve(v)] if filtros else versoes), filtros

    def com_nome(itens: list[dict]) -> list[dict]:
        for v in itens:  # nome do arquivo de origem, para títulos
            it = registro.itens.get(v["doc"])
            v["documento"] = it.nome if it else ""
        return itens

    @app.get("/api/catalogo/exemplos")
    def api_catalogo_exemplos():
        """Exemplos reais já gerados, um por técnica × classe: {técnica: [exemplo, ...]} (o modo fácil mostra o
        antes → depois, de preferência da classe do tipo). Prefere versões aprovadas na revisão e, entre
        elas, a mais recente; nunca uma reprovada."""
        def caixa_de(b) -> list[int]:  # no catálogo a caixa é "x0 y0 x1 y1"
            try:
                return [int(float(v)) for v in str(b or "").split()]
            except ValueError:
                return []
        melhor: dict = {}
        for l in exportar.ler_catalogo(raiz_saida):
            tid = l.get("tecnica")
            if not tid or l.get("revisao") == "reprovada" or len(caixa_de(l.get("bbox"))) != 4:
                continue
            nota = (l.get("revisao") == "aprovada", l.get("data") or "")
            chave = (tid, l.get("categoria"))
            if chave not in melhor or nota > melhor[chave][0]:
                melhor[chave] = (nota, l)
        out: dict = {}
        for (tid, _), (nota, l) in sorted(melhor.items(), key=lambda kv: kv[1][0], reverse=True):
            out.setdefault(tid, []).append({"split": l["split"], "nome": l["nome"], "bbox": caixa_de(l["bbox"]),
                                            "antigo": l.get("texto_antigo") or "", "novo": l.get("texto_novo") or "",
                                            "categoria": l.get("categoria")})
        return jsonify(out)

    @app.get("/api/catalogo/itens")
    def api_catalogo_itens():
        a = request.args
        versoes, _ = filtrar(_versoes(exportar.ler_catalogo(raiz_saida)), a)
        pagina, por_pagina = max(0, int(a.get("pagina", 0))), max(1, min(100, int(a.get("por_pagina", 24))))
        itens = com_nome(versoes[pagina * por_pagina:(pagina + 1) * por_pagina])
        return jsonify(total=len(versoes), pagina=pagina, por_pagina=por_pagina, itens=itens)

    @app.post("/api/catalogo/exportar")
    def api_catalogo_exportar():
        """Manifesto do subconjunto filtrado (ex.: só aprovadas) em saida/_exportacoes/<nome>/."""
        d = corpo()
        versoes, filtros = filtrar(_versoes(exportar.ler_catalogo(raiz_saida)), d.get("filtros") or {})
        return jsonify(exportar.exportar_subconjunto(raiz_saida, versoes, str(d.get("nome") or ""), filtros))

    @app.get("/api/catalogo/exportacao/<nome>/manifesto.<ext>")
    def api_catalogo_exportacao(nome, ext):
        if ext not in ("csv", "json") or not re.match(r"^[\w.-]{1,60}$", nome):
            raise ErroOperacao("exportação inválida")
        arq = (raiz_saida / "_exportacoes" / nome / f"manifesto.{ext}").resolve()
        if not arq.is_relative_to((raiz_saida / "_exportacoes").resolve()) or not arq.is_file():
            raise ErroOperacao("exportação não encontrada")
        return send_file(arq, as_attachment=True, download_name=f"{nome}_manifesto.{ext}")

    # ------------------------------------------------------------------ revisão humana
    @app.get("/api/revisao/fila")
    def api_revisao_fila():
        """Versões para revisar (padrão: pendentes), com os mesmos filtros do catálogo."""
        a = dict(request.args)
        a.setdefault("revisao", "pendente")
        if a.get("revisao") == "todas":
            a.pop("revisao")
        versoes, _ = filtrar(_versoes(exportar.ler_catalogo(raiz_saida)), a)
        versoes.sort(key=lambda v: (v["data"], v["nome"]))  # mais antigas primeiro
        cont = Counter(v["revisao"] for v in _versoes(exportar.ler_catalogo(raiz_saida)))
        return jsonify(total=len(versoes), itens=com_nome(versoes[:500]), contagem=dict(cont),
                       motivos=exportar.MOTIVOS_REVISAO)

    @app.post("/api/revisao")
    def api_revisao():
        """{itens: [{split, nome}], estado: aprovada|reprovada|pendente, motivos, comentario}"""
        d = corpo()
        itens = [(str(i.get("split", "")), str(i.get("nome", ""))) for i in (d.get("itens") or []) if isinstance(i, dict)]
        if not itens and d.get("split"):
            itens = [(str(d.get("split")), str(d.get("nome", "")))]
        n = exportar.revisar(raiz_saida, itens, str(d.get("estado", "")), operador() if operador() != ANONIMO else "",
                             d.get("motivos") or [], str(d.get("comentario") or ""))
        return jsonify(revisadas=n)

    # ------------------------------------------------------------------ equipe e metas
    @app.get("/api/equipe")
    def api_equipe():
        versoes = _versoes(exportar.ler_catalogo(raiz_saida))
        por_op: dict[str, dict] = {}
        for v in versoes:
            o = por_op.setdefault(v["operador"] or "(sem nome)", {"versoes": 0, "edicoes": 0, "revisoes": 0,
                                                                   "aprovadas": 0, "reprovadas": 0})
            o["versoes"] += 1
            o["edicoes"] += len(v["edicoes"])
            o["aprovadas"] += v["revisao"] == "aprovada"
            o["reprovadas"] += v["revisao"] == "reprovada"
            if v["revisor"]:
                por_op.setdefault(v["revisor"], {"versoes": 0, "edicoes": 0, "revisoes": 0, "aprovadas": 0,
                                                 "reprovadas": 0})["revisoes"] += 1
        atrib, vivas = equipe.atribuicoes(), travas.todas()
        docs = []
        for sp in SPLITS:
            cont = contagem_versoes(sp)
            for it in registro.por_split[sp]:
                t = vivas.get(it.doc)
                docs.append({"id": it.doc, "nome": it.nome, "split": sp, "versoes": cont.get(it.doc, 0),
                             "responsavel": atrib.get(it.doc),
                             "em_edicao_por": t["operador"] if t else None,
                             "desde": time.strftime("%H:%M", time.localtime(t["desde"])) if t else None})
        nomes = sorted({n for n in list(por_op) + list(atrib.values()) + [t["operador"] for t in vivas.values()]
                        if n and n not in ("(sem nome)", ANONIMO) and not n.startswith("gerador automático")})
        return jsonify(operadores=nomes, por_operador=por_op, documentos=docs, eu=operador())

    @app.post("/api/equipe/atribuir")
    def api_equipe_atribuir():
        d = corpo()
        ids = [str(i) for i in (d.get("ids") or []) if str(i) in registro.itens]
        if not ids:
            raise ErroOperacao("nenhum documento escolhido")
        op = str(d.get("operador") or "").strip()
        return jsonify(atribuicoes=equipe.atribuir(ids, op or None))

    @app.get("/api/metas")
    def api_metas():
        return jsonify(progresso(equipe.metas(), exportar.ler_catalogo(raiz_saida)))

    @app.post("/api/metas")
    def api_metas_definir():
        try:
            m = equipe.definir_metas(corpo(), {c["chave"] for c in categorias})
        except (TypeError, ValueError) as e:
            raise ErroOperacao(str(e))
        return jsonify(progresso(m, exportar.ler_catalogo(raiz_saida)))

    @app.get("/api/catalogo/imagem/<split>/<nome>/<tipo>.png")
    def api_catalogo_imagem(split, nome, tipo):
        """Miniatura/recorte de uma versão salva: editada, original, mascara, tecnicas ou sobreposicao."""
        if split not in SPLITS or not re.match(r"^[0-9a-f]{8,64}__v\d{2,}$", nome):
            raise ErroOperacao("versão inválida")
        if tipo not in ("editada", "original", "mascara", "tecnicas", "sobreposicao"):
            raise ErroOperacao("tipo de imagem inválido")
        raiz_split = (raiz_saida / split).resolve()
        anot_arq = raiz_split / "anotacoes" / f"{nome}.json"
        if not anot_arq.is_file():
            raise ErroOperacao("versão não encontrada")
        anot = json.loads(anot_arq.read_text(encoding="utf-8"))
        arqs = anot["arquivos"]
        chave_arq = {"editada": "editada", "original": "original", "mascara": "mascara", "tecnicas": "mascara_tecnicas",
                     "sobreposicao": "editada"}[tipo]
        if chave_arq not in arqs:
            raise ErroOperacao("esta versão não tem essa máscara (salva por uma versão antiga)")
        caminho = (raiz_split / arqs[chave_arq]).resolve()
        if not caminho.is_relative_to(raiz_split) or not caminho.is_file():
            raise ErroOperacao("arquivo da versão não encontrado")
        a = request.args
        maximo = max(64, min(2000, int(a.get("max", 480))))
        crop = [a.get(k) for k in ("x0", "y0", "x1", "y1")]
        crop = [int(float(v)) for v in crop] if all(v not in (None, "") for v in crop) else None
        chave = f"{nome}_{tipo}_{maximo}_{'-'.join(map(str, crop)) if crop else 'todo'}_{caminho.stat().st_mtime_ns}"
        cache = raiz_saida / "_cache" / "catalogo" / split / f"{chave}.png"
        if not cache.is_file():
            if tipo in ("mascara", "tecnicas"):
                with Image.open(caminho) as im:
                    img = np.array(im)
                img = exportar.colorir_tecnicas(img) if tipo == "tecnicas" else np.repeat(img[..., None], 3, 2)
            else:
                img = documentos.carregar(caminho, 0, registro.dpi)
            if tipo == "sobreposicao" and "mascara_tecnicas" in arqs and (raiz_split / arqs["mascara_tecnicas"]).is_file():
                with Image.open(raiz_split / arqs["mascara_tecnicas"]) as im:
                    m = np.array(im)
                if m.shape == img.shape[:2]:
                    cor = exportar.colorir_tecnicas(m).astype(np.float32)
                    a_ = (m > 0)[..., None] * 0.45
                    img = (img * (1 - a_) + cor * a_).astype(np.uint8)
            if crop:
                x0, y0, x1, y1 = crop
                H, W = img.shape[:2]
                x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
                if x1 > x0 and y1 > y0:
                    img = img[y0:y1, x0:x1]
            esc = min(1.0, maximo / max(img.shape[:2]))
            if esc < 1:
                img = cv2.resize(img, (max(1, int(img.shape[1] * esc)), max(1, int(img.shape[0] * esc))),
                                 interpolation=cv2.INTER_AREA)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(png(img))
        return send_file(cache, mimetype="image/png")

    @app.post("/api/catalogo/excluir")
    def api_catalogo_excluir():
        d = corpo()
        return jsonify(exportar.excluir_versao(raiz_saida, str(d.get("split", "")), str(d.get("nome", ""))))

    @app.post("/api/catalogo/reindexar")
    def api_catalogo_reindexar():
        for sp in SPLITS:
            if (raiz_saida / sp / "anotacoes").is_dir():
                exportar.reconstruir_indices(raiz_saida, sp)
        return jsonify(ok=True)

    @app.get("/api/ia/status")
    def api_ia_status():
        return jsonify(ia.status())

    @app.post("/api/ia/descarregar")
    def api_ia_descarregar():
        return jsonify(ia.descarregar())

    @app.get("/api/ia/progresso")
    def api_ia_progresso():
        return jsonify(ia.progresso())

    @app.post("/api/ia/cancelar")
    def api_ia_cancelar():
        return jsonify(ia.cancelar())

    # ------------------------------------------------------------------ perito (pistas que a edição deixa)
    def mapa_perito_png(m: np.ndarray, maximo: int | None = None) -> bytes:
        """PNG RGBA do mapa, do tamanho exato da imagem (o visor desenha por cima do documento); ``maximo`` reduz."""
        if maximo and maximo < max(m.shape):
            esc = maximo / max(m.shape)
            m = cv2.resize(m, (max(1, int(m.shape[1] * esc)), max(1, int(m.shape[0] * esc))), interpolation=cv2.INTER_AREA)
        return png(perito.colorir(m))

    @app.get("/api/perito/<split>/<nome>")
    def api_perito_versao(split, nome):
        """Medidas e nota (0..1) das pistas que cada edição de uma versão salva deixou (rotulador.perito)."""
        return jsonify(edicoes=perito.medir_versao(raiz_saida, split, nome))

    @app.get("/api/perito/<split>/<nome>/mapa.png")
    def api_perito_mapa(split, nome):
        """Mapa de calor (RGBA do tamanho da editada; transparente sem pista, laranja → vermelho com pista) de onde
        a edição ficou óbvia. Só reduz com ``max`` (lado maior em px)."""
        original, editada, regiao, _ = perito.carregar_versao(raiz_saida, split, nome)
        maximo = max(64, min(4000, int(request.args["max"]))) if request.args.get("max") else None
        r = send_file(io.BytesIO(mapa_perito_png(perito.mapa(original, editada, regiao), maximo)), mimetype="image/png")
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.post("/api/perito/sessao")
    def api_perito_sessao():
        """{doc: id} -> nota da região editada da sessão, ``medidas`` = lista por edição ({tecnica, nota, medidas}; sem
        lista de edições, uma entrada só da região toda) e o mapa de calor (PNG RGBA em base64, do tamanho exato do
        documento), para o operador ver onde a edição manual ficou óbvia."""
        d = corpo()
        s = sessao(d.get("doc") or d.get("id") or "")
        with s.lock:
            _, regiao, _ = s.mascaras()
            if not regiao.any():
                raise ErroOperacao("nenhuma edição na sessão: edite algo antes de pedir a perícia")
            atual = s.atual.copy()
            mapa_eds, ents = s.mapa_edicoes()
        geral = perito.avaliar(s.base, atual, regiao)
        lista = []
        for e in exportar.edicoes_da_sessao(mapa_eds, ents):
            m = perito.medir(s.base, atual, mapa_eds == e["ordem"])
            lista.append({k: e.get(k) for k in ("ordem", "tecnica", "nome_tecnica", "categoria", "bbox")}
                         | {"nota": perito.nota(m), "medidas": m})
        if not lista:  # camadas sem edição própria (mesclar/duplicar): a região toda numa entrada só
            lista = [{"tecnica": None, "nota": geral["nota"], "medidas": geral["medidas"]}]
        H, W = regiao.shape
        return jsonify({**geral, "medidas": lista, "largura": W, "altura": H,
                        "mapa": base64.b64encode(mapa_perito_png(perito.mapa(s.base, atual, regiao))).decode()})

    return app


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Rotulador de fraude documental")
    ap.add_argument("--dados", default=str(RAIZ / "dados"))
    ap.add_argument("--saida", default=str(RAIZ / "saida"))
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--porta", type=int, default=int(os.environ.get("PORT", 5000)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--categorias", default=str(RAIZ / "config" / "categorias.json"))
    ap.add_argument("--sem-navegador", action="store_true")
    a = ap.parse_args()

    try:
        reg = Registro(Path(a.dados), Path(a.saida), dpi=a.dpi)
    except ErroVazamento as e:
        print("\n*** VAZAMENTO ENTRE SPLITS — o servidor NÃO vai subir ***")
        for c in e.conflitos:
            print("  -", c)
        print("\nResolva (tire o documento de um dos splits) e rode de novo.")
        sys.exit(2)
    for aviso in reg.avisos:
        print("aviso:", aviso)
    for sp in SPLITS:
        print(f"{sp:>10}: {len(reg.por_split[sp])} página(s)")

    app = criar_app(reg, Path(a.saida), carregar_categorias(Path(a.categorias)))
    endereco = f"http://{a.host}:{a.porta}"
    print(f"Rotulador rodando em {endereco}  (Ctrl+C para sair)")
    if not a.sem_navegador:
        threading.Timer(1.0, webbrowser.open, [endereco]).start()
    try:
        try:  # servidor de produção (várias pessoas ao mesmo tempo); sem ele, o do Flask
            from waitress import serve
        except ImportError:
            app.run(host=a.host, port=a.porta, debug=False, threaded=True)
        else:
            serve(app, host=a.host, port=a.porta, threads=12, channel_timeout=3600)
    except OSError as erro:
        sys.exit(f"Não consegui usar a porta {a.porta} ({erro}). Tente: python app.py --porta 5001")


if __name__ == "__main__":
    main()
