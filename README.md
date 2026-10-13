# Rotulador UNIDOCK — fraude em comprovantes de endereço

Editor estilo Photoshop, em Flask, para **gerar e rotular** adulterações em comprovantes
de endereço (contas de luz, água, gás, telefone, boletos). A cada documento editado ele salva:

- a imagem **original** (classe autêntica);
- a imagem **editada**;
- a **máscara** dos pixels alterados, da área editada e da categoria por pixel;
- um **JSON** com tudo o que foi feito.

A separação **treino / teste / validação** é garantida pelo servidor. Nada que nasce de um
documento do treino vai para o teste.

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install --no-deps rapidocr_onnxruntime==1.2.3   # OCR, sem o OpenCV que conflita
```

### OCR

O texto do documento é lido pelo **detector PP-OCRv6 small** + **reconhecedor latino PP-OCRv5 mobile** (ONNX
oficiais da PaddlePaddle no Hugging Face), em CPU, pelo `rapidocr_onnxruntime` (só o detector e o reconhecedor são
trocados; sem o classificador 0/180°, que virava a linha digitável de cabeça para baixo). Lê acentos, cedilha e
espaços ("JOSÉ CARLOS RIBEIRO", "MÊS REFERÊNCIA: AGO/2026") e a caixa de cada linha cobre a tinta toda.

- **Modelos:** na primeira leitura, ~18 MB são baixados para `modelos/ocr/` (fora do git), de um commit fixo e
  conferidos pelo sha256. Para baixar antes (máquina que vai ficar sem rede):
  `.venv\Scripts\python.exe -c "from rotulador import ocr; print(ocr.motor())"`.
- **Sem rede ou sem o modelo:** lê com o OCR antigo que vem no pacote (PP-OCRv3 chinês: sem acentos, espaços
  deduzidos dos vãos de tinta) e avisa no console.
- **Cache:** `saida/_cache/ocr/<hash>.json` guarda o nome do motor; a leitura de outro motor (inclusive o cache
  antigo, do PP-OCRv3) é refeita sozinha. O voto de fonte do documento (`_cache/fontes`) também é refeito.

Benchmark (04/10/2026) nos documentos do projeto: 19 páginas sintéticas com o texto e a caixa de cada linha
conhecidos (`tests_ui/gerar_variados.py` e `gerar_amostras.py --realista`: 8 fontes, 150–300 dpi, foto inclinada,
JPEG; 490 linhas) e 94 linhas transcritas à mão da conta real (scan). CPU de 24 threads, tempo por página A4 a 300 dpi.

| motor | CER sint. | linhas exatas | acentos | espaços | caixa cobre a tinta | CER conta real | s/página | RAM pico |
|---|---|---|---|---|---|---|---|---|
| antes: PP-OCRv3 ch (+ espaços pela tinta) | 9,8% | 70% | 0% | 82% | 65% | 7,3% | 2,2 | 1,8 GB |
| **agora: PP-OCRv6 small det + latin PP-OCRv5 rec** | **0,19%** | **97,6%** | **100%** | **99,8%** | **99,4%** | **0,36%** | **2,4** | **1,9 GB** |
| PP-OCRv6 small (det + rec) | 0,42% | 94,5% | 100% | 96,1% | 99,4% | 0,61% | ~2,4 | 2,3 GB |
| PP-OCRv6 medium (det + rec) | 0,17% | 97,8% | 100% | 99,8% | 99,0% | 0,67% | 7,6 | 3,8 GB |
| PP-OCRv6 tiny det + latin PP-OCRv5 rec | 0,19% | 97,6% | 100% | 99,8% | 97,8% | 0,36% | 1,5 | 1,6 GB |

Quase todas as linhas "não exatas" do motor novo são "Nº" lido como "N°" (na Arial os dois são iguais). O
reconhecedor sozinho (`ocr.reconhecer`, usado na conferência das edições) erra 0,19% dos caracteres (antes, 3,4%).
O PaddleOCR-VL-1.6 (0,9B, transformers) foi testado como segunda opinião e ficou de fora: ~0,8 s por linha na RTX
4060 (o reconhecedor acima: ~0,06 s em CPU), "corrige" o que está impresso ("Local de pagemento" → "pagamento",
número sublinhado vira LaTeX) e o *spotting* de uma página inteira passou de 13 GB de RAM.

## Modo simples (padrão, para quem não conhece editor de imagem)

O rotulador abre no modo simples. Na primeira vez, um tutorial de 4 passos explica o uso.

1. Abra um documento. Ele é **lido sozinho** (OCR, ~5 s na primeira vez, depois fica em cache) e
   cada campo aparece **destacado** com a cor do seu tipo: nome, CPF, endereço, CEP, datas, valor,
   consumo, nº de instalação, linha digitável, código de barras, QR…
2. Clique num campo (na imagem ou na lista) e **escreva o valor falso**, ou clique em 🎲 para
   sortear um no mesmo formato. A prévia aparece na hora.
3. Clique em **✔ Trocar** (ou Enter). O resto é automático:
   - apaga o texto antigo;
   - **identifica a fonte** entre as 10 mais usadas em documentos (Arial, Helvetica, Times New Roman,
     Calibri, Verdana, Tahoma, Segoe UI, Courier New, Georgia, Arial Narrow; regular ou negrito),
     comparando o desenho das letras com o original;
   - escreve no mesmo tamanho, cor e posição;
   - **iguala o grão do papel**;
   - grava na camada da categoria certa.
4. **✔ Salvar e próximo** salva e já abre o próximo documento pendente. **↶ Desfazer** volta um passo.

**🤖 Trocar com IA** (aparece quando o servidor de IA está rodando): em vez de apagar e
desenhar o texto com uma fonte do Windows, a IA **reescreve o campo na própria imagem**,
imitando fonte, tinta e fundo. É o fluxo "OCR acha o nome/endereço → IA gera o texto novo
naquela região" usado na literatura de geração de fraudes (ver `docs/pesquisa_geracao_dados.md`).
Escreva o valor novo (ou 🎲), clique em 🤖 e espere de 1 a 3 minutos. Só a região do campo
muda. O modelo é escolhido no seletor "🤖 IA" acima da lista. A caixa "edição automática com IA"
e a opção do lote fazem o mesmo nos campos de texto sorteados.

**Como editar** (abaixo do botão Trocar): qualquer técnica do catálogo que serve para o campo.
Exemplos: trocar só um dígito copiando a letra de outro ponto do documento, só o dia da data, só
os centavos, apagar um caractere fechando o vão, acrescentar traços (3→8), corretivo ou estilo
amador. A prévia aparece no documento, 🎲 sorteia outra variação e ▶ Aplicar grava a edição com o
nome da técnica.

**Atalhos automáticos:**
- **✨ Editar os marcados** troca EXATAMENTE os campos marcados na lista (a caixinha à esquerda de cada
  campo). Ao abrir um documento, **nome e endereço já vêm marcados**; os botões "Marcar: nome e endereço ·
  endereço completo · todos · nenhum" mudam a seleção (a escolha fica lembrada).
  - Cada campo é reescrito na **mesma fonte** do documento, identificada só entre as 10 mais usadas em
    documentos (técnica `campo.mesma_fonte`, abaixo), no mesmo tamanho, cor e posição.
  - O mesmo dado repetido (o nome no envelope e no boleto) recebe o **mesmo valor novo**, na caixa de cada
    lugar; trocando a cidade, UF e CEP saem da mesma cidade.
  - Os valores falsos saem no formato e no tamanho do original (um nome com iniciais continua com iniciais).
  - Um aviso diz o que foi feito e o que não deu; o que ficou ruim se desfaz.
- **＋ Marcar outro texto**: arraste em volta de um texto que o leitor não achou. Ele lê o trecho e
  sugere o tipo.
- **🏭 Gerador automático** abre a página de geração em lote (próxima seção).
- **🔍 Pistas**: o perito (`rotulador/perito.py`) mede, na edição que você acabou de fazer, as pistas que um
  detector de resíduo usa (grão, espectro do grão, nitidez, tom do papel, grade do JPEG dentro contra fora
  da região) e pinta no documento onde a edição destoa do papel. A nota vai de 0 (invisível) a 100 (óbvia).
  É o **desafio**: edite até a nota cair; o que passar vai para o treino como exemplo difícil.

Atalhos: `Ctrl+Enter` salva e vai para o próximo documento, `Alt+↓`/`Alt+↑` passam de campo e `?`
lista todos. O guia de 4 passos recolhe sozinho depois do primeiro documento salvo. **↺ Recomeçar**
(longe do Salvar) descarta as edições do documento. Ao abrir um documento, os dois seguintes já
são lidos (OCR) em segundo plano.

O **🛠 Modo avançado** abre o editor completo descrito abaixo. A lista de campos e a fraude
automática também ficam lá.

## Gerador automático (técnicas × classes, em lote)

A página `/gerador` (botão **🏭 Gerador automático**) abre na tela **Gerar fraudes**, de um clique. A receita
completa (modos exato, fácil e avançado, abaixo) fica em **▸ Opções avançadas**.

### ▶ Gerar fraudes (um clique, padrão)

- **Documentos**: um chip por split (vêm marcados os que têm documento sem versão); "escolher documentos…" abre
  a lista. **Versões por documento**: − / +. **O que mudar**: "Nome e endereço — às vezes também CPF, CEP, datas
  ou valores" (padrão), "Só nome e endereço" ou "Escolher campos…".
- **Estilo das fraudes** (estilos prontos, `gerador.ESTILOS`; cada cartão diz o que faz, para que serve e o que muda
  na imagem):
  - ⭐ **Como gente de verdade** (padrão): 85% das versões editadas por um operador humano (`rotulador/humano.py`:
    seleção em volta do texto, ferramenta de apagar, suavização do Photoshop, conta-gotas, ajuste no olho, retoques
    e o "Salvar como" do programa, aplicado também à original); 15% com acabamento perfeito;
  - 🎯 **Perfeccionista**: só acabamento perfeito (só a tinta muda; cor, grão e desfoque do documento) — o caso mais
    difícil;
  - 🧑 **Só como pessoas**: 100% operador humano — um conjunto de teste parecido com fraude real;
  - 📤 **Como chega no atendimento**: o padrão e, em 70% das versões, o envio do arquivo (WhatsApp, e-mail, rede
    social: canal `envio`, sem foto nem recorte), igual na original e na falsa.
  "Como funciona, passo a passo" (no topo) explica o caminho: ler, escolher, inventar valores, editar, conferir, salvar.
- **👁 Ver um exemplo** edita um documento sorteado sem salvar e mostra ANTES | DEPOIS com o que mudou numerado
  (antigo → novo, ampliável), **quem editou e como** (em português) e se o texto ficou coerente (as regras de um
  verificador: CEP e cidade, datas, código de barras, CPF); a vista grande de cada versão do lote também diz quem editou. **▶ Gerar N fraudes** mostra o progresso e uma galeria ao vivo; no fim, o resumo
  (e por que algo falhou) e os atalhos Revisar e Catálogo.
- **Só edições profissionais** (perfil `receita_profissional`, `gerador.PERFIL_PROFISSIONAL`): em cada campo a
  técnica e a granularidade são sorteadas como um falsificador caprichoso faria:
  - o campo inteiro (≈55%): na mesma fonte identificada, ou montado com as **letras recortadas do próprio
    documento** (`campo.letras_do_documento`: fonte, peso, cor, desfoque e grão idênticos ao resto da linha);
  - só um pedaço (≈25%): o número da casa, o sobrenome, o dia, os centavos;
  - uma letra ou dígito (≈15%): copiada de outro ponto do documento, redesenhada, inserida ou trocada de lugar;
  - acrescentar um complemento (APTO, FUNDOS) ou tirar a parte final (≈5%).
  Nada de retângulo, fonte padrão, corretivo, rabisco ou borrão. Guardas desfazem o que sairia mal (acento antigo
  que sobra, fonte mal identificada numa troca parcial) e refazem o campo inteiro.
- **Campos**: sempre nome e/ou endereço; em ~45% das versões também 1 ou 2 grupos extras, coerentes entre si: o CPF
  do cliente, o CEP com o bairro (e às vezes a cidade), a data com o mês de referência, o valor com o consumo. O
  mesmo dado repetido sai igual em todo lugar. O "✨ Editar os marcados" do modo simples usa a mesma mistura de
  técnicas, só nos campos marcados.
- Datas e mês de referência são trocados só pelo campo inteiro, que segue o ciclo de datas da versão (vencimento,
  emissão e leitura andando juntos); a troca de um dígito escolhia a data sozinha.
- Na API: `{"receita": {"perfil": "profissional", "estilo": "recomendado", "extras": true, "variacoes": 3,
  "documentos": {...}}}` em `/api/gerador/iniciar` e `/api/gerador/previa`; `GET /api/gerador/estilos` lista os
  estilos; `GET /api/gerador/receita_profissional?estilo=...` mostra a mistura.
  A anotação traz `gerador.estilo` (classes, extras, técnica e granularidade de cada campo).

### 🎯 Modo exato: marque o que editar

- **O que editar:** caixas de marcar por classe (Pessoa, Endereço, Valores e datas, Códigos, Emissor), com
  atalhos "Nome e endereço" (o padrão), "Endereço completo" e "Nome, endereço completo e CPF".
- Em cada documento, **todos** os campos dessas classes que o leitor achar são trocados, um por um, na
  **mesma fonte** do documento. O mesmo nome e endereço novos em todo lugar onde se repetem (inclusive dentro
  de outra linha, como "NOME CPF ..."); cidade, UF e CEP coerentes entre si. Lugar que ficou com o valor antigo
  vai para `ocorrencias_antigas` na anotação.
- **Só a tinta muda:** o texto antigo é apagado letra a letra (a tinta dilatada), com o fundo reconstruído pela
  luz do papel em volta; o papel entre as letras fica intacto. O texto novo sai sem hinting, na inclinação da
  linha, na escuridão da tinta antiga, com o grão (luma e cor) do papel; num documento JPEG a região editada é
  recomprimida com as tabelas de quantização do próprio arquivo, na grade dele (`acabamento.requantizar`).
- **A imagem salva é a original com essas edições, e nada mais:** sem canal de envio (foto, scanner, tela,
  WhatsApp), sem marcas benignas, sem falsificador e sem disfarce. A original gravada é o próprio documento.
- **Versões por documento:** cada versão troca outros valores e, com "Variar quais classes entram em cada
  versão" (ligado por padrão), sorteia quais das classes marcadas entram (às vezes só o nome, às vezes só o
  endereço), sem repetir o sorteio no mesmo documento enquanto houver outro.
- **Fonte:** `fonte_identificada` no rótulo é `top10` quando a semelhança com a tinta passa de
  `identificar_fonte.LIMIAR_MESMA_FONTE`, `aproximada` quando a fonte do documento não está entre as 10, e
  `estimada` quando não deu para medir.
- **Letras do próprio documento** (`campo.letras_do_documento`, nº 13, cópia interna): o valor novo é montado com
  as letras recortadas do próprio campo e das linhas no mesmo estilo (altura, cor da tinta, espessura do traço e
  nitidez), transferidas sobre o papel reconstruído, com a fonte identificada só como gabarito de posição; a letra
  que o documento não tem sai desenhada e o rótulo diz `fornecedores.texto` = `glifos_do_documento` ou `misto`
  (faltando mais de 25% das letras, a técnica desiste e a edição cai na mesma fonte).
- Na API, é a receita `{"exato": true, ...}`; `gerador.receita_limpa(ids, categorias)` monta a padrão.

Os modos fácil e avançado abaixo continuam para quem quer variar técnicas, canal de envio e falsificadores;
o padrão deles agora também é a imagem como está (canal "nenhum", sem marcas e sem falsificador).

### ✨ Modo fácil: tipos de edição (padrão)

A receita é uma lista de **tipos de edição**. Cada tipo aparece como uma frase, por exemplo:
*"Mexe em Valor ou Consumo · mexer em uma letra ou dígito ou mudar só um pedaço · capricho
profissional"*, com **pouco / normal / muito** ao lado.

- **Ideias prontas** acrescentam um tipo com um clique: valor da conta, trocar o titular, outro
  endereço, mexer nas datas, códigos de pagamento, apagar informações, retoque sem mudar o texto
  e tudo um pouco. Uma receita nova já começa com as quatro primeiras.
- **＋ Criar tipo de edição** abre um diálogo de 4 passos, com cartões grandes:
  1. **o que** adulterar: os campos, cada um com um exemplo;
  2. **como**: 7 jeitos em linguagem simples, cada um com um exemplo antes → depois:
     - trocar o texto todo;
     - mudar só um pedaço;
     - mexer em uma letra ou dígito;
     - copiar e colar;
     - apagar;
     - gerar código novo;
     - retoque sem mudar o texto.

     Depois do primeiro lote, cada jeito mostra também um **recorte real** já gerado. Um jeito que
     não serve para as classes escolhidas fica apagado (código novo não vale para CEP, por exemplo);
  3. o **capricho** do falsificador: amador, profissional ou IA. Dá para misturar;
  4. **quanto** o tipo aparece no lote.

  O rodapé do diálogo mostra a frase do tipo e quantas técnicas e combinações ele usa.
- Cada tipo pode ser ligado e desligado, editado, duplicado e **👁 testado sozinho**: a prévia sai
  só com ele.
- **Quanto gerar** define as versões por documento e as edições por imagem.
- **Como o documento chega** a quem confere é escolhido em cartões: nenhum, misturado, scanner,
  celular, tela, impressão ou WhatsApp. Ali também se ligam os carimbos e as anotações.
- Grão do papel, disfarce e formato ficam no automático.
- **Quem falsifica**: por padrão, 80% das versões são feitas **como uma pessoa faria** (abaixo).
  Cartões ligam e desligam cada falsificador e cada motivo; um controle diz a fração de versões.
- À direita, o **Plano do lote** mostra a fatia de cada tipo, a mistura de falsificadores e motivos,
  e exemplos reais antes → depois das técnicas dele, de preferência nas classes do tipo.

### 👤 Como uma pessoa faria (falsificadores e motivos)

Um sorteio uniforme de técnicas e campos não é o que um fraudador faz, e o detector aprende o
sorteio em vez da fraude (no RTM, o manual é localizado com IoU 29,9% contra 57,6% do automático).
Por isso, em cada versão, o gerador sorteia:

- um **falsificador**, com a ferramenta e os hábitos dele, fixos na versão inteira:
  - 📱 **celular, app de edição** (27%): retângulo ou pincelada, fonte do sistema, letra copiada com o
    fundo, rabisco, borrão, dedo ou adesivo na foto; manda a foto da tela ou pelo WhatsApp;
  - 🖌️ **Paint ou editor simples** (13%): copia e cola dígitos do próprio documento, como 78% das
    edições dos voluntários do *Find it again!*;
  - 📄 **no papel** (8%): corretivo líquido, caneta esferográfica, recorte colado, post-it ou dedo por
    cima — e depois fotografa ou escaneia de novo (o canal é obrigatório);
  - 📝 **editor de PDF ou de texto** (27%): troca o texto mantendo a fonte; o texto novo sai **nítido**,
    sem o grão do papel escaneado (a "mancha lisa demais" que os detectores procuram);
  - 🎯 **Photoshop/GIMP com cuidado** (22%): preenchimento inteligente, grão igualado, letra colada
    com alfa; desfoca e aguça depois, sempre com os mesmos parâmetros (o FSTS mediu que cada pessoa
    repete os seus);
  - 🤖 **IA generativa** (3%, só com o servidor ligado);
- um **motivo**, que escolhe campos que combinam e valores combinados:
  - 📅 **deixar a conta recente** (35%): vencimento de 3 a 85 dias atrás e o mês de referência anterior
    a ele (é o campo mais alterado em comprovantes de residência: a regra dos 90 dias);
  - 👤 **pôr outro titular** (25%): o mesmo nome em todo lugar; o CPF entra em 60% das vezes;
  - 🏠 **provar outro endereço** (20%): rua sempre; CEP, bairro e cidade às vezes;
  - 💰 **mudar o valor** (12%): às vezes um valor redondo; o código de barras fica velho;
  - 🧽 **sumir com uma informação** (5%); 🎲 sem motivo (3%).

Cada pessoa usa só as técnicas da ferramenta dela, nos pesos medidos, e sempre dentro do que a
receita (tipos de edição ou técnicas × classes) permite. O resto das versões sai do sorteio clássico,
que mantém a cobertura uniforme. Na receita: `falsificadores: {prob, pesos, motivos}`; `pesos` e
`motivos` listados valem só para quem está na lista. Quem fez e por quê fica na anotação
(`falsificador`), nas colunas `falsificador`/`motivo` dos índices, nos filtros do catálogo e no
datasheet; dá para treinar sem um falsificador e testar nele. As proporções entre falsificadores são
**estimativas** (ninguém as mediu): ajuste-as se tiver fraudes reais classificadas. Evidências,
números e limites: [docs/falsificadores.md](docs/falsificadores.md).

No servidor (`tipos` na receita, em `rotulador/gerador.py`), só entram as combinações técnica ×
classe de algum tipo. O peso de cada tipo se divide entre as combinações dele, tanto na
distribuição equilibrada quanto na por pesos. Os jeitos ficam em `rotulador/catalogo.py`
(`JEITOS`), e cada técnica automática está em exatamente um.

### 🔧 Modo avançado

Mostra todas as técnicas, classes, pesos e acabamentos. Enquanto houver tipos do modo fácil
valendo, as listas de técnicas e classes ficam só para ver; as opções ⚙ de cada técnica continuam
valendo. **Usar estas técnicas e classes no lugar dos tipos** volta à lista completa.

1. **Documentos:** splits, todos ou alguns, só os pendentes.
2. **Técnicas, o *como*:** 29 técnicas automáticas em 8 famílias de fraude, com peso e opções por
   técnica. Há atalhos: só caractere, trecho e campo, profissional, amador, cópia e colagem,
   remoção, traços e IA. Três opções aparecem em várias técnicas, porque cada ferramenta real as faz
   de um jeito: **como apagar** (PatchMatch, inpainting Telea ou Navier-Stokes de app, cor chapada do
   Paint, borrão, carimbo de papel vizinho), **vestígio** (apagado malfeito, com um fantasma fraco do
   texto antigo) e, no estilo amador, o **rasterizador** do texto (FreeType, GDI com hinting e poucos
   tons, ClearType com franjas coloridas, sem antialias).
3. **Classes, o *quê*:** nome, valor, data, CPF… com peso e atalhos (identidade, endereço,
   financeiro, códigos).
4. **Quantidade e acabamento:**
   - variações por documento e edições por imagem;
   - distribuição **equilibrada**, que cobre cada combinação técnica × classe por igual, ou pelos
     pesos;
   - grão do papel e recompressão JPEG local;
   - **disfarce**: retoques que falsificadores reais fazem depois da edição (desfoque, aguçar,
     curvas, cor), nas frequências medidas no FSTS;
   - **canal de envio**: o caminho até quem verifica (impressão, scanner, foto de celular, tela
     fotografada, WhatsApp, rede social, e-mail), aplicado **igual na original e na editada** de
     cada par, numa fração das versões. Metade dos JPEG das capturas usa as tabelas de quantização
     dos próprios documentos de entrada do split (aparelhos de verdade); os saltos de WhatsApp/rede
     social usam as do libjpeg. As máscaras passam pela mesma geometria;
   - **marcas benignas**: autenticação mecânica do banco, carimbo, anotação à mão, marca-texto,
     rubrica, dobra e furos. Vão iguais nas duas imagens e só com captura física. Ficam fora da
     edição, exceto a dobra, que é sombra do papel inteiro. Ensinam que marca não é fraude;
   - **captura antes ou depois da edição** (`canal.captura`: `auto`, `antes`, `depois`). Quem edita
     no celular ou no Paint parte de uma **foto ou de um scan**: a captura entra ANTES da edição, vira
     a base da versão (a original gravada é a própria captura), os campos lidos pelo OCR são levados
     pela geometria sem OCR de novo, e o retângulo ou o texto novo fica **liso sobre o papel
     fotografado**, que é a pista que os detectores comerciais descrevem. Impressão, tela e
     transmissão (WhatsApp, rede social) ficam para depois, iguais no par. `auto` segue o hábito do
     falsificador (celular, Paint, Photoshop e IA antes; papel e editor de PDF depois); um arquivo de
     entrada que já é foto (JPEG/HEIC) dispensa a captura antes metade das vezes. O canal ganhou
     também o **app de scanner** (CamScanner, Adobe Scan: foto, retificação, realce em cor, cinza ou
     preto e branco), **curvatura da página**, **distorção de lente** com aberração cromática,
     **retícula de meio-tom** na impressão e o envio pelo WhatsApp **como documento** (sem recompressão);
   - **grão** (`acabamento.grao`: `auto`, `espectral`, `branco`): o que entra na região editada das
     técnicas profissionais. `espectral` sintetiza ruído com o **mesmo espectro** do papel em volta
     (`rotulador/pericia.py`); `branco` é o Add Noise do Photoshop (só a variância). `auto` = 70%
     espectral, ou o hábito do falsificador. Antes, todo grão era branco, e esse era o tell nº 1 das
     edições profissionais;
   - **dificuldade** (`dificuldade: {variantes, prob, detector}`): cada edição é gerada `variantes`
     vezes com sementes diferentes e fica a que o **perito** menos acusa (ou um detector externo em
     `detector.url`, que recebe original, editada e região em PNG e devolve `{"nota": ...}`). É a
     mineração de exemplos difíceis: o dataset vai para a ponta que o detector atual não pega;
   - formato e operador.

   O padrão da página segue o estado da arte em dados para generalizar: canal misturado em 70% das
   versões e marcas em 30% das que têm captura física (≈ 18% de todas). Uma receita enviada pela API
   sem `canal` não usa canal, e a fraude automática do modo simples também não. Evidências e números: [docs/tecnicas.md](docs/tecnicas.md#qualidade-e-generalização).

**👁 Prévia** roda a receita num documento **sem salvar**:
- mostra a imagem com cada edição marcada e a máscara de técnicas;
- lista as edições, cada uma com recorte antes/depois e a descrição;
- mostra as tentativas que não couberam.

**▶ Gerar** roda em segundo plano, com progresso por técnica, as últimas versões e botão Parar.
Cada versão é salva no **próprio split**, e letras e campos colados vêm só de documentos do mesmo
split.

| Família (Find it again!) | Técnicas |
|---|---|
| Imitação (IMI) | reescrever o campo com a fonte imitada; **reescrever na camada de texto do PDF** (edição vetorial: redação + fonte embutida ou a de reserva do editor, página rasterizada de novo, sem rastro de pixel; só em PDF com texto); estilo amador (retângulo + fonte padrão, com o rasterizador do programa); acrescentar texto no fim; trocar só um trecho (dia, centavos, nº da casa, sobrenome, DV); trocar um caractere redesenhado; inserir um dígito |
| Cópia interna (CPI) | trocar um caractere pela mesma letra recortada de outro ponto do documento (no Paint, esticada na horizontal); inverter dois dígitos vizinhos; copiar outro campo do documento por cima. Colagem: só a tinta, retângulo com o fundo, Poisson ou **recorte de papel colado** (borda e sombra, depois fotografado) |
| Colagem de outro documento (CPO) | letra de outro documento; o mesmo campo de outro documento (splicing por similaridade); **o bloco de endereço inteiro** (rua, bairro, CEP, cidade em linhas vizinhas) de outro documento, como um recorte de tela colado |
| Remoção (CUT) | apagar um caractere (fechando ou deixando o vão); apagar o campo ou a última palavra com preenchimento inteligente; corretivo (retângulo, pinceladas do app ou **corretivo líquido no papel**); **esconder com um objeto na foto** (dedo, adesivo, post-it, fita); **rabiscar** (caneta, marcador, pincel do app); **borrar ou pixelizar** (app de celular) |
| Traços e retoque (PIX) | acrescentar traços (3→8, 1→4), digitais ou **de caneta esferográfica no papel**; raspar traços (4→1); **retoque sem mudar o texto** (Texture Jitter: desfoca, reamostra, aguça, recomprime ou muda 1–5% do tamanho de um trecho) |
| Recriação (FAB) | **documento refeito do zero** (`pagina.recriar`): todas as linhas de texto apagadas e re-tipografadas, linha a linha na fonte mais parecida ou numa fonte de escritório para a página toda (Word/Canva), logotipo e códigos mantidos; é sempre a primeira edição da versão e as trocas de campo vêm por cima, no mesmo rasterizador — a fraude "fabricada", classe `outro` na máscara |
| Códigos | código de barras novo + linha digitável coerente; QR PIX novo |
| IA generativa | reescrever o campo; trocar um caractere; apagar o campo. A saída volta de três jeitos: só a região, com borda suave, ou um retângulo em volta do campo regenerado inteiro (nos dois últimos, a região é a área colada; a máscara de pixels, o que mudou) |

Cada técnica tem nível (**amador**, **profissional** ou **IA**), alcance (**caractere**,
**trecho** ou **campo**) e opções. Uma opção pode ser fixada na receita ou ficar como "varia",
sorteada a cada edição.

**Controles de qualidade:**
- só dígitos mudam em campos numéricos;
- a data continua possível, e as datas falsas são relativas à **data de envio** da versão (de 5 a 400
  dias depois da data mais recente impressa no documento), não a hoje: um lote gerado em 2026 sobre
  contas de 2024 não ganha "2026" como rótulo;
- formatos fixos (data, CEP, CPF) não ganham nem perdem dígito;
- o **CEP falso cai na faixa da cidade/UF** impressa (ou da cidade nova, quando o motivo troca a
  cidade), o consumo acompanha a proporção do valor novo, e o **código de barras novo leva o total
  impresso** (ou o total novo, se o falsificador for do tipo que refaz o código) e a mesma
  empresa/segmento da linha original;
- os valores falsos saem de bases oficiais (`config/vocabulario/`, abaixo): 100 mil prenomes, 20 mil
  sobrenomes, 342 mil trechos de rua reais com bairro e CEP, os 5.570 municípios; repartidos por split;
- a letra copiada precisa ter a mesma forma, cor e altura;
- traço acrescentado ou raspado só vale se o resultado "lê" como o dígito novo;
- nos testes, o OCR relê cada edição e confere o valor novo.

Detalhes, algoritmos e limitações: [docs/tecnicas.md](docs/tecnicas.md).

**Aba Catálogo:** o que já foi gerado (e o que foi feito à mão):
- totais por split, nível e origem;
- **matriz técnica × classe**: clicar numa célula filtra a galeria;
- filtros por split, técnica, classe, nível, família e origem, e busca no texto;
- galeria com a máscara de técnicas sobreposta;
- detalhe de cada versão: editada, original, máscara de técnicas e pixels alterados, com a lista
  de edições, zoom em cada uma, **✔ aprovar / ✖ reprovar** e **🗑 excluir** a versão (os índices
  são refeitos);
- **curadoria em lote**: cada versão tem um selo ✔/✖ da revisão e uma caixinha de marcar. As
  versões marcadas (ou a página inteira) podem ser aprovadas, reprovadas com motivo, voltadas a
  pendente ou excluídas de uma vez;
- filtros também por revisão, canal (com, sem ou um perfil), operador e **sem a técnica** (para o
  teste deixa-uma-de-fora);
- **⤓ exportar filtrados** grava `saida/_exportacoes/<nome>/manifesto.csv` e `manifesto.json` só com
  as versões do filtro (só as aprovadas, por exemplo), com caminhos relativos a `saida/`. Os dois
  arquivos ficam para baixar ali mesmo;
- sem nada gerado, a aba mostra um atalho para **⚙ Gerar** em vez de uma página vazia.

## Realismo e generalização: o que o detector não deve decorar

Um detector treinado em dado sintético aprende a **assinatura do gerador** (o OSTF mediu: treinar
num editor e testar em outro derruba o F de 85 para 0). O gerador combate isso em cinco frentes:

- **Fornecedores.** Cada peça da edição tem mais de um fornecedor e o escolhido fica no rótulo
  (`edicoes[].fornecedores`: `apagar`, `texto`, `fonte`, `grao`, `colagem`, `vestigio`): seis
  apagadores (`rotulador/ops/apagar.py`), quatro rasterizadores de texto, fontes do sistema mais as da
  pasta `fontes/` (ponha ali Roboto, Open Sans, Liberation, DejaVu: fontes de celular e de Mac que o
  Windows não tem), grão espectral ou branco, quatro colagens. Com o `catalogo.csv` dá para treinar
  sem um fornecedor e medir se o detector o decorou.
- **Canal em duas fases** (acima): a edição acontece sobre a foto ou o scan quando é assim que a
  pessoa faz, e a impressão, a tela e a transmissão vêm depois.
- **Edição vetorial** (`rotulador/vetorial.py`, técnica `campo.reescrever_pdf`): a fraude sem rastro de
  pixel. Sai com `fornecedores.texto = "pdf-vetorial"`, para pesar menos (ou nada) no treino do
  detector de pixel e servir de **piso de falso negativo** na avaliação e de treino para a cabeça que
  lê o texto.
- **Perícia e perito.** A perícia (`rotulador/pericia.py`) esconde a edição como um profissional
  (iluminação, grão com o espectro do papel, nitidez, costura), em níveis rotulados; o perito
  (`rotulador/perito.py`) mede o contrário, as pistas que sobraram, por edição (grão, espectro,
  nitidez, tom, grade do JPEG, nota de 0 a 1). O datasheet traz a mediana das pistas por técnica e
  nível, `GET /api/perito/<split>/<nome>` as de uma versão, `POST /api/perito/sessao` as da edição
  manual aberta (o botão 🔍 Pistas) e a receita `dificuldade` usa a nota para ficar com a variante
  mais difícil. Um detector seu entra no lugar do perito por `dificuldade.detector.url`.
- **Coerência do texto.** CEP na faixa da cidade, código de barras com o total e a empresa do
  documento, consumo na proporção do valor, datas relativas à data de envio e coerentes por rótulo
  (leitura antes da emissão, antes do vencimento), vocabulário grande. Um modelo que lê o texto não
  acha a fraude pela incoerência que só o gerador deixaria. E o que sobra é **rotulado**: as regras de
  verificador (`rotulador/coerencia.py`: CPF/CNPJ, CEP × cidade, datas, mês de referência, DVs da linha,
  código × total, tarifa) rodam antes e depois da edição; a anotação diz quais violações a edição criou
  e se a versão ficou **sem pista semântica** (só o pixel pega), e o datasheet conta.
- **Laudo.** Cada versão grava um relatório pericial em texto (`laudo`: técnica, campo, antes → depois,
  fornecedores, perícia, canal, pistas de pixel medidas pelo perito por edição e pistas semânticas),
  como os benchmarks de 2025–2026 que treinam detectores que explicam.
- **Desgaste físico.** O canal reproduz o que impressora, scanner e fotocópia deixam (tinta fraca,
  riscos dos rolos, sangria, fotocópia com poeira, verso aparecendo, sujeira no vidro), o reflexo na
  foto e a tela PenTile com a faixa do refresh. Tudo igual no par.
- **Enquadramento e modo de captura.** Um quinto das versões manda só um **pedaço da página** (o
  cabeçalho, a metade de cima: passo `recorte` do canal, na fase da captura; depois da edição a janela
  sempre contém a edição; antes dela, os campos cortados saem do documento). O scanner sai **colorido,
  em tons de cinza ou "texto"/P&B** (binarizado pelo driver), e a foto de celular tem a mesa em volta:
  lisa, madeira, tecido, outra folha por baixo ou granito.
- **Negativos difíceis digitais.** Além das marcas no papel (carimbo, autenticação, anotação, dobra,
  dedo), entram marcas **digitais**, em qualquer canal: **tarja**, borrão ou pixelado sobre um campo
  sensível (CPF, código do cliente, linha digitável, código de barras, QR: quem esconde o próprio dado
  antes de enviar) e a marca d'água "CÓPIA"/"2ª VIA" dos apps. Iguais no par, nunca sobre a edição
  (`marcas_benignas[].tipo`). Sem elas o detector aprenderia que retângulo preto = fraude.
- **Cobertura ativa.** A distribuição `cobertura` do gerador parte do que o split **já tem salvo**
  (`catalogo.csv`) e escolhe primeiro as combinações técnica × classe que faltam; o datasheet lista as
  lacunas e a **diversidade** (entropia normalizada) de cada eixo: técnicas, classes, canais, passos,
  falsificadores, motivos, perícia, marcas, fornecedores.

Ferramentas de apoio: `auditar_vazamento.py` agora agrupa os documentos por **emissor** (CNPJ) e avisa
quando o mesmo emissor está em dois splits, e dá ERRO quando o mesmo **modelo de conta** (layout quase igual)
ou o mesmo cliente (CPF, inclusive mascarado, nome, nº de instalação, linha digitável) está em dois splits
(`--sugerir-split` propõe uma divisão que mantém cada emissor e cada modelo num split só, em
`saida/sugestao_split.json`, sem mover nada); o datasheet ganhou "Alertas do dataset" (teste ou validação
vazios, menos de 20 documentos de origem, menos de 3 modelos/emissores, modelo em dois splits); `tests_ui/benchmark_campos.py PASTA SAIDA` mede o que o
detector de campos acha em contas de verdade (CSV, resumo e folhas com as caixas), porque o gerador só
edita o que o detector encontra; **`auditar_qualidade.py SAIDA [--ocr] [--gravar]`** confere, versão por
versão, se os rótulos batem com as imagens e se a edição vale (máscaras coerentes com a região e com o
par; edição invisível; texto novo igual ao antigo; área demais; versão duplicada; revisão reprovada;
perícia forense que não escondeu nada; com `--ocr`, o campo editado relido pelo reconhecedor). Com
`--gravar` os alertas vão para a anotação (`auditoria_qualidade.alertas`; `qualidade` continua sendo a
qualidade do JPEG de saída) e o datasheet os conta.

## Trabalho em equipe

Várias pessoas podem usar o mesmo rotulador ao mesmo tempo. O servidor usa **waitress** (12
threads) quando ele está instalado (`requirements.txt`).

- **Nome obrigatório** na primeira visita, sem senha. Ele vai nas versões salvas, nas revisões e
  nas atribuições. Para trocar, use o campo Operador (editor) ou o 👤 (gerador).
- **Trava por documento.** Quem abre primeiro edita. Quem abre depois vê *"🔒 Ana está editando
  este documento desde 14:02. Você está só olhando."*:
  - as edições ficam bloqueadas, e **Assumir edição** toma o documento;
  - quem perdeu a trava fica sabendo na próxima ação;
  - a trava vence após 10 min sem sinal e é solta ao trocar de documento ou fechar a aba;
  - quem só olhava é avisado quando o documento fica livre;
  - um pedido pela API sem o cabeçalho `X-Operador` (um script) não pega trava nem é barrado por uma.
- **Lista de documentos.** Mostra 🔒 (com outra pessoa), ★ (atribuído a você) e → (atribuído a
  outra pessoa). Tem busca e "só os meus", e desenha aos poucos (150 por vez) quando há milhares.
- **Aba ✔ Revisar** (no gerador): a fila das versões pendentes, das mais antigas para as mais novas.
  - `A` aprova;
  - `R` reprova; precisa de motivo (`1`–`6`) ou comentário;
  - `→` e `←` pulam e voltam;
  - clicar numa edição amplia.

  A decisão fica na anotação (`revisao`: estado, motivos, comentário, quem, quando) e nas colunas
  `revisao` e `revisor` dos índices.
- **Aba 🎯 Metas**: quantas edições de cada classe em cada split, com barras de progresso. Dá para
  contar só as versões aprovadas. **⚙ gerar o que falta** monta a receita do split (modo
  avançado) com as classes que faltam, pesadas pelo quanto falta.
- **Aba 👥 Equipe**:
  - quanto cada pessoa salvou e a taxa de aprovação das versões dela;
  - as revisões que cada uma fez;
  - quem está editando o quê agora;
  - marcar documentos e **atribuir** a alguém.

## Uso

1. Coloque os documentos em `dados/treino`, `dados/teste` e `dados/validacao` (subpastas
   valem). Formatos aceitos: JPG, PNG, TIFF (inclusive multipágina), BMP, WEBP, HEIC e PDF.
   Cada página vira um item; PDF é rasterizado a 200 DPI (`--dpi`).
2. `.venv\Scripts\python.exe app.py` abre o navegador em http://127.0.0.1:5000.
   Opções: `--dados`, `--saida`, `--porta`, `--dpi`, `--sem-navegador`.
3. Para cada documento:
   1. Escolha a **categoria** do que vai editar (barra amarela, `Alt+1..0`, ou clique na lista
      "O que foi editado").
   2. Edite. A primeira edição de cada categoria cria **uma camada só dela** (ver "Camadas").
   3. Repita para outras categorias.
   4. Salve com **Salvar** (`Ctrl+S`).

   O botão só libera quando há edição e toda camada visível tem categoria. Depois de salvar, o
   documento volta ao original e você pode criar outra variação (v02, v03…).
4. Antes de treinar, rode `.venv\Scripts\python.exe auditar_vazamento.py`.

**Rodar em segundo plano, sem janela** (rotulador na porta 5010 e IA na 5051):

```powershell
wscript //B lancador\lancar_oculto.vbs "C:/Users/netinhoklz/Projetos pessoais/rotulador-dados-UNIDOCK/lancador/iniciar.sh"
bash lancador/parar.sh        # para os dois
```

- Logs ficam em `E:\rotulador\logs` (mude com a variável `ROTULADOR_BASE`).
- Se `dados/` estiver vazio, o rotulador sobe com contas de demonstração.
- Para não depender do terminal aberto, dispare pelo Agendador de Tarefas com o mesmo comando e
  apague a tarefa logo depois.

As edições ficam em rascunho no disco (`saida/<split>/_rascunhos`). Se o servidor cair, o
trabalho volta quando você reabre o documento.

## Camadas

Funciona como no Photoshop. O original é o **Fundo** (travado) e cada edição vai para uma camada.

- **Cada categoria tem a sua camada.** Escolher "valor" e editar cria a camada "valor 1"; escolher
  "data" cria outra. Escolher de novo uma categoria que já tem camada volta a editar nela. Clicar
  numa camada a ativa e põe a categoria dela na barra amarela.
- **Painel:** 👁 oculta/mostra, duplo clique renomeia, a caixa de categoria de cada camada define o
  que vai para a máscara, arrastar reordena, e há controle de opacidade. Botões: ＋ nova camada,
  ⧉ duplicar, ⤓ mesclar com a de baixo, 🗑 excluir.
- **Atalhos:** `Ctrl+Shift+N` nova, `Ctrl+J` duplicar, `Ctrl+E` mesclar, `Ctrl+[` e `Ctrl+]` mudam a
  posição na pilha.
- **Como as ferramentas usam as camadas:** elas enxergam a imagem composta (todas as camadas
  visíveis) e gravam na camada ativa. A **borracha** apaga da camada ativa e revela o que está
  embaixo. **Mover (V) sem seleção** arrasta a camada inteira; as setas empurram 1 px (com `Shift`,
  10 px).
- **Rótulos:**
  - a categoria por pixel é a da camada visível **mais alta**;
  - camadas **ocultas não entram** no salvamento, e o painel avisa quando há alguma;
  - o JSON salvo lista as camadas.
- Desfazer/refazer cobre pixels e mudanças na pilha. Criar a camada e pintar nela é **um** passo
  só no `Ctrl+Z`.

## Ferramentas

| Atalho | Ferramenta |
|---|---|
| `M` / `Shift+M` | seleção retangular / elíptica |
| `L` | laço (marque "Poligonal" para clicar ponto a ponto) |
| `W` | varinha mágica (tolerância, contígua ou por cor) |
| | Seleção: `Shift` soma, `Alt` subtrai, `Shift+Alt` intersecta. Também: expandir/contrair, suavizar, `Ctrl+A`, `Ctrl+D`, `Ctrl+Shift+I` (inverter) |
| `B` | pincel (`Alt+clique` pega a cor; `[` `]` mudam o tamanho) |
| `E` | borracha: apaga da **camada** ativa e revela o que está embaixo (não precisa de categoria) |
| `G` | balde de tinta |
| `I` | conta-gotas (lê o pixel no servidor) |
| `S` | carimbo de clonagem: `Alt+clique` na origem, depois pinte. Mostra sob o cursor o que vai ser copiado. Opções: copiar da imagem atual ou do **original**, escala, rotação, alinhado, modo de mescla (normal, escurecer, clarear, multiplicar, tela) |
| `Shift+J` | pincel de recuperação: como o carimbo, mas a cor e a luz vêm do destino (mescla Poisson) |
| `J` | pincel corretivo pontual: reconstrói o traço a partir do entorno |
| `Y` | remendo: com a área ruim selecionada, arraste a seleção até uma área boa; a textura vem de lá, mesclada. Modo "destino" leva a seleção para lá |
| `O` | pincel de retoque: desfocar, nitidez, borrar (dedo), clarear, escurecer, saturar, dessaturar |
| `U` | formas: retângulo, elipse, linha (`Shift` deixa quadrado/círculo/reta), com preenchimento e contorno |
| `Q` | pincel de seleção: pinte a seleção (`Alt` tira) |
| | "Só a tinta" (nas ferramentas de seleção) reduz a seleção aos traços de texto/tinta dentro dela |
| `T` | texto: clique na linha de base, digite e dê `Enter`. Ajustes: fonte do sistema ou da pasta `fontes/`, tamanho, espaçamento, largura, negrito, rotação, desfoque, suavização |
| `R` | **substituir texto**: arraste sobre o texto antigo. O servidor estima posição, tamanho e cor, apaga o antigo com preenchimento inteligente e escreve o novo. 🎲 sorteia um valor plausível da categoria |
| `V` | com seleção: mover/duplicar. Arrastar move; o canto escala (`Shift` mantém a proporção, `Ctrl` faz perspectiva); fora do contorno gira. Sem seleção: move a **camada** ativa |
| ⧉ | colar um trecho de **outro documento do mesmo split** (splicing) |
| `Shift+F5` | **preenchimento por similaridade** (PatchMatch), com prévia ao vivo. Escolha de onde amostrar (automático, documento inteiro ou faixa em px), tamanho do patch e 🎲 outra variação |
| ◐ | ajustes na seleção: brilho/contraste, níveis, matiz/saturação, desfoque, nitidez, ruído, recompressão JPEG local, cinza e **igualar ruído ao papel** (deixa a área editada com o mesmo grão do entorno; testado: a razão de ruído dentro/fora fica em 1 ± 0,12) |
| ▮▯ | código de barras ITF-25. 🎲 gera um código válido de arrecadação (contas, começa com 8) ou de boleto, junto com a linha digitável |
| ▦ | QR code. 🎲 gera um PIX "copia e cola" válido (CRC conferido) |
| 🤖 | edição por IA da seleção (ver abaixo) |
| `Ctrl+Z` / `Ctrl+Y` | desfazer / refazer |
| `\` (segurar) | ver o original |
| `K` | sobrepor a máscara de pixels alterados |
| `X` / `D` | trocar as cores / cores padrão |
| Roda, `Espaço`+arrastar, `0`, `1` | zoom, mover a vista, ajustar à tela, 100% |
| `PageUp` / `PageDown` | documento anterior / próximo |

`?` mostra todos os atalhos (gerais, do modo simples e do avançado).

**🔍 Qual é a fonte?** (nas ferramentas `T` e `R`) descobre a fonte de um texto selecionado.

- **Como compara:** o rotulador desenha o mesmo texto em **todas as fontes instaladas**, mais as da
  pasta `fontes/`. Ficam de fora as fontes sem alguma letra do texto e as que precisariam ser
  esticadas. As outras são comparadas pela intensidade da tinta, com o desfoque do scan medido nas
  bordas.
- **O que mostra:** as 6 mais parecidas, cada uma com o texto desenhado ao lado do original.
  **Usar esta** põe fonte, tamanho, largura e negrito nas opções e a linha de base no lugar da
  antiga.
- **Fontes iguais:** as que desenham o texto do mesmo jeito (Arial, Helvetica, Swis721) aparecem
  juntas.
- **Campos curtos:** o resto do documento vota, e um "12" fica com a fonte que a conta usa.
- **Onde mais é usado:** na troca automática de campo e nas técnicas do gerador. Antes eram 24
  fontes fixas.
- **Resultado medido:** num teste com 30 fontes em linhas degradadas como scan (desfoque, ruído,
  JPEG), a fonte certa saiu em 1º lugar em 92% dos casos e entre as 3 primeiras em 96% (antes:
  57%), a ~0,17 s por campo.

Os pixels **nunca** são calculados no navegador. Ele só descreve a operação (pontos,
retângulo, texto); o servidor aplica na resolução original. Por isso a máscara é exata.

## Categorias

Ficam em `config/categorias.json`: nome, CPF/CNPJ, endereço, bairro, CEP, cidade/UF, data,
mês de referência, valor, consumo/leitura, código de barras, linha digitável, identificador do
documento, emissor/logotipo, QR code/PIX e outro (exige descrição).

O `id` de cada categoria é o valor do pixel em `mascaras_classes`. **Nunca troque o id de uma
categoria já usada.** Para criar uma nova, use um id novo.

## Saída

```
saida/<split>/
  originais/<doc>.png              uma vez por documento (com perda: <doc>__q90.jpg); com canal de
                                   envio ou marcas: uma por versão, <doc>__v01.png (passou pelo
                                   mesmo canal que a editada)
  editadas/<doc>__v01.png          cada variação salva
  mascaras/<doc>__v01.png          0/255: pixels cujo valor mudou (exato, antes da compressão; com
                                   canal de envio, reamostrada pela mesma geometria: ≥ 15% de cobertura)
  mascaras_regiao/<doc>__v01.png   0/255: área editada (pegada das ferramentas)
  mascaras_classes/<doc>__v01.png  id da categoria por pixel (0 = não editado): o QUÊ
  mascaras_tecnicas/<doc>__v01.png nº da técnica por pixel (0 = não editado): o COMO
  mascaras_ignorar/<doc>__v01.png  0/255: rótulo ambíguo, para tirar da perda e da métrica: 1 px em
                                   volta do que mudou (só por fora), a região que não mudou e, com canal
                                   ou saída com perda (JPEG, WEBP, PDF), tudo o que o codec espalhou em volta
  anotacoes/<doc>__v01.json        origem + hashes, categorias, operações e "edicoes" (técnica,
                                   classe, texto antes/depois, caracteres alterados, descrição,
                                   caixa, pixels, fornecedores e perícia de cada edição); "canal"
                                   (passos com a fase: antes/depois da edição), "captura_antes",
                                   "gerador.data_envio", "falsificador" (com a captura e os hábitos),
                                   "dificuldade" (1 − maior nota do perito entre as edições medidas: a
                                   mais visível decide; None sem medida), "origem" (gerador, automatico,
                                   assistido, manual), "ocorrencias_antigas", "auditoria_qualidade"
  manifesto.csv                    uma linha por variação (com fracao_alterada e dificuldade)
  catalogo.csv                     uma linha por EDIÇÃO (técnica, família, nível, classe, textos,
                                   fornecedores)
