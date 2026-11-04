// Duas barras que acompanham o que a pessoa está fazendo:
//  - a barra de tarefas sob a seleção (como a "barra de tarefas contextual" do Photoshop): recortar, copiar, colar,
//    puxar, apagar com preenchimento, substituir o texto… e, com um trecho solto, aplicar ou cancelar;
//  - as dicas da barra de status (como no Graphite): o que cada gesto do mouse e cada tecla fazem AGORA.
// As duas são recalculadas a cada quadro do visor, mas só se refazem quando o estado muda.
import { DICAS, NOMES } from "./ferramentas.js";
import { icone, mouseEsquerdo } from "./icones.js";

function el(tag, props = {}, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const f of filhos.flat()) if (f !== null && f !== undefined && f !== false) e.append(f);
  return e;
}

export function instalarBarras(app) {
  const visor = document.getElementById("visor");
  const barra = document.getElementById("barra-contexto");
  const dicas = document.getElementById("dicas");
  let chaveBarra = null, chaveDicas = null;

  function botao(ic, rotulo, titulo, acao, classe = "") {
    return el("button", { type: "button", class: classe, title: titulo, "aria-label": rotulo || titulo,
      onpointerdown: (e) => e.stopPropagation(),
      onclick: (e) => { e.stopPropagation(); acao(); visor.focus({ preventScroll: true }); } },
    icone(ic, 16), rotulo ? el("span", {}, rotulo) : null);
  }
  const sep = () => el("span", { class: "bc-sep" });

  // ------------------------------------------------------------------ barra de tarefas
  function estado() {
    if (!app.sessao || app.ocupado || app.apertado || app.somenteLeitura) return null;
    const id = app.idFerramenta, solto = app.ferramenta?.solto?.();
    if (solto && (solto.sujo || solto.recorte || solto.modo === "colar")) {
      if (solto.arrastando) return null;
      return { tipo: "solto", chave: `solto:${solto.modo}:${solto.recorte}:${solto.sujo}` , solto };
    }
    if (!app.temSelecao() || ["texto", "substituir", "remendo"].includes(id)) return null;
    return { tipo: "sel", chave: `sel:${id}:${app.clip ? 1 : 0}:${app.podeVariar() ? 1 : 0}` };
  }

  function montarSelecao() {
    const itens = [
      botao("recortar", "Recortar", "Recortar (Ctrl+X): o trecho fica solto para você arrastar; o lugar de onde saiu é preenchido com o papel em volta", app.recortar),
      botao("copiar", "Copiar", "Copiar (Ctrl+C): cole com Ctrl+V nesta imagem ou em outra do MESMO split", app.copiar),
      app.clip ? botao("colar", "Colar", `Colar (Ctrl+V) o trecho copiado de ${app.clip.nome}`, app.colar) : null,
      app.idFerramenta === "transformar" ? null
        : botao("mover", "Puxar", "Puxar (V, ou Ctrl+arrastar): arraste a seleção e os pixels vão junto; o buraco é preenchido", app.puxar),
      sep(),
      botao("apagar", "Apagar", "Apagar com preenchimento (Delete): o que está selecionado some, refeito com o papel em volta", () => app.apagarComPreenchimento(), "destaque"),
      app.podeVariar() ? botao("dado", "", "Outra variação do preenchimento (refaz o último Apagar com outra semente)", app.outraVariacao) : null,
      botao("substituir", "Substituir texto", "Substituir texto (R): apaga o texto selecionado e escreve outro na mesma fonte, cor e posição", () => app.usar("substituir")),
      botao("ajustes", "", "Ajustes na seleção (brilho, níveis, desfoque, ruído, JPEG…)", app.abrirAjustes),
      sep(),
      botao("inverter", "", "Inverter a seleção (Ctrl+Shift+I)", app.inverterSelecao),
      botao("fechar", "", "Desmarcar (Ctrl+D)", app.desmarcar),
    ];
    barra.replaceChildren(...itens.filter(Boolean));
  }

  function montarSolto({ solto }) {
    const f = app.ferramenta;
    const itens = [];
    if (solto.modo === "colar") {
      itens.push(el("span", { class: "bc-txt" }, app.F.colar && app.F.colar.solto()?.doador === app.sessao?.id
        ? "Trecho desta imagem" : "Trecho de outra imagem do split"));
    } else if (solto.recorte && !solto.sujo) {
      itens.push(el("span", { class: "bc-txt" }, "Recortado: arraste para onde quer colar"));
    } else {
      // puxar (o buraco é preenchido) ou duplicar (a origem fica): dá para trocar antes de aplicar
      const seg = el("span", { class: "bc-seg", role: "group", "aria-label": "Ao arrastar" },
        ...[["mover", "Puxar"], ["duplicar", "Duplicar"]].map(([m, r]) => el("button", {
          type: "button", class: solto.modo === m ? "ativo" : "", "aria-pressed": String(solto.modo === m),
          title: m === "mover" ? "O trecho sai daqui e o buraco é preenchido" : "O trecho fica aqui também (Alt+arrastar)",
          onpointerdown: (e) => e.stopPropagation(),
          onclick: (e) => { e.stopPropagation(); f.trocarModo(m); visor.focus({ preventScroll: true }); } }, r)));
      itens.push(seg);
    }
    itens.push(sep());
    itens.push(botao("ok", solto.recorte && !solto.sujo ? "Só apagar" : "Aplicar",
      solto.recorte && !solto.sujo ? "Apaga o trecho daqui (com preenchimento) sem colar em outro lugar" : "Aplicar (Enter)",
      () => f.confirmar(true), "primario"));
    itens.push(botao("fechar", "Cancelar", "Cancelar (Esc): o trecho volta ao lugar", () => f.cancelar()));
    barra.replaceChildren(...itens);
  }

  function posicionar(anc) {
    const v = app.visor, r = visor.getBoundingClientRect();
    const a = v.imgParaTela(anc[0], anc[1]), b = v.imgParaTela(anc[2], anc[3]);
    const w = barra.offsetWidth, h = barra.offsetHeight, m = 10;
    let top = b.y + m;
    if (top + h > r.height - 8) top = a.y - h - m;          // sem espaço embaixo: em cima
    if (top < 8) top = Math.min(r.height - h - 8, Math.max(8, b.y - h - m));  // seleção maior que a vista
    const left = Math.max(8, Math.min(r.width - w - 8, (a.x + b.x) / 2 - w / 2));
    barra.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  function atualizarBarra() {
    const e = estado();
    if (!e) { if (!barra.hidden) barra.hidden = true; chaveBarra = null; return; }
    if (e.chave !== chaveBarra) {
      chaveBarra = e.chave;
      if (e.tipo === "sel") montarSelecao(); else montarSolto(e);
      barra.hidden = false;
    }
    const anc = e.tipo === "solto" ? app.ferramenta.ancora() : app.selBbox();
    if (anc) posicionar(anc);
  }

  // ------------------------------------------------------------------ dicas da barra de status
  function item({ k, m, t }) {
    const partes = [];
    if (k) partes.push(...k.split("+").flatMap((tecla, i) => [i ? el("span", { class: "mais" }, "+") : null, el("kbd", {}, tecla)]));
    if (m) partes.push(el("span", { class: "gesto" }, mouseEsquerdo(13), m));
    return el("span", { class: "dica-item" }, ...partes, el("span", { class: "dica-txt" }, t));
  }
  function atualizarDicas() {
    const id = app.idFerramenta;
    const solto = app.ferramenta?.solto?.();
    const sel = app.temSelecao();
    const chave = `${id}:${sel}:${Boolean(solto)}:${app.sessao ? 1 : 0}`;
    if (chave === chaveDicas) return;
    chaveDicas = chave;
    let lista = id === "transformar" ? (solto ? DICAS.transformar : DICAS.transformar_sem) : (DICAS[id] || []);
    if (sel && !["texto", "substituir", "transformar", "colar"].includes(id)) lista = [...lista, ...DICAS.com_selecao];
    dicas.replaceChildren(el("span", { class: "dica-ferr" }, NOMES[id] || ""), ...[...lista, ...DICAS.sempre].map(item));
  }

  app.atualizarBarras = () => { atualizarBarra(); atualizarDicas(); };
}
