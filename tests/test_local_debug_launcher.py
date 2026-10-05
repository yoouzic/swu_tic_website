import os
import json
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


ROOT = Path(__file__).resolve().parents[1]


class LocalDebugDataTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name, 'lecture_forms-debug.db')
        import pandas as pd
        self.schedule_path = Path(self.temp_dir.name, 'test-schedule.xlsx')
        pd.DataFrame([{'学年': '2025-2026', '学期': '2', '姓名': '测试教师',
                       '教师所属学院': '测试学院', '课程名称': '测试课程', '星期几': '1',
                       '上课节次': '第3-4节', '上课地点': '8-309',
                       '教学班组成': '2025测试01班', '起始周': '1-20'}]).to_excel(self.schedule_path, index=False)

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
            'LOCAL_DEBUG_SCHEDULE_FILE': str(self.schedule_path),
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

    def test_prepare_configures_one_current_schedule_and_reuses_it_on_restart(self):
        for _ in range(2):
            result = self.run_prepare()
            self.assertEqual(result.returncode, 0, result.stderr)
        con = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(con.execute("select value from system_settings where key='teaching_current_semester'").fetchone()[0], '2025-2026-2')
            self.assertEqual(con.execute('select count(*) from schedule_import_batches').fetchone()[0], 1)
            self.assertEqual(con.execute('select count(*) from listening_assistant_schedule_entries').fetchone()[0], 1)
        finally:
            con.close()

    def test_prepare_reports_missing_timetable_without_creating_a_snapshot(self):
        result = self.run_prepare(extra_env={'LOCAL_DEBUG_SCHEDULE_FILE': str(Path(self.temp_dir.name, 'missing.xlsx'))})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('调试课表不存在', result.stderr)

    def test_prepare_refuses_a_workbook_with_multiple_semesters(self):
        import pandas as pd
        frame = pd.read_excel(self.schedule_path)
        other = frame.copy()
        other['学期'] = '1'
        pd.concat([frame, other]).to_excel(self.schedule_path, index=False)
        result = self.run_prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('一个学期的一份课表', result.stderr)

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


class LocalDebugServerContractTest(unittest.TestCase):
    def test_server_requires_debug_guard_and_uses_loopback(self):
        source_path = ROOT / 'tools' / 'local_debug_server.py'
        self.assertTrue(source_path.exists(), 'local debug server wrapper is missing')
        source = source_path.read_text(encoding='utf-8')
        self.assertIn("LOCAL_DEBUG_MODE') != '1'", source)
        self.assertIn("host='127.0.0.1'", source)
        self.assertIn('use_reloader=False', source)
        self.assertIn('LOCAL_DEBUG_LOG_PATH', source)

    def test_server_does_not_reference_default_database(self):
        source_path = ROOT / 'tools' / 'local_debug_server.py'
        self.assertTrue(source_path.exists(), 'local debug server wrapper is missing')
        source = source_path.read_text(encoding='utf-8')
        self.assertNotIn('data/instance/lecture_forms.db', source.replace('\\', '/'))


class LocalDebugPowerShellContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = ROOT / 'tools' / 'local_debug.ps1'
        cls.source = source_path.read_text(encoding='utf-8') if source_path.exists() else ''

    def test_manager_exposes_required_actions_and_loopback_url(self):
        for action in ('start', 'stop', 'restart', 'status', 'open'):
            self.assertIn(f"'{action}'", self.source)
        self.assertIn('http://127.0.0.1:', self.source)

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
        self.assertIn('Get-NetTCPConnection', self.source)
        self.assertIn('未终止该进程', self.source)
        self.assertIn('未终止任何进程', self.source)

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

    def test_browser_open_helper_honors_test_no_browser_guard(self):
        helper = self.source.split('function Open-DebugUrl', 1)[1].split(
            'function Get-DebugStatus', 1
        )[0]
        force_failure = "if ($env:LOCAL_DEBUG_TEST_FORCE_BROWSER_FAILURE -eq '1')"
        no_browser = "if ($env:LOCAL_DEBUG_NO_BROWSER -eq '1')"
        self.assertIn(force_failure, helper)
        self.assertIn(no_browser, helper)
        self.assertLess(helper.index(force_failure), helper.index(no_browser))
        self.assertLess(helper.index(no_browser), helper.index('Start-Process'))

    def test_manager_keeps_utf8_bom_for_windows_powershell(self):
        raw = (ROOT / 'tools' / 'local_debug.ps1').read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))

    def test_lifecycle_helpers_do_not_return_boolean_values(self):
        start_body = self.source.split('function Start-LocalDebug', 1)[1].split(
            'function Stop-LocalDebug', 1
        )[0]
        stop_body = self.source.split('function Stop-LocalDebug', 1)[1].split(
            'function Show-Menu', 1
        )[0]
        for body in (start_body, stop_body):
            self.assertIsNone(re.search(r'return\s+\$(?:true|false)\b', body))


class LocalDebugPortContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'tools' / 'local_debug.ps1').read_text(encoding='utf-8-sig')

    def test_manager_has_explicit_validated_port_override_and_shared_port_state(self):
        self.assertIn('$DefaultPort = 5087', self.source)
        self.assertIn('$env:LOCAL_DEBUG_PORT', self.source)
        self.assertIn('[int]::TryParse', self.source)
        self.assertIn('1 and 65535', self.source)
        self.assertIn("$env:FLASK_RUN_PORT = [string]$Port", self.source)
        self.assertIn('http://127.0.0.1:$Port', self.source)

    def test_manager_rejects_invalid_port_without_starting(self):
        env = os.environ.copy()
        env['LOCAL_DEBUG_PORT'] = 'not-a-port'
        result = subprocess.run(
            [
                'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(ROOT / 'tools' / 'local_debug.ps1'), '-Action', 'status',
            ],
            cwd=ROOT,
            env=env,
            text=True,
            encoding='utf-8',
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LOCAL_DEBUG_PORT must be an integer between 1 and 65535', result.stderr)


class LocalDebugIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir.name, 'runtime')
        self.storage_root = Path(self.temp_dir.name, 'storage')

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_action(self, action, *, env=None, input_text=None):
        process_env = os.environ.copy()
        process_env.update({
            'LOCAL_DEBUG_RUNTIME_ROOT': str(self.runtime_root),
            'LOCAL_DEBUG_STORAGE_ROOT': str(self.storage_root),
            'LOCAL_DEBUG_NO_BROWSER': '1',
        })
        if env:
            process_env.update(env)
        return subprocess.run(
            [
                'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(ROOT / 'tools' / 'local_debug.ps1'),
                '-Action', action,
            ],
            cwd=ROOT,
            env=process_env,
            input=input_text,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )


class LocalDebugActionValidationTest(unittest.TestCase):
    def test_unknown_action_is_one_line_failure_through_cmd_wrapper(self):
        result = subprocess.run(
            ['cmd.exe', '/d', '/c', '本地调试.cmd nonsense'],
            cwd=ROOT,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )
        combined = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(combined.count('[失败]'), 1, combined)
        self.assertNotIn('CategoryInfo', combined)
        self.assertNotIn('FullyQualifiedErrorId', combined)
        self.assertNotIn('local_debug.ps1:', combined)


