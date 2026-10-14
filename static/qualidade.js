// Aba 📊 Qualidade do gerador: o dataset está bom? Quatro cartões, cada um dizendo o que mede e como ler:
//   Documentos de entrada — quantos, de que tipo, quantos modelos de conta, o que falta juntar (entradas.py);
//   Última geração        — versões refeitas/descartadas por incoerência e campos editados x desistidos por tamanho;
//   Auditoria             — a auditoria de qualidade (qualidade.py), com os atalhos entre original e editada;
//   Detectabilidade       — por técnica, quanto um detector simples separa a edição do original (treino/detectabilidade).

const NOME_ALERTA = {
  estrutura: "estrutura (arquivo ou máscara com problema)", invisivel: "edição invisível (quase nada mudou)",
  sem_mudanca: "texto novo igual ao antigo", area: "área editada grande demais", duplicada: "versão repetida",
  reprovada: "reprovada na revisão", pericia: "perícia não escondeu a edição", ocr: "o OCR não lê o texto novo",
};
const NOME_MEDIDA = {
  ruido: "ruído", curtose: "forma do ruído", ruido_papel: "grão do papel", nitidez: "nitidez", blocos: "grade do JPEG",
  alta_freq: "detalhe fino", tinta: "quantidade de tinta", peso: "espessura do traço", cor_ruido: "cor do ruído",
  tom: "tom (claro/escuro)",
};
const ORDEM_TAMANHO = ["miúdo", "pequeno", "médio", "grande"];

