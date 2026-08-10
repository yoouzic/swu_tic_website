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
$ServerScriptPath = [IO.Path]::GetFullPath((Join-Path $RepoRoot 'tools\local_debug_server.py'))
$PidPath = Join-Path $RuntimeRoot 'local-debug.pid'
$StatePath = Join-Path $RuntimeRoot 'local-debug-state.json'
$DatabasePath = Join-Path $RuntimeRoot 'lecture_forms-debug.db'
$LogPath = Join-Path $StorageRoot 'logs\local-debug.log'
$ConsoleLogPath = Join-Path $StorageRoot 'logs\local-debug-console.log'
$DefaultPort = 5087
$SharedPassword = '1234564'
$HealthTimeoutSeconds = 30
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

function Resolve-HealthTimeout {
    $configured = $env:LOCAL_DEBUG_HEALTH_TIMEOUT_SECONDS
    if ([string]::IsNullOrWhiteSpace($configured)) { return 30 }
    $parsed = 0
    if (
        -not [int]::TryParse($configured.Trim(), [ref]$parsed) -or
        $parsed -lt 1 -or
        $parsed -gt 300
    ) {
        throw 'LOCAL_DEBUG_HEALTH_TIMEOUT_SECONDS must be an integer between 1 and 300'
    }
    return $parsed
}

function Resolve-PythonCommand {
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:LOCAL_DEBUG_PYTHON)) {
        $override = [IO.Path]::GetFullPath($env:LOCAL_DEBUG_PYTHON)
        if (-not (Test-Path -LiteralPath $override)) {
            throw "LOCAL_DEBUG_PYTHON was not found: $override"
        }
        $candidates += [pscustomobject]@{ Exe = $override; Prefix = @() }
    }
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
    try {
        $raw = (Get-Content -LiteralPath $PidPath -Raw -ErrorAction Stop).Trim()
        $parsed = 0
        if ([int]::TryParse($raw, [ref]$parsed) -and $parsed -gt 0) {
            return $parsed
        }
    } catch {
        return $null
    }
    return $null
}