saida/catalogo.json                legenda dos números das máscaras de técnica e de classe
saida/relatorio_vazamento.txt/.json  resultado da última auditoria (auditar_vazamento.py)
saida/DATASHEET.md, datasheet.json composição, técnicas, canais, marcas, auditoria, usos e limitações
                                   (refeitos a cada lote; ou: python -m rotulador.datasheet saida)
```

- `manifesto.csv` e `catalogo.csv` são refeitos a partir dos JSON quando preciso (exclusão de
  versão, índice de versão antiga). Os JSON são a fonte da verdade.

- `<doc>` é um hash do conteúdo. O nome original do arquivo, que pode ter o nome do cliente,
  fica só no JSON.
- O formato de saída é escolhido no topo: PNG, JPEG, WEBP, TIFF ou PDF.
- **Original e editada do mesmo par saem sempre no mesmo formato e qualidade**, sem EXIF nem
  perfil de cor. Assim o modelo não separa as classes pela compressão ou por metadados.

Pesquisa sobre geração de dados de fraude e o que falta no rotulador: [docs/pesquisa_geracao_dados.md](docs/pesquisa_geracao_dados.md).

## Treino e avaliação (`rotulador/treino`)

O que a literatura de 2025–2026 pede de quem treina (SEED, DocForge-Bench, DTD/CLTD, ADCD-Net, MVSS-Net,
CAT-Net/DocQT; ver a quinta rodada em docs/pesquisa_geracao_dados.md) vem pronto, sem depender de framework:

```python
from rotulador.treino import dados
treino = dados.listar("saida", "treino")      # uma entrada por versão: caminhos, famílias, técnicas, canal, perícia,
                                              # falsificador, fração alterada, dificuldade, pista semântica, revisão
