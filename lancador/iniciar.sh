#!/usr/bin/env bash
# Sobe o rotulador (porta 5010) e o servidor de IA (porta 5051) sem janela.
# Logs e demonstração ficam em $ROTULADOR_BASE (padrão E:/rotulador, porque o C: vive cheio).
# Usa dados/ do projeto se houver documentos; senão, gera e usa contas de demonstração.
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${ROTULADOR_BASE:-/e/rotulador}"
mkdir -p "$BASE/logs"
cd "$PROJ" || exit 1

if find dados -type f ! -name .gitkeep | grep -q .; then
  ARGS=()
else
  [ -d "$BASE/demo/dados" ] || .venv/Scripts/python.exe tests_ui/gerar_amostras.py "$BASE/demo/dados"
  ARGS=(--dados "$BASE/demo/dados" --saida "$BASE/demo/saida")
fi

export PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
.venv/Scripts/python.exe app.py --porta 5010 --sem-navegador "${ARGS[@]}" > "$BASE/logs/rotulador.log" 2>&1 &
if [ -x .venv-ia/Scripts/python.exe ]; then
  .venv-ia/Scripts/python.exe ia_servidor.py > "$BASE/logs/ia.log" 2>&1 &
fi
wait
