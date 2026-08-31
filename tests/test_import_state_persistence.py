# -*- coding: utf-8 -*-
"""Tests for persisted import/export runtime state (Phase 2B-P2 Step 1)."""
import json
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
            id='expired-import',
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

    # ---- Round 7C-P1-R2: COMMITTED terminal state / publication proof ----

    def test_codec_decodes_all_three_states(self):
        import_id = 'codec-three'
        # legacy/plain → AVAILABLE
        db.session.add(ImportPreviewSession(
            id=import_id,
            payload_json=json.dumps({'rows': [{'a': 1}], 'expected_cols': ['a']}),
        ))
        db.session.commit()
        loaded = import_state.load_import_preview(import_id)
        self.assertEqual(loaded['rows'], [{'a': 1}])

        # claimed v1 → CLAIMED（公共视角不可见，lease 内不可 claim）
        record = db.session.get(ImportPreviewSession, import_id)
        record.payload_json = json.dumps({
            '_import_state_version': 1,
            '_import_state': 'claimed',
            '_claim_token': 'a' * 32,
            '_claimed_at': datetime.now().isoformat(),
            'payload': {'rows': [{'a': 1}], 'expected_cols': ['a']},
        })
        db.session.commit()
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))

        # committed v2 → COMMITTED（公共视角不可见；精确 proof 匹配；无 replay）
        record.payload_json = json.dumps({
            '_import_state_version': 2,
            '_import_state': 'committed',
            '_claim_token': 'a' * 32,
            '_committed_at': datetime.now().isoformat(),
            '_password_artifact': f'passwords_{import_id}_{"a" * 32}.xlsx',
        })
        db.session.commit()
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))
        self.assertTrue(import_state.is_password_artifact_published(
            import_id, 'a' * 32, f'passwords_{import_id}_{"a" * 32}.xlsx',
        ))
        self.assertIsNone(import_state.get_committed_import_result(import_id))

        # committed v3 → COMMITTED（publication proof 有效 + replay metadata 可用）
        record.payload_json = json.dumps({
            '_import_state_version': 3,
            '_import_state': 'committed',
            '_claim_token': 'a' * 32,
            '_committed_at': datetime.now().isoformat(),
            '_password_artifact': f'passwords_{import_id}_{"a" * 32}.xlsx',
            '_result': {
                'imported_count': 1, 'updated_count': 2,
                'skipped_count': 3, 'total_users': 4,
            },
        })
        db.session.commit()
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))
        self.assertTrue(import_state.is_password_artifact_published(
            import_id, 'a' * 32, f'passwords_{import_id}_{"a" * 32}.xlsx',
        ))
        committed = import_state.get_committed_import_result(import_id)
        self.assertIsNotNone(committed)
        self.assertEqual(
            (committed.imported_count, committed.updated_count,
             committed.skipped_count, committed.total_users),
            (1, 2, 3, 4),
        )
        self.assertEqual(committed.claim_token, 'a' * 32)
        self.assertEqual(committed.password_artifact, f'passwords_{import_id}_{"a" * 32}.xlsx')

    def test_get_committed_import_result_contract(self):
        # §九：missing/AVAILABLE/CLAIMED/malformed → None；COMMITTED v2 → None；
        # 只有有效 COMMITTED v3 → typed result（不返回 raw envelope）。
        import_id = 'replay-contract'
        self.assertIsNone(import_state.get_committed_import_result(import_id))
        self.assertIsNone(import_state.get_committed_import_result(None))
        self.assertIsNone(import_state.get_committed_import_result(''))

        db.session.add(ImportPreviewSession(id=import_id, payload_json='{"rows":[]}'))
        db.session.commit()
        self.assertIsNone(import_state.get_committed_import_result(import_id))  # AVAILABLE

        record = db.session.get(ImportPreviewSession, import_id)
        record.payload_json = json.dumps({
            '_import_state_version': 1,
            '_import_state': 'claimed',
            '_claim_token': 'a' * 32,
            '_claimed_at': datetime.now().isoformat(),
            'payload': {'rows': []},
        })
        db.session.commit()
        self.assertIsNone(import_state.get_committed_import_result(import_id))  # CLAIMED

        record.payload_json = '{not-json'
        db.session.commit()
        self.assertIsNone(import_state.get_committed_import_result(import_id))  # malformed

    def test_is_password_artifact_published_requires_exact_committed_proof(self):
        import_id = 'pub-proof'
        filename = f'passwords_{import_id}_{"c" * 32}.xlsx'
        # row missing → deny（absence of blocker ≠ proof of success）
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'c' * 32, filename))

        db.session.add(ImportPreviewSession(id=import_id, payload_json='{"rows":[]}'))
        db.session.commit()
        # AVAILABLE / file in disk state → deny
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'c' * 32, filename))

        record = db.session.get(ImportPreviewSession, import_id)
        record.payload_json = json.dumps({
            '_import_state_version': 2,
            '_import_state': 'committed',
            '_claim_token': 'c' * 32,
            '_committed_at': datetime.now().isoformat(),
            '_password_artifact': filename,
        })
        db.session.commit()
        self.assertTrue(import_state.is_password_artifact_published(import_id, 'c' * 32, filename))
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'd' * 32, filename))
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'c' * 32, 'passwords_other.xlsx'))
        self.assertFalse(import_state.is_password_artifact_published(import_id, None, filename))
        self.assertFalse(import_state.is_password_artifact_published(None, 'c' * 32, filename))
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'c' * 32, None))

    def test_cleanup_preserves_committed_rows(self):
        # §12：COMMITTED 是发布证明，PASSWORD_EXPORT_RETENTION_POLICY 裁决前
        # 不做 TTL 清理；stale AVAILABLE 照常清理，CLAIMED 保持 lease 协议。
        committed_id = 'keep-committed'
        stale_id = 'drop-stale'
        claimed_id = 'keep-claimed'
        db.session.add_all([
            ImportPreviewSession(
                id=committed_id,
                payload_json=json.dumps({
                    '_import_state_version': 2,
                    '_import_state': 'committed',
                    '_claim_token': 'e' * 32,
                    '_committed_at': datetime.now().isoformat(),
                    '_password_artifact': None,
                }),
                created_at=datetime.now() - timedelta(hours=48),
            ),
            ImportPreviewSession(
                id=stale_id,
                payload_json='{"rows":[]}',
                created_at=datetime.now() - timedelta(hours=48),
            ),
            ImportPreviewSession(
                id=claimed_id,
                payload_json=json.dumps({
                    '_import_state_version': 1,
                    '_import_state': 'claimed',
                    '_claim_token': 'f' * 32,
                    '_claimed_at': (datetime.now() - timedelta(hours=48)).isoformat(),
                    'payload': {'rows': []},
                }),
                created_at=datetime.now() - timedelta(hours=48),
            ),
        ])
        db.session.commit()

        import_state.save_import_preview([], [])  # 触发 _cleanup_stale

        self.assertIsNotNone(db.session.get(ImportPreviewSession, committed_id))
        self.assertIsNone(db.session.get(ImportPreviewSession, stale_id))
        self.assertIsNotNone(db.session.get(ImportPreviewSession, claimed_id))

    def test_finalize_rolls_back_with_caller_transaction(self):
        # §10：CAS CLAIMED → COMMITTED（含 replay result metadata）发生在调用方
        # session，回滚后必须回到 CLAIMED —— publication proof、replay result
        # 与业务变更同 commit 同 rollback。
        import_id = import_state.save_import_preview([{'a': 1}], ['a'])
        claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim)

        import_state.finalize_import_preview_claim(
            claim, db.session,
            password_artifact='passwords_artifact.xlsx',
            result={
                'imported_count': 1, 'updated_count': 0,
                'skipped_count': 0, 'total_users': 1,
            },
        )
        db.session.rollback()

        # 回滚后 row 仍是原始 claimed envelope，可重新 finalize。
        raw = db.session.get(ImportPreviewSession, import_id).payload_json
        self.assertIn('"_import_state": "claimed"', raw)
        self.assertIsNone(import_state.get_committed_import_result(import_id))
        import_state.finalize_import_preview_claim(
            claim, db.session,
            password_artifact='passwords_artifact.xlsx',
            result={
                'imported_count': 1, 'updated_count': 0,
                'skipped_count': 0, 'total_users': 1,
            },
        )
        db.session.commit()
        self.assertTrue(import_state.is_password_artifact_published(import_id, claim.claim_token, 'passwords_artifact.xlsx'))
        committed = import_state.get_committed_import_result(import_id)
        self.assertIsNotNone(committed)
        self.assertEqual(
            (committed.imported_count, committed.updated_count,
             committed.skipped_count, committed.total_users),
            (1, 0, 0, 1),
        )


if __name__ == '__main__':
    unittest.main()
