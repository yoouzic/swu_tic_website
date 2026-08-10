@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "MANAGER=%~dp0tools\local_debug.ps1"

if not "%~1"=="" goto argument_mode

:menu
cls
echo ========================================
echo        西大听课工作台 - 本地调试
echo ========================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action status
echo.
echo [1] 启动服务
echo [2] 关闭服务
echo [3] 重启服务
echo [4] 查看运行状态
echo [5] 打开本地网页
echo [0] 退出
echo.
set "MENU_CHOICE="
set /p "MENU_CHOICE=请输入操作编号："
if "%MENU_CHOICE%"=="1" call :run start
if "%MENU_CHOICE%"=="2" call :run stop
if "%MENU_CHOICE%"=="3" call :run restart
if "%MENU_CHOICE%"=="4" call :run status
if "%MENU_CHOICE%"=="5" call :run open
if "%MENU_CHOICE%"=="0" exit /b 0
if not "%MENU_CHOICE%"=="1" if not "%MENU_CHOICE%"=="2" if not "%MENU_CHOICE%"=="3" if not "%MENU_CHOICE%"=="4" if not "%MENU_CHOICE%"=="5" echo 输入无效，请输入 0-5。
echo.
pause
goto menu

:argument_mode
if /i "%~1"=="start" goto arg_start
if /i "%~1"=="stop" goto arg_stop
if /i "%~1"=="restart" goto arg_restart
if /i "%~1"=="status" goto arg_status
if /i "%~1"=="open" goto arg_open
echo 未知参数：%~1
echo 支持：start、stop、restart、status、open
exit /b 2

:arg_start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action start
exit /b %ERRORLEVEL%
:arg_stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action stop
exit /b %ERRORLEVEL%
:arg_restart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action restart
exit /b %ERRORLEVEL%
:arg_status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action status
exit /b %ERRORLEVEL%
:arg_open
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action open
exit /b %ERRORLEVEL%

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action %~1
exit /b %ERRORLEVEL%
