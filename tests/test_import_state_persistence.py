# -*- coding: utf-8 -*-
"""Tests for persisted import/export runtime state (Phase 2B-P2 Step 1)."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import (
    ExportJobRecord,
    ImportPreviewSession,
    User,
    db,
)
from app.services import import_state
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class ImportStatePersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='import-state-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'state-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.super_admin = User(
            number='S1',
            department='办公部',
            name='Super Admin',
            gender='-',
            grade='-',
            college='Test',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id='s-super',
            password_hash=generate_password_hash('password'),
            role='超级管理员',
            group='一组',
            is_active=True,
        )
        db.session.add(self.super_admin)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.super_admin.id
            sess['user_role'] = self.super_admin.role
            sess['user_name'] = self.super_admin.name

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def test_preview_persists_across_session_removal(self):
        import_id = import_state.save_import_preview([{'a': 1}], ['a'])
        db.session.remove()
        loaded = import_state.load_import_preview(import_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['rows'], [{'a': 1}])
        self.assertEqual(loaded['expected_cols'], ['a'])

    def test_consume_removes_preview(self):
        import_id = import_state.save_import_preview([{'a': 1}], ['a'])
        payload = import_state.consume_import_preview(import_id)
        self.assertIsNotNone(payload)
        self.assertIsNone(import_state.load_import_preview(import_id))

    def test_expired_preview_is_treated_as_missing(self):
        import_id = 'oldpreview'
        db.session.add(ImportPreviewSession(
            id=import_id,
            payload_json='{"rows":[]}',
            created_at=datetime.now() - timedelta(hours=25),
        ))
        db.session.commit()
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.load_import_preview(import_id))

    def test_expired_export_is_treated_as_missing(self):
        job_id = 'oldexport'
        db.session.add(ExportJobRecord(
            id=job_id,
            status='running',
            percent=0,
            message='old',
            created_at=datetime.now() - timedelta(hours=25),
        ))
        db.session.commit()
        self.assertIsNone(import_state.get_export_job(job_id))

    def test_save_and_start_clean_stale_rows(self):
        db.session.add(ImportPreviewSession(
            id='stale-preview',
            payload_json='{}',
            created_at=datetime.now() - timedelta(hours=25),
        ))
        db.session.add(ExportJobRecord(
            id='stale-export',
            status='running',
            percent=0,
            message='old',
            created_at=datetime.now() - timedelta(hours=25),
        ))
        db.session.commit()
        import_state.save_import_preview([], [])
        job_id = import_state.start_export_job()
        self.assertIsNone(db.session.get(ImportPreviewSession, 'stale-preview'))
        self.assertIsNone(db.session.get(ExportJobRecord, 'stale-export'))
        self.assertIsNotNone(import_state.get_export_job(job_id))

    def test_running_shape_has_no_download_url_and_no_internal_fields(self):
        job_id = import_state.start_export_job()
        progress = import_state.get_export_job(job_id)
        self.assertEqual(progress['status'], 'running')
        self.assertEqual(progress['percent'], 0)
        self.assertIn('message', progress)
        self.assertNotIn('download_url', progress)
        self.assertNotIn('id', progress)
        self.assertNotIn('created_at', progress)
        self.assertNotIn('updated_at', progress)

    def test_completed_shape_has_download_url(self):
        job_id = import_state.start_export_job()
        import_state.update_export_job(
            job_id,
            status='completed',
            percent=100,
            message='导出完成',
            download_url='/admin/download_passwords/test.xlsx',
        )
        progress = import_state.get_export_job(job_id)
        self.assertEqual(progress['status'], 'completed')
        self.assertEqual(progress['percent'], 100)
        self.assertEqual(progress['download_url'], '/admin/download_passwords/test.xlsx')
        self.assertNotIn('id', progress)

    def test_export_progress_404_message(self):
        response = self.client.get('/admin/api/export/progress/not-exists')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(response.get_json()['message'], '任务不存在')

    def test_confirm_import_expired_message(self):
        import_id = 'expired-import'
        db.session.add(ImportPreviewSession(
            id=import_id,
            payload_json='{"rows":[]}',
            created_at=datetime.now() - timedelta(hours=25),
        ))
        db.session.commit()
        response = self.client.post(
            '/admin/confirm_import',
            json={'import_id': import_id, 'overwrite': False},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(response.get_json()['message'], '导入会话已失效，请重新预览')


if __name__ == '__main__':
    unittest.main()
