param(
    [ValidateSet(
        'start-flask', 'stop-flask', 'status-flask',
        'start-worker', 'stop-worker', 'status-worker',
        'start-test-helper', 'stop-test-helper', 'status-test-helper'
    )]
    [string]$Action = 'status-worker',
    [int]$Concurrency = 4,
    [string]$RuntimeRootOverride = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))

function Resolve-Root {
    param([string]$Override, [string]$EnvironmentName, [string]$Fallback)
    $candidate = $Override
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = [Environment]::GetEnvironmentVariable($EnvironmentName)
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = Join-Path $RepoRoot $Fallback
    }
    return [IO.Path]::GetFullPath($candidate)
}

$RuntimeRoot = Resolve-Root $RuntimeRootOverride 'ACCEPTANCE_RUNTIME_ROOT' 'data\storage\acceptance-2026-08-11'
$LogRoot = Join-Path $RuntimeRoot 'logs'
$WorkerStatePath = Join-Path $RuntimeRoot 'celery-worker-state.json'
$FlaskStatePath = Join-Path $RuntimeRoot 'flask-state.json'
$TestHelperStatePath = Join-Path $RuntimeRoot 'test-helper-state.json'

function Resolve-Python {
    $configured = [Environment]::GetEnvironmentVariable('ACCEPTANCE_RUNTIME_PYTHON')
    if (-not [string]::IsNullOrWhiteSpace($configured)) {
        $resolved = [IO.Path]::GetFullPath($configured)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "ACCEPTANCE_RUNTIME_PYTHON was not found: $resolved"
        }
        return $resolved
    }
    $candidates = @(
        (Join-Path $RepoRoot '.venv\Scripts\python.exe'),
        (Join-Path (Split-Path $RepoRoot -Parent) 'SWU_TIC-main\.venv\Scripts\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw 'No Python interpreter was found for the acceptance runtime'
}

function Write-Utf8Json {
    param([string]$Path, [object]$Value)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    $json = $Value | ConvertTo-Json -Depth 8
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    try {
        [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, $utf8)
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Read-State {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        $raw = [IO.File]::ReadAllText($Path)
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        return $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Recorded runtime state is invalid: $Path"
    }
}

function Get-ProcessIdentity {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $null }
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if (-not $cim -or [string]::IsNullOrWhiteSpace([string]$cim.CommandLine)) {
            return $null
        }
        $startUtc = $process.StartTime.ToUniversalTime()
        return [pscustomobject]@{
            pid = [int]$process.Id
            processStartTime = $startUtc.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
            commandLine = [string]$cim.CommandLine
        }
    } catch {
        return $null
    }
}

