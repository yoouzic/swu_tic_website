# -*- coding: utf-8 -*-
"""Round 8A registration-binding single-source-of-truth regressions.

Canonical binding state = 存在 ``LectureForm.registration_id == registration.id``
（经 ``app.services.form_bindings`` 批量查询）。``CourseRegistration.is_used``
降级为兼容 mirror：不得决定 availability（unused API）/ editability（PUT/DELETE）/
显示语义；写路径通过 ``reconcile_registration_usage_flags`` 在业务事务内同步。

Covers the Round 8A matrix (A–L):
- A/B: unused_reservations 由 canonical binding 决定（stale flag 不再过滤）;
- C: history ``is_used`` 兼容字段值源收敛为 canonical ``is_bound``;
- D/E/F: my_reservations / PUT / DELETE 只认 canonical binding;
- G/H: pending-form rebind 的 mirror reconciliation（含多绑定保留）;
- I: duplicate accepted 修复 stale False mirror（同事务单 commit）;
- J: commit failure 回滚 form + 双方 mirror flag + draft;
- K/L: 架构断言与既有 version semantics（见 service/draft/bindings 测试文件）。
"""
import os
import tempfile
import unittest
from unittest import mock

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'reg_binding_test.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'

from app.app import app
from app.models import (
    Course,
    CourseRegistration,
    LectureForm,
    LectureFormDraft,
    User,
    db,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


INFO_MEMBER = '\u4fe1\u606f\u5458'
PPT_METHOD = 'PPT\u6f14\u793a\u6cd5'
GOOD = '\u597d'
RECOMMENDED = '\u63a8\u8350'


class RegistrationBindingConsistencyTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
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

    def _make_registration(self, course_code, selection_code, is_used=False, with_course=True):
        if with_course:
            db.session.add(Course(
                course_code=course_code,
                selection_code=selection_code,
                course_name=f'Course {course_code}',
            ))
        registration = CourseRegistration(
            course_code=course_code,
            selection_code=selection_code,
            user_id=self.user.id,
            is_used=is_used,
        )
        db.session.add(registration)
        db.session.commit()
        return registration

    def _set_stored_flag(self, registration, value):
        """Directly set the legacy mirror flag (simulate legacy drift)."""
        db.session.rollback()
        registration = db.session.get(CourseRegistration, registration.id)
        registration.is_used = value
        db.session.commit()

    def _durable_registration(self, registration_id):
        db.session.rollback()
        db.session.expire_all()
        return db.session.get(CourseRegistration, registration_id)

    def _submit(self, payload=None, registration_id=None, unique_id=None):
        data = dict(payload or self._valid_form_payload())
        if registration_id is not None:
            data['registration_id'] = str(registration_id)
        if unique_id is not None:
            data['unique_id'] = str(unique_id)
        return self.client.post('/user/submit_form', data=data)

    def _save_draft(self):
        response = self.client.put(
            '/user/api/lecture_form_draft',
            json={'data': {'course_title': 'Pending Draft'}},
        )
        self.assertEqual(response.status_code, 200)

    def _draft_count(self):
        db.session.rollback()
        return LectureFormDraft.query.filter_by(user_id=self.user.id).count()

    def _owned_pending_form(self):
        db.session.rollback()
        return LectureForm.query.filter_by(listener_number=self.user.number).one()

    def _unused_api_ids(self):
        response = self.client.get('/user/api/unused_reservations')
        self.assertEqual(response.status_code, 200)
        return {item['id'] for item in response.get_json()['data']}

    # ---- A/B: unused_reservations 由 canonical binding 决定 ----

    def test_bound_registration_never_exposed_as_unused(self):
        # A / Bug A（BOUND_REGISTRATION_EXPOSED_AS_UNUSED）：actual binding=True
        # 的 registration 即使 legacy flag 漂移为 False 也不得出现在 unused API。
        registration = self._make_registration('X100', 'Y100')
        first = self._submit(registration_id=registration.id)
        self.assertEqual(first.status_code, 302)
        self._set_stored_flag(registration, False)  # legacy drift: stored False + actual bound

        self.assertNotIn(registration.id, self._unused_api_ids())

    def test_unbound_registration_not_hidden_by_stale_flag(self):
        # B / Bug B（UNBOUND_REGISTRATION_HIDDEN_BY_STALE_IS_USED）：actual
        # unbound 的 registration 即使 legacy flag 漂移为 True 也必须返回。
        registration = self._make_registration('X200', 'Y200', is_used=True)  # stale True + unbound

        self.assertIn(registration.id, self._unused_api_ids())

    # ---- C: history is_used 兼容字段值源收敛 ----

    def test_history_used_flag_converges_to_canonical_binding(self):
        # C：is_used 兼容字段与 canonical is_bound 同值（双向）。
        bound_reg = self._make_registration('X300', 'Y300')
        self._submit(registration_id=bound_reg.id)
        self._set_stored_flag(bound_reg, False)  # stored False + actual bound

        stale_reg = self._make_registration('X400', 'Y400', is_used=True)  # stored True + unbound

        response = self.client.get(
            '/user/api/course_registration_history?course_code=X300&selection_code=Y300'
        )
        self.assertEqual(response.status_code, 200)
        items = {item['id']: item for item in response.get_json()['data']}
        self.assertTrue(items[bound_reg.id]['is_bound'])
        self.assertTrue(items[bound_reg.id]['is_used'])

        response = self.client.get(
            '/user/api/course_registration_history?course_code=X400&selection_code=Y400'
        )
        self.assertEqual(response.status_code, 200)
        items = {item['id']: item for item in response.get_json()['data']}
        self.assertFalse(items[stale_reg.id]['is_bound'])
        self.assertFalse(items[stale_reg.id]['is_used'])

    # ---- D/E/F: my_reservations 与 PUT/DELETE 只认 canonical binding ----

    def test_my_reservations_editability_ignores_stale_flag(self):
        # D：can_edit / can_delete 只由 canonical is_bound 决定，stale flag 不参与。
        stale_true_unbound = self._make_registration('X500', 'Y500', is_used=True)
        stored_false_bound = self._make_registration('X600', 'Y600')
        self._submit(registration_id=stored_false_bound.id)
        self._set_stored_flag(stored_false_bound, False)

        response = self.client.get('/user/api/my_reservations')
        self.assertEqual(response.status_code, 200)
        items = {item['id']: item for item in response.get_json()['data']}

        self.assertTrue(items[stale_true_unbound.id]['is_bound'] is False)
        self.assertTrue(items[stale_true_unbound.id]['can_edit'])
        self.assertTrue(items[stale_true_unbound.id]['can_delete'])

        self.assertTrue(items[stored_false_bound.id]['is_bound'])
        self.assertFalse(items[stored_false_bound.id]['can_edit'])
        self.assertFalse(items[stored_false_bound.id]['can_delete'])

    def test_bound_registration_put_delete_denied_regardless_of_stale_flag(self):
        # E/F：actual bound → PUT/DELETE deny，无论 legacy flag 是 False 还是 True。
        registration = self._make_registration('X700', 'Y700')
        self._submit(registration_id=registration.id)
        self._set_stored_flag(registration, False)  # stale False + actual bound

        put = self.client.put(
            f'/user/api/my_reservations/{registration.id}',
            json={'listening_info': '2026-06-10 3-4节 32-302'},
        )
        self.assertEqual(put.status_code, 400)
        delete = self.client.delete(f'/user/api/my_reservations/{registration.id}')
        self.assertEqual(delete.status_code, 400)

    # ---- G/H: pending-form rebind 的 mirror reconciliation ----

    def test_pending_form_rebind_reconciles_both_mirrors(self):
        # G / §14（OLD_REGISTRATION_MIRROR_STALE_AFTER_REBIND）：pending 表单
        # A→B rebind 后，mirror 必须同步：A False / B True。
        reg_a = self._make_registration('X800', 'Y800')
        reg_b = self._make_registration('X900', 'Y900')

        first = self._submit(registration_id=reg_a.id)
        self.assertEqual(first.status_code, 302)
        form = self._owned_pending_form()
        self.assertIsNotNone(form.unique_id)

        second = self._submit(registration_id=reg_b.id, unique_id=form.unique_id)
        self.assertEqual(second.status_code, 302)

        db.session.rollback()
        form_after = self._owned_pending_form()
        self.assertEqual(str(form_after.registration_id), str(reg_b.id))

        self.assertFalse(self._durable_registration(reg_a.id).is_used)
        self.assertTrue(self._durable_registration(reg_b.id).is_used)

    def test_rebind_preserves_multi_binding_old_registration(self):
        # H / §16：old registration 仍有其它 binding 时不得置 False——
        # canonical reconcile 查询实际 binding graph，而不是手写 old=False。
        reg_a = self._make_registration('X950', 'Y950')
        reg_b = self._make_registration('X960', 'Y960')

        first = self._submit(registration_id=reg_a.id)
        self.assertEqual(first.status_code, 302)
        form_f1 = self._owned_pending_form()

        other_payload = dict(self._valid_form_payload())
        other_payload['course_title'] = 'A Different Course Title'
        second = self._submit(payload=other_payload, registration_id=reg_a.id)
        self.assertEqual(second.status_code, 302)  # F2：不同内容 → 非 duplicate → A 的第二个逻辑表单

        third = self._submit(registration_id=reg_b.id, unique_id=form_f1.unique_id)
        self.assertEqual(third.status_code, 302)  # F1 rebind → B

        # F2 仍引用 A → A.is_used 必须保持 True；B.is_used True。
        self.assertTrue(self._durable_registration(reg_a.id).is_used)
        self.assertTrue(self._durable_registration(reg_b.id).is_used)

    # ---- I: duplicate accepted 修复 stale False mirror ----

    def test_duplicate_accept_repairs_stale_false_mirror(self):
        # I / §18：duplicate detection signature 含 registration_id，existing
        # form 的 binding 与请求一致；duplicate accepted 后 reconciliation 在
        # 同一事务内修复 legacy is_used=False 漂移（actual binding True）。
        registration = self._make_registration('X970', 'Y970')
        first = self._submit(registration_id=registration.id)
        self.assertEqual(first.status_code, 302)
        self._set_stored_flag(registration, False)  # legacy drift

        self._save_draft()
        self.assertEqual(self._draft_count(), 1)

        second = self._submit(registration_id=registration.id)
        self.assertEqual(second.status_code, 302)  # duplicate accepted

        self.assertEqual(self._form_count(), 1)
        self.assertEqual(self._draft_count(), 0)
        self.assertTrue(self._durable_registration(registration.id).is_used)

    def _form_count(self):
        db.session.rollback()
        return LectureForm.query.filter_by(listener_number=self.user.number).count()

    # ---- J: commit failure 回滚 form + 双方 mirror flag + draft ----

    def test_rebind_commit_failure_rolls_back_form_and_mirrors(self):
        # J / §20：rebind 最终 commit fault → form 仍绑定 A，A/B mirror 保持
        # 原状态，draft 保留（reconciliation 绝不能 self-commit / 提前生效）。
        reg_a = self._make_registration('X980', 'Y980')
        reg_b = self._make_registration('X990', 'Y990')

        first = self._submit(registration_id=reg_a.id)
        self.assertEqual(first.status_code, 302)
        form = self._owned_pending_form()
        self._save_draft()

        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('commit fault')):
            response = self._submit(registration_id=reg_b.id, unique_id=form.unique_id)

        self.assertEqual(response.status_code, 200)  # 错误 flash + 表单页
        db.session.rollback()
        form_after = self._owned_pending_form()
        self.assertEqual(str(form_after.registration_id), str(reg_a.id))
        self.assertTrue(self._durable_registration(reg_a.id).is_used)
        self.assertFalse(self._durable_registration(reg_b.id).is_used)
        self.assertEqual(self._draft_count(), 1)

    # ---- reconcile primitive 直接契约 ----

    def test_reconcile_primitive_syncs_flags_without_commit(self):
        # reconcile 为 transaction-local mirror primitive：给定 ids，按当前
        # 事务内实际 binding 同步 is_used；由调用方负责 flush/commit。
        # （POST_FIX-only：R8A 新增 API，方法内延迟导入。）
        from app.services.form_bindings import reconcile_registration_usage_flags

        bound = self._make_registration('X995', 'Y995')
        unbound = self._make_registration('X996', 'Y996')
        # 先把漂移状态入库（committed baseline）：
        # bound: stored False + actual bound；unbound: stored True + actual unbound。
        bound.is_used = False
        unbound.is_used = True
        db.session.commit()
        db.session.add(LectureForm(
            unique_id=9001,
            registration_id=bound.id,
            listener_name='L',
            listener_number=self.user.number,
            lecture_date='2026-06-04',
            class_period='3-4',
            lecture_location='A101',
            teacher_name='T',
            teacher_college='C',
            course_title='C',
            student_grade_class='G',
            teaching_method='M',
            classroom_discipline='D',
            classroom_atmosphere='A',
            courseware_quality='Q',
            overall_effect='E',
            quality_case='Case',
            course_feedback='F',
            suggestions='S',
            student_signature1='sig',
            contact_phone1='123',
        ))
        db.session.flush()  # 调用方显式 flush（§12）

        flags = reconcile_registration_usage_flags([bound.id, unbound.id, None, 999999])
        self.assertEqual(flags[bound.id], True)
        self.assertEqual(flags[unbound.id], False)

        db.session.rollback()  # 未 commit → 回滚后 mirror 回到漂移状态
        db.session.expire_all()
        self.assertFalse(db.session.get(CourseRegistration, bound.id).is_used)
        self.assertTrue(db.session.get(CourseRegistration, unbound.id).is_used)


if __name__ == '__main__':
    unittest.main()
