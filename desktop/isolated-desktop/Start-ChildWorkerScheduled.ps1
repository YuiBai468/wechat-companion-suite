param([Parameter(Mandatory=$true)][int]$ParentPid)
$ErrorActionPreference='Stop'
$taskName='WeChatChildBootstrap-'+[guid]::NewGuid().ToString('N')
$folder=$null
$registered=$false
try {
    $exe=Join-Path $PSScriptRoot 'WeChatDesktop.exe'
    $parent=Get-Process -Id $ParentPid
    if ($parent.Path -ine $exe) { throw 'Parent executable did not match.' }
    Add-Type -Path (Join-Path $PSScriptRoot 'backend\SessionBackend.dll')
    $parentSession=[WeChatIsolatedDesktop.SessionBackend]::GetCurrentSessionId()
    $child=[WeChatIsolatedDesktop.SessionBackend]::GetChildSessionId()
    if ($child -le 0 -or $child -eq $parentSession -or $parent.SessionId -ne $parentSession) { throw 'No verified child session.' }
    $scheduler=New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder=$scheduler.GetFolder('\')
    $definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='One-time bootstrap into the current user child session. No triggers.'
    $definition.Principal.LogonType=3
    $definition.Principal.RunLevel=0
    $definition.Settings.AllowDemandStart=$true
    $definition.Settings.DisallowStartIfOnBatteries=$false
    $definition.Settings.StopIfGoingOnBatteries=$false
    $definition.Settings.ExecutionTimeLimit='PT0S'
    $action=$definition.Actions.Create(0)
    $action.Path=$exe
    $action.Arguments='--worker --parent-session '+$parentSession+' --parent-pid '+$ParentPid
    $action.WorkingDirectory=$PSScriptRoot
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,$sid,$null,3,$null)
    $registered=$true
    $running=$task.RunEx($null,4,$child,$null)
    $ready=$false
    for ($attempt=0;$attempt -lt 24;$attempt++) {
        Start-Sleep -Milliseconds 250
        $statePath=Join-Path $PSScriptRoot 'child-worker-state.json'
        if (Test-Path -LiteralPath $statePath) {
            $state=Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($state.sessionId -eq $child -and $state.parentSessionId -eq $parentSession) { $ready=$true;break }
        }
    }
    if (-not $ready) { throw ('Worker did not report ready in target session. Task state='+$running.State) }
    $result=[ordered]@{ok=$true;method='TaskScheduler.RunEx';parentSessionId=$parentSession;childSessionId=$child;temporaryTaskRemoved=$false}
} catch { $result=[ordered]@{ok=$false;error=$_.Exception.Message;temporaryTaskRemoved=$false} }
finally {
    if ($registered -and $folder) { $folder.DeleteTask($taskName,0);$result.temporaryTaskRemoved=$true }
}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'scheduler-bootstrap-result.json'),($result|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
$result | ConvertTo-Json
if (-not $result.ok) {exit 1}
