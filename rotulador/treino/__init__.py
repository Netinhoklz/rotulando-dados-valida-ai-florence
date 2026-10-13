"""O lado do treino: carregador do dataset gerado (``dados``), protocolo de avaliação (``avaliar``), conjunto
de robustez (``robustez``) e linha de base sem treino (``base``). Nada aqui depende de framework; o envoltório
PyTorch em ``dados.conjunto_torch`` importa o torch só quando chamado."""
