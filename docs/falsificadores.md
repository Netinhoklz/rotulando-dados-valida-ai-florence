# Como uma pessoa faria: falsificadores, motivos e hábitos no gerador

Revisão de 03/10/2026. A pergunta: o gerador produz as adulterações **do jeito que uma pessoa de
verdade as faria**, ou só sorteia técnicas e campos de modo uniforme? A literatura é clara sobre o
preço do segundo caminho: um detector treinado em dado sintético "sem ponto de vista" aprende o
gerador, não a fraude.

- No **RTM** (Pattern Recognition 2024), o mesmo detector localiza as adulterações manuais de 25
  profissionais com IoU de **29,9%**, contra **57,6%** nas automáticas: o automático é fácil demais
  porque deixa traços que gente não deixa.
- No **FSTS** (NeurIPS 2025), treinar com as operações e os parâmetros medidos em 67 pessoas, em vez
  do DocTamper, levou o F1 do TruFor em cinco conjuntos reais de **0,198 para 0,477**.
- No **OSTF** (AAAI 2025), o detector aprende a "impressão digital" do editor: treinar num e testar
  noutro derruba o F de 85 para 0.

Fontes no fim. Itens marcados **(estimado)** não foram medidos por ninguém e são escolha nossa,
ajustável na receita.

## 1. O que se mediu sobre falsificadores humanos

### Ferramentas e tipos de edição — Find it again! (ICDAR 2023)

19 voluntários, 163 recibos forjados, 455 áreas modificadas. Cada um usou a ferramenta que quis:

| Ferramenta | Documentos | | Tipo de edição | Áreas | % |
|---|---|---|---|---|---|
| Paint | 70 | | **CPI** copiar-colar dentro do próprio documento | **353** | **78%** |
| GIMP | 65 | | IMI imitação (caixa de texto) | 36 | 8% |
| Preview (Mac) | 15 | | CUT apagar | 36 | 8% |
| Paint 3D | 10 | | PIX pixel/traço à mão | 33 | 7% |
| KolourPaint | 3 | | CPO colar de outro documento | 10 | 2% |

Alvos: total/pagamento 234, produto 95, data/hora 82, empresa 26. Metade dos documentos foi
feita no Paint ou parecido; e **quem usa Paint não digita: copia um dígito de outro lugar do
mesmo documento e cola por cima**, com o retângulo de fundo junto.

### Operações e hábitos no Photoshop — FSTS (NeurIPS 2025)

67 pessoas, 16.750 adulterações, com vídeo, PSD e log de cada uma. Na **substituição** de texto:
Content-Aware Fill (preenchimento inteligente para apagar o antigo) em **61,7%** dos casos, desfoque
gaussiano em **39,7%**, ruído acrescentado em **34,9%**. Pós-processamento geral: desfoque 12–17%,
aguçar 13%, curvas 8–17%, cor 10%.

Duas observações estruturais:

- **recorrência individual**: cada pessoa repete os próprios parâmetros (a mesma σ de desfoque,
  o mesmo ruído) de uma adulteração para a outra;
- **recorrência populacional**: as mesmas escolhas aparecem em muita gente.

### Profissionais — RTM (Pattern Recognition 2024)

25 profissionais no Photoshop: colocam o texto **alinhado**, escolhem uma região de referência
**limpa** (sem outro texto), igualam cor, fonte, orientação e fundo. É por isso que o manual é mais
difícil. A pós-produção do dataset foi redimensionar (0,5–2,0) e JPEG 75–100.

### Comprovantes de residência no Brasil — dynadok (2026) e detectores comerciais

- O fraudador **parte de um documento real** e troca nome, endereço ou data. **A data é o campo
  mais alterado**: quase toda política exige emissão nos últimos 90 dias e quase nenhuma confere.
- As contas chegam como **PDF editável sem assinatura**: qualquer editor de PDF troca o texto
  mantendo fonte e logotipo. A captura de tela destrói os metadados (e muita gente manda a foto da
  tela mesmo).
