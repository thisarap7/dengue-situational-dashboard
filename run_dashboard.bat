@echo off
REM ============================================================
REM  Dengue Situational Dashboard - one-click launcher (Windows)
REM  Double-click this file to open the dashboard in your browser.
REM ============================================================
setlocal
cd /d "%~dp0"

echo Starting the Dengue Situational Dashboard...
echo (A browser tab will open at http://localhost:8501)
echo Close this window to stop the dashboard.
echo.

python -m streamlit run app.py
if errorlevel 1 (
  echo.
  echo -----------------------------------------------------------
  echo Something went wrong. If a package was reported missing, run:
  echo     pip install -r requirements.txt
  echo and then double-click this file again.
  echo -----------------------------------------------------------
  pause
)
endlocal
