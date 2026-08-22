# Build frozen CoProducer Desktop (PyInstaller onedir)
# Output: packaging/dist/CoProducer/
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Pack = Join-Path $Root "packaging"
$Dist = Join-Path $Pack "dist"
$Work = Join-Path $Pack "build"
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "build_exe.log"
$AppVersion = "1.0.0-beta"

function Log([string]$m, [string]$lvl = "INFO") {
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $lvl, $m
    Write-Host $line
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Log "=== CoProducer freeze build starting ==="
Log "Root: $Root"
Log "Version: $AppVersion"
Set-Location $Root

# Python 3.11
$py = $null
try { $py = & py -3.11 -c "import sys; print(sys.executable)" 2>$null } catch {}
if (-not $py) { throw "Python 3.11 required (py -3.11). Install from python.org" }
Log "Python: $py"

# Ensure ALL runtime deps + PyInstaller (one smooth install path)
Log "Installing runtime dependencies (requirements.txt + packaging tools)..."
& $py -m pip install --upgrade pip setuptools wheel 2>&1 | ForEach-Object { Log "$_" }
& $py -m pip install -r (Join-Path $Root "requirements.txt") 2>&1 | ForEach-Object { Log "$_" }
& $py -m pip install "PySide6>=6.6.0,<7.0.0" "pyinstaller>=6.0" "pedalboard>=0.9.0" 2>&1 | ForEach-Object { Log "$_" }

# Verify critical imports before freeze
Log "Verifying critical modules..."
$env:PYTHONPATH = (Join-Path $Root "app")
& $py -c "import PySide6,numpy,scipy,librosa,soundfile,sounddevice,pyloudnorm,mutagen,pedalboard,nodaw; from nodaw.audio.pcm_player import HiFiPlayer; print('imports OK')" 2>&1 | ForEach-Object { Log "$_" }
if ($LASTEXITCODE -ne 0) {
    throw "Critical module import failed - fix deps before freeze"
}

# Clean previous
if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }
if (Test-Path $Work) { Remove-Item $Work -Recurse -Force }
New-Item -ItemType Directory -Path $Dist -Force | Out-Null
New-Item -ItemType Directory -Path $Work -Force | Out-Null

$spec = Join-Path $Pack "CoProducer.spec"
Log "Running PyInstaller: $spec"
$piArgs = "-3.11 -m PyInstaller `"$spec`" --noconfirm --clean --distpath `"$Dist`" --workpath `"$Work`""
cmd /c "py $piArgs" 2>&1 | ForEach-Object { Log ("$_") }

$appDir = Join-Path $Dist "CoProducer"
$exe = Join-Path $appDir "CoProducer.exe"
if (-not (Test-Path $exe)) {
    $alt = Get-ChildItem $Dist -Recurse -Filter "CoProducer.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($alt) {
        $appDir = $alt.Directory.FullName
        $exe = $alt.FullName
    }
}
if (-not (Test-Path $exe)) { throw "Freeze failed - CoProducer.exe not found under $Dist" }
Log "Frozen app: $exe"