- No papel fotografado, o trecho editado no computador aparece como **uma mancha retangular lisa
  demais, sem o grão do papel** em volta.
- IA generativa em **1 de cada 50** documentos falsos.
- Resistant AI e Inscribe (detectores comerciais): fontes misturadas (Arial com Times), negrito
  errático, seções desalinhadas, **totais que não batem com os itens**, **valores redondos**, kWh
  impossíveis, logotipo em resolução diferente. Também: 64 "fazendas" de modelos (33.669 templates
  à venda) e documentos gerados do zero, fora do escopo deste gerador.

### Relatórios do mercado (2025–2026)

- **Inscribe, 2026 State of Document Fraud**: 91,2% dos documentos sinalizados alteram dados financeiros;
  a fração que altera **identidade e valores ao mesmo tempo** subiu de 40,2% (2024) para **59,8%** (2025)
  — por isso uma versão pode ter dois motivos; fraude por **template** (modelo editável comprado por
  US$ 10) em 1 de cada 5 documentos sinalizados (era 1 em 14).
- **Sumsub, Identity Fraud Report 2025**: forja assistida por IA foi de 0% para **2%** dos casos
  (ChatGPT, Grok, Gemini); documentos sintéticos +195%; contas de consumo são ~5% das verificações.
- **MIDV-DM** (2025): oito tipos de manipulação de documentos de identidade definidos a partir de mais
  de **2.000 tentativas reais** de fraude.
- **Acrobat**: quando a fonte do PDF não está embutida nem instalada, o texto editado sai em **Adobe Sans
  MM / Adobe Serif MM** (fontes que só existem dentro do Acrobat) e, se o PDF é "refrito", elas ficam
  embutidas — uma pista forense conhecida. Os editores on-line (iLovePDF, Smallpdf, Sejda, PDFescape)
  oferecem só Arial, Helvetica, Times, Courier e Verdana: o texto novo sai numa fonte **parecida, não
  igual**, nítida e alinhada. É a opção `fonte_editor: substituta` do reescrever (hábito do falsificador
  "editor de PDF", metade das vezes).

### Exame forense clássico

Letras copiadas e coladas têm cor de pixel diferente das vizinhas e a borda da alteração perturba a
sequência de pixels; **imprimir e escanear de novo apaga essas diferenças de cor**. É o que o canal
de envio "impressão" simula.

## 2. As nossas técnicas diante disso

| Comportamento medido | Antes (sorteio clássico) | Agora (modo "como uma pessoa faria") |
|---|---|---|
| Quem usa Paint copia e cola dígitos do próprio documento (78%) | cada técnica com peso 1: cópia interna ≈ 14% das edições | falsificador **Paint**: cópia interna 9 de 12,4 de peso (≈ 73%), colagem com o retângulo de fundo |
| Data é o campo mais alterado; nome e endereço depois | pesos por classe fixos (valor 3, nome 2,5, endereço 2,5, data 2) | **motivos**: conta recente 35%, outro titular 25%, outro endereço 20%, valor 12%, apagar 5% (estimado a partir do que a dynadok descreve) |
| "Conta recente" = data dentro de 90 dias | data sorteada em até 540 dias | motivo *recência*: vencimento de **3 a 85 dias atrás**, mês de referência coerente (mês anterior), as duas datas do documento iguais quando as duas entram |
| Outro titular = o mesmo nome onde ele aparece; CPF às vezes esquecido | campos independentes, nomes diferentes em cada um | motivo *identidade*: o mesmo nome nos dois lugares; CPF trocado em 60% |
| Valores redondos | nunca | motivo *valor*: 30% dos falsificadores arredondam (R$ 187,00) |
| O total muda, o código de barras fica velho | código regenerado só se a técnica for sorteada | o motivo *valor* **não toca** no código: a inconsistência que detectores reais exploram |
| Cada pessoa repete os próprios parâmetros | fonte, retângulo, colagem e retoque sorteados **a cada edição** | **hábitos por versão**: a mesma fonte do sistema, o mesmo retângulo (branco ou cor do papel), a mesma colagem, o mesmo desfoque/aguçar com os mesmos parâmetros |
| Editor de PDF: texto novo nítido sobre página escaneada | reescrita sempre com o desfoque do scan igualado | falsificador **editor de PDF**: `desfoque 0`, sem grão, sem disfarce |
| Photoshop: preenchimento inteligente 62%, desfoque 40%, ruído 35% | disfarce 15% de desfoque em qualquer nível | falsificador **Photoshop**: apagar com PatchMatch, grão igualado sempre, desfoque em 40% das pessoas (fixo na versão) |
| Celular: app simples, foto da tela, WhatsApp | canal sorteado igual para todos | falsificador **celular**: retângulo + fonte do sistema + letra colada com fundo; canal WhatsApp 45%, tela 30%, celular 15% |
| Desleixo varia de pessoa para pessoa | deslocamento fixo de ±15% da altura | **capricho** por pessoa: 0,3 (cuidadoso) a 1,6 (desleixado) multiplica deslocamento e erro de tamanho |
| IA em 1 de 50 | só se a receita marcar técnicas de IA | falsificador **IA**: 3% (só com o servidor ligado) |

