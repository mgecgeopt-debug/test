@echo off
rem Beendet den im Hintergrund laufenden Deal-Finder.
cd /d "%~dp0"
for /f "tokens=2" %%p in ('wmic process where "commandline like '%%deal_finder.main%%' and name like 'python%%'" get processid 2^>nul ^| findstr /r "[0-9]"') do taskkill /PID %%p /F >nul 2>&1
echo Deal-Finder gestoppt.
pause
