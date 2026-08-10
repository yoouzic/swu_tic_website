param(
    [string]$Action = 'menu'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
function Resolve-DebugRoot([string]$Override, [string]$Fallback) {
    if ([string]::IsNullOrWhiteSpace($Override)) {
        return [IO.Path]::GetFullPath($Fallback)
    }
    return [IO.Path]::GetFullPath($Override)
}

$RuntimeRoot = Resolve-DebugRoot $env:LOCAL_DEBUG_RUNTIME_ROOT (Join-Path $RepoRoot 'data\instance\debug')
$StorageRoot = Resolve-DebugRoot $env:LOCAL_DEBUG_STORAGE_ROOT (Join-Path $RepoRoot 'data\storage\debug')
$PidPath = Join-Path $RuntimeRoot 'local-debug.pid'
$StatePath = Join-Path $RuntimeRoot 'local-debug-state.json'
$DatabasePath = Join-Path $RuntimeRoot 'lecture_forms-debug.db'
$LogPath = Join-Path $StorageRoot 'logs\local-debug.log'
$DefaultPort = 5087
$SharedPassword = '1234564'
$Port = $null
$Url = $null

function Resolve-DebugPort {
    $configured = $env:LOCAL_DEBUG_PORT
    if ([string]::IsNullOrWhiteSpace($configured)) { return $DefaultPort }
    $parsed = 0
    if (
        -not [int]::TryParse($configured.Trim(), [ref]$parsed) -or
        $parsed -lt 1 -or
        $parsed -gt 65535
    ) {
        throw 'LOCAL_DEBUG_PORT must be an integer between 1 and 65535'
    }
    return $parsed
}

function Resolve-PythonCommand {
    $candidates = @()
    $localVenv = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    $siblingVenv = Join-Path (Split-Path $RepoRoot -Parent) 'SWU_TIC-main\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localVenv) {
        $candidates += [pscustomobject]@{ Exe = $localVenv; Prefix = @() }
    }
    if (Test-Path -LiteralPath $siblingVenv) {
        $candidates += [pscustomobject]@{ Exe = $siblingVenv; Prefix = @() }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += [pscustomobject]@{ Exe = 'py'; Prefix = @('-3') }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $candidates += [pscustomobject]@{ Exe = 'python'; Prefix = @() }
    }
    foreach ($candidate in $candidates) {
        & $candidate.Exe @($candidate.Prefix) -c 'import flask, flask_sqlalchemy, werkzeug' 2>$null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    throw 'No Python interpreter with project dependencies was found. Install requirements.txt first.'
}

function Set-DebugEnvironment {
    New-Item -ItemType Directory -Force -Path $RuntimeRoot, (Join-Path $StorageRoot 'uploads'), (Split-Path $LogPath -Parent) | Out-Null
    $env:LOCAL_DEBUG_MODE = '1'
    $env:APP_ENV = 'development'
    $env:FLASK_DEBUG = '1'
    $env:FLASK_RUN_HOST = '127.0.0.1'
    $env:FLASK_RUN_PORT = [string]$Port
    $env:INSTANCE_DIR = $RuntimeRoot
    $env:SQLITE_DB_PATH = $DatabasePath
    $env:UPLOAD_FOLDER = Join-Path $StorageRoot 'uploads'
    $env:LOCAL_DEBUG_LOG_PATH = $LogPath
    $env:DATABASE_URL = ''
    $env:SECRET_KEY = 'local-debug-only-secret'
    $env:PYTHONUTF8 = '1'
}

function Invoke-PythonCommand($Python, [string[]]$Arguments) {
    $output = & $Python.Exe @($Python.Prefix) @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
}

function Read-RecordedPid {
    if (-not (Test-Path -LiteralPath $PidPath)) { return $null }
    $raw = (Get-Content -LiteralPath $PidPath -Raw).Trim()
    $parsed = 0
    if (-not [int]::TryParse($raw, [ref]$parsed)) {
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $parsed
}

function Test-RecordedProcess([int]$ProcessId) {
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Test-TcpPort {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return $task.Wait(300) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Test-Http {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 -MaximumRedirection 0 -ErrorAction Stop
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -lt 400) { return $true }
        return $false
    }
}

function Remove-StaleState {
    Remove-Item -LiteralPath $PidPath, $StatePath -Force -ErrorAction SilentlyContinue
}

function Get-PortOccupancyMessage {
    $connection = $null
    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    } catch {
        $connection = $null
    }
    if ($connection) {
        $owner = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
        if ($owner) {
            return "端口 $Port 已被 $($owner.ProcessName)（PID $($owner.Id)）占用；未终止该进程。"
        }
    }
    return "端口 $Port 已被其他程序占用；未终止任何进程。"
}

function Write-MenuResult([string]$Kind, [string]$Message) {
    $color = switch ($Kind) {
        '成功' { 'Green' }
        '失败' { 'Red' }
        default { 'Yellow' }
    }
    Write-Host "[$Kind] $Message" -ForegroundColor $color
}

function Get-DebugStatus {
    $recordedPid = Read-RecordedPid
    $processUp = [bool]($recordedPid -and (Test-RecordedProcess $recordedPid))
    if (-not $processUp -and $recordedPid) {
        Remove-StaleState
        $recordedPid = $null
    }
    $portUp = Test-TcpPort
    $httpUp = Test-Http
    $label = if ($processUp -and $httpUp) {
        '运行中'
    } elseif (-not $processUp -and -not $portUp -and -not $httpUp) {
        '未运行'
    } else {
        '状态异常'
    }
    [pscustomobject]@{
        Pid = $recordedPid
        ProcessUp = $processUp
        PortUp = $portUp
        HttpUp = $httpUp
        Label = $label
        Url = $Url
    }
}

function Show-Status {
    $status = Get-DebugStatus
    Write-Host ('PID: ' + $(if ($status.Pid) { $status.Pid } else { '-' }))
    Write-Host ('进程状态: ' + $(if ($status.ProcessUp) { '运行中' } else { '未运行' }))
    Write-Host ('端口状态: ' + $(if ($status.PortUp) { '已占用' } else { '空闲' }))
    Write-Host ('网页状态: ' + $(if ($status.HttpUp) { '可访问' } else { '不可访问' }))
    Write-Host ('访问地址: ' + $status.Url)
    return $status.ProcessUp -and $status.HttpUp
}

function Start-LocalDebug {
    Set-DebugEnvironment
    $recordedPid = Read-RecordedPid
    if ($recordedPid -and (Test-RecordedProcess $recordedPid)) {
        Write-MenuResult '提示' "服务已经运行：$Url"
        return
    }
    if ($recordedPid) { Remove-StaleState }
    if (Test-TcpPort) { throw (Get-PortOccupancyMessage) }

    $python = Resolve-PythonCommand
    Invoke-PythonCommand $python @('tools/prepare_local_debug.py', '--password', $SharedPassword)

    $prefixText = ($python.Prefix | ForEach-Object { $_ }) -join ' '
    $pythonText = '"' + $python.Exe + '"'
    if ($prefixText) { $pythonText += ' ' + $prefixText }
    $serverCommand = "cd /d `"$RepoRoot`" && $pythonText -u tools/local_debug_server.py"
    $process = Start-Process `
        -FilePath 'cmd.exe' `
        -ArgumentList '/c', $serverCommand `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $PidPath -Value $process.Id -Encoding ascii
    [pscustomobject]@{
        pid = $process.Id
        startedAt = (Get-Date).ToString('o')
        port = $Port
        url = $Url
        python = $python.Exe
        database = $DatabasePath
    } | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8

    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-Http) {
            Write-MenuResult '成功' "服务已启动：$Url"
            Start-Process $Url
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Flask did not become reachable within 30 seconds. Check $LogPath"
}

function Stop-LocalDebug([switch]$Quiet) {
    $recordedPid = Read-RecordedPid
    if (-not $recordedPid) {
        Remove-StaleState
        if (-not $Quiet) { Write-MenuResult '提示' '服务尚未启动。' }
        return
    }

    $processUp = Test-RecordedProcess $recordedPid
    if ($processUp) {
        $taskKillOutput = & taskkill.exe /PID $recordedPid /T /F 2>&1
        if ($LASTEXITCODE -ne 0) { throw "无法关闭启动器记录的进程树 PID=$recordedPid" }
    }
    Remove-StaleState
    if ($processUp) {
        for ($attempt = 0; $attempt -lt 20 -and (Test-TcpPort); $attempt++) {
            Start-Sleep -Milliseconds 250
        }
        if (Test-TcpPort) { throw "记录进程已停止，但端口 $Port 仍被占用；未终止其他进程。" }
    }
    if (-not $Quiet) { Write-MenuResult '成功' '本地调试服务已关闭；调试数据库和日志已保留。' }
}

function Show-Menu {
    while ($true) {
        Clear-Host
        $status = Get-DebugStatus
        Write-Host 'SWU TIC 本地调试'
        Write-Host "状态：$($status.Label)"
        Write-Host "地址：$Url"
        Write-Host ''
        Write-Host '1  启动'
        Write-Host '2  关闭'
        Write-Host '3  重启'
        Write-Host '4  打开网页'
        Write-Host '0  退出'
        Write-Host ''
        $choice = Read-Host '请选择'
        if ($choice -eq '0') { return }
        try {
            switch ($choice) {
                '1' { Start-LocalDebug; break }
                '2' { Stop-LocalDebug; break }
                '3' { Stop-LocalDebug -Quiet; Start-LocalDebug; break }
                '4' {
                    if (-not (Test-Http)) { throw '服务尚未启动。' }
                    Start-Process $Url
                    Write-MenuResult '成功' "已打开：$Url"
                    break
                }
                default { Write-MenuResult '提示' '请输入 0-4。' }
            }
        } catch {
            Write-MenuResult '失败' $_.Exception.Message
        }
        [void](Read-Host '按回车返回')
    }
}

$locationPushed = $false
try {
    Push-Location $RepoRoot
    $locationPushed = $true
    $validActions = @('menu', 'start', 'stop', 'restart', 'status', 'open')
    if ($validActions -notcontains $Action) {
        throw "未知操作：$Action"
    }
    $Port = Resolve-DebugPort
    $Url = "http://127.0.0.1:$Port"
    switch ($Action) {
        'menu' { Show-Menu }
        'start' { Start-LocalDebug }
        'stop' { Stop-LocalDebug }
        'restart' { Stop-LocalDebug -Quiet; Start-LocalDebug }
        'status' { [void](Show-Status) }
        'open' {
            if (-not (Test-Http)) { throw '服务尚未启动。' }
            Start-Process $Url
            Write-MenuResult '成功' "已打开：$Url"
        }
    }
} catch {
    [Console]::Error.WriteLine("[失败] $($_.Exception.Message)")
    exit 1
} finally {
    if ($locationPushed) { Pop-Location }
}
