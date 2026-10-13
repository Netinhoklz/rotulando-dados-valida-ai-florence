"""Catálogo das técnicas de adulteração: o "COMO" de cada edição.

Toda edição salva tem uma técnica (automática ou manual), com:
  - ``num``: valor do pixel na máscara de técnicas (mascaras_tecnicas). NUNCA troque o num de
    uma técnica já usada em dados salvos; técnica nova = num novo;
  - família (o tipo de fraude, alinhado à taxonomia do "Find it again!", ICDAR 2023:
    IMI imitação, CPI cópia interna, CPO colagem de outro documento, CUT remoção,
    PIX traço/pixel; mais códigos e IA generativa);
  - nível (simples/amador, profissional, IA) e granularidade (caractere, trecho, campo);
  - opções ("como"), cada uma com valores possíveis; "aleatorio" sorteia por edição.

A CATEGORIA (o QUÊ: nome, valor, data...) continua vindo de config/categorias.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FAMILIAS = {
    "imitacao": {"rotulo": "Imitação (texto redesenhado)", "literatura": "IMI",
                 "dica": "o texto novo é desenhado imitando a fonte do documento"},
    "copia_interna": {"rotulo": "Cópia interna (copy-move)", "literatura": "CPI",
                      "dica": "letras ou campos copiados de outro lugar do MESMO documento"},
    "colagem_externa": {"rotulo": "Colagem de outro documento (splicing)", "literatura": "CPO",
                        "dica": "letras ou campos trazidos de OUTRO documento do mesmo split"},
    "remocao": {"rotulo": "Remoção / apagamento", "literatura": "CUT",
                "dica": "conteúdo apagado (preenchimento, corretivo, letra removida)"},
    "pixel": {"rotulo": "Traços e pixels", "literatura": "PIX",
              "dica": "traços acrescentados ou raspados de uma letra (3→8, 8→3)"},
    "codigos": {"rotulo": "Códigos (barras, QR)", "literatura": "IMI",
                "dica": "código de barras ou QR gerado de novo"},
    "ia": {"rotulo": "Generativa (IA)", "literatura": "GEN",
           "dica": "um modelo de difusão (Qwen) reescreve a região"},
    "recriacao": {"rotulo": "Recriação (documento refeito)", "literatura": "FAB",
                  "dica": "o documento inteiro é refeito num editor: mesmo layout, texto todo re-tipografado"},
    "manual": {"rotulo": "Manual (editor)", "literatura": "MAN",
               "dica": "edição feita à mão no modo avançado"},
}
NIVEIS = {"simples": "Simples (amador)", "profissional": "Profissional", "ia": "IA generativa", "manual": "Manual"}
GRANULARIDADES = {"caractere": "Caractere", "trecho": "Trecho do campo", "campo": "Campo inteiro",
                  "regiao": "Região livre", "pagina": "Página inteira"}

COLAGEM = {"rotulo": "Como colar", "valores": [("tinta", "só a tinta (alfa suave)"),
                                               ("retangulo", "retângulo com o fundo (amador)"),
                                               ("poisson", "mistura de Poisson"),
                                               ("papel", "recorte de papel colado (borda e sombra, depois fotografado)")]}
ALVO = {"rotulo": "Que caractere", "valores": [("digito", "só dígitos"), ("letra", "só letras"),
                                               ("qualquer", "dígito ou letra")]}
COLAGEM_IA = {"rotulo": "Como a saída da IA volta", "valores": [
    ("selecao", "só dentro da região (recorte exato)"), ("suave", "borda suave de 1–4 px"),
    ("contexto", "um retângulo em volta do campo, regenerado inteiro")]}
# Quem apaga o texto antigo. Um fornecedor só (PatchMatch) vira a impressão digital do gerador (OSTF): o
# detector decora a textura do apagamento em vez da fraude. Cada ferramenta real apaga de um jeito.
APAGAR = {"rotulo": "Como apagar", "valores": [
    ("patchmatch", "preenchimento inteligente (PatchMatch, Content-Aware Fill)"),
    ("telea", "inpainting por difusão (borracha de app de celular)"),
    ("ns", "inpainting Navier-Stokes (borracha mágica simples)"),
    ("chapado", "cor chapada do papel (balde do Paint)"),
    ("borrao", "borrão do entorno (pincel de recuperação simples)"),
    ("carimbo", "carimbo: papel vizinho copiado por cima"),
    ("recuperar", "pincel de recuperação: textura de papel vizinho no tom do entorno")]}
VESTIGIO = {"rotulo": "Vestígio do texto antigo", "valores": [("nenhum", "apagado limpo"),
                                                               ("leve", "sobra um fantasma fraco do texto antigo")]}
RASTERIZADOR = {"rotulo": "Rasterizador do texto", "valores": [
    ("freetype", "antialias padrão (FreeType/Skia: celular, Photoshop)"),
    ("gdi", "GDI do Windows (hinting, poucos níveis de cinza: Paint, Word)"),
    ("cleartype", "ClearType (franjas coloridas nas bordas: captura de tela do Windows)"),
    ("aliasado", "sem antialias (bordas serrilhadas)")]}
# categorias em que só dígitos podem mudar (trocar a letra de "R$", "kWh" ou "SET" não é fraude plausível)
NUMERICAS = frozenset({"valor", "data", "cep", "cpf/cnpj", "consumo/leitura", "identificador documento",
                       "linha digitável", "mês de referência"})


@dataclass(frozen=True)
class Tecnica:
    id: str
    num: int
    nome: str
    familia: str
    nivel: str
    granularidade: str
    descricao: str
    tipos: tuple = ("texto",)  # tipos de campo aceitos (texto, barras, qr)
    categorias: tuple | None = None  # None = qualquer categoria do tipo aceito
    opcoes: dict = field(default_factory=dict)
    automatica: bool = True  # aparece no gerador
    precisa_ia: bool = False
    precisa_doador: bool = False
    precisa_pdf: bool = False  # só em documento que é PDF com camada de texto (edição vetorial)

    @property
    def literatura(self) -> str:
        return FAMILIAS[self.familia]["literatura"]

    def para_json(self) -> dict:
        return {"id": self.id, "num": self.num, "nome": self.nome, "familia": self.familia, "nivel": self.nivel,
                "granularidade": self.granularidade, "descricao": self.descricao, "literatura": self.literatura,
                "tipos": list(self.tipos), "categorias": None if self.categorias is None else list(self.categorias),
                "opcoes": {k: {"rotulo": v["rotulo"], "valores": [list(x) for x in v["valores"]]}
                           for k, v in self.opcoes.items()},
                "automatica": self.automatica, "precisa_ia": self.precisa_ia, "precisa_doador": self.precisa_doador,
                "precisa_pdf": self.precisa_pdf}


_LISTA = [
    # ------------------------------------------------------------------ imitação (IMI)
    Tecnica("campo.reescrever", 1, "Reescrever o campo (fonte imitada)", "imitacao", "profissional", "campo",
            "Apaga o texto com preenchimento inteligente e escreve um valor falso no mesmo formato, na fonte, "
            "tamanho, cor, desfoque e grão do documento.",
            opcoes={"alinhamento": {"rotulo": "Alinhamento", "valores": [("esquerda", "pela esquerda"),
                                                                         ("direita", "pela direita")]}, "fonte_editor": {"rotulo": "Fonte", "valores": [("imitada", "a mais parecida entre todas as instaladas"), ("substituta", "a do editor de PDF (Arial, Helvetica, Times, Courier), a mais parecida")]},
                    "apagar": APAGAR, "vestigio": VESTIGIO}),
    Tecnica("campo.amador", 2, "Reescrever estilo amador (retângulo + fonte padrão)", "imitacao", "simples", "campo",
            "Cobre o campo com um retângulo liso e digita o valor falso numa fonte comum, sem imitar a original "
            "e sem grão — como no Paint/Word.",
            opcoes={"fundo": {"rotulo": "Retângulo", "valores": [("papel", "cor do papel"), ("branco", "branco puro")]},
                    "fonte": {"rotulo": "Fonte", "valores": [("Arial", "Arial"), ("Calibri", "Calibri"),
                                                             ("Times New Roman", "Times New Roman"),
                                                             ("Verdana", "Verdana")]},
                    "rasterizador": RASTERIZADOR}),
    Tecnica("campo.acrescentar", 3, "Acrescentar texto no fim do campo", "imitacao", "profissional", "trecho",
            "Escreve um complemento depois do texto (APTO, sobrenome, dígito a mais) na fonte do documento, "
            "sem apagar nada.",
            categorias=("nome", "endereço", "bairro", "identificador documento")),
    Tecnica("trecho.trocar", 4, "Trocar só um trecho (dia, centavos, número...)", "imitacao", "profissional", "trecho",
            "Edição direcionada: troca só uma parte do campo — o dia da data, os centavos, o número da casa, o "
            "sobrenome, os dígitos verificadores — e mantém o resto intacto.",
            opcoes={"apagar": APAGAR, "vestigio": VESTIGIO}),
    Tecnica("caractere.redesenhar", 5, "Trocar um caractere (redesenhado na fonte)", "imitacao", "profissional",
            "caractere",
            "Apaga UMA letra/dígito e desenha outro no lugar, na fonte, cor e desfoque do documento.",
            opcoes={"alvo": ALVO, "apagar": APAGAR}),
    Tecnica("caractere.inserir", 6, "Inserir um dígito", "imitacao", "profissional", "caractere",
            "Abre espaço empurrando o resto do número e insere um dígito a mais (151,37 → 1151,37; nº 101 → 1011).",
            categorias=("valor", "consumo/leitura", "endereço", "identificador documento"), opcoes={"apagar": APAGAR}),
    Tecnica("campo.mesma_fonte", 8, "Reescrever na mesma fonte (as 10 mais usadas em documentos)", "imitacao",
            "profissional", "campo",
            "Identifica a fonte de CADA campo entre as 10 famílias mais usadas em documentos (Arial, Helvetica, Times "
            "New Roman, Calibri, Verdana, Tahoma, Segoe UI, Courier New, Georgia, Arial Narrow; regular ou negrito), "
            "mede tamanho, largura, cor, desfoque e linha de base no próprio campo, apaga o texto com preenchimento "
            "inteligente e escreve o valor falso nessa mesma fonte. Nomes e endereços pela esquerda; valores pela direita.",
            opcoes={"alinhamento": {"rotulo": "Alinhamento", "valores": [("esquerda", "pela esquerda"),
                                                                         ("direita", "pela direita")]},
                    "apagar": APAGAR}),
    Tecnica("campo.reescrever_pdf", 7, "Reescrever o campo no PDF (edição vetorial)", "imitacao", "profissional",
            "campo",
            "Edita a camada de TEXTO do PDF, como um editor de PDF faz: apaga as palavras do campo (redação) e "
            "escreve o valor falso com a fonte embutida do próprio arquivo, ou com a fonte de reserva do editor "
            "(Helvetica, Times, Courier) quando a embutida não tem as letras. A página é rasterizada de novo: não "
            "há rastro de pixel além da tipografia. Só em PDF com texto.",
            opcoes={"fonte_pdf": {"rotulo": "Fonte", "valores": [("embutida", "a fonte do PDF (se embutida e com as letras)"),
                                                                 ("substituta", "a de reserva do editor (Helvetica/Times/Courier)")]},
                    "alinhamento": {"rotulo": "Alinhamento", "valores": [("esquerda", "pela esquerda"),
                                                                         ("direita", "pela direita")]}},
            precisa_pdf=True),
    # ------------------------------------------------------------------ cópia interna (CPI)
    Tecnica("caractere.copiar_glifo", 10, "Trocar um caractere copiando a letra do próprio documento",
            "copia_interna", "profissional", "caractere",
            "Apaga uma letra/dígito e cola no lugar a MESMA letra recortada de outro ponto do documento "
            "(mesma fonte e impressão).", opcoes={"colagem": COLAGEM, "alvo": ALVO, "apagar": APAGAR}),
    Tecnica("caractere.transpor", 11, "Inverter dois dígitos vizinhos", "copia_interna", "profissional",
            "caractere", "Troca de lugar dois dígitos vizinhos (15 → 51) movendo os próprios glifos.",
            opcoes={"apagar": APAGAR}),
    Tecnica("campo.copiar_interno", 12, "Copiar outro campo do documento por cima", "copia_interna",
            "profissional", "campo",
            "Apaga o campo e cola por cima a imagem de outro campo do mesmo tipo do MESMO documento "
            "(ex.: a data de leitura no lugar do vencimento).", opcoes={"colagem": COLAGEM, "apagar": APAGAR}),
    Tecnica("campo.letras_do_documento", 13, "Reescrever com as letras do próprio documento", "copia_interna",
            "profissional", "campo",
            "Como um falsificador profissional: o valor falso é MONTADO letra a letra com as letras recortadas do "
            "próprio documento — do próprio campo e das linhas no mesmo estilo (altura, cor da tinta, espessura do "
            "traço e nitidez iguais) —, com a tinta transferida sobre o papel reconstruído (antialias, grão e "
            "desfoque de verdade). A fonte identificada serve só de gabarito de posição; o vão entre letras e "
            "palavras é o do documento. Apaga só a tinta antiga (fundo local). Letra que o documento não tem sai "
            "desenhada na fonte (rótulo 'misto'); faltando mais de 25% delas, não é usada.",
            opcoes={"alinhamento": {"rotulo": "Alinhamento", "valores": [("esquerda", "pela esquerda"),
                                                                         ("direita", "pela direita")]}}),
    # ------------------------------------------------------------------ colagem externa (CPO)
    Tecnica("caractere.glifo_externo", 20, "Trocar um caractere com letra de outro documento",
            "colagem_externa", "profissional", "caractere",
            "Como copiar letra, mas o glifo vem de OUTRO documento do mesmo split.",
            opcoes={"colagem": COLAGEM, "alvo": ALVO, "apagar": APAGAR}, precisa_doador=True),
    Tecnica("campo.colagem_externa", 21, "Colar o mesmo campo de outro documento (splicing)", "colagem_externa",
            "profissional", "campo",
            "Apaga o campo e cola o campo da mesma categoria de OUTRO documento do mesmo split, escolhido por "
            "altura e tamanho parecidos.", opcoes={"colagem": COLAGEM, "apagar": APAGAR}, precisa_doador=True),
    Tecnica("bloco.colagem_externa", 22, "Colar o bloco de endereço inteiro de outro documento", "colagem_externa",
            "profissional", "campo",
            "Troca VÁRIAS linhas de uma vez: o bloco de endereço (rua, bairro, CEP, cidade), em linhas vizinhas, "
            "é apagado e recebe por cima o bloco equivalente de OUTRO documento do mesmo split, como um recorte de "
            "tela colado. A máscara de classe fica com a classe do campo escolhido.",
            categorias=("endereço", "bairro", "cep", "cidade/uf"), opcoes={"colagem": COLAGEM, "apagar": APAGAR},
            precisa_doador=True),
    # ------------------------------------------------------------------ remoção (CUT)
    Tecnica("caractere.apagar", 30, "Apagar um caractere", "remocao", "profissional", "caractere",
            "Remove um dígito/letra e fecha o vão movendo o resto do texto (ou deixa o vão). Só em campos de "
            "tamanho variável: formatos fixos (data, CEP, CPF) ficariam impossíveis.",
            categorias=("valor", "consumo/leitura", "endereço", "identificador documento", "nome", "bairro"),
            opcoes={"vao": {"rotulo": "O vão", "valores": [("fechar", "fechar (mover o texto)"),
                                                           ("deixar", "deixar o espaço")]}, "alvo": ALVO,
                    "apagar": APAGAR, "vestigio": VESTIGIO}),
    Tecnica("campo.remover", 31, "Apagar o campo (preenchimento inteligente)", "remocao", "profissional", "campo",
            "Apaga o campo inteiro, ou só a parte final dele, reconstruindo o papel (PatchMatch ou outro apagador).",
            opcoes={"parte": {"rotulo": "O que apagar", "valores": [("campo", "o campo todo"),
                                                                     ("final", "só a última palavra")]},
                    "apagar": APAGAR, "vestigio": VESTIGIO}),
    Tecnica("campo.ocultar", 33, "Esconder o campo com um objeto (dedo, adesivo, post-it, fita)", "remocao", "simples",
            "campo", "Na hora de fotografar, um dedo, um adesivo, um post-it ou uma fita cobrem o campo. Não há edição "
            "digital: o objeto está na foto (e, nos autênticos, há dedos na borda segurando o papel).",
            tipos=("texto", "barras", "qr"),
            opcoes={"objeto": {"rotulo": "Objeto", "valores": [("dedo", "dedo"), ("adesivo", "adesivo/etiqueta"),
                                                                 ("postit", "post-it"), ("fita", "fita adesiva")]}}),
    Tecnica("campo.rabiscar", 34, "Rabiscar por cima (caneta, marcador ou pincel do app)", "remocao", "simples", "campo",
            "Riscos por cima do campo até não dar para ler: caneta esferográfica no papel, marcador digital ou o pincel "
            "de rabisco do WhatsApp/Markup.", tipos=("texto", "barras", "qr"),
            opcoes={"tinta": {"rotulo": "Tinta", "valores": [("caneta", "caneta esferográfica"), ("marcador", "marcador grosso"),
                                                               ("pincel_app", "pincel do app (cor chapada)")]}}),
    Tecnica("campo.borrar", 35, "Borrar ou pixelizar o campo (app de celular)", "remocao", "simples", "campo",
            "A ferramenta de 'borrar/pixelizar' dos apps de foto por cima do campo: desfoque forte ou mosaico.",
            tipos=("texto", "barras", "qr"),
            opcoes={"modo": {"rotulo": "Modo", "valores": [("desfoque", "desfoque forte"), ("mosaico", "mosaico/pixelizado")]}}),
    Tecnica("campo.corretivo", 32, "Corretivo (retângulo liso)", "remocao", "simples", "campo",
            "Cobre o campo com um retângulo liso da cor do papel ou branco, sem textura — corretivo/Paint.",
            opcoes={"fundo": {"rotulo": "Retângulo", "valores": [("papel", "cor do papel"), ("branco", "branco puro")]}, "forma": {"rotulo": "Forma", "valores": [("retangulo", "retângulo (ferramenta de forma)"), ("pincelada", "pinceladas do app (borda irregular)"), ("liquido", "corretivo líquido no papel")]}}),
    # ------------------------------------------------------------------ traços (PIX)
    Tecnica("caractere.acrescentar_tracos", 40, "Acrescentar traços (3→8, 1→4...)", "pixel", "simples", "caractere",
            "Transforma um dígito em outro só ACRESCENTANDO tinta, como uma caneta por cima (sem apagar).",
            opcoes={"cor": {"rotulo": "Cor do traço", "valores": [("mesma", "a mesma da tinta"),
                                                                  ("diferente", "um pouco diferente (caneta)")]}, "tinta": {"rotulo": "Tinta", "valores": [("digital", "traço digital, borda nítida"), ("caneta", "caneta esferográfica no papel")]}}),
    Tecnica("caractere.raspar_tracos", 41, "Raspar traços (4→1...)", "pixel", "profissional", "caractere",
            "Transforma um dígito em outro APAGANDO parte dos traços e reconstruindo o papel.",
            opcoes={"apagar": APAGAR}),
    Tecnica("trecho.jitter", 42, "Retoque sem mudar o texto (jitter de textura)", "pixel", "profissional", "trecho",
            "Reprocessa um trecho de texto SEM mudar o que está escrito (desfoca, reamostra, tremido, aguça, "
            "recomprime ou muda 1–5% do tamanho) e cola de volta com borda suave. É o \"Texture Jitter\" do OSTF "
            "(Qu et al., AAAI 2025): o detector aprende a costura e o processamento local, em vez de decorar como "
            "cada editor desenha letras.",
            opcoes={"operacao": {"rotulo": "Retoque", "valores": [
                ("desfocar", "desfocar"), ("reamostrar", "reduzir e ampliar"), ("tremido", "tremido (movimento)"),
                ("nitidez", "aguçar"), ("jpeg", "recomprimir em JPEG"), ("tamanho", "mudar 1–5% do tamanho"),
                ("combinado", "dois retoques juntos")]}}),
    # ------------------------------------------------------------------ códigos
    Tecnica("codigo.regenerar", 50, "Novo código de barras (+ linha digitável coerente)", "codigos",
            "profissional", "campo",
            "Gera um código de barras ITF-25 válido no lugar do original e reescreve a linha digitável para "
            "combinar com ele.", tipos=("barras",)),
    Tecnica("codigo.qr", 51, "Novo QR code / PIX", "codigos", "profissional", "campo",
            "Gera um QR code PIX novo (payload válido) no lugar do original.", tipos=("qr",)),
    # ------------------------------------------------------------------ IA
    Tecnica("ia.reescrever", 60, "IA reescreve o campo", "ia", "ia", "campo",
            "O modelo de difusão (Qwen) escreve o valor falso na região do campo imitando o documento; só a "
            "região muda (ou, conforme o modo de colagem, uma borda suave ou o recorte de contexto inteiro).",
            opcoes={"colagem_ia": COLAGEM_IA}, precisa_ia=True),
    Tecnica("ia.caractere", 61, "IA troca um caractere", "ia", "ia", "caractere",
            "A IA reescreve o campo com UM caractere trocado, mas só a região dessa letra é aproveitada.",
            opcoes={"alvo": ALVO}, precisa_ia=True),
    Tecnica("ia.remover", 62, "IA apaga o campo", "ia", "ia", "campo",
            "A IA remove o texto do campo e reconstrói o fundo.", opcoes={"colagem_ia": COLAGEM_IA}, precisa_ia=True),
    # ------------------------------------------------------------------ recriação (documento refeito)
    Tecnica("pagina.recriar", 112, "Documento refeito do zero (todas as linhas re-tipografadas)", "recriacao",
            "profissional", "pagina",
            "A conta inteira é recriada num editor (Word, Canva, editor de PDF) com o mesmo layout: TODAS as linhas "
            "de texto da página são apagadas e digitadas de novo com o mesmo texto; logotipo, código de barras, QR e "
            "linhas de tabela ficam como estão. Os campos com valor trocado vêm depois, por outras técnicas, por cima "
            "desta. Chamada com o pseudo-campo da página (tipo \"pagina\"), nunca com um campo.",
            tipos=("pagina",),
            opcoes={"fonte_editor": {"rotulo": "Fonte", "valores": [
                        ("imitada", "cada linha na fonte mais parecida com a original"),
                        ("substituta", "uma fonte de escritório para a página toda: Arial, Calibri, Times New Roman ou Verdana")]},
                    "rasterizador": RASTERIZADOR, "apagar": APAGAR}),
    # ------------------------------------------------------------------ manuais (editor avançado)
    Tecnica("manual.pintura", 100, "Pincel (manual)", "manual", "manual", "regiao", "Pintura à mão livre.",
            automatica=False),
    Tecnica("manual.clonagem", 101, "Carimbo (manual)", "manual", "manual", "regiao", "Clonagem com carimbo.",
            automatica=False),
    Tecnica("manual.copia_movimento", 102, "Mover/duplicar seleção (manual)", "manual", "manual", "regiao",
            "Copy-move de uma seleção.", automatica=False),
    Tecnica("manual.colagem", 103, "Colar de outro documento (manual)", "manual", "manual", "regiao",
            "Splicing manual.", automatica=False),
    Tecnica("manual.preenchimento", 104, "Preencher/corretivo/remendo (manual)", "manual", "manual", "regiao",
            "Remoção com preenchimento.", automatica=False),
    Tecnica("manual.texto", 105, "Texto (manual)", "manual", "manual", "regiao", "Escrever ou substituir texto.",
            automatica=False),
    Tecnica("manual.ajuste", 106, "Ajuste de imagem (manual)", "manual", "manual", "regiao",
            "Brilho, desfoque, ruído, JPEG local...", automatica=False),
    Tecnica("manual.codigo", 107, "Código de barras/QR (manual)", "manual", "manual", "regiao",
            "Gerador de código na seleção.", automatica=False),
    Tecnica("manual.ia", 108, "IA na seleção (manual)", "manual", "manual", "regiao", "Edição por IA com prompt.",
            automatica=False),
    Tecnica("manual.forma", 109, "Forma/balde (manual)", "manual", "manual", "regiao", "Formas e balde de tinta.",
            automatica=False),
    Tecnica("manual.retoque", 110, "Retoque (manual)", "manual", "manual", "regiao", "Desfocar, borrar, clarear...",
            automatica=False),
    Tecnica("manual.mover_camada", 111, "Mover camada (manual)", "manual", "manual", "regiao",
            "Deslocamento de uma camada inteira.", automatica=False),
]
TECNICAS: dict[str, Tecnica] = {t.id: t for t in _LISTA}
POR_NUM: dict[int, Tecnica] = {t.num: t for t in _LISTA}
assert len(POR_NUM) == len(_LISTA), "num repetido no catálogo"
DESCONHECIDA = 255

# operação do editor -> técnica (o que não está aqui não é edição: camadas, borracha)
_OPERACAO = {"pincel": "manual.pintura", "carimbo": "manual.clonagem", "transformar": "manual.copia_movimento",
             "colar": "manual.colagem", "preencher": "manual.preenchimento", "corretivo": "manual.preenchimento",
             "remendo": "manual.preenchimento", "recuperacao": "manual.preenchimento", "texto": "manual.texto",
             "substituir_texto": "manual.texto", "ajuste": "manual.ajuste", "codigo_barras": "manual.codigo",
             "qrcode": "manual.codigo", "ia": "manual.ia", "forma": "manual.forma", "balde": "manual.forma",
             "retoque": "manual.retoque", "mover_camada": "manual.mover_camada",
             "trocar_campo_ia": "ia.reescrever"}


# campos detectados de código de barras e QR são imagem, não texto: só técnicas desses tipos servem
TIPO_DA_CATEGORIA = {"codigo barras": "barras", "qr code/pix": "qr"}


def compativel(t: Tecnica, categoria: str) -> bool:
    """A técnica serve, em princípio, para campos desta categoria? (o texto de cada campo, o
    ``tecnicas.aplicavel`` confere na hora)"""
    return TIPO_DA_CATEGORIA.get(categoria, "texto") in t.tipos and (t.categorias is None or categoria in t.categorias)


# Modo fácil do gerador: as técnicas automáticas agrupadas em JEITOS de adulterar, em linguagem simples.
# Cada técnica automática está em exatamente um jeito (conferido abaixo); o nível de cada técnica
# (amador, profissional, IA) é o "capricho" que a pessoa escolhe à parte.
JEITOS = [
    {"id": "reescrever", "rotulo": "Trocar o texto todo", "icone": "✍️",
     "dica": "o valor antigo some e o novo é escrito no lugar, imitando a fonte (ou de qualquer jeito, no amador)",
     "exemplo": ["R$ 171,37", "R$ 405,82"],
     "tecnicas": ["campo.mesma_fonte", "campo.letras_do_documento", "campo.reescrever", "campo.amador", "ia.reescrever",
                  "campo.reescrever_pdf"]},
    {"id": "trecho", "rotulo": "Mudar só um pedaço", "icone": "✂️",
     "dica": "troca o dia, os centavos ou o número da casa; ou acrescenta algo no fim do campo",
     "exemplo": ["10/09/2026", "18/09/2026"], "tecnicas": ["trecho.trocar", "campo.acrescentar"]},
    {"id": "caractere", "rotulo": "Mexer em uma letra ou dígito", "icone": "🔤",
     "dica": "troca, insere, apaga ou inverte um caractere; ou acrescenta/raspa traços (3→8, 8→3)",
     "exemplo": ["R$ 171,37", "R$ 177,37"],
     "tecnicas": ["caractere.redesenhar", "caractere.copiar_glifo", "caractere.glifo_externo", "caractere.inserir",
                  "caractere.apagar", "caractere.transpor", "caractere.acrescentar_tracos", "caractere.raspar_tracos",
                  "ia.caractere"]},
    {"id": "colar", "rotulo": "Copiar e colar", "icone": "📋",
     "dica": "cola por cima outro campo do mesmo documento, ou o mesmo campo de outro documento (do mesmo split)",
     "exemplo": ["MARIA SOUZA", "JOÃO LIMA"],
     "tecnicas": ["campo.copiar_interno", "campo.colagem_externa", "bloco.colagem_externa"]},
    {"id": "apagar", "rotulo": "Apagar ou esconder", "icone": "🧽",
     "dica": "o campo some ou fica ilegível: preenchimento inteligente, corretivo, IA, dedo/adesivo na foto, rabisco, borrão",
     "exemplo": ["Apto 45", ""], "tecnicas": ["campo.remover", "campo.corretivo", "ia.remover", "campo.ocultar",
                                               "campo.rabiscar", "campo.borrar"]},
    {"id": "codigo", "rotulo": "Gerar código novo", "icone": "▥",
     "dica": "código de barras novo (com a linha digitável coerente) ou QR code PIX novo",
     "exemplo": ["8462 0000 1713", "8391 0000 4058"], "tecnicas": ["codigo.regenerar", "codigo.qr"]},
    {"id": "retoque", "rotulo": "Retoque sem mudar o texto", "icone": "🖌️",
     "dica": "mexe só na textura dos pixels e o texto continua igual: o detector aprende a olhar o pixel, não o valor",
     "exemplo": ["R$ 171,37", "R$ 171,37"], "tecnicas": ["trecho.jitter"]},
    {"id": "recriar", "rotulo": "Refazer o documento inteiro", "icone": "📄",
     "dica": "a página toda é apagada e re-tipografada num editor, mantendo logotipo e códigos; os campos trocados vêm por cima",
     "exemplo": ["conta original", "conta refeita"], "tecnicas": ["pagina.recriar"]},
]
_nos_jeitos = [tid for j in JEITOS for tid in j["tecnicas"]]
assert sorted(_nos_jeitos) == sorted(t.id for t in _LISTA if t.automatica), "técnica automática fora dos jeitos"
assert len(_nos_jeitos) == len(set(_nos_jeitos)), "técnica em dois jeitos"


def tecnica_da_operacao(tipo: str, params: dict | None) -> Tecnica | None:
    """Técnica de uma entrada do histórico (None = não é edição de pixel: camadas, borracha)."""
    params = params or {}
    if tipo == "tecnica":
        return TECNICAS.get(str(params.get("tecnica", "")))
    if tipo == "trocar_campo":
        t = params.get("tipo", "texto")
        return TECNICAS["codigo.regenerar" if t == "barras" else "codigo.qr" if t == "qr" else "campo.reescrever"]
    chave = _OPERACAO.get(tipo)
    return TECNICAS.get(chave) if chave else None


def legenda() -> dict:
    """Para catalogo.json na raiz da saída: num -> técnica."""
    return {"_leia": "mascaras_tecnicas: valor do pixel = num da técnica; 0 = não editado; "
                     f"{DESCONHECIDA} = edição sem técnica conhecida.",
            "tecnicas": [t.para_json() for t in _LISTA], "familias": FAMILIAS, "niveis": NIVEIS,
            "granularidades": GRANULARIDADES}


def para_json() -> dict:
    return {"tecnicas": [t.para_json() for t in _LISTA], "familias": FAMILIAS, "niveis": NIVEIS,
            "granularidades": GRANULARIDADES, "jeitos": JEITOS, "tipo_da_categoria": TIPO_DA_CATEGORIA}
