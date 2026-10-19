$ErrorActionPreference = 'Stop'
$phaseApp = $PSScriptRoot
$phasePython = Join-Path (Split-Path $phaseApp -Parent) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $phasePython -PathType Leaf)) {
    throw 'Please run install_phase1.ps1 first.'
}
if (-not $env:APP_HOST) { $env:APP_HOST = '127.0.0.1' }
if (-not $env:APP_PORT) { $env:APP_PORT = '8767' }
if (-not $env:TYPHOON_REPORT_MODE) { $env:TYPHOON_REPORT_MODE = 'template' }
$phaseListeners = @(Get-NetTCPConnection -LocalPort ([int]$env:APP_PORT) -State Listen -ErrorAction SilentlyContinue)
if ($phaseListeners.Count -gt 0) {
    $phasePids = @($phaseListeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($phasePids.Count -eq 1) {
        $phaseProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $phasePids[0])
        if ($phaseProcess -and $phaseProcess.CommandLine.Contains((Join-Path $phaseApp 'production_server.py')) -and $phaseProcess.CommandLine.Contains($phasePython)) {
            try {
                $phaseHealth = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $env:APP_PORT + '/api/assistant/session') -TimeoutSec 5
                if ($phaseHealth.token) {
                    Write-Output ('Service is already running. Open: http://127.0.0.1:' + $env:APP_PORT + '/')
                    return
                }
            } catch { }
        }
    }
    throw ('Port ' + $env:APP_PORT + ' is already in use. If this is the phase-one service, open its URL instead of starting it twice.')
}
Write-Output ('Map and conversation: http://127.0.0.1:' + $env:APP_PORT + '/')
Write-Output ('Simple conversation: http://127.0.0.1:' + $env:APP_PORT + '/chat')
Write-Output ('Data management: http://127.0.0.1:' + $env:APP_PORT + '/knowledge.html')
Write-Output 'Keep this terminal running. Original service on 8766 is unchanged.'
Push-Location -LiteralPath $phaseApp
try { & $phasePython (Join-Path $phaseApp 'production_server.py') }
finally { Pop-Location }
