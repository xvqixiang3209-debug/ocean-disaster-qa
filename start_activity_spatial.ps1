$ErrorActionPreference = 'Stop'
# The latest local workbench has one stable entry point.
$activityPort = 8769
$activityUrl = 'http://127.0.0.1:' + $activityPort
$activityListeners = @(Get-NetTCPConnection -LocalPort $activityPort -State Listen -ErrorAction SilentlyContinue)
if ($activityListeners.Count -gt 0) {
    $activityStatus = $null
    try {
        $activityStatus = Invoke-RestMethod -Uri ($activityUrl + '/api/spatial/status') -TimeoutSec 8
    } catch { }
    if ($activityStatus.state) {
            $activityBuild = Invoke-RestMethod -Uri ($activityUrl + '/api/assistant/status') -TimeoutSec 8
            $activityExpected = ((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'VERSION.txt') -Encoding UTF8 | Where-Object { $_ -like 'Version: *' }) -replace '^Version: ', '').Trim()
            if ($activityBuild.version -ne $activityExpected) {
                throw ('The running service is ' + $activityBuild.version + ', but this folder is ' + $activityExpected + '. Restart the old service before opening the latest workbench.')
            }
            Write-Output ('Activity and spatial preview is running: ' + $activityUrl + '/')
            return
    }
    throw ('Port ' + $activityPort + ' is occupied by a service without the spatial status endpoint.')
}
$activityOldPort = $env:APP_PORT
$activityOldHost = $env:APP_HOST
$activityOldWorker = $env:MARINE_FORECAST_WORKER
try {
    $env:APP_HOST = '127.0.0.1'
    $env:APP_PORT = [string]$activityPort
    # Question-driven acquisition still fetches required NC files on demand.
    $env:MARINE_FORECAST_WORKER = 'false'
    & (Join-Path $PSScriptRoot 'start_phase1.ps1')
} finally {
    $env:APP_PORT = $activityOldPort
    $env:APP_HOST = $activityOldHost
    $env:MARINE_FORECAST_WORKER = $activityOldWorker
}
