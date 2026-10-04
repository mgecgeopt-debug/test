@echo off
rem Richtet in der Windows-Aufgabenplanung ein, dass der Deal-Finder bei jeder
rem Anmeldung automatisch im Hintergrund startet. Einmal per Doppelklick ausfuehren.
cd /d "%~dp0"
schtasks /Create /F /TN "DealFinder" /SC ONLOGON /RL LIMITED ^
  /TR "wscript.exe \"%~dp0start_unsichtbar.vbs\""
if errorlevel 1 (
    echo Fehlgeschlagen. Rechtsklick auf diese Datei ^> "Als Administrator ausfuehren".
    pause
    exit /b 1
)
echo Aufgabe "DealFinder" angelegt. Startet jetzt sofort ...
schtasks /Run /TN "DealFinder"
echo.
echo Fertig. Entfernen mit:  schtasks /Delete /TN "DealFinder" /F
pause