O que **não** mudou e continua valendo: o split de cada documento, os doadores só do mesmo split, o
vocabulário dos valores falsos repartido por split, as máscaras exatas, o canal igual no par.

## 3. O modelo

Por versão (`rotulador/falsificador.py`), com probabilidade `prob` (padrão 80%; o resto sai do
sorteio clássico, que mantém a cobertura uniforme):

1. **Falsificador** sorteado pelos pesos **(estimados)**:

   | | Peso | Técnicas (peso relativo) | Hábitos fixos na versão | Acabamento | Caminho depois |
   |---|---|---|---|---|---|
   | 📱 celular, app de edição | 0,30 | amador 4, corretivo 3, letra copiada 3, campo copiado 1, traços 1, apagar letra 1 | fonte do sistema (Roboto se instalada, Segoe UI, Arial, Calibri), retângulo, colagem com fundo, capricho 0,9–1,6 | sem grão, sem disfarce | WhatsApp 45%, tela 30%, celular 15%, nenhum 10% |
   | 🖌️ Paint ou editor simples | 0,15 | letra copiada 5, dígitos invertidos 2, campo copiado 2, amador 1, corretivo 0,6, apagar letra 0,6, traços 0,6/0,4, colar de outro doc 0,2 | fonte, colagem (retângulo ou tinta), capricho 0,7–1,3 | sem grão, sem disfarce | nenhum 40%, scanner 20%, impressão 20%, tela 20% |
   | 📝 editor de PDF ou de texto | 0,30 | reescrever 5, trecho 3, caractere redesenhado 1, acrescentar 1, apagar campo 0,5 | alinhamento, **texto nítido**, capricho 0,3–0,7 | sem grão, sem disfarce | nenhum 50%, tela 25%, WhatsApp 15%, impressão 10% |
   | 🎯 Photoshop/GIMP com cuidado | 0,22 | reescrever 3, letra copiada 3, apagar campo 2, campo copiado 2, redesenhar 2, trecho 2, colar de outro doc 1, inverter 1, inserir 1, apagar letra 1, letra de outro doc 0,5, jitter 0,5, código 0,5 | colagem (tinta ou Poisson), alinhamento, **disfarce fixo** (desfoque 40%, aguçar 13%, curvas 12%, cor 10%, com parâmetros sorteados uma vez), capricho 0,3–0,8 | grão igualado sempre | misturado |
   | 🤖 IA generativa | 0,03 | IA reescreve 3, apaga 1, troca letra 1 | — | grão automático | misturado 70%, nenhum 30% |

   Quem não usa nenhuma técnica permitida pela receita fica de fora do sorteio; com a IA desligada,
   o falsificador IA também.

