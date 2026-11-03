"""O estúdio: a interface do PhotoCraft (a versão web, servida em /photocraft/) com a aba Gerar do rotulador.

Cada documento que o estúdio abre no PhotoCraft ganha um TOKEN, que vai no nome do arquivo ("conta luz
[<doc>__m1].png"). O PhotoCraft salva com o mesmo nome (".psd"), e o token diz de que documento e de que abertura veio:
  - ``<doc>__m<k>``: o documento original, aberto para editar à mão;
  - ``<doc>__a<k>``: uma versão feita pela aba Gerar (as ferramentas do PhotoCraft), aberta para revisar.

Ao salvar (Ctrl+S no PhotoCraft), o arquivo vem para cá em vez de virar download. O PhotoCraft achata o PSD e a máscara é a
diferença EXATA entre o salvo e o que foi aberto: o PNG do estúdio passa pelo PhotoCraft sem mudar nenhum pixel. Nada é
perguntado. A classe de cada pedaço editado vem do campo lido no documento (OCR, em cache) que ele cobre; o texto antigo é
o do campo e o novo é lido no arquivo salvo. A versão vai para o split do documento (saida/<split>/...) com as máscaras em
0/1 (1 = editado), a anotação, os índices e a planilha do dataset. Salvar de novo o mesmo documento aberto SUBSTITUI a
versão, como o Ctrl+S do Photoshop grava por cima do arquivo: uma abertura = uma versão no dataset.

As versões salvas aqui ficam num histórico, em ordem: o "Apagar a última edição" tira do dataset a mais recente (a
editada, as máscaras, a anotação e a linha da planilha). A imagem de entrada, na pasta de dados, nunca é tocada."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import documentos, exportar, fsts, ocr, photocraft, planilha, qualidade
from .contexto import Contexto
from .gerador import _perito_por_edicao
from .sessao import ErroOperacao, Resultado, Sessao

TOKEN = re.compile(r"^(?P<doc>[0-9a-f]{16})__(?P<tipo>[ma])(?P<k>\d{1,6})$")
NO_NOME = re.compile(r"\[(?P<token>[0-9a-f]{16}__[ma]\d{1,6})\]")
SEM_PERDA = {".psd", ".psb", ".png", ".tif", ".tiff", ".bmp"}
DO_MOTOR = {".psd", ".psb"}  # o formato do PhotoCraft: quem achata é ele
JUNTAR = 0.6  # pedaços editados a menos de JUNTAR × a altura do texto um do outro viram uma edição só
FORA_DOS_CAMPOS = "edição no PhotoCraft fora dos campos lidos no documento"
DIAS_RASCUNHO = 7  # a versão gerada que ninguém salvou some do disco depois disso
HISTORICO = 500  # versões salvas lembradas para o "Apagar a última edição"


def _token(t: str) -> re.Match:
    m = TOKEN.match(str(t or ""))
    if not m:
        raise ErroOperacao("documento do estúdio inválido")
    return m


class Estudio:
    def __init__(self, registro, raiz_saida: Path, ids_categoria: dict[str, int], gerador):
        self.registro, self.ids, self.gerador = registro, ids_categoria, gerador
        self.raiz_saida = Path(raiz_saida)
        self.pasta = self.raiz_saida / "_estudio"
        self.lock = threading.RLock()
        self._lendo: set[str] = set()

    # ------------------------------------------------------------------ estado (contadores e versões salvas)
    def _estado(self) -> dict:
        try:
            e = json.loads((self.pasta / "estado.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            e = {}
        return {"contador": e.get("contador") or {}, "salvos": e.get("salvos") or {}, "historico": e.get("historico") or []}

    def _gravar_estado(self, e: dict) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        tmp = self.pasta / "estado.tmp"
        tmp.write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.pasta / "estado.json")

    def _novo_token(self, doc: str, tipo: str) -> str:
        with self.lock:
            e = self._estado()
            k = int(e["contador"].get(f"{doc}{tipo}", 0)) + 1
            e["contador"][f"{doc}{tipo}"] = k
            self._gravar_estado(e)
        return f"{doc}__{tipo}{k}"

    def _item(self, doc: str):
        try:
            return self.registro.item(str(doc))
        except KeyError:
            raise ErroOperacao("documento não encontrado no dataset")

    @staticmethod
    def nome_arquivo(it, token: str) -> str:
        """O nome com que o documento abre no PhotoCraft (e com que ele salva): legível e com o token."""
        stem = re.sub(r"[\[\]\\/:*?\"<>|]+", " ", Path(it.rel).stem).strip()[:40] or "documento"
        return f"{stem}{f' p{it.pagina + 1}' if it.paginas > 1 else ''} [{token}].png"

    def _aberto(self, it, token: str, **extra) -> dict:
        return {"token": token, "arquivo": self.nome_arquivo(it, token), "doc": it.doc, "split": it.split,
                "nome": it.nome, **extra}

    def preler(self, it) -> None:
        """Lê os campos (OCR) do documento em segundo plano: o salvamento usa para dar nome ao que foi editado."""
        if it.doc in self._lendo:
            return
        self._lendo.add(it.doc)

        def ler():
            try:
                Contexto(it, self.registro.carregar(it.doc), self.raiz_saida, self.registro).campos
            except Exception:  # noqa: BLE001 - pré-leitura é só conveniência
                pass
            finally:
                self._lendo.discard(it.doc)
        threading.Thread(target=ler, daemon=True).start()

    # ------------------------------------------------------------------ abrir
    def abrir(self, doc: str) -> dict:
        """Um documento do dataset aberto para editar à mão (o PNG dele é o ``/api/img/<doc>/original.png``)."""
        it = self._item(doc)
        self.preler(it)
        return self._aberto(it, self._novo_token(it.doc, "m"), png=f"/api/img/{it.doc}/original.png")

    def registrar_arquivo(self, nome: str, conteudo: bytes) -> dict:
        """O arquivo que a pessoa abriu pelo "Abrir" (ou arrastou) no PhotoCraft entra no dataset (``Registro.adicionar``:
        o split sai sozinho) e abre como o PNG do registro, para a diferença no salvamento ser exata."""
        n_antes = len(self.registro.itens)
        try:
            itens = self.registro.adicionar(nome, conteudo)
        except ValueError as e:
            raise ErroOperacao(str(e))
        aberto = self.abrir(itens[0].doc)
        aberto.update(novo=len(self.registro.itens) > n_antes,
                      paginas=[{"doc": it.doc, "pagina": it.pagina + 1} for it in itens])
        return aberto

    # ------------------------------------------------------------------ gerar (aba Gerar)
    def gerar(self, doc: str, opcoes: dict | None = None) -> dict:
        """Uma versão pelas ferramentas do PhotoCraft (perfil profissional com ``motor="photocraft"``: nome e endereço e,
        às vezes, outros dados coerentes; ou, com ``metodo="fsts"``, o método do artigo FSTS: rotulador.fsts), sem
        gravar: abre no PhotoCraft para a pessoa revisar e salvar."""
        if not photocraft.disponivel():
            raise ErroOperacao("o motor PhotoCraft (photocraft-cli) não está instalado: veja config/photocraft.json")
        op = opcoes or {}
        it = self._item(doc)
        self._limpar()
        token = self._novo_token(it.doc, "a")
        pasta = self.pasta / token
        if op.get("metodo") == "fsts":
            rec = {"perfil": "fsts", "operador": "estúdio PhotoCraft (aba Gerar, método FSTS)"}
        else:
            rec = {"perfil": "profissional", "motor": "photocraft", "estilo": op.get("estilo") or "recomendado",
                   "extras": bool(op.get("extras", True)), "operador": "estúdio PhotoCraft (aba Gerar)"}
            if op.get("categorias"):
                rec["categorias_base"] = list(op["categorias"])
        try:
            v = self.gerador.versao_avulsa(it, rec, pasta)
        except BaseException:
            shutil.rmtree(pasta, ignore_errors=True)
            raise
        (pasta / "extra.json").write_text(json.dumps(v["extra"], ensure_ascii=False, default=str), encoding="utf-8")
        trocas = [{k: f.get(k) for k in ("categoria", "antigo", "novo", "tecnica", "nome_tecnica", "bbox", "fsts")}
                  for f in v["feitos"]]
        fs = v["extra"]["gerador"].get("fsts")
        return self._aberto(it, token, png=f"/api/estudio/png/{token}", trocas=trocas,
                            humano=(v["extra"]["gerador"].get("humano") or {}).get("descricao"),
                            fsts={"regioes_pedidas": fs["regioes_pedidas"],
                                  "tipos": {t: fsts.TIPOS[t]["rotulo"] for t in fsts.TIPOS}} if fs else None)

    def _limpar(self, dias: float = DIAS_RASCUNHO) -> None:
        """Os rascunhos de versões geradas há mais de ``dias`` saem (a versão já salva fica no dataset; só deixa de
        dar para salvar de novo por cima dela)."""
        limite = time.time() - dias * 86400
        if not self.pasta.is_dir():
            return
        for p in self.pasta.iterdir():
            if p.is_dir() and re.match(r"^[0-9a-f]{16}__a\d+", p.name) and p.stat().st_mtime < limite:
                shutil.rmtree(p, ignore_errors=True)

    def _sessao(self, token: str, it, base: np.ndarray) -> Sessao:
        """A sessão do token: a versão gerada (cópia do rascunho dela, que fica para um novo salvamento) ou o
        original limpo."""
        destino = self.pasta / f"{token}__salvar"
        shutil.rmtree(destino, ignore_errors=True)
        rascunho = self.pasta / token
        if _token(token)["tipo"] == "a":
            if not (rascunho / "estado.json").is_file():
                raise ErroOperacao("a versão gerada não está mais no servidor: gere de novo")
            shutil.copytree(rascunho, destino, ignore=shutil.ignore_patterns("extra.json"))
        return Sessao(it, base, destino, self.ids)

    def imagem_gerada(self, token: str) -> np.ndarray:
        m = _token(token)
        if m["tipo"] != "a":
            raise ErroOperacao("só as versões geradas têm imagem própria")
        it = self._item(m["doc"])
        with self.lock:
            s = self._sessao(token, it, self.registro.carregar(it.doc))
            try:
                return s.atual.copy()
            finally:
                shutil.rmtree(s.pasta, ignore_errors=True)

    # ------------------------------------------------------------------ salvar (Ctrl+S no PhotoCraft)
    @staticmethod
    def decodificar(conteudo: bytes, sufixo: str) -> np.ndarray:
        if sufixo in DO_MOTOR:
            return photocraft.achatar(conteudo, sufixo)
        try:
            with Image.open(io.BytesIO(conteudo)) as im:
                im.load()
                return documentos.imagem_para_rgb(im)
        except (OSError, ValueError) as e:
            raise ErroOperacao(f"não consegui ler o arquivo salvo ({e})")

    def _qual_token(self, nome: str, img: np.ndarray, abertos: list[str]) -> str:
        """O token no nome do arquivo; sem ele ("Salvar como" com outro nome), o documento aberto nesta página cuja
        original difere menos do salvo."""
        m = NO_NOME.search(nome or "")
        if m:
            return m["token"]
        melhor, menor = None, 0.5
        for t in abertos[:30]:
            mt = TOKEN.match(str(t))
            it = mt and self.registro.itens.get(mt["doc"])
            if not it or (it.altura, it.largura) != img.shape[:2]:
                continue
            frac = float((self.registro.carregar(it.doc) != img).any(2).mean())
            if frac < menor:
                melhor, menor = t, frac
        if melhor is None:
            raise ErroOperacao("este arquivo não foi aberto pelo estúdio: abra o documento pela aba ⚡ Gerar "
                               "(ou pelo Abrir do PhotoCraft) e edite nele")
        return melhor

    def _campos(self, it, base: np.ndarray) -> list[dict]:
        try:
            return [f for f in Contexto(it, base, self.raiz_saida, self.registro).campos if f.get("caixa")]
        except Exception:  # noqa: BLE001 - sem OCR, a edição sai como "outro" (a máscara não depende disso)
            return []

    def _edicoes_manuais(self, s: Sessao, img: np.ndarray, it, nome: str) -> list[dict]:
        """O que mudou entre o salvo e a sessão vira edições ``photocraft`` (técnica manual.photocraft), uma por grupo de
        pedaços próximos, com a classe, o texto antigo e o novo do campo que o grupo cobre."""
        dif = (img != s.atual).any(2)
        if not dif.any():
            return []
        campos = self._campos(it, s.base)
        alturas = [f["caixa"][3] - f["caixa"][1] for f in campos if f.get("tipo", "texto") == "texto"]
        r = max(2, int(JUNTAR * (float(np.median(alturas)) if alturas else 20)))
        perto = cv2.dilate(dif.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1)))
        n, rot = cv2.connectedComponents(perto, connectivity=8)
        feitas = []
        for g in range(1, n):
            m = dif & (rot == g)
            ys, xs = np.nonzero(m)
            if not ys.size:
                continue
            y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
            campo, melhor = None, 0
            for f in campos:
                fx0, fy0, fx1, fy1 = (int(round(float(v))) for v in f["caixa"])
                dentro = int(m[max(0, fy0 - 2):max(0, fy1 + 2), max(0, fx0 - 2):max(0, fx1 + 2)].sum())
                if dentro > melhor and f.get("categoria") in self.ids:
                    campo, melhor = f, dentro
            cat = campo["categoria"] if campo else "outro"
            desc = "" if campo and cat != "outro" else FORA_DOS_CAMPOS
            meta = {"rotulo": "PhotoCraft (manual)", "descricao": None, "fornecedores": {"editor": "photocraft-web"}}
            if campo:
                caixa = [int(round(float(v))) for v in campo["caixa"]]
                meta.update(campo=campo.get("id"), texto_antigo=campo.get("texto"))
                if campo.get("tipo", "texto") == "texto":
                    try:
                        meta["texto_novo"] = ocr.ler_trecho(img, tuple(caixa))
                    except Exception:  # noqa: BLE001 - sem OCR o texto novo fica em branco
                        meta["texto_novo"] = None
                antigo, novo = meta.get("texto_antigo"), meta.get("texto_novo")
                meta["descricao"] = (f"edição no PhotoCraft no campo {cat}"
                                     + (f": '{antigo}' → '{novo}'" if antigo is not None and novo is not None else ""))
            else:
                meta["descricao"] = FORA_DOS_CAMPOS
            res = Resultado(y0, y1, x0, x1, recorte=img[y0:y1, x0:x1].copy(), pegada=m[y0:y1, x0:x1].copy(), meta=meta)
            if s.ordem:  # a camada nova vai para o topo: nenhuma camada gerada esconde o que a pessoa fez
                s.acao_camada("ativar", camada=s.ordem[-1])
            s.acao_camada("criar", categoria=cat, nome=f"PhotoCraft (manual) {g}", descricao=desc)
            s.aplicar("photocraft", cat, desc, {"campo": (campo or {}).get("id"), "arquivo": nome}, res, camada=s.ativa)
            feitas.append({"categoria": cat, "bbox": [x0, y0, x1, y1], "pixels": int(m.sum()),
                           "antigo": meta.get("texto_antigo"), "novo": meta.get("texto_novo")})
        return feitas

    def salvar(self, nome: str, conteudo: bytes, abertos: list[str] | None = None, operador: str = "") -> dict:
        """O arquivo salvo no PhotoCraft vira (ou substitui) a versão do documento no dataset. Devolve o que
        ``exportar.salvar`` devolve, mais ``token``, ``doc`` (o documento de origem: a página passa para o seguinte da
        lista), ``nome`` (da versão), ``manuais`` (as edições feitas à mão) e ``substituiu`` (a versão anterior desta
        abertura, que saiu)."""
        sufixo = Path(nome or "").suffix.lower()
        if sufixo not in SEM_PERDA:
            raise ErroOperacao(f"'{sufixo or nome}' tem perda (JPEG, WEBP...): a diferença com o original sairia na "
                               "imagem inteira. Salve com Ctrl+S (PSD) ou exporte em PNG para ir ao dataset")
        img = self.decodificar(conteudo, sufixo)
        token = self._qual_token(nome, img, list(abertos or []))
        mt = _token(token)
        it = self._item(mt["doc"])
        base = self.registro.carregar(it.doc)
        if img.shape != base.shape:
            raise ErroOperacao(f"o tamanho mudou ({img.shape[1]}×{img.shape[0]}, o original tem "
                               f"{base.shape[1]}×{base.shape[0]}): recorte e redimensionamento não entram no dataset")
        fracao = float((img != base).any(2).mean())
        if fracao > qualidade.AREA_MAXIMA:  # a auditoria recusaria: outro documento por cima, ajuste na página inteira
            raise ErroOperacao(f"{100 * fracao:.0f}% da página mudou (o limite é {100 * qualidade.AREA_MAXIMA:.0f}%): "
                               "parece outro documento inserido por cima, ou um ajuste na imagem inteira. Nada foi "
                               "gravado; desfaça isso no PhotoCraft e salve de novo")
        with self.lock:
            s = self._sessao(token, it, base)
            try:
                manuais = self._edicoes_manuais(s, img, it, nome)
                if not np.array_equal(s.atual, img):  # a editada gravada tem de ser o que a pessoa salvou, pixel a pixel
                    raise ErroOperacao("a versão montada não ficou igual ao arquivo salvo; nada foi gravado")
                if not (s.atual != s.base).any():
                    raise ErroOperacao("nada foi editado: a imagem salva é igual ao original")
                extra = {}
                if mt["tipo"] == "a":
                    try:
                        extra = json.loads((self.pasta / token / "extra.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        extra = {}
                extra["perito_por_edicao"] = _perito_por_edicao(s)  # medido de novo: entram as edições à mão
                extra["estudio"] = {"token": token, "arquivo": nome, "editor": "PhotoCraft (web)",
                                    "gerada_pela_aba": mt["tipo"] == "a", "edicoes_manuais": len(manuais)}
                anterior = self._estado()["salvos"].get(token)
                salvo = exportar.salvar(s, self.raiz_saida, "png", 95, operador, self.registro.dpi, extra=extra)
            finally:
                shutil.rmtree(s.pasta, ignore_errors=True)
            nome_versao = f"{it.doc}__v{salvo['versao']:02d}"
            # o mesmo documento aberto, salvo de novo: a nova já está gravada, a anterior sai (falhando o salvar, ela fica)
            if anterior and anterior["nome"] != nome_versao:
                try:
                    exportar.excluir_versao(self.raiz_saida, anterior["split"], anterior["nome"])
                except ErroOperacao:
                    pass
            e = self._estado()
            e["salvos"][token] = {"split": it.split, "nome": nome_versao}
            e["historico"] = [h for h in e["historico"] if h.get("token") != token][-(HISTORICO - 1):] + [
                {"token": token, "split": it.split, "nome": nome_versao, "versao": salvo["versao"], "doc": it.doc,
                 "documento": it.nome}]
            self._gravar_estado(e)
        planilha.agendar(self.raiz_saida, self.registro, self.ids)
        return {**salvo, "token": token, "doc": it.doc, "nome": nome_versao, "documento": it.nome, "manuais": manuais,
                "pixels_mascara": int((img != base).any(2).sum()),  # os pixels = 1 na máscara gravada
                "substituiu": anterior["nome"] if anterior else None}

    # ------------------------------------------------------------------ apagar a última edição
    def _no_dataset(self, h: dict) -> bool:
        return (self.raiz_saida / h["split"] / "anotacoes" / f"{h['nome']}.json").is_file()

    def ultima(self) -> dict | None:
        """A versão salva aqui mais recente que ainda está no dataset (a que o "Apagar a última edição" tira), ou None."""
        with self.lock:
            return next((h for h in reversed(self._estado()["historico"]) if self._no_dataset(h)), None)

    def desfazer(self, nome: str) -> dict:
        """Tira do dataset a versão ``nome`` salva aqui: a imagem editada, as máscaras, a anotação e a linha da planilha
        (``exportar.excluir_versao``). A imagem de entrada fica como está. Devolve a versão que saiu (com ``doc``, para
        a página voltar a ela) e a nova ``ultima``."""
        with self.lock:
            e = self._estado()
            h = next((x for x in reversed(e["historico"]) if x.get("nome") == nome), None)
            if h is None:
                raise ErroOperacao("essa edição não foi salva pelo estúdio (ou já foi apagada)")
            apagados = exportar.excluir_versao(self.raiz_saida, h["split"], h["nome"])["apagados"] \
                if self._no_dataset(h) else []
            e["historico"] = [x for x in e["historico"] if x is not h]
            if (e["salvos"].get(h["token"]) or {}).get("nome") == h["nome"]:
                del e["salvos"][h["token"]]  # salvar de novo essa abertura cria outra versão
            self._gravar_estado(e)
        planilha.agendar(self.raiz_saida, self.registro, self.ids)
        return {**h, "apagados": apagados, "ultima": self.ultima()}
