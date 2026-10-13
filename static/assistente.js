// Modo fácil do gerador: a receita é uma lista de TIPOS DE EDIÇÃO em linguagem simples.
// Cada tipo junta o QUÊ (classes: valor, nome...), o COMO (jeitos de editar, do catálogo: trocar o
// texto todo, mexer num dígito...), o capricho do falsificador (amador, profissional, IA) e o quanto
// (pouco, normal, muito). O servidor sorteia só as combinações técnica × classe de algum tipo, e o
// peso de cada tipo se divide entre as combinações dele (gerador.Receita). O modo avançado continua
// com a lista completa de técnicas e classes.

// classe -> ícone e um exemplo do que aparece no documento
export const CLASSES = {
  "nome": ["👤", "JOÃO P. LIMA"], "cpf/cnpj": ["🆔", "123.456.789-09"], "endereço": ["🏠", "Rua das Flores, 123"],
  "bairro": ["🏘️", "Centro"], "cep": ["📮", "30123-456"], "cidade/uf": ["🗺️", "Belo Horizonte/MG"],
  "data": ["📅", "10/09/2026"], "mês de referência": ["🗓️", "SET/2026"], "valor": ["💰", "R$ 171,37"],
  "consumo/leitura": ["⚡", "342 kWh"], "codigo barras": ["▥", "barras do boleto"], "linha digitável": ["🔢", "8462 0000 1713…"],
  "identificador documento": ["#️⃣", "UC 3012-44"], "emissor/logotipo": ["🏢", "CIA DE ENERGIA"], "qr code/pix": ["🔳", "QR PIX"],
};
export const GRUPOS = [["Pessoa", ["nome", "cpf/cnpj"]], ["Endereço", ["endereço", "bairro", "cep", "cidade/uf"]],
  ["Valores e datas", ["valor", "consumo/leitura", "data", "mês de referência"]],
  ["Códigos e números", ["codigo barras", "linha digitável", "qr code/pix", "identificador documento"]],
  ["Emissor", ["emissor/logotipo"]]];
const CAPRICHO = [
  ["simples", "🙂", "Amador", "dá para perceber: retângulo branco, fonte padrão, traço grosso"],
  ["profissional", "🎯", "Profissional", "fonte imitada e grão do papel igual: difícil de ver"],
  ["ia", "🤖", "IA", "um modelo de difusão reescreve a região (bem mais lento; precisa da IA ligada)"],
];
const QUANTO = [[1, "pouco"], [2, "normal"], [4, "muito"]];
const EDICOES = [[[1, 1], "1 só"], [[1, 3], "1 a 3"], [[2, 5], "2 a 5"]];
const CANAIS = {
  nenhum: ["📄", "a imagem como está: só a edição muda"], misturado: ["🎲", "um pouco de cada caminho, como na vida real"],
  scanner: ["🖨️", "impresso e escaneado"], celular: ["📱", "foto de celular do papel"], tela: ["🖥️", "foto da tela do computador"],
  impressao: ["🔁", "impresso e capturado de novo"], whatsapp: ["💬", "recomprimido ao passar pelo WhatsApp"],
};
const CORES = ["#3d8bfd", "#e3b341", "#3fb950", "#f0883e", "#bc8cff", "#ff7b72", "#39c5cf", "#d2a8ff"];
const IDEIAS = [
  { icone: "💰", nome: "Valor da conta", categorias: ["valor", "consumo/leitura"], jeitos: ["caractere", "trecho", "reescrever"], peso: 4 },
  { icone: "👤", nome: "Trocar o titular", categorias: ["nome", "cpf/cnpj"], jeitos: ["reescrever", "caractere", "colar"], peso: 2 },
  { icone: "🏠", nome: "Outro endereço", categorias: ["endereço", "bairro", "cep", "cidade/uf"], jeitos: ["reescrever", "trecho", "colar"], peso: 2 },
  { icone: "📅", nome: "Mexer nas datas", categorias: ["data", "mês de referência"], jeitos: ["caractere", "trecho"], peso: 2 },
  { icone: "▥", nome: "Códigos de pagamento", categorias: ["codigo barras", "qr code/pix", "linha digitável"], jeitos: ["codigo", "caractere"], peso: 1 },
  { icone: "🧽", nome: "Apagar informações", categorias: ["endereço", "nome", "identificador documento", "valor"], jeitos: ["apagar"], peso: 1 },
  { icone: "🖌️", nome: "Retoque sem mudar o texto", categorias: ["valor", "data", "nome", "endereço", "cep"], jeitos: ["retoque"], peso: 1 },
  { icone: "🎲", nome: "Tudo um pouco", categorias: null, jeitos: null, peso: 2 },
];
const INICIAIS = 4;  // receita nova começa com as 4 primeiras ideias (dá para ver como um tipo é)

