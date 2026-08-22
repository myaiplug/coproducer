@echo off
setlocal
cd /d "%~dp0"
title CoProducer Mobile Companion
echo.
echo  CoProducer Mobile Companion
echo  Phone must be on the same Wi-Fi as this PC.
echo.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" mobile\server.py
) else (
  py -3.11 mobile\server.py
)
pause
