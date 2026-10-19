$ErrorActionPreference = 'Stop'
$phaseApp = $PSScriptRoot
$phaseVenv = Join-Path (Split-Path $phaseApp -Parent) '.venv'
if (-not (Test-Path -LiteralPath $phaseVenv)) {
    py -3 -m venv $phaseVenv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
$phasePython = Join-Path $phaseVenv 'Scripts\python.exe'
& $phasePython -m pip install -r (Join-Path $phaseApp 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Output 'Installed. Run start_phase1.ps1 next.'
