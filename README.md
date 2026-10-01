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

**Três problemas de bibliotecas foram contornados no código:**

- o `peft` do miniconda quebra com o transformers 5.5, por isso a venv própria tem `peft` novo;
- o transformers 5 embrulha a torre visual em camada 4 bits apesar da lista de exclusão; o servidor
  desfaz isso;
- o leitor de GGUF do diffusers copia os 13 GB para a RAM; o servidor troca por um que mapeia.

## Testes

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

São 81 testes. Eles cobrem:

- **vazamento:** mesmo conteúdo em PNG e TIFF em splits diferentes, documento rotulado que mudou
  de split, doador de outro split, auditoria;
- **operações e máscaras:** cada operação mantém intactos os pixels fora da área editada, a
  máscara bate com a diferença real e desfazer/refazer restaura bit a bit;
- **códigos:** 20 códigos de barras gerados são decodificados de volta, o QR é lido pelo OpenCV,
  os DVs de CPF, CNPJ, arrecadação e boleto conferem;
- **salvamento:** formatos de saída e rascunho que sobrevive à queda do servidor.
- **camadas:** a de cima vence na máscara, ocultar tira dos rótulos, opacidade, excluir/mesclar/duplicar
  com desfazer, a borracha revela a camada de baixo, mover camada, rascunho com camadas;
- **ferramentas profissionais:** Poisson (identidade e cor do destino), carimbo com fonte original,
  escala e rotação, recuperação, remendo, os 7 modos de retoque, formas, amostragens do
  preenchimento, igualar ruído calibrado, seleção por tinta e por pincel.

Também há testes de interface com Selenium + Edge em `tests_ui/`: 27 checks no fluxo geral
(`ui_fluxo.py`) e 25 nas camadas e ferramentas novas (`ui_camadas.py`). Eles usam documentos
sintéticos numa pasta de trabalho separada, nunca os seus `dados/`:

```powershell
.venv\Scripts\python.exe tests_ui\gerar_amostras.py C:\tmp\rot\dados
.venv\Scripts\python.exe app.py --dados C:\tmp\rot\dados --saida C:\tmp\rot\saida_teste --porta 5077 --sem-navegador
python tests_ui\ui_camadas.py C:\tmp\rot        # em outro terminal (precisa de selenium e do Edge)
```

`tests_ui/ia_ponta_a_ponta.py` faz uma edição real por IA através do rotulador. Precisa do servidor de
IA rodando.
