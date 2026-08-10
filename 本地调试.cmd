@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "MANAGER=%~dp0tools\local_debug.ps1"

if "%~1"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action menu
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action "%~1"
)

exit /b %ERRORLEVEL%
