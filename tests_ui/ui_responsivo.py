"""Layout em tela de computador e de celular (Edge headless), contra o servidor em 5077.

    python tests_ui/ui_responsivo.py E:/rotulador/real

Problemas que a exploração do site achou (2026-10-02) e que este teste segura:
  - tutorial vazio (o texto ia para um atributo, não para o conteúdo);
  - nome do documento com 0 px no topo;
  - no celular: documento com 0 px de largura, prévia do gerador fora de alcance, diálogo
    flutuante saindo pela esquerda da tela.
"""
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8")
PASTA = Path(sys.argv[1])
URL = "http://127.0.0.1:5077"
LOG = PASTA / "servidor.log"

op = webdriver.EdgeOptions()
op.add_argument("--headless=new")
op.add_argument("--window-size=1400,900")
op.set_capability("ms:loggingPrefs", {"browser": "ALL"})
d = webdriver.Edge(options=op)
falhas = []
log_antes = LOG.read_text(encoding="utf-8", errors="replace") if LOG.is_file() else ""


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


def sem_rolagem_lateral():
    return js("return document.documentElement.scrollWidth <= window.innerWidth + 2")


def largura(seletor):
    return js("const e = document.querySelector(arguments[0]); return e ? e.getBoundingClientRect().width : -1", seletor)


try:
    # ------------------------------------------------------------------ computador (1400x900)
    d.get(URL + "/")
    js("localStorage.clear(); localStorage.setItem('rotulador.operador', JSON.stringify('Teste UI'))")
    d.get(URL + "/")
    checa(espera("document.getElementById('modal-titulo') && document.getElementById('modal-titulo').textContent.includes('Bem-vindo')"),
          "tutorial abre na 1ª visita")
    checa(len(d.find_element(By.ID, "modal-corpo").text) > 150, "tutorial mostra os 4 passos (não vazio)")
    d.find_element(By.CSS_SELECTOR, "#modal-acoes button").click()
    checa(espera("document.querySelectorAll('#lista-docs li').length > 0"), "lista de documentos")
    d.find_elements(By.CSS_SELECTOR, "#lista-docs li")[0].click()
    checa(espera("document.getElementById('doc-nome').textContent.length > 3 && !document.getElementById('vazio') ||"
                 " document.getElementById('vazio').hidden", 60), "documento aberto")
    time.sleep(1)
    checa(largura("#doc-nome") >= 120, f"nome do documento visível no topo, modo simples ({largura('#doc-nome'):.0f} px)")
    d.find_element(By.ID, "btn-modo").click()
    time.sleep(0.5)
    checa(largura("#doc-nome") >= 120, f"nome do documento visível no topo, modo avançado ({largura('#doc-nome'):.0f} px)")
    d.find_element(By.ID, "btn-modo").click()  # volta ao simples

    # ------------------------------------------------------------------ celular (390x844)
    d.set_window_size(390, 844)
    d.get(URL + "/")
    checa(espera("document.querySelectorAll('#lista-docs li').length > 0"), "celular: página do editor carrega")
    d.find_elements(By.CSS_SELECTOR, "#lista-docs li")[0].click()
    time.sleep(1.5)
    checa(largura("#visor") >= 300, f"celular, modo simples: documento visível ({largura('#visor'):.0f} px)")
    checa(sem_rolagem_lateral(), "celular, modo simples: sem rolagem lateral")
    d.find_element(By.ID, "btn-modo").click()
    time.sleep(0.8)
    checa(largura("#visor") >= 250, f"celular, modo avançado: documento visível ({largura('#visor'):.0f} px)")
    dir_salvar = js("return document.getElementById('btn-salvar').getBoundingClientRect().right")
    checa(dir_salvar <= js("return window.innerWidth") + 1, f"celular, modo avançado: botão Salvar inteiro na tela ({dir_salvar:.0f})")
    flut = [b for b in d.find_elements(By.CSS_SELECTOR, "#ferramentas button")
            if any(p in (b.get_attribute("title") or "") for p in ("Ajustes", "ajustes", "Preencher", "preencher"))]
    if flut:
        js("arguments[0].click()", flut[0])
        time.sleep(0.8)
        caixa = js("const r = document.querySelector('dialog[open]')?.getBoundingClientRect(); return r ? [r.left, r.right] : null")
        if caixa:
            checa(caixa[0] >= 0 and caixa[1] <= js("return window.innerWidth") + 1, f"celular: diálogo flutuante dentro da tela {caixa}")
            js("document.querySelector('dialog[open]')?.close()")
    d.find_element(By.ID, "btn-modo").click()  # volta ao simples

    js("localStorage.setItem('rotulador.geradorAvancado', 'true')")  # receita completa aberta: o layout dela também conta
    d.get(URL + "/gerador")
    checa(espera("document.querySelectorAll('#tec-familias .tec').length > 0"), "celular: gerador carrega")
    checa(sem_rolagem_lateral(), "celular, gerador: sem rolagem lateral")
    esq = js("return document.getElementById('resultado').getBoundingClientRect().left")
    checa(esq < 40, f"celular, gerador: resultados embaixo da receita, alcançáveis ({esq:.0f})")

    erros = [l for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]]
    checa(not erros, f"sem erros de JS: {erros[:3]}")
    novo_log = (LOG.read_text(encoding="utf-8", errors="replace") if LOG.is_file() else "")[len(log_antes):]
    checa("Traceback" not in novo_log, "sem traceback no servidor")
finally:
    d.quit()
print("FALHAS:", len(falhas))
sys.exit(1 if falhas else 0)
