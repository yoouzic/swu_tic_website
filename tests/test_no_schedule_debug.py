"""Real subprocess checks for an isolated, persistent no-timetable profile."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from urllib.request import urlopen
from contextlib import closing, contextmanager

from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def database_connection(path):
    with closing(sqlite3.connect(path)) as con:
        with con:
            yield con


class NoScheduleDebugTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='no-schedule-launcher-')
        self.runtime = Path(self.temp.name)/'runtime'
        self.storage = Path(self.temp.name)/'storage'
        self.database = self.runtime/'lecture_forms-debug.db'
        self.env = os.environ.copy()
        self.env.update(LOCAL_DEBUG_MODE='1', INSTANCE_DIR=str(self.runtime),
                        SQLITE_DB_PATH=str(self.database), LOCAL_DEBUG_STORAGE_ROOT=str(self.storage),
                        LOCAL_DEBUG_RUNTIME_ROOT=str(self.runtime), LOCAL_DEBUG_NO_BROWSER='1',
                        LOCAL_DEBUG_PYTHON=sys.executable, PYTHONUTF8='1')

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, **env):
        return subprocess.run([sys.executable, 'tools/prepare_no_schedule_debug.py', '--password', '1234564'],
                              cwd=ROOT, env={**self.env, **env}, capture_output=True, text=True, encoding='utf-8')

    def action(self, action, **env):
        return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                               str(ROOT/'tools/local_debug.ps1'),'-Profile','no-schedule','-Action',action],
                              cwd=ROOT, env={**self.env, **env}, capture_output=True, text=True, encoding='utf-8')

    def test_fresh_profile_has_roles_and_calendar_but_no_schedule_or_forms(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        with database_connection(self.database) as con:
            con.row_factory = sqlite3.Row
            users = {r['student_id']:dict(r) for r in con.execute('SELECT * FROM users')}
            self.assertEqual(set(users), {'super','manager','leader','center','user001','user002','user003'})
            for account in users.values():
                self.assertTrue(check_password_hash(account['password_hash'], '1234564'))
            for table in ('courses','schedule_import_batches','listening_assistant_schedule_entries',
                          'schedule_semester_selections','lecture_forms','automation_schedule_datasets'):
                self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)
            self.assertTrue(users['user001']['group_id'])
            self.assertEqual(users['user001']['group_id'], users['leader']['group_id'])
            self.assertNotEqual(users['user001']['group_id'], users['user002']['group_id'])
            self.assertNotEqual(users['user001']['department'], users['user003']['department'])
            self.assertEqual(con.execute('SELECT leader_id FROM groups WHERE id=?', (users['leader']['group_id'],)).fetchone()[0],users['leader']['id'])
            for account, expected in [('leader','审表_小组'),('manager','审表_部门'),('center','审表_中心')]:
                permissions = {r[0] for r in con.execute('SELECT p.name FROM permissions p JOIN role_permissions rp ON p.id=rp.permission_id WHERE rp.role=?',(f"特殊角色_{users[account]['id']}",))}
                self.assertIn(expected, permissions)
                self.assertNotIn('填表', permissions)
            self.assertTrue(con.execute("SELECT value FROM system_settings WHERE key='teaching_current_semester'").fetchone()[0])
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        marker = json.loads((self.runtime/'no-schedule-profile.json').read_text(encoding='utf-8'))
        self.assertEqual(marker['profile'],'no-schedule')
        self.assertEqual(Path(marker['database']), self.database.resolve())
        self.assertTrue((self.storage/'templates'/'schedule.xlsx').exists())

    def test_repeat_initialization_preserves_passwords_settings_drafts_and_imports(self):
        first = self.prepare()
        self.assertEqual(first.returncode,0,first.stderr)
        with database_connection(self.database) as con:
            officer_id = con.execute("SELECT id FROM users WHERE student_id='user001'").fetchone()[0]
            con.execute("UPDATE users SET password_hash=? WHERE student_id='user001'",(generate_password_hash('ChangedLocal!'),))
            con.execute("UPDATE system_settings SET value='18' WHERE key='teaching_total_weeks'")
            con.execute("INSERT INTO lecture_form_drafts(user_id,draft_key,payload_json) VALUES(?,?,?)",(officer_id,'submit_form','{"course_title":"保留测试草稿"}'))
            con.execute("INSERT INTO courses(course_code,selection_code,course_name,semester) VALUES('KEEP','1','保留导入课程','TEST')")
            before = {t:con.execute(f'SELECT * FROM {t}').fetchall() for t in ('users','system_settings','courses','lecture_form_drafts')}
        second = self.prepare()
        self.assertEqual(second.returncode,0,second.stderr)
        with database_connection(self.database) as con:
            after = {t:con.execute(f'SELECT * FROM {t}').fetchall() for t in before}
        self.assertEqual(before,after)

    def test_existing_unmarked_database_is_rejected_without_any_write(self):
        self.runtime.mkdir()
        with database_connection(self.database) as con:
            con.execute('CREATE TABLE sentinel(value TEXT)')
            con.execute("INSERT INTO sentinel VALUES('keep original')")
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        result = self.prepare()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('未经此脚本初始化',result.stderr)
        self.assertEqual(before,hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_requires_debug_guard_and_refuses_business_database(self):
        for env, expected in [({'LOCAL_DEBUG_MODE':'0'},'LOCAL_DEBUG_MODE=1'),
                              ({'SQLITE_DB_PATH':str(ROOT/'data/instance/lecture_forms.db')},'专用测试数据库')]:
            with self.subTest(env=env):
                result = self.prepare(**env)
                self.assertNotEqual(result.returncode,0)
                self.assertIn(expected,result.stderr)

    def test_no_schedule_profile_never_adopts_other_profile_state(self):
        self.runtime.mkdir()
        state = self.runtime/'local-debug-state.json'
        state.write_text(json.dumps({'profile':'default','database':str(self.database),'pid':1}),encoding='utf-8')
        before = state.read_bytes()
        result = self.action('stop')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('属于其他测试环境',result.stderr)
        self.assertTrue(state.exists(), 'Must preserve the other profile state file')
        self.assertEqual(before,state.read_bytes())

    def test_foreign_port_does_not_initialize_database(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); listener.listen(1)
            result = self.action('start',LOCAL_DEBUG_PORT=str(listener.getsockname()[1]))
            self.assertNotEqual(result.returncode,0)
            self.assertIn('占用',result.stderr)
            self.assertFalse(self.database.exists())

    def test_new_cmd_is_double_click_launch_and_utf8_crlf(self):
        path = ROOT/'无课表测试.cmd'
        self.assertTrue(path.is_file())
        raw = path.read_bytes()
        self.assertNotIn(b'\xef\xbb\xbf',raw[:3])
        self.assertEqual(raw.count(b'\n'),raw.count(b'\r\n'))
        text = raw.decode('utf-8')
        self.assertIn('chcp 65001',text)
        self.assertIn('-Profile no-schedule',text)
        self.assertIn('-Action launch',text)

    def test_marker_wrong_types_are_a_controlled_error_without_database_write(self):
        self.assertEqual(self.prepare().returncode,0)
        path = self.runtime/'no-schedule-profile.json'
        marker = json.loads(path.read_text(encoding='utf-8'))
        marker['database'] = None
        path.write_text(json.dumps(marker),encoding='utf-8')
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        result = self.prepare()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('标记',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
        self.assertEqual(before,hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_protected_storage_subtree_is_rejected_before_initialization(self):
        fake_tools = Path(self.temp.name)/'fake-repository'/'tools'
        fake_tools.mkdir(parents=True)
        script = fake_tools/'prepare_no_schedule_debug.py'
        script.write_bytes((ROOT/'tools/prepare_no_schedule_debug.py').read_bytes())
        protected_storage = fake_tools.parent/'data/storage/exports'
        result = subprocess.run([sys.executable,str(script),'--password','1234564'],cwd=ROOT,
                                env={**self.env,'LOCAL_DEBUG_STORAGE_ROOT':str(protected_storage)},
                                capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('存储目录',result.stderr)
        self.assertFalse(self.database.exists())
        self.assertFalse(protected_storage.exists())

    def test_real_windows_start_restart_stop_keeps_data_and_separate_cookie(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            port = str(listener.getsockname()[1])
        try:
            started = self.action('start',LOCAL_DEBUG_PORT=port)
            self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            with urlopen(f'http://127.0.0.1:{port}/auth/login',timeout=5) as response:
                self.assertEqual(response.status,200)
                self.assertIn('swu_tic_no_schedule_debug=',response.headers.get('Set-Cookie',''))
            with database_connection(self.database) as con:
                con.execute("UPDATE system_settings SET value='17' WHERE key='teaching_total_weeks'")
            restarted = self.action('restart',LOCAL_DEBUG_PORT=port)
            self.assertEqual(restarted.returncode,0,restarted.stdout+restarted.stderr)
            with database_connection(self.database) as con:
                self.assertEqual(con.execute("SELECT value FROM system_settings WHERE key='teaching_total_weeks'").fetchone()[0],'17')
                self.assertEqual(con.execute('SELECT COUNT(*) FROM courses').fetchone()[0],0)
        finally:
            stopped = self.action('stop',LOCAL_DEBUG_PORT=port)
            self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)


if __name__ == '__main__':
    unittest.main()
