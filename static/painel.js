// Abas de equipe do gerador:
//   Revisar — fila de versões salvas para uma pessoa aprovar ou reprovar (com motivo); A/R/→/← no teclado;
//   Metas   — quantas edições de cada classe em cada split, o feito e o que falta (e "gerar o que falta");
//   Equipe  — o que cada pessoa fez, atribuição de documentos e quem está editando o quê agora.
// O nome de quem revisa/atribui vem do cabeçalho X-Operador (api.js).

const ESTADOS = [["pendente", "pendentes"], ["reprovada", "reprovadas"], ["aprovada", "aprovadas"], ["todas", "todas"]];
const MODOS = [["sobreposicao", "Sobreposição"], ["editada", "Editada"], ["original", "Original"], ["tecnicas", "Máscara de técnicas"]];
const SELO = { aprovada: ["✔ aprovada", "ok"], reprovada: ["✖ reprovada", "ruim"], pendente: ["pendente", ""] };

export function instalarPainel(g) {
  const { G, $, el, por, aviso, get, post, rotuloCat, rotuloSplit, SPLITS } = g;
  const R = { fila: [], i: 0, estado: "pendente", split: "", origem: "", modo: "sobreposicao", zoom: null, motivos: new Set(),
    contagem: {}, textoMotivos: G.cat.motivos_revisao || {}, total: 0 };

  // ------------------------------------------------------------------ motivos de reprovação (diálogo, para o catálogo)
  function pedirMotivos(titulo) {
    return new Promise((resolver) => {
      const marcados = new Set();
      const coment = el("textarea", { rows: 2, placeholder: "comentário (opcional se marcar um motivo)", "aria-label": "comentário" });
      const dlg = el("dialog", { class: "dlg-motivos" });
      const fim = (v) => { dlg.close(); dlg.remove(); resolver(v); };
      por(dlg, el("form", { method: "dialog", onsubmit: (e) => {
        e.preventDefault();
        if (!marcados.size && !coment.value.trim()) { aviso("Diga o motivo da reprovação.", "erro"); return; }
        fim({ motivos: [...marcados], comentario: coment.value.trim() });
      } },
      el("h3", {}, titulo),
      el("div", { class: "motivos" }, ...Object.entries(R.textoMotivos).map(([k, txt]) => el("button", { type: "button", class: "motivo",
        "aria-pressed": "false", onclick: (e) => {
          marcados.has(k) ? marcados.delete(k) : marcados.add(k);
          e.currentTarget.classList.toggle("on", marcados.has(k)); e.currentTarget.setAttribute("aria-pressed", String(marcados.has(k)));
        } }, txt))),
      coment,
      el("div", { class: "modal-acoes" }, el("button", { type: "button", onclick: () => fim(null) }, "Cancelar"),
        el("button", { type: "submit", class: "perigo" }, "✖ Reprovar"))));
      dlg.addEventListener("cancel", () => fim(null));
      document.body.append(dlg);
      dlg.showModal();
    });
  }
  async function revisar(itens, estado, extra = {}) {
    const r = await post("/api/revisao", { itens: itens.map(({ split, nome }) => ({ split, nome })), estado, ...extra });
    return r.revisadas;
  }

  // ------------------------------------------------------------------ Revisar
  const aba = $("aba-revisar");
  por(aba,
    el("section", { class: "coluna rev-imagem" },
      el("div", { class: "rev-barra" },
        el("label", {}, "Mostrar ", el("select", { id: "rev-estado", onchange: (e) => { R.estado = e.target.value; carregarFila(); } },
          ...ESTADOS.map(([v, r]) => el("option", { value: v }, r)))),
        el("select", { id: "rev-split", "aria-label": "split", onchange: (e) => { R.split = e.target.value; carregarFila(); } },
          el("option", { value: "" }, "todos os splits"), ...SPLITS.map(([s, r]) => el("option", { value: s }, r))),
        el("select", { id: "rev-origem", "aria-label": "origem", onchange: (e) => { R.origem = e.target.value; carregarFila(); } },
          el("option", { value: "" }, "todas as origens"), el("option", { value: "gerador" }, "só gerador"),
          el("option", { value: "automatico" }, "só automático (editor)"), el("option", { value: "assistido" }, "só assistido"),
          el("option", { value: "manual" }, "só manual")),
        el("span", { id: "rev-pos", class: "pilula" }), el("span", { id: "rev-contagem", class: "dica" })),
      el("div", { class: "det-modos", id: "rev-modos" }),
      el("div", { class: "rev-vazio", id: "rev-vazio", hidden: true, role: "status" }),
      el("div", { class: "det-imagem rev-img", id: "rev-quadro" }, el("img", { id: "rev-img", alt: "" }), el("div", { id: "rev-caixas" }))),
    el("section", { class: "coluna rev-lado" },
      el("h2", { id: "rev-titulo" }),
      el("div", { id: "rev-selo" }),
      el("div", { id: "rev-edicoes", class: "lista-edicoes" }),
      el("div", { class: "rev-decisao" },
        el("div", { class: "dica" }, "Se reprovar, marque o motivo (teclas 1–6):"),
        el("div", { id: "rev-motivos", class: "motivos" }),
        el("textarea", { id: "rev-comentario", rows: 2, placeholder: "comentário (opcional)", "aria-label": "comentário da revisão" }),
        el("div", { class: "linha rev-botoes" },
          el("button", { type: "button", id: "rev-voltar", title: "Voltar (←)", onclick: () => mover(-1) }, "← Voltar"),
          el("button", { type: "button", id: "rev-pular", title: "Pular sem decidir (→)", onclick: () => mover(1) }, "Pular →"),
          el("button", { type: "button", id: "rev-reprovar", class: "perigo grande", onclick: () => decidir("reprovada") }, "✖ Reprovar (R)"),
          el("button", { type: "button", id: "rev-aprovar", class: "primario grande", onclick: () => decidir("aprovada") }, "✔ Aprovar (A)")),
        el("p", { class: "dica" }, "A aprova · R reprova · 1–6 marcam o motivo · → pula · ← volta. Clique numa edição para ampliar."))));

  async function carregarFila() {
    const q = new URLSearchParams({ revisao: R.estado });
    if (R.split) q.set("split", R.split);
    if (R.origem) q.set("origem", R.origem);
    try {
      const r = await get(`/api/revisao/fila?${q}`);
      Object.assign(R, { fila: r.itens, total: r.total, contagem: r.contagem, textoMotivos: r.motivos, i: 0, zoom: null });
    } catch (e) { aviso(e.message, "erro"); return; }
    desenharRevisar();
  }
  function desenharRevisar() {
    const c = R.contagem;
    $("rev-contagem").textContent = `pendentes ${c.pendente || 0} · aprovadas ${c.aprovada || 0} · reprovadas ${c.reprovada || 0}`;
    const v = R.fila[R.i];
    $("rev-pos").textContent = v ? `${R.i + 1} de ${R.total}` : "0";
    for (const id of ["rev-aprovar", "rev-reprovar", "rev-pular", "rev-voltar"]) $(id).disabled = !v;
    // sem versão para mostrar: a área da imagem diz por quê e o que fazer; os botões de decisão saem de cena
    $("rev-vazio").hidden = Boolean(v);
    $("rev-quadro").hidden = !v;
    aba.querySelector(".rev-decisao").hidden = !v;
    if (!v) {
      $("rev-img").removeAttribute("src");
      $("rev-caixas").replaceChildren();
      por($("rev-modos"));
      const total = (c.pendente || 0) + (c.aprovada || 0) + (c.reprovada || 0);
      const [titulo, texto] = !total
        ? ["Ainda não há versões para revisar", "As versões aparecem aqui depois que o gerador cria fraudes (aba ⚙ Gerar) ou "
          + "quando alguém salva uma edição no editor."]
        : R.estado === "pendente" && !R.split && !R.origem
          ? ["Tudo revisado 🎉", `${c.aprovada || 0} aprovada(s) e ${c.reprovada || 0} reprovada(s). Gere mais versões ou `
            + "escolha \"aprovadas\"/\"reprovadas\" em Mostrar para rever uma decisão."]
          : ["Nada com esses filtros", "Troque o que está em Mostrar, o split ou a origem, lá em cima."];
      $("rev-titulo").textContent = titulo;
      const irPendentes = R.estado !== "pendente" && c.pendente
        ? el("button", { type: "button", class: "primario", onclick: () => { R.estado = "pendente"; R.split = ""; R.origem = "";
          for (const [id, v] of [["rev-estado", "pendente"], ["rev-split", ""], ["rev-origem", ""]]) $(id).value = v;
          carregarFila(); } }, `Mostrar as ${c.pendente} pendente(s)`) : null;
      por($("rev-vazio"), el("h3", {}, titulo), el("p", {}, texto),
        el("div", { class: "linha" }, irPendentes,
          el("button", { type: "button", class: irPendentes ? "" : "primario", onclick: () => g.trocarAba("gerar") }, "⚙ Gerar versões"),
          el("a", { href: "/", class: "botao" }, "✏ Abrir o editor")));
      por($("rev-selo")); por($("rev-edicoes")); por($("rev-motivos"));
      return;
    }
    $("rev-titulo").textContent = `${rotuloSplit(v.split)} · ${v.documento || v.doc} · ${v.nome.split("__")[1]}`;
    const [selo, cls] = SELO[v.revisao] || SELO.pendente;
    por($("rev-selo"), el("span", { class: `pilula ${cls}` }, selo),
      v.revisor ? el("span", { class: "dica" }, ` por ${v.revisor}`) : null,
      el("span", { class: "dica" }, ` · ${v.origem}${v.operador ? ` (${v.operador})` : ""}${v.canal ? ` · canal ${v.canal}` : ""}`));
    por($("rev-modos"), ...MODOS.map(([m, rot]) => el("button", { type: "button", class: m === R.modo ? "ativa" : "",
      onclick: () => { R.modo = m; desenharRevisar(); } }, rot)),
    R.zoom ? el("button", { type: "button", onclick: () => { R.zoom = null; desenharRevisar(); } }, "🔍 ver inteira") : null);
    let q = "max=1600";
    if (R.zoom) {
      const [x0, y0, x1, y1] = R.zoom;
      const m = Math.max(40, 0.8 * (y1 - y0));
      q = `max=1200&x0=${Math.round(x0 - 3 * m)}&y0=${Math.round(y0 - m)}&x1=${Math.round(x1 + 3 * m)}&y1=${Math.round(y1 + m)}`;
    }
    const img = $("rev-img");
    img.onload = () => {
      if (R.zoom || !v.largura) { $("rev-caixas").replaceChildren(); return; }
      g.caixasSobre($("rev-caixas"), v.edicoes, v.largura, v.altura, (i) => { R.zoom = g.caixaDe(v.edicoes[i].bbox); desenharRevisar(); });
    };
    img.src = `/api/catalogo/imagem/${v.split}/${v.nome}/${R.modo}.png?${q}`;
    img.alt = `${R.modo} de ${v.nome}`;
    por($("rev-edicoes"), ...v.edicoes.map((e, i) => {
      const card = g.cartaoEdicao(e, i);
      card.classList.add("clicavel");
      card.title = "Clique para ampliar esta edição";
      card.onclick = () => { R.zoom = g.caixaDe(e.bbox); desenharRevisar(); };
      return card;
    }));
    por($("rev-motivos"), ...Object.entries(R.textoMotivos).map(([k, txt], i) => el("button", { type: "button",
      class: `motivo ${R.motivos.has(k) ? "on" : ""}`, "aria-pressed": String(R.motivos.has(k)),
      onclick: () => { alternarMotivo(k); } }, `${i + 1} · ${txt}`)));
    const prox = R.fila[R.i + 1];  // pré-carrega a próxima imagem
    if (prox) new Image().src = `/api/catalogo/imagem/${prox.split}/${prox.nome}/${R.modo}.png?max=1600`;
  }
  function alternarMotivo(k) {
    R.motivos.has(k) ? R.motivos.delete(k) : R.motivos.add(k);
    desenharRevisar();
  }
  function mover(passo) {
    if (!R.fila.length) return;
    R.i = Math.max(0, Math.min(R.fila.length - 1, R.i + passo));
    R.zoom = null; R.motivos.clear(); $("rev-comentario").value = "";
    desenharRevisar();
  }
  async function decidir(estado) {
    const v = R.fila[R.i];
    if (!v) return;
    const comentario = $("rev-comentario").value.trim();
    if (estado === "reprovada" && !R.motivos.size && !comentario) {
      aviso("Marque o motivo da reprovação (teclas 1–6) ou escreva um comentário.", "erro");
      $("rev-motivos").querySelector(".motivo")?.focus();
      return;
    }
    try {
      await revisar([v], estado, estado === "reprovada" ? { motivos: [...R.motivos], comentario } : { comentario });
    } catch (e) { aviso(e.message, "erro"); return; }
    R.contagem[v.revisao] = Math.max(0, (R.contagem[v.revisao] || 0) - 1);
    R.contagem[estado] = (R.contagem[estado] || 0) + 1;
    v.revisao = estado;
    if (R.estado !== "todas" && R.estado !== estado) {  // saiu da fila mostrada
      R.fila.splice(R.i, 1); R.total -= 1;
      if (R.i >= R.fila.length) R.i = Math.max(0, R.fila.length - 1);
      R.zoom = null; R.motivos.clear(); $("rev-comentario").value = "";
      desenharRevisar();
    } else mover(1);
    if (!R.fila.length && R.estado === "pendente") carregarFila();  // a fila mostra até 500: busca o resto
  }
  document.addEventListener("keydown", (ev) => {
    if (aba.hidden || ev.ctrlKey || ev.metaKey || ev.altKey || document.querySelector("dialog[open]")) return;
    const t = ev.target;
    if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) return;
    const k = ev.key.toLowerCase();
    if (k === "a") { ev.preventDefault(); decidir("aprovada"); } else if (k === "r") { ev.preventDefault(); decidir("reprovada"); } else if (ev.key === "ArrowRight") { ev.preventDefault(); mover(1); } else if (ev.key === "ArrowLeft") { ev.preventDefault(); mover(-1); } else if (/^[1-9]$/.test(k)) {
      const chave = Object.keys(R.textoMotivos)[Number(k) - 1];
      if (chave) { ev.preventDefault(); alternarMotivo(chave); }
    }
  });

  // ------------------------------------------------------------------ Metas
  const abaMetas = $("aba-metas");
  async function abrirMetas() {
    let r;
    try { r = await get("/api/metas"); } catch (e) { aviso(e.message, "erro"); return; }
    desenharMetas(r);
  }
  function desenharMetas(r) {
    const metas = structuredClone(r.metas);
    const classes = G.cat.categorias.filter((c) => c.chave !== "outro");
    const celula = (sp, k) => {
      const meta = metas.classes[sp]?.[k] || 0, feito = r.feito[sp]?.[k] || 0;
      const pct = meta ? Math.min(100, Math.round(100 * feito / meta)) : 0;
      return el("td", { class: meta && feito >= meta ? "cumprida" : "" },
        el("input", { type: "number", min: 0, max: 100000, step: 10, value: meta || "", placeholder: "—",
          "aria-label": `meta de ${rotuloCat(k)} no ${rotuloSplit(sp)}`,
          onchange: (e) => { metas.classes[sp] = metas.classes[sp] || {}; metas.classes[sp][k] = Math.max(0, Math.round(Number(e.target.value)) || 0); } }),
        el("span", { class: "feito" }, `${feito}${meta ? ` / ${meta}` : ""}`),
        meta ? el("span", { class: "trilho-pct", "aria-hidden": "true" }, el("span", { style: { width: `${pct}%`, background: pct >= 100 ? "var(--ok)" : "var(--destaque)" } })) : null);
    };
    const totalFalta = (sp) => Object.values(r.falta[sp] || {}).reduce((a, b) => a + b, 0);
    const todas = el("input", { type: "number", min: 0, step: 10, placeholder: "ex.: 200", "aria-label": "mesma meta para todas" });
    por(abaMetas, el("section", { class: "coluna larga metas" },
      el("h2", {}, "Metas por classe e split ", el("span", { class: "dica" }, "— quantas edições de cada classe você quer em cada split")),
      el("div", { class: "linha" },
        el("label", { class: "toggle" }, el("input", { type: "checkbox", checked: metas.so_aprovadas, onchange: (e) => { metas.so_aprovadas = e.target.checked; } }),
          " contar só versões aprovadas na revisão"),
        el("span", { class: "dica" }, "Preencher tudo com"), todas,
        el("button", { type: "button", onclick: () => {
          const n = Math.max(0, Math.round(Number(todas.value)) || 0);
          for (const [sp] of SPLITS) metas.classes[sp] = Object.fromEntries(classes.map((c) => [c.chave, n]));
          desenharMetas({ ...r, metas });
        } }, "aplicar"),
        el("button", { type: "button", class: "primario", onclick: async () => {
          try { desenharMetas(await post("/api/metas", metas)); aviso("Metas guardadas.", "ok"); } catch (e) { aviso(e.message, "erro"); }
        } }, "💾 Guardar metas")),
      el("div", { class: "matriz" }, el("table", { class: "tabela-metas" },
        el("thead", {}, el("tr", {}, el("th", {}, "classe"), ...SPLITS.map(([sp, rot]) => el("th", {}, rot)))),
        el("tbody", {}, ...classes.map((c) => el("tr", {}, el("th", { class: "tec-col" },
          el("span", { class: "chip", style: { background: g.corCategoria(c.chave) } }), ` ${c.rotulo}`), ...SPLITS.map(([sp]) => celula(sp, c.chave))))),
        el("tfoot", {}, el("tr", {}, el("th", {}, "falta"), ...SPLITS.map(([sp]) => el("td", {},
          el("b", {}, String(totalFalta(sp))), " edições ",
          totalFalta(sp) ? el("button", { type: "button", title: "Prepara a receita (modo avançado) com o que falta neste split",
            onclick: () => g.gerarOQueFalta(sp, r.falta[sp]) }, `⚙ gerar o que falta`)
            // "cumprida" só com meta: sem nenhuma, o split não tem o que cumprir (antes aparecia ✔ com 0 edições)
            : Object.values(metas.classes[sp] || {}).some((n) => n > 0) ? el("span", { class: "pilula ok" }, "✔ cumprida")
              : el("span", { class: "dica" }, "sem meta — preencha a coluna"))))))),
      el("p", { class: "dica" }, "\"Gerar o que falta\" monta a receita no modo avançado: só aquele split, as classes que faltam com peso pelo quanto falta, "
        + "e o número de versões estimado. Confira antes de gerar.")));
  }

  // ------------------------------------------------------------------ Equipe
  const abaEquipe = $("aba-equipe");
  const E = { dados: null, marcados: new Set(), split: "", so_livres: false, busca: "", timer: null };
  async function abrirEquipe() {
    try { E.dados = await get("/api/equipe"); } catch (e) { aviso(e.message, "erro"); return; }
    desenharEquipe();
    clearTimeout(E.timer);
    E.timer = setTimeout(() => { if (!abaEquipe.hidden && !abaEquipe.contains(document.activeElement)) abrirEquipe(); }, 15000);
  }
  function desenharEquipe() {
    const d = E.dados;
    const pessoas = Object.entries(d.por_operador).sort((a, b) => b[1].versoes - a[1].versoes);
    const docs = d.documentos.filter((x) => (!E.split || x.split === E.split) && (!E.so_livres || !x.responsavel)
      && (!E.busca || x.nome.toLowerCase().includes(E.busca)));
    const quem = el("input", { type: "text", list: "eq-nomes", placeholder: "nome de quem vai fazer", "aria-label": "atribuir a" });
    const atribuir = async (op) => {
      if (!E.marcados.size) { aviso("Marque os documentos primeiro.", "erro"); return; }
      try {
        await post("/api/equipe/atribuir", { ids: [...E.marcados], operador: op });
        aviso(op ? `${E.marcados.size} documento(s) atribuído(s) a ${op}.` : "Atribuição tirada.", "ok");
        E.marcados.clear(); abrirEquipe();
      } catch (e) { aviso(e.message, "erro"); }
    };
    const vivas = d.documentos.filter((x) => x.em_edicao_por);
    por(abaEquipe, el("section", { class: "coluna larga equipe" },
      el("h2", {}, "Quem fez o quê ", el("span", { class: "dica" }, `— você é ${d.eu}`)),
      pessoas.length ? el("div", { class: "matriz" }, el("table", {},
        el("thead", {}, el("tr", {}, ...["pessoa", "versões salvas", "edições", "aprovadas", "reprovadas", "% aprovação", "revisões feitas"].map((h) => el("th", {}, h)))),
        el("tbody", {}, ...pessoas.map(([nome, o]) => {
          const rev = o.aprovadas + o.reprovadas;
          return el("tr", {}, el("th", { class: "tec-col" }, nome), el("td", {}, String(o.versoes)), el("td", {}, String(o.edicoes)),
            el("td", {}, String(o.aprovadas)), el("td", {}, String(o.reprovadas)),
            el("td", {}, rev ? `${Math.round(100 * o.aprovadas / rev)}%` : "—"), el("td", {}, String(o.revisoes)));
        })))) : el("p", { class: "dica" }, "Ninguém salvou nada ainda."),
      el("h2", {}, "Editando agora ", el("span", { class: "dica" }, "— a trava vence após 10 min sem sinal")),
      vivas.length ? el("ul", { class: "vivas" }, ...vivas.map((x) => el("li", {}, `🔒 ${x.em_edicao_por} — ${rotuloSplit(x.split)} · ${x.nome} (desde ${x.desde})`)))
        : el("p", { class: "dica" }, "Ninguém com documento aberto."),
      el("h2", {}, "Documentos e responsáveis"),
      el("datalist", { id: "eq-nomes" }, ...d.operadores.map((n) => el("option", { value: n }))),
      el("div", { class: "linha" },
        el("select", { "aria-label": "split", onchange: (e) => { E.split = e.target.value; desenharEquipe(); } },
          el("option", { value: "" }, "todos os splits"), ...SPLITS.map(([s, r]) => el("option", { value: s, selected: E.split === s }, r))),
        el("label", { class: "toggle" }, el("input", { type: "checkbox", checked: E.so_livres, onchange: (e) => { E.so_livres = e.target.checked; desenharEquipe(); } }), " só sem responsável"),
        el("input", { type: "search", placeholder: "buscar documento…", value: E.busca, "aria-label": "buscar documento",
          onchange: (e) => { E.busca = e.target.value.trim().toLowerCase(); desenharEquipe(); } }),
        el("button", { type: "button", onclick: () => { docs.forEach((x) => E.marcados.add(x.id)); desenharEquipe(); } }, `marcar os ${docs.length}`),
        el("button", { type: "button", onclick: () => { E.marcados.clear(); desenharEquipe(); } }, "desmarcar")),
      el("div", { class: "linha barra-atribuir" }, el("b", {}, `${E.marcados.size} marcado(s)`), " → ", quem,
        el("button", { type: "button", class: "primario", onclick: () => atribuir(quem.value.trim()) }, "Atribuir"),
        el("button", { type: "button", onclick: () => atribuir("") }, "Tirar atribuição")),
      el("div", { class: "matriz" }, el("table", { class: "tabela-docs" },
        el("thead", {}, el("tr", {}, el("th", {}, ""), ...["documento", "split", "versões", "responsável", "em edição"].map((h) => el("th", {}, h)))),
        el("tbody", {}, ...docs.slice(0, 1000).map((x) => el("tr", { class: E.marcados.has(x.id) ? "marcado" : "" },
          el("td", {}, el("input", { type: "checkbox", checked: E.marcados.has(x.id), "aria-label": `marcar ${x.nome}`,
            onchange: (e) => { e.target.checked ? E.marcados.add(x.id) : E.marcados.delete(x.id); desenharEquipe(); } })),
          el("th", { class: "tec-col" }, x.nome), el("td", {}, rotuloSplit(x.split)), el("td", {}, String(x.versoes)),
          el("td", {}, x.responsavel || "—"), el("td", {}, x.em_edicao_por ? `🔒 ${x.em_edicao_por} (${x.desde})` : "")))))),
      docs.length > 1000 ? el("p", { class: "dica" }, `Mostrando 1000 de ${docs.length}: use a busca ou o split.`) : null));
  }

  return { abrirRevisar: carregarFila, abrirMetas, abrirEquipe, pedirMotivos, revisar };
}
