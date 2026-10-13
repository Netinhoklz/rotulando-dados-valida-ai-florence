# Gabarito do Brasil: o detector de campos consultando as bases

O detector de campos (`rotulador/campos.py`) usa as bases desta pasta para **reconhecer** os campos do documento,
não só para gerar valores falsos. A linha solta sem rótulo ("JOÃO BATISTA DOS SANTOS", "ALDEOTA", "FORTALEZA CE",
"R JOAO CORDEIRO 1100") é conferida contra os prenomes e sobrenomes do país, os 278 mil bairros, os 5.570 municípios
e os tipos de logradouro. As consultas ficam em `rotulador/gabarito.py`: elas carregam as bases na primeira chamada
e as guardam em cache.

Sem as bases, as consultas respondem "não sei" e o detector volta às regras antigas.

## O que ele reconhece

| consulta | base | regra |
|---|---|---|
| `nome_de_pessoa(t)` → 0..1 | `prenomes.tsv.gz`, `sobrenomes.tsv.gz` | 1º token é prenome (≥ 50 pessoas no Censo) ou inicial; nota = fração dos tokens conhecidos (prenome, sobrenome, partícula, FILHO/JÚNIOR/NETO); sem dígito; dá 0 se a linha começa com tipo de rua ou com título de homenagem (DOUTOR, PADRE, PRESIDENTE, DR., PE.…) ou tem S/N, Nº, KM |
| `homenagem(t)` → nº de municípios | `logradouros.tsv.gz` | quantas cidades têm rua com exatamente esse nome: GETÚLIO VARGAS 447, RUI BARBOSA 336, JOSÉ DA SILVA 15; ≥ 40 é homenagem famosa |
| `logradouro_sem_tipo(t, cod, cep)` → `cidade`/`cep`/`pais`/`""` | `logradouros.tsv.gz` | rua conhecida sem o tipo, com número ou S/N ("RUI BARBOSA 455", "JOSÉ DE ALENCAR S/N"); nome de 2 palavras ou mais |
| `titulo(t)` | `logradouros.tsv.gz` | 1ª palavra é título de rua: os da coluna `titulo` do CNEFE com ≥ 50 ruas (menos os que são prenome de mais de 1.000 pessoas: SANTA, SANTO, MARQUES, PIO) e as abreviações |
| `empresa(t)` | — | termina em LTDA, ME, EPP, EIRELI, S/A, CIA (cliente pessoa jurídica) |
| `municipio(t)` → (nome, UF, código) | `municipios.tsv` | o texto inteiro é um município, com UF opcional no fim ("BELO HORIZONTE MG", "Fortaleza - CE"); a UF tem de bater |
| `bairro(t, cod, cep)` → `cidade`/`cep`/`pais`/`""` | `bairros.tsv.gz` | bairro da cidade do documento, do prefixo do CEP, ou de algum lugar do país; aceita "DISTRITO DE …"/"POVOADO …" e o OCR que quebrou a palavra ("AL D EOTA") |
| `tipo_logradouro(t)` → (tipo, força) | `tipos_logradouro.tsv` | ver abaixo |
| `emissor(t)` | `emissores.tsv` | apelido da concessionária como palavra inteira (ENEL, CEMIG, SABESP, VIVO…) |
| `complemento(t)` | — | APTO, BLOCO, CASA, LOTE, QD, SALA… com número ou letra |

### Tipos de logradouro (`tipos_logradouro.tsv`, 406 tipos)

Colunas: `tipo`, `forca`, `variantes` (separadas por `|`, sem acento e sem ponto) e `n_domicilios` (contagem do CNEFE
2022 em `enderecos_estatisticas.json`).

- **forte** (42): RUA, AVENIDA, ESTRADA, TRAVESSA, RODOVIA, ALAMEDA, SÍTIO, CONDOMÍNIO… São sempre logradouro.
  - "R" sozinho precisa de número.
  - A abreviação de até 3 letras sem número precisa de uma palavra de verdade depois. Assim "AL D EOTA" continua
    sendo o bairro.
- **ambiguo**: VILA, JARDIM, PARQUE, CONJUNTO, SETOR… Também são nome de bairro ("VILA NOVA"), então só valem com
  número, S/N ou KM. Os tipos raros (menos de 2.000 domicílios) ficam de fora, porque trazem palavras comuns como
  "CENTRAL" e "LINHA".
- **df** (83): as siglas de Brasília (SQN, SHIS, QNM, CLN…). Precisam de um número logo depois.
- O número de telefone não conta como número da casa ("Central de atendimento: 0800 …").
- "SET" não é abreviação de SETOR: é o mês no histórico ("Set 2026").

