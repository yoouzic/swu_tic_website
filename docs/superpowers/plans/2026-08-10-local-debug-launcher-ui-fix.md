# Local Debug Launcher UI Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Windows local-debug launcher use port 5087 and present a quiet, compact menu without CMD command echo or PowerShell stack dumps.

**Architecture:** Reduce `本地调试.cmd` to a BOM-free ASCII wrapper and move all interactive behavior into the existing PowerShell lifecycle manager. Keep the isolated database and PID model unchanged, run the Flask console process hidden, and preserve non-interactive actions for testing and automation.

**Tech Stack:** Windows CMD, Windows PowerShell 5.1, Python 3, Flask, `unittest`, SQLite

---

## File structure

- Modify `本地调试.cmd`: silent, ASCII-only entry point and argument forwarding.
- Modify `tools/local_debug.ps1`: port 5087 default, compact menu, shared status model, hidden server window, concise errors.
- Modify `tests/test_local_debug_launcher.py`: byte-level CMD regressions, menu/default-port contracts, concise-error process test.
- Do not modify `tools/prepare_local_debug.py` or application files unless a failing acceptance test proves a launcher-specific need.

### Task 1: Add regressions for the reported launcher failures

**Files:**
- Modify: `tests/test_local_debug_launcher.py`
- Test: `tests/test_local_debug_launcher.py`

- [ ] **Step 1: Replace the CMD contract setup with byte-aware assertions**

Keep the existing action-forwarding coverage, but make the class read both raw bytes and ASCII text. Add these tests:

```python
class LocalDebugCmdContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / '本地调试.cmd'
        cls.raw = cls.path.read_bytes() if cls.path.exists() else b''
        cls.source = cls.raw.decode('ascii') if cls.raw else ''

    def test_wrapper_is_bom_free_ascii_crlf_and_starts_with_echo_off(self):
        self.assertTrue(self.raw.startswith(b'@echo off\r\n'))
        self.assertFalse(self.raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', self.raw.replace(b'\r\n', b''))

    def test_wrapper_delegates_menu_and_noninteractive_actions(self):
        self.assertIn('tools\\local_debug.ps1', self.source)
        self.assertIn('-Action menu', self.source)
        self.assertIn('-Action "%~1"', self.source)
        self.assertNotIn('set /p', self.source.lower())
        self.assertNotIn('echo [1]', self.source.lower())
```

- [ ] **Step 2: Add PowerShell UI and default-port contract tests**

Extend `LocalDebugPowerShellContractTest` with:

```python
def test_manager_owns_compact_menu_and_uses_5087_by_default(self):
    self.assertIn("'menu'", self.source)
    self.assertIn('$DefaultPort = 5087', self.source)
    for label in ('SWU TIC 本地调试', '1  启动', '2  关闭', '3  重启',
                  '4  打开网页', '0  退出', '请选择'):
        self.assertIn(label, self.source)
    self.assertNotIn('$DefaultPort = 5000', self.source)

def test_manager_hides_server_console_and_avoids_write_error_stack_dump(self):
    self.assertIn('-WindowStyle Hidden', self.source)
    self.assertIn('[Console]::Error.WriteLine', self.source)
    self.assertNotIn('Write-Error $_.Exception.Message', self.source)
```

Update the existing port contract assertion from `$DefaultPort = 5000` to `$DefaultPort = 5087`. Keep the explicit `LOCAL_DEBUG_PORT` validation assertions.

- [ ] **Step 3: Add a real concise-error regression**

Add a helper that launches a temporary loopback listener on a free port, invokes the PowerShell `start` action with `LOCAL_DEBUG_PORT` set to that port, then checks that the listener survives and the output contains no stack metadata. Use a Python socket listener owned by the test process so cleanup is exact:

```python
import socket


class LocalDebugErrorOutputTest(unittest.TestCase):
    def test_foreign_port_error_is_concise_and_does_not_kill_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            env = os.environ.copy()
            env['LOCAL_DEBUG_PORT'] = str(port)
            result = subprocess.run(
                [
                    'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-File', str(ROOT / 'tools' / 'local_debug.ps1'),
                    '-Action', 'start',
                ],
                cwd=ROOT,
                env=env,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(port), combined)
            self.assertNotIn('CategoryInfo', combined)
            self.assertNotIn('FullyQualifiedErrorId', combined)
            self.assertNotIn('Write-Error', combined)
            self.assertEqual(listener.getsockname()[1], port)
        finally:
            listener.close()
```

- [ ] **Step 4: Run the targeted tests and confirm RED**

Run:

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugCmdContractTest tests.test_local_debug_launcher.LocalDebugPowerShellContractTest tests.test_local_debug_launcher.LocalDebugPortContractTest tests.test_local_debug_launcher.LocalDebugErrorOutputTest -v
```

Expected: failures report the current BOM/non-ASCII CMD, missing `menu`, default port `5000`, visible server window, and stack-producing `Write-Error`. The temporary listener remains alive until the test closes it.

- [ ] **Step 5: Commit the RED regression tests**

```powershell
git add -- tests/test_local_debug_launcher.py
git commit -m "test: cover local debug launcher console regressions"
```

### Task 2: Replace the batch menu with a silent ASCII wrapper

**Files:**
- Modify: `本地调试.cmd`
- Test: `tests/test_local_debug_launcher.py`

- [ ] **Step 1: Replace the file with the minimal wrapper**

The complete logical contents must be:

```bat
@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "MANAGER=%~dp0tools\local_debug.ps1"