class LocalDebugRootOverrideTest(LocalDebugIsolatedTestCase):
    def test_start_uses_temporary_runtime_and_storage_roots_before_port_check(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            result = self.run_action('start', env={'LOCAL_DEBUG_PORT': str(port)})
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(self.runtime_root.exists())
            self.assertTrue(Path(self.storage_root, 'uploads').exists())
            self.assertTrue(Path(self.storage_root, 'logs').exists())
            self.assertFalse(Path(self.runtime_root, 'local-debug.pid').exists())
        finally:
            listener.close()


class LocalDebugIdentitySafetyTest(LocalDebugIsolatedTestCase):
    def test_identity_mismatch_does_not_taskkill_external_process(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        sleeper = subprocess.Popen([
            sys.executable, '-c', 'import time; time.sleep(60)',
        ])
        pid_path = self.runtime_root / 'local-debug.pid'
        state_path = self.runtime_root / 'local-debug-state.json'
        try:
            self.runtime_root.mkdir(parents=True, exist_ok=True)
            pid_path.write_text(str(sleeper.pid), encoding='ascii')
            state_path.write_text(json.dumps({
                'pid': sleeper.pid,
                'processStartTime': '2000-01-01T00:00:00.0000000Z',
                'commandLine': 'external-process-not-the-debug-server',
                'port': port,
                'url': f'http://127.0.0.1:{port}',
            }), encoding='utf-8')
            result = self.run_action('stop', env={'LOCAL_DEBUG_PORT': str(port)})
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(port), combined)
            self.assertNotIn('[成功]', combined)
            self.assertIsNone(sleeper.poll(), combined)
            self.assertEqual(listener.getsockname()[1], port)
            self.assertFalse(pid_path.exists())
            self.assertFalse(state_path.exists())
        finally:
            listener.close()
            if sleeper.poll() is None:
                sleeper.terminate()
                sleeper.wait(timeout=5)


class LocalDebugIdentityProbeFailureTest(LocalDebugIsolatedTestCase):
    def test_identity_probe_failure_cleans_new_process_tree_and_port(self):
        fake_python = Path(self.temp_dir.name, 'fake-python.cmd')
        marker = Path(self.temp_dir.name, 'parent.pid')
        real_python = str(Path(sys.executable))
        fake_python.write_bytes(
            (
                '@echo off\r\n'
                'if "%~1"=="-c" exit /b 0\r\n'
                'if "%~1"=="tools/prepare_local_debug.py" exit /b 0\r\n'
                f'"{real_python}" -c "import os,socket,time; '
                "open(os.environ['LOCAL_DEBUG_MARKER'],'w').write(str(os.getppid())); "
                's=socket.socket(); '
                "s.bind(('127.0.0.1',int(os.environ['FLASK_RUN_PORT']))); "
                's.listen(1); time.sleep(2)"\r\n'
            ).encode('ascii')
        )
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        env = {
            'LOCAL_DEBUG_PORT': str(port),
            'LOCAL_DEBUG_PYTHON': str(fake_python),
            'LOCAL_DEBUG_HEALTH_TIMEOUT_SECONDS': '1',
            'LOCAL_DEBUG_TEST_FORCE_IDENTITY_FAILURE': '1',
            'LOCAL_DEBUG_MARKER': str(marker),
        }
        try:
            result = self.run_action('start', env=env)
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('无法验证本地调试服务进程身份', combined)
            self.assertFalse((self.runtime_root / 'local-debug.pid').exists())
            self.assertFalse((self.runtime_root / 'local-debug-state.json').exists())
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(marker.exists(), combined)
            parent_pid = int(marker.read_text(encoding='ascii'))
            process_check = subprocess.run(
                [
                    'powershell.exe', '-NoProfile', '-Command',
                    f'if (Get-Process -Id {parent_pid} -ErrorAction SilentlyContinue) {{ exit 1 }}',
                ],
                capture_output=True,
                check=False,
            )
            self.assertEqual(process_check.returncode, 0, combined)
            check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.assertNotEqual(check.connect_ex(('127.0.0.1', port)), 0)
            finally:
                check.close()
        finally:
            self.run_action('stop', env=env)
            time.sleep(3)


class LocalDebugCleanupFailureTest(LocalDebugIsolatedTestCase):
    def test_cleanup_failure_retains_manageable_state_for_later_stop(self):
        fake_python = Path(self.temp_dir.name, 'fake-python.cmd')
        marker = Path(self.temp_dir.name, 'parent.pid')
        real_python = str(Path(sys.executable))
        fake_python.write_bytes(
            (
                '@echo off\r\n'
                'if "%~1"=="-c" exit /b 0\r\n'
                'if "%~1"=="tools/prepare_local_debug.py" exit /b 0\r\n'
                f'"{real_python}" -c "import os,time; '
                "open(os.environ['LOCAL_DEBUG_MARKER'],'w').write(str(os.getppid())); "
                'time.sleep(30)"\r\n'
            ).encode('ascii')
        )
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        env = {
            'LOCAL_DEBUG_PORT': str(port),
            'LOCAL_DEBUG_PYTHON': str(fake_python),
            'LOCAL_DEBUG_TEST_FORCE_IDENTITY_FAILURE': '1',
            'LOCAL_DEBUG_TEST_FORCE_CLEANUP_FAILURE': '1',
            'LOCAL_DEBUG_MARKER': str(marker),
        }
        try:
            result = self.run_action('start', env=env)
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(combined.count('[失败]'), 1, combined)
            self.assertIn('无法清理', combined)
            pid_path = self.runtime_root / 'local-debug.pid'
            state_path = self.runtime_root / 'local-debug-state.json'
            self.assertTrue(pid_path.exists(), combined)
            self.assertTrue(state_path.exists(), combined)
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(marker.exists(), combined)
            state = json.loads(state_path.read_text(encoding='utf-8-sig'))
            recorded_pid = int(pid_path.read_text(encoding='ascii').strip())
            self.assertEqual(state['pid'], recorded_pid)
            process_check = subprocess.run(
                [
                    'powershell.exe', '-NoProfile', '-Command',
                    f'if (-not (Get-Process -Id {recorded_pid} -ErrorAction SilentlyContinue)) {{ exit 1 }}',
                ],
                capture_output=True,
                check=False,
            )
            self.assertEqual(process_check.returncode, 0, combined)
        finally:
            self.run_action('stop', env={'LOCAL_DEBUG_PORT': str(port)})
            if marker.exists():
                parent_pid = int(marker.read_text(encoding='ascii'))
                subprocess.run(
                    ['taskkill.exe', '/PID', str(parent_pid), '/T', '/F'],
                    capture_output=True,
                    check=False,
                )
            time.sleep(1)


class LocalDebugHealthIdentityTest(LocalDebugIsolatedTestCase):
    def test_health_success_requires_new_process_identity(self):
        fake_python = Path(self.temp_dir.name, 'fake-python.cmd')
        marker = Path(self.temp_dir.name, 'external.pid')
        real_python = str(Path(sys.executable))
        fake_python.write_bytes(
            (
                '@echo off\r\n'
                'if "%~1"=="-c" exit /b 0\r\n'
                'if "%~1"=="tools/prepare_local_debug.py" exit /b 0\r\n'
                f'start "" /b "{real_python}" -c "import os,time,http.server; '
                'time.sleep(2); '
                "open(os.environ['LOCAL_DEBUG_MARKER'],'w').write(str(os.getpid())); "
                "server=http.server.ThreadingHTTPServer(('127.0.0.1',int(os.environ['FLASK_RUN_PORT'])),http.server.SimpleHTTPRequestHandler); "
                'server.serve_forever()"\r\n'
                'timeout /t 1 /nobreak >nul\r\n'
            ).encode('ascii')
        )
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        env = {
            'LOCAL_DEBUG_PORT': str(port),
            'LOCAL_DEBUG_PYTHON': str(fake_python),
            'LOCAL_DEBUG_HEALTH_TIMEOUT_SECONDS': '6',
            'LOCAL_DEBUG_MARKER': str(marker),
        }
        try:
            result = self.run_action('start', env=env)
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, combined)
            self.assertNotIn('[成功]', combined)
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(marker.exists(), combined)
            external_pid = int(marker.read_text(encoding='ascii'))
            process_check = subprocess.run(
                [
                    'powershell.exe', '-NoProfile', '-Command',
                    f'if (-not (Get-Process -Id {external_pid} -ErrorAction SilentlyContinue)) {{ exit 1 }}',
                ],
                capture_output=True,
                check=False,
            )
            self.assertEqual(process_check.returncode, 0, combined)
        finally:
            if marker.exists():
                external_pid = int(marker.read_text(encoding='ascii'))
                subprocess.run(
                    ['taskkill.exe', '/PID', str(external_pid), '/T', '/F'],
                    capture_output=True,
                    check=False,
                )
            self.run_action('stop', env={'LOCAL_DEBUG_PORT': str(port)})
            time.sleep(1)


