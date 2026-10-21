@echo off
rem Spusti program hodin na displeji (a zapne automaticke spousteni po prihlaseni).
cd /d "%~dp0"
> stav.txt echo bezi
powershell -NoProfile -ExecutionPolicy Bypass -Command "Enable-ScheduledTask 'Displej hodiny' | Out-Null; Start-ScheduledTask 'Displej hodiny'"
echo Displej se spousti, obraz se objevi asi za 10 sekund.
timeout /t 4 >nul
