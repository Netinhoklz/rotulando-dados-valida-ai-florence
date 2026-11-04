// Ferramentas do editor. Cada uma descreve a edição em vetores (pontos, retângulos, texto);
// o servidor aplica na resolução original. ``app`` é o editor (editor.js).

const RAIO_ALCA = 7; // px de tela

function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
// o ponto com o instante em que o mouse passou por ele: os traços salvos levam ``tempos`` (ms desde o 1º ponto), e
// a geração automática aprende com eles a velocidade e o ritmo de uma pessoa de verdade (rotulador/gesto.py)
function comTempo(p) { return { ...p, t: performance.now() }; }
function tempos(pts) { return pts.map((q) => Math.round((q.t ?? pts[0].t ?? 0) - (pts[0].t ?? 0))); }

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

// prévia "fantasma": o que a ferramenta de clonagem vai copiar para baixo do cursor
function fantasma(ctx, v, mouse, origem, inicio, o) {
  if (!mouse || !origem || !v.img) return;
  const i = inicio || mouse;
  const esc = (o.escala || 100) / 100, ang = ((o.rotacao || 0) * Math.PI) / 180;
  const c = Math.cos(-ang) / esc, s = Math.sin(-ang) / esc; // A^-1
  const tx = i.x - (c * origem.x - s * origem.y), ty = i.y - (s * origem.x + c * origem.y);
  const e = v.escala, sc = v.imgParaTela(mouse.x, mouse.y);
  ctx.save();
  ctx.beginPath(); ctx.arc(sc.x, sc.y, Math.max(2, (o.tamanho * e) / 2), 0, Math.PI * 2); ctx.clip();
  ctx.globalAlpha = 0.6;
  ctx.imageSmoothingEnabled = e < 1.5;
  ctx.transform(e * c, e * s, -e * s, e * c, e * (tx - v.ox), e * (ty - v.oy));
  ctx.drawImage(o.fonte === "original" && v.orig ? v.orig : v.img, 0, 0);
  ctx.restore();
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
    { rotulo: "Só a tinta", titulo: "Reduz a seleção aos traços de texto/tinta dentro dela", acao: () => app.soTinta() },
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
        if (!this._pts) { this._pts = [comTempo(p)]; this._modo = modoSelecao(ev, op("laco").modo); }
        else if (this._pts.length > 2 && dist(app.visor.imgParaTela(p.x, p.y), app.visor.imgParaTela(this._pts[0].x, this._pts[0].y)) < 8) this.fechar();
        else this._pts.push(comTempo(p));
      } else { this._pts = [comTempo(p)]; this._modo = modoSelecao(ev, op("laco").modo); this._arrastando = true; }
    },
    move(p) {
      this._mouse = p;
      if (this._arrastando && this._pts) {
        const ult = this._pts[this._pts.length - 1];
        if (dist(app.visor.imgParaTela(p.x, p.y), app.visor.imgParaTela(ult.x, ult.y)) >= 2) this._pts.push(comTempo(p));
      }
      if (this._pts) app.visor.redesenhar();
    },
    up() { if (this._arrastando) { this._arrastando = false; this.fechar(); } },
    dbl() { if (op("laco").poligonal) this.fechar(); },
    fechar() {
      const pts = this._pts; this._pts = null;
      if (pts && pts.length >= 3) {
        app.adicionarForma({ tipo: "poligono", pontos: pts.map((q) => [q.x, q.y]), tempos: tempos(pts),
          zoom: Math.round(app.visor.escala * 1000) / 1000 }, this._modo);
      }
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

  F.pincelsel = {
    nome: "Pincel de seleção (Q): pinte a área a selecionar (Alt = tirar da seleção)", icone: "🖍", atalho: "q",
    cursor: "none", grupo: "selecao", campos: [{ chave: "tamanho", tipo: "num", rotulo: "Tamanho ([ ])", min: 1, max: 1000, passo: 1, padrao: 20 }],
    botoes: botoesSelecao,
    down(p, ev) { this._pts = [comTempo(p)]; this._modo = ev.altKey ? "subtrair" : (app.temSelecao() ? "somar" : "novo"); },
    move(p) {
      this._mouse = p;
      if (this._pts) {
        const ult = this._pts[this._pts.length - 1];
        if (dist(app.visor.imgParaTela(p.x, p.y), app.visor.imgParaTela(ult.x, ult.y)) >= 2) this._pts.push(comTempo(p));
      }
      app.visor.redesenhar();
    },
    up() {
      if (!this._pts) return;
      const t = tempos(this._pts);
      const pts = this._pts.map((q) => [q.x, q.y]); this._pts = null;
      app.adicionarForma({ tipo: "traco", pontos: pts, tamanho: op("pincelsel").tamanho, tempos: t,
        zoom: Math.round(app.visor.escala * 1000) / 1000 }, this._modo);
    },
    desenhar(ctx, v) {
      if (this._pts) {
        ctx.save(); ctx.globalAlpha = 0.45; ctx.lineCap = "round"; ctx.lineJoin = "round";
        ctx.strokeStyle = this._modo === "subtrair" ? "#ff4fa0" : "#28a0ff";
        ctx.lineWidth = Math.max(1, op("pincelsel").tamanho * v.escala);
        tracarTela(ctx, v, this._pts.length === 1 ? [this._pts[0], this._pts[0]] : this._pts); ctx.stroke(); ctx.restore();
      }
      circuloCursor(ctx, v, this._mouse, op("pincelsel").tamanho);
    },
    sair() { this._mouse = null; app.visor.redesenhar(); },
    tecla(ev) {
      const o = op("pincelsel");
      if (ev.key === "[" || ev.key === "]") { o.tamanho = Math.max(1, Math.round(o.tamanho * (ev.key === "]" ? 1.25 : 0.8))); app.salvarOpcoes(); app.renderOpcoes(); app.visor.redesenhar(); return true; }
      return false;
    },
  };

  // ------------------------------------------------------------- pintura
  function ferramentaTraco(id, nome, icone, atalho, tipo, campos, extra = {}) {
    return {
      nome, icone, atalho, cursor: "none", campos, botoes: extra.botoes,
      down(p, ev) {
        if (extra.antes && extra.antes(p, ev) === false) return;
        this._pts = [comTempo(p)];
        app.visor.redesenhar();
      },
      move(p) {
        this._mouse = p;
        if (this._pts) {
          const ult = this._pts[this._pts.length - 1];
          if (dist(app.visor.imgParaTela(p.x, p.y), app.visor.imgParaTela(ult.x, ult.y)) >= 1.5) this._pts.push(comTempo(p));
        }
        app.visor.redesenhar();
      },
      async up() {
        if (!this._pts) return;
        const traco = { pts: this._pts, cor: app.corFrente, tamanho: op(id).tamanho };
        this._pts = null;  // um novo traço pode começar enquanto este espera o servidor
        const params = { ...op(id), pontos: traco.pts.map((q) => [q.x, q.y]), tempos: tempos(traco.pts),
          zoom: Math.round(app.visor.escala * 1000) / 1000, ...(extra.params ? extra.params(traco.pts) : {}) };
        if (extra.cor) params.cor = traco.cor;
        (this._pendentes ||= []).push(traco);
        try { await app.aplicar(tipo, params, { semCategoria: tipo === "borracha" }); }
        finally { this._pendentes = this._pendentes.filter((t) => t !== traco); app.visor.redesenhar(); }
      },
      desenhar(ctx, v) {
        const o = op(id);
        const tracos = [...(this._pendentes || []), ...(this._pts ? [{ pts: this._pts, cor: app.corFrente, tamanho: o.tamanho }] : [])];
        for (const t of tracos) {  // pendentes continuam visíveis até o servidor devolver o resultado
          ctx.save();
          ctx.globalAlpha = 0.55; ctx.lineCap = "round"; ctx.lineJoin = "round";
          ctx.strokeStyle = extra.cor ? t.cor : (tipo === "borracha" ? "#ff4fa0" : "#4fc3ff");
          ctx.lineWidth = Math.max(1, t.tamanho * v.escala);
          tracarTela(ctx, v, t.pts.length === 1 ? [t.pts[0], t.pts[0]] : t.pts);
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
  F.borracha = ferramentaTraco("borracha", "Borracha (E): apaga da CAMADA ativa, revelando o que está embaixo (o original)", "⌫", "e", "borracha", [tam(20), dur(1), opa]);
  F.corretivo = ferramentaTraco("corretivo", "Pincel corretivo (J): reconstrói o traço a partir do entorno", "✚", "j", "corretivo", [tam(16), dur(0.8), patch, semente]);
  function ferramentaFonte(id, nome, icone, atalho, tipo, campos) {
    const estado = { origem: null, inicio: null };
    const t = ferramentaTraco(id, nome, icone, atalho, tipo, campos, {
      antes: (p, ev) => {
        if (ev.altKey) {
          estado.origem = p; estado.inicio = null;
          app.status("origem definida: agora pinte onde quer copiar"); app.visor.redesenhar(); return false;
        }
        if (!estado.origem) { app.aviso("Alt+clique para definir a ORIGEM (de onde copiar)", "erro"); return false; }
        if (!estado.inicio || !op(id).alinhado) estado.inicio = p;
        if (op(id).fonte === "original" && !app.visor.orig) app.carregarOriginal();
      },
      params: () => ({ origem_x: estado.origem.x, origem_y: estado.origem.y, inicio_x: estado.inicio.x, inicio_y: estado.inicio.y }),
      desenhar: (ctx, v, tr) => {
        if (estado.origem) {
          const esc = (op(id).escala || 100) / 100, ang = ((op(id).rotacao || 0) * Math.PI) / 180;
          let alvo = estado.origem;
          if (tr._mouse && estado.inicio) {
            const dx = tr._mouse.x - estado.inicio.x, dy = tr._mouse.y - estado.inicio.y;
            alvo = { x: estado.origem.x + esc * (Math.cos(ang) * dx - Math.sin(ang) * dy),
              y: estado.origem.y + esc * (Math.sin(ang) * dx + Math.cos(ang) * dy) };
          }
          const sc = v.imgParaTela(alvo.x, alvo.y);
          contorno(ctx, () => { ctx.beginPath(); ctx.moveTo(sc.x - 9, sc.y); ctx.lineTo(sc.x + 9, sc.y); ctx.moveTo(sc.x, sc.y - 9); ctx.lineTo(sc.x, sc.y + 9); });
          if (!tr._pts && op(id).fantasma !== false) fantasma(ctx, v, tr._mouse, estado.origem, estado.inicio, op(id));
        }
      },
    });
    t.estado = estado;
    return t;
  }
  const camposFonte = [
    { chave: "fonte", tipo: "select", rotulo: "Amostrar de", opcoes: [["atual", "imagem atual"], ["original", "ORIGINAL"]], padrao: "atual" },
    { chave: "escala", tipo: "num", rotulo: "Escala %", min: 10, max: 400, passo: 1, padrao: 100 },
    { chave: "rotacao", tipo: "num", rotulo: "Rot.°", min: -180, max: 180, passo: 0.5, padrao: 0 },
    { chave: "alinhado", tipo: "bool", rotulo: "Alinhado", padrao: true },
    { chave: "fantasma", tipo: "bool", rotulo: "Prévia sob o cursor", padrao: true },
  ];
  F.carimbo = ferramentaFonte("carimbo", "Carimbo de clonagem (S): Alt+clique na ORIGEM, depois pinte. Prévia sob o cursor.", "⎘", "s",
    "carimbo", [tam(16), dur(0.8), opa,
      { chave: "modo_mescla", tipo: "select", rotulo: "Mescla", opcoes: [["normal", "normal"], ["escurecer", "escurecer"], ["clarear", "clarear"], ["multiplicar", "multiplicar"], ["tela", "tela"]], padrao: "normal" },
      ...camposFonte]);
  F.recuperacao = ferramentaFonte("recuperacao", "Pincel de recuperação (Shift+J): como o carimbo, mas casa cor e luz com o destino",
    "🩹", "J", "recuperacao", [tam(16), dur(1), ...camposFonte]);
  F.retoque = ferramentaTraco("retoque", "Pincel de retoque (O): desfocar, nitidez, borrar, clarear, escurecer, saturar", "💧", "o", "retoque", [
    { chave: "modo", tipo: "select", rotulo: "Modo", opcoes: [["desfocar", "desfocar"], ["nitidez", "nitidez"], ["borrar", "borrar (dedo)"], ["clarear", "clarear (subexposição)"], ["escurecer", "escurecer (superexposição)"], ["saturar", "saturar"], ["dessaturar", "dessaturar"]], padrao: "desfocar" },
    { chave: "forca", tipo: "faixa", rotulo: "Força", min: 0.05, max: 1, passo: 0.05, padrao: 0.5 }, tam(24), dur(0.6)]);

  F.balde = {
    nome: "Balde de tinta (G)", icone: "🪣", atalho: "g", cursor: "crosshair",
    campos: [{ chave: "tolerancia", tipo: "num", rotulo: "Tolerância", min: 0, max: 255, passo: 1, padrao: 20 },
      { chave: "contigua", tipo: "bool", rotulo: "Contígua", padrao: true }, opa],
    down(p) { app.aplicar("balde", { ...op("balde"), x: p.x, y: p.y, cor: app.corFrente }); },
  };
  F.contagotas = {
    nome: "Conta-gotas (I): lê a cor do pixel no servidor", icone: "🎯", atalho: "i", cursor: "crosshair",
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
      botoes: [...(extra.botoes || []),
        { rotulo: "🔍 Qual é a fonte?", titulo: "Compara o texto selecionado com todas as fontes instaladas e mostra as mais parecidas",
          acao: () => app.identificarFonte() },
        { rotulo: "Aplicar (Enter)", primario: true, acao: () => t.aplicar() }, { rotulo: "Cancelar (Esc)", acao: () => t.cancelar() }],
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

  // ------------------------------------------------------------- remendo
  const RM = { a: null, d: { x: 0, y: 0 } };
  F.remendo = {
    nome: "Remendo (Y): com uma seleção feita, arraste-a até uma área boa; a seleção recebe aquela textura, mesclada",
    icone: "🧩", atalho: "y", cursor: "move",
    campos: [
      { chave: "modo", tipo: "select", rotulo: "Modo", opcoes: [["origem", "origem: preencher a seleção"], ["destino", "destino: levar a seleção para lá"]], padrao: "origem" },
      { chave: "mesclar", tipo: "bool", rotulo: "Mesclar cor/luz (Poisson)", padrao: true },
    ],
    down(p) { if (!app.temSelecao()) { app.aviso("faça uma seleção da área a corrigir antes", "erro"); return; } RM.a = p; RM.d = { x: 0, y: 0 }; },
    move(p) {
      if (!RM.a) return;
      RM.d = { x: Math.round(p.x - RM.a.x), y: Math.round(p.y - RM.a.y) };
      if (RM.d.x || RM.d.y) app.previa("remendo", { ...op("remendo"), dx: RM.d.x, dy: RM.d.y });
      app.visor.redesenhar();
    },
    async up() {
      if (!RM.a) return;
      const d = RM.d; RM.a = null;
      if (!d.x && !d.y) { app.visor.redesenhar(); return; }
      if (await app.aplicar("remendo", { ...op("remendo"), dx: d.x, dy: d.y })) app.desmarcar();
      app.visor.redesenhar();
    },
    desenhar(ctx, v) {
      const bb = app.selBbox();
      if (!bb || !RM.a) return;
      const a = v.imgParaTela(bb[0] + RM.d.x, bb[1] + RM.d.y), b = v.imgParaTela(bb[2] + RM.d.x, bb[3] + RM.d.y);
      contorno(ctx, () => { ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); });
    },
    desativar() { RM.a = null; app.limparPrevia(); },
  };

  // ------------------------------------------------------------- formas
  F.forma = {
    nome: "Formas (U): arraste para desenhar; Shift = quadrado/círculo/linha reta", icone: "▭", atalho: "u", cursor: "crosshair",
    campos: [
      { chave: "tipo", tipo: "select", rotulo: "Forma", opcoes: [["ret", "retângulo"], ["elipse", "elipse"], ["linha", "linha"]], padrao: "ret" },
      { chave: "preencher", tipo: "bool", rotulo: "Preencher (cor de frente)", padrao: true },
      { chave: "_cor", tipo: "corfrente", rotulo: "Preench." },
      { chave: "espessura", tipo: "num", rotulo: "Contorno px", min: 0, max: 200, passo: 0.5, padrao: 0 },
      { chave: "cor_contorno", tipo: "cor", rotulo: "Cor contorno", padrao: "#000000" },
      opa,
    ],
    down(p) { this._a = p; this._b = p; },
    move(p, ev) {
      if (!this._a) return;
      let b = p;
      if (ev.shiftKey) {
        const dx = p.x - this._a.x, dy = p.y - this._a.y;
        if (op("forma").tipo === "linha") b = Math.abs(dx) > Math.abs(dy) ? { x: p.x, y: this._a.y } : { x: this._a.x, y: p.y };
        else { const m = Math.max(Math.abs(dx), Math.abs(dy)); b = { x: this._a.x + Math.sign(dx || 1) * m, y: this._a.y + Math.sign(dy || 1) * m }; }
      }
      this._b = b; app.visor.redesenhar();
    },
    async up() {
      if (!this._a) return;
      const a = this._a, b = this._b; this._a = null;
      if (Math.hypot(a.x - b.x, a.y - b.y) * app.visor.escala < 3) { app.visor.redesenhar(); return; }
      const o = op("forma");
      if (o.tipo === "linha" && !(o.espessura > 0)) { app.aviso("a linha precisa de 'Contorno px' maior que zero", "erro"); return; }
      await app.aplicar("forma", { ...o, x0: a.x, y0: a.y, x1: b.x, y1: b.y, cor: app.corFrente });
      app.visor.redesenhar();
    },
    desenhar(ctx, v) {
      if (!this._a) return;
      const a = v.imgParaTela(this._a.x, this._a.y), b = v.imgParaTela(this._b.x, this._b.y), t = op("forma").tipo;
      contorno(ctx, () => {
        ctx.beginPath();
        if (t === "linha") { ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); }
        else if (t === "ret") ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y);
        else ctx.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2, Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2, 0, 0, Math.PI * 2);
      });
    },
  };

  // ------------------------------------------------------------- mover / puxar
  // Com uma seleção, arrastar PUXA os pixels: eles seguem o mouse na hora e, ao soltar, o servidor mostra o resultado de
  // verdade (com o buraco já preenchido). Alt no começo do arraste = duplicar. Cantos escalam (Shift = proporção, Ctrl =
  // perspectiva); fora da caixa, gira. Enter (ou ✓) aplica, Esc desfaz o arraste. Como no Photoshop, trocar de
  // ferramenta, desmarcar, salvar ou trocar de imagem também aplicam o que está solto.
  const V = { quad: null, base: null, sujo: false, modo: null, recorte: false, arrastando: false, flut: null, buraco: null };
  function iniciarQuad() {
    const bb = app.selBbox();
    Object.assign(V, { quad: bb ? new Quad(bb[0], bb[1], bb[2], bb[3]) : null, base: bb, sujo: false, modo: null,
      recorte: false, arrastando: false, flut: null, buraco: null });
    app.visor.esconderSelecao = false;
  }
  const modoV = () => V.modo || op("transformar").acao || "mover";
  function paramsV() {
    const o = op("transformar");
    return { modo: modoV(), origem: o.origem, interpolacao: o.interpolacao, semente: o.semente,
      destino: V.quad.destino(), cor_fundo: app.corFundo };
  }
  function previaV() {
    app.limparPrevia();
    app.visor.esconderSelecao = Boolean(V.quad && (V.sujo || V.recorte));
    if (V.quad && V.sujo) app.previa("transformar", paramsV());
    app.visor.redesenhar();
  }
  const quadV = interacaoQuad(app, () => V.quad, () => { V.sujo = true; previaV(); });
  // o buraco que a seleção deixa (listrado = será preenchido pelo papel em volta; ou a cor de fundo)
  function buracoV() {
    if (V.buraco) return V.buraco;
    const s = app.visor.selecao, origem = op("transformar").origem;
    if (!s?.alfa || origem === "nada") return null;
    const c = document.createElement("canvas");
    c.width = s.alfa.width; c.height = s.alfa.height;
    const g = c.getContext("2d");
    if (origem === "cor") g.fillStyle = app.corFundo;
    else {
      const t = document.createElement("canvas");
      t.width = t.height = 8;
      const gt = t.getContext("2d");
      gt.fillStyle = "#eceef1"; gt.fillRect(0, 0, 8, 8);
      gt.strokeStyle = "#b8c0ca"; gt.lineWidth = 1.5; gt.beginPath(); gt.moveTo(-1, 9); gt.lineTo(9, -1); gt.stroke();
      g.fillStyle = g.createPattern(t, "repeat");
    }
    g.fillRect(0, 0, c.width, c.height);
    g.globalCompositeOperation = "destination-in";
    g.drawImage(s.alfa, 0, 0);
    return (V.buraco = c);
  }
  // o trecho solto, desenhado no navegador enquanto o servidor não devolve a prévia (perspectiva: só a prévia)
  function desenharSolto(ctx, v) {
    if (!V.base) return;
    const [x0, y0, x1, y1] = V.base, w0 = x1 - x0, h0 = y1 - y0, e = v.escala;
    V.flut ||= app.visor.recortarSelecao();
    ctx.save();
    ctx.transform(e, 0, 0, e, -v.ox * e, -v.oy * e);  // daqui em diante, coordenadas da imagem
    ctx.imageSmoothingEnabled = e < 1.5;
    if (modoV() === "mover") { const b = buracoV(); if (b) ctx.drawImage(b, x0, y0); }
    if (V.flut && !V.quad.livre) {
      const q = V.quad;
      ctx.translate(q.cx, q.cy); ctx.rotate(q.ang); ctx.scale(q.w / w0, q.h / h0);
      ctx.drawImage(V.flut, -w0 / 2, -h0 / 2);
    }
    ctx.restore();
  }
  F.transformar = {
    nome: "Mover / puxar (V): arraste a seleção para puxar os pixels (Alt = duplicar); cantos escalam, fora gira; sem seleção, move a CAMADA ativa",
    icone: "✥", atalho: "v", cursor: "move",
    campos: [
      { chave: "acao", tipo: "select", rotulo: "Ao arrastar", opcoes: [["mover", "puxar (o buraco é preenchido)"], ["duplicar", "duplicar (ou Alt+arrastar)"]], padrao: "mover" },
      { chave: "origem", tipo: "select", rotulo: "Buraco", opcoes: [["preencher", "preencher com o papel em volta"], ["cor", "cor de fundo"], ["nada", "deixar como está"]], padrao: "preencher" },
      { chave: "interpolacao", tipo: "select", rotulo: "Interp.", opcoes: [["bicubica", "bicúbica"], ["bilinear", "bilinear"], ["vizinho", "vizinho"]], padrao: "bicubica" },
      semente,
    ],
    botoes: [{ rotulo: "Aplicar (Enter)", primario: true, acao: () => F.transformar.confirmar(true) },
      { rotulo: "Cancelar (Esc)", acao: () => F.transformar.cancelar() }],
    pendente: () => Boolean(V.quad && (V.sujo || V.recorte) && !V.aplicando),
    solto: () => (V.quad ? { modo: modoV(), recorte: V.recorte, sujo: V.sujo, arrastando: V.arrastando } : null),
    ancora() {
      if (!V.quad) return null;
      const c = V.quad.cantos();
      return [Math.min(...c.map((p) => p.x)), Math.min(...c.map((p) => p.y)), Math.max(...c.map((p) => p.x)), Math.max(...c.map((p) => p.y))];
    },
    // deixa a seleção solta para puxar (Ctrl+X = recortar; Ctrl+V no mesmo documento = duplicar, um pouco deslocada)
    soltar({ recorte = false, modo = null, deslocar = 0 } = {}) {
      iniciarQuad();
      if (!V.quad) return false;
      V.recorte = recorte; V.modo = modo;
      if (deslocar) { V.quad.mover(deslocar, deslocar); V.sujo = true; }
      previaV();
      return true;
    },
    trocarModo(modo) { V.modo = modo; V.buraco = null; previaV(); },
    async confirmar(explicito = false) {
      if (V.aplicando) return false;
      if (!V.quad) { if (explicito) app.aviso("selecione algo para mover", "erro"); return false; }
      if (!V.sujo && !V.recorte) {
        if (explicito) app.aviso("arraste o trecho para outro lugar antes de aplicar (Esc cancela)", "erro");
        return false;
      }
      V.aplicando = true;
      try {
        if (!V.sujo) {  // recortou e não levou a lugar nenhum: o trecho some (apagar com preenchimento)
          const ok = await app.apagarComPreenchimento();
          if (ok) { V.recorte = false; app.visor.esconderSelecao = false; app.visor.redesenhar(); }
          return ok;
        }
        app.ajustarCategoria("copiar e colar");
        const ok = await app.aplicar("transformar", paramsV(),
          { msg: modoV() === "mover" ? "puxando… (preenchendo o buraco)" : "duplicando…" });
        if (ok) { V.sujo = V.recorte = false; app.desmarcar(); }
        return ok;
      } finally { V.aplicando = false; }
    },
    cancelar() {
      const tinha = V.sujo || V.recorte;
      iniciarQuad(); previaV();
      if (tinha) app.status("puxar cancelado: o trecho voltou ao lugar");
    },
    down(p, ev) {
      if (V.quad) {
        if (!V.sujo && ev.altKey) { V.modo = "duplicar"; V.buraco = null; }
        V.arrastando = true;
        quadV.down(p, ev);
        return;
      }
      const at = app.camadaAtiva();
      if (!at || at.vazia) { app.aviso("faça uma seleção (ou escolha uma camada com conteúdo) para mover", "erro"); return; }
      V.cam = { a: p, d: { x: 0, y: 0 }, bbox: at.bbox, id: at.id };
    },
    move(p, ev) {
      if (V.cam) { V.cam.d = { x: Math.round(p.x - V.cam.a.x), y: Math.round(p.y - V.cam.a.y) }; app.visor.redesenhar(); return; }
      quadV.move(p, ev);
    },
    up() {
      if (V.cam) {
        const c = V.cam; V.cam = null;
        if (c.d.x || c.d.y) app.acaoCamada("mover", { camada: c.id, dx: c.d.x, dy: c.d.y });
        app.visor.redesenhar(); return;
      }
      V.arrastando = false;
      quadV.up();
      app.visor.redesenhar();
    },
    cursorEm: (p) => quadV.cursor(p),
    tecla(ev) {
      if (ev.key === "Enter") { F.transformar.confirmar(true); return true; }
      if (ev.key === "Escape" && V.quad) { F.transformar.cancelar(); return true; }
      if (!V.quad) {  // sem seleção: setas empurram a camada ativa
        const at = app.camadaAtiva(), passo = ev.shiftKey ? 10 : 1;
        const mov = { ArrowLeft: [-passo, 0], ArrowRight: [passo, 0], ArrowUp: [0, -passo], ArrowDown: [0, passo] }[ev.key];
        if (mov && at && !at.vazia) { app.acaoCamada("mover", { camada: at.id, dx: mov[0], dy: mov[1] }); return true; }
        return false;
      }
      return quadV.tecla(ev);
    },
    desenhar(ctx, v) {
      if (V.quad) {
        if ((V.sujo || V.recorte) && !v.previa) desenharSolto(ctx, v);
        desenharQuad(ctx, v, V.quad);
      }
      if (V.cam && V.cam.bbox) {
        const [x0, y0, x1, y1] = V.cam.bbox, d = V.cam.d;
        const a = v.imgParaTela(x0 + d.x, y0 + d.y), b = v.imgParaTela(x1 + d.x, y1 + d.y);
        contorno(ctx, () => { ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); });
      }
    },
    mudouOpcao() { V.buraco = null; previaV(); },
    ativar(origem) { if (!V.quad || origem === "documento") iniciarQuad(); previaV(); },
    desativar() { V.quad = null; V.sujo = V.recorte = V.arrastando = false; app.visor.esconderSelecao = false; app.limparPrevia(); },
    aoMudarSelecao() { iniciarQuad(); previaV(); },
  };

  // ------------------------------------------------------------- colar (Ctrl+V de outro documento ou o diálogo do doador)
  const C = { quad: null, doador: null, origem: null, selecao: null, pre: null };
  function paramsC() {
    return { ...op("colar"), doador: C.doador, origem: C.origem, destino: C.quad.destino(),
      ...(C.selecao ? { selecao_doador: C.selecao } : {}) };
  }
  function previaC() {
    if (!C.quad) return;
    app.limparPrevia();
    app.previa("colar", paramsC());
    app.visor.redesenhar();
  }
  const quadC = interacaoQuad(app, () => C.quad, previaC);
  F.colar = {
    nome: "Colar de outro documento do MESMO split (Ctrl+V depois de copiar em outra imagem, ou escolha o trecho)", icone: "⧉", atalho: "", cursor: "move",
    campos: [
      { chave: "suavizar_borda", tipo: "num", rotulo: "Suavizar borda px", min: 0, max: 50, passo: 0.5, padrao: 0 },
      { chave: "interpolacao", tipo: "select", rotulo: "Interp.", opcoes: [["bicubica", "bicúbica"], ["bilinear", "bilinear"], ["vizinho", "vizinho"]], padrao: "bicubica" },
      { chave: "limitar_selecao", tipo: "bool", rotulo: "Só dentro da seleção", padrao: false },
    ],
    botoes: [{ rotulo: "Escolher trecho…", acao: () => app.escolherDoador() },
      { rotulo: "Aplicar (Enter)", primario: true, acao: () => F.colar.confirmar(true) },
      { rotulo: "Cancelar (Esc)", acao: () => F.colar.cancelar() }],
    // trecho do doador (retângulo ``origem``; com ``selecao``, só o formato copiado). ``pre`` = os pixels, para a
    // prévia na hora; ``emLugar`` = nas mesmas coordenadas (se couber), senão no meio da vista
    iniciar(doador, origem, { selecao = null, pre = null, emLugar = false } = {}) {
      Object.assign(C, { doador, origem, selecao, pre });
      const w = origem.x1 - origem.x0, h = origem.y1 - origem.y0;
      if (emLugar && app.sessao && origem.x1 <= app.sessao.largura && origem.y1 <= app.sessao.altura) {
        C.quad = new Quad(origem.x0, origem.y0, origem.x1, origem.y1);
      } else {
        const v = app.visor, r = v.el.getBoundingClientRect();
        const c = v.telaParaImg(r.width / 2, r.height / 2);
        C.quad = new Quad(c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2);
      }
      previaC();
    },
    pendente: () => Boolean(C.quad && !C.aplicando),
    solto: () => (C.quad ? { modo: "colar", doador: C.doador, arrastando: C.arrastando } : null),
    ancora() {
      if (!C.quad) return null;
      const c = C.quad.cantos();
      return [Math.min(...c.map((p) => p.x)), Math.min(...c.map((p) => p.y)), Math.max(...c.map((p) => p.x)), Math.max(...c.map((p) => p.y))];
    },
    async confirmar(explicito = false) {
      if (C.aplicando) return false;
      if (!C.quad) { if (explicito) app.escolherDoador(); return false; }
      app.ajustarCategoria(C.doador === app.sessao?.id ? "copiar e colar" : "pegar de outro documento");
      C.aplicando = true;
      try {
        const ok = await app.aplicar("colar", paramsC(), { msg: "colando…" });
        if (ok) { C.quad = null; C.pre = null; app.visor.redesenhar(); }
        return ok;
      } finally { C.aplicando = false; }
    },
    cancelar() { C.quad = null; C.pre = null; app.limparPrevia(); app.visor.redesenhar(); },
    down(p, ev) { if (!C.quad) { app.escolherDoador(); return; } C.arrastando = true; quadC.down(p, ev); },
    move(p, ev) { quadC.move(p, ev); },
    up() { C.arrastando = false; quadC.up(); app.visor.redesenhar(); },
    cursorEm: (p) => quadC.cursor(p),
    tecla(ev) {
      if (ev.key === "Enter") { F.colar.confirmar(true); return true; }
      if (ev.key === "Escape" && C.quad) { F.colar.cancelar(); return true; }
      return quadC.tecla(ev);
    },
    desenhar(ctx, v) {
      if (!C.quad) return;
      if (C.pre && !v.previa && !C.quad.livre) {  // os pixels copiados seguem o mouse; a prévia do servidor vem depois
        const e = v.escala, q = C.quad, w0 = C.pre.width, h0 = C.pre.height;
        ctx.save();
        ctx.transform(e, 0, 0, e, -v.ox * e, -v.oy * e);
        ctx.imageSmoothingEnabled = e < 1.5;
        ctx.translate(q.cx, q.cy); ctx.rotate(q.ang); ctx.scale(q.w / w0, q.h / h0);
        ctx.drawImage(C.pre, -w0 / 2, -h0 / 2);
        ctx.restore();
      }
      desenharQuad(ctx, v, C.quad);
    },
    mudouOpcao: previaC,
    ativar(origem) { if (C.quad) previaC(); else if (origem === "usuario") app.escolherDoador(); },
    desativar() { C.quad = null; C.pre = null; app.limparPrevia(); },
  };

  return F;
}

