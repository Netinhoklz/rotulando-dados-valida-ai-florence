// Editor principal: estado, eventos, painéis e diálogos.
import { bitmap, bitmapUrl, get, post } from "./api.js";
import { instalarBarras } from "./barras.js";
import { criarFerramentas, GRUPOS, ICONES, NOMES } from "./ferramentas.js";
import { abrirGlossario, instalarGlossario, ligarTermos } from "./glossario.js";
import { icone } from "./icones.js";
import { menuAberto, montarMenus } from "./menus.js";
import { definirOperador, exigirOperador, nomeOperador, notificar } from "./operador.js";
import { Visor } from "./visor.js";

const $ = (id) => document.getElementById(id);
const SPLITS = [["treino", "Treino"], ["teste", "Teste"], ["validacao", "Validação"]];
const LENTAS = new Set(["preencher", "corretivo", "substituir_texto", "transformar", "ia", "recuperacao", "remendo",
  "trocar_campo_ia", "colar"]);
const AJUSTES = {
  brilho_contraste: ["Brilho/contraste", [["brilho", -100, 100, 1, 0], ["contraste", -100, 100, 1, 0]]],
  niveis: ["Níveis", [["preto", 0, 254, 1, 0], ["branco", 1, 255, 1, 255], ["gama", 0.1, 5, 0.05, 1]]],
  matiz_saturacao: ["Matiz/saturação", [["matiz", -180, 180, 1, 0], ["saturacao", -100, 100, 1, 0], ["luminosidade", -100, 100, 1, 0]]],
  desfoque: ["Desfoque gaussiano", [["sigma", 0.1, 20, 0.1, 1]]],
  nitidez: ["Nitidez (máscara de nitidez)", [["quantidade", 0, 500, 5, 100], ["raio", 0.1, 10, 0.1, 1]]],
  ruido: ["Ruído", [["sigma", 0, 60, 0.5, 5], ["semente", 0, 99999, 1, 0]]],
  jpeg: ["Recompressão JPEG local", [["qualidade", 1, 100, 1, 70]]],
  cinza: ["Tons de cinza", []],
  igualar_ruido: ["Igualar ruído ao papel ao redor", [["forca", 0, 2, 0.05, 1], ["semente", 0, 99999, 1, 0]]],
};

function armazenar(chave, valor) { try { localStorage.setItem("rotulador." + chave, JSON.stringify(valor)); } catch { /* sem storage */ } }
function lembrar(chave, padrao) {
  try { const v = localStorage.getItem("rotulador." + chave); return v === null ? padrao : JSON.parse(v); } catch { return padrao; }
}
function el(tag, props = {}, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "innerHTML" || k === "textContent" || (k in e && typeof v !== "string")) e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const f of filhos.flat()) if (f !== null && f !== undefined) e.append(f);
  return e;
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ===========================================================================
const app = {
  estado: null, split: lembrar("split", "treino"), docs: [], sessao: null,
  idFerramenta: "ret", ferramenta: null, F: null,
  selecao: { formas: [], inverter: false, expandir: 0, suavizar: 0 }, selInfo: null,
  corFrente: lembrar("corFrente", "#000000"), corFundo: lembrar("corFundo", "#ffffff"),
  categoria: "", descricao: "", ocupado: false, espaco: false, _opcoes: lembrar("opcoes", {}),
  clip: null,  // o que o Ctrl+C / Ctrl+X guardou: {doc, split, nome, selecao, bbox, pixels}
  apertado: false,  // botão do mouse apertado na tela (a barra de tarefas some durante o arraste)
  visor: new Visor($("visor"), $("tela")),
};
window.rotulador = app; // facilita depurar no console

// ------------------------------------------------------------------ avisos
app.aviso = (msg, tipo = "") => {
  const a = el("div", { class: "aviso " + tipo }, msg);
  $("avisos").append(a);
  setTimeout(() => a.remove(), tipo === "erro" ? 9000 : 5000);
};
app.status = (msg) => { $("st-msg").textContent = msg; };
let vigiaIA = null;
function ocupar(msg) {
  app.ocupado = !!msg;
  $("ocupado").hidden = !msg;
  if (msg) $("ocupado-msg").textContent = msg;
  clearTimeout(vigiaIA);
  $("ocupado-ia").textContent = "";
  $("ocupado-cancelar").hidden = true;
  if (msg) vigiaIA = setTimeout(acompanharIA, 2000);  // operação longa: pode ser a IA
  app.visor.redesenhar();  // a barra de tarefas some enquanto o servidor trabalha e volta depois
}
// enquanto a página espera, pergunta à IA em que pé está (etapa, passo da difusão, tempo que falta)
async function acompanharIA() {
  if (!app.ocupado) return;
  try {
    const p = await get("/api/ia/progresso");
    if (app.ocupado && p.ocupado) {
      const etapa = p.etapa === "difusão" && p.passos ? `difusão, passo ${p.passo} de ${p.passos}` : (p.etapa || "trabalhando");
      const resta = p.restante_s ? ` · faltam ~${p.restante_s < 90 ? `${Math.round(p.restante_s)} s` : `${Math.ceil(p.restante_s / 60)} min`}` : "";
      $("ocupado-ia").textContent = `IA: ${etapa}${p.segundos ? ` · ${Math.round(p.segundos)} s` : ""}${resta}${p.cancelando ? " · cancelando…" : ""}`;
      $("ocupado-cancelar").hidden = Boolean(p.cancelando);
    }
  } catch { /* IA desligada: nada a mostrar */ }
  if (app.ocupado) vigiaIA = setTimeout(acompanharIA, 1500);
}

// Fila de pedidos que mudam a imagem: cada gesto do usuário vira um pedido, aplicado EM ORDEM.
// Nenhum traço se perde enquanto o servidor ainda processa o anterior (documentos grandes levam 1–3 s).
app.fila = Promise.resolve();
app.pendentes = 0;
function indicarFila() {
  $("st-fila").textContent = app.pendentes ? `processando… (${app.pendentes})` : "";
  $("visor").classList.toggle("processando", app.pendentes > 0);
}
function enfileirar(tarefa) {
  app.pendentes++;
  indicarFila();
  const p = app.fila.then(tarefa).finally(() => { app.pendentes--; indicarFila(); });
  app.fila = p.catch(() => {});
  return p;
}

// ------------------------------------------------------------------ opções
app.opcoes = (id) => {
  const f = app.F[id];
  const o = (app._opcoes[id] ||= {});
  for (const c of f.campos || []) if (c.padrao !== undefined && o[c.chave] === undefined) o[c.chave] = c.padrao;
  return o;
};
app.salvarOpcoes = () => {
  const copia = JSON.parse(JSON.stringify(app._opcoes));
  for (const o of Object.values(copia)) delete o.texto; // texto digitado não fica salvo
  armazenar("opcoes", copia);
};
app.definirCorFrente = (c) => {
  app.corFrente = c; $("cor-frente").value = c; armazenar("corFrente", c);
  if (app.ferramenta?.mudouOpcao) app.ferramenta.mudouOpcao();
};

function campo(c, o, aoMudar) {
  const id = "op-" + c.chave;
  if (c.tipo === "botao") return el("button", { type: "button", title: c.titulo || "", onclick: c.acao }, c.rotulo);
  if (c.tipo === "cor") {
    if (!o[c.chave]) o[c.chave] = c.padrao || "#000000";
    return el("span", { class: "grupo" }, el("label", {}, c.rotulo),
      el("input", { type: "color", value: o[c.chave], oninput: (e) => { o[c.chave] = e.target.value; app.salvarOpcoes(); } }));
  }
  if (c.tipo === "corfrente") {
    return el("span", { class: "grupo" }, el("label", {}, c.rotulo),
      el("input", { type: "color", value: app.corFrente, oninput: (e) => app.definirCorFrente(e.target.value) }));
  }
  let entrada;
  const set = (v) => { o[c.chave] = v; app.salvarOpcoes(); aoMudar(); };
  if (c.tipo === "bool") {
    entrada = el("input", { type: "checkbox", id, checked: !!o[c.chave], onchange: (e) => set(e.target.checked) });
  } else if (c.tipo === "select" || c.tipo === "fonte") {
    const opcoes = c.tipo === "fonte" ? app.estado.fontes.map((f) => [f, f]) : c.opcoes;
    entrada = el("select", { id, onchange: (e) => set(c.tipo === "select" && typeof c.padrao === "number" ? Number(e.target.value) : e.target.value) },
      opcoes.map(([v, r]) => el("option", { value: String(v) }, r)));
    entrada.value = String(o[c.chave] || (c.tipo === "fonte" ? app.estado.fonte_padrao : c.padrao));
  } else if (c.tipo === "texto") {
    entrada = el("input", { type: "text", id, value: o[c.chave] ?? "", class: c.largo ? "largo" : "", "data-dado": "1",
      oninput: (e) => { o[c.chave] = e.target.value; aoMudar(); } });
  } else if (c.tipo === "faixa") {
    const num = el("input", { type: "number", min: c.min, max: c.max, step: c.passo, value: o[c.chave] });
    entrada = el("input", { type: "range", id, min: c.min, max: c.max, step: c.passo, value: o[c.chave],
      oninput: (e) => { num.value = e.target.value; set(Number(e.target.value)); } });
    num.addEventListener("change", (e) => { entrada.value = e.target.value; set(Number(e.target.value)); });
    return el("span", { class: "grupo" }, el("label", { for: id }, c.rotulo), entrada, num);
  } else {
    entrada = el("input", { type: "number", id, min: c.min, max: c.max, step: c.passo, value: o[c.chave],
      onchange: (e) => set(Number(e.target.value)) });
  }
  return el("span", { class: "grupo" }, el("label", { for: id }, c.rotulo), entrada);
}

// para que serve cada ferramenta, em uma frase (vai na dica do botão e no começo da barra de opções)
const PARA_QUE = {
  mao: "Arrasta a imagem para ver outra parte (ou segure Espaço com qualquer ferramenta).",
  zoom: "Aproxima onde você clica; com Alt, afasta. 0 ajusta à tela, 1 mostra em 100%.",
  ret: "Seleciona um retângulo. A seleção limita onde as outras ferramentas mexem.",
  elipse: "Seleciona uma elipse (um carimbo redondo, um logotipo).",
  laco: "Seleciona desenhando o contorno à mão livre, ponto a ponto.",
  varinha: "Seleciona os pixels de cor parecida com a do clique (o fundo de um campo, uma faixa colorida).",
  pincelsel: "Pinta a seleção com um pincel: bom para pegar só as letras de uma palavra.",
  pincel: "Pinta com a cor de frente, com borda suave. Para retocar traços de tinta.",
  borracha: "Apaga a edição da camada ativa e devolve o documento original naquele ponto.",
  balde: "Preenche a área de cor parecida com a cor de frente (como o balde do Paint: deixa mancha lisa).",
  contagotas: "Pega a cor de um ponto do documento para usar como cor de frente (a cor da tinta, do papel).",
  forma: "Desenha retângulo, elipse ou linha (tarja, sublinhado, moldura).",
  carimbo: "Copia uma parte do documento para outra (Alt+clique escolhe a origem): apaga texto com o papel de verdade.",
  recuperacao: "Como o carimbo, mas casa a cor e a luz do lugar de destino: some com manchas e letras sem emenda.",
  corretivo: "Pincel corretivo: o que você pinta some, reconstruído a partir do papel em volta (o pincel de correção de manchas do Photoshop).",
  remendo: "Selecione o que quer cobrir e arraste até um pedaço de papel limpo: ele vira o remendo, com a luz casada.",
  retoque: "Pincel de retoque: desfoca, aguça, clareia ou escurece só onde passa (acabamento da edição).",
  texto: "Escreve um texto novo onde você clicar, na fonte, tamanho e cor escolhidos (🔍 descobre a fonte do documento).",
  substituir: "Arraste sobre o texto antigo, digite o novo e Enter: o antigo é apagado e o novo sai na fonte, cor e posição estimadas.",
  transformar: "Arraste a seleção e os pixels vão junto; o buraco é preenchido com o papel em volta (Alt = duplicar). Enter aplica.",
  colar: "Cola um trecho de outro documento do MESMO split: copie lá (Ctrl+C) e cole aqui (Ctrl+V), ou escolha o trecho.",
};

app.renderOpcoes = () => {
  const box = $("opcoes-ferramenta");
  box.replaceChildren();
  const f = app.ferramenta;
  if (!f) return;
  if (PARA_QUE[app.idFerramenta]) box.append(el("span", { class: "para-que", title: PARA_QUE[app.idFerramenta] }, "ℹ " + PARA_QUE[app.idFerramenta]));
  const o = app.opcoes(app.idFerramenta);
  const aoMudar = debounce(() => { if (f.mudouOpcao) f.mudouOpcao(); app.visor.redesenhar(); }, 120);
  for (const c of f.campos || []) box.append(campo(c, o, aoMudar));
  if (f.botoes?.length) box.append(el("span", { class: "sep" }));
  for (const b of f.botoes || []) {
    if (b.campoSel) {
      box.append(el("span", { class: "grupo" }, el("label", {}, b.rotulo),
        el("input", { type: "number", min: b.campoSel === "suavizar" ? 0 : -200, max: 200, step: 1,
          value: app.selecao[b.campoSel] || 0,
          onchange: (e) => { app.selecao[b.campoSel] = Number(e.target.value) || 0; app.atualizarSelecao(); } })));
    } else {
      box.append(el("button", { type: "button", class: b.primario ? "primario" : "", title: b.titulo || "", onclick: b.acao }, b.rotulo));
    }
  }
};

