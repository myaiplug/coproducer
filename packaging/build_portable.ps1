param(
    [string]$PythonRuntime,
    [string]$FFmpegRuntime
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dist = Join-Path $Root 'dist'
$Name = 'NoDAW_Audio_Quality_Analyzer_PRO_v3.0.0'
$Target = Join-Path $Dist $Name
$Archive = Join-Path $Dist ($Name + '_portable.zip')

if (Test-Path -LiteralPath $Target) {
    $resolved = (Resolve-Path -LiteralPath $Target).Path
    if (-not $resolved.StartsWith($Dist, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe build target: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
New-Item -ItemType Directory -Path $Target -Force | Out-Null

$directories = 'app','assets','config','docs'
$files = 'START_ANALYZER_PRO.bat','README.md','CHANGELOG.md','RELEASE_NOTES.md','LICENSE.txt','requirements.txt'
foreach ($directory in $directories) {
    Copy-Item -LiteralPath (Join-Path $Root $directory) -Destination $Target -Recurse -Force
}
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $Root $file) -Destination $Target -Force
}
foreach ($inputName in 'song','reference','batch','album') {
    $inputTarget = Join-Path $Target ('input\' + $inputName)
    New-Item -ItemType Directory -Path $inputTarget -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $Root ('input\' + $inputName + '\README.txt')) -Destination $inputTarget -Force
}
foreach ($directory in 'reports','exports','logs') {
    New-Item -ItemType Directory -Path (Join-Path $Target $directory) -Force | Out-Null
}

Get-ChildItem -LiteralPath $Target -Directory -Filter '__pycache__' -Recurse |
    ForEach-Object {
        if (-not $_.FullName.StartsWith($Target, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe cache path: $($_.FullName)"
        }
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
Get-ChildItem -LiteralPath $Target -File -Filter '*.pyc' -Recurse |
    Remove-Item -Force

if ($PythonRuntime) {
    Copy-Item -LiteralPath $PythonRuntime -Destination (Join-Path $Target 'runtime\python') -Recurse -Force
}
if ($FFmpegRuntime) {
    Copy-Item -LiteralPath $FFmpegRuntime -Destination (Join-Path $Target 'runtime\ffmpeg') -Recurse -Force
}

Compress-Archive -LiteralPath $Target -DestinationPath $Archive -CompressionLevel Optimal
if (-not $Target.StartsWith($Dist, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe post-build cleanup target: $Target"
}
Remove-Item -LiteralPath $Target -Recurse -Force
Write-Output $Archive
