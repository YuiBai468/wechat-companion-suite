# verify.ps1
# Verification-only script for the isolated-desktop backend. It re-runs the acceptance checks that
# can be executed safely on a non-admin machine with no child session present:
#
#   1. build outputs exist
#   2. read-only status query (SessionBackend.StatusJson via StatusTool.exe)
#   3. shared contract API surface (StatusTool.exe --api)
#   4. LaunchInChild safe failure when no child session exists (never falls back to the parent)
#   5. ChildProbe refuses to run when --parent-session equals the current session
#   6. cursor / foreground window / clipboard state unchanged by the refusal run
#   7. argument validation exit codes
#
# It never creates a child session, never moves the parent cursor, never writes the clipboard and
# never launches anything. It only writes snapshot evidence files inside .\verify.
#
# Usage (from anywhere):
#   powershell -NoProfile -File verify.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$verifyDir = Join-Path $root "verify"
if (-not (Test-Path -LiteralPath $verifyDir)) {
    New-Item -ItemType Directory -Path $verifyDir | Out-Null
}

$script:failures = New-Object System.Collections.Generic.List[string]

function Assert-Condition {
    param([bool]$Condition, [string]$Message)
    if ($Condition) {
        Write-Host ("PASS  {0}" -f $Message)
    } else {
        Write-Host ("FAIL  {0}" -f $Message)
        [void]$script:failures.Add($Message)
    }
}

function Invoke-Tool {
    param([string]$Exe, [string[]]$Arguments)
    $output = & $Exe @Arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
    return [pscustomobject]@{ Output = $output; ExitCode = $code }
}

$statusTool = Join-Path $root "StatusTool.exe"
$childProbe = Join-Path $root "ChildProbe.exe"
$sessionDll = Join-Path $root "SessionBackend.dll"

Write-Host "=== 1. build outputs ==="
foreach ($artifact in @($sessionDll, $childProbe, $statusTool)) {
    Assert-Condition (Test-Path -LiteralPath $artifact -PathType Leaf) ("exists: " + $artifact)
}

Write-Host ""
Write-Host "=== 2. read-only status query ==="
$statusResult = Invoke-Tool -Exe $statusTool -Arguments @()
Assert-Condition ($statusResult.ExitCode -eq 0) ("StatusTool exit code 0 (actual " + $statusResult.ExitCode + ")")
$statusText = $statusResult.Output.Trim()
Write-Host $statusText
$status = $null
try { $status = $statusText | ConvertFrom-Json } catch { }
Assert-Condition ($null -ne $status) "status output is valid JSON"
if ($null -ne $status) {
    Assert-Condition ($null -ne $status.enabled) "status has 'enabled'"
    Assert-Condition ($null -ne $status.parentSessionId) "status has 'parentSessionId'"
    Assert-Condition ($null -ne $status.childSessionId) "status has 'childSessionId'"
    Assert-Condition ($null -ne $status.isAdmin) "status has 'isAdmin'"
    Assert-Condition ($status.parentSessionId -gt 0) ("parentSessionId is a real session id (" + $status.parentSessionId + ")")
    $currentSession = [int]$status.parentSessionId
} else {
    $currentSession = -1
}

Write-Host ""
Write-Host "=== 3. shared contract API surface ==="
$apiResult = Invoke-Tool -Exe $statusTool -Arguments @("--api")
$apiText = $apiResult.Output
Write-Host $apiText
foreach ($signature in @(
    "Int32 GetCurrentSessionId()",
    "Boolean IsChildSessionsEnabled()",
    "Int32 GetChildSessionId()",
    "String StatusJson()",
    "Int32 LaunchInChild(String, String, String)"
)) {
    Assert-Condition ($apiText.Contains($signature)) ("contract method present: " + $signature)
}