// ------------------------------------------------------------------ ferramentas
// caixa de ferramentas em grupos (como no Photoshop): cada botão mostra a última ferramenta usada do grupo; o
// triângulo, o clique direito ou segurar o botão abrem as outras do grupo
const textoAtalho = (f) => (!f.atalho ? "" : f.atalho === f.atalho.toUpperCase() && f.atalho !== f.atalho.toLowerCase()
  ? `Shift+${f.atalho}` : f.atalho.toUpperCase());
const dicaFerramenta = (id) => `${NOMES[id]}${textoAtalho(app.F[id]) ? ` (${textoAtalho(app.F[id])})` : ""}${PARA_QUE[id] ? ` — ${PARA_QUE[id]}` : ""}`;
let gaveta = null;
function fecharGaveta() { if (gaveta) { gaveta.remove(); gaveta = null; } }
function abrirGaveta(botao, grupo) {
  fecharGaveta();
  const r = botao.getBoundingClientRect();
  gaveta = el("div", { class: "gaveta", role: "menu", style: `left:${r.right + 4}px;top:${r.top}px` },
    ...grupo.map((id) => el("button", { type: "button", role: "menuitem", class: id === app.idFerramenta ? "ativa" : "",
      title: PARA_QUE[id] || "", onclick: () => { fecharGaveta(); app.usar(id); } },
    icone(ICONES[id], 18), el("span", { class: "g-nome" }, NOMES[id]), el("span", { class: "g-atalho" }, textoAtalho(app.F[id])))));
  document.body.append(gaveta);
  const alto = gaveta.getBoundingClientRect();
  if (alto.bottom > innerHeight - 8) gaveta.style.top = `${Math.max(8, innerHeight - alto.height - 8)}px`;
}
function montarFerramentas() {
  const box = $("ferramentas");
  app.ultimaDoGrupo = lembrar("ultimaDoGrupo", {});
  for (const g of GRUPOS) {
    if (g === "|") { box.append(el("hr")); continue; }
    const chave = g[0];
    const id = g.includes(app.ultimaDoGrupo[chave]) ? app.ultimaDoGrupo[chave] : g[0];
    let segurar = null;
    const b = el("button", { type: "button", class: `ferr${g.length > 1 ? " grupo" : ""}`, "data-f": id, "data-g": chave,
      title: dicaFerramenta(id), "aria-label": NOMES[id],
      onclick: () => { if (b._gavetaAberta) { b._gavetaAberta = false; return; } app.usar(b.dataset.f); },
      oncontextmenu: (e) => { e.preventDefault(); if (g.length > 1) abrirGaveta(b, g); },
      onpointerdown: () => { if (g.length > 1) segurar = setTimeout(() => { b._gavetaAberta = true; abrirGaveta(b, g); }, 380); },
      onpointerup: () => clearTimeout(segurar), onpointerleave: () => clearTimeout(segurar) },
    icone(ICONES[id], 20));
    box.append(b);
  }
  const cores = el("div", { class: "cores", title: "Cor de frente / fundo (X troca, D volta ao preto e branco)" },
    el("input", { type: "color", id: "cor-frente", value: app.corFrente, "aria-label": "cor de frente", oninput: (e) => app.definirCorFrente(e.target.value) }),
    el("input", { type: "color", id: "cor-fundo", value: app.corFundo, "aria-label": "cor de fundo", oninput: (e) => { app.corFundo = e.target.value; armazenar("corFundo", app.corFundo); } }));
  box.append(el("div", { class: "estica" }), cores);
  document.addEventListener("pointerdown", (e) => { if (gaveta && !gaveta.contains(e.target) && !e.target.closest?.("#ferramentas")) fecharGaveta(); }, true);
  document.addEventListener("keydown", (e) => { if (gaveta && e.key === "Escape") fecharGaveta(); });
}

// troca de ferramenta: o que estava solto (trecho puxado ou colado) é aplicado antes, como no Photoshop
app.usar = (id) => {
  if (!app.F[id]) return;
  fecharGaveta();
  if (id === app.idFerramenta && app.ferramenta) { app.renderOpcoes(); return; }
  if (app.ferramenta?.pendente?.()) app.ferramenta.confirmar();
  if (app.ferramenta?.desativar) app.ferramenta.desativar();
  app.limparPrevia();
  app.idFerramenta = id;
  app.ferramenta = app.F[id];
  for (const b of document.querySelectorAll("#ferramentas button[data-g]")) {
    const g = GRUPOS.find((x) => x !== "|" && x[0] === b.dataset.g);
    if (g.includes(id)) {
      b.dataset.f = id; b.title = dicaFerramenta(id); b.setAttribute("aria-label", NOMES[id]);
      b.replaceChildren(icone(ICONES[id], 20));
      app.ultimaDoGrupo[g[0]] = id; armazenar("ultimaDoGrupo", app.ultimaDoGrupo);
    }
    b.classList.toggle("ativa", g.includes(id));
  }
  $("ferr-atual").replaceChildren(icone(ICONES[id], 16), el("b", {}, NOMES[id]));
  app.renderOpcoes();
  $("tela").style.cursor = app.ferramenta.cursor || "default";
  if (app.ferramenta.ativar && app.sessao) app.ferramenta.ativar("usuario");
  app.visor.redesenhar();
};
// o que está solto é aplicado (antes de salvar, trocar de imagem, desmarcar…)
app.confirmarSolto = async () => (app.ferramenta?.pendente?.() ? app.ferramenta.confirmar() : true);

// ------------------------------------------------------------------ categorias
// só as 3 do editor (copiar e colar, pegar de outro documento, apagar e substituir): a pessoa escolhe antes de editar,
// e a escolhida fica lembrada para a próxima imagem
const categorias = () => app.estado.categorias_editor;
function montarCategorias() {
  $("lista-categorias").replaceChildren(...categorias().map((c, i) =>
    el("li", { "data-c": c.chave, role: "radio", tabindex: "0", title: `${c.dica} (Alt+${i + 1})`,
      onclick: () => escolherCategoria(c.chave),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); escolherCategoria(c.chave); } } },
    el("span", { class: "chip", style: `background:${corCategoria(c.chave)}` }),
    el("span", { class: "rot-cat" }, c.rotulo, el("small", {}, c.dica)),
    el("span", { class: "marca-cat" }))));
}
// escolha do USUÁRIO: a camada ativa passa a ser a dessa categoria (ou uma nova é criada na 1ª edição)
function escolherCategoria(chave) {
  definirCategoria(chave);
  const cams = app.sessao?.camadas || [];
  const at = cams.find((c) => c.ativa);
  if (!chave || (at && at.categoria === chave)) { app._catPendente = false; return; }
  if (at && at.vazia) { app.acaoCamada("props", { camada: at.id, categoria: chave }); app._catPendente = false; return; }
  const existente = cams.find((c) => c.categoria === chave);  // lista vem de cima para baixo
  if (existente) { app.acaoCamada("ativar", { camada: existente.id }); app._catPendente = false; return; }
  app._catPendente = true;
  app.status(`a próxima edição cria a camada '${chave}'`);
}
function definirCategoria(chave) {
  if (chave && !categorias().some((c) => c.chave === chave)) chave = "";  // camada antiga, de uma classe de campo
  app.categoria = chave;
  if (chave) armazenar("categoria", chave);
  $("sec-tipo").classList.remove("faltando");
  document.querySelectorAll("#lista-categorias li").forEach((li) => {
    li.classList.toggle("ativa", li.dataset.c === chave);
    li.setAttribute("aria-checked", String(li.dataset.c === chave));
  });
  document.querySelectorAll(".sel-cat-modal").forEach((s) => { s.value = chave; });
}
function exigirCategoria() {
  if (app.categoria) return true;
  $("sec-tipo").classList.add("faltando");
  $("lista-categorias").querySelector("li")?.focus();
  app.aviso("Escolha o TIPO da edição (copiar e colar, pegar de outro documento ou apagar e substituir) antes de editar.", "erro");
  return false;
}
// a ação já diz o tipo em dois casos sem dúvida: colar de OUTRA imagem é "pegar de outro documento", e copiar/puxar
// dentro da mesma imagem não é. Escolhido o tipo errado, ele é corrigido (com aviso); sem tipo, vale a regra de
// sempre (o aplicar pede que a pessoa escolha)
app.escolherCategoria = (chave) => escolherCategoria(chave);
app.ajustarCategoria = (implicita) => {
  const atual = app.categoria;
  if (!atual || atual === implicita) return;
  const errado = implicita === "pegar de outro documento" || atual === "pegar de outro documento";
  if (!errado) return;
  escolherCategoria(implicita);
  const rot = categorias().find((c) => c.chave === implicita)?.rotulo || implicita;
  app.aviso(implicita === "pegar de outro documento"
    ? `Tipo da edição: '${rot}' (o trecho veio de outra imagem).`
    : `Tipo da edição: '${rot}' (o trecho é desta mesma imagem).`, "ok");
};
function seletorCategoria() {
  const s = el("select", { class: "sel-cat-modal", onchange: (e) => escolherCategoria(e.target.value) },
    el("option", { value: "" }, "— escolha —"), ...categorias().map((c) => el("option", { value: c.chave }, c.rotulo)));
  s.value = app.categoria;
  return el("div", { class: "linha" }, el("label", {}, "Tipo da edição"), s);
}

// ------------------------------------------------------------------ documentos
function montarAbas() {
  $("abas-split").replaceChildren(...SPLITS.map(([sp, nome]) =>
    el("button", { type: "button", "data-sp": sp, onclick: () => trocarSplit(sp) }, nome, el("span", { class: "pilula", "data-cont": sp }))));
  atualizarAbas();
}
function atualizarAbas() {
  for (const [sp] of SPLITS) {
    const info = app.estado.splits[sp];
    document.querySelector(`[data-cont="${sp}"]`).textContent = `${info.feitos}/${info.total}`;
    document.querySelector(`[data-sp="${sp}"]`).classList.toggle("ativa", sp === app.split);
  }
}
async function trocarSplit(sp) {
  app.split = sp; armazenar("split", sp); atualizarAbas();
  await carregarDocs();
  const ultimo = lembrar("ultimo." + sp, null);
  const alvo = app.docs.find((d) => d.id === ultimo) || app.docs.find((d) => !d.versoes) || app.docs[0];
  if (alvo) await abrirDoc(alvo.id); else limparDoc();
}
async function carregarDocs() {
  app.docs = await get(`/api/documentos?split=${app.split}`);
  desenharPosicao();
}
// sem a lista de documentos: o topo diz em qual imagem a pessoa está (◀ ▶ e PageUp/PageDown passam de uma para outra)
function desenharPosicao() {
  const s = app.sessao;
  const i = app.docs.findIndex((d) => d.id === s?.id);
  const pend = app.docs.filter((d) => !d.versoes).length;
  $("doc-versoes").textContent = s ? `${i + 1} de ${app.docs.length} · ${s.versoes} salva(s)` : "";
  $("doc-versoes").title = s ? `${pend} imagem(ns) deste split ainda sem versão salva` : "";
}
function limparDoc() {
  app.sessao = null; app.visor.img = null; app.visor.redesenhar();
  $("vazio").hidden = false; $("doc-nome").textContent = "nenhum documento neste split";
  atualizarSessao(null);
}

async function abrirDoc(id, { forcar = false } = {}) {
  if (app.ocupado) return;
  if (app.sessao && app.sessao.id !== id) await app.confirmarSolto();  // o trecho solto fica nesta imagem
  await app.fila;  // edições ainda na fila terminam no documento certo
  try {
    ocupar("abrindo…");
    if (app.ferramenta?.desativar) app.ferramenta.desativar();
    const anterior = app.sessao?.id;
    const s = await post("/api/abrir", { id, forcar });
    if (anterior && anterior !== id && !app.somenteLeitura) post("/api/trava/soltar", { id: anterior }).catch(() => {});
    mostrarTrava(s.travado_por);
    const bmp = await bitmapUrl(`/api/img/${id}/atual.png?t=${Date.now()}`);
    app.visor.definirImagem(bmp);
    app.selecao = { formas: [], inverter: false, expandir: 0, suavizar: 0 }; app.selInfo = null;
    $("st-sel").textContent = "";
    $("vazio").hidden = true;
    armazenar("ultimo." + s.split, id);
    atualizarSessao(s);
    if (app.ferramenta?.ativar) app.ferramenta.ativar("documento");
    app.renderOpcoes();
    prelerProximos();
  } catch (e) { app.aviso(e.message, "erro"); } finally { ocupar(null); }
}
// o OCR dos próximos documentos roda em segundo plano: quando a pessoa chegar neles, os campos já estão lidos
function prelerProximos() {
  const i = app.docs.findIndex((d) => d.id === app.sessao?.id);
  const ids = app.docs.slice(i + 1, i + 3).map((d) => d.id);
  if (ids.length) post("/api/campos/preler", { ids }).catch(() => {});
}

