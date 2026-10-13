// Gerador automático de fraudes.
// Aba Gerar: no topo, a tela "Gerar fraudes" de um clique (umclique.js: só edições profissionais). Em "Opções
// avançadas", a RECEITA completa — no modo fácil, como uma lista de tipos de edição (assistente.js); no
// avançado, documentos × técnicas × classes × quantidade e acabamento —, mostra a prévia num
// documento (nada é salvo) e acompanha a geração em lote.
// Aba Catálogo: o que já foi gerado, por técnica e classe, com a matriz técnica × classe, galeria,
// detalhe de cada versão (editada, original, máscara de técnicas), revisão e exclusão em lote e
// exportação do subconjunto filtrado. Abas Revisar, Metas e Equipe: painel.js.
import { get, post } from "./api.js";
import { instalarAssistente } from "./assistente.js";
import { definirOperador, exigirOperador, nomeOperador, notificar, pedirPermissaoNotificacao } from "./operador.js";
import { instalarPainel } from "./painel.js";
import { instalarUmClique } from "./umclique.js";

const $ = (id) => document.getElementById(id);
function el(tag, props = {}, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k in e && typeof v !== "string") e[k] = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const f of filhos.flat()) if (f !== null && f !== undefined && f !== false) e.append(f);
  return e;
}
// replaceChildren sem os null/false das partes condicionais (senão aparece o texto "null")
const por = (alvo, ...filhos) => alvo.replaceChildren(...filhos.flat().filter((f) => f !== null && f !== undefined && f !== false));
function guardar(chave, valor) { try { localStorage.setItem("rotulador." + chave, JSON.stringify(valor)); } catch { /* sem storage */ } }
function lembrar(chave, padrao) {
  try { const v = localStorage.getItem("rotulador." + chave); return v === null ? padrao : JSON.parse(v); } catch { return padrao; }
}
function aviso(msg, tipo = "") {
  const a = el("div", { class: "aviso " + tipo }, msg);
  $("avisos").append(a);
  setTimeout(() => a.remove(), tipo === "erro" ? 9000 : 5000);
}

const PALETA = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6",
  "#bfef45", "#fabed4", "#469990", "#dcbeff", "#9a6324", "#fffac8", "#800000", "#aaffc3"];
// mesmas cores de exportar.CORES_FAMILIA (máscara de técnicas colorida)
const COR_FAMILIA = { imitacao: "#dc3c3c", copia_interna: "#3c8ce6", colagem_externa: "#a046dc", remocao: "#f0a01e",
  pixel: "#28be78", codigos: "#787878", ia: "#e646c8", manual: "#c8c83c" , recriacao: "#5ac8dc" };
const NIVEL = { simples: "amador", profissional: "profissional", ia: "IA", manual: "manual" };
const ORDEM_FAMILIAS = ["imitacao", "copia_interna", "colagem_externa", "remocao", "pixel", "codigos", "ia", "recriacao"];
const SPLITS = [["treino", "Treino"], ["teste", "Teste"], ["validacao", "Validação"]];
const SEG_POR_IMAGEM = 4.5;   // medido sem IA (A4 300 dpi): ~4,5 s por imagem salva
const SEG_POR_EDICAO_IA = 150; // ~2 a 4 min por edição de IA

const G = { cat: null, docs: {}, rec: null, sel: new Set(), iaOnline: false, iaModelos: [], previa: null,
  previaModo: "editada", timer: null, filtro: {}, pagina: 0, detalhe: null, modo: lembrar("modoGeradorV2", "exato") };
let A = null;  // modo fácil (tipos de edição): assistente.js
let P = null;  // abas Revisar, Metas e Equipe: painel.js
let U = null;  // tela "Gerar fraudes" de um clique: umclique.js
const ABAS = ["gerar", "catalogo", "revisar", "metas", "equipe"];

const tec = (id) => G.cat.tecnicas.find((t) => t.id === id);
const automaticas = () => G.cat.tecnicas.filter((t) => t.automatica);
const corCategoria = (chave) => {
  const c = G.cat.categorias.find((x) => x.chave === chave);
  return c ? PALETA[(c.id - 1) % PALETA.length] : "#666";
};
const rotuloCat = (chave) => G.cat.categorias.find((x) => x.chave === chave)?.rotulo || chave || "—";
const rotuloSplit = (sp) => (SPLITS.find(([s]) => s === sp) || [sp, sp])[1];
const caixaDe = (b) => (Array.isArray(b) ? b : String(b || "").split(/\s+/).filter(Boolean).map(Number));

// ============================================================================ receita
function receitaPadrao() {
  return {
    nome: "minha receita",
    documentos: { splits: ["treino"], ids: null, so_pendentes: false },
    tecnicas: Object.fromEntries(automaticas().filter((t) => !t.precisa_ia).map((t) => [t.id, 1])),
    opcoes: {},
    categorias: Object.fromEntries(G.cat.categorias.filter((c) => c.chave !== "outro")
      .map((c) => [c.chave, G.cat.pesos_categoria[c.chave] ?? 1])),
    edicoes: [1, 3], variacoes: 2, distribuicao: "equilibrada",
    acabamento: { ruido: "auto", disfarce: "auto", jpeg: 0, pericia: "auto" }, formato: "png", qualidade: 95, operador: "", modelo_ia: null,
    // padrão: a imagem como está (só a edição muda); canal, marcas e falsificadores são escolha do modo fácil/avançado
    // marcas: em 20% das versões com marca, uma delas fica por cima da edição (igual no par)
    canal: { perfil: "nenhum", prob: 0.7 }, marcas: { prob: 0, sobre_edicao: 0.2 },
    // "como uma pessoa faria": por versão, um falsificador (ferramenta, hábitos) e um motivo (campos coerentes)
    falsificadores: { prob: 0, pesos: null, motivos: null },  // null = pesos padrão do servidor
    // operador humano (rotulador/humano.py): fração das versões editadas como uma pessoa num editor de imagem
    humano: { prob: 0 },
    exato_categorias: ["nome", "endereço"],  // modo 🎯 Exato: o que editar (todos os campos dessas classes)
    exato_subconjuntos: true,  // modo 🎯 Exato: cada versão sorteia quais dessas classes troca
  };
}

function lerFalsificadores(c, padrao) {
  if (c === false || c === 0) return { ...padrao, prob: 0 };
  if (!c || typeof c !== "object") return { ...padrao };
  const tabela = (t) => (t && typeof t === "object" && !Array.isArray(t) ? { ...t } : null);
  return { prob: Math.max(0, Math.min(1, Number(c.prob ?? padrao.prob) || 0)), pesos: tabela(c.pesos), motivos: tabela(c.motivos) };
}

// canal de uma receita salva/importada: "scanner" (texto), null (sem canal) ou {perfil, prob}
function lerCanal(c, padrao) {
  if (c === null) return { perfil: "nenhum", prob: padrao.prob };
  if (typeof c === "string") return { perfil: c, prob: 1 };
  if (c && typeof c === "object") return { ...padrao, ...c };
  return { ...padrao };
}

function normalizar(r) {
  const p = receitaPadrao();
  if (!r || typeof r !== "object") return p;
  const out = { ...p, ...r, documentos: { ...p.documentos, ...(r.documentos || {}) },
    acabamento: { ...p.acabamento, ...(r.acabamento || {}) }, canal: lerCanal(r.canal, p.canal),
    marcas: { ...p.marcas, ...(r.marcas && typeof r.marcas === "object" ? r.marcas : {}) },
    falsificadores: lerFalsificadores(r.falsificadores, p.falsificadores),
    humano: { prob: Math.max(0, Math.min(1, Number(r.humano?.prob ?? 0) || 0)) } };
  out.tecnicas = Object.fromEntries(Object.entries(r.tecnicas || p.tecnicas).filter(([id]) => tec(id)?.automatica));
  out.categorias = Object.fromEntries(Object.entries(r.categorias || p.categorias)
    .filter(([k]) => G.cat.categorias.some((c) => c.chave === k)));
  out.opcoes = r.opcoes || {};
  out.exato_categorias = (Array.isArray(r.exato_categorias) ? r.exato_categorias : p.exato_categorias)
    .filter((k) => G.cat.categorias.some((c) => c.chave === k));
  out.exato_subconjuntos = r.exato_subconjuntos !== false;
  return out;
}

// modo 🎯 Exato: técnicas e classes que vão para o servidor (a mesma fonte; códigos gerados de novo, se marcados)
function receitaExata() {
  const cats = G.rec.exato_categorias || [];
  const tecnicas = { "campo.mesma_fonte": 1 };
  if (cats.includes("codigo barras")) tecnicas["codigo.regenerar"] = 1;
  if (cats.includes("qr code/pix")) tecnicas["codigo.qr"] = 1;
  return { tecnicas, categorias: Object.fromEntries(cats.map((k) => [k, 1])) };
}

