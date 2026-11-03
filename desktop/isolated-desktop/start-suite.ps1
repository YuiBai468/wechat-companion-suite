param(
    [switch]$Background
)
# One-click background launcher for the dashboard suite.
# - Runs only in the parent session; the controller verifies parent/child identity.
# - Reuses an existing host/bot; starts WeChatDesktop.exe --background async when absent.
# - Starts the bot only after a nonce-scoped child preflight and validates the 8765 PID.
# - Deletes temporary task definitions on success/failure; never kills running processes.
# - On success hides the host and opens the user's default browser console.
# pwsh is already RemoteSigned; no execution-policy override is used.
$ErrorActionPreference = 'Stop'
$python = $env:COMPANION_PYTHON
if (-not $python) { $venv = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) '.venv\Scripts\python.exe'; if (Test-Path -LiteralPath $venv) { $python = $venv } else { $python = 'python.exe' } }
$controller = Join-Path $PSScriptRoot 'suite_controller.py'
if (-not (Test-Path -LiteralPath $python)) {
    $command = Get-Command $python -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}
if (-not (Test-Path -LiteralPath $python)) {
    Write-Output '{"ok":false,"error":"python_missing","reason":"找不到 Python 运行时"}'
    exit 1
}
if (-not (Test-Path -LiteralPath $controller)) {
    Write-Output '{"ok":false,"error":"controller_missing","reason":"找不到套件启动控制器"}'
    exit 1
}
$env:PYTHONUTF8 = '1'
& $python $controller
exit $LASTEXITCODE
