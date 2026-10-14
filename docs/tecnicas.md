# Técnicas automáticas de adulteração e o catálogo de rótulos

O gerador automático produz documentos adulterados com **28 técnicas**. Para cada edição ele
grava:

- o **quê** foi editado: a categoria (nome, valor, data…, de `config/categorias.json`);
- o **como**: a técnica, a família de fraude, o nível e o alcance;
- o **antes e depois**: o texto antigo, o novo e quais caracteres mudaram;
- uma **descrição** em português.

O catálogo fica em `rotulador/catalogo.py` e as implementações em `rotulador/ops/tecnicas.py`.

## Taxonomia

As famílias seguem os tipos de fraude do *Find it again!* (ICDAR 2023), que anota recibos
forjados por pessoas:

| Família | Código | O que é |
|---|---|---|
| Imitação | IMI | o texto novo é desenhado imitando a fonte do documento |
| Cópia interna | CPI | letras ou campos copiados de outro lugar do **mesmo** documento (copy-move) |
| Colagem de outro documento | CPO | letras ou campos trazidos de **outro** documento do mesmo split (splicing) |
| Remoção | CUT | conteúdo apagado (preenchimento, corretivo, letra removida) |
| Traços e pixels | PIX | traços acrescentados ou raspados de um dígito (3→8, 4→1) |
| Códigos | IMI | código de barras ou QR gerado de novo |
| Generativa | GEN | um modelo de difusão (Qwen) reescreve a região |
| Recriação | FAB | o documento inteiro é refeito num editor: mesmo layout, todas as linhas re-tipografadas com o mesmo texto; logotipo e códigos ficam (documento fabricado) |

Além da família, cada técnica tem dois atributos:

- **Nível:**
  - *amador*: o que alguém faz no Paint ou no Word (retângulo liso, fonte padrão, caneta);
  - *profissional*: fonte imitada, preenchimento inteligente, grão igualado;
  - *IA*: generativa.
- **Alcance:** caractere, trecho do campo, campo inteiro ou a página inteira.

As edições feitas à mão no modo avançado também entram no catálogo, com números de 100 a 111
(`manual.*`).

## As técnicas

| nº | id | Técnica | Família (lit.) | Nível | Alcance | Opções (o "como") |
|---|---|---|---|---|---|---|
| 1 | `campo.reescrever` | Reescrever o campo (fonte imitada) | Imitação (IMI) | profissional | campo inteiro | alinhamento: esquerda / direita; fonte: imitada / substituta (a do editor de PDF: Arial, Helvetica, Times, Courier, a mais parecida) |
| 2 | `campo.amador` | Reescrever estilo amador (retângulo + fonte padrão) | Imitação (IMI) | amador | campo inteiro | retângulo: papel / branco; fonte: Arial / Calibri / Times New Roman / Verdana |
| 3 | `campo.acrescentar` | Acrescentar texto no fim do campo (só nome, endereço, bairro, identificador) | Imitação (IMI) | profissional | trecho | — |
| 4 | `trecho.trocar` | Trocar só um trecho (dia, centavos, número...) | Imitação (IMI) | profissional | trecho | — |
| 5 | `caractere.redesenhar` | Trocar um caractere (redesenhado na fonte) | Imitação (IMI) | profissional | caractere | que caractere: dígito / letra / qualquer |
| 6 | `caractere.inserir` | Inserir um dígito (só valor, consumo, endereço, identificador) | Imitação (IMI) | profissional | caractere | como apagar |
| 7 | `campo.reescrever_pdf` | Reescrever na camada de texto do PDF (edição vetorial) | Imitação (IMI) | profissional | campo inteiro | fonte: embutida (a do PDF, se tem as letras) / substituta (Helvetica, Times, Courier: a reserva do editor); alinhamento. Só em PDF com texto; sem rastro de pixel |
| 8 | `campo.mesma_fonte` | Reescrever na mesma fonte (as 10 mais usadas em documentos) | Imitação (IMI) | profissional | campo inteiro | a fonte de CADA campo é identificada só entre Arial, Helvetica, Times New Roman, Calibri, Verdana, Tahoma, Segoe UI, Courier New, Georgia e Arial Narrow (regular ou negrito, pelo traço); alinhamento: esquerda (nome, endereço) / direita (valor, consumo); como apagar: cor do papel em fundo liso, PatchMatch em fundo com textura. A técnica padrão da edição exata e do modo simples |
| 10 | `caractere.copiar_glifo` | Trocar um caractere copiando a letra do próprio documento | Cópia interna (CPI) | profissional | caractere | como colar: tinta / retângulo / Poisson / recorte de papel (borda e sombra); que caractere |
| 11 | `caractere.transpor` | Inverter dois dígitos vizinhos | Cópia interna (CPI) | profissional | caractere | — |
| 12 | `campo.copiar_interno` | Copiar outro campo do documento por cima | Cópia interna (CPI) | profissional | campo inteiro | como colar |
| 20 | `caractere.glifo_externo` | Trocar um caractere com letra de outro documento | Colagem (CPO) | profissional | caractere | como colar; que caractere |
| 21 | `campo.colagem_externa` | Colar o mesmo campo de outro documento (splicing) | Colagem (CPO) | profissional | campo inteiro | como colar; como apagar |
| 22 | `bloco.colagem_externa` | Colar o bloco de endereço inteiro (rua, bairro, CEP, cidade em linhas vizinhas) de outro documento | Colagem (CPO) | profissional | campo inteiro (várias linhas; a classe da máscara é a do campo escolhido) | como colar; como apagar |
| 30 | `caractere.apagar` | Apagar um caractere (só campos de tamanho variável) | Remoção (CUT) | profissional | caractere | o vão: fechar / deixar; que caractere |
| 31 | `campo.remover` | Apagar o campo (preenchimento inteligente) | Remoção (CUT) | profissional | campo inteiro | o que apagar: campo / última palavra |
| 32 | `campo.corretivo` | Corretivo (retângulo liso) | Remoção (CUT) | amador | campo inteiro | retângulo: papel / branco; forma: retângulo / pinceladas do app / corretivo líquido no papel |
| 33 | `campo.ocultar` | Esconder o campo com um objeto na foto | Remoção (CUT) | amador | campo inteiro | objeto: dedo / adesivo / post-it / fita |
| 34 | `campo.rabiscar` | Rabiscar por cima até ficar ilegível | Remoção (CUT) | amador | campo inteiro | tinta: caneta esferográfica / marcador / pincel do app |
| 35 | `campo.borrar` | Borrar ou pixelizar (app de celular) | Remoção (CUT) | amador | campo inteiro | modo: desfoque / mosaico |
| 40 | `caractere.acrescentar_tracos` | Acrescentar traços (3→8, 1→4...) | Traços (PIX) | amador | caractere | cor do traço: mesma / diferente; tinta: digital / caneta esferográfica no papel |
| 41 | `caractere.raspar_tracos` | Raspar traços (4→1...) | Traços (PIX) | profissional | caractere | — |
| 42 | `trecho.jitter` | Retoque sem mudar o texto (Texture Jitter) | Traços (PIX) | profissional | trecho | desfocar / reamostrar / tremido / aguçar / JPEG / tamanho / combinado |
| 50 | `codigo.regenerar` | Novo código de barras (+ linha digitável coerente) | Códigos | profissional | campo inteiro | — |
| 51 | `codigo.qr` | Novo QR code / PIX | Códigos | profissional | campo inteiro | — |
| 60 | `ia.reescrever` | IA reescreve o campo | Generativa (GEN) | IA | campo inteiro | colagem: região / borda suave / retângulo em volta |
| 61 | `ia.caractere` | IA troca um caractere | Generativa (GEN) | IA | caractere | que caractere |
| 62 | `ia.remover` | IA apaga o campo | Generativa (GEN) | IA | campo inteiro | colagem: região / borda suave / retângulo em volta |
| 112 | `pagina.recriar` | Documento refeito do zero: todas as linhas de texto da página apagadas e re-tipografadas com o mesmo texto; logotipo, códigos e linhas de tabela ficam (os campos trocados vêm depois, por cima) | Recriação (FAB) | profissional | página inteira | fonte: imitada (cada linha na fonte mais parecida) / substituta (Arial, Calibri, Times New Roman ou Verdana, uma para a página toda, nítida); rasterizador; como apagar. Só pelo pseudo-campo da página (tipo `pagina`), nunca por um campo |

