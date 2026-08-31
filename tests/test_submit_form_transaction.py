# -*- coding: utf-8 -*-
"""Round 7C-P2 submit_form transaction ownership regressions.

Transaction outcomes (§五):

- Outcome A (normal accepted): form mutation + registration binding +
  optional leave makeup + draft DELETE → ONE final commit;
- Outcome B (duplicate accepted): no new LectureForm; registration binding
  bookkeeping + optional leave makeup + draft DELETE → ONE final commit,
  regardless of leave_makeup presence;
- Outcome C (failure): rollback — no partial form/registration/leave
  mutation, draft preserved.

Covers:
- duplicate success persists the draft deletion (PRE_FIX: not committed);
- registration.is_used no longer depends on Course lookup success;
- normal / duplicate commit failures roll back and preserve the draft;
- leave makeup stays in the same transaction as the draft deletion.
"""
import os
import tempfile
import unittest
from unittest import mock

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'submit_form_tx_test.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'

from app.app import app
from app.models import (
    AssessmentOverride,
    Course,
    CourseRegistration,
    LectureForm,
    LectureFormDraft,
    User,
    db,
)
from app.utils.leave_management import get_leave_makeup_forms, LEAVE_OVERRIDE_TYPE
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


INFO_MEMBER = '\u4fe1\u606f\u5458'
PPT_METHOD = 'PPT\u6f14\u793a\u6cd5'
GOOD = '\u597d'
RECOMMENDED = '\u63a8\u8350'


class SubmitFormTransactionTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
        )
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.user = self._create_user('1001', 'student-1001')
        self._login_as(self.user)

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    # ---- fixtures ----

    def _create_user(self, number, student_id):
        user = User(
            number=number,
            department='test',
            name='Test User',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=INFO_MEMBER,
            group='test',
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _login_as(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _valid_form_payload(self):
        return {
            'lecture_date': '2026-06-04',
            'lecture_date_display': '2026/06/04',
            'start_period': '3',
            'end_period': '4',
            'class_period': '\u7b2c3-4\u8282',
            'lecture_location': '32-302',
            'teacher_name': 'Teacher A',
            'teacher_college': 'Test College',
            'course_title': 'Database Systems',
            'student_grade_class': '2024 Test Class',
            'course_changes': '\u65e0',
            'abnormal_situation': '\u65e0',
            'teaching_method': PPT_METHOD,
            'classroom_discipline': GOOD,
            'classroom_atmosphere': GOOD,
            'courseware_quality': GOOD,
            'overall_effect': GOOD,
            'quality_case': RECOMMENDED,
            'course_feedback': 'This course feedback is intentionally longer than fifty characters for testing submit.',
            'suggestions': '\u65e0',
            'student_signature1': 'Student One',
            'contact_phone1': '13800000001',
            'student_signature2': 'Student Two',
            'contact_phone2': '13800000002',
        }

    def _save_draft(self, payload=None):
        response = self.client.put(
            '/user/api/lecture_form_draft',
            json={'data': payload or {'course_title': 'Pending Draft'}},
        )
        self.assertEqual(response.status_code, 200)
        return response

    def _draft_count(self):
        # 测试用例长驻 app context 时，请求与测试共享同一 scoped session：
        # 重定向不会回收 session，未 commit 的 DELETE 会在下一次查询时被
        # autoflush，制造"看似已删除"的假象。先 rollback 丢弃 pending 变更，
        # 再查询，确保计数反映数据库的持久化状态。
        db.session.rollback()
        return LectureFormDraft.query.filter_by(user_id=self.user.id).count()

    def _form_count(self):
        db.session.rollback()
        return LectureForm.query.filter_by(listener_number=self.user.number).count()

    def _make_registration(self, course_code='X100', selection_code='Y200', with_course=True):
        if with_course:
            db.session.add(Course(
                course_code=course_code,
                selection_code=selection_code,
                course_name='Database Systems',
            ))
        registration = CourseRegistration(
            course_code=course_code,
            selection_code=selection_code,
            user_id=self.user.id,
            is_used=False,
        )
        db.session.add(registration)
        db.session.commit()
        return registration

    def _make_leave_override(self, start_week=2, end_week=4):
        override = AssessmentOverride(
            user_id=self.user.id,
            start_week=start_week,
            end_week=end_week,
            override_type=LEAVE_OVERRIDE_TYPE,
            reason='测试请假',
            created_by=self.user.id,
        )
        db.session.add(override)
        db.session.commit()
        return override

    def _pending_leave_stub(self, override):
        return {
            'override_id': override.id,
            'leave_week': 3,
            'pending_makeup_count': 1,
        }

    def _durable_registration(self, registration_id):
        """Reload registration state from the database (discard pending changes)."""
        db.session.rollback()
        db.session.expire_all()
        return db.session.get(CourseRegistration, registration_id)

    def _submit(self, payload=None, registration_id=None):
        data = dict(payload or self._valid_form_payload())
        if registration_id is not None:
            data['registration_id'] = str(registration_id)
        return self.client.post('/user/submit_form', data=data)

    # ---- Bug A: duplicate success persists the draft deletion ----

    def test_duplicate_success_deletes_draft_persistently(self):
        # PRE_FIX_REPRO（DUPLICATE_SUBMISSION_DRAFT_DELETE_NOT_COMMITTED）：
        # 旧代码 duplicate 分支仅在 leave_makeup 存在时才 commit，
        # 无 leave 的 duplicate 成功丢失 draft DELETE。
        first = self._submit()
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self._form_count(), 1)

        self._save_draft()
        self.assertEqual(self._draft_count(), 1)

        second = self._submit()  # 120 秒 duplicate window 内的相同 payload
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self._form_count(), 1)   # 不新建表单
        self.assertEqual(self._draft_count(), 0)  # DELETE 必须被持久化

    def test_duplicate_success_full_terminal_state(self):
        # §17：duplicate success 的完整 terminal-state contract——表单数不变、
        # draft 消失、registration 绑定置位。
        registration = self._make_registration()

        first = self._submit(registration_id=registration.id)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self._form_count(), 1)
        self.assertTrue(self._durable_registration(registration.id).is_used)

        self._save_draft()
        self.assertEqual(self._draft_count(), 1)

        second = self._submit(registration_id=registration.id)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self._form_count(), 1)
        self.assertEqual(self._draft_count(), 0)
        self.assertTrue(self._durable_registration(registration.id).is_used)

    # ---- Bug B: registration binding decoupled from Course lookup ----

    def test_orphan_course_registration_still_bound_on_accept(self):
        # PRE_FIX_REPRO（REGISTRATION_USED_STATE_DEPENDS_ON_COURSE_LOOKUP）：
        # 旧代码仅在 Course lookup 命中时置 is_used；Course 缺失时
        # registration 仍应被成功提交的表单绑定（HTTP 行为不变，仍 302）。
        registration = self._make_registration(with_course=False)  # Course(X, Y) 不存在

        response = self._submit(registration_id=registration.id)
        self.assertEqual(response.status_code, 302)

        form = LectureForm.query.filter_by(listener_number=self.user.number).first()
        self.assertIsNotNone(form)
        self.assertEqual(str(form.registration_id), str(registration.id))
        self.assertTrue(self._durable_registration(registration.id).is_used)

    # ---- Outcome C: commit failures roll back and preserve the draft ----

    def test_failed_normal_submission_preserves_draft_and_registration(self):
        # §14：FAILED_SUBMISSION_PRESERVES_DRAFT——normal 提交最终 commit 失败
        # → rollback：无表单、draft 保留、registration 绑定回滚。
        self._save_draft()
        registration = self._make_registration()
        self.assertEqual(self._draft_count(), 1)

        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('commit fault')):
            response = self._submit(registration_id=registration.id)

        self.assertEqual(response.status_code, 200)  # 错误 flash + 表单页
        self.assertEqual(self._form_count(), 0)
        self.assertEqual(self._draft_count(), 1)
        self.assertFalse(self._durable_registration(registration.id).is_used)

    def test_failed_duplicate_submission_preserves_draft(self):
        # §15：DUPLICATE_SUCCESS_TRANSACTION_COMMIT_PROVEN——duplicate 分支的
        # commit 真正属于事务：commit 失败 → 非 success redirect、表单数不变、
        # draft 保留（旧代码 leave_makeup=False 时根本不 commit，fault 不触发，
        # 会错误地 302 成功）。
        first = self._submit()
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self._form_count(), 1)

        self._save_draft()
        self.assertEqual(self._draft_count(), 1)

        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('commit fault')):
            response = self._submit()  # duplicate path

        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(self._form_count(), 1)
        self.assertEqual(self._draft_count(), 1)

    # ---- leave makeup stays in the same transaction ----

    def test_duplicate_accept_with_pending_leave_binds_existing_form(self):
        # §18：duplicate accepted + pending leave → 不新建表单、leave makeup
        # 指向已存在的 duplicate 表单、draft 消失、同一事务。
        override = self._make_leave_override()
        leave_stub = self._pending_leave_stub(override)

        with mock.patch('app.blueprints.user.forms.get_pending_leave_makeup',
                        return_value=leave_stub):
            first = self._submit()
            self.assertEqual(first.status_code, 302)
            self.assertEqual(self._form_count(), 1)

            self._save_draft()
            self.assertEqual(self._draft_count(), 1)

            second = self._submit()
            self.assertEqual(second.status_code, 302)

        self.assertEqual(self._form_count(), 1)
        self.assertEqual(self._draft_count(), 0)

        db.session.rollback()
        db.session.expire_all()
        override_after = db.session.get(AssessmentOverride, override.id)
        makeup_forms = get_leave_makeup_forms(override_after)
        self.assertEqual(len(makeup_forms), 1)
        existing_form = LectureForm.query.filter_by(listener_number=self.user.number).first()
        self.assertEqual(makeup_forms[0].get('form_id'), existing_form.id)


if __name__ == '__main__':
    unittest.main()
