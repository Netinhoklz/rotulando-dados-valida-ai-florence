# Rotulador

Editor de imagens **baseado no [Graphite](https://github.com/GraphiteEditor/Graphite)**. A interface (cores, fonte,
painéis, controles) e o comportamento das ferramentas são os dele, reescritos em Python e JavaScript para uma tarefa
só: **montar dados de treino para detectar adulteração em documentos**. São comprovantes de endereço: contas de luz,
água, gás e telefone, e boletos.

Cada documento adulterado, à mão ou em lote, sai com:

- a **original** (a classe autêntica) e a **editada**;
- as **máscaras**: dos pixels que mudaram, da área editada e do tipo de edição em cada pixel;
- um **JSON** com tudo o que foi feito: o campo, o texto antes e depois, a técnica, a ferramenta e o lugar.

Os atalhos de teclado são os do Photoshop, para quem já edita imagem não ter de aprender outros.

## Como funciona

```
dados/<split>/  ─►  leitura  ─►  edição  ─►  conferência  ─►  revisão  ─►  saida/<split>/
  treino            OCR          editor      OCR relê,        aprovar      original, editada,
  teste             campos       ou          fonte e traço,   ou           máscaras e JSON
  validacao         fonte        gerador     coerência        reprovar     + saida/_treinamento/
```

### 1. Entrada e splits

Os documentos ficam em `dados/treino`, `dados/teste` e `dados/validacao`, em JPG, PNG, TIFF, BMP, WEBP, HEIC ou PDF.
Cada página vira um item, e o PDF é rasterizado a 200 DPI.

O split de um documento é a pasta onde ele está, e nada mais. O navegador manda só o id do documento, e o servidor
decide onde gravar. Na partida, o servidor calcula dois hashes de cada página, um dos bytes e outro dos pixels. Se o
mesmo documento estiver em dois splits, ele não sobe.

### 2. Leitura do documento

Antes de qualquer edição, o rotulador entende o documento:

