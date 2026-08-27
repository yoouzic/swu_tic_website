import ast
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class AdminAutoReviewRouteTest(unittest.TestCase):
    RETAINED = {
        'admin.auto_review_page': '/admin/auto_review',
        'admin.batch_auto_check': '/admin/api/review/batch_auto_check',
        'admin.batch_auto_check_preview': '/admin/api/review/batch_auto_check/preview',
        'admin.get_auto_check_status': '/admin/api/review/auto_check/status',
    }

    RETIRED = {
        'admin.auto_review_settings',
        'admin.auto_review_upload',
        'admin.auto_review_feedback_run',
        'admin.auto_review_download',
    }

    def test_auto_review_page_kept_and_legacy_endpoints_retired(self):
        rules_by_endpoint = {
            rule.endpoint: rule.rule
            for rule in app.url_map.iter_rules()
        }

        for endpoint, rule in self.RETAINED.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(rules_by_endpoint.get(endpoint), rule)

        for endpoint in self.RETIRED:
            with self.subTest(endpoint=endpoint):
                self.assertNotIn(endpoint, rules_by_endpoint)


    def test_auto_review_engine_only_imported_by_allowed_review_module(self):
        allowed = {'app/blueprints/admin/review.py'}
        found = set()
        for path in Path('app').rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == 'AutoReviewEngine':
                            found.add(str(path).replace('\\', '/'))
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == 'AutoReviewEngine':
                            found.add(str(path).replace('\\', '/'))
        self.assertEqual(found, allowed)


class AdminAutoReviewHttpRegressionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'auto-review-http.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.user = User(
            number='S100',
            department='办公部',
            name='超级管理员',
            gender='-',
            grade='-',
            college='Test',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id='super',
            password_hash=generate_password_hash('password'),
            role='超级管理员',
            group='未分配小组',
            group_id=None,
            is_active=True,
        )
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _auth_client(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = self.user.id
        return client

    def test_auto_review_page_redirects_to_automation_tab(self):
        response = self._auth_client().get('/admin/auto_review')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/system_management', response.headers['Location'])
        self.assertIn('tab=automation', response.headers['Location'])

    def test_retired_auto_review_urls_return_404_for_authenticated_super_admin(self):
        client = self._auth_client()
        requests = [
            ('GET', '/admin/api/auto_review/settings'),
            ('POST', '/admin/api/auto_review/settings'),
            ('POST', '/admin/api/auto_review/upload'),
            ('POST', '/admin/api/auto_review/feedback_run'),
            ('GET', '/admin/auto_review/download'),
        ]
        for method, url in requests:
            with self.subTest(method=method, url=url):
                response = client.open(url, method=method)
                self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
