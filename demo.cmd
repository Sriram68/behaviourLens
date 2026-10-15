@echo off
rem One-word commands for running the BehaviorLens demo (see docs/demo-script.md).
rem   demo prep      regenerate the 8-week history, rebuild everything (no Teams alert), wake the warehouse
rem   demo check     preflight without Ask (safe to run any time)
rem   demo preflight full preflight incl. warming the 4 suggested Ask questions (run ~10 min before going on)
rem   demo pipeline  the on-stage run: sync today's events, run the Job, send the Teams alert once
rem   demo realert   re-run the AI step's alert even if this incident was already alerted
setlocal
cd /d "%~dp0"
set "PY=azure-function\.venv\Scripts\python.exe"
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
rem Blob folders are named by UTC date, so sync from today's UTC date.
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).ToUniversalTime().ToString('yyyy/MM/dd')"') do set "TODAY=%%d"

if /i "%~1"=="prep" goto prep
if /i "%~1"=="check" goto check
if /i "%~1"=="preflight" goto preflight
if /i "%~1"=="pipeline" goto pipeline
if /i "%~1"=="realert" goto realert
echo Usage: demo prep ^| check ^| preflight ^| pipeline ^| realert
exit /b 1

:prep
echo [1/3] Sending 8 weeks of simulated ReportHub history through the Function...
"%PY%" qa\synthetic_event_generator.py || goto fail
echo [2/3] Rebuilding Databricks from today's files only (%TODAY%), Teams alert held for the stage run...
"%PY%" databricks\run_pipeline.py --reset --since %TODAY% --alert never || goto fail
echo [3/3] Preflight (wakes the SQL warehouse). Next: open BehaviorLens.pbip, Home - Refresh, Save.
"%PY%" scripts\preflight.py --no-ask
exit /b %errorlevel%

:check
"%PY%" scripts\preflight.py --no-ask
exit /b %errorlevel%

:preflight
"%PY%" scripts\preflight.py
exit /b %errorlevel%

:pipeline
"%PY%" databricks\run_pipeline.py --since %TODAY%
exit /b %errorlevel%

:realert
"%PY%" databricks\run_pipeline.py --skip-sync --alert always
exit /b %errorlevel%

:fail
echo Step failed: see the message above. Fallbacks are in docs/demo-script.md.
exit /b 1