2. **Motivo** sorteado pelos pesos **(estimados a partir das fontes)**: deixar a conta recente 35%
   (data 100%, mês de referência 50%), pôr outro titular 25% (nome 100%, CPF 60%), provar outro
   endereço 20% (endereço 100%, CEP 50%, bairro 40%, cidade 30%), mudar o valor 12% (valor 100%,
   consumo 30%), sumir com uma informação 5% (só o jeito "apagar"), sem motivo 3%. Campos repetidos
   (duas datas, o titular em dois lugares) entram com metade da probabilidade: quem frauda esquece o
   segundo lugar. Se o motivo não cabe no documento ou na receita, tenta-se outro; se nenhum cabe, a
   versão sai do sorteio clássico (ainda com as técnicas da ferramenta).

3. **Valores combinados**: a mesma data recente nas datas do documento, o mês de referência anterior
   ao vencimento, o mesmo nome em todo lugar, valor às vezes redondo. Só as técnicas que escrevem o
   campo inteiro (reescrever, amador, IA) recebem o valor pedido; as de trecho e de caractere
   escolhem sozinhas o que mudar (a data continua possível, mas pode não ficar "recente").

4. **O que a receita permite continua mandando**: os tipos de edição do modo fácil (ou técnicas ×
   classes do avançado) definem o conjunto possível; o falsificador escolhe dentro dele. Nenhuma edição
   aleatória fora do motivo é acrescentada: o motivo decide quantos campos mudar (até o máximo da
   receita).

Tudo fica gravado: `falsificador` (nome, motivo, hábitos, canal) na anotação de cada versão, nas
colunas `falsificador` e `motivo` de `manifesto.csv` e `catalogo.csv`, nos filtros do catálogo e no
datasheet. Dá para treinar sem um falsificador e testar nele (como se faz com técnica e canal).

## 4. Inventário: o que uma pessoa usa de verdade, e onde cada coisa está

Pensando como quem vai cometer a fraude, da ferramenta mais à mão para a mais sofisticada. A coluna
"traço" é o que a ferramenta deixa na imagem; "aqui" é como o gerador reproduz.

