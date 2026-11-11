@echo off
REM Windows launcher - double-click this file.
cd /d "%~dp0"
if not exist ".venv" (
    echo Creating a private Python environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
start "" http://127.0.0.1:5000
python app.py
pause
