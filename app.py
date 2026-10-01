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
import sys
import threading
import webbrowser
from collections import OrderedDict
from pathlib import Path

try:
    import cv2
    import numpy as np
    from flask import Flask, jsonify, render_template, request, send_file
except ModuleNotFoundError as erro:
    sys.exit(f"Falta a dependência '{erro.name}'. Instale com:  pip install -r requirements.txt")

from rotulador import exportar, selecao
from rotulador.ops import COM_PREVIA, executar, geradores, ia, texto
from rotulador.registro import SPLITS, ErroVazamento, Registro
from rotulador.sessao import ErroOperacao, Sessao

RAIZ = Path(__file__).resolve().parent
MAX_SESSOES = 6


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
    raiz_saida = Path(raiz_saida).resolve()
    ids_categoria = {c["chave"]: int(c["id"]) for c in categorias}
    sessoes: OrderedDict[str, Sessao] = OrderedDict()
    lock_sessoes = threading.Lock()
    app.registro = registro
    app.sessoes = sessoes

    # ------------------------------------------------------------------ apoio
    def item_ou_404(doc: str):
        try:
            return registro.item(str(doc))
        except KeyError:
            raise ErroOperacao("documento não encontrado")

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
        return jsonify([{"id": it.doc, "nome": it.nome, "largura": it.largura, "altura": it.altura,
                         "versoes": cont.get(it.doc, 0), "rascunho": (rasc / it.doc / "estado.json").is_file()}
                        for it in registro.por_split[sp]])

    @app.get("/api/doadores")
    def api_doadores():
        s = sessao(request.args.get("id", ""))
        return jsonify([{"id": it.doc, "nome": it.nome, "largura": it.largura, "altura": it.altura}
                        for it in registro.por_split[s.item.split] if it.doc != s.item.doc])

    @app.post("/api/abrir")
    def api_abrir():
        return jsonify(estado_sessao(sessao(corpo().get("id", ""))))

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
        with s.lock:
            sel = selecao.rasterizar(d.get("selecao"), s.atual)
            chave = (s.item.doc, s.versao, json.dumps(d.get("selecao"), sort_keys=True))
            res = executar(tipo, s.atual, params, sel, base=s.base, carregar_doador=carregador_doador(s),
                           chave_cache=chave)
            if previa:
                antes = s.atual[res.y0:res.y1, res.x0:res.x1]
                rec = np.where(res.pegada[..., None], res.recorte, antes)
                return jsonify(bbox=[res.x0, res.y0, res.x1, res.y1], png=b64png(rec))
            sel_spec = d.get("selecao")
            registro_params = dict(params)
            if sel_spec and sel_spec.get("formas"):
                registro_params["selecao"] = sel_spec
            delta = s.aplicar(tipo, d.get("categoria"), str(d.get("descricao", "")), registro_params, res)
            return jsonify(bbox=[delta.x0, delta.y0, delta.x1, delta.y1], png=b64png(delta.depois),
                           estado=estado_sessao(s))

    def _volta(s: Sessao, delta, recorte_attr: str):
        if delta is None:
            return jsonify(nada=True, estado=estado_sessao(s))
        return jsonify(bbox=[delta.x0, delta.y0, delta.x1, delta.y1], png=b64png(getattr(delta, recorte_attr)),
                       estado=estado_sessao(s))

    @app.post("/api/desfazer")
    def api_desfazer():
        s = sessao(corpo().get("id", ""))
        return _volta(s, s.desfazer(), "antes")

    @app.post("/api/refazer")
    def api_refazer():
        s = sessao(corpo().get("id", ""))
        return _volta(s, s.refazer(), "depois")

    @app.post("/api/descartar")
    def api_descartar():
        s = sessao(corpo().get("id", ""))
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
        try:
            q = int(d.get("qualidade", 95))
        except (TypeError, ValueError):
            raise ErroOperacao("qualidade inválida")
        r = exportar.salvar(s, raiz_saida, str(d.get("formato", "png")), q,
                            str(d.get("operador", ""))[:80], registro.dpi)
        return jsonify(salvo=r, estado=estado_sessao(s))

    @app.get("/api/gerar")
    def api_gerar():
        return jsonify(geradores.gerar(request.args.get("categoria", "")))

    @app.get("/api/ia/status")
    def api_ia_status():
        return jsonify(ia.status())

    @app.post("/api/ia/descarregar")
    def api_ia_descarregar():
        return jsonify(ia.descarregar())

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
        app.run(host=a.host, port=a.porta, debug=False, threaded=True)
    except OSError as erro:
        sys.exit(f"Não consegui usar a porta {a.porta} ({erro}). Tente: python app.py --porta 5001")


if __name__ == "__main__":
    main()
