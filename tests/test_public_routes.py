import os
import tempfile
import unittest
from pathlib import Path


TEST_ROOT = tempfile.TemporaryDirectory(prefix='public-routes-')
TEST_ROOT_PATH = Path(TEST_ROOT.name)
os.environ['SQLITE_DB_PATH'] = str(TEST_ROOT_PATH / 'public_routes.db')
os.environ['INSTANCE_DIR'] = str(TEST_ROOT_PATH / 'instance')
os.environ['UPLOAD_FOLDER'] = str(TEST_ROOT_PATH / 'uploads')
os.environ['AUTOMATION_UPLOAD_DIR'] = str(TEST_ROOT_PATH / 'automation-uploads')
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app


class PublicRouteTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        TEST_ROOT.cleanup()

    def setUp(self):
        app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        self.client = app.test_client()

    def assert_public_page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        html = response.get_data(as_text=True)
        self.assertIn('<main class="public-shell">', html)
        self.assertIn('<div class="auth-layout">', html)
        self.assertIn('<section class="auth-panel"', html)
        self.assertEqual(html.count('class="btn btn-primary"'), 1)
        self.assertEqual(html.count('href="/auth/login"'), 1)
        self.assertNotIn('linear-gradient', html)
        self.assertNotIn('#667eea', html)
        self.assertNotIn('https://', html)

    def test_about_page_renders_public_shell_with_one_login_action(self):
        self.assert_public_page('/about')

    def test_anonymous_home_and_login_keep_the_welcoming_intro(self):
        self.assert_public_page('/')
        for path in ('/', '/auth/login'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn('认真听见，<br><em>共同成长。</em>', html)
                self.assertIn('记录课堂里的发现，让每一份教学反馈，成为校园向前的一小步。', html)
                self.assertIn('SWU · TEACHING INFORMATION CENTER', html)

    def test_help_page_renders_public_shell_with_one_login_action(self):
        self.assert_public_page('/help')


if __name__ == '__main__':
    unittest.main()
