# Geração de dados para detectar fraude em documentos — o que diz a literatura

Pesquisa de 02/10/2026 sobre geração de dados sintéticos de adulteração de documentos para
treinar detectores, e o que isso implica para o rotulador. Itens marcados **(não verificado)**
não puderam ser confirmados na fonte primária.

## 1. Datasets públicos e como foram gerados

| Dataset | Tamanho | Como foi gerado | Rótulos | Ressalvas |
|---|---|---|---|---|
| [DocTamper](https://github.com/qcf-568/DocTamper) (CVPR 2023) | 170 mil imagens (120 mil adulteradas), cerca de 50 mil docs | regras automáticas sobre a caixa do texto: copy-move, splicing, "generation" (troca de conteúdo), cores estimadas por Sauvola | máscara de pixel | Deixa atalhos visíveis: fonte trocada, desfoque, fundo e alinhamento inconsistentes ([Dhouib 2026](https://arxiv.org/html/2602.17322)). O método DTD cai de 68% a 73% de F1 com crop de 2% ou deslocamento de 1 px ([ADCD-Net](https://arxiv.org/html/2507.16397)). Uso não comercial. |
| [RTM](https://arxiv.org/html/2312.06934v1) (Pattern Recognition 2024) | 14.250 imagens | 5.986 manuais (25 profissionais no Photoshop), 5.258 automáticas (scripts + OCR), 3.006 autênticas | máscara (camadas do PS) | **Manual é muito mais difícil:** IoU de 29,9% contra 57,6% no automático |
| [OSTF](https://arxiv.org/html/2407.21422v2) (AAAI 2025) | 4.418 imagens | 8 editores de texto por IA (SRNet, MOSTEL, DiffSTE, AnyText, TextDiffuser...) | máscara | Treinar em um editor e testar em outro derruba o F de 85 para 0 |
| [Find it again!](https://l3i.univ-larochelle.fr/app/uploads/sites/12/2024/05/ICDAR_2023_Find_it_again.pdf) (ICDAR 2023) | 988 recibos, 163 forjados | 19 voluntários com Paint/GIMP | caixa + tipo de forja (CPI, CPO, IMI, CUT, PIX) + campo alterado + transcrição | Os autênticos têm anotações benignas (carimbo, marca-texto, manuscrito) |
| [SIDTD](https://arxiv.org/html/2401.01858v1) (Scientific Data 2024) | 1.222 forjados (IDs) | crop & replace + inpainting e re-render | classe | Few-shot por nacionalidade: acurácia de 0,53 a 0,64 |
| [AIForge-Doc](https://arxiv.org/html/2602.20569) (2026) | 4.061 imagens | edição por Gemini/Ideogram de valores, datas e endereços | máscara | O detector DTD chega só a AUC 0,563; edição por difusão não deixa traço de JPEG |

Também: T-SROIE e Tampered-IC13 (gerados com SRNet; gerador do T-SROIE **não verificado**),
[ForensicHub](https://arxiv.org/abs/2505.11003) (benchmark que reúne os anteriores),
[FantasyID](https://arxiv.org/abs/2507.20808) e DLC-2021 (impressão, foto e recaptura de tela).

## 2. O que faz o dado sintético funcionar em fraude REAL

- **Realismo vale mais que volume.** O trabalho de [Dhouib et al. (2026)](https://arxiv.org/html/2602.17322):
  - escolhe o recorte doador por similaridade (mesma linha, altura e nº de caracteres);
  - rejeita recortes que cortam letras;
  - renderiza o texto em **várias fontes e cores e fica com a mais parecida**.

  Com isso, o F1 zero-shot foi de 9,4 para 15,7, e o FFDN no FindItAgain foi de 11,3 para 25,5.
- **Atalhos de compressão e formato.**
  - Variar só o fator de qualidade do JPEG não representa as tabelas de quantização reais
    ([DocQT](https://arxiv.org/abs/2605.19688)).
  - No CASIA v2, só formato e metadados já dão AUC 0,92
    ([estudo](https://arxiv.org/pdf/2607.06615)).
  - Detectores baseados no grid DCT 8×8 quebram com deslocamento de 1 px.
- **Impressão digital do gerador.** O modelo aprende o editor usado, e não a fraude
  ([OSTF](https://arxiv.org/html/2407.21422v2)). Por isso é preciso misturar editores.
- **Calibração do limiar.** A área adulterada é só 0,3% a 4% dos pixels, e um limiar fixo de 0,5
  derruba o F1. Recalibrar com 10 imagens do domínio recupera de 39% a 55% da perda
  ([DOCFORGE-BENCH](https://arxiv.org/abs/2603.01433)).
- **Impressão e foto.** É o teste mais difícil (SIDTD, FantasyID). Não achei estudo quantitativo de
  impressão/scan para texto adulterado **(não verificado)**.

### Atualização (02/10/2026, segunda rodada): o que mais generaliza

- **Operações e disfarces de falsificadores reais.** O [FSTS](https://proceedings.neurips.cc/paper_files/paper/2025/file/df34ea73a7e2108b816194200f1442be-Paper-Datasets_and_Benchmarks_Track.pdf)
  (NeurIPS 2025) registrou o que falsificadores fazem no Photoshop. Depois da edição: desfoque em
  12–17% dos casos, aguçar em 13%, curvas em 8–17%, cor em 10%. Treinar nessa distribuição, em vez
  do DocTamper, mudou o F1 médio em cinco conjuntos reais: TruFor 0,198 → 0,477, DTD 0,097 → 0,345.
- **Texture Jitter** ([OSTF](https://arxiv.org/abs/2407.21422)): trechos de texto autêntico
  reprocessados (desfoque, reamostragem, JPEG, aguçar) **sem mudar o conteúdo**. O F1 médio em 81
  cenários cruzados foi de 34,8–45,8 para 72,0. Retoque forte em texto pequeno vira atalho.
- **Mesmo pós-processamento no par.** O SBI aplica a mesma aumentação no real e no falso. Igualar
  JPEG e tamanho entre as classes deu +11 pontos entre geradores
  ([Fake or JPEG?](https://arxiv.org/abs/2403.17608)).
- **Tabelas JPEG reais** ([DocQT](https://arxiv.org/abs/2605.19688)): treinar com as tabelas de um
  corpus real levou o FFDN de 0,708 para 0,853 de F1, e o falso positivo por pixel de 5,6e-4 para
  8,7e-5. Variar só a qualidade não substitui.
- **Grade e redimensionamento.** O TruFor treina com redimensionamento de 0,5–1,5 e JPEG 30–100. O
  [Wu et al. (CVPR 2022)](https://openaccess.thecvf.com/content/CVPR2022/papers/Wu_Robust_Image_Forgery_Detection_Over_Online_Social_Network_Shared_Images_CVPR_2022_paper.pdf)
  modela redes sociais: Facebook reduz acima de 2048 px e usa JPEG 71–95, e o F1 nesse canal foi
  de 0,331 para 0,488.
- **WhatsApp** ([Bertini et al.](https://arxiv.org/abs/2006.03903); tese NCMF): lado maior de
  1600 px, 4:2:0, JPEG ~70–80 no celular e ~92 na web; saída determinística.
- **Colagem da IA.** Treinar só com a saída colada exatamente na máscara ensina a costura; em
  regiões regeneradas inteiras o F1 cai ([TGIF](https://arxiv.org/abs/2407.11566),
  [GIFL](https://arxiv.org/abs/2504.07462)). Misturar colagem dura, suave e de contexto. O Face
  X-ray treinado com alfa e testado com Poisson caiu de AUC 99,5 para 94,6.
- **Negativos.** O GIFL com autênticos 1:1 levou a acurácia em autênticos do TruFor de 0,66 para
  1,00. Negativos benignos reduzem o falso positivo (MVSS-Net; o *Find it again* mantém anotações
  nos autênticos).
- **Rótulos.** A banda "sem nota" na borda da máscara vem do NIST MFC; perda de borda/soft mask
  ajudam (MVSS-Net, Face X-ray). A hierarquia de rótulos ajuda (HiFi-Net).
- **Documentação.** Datasheet ([Gebru et al.](https://arxiv.org/abs/1803.09010)); avaliar com
  TPR em FPR fixo e intervalo por bootstrap sobre documentos (NIST MFC2018, CAT-Net).

### Atualização (03/10/2026, terceira rodada): o ponto de vista de quem frauda

- **Find it again!** (ICDAR 2023, 19 voluntários): metade dos recibos foi forjada no Paint; 78% das
  455 áreas são copiar-colar de dentro do próprio documento, 8% imitação, 8% apagar, 7% traço à mão,
  2% colar de outro documento. Alvos: total/pagamento 234, produto 95, data/hora 82.
- **FSTS**: na substituição, Content-Aware Fill 61,7%, desfoque 39,7%, ruído 34,9%; cada pessoa repete
  os próprios parâmetros (recorrência individual).
- **RTM**: o manual é localizado com IoU 29,9% contra 57,6% do automático; profissionais alinham,
  escolhem referência limpa e igualam cor, fonte e fundo.
- **Comprovantes no Brasil** (dynadok, 2026): parte-se de um documento real; a **data** é o campo mais
  alterado (regra dos 90 dias); editor de PDF mantém fonte e logotipo; foto da tela destrói metadados;
  IA em 1 de 50.
- Detectores comerciais (Resistant AI, Inscribe): fontes misturadas, totais que não batem, valores
  redondos, seções desalinhadas.

Implementação: `rotulador/falsificador.py` (falsificador + motivo + hábitos por versão). Detalhes e o que
ficou estimado: [falsificadores.md](falsificadores.md).

### Atualização (03/10/2026, quarta rodada): o que a literatura de 2025–2026 acrescenta

Segunda busca, depois de implementar o canal em duas fases, os fornecedores, a edição vetorial, a perícia e o
perito. O que entrou e de onde veio:

- **A fronteira virou semântica.** Os recibos gerados por GPT-4o + GPT-Image-1 são visualmente plausíveis
  e caem pela **aritmética** (subtotal que não soma, imposto que não bate) e pela plausibilidade factual
  (endereço, loja × itens); humanos ficam em F1 0,85 e um LLM que confere as contas em 0,975
  ([GPT4o-Receipt](https://arxiv.org/html/2603.11442v2)). Na edição por **GPT-Image-2**, o inpainting apaga as
  descontinuidades locais (sem desalinhamento de PRNU nem de bloco JPEG): humanos 50,1%, TruFor AUC 0,60,
  DocTamper 0,585; a recomendação é treinar com positivos de inpainting por IA e explorar microtipografia e
  plausibilidade do texto ([When the Forger Is the Judge](https://arxiv.org/html/2604.25213v1)). Detectores
  comerciais de comprovante conferem CEP × cidade, código de barras × total, datas e janela de 90 dias
  ([Didit](https://didit.me/pt-BR/blog/combating-proof-of-address-fraud-ptbr/), que também lista ELA, clone
  detection e metadados). **Aqui:** `rotulador/coerencia.py` roda as regras de verificador antes e depois da
  edição (CPF/CNPJ, CEP na faixa da cidade, datas em ordem e dentro de 90 dias, mês de referência, DVs da
  linha digitável, código × total, tarifa plausível); a anotação ganha `coerencia` (violações que a edição
  criou, `sem_pista_semantica`) e o datasheet conta quantas versões só o pixel pega. O falsificador de
  recência passou a dar datas coerentes por rótulo (leitura < emissão < vencimento < próxima leitura) em vez
  de uma data igual em todo campo.
- **Laudo por amostra.** Os benchmarks de texto adulterado de 2025–2026 trazem um relatório pericial por
  imagem e treinam detectores que explicam: TFR (45,9 mil falsas × 45,5 mil reais; documentos, cena e
  cartões; 10 técnicas: copy-move, splicing, rendering, SR-Net, DiffUTE, TextDiffuser-2, UDiffText, GPT-4o-image,
  AnyText-2, ControlNet; 16 línguas) em [TextShield-R1](https://arxiv.org/html/2602.19828v1); RealText-V2
  (20 mil amostras, 6 línguas, 6 domínios, 100+ manipulações, laudo de perito) em
  [SEED](https://arxiv.org/html/2606.21138), que também mostra que pares (limpa, forjada) e **menos
  capacidade** (LoRA menor, menos passos) generalizam melhor entre domínios. **Aqui:** cada versão grava um
  `laudo` em texto (`rotulador/laudo.py`): técnica, campo, antes → depois, fornecedores, perícia, canal, pistas
  de pixel medidas pelo perito por edição (`edicoes[].perito`) e pistas semânticas.
- **Degradação física.** [Augraphy](https://augraphy.readthedocs.io/) cataloga o que impressora, scanner e
  fotocópia fazem (ink bleed, low ink, dirty rollers/drum, dirty screen, bleed-through, bad photocopy, fax,
  folding, lighting, lens flare); a competição de forja de identidade de 2026 reproduz ataques de impressão
  (papel brilhante, 180 g, 600 dpi) e de tela (notebook, tablet, celular, várias resoluções)
  ([3ª competição](https://arxiv.org/html/2607.15734v1)); a recaptura de tela deixa artefatos de cromaticidade
  que sobrevivem às redes sociais ([CMA, CVPR 2024](https://openaccess.thecvf.com/content/CVPR2024/html/Chen_CMA_A_Chromaticity_Map_Adapter_for_Robust_Detection_of_Screen-Recapture_CVPR_2024_paper.html)).
  **Aqui:** `rotulador/canal.py` ganhou o **desgaste** da impressão (faixas de tinta fraca, riscos dos rolos,
  sangria da tinta, fotocópia ruim com poeira de toner), o **verso aparecendo** e a **sujeira no vidro** do
  scanner, o **reflexo** na foto e, na tela, subpixels **PenTile**, ponto branco azulado e a faixa do refresh.
  Tudo igual no par (o verso vem da original: ele não muda com a edição).
- **Onde o detector olha.** Em documentos, os traços de geração se concentram nas bordas do texto e as
  regiões homogêneas não ajudam; seleção de regiões por densidade de texto deu +9,6 pontos
  ([AIGDoc](https://arxiv.org/html/2609.14352v1)). O [DocForge-Bench](https://arxiv.org/html/2603.01433v1)
  mede 0,27–4,17% de pixels adulterados e confirma que o limiar 0,5 é "catastroficamente" descalibrado.
  Nada a mudar no gerador: as máscaras e a área editada por versão já estão no datasheet para a calibração.
- **Fora do alcance (sem rede/disco):** editores de texto por difusão além do Qwen (DiffUTE, TextDiffuser-2,
  UDiffText, AnyText-2) e o GPT-Image-2; a edição por IA continua com o servidor local quando ligado.

Outras fontes consultadas nesta rodada: [ForensicHub](https://arxiv.org/pdf/2505.11003),
[Venus-DeFakerOne](https://arxiv.org/pdf/2605.14091), [Detecting Text Manipulation with VLMs](https://arxiv.org/pdf/2509.10278),
[FD-VIED](https://arxiv.org/abs/2311.03650), [survey de identidade](https://arxiv.org/html/2607.01442v1),
[regras programáticas por LLM](https://arxiv.org/pdf/2512.19228), [Inscribe 2026](https://www.inscribe.ai/reports/2026-document-fraud-report).

### Atualização (03/10/2026, quinta rodada): do dado ao treino

Terceira busca, agora olhando o que a literatura faz DEPOIS de ter o dado: como treina e como mede. O que entrou
(`rotulador/treino/`):

- **Limiar descalibrado.** O [DocForge-Bench](https://arxiv.org/html/2603.01433v2) (14 métodos, 8 datasets, zero-shot)
  mede 0,27–4,17% de pixels adulterados; os métodos têm AUC ≥ 0,76 e F1 quase zero no limiar 0,5, porque marcam
  15–25% da página; UM limiar ajustado em 10 imagens do domínio recupera 39–55% do teto (os calibrados ficam entre
  0,02 e 0,15). Recomenda reportar a taxa-base, AUC junto do F1, negativos íntegros na prevalência real e perdas
  sensíveis ao desbalanceamento. **Aqui:** `treino/avaliar.py` implementa o protocolo (AUC/F1/IoU por imagem,
  oráculo, calibração em N versões por histogramas de 256 faixas, redução a 2 MP, imagem sem positivo fora da
  média) mais o que o datasheet já pedia (AUC e TPR por imagem, bootstrap sobre documentos); `dados.pesos` dá a
  taxa-base e o peso do positivo.
- **Par no mesmo lote.** O [SEED](https://arxiv.org/pdf/2606.21138) monta cada lote com pares (limpa, forjada) do mesmo
  documento em vez de embaralhar, e isso bate o treino comum de forma consistente; o "harness" evolui minerando os
  casos em que o modelo erra. **Aqui:** `dados.conjunto_torch` devolve o MESMO recorte da original e da editada;
  `dificuldade` (1 − nota do perito) na anotação e no manifesto ordena o currículo (`dados.curriculo`).
- **Recorte, borda e currículo.** O DTD treina em recortes 512×512 com CE + Dice e cabeça de borda em frequência
  ([Qu et al., CVPR 2023](https://openaccess.thecvf.com/content/CVPR2023/papers/Qu_Towards_Robust_Tampered_Text_Detection_in_Document_Image_New_Dataset_CVPR_2023_paper.pdf));
  o CLTD (currículo) e a mineração por dificuldade revisitam edições pequenas e bordas ambíguas
  ([2606.22285](https://arxiv.org/pdf/2606.22285): CE + Dice + borda, JPEG 50–95 repetido, redimensionar antes de
  recomprimir, currículo do fácil ao difícil); no [AIGDoc](https://arxiv.org/html/2609.14352v1) os traços ficam nas
  bordas do texto. **Aqui:** `dados.recortar` centra o recorte na edição (60%), em texto (30%) ou ao acaso;
  `dados.borda` é o alvo de borda suave (MVSS-Net/Face X-ray); `dados.tinta` separa texto de fundo (o ADCD-Net usa
  OCR para isso).
- **Robustez medida, não suposta.** O [ADCD-Net](https://arxiv.org/html/2507.16397v1) testa com ruído gaussiano,
  desfoque (k=7), redução a 0,7×, JPEG 75–90 e desalinhamento da grade (corte de 2%, deslocamento de 1 px); o
  [CAT-Net](https://arxiv.org/pdf/2108.12947) pré-treina o ramo DCT em JPEG duplo (p-F1 24,6 → 76,5); o
  [DocQT](https://arxiv.org/pdf/2605.19688) varia as tabelas de quantização. **Aqui:** `treino/robustez.py` gera
  cópias do split com JPEG 50/75/90, JPEG duplo, WhatsApp, redução 0,7, desfoque, ruído, tela, impressão e celular
  (máscaras pela mesma geometria), e `dados.aumentar` usa o canal (com as tabelas de aparelho) como aumento de treino.
- **Piso e atalho.** `treino/base.py` usa o mapa do perito como detector sem treino: dá o formato das predições, o
  piso a superar e um teste de atalho no dataset (os números medidos nesta rodada estão no README).

Outras fontes: [ForensicHub](https://arxiv.org/pdf/2505.11003), [Evidence-Guided Text-Centric Forensics](https://arxiv.org/pdf/2609.02097),
[Gardella et al., ICCVW 2025](https://openaccess.thecvf.com/content/ICCV2025W/VisionDocs/papers/Gardella_Scanned_documents_forensics_detecting_inserted_characters_through_noise_and_chromatic_ICCVW_2025_paper.pdf),
[FD-VIED / pré-treino autossupervisionado](https://arxiv.org/abs/2311.03650), [CLUE](https://arxiv.org/pdf/2508.07413).

### Atualização (03/10/2026, sexta rodada): diversidade e qualidade do dataset

O pedido mudou de foco: o produto é o DATASET (o modelo vem depois), então a rodada foi atrás do que ainda
faltava para ele ser diverso e correto, não de receita de treino:

- **Negativos difíceis digitais.** Quem manda um comprovante esconde o próprio CPF com tarja, borrão ou
  pixelado, e apps de banco e sites de 2ª via carimbam "CÓPIA"/"2ª VIA" por cima; verificadores comerciais
  listam esses casos entre os falsos positivos mais comuns ([Ocrolus](https://help.ocrolus.com/hc/en-us/articles/11569926778772-False-Positives)).
  **Aqui:** `rotulador/marcas.py` ganhou as marcas digitais (`DIGITAIS`), que entram em qualquer canal, só em
  campo sensível que não é a edição, iguais no par.
- **Enquadramento e modo de captura.** O [DocForge-Bench](https://arxiv.org/html/2603.01433v2) pede testes em
  condições de imagem não vistas; o [Augraphy](https://augraphy.readthedocs.io/) cataloga o que scanner e
  fotocópia fazem, inclusive o modo "texto"; e boa parte dos comprovantes reais chega como foto de um pedaço
  da conta. **Aqui:** passo `recorte` do canal (só um pedaço da página, contendo a edição quando ela já
  existe), scanner colorido/cinza/P&B binarizado e a mesa em volta do papel na foto (madeira, tecido, outra
  folha, granito).
- **Cobertura ativa e diversidade medida.** Um dataset "equilibrado por lote" fica com lacunas entre lotes.
  **Aqui:** `distribuicao: cobertura` parte do `catalogo.csv` do split; o datasheet traz a matriz técnica ×
  classe, as lacunas compatíveis sem nenhuma edição e a entropia normalizada de cada eixo.
- **Rótulo perfeito é rótulo auditado.** **Aqui:** `auditar_qualidade.py` confere, versão por versão, as
  invariantes das máscaras e do par e sinaliza edição invisível, texto igual ao antigo, área demais, versão
  duplicada, revisão reprovada, perícia que não escondeu nada e (com `--ocr`) campo que o reconhecedor não
  lê; os alertas vão para a anotação e o datasheet os conta.
- **Documento refeito do zero.** A fraude "fabricada" (a conta inteira refeita no Word/Canva, mesmo layout,
  texto re-tipografado) não existia no catálogo. **Aqui:** técnica `pagina.recriar` (família `recriacao`,
  sempre a primeira edição da versão, com as trocas de campo por cima). Um gerador de documentos-base
  sintéticos chegou a ser planejado e foi abandonado: o pedido seguinte foi editar só os documentos originais.

### Atualização (03/10/2026, sétima rodada): editar só a original, com foco em nome e endereço

Depois de testar o aplicativo com a conta real do usuário (fatura com boleto), o problema não era falta de
diversidade, era qualidade: o detector chamava rótulos de endereço ("Data do documento", "Beneficiário
final") e o gerador escrevia uma rua por cima deles; a caixa de cada campo do boleto pegava o rótulo miúdo de
cima e o fio da tabela, a fonte medida saía com o dobro do tamanho e o apagamento borrava o rótulo; e o
padrão passava a imagem por foto, scanner e marcas. **Aqui:** separação de palavras e caixa da linha que
ignoram fios de tabela e o rótulo encostado (`ocr.tinta_da_linha`, `ocr.caixa_da_linha`); filtro de
plausibilidade e bloco de endereçamento sem rótulo no detector (`campos.plausivel`); apagamento que preserva os
fios e o rótulo vizinho e usa a cor do papel em fundo liso; técnica `campo.mesma_fonte` (a fonte de cada
campo identificada só entre as 10 famílias mais usadas em documentos, com regular × negrito decidido pela
quantidade de tinta); edição EXATA dos campos marcados com o mesmo valor novo onde o dado se repete (e
cidade, UF e CEP da mesma cidade); e a receita padrão limpa (`gerador.receita_limpa`).

## 3. Divisão treino/teste e vazamento

- Separar por documento de origem **e** pelos doadores de colagem (componentes conectados no mesmo
  split).
- Separar por emissor/modelo de conta (template): o hold-out por template derruba o desempenho
  ([SIDTD](https://arxiv.org/html/2401.01858v1)).
- Deixar pelo menos um gerador/editor fora do treino ([OSTF](https://arxiv.org/html/2407.21422v2)).
- Viés de aquisição: com uma câmera só, o "Find it!" ficou trivial.

## 4. Recomendação → situação no rotulador

| Recomendação | Situação |
|---|---|
| Cobrir copy-move, splicing, inserção, inpainting e cobertura | ✅ mover/duplicar, colar de outro documento, texto, preenchimento por similaridade, formas/balde |
| Escolher fonte/cor pela mais parecida entre várias candidatas | ✅ `ajustar_fonte` compara o desenho das letras em ~24 fontes (acertou Arial × Arial Bold e a cor branca do cabeçalho nos testes) |
| Igualar grão e borda ao entorno | ✅ "igualar ruído" com referência no papel original (calibrado) + Poisson no remendo e recuperação |
| Difusão: colar só a região editada e calcular a máscara pela diferença real | ✅ a IA só altera a área colada (seleção, borda suave ou retângulo em volta) e a máscara de pixels é a diferença exata |
| Mesma cadeia de pós-processamento em autênticos e adulterados | ✅ original e editada no mesmo formato e qualidade, sem metadados |
| Rótulos ricos: máscara de pixel e de região, entidade, tipo de operação, texto antes e depois | ✅ 5 máscaras (pixels, região, classe, técnica, ignorar) + JSON com categoria, técnica, operações, `texto_antigo` e `texto_novo` em cada edição |
| Divisão por documento e por doador | ✅ o split vem da pasta, doador só do mesmo split e auditoria de vazamento |
| Divisão por emissor/template | ⚠️ não automático: montar as pastas por concessionária |
| Mais de um editor generativo | ⚠️ dois modelos da mesma família (Qwen-Image-Edit-2511 e Qwen-Image-2.1); vale somar outra família (ex.: FLUX.2-klein) |
| Escolha automática do doador por similaridade (copy-move/splicing) | ✅ técnicas de cópia e colagem do gerador (altura, cor e espaço livre) |
| JPEG com tabelas reais, 1 a 3 recompressões, desalinhamento do grid, blur/ruído | ✅ **canal de envio** (`rotulador/canal.py`), igual no par: scanner, celular, tela, impressão, WhatsApp, rede social, app (reamostra + JPEG/WEBP); tabelas JPEG dos documentos de entrada do split; corte de 1–7 px tira a grade do lugar |
| Negativos difíceis: edições benignas (carimbo, marca-texto, anotação) em autênticos | ✅ **marcas benignas** (`rotulador/marcas.py`): autenticação mecânica, carimbo, anotação, marca-texto, rubrica, dobra, furos; iguais no par, fora da edição, só com captura física |
| Disfarce como falsificadores reais (FSTS) | ✅ desfoque/aguçar/curvas/cor depois da edição, nas frequências medidas |
| Texture Jitter (OSTF) | ✅ técnica `trecho.jitter` |
| Colagem da IA variada (TGIF/GIFL) | ✅ região, borda suave ou retângulo em volta do campo (região = área colada; máscara de pixels = o que mudou) |
| Banda de rótulo ambíguo | ✅ `mascaras_ignorar/` |
| Datasheet do dataset | ✅ `DATASHEET.md` + `datasheet.json`, refeitos a cada lote |
| Mesmo cliente em splits diferentes | ✅ a auditoria lê CPF/nome/instalação/endereço pelo OCR (CPF repetido = erro) |
| Teste com adulterações manuais e com impressão/foto | ⚠️ o modo simples serve para o conjunto manual; o canal SIMULA impressão/foto, mas o teste final deve ter capturas físicas de verdade |
| Proporção de mistura | A literatura não prescreve. Referências: RTM ~42% manual / 37% automático / 21% autêntico; DocTamper 80% adulterado / 20% intacto |
| Edições com o ponto de vista de quem frauda: ferramenta, hábitos repetidos, motivo, campos e valores coerentes | ✅ `rotulador/falsificador.py`: 80% das versões saem como uma pessoa faria (celular, Paint, editor de PDF, Photoshop, IA) com um motivo (conta recente, outro titular, outro endereço, valor, apagar); gravado por versão e filtrável |

## 5. Fraude em transações (contexto)

PaySim e AMLworld simulam transações com fraude rotulada. A lição que se transfere: **dado sintético
aumenta a cobertura, mas a validação tem de ser em dado real.**
