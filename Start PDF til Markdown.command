#!/bin/zsh
# Dobbeltklikk for å starte. Første gang settes Python-miljøet opp automatisk.
VENV="$HOME/.venvs/pdf_converter"
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Setter opp Python-miljø (bare første gang) …"
  PY=/opt/homebrew/bin/python3.12
  [ -x "$PY" ] || { brew install python@3.12 || exit 1; }
  "$PY" -m venv "$VENV" && "$VENV/bin/pip" install -q "pymupdf4llm==1.28.2" || exit 1
fi

TESS="$VENV/tessdata"
if [ ! -f "$TESS/nor.traineddata" ]; then
  echo "Setter opp OCR (bare første gang) …"
  command -v tesseract >/dev/null || brew install tesseract || exit 1
  mkdir -p "$TESS"
  cp "$(brew --prefix)"/share/tessdata/{eng,osd}.traineddata "$TESS/"
  curl -fsSL -o "$TESS/nor.traineddata" https://github.com/tesseract-ocr/tessdata_best/raw/main/nor.traineddata || exit 1
fi

# Stopp en eventuell gammel instans på samme port
lsof -ti tcp:8765 | xargs kill 2>/dev/null

exec "$VENV/bin/python" "$DIR/app.py"
