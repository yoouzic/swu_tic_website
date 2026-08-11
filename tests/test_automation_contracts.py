import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from app.app import app


class AutomationBootstrapTest(unittest.TestCase):
    def test_fresh_worker_entry_registers_automated_review_tasks(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='synthetic-worker-contract-') as temp_dir:
            temp_root = Path(temp_dir)
            child_env = os.environ.copy()
            child_env.update(
                {
                    'AUTOMATION_UPLOAD_DIR': str(temp_root / 'automation'),
                    'AUTO_REVIEW_UPLOAD_DIR': str(temp_root / 'auto-review'),
                    'CELERY_BROKER_URL': 'redis://127.0.0.1:6389/15',
                    'CELERY_RESULT_BACKEND': 'redis://127.0.0.1:6389/14',
                    'CELERY_TASK_ALWAYS_EAGER': '0',
                    'DATABASE_URL': '',
                    'DEEPSEEK_API_KEY': '',
                    'FLASK_ENV': 'development',
                    'FLASK_DEBUG': '0',
                    'INSTANCE_DIR': str(temp_root / 'instance'),
                    'PYTHONPATH': os.pathsep.join(
                        filter(None, [str(repo_root), child_env.get('PYTHONPATH')])
                    ),
                    'SECRET_KEY': 'synthetic-worker-contract-secret',
                    'SQLITE_DB_PATH': str(temp_root / 'worker.sqlite'),
                    'UPLOAD_FOLDER': str(temp_root / 'uploads'),
                }
            )
            child_script = textwrap.dedent(
                """
                import json

                import celery_worker

                task_names = {
                    'review_automation.assess_form',
                    'review_automation.run_batch',
                }
                print(json.dumps(sorted(task_names & set(celery_worker.celery_app.tasks))))
                """
            )
            result = subprocess.run(
                [sys.executable, '-c', child_script],
                cwd=repo_root,
                env=child_env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(
            result.returncode,
            0,
            msg=f'fresh worker import failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}',
        )
        observed = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(
            observed,
            ['review_automation.assess_form', 'review_automation.run_batch'],
        )

    def test_default_configuration_is_safe_and_explicit(self):
        self.assertEqual(app.config['DEEPSEEK_BASE_URL'], 'https://api.deepseek.com')
        self.assertEqual(app.config['DEEPSEEK_MODEL'], 'deepseek-v4-flash')
        self.assertTrue(app.config['DEEPSEEK_THINKING_ENABLED'])
        self.assertEqual(app.config['DEEPSEEK_MAX_TOKENS'], 8192)
        self.assertFalse(app.config['CELERY_TASK_ALWAYS_EAGER'])
        self.assertNotIn('DEEPSEEK_API_KEY', app.config.get('AUTOMATION_PUBLIC_CONFIG', {}))

    def test_automation_blueprint_is_registered(self):
        self.assertIn('review_automation.health', {rule.endpoint for rule in app.url_map.iter_rules()})


if __name__ == '__main__':
    unittest.main()
