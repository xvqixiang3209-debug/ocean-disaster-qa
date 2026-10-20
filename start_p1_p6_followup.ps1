param([int]$Port = 8771, [ValidateSet('on','shadow')][string]$Grounding = 'on')
$ErrorActionPreference = 'Stop'
$followupPreviousWarning = [Environment]::GetEnvironmentVariable('MARINE_WARNING_WORKER','Process')
try {
    # Start one isolated reviewed-code preview. Existing 8769/8770 services
    # continue their earlier loaded modules; no automatic data acquisition.
    $env:MARINE_WARNING_WORKER = 'false'
    & (Join-Path $PSScriptRoot 'start_p1_p6_preview.ps1') -Port $Port -Grounding $Grounding
} finally {
    [Environment]::SetEnvironmentVariable('MARINE_WARNING_WORKER',$followupPreviousWarning,'Process')
}
