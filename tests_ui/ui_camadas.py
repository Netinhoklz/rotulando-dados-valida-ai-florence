"""Teste de UI das camadas e ferramentas novas (Edge headless) contra o servidor em 5077."""
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

sys.stdout.reconfigure(encoding="utf-8")
S = Path(sys.argv[1])
SAIDA = S / "saida_teste"
URL = "http://127.0.0.1:5077"


def api(rota, corpo=None):
    req = urllib.request.Request(URL + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


for sp in ("treino", "teste", "validacao"):
    for item in api(f"/api/documentos?split={sp}"):
        api("/api/descartar", {"id": item["id"]})
    shutil.rmtree(SAIDA / sp, ignore_errors=True)

op = webdriver.EdgeOptions()
op.add_argument("--headless=new")
op.add_argument("--window-size=1700,1050")
op.set_capability("ms:loggingPrefs", {"browser": "ALL"})
d = webdriver.Edge(options=op)
falhas = []


def checa(cond, msg):
    print(("OK    " if cond else "FALHA ") + msg)
    if not cond:
        falhas.append(msg)


def espera(js, t=60):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            if d.execute_script("return " + js):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def ocioso():
    time.sleep(0.25)
    espera("document.getElementById('ocupado').hidden && !window.rotulador.ocupado")
    time.sleep(0.15)


def tela(x, y):
    return d.execute_script(
        "const v=window.rotulador.visor, r=v.canvas.getBoundingClientRect();"
        "const s=v.imgParaTela(arguments[0],arguments[1]); return [r.left+s.x, r.top+s.y];", x, y)


def arrastar(a, b, passos=8, alt=False):
    ab = ActionBuilder(d)
    x0, y0 = tela(*a)
    x1, y1 = tela(*b)
    if alt:
        ab.key_action.key_down(Keys.ALT)
        ab.pointer_action.pause(0)
    ab.pointer_action.move_to_location(int(x0), int(y0))
    ab.pointer_action.pointer_down()
    for i in range(1, passos + 1):
        ab.pointer_action.move_to_location(int(x0 + (x1 - x0) * i / passos), int(y0 + (y1 - y0) * i / passos))
    ab.pointer_action.pointer_up()
    if alt:
        ab.key_action.pause(0)
        ab.key_action.key_up(Keys.ALT)
    ab.perform()


def tecla(*ks):
    d.execute_script("document.activeElement && document.activeElement.blur(); document.getElementById('visor').focus()")
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


def sessao():
    return d.execute_script("return window.rotulador.sessao")


def camadas():
    return sessao()["camadas"]


def categoria(v):
    Select(d.find_element(By.ID, "categoria")).select_by_value(v)
    time.sleep(0.3)


def shot(n):
    d.save_screenshot(str(S / f"cam_{n}.png"))


d.get(URL)
espera("window.rotulador && window.rotulador.sessao && document.getElementById('vazio').hidden")
d.find_element(By.CSS_SELECTOR, '[data-sp="treino"]').click()
ocioso()
d.find_elements(By.CSS_SELECTOR, "#lista-docs li")[0].click()
ocioso()
checa(camadas() == [], "documento começa sem camadas")
checa("cada categoria ganha" in d.find_element(By.ID, "cam-dica").text, "dica de camadas vazia explica o fluxo")

# 1) categoria -> camada automática
categoria("valor")
tecla("b")
arrastar((300, 1100), (600, 1100))
ocioso()
checa([c["categoria"] for c in camadas()] == ["valor"], "1ª edição criou a camada 'valor'")
categoria("nome")
arrastar((300, 1130), (600, 1130))
ocioso()
checa([c["categoria"] for c in camadas()] == ["nome", "valor"], "2ª categoria criou camada própria, no topo")
checa(len(d.find_elements(By.CSS_SELECTOR, "#lista-camadas li")) == 3, "painel: 2 camadas + fundo")
checa(len(d.find_elements(By.CSS_SELECTOR, "#lista-camadas li .mini img")) == 2, "miniaturas das camadas")
categoria("valor")
checa(camadas()[1]["ativa"], "escolher a categoria 'valor' ativa a camada dela")
shot("01_duas_camadas")

# 2) ocultar / mostrar
olho = d.find_elements(By.CSS_SELECTOR, "#lista-camadas li .olho")[0]
olho.click()
ocioso()
checa(not camadas()[0]["visivel"] and "nome" not in sessao()["categorias"], "ocultar tira a categoria dos rótulos")
checa("NÃO entram" in d.find_element(By.ID, "cam-dica").text, "aviso de camada oculta")
d.find_elements(By.CSS_SELECTOR, "#lista-camadas li .olho")[0].click()
ocioso()
checa(camadas()[0]["visivel"], "mostrar de novo")

# 3) renomear (duplo clique)
nome = d.find_elements(By.CSS_SELECTOR, "#lista-camadas li .nome-cam")[0]
ActionChains(d).double_click(nome).perform()
time.sleep(0.3)
inp = d.find_element(By.CSS_SELECTOR, "#lista-camadas li .nome-cam input")
inp.send_keys(Keys.CONTROL, "a")
inp.send_keys("Nome falso")
inp.send_keys(Keys.ENTER)
ocioso()
checa(camadas()[0]["nome"] == "Nome falso", "renomear com duplo clique")

# 4) reordenar arrastando (eventos de arrastar do HTML5)
d.execute_script("""
const lis = document.querySelectorAll('#lista-camadas li[data-cam]');
const dt = new DataTransfer();
lis[0].dispatchEvent(new DragEvent('dragstart', {dataTransfer: dt, bubbles: true}));
const r = lis[1].getBoundingClientRect();
const op = {dataTransfer: dt, bubbles: true, cancelable: true, clientY: r.bottom - 2};
lis[1].dispatchEvent(new DragEvent('dragover', op));
lis[1].dispatchEvent(new DragEvent('drop', op));
lis[0].dispatchEvent(new DragEvent('dragend', {dataTransfer: dt, bubbles: true}));""")
ocioso()
checa([c["categoria"] for c in camadas()] == ["valor", "nome"], "arrastar reordena a pilha")

# 5) opacidade
d.execute_script("const s=document.getElementById('cam-opac'); s.value=40; s.dispatchEvent(new Event('change'));")
ocioso()
at = [c for c in camadas() if c["ativa"]][0]
checa(abs(at["opacidade"] - 0.4) < 1e-6, f"opacidade da camada ativa ({at['nome']})")

# 6) duplicar + desfazer
n = len(camadas())
tecla(Keys.CONTROL, "j")
ocioso()
checa(len(camadas()) == n + 1, "Ctrl+J duplica a camada")
tecla(Keys.CONTROL, "z")
ocioso()
checa(len(camadas()) == n, "Ctrl+Z desfaz a duplicação")

# 7) carimbo com Alt+clique e prévia fantasma
categoria("endereço")
tecla("s")
arrastar((340, 330), (340, 330), passos=1, alt=True)
time.sleep(0.3)
ActionChains(d).move_by_offset(5, 5).perform()
arrastar((340, 1300), (700, 1300))
ocioso()
checa(sessao()["historico"][-1]["rotulo"] == "carimbo", "carimbo aplicado após Alt+clique")
shot("02_carimbo")

# 8) preenchimento por similaridade (diálogo)
categoria("cep")
tecla("m")
arrastar((325, 410), (520, 452))
ocioso()
tecla(Keys.SHIFT, Keys.F5)
time.sleep(0.5)
checa("similaridade" in d.find_element(By.ID, "modal-titulo").text, "Shift+F5 abre o preenchimento por similaridade")
espera("!!window.rotulador.visor.previa", 30)
checa(d.execute_script("return !!window.rotulador.visor.previa"), "prévia do preenchimento")
shot("03_preencher")
[b for b in d.find_elements(By.CSS_SELECTOR, "#modal-acoes button") if b.text == "Aplicar"][0].click()
ocioso()
print("AVISOS:", [e.text for e in d.find_elements(By.CSS_SELECTOR, ".aviso")])
checa(sessao()["historico"][-1]["rotulo"] == "preencher", "preenchimento aplicado")

# 9) remendo
tecla("m")
arrastar((535, 450), (640, 480))
ocioso()
tecla("y")
arrastar((580, 465), (580, 1250))
ocioso()
checa(sessao()["historico"][-1]["rotulo"] == "remendo", "remendo aplicado")

# 10) formas
tecla("u")
arrastar((800, 1250), (1000, 1290))
ocioso()
checa(sessao()["historico"][-1]["rotulo"] == "forma", "forma aplicada")

# 11) pincel de seleção
tecla("q")
arrastar((330, 300), (600, 300))
ocioso()
checa(d.execute_script("return !!window.rotulador.selInfo"), "pincel de seleção cria seleção")
tecla(Keys.CONTROL, "d")
ocioso()

# 12) V sem seleção move a camada ativa
tecla("v")
time.sleep(0.3)
arrastar((800, 1270), (850, 1300))
ocioso()
checa(sessao()["historico"][-1]["tipo"] == "mover_camada", "V sem seleção move a camada")
shot("04_final")

# 13) salvar: JSON lista camadas
d.find_element(By.ID, "btn-salvar").click()
ocioso()
anot = sorted((SAIDA / "treino" / "anotacoes").glob("*.json"))
a = json.loads(anot[-1].read_text(encoding="utf-8")) if anot else {}
checa(len(a.get("camadas", [])) >= 4, f"JSON salvo lista as camadas ({len(a.get('camadas', []))})")
checa(set(a.get("categorias", [])) >= {"valor", "nome", "endereço", "cep"}, f"categorias salvas: {a.get('categorias')}")

erros = [e for e in d.get_log("browser") if e["level"] == "SEVERE"]
checa(not erros, f"sem erros de JS: {erros[:3]}")
d.quit()
print("\nFALHAS:", len(falhas))