# Writable folders beside exe
foreach ($sub in @(
    "reports","reports\html","reports\json","reports\txt","reports\csv","reports\history",
    "exports","exports\repairs","exports\previews","exports\eq_preview","exports\converts",
    "exports\trims","exports\spectral","exports\ab_playback_cache",
    "config","logs","input","input\song","input\reference","input\batch","input\album","docs"
)) {
    $p = Join-Path $appDir $sub
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

# Seed settings
$settingsSrc = Join-Path $Root "config\settings.json"
$settingsDst = Join-Path $appDir "config\settings.json"
if ((Test-Path $settingsSrc) -and -not (Test-Path $settingsDst)) {
    Copy-Item $settingsSrc $settingsDst -Force
}

# Bundle FFmpeg essentials into runtime/ffmpeg/bin (required for smooth convert/repair)
$ffBin = Join-Path $appDir "runtime\ffmpeg\bin"
$needFf = -not (Test-Path (Join-Path $ffBin "ffmpeg.exe")) -or -not (Test-Path (Join-Path $ffBin "ffprobe.exe"))
if ($needFf) {
    Log "Bundling FFmpeg essentials into app runtime..."
    New-Item -ItemType Directory -Path $ffBin -Force | Out-Null
    $sysFfCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $sysFpCmd = Get-Command ffprobe -ErrorAction SilentlyContinue
    $sysFf = if ($sysFfCmd) { $sysFfCmd.Source } else { $null }
    $sysFp = if ($sysFpCmd) { $sysFpCmd.Source } else { $null }
    if ($sysFf -and $sysFp) {
        Copy-Item $sysFf (Join-Path $ffBin "ffmpeg.exe") -Force
        Copy-Item $sysFp (Join-Path $ffBin "ffprobe.exe") -Force
        $sysDir = Split-Path $sysFf -Parent
        Get-ChildItem $sysDir -Filter "*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item $_.FullName $ffBin -Force -ErrorAction SilentlyContinue
        }
        Log "Copied system FFmpeg from $sysDir"
    } else {
        Log "System FFmpeg not found - downloading essentials build..." "WARN"
        $zipUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        $zipPath = Join-Path $env:TEMP "ffmpeg-essentials.zip"
        $extract = Join-Path $env:TEMP "ffmpeg-essentials-extract"
        try {
            Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
            if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
            Expand-Archive -Path $zipPath -DestinationPath $extract -Force
            $binSrc = Get-ChildItem $extract -Recurse -Directory -Filter "bin" | Select-Object -First 1
            if ($binSrc) {
                Copy-Item (Join-Path $binSrc.FullName "*") $ffBin -Force -Recurse
                Log "Downloaded FFmpeg into $ffBin"
            } else {
                Log "Could not locate bin/ in FFmpeg zip" "WARN"
            }
        } catch {
            Log "FFmpeg download failed: $_" "WARN"
        }
    }
} else {
    Log "FFmpeg already present at $ffBin"
}

if (-not (Test-Path (Join-Path $ffBin "ffmpeg.exe"))) {
    Log "WARNING: FFmpeg missing from bundle - convert/repair will fail for end users" "WARN"
} else {
    Log "FFmpeg OK: $(Join-Path $ffBin 'ffmpeg.exe')"
}

# Extra docs if PyInstaller datas missed them
foreach ($doc in @("USER_GUIDE.md","INSTALLATION.md","BETA_TESTER_GUIDE.md","KNOWN_ISSUES.md")) {
    $src = Join-Path $Root "docs\$doc"
    $dst = Join-Path $appDir "docs\$doc"
    if ((Test-Path $src) -and -not (Test-Path $dst)) {
        Copy-Item $src $dst -Force
    }
}

# README for install tree
$readmeSrc = Join-Path $Pack "installer_readme.txt"
if (Test-Path $readmeSrc) {
    Copy-Item $readmeSrc (Join-Path $appDir "README_INSTALL.txt") -Force
}

# Version stamp
$verFile = Join-Path $appDir "VERSION.txt"
Set-Content -Path $verFile -Value "CoProducer $AppVersion`nBuilt $(Get-Date -Format o)`nIncludes: engine, Pedalboard, FFmpeg, PySide6 GUI" -Encoding UTF8

$sizeMB = [math]::Round(((Get-ChildItem $appDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Log "=== Freeze complete ==="
Log "App folder: $appDir ($sizeMB MB)"
Write-Host ""
Write-Host "Frozen CoProducer ready:" -ForegroundColor Green
Write-Host "  $exe" -ForegroundColor Cyan
Write-Host "  Size: $sizeMB MB" -ForegroundColor DarkGray
Write-Host "Next: packaging\build_installer.ps1" -ForegroundColor DarkGray
