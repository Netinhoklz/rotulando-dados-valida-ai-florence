// Barra de menus no alto (Arquivo, Editar, Selecionar…), como num editor de imagem de desktop.
//   montarMenus(nav, menus) — menus: [{ rotulo, itens: [{ rotulo, atalho, acao, ativo?(), marcado?(), icone? } | "-"] }]
//   menuAberto()            — o teclado do editor fica quieto enquanto um menu está aberto
import { icone } from "./icones.js";

let aberto = null;  // { botao, lista }

export const menuAberto = () => Boolean(aberto);

function fechar() {
  if (!aberto) return;
  aberto.lista.remove();
  aberto.botao.classList.remove("aberto");
  aberto.botao.setAttribute("aria-expanded", "false");
  aberto = null;
}

function abrir(botao, menu, focar = false) {
  fechar();
  const lista = document.createElement("div");
  lista.className = "menu-lista";
  lista.setAttribute("role", "menu");
  for (const it of menu.itens) {
    if (it === "-") { lista.append(Object.assign(document.createElement("hr"), { className: "menu-sep" })); continue; }
    if (it.titulo) {  // cabeçalho de grupo dentro do menu
      lista.append(Object.assign(document.createElement("div"), { className: "menu-grupo", textContent: it.titulo }));
      continue;
    }
    const b = document.createElement("button");
    b.type = "button";
    b.className = "menu-item";
    b.setAttribute("role", it.marcado ? "menuitemcheckbox" : "menuitem");
    const ativo = it.ativo ? it.ativo() : true;
    const marcado = it.marcado ? it.marcado() : null;
    b.disabled = !ativo;
    if (marcado !== null) b.setAttribute("aria-checked", String(marcado));
    const marca = document.createElement("span");
    marca.className = "menu-marca";
    if (marcado) marca.append(icone("ok", 14));
    else if (it.icone) marca.append(icone(it.icone, 15));
    const rot = Object.assign(document.createElement("span"), { className: "menu-rot", textContent: it.rotulo });
    const at = Object.assign(document.createElement("span"), { className: "menu-atalho", textContent: it.atalho || "" });
    b.append(marca, rot, at);
    if (it.dica) b.title = it.dica;
    b.addEventListener("click", () => { fechar(); it.acao(); });
    lista.append(b);
  }
  const r = botao.getBoundingClientRect();
  lista.style.left = `${Math.max(4, Math.min(r.left, innerWidth - 300))}px`;
  lista.style.top = `${r.bottom + 2}px`;
  document.body.append(lista);
  botao.classList.add("aberto");
  botao.setAttribute("aria-expanded", "true");
  aberto = { botao, lista };
  if (focar) lista.querySelector(".menu-item:not(:disabled)")?.focus();
}

export function montarMenus(nav, menus) {
  nav.replaceChildren();
  const botoes = menus.map((m) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "menu-topo";
    b.textContent = m.rotulo;
    b.setAttribute("aria-haspopup", "menu");
    b.setAttribute("aria-expanded", "false");
    b.addEventListener("click", (e) => { e.stopPropagation(); if (aberto?.botao === b) fechar(); else abrir(b, m, e.detail === 0); });
    // com um menu aberto, passar o mouse em outro o abre (como no desktop)
    b.addEventListener("pointerenter", () => { if (aberto && aberto.botao !== b) abrir(b, m); });
    nav.append(b);
    return [b, m];
  });
  document.addEventListener("pointerdown", (e) => {
    if (aberto && !aberto.lista.contains(e.target) && !nav.contains(e.target)) fechar();
  }, true);
  window.addEventListener("blur", fechar);
  window.addEventListener("resize", fechar);
  document.addEventListener("keydown", (e) => {
    if (!aberto) return;
    const itens = [...aberto.lista.querySelectorAll(".menu-item:not(:disabled)")];
    const i = itens.indexOf(document.activeElement);
    const k = e.key;
    if (k === "Escape") { e.preventDefault(); e.stopPropagation(); const b = aberto.botao; fechar(); b.focus(); return; }
    if (k === "ArrowDown" || k === "ArrowUp") {
      e.preventDefault(); e.stopPropagation();
      const j = i < 0 ? (k === "ArrowDown" ? 0 : itens.length - 1) : (i + (k === "ArrowDown" ? 1 : -1) + itens.length) % itens.length;
      itens[j]?.focus();
      return;
    }
    if (k === "ArrowLeft" || k === "ArrowRight") {
      e.preventDefault(); e.stopPropagation();
      const n = botoes.findIndex(([b]) => b === aberto.botao);
      const [b, m] = botoes[(n + (k === "ArrowRight" ? 1 : -1) + botoes.length) % botoes.length];
      abrir(b, m, true);
      return;
    }
    e.stopPropagation();  // nada de atalhos de ferramenta com o menu aberto; Enter/Espaço clicam o item em foco
    if (k !== "Enter" && k !== " " && k !== "Tab") e.preventDefault();
  }, true);
}
