@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Instant handoff — prefer VBS so the bat window never lingers.
if not exist "logs" mkdir logs

if exist "%~dp0CoProducer.vbs" (
  start "" /B wscript //nologo "%~dp0CoProducer.vbs"
  exit /b 0
)

set "PYTHONPATH=%~dp0app"

REM Fallback: pythonw detached
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" /B "%~dp0.venv\Scripts\pythonw.exe" -u "%~dp0CoProducerDesktop.py" %*
  exit /b 0
)

if exist "%~dp0.venv\Scripts\python.exe" (
  start "" /B "%~dp0.venv\Scripts\python.exe" -u "%~dp0CoProducerDesktop.py" %*
  exit /b 0
)

py -3.11 -c "import PySide6" >nul 2>nul
if not errorlevel 1 (
  start "" /B py -3.11 -u "%~dp0CoProducerDesktop.py" %*
  exit /b 0
)

REM Only show a console if we truly cannot start
echo [ERROR] No Python with PySide6 found.
echo Install: py -3.11 -m pip install -r requirements.txt
pause
exit /b 1