function Normalize-Text {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    return (($Value -replace '/', '\') -replace '\s+', ' ').Trim().ToLowerInvariant()
}

function Test-RecordedIdentity {
    param([object]$State, [string]$RequiredMarker)
    if (-not $State) { return $false }
    $pidValue = 0
    if (-not [int]::TryParse([string]$State.pid, [ref]$pidValue) -or $pidValue -le 0) {
        return $false
    }
    $current = Get-ProcessIdentity $pidValue
    if (-not $current) { return $false }
    if ($current.pid -ne $pidValue) { return $false }
    $recordedCommand = Normalize-Text ([string]$State.commandLine)
    $currentCommand = Normalize-Text ([string]$current.commandLine)
    if ([string]::IsNullOrWhiteSpace($recordedCommand) -or $recordedCommand -ne $currentCommand) {
        return $false
    }
    if (-not [string]::IsNullOrWhiteSpace($RequiredMarker)) {
        if (-not $currentCommand.Contains((Normalize-Text $RequiredMarker))) {
            return $false
        }
    }
    try {
        $recordedStart = [DateTime]::Parse(
            [string]$State.processStartTime,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $currentStart = [DateTime]::Parse(
            [string]$current.processStartTime,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        return [Math]::Abs(($recordedStart - $currentStart).TotalSeconds) -le 2
    } catch {
        return $false
    }
}

function Test-ExplicitAcceptanceDatabase {
    $database = [Environment]::GetEnvironmentVariable('SQLITE_DB_PATH')
    if ([string]::IsNullOrWhiteSpace($database)) {
        throw 'SQLITE_DB_PATH must explicitly select the isolated acceptance database'
    }
    $databaseRoot = ([IO.Path]::GetFullPath((Join-Path $RepoRoot 'data\instance\acceptance-2026-08-11'))).TrimEnd('\') + '\'
    $databasePath = [IO.Path]::GetFullPath($database)
    if (-not $databasePath.StartsWith($databaseRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'SQLITE_DB_PATH must remain beneath the acceptance instance root'
    }
}

function Resolve-StatePath {
    param([string]$Kind)
    switch ($Kind) {
        'flask' { return $FlaskStatePath }
        'worker' { return $WorkerStatePath }
        'test-helper' { return $TestHelperStatePath }
        default { throw "Unknown runtime process kind: $Kind" }
    }
}

function Resolve-LogPath {
    param([string]$Kind)
    return Join-Path $LogRoot "$Kind.log"
}

function Resolve-ErrorLogPath {
    param([string]$Kind)
    return Join-Path $LogRoot "$Kind.stderr.log"
}

function Start-ManagedProcess {
    param(
        [string]$Kind,
        [string[]]$Arguments,
        [string]$RequiredMarker
    )
    $statePath = Resolve-StatePath $Kind
    $oldState = Read-State $statePath
    if ($oldState) {
        if (Test-RecordedIdentity $oldState $RequiredMarker) {
            throw "$Kind is already running with the recorded identity"
        }
        $oldPid = 0
        if ([int]::TryParse([string]$oldState.pid, [ref]$oldPid) -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
            throw "$Kind state identity mismatch; refusing to overwrite a live process"
        }
        Remove-Item -LiteralPath $statePath -Force
    }

    $python = Resolve-Python
    $logPath = Resolve-LogPath $Kind
    $errorLogPath = Resolve-ErrorLogPath $Kind
    New-Item -ItemType Directory -Force -Path $RuntimeRoot, $LogRoot | Out-Null
    $pythonText = '"' + $python + '"'
    $argumentText = ($Arguments -join ' ')
    $launchCommand = "cd /d `"$RepoRoot`" && $pythonText -u $argumentText >> `"$logPath`" 2>> `"$errorLogPath`""
    $process = Start-Process `
        -FilePath 'cmd.exe' `
        -ArgumentList '/c', $launchCommand `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -PassThru
    Start-Sleep -Milliseconds 250
    $identity = Get-ProcessIdentity $process.Id
    if (-not $identity -or -not (Test-RecordedIdentity ([pscustomobject]@{
        pid = $identity.pid
        processStartTime = $identity.processStartTime
        commandLine = $identity.commandLine
    }) $RequiredMarker)) {
        throw "$Kind process identity could not be verified"
    }
    $state = [pscustomobject]@{
        kind = $Kind
        pid = $identity.pid
        processStartTime = $identity.processStartTime
        commandLine = $identity.commandLine
        hostname = [Environment]::MachineName
        logPath = $logPath
        errorLogPath = $errorLogPath
        python = $python
        arguments = $Arguments
    }
    Write-Utf8Json $statePath $state
    Write-Output "START=PASS kind=$Kind pid=$($identity.pid)"
}

function Stop-ManagedProcess {
    param(
        [string]$Kind,
        [string]$RequiredMarker
    )
    $statePath = Resolve-StatePath $Kind
    $state = Read-State $statePath
    if (-not $state) {
        Write-Output "STOP=PASS kind=$Kind running=0"
        return
    }
    if (-not (Test-RecordedIdentity $state $RequiredMarker)) {
        throw "$Kind recorded identity mismatch; no process was stopped"
    }
    $pidValue = [int]$state.pid
    $output = & taskkill.exe /PID $pidValue /T /F 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$Kind recorded process tree could not be stopped"
    }
    Start-Sleep -Milliseconds 250
    if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
        throw "$Kind recorded process remains after stop"
    }
    Remove-Item -LiteralPath $statePath -Force
    Write-Output "STOP=PASS kind=$Kind pid=$pidValue"
}

function Show-ManagedStatus {
    param(
        [string]$Kind,
        [string]$RequiredMarker
    )
    $state = Read-State (Resolve-StatePath $Kind)
    if (-not $state) {
        Write-Output "STATUS=PASS kind=$Kind running=0"
        return
    }
    $running = Test-RecordedIdentity $state $RequiredMarker
    Write-Output "STATUS=PASS kind=$Kind running=$(if ($running) { 1 } else { 0 }) pid=$($state.pid)"
}

function Start-Flask {
    Test-ExplicitAcceptanceDatabase
    $env:LOCAL_DEBUG_MODE = '1'
    $env:FLASK_RUN_HOST = '127.0.0.1'
    if ([string]::IsNullOrWhiteSpace($env:FLASK_RUN_PORT)) { $env:FLASK_RUN_PORT = '5087' }
    $env:LOCAL_DEBUG_PORT = $env:FLASK_RUN_PORT
    $env:LOCAL_DEBUG_LOG_PATH = Join-Path $LogRoot 'flask-app.log'
    $env:DATABASE_URL = ''
    Start-ManagedProcess 'flask' @('tools/local_debug_server.py') 'local_debug_server.py'
}

function Start-Worker {
    if ($Concurrency -lt 1 -or $Concurrency -gt 32) {
        throw 'Concurrency must be between 1 and 32 for worker actions'
    }
    $arguments = @(
        '-m', 'celery',
        '-A', 'celery_worker.celery_app',
        'worker',
        '--pool=threads',
        '--concurrency', [string]$Concurrency,
        '--loglevel=INFO',
        "--hostname=acceptance-$Concurrency@%h"
    )
    Start-ManagedProcess 'worker' $arguments 'celery_worker.celery_app'
}

function Start-TestHelper {
    if ([Environment]::GetEnvironmentVariable('ACCEPTANCE_RUNTIME_ALLOW_TEST_HELPER') -ne '1') {
        throw 'ACCEPTANCE_RUNTIME_ALLOW_TEST_HELPER=1 is required for the test helper'
    }
    Start-ManagedProcess 'test-helper' @('-c', '"import time; time.sleep(600)"') 'time.sleep'
}

try {
    switch ($Action) {
        'start-flask' { Start-Flask }
        'stop-flask' { Stop-ManagedProcess 'flask' 'local_debug_server.py' }
        'status-flask' { Show-ManagedStatus 'flask' 'local_debug_server.py' }
        'start-worker' { Start-Worker }
        'stop-worker' { Stop-ManagedProcess 'worker' 'celery_worker.celery_app' }
        'status-worker' { Show-ManagedStatus 'worker' 'celery_worker.celery_app' }
        'start-test-helper' { Start-TestHelper }
        'stop-test-helper' { Stop-ManagedProcess 'test-helper' 'time.sleep' }
        'status-test-helper' { Show-ManagedStatus 'test-helper' 'time.sleep' }
        default { throw "Unsupported runtime action: $Action" }
    }
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 2
}
