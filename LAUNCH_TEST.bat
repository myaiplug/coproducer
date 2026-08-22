@echo off
REM Instant handoff to silent VBS — do not leave a console visible.
setlocal
cd /d "%~dp0"
if exist "%~dp0CoProducer_test.vbs" (
  start "" /B wscript //nologo "%~dp0CoProducer_test.vbs"
  exit /b 0
)
if exist "%~dp0CoProducer.vbs" (
  set COPRODUCER_BETA_BYPASS=1
  start "" /B wscript //nologo "%~dp0CoProducer.vbs"
  exit /b 0
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  set COPRODUCER_BETA_BYPASS=1
  set PYTHONUNBUFFERED=1
  set PYTHONPATH=%~dp0app
  start "" /B "%~dp0.venv\Scripts\pythonw.exe" -u "%~dp0CoProducerDesktop.py"
  exit /b 0
)
exit /b 1
