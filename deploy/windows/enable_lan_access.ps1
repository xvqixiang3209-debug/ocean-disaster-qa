#Requires -RunAsAdministrator
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$ruleName = "ZhejiangTyphoonAgent-LAN-$Port"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

if ($existing) {
    Enable-NetFirewallRule -DisplayName $ruleName
    Write-Output "Enabled existing firewall rule: $ruleName"
}
else {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -RemoteAddress LocalSubnet `
        -Profile Domain,Private | Out-Null
    Write-Output "Created LAN-only firewall rule: $ruleName"
}

Write-Output "Restart the application with APP_HOST=0.0.0.0 before testing from another computer."
