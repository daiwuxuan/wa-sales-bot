# 停止本项目的服务。
#
# 为什么不直接在 .bat 里写：
#   PowerShell 命令嵌在 cmd 的双引号里，反斜杠和引号要转义两层，
#   极容易改坏而且坏了不报错。抽成独立文件后可以单独跑、单独测。
#
# 为什么不能只杀 netstat 报的那个 PID：
#   uvicorn --reload 是「reloader 父进程 + worker 子进程」两个进程。
#   父进程持有监听套接字并把它传给子进程。杀掉父进程后，子进程变成孤儿，
#   却**仍然继承着那个套接字**，于是端口继续服务。
#   更麻烦的是 netstat 这时还报着已死的父进程 PID，按那个 PID 怎么也杀不掉。
#
#   这类孤儿的命令行是：
#     python -c "from multiprocessing.spawn import spawn_main;
#                spawn_main(parent_pid=11816, ...)" "--multiprocessing-fork"
#   注意它既不含项目路径也不含 "uvicorn"，只能靠「父进程已死」来识别。

param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'SilentlyContinue'
$ProjectRoot = $ProjectRoot.TrimEnd('\')

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

# ── 第一轮：按端口杀掉占用者及其整个进程树 ──────────────
foreach ($port in 8000, 3000) {
    $label = if ($port -eq 8000) { '后端' } else { '前端' }

    if (-not (Test-PortListening $port)) {
        Write-Host "  [本来就空闲]  $label  端口 $port"
        continue
    }

    # taskkill /T 连子进程一起杀 —— 这能覆盖大部分情况
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { & taskkill /PID $_ /T /F 2>&1 | Out-Null }

    Start-Sleep -Milliseconds 300
    Write-Host "  [已停止]      $label  端口 $port"
}

# ── 第二轮：清扫第一轮漏掉的孤儿进程 ────────────────────
# 识别条件（满足其一）：
#   a) 命令行里引用了本项目路径        —— 直接就是我们的进程
#   b) 是孤儿，且命令行含 uvicorn/next/multiprocessing
#      —— uvicorn 的 reload worker 就长这样
# 用「父进程已死」而不是「命令行含项目名」来识别 (b)，
# 是因为那个 worker 的命令行里根本没有项目路径。
$swept = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='node.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return }

        $mentionsProject = $cmd -like "*$ProjectRoot*"

        $parentAlive = $false
        if ($_.ParentProcessId) {
            $parentAlive = [bool](Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue)
        }
        $isOrphanWorker = (-not $parentAlive) -and ($cmd -match 'uvicorn|next|multiprocessing')

        if ($mentionsProject -or $isOrphanWorker) {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $swept += "$($_.ProcessId) $($_.Name)"
        }
    }

if ($swept.Count -gt 0) {
    Write-Host "  [额外清理]    结束了 $(($swept -join ', '))"
}

# ── 结果 ────────────────────────────────────────────────
Start-Sleep -Milliseconds 500
$stillUp = @(8000, 3000 | Where-Object { Test-PortListening $_ })

if ($stillUp.Count -eq 0) {
    Write-Host ""
    Write-Host "  服务已全部停止。"
    exit 0
}

# 还占着 —— 明确报出来，而不是假装成功。
# 「报了成功但端口还在」比「报错」糟糕得多，因为人会以为自己已经停干净了。
Write-Host ""
Write-Host "  [警告] 以下端口仍在监听：$($stillUp -join ', ')"
Write-Host "         可能有以管理员权限启动的进程，需要你手动处理："
foreach ($port in $stillUp) {
    $owner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
    Write-Host "           taskkill /PID $owner /T /F     (端口 $port)"
}
exit 1
