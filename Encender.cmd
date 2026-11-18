@echo off
rem Doble clic: enciende todo el sistema (web, panel y bot) y abre la pagina.
title Encender sistema
cd /d "%~dp0"
echo Encendiendo el sistema...
rem Si ya estaba encendido, se apaga primero para no duplicar.
call "%~dp0Apagar.cmd" silencioso
call despliegue\publicar.cmd
if errorlevel 1 (echo. & echo No se pudo encender. Revise el mensaje de arriba. & pause & exit /b 1)

set /a N=0
:esperar
curl -s -o nul -w "" http://localhost:8080/ && goto listo
set /a N+=1
if %N% geq 30 (echo. & echo La web no responde. Ver despliegue\logs\web.log & pause & exit /b 1)
timeout /t 1 /nobreak >nul
goto esperar

:listo
echo.
echo Sistema encendido. Se reinicia solo si algo falla.
echo   Pagina:  https://restaurantes.nubiksoft.com
echo   Panel:   https://restaurantes.nubiksoft.com/panel
echo Para apagar: Apagar.cmd
start "" https://restaurantes.nubiksoft.com
timeout /t 8 >nul
