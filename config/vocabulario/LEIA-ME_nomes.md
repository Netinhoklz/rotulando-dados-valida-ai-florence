# Base de nomes de pessoas: prenomes, sobrenomes, estrutura do nome e acentos

Valores **reais e na proporção do país** para o gerador trocar o nome de uma pessoa num comprovante: o prenome com a
frequência do Censo (MARIA, mas também ALYNE e HALINE na medida certa), o sobrenome com a partícula que ele costuma
ter (DOS SANTOS, DA SILVA, PEREIRA sem nada), a forma do nome completo (prenome composto, MARIA DE FÁTIMA, FILHO/JÚNIOR,
DE JESUS, COSTA E SILVA) e o acento como ele aparece de verdade (JOSÉ escrito com acento em ~48% dos registros). É uso
defensivo: o dataset treina detectores de adulteração.

Gerado por `ferramentas/construir_vocabulario_nomes.py` em 03/10/2026 (dados baixados nesse dia).

## Arquivos

TSV em UTF-8, separado por tabulação, fim de linha `\n`, com cabeçalho, sem aspas, gzip
(`gzip.open(..., "rt", encoding="utf-8")`). Textos em MAIÚSCULAS. Frações com 4 casas.

| arquivo | linhas (sem o cabeçalho) | tamanho |
|---|---|---|
| `prenomes.tsv.gz` | 100.787 (55.205 mais femininos, 45.537 mais masculinos, 45 empatados) | 0,89 MB |
| `sobrenomes.tsv.gz` | 20.533 (185 compostos ou com apóstrofo) | 0,17 MB |
| `acentos.tsv.gz` | 3.636 (3.292 do TSE + 344 de endereço, à mão) | 0,04 MB |
| `nomes_estrutura.json` | — | 0,16 MB |

Total 1,25 MB (o teto era ~6 MB: nada foi cortado além do que a privacidade manda).

### `prenomes.tsv.gz` — um por prenome do Censo 2010, do mais comum para o mais raro

| coluna | conteúdo |
|---|---|
| `nome` | prenome como o IBGE grava: ASCII, maiúsculas, sem acento (JOSE). É a chave |
| `acentuado` | a grafia com acento mais comum (JOSÉ, JOÃO, ANTÔNIO, MÁRCIA); igual a `nome` se não leva acento ou não há evidência |
| `frac_acento` | fração das ocorrências no TSE escritas exatamente como `acentuado` (JOSÉ 0,4770; JOÃO 0,6826; ANTÔNIO 0,1488); 0 se `acentuado` = `nome`; nas linhas `regra`, a taxa média do final da palavra |
| `n_tse` | quantas vezes a palavra aparece (em qualquer posição) nos nomes do TSE |
| `origem_acento` | `tse` = o TSE decidiu (com acento, ou sem acento e vista 3+ vezes); `regra` = o final da palavra decidiu (ver "Acentos"); `-` = sem evidência, fica como no Censo |
| `freq_f`, `freq_m` | pessoas com esse primeiro nome no Censo 2010, por sexo. Somam 181.186.624 (51,07% mulheres). O IBGE só divulga prenome com 20+ pessoas: todos entram |

Ordem: `freq_f + freq_m` decrescente, depois `nome`.

### `sobrenomes.tsv.gz` — um por sobrenome visto 5+ vezes no TSE, do mais comum para o mais raro

