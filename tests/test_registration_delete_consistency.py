# -*- coding: utf-8 -*-
"""Round 8A-R1 registration delete consistency / legacy cancel hardening.

所有删除 CourseRegistration 的用户入口都必须服从 canonical actual binding：

- modern ``DELETE /user/api/my_reservations/<id>``（已有
  ``registration_has_form_binding`` guard，保持契约）；
- legacy ``POST /user/api/cancel_reservation``（本轮收口）：按
  ``(user_id, course_code, selection_code)`` 全量匹配，显式 ordering
  ``created_at DESC, id DESC``，经 ``form_bindings`` 批量分类后只删除
  **最新的 canonical unbound registration**；全部 bound → 400 保护 binding
  graph；``is_used`` mirror 不参与 cancel decision。

Test matrix A–L（§17）；所有断言读持久化 DB 状态（rollback 后再读）。
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'reg_delete_test.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'

from app.app import app
from app.models import CourseRegistration, LectureForm, User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


INFO_MEMBER = '\u4fe1\u606f\u5458'
BOUND_MSG = '该登记已绑定听课反馈表单，不能删除'


class RegistrationDeleteConsistencyTest(unittest.TestCase):
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

    def _make_registration(self, course_code='C', selection_code='S', is_used=False,
                           created_at=None):
        registration = CourseRegistration(
            course_code=course_code,
            selection_code=selection_code,
            user_id=self.user.id,
            is_used=is_used,
            created_at=created_at or datetime.now(),
        )
        db.session.add(registration)
        db.session.commit()
        return registration

    def _make_bound_form(self, registration, unique_id=9001):
        form = LectureForm(
            unique_id=unique_id,
            registration_id=registration.id,
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
        )
        db.session.add(form)
        db.session.commit()
        return form

    def _legacy_cancel(self, course_code='C', selection_code='S', raw_body=None):
        if raw_body is not None:
            return self.client.post(
                '/user/api/cancel_reservation',
                data=raw_body, content_type='application/json',
            )
        return self.client.post(
            '/user/api/cancel_reservation',
            json={'course_code': course_code, 'selection_code': selection_code},
        )

    def _durable_registration(self, registration_id):
        db.session.rollback()
        db.session.expire_all()
        return db.session.get(CourseRegistration, registration_id)

    def _durable_form_link(self, form):
        db.session.rollback()
        db.session.expire_all()
        return db.session.get(LectureForm, form.id).registration_id

    # ---- A/L: legacy cancel 不得删除 bound registration ----

    def test_a_legacy_cancel_bound_registration_denied_binding_preserved(self):
        # A + L：canonical bound → 400，且 binding graph 完整保留（registration
        # row 存在、form row 存在、form.registration_id 原值不变）。
        registration = self._make_registration(created_at=datetime(2026, 1, 1, 12, 0, 0))
        form = self._make_bound_form(registration)

        response = self._legacy_cancel()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(response.get_json()['message'], BOUND_MSG)

        self.assertIsNotNone(self._durable_registration(registration.id))
        db.session.rollback()
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(LectureForm, form.id))
        self.assertEqual(self._durable_form_link(form), registration.id)

    # ---- B: legacy cancel unbound single → success ----

    def test_b_legacy_cancel_unbound_single_registration_succeeds(self):
        registration = self._make_registration(created_at=datetime(2026, 1, 1, 12, 0, 0))

        response = self._legacy_cancel()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(response.get_json()['message'], '取消登记成功')
        self.assertIsNone(self._durable_registration(registration.id))

    # ---- C: duplicate matches: bound old + unbound new ----

    def test_c_duplicate_bound_old_unbound_new_deletes_unbound_only(self):
        # Bug B（LEGACY_CANCEL_DUPLICATE_TARGET_AMBIGUITY）：无序 .first() 在
        # 重复登记下目标歧义。canonical contract：删除最新的 unbound（R2），
        # 保留 bound（R1）及其 form binding。
        reg_bound_old = self._make_registration(created_at=datetime(2026, 1, 1, 12, 0, 0))
        reg_unbound_new = self._make_registration(created_at=datetime(2026, 1, 2, 12, 0, 0))
        form = self._make_bound_form(reg_bound_old, unique_id=9101)

        response = self._legacy_cancel()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertIsNone(self._durable_registration(reg_unbound_new.id))
        self.assertIsNotNone(self._durable_registration(reg_bound_old.id))
        self.assertEqual(self._durable_form_link(form), reg_bound_old.id)

    # ---- D: duplicate matches: unbound old + bound new ----

    def test_d_duplicate_unbound_old_bound_new_deletes_latest_unbound(self):
        # unbound old 是唯一 unbound → "最新可取消" 即它；bound new 保留。
        reg_unbound_old = self._make_registration(created_at=datetime(2026, 1, 1, 12, 0, 0))
        reg_bound_new = self._make_registration(created_at=datetime(2026, 1, 2, 12, 0, 0))
        form = self._make_bound_form(reg_bound_new, unique_id=9102)

        response = self._legacy_cancel()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._durable_registration(reg_unbound_old.id))
        self.assertIsNotNone(self._durable_registration(reg_bound_new.id))
        self.assertEqual(self._durable_form_link(form), reg_bound_new.id)

    # ---- E: all duplicate matches bound → 400 + all preserved ----

    def test_e_duplicate_all_bound_denied_and_all_preserved(self):
        reg_1 = self._make_registration(created_at=datetime(2026, 1, 1, 12, 0, 0))
        reg_2 = self._make_registration(created_at=datetime(2026, 1, 2, 12, 0, 0))
        form_1 = self._make_bound_form(reg_1, unique_id=9103)
        form_2 = self._make_bound_form(reg_2, unique_id=9104)

        response = self._legacy_cancel()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['message'], BOUND_MSG)
        self.assertIsNotNone(self._durable_registration(reg_1.id))
        self.assertIsNotNone(self._durable_registration(reg_2.id))
        self.assertEqual(self._durable_form_link(form_1), reg_1.id)
        self.assertEqual(self._durable_form_link(form_2), reg_2.id)

    # ---- F/G: stale is_used 不参与 cancel decision ----

    def test_f_stale_false_on_bound_registration_still_protected(self):
        # F：actual bound + legacy flag 漂移 False → 仍被 guard 保护。
        registration = self._make_registration(is_used=False, created_at=datetime(2026, 1, 1, 12, 0, 0))
        form = self._make_bound_form(registration)
        db.session.rollback()
        stored = db.session.get(CourseRegistration, registration.id)
        stored.is_used = False
        db.session.commit()

        response = self._legacy_cancel()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._durable_form_link(form), registration.id)

    def test_g_stale_true_on_unbound_registration_still_cancelable(self):
        # G：actual unbound + legacy flag 漂移 True → 仍可取消（mirror 不参与）。
        registration = self._make_registration(is_used=False, created_at=datetime(2026, 1, 1, 12, 0, 0))
        db.session.rollback()
        stored = db.session.get(CourseRegistration, registration.id)
        stored.is_used = True
        db.session.commit()

        response = self._legacy_cancel()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._durable_registration(registration.id))

    # ---- H: no matching registration → existing 404 ----

    def test_h_no_matching_registration_returns_404(self):
        self._make_registration(course_code='OTHER', selection_code='S')

        response = self._legacy_cancel(course_code='MISSING', selection_code='S')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['message'], '未找到预定记录')

    # ---- I: JSON null / empty object → 400（不得 500）----

    def test_i_null_json_and_empty_object_return_400_not_500(self):
        response_null = self._legacy_cancel(raw_body='null')
        self.assertEqual(response_null.status_code, 400)
        self.assertEqual(response_null.get_json()['message'], '参数不完整')

        response_empty = self.client.post(
            '/user/api/cancel_reservation', json={},
        )
        self.assertEqual(response_empty.status_code, 400)
        self.assertEqual(response_empty.get_json()['message'], '参数不完整')

    # ---- J/K: modern DELETE parity ----

    def test_j_modern_delete_bound_parity_with_legacy(self):
        # J：同一 bound registration——modern delete 与 legacy cancel 一致拒绝。
        registration = self._make_registration(created_at=datetime(2026, 1, 1, 12, 0, 0))
        self._make_bound_form(registration)

        modern = self.client.delete(f'/user/api/my_reservations/{registration.id}')
        self.assertEqual(modern.status_code, 400)
        legacy = self._legacy_cancel()
        self.assertEqual(legacy.status_code, 400)
        self.assertIsNotNone(self._durable_registration(registration.id))

    def test_k_modern_delete_unbound_parity_with_legacy(self):
        # K：unbound registration——两条删除入口都允许删除。
        registration = self._make_registration(created_at=datetime(2026, 1, 1, 12, 0, 0))

        modern = self.client.delete(f'/user/api/my_reservations/{registration.id}')
        self.assertEqual(modern.status_code, 200)
        self.assertIsNone(self._durable_registration(registration.id))

        other = self._make_registration(course_code='C2', selection_code='S2')
        legacy = self._legacy_cancel(course_code='C2', selection_code='S2')
        self.assertEqual(legacy.status_code, 200)
        self.assertIsNone(self._durable_registration(other.id))


if __name__ == '__main__':
    unittest.main()
