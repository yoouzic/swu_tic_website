# -*- coding: utf-8 -*-
"""Application factory compatibility tests (Phase 2B-P2 Step 3)."""
import os
import tempfile
import unittest
from pathlib import Path

from app.app import app, create_app


def _route_counts(flask_app):
    rules = list(flask_app.url_map.iter_rules())
    admin = len([
        r for r in rules
        if r.rule.startswith('/admin') and r.endpoint.startswith('admin.')
    ])
    user = len([
        r for r in rules
        if r.rule.startswith('/user') and r.endpoint.startswith('user.')
    ])
    return len(rules), admin, user


class AppFactoryTests(unittest.TestCase):
    def test_module_singleton_import_succeeds(self):
        self.assertIsNotNone(app)
        self.assertTrue(hasattr(create_app, '__call__'))

    def test_singleton_route_contract(self):
        self.assertEqual(_route_counts(app), (168, 129, 20))

    def test_secondary_app_route_contract_and_override_consistency(self):
        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / 'secondary.sqlite'
            upload_dir = Path(tmp) / 'uploads'
            automation_upload_dir = Path(tmp) / 'automation'
            a2 = create_app({
                'TESTING': True,
                'SQLALCHEMY_DATABASE_URI': f"sqlite:///{sqlite_path}",
                'UPLOAD_FOLDER': str(upload_dir),
                'AUTOMATION_UPLOAD_DIR': str(automation_upload_dir),
                'DEEPSEEK_MODEL': 'override-model',
            })
            self.assertEqual(_route_counts(a2), (168, 129, 20))
            self.assertEqual(a2.config['DEEPSEEK_MODEL'], 'override-model')
            self.assertEqual(
                a2.config['AUTOMATION_PUBLIC_CONFIG']['DEEPSEEK_MODEL'],
                'override-model',
            )

    def test_csrf_strict_secondary_app_rejects_post_without_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / 'csrf.sqlite'
            a2 = create_app({
                'TESTING': True,
                'WTF_CSRF_ENABLED': True,
                'SQLALCHEMY_DATABASE_URI': f"sqlite:///{sqlite_path}",
                'UPLOAD_FOLDER': str(Path(tmp) / 'uploads'),
                'AUTOMATION_UPLOAD_DIR': str(Path(tmp) / 'automation'),
            })
            client = a2.test_client()
            response = client.post('/auth/logout')
            self.assertEqual(response.status_code, 400)
            self.assertIn(
                '请求校验失败，请刷新页面后重试。',
                response.get_data(as_text=True),
            )


if __name__ == '__main__':
    unittest.main()
