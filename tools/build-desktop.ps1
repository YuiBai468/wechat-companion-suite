# Build the Windows native helpers for the optional isolated desktop.
# Requires the in-box .NET Framework 4.8 C# compiler. No build output is committed.
[CmdletBinding()]
param(
    [string]$Configuration = "Release"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "desktop\isolated-desktop\backend"
$Isolated = Join-Path $Root "desktop\isolated-desktop"

function Find-Csc {
    $candidates = @(
        "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw "Cannot find the in-box .NET Framework csc.exe. Install .NET Framework 4.8 developer components."
}

Write-Host "==> building isolated backend"
& (Join-Path $Backend "build.ps1") -Configuration $Configuration
if ($LASTEXITCODE -ne 0) { throw "backend build failed" }

$csc = Find-Csc
$common = @("/nologo", "/langversion:5", "/codepage:65001", "/warn:4", "/optimize+", "/debug-", "/r:System.dll")
$refs = @("/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.Web.Extensions.dll")
$backendDll = Join-Path $Backend "SessionBackend.dll"

Write-Host "==> building launcher"
& $csc @common @refs "/target:winexe" "/out:$(Join-Path $Isolated 'WeChatDesktop.exe')" "/r:$backendDll" (Join-Path $Isolated "Launcher.cs")
if ($LASTEXITCODE -ne 0) { throw "launcher build failed" }

Write-Host "==> building OpenCompanion"
& $csc @common @refs "/target:winexe" "/out:$(Join-Path $Isolated 'OpenCompanion.exe')" (Join-Path $Isolated "OpenCompanion.cs")
if ($LASTEXITCODE -ne 0) { throw "OpenCompanion build failed" }
Write-Host "build completed. Binaries are under desktop/isolated-desktop/ and are not intended for commit."
