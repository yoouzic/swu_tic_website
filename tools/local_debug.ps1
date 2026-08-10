param(
    [ValidateSet('start', 'stop', 'restart', 'status', 'open')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RuntimeRoot = Join-Path $RepoRoot 'data\instance\debug'
$StorageRoot = Join-Path $RepoRoot 'data\storage\debug'
$PidPath = Join-Path $RuntimeRoot 'local-debug.pid'
$StatePath = Join-Path $RuntimeRoot 'local-debug-state.json'
$DatabasePath = Join-Path $RuntimeRoot 'lecture_forms-debug.db'
$LogPath = Join-Path $StorageRoot 'logs\local-debug.log'
$Url = 'http://127.0.0.1:5000'
$Port = 5000
$SharedPassword = '1234564'

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
    & $Python.Exe @($Python.Prefix) @Arguments
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
    } catch { return $false } finally { $client.Dispose() }
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

function Show-Status {
    $recordedPid = Read-RecordedPid
    $processUp = $recordedPid -and (Test-RecordedProcess $recordedPid)
    $httpUp = Test-Http
    Write-Host ('PID: ' + $(if ($recordedPid) { $recordedPid } else { '-' }))
    Write-Host ('进程状态: ' + $(if ($processUp) { '运行中' } else { '未运行' }))
    Write-Host ('端口状态: ' + $(if (Test-TcpPort) { '已占用' } else { '空闲' }))
    Write-Host ('网页状态: ' + $(if ($httpUp) { '可访问' } else { '不可访问' }))
    Write-Host ('访问地址: ' + $Url)
    if (-not $processUp -and $recordedPid) { Remove-StaleState }
    return $processUp -and $httpUp
}

function Start-LocalDebug {
    Set-DebugEnvironment
    $recordedPid = Read-RecordedPid
    if ($recordedPid -and (Test-RecordedProcess $recordedPid)) {
        Write-Host "本地调试服务已经运行，PID=$recordedPid"
        return
    }
    if ($recordedPid) { Remove-StaleState }
    if (Test-TcpPort) { throw 'Port 5000 is occupied by another process; no process was terminated.' }

    $python = Resolve-PythonCommand
    Invoke-PythonCommand $python @('tools/prepare_local_debug.py', '--password', $SharedPassword)

    $prefixText = ($python.Prefix | ForEach-Object { $_ }) -join ' '
    $pythonText = '"' + $python.Exe + '"'
    if ($prefixText) { $pythonText += ' ' + $prefixText }
    $serverCommand = "title 西大听课工作台 - 本地调试日志 && cd /d `"$RepoRoot`" && $pythonText -u tools/local_debug_server.py"
    $process = Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', $serverCommand -WorkingDirectory $RepoRoot -PassThru
    Set-Content -LiteralPath $PidPath -Value $process.Id -Encoding ascii
    [pscustomobject]@{
        pid = $process.Id
        startedAt = (Get-Date).ToString('o')
        url = $Url
        python = $python.Exe
        database = $DatabasePath
    } | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8

    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-Http) {
            Write-Host "服务已启动：$Url"
            Write-Host '测试账号：super / manager / user001，密码均为 1234564'
            Start-Process $Url
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Flask did not become reachable within 30 seconds. Check $LogPath"
}

function Stop-LocalDebug {
    $recordedPid = Read-RecordedPid
    if (-not $recordedPid) {
        Write-Host '没有记录到由启动器创建的服务。'
        Remove-StaleState
        return
    }
    if (Test-RecordedProcess $recordedPid) {
        & taskkill.exe /PID $recordedPid /T /F | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Unable to stop recorded process tree PID=$recordedPid" }
    }
    Remove-StaleState
    for ($attempt = 0; $attempt -lt 20 -and (Test-TcpPort); $attempt++) {
        Start-Sleep -Milliseconds 250
    }
    if (Test-TcpPort) { throw 'Recorded process stopped but port 5000 is still occupied.' }
    Write-Host '本地调试服务已关闭；调试数据库和日志已保留。'
}

try {
    Push-Location $RepoRoot
    switch ($Action) {
        'start' { Start-LocalDebug }
        'stop' { Stop-LocalDebug }
        'restart' { Stop-LocalDebug; Start-LocalDebug }
        'status' { [void](Show-Status) }
        'open' { Start-Process $Url; Write-Host "已打开：$Url" }
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
} finally {
    Pop-Location
}