Write-Host ""
Write-Host "=== 3b. pure probe result-judgment tests (no desktop access) ==="
$probeLogicTest = Join-Path $verifyDir "ProbeLogicTest.exe"
Assert-Condition (Test-Path -LiteralPath $probeLogicTest -PathType Leaf) ("exists: " + $probeLogicTest)
$logicResult = Invoke-Tool -Exe $probeLogicTest -Arguments @()
Write-Host $logicResult.Output.Trim()
Assert-Condition ($logicResult.ExitCode -eq 0) ("ProbeLogicTest exit code 0 (actual " + $logicResult.ExitCode + ")")
Assert-Condition ($logicResult.Output -match "0 failed") "all synthetic probe-logic cases passed (ok=false for every broken evidence)"

Write-Host ""
Write-Host "=== 4. LaunchInChild safe failure when no child session ==="
$childSessionId = $null
if ($null -ne $status) { $childSessionId = [int]$status.childSessionId }
if ($childSessionId -eq -1) {
    $notepadBefore = @(Get-Process notepad -ErrorAction SilentlyContinue).Count
    $launchResult = Invoke-Tool -Exe $statusTool -Arguments @("--launch", "C:\Windows\System32\notepad.exe")
    Write-Host $launchResult.Output.Trim()
    Assert-Condition ($launchResult.ExitCode -ne 0) ("LaunchInChild returned a failure exit code (" + $launchResult.ExitCode + ")")
    Assert-Condition ($launchResult.Output -match "LAUNCH-FAILED") "LaunchInChild reported a safe failure"
    Start-Sleep -Milliseconds 300
    $notepadAfter = @(Get-Process notepad -ErrorAction SilentlyContinue).Count
    Assert-Condition ($notepadBefore -eq $notepadAfter) ("no target process was started (notepad count " + $notepadBefore + " -> " + $notepadAfter + ")")
} else {
    Write-Host "SKIP  a child session exists (id $childSessionId); the no-child failure path cannot be exercised"
}

Write-Host ""
Write-Host "=== 5. ChildProbe parent-session refusal ==="
Assert-Condition ($currentSession -gt 0) "current session known"
$refusedOutputPath = Join-Path $verifyDir "probe-refused-should-not-exist.json"
if (Test-Path -LiteralPath $refusedOutputPath) { Remove-Item -LiteralPath $refusedOutputPath -Force }

$snapshotBeforePath = Join-Path $verifyDir "snapshot-before.json"
$snapshotAfterPath = Join-Path $verifyDir "snapshot-after.json"
$snapshotBeforeResult = Invoke-Tool -Exe $statusTool -Arguments @("--snapshot")
$snapshotBeforeResult.Output.Trim() | Set-Content -LiteralPath $snapshotBeforePath -Encoding ASCII

$probeResult = Invoke-Tool -Exe $childProbe -Arguments @("--parent-session", [string]$currentSession, "--out", $refusedOutputPath)
Write-Host $probeResult.Output.Trim()
Assert-Condition ($probeResult.ExitCode -eq 2) ("ChildProbe refused with exit code 2 (actual " + $probeResult.ExitCode + ")")
Assert-Condition ($probeResult.Output -match "REFUSED") "ChildProbe printed a REFUSED message"
Assert-Condition (-not (Test-Path -LiteralPath $refusedOutputPath)) "ChildProbe wrote no JSON on refusal"

$snapshotAfterResult = Invoke-Tool -Exe $statusTool -Arguments @("--snapshot")
$snapshotAfterResult.Output.Trim() | Set-Content -LiteralPath $snapshotAfterPath -Encoding ASCII

