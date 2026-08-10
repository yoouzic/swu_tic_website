# Local Debug Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows `本地调试.cmd` menu that safely starts, stops, restarts, inspects, and opens an isolated local Flask debug site with three fixed test accounts using password `1234564`.

**Architecture:** Keep the CMD file focused on the Chinese menu and argument dispatch. Delegate process lifecycle and environment isolation to a PowerShell manager, data preparation to a guarded Python helper, and Flask log/file behavior to a small debug server wrapper. All runtime state stays under ignored `data/instance/debug` and `data/storage/debug` paths.

**Tech Stack:** Windows CMD, PowerShell 5.1+, Python 3, Flask, Flask-SQLAlchemy, Werkzeug password hashing, `unittest`, SQLite.

---

## File map

- Create `本地调试.cmd`: user-facing Chinese menu and non-interactive command dispatch.
- Create `tools/local_debug.ps1`: Python discovery, isolated environment, PID/state, port and HTTP checks, process start/stop/restart/status/open.
- Create `tools/prepare_local_debug.py`: guarded isolated-database preparation and password reset.
- Create `tools/local_debug_server.py`: guarded Flask debug entry point with console and file logging.
- Create `tests/test_local_debug_launcher.py`: Python integration tests and static contracts for all launcher components.

Do not modify `app/app.py`, the default database, production configuration, `.gitignore`, or product templates.

### Task 1: Add isolated debug data preparation

**Files:**
- Create: `tools/prepare_local_debug.py`
- Create: `tests/test_local_debug_launcher.py`

- [ ] **Step 1: Write failing isolation and account tests**

Create `tests/test_local_debug_launcher.py` with these initial tests:

```python
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


ROOT = Path(__file__).resolve().parents[1]


class LocalDebugDataTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name, 'lecture_forms-debug.db')

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_prepare(self, *, extra_env=None):
        env = os.environ.copy()
        env.update({
            'LOCAL_DEBUG_MODE': '1',
            'INSTANCE_DIR': self.temp_dir.name,
            'SQLITE_DB_PATH': str(self.db_path),
            'UPLOAD_FOLDER': str(Path(self.temp_dir.name, 'uploads')),
            'DATABASE_URL': '',
            'SECRET_KEY': 'local-debug-test',
            'PYTHONUTF8': '1',
        })
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, 'tools/prepare_local_debug.py', '--password', '1234564'],
            cwd=ROOT,
            env=env,
            text=True,
            encoding='utf-8',
            capture_output=True,
            check=False,
        )

    def rows(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                'select student_id, role, password_hash from users order by student_id'
            ).fetchall()
        finally:
            con.close()

    def test_prepare_creates_three_roles_with_shared_password(self):
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        users = {row['student_id']: row for row in self.rows()}
        self.assertEqual(users['super']['role'], '超级管理员')
        self.assertEqual(users['manager']['role'], '管理员')
        self.assertEqual(users['user001']['role'], '信息员')
        for student_id in ('super', 'manager', 'user001'):
            self.assertTrue(check_password_hash(users[student_id]['password_hash'], '1234564'))

    def test_prepare_resets_all_admin_roles_but_not_unrelated_information_officer(self):
        first = self.run_prepare()
        self.assertEqual(first.returncode, 0, first.stderr)
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                'insert into users '
                '(student_id, password_hash, number, name, department, "group", gender, grade, '
                'college, major, dormitory, phone, qq, role, is_active) '
                'values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ('extra-admin', generate_password_hash('old-admin'), 'M900', '额外管理员',
                 '办公部', '未分配小组', '-', '-', '-', '-', '-', '-', '-', '管理员', 1),
            )
            con.execute(
                'insert into users '
                '(student_id, password_hash, number, name, department, "group", gender, grade, '
                'college, major, dormitory, phone, qq, role, is_active) '
                'values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ('extra-user', generate_password_hash('keep-me'), 'U900', '额外信息员',
                 '办公部', '未分配小组', '-', '-', '-', '-', '-', '-', '-', '信息员', 1),
            )
            con.commit()
        finally:
            con.close()

        second = self.run_prepare()
        self.assertEqual(second.returncode, 0, second.stderr)
        users = {row['student_id']: row for row in self.rows()}
        self.assertTrue(check_password_hash(users['extra-admin']['password_hash'], '1234564'))
        self.assertTrue(check_password_hash(users['extra-user']['password_hash'], 'keep-me'))

    def test_prepare_refuses_to_run_without_explicit_debug_guard(self):
        result = self.run_prepare(extra_env={'LOCAL_DEBUG_MODE': '0'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LOCAL_DEBUG_MODE=1', result.stderr)

    def test_prepare_refuses_default_business_database(self):
        result = self.run_prepare(extra_env={
            'SQLITE_DB_PATH': str(ROOT / 'data' / 'instance' / 'lecture_forms.db'),
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('default business database', result.stderr)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugDataTest -v
```

