# Base de lugares: municípios, CEPs, logradouros, bairros e estatísticas de endereço

Valores **reais e coerentes entre si** para o gerador trocar endereço, bairro, cidade e CEP de um comprovante: a rua
existe na cidade, com o bairro e o CEP dela; o CEP cai numa faixa que a cidade usa de verdade; a mistura de tipos de
logradouro, números e complementos segue a do país (por porte de município). Tudo sai de dados públicos do IBGE
(Censo 2022 e CNEFE 2022). É uso defensivo: o dataset treina detectores de adulteração.

Gerado por `ferramentas/construir_vocabulario_lugares.py` em 03/10/2026 (dados baixados nesse dia).

## Arquivos

TSV em UTF-8, separado por tabulação, fim de linha `\n`, com cabeçalho, sem aspas. Os `.gz` são gzip comuns
(`gzip.open(..., "rt", encoding="utf-8")`). Textos em MAIÚSCULAS **com acento** (ver "Acentos", abaixo); o nome do
município fica na grafia oficial do IBGE ("São Paulo", "Alta Floresta D'Oeste").

| arquivo | linhas (sem o cabeçalho) | tamanho |
|---|---|---|
| `municipios.tsv` | 5.571 | 0,46 MB |
| `ceps_municipio.tsv.gz` | 24.211 | 0,37 MB |
| `logradouros.tsv.gz` | 342.196 (305 mil ruas distintas, 186 mil nomes distintos) | 5,07 MB |
| `bairros.tsv.gz` | 278.221 | 3,02 MB |
| `enderecos_estatisticas.json` | — | 0,12 MB |

Universo: 111,1 milhões de registros do CNEFE 2022 (90,6 milhões de domicílios particulares) em 5.570 municípios.
CEP único em 4.789 municípios (86,0%: 95% dos com até 20 mil habitantes, 6% dos de 100 a 500 mil, nenhum acima).

### `municipios.tsv` — um por município (5.571, inclusive Boa Esperança do Norte/MT, criado em 2023, sem dados)

| coluna | conteúdo |
|---|---|
| `cod_ibge` | código IBGE de 7 dígitos (os 2 primeiros são a UF) |
| `nome` | nome oficial, com acento e caixa mista (API de localidades do IBGE) |
| `uf` | sigla da UF |
| `populacao_2022` | população residente, Censo 2022 (SIDRA 4709, variável 93); 0 = sem dado |
| `ddd` | DDD do município (2 dígitos) |
| `capital` | 1 = capital da UF (27, com Brasília) |
| `porte` | `ate_20mil`, `20_a_100mil`, `100_a_500mil` ou `500mil_ou_mais` (pela população 2022) |
| `n_enderecos_cnefe` | registros do CNEFE 2022 no município (todas as espécies) |
| `n_domicilios_cnefe` | registros de domicílio particular (COD_ESPECIE = 1) |
| `cep_unico` | 1 = município de CEP único: ≥ 90% dos endereços usam CEP terminado em `000` |
| `cep_geral` | o CEP `XXXXX000` mais usado no município (o CEP único da sede); vazio se não há |
| `n_ceps` | CEPs distintos (8 dígitos) que o município usa |
| `cep_min`, `cep_max` | menor e maior CEP usados (não é uma faixa contínua: ver `ceps_municipio`) |

### `ceps_municipio.tsv.gz` — faixas reais de CEP: uma linha por (município, prefixo de 5 dígitos)

| coluna | conteúdo |
|---|---|
| `cod_ibge` | município |
| `prefixo` | 5 primeiros dígitos do CEP |
| `n_enderecos` | endereços do CNEFE do município com CEP nesse prefixo |
| `n_ceps` | CEPs distintos nesse prefixo |
| `cep_min`, `cep_max` | menor e maior CEP observados no prefixo |
| `fracao` | `n_enderecos` / endereços do município (com CEP válido e próprio) |