Write-Host ""
Write-Host "=== 6. parent cursor / foreground / clipboard unchanged ==="
$before = $null
$after = $null
try { $before = (Get-Content -LiteralPath $snapshotBeforePath -Raw) | ConvertFrom-Json } catch { }
try { $after = (Get-Content -LiteralPath $snapshotAfterPath -Raw) | ConvertFrom-Json } catch { }
if ($null -ne $before -and $null -ne $after) {
    Assert-Condition ($before.cursor.ok -and $after.cursor.ok) "cursor position readable"
    Assert-Condition ($before.cursor.x -eq $after.cursor.x -and $before.cursor.y -eq $after.cursor.y) (
        "cursor unchanged (" + $before.cursor.x + "," + $before.cursor.y + ") -> (" + $after.cursor.x + "," + $after.cursor.y + ")")
    Assert-Condition ($before.foregroundHwnd -eq $after.foregroundHwnd) (
        "foreground HWND unchanged (" + $before.foregroundHwnd + " -> " + $after.foregroundHwnd + ")")
    Assert-Condition ($before.clipboard.formatCount -eq $after.clipboard.formatCount) (
        "clipboard format count unchanged (" + $before.clipboard.formatCount + " -> " + $after.clipboard.formatCount + ")")
    Assert-Condition ($before.clipboard.hasUnicodeText -eq $after.clipboard.hasUnicodeText) "clipboard text availability unchanged"
} else {
    Assert-Condition $false "snapshot JSON readable"
}

Write-Host ""
Write-Host "=== 7. argument validation ==="
$bad1 = Invoke-Tool -Exe $childProbe -Arguments @("--parent-session", [string]$currentSession)
Assert-Condition ($bad1.ExitCode -eq 3) ("missing --out exits 3 (actual " + $bad1.ExitCode + ")")
$bad2 = Invoke-Tool -Exe $childProbe -Arguments @("--out", (Join-Path $verifyDir "x.json"))
Assert-Condition ($bad2.ExitCode -eq 3) ("missing --parent-session exits 3 (actual " + $bad2.ExitCode + ")")
$bad3 = Invoke-Tool -Exe $childProbe -Arguments @("--parent-session", "0", "--out", (Join-Path $verifyDir "x.json"))
Assert-Condition ($bad3.ExitCode -eq 3) ("invalid --parent-session 0 exits 3 (actual " + $bad3.ExitCode + ")")
Assert-Condition (-not (Test-Path -LiteralPath (Join-Path $verifyDir "x.json"))) "no JSON written for invalid arguments"

Write-Host ""
Write-Host "=== 8. static safety checks (source order / no removed fallback) ==="
$backendSource = Get-Content -LiteralPath (Join-Path $root "SessionBackend.cs") -Raw
$probeSource = Get-Content -LiteralPath (Join-Path $root "ChildProbe.cs") -Raw

Assert-Condition ($backendSource -match "CREATE_SUSPENDED") "SessionBackend creates the target process with CREATE_SUSPENDED"
$verifyIndex = $backendSource.IndexOf("Mandatory session verification BEFORE", [System.StringComparison]::Ordinal)
$resumeIndex = $backendSource.IndexOf("ResumeThread(processInfo.hThread)", [System.StringComparison]::Ordinal)
$terminateOnMismatch = $backendSource.IndexOf("TerminateSuspendedProcess(processInfo);", [System.StringComparison]::Ordinal)
Assert-Condition ($verifyIndex -ge 0) "explicit session-verification marker exists"
Assert-Condition ($resumeIndex -gt $verifyIndex) "ResumeThread appears only after the session-verification block"
Assert-Condition ($terminateOnMismatch -gt 0 -and $terminateOnMismatch -lt $resumeIndex) "suspended process is terminated on unverified/mismatched session before any resume"
Assert-Condition (-not ($backendSource -match "CreateProcessWithTokenW\s*\(")) "no CreateProcessWithTokenW call remains (single explicit CreateProcessAsUser path)"

Assert-Condition ($probeSource -match 'AppendJsonBool\(sb, "ok", evaluation\.Ok\)') "ChildProbe writes top-level ok from the evaluation result"
Assert-Condition ($probeSource -match "run\.Ok \? EXIT_OK : EXIT_ERROR") "ChildProbe exit code follows the evaluation result"
Assert-Condition ($probeSource -match "did not move the cursor") "ChildProbe initial GetCursorPos failure aborts before moving"

Write-Host ""
if ($script:failures.Count -eq 0) {
    Write-Host "RESULT: PASS (all checks)"
    exit 0
} else {
    Write-Host ("RESULT: FAIL (" + $script:failures.Count + " check(s) failed)")
    foreach ($failure in $script:failures) { Write-Host ("  - " + $failure) }
    exit 1
}
