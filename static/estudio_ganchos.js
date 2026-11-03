/* Ganchos do estúdio DENTRO do PhotoCraft web (o app.py põe este script no <head> do index.html dele, antes de tudo).
 *
 * O PhotoCraft web não tem API para abrir ou salvar: abre pelo seletor de arquivo (ou soltando o arquivo na tela) e
 * salva baixando o arquivo. Aqui:
 *   - SALVAR: o download (âncora com um blob) vai para a página do estúdio (window.parent.__estudio.salvou), que grava
 *     a versão no dataset com a máscara; o arquivo não vai para a pasta de downloads;
 *   - ABRIR: o arquivo escolhido no "Abrir" (ou arrastado para a tela) passa antes pelo estúdio, que o registra no
 *     dataset e devolve o PNG do registro (os mesmos pixels que o rotulador usa: a diferença no salvar é exata);
 *   - window.__estudioAbrir(arquivos): abre arquivos num documento novo do PhotoCraft (pelo Abrir);
 *   - window.__estudioFechar(alterado): fecha o documento ativo (depois de salvo, para passar ao seguinte);
 *   - window.__estudioMexeu(): houve clique ou tecla da pessoa desde que o estúdio abriu o documento;
 *   - idioma: português na primeira vez (as preferências do PhotoCraft ficam no localStorage).
 * Fora do estúdio (página aberta sozinha), tudo funciona como no PhotoCraft original. */
