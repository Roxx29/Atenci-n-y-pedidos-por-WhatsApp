@echo off
rem Apaga web, tunel y sus vigilantes. "Apagar.cmd silencioso" no pausa (lo usa Encender.cmd).
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'vigilar\.cmd') -or ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'motor web') -or $_.Name -eq 'cloudflared.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
if /i "%~1"=="silencioso" exit /b 0
echo Sistema apagado.
timeout /t 4 >nul
