// Glossário: o que é cada coisa do rotulador, em português simples. Usado pelo editor e pelo gerador.
//   instalarGlossario(botao)  — o botão do cabeçalho abre o glossário inteiro (com busca)
//   ligarTermos(raiz)         — todo elemento com data-termo="chave" ganha um ⓘ que abre o glossário naquele termo
//   abrirGlossario(chave)     — abre direto num termo (ex.: de um link "o que é isso?")

export const TERMOS = [
  { chave: "original", termo: "Documento original", grupo: "Dados",
    explica: "O arquivo de verdade que você colocou em dados/<split>/ (PDF, foto ou scan). O rotulador nunca altera esse arquivo: toda fraude é uma cópia." },
  { chave: "versao", termo: "Versão", grupo: "Dados",
    explica: "Uma cópia do original com uma fraude. Cada documento pode ter várias versões, cada uma com valores e técnicas diferentes. Sai com a imagem editada, a original e as máscaras." },
  { chave: "split", termo: "Split (treino, teste, validação)", grupo: "Dados",
    explica: "A divisão do dataset. O modelo aprende com o TREINO, é ajustado na VALIDAÇÃO e é medido no TESTE. Um documento (e o seu modelo de conta) fica num split só; senão o teste mede o que o modelo já viu." },
  { chave: "modelo_conta", termo: "Modelo de conta (layout)", grupo: "Dados",
    explica: "O desenho da conta de um emissor (onde fica cada campo, o logotipo, as cores). Duas contas da mesma empresa e do mesmo mês têm o mesmo modelo. Com poucos modelos, o detector decora o layout em vez de aprender a fraude." },
  { chave: "emissor", termo: "Emissor", grupo: "Dados",
    explica: "A empresa que emitiu a conta (concessionária de luz, água, gás, telefone, internet). O endereço e o CNPJ dela NÃO são editados: não são dados do cliente." },
  { chave: "campo", termo: "Campo", grupo: "Documento",
    explica: "Um pedaço do documento que o leitor (OCR) achou e reconheceu: nome, endereço, bairro, CEP, cidade, CPF, data, mês de referência, valor, consumo, código de barras, QR code." },
  { chave: "ocr", termo: "Leitor de texto (OCR)", grupo: "Documento",
    explica: "Lê o texto da imagem e diz onde está cada linha. Na primeira vez leva uns segundos por documento; depois fica guardado. Também confere se a edição ficou legível." },
  { chave: "classe", termo: "Classe (categoria)", grupo: "Rótulos",
    explica: "O tipo de dado adulterado: nome, endereço, CEP, data, valor… Vai na máscara de classes (cada classe com uma cor/número) e diz ao modelo O QUE foi falsificado." },
  { chave: "tecnica", termo: "Técnica", grupo: "Rótulos",
    explica: "COMO a edição foi feita: o campo reescrito na mesma fonte, remontado com letras do próprio documento, só um trecho trocado (número da casa, sobrenome), uma letra copiada… Vai na máscara de técnicas." },
  { chave: "mascara", termo: "Máscara", grupo: "Rótulos",
    explica: "Imagem do tamanho do documento que diz, pixel a pixel, o que mudou (branco) e o que não mudou (preto). É o gabarito que o detector tenta acertar. Há a máscara dos pixels alterados, a da região editada, a de classes e a de técnicas." },
  { chave: "regiao", termo: "Região editada", grupo: "Rótulos",
    explica: "A área que a pessoa mexeu (a seleção dela), inclusive pixels que mudaram pouco. É maior que só os pixels alterados." },
  { chave: "mesma_fonte", termo: "Mesma fonte", grupo: "Técnicas",
    explica: "O texto novo é escrito na fonte do documento (identificada entre as 10 mais usadas em contas), com o tamanho, a cor, a inclinação e a espessura do traço do original. Se a fonte não é identificada, a técnica não é usada." },
  { chave: "letras_documento", termo: "Letras do próprio documento", grupo: "Técnicas",
    explica: "O campo novo é montado recortando letras que já existem no documento, no mesmo estilo. É a técnica mais difícil de achar, porque a letra é a do papel." },
  { chave: "trecho", termo: "Trecho / caractere", grupo: "Técnicas",
    explica: "Em vez do campo inteiro, troca só um pedaço (o número da casa, o sobrenome, o dia, os centavos) ou uma única letra/dígito." },
  { chave: "operador", termo: "Operador humano", grupo: "Como se edita",
    explica: "A maioria das versões é feita como uma pessoa faria num editor de imagem: seleciona em volta do texto, apaga com uma ferramenta (preenchimento inteligente, carimbo, pincel), escreve no olho e às vezes desfoca ou põe ruído. Cada pessoa tem hábitos; eles vão na anotação." },
  { chave: "perfeccionista", termo: "Perfeccionista (acabamento meticuloso)", grupo: "Como se edita",
    explica: "Parte das versões sai sem os erros de uma pessoa: apaga só a tinta, casa a cor, o grão do papel e a nitidez. É a ponta mais difícil para o detector." },
  { chave: "peso_traco", termo: "Peso (espessura) do traço", grupo: "Como se edita",
    explica: "Quão grossa é a linha das letras. O texto novo tem de ter a mesma espessura do original; o gerador mede, corrige e descarta a edição que continua diferente (o \"FEV\" mais grosso que o \"/2026\")." },
  { chave: "canal", termo: "Canal de envio", grupo: "Depois da edição",
    explica: "O caminho que o arquivo faz até quem confere: impresso e escaneado, fotografado com o celular, mandado pelo WhatsApp, e-mail… O mesmo canal passa na original e na editada, para não virar pista." },
  { chave: "marcas", termo: "Marcas benignas (negativos difíceis)", grupo: "Depois da edição",
    explica: "Coisas que aparecem em contas VERDADEIRAS e não são fraude: carimbo de pago, autenticação do banco, anotação e marca de caneta, grampo, mancha de copo, dobra, canto dobrado, tarja sobre o CPF. Entram iguais nas duas imagens, para o modelo não achar que toda marca é fraude." },
  { chave: "coerencia", termo: "Coerência", grupo: "Qualidade",
    explica: "As regras que um verificador confere no texto: o CEP é da cidade, a rua tem aquele CEP, as datas estão em ordem, o código de barras tem o valor impresso, o consumo bate com o valor. Uma fraude cuidadosa não quebra essas regras." },
  { chave: "refeita", termo: "Versão refeita / descartada", grupo: "Qualidade",
    explica: "Quando a edição cria uma incoerência (rua nova com o CEP antigo, vencimento antes da emissão), a versão é refeita corrigindo a causa (troca o CEP junto, tira o valor). Se insistir, é descartada e contada no status." },
  { chave: "auditoria", termo: "Auditoria de qualidade", grupo: "Qualidade",
    explica: "Confere todas as versões salvas: máscaras do tamanho certo, edição visível, nada mudado fora da região, versões repetidas, e procura ATALHOS (diferenças entre original e editada que não são a fraude)." },
  { chave: "atalho", termo: "Atalho", grupo: "Qualidade",
    explica: "Uma diferença entre a original e a editada que não é a adulteração (arquivo maior, ruído diferente longe da edição, um metadado a mais). O modelo aprenderia o atalho em vez da fraude." },
  { chave: "detectabilidade", termo: "Detectabilidade (AUC)", grupo: "Qualidade",
    explica: "Quanto um detector simples, treinado nos próprios pares, separa o trecho editado do mesmo trecho na original. 0,5 = não separa (bom: o vestígio não é óbvio); perto de 1 = vestígio fácil (a técnica precisa melhorar)." },
  { chave: "perito", termo: "Perito / Pistas", grupo: "Qualidade",
    explica: "Mede as pistas de pixel que a edição deixou (grão do papel, nitidez, tom, grade do JPEG) contra o resto da página. Nota alta = um perito ou detector pegaria." },
  { chave: "tamanho_texto", termo: "Tamanho do texto (miúdo, pequeno…)", grupo: "Qualidade",
    explica: "A altura das letras do campo. Texto miúdo (datas do boleto, rodapé) é mais difícil de editar bem e falha mais; o status mostra quantos campos de cada tamanho foram editados e quantos ficaram de fora." },
  { chave: "camada", termo: "Camada (editor)", grupo: "Editor",
    explica: "Cada edição fica numa camada, como no Photoshop: dá para esconder, mudar a opacidade, reordenar, mesclar. A categoria da camada vai para a máscara de classes." },
];

