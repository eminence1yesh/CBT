#!/usr/bin/env bash
# macOS / Linux launcher:  ./run.sh
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "Creating a private Python environment..."
    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
else
    . .venv/bin/activate
fi
python app.py