| Ferramenta do mundo real | O que a pessoa faz | Traço que deixa | Aqui |
|---|---|---|---|
| **Dedo, adesivo, post-it, fita** na hora da foto | esconde o campo sem editar nada | objeto com sombra sobre o papel; nos autênticos, dedos só na borda | `campo.ocultar` (4 objetos); marca benigna "dedo na borda" |
| **Caneta esferográfica** no papel | rabisca o campo; completa um 3 para virar 8 | tinta azul/preta diferente da impressão, pressão irregular | `campo.rabiscar` (caneta); traços com `tinta: caneta` |
| **Corretivo líquido** + caneta ou impressora | cobre e reescreve | mancha quase branca com textura e relevo, borda com sombra | `campo.corretivo` com `forma: liquido` |
| **Tesoura e cola** (recorte de outro papel), depois cópia/foto | cola um trecho por cima | borda do recorte, sombra em dois lados, papel de outro tom | colagem `papel` (letra, campo, de outro documento) |
| **Rabisco/pincel do WhatsApp, Markup, Fotos** | risca ou pinta por cima no celular | cor chapada, traço redondo, sem textura | `campo.rabiscar` (pincel_app, marcador); `campo.corretivo` com `forma: pincelada` |
| **Borrar/pixelizar** dos apps de foto | "redige" o campo | desfoque forte ou mosaico com borda reta | `campo.borrar` |
| **Texto do Markup/WhatsApp/app de edição** | digita por cima do campo | fonte do sistema, deslocamento, cor preta pura | `campo.amador` (fonte e capricho do falsificador) |
| **Paint, Paint 3D, Preview, KolourPaint** | seleciona um dígito e cola em outro lugar (78% no Find it again!) | retângulo com o fundo, tom levemente diferente | `caractere.copiar_glifo` e `campo.copiar_interno` com `colagem: retangulo` |
| **Editor de PDF** (Acrobat, Foxit, Smallpdf, iLovePDF, PDFescape, Sejda) | troca o texto no arquivo | fonte igual ou **substituída** (Adobe Sans MM, Arial, Helvetica, Times, Courier), texto nítido sobre o resto escaneado, alinhamento perfeito | `campo.reescrever` com `fonte_editor: imitada/substituta` e `nitido`; `trecho.trocar`; alinhamento fixo |
| **Word/LibreOffice/Canva com a imagem de fundo** | caixa de texto branca por cima | retângulo branco puro, fonte Calibri/Arial | `campo.amador` com `fundo: branco`, fonte Calibri |
| **Photoshop/GIMP/Photopea** | Content-Aware Fill, carimbo, texto com a fonte certa, casa luz/grão/nitidez | quase nada: iluminação, grão espectral e nitidez casados | `campo.remover`, `campo.reescrever`, colagem `tinta`/`poisson`, **perícia forense** (`rotulador/pericia.py`) |
| **Borracha mágica** (Google Fotos, Samsung, Snapseed) e **preenchimento generativo** | apaga ou reescreve com IA no celular | sem costura, textura plausível, às vezes borrada | `ia.remover`/`ia.reescrever` (Qwen); outros modelos ficam como recomendação |
| **ChatGPT/Gemini/Canva Magic Edit** com a imagem | "troque o nome por..." | regeneração de um retângulo inteiro, texto às vezes errado | `ia.reescrever` com `colagem_ia: contexto` |
| **Gerador de código de barras / PIX** | cola um código novo | código válido, linha digitável às vezes não bate | `codigo.regenerar` (com linha coerente) e `codigo.qr`; o motivo *valor* deixa o código velho de propósito |
| **Impressora + scanner/câmera** depois de qualquer edição | "lava" os traços digitais | perde a diferença de cor dos pixels colados; ganha grão, desfoque, moiré | canal `impressao`, `scanner`, `celular`, `tela` |
| **Captura de tela** do PDF/app | destrói metadados, muda a geometria | reamostragem, JPEG/PNG da tela, bordas da janela | canal `tela` e passos de transmissão |
| **WhatsApp, e-mail, rede social** | manda o arquivo | recompressão (tabelas conhecidas), redução a 1600 px | canal `whatsapp` e transmissões |

### O que a pessoa faz e **não deixa traço de pixel** (fora deste gerador)

- **Mandar a conta velha sem mexer**: a fraude de recência mais simples. Não há adulteração; só a
  leitura da data detecta. O dataset ajuda indiretamente (o OCR dos campos), mas o detector de pixel não.
- **Trocar a data do aparelho antes da captura de tela** do app da concessionária: a imagem é autêntica.
- **Recortar a foto** para deixar a data ou o nome de fora: documento incompleto, não adulterado. Vale
  um rótulo de "campo obrigatório ausente" no leitor de campos, não uma máscara.
- **Documento inteiro feito do zero** (template comprado, Word com o logotipo, IA gerando a página):
  outro problema (verificação de layout/emissor/metadados).
- **Editar o PDF vetorial e mandar o PDF**: a imagem rasterizada fica perfeita; a detecção é por
  estrutura e fontes do arquivo PDF, não por pixel.

## 4b. As peças de cada ferramenta (03/10/2026, tarde)

Além das técnicas e dos pesos, cada falsificador fixa na versão **com que peças** trabalha, e o gerador
grava cada escolha em `edicoes[].fornecedores`:

