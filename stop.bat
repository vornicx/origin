@echo off
setlocal
cd /d "%~dp0"

echo [INFO] Stopping Origin...

taskkill /f /im origin.exe >nul 2>&1
taskkill /f /im origin-shell.exe >nul 2>&1

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":9001" 2^>nul') do (
    taskkill /f /pid %%a >nul 2>&1
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173" 2^>nul') do (
    taskkill /f /pid %%a >nul 2>&1
)

echo [INFO] Origin stopped.
endlocal
exit /b 0
