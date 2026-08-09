import os
from pathlib import Path
import tempfile
import unittest
from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'activity_center.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.models import User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class ActivityCenterTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.user = User(number='U001', department='办公部', name='周雨', gender='-', grade='-', college='-', major='-', dormitory='-', phone='-', qq='-', student_id='user001', password_hash=generate_password_hash('password'), role='信息员', group='一组', is_active=True)
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()

    def login_session(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def test_activity_center_supports_registration_and_records_tabs(self):
        self.login_session(self.user)
        response = self.client.get('/user/listening_registration?tab=records')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-activity-tab="registration"', response.data)
        self.assertIn(b'data-activity-tab="records"', response.data)
        self.assertIn(b'data-active-tab="records"', response.data)

    def test_my_forms_compatibility_route_redirects_to_records_tab(self):
        self.login_session(self.user)
        response = self.client.get('/user/my_forms')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/user/listening_registration?tab=records'))

    def test_lecture_form_has_section_navigation_and_save_status(self):
        template = Path('app/templates/user/lecture_form.html').read_text(encoding='utf-8')
        self.assertIn('class="form-section-nav"', template)
        self.assertIn('id="draftSaveStatus"', template)
        self.assertNotIn('bg-primary text-white', template)


if __name__ == '__main__':
    unittest.main()
