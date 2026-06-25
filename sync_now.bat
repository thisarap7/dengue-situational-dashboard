@echo off
REM Manually commit & push any new dengue PDFs right now (no waiting).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch_and_push.ps1" -Once
echo.
echo Done. (See auto_sync.log for details.)
pause
