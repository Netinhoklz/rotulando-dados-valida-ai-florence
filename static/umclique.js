// Tela "Gerar fraudes" (o que aparece ao abrir o gerador): um clique. Documentos, versões por documento e o que
// mudar; só edições PROFISSIONAIS (perfil "profissional" do motor: a mesma fonte, letras do próprio documento,
// trocar só um pedaço). "Ver um exemplo" roda a prévia num documento sorteado (nada é salvo) e mostra ANTES |
// DEPOIS com o que mudou; durante o lote, barra, tempo e uma galeria ao vivo. A receita completa (modos Exato,
// Fácil e Avançado) continua em "Opções avançadas" (gerador.js e assistente.js).
import { CLASSES, GRUPOS } from "./assistente.js";

const OQUE = ["extras", "base", "escolher"];
const EXTRAS = ["cpf/cnpj", "cep", "data", "valor"];  // "às vezes também" (só no motor sem o perfil: receitaAntiga)
const SEG_VERSAO = 9;     // medido sem IA: ~9 s por versão salva (nome, endereço e às vezes outros campos)
const SEG_LEITURA = 12;   // 1ª leitura (OCR) de um documento que ainda não foi lido
const limitar = (v) => Math.max(1, Math.min(50, Math.round(Number(v)) || 1));
const plural = (n, um, varios) => `${n} ${n === 1 ? um : varios}`;
const tempo = (s) => (s < 90 ? `${Math.max(5, Math.round(s / 5) * 5)} s` : s < 5400 ? `${Math.round(s / 60)} min`
  : `${(s / 3600).toFixed(1).replace(".", ",")} h`);
const b64 = (s) => `data:image/png;base64,${s}`;
const versaoN = (v) => Number(String(v.nome).split("__v")[1]) || "";

// erros do servidor em linguagem simples (o texto original fica no title, para quem precisar)
const EXPLICA = [
  [/nenhuma técnica coube|nenhum campo|não achei|nada para editar/i, "não achei nome ou endereço legível que desse para trocar"],
  [/cannot identify image|não consegui (abrir|carregar|ler o arquivo)|arquivo .*(corrompido|inválido)/i, "não consegui abrir o arquivo"],
  [/tesseract|\bocr\b|leitor/i, "o leitor de texto falhou neste documento"],
  [/MemoryError|memória/i, "faltou memória no computador"],
  [/No space left|Errno 28|disco cheio/i, "o disco está cheio"],
];
const explicar = (m) => (EXPLICA.find(([re]) => re.test(m))?.[1] || m);
function simples(e) {
  if (e instanceof TypeError) return "sem resposta do servidor. Confira se o rotulador está ligado e tente de novo.";
  const m = e?.message || String(e);
  if (/já há uma geração rodando/i.test(m)) return "já há uma geração rodando. Espere terminar ou clique em Parar.";
  if (/nenhum documento/i.test(m)) return "nenhum documento para editar: marque pelo menos um.";
  if (/documento não encontrado/i.test(m)) return "esse documento não está mais na pasta de dados. Recarregue a página.";
  return explicar(m);
}
// "conta.jpg v2: erro" -> "conta.jpg (versão 2): explicação"
function erroDoLote(m) {
  const x = /^(.+?)(?: v(\d+))?: ([\s\S]*)$/.exec(m);
  return x ? `${x[1]}${x[2] ? ` (versão ${x[2]})` : ""}: ${explicar(x[3])}` : explicar(m);
}
// motor antigo (sem o perfil): a receita sem técnicas é recusada com esta mensagem
const motorAntigo = (e) => e?.status === 400 && /pelo menos uma t[ée]cnica/i.test(e.message || "");