export function instalarQualidade(g) {
  const { $, el, por, aviso, get, post, ligarTermos } = g;
  let timer = null;
  let ultimo = null;

  const quando = (t) => (t ? new Date(t * 1000).toLocaleString("pt-BR") : "");
  // titulo: texto, ou [texto, chave do glossário] (o título ganha o ⓘ do termo)
  const cartao = (titulo, explica, ...corpo) => el("section", { class: "cartao q-cartao" },
    Array.isArray(titulo) ? el("h3", { "data-termo": titulo[1] }, titulo[0]) : el("h3", {}, titulo),
    el("p", { class: "dica" }, explica), ...corpo);
  const lista = (itens, classe) => (itens && itens.length ? el("ul", { class: classe }, ...itens.map((t) => el("li", {}, t))) : null);

  function entradas(e) {
    if (!e) return cartao("📥 Documentos de entrada", "carregando…");
    const linhas = Object.entries(e.splits).map(([sp, v]) => el("tr", {},
      el("th", {}, sp), el("td", {}, String(v.documentos)), el("td", {}, String(v.arquivos)),
      el("td", {}, Object.entries(v.tipos).map(([k, n]) => `${n} ${k}`).join(", ") || "—")));
    return cartao(["📥 Documentos de entrada", "modelo_conta"],
      "As contas de verdade em dados/<split>/. É o limite de qualidade que nenhuma técnica corrige: com poucas contas, "
      + "de poucos emissores e de um tipo só, o detector decora o papel em vez de aprender a fraude.",
      el("table", { class: "q-tabela" }, el("thead", {}, el("tr", {}, el("th", {}, "split"), el("th", {}, "documentos"),
        el("th", {}, "arquivos"), el("th", {}, "como chegaram"))), el("tbody", {}, ...linhas)),
      el("p", {}, el("b", {}, `${e.total.documentos} documento(s)`),
        e.total.modelos !== null ? ` · ${e.total.modelos} modelo(s) de conta distinto(s)` : ""),
      e.alertas.length ? el("div", { class: "q-alerta" }, el("b", {}, "Atenção"), lista(e.alertas)) : el("p", { class: "q-ok" }, "✔ Sem alertas nas entradas."),
      e.sugestoes.length ? el("div", { class: "q-sugestao" }, el("b", {}, "O que juntar a seguir"), lista(e.sugestoes)) : null);
  }

  function geracao(s) {
    if (!s || !s.inicio) {
      return cartao("⚙ Última geração", "Aparece depois de gerar (aba ⚙ Gerar).");
    }
    const pt = s.por_tamanho || {};
    const barras = ORDEM_TAMANHO.filter((k) => pt[k]).map((k) => {
      const v = pt[k];
      const tot = v.feitas + v.desistidas;
      const pct = tot ? Math.round((100 * v.feitas) / tot) : 0;
      return el("div", { class: "q-barra-linha" }, el("span", { class: "q-rot" }, k),
        el("span", { class: "q-barra" }, el("span", { class: "q-barra-cheia" + (pct < 70 ? " ruim" : ""), style: { width: `${pct}%` } })),
        el("span", {}, `${v.feitas} editados, ${v.desistidas} de fora (${pct}%)`));
    });
    const miudo = pt["miúdo"];
    const alerta = miudo && miudo.feitas + miudo.desistidas >= 5 && miudo.desistidas > 0.3 * (miudo.feitas + miudo.desistidas)
      ? el("p", { class: "q-alerta" }, "Muitos campos de texto miúdo ficaram de fora: o dataset terá poucas edições difíceis "
        + "(datas do boleto, rodapé). Veja os erros na aba Gerar.") : null;
    return cartao(["⚙ Última geração", "refeita"], `${s.rodando ? "Gerando agora" : "Terminou"} — ${s.feitas} versão(ões) salva(s), `
      + `${s.falhas} que não deram certo, ${s.edicoes} edições.`,
      el("p", {}, el("b", {}, `${s.refeitas_incoerentes || 0}`), " versão(ões) refeita(s) e ",
        el("b", {}, `${s.descartadas_incoerentes || 0}`), " descartada(s) por incoerência ",
        el("span", { class: "dica" }, "(a edição deixava o texto contraditório: rua nova com o CEP antigo, vencimento antes da "
          + "emissão; a nova tentativa corrige a causa)")),
      barras.length ? el("div", {}, el("h4", { "data-termo": "tamanho_texto" }, "Campos por tamanho do texto"), ...barras,
        el("p", { class: "dica" }, "% editados = campos daquele tamanho que receberam a edição; os de fora não couberam em "
          + "nenhuma técnica (texto miúdo é o que mais falha).")) : null,
      alerta);
  }

  function auditoria(a, t) {
    const botao = el("button", { type: "button", class: "primario", disabled: t?.rodando || undefined,
      onclick: () => rodar("auditoria") }, t?.rodando ? "⏳ Auditando…" : "🔎 Rodar auditoria");
    if (!a) {
      return cartao("🔎 Auditoria do dataset", "Confere todas as versões salvas (máscaras, edição visível, repetidas) e "
        + "procura atalhos entre a original e a editada. Ainda não rodou.", botao, t?.erro ? el("p", { class: "q-alerta" }, t.erro) : null);
    }
    const tipos = Object.entries(a.por_tipo || {}).map(([k, n]) => `${n} × ${NOME_ALERTA[k] || k}`);
    const ds = a.dataset || {};
    const at = ds.atalhos || {};
    return cartao(["🔎 Auditoria do dataset", "auditoria"], `Última: ${quando(a._quando)}. ${a.versoes} versões, ${a.com_alerta} com alerta.`,
      botao,
      tipos.length ? el("div", { class: "q-alerta" }, el("b", {}, "Alertas nas versões"), lista(tipos)) : el("p", { class: "q-ok" }, "✔ Nenhuma versão com alerta."),
      (ds.alertas || []).length ? el("div", { class: "q-alerta" }, el("b", {}, "Alertas do conjunto"), lista(ds.alertas)) : null,
      at.pares ? el("p", { class: "dica" }, `Atalhos medidos em ${at.pares} pares: ruído longe da edição ${at.ruido?.mediana ?? "—"}×, `
        + `tom ${at.tom?.mediana ?? "—"}, arquivo ${at.bytes?.mediana ?? "—"}× (perto de 1 e 0 = sem atalho).`) : null,
      t?.erro ? el("p", { class: "q-alerta" }, t.erro) : null);
  }

  function detectabilidade(d, t) {
    const botao = el("button", { type: "button", class: "primario", disabled: t?.rodando || undefined,
      onclick: () => rodar("detectabilidade") }, t?.rodando ? "⏳ Medindo…" : "🎯 Medir detectabilidade");
    const explica = "Por técnica, quanto um detector simples (treinado nos próprios pares, sem ver o documento que julga) "
      + "separa o trecho editado do mesmo trecho na original. Perto de 0,5 = o vestígio não aparece (bom); perto de 1 = "
      + "vestígio fácil: é a técnica a melhorar primeiro.";
    const tit = ["🎯 Detectabilidade por técnica", "detectabilidade"];
    if (!d) return cartao(tit, explica + " Ainda não rodou.", botao);
    if (d.aviso) return cartao(tit, explica, botao, el("p", { class: "q-alerta" }, d.aviso));
    const nivel = (auc) => (auc >= 0.75 ? ["vestígio fácil", "ruim"] : auc >= 0.62 ? ["médio", "medio"] : ["difícil de achar", "bom"]);
    const linhas = Object.entries(d.por_tecnica || {}).sort((a, b) => b[1].auc - a[1].auc).map(([tid, v]) => {
      const [txt, cls] = nivel(v.auc);
      return el("tr", {}, el("td", {}, tid), el("td", {}, String(v.auc)), el("td", {}, el("span", { class: `q-chip ${cls}` }, txt)),
        el("td", {}, String(v.n)),
        el("td", {}, Object.entries(v.pesos || {}).map(([k, w]) => `${NOME_MEDIDA[k] || k} ${w > 0 ? "maior" : "menor"} na editada`).join("; ")));
    });
    return cartao(tit, explica, botao,
      el("p", {}, `Última: ${quando(d._quando)} — ${d.versoes} versões, ${d.documentos} documentos. AUC geral `, el("b", {}, String(d.geral))),
      el("table", { class: "q-tabela" }, el("thead", {}, el("tr", {}, el("th", {}, "técnica"), el("th", {}, "AUC"),
        el("th", {}, "leitura"), el("th", {}, "pares"), el("th", {}, "o que mais denuncia"))), el("tbody", {}, ...linhas)),
      t?.erro ? el("p", { class: "q-alerta" }, t.erro) : null);
  }

  function desenhar(r) {
    ultimo = r;
    const t = r.tarefas || {};
    por($("aba-qualidade"),
      el("div", { class: "q-cab" }, el("h2", {}, "📊 Qualidade do dataset"),
        el("p", { class: "dica" }, "O dataset está bom para treinar? Aqui ficam o que falta nas entradas, como foi a última geração, "
          + "a auditoria das versões salvas e quais técnicas deixam vestígio fácil. Passe o mouse no ⓘ de cada termo ou abra o 📖 Glossário."),
        el("button", { type: "button", onclick: () => abrir(true) }, "↻ Atualizar")),
      el("div", { class: "q-grade" }, entradas(r.entradas), geracao(r.geracao), auditoria(r.auditoria, t.auditoria),
        detectabilidade(r.detectabilidade, t.detectabilidade)));
    if (ligarTermos) ligarTermos($("aba-qualidade"));
    const rodando = Object.values(t).some((x) => x.rodando) || r.geracao?.rodando;
    clearTimeout(timer);
    if (rodando && !$("aba-qualidade").hidden) timer = setTimeout(() => abrir(false), 4000);
  }

  async function abrir(atualizar = false) {
    try {
      desenhar(await get(`/api/qualidade/resumo${atualizar ? "?atualizar=1" : ""}`));
    } catch (e) {
      aviso(`Não consegui carregar a qualidade: ${e.message}`, "erro");
    }
  }

  async function rodar(o_que) {
    try {
      await post("/api/qualidade/rodar", { o_que });
      aviso(o_que === "auditoria" ? "Auditoria começou (pode levar alguns minutos)." : "Medida de detectabilidade começou (pode levar alguns minutos).", "ok");
      abrir(false);
    } catch (e) { aviso(e.message, "erro"); }
  }

  return { abrir, ultimo: () => ultimo };
}
