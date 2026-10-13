"""Teste da página do GERADOR AUTOMÁTICO e do "como editar" do modo simples (Edge headless),
contra o servidor em 5077 com os documentos realistas de gerar_amostras.py --realista.

    python tests_ui/ui_gerador.py E:/rotulador/real

Usa a página como uma pessoa: presets de técnicas e classes, seleção de documentos, prévia,
geração em lote, catálogo (matriz técnica × classe, filtros, detalhe, exclusão) e, no editor,
uma técnica do catálogo aplicada num campo. Falha com erro de JavaScript, traceback no servidor
ou qualquer passo que não aconteça.
"""
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

sys.stdout.reconfigure(encoding="utf-8")
PASTA = Path(sys.argv[1])
URL = "http://127.0.0.1:5077"
LOG = PASTA / "servidor.log"


def api(rota, corpo=None):
    req = urllib.request.Request(URL + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


for sp in ("treino", "teste", "validacao"):
    for item in api(f"/api/documentos?split={sp}"):
        api("/api/descartar", {"id": item["id"]})
    shutil.rmtree(PASTA / "saida" / sp, ignore_errors=True)

op = webdriver.EdgeOptions()
op.add_argument("--headless=new")
op.add_argument("--window-size=1700,1050")
op.set_capability("ms:loggingPrefs", {"browser": "ALL"})
d = webdriver.Edge(options=op)
falhas = []


def checa(cond, msg):
    print(("OK    " if cond else "FALHA ") + msg, flush=True)
    if not cond:
        falhas.append(msg)


def js(c, *a):
    return d.execute_script(c, *a)


def espera(cond, t=60):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            if js("return " + cond):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def clicar(seletor, texto=None):
    els = d.find_elements(By.CSS_SELECTOR, seletor)
    if texto is not None:
        els = [e for e in els if texto in e.text]
    if not els:
        raise AssertionError(f"não achei {seletor} {texto or ''}")
    js("arguments[0].scrollIntoView({block:'center'})", els[0])
    els[0].click()
    return els[0]


def erros_js():
    return [l for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]]


