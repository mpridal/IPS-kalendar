@echo off
rem Zastavi program hodin, zhasne displej a vypne automaticke spousteni (do dalsiho "Spustit displej").
cd /d "%~dp0"
> stav.txt echo zastavit
echo Zastavuji displej...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Disable-ScheduledTask 'Displej hodiny' | Out-Null; $najdi = { Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*hodiny.py*' } }; $konec = (Get-Date).AddSeconds(15); while ((Get-Date) -lt $konec -and (& $najdi)) { Start-Sleep 1 }; & $najdi | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Stop-ScheduledTask 'Displej hodiny'"
echo Hotovo. Znovu zapnes pres "Spustit displej".
timeout /t 4 >nul
