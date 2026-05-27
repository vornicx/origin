@echo off
:: portable_to_usb.cmd — Copia Origin a un pendrive o carpeta de destino.
::
:: Uso:
::   portable_to_usb.cmd E:\Origin        — copia a E:\Origin
::   portable_to_usb.cmd D:\Proyectos\AI  — copia a cualquier destino
::
:: Requiere ~2.1 GB libres (pendrive 4 GB mínimo, 8 GB recomendado).
:: Usa robocopy (incluido en Windows 7+) para copiar solo cambios.
::
:: Excluye (no son necesarios para ejecutar Origin):
::   - frontend\node_modules   (179 MB, reconstruible con npm install)
::   - frontend\src-tauri\target (build artifacts Rust, reconstruible)
::   - .uv-cache               (caché de descargas)
::   - __pycache__             (caché Python)
::   - .pytest_cache
::   - *.pyc / *.pyo

setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%~1"=="" (
    echo.
    echo  Uso: portable_to_usb.cmd ^<DESTINO^>
    echo  Ejemplo: portable_to_usb.cmd E:\Origin
    echo.
    echo  Tamanio estimado: ~2.1 GB  ^(pendrive 4 GB minimo^)
    echo.
    set /p DEST="Introduce la ruta de destino: "
) else (
    set "DEST=%~1"
)

if "!DEST!"=="" (
    echo [ERROR] No se especifico destino.
    pause
    exit /b 1
)

echo.
echo  Origen : %~dp0
echo  Destino: !DEST!
echo.
echo  Copiando... ^(puede tardar varios minutos la primera vez^)
echo  Actualizaciones posteriores solo copian cambios ^(robocopy /mir^)
echo.

:: ─── Copia principal ─────────────────────────────────────────────────────────
robocopy "%~dp0" "!DEST!" /MIR /R:1 /W:1 /NP /NFL /NDL ^
    /XD "%~dp0frontend\node_modules" ^
    /XD "%~dp0frontend\src-tauri\target" ^
    /XD "%~dp0.uv-cache" ^
    /XD "%~dp0__pycache__" ^
    /XD "%~dp0.pytest_cache" ^
    /XF "*.pyc" "*.pyo" "*.bak" ^
    /LOG+:"%TEMP%\origin_usb_copy.log"

:: robocopy exits 0-7 = success (8+ = errors)
if !errorlevel! GEQ 8 (
    echo.
    echo [ERROR] robocopy fallo ^(codigo !errorlevel!^). Revisa el log:
    echo         %TEMP%\origin_usb_copy.log
    pause
    exit /b 1
)

echo.
echo [OK] Copia completada.
echo.

:: ─── Verificar en destino ─────────────────────────────────────────────────────
if exist "!DEST!\portable_check.py" if exist "!DEST!\.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" (
    echo  Ejecutando portable_check en el destino...
    set "SITE=!DEST!\venv\Lib\site-packages"
    set "PYTHONPATH=!SITE!;!SITE!\win32;!SITE!\win32\lib;!SITE!\Pythonwin"
    "!DEST!\.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" "!DEST!\portable_check.py" --summary
    echo.
)

echo  Pendrive listo. En el ordenador de destino:
echo    1. Abre la carpeta del pendrive
echo    2. Haz doble clic en  portable_launch.cmd
echo    3. Abre el navegador en  http://localhost:9001
echo.
echo  O desde CMD/PowerShell:
echo    cd /d !DEST!
echo    origin dev-web
echo.
pause