Expected: FAIL because `tools/prepare_local_debug.py` does not exist.

- [ ] **Step 3: Implement the guarded preparation helper**

Create `tools/prepare_local_debug.py`:

```python
# -*- coding: utf-8 -*-
"""Prepare the explicitly isolated SQLite database used by the Windows debug launcher."""

import argparse
import os
import sys
from argparse import Namespace
from pathlib import Path

from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def require_isolated_debug_database():
    if os.environ.get('LOCAL_DEBUG_MODE') != '1':
        raise RuntimeError('LOCAL_DEBUG_MODE=1 is required for local debug preparation')
    configured = os.environ.get('SQLITE_DB_PATH')
    if not configured:
        raise RuntimeError('SQLITE_DB_PATH must explicitly select an isolated debug database')
    selected = Path(configured).resolve()
    default_db = (ROOT / 'data' / 'instance' / 'lecture_forms.db').resolve()
    if selected == default_db:
        raise RuntimeError('refusing to modify the default business database')
    return selected


DEBUG_DB = require_isolated_debug_database()

from app.app import app
from app.models import User, db
from tools.init_user import (
    DEFAULT_DEPARTMENT,
    DEFAULT_GROUP,
    DEFAULT_VALUE,
    create_demo_users,
)


def demo_args(password):
    return Namespace(
        student_id='admin',
        password=password,
        create_demo_users=True,
        super_password=password,
        manager_password=password,
        user_password=password,
        role='超级管理员',
        number=None,
        name=None,
        department=DEFAULT_DEPARTMENT,
        group=DEFAULT_GROUP,
        gender=DEFAULT_VALUE,
        grade=DEFAULT_VALUE,
        college=DEFAULT_VALUE,
        major=DEFAULT_VALUE,
        dormitory=DEFAULT_VALUE,
        phone=DEFAULT_VALUE,
        qq=DEFAULT_VALUE,
        inactive=False,
    )


def prepare(password):
    result = create_demo_users(demo_args(password))
    if result:
        return result
    with app.app_context():
        administrators = User.query.filter(User.role.in_(('管理员', '超级管理员'))).all()
        for user in administrators:
            user.password_hash = generate_password_hash(password)
        db.session.commit()
        print(
            f'local debug database ready: {DEBUG_DB} '
            f'admin_passwords_reset={len(administrators)}'
        )
    return 0


def main():
    parser = argparse.ArgumentParser(description='Prepare the isolated local debug database.')
    parser.add_argument('--password', required=True)
    args = parser.parse_args()
    if not args.password:
        parser.error('--password cannot be empty')
    return prepare(args.password)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
```

- [ ] **Step 4: Run the data tests and verify GREEN**

Run the same command from Step 2.

