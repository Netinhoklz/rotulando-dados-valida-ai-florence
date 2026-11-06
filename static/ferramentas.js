// Ferramentas do editor. Cada uma descreve a edição em vetores (pontos, retângulos, texto);
// o servidor aplica na resolução original. ``app`` é o editor (editor.js).
//
// Arquitetura e comportamento inspirados nas ferramentas do Graphite (github.com/GraphiteEditor/Graphite,
// editor/src/messages/tool/tool_messages; Apache-2.0. Só o desenho e o comportamento, nenhum código ou ícone copiado):
//  - cada ferramenta é uma MÁQUINA DE ESTADOS. Ela declara os estados em que pode estar (pronta, arrastando, solto…),
//    uma transição para cada evento de entrada em cada estado (pressionar, mover, soltar, tecla…), os dados que guarda
//    entre um evento e outro, a barra de opções (como dados) e as dicas da barra de status de CADA estado: o que cada
//    gesto e cada tecla fazem agora. ``maquina`` monta a ferramenta e a entrega ao editor com a interface de sempre;
//  - o uso: no arraste de seleção e de forma, Shift iguala os lados, Alt cresce a partir do centro e Espaço leva a
//    forma junto; a caixa de transformação tem alças nos cantos e nos lados (Shift mantém a proporção, Alt escala a
//    partir do centro), gira por fora (Shift de 15 em 15°) e Shift ao mover prende no eixo; o zoom aproxima no clique,
//    afasta com Alt e arrastando para os lados é contínuo; conta-gotas e balde usam o botão direito para a cor de fundo;
//    as sobreposições são na cor de destaque, com a medida (L × A px, ângulo) ao lado do cursor.
// Os atalhos continuam os do Photoshop (editor.js): Shift/Alt no COMEÇO do arraste de seleção somam/subtraem; o
// quadrado e o centro valem quando a tecla é apertada depois de começar (solte e aperte de novo, como no Photoshop).

const RAIO_ALCA = 7; // px de tela
const ACENTO = "#00a8ff";  // a cor de destaque das sobreposições (seleção, alças, medidas)
// cursor de girar (desenho próprio: um arco com a seta)
const CURSOR_GIRAR = `url("data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
  + '<path d="M6 13a6.5 6.5 0 1 1 2 4.6" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round"/>'
  + '<path d="M6 13a6.5 6.5 0 1 1 2 4.6" fill="none" stroke="#000" stroke-width="1.6" stroke-linecap="round"/>'
  + '<path d="M2.5 17.5l5.8.2-1.6-5.4z" fill="#000" stroke="#fff" stroke-width="0.9"/></svg>')}") 12 12, alias`;
const CURSORES_LADO = ["ew-resize", "nwse-resize", "ns-resize", "nesw-resize"];
// o cursor de redimensionar que aponta na direção (dx, dy) da tela
function cursorNaDirecao(dx, dy) {
  const k = ((Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) % 4) + 4) % 4;
  return CURSORES_LADO[k];
}

function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
// o retângulo de um arraste de ``a`` até ``p``: ``quadrado`` iguala os lados; ``centro`` cresce para os dois lados a
// partir de ``a`` (o ponto em que o arraste começou)
function retDoArraste(a, p, quadrado, centro) {
  let dx = p.x - a.x, dy = p.y - a.y;
  if (quadrado) { const m = Math.max(Math.abs(dx), Math.abs(dy)); dx = Math.sign(dx || 1) * m; dy = Math.sign(dy || 1) * m; }
  return centro ? { x0: a.x - dx, y0: a.y - dy, x1: a.x + dx, y1: a.y + dy } : { x0: a.x, y0: a.y, x1: a.x + dx, y1: a.y + dy };
}
// graus da tela para leitura (anti-horário positivo, de -180 a 180)
function graus(rad) { let g = (-rad * 180) / Math.PI; g = ((g + 180) % 360 + 360) % 360 - 180; return Math.round(g * 10) / 10; }
// a medida ao lado do cursor (L × A px, ângulo): etiqueta na cor de destaque, como as do Graphite
function leitura(ctx, s, texto) {
  if (!s || !texto) return;
  ctx.save();
  ctx.font = "11px 'Segoe UI', system-ui, sans-serif";
  const w = Math.ceil(ctx.measureText(texto).width) + 10, h = 18, dpr = window.devicePixelRatio || 1;
  const x = Math.round(Math.min(s.x + 14, ctx.canvas.width / dpr - w - 4)), y = Math.round(Math.min(s.y + 16, ctx.canvas.height / dpr - h - 4));
  ctx.fillStyle = ACENTO;
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, 3); else ctx.rect(x, y, w, h);
  ctx.fill();
  ctx.fillStyle = "#fff"; ctx.textBaseline = "middle";
  ctx.fillText(texto, x + 5, y + h / 2 + 0.5);
  ctx.restore();
}
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

// contorno de sobreposição: a cor de destaque sobre um halo claro (lê-se no papel e no fundo escuro); ``tracejado``
// marca o que está solto (o trecho puxado ou colado, ainda não aplicado)
function contorno(ctx, desenhar, tracejado = false) {
  ctx.save();
  ctx.setLineDash([]);
  ctx.lineWidth = 3; ctx.strokeStyle = "rgba(255,255,255,0.7)"; desenhar(); ctx.stroke();
  ctx.lineWidth = 1; ctx.strokeStyle = ACENTO;
  if (tracejado) ctx.setLineDash([5, 3]);
  desenhar(); ctx.stroke();
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
// a máquina de estados de cada ferramenta
// ---------------------------------------------------------------------------
// Uma ferramenta é declarada com:
//   estados   {estado: {evento: (t, ...args) => próximo estado}}; devolver nada = fica no estado; ``false`` = não tratou
//             (a tecla segue para os atalhos do editor)
//   sempre    as transições que valem em qualquer estado (as do estado atual vêm antes)
//   inicial   o estado de partida (padrão "pronta"); ``repouso``: os estados em que ela espera a pessoa começar algo
//   dados     () => os dados da ferramenta (``t.d``: o que ela guarda entre os eventos)
//   opcoes    a barra de opções, como dados, na ordem em que aparece: campos {chave, tipo, rotulo, …}, botões
//             {rotulo, acao, …}, campos da seleção {campoSel, rotulo} e "|" (separador)
//   dicas     {estado: [dica, …] | (t) => [dica, …]}: o que cada gesto e cada tecla fazem AGORA (barra de status)
//   desenhar  (ctx, visor, t) => o que a ferramenta desenha por cima da imagem
//   cursorEm  (p, t) => o cursor sobre o ponto p
//   api       (t) => métodos e propriedades que o editor chama direto (confirmar, cancelar, solto, ancora…)
// Eventos: pressionar, mover, soltar, duplo, sair, tecla e os da vida da ferramenta (ativar, desativar, opcao = mudou
// uma opção, selecao = a seleção mudou).
const EVENTOS = { down: "pressionar", move: "mover", up: "soltar", dbl: "duplo", sair: "sair", tecla: "tecla",
  ativar: "ativar", desativar: "desativar", mudouOpcao: "opcao", aoMudarSelecao: "selecao" };

function maquina(app, def) {
  const inicial = def.inicial || "pronta";
  const t = {
    nome: def.nome, icone: def.icone, atalho: def.atalho, cursor: def.cursor || "default",
    estado: inicial, d: def.dados ? def.dados() : {}, layout: def.opcoes || [],
    aceitaDireito: Boolean(def.aceitaDireito),  // o editor também manda o clique do botão direito (cor de fundo)
  };
  t.campos = t.layout.filter((w) => w !== "|" && w.chave);
  t.botoes = t.layout.filter((w) => w !== "|" && !w.chave);
  const repouso = new Set(def.repouso || [inicial, "pronta"]);
  t.emRepouso = () => repouso.has(t.estado);
  // muda de estado; a barra de status passa a mostrar as dicas do estado novo
  t.ir = (estado) => {
    if (typeof estado !== "string" || estado === t.estado) return;
    t.estado = estado;
    app.visor.redesenhar();
  };
  // um evento: a transição do estado atual (ou a de ``sempre``) faz o efeito e diz o próximo estado
  t.enviar = (evento, ...args) => {
    const tr = def.estados?.[t.estado]?.[evento] || def.sempre?.[evento];
    if (!tr) return false;
    const r = tr(t, ...args);
    if (r === false) return false;
    t.ir(r);
    return true;
  };
  const trata = (evento) => evento in (def.sempre || {}) || Object.values(def.estados || {}).some((e) => evento in e);
  for (const [metodo, evento] of Object.entries(EVENTOS)) if (trata(evento)) t[metodo] = (...a) => t.enviar(evento, ...a);
  t.dicasAgora = () => {
    const ds = def.dicas?.[t.estado] ?? def.dicas?.[inicial] ?? [];
    return (typeof ds === "function" ? ds(t) : ds) || [];
  };
  if (def.desenhar) t.desenhar = (ctx, v) => def.desenhar(ctx, v, t);
  if (def.cursorEm) t.cursorEm = (p) => def.cursorEm(p, t);
  if (def.api) Object.defineProperties(t, Object.getOwnPropertyDescriptors(def.api(t)));
  return t;
}

// dica da barra de status: tecla (k), gesto do mouse (m) e o que acontece (t)
const h = (k, m, t) => ({ k, m, t });
const DICA_SEL = [h("", "arrastar", "selecionar"), h("Shift", "arrastar", "somar"), h("Alt", "arrastar", "subtrair")];
const DICA_TAM = h("[ ]", "", "tamanho · Shift dureza · 1–0 opacidade");
const DICA_FONTE = [h("Alt", "clique", "origem"), h("", "arrastar", "pintar a cópia"), DICA_TAM];
const NOME_MODO_SEL = { novo: "selecionar", somar: "somar à seleção", subtrair: "tirar da seleção", intersectar: "intersectar" };
// dicas que valem com a ferramenta parada: com uma seleção feita, e sempre
export const DICAS = {
  com_selecao: [h("Ctrl", "arrastar", "puxar"), h("Del", "", "apagar com preenchimento"),
    h("Ctrl+C · Ctrl+X · Ctrl+V", "", "copiar · recortar · colar")],
  sempre: [h("Espaço", "arrastar", "mover a vista")],
};

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
  // canto ``i`` arrastado até ``p``: o canto oposto fica (com ``doCentro``, o centro do começo do arraste: cresce para
  // os dois lados); ``proporcao`` mantém a do começo; ``perspectiva`` solta o canto
  arrastarCanto(i, p, perspectiva, proporcao, base, doCentro = false) {
    if (perspectiva || this.livre) {
      if (!this.livre) this.livre = this.cantos();
      this.livre[i] = { x: p.x, y: p.y };
      return;
    }
    const u = { x: Math.cos(this.ang), y: Math.sin(this.ang) }, v = { x: -u.y, y: u.x };
    const fixo = doCentro && base?.c ? base.c : this.cantos()[(i + 2) % 4];
    const d = { x: p.x - fixo.x, y: p.y - fixo.y }, k = doCentro && base?.c ? 2 : 1;
    let du = k * (d.x * u.x + d.y * u.y), dv = k * (d.x * v.x + d.y * v.y);
    if (proporcao && base) {
      const f = Math.max(Math.abs(du) / base.w, Math.abs(dv) / base.h);
      du = Math.sign(du || 1) * base.w * f; dv = Math.sign(dv || 1) * base.h * f;
    }
    this.w = Math.max(1, Math.abs(du)); this.h = Math.max(1, Math.abs(dv));
    if (k === 2) { this.cx = fixo.x; this.cy = fixo.y; return; }
    const su = Math.sign(du || 1) * this.w / 2, sv = Math.sign(dv || 1) * this.h / 2;
    this.cx = fixo.x + su * u.x + sv * v.x;
    this.cy = fixo.y + su * u.y + sv * v.y;
  }
  // os lados: 0 em cima, 1 à direita, 2 embaixo, 3 à esquerda (o meio de cada um tem uma alça)
  meioDoLado(i) { const c = this.cantos(), a = c[i], b = c[(i + 1) % 4]; return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
  // lado ``i`` arrastado até ``p``: estica só a largura (lados 1 e 3) ou só a altura (0 e 2); o lado oposto fica (com
  // ``doCentro``, o centro: estica para os dois lados). ``base.fixo`` e ``base.c`` vêm do começo do arraste
  arrastarLado(i, p, doCentro, base) {
    if (this.livre) return;
    const u = { x: Math.cos(this.ang), y: Math.sin(this.ang) }, v = { x: -u.y, y: u.x };
    const eixo = i % 2 ? u : v;
    const fixo = doCentro ? base.c : base.fixo;
    const s = (p.x - fixo.x) * eixo.x + (p.y - fixo.y) * eixo.y;
    const novo = Math.max(1, Math.abs(s) * (doCentro ? 2 : 1));
    if (i % 2) this.w = novo; else this.h = novo;
    if (doCentro) { this.cx = fixo.x; this.cy = fixo.y; } else { this.cx = fixo.x + eixo.x * s / 2; this.cy = fixo.y + eixo.y * s / 2; }
  }
  destino() { return this.cantos().map((p) => [p.x, p.y]); }
}

// as alças dos lados que cabem na tela: só com o lado comprido e a caixa alta o bastante (numa linha de texto miúda,
// a alça do lado ficaria em cima do miolo e o arraste para mover viraria esticar)
function ladosVisiveis(quad, visor) {
  if (quad.livre) return [];
  const c = quad.cantos().map((p) => visor.imgParaTela(p.x, p.y));
  const comp = (i) => dist(c[i], c[(i + 1) % 4]);
  return [0, 1, 2, 3].filter((i) => comp(i) >= 40 && comp((i + 1) % 4) >= 24)
    .map((i) => ({ i, s: { x: (c[i].x + c[(i + 1) % 4].x) / 2, y: (c[i].y + c[(i + 1) % 4].y) / 2 } }));
}

// a caixa de transformação: contorno na cor de destaque (tracejado = o trecho está solto) e alças brancas com a borda
// na cor de destaque nos cantos e no meio dos lados
function desenharQuad(ctx, visor, quad, tracejado = false) {
  const q = quad.cantos().map((p) => visor.imgParaTela(p.x, p.y));
  contorno(ctx, () => { ctx.beginPath(); q.forEach((s, i) => (i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y))); ctx.closePath(); }, tracejado);
  ctx.save();
  ctx.lineWidth = 1;
  for (const s of [...q, ...ladosVisiveis(quad, visor).map((l) => l.s)]) {
    const x = Math.round(s.x) - 3.5, y = Math.round(s.y) - 3.5;
    ctx.fillStyle = "#fff"; ctx.fillRect(x, y, 7, 7);
    ctx.strokeStyle = ACENTO; ctx.strokeRect(x, y, 7, 7);
  }
  ctx.restore();
}

