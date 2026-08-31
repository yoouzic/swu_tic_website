# -*- coding: utf-8 -*-
"""Round 7C-P1 contacts-import transaction regressions.

Covers:
- at-most-once preview claim (``claim_import_preview``): cross-session /
  cross-worker ownership decided by a CAS UPDATE, not process locks;
- deterministic pre-fix repro of the old load→business→consume double-winner
  gap (barrier-forced interleaving);
- confirm_import transaction boundary: password workbook built BEFORE the
  single business commit; export/commit failures roll back, remove the
  artifact and restore the preview for retry;
- DB hash verifies the exported plaintext password end-to-end.

Round 7C-P1-R1 (durable claim): crashed claims are recoverable after the
lease expires; stale workers can neither release nor finalize.

Round 7C-P1-R2 (claim-token artifact / COMMITTED publication proof):
- artifact filename embeds the claim token → stale workers are physically
  isolated from the winner's file (cannot overwrite or delete it);
- finalize is a CAS CLAIMED → COMMITTED terminal state recorded in the same
  business transaction (no more destructive DELETE of the claimed row);
- new-style token artifacts are served only with a positive COMMITTED marker
  matching (import_id, claim_token, filename) — row absence never publishes
  (TTL cleanup / maintenance orphans stay fail-closed);
- the COMMITTED envelope holds no business payload (rows/expected_cols).
"""
import glob
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd
from werkzeug.security import check_password_hash, generate_password_hash

from app.app import app
from app.blueprints.admin.contacts import _password_artifact_filename
from app.models import (
    Department,
    Group,
    ImportPreviewSession,
    PasswordAuditLog,
    User,
    db,
)
from app.services import import_state
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

EXPECTED_COLS = ['number', 'department', 'name', 'gender', 'grade', 'college',
                 'major', 'dormitory', 'phone', 'qq', 'student_id', 'role', 'group']
OLD_PASSWORD = 'oldpass123'
EXPIRED_MESSAGE = '导入会话已失效，请重新预览'


def _row(number, student_id, **overrides):
    row = {
        'number': number,
        'department': '办公部',
        'name': f'User {number}',
        'gender': '男',
        'grade': '2024',
        'college': 'Test College',
        'major': 'M',
        'dormitory': 'D',
        'phone': '13800000000',
        'qq': '12345',
        'student_id': student_id,
        'role': '信息员',
        'group': '一组',
        'has_error': False,
    }
    row.update(overrides)
    return row


class ContactsImportTransactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='import-tx-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'import-tx-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()

        self.export_dir = os.path.join(self.temp_dir.name, f'exports-{id(self)}')
        self._previous_export_dir = os.environ.get('EXPORT_DIR')
        os.environ['EXPORT_DIR'] = self.export_dir

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
        if self._previous_export_dir is None:
            os.environ.pop('EXPORT_DIR', None)
        else:
            os.environ['EXPORT_DIR'] = self._previous_export_dir

    # ---- helpers ----

    def _make_existing_user(self, number='80001', student_id='existing-1'):
        user = User(
            number=number,
            department='办公部',
            name=f'Existing {number}',
            gender='男',
            grade='2023',
            college='Test College',
            major='M',
            dormitory='D',
            phone='13800000001',
            qq='12345',
            student_id=student_id,
            password_hash=generate_password_hash(OLD_PASSWORD),
            role='信息员',
            group='一组',
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _save_preview(self, rows):
        return import_state.save_import_preview(rows, EXPECTED_COLS)

    def _confirm(self, import_id, overwrite=True):
        return self.client.post(
            '/admin/confirm_import',
            json={'import_id': import_id, 'overwrite': overwrite},
        )

    def _user_by_student(self, student_id):
        return db.session.query(User).filter_by(student_id=student_id).first()

    def _audit_count(self, user):
        return PasswordAuditLog.query.filter_by(target_user_id=user.id).count()

    def _export_path(self, filename):
        return os.path.join(self.export_dir, filename)

    def _artifact_filenames(self, import_id):
        """All claim-token artifacts on disk for this import (any claim)."""
        pattern = os.path.join(glob.escape(self.export_dir), f'passwords_{import_id}_*.xlsx')
        return sorted(os.path.basename(p) for p in glob.glob(pattern))

    def _raw_state(self, import_id):
        """Raw payload_json of the preview row (None if row missing)."""
        record = db.session.get(ImportPreviewSession, import_id)
        return None if record is None else record.payload_json

    def _state_token(self, import_id):
        """Claim token embedded in the row's current envelope."""
        raw = self._raw_state(import_id)
        if raw is None:
            return None
        decoded = json.loads(raw)
        return decoded.get('_claim_token')

    def _filename_from_url(self, url):
        return url.rsplit('/', 1)[-1]

    # ---- service-level atomic claim (§五/§七/§八) ----

    def test_a_claim_returns_payload_once_then_missing(self):
        import_id = self._save_preview([_row('90001', 'claim-1')])
        claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim)
        self.assertEqual(claim.payload['rows'][0]['student_id'], 'claim-1')
        self.assertIsNotNone(claim.created_at)

        loser = import_state.claim_import_preview(import_id)
        self.assertIsNone(loser)
        self.assertIsNone(import_state.load_import_preview(import_id))

    def test_b_concurrent_claims_have_exactly_one_winner(self):
        import_id = self._save_preview([_row('90002', 'claim-race')])
        barrier = threading.Barrier(2, timeout=10)

        def claim(_):
            with app.app_context():
                barrier.wait()
                return import_state.claim_import_preview(import_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))

        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].payload['rows'][0]['student_id'], 'claim-race')
        self.assertIsNone(import_state.load_import_preview(import_id))

    def test_c_expired_preview_cannot_claim_and_is_cleaned(self):
        import_id = 'expired-claim'
        db.session.add(import_state.ImportPreviewSession(
            id=import_id,
            payload_json='{"rows":[]}',
            created_at=datetime.now() - timedelta(hours=25),
        ))
        db.session.commit()

        self.assertIsNone(import_state.claim_import_preview(import_id))
        self.assertIsNone(db.session.get(import_state.ImportPreviewSession, import_id))

    def test_d_consume_import_preview_alias_keeps_compat_contract(self):
        import_id = self._save_preview([_row('90003', 'consume-1')])
        payload = import_state.consume_import_preview(import_id)
        self.assertIsNotNone(payload)
        self.assertIsNone(import_state.consume_import_preview(import_id))
        self.assertIsNone(import_state.load_import_preview(import_id))

    # ---- release primitive（7C-P1-R1 durable claim 语义）----

    def test_release_returns_claimed_preview_for_retry(self):
        import_id = self._save_preview([_row('90004', 'restore-1')])
        claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim)

        released = import_state.release_import_preview_claim(claim)
        self.assertTrue(released)

        retry_claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(retry_claim)
        self.assertEqual(retry_claim.payload['rows'][0]['student_id'], 'restore-1')
        self.assertEqual(retry_claim.created_at, claim.created_at)
        self.assertNotEqual(retry_claim.claim_token, claim.claim_token)

    def test_restore_wrapper_never_touches_existing_row(self):
        import_id = self._save_preview([_row('90005', 'keep-1')])
        restored = import_state.restore_import_preview(
            import_id, {'rows': [_row('99999', 'intruder')]}, datetime.now(),
        )
        # 行仍存在（AVAILABLE）时无 token 的 restore 无法证明所有权 → False。
        self.assertFalse(restored)
        payload = import_state.load_import_preview(import_id)
        self.assertEqual(payload['rows'][0]['student_id'], 'keep-1')

        # legacy "row missing" 场景保留：可插回 AVAILABLE 行。
        import_state.claim_import_preview(import_id)
        # claim 后行仍在（durable claim），restore 仍拒绝：
        self.assertFalse(import_state.restore_import_preview(
            import_id, {'rows': []}, datetime.now(),
        ))

    # ---- deterministic pre-fix repro: load→business→consume gap (§四) ----

    def test_concurrent_consume_via_load_gate_has_exactly_one_winner(self):
        # PRE_FIX_REPRO：旧实现 load 与 delete 分离，两个并发 confirm 都能通过
        # load 门并各自成功 consume（双 winner）。修复后 claim 的 peek/delete
        # 同属一个原子 primitive，不再调用 load_import_preview，屏障自然失效。
        real_load = import_state.load_import_preview
        import_id = self._save_preview([_row('90006', 'race-consume')])
        barrier = threading.Barrier(2, timeout=10)

        def gated_load(target_import_id):
            payload = real_load(target_import_id)
            barrier.wait()
            return payload

        def consume(_):
            with app.app_context():
                return import_state.consume_import_preview(import_id)

        with mock.patch.object(import_state, 'load_import_preview', side_effect=gated_load):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(consume, range(2)))

        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]['rows'][0]['student_id'], 'race-consume')
        self.assertIsNone(import_state.load_import_preview(import_id))

    # ---- endpoint at-most-once + idempotent replay (§11/§13/§14) ----

    def test_r3_second_confirm_replays_committed_result(self):
        # EXPECTED_CONTRACT_CHANGE（R3）:
        #   COMMITTED_CONFIRM_400 → COMMITTED_CONFIRM_SUCCESS_REPLAY
        # 首次成功后，同 import_id 的再次 confirm 幂等重放同一成功结果；
        # side effect 仍严格 at-most-once（COMMITTED terminal，不重执行）。
        import_id = self._save_preview([_row('90007', 'confirm-e1')])

        first = self._confirm(import_id, overwrite=False)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json()['success'])
        self.assertIsNone(import_state.load_import_preview(import_id))

        second = self._confirm(import_id, overwrite=False)
        self.assertEqual(second.status_code, 200)
        replay = second.get_json()
        self.assertTrue(replay['success'])
        # 与首次成功完全一致（同一 builder 渲染，无字段漂移）：
        self.assertEqual(replay['message'], first.get_json()['message'])
        self.assertEqual(replay['imported_count'], first.get_json()['imported_count'])
        self.assertEqual(replay['updated_count'], first.get_json()['updated_count'])
        self.assertEqual(replay['skipped_count'], first.get_json()['skipped_count'])
        self.assertEqual(replay['total_users'], first.get_json()['total_users'])
        self.assertEqual(replay['password_file_url'], first.get_json()['password_file_url'])

    def test_r3_replay_is_side_effect_free(self):
        # §15：COMMITTED_RESULT_REPLAY_SIDE_EFFECT_FREE——重放不重置密码、不增
        # 审计、不改用户/部门/小组计数、不生成第二个工件、工件内容不变。
        user = self._make_existing_user(number='80010', student_id='confirm-f1')
        import_id = self._save_preview([_row('80010', 'confirm-f1')])

        first = self._confirm(import_id, overwrite=True)
        self.assertEqual(first.status_code, 200)
        audits_after_first = self._audit_count(user)
        self.assertGreaterEqual(audits_after_first, 1)

        db.session.expire_all()
        hash_after_first = db.session.get(User, user.id).password_hash

        artifact_name = self._filename_from_url(first.get_json()['password_file_url'])
        artifact_bytes_before = self._read_artifact(artifact_name)
        users_before = User.query.count()
        departments_before = Department.query.count()
        groups_before = Group.query.count()

        second = self._confirm(import_id, overwrite=True)
        self.assertEqual(second.status_code, 200)
        replay = second.get_json()
        self.assertTrue(replay['success'])
        self.assertEqual(replay['imported_count'], first.get_json()['imported_count'])
        self.assertEqual(replay['updated_count'], first.get_json()['updated_count'])
        self.assertEqual(replay['skipped_count'], first.get_json()['skipped_count'])
        self.assertEqual(replay['total_users'], first.get_json()['total_users'])
        self.assertEqual(replay['password_file_url'], first.get_json()['password_file_url'])

        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertEqual(user_after.password_hash, hash_after_first)
        self.assertEqual(self._audit_count(user_after), audits_after_first)
        self.assertEqual(User.query.count(), users_before)
        self.assertEqual(Department.query.count(), departments_before)
        self.assertEqual(Group.query.count(), groups_before)
        self.assertEqual(self._artifact_filenames(import_id), [artifact_name])
        self.assertEqual(self._read_artifact(artifact_name), artifact_bytes_before)

    # ---- Bug B: post-commit export failure / transaction boundary (§十二–§十六) ----

    def test_g_password_export_failure_leaves_db_unchanged_and_preview_retryable(self):
        user = self._make_existing_user(number='80020', student_id='export-h1')
        import_id = self._save_preview([_row('80020', 'export-h1')])

        with mock.patch('pandas.DataFrame.to_excel', side_effect=OSError('disk full')):
            response = self._confirm(import_id, overwrite=True)

        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json()['success'])

        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertTrue(check_password_hash(user_after.password_hash, OLD_PASSWORD))
        self.assertEqual(self._audit_count(user_after), 0)

        retry_claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(retry_claim)
        self.assertEqual(retry_claim.payload['rows'][0]['student_id'], 'export-h1')

    def test_h_commit_failure_removes_password_artifact_and_restores_preview(self):
        user = self._make_existing_user(number='80021', student_id='commit-k1')
        import_id = self._save_preview([_row('80021', 'commit-k1')])

        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('commit fault')):
            response = self._confirm(import_id, overwrite=True)

        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json()['success'])

        # 失败路径的 ownership-local cleanup 必须删掉当前 claim token 的工件。
        self.assertEqual(self._artifact_filenames(import_id), [])

        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertTrue(check_password_hash(user_after.password_hash, OLD_PASSWORD))
        self.assertEqual(self._audit_count(user_after), 0)

        retry_claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(retry_claim)
        self.assertEqual(retry_claim.payload['rows'][0]['student_id'], 'commit-k1')

        # §19：commit 失败绝不产生 replay result——重试必须走正常重执行。
        self.assertIsNone(import_state.get_committed_import_result(import_id))

    # ---- password artifact naming（R2：claim token 物理隔离）----

    def test_i_two_imports_get_distinct_password_files(self):
        import_id_a = self._save_preview([_row('90030', 'file-a')])
        import_id_b = self._save_preview([_row('90031', 'file-b')])

        first = self._confirm(import_id_a, overwrite=False)
        second = self._confirm(import_id_b, overwrite=False)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        url_a = first.get_json()['password_file_url']
        url_b = second.get_json()['password_file_url']
        self.assertIsNotNone(url_a)
        self.assertIsNotNone(url_b)
        self.assertNotEqual(url_a, url_b)

        # 新式命名：passwords_<16hex import_id>_<32hex claim_token>.xlsx，且
        # 文件名中的 claim token 与 COMMITTED marker 记录一致。
        name_a = self._filename_from_url(url_a)
        name_b = self._filename_from_url(url_b)
        self.assertRegex(name_a, r'^passwords_([0-9a-f]{16})_([0-9a-f]{32})\.xlsx$')
        self.assertRegex(name_b, r'^passwords_([0-9a-f]{16})_([0-9a-f]{32})\.xlsx$')
        self.assertTrue(name_a.startswith(f'passwords_{import_id_a}_'))
        self.assertTrue(name_b.startswith(f'passwords_{import_id_b}_'))
        self.assertTrue(os.path.exists(self._export_path(name_a)))
        self.assertTrue(os.path.exists(self._export_path(name_b)))

    # ---- §二十九 end-to-end password consistency（R2：token filename）----

    def test_j_db_hash_verifies_exported_password_after_success(self):
        user = self._make_existing_user(number='80040', student_id='consistent-1')
        import_id = self._save_preview([_row('80040', 'consistent-1')])

        response = self._confirm(import_id, overwrite=True)
        self.assertEqual(response.status_code, 200)

        artifact_name = self._filename_from_url(response.get_json()['password_file_url'])
        artifact_path = self._export_path(artifact_name)
        self.assertTrue(os.path.exists(artifact_path))

        # 成功提交后，COMMITTED marker 已发布该工件：下载 200（§18）。
        published = self._download(artifact_name)
        try:
            self.assertEqual(published.status_code, 200)
            self.assertTrue(published.data)
        finally:
            published.close()

        exported = pd.read_excel(artifact_path)
        row = exported.loc[exported['student_id'] == 'consistent-1'].iloc[0]
        exported_password = str(row['password'])

        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertTrue(check_password_hash(user_after.password_hash, exported_password))
        self.assertFalse(check_password_hash(user_after.password_hash, OLD_PASSWORD))
        self.assertIsNone(import_state.load_import_preview(import_id))

    def test_k_export_failure_keeps_old_hash_not_exported_password(self):
        user = self._make_existing_user(number='80041', student_id='inconsistent-1')
        import_id = self._save_preview([_row('80041', 'inconsistent-1')])

        with mock.patch('pandas.DataFrame.to_excel', side_effect=OSError('disk full')):
            response = self._confirm(import_id, overwrite=True)
        self.assertEqual(response.status_code, 500)

        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertTrue(check_password_hash(user_after.password_hash, OLD_PASSWORD))

        self.assertEqual(self._artifact_filenames(import_id), [])

    # ---- Round 7C-P1-R1: durable claim / crash recovery / publish gate ----

    def test_claimed_preview_row_survives_worker_crash(self):
        # PRE_FIX_REPRO（CLAIM_DELETE_CRASH_WINDOW）：P1 的破坏性 claim 在
        # "claim 后 worker 消失（不 release/finalize）" 场景下丢行，无法 reclaim。
        import_id = self._save_preview([_row('90050', 'crash-window')])
        claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim)

        # 模拟进程消失：不 release、不 finalize，检查持久状态。
        self.assertIsNotNone(db.session.get(import_state.ImportPreviewSession, import_id))
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))

    def test_crashed_claim_recoverable_after_lease_expiry(self):
        import_id = self._save_preview([_row('90060', 'crash-1')])
        claim1 = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim1)

        # crash：行仍在 CLAIMED；lease 内不可重取。
        self.assertIsNotNone(db.session.get(import_state.ImportPreviewSession, import_id))
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))

        after_lease = datetime.now() + import_state.IMPORT_CLAIM_LEASE + timedelta(minutes=1)
        with mock.patch('app.services.import_state._now', return_value=after_lease):
            claim2 = import_state.claim_import_preview(import_id)

        self.assertIsNotNone(claim2)
        self.assertEqual(claim2.payload['rows'][0]['student_id'], 'crash-1')
        self.assertNotEqual(claim2.claim_token, claim1.claim_token)
        self.assertEqual(claim2.created_at, claim1.created_at)

    def test_stale_worker_release_blocked_after_recovery(self):
        import_id = self._save_preview([_row('90061', 'stale-release')])
        claim_a = import_state.claim_import_preview(import_id)

        after_lease = datetime.now() + import_state.IMPORT_CLAIM_LEASE + timedelta(minutes=1)
        with mock.patch('app.services.import_state._now', return_value=after_lease):
            claim_b = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim_b)

        # 旧 worker 的 release 不得覆盖新 owner 的 claim。
        self.assertFalse(import_state.release_import_preview_claim(claim_a))
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))

    def test_stale_worker_finalize_blocked_after_recovery(self):
        import_id = self._save_preview([_row('90062', 'stale-finalize')])
        claim_a = import_state.claim_import_preview(import_id)

        after_lease = datetime.now() + import_state.IMPORT_CLAIM_LEASE + timedelta(minutes=1)
        with mock.patch('app.services.import_state._now', return_value=after_lease):
            claim_b = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim_b)

        with self.assertRaises(import_state.ImportPreviewOwnershipLostError):
            import_state.finalize_import_preview_claim(claim_a, db.session)
        db.session.rollback()

        # B 的 claim 状态保持完整，行未被 A 删除。
        self.assertIsNotNone(db.session.get(import_state.ImportPreviewSession, import_id))
        self.assertIsNone(import_state.load_import_preview(import_id))

    def test_payload_codec_states_fail_closed(self):
        import_id = self._save_preview([_row('90070', 'codec-1')])

        # legacy plain payload → AVAILABLE，且业务 payload 不泄漏 claim 内部键。
        payload = import_state.load_import_preview(import_id)
        self.assertIsNotNone(payload)
        self.assertNotIn('_import_state', payload)
        self.assertNotIn('_claim_token', payload)

        # claimed envelope → load None（公共视角不可见）、lease 内 claim None。
        record = db.session.get(import_state.ImportPreviewSession, import_id)
        record.payload_json = json.dumps({
            '_import_state_version': 1,
            '_import_state': 'claimed',
            '_claim_token': 'a' * 32,
            '_claimed_at': datetime.now().isoformat(),
            'payload': {'rows': [], 'expected_cols': []},
        }, ensure_ascii=False)
        db.session.commit()
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))

        # malformed → fail closed。
        record.payload_json = '{not-json'
        db.session.commit()
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))

    def test_cleanup_spares_claimed_rows_and_cleans_stale_available(self):
        stale_id = self._save_preview([_row('90080', 'stale-avail')])
        claimed_id = self._save_preview([_row('90081', 'claimed-keep')])
        self.assertIsNotNone(import_state.claim_import_preview(claimed_id))

        old = datetime.now() - timedelta(hours=25)
        for target_id in (stale_id, claimed_id):
            record = db.session.get(import_state.ImportPreviewSession, target_id)
            record.created_at = old
        db.session.commit()

        import_state.save_import_preview([], [])  # 触发 _cleanup_stale

        self.assertIsNone(db.session.get(import_state.ImportPreviewSession, stale_id))
        self.assertIsNotNone(db.session.get(import_state.ImportPreviewSession, claimed_id))

    # ---- publish gate（§19–§22）----

    def _download(self, filename):
        return self.client.get(f'/admin/download_passwords/{filename}')

    def test_password_artifact_download_blocked_until_commit_published(self):
        # PRE_FIX_REPRO（PASSWORD_ARTIFACT_PRECOMMIT_VISIBLE）：P1 在 commit 前
        # 已删除 preview row，工件在事务窗口内即可下载。R1 后 commit 即发布边界；
        # R2 起发布 proof 是 durable COMMITTED marker（token filename 精确匹配）。
        user = self._make_existing_user(number='80050', student_id='gate-1')
        import_id = self._save_preview([_row('80050', 'gate-1')])

        commit_gate = threading.Event()
        release_gate = threading.Event()
        real_commit = db.session.commit

        def gated_commit():
            commit_gate.set()
            if not release_gate.wait(timeout=15):
                raise RuntimeError('commit gate timeout')
            return real_commit()

        result = {}

        def worker():
            with app.app_context():
                result['response'] = self._confirm(import_id, overwrite=True)

        with mock.patch.object(db.session, 'commit', side_effect=gated_commit):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(commit_gate.wait(timeout=15))
                # 事务窗口内：claim envelope 已带 token，工件已在磁盘，但
                # finalize 未 commit → 无 COMMITTED marker → 拒绝。
                artifact_name = _password_artifact_filename(
                    mock.Mock(import_id=import_id, claim_token=self._state_token(import_id)),
                )
                self.assertTrue(os.path.exists(self._export_path(artifact_name)))
                blocked = self._download(artifact_name)
                try:
                    self.assertEqual(blocked.status_code, 302)
                finally:
                    blocked.close()
                # §18：active CLAIMED 不得被误判成 committed success。
                concurrent = self._confirm(import_id, overwrite=True)
                self.assertEqual(concurrent.status_code, 400)
                self.assertFalse(concurrent.get_json()['success'])
                self.assertEqual(concurrent.get_json()['message'], EXPIRED_MESSAGE)
            finally:
                release_gate.set()
            thread.join(timeout=30)

        self.assertEqual(result['response'].status_code, 200)

        # commit 后：同一文件名，COMMITTED marker 发布 → 200。
        published = self._download(artifact_name)
        try:
            self.assertEqual(published.status_code, 200)
            self.assertTrue(published.data)
        finally:
            published.close()

        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertFalse(check_password_hash(user_after.password_hash, OLD_PASSWORD))

    def test_commit_failure_in_precommit_window_keeps_old_password(self):
        user = self._make_existing_user(number='80051', student_id='gate-fail')
        import_id = self._save_preview([_row('80051', 'gate-fail')])

        commit_gate = threading.Event()
        release_gate = threading.Event()
        real_commit = db.session.commit

        def gated_commit():
            commit_gate.set()
            if not release_gate.wait(timeout=15):
                raise RuntimeError('commit gate timeout')
            raise RuntimeError('commit fault')

        result = {}

        def worker():
            with app.app_context():
                result['response'] = self._confirm(import_id, overwrite=True)

        with mock.patch.object(db.session, 'commit', side_effect=gated_commit):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(commit_gate.wait(timeout=15))
                artifact_name = _password_artifact_filename(
                    mock.Mock(import_id=import_id, claim_token=self._state_token(import_id)),
                )
                blocked = self._download(artifact_name)
                try:
                    self.assertEqual(blocked.status_code, 302)
                finally:
                    blocked.close()
            finally:
                release_gate.set()
            thread.join(timeout=30)

        self.assertEqual(result['response'].status_code, 500)
        self.assertEqual(self._artifact_filenames(import_id), [])

        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertTrue(check_password_hash(user_after.password_hash, OLD_PASSWORD))

        # 失败 → claim release → AVAILABLE，可重试。
        retry_claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(retry_claim)

    def test_l_cleanup_failure_keeps_artifact_blocked_by_available_row(self):
        # §20 复现基础：业务失败 + 工件删除失败 → 文件留在磁盘，但 token 工件
        # 需要 positive COMMITTED proof 才能发布：AVAILABLE 状态（无论 row 是否
        # 存在）一律拒绝下载。注意：不能 patch 全局 os.remove —— openpyxl 保存
        # 过程内部也会调用它（误伤 to_excel），这里以 cleanup 函数 no-op 模拟。
        self._make_existing_user(number='80052', student_id='gate-cleanup')
        import_id = self._save_preview([_row('80052', 'gate-cleanup')])

        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('commit fault')), \
             mock.patch('app.blueprints.admin.contacts._cleanup_generated_password_file'):
            response = self._confirm(import_id, overwrite=True)
        self.assertEqual(response.status_code, 500)

        artifacts = self._artifact_filenames(import_id)
        self.assertEqual(len(artifacts), 1)
        self.assertTrue(os.path.exists(self._export_path(artifacts[0])))
        blocked = self._download(artifacts[0])
        try:
            self.assertEqual(blocked.status_code, 302)
        finally:
            blocked.close()

        # AVAILABLE preview 可重试（管理员可走完导入后正常发布）。
        retry_claim = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(retry_claim)

    def test_publish_gate_spares_contacts_and_legacy_files(self):
        os.makedirs(self.export_dir, exist_ok=True)
        legacy_names = (
            'contacts_办公部_20260822_120000.xlsx',
            'passwords_20260822_120000.xlsx',          # legacy 时间戳命名
            'passwords_ffffffffffffffff.xlsx',          # 16-hex 但 preview row 不存在 = 已发布/外部文件
        )
        for name in legacy_names:
            with open(self._export_path(name), 'wb') as fh:
                fh.write(b'workbook-bytes')

        for name in legacy_names:
            response = self._download(name)
            try:
                self.assertEqual(response.status_code, 200, msg=name)
                self.assertEqual(response.data, b'workbook-bytes', msg=name)
            finally:
                response.close()

    # ---- Round 7C-P1-R2: claim-token artifact / COMMITTED publication proof ----

    def _claimed_json(self, token, claimed_at=None):
        return json.dumps({
            '_import_state_version': 1,
            '_import_state': 'claimed',
            '_claim_token': token,
            '_claimed_at': (claimed_at or datetime.now()).isoformat(),
            'payload': {'rows': [], 'expected_cols': []},
        }, ensure_ascii=False)

    def _committed_json(self, token, artifact):
        return json.dumps({
            '_import_state_version': 2,
            '_import_state': 'committed',
            '_claim_token': token,
            '_committed_at': datetime.now().isoformat(),
            '_password_artifact': artifact,
        }, ensure_ascii=False)

    def _set_state(self, import_id, raw):
        record = db.session.get(ImportPreviewSession, import_id)
        record.payload_json = raw
        db.session.commit()

    def _write_artifact(self, filename, content):
        os.makedirs(self.export_dir, exist_ok=True)
        with open(self._export_path(filename), 'wb') as fh:
            fh.write(content)

    def _read_artifact(self, filename):
        with open(self._export_path(filename), 'rb') as fh:
            return fh.read()

    def _close(self, response):
        try:
            return response.status_code
        finally:
            response.close()

    def test_r2_claim_token_artifact_paths_physically_isolated(self):
        # §13/§15：claim A / claim B 的工件路径必须不同；旧 A 写自己的文件绝不
        # 改写 winner B 的内容（内容级断言，不只是字符串不等）。
        import_id = self._save_preview([_row('90100', 'iso-1')])
        claim_a = import_state.claim_import_preview(import_id)
        after_lease = datetime.now() + import_state.IMPORT_CLAIM_LEASE + timedelta(minutes=1)
        with mock.patch('app.services.import_state._now', return_value=after_lease):
            claim_b = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim_a)
        self.assertIsNotNone(claim_b)

        name_a = _password_artifact_filename(claim_a)
        name_b = _password_artifact_filename(claim_b)
        self.assertNotEqual(name_a, name_b)
        self.assertTrue(name_a.startswith(f'passwords_{import_id}_'))
        self.assertTrue(name_b.startswith(f'passwords_{import_id}_'))

        winner_bytes = b'winner-b-workbook-bytes'
        self._write_artifact(name_b, winner_bytes)
        self._write_artifact(name_a, b'stale-a-workbook-bytes')

        self.assertEqual(self._read_artifact(name_b), winner_bytes)
        self.assertEqual(
            sorted(self._artifact_filenames(import_id)), sorted([name_a, name_b]),
        )

    def test_r2_stale_worker_cleanup_cannot_delete_winner_artifact(self):
        # §14：stale worker A 的失败路径 cleanup 只能删 A 自己的 token 工件。
        import_id = self._save_preview([_row('90110', 'cleanup-iso')])
        claim_a = import_state.claim_import_preview(import_id)
        after_lease = datetime.now() + import_state.IMPORT_CLAIM_LEASE + timedelta(minutes=1)
        with mock.patch('app.services.import_state._now', return_value=after_lease):
            claim_b = import_state.claim_import_preview(import_id)

        name_a = _password_artifact_filename(claim_a)
        name_b = _password_artifact_filename(claim_b)
        winner_bytes = b'winner-b-artifact'
        self._write_artifact(name_b, winner_bytes)
        self._write_artifact(name_a, b'stale-a-artifact')

        from app.blueprints.admin.contacts import _cleanup_generated_password_file
        _cleanup_generated_password_file(self._export_path(name_a))

        self.assertFalse(os.path.exists(self._export_path(name_a)))
        self.assertTrue(os.path.exists(self._export_path(name_b)))
        self.assertEqual(self._read_artifact(name_b), winner_bytes)

    def test_r2_download_gate_fail_closed_matrix(self):
        # §17：新式 token 工件只在 “COMMITTED + token 匹配 + 文件名精确匹配”
        # 时可下载；其余一律拒绝（包括 row missing）。
        import_id = self._save_preview([_row('90120', 'matrix-1')])
        token_a = 'a' * 32
        token_b = 'b' * 32
        name_a = f'passwords_{import_id}_{token_a}.xlsx'
        name_b = f'passwords_{import_id}_{token_b}.xlsx'
        self._write_artifact(name_a, b'artifact-a')
        self._write_artifact(name_b, b'artifact-b')

        # AVAILABLE + file → deny
        self.assertEqual(self._close(self._download(name_a)), 302)
        self.assertEqual(self._close(self._download(name_b)), 302)

        # CLAIMED A + file A → deny
        self._set_state(import_id, self._claimed_json(token_a))
        self.assertEqual(self._close(self._download(name_a)), 302)

        # CLAIMED B + file A → deny
        self._set_state(import_id, self._claimed_json(token_b))
        self.assertEqual(self._close(self._download(name_a)), 302)
        # CLAIMED B + file B → deny（未 finalize）
        self.assertEqual(self._close(self._download(name_b)), 302)

        # COMMITTED B + file A → deny；COMMITTED B + file B → allow
        self._set_state(import_id, self._committed_json(token_b, name_b))
        self.assertEqual(self._close(self._download(name_a)), 302)
        published = self._download(name_b)
        try:
            self.assertEqual(published.status_code, 200)
            self.assertEqual(published.data, b'artifact-b')
        finally:
            published.close()

        # malformed state + file → deny
        self._set_state(import_id, '{not-json')
        self.assertEqual(self._close(self._download(name_b)), 302)

        # row missing + file B → deny（R2 核心语义：absence ≠ proof of success）
        record = db.session.get(ImportPreviewSession, import_id)
        db.session.delete(record)
        db.session.commit()
        self.assertEqual(self._close(self._download(name_b)), 302)

    def test_r2_lease_recovery_winner_publishes_and_stale_worker_cannot_harm(self):
        # §19：本最重要 end-to-end invariant。A claim → A file；lease 恢复后
        # B 完整成功并发布；A 的 finalize / cleanup 都不能伤害 B 的发布。
        user = self._make_existing_user(number='80060', student_id='lease-e2e')
        import_id = self._save_preview([_row('80060', 'lease-e2e')])

        claim_a = import_state.claim_import_preview(import_id)
        self.assertIsNotNone(claim_a)
        name_a = _password_artifact_filename(claim_a)
        self._write_artifact(name_a, b'stale-a-artifact')

        after_lease = datetime.now() + import_state.IMPORT_CLAIM_LEASE + timedelta(minutes=1)
        with mock.patch('app.services.import_state._now', return_value=after_lease):
            response = self._confirm(import_id, overwrite=True)
        self.assertEqual(response.status_code, 200)
        name_b = self._filename_from_url(response.get_json()['password_file_url'])

        # winner 发布：B 可下载；A 不可下载。
        self.assertEqual(self._close(self._download(name_b)), 200)
        self.assertEqual(self._close(self._download(name_a)), 302)

        # 旧 A 恢复：finalize → ownership lost；cleanup 只删 A 的文件。
        with self.assertRaises(import_state.ImportPreviewOwnershipLostError):
            import_state.finalize_import_preview_claim(claim_a, db.session)
        db.session.rollback()

        from app.blueprints.admin.contacts import _cleanup_generated_password_file
        _cleanup_generated_password_file(self._export_path(name_a))

        self.assertFalse(os.path.exists(self._export_path(name_a)))
        self.assertTrue(os.path.exists(self._export_path(name_b)))
        self.assertEqual(self._close(self._download(name_b)), 200)

        exported = pd.read_excel(self._export_path(name_b))
        row = exported.loc[exported['student_id'] == 'lease-e2e'].iloc[0]
        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertTrue(check_password_hash(user_after.password_hash, str(row['password'])))

    def test_r2_ttl_orphan_artifact_remains_unpublished(self):
        # §20：commit 失败 + cleanup 失败 → release → AVAILABLE；TTL 清理删除
        # AVAILABLE row 后，孤儿文件即使仍在磁盘也必须保持不可发布。
        self._make_existing_user(number='80062', student_id='orphan-1')
        import_id = self._save_preview([_row('80062', 'orphan-1')])

        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('commit fault')), \
             mock.patch('app.blueprints.admin.contacts._cleanup_generated_password_file'):
            response = self._confirm(import_id, overwrite=True)
        self.assertEqual(response.status_code, 500)

        artifacts = self._artifact_filenames(import_id)
        self.assertEqual(len(artifacts), 1)
        orphan = artifacts[0]
        self.assertEqual(self._close(self._download(orphan)), 302)

        # TTL 到期：AVAILABLE row 被 cleanup 删除（mock clock，无真实 sleep）。
        after_ttl = datetime.now() + import_state.STATE_TTL + timedelta(hours=2)
        with mock.patch('app.services.import_state._now', return_value=after_ttl):
            import_state.save_import_preview([], [])
        self.assertIsNone(self._raw_state(import_id))

        # row missing + 孤儿文件在磁盘 → 仍然 DENY（positive proof 才能发布）。
        self.assertEqual(self._close(self._download(orphan)), 302)

    def test_r2_release_after_committed_returns_false(self):
        # §23：COMMITTED 之后的 release 必须失败，不得把已发布 import 变回
        # AVAILABLE（CAS raw-state 保证 + 显式回归）。
        import_id = self._save_preview([_row('90160', 'rel-committed')])
        claim_a = import_state.claim_import_preview(import_id)
        name_a = _password_artifact_filename(claim_a)

        import_state.finalize_import_preview_claim(claim_a, db.session, password_artifact=name_a)
        db.session.commit()

        self.assertFalse(import_state.release_import_preview_claim(claim_a))
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))

    def test_r2_stale_finalize_against_committed_winner_fails_marker_unchanged(self):
        # §24：lease 恢复 + winner 已 COMMITTED 后，旧 A finalize 必须 ownership
        # lost，且 COMMITTED B marker 原样保留。
        import_id = self._save_preview([_row('90170', 'stale-committed')])
        claim_a = import_state.claim_import_preview(import_id)
        after_lease = datetime.now() + import_state.IMPORT_CLAIM_LEASE + timedelta(minutes=1)
        with mock.patch('app.services.import_state._now', return_value=after_lease):
            claim_b = import_state.claim_import_preview(import_id)

        name_b = _password_artifact_filename(claim_b)
        import_state.finalize_import_preview_claim(claim_b, db.session, password_artifact=name_b)
        db.session.commit()
        committed_raw = self._raw_state(import_id)
        self.assertIn('"_import_state": "committed"', committed_raw)

        with self.assertRaises(import_state.ImportPreviewOwnershipLostError):
            import_state.finalize_import_preview_claim(claim_a, db.session)
        db.session.rollback()

        self.assertEqual(self._raw_state(import_id), committed_raw)

    def test_r2_committed_marker_contains_no_business_payload(self):
        # §26/§22：COMMITTED envelope 只含 terminal metadata（R3 起含 4 计数的
        # `_result` 快照），不保留 rows / expected_cols / 通讯录字段 / 明文密码，
        # 可无限期保留而不泄露 preview 明文。
        user = self._make_existing_user(number='80070', student_id='payload-1')
        import_id = self._save_preview([_row('80070', 'payload-1')])

        response = self._confirm(import_id, overwrite=True)
        self.assertEqual(response.status_code, 200)

        raw = self._raw_state(import_id)
        self.assertIsNotNone(raw)
        decoded = json.loads(raw)
        self.assertEqual(decoded['_import_state'], 'committed')
        self.assertEqual(decoded['_import_state_version'], 3)
        self.assertNotIn('rows', decoded)
        self.assertNotIn('expected_cols', decoded)
        self.assertNotIn('payload', decoded)
        self.assertEqual(
            decoded['_password_artifact'],
            self._filename_from_url(response.get_json()['password_file_url']),
        )
        self.assertEqual(decoded['_claim_token'], self._state_token(import_id))

        # A：v3 `_result` 只允许 4 个计数键，且与成功响应快照一致。
        self.assertEqual(
            set(decoded['_result'].keys()),
            {'imported_count', 'updated_count', 'skipped_count', 'total_users'},
        )
        for key in ('imported_count', 'updated_count', 'skipped_count', 'total_users'):
            self.assertEqual(decoded['_result'][key], response.get_json()[key])

        # N：原始行字段与导出明文密码都不得出现在 envelope 里。
        self.assertNotIn('payload-1', raw)
        self.assertNotIn('80070', raw)
        exported = pd.read_excel(self._export_path(decoded['_password_artifact']))
        plaintext = str(exported.loc[exported['student_id'] == 'payload-1'].iloc[0]['password'])
        self.assertNotIn(plaintext, raw)

    def test_r2_committed_state_terminal_for_load_and_claim(self):
        # §6/§9/§11：COMMITTED 是 terminal——load None、claim None（含 lease
        # “过期”也不得 reclaim）、重复 finalize ownership lost；物理 row 仍在
        # 属于 logical consume，不重建 HTTP at-most-once 语义。
        import_id = self._save_preview([_row('90180', 'terminal')])
        claim = import_state.claim_import_preview(import_id)
        name = _password_artifact_filename(claim)

        import_state.finalize_import_preview_claim(claim, db.session, password_artifact=name)
        db.session.commit()

        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))
        long_after = datetime.now() + import_state.IMPORT_CLAIM_LEASE + timedelta(days=30)
        with mock.patch('app.services.import_state._now', return_value=long_after):
            self.assertIsNone(import_state.claim_import_preview(import_id))
        self.assertIsNotNone(self._raw_state(import_id))  # LOGICALLY_CONSUMED_TERMINAL_STATE

        with self.assertRaises(import_state.ImportPreviewOwnershipLostError):
            import_state.finalize_import_preview_claim(claim, db.session)
        db.session.rollback()

    def test_r2_consume_import_preview_leaves_no_committed_marker(self):
        # §25：历史 consume 契约保持 first → payload / second → None，且它不是
        # 发布协议——不留下 COMMITTED row（物理删除）。
        import_id = self._save_preview([_row('90190', 'consume-r2')])
        payload = import_state.consume_import_preview(import_id)
        self.assertIsNotNone(payload)
        self.assertIsNone(import_state.consume_import_preview(import_id))
        self.assertIsNone(self._raw_state(import_id))

    def test_r2_malformed_and_unknown_envelopes_fail_closed(self):
        # §8：残缺 committed envelope、错误版本、未知状态标记一律 fail closed，
        # 绝不当作 AVAILABLE 或已发布。
        import_id = self._save_preview([_row('90200', 'codec-r2')])
        name = f'passwords_{import_id}_{"b" * 32}.xlsx'
        self._write_artifact(name, b'artifact')

        # committed envelope 缺 claim token → fail closed
        self._set_state(import_id, json.dumps({
            '_import_state_version': 2,
            '_import_state': 'committed',
            '_committed_at': datetime.now().isoformat(),
            '_password_artifact': name,
        }))
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'b' * 32, name))

        # committed envelope 版本错误 → fail closed
        self._set_state(import_id, self._committed_json('b' * 32, name).replace(
            '"_import_state_version": 2', '"_import_state_version": 1',
        ))
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'b' * 32, name))
        self.assertIsNone(import_state.load_import_preview(import_id))

        # 未知状态标记 → fail closed（不得当 AVAILABLE）
        self._set_state(import_id, json.dumps({'_import_state': 'frozen', 'rows': []}))
        self.assertIsNone(import_state.load_import_preview(import_id))
        self.assertIsNone(import_state.claim_import_preview(import_id))
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'b' * 32, name))

        # 合法 committed envelope → 精确 (import_id, token, filename) 匹配才发布
        self._set_state(import_id, self._committed_json('b' * 32, name))
        self.assertTrue(import_state.is_password_artifact_published(import_id, 'b' * 32, name))
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'c' * 32, name))
        self.assertFalse(import_state.is_password_artifact_published(import_id, 'b' * 32, f'passwords_{import_id}_{"c" * 32}.xlsx'))
        self.assertFalse(import_state.is_password_artifact_published(import_id, '', name))
        self.assertFalse(import_state.is_password_artifact_published('', 'b' * 32, name))

    # ---- Round 7C-P1-R3: lost-response recovery / idempotent result replay ----

    def _committed_v3_json(self, token, artifact, result=None):
        envelope = {
            '_import_state_version': 3,
            '_import_state': 'committed',
            '_claim_token': token,
            '_committed_at': datetime.now().isoformat(),
            '_password_artifact': artifact,
        }
        if result is not None:
            envelope['_result'] = result
        return json.dumps(envelope, ensure_ascii=False)

    def test_r3_post_commit_response_loss_recovered(self):
        # §17：POST_COMMIT_RESPONSE_LOSS_RECOVERED——业务成功 + 响应投递丢失
        # （fault 点在 db.session.commit() 之后）后，同 import_id 重试必须
        # 幂等重放原成功结果，且 artifact 可下载、明文验证 DB hash。
        user = self._make_existing_user(number='80090', student_id='r3-loss')
        import_id = self._save_preview([_row('80090', 'r3-loss')])

        with mock.patch('app.blueprints.admin.contacts.jsonify',
                        side_effect=RuntimeError('response lost')):
            with self.assertRaises(RuntimeError):
                self._confirm(import_id, overwrite=True)

        # 服务器实际已经成功（§四）。
        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertFalse(check_password_hash(user_after.password_hash, OLD_PASSWORD))
        self.assertGreaterEqual(self._audit_count(user_after), 1)
        committed = import_state.get_committed_import_result(import_id)
        self.assertIsNotNone(committed)
        self.assertEqual(
            (committed.imported_count, committed.updated_count,
             committed.skipped_count, committed.total_users),
            (0, 1, 0, 2),  # 原始结果快照：更新 1（现有用户），总用户 2
        )

        # 重试：200 幂等重放（PRE_FIX 在 R2 实测 400）。
        retry = self._confirm(import_id, overwrite=True)
        self.assertEqual(retry.status_code, 200)
        replay = retry.get_json()
        self.assertTrue(replay['success'])
        self.assertEqual(replay['message'], '导入完成：新增 0，更新 1，跳过 0')
        self.assertEqual(replay['imported_count'], committed.imported_count)
        self.assertEqual(replay['updated_count'], committed.updated_count)
        self.assertEqual(replay['skipped_count'], committed.skipped_count)
        self.assertEqual(replay['total_users'], committed.total_users)  # 原始快照，非重算

        # password_file_url 可恢复且可下载，明文验证 committed DB hash。
        self.assertEqual(
            replay['password_file_url'],
            f'/admin/download_passwords/{committed.password_artifact}',
        )
        published = self._download(committed.password_artifact)
        try:
            self.assertEqual(published.status_code, 200)
        finally:
            published.close()
        exported = pd.read_excel(self._export_path(committed.password_artifact))
        row = exported.loc[exported['student_id'] == 'r3-loss'].iloc[0]
        self.assertTrue(check_password_hash(user_after.password_hash, str(row['password'])))

    def test_r3_replay_ignores_overwrite_mismatch(self):
        # §16：COMMITTED 已 terminal，第二次请求的 overwrite 不得重新解释
        # committed import——两种方向都只重放原结果，无 side effect。
        user = self._make_existing_user(number='80091', student_id='replay-h1')
        import_id = self._save_preview([_row('80091', 'replay-h1')])

        first = self._confirm(import_id, overwrite=True)
        self.assertEqual(first.status_code, 200)
        db.session.expire_all()
        hash_after_first = db.session.get(User, user.id).password_hash
        audits_after_first = self._audit_count(user)

        second = self._confirm(import_id, overwrite=False)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()['success'])
        self.assertEqual(second.get_json()['imported_count'], first.get_json()['imported_count'])
        self.assertEqual(second.get_json()['updated_count'], first.get_json()['updated_count'])
        self.assertEqual(second.get_json()['skipped_count'], first.get_json()['skipped_count'])
        self.assertEqual(second.get_json()['password_file_url'], first.get_json()['password_file_url'])

        db.session.expire_all()
        user_after = db.session.get(User, user.id)
        self.assertEqual(user_after.password_hash, hash_after_first)
        self.assertEqual(self._audit_count(user_after), audits_after_first)

    def test_r2_committed_v2_marker_publishes_but_has_no_replay(self):
        # §21/§7：升级不得破坏 R2 既有 v2 工件——publication proof 仍有效，
        # 但无 replay metadata：endpoint 走既有 400 兼容行为。
        import_id = self._save_preview([_row('90210', 'v2-compat')])
        claim = import_state.claim_import_preview(import_id)
        name = _password_artifact_filename(claim)
        import_state.finalize_import_preview_claim(claim, db.session, password_artifact=name)
        db.session.commit()
        self._write_artifact(name, b'v2-artifact')

        self.assertTrue(import_state.is_password_artifact_published(import_id, claim.claim_token, name))
        published = self._download(name)
        try:
            self.assertEqual(published.status_code, 200)
            self.assertEqual(published.data, b'v2-artifact')
        finally:
            published.close()

        self.assertIsNone(import_state.get_committed_import_result(import_id))
        confirm = self._confirm(import_id, overwrite=True)
        self.assertEqual(confirm.status_code, 400)
        self.assertEqual(confirm.get_json()['message'], EXPIRED_MESSAGE)

    def test_r3_malformed_v3_result_fails_closed(self):
        # M：v3 `_result` 残缺/类型错误/携带额外键 → replay None（不得伪造
        # 成功重放）；token+filename 完好时 publication proof 不受影响。
        import_id = self._save_preview([_row('90220', 'v3-bad')])
        name = f'passwords_{import_id}_{"b" * 32}.xlsx'
        self._write_artifact(name, b'artifact')

        bad_results = (
            None,                                     # 缺失
            {'imported_count': '1', 'updated_count': 0, 'skipped_count': 0, 'total_users': 2},
            {'imported_count': 1, 'updated_count': 0, 'skipped_count': 0},          # 缺键
            {'imported_count': 1, 'updated_count': 0, 'skipped_count': 0, 'total_users': 2, 'rows': []},
        )
        for bad in bad_results:
            self._set_state(import_id, self._committed_v3_json('b' * 32, name, bad))
            self.assertIsNone(import_state.get_committed_import_result(import_id))
            self.assertEqual(self._close(self._confirm(import_id, overwrite=True)), 400)
            # token+filename 完好 → publication proof 保持（R2 契约不变）。
            self.assertTrue(import_state.is_password_artifact_published(import_id, 'b' * 32, name))
            self.assertEqual(self._close(self._download(name)), 200)

        # 合法 v3 → replay 可用。
        self._set_state(import_id, self._committed_v3_json(
            'b' * 32, name,
            {'imported_count': 1, 'updated_count': 0, 'skipped_count': 0, 'total_users': 2},
        ))
        committed = import_state.get_committed_import_result(import_id)
        self.assertIsNotNone(committed)
        self.assertEqual(committed.imported_count, 1)

    def test_r3_no_password_import_replay_returns_no_artifact_url(self):
        # O：无密码工件（全部跳过）的 import，replay 的 password_file_url 为
        # None，且不产生任何工件文件。
        self._make_existing_user(number='80093', student_id='replay-o1')
        import_id = self._save_preview([_row('80093', 'replay-o1')])

        first = self._confirm(import_id, overwrite=False)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json()['success'])
        self.assertIsNone(first.get_json()['password_file_url'])
        self.assertEqual(first.get_json()['message'], '导入完成：新增 0，更新 0，跳过 1')

        second = self._confirm(import_id, overwrite=False)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()['success'])
        self.assertIsNone(second.get_json()['password_file_url'])
        self.assertEqual(second.get_json()['total_users'], first.get_json()['total_users'])
        self.assertEqual(self._artifact_filenames(import_id), [])


if __name__ == '__main__':
    unittest.main()
