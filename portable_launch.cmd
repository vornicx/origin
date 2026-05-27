@echo off
:: portable_launch.cmd — Lanza Origin desde cualquier ruta o unidad (pendrive, disco externo, etc.)
::
:: Uso:
::   portable_launch.cmd          — arranca en modo browser (recomendado para pendrive)
::   portable_launch.cmd check    — verifica el entorno antes de arrancar
::   portable_launch.cmd stop     — detiene el backend
::   portable_launch.cmd setup    — instala dependencias desde cero
::
:: NO requiere instalación de Python en el sistema.
:: NO requiere modificar el registro ni el PATH.
:: Funciona desde cualquier letra de unidad (C:\, E:\, F:\, etc.)

setlocal enabledelayedexpansion
cd /d "%~dp0"

if /i "%1"=="check"  goto :CHECK
if /i "%1"=="stop"   goto :STOP
if /i "%1"=="status" goto :STATUS
if /i "%1"=="setup"  goto :SETUP
goto :LAUNCH

:: ─── Detect Python ───────────────────────────────────────────────────────────
:FIND_PY
set "ORIGIN_PY="
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe" (
    "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe" --version >nul 2>&1
    if not errorlevel 1 set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe"
)
if not defined ORIGIN_PY (
    if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" (
        "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" --version >nul 2>&1
        if not errorlevel 1 set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe"
    )
)
if not defined ORIGIN_PY (
    if exist "%~dp0.uv-python\cpython-3.11-windows-x86_64-none\pythonw.exe" (
        "%~dp0.uv-python\cpython-3.11-windows-x86_64-none\pythonw.exe" --version >nul 2>&1
        if not errorlevel 1 set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11-windows-x86_64-none\pythonw.exe"
    )
)
if not defined ORIGIN_PY (
    if exist "%~dp0.uv-python\cpython-3.11-windows-x86_64-none\python.exe" (
        "%~dp0.uv-python\cpython-3.11-windows-x86_64-none\python.exe" --version >nul 2>&1
        if not errorlevel 1 set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11-windows-x86_64-none\python.exe"
    )
)
if not defined ORIGIN_PY (
    if exist "%~dp0venv\Scripts\pythonw.exe" (
        "%~dp0venv\Scripts\pythonw.exe" --version >nul 2>&1
        if not errorlevel 1 set "ORIGIN_PY=%~dp0venv\Scripts\pythonw.exe"
    )
)
if not defined ORIGIN_PY (
    if exist "%~dp0venv\Scripts\python.exe" (
        "%~dp0venv\Scripts\python.exe" --version >nul 2>&1
        if not errorlevel 1 set "ORIGIN_PY=%~dp0venv\Scripts\python.exe"
    )
)
exit /b 0

:FIND_PY_CONSOLE
set "ORIGIN_PY_C="
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" (
    "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" --version >nul 2>&1
    if not errorlevel 1 set "ORIGIN_PY_C=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe"
)
if not defined ORIGIN_PY_C if exist "%~dp0.uv-python\cpython-3.11-windows-x86_64-none\python.exe" (
    "%~dp0.uv-python\cpython-3.11-windows-x86_64-none\python.exe" --version >nul 2>&1
    if not errorlevel 1 set "ORIGIN_PY_C=%~dp0.uv-python\cpython-3.11-windows-x86_64-none\python.exe"
)
if not defined ORIGIN_PY_C if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" --version >nul 2>&1
    if not errorlevel 1 set "ORIGIN_PY_C=%~dp0venv\Scripts\python.exe"
)
exit /b 0

:: ─── Auto-Fix: pyvenv.cfg + .env ─────────────────────────────────────────────
:AUTO_FIX
echo [INFO] Verificando entorno portatil...
call :FIND_PY_CONSOLE
if defined ORIGIN_PY_C (
    :: Fix pyvenv.cfg paths para que apunten al Python portatil
    set "SITE=%~dp0venv\Lib\site-packages"
    set "PYTHONPATH=!SITE!;!SITE!\win32;!SITE!\win32\lib;!SITE!\Pythonwin"
    "!ORIGIN_PY_C!" "%~dp0portable_check.py" --fix >nul 2>&1
    if not errorlevel 1 echo [OK]  pyvenv.cfg actualizado al Python portatil
)
:: Auto-crear .env si no existe
if not exist "%~dp0.env" (
    if exist "%~dp0.env.example" (
        copy /y "%~dp0.env.example" "%~dp0.env" >nul
        echo [INFO] .env creado desde .env.example
        echo [INFO] Edita .env con tus API keys antes de usar Origin
    )
)
exit /b 0