| coluna | conteúdo |
|---|---|
| `sobrenome` | grafia canônica com acento (GONÇALVES, ARAÚJO, CONCEIÇÃO; SOUZA); compostos com espaço (VILAS BOAS, ESPÍRITO SANTO, SÃO PEDRO, DAL MOLIN, DI DOMENICO) ou apóstrofo (D'ÁVILA, SANT'ANA, DALL'AGNOL) |
| `ascii` | o mesmo sem acento (a chave) |
| `n` | ocorrências como sobrenome nos nomes do TSE (pessoas únicas; um nome com SILVA duas vezes conta 2) |
| `frac_acento` | fração das ocorrências escritas como `sobrenome` (GONÇALVES 0,8753; ARAÚJO 0,3084); 0 sem acento |
| `p_nenhuma`, `p_da`, `p_de`, `p_do`, `p_dos`, `p_das`, `p_e` | distribuição da partícula logo antes do sobrenome (somam 1). SANTOS: DOS 0,72; SILVA: DA 0,76, E 0,01; PEREIRA: nenhuma 1,00 |

Os 20.533 cobrem 95,7% das ocorrências de sobrenome do TSE (116.592 distintos; a cauda com menos de 5 não sai daqui).

### `acentos.tsv.gz` — palavra sem acento → grafia com acento

| coluna | conteúdo |
|---|---|
| `palavra` | ASCII, maiúsculas, só letras (a chave) |
| `acentuado` | a grafia com acento |
| `frac` | do TSE: fração das ocorrências escritas assim; `manual`: a mesma fração quando o TSE concorda, senão 1 |
| `n` | ocorrências no TSE; 0 = palavra só de endereço, sem apoio do TSE |
| `origem` | `tse` (palavras de nomes de pessoa) ou `manual` (palavras comuns em endereço: PRAÇA, CÓRREGO, CHÁCARA, RIBEIRÃO, SÃO, PARANÁ, GOIÂNIA, IPÊ, ACÁCIAS, INDEPENDÊNCIA, GETÚLIO, PEÇANHA…; a lista está no script) |

Ordem: `tse` por `n` decrescente, depois `manual` por `n`.

### `nomes_estrutura.json` — a forma do nome completo

Cada chave está descrita dentro do próprio arquivo, em `_chaves`. Resumo:

| chave | conteúdo |
|---|---|
| `fontes` | fontes, pessoas do TSE (1.925.842), sobrenomes distintos, gravados e cobertura |
| `parametros` | os limiares usados |
| `pessoas` | pessoas por gênero (F 658.650, M 1.265.343, N 1.849 não divulgado): a base das taxas |
| `palavras_por_nome` | gênero → nº de palavras (com partículas e sufixo) → pessoas |
| `tipo_prenome` | gênero → simples / composto / devocional / composto_devocional → pessoas |
| `compostos` | gênero → `[["MARIA APARECIDA", 7517], …]`, os 400 pares de prenomes mais comuns (contagem ≥ 5). Só os dois prenomes, nunca sobrenome |
| `segundo_prenome` | gênero → `[["APARECIDA", 34779], …]`: o 2º prenome dos compostos, para montar pares fora da lista |
| `por_primeiro_prenome` | gênero → 1º prenome ASCII → `{n, composto, devocional}`: com esse 1º prenome, a fração com 2º prenome e com devoção (ANA: 0,93 composto; MARIA: 0,63 e 0,25; JOÃO: 0,46 e 0,19). Os 400 mais comuns |
| `devocoes` | lista de `{complemento, total, F, M, taxa_F, taxa_M, primeiros}`: DE FÁTIMA, BATISTA, DE LOURDES, DAS GRAÇAS, DO SOCORRO, DE CÁSSIA, NONATO, DE ASSIS, DAS CHAGAS… (58), com os 1ºs prenomes a que se ligam |
| `sobrenomes_por_nome` | gênero → nº de sobrenomes (sem partícula e sufixo; "5" = 5+) → pessoas |
| `sobrenomes_por_tipo_prenome` | o mesmo por tipo de prenome |
| `particula_por_posicao` | k = 1..4 sobrenomes → lista de k distribuições de partícula (1º, 2º… sobrenome) |
| `sufixos` | gênero → FILHO / JUNIOR / NETO / SOBRINHO / NETTO / SEGUNDO… → pessoas; `nenhum` = sem sufixo |
| `de_jesus` | gênero → fração com DE JESUS em qualquer posição, logo depois do prenome e como último sobrenome |
| `e_entre_sobrenomes` | taxa de nomes com E entre sobrenomes por gênero, e os sobrenomes mais comuns antes e depois do E |
| `acento` | por gênero: fração dos nomes com algum acento e por tipo (til, cedilha, agudo, circunflexo, trema); entre os nomes com palavra acentuável, quantos têm acento; `estilo` = nos nomes com 2+ palavras acentuáveis, quantos acentuam todas, parte ou nenhuma |

## Fontes e licença

* **Prenomes**: IBGE, Censo Demográfico 2010, frequência de primeiro nome por sexo, via Brasil.IO (dataset
  `genero-nomes`, `https://data.brasil.io/dataset/genero-nomes/nomes.csv.gz`, de Álvaro Justen/turicas, a partir da
  API de nomes do IBGE). Dados públicos do IBGE; o Brasil.IO publica seus datasets sob CC BY-SA 4.0 (confira na página
  do dataset). Daqui saem `nome`, `freq_f` e `freq_m`.
* **Nomes civis completos**: TSE, dados abertos de candidaturas (`consulta_cand_<ano>.zip`, coluna `NM_CANDIDATO`,
  `https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/`), todas as eleições de 2004 a 2024 (11 arquivos,
  ~610 MB, 2,92 milhões de candidaturas, 1,93 milhão de pessoas únicas). Dados públicos do Portal de Dados Abertos do
  TSE (termos em dadosabertos.tse.jus.br).

**Privacidade.** Do TSE só sai estatística agregada. Nenhum nome completo é gravado, nem no cache: os zips são lidos
em fluxo, sem extrair, e só contagens ficam em memória. A deduplicação usa um hash de 8 bytes do nome com a data de
nascimento. Sobrenome visto menos de 5 vezes é descartado. Nenhuma combinação prenome+sobrenome real é gravada: os
pares em `compostos` são só de prenomes, e as listas da estrutura exigem contagem ≥ 5. Os zips são apagados no fim.

## Como refazer

```powershell
.venv\Scripts\python.exe ferramentas\construir_vocabulario_nomes.py [--cache DIR] [--saida DIR] [--manter-zips]
```

O padrão de `--cache` é `%TEMP%\rotulador_nomes`. Baixa ~612 MB e leva ~3 minutos mais o download. A memória fica em
algumas centenas de MB. O script é idempotente: com os mesmos dados, rodar duas vezes dá os mesmos bytes (gzip sem
data, ordem fixa). Só usa a biblioteca padrão. Para conferir:
`.venv\Scripts\python.exe -m unittest tests.test_vocabulario_nomes`.

## Como foi feito (decisões)

* **Pessoas.** As candidaturas de 2004 a 2024 são lidas do arquivo `_BRASIL.csv` de cada ano (os arquivos por UF
  repetem os mesmos registros). Quem aparece em mais de um ano ou turno, com o mesmo nome sem acento e a mesma data
  de nascimento, conta uma vez, com a grafia do ano mais recente. Saem ~100 nomes com dígito, hífen ou vírgula. O
  apóstrofo vira espaço, como o TSE já grava ("D AVILA"), e depois é juntado de volta (D'ÁVILA).
* **Prenome × sobrenome.**
  * O 1º token é sempre prenome.
  * O 2º é 2º prenome quando:
    * é prenome do Censo;
    * aparece no TSE mais como 1º prenome do que como último sobrenome sem partícula antes
      (r = 1º/(1º+último) ≥ 0,5: CARLOS 0,97, PAULA 0,77, ROSA 0,29, BATISTA 0,01);
    * combina com o gênero da pessoa pelo Censo (MARIA JOSÉ e JOSÉ MARIA são aceitos);
    * ainda sobra sobrenome depois.
  * No máximo dois prenomes.
* **Devoções.** Depois do prenome, com sobrenome depois, contam como parte do prenome:
  * DE FÁTIMA, DO SOCORRO, DE CÁSSIA, DE LOURDES, DAS GRAÇAS, DAS CHAGAS, DAS DORES, DA PENHA, DE NAZARÉ, DE RIBAMAR,
    DA GLÓRIA, DO ESPÍRITO SANTO, DE PÁDUA, D'ARC… (lista no script). No TSE quase nunca são o último sobrenome: DE
    FÁTIMA aparece 4.216 vezes no meio e 35 no fim.
  * Os que também são sobrenome comum só contam depois de certos prenomes: MARIA DA CONCEIÇÃO, MARIA DO CARMO,
    FRANCISCO DE ASSIS, JOÃO BATISTA, JOÃO BOSCO, RAIMUNDO NONATO, LUIZ GONZAGA…
  * **DE JESUS fica como sobrenome**: aparece no meio e no fim quase na mesma proporção. A estrutura dá a taxa à parte.
* **Sobrenomes.** Cada sobrenome guarda a partícula logo antes dele (DA, DE, DO, DOS, DAS, E).
  * Prefixos colam no seguinte: SÃO, SANTA, VILA(S), VILLA(S), BOA, BOM, ESPÍRITO, DAL, DALLA, DELLA, DEL, DI, VON, VAN,
    DER, DEN, TEN, LA, LE, MAC, MC, ORO, MONT.
  * D, SANT e DALL juntam com apóstrofo.
  * Palavras de uma letra são descartadas.
  * Sufixos só contam no fim de um nome com 3+ palavras: FILHO, FILHA, JUNIOR, JR, NETO, NETA, NETTO, SOBRINHO,
    SOBRINHA, SEGUNDO, TERCEIRO, II, III.
* **Acentos.** O registro civil e o cadastro eleitoral omitem muito o acento. Só 17% dos nomes têm algum acento.
  JOSÉ sai acentuado em ~48% das vezes, ANTÔNIO em 15–23%, ARAÚJO em 31–37%. O erro de digitação fica abaixo de 2%
  (MARÍA 0,02%, JÉSUS 0,2%). Por isso a regra **não** é "≥ 60% das ocorrências", que deixaria de fora JOSÉ, ANTÔNIO,
  MÁRCIA e ARAÚJO. A grafia acentuada vale quando:
  * ao menos 10% das ocorrências têm acento válido, e no mínimo 2;
  * uma grafia acentuada tem ≥ 60% das acentuadas (o "dominante" vale entre as grafias acentuadas);
  * a grafia é válida: sem acento grave, no máximo um acento tônico, e sem o acento diferencial anterior a 1971
    (MACÊDO, LÔBO, CÔRTES).

  Prenome que o TSE viu pouco ou não viu usa a **regra do final**: o final de 3 a 6 letras com acento somado sobre
  todas as palavras do TSE que terminam assim. Exemplos: AÇÃO, ÉLIO, ÊNCIA, ÁRIO, IÃO. São 61 finais. Na validação
  (metade das palavras contra a outra metade), a regra acertou 97% das vezes em que disparou. Ela vale:
  * quando ver zero acentos em n ocorrências ainda é provável, isto é, (1 − taxa)ⁿ ≥ 5%;
  * nunca em romanização japonesa ou chinesa (HISAO, ZHAO).

  No mapa de acentos ficam de fora:
  * letra solta e sigla de UF (RUA O, BA, CE);
  * palavras de 2 letras com n < 50 e de 3 letras com n < 20;
  * palavras comuns cuja forma acentuada vem de poucos nomes (CONDE, CABO, LAGOA, LUA, CIRCO…);
  * as que dependem do contexto: MARCO/MARÇO, MARQUES/MARQUÊS, ROMA/ROMÃ, IRMA/IRMÃ, MEIER/MÉIER, SE/SÉ.

## Números

* Prenomes: 100.787, que cobrem 100% das pessoas do Censo 2010 (≥ 50 pessoas: 60.086 nomes, 99,3%; ≥ 1.000: 8.225
  nomes, 93,4%). Acento: 4.005 prenomes ganham grafia acentuada (2.436 pelo TSE, 1.569 pela regra), cobrindo 19,5% da
  população. `origem_acento`: tse 30.159 (96,5% da população), regra 1.569, `-` 69.059 (3,4% da população, nomes raros).
* Sobrenomes, os 10 primeiros: SILVA 315.904 (DA 0,76), SANTOS 187.940 (DOS 0,72), OLIVEIRA 122.904 (DE 0,72),
  SOUZA 100.500 (DE 0,76), PEREIRA 84.548 (nenhuma 1,00), FERREIRA, ALVES, RODRIGUES, LIMA (DE 0,45), COSTA (DA 0,45).
  JESUS: DE 0,95. NASCIMENTO: DO 0,62. ARAÚJO: DE 0,50. ROCHA: DA 0,35.
* Estrutura (F / M):
  * prenome simples 56,8% / 61,0%; composto 37,7% / 37,4%; devocional 5,5% / 1,5%;
  * nº de sobrenomes: 1 → 21% / 35%, 2 → 63% / 61%, 3 → 15% / 3,8%;
  * nº de palavras mais comum: 4 (42% / 44%);
  * sufixo: 0,2% / 5,35% (FILHO 2,05%, JÚNIOR 1,67%, NETO 1,30%, SOBRINHO 0,26%);
  * DE JESUS em qualquer posição: 1,6% / 1,1%; E entre sobrenomes: 0,42% / 0,26%;
  * nome com algum acento: 13,6% / 18,3%.
* Partícula por posição: com 2 sobrenomes, o 1º tem partícula em 17,8% e o 2º em 39,1%. Com 1 sobrenome, 34,5%.

## Para usar no gerador (recomendações)

* **Gênero e prenome.**
  * Sorteie o gênero pela proporção do Censo (51,07% F).
  * Sorteie o 1º prenome com peso `freq_f` ou `freq_m`: a frequência real, não a posição.
  * A ordem do arquivo já é por frequência; o "núcleo" dos N primeiros continua valendo para a repartição por split.
* **Composto ou devoção.** Use `por_primeiro_prenome[g][NOME]` quando o nome está lá. Senão, a taxa geral do gênero
  (`tipo_prenome`).
  * Composto: sorteie o par em `compostos[g]` que começa com o 1º prenome. Se não houver, sorteie o 2º em
    `segundo_prenome[g]`, sem repetir o 1º.
  * Devoção: sorteie em `devocoes` entre as que têm esse prenome em `primeiros`.
* **Sobrenomes.**
  * Quantos: por `sobrenomes_por_tipo_prenome[tipo]` (devoção e composto puxam menos sobrenomes).
  * Cada um com peso `n`, sem repetir. A partícula de cada um sai de `p_*`.
  * `particula_por_posicao` mostra que o último sobrenome leva partícula mais vezes que o primeiro: se quiser, ajuste.
  * O E entre sobrenomes já vem em `p_e` (SILVA 1,3%).
* **Sufixo** só em homens (5,35%, por `sufixos["M"]`). Nas mulheres, NETA e FILHA são raríssimos; FILHO, JUNIOR e NETO
  em mulheres são artefatos ou sobrenomes: não gere.
* **Acento.** No comprovante, imprimir acento é estilo do emissor. Muitos sistemas usam o nome do CPF, que vem sem
  acento. Decida por documento. Quando o documento tem acento:
  * use `acentuado` com probabilidade `frac_acento` (o registro civil real), ou sempre (grafia "correta");
  * `acento.estilo` mostra que, num mesmo nome, é comum acentuar só parte (29%).
* **Repartição por split.** A chave é o ASCII. Sobrenomes compostos (VILAS BOAS) e com apóstrofo (D'AVILA) são uma
  entrada só.
* **Mapa de acentos para ruas e bairros.**
  * Em nome de pessoa, use todas as linhas.
  * Em logradouro e bairro, prefira `n ≥ 20` ou `origem = manual` (1.238 + 344 linhas): abaixo disso há ruído de
    grafia de família (FALÇONI, LORENÇATTO).
  * Combine com a restauração de acentos da base de lugares (`LEIA-ME_lugares.md`): o dicionário dela resolve as
    palavras comuns; este mapa, os nomes próprios.
  * Trate pelo contexto: "DE MARCO" depois de número vira MARÇO; "MARQUES DE" (título) vira MARQUÊS; IRMA antes de
    nome vira IRMÃ.

## Limitações

* **População do TSE.** São candidatos: adultos (sobretudo de 30 a 60 anos), 66% homens, mais de cidades pequenas.
  Por isso as taxas vêm por gênero, mas os compostos refletem gerações mais velhas (MARIA APARECIDA, JOSÉ CARLOS).
  Nomes de gente mais nova (MARIA EDUARDA, JOÃO PEDRO) estão lá, mas abaixo do que seriam hoje. Os prenomes (Censo
  2010) cobrem toda a população, inclusive crianças.
* **Prenome × sobrenome é estatístico.** Nomes com 3 prenomes têm o 3º contado como sobrenome. ~1,1% da massa de
  sobrenomes são palavras que são prenomes fortes (FRANCISCO, LUIZ, PAULO, MARIA): parte é sobrenome de fato, parte
  vem disso. ROSA e CONCEIÇÃO como 2º nome (MARIA ROSA) contam como sobrenome (r < 0,5).
* **Acento.** Prenomes com evidência fraca ficam sem acento (LUIS: LUÍS só em ~9% dos registros; o mapa de endereço
  traz LUÍS à mão). Sobrenomes com grafia antiga muito usada continuam como estão registrados: CORRÊA (17%), VILLAS
  BÔAS, FIGUEIRÔA. A regra do final é conservadora e não alcança a maioria dos nomes raros (69 mil ficam como no Censo).
* **Recorte por frequência.** A cauda de sobrenomes com menos de 5 ocorrências (96 mil distintos, 4,3% das ocorrências)
  não está aqui, por privacidade. Grafias muito raras de prenome existem só porque vêm do Censo, que corta abaixo de
  20 pessoas.
