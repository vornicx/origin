@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if /i "%1"=="stop" goto :STOP
if /i "%1"=="status" goto :STATUS
if /i "%1"=="open" goto :OPEN
if /i "%1"=="setup" goto :SETUP
if /i "%1"=="dev-web" goto :DEVWEB
if /i "%1"=="start" goto :START
if /i "%1"=="crucix" goto :CRUCIX
if /i "%1"=="osiris" goto :OSIRIS
if /i "%1"=="intel" goto :INTEL
if "%1"=="" goto :START
echo Usage: origin start, stop, status, open, dev-web, setup, crucix, osiris, intel
exit /b 1

:FIND_PYTHONW
set "ORIGIN_PY="
if exist "%~dp0venv\Scripts\pythonw.exe" if not defined ORIGIN_PY "%~dp0venv\Scripts\pythonw.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0venv\Scripts\pythonw.exe"
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe" if not defined ORIGIN_PY "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe"
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" if not defined ORIGIN_PY "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe"
exit /b 0

:FIND_PYTHON
set "ORIGIN_PY="
if exist "%~dp0venv\Scripts\python.exe" if not defined ORIGIN_PY "%~dp0venv\Scripts\python.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0venv\Scripts\python.exe"
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" if not defined ORIGIN_PY "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe"
exit /b 0

:START
set "ORIGIN_ROOT=%~dp0"
if "%ORIGIN_ROOT:~-1%"=="\" set "ORIGIN_ROOT=%ORIGIN_ROOT:~0,-1%"
set "ORIGIN_EXE="
if exist "%~dp0frontend\src-tauri\target\debug\origin.exe" set "ORIGIN_EXE=%~dp0frontend\src-tauri\target\debug\origin.exe"
if not defined ORIGIN_EXE if exist "%~dp0frontend\src-tauri\target\release\origin.exe" set "ORIGIN_EXE=%~dp0frontend\src-tauri\target\release\origin.exe"
if not defined ORIGIN_EXE if exist "%~dp0frontend\src-tauri\target\debug\origin-shell.exe" set "ORIGIN_EXE=%~dp0frontend\src-tauri\target\debug\origin-shell.exe"
if not defined ORIGIN_EXE if exist "%~dp0frontend\src-tauri\target\release\origin-shell.exe" set "ORIGIN_EXE=%~dp0frontend\src-tauri\target\release\origin-shell.exe"
if not defined ORIGIN_EXE (
    echo [ERROR] origin.exe not found. Build with: cd frontend && npm run tauri:build
    echo Or use browser debug mode: origin dev-web
    exit /b 1
)
echo [Origin] Starting native shell...
start "" /b "!ORIGIN_EXE!"
exit /b 0

:DEVWEB
echo [Origin] Browser debug mode...
call :FIND_PYTHONW
if not defined ORIGIN_PY (
    echo [ERROR] No portable Python found in venv or .uv-python
    exit /b 1
)
start "" /b "!ORIGIN_PY!" "origin_launcher.pyw"
timeout /t 2 /nobreak >nul
start http://localhost:9001
exit /b 0

:STOP
taskkill /f /im origin.exe >nul 2>&1
taskkill /f /im origin-shell.exe >nul 2>&1
REM Stop Node.js processes (Crucix + Osiris)
for /f "tokens=2" %%a in ('netstat -ano ^| findstr ":3117 " ^| findstr "LISTENING" 2^>nul') do taskkill /f /pid %%a >nul 2>&1
for /f "tokens=2" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING" 2^>nul') do taskkill /f /pid %%a >nul 2>&1
if exist "stop.bat" call "stop.bat"
echo [Origin] Stopped (including Crucix + Osiris).
exit /b 0

:STATUS
>nul 2>&1 curl --max-time 2 -s http://127.0.0.1:9001/health && (
    echo [Origin]  RUNNING - backend at http://localhost:9001
) || (
    echo [Origin]  STOPPED
)
>nul 2>&1 curl --max-time 2 -s http://127.0.0.1:3117/api/health && (
    echo [Crucix]  RUNNING - dashboard at http://localhost:3117
) || (
    echo [Crucix]  STOPPED
)
>nul 2>&1 curl --max-time 2 -s http://127.0.0.1:3000/api/health && (
    echo [Osiris]  RUNNING - dashboard at http://localhost:3000
) || (
    echo [Osiris]  STOPPED
)
tasklist /fi "imagename eq origin.exe" 2>nul | find /i "origin.exe" >nul && echo [Origin] Native shell active: origin.exe
tasklist /fi "imagename eq origin-shell.exe" 2>nul | find /i "origin-shell.exe" >nul && echo [Origin] Native shell active: origin-shell.exe
exit /b 0

:OPEN
tasklist /fi "imagename eq origin.exe" 2>nul | find /i "origin.exe" >nul && (
    echo [Origin] Native shell already active. Use Ctrl+Alt+H to show or hide.
    exit /b 0
)
tasklist /fi "imagename eq origin-shell.exe" 2>nul | find /i "origin-shell.exe" >nul && (
    echo [Origin] Native shell already active. Use Ctrl+Alt+H to show or hide.
    exit /b 0
)
start http://localhost:9001
exit /b 0

:CRUCIX
echo [Origin] Starting Crucix Intelligence Engine on port 3117...
cd /d "%~dp0crucix"
if not exist "node_modules" (
    echo [Origin] Installing Crucix dependencies...
    npm install
)
start "" /b cmd /c "node server.mjs"
timeout /t 3 /nobreak >nul
start http://localhost:3117
echo [Origin] Crucix running at http://localhost:3117
cd /d "%~dp0"
exit /b 0

:OSIRIS
echo [Origin] Starting Osiris OSINT Dashboard on port 3000...
cd /d "%~dp0osiris"
if not exist "node_modules" (
    echo [Origin] Installing Osiris dependencies...
    npm install
)
start "" /b cmd /c "npm run dev"
timeout /t 5 /nobreak >nul
start http://localhost:3000
echo [Origin] Osiris running at http://localhost:3000
cd /d "%~dp0"
exit /b 0

:INTEL
echo [Origin] Starting full intelligence stack (Crucix + Osiris)...
echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║         ORIGIN INTELLIGENCE SUITE            ║
echo  ╠══════════════════════════════════════════════╣
echo  ║  Crucix:  http://localhost:3117              ║
echo  ║  Osiris:  http://localhost:3000              ║
echo  ║  Origin:  http://localhost:9001              ║
echo  ╚══════════════════════════════════════════════╝
echo.
cd /d "%~dp0crucix"
if not exist "node_modules" (
    echo [Origin] Installing Crucix dependencies...
    npm install
)
start "" /b cmd /c "node server.mjs"
cd /d "%~dp0osiris"
if not exist "node_modules" (
    echo [Origin] Installing Osiris dependencies...
    npm install
)
start "" /b cmd /c "npm run dev"
cd /d "%~dp0"
timeout /t 5 /nobreak >nul
start http://localhost:3117
start http://localhost:3000
echo [Origin] Intelligence stack running.
exit /b 0

:SETUP
echo [Origin] Installing Windows integration...
call :FIND_PYTHON
if not defined ORIGIN_PY (
    echo [ERROR] No portable Python found in venv or .uv-python
    exit /b 1
)
"!ORIGIN_PY!" "scripts\install_origin.py"
exit /b 0
