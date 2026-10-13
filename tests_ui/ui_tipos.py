"""Teste do MODO FÁCIL do gerador (tipos de edição) no Edge headless, contra o servidor em 5077 com os
documentos realistas de gerar_amostras.py --realista.

    python tests_ui/ui_tipos.py E:/rotulador/real

Usa a página como uma pessoa: começa com os tipos iniciais, acrescenta uma ideia pronta, cria um tipo
pelo diálogo (classes, jeito, capricho, quanto), testa o tipo sozinho (a prévia só pode ter edições
dele), gera um lote pequeno e confere nas anotações que só entraram combinações técnica × classe de
algum tipo; depois o modo avançado mostra o aviso de tipos valendo. Tira capturas em PASTA/ui_tipos_*.png.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

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


def clica(css, texto=None, raiz=None):
    els = (raiz or d).find_elements(By.CSS_SELECTOR, css)
    alvo = next((e for e in els if texto is None or texto in e.text), None)
    assert alvo is not None, f"não achei {css} {texto or ''}"
    d.execute_script("arguments[0].scrollIntoView({block:'center'})", alvo)
    alvo.click()
    return alvo


def foto(n, nome):
    d.save_screenshot(str(PASTA / f"ui_tipos_{n:02d}_{nome}.png"))


try:
    status = api("/api/gerador/status")
    assert not status.get("rodando"), "há uma geração rodando no servidor de teste"
    d.get(URL + "/gerador")
    # o modo fácil fica em "Opções avançadas" (recolhida por padrão): o teste a abre
    js("localStorage.clear(); localStorage.setItem('rotulador.operador', JSON.stringify('Teste UI'));"
       " localStorage.setItem('rotulador.geradorAvancado', 'true');"
       " localStorage.setItem('rotulador.modoGeradorV2', JSON.stringify('facil'))")
    d.get(URL + "/gerador")
    checa(espera(lambda: len(d.find_elements(By.CSS_SELECTOR, "#tipos-lista .tipo")) == 4), "começa com 4 tipos iniciais")
    checa(not d.find_element(By.ID, "bloco-tipos").get_attribute("hidden"), "modo fácil é o padrão")
    checa(d.find_element(By.ID, "bloco-tec").get_attribute("hidden") is not None, "técnicas do avançado escondidas")
    checa(espera(lambda: "imagens" in d.find_element(By.ID, "btn-gerar").text), "estimativa com os tipos")
    checa("Valor da conta" in d.find_element(By.ID, "plano-corpo").text, "plano do lote lista os tipos")
    checa(len(d.find_elements(By.CSS_SELECTOR, "#quem-cartoes .cartao-escolha.on")) >= 4, "falsificadores ligados por padrão")
    # padrão: a imagem como está, só a edição (o "como uma pessoa faria" é opção): liga em 80%
    checa("como uma pessoa faria" not in d.find_element(By.ID, "plano-corpo").text, "padrão: sem falsificador")
    js("const r = document.getElementById('quem-prob'); r.value = 80; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change'))")
    checa(espera(lambda: "como uma pessoa faria" in d.find_element(By.ID, "plano-corpo").text), "plano mostra o modo humano")
    clica("#quem-cartoes .cartao-escolha", "Paint")
    checa(espera(lambda: not any("Paint" in b.text for b in d.find_elements(By.CSS_SELECTOR, "#quem-cartoes .cartao-escolha.on"))),
          "desliga um falsificador")
    rec = js("return JSON.parse(localStorage.getItem('rotulador.receitaGerador'))")
    checa(rec["falsificadores"]["pesos"]["paint"] == 0 and rec["falsificadores"]["prob"] == 0.8, "receita guarda o peso 0 e 80%")
    js("const r = document.getElementById('quem-prob'); r.value = 100; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change'))")
    checa(espera(lambda: "100%" in d.find_element(By.ID, "quem-resumo").text), "100% das versões como uma pessoa faria")
    foto(1, "inicio")

    # uma ideia pronta
    clica("#tipos-ideias .ideia", "Códigos de pagamento")
    checa(espera(lambda: len(d.find_elements(By.CSS_SELECTOR, "#tipos-lista .tipo")) == 5), "ideia pronta acrescenta um tipo")
    checa("Códigos de pagamento" in d.find_elements(By.CSS_SELECTOR, "#tipos-lista .tipo")[-1].text, "tipo da ideia no fim")

    # tirar todos menos um novo, criado no diálogo
    # cada clique redesenha a lista: procura de novo a cada vez
    js("let c; while ((c = document.querySelector('#tipos-lista .tipo input[type=checkbox]:checked'))) c.click();")
    checa(espera(lambda: d.find_element(By.ID, "btn-gerar").get_attribute("disabled") is not None), "sem tipo ligado: Gerar desliga")
    checa("tipo de edição" in (d.find_element(By.ID, "btn-gerar").get_attribute("title") or ""), "Gerar explica o que falta")
    clica("#tipo-novo")
    dlg = d.find_element(By.ID, "dlg-tipo")
    checa(espera(lambda: dlg.is_displayed()), "diálogo do tipo abre")
    checa(d.find_element(By.ID, "dt-salvar").get_attribute("disabled") is not None, "sem escolhas não dá para salvar")
    clica("#dt-classes .cartao-escolha", "CEP", dlg)
    clica("#dt-classes .cartao-escolha", "Data", dlg)
    codigo = next(b for b in dlg.find_elements(By.CSS_SELECTOR, "#dt-jeitos .cartao-escolha") if "código novo" in b.text)
    checa(codigo.get_attribute("disabled") is not None, "jeito incompatível (código novo p/ CEP e data) fica desligado")
    clica("#dt-jeitos .cartao-escolha", "letra ou dígito", dlg)
    clica("#dt-capricho .cartao-escolha", "Amador", dlg)  # fica só o profissional
    clica("#dt-quanto button", "muito", dlg)
    frase = d.find_element(By.ID, "dt-frase").text
    checa("CEP" in frase and "profissional" in frase and "técnicas" in frase, f"frase do tipo: {frase}")
    checa(d.find_element(By.ID, "dt-nome").get_attribute("placeholder") == "CEP e Data", "nome automático pelas classes")
    foto(2, "dialogo")
    clica("#dt-salvar")
    checa(espera(lambda: not dlg.is_displayed()), "diálogo fecha ao guardar")
    tipos = d.find_elements(By.CSS_SELECTOR, "#tipos-lista .tipo")
    checa(len(tipos) == 6 and "CEP e Data" in tipos[-1].text and "100% das edições" in tipos[-1].text, "novo tipo é o único valendo")
    checa(espera(lambda: not d.find_element(By.ID, "btn-gerar").get_attribute("disabled")), "Gerar volta a ligar")

    # testar só este tipo: a prévia só tem edições de técnicas profissionais de caractere em CEP/data
    rec = js("return JSON.parse(localStorage.getItem('rotulador.receitaGerador'))")
    cat = api("/api/catalogo")
    jeito = next(j for j in cat["jeitos"] if j["id"] == "caractere")
    prof = {t["id"] for t in cat["tecnicas"] if t["id"] in jeito["tecnicas"] and t["nivel"] == "profissional"}
    clica("#tipos-lista .tipo:last-child .tipo-acoes button", "testar")
    checa(espera(lambda: "só o tipo" in d.find_element(By.ID, "previa-titulo").text, 180), "prévia do tipo sozinho")
    eds = js("return [...document.querySelectorAll('#previa-lista .edicao .cat-chip')].map(e => e.textContent)")
    checa(eds and set(eds) <= {"CEP", "Data"}, f"prévia só em CEP/Data: {eds}")
    quem = [e.text for e in d.find_elements(By.CSS_SELECTOR, "#previa-lista .falsificador-previa")]
    checa(quem and "Feita como uma pessoa faria" in quem[0], f"prévia diz quem fez: {quem}")
    foto(3, "previa_tipo")

    # lote pequeno: um documento, 2 versões
    clica("#docs-nenhum")
    primeiro = d.find_elements(By.CSS_SELECTOR, "#docs-lista input[type=checkbox]")[0]
    js("arguments[0].click()", primeiro)
    js("document.querySelector('#quanto .stepper input').value = 2; document.querySelector('#quanto .stepper input').dispatchEvent(new Event('change'))")
    clica("#quanto .segmentado button", "1 só")
    checa(espera(lambda: "2 imagens" in d.find_element(By.ID, "btn-gerar").text), "estimativa: 2 imagens")
    clica("#canal-cartoes .cartao-escolha", "Nenhum")
    def todas():  # catálogo inteiro (a API pagina de 100 em 100)
        out, pg = {}, 0
        while True:
            r = api(f"/api/catalogo/itens?por_pagina=100&pagina={pg}")
            out.update({(v["split"], v["nome"]): v for v in r["itens"]})
            if (pg + 1) * 100 >= r["total"]:
                return out
            pg += 1
    antes = set(todas())
    clica("#btn-gerar")
    checa(espera(lambda: api("/api/gerador/status")["rodando"] is False and len(todas()) >= len(antes) + 2, 600), "lote gerado")
    novas = [v for k, v in todas().items() if k not in antes]
    checa(len(novas) == 2, f"2 versões novas: {len(novas)}")
    tecs = {(e["tecnica"], e["categoria"]) for v in novas for e in v["edicoes"]}
    checa(tecs and all(t in prof and c in ("cep", "data") for t, c in tecs), f"só combinações do tipo: {tecs}")
    checa(all(v.get("falsificador") for v in novas), f"as versões gravam o falsificador: {[v.get('falsificador') for v in novas]}")
    checa(espera(lambda: d.find_elements(By.CSS_SELECTOR, "#plano-corpo .exemplos-reais img"), 30), "plano mostra exemplos reais")
    foto(4, "depois_do_lote")

    # avançado: aviso de tipos valendo; desligar os tipos devolve o controle às técnicas
    clica("#modo-receita button", "Avançado")
    checa(espera(lambda: d.find_element(By.ID, "aviso-tipos").is_displayed()), "avançado avisa que os tipos estão valendo")
    foto(5, "avancado")
    clica("#aviso-tipos-desligar")
    checa(espera(lambda: not d.find_element(By.ID, "aviso-tipos").is_displayed()), "desligar os tipos tira o aviso")
    checa(espera(lambda: "Imitação" in d.find_element(By.ID, "plano-corpo").text or "imitação" in d.find_element(By.ID, "plano-corpo").text.lower()),
          "plano do avançado por família")
    clica("#modo-receita button", "Fácil")
    checa(espera(lambda: not d.find_element(By.ID, "bloco-tipos").get_attribute("hidden")), "volta ao fácil")

    # celular estreito: nada vaza para os lados
    d.set_window_size(400, 900)
    time.sleep(0.5)
    larg = js("return [document.documentElement.scrollWidth, window.innerWidth]")
    checa(larg[0] <= larg[1] + 1, f"sem rolagem lateral no celular: {larg}")
    foto(6, "celular")

    erros = [l for l in d.get_log("browser") if l["level"] == "SEVERE"]
    checa(not erros, f"sem erros no console: {erros[:3]}")
finally:
    d.quit()

print("\n" + ("TUDO OK" if not falhas else f"{len(falhas)} FALHA(S): {falhas}"))
sys.exit(1 if falhas else 0)
