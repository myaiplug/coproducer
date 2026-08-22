@echo off
setlocal EnableExtensions
title CoProducer Core Analyzer v3.1.0
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "CLI=%ROOT%\app\nodaw_cli.py"
set "PYTHON=python"

REM Prefer explicit Python 3.11 via py launcher or PATH (locked target runtime)
where py >nul 2>nul && (
  for /f "delims=" %%p in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%p"
)

if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if exist "%ROOT%\runtime\python\python.exe" set "PYTHON=%ROOT%\runtime\python\python.exe"
if exist "%ROOT%\runtime\ffmpeg\bin\ffmpeg.exe" set "PATH=%ROOT%\runtime\ffmpeg\bin;%PATH%"

if /I "%PYTHON%"=="python" (
    py -3.11 -c "import sys; print(sys.version)" >nul 2>nul || (
        where python >nul 2>nul || (
            echo [ERROR] Python 3.11 is required (locked runtime).
            echo Install Python 3.11 from https://www.python.org/downloads/release/python-3119/
            echo or use: winget install --id Python.Python.3.11 --exact
            echo Then re-run the installer or this launcher.
            echo See docs\INSTALLATION.md for details.
            exit /b 1
        )
    )
) else if not exist "%PYTHON%" (
    echo [ERROR] Selected Python runtime not found: %PYTHON%
    exit /b 1
)
where ffmpeg >nul 2>nul || (
    echo [ERROR] FFmpeg is required and must be on PATH.
    echo See docs\INSTALLATION.md.
    exit /b 1
)
where ffprobe >nul 2>nul || (
    echo [ERROR] FFprobe is required and must be on PATH.
    echo See docs\INSTALLATION.md.
    exit /b 1
)
if not exist "%CLI%" (
    echo [ERROR] Missing application entry point: %CLI%
    exit /b 1
)

if not "%~1"=="" (
    "%PYTHON%" "%CLI%" --root "%ROOT%" %*
    exit /b %errorlevel%
)

:menu
cls
echo ===============================================================
echo       NoDAW Audio Quality Analyzer PRO v3.0
echo ===============================================================
echo.
echo  1. Single-file analysis
echo  2. Reference comparison
echo  3. Folder / batch analysis
echo  4. Album consistency analysis
echo  5. Codec analysis and previews
echo  6. Streaming readiness and previews
echo  7. Repair recommendations and repair script
echo  8. Project history dashboard
echo  9. Complete analysis
echo 10. Export current reports
echo 11. Dependency diagnostics
echo  0. Exit
echo.
set "MODE="
set /p "CHOICE=Choose option: "
if "%CHOICE%"=="1" set "MODE=analyze"
if "%CHOICE%"=="2" set "MODE=reference"
if "%CHOICE%"=="3" set "MODE=batch"
if "%CHOICE%"=="4" set "MODE=album"
if "%CHOICE%"=="5" set "MODE=codecs"
if "%CHOICE%"=="6" set "MODE=streaming"
if "%CHOICE%"=="7" set "MODE=fixes"
if "%CHOICE%"=="8" set "MODE=history"
if "%CHOICE%"=="9" set "MODE=all"
if "%CHOICE%"=="10" set "MODE=export"
if "%CHOICE%"=="11" set "MODE=doctor"
if "%CHOICE%"=="0" exit /b 0
if not defined MODE (
    echo Invalid choice.
    pause
    goto menu
)
"%PYTHON%" "%CLI%" --root "%ROOT%" --mode "%MODE%"
set "RESULT=%errorlevel%"
echo.
if not "%RESULT%"=="0" echo Analysis failed with exit code %RESULT%. See logs\nodaw.log.
if "%RESULT%"=="0" echo Operation completed. Reports are under reports\.
pause
goto menu
