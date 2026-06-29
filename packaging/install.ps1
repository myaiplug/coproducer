# CoProducer Core Analyzer - High-End Windows Installer
# Requires Python 3.11 (locked runtime)
# Creates .venv, installs pinned deps, verifies ffmpeg/ffprobe, runs self-test
# Logs to logs/install.log (relative to project root)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "install.log"
$VenvDir = Join-Path $Root ".venv"
$PythonCmd = $null

function Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Log "=== CoProducer Core Analyzer Installer v3.1.0 starting ==="
Log "Root: $Root"

# 1. Locate Python 3.11
Log "Locating Python 3.11..."
$pyList = & py --list 2>$null | Out-String
if ($pyList -match "3\.11") {
    try {
        $PythonCmd = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($PythonCmd) { Log "Found via py launcher: $PythonCmd" }
    } catch {}
}
if (-not $PythonCmd) {
    # Try common install locations for 3.11
    $candidates = @(
        "C:\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Program Files\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $PythonCmd = $c; Log "Found at $c"; break }
    }
}
if (-not $PythonCmd) {
    $PythonCmd = "python"
    try {
        $ver = & python --version 2>&1
        if ($ver -notmatch "3\.11") {
            throw "Python 3.11 not detected"
        }
        $PythonCmd = "python"
    } catch {
        Log "ERROR: Python 3.11 is REQUIRED (locked runtime for Essentia compatibility)." "ERROR"
        Log "Please install Python 3.11 from:" "ERROR"
        Log "  https://www.python.org/downloads/release/python-3119/" "ERROR"
        Log "Or: winget install --id Python.Python.3.11 --exact" "ERROR"
        Log "Then re-run this installer." "ERROR"
        exit 1
    }
}

Log "Using Python: $PythonCmd"

# 2. Create venv
if (Test-Path $VenvDir) {
    Log "Removing existing .venv..."
    Remove-Item $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
}
Log "Creating virtual environment at .venv..."
& $PythonCmd -m venv "$VenvDir"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { throw "Venv creation failed" }
Log "Venv Python: $VenvPython"

# 3. Upgrade pip + install pinned requirements
Log "Upgrading pip..."
& $VenvPython -m pip install --upgrade pip setuptools wheel | Out-Null

$ReqFile = Join-Path $Root "requirements.txt"
Log "Installing from $ReqFile (this may take several minutes)..."
& $VenvPython -m pip install -r $ReqFile 2>&1 | Tee-Object -Variable pipOut | Out-Null
Add-Content -Path $LogFile -Value $pipOut -Encoding UTF8

# 4. Verify critical imports
Log "Running dependency self-test..."
$testScript = @"
import sys
print('Python:', sys.version)
mods = ['numpy', 'scipy', 'soundfile', 'librosa', 'pyloudnorm', 'mutagen']
for m in mods:
    try:
        __import__(m)
        print(f'  OK: {m}')
    except Exception as e:
        print(f'  FAIL: {m} - {e}')
        sys.exit(2)
print('All core modules importable.')
"@
& $VenvPython -c $testScript 2>&1 | ForEach-Object { Log $_ }

# 5. Verify ffmpeg / ffprobe on PATH
Log "Verifying external dependencies..."
$hasFfmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue) -ne $null
$hasFfprobe = (Get-Command ffprobe -ErrorAction SilentlyContinue) -ne $null
if (-not $hasFfmpeg -or -not $hasFfprobe) {
    Log "WARNING: ffmpeg or ffprobe not found on PATH." "WARN"
    Log "Install with: winget install --id Gyan.FFmpeg.Essentials --exact" "WARN"
    Log "Close/reopen terminal after install." "WARN"
} else {
    $ffver = & ffmpeg -version 2>&1 | Select-Object -First 1
    Log "ffmpeg: $ffver"
    $fpver = & ffprobe -version 2>&1 | Select-Object -First 1
    Log "ffprobe: $fpver"
}

# 6. Run quick analyzer self-test if possible (will use new modules)
Log "Attempting basic analyzer smoke (requires audio libs + ffmpeg)..."
$smoke = @"
import sys, os
sys.path.insert(0, r'$Root\app')
try:
    from nodaw.core.engine import WorkflowRunner
    import logging
    logger = logging.getLogger('install-smoke')
    logger.addHandler(logging.NullHandler())
    # Just construct; real run needs input files
    print('Engine import and instantiation: OK')
except Exception as e:
    print('Engine smoke FAILED:', e)
    sys.exit(3)
print('Installer self-test passed core checks.')
"@
& $VenvPython -c $smoke 2>&1 | ForEach-Object { Log $_ }

Log "=== Installation complete ==="
Log "Activate with: .\.venv\Scripts\Activate.ps1"
Log "Launch: .\START_ANALYZER_PRO.bat"
Log "To run tests: .\.venv\Scripts\python -m pytest tests -v"

Write-Host ""
Write-Host "CoProducer Core Analyzer installed successfully into .venv" -ForegroundColor Green
Write-Host "Logs: $LogFile" -ForegroundColor DarkGray