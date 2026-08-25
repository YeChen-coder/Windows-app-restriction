@echo off
setlocal
cd /d "%~dp0"

where pyw.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  start "" pyw.exe -3 "%~dp0app_limiter.pyw"
  exit /b 0
)

where pythonw.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  start "" pythonw.exe "%~dp0app_limiter.pyw"
  exit /b 0
)

where python.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  start "" python.exe "%~dp0app_limiter.pyw"
  exit /b 0
)

echo Python was not found. Please install Python 3 or add it to PATH.
pause
exit /b 1
