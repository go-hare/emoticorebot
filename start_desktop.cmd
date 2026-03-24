@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
  python -m emoticorebot desktop-dev %*
) else (
  py -3 -m emoticorebot desktop-dev %*
)

set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Desktop start failed with exit code %EXIT_CODE%.
  pause
)

exit /b %EXIT_CODE%