// a caixa de ferramentas em grupos, como no Photoshop: o botão mostra a última usada do grupo; clique com o botão
// direito (ou segure) para ver as outras
export const GRUPOS = [
  ["transformar", "colar"], ["ret", "elipse"], ["laco"], ["varinha", "pincelsel"], ["contagotas"], "|",
  ["corretivo", "recuperacao", "remendo"], ["pincel"], ["carimbo"], ["borracha"], ["balde"], ["retoque"], "|",
  ["texto", "substituir"], ["forma"], "|", ["mao"], ["zoom"],
];
export const ICONES = {
  transformar: "mover", colar: "colar_doc", ret: "ret", elipse: "elipse", laco: "laco", varinha: "varinha",
  pincelsel: "pincelsel", contagotas: "contagotas", corretivo: "corretivo", recuperacao: "recuperacao", remendo: "remendo",
  pincel: "pincel", carimbo: "carimbo", borracha: "borracha", balde: "balde", retoque: "retoque", texto: "texto",
  substituir: "substituir", forma: "forma", mao: "mao", zoom: "zoom",
};
export const NOMES = {
  transformar: "Mover / puxar", colar: "Colar de outro documento", ret: "Seleção retangular", elipse: "Seleção elíptica",
  laco: "Laço", varinha: "Varinha mágica", pincelsel: "Pincel de seleção", contagotas: "Conta-gotas",
  corretivo: "Pincel corretivo", recuperacao: "Pincel de recuperação", remendo: "Remendo", pincel: "Pincel",
  carimbo: "Carimbo de clonagem", borracha: "Borracha", balde: "Balde de tinta", retoque: "Retoque", texto: "Texto",
  substituir: "Substituir texto", forma: "Formas", mao: "Mão", zoom: "Zoom",
};