// modo 🎯 Exato: "variar quais classes entram em cada versão" (a caixa está no HTML; o resto do bloco, no assistente)
function montarSubconjuntos() {
  const c = $("exato-subconjuntos");
  c.checked = G.rec.exato_subconjuntos !== false;
  c.onchange = () => { G.rec.exato_subconjuntos = c.checked; salvarReceita(); };
}

function salvarReceita() {
  const todos = Object.values(G.docs).flat();
  G.rec.documentos.ids = todos.every((d) => G.sel.has(d.id)) ? null : [...G.sel];
  guardar("receitaGerador", G.rec);
  atualizarResumo();
}

function receitaParaEnvio() {
  const r = structuredClone(G.rec);
  const doSplit = docsDosSplits();
  const marcados = doSplit.filter((d) => G.sel.has(d.id));
  r.documentos.ids = marcados.length === doSplit.length ? null : marcados.map((d) => d.id);
  r.operador = r.operador ? `gerador automático (${r.operador})` : "gerador automático";
  delete r.exato_categorias;
  delete r.exato_subconjuntos;
  if (G.modo === "exato") {  // só a original muda: sem canal, marcas, falsificadores nem disfarce
    Object.assign(r, receitaExata(), { exato: true, subconjuntos: G.rec.exato_subconjuntos !== false, canal: null,
      marcas: null, distribuicao: "equilibrada", acabamento: { ruido: "auto", disfarce: "nunca", jpeg: 0 } });
    delete r.tipos; delete r.tipos_desligados; delete r.falsificadores; delete r.opcoes; delete r.humano;
    return r;
  }
  if (!(r.humano?.prob > 0)) r.humano = null;
  const ef = A.efetivas();  // com tipos de edição, técnicas e classes saem deles
  r.tecnicas = { ...ef.tecnicas };
  r.categorias = { ...ef.categorias };
  if (A.usandoTipos()) r.tipos = A.tiposParaEnvio(); else delete r.tipos;
  delete r.tipos_desligados;
  if (!(r.falsificadores?.prob > 0)) delete r.falsificadores;
  else { if (!r.falsificadores.pesos) delete r.falsificadores.pesos; if (!r.falsificadores.motivos) delete r.falsificadores.motivos; }
  if (!G.iaOnline) for (const id of Object.keys(r.tecnicas)) if (tec(id)?.precisa_ia) delete r.tecnicas[id];
  return r;
}

// ============================================================================ 1. documentos
// documentos que o lote vai usar: os marcados (menos os que já têm versão, se "só pendentes")
function docsAGerar() {
  return docsDosSplits().filter((d) => G.sel.has(d.id) && !(G.rec.documentos.so_pendentes && d.versoes));
}

function docsDosSplits() {
  return G.rec.documentos.splits.flatMap((sp) => (G.docs[sp] || []).map((d) => ({ ...d, split: sp })));
}

function montarDocs() {
  $("docs-splits").replaceChildren(...SPLITS.map(([sp, rot]) => el("label", { class: "toggle" },
    el("input", { type: "checkbox", checked: G.rec.documentos.splits.includes(sp), onchange: (e) => {
      const s = new Set(G.rec.documentos.splits);
      e.target.checked ? s.add(sp) : s.delete(sp);
      G.rec.documentos.splits = SPLITS.map(([x]) => x).filter((x) => s.has(x));
      salvarReceita(); desenharDocs();
    } }), ` ${rot} `, el("span", { class: "dica" }, `(${(G.docs[sp] || []).length})`))));
  desenharDocs();
}

function desenharDocs() {
  const busca = $("docs-busca").value.trim().toLowerCase();
  const lista = docsDosSplits().filter((d) => !busca || d.nome.toLowerCase().includes(busca));
  $("docs-lista").replaceChildren(...(lista.length ? lista.map((d) => el("li", { class: G.sel.has(d.id) ? "marcado" : "" },
    el("label", {},
      el("input", { type: "checkbox", checked: G.sel.has(d.id), onchange: (e) => {
        e.target.checked ? G.sel.add(d.id) : G.sel.delete(d.id);
        e.target.closest("li").classList.toggle("marcado", e.target.checked);
        salvarReceita();
      } }),
      el("img", { src: `/api/miniatura/${d.id}.jpg`, loading: "lazy", alt: "" }),
      el("span", { class: "nome", title: d.nome }, d.nome),
      el("span", { class: `pilula split-${d.split}` }, rotuloSplit(d.split)),
      d.versoes ? el("span", { class: "pilula ok", title: "versões já salvas" }, `${d.versoes} salva${d.versoes > 1 ? "s" : ""}`) : null,
      d.lido ? null : el("span", { class: "pilula", title: "Ainda não lido pelo OCR: a 1ª vez leva alguns segundos" }, "não lido"))))
    : [el("li", { class: "vazio" }, "Nenhum documento nos splits marcados.")]));
  const docs = docsDosSplits();
  const sel = $("previa-doc"), atual = sel.value;
  sel.replaceChildren(...docs.filter((d) => G.sel.has(d.id)).map((d) => el("option", { value: d.id }, `${rotuloSplit(d.split)} · ${d.nome}`)));
  if ([...sel.options].some((o) => o.value === atual)) sel.value = atual;
  atualizarResumo();
}

function selecionarDocs(regra) {
  for (const d of docsDosSplits()) (regra(d) ? G.sel.add(d.id) : G.sel.delete(d.id));
  salvarReceita(); desenharDocs();
}

// ============================================================================ 2. técnicas
const PRESETS_TEC = [
  ["Tudo (sem IA)", (t) => !t.precisa_ia],
  ["Só caractere", (t) => t.granularidade === "caractere" && !t.precisa_ia],
  ["Trecho e campo", (t) => t.granularidade !== "caractere" && !t.precisa_ia],
  ["Profissional", (t) => t.nivel === "profissional"],
  ["Amador", (t) => t.nivel === "simples"],
  ["Cópia e colagem", (t) => ["copia_interna", "colagem_externa"].includes(t.familia)],
  ["Remoção", (t) => t.familia === "remocao"],
  ["Traços", (t) => t.familia === "pixel"],
  ["Só IA", (t) => t.precisa_ia],
];

function montarTecnicas() {
  $("tec-presets").replaceChildren(...PRESETS_TEC.map(([rot, regra]) => el("button", { type: "button", onclick: () => {
    G.rec.tecnicas = Object.fromEntries(automaticas().filter(regra).map((t) => [t.id, G.rec.tecnicas[t.id] || 1]));
    salvarReceita(); montarTecnicas();
  } }, rot)), el("button", { type: "button", onclick: () => { G.rec.tecnicas = {}; salvarReceita(); montarTecnicas(); } }, "Limpar"));
  const grupos = ORDEM_FAMILIAS.filter((f) => G.cat.familias[f]).map((f) => [f, G.cat.familias[f]]);
  $("tec-familias").replaceChildren(...grupos.map(([fam, info]) => {
    const ts = automaticas().filter((t) => t.familia === fam);
    const n = ts.filter((t) => G.rec.tecnicas[t.id] > 0).length;
    const todas = el("input", { type: "checkbox", checked: n === ts.length, onchange: (e) => {
      for (const t of ts) {
        if (e.target.checked && !(t.precisa_ia && !G.iaOnline)) G.rec.tecnicas[t.id] = G.rec.tecnicas[t.id] || 1;
        else if (!e.target.checked) delete G.rec.tecnicas[t.id];
      }
      salvarReceita(); montarTecnicas();
    } });
    todas.indeterminate = n > 0 && n < ts.length;
    return el("div", { class: "familia" },
      el("div", { class: "familia-cab", title: info.dica },
        el("label", {}, todas, el("span", { class: "chip", style: { background: COR_FAMILIA[fam] } }), ` ${info.rotulo}`),
        el("span", { class: "lit", title: "código na taxonomia do Find it again! (ICDAR 2023)" }, info.literatura),
        el("span", { class: "dica" }, `${n}/${ts.length}`)),
      ...ts.map(linhaTecnica));
  }));
  if (G.rec && $("qtd").childElementCount) montarQtd();  // o seletor de modelo de IA depende das técnicas
  atualizarResumo();
}

