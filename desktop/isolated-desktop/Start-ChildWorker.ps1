param([Parameter(Mandatory=$true)][int]$ParentPid)
$ErrorActionPreference='Stop'
$resultPath=Join-Path $PSScriptRoot 'bootstrap-result.json'
try {
    $expected=Join-Path $PSScriptRoot 'WeChatDesktop.exe'
    $parentProcess=Get-Process -Id $ParentPid
    if ($parentProcess.Path -ine $expected) { throw 'Parent process path did not match the launcher.' }
    Add-Type -Path (Join-Path $PSScriptRoot 'backend\SessionBackend.dll')
    $parentSession=[WeChatIsolatedDesktop.SessionBackend]::GetCurrentSessionId()
    if ($parentProcess.SessionId -ne $parentSession) { throw 'Parent process is in a different session.' }
    $child=[WeChatIsolatedDesktop.SessionBackend]::GetChildSessionId()
    if ($child -le 0 -or $child -eq $parentSession) { throw 'No verified child session exists.' }
    $workerArgs='--worker --parent-session '+$parentSession+' --parent-pid '+$ParentPid
    $childPid=[WeChatIsolatedDesktop.SessionBackend]::LaunchInChild($expected,$workerArgs,$PSScriptRoot)
    $result=[ordered]@{ok=$true;parentSessionId=$parentSession;childSessionId=$child;workerPid=$childPid;time=(Get-Date -Format o)}
} catch {
    $result=[ordered]@{ok=$false;error=$_.Exception.Message;time=(Get-Date -Format o)}
}
[IO.File]::WriteAllText($resultPath,($result|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
if (-not $result.ok) { exit 1 }
