// Abas de equipe do gerador:
//   Revisar — fila de versões salvas para uma pessoa aprovar ou reprovar (com motivo); A/R/→/← no teclado; em cada
//             edição o antes e o depois e a fidelidade (altura, traço, fonte); em grade, várias de uma vez;
//   Metas   — quantas edições de cada classe em cada split, o feito e o que falta (e "gerar o que falta");
//   Equipe  — o que cada pessoa fez, atribuição de documentos e quem está editando o quê agora.
// O nome de quem revisa/atribui vem do cabeçalho X-Operador (api.js).

const ESTADOS = [["pendente", "pendentes"], ["reprovada", "reprovadas"], ["aprovada", "aprovadas"], ["todas", "todas"]];
const MODOS = [["sobreposicao", "Sobreposição"], ["editada", "Editada"], ["original", "Original"], ["tecnicas", "Máscara de técnicas"]];
const SELO = { aprovada: ["✔ aprovada", "ok"], reprovada: ["✖ reprovada", "ruim"], pendente: ["pendente", ""] };

export function instalarPainel(g) {
  const { G, $, el, por, aviso, get, post, rotuloCat, rotuloSplit, SPLITS } = g;
  const R = { fila: [], todas: [], i: 0, estado: "pendente", split: "", origem: "", modo: "sobreposicao", zoom: null, iEd: -1,
    motivos: new Set(), contagem: {}, textoMotivos: G.cat.motivos_revisao || {}, total: 0, totalServidor: 0, ordem: "antigas",
    soAlerta: false, vista: "uma", grade: { reprovar: new Set(), n: 24 }, segurandoO: false };

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
  // a fidelidade de cada edição, medida no gerador (rotulador.fidelidade): a altura das letras e a grossura do traço do
  // texto novo ÷ o antigo, o que passou da tolerância (fid_fora) e a fonte usada com a semelhança (identificar_fonte)
  const LIMIAR_FONTE = 0.90;
  function fid(e) {
    const n = (x) => (x === "" || x == null || Number.isNaN(Number(x)) ? null : Number(x));
    const fora = String(e.fid_fora || "").split(";").filter(Boolean);
    const sem = n(e.semelhanca_fonte);
    if (sem != null && sem < LIMIAR_FONTE) fora.push("fonte");
    return { altura: n(e.fid_altura), grossura: n(e.fid_grossura), sem, fora,
      fonte: /(?:^|;)fonte=([^;]+)/.exec(e.fornecedores || "")?.[1] || null };
  }
  const nAlertas = (v) => v.edicoes.filter((e) => fid(e).fora.length).length;
  const medida = (v) => v.edicoes.some((e) => fid(e).altura != null || fid(e).grossura != null);
  function chipsFidelidade(e) {
    const f = fid(e);
    if (f.altura == null && f.grossura == null && !f.fonte) return null;
    const pct = (x) => { const d = Math.round((x - 1) * 100); return d ? `${d > 0 ? "+" : ""}${d}%` : "igual"; };
    const chip = (rot, val, ruim, titulo) => el("span", { class: `fid ${ruim ? "ruim" : "ok"}`, title: titulo }, `${ruim ? "⚠" : "✓"} ${rot} ${val}`);
    return el("div", { class: "fid-linha" },
      f.altura != null ? chip("altura", pct(f.altura), f.fora.includes("altura"), "altura das letras do texto novo ÷ a do antigo (tolerância: 4% ou 1 px)") : null,
      f.grossura != null ? chip("traço", pct(f.grossura), f.fora.includes("grossura"), "espessura do traço do texto novo ÷ a do antigo (tolerância: 10%)") : null,
      f.fonte ? chip("fonte", f.fonte + (f.sem != null ? ` · ${f.sem.toFixed(2)}` : ""), f.fora.includes("fonte"),
        `a fonte usada e o quanto ela casou com a do documento (1 = idêntica; abaixo de ${LIMIAR_FONTE.toFixed(2)}, aproximada)`) : null);
  }
  // o trecho da edição antes (original) e depois (editada), na MESMA caixa e escala, um sobre o outro; com a linha em
  // volta (5 alturas de letra de cada lado): a fonte e o tamanho se julgam contra as letras vizinhas
  function recorte(e, maximo = 700) {
    const [x0, y0, x1, y1] = g.caixaDe(e.bbox);
    const h = Math.max(12, y1 - y0);
    return `max=${maximo}&x0=${Math.round(x0 - 5 * h)}&y0=${Math.round(y0 - 0.6 * h)}&x1=${Math.round(x1 + 5 * h)}&y1=${Math.round(y1 + 0.6 * h)}`;
  }
  function antesDepois(v, e, maximo) {
    const q = recorte(e, maximo), url = (m) => `/api/catalogo/imagem/${v.split}/${v.nome}/${m}.png?${q}`;
    return el("div", { class: "antes-depois" },
      el("span", { class: "rot" }, "antes"), el("img", { src: url("original"), alt: "trecho original", loading: "lazy" }),
      el("span", { class: "rot" }, "depois"), el("img", { src: url("editada"), alt: "trecho editado", loading: "lazy" }));
  }
  function seloFidelidade(v) {
    const n = nAlertas(v);
    if (n) return el("span", { class: "pilula ruim", title: "altura, traço ou fonte do texto novo diferente do antigo" },
      `⚠ ${n} ${n === 1 ? "edição diferente" : "edições diferentes"} do original`);
    return medida(v) ? el("span", { class: "pilula ok", title: "altura das letras, traço e fonte medidos no gerador" },
      "✓ tamanho, traço e fonte iguais") : null;
  }

  const aba = $("aba-revisar");
  por(aba,
    el("section", { class: "coluna rev-imagem" },
      el("div", { class: "rev-barra" },
        el("div", { class: "segmentado", role: "group", "aria-label": "como revisar" },
          el("button", { type: "button", id: "rev-vista-uma", class: "ativa", onclick: () => trocarVista("uma") }, "Uma a uma"),
          el("button", { type: "button", id: "rev-vista-grade", onclick: () => trocarVista("grade"),
            title: "Várias versões de uma vez: marque as ruins e aprove o resto" }, "Em grade")),
        el("label", {}, "Mostrar ", el("select", { id: "rev-estado", onchange: (e) => { R.estado = e.target.value; carregarFila(); } },
          ...ESTADOS.map(([v, r]) => el("option", { value: v }, r)))),
        el("select", { id: "rev-split", "aria-label": "split", onchange: (e) => { R.split = e.target.value; carregarFila(); } },
          el("option", { value: "" }, "todos os splits"), ...SPLITS.map(([s, r]) => el("option", { value: s }, r))),
        el("select", { id: "rev-origem", "aria-label": "origem", onchange: (e) => { R.origem = e.target.value; carregarFila(); } },
          el("option", { value: "" }, "todas as origens"), el("option", { value: "gerador" }, "só gerador"),
          el("option", { value: "automatico" }, "só automático (editor)"), el("option", { value: "assistido" }, "só assistido"),
          el("option", { value: "manual" }, "só manual")),
        el("select", { id: "rev-ordem", "aria-label": "ordem", onchange: (e) => { R.ordem = e.target.value; ordenar(); } },
          el("option", { value: "antigas" }, "mais antigas primeiro"), el("option", { value: "suspeitas" }, "suspeitas primeiro")),
        el("label", { class: "toggle", title: "só as versões com altura, traço ou fonte diferente do original" },
          el("input", { type: "checkbox", id: "rev-so-alerta", onchange: (e) => { R.soAlerta = e.target.checked; ordenar(); } }), " só com ⚠"),
        el("span", { id: "rev-pos", class: "pilula" }), el("span", { id: "rev-contagem", class: "dica" })),
      el("div", { class: "det-modos", id: "rev-modos" }),
      el("div", { class: "rev-vazio", id: "rev-vazio", hidden: true, role: "status" }),
      el("div", { class: "det-imagem rev-img", id: "rev-quadro" }, el("img", { id: "rev-img", alt: "" }), el("div", { id: "rev-caixas" })),
      el("div", { class: "rev-grade", id: "rev-grade", hidden: true })),
    el("section", { class: "coluna rev-lado" },
      el("h2", { id: "rev-titulo" }),
      el("div", { id: "rev-selo", class: "rev-selo" }),
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
        el("p", { class: "dica" }, "A aprova · R reprova · 1–6 marcam o motivo · → pula · ← volta · Z vai de edição em edição "
          + "(Esc: a página inteira) · segure O para ver a original"))));

  async function carregarFila() {
    const q = new URLSearchParams({ revisao: R.estado });
    if (R.split) q.set("split", R.split);
    if (R.origem) q.set("origem", R.origem);
    try {
      const r = await get(`/api/revisao/fila?${q}`);
      Object.assign(R, { todas: r.itens, totalServidor: r.total, contagem: r.contagem, textoMotivos: r.motivos, zoom: null });
    } catch (e) { aviso(e.message, "erro"); return; }
    ordenar();
  }
  // a ordem e o filtro "só com ⚠" valem na fila que veio do servidor (até 500 versões)
  function ordenar() {
    let f = R.todas || [];
    if (R.soAlerta) f = f.filter(nAlertas);
    if (R.ordem === "suspeitas") f = [...f].sort((a, b) => nAlertas(b) - nAlertas(a));
    Object.assign(R, { fila: f, total: R.soAlerta ? f.length : R.totalServidor, i: 0, zoom: null, iEd: -1 });
    R.grade.reprovar.clear();
    R.grade.n = 24;
    desenhar();
  }
  function desenhar() {
    if (R.vista === "grade") desenharGrade(); else desenharRevisar();
  }
  function trocarVista(vista) {
    R.vista = vista;
    aba.classList.toggle("em-grade", vista === "grade");
    $("rev-vista-uma").classList.toggle("ativa", vista === "uma");
    $("rev-vista-grade").classList.toggle("ativa", vista === "grade");
    desenhar();
  }
  function vazio(c) {
    const total = (c.pendente || 0) + (c.aprovada || 0) + (c.reprovada || 0);
    const [titulo, texto] = !total
      ? ["Ainda não há versões para revisar", "As versões aparecem aqui depois que o gerador cria fraudes (aba ⚙ Gerar) ou "
        + "quando alguém salva uma edição no editor."]
      : R.estado === "pendente" && !R.split && !R.origem && !R.soAlerta
        ? ["Tudo revisado 🎉", `${c.aprovada || 0} aprovada(s) e ${c.reprovada || 0} reprovada(s). Gere mais versões ou `
          + "escolha \"aprovadas\"/\"reprovadas\" em Mostrar para rever uma decisão."]
        : ["Nada com esses filtros", "Troque o que está em Mostrar, o split, a origem ou o \"só com ⚠\", lá em cima."];
    const irPendentes = R.estado !== "pendente" && c.pendente
      ? el("button", { type: "button", class: "primario", onclick: () => { R.estado = "pendente"; R.split = ""; R.origem = "";
        for (const [id, v] of [["rev-estado", "pendente"], ["rev-split", ""], ["rev-origem", ""]]) $(id).value = v;
        carregarFila(); } }, `Mostrar as ${c.pendente} pendente(s)`) : null;
    por($("rev-vazio"), el("h3", {}, titulo), el("p", {}, texto),
      el("div", { class: "linha" }, irPendentes,
        el("button", { type: "button", class: irPendentes ? "" : "primario", onclick: () => g.trocarAba("gerar") }, "⚙ Gerar versões"),
        el("a", { href: "/", class: "botao" }, "✏ Abrir o editor")));
    return titulo;
  }
  function srcPrincipal(v, modo) {
    let q = "max=1600";
    if (R.zoom) {
      const [x0, y0, x1, y1] = R.zoom;
      const m = Math.max(40, 0.8 * (y1 - y0));
      q = `max=1200&x0=${Math.round(x0 - 3 * m)}&y0=${Math.round(y0 - m)}&x1=${Math.round(x1 + 3 * m)}&y1=${Math.round(y1 + m)}`;
    }
    return `/api/catalogo/imagem/${v.split}/${v.nome}/${modo}.png?${q}`;
  }
  function desenharRevisar() {
    const c = R.contagem;
    $("rev-contagem").textContent = `pendentes ${c.pendente || 0} · aprovadas ${c.aprovada || 0} · reprovadas ${c.reprovada || 0}`;
    $("rev-grade").hidden = true;
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
      $("rev-titulo").textContent = vazio(c);
      por($("rev-selo")); por($("rev-edicoes")); por($("rev-motivos"));
      return;
    }
    $("rev-titulo").textContent = `${rotuloSplit(v.split)} · ${v.documento || v.doc} · ${v.nome.split("__")[1]}`;
    const [selo, cls] = SELO[v.revisao] || SELO.pendente;
    por($("rev-selo"), el("span", { class: `pilula ${cls}` }, selo),
      v.revisor ? el("span", { class: "dica" }, ` por ${v.revisor}`) : null,
      el("span", { class: "dica" }, ` · ${v.origem}${v.operador ? ` (${v.operador})` : ""}${v.canal ? ` · canal ${v.canal}` : ""}`),
      seloFidelidade(v));
    por($("rev-modos"), ...MODOS.map(([m, rot]) => el("button", { type: "button", class: m === R.modo ? "ativa" : "",
      onclick: () => { R.modo = m; desenharRevisar(); } }, rot)),
    R.zoom ? el("button", { type: "button", onclick: () => { R.zoom = null; R.iEd = -1; desenharRevisar(); } }, "🔍 ver inteira") : null);
    const img = $("rev-img");
    img.onload = () => {
      if (R.zoom || !v.largura) { $("rev-caixas").replaceChildren(); return; }
      g.caixasSobre($("rev-caixas"), v.edicoes, v.largura, v.altura, (i) => irParaEdicao(i));
    };
    img.src = srcPrincipal(v, R.segurandoO ? "original" : R.modo);
    img.alt = `${R.modo} de ${v.nome}`;
    por($("rev-edicoes"), ...v.edicoes.map((e, i) => {
      const card = g.cartaoEdicao(e, i, el("div", { class: "rev-comparar" }, chipsFidelidade(e), antesDepois(v, e)));
      card.classList.add("clicavel");
      card.classList.toggle("atual", i === R.iEd);
      card.classList.toggle("alerta", fid(e).fora.length > 0);
      card.title = "Clique para ampliar esta edição na imagem grande";
      card.onclick = () => irParaEdicao(i);
      return card;
    }));
    por($("rev-motivos"), ...Object.entries(R.textoMotivos).map(([k, txt], i) => el("button", { type: "button",
      class: `motivo ${R.motivos.has(k) ? "on" : ""}`, "aria-pressed": String(R.motivos.has(k)),
      onclick: () => { alternarMotivo(k); } }, `${i + 1} · ${txt}`)));
    const prox = R.fila[R.i + 1];  // pré-carrega a próxima imagem
    if (prox) new Image().src = `/api/catalogo/imagem/${prox.split}/${prox.nome}/${R.modo}.png?max=1600`;
  }
  function irParaEdicao(i) {
    const v = R.fila[R.i];
    if (!v || !v.edicoes.length) return;
    R.iEd = ((i % v.edicoes.length) + v.edicoes.length) % v.edicoes.length;
    R.zoom = g.caixaDe(v.edicoes[R.iEd].bbox);
    desenharRevisar();
    aba.querySelector(".rev-lado .edicao.atual")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  function alternarMotivo(k) {
    R.motivos.has(k) ? R.motivos.delete(k) : R.motivos.add(k);
    desenharRevisar();
  }
  function mover(passo) {
    if (!R.fila.length) return;
    R.i = Math.max(0, Math.min(R.fila.length - 1, R.i + passo));
    R.zoom = null; R.iEd = -1; R.motivos.clear(); $("rev-comentario").value = "";
    desenharRevisar();
  }
  function contar(v, estado) {
    R.contagem[v.revisao] = Math.max(0, (R.contagem[v.revisao] || 0) - 1);
    R.contagem[estado] = (R.contagem[estado] || 0) + 1;
    v.revisao = estado;
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
    contar(v, estado);
    if (R.estado !== "todas" && R.estado !== estado) {  // saiu da fila mostrada
      R.fila.splice(R.i, 1); R.total -= 1;
      R.todas = R.todas.filter((x) => x !== v);
      if (R.i >= R.fila.length) R.i = Math.max(0, R.fila.length - 1);
      R.zoom = null; R.iEd = -1; R.motivos.clear(); $("rev-comentario").value = "";
      desenharRevisar();
    } else mover(1);
    if (!R.fila.length && R.estado === "pendente") carregarFila();  // a fila mostra até 500: busca o resto
  }

  // ------------------------------------------------------------------ Revisar em grade
  // as versões lado a lado, cada uma com o antes e o depois das edições: clicar marca para reprovar; o rodapé aprova
  // as não marcadas e reprova as marcadas (com o motivo) de uma vez
  function desenharGrade() {
    const c = R.contagem;
    $("rev-contagem").textContent = `pendentes ${c.pendente || 0} · aprovadas ${c.aprovada || 0} · reprovadas ${c.reprovada || 0}`;
    const lista = R.fila.slice(0, R.grade.n);
    $("rev-pos").textContent = `${lista.length} de ${R.total}`;
    $("rev-vazio").hidden = lista.length > 0;
    $("rev-quadro").hidden = true;
    por($("rev-modos"));
    const grade = $("rev-grade");
    grade.hidden = !lista.length;
    if (!lista.length) { vazio(c); grade.replaceChildren(); return; }
    const marcadas = R.grade.reprovar;
    const chaveDe = (v) => `${v.split}/${v.nome}`;
    const cartoes = lista.map((v) => {
      const marcada = marcadas.has(chaveDe(v));
      return el("article", { class: `rev-cartao${marcada ? " reprovar" : ""}${nAlertas(v) ? " alerta" : ""}`, tabindex: 0,
        role: "checkbox", "aria-checked": String(marcada),
        title: "Clique para marcar para REPROVAR (de novo, desmarca). Duplo clique abre esta versão uma a uma.",
        onclick: () => { marcada ? marcadas.delete(chaveDe(v)) : marcadas.add(chaveDe(v)); desenharGrade(); },
        ondblclick: () => { R.i = R.fila.indexOf(v); trocarVista("uma"); },
        onkeydown: (ev) => { if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); ev.currentTarget.click(); } } },
      el("header", {}, el("b", {}, `${v.documento || v.doc} · ${v.nome.split("__")[1]}`),
        el("span", { class: "dica" }, ` ${rotuloSplit(v.split)}`),
        el("span", { class: "marca" }, marcada ? "✖ reprovar" : "✔ aprovar")),
      ...v.edicoes.slice(0, 3).map((e) => el("div", { class: "rev-cartao-ed" },
        el("div", { class: "textos" }, el("span", { class: "antigo" }, e.texto_antigo || "—"), " → ",
          el("span", { class: "novo" }, e.texto_novo === "" ? "(apagado)" : (e.texto_novo || "—"))),
        chipsFidelidade(e), antesDepois(v, e, 520))),
      v.edicoes.length > 3 ? el("p", { class: "dica" }, `+ ${v.edicoes.length - 3} edição(ões): duplo clique para ver todas`) : null);
    });
    const aprovar = lista.filter((v) => !marcadas.has(chaveDe(v)));
    const reprovar = lista.filter((v) => marcadas.has(chaveDe(v)));
    por(grade, el("div", { class: "rev-grade-itens" }, ...cartoes),
      R.fila.length > lista.length ? el("button", { type: "button", class: "mais", onclick: () => { R.grade.n += 24; desenharGrade(); } },
        `Mostrar mais ${Math.min(24, R.fila.length - lista.length)}`) : null,
      el("div", { class: "rev-grade-rodape" },
        el("span", { class: "dica" }, "Clique nas ruins para marcar; as outras são aprovadas. Duplo clique abre a versão."),
        el("button", { type: "button", class: "perigo grande", disabled: !reprovar.length, onclick: () => decidirGrade(aprovar, reprovar, true) },
          `✖ Só reprovar as ${reprovar.length} marcada(s)…`),
        el("button", { type: "button", class: "primario grande", onclick: () => decidirGrade(aprovar, reprovar, false) },
          reprovar.length ? `✔ Aprovar ${aprovar.length} e reprovar ${reprovar.length}…` : `✔ Aprovar as ${aprovar.length}`)));
  }
  async function decidirGrade(aprovar, reprovar, soReprovar) {
    let extra = null;
    if (reprovar.length) {
      extra = await pedirMotivos(`Reprovar ${reprovar.length} versão(ões): por quê?`);
      if (!extra) return;
    }
    try {
      if (reprovar.length) await revisar(reprovar, "reprovada", extra);
      if (!soReprovar && aprovar.length) await revisar(aprovar, "aprovada", {});
    } catch (e) { aviso(e.message, "erro"); return; }
    aviso(`${soReprovar ? 0 : aprovar.length} aprovada(s) e ${reprovar.length} reprovada(s).`, "ok");
    await carregarFila();
  }

  document.addEventListener("keydown", (ev) => {
    if (aba.hidden || R.vista === "grade" || ev.ctrlKey || ev.metaKey || ev.altKey || document.querySelector("dialog[open]")) return;
    const t = ev.target;
    if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) return;
    const k = ev.key.toLowerCase();
    if (k === "a") { ev.preventDefault(); decidir("aprovada"); } else if (k === "r") { ev.preventDefault(); decidir("reprovada"); } else if (ev.key === "ArrowRight") { ev.preventDefault(); mover(1); } else if (ev.key === "ArrowLeft") { ev.preventDefault(); mover(-1); } else if (k === "z") {
      ev.preventDefault(); irParaEdicao(R.iEd + (ev.shiftKey ? -1 : 1));
    } else if (ev.key === "Escape" && R.zoom) {
      ev.preventDefault(); R.zoom = null; R.iEd = -1; desenharRevisar();
    } else if (k === "o" && !ev.repeat) {  // segurar O: a original na imagem grande (soltar volta)
      const v = R.fila[R.i];
      if (v) { ev.preventDefault(); R.segurandoO = true; $("rev-img").src = srcPrincipal(v, "original"); }
    } else if (/^[1-9]$/.test(k)) {
      const chave = Object.keys(R.textoMotivos)[Number(k) - 1];
      if (chave) { ev.preventDefault(); alternarMotivo(chave); }
    }
  });
  document.addEventListener("keyup", (ev) => {
    if (ev.key.toLowerCase() !== "o" || !R.segurandoO) return;
    R.segurandoO = false;
    const v = R.fila[R.i];
    if (v) $("rev-img").src = srcPrincipal(v, R.modo);
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