function linhaTecnica(t) {
  const ativo = (G.rec.tecnicas[t.id] || 0) > 0;
  const semIA = t.precisa_ia && !G.iaOnline;
  const opcoes = Object.entries(t.opcoes || {});
  const escolhidas = G.rec.opcoes[t.id] || {};
  const caixaOp = opcoes.length ? el("div", { class: "opcoes-tec", hidden: !Object.keys(escolhidas).length },
    ...opcoes.map(([nome, spec]) => {
      const s = el("select", { onchange: (e) => {
        G.rec.opcoes[t.id] = { ...(G.rec.opcoes[t.id] || {}), [nome]: e.target.value };
        if (e.target.value === "aleatorio") delete G.rec.opcoes[t.id][nome];
        salvarReceita();
      } }, el("option", { value: "aleatorio" }, "varia (sorteado a cada edição)"),
      ...spec.valores.map(([v, r]) => el("option", { value: v }, r)));
      s.value = escolhidas[nome] || "aleatorio";
      return el("label", {}, `${spec.rotulo}: `, s);
    })) : null;
  const peso = el("input", { type: "number", min: 1, max: 10, step: 1, value: G.rec.tecnicas[t.id] || 1, disabled: !ativo,
    "aria-label": `peso de ${t.nome}`,
    title: "Peso: quanto esta técnica aparece em relação às outras",
    onchange: (e) => { G.rec.tecnicas[t.id] = Math.max(1, Math.min(10, Math.round(Number(e.target.value)) || 1));
      e.target.value = G.rec.tecnicas[t.id]; salvarReceita(); } });
  const cats = t.categorias ? ` Só em: ${t.categorias.map(rotuloCat).join(", ")}.` : "";
  return el("div", { class: `tec ${ativo ? "ativa" : ""} ${semIA ? "indisponivel" : ""}`,
    title: semIA ? "Ligue o servidor de IA para usar esta técnica" : t.descricao },
    el("div", { class: "tec-linha" },
      el("label", { class: "tec-nome" }, el("input", { type: "checkbox", checked: ativo, disabled: semIA && !ativo, onchange: (e) => {
        if (e.target.checked) G.rec.tecnicas[t.id] = Number(peso.value) || 1; else delete G.rec.tecnicas[t.id];
        salvarReceita(); montarTecnicas();
      } }), ` ${t.nome}`),
      el("span", { class: `nivel nivel-${t.nivel}` }, NIVEL[t.nivel]),
      el("span", { class: "gran" }, G.cat.granularidades[t.granularidade]),
      el("label", { class: "peso" }, "×", peso),
      opcoes.length ? el("button", { type: "button", class: "mini", title: "Como: opções desta técnica",
        onclick: () => { caixaOp.hidden = !caixaOp.hidden; } }, "⚙") : el("span", { class: "mini-vazio" })),
    el("div", { class: "desc" }, t.descricao + cats),
    caixaOp);
}

// ============================================================================ 3. classes
const PRESETS_CAT = [
  ["Todas", () => true],
  ["Identidade", (k) => ["nome", "cpf/cnpj"].includes(k)],
  ["Endereço", (k) => ["endereço", "bairro", "cep", "cidade/uf"].includes(k)],
  ["Financeiro", (k) => ["valor", "data", "mês de referência", "consumo/leitura"].includes(k)],
  ["Códigos", (k) => ["codigo barras", "linha digitável", "qr code/pix", "identificador documento"].includes(k)],
];

function montarClasses() {
  $("cat-presets").replaceChildren(...PRESETS_CAT.map(([rot, regra]) => el("button", { type: "button", onclick: () => {
    G.rec.categorias = Object.fromEntries(G.cat.categorias.filter((c) => c.chave !== "outro" && regra(c.chave))
      .map((c) => [c.chave, G.rec.categorias[c.chave] || G.cat.pesos_categoria[c.chave] || 1]));
    salvarReceita(); montarClasses();
  } }, rot)), el("button", { type: "button", onclick: () => { G.rec.categorias = {}; salvarReceita(); montarClasses(); } }, "Limpar"));
  $("cat-lista").replaceChildren(...G.cat.categorias.filter((c) => c.chave !== "outro").map((c) => {
    const ativo = (G.rec.categorias[c.chave] || 0) > 0;
    const peso = el("input", { type: "number", min: 0.5, max: 10, step: 0.5, value: G.rec.categorias[c.chave] || 1, disabled: !ativo,
      "aria-label": `peso de ${c.rotulo}`,
      onchange: (e) => { G.rec.categorias[c.chave] = Math.max(0.5, Math.min(10, Math.round(2 * Number(e.target.value)) / 2 || 1));
        e.target.value = G.rec.categorias[c.chave]; salvarReceita(); } });
    return el("div", { class: `cat ${ativo ? "ativa" : ""}`, title: c.dica || "" },
      el("label", {}, el("input", { type: "checkbox", checked: ativo, onchange: (e) => {
        if (e.target.checked) G.rec.categorias[c.chave] = Number(peso.value) || 1; else delete G.rec.categorias[c.chave];
        salvarReceita(); montarClasses();
      } }), el("span", { class: "chip", style: { background: corCategoria(c.chave) } }), ` ${c.rotulo}`),
      el("label", { class: "peso" }, "×", peso));
  }));
  atualizarResumo();
}

