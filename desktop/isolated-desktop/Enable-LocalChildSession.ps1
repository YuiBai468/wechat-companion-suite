# Restore only the local service prerequisites for Windows child sessions.
# This script does not enable external Remote Desktop, change firewall rules, or handle credentials.
$ErrorActionPreference = 'Stop'
$setupRoot = $PSScriptRoot
$setupResult = Join-Path $setupRoot 'service-setup-result.json'
function Save-SetupResult($value) {
    $json = $value | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($setupResult, $json, [System.Text.UTF8Encoding]::new($false))
}
try {
    $setupIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $setupPrincipal = New-Object Security.Principal.WindowsPrincipal($setupIdentity)
    if (-not $setupPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Administrator authorization is required to start the disabled Windows session services.'
    }
    $terminalKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
    $denyBefore = (Get-ItemProperty -LiteralPath $terminalKey -Name fDenyTSConnections).fDenyTSConnections
    if ($denyBefore -ne 1) { throw 'External Remote Desktop is not in the expected disabled state. No service changes were made.' }
    $names = @('TermService','SessionEnv','UmRdpService')
    $before = @(foreach ($name in $names) {
        $svc = Get-Service -Name $name
        [ordered]@{name=$name;status=$svc.Status.ToString();startType=$svc.StartType.ToString()}
    })
    $backupPath = Join-Path $setupRoot 'service-state-before.json'
    if (-not (Test-Path -LiteralPath $backupPath)) {
        [System.IO.File]::WriteAllText($backupPath, ($before | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
    }
    foreach ($name in $names) {
        $svc = Get-Service -Name $name
        if ($svc.StartType.ToString() -eq 'Disabled') { Set-Service -Name $name -StartupType Manual }
        if ((Get-Service -Name $name).Status.ToString() -ne 'Running') { Start-Service -Name $name }
    }
    $denyAfter = (Get-ItemProperty -LiteralPath $terminalKey -Name fDenyTSConnections).fDenyTSConnections
    if ($denyAfter -ne $denyBefore) { throw 'External Remote Desktop setting changed unexpectedly. Inspect system policy before proceeding.' }
    $after = @(foreach ($name in $names) {
        $svc = Get-Service -Name $name
        [ordered]@{name=$name;status=$svc.Status.ToString();startType=$svc.StartType.ToString()}
    })
    Save-SetupResult ([ordered]@{ok=$true;time=(Get-Date -Format o);before=$before;after=$after;externalRdpDenied=$denyAfter;firewallChanged=$false})
    exit 0
} catch {
    Save-SetupResult ([ordered]@{ok=$false;time=(Get-Date -Format o);error=$_.Exception.Message;firewallChanged=$false})
    exit 1
}