class LocalDebugBrowserOpenTest(LocalDebugIsolatedTestCase):
    def test_browser_open_failure_keeps_healthy_service_running(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        env = {
            'LOCAL_DEBUG_PORT': str(port),
            'LOCAL_DEBUG_TEST_FORCE_BROWSER_FAILURE': '1',
        }
        try:
            result = self.run_action('start', env=env)
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, combined)
            self.assertEqual(combined.count('[提示]'), 1, combined)
            self.assertNotIn('[成功]', combined)
            self.assertIn('手动访问', combined)
            self.assertTrue((self.runtime_root / 'local-debug.pid').exists())
            self.assertTrue((self.runtime_root / 'local-debug-state.json').exists())
            with urlopen(f'http://127.0.0.1:{port}', timeout=5) as response:
                self.assertEqual(response.status, 200)
        finally:
            stopped = self.run_action('stop', env={'LOCAL_DEBUG_PORT': str(port)})
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertFalse((self.runtime_root / 'local-debug.pid').exists())
            self.assertFalse((self.runtime_root / 'local-debug-state.json').exists())


class LocalDebugStatePortTest(LocalDebugIsolatedTestCase):
    def test_start_with_new_request_port_reports_recorded_state_url(self):
        first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        first.bind(('127.0.0.1', 0))
        first_port = first.getsockname()[1]
        first.close()
        second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        second.bind(('127.0.0.1', 0))
        second_port = second.getsockname()[1]
        second.close()
        try:
            started = self.run_action('start', env={'LOCAL_DEBUG_PORT': str(first_port)})
            self.assertEqual(started.returncode, 0, started.stderr)
            state = json.loads(
                (self.runtime_root / 'local-debug-state.json').read_text(encoding='utf-8-sig')
            )
            self.assertEqual(state['port'], first_port)
            self.assertEqual(state['url'], f'http://127.0.0.1:{first_port}')
            self.assertTrue(state['processStartTime'])
            command_line = state['commandLine'].replace('/', '\\').lower()
            self.assertIn('tools\\local_debug_server.py', command_line)
            requested_again = self.run_action(
                'start', env={'LOCAL_DEBUG_PORT': str(second_port)}
            )
            self.assertEqual(requested_again.returncode, 0, requested_again.stderr)
            self.assertIn(f'http://127.0.0.1:{first_port}', requested_again.stdout)
            self.assertNotIn(f'http://127.0.0.1:{second_port}', requested_again.stdout)
            status = self.run_action('status', env={'LOCAL_DEBUG_PORT': str(second_port)})
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn(f'访问地址: http://127.0.0.1:{first_port}', status.stdout)
            self.assertIn(f'本次请求地址: http://127.0.0.1:{second_port}', status.stdout)
        finally:
            stopped = self.run_action('stop', env={'LOCAL_DEBUG_PORT': str(second_port)})
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertFalse((self.runtime_root / 'local-debug.pid').exists())
            self.assertFalse((self.runtime_root / 'local-debug-state.json').exists())
            for check_port in (first_port, second_port):
                check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    self.assertNotEqual(check.connect_ex(('127.0.0.1', check_port)), 0)
                finally:
                    check.close()