// ============================================================================ 4. quantidade e acabamento
function montarQtd() {
  const r = G.rec;
  const num = (valor, mn, mx, aoMudar, passo = 1) => el("input", { type: "number", min: mn, max: mx, step: passo, value: valor,
    onchange: (e) => { aoMudar(Math.max(mn, Math.min(mx, Number(e.target.value) || mn))); salvarReceita(); montarQtd(); } });
  const sel = (valor, opcoes, aoMudar) => {
    const s = el("select", { onchange: (e) => { aoMudar(e.target.value); salvarReceita(); montarQtd(); } },
      ...opcoes.map(([v, rot]) => el("option", { value: v }, rot)));
    s.value = valor;
    return s;
  };
  const faixa = (valor, aoMudar, sufixo) => el("span", {}, el("input", { type: "range", min: 0, max: 100, step: 5,
    value: Math.round(valor * 100),
    oninput: (e) => { aoMudar(Number(e.target.value) / 100); e.target.nextSibling.textContent = ` ${e.target.value}% ${sufixo}`; },
    onchange: () => salvarReceita() }), ` ${Math.round(valor * 100)}% ${sufixo}`);
  const usaIA = Object.keys(r.tecnicas).some((id) => tec(id)?.precisa_ia);
  const comMarcas = !["nenhum", "whatsapp"].includes(r.canal.perfil);  // marca no papel pede captura física
  por($("qtd"), 
    el("label", {}, "Variações por documento"), num(r.variacoes, 1, 50, (v) => { r.variacoes = v; }),
    el("label", {}, "Edições por imagem"), el("span", {}, "de ", num(r.edicoes[0], 1, 10, (v) => { r.edicoes = [v, Math.max(v, r.edicoes[1])]; }),
      " a ", num(r.edicoes[1], 1, 10, (v) => { r.edicoes = [Math.min(v, r.edicoes[0]), v]; })),
    el("label", {}, "Distribuição"), sel(r.distribuicao, [["equilibrada", "equilibrada: cobre técnica × classe por igual"],
      ["cobertura", "cobertura: equilibrada contando o que o split já tem salvo"],
      ["pesos", "pelos pesos (sorteio)"]], (v) => { r.distribuicao = v; }),
    el("label", { title: "Grão do papel nas áreas editadas" }, "Igualar grão"), sel(r.acabamento.ruido, [["auto", "automático (profissional sim, amador não)"],
      ["sempre", "sempre"], ["nunca", "nunca"]], (v) => { r.acabamento.ruido = v; }),
    el("label", { title: "Retoques que falsificadores reais fazem depois da edição (desfoque, aguçar, curvas, cor), nas frequências medidas no FSTS" },
      "Disfarce"), sel(r.acabamento.disfarce || "auto", [["auto", "como falsificadores reais (às vezes)"], ["sempre", "sempre"],
      ["nunca", "nunca"]], (v) => { r.acabamento.disfarce = v; }),
    el("label", { title: "Esconder a edição como um PROFISSIONAL faria, casando na região os sinais que um perito/detector usa: iluminação do papel (tira a 'mancha chapada'), grão com o MESMO espectro do papel (não ruído branco) e nitidez igual à vizinhança. Gere em forense para treinar o detector na ponta mais difícil. auto = pelo nível da técnica e pelo falsificador." },
      "Perícia (esconder)"), sel(r.acabamento.pericia || "auto", [["auto", "automático (pelo falsificador/técnica)"],
      ["cru", "cru: edição limpa, fácil de ver"], ["padrao", "padrão: só igualar o grão"],
      ["profissional", "profissional: + iluminação + nitidez"], ["forense", "forense: + grão espectral + costura"]],
      (v) => { r.acabamento.pericia = v; }),
    el("label", { title: "O caminho até quem verifica: impressão, scanner, foto de celular, tela, WhatsApp. Passa IGUAL na original e na editada de cada par." },
      "Canal de envio"), el("span", {}, sel(r.canal.perfil, G.cat.canais, (v) => { r.canal.perfil = v; }),
      r.canal.perfil !== "nenhum" ? faixa(r.canal.prob, (v) => { r.canal.prob = v; }, "das versões") : null),
    comMarcas ? el("label", { title: "Carimbo, autenticação do banco, anotação à mão, marca-texto, dobra: iguais nas duas imagens; em 20% das versões com marca, uma delas fica por cima da edição (onde ela cobre, a máscara diz o que mudou de fato). Ensinam que marca não é fraude. Só entram quando o canal tem captura física (impressão, scanner, celular, tela)." },
      "Marcas benignas") : null,
    comMarcas ? faixa(r.marcas.prob, (v) => { r.marcas.prob = v; }, "das versões com captura") : null,
    el("label", { title: "Recomprime em JPEG só a área editada, como faz quem edita e salva num editor" }, "JPEG local"),
    el("span", {}, el("input", { type: "range", min: 0, max: 100, step: 5, value: Math.round(r.acabamento.jpeg * 100),
      oninput: (e) => { r.acabamento.jpeg = Number(e.target.value) / 100; e.target.nextSibling.textContent = ` ${e.target.value}% das edições`; },
      onchange: () => salvarReceita() }), ` ${Math.round(r.acabamento.jpeg * 100)}% das edições`),
    el("label", { title: "Por versão, um falsificador (celular, Paint, editor de PDF, Photoshop, IA) com hábitos fixos e um motivo (conta recente, outro titular, outro endereço, valor, apagar) escolhem campos coerentes, valores combinados e as técnicas da ferramenta dele, dentro do que a receita permite. Quem são e com que pesos: modo fácil, bloco 'Quem falsifica'." },
      "Falsificador e motivo"), faixa(r.falsificadores.prob, (v) => { r.falsificadores.prob = v; }, "das versões"),
    el("label", { title: "Por versão, uma PESSOA edita num editor de imagem, com os hábitos dela (frequências medidas em 67 falsificadores, FSTS 2025): seleciona em volta do texto (retângulo, laço, pincel), apaga com a ferramenta dela (preenchimento inteligente, carimbo, pincel de recuperação, cor do papel), escreve com a suavização do Photoshop, pega a cor com o conta-gotas, ajusta tamanho e posição no olho, às vezes desfoca, aguça ou põe ruído, e salva como o programa salva (o JPEG regrava a original também). Nas outras versões, o acabamento da receita (perícia, disfarce)." },
      "Edição como pessoa (editor)"), faixa(r.humano.prob, (v) => { r.humano.prob = v; }, "das versões"),
    el("label", {}, "Formato de saída"), el("span", {}, sel(r.formato, G.cat.formatos.map((f) => [f, f.toUpperCase()]), (v) => { r.formato = v; }),
      ["jpeg", "webp", "pdf"].includes(r.formato) ? el("span", {}, " qualidade ", num(r.qualidade, 1, 100, (v) => { r.qualidade = v; })) : null),
    el("label", {}, "Operador"), el("input", { type: "text", value: r.operador, maxlength: 60, placeholder: "seu nome (opcional)",
      onchange: (e) => { r.operador = e.target.value.trim(); salvarReceita(); } }),
    usaIA ? el("label", {}, "Modelo de IA") : null,
    usaIA ? sel(r.modelo_ia || (G.iaModelos[0]?.id ?? ""), G.iaModelos.map((m) => [m.id, `${m.nome} — ${m.licenca}`]),
      (v) => { r.modelo_ia = v; }) : null,
    el("label", {}, el("input", { type: "checkbox", id: "so-pendentes", checked: r.documentos.so_pendentes,
      onchange: (e) => { r.documentos.so_pendentes = e.target.checked; salvarReceita(); atualizarResumo(); } })),
    el("label", { class: "dica", for: "so-pendentes" }, "pular documentos que já têm versão salva"));
  atualizarResumo();
}

// ============================================================================ resumo e estimativa
const nCatExato = (r) => (r.exato_categorias || []).length;

function atualizarResumo() {
  if (!G.rec) return;
  const r = G.rec;
  const docs = docsAGerar();
  const ef = G.modo === "exato" ? receitaExata() : A ? A.efetivas() : { tecnicas: r.tecnicas, categorias: r.categorias };
  const ids = Object.keys(ef.tecnicas).filter((id) => ef.tecnicas[id] > 0);
  const nIA = ids.filter((id) => tec(id)?.precisa_ia).length;
  const imagens = docs.length * r.variacoes;
  const edicoes = G.modo === "exato" ? imagens * 1.6 * Object.keys(ef.categorias).length : imagens * (r.edicoes[0] + r.edicoes[1]) / 2;
  const fracIA = ids.length ? nIA / ids.length : 0;
  const seg = imagens * SEG_POR_IMAGEM + edicoes * fracIA * SEG_POR_EDICAO_IA;
  const tempo = seg < 90 ? `${Math.max(1, Math.round(seg))} s` : seg < 5400 ? `${Math.round(seg / 60)} min` : `${(seg / 3600).toFixed(1)} h`;
  $("docs-resumo").textContent = `${docs.length} selecionado${docs.length === 1 ? "" : "s"}`;
  $("tec-resumo").textContent = `${ids.length} de ${automaticas().length}`;
  const nCat = Object.keys(ef.categorias).length;
  $("cat-resumo").textContent = `${nCat} ${nCat === 1 ? "classe" : "classes"}`;
  const nImg = `${imagens} ${imagens === 1 ? "imagem" : "imagens"}`;
  $("estimativa").replaceChildren(...[el("b", {}, nImg), ` · ~${Math.round(edicoes)} edições · ≈ ${tempo}`,
    nIA ? el("span", { class: "alerta" }, ` · ${nIA} técnica(s) de IA: bem mais lento`) : null].filter(Boolean));
  $("btn-gerar").textContent = `▶ Gerar ${nImg}`;
  if ($("exato-resumo")) $("exato-resumo").textContent = `${nCatExato(r)} ${nCatExato(r) === 1 ? "classe" : "classes"}`;
  const falta = G.modo === "exato" ? (!nCat ? "marque pelo menos uma coisa para editar" : !imagens ? "escolha documentos" : "")
    : A?.semTiposValendo() ? "crie ou ligue pelo menos um tipo de edição"
    : !ids.length ? "escolha pelo menos uma técnica" : !nCat ? "escolha pelo menos uma classe"
    : !imagens ? (r.documentos.so_pendentes ? "todos os documentos escolhidos já têm versão salva" : "escolha documentos") : "";
  $("btn-gerar").disabled = Boolean(falta) || G.rodando;
  $("btn-gerar").title = G.rodando ? "já há uma geração rodando" : falta;
  $("btn-previa").disabled = !ids.length || !nCat;
  $("btn-previa").title = !ids.length || !nCat ? falta : "roda a receita num documento sem salvar";
  A?.aoResumo();
}

// ============================================================================ prévia
// ``extra`` troca partes da receita (o modo fácil testa um tipo de edição sozinho); ``rotulo`` diz o que foi
async function previa(extra = {}, rotulo = "") {
  const doc = $("previa-doc").value;
  if (!doc) { aviso("selecione pelo menos um documento", "erro"); return; }
  const b = $("btn-previa");
  b.disabled = true; b.textContent = "⏳ gerando prévia…";
  try {
    G.previa = await post("/api/gerador/previa", { receita: { ...receitaParaEnvio(), ...extra }, doc });
    G.previaRotulo = rotulo;
    desenharPrevia();
    if (rotulo) $("painel-previa").scrollIntoView({ behavior: "smooth", block: "start" });
    if (!G.previa.edicoes.length) aviso("Nenhuma técnica coube neste documento com essa receita (veja as tentativas).", "erro");
  } catch (e) { aviso(e.message, "erro"); } finally { b.disabled = false; b.textContent = "👁 Prévia"; }
}

function caixasSobre(alvo, edicoes, W, H, aoClicar) {
  alvo.replaceChildren(...edicoes.map((e, i) => {
    const [x0, y0, x1, y1] = caixaDe(e.bbox);
    const cor = COR_FAMILIA[e.familia] || "#fff";
    return el("div", { class: "caixa", title: `${e.nome_tecnica} · ${rotuloCat(e.categoria)}`,
      style: { left: `${100 * x0 / W}%`, top: `${100 * y0 / H}%`, width: `${100 * (x1 - x0) / W}%`, height: `${100 * (y1 - y0) / H}%`,
        borderColor: cor }, onclick: aoClicar ? () => aoClicar(i) : null },
    el("span", { class: "rotulo-caixa", style: { background: cor } }, String(i + 1)));
  }));
}

