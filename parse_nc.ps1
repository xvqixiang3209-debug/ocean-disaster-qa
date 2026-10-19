param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$NcArguments
)

$ErrorActionPreference = 'Stop'
$ncApp = $PSScriptRoot
$ncPython = Join-Path (Split-Path $ncApp -Parent) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ncPython -PathType Leaf)) {
    throw 'Please run install_phase1.ps1 first, or use: python tools/parse_nc.py --help'
}
if (-not $NcArguments -or $NcArguments.Count -eq 0) {
    $NcArguments = @('--help')
}
$env:PYTHONIOENCODING = 'utf-8'
Push-Location -LiteralPath $ncApp
try {
    & $ncPython (Join-Path $ncApp 'tools\parse_nc.py') @NcArguments
    $ncExit = $LASTEXITCODE
}
finally { Pop-Location }
exit $ncExit