if "%~1"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action menu
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action "%~1"
)

exit /b %ERRORLEVEL%
```

Save it as ASCII without BOM and with CRLF line endings. Do not put Chinese text or menu parsing back into this file.

- [ ] **Step 2: Run CMD contract tests**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher.LocalDebugCmdContractTest -v
```

Expected: both CMD tests PASS.

- [ ] **Step 3: Verify the wrapper does not echo commands in argument mode**

```powershell
$output = cmd.exe /d /c '"本地调试.cmd" status' 2>&1 | Out-String
if ($output -match '(?im)^.*powershell\.exe .*local_debug\.ps1' -or $output -match '(?im)^.*exit /b') {
    throw "wrapper echoed internal commands`n$output"
}
```

Expected: no internal CMD or PowerShell invocation is present in output.

- [ ] **Step 4: Commit the wrapper**

```powershell
git add -- '本地调试.cmd'
git commit -m "fix: make local debug cmd wrapper silent"
```

### Task 3: Implement the compact PowerShell menu and error handling

**Files:**
- Modify: `tools/local_debug.ps1`
- Test: `tests/test_local_debug_launcher.py`

- [ ] **Step 1: Extend the action contract and change the default port**

Change the parameter and port declarations to:

```powershell
param(
    [ValidateSet('menu', 'start', 'stop', 'restart', 'status', 'open')]
    [string]$Action = 'menu'
)

