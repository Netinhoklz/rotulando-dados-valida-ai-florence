// Ferramentas do editor. Cada uma descreve a edição em vetores (pontos, retângulos, texto);
// o servidor aplica na resolução original. ``app`` é o editor (editor.js).

const RAIO_ALCA = 7; // px de tela

function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

function modoSelecao(ev, padrao) {
  if (ev.shiftKey && ev.altKey) return "intersectar";
  if (ev.shiftKey) return "somar";
  if (ev.altKey) return "subtrair";
  return padrao || "novo";
}

function tracarTela(ctx, visor, pts, fechar) {
  if (pts.length < 2) return;
  ctx.beginPath();
  pts.forEach((p, i) => { const s = visor.imgParaTela(p.x, p.y); i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y); });
  if (fechar) ctx.closePath();
}

function contorno(ctx, desenhar) {
  ctx.save();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "#000"; ctx.setLineDash([]); desenhar(); ctx.stroke();
  ctx.strokeStyle = "#fff"; ctx.setLineDash([4, 4]); desenhar(); ctx.stroke();
  ctx.restore();
}

function circuloCursor(ctx, visor, p, tamanho) {
  if (!p) return;
  const s = visor.imgParaTela(p.x, p.y);
  const r = Math.max(1, tamanho * visor.escala / 2);
  contorno(ctx, () => { ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, Math.PI * 2); });
}

// ---------------------------------------------------------------------------
// quadrilátero com alças (mover / escalar / girar / perspectiva)
// ---------------------------------------------------------------------------
export class Quad {
  constructor(x0, y0, x1, y1) {
    this.cx = (x0 + x1) / 2; this.cy = (y0 + y1) / 2;
    this.w = x1 - x0; this.h = y1 - y0; this.ang = 0; this.livre = null;
  }
  cantos() {
    if (this.livre) return this.livre.map((p) => ({ ...p }));
    const c = Math.cos(this.ang), s = Math.sin(this.ang), w = this.w / 2, h = this.h / 2;
    return [[-w, -h], [w, -h], [w, h], [-w, h]].map(([u, v]) => ({ x: this.cx + u * c - v * s, y: this.cy + u * s + v * c }));
  }
  centro() {
    if (!this.livre) return { x: this.cx, y: this.cy };
    return { x: this.livre.reduce((a, p) => a + p.x, 0) / 4, y: this.livre.reduce((a, p) => a + p.y, 0) / 4 };
  }
  contem(p) {
    const q = this.cantos();
    let dentro = false;
    for (let i = 0, j = 3; i < 4; j = i++) {
      if ((q[i].y > p.y) !== (q[j].y > p.y) && p.x < (q[j].x - q[i].x) * (p.y - q[i].y) / (q[j].y - q[i].y) + q[i].x) dentro = !dentro;
    }
    return dentro;
  }
  mover(dx, dy) {
    if (this.livre) this.livre.forEach((p) => { p.x += dx; p.y += dy; });
    else { this.cx += dx; this.cy += dy; }
  }
  girar(da) {
    if (!this.livre) { this.ang += da; return; }
    const c = this.centro(), co = Math.cos(da), si = Math.sin(da);
    this.livre = this.livre.map((p) => ({ x: c.x + (p.x - c.x) * co - (p.y - c.y) * si, y: c.y + (p.x - c.x) * si + (p.y - c.y) * co }));
  }
  arrastarCanto(i, p, perspectiva, proporcao, base) {
    if (perspectiva || this.livre) {
      if (!this.livre) this.livre = this.cantos();
      this.livre[i] = { x: p.x, y: p.y };
      return;
    }
    const opp = this.cantos()[(i + 2) % 4];
    const u = { x: Math.cos(this.ang), y: Math.sin(this.ang) }, v = { x: -u.y, y: u.x };
    const d = { x: p.x - opp.x, y: p.y - opp.y };
    let du = d.x * u.x + d.y * u.y, dv = d.x * v.x + d.y * v.y;
    if (proporcao && base) {
      const f = Math.max(Math.abs(du) / base.w, Math.abs(dv) / base.h);
      du = Math.sign(du || 1) * base.w * f; dv = Math.sign(dv || 1) * base.h * f;
    }
    this.w = Math.max(1, Math.abs(du)); this.h = Math.max(1, Math.abs(dv));
    const su = Math.sign(du || 1) * this.w / 2, sv = Math.sign(dv || 1) * this.h / 2;
    this.cx = opp.x + su * u.x + sv * v.x;
    this.cy = opp.y + su * u.y + sv * v.y;
  }
  destino() { return this.cantos().map((p) => [p.x, p.y]); }
}