(() => {
  'use strict';
  const estudio = () => {
    try { return (window.parent !== window && window.parent.__estudio) || null; } catch (e) { return null; }
  };

  // ------------------------------------------------------------ idioma
  // antes de o PhotoCraft subir: na primeira vez ele aceita as preferências só com o idioma (completa o resto com o
  // padrão); depois, troca o "auto" (que no navegador fica em inglês) e respeita o que a pessoa escolheu
  const PREFS = 'photocraft.preferences';
  try {
    const p = JSON.parse(localStorage.getItem(PREFS) || 'null');
    if (!p) {
      localStorage.setItem(PREFS, JSON.stringify({ interface: { language: 'pt-BR' } }));
    } else if (p.interface && p.interface.language === 'auto') {
      p.interface.language = 'pt-BR';
      localStorage.setItem(PREFS, JSON.stringify(p));
    }
  } catch (e) { /* sem localStorage: fica no idioma padrão */ }

  // ------------------------------------------------------------ salvar
  const blobs = new Map();
  const criarUrl = URL.createObjectURL;
  URL.createObjectURL = function (b) {
    const u = criarUrl.call(URL, b);
    if (b instanceof Blob) blobs.set(u, b);
    return u;
  };
  const revogar = URL.revokeObjectURL;
  URL.revokeObjectURL = function (u) {  // o blob só fica guardado enquanto a URL dele vale
    blobs.delete(u);
    return revogar.call(URL, u);
  };
  const clicar = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    const b = blobs.get(this.href), e = estudio();
    if (b) blobs.delete(this.href);
    if (b && this.download && e && e.salvou(this.download, b)) return;  // foi para o dataset: sem download
    return clicar.call(this);
  };

  // ------------------------------------------------------------ abrir
  // O "Abrir" do PhotoCraft web é o da biblioteca rfd: uma janelinha (#rfd-overlay) com o seletor #rfd-input (com
  // "multiple"; o Inserir não tem) e os botões Ok e Cancelar. Com a página ativada por um clique, ela esconde a
  // janelinha e chama input.click() (o seletor do sistema); o "change" do seletor (ou o Ok) resolve, e o "cancel" fecha
  // tirando a janelinha (se ela já saiu, o PhotoCraft trava). Soltar um arquivo na tela com um documento aberto INSERE
  // como camada (como no Photoshop): por isso tudo o que o estúdio abre vai pelo Abrir, e vira um documento novo.
  const tecla = (key, extra = {}) => {
    const cv = document.getElementById('photocraft_canvas');
    if (!cv) return false;
    cv.focus();
    for (const t of ['keydown', 'keyup']) {
      cv.dispatchEvent(new KeyboardEvent(t, { key, code: `Key${key.toUpperCase()}`, ctrlKey: true, bubbles: true,
                                              cancelable: true, ...extra }));
    }
    return true;
  };
  const listaDe = arquivos => { const dt = new DataTransfer(); arquivos.forEach(a => dt.items.add(a)); return dt.files; };
  const ehAbrir = inp => inp && inp.id === 'rfd-input' && inp.multiple;  // sem "multiple": Inserir, pincel... intacto
  const repassar = inp => {  // o "change" que o rfd ouve (resolve com os arquivos que estão no seletor)
    inp.__repassar = true;
    try { inp.dispatchEvent(new Event('change')); } finally { inp.__repassar = false; }
  };
  let fila = null;  // {arquivos, ok}: o próximo Abrir é do estúdio (o painel pediu)
  // a pessoa clicou ou digitou no PhotoCraft desde que o estúdio abriu o documento? (o Pular só pergunta nesse caso)
  let mexeu = false;
  for (const t of ['pointerdown', 'keydown']) window.addEventListener(t, ev => { if (ev.isTrusted) mexeu = true; }, true);
  window.__estudioMexeu = () => mexeu;

  const atender = inp => {  // o estúdio abrindo: o arquivo dele no seletor, sem o seletor do sistema
    const f = fila;
    fila = null;
    mexeu = false;
    inp.__estudioAbrir = true;
    inp.files = listaDe(f.arquivos);
    setTimeout(() => { repassar(inp); f.ok(true); }, 0);
  };
  const clicarInput = HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click = function () {
    if (fila && ehAbrir(this)) { atender(this); return; }  // nada de abrir o seletor do sistema
    return clicarInput.call(this);
  };

  const nova = inp => {
    if (inp.__visto || !ehAbrir(inp)) return;
    inp.__visto = true;
    inp.__estudioAbrir = true;
    if (fila) { atender(inp); return; }  // janelinha à vista (sem clique recente): o mesmo caminho
    // a pessoa escolheu um arquivo: o "change" espera ele passar pelo estúdio (entra no dataset e volta como o PNG do
    // registro), e só então chega ao rfd
    inp.addEventListener('change', ev => {
      const e = estudio(), arqs = Array.from(inp.files || []);
      if (inp.__repassar || !e || !arqs.length) return;
      ev.stopImmediatePropagation();
      e.abriu(arqs).then(novos => { if (novos && novos.length) inp.files = listaDe(novos); })
        .catch(() => {}).finally(() => repassar(inp));
    }, true);
  };
  new MutationObserver(() => {
    const inp = document.getElementById('rfd-input');
    if (inp) nova(inp);
  }).observe(document.documentElement, { childList: true, subtree: true });

  // abre arquivos num documento novo do PhotoCraft (o Abrir, preenchido pelo estúdio); resolve quando o Ok foi clicado
  window.__estudioAbrir = arquivos => new Promise((ok, erro) => {
    if (!document.getElementById('photocraft_canvas') || document.getElementById('photocraft_loading')) {
      erro(new Error('o PhotoCraft ainda está carregando'));
      return;
    }
    fila = { arquivos, ok, erro };
    tecla('o');
    setTimeout(() => { if (fila && fila.arquivos === arquivos) { fila = null; erro(new Error('o PhotoCraft não abriu a janela de Abrir (feche o diálogo ou a transformação em andamento e tente de novo)')); } }, 4000);
  });

  // arquivo de verdade solto na tela: passa pelo estúdio e abre num documento novo
  window.addEventListener('drop', ev => {
    const e = estudio();
    if (!ev.isTrusted || !e || !ev.dataTransfer || !ev.dataTransfer.files.length) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    e.abriu(Array.from(ev.dataTransfer.files)).then(novos => novos && novos.length && window.__estudioAbrir(novos))
      .catch(err => e.avisar && e.avisar(err.message, 'erro'));
  }, true);

  // o botão Salvar do painel: o Ctrl+S do PhotoCraft
  window.__estudioCtrlS = () => tecla('s');

  // fecha o documento ativo (Ctrl+W). O Ctrl+S daqui baixa o arquivo e o PhotoCraft web continua achando que há
  // alterações não salvas: com ``alterado`` (a pessoa mexeu no documento), ele pergunta se salva antes de fechar e a
  // resposta é "Não" (N) — a versão já está no dataset. Documento aberto e não mexido fecha sem perguntar (aí um N
  // sobraria para o documento seguinte)
  window.__estudioFechar = alterado => new Promise(ok => {
    if (!tecla('w')) { ok(false); return; }
    if (!alterado) { setTimeout(() => ok(true), 300); return; }
    setTimeout(() => { tecla('n', { ctrlKey: false }); setTimeout(() => ok(true), 300); }, 500);
  });
})();