// interação comum de V (transformar) e colar: o que o arraste pegou — canto (escala), lado (estica um eixo), miolo
// (move; Shift prende no eixo) ou fora (gira; Shift de 15 em 15°)
function interacaoQuad(app, obterQuad, aoMudar) {
  let arr = null, mouse = null;
  const naTela = (p) => app.visor.imgParaTela(p.x, p.y);
  function alvo(quad, p) {
    const s = naTela(p), cantos = quad.cantos().map(naTela);
    const i = cantos.findIndex((c) => dist(c, s) <= RAIO_ALCA + 2);
    if (i >= 0) return { tipo: "canto", i, s, c: cantos[i] };
    const l = ladosVisiveis(quad, app.visor).find((x) => dist(x.s, s) <= RAIO_ALCA);
    if (l) return { tipo: "lado", i: l.i, s, c: l.s };
    if (quad.contem(p)) return { tipo: "mover", s };
    return { tipo: "girar", s };
  }
  return {
    pegou: () => arr?.tipo || null,
    mouse: () => mouse,
    // a medida do que o arraste está fazendo (para a etiqueta ao lado do cursor)
    medida() {
      const quad = obterQuad();
      if (!arr || !quad) return "";
      if (arr.tipo === "mover") return `Δ ${Math.round(arr.dx || 0)}, ${Math.round(arr.dy || 0)} px`;
      if (arr.tipo === "girar") return `${graus(quad.ang)}°`;
      return quad.livre ? "perspectiva" : `${Math.round(quad.w)} × ${Math.round(quad.h)} px`;
    },
    down(p) {
      const quad = obterQuad();
      if (!quad) return null;
      const a = alvo(quad, p);
      mouse = a.s;
      if (a.tipo === "canto") arr = { tipo: "canto", i: a.i, base: { w: quad.w, h: quad.h, c: quad.centro() } };
      else if (a.tipo === "lado") {
        const fixo = quad.meioDoLado((a.i + 2) % 4);
        arr = { tipo: "lado", i: a.i, base: { c: quad.centro(), fixo } };
      } else if (a.tipo === "mover") {
        arr = { tipo: "mover", inicio: p, cx: quad.cx, cy: quad.cy, livre: quad.livre?.map((q) => ({ ...q })), dx: 0, dy: 0 };
      } else {  // o giro conta do começo do arraste (com Shift, o total anda de 15 em 15°)
        const c = quad.centro();
        arr = { tipo: "girar", ang0: Math.atan2(p.y - c.y, p.x - c.x), ang: quad.ang, livre: quad.livre?.map((q) => ({ ...q })) };
      }
      return arr.tipo;
    },
    move(p, ev) {
      const quad = obterQuad();
      mouse = naTela(p);
      if (!arr || !quad) return;
      if (arr.tipo === "mover") {
        let dx = p.x - arr.inicio.x, dy = p.y - arr.inicio.y;
        if (ev.shiftKey) { if (Math.abs(dx) >= Math.abs(dy)) dy = 0; else dx = 0; }  // prende no eixo
        quad.cx = arr.cx; quad.cy = arr.cy;
        if (arr.livre) quad.livre = arr.livre.map((q) => ({ ...q }));
        quad.mover(dx, dy);
        arr.dx = dx; arr.dy = dy;
      } else if (arr.tipo === "canto") quad.arrastarCanto(arr.i, p, ev.ctrlKey, ev.shiftKey, arr.base, ev.altKey);
      else if (arr.tipo === "lado") quad.arrastarLado(arr.i, p, ev.altKey, arr.base);
      else {
        const c = quad.centro();
        let da = Math.atan2(p.y - c.y, p.x - c.x) - arr.ang0;
        if (ev.shiftKey) da = Math.round(da / (Math.PI / 12)) * (Math.PI / 12);
        quad.ang = arr.ang;
        if (arr.livre) quad.livre = arr.livre.map((q) => ({ ...q }));
        quad.girar(da);
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
      const a = alvo(quad, p);
      if (a.tipo === "canto" || a.tipo === "lado") {
        const cc = naTela(quad.centro());
        return cursorNaDirecao(a.c.x - cc.x, a.c.y - cc.y);
      }
      return a.tipo === "mover" ? "move" : CURSOR_GIRAR;
    },
  };
}
// as dicas enquanto se arrasta o quadrilátero, conforme o que o arraste pegou
function dicasDoArraste(pegou, soltar) {
  if (pegou === "canto") return [h("Shift", "", "manter a proporção"), h("Alt", "", "a partir do centro"), h("Ctrl", "", "perspectiva (canto livre)")];
  if (pegou === "lado") return [h("Alt", "", "a partir do centro")];
  if (pegou === "girar") return [h("Shift", "", "girar de 15 em 15°")];
  return [h("Shift", "", "só na horizontal ou na vertical"), h("", "soltar", soltar)];
}
const SETAS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

// ---------------------------------------------------------------------------
export function criarFerramentas(app) {
  const F = {};
  const op = (id) => app.opcoes(id);
  const nova = (def) => maquina(app, def);
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
  // o ponto andou ao menos ``px`` de tela desde o último do traço
  const andou = (p, ult, px) => dist(app.visor.imgParaTela(p.x, p.y), app.visor.imgParaTela(ult.x, ult.y)) >= px;
  const zoomAtual = () => Math.round(app.visor.escala * 1000) / 1000;

  // ------------------------------------------------------------- navegação
  // a Mão: o editor faz o arraste da vista (também com Espaço, com qualquer ferramenta); aqui só os estados e as dicas
  F.mao = nova({
    nome: "Mão (H / segure Espaço)", icone: "✋", atalho: "h", cursor: "grab",
    dados: () => ({ u: null }),
    estados: {
      pronta: { pressionar: (t, p, ev) => { t.d.u = app.visor.telaDoEvento(ev); return "arrastando"; } },
      arrastando: {
        mover: (t, p, ev) => { const s = app.visor.telaDoEvento(ev); app.visor.mover(s.x - t.d.u.x, s.y - t.d.u.y); t.d.u = s; },
        soltar: (t) => { t.d.u = null; return "pronta"; },
      },
    },
    dicas: { pronta: [h("", "arrastar", "mover a vista"), h("", "roda", "zoom")], arrastando: [h("", "soltar", "parar")] },
  });
  // zoom: o clique aproxima (Alt: afasta) onde foi; arrastar para a direita aproxima e para a esquerda afasta, sem
  // parar, em volta do ponto em que o arraste começou (o "zoom arrastando" do Graphite)
  const zoomArrastando = (t, p, ev) => {
    const s = app.visor.telaDoEvento(ev), dx = s.x - t.d.s0.x;
    if (t.estado === "pressionado" && Math.abs(dx) < 4) return undefined;
    app.visor.zoomEm((t.d.e0 * Math.exp(dx * 0.01)) / app.visor.escala, t.d.s0.x, t.d.s0.y);
    return "arrastando";
  };
  F.zoom = nova({
    nome: "Zoom (Z; Alt = afastar)", icone: "🔍", atalho: "z", cursor: "zoom-in",
    opcoes: ["|", { rotulo: "Ajustar (Ctrl+0)", acao: () => app.visor.ajustar() }, { rotulo: "100% (Ctrl+1)", acao: () => app.visor.zoom100() }],
    dados: () => ({ s0: null, e0: 1, alt: false }),
    estados: {
      pronta: {
        pressionar: (t, p, ev) => { Object.assign(t.d, { s0: app.visor.telaDoEvento(ev), e0: app.visor.escala, alt: ev.altKey }); return "pressionado"; },
      },
      pressionado: {
        mover: zoomArrastando,
        soltar: (t, p, ev) => { app.visor.zoomEm(t.d.alt || ev.altKey ? 1 / 1.6 : 1.6, t.d.s0.x, t.d.s0.y); return "pronta"; },
      },
      arrastando: { mover: zoomArrastando, soltar: () => "pronta" },
    },
    dicas: {
      pronta: [h("", "clique", "aproximar"), h("Alt", "clique", "afastar"), h("", "arrastar ←→", "zoom contínuo"),
        h("Ctrl+0", "", "caber na tela"), h("Ctrl+1", "", "100%")],
      pressionado: [h("", "soltar", "aproximar (Alt: afastar)"), h("", "arrastar ←→", "zoom contínuo")],
      arrastando: [h("", "arrastar →", "aproximar"), h("", "arrastar ←", "afastar")],
    },
  });

  // ------------------------------------------------------------- seleção
  // arraste de retângulo/elipse (seleção e formas), como no Graphite: Shift iguala os lados, Alt cresce a partir do
  // centro e Espaço (segurado durante o arraste) leva a forma junto com o mouse. ``prende``: Shift e Alt só valem
  // depois de soltos e apertados de novo, quando no começo do arraste eles já disseram outra coisa (somar/subtrair)
  function arrasteDeForma(t, p, ev, prende) {
    const d = t.d;
    if (app.espaco && d.ultimo) d.a = { x: d.a.x + p.x - d.ultimo.x, y: d.a.y + p.y - d.ultimo.y };
    d.ultimo = p; d.b = p;
    if (!ev.shiftKey) d.shift0 = false;
    if (!ev.altKey) d.alt0 = false;
    d.quadrado = ev.shiftKey && !(prende && d.shift0);
    d.centro = ev.altKey && !(prende && d.alt0);
    d.r = retDoArraste(d.a, p, d.quadrado, d.centro);
    app.visor.redesenhar();
  }
  const comecarForma = (t, p, ev) => Object.assign(t.d, { a: p, b: p, ultimo: p, shift0: ev.shiftKey, alt0: ev.altKey,
    quadrado: false, centro: false, r: { x0: p.x, y0: p.y, x1: p.x, y1: p.y } });
  const tamanhoDoRet = (r) => `${Math.round(Math.abs(r.x1 - r.x0))} × ${Math.round(Math.abs(r.y1 - r.y0))} px`;
  function selRet(id, nome, icone, atalho, tipo) {
    const forma = tipo === "ret" ? "quadrado" : "círculo";
    return nova({
      nome, icone, atalho, cursor: "crosshair", opcoes: [...camposSelecao, "|", ...botoesSelecao],
      dados: () => ({ a: null, b: null, modo: "novo", r: null }),
      sempre: {
        pressionar: (t, p, ev) => { comecarForma(t, p, ev); t.d.modo = modoSelecao(ev, op(id).modo); return "arrastando"; },
      },
      estados: {
        arrastando: {
          mover: (t, p, ev) => arrasteDeForma(t, p, ev, true),
          soltar: (t, p, ev) => {
            arrasteDeForma(t, p, ev, true);
            const { r, modo } = t.d;
            t.d.a = null;
            const pequeno = Math.abs(r.x1 - r.x0) * app.visor.escala < 3 && Math.abs(r.y1 - r.y0) * app.visor.escala < 3;
            if (pequeno) { if (modo === "novo") app.desmarcar(); app.visor.redesenhar(); return "pronta"; }
            app.adicionarForma({ tipo, x0: r.x0, y0: r.y0, x1: r.x1, y1: r.y1 }, modo);
            return "pronta";
          },
        },
      },
      dicas: {
        pronta: DICA_SEL,
        arrastando: (t) => [h("", "soltar", NOME_MODO_SEL[t.d.modo] || "selecionar"),
          h("Shift", "", t.d.shift0 ? `${forma} (solte e aperte de novo)` : forma),
          h("Alt", "", t.d.alt0 ? "a partir do centro (solte e aperte de novo)" : "a partir do centro"),
          h("Espaço", "", "levar a seleção junto"),
          ...(t.d.modo === "novo" ? [h("", "clique sem arrastar", "desmarcar")] : [])],
      },
      desenhar(ctx, v, t) {
        if (t.estado !== "arrastando" || !t.d.a) return;
        const { r } = t.d, a = v.imgParaTela(r.x0, r.y0), b = v.imgParaTela(r.x1, r.y1);
        contorno(ctx, () => {
          ctx.beginPath();
          if (tipo === "ret") ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y);
          else ctx.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2, Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2, 0, 0, Math.PI * 2);
        });
        leitura(ctx, v.imgParaTela(t.d.b.x, t.d.b.y), tamanhoDoRet(r));
      },
    });
  }
  F.ret = selRet("ret", "Seleção retangular (M)", "⬚", "m", "ret");
  F.elipse = selRet("elipse", "Seleção elíptica (M; Shift+M alterna)", "◯", "m", "elipse");

  // laço: "contornando" (à mão livre, arrastando) ou "poligono" (um clique por ponto)
  function fecharLaco(t) {
    const pts = t.d.pts;
    t.d.pts = null;
    if (pts && pts.length >= 3) {
      app.adicionarForma({ tipo: "poligono", pontos: pts.map((q) => [q.x, q.y]), tempos: tempos(pts), zoom: zoomAtual() }, t.d.modo);
    }
    app.visor.redesenhar();
    return "pronta";
  }
  function teclaLaco(t, ev) {
    if (!t.d.pts) return false;
    if (ev.key === "Enter") return fecharLaco(t);
    if (ev.key === "Escape") { t.d.pts = null; app.visor.redesenhar(); return "pronta"; }
    if (ev.key === "Backspace") {
      t.d.pts.pop();
      app.visor.redesenhar();
      if (!t.d.pts.length) { t.d.pts = null; return "pronta"; }
      return undefined;
    }
    return false;
  }
  F.laco = nova({
    nome: "Laço (L) — marque 'poligonal' para clicar ponto a ponto", icone: "➰", atalho: "l", cursor: "crosshair",
    opcoes: [...camposSelecao, { chave: "poligonal", tipo: "bool", rotulo: "Poligonal", padrao: false }, "|", ...botoesSelecao],
    dados: () => ({ pts: null, modo: "novo", mouse: null }),
    sempre: {
      mover: (t, p) => { t.d.mouse = p; if (t.d.pts) app.visor.redesenhar(); },
      desativar: (t) => { t.d.pts = null; return "pronta"; },
    },
    estados: {
      pronta: {
        pressionar: (t, p, ev) => {
          t.d.modo = modoSelecao(ev, op("laco").modo);
          t.d.pts = [comTempo(p)];
          return op("laco").poligonal ? "poligono" : "contornando";
        },
      },
      contornando: {
        pressionar: (t, p, ev) => { t.d.modo = modoSelecao(ev, op("laco").modo); t.d.pts = [comTempo(p)]; },
        mover: (t, p) => {
          t.d.mouse = p;
          if (andou(p, t.d.pts[t.d.pts.length - 1], 2)) t.d.pts.push(comTempo(p));
          app.visor.redesenhar();
        },
        soltar: fecharLaco,
        tecla: teclaLaco,
      },
      poligono: {
        pressionar: (t, p) => {
          const pts = t.d.pts;
          if (pts.length > 2 && !andou(p, pts[0], 8)) return fecharLaco(t);
          pts.push(comTempo(p));
          return undefined;
        },
        duplo: fecharLaco,
        tecla: teclaLaco,
      },
    },
    dicas: {
      pronta: () => (op("laco").poligonal
        ? [h("", "clique", "primeiro ponto"), h("Shift", "", "somar"), h("Alt", "", "subtrair")]
        : [h("", "arrastar", "contornar"), h("Shift", "", "somar"), h("Alt", "", "subtrair")]),
      contornando: [h("", "soltar", "fechar o laço"), h("Enter", "", "fechar"), h("Backspace", "", "tirar o último ponto"),
        h("Esc", "", "cancelar")],
      poligono: [h("", "clique", "próximo ponto"), h("", "clique no 1º ponto · duplo clique", "fechar"), h("Enter", "", "fechar"),
        h("Backspace", "", "tirar o último ponto"), h("Esc", "", "cancelar")],
    },
    desenhar(ctx, v, t) {
      if (!t.d.pts) return;
      const pts = t.estado === "poligono" && t.d.mouse ? [...t.d.pts, t.d.mouse] : t.d.pts;
      contorno(ctx, () => tracarTela(ctx, v, pts, false));
    },
  });

  // caneta: a seleção por caminho, como a Caneta do Photoshop e do Graphite. Clique = ponto de canto; clicar e arrastar =
  // ponto suave (as duas alças saem simétricas e a curva passa macia por ele); Alt ao arrastar = só a alça de saída (a
  // curva muda de direção no ponto). Clique no 1º ponto, duplo clique ou Enter fecham o caminho, que vira a seleção:
  // cada curva é dividida em segmentos de ~1 px e vai ao servidor como polígono. Backspace tira o último ponto; Esc
  // cancela. Ponto: {x, y, ent, sai}, as alças de entrada e de saída (coordenadas da imagem) ou null
  const bezier = (a, c1, c2, b, u) => {
    const v = 1 - u;
    return { x: v * v * v * a.x + 3 * v * v * u * c1.x + 3 * v * u * u * c2.x + u * u * u * b.x,
             y: v * v * v * a.y + 3 * v * v * u * c1.y + 3 * v * u * u * c2.y + u * u * u * b.y };
  };
  function caminhoEmPontos(pts, fechar) {
    const out = [{ x: pts[0].x, y: pts[0].y }];
    const n = fechar ? pts.length : pts.length - 1;
    for (let i = 0; i < n; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      if (!a.sai && !b.ent) { out.push({ x: b.x, y: b.y }); continue; }  // trecho reto
      const c1 = a.sai || a, c2 = b.ent || b;
      const k = Math.max(2, Math.min(400, Math.ceil(dist(a, c1) + dist(c1, c2) + dist(c2, b))));
      for (let j = 1; j <= k; j++) out.push(bezier(a, c1, c2, b, j / k));
    }
    return out;
  }
  function fecharCaneta(t) {
    const pts = t.d.pts;
    t.d.pts = null;
    if (pts && pts.length >= 2) {
      const poli = caminhoEmPontos(pts, true);
      let area = 0;
      for (let i = 0; i < poli.length; i++) {
        const a = poli[i], b = poli[(i + 1) % poli.length];
        area += a.x * b.y - b.x * a.y;
      }
      if (Math.abs(area) / 2 >= 1) {
        app.adicionarForma({ tipo: "poligono", pontos: poli.map((q) => [q.x, q.y]), zoom: zoomAtual() }, t.d.modo);
      } else {
        app.aviso("o caminho não fecha uma área: ponha ao menos três pontos (ou arraste para fazer curvas)", "erro");
      }
    }
    app.visor.redesenhar();
    return "pronta";
  }
  function teclaCaneta(t, ev) {
    if (!t.d.pts) return false;
    if (ev.key === "Enter") return fecharCaneta(t);
    if (ev.key === "Escape") { t.d.pts = null; app.visor.redesenhar(); return "pronta"; }
    if (ev.key === "Backspace" || ev.key === "Delete") {
      t.d.pts.pop();
      app.visor.redesenhar();
      if (!t.d.pts.length) { t.d.pts = null; return "pronta"; }
      return "caminho";
    }
    return false;
  }
  const pertoDoInicio = (t) => t.d.pts && t.d.pts.length >= 2 && t.d.mouse && !andou(t.d.mouse, t.d.pts[0], 8);
  F.caneta = nova({
    nome: "Caneta (P): clique ponto a ponto, arraste para fazer curvas; feche no 1º ponto (ou Enter) e o caminho vira a seleção",
    icone: "✒", atalho: "p", cursor: "crosshair",
    opcoes: [...camposSelecao, "|", ...botoesSelecao],
    dados: () => ({ pts: null, modo: "novo", mouse: null }),
    sempre: {
      mover: (t, p) => { t.d.mouse = p; if (t.d.pts) app.visor.redesenhar(); },
      sair: (t) => { t.d.mouse = null; app.visor.redesenhar(); },
      desativar: (t) => { t.d.pts = null; return "pronta"; },
    },
    estados: {
      pronta: {
        pressionar: (t, p, ev) => {
          t.d.modo = modoSelecao(ev, op("caneta").modo);
          t.d.pts = [{ x: p.x, y: p.y, ent: null, sai: null }];
          return "puxando";
        },
      },
      // o botão ainda apertado depois de pôr o ponto: arrastar puxa as alças dele
      puxando: {
        mover: (t, p, ev) => {
          t.d.mouse = p;
          const q = t.d.pts[t.d.pts.length - 1];
          if (andou(p, q, 3)) {
            q.sai = { x: p.x, y: p.y };
            if (!ev.altKey) q.ent = { x: 2 * q.x - p.x, y: 2 * q.y - p.y };
          }
          app.visor.redesenhar();
        },
        soltar: () => "caminho",
        tecla: teclaCaneta,
      },
      caminho: {
        pressionar: (t, p) => {
          if (pertoDoInicio({ d: { ...t.d, mouse: p } })) return fecharCaneta(t);
          t.d.pts.push({ x: p.x, y: p.y, ent: null, sai: null });
          return "puxando";
        },
        duplo: fecharCaneta,
        tecla: teclaCaneta,
      },
    },
    dicas: {
      pronta: [h("", "clique", "primeiro ponto"), h("", "clicar e arrastar", "ponto com curva"), h("Shift", "", "somar"),
        h("Alt", "", "subtrair")],
      puxando: [h("", "arrastar", "as alças da curva"), h("Alt", "arrastar", "só a alça de saída (quebra a curva)"),
        h("", "soltar", "fixar o ponto")],
      caminho: (t) => [
        ...(pertoDoInicio(t) ? [h("", "clique", "fechar o caminho")] : [h("", "clique", "ponto de canto"),
          h("", "clicar e arrastar", "ponto com curva")]),
        h("", "clique no 1º ponto · duplo clique", "fechar"), h("Enter", "", "fechar e selecionar"),
        h("Backspace", "", "tirar o último ponto"), h("Esc", "", "cancelar")],
    },
    desenhar(ctx, v, t) {
      const pts = t.d.pts;
      if (!pts) return;
      // o caminho e, esperando o próximo clique, o trecho até o mouse (já com a curva da alça de saída)
      const ate = t.estado === "caminho" && t.d.mouse ? [...pts, { x: t.d.mouse.x, y: t.d.mouse.y, ent: null, sai: null }] : pts;
      if (ate.length >= 2) contorno(ctx, () => tracarTela(ctx, v, caminhoEmPontos(ate, false), false));
      ctx.save();
      ctx.lineWidth = 1; ctx.strokeStyle = ACENTO;
      for (const q of pts) {  // as alças: uma linha fina até um círculo
        const s = v.imgParaTela(q.x, q.y);
        for (const a of [q.ent, q.sai]) {
          if (!a) continue;
          const sa = v.imgParaTela(a.x, a.y);
          ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(sa.x, sa.y); ctx.stroke();
          ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(sa.x, sa.y, 3, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        }
      }
      pts.forEach((q, i) => {  // os pontos: quadradinhos; o 1º fica cheio quando um clique ali fecha o caminho
        const s = v.imgParaTela(q.x, q.y);
        ctx.fillStyle = i === 0 && pertoDoInicio(t) ? ACENTO : "#fff";
        ctx.fillRect(s.x - 3.5, s.y - 3.5, 7, 7); ctx.strokeRect(s.x - 3.5, s.y - 3.5, 7, 7);
      });
      ctx.restore();
    },
  });

  F.varinha = nova({
    nome: "Varinha mágica (W)", icone: "✨", atalho: "w", cursor: "crosshair",
    opcoes: [...camposSelecao,
      { chave: "tolerancia", tipo: "num", rotulo: "Tolerância", min: 0, max: 255, passo: 1, padrao: 24 },
      { chave: "contigua", tipo: "bool", rotulo: "Contígua", padrao: true }, "|", ...botoesSelecao],
    estados: {
      pronta: {
        pressionar: (t, p, ev) => {
          const o = op("varinha");
          app.adicionarForma({ tipo: "varinha", x: p.x, y: p.y, tolerancia: o.tolerancia, contigua: o.contigua }, modoSelecao(ev, o.modo));
        },
      },
    },
    dicas: { pronta: [h("", "clique", "cor parecida"), h("Shift", "clique", "somar"), h("Alt", "clique", "subtrair")] },
  });

  F.pincelsel = nova({
    nome: "Pincel de seleção (Q): pinte a área a selecionar (Alt = tirar da seleção)", icone: "🖍", atalho: "q", cursor: "none",
    opcoes: [{ chave: "tamanho", tipo: "num", rotulo: "Tamanho ([ ])", min: 1, max: 1000, passo: 1, padrao: 20 }, "|", ...botoesSelecao],
    dados: () => ({ pts: null, modo: "novo", mouse: null }),
    sempre: {
      pressionar: (t, p, ev) => {
        t.d.pts = [comTempo(p)];
        t.d.modo = ev.altKey ? "subtrair" : (app.temSelecao() ? "somar" : "novo");
        return "pintando";
      },
      mover: (t, p) => {
        t.d.mouse = p;
        if (t.d.pts && andou(p, t.d.pts[t.d.pts.length - 1], 2)) t.d.pts.push(comTempo(p));
        app.visor.redesenhar();
      },
      sair: (t) => { t.d.mouse = null; app.visor.redesenhar(); },
      tecla: (t, ev) => {
        const o = op("pincelsel");
        if (ev.key !== "[" && ev.key !== "]") return false;
        o.tamanho = Math.max(1, Math.round(o.tamanho * (ev.key === "]" ? 1.25 : 0.8)));
        app.salvarOpcoes(); app.renderOpcoes(); app.visor.redesenhar();
        return undefined;
      },
    },
    estados: {
      pintando: {
        soltar: (t) => {
          const pts = t.d.pts;
          t.d.pts = null;
          app.adicionarForma({ tipo: "traco", pontos: pts.map((q) => [q.x, q.y]), tamanho: op("pincelsel").tamanho, tempos: tempos(pts),
            zoom: zoomAtual() }, t.d.modo);
          return "pronta";
        },
      },
    },
    dicas: {
      pronta: [h("", "arrastar", "pintar a seleção"), h("Alt", "arrastar", "tirar"), DICA_TAM],
      pintando: (t) => [h("", "soltar", NOME_MODO_SEL[t.d.modo] || "selecionar")],
    },
    desenhar(ctx, v, t) {
      if (t.d.pts) {
        ctx.save(); ctx.globalAlpha = 0.45; ctx.lineCap = "round"; ctx.lineJoin = "round";
        ctx.strokeStyle = t.d.modo === "subtrair" ? "#ff4fa0" : "#28a0ff";
        ctx.lineWidth = Math.max(1, op("pincelsel").tamanho * v.escala);
        tracarTela(ctx, v, t.d.pts.length === 1 ? [t.d.pts[0], t.d.pts[0]] : t.d.pts); ctx.stroke(); ctx.restore();
      }
      circuloCursor(ctx, v, t.d.mouse, op("pincelsel").tamanho);
    },
  });

  // ------------------------------------------------------------- pintura
  // [ ] tamanho; Shift+[ Shift+] dureza (de 25 em 25%), como no Photoshop
  function teclaPincel(id, ev) {
    const o = op(id), k = ev.key;
    if ((k === "{" || k === "}" || (ev.shiftKey && (k === "[" || k === "]"))) && "dureza" in o) {
      o.dureza = Math.min(1, Math.max(0, Math.round((o.dureza + (k === "}" || k === "]" ? 0.25 : -0.25)) * 100) / 100));
      app.salvarOpcoes(); app.renderOpcoes(); app.status(`dureza ${Math.round(o.dureza * 100)}%`);
      return undefined;
    }
    if (k === "[" || k === "]") {
      o.tamanho = Math.max(1, Math.round(o.tamanho * (k === "]" ? 1.25 : 0.8)));
      app.salvarOpcoes(); app.renderOpcoes(); app.visor.redesenhar();
      return undefined;
    }
    return false;
  }
  // um traço de pincel: "pronta" → (pressionar) "pintando" → (soltar) manda o traço ao servidor e volta a "pronta". Os
  // traços que esperam o servidor ficam desenhados (``pendentes``); um novo pode começar enquanto isso.
  // ``extra``: antes(t, p, ev) (false = não pinta), params(t), desenhar(ctx, v, t), dados(), inicial, dicas
  function ferramentaTraco(id, nome, icone, atalho, tipo, campos, extra = {}) {
    async function aplicarTraco(t) {
      const traco = { pts: t.d.pts, cor: app.corFrente, tamanho: op(id).tamanho };
      t.d.pts = null;
      const params = { ...op(id), pontos: traco.pts.map((q) => [q.x, q.y]), tempos: tempos(traco.pts), zoom: zoomAtual(),
        ...(extra.params ? extra.params(t) : {}) };
      if (extra.cor) params.cor = traco.cor;
      t.d.pendentes.push(traco);
      try { await app.aplicar(tipo, params, { semCategoria: tipo === "borracha" }); }
      finally { t.d.pendentes = t.d.pendentes.filter((x) => x !== traco); app.visor.redesenhar(); }
    }
    return nova({
      nome, icone, atalho, cursor: "none", opcoes: campos, inicial: extra.inicial, repouso: extra.repouso,
      dados: () => ({ pts: null, mouse: null, pendentes: [], ...(extra.dados ? extra.dados() : {}) }),
      sempre: {
        pressionar: (t, p, ev) => {
          if (extra.antes && extra.antes(t, p, ev) === false) return undefined;
          t.d.pts = [comTempo(p)];
          app.visor.redesenhar();
          return "pintando";
        },
        mover: (t, p) => {
          t.d.mouse = p;
          if (t.d.pts && andou(p, t.d.pts[t.d.pts.length - 1], 1.5)) t.d.pts.push(comTempo(p));
          app.visor.redesenhar();
        },
        sair: (t) => { t.d.mouse = null; app.visor.redesenhar(); },
        tecla: (t, ev) => teclaPincel(id, ev),
      },
      estados: { pintando: { soltar: (t) => { aplicarTraco(t); return "pronta"; } } },
      dicas: { pintando: [h("", "soltar", "aplicar o traço")], ...extra.dicas },
      desenhar(ctx, v, t) {
        const o = op(id);
        const tracos = [...t.d.pendentes, ...(t.d.pts ? [{ pts: t.d.pts, cor: app.corFrente, tamanho: o.tamanho }] : [])];
        for (const tr of tracos) {  // pendentes continuam visíveis até o servidor devolver o resultado
          ctx.save();
          ctx.globalAlpha = 0.55; ctx.lineCap = "round"; ctx.lineJoin = "round";
          ctx.strokeStyle = extra.cor ? tr.cor : (tipo === "borracha" ? "#ff4fa0" : "#4fc3ff");
          ctx.lineWidth = Math.max(1, tr.tamanho * v.escala);
          tracarTela(ctx, v, tr.pts.length === 1 ? [tr.pts[0], tr.pts[0]] : tr.pts);
          ctx.stroke(); ctx.restore();
        }
        circuloCursor(ctx, v, t.d.mouse, o.tamanho);
        if (extra.desenhar) extra.desenhar(ctx, v, t);
      },
    });
  }
  const tam = (p) => ({ chave: "tamanho", tipo: "num", rotulo: "Tamanho ([ ])", min: 1, max: 1000, passo: 1, padrao: p });
  const dur = (p) => ({ chave: "dureza", tipo: "faixa", rotulo: "Dureza", min: 0, max: 1, passo: 0.05, padrao: p });
  const opa = { chave: "opacidade", tipo: "faixa", rotulo: "Opacidade", min: 0.05, max: 1, passo: 0.05, padrao: 1 };
  const patch = { chave: "patch", tipo: "select", rotulo: "Patch", opcoes: [[5, "5"], [7, "7"], [9, "9"], [11, "11"]], padrao: 7 };
  const semente = { chave: "semente", tipo: "num", rotulo: "Semente", min: 0, max: 999999, passo: 1, padrao: 0 };

  F.pincel = ferramentaTraco("pincel", "Pincel (B) — Alt+clique pega a cor", "🖌", "b", "pincel", [tam(8), dur(0.9), opa], {
    cor: true,
    antes: (t, p, ev) => { if (ev.altKey) { app.contaGotas(p); return false; } return true; },
    dicas: { pronta: [h("", "arrastar", "pintar"), h("Alt", "clique", "pegar a cor"), DICA_TAM] },
  });
  F.borracha = ferramentaTraco("borracha", "Borracha (E): apaga da CAMADA ativa, revelando o que está embaixo (o original)", "⌫", "e",
    "borracha", [tam(20), dur(1), opa], { dicas: { pronta: [h("", "arrastar", "devolver o original"), DICA_TAM] } });
  F.corretivo = ferramentaTraco("corretivo", "Pincel corretivo (J): reconstrói o traço a partir do entorno", "✚", "j", "corretivo",
    [tam(16), dur(0.8), patch, semente], { dicas: { pronta: [h("", "arrastar", "sumir com o que pintar"), DICA_TAM] } });
  // carimbo e recuperação: "sem_origem" até o Alt+clique dizer de onde copiar; depois "pronta" ⇄ "pintando"
  function ferramentaFonte(id, nome, icone, atalho, tipo, campos) {
    return ferramentaTraco(id, nome, icone, atalho, tipo, campos, {
      inicial: "sem_origem", repouso: ["sem_origem", "pronta"],
      dados: () => ({ origem: null, inicio: null }),
      antes: (t, p, ev) => {
        if (ev.altKey) {
          t.d.origem = p; t.d.inicio = null;
          app.status("origem definida: agora pinte onde quer copiar"); app.visor.redesenhar();
          t.ir("pronta");
          return false;
        }
        if (!t.d.origem) { app.aviso("Alt+clique para definir a ORIGEM (de onde copiar)", "erro"); return false; }
        if (!t.d.inicio || !op(id).alinhado) t.d.inicio = p;
        if (op(id).fonte === "original" && !app.visor.orig) app.carregarOriginal();
        return true;
      },
      params: (t) => ({ origem_x: t.d.origem.x, origem_y: t.d.origem.y, inicio_x: t.d.inicio.x, inicio_y: t.d.inicio.y }),
      dicas: { sem_origem: [h("Alt", "clique", "definir a origem (de onde copiar)"), DICA_TAM], pronta: DICA_FONTE },
      desenhar: (ctx, v, t) => {
        if (!t.d.origem) return;
        const esc = (op(id).escala || 100) / 100, ang = ((op(id).rotacao || 0) * Math.PI) / 180;
        let alvo = t.d.origem;
        if (t.d.mouse && t.d.inicio) {
          const dx = t.d.mouse.x - t.d.inicio.x, dy = t.d.mouse.y - t.d.inicio.y;
          alvo = { x: t.d.origem.x + esc * (Math.cos(ang) * dx - Math.sin(ang) * dy),
            y: t.d.origem.y + esc * (Math.sin(ang) * dx + Math.cos(ang) * dy) };
        }
        const sc = v.imgParaTela(alvo.x, alvo.y);
        contorno(ctx, () => { ctx.beginPath(); ctx.moveTo(sc.x - 9, sc.y); ctx.lineTo(sc.x + 9, sc.y); ctx.moveTo(sc.x, sc.y - 9); ctx.lineTo(sc.x, sc.y + 9); });
        if (!t.d.pts && op(id).fantasma !== false) fantasma(ctx, v, t.d.mouse, t.d.origem, t.d.inicio, op(id));
      },
    });
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
  F.recuperacao = ferramentaFonte("recuperacao", "Pincel de recuperação (J; Shift+J alterna): como o carimbo, mas casa cor e luz com o destino",
    "🩹", "j", "recuperacao", [tam(16), dur(1), ...camposFonte]);
  F.retoque = ferramentaTraco("retoque", "Pincel de retoque (O): desfocar, nitidez, borrar, clarear, escurecer, saturar", "💧", "o", "retoque", [
    { chave: "modo", tipo: "select", rotulo: "Modo", opcoes: [["desfocar", "desfocar"], ["nitidez", "nitidez"], ["borrar", "borrar (dedo)"], ["clarear", "clarear (subexposição)"], ["escurecer", "escurecer (superexposição)"], ["saturar", "saturar"], ["dessaturar", "dessaturar"]], padrao: "desfocar" },
    { chave: "forca", tipo: "faixa", rotulo: "Força", min: 0.05, max: 1, passo: 0.05, padrao: 0.5 }, tam(24), dur(0.6)],
  { dicas: { pronta: [h("", "arrastar", "retocar"), DICA_TAM] } });

  F.balde = nova({
    nome: "Balde de tinta (G)", icone: "🪣", atalho: "g", cursor: "crosshair",
    opcoes: [{ chave: "tolerancia", tipo: "num", rotulo: "Tolerância", min: 0, max: 255, passo: 1, padrao: 20 },
      { chave: "contigua", tipo: "bool", rotulo: "Contígua", padrao: true }, opa],
    aceitaDireito: true,  // como no Graphite: clique = cor de frente, clique direito = cor de fundo
    estados: {
      pronta: {
        pressionar: (t, p, ev) => {
          app.aplicar("balde", { ...op("balde"), x: p.x, y: p.y, cor: ev.button === 2 ? app.corFundo : app.corFrente });
        },
      },
    },
    dicas: { pronta: [h("", "clique", "preencher com a cor de frente"), h("", "clique direito", "com a cor de fundo")] },
  });
  F.contagotas = nova({
    nome: "Conta-gotas (I): lê a cor do pixel no servidor", icone: "🎯", atalho: "i", cursor: "crosshair",
    opcoes: [{ chave: "raio", tipo: "select", rotulo: "Amostra", opcoes: [[0, "1 px"], [1, "3x3 (mediana)"], [2, "5x5 (mediana)"]], padrao: 1 }],
    aceitaDireito: true,
    estados: {
      pronta: { pressionar: (t, p, ev) => { app.contaGotas(p, op("contagotas").raio, ev.button === 2 ? "fundo" : "frente"); } },
    },
    dicas: { pronta: [h("", "clique", "cor de frente"), h("", "clique direito", "cor de fundo")] },
  });

  // ------------------------------------------------------------- texto
  const camposTexto = [
    { chave: "texto", tipo: "texto", rotulo: "Texto", padrao: "", largo: true, dado: true },
    { chave: "_gerar", tipo: "botao", rotulo: "🎲", titulo: "Valor novo parecido com o texto antigo (o selecionado, ou o campo onde você clicou)", acao: () => app.gerarTexto() },
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
  // posta a linha de base, o que se digita vai para o campo "Texto" (o teclado do editor manda, ver montarTeclado) e
  // a 1ª tecla troca o que havia nele, como um texto selecionado (Enter mantém). O foco fica na imagem até lá: as
  // setas continuam ajustando a linha de base. Antes do 06/11/2026 cada letra virava um atalho (B pincel, S carimbo…)
  const armarTexto = () => { app.textoNovo = true; };
  // texto: "pronta" (sem linha de base) → (clique) "posicionando" (arrastando a linha de base) → (soltar) "digitando"
  // (linha de base no lugar: digita nas opções, setas ajustam, Enter aplica, Esc cancela). O substituir tem também
  // "selecionando" (arraste sobre o texto antigo; ao soltar, o servidor estima posição, tamanho e cor).
  // ``extra``: pressionar(t, p, ev) → estado (ou nada: segue o clique normal), estados, dicas, desenhar, ativar, depois,
  // exigeSelecao, botoes
  function ferramentaTexto(id, tipo, nome, icone, atalho, extraCampos, extra = {}) {
    let t = null;
    const previa = () => { if (t.pronto()) app.previa(tipo, t.params()); else app.limparPrevia(); };
    t = nova({
      nome, icone, atalho, cursor: "text",
      opcoes: [...camposTexto, ...extraCampos, "|", ...(extra.botoes || []),
        { rotulo: "🔍 Qual é a fonte?", titulo: "Compara o texto selecionado com todas as fontes instaladas e mostra as mais parecidas",
          acao: () => app.identificarFonte() },
        { rotulo: "Aplicar (Enter)", primario: true, acao: () => t.aplicar() }, { rotulo: "Cancelar (Esc)", acao: () => t.cancelar() }],
      dados: () => ({ ancora: null, arr: null }),
      sempre: {
        pressionar: (t, p, ev) => {
          const r = extra.pressionar ? extra.pressionar(t, p, ev) : undefined;
          if (r) return r;
          t.d.ancora = p; app.visor.redesenhar(); previa();
          return "posicionando";
        },
        tecla: (t, ev) => {
          if (ev.key === "Enter") { t.aplicar(); return undefined; }
          if (ev.key === "Escape") { t.cancelar(); return undefined; }
          if (!t.d.ancora) return false;
          const passo = ev.shiftKey ? 10 : (ev.altKey ? 0.25 : 1), m = SETAS[ev.key];
          if (!m) return false;
          t.d.ancora = { x: t.d.ancora.x + m[0] * passo, y: t.d.ancora.y + m[1] * passo };
          app.visor.redesenhar(); previa();
          return undefined;
        },
        opcao: () => { previa(); },
        ativar: (t) => { if (extra.ativar) extra.ativar(t); previa(); },
        desativar: (t) => { t.d.ancora = null; t.d.arr = null; app.limparPrevia(); return "pronta"; },
      },
      estados: {
        posicionando: {
          mover: (t, p) => { t.d.ancora = p; app.visor.redesenhar(); },
          soltar: () => { previa(); armarTexto(); return "digitando"; },
        },
        ...extra.estados,
      },
      dicas: {
        pronta: [h("", "clique", "linha de base"), h("Enter", "", "aplicar"), h("Esc", "", "cancelar"), h("setas", "", "ajuste fino")],
        posicionando: [h("", "arrastar", "posicionar a linha de base"), h("", "soltar", "digitar o texto nas opções")],
        digitando: [h("Enter", "", "aplicar"), h("Esc", "", "cancelar"), h("setas", "", "ajuste fino (Shift 10 px · Alt ¼ px)"),
          h("", "clique", "outra linha de base")],
        ...extra.dicas,
      },
      desenhar(ctx, v, t) {
        if (extra.desenhar) extra.desenhar(ctx, v, t);
        if (!t.d.ancora) return;
        const s = v.imgParaTela(t.d.ancora.x, t.d.ancora.y);
        const tamTela = op(id).tamanho * v.escala;
        contorno(ctx, () => { ctx.beginPath(); ctx.moveTo(s.x - 6, s.y); ctx.lineTo(s.x + Math.max(30, tamTela * 3), s.y); ctx.moveTo(s.x, s.y + 4); ctx.lineTo(s.x, s.y - Math.max(10, tamTela)); });
      },
      api: (t) => ({
        // a linha de base: o editor lê e escreve direto (🔍 "usar esta fonte" põe a tinta no mesmo lugar)
        get ancora() { return t.d.ancora; },
        set ancora(v) { t.d.ancora = v; t.ir(v ? (t.estado === "posicionando" ? "posicionando" : "digitando") : "pronta"); },
        params() {
          const o = op(id);
          return { ...o, fonte: o.fonte || app.estado.fonte_padrao, x: t.d.ancora.x, y: t.d.ancora.y, cor: app.corFrente };
        },
        pronto() { return Boolean(t.d.ancora && (op(id).texto || "").trim() && (!extra.exigeSelecao || app.temSelecao())); },
        previa,
        async aplicar() {
          if (!t.pronto()) {
            app.aviso(extra.exigeSelecao && !app.temSelecao() ? "selecione o texto antigo primeiro" : "clique na imagem e digite o texto", "erro");
            return;
          }
          if (await app.aplicar(tipo, t.params())) { t.ancora = null; if (extra.depois) extra.depois(); app.visor.redesenhar(); }
        },
        cancelar() { t.ancora = null; app.limparPrevia(); app.visor.redesenhar(); },
      }),
    });
    return t;
  }
  F.texto = ferramentaTexto("texto", "texto", "Texto (T): clique na linha de base, digite e Enter", "T", "t", []);

  // substituir: arraste sobre o texto antigo; o servidor estima posição/tamanho/cor
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
    armarTexto();
  }
  F.substituir = ferramentaTexto("substituir", "substituir_texto",
    "Substituir texto (T; Shift+T alterna): arraste sobre o texto antigo, digite o novo e Enter (apaga com preenchimento inteligente)", "T↔", "t",
    [patch, semente], {
      exigeSelecao: true,
      botoes: [{ rotulo: "Re-estimar", titulo: "Estimar de novo posição, tamanho e cor", acao: estimar }],
      // dentro da seleção: move a linha de base; fora (ou com Shift): uma seleção nova sobre o texto antigo
      pressionar: (t, p, ev) => {
        const bb = app.selBbox();
        if (bb && p.x >= bb[0] && p.x <= bb[2] && p.y >= bb[1] && p.y <= bb[3] && !ev.shiftKey) return undefined;
        t.d.arr = { a: p, b: p };
        return "selecionando";
      },
      estados: {
        selecionando: {
          mover: (t, p) => { t.d.arr.b = p; app.visor.redesenhar(); },
          soltar: (t, p) => {
            const { a } = t.d.arr;
            t.d.arr = null;
            const volta = t.d.ancora ? "digitando" : "pronta";
            if (Math.abs(a.x - p.x) * app.visor.escala < 3 || Math.abs(a.y - p.y) * app.visor.escala < 3) {
              // um clique (sem arrastar): a linha de texto em que ele caiu vira o texto antigo, como o clique da
              // ferramenta de texto do Photoshop entra na edição. Antes nada acontecia, e o que a pessoa digitava em
              // seguida virava atalho (Shift+V abria o "Colar de outro documento")
              app.visor.redesenhar();
              app.campoNoPonto(p).then((c) => {
                if (!c) { app.aviso("clique em cima de um texto (ou arraste sobre ele) para substituí-lo", "erro"); return; }
                const [x0, y0, x1, y1] = c.caixa;
                app.adicionarForma({ tipo: "ret", x0, y0, x1, y1 }, "novo").then(estimar);
              }).catch((e) => app.aviso(e.message, "erro"));
              return volta;
            }
            app.adicionarForma({ tipo: "ret", x0: a.x, y0: a.y, x1: p.x, y1: p.y }, "novo").then(estimar);
            return volta;
          },
        },
      },
      dicas: {
        pronta: [h("", "clique", "no texto antigo"), h("", "arrastar", "sobre o texto antigo"), h("Enter", "", "aplicar"),
          h("Esc", "", "cancelar")],
        selecionando: [h("", "soltar", "estimar fonte, tamanho e cor do texto antigo")],
        digitando: [h("Enter", "", "aplicar"), h("Esc", "", "cancelar"), h("setas", "", "ajuste fino (Shift 10 px · Alt ¼ px)"),
          h("", "arrastar dentro", "mover a linha de base"), h("Shift", "arrastar", "outro texto antigo")],
      },
      desenhar(ctx, v, t) {
        if (!t.d.arr) return;
        const a = v.imgParaTela(t.d.arr.a.x, t.d.arr.a.y), b = v.imgParaTela(t.d.arr.b.x, t.d.arr.b.y);
        contorno(ctx, () => { ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); });
      },
      ativar() { if (app.temSelecao() && !F.substituir.ancora) estimar(); },
      depois() { app.desmarcar(); },
    });
  // a seleção mudou: estima de novo (evento à parte do texto, que não tem)
  F.substituir.aoMudarSelecao = () => { if (app.temSelecao()) estimar(); };

  // ------------------------------------------------------------- remendo
  F.remendo = nova({
    nome: "Remendo (J; Shift+J alterna): com uma seleção feita, arraste-a até uma área boa; a seleção recebe aquela textura, mesclada",
    icone: "🧩", atalho: "j", cursor: "move",
    opcoes: [
      { chave: "modo", tipo: "select", rotulo: "Modo", opcoes: [["origem", "origem: preencher a seleção"], ["destino", "destino: levar a seleção para lá"]], padrao: "origem" },
      { chave: "mesclar", tipo: "bool", rotulo: "Mesclar cor/luz (Poisson)", padrao: true },
    ],
    dados: () => ({ a: null, d: { x: 0, y: 0 } }),
    sempre: {
      pressionar: (t, p) => {
        if (!app.temSelecao()) { app.aviso("faça uma seleção da área a corrigir antes", "erro"); return "pronta"; }
        t.d.a = p; t.d.d = { x: 0, y: 0 };
        return "arrastando";
      },
      desativar: (t) => { t.d.a = null; app.limparPrevia(); return "pronta"; },
    },
    estados: {
      arrastando: {
        mover: (t, p) => {
          t.d.d = { x: Math.round(p.x - t.d.a.x), y: Math.round(p.y - t.d.a.y) };
          if (t.d.d.x || t.d.d.y) app.previa("remendo", { ...op("remendo"), dx: t.d.d.x, dy: t.d.d.y });
          app.visor.redesenhar();
        },
        soltar: (t) => {
          const d = t.d.d;
          t.d.a = null;
          if (!d.x && !d.y) { app.visor.redesenhar(); return "pronta"; }
          app.aplicar("remendo", { ...op("remendo"), dx: d.x, dy: d.y }).then((ok) => { if (ok) app.desmarcar(); app.visor.redesenhar(); });
          return "pronta";
        },
      },
    },
    dicas: {
      pronta: () => (app.temSelecao() ? [h("", "arrastar", "leve a seleção até papel limpo")]
        : [h("", "", "faça antes uma seleção da área a corrigir"), h("", "arrastar", "leve a seleção até papel limpo")]),
      arrastando: [h("", "soltar", "remendar com o papel daqui")],
    },
    desenhar(ctx, v, t) {
      const bb = app.selBbox();
      if (!bb || !t.d.a) return;
      const a = v.imgParaTela(bb[0] + t.d.d.x, bb[1] + t.d.d.y), b = v.imgParaTela(bb[2] + t.d.d.x, bb[3] + t.d.d.y);
      contorno(ctx, () => { ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); });
    },
  });

  // ------------------------------------------------------------- formas
  F.forma = nova({
    nome: "Formas (U): arraste para desenhar; Shift = quadrado/círculo/linha reta", icone: "▭", atalho: "u", cursor: "crosshair",
    opcoes: [
      { chave: "tipo", tipo: "select", rotulo: "Forma", opcoes: [["ret", "retângulo"], ["elipse", "elipse"], ["linha", "linha"]], padrao: "ret" },
      { chave: "preencher", tipo: "bool", rotulo: "Preencher (cor de frente)", padrao: true },
      { chave: "_cor", tipo: "corfrente", rotulo: "Preench." },
      { chave: "espessura", tipo: "num", rotulo: "Contorno px", min: 0, max: 200, passo: 0.5, padrao: 0 },
      { chave: "cor_contorno", tipo: "cor", rotulo: "Cor contorno", padrao: "#000000" },
      opa,
    ],
    // retângulo e elipse: Shift iguala os lados, Alt cresce a partir do centro; linha: Shift prende o ângulo de 15 em
    // 15°, Alt estende para os dois lados do ponto inicial; Espaço leva a forma junto
    dados: () => ({ a: null, b: null, r: null }),
    sempre: { pressionar: (t, p, ev) => { comecarForma(t, p, ev); return "desenhando"; } },
    estados: {
      desenhando: {
        mover: (t, p, ev) => { arrasteDaForma(t, p, ev); },
        soltar: (t, p, ev) => {
          arrasteDaForma(t, p, ev);
          const { r } = t.d;
          t.d.a = null;
          if (Math.hypot(r.x1 - r.x0, r.y1 - r.y0) * app.visor.escala < 3) { app.visor.redesenhar(); return "pronta"; }
          const o = op("forma");
          if (o.tipo === "linha" && !(o.espessura > 0)) { app.aviso("a linha precisa de 'Contorno px' maior que zero", "erro"); return "pronta"; }
          app.aplicar("forma", { ...o, x0: r.x0, y0: r.y0, x1: r.x1, y1: r.y1, cor: app.corFrente }).then(() => app.visor.redesenhar());
          return "pronta";
        },
      },
    },
    dicas: {
      pronta: [h("", "arrastar", "desenhar"), h("Shift", "arrastar", "quadrado / linha de 15 em 15°"), h("Alt", "arrastar", "a partir do centro")],
      desenhando: () => [h("Shift", "", op("forma").tipo === "linha" ? "de 15 em 15°" : "quadrado / círculo"),
        h("Alt", "", "a partir do centro"), h("Espaço", "", "levar a forma junto"), h("", "soltar", "desenhar")],
    },
    desenhar(ctx, v, t) {
      if (!t.d.a) return;
      const { r } = t.d, a = v.imgParaTela(r.x0, r.y0), b = v.imgParaTela(r.x1, r.y1), tp = op("forma").tipo;
      contorno(ctx, () => {
        ctx.beginPath();
        if (tp === "linha") { ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); }
        else if (tp === "ret") ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y);
        else ctx.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2, Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2, 0, 0, Math.PI * 2);
      });
      leitura(ctx, v.imgParaTela(t.d.b.x, t.d.b.y), tp === "linha"
        ? `${Math.round(Math.hypot(r.x1 - r.x0, r.y1 - r.y0))} px · ${graus(Math.atan2(r.y1 - r.y0, r.x1 - r.x0))}°` : tamanhoDoRet(r));
    },
  });
  // o arraste da ferramenta Formas: o retângulo de sempre; na linha, o Shift prende o ângulo e o Alt a centra
  function arrasteDaForma(t, p, ev) {
    if (op("forma").tipo !== "linha") { arrasteDeForma(t, p, ev, false); return; }
    const d = t.d;
    if (app.espaco && d.ultimo) d.a = { x: d.a.x + p.x - d.ultimo.x, y: d.a.y + p.y - d.ultimo.y };
    d.ultimo = p; d.b = p;
    let dx = p.x - d.a.x, dy = p.y - d.a.y;
    if (ev.shiftKey) {
      const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 12)) * (Math.PI / 12), len = Math.hypot(dx, dy);
      dx = len * Math.cos(ang); dy = len * Math.sin(ang);
    }
    d.r = ev.altKey ? { x0: d.a.x - dx, y0: d.a.y - dy, x1: d.a.x + dx, y1: d.a.y + dy } : { x0: d.a.x, y0: d.a.y, x1: d.a.x + dx, y1: d.a.y + dy };
    app.visor.redesenhar();
  }

  // ------------------------------------------------------------- mover / puxar
  // Com uma seleção, arrastar PUXA os pixels: eles seguem o mouse na hora e, ao soltar, o servidor mostra o resultado de
  // verdade (com o buraco já preenchido). Alt no começo do arraste = duplicar. Cantos escalam (Shift = proporção, Ctrl =
  // perspectiva); fora da caixa, gira. Enter (ou ✓) aplica, Esc desfaz o arraste. Como no Photoshop, trocar de
  // ferramenta, desmarcar, salvar ou trocar de imagem também aplicam o que está solto.
  // Estados: "sem_selecao" (arrastar move a camada ativa) ⇄ "movendo_camada"; com seleção, "pronta" → "arrastando" →
  // "solto" (o trecho fora do lugar, esperando Enter/Esc) → "aplicando". O estado sai sempre dos dados (``estadoV``):
  // o editor também solta (Ctrl+X, Ctrl+V), confirma e cancela por fora.
  const V = { quad: null, base: null, sujo: false, modo: null, recorte: false, arrastando: false, flut: null, buraco: null,
    cam: null, aplicando: false };
  const estadoV = () => (V.aplicando ? "aplicando" : V.cam ? "movendo_camada" : !V.quad ? "sem_selecao"
    : V.arrastando ? "arrastando" : (V.sujo || V.recorte) ? "solto" : "pronta");
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
  // Alt no começo do arraste: no miolo, duplica (o Mover do Photoshop); num canto ou lado, escala a partir do centro
  const pegarQuad = (t, p, ev) => {
    const pegou = quadV.down(p, ev);
    if (!V.sujo && ev.altKey && pegou === "mover") { V.modo = "duplicar"; V.buraco = null; }
    V.arrastando = true;
    return "arrastando";
  };
  const pegarCamada = (t, p) => {
    const at = app.camadaAtiva();
    if (!at || at.vazia) { app.aviso("faça uma seleção (ou escolha uma camada com conteúdo) para mover", "erro"); return undefined; }
    V.cam = { a: p, d: { x: 0, y: 0 }, bbox: at.bbox, id: at.id };
    return "movendo_camada";
  };
  F.transformar = nova({
    nome: "Mover / puxar (V): arraste a seleção para puxar os pixels (Alt = duplicar); cantos escalam, fora gira; sem seleção, move a CAMADA ativa",
    icone: "✥", atalho: "v", cursor: "move", inicial: "sem_selecao", repouso: ["sem_selecao", "pronta"],
    opcoes: [
      { chave: "acao", tipo: "select", rotulo: "Ao arrastar", opcoes: [["mover", "puxar (o buraco é preenchido)"], ["duplicar", "duplicar (ou Alt+arrastar)"]], padrao: "mover" },
      { chave: "origem", tipo: "select", rotulo: "Buraco", opcoes: [["preencher", "preencher com o papel em volta"], ["cor", "cor de fundo"], ["nada", "deixar como está"]], padrao: "preencher" },
      { chave: "interpolacao", tipo: "select", rotulo: "Interp.", opcoes: [["bicubica", "bicúbica"], ["bilinear", "bilinear"], ["vizinho", "vizinho"]], padrao: "bicubica" },
      semente, "|",
      { rotulo: "Aplicar (Enter)", primario: true, acao: () => F.transformar.confirmar(true) },
      { rotulo: "Cancelar (Esc)", acao: () => F.transformar.cancelar() },
    ],
    estados: {
      sem_selecao: { pressionar: pegarCamada },
      movendo_camada: {
        pressionar: pegarCamada,  // o soltar anterior se perdeu: recomeça daqui
        mover: (t, p, ev) => {  // Shift prende no eixo
          let dx = Math.round(p.x - V.cam.a.x), dy = Math.round(p.y - V.cam.a.y);
          if (ev.shiftKey) { if (Math.abs(dx) >= Math.abs(dy)) dy = 0; else dx = 0; }
          V.cam.d = { x: dx, y: dy };
          V.cam.mouse = app.visor.imgParaTela(p.x, p.y);
          app.visor.redesenhar();
        },
        soltar: () => {
          const c = V.cam;
          V.cam = null;
          if (c.d.x || c.d.y) app.acaoCamada("mover", { camada: c.id, dx: c.d.x, dy: c.d.y });
          app.visor.redesenhar();
          return estadoV();
        },
      },
      pronta: { pressionar: pegarQuad },
      solto: { pressionar: pegarQuad },
      aplicando: { pressionar: pegarQuad },
      arrastando: {
        pressionar: pegarQuad,  // o soltar anterior se perdeu: recomeça daqui
        mover: (t, p, ev) => { quadV.move(p, ev); },
        soltar: () => { V.arrastando = false; quadV.up(); app.visor.redesenhar(); return estadoV(); },
      },
    },
    sempre: {
      soltar: () => { V.arrastando = false; quadV.up(); app.visor.redesenhar(); return estadoV(); },
      tecla: (t, ev) => {
        if (ev.key === "Enter") { F.transformar.confirmar(true); return undefined; }
        if (ev.key === "Escape" && V.quad) { F.transformar.cancelar(); return undefined; }
        if (!V.quad) {  // sem seleção: setas empurram a camada ativa
          const at = app.camadaAtiva(), m = SETAS[ev.key], passo = ev.shiftKey ? 10 : 1;
          if (m && at && !at.vazia) { app.acaoCamada("mover", { camada: at.id, dx: m[0] * passo, dy: m[1] * passo }); return undefined; }
          return false;
        }
        return quadV.tecla(ev) ? estadoV() : false;
      },
      opcao: () => { V.buraco = null; previaV(); return estadoV(); },
      ativar: (t, origem) => { if (!V.quad || origem === "documento") iniciarQuad(); previaV(); return estadoV(); },
      desativar: () => {
        V.quad = null; V.sujo = V.recorte = V.arrastando = false; V.cam = null;
        app.visor.esconderSelecao = false; app.limparPrevia();
        return estadoV();
      },
      selecao: () => { iniciarQuad(); previaV(); return estadoV(); },
    },
    dicas: {
      sem_selecao: [h("", "arrastar", "mover a camada ativa"), h("setas", "", "empurrar 1 px (Shift 10)")],
      movendo_camada: [h("Shift", "", "só na horizontal ou na vertical"), h("", "soltar", "deixar a camada aqui")],
      pronta: [h("", "arrastar", "puxar"), h("Alt", "arrastar", "duplicar"), h("", "arrastar canto", "escala"),
        h("", "arrastar lado", "esticar"), h("", "arrastar fora", "girar"), h("setas", "", "1 px")],
      arrastando: () => dicasDoArraste(quadV.pegou(), modoV() === "duplicar" ? "deixar a cópia aqui" : "deixar o trecho aqui"),
      solto: [h("Enter", "", "aplicar"), h("Esc", "", "cancelar"), h("", "arrastar", "ajustar"), h("", "arrastar canto", "escala"),
        h("", "arrastar lado", "esticar"), h("", "arrastar fora", "girar"), h("setas", "", "1 px (Shift 10)")],
      aplicando: [h("", "", "aplicando…")],
    },
    cursorEm: (p) => quadV.cursor(p),
    desenhar(ctx, v) {
      if (V.quad) {
        if ((V.sujo || V.recorte) && !v.previa) desenharSolto(ctx, v);
        desenharQuad(ctx, v, V.quad, V.sujo || V.recorte);
        if (V.arrastando) leitura(ctx, quadV.mouse(), quadV.medida());
      }
      if (V.cam && V.cam.bbox) {
        const [x0, y0, x1, y1] = V.cam.bbox, d = V.cam.d;
        const a = v.imgParaTela(x0 + d.x, y0 + d.y), b = v.imgParaTela(x1 + d.x, y1 + d.y);
        contorno(ctx, () => { ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); }, true);
        leitura(ctx, V.cam.mouse, `Δ ${d.x}, ${d.y} px`);
      }
    },
    api: (t) => ({
      pendente: () => Boolean(V.quad && (V.sujo || V.recorte) && !V.aplicando),
      solto: () => (V.quad ? { modo: modoV(), recorte: V.recorte, sujo: V.sujo, arrastando: V.arrastando, angulo: graus(V.quad.ang) } : null),
      ancora() {
        if (!V.quad) return null;
        const c = V.quad.cantos();
        return [Math.min(...c.map((p) => p.x)), Math.min(...c.map((p) => p.y)), Math.max(...c.map((p) => p.x)), Math.max(...c.map((p) => p.y))];
      },
      // deixa a seleção solta para puxar (Ctrl+X = recortar; Ctrl+V no mesmo documento = duplicar, um pouco deslocada)
      soltar({ recorte = false, modo = null, deslocar = 0 } = {}) {
        iniciarQuad();
        if (!V.quad) { t.ir(estadoV()); return false; }
        V.recorte = recorte; V.modo = modo;
        if (deslocar) { V.quad.mover(deslocar, deslocar); V.sujo = true; }
        previaV();
        t.ir(estadoV());
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
        t.ir(estadoV());
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
        } finally { V.aplicando = false; t.ir(estadoV()); }
      },
      cancelar() {
        const tinha = V.sujo || V.recorte;
        iniciarQuad(); previaV();
        t.ir(estadoV());
        if (tinha) app.status("puxar cancelado: o trecho voltou ao lugar");
      },
    }),
  });

  // ------------------------------------------------------------- colar (Ctrl+V de outro documento ou o diálogo do doador)
  // Estados: "vazio" (nada para colar: o clique abre a escolha do trecho) → "posicionando" ⇄ "arrastando" →
  // "aplicando". Como no Mover, o estado sai dos dados (``estadoC``).
  const C = { quad: null, doador: null, origem: null, selecao: null, pre: null, arrastando: false, aplicando: false };
  const estadoC = () => (C.aplicando ? "aplicando" : !C.quad ? "vazio" : C.arrastando ? "arrastando" : "posicionando");
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
  const pegarC = (t, p, ev) => { C.arrastando = true; quadC.down(p, ev); return estadoC(); };
  F.colar = nova({
    nome: "Colar de outro documento do MESMO split (V; Shift+V alterna. Ctrl+V depois de copiar em outra imagem, ou escolha o trecho)", icone: "⧉", atalho: "v", cursor: "move",
    inicial: "vazio", repouso: ["vazio", "posicionando"],
    opcoes: [
      { chave: "suavizar_borda", tipo: "num", rotulo: "Suavizar borda px", min: 0, max: 50, passo: 0.5, padrao: 0 },
      { chave: "interpolacao", tipo: "select", rotulo: "Interp.", opcoes: [["bicubica", "bicúbica"], ["bilinear", "bilinear"], ["vizinho", "vizinho"]], padrao: "bicubica" },
      { chave: "limitar_selecao", tipo: "bool", rotulo: "Só dentro da seleção", padrao: false }, "|",
      { rotulo: "Escolher trecho…", acao: () => app.escolherDoador() },
      { rotulo: "Aplicar (Enter)", primario: true, acao: () => F.colar.confirmar(true) },
      { rotulo: "Cancelar (Esc)", acao: () => F.colar.cancelar() },
    ],
    estados: {
      vazio: { pressionar: () => { app.escolherDoador(); } },
      posicionando: { pressionar: pegarC },
      arrastando: { pressionar: pegarC },
      aplicando: { pressionar: pegarC },
    },
    sempre: {
      mover: (t, p, ev) => { quadC.move(p, ev); },
      soltar: () => { C.arrastando = false; quadC.up(); app.visor.redesenhar(); return estadoC(); },
      tecla: (t, ev) => {
        if (ev.key === "Enter") { F.colar.confirmar(true); return undefined; }
        if (ev.key === "Escape" && C.quad) { F.colar.cancelar(); return undefined; }
        return quadC.tecla(ev) ? estadoC() : false;
      },
      opcao: () => { previaC(); },
      ativar: (t, origem) => { if (C.quad) previaC(); else if (origem === "usuario") app.escolherDoador(); return estadoC(); },
      desativar: () => { C.quad = null; C.pre = null; C.arrastando = false; app.limparPrevia(); return estadoC(); },
    },
    dicas: {
      vazio: [h("", "clique", "escolher o trecho de outro documento"), h("Ctrl+V", "", "colar o que copiou")],
      posicionando: [h("", "arrastar", "posicionar"), h("", "arrastar canto", "escala"), h("", "arrastar lado", "esticar"),
        h("", "arrastar fora", "girar"), h("Enter", "", "aplicar"), h("Esc", "", "cancelar")],
      arrastando: () => dicasDoArraste(quadC.pegou(), "deixar o trecho aqui"),
      aplicando: [h("", "", "colando…")],
    },
    cursorEm: (p) => quadC.cursor(p),
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
      desenharQuad(ctx, v, C.quad, true);  // tracejado: o trecho colado ainda está solto
      if (C.arrastando) leitura(ctx, quadC.mouse(), quadC.medida());
    },
    api: (t) => ({
      // trecho do doador (retângulo ``origem``; com ``selecao``, só o formato copiado). ``pre`` = os pixels, para a
      // prévia na hora; ``centro`` = onde o trecho foi solto; ``emLugar`` = nas mesmas coordenadas (se couber); sem
      // nenhum dos dois, no meio da vista
      iniciar(doador, origem, { selecao = null, pre = null, emLugar = false, centro = null } = {}) {
        Object.assign(C, { doador, origem, selecao, pre });
        const w = origem.x1 - origem.x0, hh = origem.y1 - origem.y0;
        if (centro) {
          C.quad = new Quad(centro.x - w / 2, centro.y - hh / 2, centro.x + w / 2, centro.y + hh / 2);
        } else if (emLugar && app.sessao && origem.x1 <= app.sessao.largura && origem.y1 <= app.sessao.altura) {
          C.quad = new Quad(origem.x0, origem.y0, origem.x1, origem.y1);
        } else {
          const v = app.visor, r = v.el.getBoundingClientRect();
          const c = v.telaParaImg(r.width / 2, r.height / 2);
          C.quad = new Quad(c.x - w / 2, c.y - hh / 2, c.x + w / 2, c.y + hh / 2);
        }
        previaC();
        t.ir(estadoC());
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
        t.ir(estadoC());
        try {
          const ok = await app.aplicar("colar", paramsC(), { msg: "colando…" });
          if (ok) { C.quad = null; C.pre = null; app.visor.redesenhar(); }
          return ok;
        } finally { C.aplicando = false; t.ir(estadoC()); }
      },
      cancelar() { C.quad = null; C.pre = null; app.limparPrevia(); app.visor.redesenhar(); t.ir(estadoC()); },
    }),
  });

  return F;
}

