@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title O R I G I N

set "ORIGIN_ROOT=%~dp0"
if "%ORIGIN_ROOT:~-1%"=="\" set "ORIGIN_ROOT=%ORIGIN_ROOT:~0,-1%"

set "ORIGIN_EXE="
if exist "%~dp0frontend\src-tauri\target\debug\origin.exe" set "ORIGIN_EXE=%~dp0frontend\src-tauri\target\debug\origin.exe"
if not defined ORIGIN_EXE if exist "%~dp0frontend\src-tauri\target\release\origin.exe" set "ORIGIN_EXE=%~dp0frontend\src-tauri\target\release\origin.exe"
if not defined ORIGIN_EXE if exist "%~dp0frontend\src-tauri\target\debug\origin-shell.exe" set "ORIGIN_EXE=%~dp0frontend\src-tauri\target\debug\origin-shell.exe"
if not defined ORIGIN_EXE if exist "%~dp0frontend\src-tauri\target\release\origin-shell.exe" set "ORIGIN_EXE=%~dp0frontend\src-tauri\target\release\origin-shell.exe"

if not defined ORIGIN_EXE (
    echo [ERROR] origin.exe not found.
    echo Build the native shell:
    echo   cd frontend
    echo   npm run tauri:build
    echo.
    echo Browser debug mode: origin dev-web
    pause
    exit /b 1
)

set "ORIGIN_PY="
if exist "%~dp0venv\Scripts\pythonw.exe" if not defined ORIGIN_PY "%~dp0venv\Scripts\pythonw.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0venv\Scripts\pythonw.exe"
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe" if not defined ORIGIN_PY "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe"
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" if not defined ORIGIN_PY "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe"

if not defined ORIGIN_PY (
    echo [ERROR] No portable Python found in venv or .uv-python.
    pause
    exit /b 1
)

echo [INFO] Origin root: !ORIGIN_ROOT!
echo [INFO] Native shell: !ORIGIN_EXE!
echo [INFO] Python: !ORIGIN_PY!
start "" /b "!ORIGIN_EXE!"

endlocal
exit /b 0
