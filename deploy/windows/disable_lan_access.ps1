#Requires -RunAsAdministrator
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$ruleName = "ZhejiangTyphoonAgent-LAN-$Port"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

if (-not $existing) {
    Write-Output "Firewall rule does not exist: $ruleName"
    exit 0
}

Disable-NetFirewallRule -DisplayName $ruleName
Write-Output "Disabled firewall rule: $ruleName"
