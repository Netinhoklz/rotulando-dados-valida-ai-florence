"""Teste do MODO SIMPLES como um usuário leigo (Edge headless) contra o servidor em 5077,
com os documentos realistas de gerar_amostras.py --realista.

    python tests_ui/ui_simples.py E:/rotulador/real
"""
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
PASTA = Path(sys.argv[1])
URL = "http://127.0.0.1:5077"
LOG = PASTA / "servidor.log"


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
        time.sleep(0.15)
    return False


def ocioso(t=300):
    time.sleep(0.3)
    espera("document.getElementById('ocupado').hidden && !window.rotulador.ocupado && !window.rotulador.pendentes", t)
    time.sleep(0.2)


def tela(x, y):
    return js("const v=window.rotulador.visor, r=v.canvas.getBoundingClientRect();"
              "const s=v.imgParaTela(arguments[0],arguments[1]); return [r.left+s.x, r.top+s.y];", x, y)


def arrastar(a, b, passos=6):
    ab = ActionBuilder(d)
    x0, y0 = tela(*a)
    x1, y1 = tela(*b)
    ab.pointer_action.move_to_location(int(x0), int(y0))
    ab.pointer_action.pointer_down()
    for i in range(1, passos + 1):
        ab.pointer_action.move_to_location(int(x0 + (x1 - x0) * i / passos), int(y0 + (y1 - y0) * i / passos))
    ab.pointer_action.pointer_up()
    ab.perform()


def sessao():
    return js("return window.rotulador.sessao")


def campos():
    return js("return window.rotulador.simples.campos")


def hist():
    return sessao()["historico"]


def shot(n):
    d.save_screenshot(str(PASTA / f"simples_{n}.png"))


# ------------------------------------------------------------------ 1ª visita
d.get(URL)
js("localStorage.clear(); localStorage.setItem('rotulador.operador', JSON.stringify('Teste UI'))")
d.get(URL)
espera("window.rotulador && window.rotulador.sessao", 120)
espera("document.getElementById('modal').open", 10)
checa("Bem-vindo" in d.find_element(By.ID, "modal-titulo").text, "tutorial aparece na 1ª visita")
checa(len(d.find_element(By.ID, "modal-corpo").text) > 150, "tutorial mostra os passos (não vazio)")
shot("01_tutorial")
d.find_element(By.XPATH, "//button[contains(., 'Entendi')]").click()
time.sleep(0.4)
checa(js("return document.body.classList.contains('simples')"), "abre no modo simples")
checa(not d.find_element(By.ID, "ferramentas").is_displayed(), "barra de ferramentas escondida")
d.find_element(By.CSS_SELECTOR, '[data-sp="treino"]').click()
ocioso()
d.find_elements(By.CSS_SELECTOR, "#lista-docs li")[0].click()
ocioso()
checa(espera("window.rotulador.simples.campos.length >= 10 && !window.rotulador.simples.lendo", 120),
      f"campos detectados ({len(campos())})")
cats = {c["categoria"] for c in campos()}
checa({"valor", "data", "nome", "cpf/cnpj", "cep"} <= cats, f"categorias: {sorted(c for c in cats if c)}")
shot("02_campos")

# ------------------------------------------------------------------ clicar no campo na imagem, 🎲, Trocar
valor = next(c for c in campos() if c["categoria"] == "valor")
cx, cy = (valor["caixa"][0] + valor["caixa"][2]) / 2, (valor["caixa"][1] + valor["caixa"][3]) / 2
arrastar((cx, cy), (cx, cy), passos=1)
time.sleep(0.4)
checa(js("return window.rotulador.simples.sel") == valor["id"], "clicar no campo da imagem seleciona")
d.find_element(By.XPATH, "//li[contains(@class,'ativo')]//button[contains(., '🎲')]").click()
time.sleep(1.0)
novo = d.find_element(By.CSS_SELECTOR, "li.campo.ativo input.novo").get_attribute("value")
checa(novo.startswith("R$ "), f"🎲 sorteou valor no formato ({novo})")
checa(espera("!!window.rotulador.visor.previa", 60), "prévia da troca")
shot("03_previa")
d.find_element(By.XPATH, "//li[contains(@class,'ativo')]//button[contains(., 'Trocar')]").click()
ocioso()
checa(hist() and hist()[-1]["tipo"] == "trocar_campo" and hist()[-1]["categoria"] == "valor", "Trocar aplicou (valor)")
checa(js("return window.rotulador.simples.sel") != valor["id"], "seleção passou para o próximo campo")
checa(len(d.find_elements(By.CSS_SELECTOR, "#lista-campos li.feito")) == 1, "campo marcado como trocado")

