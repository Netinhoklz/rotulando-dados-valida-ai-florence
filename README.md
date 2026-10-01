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
```

## Uso

1. Coloque os documentos em `dados/treino`, `dados/teste` e `dados/validacao` (subpastas
   valem). Formatos aceitos: JPG, PNG, TIFF (inclusive multipágina), BMP, WEBP, HEIC e PDF.
   Cada página vira um item; PDF é rasterizado a 200 DPI (`--dpi`).
2. `.venv\Scripts\python.exe app.py` abre o navegador em http://127.0.0.1:5000.
   Opções: `--dados`, `--saida`, `--porta`, `--dpi`, `--sem-navegador`.
3. Para cada documento:
   1. Escolha a **categoria** do que vai editar (barra amarela, `Alt+1..0`, ou clique na lista
      "O que foi editado").
   2. Edite.
   3. Repita para outras categorias.
   4. Salve com **Salvar** (`Ctrl+S`).

   O botão só libera quando há edição e toda edição tem categoria. Depois de salvar, o documento
   volta ao original e você pode criar outra variação (v02, v03…).
4. Antes de treinar, rode `.venv\Scripts\python.exe auditar_vazamento.py`.

As edições ficam em rascunho no disco (`saida/<split>/_rascunhos`). Se o servidor cair, o
trabalho volta quando você reabre o documento.

## Ferramentas

| Atalho | Ferramenta |
|---|---|
| `M` / `Shift+M` | seleção retangular / elíptica |
| `L` | laço (marque "Poligonal" para clicar ponto a ponto) |
| `W` | varinha mágica (tolerância, contígua ou por cor) |
| | Seleção: `Shift` soma, `Alt` subtrai, `Shift+Alt` intersecta. Também: expandir/contrair, suavizar, `Ctrl+A`, `Ctrl+D`, `Ctrl+Shift+I` (inverter) |
| `B` | pincel (`Alt+clique` pega a cor; `[` `]` mudam o tamanho) |
| `E` | borracha que **devolve o original** (não precisa de categoria) |
| `G` | balde de tinta |
| `I` | conta-gotas (lê o pixel no servidor) |
| `S` | carimbo de clonagem (`Alt+clique` define a origem) |
| `J` | pincel corretivo: reconstrói o traço a partir do entorno |
| `T` | texto: clique na linha de base, digite e dê `Enter`. Ajustes: fonte do sistema ou da pasta `fontes/`, tamanho, espaçamento, largura, negrito, rotação, desfoque, suavização |
| `R` | **substituir texto**: arraste sobre o texto antigo. O servidor estima posição, tamanho e cor, apaga o antigo com preenchimento inteligente e escreve o novo. 🎲 sorteia um valor plausível da categoria |
| `V` | mover/duplicar a seleção. Arrastar move; o canto escala (`Shift` mantém a proporção, `Ctrl` faz perspectiva); fora do contorno gira |
| ⧉ | colar um trecho de **outro documento do mesmo split** (splicing) |
| `Shift+F5` | preencher a seleção (preenchimento inteligente / PatchMatch) |
| ◐ | ajustes na seleção: brilho/contraste, níveis, matiz/saturação, desfoque, nitidez, ruído, recompressão JPEG local, cinza |
| ▮▯ | código de barras ITF-25. 🎲 gera um código válido de arrecadação (contas, começa com 8) ou de boleto, junto com a linha digitável |
| ▦ | QR code. 🎲 gera um PIX "copia e cola" válido (CRC conferido) |
| 🤖 | edição por IA da seleção (ver abaixo) |
| `Ctrl+Z` / `Ctrl+Y` | desfazer / refazer |
| `\` (segurar) | ver o original |
| `K` | sobrepor a máscara de pixels alterados |
| `X` / `D` | trocar as cores / cores padrão |
| Roda, `Espaço`+arrastar, `0`, `1` | zoom, mover a vista, ajustar à tela, 100% |
| `PageUp` / `PageDown` | documento anterior / próximo |

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
  originais/<doc>.png              uma vez por documento (com perda: <doc>__q90.jpg)
  editadas/<doc>__v01.png          cada variação salva
  mascaras/<doc>__v01.png          0/255: pixels cujo valor mudou (exato, antes da compressão)
  mascaras_regiao/<doc>__v01.png   0/255: área editada (pegada das ferramentas)
  mascaras_classes/<doc>__v01.png  id da categoria por pixel (0 = não editado)
  anotacoes/<doc>__v01.json        origem + hashes, categorias, caixas por categoria, operações
  manifesto.csv                    uma linha por variação
```

- `<doc>` é um hash do conteúdo. O nome original do arquivo, que pode ter o nome do cliente,
  fica só no JSON.
- O formato de saída é escolhido no topo: PNG, JPEG, WEBP, TIFF ou PDF.
- **Original e editada do mesmo par saem sempre no mesmo formato e qualidade**, sem EXIF nem
  perfil de cor. Assim o modelo não separa as classes pela compressão ou por metadados.

## Defesas contra vazamento

1. **O split vem só da pasta.** O navegador manda apenas o id do documento. O servidor resolve
   o split e só grava dentro de `saida/<split>`.
