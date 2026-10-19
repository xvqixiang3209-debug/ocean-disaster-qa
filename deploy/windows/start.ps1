$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$environmentFile = Join-Path $projectRoot ".env"
$databaseFile = Join-Path $projectRoot "typhoon\data\typhoon_history.sqlite3"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Virtual environment not found. Run deploy\windows\install.ps1 first."
}
if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    throw ".env not found. Copy deploy\windows\windows.env.example to .env and edit it first."
}
if (-not (Test-Path -LiteralPath $databaseFile -PathType Leaf)) {
    Write-Warning "Default SQLite database was not found. Verify TYPHOON_SQLITE_PATH in .env."
}

Set-Location -LiteralPath $projectRoot
& $venvPython production_server.py
exit $LASTEXITCODE
