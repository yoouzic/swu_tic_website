"""Real leave retries and photo-draft interleavings in disposable SQLite."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

_BOOT_DIR = tempfile.TemporaryDirectory(prefix='submission-lifecycle-bootstrap-')
_BOOT = Path(_BOOT_DIR.name)
os.environ.update({
    'DATABASE_URL': '', 'INSTANCE_DIR': str(_BOOT / 'instance'), 'SQLITE_DB_PATH': str(_BOOT / 'bootstrap.sqlite'),
    'UPLOAD_FOLDER': str(_BOOT / 'uploads'), 'AUTOMATION_UPLOAD_DIR': str(_BOOT / 'automation'),
    'AUTO_REVIEW_UPLOAD_DIR': str(_BOOT / 'auto-review'), 'AUTO_REVIEW_REPORT_DIR': str(_BOOT / 'reports'),
    'EXPORT_DIR': str(_BOOT / 'exports'), 'CONTACT_TEMPLATE_PATH': str(_BOOT / 'contacts.xlsx'),
    'SCHEDULE_TEMPLATE_PATH': str(_BOOT / 'schedule.xlsx'), 'AUTO_REVIEW_DEFAULT_SCHEDULE_PATH': str(_BOOT / 'missing.xlsx'),
    'STORAGE_CLEANUP_ENABLED': 'false', 'DEEPSEEK_API_KEY': '', 'DEEPSEEK_BASE_URL': 'http://127.0.0.1:1/disabled',
    'CELERY_BROKER_URL': 'memory://', 'CELERY_RESULT_BACKEND': 'cache+memory://', 'SECRET_KEY': 'isolated-lifecycle',
})

from app.blueprints.user import forms as form_routes
from app.models import AssessmentOverride, CourseRegistration, LectureForm, LectureFormDraft, LectureSiteCapture, SystemSetting, User, db
from app.utils.leave_management import get_leave_makeup_forms
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase
from tests.test_launch_submission_safety import valid_submission


class _LeaveClock(datetime):
    @classmethod
    def now(cls):
        return cls(2026, 10, 5, 12)


class SubmissionLifecycleTests(_ReviewMutationCompatibilityBase):
    def setUp(self):
        super().setUp()
        self.client.application.config['LECTURE_CAPTURE_ENABLED'] = False
        self.clock = mock.patch('app.utils.leave_management.datetime', _LeaveClock)
        self.clock.start()
        for key, value in [('teaching_current_semester', '2026-2027-1'), ('teaching_first_week_monday', '2026-09-07'),
                           ('teaching_total_weeks', '16'), ('teaching_required_submission', '1')]:
            SystemSetting.set(key, value)
        with self.client.session_transaction() as session:
            session.update(user_id=self.officer.id, user_role=self.officer.role, user_name=self.officer.name)

    def tearDown(self):
        self.clock.stop()
        super().tearDown()

    def test_oversized_ascii_ids_return_400_and_keep_inputs(self):
        self.client.application.config['TESTING'] = False
        for value in ['9223372036854775808', '9' * 5000]:
            with self.subTest(length=len(value)):
                response = self.client.post('/user/submit_form', data=valid_submission(unique_id=value))
                self.assertEqual(response.status_code, 400)
                self.assertIn(json.dumps('隔离教师', ensure_ascii=True)[1:-1], response.get_data(as_text=True))
                self.assertEqual(LectureForm.query.count(), 0)

    def test_real_leave_completion_cannot_change_retry_identity(self):
        leave = AssessmentOverride(user_id=self.officer.id, semester='2026-2027-1', start_week=1, end_week=1,
                                   override_type='leave', reason='合成已结束请假', created_by=self.dept_admin.id)
        db.session.add(leave)
        db.session.commit()
        body = valid_submission()
        first = self.client.post('/user/submit_form', data=body)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(len(get_leave_makeup_forms(leave)), 1)
        first_form = LectureForm.query.one()
        self.assertIn('请假补交表', first_form.suggestions)
        second = self.client.post('/user/submit_form', data=body)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second.headers['Location'], first.headers['Location'])
        self.assertEqual(LectureForm.query.count(), 1)
        self.assertEqual(len(get_leave_makeup_forms(leave)), 1)

    def test_distinct_request_rechecks_leave_after_actor_lock(self):
        leave = AssessmentOverride(user_id=self.officer.id, semester='2026-2027-1', start_week=1, end_week=1,
                                   override_type='leave', reason='合成请假', created_by=self.dept_admin.id)
        db.session.add(leave)
        db.session.commit()
        user_id, leave_id = self.officer.id, leave.id
        waiting, proceed = threading.Event(), threading.Event()
        real_lock = form_routes.serialize_new_submission
        def paused_lock(actor_id):
            if form_routes.request.form.get('course_title') == '后提交的不同课程':
                waiting.set()
                self.assertTrue(proceed.wait(timeout=15))
            return real_lock(actor_id)
        def submit_second():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=user_id, user_role='信息员')
                return client.post('/user/submit_form', data=valid_submission(course_title='后提交的不同课程')).status_code
        db.session.remove()
        with mock.patch.object(form_routes, 'serialize_new_submission', paused_lock):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(submit_second)
                try:
                    self.assertTrue(waiting.wait(timeout=15))
                    first = self.client.post('/user/submit_form', data=valid_submission())
                    self.assertEqual(first.status_code, 302)
                finally:
                    proceed.set()
                self.assertEqual(future.result(timeout=20), 302)
        db.session.expire_all()
        second = LectureForm.query.filter_by(course_title='后提交的不同课程').one()
        self.assertEqual(second.suggestions, '无')
        self.assertNotIn('第1周', second.audit_tag)
        self.assertEqual(len(get_leave_makeup_forms(db.session.get(AssessmentOverride, leave_id))), 1)

    def test_first_result_survives_more_than_twenty_other_recent_submissions(self):
        first = self.client.post('/user/submit_form', data=valid_submission())
        self.assertEqual(first.status_code, 302)
        for index in range(21):
            response = self.client.post('/user/submit_form', data=valid_submission(course_title=f'合成其他课程{index}'))
            self.assertEqual(response.status_code, 302)
        retried = self.client.post('/user/submit_form', data=valid_submission())
        self.assertEqual(retried.headers['Location'], first.headers['Location'])
        self.assertEqual(LectureForm.query.count(), 22)

    def make_capture(self, marker):
        record = LectureSiteCapture(user_id=self.officer.id, photo_key='synthetic-' + marker,
                                    received_at='2026-10-05T08:00:00+08:00', building='32', room_number='302',
                                    draft_json=json.dumps(valid_submission(course_title='草稿课程-' + marker), ensure_ascii=False))
        db.session.add(record)
        db.session.commit()
        return record

    def enable_capture_with_current_schedule(self):
        # Binding/race contracts belong to the photo-enabled state; a config
        # flag alone cannot activate that state without real current candidates.
        from tests.schedule_fixture import seed_current_schedule
        seed_current_schedule('2026-2027-1')
        self.client.application.config['LECTURE_CAPTURE_ENABLED'] = True

    def test_different_capture_and_registration_are_distinct_business_identities(self):
        self.enable_capture_with_current_schedule()
        first_capture = self.make_capture('A')
        second_capture = self.make_capture('B')
        for record in [first_capture, second_capture]:
            body = valid_submission(site_capture_id=str(record.id))
            response = self.client.post('/user/submit_form', data=body)
            self.assertEqual(response.status_code, 302)
        self.assertEqual(LectureForm.query.count(), 2)
        self.assertNotEqual(first_capture.form_id, second_capture.form_id)
        self.client.application.config['LECTURE_CAPTURE_ENABLED'] = False
        registrations = [CourseRegistration(user_id=self.officer.id, course_code='SYN-' + marker,
                                             selection_code='SYN-SELECT-' + marker) for marker in ['A', 'B']]
        db.session.add_all(registrations)
        db.session.commit()
        for registration in registrations:
            response = self.client.post('/user/submit_form', data=valid_submission(registration_id=str(registration.id)))
            self.assertEqual(response.status_code, 302)
        self.assertEqual(LectureForm.query.count(), 4)

    def test_same_capture_concurrent_distinct_payload_cannot_overwrite_binding(self):
        self.enable_capture_with_current_schedule()
        capture_id, user_id = self.make_capture('shared').id, self.officer.id
        waiting, proceed = threading.Event(), threading.Event()
        real_lock = form_routes.serialize_new_submission
        def paused_lock(actor_id):
            if form_routes.request.form.get('course_title') == '后提交的不同课程':
                waiting.set()
                self.assertTrue(proceed.wait(timeout=15))
            return real_lock(actor_id)
        def submit_second():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=user_id, user_role='信息员')
                return client.post('/user/submit_form', data=valid_submission(
                    site_capture_id=str(capture_id), course_title='后提交的不同课程'))
        db.session.remove()
        with mock.patch.object(form_routes, 'serialize_new_submission', paused_lock):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(submit_second)
                try:
                    self.assertTrue(waiting.wait(timeout=15))
                    first = self.client.post('/user/submit_form', data=valid_submission(site_capture_id=str(capture_id)))
                    self.assertEqual(first.status_code, 302)
                finally:
                    proceed.set()
                second = future.result(timeout=20)
        self.assertEqual(second.status_code, 400)
        self.assertIn(json.dumps('后提交的不同课程', ensure_ascii=True)[1:-1], second.get_data(as_text=True))
        db.session.expire_all()
        self.assertEqual(LectureForm.query.count(), 1)
        first_id = LectureForm.query.one().id
        self.assertEqual(db.session.get(LectureSiteCapture, capture_id).form_id, first_id)
        same_wire = self.client.post('/user/submit_form', data=valid_submission(site_capture_id=str(capture_id)))
        self.assertEqual(same_wire.status_code, 302)
        self.assertEqual(same_wire.headers['Location'], first.headers['Location'])

    def test_deleted_registration_is_rechecked_under_submission_lock(self):
        registration = CourseRegistration(user_id=self.officer.id, course_code='SYN', selection_code='SYN')
        db.session.add(registration)
        db.session.commit()
        registration_id, user_id = registration.id, self.officer.id
        waiting, proceed = threading.Event(), threading.Event()
        real_lock = form_routes.serialize_new_submission
        def paused_lock(actor_id):
            waiting.set()
            self.assertTrue(proceed.wait(timeout=15))
            return real_lock(actor_id)
        def submit_pending():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=user_id, user_role='信息员')
                return client.post('/user/submit_form', data=valid_submission(registration_id=str(registration_id)))
        db.session.remove()
        with mock.patch.object(form_routes, 'serialize_new_submission', paused_lock):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(submit_pending)
                try:
                    self.assertTrue(waiting.wait(timeout=15))
                    deleted = self.client.delete(f'/user/api/my_reservations/{registration_id}')
                    self.assertEqual(deleted.status_code, 200)
                finally:
                    proceed.set()
                response = future.result(timeout=20)
        self.assertEqual(response.status_code, 400)
        db.session.expire_all()
        self.assertIsNone(db.session.get(CourseRegistration, registration_id))
        self.assertEqual(LectureForm.query.count(), 0)

    def test_all_form_delete_paths_remove_receipts_before_sqlite_id_reuse(self):
        from app.services.submission_receipts import submission_receipts
        for route in ['user', 'admin_single', 'admin_group']:
            with self.subTest(route=route):
                with self.client.session_transaction() as session:
                    session.update(user_id=self.officer.id, user_role='信息员')
                first_body = valid_submission(course_title='原始-' + route)
                first = self.client.post('/user/submit_form', data=first_body)
                self.assertEqual(first.status_code, 302)
                form_id = LectureForm.query.order_by(LectureForm.id.desc()).first().id
                if route == 'user':
                    deleted = self.client.post(f'/user/delete_form/{form_id}')
                else:
                    with self.client.session_transaction() as session:
                        session.update(user_id=self.center_admin.id, user_role='管理员')
                    kind = 'form' if route == 'admin_single' else 'group'
                    deleted = self.client.delete(f'/admin/api/review/{kind}/{form_id}')
                self.assertEqual(deleted.status_code, 200)
                with self.client.session_transaction() as session:
                    session.update(user_id=self.officer.id, user_role='信息员')
                replacement = self.client.post('/user/submit_form', data=valid_submission(course_title='复用ID-' + route))
                self.assertEqual(replacement.status_code, 302)
                reused = db.session.get(LectureForm, form_id)
                self.assertEqual(reused.course_title, '复用ID-' + route)
                retried = self.client.post('/user/submit_form', data=first_body)
                self.assertEqual(retried.status_code, 302)
                self.assertNotEqual(retried.headers['Location'], replacement.headers['Location'])
                self.assertEqual(LectureForm.query.filter_by(course_title='原始-' + route).count(), 1)
                self.assertEqual(db.session.execute(db.select(db.func.count()).select_from(submission_receipts)
                                                   .where(submission_receipts.c.form_id == form_id)).scalar(), 1)

    def test_inflight_a_submission_preserves_independent_b_draft(self):
        self.enable_capture_with_current_schedule()
        capture_a, capture_b = self.make_capture('A'), self.make_capture('B')
        user_id, capture_a_id, capture_b_id = self.officer.id, capture_a.id, capture_b.id
        saved = self.client.put('/user/api/lecture_form_draft', json={'data': {
            **valid_submission(), 'site_capture_id': capture_a_id}})
        self.assertEqual(saved.status_code, 200)
        entered, resumed = threading.Event(), threading.Event()
        real_extract = form_routes._extract_assistant_submission_payload
        def pause_a():
            entered.set()
            self.assertTrue(resumed.wait(timeout=15))
            return real_extract()
        def submit_a():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=user_id, user_role='信息员')
                return client.post('/user/submit_form', data=valid_submission(site_capture_id=str(capture_a_id))).status_code
        db.session.remove()
        with mock.patch.object(form_routes, '_extract_assistant_submission_payload', pause_a):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(submit_a)
                try:
                    self.assertTrue(entered.wait(timeout=15))
                    with self.client.application.test_client() as other:
                        with other.session_transaction() as session:
                            session.update(user_id=user_id, user_role='信息员')
                        response = other.post(f'/user/api/site-capture/{capture_b_id}/resume')
                        self.assertEqual(response.status_code, 200)
                finally:
                    resumed.set()
                self.assertEqual(pending.result(timeout=20), 302)
        db.session.expire_all()
        draft = LectureFormDraft.query.filter_by(user_id=user_id, draft_key='submit_form').one_or_none()
        self.assertIsNotNone(draft, 'A submission must not delete the newly resumed B draft')
        payload = json.loads(draft.payload_json)
        self.assertEqual(payload['site_capture_id'], capture_b_id)
        self.assertEqual(payload['course_title'], '草稿课程-B')
        self.assertIsNone(db.session.get(LectureSiteCapture, capture_b_id).form_id)
        self.assertIsNotNone(db.session.get(LectureSiteCapture, capture_a_id).form_id)

    def test_claim_expires_after_120_seconds_and_rollback_leaves_no_claim(self):
        self.assertIsNotNone(importlib.util.find_spec('app.services.submission_receipts'))
        from app.services.submission_receipts import submission_receipts
        first = self.client.post('/user/submit_form', data=valid_submission())
        self.assertEqual(first.status_code, 302)
        form = LectureForm.query.one()
        expired = datetime.now() - timedelta(seconds=121)
        form.created_at = expired
        db.session.execute(db.update(submission_receipts).values(accepted_at=expired))
        db.session.commit()
        second = self.client.post('/user/submit_form', data=valid_submission())
        self.assertEqual(second.status_code, 302)
        self.assertNotEqual(second.headers['Location'], first.headers['Location'])
        self.assertEqual(LectureForm.query.count(), 2)
        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('injected commit failure')):
            failed = self.client.post('/user/submit_form', data=valid_submission(course_title='不能留下认领'))
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(LectureForm.query.count(), 2)
        self.assertEqual(db.session.execute(db.select(db.func.count()).select_from(submission_receipts)).scalar(), 1)

    def test_physical_account_delete_cleans_receipt_with_sqlite_foreign_keys_off(self):
        from app.services.submission_receipts import submission_receipts
        self.assertEqual(db.session.connection().exec_driver_sql('PRAGMA foreign_keys').scalar(), 0)
        created = self.client.post('/user/submit_form', data=valid_submission())
        self.assertEqual(created.status_code, 302)
        form_id, user_id = LectureForm.query.one().id, self.officer.id
        self.assertEqual(self.client.post(f'/user/delete_form/{form_id}').status_code, 200)
        self.assertEqual(db.session.execute(db.select(db.func.count()).select_from(submission_receipts)).scalar(), 0)
        # A legacy/deleted target receipt is still cleaned by account deletion,
        # including deployments that had receipts before the form cleanup fix.
        db.session.execute(db.insert(submission_receipts).values(
            user_id=user_id, signature='synthetic-legacy-receipt', form_id=form_id, accepted_at=datetime.now()))
        db.session.commit()
        self.center_admin.role = '超级管理员'
        db.session.commit()
        with self.client.session_transaction() as session:
            session.update(user_id=self.center_admin.id, user_role='超级管理员')
        deleted = self.client.delete(f'/admin/api/users/{user_id}', json={'password': 'password'})
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.get_json()['success'])
        self.assertEqual(db.session.execute(db.select(db.func.count()).select_from(submission_receipts)).scalar(), 0)

    def test_account_delete_count_and_submit_share_actor_lock(self):
        from app.services import submission_receipts as receipt_service
        self.center_admin.role = '超级管理员'
        db.session.commit()
        admin_id, user_id = self.center_admin.id, self.officer.id
        counted, proceed, submit_entered = threading.Event(), threading.Event(), threading.Event()
        real_cleanup = receipt_service.delete_user_submission_receipts
        real_lock = form_routes.serialize_new_submission
        def pause_delete(actor_id):
            counted.set()  # Existing form count is zero; deletion is imminent.
            self.assertTrue(proceed.wait(timeout=15))
            return real_cleanup(actor_id)
        def mark_submit(actor_id):
            submit_entered.set()
            return real_lock(actor_id)
        def delete_account():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=admin_id, user_role='超级管理员')
                return client.delete(f'/admin/api/users/{user_id}', json={'password': 'password'}).status_code
        def submit_form():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=user_id, user_role='信息员')
                return client.post('/user/submit_form', data=valid_submission()).status_code
        db.session.remove()
        with mock.patch.object(receipt_service, 'delete_user_submission_receipts', pause_delete):
            with mock.patch.object(form_routes, 'serialize_new_submission', mark_submit):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    deletion = pool.submit(delete_account)
                    submission = None
                    try:
                        self.assertTrue(counted.wait(timeout=15))
                        submission = pool.submit(submit_form)
                        self.assertTrue(submit_entered.wait(timeout=15))
                        threading.Event().wait(timeout=.3)
                        self.assertFalse(submission.done(), 'New submission must wait while account deletion owns the actor lock')
                    finally:
                        proceed.set()
                    self.assertEqual(deletion.result(timeout=20), 200)
                    self.assertIsNotNone(submission)
                    self.assertEqual(submission.result(timeout=20), 409)
        db.session.expire_all()
        self.assertIsNone(db.session.get(User, user_id))
        self.assertEqual(LectureForm.query.count(), 0)

    def test_completed_submission_before_account_lock_blocks_delete(self):
        from app.blueprints.admin import users as user_routes
        self.center_admin.role = '超级管理员'
        db.session.commit()
        admin_id, user_id = self.center_admin.id, self.officer.id
        waiting, proceed = threading.Event(), threading.Event()
        real_lock = user_routes.serialize_new_submission
        def pause_delete(actor_id):
            waiting.set()
            self.assertTrue(proceed.wait(timeout=15))
            return real_lock(actor_id)
        def delete_account():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=admin_id, user_role='超级管理员')
                return client.delete(f'/admin/api/users/{user_id}', json={'password': 'password'}).status_code
        db.session.remove()
        with mock.patch.object(user_routes, 'serialize_new_submission', pause_delete):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(delete_account)
                try:
                    self.assertTrue(waiting.wait(timeout=15))
                    submitted = self.client.post('/user/submit_form', data=valid_submission())
                    self.assertEqual(submitted.status_code, 302)
                finally:
                    proceed.set()
                self.assertEqual(pending.result(timeout=20), 400)
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(User, user_id))
        self.assertEqual(LectureForm.query.count(), 1)

    def test_receipt_table_creation_is_additive_and_repeatable_for_old_database(self):
        from app.services.submission_receipts import submission_receipts
        created = self.client.post('/user/submit_form', data=valid_submission())
        self.assertEqual(created.status_code, 302)
        before = db.session.connection().exec_driver_sql('SELECT * FROM lecture_forms ORDER BY id').fetchall()
        db.session.commit()
        submission_receipts.drop(db.engine)
        db.create_all()
        db.create_all()
        after = db.session.connection().exec_driver_sql('SELECT * FROM lecture_forms ORDER BY id').fetchall()
        self.assertEqual(before, after)
        self.assertEqual(db.session.execute(db.select(db.func.count()).select_from(submission_receipts)).scalar(), 0)
        retry = self.client.post('/user/submit_form', data=valid_submission())
        self.assertEqual(retry.headers['Location'], created.headers['Location'])
        self.assertEqual(db.session.execute(db.select(db.func.count()).select_from(submission_receipts)).scalar(), 1)

    def test_receipt_cleanup_rolls_back_with_failed_form_deletion(self):
        from app.services.submission_receipts import submission_receipts
        self.assertEqual(self.client.post('/user/submit_form', data=valid_submission()).status_code, 302)
        form_id = LectureForm.query.one().id
        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('injected delete commit failure')):
            deleted = self.client.post(f'/user/delete_form/{form_id}')
        self.assertEqual(deleted.status_code, 500)
        self.assertIsNotNone(db.session.get(LectureForm, form_id))
        self.assertEqual(db.session.execute(db.select(db.func.count()).select_from(submission_receipts)).scalar(), 1)
