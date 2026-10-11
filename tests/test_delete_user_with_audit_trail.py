# -*- coding: utf-8 -*-
"""Regression: physically deleting a user must survive existing audit rows.

Pre-fix behaviour: ``DELETE /admin/api/users/<id>`` returned HTTP 500 with
``NOT NULL constraint failed: password_audit_logs.target_user_id`` for every user
that had ever had a password event. Because the contacts import and every admin
password reset write a ``PasswordAuditLog`` row, that was effectively every user
in the system, making the delete path unusable.

The fix declares ``PasswordAuditLog.target_user_id`` nullable, matching what the
ORM already tries to do on parent deletion: keep the audit trail, clear the link.
"""
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import PasswordAuditLog, PersonnelMovementRecord, User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

ADMIN_PASSWORD = 'password'


class DeleteUserWithAuditTrailTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='del-user-')
        self.db_path = Path(self.temp_dir.name) / f'del-user-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()

        self.admin = User(
            number='S1', department='办公部', name='Super Admin', gender='-',
            grade='-', college='Test', major='-', dormitory='-', phone='-', qq='-',
            student_id='s-super', password_hash=generate_password_hash(ADMIN_PASSWORD),
            role='超级管理员', group='一组', is_active=True,
        )
        db.session.add(self.admin)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.admin.id
            sess['user_role'] = self.admin.role
            sess['user_name'] = self.admin.name

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _make_user(self, number='91001', student_id='del-target-1'):
        user = User(
            number=number, department='办公部', name=f'Target {number}', gender='男',
            grade='2024', college='Test College', major='M', dormitory='D',
            phone='13800000009', qq='12345', student_id=student_id,
            password_hash=generate_password_hash('123456'), role='信息员',
            group='一组', is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def test_delete_user_that_has_password_audit_rows(self):
        target = self._make_user()
        target_id = target.id
        db.session.add(PasswordAuditLog(
            actor_user_id=self.admin.id, target_user_id=target_id,
            action='import_create_user_password_init', result='success',
        ))
        db.session.commit()

        response = self.client.delete(
            f'/admin/api/users/{target_id}', json={'password': ADMIN_PASSWORD},
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(response.get_json().get('success'), body)

        # the user is gone
        self.assertIsNone(db.session.get(User, target_id))

        # the audit trail survives, with the link cleared rather than the row lost
        log = PasswordAuditLog.query.filter_by(
            action='import_create_user_password_init').one()
        self.assertIsNone(log.target_user_id)

        # and the deletion is recorded in the personnel movement log
        movement = PersonnelMovementRecord.query.filter_by(
            action_type='删除用户', target_user_name=target.name).one()
        self.assertEqual(movement.operator_user_id, self.admin.id)

    def test_delete_refuses_without_correct_password(self):
        target = self._make_user(number='91002', student_id='del-target-2')
        target_id = target.id
        response = self.client.delete(
            f'/admin/api/users/{target_id}', json={'password': 'wrong-password'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(db.session.get(User, target_id))

    def test_delete_still_refuses_users_with_lecture_forms(self):
        from app.models import LectureForm
        target = self._make_user(number='91003', student_id='del-target-3')
        target_id = target.id
        db.session.add(PasswordAuditLog(
            actor_user_id=self.admin.id, target_user_id=target_id,
            action='import_create_user_password_init', result='success',
        ))
        db.session.add(LectureForm(
            listener_name=target.name, listener_number=target.number,
            lecture_date='2026-01-01', class_period='3-4节', lecture_location='A101',
            teacher_name='T', teacher_college='C', course_title='Course',
            student_grade_class='G', teaching_method='M', classroom_discipline='D',
            classroom_atmosphere='A', courseware_quality='Q', overall_effect='E',
            quality_case='Case', course_feedback='F', suggestions='S',
            student_signature1='sig', contact_phone1='123', status='待审核',
        ))
        db.session.commit()

        response = self.client.delete(
            f'/admin/api/users/{target_id}', json={'password': ADMIN_PASSWORD},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(db.session.get(User, target_id))


if __name__ == '__main__':
    unittest.main()
