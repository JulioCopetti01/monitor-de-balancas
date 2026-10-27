@echo off
rem Inicia o monitor de balancas e reinicia automaticamente se ele parar.
rem Uso: iniciar_monitor.bat "C:\caminho\python.exe"
rem Executado pela tarefa agendada criada por servico_monitor.ps1.

setlocal
cd /d "%~dp0"

set "PYTHON=%~1"
if "%PYTHON%"=="" set "PYTHON=py"

set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

if not exist logs mkdir logs

:loop
echo [%date% %time%] Iniciando monitor >> logs\monitor.log
"%PYTHON%" "%~dp0app.py" >> logs\monitor.log 2>&1
echo [%date% %time%] Monitor parou (codigo %errorlevel%), reiniciando em 10s >> logs\monitor.log
rem ping no lugar de "timeout", que falha quando roda sem console (tarefa agendada)
ping -n 11 127.0.0.1 > nul
goto loop