Só entram CEPs **do município**: um CEP é atribuído ao município onde está a maioria dos endereços que o usam
(endereço com CEP de outro município — erro de cadastro ou divisa — não conta) e tem de estar na faixa dos
Correios da UF. Prefixos com menos de 3 endereços (digitação) ficam de fora.

### `logradouros.tsv.gz` — amostra de trechos de logradouro reais

Uma linha por (município, tipo, título, nome, bairro, CEP) distinto.

| coluna | conteúdo |
|---|---|
| `cod_ibge` | município |
| `tipo` | tipo do logradouro como no CNEFE, por extenso: RUA, AVENIDA, TRAVESSA, RODOVIA, ESTRADA, LINHA, PRAÇA, EDF (endereço do DF: "SQN 211 BLOCO K")… |
| `titulo` | título separado pelo CNEFE (DOUTOR, PRESIDENTE, SÃO, PADRE…); vazio na maioria |
| `nome` | nome do logradouro (sem o tipo e sem o título): "NILO PEÇANHA", "DAS LARANJEIRAS" |
| `bairro` | bairro do trecho (ver "Bairro", abaixo); vazio em 1,5% (o CNEFE não dá bairro ao trecho) |
| `cep` | CEP de 8 dígitos, sem hífen |
| `urbano` | 1 = setor censitário urbano, 0 = rural, vazio = setor desconhecido |
| `n_enderecos` | endereços do CNEFE nesse trecho (todas as espécies) |
| `n_domicilios` | domicílios particulares nesse trecho |

O logradouro completo é `tipo + " " + titulo + " " + nome` (pulando o título vazio): "AVENIDA PRESIDENTE VARGAS".
Não há número de casa, complemento, coordenada nem nome de estabelecimento: só a rua, o bairro e o CEP.

**Amostragem.** Por município, K = min(2500, max(20, população/800)); nas capitais, K = min(8000, max(3000,
população/400)) quando maior (São Paulo e Rio ~8000, BH 5788, Brasília 7043, capitais pequenas ~3000). 1/20 de K
são as ruas grandes da cidade (os pares rua + localidade com mais endereços; entram sempre, pelo trecho de mais
domicílios: AV PAULISTA, AV AFONSO PENA, RUA DAS LARANJEIRAS); o resto é sorteado sem reposição com peso pelos
domicílios (Efraimidis–Spirakis; peso = domicílios + 0,2 × outros endereços), semente fixa por município.
Municípios com menos de K trechos entram inteiros. Só trechos com tipo e nome, com CEP próprio do município e com
nome de verdade: "SEM DENOMINAÇÃO 3", "SEM NOME", "DESCONHECIDO" (2,5% dos endereços do CNEFE, marcador do
recenseamento) ficam de fora. Na amostra: RUA 65,4%, AVENIDA 8,5%, ESTRADA 7,0%, TRAVESSA 3,4%, EDF 2,0%;
`urbano` 1 em 83%, 0 em 15%, vazio em 2%; 13,8% com título.

### `bairros.tsv.gz` — todos os bairros/localidades com pelo menos 20 endereços

14.121 com a grafia oficial do IBGE; `urbano` 1 em 101 mil, 0 em 153 mil (localidades rurais: povoados, linhas,
sítios), vazio em 24 mil. Localidade vazia, "SEM DENOMINAÇÃO" ou igual ao nome da cidade não é bairro e não entra.

| coluna | conteúdo |
|---|---|
| `cod_ibge` | município |
| `bairro` | nome (mesma grafia da coluna `bairro` de `logradouros`) |
| `urbano` | 1/0/vazio: situação da maioria dos endereços (setor urbano/rural) |
| `n_enderecos`, `n_domicilios` | como acima |
| `prefixos_cep` | até 3 prefixos de 5 dígitos mais usados no bairro, do mais comum para o menos, separados por vírgula |
| `oficial` | 1 = o nome coincide com um bairro oficial do IBGE no município (e a grafia é a oficial) |
| `bairro_ibge` | bairro oficial do IBGE onde fica a maioria dos endereços (vazio se o município não tem bairros oficiais) |