| | captura | apagador | rasterizador do texto | vestígio (apagado malfeito) | grão | refaz o código com o total novo |
|---|---|---|---|---|---|---|
| 📱 celular | **antes** da edição (foto; edita por cima) | telea (borracha do app), chapado, borrão | FreeType (Skia) | 35% | — | — |
| 🖌️ Paint | **antes** (scan ou foto) | chapado (balde), carimbo, telea | GDI, ClearType, aliasado | 30% | — | — |
| 📄 papel | depois (fotografa o papel alterado) | — | — | — | — | — |
| 📝 editor de PDF | depois (o documento é digital) | PatchMatch, chapado; ou a **camada de texto do PDF** (`campo.reescrever_pdf`, fonte embutida ou a de reserva) | FreeType | 0 | — | 30% |
| 🎯 Photoshop | **antes** (abre a foto/scan no programa) | PatchMatch, carimbo, Navier-Stokes | FreeType | 5% | espectral 2/3, branco 1/3 | 50% |
| 🤖 IA | antes | — | — | — | espectral | — |

O editor de PDF (peso 1,5) e o Photoshop (0,5) também **refazem a conta inteira** (`pagina.recriar`: todas as
linhas re-tipografadas, fonte imitada linha a linha ou uma de escritório para a página toda, logotipo e
códigos mantidos) e trocam os campos por cima, com o mesmo rasterizador: a fraude "fabricada".

A captura **antes** é a correção mais importante desta rodada: todo canal era aplicado depois da
edição, nas duas imagens, então o retângulo do celular ganhava perspectiva, grão de sensor e desfoque
junto com o papel, como se fosse um post-it. Agora a foto (ou o scan) vira a base da versão e a edição
fica lisa sobre ela, como a dynadok descreve. Os campos lidos pelo OCR são levados pela mesma
geometria (sem OCR de novo), e as marcas benignas, quando entram, vão no papel antes da foto, longe
de todo campo editável.

Também por versão: a **data de envio** (de 5 a 400 dias depois da data mais recente impressa no
documento) ancora as datas falsas da recência, o **CEP** sai na faixa da cidade (a do documento, ou a
nova quando o motivo troca a cidade), o **consumo** acompanha a proporção do valor novo, e o
**desleixo** (`capricho` > 1) escorrega a linha de base e o espaçamento do texto reescrito e estica na
horizontal a letra colada como retângulo.

## 4c. O operador humano no perfil profissional (11/10/2026)

O perfil profissional (a tela de um clique) saía **perfeito demais**: só a tinta apagada, posição em 1/4 de pixel,
tamanho medido, cor calibrada pelo p10 da tinta, desfoque igual ao do scan, grão com o espectro do papel e a edição
requantizada com as tabelas da origem. Nenhuma pessoa trabalha assim, e um detector treinado só nisso aprende o
gerador. Desde 11/10/2026, em **85%** das versões (`gerador.HUMANO_PROB`; receita `humano: {prob, salvar}`) a edição
é feita por um **operador humano** (`rotulador/humano.py`), com hábitos fixos na versão e as frequências da tabela
"Replacement" do FSTS (67 pessoas):

| Passo | O que a pessoa faz | Frequência | Aqui |
|---|---|---|---|
| seleção | letreiro, laço, pincel ou intervalo de cores | estimado 55/15/15/15% | `humano.selecionar`: cresce até a folga dela e para antes da tinta vizinha; a máscara deixa de ter sempre a forma das letras |
| apagar | Content-Aware Fill, cor sólida, fundo clonado, carimbo, pincel de recuperação | 61,7 / 9,6 / 9,5 / 10,4 / 8,8% | `patchmatch`, `chapado`, `carimbo`, `recuperar` (novo: textura de um retalho vizinho no tom do entorno) |
| suavização do texto | Nítida, Precisa, Forte, Suave, Nenhuma | uma delas em 100% | rasterizadores `ps_nitida`, `ps_precisa`, `ps_forte`, `ps_suave`, `aliasado` |
| cor | conta-gotas no texto original / cor escura "segura" | 88,4 / 11,6% | um ponto (ou 3x3, 5x5) no miolo do traço; a segura só em tinta preta, perto do tom dela |
| tamanho e posição | ajusta no olho | ±5% de escala em 43,5% | escala ±1,5% × capricho, camada no pixel inteiro, ±1 px na linha de base, ±2 px no começo |
| nitidez | desfoca até parecer o scan, deixa nítido, ou Desfoque Gaussiano na seleção | 11,9% gaussiano | `casada` 50%, `nitida` 30%, `manual` 20% (raio = desfoque do documento × o excesso da pessoa, 0,9–1,4, até 1 px) |
| retoques | aguçar, Adicionar Ruído, Desfoque de Superfície | 12,7 / 10,5 / 7,6% | fixos na versão; desfoque de movimento, brilho externo, traçado e sombra projetada ficam de fora (efeitos de cartaz, visíveis à toa num campo de conta) |
| salvar | JPEG do editor, PNG, mesmas tabelas da origem | estimado 55/25/20% (origem JPEG) | Photoshop (presets do Pillow: "Salvar como" e "Salvar para Web"), GIMP, Paint, app, Photopea |

