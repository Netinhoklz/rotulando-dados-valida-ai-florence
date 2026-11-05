"""O editor numa janela própria do Windows, em vez de uma aba do navegador.

No navegador, vários atalhos do Photoshop são do próprio navegador e não chegam à página (Ctrl+Tab, Ctrl+W, Ctrl+N,
Ctrl+R, F5, Ctrl+P…). Aqui a página roda no WebView2 (o motor do Edge, que já vem no Windows) pelo pywebview: sem
o modo de depuração, o pywebview desliga os atalhos de navegador (AreBrowserAcceleratorKeysEnabled) e o editor recebe
todas as teclas. O que a página guarda (nome do operador, último tipo da edição, opções das ferramentas) fica num perfil
fixo: ``ROTULADOR_JANELA_PERFIL`` ou a pasta ``.janela`` do projeto.
"""

from __future__ import annotations

import os
from pathlib import Path

TITULO = "Rotulador UNIDOCK"
PERFIL = Path(os.environ.get("ROTULADOR_JANELA_PERFIL") or Path(__file__).resolve().parents[1] / ".janela")


def disponivel() -> bool:
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return True


class Api:
    """O que a página chama por ``window.pywebview.api``: outra janela (o gerador), tela cheia e sair."""

    def __init__(self, endereco: str):
        self._endereco = endereco.rstrip("/")
        self._janelas: dict[str, object] = {}

    def abrir(self, caminho: str, titulo: str = "") -> None:
        import webview
        j = self._janelas.get(caminho)
        if j is not None and j in webview.windows:
            j.restore()
            j.show()
            return
        self._janelas[caminho] = webview.create_window(f"{titulo} · {TITULO}" if titulo else TITULO,
                                                       self._endereco + caminho, js_api=self, maximized=True,
                                                       min_size=(900, 600))

    def tela_cheia(self) -> None:
        import webview
        (webview.active_window() or webview.windows[0]).toggle_fullscreen()

    def sair(self) -> None:
        import webview
        for j in list(webview.windows):
            j.destroy()


def abrir(endereco: str) -> None:
    """Abre o editor e só volta quando a última janela fecha."""
    import webview
    webview.settings["ALLOW_DOWNLOADS"] = True  # o gerador baixa a receita (.json)
    PERFIL.mkdir(parents=True, exist_ok=True)
    webview.create_window(TITULO, endereco, js_api=Api(endereco), maximized=True, min_size=(900, 600),
                          text_select=True)
    webview.start(private_mode=False, storage_path=str(PERFIL))