Expected: four tests PASS; the temporary database is deleted by test teardown.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- tools/prepare_local_debug.py tests/test_local_debug_launcher.py
git commit -m "feat: prepare isolated local debug accounts"
```

### Task 2: Add a guarded Flask debug server entry point

**Files:**
- Create: `tools/local_debug_server.py`
- Modify: `tests/test_local_debug_launcher.py`

- [ ] **Step 1: Add failing server-wrapper contracts**

Append to `tests/test_local_debug_launcher.py`:

```python
class LocalDebugServerContractTest(unittest.TestCase):
    def test_server_requires_debug_guard_and_uses_loopback(self):
        source = (ROOT / 'tools' / 'local_debug_server.py').read_text(encoding='utf-8')
        self.assertIn("LOCAL_DEBUG_MODE') != '1'", source)
        self.assertIn("host='127.0.0.1'", source)
        self.assertIn('use_reloader=False', source)
        self.assertIn('LOCAL_DEBUG_LOG_PATH', source)

    def test_server_does_not_reference_default_database(self):
        source = (ROOT / 'tools' / 'local_debug_server.py').read_text(encoding='utf-8')
        self.assertNotIn('data/instance/lecture_forms.db', source.replace('\\\\', '/'))
```

- [ ] **Step 2: Run the server contract tests and verify RED**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugServerContractTest -v
```

Expected: FAIL because `tools/local_debug_server.py` is missing.

- [ ] **Step 3: Implement the server wrapper**

Create `tools/local_debug_server.py`:

```python
# -*- coding: utf-8 -*-
"""Run the Flask app in explicitly isolated local-debug mode."""

import logging
import os
import sys
from pathlib import Path


if os.environ.get('LOCAL_DEBUG_MODE') != '1':
    print('LOCAL_DEBUG_MODE=1 is required', file=sys.stderr)
    raise SystemExit(2)
if not os.environ.get('SQLITE_DB_PATH'):
    print('SQLITE_DB_PATH is required', file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log_path = Path(os.environ['LOCAL_DEBUG_LOG_PATH']).resolve()
log_path.parent.mkdir(parents=True, exist_ok=True)
handler = logging.FileHandler(log_path, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))

from app.app import app, init_database

app.logger.addHandler(handler)
logging.getLogger('werkzeug').addHandler(handler)
init_database()

if __name__ == '__main__':
    app.run(
        host='127.0.0.1',
        port=int(os.environ.get('FLASK_RUN_PORT', '5000')),
        debug=True,
        use_reloader=False,
    )
```

- [ ] **Step 4: Verify GREEN and Python syntax**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugServerContractTest -v
..\SWU_TIC-main\.venv\Scripts\python.exe -m py_compile tools/prepare_local_debug.py tools/local_debug_server.py
```

Expected: both server tests PASS and `py_compile` exits 0.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- tools/local_debug_server.py tests/test_local_debug_launcher.py
git commit -m "feat: add isolated Flask debug entry point"
```

### Task 3: Implement precise PowerShell process management

**Files:**
- Create: `tools/local_debug.ps1`
- Modify: `tests/test_local_debug_launcher.py`

- [ ] **Step 1: Add failing PowerShell contracts**

Append:

```python
class LocalDebugPowerShellContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'tools' / 'local_debug.ps1').read_text(encoding='utf-8')

    def test_manager_exposes_required_actions_and_loopback_url(self):
        for action in ('start', 'stop', 'restart', 'status', 'open'):
            self.assertIn(f"'{action}'", self.source)
        self.assertIn('http://127.0.0.1:5000', self.source)

    def test_manager_uses_isolated_paths_and_clears_database_url(self):
        self.assertIn("data\\instance\\debug", self.source)
        self.assertIn('lecture_forms-debug.db', self.source)
        self.assertIn("$env:DATABASE_URL = ''", self.source)
        self.assertIn("$env:LOCAL_DEBUG_MODE = '1'", self.source)

    def test_manager_records_pid_and_never_kills_python_by_name(self):
        self.assertIn('local-debug.pid', self.source)
        self.assertIn('/T', self.source)
        self.assertIn('/PID', self.source)
        self.assertNotIn('/im python', self.source.lower())
        self.assertNotIn('Stop-Process -Name', self.source)

    def test_manager_refuses_foreign_port_occupants(self):
        self.assertIn('Test-TcpPort', self.source)
        self.assertIn('occupied by another process', self.source)
```

