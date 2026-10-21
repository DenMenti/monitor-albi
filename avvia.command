#!/bin/bash
# Versione locale per Mac: doppio clic per avviare.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then
  echo "Python 3 non trovato. Installalo da https://www.python.org/downloads/"; read -p "Premi Invio per chiudere"; exit 1
fi
if [ ! -x venv/bin/python ]; then
  echo "Prima installazione: preparo il programma, ci vuole qualche minuto..."
  python3 -m venv venv
  venv/bin/python -m pip install --upgrade pip
  venv/bin/python -m pip install -r requirements.txt
  venv/bin/python -m playwright install chromium
fi
venv/bin/python app.py