function cartaoEdicao(e, i, extra = null) {
  const cor = COR_FAMILIA[e.familia] || "#888";
  const antigo = e.antigo ?? e.texto_antigo, novo = e.novo ?? e.texto_novo;
  return el("div", { class: "edicao", style: { borderLeftColor: cor } },
    el("div", { class: "edicao-cab" },
      el("span", { class: "n", style: { background: cor } }, String(i + 1)),
      el("b", {}, e.nome_tecnica || e.tecnica),
      e.nivel ? el("span", { class: `nivel nivel-${e.nivel}` }, NIVEL[e.nivel] || e.nivel) : null,
      el("span", { class: "cat-chip", style: { borderColor: corCategoria(e.categoria) } }, rotuloCat(e.categoria))),
    antigo !== undefined && antigo !== null && antigo !== "" ? el("div", { class: "textos" },
      el("span", { class: "antigo" }, antigo), " → ", el("span", { class: "novo" }, novo === "" ? "(apagado)" : novo)) : null,
    el("div", { class: "dica" }, e.descricao || ""),
    extra);
}

function desenharPrevia() {
  const r = G.previa;
  if (!r) return;
  $("previa-modos").hidden = false;
  $("previa-titulo").textContent = `— ${G.previaRotulo ? `${G.previaRotulo} · ` : ""}${rotuloSplit(r.split)} · ${r.nome} · `
    + `${r.edicoes.length} ${r.edicoes.length === 1 ? "edição" : "edições"} · nada foi salvo`;
  $("tela-previa").hidden = false;
  document.querySelectorAll("#previa-modos .so-canal").forEach((b) => { b.hidden = !r.canal_editada; });
  if (!r[G.previaModo]) G.previaModo = "editada";
  document.querySelectorAll("#previa-modos button").forEach((b) => b.classList.toggle("ativa", b.dataset.modo === G.previaModo));
  $("previa-img").src = `data:image/png;base64,${r[G.previaModo]}`;
  const pelo = G.previaModo.startsWith("canal_");  // a geometria do canal mudou: caixas só na imagem sem canal
  caixasSobre($("previa-caixas"), pelo ? [] : r.edicoes, r.largura, r.altura,
    (i) => document.querySelectorAll("#previa-lista .edicao")[i]?.scrollIntoView({ behavior: "smooth", block: "center" }));
  const falhas = r.falhas?.length ? el("details", { class: "falhas" },
    el("summary", {}, `${r.falhas.length} tentativa(s) não couberam (normal: a técnica não serve para aquele campo)`),
    el("ul", {}, ...r.falhas.map((f) => el("li", {}, `${tec(f.tecnica)?.nome || f.tecnica} · ${rotuloCat(f.categoria)}: ${f.erro}`)))) : null;
  por($("previa-lista"), ...r.edicoes.map((e, i) => cartaoEdicao(e, i, r.recortes[i] ? el("div", { class: "antes-depois" },
    el("figure", {}, el("img", { src: `data:image/png;base64,${r.recortes[i].antes}`, alt: "antes" }), el("figcaption", {}, "antes")),
    el("figure", {}, el("img", { src: `data:image/png;base64,${r.recortes[i].depois}`, alt: "depois" }), el("figcaption", {}, "depois"))) : null)),
  r.falsificador ? el("div", { class: "dica canal-previa falsificador-previa" },
    `👤 Feita como uma pessoa faria: ${r.falsificador.rotulo.toLowerCase()} · motivo: ${r.falsificador.motivo_rotulo.toLowerCase()}`
    + (r.falsificador.habitos?.fonte ? ` · fonte ${r.falsificador.habitos.fonte}` : "") + ".") : null,
  r.canal ? el("div", { class: "dica canal-previa" }, `Canal sorteado nesta prévia: ${r.canal}.`,
    r.captura_antes ? ` A captura (${r.captura_antes}) veio ANTES da edição: a imagem "original" acima já é a foto/scan, e a edição foi feita por cima dela.` : "",
    r.marcas ? ` Marcas benignas: ${r.marcas}.` : "") : null,
  r.classes ? el("div", { class: "dica canal-previa" }, `Classes desta versão: ${r.classes.map(rotuloCat).join(", ")}.`,
    r.ocorrencias_antigas?.length ? ` Ficaram com o dado antigo (vai na anotação): `
      + r.ocorrencias_antigas.map((o) => `"${o.texto}" (${o.motivo})`).join("; ") + "." : "") : null,
  falhas);
}

// ============================================================================ geração
async function gerar() {
  const r = receitaParaEnvio();
  const imagens = docsAGerar().length * r.variacoes;
  if (imagens > 300 && !confirm(`Gerar ${imagens} imagens? Isso pode levar bastante tempo.`)) return;
  pedirPermissaoNotificacao();
  try {
    await post("/api/gerador/iniciar", { receita: r });
    aviso("Geração iniciada. Pode continuar usando o editor em outra aba.", "ok");
    G.ultimoStatus = null;
    acompanhar();
    setTimeout(() => $("painel-progresso").scrollIntoView({ behavior: "smooth", block: "start" }), 400);
  } catch (e) { aviso(e.message, "erro"); }
}

// acompanha sempre: 1 s enquanto roda, 4 s parado (a geração pode ter começado em outra aba)
function acompanhar() {
  clearTimeout(G.timer);
  const ciclo = async () => {
    const rodando = await atualizarStatus();
    G.timer = setTimeout(ciclo, rodando ? 1000 : 4000);
  };
  ciclo();
}

async function atualizarStatus() {
  let st;
  try { st = await get("/api/gerador/status"); } catch { return false; }
  const pill = $("lote-info");
  if (!st.total) { $("painel-progresso").hidden = true; pill.hidden = true; U?.aoStatus(st); return false; }
  const mudou = G.ultimoStatus !== `${st.id}|${st.rodando}|${st.feitas}|${st.falhas}`;
  G.ultimoStatus = `${st.id}|${st.rodando}|${st.feitas}|${st.falhas}`;
  if (!mudou) return st.rodando;  // nada novo: não redesenha (miniaturas não piscam)
  $("painel-progresso").hidden = false;
  const pct = Math.round(100 * (st.feitas + st.falhas) / st.total);
  $("prog-barra").style.width = `${pct}%`;
  const resta = st.restante_s ? ` · faltam ~${st.restante_s < 120 ? `${st.restante_s} s` : `${Math.ceil(st.restante_s / 60)} min`}` : "";
  $("prog-titulo").textContent = st.rodando ? "rodando…" : st.interrompido ? "parada" : "terminada";
  const quem = Object.entries(st.por_falsificador || {}).map(([k, n]) => `${(G.cat.falsificadores.find((f) => f.id === k)?.rotulo || k).toLowerCase()} ${n}`);
  $("prog-texto").textContent = (st.rodando
    ? `${st.feitas} de ${st.total} imagens salvas (${pct}%) · ${st.edicoes} edições${resta} · agora: ${st.atual}`
    : `${st.feitas} imagens salvas, ${st.falhas} falharam, ${st.edicoes} edições.`) + (quem.length ? ` · falsificadores: ${quem.join(", ")}` : "");
  const pt = Object.entries(st.por_tecnica || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...pt.map(([, n]) => n));
  $("prog-tecnicas").replaceChildren(...pt.map(([id, n]) => {
    const t = tec(id);
    return el("div", { class: "barra-tec", title: `${n} ${n === 1 ? "edição" : "edições"}` },
      el("span", { class: "nome" }, t?.nome || id),
      el("span", { class: "trilho" }, el("span", { style: { width: `${100 * n / max}%`, background: COR_FAMILIA[t?.familia] || "#888" } })),
      el("span", { class: "n" }, String(n)));
  }));
  $("prog-recentes").replaceChildren(...(st.recentes || []).map((v) => miniatura(v, () => abrirDetalhe({ ...v,
    edicoes: v.edicoes.map((e) => ({ ...e, texto_antigo: e.antigo, texto_novo: e.novo })) }))));
  $("prog-erros-box").hidden = !st.erros?.length;
  $("prog-erros").replaceChildren(...(st.erros || []).slice(-20).map((e) => el("li", {}, e)));
  $("btn-parar").disabled = !st.rodando;
  if (G.rodando !== Boolean(st.rodando)) { G.rodando = Boolean(st.rodando); atualizarResumo(); }
  U?.aoStatus(st);
  pill.hidden = false;
  pill.textContent = st.rodando ? `gerando ${st.feitas}/${st.total}` : `última geração: ${st.feitas} imagens`;
  pill.className = `pilula ${st.rodando ? "ok" : ""}`;
  if (!st.rodando && G.rodava) {  // acabou agora: atualiza contagens, catálogo e exemplos reais
    notificar("Geração terminou", `${st.feitas} imagens salvas, ${st.falhas} falharam.`);
    G.docs = await get("/api/gerador/documentos"); desenharDocs(); U?.redesenhar();
    A.recarregarExemplos();
    if (!$("aba-catalogo").hidden) abrirCatalogo();
  }
  G.rodava = st.rodando;
  return st.rodando;
}

