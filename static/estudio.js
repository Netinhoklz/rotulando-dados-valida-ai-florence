// O estúdio: o PhotoCraft web na tela inteira (iframe /photocraft/) e a aba ⚡ Gerar no alto à direita.
// Os ganchos dentro do PhotoCraft (estudio_ganchos.js) chamam window.__estudio.abriu (Abrir/arrastar) e
// window.__estudio.salvou (Ctrl+S); aqui elas viram chamadas ao servidor (rotulador/estudio.py). Nada é perguntado:
// o nome de quem usa vai se já existir (o editor clássico guarda), a classe do que foi editado sai do documento.
import { ErroApi, get, post } from "./api.js";
import { nomeOperador } from "./operador.js";

const $ = (id) => document.getElementById(id);
const SPLITS = { treino: "Treino", teste: "Teste", validacao: "Validação" };
const SEM_PERDA = /\.(psd|psb|png|tiff?|bmp)$/i;
const pc = $("pc");
// atual: a imagem que o estúdio abriu no PhotoCraft (a que o Salvar grava e o Pular fecha); ultima: a última versão
// salva por aqui que ainda está no dataset (a que o "Apagar a última edição" tira)
const estado = { split: "treino", docs: {}, escolhido: null, abertos: [], busca: "", atual: null, ultima: null };