### `enderecos_estatisticas.json` — como é um endereço brasileiro

Contagens (inteiros) sobre os **domicílios particulares** do CNEFE 2022; para sortear, use as contagens como pesos.
`por_porte` usa os portes de `municipios.tsv`.

| chave | conteúdo |
|---|---|
| `n_domicilios`, `n_domicilios_por_porte` | universo das contagens |
| `especies` | registros do CNEFE por espécie (domicílio particular, coletivo, estabelecimentos…) |
| `tipos_logradouro.nacional` / `.por_porte` / `.por_uf` | frequência do tipo (RUA, AVENIDA…); nacional com ≥ 50, por porte e UF os 40 primeiros |
| `titulos.fracao_com_titulo`, `.nacional`, `.por_porte` | quanto tem título e quais (DOUTOR, SÃO…) |
| `numero.nacional` / `.por_porte[p]` | `fracao_sem_numero` (SN); `digitos` = quantos dígitos tem o número ("0" = sem número); `primeiro_digito_por_digitos[n][d]` = primeiro dígito dos números de n dígitos (para sortear 1–99999 realista) |
| `modificadores` | `DSC_MODIFICADOR` (o que vem junto do número: SN, KM — o número é o quilômetro —, A, B, FUNDOS, CASA 2, LOTE…), nacional e por porte. SMS, FNS, SUCAM, FUNASA, SUS, IBGE, COELBA, ENERGISA, CELG… dizem que o número foi pintado por agente de saúde ou pela distribuidora (casa sem numeração oficial): é real no CNEFE, mas não sai na conta |
| `complementos.fracao_com_complemento` | fração dos domicílios com algum complemento |
| `complementos.n_elementos_por_domicilio` | quantos elementos de complemento (0 a 5) |
| `complementos.elementos[NOME]` | para cada elemento (APARTAMENTO, CASA, BLOCO, LOTE, QUADRA, FUNDOS, SALA, LOJA, TÉRREO…): `n`, `tipo_valor` (vazio / numero / letra / alfanumerico / texto) e `valores_top` (até 40 valores com ≥ 50 ocorrências: "101", "A", "1") |
| `complementos.combinacoes` | combinações na ordem do CNEFE: "BLOCO+APARTAMENTO", "QUADRA+LOTE", "LOTE+CASA"… |
| `complementos.por_porte[p]` | `fracao_com_complemento`, `elementos` e `combinacoes` por porte |
| `apartamentos.digitos` | dígitos do número do apartamento |
| `apartamentos.andar`, `.unidade` | dos números de 3–4 dígitos: andar (101 → 1, 1104 → 11) e final (01, 02…) |
| `apartamentos.valores_top` | os 200 números de apartamento mais comuns |
| `cep_sufixo` | nos CEPs válidos, ponderado por domicílio: `fracao_000` (CEP de localidade), e nos demais o último dígito, a centena (6º dígito) e `fracao_terminado_em_00` |

Valores que aparecem menos de 50 vezes no país não saem. Alguns números de referência: 20% dos domicílios sem
número (36% nos municípios até 20 mil, 13% acima de 100 mil); 43% com complemento (22% até 20 mil, 65% acima de
500 mil); CEP terminado em 000 em 40% dos domicílios.

## Decisões