const POR_CHAVE = Object.fromEntries(TERMOS.map((t) => [t.chave, t]));
let dlg = null;

function el(tag, props = {}, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const f of filhos.flat()) if (f !== null && f !== undefined && f !== false) e.append(f);
  return e;
}

function montar() {
  if (dlg) return dlg;
  const busca = el("input", { type: "search", placeholder: "procurar um termo…", "aria-label": "procurar no glossário" });
  const lista = el("div", { class: "glossario-lista" });
  const desenhar = () => {
    const q = busca.value.trim().toLowerCase();
    const grupos = {};
    for (const t of TERMOS) {
      if (q && !(`${t.termo} ${t.explica}`.toLowerCase().includes(q))) continue;
      (grupos[t.grupo] ||= []).push(t);
    }
    lista.replaceChildren(...Object.entries(grupos).map(([g, ts]) => el("section", {},
      el("h4", {}, g),
      ...ts.map((t) => el("dl", { id: `termo-${t.chave}`, class: "glossario-termo" }, el("dt", {}, t.termo), el("dd", {}, t.explica))))));
    if (!lista.children.length) lista.append(el("p", { class: "dica" }, "nenhum termo com isso"));
  };
  busca.addEventListener("input", desenhar);
  dlg = el("dialog", { class: "glossario", "aria-labelledby": "glossario-titulo" },
    el("form", { method: "dialog" },
      el("div", { class: "glossario-cab" }, el("h3", { id: "glossario-titulo" }, "📖 O que é cada coisa"), busca,
        el("button", { value: "fechar", "aria-label": "fechar o glossário" }, "✕")),
      lista));
  dlg._desenhar = desenhar;
  dlg._busca = busca;
  document.body.append(dlg);
  return dlg;
}