function miniatura(v, aoClicar) {
  const bs = (v.edicoes || []).map((e) => caixaDe(e.bbox)).filter((b) => b.length === 4);
  let q = "";
  if (bs.length) {
    const x0 = Math.min(...bs.map((b) => b[0])), y0 = Math.min(...bs.map((b) => b[1]));
    const x1 = Math.max(...bs.map((b) => b[2])), y1 = Math.max(...bs.map((b) => b[3]));
    const m = Math.max(60, 0.25 * Math.max(x1 - x0, y1 - y0));
    q = `&x0=${Math.round(x0 - m)}&y0=${Math.round(y0 - m)}&x1=${Math.round(x1 + m)}&y1=${Math.round(y1 + m)}`;
  }
  return el("button", { type: "button", class: "mini-versao", title: v.nome, onclick: aoClicar },
    el("img", { src: `/api/catalogo/imagem/${v.split}/${v.nome}/sobreposicao.png?max=360${q}`, loading: "lazy", alt: "",
      onerror: (e) => { e.target.replaceWith(el("span", { class: "sem-imagem" }, "versão excluída")); } }),
    el("span", { class: "legenda" }, el("span", { class: `pilula split-${v.split}` }, rotuloSplit(v.split)), ` ${v.nome.split("__")[1]}`),
    el("span", { class: "chips" }, ...[...new Set((v.edicoes || []).map((e) => e.familia))].map((f) =>
      el("span", { class: "chip", style: { background: COR_FAMILIA[f] || "#888" }, title: G.cat.familias[f]?.rotulo || f }))));
}

// ============================================================================ catálogo
async function abrirCatalogo() {
  let res;
  try { res = await get("/api/catalogo/resumo"); } catch (e) { aviso(e.message, "erro"); return; }
  if (!res.versoes) {  // nada gerado: um caminho claro em vez de uma página vazia
    por($("cat-cartoes")); por($("cat-matriz")); por($("cat-filtros")); por($("cat-lote")); por($("cat-paginas"));
    por($("cat-galeria"), el("div", { class: "vazio-grande" }, el("b", {}, "Nada gerado ainda."),
      el("p", { class: "dica" }, "O catálogo mostra cada versão salva pelo gerador ou pelo editor, com o que foi mudado e onde."),
      el("button", { type: "button", class: "primario grande", onclick: () => trocarAba("gerar") }, "⚙ Ir para Gerar")));
    return;
  }
  const cartao = (n, rot, extra = "") => el("div", { class: "cartao-num" }, el("b", {}, String(n)), el("span", {}, rot), extra ? el("span", { class: "dica" }, extra) : null);
  $("cat-cartoes").replaceChildren(
    cartao(res.versoes, "versões salvas", SPLITS.map(([sp, r]) => `${r}: ${res.por_split[sp] || 0}`).join(" · ")),
    cartao(res.edicoes, "edições rotuladas", Object.entries(res.por_origem).map(([o, n]) => `${o}: ${n}`).join(" · ")),
    cartao(Object.keys(res.por_tecnica).length, "técnicas usadas", "edições por nível: " + Object.entries(res.por_nivel).map(([k, n]) => `${NIVEL[k] || k} ${n}`).join(" · ")),
    cartao(Object.keys(res.por_categoria).length, "classes editadas"));
  desenharMatriz(res);
  montarFiltros();
  carregarGaleria();
}

function desenharMatriz(res) {
  const tecs = Object.keys(res.por_tecnica);
  const cats = Object.keys(res.por_categoria);
  if (!tecs.length) { $("cat-matriz").replaceChildren(el("p", { class: "dica" }, "Nada gerado ainda.")); return; }
  const n = new Map(res.matriz.map((m) => [`${m.tecnica}|${m.categoria}`, m.n]));
  const max = Math.max(1, ...res.matriz.map((m) => m.n));
  $("cat-matriz").replaceChildren(el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "técnica \\ classe"), ...cats.map((c) => el("th", { title: rotuloCat(c) },
      el("span", { class: "chip", style: { background: corCategoria(c) } }), rotuloCat(c))), el("th", {}, "total"))),
    el("tbody", {}, ...tecs.map((tid) => {
      const t = tec(tid);
      return el("tr", {}, el("th", { class: "tec-col" }, el("span", { class: "chip", style: { background: COR_FAMILIA[t?.familia] || "#888" } }), t?.nome || tid),
        ...cats.map((c) => {
          const v = n.get(`${tid}|${c}`) || 0;
          return el("td", { class: v ? "tem" : "", style: v ? { background: `rgba(61,139,253,${0.15 + 0.75 * v / max})` } : null,
            title: `${t?.nome || tid} × ${rotuloCat(c)}: ${v}`,
            onclick: v ? () => { G.filtro = { tecnica: tid, categoria: c }; G.pagina = 0; montarFiltros(); carregarGaleria(); } : null }, v || "");
        }), el("td", { class: "total" }, String(res.por_tecnica[tid])));
    }))));
}

function montarFiltros() {
  const f = G.filtro;
  const sel = (chave, rot, opcoes) => {
    const s = el("select", { onchange: (e) => { if (e.target.value) f[chave] = e.target.value; else delete f[chave]; G.pagina = 0; carregarGaleria(); } },
      el("option", { value: "" }, `${rot}: todos`), ...opcoes.map(([v, r]) => el("option", { value: v }, r)));
    s.value = f[chave] || "";
    return s;
  };
  const busca = el("input", { type: "search", placeholder: "buscar texto/descrição…", value: f.q || "",
    onchange: (e) => { if (e.target.value.trim()) f.q = e.target.value.trim(); else delete f.q; G.pagina = 0; carregarGaleria(); } });
  const operador = el("input", { type: "search", placeholder: "operador…", value: f.operador || "", "aria-label": "filtrar por operador",
    onchange: (e) => { if (e.target.value.trim()) f.operador = e.target.value.trim(); else delete f.operador; G.pagina = 0; carregarGaleria(); } });
  $("cat-filtros").replaceChildren(
    sel("split", "split", SPLITS),
    sel("tecnica", "técnica", G.cat.tecnicas.map((t) => [t.id, t.nome])),
    sel("exceto_tecnica", "sem a técnica", G.cat.tecnicas.filter((t) => t.automatica).map((t) => [t.id, t.nome])),
    sel("categoria", "classe", G.cat.categorias.map((c) => [c.chave, c.rotulo])),
    sel("nivel", "nível", Object.entries(G.cat.niveis)),
    sel("familia", "família", Object.entries(G.cat.familias).map(([k, v]) => [k, v.rotulo])),
    sel("origem", "origem", [["gerador", "gerador"], ["automatico", "automático (editor)"], ["assistido", "assistido"],
      ["manual", "manual"]]),
    sel("revisao", "revisão", [["pendente", "pendente"], ["aprovada", "aprovada"], ["reprovada", "reprovada"]]),
    sel("pericia", "perícia", [["auto", "sem perícia"], ["padrao", "padrão"], ["profissional", "profissional"], ["forense", "forense"]]),
    sel("canal", "canal", [["com", "com canal"], ["sem", "sem canal"], ...G.cat.canais.filter(([k]) => k !== "nenhum")]),
    sel("falsificador", "falsificador", [["nenhum", "sorteio clássico / manual"], ...G.cat.falsificadores.map((f) => [f.id, f.rotulo])]),
    sel("motivo", "motivo", [["nenhum", "sem motivo"], ...G.cat.motivos.filter((m) => m.id !== "misto").map((m) => [m.id, m.rotulo])]),
    operador,
    busca,
    el("button", { type: "button", onclick: () => { G.filtro = {}; G.pagina = 0; montarFiltros(); carregarGaleria(); } }, "limpar filtros"),
    el("button", { type: "button", title: "Grava um manifesto (CSV e JSON) só com as versões destes filtros, para treinar com elas",
      onclick: exportarFiltrados }, "⤓ exportar filtrados"),
    el("button", { type: "button", title: "Refaz manifesto.csv e catalogo.csv a partir dos JSON", onclick: async () => {
      try { await post("/api/catalogo/reindexar", {}); aviso("Índices refeitos.", "ok"); abrirCatalogo(); } catch (e) { aviso(e.message, "erro"); }
    } }, "🔄 reindexar"));
}

