// Visor: mostra a imagem com zoom/pan e as camadas de exibição (prévia, seleção, máscara).
// Coordenadas da imagem são contínuas: o pixel (i, j) cobre [j, j+1) x [i, i+1).

export class Visor {
  constructor(el, canvas) {
    this.el = el;
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.img = null;          // canvas em resolução cheia com o estado atual
    this.orig = null;         // bitmap do original (carregado sob demanda)
    this.escala = 1;
    this.ox = 0; this.oy = 0; // coordenada da imagem no canto sup-esq da tela
    this.previa = null;       // {bitmap, x0, y0}
    this.selecao = null;      // {canvas, x0, y0}
    this.mascara = null;      // bitmap RGBA
    this.mostrarOriginal = false;
    this.mostrarMascara = false;
    this.extra = null;        // (ctx, visor) => desenho da ferramenta, em px de tela
    this.aoMudar = null;      // callback de zoom
    this._pendente = false;
    new ResizeObserver(() => this._redimensionar()).observe(el);
    this._redimensionar();
  }

  get largura() { return this.img ? this.img.width : 0; }
  get altura() { return this.img ? this.img.height : 0; }

  _redimensionar() {
    const dpr = window.devicePixelRatio || 1;
    const r = this.el.getBoundingClientRect();
    this.canvas.width = Math.max(1, Math.round(r.width * dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * dpr));
    this.redesenhar();
  }

  definirImagem(bitmap) {
    const c = document.createElement("canvas");
    c.width = bitmap.width; c.height = bitmap.height;
    c.getContext("2d").drawImage(bitmap, 0, 0);
    this.img = c;
    this.orig = null;
    this.previa = this.selecao = this.mascara = null;
    this.ajustar();
  }

  aplicarRecorte(bitmap, x0, y0) {
    const ctx = this.img.getContext("2d");
    ctx.clearRect(x0, y0, bitmap.width, bitmap.height);
    ctx.drawImage(bitmap, x0, y0);
    this.redesenhar();
  }

  // ------------------------------------------------------------- coordenadas
  telaParaImg(sx, sy) { return { x: sx / this.escala + this.ox, y: sy / this.escala + this.oy }; }
  imgParaTela(x, y) { return { x: (x - this.ox) * this.escala, y: (y - this.oy) * this.escala }; }
  doEvento(ev) {
    const r = this.canvas.getBoundingClientRect();
    return this.telaParaImg(ev.clientX - r.left, ev.clientY - r.top);
  }
  telaDoEvento(ev) {
    const r = this.canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  ajustar() {
    if (!this.img) return;
    const r = this.el.getBoundingClientRect();
    const e = Math.min((r.width - 20) / this.img.width, (r.height - 20) / this.img.height);
    this.escala = Math.max(0.01, e);
    this.ox = -(r.width / this.escala - this.img.width) / 2;
    this.oy = -(r.height / this.escala - this.img.height) / 2;
    this._mudou();
  }

  zoom100(sx, sy) { this.zoomEm(1 / this.escala, sx, sy); }

  zoomEm(fator, sx, sy) {
    if (!this.img) return;
    if (sx === undefined) {
      const r = this.el.getBoundingClientRect();
      sx = r.width / 2; sy = r.height / 2;
    }
    const p = this.telaParaImg(sx, sy);
    this.escala = Math.min(64, Math.max(0.02, this.escala * fator));
    this.ox = p.x - sx / this.escala;
    this.oy = p.y - sy / this.escala;
    this._mudou();
  }

  mover(dxTela, dyTela) {
    this.ox -= dxTela / this.escala;
    this.oy -= dyTela / this.escala;
    this._mudou();
  }

  _mudou() { this.redesenhar(); if (this.aoMudar) this.aoMudar(); }

  // ------------------------------------------------------------- camadas
  definirPrevia(bitmap, x0, y0) { this.previa = bitmap ? { bitmap, x0, y0 } : null; this.redesenhar(); }

  // máscara da seleção (PNG em tons de cinza) -> interior azulado + contorno
  definirSelecao(bitmap, x0, y0) {
    if (!bitmap) { this.selecao = null; this.redesenhar(); return; }
    const w = bitmap.width, h = bitmap.height;
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bitmap, 0, 0);
    const d = ctx.getImageData(0, 0, w, h);
    const px = d.data;
    const a = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) a[i] = px[i * 4];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x, k = i * 4, v = a[i];
        if (!v) { px[k + 3] = 0; continue; }
        const borda = x === 0 || y === 0 || x === w - 1 || y === h - 1 ||
          !a[i - 1] || !a[i + 1] || !a[i - w] || !a[i + w];
        if (borda) { px[k] = 0; px[k + 1] = 230; px[k + 2] = 255; px[k + 3] = 255; }
        else { px[k] = 40; px[k + 1] = 140; px[k + 2] = 255; px[k + 3] = Math.round(v * 0.22); }
      }
    }
    ctx.putImageData(d, 0, 0);
    this.selecao = { canvas: c, x0, y0 };
    this.redesenhar();
  }

  definirMascara(bitmap) { this.mascara = bitmap; this.redesenhar(); }

  redesenhar() {
    if (this._pendente) return;
    this._pendente = true;
    requestAnimationFrame(() => { this._pendente = false; this._desenhar(); });
  }

  _desenhar() {
    const ctx = this.ctx, dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (!this.img) return;
    const e = this.escala * dpr;
    ctx.setTransform(e, 0, 0, e, -this.ox * e, -this.oy * e);
    ctx.imageSmoothingEnabled = this.escala < 1.5;
    ctx.imageSmoothingQuality = "high";
    ctx.fillStyle = "#fff";
    const base = this.mostrarOriginal && this.orig ? this.orig : this.img;
    ctx.drawImage(base, 0, 0);
    if (!this.mostrarOriginal) {
      if (this.previa) ctx.drawImage(this.previa.bitmap, this.previa.x0, this.previa.y0);
      if (this.mostrarMascara && this.mascara) ctx.drawImage(this.mascara, 0, 0);
    }
    if (this.selecao) {
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(this.selecao.canvas, this.selecao.x0, this.selecao.y0);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // contorno da imagem
    const a = this.imgParaTela(0, 0), b = this.imgParaTela(this.img.width, this.img.height);
    ctx.strokeStyle = "#555"; ctx.lineWidth = 1;
    ctx.strokeRect(Math.round(a.x) - 0.5, Math.round(a.y) - 0.5, Math.round(b.x - a.x) + 1, Math.round(b.y - a.y) + 1);
    if (this.mostrarOriginal) {
      ctx.fillStyle = "#000a"; ctx.fillRect(8, 8, 92, 22);
      ctx.fillStyle = "#fff"; ctx.font = "12px sans-serif"; ctx.fillText("ORIGINAL", 18, 23);
    }
    if (this.extra) this.extra(ctx, this);
  }
}
