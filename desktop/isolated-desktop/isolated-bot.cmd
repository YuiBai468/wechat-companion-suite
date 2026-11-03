@echo off

REM ============================================================================

REM isolated-bot.cmd - start the WeChat bot bound to the given child session.

REM

REM Usage:

REM     isolated-bot.cmd <parent-session> <expected-session>

REM

REM Examples (numbers are Windows session ids, not process ids):

REM     isolated-bot.cmd 1 2      parent session 1, bot must run in session 2

REM

REM This wrapper only forwards the two session numbers to isolated-bot.py.

REM The Python entry verifies the session identity guard BEFORE importing the

REM bot or the wechatauto SDK; on any refusal it exits without touching WeChat.

REM The parent/expected values are supplied by the WinForms launcher; this

REM script does not discover session ids itself and never starts the bot on

REM its own.

REM ============================================================================

setlocal



if "%~1"=="" goto usage

if "%~2"=="" goto usage

echo %~1|findstr /r "^[0-9][0-9]*$" >nul || goto usage

echo %~2|findstr /r "^[0-9][0-9]*$" >nul || goto usage



REM ── clear proxy environment inherited from the parent shell (same as bot.cmd) ──

REM A lingering HTTPS_PROXY makes every DeepSeek request fail with 10061 once

REM the proxy app is closed; the bot talks to DeepSeek directly.

set HTTP_PROXY=

set HTTPS_PROXY=

set ALL_PROXY=

set http_proxy=

set https_proxy=

set all_proxy=



set PYTHONUTF8=1

set PYTHONIOENCODING=utf-8:backslashreplace



set "PYTHON_BIN=%COMPANION_PYTHON%"
if "%PYTHON_BIN%"=="" if exist "%~dp0..\..\.venv\Scripts\python.exe" set "PYTHON_BIN=%~dp0..\..\.venv\Scripts\python.exe"
if "%PYTHON_BIN%"=="" set "PYTHON_BIN=python"
if not exist "%~dp0..\..\data\logs" mkdir "%~dp0..\..\data\logs" >nul 2>&1
"%PYTHON_BIN%" -X utf8 -u "%~dp0isolated-bot.py" --parent-session "%~1" --expected-session "%~2" >> "%~dp0..\..\data\logs\wechat-bot.out.log" 2>&1

set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%



:usage

echo Usage: isolated-bot.cmd ^<parent-session^> ^<expected-session^> 1>&2

echo        both must be positive numeric Windows session ids 1>&2

endlocal & exit /b 2