O **nº** é o valor do pixel na máscara de técnicas e nunca muda. Uma técnica nova recebe um número
novo.

**Opções comuns a várias técnicas** (cada ferramenta real faz de um jeito, e um fornecedor só vira a
impressão digital do gerador):

- **como apagar** (`apagar`, em toda técnica que apaga texto; `rotulador/ops/apagar.py`): `patchmatch`
  (Content-Aware Fill: retalhos do papel vizinho), `telea` e `ns` (inpainting por difusão do OpenCV,
  a borracha dos apps de celular: papel liso, sem grão), `chapado` (balde do Paint: a cor mediana do
  papel), `borrao` (média ponderada do entorno) e `carimbo` (um retalho de papel vizinho copiado
  em faixas ao longo da linha, cada uma de um lugar de papel livre, mantendo o deslocamento: grão duplicado) e
  `recuperar` (pincel de recuperação: a textura de um retalho vizinho no tom do entorno). O `campo.reescrever` apaga pelo mesmo caminho (`texto.apagar`);
- **vestígio** (`vestigio`, nas técnicas que apagam um campo ou trecho): `leve` deixa um fantasma de 8 a
  20% da tinta antiga por baixo (apagado malfeito). No sorteio clássico entra em 15% das edições; nos
  falsificadores, pelo hábito (celular 35%, Paint 30%, Photoshop 5%, editor de PDF e papel nunca);
- **rasterizador** (`rasterizador`, no `campo.amador` e no `pagina.recriar`): `freetype` (antialias padrão, Skia/Photoshop),
  `gdi` (Windows sem ClearType: linha de base em pixel inteiro e quatro tons de cinza), `cleartype`
  (franjas coloridas R/B nas bordas: texto do Windows capturado da tela) e `aliasado`;
- no `campo.reescrever`, o desleixo do falsificador (`capricho` > 1) escorrega a linha de base e muda o
  espaçamento entre letras; no `caractere.copiar_glifo` colado como retângulo, estica a letra na
  horizontal (quem arrasta o canto no Paint).

O fornecedor de cada peça (`apagar`, `texto`, `fonte`, `grao`, `colagem`, `vestigio`) vai para
`edicoes[].fornecedores` na anotação e para a coluna `fornecedores` do `catalogo.csv`.

## Como funcionam

**Separar as letras** (`rotulador/glifos.py`). O OCR dá só a caixa da linha. Cada caractere é
localizado assim:

1. o texto é desenhado na fonte do documento;
2. a largura de cada caractere dá o ponto previsto de corte entre letras, que é mapeado para a
   tinta de cada palavra;
3. o corte final cai na coluna com menos tinta perto da previsão.

Isso separa letras coladas e mantém juntos pingos, acentos e a cauda da vírgula. Se a tinta não
bate com o texto (OCR errado), a técnica recusa o campo.

**Peças comuns:**

- **Fonte:** fonte, tamanho, largura e negrito vêm de `auto.ajustar_fonte`, que desenha o texto
  antigo em cerca de 24 fontes e fica com a de maior sobreposição.
- **Desfoque:** é estimado pela razão Σ|∇|²/Σ|∇| das bordas, que não depende do alinhamento. O
  texto desenhado sai tão macio quanto o do scan.
- **Linha de base:** é a mediana do pé das letras sem descendente.
- **Fundo:** a letra a apagar vira buraco num PatchMatch em que **toda** a tinta em volta também
  é buraco. Assim só papel é copiado e as letras vizinhas não "vazam" para dentro.
- **Glifo copiado:**
  - o alfa é suave, pela projeção de cada pixel na reta papel→tinta;
  - a posição tem precisão subpixel;
  - a cor da tinta pode ser trocada;
  - a altura é ajustada à letra de destino.

**Fonte imitada.** A escolha da fonte tem duas passadas:

1. compara o desenho de cada candidata com a tinta;
2. desfoca as melhores no mesmo tanto que o scan e compara com a **intensidade** da tinta por
   correlação normalizada, que não depende do contraste.

Comparar só máscaras nítidas fazia a Courier fina do documento parecer Courier Bold, porque o scan
engrossa o traço. O negrito sintético só entra se o traço mais grosso parecer mais com a tinta.

**Conferência final por OCR.** Toda técnica que escreve texto termina relendo o campo editado com
o reconhecedor do OCR, em **dois recortes diferentes**. Se a leitura não é o valor novo, o
resultado é **descartado** e o gerador tenta outra edição.

- Só vale quando o OCR lê certo o campo original (controle).
- As confusões clássicas do OCR são toleradas: O/0, I/1, "R$" lido como "RS", "/" lido como "I".
- Um dígito trocado continua sendo diferença.

**Controles de qualidade** (uma técnica que não passa recusa o campo, e o gerador tenta outro):

- **Plausibilidade:** a data continua existindo, o mês fica entre 1 e 12 e o ano a até 3 anos do
  original. Valor e consumo não ganham zero à esquerda.
- **Formato fixo:** data, CEP e CPF nunca ganham nem perdem dígito. Em campos numéricos, como
  valor ou data, só dígitos mudam (o "R$", o "kWh" e o "SET" não).
- **Forma:** a letra antes de ser trocada e a letra copiada são comparadas com a mesma letra
  desenhada na fonte (IoU). Isso protege de OCR errado e de letra de outra fonte.
- **Cor e altura:** a letra copiada precisa ter a mesma cor de tinta e uma altura compatível.
  Rótulo cinza ou cabeçalho branco não servem.
- **Traços:** o resultado é comparado com os 10 dígitos desenhados na fonte e precisa ser lido
  como o dígito novo, com folga sobre o antigo. Sem isso, um 0 com a cintura do 8 passava (vira θ).
- **IA num caractere:** o modelo redesenha o campo inteiro um pouco deslocado. Então:
  - a saída é **alinhada** ao documento pela tinta das letras que não mudaram;
  - só a letra trocada é aproveitada, com borda suave;
  - ela só é aceita se **lê como o caractere pedido** (mesma comparação com a fonte usada nos
    traços). Senão o resultado é descartado e o gerador tenta outra edição.
- **Mesmo split:** letras e campos de outro documento vêm só do mesmo split. `auditar_vazamento.py`
  confere isso nas anotações.