A perícia automática (grão espectral, luz e nitidez casadas) **não** entra nessas versões: ninguém faz isso no
editor. O "Salvar como" JPEG é um passo `salvar_editor` do canal, aplicado **ao par** (a original é aberta e salva do
mesmo jeito, negativo benigno): a pista que sobra é local — a área editada comprimida uma vez, o resto duas, o sinal
que CAT-Net, DTD e ADCD-Net usam — e não "a imagem foi regravada". Conferido: nenhum pixel do par difere a mais de
20 px das edições.

Na tela de um clique o operador humano entra pelos **estilos prontos** (`gerador.ESTILOS`): ⭐ Como gente de verdade
(85%, o padrão), 🎯 Perfeccionista (0%), 🧑 Só como pessoas (100%) e 📤 Como chega no atendimento (85% + envio do
arquivo pelo canal `envio`: WhatsApp, e-mail, rede social, sem foto nem recorte). No modo avançado, a faixa "Edição
como pessoa (editor)" dá a fração livre.

Rótulo: `gerador.humano` na anotação da versão (os hábitos e a descrição), `edicoes[].humano` (descrição),
`edicoes[].fornecedores` com `selecao`, `texto` (o rasterizador), `cor`, `nitidez`, `grao` (`nenhum` ou
`ruido_do_editor`) e `salvar`; a versão com o JPEG do editor tem `canal.perfil = "editor"` e original própria. As
outras 15% seguem com o acabamento meticuloso de antes (a ponta difícil, que um perfeccionista alcança).

Medido no lote de conferência (4 documentos, 12 versões, 35 edições, 0 falhas): tom da tinta nova a ±9 níveis da
antiga (a cor segura escurece ~20, de propósito); a primeira versão do conta-gotas, com a média 5x5 pegando papel,
clareava o texto em até +88 e foi corrigida. Folhas de contato com cada seleção × ferramenta, na conta NIO real e nas
sintéticas, olhadas uma a uma.

## 5. O que ainda não é humano

- **As proporções entre falsificadores e entre motivos são estimativas.** Ninguém mediu quantos
  fraudadores de comprovante usam o celular, o Paint ou o PDF. Se você tiver uma amostra de fraudes
  reais classificada, ajuste `falsificadores.pesos` e `falsificadores.motivos` na receita.
- **Edição vetorial do PDF**: existe (`campo.reescrever_pdf`), mas só quando o documento de entrada é
  um PDF com camada de texto. Em imagem, o falsificador "editor de PDF" continua sendo a aproximação
  (texto nítido sobre o resto).
- **Documentos gerados do zero** (templates à venda, IA gerando a página inteira): fora do escopo;
  não há máscara de pixel para rotular.
- **Rastros de tentativa e erro** (desfazer, salvar várias vezes, abrir em dois programas): o vestígio,
  a recompressão JPEG local e o canal cobrem parte disso.
- **Fonte do celular**: o Roboto (Android) não vem com o Windows; o falsificador celular usa Segoe UI,
  Arial ou Calibri. Pondo `Roboto-Regular.ttf` em `fontes/`, ele passa a usar o Roboto. O mesmo vale
  para Open Sans, Liberation, DejaVu e as fontes de Mac: a pasta `fontes/` vence as do Windows.
