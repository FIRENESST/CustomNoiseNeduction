@echo off
setlocal
cd /d "%~dp0"
set "QUIET_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "QUIET_PYTHON=%~dp0.venv\Scripts\python.exe"
"%QUIET_PYTHON%" -B "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo Start failed. Check that Python is installed, then install the required packages:
    echo     "%QUIET_PYTHON%" -m pip install -r requirements.txt
    pause
    exit /b 1
)