// ------------------------------------------------------------------ trava: um documento, uma pessoa por vez
app.somenteLeitura = false;
function mostrarTrava(t) {
  app.somenteLeitura = Boolean(t);
  app.trava = t || null;
  $("banner-trava").hidden = !t;
  $("visor").classList.toggle("so-olhando", Boolean(t));
  if (t) {
    const desde = new Date(t.desde * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    $("banner-trava-txt").textContent = `🔒 ${t.operador} está editando este documento desde ${desde}. Você está só olhando.`;
  }
}
function podeEditar() {
  if (!app.somenteLeitura) return true;
  app.aviso(`${app.trava?.operador || "Outra pessoa"} está com este documento: use "Assumir edição" para editar.`, "erro");
  return false;
}
function assumir() {
  const t = app.trava;
  if (!app.sessao || !t) return;
  if (!confirm(`Assumir o documento? ${t.operador} deixa de poder editá-lo (o que já foi feito continua no rascunho).`)) return;
  abrirDoc(app.sessao.id, { forcar: true });
}
// sinal de vida: a trava vence em 10 min sem sinal. Quem só olha fica sabendo quando o documento ficar livre
async function sinalDeVida() {
  if (!app.sessao) return;
  try {
    const r = await post("/api/trava/ping", { id: app.sessao.id });
    if (app.somenteLeitura && r.ok) { app.aviso("O documento ficou livre: agora você pode editar.", "ok"); abrirDoc(app.sessao.id); }
    else if (!app.somenteLeitura && !r.ok) mostrarTrava(r.travado_por);
    app.docs = await get(`/api/documentos?split=${app.split}`);  // quem está editando o quê
    desenharPosicao();
  } catch { /* servidor ocupado: tenta no próximo */ }
}
function proximoPendente() {
  if (!app.docs.length) return;
  const i = app.docs.findIndex((d) => d.id === app.sessao?.id);
  const ordem = [...app.docs.slice(i + 1), ...app.docs.slice(0, i + 1)];
  const alvo = ordem.find((d) => !d.versoes) || app.docs[(i + 1) % app.docs.length];
  if (alvo) abrirDoc(alvo.id);
}
function navegar(passo) {
  if (!app.docs.length) return;
  const i = app.docs.findIndex((d) => d.id === app.sessao?.id);
  const alvo = app.docs[(i + passo + app.docs.length) % app.docs.length];
  if (alvo) abrirDoc(alvo.id);
}

// o passo no Histórico com o nome que a pessoa conhece (o servidor guarda o nome técnico da operação)
const PASSOS = {
  preencher: "Apagar com preenchimento", pincel: "Pincel", borracha: "Borracha", corretivo: "Pincel corretivo",
  carimbo: "Carimbo", recuperacao: "Pincel de recuperação", remendo: "Remendo", retoque: "Retoque", balde: "Balde de tinta",
  forma: "Forma", texto: "Texto", substituir_texto: "Substituir texto", ajuste: "Ajuste", codigo_barras: "Código de barras",
  qrcode: "QR code", ia: "Edição com IA",
};
function nomeDoPasso(h, s) {
  if (h.rotulo === "transformar") return h.params?.modo === "duplicar" ? "Duplicar" : "Puxar";
  if (h.rotulo === "colar") return h.params?.doador === s?.id ? "Colar (desta imagem)" : "Colar de outro documento";
  return PASSOS[h.rotulo] || h.rotulo;
}
function atualizarSessao(s) {
  app.sessao = s;
  const ed = new Set(s?.categorias || []);
  document.querySelectorAll("#lista-categorias li").forEach((li) => {
    li.classList.toggle("editada", ed.has(li.dataset.c));
    li.querySelector(".marca-cat").textContent = ed.has(li.dataset.c) ? "✓" : "";
  });
  $("doc-nome").textContent = s ? s.nome : "nenhum documento";
  $("doc-nome").title = s ? `${s.split}/${s.nome} — ${s.largura}x${s.altura}` : "";
  $("btn-salvar").disabled = !s?.pode_salvar;
  $("btn-salvar").title = s?.pode_salvar ? "Salvar original + editada + máscaras" : (s?.motivo || "");
  $("motivo-salvar").textContent = s && !s.pode_salvar ? `Salvar bloqueado: ${s.motivo}.` : (s ? "Pronto para salvar." : "");
  $("btn-desfazer").disabled = !s?.historico?.length;
  $("btn-refazer").disabled = !s?.pode_refazer;
  $("historico").replaceChildren(...(s?.historico || []).map((h) =>
    el("li", { class: h.restaura ? "restaura" : "", title: JSON.stringify(h.params || {}).slice(0, 400) },
      `${nomeDoPasso(h, s)} `, h.nome_camada ? el("span", { class: "cat" }, `· ${h.nome_camada}`) : null,
      h.pixels_alterados ? ` · ${h.pixels_alterados} px` : "")));
  desenharCamadas(s);
  const at = s?.camadas?.find((c) => c.ativa);
  if (at && !app._catPendente && at.categoria !== app.categoria) definirCategoria(at.categoria);
  const d = app.docs.find((x) => x.id === s?.id);
  if (d && s) { d.rascunho = s.historico.length > 0; d.versoes = s.versoes; }
  desenharPosicao();
  if (app.visor.mostrarMascara) carregarMascara();
}

// ------------------------------------------------------------------ camadas
const PALETA = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6",
  "#bfef45", "#fabed4", "#469990", "#dcbeff", "#9a6324", "#fffac8", "#800000", "#aaffc3"];