arr = dados.carregar(treino[0])               # original, editada, mascara, regiao, ignorar, classes, tecnicas
arr, theta = dados.aumentar(arr, rng, {"perfil": "misturado", "prob": 0.5})  # canal no treino: igual no par e nas máscaras
rec = dados.recortar(arr, 512, rng)           # recorte centrado na edição (60%), em texto (30%) ou ao acaso
alvo_borda = dados.borda(rec["regiao"])       # borda suave da região (supervisão de borda)
faceis = dados.curriculo(treino, 0.3)         # as 30% mais fáceis pela nota do perito: currículo
dados.pesos(treino)                           # taxa-base (sobre original + editada), peso do positivo, famílias
cj = dados.conjunto_torch(treino, 512, aumento={"perfil": "misturado", "prob": 0.5})  # Dataset do PyTorch, pareado;
                                              # cada item traz "peso": autênticas e adulteradas equilibradas
limpas = dados.listar("saida", "treino", revisao=("aprovada",), sem_alertas=True, sem_ocorrencias_antigas=True)
sem_arial = dados.listar("saida", "teste", excluir_fornecedor={"fonte": "Arial"})  # deixar uma ferramenta de fora
```

- **Par no mesmo lote.** Cada item de `conjunto_torch` traz o MESMO recorte da original (rótulo zero) e da
  editada: o lote pareado do SEED, que generaliza melhor entre domínios do que embaralhar.
- **Recorte com edição.** Com 0,3–4% da página alterada, um recorte 512×512 ao acaso quase nunca tem positivo.
- **Dificuldade.** `dificuldade` na anotação e no manifesto = 1 − maior nota do perito entre as edições medidas
  (0 = pista forte, 1 = invisível para as medidas de grão, nitidez e tom): currículo (CLTD) e mineração de casos
  difíceis.
- **Equilíbrio.** Uma original serve N editadas; `conjunto_torch(equilibrar=True)` dá o mesmo peso total às
  autênticas e às adulteradas (multiplique a perda pela chave `peso`).
- **Aumento de canal.** `aumentar` sorteia um canal (JPEG com tabela de aparelho, WhatsApp, redução, scanner,
  tela...) e aplica na página inteira, antes do recorte.

**Avaliar** (protocolo do DocForge-Bench mais o do datasheet): grave um mapa de probabilidade por versão em
`PRED/<nome>.png` (0..255) e o de cada original em `PRED/originais/<original>.png`, e rode

```
python -m rotulador.treino.avaliar saida validacao PRED --calibrar 10 --md relatorio.md
```

AUC, F1 e IoU por pixel no limiar fixo, F1 oráculo (o teto) e F1 com UM limiar calibrado em 10 versões (com
menos de 1% de pixel adulterado o 0,5 falha, e a calibração recupera boa parte do teto); AUC e TPR com FPR de
1% e 5% por imagem; IC 95% por bootstrap sobre documentos; recortes por família, técnica, nível, perícia, canal,
captura, falsificador, pista semântica e área alterada. A faixa de `mascaras_ignorar` fica fora de tudo.

**Robustez** (DTD/CLTD, ADCD-Net, DocQT): cópias do split perturbadas DEPOIS da edição, com as máscaras levadas
pela mesma geometria, para avaliar perfil a perfil:

```
python -m rotulador.treino.robustez saida validacao E:/robustez --perfis jpeg75,jpeg_dupla,whatsapp,reduzida,desfoque,ruido,tela,impressao
python -m rotulador.treino.avaliar E:/robustez/jpeg75 validacao PRED_jpeg75
```

**Linha de base sem treino:** `python -m rotulador.treino.base saida validacao PRED_base` grava o mapa do perito
como predição: o formato de referência, o piso a superar e um teste de atalho (AUC alta aqui diria que o par
entrega a edição de graça).

Medido em 03/10/2026 (4 documentos sintéticos × 3 variações, falsificadores, canal misturado e marcas; linha de
base sem treino): com acabamento `cru`, AUC por pixel 0,85 (IC 0,84–0,87) e F1 no limiar fixo 0,06; com
acabamento `forense`, AUC 0,72 (IC 0,61–0,81) e F1 0,04; AUC por imagem 0,49 nos dois (sem atalho entre
original e editada). Nas cópias de robustez do lote `cru`, a AUC da base cai para 0,80 (JPEG 75), 0,81 (JPEG
duplo), 0,77 (WhatsApp), 0,77 (reduzida 0,7×) e 0,82 (impressão e nova captura). Um modelo treinado tem de
passar disso.

## Defesas contra vazamento

1. **O split vem só da pasta.** O navegador manda apenas o id do documento. O servidor resolve
   o split e só grava dentro de `saida/<split>`.
2. **Na partida**, cada página recebe dois hashes: um dos bytes do arquivo e outro dos pixels
   decodificados. O segundo pega o mesmo documento salvo em outro formato. Se um hash aparece em
   dois splits, **o servidor não sobe**. Também não sobe se um documento já rotulado (ou com
   rascunho) num split aparecer em outro, por exemplo se alguém mover o arquivo depois.
3. **Colar de outro documento** só aceita doador do mesmo split. A UI só lista esses doadores
   e o servidor recusa os outros.
4. **Valores falsos**: o 🎲 sorteia nomes, CPF/CNPJ válidos, endereços, datas, valores,
   códigos de barras etc. Assim o operador não repete o mesmo "JOÃO DA SILVA" no treino e no
   teste, que o modelo decoraria.
   - O vocabulário aberto (prenomes, sobrenomes, ruas, bairros, cidades) é **repartido entre os
     splits**: um núcleo dos mais comuns (MARIA, JOSÉ, SILVA, as capitais) vale em todos; a cauda é
     repartida por hash de cada entrada; e um nome completo falso é de um split só (hash da combinação).
     Sem isso, todo falso do teste já teria aparecido no treino, e um modelo que lê o texto (OCR +
     layout, VLM) acertaria o teste decorando a lista; e sem o núcleo, "MARIA" só apareceria em
     originais no treino ("MARIA ⇒ genuíno").
   - Vale para o 🎲, o modo simples, as técnicas e o gerador. Meses, UF e "RUA/AV" ficam inteiros.
   - **Bases de valores** (`config/vocabulario/`, refeitas por `ferramentas/construir_vocabulario_nomes.py` e
     `ferramentas/construir_vocabulario_lugares.py`; fontes, licenças e colunas nos `LEIA-ME_*.md`):
     - **nomes**: 100.787 prenomes do Censo 2010 (IBGE, via brasil.io) com a frequência por sexo e a grafia
       acentuada; 20.533 sobrenomes, a partícula de cada um ("DOS SANTOS", "DA SILVA") e a estrutura dos nomes
       (compostos, devoções como MARIA DE FÁTIMA, nº de sobrenomes, FILHO/JÚNIOR/NETO) tiradas das candidaturas
       do TSE 2004–2024 só como contagem agregada (nenhum nome completo é gravado);
     - **lugares**: os 5.570 municípios (população do Censo 2022, DDD, faixas reais de CEP) e uma amostra de
       342 mil trechos de rua reais do CNEFE 2022 (IBGE), cada um com o bairro e o CEP dele, 278 mil bairros e as
       estatísticas reais de número e complemento (APTO 101, BLOCO + APTO, QUADRA + LOTE) por porte da cidade.
     - O nome sai com o peso real (51% mulheres, compostos, partículas e sufixos na taxa do TSE, acento como no
       registro civil quando o documento acentua); o endereço é uma rua de verdade da cidade, no molde do
       original (R./AV., título abreviado, Nº, complemento), de preferência com o MESMO CEP do documento; bairro,
       CEP e cidade trocados na mesma versão saem da mesma linha da base. Datas formam um ciclo de conta real
       (vencimento em dia útil no dia do cliente, emissão, leitura, referência; o histórico de meses anda junto),
       valores e consumo seguem a tarifa, telefone tem o DDD da cidade, e tudo sai no formato do original.
     - Sem as bases (arquivos ausentes), o gerador usa o vocabulário embutido.
     - **Gabarito do Brasil** (`rotulador/gabarito.py`, `config/vocabulario/LEIA-ME_gabarito.md`): o detector de
       campos usa as mesmas bases para *reconhecer* a linha solta sem rótulo. Ele confere nome de pessoa (prenome
       do Censo + sobrenomes), bairro (da cidade, do CEP ou do país), município com UF, tipo de logradouro (406
       tipos do CNEFE + abreviações dos Correios + siglas do DF), concessionária emissora (73) e complemento.
       Na bateria de campos: nome 56% → 99%, endereço 65% → 100%, bairro 46% → 100%, cidade 91% → 99%.
5. **Mesma mistura em todos os splits:** no gerador, a distribuição "equilibrada" conta por split.
   Antes, com uma cota só, o split processado por último ficava com as sobras do primeiro. Num
   teste, o treino tinha 8 edições de valor e o teste 1.
6. **`auditar_vazamento.py`** refaz tudo a partir dos arquivos (código de saída 1 se houver erro).
   - **Erro:**
     - anotação fora do split, doador de outro split;
     - arquivo de saída repetido entre splits;
     - par em formatos diferentes, máscara do tamanho errado;
     - **o mesmo CPF em documentos de splits diferentes** (é o mesmo cliente).
   - **Aviso:**
     - textos digitados repetidos entre splits, e nomes/endereços falsos das técnicas repetidos;
     - o mesmo nome, nº de instalação/cliente ou endereço em splits diferentes;
     - o mesmo **emissor** (CNPJ da concessionária) em mais de um split: a generalização por emissor
       não está sendo medida (`--sugerir-split` propõe uma divisão que mantém cada emissor num split);
     - uma lista de pares *parecidos* entre splits, para revisão humana.
   - O CPF, o nome e o resto vêm do OCR dos documentos de entrada. O OCR fica em cache e só roda
     na primeira vez; `--sem-clientes` pula essa parte.
   - Endereço e CNPJ impressos em mais de 20% dos documentos são considerados do emissor e ficam de
     fora.
7. **Atalhos entre original e editada:** `tests_ui/verifica_atalhos.py SAIDA` procura diferenças
   que não são a fraude e que o modelo aprenderia no lugar dela, como o viés de formato e
   qualidade JPEG do CASIA v2. Confere, par a par:
   - formato, modo, tamanho e chaves de metadados;
   - tabelas de quantização e subamostragem do JPEG, e chunks do PNG;
   - ausência de EXIF/XMP/ICC;
   - nenhum pixel diferente longe da região editada. Em JPEG, conta só fora dos blocos que a tocam.
     Com canal de envio, fora de um raio que cresce com as passagens de JPEG. Com WEBP com perda, a
     conferência é estatística: média < 2,5 níveis e p99,9 ≤ 48 longe da edição.
8. **O que a ferramenta não resolve sozinha:**
   - **Cliente sem CPF legível em splits diferentes:** a auditoria só pega o que o OCR lê. Separe
     por cliente ao montar as pastas.
   - **Pares "parecidos":** a lista ajuda só até certo ponto. Foi medido que o hash perceptual
     **não** distingue "o mesmo papel recapturado" de "outro cliente, mesma conta".
   - **Mesmo modelo de conta em todos os splits:** não é vazamento. Para medir generalização,
     reserve um emissor inteiro só para o teste. Veja
     [docs/tecnicas.md](docs/tecnicas.md#vazamento-e-atalhos).

## IA local (Qwen-Image-Edit-2511 e Qwen-Image-2.1 Uncensored)

Selecione a área, clique em 🤖, escolha o modelo e descreva a troca, por exemplo
`Troque o texto "R$ 151,37" por "R$ 987,65"…`. No modo simples, use **🤖 Trocar com IA**
num campo detectado: o prompt é montado sozinho a partir do texto lido pelo OCR.

Enquanto a IA trabalha, a tela mostra a etapa (carregando o modelo, lendo o pedido, difusão passo N
de M), o tempo e quanto falta. **Cancelar** para a edição no próximo passo; nada é aplicado. O
servidor de IA expõe isso em `/progresso` e `/cancelar`.

| Modelo | Passos | Tempo por campo* | Licença | Uso |
|---|---|---|---|---|
| Qwen-Image-Edit-2511 (GGUF Q4_K_M + LoRA Lightning) | 4 | ~2 min | Apache-2.0 | padrão, mais rápido; erra letras às vezes |
| Qwen-Image-2.1 Uncensored (`abenzerps/Qwen-Image-2.1-Uncensored-GGUF`, Q4_K_M, 4,3 GB) | 40 | ~3,5–4 min | **qwen-research: só pesquisa, não comercial** | escreve o texto certo |

\* "Trocar com IA" no nome e no endereço de uma conta de exemplo, RTX 4060 8 GB
(`tests_ui/ia_trocar_campo.py`). O 2511 escreveu "INCECIAS" no lugar de "ACACIAS" e borrou o fim
de "FERREIRA". O 2.1 acertou os dois, na mesma fonte e alinhamento. Nos dois modelos nenhum pixel
fora da região do campo mudou.

Só um modelo fica carregado por vez: pedir o outro descarrega o atual. O modelo usado vai para o
JSON (`modelo_id`).

- A seleção e uma margem de contexto são ampliadas para cerca de 1 MP, com lados múltiplos de 32.
  Sem isso, o texto pequeno fica ilegível para o modelo.
- O resultado volta para a resolução original e é colado **só dentro da seleção**. O VAE altera a
  imagem inteira, e o que fica fora da seleção é descartado.
- Prompt, semente e passos vão para o JSON.

**Por que esse modelo:**

- O Qwen-Image-Edit-2511 tem licença Apache-2.0 e é o padrão.
- O Qwen-Image-2.1 Uncensored entrou a pedido, como segunda opção. Ele herda a licença do
  Qwen-Image-2.1, que é só de pesquisa: dados gerados com ele não devem treinar um modelo de uso
  comercial. O encoder dele é o Qwen3-VL-8B; aqui ele é usado em 4 bits
  (`unsloth/Qwen3-VL-8B-Instruct-bnb-4bit`, tensores idênticos aos do original).

**Configuração para GPU de 8 GB e pouca memória livre.** Medida nesta máquina: RTX 4060 8 GB, com
2,5 GB de VRAM já ocupados por outros programas e ~6–10 GB de commit livre.

- **transformer:** GGUF Q4_K_M (13,2 GB) **mapeado do disco**, sem cópia. Cada um dos 60 blocos só
  vai para a GPU durante o seu forward. Carregar gasta ~2,5 GB de commit, contra os 13,2 GB do
  caminho padrão do diffusers, e leva 2 s.
- **encoder:** Qwen2.5-VL-7B em 4 bits. Os tensores foram conferidos: é o mesmo encoder do
  Qwen-Image-Edit-2511. Ele é lido do disco a cada pedido, com um leitor somente leitura (o
  `safetensors` padrão reserva 6,9 GB de commit no Windows e falha com "arquivo de paginação muito
  pequeno"). Embeddings e `lm_head` ficam na CPU, o resto vai para a GPU, e tudo é liberado logo
  depois de ler o pedido.
- **LoRA:** Lightning de 4 passos. **VAE:** na GPU, decodificando em blocos.

**Medido numa edição real** (recorte ampliado para 1280×768):

| Etapa | Tempo |
|---|---|
| ler o encoder do disco | 9–30 s (mais rápido quando o arquivo já está em cache) |
| codificar o pedido | 6–11 s |
| difusão | 83–90 s |
| **total por edição** | **~1,5–2,5 min** |

- Pico de VRAM: 5,9 GB. Commit livre durante a edição: ~6 GB.
- O teste trocou "R$ 151,37" por "R$ 987,65" na mesma fonte e cor, sem mudar nenhum pixel fora da
  seleção.

**Instalação:** a IA roda numa venv própria, criada a partir do python que tem torch com CUDA (aqui
é o miniconda):

```powershell
C:\Users\netinhoklz\miniconda3\python.exe -m venv --system-site-packages .venv-ia
.venv-ia\Scripts\python.exe -m pip install -r requirements-ia.txt
.venv-ia\Scripts\python.exe ia_servidor.py --baixar     # ~21 GB em HF_HOME (padrão: E:\hf_cache)
.venv-ia\Scripts\python.exe ia_servidor.py              # deixe rodando; o rotulador detecta sozinho
.venv-ia\Scripts\python.exe ia_servidor.py --teste      # opcional: troca um texto numa imagem e mede
```

O servidor confere a memória antes de carregar (precisa de ~5 GB de commit livre) e recusa com uma
mensagem clara em vez de travar a máquina.

**Problemas de bibliotecas contornados no código:**

- o `peft` do miniconda quebra com o transformers 5.5, por isso a venv própria tem `peft` novo;
- o transformers 5 casa a lista de exclusão do 4 bits com `re.match`, que ancora no início do nome.
  Por isso "visual" não pega "model.visual.*" e a torre visual vira 4 bits apesar da lista: no
  Qwen2.5-VL o peso fica bf16 dentro da camada errada, e no Qwen3-VL fica uint8 corrompido. O
  servidor acrescenta os nomes completos à lista e desfaz o que ainda escapar;
- o leitor de GGUF do diffusers copia os 13 GB para a RAM; o servidor troca por um que mapeia;
- o diffusers trata tensores BF16 do GGUF como bytes crus, e normas e biases em BF16 chegam com o
  dobro do tamanho. O GGUF do 2.1 tem 68 deles, e o leitor os entrega como bf16 de verdade;
- o `QwenImage21Pipeline` só existe no main do diffusers (`requirements-ia.txt` fixa o commit).
- A saída do encoder deixada na GPU prendia, por fragmentação, 6,2 GB do alocador do PyTorch, e o
  `empty_cache` não devolvia nada. Ela agora passa pela CPU enquanto a VRAM é liberada. Com isso o
  2511 fixa 13 blocos na GPU e a difusão cai de 110 s para 83 s. O 2.1 reserva 4,5 GB para as
  ativações (pico medido em 1 MP): com menos, elas transbordam para a RAM compartilhada e ficam 7×
  mais lentas.
- O pipeline 2.1 não aceita embeddings prontos junto com imagem. O servidor codifica o pedido com o
  encoder carregado só durante a codificação e entrega o resultado ao pipeline.

Para testar só o 2.1: `.venv-ia\Scripts\python.exe ia_servidor.py --teste --modelo 21uc`.
Para o servidor carregar o 2.1 ao subir: `--modelo 21uc --carregar-ja`. O rotulador pode pedir qualquer um dos
dois a qualquer momento.

## Testes

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

São 332 testes. Eles cobrem:

- **vazamento:** mesmo conteúdo em PNG e TIFF em splits diferentes, documento rotulado que mudou
  de split, doador de outro split, auditoria, o mesmo CPF (cliente) em dois splits, vocabulário
  dos valores falsos repartido por split, e a cota do gerador valendo dentro de cada split;
- **operações e máscaras:** cada operação mantém intactos os pixels fora da área editada, a
  máscara bate com a diferença real e desfazer/refazer restaura bit a bit;
- **códigos:** 20 códigos de barras gerados são decodificados de volta, o QR é lido pelo OpenCV,
  os DVs de CPF, CNPJ, arrecadação e boleto conferem;
- **salvamento:** formatos de saída e rascunho que sobrevive à queda do servidor.
- **regressões da revisão** (`test_revisao.py`): cada defeito achado nas rodadas de revisão tem
  um teste (receita estranha dá 400, original órfã, caixas das marcas, auditoria sem OCR, TIFF
  determinístico, datasheet com anotação antiga, CPF de modelo, vocabulário, Jitter, colagem da IA);
- **canal de envio, marcas e disfarce:** o par só difere perto da edição em todos os perfis (e,
  sem codecs, nada difere: todo campo de ruído/luz/sombra é do par), a máscara acompanha a
  geometria, determinismo, lote com canal + marcas + datasheet, colagem da IA nos três modos,
  Texture Jitter sem mudar o texto e disfarce dentro da pegada.
- **automático:**
  - classificação de campos: padrões, rótulos tolerantes a erro de OCR, rótulo grudado no valor;
  - valores no mesmo formato do original e escolha de fonte (Arial × Arial Bold);
  - troca de campo e fraude automática com desfazer;
  - um lote completo do gerador pela API, que salva só no split pedido, e exclusão pelo catálogo.
- **técnicas** (`test_tecnicas.py`, em documentos sintéticos com o OCR em cache):
  - letras separadas uma a uma;
  - cada técnica muda só a região do campo, e o **OCR relê o valor novo**;
  - a mesma semente dá o mesmo resultado (a prévia é igual ao que é aplicado);
  - doadores só do mesmo split; plausibilidade; IA com editor falso;
  - distribuição equilibrada;
  - máscara de técnicas e `catalogo.csv` batem com as edições, os índices são refeitos e a
    exclusão funciona;
  - a camada de cima vence também na máscara de técnicas.
- **camadas:** a de cima vence na máscara, ocultar tira dos rótulos, opacidade, excluir/mesclar/duplicar
  com desfazer, a borracha revela a camada de baixo, mover camada, rascunho com camadas;
- **ferramentas profissionais:** Poisson (identidade e cor do destino), carimbo com fonte original,
  escala e rotação, recuperação, remendo, os 7 modos de retoque, formas, amostragens do
  preenchimento, igualar ruído calibrado, seleção por tinta e por pincel.
- **tipos de edição** (`test_tipos.py`):
  - cada técnica automática está em um jeito;
  - compatibilidade técnica × classe;
  - pesos divididos pelas combinações e erros claros;
  - o plano só sorteia combinações dos tipos, na proporção dos pesos;
  - prévia, lote e exemplos reais pela API.
- **identificador de fonte** (`test_fonte.py`):
  - acerta a fonte de linhas degradadas como scan (≥85% em 1º, ≥95% entre as 3 primeiras);
  - tamanho e largura;
  - fontes sem as letras do texto ficam de fora;
  - voto do documento;
  - a API devolve o texto desenhado em cada fonte.
- **perícia / acabamento forense** (`test_pericia.py`): casar iluminação (o fundo da região segue o
  gradiente de luz do papel), grão espectral (ruído com o MESMO espectro do papel, não branco) e
  nitidez (igualada à vizinhança); o nível `auto` não muda o de hoje; `forense` casa os sinais, não
  vaza para fora da região e fica gravado e filtrável (receita, anotação, catálogo, datasheet).
- **técnicas do mundo real** (`test_humano.py`): esconder com objeto (4 objetos), rabiscar (3 tintas),
  borrar (2 modos), corretivo em 3 formas, caneta nos traços e recorte de papel — cada uma mexe só perto
  do campo, cobre o texto e o OCR não lê mais o valor antigo; o falsificador "no papel" sempre fotografa.
- **falsificador humano** (`test_falsificador.py`):
  - formatos de data, mês e caixa; valor redondo;
  - receita valida pesos e motivos (lista = só esses);
  - sorteio respeita a probabilidade, a IA desligada e as técnicas permitidas;
  - hábitos fixos por versão (fonte, colagem, retângulo, disfarce) e o caminho do documento;
  - motivo recência: data de 3 a 85 dias atrás, a mesma nas duas datas, mês de referência anterior;
  - motivo identidade: o mesmo nome em todo lugar, CPF às vezes esquecido;
  - a ferramenta restringe as técnicas; motivo que não cabe cai para outro ou para o sorteio clássico;
  - prévia e lote gravam quem fez, o catálogo filtra por falsificador e motivo, o datasheet conta.
- **equipe** (`test_equipe.py`):
  - trava entre duas pessoas e assumir;
  - atribuição;
  - revisão, fila e exportação filtrada;
  - metas;
  - pré-leitura do OCR;
  - progresso e cancelamento da IA.
- **fornecedores** (`test_fornecedores.py`): cada apagador tira a tinta e fica no rótulo; vestígio deixa um
  fantasma; os quatro rasterizadores escrevem legível, o ClearType tem franjas coloridas e o aliasado
  só dois tons; o bloco de endereço vem inteiro de outro documento do mesmo split.
- **edição vetorial** (`test_vetorial.py`): PDF com texto é reconhecido, a troca muda só o campo, o
  rótulo diz `pdf-vetorial`, a fonte embutida é usada quando tem as letras, e o gerador só oferece a
  técnica em PDF com texto (nunca depois de uma captura).
- **canal em duas fases** (`test_fases.py`): data de envio por documento; captura antes vira a base da
  versão, com original própria, máscaras do tamanho da imagem salva e par igual fora da edição;
  captura depois continua como era.
- **geradores** (`test_geradores.py`): data de referência aninhada, CEP na faixa da cidade, vocabulário
  repartido, partes da linha digitável, código com o valor pedido.
- **perito** (`test_perito.py`): retângulo chapado dá nota alta, cópia do papel dá nota baixa, mapa e
  rota da sessão.
- **edição exata e limpa** (`test_exato.py`): espaços recuperados numa linha com fio de tabela; caixa da
  linha sem o rótulo de cima e sem o fio; o apagamento preserva o fio; o detector recusa rótulos como
  endereço, acha o nome e o bairro no bloco de endereçamento sem rótulo e separa o endereço do beneficiário
  (emissor) do do pagador; a fonte sai entre as 10 mais usadas, com o peso certo; a técnica da mesma fonte não
  toca no rótulo nem no fio; a edição exata dá o mesmo nome e endereço novos nos dois lugares e uma cidade
  no formato do campo; a API edita só os campos mandados; o lote limpo grava a original igual ao documento e
  a editada diferente só na máscara.
- **marcas digitais** (`test_marcas_digitais.py`): sem captura física só as digitais entram; tarja, borrão e
  pixelado cobrem a caixa do campo sensível inteira, nunca a edição, e o par fica igual; a marca d'água é
  igual no par; o lote pelo WhatsApp grava só marcas digitais.
- **recorte e captura** (`test_recorte.py`): depois da edição a janela contém a edição (e cresce se
  preciso); antes dela a janela é livre e os pontos acompanham; scanner em cinza (canais iguais) e P&B
  (quase tudo 0 ou 255, a edição vira tinta preta); as cinco texturas de fundo; o sorteio grava tudo.
- **qualidade** (`test_qualidade.py`): versão limpa sem alerta; cada defeito dá o seu alerta; duplicadas;
  `--gravar` escreve na anotação e o datasheet conta (e lista as lacunas de cobertura).
- **treino** (`test_treino.py`): o carregador lê um split (dificuldade, famílias, canal), o recorte com
  `p_edicao=1` sempre contém a edição e o de texto sempre contém tinta, a borda suave é 1 na fronteira e 0
  longe, o currículo ordena pela dificuldade, o aumento de canal mantém o par igual fora da edição; na
  avaliação, predição perfeita dá AUC/F1/IoU 1 e predição com confiança 0,3 dá F1 0 no limiar fixo mas
  oráculo 1 e calibrado 1; o conjunto de robustez sai coerente (máscaras, caixas, índices) e a linha de base
  gera predições que a avaliação lê.

O modo exato do gerador tem `ui_exato.py` (abre no exato com nome e endereço marcados, atalhos, prévia
toda na mesma fonte, lote de 1 imagem conferido na anotação e nos pixels). O modo fácil tem `ui_tipos.py`, que:

- acrescenta uma ideia pronta e cria um tipo pelo diálogo;
- testa o tipo sozinho;
- gera um lote e confere que só entraram as combinações do tipo;
- confere o aviso do modo avançado e o celular sem rolagem lateral.

O trabalho em equipe tem `ui_equipe.py`, com dois navegadores, ou seja, duas pessoas:

- nome obrigatório, trava e assumir;
- busca, atalhos e sugestão de categoria;
- "Qual é a fonte?";
- revisão por teclado, aprovação em lote e exportação;
- metas e atribuição.

O modo simples tem o seu próprio teste de interface, `ui_simples.py` (com os documentos realistas). O
gerador também tem o seu, `ui_gerador.py` (51 checks). Ele usa os presets, a prévia e a geração de
8 imagens, e no catálogo a matriz, os filtros, o detalhe e a exclusão. No editor, aplica uma técnica
pelo "Como editar" e a fraude automática. Falha com erro de JavaScript ou traceback no servidor.

Também há testes de interface com Selenium + Edge em `tests_ui/`: 27 checks no fluxo geral
(`ui_fluxo.py`) e 25 nas camadas e ferramentas novas (`ui_camadas.py`). Eles usam documentos
sintéticos numa pasta de trabalho separada, nunca os seus `dados/`:

```powershell
.venv\Scripts\python.exe tests_ui\gerar_amostras.py C:\tmp\rot\dados
.venv\Scripts\python.exe app.py --dados C:\tmp\rot\dados --saida C:\tmp\rot\saida_teste --porta 5077 --sem-navegador
python tests_ui\ui_camadas.py C:\tmp\rot        # em outro terminal (precisa de selenium e do Edge)
```

`tests_ui/ia_trocar_campo.py <pasta>` faz o "Trocar com IA" real no nome e no endereço com os dois
modelos. Ele salva antes/depois e confere que nada fora dos campos mudou (~12 min; rotulador na
5010 e servidor de IA rodando; não salva nada).

`tests_ui/ia_ponta_a_ponta.py PASTA` faz uma edição real por IA através do rotulador. Precisa do
rotulador na porta 5077 e do servidor de IA rodando.

**Layout** (`ui_responsivo.py PASTA`): tela de computador e de celular. Confere o tutorial com
conteúdo, o nome do documento no topo, o documento visível e sem rolagem lateral no celular, e o
diálogo flutuante dentro da tela.

**Teste de todas as ferramentas** (`ui_todas.py`, 62 casos) num documento **realista**: A4 a 300 DPI,
com grão de papel, desfoque de scan, JPEG e uma foto de celular com orientação EXIF. Ele usa cada
ferramenta e cada opção como um usuário, e falha se aparecer:
- aviso de erro na tela;
- erro de JavaScript;
- traceback no log do servidor;
- operação sem efeito.

```powershell
.venv\Scripts\python.exe tests_ui\gerar_amostras.py E:\rotulador\real\dados --realista
.venv\Scripts\python.exe app.py --dados E:\rotulador\real\dados --saida E:\rotulador\real\saida --porta 5077 --sem-navegador > E:\rotulador\real\servidor.log 2>&1
python tests_ui\ui_todas.py E:\rotulador\real --ia   # --ia inclui a edição por IA (servidor de IA rodando)
```

**Verificação das técnicas automáticas em documentos de verdade.** Cada técnica é aplicada em
cada campo onde serve. O OCR da página inteira precisa ler o valor novo, sem exceção e sem mexer
longe do campo. Também são geradas folhas de contato antes/depois para revisar a olho.
Resultados e problemas encontrados: [docs/tecnicas.md](docs/tecnicas.md#verificação-em-documentos-variados).

```powershell
.venv\Scripts\python.exe tests_ui\gerar_variados.py E:\rotulador\variado\dados    # 6 fontes/layouts + foto
.venv\Scripts\python.exe tests_ui\verifica_tecnicas.py E:\rotulador\variado\dados E:\rotulador\verifica\variado
.venv\Scripts\python.exe tests_ui\verifica_tecnicas.py E:\rotulador\variado\dados E:\rotulador\verifica\ia --ia --docs=calibri.jpg
.venv\Scripts\python.exe tests_ui\verifica_saida.py E:\rotulador\variado\saida   # confere máscaras, rótulos e índices gravados
.venv\Scripts\python.exe tests_ui\verifica_atalhos.py E:\rotulador\variado\saida # atalhos original x editada, mistura por split
```