Abreviações: tabela de tipos do DNE dos Correios, mais as que aparecem em conta (AV, AVDA, ESTR, TRV, COND, SIT…).

### Emissores (`emissores.tsv`, 73)

Colunas: `nome`, `setor` (energia, saneamento, gás, telecom), `ufs`, `apelidos`.

A lista foi montada à mão em 12/10/2026 a partir das distribuidoras de energia (ANEEL), das companhias estaduais de
saneamento, das de gás canalizado e das operadoras. A linha que cita o emissor é dele, inclusive o endereço da
empresa ("R Sena Madureira, 1020 - Centro" na conta da NIO). Não é campo do cliente.

## Como o detector usa

Isto fica em `_pelo_gabarito`, que roda antes do bloco de endereçamento.

### Nome de gente em rua, bairro e cidade

Muita rua, bairro e cidade tem nome de pessoa: GETÚLIO VARGAS, CARLOS PRATES, JOSÉ WALTER, JOÃO PESSOA, ANTÔNIO CARLOS.
Para todos eles a nota de nome de pessoa é 1,0, então quem decide é a **posição no envelope** (nome / rua / bairro /
CEP e cidade) e a **homenagem**:

- logo **acima** da rua é o nome, mesmo que seja cidade ("PAULO AFONSO") ou bairro ("MARIA DOS SANTOS");
- logo **abaixo** da rua, ou do complemento dela, nunca é nome: é o bairro (CARLOS PRATES, JOSÉ WALTER), mesmo sem
  a cidade na página;
- município **com UF** é cidade em qualquer lugar. **Sem UF** e com nome de gente, a posição decide:
  - acima da rua, é o nome;
  - debaixo da rua com a cidade ou o CEP embaixo, é o bairro;
  - sozinho na página, é homenagem;
- linha solta com nome de homenagem famosa (rua em 40 cidades ou mais) não vira nome; o nome de cliente sozinho
  continua virando nome;
- título de homenagem no começo da linha ("PRESIDENTE KENNEDY", "MARECHAL DEODORO DA FONSECA") e S/N, Nº ou KM
  fazem a linha não ser gente;
- rua conhecida sem o tipo, com número ou S/N, vira `endereço` se for rua da cidade ou do CEP do documento, ou se
  vier logo abaixo de um nome de pessoa;
- se a rua do bloco é do emissor, o nome e o bairro que o gabarito marcou em volta dela voltam a ficar sem
  categoria.

Pela sonda `E:/rotulador/ferr/sonda_homenagem.py` (16 envelopes com essas confusões, 54 linhas), o detector passou de
43 para 54 linhas certas.

### Ordem

1. A linha que é um município vira `cidade/uf` (com as regras de posição acima).
2. A rua sem tipo vira `endereço`.
3. O código IBGE e o CEP do documento restringem o bairro: com eles, só vale bairro da cidade ou do CEP; sem eles,
   vale bairro do país.
4. Cada linha sem categoria é testada nesta ordem:
   - complemento logo abaixo da rua vira `endereço`;
   - linha debaixo da rua só pode ser `bairro`;
   - nome de pessoa claro (nota ≥ 0,9, não empresa, não emissor) vira `nome` quando está logo acima da rua, ou
     quando o bairro não é da cidade/CEP e o nome não é homenagem famosa;
   - bairro conhecido, perto do endereço e com até 5 palavras, vira `bairro`.
5. "NOME - CPF: …" na mesma linha é separado em `nome` (parte da linha) e `cpf`.
6. Endereço: `_eh_endereco` aceita a regex antiga **ou** `tipo_logradouro`.

## Resultado

Medido em 12/10/2026 com a bateria de `E:/rotulador/analise_campos`, que tem 64 casos sintéticos com gabarito
(com rótulo, sem rótulo, envelope, DF, zona rural, PJ), cada um em 3 fontes × limpo/escaneado. Acerto por categoria:

| categoria | antes | com o gabarito |
|---|---|---|
| nome | 56% | 99% |
| endereço | 65% | 100% |
| bairro | 46% | 100% |
| cidade/uf | 91% | 99% |
| cep | 93% | 93% |

Nos 11 documentos reais de teste, a única linha que mudou de categoria foi o endereço da NIO, que agora é do
emissor. Os testes ficam em `tests/test_gabarito.py`.

## Refazer

```
python ferramentas/construir_gabarito.py   # tipos_logradouro.tsv e emissores.tsv
```

Este script depende de `enderecos_estatisticas.json` (de `construir_vocabulario_lugares.py`). Prenomes, sobrenomes,
municípios e bairros vêm de `construir_vocabulario_nomes.py` e `construir_vocabulario_lugares.py`. As fontes e as
licenças estão em `LEIA-ME_nomes.md` e `LEIA-ME_lugares.md`.
