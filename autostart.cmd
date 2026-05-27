@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "ORIGIN_PY="
if exist "%~dp0venv\Scripts\pythonw.exe" if not defined ORIGIN_PY "%~dp0venv\Scripts\pythonw.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0venv\Scripts\pythonw.exe"
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe" if not defined ORIGIN_PY "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\pythonw.exe"
if exist "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" if not defined ORIGIN_PY "%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe" --version >nul 2>&1 && set "ORIGIN_PY=%~dp0.uv-python\cpython-3.11.15-windows-x86_64-none\python.exe"
if not defined ORIGIN_PY exit /b 1

start "" /b "!ORIGIN_PY!" "%~dp0origin_launcher.pyw"
endlocal
exit /b 0