:: ─── Launch ──────────────────────────────────────────────────────────────────
:LAUNCH
echo.
echo  O R I G I N  —  Portable Launch
echo  Root: %~dp0
echo.

call :AUTO_FIX

call :FIND_PY
if not defined ORIGIN_PY (
    echo [ERROR] No se encontro Python portable.
    echo         Asegurate de que .uv-python\cpython-3.11.x-windows-x86_64-none\ esta presente.
    echo         Si es la primera vez, ejecuta: portable_launch.cmd setup
    pause
    exit /b 1
)

echo [INFO] Python: !ORIGIN_PY!
echo [INFO] Iniciando backend en http://localhost:9001 ...
echo [INFO] (Abre el navegador en http://localhost:9001 cuando veas "Application startup complete")
echo.

set "SITE=%~dp0venv\Lib\site-packages"
set "PYTHONPATH=!SITE!;!SITE!\win32;!SITE!\win32\lib;!SITE!\Pythonwin"
start "" /b "!ORIGIN_PY!" "%~dp0origin_launcher.pyw"

echo [INFO] Esperando servidor...
timeout /t 4 /nobreak >nul
start http://localhost:9001
exit /b 0

:: ─── Check ───────────────────────────────────────────────────────────────────
:CHECK
call :FIND_PY_CONSOLE
if not defined ORIGIN_PY_C (
    echo [ERROR] No se encontro Python portable para ejecutar portable_check.py
    pause
    exit /b 1
)
set "SITE=%~dp0venv\Lib\site-packages"
set "PYTHONPATH=!SITE!;!SITE!\win32;!SITE!\win32\lib;!SITE!\Pythonwin"
"!ORIGIN_PY_C!" "%~dp0portable_check.py" %2 %3
exit /b !errorlevel!

:: ─── Status ──────────────────────────────────────────────────────────────────
:STATUS
>nul 2>&1 curl --max-time 2 -s http://127.0.0.1:9001/health && (
    echo [Origin] RUNNING - http://localhost:9001
) || (
    echo [Origin] STOPPED
)
exit /b 0

:: ─── Stop ────────────────────────────────────────────────────────────────────
:STOP
echo [INFO] Deteniendo Origin...
taskkill /f /im pythonw.exe >nul 2>&1
taskkill /f /im python.exe /fi "windowtitle eq origin*" >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":9001" 2^>nul') do (
    taskkill /f /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":9001" 2^>nul') do (
    taskkill /f /pid %%a >nul 2>&1
)
echo [INFO] Detenido.
exit /b 0

:: ─── Setup (install desde cero) ──────────────────────────────────────────────
:SETUP
echo.
echo  O R I G I N  —  Setup Completo
echo  Root: %~dp0
echo.

call :FIND_PY_CONSOLE
if not defined ORIGIN_PY_C (
    echo [ERROR] No se encontro Python portable en .uv-python\
    echo         Descarga Python 3.11 embeddable y extraelo en:
    echo         %~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\
    pause
    exit /b 1
)

:: Crear .env si no existe
if not exist "%~dp0.env" (
    if exist "%~dp0.env.example" (
        copy /y "%~dp0.env.example" "%~dp0.env" >nul
        echo [INFO] .env creado desde .env.example
    )
)

:: Instalar dependencias
echo [INFO] Instalando dependencias Python...
set "SITE=%~dp0venv\Lib\site-packages"
set "PYTHONPATH=!SITE!;!SITE!\win32;!SITE!\win32\lib;!SITE!\Pythonwin"
if not exist "!SITE!\fastapi" (
    "!ORIGIN_PY_C!" -m pip install -r "%~dp0requirements.txt" --target "!SITE!" --upgrade
    if errorlevel 1 (
        echo [ERROR] Fallo la instalacion de dependencias
        pause
        exit /b 1
    )
    echo [OK]  Dependencias instaladas
)

:: Frontend
if exist "%~dp0frontend\node_modules" (
    echo [OK]  Frontend ya instalado
) else (
    echo [INFO] Instalando dependencias del frontend...
    cd /d "%~dp0frontend"
    call npm install
    cd /d "%~dp0"
)

:: Verificacion final
echo [INFO] Verificando instalacion...
call :AUTO_FIX
"!ORIGIN_PY_C!" "%~dp0portable_check.py" --summary

echo.
echo  Setup completado. Ejecuta portable_launch.cmd para iniciar Origin.
echo.
pause
exit /b 0
