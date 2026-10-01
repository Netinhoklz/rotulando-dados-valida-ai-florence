"""Teste de UI ponta a ponta (Edge headless) contra o servidor em 5077."""
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

sys.stdout.reconfigure(encoding="utf-8")
S = Path(sys.argv[1])
URL = "http://127.0.0.1:5077"
op = webdriver.EdgeOptions()
op.add_argument("--headless=new")
op.add_argument("--window-size=1600,1000")
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
    time.sleep(0.15)
    return espera("document.getElementById('ocupado').hidden && !window.rotulador.ocupado")


def tela(x, y):
    return d.execute_script(
        "const v=window.rotulador.visor, r=v.canvas.getBoundingClientRect();"
        "const s=v.imgParaTela(arguments[0],arguments[1]); return [r.left+s.x, r.top+s.y];", x, y)


def arrastar(a, b, passos=8):
    ab = ActionBuilder(d)
    p = ab.pointer_action
    x0, y0 = tela(*a)
    x1, y1 = tela(*b)
    p.move_to_location(int(x0), int(y0))
    p.pointer_down()
    for i in range(1, passos + 1):
        p.move_to_location(int(x0 + (x1 - x0) * i / passos), int(y0 + (y1 - y0) * i / passos))
    p.pointer_up()
    ab.perform()


def tecla(*ks):
    # foca sem clicar (send_keys_to_element clica e o clique viraria uma pincelada)
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


def shot(n):
    d.save_screenshot(str(S / f"ui_{n}.png"))


def sessao():
    return d.execute_script("return window.rotulador.sessao")


def botao_modal(texto):
    return [b for b in d.find_elements(By.CSS_SELECTOR, "#modal-acoes button") if b.text == texto][0]


# estado limpo: descarta rascunhos e apaga saídas de execuções anteriores
import json
import shutil
import urllib.request