export function instalarAssistente(g) {
  const { G, $, el, por, aviso, guardar, tec, rotuloCat } = g;
  const jeito = (id) => G.cat.jeitos.find((j) => j.id === id);
  const todasClasses = () => G.cat.categorias.filter((c) => c.chave !== "outro").map((c) => c.chave);
  const tipoCampo = (k) => G.cat.tipo_da_categoria[k] || "texto";
  const compativel = (t, k) => t.tipos.includes(tipoCampo(k)) && (!t.categorias || t.categorias.includes(k));
  const alternar = (lista, v) => { const i = lista.indexOf(v); if (i >= 0) lista.splice(i, 1); else lista.push(v); };
  let exemplos = {};  // técnica -> exemplos reais já gerados (um por classe)
  // exemplo real de uma técnica, de preferência de uma das classes pedidas
  const exemploDe = (tid, classes = []) => (exemplos[tid] || []).find((e) => classes.includes(e.categoria)) || exemplos[tid]?.[0];

  // ------------------------------------------------------------------ tipos
  function novoTipo(base = {}) {
    return { nome: "", icone: "", categorias: [], jeitos: [], niveis: ["simples", "profissional"], peso: 2, ativo: true,
      ...structuredClone(base), id: Math.random().toString(36).slice(2, 10) };
  }
  const daIdeia = (i) => novoTipo({ nome: i.nome, icone: i.icone, categorias: i.categorias || todasClasses(),
    jeitos: i.jeitos || G.cat.jeitos.map((j) => j.id), peso: i.peso });
  function limpar(tp) {  // receita salva ou importada: só o que ainda existe no catálogo
    const classes = new Set(todasClasses());
    return { ...novoTipo(), ...tp, id: tp.id || novoTipo().id,
      categorias: (tp.categorias || []).filter((k) => classes.has(k)), jeitos: (tp.jeitos || []).filter((j) => jeito(j)),
      niveis: (tp.niveis || ["simples", "profissional"]).filter((n) => CAPRICHO.some(([k]) => k === n)),
      peso: QUANTO.some(([v]) => v === tp.peso) ? tp.peso : 2 };
  }
  const autoNome = (tp) => tp.categorias.length ? tp.categorias.slice(0, 2).map(rotuloCat).join(" e ")
    + (tp.categorias.length > 2 ? ` +${tp.categorias.length - 2}` : "") : "";
  const nomeDe = (tp) => tp.nome.trim() || autoNome(tp) || "tipo sem nome";
  const iconeDe = (tp) => tp.icone || CLASSES[tp.categorias[0]]?.[0] || "🏷️";
  const corDe = (tp) => CORES[Math.max(0, (G.rec.tipos || []).indexOf(tp)) % CORES.length];

  // técnicas do tipo (pelo capricho, pela IA disponível e por servir a alguma classe escolhida)
  const tecnicasDe = (tp) => tp.jeitos.flatMap((j) => jeito(j)?.tecnicas || []).map(tec).filter((t) => t
    && tp.niveis.includes(t.nivel) && (!t.precisa_ia || G.iaOnline) && tp.categorias.some((k) => compativel(t, k)));
  const paresDe = (tp) => tecnicasDe(tp).flatMap((t) => tp.categorias.filter((k) => compativel(t, k)).map((k) => [t.id, k]));
  function problema(tp) {
    if (!tp.categorias.length) return "escolha o que adulterar";
    if (!tp.jeitos.length) return "escolha como adulterar";
    if (!tp.niveis.length) return "escolha o capricho";
    if (paresDe(tp).length) return "";
    const comIA = tp.niveis.includes("ia") && tp.jeitos.flatMap((j) => jeito(j)?.tecnicas || []).map(tec)
      .some((t) => t?.precisa_ia && tp.categorias.some((k) => compativel(t, k)));
    return comIA && !G.iaOnline ? "só dá com o servidor de IA ligado" : "esses jeitos não servem para essas classes com esse capricho";
  }
  const validos = () => (G.rec.tipos || []).filter((tp) => tp.ativo !== false && !problema(tp));
  const usandoTipos = () => G.modo !== "exato" && (G.modo === "facil"
    || (!G.rec.tipos_desligados && (G.rec.tipos || []).some((tp) => tp.ativo !== false)));
  function efetivas() {  // técnicas e classes que o lote vai usar (com os pesos)
    if (!usandoTipos()) return { tecnicas: G.rec.tecnicas, categorias: G.rec.categorias };
    const tecnicas = {}, categorias = {};
    for (const tp of validos()) {
      const ps = paresDe(tp);
      for (const [tid, k] of ps) {
        tecnicas[tid] = (tecnicas[tid] || 0) + tp.peso / ps.length;
        categorias[k] = (categorias[k] || 0) + tp.peso / ps.length;
      }
    }
    return { tecnicas, categorias };
  }
  const paraEnvio = (tp) => ({ nome: nomeDe(tp), categorias: [...tp.categorias], tecnicas: tecnicasDe(tp).map((t) => t.id), peso: tp.peso });
  const pct = (tp) => {
    const total = validos().reduce((s, x) => s + x.peso, 0);
    return tp.ativo !== false && !problema(tp) && total ? Math.round(100 * tp.peso / total) : 0;
  };

  function mudou() { g.salvarReceita(); desenharTipos(); }

  // frase do tipo: "valor ou consumo · mexendo em um dígito ou mudando um pedaço · profissional"
  function frase(tp) {
    const lista = (itens) => itens.flatMap((x, i) => [i ? (i === itens.length - 1 ? " ou " : ", ") : "", el("b", {}, x)]);
    return el("span", { class: "frase" },
      tp.categorias.length ? ["Mexe em ", ...lista(tp.categorias.map(rotuloCat))] : el("i", {}, "(o quê?)"), " · ",
      tp.jeitos.length ? lista(tp.jeitos.map((j) => jeito(j)?.rotulo.toLowerCase() || j)) : el("i", {}, "(como?)"), " · ",
      tp.niveis.length ? ["capricho ", ...lista(tp.niveis.map((n) => CAPRICHO.find(([k]) => k === n)[2].toLowerCase()))]
        : el("i", {}, "(capricho?)"));
  }
  function segmentado(itens, atual, aoEscolher, rotulo) {
    return el("span", { class: "segmentado", role: "group", "aria-label": rotulo }, ...itens.map(([v, r]) => {
      const on = JSON.stringify(v) === JSON.stringify(atual);
      return el("button", { type: "button", class: on ? "ativa" : "", "aria-pressed": String(on), onclick: () => aoEscolher(v) }, r);
    }));
  }

  // ------------------------------------------------------------------ lista de tipos
  function desenharTipos() {
    const tipos = G.rec.tipos || [];
    por($("tipos-lista"), tipos.length ? tipos.map(cartaoTipo)
      : el("div", { class: "tipos-vazio" }, "Nenhum tipo de edição ainda. Comece por uma ideia pronta acima ou crie o seu."));
    const n = validos().length;
    $("tipos-resumo").textContent = `${n} ${n === 1 ? "tipo valendo" : "tipos valendo"}`;
  }
  function cartaoTipo(tp, i) {
    const p = problema(tp), ativo = tp.ativo !== false, cor = corDe(tp), por100 = pct(tp);
    return el("div", { class: `tipo ${ativo ? "" : "desligado"} ${p ? "com-problema" : ""}`, style: { borderLeftColor: cor } },
      el("div", { class: "tipo-cab" },
        el("input", { type: "checkbox", checked: ativo, "aria-label": `${ativo ? "desligar" : "ligar"} o tipo ${nomeDe(tp)}`,
          title: ativo ? "Desligar este tipo (fica guardado)" : "Ligar este tipo", onchange: (e) => { tp.ativo = e.target.checked; mudou(); } }),
        el("span", { class: "tipo-icone", "aria-hidden": "true" }, iconeDe(tp)),
        el("b", { class: "tipo-nome" }, nomeDe(tp)),
        segmentado(QUANTO, tp.peso, (v) => { tp.peso = v; mudou(); }, `quanto aparece: ${nomeDe(tp)}`)),
      el("div", { class: "tipo-frase" }, frase(tp)),
      el("div", { class: "tipo-rodape" },
        p ? el("span", { class: "alerta" }, `⚠ ${p}`)
          : el("span", { class: "dica" }, ativo ? `${por100}% das edições · ${tecnicasDe(tp).length} técnicas` : "desligado"),
        el("span", { class: "trilho-pct", "aria-hidden": "true" }, el("span", { style: { width: `${por100}%`, background: cor } })),
        el("span", { class: "tipo-acoes" },
          el("button", { type: "button", onclick: () => abrirEditor(tp) }, "✎ editar"),
          el("button", { type: "button", disabled: Boolean(p) || !ativo, title: "Gera um exemplo só com este tipo (nada é salvo)",
            onclick: () => testar(tp) }, "👁 testar"),
          el("button", { type: "button", title: "Duplicar", "aria-label": `duplicar ${nomeDe(tp)}`, onclick: () => {
            G.rec.tipos.splice(i + 1, 0, novoTipo({ ...tp, nome: tp.nome ? `${tp.nome} (cópia)` : "" })); mudou();
          } }, "⧉"),
          el("button", { type: "button", class: "perigo", title: "Remover", "aria-label": `remover ${nomeDe(tp)}`, onclick: () => {
            if (!confirm(`Remover o tipo '${nomeDe(tp)}'?`)) return;
            G.rec.tipos.splice(i, 1); mudou();
          } }, "🗑"))));
  }
  function desenharIdeias() {
    por($("tipos-ideias"), el("span", { class: "dica" }, "Ideias prontas (um clique acrescenta):"),
      ...IDEIAS.map((ideia) => el("button", { type: "button", class: "ideia",
        title: `${(ideia.categorias || ["todas as classes"]).map(rotuloCat).join(", ")} · ${(ideia.jeitos || ["todos os jeitos"])
          .map((j) => jeito(j)?.rotulo || j).join(", ")}`,
        onclick: () => { G.rec.tipos.push(daIdeia(ideia)); mudou(); aviso(`Tipo '${ideia.nome}' acrescentado. Use ✎ para ajustar.`, "ok"); } },
      `${ideia.icone} ${ideia.nome}`)));
  }

  // ------------------------------------------------------------------ editor de um tipo
  const dlg = el("dialog", { id: "dlg-tipo", class: "dlg-tipo", "aria-labelledby": "dlg-tipo-titulo" });
  document.body.append(dlg);
  let rascunho = null, alvo = null, nomeTocado = false;
  function abrirEditor(tp) {
    alvo = tp || null;
    rascunho = tp ? structuredClone(tp) : novoTipo({ categorias: [], jeitos: [] });
    nomeTocado = Boolean(tp?.nome);
    const nome = el("input", { type: "text", id: "dt-nome", value: rascunho.nome, maxlength: 60, placeholder: "ex.: valor da conta",
      oninput: (e) => { rascunho.nome = e.target.value; nomeTocado = Boolean(e.target.value); } });
    por(dlg, el("form", { method: "dialog", onsubmit: (e) => { e.preventDefault(); salvarEditor(); } },
      el("h2", { id: "dlg-tipo-titulo" }, alvo ? `Editar tipo: ${nomeDe(alvo)}` : "Novo tipo de edição"),
      el("div", { class: "dlg-tipo-corpo" },
        passo(1, "O que vai ser adulterado?", "pode marcar mais de um campo", "dt-classes"),
        passo(2, "Como?", "cada jeito junta técnicas parecidas; o gerador sorteia entre elas a cada edição", "dt-jeitos"),
        passo(3, "Capricho do falsificador", "quem fez a fraude: dá para misturar", "dt-capricho"),
        passo(4, "Quanto aparece no lote?", "em relação aos outros tipos", "dt-quanto"),
        el("label", { class: "nome-tipo" }, "Nome do tipo ", nome)),
      el("div", { class: "dlg-tipo-frase", id: "dt-frase", "aria-live": "polite" }),
      el("div", { class: "modal-acoes" },
        el("button", { type: "button", onclick: () => dlg.close() }, "Cancelar"),
        el("button", { type: "button", id: "dt-testar", title: "Guarda o tipo e gera um exemplo só com ele (nada é salvo)",
          onclick: () => { const tp = salvarEditor(); if (tp) testar(tp); } }, "👁 Guardar e testar"),
        el("button", { type: "submit", class: "primario", id: "dt-salvar" }, alvo ? "Guardar" : "Acrescentar"))));
    atualizarEditor();
    dlg.showModal();
    dlg.querySelector(".cartao-escolha")?.focus();
  }
  function passo(n, titulo, dica, id) {
    return el("section", { class: "passo-tipo", "aria-labelledby": `${id}-t` },
      el("h3", { id: `${id}-t` }, el("span", { class: "num" }, String(n)), ` ${titulo} `, el("span", { class: "dica" }, dica)),
      el("div", { id }));
  }
  function atualizarEditor() {
    const r = rascunho;
    const foco = document.activeElement?.dataset?.k;  // o redesenho não pode tirar o foco de quem usa teclado
    if (!nomeTocado) { r.nome = ""; $("dt-nome").placeholder = autoNome(r) || "ex.: valor da conta"; }
    por($("dt-classes"), ...GRUPOS.map(([grupo, ks]) => {
      const disp = ks.filter((k) => todasClasses().includes(k));
      if (!disp.length) return null;
      const todos = disp.every((k) => r.categorias.includes(k));
      return el("div", { class: "grupo-classes" },
        el("div", { class: "grupo-cab" }, grupo, el("button", { type: "button", class: "mini", "data-k": `g:${grupo}`,
          onclick: () => { r.categorias = todos ? r.categorias.filter((k) => !disp.includes(k)) : [...new Set([...r.categorias, ...disp])];
            atualizarEditor(); } }, todos ? "nenhum" : "todos")),
        el("div", { class: "grade-escolha" }, ...disp.map((k) => {
          const on = r.categorias.includes(k);
          return el("button", { type: "button", class: `cartao-escolha ${on ? "on" : ""}`, "aria-pressed": String(on), "data-k": `c:${k}`,
            title: G.cat.categorias.find((c) => c.chave === k)?.dica || "", onclick: () => { alternar(r.categorias, k); atualizarEditor(); } },
          el("span", { class: "ce-icone", "aria-hidden": "true" }, CLASSES[k]?.[0] || "🏷️"),
          el("span", { class: "ce-nome" }, rotuloCat(k)), el("span", { class: "ce-ex" }, CLASSES[k]?.[1] || ""));
        })));
    }));
    por($("dt-jeitos"), el("div", { class: "grade-escolha jeitos" }, ...G.cat.jeitos.map((j) => {
      const on = r.jeitos.includes(j.id);
      const ts = j.tecnicas.map(tec).filter(Boolean);
      const serve = !r.categorias.length || ts.some((t) => r.categorias.some((k) => compativel(t, k)));
      const ex = j.tecnicas.map((tid) => exemploDe(tid, r.categorias)).find(Boolean);
      return el("button", { type: "button", class: `cartao-escolha jeito ${on ? "on" : ""}`, "aria-pressed": String(on), "data-k": `j:${j.id}`,
        disabled: !serve && !on, title: serve ? j.dica : `não serve para ${r.categorias.map(rotuloCat).join(", ")}`,
        onclick: () => { alternar(r.jeitos, j.id); atualizarEditor(); } },
      el("span", { class: "ce-icone", "aria-hidden": "true" }, j.icone),
      el("span", { class: "ce-nome" }, j.rotulo),
      el("span", { class: "ce-antes-depois" }, el("s", {}, j.exemplo[0]), " → ", el("b", {}, j.exemplo[1] || "(apagado)")),
      ex ? el("span", { class: "ce-real", title: "exemplo real já gerado: antes e depois" },
        el("img", { src: recorte(ex, "original"), alt: "antes", loading: "lazy" }),
        el("img", { src: recorte(ex, "editada"), alt: "depois", loading: "lazy" })) : null,
      el("span", { class: "ce-ex" }, serve ? j.dica : "não serve para as classes escolhidas"),
      el("span", { class: "ce-niveis" }, ...CAPRICHO.map(([k, ic, rot]) => {
        const n = ts.filter((t) => t.nivel === k).length;
        return n ? el("span", { title: `${n} técnica(s) ${rot.toLowerCase()}` }, `${ic} ${n}`) : null;
      })));
    })));
    por($("dt-capricho"), el("div", { class: "grade-escolha capricho" }, ...CAPRICHO.map(([k, ic, rot, dica]) => {
      const on = r.niveis.includes(k);
      const n = r.jeitos.flatMap((j) => jeito(j)?.tecnicas || []).map(tec).filter((t) => t?.nivel === k).length;
      return el("button", { type: "button", class: `cartao-escolha ${on ? "on" : ""}`, "aria-pressed": String(on), "data-k": `n:${k}`,
        title: dica, onclick: () => { alternar(r.niveis, k); atualizarEditor(); } },
      el("span", { class: "ce-icone", "aria-hidden": "true" }, ic), el("span", { class: "ce-nome" }, rot),
      el("span", { class: "ce-ex" }, k === "ia" && !G.iaOnline ? "servidor de IA desligado agora" : dica),
      el("span", { class: "ce-niveis" }, r.jeitos.length ? `${n} técnica(s) nos jeitos escolhidos` : ""));
    })));
    por($("dt-quanto"), segmentado(QUANTO, r.peso, (v) => { r.peso = v; atualizarEditor(); }, "quanto aparece"));
    const p = problema(r);
    por($("dt-frase"), p ? el("span", { class: "alerta" }, `⚠ ${p}`)
      : [frase(r), el("span", { class: "dica" }, ` → ${tecnicasDe(r).length} técnicas, ${paresDe(r).length} combinações técnica × classe`)]);
    $("dt-salvar").disabled = Boolean(p);
    $("dt-testar").disabled = Boolean(p);
    if (foco) dlg.querySelector(`[data-k="${CSS.escape(foco)}"]`)?.focus();
  }
  function salvarEditor() {
    if (problema(rascunho)) return null;
    const tp = alvo ? Object.assign(alvo, rascunho) : rascunho;
    if (!alvo) G.rec.tipos.push(tp);
    dlg.close();
    mudou();
    return tp;
  }
  function recorte(ex, tipo) {  // em volta da edição, com folga para ver o contexto
    const [x0, y0, x1, y1] = ex.bbox;
    const m = Math.max(8, Math.round(0.6 * (y1 - y0)));
    return `/api/catalogo/imagem/${ex.split}/${ex.nome}/${tipo}.png?max=240&x0=${x0 - 2 * m}&y0=${y0 - m}&x1=${x1 + 2 * m}&y1=${y1 + m}`;
  }
  const testar = (tp) => g.previa({ tipos: [paraEnvio(tp)], edicoes: [1, 2] }, `só o tipo '${nomeDe(tp)}'`);

  // ------------------------------------------------------------------ quanto e canal
  function desenharQuanto() {
    const r = G.rec;
    const mudar = (v) => { r.variacoes = Math.max(1, Math.min(50, Math.round(Number(v)) || 1)); g.salvarReceita(); desenharQuanto(); };
    const padrao = EDICOES.some(([v]) => v[0] === r.edicoes[0] && v[1] === r.edicoes[1]);
    por($("quanto"),
      el("div", { class: "q-linha" }, el("span", { class: "q-rot", id: "q-var" }, "Versões de cada documento"),
        el("span", { class: "stepper" },
          el("button", { type: "button", "aria-label": "uma versão a menos", onclick: () => mudar(r.variacoes - 1) }, "−"),
          el("input", { type: "number", min: 1, max: 50, value: r.variacoes, "aria-labelledby": "q-var", onchange: (e) => mudar(e.target.value) }),
          el("button", { type: "button", "aria-label": "uma versão a mais", onclick: () => mudar(r.variacoes + 1) }, "+")),
        el("span", { class: "dica" }, "cada versão é uma fraude diferente do mesmo documento")),
      el("div", { class: "q-linha" }, el("span", { class: "q-rot" }, "Edições em cada imagem"),
        segmentado(padrao ? EDICOES : [...EDICOES, [r.edicoes, `${r.edicoes[0]} a ${r.edicoes[1]}`]], r.edicoes,
          (v) => { r.edicoes = [...v]; g.salvarReceita(); desenharQuanto(); }, "edições em cada imagem"),
        el("span", { class: "dica" }, "várias edições na mesma imagem, cada uma de um tipo sorteado")));
  }
  function desenharCanal() {
    const r = G.rec;
    por($("canal-cartoes"), ...G.cat.canais.map(([k, rot]) => {
      const on = r.canal.perfil === k;
      const [ic, dica] = CANAIS[k] || ["📨", ""];
      return el("button", { type: "button", class: `cartao-escolha ${on ? "on" : ""}`, "aria-pressed": String(on), title: rot,
        onclick: () => { r.canal.perfil = k; g.salvarReceita(); desenharCanal(); } },
      el("span", { class: "ce-icone", "aria-hidden": "true" }, ic), el("span", { class: "ce-nome" }, rot.replace(/\s*\(.*\)$/, "")),
      el("span", { class: "ce-ex" }, k === "misturado" ? `${dica} (recomendado)` : dica));
    }));
    const fisico = !["nenhum", "whatsapp"].includes(r.canal.perfil);  // marca no papel pede captura física
    const p100 = Math.round(r.canal.prob * 100);
    por($("canal-extra"), r.canal.perfil === "nenhum"
      ? el("span", { class: "dica" }, "Sem canal: o detector vê a imagem exatamente como foi editada.")
      : el("label", { class: "toggle" }, "Passa pelo canal em ", el("input", { type: "range", min: 10, max: 100, step: 10, value: p100,
        "aria-label": "porcentagem das versões que passam pelo canal",
        oninput: (e) => { r.canal.prob = Number(e.target.value) / 100; e.target.nextSibling.textContent = ` ${e.target.value}%`; },
        onchange: () => g.salvarReceita() }), ` ${p100}%`, " das versões"),
    fisico ? el("label", { class: "toggle", title: "Carimbo, autenticação, anotação à mão, marca-texto, dobra: iguais na original e na editada" },
      el("input", { type: "checkbox", checked: r.marcas.prob > 0, onchange: (e) => { r.marcas.prob = e.target.checked ? 0.3 : 0; g.salvarReceita(); } }),
      " carimbos e anotações em algumas versões (ensinam que marca não é fraude)") : null,
    r.canal.perfil !== "nenhum" && r.falsificadores?.prob > 0 ? el("p", { class: "dica" },
      "Nas versões feitas como uma pessoa faria, o caminho é o de cada falsificador (quem edita no celular manda a foto da tela ou pelo WhatsApp; "
      + "quem edita o PDF costuma mandar o arquivo); este cartão vale para as outras versões.") : null);
    $("canal-resumo").textContent = G.cat.canais.find(([k]) => k === r.canal.perfil)?.[1] || "";
  }

  // ------------------------------------------------------------------ quem falsifica
  const pesosQuem = () => G.rec.falsificadores.pesos || Object.fromEntries(G.cat.falsificadores.map((f) => [f.id, f.peso]));
  const pesosMotivos = () => G.rec.falsificadores.motivos || Object.fromEntries(G.cat.motivos.map((m) => [m.id, m.peso]));
  const soma = (t) => Object.values(t).reduce((a, b) => a + (b > 0 ? b : 0), 0);
  function desenharQuem() {
    const fz = G.rec.falsificadores;
    const pesos = pesosQuem(), tot = soma(pesos) || 1;
    const ligado = fz.prob > 0;
    $("quem-prob").value = Math.round(fz.prob * 100);
    $("quem-prob-txt").textContent = ligado ? ` ${Math.round(fz.prob * 100)}%` : " 0% (desligado)";
    $("quem-resumo").textContent = ligado ? `${Math.round(fz.prob * 100)}% das versões · ${Object.values(pesos).filter((v) => v > 0).length} falsificadores` : "desligado";
    $("bloco-quem").classList.toggle("desligado", !ligado);
    por($("quem-cartoes"), ...G.cat.falsificadores.map((f) => {
      const on = (pesos[f.id] || 0) > 0;
      const semIA = f.precisa_ia && !G.iaOnline;
      return el("button", { type: "button", class: `cartao-escolha ${on ? "on" : ""}`, "aria-pressed": String(on),
        disabled: !ligado || (semIA && !on), title: f.dica, onclick: () => {
          const p = { ...pesos };
          p[f.id] = on ? 0 : f.peso;
          if (!Object.values(p).some((v) => v > 0)) { aviso("Deixe pelo menos um falsificador ligado, ou ponha 0% nas versões feitas assim.", "erro"); return; }
          fz.pesos = p; g.salvarReceita(); desenharQuem(); g.atualizarResumo();
        } },
      el("span", { class: "ce-icone", "aria-hidden": "true" }, f.icone), el("span", { class: "ce-nome" }, f.rotulo),
      el("span", { class: "ce-ex" }, semIA ? "servidor de IA desligado agora" : f.dica),
      el("span", { class: "ce-niveis" }, on ? `${Math.round(100 * pesos[f.id] / tot)}% das versões humanas` : "desligado"));
    }));
    const motivos = pesosMotivos(), totM = soma(motivos) || 1;
    por($("quem-motivos"), ...G.cat.motivos.map((m) => {
      const on = (motivos[m.id] || 0) > 0;
      return el("button", { type: "button", class: `chip-toggle ${on ? "on" : ""}`, "aria-pressed": String(on), disabled: !ligado,
        title: `${m.dica}${m.campos.length ? ` — campos: ${m.campos.map(rotuloCat).join(", ")}` : ""}`, onclick: () => {
          const p = { ...motivos };
          p[m.id] = on ? 0 : m.peso;
          if (!Object.values(p).some((v) => v > 0)) { aviso("Deixe pelo menos um motivo ligado.", "erro"); return; }
          fz.motivos = p; g.salvarReceita(); desenharQuem(); g.atualizarResumo();
        } }, `${m.icone} ${m.rotulo}`, on ? el("span", { class: "dica" }, ` ${Math.round(100 * motivos[m.id] / totM)}%`) : null);
    }));
  }
  $("quem-prob").addEventListener("input", (e) => {
    G.rec.falsificadores.prob = Number(e.target.value) / 100;
    $("quem-prob-txt").textContent = G.rec.falsificadores.prob > 0 ? ` ${e.target.value}%` : " 0% (desligado)";
  });
  $("quem-prob").addEventListener("change", () => { g.salvarReceita(); desenharQuem(); desenharCanal(); g.atualizarResumo(); });
  function fraseQuem() {  // para o plano do lote
    const fz = G.rec.falsificadores;
    if (!(fz.prob > 0)) return null;
    const pesos = pesosQuem(), tot = soma(pesos) || 1;
    const quem = G.cat.falsificadores.filter((f) => (pesos[f.id] || 0) > 0 && !(f.precisa_ia && !G.iaOnline))
      .map((f) => `${f.rotulo.toLowerCase()} ${Math.round(100 * pesos[f.id] / tot)}%`);
    const motivos = pesosMotivos(), totM = soma(motivos) || 1;
    const porque = G.cat.motivos.filter((m) => (motivos[m.id] || 0) > 0).map((m) => `${m.rotulo.toLowerCase()} ${Math.round(100 * motivos[m.id] / totM)}%`);
    return el("p", { class: "dica" }, el("b", {}, `👤 ${Math.round(fz.prob * 100)}% das versões feitas como uma pessoa faria`),
      `: ${quem.join(", ")} · motivos: ${porque.join(", ")}. Cada uma usa só as técnicas da ferramenta dela, dentro do que a receita permite, `
      + "e mexe em campos que combinam com o motivo.");
  }

  // ------------------------------------------------------------------ plano do lote (coluna da direita)
  function desenharPlano() {
    if (G.modo === "exato") {
      const cats = G.rec.exato_categorias || [];
      por($("plano-corpo"), cats.length
        ? el("div", {}, el("p", {}, cats.length > 1 && G.rec.exato_subconjuntos !== false
            ? "Cada versão sorteia quais destas classes entram (às vezes todas, às vezes só uma parte); toda classe que entra tem todos os seus campos trocados, na mesma fonte:"
            : "Em cada documento, todos os campos destas classes são trocados, na mesma fonte:"),
          el("ul", { class: "plano-lista" }, ...cats.map((k) => el("li", {}, `${CLASSES[k]?.[0] || "🏷️"} ${rotuloCat(k)}`))),
          el("p", { class: "dica" }, "A imagem salva é a original com essas edições, e nada mais (sem canal de envio, marcas ou disfarce)."))
        : el("p", { class: "dica" }, "Marque o que editar."));
      return;
    }
    const vs = usandoTipos() ? validos() : [];
    const r = G.rec;
    const canal = r.canal.perfil === "nenhum" ? "sem canal de envio"
      : `canal: ${(G.cat.canais.find(([k]) => k === r.canal.perfil)?.[1] || r.canal.perfil).toLowerCase()} em ${Math.round(r.canal.prob * 100)}% das versões`;
    const geral = el("p", { class: "dica" }, `Cada imagem recebe ${r.edicoes[0] === r.edicoes[1] ? r.edicoes[0] : `de ${r.edicoes[0]} a ${r.edicoes[1]}`} `
      + `${r.edicoes[1] === 1 ? "edição" : "edições"}; ${canal}.`);
    if (!usandoTipos()) {  // modo avançado sem tipos: por família de técnica
      const ef = efetivas();
      const fam = {};
      for (const [tid, p] of Object.entries(ef.tecnicas)) { const f = tec(tid)?.familia; if (f) fam[f] = (fam[f] || 0) + p; }
      const tot = Object.values(fam).reduce((a, b) => a + b, 0) || 1;
      por($("plano-corpo"), el("div", { class: "plano-barra", role: "img", "aria-label": "divisão por família de técnica" },
        ...Object.entries(fam).map(([f, p]) => el("span", { style: { flex: p, background: g.COR_FAMILIA[f] || "#888" },
          title: `${G.cat.familias[f]?.rotulo || f}: ${Math.round(100 * p / tot)}%` }))),
      el("ul", { class: "plano-lista" }, ...Object.entries(fam).map(([f, p]) => el("li", {},
        el("span", { class: "chip", style: { background: g.COR_FAMILIA[f] || "#888" } }), ` ${G.cat.familias[f]?.rotulo || f} `,
        el("span", { class: "dica" }, `${Math.round(100 * p / tot)}%`)))), geral, fraseQuem());
      return;
    }
    if (!vs.length) {
      por($("plano-corpo"), el("p", { class: "dica" }, "Nenhum tipo de edição valendo: crie um tipo ou use uma ideia pronta."));
      return;
    }
    const total = vs.reduce((s, tp) => s + tp.peso, 0);
    por($("plano-corpo"),
      el("div", { class: "plano-barra", role: "img", "aria-label": vs.map((tp) => `${nomeDe(tp)} ${Math.round(100 * tp.peso / total)}%`).join(", ") },
        ...vs.map((tp) => el("span", { style: { flex: tp.peso, background: corDe(tp) }, title: `${nomeDe(tp)}: ${Math.round(100 * tp.peso / total)}%` }))),
      el("ul", { class: "plano-lista" }, ...vs.map((tp) => {
        const reais = tecnicasDe(tp).map((t) => [t, exemploDe(t.id, tp.categorias)]).filter(([, ex]) => ex)
          .sort(([, a], [, b]) => tp.categorias.includes(b.categoria) - tp.categorias.includes(a.categoria)).slice(0, 4);
        return el("li", {},
          el("div", {}, el("span", { class: "chip", style: { background: corDe(tp) } }), ` ${iconeDe(tp)} `, el("b", {}, nomeDe(tp)),
            el("span", { class: "dica" }, ` ${Math.round(100 * tp.peso / total)}% das edições`)),
          el("div", { class: "dica" }, frase(tp)),
          reais.length ? el("div", { class: "exemplos-reais" }, ...reais.map(([t, ex]) => el("figure", { title: `${t.nome} · ${rotuloCat(ex.categoria)}` },
            el("img", { src: recorte(ex, "original"), alt: `antes: ${t.nome}`, loading: "lazy" }),
            el("img", { src: recorte(ex, "editada"), alt: `depois: ${t.nome}`, loading: "lazy" }),
            el("figcaption", {}, t.nome)))) : null);
      })), geral, fraseQuem(),
      Object.keys(exemplos).length ? null : el("p", { class: "dica" }, "Os exemplos reais (antes → depois) aparecem aqui depois do primeiro lote gerado."));
  }

  // ------------------------------------------------------------------ modo fácil × avançado
  function trocarModo(m) {
    if (!["exato", "facil", "avancado"].includes(m)) m = "exato";
    G.modo = m;
    guardar("modoGeradorV2", m);
    if (m === "facil") G.rec.tipos_desligados = false;
    for (const b of document.querySelectorAll("#modo-receita button")) {
      const on = b.dataset.modo === m;
      b.classList.toggle("ativa", on);
      b.setAttribute("aria-selected", String(on));
    }
    for (const id of ["bloco-tec", "bloco-cat", "bloco-qtd"]) $(id).hidden = m !== "avancado";
    for (const id of ["bloco-tipos", "bloco-quanto", "bloco-canal", "bloco-quem"]) $(id).hidden = m !== "facil";
    $("bloco-exato").hidden = m !== "exato";
    if (m === "facil") { desenharQuanto(); desenharCanal(); desenharQuem(); }
    else if (m === "exato") desenharExato();
    else g.montarQtd();
    g.salvarReceita();
  }

  // ------------------------------------------------------------------ modo exato: marque o que editar
  const PRESETS_EXATO = [["Nome e endereço", ["nome", "endereço"]],
    ["Endereço completo", ["endereço", "bairro", "cep", "cidade/uf"]],
    ["Nome, endereço completo e CPF", ["nome", "cpf/cnpj", "endereço", "bairro", "cep", "cidade/uf"]]];
  function desenharExato() {
    const marcadas = new Set(G.rec.exato_categorias || []);
    const mudar = (lista) => { G.rec.exato_categorias = lista; g.salvarReceita(); desenharExato(); };
    por($("exato-presets"), el("span", { class: "dica" }, "Atalhos:"),
      ...PRESETS_EXATO.map(([nome, lista]) => el("button", { type: "button", onclick: () => mudar([...lista]) }, nome)),
      el("button", { type: "button", onclick: () => mudar([]) }, "nenhum"));
    const caixa = (k) => {
      const c = G.cat.categorias.find((x) => x.chave === k);
      if (!c) return null;
      return el("label", { class: `exato-item ${marcadas.has(k) ? "on" : ""}`, title: c.dica || "" },
        el("input", { type: "checkbox", checked: marcadas.has(k), "data-classe": k,
          onchange: (e) => mudar(e.target.checked ? [...marcadas, k] : [...marcadas].filter((x) => x !== k)) }),
        ` ${CLASSES[k]?.[0] || "🏷️"} ${c.rotulo}`, el("span", { class: "dica" }, ` ${CLASSES[k]?.[1] || ""}`));
    };
    por($("exato-classes"), ...GRUPOS.map(([nome, ks]) => el("div", { class: "exato-grupo" },
      el("div", { class: "grupo-cab" }, nome), ...ks.map(caixa).filter(Boolean))));
    const v = $("exato-variacoes");
    v.value = G.rec.variacoes;
    v.onchange = () => { G.rec.variacoes = Math.max(1, Math.min(50, Number(v.value) || 1)); g.salvarReceita(); };
  }
  // no avançado com tipos valendo: técnicas e classes ficam só para ver (as opções ⚙ continuam valendo)
  function atualizarTrava() {
    const valendo = G.modo === "avancado" && usandoTipos();
    $("receita").classList.toggle("tipos-valendo", valendo);
    $("aviso-tipos").hidden = !valendo;
    const n = validos().length;
    if (valendo) $("aviso-tipos-txt").textContent = n === 1
      ? "O tipo de edição do modo fácil está valendo: só as combinações dele são geradas."
      : `Os ${n} tipos de edição do modo fácil estão valendo: só as combinações deles são geradas.`;
  }

  async function recarregarExemplos() {
    try { exemplos = await g.get("/api/catalogo/exemplos"); } catch { exemplos = {}; }
    desenharPlano();
  }
  function aoCarregar() {  // receita importada
    if (Array.isArray(G.rec.tipos) && G.rec.tipos.length) {
      G.rec.tipos = G.rec.tipos.map(limpar);
    } else {
      G.rec.tipos = [];
      G.rec.tipos_desligados = true;
      trocarModo("avancado");
      aviso("Esta receita não tem tipos de edição: aberta no modo avançado.");
    }
    redesenhar();
  }
  function redesenhar() {
    desenharIdeias(); desenharTipos();
    if (G.modo === "facil") { desenharQuanto(); desenharCanal(); desenharQuem(); }
    if (dlg.open) atualizarEditor();
    g.atualizarResumo();
  }

  // ------------------------------------------------------------------ início
  if (G.rec.tipos === undefined) {  // receita nova: as ideias iniciais, valendo só para quem começa no modo fácil
    G.rec.tipos = IDEIAS.slice(0, INICIAIS).map(daIdeia);
    if (G.modo !== "facil") G.rec.tipos_desligados = true;
  } else G.rec.tipos = (G.rec.tipos || []).map(limpar);
  $("tipo-novo").onclick = () => abrirEditor(null);
  $("aviso-tipos-desligar").onclick = () => { G.rec.tipos_desligados = true; g.salvarReceita(); };
  for (const b of document.querySelectorAll("#modo-receita button")) b.onclick = () => trocarModo(b.dataset.modo);
  trocarModo(G.modo);
  redesenhar();
  recarregarExemplos();
  return {
    usandoTipos, efetivas, tiposParaEnvio: () => validos().map(paraEnvio), aoResumo: () => { atualizarTrava(); desenharPlano(); },
    trocarModo,
    semTiposValendo: () => usandoTipos() && !validos().length, redesenhar, aoCarregar, recarregarExemplos,
  };
}
