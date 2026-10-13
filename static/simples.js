// Modo simples: para quem não conhece editor de imagem.
// O documento é lido sozinho (OCR), cada campo aparece destacado; a pessoa clica, escreve o
// valor falso (ou 🎲) e troca. Fonte, cor, posição, grão do papel e categoria são automáticos.
import { bitmapUrl, get, post } from "./api.js";

const PASSO = (n, texto) => `<span class="passo-n">${n}</span>${texto}`;

export function instalarSimples(app, u) {
  const { el, $ } = u;
  const S = { campos: [], doc: null, lendo: false, sel: null, marcando: false, arr: null, novos: {}, erro: null,
    tecnicas: [], tecCampo: {}, sementeTec: {}, marcados: new Set(), editando: false };
  // classes marcadas sozinhas ao abrir um documento (o foco: nome e endereço); a pessoa muda e fica lembrado
  const classesAuto = () => u.lembrar("classesAuto", ["nome", "endereço"]);
  app.simples = S;

  // ------------------------------------------------------------------ apoio
  const trocas = () => {  // campo -> última edição aplicada nele (vem do histórico: desfazer atualiza sozinho)
    const m = {};
    for (const h of app.sessao?.historico || []) {
      const id = h.tipo === "tecnica" ? h.params?.campo?.id : ["trocar_campo", "trocar_campo_ia"].includes(h.tipo) ? h.params?.campo : null;
      if (id) m[id] = h;
    }
    return m;
  };
  // texto e caixa ATUAIS de um campo (depois da última edição nele)
  const textoDa = (h) => (h.tipo === "tecnica" ? h.meta?.texto_novo : h.params?.texto);
  function estadoCampo(c) {
    const prev = trocas()[c.id];
    if (!prev) return { texto: c.texto, caixa: c.caixa, recalcular: false, prev: null };
    return { texto: textoDa(prev) ?? c.texto, caixa: prev.bbox, recalcular: true, prev };
  }
  const rotuloCat = (chave) => app.estado.categorias.find((c) => c.chave === chave)?.rotulo || "sem tipo";
  const campoPorId = (id) => S.campos.find((c) => c.id === id);

  function parametros(c, novo) {
    const { prev, texto, caixa } = estadoCampo(c);
    // fontes: "top10" = a fonte é identificada só entre as 10 mais usadas em documentos (como a edição automática)
    const p = { caixa, texto: novo, texto_antigo: texto, tipo: c.tipo, campo: c.id, fontes: "top10",
      semente: S.semente ||= Math.floor(Math.random() * 1e6) };
    if (prev?.tipo === "trocar_campo" && prev.meta?.ajuste) p.fonte_params = prev.meta.ajuste;  // mesma fonte da 1ª troca
    if (c.alinhar) p.alinhar = c.alinhar;
    return p;
  }

  // ------------------------------------------------------------------ leitura do documento
  async function ler(doc) {
    S.doc = doc; S.campos = []; S.sel = null; S.novos = {}; S.erro = null; S.lendo = true;
    app.campos = [];
    desenhar();
    try {
      const r = await get(`/api/campos/${doc}`);
      if (S.doc !== doc) return;
      S.campos = r.campos;
      S.marcados = new Set(S.campos.filter((c) => classesAuto().includes(c.categoria)).map((c) => c.id));
      app.campos = S.campos;  // o modo avançado sugere a categoria pelo campo sob o clique
    } catch (e) {
      if (S.doc === doc) S.erro = e.message;
    } finally {
      if (S.doc === doc) { S.lendo = false; desenhar(); app.visor.redesenhar(); }
    }
  }

  // ------------------------------------------------------------------ ações
  function selecionar(id, rolar = true) {
    S.sel = id;
    app.limparPrevia();
    desenhar();
    app.visor.redesenhar();
    const c = campoPorId(id);
    if (!c) return;
    if (rolar) document.querySelector(`#lista-campos [data-campo="${id}"]`)?.scrollIntoView({ block: "nearest" });
    setTimeout(() => document.querySelector(`#lista-campos [data-campo="${id}"] input.novo`)?.focus(), 50);
  }

  async function sortear(c) {
    if (c.tipo !== "texto") return "";
    const r = await post("/api/auto/sugerir", { id: app.sessao.id, categoria: c.categoria, texto: estadoCampo(c).texto, tipo: c.tipo });
    return r.texto;
  }

  // ------------------------------------------------------------------ técnicas do catálogo ("como editar")
  const TEC_COM_TEXTO = new Set(["campo.reescrever", "campo.amador", "campo.acrescentar", "trecho.trocar", "ia.reescrever",
    "codigo.qr"]);
  const tecPorId = (id) => S.tecnicas.find((t) => t.id === id);
  // técnica de caractere em campo sem dígito trocaria letra = erro de grafia (o servidor recusa): não oferece
  const temDigito = (c) => /\d/.test(estadoCampo(c).texto || c.texto || "");
  const tecnicasDo = (c) => S.tecnicas.filter((t) => t.tipos.includes(c.tipo) && (!t.categorias || t.categorias.includes(c.categoria))
    && (!t.precisa_ia || S.iaOnline) && (t.granularidade !== "caractere" || c.tipo !== "texto" || temDigito(c)));
  function paramsTecnica(c, tid) {
    const st = estadoCampo(c);
    const novo = (S.novos[c.id] ?? "").trim();
    const p = { tecnica: tid, campo: { id: c.id, caixa: st.caixa, texto: st.texto, categoria: c.categoria, tipo: c.tipo },
      recalcular_caixa: st.recalcular, semente: (S.sementeTec[c.id] ??= Math.floor(Math.random() * 1e6)) };
    if (novo && TEC_COM_TEXTO.has(tid)) p.texto = novo;
    if (tecPorId(tid)?.precisa_ia) p.modelo = iaModelo();
    return p;
  }
  const preverTec = u.debounce((c) => {
    const tid = S.tecCampo[c.id];
    if (!tid || tecPorId(tid)?.precisa_ia) { app.limparPrevia(); return; }
    app.previa("tecnica", paramsTecnica(c, tid));
  }, 250);
  function categoriaDoCampo(c) {
    u.definirCategoria(c.categoria);
    app._catPendente = false;
    if (c.categoria === "outro" && !app.descricao.trim()) {  // "Outro" pede descrição; no modo simples, o texto do campo
      app.descricao = `campo marcado à mão: "${(estadoCampo(c).texto || c.texto || "").slice(0, 60)}"`;
      $("descricao").value = app.descricao;
    }
  }

  async function aplicarTecnica(c) {
    const t = tecPorId(S.tecCampo[c.id]);
    if (!t) return;
    if (!c.categoria) { app.aviso("escolha o TIPO deste campo antes de editar", "erro"); return; }
    categoriaDoCampo(c);
    const ok = await app.aplicar("tecnica", paramsTecnica(c, t.id),
      { msg: t.precisa_ia ? "🤖 a IA está editando… (1 a 4 minutos)" : `${t.nome}…` });
    if (ok) {
      const h = trocas()[c.id];
      delete S.novos[c.id];
      S.sementeTec[c.id] = null;
      app.status(`${t.nome}: ${h?.meta?.descricao || rotuloCat(c.categoria)}`);
      desenhar();
    }
  }

  const prever = u.debounce((c) => {
    const novo = S.novos[c.id] ?? "";
    if (c.tipo === "texto" && !novo.trim()) { app.limparPrevia(); return; }
    app.previa("trocar_campo", parametros(c, novo));
  }, 300);

  async function trocar(c) {
    const novo = (S.novos[c.id] ?? "").trim();
    if (c.tipo === "texto" && !novo) { app.aviso("escreva o valor novo (ou clique em 🎲)", "erro"); return; }
    if (!c.categoria) { app.aviso("escolha o TIPO deste campo antes de trocar", "erro"); return; }
    categoriaDoCampo(c);
    const ok = await app.aplicar("trocar_campo", parametros(c, novo), { msg: "trocando…" });
    if (ok) {
      delete S.novos[c.id];
      S.semente = null;
      // próximo campo pendente ABAIXO deste (depois volta ao começo)
      const pend = (x) => x.categoria && !trocas()[x.id] && x.id !== c.id;
      const i = S.campos.indexOf(c);
      const prox = S.campos.slice(i + 1).find(pend) || S.campos.find(pend);
      app.status(`${rotuloCat(c.categoria)} trocado`);
      selecionar(prox ? prox.id : c.id, true);
    }
  }

  // ------------------------------------------------------------------ IA
  const iaModelo = () => $("ia-modelo").value || null;
  async function verificarIA() {
    try {
      const st = await get("/api/ia/status");
      $("ia-controles").hidden = !st.online;
      const mudou = S.iaOnline !== !!st.online;
      S.iaOnline = !!st.online;
      if (st.online && st.modelos) {
        const sel = $("ia-modelo"), atual = sel.value || u.lembrar("iaModelo", st.modelo_id);
        sel.replaceChildren(...st.modelos.map((m) => el("option", { value: m.id, title: m.licenca },
          m.id === "2511" ? "Qwen-Image-Edit-2511 (rápido)" : "Qwen-Image-2.1 Uncensored (só pesquisa)")));
        sel.value = st.modelos.some((m) => m.id === atual) ? atual : st.modelo_id;
      }
      if (mudou) desenhar();
    } catch {
      $("ia-controles").hidden = true;
      if (S.iaOnline) { S.iaOnline = false; desenhar(); }
    }
  }

  async function trocarComIA(c) {
    const novo = (S.novos[c.id] ?? "").trim();
    if (!novo) { app.aviso("escreva o valor novo (ou clique em 🎲) antes de pedir à IA", "erro"); return; }
    if (!c.categoria) { app.aviso("escolha o TIPO deste campo antes de trocar", "erro"); return; }
    const { texto: antigo, caixa } = estadoCampo(c);
    if (!antigo) { app.aviso("a IA precisa saber o texto antigo; marque o campo de novo", "erro"); return; }
    categoriaDoCampo(c);
    const p = { caixa, texto: novo, texto_antigo: antigo, campo: c.id, modelo: iaModelo(),
      semente: Math.floor(Math.random() * 1e6) };
    const ok = await app.aplicar("trocar_campo_ia", p,
      { msg: "🤖 a IA está escrevendo o texto novo… (1 a 3 minutos; a 1ª vez demora mais)" });
    if (ok) { delete S.novos[c.id]; app.status(`${rotuloCat(c.categoria)} trocado pela IA`); desenhar(); }
  }

  // ------------------------------------------------------------------ edição automática EXATA: só os campos marcados
  const marcadosValidos = () => S.campos.filter((c) => S.marcados.has(c.id) && c.categoria && c.categoria !== "outro");
  // só os ainda não trocados: trocar de novo por cima empilhava duas edições no mesmo campo, com o texto antigo
  // errado no rótulo (para refazer um campo: Desfazer)
  const marcadosPendentes = () => { const feitos = trocas(); return marcadosValidos().filter((c) => !feitos[c.id]); };
  function marcarClasses(lista) {
    u.armazenar("classesAuto", lista);
    S.marcados = new Set(S.campos.filter((c) => lista.includes(c.categoria)).map((c) => c.id));
    desenhar();
  }
  function desenharMarcarRapido() {
    const caixa = $("marcar-rapido");
    if (!caixa) return;
    const n = marcadosPendentes().length;
    const todos = () => { S.marcados = new Set(S.campos.filter((c) => c.categoria && c.categoria !== "outro").map((c) => c.id)); desenhar(); };
    caixa.replaceChildren(el("span", { class: "dica" }, "Marcar:"),
      el("button", { type: "button", onclick: () => marcarClasses(["nome", "endereço"]) }, "nome e endereço"),
      el("button", { type: "button", onclick: () => marcarClasses(["endereço", "bairro", "cep", "cidade/uf"]) }, "endereço completo"),
      el("button", { type: "button", onclick: todos }, "todos"),
      el("button", { type: "button", onclick: () => { S.marcados = new Set(); desenhar(); } }, "nenhum"));
    $("btn-fraude").textContent = `✨ Editar os marcados (${n})`;
    $("btn-fraude").disabled = !n || S.editando;
  }

  async function fraudeAutomatica() {
    if (!app.sessao || app.ocupado || S.editando) return;
    const doc = app.sessao.id;
    const alvo = marcadosPendentes();
    if (!alvo.length) {
      app.aviso(marcadosValidos().length ? "os campos marcados já foram trocados: clique em Desfazer para trocar de novo"
        : "marque na lista os campos que quer editar (a caixinha à esquerda de cada um)", "erro");
      return;
    }
    S.editando = true;  // botão ocupado até a resposta: um segundo clique editaria por cima do primeiro
    desenharMarcarRapido();
    try {
      await app.enfileirar(async () => {
        const comIA = S.iaOnline && $("fraude-ia").checked;
        u.ocupar(comIA ? `🤖 a IA está editando ${alvo.length} campo(s) (~2 min cada)…`
          : `editando ${alvo.length} campo${alvo.length > 1 ? "s" : ""} na mesma fonte…`);
        try {
          const campos = alvo.map((c) => {
            const st = estadoCampo(c);
            return { id: c.id, caixa: st.caixa, texto: st.texto, categoria: c.categoria, tipo: c.tipo };
          });
          const r = await post("/api/auto/fraude", { id: doc, campos, usar_ia: comIA, modelo: iaModelo() });
          if (app.sessao?.id !== doc) return;
          await app.receber(r);
          const falhas = r.falhas?.length ? ` Não deu em ${r.falhas.length}: ${r.falhas.join("; ")}.` : "";
          app.aviso(`Feito: ${r.trocas.map((t) => `${rotuloCat(t.categoria)} (${t.antigo} → ${t.novo || "apagado"})`)
            .join("; ")}.${falhas} Não gostou de algum? Clique em Desfazer.`, "ok");
        } catch (e) { app.aviso(e.message, "erro"); } finally { u.ocupar(null); }
      });
    } finally { S.editando = false; desenhar(); }
  }

  // ------------------------------------------------------------------ pistas (perito): o desafio de passar despercebido
  function resumoPistas(medidas) {
    const pior = (medidas || []).reduce((a, m) => (!a || (m.nota || 0) > (a.nota || 0) ? m : a), null);
    if (!pior) return "";
    const m = pior.medidas || pior;
    const partes = [];
    if (m.grao != null) partes.push(`grão ×${Number(m.grao).toFixed(2)}`);
    if (m.nitidez != null) partes.push(`nitidez ×${Number(m.nitidez).toFixed(2)}`);
    if (m.tom != null) partes.push(`tom do papel ${Number(m.tom).toFixed(1)} níveis`);
    if (m.espectro != null) partes.push(`espectro ${Number(m.espectro).toFixed(2)}`);
    return partes.length ? ` Pior edição: ${partes.join(", ")} (dentro/fora da região).` : "";
  }
  async function mostrarPistas() {
    if (!app.sessao || app.ocupado) return;
    if (S.pistas) {  // segundo clique: esconde
      S.pistas = false; app.visor.mostrarMascara = false; app.visor.redesenhar();
      $("btn-pistas").classList.remove("ativo");
      return;
    }
    u.ocupar("o perito está procurando pistas…");
    try {
      const r = await post("/api/perito/sessao", { doc: app.sessao.id });
      app.visor.definirMascara(await bitmapUrl(`data:image/png;base64,${r.mapa}`));
      app.visor.mostrarMascara = true; S.pistas = true;
      $("btn-pistas").classList.add("ativo");
      if (r.nota == null) {  // sem medida não é "invisível": a região é pequena ou não há papel em volta para comparar
        app.aviso("O perito não conseguiu medir esta edição (região pequena demais ou sem papel em volta para comparar). "
          + "As manchas no documento mostram onde ela destoa da página.", "ok");
      } else {
        const nota = Math.round(100 * r.nota);
        app.aviso(`Pistas forenses: nota ${nota}/100 (0 = invisível para o perito, 100 = óbvia).${resumoPistas(r.medidas)} `
          + "As manchas no documento mostram onde a edição destoa do papel em volta.", nota > 50 ? "erro" : "ok");
      }
    } catch (e) { app.aviso(e.message, "erro"); } finally { u.ocupar(null); }
  }

  async function marcarRetangulo(a, b) {
    const caixa = [a.x, a.y, b.x, b.y].map(Math.round);
    try {
      const r = await post("/api/campos/ler", { id: app.sessao.id, caixa });
      const id = `m${Date.now() % 1e7}`;
      S.campos.push({ id, categoria: r.categoria || null, texto: r.texto || "", caixa: r.caixa, tipo: "texto", manual: true });
      S.campos.sort((x, y) => x.caixa[1] - y.caixa[1] || x.caixa[0] - y.caixa[0]);
      selecionar(id);
      if (!r.categoria) app.aviso("Escolha o TIPO deste campo na lista (nome, valor, data…).");
    } catch (e) { app.aviso(e.message, "erro"); }
  }

  // ------------------------------------------------------------------ ferramenta "campos" (mouse no documento)
  const noCampo = (p) => {
    const dentro = S.campos.filter((c) => p.x >= c.caixa[0] - 6 && p.x <= c.caixa[2] + 6 && p.y >= c.caixa[1] - 6 && p.y <= c.caixa[3] + 6);
    dentro.sort((a, b) => (a.caixa[2] - a.caixa[0]) * (a.caixa[3] - a.caixa[1]) - (b.caixa[2] - b.caixa[0]) * (b.caixa[3] - b.caixa[1]));
    return dentro[0];
  };
  app.F.campos = {
    nome: "Campos do documento: clique num campo destacado para trocar", icone: "▤", atalho: "", cursor: "default",
    down(p, ev) {
      if (S.marcando) { S.arr = { a: p, b: p }; return; }
      const c = noCampo(p);
      if (c) selecionar(c.id);
      else S.pan = app.visor.telaDoEvento(ev);
    },
    move(p, ev) {
      if (S.arr) { S.arr.b = p; app.visor.redesenhar(); return; }
      if (S.pan) { const s = app.visor.telaDoEvento(ev); app.visor.mover(s.x - S.pan.x, s.y - S.pan.y); S.pan = s; }
    },
    up() {
      if (S.arr) {
        const { a, b } = S.arr; S.arr = null; S.marcando = false; desenhar();
        if (Math.abs(a.x - b.x) > 4 && Math.abs(a.y - b.y) > 4) marcarRetangulo({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y) }, { x: Math.max(a.x, b.x), y: Math.max(a.y, b.y) });
        app.visor.redesenhar();
      }
      S.pan = null;
    },
    cursorEm(p) { return S.marcando ? "crosshair" : (noCampo(p) ? "pointer" : "grab"); },
    desenhar(ctx, v) {
      const feitos = trocas();
      for (const c of S.campos) {
        const a = v.imgParaTela(c.caixa[0] - 4, c.caixa[1] - 4), b = v.imgParaTela(c.caixa[2] + 4, c.caixa[3] + 4);
        const cor = u.corCategoria(c.categoria);
        ctx.save();
        ctx.lineWidth = c.id === S.sel ? 3 : 1.5;
        ctx.strokeStyle = cor;
        ctx.setLineDash(c.categoria ? [] : [5, 4]);
        if (c.id === S.sel) { ctx.fillStyle = cor + "33"; ctx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); }
        ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
        if (c.id === S.sel) {  // nome da classe escrito (não só a cor, que daltônico não distingue)
          const nome = rotuloCat(c.categoria);
          ctx.font = "bold 12px sans-serif";
          const w = ctx.measureText(nome).width + 8;
          ctx.fillStyle = cor; ctx.fillRect(a.x, a.y - 17, w, 16);
          ctx.fillStyle = "#000"; ctx.fillText(nome, a.x + 4, a.y - 5);
        }
        if (feitos[c.id]) {  // selo de "trocado"
          const sx = b.x + 13, sy = (a.y + b.y) / 2;  // ao lado da caixa, sem cobrir o texto
          ctx.fillStyle = "#3fb950"; ctx.beginPath(); ctx.arc(sx, sy, 9, 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = "#000"; ctx.font = "bold 12px sans-serif"; ctx.fillText("✓", sx - 4, sy + 4);
        }
        ctx.restore();
      }
      if (S.arr) {
        const a = v.imgParaTela(S.arr.a.x, S.arr.a.y), b = v.imgParaTela(S.arr.b.x, S.arr.b.y);
        ctx.save(); ctx.strokeStyle = "#fff"; ctx.setLineDash([5, 4]); ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); ctx.restore();
      }
    },
  };

  // ------------------------------------------------------------------ painel
  function linha(c, feitos) {
    const feito = feitos[c.id];
    const ativo = c.id === S.sel;
    const cat = el("select", { class: "cat-campo", title: "Tipo do campo (vai para a máscara)",
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { c.categoria = e.target.value || null; if (classesAuto().includes(c.categoria)) S.marcados.add(c.id); desenhar(); app.visor.redesenhar(); } },
      el("option", { value: "" }, "— tipo? —"), ...app.estado.categorias.map((k) => el("option", { value: k.chave }, k.rotulo)));
    cat.value = c.categoria || "";
    const marcar = el("input", { type: "checkbox", class: "marcar-auto", checked: S.marcados.has(c.id),
      title: "Marcado: o ✨ Editar os marcados troca este campo", "aria-label": "editar este campo automaticamente",
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { if (e.target.checked) S.marcados.add(c.id); else S.marcados.delete(c.id); desenharMarcarRapido(); } });
    const cab = el("div", { class: "campo-cab" }, marcar,
      el("span", { class: "chip", style: `background:${u.corCategoria(c.categoria)}` }), cat,
      el("span", { class: "campo-antigo", title: c.texto }, feito ? (textoDa(feito) ?? "") || "(apagado/código gerado)" : c.texto),
      feito ? el("span", { class: "selo", title: `antes: ${c.texto}\n${feito.meta?.descricao || ""}` },
        feito.tipo === "tecnica" ? `✓ ${feito.rotulo}` : "✓ trocado") : null);
    const li = el("li", { class: `campo ${ativo ? "ativo" : ""} ${feito ? "feito" : ""}`, "data-campo": c.id,
      onclick: () => { if (S.sel !== c.id) selecionar(c.id, false); } }, cab);
    if (ativo) {
      const codigo = c.tipo !== "texto";
      const inp = el("input", { type: "text", class: "novo", "data-dado": "1",
        placeholder: codigo ? "deixe vazio para gerar um código novo" : "escreva o valor falso…",
        value: S.novos[c.id] ?? "",
        oninput: (e) => { S.novos[c.id] = e.target.value; prever(c); },
        onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); trocar(c); } } });
      const corpo = el("div", { class: "campo-corpo" },
        el("div", { class: "linha-novo" }, inp,
          el("button", { type: "button", class: "grande", title: "Sortear um valor falso no mesmo formato",
            onclick: async () => { const t = await sortear(c); S.novos[c.id] = t; inp.value = t; prever(c); } }, "🎲")),
        el("div", { class: "linha-acoes" },
          el("button", { type: "button", class: "primario grande", onclick: () => trocar(c) }, feito ? "✔ Trocar de novo" : "✔ Trocar"),
          S.iaOnline && c.tipo === "texto" ? el("button", { type: "button", class: "grande ia",
            title: "A IA escreve o valor novo nesta região, imitando o documento (mais lento)",
            onclick: () => trocarComIA(c) }, "🤖 Trocar com IA") : null,
          c.tipo === "texto" ? el("label", { class: "pequeno", title: "Para valores alinhados pela direita (ex.: totais)" },
            el("input", { type: "checkbox", checked: c.alinhar === "direita",
              onchange: (e) => { c.alinhar = e.target.checked ? "direita" : null; prever(c); } }), " alinhar pela direita") : null),
        linhaTecnicas(c));
      li.append(corpo);
      if (codigo && !S.novos[c.id]) setTimeout(() => prever(c), 0);
    }
    return li;
  }

  function linhaTecnicas(c) {  // "como editar": qualquer técnica do catálogo que serve para este campo
    const lista = tecnicasDo(c);
    if (!lista.length) return null;
    const tid = S.tecCampo[c.id] || "";
    const sel = el("select", { class: "tec-campo", title: "Escolha uma técnica: a prévia aparece no documento",
      onchange: (e) => { S.tecCampo[c.id] = e.target.value || null; S.sementeTec[c.id] = null; desenhar(); preverTec(c); } },
    el("option", { value: "" }, "outras técnicas…"),
    ...Object.entries(lista.reduce((g, t) => ((g[t.familia] ||= []).push(t), g), {})).map(([fam, ts]) =>
      el("optgroup", { label: S.familias?.[fam]?.rotulo || fam }, ...ts.map((t) => el("option", { value: t.id, title: t.descricao },
        `${t.nome} (${t.nivel === "simples" ? "amador" : t.nivel})`)))));
    sel.value = tid;
    const t = tecPorId(tid);
    return el("div", { class: "linha-tecnica" },
      el("label", { class: "pequeno" }, "Como editar: "), sel,
      t ? el("button", { type: "button", class: t.precisa_ia ? "ia" : "", title: t.descricao, onclick: () => aplicarTecnica(c) }, "▶ Aplicar") : null,
      t && !t.precisa_ia ? el("button", { type: "button", title: "Outra variação (outra letra, outro valor, outro modo)",
        onclick: () => { S.sementeTec[c.id] = null; preverTec(c); } }, "🎲") : null,
      t ? el("div", { class: "dica" }, t.descricao + (TEC_COM_TEXTO.has(t.id) ? " Usa o valor escrito acima, se houver." : "")) : null);
  }

  function desenhar() {
    const ul = $("lista-campos");
    if (!ul) return;
    desenharMarcarRapido();
    const feitos = trocas();
    const n = Object.keys(feitos).length;
    $("campos-resumo").textContent = S.lendo ? "lendo o documento…" : S.erro ? "" : `${S.campos.length} campos · ${n} trocado${n === 1 ? "" : "s"}`;
    if (S.lendo) { ul.replaceChildren(el("li", { class: "aviso-lista" }, el("span", { class: "giro pequeno" }), " Lendo o documento… (só na 1ª vez, alguns segundos)")); return; }
    if (S.erro) { ul.replaceChildren(el("li", { class: "aviso-lista erro" }, `Não consegui ler o documento: ${S.erro}`)); return; }
    if (!S.campos.length) { ul.replaceChildren(el("li", { class: "aviso-lista" }, "Nenhum campo encontrado. Use \"Marcar outro texto\".")); return; }
    ul.replaceChildren(...S.campos.map((c) => linha(c, feitos)));
  }

  // ------------------------------------------------------------------ modo simples x avançado
  function aplicarModo(simples) {
    app.modoSimples = simples;
    document.body.classList.toggle("simples", simples);
    $("btn-modo").textContent = simples ? "🛠 Modo avançado" : "🙂 Modo simples";
    $("btn-salvar").textContent = simples ? "✔ Salvar e próximo" : "Salvar (Ctrl+S)";
    $("btn-descartar").textContent = simples ? "Recomeçar" : "Descartar";
    u.armazenar("modoSimples", simples);
    if (simples) app.usar("campos");
    else if (app.idFerramenta === "campos") app.usar("ret");
    requestAnimationFrame(() => app.visor.ajustar());
  }

  function tutorial() {
    u.abrirModal({
      titulo: "Bem-vindo! Como gerar uma fraude em 4 passos",
      flutuante: false,
      corpo: el("div", { class: "tutorial", innerHTML: [
        PASSO(1, "Escolha um documento na lista à direita (ou use ◀ ▶)."),
        PASSO(2, "Clique num <b>campo destacado</b> no documento: valor, data, nome, CPF…"),
        PASSO(3, "Escreva o <b>valor falso</b> (ou clique em 🎲 para sortear) e clique em <b>✔ Trocar</b>. Fonte, cor e posição são automáticas."),
        PASSO(4, "Clique em <b>✔ Salvar e próximo</b>. Errou? <b>↶ Desfazer</b> volta um passo."),
        "<p class='dica'>Atalho: marque os campos na lista (nome e endereço já vêm marcados) e clique em <b>✨ Editar os marcados</b>: todos são trocados de uma vez, na mesma fonte. O editor completo (camadas, carimbo, IA…) fica em <b>🛠 Modo avançado</b>.</p>",
      ].map((t) => `<p>${t}</p>`).join("") }),
      botoes: [{ rotulo: "Entendi, começar", primario: true, acao: () => true }],
      aoFechar: () => u.armazenar("tutorialVisto", true),
    });
  }

  // ------------------------------------------------------------------ ligações
  $("btn-modo").addEventListener("click", () => aplicarModo(!app.modoSimples));
  $("btn-lote").addEventListener("click", () => window.open("/gerador", "gerador"));
  $("btn-fraude").addEventListener("click", () => fraudeAutomatica());
  $("btn-marcar").addEventListener("click", () => {
    if (!app.sessao) return;
    if (app.idFerramenta !== "campos") app.usar("campos");
    S.marcando = true; desenhar();
    app.aviso("Arraste um retângulo em volta do texto que quer trocar.");
  });
  $("btn-desfazer-grande").addEventListener("click", () => app.desfazer());
  $("btn-pistas").addEventListener("click", mostrarPistas);
  $("btn-recomecar").addEventListener("click", () => app.descartar?.());
  // guia de 4 passos: recolhe depois de aprender (fica recolhido entre visitas)
  const guia = $("guia");
  if (u.lembrar("guiaRecolhido", false)) guia.removeAttribute("open");
  guia.addEventListener("toggle", () => u.armazenar("guiaRecolhido", !guia.open));
  // Alt+↓ / Alt+↑: próximo / anterior campo (sem tirar a mão do teclado)
  app.campoSeguinte = (passo) => {
    const lista = S.campos.filter((c) => c.categoria);
    if (!lista.length) return;
    const i = lista.findIndex((c) => c.id === S.sel);
    const alvo = lista[(i + passo + lista.length) % lista.length];
    if (alvo) selecionar(alvo.id, true);
  };
  $("btn-ajuda").addEventListener("click", tutorial);
  $("ia-modelo").addEventListener("change", (e) => u.armazenar("iaModelo", e.target.value));
  verificarIA();
  setInterval(verificarIA, 30000);  // o servidor de IA pode subir depois
  app.aoAbrirDoc = (s) => { ler(s.id); S.pistas = false; $("btn-pistas").classList.remove("ativo"); };
  app.aoAtualizarSessao = () => {  // a edição mudou: o mapa de pistas ficou velho
    if (S.pistas) { S.pistas = false; app.visor.mostrarMascara = false; $("btn-pistas").classList.remove("ativo"); }
    desenhar();
  };
  app.fraudeAutomatica = fraudeAutomatica;
  const statusGerador = () => get("/api/gerador/status").then(u.indicarLote).catch(() => {});
  statusGerador();
  setInterval(statusGerador, 15000);
  get("/api/catalogo").then((r) => { S.tecnicas = r.tecnicas.filter((t) => t.automatica); S.familias = r.familias; desenhar(); })
    .catch(() => {});

  aplicarModo(u.lembrar("modoSimples", true));
  if (!u.lembrar("tutorialVisto", false)) setTimeout(tutorial, 400);
}
