// Quem está usando: o nome vai no cabeçalho X-Operador de toda chamada (api.js) e aparece nas travas
// ("em edição por…"), nas atribuições e nas revisões. Sem senha (fora do escopo).

const CHAVE = "rotulador.operador";

export function nomeOperador() {
  try { return String(JSON.parse(localStorage.getItem(CHAVE) || '""') || "").trim(); } catch { return ""; }
}

export function definirOperador(nome) {
  nome = String(nome || "").trim().slice(0, 80);
  if (!nome) return false;
  try { localStorage.setItem(CHAVE, JSON.stringify(nome)); } catch { /* sem storage */ }
  window.dispatchEvent(new CustomEvent("operador", { detail: nome }));
  return true;
}

// Pede o nome se ainda não há um (diálogo que não fecha sem nome). Resolve com o nome.
export function exigirOperador() {
  const atual = nomeOperador();
  if (atual) return Promise.resolve(atual);
  return new Promise((resolver) => {
    const dlg = document.createElement("dialog");
    dlg.className = "dlg-operador";
    dlg.innerHTML = `<form method="dialog">
      <h2>Quem é você?</h2>
      <p class="dica">O seu nome aparece nos documentos que você está editando (para ninguém editar o mesmo
      documento ao mesmo tempo), nas versões que você salvar e nas revisões que fizer.</p>
      <label>Seu nome <input name="nome" type="text" maxlength="80" required autocomplete="name"
        placeholder="ex.: Ana Souza"></label>
      <div class="modal-acoes"><button class="primario" value="ok">Começar</button></div>
    </form>`;
    document.body.append(dlg);
    const inp = dlg.querySelector("input");
    dlg.addEventListener("cancel", (e) => e.preventDefault());  // Esc não fecha sem nome
    dlg.querySelector("form").addEventListener("submit", (e) => {
      if (!definirOperador(inp.value)) { e.preventDefault(); inp.focus(); return; }
      setTimeout(() => { dlg.remove(); resolver(nomeOperador()); });
    });
    dlg.showModal();
    inp.focus();
  });
}

// Aviso do sistema quando algo longo termina (lote, IA) e a aba não está à vista.
export function notificar(titulo, corpo) {
  try {
    if (document.visibilityState === "visible" && document.hasFocus()) return;
    if ("Notification" in window && Notification.permission === "granted") new Notification(titulo, { body: corpo });
  } catch { /* sem notificações */ }
  const original = document.title.replace(/^✔ /, "");
  document.title = `✔ ${original}`;
  const voltar = () => { document.title = original; window.removeEventListener("focus", voltar); };
  window.addEventListener("focus", voltar);
}

export function pedirPermissaoNotificacao() {
  try { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); } catch { /* ok */ }
}