class LocalDebugStartupCleanupTest(LocalDebugIsolatedTestCase):
    def test_health_timeout_cleans_new_process_and_state(self):
        fake_python = Path(self.temp_dir.name, 'fake-python.cmd')
        fake_python.write_bytes(
            b'@echo off\r\n'
            b'if "%~1"=="-c" exit /b 0\r\n'
            b'if "%~1"=="tools/prepare_local_debug.py" exit /b 0\r\n'
            b'timeout /t 3 /nobreak >nul\r\n'
        )
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        env = {
            'LOCAL_DEBUG_PORT': str(port),
            'LOCAL_DEBUG_PYTHON': str(fake_python),
            'LOCAL_DEBUG_HEALTH_TIMEOUT_SECONDS': '1',
        }
        try:
            result = self.run_action('start', env=env)
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('within 1 seconds', combined)
            self.assertFalse(Path(self.runtime_root, 'local-debug.pid').exists())
            self.assertFalse(Path(self.runtime_root, 'local-debug-state.json').exists())
            check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.assertNotEqual(check.connect_ex(('127.0.0.1', port)), 0)
            finally:
                check.close()
        finally:
            self.run_action('stop', env=env)


class LocalDebugErrorOutputTest(LocalDebugIsolatedTestCase):
    def test_foreign_port_error_is_concise_and_does_not_kill_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            result = self.run_action('start', env={'LOCAL_DEBUG_PORT': str(port)})
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(port), combined)
            self.assertNotIn('CategoryInfo', combined)
            self.assertNotIn('FullyQualifiedErrorId', combined)
            self.assertNotIn('Write-Error', combined)
            self.assertEqual(listener.getsockname()[1], port)
        finally:
            listener.close()