def api(rota, corpo=None):
    req = urllib.request.Request(URL + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


for sp in ("treino", "teste", "validacao"):
    for item in api(f"/api/documentos?split={sp}"):
        api("/api/descartar", {"id": item["id"]})
    for sub in ("originais", "editadas", "mascaras", "mascaras_regiao", "mascaras_classes", "anotacoes"):
        shutil.rmtree(S / "saida_teste" / sp / sub, ignore_errors=True)
    (S / "saida_teste" / sp / "manifesto.csv").unlink(missing_ok=True)

d.get(URL)
checa(espera("window.rotulador && window.rotulador.sessao && document.getElementById('vazio').hidden"),
      "abre documento inicial")
ocioso()
shot("01_inicio")
d.find_element(By.CSS_SELECTOR, '[data-sp="treino"]').click()
ocioso()
d.find_elements(By.CSS_SELECTOR, "#lista-docs li")[0].click()
ocioso()
checa(sessao()["split"] == "treino", "trocou para o treino")
doc = sessao()["id"]

# 1) sem categoria -> bloqueado
tecla("b")
arrastar((300, 1200), (600, 1200))
time.sleep(0.6)
checa(len(sessao()["historico"]) == 0, "pincel sem categoria NAO aplica")
checa("faltando" in d.find_element(By.ID, "categoria").get_attribute("class"), "destaca o seletor de categoria")

# 2) substituir texto (valor)
Select(d.find_element(By.ID, "categoria")).select_by_value("valor")
tecla("r")
time.sleep(0.2)
arrastar((790, 772), (1080, 830))
ocioso()
espera("window.rotulador.F.substituir.ancora !== null", 10)
anc = d.execute_script("return window.rotulador.F.substituir.ancora")
checa(anc is not None and 790 <= anc["x"] <= 830, f"estimou ancora do texto antigo: {anc}")
tam = d.execute_script("return window.rotulador.opcoes('substituir').tamanho")
checa(25 <= tam <= 70, f"estimou tamanho da fonte: {tam}")
campo = d.find_element(By.ID, "op-texto")
campo.clear()
campo.send_keys("R$ 987,65")
time.sleep(2.0)
shot("02_previa_substituir")
checa(d.execute_script("return !!window.rotulador.visor.previa"), "mostra previa do texto novo")
campo.send_keys(Keys.ENTER)
ocioso()
s = sessao()
checa(len(s["historico"]) == 1 and s["historico"][0]["categoria"] == "valor", "substituir_texto aplicado (valor)")
checa(s["pode_salvar"], "salvar liberado")
shot("03_aplicado")

# 3) pincel (nome) + desfazer/refazer
Select(d.find_element(By.ID, "categoria")).select_by_value("nome")
tecla("b")
arrastar((340, 225), (500, 235))
ocioso()
checa(len(sessao()["historico"]) == 2, "pincel aplicado")
tecla(Keys.CONTROL, "z")
ocioso()
checa(len(sessao()["historico"]) == 1 and sessao()["pode_refazer"], "Ctrl+Z desfaz")
tecla(Keys.CONTROL, "y")
ocioso()
checa(len(sessao()["historico"]) == 2, "Ctrl+Y refaz")

# 4) selecao + transformar (duplicar) o CEP
Select(d.find_element(By.ID, "categoria")).select_by_value("cep")
tecla("m")
arrastar((328, 418), (500, 452))
ocioso()
time.sleep(0.4)
checa(d.execute_script("return !!window.rotulador.selInfo"), "selecao retangular feita")
tecla("v")
time.sleep(0.5)
arrastar((400, 435), (400, 1300))
time.sleep(2.0)
shot("04_transformar")
checa(d.execute_script("return !!window.rotulador.visor.previa"), "previa do transformar")
tecla(Keys.ENTER)
ocioso()
checa(len(sessao()["historico"]) == 3, "transformar aplicado")

# 5) ajuste (desfoque) via dialogo
Select(d.find_element(By.ID, "categoria")).select_by_value("endereço")
tecla("m")
arrastar((328, 310), (800, 345))
ocioso()
time.sleep(0.3)
d.find_element(By.CSS_SELECTOR, '#ferramentas button[title^="Ajustes"]').click()
time.sleep(0.4)
checa(d.find_element(By.ID, "modal").get_attribute("open") is not None, "abre dialogo de ajustes")
Select(d.find_element(By.CSS_SELECTOR, "#modal-corpo select:not(.sel-cat-modal)")).select_by_value("desfoque")
time.sleep(1.0)
shot("05_ajuste")
botao_modal("Aplicar").click()
ocioso()
checa(len(sessao()["historico"]) == 4, "ajuste aplicado")

# 6) mascara
tecla("k")
time.sleep(0.8)
shot("06_mascara")
tecla("k")
checa(set(sessao()["categorias"]) == {"valor", "nome", "cep", "endereço"}, f"categorias derivadas: {sessao()['categorias']}")

# 7) salvar
Select(d.find_element(By.ID, "formato")).select_by_value("png")
d.find_element(By.ID, "operador").send_keys("teste-ui")
d.find_element(By.ID, "btn-salvar").click()
ocioso()
time.sleep(0.5)
shot("07_salvo")
s = sessao()
checa(s["versoes"] == 1 and s["historico"] == [], "salvou v01 e recomecou do original")
base = S / "saida_teste" / "treino"
checa((base / "editadas" / f"{doc}__v01.png").is_file(), "editada em saida/treino")
checa((base / "originais" / f"{doc}.png").is_file(), "original em saida/treino")
checa(not (S / "saida_teste" / "teste" / "editadas").exists() and not (S / "saida_teste" / "validacao" / "editadas").exists(),
      "nada nos outros splits")

# 8) colar de outro documento: so do mesmo split
d.find_element(By.CSS_SELECTOR, '#ferramentas button[data-f="colar"]').click()
time.sleep(1.0)
nomes = [e.get_attribute("title") for e in d.find_elements(By.CSS_SELECTOR, ".grade-doadores button")]
checa(len(nomes) == 3 and all(n.startswith("conta_0") for n in nomes), f"doadores so do treino: {nomes}")
d.find_elements(By.CSS_SELECTOR, ".grade-doadores button")[0].click()
time.sleep(1.5)
c = d.find_element(By.CSS_SELECTOR, ".doador-area canvas")
ActionChains(d).move_to_element_with_offset(c, -200, -250).click_and_hold().move_by_offset(150, 30).release().perform()
botao_modal("Usar trecho").click()
time.sleep(1.5)
Select(d.find_element(By.ID, "categoria")).select_by_value("nome")
shot("08_colar")
tecla(Keys.ENTER)
ocioso()
checa(len(sessao()["historico"]) == 1 and sessao()["historico"][0]["tipo"] == "colar", "colagem de doador aplicada")

# 9) PDF no teste
d.find_element(By.CSS_SELECTOR, '[data-sp="teste"]').click()
ocioso()
pdfs = [e for e in d.find_elements(By.CSS_SELECTOR, "#lista-docs li") if "pdf" in e.text]
checa(len(pdfs) == 2, f"PDF de 2 paginas listado como 2 itens ({len(pdfs)})")
pdfs[1].click()
ocioso()
checa("pág. 2/2" in sessao()["nome"], "abriu a pagina 2 do PDF")
shot("09_pdf")

erros = [entrada for entrada in d.get_log("browser") if entrada["level"] == "SEVERE"]
checa(not erros, f"sem erros de JS no console: {erros[:3]}")
d.quit()
print("\nFALHAS:", len(falhas))
