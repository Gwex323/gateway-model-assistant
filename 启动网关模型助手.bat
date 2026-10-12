@echo off
setlocal
cd /d "%~dp0"
where pyw >nul 2>nul
if not errorlevel 1 (
  start "" pyw -3 "%~dp0app.py"
  exit /b
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0app.py"
  exit /b
)
echo Python 3.10+ with Tkinter is required. Run the diagnostics launcher for details.
pause
