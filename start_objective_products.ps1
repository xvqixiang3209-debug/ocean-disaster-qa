param([int]$Port = 8773)
$ErrorActionPreference = 'Stop'
# Only L2/L5 wave and wind are polled; the broad legacy worker remains off.
$previousObjectiveSync = $env:MARINE_OBJECTIVE_AUTO_SYNC
try {
    $env:MARINE_OBJECTIVE_AUTO_SYNC = 'true'
    & (Join-Path $PSScriptRoot 'start_p1_p6_followup.ps1') -Port $Port -Grounding on
} finally {
    $env:MARINE_OBJECTIVE_AUTO_SYNC = $previousObjectiveSync
}