// dicas da barra de status (como no Graphite): tecla (k), gesto do mouse (m) e o que acontece (t)
const h = (k, m, t) => ({ k, m, t });
const DICA_SEL = [h("", "arrastar", "selecionar"), h("Shift", "arrastar", "somar"), h("Alt", "arrastar", "subtrair")];
const DICA_TAM = h("[ ]", "", "tamanho");
const DICA_FONTE = [h("Alt", "clique", "origem"), h("", "arrastar", "pintar a cópia"), DICA_TAM];
export const DICAS = {
  mao: [h("", "arrastar", "mover a vista"), h("", "roda", "zoom")],
  zoom: [h("", "clique", "aproximar"), h("Alt", "clique", "afastar"), h("0", "", "caber na tela"), h("1", "", "100%")],
  ret: DICA_SEL, elipse: DICA_SEL,
  laco: [h("", "arrastar", "contornar"), h("Shift", "", "somar"), h("Alt", "", "subtrair")],
  varinha: [h("", "clique", "cor parecida"), h("Shift", "clique", "somar"), h("Alt", "clique", "subtrair")],
  pincelsel: [h("", "arrastar", "pintar a seleção"), h("Alt", "arrastar", "tirar"), DICA_TAM],
  pincel: [h("", "arrastar", "pintar"), h("Alt", "clique", "pegar a cor"), DICA_TAM],
  borracha: [h("", "arrastar", "devolver o original"), DICA_TAM],
  corretivo: [h("", "arrastar", "sumir com o que pintar"), DICA_TAM],
  carimbo: DICA_FONTE, recuperacao: DICA_FONTE,
  remendo: [h("", "arrastar", "leve a seleção até papel limpo")],
  retoque: [h("", "arrastar", "retocar"), DICA_TAM],
  balde: [h("", "clique", "preencher")],
  contagotas: [h("", "clique", "pegar a cor")],
  forma: [h("", "arrastar", "desenhar"), h("Shift", "arrastar", "quadrado / linha reta")],
  texto: [h("", "clique", "linha de base"), h("Enter", "", "aplicar"), h("Esc", "", "cancelar"), h("setas", "", "ajuste fino")],
  substituir: [h("", "arrastar", "sobre o texto antigo"), h("Enter", "", "aplicar"), h("Esc", "", "cancelar")],
  transformar: [h("", "arrastar", "puxar"), h("Alt", "arrastar", "duplicar"), h("", "arrastar canto", "escala"),
    h("", "arrastar fora", "girar"), h("setas", "", "1 px"), h("Enter", "", "aplicar"), h("Esc", "", "cancelar")],
  transformar_sem: [h("", "arrastar", "mover a camada ativa"), h("setas", "", "empurrar 1 px (Shift 10)")],
  colar: [h("", "arrastar", "posicionar"), h("", "arrastar canto", "escala"), h("", "arrastar fora", "girar"),
    h("Enter", "", "aplicar"), h("Esc", "", "cancelar")],
  com_selecao: [h("Ctrl", "arrastar", "puxar"), h("Del", "", "apagar com preenchimento"),
    h("Ctrl+C · Ctrl+X · Ctrl+V", "", "copiar · recortar · colar")],
  sempre: [h("Espaço", "arrastar", "mover a vista")],
};