export function abrirGlossario(chave) {
  const d = montar();
  d._busca.value = "";
  d._desenhar();
  d.showModal();
  if (chave && POR_CHAVE[chave]) {
    const alvo = d.querySelector(`#termo-${chave}`);
    if (alvo) {
      alvo.classList.add("destaque");
      alvo.scrollIntoView({ block: "center" });
      setTimeout(() => alvo.classList.remove("destaque"), 2500);
    }
  } else {
    d._busca.focus();
  }
}

export function instalarGlossario(botao) {
  if (botao) botao.addEventListener("click", () => abrirGlossario());
}

// ⓘ ao lado de cada elemento com data-termo (uma vez por elemento); title com a explicação curta
export function ligarTermos(raiz = document) {
  for (const alvo of raiz.querySelectorAll("[data-termo]")) {
    if (alvo.dataset.termoLigado) continue;
    const t = POR_CHAVE[alvo.dataset.termo];
    if (!t) continue;
    alvo.dataset.termoLigado = "1";
    alvo.append(" ", el("button", { type: "button", class: "termo-i", title: `${t.termo}: ${t.explica}`,
      "aria-label": `o que é ${t.termo}`, onclick: (e) => { e.preventDefault(); e.stopPropagation(); abrirGlossario(t.chave); } }, "ⓘ"));
  }
}