// ------------------------------------------------------------------ avisos
function avisar(texto, tipo = "", ms = 6000) {
  const d = document.createElement("div");
  d.className = `aviso ${tipo}`;
  d.textContent = texto;
  $("avisos").append(d);
  setTimeout(() => d.remove(), ms);
  return d;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ------------------------------------------------------------------ painel
function abrirPainel(abrir) {
  document.body.classList.toggle("aberto", abrir);
  $("painel").hidden = !abrir;
  // a barra da imagem aberta fica presa logo abaixo das abas do painel
  if (abrir) document.documentElement.style.setProperty("--abas", `${document.querySelector(".abas").offsetHeight}px`);
  try { localStorage.setItem("estudio.painel", abrir ? "1" : "0"); } catch { /* sem storage */ }
}
$("aba").addEventListener("click", () => abrirPainel($("painel").hidden));
$("fechar").addEventListener("click", () => abrirPainel(false));
for (const b of document.querySelectorAll(".abas [data-aba]")) {
  b.addEventListener("click", () => {
    for (const x of document.querySelectorAll(".abas [data-aba]")) x.classList.toggle("ativa", x === b);
    for (const s of document.querySelectorAll("[data-pagina]")) s.hidden = s.dataset.pagina !== b.dataset.aba;
    if (b.dataset.aba === "dataset") carregarEstado();
  });
}

// ------------------------------------------------------------------ documentos
async function carregarDocumentos() {
  estado.docs = await get("/api/gerador/documentos");
  desenharSplits();
  desenharLista();
}

function desenharSplits() {
  $("splits").innerHTML = Object.entries(SPLITS).map(([sp, nome]) =>
    `<button type="button" data-split="${sp}" class="${sp === estado.split ? "ativo" : ""}">${nome} <b>${(estado.docs[sp] || []).length}</b></button>`).join("");
  for (const b of $("splits").querySelectorAll("button")) {
    b.addEventListener("click", () => { estado.split = b.dataset.split; desenharSplits(); desenharLista(); });
  }
}

function desenharLista() {
  const q = estado.busca.toLowerCase();
  const docs = (estado.docs[estado.split] || []).filter((d) => !q || d.nome.toLowerCase().includes(q));
  $("lista").innerHTML = docs.length ? docs.map((d) =>
    `<li data-doc="${d.id}" class="${estado.escolhido?.id === d.id ? "ativo" : ""}" title="${esc(d.nome)}${d.versoes ? `\n${esc(feito(d))}` : ""}">
       <span>${esc(d.nome)}${d.tipos?.length ? `<small>${esc(d.tipos.join(" · "))}</small>` : ""}</span><span class="contagem">${d.versoes ? `${d.versoes} versão${d.versoes > 1 ? "ões" : ""}` : "—"}</span></li>`).join("")
    : `<li class="vazio">nenhum documento ${q ? "com esse nome" : "neste split"}</li>`;
  for (const li of $("lista").querySelectorAll("li[data-doc]")) {
    li.addEventListener("click", () => escolher(docs.find((d) => d.id === li.dataset.doc), estado.split));
    li.addEventListener("dblclick", () => abrirDoc(li.dataset.doc));
  }
}
$("busca").addEventListener("input", (e) => { estado.busca = e.target.value; desenharLista(); });

// o que já foi feito na imagem (pelas versões salvas): os tipos de edição e os campos
const feito = (d) => (d.versoes ? `Já feito: ${(d.tipos || []).join(", ") || "edição"}${d.campos?.length ? ` (em ${d.campos.join(", ")})` : ""}` : "Nenhuma edição salva ainda.");

function escolher(d, split) {
  if (!d) return;
  estado.escolhido = { id: d.id, nome: d.nome, split };
  $("escolhido").hidden = false;
  $("esc-nome").textContent = d.nome;
  $("esc-split").textContent = SPLITS[split];
  $("esc-split").className = `selo ${split}`;
  $("esc-feito").textContent = d.versoes === undefined ? "" : feito(d);
  $("gerar").disabled = $("lote").disabled = false;
  $("gerar").title = $("lote").title = `Gerar em ${d.nome}`;
  desenharLista();
  desenharAtual();
}

function desenharAtual() {
  const a = estado.atual;
  $("atual-nome").textContent = a ? a.nome : "nenhuma";
  $("atual-split").hidden = !a;
  if (a) {
    $("atual-split").textContent = SPLITS[a.split];
    $("atual-split").className = `selo ${a.split}`;
  }
  $("salvar").disabled = !a;
  $("pular").disabled = !a && !estado.escolhido;
}

// ------------------------------------------------------------------ abrir no PhotoCraft
const janelaPc = () => pc && pc.contentWindow;

async function arquivoDoEstudio(aberto) {
  const r = await fetch(aberto.png, { cache: "no-store" });
  if (!r.ok) throw new ErroApi("não consegui baixar a imagem do documento");
  return new File([await r.blob()], aberto.arquivo, { type: "image/png" });
}

function registrarAberto(aberto) {
  estado.abertos = [aberto.token, ...estado.abertos.filter((t) => t !== aberto.token)].slice(0, 30);
  const lista = estado.docs[aberto.split] || [];
  escolher(lista.find((d) => d.id === aberto.doc) || { id: aberto.doc, nome: aberto.nome }, aberto.split);
}

// a imagem aberta no PhotoCraft pelo estúdio (ou null, depois de fechada)
function marcarAtual(aberto) {
  estado.atual = aberto ? { doc: aberto.doc, split: aberto.split, nome: aberto.nome } : null;
  desenharAtual();
}

// abre num documento NOVO do PhotoCraft (pelo Abrir dele: soltar na tela com um documento aberto inseriria como camada)
async function abrirNoPhotoCraft(arquivos) {
  for (let i = 0; i < 60; i++) {  // o PhotoCraft pode ainda estar subindo
    const w = janelaPc();
    if (w && w.__estudioAbrir && w.document.getElementById("photocraft_canvas")
        && !w.document.getElementById("photocraft_loading")) {
      await w.__estudioAbrir(arquivos);
      return;
    }
    await new Promise((ok) => setTimeout(ok, 500));
  }
  throw new ErroApi("o PhotoCraft não respondeu");
}

async function abrirDoc(doc) {
  try {
    const aberto = await post("/api/estudio/abrir", { doc });
    registrarAberto(aberto);
    await abrirNoPhotoCraft([await arquivoDoEstudio(aberto)]);
    marcarAtual(aberto);
    avisar(`Aberto no PhotoCraft: ${aberto.nome} (${SPLITS[aberto.split]}). Edite e salve com Ctrl+S.`);
  } catch (e) { avisar(e.message, "erro"); }
}
$("abrir").addEventListener("click", () => estado.escolhido && abrirDoc(estado.escolhido.id));

// ------------------------------------------------------------------ gerar
// o método: o profissional (nome e endereço) ou o do artigo FSTS (cinco tipos, pelas tabelas dele)
const metodo = () => document.querySelector('input[name="metodo"]:checked')?.value || "profissional";
document.querySelectorAll('input[name="metodo"]').forEach((r) => r.addEventListener("change", () => {
  document.querySelectorAll("[data-metodo]").forEach((el) => { el.hidden = el.dataset.metodo !== metodo(); });
}));

function desenharGerado(g) {
  const tipos = g.fsts?.tipos || {};
  const trocas = (g.trocas || []).map((t) =>
    `<li>${t.fsts ? `<i>${esc(tipos[t.fsts] || t.fsts)}</i> · ` : ""}<b>${esc(t.categoria)}</b>: ${esc(t.antigo ?? "")} → ${esc(t.novo ?? "")} <span class="contagem">(${esc(t.nome_tecnica || t.tecnica || "")})</span></li>`).join("");
  $("gerado").hidden = false;
  $("gerado").innerHTML = `<div class="titulo">Gerado e aberto para revisar <span class="selo ${g.split}">${SPLITS[g.split]}</span></div>
    ${g.fsts ? `<p class="dica">Método FSTS: ${(g.trocas || []).length} de ${g.fsts.regioes_pedidas} região(ões) sorteada(s).</p>` : ""}
    <ul>${trocas}</ul>${g.humano ? `<p class="dica">Como foi feito: ${esc(g.humano)}</p>` : ""}
    <p class="dica">Confira no PhotoCraft; ajuste se quiser e salve com <b>Ctrl+S</b>.</p>`;
}

$("gerar").addEventListener("click", async () => {
  if (!estado.escolhido) return;
  const b = $("gerar");
  b.disabled = true;
  const espera = avisar("Gerando com as ferramentas do PhotoCraft… (o primeiro documento lê os campos: pode levar um minuto)", "", 120000);
  try {
    const g = await post("/api/estudio/gerar", { doc: estado.escolhido.id, extras: $("extras").checked, metodo: metodo() });
    registrarAberto(g);
    // a original desta imagem, aberta e sem mexer, dá lugar à versão gerada: uma imagem aberta por vez
    const w = janelaPc();
    if (estado.atual?.doc === g.doc && w?.__estudioMexeu && !w.__estudioMexeu()) await w.__estudioFechar(false);
    await abrirNoPhotoCraft([await arquivoDoEstudio(g)]);
    marcarAtual(g);
    desenharGerado(g);
    avisar(`Versão gerada aberta no PhotoCraft (${(g.trocas || []).length} campo(s)). Salve com Ctrl+S para ir ao dataset.`, "ok");
  } catch (e) { avisar(`Não deu para gerar: ${e.message}`, "erro", 10000); }
  finally { espera.remove(); b.disabled = false; }
});

// lote: as versões vão direto para o dataset (o gerador em segundo plano, com o motor PhotoCraft)
let vigia = null;
$("lote").addEventListener("click", async () => {
  if (!estado.escolhido) return;
  try {
    await post("/api/estudio/lote", { doc: estado.escolhido.id, quantidade: Number($("quantidade").value) || 5,
                                      extras: $("extras").checked, metodo: metodo() });
    $("lote").disabled = true;
    acompanharLote();
  } catch (e) { avisar(e.message, "erro"); }
});

function acompanharLote() {
  clearInterval(vigia);
  $("progresso").hidden = false;
  vigia = setInterval(async () => {
    let st;
    try { st = await get("/api/gerador/status"); } catch { return; }
    const feitas = (st.feitas || 0) + (st.falhas || 0), total = st.total || 1;
    $("progresso").querySelector("div").style.width = `${Math.round(100 * feitas / total)}%`;
    $("progresso").querySelector("span").textContent =
      `${st.feitas || 0} de ${total} salvas${st.falhas ? `, ${st.falhas} sem edição possível` : ""}`;
    if (!st.rodando) {
      clearInterval(vigia);
      $("lote").disabled = false;
      avisar(`Lote pronto: ${st.feitas || 0} versão(ões) no dataset${st.erros?.length ? ` (último problema: ${st.erros[st.erros.length - 1]})` : ""}.`,
             st.feitas ? "ok" : "erro", 10000);
      carregarDocumentos();
      setTimeout(() => { $("progresso").hidden = true; }, 4000);
    }
  }, 1500);
}

// ------------------------------------------------------------------ salvar (Ctrl+S no PhotoCraft)
$("salvar").addEventListener("click", () => {
  const w = janelaPc();
  if (!w || !w.__estudioCtrlS || !w.__estudioCtrlS()) avisar("Clique no documento no PhotoCraft e use Ctrl+S.", "erro");
});

async function enviarSalvo(nome, blob) {
  const espera = avisar(`Salvando ${nome} no dataset…`, "", 120000);
  // enquanto salva, a tela do PhotoCraft não recebe clique: o documento que fecha depois é com certeza o que foi salvo
  document.body.classList.add("salvando");
  let d = null;
  try {
    const fd = new FormData();
    fd.append("arquivo", blob, nome);
    fd.append("nome", nome);
    fd.append("abertos", JSON.stringify(estado.abertos));
    const r = await fetch("/api/estudio/salvar", { method: "POST", body: fd,
                                                     headers: { "X-Operador": encodeURIComponent(nomeOperador()) } });
    d = await r.json().catch(() => ({}));
    if (!r.ok) throw new ErroApi(d.erro || `erro ${r.status}`);
    desenharSalvo(d);
    avisar(`Salvo em ${SPLITS[d.split]}: ${d.nome} · ${d.edicoes} edição(ões) · máscara com ${(d.pixels_mascara || 0).toLocaleString("pt-BR")} px = 1${d.substituiu ? ` (substituiu ${d.substituiu})` : ""}.`, "ok", 9000);
  } catch (e) {
    d = null;
    baixar(nome, blob);  // o trabalho da pessoa não some: vai para os downloads, como o PhotoCraft faria
    avisar(`Não foi para o dataset: ${e.message}. O arquivo foi baixado.`, "erro", 12000);
  } finally { espera.remove(); }
  try {
    if (d) {
      await carregarDocumentos().catch(() => {});
      atualizarUltima();
      await passarParaOProximo(d);
    }
  } finally { document.body.classList.remove("salvando"); }
}

// depois de salvar: o documento salvo fecha no PhotoCraft e o seguinte da lista (o mesmo split, na ordem e com a busca
// da tela) abre para editar. Recusado pelo servidor, nada disso: o documento continua aberto para corrigir
async function passarParaOProximo(d) {
  const w = janelaPc();
  if (w && w.__estudioFechar) await w.__estudioFechar((d.manuais || []).length > 0);
  marcarAtual(null);
  $("gerado").hidden = true;  // o cartão da versão gerada era do documento que fechou
  await abrirProximo(d.doc, d.split);
}

async function abrirProximo(doc, split) {
  const q = split === estado.split ? estado.busca.toLowerCase() : "";
  const lista = (estado.docs[split] || []).filter((x) => !q || x.nome.toLowerCase().includes(q));
  const i = lista.findIndex((x) => x.id === doc);
  if (i < 0) { avisar("Escolha o próximo documento na lista.", "", 9000); return; }
  const prox = lista[i + 1];
  if (!prox) { avisar(`Era o último documento de ${SPLITS[split]} na lista.`, "ok", 9000); return; }
  if (estado.split !== split) { estado.split = split; desenharSplits(); }
  await abrirDoc(prox.id);
}

// fecha a imagem aberta sem salvar; se a pessoa mexeu nela, só depois de confirmar (o PhotoCraft pergunta se salva e a
// resposta é "Não"). Devolve false se a pessoa desistiu
async function fecharSemSalvar(pergunta) {
  const w = janelaPc();
  if (!estado.atual || !w || !w.__estudioFechar) { marcarAtual(null); return true; }
  const mexeu = w.__estudioMexeu ? w.__estudioMexeu() : true;
  if (mexeu && !confirm(pergunta)) return false;
  await w.__estudioFechar(mexeu);
  marcarAtual(null);
  $("gerado").hidden = true;
  return true;
}

// ⏭ Pular: a imagem fecha sem ir para o dataset e a seguinte da lista abre
$("pular").addEventListener("click", async () => {
  const a = estado.atual || (estado.escolhido && { doc: estado.escolhido.id, split: estado.escolhido.split });
  if (!a) return;
  if (!(await fecharSemSalvar(`Pular ${estado.atual?.nome}? O que você mudou nela e não salvou se perde.`))) return;
  await abrirProximo(a.doc, a.split);
});

// ↩ Apagar a última edição salva: sai do dataset a versão (editada, máscaras, anotação, linha da planilha); a imagem
// original continua na pasta. Depois a pessoa escolhe: voltar a essa imagem ou continuar onde está
async function atualizarUltima() {
  try { estado.ultima = (await get("/api/estudio/ultima")).ultima; } catch { estado.ultima = null; }
  const u = estado.ultima;
  $("desfazer").disabled = !u;
  $("desfazer-qual").textContent = u ? `Última salva: ${u.documento} · v${String(u.versao).padStart(2, "0")} (${SPLITS[u.split]})`
                                     : "Nenhuma edição salva por aqui para apagar.";
}

$("desfazer").addEventListener("click", async () => {
  const u = estado.ultima;
  if (!u || !confirm(`Apagar a edição v${String(u.versao).padStart(2, "0")} de ${u.documento}?\n\nSaem do dataset a imagem `
                     + "editada, as máscaras, a anotação e a linha da planilha. A imagem original continua na pasta.")) return;
  try {
    const r = await post("/api/estudio/desfazer", { nome: u.nome });
    $("desfeito").hidden = false;
    $("desfeito").innerHTML = `<div class="titulo">Edição apagada: ${esc(r.documento)} · v${String(r.versao).padStart(2, "0")}
        <span class="selo ${r.split}">${SPLITS[r.split]}</span></div>
      <p class="dica">Saíram a imagem editada, as máscaras, a anotação e a linha da planilha. A imagem original continua
        na pasta.</p>
      <div class="linha"><button type="button" class="secundario" id="voltar-img">↩ Voltar a essa imagem</button>
        <button type="button" class="secundario" id="continuar">Continuar aqui</button></div>`;
    $("voltar-img").addEventListener("click", async () => {
      if (estado.atual?.doc !== r.doc
          && !(await fecharSemSalvar(`Voltar a ${r.documento}? A imagem aberta agora (${estado.atual?.nome}) fecha, e o que você mudou nela e não salvou se perde.`))) return;
      $("desfeito").hidden = true;
      if (estado.atual?.doc !== r.doc) await abrirDoc(r.doc);
    });
    $("continuar").addEventListener("click", () => { $("desfeito").hidden = true; });
    avisar(`Edição apagada: ${r.documento} · v${String(r.versao).padStart(2, "0")}.`, "ok");
    await carregarDocumentos().catch(() => {});
  } catch (e) { avisar(`Não deu para apagar: ${e.message}`, "erro", 10000); }
  atualizarUltima();
});

function baixar(nome, blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = nome;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}

function desenharSalvo(d) {
  const eds = (d.lista_edicoes || []).map((e) =>
    `<li><b>${esc(e.categoria)}</b>${e.texto_antigo != null ? `: ${esc(e.texto_antigo)} → ${esc(e.texto_novo ?? "?")}` : ""} <span class="contagem">(${esc(e.nome_tecnica)}, ${(e.pixels || 0).toLocaleString("pt-BR")} px)</span></li>`).join("");
  const img = (tipo) => `/api/catalogo/imagem/${d.split}/${d.nome}/${tipo}.png?max=300&t=${Date.now()}`;
  $("salvo").hidden = false;
  $("salvo").innerHTML = `<div class="titulo">${esc(d.nome)} <span class="selo ${d.split}">${SPLITS[d.split]}</span></div>
    <div>${esc(d.documento || "")}</div><ul>${eds}</ul>
    <div class="par"><figure><img alt="editada com a edição marcada" src="${img("sobreposicao")}"><figcaption>editada (edição marcada)</figcaption></figure>
    <figure><img alt="máscara" src="${img("mascara")}"><figcaption>máscara (branco = 1)</figcaption></figure></div>`;
}

// ------------------------------------------------------------------ ferramentas antigas (o editor clássico)
// a área de trabalho troca para o editor clássico (/classico, sem perguntar o nome) com o documento escolhido aqui; ele
// só carrega de novo quando o documento escolhido muda (o que estava aberto nele fica como rascunho no servidor)
let docAntigo;
function ferramentasAntigas(abrir) {
  document.body.classList.toggle("antigo", abrir);
  $("barra-antigo").hidden = !abrir;
  $("antigo").hidden = !abrir;
  if (!abrir) { carregarDocumentos().catch(() => {}); return; }  // o que foi salvo lá aparece nas contagens daqui
  const doc = estado.escolhido?.id || "";
  if (docAntigo === undefined || doc !== docAntigo) {
    docAntigo = doc;
    $("antigo").src = `/classico?embutido=1${doc ? `&doc=${encodeURIComponent(doc)}` : ""}`;
  }
}
$("aba-antigo").addEventListener("click", () => ferramentasAntigas(true));
$("voltar-pc").addEventListener("click", () => ferramentasAntigas(false));

// ------------------------------------------------------------------ a pasta das imagens (com treino, teste e validação)
// navega pelas pastas do computador (só no próprio computador do servidor); "Usar esta pasta" monta tudo de novo nela e
// a página recarrega. As edições e máscaras de cada pasta ficam na saída dela (a saida/ dentro da pasta)
async function carregarPasta() {
  try {
    estado.pasta = await get("/api/pasta");
    for (const [id, c] of [["pasta-dados", estado.pasta.dados], ["pasta-saida-edicoes", estado.pasta.saida]]) {
      $(id).textContent = c;
      $(id).title = c;
    }
    $("trocar-pasta").hidden = !estado.pasta.pode_trocar;
  } catch (e) { avisar(e.message, "erro"); }
}

async function navegar(caminho) {
  try {
    const l = await get(`/api/pasta/listar${caminho ? `?caminho=${encodeURIComponent(caminho)}` : ""}`);
    estado.navegando = l;
    $("pasta-caminho").value = l.caminho;
    $("pasta-acima").disabled = !l.pai;
    $("pasta-unidade").innerHTML = l.unidades.map((u) =>
      `<option value="${esc(u)}" ${l.caminho.toUpperCase().startsWith(u.toUpperCase()) ? "selected" : ""}>${esc(u)}</option>`).join("");
    $("pasta-lista").innerHTML = l.pastas.length
      ? l.pastas.map((p) => `<li data-caminho="${esc(p.caminho)}" title="${esc(p.caminho)}"><span>📁 ${esc(p.nome)}</span></li>`).join("")
      : `<li class="vazio">sem subpastas</li>`;
    for (const li of $("pasta-lista").querySelectorAll("li[data-caminho]")) {
      li.addEventListener("click", () => navegar(li.dataset.caminho));
    }
    const ordem = Object.keys(SPLITS);  // treino, teste, validação (o JSON chega em ordem alfabética)
    const faltam = ordem.filter((sp) => !l.splits[sp]).map((sp) => SPLITS[sp].toLowerCase());
    $("pasta-splits").textContent = faltam.length
      ? `Esta pasta não tem: ${faltam.join(", ")}. Entre na pasta que tem as subpastas treino, teste e validação.`
      : `✓ Tem ${ordem.map((sp) => l.splits[sp]).join(", ")}.`;
    $("pasta-usar").disabled = faltam.length > 0;
  } catch (e) { avisar(e.message, "erro"); }
}

$("trocar-pasta").addEventListener("click", () => { $("escolher-pasta").hidden = false; navegar(estado.pasta?.dados); });
$("pasta-cancelar").addEventListener("click", () => { $("escolher-pasta").hidden = true; });
$("pasta-acima").addEventListener("click", () => estado.navegando?.pai && navegar(estado.navegando.pai));
$("pasta-unidade").addEventListener("change", (e) => navegar(e.target.value));
$("pasta-caminho").addEventListener("keydown", (e) => { if (e.key === "Enter") navegar(e.target.value.trim()); });
$("pasta-usar").addEventListener("click", async () => {
  const caminho = estado.navegando?.caminho;
  if (!caminho || !confirm(`Usar as imagens de ${caminho}?\n\nO que está aberto no PhotoCraft fecha sem salvar.`)) return;
  $("pasta-usar").disabled = true;
  const espera = avisar(`Lendo as imagens de ${caminho}… (a primeira vez, numa pasta grande, pode levar alguns minutos)`, "", 3600000);
  try {
    const r = await post("/api/pasta/usar", { caminho });
    const n = Object.values(r.documentos).reduce((s, x) => s + x, 0);
    try {
      sessionStorage.setItem("estudio.aviso", `Pasta trocada: ${r.dados} (${n} imagem(ns): ${Object.keys(SPLITS)
        .map((sp) => `${SPLITS[sp].toLowerCase()} ${r.documentos[sp]}`).join(", ")}). Edições e máscaras vão para ${r.saida}.`);
    } catch { /* sem storage: só não avisa */ }
    location.reload();
  } catch (e) {
    avisar(`A pasta não foi trocada: ${e.message}`, "erro", 20000);
    $("pasta-usar").disabled = false;
  } finally { espera.remove(); }
});

// ------------------------------------------------------------------ dataset
async function carregarEstado() {
  try {
    const e = await get("/api/estudio/estado");
    $("pasta-saida").textContent = e.saida;
    $("tabela").innerHTML = `<tr><th>Split</th><th>Documentos</th><th>Com versão</th><th>Versões</th></tr>` +
      Object.entries(SPLITS).map(([sp, nome]) => `<tr><td><span class="selo ${sp}">${nome}</span></td>
        <td>${e.splits[sp].documentos}</td><td>${e.splits[sp].com_versao}</td><td>${e.splits[sp].versoes}</td></tr>`).join("");
  } catch (err) { avisar(err.message, "erro"); }
}

// ------------------------------------------------------------------ o que os ganchos do PhotoCraft chamam
window.__estudio = {
  avisar,
  // arquivos abertos no PhotoCraft (Abrir ou arrastar) → entram no dataset; volta o PNG do registro
  async abriu(arquivos) {
    const novos = [];
    for (const f of arquivos) {
      try {
        const fd = new FormData();
        fd.append("arquivo", f, f.name);
        const r = await fetch("/api/estudio/arquivo", { method: "POST", body: fd });
        const a = await r.json().catch(() => ({}));
        if (!r.ok) throw new ErroApi(a.erro || `erro ${r.status}`);
        registrarAberto(a);
        novos.push(await arquivoDoEstudio(a));
        marcarAtual(a);
        avisar(a.novo ? `${f.name} entrou no dataset, no split ${SPLITS[a.split]}.`
                      : `${f.name} já estava no dataset (${SPLITS[a.split]}).`, "ok");
        if (a.novo) carregarDocumentos();
      } catch (e) {
        avisar(`${f.name}: ${e.message}. Abrindo sem ir para o dataset.`, "erro", 10000);
        novos.push(f);
      }
    }
    return novos;
  },
  // o download do PhotoCraft: sem perda → dataset (e nada de download); com perda → baixa normalmente
  salvou(nome, blob) {
    if (!SEM_PERDA.test(nome)) {
      avisar(`${nome}: exportado com perda (JPEG/WEBP), baixado normalmente. Para o dataset, salve com Ctrl+S.`, "", 9000);
      return false;
    }
    enviarSalvo(nome, blob);
    return true;
  },
};

// ------------------------------------------------------------------ início
try { abrirPainel(localStorage.getItem("estudio.painel") !== "0"); } catch { abrirPainel(true); }
carregarDocumentos().catch((e) => avisar(e.message, "erro"));
carregarPasta();
atualizarUltima();
desenharAtual();
try {
  const aviso = sessionStorage.getItem("estudio.aviso");
  if (aviso) { sessionStorage.removeItem("estudio.aviso"); avisar(aviso, "ok", 15000); }
} catch { /* sem storage */ }
