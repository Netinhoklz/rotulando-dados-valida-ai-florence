// Ícones das ferramentas, menus e barras: formas cheias em DOIS TONS na grade de 24 x 24, no estilo dos ícones do
// Graphite (a parte principal na cor do texto; a secundária, com opacidade .45). Desenhados para o rotulador: os
// ícones do Graphite são proprietários ("Graphite Branding License": proibido copiar ou usar fora do programa
// oficial), então nenhum arquivo, traço ou coordenada deles foi usado aqui.

const T2 = 'opacity=".45"';  // o segundo tom
const LINHA = 'fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"';

const D = {
  // ------------------------------------------------------------------ ferramentas (24 px)
  mover: `<path d="M5 2.5v17l4.3-3.9 2.9 6.2 2.8-1.3-2.8-6.1H18Z"/><path ${T2} d="M15 3h6v6h-2V5h-4Z"/>`,
  colar_doc: `<path ${T2} d="M3 2h9l3 3v3H10a2.5 2.5 0 0 0-2.5 2.5V17H3Z"/><path d="M9.5 9.5h8L21 13v9H9.5Z"/>`
    + `<path ${T2} d="M17.5 9.5V13H21Z"/>`,
  ret: `<rect x="3.5" y="5.5" width="17" height="13" opacity=".25"/>`
    + `<rect x="3.5" y="5.5" width="17" height="13" ${LINHA} stroke-width="2" stroke-dasharray="3.4 2.3" stroke-linecap="butt"/>`,
  elipse: `<ellipse cx="12" cy="12" rx="9" ry="7" opacity=".25"/>`
    + `<ellipse cx="12" cy="12" rx="9" ry="7" ${LINHA} stroke-width="2" stroke-dasharray="3.4 2.3" stroke-linecap="butt"/>`,
  laco: `<ellipse cx="13" cy="9.5" rx="8" ry="5.5" opacity=".25"/>`
    + `<path ${LINHA} stroke-width="2" d="M8.3 14.3C5.7 13.4 4 11.6 4 9.5 4 6.5 8 4 13 4s9 2.5 9 5.5-4 5.5-9 5.5c-.9 0-1.8-.1-2.6-.2"/>`
    + `<circle cx="7.6" cy="16.4" r="2.3"/><path ${LINHA} stroke-width="1.8" d="M6.7 18.5c-.3 1.4.2 2.6 1.4 3.5"/>`,
  // a pena da caneta com a ponta para baixo e, atrás, um caminho com dois pontos (desenho nosso)
  caneta: `<path ${LINHA} stroke-width="1.6" ${T2} d="M2.5 20.5C5 13 9 21.5 12 21.5"/>`
    + `<path fill-rule="evenodd" d="M12 21 7 12.2 9.4 6h5.2l2.4 6.2ZM12 10.2a1.7 1.7 0 1 0 0 3.4 1.7 1.7 0 1 0 0-3.4Z"/>`
    + `<path ${T2} d="M9 2.5h6V5H9Z"/><rect x="1" y="19" width="3" height="3" ${T2}/>`,
  varinha: `<path d="M3.3 19.3 14.6 8l2 2L5.3 21.3a1 1 0 0 1-1.4 0l-.6-.6a1 1 0 0 1 0-1.4Z"/>`
    + `<path ${T2} d="m14.6 8 2-2 2 2-2 2Z"/><path d="m18.5 1.5.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8Z"/>`
    + `<path ${T2} d="m9 2 .5 1.5L11 4l-1.5.5L9 6l-.5-1.5L7 4l1.5-.5ZM20 12l.5 1.5 1.5.5-1.5.5-.5 1.5-.5-1.5-1.5-.5 1.5-.5Z"/>`,
  pincelsel: `<path d="M19.6 2.6a1.7 1.7 0 0 1 2.4 2.4l-8.6 8.6-2.4-2.4Z"/>`
    + `<path ${T2} d="m10.2 11.9 2.4 2.4c-.6 2.4-2.7 3.9-5.4 3.5.9-1.5.4-4.4 3-5.9Z"/>`
    + `<path ${LINHA} stroke-width="2" stroke-dasharray="2 2.5" d="M2.5 21.5c2.6-.8 3.6-2.6 6-2.9"/>`,
  contagotas: `<path d="M14.5 3.6a3 3 0 0 1 4.2 0l1.7 1.7a3 3 0 0 1 0 4.2l-1.6 1.6.9.9-1.8 1.8-6.3-6.3 1.8-1.8.9.9Z"/>`
    + `<path ${T2} d="m10.4 9.2 4.4 4.4-7.6 7.6c-.5.5-1.2.7-1.9.6l-1.4.9-.8-.8.9-1.4c-.1-.7.1-1.4.6-1.9Z"/>`,
  corretivo: `<path ${T2} d="M3.6 14.5 14.5 3.6a3.5 3.5 0 0 1 5 0l.9.9a3.5 3.5 0 0 1 0 5L9.5 20.4a3.5 3.5 0 0 1-5 0l-.9-.9a3.5 3.5 0 0 1 0-5Z"/>`
    + `<path d="M8.5 12 12 8.5l3.5 3.5-3.5 3.5Z"/>`,
  recuperacao: `<path ${T2} d="M3.6 14.5 14.5 3.6a3.5 3.5 0 0 1 5 0l.9.9a3.5 3.5 0 0 1 0 5L9.5 20.4a3.5 3.5 0 0 1-5 0l-.9-.9a3.5 3.5 0 0 1 0-5Z"/>`
    + `<path d="M11 8h2v3h3v2h-3v3h-2v-3H8v-2h3Z"/>`,
  remendo: `<rect x="2.5" y="2.5" width="10" height="10" rx="1" ${LINHA} stroke-width="1.8" stroke-dasharray="2.6 2" stroke-linecap="butt"/>`
    + `<rect x="11.5" y="11.5" width="10" height="10" rx="1.5"/><path ${T2} d="M15 4h2.5a2 2 0 0 1 2 2v3.5h-2V6H15Z"/>`,
  pincel: `<path d="M19.8 2.6c.7-.6 1.7-.5 2.2.1s.5 1.5 0 2.1l-8 8.6-2.9-2.9Z"/>`
    + `<path ${T2} d="m10.2 11.4 2.4 2.4c-.4 3.5-3.2 6.7-9.6 7.2 1.6-1.4 1.9-3.3 2.5-5.1.8-2.6 2.6-4.3 4.7-4.5Z"/>`,
  carimbo: `<path d="M9 4.5a3 3 0 0 1 6 0c0 1.8-1 2.7-1 4.5v2.5h-4V9c0-1.8-1-2.7-1-4.5Z"/>`
    + `<path ${T2} d="M5 12.5h14a1.5 1.5 0 0 1 1.5 1.5v3h-17v-3A1.5 1.5 0 0 1 5 12.5Z"/><rect x="3" y="19" width="18" height="2.5" rx="1"/>`,
  borracha: `<path d="m14.6 2.6 6.8 6.8-8 8-6.8-6.8Z"/>`
    + `<path ${T2} d="m6.6 10.6 6.8 6.8-2.6 2.6H6.2L3 16.8a1.5 1.5 0 0 1 0-2.1Z"/><rect x="12" y="19.6" width="9.5" height="1.9" rx=".9"/>`,
  balde: `<path ${T2} d="m10.5 2.8 9.2 9.2-7.4 7.4a2 2 0 0 1-2.8 0l-5.9-5.9a2 2 0 0 1 0-2.8Z"/>`
    + `<path d="M3.2 12.1h16.5l-7.4 7.3a2 2 0 0 1-2.8 0l-5.9-5.9a2 2 0 0 1-.4-1.4Z"/>`
    + `<path d="M21 14.5s2 2.6 2 3.8a2 2 0 0 1-4 0c0-1.2 2-3.8 2-3.8Z"/>`,
  retoque: `<path ${T2} d="M12 2.5c2.5 4 6.5 7.6 6.5 11.5a6.5 6.5 0 0 1-13 0c0-3.9 4-7.5 6.5-11.5Z"/>`
    + `<path d="M12 2.5v18A6.5 6.5 0 0 1 5.5 14c0-3.9 4-7.5 6.5-11.5Z"/>`,
  texto: `<path d="M4 3h16v5h-2.5l-.6-2.3h-3.6v12.8l2.2.6V21h-7v-1.9l2.2-.6V5.7H7.1L6.5 8H4Z"/>`,
  substituir: `<path d="M2.5 2.5h10V6h-1.8l-.4-1.5H8.5v8.3l1.4.4v1.3H5.1v-1.3l1.4-.4V4.5H4.7L4.3 6H2.5Z"/>`
    + `<path ${T2} d="M12.5 16.5h6.7l-1.6-1.6L19 13.5l4 4-4 4-1.4-1.4 1.6-1.6h-6.7Z"/>`,
  forma: `<rect ${T2} x="2.5" y="2.5" width="12" height="12" rx="1.5"/><circle cx="15.5" cy="15.5" r="6"/>`,
  mao: `<path d="M8.5 4.8a1.6 1.6 0 0 1 3.2 0V11h.6V3.6a1.6 1.6 0 0 1 3.2 0V11h.6V5.4a1.6 1.6 0 0 1 3.2 0v8.8c0 4.3-3 7.8-7.4 `
    + `7.8-2.6 0-4.4-1-5.9-2.9l-3.2-4.2a1.6 1.6 0 0 1 2.5-2l1.9 2.3h.7Z"/>`,
  zoom: `<circle cx="10" cy="10" r="6.3" opacity=".3"/>`
    + `<path fill-rule="evenodd" d="M10 2a8 8 0 1 1 0 16 8 8 0 0 1 0-16Zm0 2a6 6 0 1 0 0 12 6 6 0 0 0 0-12Z"/>`
    + `<path d="m15.3 13.9 6.4 6.4-1.4 1.4-6.4-6.4Z"/>`,
  // ------------------------------------------------------------------ ações e painéis (16 px)
  recortar: `<path fill-rule="evenodd" d="M6 2.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Zm0 2a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z"/>`
    + `<path fill-rule="evenodd" d="M6 14.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Zm0 2a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z"/>`
    + `<path d="m8.6 8.1 12.9 11.5-1.3 1.5L7.3 9.6Z"/><path ${T2} d="M8.6 15.9 21.5 4.4l-1.3-1.5L7.3 14.4Z"/>`,
  copiar: `<rect ${T2} x="2.5" y="2.5" width="13" height="13" rx="2"/><rect x="8.5" y="8.5" width="13" height="13" rx="2"/>`,
  colar: `<path ${T2} d="M6 4h2v2h8V4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/>`
    + `<rect x="8" y="2" width="8" height="5" rx="1"/>`,
  apagar: `<rect x="3" y="3" width="18" height="18" rx="2" ${LINHA} stroke-width="1.8" stroke-dasharray="3 2.4" stroke-linecap="butt"/>`
    + `<path d="m12 6.5 1.5 4 4 1.5-4 1.5-1.5 4-1.5-4-4-1.5 4-1.5Z"/>`,
  dado: `<rect ${T2} x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.8"/><circle cx="16" cy="8" r="1.8"/>`
    + `<circle cx="12" cy="12" r="1.8"/><circle cx="8" cy="16" r="1.8"/><circle cx="16" cy="16" r="1.8"/>`,
  ajustes: `<path ${T2} d="M4 3h2v18H4ZM11 3h2v18h-2ZM18 3h2v18h-2Z"/>`
    + `<rect x="2" y="12" width="6" height="3.5" rx="1"/><rect x="9" y="6" width="6" height="3.5" rx="1"/><rect x="16" y="14.5" width="6" height="3.5" rx="1"/>`,
  inverter: `<rect ${T2} x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7Z"/>`,
  fechar: `<path d="m5.6 4.2 6.4 6.4 6.4-6.4 1.4 1.4-6.4 6.4 6.4 6.4-1.4 1.4-6.4-6.4-6.4 6.4-1.4-1.4 6.4-6.4-6.4-6.4Z"/>`,
  ok: `<path d="M9 15.2 19.6 4.6 21 6 9 18l-6-6 1.4-1.4Z"/>`,
  desfazer: `<path d="M7.8 5.3 9.2 6.7 6.9 9H13a6 6 0 0 1 0 12H9v-2h4a4 4 0 0 0 0-8H6.9l2.3 2.3-1.4 1.4L3 10Z"/>`,
  refazer: `<path d="m16.2 5.3-1.4 1.4L17.1 9H11a6 6 0 0 0 0 12h4v-2h-4a4 4 0 0 1 0-8h6.1l-2.3 2.3 1.4 1.4L21 10Z"/>`,
  olho: `<path ${T2} d="M12 5c5.5 0 9.3 4.1 10.5 7-1.2 2.9-5 7-10.5 7S2.7 14.9 1.5 12C2.7 9.1 6.5 5 12 5Z"/><circle cx="12" cy="12" r="3.6"/>`,
  olho_fechado: `<path ${T2} d="M12 5c5.5 0 9.3 4.1 10.5 7-1.2 2.9-5 7-10.5 7S2.7 14.9 1.5 12C2.7 9.1 6.5 5 12 5Z"/>`
    + `<path d="m3.7 2.3 18 18-1.4 1.4-18-18Z"/>`,
  mais: `<path d="M11 4h2v7h7v2h-7v7h-2v-7H4v-2h7Z"/>`,
  duplicar: `<rect ${T2} x="2.5" y="2.5" width="12" height="12" rx="2"/>`
    + `<path fill-rule="evenodd" d="M11.5 9.5h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Zm3 2.5v2.75h-2.75v1.5h2.75V19H16v-2.75h2.75v-1.5H16V12Z"/>`,
  mesclar: `<path ${T2} d="M3 19h18v2.5H3Z"/><path d="M11 2.5h2v9.7l3.3-3.3 1.4 1.4L12 16l-5.7-5.7 1.4-1.4 3.3 3.3Z"/>`,
  lixeira: `<path d="M9 2h6l1 2h4.5v2h-17V4H8Z"/><path ${T2} d="M5 7.5h14l-1 12.6a2 2 0 0 1-2 1.9H8a2 2 0 0 1-2-1.9Z"/>`,
  cadeado: `<path ${T2} d="M7.5 10V7a4.5 4.5 0 0 1 9 0v3h-2V7a2.5 2.5 0 0 0-5 0v3Z"/><rect x="4" y="10" width="16" height="11.5" rx="2"/>`,
  camadas: `<path ${T2} d="m12 10.8 10 4.7-10 4.7-10-4.7Z"/><path d="m12 3 10 4.8-10 4.8L2 7.8Z"/>`,
  historico: `<path d="M12 3a9 9 0 1 1-8.5 12l1.9-.6A7 7 0 1 0 6.3 7.5L8.8 10H2.5V3.7l2.4 2.4A9 9 0 0 1 12 3Z"/>`
    + `<path ${T2} d="M11 7h2v4.6l3.3 2-1 1.7-4.3-2.6Z"/>`,
  etiqueta: `<path fill-rule="evenodd" d="M3 3h8.2l9.6 9.6a2 2 0 0 1 0 2.8l-5.4 5.4a2 2 0 0 1-2.8 0L3 11.2Zm4.5 2.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z"/>`,
  barras: `<path d="M3 5h1.6v14H3ZM6.2 5h2.6v14H6.2ZM10.4 5H12v14h-1.6ZM13.6 5h2.6v14h-2.6ZM17.8 5h1.6v14h-1.6ZM20.4 5H21v14h-.6Z"/>`,
  qr: `<path fill-rule="evenodd" d="M3 3h8v8H3Zm2 2v4h4V5ZM13 3h8v8h-8Zm2 2v4h4V5ZM3 13h8v8H3Zm2 2v4h4v-4Z"/>`
    + `<path ${T2} d="M13 13h3v3h-3ZM18 13h3v3h-3ZM13 18h3v3h-3ZM18 18h3v3h-3Z"/><path d="M15.5 15.5h3v3h-3Z"/>`,
  ia: `<path d="m10 2 2 6 6 2-6 2-2 6-2-6-6-2 6-2Z"/><path ${T2} d="m18.5 14 1 3 3 1-3 1-1 3-1-3-3-1 3-1Z"/>`,
  fonte: `<path fill-rule="evenodd" d="M7.2 4h2.6l5.4 16h-2.4l-1.4-4.3H5.6L4.2 20H1.8Zm-.9 9.7h4.4L8.5 7Z"/>`
    + `<path ${T2} fill-rule="evenodd" d="M18 11a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Zm0 2a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z"/>`
    + `<rect ${T2} x="20.5" y="11" width="2" height="9"/>`,
  salvar: `<path ${T2} d="M4 3h12.2L21 7.8V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4a1 1 0 0 1 1-1Z"/><path d="M7 3h8v5H7ZM6.5 12.5h11V21h-11Z"/>`,
  anterior: `<path d="m15.4 4.6 1.4 1.4-6 6 6 6-1.4 1.4L8 12Z"/>`,
  proximo: `<path d="M8.6 4.6 7.2 6l6 6-6 6 1.4 1.4L16 12Z"/>`,
  fabrica: `<path fill-rule="evenodd" d="M2 21V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v8.5L14 8v4.5L20 8v13ZM5 16v2h3v-2Zm5.5 0v2h3v-2Zm5.5 0v2h3v-2Z"/>`,
  ajuda: `<circle ${T2} cx="12" cy="12" r="10"/><path d="M12 6.5c2.2 0 3.8 1.4 3.8 3.3 0 1.6-1 2.4-2 3-.6.4-.8.7-.8 1.3v.4h-2v-.6c0-1.3.6-2 `
    + `1.6-2.6.7-.4 1.2-.8 1.2-1.5 0-.8-.7-1.4-1.8-1.4s-1.9.7-2 1.7l-2-.2c.2-2.1 1.8-3.4 4-3.4ZM11 16.5h2v2h-2Z"/>`,
  livro: `<path ${T2} d="M6.5 2H20v15H6.5A2.5 2.5 0 0 0 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/>`
    + `<path fill-rule="evenodd" d="M6.5 17H20v5H6.5a2.5 2.5 0 0 1 0-5Zm0 1.8a.7.7 0 0 0 0 1.4H18v-1.4Z"/>`,
  teclado: `<rect ${T2} x="2" y="5" width="20" height="14" rx="2"/>`
    + `<path d="M5 8h2v2H5ZM9 8h2v2H9ZM13 8h2v2h-2ZM17 8h2v2h-2ZM5 12h2v2H5ZM9 12h2v2H9ZM13 12h2v2h-2ZM17 12h2v2h-2ZM7 15.5h10V17H7Z"/>`,
  original: `<rect ${T2} x="3" y="3" width="18" height="18" rx="2"/><path d="M5 3h14a2 2 0 0 1 2 2v6H3V5a2 2 0 0 1 2-2Z"/>`,
  mascara: `<rect ${T2} x="3" y="3" width="18" height="18" rx="2"/>`
    + `<path d="m3 17.5 5-6 4 4.5 3-3.5 6 6V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><circle cx="16" cy="8" r="2"/>`,
  mouse: `<path fill-rule="evenodd" d="M12 2a6 6 0 0 1 6 6v8a6 6 0 0 1-12 0V8a6 6 0 0 1 6-6Zm0 2a4 4 0 0 0-4 4v8a4 4 0 0 0 8 0V8a4 4 0 0 0-4-4Z"/>`
    + `<path d="M11.2 3h1.6v6.5h-1.6Z"/>`,
  logo: `<rect ${T2} x="2.5" y="2.5" width="19" height="19" rx="4.5"/><path d="M6.5 7h11v2h-11ZM6.5 11h7v2h-7ZM6.5 15h9v2h-9Z"/>`,
};

// <svg> pronto para pôr num botão; tamanho em px
export function icone(nome, tam = 18) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("width", tam); s.setAttribute("height", tam);
  s.setAttribute("fill", "currentColor");
  s.setAttribute("aria-hidden", "true");
  s.classList.add("ico");
  s.innerHTML = D[nome] || D.dado;
  return s;
}

// mouse com o botão esquerdo aceso (dicas da barra de status)
export function mouseEsquerdo(tam = 14) {
  const s = icone("mouse", tam);
  s.insertAdjacentHTML("beforeend", '<path d="M11.2 4.1A4 4 0 0 0 8 8v1.5h3.2Z"/>');
  return s;
}

export const TEM = (nome) => nome in D;