function Read-RecordedState {
    if (-not (Test-Path -LiteralPath $StatePath)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $StatePath -Raw -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        return ($raw | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Normalize-CommandLine([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return '' }
    return (($CommandLine -replace '/', '\') -replace '\s+', ' ').Trim().ToLowerInvariant()
}

function Test-ServerCommandLine([string]$CommandLine) {
    $normalized = Normalize-CommandLine $CommandLine
    $serverPath = Normalize-CommandLine $ServerScriptPath
    return -not [string]::IsNullOrWhiteSpace($normalized) -and $normalized.Contains($serverPath)
}

function Get-CurrentProcessIdentity([int]$ProcessId) {
    if ($ProcessId -le 0) { return $null }
    if ($env:LOCAL_DEBUG_TEST_FORCE_IDENTITY_FAILURE -eq '1') { return $null }
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        $cimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if (-not $cimProcess -or [string]::IsNullOrWhiteSpace([string]$cimProcess.CommandLine)) {
            return $null
        }
        $startTimeUtc = $process.StartTime.ToUniversalTime()
        return [pscustomobject]@{
            Pid = [int]$process.Id
            ProcessStartTime = $startTimeUtc
            ProcessStartTimeText = $startTimeUtc.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
            CommandLine = [string]$cimProcess.CommandLine
        }
    } catch {
        return $null
    }
}

function Test-RecordedIdentity($State, $Identity) {
    if (-not $State -or -not $Identity) { return $false }
    if ([int]$State.pid -ne $Identity.Pid) { return $false }
    if (-not (Test-ServerCommandLine $State.commandLine)) { return $false }
    if (-not (Test-ServerCommandLine $Identity.CommandLine)) { return $false }
    try {
        $recordedStart = [DateTime]::Parse(
            [string]$State.processStartTime,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $difference = [Math]::Abs(($recordedStart - $Identity.ProcessStartTime).TotalSeconds)
        return $difference -le 2
    } catch {
        return $false
    }
}

function Get-RecordedServiceRecord {
    $pidFileExists = Test-Path -LiteralPath $PidPath
    $stateFileExists = Test-Path -LiteralPath $StatePath
    $state = Read-RecordedState
    $pidFromFile = Read-RecordedPid
    $recordedPort = $null
    $recordedUrl = $null
    $recordedPortValid = $false
    $recordedUrlValid = $false
    $statePid = $null
    $statePidValid = $false
    if ($state) {
        $candidatePid = 0
        if ([int]::TryParse([string]$state.pid, [ref]$candidatePid) -and $candidatePid -gt 0) {
            $statePid = $candidatePid
            $statePidValid = $true
        }
        $candidatePort = 0
        if (
            [int]::TryParse([string]$state.port, [ref]$candidatePort) -and
            $candidatePort -ge 1 -and
            $candidatePort -le 65535
        ) {
            $recordedPort = $candidatePort
            $recordedPortValid = $true
            $recordedUrl = [string]$state.url
            $recordedUrlValid = $recordedUrl -eq "http://127.0.0.1:$recordedPort"
        }
    }
    $identity = $null
    if ($pidFromFile) {
        $identity = Get-CurrentProcessIdentity $pidFromFile
    }
    $identityMatches = $false
    if (
        $state -and
        $pidFromFile -and
        $recordedPortValid -and
        $recordedUrlValid -and
        $statePidValid -and
        $statePid -eq $pidFromFile
    ) {
        $identityMatches = Test-RecordedIdentity $state $identity
    }
    [pscustomobject]@{
        HasRecord = $pidFileExists -or $stateFileExists
        State = $state
        StatePid = $statePid
        Pid = $pidFromFile
        RecordedPort = $recordedPort
        RecordedUrl = $recordedUrl
        Identity = $identity
        IdentityMatches = $identityMatches
        TargetPort = if ($recordedPortValid) { $recordedPort } else { $Port }
        TargetUrl = if ($recordedUrlValid) { $recordedUrl } else { $Url }
    }
}

function Test-TcpPort([int]$CheckPort) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $CheckPort)
        return $task.Wait(300) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Stop-NewProcessSafely($Process) {
    if (-not $Process) { return $false }
    if ($env:LOCAL_DEBUG_TEST_FORCE_CLEANUP_FAILURE -eq '1') { return $false }
    try {
        $processId = [int]$Process.Id
        $processStartTime = $Process.StartTime.ToUniversalTime()
        if ($Process.HasExited) { return $true }
        $currentProcess = Get-Process -Id $processId -ErrorAction Stop
        $currentStartTime = $currentProcess.StartTime.ToUniversalTime()
        if ([Math]::Abs(($currentStartTime - $processStartTime).TotalSeconds) -gt 2) {
            return $false
        }
        if ($Process.HasExited) { return $true }
        $killOutput = & taskkill.exe /PID $processId /T /F 2>&1
        if ($LASTEXITCODE -ne 0) { return $false }
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if ($Process.HasExited) { return $true }
            Start-Sleep -Milliseconds 250
        }
        return $Process.HasExited
    } catch {
        return $false
    }
}

function Wait-TcpPortFree([int]$CheckPort) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Test-TcpPort $CheckPort)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return -not (Test-TcpPort $CheckPort)
}

function Test-Http([string]$CheckUrl) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $CheckUrl -TimeoutSec 2 -MaximumRedirection 0 -ErrorAction Stop
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -lt 400) { return $true }
        return $false
    }
}

function Remove-StaleState {
    Remove-Item -LiteralPath $PidPath, $StatePath -Force -ErrorAction SilentlyContinue
}

function Get-PortOccupancyMessage([int]$CheckPort) {
    $connection = $null
    try {
        $connection = Get-NetTCPConnection -LocalPort $CheckPort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    } catch {
        $connection = $null
    }
    if ($connection) {
        $owner = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
        if ($owner) {
            return "端口 $CheckPort 已被 $($owner.ProcessName)（PID $($owner.Id)）占用；未终止该进程。"
        }
    }
    return "端口 $CheckPort 已被其他程序占用；未终止任何进程。"
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
    $record = Get-RecordedServiceRecord
    $processUp = $record.IdentityMatches
    $portUp = Test-TcpPort $record.TargetPort
    $httpUp = Test-Http $record.TargetUrl
    if ($record.HasRecord -and -not $processUp) {
        Remove-StaleState
    }
    $label = if ($processUp -and $httpUp) {
        '运行中'
    } elseif (-not $processUp -and -not $portUp -and -not $httpUp) {
        '未运行'
    } else {
        '状态异常'
    }
    [pscustomobject]@{
        Pid = $record.Pid
        ProcessUp = $processUp
        PortUp = $portUp
        HttpUp = $httpUp
        Label = $label
        Url = $record.TargetUrl
        RequestedUrl = $Url
    }
}

