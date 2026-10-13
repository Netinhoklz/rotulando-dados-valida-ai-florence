"""Teste do modo 🎯 EXATO do gerador (o padrão) no Edge headless, contra o servidor em 5077 com os documentos
realistas.

    python tests_ui/ui_exato.py E:/rotulador/real

A página abre no modo exato com nome e endereço marcados; os atalhos e as caixas mudam o que é editado; a prévia
só tem edições das classes marcadas, todas na mesma fonte; o lote grava versões sem canal, sem marcas e sem
falsificador, com a original igual ao documento e só as classes marcadas editadas. Capturas em PASTA/ui_exato_*.png.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8")
PASTA = Path(sys.argv[1])
URL = "http://127.0.0.1:5077"


def api(rota, corpo=None):
    req = urllib.request.Request(URL + rota, data=None if corpo is None else json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


op = webdriver.EdgeOptions()
op.add_argument("--headless=new")
op.add_argument("--window-size=1600,1000")
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
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def clica(css, texto=None):
    alvo = next((e for e in d.find_elements(By.CSS_SELECTOR, css) if texto is None or texto in e.text), None)
    assert alvo is not None, f"não achei {css} {texto or ''}"
    d.execute_script("arguments[0].scrollIntoView({block:'center'})", alvo)
    alvo.click()
    return alvo


def marcadas():
    return sorted(e.get_attribute("data-classe") for e in d.find_elements(By.CSS_SELECTOR, "#exato-classes input:checked"))


try:
    assert not api("/api/gerador/status").get("rodando"), "há uma geração rodando no servidor de teste"
    d.get(URL + "/gerador")
    # o modo exato fica em "Opções avançadas" (recolhida por padrão): o teste a abre
    js("localStorage.clear(); localStorage.setItem('rotulador.operador', JSON.stringify('Teste UI')); localStorage.setItem('rotulador.geradorAvancado', 'true');")
    d.get(URL + "/gerador")
    checa(espera(lambda: d.find_element(By.ID, "bloco-exato").is_displayed()), "abre no modo 🎯 Exato")
    checa(all(d.find_element(By.ID, i).get_attribute("hidden") is not None
              for i in ("bloco-tipos", "bloco-tec", "bloco-cat", "bloco-canal", "bloco-quem")), "o resto da receita fica escondido")
    checa(marcadas() == ["endereço", "nome"], f"nome e endereço marcados por padrão ({marcadas()})")
    clica("#exato-presets button", "Endereço completo")
    checa(marcadas() == ["bairro", "cep", "cidade/uf", "endereço"], f"atalho 'Endereço completo' ({marcadas()})")
    clica("#exato-classes label", "Nome")
    checa("nome" in marcadas(), "marcar uma classe à mão")
    checa("Nome" in d.find_element(By.ID, "plano-corpo").text, "plano lista o que será editado")
    d.save_screenshot(str(PASTA / "ui_exato_01_marcar.png"))

    # prévia: só edições das classes marcadas, todas na mesma fonte
    clica("#btn-previa")
    checa(espera(lambda: len(d.find_elements(By.CSS_SELECTOR, "#previa-lista .edicao")) >= 3, 240), "prévia com as edições")
    textos = [e.text for e in d.find_elements(By.CSS_SELECTOR, "#previa-lista .edicao")]
    checa(all("mesma fonte" in t.lower() for t in textos), f"todas na mesma fonte ({len(textos)})")
    d.save_screenshot(str(PASTA / "ui_exato_02_previa.png"))

    # lote: 1 documento, 1 versão
    v = d.find_element(By.ID, "exato-variacoes")
    js("arguments[0].value = 1; arguments[0].dispatchEvent(new Event('change'))", v)
    clica("#docs-nenhum")
    primeiro = d.find_elements(By.CSS_SELECTOR, "#docs-lista li input")[0]
    js("arguments[0].click()", primeiro)
    checa(espera(lambda: "1 imagem" in d.find_element(By.ID, "btn-gerar").text), "estimativa de 1 imagem")
    antes = {p.name for p in (PASTA / "saida" / "treino" / "anotacoes").glob("*.json")} if (PASTA / "saida" / "treino" / "anotacoes").is_dir() else set()
    clica("#btn-gerar")
    checa(espera(lambda: api("/api/gerador/status").get("rodando") is False and api("/api/gerador/status").get("feitas", 0) >= 1, 600),
          "lote terminou")
    novas = [p for p in (PASTA / "saida" / "treino" / "anotacoes").glob("*.json") if p.name not in antes]
    checa(len(novas) == 1, f"uma versão nova ({len(novas)})")
    if novas:
        a = json.loads(novas[0].read_text(encoding="utf-8"))
        raiz = PASTA / "saida" / "treino"
        checa(a["canal"] is None and not a["marcas_benignas"] and "falsificador" not in a, "sem canal, marcas ou falsificador")
        checa({e["categoria"] for e in a["edicoes"]} <= {"nome", "bairro", "cep", "cidade/uf", "endereço"},
              f"só as classes marcadas ({sorted({e['categoria'] for e in a['edicoes']})})")
        checa(all(e["tecnica"] == "campo.mesma_fonte" for e in a["edicoes"]), "todas pela técnica da mesma fonte")
        orig = np.asarray(Image.open(raiz / a["arquivos"]["original"]).convert("RGB"))
        ed = np.asarray(Image.open(raiz / a["arquivos"]["editada"]).convert("RGB"))
        m = np.asarray(Image.open(raiz / a["arquivos"]["mascara"])) > 0
        checa(orig.shape == ed.shape and np.array_equal((orig != ed).any(2), m), "a editada só difere da original na máscara")
        api("/api/catalogo/excluir", {"split": "treino", "nome": novas[0].stem})
    erros = [l for l in d.get_log("browser") if l["level"] == "SEVERE"]
    checa(not erros, f"sem erros no console: {erros[:2]}")
finally:
    d.quit()

print("\nTUDO OK" if not falhas else f"\nFALHAS: {len(falhas)}")
sys.exit(1 if falhas else 0)
