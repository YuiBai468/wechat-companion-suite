# build.ps1
# Builds the isolated-desktop backend with the in-box .NET Framework 4.8 C# 5 compiler.
#
#   SessionBackend.dll : shared backend library (contract consumed by the GPT-owned WinForms launcher)
#   ChildProbe.exe     : isolated desktop probe (console, self-contained)
#   StatusTool.exe     : verification-only command line helper for the contract (not used by the launcher)
#
# Usage (from anywhere):
#   powershell -NoProfile -File build.ps1
#
# The script only writes inside its own directory. It does not touch the parent session, WeChat,
# services, tasks, RDP settings, firewall or any security policy.

[CmdletBinding()]
param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

function Find-Csc {
    $candidates = @(
        "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Invoke-Csc {
    param(
        [string]$CscPath,
        [string[]]$Arguments,
        [string]$Description
    )
    Write-Host ("==> {0}" -f $Description)
    Write-Host ("    {0} {1}" -f $CscPath, ($Arguments -join " "))
    & $CscPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("C# compilation failed ({0}), csc exit code {1}" -f $Description, $LASTEXITCODE)
    }
}

$csc = Find-Csc
if (-not $csc) {
    throw "Cannot find csc.exe under C:\Windows\Microsoft.NET\Framework(64)\v4.0.30319."
}

$sessionBackendSource = Join-Path $root "SessionBackend.cs"
$childProbeSource     = Join-Path $root "ChildProbe.cs"
$statusToolSource     = Join-Path $root "verify\StatusTool.cs"
$probeLogicTestSource = Join-Path $root "verify\ProbeLogicTest.cs"

foreach ($source in @($sessionBackendSource, $childProbeSource, $statusToolSource, $probeLogicTestSource)) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw ("Missing source file: {0}" -f $source)
    }
}

$sessionBackendDll = Join-Path $root "SessionBackend.dll"
$childProbeExe     = Join-Path $root "ChildProbe.exe"
$statusToolExe     = Join-Path $root "StatusTool.exe"
$probeLogicTestExe = Join-Path $root "verify\ProbeLogicTest.exe"

foreach ($output in @($sessionBackendDll, $childProbeExe, $statusToolExe, $probeLogicTestExe)) {
    if (Test-Path -LiteralPath $output) {
        Remove-Item -LiteralPath $output -Force
    }
}

# /langversion:5 pins the sources to the C# 5 feature set (no interpolation, no expression bodies,
# no null-conditional operators) so they stay buildable with the in-box csc.exe.
$common = @(
    "/nologo",
    "/langversion:5",
    "/codepage:65001",
    "/warn:4",
    "/optimize+",
    "/debug-",
    "/r:System.dll"
)

Invoke-Csc -CscPath $csc -Description "SessionBackend.dll (library)" -Arguments ([string[]](
    $common + @(
        "/target:library",
        "/out:$sessionBackendDll",
        $sessionBackendSource
    )
))

Invoke-Csc -CscPath $csc -Description "ChildProbe.exe (console, standalone)" -Arguments ([string[]](
    $common + @(
        "/target:exe",
        "/out:$childProbeExe",
        $childProbeSource
    )
))

Invoke-Csc -CscPath $csc -Description "StatusTool.exe (verification helper)" -Arguments ([string[]](
    $common + @(
        "/target:exe",
        "/out:$statusToolExe",
        "/r:$sessionBackendDll",
        $statusToolSource
    )
))

# The probe logic test is compiled together with ChildProbe.cs so it can call the same pure
# EvaluateProbe function; /main selects the test entry point. It never touches the desktop.
Invoke-Csc -CscPath $csc -Description "verify\ProbeLogicTest.exe (pure probe-logic harness)" -Arguments ([string[]](
    $common + @(
        "/target:exe",
        "/main:WeChatIsolatedDesktop.ProbeLogicTest",
        "/out:$probeLogicTestExe",
        $childProbeSource,
        $probeLogicTestSource
    )
))

Write-Host ""
Write-Host "Build succeeded. Outputs:"
foreach ($output in @($sessionBackendDll, $childProbeExe, $statusToolExe, $probeLogicTestExe)) {
    $item = Get-Item -LiteralPath $output
    $hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash
    Write-Host ("  {0}  {1,8} bytes  sha256={2}" -f $item.FullName, $item.Length, $hash)
}

Write-Host ""
Write-Host "Optional read-only checks:"
Write-Host ("  & '{0}'" -f $statusToolExe)
Write-Host ("  & '{0}' --api" -f $statusToolExe)
Write-Host ("  & '{0}' --snapshot" -f $statusToolExe)
Write-Host ("  & '{0}'   # pure probe-logic tests, no desktop access" -f $probeLogicTestExe)