export function instalarUmClique(g) {
  const { G, $, el, por, aviso, guardar, lembrar, get, post, rotuloCat, rotuloSplit, SPLITS, caixaDe, corCategoria } = g;
  const existe = (k) => k !== "outro" && G.cat.categorias.some((c) => c.chave === k);
  const nomesSplit = SPLITS.map(([sp]) => sp);
  const U = lerEstado(lembrar("umClique", null));  // o que a pessoa escolheu (lembrado)
  // o resto: lote acompanhado, galeria, motor (novo/antigo), exemplo
  const L = { lote: null, galeria: new Map(), viu: false, motor: null, expandida: null, rota: true, ultimoDoc: null,
    ocupado: false, iniciando: false, acoes: "", estilos: null };

  function lerEstado(s) {
    s = s && typeof s === "object" ? s : {};
    const pendentes = nomesSplit.filter((sp) => (G.docs[sp] || []).some((d) => !d.versoes));
    return {
      splits: Array.isArray(s.splits) ? s.splits.filter((sp) => nomesSplit.includes(sp)) : pendentes.length ? pendentes : ["treino"],
      fora: Array.isArray(s.fora) ? s.fora.map(String) : [],  // documentos desmarcados em "escolher documentos…"
      variacoes: limitar(s.variacoes ?? 3),
      oque: OQUE.includes(s.oque) ? s.oque : "extras",
      campos: (Array.isArray(s.campos) ? s.campos : ["nome", "endereço"]).filter(existe),
      estilo: typeof s.estilo === "string" ? s.estilo : "recomendado",  // estilo pronto (o servidor diz quais existem)
    };
  }
  function mudou() { guardar("umClique", U); desenhar(); }

  // ------------------------------------------------------------------ documentos
  const fora = () => new Set(U.fora);
  const doSplit = (sp) => (G.docs[sp] || []).map((d) => ({ ...d, split: sp }));
  function escolhidos() {
    const f = fora();
    return U.splits.flatMap(doSplit).filter((d) => !f.has(d.id));
  }

  function desenharSplits() {
    const f = fora();
    por($("uc-splits"), ...SPLITS.map(([sp, rot]) => {
      const docs = G.docs[sp] || [];
      const n = docs.filter((d) => !f.has(d.id)).length;
      const on = U.splits.includes(sp) && n > 0;
      const novos = docs.filter((d) => !d.versoes).length;
      return el("label", { class: `uc-chip split-${sp}${on ? " on" : ""}${docs.length ? "" : " vazio"}`,
        title: docs.length ? `${plural(docs.length, "documento", "documentos")}; ${novos} ainda sem nenhuma versão` : "nenhum documento neste grupo" },
      el("input", { type: "checkbox", checked: on, disabled: !docs.length, "data-foco": `sp:${sp}`, onchange: (e) => {
        const s = new Set(U.splits);
        if (e.target.checked) {  // marcar o grupo = todos os documentos dele
          s.add(sp);
          const dele = new Set(docs.map((x) => x.id));
          U.fora = U.fora.filter((id) => !dele.has(id));
        } else s.delete(sp);
        U.splits = nomesSplit.filter((x) => s.has(x));
        mudou();
      } }),
      el("span", {}, rot), el("b", {}, on && n < docs.length ? `${n}/${docs.length}` : String(docs.length)),
      docs.length && novos < docs.length ? el("span", { class: "uc-chip-pend" }, novos ? `${novos} sem versão` : "todos já têm versão") : null);
    }));
  }

  const dlgDocs = el("dialog", { id: "uc-dlg-docs", class: "uc-dlg-docs", "aria-labelledby": "uc-dlg-docs-titulo" });
  document.body.append(dlgDocs);
  function abrirDocs() {
    const busca = el("input", { type: "search", placeholder: "filtrar por nome…", "aria-label": "filtrar documentos por nome",
      oninput: () => lista() });
    const ul = el("ul", { class: "lista-docs uc-lista-docs" });
    const contagem = el("span", { class: "dica", "aria-live": "polite" });
    const resumo = () => { contagem.textContent = `${escolhidos().length} de ${U.splits.flatMap(doSplit).length} marcados`; };
    const marcar = (regra) => {
      const f = fora();
      for (const d of U.splits.flatMap(doSplit)) { if (regra(d)) f.delete(d.id); else f.add(d.id); }
      U.fora = [...f]; mudou(); lista();
    };
    function lista() {
      const q = busca.value.trim().toLowerCase(), f = fora();
      const docs = U.splits.flatMap(doSplit).filter((d) => !q || d.nome.toLowerCase().includes(q));
      por(ul, docs.length ? docs.map((d) => el("li", { class: f.has(d.id) ? "" : "marcado" }, el("label", {},
        el("input", { type: "checkbox", checked: !f.has(d.id), onchange: (e) => {
          const s = fora();
          if (e.target.checked) s.delete(d.id); else s.add(d.id);
          U.fora = [...s];
          e.target.closest("li").classList.toggle("marcado", e.target.checked);
          mudou(); resumo();
        } }),
        el("img", { src: `/api/miniatura/${d.id}.jpg`, loading: "lazy", alt: "" }),
        el("span", { class: "nome", title: d.nome }, d.nome),
        el("span", { class: `pilula split-${d.split}` }, rotuloSplit(d.split)),
        d.versoes ? el("span", { class: "pilula ok" }, plural(d.versoes, "versão", "versões")) : el("span", { class: "pilula" }, "sem versão"))))
        : el("li", { class: "vazio" }, U.splits.length ? "Nenhum documento com esse nome." : "Marque um grupo (Treino, Teste ou Validação) primeiro."));
      resumo();
    }
    por(dlgDocs, el("form", { method: "dialog" },
      el("h3", { id: "uc-dlg-docs-titulo" }, "Escolher documentos"),
      el("p", { class: "dica" }, `Documentos de ${U.splits.map(rotuloSplit).join(", ") || "nenhum grupo"}. `
        + "Cada versão fica no grupo do próprio documento."),
      el("div", { class: "linha" },
        el("button", { type: "button", onclick: () => marcar(() => true) }, "todos"),
        el("button", { type: "button", onclick: () => marcar(() => false) }, "nenhum"),
        el("button", { type: "button", title: "Só os que ainda não têm nenhuma versão salva", onclick: () => marcar((d) => !d.versoes) }, "sem versão"),
        busca, contagem),
      ul,
      el("div", { class: "modal-acoes" }, el("button", { value: "ok", class: "primario" }, "Pronto"))));
    lista();
    dlgDocs.showModal();
  }

  // ------------------------------------------------------------------ o que mudar
  function desenharOque() {
    for (const r of document.querySelectorAll('input[name="uc-oque"]')) r.checked = r.value === U.oque;
    $("uc-variacoes").value = U.variacoes;
    const c = $("uc-campos");
    c.hidden = U.oque !== "escolher";
    if (c.hidden) return;
    const marc = new Set(U.campos);
    por(c, el("p", { class: "dica" }, "Cada versão troca os campos marcados, em todo lugar onde eles aparecem no documento:"),
      el("div", { class: "exato-grupos" }, ...GRUPOS.map(([nome, ks]) => el("div", { class: "exato-grupo", role: "group", "aria-label": nome },
        el("div", { class: "grupo-cab" }, nome),
        ...ks.filter(existe).map((k) => el("label", { class: `exato-item${marc.has(k) ? " on" : ""}`,
          title: G.cat.categorias.find((x) => x.chave === k)?.dica || "" },
        el("input", { type: "checkbox", checked: marc.has(k), "data-campo": k, "data-foco": `campo:${k}`, onchange: (e) => {
          U.campos = e.target.checked ? [...U.campos, k] : U.campos.filter((x) => x !== k);
          mudou(); conhecerMotor();
        } }),
        ` ${CLASSES[k]?.[0] || "🏷️"} ${rotuloCat(k)}`, el("span", { class: "dica" }, ` ${CLASSES[k]?.[1] || ""}`)))))));
  }

  // ------------------------------------------------------------------ receita (contrato do motor) e envio
  function receita() {
    const docs = escolhidos();
    const splits = U.splits.filter((sp) => docs.some((d) => d.split === sp));
    const todos = splits.flatMap(doSplit);
    const quem = g.nomeOperador();
    const r = { nome: "gerar fraudes (um clique)", perfil: "profissional", extras: U.oque === "extras", variacoes: U.variacoes,
      documentos: { splits, ids: docs.length === todos.length ? null : docs.map((d) => d.id), so_pendentes: false },
      operador: quem ? `gerador automático (${quem})` : "gerador automático" };
    if (U.oque === "escolher") r.categorias_base = [...U.campos];
    if (L.estilos) r.estilo = U.estilo;
    return r;
  }
  // motor ainda sem o perfil: a receita "mesma fonte" do modo exato (gerador.receita_limpa), com os campos pedidos
  function receitaAntiga(r) {
    const cats = (r.categorias_base || (r.extras ? ["nome", "endereço", ...EXTRAS] : ["nome", "endereço"])).filter(existe);
    const tecnicas = { "campo.mesma_fonte": 1 };
    if (cats.includes("codigo barras")) tecnicas["codigo.regenerar"] = 1;
    if (cats.includes("qr code/pix")) tecnicas["codigo.qr"] = 1;
    const out = { ...r, exato: true, subconjuntos: true, tecnicas, categorias: Object.fromEntries(cats.map((k) => [k, 1])),
      edicoes: [1, 3], distribuicao: "equilibrada", acabamento: { ruido: "auto", disfarce: "nunca", jpeg: 0 }, canal: null, marcas: null };
    delete out.perfil; delete out.extras; delete out.categorias_base;
    return out;
  }
  async function enviar(rota, corpo) {
    if (L.motor !== "antigo") {
      try {
        const r = await post(rota, corpo);
        L.motor = "novo";
        return r;
      } catch (e) {
        if (!motorAntigo(e)) throw e;
        L.motor = "antigo";
        desenharDetalhes();
      }
    }
    return post(rota, { ...corpo, receita: receitaAntiga(corpo.receita) });
  }

  // "o que o gerador vai usar" (só quando a pessoa abre: a rota é opcional e um 404 não deve sujar o console à toa):
  // a receita expandida do motor ou, sem ela, se o motor tem o perfil
  async function conhecerMotor() {
    if (!$("uc-detalhes").open) return;
    if (L.rota) {
      const q = new URLSearchParams({ extras: U.oque === "extras" ? "1" : "0", ...(U.oque === "escolher" ? { base: U.campos.join(",") } : {}),
        ...(L.estilos ? { estilo: U.estilo } : {}) });
      try {
        L.expandida = await get(`/api/gerador/receita_profissional?${q}`);
        L.motor = "novo";
      } catch (e) {
        L.expandida = null;
        if (e.status === 404) L.rota = false;
      }
    }
    if (!L.rota && L.motor === null) {  // sonda: um motor com o perfil lê a receita e só então recusa o documento vazio
      try { await post("/api/gerador/previa", { receita: { ...receita(), documentos: { splits: ["treino"], ids: null } }, doc: "" }); }
      catch (e) { if (e.status) L.motor = motorAntigo(e) ? "antigo" : "novo"; }
    }
    desenharDetalhes();
  }
  const GRANULARIDADE = { campo: "o campo inteiro", trecho: "só um pedaço (número da casa, sobrenome, dia, centavos)",
    caractere: "uma letra ou um dígito", acrescimo: "um complemento a mais ou a menos (APTO, sobrenome)" };
  const GRUPO_EXTRA = { documento: "o CPF", local: "CEP, bairro e cidade", datas: "as datas", valores: "o valor e o consumo" };
  const ou = (xs) => (xs.length > 1 ? `${xs.slice(0, -1).join(", ")} e/ou ${xs.at(-1)}` : xs[0] || "");
  function desenharDetalhes() {
    const corpo = $("uc-detalhes-corpo");
    const nomeTec = (id) => g.tec(id)?.nome || id;
    const pesos = (v) => (Array.isArray(v) ? Object.fromEntries(v.map((k) => [k, 1])) : v && typeof v === "object" ? v : null);
    const lista = (obj, nomeDe) => {
      const tot = Object.values(obj).reduce((a, b) => a + (Number(b) || 0), 0) || 1;
      return el("ul", {}, ...Object.entries(obj).sort((a, b) => b[1] - a[1]).map(([k, p]) =>
        el("li", {}, nomeDe(k), el("span", { class: "dica" }, ` ${Math.round(100 * (Number(p) || 0) / tot)}%`))));
    };
    if (L.motor === "antigo") {
      const ra = receitaAntiga(receita());
      por(corpo, el("p", {}, "Este servidor ainda não tem o perfil profissional; até ele chegar, a tela usa a receita ",
        el("b", {}, "mesma fonte"), ": cada versão troca uma parte destes campos, escritos na fonte do próprio documento."),
      el("p", { class: "dica" }, "Técnicas: ", Object.keys(ra.tecnicas).map(nomeTec).join(", ")),
      el("p", { class: "dica" }, "Campos: ", Object.keys(ra.categorias).map(rotuloCat).join(", ")));
      return;
    }
    const x = L.expandida, tecs = pesos(x?.tecnicas), cats = pesos(x?.categorias);
    if (x?.mistura?.granularidade) {  // o perfil do motor, em linguagem simples
      const ex = x.mistura.extras || {};
      const pNenhum = Array.isArray(ex.prob) ? Number(ex.prob[0]) : 1;
      por(corpo, el("p", {}, "Em cada campo trocado, a técnica é sorteada entre as profissionais:"),
        el("ul", {}, ...Object.entries(x.mistura.granularidade).map(([k, gr]) => el("li", {},
          el("b", {}, `${Math.round(100 * Number(gr.peso || 0))}%`), ` ${GRANULARIDADE[k] || k}`,
          el("span", { class: "dica" }, ` — ${(gr.tecnicas || []).map(nomeTec).join("; ")}`)))),
        el("p", {}, `Campos: sempre ${ou((x.categorias_base || []).map(rotuloCat))}`,
          x.extras && pNenhum < 1 ? `; em ${Math.round(100 * (1 - pNenhum))}% das versões, também um ou dois destes grupos: `
            + Object.keys(ex.grupos || {}).map((k) => GRUPO_EXTRA[k] || k).join(", ") : "", "."),
        el("p", { class: "dica" }, Number(x.mistura.humano) > 0
          ? `Em ${Math.round(100 * Number(x.mistura.humano))}% das versões a edição é feita como uma pessoa faria num `
            + "editor: seleção em volta do texto, a ferramenta de apagar dela, conta-gotas, tamanho e posição no olho, "
            + "retoques e o “Salvar como” do programa; nas outras, com acabamento perfeito."
          : "Todas as versões com acabamento perfeito: só as letras mudam, com cor, grão e desfoque iguais aos do documento."),
        x.canal ? el("p", { class: "dica" }, `Depois da edição, ${Math.round(100 * Number(x.canal.prob ?? 1))}% das versões `
          + "passam por um envio (WhatsApp, e-mail, rede social), igual na original e na falsa.") : null);
      return;
    }
    if (!tecs && !cats) {
      por(corpo, el("p", { class: "dica" }, L.motor === "novo"
        ? "O servidor sorteia, a cada versão, entre as técnicas profissionais (perfil “profissional”)."
        : "A mesma fonte do documento, letras do próprio documento ou só um pedaço trocado: o servidor sorteia a técnica a cada versão."));
      return;
    }
    por(corpo, typeof x.descricao === "string" ? el("p", {}, x.descricao) : null,
      tecs ? [el("b", {}, "Técnicas"), lista(tecs, nomeTec)] : null,
      cats ? [el("b", {}, "Campos"), lista(cats, rotuloCat)] : null);
  }

  // ------------------------------------------------------------------ contagem e botões
  function atualizar() {
    const docs = escolhidos();
    const n = docs.length * U.variacoes;
    const falta = !docs.length ? "Marque pelo menos um grupo de documentos."
      : U.oque === "escolher" && !U.campos.length ? "Marque pelo menos um campo para trocar." : "";
    const b = $("uc-gerar");
    if (!L.iniciando) b.textContent = `▶ Gerar ${plural(n, "fraude", "fraudes")}`;
    b.disabled = Boolean(falta) || Boolean(G.rodando) || L.iniciando;
    $("uc-exemplo-btn").disabled = Boolean(falta) || L.ocupado;
    $("uc-falta").textContent = falta || (G.rodando ? "Há uma geração rodando: acompanhe abaixo." : "");
    const seg = n * SEG_VERSAO + docs.filter((d) => !d.lido).length * SEG_LEITURA;
    $("uc-estimativa").textContent = n ? `${plural(docs.length, "documento", "documentos")} × ${plural(U.variacoes, "versão", "versões")}`
      + ` · leva uns ${tempo(seg)}` : "";
  }
  function desenhar() {
    const foco = document.activeElement?.dataset?.foco;  // o redesenho não tira o foco de quem usa teclado
    desenharSplits(); desenharOque(); desenharEstilos(); atualizar();
    if (foco) document.querySelector(`#um-clique [data-foco="${CSS.escape(foco)}"]`)?.focus();
  }

  // ------------------------------------------------------------------ ANTES | DEPOIS com zoom sincronizado
  // caixa que cobre as mudanças, com folga e proporção legível, dentro da página (null: melhor a página inteira)
  function regiao(bs, W, H) {
    if (!bs.length) return null;
    const x0 = Math.min(...bs.map((b) => b[0])), y0 = Math.min(...bs.map((b) => b[1]));
    const x1 = Math.max(...bs.map((b) => b[2])), y1 = Math.max(...bs.map((b) => b[3]));
    const m = Math.max(0.02 * Math.max(W, H), 1.2 * Math.max(...bs.map((b) => b[3] - b[1])));
    const r = [x0 - 3 * m, y0 - 2 * m, x1 + 3 * m, y1 + 2 * m];
    const larg = r[2] - r[0], alt = r[3] - r[1];
    if (larg / alt > 3) { const d = (larg / 3 - alt) / 2; r[1] -= d; r[3] += d; }  // nem faixa fina demais
    else if (alt / larg > 1.4) { const d = (alt / 1.4 - larg) / 2; r[0] -= d; r[2] += d; }  // nem coluna
    const caber = (a, b, max) => (b - a >= max ? [0, max] : a < 0 ? [0, b - a] : b > max ? [max - (b - a), max] : [a, b]);
    [r[0], r[2]] = caber(r[0], r[2], W);
    [r[1], r[3]] = caber(r[1], r[3], H);
    return (r[2] - r[0]) * (r[3] - r[1]) > 0.6 * W * H ? null : r.map(Math.round);
  }
  function comparador({ antes, depois, W, H, edicoes }) {
    const bs = edicoes.map((e) => caixaDe(e.bbox));
    const geral = regiao(bs, W, H);
    const c = { aoAmpliar: null };
    const lado = (rot, src, foco) => {
      const img = el("img", { src, alt: `${rot.toLowerCase()} da edição`, draggable: "false" });
      const caixas = bs.map((b, k) => el("button", { type: "button", class: "uc-caixa", tabindex: foco ? "0" : "-1",
        "aria-label": `ampliar a mudança ${k + 1}: ${rotuloCat(edicoes[k].categoria)}`, style: { borderColor: corCategoria(edicoes[k].categoria) },
        onclick: () => ampliar(k) }, el("span", { style: { background: corCategoria(edicoes[k].categoria) } }, String(k + 1))));
      const janela = el("div", { class: "uc-janela" }, img, el("div", { class: "uc-caixas" }, ...caixas));
      return { img, caixas, janela, fig: el("figure", { class: "uc-lado" }, el("figcaption", {}, rot), janela) };
    };
    const lados = [lado("ANTES", antes, true), lado("DEPOIS", depois, false)];
    const bTodas = el("button", { type: "button", onclick: () => mostrar(geral, -1) }, "🔍 todas as mudanças");
    const bInteira = el("button", { type: "button", onclick: () => mostrar(null, -1) }, "📄 página inteira");
    const ladosEl = el("div", { class: "uc-lados" }, lados.map((s) => s.fig));
    function mostrar(z, i) {
      const [x0, y0, x1, y1] = z || [0, 0, W, H];
      const bw = x1 - x0, bh = y1 - y0;
      const empilhar = bw / bh > 1.5;  // faixa larga (uma linha de texto): um em cima do outro, bem maiores
      ladosEl.classList.toggle("empilhado", empilhar);
      for (const s of lados) {
        s.fig.style.maxWidth = `calc(${empilhar ? 28 : 64}vh * ${(bw / bh).toFixed(4)})`;  // nunca mais alto que a tela
        s.janela.style.aspectRatio = `${bw} / ${bh}`;
        Object.assign(s.img.style, { width: `${100 * W / bw}%`, left: `${-100 * x0 / bw}%`, top: `${-100 * y0 / bh}%` });
        s.caixas.forEach((cx, k) => {
          const b = bs[k];
          cx.classList.toggle("ativa", k === i);
          Object.assign(cx.style, { left: `${100 * (b[0] - x0) / bw}%`, top: `${100 * (b[1] - y0) / bh}%`,
            width: `${100 * (b[2] - b[0]) / bw}%`, height: `${100 * (b[3] - b[1]) / bh}%` });
        });
      }
      bTodas.disabled = !geral || (z === geral && i < 0);
      bInteira.disabled = !z;
      c.aoAmpliar?.(i);
    }
    function ampliar(i) { mostrar(regiao([bs[i]], W, H), i); }
    c.ampliar = ampliar;
    c.el = el("div", { class: "uc-comparador" }, ladosEl,
      el("div", { class: "uc-zoom" }, bTodas, bInteira, el("span", { class: "dica" }, "clique numa caixa ou numa mudança para ampliar")));
    mostrar(geral, -1);
    return c;
  }
  // "o que mudou: antigo → novo", com o recorte antes e depois de cada mudança (clique: amplia no comparador)
  function listaMudancas(eds, recorteDe, comp) {
    const ol = el("ol", { class: "uc-mudancas" }, ...eds.map((e, i) => {
      const antigo = e.antigo ?? e.texto_antigo ?? "", novo = e.novo ?? e.texto_novo ?? "";
      const rec = recorteDe(i);
      return el("li", {}, el("button", { type: "button", class: "uc-mudanca", "data-i": String(i), title: e.nome_tecnica || "",
        onclick: () => comp.ampliar(i) },
      el("span", { class: "uc-num", style: { background: corCategoria(e.categoria) } }, String(i + 1)),
      el("span", { class: "uc-mud-txt" }, el("b", {}, rotuloCat(e.categoria)), " ",
        antigo ? [el("s", { class: "antigo" }, antigo), " → ", el("span", { class: "novo" }, novo === "" ? "(apagado)" : novo)]
          : el("span", { class: "dica" }, e.descricao || "")),
      rec ? el("span", { class: "uc-recorte" },
        el("span", { class: "uc-rec", "data-rot": "antes" }, el("img", { src: rec[0], alt: `antes: ${antigo}`, loading: "lazy" })),
        el("span", { class: "uc-rec", "data-rot": "depois" }, el("img", { src: rec[1], alt: `depois: ${novo}`, loading: "lazy" }))) : null));
    }));
    comp.aoAmpliar = (i) => ol.querySelectorAll(".uc-mudanca").forEach((b) => b.classList.toggle("ativa", Number(b.dataset.i) === i));
    return el("div", { class: "uc-lista" }, el("h4", {}, `O que mudou (${eds.length})`), ol);
  }

  // "quem editou e como" (rotulador.humano.descrever) e se o texto continua coerente (as regras de um verificador)
  const REGRA = {
    cpf_cnpj: "o CPF/CNPJ não fecha os dígitos verificadores", cep_cidade: "o CEP não é da cidade impressa",
    bairro_cidade: "o bairro não existe nessa cidade", datas_ordenadas: "as datas ficaram fora de ordem",
    mes_referencia: "o mês de referência não combina com o vencimento", vencimento_util: "o vencimento caiu em fim de semana ou feriado",
    linha_digitavel: "a linha digitável não fecha", codigo_total: "o código de barras não bate com o total",
    consumo_valor: "o valor não combina com o consumo", telefone_ddd: "o DDD não é do estado",
    endereco_cep: "a rua mudou e o CEP ficou o antigo",
  };
  function quemEditou(humano, coer, envio) {
    if (!humano && !coer && !envio) return null;
    const novas = coer?.novas_violacoes || [];
    return el("div", { class: "uc-quem" },
      humano ? el("p", {}, el("b", {}, "Quem editou: "), humano) : null,
      envio ? el("p", {}, el("b", {}, "Depois: "), envio) : null,
      coer ? (novas.length
        ? el("p", { class: "uc-aviso", title: "Fica anotado na versão: um verificador que lê o texto pegaria. Fraudadores "
            + "reais também esquecem esses detalhes; o detector de pixel não deve depender disso." },
          el("b", {}, "⚠ Pista no texto: "), novas.map((n) => REGRA[n] || n).join("; "), " (vai anotado).")
        : el("p", { class: "ok" }, el("b", {}, "✔ Texto coerente: "), "nenhuma regra de verificador ficou quebrada "
            + "(CEP e cidade, datas, código de barras, dígitos do CPF).")) : null);
  }

  // ------------------------------------------------------------------ estilos prontos
  function desenharEstilos() {
    const f = $("uc-estilos-f");
    f.hidden = !L.estilos;
    if (!L.estilos) return;
    if (!L.estilos.some((e) => e.id === U.estilo)) U.estilo = (L.estilos.find((e) => e.padrao) || L.estilos[0]).id;
    por($("uc-estilos"), ...L.estilos.map((e) => el("label", { class: "uc-estilo-card", title: e.bom_para ? `Bom para: ${e.bom_para}` : "" },
      el("input", { type: "radio", name: "uc-estilo", value: e.id, checked: e.id === U.estilo, "data-foco": `estilo:${e.id}`,
        onchange: () => { U.estilo = e.id; mudou(); conhecerMotor(); } }),
      el("span", { class: "ic", "aria-hidden": "true" }, e.icone),
      el("span", { class: "nm" }, e.nome, e.padrao ? el("span", { class: "pilula" }, "recomendado") : null),
      el("span", { class: "rs" }, e.resumo))));
    const e = L.estilos.find((x) => x.id === U.estilo);
    por($("uc-estilo-det"), el("p", {}, el("b", {}, `${e.icone} ${e.nome}: o que acontece`)),
      el("ul", {}, ...(e.faz || []).map((t) => el("li", {}, t))),
      e.bom_para ? el("p", { class: "dica" }, el("b", {}, "Bom para: "), e.bom_para) : null,
      e.aviso ? el("p", { class: "uc-aviso" }, "⚠ ", e.aviso) : null);
  }
  async function carregarEstilos() {
    try { L.estilos = await get("/api/gerador/estilos"); } catch { L.estilos = null; }  // servidor antigo: sem estilos
    desenhar();
  }

  // ------------------------------------------------------------------ exemplo (prévia: nada é salvo)
  function cabExemplo(texto, comBotoes = true) {
    return el("div", { class: "uc-painel-cab" },
      el("h3", { id: "uc-exemplo-titulo" }, "Exemplo ", el("span", { class: "dica" }, texto)),
      comBotoes ? el("button", { type: "button", id: "uc-outro", onclick: verExemplo }, "↻ outro exemplo") : null,
      comBotoes ? el("button", { type: "button", "aria-label": "fechar o exemplo", title: "fechar o exemplo",
        onclick: () => { $("uc-exemplo").hidden = true; $("uc-exemplo-btn").focus(); } }, "✕") : null);
  }
  async function verExemplo() {
    const docs = escolhidos();
    if (!docs.length || L.ocupado) return;
    const outros = docs.length > 1 ? docs.filter((d) => d.id !== L.ultimoDoc) : docs;
    const doc = outros[Math.floor(Math.random() * outros.length)];
    L.ultimoDoc = doc.id;
    L.ocupado = true;
    const painel = $("uc-exemplo");
    painel.hidden = false;
    painel.setAttribute("aria-busy", "true");
    por(painel, cabExemplo(`${rotuloSplit(doc.split)} · ${doc.nome}`, false),
      el("p", { class: "uc-carregando", role: "status" }, el("span", { class: "uc-giro", "aria-hidden": "true" }),
        ` Editando ${doc.nome}… ${doc.lido ? "" : "Na primeira vez o documento é lido antes (uns 15 s)."}`));
    $("uc-exemplo-btn").textContent = "⏳ preparando o exemplo…";
    atualizar();
    painel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const r = await enviar("/api/gerador/previa", { receita: receita(), doc: doc.id });
      const lido = (G.docs[doc.split] || []).find((d) => d.id === doc.id);
      if (lido) lido.lido = true;
      desenharExemplo(r);
    } catch (e) {
      por(painel, cabExemplo(`${rotuloSplit(doc.split)} · ${doc.nome}`),
        el("p", { class: "uc-erro", title: e?.message || "" }, `Não deu para fazer o exemplo em ${doc.nome}: ${simples(e)}`));
    } finally {
      L.ocupado = false;
      painel.setAttribute("aria-busy", "false");
      $("uc-exemplo-btn").textContent = "👁 Ver um exemplo";
      atualizar();
      // o exemplo pronto cresce para baixo da dobra: leva a pessoa até ele (antes parecia que nada tinha acontecido)
      painel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }
  function desenharExemplo(r) {
    const painel = $("uc-exemplo");
    const cab = cabExemplo(`${rotuloSplit(r.split)} · ${r.nome} · nada foi salvo`);
    if (!r.edicoes?.length) {
      const porque = [...new Set((r.falhas || []).map((f) => explicar(f.erro || "")))].filter(Boolean);
      por(painel, cab, el("p", { class: "uc-erro" }, "Neste documento não achei o que trocar com essas escolhas"
        + `${porque.length ? ` (${porque.slice(0, 2).join("; ")})` : ""}. Tente outro exemplo.`));
      return;
    }
    const comp = comparador({ antes: b64(r.original), depois: b64(r.editada), W: r.largura, H: r.altura, edicoes: r.edicoes });
    const recorte = (i) => (r.recortes?.[i] ? [b64(r.recortes[i].antes), b64(r.recortes[i].depois)] : null);
    const antigas = r.ocorrencias_antigas || [];
    por(painel, cab, el("div", { class: "uc-exemplo-corpo" }, comp.el, listaMudancas(r.edicoes, recorte, comp)),
      quemEditou(r.humano, r.coerencia),
      antigas.length ? el("p", { class: "dica" }, `Ficou com o dado antigo em ${plural(antigas.length, "lugar", "lugares")} (vai anotado): `
        + antigas.map((o) => `“${o.texto}”`).join(", ") + ".") : null);
  }

  // ------------------------------------------------------------------ geração e galeria ao vivo
  async function gerar() {
    const n = escolhidos().length * U.variacoes;
    if (!n || L.iniciando) return;
    if (n > 300 && !confirm(`Gerar ${n} fraudes? Isso pode levar bastante tempo.`)) return;
    g.pedirPermissaoNotificacao();
    L.iniciando = true;
    $("uc-gerar").textContent = "⏳ começando…";
    atualizar();
    try {
      await enviar("/api/gerador/iniciar", { receita: receita() });
      L.viu = true;
      G.rodando = true;  // até o status confirmar: sem segundo clique
      $("uc-exemplo").hidden = true;
      $("uc-progresso").hidden = false;
      $("uc-prog-titulo").textContent = `Gerando ${plural(n, "fraude", "fraudes")}…`;
      $("uc-prog-texto").textContent = "começando…";
      g.recomecarStatus();
      setTimeout(() => $("uc-progresso").scrollIntoView({ behavior: "smooth", block: "start" }), 300);
    } catch (e) {
      aviso(`Não deu para começar: ${simples(e)}`, "erro");
    } finally {
      L.iniciando = false;
      atualizar();
    }
  }
  async function parar() {
    try { await post("/api/gerador/parar", {}); } catch (e) { aviso(`Não deu para parar: ${simples(e)}`, "erro"); }
    g.recomecarStatus();
  }

  // recorte de uma versão salva em volta de uma troca: a linha dela com contexto (uma troca parcial mexe só num pedaço);
  // a folga é de ~uma linha de texto (uma caixa de várias linhas não vira folga enorme) e o recorte tem pelo menos 30%
  // da largura da página; o servidor limita o que passar da página
  function recorteURL(v, e, tipo, max, folgaV) {
    const [x0, y0, x1, y1] = caixaDe(e.bbox);
    const W = v.largura || 2 * x1, H = v.altura || 2 * y1;
    const m = Math.min(Math.max(12, y1 - y0), 0.012 * Math.max(W, H));
    const mx = Math.max(4 * m, (0.3 * W - (x1 - x0)) / 2);
    const q = [x0 - mx, y0 - folgaV * m, x1 + mx, y1 + folgaV * m].map(Math.round);
    return `/api/catalogo/imagem/${v.split}/${v.nome}/${tipo}.png?max=${max}&x0=${q[0]}&y0=${q[1]}&x1=${q[2]}&y1=${q[3]}`;
  }

  function miniatura(v) {
    const eds = v.edicoes || [];
    const alvo = eds.find((e) => e.categoria === "nome") || eds.find((e) => e.categoria === "endereço") || eds[0];
    const src = (tipo) => (alvo ? recorteURL(v, alvo, tipo, 420, 0.45) : `/api/catalogo/imagem/${v.split}/${v.nome}/${tipo}.png?max=420`);
    const img = (tipo, rot) => el("span", { class: "uc-rec", "data-rot": rot },
      el("img", { src: src(tipo), alt: "", loading: "lazy",
        onerror: (e) => e.target.replaceWith(el("span", { class: "sem-imagem" }, "versão excluída")) }));
    const classes = [...new Set(eds.map((e) => rotuloCat(e.categoria)))];
    return el("button", { type: "button", class: "uc-mini", onclick: () => abrirVersao(v),
      "aria-label": `${v.documento || v.nome}, versão ${versaoN(v)}: mudou ${classes.join(", ")}. Abrir grande.` },
    el("span", { class: "uc-mini-par" }, img("original", "antes"), img("editada", "depois")),
    el("span", { class: "uc-mini-leg" }, el("b", {}, v.documento || v.nome), ` · v${versaoN(v)}`),
    el("span", { class: "uc-mini-classes" }, classes.join(" · ") + (eds.length > 1 ? ` · ${eds.length} trocas` : "")));
  }

  const dlgVer = el("dialog", { id: "uc-ver", class: "uc-ver", "aria-labelledby": "uc-ver-titulo" });
  document.body.append(dlgVer);
  function abrirVersao(v) {
    const base = `/api/catalogo/imagem/${v.split}/${v.nome}`;
    const eds = v.edicoes || [];
    const comp = comparador({ antes: `${base}/original.png?max=1400`, depois: `${base}/editada.png?max=1400`,
      W: v.largura, H: v.altura, edicoes: eds });
    const recorte = (i, tipo) => recorteURL(v, eds[i], tipo, 600, 0.6);
    por(dlgVer, el("form", { method: "dialog" },
      el("h3", { id: "uc-ver-titulo" }, `${v.documento || v.nome} · versão ${versaoN(v)} `,
        el("span", { class: `pilula split-${v.split}` }, rotuloSplit(v.split))),
      el("div", { class: "uc-ver-corpo" }, comp.el, listaMudancas(eds, (i) => [recorte(i, "original"), recorte(i, "editada")], comp)),
      quemEditou(v.humano, null, v.envio),
      el("div", { class: "modal-acoes" },
        el("button", { type: "button", title: "O detalhe completo da versão: máscaras, revisão e exclusão", onclick: () => {
          dlgVer.close();
          g.abrirDetalhe({ ...v, edicoes: eds.map((e) => ({ ...e, texto_antigo: e.antigo, texto_novo: e.novo })) });
        } }, "Aprovar, reprovar ou excluir…"),
        el("button", { value: "fechar", class: "primario" }, "Fechar"))));
    dlgVer.showModal();
  }

  function desenharProgresso(st) {
    $("uc-progresso").hidden = false;
    const { feitas, falhas, total } = st;
    const pct = Math.round(100 * (feitas + falhas) / total);
    $("uc-barra").style.width = `${pct}%`;
    $("uc-barra").parentElement.setAttribute("aria-valuenow", String(pct));
    if (st.rodando) {
      $("uc-prog-titulo").textContent = `Gerando ${plural(total, "fraude", "fraudes")}…`;
      $("uc-prog-texto").textContent = `${feitas} de ${total} prontas (${pct}%)`
        + (falhas ? ` · ${falhas} não ${falhas === 1 ? "deu" : "deram"} certo` : "")
        + ` · ${st.restante_s ? `faltam uns ${tempo(st.restante_s)}` : "calculando o tempo…"}`
        + (st.atual ? ` · agora: ${st.atual.split("/").pop()}` : "");
    } else {
      const docs = L.galeria.size >= feitas ? new Set([...L.galeria.values()].map((v) => String(v.nome).split("__")[0])).size : st.documentos;
      $("uc-prog-titulo").textContent = st.interrompido ? "Geração parada" : feitas ? "Pronto!" : "Nenhuma fraude saiu";
      $("uc-prog-texto").textContent = `${plural(feitas, "fraude", "fraudes")} em ${plural(docs, "documento", "documentos")}`
        + (falhas ? ` · ${falhas} ${falhas === 1 ? "falhou" : "falharam"}` : "") + (st.interrompido ? " (parada antes do fim)" : "") + ".";
    }
    const estado = st.rodando ? "rodando" : feitas ? "fim" : "vazio";
    if (L.acoes !== estado) {  // só refaz os botões quando o estado muda (o foco de quem usa teclado fica)
      L.acoes = estado;
      por($("uc-prog-acoes"), st.rodando ? el("button", { type: "button", class: "perigo", onclick: parar }, "■ Parar") : [
        feitas ? el("button", { type: "button", class: "primario", onclick: () => g.trocarAba("revisar") }, "✔ Revisar") : null,
        feitas ? el("button", { type: "button", onclick: () => g.trocarAba("catalogo") }, "📚 Ver no catálogo") : null,
        el("button", { type: "button", onclick: () => { $("uc-progresso").hidden = true; L.viu = false; } }, "fechar")]);
    }
    const erros = st.erros || [];
    $("uc-erros").hidden = !erros.length;
    $("uc-erros-titulo").textContent = falhas ? `por que ${falhas === 1 ? "1 falhou" : `${falhas} falharam`}?` : "avisos";
    por($("uc-erros-lista"), ...erros.slice(-20).map((m) => el("li", { title: m }, erroDoLote(m))));
  }

  // chamado por gerador.js a cada status novo (o laço de acompanhamento é o de lá)
  function aoStatus(st) {
    if (st?.total) {
      if (st.id !== L.lote) { L.lote = st.id; L.galeria = new Map(); L.acoes = ""; por($("uc-galeria")); }
      for (const v of [...(st.recentes || [])].reverse()) {  // recentes vêm do mais novo ao mais velho
        if (L.galeria.has(v.nome)) continue;
        L.galeria.set(v.nome, v);
        $("uc-galeria").prepend(miniatura(v));
      }
      if (st.rodando) L.viu = true;
      if (L.viu) desenharProgresso(st);
    }
    atualizar();
  }

  // ------------------------------------------------------------------ início
  $("uc-menos").onclick = () => { U.variacoes = limitar(U.variacoes - 1); mudou(); };
  $("uc-mais").onclick = () => { U.variacoes = limitar(U.variacoes + 1); mudou(); };
  $("uc-variacoes").onchange = (e) => { U.variacoes = limitar(e.target.value); mudou(); };
  for (const r of document.querySelectorAll('input[name="uc-oque"]')) r.onchange = () => { U.oque = r.value; mudou(); conhecerMotor(); };
  $("uc-escolher-docs").onclick = abrirDocs;
  $("uc-gerar").onclick = gerar;
  $("uc-exemplo-btn").onclick = verExemplo;
  $("uc-detalhes").addEventListener("toggle", conhecerMotor);
  desenhar();
  desenharDetalhes();
  carregarEstilos();
  return { aoStatus, redesenhar: desenhar };
}
