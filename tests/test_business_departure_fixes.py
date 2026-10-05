"""REV-05: departure must leave outstanding latest reviews actionable."""
import os
import tempfile
from pathlib import Path

# Isolate bootstrap imports as well as each fixture database.
_bootstrap_dir = tempfile.TemporaryDirectory(prefix='business-departure-bootstrap-')
for _key, _directory in {
    'INSTANCE_DIR': 'instance', 'SQLITE_DB_PATH': 'bootstrap.sqlite',
    'UPLOAD_FOLDER': 'uploads', 'AUTOMATION_UPLOAD_DIR': 'automation',
}.items():
    os.environ[_key] = str(Path(_bootstrap_dir.name) / _directory)
os.environ['DATABASE_URL'] = ''
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'
os.environ['DEEPSEEK_API_KEY'] = ''
os.environ['SITE_CAPTURE_ENABLED'] = '0'

from app.models import db, PersonnelMovementRecord, RolePermission
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


class BusinessDepartureFixesTest(_ReviewMutationCompatibilityBase):
    def setUp(self):
        super().setUp()
        self.super_admin = self._make_user(
            'S100', 'super-s100', '超级管理员', self.dept_a.name, self.group_a1)
        db.session.commit()
        self._login(self.super_admin)

    def depart(self, user=None, password='password'):
        return self.client.post(
            f'/admin/api/users/{(user or self.officer).id}/depart',
            json={'password': password},
        )

    def test_pending_latest_blocks_departure_without_deactivating_or_moving_user(self):
        self._make_form(self.officer, status='待审核')
        response = self.depart()
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['outstanding_count'], 1)
        self.assertIn('未结', body['message'])
        self.assertEqual(body['action']['url'], '/admin/review_forms')
        db.session.refresh(self.officer)
        self.assertTrue(self.officer.is_active)
        self.assertEqual(self.officer.department, self.dept_a.name)
        self.assertEqual(self.officer.group_id, self.group_a1.id)
        self.assertEqual(PersonnelMovementRecord.query.count(), 0)

    def test_department_reviewed_latest_counts_once_despite_pending_history(self):
        old = self._make_form(self.officer, status='待审核', backfill_unique_id=False)
        latest = self._make_form(self.officer, status='部门已审核')
        latest.unique_id = old.id
        db.session.commit()
        response = self.depart()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['outstanding_count'], 1)
        self.assertTrue(self.officer.is_active)

    def test_terminal_latest_allows_departure_even_if_older_pending_row_was_touched(self):
        from datetime import datetime, timedelta
        for status in ('中心已审核', '已驳回'):
            old = self._make_form(self.officer, status='待审核')
            latest = self._make_form(self.officer, status=status)
            latest.unique_id = old.id
            old.created_at = datetime.now() + timedelta(days=1)
        db.session.commit()
        response = self.depart()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertFalse(self.officer.is_active)

    def test_password_validation_precedes_unfinished_form_disclosure(self):
        self._make_form(self.officer)
        response = self.depart(password='wrong')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('outstanding_count', response.get_json())
        self.assertTrue(self.officer.is_active)

    def test_repeated_departure_of_inactive_user_does_not_reactivate(self):
        self._make_form(self.officer)
        self.officer.is_active = False
        self.officer.department = '离任'
        db.session.commit()
        response = self.depart()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.officer.is_active)
