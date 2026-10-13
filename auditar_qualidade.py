"""Auditoria de qualidade dos rótulos e das edições (ver rotulador/qualidade.py).

    python auditar_qualidade.py SAIDA [--split treino] [--ocr] [--gravar]
"""
import sys

from rotulador.qualidade import main

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
