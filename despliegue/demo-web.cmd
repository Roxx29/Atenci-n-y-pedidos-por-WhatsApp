@echo off
rem Demo de un clic: Ollama (11435) + web de pedidos + panel. Tunel publico si hay cloudflared.
rem Uso: doble clic, o  despliegue\demo-web.cmd [puerto]
setlocal
cd /d "%~dp0.."
set "PUERTO=%~1"
if "%PUERTO%"=="" set "PUERTO=8080"
set "SERVICIO=servicios\restaurante-pedidos"
set "CLOUDFLARED=F:\herramientas\cloudflared\cloudflared.exe"

set "ANTHROPIC_BASE_URL=http://127.0.0.1:11435"
if "%ANTHROPIC_API_KEY%"=="" set "ANTHROPIC_API_KEY=ollama"
set "LLM_MODEL=qwen3:8b"

rem Si el Ollama de demo ya responde, no abre otro.
curl -s -o nul http://127.0.0.1:11435/api/version || start "Ollama demo" /min cmd /c demo-ollama.cmd
start "Web pedidos" cmd /k python -m motor web %SERVICIO% %PUERTO%
start "Panel" python -m motor panel %SERVICIO%

if exist "%CLOUDFLARED%" (
  rem La URL https://xxxx.trycloudflare.com sale en esa ventana.
  start "Tunel publico" cmd /k "%CLOUDFLARED%" tunnel --url http://localhost:%PUERTO%
) else (
  echo Sin cloudflared: solo red local. Ver despliegue\demo-web.md
)
echo Local: http://localhost:%PUERTO%
endlocal
