@echo off
rem Reinicia un proceso si se cae y deja registro en despliegue\logs\<nombre>.log
rem Uso: despliegue\vigilar.cmd <nombre> <comando...>     (lo usa publicar.cmd)
setlocal
rem Antes de shift: shift tambien mueve %0
cd /d "%~dp0.."
set "NOMBRE=%~1"
shift
set "CMD="
:args
if "%~1"=="" goto listo
set "CMD=%CMD% %1"
shift
goto args
:listo
if not exist despliegue\logs mkdir despliegue\logs
set "LOG=despliegue\logs\%NOMBRE%.log"
rem Salida a archivo: sin UTF-8, Python falla al imprimir tildes/emojis.
set "PYTHONIOENCODING=utf-8"
title %NOMBRE% (vigilado)
:bucle
echo [%date% %time%] inicia:%CMD%>> "%LOG%"
%CMD% >> "%LOG%" 2>&1
echo [%date% %time%] se detuvo (codigo %errorlevel%); reinicio en 5 s>> "%LOG%"
echo %NOMBRE% se detuvo (codigo %errorlevel%); reinicio en 5 s. Ver %LOG%
timeout /t 5 /nobreak >nul
goto bucle
