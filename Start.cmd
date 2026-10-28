@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -B -c "import sys;sys.exit(sys.version_info<(3,11))" >nul 2>nul
  if not errorlevel 1 goto venv
)
py -3 -c "import sys;sys.exit(sys.version_info<(3,11))" >nul 2>nul
if not errorlevel 1 goto py
python -c "import sys;sys.exit(sys.version_info<(3,11))" >nul 2>nul
if not errorlevel 1 goto python
echo [Python unavailable] Install Python 3.11+ or repair the project .venv.
set "result=2"
goto finish
:venv
".venv\Scripts\python.exe" -B "%~dp0bootstrap.py" %*
goto status
:py
py -3 -B "%~dp0bootstrap.py" %*
goto status
:python
python -B "%~dp0bootstrap.py" %*
:status
set "result=%errorlevel%"
:finish
if not "%result%"=="0" (
 echo [Startup failed] Exit code: %result%
 pause
)
exit /b %result%
