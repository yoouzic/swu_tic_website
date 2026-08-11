import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCRIPT = ROOT / 'tools' / 'business_acceptance' / 'runtime.ps1'


class BusinessAcceptanceRuntimeTest(unittest.TestCase):
    def run_runtime(self, action, runtime_root, *, extra_env=None):
        env = dict(__import__('os').environ)
        env.update({
            'ACCEPTANCE_RUNTIME_ROOT': str(runtime_root),
            'ACCEPTANCE_RUNTIME_PYTHON': sys.executable,
        })
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [
                'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(RUNTIME_SCRIPT), '-Action', action,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            encoding='utf-8',
            capture_output=True,
            check=False,
        )

    def test_runtime_script_has_safe_worker_contract(self):
        source = RUNTIME_SCRIPT.read_text(encoding='utf-8-sig')
        self.assertIn('celery_worker.celery_app', source)
        self.assertIn('--pool=threads', source)
        self.assertIn('--concurrency', source)
        self.assertIn('-WindowStyle Hidden', source)
        self.assertNotIn('/IM python', source)
        self.assertNotIn('/IM celery', source)
        self.assertNotIn('Stop-Process -Name', source)

    def test_stop_only_terminates_recorded_helper_and_keeps_unrelated_process(self):
        with tempfile.TemporaryDirectory(prefix='acceptance-runtime-') as tmp:
            runtime_root = Path(tmp)
            unrelated = subprocess.Popen(
                [sys.executable, '-c', 'import time; time.sleep(120)'],
                cwd=ROOT,
            )
            try:
                started = self.run_runtime(
                    'start-test-helper',
                    runtime_root,
                    extra_env={'ACCEPTANCE_RUNTIME_ALLOW_TEST_HELPER': '1'},
                )
                self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
                state_path = runtime_root / 'test-helper-state.json'
                self.assertTrue(state_path.exists(), started.stdout + started.stderr)
                state = json.loads(state_path.read_text(encoding='utf-8'))
                self.assertGreater(int(state['pid']), 0)
                self.assertTrue(state['processStartTime'])
                self.assertIn('sleep', state['commandLine'].lower())

                def process_exists(pid):
                    return subprocess.run(
                        ['powershell.exe', '-NoProfile', '-Command',
                         f'if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ exit 0 }}; exit 1'],
                        capture_output=True,
                        check=False,
                    ).returncode == 0

                stopped = self.run_runtime('stop-test-helper', runtime_root)
                self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                self.assertFalse(state_path.exists(), stopped.stdout + stopped.stderr)

                deadline = time.monotonic() + 5
                while process_exists(state['pid']) and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertFalse(process_exists(state['pid']))
                self.assertIsNone(unrelated.poll())
            finally:
                if unrelated.poll() is None:
                    unrelated.terminate()
                    unrelated.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
