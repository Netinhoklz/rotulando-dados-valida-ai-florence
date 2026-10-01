// Chamadas ao servidor. Todo pixel que vira dado é calculado lá; aqui só se exibe.

export class ErroApi extends Error {}

async function tratar(r) {
  let dados = null;
  try { dados = await r.json(); } catch { /* resposta sem JSON */ }
  if (!r.ok) throw new ErroApi((dados && dados.erro) || `erro ${r.status}`);
  return dados;
}

export async function get(rota) {
  return tratar(await fetch(rota, { cache: "no-store" }));
}

export async function post(rota, corpo) {
  return tratar(await fetch(rota, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo ?? {}),
  }));
}

// PNG em base64 -> ImageBitmap sem conversão de cor (é só para exibir)
export async function bitmap(b64) {
  const blob = await (await fetch(`data:image/png;base64,${b64}`)).blob();
  return createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
}

export async function bitmapUrl(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new ErroApi(`não consegui carregar ${url}`);
  return createImageBitmap(await r.blob(), { colorSpaceConversion: "none", premultiplyAlpha: "none" });
}
