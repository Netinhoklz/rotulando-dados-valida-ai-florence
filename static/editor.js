// Editor principal: estado, eventos, painéis e diálogos.
import { bitmap, bitmapUrl, get, post } from "./api.js";
import { criarFerramentas, ORDEM } from "./ferramentas.js";
import { Visor } from "./visor.js";

const $ = (id) => document.getElementById(id);
const SPLITS = [["treino", "Treino"], ["teste", "Teste"], ["validacao", "Validação"]];
const LENTAS = new Set(["preencher", "corretivo", "substituir_texto", "transformar", "ia"]);
const AJUSTES = {
  brilho_contraste: ["Brilho/contraste", [["brilho", -100, 100, 1, 0], ["contraste", -100, 100, 1, 0]]],
  niveis: ["Níveis", [["preto", 0, 254, 1, 0], ["branco", 1, 255, 1, 255], ["gama", 0.1, 5, 0.05, 1]]],
  matiz_saturacao: ["Matiz/saturação", [["matiz", -180, 180, 1, 0], ["saturacao", -100, 100, 1, 0], ["luminosidade", -100, 100, 1, 0]]],
  desfoque: ["Desfoque gaussiano", [["sigma", 0.1, 20, 0.1, 1]]],
  nitidez: ["Nitidez (máscara de nitidez)", [["quantidade", 0, 500, 5, 100], ["raio", 0.1, 10, 0.1, 1]]],
  ruido: ["Ruído", [["sigma", 0, 60, 0.5, 5], ["semente", 0, 99999, 1, 0]]],
  jpeg: ["Recompressão JPEG local", [["qualidade", 1, 100, 1, 70]]],
  cinza: ["Tons de cinza", []],
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
    else if (k in e && typeof v !== "string") e[k] = v;
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
function ocupar(msg) {
  app.ocupado = !!msg;
  $("ocupado").hidden = !msg;
  if (msg) $("ocupado-msg").textContent = msg;
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

app.renderOpcoes = () => {
  const box = $("opcoes-ferramenta");
  box.replaceChildren();
  const f = app.ferramenta;
  if (!f) return;
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
function montarFerramentas() {
  const box = $("ferramentas");
  for (const id of ORDEM) {
    if (id === "|") { box.append(el("hr")); continue; }
    const f = app.F[id];
    const atalho = f.atalho ? (f.atalho === f.atalho.toUpperCase() && f.atalho !== f.atalho.toLowerCase() ? "⇧" + f.atalho : f.atalho.toUpperCase()) : "";
    box.append(el("button", { type: "button", "data-f": id, title: f.nome, onclick: () => app.usar(id) },
      f.icone, atalho ? el("small", {}, atalho) : null));
  }
  box.append(el("hr"));
  const acoes = [
    ["▣", "Preencher seleção com preenchimento inteligente (Shift+F5)", () => app.preencherSelecao()],
    ["◐", "Ajustes na seleção (brilho, níveis, desfoque, ruído, JPEG…)", () => abrirAjustes()],
    ["▮▯", "Código de barras ITF-25 / linha digitável", () => abrirCodigoBarras()],
    ["▦", "QR code / PIX", () => abrirQR()],
    ["🤖", "Editar a seleção com IA (Qwen-Image-Edit)", () => abrirIA()],
  ];
  for (const [ic, nome, acao] of acoes) box.append(el("button", { type: "button", title: nome, onclick: acao }, ic));
  const cores = el("div", { class: "cores", title: "Cor de frente / fundo (X troca, D padrão)" },
    el("input", { type: "color", id: "cor-frente", value: app.corFrente, oninput: (e) => app.definirCorFrente(e.target.value) }),
    el("input", { type: "color", id: "cor-fundo", value: app.corFundo, oninput: (e) => { app.corFundo = e.target.value; armazenar("corFundo", app.corFundo); } }));
  box.append(cores);
}

app.usar = (id) => {
  if (!app.F[id]) return;
  if (id === app.idFerramenta && app.ferramenta) { app.renderOpcoes(); return; }
  if (app.ferramenta?.desativar) app.ferramenta.desativar();
  app.limparPrevia();
  app.idFerramenta = id;
  app.ferramenta = app.F[id];
  document.querySelectorAll("#ferramentas button[data-f]").forEach((b) => b.classList.toggle("ativa", b.dataset.f === id));
  app.renderOpcoes();
  $("tela").style.cursor = app.ferramenta.cursor || "default";
  if (app.ferramenta.ativar && app.sessao) app.ferramenta.ativar("usuario");
  app.visor.redesenhar();
};

// ------------------------------------------------------------------ categorias
function montarCategorias() {
  const sel = $("categoria");
  sel.replaceChildren(el("option", { value: "" }, "— escolha o que vai editar —"),
    ...app.estado.categorias.map((c, i) => el("option", { value: c.chave, title: c.dica }, `${c.rotulo}${i < 10 ? `  (Alt+${(i + 1) % 10})` : ""}`)));
  sel.addEventListener("change", () => definirCategoria(sel.value));
  $("descricao").addEventListener("input", (e) => { app.descricao = e.target.value; });
  const ul = $("lista-categorias");
  ul.replaceChildren(...app.estado.categorias.map((c) =>
    el("li", { "data-c": c.chave, title: c.dica || c.rotulo, onclick: () => definirCategoria(c.chave) },
      el("span", { class: "marca-cat" }), c.rotulo)));
}
function definirCategoria(chave) {
  app.categoria = chave;
  $("categoria").value = chave;
  $("categoria").classList.remove("faltando");
  $("descricao").hidden = chave !== "outro";
  document.querySelectorAll("#lista-categorias li").forEach((li) => li.classList.toggle("ativa", li.dataset.c === chave));
  document.querySelectorAll(".sel-cat-modal").forEach((s) => { s.value = chave; });
}
function exigirCategoria() {
  if (!app.categoria) {
    $("categoria").classList.add("faltando"); $("categoria").focus();
    app.aviso("Escolha a CATEGORIA do que está editando (valor, data, nome…) antes de aplicar.", "erro");
    return false;
  }
  if (app.categoria === "outro" && !app.descricao.trim()) {
    $("descricao").focus(); app.aviso("Descreva o que é 'outro'.", "erro"); return false;
  }
  return true;
}
function seletorCategoria(sugestao) {
  if (!app.categoria && sugestao) definirCategoria(sugestao);
  const s = el("select", { class: "sel-cat-modal", onchange: (e) => definirCategoria(e.target.value) },
    el("option", { value: "" }, "— escolha —"), ...app.estado.categorias.map((c) => el("option", { value: c.chave }, c.rotulo)));
  s.value = app.categoria;
  return el("div", { class: "linha" }, el("label", {}, "Categoria desta edição"), s);
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
  desenharDocs();
}
function desenharDocs() {
  const so = $("so-pendentes").checked;
  $("lista-docs").replaceChildren(...app.docs.filter((d) => !so || !d.versoes || d.id === app.sessao?.id).map((d) =>
    el("li", { class: d.id === app.sessao?.id ? "ativo" : "", title: d.nome, onclick: () => abrirDoc(d.id) },
      el("span", { class: "nome" }, d.nome),
      d.rascunho ? el("span", { class: "st rasc", title: "tem edições não salvas (rascunho)" }, "✎") : null,
      el("span", { class: "st" + (d.versoes ? " feito" : "") }, d.versoes ? `✓ ${d.versoes}` : "—"))));
}
function limparDoc() {
  app.sessao = null; app.visor.img = null; app.visor.redesenhar();
  $("vazio").hidden = false; $("doc-nome").textContent = "nenhum documento neste split";
  atualizarSessao(null);
}

async function abrirDoc(id) {
  if (app.ocupado) return;
  try {
    ocupar("abrindo…");
    if (app.ferramenta?.desativar) app.ferramenta.desativar();
    const s = await post("/api/abrir", { id });
    const bmp = await bitmapUrl(`/api/img/${id}/atual.png?t=${Date.now()}`);
    app.visor.definirImagem(bmp);
    app.selecao = { formas: [], inverter: false, expandir: 0, suavizar: 0 }; app.selInfo = null;
    $("vazio").hidden = true;
    armazenar("ultimo." + s.split, id);
    atualizarSessao(s);
    desenharDocs();
    if (app.ferramenta?.ativar) app.ferramenta.ativar("documento");
    app.renderOpcoes();
  } catch (e) { app.aviso(e.message, "erro"); } finally { ocupar(null); }
}
function navegar(passo) {
  if (!app.docs.length) return;
  const i = app.docs.findIndex((d) => d.id === app.sessao?.id);
  const alvo = app.docs[(i + passo + app.docs.length) % app.docs.length];
  if (alvo) abrirDoc(alvo.id);
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
  $("doc-versoes").textContent = s ? `${s.versoes} salva(s)` : "";
  $("btn-salvar").disabled = !s?.pode_salvar;
  $("btn-salvar").title = s?.pode_salvar ? "Salvar original + editada + máscaras" : (s?.motivo || "");
  $("motivo-salvar").textContent = s && !s.pode_salvar ? `Salvar bloqueado: ${s.motivo}.` : (s ? "Pronto para salvar." : "");
  $("btn-desfazer").disabled = !s?.historico?.length;
  $("btn-refazer").disabled = !s?.pode_refazer;
  $("historico").replaceChildren(...(s?.historico || []).map((h) =>
    el("li", { class: h.restaura ? "restaura" : "", title: JSON.stringify(h.params).slice(0, 400) },
      `${h.tipo} `, el("span", { class: "cat" }, h.restaura ? "(restaura original)" : `· ${h.categoria}`),
      ` · ${h.pixels_alterados} px`)));
  const d = app.docs.find((x) => x.id === s?.id);
  if (d && s) { d.rascunho = s.historico.length > 0; d.versoes = s.versoes; }
  if (app.visor.mostrarMascara) carregarMascara();
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
app.desmarcar = () => { app.selecao = { formas: [], inverter: false, expandir: 0, suavizar: 0 }; app.atualizarSelecao(); app.renderOpcoes(); };
app.selecionarTudo = () => {
  if (!app.sessao) return;
  app.selecao = { formas: [{ tipo: "ret", x0: 0, y0: 0, x1: app.sessao.largura, y1: app.sessao.altura, modo: "novo" }], inverter: false, expandir: 0, suavizar: 0 };
  app.atualizarSelecao();
};
app.inverterSelecao = () => { if (app.selecao.formas.length) { app.selecao.inverter = !app.selecao.inverter; app.atualizarSelecao(); } };

// ------------------------------------------------------------------ operações
let seqPrevia = 0;
const enviarPrevia = debounce(async (tipo, params, seq) => {
  try {
    const r = await post("/api/op", { id: app.sessao.id, tipo, params, selecao: app.specSelecao(), previa: true });
    if (seq !== seqPrevia) return;
    app.visor.definirPrevia(await bitmap(r.png), r.bbox[0], r.bbox[1]);
    app.status("prévia");
  } catch (e) { if (seq === seqPrevia) { app.visor.definirPrevia(null); app.status(e.message); } }
}, 150);
app.previa = (tipo, params) => { if (!app.sessao) return; seqPrevia++; enviarPrevia(tipo, params, seqPrevia); };
app.limparPrevia = () => { seqPrevia++; app.visor.definirPrevia(null); };

app.aplicar = async (tipo, params, { semCategoria = false, msg = null } = {}) => {
  if (!app.sessao || app.ocupado) return false;
  if (!semCategoria && !exigirCategoria()) return false;
  try {
    ocupar(LENTAS.has(tipo) || msg ? (msg || "aplicando…") : null);
    const r = await post("/api/op", {
      id: app.sessao.id, tipo, params, selecao: app.specSelecao(),
      categoria: semCategoria ? null : app.categoria, descricao: app.descricao,
    });
    app.limparPrevia();
    app.visor.aplicarRecorte(await bitmap(r.png), r.bbox[0], r.bbox[1]);
    atualizarSessao(r.estado);
    app.status(`${tipo} aplicado`);
    return true;
  } catch (e) { app.aviso(e.message, "erro"); return false; } finally { ocupar(null); }
};

async function voltar(rota) {
  if (!app.sessao || app.ocupado) return;
  try {
    const r = await post(rota, { id: app.sessao.id });
    if (!r.nada) app.visor.aplicarRecorte(await bitmap(r.png), r.bbox[0], r.bbox[1]);
    app.limparPrevia();
    atualizarSessao(r.estado);
  } catch (e) { app.aviso(e.message, "erro"); }
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
  const r = await get(`/api/gerar?categoria=${encodeURIComponent(app.categoria)}`);
  if (!r.texto) { app.aviso("sem gerador para esta categoria; digite o valor"); return; }
  const o = app.opcoes(app.idFerramenta);
  o.texto = r.texto;
  app.renderOpcoes();
  if (app.ferramenta.mudouOpcao) app.ferramenta.mudouOpcao();
};
app.preencherSelecao = () => {
  if (!app.temSelecao()) { app.aviso("selecione a área a preencher", "erro"); return; }
  app.aplicar("preencher", { patch: 7, semente: Math.floor(Math.random() * 1e6) });
};

async function salvar() {
  if (!app.sessao?.pode_salvar || app.ocupado) return;
  try {
    ocupar("salvando…");
    const r = await post("/api/salvar", {
      id: app.sessao.id, formato: $("formato").value, qualidade: Number($("qualidade").value), operador: $("operador").value,
    });
    const s = r.salvo;
    app.aviso(`Versão v${String(s.versao).padStart(2, "0")} salva em saida/${s.split}/ (${s.categorias.join(", ")}). ` +
      `PageDown = próximo documento.`, "ok");
    app.visor.definirImagem(await bitmapUrl(`/api/img/${app.sessao.id}/atual.png?t=${Date.now()}`));
    app.selecao.formas = []; app.selInfo = null;
    atualizarSessao(r.estado);
    app.estado.splits = (await get("/api/estado")).splits;
    atualizarAbas(); desenharDocs();
  } catch (e) { app.aviso(e.message, "erro"); } finally { ocupar(null); }
}
async function descartar() {
  if (!app.sessao?.historico?.length) return;
  if (!confirm("Descartar TODAS as edições deste documento?")) return;
  try {
    const s = await post("/api/descartar", { id: app.sessao.id });
    app.visor.definirImagem(await bitmapUrl(`/api/img/${app.sessao.id}/atual.png?t=${Date.now()}`));
    atualizarSessao(s); desenharDocs();
  } catch (e) { app.aviso(e.message, "erro"); }
}

// ------------------------------------------------------------------ original / máscara
async function mostrarOriginal(sim) {
  if (!app.sessao) return;
  if (sim && !app.visor.orig) {
    try { app.visor.orig = await bitmapUrl(`/api/img/${app.sessao.id}/original.png`); } catch (e) { app.aviso(e.message, "erro"); return; }
  }
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
let aoFecharModal = null;
function abrirModal({ titulo, corpo, botoes, flutuante = true, aoFechar = null }) {
  if (dlg.open) dlg.close();
  $("modal-titulo").textContent = titulo;
  $("modal-corpo").replaceChildren(...[corpo].flat());
  $("modal-acoes").replaceChildren(...botoes.map((b) => el("button", {
    type: "button", class: b.primario ? "primario" : "",
    onclick: async () => { const fechar = await b.acao(); if (fechar !== false) fecharModal(); },
  }, b.rotulo)));
  aoFecharModal = aoFechar ?? (flutuante ? app.limparPrevia : null);
  dlg.classList.toggle("flutuante", flutuante);
  if (flutuante) {
    Object.assign(dlg.style, { position: "fixed", top: "96px", right: "316px", left: "auto", margin: "0" });
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
    corpo: [seletorCategoria("codigo barras"),
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
  const gerar = async () => { const r = await get("/api/gerar?categoria=qr%20code%2Fpix"); p.conteudo = r.texto; txt.value = r.texto; prever(); };
  const corr = el("select", { onchange: (e) => { p.correcao = e.target.value; prever(); } }, ["L", "M", "Q", "H"].map((c) => el("option", { value: c }, c)));
  corr.value = "M";
  abrirModal({
    titulo: "QR code",
    corpo: [seletorCategoria("qr code/pix"), txt,
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
  const p = lembrar("ia", { passos: 4, contexto: 0.5, megapixels: 1.0 });
  p.prompt = p.prompt || 'Troque o texto "ANTIGO" por "NOVO", mantendo a mesma fonte, tamanho, cor, alinhamento e fundo do documento.';
  p.semente = Math.floor(Math.random() * 1e6);
  const st = el("div", { class: "dica" }, "verificando servidor de IA…");
  const txt = el("textarea", { value: p.prompt, oninput: (e) => { p.prompt = e.target.value; } });
  txt.value = p.prompt;
  get("/api/ia/status").then((s) => {
    st.textContent = s.online
      ? `Servidor de IA online · ${s.modelo || ""} · ${s.carregado ? "modelo carregado" : "modelo carrega no 1º uso (demora)"}`
      : "Servidor de IA OFFLINE. Rode em outro terminal:  .venv-ia\\Scripts\\python.exe ia_servidor.py  (ver README).";
  });
  abrirModal({
    titulo: "Editar seleção com IA (Qwen-Image-Edit-2511)",
    corpo: [seletorCategoria(), el("label", {}, "Instrução (o modelo vê a seleção + contexto ao redor):"), txt,
      el("div", { class: "linha" },
        el("label", {}, "Semente"), el("input", { type: "number", value: p.semente, onchange: (e) => { p.semente = Number(e.target.value); } }),
        el("label", {}, "Passos"), el("input", { type: "number", min: 1, max: 50, value: p.passos, onchange: (e) => { p.passos = Number(e.target.value); } }),
        el("label", {}, "Contexto ×"), el("input", { type: "number", min: 0, max: 3, step: 0.1, value: p.contexto, onchange: (e) => { p.contexto = Number(e.target.value); } }),
        el("label", {}, "Megapixels"), el("input", { type: "number", min: 0.25, max: 2, step: 0.05, value: p.megapixels, onchange: (e) => { p.megapixels = Number(e.target.value); } })),
      el("div", { class: "dica" }, "Só os pixels DENTRO da seleção mudam; o resto do resultado da IA é descartado."),
      st],
    botoes: [
      { rotulo: "Descarregar modelo", acao: async () => { try { await post("/api/ia/descarregar", {}); app.aviso("modelo descarregado da GPU"); } catch (e) { app.aviso(e.message, "erro"); } return false; } },
      { rotulo: "Cancelar", acao: () => true },
      { rotulo: "Editar com IA", primario: true, acao: async () => {
        armazenar("ia", { passos: p.passos, contexto: p.contexto, megapixels: p.megapixels, prompt: p.prompt });
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
        fecharModal(); app.F.colar.iniciar(d.id, ret); app.usar("colar"); return true;
      } }],
  });
}

// ------------------------------------------------------------------ mouse
function montarMouse() {
  const tela = $("tela");
  let pan = null;
  tela.addEventListener("pointerdown", (ev) => {
    if (!app.sessao || app.ocupado) return;
    $("visor").focus();
    tela.setPointerCapture(ev.pointerId);
    if (ev.button === 1 || app.espaco || app.idFerramenta === "mao") { pan = app.visor.telaDoEvento(ev); tela.style.cursor = "grabbing"; return; }
    if (ev.button !== 0) return;
    app.ferramenta?.down?.(app.visor.doEvento(ev), ev);
  });
  tela.addEventListener("pointermove", (ev) => {
    if (!app.visor.img) return;
    const p = app.visor.doEvento(ev);
    $("st-pos").textContent = `x ${Math.floor(p.x)}  y ${Math.floor(p.y)}`;
    if (pan) { const s = app.visor.telaDoEvento(ev); app.visor.mover(s.x - pan.x, s.y - pan.y); pan = s; return; }
    if (app.ocupado) return;
    app.ferramenta?.move?.(p, ev);
    if (!app.espaco) tela.style.cursor = app.ferramenta?.cursorEm?.(p) || app.ferramenta?.cursor || "default";
  });
  const soltar = (ev) => {
    if (pan) { pan = null; tela.style.cursor = app.espaco ? "grab" : (app.ferramenta?.cursor || "default"); return; }
    if (!app.sessao || app.ocupado) return;
    app.ferramenta?.up?.(app.visor.doEvento(ev), ev);
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
  app.visor.extra = (ctx, v) => app.ferramenta?.desenhar?.(ctx, v);
  app.visor.aoMudar = () => { $("st-zoom").textContent = `${Math.round(app.visor.escala * 100)}%`; };
}

// ------------------------------------------------------------------ teclado
function montarTeclado() {
  document.addEventListener("keydown", (ev) => {
    const alvo = ev.target;
    const digitando = alvo instanceof HTMLInputElement && !["checkbox", "range", "color"].includes(alvo.type) ||
      alvo instanceof HTMLTextAreaElement || alvo instanceof HTMLSelectElement;
    if (dlg.open && dlg.classList.contains("flutuante") === false) return; // diálogo modal cuida de si
    if (digitando) {
      if (ev.key === "Enter" && alvo.dataset?.dado && app.ferramenta?.tecla) { ev.preventDefault(); app.ferramenta.tecla(ev); }
      if (ev.key === "Escape") alvo.blur();
      return;
    }
    const k = ev.key, ctrl = ev.ctrlKey || ev.metaKey;
    if (ctrl) {
      const l = k.toLowerCase();
      if (l === "z" && ev.shiftKey || l === "y") { ev.preventDefault(); app.refazer(); return; }
      if (l === "z") { ev.preventDefault(); app.desfazer(); return; }
      if (l === "s") { ev.preventDefault(); salvar(); return; }
      if (l === "d") { ev.preventDefault(); app.desmarcar(); return; }
      if (l === "a") { ev.preventDefault(); app.selecionarTudo(); return; }
      if (l === "i" && ev.shiftKey) { ev.preventDefault(); app.inverterSelecao(); return; }
      if (k === "0") { ev.preventDefault(); app.visor.ajustar(); return; }
      if (k === "+" || k === "=") { ev.preventDefault(); app.visor.zoomEm(1.25); return; }
      if (k === "-") { ev.preventDefault(); app.visor.zoomEm(0.8); return; }
      return;
    }
    if (ev.altKey && /^[0-9]$/.test(k)) {
      ev.preventDefault();
      const i = (Number(k) + 9) % 10;
      const c = app.estado.categorias[i];
      if (c) definirCategoria(c.chave);
      return;
    }
    if (app.ferramenta?.tecla?.(ev)) { ev.preventDefault(); return; }
    if (k === " ") { ev.preventDefault(); if (!app.espaco) { app.espaco = true; $("tela").style.cursor = "grab"; } return; }
    if (k === "\\") { ev.preventDefault(); if (!ev.repeat) mostrarOriginal(true); return; }
    if ((k === "F5" || k === "Backspace") && ev.shiftKey) { ev.preventDefault(); app.preencherSelecao(); return; }
    if (k === "PageDown") { ev.preventDefault(); navegar(1); return; }
    if (k === "PageUp") { ev.preventDefault(); navegar(-1); return; }
    if (k === "Escape") { if (dlg.open) fecharModal(); app.limparPrevia(); return; }
    if (k === "k" || k === "K") { alternarMascara(); return; }
    if (k === "x" || k === "X") { const f = app.corFrente; app.definirCorFrente(app.corFundo); app.corFundo = f; $("cor-fundo").value = f; armazenar("corFundo", f); return; }
    if (k === "d" || k === "D") { app.definirCorFrente("#000000"); app.corFundo = "#ffffff"; $("cor-fundo").value = "#ffffff"; return; }
    if (k === "0") { app.visor.ajustar(); return; }
    if (k === "1") { app.visor.zoom100(); return; }
    if (k === "+" || k === "=") { app.visor.zoomEm(1.25); return; }
    if (k === "-") { app.visor.zoomEm(0.8); return; }
    const id = Object.keys(app.F).find((f) => app.F[f].atalho && app.F[f].atalho === k) ||
      Object.keys(app.F).find((f) => app.F[f].atalho && app.F[f].atalho === k.toLowerCase());
    if (id) { ev.preventDefault(); app.usar(id); }
  });
  document.addEventListener("keyup", (ev) => {
    if (ev.key === " ") { app.espaco = false; $("tela").style.cursor = app.ferramenta?.cursor || "default"; }
    if (ev.key === "\\") mostrarOriginal(false);
  });
  window.addEventListener("blur", () => { app.espaco = false; if (app.visor.mostrarOriginal) mostrarOriginal(false); });
}

// ------------------------------------------------------------------ início
async function iniciar() {
  try { app.estado = await get("/api/estado"); } catch (e) { app.aviso("servidor indisponível: " + e.message, "erro"); return; }
  app.F = criarFerramentas(app);
  montarFerramentas();
  montarCategorias();
  montarAbas();
  montarMouse();
  montarTeclado();

  const fmt = $("formato");
  fmt.replaceChildren(...app.estado.formatos.map((f) => el("option", { value: f }, f.toUpperCase())));
  fmt.value = lembrar("formato", "png");
  $("qualidade").value = lembrar("qualidade", 95);
  $("operador").value = lembrar("operador", "");
  const atualizaQ = () => { $("rot-qualidade").style.display = ["jpeg", "webp", "pdf"].includes(fmt.value) ? "" : "none"; };
  fmt.addEventListener("change", () => { armazenar("formato", fmt.value); atualizaQ(); });
  atualizaQ();
  $("qualidade").addEventListener("change", (e) => armazenar("qualidade", Number(e.target.value)));
  $("operador").addEventListener("change", (e) => armazenar("operador", e.target.value));
  $("modal-form").addEventListener("submit", (e) => e.preventDefault());
  $("btn-salvar").addEventListener("click", salvar);
  $("btn-descartar").addEventListener("click", descartar);
  $("btn-desfazer").addEventListener("click", app.desfazer);
  $("btn-refazer").addEventListener("click", app.refazer);
  $("doc-ant").addEventListener("click", () => navegar(-1));
  $("doc-prox").addEventListener("click", () => navegar(1));
  $("so-pendentes").addEventListener("change", desenharDocs);
  $("btn-original").addEventListener("click", () => mostrarOriginal(!app.visor.mostrarOriginal));
  $("btn-mascara").addEventListener("click", alternarMascara);

  const ia = app.estado.ia;
  $("ia-status").textContent = ia.online ? "online" : "offline";
  $("ia-status").className = "pilula " + (ia.online ? "ok" : "ruim");
  $("ia-dica").textContent = ia.online ? (ia.modelo || "") : "Para usar a IA: .venv-ia\\Scripts\\python.exe ia_servidor.py";
  for (const a of app.estado.avisos) app.aviso(a);

  app.usar(lembrar("ferramenta", "ret"));
  window.addEventListener("beforeunload", () => armazenar("ferramenta", app.idFerramenta));
  if (!SPLITS.some(([s]) => s === app.split)) app.split = "treino";
  await trocarSplit(app.split);
}

iniciar();
