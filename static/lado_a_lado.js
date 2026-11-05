// Lado a lado: com o tipo "pegar de outro documento", outra imagem do MESMO split aparece à esquerda da que você edita,
// de preferência uma que ainda não serviu de doadora. Selecione o trecho nela (retângulo ou laço) e arraste-o até a
// imagem da direita; duplo clique, "Mesmo lugar" ou Ctrl+Shift+V colam nas mesmas coordenadas (Ctrl+V, no meio da vista). O trecho vem do original da
// doadora, só no formato selecionado (é o mesmo Ctrl+C / Ctrl+V entre imagens; o servidor recusa outro split).
import { bitmapUrl, get } from "./api.js";
import { Visor } from "./visor.js";

const TIPO = "pegar de outro documento";

function contorno(ctx, desenhar) {
  ctx.save();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "#000"; ctx.setLineDash([]); desenhar(); ctx.stroke();
  ctx.strokeStyle = "#fff"; ctx.setLineDash([4, 4]); desenhar(); ctx.stroke();
  ctx.restore();
}

export function instalarLadoALado(app) {
  const $ = (id) => document.getElementById(id);
  const painel = $("doador"), tela = $("doador-tela"), area = $("area-doc");
  const v = new Visor($("doador-visor"), tela);
  app.visorDoador = v;  // para depurar e para os roteiros de tela
  // lista de doadoras, a mostrada, a forma selecionada nela e os gestos em andamento
  const E = { lista: [], i: 0, doc: null, forma: null, clip: null, desenho: null, arrasto: null, fantasma: null,
    pan: null, modo: "ret", fechado: false, pedido: 0 };

  const visivel = () => !painel.hidden;
  const deveMostrar = () => Boolean(app.sessao) && app.categoria === TIPO && !E.fechado && innerWidth >= 900;
  const doadora = () => E.lista[E.i] || null;
  const usadaAqui = (id) => (app.sessao?.historico || []).some((h) => h.rotulo === "colar" && h.params?.doador === id);

  // ------------------------------------------------------------------ qual imagem fica ao lado
  async function carregarLista() {
    const doc = app.sessao.id, n = ++E.pedido;
    E.doc = doc;
    let lista = [];
    try { lista = await get(`/api/doadores?id=${encodeURIComponent(doc)}`); } catch (e) { app.aviso(e.message, "erro"); }
    if (n !== E.pedido) return;
    // primeiro as que ainda não serviram de doadoras (nas versões salvas do split); depois as menos usadas
    E.lista = lista.map((d, k) => ({ ...d, k })).sort((a, b) => a.usos - b.usos || a.k - b.k);
    E.i = 0;
    await mostrarDoadora();
  }

  function atualizarSelo() {
    const d = doadora(), s = $("doador-selo");
    if (!d) { s.textContent = ""; s.className = "pilula"; return; }
    const aqui = usadaAqui(d.id);
    s.textContent = aqui ? "usada nesta imagem" : d.usos ? `já usada em ${d.usos} versão(ões)` : "ainda não usada";
    s.className = `pilula${d.usos || aqui ? "" : " ok"}`;
  }

  async function mostrarDoadora() {
    const d = doadora();
    limparForma();
    $("doador-nome").textContent = d ? d.nome : "não há outra imagem neste split";
    $("doador-nome").title = d ? d.nome : "";
    $("doador-pos").textContent = d ? `${E.i + 1} de ${E.lista.length}` : "";
    $("doador-ant").disabled = $("doador-prox").disabled = E.lista.length < 2;
    $("doador-colar").disabled = true;
    atualizarSelo();
    if (!d) { v.img = null; v.redesenhar(); return; }
    const n = ++E.pedido;
    $("doador-carregando").hidden = false;
    try {
      const bmp = await bitmapUrl(`/api/img/${d.id}/original.png`);
      if (n === E.pedido) v.definirImagem(bmp);
    } catch (e) { app.aviso(e.message, "erro"); } finally { if (n === E.pedido) $("doador-carregando").hidden = true; }
  }

  function trocar(passo) {
    if (E.lista.length < 2) return;
    E.i = (E.i + passo + E.lista.length) % E.lista.length;
    mostrarDoadora();
  }

  // mostra ou esconde conforme o tipo da edição; troca a doadora quando a imagem da direita muda
  app.atualizarLadoALado = async () => {
    const mostrar = deveMostrar();
    if (mostrar !== visivel()) {
      painel.hidden = !mostrar;
      area.classList.toggle("lado-a-lado", mostrar);
      requestAnimationFrame(() => { app.visor.ajustar(); if (mostrar) v.ajustar(); });
    }
    if (!mostrar) return;
    if (E.doc !== app.sessao.id) await carregarLista();
    else atualizarSelo();
  };
  app.ladoALadoVisivel = visivel;
  app.alternarLadoALado = () => {
    if (visivel()) E.fechado = true;
    else {
      E.fechado = false;
      if (app.categoria !== TIPO) app.escolherCategoria(TIPO);
    }
    app.atualizarLadoALado();
  };

  // ------------------------------------------------------------------ seleção na doadora
  function limparForma() {
    E.forma = E.clip = E.desenho = null;
    $("doador-colar").disabled = true;
    v.redesenhar();
  }
  const preso = (p) => ({ x: Math.max(0, Math.min(v.largura, p.x)), y: Math.max(0, Math.min(v.altura, p.y)) });
  const dentro = (p) => { const b = E.forma?.bbox; return Boolean(b) && p.x >= b[0] && p.x <= b[2] && p.y >= b[1] && p.y <= b[3]; };

  function terminarForma() {
    const { a, b, pts } = E.desenho;
    E.desenho = null;
    let forma;
    if (E.modo === "laco") {
      if (pts.length < 3) { limparForma(); return; }
      forma = { tipo: "poligono", pontos: pts.map((q) => [q.x, q.y]) };
    } else {
      if (Math.abs(a.x - b.x) * v.escala < 3 || Math.abs(a.y - b.y) * v.escala < 3) { limparForma(); return; }
      forma = { tipo: "ret", x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y) };
    }
    const xs = E.modo === "laco" ? pts.map((q) => q.x) : [forma.x0, forma.x1];
    const ys = E.modo === "laco" ? pts.map((q) => q.y) : [forma.y0, forma.y1];
    const bbox = [Math.floor(Math.min(...xs)), Math.floor(Math.min(...ys)), Math.ceil(Math.max(...xs)), Math.ceil(Math.max(...ys))];
    if (bbox[2] - bbox[0] < 2 || bbox[3] - bbox[1] < 2) { limparForma(); return; }
    // os pixels (a prévia segue o mouse na imagem da direita até o servidor responder)
    const pre = document.createElement("canvas");
    pre.width = bbox[2] - bbox[0]; pre.height = bbox[3] - bbox[1];
    const g = pre.getContext("2d");
    if (E.modo === "laco") {
      g.beginPath();
      pts.forEach((q, k) => (k ? g.lineTo(q.x - bbox[0], q.y - bbox[1]) : g.moveTo(q.x - bbox[0], q.y - bbox[1])));
      g.closePath(); g.clip();
    }
    g.drawImage(v.img, -bbox[0], -bbox[1]);
    const d = doadora();
    E.forma = { forma, bbox, pts: E.modo === "laco" ? pts : null };
    $("doador-dica").hidden = true;  // a pessoa já entendeu: a dica não cobre mais a imagem
    E.clip = { doc: d.id, split: app.sessao.split, nome: d.nome, selecao: { formas: [{ ...forma, modo: "novo" }], inverter: false,
      expandir: 0, suavizar: 0 }, bbox, pixels: pre, recorte: false };
    app.clip = E.clip;  // Ctrl+V na imagem da direita também cola (no mesmo lugar)
    $("doador-colar").disabled = false;
    app.status("trecho pego da outra imagem: arraste-o até a imagem da direita (ou duplo clique: mesmo lugar)");
    v.redesenhar();
  }

  function colar(centro) {
    if (!E.clip) return;
    app.clip = E.clip;
    app.colar(centro ? { centro } : { noLugar: true });  // sem ponto: nas mesmas coordenadas
  }

  // ------------------------------------------------------------------ arrastar o trecho até a imagem da direita
  const sobreVisor = (ev) => {
    const r = app.visor.el.getBoundingClientRect();
    return ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
  };
  function moverFantasma(ev) {
    const a = E.arrasto;
    if (!a) return;
    if (!a.moveu && Math.hypot(ev.clientX - a.x, ev.clientY - a.y) < 4) return;
    if (!a.moveu) {
      a.moveu = true;
      const c = document.createElement("canvas"), pre = E.clip.pixels;
      c.width = pre.width; c.height = pre.height;
      c.getContext("2d").drawImage(pre, 0, 0);
      c.className = "fantasma-doador";
      // no tamanho que vai ter na imagem da direita (o zoom de lá)
      c.style.width = `${pre.width * app.visor.escala}px`; c.style.height = `${pre.height * app.visor.escala}px`;
      document.body.append(c);
      E.fantasma = c;
    }
    const f = E.fantasma;
    f.style.left = `${ev.clientX - f.offsetWidth / 2}px`; f.style.top = `${ev.clientY - f.offsetHeight / 2}px`;
    app.visor.el.classList.toggle("alvo-soltar", sobreVisor(ev));
  }
  // o arraste entre as duas imagens é ouvido na janela inteira: o soltar vale mesmo se o navegador perder a captura
  // do ponteiro no meio do caminho (o trecho não fica preso ao mouse)
  function comecarArrasto(ev) {
    E.arrasto = { x: ev.clientX, y: ev.clientY, moveu: false };
    window.addEventListener("pointermove", moverFantasma, true);
    window.addEventListener("pointerup", soltarFantasma, true);
    window.addEventListener("pointercancel", soltarFantasma, true);
  }
  function soltarFantasma(ev) {
    window.removeEventListener("pointermove", moverFantasma, true);
    window.removeEventListener("pointerup", soltarFantasma, true);
    window.removeEventListener("pointercancel", soltarFantasma, true);
    const a = E.arrasto;
    if (!a) return;
    E.arrasto = null;
    if (E.fantasma) { E.fantasma.remove(); E.fantasma = null; }
    app.visor.el.classList.remove("alvo-soltar");
    if (!a.moveu) return;
    if (!sobreVisor(ev)) { app.status("solte o trecho em cima da imagem da direita"); return; }
    colar(app.visor.doEvento(ev));
  }

  // ------------------------------------------------------------------ mouse na doadora
  tela.addEventListener("pointerdown", (ev) => {
    if (!v.img || app.ocupado) return;
    tela.setPointerCapture(ev.pointerId);
    if (ev.button === 1 || app.espaco) { E.pan = v.telaDoEvento(ev); tela.style.cursor = "grabbing"; return; }
    if (ev.button !== 0) return;
    const p = preso(v.doEvento(ev));
    if (dentro(p)) { comecarArrasto(ev); return; }
    E.desenho = { a: p, b: p, pts: [p] };
  });
  tela.addEventListener("pointermove", (ev) => {
    if (!v.img) return;
    if (E.pan) { const s = v.telaDoEvento(ev); v.mover(s.x - E.pan.x, s.y - E.pan.y); E.pan = s; return; }
    if (E.desenho) {
      const p = preso(v.doEvento(ev)), ult = E.desenho.pts[E.desenho.pts.length - 1];
      E.desenho.b = p;
      if (E.modo === "laco" && Math.hypot(p.x - ult.x, p.y - ult.y) * v.escala >= 2) E.desenho.pts.push(p);
      v.redesenhar();
      return;
    }
    if (E.arrasto) return;  // quem segue o arraste é a janela (comecarArrasto)
    tela.style.cursor = app.espaco ? "grab" : dentro(v.doEvento(ev)) ? "grab" : "crosshair";
  });
  const soltar = (ev) => {
    if (E.pan) { E.pan = null; tela.style.cursor = "crosshair"; return; }
    if (E.desenho) terminarForma();
  };
  tela.addEventListener("pointerup", soltar);
  tela.addEventListener("pointercancel", soltar);
  tela.addEventListener("dblclick", (ev) => { if (dentro(v.doEvento(ev))) colar(null); });
  tela.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const s = v.telaDoEvento(ev);
    if (ev.shiftKey) v.mover(-ev.deltaY, 0); else v.zoomEm(Math.exp(-ev.deltaY * 0.0015), s.x, s.y);
  }, { passive: false });
  tela.addEventListener("contextmenu", (ev) => ev.preventDefault());

  // a seleção desenhada (em andamento ou pronta), em px de tela
  v.extra = (ctx, vv) => {
    const caminho = (pts, fechar) => () => {
      ctx.beginPath();
      pts.forEach((q, k) => { const s = vv.imgParaTela(q.x, q.y); k ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y); });
      if (fechar) ctx.closePath();
    };
    const ret = (a, b) => () => {
      const s = vv.imgParaTela(Math.min(a.x, b.x), Math.min(a.y, b.y)), t = vv.imgParaTela(Math.max(a.x, b.x), Math.max(a.y, b.y));
      ctx.beginPath(); ctx.rect(s.x, s.y, t.x - s.x, t.y - s.y);
    };
    let desenhar = null;
    if (E.desenho) desenhar = E.modo === "laco" ? caminho(E.desenho.pts, false) : ret(E.desenho.a, E.desenho.b);
    else if (E.forma) {
      const f = E.forma.forma;
      desenhar = E.forma.pts ? caminho(E.forma.pts, true) : ret({ x: f.x0, y: f.y0 }, { x: f.x1, y: f.y1 });
      ctx.save(); ctx.fillStyle = "#00a8ff22"; desenhar(); ctx.fill(); ctx.restore();
    }
    if (desenhar) contorno(ctx, desenhar);
  };

  // ------------------------------------------------------------------ cabeçalho
  $("doador-ant").addEventListener("click", () => trocar(-1));
  $("doador-prox").addEventListener("click", () => trocar(1));
  $("doador-colar").addEventListener("click", () => colar(null));
  $("doador-fechar").addEventListener("click", () => { E.fechado = true; app.atualizarLadoALado(); });
  for (const b of painel.querySelectorAll("[data-modo]")) {
    b.addEventListener("click", () => {
      E.modo = b.dataset.modo;
      painel.querySelectorAll("[data-modo]").forEach((x) => {
        x.classList.toggle("ativo", x === b); x.setAttribute("aria-pressed", String(x === b));
      });
    });
  }
  window.addEventListener("resize", () => app.atualizarLadoALado());
}
