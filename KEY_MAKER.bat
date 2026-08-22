@echo off
REM CoProducer Activation Key Maker — owner tool (no console linger)
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0app"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" /B "%~dp0.venv\Scripts\pythonw.exe" -u "%~dp0tools\KeyMaker.py"
  exit /b 0
)
if exist "%~dp0.venv\Scripts\python.exe" (
  start "" /B "%~dp0.venv\Scripts\python.exe" -u "%~dp0tools\KeyMaker.py"
  exit /b 0
)
start "" /B py -3.11 -u "%~dp0tools\KeyMaker.py"
exit /b 0
