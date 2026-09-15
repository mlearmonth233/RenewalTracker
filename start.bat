@echo off
REM One-click launcher for Windows.
REM First run: creates a virtual environment and installs dependencies.
REM Every run: starts RenewalTracker and opens it in your browser.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && (py -3.11 -c "exit()" >nul 2>nul && set "PY=py -3.11")
if not defined PY (where py >nul 2>nul && (py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=py -3"))
if not defined PY (where python >nul 2>nul && (python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=python"))
if not defined PY (
  echo RenewalTracker needs Python 3.11 or newer.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up a private Python environment ^(this takes a minute^)...
  %PY% -m venv .venv || goto :fail
)

REM Re-install only when requirements.txt changed since last time.
set "STAMP=.venv\requirements.stamp"
set "NEED_INSTALL=1"
if exist "%STAMP%" (
  fc /b requirements.txt "%STAMP%" >nul 2>nul && set "NEED_INSTALL=0"
)
if "%NEED_INSTALL%"=="1" (
  echo Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :fail
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :fail
  copy /y requirements.txt "%STAMP%" >nul
)

".venv\Scripts\python.exe" run.py %*
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo Something went wrong. See the messages above.
pause
exit /b 1
