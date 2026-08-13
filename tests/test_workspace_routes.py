import os
import tempfile
import unittest
from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'workspace_routes.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.models import User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class WorkspaceRouteTest(unittest.TestCase):
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
        self.information_officer = self.create_user('user001', 'U001', '周雨', '信息员')
        self.manager = self.create_user('manager', 'M001', '林老师', '管理员')
        self.super_admin = self.create_user('super', 'SA001', '超管测试', '超级管理员')
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()

    def create_user(self, student_id, number, name, role):
        user = User(number=number, department='办公部', name=name, gender='-', grade='-', college='-', major='-', dormitory='-', phone='-', qq='-', student_id=student_id, password_hash=generate_password_hash('password'), role=role, group='一组', is_active=True)
        db.session.add(user)
        db.session.flush()
        return user

    def login_session(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def test_information_officer_root_renders_shared_workspace(self):
        self.login_session(self.information_officer)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-workspace-role="information-officer"', response.data)
        self.assertIn('填写听课表'.encode('utf-8'), response.data)
        self.assertIn('placeholder="搜索课程、教师或地点"'.encode('utf-8'), response.data)
        self.assertIn('aria-label="提交搜索"'.encode('utf-8'), response.data)

    def test_manager_root_renders_review_work(self):
        self.login_session(self.manager)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-workspace-role="manager"', response.data)
        self.assertIn('处理审核'.encode('utf-8'), response.data)
        self.assertIn('placeholder="搜索听课人、教师或课程"'.encode('utf-8'), response.data)

    def test_legacy_dashboards_redirect_to_root(self):
        self.login_session(self.super_admin)
        response = self.client.get('/admin/super_admin_dashboard')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_login_redirects_every_role_to_root(self):
        response = self.client.post('/auth/login', data={'student_id': self.information_officer.student_id, 'password': 'password'})
        self.assertEqual(response.headers['Location'], '/')


if __name__ == '__main__':
    unittest.main()
