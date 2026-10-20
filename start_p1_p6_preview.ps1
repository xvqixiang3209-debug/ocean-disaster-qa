param([int]$Port = 8770, [ValidateSet('on','shadow')][string]$Grounding = 'on')
$ErrorActionPreference = 'Stop'
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw ('Port ' + $Port + ' is already in use. Open the existing preview or choose -Port another value.')
}
$phaseKeys = @('MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE','MARINE_CONNECTOR_V2',
               'MARINE_TYPED_GROUNDING','MARINE_RELATION_GUARD','APP_HOST','APP_PORT','MARINE_FORECAST_WORKER')
$phasePrevious = @{}
foreach ($phaseKey in $phaseKeys) { $phasePrevious[$phaseKey] = [Environment]::GetEnvironmentVariable($phaseKey,'Process') }
try {
    foreach ($phaseKey in @('MARINE_FACET_CONTRACT','MARINE_EVIDENCE_UNITS','MARINE_SCOPE_GATE','MARINE_CONNECTOR_V2')) {
        [Environment]::SetEnvironmentVariable($phaseKey,'true','Process')
    }
    $env:MARINE_TYPED_GROUNDING = $Grounding
    $env:MARINE_RELATION_GUARD = $Grounding
    $env:APP_HOST = '127.0.0.1'
    $env:APP_PORT = [string]$Port
    $env:MARINE_FORECAST_WORKER = 'false'
    & (Join-Path $PSScriptRoot 'start_phase1.ps1')
} finally {
    foreach ($phaseKey in $phaseKeys) { [Environment]::SetEnvironmentVariable($phaseKey,$phasePrevious[$phaseKey],'Process') }
}
