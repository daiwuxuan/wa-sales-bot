@echo off
chcp 936 >nul
title WhatsApp AI 销售机器人 - 启动

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "PY=%BACKEND%\.venv\Scripts\python.exe"

echo.
echo ============================================================
echo   WhatsApp AI 销售机器人
echo ============================================================
echo.

if not exist "%PY%" (
    echo [错误] 找不到 Python 虚拟环境
    echo        预期位置：%PY%
    echo.
    echo   请先在 backend 目录执行一次：
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "%FRONTEND%\node_modules" (
    echo [错误] 前端依赖没有安装
    echo.
    echo   请先在 frontend 目录执行一次：npm install
    echo.
    pause
    exit /b 1
)

REM 端口占用检查。
REM 用 Get-NetTCPConnection 而不是 netstat + findstr：
REM netstat 的输出会匹配到「远端地址」里的同号端口，容易误判。
call :port_in_use 8000
if not errorlevel 1 (
    echo [错误] 端口 8000 已被占用，后端起不来
    echo        先跑一次 stop.bat，或查一下是谁占着：
    echo        Get-NetTCPConnection -LocalPort 8000 -State Listen
    echo.
    pause
    exit /b 1
)

call :port_in_use 3000
if not errorlevel 1 (
    echo [错误] 端口 3000 已被占用，前端起不来
    echo        多半是上一次的 npm run dev 没关干净，先跑一次 stop.bat
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%data\app.db" (
    echo [初始化] 数据库不存在，正在灌入演示数据...
    pushd "%BACKEND%"
    "%PY%" seed.py
    if errorlevel 1 (
        echo [错误] 灌数据失败，请看上面的报错
        popd
        pause
        exit /b 1
    )
    popd
    echo.
)

REM /D 指定工作目录，省得在命令里嵌套引号 —— 嵌套引号很容易把路径拆坏。
REM 两个服务各自开一个窗口，日志能看见、Ctrl+C 就能单独停。
echo [启动] 后端  http://127.0.0.1:8000
start "后端 :8000" /D "%BACKEND%" cmd /k ""%PY%" -m uvicorn app.main:app --reload --port 8000"

echo [启动] 前端  http://localhost:3000
start "前端 :3000" /D "%FRONTEND%" cmd /k "npm run dev"

echo.
echo [等待] 后端就绪...
call :waitup 8000 45
set "OK_B=%errorlevel%"
echo [等待] 前端就绪（首次编译比较慢，最多等 3 分钟）...
call :waitup 3000 180
set "OK_F=%errorlevel%"

echo.
echo ============================================================
if "%OK_B%"=="0" (echo   后端  [OK] 已就绪   http://127.0.0.1:8000) else (echo   后端  [X]  没起来    看「后端 :8000」窗口的报错)
if "%OK_F%"=="0" (echo   前端  [OK] 已就绪   http://localhost:3000) else (echo   前端  [X]  没起来    看「前端 :3000」窗口的报错)
echo ============================================================
echo.
if "%OK_B%%OK_F%"=="00" (
    echo   浏览器打开： http://localhost:3000
    echo.
    echo   一定要用 localhost，不要用 127.0.0.1。
    echo   Next 16 会把 127.0.0.1 当成跨源并拦掉 dev 资源，
    echo   现象是页面能显示、但完全点不动、数据一直转圈。
    echo.
) else (
    echo   有服务没起来。两个服务窗口是独立开的，
    echo   去对应窗口看报错，修好后重新跑 start.bat 即可。
    echo.
)
echo   停止服务：双击 stop.bat
echo ============================================================
echo.
pause
exit /b 0


:port_in_use
REM 端口被占用返回 0，空闲返回 1。
REM 这样调用方写 if not errorlevel 1 就是「被占用了」，读起来不绕。
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %~1 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
exit /b %errorlevel%


:waitup
REM %~1 = 端口, %~2 = 超时秒数。起来了返回 0，超时返回 1。
REM ★ 整个等待在**一个** PowerShell 进程里轮询完成。
REM   不要写成 batch 循环里每轮起一个 PowerShell —— 每个进程要 1~2 秒启动，
REM   90 轮下来就是好几分钟，等待本身比服务启动还慢。
powershell -NoProfile -Command "$end=(Get-Date).AddSeconds(%~2); while((Get-Date) -lt $end){ try { $null=Invoke-WebRequest 'http://localhost:%~1' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { Start-Sleep -Milliseconds 500 } }; exit 1" >nul 2>&1
exit /b %errorlevel%
