@echo off
rem Web local (sin Cloudflare Tunnel: deshabilitado en este PC; para reactivarlo quita los "rem DESHABILITADO" de este archivo). Guia: despliegue\cloudflare.md
rem Uso: despliegue\publicar.cmd [puerto]     Necesita en .env (git lo ignora): TUNNEL_TOKEN, PANEL_PIN y DEEPSEEK_API_KEY (o Ollama).
setlocal
cd /d "%~dp0.."
set "PUERTO=%~1"
if "%PUERTO%"=="" set "PUERTO=8080"
set "SERVICIO=servicios\restaurante-pedidos"
set "CLOUDFLARED=F:\herramientas\cloudflared\cloudflared.exe"

rem DESHABILITADO (sin tunel en este PC): if not exist "%CLOUDFLARED%" (echo Falta %CLOUDFLARED% - ver despliegue\cloudflare.md & exit /b 1)
if not exist .env (echo Falta .env en la raiz - ver despliegue\cloudflare.md & exit /b 1)

rem Lee solo el token del .env (sin mostrarlo). cloudflared lo toma de TUNNEL_TOKEN: no queda en la linea de comandos.
rem DESHABILITADO (sin tunel en este PC): for /f "usebackq tokens=1,* delims==" %%a in (".env") do if "%%a"=="TUNNEL_TOKEN" set "TUNNEL_TOKEN=%%b"
rem DESHABILITADO (sin tunel en este PC): if "%TUNNEL_TOKEN%"=="" (echo Falta TUNNEL_TOKEN en .env & exit /b 1)

rem TUNEL=1: confiar en CF-Connecting-IP (IP real, bloqueo de PIN por cliente) y marcar cookies Secure.
rem PANEL_PUBLICO=1: /panel y /repartidor accesibles por internet (siempre con PIN).
rem DESHABILITADO (sin tunel en este PC): set "TUNEL=1"
set "PANEL_PUBLICO=1"

findstr /b /c:"DEEPSEEK_API_KEY=" .env >nul 2>nul
if not errorlevel 1 (
  set "ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic"
  set "LLM_MODEL=deepseek-v4-flash"
  set "LLM_RESPALDO_BASE_URL=http://127.0.0.1:11435"
  set "LLM_RESPALDO_MODEL=qwen3:8b"
) else (
  set "ANTHROPIC_BASE_URL=http://127.0.0.1:11435"
  if "%ANTHROPIC_API_KEY%"=="" set "ANTHROPIC_API_KEY=ollama"
  set "LLM_MODEL=qwen3:8b"
)

curl -s -o nul http://127.0.0.1:11435/api/version || start "Ollama demo" /min cmd /c demo-ollama.cmd
rem Vigilados: si se caen se reinician solos; registro en despliegue\logs\
start "Web pedidos" /min cmd /c despliegue\vigilar.cmd web python -m motor web %SERVICIO% %PUERTO%
rem DESHABILITADO (sin tunel en este PC): start "Tunel Cloudflare" /min cmd /c despliegue\vigilar.cmd tunel "%CLOUDFLARED%" tunnel --no-autoupdate run
echo Servidor en http://localhost:%PUERTO% (se reinicia solo). Para parar: cerrar las ventanas minimizadas.
endlocal
