"""Abre o editor numa janela do Windows apontando para um servidor que já está rodando (o do lançador, porta 5010).

    .venv\\Scripts\\pythonw.exe janela.py            (porta 5010)
    .venv\\Scripts\\pythonw.exe janela.py --porta 5000

Com ``python app.py`` (sem --sem-navegador) a janela já abre sozinha, com o servidor junto.
"""

import argparse
import socket
import sys

from rotulador import janela


def main() -> None:
    ap = argparse.ArgumentParser(description="Editor do rotulador numa janela do Windows")
    ap.add_argument("--porta", type=int, default=5010)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    if not janela.disponivel():
        sys.exit("Instale o pywebview: .venv\\Scripts\\python.exe -m pip install pywebview")
    try:
        socket.create_connection((a.host, a.porta), timeout=2).close()
    except OSError:
        sys.exit(f"Nenhum rotulador rodando em {a.host}:{a.porta}: rode o lançador ou python app.py --porta {a.porta}")
    janela.abrir(f"http://{a.host}:{a.porta}")


if __name__ == "__main__":
    main()