function Show-Status {
    $status = Get-DebugStatus
    Write-Host ('PID: ' + $(if ($status.Pid) { $status.Pid } else { '-' }))
    Write-Host ('进程状态: ' + $(if ($status.ProcessUp) { '运行中' } else { '未运行' }))
    Write-Host ('端口状态: ' + $(if ($status.PortUp) { '已占用' } else { '空闲' }))
    Write-Host ('网页状态: ' + $(if ($status.HttpUp) { '可访问' } else { '不可访问' }))
    Write-Host ('访问地址: ' + $status.Url)
    if ($status.Url -ne $status.RequestedUrl) {
        Write-Host ('本次请求地址: ' + $status.RequestedUrl)
    }
    return $status.ProcessUp -and $status.HttpUp
}

function Start-LocalDebug {
    Set-DebugEnvironment
    $record = Get-RecordedServiceRecord
    if ($record.IdentityMatches) {
        if (Test-Http $record.TargetUrl) {
            Write-MenuResult '提示' "服务已经运行：$($record.TargetUrl)"
            return
        }
        throw "记录的调试服务进程仍存在，但网页不可访问：$($record.TargetUrl)"
    }
    if ($record.HasRecord) {
        $stalePort = $record.RecordedPort
        Remove-StaleState
        if ($stalePort -and (Test-TcpPort $stalePort)) {
            throw (Get-PortOccupancyMessage $stalePort)
        }
    }
    if (Test-TcpPort $Port) { throw (Get-PortOccupancyMessage $Port) }

    $python = Resolve-PythonCommand
    Invoke-PythonCommand $python @('tools/prepare_local_debug.py', '--password', $SharedPassword)

    $prefixText = ($python.Prefix | ForEach-Object { $_ }) -join ' '
    $pythonText = '"' + $python.Exe + '"'
    if ($prefixText) { $pythonText += ' ' + $prefixText }
    $serverCommand = "cd /d `"$RepoRoot`" && $pythonText -u `"$ServerScriptPath`" >> `"$ConsoleLogPath`" 2>&1"
    $process = $null
    $identity = $null
    $launchState = $null
    try {
        $process = Start-Process `
            -FilePath 'cmd.exe' `
            -ArgumentList '/c', $serverCommand `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -PassThru
        $processStartTimeText = $null
        try {
            $processStartTimeText = $process.StartTime.ToUniversalTime().ToString(
                'o',
                [Globalization.CultureInfo]::InvariantCulture
            )
        } catch {
            $processStartTimeText = $null
        }
        $cmdPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
        $expectedCommandLine = '"' + $cmdPath + '" /c ' + $serverCommand
        $launchState = [pscustomobject]@{
            pid = $process.Id
            startedAt = (Get-Date).ToString('o')
            processStartTime = $processStartTimeText
            commandLine = $expectedCommandLine
            port = $Port
            url = $Url
            python = $python.Exe
            database = $DatabasePath
        }
        Set-Content -LiteralPath $PidPath -Value $process.Id -Encoding ascii
        $launchState | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8
        for ($attempt = 0; $attempt -lt 10 -and -not $identity; $attempt++) {
            $identity = Get-CurrentProcessIdentity $process.Id
            if (-not $identity) { Start-Sleep -Milliseconds 100 }
        }
        if (-not $identity -or -not (Test-ServerCommandLine $identity.CommandLine)) {
            throw "无法验证本地调试服务进程身份 PID=$($process.Id)"
        }
        $launchState.processStartTime = $identity.ProcessStartTimeText
        $launchState.commandLine = $identity.CommandLine
        $launchState | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8

        for ($attempt = 0; $attempt -lt ($HealthTimeoutSeconds * 2); $attempt++) {
            if (Test-Http $Url) {
                Write-MenuResult '成功' "服务已启动：$Url"
                Start-Process $Url
                return
            }
            Start-Sleep -Milliseconds 500
        }
        throw "Flask did not become reachable within $HealthTimeoutSeconds seconds. Check $LogPath"
    } catch {
        $launchFailure = $_.Exception
        $cleanupSucceeded = $false
        if ($process) { $cleanupSucceeded = Stop-NewProcessSafely $process }
        if (-not $cleanupSucceeded) {
            throw "本次启动的进程无法清理，状态已保留：$StatePath"
        }
        $portReleased = Wait-TcpPortFree $Port
        Remove-StaleState
        if (-not $portReleased) {
            throw "$($launchFailure.Message)；端口 $Port 仍被占用，未终止其他进程。"
        }
        throw $launchFailure
    }
}

function Stop-LocalDebug([switch]$Quiet) {
    $record = Get-RecordedServiceRecord
    $targetPort = $record.RecordedPort
    if (-not $targetPort) { $targetPort = $Port }
    if ($record.IdentityMatches) {
        $latestIdentity = Get-CurrentProcessIdentity $record.Pid
        if (-not (Test-RecordedIdentity $record.State $latestIdentity)) {
            Remove-StaleState
            if (Test-TcpPort $targetPort) {
                throw (Get-PortOccupancyMessage $targetPort)
            }
            if (-not $Quiet) { Write-MenuResult '提示' '服务尚未启动。' }
            return
        }
        $taskKillOutput = & taskkill.exe /PID $record.Pid /T /F 2>&1
        if ($LASTEXITCODE -ne 0) { throw "无法关闭启动器记录的进程树 PID=$($record.Pid)" }
    } elseif ($record.HasRecord) {
        Remove-StaleState
        if (Test-TcpPort $targetPort) {
            throw (Get-PortOccupancyMessage $targetPort)
        }
        if (-not $Quiet) { Write-MenuResult '提示' '服务尚未启动。' }
        return
    } else {
        if (Test-TcpPort $targetPort) {
            throw (Get-PortOccupancyMessage $targetPort)
        }
        if (-not $Quiet) { Write-MenuResult '提示' '服务尚未启动。' }
        return
    }
    Remove-StaleState
    if ($record.IdentityMatches) {
        for ($attempt = 0; $attempt -lt 20 -and (Test-TcpPort $targetPort); $attempt++) {
            Start-Sleep -Milliseconds 250
        }
        if (Test-TcpPort $targetPort) { throw "记录进程已停止，但端口 $targetPort 仍被占用；未终止其他进程。" }
    }
    if (-not $Quiet) { Write-MenuResult '成功' '本地调试服务已关闭；调试数据库和日志已保留。' }
}

function Show-Menu {
    while ($true) {
        Clear-Host
        $status = Get-DebugStatus
        Write-Host 'SWU TIC 本地调试'
        Write-Host "状态：$($status.Label)"
        Write-Host "地址：$($status.Url)"
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
                    $currentStatus = Get-DebugStatus
                    if (-not (Test-Http $currentStatus.Url)) { throw '服务尚未启动。' }
                    Start-Process $currentStatus.Url
                    Write-MenuResult '成功' "已打开：$($currentStatus.Url)"
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
    $HealthTimeoutSeconds = Resolve-HealthTimeout
    $Url = "http://127.0.0.1:$Port"
    switch ($Action) {
        'menu' { Show-Menu }
        'start' { Start-LocalDebug }
        'stop' { Stop-LocalDebug }
        'restart' { Stop-LocalDebug -Quiet; Start-LocalDebug }
        'status' { [void](Show-Status) }
        'open' {
            $status = Get-DebugStatus
            if (-not (Test-Http $status.Url)) { throw '服务尚未启动。' }
            Start-Process $status.Url
            Write-MenuResult '成功' "已打开：$($status.Url)"
        }
    }
} catch {
    [Console]::Error.WriteLine("[失败] $($_.Exception.Message)")
    exit 1
} finally {
    if ($locationPushed) { Pop-Location }
}