**Acabamento.** O grão do papel é igualado automaticamente nas técnicas profissionais e de IA, e
não nas amadoras: por padrão 70% das vezes com ruído com o **mesmo espectro** do papel em volta
(`rotulador/pericia.py`, `casar_grao`) e 30% com ruído branco (o Add Noise do Photoshop; só a
variância), ou pelo hábito do falsificador (`acabamento.grao` força). Medido antes da mudança: o grão
branco deixava a autocorrelação do grão dentro da região diferente da de fora em toda edição
profissional, a pista nº 1 de um detector de resíduo. A recompressão JPEG local da área editada é
opcional e simula quem edita e salva num editor. **Código de barras novo:** o valor é o total impresso
no documento (ou o total novo da versão, quando o falsificador é do tipo que refaz o código para
combinar) e a empresa/segmento (ou o banco) são os da linha digitável original.

## Bateria das ferramentas (11–12/10/2026)

Cada ferramenta testada em 11 páginas (as 10 realistas e a conta NIO). Os scripts ficam em `E:/rotulador/ferr`:
`bateria_apagar.py` (cada seleção × apagador, só apagando: tinta que sobra, tom e grão dentro × em volta),
`bateria_texto.py` (reescreve o campo com o MESMO texto e compara espessura, sobreposição, tom e nitidez) e
`tests_ui/verifica_tecnicas.py` (todas as técnicas; agora com `--humano` e com a nota do perito e o tom de cada edição).

| Achado | Antes | Depois |
|---|---|---|
| carimbo/fundo clonado: procurava UM retalho do tamanho de todo o buraco (que inclui a tinta do recorte inteiro) e caía no PatchMatch | origem achada em 0 de 16 campos; grão 0,78 | faixas ao longo da linha com origem alinhada (`apagar._carimbo`): grão 0,96–0,97, ~10× mais rápido |
| PatchMatch: a votação tira a média de dezenas de patches e o grão some (mancha lisa com a forma do texto) | grão 0,76–0,80 (retângulo), 0,28 (só as letras) | tom/forma da votação + passa-alta do patch escolhido (`preenchimento._com_grao`): 0,89–0,91 e 0,49 |
| telea/ns (borracha de app): o halo do antialias na borda virava manchinha no miolo das letras redondas | manchas visíveis | buraco com 2 px de folga |
| texto claro sobre fundo escuro (o CNPJ branco na faixa azul): o papel branco de fora virava "papel" e o azul "tinta" | borrão branco engordado | recusado (`tecnicas.texto_claro_em_fundo_escuro`) |
| raspar traço com o operador humano: a seleção larga levava o dígito inteiro | 110 recusas | borracha pequena (85, igual ao normal) |
| letras do documento ignoravam a ferramenta da pessoa | sempre só a tinta, fundo local | a seleção e o apagador do operador humano |
| datas no perfil profissional: o dígito trocado escolhia a data sozinho | "Emissão 25/01/2026 → 2023" | só o campo inteiro, no ciclo de datas da versão |
| detector: "Emissão em 25/01/2026" ficava com o "em" (a fonte saía a da palavra regular) | data exata 84/132 | 90/132 |
| detector: "CEP CIDADE UF" numa linha virava só CEP | CEP 14%, cidade 8% (bateria com gabarito) | CEP 93%, cidade 91%, sem falso positivo novo |
| cidade nova no formato "CIDADE UF" (só espaço) perdia a UF | "LEOPOLDINA" | "LEOPOLDINA MG" |

Segunda rodada (12/10/2026): as ferramentas do **editor manual** e as peças do operador humano que faltavam
(`E:/rotulador/ferr/bateria_editor.py`, `bateria_salvar.py`):

| Ferramenta | Achado | Antes → depois |
|---|---|---|
| balde de tinta e varinha mágica | `floodFill` do OpenCV 5 recusa imagem somente leitura (a original da sessão é travada) | erro em toda chamada sobre a original → cópia gravável |
| pincel de recuperação e remendo | o `seamlessClone` puxava a cor de tudo que cruzava a borda: fantasma do texto apagado e, perto de fio de tabela, borrão branco-alaranjado | costura 4,3 → mescla de membrana pelo papel da borda (`pro.poisson`); remendo sem nenhum caso ruim, tom 1,0 → 0,0 |
| igualar ruído | ruído BRANCO (autocorrelação −0,05 × +0,3 do papel; grão 0,82) e, em PDF limpo de layout denso, 2–2,6x o papel | grão com o espectro do papel (`espectro: "papel"`, padrão; `"branco"` = o antigo): 0,93–0,98 com a autocorrelação do papel; papel liso de verdade → recusa |
| código de barras / QR regenerado | preto puro e nítido num scan em que o original é cinza e borrado; ITF de 44 dígitos aceito numa caixa de 494 px (módulos de 1,2 px, ilegível) | cor da tinta e desfoque do código original; recusa abaixo de 1,4 px por módulo (medido: lê a partir de 1,4, mesmo desfocado) |
| perito (a nota de cada edição, no Salvar do editor) | dilatação elíptica com núcleo do tamanho da margem: 128 s numa camada grande (Salvar parado ~1 min) | transformada de distância: 0,5 s |

Sem defeito: preencher, corretivo, carimbo (grão 0,84–1,06, costura < 1, nada de tinta sobrando); mover/duplicar e
colar por identidade = cópia exata; os 9 ajustes, formas, pincel, texto e os 7 modos do retoque não mexem fora da
seleção/traço. O "Salvar como" do operador humano deixa o rastro previsto (JPEG ghost com as tabelas da origem,
mudança média por bloco editado × em volta): mesmas tabelas 0,06 × 0,01 (escondido), PNG 1,49 × 0,01, JPEG do editor
1,05 × 0,44 (a dupla compressão local).

Ferramenta de texto (reescrever o mesmo texto): espessura 0,98–1,04 e tom ±1 nível em todas as suavizações; o texto
"nítido como digitou" sai 1,4–1,6× mais nítido que o scan e a suavização "Nenhuma" 1,8× — os rastros previstos.

Continua: a 2ª data da linha ("Período de 18/03/2026 a **23/01/2026**") não é detectada e fica velha; fonte fora das
10 mais usadas (a geométrica da NIO) sai aproximada; o PatchMatch ainda fica com ~0,9 do grão (rastro de
preenchimento inteligente, aceitável).

## Rótulos gravados

Para cada versão salva, além das máscaras de pixels alterados, de região e de classes:

- `mascaras_tecnicas/<doc>__vNN.png`: uint8, nº da técnica por pixel (0 = não editado). A regra é
  a mesma da máscara de classes: vale a camada visível mais alta e, dentro dela, a última edição.
- `anotacoes/<doc>__vNN.json` → `edicoes`: uma entrada por edição **visível**, com:
  - `tecnica`, `tecnica_num`, `nome_tecnica`, `familia`, `nivel` (`simples` = amador, `profissional`,
    `ia`, `manual`), `granularidade`, `literatura`;
  - `categoria`, `texto_antigo`, `texto_novo`;
  - `alteracoes` (posição, de, para, tipo), `opcoes` (o "como" sorteado), `acabamento`;
  - `descricao`, `bbox` e `pixels` (os pixels dela na máscara);
  - `origem_glifo` / `origem_campo` (documento doador), quando há.