$DefaultPort = 5087
```

Keep `Resolve-DebugPort` and its explicit `LOCAL_DEBUG_PORT` range validation. Do not add automatic fallback.

- [ ] **Step 2: Introduce one shared status object**

Replace direct status calculation in `Show-Status` with these responsibilities:

```powershell
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
    } elseif (-not $processUp -and -not $httpUp) {
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
```

All menu and non-interactive status output must use this shared object so PID cleanup and labels cannot diverge.

- [ ] **Step 3: Add compact menu rendering and dispatch**

Implement a loop equivalent to:

```powershell
function Write-MenuResult([string]$Kind, [string]$Message) {
    $color = switch ($Kind) {
        '成功' { 'Green' }
        '失败' { 'Red' }
        default { 'Yellow' }
    }
    Write-Host "[$Kind] $Message" -ForegroundColor $color
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
                '3' { Stop-LocalDebug; Start-LocalDebug; break }
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
```

Adjust `Start-LocalDebug` and `Stop-LocalDebug` so each successful interactive operation produces only one main result line. Repeated start and already-stopped cases use `[提示]`; successful start/stop use `[成功]`. Do not print the account list on every menu action.

- [ ] **Step 4: Make port collision text safe and informative**

Before throwing for `Test-TcpPort`, query the loopback listener with `Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue`, then use `Get-Process` for its PID when available. The final message format is:

```text
端口 5087 已被 python（PID 1234）占用；未终止该进程。
```

If process details cannot be read, use:

```text
端口 5087 已被其他程序占用；未终止任何进程。
```

Never call `taskkill` for this path.

- [ ] **Step 5: Start the server process hidden**

Keep the existing PID/state file model and command construction, but use a terminating hidden command shell rather than a visible persistent one:

```powershell
$process = Start-Process `
    -FilePath 'cmd.exe' `
    -ArgumentList '/c', $serverCommand `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -PassThru
```

Remove the `/k` behavior and do not open a second console. Keep logging through `tools/local_debug_server.py` and retain `taskkill /PID <recorded PID> /T /F` for exact tree shutdown.

- [ ] **Step 6: Replace top-level stack-producing error output**

Extend the switch with `menu`, keep all non-interactive actions, and replace `Write-Error` with a plain stderr line:

```powershell
try {
    Push-Location $RepoRoot
    switch ($Action) {
        'menu' { Show-Menu }
        'start' { Start-LocalDebug }
        'stop' { Stop-LocalDebug }
        'restart' { Stop-LocalDebug; Start-LocalDebug }
        'status' { [void](Show-Status) }
        'open' {
            if (-not (Test-Http)) { throw '服务尚未启动。' }
            Start-Process $Url
            Write-Host "[成功] 已打开：$Url"
        }
    }
} catch {
    [Console]::Error.WriteLine("[失败] $($_.Exception.Message)")
    exit 1
} finally {
    Pop-Location
}
```

Ensure `Pop-Location` is only reached after a successful `Push-Location`; a boolean guard is acceptable if parser/testing exposes that edge case.

- [ ] **Step 7: Run targeted tests to GREEN**

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_local_debug_launcher -v
powershell.exe -NoProfile -Command "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'tools/local_debug.ps1'),[ref]$tokens,[ref]$errors) | Out-Null; if($errors.Count){$errors | Format-List; exit 1}"
```

Expected: all launcher tests PASS and parser reports no errors.

- [ ] **Step 8: Commit the PowerShell implementation**

```powershell
git add -- tools/local_debug.ps1 tests/test_local_debug_launcher.py
git commit -m "fix: simplify local debug launcher interface"
```

### Task 4: Perform real Windows lifecycle and repository verification

**Files:**
- Verify: `本地调试.cmd`
- Verify: `tools/local_debug.ps1`
- Verify: `data/instance/debug/`
- Verify: `data/storage/debug/`

- [ ] **Step 1: Record the protected port-5000 process and business database state**

```powershell
$protected5000 = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
$protectedPid = if ($protected5000) { $protected5000.OwningProcess } else { $null }
$businessDb = 'data\instance\lecture_forms.db'
$businessBefore = if (Test-Path $businessDb) { Get-FileHash $businessDb -Algorithm SHA256 } else { $null }
```

Expected: record only; do not terminate or mutate either target.

- [ ] **Step 2: Verify port 5087 is safe to use**

```powershell
$foreign5087 = Get-NetTCPConnection -LocalPort 5087 -State Listen -ErrorAction SilentlyContinue
if ($foreign5087) { throw 'Port 5087 is occupied by an external process; do not kill it.' }
```

If occupied, use a validated explicit `LOCAL_DEBUG_PORT` only for lifecycle automation and report that the fixed-default acceptance remains blocked. Do not silently claim 5087 passed.

- [ ] **Step 3: Exercise start, duplicate start, status, restart and HTTP health**

```powershell
cmd.exe /d /c '"本地调试.cmd" start'
$firstPid = (Get-Content 'data\instance\debug\local-debug.pid' -Raw).Trim()
$response = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:5087/' -TimeoutSec 5
if ($response.StatusCode -ne 200) { throw 'HTTP health check failed' }
cmd.exe /d /c '"本地调试.cmd" start'
$samePid = (Get-Content 'data\instance\debug\local-debug.pid' -Raw).Trim()
if ($firstPid -ne $samePid) { throw 'duplicate start created a second process' }
cmd.exe /d /c '"本地调试.cmd" status'
cmd.exe /d /c '"本地调试.cmd" restart'
$secondPid = (Get-Content 'data\instance\debug\local-debug.pid' -Raw).Trim()
if ($firstPid -eq $secondPid) { throw 'restart did not replace the process' }
```

Expected: 200 response, duplicate start preserves PID, restart changes PID, and no second visible CMD window is created.

- [ ] **Step 4: Verify the three isolated-debug logins**

Use three independent HTTP sessions or the existing browser flow to POST `/auth/login` with `student_id` equal to `super`, `manager`, and `user001`, password `1234564`, and redirects enabled. Confirm each session reaches an authenticated role workspace rather than returning the login form.

Expected:

```text
super   -> authenticated super-admin workspace
manager -> authenticated manager workspace
user001 -> authenticated information-officer workspace
```

- [ ] **Step 5: Stop and verify cleanup boundaries**

```powershell
cmd.exe /d /c '"本地调试.cmd" stop'
if (Get-NetTCPConnection -LocalPort 5087 -State Listen -ErrorAction SilentlyContinue) { throw '5087 still listening' }
if (Test-Path 'data\instance\debug\local-debug.pid') { throw 'PID file remains' }
if (-not (Test-Path 'data\instance\debug\lecture_forms-debug.db')) { throw 'debug database was removed' }
if (-not (Test-Path 'data\storage\debug\logs\local-debug.log')) { throw 'debug log is missing' }
```

Expected: service and transient state stop; debug database/log remain.

- [ ] **Step 6: Verify protected external state and run the full suite**

```powershell
if ($protectedPid -and -not (Get-Process -Id $protectedPid -ErrorAction SilentlyContinue)) { throw 'protected port-5000 process was terminated' }
if ($businessBefore) {
    $businessAfter = Get-FileHash $businessDb -Algorithm SHA256
    if ($businessAfter.Hash -ne $businessBefore.Hash) { throw 'business database changed' }
} elseif (Test-Path $businessDb) {
    throw 'business database was unexpectedly created'
}
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\SWU_TIC-main\.venv\Scripts\python.exe -m compileall app tools tests
powershell.exe -NoProfile -Command "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'tools/local_debug.ps1'),[ref]$tokens,[ref]$errors) | Out-Null; if($errors.Count){$errors | Format-List; exit 1}"
git diff --check
git status --short
```

Expected: all tests pass; compile/parser/diff checks exit 0; the protected process and business database are unchanged; no server is left running.

- [ ] **Step 7: Commit only acceptance-driven corrections, then report**

If real acceptance exposes a defect, add a focused failing test before the correction, rerun the same checks, and commit only the necessary launcher files:

```powershell
git add -- tests/test_local_debug_launcher.py tools/local_debug.ps1 '本地调试.cmd'
git commit -m "fix: harden local debug launcher acceptance"
```

Do not create an empty commit. Report exact commits, RED/GREEN evidence, test count, start/duplicate/restart/stop results, HTTP and login results, port-5000 process preservation, database non-mutation, final port state, and final `git status`.
