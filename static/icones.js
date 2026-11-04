// Ícones de linha (24x24, traço na cor do texto) das ferramentas, menus e barras. Vários seguem o desenho do
// Lucide (https://lucide.dev, licença ISC: Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as
// part of Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022).

const D = {
  mover: '<path d="M12 2v20M2 12h20"/><path d="m9 5 3-3 3 3M9 19l3 3 3-3M5 9l-3 3 3 3M19 9l3 3-3 3"/>',
  colar_doc: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v5M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6"/><path d="M14 17h8M19 14l3 3-3 3"/>',
  ret: '<rect x="3.5" y="5.5" width="17" height="13" rx="0.5" stroke-dasharray="3 2.4"/>',
  elipse: '<ellipse cx="12" cy="12" rx="9" ry="7" stroke-dasharray="3 2.4"/>',
  laco: '<path d="M7 22a5 5 0 0 1-2-4"/><path d="M3.3 14A6.8 6.8 0 0 1 2 10c0-4.4 4.5-8 10-8s10 3.6 10 8-4.5 8-10 8a12 12 0 0 1-5-1"/><circle cx="5" cy="16" r="2"/>',
  varinha: '<path d="m21.6 3.6-1.2-1.2a1.2 1.2 0 0 0-1.7 0L2.4 18.6a1.2 1.2 0 0 0 0 1.7l1.3 1.3a1.2 1.2 0 0 0 1.7 0L21.6 5.4a1.2 1.2 0 0 0 0-1.8Z"/><path d="m14 7 3 3M5 6v4M19 14v4M10 2v2M7 8H3M21 16h-4M11 3H9"/>',
  pincelsel: '<path d="m9.1 11.9 8-8a2.9 2.9 0 1 1 4 4l-8 8.1"/><path d="M7 15c-1.7 0-3 1.3-3 3 0 1.3-2.5 1.5-2 2 1.1 1.1 2.5 2 4 2 2.2 0 4-1.8 4-4a3 3 0 0 0-3-3Z" stroke-dasharray="2.5 2"/>',
  contagotas: '<path d="m2 22 1-1h3l9-9"/><path d="M3 21v-3l9-9"/><path d="m15 6 3.4-3.4a2.1 2.1 0 1 1 3 3L18 9l.4.4a2.1 2.1 0 1 1-3 3l-3.8-3.8a2.1 2.1 0 1 1 3-3l.4.4Z"/>',
  corretivo: '<rect x="2" y="7" width="20" height="10" rx="5" transform="rotate(-35 12 12)"/><path d="M10.5 10.5h.01M13.5 13.5h.01M13.5 10.5h.01M10.5 13.5h.01"/>',
  recuperacao: '<rect x="2" y="7" width="20" height="10" rx="5" transform="rotate(-35 12 12)"/><path d="M12 9.5v5M9.5 12h5"/>',
  remendo: '<rect x="3" y="3" width="9" height="9" rx="1" stroke-dasharray="2.5 2"/><rect x="12" y="12" width="9" height="9" rx="1"/><path d="M8 16h-1.5a2 2 0 0 1-2-2v-1M16 8v-1.5a2 2 0 0 0-2-2h-1"/>',
  pincel: '<path d="m9.1 11.9 8-8a2.9 2.9 0 1 1 4 4l-8 8.1"/><path d="M7 15c-1.7 0-3 1.3-3 3 0 1.3-2.5 1.5-2 2 1.1 1.1 2.5 2 4 2 2.2 0 4-1.8 4-4a3 3 0 0 0-3-3Z"/>',
  carimbo: '<path d="M5 22h14"/><path d="M19.3 13.7a2.5 2.5 0 0 0-1.8-.7h-11A2.5 2.5 0 0 0 4 15.5V17a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1.5c0-.7-.3-1.3-.7-1.8Z"/><path d="M14 13V8.5C14 7 15 7 15 5a3 3 0 0 0-3-3c-1.7 0-3 1-3 3s1 2 1 3.5V13"/>',
  borracha: '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7M5 11l9 9"/>',
  balde: '<path d="m19 11-8-8-8.6 8.6a2 2 0 0 0 0 2.8l5.2 5.2c.8.8 2 .8 2.8 0L19 11Z"/><path d="m5 2 5 5M2 13h15"/><path d="M22 20a2 2 0 1 1-4 0c0-1.6 1.7-2.4 2-4 .3 1.6 2 2.4 2 4Z"/>',
  retoque: '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7Z"/>',
  texto: '<path d="M4 7V4h16v3M9 20h6M12 4v16"/>',
  substituir: '<path d="M3 5V3h10v2M6.5 13h3M8 3v10"/><path d="M14 15h7M14 19h7M18 12l3 3-3 3"/>',
  forma: '<rect x="3" y="3" width="13" height="13" rx="1.5"/><circle cx="16" cy="16" r="5.5"/>',
  mao: '<path d="M18 11V6a2 2 0 0 0-4 0M14 10V4a2 2 0 0 0-4 0v2M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.9-6-2.3l-3.6-3.6a2 2 0 0 1 2.8-2.8L7 15"/>',
  zoom: '<circle cx="11" cy="11" r="7.5"/><path d="m21 21-4.3-4.3M8 11h6M11 8v6"/>',
  // ações
  recortar: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M8.1 8.1 12 12M20 4 8.1 15.9M14.8 14.8 20 20"/>',
  copiar: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/>',
  colar: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
  apagar: '<rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="3 2.4"/><path d="M12 7.5l1.2 3.3 3.3 1.2-3.3 1.2L12 16.5l-1.2-3.3L7.5 12l3.3-1.2Z"/>',
  dado: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 8h.01M16 8h.01M12 12h.01M8 16h.01M16 16h.01"/>',
  ajustes: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M2 14h4M10 8h4M18 16h4"/>',
  inverter: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18" /><path d="M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7Z" fill="currentColor" fill-opacity=".35" stroke="none"/>',
  fechar: '<path d="M18 6 6 18M6 6l12 12"/>',
  ok: '<path d="M20 6 9 17l-5-5"/>',
  desfazer: '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>',
  refazer: '<path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 15-6.7L21 13"/>',
  olho: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  olho_fechado: '<path d="M9.9 4.2A10 10 0 0 1 12 4c7 0 10 8 10 8a13 13 0 0 1-1.7 2.7M6.6 6.6A13.5 13.5 0 0 0 2 12s3 8 10 8a9.7 9.7 0 0 0 5.4-1.6M2 2l20 20"/><path d="M14.1 14.1a3 3 0 1 1-4.2-4.2"/>',
  mais: '<path d="M5 12h14M12 5v14"/>',
  duplicar: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2M14.5 11.5v6M11.5 14.5h6"/>',
  mesclar: '<path d="M12 3v12M8 11l4 4 4-4M4 21h16"/>',
  lixeira: '<path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2"/>',
  cadeado: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  camadas: '<path d="m12 2 10 5-10 5L2 7Z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
  historico: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>',
  etiqueta: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4Z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  barras: '<path d="M3 5v14M7 5v14M10 5v14M14 5v14M17 5v14M21 5v14" stroke-width="1.4"/><path d="M5 5v14M12 5v14M19 5v14" stroke-width="2.6"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 20h4v-3"/>',
  ia: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8Z"/>',
  fonte: '<path d="M4 20 9.5 5h1L16 20M6 15h9"/><circle cx="18.5" cy="16.5" r="3.5"/><path d="M22 20v-7"/>',
  salvar: '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7M7 3v4a1 1 0 0 0 1 1h7"/>',
  anterior: '<path d="m15 18-6-6 6-6"/>',
  proximo: '<path d="m9 18 6-6-6-6"/>',
  fabrica: '<path d="M2 20a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8l-7 5V8l-7 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M17 18h1M12 18h1M7 18h1"/>',
  ajuda: '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
  livro: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
  teclado: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h.01M12 12h.01M16 12h.01M7 16h10"/>',
  original: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 12h18" stroke-dasharray="2 2"/>',
  mascara: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 15l4-5 3 3 3-4" />',
  mouse: '<rect x="6" y="2.5" width="12" height="19" rx="6"/><path d="M12 2.5v7"/>',
  logo: '<rect x="2.5" y="2.5" width="19" height="19" rx="4"/><path d="M7 8h10M7 12h6.5M7 16h8.5"/>',
};

// <svg> pronto para pôr num botão; tamanho em px
export function icone(nome, tam = 18) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("width", tam); s.setAttribute("height", tam);
  s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.7"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
  s.setAttribute("aria-hidden", "true");
  s.classList.add("ico");
  s.innerHTML = D[nome] || D.dado;
  return s;
}

// mouse com o botão esquerdo aceso (dicas da barra de status)
export function mouseEsquerdo(tam = 14) {
  const s = icone("mouse", tam);
  s.insertAdjacentHTML("beforeend", '<path d="M12 3a5.5 5.5 0 0 0-5.5 5.5V9.5H12Z" fill="currentColor" stroke="none"/>');
  return s;
}

export const TEM = (nome) => nome in D;
