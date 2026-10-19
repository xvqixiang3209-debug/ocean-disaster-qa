$ErrorActionPreference = 'Stop'
$phaseApp = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$phaseServer = Join-Path $phaseApp 'production_server.py'
$phasePython = Join-Path (Split-Path $phaseApp -Parent) '.venv\Scripts\python.exe'
$phasePort = if ($env:APP_PORT) { [int]$env:APP_PORT } else { 8767 }
$phaseListeners = @(Get-NetTCPConnection -LocalPort $phasePort -State Listen -ErrorAction SilentlyContinue)
if ($phaseListeners.Count -eq 0) { Write-Output 'Phase-one service is not listening.'; return }
$phaseProcessIds = @($phaseListeners | Select-Object -ExpandProperty OwningProcess -Unique)
if ($phaseProcessIds.Count -ne 1) { throw 'Unexpected listener count. No process was stopped.' }
$phaseProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $phaseProcessIds[0])
if (-not $phaseProcess -or -not $phaseProcess.CommandLine.Contains($phaseServer) -or -not $phaseProcess.CommandLine.Contains($phasePython)) {
    throw 'Port belongs to a different or unverified process. No process was stopped.'
}
Write-Output ('Stopping verified phase-one process ' + $phaseProcess.ProcessId)
Stop-Process -Id $phaseProcess.ProcessId -ErrorAction Stop
Write-Output 'Stopped. No files or databases were removed.'