async function carregarGaleria() {
  const q = new URLSearchParams({ ...G.filtro, pagina: G.pagina, por_pagina: 24 });
  let r;
  try { r = await get(`/api/catalogo/itens?${q}`); } catch (e) { aviso(e.message, "erro"); return; }
  G.paginaAtual = r.itens;
  $("cat-galeria").replaceChildren(...(r.itens.length ? r.itens.map((v) => {
    const chave = `${v.split}|${v.nome}`;
    const card = miniatura(v, () => abrirDetalhe(v));
    card.append(el("span", { class: "tecs" }, ...[...new Set(v.edicoes.map((e) => e.nome_tecnica))].map((n) => el("span", {}, n))));
    const [selo, cls] = { aprovada: ["✔", "ok"], reprovada: ["✖", "ruim"] }[v.revisao] || [];
    return el("div", { class: `cartao-versao${G.marcados.has(chave) ? " marcado" : ""}` },
      el("input", { type: "checkbox", class: "marcar-versao", checked: G.marcados.has(chave), "aria-label": `marcar ${v.nome}`,
        onchange: (e) => { e.target.checked ? G.marcados.set(chave, { split: v.split, nome: v.nome }) : G.marcados.delete(chave);
          e.target.parentElement.classList.toggle("marcado", e.target.checked); desenharLote(); } }),
      selo ? el("span", { class: `pilula selo-revisao ${cls}`, title: `${v.revisao}${v.revisor ? ` por ${v.revisor}` : ""}` }, selo) : null,
      card);
  }) : [el("p", { class: "dica" }, "Nenhuma versão com esses filtros.")]));
  desenharLote();
  const paginas = Math.ceil(r.total / r.por_pagina);
  $("cat-paginas").replaceChildren(
    el("button", { type: "button", "aria-label": "página anterior", disabled: G.pagina <= 0, onclick: () => { G.pagina--; carregarGaleria(); } }, "◀"),
    el("span", { class: "dica" }, ` ${r.total} ${r.total === 1 ? "versão" : "versões"} · página ${G.pagina + 1} de ${Math.max(1, paginas)} `),
    el("button", { type: "button", "aria-label": "próxima página", disabled: G.pagina + 1 >= paginas, onclick: () => { G.pagina++; carregarGaleria(); } }, "▶"));
}

// ------------------------------------------------------------------ curadoria em lote
function desenharLote() {
  const n = G.marcados.size;
  por($("cat-lote"),
    el("button", { type: "button", onclick: () => {
      for (const v of G.paginaAtual || []) G.marcados.set(`${v.split}|${v.nome}`, { split: v.split, nome: v.nome });
      carregarGaleria();
    } }, "☑ marcar esta página"),
    n ? [el("b", {}, `${n} marcada${n > 1 ? "s" : ""}:`),
      el("button", { type: "button", onclick: () => revisarMarcadas("aprovada") }, "✔ aprovar"),
      el("button", { type: "button", onclick: () => revisarMarcadas("reprovada") }, "✖ reprovar…"),
      el("button", { type: "button", onclick: () => revisarMarcadas("pendente") }, "↺ voltar a pendente"),
      el("button", { type: "button", class: "perigo", onclick: excluirMarcadas }, "🗑 excluir"),
      el("button", { type: "button", onclick: () => { G.marcados.clear(); carregarGaleria(); } }, "desmarcar")]
      : el("span", { class: "dica" }, "Marque versões (caixinha no canto) para aprovar, reprovar ou excluir várias de uma vez."));
}
async function revisarMarcadas(estado) {
  const itens = [...G.marcados.values()];
  let extra = {};
  if (estado === "reprovada") {
    const m = await P.pedirMotivos(`Reprovar ${itens.length} versão(ões)`);
    if (!m) return;
    extra = m;
  }
  try {
    const n = await P.revisar(itens, estado, extra);
    aviso(`${n} versão(ões) ${{ aprovada: "aprovada(s)", reprovada: "reprovada(s)", pendente: "de volta a pendente" }[estado]}.`, "ok");
    G.marcados.clear();
    abrirCatalogo();
  } catch (e) { aviso(e.message, "erro"); }
}
async function excluirMarcadas() {
  const itens = [...G.marcados.values()];
  if (!confirm(`Excluir ${itens.length} versão(ões)? A imagem editada, as máscaras e a anotação de cada uma são apagadas.`)) return;
  let ok = 0;
  for (const v of itens) {
    try { await post("/api/catalogo/excluir", v); ok++; } catch (e) { aviso(`${v.nome}: ${e.message}`, "erro"); }
  }
  aviso(`${ok} versão(ões) excluída(s).`, "ok");
  G.marcados.clear();
  abrirCatalogo();
  G.docs = await get("/api/gerador/documentos"); desenharDocs();
}
async function exportarFiltrados() {
  const nome = prompt("Nome da exportação (letras, números, - e _):", `selecao-${new Date().toISOString().slice(0, 10)}`);
  if (!nome) return;
  try {
    const r = await post("/api/catalogo/exportar", { nome, filtros: G.filtro });
    por($("cat-exportacao"), `✔ ${r.versoes} versões (${SPLITS.map(([sp, rot]) => `${rot} ${r.por_split[sp] || 0}`).join(", ")}) em saida/_exportacoes/${r.nome}/ — `,
      el("a", { href: `/api/catalogo/exportacao/${r.nome}/manifesto.csv` }, "manifesto.csv"), " · ",
      el("a", { href: `/api/catalogo/exportacao/${r.nome}/manifesto.json` }, "manifesto.json"));
  } catch (e) { aviso(e.message, "erro"); }
}

// ------------------------------------------------------------------ detalhe de uma versão
function abrirDetalhe(v) {
  G.detalhe = { v, modo: "sobreposicao", zoom: null };
  desenharSeloDetalhe();
  $("det-titulo").textContent = `${rotuloSplit(v.split)} · ${v.nome}${v.documento ? ` · ${v.documento}` : ""}`;
  desenharDetalhe();
  const d = $("detalhe");
  if (!d.open) d.showModal();
}

function desenharDetalhe() {
  const { v, modo, zoom } = G.detalhe;
  const modos = [["sobreposicao", "Sobreposição"], ["editada", "Editada"], ["original", "Original"], ["tecnicas", "Máscara de técnicas"], ["mascara", "Pixels alterados"]];
  por($("det-modos"), ...modos.map(([m, r]) => el("button", { type: "button", class: m === modo ? "ativa" : "",
    onclick: () => { G.detalhe.modo = m; desenharDetalhe(); } }, r)),
    zoom ? el("button", { type: "button", onclick: () => { G.detalhe.zoom = null; desenharDetalhe(); } }, "🔍 ver inteira") : null);
  let q = "max=1400";
  if (zoom) {
    const [x0, y0, x1, y1] = zoom;
    const m = Math.max(40, 0.6 * (y1 - y0));
    q = `max=1100&x0=${Math.round(x0 - 3 * m)}&y0=${Math.round(y0 - m)}&x1=${Math.round(x1 + 3 * m)}&y1=${Math.round(y1 + m)}`;
  }
  const img = $("det-img");
  img.src = `/api/catalogo/imagem/${v.split}/${v.nome}/${modo}.png?${q}`;
  const eds = v.edicoes || [];
  img.onload = () => {  // caixas em % do documento inteiro (a imagem mostrada mantém a proporção)
    if (zoom || !v.largura) { $("det-caixas").replaceChildren(); return; }
    caixasSobre($("det-caixas"), eds, v.largura, v.altura, (i) => { G.detalhe.zoom = caixaDe(eds[i].bbox); desenharDetalhe(); });
  };
  $("det-lista").replaceChildren(...eds.map((e, i) => {
    const c = cartaoEdicao(e, i);
    c.classList.add("clicavel");
    c.title = "Clique para ampliar esta edição";
    c.onclick = () => { G.detalhe.zoom = caixaDe(e.bbox); desenharDetalhe(); };
    return c;
  }));
}

function desenharSeloDetalhe() {
  const v = G.detalhe.v, r = v.revisao || "pendente";
  por($("det-selo"), el("span", { class: `pilula ${{ aprovada: "ok", reprovada: "ruim" }[r] || ""}` },
    r === "pendente" ? "revisão pendente" : `${r}${v.revisor ? ` por ${v.revisor}` : ""}`));
}
async function revisarDetalhe(estado) {
  const { v } = G.detalhe;
  let extra = {};
  if (estado === "reprovada") {
    const m = await P.pedirMotivos(`Reprovar ${v.nome}`);
    if (!m) return;
    extra = m;
  }
  try {
    await P.revisar([v], estado, extra);
    v.revisao = estado; v.revisor = nomeOperador();
    desenharSeloDetalhe();
    aviso(`Versão ${v.nome} ${estado}.`, "ok");
    if (!$("aba-catalogo").hidden) carregarGaleria();
  } catch (e) { aviso(e.message, "erro"); }
}

