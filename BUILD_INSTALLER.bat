@echo off
REM One-command: freeze app + build CoProducer-Setup-1.0.0-beta.exe
setlocal
cd /d "%~dp0"
echo.
echo  CoProducer — building full Windows installer
echo  This takes several minutes (PyInstaller + Inno Setup).
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_installer.ps1" %*
if errorlevel 1 (
  echo.
  echo  BUILD FAILED — see logs\build_installer.log and logs\build_exe.log
  pause
  exit /b 1
)
echo.
echo  Done. Installer is in:
echo    packaging\output\
dir /b /o-d "%~dp0packaging\output\CoProducer-Setup-*.exe" 2>nul
echo.
pause
exit /b 0