# ------------------------------------------------------------------ digitar + Enter no campo seguinte
data = next(c for c in campos() if c["categoria"] == "data")
d.find_element(By.CSS_SELECTOR, f'#lista-campos li[data-campo="{data["id"]}"] .campo-antigo').click()
time.sleep(0.4)
inp = d.find_element(By.CSS_SELECTOR, "li.campo.ativo input.novo")
inp.send_keys("25/12/2026")
inp.send_keys(Keys.ENTER)
ocioso()
checa(hist()[-1]["params"]["texto"] == "25/12/2026", "digitar + Enter troca")

# trocar o mesmo campo de novo (usa a fonte da 1ª troca)
d.find_element(By.CSS_SELECTOR, f'#lista-campos li[data-campo="{data["id"]}"] .campo-antigo').click()
time.sleep(0.4)
inp = d.find_element(By.CSS_SELECTOR, "li.campo.ativo input.novo")
inp.send_keys("31/01/2027")
inp.send_keys(Keys.ENTER)
ocioso()
checa(hist()[-1]["params"]["texto"] == "31/01/2027" and "fonte_params" in hist()[-1]["params"], "trocar de novo reaproveita a fonte")

# ------------------------------------------------------------------ desfazer grande
n = len(hist())
d.find_element(By.ID, "btn-desfazer-grande").click()
ocioso()
checa(len(hist()) == n - 1, "↶ Desfazer")

# ------------------------------------------------------------------ edição automática dos campos marcados
marcados = js("return [...window.rotulador.simples.marcados]")
cats = {c["id"]: c["categoria"] for c in campos()}
checa(marcados and {cats.get(i) for i in marcados} <= {"nome", "endereço"}, f"nome e endereço vêm marcados ({[cats.get(i) for i in marcados]})")
d.find_element(By.CSS_SELECTOR, "#marcar-rapido button:nth-of-type(2)").click()  # endereço completo
time.sleep(0.3)
marcados = js("return [...window.rotulador.simples.marcados]")
checa({cats.get(i) for i in marcados} == {c for c in ("endereço", "bairro", "cep", "cidade/uf") if c in cats.values()},
      "atalho 'endereço completo' marca rua, bairro, CEP e cidade")
n = len(hist())
d.find_element(By.ID, "btn-fraude").click()
ocioso(600)
novos = hist()[n:]
checa(len(novos) == len(marcados), f"editou exatamente os {len(marcados)} campos marcados ({len(novos)})")
# desde 04/10/2026 o botão usa a mistura do perfil profissional (gerador.PERFIL_PROFISSIONAL), não só a mesma fonte
PROFISSIONAIS = {"campo.mesma_fonte", "campo.letras_do_documento", "campo.reescrever_pdf", "ia.reescrever", "trecho.trocar",
                 "caractere.copiar_glifo", "caractere.redesenhar", "caractere.inserir", "caractere.transpor",
                 "campo.acrescentar", "campo.remover"}
checa(all(h["params"].get("tecnica") in PROFISSIONAIS for h in novos),
      f"só técnicas profissionais ({sorted({h['params'].get('tecnica') for h in novos})})")
checa(any(a.text.startswith("Feito:") for a in d.find_elements(By.CSS_SELECTOR, ".aviso.ok")),
      "aviso lista o que foi trocado")