// a caixa de ferramentas em grupos, como no Photoshop: o botão mostra a última usada do grupo; clique com o botão
// direito (ou segure) para ver as outras
export const GRUPOS = [
  ["transformar", "colar"], ["ret", "elipse"], ["laco"], ["caneta"], ["varinha", "pincelsel"], ["contagotas"], "|",
  ["corretivo", "recuperacao", "remendo"], ["pincel"], ["carimbo"], ["borracha"], ["balde"], ["retoque"], "|",
  ["texto", "substituir"], ["forma"], "|", ["mao"], ["zoom"],
];
export const ICONES = {
  transformar: "mover", colar: "colar_doc", ret: "ret", elipse: "elipse", laco: "laco", caneta: "caneta", varinha: "varinha",
  pincelsel: "pincelsel", contagotas: "contagotas", corretivo: "corretivo", recuperacao: "recuperacao", remendo: "remendo",
  pincel: "pincel", carimbo: "carimbo", borracha: "borracha", balde: "balde", retoque: "retoque", texto: "texto",
  substituir: "substituir", forma: "forma", mao: "mao", zoom: "zoom",
};
export const NOMES = {
  transformar: "Mover / puxar", colar: "Colar de outro documento", ret: "Seleção retangular", elipse: "Seleção elíptica",
  laco: "Laço", caneta: "Caneta (seleção por caminho)", varinha: "Varinha mágica", pincelsel: "Pincel de seleção", contagotas: "Conta-gotas",
  corretivo: "Pincel corretivo", recuperacao: "Pincel de recuperação", remendo: "Remendo", pincel: "Pincel",
  carimbo: "Carimbo de clonagem", borracha: "Borracha", balde: "Balde de tinta", retoque: "Retoque", texto: "Texto",
  substituir: "Substituir texto", forma: "Formas", mao: "Mão", zoom: "Zoom",
};