function desenharQuad(ctx, visor, quad) {
  const q = quad.cantos().map((p) => visor.imgParaTela(p.x, p.y));
  contorno(ctx, () => { ctx.beginPath(); q.forEach((s, i) => (i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y))); ctx.closePath(); });
  ctx.save();
  for (const s of q) {
    ctx.fillStyle = "#fff"; ctx.strokeStyle = "#000";
    ctx.fillRect(s.x - 4, s.y - 4, 8, 8); ctx.strokeRect(s.x - 4.5, s.y - 4.5, 9, 9);
  }
  ctx.restore();
}

// interação comum de V (transformar) e colar
function interacaoQuad(app, obterQuad, aoMudar) {
  let arr = null;
  return {
    down(p, ev) {
      const quad = obterQuad();
      if (!quad) return false;
      const s = app.visor.imgParaTela(p.x, p.y);
      const cantos = quad.cantos();
      const i = cantos.findIndex((c) => dist(app.visor.imgParaTela(c.x, c.y), s) <= RAIO_ALCA + 2);
      if (i >= 0) arr = { tipo: "canto", i, base: { w: quad.w, h: quad.h } };
      else if (quad.contem(p)) arr = { tipo: "mover", ultimo: p };
      else {
        const c = quad.centro();
        arr = { tipo: "girar", ang0: Math.atan2(p.y - c.y, p.x - c.x) };
      }
      return true;
    },
    move(p, ev) {
      const quad = obterQuad();
      if (!arr || !quad) return;
      if (arr.tipo === "mover") { quad.mover(p.x - arr.ultimo.x, p.y - arr.ultimo.y); arr.ultimo = p; }
      else if (arr.tipo === "canto") quad.arrastarCanto(arr.i, p, ev.ctrlKey, ev.shiftKey, arr.base);
      else {
        const c = quad.centro(), a = Math.atan2(p.y - c.y, p.x - c.x);
        let da = a - arr.ang0;
        if (ev.shiftKey) da = Math.round(da / (Math.PI / 12)) * (Math.PI / 12);
        quad.girar(da); arr.ang0 = ev.shiftKey ? arr.ang0 + da : a;
      }
      aoMudar();
    },
    up() { arr = null; },
    tecla(ev) {
      const quad = obterQuad();
      if (!quad) return false;
      const passo = ev.shiftKey ? 10 : 1;
      const mov = { ArrowLeft: [-passo, 0], ArrowRight: [passo, 0], ArrowUp: [0, -passo], ArrowDown: [0, passo] }[ev.key];
      if (!mov) return false;
      quad.mover(...mov); aoMudar(); return true;
    },
    cursor(p) {
      const quad = obterQuad();
      if (!quad) return "default";
      const s = app.visor.imgParaTela(p.x, p.y);
      if (quad.cantos().some((c) => dist(app.visor.imgParaTela(c.x, c.y), s) <= RAIO_ALCA + 2)) return "nwse-resize";
      return quad.contem(p) ? "move" : "alias";
    },
  };
}