class LocalDebugRuntimeOutputTest(LocalDebugIsolatedTestCase):

    def test_status_output_is_utf8_chinese_without_mojibake(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.close()
        result = self.run_action('status', env={'LOCAL_DEBUG_PORT': str(port)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('访问地址: http://127.0.0.1:', result.stdout)
        self.assertNotIn('\ufffd', result.stdout)
        self.assertNotIn('锛', result.stdout)

    def test_stop_output_does_not_leak_false_when_no_service_is_recorded(self):
        pid_path = self.runtime_root / 'local-debug.pid'
        self.assertFalse(pid_path.exists(), 'test requires no recorded local debug service')
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.close()
        result = self.run_action('stop', env={'LOCAL_DEBUG_PORT': str(port)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('\nFalse\n', '\n' + result.stdout + '\n')
        self.assertEqual(result.stdout.count('[提示]'), 1)

    def test_start_output_hides_prepare_details_and_keeps_one_success_line(self):
        pid_path = self.runtime_root / 'local-debug.pid'
        self.assertFalse(pid_path.exists(), 'test requires no recorded local debug service')
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.close()
        env = {'LOCAL_DEBUG_PORT': str(port)}
        try:
            result = self.run_action('start', env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count('[成功]'), 1)
            self.assertNotIn('student_id=', result.stdout)
            self.assertNotIn('local debug database ready:', result.stdout)
        finally:
            self.run_action('stop', env=env)

    def test_menu_marks_foreign_non_http_listener_as_abnormal(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            result = self.run_action(
                'menu', env={'LOCAL_DEBUG_PORT': str(port)}, input_text='0\n'
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('状态：状态异常', result.stdout)
        finally:
            listener.close()


class LocalDebugCmdContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / '本地调试.cmd'
        cls.raw = cls.path.read_bytes() if cls.path.exists() else b''
        cls.source = cls.raw.decode('ascii', errors='replace') if cls.raw else ''

    def test_wrapper_is_bom_free_ascii_crlf_and_starts_with_echo_off(self):
        self.assertTrue(self.raw.startswith(b'@echo off\r\n'))
        self.assertFalse(self.raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', self.raw.replace(b'\r\n', b''))
        self.assertTrue(all(byte < 128 for byte in self.raw))

    def test_wrapper_delegates_menu_and_noninteractive_actions(self):
        self.assertIn('tools\\local_debug.ps1', self.source)
        self.assertIn('-Action menu', self.source)
        self.assertIn('-Action "%~1"', self.source)
        self.assertNotIn('set /p', self.source.lower())
        self.assertNotIn('echo [1]', self.source.lower())


if __name__ == '__main__':
    unittest.main()
