@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "MANAGER=%~dp0tools\local_debug.ps1"
if not "%~1"=="" goto action
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Profile no-schedule -Action launch
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
:action
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Profile no-schedule -Action "%~1"
exit /b %ERRORLEVEL%