// ---------------------------------------------------------------------------
export function criarFerramentas(app) {
  const F = {};
  const op = (id) => app.opcoes(id);
  const camposSelecao = [
    { chave: "modo", tipo: "select", rotulo: "Modo", opcoes: [["novo", "nova"], ["somar", "somar (Shift)"], ["subtrair", "subtrair (Alt)"], ["intersectar", "intersectar (Shift+Alt)"]], padrao: "novo" },
  ];
  const botoesSelecao = [
    { rotulo: "Tudo", titulo: "Selecionar tudo (Ctrl+A)", acao: () => app.selecionarTudo() },
    { rotulo: "Inverter", titulo: "Inverter seleção (Ctrl+Shift+I)", acao: () => app.inverterSelecao() },
    { rotulo: "Desmarcar", titulo: "Desmarcar (Ctrl+D)", acao: () => app.desmarcar() },
    { campoSel: "expandir", rotulo: "Expandir/contrair px" },
    { campoSel: "suavizar", rotulo: "Suavizar px" },
  ];

  // ------------------------------------------------------------- navegação
  F.mao = {
    nome: "Mão (H / segure Espaço)", icone: "✋", atalho: "h", cursor: "grab",
    down(p, ev) { this._u = app.visor.telaDoEvento(ev); },
    move(p, ev) { if (!this._u) return; const s = app.visor.telaDoEvento(ev); app.visor.mover(s.x - this._u.x, s.y - this._u.y); this._u = s; },
    up() { this._u = null; },
  };
  F.zoom = {
    nome: "Zoom (Z; Alt = afastar)", icone: "🔍", atalho: "z", cursor: "zoom-in",
    down(p, ev) { const s = app.visor.telaDoEvento(ev); app.visor.zoomEm(ev.altKey ? 1 / 1.6 : 1.6, s.x, s.y); },
    botoes: [{ rotulo: "Ajustar (0)", acao: () => app.visor.ajustar() }, { rotulo: "100% (1)", acao: () => app.visor.zoom100() }],
  };

  // ------------------------------------------------------------- seleção
  function selRet(id, nome, icone, atalho, tipo) {
    return {
      nome, icone, atalho, cursor: "crosshair", grupo: "selecao", campos: camposSelecao, botoes: botoesSelecao,
      down(p, ev) { this._a = p; this._b = p; this._modo = modoSelecao(ev, op(id).modo); },
      move(p) { if (this._a) { this._b = p; app.visor.redesenhar(); } },
      up(p) {
        if (!this._a) return;
        const a = this._a, b = p; this._a = null;
        const pequeno = Math.abs(a.x - b.x) * app.visor.escala < 3 && Math.abs(a.y - b.y) * app.visor.escala < 3;
        if (pequeno) { if (this._modo === "novo") app.desmarcar(); app.visor.redesenhar(); return; }
        app.adicionarForma({ tipo, x0: a.x, y0: a.y, x1: b.x, y1: b.y }, this._modo);
      },
      desenhar(ctx, v) {
        if (!this._a) return;
        const a = v.imgParaTela(this._a.x, this._a.y), b = v.imgParaTela(this._b.x, this._b.y);
        contorno(ctx, () => {
          ctx.beginPath();
          if (tipo === "ret") ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y);
          else ctx.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2, Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2, 0, 0, Math.PI * 2);
        });
      },
    };
  }
  F.ret = selRet("ret", "Seleção retangular (M)", "⬚", "m", "ret");
  F.elipse = selRet("elipse", "Seleção elíptica (Shift+M)", "◯", "M", "elipse");

  F.laco = {
    nome: "Laço (L) — marque 'poligonal' para clicar ponto a ponto", icone: "➰", atalho: "l", cursor: "crosshair", grupo: "selecao",
    campos: [...camposSelecao, { chave: "poligonal", tipo: "bool", rotulo: "Poligonal", padrao: false }],
    botoes: botoesSelecao,
    down(p, ev) {
      if (op("laco").poligonal) {
        if (!this._pts) { this._pts = [p]; this._modo = modoSelecao(ev, op("laco").modo); }
        else if (this._pts.length > 2 && dist(app.visor.imgParaTela(p.x, p.y), app.visor.imgParaTela(this._pts[0].x, this._pts[0].y)) < 8) this.fechar();
        else this._pts.push(p);
      } else { this._pts = [p]; this._modo = modoSelecao(ev, op("laco").modo); this._arrastando = true; }
    },
    move(p) {
      this._mouse = p;
      if (this._arrastando && this._pts) {
        const ult = this._pts[this._pts.length - 1];
        if (dist(app.visor.imgParaTela(p.x, p.y), app.visor.imgParaTela(ult.x, ult.y)) >= 2) this._pts.push(p);
      }
      if (this._pts) app.visor.redesenhar();
    },
    up() { if (this._arrastando) { this._arrastando = false; this.fechar(); } },
    dbl() { if (op("laco").poligonal) this.fechar(); },
    fechar() {
      const pts = this._pts; this._pts = null;
      if (pts && pts.length >= 3) app.adicionarForma({ tipo: "poligono", pontos: pts.map((q) => [q.x, q.y]) }, this._modo);
      app.visor.redesenhar();
    },
    tecla(ev) {
      if (!this._pts) return false;
      if (ev.key === "Enter") { this.fechar(); return true; }
      if (ev.key === "Escape") { this._pts = null; app.visor.redesenhar(); return true; }
      if (ev.key === "Backspace") { this._pts.pop(); if (!this._pts.length) this._pts = null; app.visor.redesenhar(); return true; }
      return false;
    },
    desenhar(ctx, v) {
      if (!this._pts) return;
      const pts = op("laco").poligonal && this._mouse ? [...this._pts, this._mouse] : this._pts;
      contorno(ctx, () => tracarTela(ctx, v, pts, false));
    },
    desativar() { this._pts = null; },
  };

  F.varinha = {
    nome: "Varinha mágica (W)", icone: "✨", atalho: "w", cursor: "crosshair", grupo: "selecao",
    campos: [...camposSelecao,
      { chave: "tolerancia", tipo: "num", rotulo: "Tolerância", min: 0, max: 255, passo: 1, padrao: 24 },
      { chave: "contigua", tipo: "bool", rotulo: "Contígua", padrao: true }],
    botoes: botoesSelecao,
    down(p, ev) {
      const o = op("varinha");
      app.adicionarForma({ tipo: "varinha", x: p.x, y: p.y, tolerancia: o.tolerancia, contigua: o.contigua }, modoSelecao(ev, o.modo));
    },
  };

  // ------------------------------------------------------------- pintura
  function ferramentaTraco(id, nome, icone, atalho, tipo, campos, extra = {}) {
    return {
      nome, icone, atalho, cursor: "none", campos, botoes: extra.botoes,
      down(p, ev) {
        if (extra.antes && extra.antes(p, ev) === false) return;
        this._pts = [p];
        app.visor.redesenhar();
      },
      move(p) {
        this._mouse = p;
        if (this._pts) {
          const ult = this._pts[this._pts.length - 1];
          if (dist(app.visor.imgParaTela(p.x, p.y), app.visor.imgParaTela(ult.x, ult.y)) >= 1.5) this._pts.push(p);
        }
        app.visor.redesenhar();
      },
      async up() {
        if (!this._pts) return;
        const pts = this._pts.map((q) => [q.x, q.y]);
        const params = { ...op(id), pontos: pts, ...(extra.params ? extra.params(this._pts) : {}) };
        if (extra.cor) params.cor = app.corFrente;
        await app.aplicar(tipo, params, { semCategoria: tipo === "borracha" });
        this._pts = null;
        app.visor.redesenhar();
      },
      desenhar(ctx, v) {
        const o = op(id);
        if (this._pts) {
          ctx.save();
          ctx.globalAlpha = 0.55; ctx.lineCap = "round"; ctx.lineJoin = "round";
          ctx.strokeStyle = extra.cor ? app.corFrente : (tipo === "borracha" ? "#ff4fa0" : "#4fc3ff");
          ctx.lineWidth = Math.max(1, o.tamanho * v.escala);
          tracarTela(ctx, v, this._pts.length === 1 ? [this._pts[0], this._pts[0]] : this._pts);
          ctx.stroke(); ctx.restore();
        }
        circuloCursor(ctx, v, this._mouse, o.tamanho);
        if (extra.desenhar) extra.desenhar(ctx, v, this);
      },
      sair() { this._mouse = null; app.visor.redesenhar(); },
      tecla(ev) {
        const o = op(id);
        if (ev.key === "[" || ev.key === "]") {
          o.tamanho = Math.max(1, Math.round(o.tamanho * (ev.key === "]" ? 1.25 : 0.8)));
          app.salvarOpcoes(); app.renderOpcoes(); app.visor.redesenhar(); return true;
        }
        return false;
      },
    };
  }
  const tam = (p) => ({ chave: "tamanho", tipo: "num", rotulo: "Tamanho ([ ])", min: 1, max: 1000, passo: 1, padrao: p });
  const dur = (p) => ({ chave: "dureza", tipo: "faixa", rotulo: "Dureza", min: 0, max: 1, passo: 0.05, padrao: p });
  const opa = { chave: "opacidade", tipo: "faixa", rotulo: "Opacidade", min: 0.05, max: 1, passo: 0.05, padrao: 1 };
  const patch = { chave: "patch", tipo: "select", rotulo: "Patch", opcoes: [[5, "5"], [7, "7"], [9, "9"], [11, "11"]], padrao: 7 };
  const semente = { chave: "semente", tipo: "num", rotulo: "Semente", min: 0, max: 999999, passo: 1, padrao: 0 };

  F.pincel = ferramentaTraco("pincel", "Pincel (B) — Alt+clique pega a cor", "🖌", "b", "pincel", [tam(8), dur(0.9), opa], {
    cor: true,
    antes: (p, ev) => { if (ev.altKey) { app.contaGotas(p); return false; } },
  });
  F.borracha = ferramentaTraco("borracha", "Borracha que devolve o ORIGINAL (E) — não precisa de categoria", "⌫", "e", "borracha", [tam(20), dur(1), opa]);
  F.corretivo = ferramentaTraco("corretivo", "Pincel corretivo (J): reconstrói o traço a partir do entorno", "✚", "j", "corretivo", [tam(16), dur(0.8), patch, semente]);
  F.carimbo = ferramentaTraco("carimbo", "Carimbo de clonagem (S): Alt+clique define a origem", "⎘", "s", "carimbo", [tam(16), dur(0.8), opa,
    { chave: "alinhado", tipo: "bool", rotulo: "Alinhado", padrao: true }], {
    antes: (p, ev) => {
      if (ev.altKey) { F.carimbo._origem = p; F.carimbo._d = null; app.status("origem do carimbo definida"); app.visor.redesenhar(); return false; }
      if (!F.carimbo._origem) { app.aviso("Alt+clique para definir a origem do carimbo", "erro"); return false; }
      if (!F.carimbo._d || !op("carimbo").alinhado) F.carimbo._d = { x: F.carimbo._origem.x - p.x, y: F.carimbo._origem.y - p.y };
    },
    params: () => ({ dx: F.carimbo._d.x, dy: F.carimbo._d.y }),
    desenhar: (ctx, v, t) => {
      let alvo = null;
      if (t._mouse && F.carimbo._d) alvo = { x: t._mouse.x + F.carimbo._d.x, y: t._mouse.y + F.carimbo._d.y };
      else if (F.carimbo._origem && !F.carimbo._d) alvo = F.carimbo._origem;
      if (!alvo) return;
      const s = v.imgParaTela(alvo.x, alvo.y);
      contorno(ctx, () => { ctx.beginPath(); ctx.moveTo(s.x - 8, s.y); ctx.lineTo(s.x + 8, s.y); ctx.moveTo(s.x, s.y - 8); ctx.lineTo(s.x, s.y + 8); });
    },
  });

  F.balde = {
    nome: "Balde de tinta (G)", icone: "🪣", atalho: "g", cursor: "crosshair",
    campos: [{ chave: "tolerancia", tipo: "num", rotulo: "Tolerância", min: 0, max: 255, passo: 1, padrao: 20 },
      { chave: "contigua", tipo: "bool", rotulo: "Contígua", padrao: true }, opa],
    down(p) { app.aplicar("balde", { ...op("balde"), x: p.x, y: p.y, cor: app.corFrente }); },
  };
  F.contagotas = {
    nome: "Conta-gotas (I): lê a cor do pixel no servidor", icone: "💧", atalho: "i", cursor: "crosshair",
    campos: [{ chave: "raio", tipo: "select", rotulo: "Amostra", opcoes: [[0, "1 px"], [1, "3x3 (mediana)"], [2, "5x5 (mediana)"]], padrao: 1 }],
    down(p) { app.contaGotas(p, op("contagotas").raio); },
  };

  // ------------------------------------------------------------- texto
  const camposTexto = [
    { chave: "texto", tipo: "texto", rotulo: "Texto", padrao: "", largo: true, dado: true },
    { chave: "_gerar", tipo: "botao", rotulo: "🎲", titulo: "Valor aleatório plausível para a categoria ativa", acao: () => app.gerarTexto() },
    { chave: "fonte", tipo: "fonte", rotulo: "Fonte", padrao: "" },
    { chave: "tamanho", tipo: "num", rotulo: "Tam. px", min: 2, max: 1000, passo: 0.25, padrao: 24 },
    { chave: "_cor", tipo: "corfrente", rotulo: "Cor" },
    { chave: "espacamento", tipo: "num", rotulo: "Espaç.", min: -50, max: 200, passo: 0.25, padrao: 0 },
    { chave: "largura", tipo: "num", rotulo: "Largura %", min: 30, max: 300, passo: 1, padrao: 100 },
    { chave: "negrito", tipo: "num", rotulo: "Negrito", min: 0, max: 10, passo: 0.25, padrao: 0 },
    { chave: "rotacao", tipo: "num", rotulo: "Rot.°", min: -180, max: 180, passo: 0.1, padrao: 0 },
    { chave: "desfoque", tipo: "num", rotulo: "Desfoque", min: 0, max: 10, passo: 0.1, padrao: 0 },
    { chave: "opacidade", tipo: "faixa", rotulo: "Opac.", min: 0.05, max: 1, passo: 0.05, padrao: 1 },
    { chave: "antialias", tipo: "bool", rotulo: "Suavizar bordas", padrao: true },
  ];
  function ferramentaTexto(id, tipo, nome, icone, atalho, extraCampos, extra = {}) {
    const t = {
      nome, icone, atalho, cursor: "text", campos: [...camposTexto, ...extraCampos],
      botoes: [...(extra.botoes || []), { rotulo: "Aplicar (Enter)", primario: true, acao: () => t.aplicar() }, { rotulo: "Cancelar (Esc)", acao: () => t.cancelar() }],
      ancora: null,
      params() {
        const o = op(id);
        return { ...o, fonte: o.fonte || app.estado.fonte_padrao, x: t.ancora.x, y: t.ancora.y, cor: app.corFrente };
      },
      pronto() { return t.ancora && (op(id).texto || "").trim() && (!extra.exigeSelecao || app.temSelecao()); },
      previa() { if (t.pronto()) app.previa(tipo, t.params()); else app.limparPrevia(); },
      async aplicar() {
        if (!t.pronto()) { app.aviso(extra.exigeSelecao && !app.temSelecao() ? "selecione o texto antigo primeiro" : "clique na imagem e digite o texto", "erro"); return; }
        if (await app.aplicar(tipo, t.params())) { t.ancora = null; if (extra.depois) extra.depois(); app.visor.redesenhar(); }
      },
      cancelar() { t.ancora = null; app.limparPrevia(); app.visor.redesenhar(); },
      down(p, ev) {
        if (extra.down && extra.down(p, ev) === true) return;
        t._arr = true; t.ancora = p; app.visor.redesenhar(); t.previa();
      },
      move(p) { if (t._arr) { t.ancora = p; app.visor.redesenhar(); } if (extra.move) extra.move(p); },
      up(p) { if (extra.up && extra.up(p) === true) return; if (t._arr) { t._arr = false; t.previa(); } },
      mudouOpcao() { t.previa(); },
      tecla(ev) {
        if (ev.key === "Enter") { t.aplicar(); return true; }
        if (ev.key === "Escape") { t.cancelar(); return true; }
        if (!t.ancora) return false;
        const passo = ev.shiftKey ? 10 : (ev.altKey ? 0.25 : 1);
        const mov = { ArrowLeft: [-passo, 0], ArrowRight: [passo, 0], ArrowUp: [0, -passo], ArrowDown: [0, passo] }[ev.key];
        if (!mov) return false;
        t.ancora = { x: t.ancora.x + mov[0], y: t.ancora.y + mov[1] }; app.visor.redesenhar(); t.previa(); return true;
      },
      desenhar(ctx, v) {
        if (extra.desenhar) extra.desenhar(ctx, v);
        if (!t.ancora) return;
        const s = v.imgParaTela(t.ancora.x, t.ancora.y);
        const tamTela = op(id).tamanho * v.escala;
        contorno(ctx, () => { ctx.beginPath(); ctx.moveTo(s.x - 6, s.y); ctx.lineTo(s.x + Math.max(30, tamTela * 3), s.y); ctx.moveTo(s.x, s.y + 4); ctx.lineTo(s.x, s.y - Math.max(10, tamTela)); });
      },
      ativar() { if (extra.ativar) extra.ativar(); t.previa(); },
      desativar() { t.ancora = null; app.limparPrevia(); },
    };
    return t;
  }
  F.texto = ferramentaTexto("texto", "texto", "Texto (T): clique na linha de base, digite e Enter", "T", "t", []);

  // substituir: arraste sobre o texto antigo; o servidor estima posição/tamanho/cor
  const R = { arr: null };
  async function estimar() {
    if (!app.temSelecao()) return;
    const o = op("substituir");
    const est = await app.estimarTexto(o.fonte || app.estado.fonte_padrao);
    if (!est) return;
    F.substituir.ancora = { x: est.x, y: est.y };
    o.tamanho = est.tamanho;
    app.definirCorFrente(est.cor);
    app.salvarOpcoes(); app.renderOpcoes();
    if (!est.achou_texto) app.aviso("não achei texto na seleção; ajuste posição e tamanho à mão");
    F.substituir.previa();
  }
  F.substituir = ferramentaTexto("substituir", "substituir_texto",
    "Substituir texto (R): arraste sobre o texto antigo, digite o novo e Enter (apaga com preenchimento inteligente)", "T↔", "r",
    [patch, semente], {
      exigeSelecao: true,
      botoes: [{ rotulo: "Re-estimar", titulo: "Estimar de novo posição, tamanho e cor", acao: estimar }],
      down(p, ev) {
        // dentro da seleção: move a âncora; fora: nova seleção retangular
        const bb = app.selBbox();
        if (bb && p.x >= bb[0] && p.x <= bb[2] && p.y >= bb[1] && p.y <= bb[3] && !ev.shiftKey) return false;
        R.arr = { a: p, b: p };
        return true;
      },
      move(p) { if (R.arr) { R.arr.b = p; app.visor.redesenhar(); } },
      up(p) {
        if (!R.arr) return false;
        const { a } = R.arr; R.arr = null;
        if (Math.abs(a.x - p.x) * app.visor.escala < 3 || Math.abs(a.y - p.y) * app.visor.escala < 3) { app.visor.redesenhar(); return true; }
        app.adicionarForma({ tipo: "ret", x0: a.x, y0: a.y, x1: p.x, y1: p.y }, "novo").then(estimar);
        return true;
      },
      desenhar(ctx, v) {
        if (!R.arr) return;
        const a = v.imgParaTela(R.arr.a.x, R.arr.a.y), b = v.imgParaTela(R.arr.b.x, R.arr.b.y);
        contorno(ctx, () => { ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); });
      },
      ativar() { if (app.temSelecao() && !F.substituir.ancora) estimar(); },
      depois() { app.desmarcar(); },
    });
  F.substituir.aoMudarSelecao = () => { if (app.temSelecao()) estimar(); };

  // ------------------------------------------------------------- transformar
  const V = { quad: null };
  function iniciarQuad() {
    const bb = app.selBbox();
    V.quad = bb ? new Quad(bb[0], bb[1], bb[2], bb[3]) : null;
  }
  function previaV() {
    if (!V.quad) { app.limparPrevia(); return; }
    app.previa("transformar", { ...op("transformar"), destino: V.quad.destino(), cor_fundo: app.corFundo });
    app.visor.redesenhar();
  }
  const quadV = interacaoQuad(app, () => V.quad, previaV);
  F.transformar = {
    nome: "Mover/transformar seleção (V): arraste = mover, canto = escalar (Shift = proporção, Ctrl = perspectiva), fora = girar",
    icone: "✥", atalho: "v", cursor: "move",
    campos: [
      { chave: "modo", tipo: "select", rotulo: "Modo", opcoes: [["mover", "mover (copy-move)"], ["duplicar", "duplicar"]], padrao: "duplicar" },
      { chave: "origem", tipo: "select", rotulo: "Buraco na origem", opcoes: [["preencher", "preencher inteligente"], ["cor", "cor de fundo"], ["nada", "deixar"]], padrao: "preencher" },
      { chave: "interpolacao", tipo: "select", rotulo: "Interp.", opcoes: [["bicubica", "bicúbica"], ["bilinear", "bilinear"], ["vizinho", "vizinho"]], padrao: "bicubica" },
      semente,
    ],
    botoes: [{ rotulo: "Aplicar (Enter)", primario: true, acao: () => F.transformar.aplicar() },
      { rotulo: "Reiniciar (Esc)", acao: () => { iniciarQuad(); previaV(); } }],
    async aplicar() {
      if (!V.quad) { app.aviso("selecione algo para mover", "erro"); return; }
      if (await app.aplicar("transformar", { ...op("transformar"), destino: V.quad.destino(), cor_fundo: app.corFundo })) {
        app.desmarcar(); V.quad = null;
      }
    },
    down(p, ev) { if (!V.quad) { app.aviso("faça uma seleção antes", "erro"); return; } quadV.down(p, ev); },
    move(p, ev) { quadV.move(p, ev); },
    up() { quadV.up(); },
    cursorEm: (p) => quadV.cursor(p),
    tecla(ev) {
      if (ev.key === "Enter") { F.transformar.aplicar(); return true; }
      if (ev.key === "Escape") { iniciarQuad(); previaV(); return true; }
      return quadV.tecla(ev);
    },
    desenhar(ctx, v) { if (V.quad) desenharQuad(ctx, v, V.quad); },
    mudouOpcao: previaV,
    ativar() { iniciarQuad(); if (V.quad) previaV(); },
    desativar() { V.quad = null; app.limparPrevia(); },
    aoMudarSelecao() { iniciarQuad(); previaV(); },
  };

  // ------------------------------------------------------------- colar de outro documento
  const C = { quad: null, doador: null, origem: null };
  function previaC() {
    if (!C.quad) return;
    app.previa("colar", { ...op("colar"), doador: C.doador, origem: C.origem, destino: C.quad.destino() });
    app.visor.redesenhar();
  }
  const quadC = interacaoQuad(app, () => C.quad, previaC);
  F.colar = {
    nome: "Colar trecho de outro documento do MESMO split (splicing)", icone: "⧉", atalho: "", cursor: "move", oculta: false,
    campos: [
      { chave: "suavizar_borda", tipo: "num", rotulo: "Suavizar borda px", min: 0, max: 50, passo: 0.5, padrao: 0 },
      { chave: "interpolacao", tipo: "select", rotulo: "Interp.", opcoes: [["bicubica", "bicúbica"], ["bilinear", "bilinear"], ["vizinho", "vizinho"]], padrao: "bicubica" },
      { chave: "limitar_selecao", tipo: "bool", rotulo: "Só dentro da seleção", padrao: false },
    ],
    botoes: [{ rotulo: "Escolher trecho…", acao: () => app.escolherDoador() },
      { rotulo: "Aplicar (Enter)", primario: true, acao: () => F.colar.aplicar() }],
    iniciar(doador, origem) {
      C.doador = doador; C.origem = origem;
      const w = origem.x1 - origem.x0, h = origem.y1 - origem.y0;
      const v = app.visor, r = v.el.getBoundingClientRect();
      const c = v.telaParaImg(r.width / 2, r.height / 2);
      C.quad = new Quad(c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2);
      previaC();
    },
    async aplicar() {
      if (!C.quad) { app.escolherDoador(); return; }
      if (await app.aplicar("colar", { ...op("colar"), doador: C.doador, origem: C.origem, destino: C.quad.destino() })) {
        C.quad = null; app.visor.redesenhar();
      }
    },
    down(p, ev) { if (!C.quad) { app.escolherDoador(); return; } quadC.down(p, ev); },
    move(p, ev) { quadC.move(p, ev); },
    up() { quadC.up(); },
    cursorEm: (p) => quadC.cursor(p),
    tecla(ev) {
      if (ev.key === "Enter") { F.colar.aplicar(); return true; }
      if (ev.key === "Escape") { C.quad = null; app.limparPrevia(); return true; }
      return quadC.tecla(ev);
    },
    desenhar(ctx, v) { if (C.quad) desenharQuad(ctx, v, C.quad); },
    mudouOpcao: previaC,
    ativar(origem) { if (C.quad) previaC(); else if (origem === "usuario") app.escolherDoador(); },
    desativar() { C.quad = null; app.limparPrevia(); },
  };

  return F;
}

export const ORDEM = [
  "mao", "zoom", "|", "ret", "elipse", "laco", "varinha", "|", "pincel", "borracha", "balde", "contagotas", "|",
  "carimbo", "corretivo", "|", "texto", "substituir", "transformar", "colar",
];
