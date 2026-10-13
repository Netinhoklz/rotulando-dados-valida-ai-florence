"""Teste do trabalho em EQUIPE no Edge headless (dois navegadores = duas pessoas), contra o servidor em 5077
com os documentos realistas (precisa de versões salvas: rode antes ui_tipos.py ou ui_gerador.py).

    python tests_ui/ui_equipe.py E:/rotulador/real

Editor: nome obrigatório, trava (Bia só olha o documento da Ana, assume, e a Ana fica sabendo), lista
com busca e "só os meus", atalhos (?), sugestão de categoria pelo campo e "Qual é a fonte?".
Gerador: Revisar (A, R com motivo), catálogo (marcar, aprovar em lote, exportar filtrados), Metas
(guardar, gerar o que falta) e Equipe (atribuir). Capturas em PASTA/ui_equipe_*.png.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

sys.stdout.reconfigure(encoding="utf-8")
PASTA = Path(sys.argv[1])
URL = "http://127.0.0.1:5077"
falhas = []


def api(rota, corpo=None, quem="teste"):
    req = urllib.request.Request(URL + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json", "X-Operador": urllib.parse.quote(quem)})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


def navegador():
    op = webdriver.EdgeOptions()
    op.add_argument("--headless=new")
    op.add_argument("--window-size=1600,1000")
    op.set_capability("ms:loggingPrefs", {"browser": "ALL"})
    return webdriver.Edge(options=op)


def checa(cond, msg):
    print(("OK    " if cond else "FALHA ") + msg, flush=True)
    if not cond:
        falhas.append(msg)


def espera(cond, t=60):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def clica(d, css, texto=None):
    alvo = next((e for e in d.find_elements(By.CSS_SELECTOR, css) if texto is None or texto in e.text), None)
    assert alvo is not None, f"não achei {css} {texto or ''}"
    d.execute_script("arguments[0].scrollIntoView({block:'center'})", alvo)
    alvo.click()
    return alvo


def avisos(d):
    return " | ".join(e.text for e in d.find_elements(By.CSS_SELECTOR, "#avisos .aviso"))


ana, bia = navegador(), navegador()
try:
    doc = api("/api/documentos?split=treino")[0]
    for x in api("/api/documentos?split=treino"):  # travas que outros testes deixaram (navegador fechado não solta)
        api("/api/abrir", {"id": x["id"], "forcar": True}, quem="Ana")
        api("/api/trava/soltar", {"id": x["id"], "operador": "Ana"})
    api("/api/descartar", {"id": doc["id"]}, quem="Ana")

    # ---------------------------------------------------------------- nome obrigatório
    ana.get(URL)
    ana.execute_script("localStorage.clear(); localStorage.setItem('rotulador.modoSimples','false');"
                       f"localStorage.setItem('rotulador.tutorialVisto','true'); localStorage.setItem('rotulador.ultimo.treino', JSON.stringify('{doc['id']}'))")
    ana.get(URL)
    checa(espera(lambda: ana.find_element(By.CSS_SELECTOR, "dialog.dlg-operador").is_displayed()), "sem nome: pede o nome")
    campo = ana.find_element(By.CSS_SELECTOR, "dialog.dlg-operador input")
    campo.send_keys(Keys.ENTER)
    checa(ana.find_element(By.CSS_SELECTOR, "dialog.dlg-operador").is_displayed(), "nome vazio não passa")
    campo.send_keys("Ana", Keys.ENTER)
    checa(espera(lambda: not ana.find_elements(By.CSS_SELECTOR, "dialog.dlg-operador")), "com nome, o diálogo some")
    checa(espera(lambda: ana.find_element(By.ID, "operador").get_attribute("value") == "Ana"), "nome no campo Operador")
    checa(espera(lambda: ana.execute_script("return rotulador.sessao && rotulador.sessao.id") == doc["id"]), "Ana abriu o documento")

    # ---------------------------------------------------------------- trava
    bia.get(URL)
    bia.execute_script("localStorage.clear(); localStorage.setItem('rotulador.modoSimples','false');"
                       "localStorage.setItem('rotulador.tutorialVisto','true'); localStorage.setItem('rotulador.operador', JSON.stringify('Bia'));"
                       f"localStorage.setItem('rotulador.ultimo.treino', JSON.stringify('{doc['id']}'))")
    bia.get(URL)
    checa(espera(lambda: bia.find_element(By.ID, "banner-trava").is_displayed()), "Bia vê que o documento está com a Ana")
    checa("Ana" in bia.find_element(By.ID, "banner-trava-txt").text, "o aviso diz quem: Ana")
    checa(espera(lambda: "🔒" in bia.find_element(By.CSS_SELECTOR, "#lista-docs li.ativo").text), "lista mostra o cadeado")
    bia.execute_script("rotulador.desfazer()")
    checa(espera(lambda: "Assumir edição" in avisos(bia)), "Bia não edita sem assumir")
    bia.save_screenshot(str(PASTA / "ui_equipe_01_trava.png"))
    bia.execute_script("window.confirm = () => true")
    clica(bia, "#btn-assumir")
    checa(espera(lambda: not bia.find_element(By.ID, "banner-trava").is_displayed()), "Bia assume o documento")
    ana.execute_script("rotulador.desfazer()")  # a Ana tenta mexer: o servidor recusa (409) e a página mostra quem está
    checa(espera(lambda: ana.find_element(By.ID, "banner-trava").is_displayed() and "Bia" in ana.find_element(By.ID, "banner-trava-txt").text),
          "Ana fica sabendo que a Bia assumiu")

    # ---------------------------------------------------------------- lista, atalhos
    ana.find_element(By.ID, "busca-docs").send_keys(doc["nome"][:7])
    checa(espera(lambda: all(doc["nome"][:7].lower() in li.text.lower() for li in ana.find_elements(By.CSS_SELECTOR, "#lista-docs li"))),
          "busca filtra a lista")
    ana.find_element(By.TAG_NAME, "body").send_keys("?")
    checa(espera(lambda: ana.find_element(By.ID, "modal-titulo").text == "Atalhos de teclado"), "? abre os atalhos")
    checa("Ctrl+Enter" in ana.find_element(By.ID, "modal-corpo").text, "atalhos incluem o modo simples")
    ana.execute_script("document.getElementById('modal').close()")

    # ---------------------------------------------------------------- sugestão de categoria e fonte (na Bia, que tem o documento)
    checa(espera(lambda: bia.execute_script("return (rotulador.campos || []).length > 3 && !rotulador.ocupado"), 120),
          "documento reaberto e campos lidos (OCR)")
    c = bia.execute_script("return rotulador.campos.find(c => c.tipo === 'texto' && c.categoria && c.texto.length >= 6)")
    x0, y0, x1, y1 = c["caixa"]
    estado = bia.execute_script(
        "rotulador.usar('pincel'); rotulador.categoria = ''; document.getElementById('categoria').value = '';"
        "rotulador.desmarcar(); rotulador.ultimoPonto = {x: arguments[0], y: arguments[1]};"
        "const e = {ocupado: rotulador.ocupado, so: rotulador.somenteLeitura, sel: rotulador.selBbox(), campos: rotulador.campos.length};"
        "rotulador.aplicar('pincel', {pontos: [[arguments[0], arguments[1]]], tamanho: 4, cor: '#000000'}); return e;",
        (x0 + x1) / 2, (y0 + y1) / 2)
    checa(espera(lambda: bia.find_element(By.ID, "categoria").get_attribute("value") == c["categoria"]),
          f"sugere a categoria do campo ({c['categoria']}); estado: {estado}; avisos: {avisos(bia)}")
    checa("Sugestão" in avisos(bia), "e pede para conferir")
    bia.execute_script("rotulador.usar('substituir');"
                       "rotulador.selecao = {formas: [{tipo: 'ret', x0: arguments[0], y0: arguments[1], x1: arguments[2], y1: arguments[3], modo: 'novo'}],"
                       " inverter: false, expandir: 0, suavizar: 0}; return rotulador.atualizarSelecao()", x0 - 3, y0 - 3, x1 + 3, y1 + 3)
    time.sleep(1.5)  # a seleção nova re-estima o texto e redesenha a barra de opções
    bia.execute_script("[...document.querySelectorAll('#opcoes button')].find(b => b.textContent.includes('Qual é a fonte')).click()")
    checa(espera(lambda: bia.find_element(By.ID, "modal-titulo").text == "Qual é a fonte?", 120), "identificador de fonte abre")
    cands = bia.find_elements(By.CSS_SELECTOR, ".fonte-cand")
    checa(len(cands) >= 3, f"várias fontes candidatas: {len(cands)}")
    nome = cands[0].find_element(By.TAG_NAME, "b").text.split(". ", 1)[1]
    checa(bia.execute_script("return [...document.querySelectorAll('.fonte-cand img')].every(i => i.naturalWidth > 0)"), "cada fonte com o texto desenhado")
    bia.save_screenshot(str(PASTA / "ui_equipe_02_fonte.png"))
    clica(bia, ".fonte-cand.melhor button")
    checa(espera(lambda: bia.execute_script("return rotulador.opcoes('substituir').fonte") == nome), f"'Usar esta' põe a fonte nas opções ({nome})")

    # ---------------------------------------------------------------- gerador: revisar
    ana.get(URL + "/gerador?aba=revisar")
    cont0 = api("/api/revisao/fila")["contagem"]
    checa(espera(lambda: ana.find_element(By.ID, "rev-img").get_attribute("naturalWidth") not in (None, "0")), "fila de revisão mostra a versão")
    pend = cont0.get("pendente", 0)
    if pend >= 2:
        ana.find_element(By.TAG_NAME, "body").send_keys("a")
        checa(espera(lambda: api("/api/revisao/fila")["contagem"].get("aprovada", 0) == cont0.get("aprovada", 0) + 1), "A aprova")
        ana.find_element(By.TAG_NAME, "body").send_keys("r")
        checa(espera(lambda: "motivo" in avisos(ana)), "R sem motivo pede o motivo")
        ana.find_element(By.TAG_NAME, "body").send_keys("1")
        ana.find_element(By.TAG_NAME, "body").send_keys("r")
        checa(espera(lambda: api("/api/revisao/fila")["contagem"].get("reprovada", 0) == cont0.get("reprovada", 0) + 1), "1 + R reprova com motivo")
        rep = api("/api/revisao/fila?revisao=reprovada")["itens"]
        checa(any(v["revisor"] == "Ana" for v in rep), "revisor é a Ana")
    else:
        checa(False, f"poucas versões pendentes para testar a revisão ({pend})")
    ana.save_screenshot(str(PASTA / "ui_equipe_03_revisar.png"))

    # ---------------------------------------------------------------- catálogo: lote e exportação
    clica(ana, "#abas button", "Catálogo")
    checa(espera(lambda: len(ana.find_elements(By.CSS_SELECTOR, ".cartao-versao .marcar-versao")) >= 2), "galeria com caixas de marcar")
    marcas = ana.find_elements(By.CSS_SELECTOR, ".cartao-versao .marcar-versao")[:2]
    for m in marcas:
        ana.execute_script("arguments[0].click()", m)
    checa("2 marcadas" in ana.find_element(By.ID, "cat-lote").text, "barra mostra 2 marcadas")
    clica(ana, "#cat-lote button", "aprovar")
    checa(espera(lambda: "aprovada" in avisos(ana)), "aprovar em lote")
    ana.execute_script("window.prompt = () => 'ui-equipe-aprovadas'")
    sel = next(s for s in ana.find_elements(By.CSS_SELECTOR, "#cat-filtros select") if "revisão" in s.text)
    ana.execute_script("arguments[0].value = 'aprovada'; arguments[0].dispatchEvent(new Event('change'))", sel)
    clica(ana, "#cat-filtros button", "exportar filtrados")
    checa(espera(lambda: "manifesto.csv" in ana.find_element(By.ID, "cat-exportacao").text), "exportação com link do manifesto")
    man = json.loads(urllib.request.urlopen(URL + "/api/catalogo/exportacao/ui-equipe-aprovadas/manifesto.json").read())
    checa(man["versoes"] >= 2 and man["filtros"].get("revisao") == "aprovada", f"manifesto só com aprovadas ({man['versoes']})")
    ana.save_screenshot(str(PASTA / "ui_equipe_04_catalogo.png"))

    # ---------------------------------------------------------------- metas
    clica(ana, "#abas button", "Metas")
    checa(espera(lambda: ana.find_elements(By.CSS_SELECTOR, ".tabela-metas tbody tr")), "tabela de metas")
    ana.find_element(By.CSS_SELECTOR, ".metas input[aria-label='mesma meta para todas']").send_keys("5")
    clica(ana, ".metas button", "aplicar")
    clica(ana, ".metas button", "Guardar metas")
    checa(espera(lambda: api("/api/metas")["metas"]["classes"]["treino"].get("valor") == 5), "metas guardadas no servidor")
    ana.save_screenshot(str(PASTA / "ui_equipe_05_metas.png"))
    botoes = [b for b in ana.find_elements(By.CSS_SELECTOR, ".tabela-metas tfoot button") if "falta" in b.text]
    if botoes:
        botoes[0].click()
        checa(espera(lambda: not ana.find_element(By.ID, "aba-gerar").get_attribute("hidden")
                     and ana.find_element(By.ID, "bloco-tec").is_displayed()), "gerar o que falta abre a receita no avançado")
        rec = ana.execute_script("return JSON.parse(localStorage.getItem('rotulador.receitaGerador'))")
        checa(rec["distribuicao"] == "pesos" and rec["categorias"] and rec["tipos_desligados"], "receita com as classes que faltam")
    else:
        checa(False, "sem botão de gerar o que falta")
    api("/api/metas", {"classes": {}, "so_aprovadas": False})

    # ---------------------------------------------------------------- equipe
    clica(ana, "#abas button", "Equipe")
    checa(espera(lambda: ana.find_elements(By.CSS_SELECTOR, ".tabela-docs tbody tr")), "tabela de documentos")
    checa("Bia" in ana.find_element(By.CSS_SELECTOR, ".equipe .vivas").text, "mostra que a Bia está editando")
    linha = next(tr for tr in ana.find_elements(By.CSS_SELECTOR, ".tabela-docs tbody tr") if doc["nome"] in tr.text)
    ana.execute_script("arguments[0].click()", linha.find_element(By.CSS_SELECTOR, "input[type=checkbox]"))
    ana.find_element(By.CSS_SELECTOR, ".barra-atribuir input").send_keys("Bia")
    clica(ana, ".barra-atribuir button", "Atribuir")
    checa(espera(lambda: any(x["id"] == doc["id"] and x["responsavel"] == "Bia" for x in api("/api/equipe")["documentos"])), "atribuído à Bia")
    ana.save_screenshot(str(PASTA / "ui_equipe_06_equipe.png"))
    bia.execute_script("return fetch('/api/documentos?split=treino').then(r => r.json()).then(l => { rotulador.docs = l; })")
    bia.execute_script("document.getElementById('so-meus').click()")
    checa(espera(lambda: "★" in bia.find_element(By.ID, "lista-docs").text and len(bia.find_elements(By.CSS_SELECTOR, "#lista-docs li")) >= 1),
          "Bia vê o documento em 'só os meus' com ★")
    api("/api/equipe/atribuir", {"ids": [doc["id"]], "operador": ""})

    for nome_, d in (("Ana", ana), ("Bia", bia)):
        erros = [l for l in d.get_log("browser") if l["level"] == "SEVERE" and "409" not in l["message"]]
        checa(not erros, f"sem erros no console ({nome_}): {erros[:2]}")
finally:
    ana.quit()
    bia.quit()
    for quem_ in ("Ana", "Bia"):  # não deixa travas para os próximos testes
        for x in api("/api/documentos?split=treino"):
            api("/api/trava/soltar", {"id": x["id"], "operador": quem_})

print("\n" + ("TUDO OK" if not falhas else f"{len(falhas)} FALHA(S): {falhas}"))
sys.exit(1 if falhas else 0)