- [ ] **Step 2: Verify RED**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugPowerShellContractTest -v
```

Expected: FAIL because `tools/local_debug.ps1` is missing.

- [ ] **Step 3: Implement `tools/local_debug.ps1`**

The implementation must contain these functions with the stated responsibilities:

```powershell
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
    $serverCommand = "title 西大听课工作台-本地调试日志 && cd /d `"$RepoRoot`" && $pythonText -u tools/local_debug_server.py"
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
```

The implementing worker may adjust quoting only where required by PowerShell parsing, while preserving all paths, process-safety rules, and observable behavior.

- [ ] **Step 4: Verify contracts and PowerShell syntax GREEN**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugPowerShellContractTest -v
powershell.exe -NoProfile -Command "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'tools/local_debug.ps1'),[ref]$tokens,[ref]$errors) | Out-Null; if($errors.Count){$errors | Format-List; exit 1}"
```

Expected: four tests PASS and PowerShell parser exits 0.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- tools/local_debug.ps1 tests/test_local_debug_launcher.py
git commit -m "feat: manage local debug server lifecycle"
```

### Task 4: Add the Chinese CMD menu

**Files:**
- Create: `本地调试.cmd`
- Modify: `tests/test_local_debug_launcher.py`

- [ ] **Step 1: Add failing CMD contracts**

Append:

```python
class LocalDebugCmdContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / '本地调试.cmd').read_text(encoding='utf-8')

    def test_menu_contains_all_requested_choices(self):
        for label in ('[1] 启动服务', '[2] 关闭服务', '[3] 重启服务',
                      '[4] 查看运行状态', '[5] 打开本地网页', '[0] 退出'):
            self.assertIn(label, self.source)

    def test_menu_dispatches_all_noninteractive_actions(self):
        self.assertIn('tools\\local_debug.ps1', self.source)
        for action in ('start', 'stop', 'restart', 'status', 'open'):
            self.assertIn(f'-Action {action}', self.source)

    def test_menu_uses_its_own_directory_and_utf8(self):
        self.assertIn('%~dp0', self.source)
        self.assertIn('chcp 65001', self.source.lower())
```

- [ ] **Step 2: Verify RED**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugCmdContractTest -v
```

Expected: FAIL because `本地调试.cmd` is missing.

- [ ] **Step 3: Implement the CMD menu**

Create `本地调试.cmd` as UTF-8:

```bat
@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "MANAGER=%~dp0tools\local_debug.ps1"

if not "%~1"=="" goto argument_mode

:menu
cls
echo ========================================
echo        西大听课工作台 · 本地调试
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
```

- [ ] **Step 4: Verify CMD contracts GREEN**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugCmdContractTest -v
cmd.exe /d /c "本地调试.cmd status"
```

Expected: three tests PASS; status prints “未运行” and exits 0 without starting Flask.

- [ ] **Step 5: Commit Task 4**

```powershell
git add -- '本地调试.cmd' tests/test_local_debug_launcher.py
git commit -m "feat: add Chinese local debug menu"
```

### Task 5: Perform real Windows lifecycle and login acceptance

**Files:**
- Modify if a regression is found: `tests/test_local_debug_launcher.py`
- Modify only when required by a failing regression: `tools/local_debug.ps1`, `tools/prepare_local_debug.py`, `tools/local_debug_server.py`, `本地调试.cmd`

- [ ] **Step 1: Confirm clean preconditions and no production-data mutation**

Record hashes and timestamps without opening the default database for writes:

```powershell
git status --short
if (Test-Path 'data\instance\lecture_forms.db') {
  Get-FileHash 'data\instance\lecture_forms.db' -Algorithm SHA256
  Get-Item 'data\instance\lecture_forms.db' | Select-Object Length,LastWriteTimeUtc
}
```

