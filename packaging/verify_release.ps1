$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

Push-Location $Root
try {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Automated tests failed.' }
    & (Join-Path $Root 'START_ANALYZER_PRO.bat') --mode doctor --no-previews
    if ($LASTEXITCODE -ne 0) { throw 'Dependency diagnostics failed.' }
    $customerFiles = Get-ChildItem -LiteralPath $Root -File -Recurse |
        Where-Object { $_.FullName -notmatch '\\development\\|\\tests\\|\\dist\\|\\packaging\\|\\__pycache__\\' }
    $legacyPattern = @('Phase' + ' 1','Phase' + ' 2','Phase' + ' 3','1' + '.1.0','2' + '.0.0') -join '|'
    $legacy = $customerFiles | Select-String -Pattern $legacyPattern -CaseSensitive
    if ($legacy) { throw 'Legacy or internal branding remains in customer files.' }
    & (Join-Path $PSScriptRoot 'build_portable.ps1')
} finally {
    Pop-Location
}