shot("04_fraude")

# ------------------------------------------------------------------ marcar outro texto
n_campos = len(campos())
d.find_element(By.ID, "btn-marcar").click()
time.sleep(0.3)
total = next(c for c in campos() if c["categoria"] == "valor")
arrastar((170, 1560), (680, 1630))  # "TOTAL A PAGAR" (é rótulo, não vem na lista)
espera(f"window.rotulador.simples.campos.length > {n_campos}", 60)
novo_campo = [c for c in campos() if c.get("manual")]
checa(novo_campo and "TOTAL" in novo_campo[0]["texto"].upper(), f"marcar outro texto lê o trecho ({novo_campo and novo_campo[0]['texto']})")

# ------------------------------------------------------------------ salvar e próximo
doc0 = sessao()["id"]
d.find_element(By.ID, "btn-salvar").click()
ocioso()
espera(f"window.rotulador.sessao && window.rotulador.sessao.id !== '{doc0}'", 60)
checa(sessao()["id"] != doc0, "✔ Salvar e próximo abriu outro documento")
checa(len(list((PASTA / "saida" / "treino" / "anotacoes").glob("*.json"))) == 1, "versão salva no treino")
checa(espera("window.rotulador.simples.campos.length >= 10", 120), "campos do próximo documento lidos")
shot("05_proximo")

# ------------------------------------------------------------------ modo avançado e volta
d.find_element(By.ID, "btn-modo").click()
time.sleep(0.5)
checa(d.find_element(By.ID, "ferramentas").is_displayed(), "modo avançado mostra as ferramentas")
d.find_element(By.ID, "btn-modo").click()
time.sleep(0.5)
checa(not d.find_element(By.ID, "ferramentas").is_displayed(), "volta ao modo simples")

# ------------------------------------------------------------------ gerador automático (outra aba)
janelas = set(d.window_handles)
editor = d.current_window_handle
d.find_element(By.ID, "btn-lote").click()
t0 = time.time()
while time.time() - t0 < 10 and len(d.window_handles) == len(janelas):
    time.sleep(0.2)
novas = [h for h in d.window_handles if h not in janelas]
checa(bool(novas), "botão do gerador abre outra aba")
d.switch_to.window(novas[0])
checa(espera("location.pathname === '/gerador' && document.querySelectorAll('#tec-familias .tec').length >= 18", 60),
      "página do gerador carregou com o catálogo de técnicas")
lote = api("/api/gerador/iniciar", {"receita": {"documentos": {"splits": ["validacao"]}, "variacoes": 1, "edicoes": [1, 2],
                                                "tecnicas": {"caractere.redesenhar": 1, "trecho.trocar": 1, "campo.reescrever": 1}}})["id"]
t0 = time.time()  # espera ESTE lote (a página pode ainda mostrar "terminada" do lote anterior)
while time.time() - t0 < 600:
    st = api("/api/gerador/status")
    if st.get("id") == lote and not st.get("rodando"):
        break
    time.sleep(1)
checa(espera("!document.getElementById('painel-progresso').hidden && "
              "document.getElementById('prog-titulo').textContent.includes('terminada')", 60), "geração terminou")
shot("06_gerador")
n_val = len(list((PASTA / "saida" / "validacao" / "anotacoes").glob("*.json")))
checa(n_val == 2, f"gerador salvou 2 versões na validação ({n_val})")
checa(not (PASTA / "saida" / "teste" / "anotacoes").exists(), "nada salvo no teste")
erros_gerador = [e for e in d.get_log("browser") if e["level"] == "SEVERE"]
d.close()
d.switch_to.window(editor)

erros = erros_gerador + [e for e in d.get_log("browser") if e["level"] == "SEVERE"]
checa(not erros, f"sem erros de JS: {erros[:3]}")
txt = LOG.read_text(encoding="utf-8", errors="replace")
checa("Traceback" not in txt, "sem traceback no servidor")
d.quit()
print("\nFALHAS:", len(falhas))