- `anotacoes/...json` → `gerador`: lote, receita e variação, quando veio do gerador automático.
- `catalogo.csv` (por split): uma linha por edição, com versão, técnica, família, nível, classe,
  textos, caixa, descrição, origem (gerador/manual) e operador.
- `saida/catalogo.json`: a legenda dos números das máscaras de técnica e de classe.

O `manifesto.csv` e o `catalogo.csv` são **refeitos a partir dos JSON** quando preciso: ao excluir
uma versão, ou quando o índice é de uma versão antiga da ferramenta. Os JSON são a fonte da verdade.

## Receita (JSON)

A página do Gerador salva e carrega receitas neste formato:

```json
{
  "nome": "minha receita",
  "documentos": {"splits": ["treino"], "ids": null, "so_pendentes": false},
  "tecnicas": {"caractere.copiar_glifo": 2, "trecho.trocar": 1, "campo.amador": 1},
  "opcoes": {"caractere.copiar_glifo": {"colagem": "tinta"}},
  "categorias": {"valor": 3, "data": 2, "nome": 2.5},
  "edicoes": [1, 3], "variacoes": 2, "distribuicao": "equilibrada",
  "acabamento": {"ruido": "auto", "disfarce": "auto", "jpeg": 0.2, "pericia": "auto", "grao": "auto"},
  "canal": {"perfil": "misturado", "prob": 0.7, "captura": "auto"}, "marcas": {"prob": 0.3},
  "dificuldade": {"variantes": 1, "prob": 1.0, "detector": null}, "humano": {"prob": 0.85, "salvar": "auto"},
  "formato": "png", "qualidade": 95, "operador": "", "modelo_ia": null
}
```

- **`canal`:** `misturado`, `scanner`, `celular`, `app_scanner`, `tela`, `impressao`, `whatsapp` ou
  `nenhum`; `prob` é a fração das versões que passam pelo canal; `captura` (`auto`, `antes`, `depois`)
  diz se a foto/scan vem antes ou depois da edição. Sem a chave (pela API), não há canal; a página do
  Gerador começa com o misturado em 70%.
- **`acabamento.grao`:** `auto` (70% espectral), `espectral` ou `branco`.
- **`humano`:** fração das versões editadas por um operador humano (seleção, ferramenta de apagar, suavização do
  Photoshop, conta-gotas, tamanho e posição no olho, retoques e o "Salvar como" do editor; ver
  [falsificadores.md](falsificadores.md), seção 4c). `salvar` força `png`, `jpeg_editor` ou `mesmas`. O perfil
  profissional liga com 85%; sem a chave, desligado.
- **`dificuldade`:** `variantes` (1 a 6) edições por tentativa, fica a de menor nota do perito em
  `prob` das edições; `detector: {"url": ..., "timeout": 30}` usa um detector externo no lugar dele.
