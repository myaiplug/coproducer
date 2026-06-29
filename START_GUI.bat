@echo off
setlocal
title CoProducer - AI Production Assistant
cd /d "%~dp0"

set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

where %PYTHON% >nul 2>nul || (
    echo [ERROR] Python not found. Run packaging\install.ps1 first.
    pause
    exit /b 1
)

echo Launching CoProducer - AI Production Assistant (desktop)
%PYTHON% CoProducerDesktop.py %*
pause