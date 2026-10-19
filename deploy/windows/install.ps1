param(
    [string]$PythonCommand = "",
    [switch]$Offline,
    [string]$Wheelhouse = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$venvDirectory = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
$requirements = Join-Path $projectRoot "requirements.txt"
$environmentFile = Join-Path $projectRoot ".env"
$environmentExample = Join-Path $PSScriptRoot "windows.env.example"

Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if (-not [string]::IsNullOrWhiteSpace($PythonCommand)) {
        & $PythonCommand -m venv $venvDirectory
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -m venv $venvDirectory
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $venvDirectory
    }
    else {
        throw "Python was not found. Install Python 3.11 x64 or pass -PythonCommand with python.exe."
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create Python virtual environment."
    }
}

$pythonVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to read the virtual environment Python version."
}
if ($Offline -and $pythonVersion.Trim() -ne "3.11") {
    throw "The included offline wheelhouse requires Python 3.11 x64; current virtual environment is Python $pythonVersion."
}

if ($Offline) {
    if ([string]::IsNullOrWhiteSpace($Wheelhouse)) {
        throw "Offline installation requires -Wheelhouse with an existing directory."
    }
    $resolvedWheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).Path
    & $venvPython -m pip install --no-index --find-links $resolvedWheelhouse -r $requirements
}
else {
    & $venvPython -m pip install -r $requirements
}
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

if (-not (Test-Path -LiteralPath $environmentFile)) {
    Copy-Item -LiteralPath $environmentExample -Destination $environmentFile
    Write-Warning "Created .env from the example. Edit paths and inject the API key before starting."
}

Write-Output "Installation completed: $projectRoot"
Write-Output "Next: edit $environmentFile, then run deploy\windows\start.ps1"
