import os
import tempfile
import unittest

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'user_profile_template_test.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.models import User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class UserProfileTemplateTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def test_profile_falls_back_to_legacy_group_name(self):
        user = User(
            number='U001',
            department='办公部',
            name='信息员测试',
            gender='-',
            grade='-',
            college='-',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id='user001',
            password_hash=generate_password_hash('password'),
            role='信息员',
            group='一组',
            group_id=None,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

        response = self.client.get('/user/profile')

        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn('办公部 - 一组'.encode('utf-8'), response.get_data())
        finally:
            response.close()


if __name__ == '__main__':
    unittest.main()