- **Preenchimento generativo de outros fabricantes**: só a família Qwen está aqui (os apagadores por
  difusão do OpenCV, Telea e Navier-Stokes, cobrem a "borracha" dos apps simples). O OSTF mostra que o
  detector decora o editor; vale somar um modelo de inpainting leve (LaMa, que roda em CPU) e um
  segundo modelo de difusão quando houver disco.
- **Objetos de oclusão**: quatro formas sintéticas (dedo, adesivo, post-it, fita). Fotos reais de dedos
  e adesivos, recortadas, dariam mais variedade de iluminação e textura.
- **Coerência semântica profunda** (o consumo bater com o valor, a soma dos itens bater com o total):
  o motivo *valor* muda o total e, às vezes, o consumo; a inconsistência resultante é realista, mas
  o oposto (um fraudador meticuloso que acerta tudo) só aparece com o Photoshop e o código refeito.

## Fontes

- Martínez Tornés et al., *Receipt Dataset for Document Forgery Detection* ("Find it again!"),
  ICDAR 2023 — https://hal.science/hal-04295385 (Tabela 2: tipos e entidades; seção 3.2: ferramentas).
- Yu et al., *Toward Real-world Text Image Forgery Localization: Structured and Interpretable Data
  Synthesis* (FSTS), NeurIPS 2025 D&B — https://arxiv.org/abs/2511.12658 (Figura 2 e apêndice B).
- Luo et al., *Toward Real Text Manipulation Detection: New Dataset and New Solution* (RTM), Pattern
  Recognition 2024 — https://arxiv.org/abs/2312.06934.
- Qu et al., *Towards Robust Tampered Text Detection in Document Image* (DocTamper), CVPR 2023.
- OSTF, *Open-Set Text Forgery*, AAAI 2025 — https://arxiv.org/abs/2407.21422.
- dynadok, *Fraudes em comprovantes de residência: por que são tão comuns?* (2026) —
  https://blog.dynadok.com/antifraude/fraudes-em-comprovantes-de-residencia-por-que-sao-tao-comuns/ ;
  *Como identificar um documento falsificado* (mesmo blog).
- Resistant AI, *How to spot fake utility bills* — https://resistant.ai/blog/how-to-spot-fake-utility-bills.
- Inscribe, *Fake utility bills: AI detection for proof-of-address fraud* —
  https://www.inscribe.ai/document-processing/fake-utility-bill.
- Sumsub, *Proof of Address guide (2026)* — https://sumsub.com/blog/proof-of-address-guide/.
- Artaud et al., *Find it! Fraud Detection Contest Report*, ICPR 2018 (cenários: reembolso, seguro,
  garantia com "data velha demais").
- Inscribe, *2026 State of Document Fraud Report* — https://www.inscribe.ai/reports/2026-document-fraud-report
  e *Document fraud statistics* — https://www.inscribe.ai/blog/document-fraud-statistics.
- Sumsub, *Identity Fraud Report 2025* — https://sumsub.com/newsroom/sumsubs-annual-report-fraud-shifts-to-complex-multi-step-schemes-in-2025-agentic-ai-scams-poised-to-surge-in-2026/.
- MIDV-DM, *A Document-Oriented Dataset for Image Manipulation Detection and Localization* (2025).
- Adobe, *PDF fonts* e fóruns da comunidade sobre Adobe Sans MM — https://helpx.adobe.com/acrobat/current/pdf-fonts.html.
- Snappt, *Document Fraud Exposed: Tools and Tactics Used by Scammers* — https://snappt.com/blog/document-fraud-tactics/.
- *Forensic examination of computer-manipulated documents using image processing techniques*,
  Egyptian Journal of Forensic Sciences, 2015 — https://www.sciencedirect.com/science/article/pii/S2090536X15000234.
