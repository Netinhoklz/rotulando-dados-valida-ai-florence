"""Exercita TODAS as ferramentas pela interface, como um usuário, num documento realista (300 dpi).

Uso (servidor já rodando com os dados de gerar_amostras.py --realista):
    python tests_ui/ui_todas.py E:/rotulador/real [--ia]

Para cada caso registra: efeito esperado, avisos de erro na tela, erros de JavaScript, tracebacks
no log do servidor (PASTA/servidor.log) e o tempo. --ia inclui a edição por IA (~2-3 min).
"""
import json
import shutil
import sys
import time
import traceback
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

sys.stdout.reconfigure(encoding="utf-8")
PASTA = Path(sys.argv[1])
COM_IA = "--ia" in sys.argv
URL = "http://127.0.0.1:5077"
LOG = PASTA / "servidor.log"
E = 2  # documento a 300 dpi: coordenadas do layout de 150 dpi x2


def api(rota, corpo=None):
    req = urllib.request.Request(URL + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


for sp in ("treino", "teste", "validacao"):
    for item in api(f"/api/documentos?split={sp}"):
        api("/api/descartar", {"id": item["id"]})
    shutil.rmtree(PASTA / "saida" / sp, ignore_errors=True)

op = webdriver.EdgeOptions()
op.add_argument("--headless=new")
op.add_argument("--window-size=1700,1050")
op.set_capability("ms:loggingPrefs", {"browser": "ALL"})
d = webdriver.Edge(options=op)
d.set_script_timeout(600)


# ------------------------------------------------------------------ apoio
def js(code, *a):
    return d.execute_script(code, *a)


def espera(cond_js, t=60):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            if js("return " + cond_js):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def ocioso(t=300):
    time.sleep(0.3)
    espera("document.getElementById('ocupado').hidden && !window.rotulador.ocupado && !window.rotulador.pendentes", t)
    time.sleep(0.2)


def tela(x, y):
    return js("const v=window.rotulador.visor, r=v.canvas.getBoundingClientRect();"
              "const s=v.imgParaTela(arguments[0],arguments[1]); return [r.left+s.x, r.top+s.y];", x * E, y * E)


def arrastar(a, b, passos=8, mods=()):
    ab = ActionBuilder(d)
    for m in mods:
        ab.key_action.key_down(m)
        ab.pointer_action.pause(0)
    x0, y0 = tela(*a)
    x1, y1 = tela(*b)
    ab.pointer_action.move_to_location(int(x0), int(y0))
    ab.pointer_action.pointer_down()
    for i in range(1, passos + 1):
        ab.pointer_action.move_to_location(int(x0 + (x1 - x0) * i / passos), int(y0 + (y1 - y0) * i / passos))
    ab.pointer_action.pointer_up()
    for m in mods:
        ab.key_action.pause(0)
        ab.key_action.key_up(m)
    ab.perform()


def clique(p, mods=()):
    arrastar(p, p, passos=1, mods=mods)


def tecla(*ks):
    js("document.activeElement && document.activeElement.blur(); document.getElementById('visor').focus()")
    ac = ActionChains(d)
    mods = [k for k in ks if k in (Keys.CONTROL, Keys.SHIFT, Keys.ALT)]
    for m in mods:
        ac.key_down(m)
    for k in ks:
        if k not in mods:
            ac.send_keys(k)
    for m in mods:
        ac.key_up(m)
    ac.perform()


def ferramenta(fid):
    d.find_element(By.CSS_SELECTOR, f'#ferramentas button[data-f="{fid}"]').click()
    time.sleep(0.3)


def opcao(chave, valor):
    """Muda um campo da barra de opções como o usuário (input/select/checkbox)."""
    el = d.find_element(By.ID, "op-" + chave)
    tipo = el.get_attribute("type")
    if el.tag_name == "select":
        Select(el).select_by_value(str(valor))
    elif tipo == "checkbox":
        if el.is_selected() != bool(valor):
            el.click()
    elif tipo == "range":
        js("arguments[0].value=arguments[1]; arguments[0].dispatchEvent(new Event('input'));", el, valor)
    else:
        el.clear()
        el.send_keys(str(valor))
        el.send_keys(Keys.TAB)
    time.sleep(0.3)


def categoria(v):
    Select(d.find_element(By.ID, "categoria")).select_by_value(v)
    time.sleep(0.4)


def sessao():
    return js("return window.rotulador.sessao")


def hist():
    return sessao()["historico"]


def ultimo():
    h = hist()
    return h[-1]["rotulo"] if h else None


def sel():
    return js("return window.rotulador.selInfo")


def botao_modal(texto):
    for b in d.find_elements(By.CSS_SELECTOR, "#modal-acoes button"):
        if b.text.strip() == texto:
            return b
    raise AssertionError(f"botão '{texto}' não está no diálogo")


def botao_opcoes(texto):
    for b in d.find_elements(By.CSS_SELECTOR, "#opcoes-ferramenta button"):
        if b.text.strip().startswith(texto):
            return b
    raise AssertionError(f"botão '{texto}' não está na barra de opções")


def acao_barra(titulo):
    d.find_element(By.CSS_SELECTOR, f'#ferramentas button[title^="{titulo}"]').click()
    time.sleep(0.5)


def selecionar(a, b):
    ferramenta("ret")
    opcao("modo", "novo")
    arrastar(a, b)
    ocioso()
    assert sel(), "a seleção não foi criada"


def desmarcar():
    tecla(Keys.CONTROL, "d")
    ocioso()


def esperar_previa(t=60):
    return espera("!!window.rotulador.visor.previa", t)


# ------------------------------------------------------------------ casos
resultados = []
pos_log = [LOG.stat().st_size if LOG.exists() else 0]


def caso(nome):
    def deco(fn):
        avisos_antes = set()
        js("document.querySelectorAll('.aviso').forEach(a => a.remove())")
        d.get_log("browser")
        t0 = time.time()
        erro = None
        try:
            fn()
        except Exception as e:
            erro = f"{type(e).__name__}: {e}".splitlines()[0][:200]
            traceback.print_exc(limit=1)
        dt = time.time() - t0
        avisos = [a.text for a in d.find_elements(By.CSS_SELECTOR, ".aviso.erro")]
        js_err = [x["message"][:160] for x in d.get_log("browser") if x["level"] == "SEVERE"]
        txt = LOG.read_text(encoding="utf-8", errors="replace")[pos_log[0]:] if LOG.exists() else ""
        pos_log[0] = LOG.stat().st_size if LOG.exists() else 0
        tb = [l for l in txt.splitlines() if "Traceback" in l or '" 500 ' in l]
        ok = erro is None and not tb and not [j for j in js_err if "400" not in j]
        resultados.append((nome, ok, round(dt, 1), erro, avisos, js_err, tb))
        print(("OK    " if ok else "FALHA ") + f"{nome:<44} {dt:6.1f}s" + (f"  ERRO: {erro}" if erro else "")
              + (f"  AVISOS: {avisos}" if avisos else "") + (f"  JS: {js_err[:2]}" if js_err else "")
              + (f"  SERVIDOR: {tb[:2]}" if tb else ""), flush=True)
        return fn
    return deco


d.get(URL)
# este teste usa o editor completo: entra direto no modo avançado, tutorial já visto
d.execute_script("localStorage.setItem('rotulador.modoSimples','false');localStorage.setItem('rotulador.tutorialVisto','true');localStorage.setItem('rotulador.operador', JSON.stringify('Teste UI'))")
d.get(URL)
espera("window.rotulador && window.rotulador.sessao && document.getElementById('vazio').hidden", 120)
d.find_element(By.CSS_SELECTOR, '[data-sp="treino"]').click()
ocioso()
d.find_elements(By.CSS_SELECTOR, "#lista-docs li")[0].click()
ocioso()
print("documento:", sessao()["nome"], sessao()["largura"], "x", sessao()["altura"])


@caso("zoom: clique, Alt+clique, 0 e 1")
def _():
    ferramenta("zoom")
    e0 = js("return window.rotulador.visor.escala")
    clique((600, 800))
    e1 = js("return window.rotulador.visor.escala")
    assert e1 > e0, "zoom não aproximou"
    clique((600, 800), mods=(Keys.ALT,))
    assert js("return window.rotulador.visor.escala") < e1, "Alt+clique não afastou"
    tecla("1")
    assert abs(js("return window.rotulador.visor.escala") - 1) < 1e-6, "1 não foi a 100%"
    tecla("0")


@caso("mão e Espaço+arrastar")
def _():
    ferramenta("mao")
    ox = js("return window.rotulador.visor.ox")
    arrastar((600, 800), (400, 700))
    assert js("return window.rotulador.visor.ox") != ox, "a mão não moveu a vista"
    tecla("0")


@caso("roda do mouse dá zoom")
def _():
    e0 = js("return window.rotulador.visor.escala")
    x, y = tela(600, 800)
    js("""const c=document.getElementById('tela');
          c.dispatchEvent(new WheelEvent('wheel',{deltaY:-300, clientX:arguments[0], clientY:arguments[1], bubbles:true, cancelable:true}));""", x, y)
    time.sleep(0.3)
    assert js("return window.rotulador.visor.escala") > e0, "a roda não deu zoom"
    tecla("0")


@caso("seleção retangular + somar + subtrair")
def _():
    selecionar((300, 200), (700, 260))
    p0 = sel()["pixels"]
    arrastar((300, 300), (700, 340), mods=(Keys.SHIFT,))
    ocioso()
    p1 = sel()["pixels"]
    assert p1 > p0, "Shift não somou"
    arrastar((400, 200), (500, 340), mods=(Keys.ALT,))
    ocioso()
    assert sel()["pixels"] < p1, "Alt não subtraiu"


@caso("seleção elíptica (Shift+M)")
def _():
    desmarcar()
    tecla(Keys.SHIFT, "m")
    assert js("return window.rotulador.idFerramenta") == "elipse", "Shift+M não escolheu a elipse"
    arrastar((300, 900), (600, 1100))
    ocioso()
    assert sel(), "elipse não selecionou"


@caso("laço livre")
def _():
    desmarcar()
    ferramenta("laco")
    opcao("poligonal", False)
    ab = ActionBuilder(d)
    pts = [(300, 900), (600, 920), (650, 1100), (320, 1150), (300, 900)]
    x, y = tela(*pts[0])
    ab.pointer_action.move_to_location(int(x), int(y))
    ab.pointer_action.pointer_down()
    for p in pts[1:]:
        x, y = tela(*p)
        ab.pointer_action.move_to_location(int(x), int(y))
    ab.pointer_action.pointer_up()
    ab.perform()
    ocioso()
    assert sel(), "laço livre não selecionou"


@caso("laço poligonal (cliques + duplo clique)")
def _():
    desmarcar()
    ferramenta("laco")
    opcao("poligonal", True)
    for p in [(300, 900), (600, 900), (600, 1100)]:
        clique(p)
        time.sleep(0.15)
    x, y = tela(300, 1100)
    ActionChains(d).move_by_offset(0, 0).perform()
    ab = ActionBuilder(d)
    ab.pointer_action.move_to_location(int(x), int(y))
    ab.pointer_action.double_click()
    ab.perform()
    ocioso()
    assert sel(), "laço poligonal não selecionou"
    opcao("poligonal", False)


@caso("varinha mágica (contígua e por cor)")
def _():
    desmarcar()
    ferramenta("varinha")
    opcao("contigua", True)
    clique((40, 20))  # faixa azul
    ocioso()
    s = sel()
    assert s and s["pixels"] > 500000, f"varinha pegou pouco: {s and s['pixels']}"
    opcao("contigua", False)
    clique((40, 20))
    ocioso()
    assert sel(), "varinha não-contígua falhou"
    opcao("contigua", True)


@caso("pincel de seleção (Q) e Alt subtrai")
def _():
    desmarcar()
    tecla("q")
    arrastar((300, 1000), (700, 1000))
    ocioso()
    p0 = sel()["pixels"]
    arrastar((450, 1000), (550, 1000), mods=(Keys.ALT,))
    ocioso()
    assert sel()["pixels"] < p0, "Alt no pincel de seleção não subtraiu"


@caso("só a tinta")
def _():
    selecionar((320, 205), (700, 245))
    p0 = sel()["pixels"]
    botao_opcoes("Só a tinta").click()
    ocioso()
    p1 = sel()["pixels"]
    assert 0 < p1 < p0 * 0.6, f"só a tinta: {p0} -> {p1}"


@caso("inverter, tudo, expandir, suavizar, desmarcar")
def _():
    selecionar((300, 900), (500, 1000))
    p0 = sel()["pixels"]
    botao_opcoes("Inverter").click()
    ocioso()
    assert sel()["pixels"] > p0 * 10, "inverter falhou"
    botao_opcoes("Tudo").click()
    ocioso()
    assert sel()["pixels"] == 2480 * 3508, "selecionar tudo falhou"
    selecionar((300, 900), (500, 1000))
    exp = d.find_elements(By.CSS_SELECTOR, "#opcoes-ferramenta input[type=number]")
    campos = {e.find_element(By.XPATH, "preceding-sibling::label").text: e for e in exp}
    e = campos["Expandir/contrair px"]
    e.clear(); e.send_keys("10"); e.send_keys(Keys.TAB)
    ocioso()
    assert sel()["pixels"] > p0, "expandir falhou"
    e = d.find_elements(By.CSS_SELECTOR, "#opcoes-ferramenta input[type=number]")
    campos = {x.find_element(By.XPATH, "preceding-sibling::label").text: x for x in e}
    s = campos["Suavizar px"]
    s.clear(); s.send_keys("4"); s.send_keys(Keys.TAB)
    ocioso()
    botao_opcoes("Desmarcar").click()
    ocioso()
    assert not sel(), "desmarcar falhou"


@caso("pincel (B) com categoria")
def _():
    categoria("valor")
    tecla("b")
    n = len(hist())
    arrastar((300, 1000), (700, 1000))
    ocioso()
    assert len(hist()) == n + 1 and ultimo() == "pincel", f"pincel não aplicou ({ultimo()})"


@caso("pincel: [ ] mudam o tamanho, Alt+clique pega cor")
def _():
    tecla("b")
    t0 = js("return window.rotulador.opcoes('pincel').tamanho")
    tecla("]")
    assert js("return window.rotulador.opcoes('pincel').tamanho") > t0, "] não aumentou"
    tecla("[")
    clique((40, 20), mods=(Keys.ALT,))
    ocioso()
    c = js("return window.rotulador.corFrente")
    assert c.lower() != "#000000" and int(c[5:7], 16) > int(c[1:3], 16), f"Alt+clique não pegou o azul ({c})"
    js("window.rotulador.definirCorFrente('#000000')")


@caso("borracha (E) apaga da camada")
def _():
    tecla("e")
    n = len(hist())
    arrastar((300, 1000), (700, 1000))
    ocioso()
    assert len(hist()) == n + 1 and ultimo() == "borracha", f"borracha não aplicou ({ultimo()})"


@caso("balde (G)")
def _():
    tecla("g")
    js("window.rotulador.definirCorFrente('#ffeeaa')")
    clique((900, 950))  # papel em branco: o ruído do papel exige tolerância
    ocioso()
    assert ultimo() == "balde", f"balde não aplicou ({ultimo()})"
    js("window.rotulador.definirCorFrente('#000000')")


@caso("conta-gotas (I)")
def _():
    tecla("i")
    clique((40, 20))
    ocioso()
    c = js("return window.rotulador.corFrente")
    assert int(c[5:7], 16) > 100, f"conta-gotas não leu o azul ({c})"
    js("window.rotulador.definirCorFrente('#000000')")


@caso("formas (U): retângulo, elipse, linha")
def _():
    tecla("u")
    opcao("tipo", "ret")
    arrastar((300, 1180), (500, 1230))
    ocioso()
    assert ultimo() == "forma", "retângulo não aplicou"
    opcao("tipo", "elipse")
    arrastar((550, 1180), (700, 1230))
    ocioso()
    assert hist()[-1]["params"]["tipo"] == "elipse", "elipse não aplicou"
    opcao("tipo", "linha")
    opcao("espessura", 3)
    arrastar((750, 1200), (1100, 1205), mods=(Keys.SHIFT,))
    ocioso()
    assert hist()[-1]["params"]["tipo"] == "linha", "linha não aplicou"
    opcao("tipo", "ret")
    opcao("espessura", 0)


@caso("carimbo (S): Alt+clique + traço")
def _():
    categoria("endereço")
    tecla("s")
    clique((340, 318), mods=(Keys.ALT,))
    time.sleep(0.3)
    arrastar((340, 1300), (650, 1300))
    ocioso()
    assert ultimo() == "carimbo", f"carimbo não aplicou ({ultimo()})"


@caso("carimbo: fonte original, escala 150, rotação, escurecer")
def _():
    tecla("s")
    opcao("fonte", "original")
    opcao("escala", 150)
    opcao("rotacao", 10)
    opcao("modo_mescla", "escurecer")
    clique((340, 370), mods=(Keys.ALT,))
    arrastar((340, 1350), (650, 1350))
    ocioso()
    assert ultimo() == "carimbo", f"carimbo com opções não aplicou ({ultimo()})"
    for k, v in (("fonte", "atual"), ("escala", 100), ("rotacao", 0), ("modo_mescla", "normal")):
        opcao(k, v)


@caso("pincel de recuperação (Shift+J)")
def _():
    tecla(Keys.SHIFT, "j")
    assert js("return window.rotulador.idFerramenta") == "recuperacao", "Shift+J não escolheu a recuperação"
    clique((900, 1000), mods=(Keys.ALT,))
    arrastar((340, 422), (500, 422))
    ocioso()
    assert ultimo() == "recuperacao", f"recuperação não aplicou ({ultimo()})"


@caso("pincel corretivo (J)")
def _():
    tecla("j")
    arrastar((340, 474), (600, 474))
    ocioso(600)
    assert ultimo() == "corretivo", f"corretivo não aplicou ({ultimo()})"


@caso("remendo (Y): origem")
def _():
    selecionar((325, 520), (520, 548))
    tecla("y")
    arrastar((420, 534), (420, 1000))
    ocioso(600)
    assert ultimo() == "remendo", f"remendo não aplicou ({ultimo()})"


@caso("remendo (Y): destino")
def _():
    selecionar((325, 570), (520, 600))
    tecla("y")
    opcao("modo", "destino")
    arrastar((420, 585), (420, 1250))
    ocioso(600)
    assert hist()[-1]["params"]["modo"] == "destino", f"remendo destino não aplicou ({ultimo()})"
    opcao("modo", "origem")


for _modo in ("desfocar", "nitidez", "borrar", "clarear", "escurecer", "saturar", "dessaturar"):
    @caso(f"retoque (O): {_modo}")
    def _(m=_modo):
        desmarcar()
        tecla("o")
        opcao("modo", m)
        y = 20 if m in ("saturar", "dessaturar") else 1500
        x0 = {"desfocar": 60, "nitidez": 220, "borrar": 380, "clarear": 540, "escurecer": 700,
              "saturar": 200, "dessaturar": 700}[m]
        n = len(hist())
        arrastar((x0, y), (x0 + 120, y))
        ocioso()
        assert len(hist()) == n + 1 and ultimo() == "retoque", f"retoque {m} não aplicou ({ultimo()})"


@caso("texto (T): clicar, digitar, Enter")
def _():
    categoria("nome")
    tecla("t")
    clique((800, 1150))
    campo = d.find_element(By.ID, "op-texto")
    campo.clear()
    campo.send_keys("TEXTO DE TESTE")
    assert esperar_previa(30), "texto sem prévia"
    campo.send_keys(Keys.ENTER)
    ocioso()
    assert ultimo() == "texto", f"texto não aplicou ({ultimo()})"


@caso("texto: opções (negrito, rotação, largura) e 🎲")
def _():
    tecla("t")
    opcao("negrito", 1)
    opcao("rotacao", 5)
    opcao("largura", 90)
    d.find_element(By.CSS_SELECTOR, '#opcoes-ferramenta button[title^="Valor aleatório"]').click()
    time.sleep(1)
    gerado = d.find_element(By.ID, "op-texto").get_attribute("value")
    assert gerado.strip(), "🎲 não gerou valor"
    clique((800, 1250))
    assert esperar_previa(30), "sem prévia"
    botao_opcoes("Aplicar").click()
    ocioso()
    assert ultimo() == "texto", f"texto com opções não aplicou ({ultimo()})"
    for k, v in (("negrito", 0), ("rotacao", 0), ("largura", 100)):
        opcao(k, v)


@caso("substituir texto (R)")
def _():
    categoria("valor")
    tecla("r")
    arrastar((790, 770), (1090, 835))
    ocioso()
    espera("window.rotulador.F.substituir.ancora !== null", 30)
    campo = d.find_element(By.ID, "op-texto")
    campo.clear()
    campo.send_keys("R$ 987,65")
    assert esperar_previa(120), "substituir sem prévia"
    campo.send_keys(Keys.ENTER)
    ocioso(600)
    assert ultimo() == "substituir_texto", f"substituir não aplicou ({ultimo()})"


@caso("mover (V) com seleção: duplicar")
def _():
    categoria("cep")
    selecionar((325, 412), (520, 450))
    tecla("v")
    opcao("modo", "duplicar")
    arrastar((420, 430), (420, 1300))
    assert esperar_previa(60), "transformar sem prévia"
    tecla(Keys.ENTER)
    ocioso()
    assert ultimo() == "transformar", f"duplicar não aplicou ({ultimo()})"


@caso("mover (V): mover + preencher, escala, perspectiva, girar")
def _():
    selecionar((325, 412), (520, 450))
    tecla("v")
    opcao("modo", "mover")
    opcao("origem", "preencher")
    arrastar((420, 430), (900, 1000))
    bb = sel()["bbox"]
    # canto inferior direito (já deslocado): escala
    arrastar(((bb[2] / E) + 480, (bb[3] / E) + 570), ((bb[2] / E) + 560, (bb[3] / E) + 600))
    # Ctrl no canto superior esquerdo: perspectiva
    arrastar(((bb[0] / E) + 480, (bb[1] / E) + 570), ((bb[0] / E) + 470, (bb[1] / E) + 550), mods=(Keys.CONTROL,))
    # fora do quadrilátero: girar
    arrastar((1150, 1100), (1150, 1150))
    assert esperar_previa(120), "sem prévia do mover"
    tecla(Keys.ENTER)
    ocioso(600)
    assert ultimo() == "transformar" and hist()[-1]["params"]["modo"] == "mover", f"mover não aplicou ({ultimo()})"
    opcao("modo", "duplicar")


@caso("mover (V) sem seleção move a camada")
def _():
    desmarcar()
    tecla("v")
    arrastar((420, 1300), (440, 1320))
    ocioso()
    assert hist()[-1]["tipo"] == "mover_camada", f"não moveu a camada ({ultimo()})"


@caso("colar de outro documento (⧉)")
def _():
    categoria("nome")
    ferramenta("colar")
    time.sleep(1.5)
    botoes = d.find_elements(By.CSS_SELECTOR, ".grade-doadores button")
    assert botoes, "não listou doadores"
    botoes[0].click()
    time.sleep(2.5)
    c = d.find_element(By.CSS_SELECTOR, ".doador-area canvas")
    ActionChains(d).move_to_element_with_offset(c, -150, -230).click_and_hold().move_by_offset(160, 30).release().perform()
    botao_modal("Usar trecho").click()
    assert esperar_previa(60), "colar sem prévia"
    tecla(Keys.ENTER)
    ocioso()
    assert ultimo() == "colar", f"colar não aplicou ({ultimo()})"


for _amostra in ("auto", "documento", "margem"):
    @caso(f"preencher por similaridade: {_amostra}")
    def _(a=_amostra):
        categoria("cidade/uf")
        selecionar((325, 465), (700, 500))
        tecla(Keys.SHIFT, Keys.F5)
        time.sleep(0.5)
        assert "similaridade" in d.find_element(By.ID, "modal-titulo").text, "diálogo não abriu"
        Select(d.find_elements(By.CSS_SELECTOR, "#modal-corpo select")[1]).select_by_value(a)
        assert esperar_previa(300), "preenchimento sem prévia"
        d.find_element(By.XPATH, "//button[contains(., 'Outra variação')]").click()
        time.sleep(0.5)
        botao_modal("Aplicar").click()
        ocioso(600)
        assert ultimo() == "preencher", f"preencher não aplicou ({ultimo()})"


_AJ = {"brilho_contraste", "niveis", "matiz_saturacao", "desfoque", "nitidez", "ruido", "jpeg", "cinza", "igualar_ruido"}
for _aj in sorted(_AJ):
    @caso(f"ajuste: {_aj}")
    def _(a=_aj):
        categoria("consumo/leitura")
        if a == "matiz_saturacao":
            selecionar((40, 10), (600, 140))
        elif a == "cinza":
            selecionar((700, 10), (1200, 140))  # outra parte da faixa: não estraga o caso da matiz
        elif a == "igualar_ruido":
            selecionar((310, 1190), (490, 1220))  # dentro do retângulo liso das formas: sem grão
        else:
            selecionar((325, 670), (600, 710))
        acao_barra("Ajustes")
        Select(d.find_elements(By.CSS_SELECTOR, "#modal-corpo select")[1]).select_by_value(a)
        time.sleep(0.4)
        if a in ("brilho_contraste", "niveis", "desfoque", "nitidez", "ruido", "jpeg", "matiz_saturacao"):
            r = d.find_elements(By.CSS_SELECTOR, "#modal-corpo input[type=range]")
            if r:
                js("arguments[0].value = arguments[0].max * 0.6; arguments[0].dispatchEvent(new Event('input'));", r[0])
        assert esperar_previa(60), "ajuste sem prévia: " + d.find_element(By.ID, "modal-erro").text
        botao_modal("Aplicar").click()
        ocioso()
        assert ultimo() == "ajuste" and hist()[-1]["params"]["ajuste"] == a, f"ajuste {a} não aplicou ({ultimo()})"


@caso("código de barras (🎲 + aplicar + copiar linha)")
def _():
    categoria("codigo barras")
    selecionar((60, 1555), (1180, 1665))
    acao_barra("Código de barras")
    d.find_element(By.XPATH, "//div[@id='modal-corpo']//button[@title='Gerar código válido']").click()
    time.sleep(1)
    assert esperar_previa(60), "código de barras sem prévia"
    d.find_element(By.XPATH, "//button[contains(., 'Copiar linha')]").click()
    time.sleep(0.3)
    botao_modal("Aplicar").click()
    ocioso()
    assert ultimo() == "codigo_barras", f"código não aplicou ({ultimo()})"


@caso("QR code (🎲 PIX)")
def _():
    categoria("qr code/pix")
    selecionar((900, 900), (1150, 1150))
    acao_barra("QR code")
    d.find_element(By.XPATH, "//button[contains(., 'PIX aleatório')]").click()
    assert esperar_previa(60), "QR sem prévia"
    botao_modal("Aplicar").click()
    ocioso()
    assert ultimo() == "qrcode", f"QR não aplicou ({ultimo()})"


if COM_IA:
    @caso("IA (Qwen) na seleção")
    def _():
        categoria("data")
        selecionar((325, 620), (560, 655))
        acao_barra("Editar a seleção com IA")
        espera("document.querySelector('#modal-corpo') && /online|OFFLINE/.test(document.querySelector('#modal-corpo').innerText)", 20)
        txt = d.find_element(By.CSS_SELECTOR, "#modal-corpo textarea")
        txt.clear()
        txt.send_keys('Troque o texto "10/10/2026" por "25/12/2026", mantendo a mesma fonte, tamanho, cor e fundo.')
        botao_modal("Editar com IA").click()
        ocioso(900)
        assert ultimo() == "ia", f"IA não aplicou ({ultimo()})"


@caso("camadas: nova, duplicar, mesclar, opacidade, ocultar, renomear, excluir")
def _():
    desmarcar()
    categoria("bairro")
    n = len(sessao()["camadas"])
    d.find_element(By.ID, "cam-nova").click()
    ocioso()
    assert len(sessao()["camadas"]) == n + 1, "nova camada falhou"
    tecla("b")
    arrastar((900, 1400), (1100, 1400))
    ocioso()
    d.find_element(By.ID, "cam-dup").click()
    ocioso()
    assert len(sessao()["camadas"]) == n + 2, "duplicar falhou"
    d.find_element(By.ID, "cam-mesclar").click()
    ocioso()
    assert len(sessao()["camadas"]) == n + 1, "mesclar falhou"
    js("const s=document.getElementById('cam-opac'); s.value=50; s.dispatchEvent(new Event('input')); s.dispatchEvent(new Event('change'));")
    ocioso()
    d.find_elements(By.CSS_SELECTOR, "#lista-camadas li .olho")[0].click()
    ocioso()
    d.find_elements(By.CSS_SELECTOR, "#lista-camadas li .olho")[0].click()
    ocioso()
    nome = d.find_elements(By.CSS_SELECTOR, "#lista-camadas li .nome-cam")[0]
    ActionChains(d).double_click(nome).perform()
    time.sleep(0.5)
    inp = d.find_element(By.CSS_SELECTOR, "#lista-camadas li .nome-cam input")
    inp.send_keys(Keys.CONTROL, "a")
    inp.send_keys("renomeada")
    inp.send_keys(Keys.ENTER)
    ocioso()
    assert sessao()["camadas"][0]["nome"] == "renomeada", "renomear falhou"
    d.find_element(By.ID, "cam-excluir").click()
    time.sleep(0.5)
    try:
        d.switch_to.alert.accept()
    except Exception:
        pass
    ocioso()
    assert len(sessao()["camadas"]) == n, "excluir falhou"


@caso("desfazer/refazer em sequência")
def _():
    n = len(hist())
    for _ in range(3):
        tecla(Keys.CONTROL, "z")
        ocioso()
    assert len(hist()) == n - 3, "Ctrl+Z x3 falhou"
    for _ in range(3):
        tecla(Keys.CONTROL, "y")
        ocioso()
    assert len(hist()) == n, "Ctrl+Y x3 falhou"


@caso("ver original (\\) e máscara (K)")
def _():
    js("document.getElementById('visor').focus()")
    ActionChains(d).key_down("\\").perform()
    espera("window.rotulador.visor.mostrarOriginal", 20)
    assert js("return window.rotulador.visor.mostrarOriginal && !!window.rotulador.visor.orig"), "original não apareceu"
    ActionChains(d).key_up("\\").perform()
    time.sleep(0.3)
    assert not js("return window.rotulador.visor.mostrarOriginal"), "original não sumiu ao soltar"
    tecla("k")
    espera("!!window.rotulador.visor.mascara", 30)
    assert js("return !!window.rotulador.visor.mascara"), "máscara não carregou"
    tecla("k")


for _fmt in ("jpeg", "webp", "tiff", "pdf", "png"):
    @caso(f"salvar em {_fmt}")
    def _(f=_fmt):
        if not sessao()["pode_salvar"]:
            categoria("valor")
            tecla("b")
            arrastar((900, 1420), (1100, 1420))
            ocioso()
        Select(d.find_element(By.ID, "formato")).select_by_value(f)
        op_ = d.find_element(By.ID, "operador")
        op_.clear()
        op_.send_keys("teste")
        d.find_element(By.ID, "btn-salvar").click()
        ocioso(300)
        assert sessao()["historico"] == [], "não salvou (histórico não zerou)"
        anot = sorted((PASTA / "saida" / "treino" / "anotacoes").glob("*.json"))
        a = json.loads(anot[-1].read_text(encoding="utf-8"))
        assert a["formato"] == f, f"formato salvo {a['formato']}"


@caso("documentos: PageDown, foto EXIF, PDF, outro split")
def _():
    tecla(Keys.PAGE_DOWN)
    ocioso()
    for li in d.find_elements(By.CSS_SELECTOR, "#lista-docs li"):
        if "conta_04" in li.text:
            li.click()
            break
    ocioso()
    s = sessao()
    assert (s["largura"], s["altura"]) == (2480, 3508), f"foto EXIF com tamanho {s['largura']}x{s['altura']}"
    d.find_element(By.CSS_SELECTOR, '[data-sp="teste"]').click()
    ocioso()
    for li in d.find_elements(By.CSS_SELECTOR, "#lista-docs li"):
        if "pdf" in li.text:
            li.click()
            break
    ocioso()
    assert "pdf" in sessao()["nome"], "PDF não abriu"


@caso("descartar com confirmação")
def _():
    categoria("valor")
    tecla("b")
    arrastar((300, 1000), (500, 1000))
    ocioso()
    d.find_element(By.ID, "btn-descartar").click()
    time.sleep(0.4)
    d.switch_to.alert.accept()
    ocioso()
    assert sessao()["historico"] == [], "descartar não limpou"


d.quit()
falhas = [r for r in resultados if not r[1]]
print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} casos OK")
lentos = sorted(resultados, key=lambda r: -r[2])[:6]
print("mais lentos:", [(r[0], r[2]) for r in lentos])
for r in falhas:
    print("FALHA:", r[0], "|", r[3], "|", r[4], "|", r[5][:2], "|", r[6][:2])
