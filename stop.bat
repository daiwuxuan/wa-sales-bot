@echo off
chcp 936 >nul
title WhatsApp AI 销售机器人 - 停止

echo.
echo ============================================================
echo   正在停止服务
echo ============================================================
echo.

REM 真正的逻辑在 scripts\stop_services.ps1 里。
REM 抽出去是因为：PowerShell 命令嵌在 cmd 的双引号里要转义两层反斜杠和引号，
REM 太长很容易改坏，而且坏了不报错。独立文件还能单独跑、单独测。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_services.ps1"
set "RC=%errorlevel%"

echo.
echo ============================================================
if "%RC%"=="0" (
    echo   数据是安全的：SQLite 用的是 WAL 模式，
    echo   强杀不会损坏数据库，未提交的事务会自动回滚。
) else (
    echo   有端口没能停掉，看上面的提示。
)
echo ============================================================
echo.
pause
exit /b %RC%