async function excluirDetalhe() {
  const { v } = G.detalhe;
  if (!confirm(`Excluir a versão ${v.nome} (${rotuloSplit(v.split)})? A imagem editada, as máscaras e a anotação serão apagadas.`)) return;
  try {
    await post("/api/catalogo/excluir", { split: v.split, nome: v.nome });
    $("detalhe").close();
    aviso(`Versão ${v.nome} excluída.`, "ok");
    abrirCatalogo();
    G.docs = await get("/api/gerador/documentos"); desenharDocs();
  } catch (e) { aviso(e.message, "erro"); }
}

// ============================================================================ IA
async function verificarIA() {
  try {
    const st = await get("/api/ia/status");
    const mudou = G.iaOnline !== !!st.online;
    G.iaOnline = !!st.online;
    G.iaModelos = st.modelos || [];
    $("ia-pill").textContent = st.online ? `IA online (${st.modelo_id || "?"})` : "IA desligada";
    $("ia-pill").className = `pilula ${st.online ? "ok" : ""}`;
    if (mudou) { montarTecnicas(); montarQtd(); A?.redesenhar(); }
  } catch { /* servidor do rotulador ocupado: tenta de novo depois */ }
}

// ============================================================================ metas → receita
function gerarOQueFalta(sp, falta) {
  const faltam = Object.entries(falta || {}).filter(([, n]) => n > 0);
  if (!faltam.length) return;
  const max = Math.max(...faltam.map(([, n]) => n));
  const total = faltam.reduce((s, [, n]) => s + n, 0);
  G.rec.documentos = { splits: [sp], ids: null, so_pendentes: false };
  for (const d of G.docs[sp] || []) G.sel.add(d.id);
  G.rec.categorias = Object.fromEntries(faltam.map(([k, n]) => [k, Math.max(0.5, Math.round(20 * n / max) / 2)]));
  if (!Object.keys(G.rec.tecnicas).length) G.rec.tecnicas = receitaPadrao().tecnicas;
  G.rec.distribuicao = "pesos";
  const medio = (G.rec.edicoes[0] + G.rec.edicoes[1]) / 2;
  G.rec.variacoes = Math.max(1, Math.min(50, Math.ceil(total / (Math.max(1, (G.docs[sp] || []).length) * medio))));
  trocarAba("gerar");
  $("opcoes-avancadas").open = true;
  A.trocarModo("avancado");
  G.rec.tipos_desligados = true;  // a receita das metas é por classe: os tipos do modo fácil ficam guardados
  salvarReceita(); montarDocs(); montarTecnicas(); montarClasses(); montarQtd();
  aviso(`Receita pronta para o ${rotuloSplit(sp)}: ~${total} edições em ${faltam.length} classe(s). Confira e clique em Gerar.`, "ok");
}

// ============================================================================ abas e ligações
function trocarAba(aba) {
  if (!ABAS.includes(aba)) aba = "gerar";
  for (const b of document.querySelectorAll("#abas button")) {
    b.classList.toggle("ativa", b.dataset.aba === aba);
    b.setAttribute("aria-current", b.dataset.aba === aba ? "page" : "false");
  }
  for (const a of ABAS) $(`aba-${a}`).hidden = a !== aba;
  guardar("abaGerador", aba);
  if (aba === "catalogo") abrirCatalogo();
  if (aba === "revisar") P.abrirRevisar();
  if (aba === "metas") P.abrirMetas();
  if (aba === "equipe") P.abrirEquipe();
}
function mostrarQuem() { $("btn-quem").textContent = `👤 ${nomeOperador()}`; }

async function iniciar() {
  try {
    [G.cat, G.docs] = await Promise.all([get("/api/catalogo"), get("/api/gerador/documentos")]);
  } catch (e) { aviso(`Não consegui carregar o gerador: ${e.message}`, "erro"); return; }
  await exigirOperador();  // o nome vai nas revisões e atribuições
  mostrarQuem();
  $("btn-quem").onclick = () => {
    const n = prompt("Seu nome:", nomeOperador());
    if (n !== null && !definirOperador(n)) aviso("O nome não pode ficar vazio.", "erro");
  };
  window.addEventListener("operador", mostrarQuem);
  G.marcados = new Map();  // versões marcadas no catálogo: "split|nome" -> {split, nome}
  G.rec = normalizar(lembrar("receitaGerador", null));
  const todos = Object.values(G.docs).flat().map((d) => d.id);
  G.sel = new Set(G.rec.documentos.ids ? G.rec.documentos.ids.filter((id) => todos.includes(id)) : todos);
  montarDocs(); montarTecnicas(); montarClasses(); montarQtd(); montarSubconjuntos();
  A = instalarAssistente({ G, $, el, por, aviso, guardar, tec, rotuloCat, get, salvarReceita, atualizarResumo, montarQtd,
    previa, COR_FAMILIA });
  P = instalarPainel({ G, $, el, por, aviso, get, post, rotuloCat, rotuloSplit, SPLITS, corCategoria, cartaoEdicao, caixasSobre,
    caixaDe, trocarAba, gerarOQueFalta });
  U = instalarUmClique({ G, $, el, por, aviso, guardar, lembrar, get, post, rotuloCat, rotuloSplit, SPLITS, caixaDe, corCategoria,
    tec, trocarAba, abrirDetalhe, nomeOperador, pedirPermissaoNotificacao,
    recomecarStatus: () => { G.ultimoStatus = null; acompanhar(); } });
  const avancadas = $("opcoes-avancadas");  // a receita completa: recolhida, a não ser que a pessoa a deixe aberta
  avancadas.open = lembrar("geradorAvancado", false) === true;
  avancadas.addEventListener("toggle", () => guardar("geradorAvancado", avancadas.open));
  atualizarResumo();
  $("docs-busca").addEventListener("input", desenharDocs);
  $("docs-todos").onclick = () => selecionarDocs(() => true);
  $("docs-nenhum").onclick = () => selecionarDocs(() => false);
  $("docs-pendentes").onclick = () => selecionarDocs((d) => !d.versoes);
  $("docs-inverter").onclick = () => selecionarDocs((d) => !G.sel.has(d.id));
  $("btn-previa").onclick = () => previa();
  $("btn-gerar").onclick = gerar;
  $("btn-parar").onclick = async () => { await post("/api/gerador/parar", {}); G.ultimoStatus = null; atualizarStatus(); };
  for (const b of document.querySelectorAll("#previa-modos button")) {
    b.onclick = () => {
      G.previaModo = b.dataset.modo;
      for (const x of document.querySelectorAll("#previa-modos button")) x.classList.toggle("ativa", x === b);
      desenharPrevia();
    };
  }
  for (const b of document.querySelectorAll("#abas button")) b.onclick = () => trocarAba(b.dataset.aba);
  $("det-excluir").onclick = excluirDetalhe;
  $("det-aprovar").onclick = () => revisarDetalhe("aprovada");
  $("det-reprovar").onclick = () => revisarDetalhe("reprovada");
  $("btn-exportar-receita").onclick = () => {
    const blob = new Blob([JSON.stringify(G.rec, null, 1)], { type: "application/json" });
    el("a", { href: URL.createObjectURL(blob), download: `${G.rec.nome || "receita"}.json` }).click();
  };
  $("importar-receita").onchange = async (e) => {
    const arq = e.target.files[0];
    if (!arq) return;
    try {
      const dados = JSON.parse(await arq.text());
      if (!dados || typeof dados !== "object" || Array.isArray(dados)) throw new Error("o arquivo não é uma receita (esperava um objeto JSON)");
      G.rec = normalizar(dados);
      const todos = Object.values(G.docs).flat().map((d) => d.id);
      G.sel = new Set(G.rec.documentos.ids ? G.rec.documentos.ids.filter((id) => todos.includes(id)) : todos);
      salvarReceita(); montarDocs(); montarTecnicas(); montarClasses(); montarQtd(); montarSubconjuntos();
      A.aoCarregar();
      aviso("Receita carregada.", "ok");
    } catch (err) { aviso(`Receita inválida: ${err.message}`, "erro"); }
    e.target.value = "";
  };
  await verificarIA();
  setInterval(verificarIA, 30000);
  acompanhar();
  trocarAba(new URLSearchParams(location.search).get("aba") || lembrar("abaGerador", "gerar"));
}

iniciar();