2. **Na partida**, cada página recebe dois hashes: um dos bytes do arquivo e outro dos pixels
   decodificados. O segundo pega o mesmo documento salvo em outro formato. Se um hash aparece em
   dois splits, **o servidor não sobe**. Também não sobe se um documento já rotulado (ou com
   rascunho) num split aparecer em outro, por exemplo se alguém mover o arquivo depois.
3. **Colar de outro documento** só aceita doador do mesmo split. A UI só lista esses doadores
   e o servidor recusa os outros.
4. **Valores digitados**: o 🎲 sorteia nomes, CPF/CNPJ válidos, endereços, datas, valores,
   códigos de barras etc. Assim o operador não repete o mesmo "JOÃO DA SILVA" no treino e no
   teste, que o modelo decoraria.
5. **`auditar_vazamento.py`** refaz tudo a partir dos arquivos (código de saída 1 se houver erro).
   - **Erro:** anotação fora do split, doador de outro split, arquivo de saída repetido entre
     splits, par em formatos diferentes, máscara do tamanho errado.
   - **Aviso:** textos digitados repetidos entre splits, e uma lista de pares *parecidos* entre
     splits para revisão humana.
6. **O que a ferramenta não resolve sozinha:** o **mesmo cliente** em meses diferentes, em
   splits diferentes. O modelo decoraria nome e endereço.
   - Separe por cliente ao montar as pastas.
   - A lista de "parecidos" da auditoria só ajuda até certo ponto. Foi medido que o hash
     perceptual **não** distingue "o mesmo papel recapturado" de "outro cliente, mesma conta":
     contas do mesmo modelo também aparecem lá.

## IA local (Qwen-Image-Edit-2511)

Selecione a área, clique em 🤖 e descreva a troca, por exemplo `Troque o texto "R$ 151,37" por
"R$ 987,65"…`.

- A seleção e uma margem de contexto são ampliadas para cerca de 1 MP, com lados múltiplos de 32.
  Sem isso, o texto pequeno fica ilegível para o modelo.
- O resultado volta para a resolução original e é colado **só dentro da seleção**. O VAE altera a
  imagem inteira, e o que fica fora da seleção é descartado.
- Prompt, semente e passos vão para o JSON.

**Por que esse modelo:**

- O Qwen-Image-Edit-2511 tem licença Apache-2.0.
- O "Qwen-Image-2.1" foi descartado: a licença é só de pesquisa (não comercial) e há relatos de
  falta de memória editando com 8 GB. O 2.0 não tem pesos públicos.

**Configuração para GPU de 8 GB:**

- transformer em GGUF Q4_K_M (13,2 GB), executado bloco a bloco a partir da RAM;
- LoRA Lightning de 4 passos;
- encoder Qwen2.5-VL-7B em 4 bits (6,9 GB), que só vai para a GPU para ler o pedido. Os tensores
  foram conferidos: é o mesmo encoder do Qwen-Image-Edit-2511.

**Instalação:** a IA roda numa venv própria, criada a partir do python que tem torch com CUDA (aqui
é o miniconda):

```powershell
C:\Users\netinhoklz\miniconda3\python.exe -m venv --system-site-packages .venv-ia
.venv-ia\Scripts\python.exe -m pip install -r requirements-ia.txt
.venv-ia\Scripts\python.exe ia_servidor.py --baixar     # ~21 GB em HF_HOME (padrão: E:\hf_cache)
.venv-ia\Scripts\python.exe ia_servidor.py              # deixe rodando; o rotulador detecta sozinho
```

**Requisitos de memória:** cerca de **23 GB de memória livre (commit)**, ou seja, RAM mais arquivo de
paginação. O servidor confere antes de carregar e recusa com uma mensagem clara em vez de travar
a máquina. Alternativas:

- `--quant Q3_K_M` reduz cerca de 3 GB;
- `--sem-stream` fixa menos RAM, mas fica mais lento;
- `--teste` carrega o modelo, troca um texto numa imagem sintética e mostra tempo e pico de memória.

**Ainda não testado com o modelo carregado:** na máquina de desenvolvimento havia só ~6 GB de commit
livre. Por isso a edição real (tempo e qualidade) ainda não rodou. O que já foi verificado:

- o servidor sobe e o rotulador detecta;
- a recusa por memória funciona;
- a composição só dentro da seleção, testada com um modelo falso;
- as importações e o bitsandbytes 4 bits em CUDA 11.8.

## Testes

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

São 61 testes. Eles cobrem:

- **vazamento:** mesmo conteúdo em PNG e TIFF em splits diferentes, documento rotulado que mudou
  de split, doador de outro split, auditoria;
- **operações e máscaras:** cada operação mantém intactos os pixels fora da área editada, a
  máscara bate com a diferença real e desfazer/refazer restaura bit a bit;
- **códigos:** 20 códigos de barras gerados são decodificados de volta, o QR é lido pelo OpenCV,
  os DVs de CPF, CNPJ, arrecadação e boleto conferem;
- **salvamento:** formatos de saída e rascunho que sobrevive à queda do servidor.