- **`marcas`:** fração das versões que recebem marcas benignas: as de papel (carimbo, autenticação,
  anotação, marca-texto, rubrica, dobra, dedo, furos) só com captura física; as **digitais** (tarja,
  borrão ou pixelado sobre um campo sensível — CPF, código do cliente, linha digitável, código de barras,
  QR — e a marca d'água "CÓPIA"/"2ª VIA") em qualquer canal, depois da edição e antes do envio.
- **`exato`:** `true` = em cada versão, TODOS os campos das classes da receita que o leitor achou são editados (um
  por vez, de cima para baixo), com o mesmo valor novo onde o dado se repete (o nome do envelope e o do boleto) e
  uma cidade nova só para a versão (cidade, UF e CEP coerentes); `edicoes` e `distribuicao` não se aplicam. É a
  receita padrão (`gerador.receita_limpa`: nome e endereço, `campo.mesma_fonte`, sem canal, marcas, falsificador
  nem disfarce) e o modo 🎯 Exato da página.
- **`distribuicao`:** `equilibrada` (a combinação técnica × classe menos usada no lote vai primeiro),
  `cobertura` (equilibrada contando também o que o split já tem salvo: lotes sucessivos preenchem as
  lacunas que o datasheet lista) ou `pesos` (sorteio proporcional).
- **`acabamento.disfarce`:** `auto` (frequências do FSTS), `sempre` ou `nunca`. Não se aplica a IA
  nem ao Texture Jitter.

- **`ids`:** `null` usa todos os documentos dos splits.
- **Pesos:** o peso de cada técnica e de cada classe diz quanto ela aparece.
- **Opção ausente:** é sorteada a cada edição.
- **`"equilibrada"`:** a combinação técnica × classe menos usada até agora vai primeiro, o que
  cobre tudo por igual. `"pesos"` sorteia pelos pesos.
- **Sem semente:** não há semente global. Cada edição sorteia a sua e a guarda nos parâmetros, e
  os mesmos parâmetros dão o mesmo resultado (é assim que a prévia sai igual à aplicação). Os
  valores falsos continuam saindo do gerador do sistema, então não se repetem entre execuções nem
  entre splits.

## Medidas

Medido num A4 a 300 dpi (CPU, sem IA):

| Etapa | Tempo |
|---|---|
| técnica de caractere | 0,1 a 0,5 s |
| reescrever o campo | ~0,6 s |
| colagem de outro documento | ~1 a 8 s (inclui ler o doador pelo OCR na 1ª vez) |
| lote | ~4,5 s por imagem salva (2 a 4 edições) |

As técnicas de IA levam ~2 a 4 min por edição (ver README, seção IA).

## Verificação em documentos variados

`tests_ui/verifica_tecnicas.py` aplica cada técnica em cada campo onde ela serve e confere:
- se houve exceção;
- se o rótulo é coerente;
- se a edição ficou perto do campo;
- se o **OCR da página inteira lê o valor novo** e não mais o antigo;
- se o código de barras decodifica e o QR é lido.

Ele gera folhas de contato antes/depois por técnica. Rodado em dois conjuntos (2026-10-02):

| Conjunto | Aplicações | Exceções | Confirmadas pelo OCR | Códigos lidos |
|---|---|---|---|---|
| realista: Arial, 300 dpi, grão e JPEG, PDF (10 páginas) | 1.416 | 0 | 100% | 10/10 |
| variado (7 páginas, ver abaixo) | 943 | 0 | 99,7% | 14/14 |
| IA real (Qwen 2511): reescrever, 1 caractere, apagar | 6 | 0 | 6/6 | — |

O conjunto variado foi gerado por `tests_ui/gerar_variados.py`:
- fontes Calibri, Verdana em tinta azul, Times com "NOME: valor" na mesma linha, Courier em
  papel cinza e Tahoma a 150 dpi;
- uma foto de celular simulada (perspectiva, inclinação, luz desigual);
- código de barras ITF e QR PIX de verdade.

As 3 divergências restantes:
- um nome reescrito certo, cujo "I" fino o OCR da página não leu;
- dois traços duvidosos: 5→6 na foto inclinada e 9→7 na Courier.

`tests_ui/verifica_saida.py` confere a saída gravada, versão por versão:
- máscara de técnicas = região;
- pixels por técnica = soma das edições;
- classe de cada edição na máscara de classes;
- uma linha por versão e por edição nos CSV.

Num lote de 28 versões (77 edições, PNG e JPEG) não houve nenhum erro, e a auditoria de vazamento
passou.

Problemas que essa verificação achou e que foram corrigidos:
- **texto comprimido cortado no fim:** o desenho não cabia na camada quando a largura era menor
  que 100%. Afetava também a ferramenta de texto manual;
- **valor novo comprido demais:** passava por cima do rótulo ou saía da página. Agora o valor é
  escolhido pela largura e cresce para o lado que tem papel livre;
- **rótulo de várias palavras** ("DATA DA LEITURA:"): entrava na caixa do valor;
- **"m³" lido como "m3":** o expoente virava um dígito de tamanho normal;
- **"MULTA" e "R$" lido como "RS":** não eram reconhecidos como valor;
- **CNPJ "achado" dentro de uma linha digitável** que o OCR cortou;
- **QR em papel colorido:** não era detectado (agora o detector Aruco entra como reserva);
- **pedaço da letra vizinha** no recorte colado como retângulo;
- **tom diferente do papel na saída da IA:** formava um retângulo visível. Agora é igualado pela
  borda da seleção;
- **partículas erradas** nos nomes gerados ("DOS SILVA").

## Qualidade e generalização

O que a literatura mostra que mais faz um detector treinado em dado sintético funcionar em fraude
de verdade, e como está no gerador. Revisão completa com links: [pesquisa_geracao_dados.md](pesquisa_geracao_dados.md).

| O quê | Como | Evidência |
|---|---|---|
| **Canal de envio** igual no par | `rotulador/canal.py`, receita `canal: {perfil, prob}` | igualar JPEG/tamanho entre classes: +11 pontos entre geradores (Fake or JPEG?); tabelas JPEG reais: F1 0,708 → 0,853 (DocQT); redes sociais: 0,331 → 0,488 (Wu et al.) |
| **Marcas benignas** | `rotulador/marcas.py`, receita `marcas: {prob}` | autênticos 1:1 e negativos difíceis cortam falso positivo (GIFL, MVSS-Net); Find it again |
| **Disfarce** como falsificadores reais | acabamento `disfarce` | FSTS: F1 em fraude real 0,198 → 0,477 (TruFor) |
| **Texture Jitter** | técnica `trecho.jitter` | OSTF: F1 médio cruzado 34,8–45,8 → 72,0 |
| **Colagem da IA** variada | opção `colagem_ia` | só recorte exato ensina a costura (TGIF, GIFL) |
| **Banda de ignorar** | `mascaras_ignorar/` | banda "sem nota" do NIST MFC |
| **Datasheet** | `DATASHEET.md` + `datasheet.json` | Gebru et al. |

### Canal de envio

Por par, um registro `theta` (gravado na anotação como `canal`, com `canal_descricao`). Cada passo tem
uma **fase**: `antes` da edição ou `depois`. Scanner, celular e app de scanner sem impressão são
capturas de quem **parte de uma foto ou de um scan** e edita por cima (celular, Paint, Photoshop, IA):
entram antes, viram a base da versão (a original gravada é a captura; os campos do OCR são levados
pela geometria, sem OCR de novo) e a edição fica lisa sobre o papel fotografado. Impressão, tela e
transmissão são sempre depois, iguais no par. `canal.captura` na receita força `antes`/`depois`;
`auto` segue o falsificador (papel e editor de PDF: depois). Um arquivo de entrada que já é foto
(JPEG/HEIC) dispensa a captura antes em metade das versões. Antes dessa mudança tudo era aplicado
depois da edição, e o "retângulo liso sobre a foto" nunca era gerado.

1. **Impressão** (laundering, só no perfil "impressão" ou 20% do "misturado"): toner espalhado em
   volta do traço, textura do toner na tinta, grão do papel e, em metade das vezes, **retícula de
   meio-tom** nos meios-tons (laser/jato colorido); e o **desgaste** (o que o Augraphy simula): faixas de
   tinta fraca (35%), riscos verticais dos rolos (30%), sangria da tinta (40%) e fotocópia ruim com
   contraste, desfoque e poeira de toner (25%). Depois vem sempre uma captura física.
2. **Captura**, uma delas:
   - **scanner**: giro de ±1,5° (a tela aumenta, aparece o vidro), escala 0,55–1,0, desfoque σ
     0,3–1,0, o **verso impresso aparecendo** espelhado de leve (30%; o campo vem da original, porque o
     verso não muda com a edição), **poeira e fios no vidro** (40%), gama 0,85–1,15, ruído σ 0,5–2,5,
     **modo** colorido (60%), tons de cinza (30%) ou "texto"/P&B binarizado pelo driver (10%),
     JPEG 75–95 em 70%;
   - **celular**: **curvatura da página** (ondas suaves ou um vinco, em 60%), perspectiva (cantos
     ±5%) sobre a **mesa** (lisa, madeira, tecido, outra folha por baixo ou granito), escala 0,45–0,85,
     rampa de luz, vinheta, sombra em 35%, balanço de branco ±5%, **distorção de lente** com aberração
     cromática (50%), desfoque σ 0,4–1,6, tremido 3–7 px em 20%, ruído de sensor Poisson-Gauss, nitidez
     do celular, JPEG 85–95;
   - **app de scanner** (CamScanner, Adobe Scan, Notas): a foto acima, depois o recorte e a
     retificação automáticos (sobra 0,3–1,5% de perspectiva), realce em cor (contraste e saturação),
     cinza ou preto-e-branco (binarização suave) e JPEG 60–85. É como grande parte dos comprovantes
     chega; lava ou realça a edição conforme a fase;
   - **tela fotografada**: grade de subpixels RGB reamostrada (moiré), em listras (LCD) ou **PenTile**
     (OLED de celular), ponto branco **azulado** da tela, às vezes a **faixa escura do refresh** (obturador
     rolante), perspectiva leve, lente, gama, ruído, JPEG 85–95. A foto de celular e o app também podem
     ter o **reflexo** da luz no papel (25%);
   - **captura**: print de tela ou PDF exportado, sem degradação.
   Em 20% das versões segue só um **pedaço da página** (passo `recorte`: 50–100% da largura, 30–90% da
   altura, a parte de cima pesando mais), na mesma fase da captura: depois da edição a janela é
   empurrada ou alargada para conter a edição inteira; antes dela a janela é livre e os campos cortados
   saem do documento.
3. **Transmissão**, de 1 a 3 saltos em 60% do "misturado":
   - WhatsApp: lado ≤ 1600, JPEG 70–80;
   - WhatsApp Web: 1600, 88–94;
   - rede social: 2048, 71–95;
   - app que reamostra (0,7–1,3) e grava em JPEG ou WEBP;
   - WEBP 70–90;
   - e-mail e WhatsApp **como documento**: nada muda.

   Metade dos saltos corta 1–7 px da borda antes, o que tira a grade 8×8 do lugar.

Os JPEG das capturas usam, metade das vezes, **as tabelas de quantização dos documentos de
entrada do mesmo split**, ou seja, de scanners e celulares de verdade. Num quarto das vezes usam a
tabela padrão perturbada em ±15%.

Regras:
- **Mesmos campos no par.** O ruído, a luz, a sombra e a grade são gerados uma vez e aplicados
  nas duas imagens. Sem os codecs, nada difere fora da edição (teste `test_canal`). Com JPEG, a
  diferença se espalha no máximo um bloco 16×16 por passagem.
- **WEBP.** Com perda, o VP8 prevê blocos pelos vizinhos e quantiza por segmentos da imagem toda,
  então a edição espalha diferenças pequenas pela página. Não é atalho, porque as duas imagens
  passam pelo mesmo codificador. A conferência para WEBP é estatística: média < 2,5 níveis longe da
  edição.
- **Máscaras.** Passam pela mesma geometria. As binárias contam como editado a partir de 15% de
  cobertura. Classe, técnica e nº da edição viajam como uma cobertura por valor, e cada pixel fica
  com o valor de maior cobertura.

  Com 40% e vizinho mais próximo, um traço de 1–2 px reduzido pelo canal sumia do rótulo e
  continuava visível na imagem. A revisão achou o caso e o teste `test_traco_fino_continua_rotulado`
  o cobre.
- **Original por versão.** Com canal ou marcas, cada versão grava a sua original (`<doc>__vNN`), e
  ela sai junto quando a versão é excluída.

### Marcas benignas

Autenticação mecânica do banco (monoespaçada, roxa ou preta), carimbo (PAGO, RECEBIDO, CONFERE
COM O ORIGINAL, CANCELADO, BANCO/AG, retângulo ou oval, tinta falhada, girado até 25°), anotação à mão (Ink
Free, Segoe Print...), marca-texto sobre uma linha de texto, rubrica (curvas de Bézier), dobra
(vinco com sombra) e furos de arquivo.

Regras:
- iguais nas duas imagens;
- nunca a menos de ~1,5% da página da edição, exceto a dobra, que é sombra multiplicativa do papel
  inteiro (passa por cima da edição, igual nas duas imagens);
- antes do canal, e **só quando o canal tem captura física** (impressão, scanner, celular ou tela).
  Uma marca colada digitalmente e chamada de "benigna" ensinaria que colagem digital não é fraude.

### Perícia: esconder a edição como um profissional (acabamento forense)

Para o detector pegar a edição **profissional**, o gerador precisa produzir edições profissionais com
os sinais forenses casados — os mesmos que um perito usa e que um falsificador cuidadoso elimina.
`rotulador/pericia.py` casa, SEMPRE medindo o papel original em volta (nunca dentro da própria edição):

1. **iluminação / cor** — o campo editado (corretivo, retângulo, recorte) tem cor chapada; a perícia
   faz o fundo da região seguir o mesmo plano de luz do papel. Mata o "retângulo liso demais", o tell
   nº 1 dos detectores comerciais.
2. **grão / ruído** — `igualar ruído` casa só a variância, com ruído **branco**. O grão de um scan é
   **correlacionado**. A perícia sintetiza ruído com o MESMO espectro do papel (randomização de fase),
   que engana um detector de resíduo de ruído (PRNU/noiseprint).
3. **nitidez / PSF** — texto ou recorte novo mais nítido que o scan ao redor: desfoca só o suficiente
   para igualar (nunca inventa detalhe).
4. (forense) **micro-ruído na costura** — um anel fino em volta da tinta nova, para não deixar borda
   limpa demais.

Níveis (`acabamento.pericia`): `cru` (edição limpa, sem acabamento nenhum: nem grão), `padrao` (o grão,
espectral ou branco conforme `acabamento.grao`), `profissional` (+ iluminação + nitidez), `forense`
(grão espectral sempre + iluminação + nitidez + costura), `auto` (pelo nível da técnica e pelo
falsificador: o padrão). O falsificador **Photoshop** usa forense, a **IA** profissional, o **editor de
PDF** metade cru / metade profissional (diversidade: às vezes o texto nítido continua sendo o tell). O
nível fica gravado por versão e é **filtrável no catálogo**, para treinar/avaliar o detector na
dificuldade que quiser.

Três defeitos medidos em 03/10/2026 e corrigidos: a iluminação era ajustada contra um plano puxado
pelos pixels de antialias e clareava a região (um retângulo claro em volta do texto e um halo em
volta do dígito redesenhado; agora a referência é o papel da própria região na original, com a
tinta dilatada excluída, e o delta é limitado a ±6 níveis); o grão forense não entrava porque a
conversão para uint8 estourava depois do clareamento (o forense saía com MENOS grão que o padrão);
e o grão sintetizado a partir do resíduo da mediana saía azulado (agora o passa-alta é linear e
compensado: a autocorrelação do grão posto difere da do papel em menos de 0,1).

**Perito (`rotulador/perito.py`).** O contrário da perícia: mede, por edição, as pistas que sobraram
(razão do grão dentro/fora no papel, diferença da autocorrelação do grão, razão da nitidez da tinta,
desvio do tom do papel, razão da energia na grade 8×8 do JPEG) e resume numa nota de 0 (invisível) a
1 (óbvia; um retângulo chapado passa de 0,7). Entra no datasheet (mediana e p90 por técnica e nível),
nas rotas `GET /api/perito/<split>/<nome>` (e `/mapa.png`) e `POST /api/perito/sessao` (o botão 🔍
Pistas do modo simples), e na receita `dificuldade` (gera cada edição `variantes` vezes e fica com a
de menor nota; `detector.url` troca o perito por um detector externo).

### Disfarce, Texture Jitter e colagem da IA

- **Disfarce.** Depois de cada edição profissional (metade da frequência nas amadoras): desfoque σ
  0,5–1,2 (15%), aguçar (13%), curvas (12%), cor ±5% (10%). Tudo numa máscara suavizada em volta
  da pegada, e a pegada cresce para incluir cada pixel mudado.
- **Texture Jitter.** Pega 1 a 3 palavras do campo e aplica uma ou duas operações:
  - desfoque, reamostragem (0,5–0,8) ou tremido;
  - aguçar, JPEG 40–85, ou mudar 1–5% do tamanho.

  A força é proporcional à altura da letra, e a borda é suave, de 1 a 3 px. O OCR confere que o
  texto continua o mesmo.
- **Colagem da IA.** São três modos:
  - "selecao": o recorte exato;
  - "suave": borda de 1–4 px;
  - "contexto": um retângulo em volta da seleção, com folga de uma altura dela e no mínimo 16 px,
    como o AIForge-Doc. Não usa o recorte de contexto inteiro, que pegaria os campos vizinhos.

  Nos dois últimos, a região é a área colada inteira, porque cada pixel dali veio da IA. A
  `mascara` continua sendo só o que mudou; o que ficou igual dentro da região vai para
  `mascaras_ignorar`.

### Lote de verificação (02/10/2026)

17 páginas, receita de teste: todas as técnicas sem IA, disfarce automático, canal misturado em
70% das versões e marcas em 50% das que têm captura (a página usa 30% por padrão). Resultado (lote
final da revisão): 34 versões, 70 edições das 19 técnicas, 0 falhas.

| Conferência | Resultado |
|---|---|
| saída gravada (`verifica_saida.py`: máscaras, rótulos, banda de ignorar, índices) | 0 erros |
| atalhos (`verifica_atalhos.py`: contêiner, metadados, par só difere perto da edição) | 0 erros |
| auditoria de vazamento | 0 erros |
| canal em 200 sorteios aleatórios (`conferir_par`) | 0 falhas |

Problemas que esse lote achou e que foram corrigidos:
- **Lista de edições vazia.** Com um canal que muda a geometria, a lista saía vazia porque usava a
  caixa da edição de antes do canal. O teste tinha uma brecha: `all(...)` numa lista vazia é
  verdadeiro.
- **Textura da mesa por cima do papel.** Na foto de celular, ela manchava o documento; agora fica
  só fora dele.
- **Balanço de branco forte demais.** Baixou de ±8% para ±5%.


## Revisão em 5 rodadas (02/10/2026)

Cada rodada teve três passos:
1. procurar defeitos: revisões de código independentes, exploração do site no navegador,
   auditoria dos dados gerados e documentação comparada com o código;
2. corrigir cada defeito com um teste que o reproduz (`tests/test_revisao.py`). Cada teste foi
   conferido falhando no código antigo;
3. rodar tudo de novo.

| Rodada | Onde procurou | O que achou e corrigiu |
|---|---|---|
| 1 | Revisão dos módulos novos (canal, marcas) e da integração | Receita estranha dava erro 500. Caixas das marcas e lista de "recentes" ficavam em coordenadas de antes do canal. Original órfã ao excluir uma versão. Auditoria quebrava sem OCR. TIFF com um byte aleatório. Datasheet contava errado anotações antigas. **Traço fino perdia o rótulo depois do canal** (agora: rótulo por cobertura, limiar de 15%). Imagem pequena com cortes travava. Furos, tremido, fotos MPO, descrição do JPEG. Leitura do catálogo fora da trava (Windows). |
| 2 | Revisão das técnicas, da IA e da auditoria; exploração do site no navegador | Texture Jitter cortava a letra da ponta. CPF de modelo virava "mesmo cliente", e CPF na mesma linha do RG passava em branco. Filtro do emissor errado em base pequena. A mesma palavra caía em splits diferentes. Colagem da IA pontilhada e pegando campos vizinhos. No site: **tutorial vazio**; "Outro" sem saída no modo simples; **celular inutilizável**; nome do documento sumido; importar receita (seleção, JSON inválido); seletor de IA; prévia sem classes; estimativa com "só pendentes"; pesos; plurais; títulos. |
| 3 | Regressão completa; formatos de saída com canal | Todos os formatos funcionam com canal e marcas. As conferências quebravam com PDF e não tratavam o WEBP como gravação final. |
| 4 | **Auditoria dos dados gerados**; documentação comparada com o código | **Artefatos visíveis**: texto do cabeçalho copiado com contraste invertido; fantasma e serifa depois de apagar; papel rosado no disfarce de cor; retângulo colado sem igualar o tom. Espaço sumia ao apagar um caractere. **Erros de grafia** nas trocas de letra. Código de barras novo com a linha digitável velha. Banda de ignorar engolia traço fino. "CANCELADO" como marca benigna. 18 pontos da documentação. |
| 5 | Revisão final das correções da rodada 4 | Troca de letra ainda virava erro de grafia em nomes (agora o sorteio só troca dígito). A linha refeita era rotulada como outra técnica. Contagem e desfazer do código novo. O canal podia apagar a edição e deixar o par idêntico. Valores com milhar malformados (R$ 1.23,56). Cidade/UF trocava só a UF. |

Estado no fim da quinta rodada (todas as conferências com o código final):

| Conferência | Resultado |
|---|---|
| testes de unidade | 166, todos passam (21 deles são as regressões da revisão) |
| navegador | editor 61/61; modo simples, gerador (51 checks) e layout (computador e celular): 0 falhas |
| técnicas em 17 páginas (`verifica_tecnicas.py`) | 2.751 aplicações, 0 exceções, 0 fora do campo, 0 rótulo ruim, códigos 17/17 e QR 7/7, OCR confirma 99,6% |
| técnicas mudadas na rodada 4 | 1.063 aplicações, 0 exceções, OCR confirma 99,8% |
| canal em 300 sorteios aleatórios | 0 falhas |
| lote final (34 versões, 70 edições, 19 técnicas) | saída, atalhos e vazamento: 0 erros; 0 trocas de letra |

As divergências de OCR que sobram são leituras erradas do OCR da página, como "II" lido como
"TI" ou um "I" fino que some, e não da edição: o OCR do próprio campo confere.

## Vazamento e atalhos

Erros conhecidos de datasets de adulteração, e o que a ferramenta faz com cada um:

| Problema (literatura) | Aqui |
|---|---|
| **Formato/qualidade diferente entre as classes.** No CASIA v2, autênticas e adulteradas foram comprimidas com qualidades diferentes, e o classificador aprende o histórico de compressão em vez da adulteração ([Verdoliva 2020](https://arxiv.org/abs/2001.06564)). No *Find it again*, os recortes adulterados tiveram de ser recomprimidos com a tabela de quantização da original, senão ficavam "fáceis demais" ([Tornés et al., ICDAR 2023](https://l3i.univ-larochelle.fr/app/uploads/sites/12/2024/05/ICDAR_2023_Find_it_again.pdf)) | original e editada saem do mesmo array pelo mesmo codificador. `verifica_atalhos.py` confere par a par: contêiner, tabelas de quantização, subamostragem, chunks, metadados, e nenhum pixel diferente fora da região editada |
| **Metadado que muda com o tempo** | o PDF de saída gravava `/CreationDate` e `/ModDate`; como a original é gravada antes das editadas, a data separava as classes. **Corrigido**: a codificação agora é determinística (teste `test_codificacao_deterministica`) |
| **Mesmo cliente / quase-duplicatas em splits diferentes** ([Larson et al., EACL 2023](https://aclanthology.org/2023.eacl-main.195), sobre o RVL-CDIP) | `auditar_vazamento.py` lê os documentos pelo OCR. **O mesmo CPF em dois splits é erro**; o mesmo nome, nº de instalação ou endereço é aviso. Nos próprios conjuntos de teste desta ferramenta isso achou o mesmo cliente nos três splits, o que o dHash não distinguia de "mesmo modelo de conta". `gerar_amostras.py` e `gerar_variados.py` agora fazem um cliente por documento |
| **Conteúdo dos falsos repetido entre treino e teste** | os nomes/ruas/bairros/cidades falsos vinham de ~110 palavras comuns aos três splits: todo falso do teste já tinha aparecido como falso no treino. Agora o vocabulário é repartido por split. A auditoria avisa se um nome/endereço falso das técnicas se repete entre splits (pega lotes antigos) |
| **Mistura diferente de técnicas/classes entre splits** | a distribuição "equilibrada" do gerador contava o lote inteiro, e o split processado por último ficava com as sobras (num lote: 8 edições de valor no treino e 1 no teste). Agora conta dentro de cada split |
| **IA de difusão regenera a imagem toda** ([TGIF](https://arxiv.org/abs/2407.11566)) | a saída da IA é colada só na área escolhida (a seleção; com borda suave; ou um retângulo em volta), com o tom igualado pela borda; fora dela nada muda (teste com um editor falso que suja a imagem inteira) |

Medido em 17 páginas (as contas realistas e as variadas, com um cliente por documento), lotes PNG e
JPEG q90 de todas as técnicas sem IA (2026-10-02):

| Verificação | Antes das correções | Depois |
|---|---|---|
| pares original/editada: contêiner, metadados, pixels fora da região (`verifica_atalhos.py`) | 102 pares, 0 erros | 102 pares, 0 erros |
| saída gravada (`verifica_saida.py`) | 200 edições, 0 erros | 200 edições, 0 erros |
| mesmo cliente em splits diferentes | 3 CPFs repetidos nos conjuntos de exemplo; a auditoria antiga dizia "nenhum erro" | 0 (um cliente por documento) |
| edições de valor / CEP, treino × teste | valor 12% × 7%, CEP 4% × 13% | valor 14% × 13%, CEP 7% × 9% |
| palavras inventadas do teste já inventadas no treino (nome, rua, bairro, cidade) | vocabulário único de ~110 palavras (em volume, 100%) | 0% |
| auditoria de vazamento | sem erro (não via o cliente) | sem erro |

As repetições que sobram entre splits são de espaço pequeno e caem por acaso (um mês, um CEP com um
dígito trocado, um valor), ou são parte fixa do PIX ("BR.GOV.BCB.PIX").

### Para treinar e avaliar

- **Separe por cliente e, para medir generalização, por emissor.** O DocTamper tem conjuntos de
  teste de outra origem e outro layout (FCD, SCD) por isso
  ([Qu et al., CVPR 2023](https://openaccess.thecvf.com/content/CVPR2023/papers/Qu_Towards_Robust_Tampered_Text_Detection_in_Document_Image_New_Dataset_CVPR_2023_paper.pdf)).
  Contas do mesmo modelo nos três splits não são vazamento, mas medem só o caso fácil.
- **Deixe uma técnica (ou família) de fora** e teste nela: `mascaras_tecnicas` e o `catalogo.csv`
  permitem esse corte. Detectores de texto adulterado treinados num editor caem a zero em outro
  ([OSTF, Qu et al., AAAI 2025](https://arxiv.org/abs/2407.21422)).
- **Guarde um conjunto feito à mão** (editor, modo simples) só para teste. No RTM, o mesmo modelo
  fez 57,6 de IoU nas edições automáticas e 29,9 nas manuais
  ([Luo et al.](https://arxiv.org/abs/2312.06934)).
- **Meça falso positivo nas originais** e calibre o limiar: a área editada mediana aqui é ~0,2% da
  página, e o limiar 0,5 por pixel quase não acerta nada nessa proporção.
- **Teste a robustez nas duas classes:** recorte de 2%, deslocamento de 1 px, escala 0,98 e
  recompressão JPEG 75–100. Detectores que usam a grade do JPEG perdem ~70% com isso
  ([ADCD-Net, ICCV 2025](https://arxiv.org/abs/2507.16397)).

### Como uma pessoa faria

Desde 03/10/2026, por padrão 80% das versões do gerador saem com um **falsificador** (ferramenta e
hábitos fixos na versão) e um **motivo** (campos coerentes, valores combinados), em vez do sorteio
uniforme. Evidências, tabelas e o que ficou estimado: [falsificadores.md](falsificadores.md).

### O gesto: a mão no mouse, sem tela (13/10/2026)

O movimento do mouse não aparece nos pixels; a geometria que ele produz, sim. `rotulador/gesto.py` gera essa
geometria como uma pessoa faria, sem abrir editor nem mexer no cursor:

- **Movimento:** curva de mínimo esforço (velocidade em sino), caminho levemente curvo, duração pela lei de Fitts.
  Em 35% dos movimentos a mão passa do alvo e corrige. Há tremor fisiológico de 8–12 Hz. Tudo é amostrado a 125 Hz,
  como os eventos do navegador.
- **Laço:** a volta canto a canto em torno do texto, cortando os cantos. O contorno fecha perto do começo, não no
  mesmo ponto.
- **Pincel por toques:** discos espaçados a 25% do diâmetro, com pressão que sobe no começo e cai no fim da passada.
  São uma ou duas passadas, às vezes de ida e volta.
- **Carimbo por passadas:** cada passada copia a partir da sua própria origem, o Alt+clique num papel livre perto.
  No modo "alinhado" (40%) a origem anda junto com a passada anterior. Os toques têm borda macia (dureza 0,7). O que
  ficou meio coberto leva uma segunda passada, e só o resto vai para as faixas antigas.
- **Zoom de trabalho:** sorteado por versão (50–300%). Muda o tremor e a precisão em pixels da imagem e aparece na
  descrição ("com zoom de 200%").
- **Texto:** um clique que erra alguns pixels de tela, depois as setas (1 px; 10 px com Shift) até parecer alinhado.
  A anotação guarda quantos toques de seta foram.

**Gestos reais.** O editor manual grava em cada laço, pincelada e passada de carimbo os pontos, os `tempos` (ms) e o
`zoom` da tela. No começo de cada lote, o gerador junta os traços das edições manuais da saída (as anotações sem
`gerador`) em `_cache/gestos_reais.json`. Com eles, 70% das seleções com laço ou pincel usam um traço que uma pessoa
da equipe fez de verdade, esticado para a caixa do campo. A edição diz qual foi usado: `fornecedores.gesto` vale
`real` ou `modelo`.

### O que a ferramenta não gera

Coisas da literatura que ficam de fora (o canal de envio e as marcas benignas, que estavam nesta
lista, agora existem; ver "Qualidade e generalização"):
- **impressão e captura físicas de verdade:** o canal SIMULA; para o teste final, capture algumas
  contas de verdade;
- **"melhorar" de app de scanner** (realce agressivo, binarização) como negativo benigno;
- **regeneração da página inteira por IA**, que não tem máscara de pixel (a edição no PDF vetorial,
  que estava nesta lista, existe desde 03/10/2026: `campo.reescrever_pdf`);
- **outros editores de texto por difusão:** AnyText, TextDiffuser, DiffUTE (os dois modelos de IA
  daqui são da mesma família, Qwen).

## Limitações conhecidas

- **Traços:** só os pares que a fonte permite passam. Em Arial: 3→8, 1→4, 3→9, 3→6 e 5→6
  acrescentando; 4→1 raspando. Em campos sem esses dígitos, a técnica não se aplica.
- **Cópia interna de campo:** precisa de outro campo da mesma classe no documento (duas datas, dois
  valores, CPF e CNPJ).
- **Letras:** no sorteio (gerador, fraude automática), as técnicas de caractere só trocam
  DÍGITOS. Sem lista de palavras, trocar uma letra vira erro de grafia (JOAA, CARITIBA) que qualquer
  OCR ou VLM acusa sem olhar pixel. A revisão achou o caso nos dados gerados. Fraude real de nome ou
  cidade troca a palavra inteira, e isso fica com as técnicas de campo e de trecho. Letra só quando
  pedida (opção "só letras"). Em campo sem dígito, as técnicas de caractere não são oferecidas.
- **Documento refeito (`pagina.recriar`):** só re-tipografa linhas que o OCR leu; linhas sobre fundo colorido
  (faixa do cabeçalho), as que cruzam código de barras/QR e as sem fonte identificável ou sem papel em volta
  ficam como estavam (contadas em `puladas` no rótulo). O alinhamento pela direita é uma heurística (a linha
  termina no mesmo x que mais linhas da página do que começa); com fonte substituta, o texto que não cabe
  na caixa é encolhido. A conferência por OCR relê até 6 linhas e exige metade legível com o mesmo texto.
- **Validação:** o realismo foi conferido nas contas de exemplo (sintéticas e "realistas" com grão,
  desfoque e JPEG). Em documentos reais muito diferentes, vale olhar a prévia antes de gerar em
  volume.