try:
    # ------------------------------------------------------------------ página
    d.get(URL + "/gerador")
    # a receita completa fica em "Opções avançadas" (recolhida por padrão): o teste a abre
    d.execute_script("localStorage.clear(); localStorage.setItem('rotulador.operador', JSON.stringify('Teste UI')); localStorage.setItem('rotulador.geradorAvancado', 'true'); localStorage.setItem('rotulador.modoGeradorV2', JSON.stringify('avancado')); localStorage.setItem('rotulador.receitaGerador', JSON.stringify({falsificadores: {prob: 0}}))")
    d.get(URL + "/gerador")
    checa(espera("document.querySelectorAll('#docs-lista li input').length === 4"), "documentos do treino listados (padrão)")
    n_tec = js("return document.querySelectorAll('#tec-familias .tec').length")
    checa(n_tec == 30, f"30 técnicas automáticas no catálogo ({n_tec})")
    checa(js("return document.querySelectorAll('#cat-lista .cat').length") == 15, "15 classes")

    # presets de técnicas: só caractere, depois tudo sem IA
    clicar("#tec-presets button", "Só caractere")
    ativas = js("return [...document.querySelectorAll('#tec-familias .tec.ativa .tec-nome')].map(e => e.textContent.trim())")
    checa(len(ativas) == 8 and all("aracter" in a or "dígito" in a.lower() or "Raspar" in a or "Acrescentar traços" in a
                                   for a in ativas), f"preset 'Só caractere' marca as 8 de caractere ({len(ativas)})")
    clicar("#tec-presets button", "Tudo (sem IA)")
    n_sem_ia = js("return document.querySelectorAll('#tec-familias .tec.ativa').length")
    checa(n_sem_ia == 27, f"preset 'Tudo (sem IA)' = 27 ({n_sem_ia})")
    # opções de uma técnica ("como")
    tec = [e for e in d.find_elements(By.CSS_SELECTOR, "#tec-familias .tec") if "copiando a letra" in e.text][0]
    botao = tec.find_element(By.CSS_SELECTOR, "button.mini")
    js("arguments[0].scrollIntoView({block:'center'})", botao)
    botao.click()
    sel = [s for s in tec.find_elements(By.CSS_SELECTOR, ".opcoes-tec select")
           if "tinta" in [o.get_attribute("value") for o in s.find_elements(By.TAG_NAME, "option")]]
    checa(len(sel) == 1, "opção 'Como colar' da técnica de copiar letra")
    Select(sel[0]).select_by_value("tinta")
    receita = json.loads(js("return localStorage.getItem('rotulador.receitaGerador')"))
    checa(receita["opcoes"].get("caractere.copiar_glifo", {}).get("colagem") == "tinta", "opção 'colagem: tinta' guardada na receita")
    # classes: só financeiro
    clicar("#cat-presets button", "Financeiro")
    checa(js("return document.querySelectorAll('#cat-lista .cat.ativa').length") == 4, "preset de classes 'Financeiro' = 4")
    clicar("#cat-presets button", "Todas")
    # documentos: só treino, 2 variações por documento, 2 a 3 edições
    for lab in d.find_elements(By.CSS_SELECTOR, "#docs-splits label"):
        cb = lab.find_element(By.TAG_NAME, "input")
        if ("Treino" in lab.text) != cb.is_selected():
            cb.click()
    clicar("#docs-todos")
    n_docs = js("return document.querySelectorAll('#docs-lista li.marcado').length")
    checa(n_docs == 4, f"4 documentos do treino selecionados ({n_docs})")
    for i, v in enumerate(("2", "2", "3")):  # o formulário é refeito a cada mudança: busca de novo
        e = d.find_elements(By.CSS_SELECTOR, "#qtd input[type=number]")[i]
        js("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('change'))", e, v)
    checa("8 imagens" in d.find_element(By.ID, "estimativa").text, f"estimativa de 8 imagens ({d.find_element(By.ID, 'estimativa').text})")
    # canal de envio, marcas benignas e disfarce (seção 4)
    def sel_canal():
        return [s for s in d.find_elements(By.CSS_SELECTOR, "#qtd select")
                if "misturado" in [o.get_attribute("value") for o in s.find_elements(By.TAG_NAME, "option")]]
    checa(len(sel_canal()) == 1 and sel_canal()[0].get_attribute("value") == "nenhum",
          "canal de envio: padrão nenhum (a imagem como está, só a edição muda)")
    checa("Marcas benignas" not in d.find_element(By.ID, "qtd").text, "sem canal, sem a linha de marcas")
    Select(sel_canal()[0]).select_by_value("misturado")
    checa("Marcas benignas" in d.find_element(By.ID, "qtd").text, "linha de marcas benignas aparece com canal")
    receita = json.loads(js("return localStorage.getItem('rotulador.receitaGerador')"))
    checa(receita.get("canal", {}).get("perfil") == "misturado" and receita.get("marcas", {}).get("prob") is not None
          and receita.get("acabamento", {}).get("disfarce") == "auto", f"receita guarda canal, marcas e disfarce ({receita.get('canal')})")

    # ------------------------------------------------------------------ prévia
    clicar("#btn-previa")
    checa(espera("!document.getElementById('tela-previa').hidden && document.querySelectorAll('#previa-lista .edicao').length >= 1", 240),
          "prévia mostra a imagem e as edições")
    n_ed = js("return document.querySelectorAll('#previa-lista .edicao').length")
    n_cx = js("return document.querySelectorAll('#previa-caixas .caixa').length")
    checa(n_ed == n_cx and n_ed >= 2, f"uma caixa por edição na prévia ({n_ed} edições, {n_cx} caixas)")
    checa(js("return [...document.querySelectorAll('#previa-lista .edicao .dica')].every(e => e.textContent.length > 20)"),
          "cada edição da prévia tem descrição")
    src = js("return document.getElementById('previa-img').src.length")
    clicar("#previa-modos button", "Máscara de técnicas")
    checa(js("return document.getElementById('previa-img').src.length") != src, "alternar para a máscara de técnicas")
    checa(js("return [...document.querySelectorAll('#previa-modos .so-canal')].every(b => !b.hidden)"),
          "botões da prévia pelo canal aparecem")
    clicar("#previa-modos button", "Editada pelo canal")
    checa(js("return document.querySelectorAll('#previa-caixas .caixa').length") == 0
          and js("return document.getElementById('previa-img').src.length") > 1000, "prévia pelo canal: imagem sem caixas")
    checa("Canal sorteado nesta prévia" in d.find_element(By.ID, "previa-lista").text, "prévia diz qual canal foi sorteado")
    clicar("#previa-modos button", "Editada")
    checa(js("return document.querySelectorAll('#previa-caixas .caixa').length") == n_ed, "de volta à editada: caixas de novo")
    checa(not list((PASTA / "saida" / "treino").glob("anotacoes/*.json")), "a prévia não salvou nada")

    # ------------------------------------------------------------------ geração
    clicar("#btn-gerar")
    checa(espera("!document.getElementById('painel-progresso').hidden", 30), "painel de progresso aparece")
    t0 = time.time()
    while time.time() - t0 < 600 and api("/api/gerador/status").get("rodando"):
        time.sleep(1)
    st = api("/api/gerador/status")
    checa(st["feitas"] == 8 and st["falhas"] == 0, f"8 imagens geradas sem falha ({st['feitas']} feitas, {st['falhas']} falhas)")
    anots = [json.loads(a.read_text(encoding="utf-8")) for a in (PASTA / "saida" / "treino").glob("anotacoes/*.json")]
    com_canal = [a for a in anots if a.get("canal")]
    checa(com_canal and all(a["arquivos"].get("mascara_ignorar") and "__v" in a["arquivos"]["original"] for a in com_canal),
          f"versões com canal: original própria e banda de ignorar ({len(com_canal)} de {len(anots)})")
    checa((PASTA / "saida" / "DATASHEET.md").is_file(), "datasheet refeito no fim do lote")
    checa(len(st["por_tecnica"]) >= 8, f"várias técnicas usadas na distribuição equilibrada ({len(st['por_tecnica'])})")
    checa(espera("document.getElementById('prog-titulo').textContent.includes('terminada')", 20), "progresso mostra 'terminada'")
    checa(espera("document.querySelectorAll('#prog-recentes .mini-versao img').length >= 4", 20), "miniaturas das últimas versões")
    anots = sorted((PASTA / "saida" / "treino" / "anotacoes").glob("*.json"))
    checa(len(anots) == 8 and not (PASTA / "saida" / "teste" / "anotacoes").exists(), "8 anotações, só no treino")
    a = json.loads(anots[0].read_text(encoding="utf-8"))
    checa(all(e.get("tecnica") and e.get("descricao") for e in a["edicoes"]), "anotação traz técnica e descrição de cada edição")

    # ------------------------------------------------------------------ catálogo
    clicar("#abas button", "Catálogo")
    checa(espera("document.querySelectorAll('#cat-galeria .mini-versao').length === 8", 60), "galeria com as 8 versões")
    checa("8" in d.find_element(By.ID, "cat-cartoes").text, "cartão com o total de versões")
    celulas = d.find_elements(By.CSS_SELECTOR, "#cat-matriz td.tem")
    checa(len(celulas) >= 6, f"matriz técnica × classe preenchida ({len(celulas)} células)")
    celulas[0].click()
    checa(espera("document.querySelectorAll('#cat-galeria .mini-versao').length >= 1 && "
                 "document.querySelectorAll('#cat-galeria .mini-versao').length < 8", 30), "clicar na célula filtra a galeria")
    clicar("#cat-filtros button", "limpar filtros")
    checa(espera("document.querySelectorAll('#cat-galeria .mini-versao').length === 8", 30), "limpar filtros volta as 8")
    d.find_elements(By.CSS_SELECTOR, "#cat-galeria .mini-versao")[0].click()
    checa(espera("document.getElementById('detalhe').open && document.getElementById('det-img').complete && "
                 "document.getElementById('det-img').naturalWidth > 100", 60), "detalhe abre com a imagem")
    n_det = js("return document.querySelectorAll('#det-lista .edicao').length")
    checa(n_det >= 2 and js("return document.querySelectorAll('#det-caixas .caixa').length") == n_det,
          f"detalhe lista as edições e marca as caixas ({n_det})")
    d.find_elements(By.CSS_SELECTOR, "#det-lista .edicao")[0].click()
    checa(espera("document.getElementById('det-img').src.includes('x0=')", 20), "clicar numa edição amplia o recorte")
    js("window.confirm = () => true")
    clicar("#det-excluir")
    checa(espera("document.querySelectorAll('#cat-galeria .mini-versao').length === 7", 60), "excluir versão: sobram 7")
    checa(len(list((PASTA / "saida" / "treino" / "anotacoes").glob("*.json"))) == 7, "anotação excluída do disco")

    # ------------------------------------------------------------------ editor: "como editar" num campo
    d.get(URL + "/")
    espera("window.rotulador && window.rotulador.estado", 30)
    js("localStorage.setItem('rotulador.tutorialVisto','true'); localStorage.setItem('rotulador.modoSimples','true')")
    d.get(URL + "/")
    checa(espera("document.querySelectorAll('#lista-campos li.campo').length >= 8", 120), "modo simples lê os campos")
    js("[...document.querySelectorAll('#lista-campos li.campo')].find(li => li.textContent.includes('R$')).click()")
    checa(espera("document.querySelector('#lista-campos li.ativo select.tec-campo')", 20), "seletor 'Como editar' no campo")
    sel = d.find_element(By.CSS_SELECTOR, "#lista-campos li.ativo select.tec-campo")
    Select(sel).select_by_value("caractere.redesenhar")
    checa(espera("window.rotulador.visor && window.rotulador.visor.previa", 30), "prévia da técnica no documento")
    clicar("#lista-campos li.ativo .linha-tecnica button", "Aplicar")
    checa(espera("(window.rotulador.sessao?.historico || []).some(h => h.tipo === 'tecnica')", 60), "técnica entrou no histórico")
    rot = js("return window.rotulador.sessao.historico.find(h => h.tipo === 'tecnica').rotulo")
    checa("caractere" in rot.lower(), f"histórico mostra o nome da técnica ({rot})")
    checa(espera("document.querySelector('#lista-campos li.feito .selo')?.textContent.includes('Trocar um caractere')", 20),
          "campo marcado com a técnica usada")
    # edição automática: exatamente os campos marcados (nome e endereço vêm marcados)
    checa(espera("window.rotulador.simples.marcados.size > 0", 20), "nome e endereço vêm marcados")
    clicar("#btn-fraude")
    checa(espera("[...document.querySelectorAll('#avisos .aviso')].some(a => a.textContent.startsWith('Feito:'))", 120),
          "✨ Editar os marcados conta o que fez")
    erros = erros_js()
    checa(not erros, f"sem erros de JS: {erros[:2]}")
finally:
    d.quit()

log = LOG.read_text(encoding="utf-8", errors="replace") if LOG.is_file() else ""
checa("Traceback" not in log, "sem traceback no servidor")
print("\nFALHAS:", len(falhas))
sys.exit(1 if falhas else 0)
