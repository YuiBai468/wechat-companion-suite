[CmdletBinding()]
param([string]$Config = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Config) { $Config = Join-Path $Root "desktop\bot-config.json" }
$Python = $env:COMPANION_PYTHON
if (-not $Python) {
    $venv = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venv) { $Python = $venv } else { $Python = "python" }
}
& $Python (Join-Path $Root "desktop\wechat-bot.py") --config $Config --check-config
exit $LASTEXITCODE
