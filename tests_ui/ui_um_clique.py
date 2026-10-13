"""Teste da tela "Gerar fraudes" de UM CLIQUE (topo do /gerador) no Edge headless, como uma pessoa usaria.

    python tests_ui/ui_um_clique.py PASTA_CAPTURAS [URL] [LOG_DO_SERVIDOR]

URL padrão http://127.0.0.1:5077 (como os outros testes de UI); LOG padrão PASTA/servidor.log (se existir).
Fluxo: o nome no "Quem é você?", chips dos grupos com contagem (marcados os que têm documentos sem versão), versões
por documento (−/+), "escolher documentos…", "O que mudar" (as três opções), a escolha lembrada depois de recarregar,
"Ver um exemplo" (ANTES | DEPOIS, caixas, lista antigo → novo, ampliar uma mudança, outro exemplo; nada é salvo),
gerar (barra, galeria ao vivo, resumo), a vista grande de uma versão, os atalhos Revisar e Ver no catálogo, as opções
avançadas recolhidas (e lembradas) e a tela de celular sem rolagem lateral. Falha com erro de JavaScript, erro do
servidor (500, traceback no log) ou qualquer passo que não aconteça. As versões geradas são excluídas no fim.
Capturas em PASTA/uc_*.png.

Motor sem o perfil "profissional" (antes de rotulador/gerador.py aceitar {"perfil": "profissional"}): a tela cai na
receita "mesma fonte"; o único 400 aceito no console é o da primeira chamada que descobre isso.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8")
PASTA = Path(sys.argv[1])
PASTA.mkdir(parents=True, exist_ok=True)
URL = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else "http://127.0.0.1:5077"
LOG = Path(sys.argv[3]) if len(sys.argv) > 3 else PASTA / "servidor.log"


def api(rota, corpo=None):
    req = urllib.request.Request(URL + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


def erro_api(rota, corpo):
    try:
        api(rota, corpo)
        return ""
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}").get("erro", str(e.code))


falhas = []
logs = []


def checa(cond, msg):
    print(("OK    " if cond else "FALHA ") + msg, flush=True)
    if not cond:
        falhas.append(msg)


assert not api("/api/gerador/status").get("rodando"), "há uma geração rodando no servidor de teste"
docs_api = api("/api/gerador/documentos")
# o motor já tem o perfil? (um motor sem ele recusa a receita antes de procurar o documento)
MOTOR_NOVO = "técnica" not in erro_api("/api/gerador/previa", {"receita": {"perfil": "profissional", "extras": True,
                                                                            "documentos": {"splits": ["treino"]}}, "doc": ""})
print(f"motor com o perfil profissional: {MOTOR_NOVO}")
# "nome e endereço, às vezes CPF, CEP, datas ou valores": as classes que o perfil pode tocar (sem ele, as da receita
# "mesma fonte" que a tela usa no lugar)
PERMITIDAS = (set(api("/api/gerador/receita_profissional?extras=1")["categorias"]) if MOTOR_NOVO
              else {"nome", "endereço", "cpf/cnpj", "cep", "data", "valor"})
log_antes = LOG.read_text(encoding="utf-8", errors="replace") if LOG.is_file() else ""
versoes_antes = api("/api/catalogo/resumo")["versoes"]

op = webdriver.EdgeOptions()
op.add_argument("--headless=new")
op.add_argument("--window-size=1500,1000")
op.set_capability("ms:loggingPrefs", {"browser": "ALL"})
d = webdriver.Edge(options=op)
geradas = []


def js(c, *a):
    return d.execute_script(c, *a)


def espera(cond, t=60):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    return False


def visivel(css):
    return any(e.is_displayed() for e in d.find_elements(By.CSS_SELECTOR, css))


def clica(css, texto=None):
    alvo = next((e for e in d.find_elements(By.CSS_SELECTOR, css) if e.is_displayed() and (texto is None or texto in e.text)), None)
    assert alvo is not None, f"não achei {css} {texto or ''}"
    js("arguments[0].scrollIntoView({block:'center'})", alvo)
    alvo.click()
    return alvo


def foto(nome):
    time.sleep(0.6)
    d.save_screenshot(str(PASTA / f"uc_{nome}.png"))


def texto(id_):
    return d.find_element(By.ID, id_).text


def n_gerar():
    m = re.search(r"Gerar (\d+) fraude", texto("uc-gerar"))
    return int(m.group(1)) if m else -1


def guardar_logs():
    logs.extend(l for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"])


def imagens_ok(css):
    return js("const im = [...document.querySelectorAll(arguments[0])]; return im.length > 0 && im.every(i => i.complete && i.naturalWidth > 0)", css)


try:
    # ------------------------------------------------------------------ abrir: o nome primeiro, depois a tela de um clique
    d.get(URL + "/gerador")
    js("localStorage.clear()")
    d.get(URL + "/gerador")
    checa(espera(lambda: visivel("dialog.dlg-operador")), "pede o nome antes (Quem é você?)")
    d.find_element(By.CSS_SELECTOR, "dialog.dlg-operador input").send_keys("Teste UI")
    clica("dialog.dlg-operador button", "Começar")
    checa(espera(lambda: n_gerar() > 0), f"botão grande com a conta ({texto('uc-gerar')})")
    checa(visivel("#um-clique") and not js("return document.getElementById('opcoes-avancadas').open"),
          "abre na tela de um clique; opções avançadas recolhidas")
    checa(not visivel("#bloco-exato") and not visivel("#btn-gerar"), "a receita completa fica escondida")
    chips = d.find_elements(By.CSS_SELECTOR, "#uc-splits .uc-chip")
    com_docs = [sp for sp in ("treino", "teste", "validacao") if docs_api.get(sp)]
    pendentes = [sp for sp in com_docs if any(not x["versoes"] for x in docs_api[sp])] or ["treino"]
    marcados = [sp for sp in ("treino", "teste", "validacao")
                if "on" in d.find_element(By.CSS_SELECTOR, f"#uc-splits .split-{sp}").get_attribute("class")]
    checa(len(chips) == 3 and marcados == [sp for sp in pendentes if sp in com_docs],
          f"chips por grupo, marcados os que têm documentos sem versão ({marcados})")
    for sp in com_docs:
        checa(str(len(docs_api[sp])) in d.find_element(By.CSS_SELECTOR, f"#uc-splits .split-{sp}").text, f"chip {sp} com a contagem")
    n_docs = sum(len(docs_api[sp]) for sp in marcados)
    checa(d.find_element(By.ID, "uc-variacoes").get_attribute("value") == "3", "3 versões por documento (padrão)")
    checa(n_gerar() == 3 * n_docs, f"Gerar {3 * n_docs} fraudes ({n_gerar()})")
    checa(js("return document.querySelector('input[name=uc-oque]:checked').value") == "extras",
          "padrão: nome e endereço, às vezes outros")
    checa("nada de retângulo ou rabisco" in texto("um-clique"), "linha fixa explica o estilo profissional")
    foto("01_abrir")

    # ------------------------------------------------------------------ versões, documentos e o que mudar
    clica("#uc-mais")
    checa(n_gerar() == 4 * n_docs, "+ soma uma versão")
    clica("#uc-menos")
    clica("#uc-menos")
    checa(d.find_element(By.ID, "uc-variacoes").get_attribute("value") == "2" and n_gerar() == 2 * n_docs, "− tira uma versão (2)")
    clica("#uc-escolher-docs")
    checa(espera(lambda: visivel("#uc-dlg-docs")), "escolher documentos… abre a lista")
    checa(len(d.find_elements(By.CSS_SELECTOR, "#uc-dlg-docs .lista-docs li input")) == n_docs, "a lista tem os documentos dos grupos marcados")
    clica("#uc-dlg-docs button", "nenhum")
    checa(espera(lambda: n_gerar() == 0) and d.find_element(By.ID, "uc-gerar").get_attribute("disabled") is not None,
          "nenhum documento: Gerar desliga")
    itens = [li for li in d.find_elements(By.CSS_SELECTOR, "#uc-dlg-docs .lista-docs li")
             if not li.find_element(By.CSS_SELECTOR, ".nome").text.lower().endswith(".pdf")][:2]  # imagens: mais rápido
    escolhidos = [li.find_element(By.CSS_SELECTOR, ".nome").text for li in itens]
    for li in itens:
        li.find_element(By.CSS_SELECTOR, "input").click()
    checa("2 de" in texto("uc-dlg-docs"), "a lista conta os marcados")
    clica("#uc-dlg-docs button", "Pronto")
    checa(espera(lambda: n_gerar() == 4), f"2 documentos × 2 versões = Gerar 4 fraudes ({texto('uc-gerar')})")
    clica("label.uc-opcao", "Escolher campos")
    checa(espera(lambda: visivel("#uc-campos")), "Escolher campos… mostra as classes com exemplo")
    marc = js("return [...document.querySelectorAll('#uc-campos input:checked')].map(i => i.dataset.campo).sort()")
    checa(marc == ["endereço", "nome"], f"nome e endereço marcados ({marc})")
    for k in marc:
        js("document.querySelector(`#uc-campos input[data-campo='${arguments[0]}']`).click()", k)
    checa(espera(lambda: d.find_element(By.ID, "uc-gerar").get_attribute("disabled") is not None and "campo" in texto("uc-falta")),
          f"sem campo: Gerar desliga e diz o que falta ({texto('uc-falta')})")
    js("document.querySelector(\"#uc-campos input[data-campo='nome']\").click()")
    checa(espera(lambda: d.find_element(By.ID, "uc-gerar").get_attribute("disabled") is None), "um campo marcado: Gerar volta")
    foto("02_escolher_campos")
    clica("label.uc-opcao", "Nome e endereço")
    checa(espera(lambda: not visivel("#uc-campos")), "volta ao padrão (a lista de campos some)")
    clica("#uc-detalhes summary")
    checa(espera(lambda: len(texto("uc-detalhes-corpo")) > 30 and "Consultando" not in texto("uc-detalhes-corpo")),
          "o que o gerador vai usar: aberto, diz as técnicas")

    # ------------------------------------------------------------------ a escolha é lembrada
    guardar_logs()
    d.refresh()
    checa(espera(lambda: n_gerar() == 4), f"recarregar: lembra 2 documentos × 2 versões ({texto('uc-gerar')})")
    checa(js("return document.querySelector('input[name=uc-oque]:checked').value") == "extras", "recarregar: lembra o que mudar")

    # ------------------------------------------------------------------ ver um exemplo (nada é salvo)
    def exemplo_pronto():
        return d.find_elements(By.CSS_SELECTOR, "#uc-exemplo .uc-mudanca") or d.find_elements(By.CSS_SELECTOR, "#uc-exemplo .uc-erro")

    clica("#uc-exemplo-btn")
    checa(espera(lambda: visivel("#uc-exemplo .uc-carregando")), "exemplo mostra que está trabalhando")
    for tentativa in range(3):
        espera(exemplo_pronto, 240)
        if d.find_elements(By.CSS_SELECTOR, "#uc-exemplo .uc-mudanca"):
            break
        print("      (sem mudança neste documento:", texto("uc-exemplo")[:120].replace("\n", " "), ")")
        clica("#uc-outro")
        time.sleep(1)
    mud = d.find_elements(By.CSS_SELECTOR, "#uc-exemplo .uc-mudanca")
    checa(len(mud) >= 1, f"exemplo com o que mudou ({len(mud)})")
    checa(espera(lambda: imagens_ok("#uc-exemplo .uc-lado img"), 30), "ANTES e DEPOIS carregados lado a lado")
    n_cx = len(d.find_elements(By.CSS_SELECTOR, "#uc-exemplo .uc-caixa"))
    checa(n_cx == 2 * len(mud), f"uma caixa por mudança em cada lado ({n_cx} caixas, {len(mud)} mudanças)")
    checa(all("→" in m.text or "apagado" in m.text for m in mud), "lista o que mudou: antigo → novo")
    checa(espera(lambda: imagens_ok("#uc-exemplo .uc-recorte img"), 30), "recortes antes e depois de cada mudança")
    foto("03_exemplo")
    largura = lambda: js("return [...document.querySelectorAll('#uc-exemplo .uc-lado img')].map(i => i.style.width)")  # noqa: E731
    clica("#uc-exemplo button", "página inteira")
    checa(espera(lambda: largura() == ["100%", "100%"]), "página inteira")
    mud[-1].click()  # com uma mudança só, a vista inicial já é a ampliada: por isso parte da página inteira
    checa(espera(lambda: largura()[0] != "100%" and largura()[0] == largura()[1])
          and "ativa" in mud[-1].get_attribute("class"), "clicar numa mudança amplia ANTES e DEPOIS juntos")
    foto("04_exemplo_ampliado")
    titulo = texto("uc-exemplo-titulo")
    clica("#uc-outro")
    checa(espera(lambda: exemplo_pronto() and not visivel("#uc-exemplo .uc-carregando"), 240), f"outro exemplo ({titulo} → {texto('uc-exemplo-titulo')})")
    checa(api("/api/catalogo/resumo")["versoes"] == versoes_antes, "o exemplo não salvou nada")

    # ------------------------------------------------------------------ gerar: progresso, galeria ao vivo, resumo
    clica("#uc-gerar")
    checa(espera(lambda: visivel("#uc-progresso"), 30), "painel de progresso aparece")
    checa(espera(lambda: d.find_element(By.ID, "uc-gerar").get_attribute("disabled") is not None, 10), "Gerar desliga enquanto roda")
    checa(espera(lambda: "falta" in texto("uc-prog-texto") or "calculando" in texto("uc-prog-texto")
                 or not api("/api/gerador/status")["rodando"], 60), f"progresso com tempo estimado ({texto('uc-prog-texto')})")
    if espera(lambda: d.find_elements(By.CSS_SELECTOR, "#uc-galeria .uc-mini") and api("/api/gerador/status")["rodando"], 300):
        checa(visivel("#uc-prog-acoes button.perigo"), "botão Parar durante a geração")
        foto("05_gerando")
    t0 = time.time()
    while time.time() - t0 < 900 and api("/api/gerador/status").get("rodando"):
        time.sleep(1)
    st = api("/api/gerador/status")
    geradas = [(v["split"], v["nome"]) for v in st.get("recentes", [])]
    checa(st["total"] == 4 and st["feitas"] + st["falhas"] == 4 and st["feitas"] >= 1,
          f"lote de 4 terminou ({st['feitas']} feitas, {st['falhas']} falhas)")
    checa(espera(lambda: texto("uc-prog-titulo") in ("Pronto!", "Geração parada"), 30), f"título final ({texto('uc-prog-titulo')})")
    checa(espera(lambda: len(d.find_elements(By.CSS_SELECTOR, "#uc-galeria .uc-mini")) == st["feitas"], 30),
          f"galeria com uma miniatura por versão ({len(d.find_elements(By.CSS_SELECTOR, '#uc-galeria .uc-mini'))})")
    checa(espera(lambda: imagens_ok("#uc-galeria .uc-mini img"), 60), "miniaturas antes e depois carregadas")
    resumo = texto("uc-prog-texto")
    checa(re.search(r"\d+ fraudes? em \d+ documentos?", resumo) is not None, f"resumo em linguagem simples ({resumo})")
    if st["falhas"]:
        checa(visivel("#uc-erros") and "falh" in texto("uc-erros-titulo"), "falhas explicadas (por quê?)")
    checa(visivel("#uc-prog-acoes button.primario") and "Revisar" in texto("uc-prog-acoes")
          and "catálogo" in texto("uc-prog-acoes"), "atalhos Revisar e Ver no catálogo")
    docs_lote = {v["documento"] for v in st["recentes"]}
    checa(docs_lote <= set(escolhidos), f"só os documentos escolhidos ({sorted(docs_lote)})")
    for v in st["recentes"]:
        cats = {e["categoria"] for e in v["edicoes"]}
        checa(all(e["nivel"] == "profissional" for e in v["edicoes"]), f"{v['nome']}: só edições profissionais")
        checa(cats <= PERMITIDAS, f"{v['nome']}: só nome, endereço e às vezes CPF, CEP, datas ou valores ({sorted(cats)})")
        if MOTOR_NOVO:
            checa(bool(cats & {"nome", "endereço"}), f"{v['nome']}: sempre nome e/ou endereço")
    foto("06_pronto")

    # ------------------------------------------------------------------ a vista grande de uma versão
    d.find_elements(By.CSS_SELECTOR, "#uc-galeria .uc-mini")[0].click()
    checa(espera(lambda: visivel("#uc-ver")), "clicar na miniatura abre grande")
    checa(espera(lambda: imagens_ok("#uc-ver .uc-lado img"), 60), "ANTES | DEPOIS da versão salva")
    mv = d.find_elements(By.CSS_SELECTOR, "#uc-ver .uc-mudanca")
    checa(len(mv) >= 1 and len(d.find_elements(By.CSS_SELECTOR, "#uc-ver .uc-caixa")) == 2 * len(mv), "caixas do que mudou")
    checa(espera(lambda: imagens_ok("#uc-ver .uc-recorte img"), 60), "recortes de cada mudança")
    foto("07_versao_grande")
    mv[0].click()
    checa(espera(lambda: "ativa" in mv[0].get_attribute("class")), "ampliar uma mudança na versão salva")
    foto("08_versao_ampliada")
    clica("#uc-ver button", "Aprovar, reprovar")
    checa(espera(lambda: visivel("#detalhe") and not visivel("#uc-ver")), "leva ao detalhe completo (revisão)")
    clica("#detalhe button", "Fechar")

    # ------------------------------------------------------------------ atalhos
    clica("#uc-prog-acoes button", "catálogo")
    checa(espera(lambda: visivel("#aba-catalogo") and len(d.find_elements(By.CSS_SELECTOR, "#cat-galeria .mini-versao")) >= st["feitas"]),
          "Ver no catálogo abre o catálogo com as versões")
    clica("#abas button", "Gerar")
    clica("#uc-prog-acoes button", "Revisar")
    checa(espera(lambda: visivel("#aba-revisar")), "Revisar abre a revisão")
    clica("#abas button", "Gerar")

    # ------------------------------------------------------------------ opções avançadas: tudo continua lá, e lembra
    clica("#opcoes-avancadas > summary")
    checa(espera(lambda: visivel("#modo-receita") and visivel("#btn-gerar") and visivel("#bloco-docs")),
          "opções avançadas mostram a receita completa (modos, documentos, Gerar)")
    foto("09_avancadas")
    guardar_logs()
    d.refresh()
    checa(espera(lambda: js("return document.getElementById('opcoes-avancadas').open") is True), "lembra as opções avançadas abertas")
    clica("#opcoes-avancadas > summary")
    checa(espera(lambda: js("return localStorage.getItem('rotulador.geradorAvancado')") == "false"), "e fechadas")

    # ------------------------------------------------------------------ celular
    d.set_window_size(390, 844)
    guardar_logs()
    d.refresh()
    checa(espera(lambda: n_gerar() == 4), "celular: carrega")
    checa(js("return document.documentElement.scrollWidth <= window.innerWidth + 2"), "celular: sem rolagem lateral")
    js("document.getElementById('uc-gerar').scrollIntoView({block:'center'})")
    checa(d.find_element(By.ID, "uc-gerar").is_displayed(), "celular: botão Gerar alcançável")
    foto("10_celular")
    guardar_logs()
finally:
    try:
        d.quit()
    finally:
        for sp, nome in geradas:  # deixa os dados como estavam
            try:
                api("/api/catalogo/excluir", {"split": sp, "nome": nome})
            except Exception as e:  # noqa: BLE001
                print("não excluí", nome, e)

aceitos = [r"receita_profissional.*status of 404"] + ([] if MOTOR_NOVO else [r"/api/gerador/(previa|iniciar) .*status of 400"])
ruins = [l["message"] for l in logs if not any(re.search(a, l["message"]) for a in aceitos)]
transicao = [l["message"] for l in logs if l["message"] not in ruins]
if transicao:
    print(f"      (aceitos, motor sem o perfil ou rota opcional: {len(transicao)})")
checa(not ruins, f"sem erros de JavaScript ou do servidor no console: {ruins[:3]}")
novo_log = (LOG.read_text(encoding="utf-8", errors="replace") if LOG.is_file() else "")[len(log_antes):]
checa("Traceback" not in novo_log, "sem traceback no servidor")
print("\nTUDO OK" if not falhas else f"\nFALHAS: {len(falhas)}")
sys.exit(1 if falhas else 0)