- **Texto** ([rotulador/ocr.py](rotulador/ocr.py)): o OCR é o [paddleocr](https://pypi.org/project/paddleocr/),
  com o detector PP-OCRv6 e o reconhecedor latino PP-OCRv5 da PaddlePaddle, na CPU. Os modelos (~18 MB) são baixados
  do ModelScope na primeira leitura, conferidos pelo sha256 e entregues ao paddleocr como pasta local. Nada vem do
  Hugging Face, nem modelo nem biblioteca. Ele lê acentos e espaços, e a leitura fica em cache.
- **Campos** ([rotulador/campos.py](rotulador/campos.py), [rotulador/gabarito.py](rotulador/gabarito.py)): o
  rotulador decide o que é cada linha. Usa o texto, os rótulos ("Vencimento", "CEP") e bases públicas do IBGE e do
  TSE. Assim acha o nome do cliente, o endereço, o bairro, o CEP, a cidade, o CPF, as datas, os valores e os códigos.
  O que é do emissor (a empresa que manda a conta) fica marcado para não mudar.
- **Fonte** ([rotulador/identificar_fonte.py](rotulador/identificar_fonte.py)): o texto lido é desenhado em todas as
  fontes instaladas e comparado com a tinta. Daí saem a fonte, o tamanho e o peso de cada campo.

### 3. Edição

Há dois caminhos, e os dois gravam a mesma coisa.

**À mão, no editor** (a página principal), como num editor de imagens comum:

- seleção por retângulo, elipse, laço, caneta (caminho com curvas) e varinha;
- mover e transformar, recortar e colar, apagar com preenchimento;
- carimbo, pincel corretivo, remendo, texto, formas e retoque;
- camadas e histórico.

Antes de editar, escolha o **tipo da edição**. É ele que vira o rótulo:

- *copiar e colar*: um pedaço do próprio documento, colado em outro lugar dele;
- *pegar de outro documento*: um pedaço de outro documento do mesmo split (há uma vista lado a lado para isso);
- *apagar e substituir*: o texto é apagado e, no lugar, entra outro ou nada.

O navegador não calcula os pixels finais. Ele descreve a operação (os pontos, a seleção, o texto), e o servidor a
aplica na resolução original. Por isso a máscara é exata. Cada documento aberto é uma sessão
([rotulador/sessao.py](rotulador/sessao.py)) com camadas, histórico e um rascunho no disco, que volta se o servidor
cair.

**Em lote, no gerador** (botão Gerador, ou `/gerador`): um clique gera N versões de cada documento. Em cada versão,
o gerador faz quatro coisas.

1. **Escolhe os campos** como uma pessoa faria. Sempre entram o nome, o endereço ou os dois, e às vezes também o
   CPF, o CEP, uma data ou um valor.
2. **Inventa os valores novos**, coerentes entre si e com o documento:
   - nomes com a frequência real do Censo;
   - ruas reais do CNEFE, com o bairro e o CEP delas;
   - datas no ciclo de uma conta;
   - código de barras com o total impresso.
3. **Edita** com uma técnica de falsificador de verdade:
   - reescrever o campo na mesma fonte;
   - montar o valor com letras recortadas do próprio documento;
   - colar de outro documento;
   - trocar só um dígito.

   Junto vêm os hábitos de quem edita: a seleção à mão, o conta-gotas, a suavização do programa
   ([rotulador/humano.py](rotulador/humano.py)).
4. **Confere** o resultado (passo 4) e salva.

Há também o método do artigo FSTS (NeurIPS 2025), em [rotulador/fsts.py](rotulador/fsts.py). Outros modos dão
controle fino das técnicas, do canal de envio (foto de celular, scanner, WhatsApp) e de marcas que não são fraude
(carimbo, anotação à mão).

### 4. Conferência

Uma edição só fica se não denunciar o gerador:

- o reconhecedor do OCR relê o campo e precisa encontrar o valor novo;
- o texto novo tem a mesma fonte, a mesma altura de letra (±4% ou ±1 px) e o mesmo traço (±10%) do antigo
  ([rotulador/fidelidade.py](rotulador/fidelidade.py)); se não tiver, o gerador tenta outra técnica;
- as regras de um verificador rodam antes e depois da edição: CPF, CEP × cidade, datas, linha digitável × total
  ([rotulador/coerencia.py](rotulador/coerencia.py)). O que a edição quebrou vai para o rótulo;
- o perito ([rotulador/perito.py](rotulador/perito.py)) mede as pistas que sobraram (grão, nitidez, tom, grade do
  JPEG) e dá a nota de dificuldade da versão.

### 5. Revisão

No gerador, a aba **Revisar** mostra cada edição antes e depois, com os selos de altura, traço e fonte. `A` aprova e
`R` reprova, com o motivo. A vista **Em grade** aprova um lote de uma vez.

O **Catálogo** mostra tudo o que foi gerado: a matriz técnica × classe, filtros por split e por revisão, e a
exportação do que estiver filtrado. Várias pessoas podem trabalhar juntas: cada documento tem trava e atribuição, e o
rotulador registra quem fez o quê.

### 6. Saída

```
saida/<split>/
  originais/ editadas/          o par
  mascaras/                     branco (255) onde o pixel mudou, preto (0) no resto
  mascaras_regiao/              branco (255) na área editada
  mascaras_classes/             o tipo da edição (ou a classe do campo) em cada pixel
  mascaras_tecnicas/            a técnica em cada pixel
  mascaras_ignorar/             a borda ambígua, que fica fora da perda e da métrica
  anotacoes/<doc>__v01.json     tudo o que foi feito
  manifesto.csv catalogo.csv categorias.csv
saida/_treinamento/<split>/
  imagens/ mascaras/            só a editada e a máscara, com o mesmo nome: o que o treino lê
saida/dataset.xlsx, saida/DATASHEET.md
```

- `<doc>` é um hash do conteúdo. O nome original do arquivo, que pode ter o nome do cliente, fica só no JSON.
- A original e a editada saem no mesmo formato e na mesma qualidade, sem metadados. Assim o modelo não separa as
  classes pela compressão.

### 7. Proteção contra vazamento

- O split vem só da pasta, e os hashes são conferidos na partida.
- Colar de outro documento só aceita doador do mesmo split.
- Os valores falsos (nomes, ruas) são repartidos entre os splits, para o modelo não decorar a lista.
- [auditar_vazamento.py](auditar_vazamento.py) refaz a conferência a partir dos arquivos. Acusa o mesmo cliente
  (CPF), o mesmo emissor ou o mesmo modelo de conta em dois splits.

### 8. Treino e avaliação

[rotulador/treino](rotulador/treino) tem três partes:

- o **carregador** do dataset: o par no mesmo lote, o recorte centrado na edição, o currículo pela dificuldade e um
  Dataset do PyTorch;
- o **protocolo de avaliação**: AUC, F1 e IoU por pixel, intervalo de confiança por bootstrap e resultados por
  técnica e por canal;
- os **conjuntos de robustez**: JPEG, WhatsApp e impressão.

## As ferramentas, do jeito do Graphite

Em [static/ferramentas.js](static/ferramentas.js), cada ferramenta é uma máquina de estados, como no Graphite
(pronta → arrastando → solta…). Cada estado declara as dicas da barra de status e as opções que mostra. Por isso a
barra de baixo sempre diz o que cada tecla e cada gesto fazem naquele momento.

O comportamento também é o dele:

- durante o arraste, `Shift` faz quadrado ou círculo, `Alt` desenha a partir do centro e `Espaço` move o desenho;
- a transformação tem alças nos lados, e o giro anda de 15 em 15° com `Shift`;
- arrastar com o zoom aproxima e afasta sem parar;
- o botão direito usa a cor de fundo;
- as medidas aparecem ao lado do cursor.

As letras das ferramentas e os atalhos são os do Photoshop.

## Mapa do código

| Onde | O quê |
|---|---|
| [app.py](app.py) | servidor Flask: a API, a partida (hashes e splits) e a janela |
| [rotulador/sessao.py](rotulador/sessao.py) | o documento aberto: camadas, histórico, rascunho e máscaras |
| [rotulador/ops/](rotulador/ops) | as operações de pixel das ferramentas, as técnicas automáticas e os valores falsos |
| `rotulador/ocr.py`, `campos.py`, `gabarito.py`, `identificar_fonte.py`, `glifos.py` | a leitura do documento |
| `rotulador/gerador.py`, `humano.py`, `falsificador.py`, `fsts.py` | a geração em lote |
| `rotulador/fidelidade.py`, `coerencia.py`, `perito.py`, `pericia.py`, `qualidade.py` | a conferência e o acabamento |
| `rotulador/canal.py`, `marcas.py` | o canal de envio e as marcas que não são fraude |
| `rotulador/exportar.py`, `catalogo.py`, `planilha.py`, `datasheet.py` | a gravação e os índices |
| [rotulador/equipe.py](rotulador/equipe.py) | as travas, as atribuições e a revisão |
| [rotulador/treino/](rotulador/treino) | o carregador, a avaliação e a robustez |
| [static/](static) | a interface: menus, ferramentas, visor, painéis e gerador |
| [ferramentas/](ferramentas) | os scripts que refazem as bases de nomes e lugares |
| [auditar_vazamento.py](auditar_vazamento.py), [auditar_qualidade.py](auditar_qualidade.py) | as auditorias do dataset |

## Instalação

Precisa do Python 3.10 a 3.13: o paddlepaddle ainda não tem pacote para o 3.14.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install --no-deps -r requirements-ocr.txt
```

O OCR (paddlepaddle, paddlex e paddleocr) entra sem dependências para não trazer as bibliotecas do Hugging Face
(`huggingface-hub` e `safetensors`). As dependências de que ele precisa estão no `requirements.txt`.

Os modelos do OCR (~18 MB) são baixados do ModelScope na primeira leitura, para `modelos/ocr` (a pasta não vai pelo
Git). Numa rede que bloqueia o ModelScope, copie essa pasta de um computador onde o OCR já leu.

**Num computador novo, se o gerador não acha o que editar** (o FSTS diz "o detector não achou o nome nem o
endereço"), rode o diagnóstico:

```powershell
.venv\Scripts\python.exe ferramentas\diagnostico.py
```

Ele mostra o OCR (o motor que lê e se os modelos estão lá), as fontes e as bases de nomes e lugares. Mostra também,
para cada documento da pasta de imagens, as linhas lidas, os campos achados e se o nome e o endereço do cliente
aparecem. O relatório vai para `diagnostico.txt` e tem os textos dos documentos: confira antes de mandar.

## Uso

```powershell
.venv\Scripts\python.exe app.py                     # abre o editor numa janela própria, com o servidor junto
.venv\Scripts\python.exe app.py --navegador         # abre no navegador
.venv\Scripts\python.exe app.py --sem-navegador     # sobe só o servidor (porta 5000, ou --porta)
```

As opções `--dados` e `--saida` apontam para outras pastas.

1. Escolha o tipo da edição (`Alt+1`, `Alt+2` ou `Alt+3`).
2. Edite.
3. `Ctrl+S` salva e abre o próximo documento.

Para gerar em lote, abra o **Gerador** e clique em **Gerar fraudes**. Antes de treinar, rode
`.venv\Scripts\python.exe auditar_vazamento.py`.

## Testes

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

Os testes de interface (Selenium + Edge) ficam em [tests_ui/](tests_ui). O [tests_ui/ui_todas.py](tests_ui/ui_todas.py)
usa cada ferramenta e cada opção como uma pessoa usaria, num documento realista.

## Documentação

- [docs/manual.md](docs/manual.md): o manual de referência, com cada tela, cada opção, a lista de atalhos, o formato
  da saída, a API e os números medidos.
- [docs/tecnicas.md](docs/tecnicas.md): as técnicas de adulteração, os algoritmos e os limites.
- [docs/falsificadores.md](docs/falsificadores.md): como o gerador imita quem falsifica.
- [docs/pesquisa_geracao_dados.md](docs/pesquisa_geracao_dados.md): a pesquisa sobre geração de dados de fraude.
- `config/vocabulario/LEIA-ME_*.md`: as bases de nomes e lugares, com as fontes e as licenças.

## Créditos e licenças

- **Graphite** (Apache-2.0): é a referência do visual e do comportamento das ferramentas. Nenhum código, ícone ou
  marca dele está aqui; os ícones de [static/icones.js](static/icones.js) foram desenhados para este projeto.
- **Source Sans Pro** (SIL OFL 1.1): a fonte da interface, em [static/fontes/](static/fontes).
- **OCR**: o paddleocr e os modelos da PaddlePaddle (Apache-2.0), com os modelos baixados do ModelScope.
- **Bases de nomes e lugares**: IBGE (Censo 2010 e 2022, CNEFE 2022) e TSE (só contagens agregadas). Os detalhes
  estão nos `LEIA-ME` de [config/vocabulario/](config/vocabulario).