function corCategoria(chave) {
  const i = categorias().findIndex((x) => x.chave === chave);
  return i >= 0 ? PALETA[i % PALETA.length] : "#666";
}
app.camadaAtiva = () => app.sessao?.camadas?.find((c) => c.ativa) || null;
app.acaoCamada = (acao, extra = {}) => (app._acaoPendente = executarAcaoCamada(acao, extra));
function executarAcaoCamada(acao, extra) {
  if (!app.sessao || app.ocupado || !podeEditar()) return Promise.resolve(false);
  const doc = app.sessao.id;
  return enfileirar(async () => {
    try {
      const r = await post("/api/camada", { id: doc, acao, ...extra });
      if (app.sessao?.id !== doc) return true;
      if (!r.nada) app.visor.aplicarRecorte(await bitmap(r.png), r.bbox[0], r.bbox[1]);
      app.limparPrevia();
      atualizarSessao(r.estado);
      return true;
    } catch (e) { app.aviso(e.message, "erro"); return false; }
  });
}
function ativarCamada(c) {
  app._catPendente = false;
  definirCategoria(c.categoria);
  if (!c.ativa) app.acaoCamada("ativar", { camada: c.id });
}
function renomearCamada(span, c) {
  const inp = el("input", { type: "text", value: c.nome });
  let feito = false;
  const fim = (salvar) => {
    if (feito) return; feito = true;
    if (salvar && inp.value.trim() && inp.value.trim() !== c.nome) app.acaoCamada("props", { camada: c.id, nome: inp.value.trim() });
    else desenharCamadas(app.sessao);
  };
  inp.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") fim(true); if (e.key === "Escape") fim(false); });
  inp.addEventListener("blur", () => fim(true));
  inp.addEventListener("click", (e) => e.stopPropagation());
  span.replaceChildren(inp); inp.focus(); inp.select();
}
function moverCamadaNaPilha(passo) {
  const cams = app.sessao?.camadas || [];
  const i = cams.findIndex((c) => c.ativa);
  if (i < 0) return;
  const posicao = cams.length - 1 - i + passo;  // servidor: índice de baixo para cima
  if (posicao < 0 || posicao >= cams.length) return;
  app.acaoCamada("ordem", { camada: cams[i].id, posicao });
}
function novaCamada() {
  if (!app.sessao) return;
  if (!exigirCategoria()) return;
  app._catPendente = false;
  app.acaoCamada("criar", { categoria: app.categoria, descricao: app.descricao });
}
let arrastoCamada = null;
function desenharCamadas(s) {
  const ul = $("lista-camadas");
  const cams = s?.camadas || [];
  const linhas = cams.map((c) => {
    const sel = el("select", { title: "Tipo da edição desta camada (é o que vai para a máscara)", onclick: (e) => e.stopPropagation(),
      onchange: (e) => app.acaoCamada("props", { camada: c.id, categoria: e.target.value }) },
      categorias().some((k) => k.chave === c.categoria) ? null : el("option", { value: c.categoria || "" }, c.categoria || "—"),
      categorias().map((k) => el("option", { value: k.chave }, k.rotulo)));
    sel.value = c.categoria || "";
    const nome = el("span", { class: "nome-cam", title: "duplo clique para renomear" }, c.nome);
    const li = el("li", { class: `${c.ativa ? "ativa" : ""} ${c.visivel ? "" : "oculta"}`, draggable: true, "data-cam": c.id,
      title: c.vazia ? "camada vazia" : `${c.pixels} px nesta camada`, onclick: () => ativarCamada(c) },
      el("span", { class: "chip", style: `background:${corCategoria(c.categoria)}` }),
      el("button", { type: "button", class: "olho", title: c.visivel ? "Ocultar (camada oculta não é salva)" : "Mostrar",
        "aria-label": c.visivel ? "ocultar a camada" : "mostrar a camada",
        onclick: (e) => { e.stopPropagation(); app.acaoCamada("props", { camada: c.id, visivel: !c.visivel }); } },
      icone(c.visivel ? "olho" : "olho_fechado", 15)),
      el("span", { class: "mini" }, c.vazia ? null : el("img", { src: `/api/camada_mini/${s.id}/${c.id}.png?v=${c.versao}`, alt: "" })),
      el("span", { class: "info" }, nome, sel));
    li.addEventListener("dragstart", (e) => { arrastoCamada = c.id; li.classList.add("arrastando"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", c.id); });
    li.addEventListener("dragend", () => { arrastoCamada = null; li.classList.remove("arrastando"); });
    const acima = (e) => { const r = li.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; };
    li.addEventListener("dragover", (e) => {
      if (!arrastoCamada || arrastoCamada === c.id) return;
      e.preventDefault(); const a = acima(e);
      li.classList.toggle("alvo-acima", a); li.classList.toggle("alvo-abaixo", !a);
    });
    li.addEventListener("dragleave", () => li.classList.remove("alvo-acima", "alvo-abaixo"));
    li.addEventListener("drop", (e) => {
      e.preventDefault(); li.classList.remove("alvo-acima", "alvo-abaixo");
      if (!arrastoCamada || arrastoCamada === c.id) return;
      const ids = cams.map((x) => x.id).filter((x) => x !== arrastoCamada);  // de cima para baixo
      const j = ids.indexOf(c.id) + (acima(e) ? 0 : 1);
      app.acaoCamada("ordem", { camada: arrastoCamada, posicao: ids.length - j });
    });
    return li;
  });
  if (s) {
    linhas.push(el("li", { class: "fundo", title: "Imagem original: não é editável e não muda" },
      el("span", { class: "olho" }, icone("cadeado", 14)),
      el("span", { class: "mini" }, el("img", { src: `/api/miniatura/${s.id}.jpg`, alt: "" })),
      el("span", { class: "info" }, el("span", { class: "nome-cam" }, "Fundo (original)"))));
  }
  ul.replaceChildren(...linhas);
  const at = cams.find((c) => c.ativa);
  $("cam-opacidade").hidden = !at;
  if (at) { $("cam-opac").value = Math.round(at.opacidade * 100); $("cam-opac-val").textContent = `${Math.round(at.opacidade * 100)}%`; }
  ["cam-dup", "cam-mesclar", "cam-excluir"].forEach((id) => { $(id).disabled = !at; });
  $("cam-dica").textContent = !s ? "" : !cams.length
    ? "Escolha o tipo da edição e edite: cada tipo ganha a sua camada automaticamente."
    : (s.ocultas?.length ? `⚠ Ocultas NÃO entram no salvamento: ${s.ocultas.join(", ")}`
      : "Arraste para reordenar · duplo clique renomeia · o olho oculta");
}

// ------------------------------------------------------------------ seleção
app.temSelecao = () => !!app.selInfo;
app.selBbox = () => app.selInfo?.bbox || null;
app.specSelecao = () => (app.selecao.formas.length ? app.selecao : null);
app.adicionarForma = async (forma, modo) => {
  forma.modo = modo;
  if (modo === "novo") { app.selecao.formas = [forma]; app.selecao.inverter = false; }
  else app.selecao.formas.push(forma);
  await app.atualizarSelecao();
};
app.atualizarSelecao = async () => {
  if (!app.sessao) return;
  if (!app.selecao.formas.length) { app.selInfo = null; app.visor.definirSelecao(null); $("st-sel").textContent = ""; }
  else {
    try {
      const r = await post("/api/selecao", { id: app.sessao.id, selecao: app.selecao });
      if (r.vazia) { app.selInfo = null; app.visor.definirSelecao(null); $("st-sel").textContent = "seleção vazia"; }
      else {
        app.selInfo = r;
        app.visor.definirSelecao(await bitmap(r.png), r.bbox[0], r.bbox[1]);
        const [x0, y0, x1, y1] = r.bbox;
        $("st-sel").textContent = `seleção ${x1 - x0}x${y1 - y0} (${r.pixels} px)`;
      }
    } catch (e) { app.aviso(e.message, "erro"); }
  }
  if (app.ferramenta?.aoMudarSelecao) app.ferramenta.aoMudarSelecao();
};
function limparSelecao() { app.selecao = { formas: [], inverter: false, expandir: 0, suavizar: 0 }; app.atualizarSelecao(); app.renderOpcoes(); }
// desmarcar com um trecho solto aplica o trecho antes (como o Ctrl+D do Photoshop)
app.desmarcar = () => {
  if (app.ferramenta?.pendente?.()) {
    app.ferramenta.confirmar().then((ok) => { if (ok && app.temSelecao()) limparSelecao(); });
    return;
  }
  limparSelecao();
};
app.dentroDaSelecao = (p) => {
  const bb = app.selBbox(), s = app.visor.selecao;
  if (!bb || p.x < bb[0] || p.y < bb[1] || p.x >= bb[2] || p.y >= bb[3]) return false;
  if (!s?.alfa) return true;
  try {  // pixel da máscara: dentro do laço, não só da caixa
    const g = s.alfa.getContext("2d", { willReadFrequently: true });
    return g.getImageData(Math.floor(p.x - s.x0), Math.floor(p.y - s.y0), 1, 1).data[3] > 0;
  } catch { return true; }
};
app.selecionarTudo = () => {
  if (!app.sessao) return;
  app.selecao = { formas: [{ tipo: "ret", x0: 0, y0: 0, x1: app.sessao.largura, y1: app.sessao.altura, modo: "novo" }], inverter: false, expandir: 0, suavizar: 0 };
  app.atualizarSelecao();
};
app.soTinta = () => {
  const bb = app.selBbox();
  if (!bb) { app.aviso("faça antes uma seleção em volta do texto", "erro"); return; }
  app.adicionarForma({ tipo: "tinta", x0: bb[0], y0: bb[1], x1: bb[2], y1: bb[3], folga: 1 }, "intersectar");
};
app.carregarOriginal = async () => {
  if (app.visor.orig || !app.sessao) return;
  try { app.visor.orig = await bitmapUrl(`/api/img/${app.sessao.id}/original.png`); } catch { /* só prévia */ }
};
app.inverterSelecao = () => { if (app.selecao.formas.length) { app.selecao.inverter = !app.selecao.inverter; app.atualizarSelecao(); } };

// ------------------------------------------------------------------ copiar, recortar, colar, puxar, apagar
// Ctrl+C guarda a seleção (e os pixels, para a prévia); Ctrl+V cola nesta imagem (duplicando, um pouco ao lado) ou em
// outra imagem do MESMO split (o trecho vem do original dela, só no formato copiado). Nunca entre splits.
const NOME_SPLIT = Object.fromEntries(SPLITS);
function exigirSelecao(acao) {
  if (app.temSelecao()) return true;
  app.aviso(`Selecione antes o trecho a ${acao} (M = retângulo, L = laço, W = varinha).`, "erro");
  return false;
}
function semNadaSolto() {
  if (!app.ferramenta?.pendente?.()) return true;
  app.aviso("Há um trecho solto: aplique (Enter) ou cancele (Esc) antes.", "erro");
  return false;
}
function guardarClip(recorte = false) {
  const s = app.sessao;
  app.clip = { doc: s.id, split: s.split, nome: s.nome, selecao: JSON.parse(JSON.stringify(app.selecao)),
    bbox: [...app.selBbox()], pixels: app.visor.recortarSelecao(), recorte };
}
app.copiar = () => {
  if (!app.sessao || !exigirSelecao("copiar") || !semNadaSolto()) return;
  guardarClip();
  const [x0, y0, x1, y1] = app.clip.bbox;
  app.aviso(`Copiado (${x1 - x0}×${y1 - y0} px). Ctrl+V cola nesta imagem ou em outra do split ${NOME_SPLIT[app.clip.split]}.`, "ok");
  app.visor.redesenhar();
};
app.recortar = () => {
  if (!app.sessao || !exigirSelecao("recortar") || !semNadaSolto() || !podeEditar()) return;
  guardarClip(true);
  app.usar("transformar");
  app.F.transformar.soltar({ recorte: true, modo: "mover" });
  app.status("recortado: arraste o trecho para onde quer · Enter aplica · Esc cancela");
};
app.puxar = () => {
  if (!app.sessao || !exigirSelecao("puxar") || !semNadaSolto()) return;
  app.usar("transformar");
  app.F.transformar.soltar({ modo: "mover" });
  app.status("arraste a seleção: os pixels vão junto e o buraco é preenchido (Alt = duplicar)");
};
app.colar = async () => {
  if (!app.sessao || !podeEditar() || !semNadaSolto()) return;
  const c = app.clip;
  if (!c) { app.aviso("Nada copiado ainda: selecione um trecho e use Ctrl+C (ou Editar › Colar de outro documento…).", "erro"); return; }
  if (c.split !== app.sessao.split) {
    app.aviso(`O trecho copiado é de uma imagem de ${NOME_SPLIT[c.split]} e esta é de ${NOME_SPLIT[app.sessao.split]}: colar `
      + "misturaria os splits (vazamento de dados). Copie de uma imagem deste split.", "erro");
    return;
  }
  if (c.doc === app.sessao.id && !c.recorte) {  // mesma imagem: os pixels de agora, duplicados um pouco ao lado
    app.selecao = JSON.parse(JSON.stringify(c.selecao));
    await app.atualizarSelecao();
    if (!app.temSelecao()) return;
    app.usar("transformar");
    app.F.transformar.soltar({ modo: "duplicar", deslocar: Math.max(4, Math.round(16 / app.visor.escala)) });
    app.status("colado: arraste para o lugar · Enter aplica · Esc cancela");
    return;
  }
  const [x0, y0, x1, y1] = c.bbox;
  app.F.colar.iniciar(c.doc, { x0, y0, x1, y1 }, { selecao: c.selecao, pre: c.pixels, emLugar: c.doc !== app.sessao.id });
  app.usar("colar");
  app.status(`colado de ${c.nome}: arraste para o lugar · Enter aplica · Esc cancela`);
};
// apagar com preenchimento: o selecionado some, refeito com o papel em volta (sem diálogo; Shift+F5 abre as opções)
app.apagarComPreenchimento = async () => {
  if (!app.sessao || !exigirSelecao("apagar")) return false;
  const p = { ...lembrar("preencher", { amostragem: "auto", margem: 150, patch: 7 }), semente: Math.floor(Math.random() * 1e6) };
  const ok = await app.aplicar("preencher", p, { msg: "apagando com preenchimento…" });
  if (ok) app.status("apagado com preenchimento · o dado na barra refaz com outra variação");
  return ok;
};
app.podeVariar = () => Boolean(app.temSelecao() && app.sessao?.historico?.at(-1)?.tipo === "preencher" && !app.sessao?.pode_refazer);
app.outraVariacao = async () => {
  if (!app.podeVariar()) return;
  const tipo = app.categoria;  // o desfazer marca o tipo da camada que fica ativa; a variação volta para o de antes
  await app.desfazer();
  if (tipo && app.categoria !== tipo) escolherCategoria(tipo);
  await app.apagarComPreenchimento();
};

// ------------------------------------------------------------------ operações
let seqPrevia = 0;
const enviarPrevia = debounce(async (tipo, params, seq) => {
  try {
    const r = await post("/api/op", { id: app.sessao.id, tipo, params, selecao: app.specSelecao(), previa: true });
    if (seq !== seqPrevia) return;
    app.visor.definirPrevia(await bitmap(r.png), r.bbox[0], r.bbox[1]);
    app.status("prévia");
    erroNoDialogo("");
  } catch (e) {
    if (seq !== seqPrevia) return;
    app.visor.definirPrevia(null);
    app.status(e.message);
    if (dlg.open) erroNoDialogo(`⚠ ${e.message}`);  // erro da prévia visível no diálogo
  }
}, 150);
app.previa = (tipo, params) => { if (!app.sessao) return; seqPrevia++; enviarPrevia(tipo, params, seqPrevia); };
app.limparPrevia = () => { seqPrevia++; app.visor.definirPrevia(null); };

app.aplicar = (tipo, params, { semCategoria = false, msg = null } = {}) => {
  if (!app.sessao || app.ocupado || !podeEditar()) return Promise.resolve(false);
  if (!semCategoria && !exigirCategoria()) return Promise.resolve(false);
  // tudo é capturado AGORA: o usuário pode mudar seleção/categoria enquanto o pedido espera na fila
  const at = app.camadaAtiva();
  const camada = semCategoria ? at?.id : (at && at.categoria === app.categoria ? at.id : null);
  if (semCategoria && !camada) { app.aviso("não há camada para apagar", "erro"); return Promise.resolve(false); }
  const doc = app.sessao.id;
  const corpo = { id: doc, tipo, params, selecao: app.specSelecao(), camada,
    categoria: semCategoria ? null : app.categoria, descricao: app.descricao };
  const lenta = LENTAS.has(tipo) || msg;
  return enfileirar(async () => {
    try {
      if (lenta) ocupar(msg || "aplicando…");
      const r = await post("/api/op", corpo);
      if (app.sessao?.id !== doc) return true;
      app.limparPrevia();
      app._catPendente = false;
      if (!r.nada) app.visor.aplicarRecorte(await bitmap(r.png), r.bbox[0], r.bbox[1]);
      atualizarSessao(r.estado);
      app.status(`${tipo} aplicado`);
      return true;
    } catch (e) { app.aviso(e.message, "erro"); return false; } finally { if (lenta) ocupar(null); }
  });
};

function voltar(rota) {
  if (!app.sessao || app.ocupado || !podeEditar()) return Promise.resolve();
  const doc = app.sessao.id;
  return enfileirar(async () => {
    try {
      const r = await post(rota, { id: doc });
      if (app.sessao?.id !== doc) return;
      if (!r.nada) app.visor.aplicarRecorte(await bitmap(r.png), r.bbox[0], r.bbox[1]);
      app.limparPrevia();
      atualizarSessao(r.estado);
    } catch (e) { app.aviso(e.message, "erro"); }
  });
}
app.desfazer = () => voltar("/api/desfazer");
app.refazer = () => voltar("/api/refazer");

app.contaGotas = async (p, raio = 1) => {
  try {
    const r = await post("/api/conta_gotas", { id: app.sessao.id, x: p.x, y: p.y, raio });
    app.definirCorFrente(r.cor); app.status(`cor ${r.cor}`);
  } catch (e) { app.aviso(e.message, "erro"); }
};
app.estimarTexto = async (fonte) => {
  try { return await post("/api/estimar_texto", { id: app.sessao.id, selecao: app.specSelecao(), fonte }); }
  catch (e) { app.aviso(e.message, "erro"); return null; }
};
app.gerarTexto = async () => {
  if (!app.categoria) { exigirCategoria(); return; }
  const r = await get(`/api/gerar?categoria=${encodeURIComponent(app.categoria)}&id=${encodeURIComponent(app.sessao?.id || "")}`);
  if (!r.texto) { app.aviso("sem gerador para esta categoria; digite o valor"); return; }
  const o = app.opcoes(app.idFerramenta);
  o.texto = r.texto;
  app.renderOpcoes();
  if (app.ferramenta.mudouOpcao) app.ferramenta.mudouOpcao();
};
app.preencherSelecao = () => abrirPreencher();

// ------------------------------------------------------------------ qual é a fonte?
app.identificarFonte = async (texto = "") => {
  if (!app.sessao) return;
  if (!app.temSelecao()) { app.aviso("Selecione (arraste sobre) o texto cuja fonte quer descobrir.", "erro"); return; }
  let r;
  try {
    ocupar("comparando com todas as fontes instaladas…");
    r = await post("/api/fonte/identificar", { id: app.sessao.id, selecao: app.specSelecao(), texto });
  } catch (e) { app.aviso(e.message, "erro"); return; } finally { ocupar(null); }
  mostrarFontes(r);
};
function mostrarFontes(r) {
  const img = (b64, alt) => el("img", { src: `data:image/png;base64,${b64}`, alt });
  const texto = el("input", { type: "text", value: r.texto, class: "largo", "aria-label": "texto que está escrito na seleção" });
  const usar = (c) => {
    const id = app.idFerramenta;
    const o = app.opcoes(id);
    Object.assign(o, { fonte: c.fonte, tamanho: c.tamanho, largura: Math.round(c.largura * 1000) / 10, negrito: c.negrito });
    if (app.ferramenta && "ancora" in app.ferramenta) app.ferramenta.ancora = { x: c.x, y: c.y };  // tinta no mesmo lugar
    app.salvarOpcoes(); app.renderOpcoes();
    app.ferramenta?.previa?.();
    app.aviso(`Fonte ${c.fonte} (tamanho ${c.tamanho}, largura ${o.largura}%${c.negrito ? `, negrito ${c.negrito}` : ""}) nas opções do texto.`, "ok");
  };
  const lista = el("div", { class: "fontes-lista" }, ...r.ranking.map((c, i) => el("div", { class: `fonte-cand${i ? "" : " melhor"}` },
    el("div", { class: "fonte-cab" },
      el("b", {}, `${i + 1}. ${c.fonte}`),
      el("span", { class: "pilula", title: "semelhança do desenho das letras, descontado o quanto a fonte precisaria ser esticada" },
        `${Math.max(0, Math.min(100, Math.round(c.nota * 100)))}% parecida`),
      c.parentes.length ? el("span", { class: "dica", title: "desenham este texto praticamente igual" }, `= ${c.parentes.slice(0, 3).join(", ")}`) : null,
      el("button", { type: "button", class: i ? "" : "primario", onclick: () => { usar(c); fecharModal(); } },
        app.ferramenta && "ancora" in app.ferramenta ? "Usar esta" : "Usar nas opções de texto")),
    img(c.png, `o texto na fonte ${c.fonte}`),
    el("div", { class: "dica" }, `tamanho ${c.tamanho} px · largura ${Math.round(c.largura * 100)}%${c.negrito ? ` · negrito ${c.negrito}` : ""}`))));
  abrirModal({
    titulo: "Qual é a fonte?", flutuante: false,
    corpo: [
      el("div", { class: "fonte-original" }, el("span", { class: "dica" }, "No documento:"), img(r.original, "o texto como está no documento")),
      el("p", { class: "dica" }, `${r.avaliadas} fontes instaladas comparadas com o desenho das letras. Confiança ${r.confianca}`
        + (r.documento?.length ? `; o resto do documento usa sobretudo ${r.documento.map(([f]) => f).slice(0, 2).join(" e ")}.` : ".")
        + " Para usar uma fonte que não está aqui, coloque o .ttf na pasta fontes/ e reinicie."),
      el("div", { class: "linha" }, el("label", {}, "Texto lido"), texto,
        el("button", { type: "button", onclick: () => { fecharModal(); app.identificarFonte(texto.value.trim()); } }, "Comparar de novo")),
      lista],
    botoes: [{ rotulo: "Fechar", acao: () => {} }],
  });
}

async function salvar() {
  if (!app.sessao || app.ocupado || !podeEditar()) return;
  await app.confirmarSolto();  // o trecho solto (puxado ou colado) entra na versão
  await app.fila;
  if (!app.sessao?.pode_salvar) return;
  try {
    ocupar("salvando…");
    const r = await post("/api/salvar", {
      id: app.sessao.id, formato: $("formato").value, qualidade: Number($("qualidade").value), operador: $("operador").value,
    });
    const s = r.salvo;
    // a imagem editada, as máscaras e a anotação vão para saida/<split>/ (o mesmo split da imagem de origem)
    app.aviso(`Versão v${String(s.versao).padStart(2, "0")} salva em saida/${s.split}/ (${s.categorias.join(", ")}). `
      + "Indo para a próxima imagem…", "ok");
    app.visor.definirImagem(await bitmapUrl(`/api/img/${app.sessao.id}/atual.png?t=${Date.now()}`));
    app.selecao.formas = []; app.selInfo = null;
    atualizarSessao(r.estado);
    app.estado.splits = (await get("/api/estado")).splits;
    atualizarAbas();
    setTimeout(proximoPendente, 50);
  } catch (e) { app.aviso(e.message, "erro"); } finally { ocupar(null); }
}
async function descartar() {
  if (!app.sessao?.historico?.length || !podeEditar()) return;
  if (!confirm("Descartar TODAS as edições deste documento?")) return;
  try {
    const s = await post("/api/descartar", { id: app.sessao.id });
    app.visor.definirImagem(await bitmapUrl(`/api/img/${app.sessao.id}/atual.png?t=${Date.now()}`));
    atualizarSessao(s);
  } catch (e) { app.aviso(e.message, "erro"); }
}

// ------------------------------------------------------------------ original / máscara
async function mostrarOriginal(sim) {
  if (!app.sessao) return;
  if (sim && !app.visor.orig) await app.carregarOriginal();
  app.visor.mostrarOriginal = sim;
  $("btn-original").classList.toggle("ativo", sim);
  app.visor.redesenhar();
}
async function carregarMascara() {
  if (!app.sessao) return;
  try { app.visor.definirMascara(await bitmapUrl(`/api/mascara/${app.sessao.id}.png?t=${Date.now()}`)); } catch (e) { app.aviso(e.message, "erro"); }
}
function alternarMascara() {
  app.visor.mostrarMascara = !app.visor.mostrarMascara;
  $("btn-mascara").classList.toggle("ativo", app.visor.mostrarMascara);
  if (app.visor.mostrarMascara) carregarMascara(); else app.visor.redesenhar();
}

// ------------------------------------------------------------------ diálogos
const dlg = $("modal");
function erroNoDialogo(texto) {
  const caixa = $("modal-erro");
  if (caixa) caixa.textContent = texto;
}
let aoFecharModal = null;
function abrirModal({ titulo, corpo, botoes, flutuante = true, aoFechar = null }) {
  if (dlg.open) dlg.close();
  $("modal-titulo").textContent = titulo;
  erroNoDialogo("");
  $("modal-corpo").replaceChildren(...[corpo].flat());
  $("modal-acoes").replaceChildren(...botoes.map((b) => el("button", {
    type: "button", class: b.primario ? "primario" : "",
    onclick: async () => { const fechar = await b.acao(); if (fechar !== false) fecharModal(); },
  }, b.rotulo)));
  aoFecharModal = aoFechar ?? (flutuante ? app.limparPrevia : null);
  dlg.classList.toggle("flutuante", flutuante);
  if (flutuante) {
    const estreito = innerWidth < 900;  // celular: o painel lateral não está à direita
    const painel = $("painel").getBoundingClientRect(), visor = $("visor").getBoundingClientRect();
    Object.assign(dlg.style, estreito ? { position: "fixed", top: "56px", left: "8px", right: "8px", margin: "0" }
      : { position: "fixed", top: `${Math.round(visor.top + 8)}px`, right: `${Math.round(innerWidth - painel.left + 8)}px`, left: "auto", margin: "0" });
    dlg.show();
  } else { dlg.removeAttribute("style"); dlg.showModal(); }
}
function fecharModal() { if (dlg.open) dlg.close(); }
dlg.addEventListener("close", () => { if (aoFecharModal) { const f = aoFecharModal; aoFecharModal = null; f(); } });

function deslizante(rotulo, min, max, passo, valor, aoMudar) {
  const n = el("input", { type: "number", min, max, step: passo, value: valor });
  const r = el("input", { type: "range", min, max, step: passo, value: valor });
  r.addEventListener("input", () => { n.value = r.value; aoMudar(Number(r.value)); });
  n.addEventListener("change", () => { r.value = n.value; aoMudar(Number(n.value)); });
  return el("div", { class: "linha" }, el("label", { style: "min-width:110px" }, rotulo), r, n);
}

function abrirAjustes() {
  if (!app.temSelecao()) { app.aviso("selecione a área a ajustar", "erro"); return; }
  const p = lembrar("ajuste", { ajuste: "brilho_contraste" });
  const area = el("div", {});
  const prever = debounce(() => app.previa("ajuste", p), 120);
  function montar() {
    const [, campos] = AJUSTES[p.ajuste];
    area.replaceChildren(...campos.map(([k, mn, mx, ps, pad]) => {
      if (p[k] === undefined) p[k] = pad;
      return deslizante(k, mn, mx, ps, p[k], (v) => { p[k] = v; prever(); });
    }));
    if (p.ajuste === "ruido") {
      area.append(el("label", {}, el("input", { type: "checkbox", checked: p.monocromatico !== false,
        onchange: (e) => { p.monocromatico = e.target.checked; prever(); } }), " monocromático"));
    }
    prever();
  }
  const tipo = el("select", { onchange: (e) => { p.ajuste = e.target.value; montar(); } },
    Object.entries(AJUSTES).map(([k, [r]]) => el("option", { value: k }, r)));
  tipo.value = p.ajuste;
  montar();
  abrirModal({
    titulo: "Ajustes na seleção", corpo: [seletorCategoria(), el("div", { class: "linha" }, el("label", {}, "Ajuste"), tipo), area],
    botoes: [{ rotulo: "Cancelar", acao: () => true },
      { rotulo: "Aplicar", primario: true, acao: async () => { armazenar("ajuste", p); return app.aplicar("ajuste", { ...p }); } }],
  });
}

function abrirPreencher() {
  if (!app.temSelecao()) { app.aviso("selecione a área a preencher", "erro"); return; }
  const p = lembrar("preencher", { amostragem: "auto", margem: 150, patch: 7 });
  p.semente = Math.floor(Math.random() * 1e6);
  const info = el("div", { class: "dica" }, "calculando prévia…");
  const prever = debounce(() => { info.textContent = "calculando prévia…"; app.previa("preencher", { ...p }); setTimeout(() => { info.textContent = "prévia na imagem (fundo do diálogo)"; }, 900); }, 250);
  const amostra = el("select", { onchange: (e) => { p.amostragem = e.target.value; margem.disabled = p.amostragem !== "margem"; prever(); } },
    [["auto", "automática (ao redor da seleção)"], ["documento", "documento inteiro"], ["margem", "faixa ao redor (px)"]].map(([v, r]) => el("option", { value: v }, r)));
  amostra.value = p.amostragem;
  const margem = el("input", { type: "number", min: 10, max: 5000, value: p.margem, disabled: p.amostragem !== "margem",
    onchange: (e) => { p.margem = Number(e.target.value); prever(); } });
  const patch = el("select", { onchange: (e) => { p.patch = Number(e.target.value); prever(); } },
    [5, 7, 9, 11].map((v) => el("option", { value: v }, `${v}x${v}`)));
  patch.value = String(p.patch);
  const sem = el("input", { type: "number", value: p.semente, onchange: (e) => { p.semente = Number(e.target.value); prever(); } });
  abrirModal({
    titulo: "Preenchimento por similaridade (sensível ao conteúdo)",
    corpo: [seletorCategoria(),
      el("div", { class: "linha" }, el("label", {}, "Amostrar de"), amostra, el("label", {}, "faixa px"), margem),
      el("div", { class: "linha" }, el("label", {}, "Tamanho do patch"), patch,
        el("span", { class: "dica" }, "maior = segue mais a estrutura (linhas, tabelas); menor = mais detalhe")),
      el("div", { class: "linha" }, el("label", {}, "Semente"), sem,
        el("button", { type: "button", onclick: () => { p.semente = Math.floor(Math.random() * 1e6); sem.value = p.semente; prever(); } }, "🎲 Outra variação")),
      info],
    botoes: [{ rotulo: "Cancelar", acao: () => true },
      { rotulo: "Aplicar", primario: true, acao: async () => { armazenar("preencher", { amostragem: p.amostragem, margem: p.margem, patch: p.patch }); return app.aplicar("preencher", { ...p }); } }],
  });
  prever();
}

function abrirCodigoBarras() {
  if (!app.temSelecao()) { app.aviso("selecione (retângulo) a área do código de barras", "erro"); return; }
  const p = { digitos: "", cor: "#000000", cor_fundo: "#ffffff", margem: 0, largo: 3 };
  const prever = debounce(() => { if (p.digitos.replace(/\D/g, "").length >= 2) app.previa("codigo_barras", p); }, 150);
  const info = el("div", { class: "dica" }, "44 dígitos (contas de consumo começam com 8). Use 🎲 para um código válido.");
  const dig = el("input", { type: "text", class: "largo", placeholder: "44 dígitos", oninput: (e) => { p.digitos = e.target.value; prever(); } });
  let linha = "";
  const gerar = async () => {
    const r = await get("/api/gerar?categoria=codigo%20barras");
    p.digitos = r.digitos; dig.value = r.digitos; linha = r.linha;
    info.textContent = `Linha digitável: ${r.linha}  ·  valor ${r.valor}`;
    prever();
  };
  abrirModal({
    titulo: "Código de barras ITF-25",
    corpo: [seletorCategoria(),
      el("div", { class: "linha" }, dig, el("button", { type: "button", onclick: gerar, title: "Gerar código válido" }, "🎲")),
      info,
      el("div", { class: "linha" },
        el("label", {}, "Barras"), el("input", { type: "color", value: p.cor, oninput: (e) => { p.cor = e.target.value; prever(); } }),
        el("label", {}, "Fundo"), el("input", { type: "color", value: p.cor_fundo, oninput: (e) => { p.cor_fundo = e.target.value; prever(); } })),
      deslizante("Margem (módulos)", 0, 40, 1, 0, (v) => { p.margem = v; prever(); }),
      deslizante("Razão larga/estreita", 2, 3.5, 0.1, 3, (v) => { p.largo = v; prever(); }),
      el("button", { type: "button", onclick: () => {
        if (!linha) { app.aviso("gere um código primeiro"); return; }
        navigator.clipboard?.writeText(linha);
        app.opcoes("substituir").texto = linha; app.opcoes("texto").texto = linha;
        app.aviso("Linha digitável copiada (e posta nas ferramentas de texto). Edite-a com a categoria 'linha digitável'.");
      } }, "Copiar linha digitável")],
    botoes: [{ rotulo: "Cancelar", acao: () => true }, { rotulo: "Aplicar", primario: true, acao: () => app.aplicar("codigo_barras", { ...p }) }],
  });
}

function abrirQR() {
  if (!app.temSelecao()) { app.aviso("selecione a área do QR code", "erro"); return; }
  const p = { conteudo: "", correcao: "M", borda: 1, cor: "#000000", cor_fundo: "#ffffff" };
  const prever = debounce(() => { if (p.conteudo) app.previa("qrcode", p); }, 150);
  const txt = el("textarea", { placeholder: "conteúdo (PIX copia-e-cola, URL…)", oninput: (e) => { p.conteudo = e.target.value; prever(); } });
  const gerar = async () => { const r = await get(`/api/gerar?categoria=qr%20code%2Fpix&id=${encodeURIComponent(app.sessao?.id || "")}`); p.conteudo = r.texto; txt.value = r.texto; prever(); };
  const corr = el("select", { onchange: (e) => { p.correcao = e.target.value; prever(); } }, ["L", "M", "Q", "H"].map((c) => el("option", { value: c }, c)));
  corr.value = "M";
  abrirModal({
    titulo: "QR code",
    corpo: [seletorCategoria(), txt,
      el("div", { class: "linha" }, el("button", { type: "button", onclick: gerar }, "🎲 PIX aleatório"),
        el("label", {}, "Correção"), corr,
        el("label", {}, "Cor"), el("input", { type: "color", value: p.cor, oninput: (e) => { p.cor = e.target.value; prever(); } }),
        el("label", {}, "Fundo"), el("input", { type: "color", value: p.cor_fundo, oninput: (e) => { p.cor_fundo = e.target.value; prever(); } })),
      deslizante("Borda (módulos)", 0, 8, 1, 1, (v) => { p.borda = v; prever(); })],
    botoes: [{ rotulo: "Cancelar", acao: () => true }, { rotulo: "Aplicar", primario: true, acao: () => app.aplicar("qrcode", { ...p }) }],
  });
}

async function abrirIA() {
  if (!app.temSelecao()) { app.aviso("selecione a área que a IA deve editar", "erro"); return; }
  const p = lembrar("ia", { passos: 4, contexto: 0.5, megapixels: 1.0, modelo: "2511" });
  p.prompt = p.prompt || 'Troque o texto "ANTIGO" por "NOVO", mantendo a mesma fonte, tamanho, cor, alinhamento e fundo do documento.';
  p.semente = Math.floor(Math.random() * 1e6);
  const st = el("div", { class: "dica" }, "verificando servidor de IA…");
  const txt = el("textarea", { value: p.prompt, oninput: (e) => { p.prompt = e.target.value; } });
  txt.value = p.prompt;
  const licenca = el("div", { class: "dica" });
  const passos = el("input", { type: "number", min: 1, max: 80, value: p.passos, onchange: (e) => { p.passos = Number(e.target.value); } });
  const modelo = el("select", { onchange: (e) => {
    p.modelo = e.target.value;
    const m = (app.iaModelos || []).find((x) => x.id === p.modelo);
    if (m) { p.passos = m.passos; passos.value = m.passos; licenca.textContent = `Licença: ${m.licenca}`; }
  } });
  get("/api/ia/status").then((s) => {
    app.iaModelos = s.modelos || [];
    modelo.replaceChildren(...app.iaModelos.map((m) => el("option", { value: m.id }, m.nome)));
    if (app.iaModelos.some((m) => m.id === p.modelo)) modelo.value = p.modelo;
    else if (s.modelo_id) { modelo.value = s.modelo_id; p.modelo = s.modelo_id; }
    const m = app.iaModelos.find((x) => x.id === modelo.value);
    if (m) licenca.textContent = `Licença: ${m.licenca}`;
    st.textContent = s.online
      ? `Servidor de IA online · ${s.modelo || ""} · ${s.carregado ? "modelo carregado" : "modelo carrega no 1º uso (demora)"}`
      : "Servidor de IA OFFLINE. Rode em outro terminal:  .venv-ia\\Scripts\\python.exe ia_servidor.py  (ver README).";
  });
  abrirModal({
    titulo: "Editar seleção com IA (Qwen)",
    corpo: [seletorCategoria(), el("label", {}, "Instrução (o modelo vê a seleção + contexto ao redor):"), txt,
      el("div", { class: "linha" },
        el("label", {}, "Semente"), el("input", { type: "number", value: p.semente, onchange: (e) => { p.semente = Number(e.target.value); } }),
        el("label", {}, "Modelo"), modelo,
        el("label", {}, "Passos"), passos,
        el("label", {}, "Contexto ×"), el("input", { type: "number", min: 0, max: 3, step: 0.1, value: p.contexto, onchange: (e) => { p.contexto = Number(e.target.value); } }),
        el("label", {}, "Megapixels"), el("input", { type: "number", min: 0.25, max: 2, step: 0.05, value: p.megapixels, onchange: (e) => { p.megapixels = Number(e.target.value); } })),
      licenca,
      el("div", { class: "dica" }, "Só os pixels DENTRO da seleção mudam; o resto do resultado da IA é descartado."),
      st],
    botoes: [
      { rotulo: "Descarregar modelo", acao: async () => { try { await post("/api/ia/descarregar", {}); app.aviso("modelo descarregado da GPU"); } catch (e) { app.aviso(e.message, "erro"); } return false; } },
      { rotulo: "Cancelar", acao: () => true },
      { rotulo: "Editar com IA", primario: true, acao: async () => {
        armazenar("ia", { passos: p.passos, contexto: p.contexto, megapixels: p.megapixels, prompt: p.prompt, modelo: p.modelo });
        return app.aplicar("ia", { ...p }, { msg: "IA editando… (pode levar minutos na primeira vez)" });
      } }],
  });
}

// colar de outro documento: escolher doador (mesmo split) e o trecho
app.escolherDoador = async () => {
  if (!app.sessao) return;
  let lista;
  try { lista = await get(`/api/doadores?id=${app.sessao.id}`); } catch (e) { app.aviso(e.message, "erro"); return; }
  if (!lista.length) { app.aviso(`não há outro documento no split '${app.sessao.split}' para servir de doador`, "erro"); return; }
  const grade = el("div", { class: "grade-doadores" }, lista.map((d) =>
    el("button", { type: "button", title: d.nome, onclick: () => escolherTrecho(d) },
      el("img", { src: `/api/miniatura/${d.id}.jpg`, loading: "lazy", alt: "" }), el("span", {}, d.nome))));
  abrirModal({ titulo: `Doador — só documentos do split '${app.sessao.split}'`, corpo: grade, flutuante: false,
    botoes: [{ rotulo: "Cancelar", acao: () => true }] });
};
async function escolherTrecho(d) {
  let bmp;
  try { ocupar("carregando doador…"); bmp = await bitmapUrl(`/api/img/${d.id}/original.png`); } catch (e) { app.aviso(e.message, "erro"); return; } finally { ocupar(null); }
  const esc = Math.min(1, 980 / bmp.width, 620 / bmp.height);
  const c = el("canvas", { width: Math.round(bmp.width * esc), height: Math.round(bmp.height * esc) });
  const ctx = c.getContext("2d");
  let ret = null, a = null;
  const desenhar = () => {
    ctx.drawImage(bmp, 0, 0, c.width, c.height);
    if (ret) { ctx.strokeStyle = "#00e6ff"; ctx.lineWidth = 2; ctx.setLineDash([5, 4]); ctx.strokeRect(ret.x0 * esc, ret.y0 * esc, (ret.x1 - ret.x0) * esc, (ret.y1 - ret.y0) * esc); }
  };
  const pt = (ev) => { const r = c.getBoundingClientRect(); return { x: Math.max(0, Math.min(bmp.width, (ev.clientX - r.left) / esc)), y: Math.max(0, Math.min(bmp.height, (ev.clientY - r.top) / esc)) }; };
  c.addEventListener("pointerdown", (ev) => { a = pt(ev); c.setPointerCapture(ev.pointerId); });
  c.addEventListener("pointermove", (ev) => {
    if (!a) return; const b = pt(ev);
    ret = { x0: Math.round(Math.min(a.x, b.x)), y0: Math.round(Math.min(a.y, b.y)), x1: Math.round(Math.max(a.x, b.x)), y1: Math.round(Math.max(a.y, b.y)) };
    desenhar();
  });
  c.addEventListener("pointerup", () => { a = null; });
  desenhar();
  abrirModal({
    titulo: `Arraste o trecho a copiar de: ${d.nome}`, corpo: el("div", { class: "doador-area" }, c), flutuante: false,
    botoes: [{ rotulo: "Voltar", acao: () => { app.escolherDoador(); return false; } },
      { rotulo: "Usar trecho", primario: true, acao: () => {
        if (!ret || ret.x1 - ret.x0 < 2 || ret.y1 - ret.y0 < 2) { app.aviso("arraste um retângulo sobre o trecho", "erro"); return false; }
        const pre = el("canvas", { width: ret.x1 - ret.x0, height: ret.y1 - ret.y0 });
        pre.getContext("2d").drawImage(bmp, -ret.x0, -ret.y0);  // os pixels seguem o mouse até a prévia chegar
        fecharModal(); app.F.colar.iniciar(d.id, ret, { pre }); app.usar("colar"); return true;
      } }],
  });
}

// ------------------------------------------------------------------ mouse
function montarMouse() {
  const tela = $("tela");
  let pan = null;
  // Ctrl+arrastar dentro da seleção puxa os pixels com qualquer ferramenta (como o Mover temporário do Photoshop)
  const puxaComCtrl = (ev, p) => ev.ctrlKey && app.temSelecao() && !["transformar", "colar", "mao", "zoom"].includes(app.idFerramenta)
    && app.dentroDaSelecao(p);
  tela.addEventListener("pointerdown", (ev) => {
    if (!app.sessao || app.ocupado) return;
    $("visor").focus({ preventScroll: true });
    tela.setPointerCapture(ev.pointerId);
    if (ev.button === 1 || app.espaco || app.idFerramenta === "mao") { pan = app.visor.telaDoEvento(ev); tela.style.cursor = "grabbing"; return; }
    if (ev.button !== 0) return;
    app.ultimoPonto = app.visor.doEvento(ev);
    if (puxaComCtrl(ev, app.ultimoPonto)) app.usar("transformar");
    app.apertado = true;
    app.ferramenta?.down?.(app.ultimoPonto, ev);
    app.visor.redesenhar();
  });
  tela.addEventListener("pointermove", (ev) => {
    if (!app.visor.img) return;
    const p = app.visor.doEvento(ev);
    $("st-pos").textContent = `x ${Math.floor(p.x)}  y ${Math.floor(p.y)}`;
    if (pan) { const s = app.visor.telaDoEvento(ev); app.visor.mover(s.x - pan.x, s.y - pan.y); pan = s; return; }
    if (app.ocupado) return;
    app.ferramenta?.move?.(p, ev);
    if (!app.espaco) {
      tela.style.cursor = !app.apertado && puxaComCtrl(ev, p) ? "move"
        : app.ferramenta?.cursorEm?.(p) || app.ferramenta?.cursor || "default";
    }
  });
  const soltar = (ev) => {
    if (pan) { pan = null; tela.style.cursor = app.espaco ? "grab" : (app.ferramenta?.cursor || "default"); return; }
    const estava = app.apertado;
    app.apertado = false;
    if (!app.sessao || app.ocupado) { app.visor.redesenhar(); return; }
    if (estava) app.ferramenta?.up?.(app.visor.doEvento(ev), ev);
    app.visor.redesenhar();
  };
  tela.addEventListener("pointerup", soltar);
  tela.addEventListener("pointercancel", soltar);
  tela.addEventListener("pointerleave", () => app.ferramenta?.sair?.());
  tela.addEventListener("dblclick", () => app.ferramenta?.dbl?.());
  tela.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const s = app.visor.telaDoEvento(ev);
    if (ev.shiftKey) app.visor.mover(-ev.deltaY, 0);
    else app.visor.zoomEm(Math.exp(-ev.deltaY * 0.0015), s.x, s.y);
  }, { passive: false });
  tela.addEventListener("contextmenu", (ev) => ev.preventDefault());
  app.visor.extra = (ctx, v) => { app.ferramenta?.desenhar?.(ctx, v); app.atualizarBarras?.(); };
  app.visor.aoMudar = () => {
    const z = `${Math.round(app.visor.escala * 100)}%`;
    $("st-zoom").textContent = z; $("zoom-val").textContent = z;
  };
}

// ------------------------------------------------------------------ teclado
function montarTeclado() {
  document.addEventListener("keydown", (ev) => {
    const alvo = ev.target;
    const digitando = alvo instanceof HTMLInputElement && !["checkbox", "range", "color"].includes(alvo.type) ||
      alvo instanceof HTMLTextAreaElement || alvo instanceof HTMLSelectElement;
    if (dlg.open && dlg.classList.contains("flutuante") === false) return; // diálogo modal cuida de si
    if (menuAberto() || document.querySelector("dialog[open]:not(#modal)")) return;  // menu ou glossário abertos
    if (digitando) {
      if (ev.key === "Enter" && alvo.dataset?.dado && app.ferramenta?.tecla) { ev.preventDefault(); app.ferramenta.tecla(ev); }
      if (ev.key === "Escape") alvo.blur();
      return;
    }
    const k = ev.key, ctrl = ev.ctrlKey || ev.metaKey;
    if (k === "?") { ev.preventDefault(); abrirAtalhos(); return; }
    if (ctrl) {
      const l = k.toLowerCase();
      if (l === "z" && ev.shiftKey || l === "y") { ev.preventDefault(); app.refazer(); return; }
      if (l === "z") { ev.preventDefault(); app.desfazer(); return; }
      if (l === "s") { ev.preventDefault(); salvar(); return; }
      if (l === "d") { ev.preventDefault(); app.desmarcar(); return; }
      if (l === "c" && !ev.shiftKey) { ev.preventDefault(); app.copiar(); return; }
      if (l === "x") { ev.preventDefault(); app.recortar(); return; }
      if (l === "v" && ev.shiftKey) { ev.preventDefault(); app.escolherDoador(); return; }
      if (l === "v") { ev.preventDefault(); app.colar(); return; }
      if (l === "t") { ev.preventDefault(); app.puxar(); return; }
      if (l === "a") { ev.preventDefault(); app.selecionarTudo(); return; }
      if (l === "i" && ev.shiftKey) { ev.preventDefault(); app.inverterSelecao(); return; }
      if (l === "n" && ev.shiftKey) { ev.preventDefault(); novaCamada(); return; }
      if (l === "j") { ev.preventDefault(); const at = app.camadaAtiva(); if (at) app.acaoCamada("duplicar", { camada: at.id }); return; }
      if (l === "e") { ev.preventDefault(); const at = app.camadaAtiva(); if (at) app.acaoCamada("mesclar_abaixo", { camada: at.id }); return; }
      if (k === "]") { ev.preventDefault(); moverCamadaNaPilha(1); return; }
      if (k === "[") { ev.preventDefault(); moverCamadaNaPilha(-1); return; }
      if (k === "0") { ev.preventDefault(); app.visor.ajustar(); return; }
      if (k === "+" || k === "=") { ev.preventDefault(); app.visor.zoomEm(1.25); return; }
      if (k === "-") { ev.preventDefault(); app.visor.zoomEm(0.8); return; }
      return;
    }
    if (ev.altKey && /^[1-9]$/.test(k)) {  // Alt+1, Alt+2, Alt+3: o tipo da edição
      ev.preventDefault();
      const c = categorias()[Number(k) - 1];
      if (c) escolherCategoria(c.chave);
      return;
    }
    if (app.ferramenta?.tecla?.(ev)) { ev.preventDefault(); return; }
    if (k === " ") { ev.preventDefault(); if (!app.espaco) { app.espaco = true; $("tela").style.cursor = "grab"; } return; }
    if (k === "\\") { ev.preventDefault(); if (!ev.repeat) mostrarOriginal(true); return; }
    if ((k === "F5" || k === "Backspace") && ev.shiftKey) { ev.preventDefault(); app.preencherSelecao(); return; }
    if ((k === "Delete" || k === "Backspace") && app.temSelecao()) {  // apagar com preenchimento, sem diálogo
      ev.preventDefault();
      if (!app.ferramenta?.pendente?.()) app.apagarComPreenchimento();
      return;
    }
    if (k === "PageDown") { ev.preventDefault(); navegar(1); return; }
    if (k === "PageUp") { ev.preventDefault(); navegar(-1); return; }
    if (k === "Escape") { if (dlg.open) fecharModal(); else if (app.temSelecao()) app.desmarcar(); app.limparPrevia(); return; }
    if (k === "k" || k === "K") { alternarMascara(); return; }
    if (k === "x" || k === "X") { const f = app.corFrente; app.definirCorFrente(app.corFundo); app.corFundo = f; $("cor-fundo").value = f; armazenar("corFundo", f); return; }
    if (k === "d" || k === "D") { app.definirCorFrente("#000000"); app.corFundo = "#ffffff"; $("cor-fundo").value = "#ffffff"; return; }
    if (k === "0") { app.visor.ajustar(); return; }
    if (k === "1") { app.visor.zoom100(); return; }
    if (k === "+" || k === "=") { app.visor.zoomEm(1.25); return; }
    if (k === "-") { app.visor.zoomEm(0.8); return; }
    const id =Object.keys(app.F).find((f) => app.F[f].atalho && app.F[f].atalho === k) ||
      Object.keys(app.F).find((f) => app.F[f].atalho && app.F[f].atalho === k.toLowerCase());
    if (id) { ev.preventDefault(); app.usar(id); }
  });
  document.addEventListener("keyup", (ev) => {
    if (ev.key === " ") { app.espaco = false; $("tela").style.cursor = app.ferramenta?.cursor || "default"; }
    if (ev.key === "\\") mostrarOriginal(false);
  });
  window.addEventListener("blur", () => { app.espaco = false; if (app.visor.mostrarOriginal) mostrarOriginal(false); });
}

function abrirAtalhos() {
  const grupo = (titulo, itens) => el("section", { class: "atalhos-grupo" }, el("h4", {}, titulo),
    el("table", {}, ...itens.map(([k, o]) => el("tr", {}, el("td", {}, el("kbd", {}, k)), el("td", {}, o)))));
  const ferr = Object.entries(app.F).filter(([, f]) => f.atalho).map(([id, f]) => [textoAtalho(f), NOMES[id]]);
  abrirModal({
    titulo: "Atalhos de teclado", flutuante: false,
    corpo: el("div", { class: "atalhos" },
      grupo("Geral", [["Ctrl+S", "salvar e ir para a próxima imagem"], ["Ctrl+Z / Ctrl+Y", "desfazer / refazer"],
        ["PageDown / PageUp", "próxima / anterior imagem"],
        ["\\ (segurar)", "ver o original"], ["K", "mostrar a máscara do que mudou"], ["0 · 1 · + · −", "caber na tela, 100%, zoom"],
        ["Espaço + arrastar", "mover a imagem"], ["?", "esta lista"]]),
      grupo("Recortar, puxar, apagar", [["Ctrl+X", "recortar: o trecho fica solto para arrastar"], ["Ctrl+C · Ctrl+V", "copiar · colar (aqui ou em outra imagem do split)"],
        ["Ctrl+Shift+V", "colar de outro documento (escolher o trecho)"], ["Ctrl+arrastar", "puxar a seleção com qualquer ferramenta"],
        ["Alt+arrastar (Mover)", "duplicar em vez de puxar"], ["Ctrl+T", "puxar / transformar a seleção"],
        ["Enter · Esc", "aplicar · cancelar o trecho solto"], ["Delete", "apagar com preenchimento"], ["Shift+F5", "preencher com opções"]]),
      grupo("Edição", [["Alt+1 · Alt+2 · Alt+3", "tipo da edição"], ["Ctrl+D · Ctrl+A · Ctrl+Shift+I", "desmarcar · tudo · inverter seleção"],
        ["Ctrl+Shift+N · Ctrl+J · Ctrl+E", "camada nova · duplicar · mesclar abaixo"],
        ["X · D", "trocar as cores · preto e branco"]]),
      grupo("Ferramentas", ferr)),
    botoes: [{ rotulo: "Fechar", primario: true, acao: () => {} }],
  });
}

// ------------------------------------------------------------------ lote do gerador e tutorial
function indicarLote(st) {
  const pill = $("lote-pill");
  pill.hidden = !st.rodando;
  if (st.rodando) pill.textContent = `gerador: ${st.feitas}/${st.total}`;
  if (app._loteRodava && !st.rodando && st.total) notificar("Lote do gerador terminou", `${st.feitas} imagens salvas, ${st.falhas} falharam.`);
  app._loteRodava = Boolean(st.rodando);
}
function tutorial() {
  const passo = (n, t) => `<p><span class="passo-n">${n}</span>${t}</p>`;
  abrirModal({
    titulo: "Como editar", flutuante: false,
    corpo: el("div", { class: "tutorial", innerHTML: [
      passo(1, "À direita, escolha o <b>tipo da edição</b>: copiar e colar, pegar de outro documento ou apagar e substituir. "
        + "Sem ele, nada é aplicado; na próxima imagem, o último escolhido já vem marcado."),
      passo(2, "Selecione o trecho (<b>M</b> retângulo, <b>L</b> laço, <b>W</b> varinha). Na barra que aparece embaixo dele: "
        + "<b>Recortar</b> (Ctrl+X) ou <b>Puxar</b> (Ctrl+arrastar) leva os pixels para outro lugar e preenche o buraco; "
        + "<b>Copiar</b>/<b>Colar</b> (Ctrl+C / Ctrl+V) duplicam, inclusive para outra imagem do mesmo split; "
        + "<b>Apagar</b> (Delete) some com o trecho usando o papel em volta. Enter aplica, Esc cancela."),
      passo(3, "As outras ferramentas ficam à esquerda (clique direito num botão mostra as do mesmo grupo) e tudo está nos "
        + "<b>menus</b> do alto. Cada tipo ganha a sua <b>camada</b>: o olho oculta, arrastar reordena, duplo clique renomeia."),
      passo(4, "Clique em <b>Salvar</b> (Ctrl+S): a imagem editada, as máscaras e a anotação vão para <code>saida/</code>, "
        + "no mesmo split da imagem de origem, e a próxima imagem abre. Errou? <b>↶</b> no Histórico (Ctrl+Z)."),
      "<p class='dica'>◀ ▶ (PageUp/PageDown) passam de imagem. O split (Treino, Teste, Validação) é o das abas no alto: "
        + "nada sai do split da imagem, nem a colagem de outro documento.</p>",
    ].join("") }),
    botoes: [{ rotulo: "Entendi", primario: true, acao: () => true }],
    aoFechar: () => armazenar("tutorialEditorVisto", true),
  });
}

// ------------------------------------------------------------------ menus
function abrirModificarSelecao() {
  if (!app.temSelecao()) { exigirSelecao("modificar"); return; }
  const p = { expandir: app.selecao.expandir || 0, suavizar: app.selecao.suavizar || 0 };
  abrirModal({
    titulo: "Modificar a seleção",
    corpo: [deslizante("Expandir (−contrair) px", -50, 50, 1, p.expandir, (v) => { p.expandir = v; }),
      deslizante("Suavizar a borda px", 0, 30, 0.5, p.suavizar, (v) => { p.suavizar = v; })],
    botoes: [{ rotulo: "Cancelar", acao: () => true },
      { rotulo: "Aplicar", primario: true, acao: () => { Object.assign(app.selecao, p); app.atualizarSelecao(); app.renderOpcoes(); } }],
  });
}
function montarMenusEditor() {
  const doc = () => Boolean(app.sessao);
  const sel = () => app.temSelecao();
  const cam = () => Boolean(app.camadaAtiva());
  const comCamada = (acao) => () => { const at = app.camadaAtiva(); if (at) app.acaoCamada(acao, { camada: at.id }); };
  montarMenus($("menus"), [
    { rotulo: "Arquivo", itens: [
      { rotulo: "Salvar e ir para a próxima", atalho: "Ctrl+S", icone: "salvar", acao: salvar, ativo: () => Boolean(app.sessao?.pode_salvar) },
      { rotulo: "Descartar as edições desta imagem", acao: descartar, ativo: () => Boolean(app.sessao?.historico?.length) },
      "-",
      { rotulo: "Próxima imagem", atalho: "PageDown", icone: "proximo", acao: () => navegar(1), ativo: doc },
      { rotulo: "Imagem anterior", atalho: "PageUp", icone: "anterior", acao: () => navegar(-1), ativo: doc },
      { rotulo: "Próxima ainda sem versão", acao: proximoPendente, ativo: doc },
      "-", { titulo: "Split" },
      ...SPLITS.map(([sp, nome]) => ({ rotulo: nome, marcado: () => app.split === sp, acao: () => trocarSplit(sp) })),
      "-",
      { rotulo: "Gerador automático (em lote)…", icone: "fabrica", acao: () => window.open("/gerador", "gerador") },
    ] },
    { rotulo: "Editar", itens: [
      { rotulo: "Desfazer", atalho: "Ctrl+Z", icone: "desfazer", acao: app.desfazer, ativo: () => Boolean(app.sessao?.historico?.length) },
      { rotulo: "Refazer", atalho: "Ctrl+Shift+Z", icone: "refazer", acao: app.refazer, ativo: () => Boolean(app.sessao?.pode_refazer) },
      "-",
      { rotulo: "Recortar", atalho: "Ctrl+X", icone: "recortar", acao: app.recortar, ativo: sel },
      { rotulo: "Copiar", atalho: "Ctrl+C", icone: "copiar", acao: app.copiar, ativo: sel },
      { rotulo: "Colar", atalho: "Ctrl+V", icone: "colar", acao: app.colar, ativo: () => Boolean(app.clip && app.sessao) },
      { rotulo: "Colar de outro documento…", atalho: "Ctrl+Shift+V", icone: "colar_doc", acao: app.escolherDoador, ativo: doc },
      "-",
      { rotulo: "Apagar com preenchimento", atalho: "Delete", icone: "apagar", acao: () => app.apagarComPreenchimento(), ativo: sel },
      { rotulo: "Preencher com opções…", atalho: "Shift+F5", acao: abrirPreencher, ativo: sel },
      { rotulo: "Puxar / transformar", atalho: "Ctrl+T", icone: "mover", acao: app.puxar, ativo: sel },
      "-", { titulo: "Tipo da edição" },
      ...categorias().map((c, i) => ({ rotulo: c.rotulo, atalho: `Alt+${i + 1}`, marcado: () => app.categoria === c.chave,
        acao: () => escolherCategoria(c.chave) })),
    ] },
    { rotulo: "Selecionar", itens: [
      { rotulo: "Tudo", atalho: "Ctrl+A", acao: app.selecionarTudo, ativo: doc },
      { rotulo: "Desmarcar", atalho: "Ctrl+D", acao: app.desmarcar, ativo: sel },
      { rotulo: "Inverter", atalho: "Ctrl+Shift+I", icone: "inverter", acao: app.inverterSelecao, ativo: sel },
      "-",
      { rotulo: "Só a tinta (os traços do texto)", acao: app.soTinta, ativo: sel },
      { rotulo: "Expandir, contrair ou suavizar…", acao: abrirModificarSelecao, ativo: sel },
    ] },
    { rotulo: "Camada", itens: [
      { rotulo: "Nova camada", atalho: "Ctrl+Shift+N", icone: "mais", acao: novaCamada, ativo: doc },
      { rotulo: "Duplicar camada", atalho: "Ctrl+J", icone: "duplicar", acao: comCamada("duplicar"), ativo: cam },
      { rotulo: "Mesclar com a de baixo", atalho: "Ctrl+E", icone: "mesclar", acao: comCamada("mesclar_abaixo"), ativo: cam },
      { rotulo: "Excluir camada", icone: "lixeira", acao: () => $("cam-excluir").click(), ativo: cam },
      "-",
      { rotulo: "Subir", atalho: "Ctrl+]", acao: () => moverCamadaNaPilha(1), ativo: cam },
      { rotulo: "Descer", atalho: "Ctrl+[", acao: () => moverCamadaNaPilha(-1), ativo: cam },
      { rotulo: "Mostrar / ocultar", icone: "olho", acao: () => { const at = app.camadaAtiva(); if (at) app.acaoCamada("props", { camada: at.id, visivel: !at.visivel }); }, ativo: cam },
    ] },
    { rotulo: "Imagem", itens: [
      { rotulo: "Ajustes na seleção…", icone: "ajustes", acao: abrirAjustes, ativo: sel },
      "-",
      { rotulo: "Código de barras ITF-25…", icone: "barras", acao: abrirCodigoBarras, ativo: sel },
      { rotulo: "QR code / PIX…", icone: "qr", acao: abrirQR, ativo: sel },
      "-",
      { rotulo: "Qual é a fonte do texto selecionado?", icone: "fonte", acao: () => app.identificarFonte(), ativo: sel },
      { rotulo: "Editar a seleção com IA…", icone: "ia", acao: abrirIA, ativo: sel },
    ] },
    { rotulo: "Exibir", itens: [
      { rotulo: "Aproximar", atalho: "Ctrl++", acao: () => app.visor.zoomEm(1.25), ativo: doc },
      { rotulo: "Afastar", atalho: "Ctrl+−", acao: () => app.visor.zoomEm(0.8), ativo: doc },
      { rotulo: "Caber na tela", atalho: "Ctrl+0", acao: () => app.visor.ajustar(), ativo: doc },
      { rotulo: "Tamanho real (100%)", atalho: "1", acao: () => app.visor.zoom100(), ativo: doc },
      "-",
      { rotulo: "Ver o original", atalho: "\\", marcado: () => app.visor.mostrarOriginal, acao: () => mostrarOriginal(!app.visor.mostrarOriginal), ativo: doc },
      { rotulo: "Máscara do que mudou", atalho: "K", marcado: () => app.visor.mostrarMascara, acao: alternarMascara, ativo: doc },
    ] },
    { rotulo: "Ajuda", itens: [
      { rotulo: "Como editar", icone: "ajuda", acao: tutorial },
      { rotulo: "Atalhos de teclado", atalho: "?", icone: "teclado", acao: abrirAtalhos },
      { rotulo: "O que é cada coisa (glossário)", icone: "livro", acao: () => abrirGlossario() },
    ] },
  ]);
}

// ------------------------------------------------------------------ início
async function iniciar() {
  try { app.estado = await get("/api/estado"); } catch (e) { app.aviso("servidor indisponível: " + e.message, "erro"); return; }
  await exigirOperador();  // o nome vai nas travas, nas versões salvas e nas revisões
  app.F = criarFerramentas(app);
  app.abrirAjustes = abrirAjustes;
  montarFerramentas();
  for (const b of document.querySelectorAll("[data-ico]")) b.prepend(icone(b.dataset.ico, Number(b.dataset.tam) || 16));
  instalarGlossario($("btn-glossario"));
  ligarTermos(document);
  montarCategorias();
  montarMenusEditor();
  instalarBarras(app);
  $("zoom-menos").addEventListener("click", () => app.visor.zoomEm(0.8));
  $("zoom-mais").addEventListener("click", () => app.visor.zoomEm(1.25));
  $("zoom-val").addEventListener("click", () => app.visor.zoom100());
  $("zoom-ajustar").addEventListener("click", () => app.visor.ajustar());
  montarAbas();
  montarMouse();
  montarTeclado();

  const fmt = $("formato");
  fmt.replaceChildren(...app.estado.formatos.map((f) => el("option", { value: f }, f.toUpperCase())));
  fmt.value = lembrar("formato", "png");
  $("qualidade").value = lembrar("qualidade", 95);
  $("operador").value = nomeOperador();
  const atualizaQ = () => { $("rot-qualidade").style.display = ["jpeg", "webp", "pdf"].includes(fmt.value) ? "" : "none"; };
  fmt.addEventListener("change", () => { armazenar("formato", fmt.value); atualizaQ(); });
  atualizaQ();
  $("qualidade").addEventListener("change", (e) => armazenar("qualidade", Number(e.target.value)));
  $("operador").addEventListener("change", (e) => {
    const antigo = nomeOperador();
    if (!definirOperador(e.target.value)) { e.target.value = antigo; app.aviso("O nome não pode ficar vazio.", "erro"); return; }
    app.aviso(`Agora você é ${nomeOperador()}.`, "ok");
    // a trava do documento aberto passa para o nome novo (senão a pessoa esbarra na própria trava). Vai pela
    // fila: um Salvar logo em seguida espera a troca terminar
    if (antigo !== nomeOperador() && app.sessao && !app.somenteLeitura) {
      const doc = app.sessao.id;
      enfileirar(async () => {
        await post("/api/trava/soltar", { id: doc, operador: antigo }).catch(() => {});
        try { const r = await post("/api/abrir", { id: doc }); if (app.sessao?.id === doc) mostrarTrava(r.travado_por); } catch { /* fica para o próximo sinal */ }
      });
    }
  });
  window.addEventListener("operador", (e) => { $("operador").value = e.detail; });
  window.addEventListener("trava", (e) => mostrarTrava(e.detail));
  $("btn-assumir").addEventListener("click", assumir);
  $("ocupado-cancelar").addEventListener("click", async () => {
    try { await post("/api/ia/cancelar", {}); $("ocupado-cancelar").hidden = true; $("ocupado-ia").textContent += " · cancelando…"; }
    catch (e) { app.aviso(e.message, "erro"); }
  });
  setInterval(sinalDeVida, 60000);
  window.addEventListener("beforeunload", () => {  // solta a trava ao fechar a aba
    if (app.sessao && !app.somenteLeitura) {
      navigator.sendBeacon("/api/trava/soltar", new Blob([JSON.stringify({ id: app.sessao.id, operador: nomeOperador() })], { type: "text/plain" }));
    }
  });
  $("btn-atalhos").addEventListener("click", abrirAtalhos);
  app.descartar = descartar;
  $("modal-form").addEventListener("submit", (e) => e.preventDefault());
  $("cam-nova").addEventListener("click", novaCamada);
  // duplo clique renomeia; o 1º clique pode ter ativado a camada e redesenhado a lista, então espera
  $("lista-camadas").addEventListener("dblclick", async (e) => {
    const li = e.target.closest("li[data-cam]");
    if (!li || e.target.closest("select, button")) return;
    const id = li.dataset.cam;
    await app._acaoPendente;
    const c = app.sessao?.camadas?.find((x) => x.id === id);
    const span = document.querySelector(`#lista-camadas li[data-cam="${id}"] .nome-cam`);
    if (c && span) renomearCamada(span, c);
  });
  $("cam-dup").addEventListener("click", () => { const at = app.camadaAtiva(); if (at) app.acaoCamada("duplicar", { camada: at.id }); });
  $("cam-mesclar").addEventListener("click", () => { const at = app.camadaAtiva(); if (at) app.acaoCamada("mesclar_abaixo", { camada: at.id }); });
  $("cam-excluir").addEventListener("click", () => {
    const at = app.camadaAtiva();
    if (at && (at.vazia || confirm(`Excluir a camada '${at.nome}'? (Ctrl+Z desfaz)`))) app.acaoCamada("excluir", { camada: at.id });
  });
  $("cam-opac").addEventListener("input", (e) => { $("cam-opac-val").textContent = `${e.target.value}%`; });
  $("cam-opac").addEventListener("change", (e) => {
    const at = app.camadaAtiva(); if (at) app.acaoCamada("props", { camada: at.id, opacidade: Number(e.target.value) / 100 });
  });
  $("btn-salvar").addEventListener("click", salvar);
  $("btn-descartar").addEventListener("click", descartar);
  $("btn-desfazer").addEventListener("click", app.desfazer);
  $("btn-refazer").addEventListener("click", app.refazer);
  $("doc-ant").addEventListener("click", () => navegar(-1));
  $("doc-prox").addEventListener("click", () => navegar(1));
  $("btn-original").addEventListener("click", () => mostrarOriginal(!app.visor.mostrarOriginal));
  $("btn-mascara").addEventListener("click", alternarMascara);

  for (const a of app.estado.avisos) app.aviso(a);

  const ferramenta = lembrar("ferramenta", "ret");
  app.usar(app.F[ferramenta] ? ferramenta : "ret");  // a lembrada pode ser de um modo que não existe mais
  definirCategoria(lembrar("categoria", ""));  // o tipo da edição: o da última vez já vem marcado
  window.addEventListener("beforeunload", () => armazenar("ferramenta", app.idFerramenta));
  app.enfileirar = enfileirar;
  app.receber = async (r) => {  // resposta padrão do servidor: trecho da imagem + estado
    if (!r.nada) app.visor.aplicarRecorte(await bitmap(r.png), r.bbox[0], r.bbox[1]);
    app.limparPrevia();
    atualizarSessao(r.estado);
  };
  $("btn-lote").addEventListener("click", () => window.open("/gerador", "gerador"));
  $("btn-ajuda").addEventListener("click", tutorial);
  const statusGerador = () => get("/api/gerador/status").then(indicarLote).catch(() => {});
  statusGerador();
  setInterval(statusGerador, 15000);
  if (!lembrar("tutorialEditorVisto", false)) setTimeout(tutorial, 400);
  if (!SPLITS.some(([s]) => s === app.split)) app.split = "treino";
  await trocarSplit(app.split);
}

iniciar();