**Bairro.** O CNEFE grava, em cada endereço, a localidade (`DSC_LOCALIDADE`): o bairro como é chamado no lugar
(nas cidades, o bairro dos Correios; na roça, a linha, a comunidade, o sítio). O IBGE também publica bairros
**oficiais** (com acento) por setor censitário, mas só em 895 municípios (17.575 bairros; alguns são divisões de
planejamento, como "Subsetor Oeste - 7 (O-7)" ou "Setor Socioeconômico 31"), e os setores do CNEFE (código com
"P") nem sempre estão na tabela final de setores (91,9% dos endereços estão). Por isso o bairro é a **localidade
do CNEFE**, escrita na grafia oficial quando o nome coincide (sem acento e pontuação) com um bairro oficial do mesmo
município ("NOVA BRASILIA" → "NOVA BRASÍLIA"; 30% dos endereços); senão, com os acentos restaurados. O bairro
oficial onde a localidade fica vai em `bairros.tsv.gz:bairro_ibge` (ex.: "CONJUNTO MORAR MELHOR" fica no bairro
oficial "AEROCLUBE"). Quando a localidade não serve (vazia, "SEM DENOMINAÇÃO" ou o próprio nome da cidade, como
"SÃO PAULO" em 76 mil endereços de São Paulo), o trecho fica com o bairro oficial do setor dele, se houver e não
for divisão de planejamento; senão, `bairro` vazio. A situação urbana/rural (`urbano`) vem do setor censitário.

**Acentos.** O CNEFE grava sem acento ("NILO PECANHA"). A grafia com acento de cada palavra sai de três evidências:
nomes oficiais do IBGE com acento (bairros, distritos, municípios…), frequência das palavras no português do Brasil
(FrequencyWords, legendas) e os radicais do dicionário pt_BR do LibreOffice; na dúvida a palavra fica como está
(SALA, LUIZ, MARIA, PE de "RODOVIA PE 060"); siglas de 1–2 letras, grafias estrangeiras (K, W, Y) e nomes em
-SON/-TON (EDILSON) não ganham acento; só entram os acentos do português (nada de Ñ, È, Ü); "31 DE MARCO" vira
"31 DE MARÇO". Tirar os acentos (`unicodedata` NFKD) devolve exatamente o texto do CNEFE (menos nos bairros com
grafia oficial, que podem ter pontuação: "AGENOR M. DE CARVALHO", "OLHO D'ÁGUA"). É heurístico: alguns nomes
ficam sem o acento normativo (ALVARO, LUIS, TATUAPE), outros ganham um acento discutível (ARÍSTIDES, PARÁ como
nome de rua). Nas saídas, ~6 mil palavras distintas ganharam acento (SÃO, JOSÉ, SÍTIO, JOÃO, ANTÔNIO, CÓRREGO…).

**Privacidade.** São logradouros, bairros e CEPs públicos. Não há número de casa ligado a rua, coordenada, nome de
estabelecimento nem complemento por endereço: números e complementos só como estatística agregada.

## Como usar (integração com o gerador)

* **Cidade**: sortear `municipios.tsv` com peso `populacao_2022` (é a chance de um comprovante ser daquela cidade).
  Para casar com a cidade impressa no documento, compare `normalizar(nome)` (sem acento, maiúsculas) + `uf`;
  há homônimos em UFs diferentes (Bom Jesus, Santa Luzia…), então a UF é obrigatória.
* **Rua da cidade**: filtrar `logradouros` pelo `cod_ibge` e sortear **uniforme** (a amostra já foi tirada com peso
  pelos domicílios; pesar de novo por `n_domicilios` concentra nas ruas grandes). Para comprovante residencial
  típico, prefira `urbano == "1"`. O bairro e o CEP da linha andam juntos: use os três da mesma linha.
* **Rua num bairro**: filtrar por `cod_ibge` + `bairro`. Bairro sozinho: `bairros.tsv.gz` com peso `n_domicilios`
  (e `urbano == "1"`).
* **CEP da cidade**: o melhor é o CEP de uma linha de `logradouros` da cidade (é um CEP que existe). Sem rua:
  se `cep_unico == 1`, `cep_geral`; senão um prefixo de `ceps_municipio` com peso `n_enderecos` e o sufixo entre
  `cep_min` e `cep_max` do prefixo (fica na faixa real, mas o CEP exato pode não existir).
* **Conferir CEP × cidade**: o prefixo de 5 dígitos do CEP deve estar em `ceps_municipio` para o `cod_ibge` da
  cidade (de preferência com `fracao` não desprezível). Isso substitui `FAIXAS_UF`/`FAIXAS_CEP` escritas à mão.
