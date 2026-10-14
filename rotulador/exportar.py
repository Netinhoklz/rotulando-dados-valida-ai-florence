"""Gravação do par (original, editada) + máscaras + anotação, sempre no split de origem.

Regras anti-vazamento/anti-atalho aplicadas aqui:
  - o split vem do registro (Item), nunca do navegador; todo caminho escrito é
    conferido contra saida/<split>;
  - original e editada do par saem no MESMO formato e qualidade (o modelo não pode
    separar as classes pela compressão);
  - nenhum metadado (EXIF, ICC, texto) vai para os arquivos;
  - nomes neutros (hash do conteúdo), o nome original do arquivo fica só no JSON;
  - cada arquivo é gravado em .tmp e renomeado; o JSON é o último (marca de "completo").

Rótulos de cada versão: máscara de pixels alterados, de região, de CATEGORIA (o quê), de
TÉCNICA (o como, rotulador/catalogo.py) e de IGNORAR (rótulo ambíguo), mais o JSON com a lista ``edicoes`` (técnica,
categoria, texto antes/depois, caracteres alterados, descrição, caixa e pixels de cada
edição). Índices por split: manifesto.csv (uma linha por versão) e catalogo.csv (uma
linha por edição) — os dois são refeitos a partir dos JSON quando preciso (exclusão de
versão, cabeçalho antigo), então os JSON são a fonte da verdade.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import canal, catalogo, documentos, laudo as mod_laudo, marcas
from .registro import SPLITS
from .sessao import ErroOperacao, Sessao

VERSAO_FERRAMENTA = "2.3"
FORMATOS = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "tiff": ".tif", "pdf": ".pdf"}
COM_PERDA = {"jpeg", "webp", "pdf"}
CAMPOS_MANIFESTO = ["split", "doc", "versao", "original", "editada", "mascara", "mascara_regiao",
                    "mascara_classes", "mascara_tecnicas", "mascara_ignorar", "anotacao", "categorias", "tecnicas",
                    "niveis",
                    "n_edicoes", "origem", "formato", "qualidade", "largura", "altura", "pixels_alterados",
                    "fracao_alterada", "dificuldade", "fonte",
                    "pagina", "sha_arquivo", "sha_pixels", "operador", "data", "canal", "revisao", "revisor",
                    "falsificador", "motivo", "pericia"]
CAMPOS_CATALOGO = ["split", "doc", "versao", "nome", "largura", "altura", "editada", "anotacao", "ordem", "categoria", "tecnica",
                   "tecnica_num", "nome_tecnica", "familia", "nivel", "granularidade", "literatura", "texto_antigo",
                   "texto_novo", "pixels", "bbox", "descricao", "origem", "operador", "data", "canal", "revisao", "revisor",
                   "falsificador", "motivo", "pericia", "fornecedores"]
# revisão humana de cada versão (gravada na anotação; "pendente" = ainda não revisada)
ESTADOS_REVISAO = ("pendente", "aprovada", "reprovada")
MOTIVOS_REVISAO = {"artefato": "artefato visível (caixa, mancha, cor, borda)", "rotulo": "rótulo errado (técnica, classe, texto)",
                   "grafia": "erro de grafia ou valor impossível", "posicao": "fora do lugar ou desalinhado",
                   "fraca": "edição fraca demais (não se vê)", "outro": "outro motivo"}
_NOME_VERSAO = re.compile(r"^[0-9a-f]{8,64}__v\d{2,}$")
# cor de cada família na prévia da máscara de técnicas
CORES_FAMILIA = {"imitacao": (220, 60, 60), "copia_interna": (60, 140, 230), "colagem_externa": (160, 70, 220),
                 "remocao": (240, 160, 30), "pixel": (40, 190, 120), "codigos": (120, 120, 120),
                 "ia": (230, 70, 200), "manual": (200, 200, 60), "recriacao": (90, 200, 220)}
_lock = threading.Lock()


class CanalApagouEdicao(ErroOperacao):
    """O canal de envio deixou original e editada iguais (ou tirou a edição do quadro)."""


def _dentro(caminho: Path, raiz: Path) -> Path:
    c, r = caminho.resolve(), raiz.resolve()
    if not c.is_relative_to(r):
        raise ErroOperacao(f"caminho fora da pasta do split: {c}")
    return c


def codificar(img: np.ndarray, formato: str, qualidade: int, dpi: int = 200) -> bytes:
    """Codifica sem metadados. Usado igualzinho para original e editada."""
    im = Image.fromarray(img) if img.ndim == 3 else Image.fromarray(img, "L")
    buf = io.BytesIO()
    if formato == "png":
        im.save(buf, "PNG", compress_level=6)
    elif formato == "jpeg":
        im.save(buf, "JPEG", quality=qualidade, subsampling=0 if qualidade >= 95 else 2, optimize=False)
    elif formato == "webp":
        im.save(buf, "WEBP", quality=qualidade, lossless=qualidade >= 100, method=4)
    elif formato == "tiff":
        im.save(buf, "TIFF", compression="tiff_lzw")
        return _zerar_alinhamento_tiff(buf.getvalue())
    elif formato == "pdf":
        # sem data/hora: a original é gravada antes das editadas, e a data separaria as classes
        im.save(buf, "PDF", resolution=float(dpi), quality=qualidade, creationDate=None, modDate=None)
    else:
        raise ErroOperacao(f"formato de saída desconhecido: {formato}")
    return buf.getvalue()


def _zerar_alinhamento_tiff(dados: bytes) -> bytes:
    """O libtiff alinha o IFD em palavra e deixa o byte de enchimento com lixo de memória: o mesmo
    conteúdo gravado duas vezes saía com um byte diferente. Zera esse byte (só se ele está entre o
    fim dos dados da imagem e o IFD)."""
    if dados[:4] != b"II*\x00":
        return dados
    ifd = int.from_bytes(dados[4:8], "little")
    with Image.open(io.BytesIO(dados)) as im:
        fim = max(o + n for o, n in zip(im.tag_v2.get(273, ()), im.tag_v2.get(279, ())))
    if fim == ifd - 1:
        dados = dados[:fim] + b"\x00" + dados[ifd:]
    return dados


def _gravar(caminho: Path, dados: bytes) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(caminho.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(dados)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, caminho)


def proxima_versao(pasta: Path, doc: str) -> int:
    padrao = re.compile(re.escape(doc) + r"__v(\d+)")
    maior = 0
    for sub in ("editadas", "anotacoes"):
        d = pasta / sub
        if d.is_dir():
            for arq in d.iterdir():
                m = padrao.match(arq.name)
                if m:
                    maior = max(maior, int(m.group(1)))
    return maior + 1


def versoes_salvas(raiz_saida: Path, split: str, doc: str) -> int:
    return proxima_versao(Path(raiz_saida) / split, doc) - 1


def _caixas_por_categoria(classes: np.ndarray, ids: dict[str, int]) -> dict[str, list[int]]:
    out = {}
    for chave, i in ids.items():
        m = classes == i
        if m.any():
            ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
            out[chave] = [int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1]
    return out


def _caixas_ocorrencias(edicoes: list[dict]) -> dict[str, list[list[int]]]:
    """Uma caixa por edição de cada categoria: o nome trocado no envelope e no boleto são duas caixas (a caixa única
    de ``caixas_por_categoria`` cobria a página toda entre os dois)."""
    out: dict[str, list[list[int]]] = {}
    for e in edicoes:
        if e.get("categoria") and e.get("bbox"):
            out.setdefault(e["categoria"], []).append(list(e["bbox"]))
    return out


def mascara_ignorar(alterados: np.ndarray, regiao: np.ndarray, espalhado: np.ndarray | None = None) -> np.ndarray:
    """Pixels de rótulo ambíguo: 1 px em volta do que mudou (antialias, reamostragem do canal), o que
    está na região editada sem ter mudado (ex.: papel dentro do retângulo de um campo) e o que o codec
    (canal de envio ou gravação com perda) espalhou fora da região (``espalhado``: onde a original e a
    editada gravadas diferem). A banda "sem nota" do NIST MFC; quem treina pode zerar a perda ali e tirar
    da métrica. Só a borda de FORA: com ±1 px, um traço de 1–2 px reduzido pelo canal ficava quase todo
    ignorado."""
    a = alterados.astype(np.uint8)
    borda = (cv2.dilate(a, np.ones((3, 3), np.uint8)) > 0) & ~alterados
    out = borda | (regiao & ~alterados)
    return out | (espalhado & ~regiao) if espalhado is not None else out


def mascara_tecnicas(mapa: np.ndarray, ents) -> np.ndarray:
    """uint8: num da técnica (catálogo) de cada pixel editado; 0 = não editado."""
    lut = np.zeros(len(ents) + 1, np.uint8)
    for n, e in enumerate(ents, 1):
        t = catalogo.tecnica_da_operacao(e.tipo, e.info.get("params"))
        lut[n] = t.num if t else catalogo.DESCONHECIDA
    return lut[mapa]


def colorir_tecnicas(m: np.ndarray) -> np.ndarray:
    """RGB para ver a máscara de técnicas: cada família com a sua cor, fundo preto."""
    lut = np.zeros((256, 3), np.uint8)
    for t in catalogo.TECNICAS.values():
        lut[t.num] = CORES_FAMILIA.get(t.familia, (255, 255, 255))
    lut[catalogo.DESCONHECIDA] = (255, 255, 255)
    return lut[m]


# quem fez cada edição: técnica do catálogo (automática), troca de campo do modo simples (a pessoa escreve o valor,
# o resto é automático) ou ferramenta do editor
_ORIGEM_OPERACAO = {"tecnica": "automatica", "trocar_campo": "assistida", "trocar_campo_ia": "assistida"}


def descrever(n: int, r: dict, pixels: int, bbox) -> dict:
    """Uma edição do histórico (Entrada.resumo) como rótulo: técnica, categoria, textos, descrição."""
    t = catalogo.tecnica_da_operacao(r.get("tipo", ""), r.get("params"))
    meta, params = r.get("meta") or {}, r.get("params") or {}
    antigo = meta.get("texto_antigo", params.get("texto_antigo"))
    novo = meta.get("texto_novo")
    if novo is None and r.get("tipo") in ("trocar_campo", "trocar_campo_ia", "texto", "substituir_texto"):
        novo = params.get("texto")
    descricao = meta.get("descricao")
    if not descricao:
        descricao = (t.nome if t else str(r.get("rotulo", ""))) + (
            f": '{antigo}' → '{novo}'" if antigo is not None and novo is not None else "")
    d = {"ordem": n, "operacao": r.get("tipo"), "categoria": r.get("categoria"), "camada": r.get("nome_camada"),
         "tecnica": t.id if t else "desconhecida", "tecnica_num": t.num if t else catalogo.DESCONHECIDA,
         "nome_tecnica": t.nome if t else str(r.get("rotulo", "")), "familia": t.familia if t else None,
         "nivel": t.nivel if t else None, "granularidade": t.granularidade if t else None,
         "literatura": t.literatura if t else None, "texto_antigo": antigo, "texto_novo": novo,
         "alteracoes": meta.get("alteracoes"), "opcoes": meta.get("opcoes"), "acabamento": meta.get("acabamento"),
         "pericia": meta.get("pericia"), "fornecedores": meta.get("fornecedores"), "campo": meta.get("campo"),
         "descricao": descricao, "bbox": [int(v) for v in bbox] if bbox is not None else None, "pixels": int(pixels)}
    d["origem"] = _ORIGEM_OPERACAO.get(r.get("tipo"), "manual")
    # conferido_ocr: True = o OCR leu o valor novo; None = não deu para julgar; sem a chave = não se aplica.
    # peso_traco: espessura do traço novo ÷ a do original (1 = igual); semelhanca_fonte: o quanto a fonte usada casou
    for k in ("origem_glifo", "origem_campo", "trecho", "linha_digitavel", "conferido_ocr", "peso_traco",
              "semelhanca_fonte"):
        if k in meta:
            d[k] = meta[k]
    return d


def edicoes_da_sessao(mapa: np.ndarray, ents, caixa_da_sessao: bool = True) -> list[dict]:
    """Só as edições que aparecem na imagem salva (cobertas por outra ou em camada oculta saem).
    ``caixa_da_sessao=False`` procura cada edição no mapa inteiro: depois do canal de envio a
    geometria mudou e a caixa gravada na sessão não vale mais."""
    out = []
    for n, e in enumerate(ents, 1):
        x0, y0, x1, y1 = (e.info.get("bbox") if caixa_da_sessao else None) or (0, 0, mapa.shape[1], mapa.shape[0])
        sub = mapa[y0:y1, x0:x1] == n
        px = int(sub.sum())
        if not px:
            continue
        ys, xs = np.flatnonzero(sub.any(1)), np.flatnonzero(sub.any(0))
        caixa = [x0 + int(xs[0]), y0 + int(ys[0]), x0 + int(xs[-1]) + 1, y0 + int(ys[-1]) + 1]
        out.append(descrever(n, e.resumo(), px, caixa))
    return out


def edicoes_de_anotacao(anot: dict) -> list[dict]:
    """Edições de uma anotação; as anteriores à versão 2.0 são reconstruídas das operações."""
    if "edicoes" in anot:
        return anot["edicoes"]
    visiveis = {c["id"] for c in anot.get("camadas", []) if c.get("visivel", True)}
    out, n = [], 0
    for r in anot.get("operacoes", []):
        if r.get("tipo") == "camada" or r.get("restaura") or (visiveis and r.get("camada") not in visiveis):
            continue
        n += 1
        out.append(descrever(n, r, r.get("pixels_alterados", 0), r.get("bbox")))
    return out


def _origem(anot: dict) -> str:
    """gerador | automatico | assistido | manual (anotações anteriores à 2.3: só gerador | manual)."""
    return anot.get("origem") or ("gerador" if "gerador" in anot else "manual")


def _origem_versao(edicoes: list[dict]) -> str:
    """Versão salva no editor: "automatico" (só técnicas do catálogo), "manual" (só ferramentas do editor) ou
    "assistido" (troca de campo do modo simples, ou mistura)."""
    tipos = {e.get("origem") or "manual" for e in edicoes}
    return "automatico" if tipos == {"automatica"} else "manual" if tipos <= {"manual"} else "assistido"


def _linha_manifesto(anot: dict) -> dict:
    eds = edicoes_de_anotacao(anot)
    arqs = anot.get("arquivos", {})
    return {"split": anot["split"], "doc": anot["doc"], "versao": anot["versao"],
            **{k: arqs.get(k, "") for k in ("original", "editada", "mascara", "mascara_regiao", "mascara_classes",
                                            "mascara_tecnicas", "mascara_ignorar", "anotacao")},
            "categorias": ";".join(anot.get("categorias", [])),
            "tecnicas": ";".join(sorted({e["tecnica"] for e in eds})),
            "niveis": ";".join(sorted({e["nivel"] for e in eds if e.get("nivel")})),
            "n_edicoes": len(eds), "origem": _origem(anot), "formato": anot.get("formato", ""),
            "qualidade": qualidade_saida(anot) or "", "largura": anot.get("largura"), "altura": anot.get("altura"),
            "pixels_alterados": anot.get("pixels_alterados"), "fracao_alterada": anot.get("fracao_alterada"),
            "dificuldade": dificuldade_de(anot), "fonte": anot.get("fonte", {}).get("arquivo", ""),
            "pagina": anot.get("fonte", {}).get("pagina", ""), "sha_arquivo": anot.get("fonte", {}).get("sha_arquivo", ""),
            "sha_pixels": anot.get("fonte", {}).get("sha_pixels", ""), "operador": anot.get("operador", ""),
            "data": anot.get("data", ""), "canal": _canal(anot), **_revisao(anot), **_falsificador(anot),
            "pericia": anot.get("pericia") or "auto"}


_RANK_PERICIA = {"cru": 0, "auto": 0, "padrao": 1, "profissional": 2, "forense": 3}


def _pericia_versao(edicoes: list) -> str:
    """Nível de acabamento forense mais alto entre as edições da versão ('auto' = nenhuma perícia)."""
    niveis = [e.get("pericia") or "auto" for e in edicoes]
    return max(niveis, key=lambda p: _RANK_PERICIA.get(p, 0), default="auto") if niveis else "auto"


def dificuldade_de(anot: dict) -> float | None:
    """0 = a edição salta aos olhos do perito (rotulador.perito), 1 = invisível para ele: 1 − MAIOR nota entre as
    edições medidas (``edicoes[].perito.nota``; a mais visível entrega a versão, e mais uma edição discreta não a
    torna mais difícil); None se nenhuma foi medida. Lê ``dificuldade`` quando já gravada. É o que ordena o
    currículo e a mineração de casos difíceis no treino (rotulador.treino.dados)."""
    if anot.get("dificuldade") is not None:
        return float(anot["dificuldade"])
    notas = [e["perito"]["nota"] for e in edicoes_de_anotacao(anot) if (e.get("perito") or {}).get("nota") is not None]
    return round(1.0 - max(notas), 4) if notas else None


def qualidade_saida(anot: dict) -> int | None:
    """Qualidade do codec com perda da versão (None = sem perda). Até a 2.2, ``auditar_qualidade.py --gravar``
    escrevia os alertas por cima desta chave: aí ela volta do nome do arquivo (``__q95``)."""
    q = anot.get("qualidade")
    if q is None or isinstance(q, int):
        return q
    m = re.search(r"__q(\d+)\.", (anot.get("arquivos") or {}).get("editada", ""))
    return int(m.group(1)) if m else None


def _falsificador(anot: dict) -> dict:
    """Quem "fez" a versão no gerador (rotulador.falsificador) e com que motivo ("" = sorteio clássico/manual)."""
    f = anot.get("falsificador") or {}
    return {"falsificador": f.get("nome", ""), "motivo": f.get("motivo", "")}


def _revisao(anot: dict) -> dict:
    r = anot.get("revisao") or {}
    return {"revisao": r.get("estado", "pendente"), "revisor": r.get("por", "")}


def _canal(anot: dict) -> str:
    """Perfil e passos do canal de envio da versão ("" = imagem como estava)."""
    c = anot.get("canal")
    return f"{c['perfil']}: {' > '.join(p['tipo'] for p in c['passos'])}" if c else ""


def _linhas_catalogo(anot: dict) -> list[dict]:
    nome = f"{anot['doc']}__v{int(anot['versao']):02d}"
    base = {"split": anot["split"], "doc": anot["doc"], "versao": anot["versao"], "nome": nome,
            "largura": anot.get("largura"), "altura": anot.get("altura"),
            "editada": anot.get("arquivos", {}).get("editada", ""), "anotacao": anot.get("arquivos", {}).get("anotacao", ""),
            "origem": _origem(anot), "operador": anot.get("operador", ""), "data": anot.get("data", ""),
            "canal": _canal(anot), **_revisao(anot), **_falsificador(anot), "pericia": anot.get("pericia") or "auto"}
    return [{**base, **{k: e.get(k) for k in ("ordem", "categoria", "tecnica", "tecnica_num", "nome_tecnica", "familia",
                                               "nivel", "granularidade", "literatura", "texto_antigo", "texto_novo",
                                               "pixels", "descricao")},
             "bbox": " ".join(str(v) for v in (e.get("bbox") or [])),
             "fornecedores": ";".join(f"{k}={v}" for k, v in (e.get("fornecedores") or {}).items())}
            for e in edicoes_de_anotacao(anot)]


def _anotacoes(raiz_split: Path) -> list[dict]:
    pasta = raiz_split / "anotacoes"
    out = []
    if pasta.is_dir():
        for arq in sorted(pasta.glob("*.json")):
            try:
                out.append(json.loads(arq.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return out


def _gravar_csv(caminho: Path, campos: list[str], linhas: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, campos, extrasaction="ignore")
    w.writeheader()
    w.writerows(linhas)
    _gravar(caminho, buf.getvalue().encode("utf-8"))


def _reconstruir(raiz_split: Path) -> None:
    anots = _anotacoes(raiz_split)
    _gravar_csv(raiz_split / "manifesto.csv", CAMPOS_MANIFESTO, [_linha_manifesto(a) for a in anots])
    _gravar_csv(raiz_split / "catalogo.csv", CAMPOS_CATALOGO, [l for a in anots for l in _linhas_catalogo(a)])


def reconstruir_indices(raiz_saida: Path, split: str) -> None:
    """Refaz manifesto.csv e catalogo.csv do split a partir dos JSON de anotação."""
    if split not in SPLITS:
        raise ErroOperacao("split inválido")
    with _lock:
        _reconstruir((Path(raiz_saida) / split).resolve())


def _cabecalho(caminho: Path) -> list[str] | None:
    try:
        with open(caminho, encoding="utf-8", newline="") as f:
            return next(csv.reader(f), None)
    except OSError:
        return None


def _indexar(raiz_split: Path, anot: dict) -> None:
    """Acrescenta a versão aos índices; se um índice é de outra versão da ferramenta (ou falta), refaz tudo."""
    man, cat = raiz_split / "manifesto.csv", raiz_split / "catalogo.csv"
    ja_havia = len(list((raiz_split / "anotacoes").glob("*.json"))) > 1
    if (_cabecalho(man) or (CAMPOS_MANIFESTO if not ja_havia else None)) != CAMPOS_MANIFESTO or \
            (_cabecalho(cat) or (CAMPOS_CATALOGO if not ja_havia else None)) != CAMPOS_CATALOGO:
        _reconstruir(raiz_split)
        return
    for caminho, campos, linhas in ((man, CAMPOS_MANIFESTO, [_linha_manifesto(anot)]),
                                    (cat, CAMPOS_CATALOGO, _linhas_catalogo(anot))):
        novo = not caminho.exists()
        with open(caminho, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, campos, extrasaction="ignore")
            if novo:
                w.writeheader()
            w.writerows(linhas)


def _legenda(raiz_saida: Path, ids_categoria: dict[str, int]) -> None:
    """catalogo.json na raiz da saída: o que significa cada valor das máscaras de categoria e de técnica."""
    dados = {**catalogo.legenda(), "categorias": ids_categoria}
    novo = json.dumps(dados, ensure_ascii=False, indent=1).encode("utf-8")
    arq = Path(raiz_saida) / "catalogo.json"
    try:
        if arq.read_bytes() == novo:
            return
    except OSError:
        pass
    _gravar(arq, novo)


def _apagar(caminho: Path) -> None:
    """unlink que espera um pouco se o arquivo estiver aberto (no Windows, uma miniatura sendo lida
    ao mesmo tempo trava o arquivo por alguns instantes)."""
    for _ in range(25):
        try:
            caminho.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.2)
    raise ErroOperacao(f"o arquivo {caminho.name} está em uso; tente de novo")


def excluir_versao(raiz_saida: Path, split: str, nome: str) -> dict:
    """Apaga uma versão (editada, máscaras, anotação) e refaz os índices do split. Se o documento
    fica sem nenhuma versão, o original salvo dele também sai."""
    if split not in SPLITS or not _NOME_VERSAO.match(str(nome)):
        raise ErroOperacao("versão inválida")
    with _lock:
        raiz_split = (Path(raiz_saida) / split).resolve()
        anot_arq = _dentro(raiz_split / "anotacoes" / f"{nome}.json", raiz_split)
        if not anot_arq.is_file():
            raise ErroOperacao("versão não encontrada")
        anot = json.loads(anot_arq.read_text(encoding="utf-8"))
        apagados = []
        for k, rel in anot.get("arquivos", {}).items():
            if k == "original" and "__v" not in Path(rel).name:
                continue  # original comum às versões; sai só quando o documento fica sem nenhuma
            c = _dentro(raiz_split / rel, raiz_split)
            if c.is_file():
                _apagar(c)
                apagados.append(rel)
        _apagar(anot_arq)  # por último: a anotação é a marca de "versão completa"
        doc = anot["doc"]
        # originais que nenhuma versão restante usa saem (a comum ou a de uma versão com canal)
        usadas = set()
        for a in (raiz_split / "anotacoes").glob(f"{doc}__v*.json"):
            try:
                usadas.add(json.loads(a.read_text(encoding="utf-8")).get("arquivos", {}).get("original"))
            except ValueError:
                usadas.add(None)  # anotação ilegível: na dúvida, não apaga nada
        if None not in usadas:
            for c in (raiz_split / "originais").glob(f"{doc}*"):
                if c.relative_to(raiz_split).as_posix() not in usadas:
                    _apagar(_dentro(c, raiz_split))
                    apagados.append(c.relative_to(raiz_split).as_posix())
        _reconstruir(raiz_split)
        return {"apagados": apagados, "split": split, "nome": nome}


_cache_catalogo: dict[str, tuple] = {}


def ler_catalogo(raiz_saida: Path) -> list[dict]:
    """Linhas do catalogo.csv de todos os splits (refaz o índice se ele ainda não existe). O CSV
    lido fica em memória enquanto o arquivo não muda (data e tamanho): com milhares de edições,
    reler a cada página do catálogo pesava."""
    linhas = []
    for sp in SPLITS:
        raiz_split = (Path(raiz_saida) / sp).resolve()
        cat = raiz_split / "catalogo.csv"
        with _lock:  # no Windows, refazer o índice (os.replace) com o arquivo aberto falha
            if (raiz_split / "anotacoes").is_dir() and _cabecalho(cat) != CAMPOS_CATALOGO:
                _reconstruir(raiz_split)
            if cat.is_file():
                st = cat.stat()
                chave = (st.st_mtime_ns, st.st_size)
                guardado = _cache_catalogo.get(str(cat))
                if guardado is None or guardado[0] != chave:
                    with open(cat, encoding="utf-8", newline="") as f:
                        guardado = _cache_catalogo[str(cat)] = (chave, list(csv.DictReader(f)))
                linhas.extend(guardado[1])
    return linhas


def revisar(raiz_saida: Path, itens: list, estado: str, por: str, motivos=(), comentario: str = "") -> int:
    """Grava a revisão humana nas anotações (``itens``: [(split, nome)]) e refaz os índices.
    "pendente" apaga a revisão. Reprovar pede um motivo."""
    if estado not in ESTADOS_REVISAO:
        raise ErroOperacao("estado de revisão inválido")
    motivos = [m for m in (motivos or []) if m in MOTIVOS_REVISAO]
    comentario = str(comentario or "").strip()[:500]
    if estado == "reprovada" and not motivos and not comentario:
        raise ErroOperacao("diga o motivo da reprovação")
    if not itens:
        raise ErroOperacao("nenhuma versão escolhida")
    raiz_saida = Path(raiz_saida)
    tocados = set()
    with _lock:
        for split, nome in itens:
            if split not in SPLITS or not _NOME_VERSAO.match(str(nome)):
                raise ErroOperacao(f"versão inválida: {split}/{nome}")
            raiz_split = (raiz_saida / split).resolve()
            arq = _dentro(raiz_split / "anotacoes" / f"{nome}.json", raiz_split)
            if not arq.is_file():
                raise ErroOperacao(f"versão não encontrada: {split}/{nome}")
            a = json.loads(arq.read_text(encoding="utf-8"))
            if estado == "pendente":
                a.pop("revisao", None)
            else:
                a["revisao"] = {"estado": estado, "motivos": motivos, "comentario": comentario, "por": por,
                                "em": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "autorrevisao": bool(por) and por == a.get("operador")}
            _gravar(arq, json.dumps(a, ensure_ascii=False, indent=1).encode("utf-8"))
            tocados.add(raiz_split)
        for raiz_split in tocados:
            _reconstruir(raiz_split)
    return len(itens)


def exportar_subconjunto(raiz_saida: Path, versoes: list, nome: str, filtros: dict) -> dict:
    """Manifesto (CSV e JSON) de um subconjunto filtrado do catálogo, para treinar só com ele (ex.: só
    as aprovadas; sem uma técnica, para o teste deixa-uma-de-fora). Os caminhos são relativos a
    ``saida/``. Fica em saida/_exportacoes/<nome>/ (fora dos splits)."""
    nome = re.sub(r"[^\w.-]+", "_", str(nome or "").strip())[:60].strip("._") or "exportacao"
    raiz_saida = Path(raiz_saida)
    destino = raiz_saida / "_exportacoes" / nome
    linhas = []
    for v in versoes:
        sp, nv = v["split"], v["nome"]
        arq = raiz_saida / sp / "anotacoes" / f"{nv}.json"
        if sp not in SPLITS or not _NOME_VERSAO.match(str(nv)) or not arq.is_file():
            continue
        linha = _linha_manifesto(json.loads(arq.read_text(encoding="utf-8")))
        for k in ("original", "editada", "mascara", "mascara_regiao", "mascara_classes", "mascara_tecnicas",
                  "mascara_ignorar", "anotacao"):
            if linha.get(k):
                linha[k] = f"{sp}/{linha[k]}"
        linhas.append(linha)
    if not linhas:
        raise ErroOperacao("nenhuma versão com esses filtros")
    _gravar_csv(destino / "manifesto.csv", CAMPOS_MANIFESTO, linhas)
    resumo = {"nome": nome, "data": time.strftime("%Y-%m-%dT%H:%M:%S"), "filtros": filtros, "versoes": len(linhas),
              "por_split": {sp: sum(1 for l in linhas if l["split"] == sp) for sp in SPLITS},
              "nota": "caminhos relativos à pasta saida/; original e editada de cada linha formam o par",
              "linhas": linhas}
    _gravar(destino / "manifesto.json", json.dumps(resumo, ensure_ascii=False, indent=1).encode("utf-8"))
    return {k: resumo[k] for k in ("nome", "versoes", "por_split", "filtros")} | {"pasta": str(destino)}


def salvar(sessao: Sessao, raiz_saida: Path, formato: str = "png", qualidade: int = 95,
           operador: str = "", dpi: int = 200, extra: dict | None = None, canal_envio: dict | None = None,
           marcas_benignas: dict | None = None, por_versao: bool = False) -> dict:
    """Grava a versão. ``canal_envio`` (rotulador.canal.sortear): o par passa pelo mesmo canal
    (impressão, scanner, celular, WhatsApp...); só os passos da fase "depois" entram aqui (os de "antes
    da edição" já estão na base da sessão, aplicados pelo gerador). ``marcas_benignas``
    (rotulador.marcas.sortear): carimbo, anotação, dobra... iguais no par, longe da edição, antes do
    canal. Com qualquer um dos dois (ou ``por_versao``: a base já é uma captura própria desta versão), a
    original desta versão é gravada à parte."""
    por_versao = bool(canal_envio or marcas_benignas or por_versao)
    extra = dict(extra or {})
    perito_eds = extra.pop("perito_por_edicao", None) or {}  # {ordem: {"nota", "medidas"}}, do gerador
    formato = formato.lower()
    if formato not in FORMATOS:
        raise ErroOperacao(f"formato de saída desconhecido: {formato}")
    qualidade = int(max(1, min(100, qualidade)))
    with sessao.lock, _lock:
        ok, motivo = sessao.pode_salvar()
        if not ok:
            raise ErroOperacao(f"não dá para salvar: {motivo}")
        it = sessao.item
        if it.split not in SPLITS:
            raise ErroOperacao("split inválido")
        raiz_split = (Path(raiz_saida) / it.split).resolve()
        raiz_split.mkdir(parents=True, exist_ok=True)
        versao = proxima_versao(raiz_split, it.doc)
        ext = FORMATOS[formato]
        sufixo_q = f"__q{qualidade}" if formato in COM_PERDA else ""
        nome = f"{it.doc}__v{versao:02d}"
        arq = {
            "original": raiz_split / "originais" / f"{nome if por_versao else it.doc}{sufixo_q}{ext}",
            "editada": raiz_split / "editadas" / f"{nome}{sufixo_q}{ext}",
            "mascara": raiz_split / "mascaras" / f"{nome}.png",
            "mascara_regiao": raiz_split / "mascaras_regiao" / f"{nome}.png",
            "mascara_classes": raiz_split / "mascaras_classes" / f"{nome}.png",
            "mascara_tecnicas": raiz_split / "mascaras_tecnicas" / f"{nome}.png",
            "mascara_ignorar": raiz_split / "mascaras_ignorar" / f"{nome}.png",
            "anotacao": raiz_split / "anotacoes" / f"{nome}.json",
        }
        for c in arq.values():
            _dentro(c, raiz_split)

        alterados, regiao, classes = sessao.mascaras()
        mapa, ents = sessao.mapa_edicoes()
        img_orig, img_ed = sessao.base, sessao.atual
        espalhado = None
        info_marcas = []
        if marcas_benignas:
            (img_orig, img_ed), info_marcas = marcas.aplicar([img_orig, img_ed], regiao, marcas_benignas)
        if canal_envio:
            mapa_marcas = np.zeros(regiao.shape, np.uint8)  # as caixas das marcas passam pelo canal também
            for i, m in enumerate(info_marcas, 1):
                x0, y0, x1, y1 = m["caixa"]
                mapa_marcas[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = i
            (img_orig, img_ed), (alterados, regiao), (classes, mapa, mapa_marcas) = canal.aplicar(
                [img_orig, img_ed], [alterados, regiao], [classes, mapa, mapa_marcas], canal_envio, fase="depois")
            for i, m in enumerate(info_marcas, 1):
                ys, xs = np.nonzero(mapa_marcas == i)
                m["caixa"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if ys.size else None
            info_marcas = [m for m in info_marcas if m["caixa"]]
            classes = canal.ajustar_rotulos(classes, regiao)
            mapa = canal.ajustar_rotulos(mapa, regiao)
            regiao &= (classes > 0) & (mapa > 0)
            classes[~regiao] = 0
            mapa[~regiao] = 0
            dif = np.abs(img_orig.astype(np.int16) - img_ed.astype(np.int16)).max(2)
            alterados &= regiao & (dif > 0)  # o que o canal apagou (saturação do scanner) não mudou mais
            espalhado = dif > 0  # blocos do JPEG e reamostragem em volta da edição (Δ pequeno também): rótulo ambíguo
            if not regiao.any() or not alterados.any():  # par "adulterado" idêntico à original: não serve
                raise CanalApagouEdicao("o canal de envio apagou a edição (original e editada ficaram iguais)")
            presentes = set(np.unique(classes[regiao]).tolist()) - {0}
            categorias = [c for c, i in sessao.ids_categoria.items() if i in presentes]
        else:
            if info_marcas:  # marca por cima da edição (``sobre_edicao``): onde ela cobre, o pixel pode não diferir mais
                alterados &= np.abs(img_orig.astype(np.int16) - img_ed.astype(np.int16)).max(2) > 0
                if not alterados.any():
                    raise CanalApagouEdicao("a marca cobriu a edição inteira (original e editada ficaram iguais)")
            categorias = sessao.categorias(exato=True)
        tecnicas = mascara_tecnicas(mapa, ents)
        edicoes = edicoes_da_sessao(mapa, ents, caixa_da_sessao=not canal_envio)
        for e in edicoes:  # as pistas de pixel medidas pelo perito na sessão (antes do canal "depois")
            if e["ordem"] in perito_eds or str(e["ordem"]) in perito_eds:
                e["perito"] = perito_eds.get(e["ordem"], perito_eds.get(str(e["ordem"])))
        if por_versao or not arq["original"].exists():
            _gravar(arq["original"], codificar(img_orig, formato, qualidade, dpi))
        _gravar(arq["editada"], codificar(img_ed, formato, qualidade, dpi))
        if formato in COM_PERDA:  # o codec da saída espalha a edição em volta: compara o par como o treino vai ler
            o_lida, e_lida = (documentos.carregar(arq[k], 0, dpi) for k in ("original", "editada"))
            if o_lida.shape == e_lida.shape == img_ed.shape:
                dif_saida = np.abs(o_lida.astype(np.int16) - e_lida.astype(np.int16)).max(2) > 0
                espalhado = dif_saida if espalhado is None else espalhado | dif_saida
        _gravar(arq["mascara"], codificar(alterados.astype(np.uint8) * 255, "png", 0))
        _gravar(arq["mascara_regiao"], codificar(regiao.astype(np.uint8) * 255, "png", 0))
        _gravar(arq["mascara_classes"], codificar(classes, "png", 0))
        _gravar(arq["mascara_tecnicas"], codificar(tecnicas, "png", 0))
        _gravar(arq["mascara_ignorar"], codificar(mascara_ignorar(alterados, regiao, espalhado).astype(np.uint8) * 255,
                                                  "png", 0))

        H, W = alterados.shape
        rel = {k: v.relative_to(raiz_split).as_posix() for k, v in arq.items()}
        data = time.strftime("%Y-%m-%dT%H:%M:%S")
        ids_usados = {c: sessao.ids_categoria[c] for c in categorias}
        anotacao = {
            "versao_ferramenta": VERSAO_FERRAMENTA,
            "split": it.split, "doc": it.doc, "versao": versao,
            "fonte": {"arquivo": it.rel, "pagina": it.pagina, "paginas": it.paginas,
                      "sha_arquivo": it.sha_arquivo, "sha_pixels": it.sha_pixels,
                      "dpi_pdf": dpi if it.caminho.suffix.lower() == ".pdf" else None},
            "arquivos": rel, "formato": formato, "qualidade": qualidade if formato in COM_PERDA else None,
            "largura": W, "altura": H,
            "categorias": categorias, "ids_categoria": ids_usados,
            "caixas_por_categoria": _caixas_por_categoria(classes, ids_usados),
            "caixas_ocorrencias_por_categoria": _caixas_ocorrencias(edicoes),
            "pixels_alterados": int(alterados.sum()), "pixels_regiao": int(regiao.sum()),
            "fracao_alterada": round(float(alterados.mean()), 6),
            "mascaras": {"mascara": "0/255: pixels cujo valor mudou (antes da compressão de saída)",
                         "mascara_regiao": "0/255: área editada (pegada das operações)",
                         "mascara_classes": "uint8: id da categoria por pixel; 0 = não editado",
                         "mascara_tecnicas": "uint8: num da técnica por pixel (catalogo.json); 0 = não editado",
                         "mascara_ignorar": "0/255: rótulo ambíguo, para tirar da perda e da métrica: 1 px em volta "
                                            "dos pixels alterados (por fora), a parte da região que não mudou e o que "
                                            "o codec (canal ou gravação com perda) espalhou: fora da região, todo "
                                            "pixel em que a original e a editada gravadas diferem"},
            "edicoes": edicoes,
            "tecnicas": sorted({e["tecnica"] for e in edicoes}),
            "ids_tecnica": {e["tecnica"]: e["tecnica_num"] for e in edicoes},
            "niveis": sorted({e["nivel"] for e in edicoes if e.get("nivel")}),
            "familias": sorted({e["familia"] for e in edicoes if e.get("familia")}),
            "pericia": _pericia_versao(edicoes),  # acabamento forense mais alto entre as edições (cru→forense)
            "dificuldade": dificuldade_de({"edicoes": edicoes}),  # 1 − maior nota do perito: currículo no treino
            "origem": "gerador" if "gerador" in extra else _origem_versao(edicoes),
            "camadas": [{k: c[k] for k in ("id", "nome", "categoria", "descricao", "visivel", "opacidade", "bbox", "pixels")}
                        for c in sessao.lista_camadas()],
            "nota_camadas": "camadas ocultas (visivel=false) não entram na editada nem nas máscaras",
            "operacoes": sessao.historico(),
            "canal": canal_envio, "canal_descricao": canal.descrever(canal_envio),
            **({"nota_coordenadas": "camadas, operacoes e ocorrencias_antigas estão nas coordenadas da sessão (antes "
                                    "do canal de envio); edicoes, caixas_por_categoria, "
                                    "caixas_ocorrencias_por_categoria e marcas_benignas, nas da imagem salva"}
               if canal_envio else {}),
            "marcas_benignas": info_marcas,
            "nota_marcas": "marcas benignas: iguais na original e na editada, não são fraude (máscaras = 0 nelas); ficam "
                           "fora da edição, menos a com sobre_edicao: true, desenhada por cima de uma linha editada (ali "
                           "a região continua rotulada e a mascara só marca o que ainda difere entre as duas)",
            "operador": operador, "data": data,
            **extra,
        }
        anotacao["laudo"] = mod_laudo.gerar(anotacao)  # o relatório pericial em texto (para detectores que explicam)
        _gravar(arq["anotacao"], json.dumps(anotacao, ensure_ascii=False, indent=1).encode("utf-8"))
        _indexar(raiz_split, anotacao)
        _legenda(raiz_saida, sessao.ids_categoria)
        sessao.recomecar()
        return {"versao": versao, "arquivos": rel, "categorias": categorias, "split": it.split,
                "edicoes": len(edicoes), "lista_edicoes": edicoes, "largura": W, "altura": H}
