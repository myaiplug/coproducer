# Full CoProducer Windows installer pipeline
# 1) Freeze app with PyInstaller (build_exe.ps1)
# 2) Compile Inno Setup → packaging/output/CoProducer-Setup-1.0.0-beta.exe
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1 -SkipFreeze
#   BUILD_INSTALLER.bat   (from project root)

param(
    [switch]$SkipFreeze
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Pack = Join-Path $Root "packaging"
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "build_installer.log"
$AppVersion = "1.0.0-beta"

function Log([string]$m, [string]$lvl = "INFO") {
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $lvl, $m
    Write-Host $line
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Log "=== CoProducer installer pipeline ($AppVersion) ==="
Set-Location $Root

if (-not $SkipFreeze) {
    Log "Step 1/2: Freezing application (this can take several minutes)..."
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Pack "build_exe.ps1")
    $freezeExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($freezeExit -ne 0) { throw "build_exe.ps1 failed with exit $freezeExit" }
} else {
    Log "Step 1/2: Skipping freeze (using existing packaging\dist\CoProducer)"
}

$appDir = Join-Path $Pack "dist\CoProducer"
$exe = Join-Path $appDir "CoProducer.exe"
if (-not (Test-Path $exe)) {
    $found = Get-ChildItem (Join-Path $Pack "dist") -Recurse -Filter "CoProducer.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) {
        $appDir = $found.Directory.FullName
        $exe = $found.FullName
    }
}
if (-not (Test-Path $exe)) { throw "Missing frozen app: $exe - run build_exe.ps1 first" }
Log "App: $exe"

# Require FFmpeg in bundle for "runs smoothly" guarantee
$ff = Join-Path $appDir "runtime\ffmpeg\bin\ffmpeg.exe"
$fp = Join-Path $appDir "runtime\ffmpeg\bin\ffprobe.exe"
if (-not (Test-Path $ff) -or -not (Test-Path $fp)) {
    Log "FFmpeg missing from freeze - attempting emergency copy..." "WARN"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Pack "build_exe.ps1")  # re-run only if skip? better: inline
    # Inline bundle (same as build_exe)
    $ffBin = Join-Path $appDir "runtime\ffmpeg\bin"
    New-Item -ItemType Directory -Path $ffBin -Force | Out-Null
    $sysFf = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
    $sysFp = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
    if ($sysFf -and $sysFp) {
        Copy-Item $sysFf (Join-Path $ffBin "ffmpeg.exe") -Force
        Copy-Item $sysFp (Join-Path $ffBin "ffprobe.exe") -Force
    }
}
if (-not (Test-Path $ff)) {
    throw "FFmpeg not bundled - convert/repair will not work. Install ffmpeg on PATH and re-run build_exe.ps1"
}
Log "FFmpeg: $ff"

# Inno Setup compiler
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    throw "Inno Setup 6 not found. Install from https://jrsoftware.org/isinfo.php then re-run."
}
Log "ISCC: $iscc"

$iss = Join-Path $Pack "CoProducer.iss"
$outDir = Join-Path $Pack "output"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

Log "Step 2/2: Compiling Setup.exe..."
Push-Location $Pack
try {
    & $iscc $iss 2>&1 | ForEach-Object { Log "$_" }
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

$setup = Get-ChildItem $outDir -Filter "CoProducer-Setup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setup) { throw "Installer EXE not produced in $outDir" }

Log "=== SUCCESS ==="
Log "Installer: $($setup.FullName)"
Log "Size: $([math]::Round($setup.Length / 1MB, 1)) MB"
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  CoProducer installer ready" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  $($setup.FullName)" -ForegroundColor Cyan
Write-Host "  $([math]::Round($setup.Length / 1MB, 1)) MB" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Give testers this one file. It installs:" -ForegroundColor White
Write-Host "  - CoProducer desktop (no Python required)"
Write-Host "  - All audio libs (Pedalboard, librosa, scipy, ...)"
Write-Host "  - Bundled FFmpeg/FFprobe"
Write-Host "  - Desktop + Start Menu shortcuts"
Write-Host "  - Uninstall via Windows Apps"
Write-Host ""