Expected: worktree contains only planned changes if Task 4 has not yet been committed; default database evidence is recorded if the file exists.

- [ ] **Step 2: Exercise status and start**

```powershell
cmd.exe /d /c "本地调试.cmd status"
cmd.exe /d /c "本地调试.cmd start"
cmd.exe /d /c "本地调试.cmd status"
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/ -MaximumRedirection 0
```

Expected: initial status is stopped; start succeeds; later status shows recorded PID and reachable HTTP; HTTP returns 200 or a login redirect.

- [ ] **Step 3: Verify all three logins against the debug database**

Use a short read-only acceptance command or existing browser automation to submit the actual login form for `super`, `manager`, and `user001`, each with `1234564`. Confirm each reaches its role workspace. Do not use or copy accounts from the default database.

Expected role workspaces:

```text
super   -> 超级管理员 workspace/navigation
manager -> 管理员 workspace/navigation
user001 -> 信息员 workspace/navigation
```

- [ ] **Step 4: Verify duplicate start, restart, and stop behavior**

```powershell
$firstPid = Get-Content 'data\instance\debug\local-debug.pid'
cmd.exe /d /c "本地调试.cmd start"
$samePid = Get-Content 'data\instance\debug\local-debug.pid'
if ($firstPid -ne $samePid) { throw 'duplicate start created another process' }
cmd.exe /d /c "本地调试.cmd restart"
$secondPid = Get-Content 'data\instance\debug\local-debug.pid'
if ($firstPid -eq $secondPid) { throw 'restart did not replace the process' }
cmd.exe /d /c "本地调试.cmd stop"
cmd.exe /d /c "本地调试.cmd status"
```

Expected: duplicate start preserves PID; restart changes PID; stop releases port and retains `lecture_forms-debug.db` plus log file.

- [ ] **Step 5: Verify foreign-port safety**

Start a temporary non-project listener on port 5000 only after the debug service is stopped. Run `本地调试.cmd start` and verify it returns nonzero without terminating the listener. Stop only the explicitly created temporary listener afterward.

Expected: output contains `occupied by another process`; listener PID remains alive until the acceptance harness stops it.

- [ ] **Step 6: Run full automated verification**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\SWU_TIC-main\.venv\Scripts\python.exe -m compileall app tools tests
powershell.exe -NoProfile -Command "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'tools/local_debug.ps1'),[ref]$tokens,[ref]$errors) | Out-Null; if($errors.Count){$errors | Format-List; exit 1}"
git diff --check
git status --short
```

Expected: all tests PASS, compileall/parser/diff checks exit 0, and runtime outputs remain ignored.

- [ ] **Step 7: Recheck default database and runtime containment**

```powershell
if (Test-Path 'data\instance\lecture_forms.db') {
  Get-FileHash 'data\instance\lecture_forms.db' -Algorithm SHA256
  Get-Item 'data\instance\lecture_forms.db' | Select-Object Length,LastWriteTimeUtc
}
git check-ignore -v data/instance/debug/lecture_forms-debug.db data/instance/debug/local-debug.pid data/storage/debug/logs/local-debug.log
git status --short
```

Expected: default database hash/timestamp are unchanged; all runtime files are ignored; no server remains running.

- [ ] **Step 8: Commit any acceptance-driven correction, then report**

If Step 2–7 exposed a defect, add a failing regression first, implement only the minimal correction, rerun the full matrix, and commit:

```powershell
git add -- tests/test_local_debug_launcher.py tools/local_debug.ps1 tools/prepare_local_debug.py tools/local_debug_server.py '本地调试.cmd'
git commit -m "fix: harden local debug launcher lifecycle"
```

If no correction was required, do not create an empty commit. Report:

- exact commits;
- RED and GREEN evidence;
- selected Python interpreter;
- database/log/PID paths;
- three account login results;
- start/duplicate/restart/stop and foreign-port results;
- default database non-mutation evidence;
- full test count and any pre-existing warnings;
- final worktree and server status.
