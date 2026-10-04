@echo off
rem Deal-Finder starten (Windows). Doppelklick oder aus der Aufgabenplanung.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Erstinstallation: virtuelle Umgebung anlegen ...
    py -3.11 -m venv .venv || python -m venv .venv
    .venv\Scripts\python.exe -m pip install -q -r requirements.txt
)
if not exist .env (
    echo FEHLER: .env fehlt. Kopiere .env.example nach .env und trage die Schluessel ein.
    pause
    exit /b 1
)
if not exist logs mkdir logs
echo Deal-Finder laeuft. Log: logs\deal_finder.log  (Fenster schliessen = Stopp)
.venv\Scripts\python.exe -m deal_finder.main %* >> logs\deal_finder.log 2>&1