* **Número e complemento**: sortear pelas contagens de `enderecos_estatisticas.json` do porte da cidade.

**Armadilhas.**
* `tipo` vem por extenso e como o CNEFE grava; abreviar (AV, R, TV, PÇA) é com o gerador. No DF quase tudo é
  `EDF` com o endereço inteiro no `nome` ("QNM 32 CONJUNTO A2", "SQN 211 BLOCO K"): imprima só o `nome`.
* O CNEFE separa o título do nome: SÃO + JOSÉ, SANTO + ANTÔNIO, DUQUE + DE CAXIAS, DOM + PEDRO II. O `nome`
  sozinho pode ser só "ANTÔNIO" ou "DE CAXIAS": junte sempre `titulo` e `nome`.
* Há tipos rurais (LINHA, RAMAL, ESTRADA, SÍTIO, FAZENDA, POVOADO, VICINAL) e nomes que são códigos ("LINHA 72",
  "RUA PROJETADA 2", "RUA 34", "RUA A"): são reais, mas raros em contas de cidade; filtre por `urbano` e `tipo` se
  quiser o caso típico. O `tipo` às vezes é estranho ("1A TRAVESSA DA RUA", "BAIRRO"): é o que está no CNEFE.
* Em cidades sem bairros oficiais, a localidade pode ser "SETOR 03", "ZONA RURAL", "LINHA 619" etc.
* CEPs terminados em 899 (área rural de algumas cidades) e 9xx (agências, caixas postais) aparecem porque estão
  no CNEFE; para um endereço residencial típico prefira os demais.
* `n_enderecos` conta unidades (cada apartamento é um endereço), por isso ruas com prédios pesam mais.
* Boa Esperança do Norte (MT) não tem população nem endereços (criado depois do Censo): peso 0.

## Fontes e licenças

* IBGE — API de localidades (`https://servicodados.ibge.gov.br/api/v1/localidades/municipios`); SIDRA tabela 4709
  (`https://apisidra.ibge.gov.br/values/t/4709/n6/all/v/93/p/2022`); Censo 2022, Agregados por Setores
  Censitários, tabela básica (`Agregados_por_setores_basico_BR_20260520.zip`); CNEFE 2022 por UF
  (`https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/`).
  Dados públicos do IBGE, de uso livre com citação da fonte.
* DDD e capital: `kelvins/municipios-brasileiros` (`csv/municipios.csv`), licença MIT.
* Só para decidir acentos (nada deles é redistribuído além da grafia das palavras): FrequencyWords de Hermit Dave
  (`content/2018/pt_br/pt_br_full.txt`, conteúdo CC BY-SA 4.0, a partir do OpenSubtitles) e o dicionário pt_BR
  VERO do LibreOffice (`LibreOffice/dictionaries`, LGPLv3/MPL).

## Como refazer

Na raiz do projeto (rede necessária; ~3,6 GB de download, lidos em fluxo direto do zip, sem extrair; 11 min com a
rede a ~40 MB/s):

    .venv\Scripts\python.exe ferramentas\construir_vocabulario_lugares.py --cache D:\algum\temp

* `--cache` (padrão: `%TEMP%\rotulador_lugares`): downloads pequenos (~45 MB) e o agregado de cada UF
  (`agregado_<NN>_<UF>.pkl.gz`, ~150 MB no total). O zip do CNEFE de cada UF é apagado depois de agregado
  (`--manter-zips` para guardar): o disco precisa só do maior zip (SP, 1 GB) livre de cada vez.
* Rodar de novo reaproveita o cache (não baixa nem relê o CNEFE) e regrava as saídas em ~1 min; `--refazer`
  agrega tudo de novo. A saída é determinística (semente fixa, gzip sem data): duas rodadas dão os mesmos bytes.
* `--ufs RO,DF --saida <pasta>` gera uma base parcial para teste.
* `tests/test_vocabulario_lugares.py` confere formato e sanidade (sem rede).
