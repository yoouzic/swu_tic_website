# -*- coding: utf-8 -*-
"""Round 8B-P0 characterization tests for the two review mutation endpoints.

    A: POST /admin/api/review/submit/<form_id>  (review.py::submit_review)
    B: POST /admin/api/review/form/<form_id>    (review.py::submit_form_review)

READ-ONLY PRODUCTION ROUND: every assertion below locks in the CURRENT real
behavior of the two endpoints, including behavior that is classified as a
likely defect.  Nothing here asserts "desired" behavior; production code must
remain unchanged while these tests pass.

Classification tags used in docstrings:
    COMMON_INVARIANT   both routes must keep this forever
    FROZEN_COMPAT      already locked by an existing regression (referenced)
    LIKELY_DEFECT      mechanically provable data/contract problem (not fixed here)
    UNKNOWN_POLICY     real divergence, owner must decide which side is canonical
    IMPLEMENTATION_ONLY code-organization only, no observable semantics

Round 8B-P1 note: the four P0 tests that locked the proven LD-1/LD-2/LD-3
defect contracts were converted in place into POST_FIX regressions
(renamed, defect assertions replaced by correctness assertions —
EXPECTED_DEFECT_CONTRACT_CHANGE).  The PRE_FIX proof lives in the P0 report
and in git history of this file.  Focused new regressions live in
tests/test_review_mutation_correctness.py.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import (
    CourseRegistration,
    Department,
    Group,
    LectureForm,
    Permission,
    RolePermission,
    ScoreItem,
    ScoreRecord,
    User,
    db,
)
from app.utils.review_permissions import can_review_status, get_next_status_after_review
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

ROUTE_A = '/admin/api/review/submit/{form_id}'
ROUTE_B = '/admin/api/review/form/{form_id}'
ALL_STATUSES = ['待审核', '部门已审核', '中心已审核', '已驳回']


class _ReviewMutationCompatibilityBase(unittest.TestCase):
    """Shared per-test SQLite database, organization fixture, and helpers."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='review-compat-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'review-compat-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self._create_organization()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    # ---- organization fixture ----
    def _create_organization(self):
        self.permission_objects = {}
        for name in ('审表_小组', '审表_部门', '审表_中心'):
            perm = Permission(name=name, description=f'test {name}')
            db.session.add(perm)
            self.permission_objects[name] = perm
        db.session.flush()

        self.dept_a = Department(name='办公部', description='test')
        self.dept_b = Department(name='策划部', description='test')
        db.session.add_all([self.dept_a, self.dept_b])
        db.session.flush()

        self.group_a1 = Group(name='一组', department=self.dept_a.name, max_members=100)
        db.session.add(self.group_a1)
        db.session.flush()

        self.officer = self._make_user('A100', 'student-a100', '信息员', self.dept_a.name, self.group_a1)
        self.officer_b = self._make_user('B100', 'student-b100', '信息员', self.dept_b.name, None)
        self.group_admin = self._make_user(
            'G100', 'admin-g100', '管理员', self.dept_a.name, self.group_a1,
            permissions={'审表_小组'})
        self.dept_admin = self._make_user(
            'D100', 'admin-d100', '管理员', self.dept_a.name, self.group_a1,
            permissions={'审表_部门'})
        self.center_admin = self._make_user(
            'C100', 'admin-c100', '管理员', self.dept_a.name, self.group_a1,
            permissions={'审表_中心'})
        db.session.commit()

    def _make_user(self, number, student_id, role, department, group, permissions=None):
        user = User(
            number=number,
            department=department,
            name=f'User {number}',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=role,
            group=group.name if group else '待分配',
            group_id=group.id if group else None,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        for name in (permissions or set()):
            db.session.add(RolePermission(
                role=f'特殊角色_{user.id}',
                permission_id=self.permission_objects[name].id,
            ))
        return user

    def _make_form(self, owner, status='待审核', backfill_unique_id=True,
                   registration=None, course_feedback='feedback-base'):
        form = LectureForm(
            listener_name=f'{owner.name}（{owner.college}）',
            listener_number=owner.number,
            course_changes='无',
            lecture_date='2026/06/04星期四',
            class_period='第3-4节',
            lecture_location='32-302',
            teacher_name='Teacher A',
            teacher_college='Test College',
            course_title='Database Systems',
            student_grade_class='2024 Test Class',
            abnormal_situation='无',
            teaching_method='PPT演示法',
            classroom_discipline='好',
            classroom_atmosphere='好',
            courseware_quality='好',
            overall_effect='好',
            quality_case='推荐',
            course_feedback=course_feedback,
            suggestions='无',
            student_signature1='Student One',
            contact_phone1='13800000001',
            student_signature2='Student Two',
            contact_phone2='13800000002',
            status=status,
            audit_tag='需要人工审核',
            registration_id=registration.id if registration else None,
        )
        db.session.add(form)
        db.session.flush()
        if backfill_unique_id:
            form.unique_id = form.id
        db.session.commit()
        return form

    def _make_registration(self, owner, used=True):
        registration = CourseRegistration(
            course_code='C1',
            selection_code='S1',
            user_id=owner.id,
            listening_info='第1周星期一第1节',
            is_used=used,
        )
        db.session.add(registration)
        db.session.commit()
        return registration

    # ---- helpers ----
    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _full_form_data(self, form, **overrides):
        """A complete form_data payload whose values equal the original row."""
        values = {
            'listener_name': form.listener_name,
            'course_changes': form.course_changes,
            'lecture_date': form.lecture_date,
            'class_period': form.class_period,
            'lecture_location': form.lecture_location,
            'teacher_name': form.teacher_name,
            'teacher_college': form.teacher_college,
            'course_title': form.course_title,
            'student_grade_class': form.student_grade_class,
            'abnormal_situation': form.abnormal_situation,
            'teaching_method': form.teaching_method,
            'classroom_discipline': form.classroom_discipline,
            'classroom_atmosphere': form.classroom_atmosphere,
            'courseware_quality': form.courseware_quality,
            'overall_effect': form.overall_effect,
            'quality_case': form.quality_case,
            'course_feedback': form.course_feedback,
            'suggestions': form.suggestions,
            'student_signature1': form.student_signature1,
            'contact_phone1': form.contact_phone1,
            'student_signature2': form.student_signature2 or '',
            'contact_phone2': form.contact_phone2 or '',
        }
        values.update(overrides)
        return values

    def _submit_a(self, form_id, payload):
        return self.client.post(ROUTE_A.format(form_id=form_id), json=payload)

    def _submit_b(self, form_id, payload):
        return self.client.post(ROUTE_B.format(form_id=form_id), json=payload)

    def _put_draft(self, form_id, comment='draft-comment'):
        response = self.client.put(
            ROUTE_B.format(form_id=form_id) + '/draft',
            json={'data': {'form_data': {}, 'review_comment': comment, 'score_data': []}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

    def _draft_exists(self, form_id):
        response = self.client.get(ROUTE_B.format(form_id=form_id) + '/draft')
        self.assertEqual(response.status_code, 200)
        return response.get_json()['exists']

    def _logical_rows(self, form):
        db.session.expire_all()
        return form.get_all_versions()

    def _positive_score(self, reason='补充扣分', dept=2.0, pers=1.0, auto=False):
        return {
            'reason': reason,
            'department_score': dept,
            'personal_score': pers,
            'is_auto_generated': auto,
        }

    def _zero_score(self, reason='zero'):
        return {
            'reason': reason,
            'department_score': 0,
            'personal_score': 0,
            'is_auto_generated': False,
        }


class ReviewMutationAuthCompatibilityTest(_ReviewMutationCompatibilityBase):
    """Authentication / actor-liveness matrix (sections 五 / 六)."""

    def test_unauthenticated_route_a_redirects_html(self):
        """A has @login_required: 302 HTML redirect, never JSON. (UNKNOWN_POLICY)"""
        form = self._make_form(self.officer)
        response = self.client.post(ROUTE_A.format(form_id=form.id), json={})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/auth/login'))
        self.assertIn('text/html', response.content_type)
        self.assertIsNone(response.get_json(silent=True))

    def test_unauthenticated_route_b_returns_json_401(self):
        """B checks session manually: JSON 401. (UNKNOWN_POLICY vs A)"""
        form = self._make_form(self.officer)
        response = self.client.post(ROUTE_B.format(form_id=form.id), json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.content_type, 'application/json')
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['message'], '请先登录')

    def test_inactive_actor_route_a_clears_session_and_redirects(self):
        """A via login_required: session.clear() + 302 redirect. (UNKNOWN_POLICY)"""
        form = self._make_form(self.officer)
        self.dept_admin.is_active = False
        db.session.commit()
        self._login(self.dept_admin)
        response = self.client.post(ROUTE_A.format(form_id=form.id), json={})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/auth/login'))
        with self.client.session_transaction() as sess:
            self.assertNotIn('user_id', sess)

    def test_inactive_actor_route_b_returns_403_and_keeps_session(self):
        """B manual session check passes, permission lookup fails closed: 403 JSON,
        session is NOT cleared. (UNKNOWN_POLICY vs A)"""
        form = self._make_form(self.officer)
        self.dept_admin.is_active = False
        db.session.commit()
        self._login(self.dept_admin)
        response = self.client.post(ROUTE_B.format(form_id=form.id), json={})
        self.assertEqual(response.status_code, 403)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertIn('没有审核权限', body['message'])
        with self.client.session_transaction() as sess:
            self.assertIn('user_id', sess)


class ReviewMutationInputCompatibilityTest(_ReviewMutationCompatibilityBase):
    """Missing form, cross-scope, envelope, and identity-freeze matrix."""

    def test_missing_form_route_a_returns_json_200_failure(self):
        """A filter_by().first(): missing form -> HTTP 200 success=False.
        (LIKELY_DEFECT contract, frozen by no existing test; characterize only)"""
        self._login(self.dept_admin)
        response = self._submit_a(999999, {'form_data': {}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, 'application/json')
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['message'], '表单不存在')

    def test_missing_form_route_b_returns_json_500_from_caught_404(self):
        """B get_or_404() raises NotFound INSIDE the route try-block, so the
        outer except converts a client 404 into HTTP 500 JSON. Not the default
        HTML 404 page. (LIKELY_DEFECT contract, characterize only)"""
        self._login(self.dept_admin)
        response = self._submit_b(999999, {'form_data': {}})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type, 'application/json')
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertIn('404', body['message'])

    def test_cross_scope_route_b_forbidden_without_mutation(self):
        """B object-level authorization fail-closed like A.
        A side is locked by test_security_authorization
        ::test_submit_review_cross_department_forbidden. (COMMON_INVARIANT)"""
        form = self._make_form(self.officer_b)
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {'form_data': {}, 'review_comment': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['success'])
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, '待审核')
        self.assertEqual(ScoreRecord.query.count(), 0)

    def test_unknown_owner_route_b_fail_closed(self):
        """B with deleted owner user: denied like A
        (A locked by test_submit_review_unknown_owner_forbidden). (COMMON_INVARIANT)"""
        form = self._make_form(self.officer)
        db.session.delete(db.session.get(User, self.officer.id))
        db.session.commit()
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {'form_data': {}, 'review_comment': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self._logical_rows(form)), 1)

    def test_inactive_owner_route_b_fail_closed(self):
        """B with is_active=False owner: denied (unknown/inactive owner => deny)."""
        form = self._make_form(self.officer)
        self.officer.is_active = False
        db.session.commit()
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {'form_data': {}, 'review_comment': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self._logical_rows(form)), 1)

    # ---- request envelope ----
    def test_route_a_accepts_form_urlencoded_with_hidden_score_json(self):
        """A legacy form mode: full field set + score_data hidden JSON string.
        (FROZEN_COMPAT semantics, success path not previously locked)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        fields = self._full_form_data(form)
        fields['review_comment'] = 'form 通过'
        fields['score_data'] = json.dumps([self._positive_score()])
        response = self.client.post(ROUTE_A.format(form_id=form.id), data=fields)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        new_form = db.session.get(LectureForm, body['form_id'])
        record = ScoreRecord.query.filter_by(form_id=new_form.id).one()
        self.assertEqual(record.total_department_score, 2.0)
        self.assertEqual(len(record.items), 1)

    def test_route_a_form_urlencoded_empty_score_data_means_no_scores(self):
        """A legacy form mode with score_data='' equals no scores.
        (FROZEN_COMPAT, see review.py empty score_data branch)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        fields = self._full_form_data(form)
        fields['review_comment'] = 'ok'
        fields['score_data'] = ''
        response = self.client.post(ROUTE_A.format(form_id=form.id), data=fields)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        new_form = db.session.get(LectureForm, response.get_json()['form_id'])
        self.assertIsNone(ScoreRecord.query.filter_by(form_id=new_form.id).first())

    def test_route_b_rejects_form_urlencoded_400(self):
        """B is JSON-only: form-urlencoded -> 400 '请求数据格式错误', no mutation.
        (UNKNOWN_POLICY vs A's dual-protocol support)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self.client.post(
            ROUTE_B.format(form_id=form.id),
            data=self._full_form_data(form),
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(len(self._logical_rows(form)), 1)

    def test_both_routes_reject_non_dict_form_data_400(self):
        """form_data not an object -> 400 on both routes before any mutation.
        (COMMON_INVARIANT)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        for submit in (self._submit_a, self._submit_b):
            response = submit(form.id, {'form_data': ['not-a-dict'], 'review_comment': 'x'})
            self.assertEqual(response.status_code, 400)
            self.assertIn('请求数据格式错误', response.get_json()['message'])
        self.assertEqual(len(self._logical_rows(form)), 1)
        self.assertEqual(ScoreRecord.query.count(), 0)

    def test_both_routes_reject_non_list_score_data_400(self):
        """score_data not an array -> ScoreValidationError -> 400 on both routes,
        before any DB mutation. (COMMON_INVARIANT)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response_a = self._submit_a(form.id, {
            'form_data': self._full_form_data(form),
            'review_comment': 'x',
            'score_data': {'reason': 'not-a-list'},
        })
        self.assertEqual(response_a.status_code, 400)
        self.assertIn('必须是数组', response_a.get_json()['message'])
        response_b = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'x',
            'score_data': {'reason': 'not-a-list'},
        })
        self.assertEqual(response_b.status_code, 400)
        self.assertIn('必须是数组', response_b.get_json()['message'])
        self.assertEqual(len(self._logical_rows(form)), 1)
        self.assertEqual(ScoreRecord.query.count(), 0)

    # ---- identity freeze ----
    def test_client_cannot_rewrite_listener_or_registration_both_routes(self):
        """listener_number / registration_id must survive a client rewrite
        attempt on both routes. A-listener locked by test_security_authorization
        ::test_submit_review_same_department_success_and_freezes_listener_number;
        A/B registration inheritance locked by test_form_version_semantics.
        This adds the explicit client-rewrite attempt for both routes, each on
        its own fresh form. (COMMON_INVARIANT)"""
        self._login(self.dept_admin)

        registration_a = self._make_registration(self.officer)
        other_registration = self._make_registration(self.officer_b)
        form_a = self._make_form(self.officer, registration=registration_a)
        payload_a = {
            'form_data': self._full_form_data(
                form_a,
                listener_number=self.officer_b.number,
                registration_id=str(other_registration.id),
            ),
            'review_comment': 'x',
            'score_data': [],
        }
        response_a = self._submit_a(form_a.id, payload_a)
        self.assertEqual(response_a.status_code, 200)
        self.assertTrue(response_a.get_json()['success'])
        new_form = db.session.get(LectureForm, response_a.get_json()['form_id'])
        self.assertEqual(new_form.listener_number, self.officer.number)
        self.assertEqual(new_form.registration_id, registration_a.id)

        registration_b = self._make_registration(self.officer)
        form_b = self._make_form(self.officer, registration=registration_b)
        payload_b = {
            'form_data': {
                'listener_number': self.officer_b.number,
                'registration_id': str(other_registration.id),
            },
            'review_comment': 'x',
            'score_data': [],
        }
        response_b = self._submit_b(form_b.id, payload_b)
        self.assertEqual(response_b.status_code, 200)
        latest = form_b.get_latest_version()
        self.assertNotEqual(latest.id, form_b.id)
        self.assertEqual(latest.listener_number, self.officer.number)
        self.assertEqual(latest.registration_id, registration_b.id)


class ReviewMutationVersionCompatibilityTest(_ReviewMutationCompatibilityBase):
    """Physical version strategy matrix (sections 十七 / 十八 / 十九)."""

    def test_legacy_null_unique_id_base_route_a_keeps_base_null(self):
        """A first review of a legacy base (unique_id=NULL) creates the new
        version with unique_id=base.id but does NOT backfill base.unique_id.
        B's backfill behavior is locked by test_form_version_semantics
        ::test_submit_form_review_unique_id_null_legacy_behavior.
        (UNKNOWN_POLICY: base-row backfill asymmetry)"""
        form = self._make_form(self.officer, backfill_unique_id=False)
        self._login(self.dept_admin)
        response = self._submit_a(form.id, {
            'form_data': self._full_form_data(form),
            'review_comment': 'ok',
            'score_data': [],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        db.session.expire_all()
        base = db.session.get(LectureForm, form.id)
        self.assertIsNone(base.unique_id)
        self.assertEqual(base.status, '待审核')
        v2_id = response.get_json()['form_id']
        rows = base.get_all_versions()
        self.assertEqual([row.id for row in rows], [v2_id, form.id])
        self.assertEqual(rows[0].unique_id, form.id)
        self.assertEqual(rows[0].status, '部门已审核')

    def test_two_stage_version_history_route_a_creates_row_per_stage(self):
        """ROUTE A VERSION HISTORY: dept stage creates v2, center stage creates
        v3 (latest.status != new_status -> always create). Base row keeps its
        old status forever. (UNKNOWN_POLICY: VERSION_MUTATION_DIVERGENCE)"""
        form = self._make_form(self.officer)

        self._login(self.dept_admin)
        stage1 = self._submit_a(form.id, {
            'form_data': self._full_form_data(form),
            'review_comment': 'dept pass',
            'score_data': [self._positive_score(reason='stage1')],
        })
        self.assertEqual(stage1.status_code, 200)
        self.assertTrue(stage1.get_json()['success'])
        v2_id = stage1.get_json()['form_id']

        self._login(self.center_admin)
        v2 = db.session.get(LectureForm, v2_id)
        stage2 = self._submit_a(v2_id, {
            'form_data': self._full_form_data(v2, course_feedback='feedback-stage2'),
            'review_comment': 'center pass',
            'score_data': [self._positive_score(reason='stage2', dept=3.0, pers=2.0)],
        })
        self.assertEqual(stage2.status_code, 200)
        self.assertTrue(stage2.get_json()['success'])
        v3_id = stage2.get_json()['form_id']

        db.session.expire_all()
        base = db.session.get(LectureForm, form.id)
        rows = base.get_all_versions()
        self.assertEqual(len(rows), 3)
        v3, v2_after, base_after = rows
        self.assertEqual(base_after.id, form.id)
        self.assertEqual(base_after.status, '待审核')
        self.assertEqual(v2_after.id, v2_id)
        self.assertEqual(v2_after.status, '部门已审核')
        self.assertEqual(v2_after.course_feedback, 'feedback-base')
        self.assertEqual(v3.id, v3_id)
        self.assertEqual(v3.status, '中心已审核')
        self.assertEqual(v3.course_feedback, 'feedback-stage2')
        logical_id = form.id
        for row in rows:
            self.assertEqual(row.unique_id, logical_id)
        self.assertIs(base.get_latest_version(), v3)

    def test_two_stage_version_history_route_b_updates_latest_in_place(self):
        """ROUTE B VERSION HISTORY: dept stage creates v2; center stage matches
        `latest.id != unique_id` and mutates v2 IN PLACE -> only 2 physical rows
        for the whole lifecycle. Stage 2 must carry no score_data: with scores
        B always fails (see the dedicated rescore test below).
        (UNKNOWN_POLICY: VERSION_MUTATION_DIVERGENCE)"""
        form = self._make_form(self.officer)

        self._login(self.dept_admin)
        stage1 = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'dept pass',
            'score_data': [],
        })
        self.assertEqual(stage1.status_code, 200)
        self.assertTrue(stage1.get_json()['success'])
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 2)
        v2_id = rows[0].id

        self._login(self.center_admin)
        stage2 = self._submit_b(v2_id, {
            'form_data': {'course_feedback': 'feedback-stage2'},
            'review_comment': 'center pass',
            'score_data': [],
        })
        self.assertEqual(stage2.status_code, 200)
        body = stage2.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['new_status'], '中心已审核')
        self.assertIn('课程反馈', body['modified_fields'])

        db.session.expire_all()
        base = db.session.get(LectureForm, form.id)
        rows = base.get_all_versions()
        self.assertEqual(len(rows), 2)
        v2_after, base_after = rows
        self.assertEqual(base_after.id, form.id)
        self.assertEqual(base_after.status, '待审核')
        self.assertEqual(base_after.unique_id, form.id)
        self.assertEqual(v2_after.id, v2_id)
        self.assertEqual(v2_after.status, '中心已审核')
        self.assertEqual(v2_after.course_feedback, 'feedback-stage2')
        self.assertEqual(v2_after.unique_id, form.id)
        self.assertIs(base.get_latest_version(), v2_after)

    def test_route_b_stage2_replacement_score_succeeds(self):
        """POST_FIX (8B-P1 LD-3; EXPECTED_DEFECT_CONTRACT_CHANGE from the P0
        test_route_b_stage2_rescore_hits_unique_constraint_and_rolls_back):
        stage1 B creates a ScoreRecord (dept=2/pers=1, reason=stage1); stage2
        center re-review on the SAME physical v2 with a new score (dept=3/
        pers=2, reason=stage2) now succeeds: the old record's DELETE is flushed
        before the replacement INSERT, so UNIQUE(form_id) is released first.
        Result: HTTP 200, 中心已审核, same physical form, exactly 1 ScoreRecord
        with new totals, old reason absent, old items removed.
        (ROUTE_B_SCORE_RECORD_REPLACEMENT_SUCCEEDS)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        stage1 = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'dept pass',
            'score_data': [self._positive_score(reason='stage1')],
        })
        self.assertEqual(stage1.status_code, 200)
        v2_id = form.get_latest_version().id
        self.assertEqual(ScoreRecord.query.filter_by(form_id=v2_id).count(), 1)

        self._login(self.center_admin)
        stage2 = self._submit_b(v2_id, {
            'form_data': {'course_feedback': 'feedback-stage2'},
            'review_comment': 'center pass',
            'score_data': [self._positive_score(reason='stage2', dept=3.0, pers=2.0)],
        })
        self.assertEqual(stage2.status_code, 200)
        body = stage2.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['new_status'], '中心已审核')

        db.session.expire_all()
        base = db.session.get(LectureForm, form.id)
        rows = base.get_all_versions()
        self.assertEqual(len(rows), 2)
        v2_after, base_after = rows
        self.assertEqual(base_after.id, form.id)
        self.assertEqual(base_after.status, '待审核')
        self.assertEqual(v2_after.id, v2_id)
        self.assertEqual(v2_after.status, '中心已审核')
        self.assertEqual(v2_after.course_feedback, 'feedback-stage2')
        records = ScoreRecord.query.filter_by(form_id=v2_id).all()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.total_department_score, 3.0)
        self.assertEqual(record.total_personal_score, 2.0)
        reasons = [item.reason for item in record.items]
        self.assertEqual(reasons, ['stage2'])
        self.assertNotIn('stage1', reasons)

    def test_same_status_update_branch_unreachable_via_normal_flow(self):
        """Both routes' `latest.status == new_status` in-place branch can never
        fire through the normal HTTP flow: can_review_status admits exactly one
        input status per level and get_next_status_after_review always returns
        a different one. B's other disjunct (latest.id != unique_id) IS
        reachable and proven by the two-stage tests. (IMPLEMENTATION_ONLY)"""
        for user in (self.group_admin, self.dept_admin, self.center_admin):
            accepted = [s for s in ALL_STATUSES if can_review_status(user.id, s)]
            self.assertEqual(len(accepted), 1)
            self.assertNotIn(get_next_status_after_review(user.id), accepted)


class ReviewMutationScoreCompatibilityTest(_ReviewMutationCompatibilityBase):
    """Score persistence divergence matrix (sections 二十 / 二十一 / 二十二)."""

    def test_zero_score_item_route_a_keeps_item(self):
        """A: all-zero score item -> ScoreRecord AND ScoreItem(count=1).
        (UNKNOWN_POLICY: zero-score retention)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_a(form.id, {
            'form_data': self._full_form_data(form),
            'review_comment': 'x',
            'score_data': [self._zero_score()],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        new_form = db.session.get(LectureForm, response.get_json()['form_id'])
        record = ScoreRecord.query.filter_by(form_id=new_form.id).one()
        self.assertEqual(record.total_department_score, 0.0)
        self.assertEqual(record.total_personal_score, 0.0)
        items = list(record.items)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].reason, 'zero')
        self.assertFalse(items[0].is_auto_generated)

    def test_zero_score_item_route_b_drops_item(self):
        """B: all-zero score item -> ScoreRecord exists with totals 0 but
        ScoreItem(count=0): the item is silently dropped. (UNKNOWN_POLICY:
        zero-score retention, divergence from A)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'x',
            'score_data': [self._zero_score()],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        new_form = form.get_latest_version()
        record = ScoreRecord.query.filter_by(form_id=new_form.id).one()
        self.assertEqual(record.total_department_score, 0.0)
        self.assertEqual(record.total_personal_score, 0.0)
        self.assertEqual(len(record.items), 0)

    def test_mixed_zero_and_positive_scores_route_a(self):
        """A: zero + positive items -> 2 items, both reasons preserved,
        totals include only the positive contribution. (COMMON_INVARIANT for
        normalize_score_items output; persistence is A-specific)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_a(form.id, {
            'form_data': self._full_form_data(form),
            'review_comment': 'x',
            'score_data': [
                self._zero_score(),
                self._positive_score(reason='positive', dept=3.0, pers=1.0, auto=True),
            ],
        })
        self.assertEqual(response.status_code, 200)
        new_form = db.session.get(LectureForm, response.get_json()['form_id'])
        record = ScoreRecord.query.filter_by(form_id=new_form.id).one()
        self.assertEqual(record.total_department_score, 3.0)
        self.assertEqual(record.total_personal_score, 1.0)
        items = {item.reason: item for item in record.items}
        self.assertEqual(set(items), {'zero', 'positive'})
        self.assertTrue(items['positive'].is_auto_generated)
        self.assertFalse(items['zero'].is_auto_generated)

    def test_mixed_zero_and_positive_scores_route_b(self):
        """B: zero + positive items -> only the positive item is persisted; the
        zero item's reason disappears entirely while totals stay identical to
        A for the same payload. (UNKNOWN_POLICY)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'x',
            'score_data': [
                self._zero_score(),
                self._positive_score(reason='positive', dept=3.0, pers=1.0, auto=True),
            ],
        })
        self.assertEqual(response.status_code, 200)
        new_form = form.get_latest_version()
        record = ScoreRecord.query.filter_by(form_id=new_form.id).one()
        self.assertEqual(record.total_department_score, 3.0)
        self.assertEqual(record.total_personal_score, 1.0)
        items = list(record.items)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].reason, 'positive')
        self.assertTrue(items[0].is_auto_generated)

    def test_route_a_never_deletes_previous_stage_score_record(self):
        """A stage 2 (create branch) never touches v2's ScoreRecord: each
        physical row keeps its own record, old rows are not cleaned up.
        (UNKNOWN_POLICY: ScoreRecord replacement asymmetry)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        stage1 = self._submit_a(form.id, {
            'form_data': self._full_form_data(form),
            'review_comment': 'dept',
            'score_data': [self._positive_score(reason='stage1')],
        })
        v2_id = stage1.get_json()['form_id']
        self._login(self.center_admin)
        v2 = db.session.get(LectureForm, v2_id)
        stage2 = self._submit_a(v2_id, {
            'form_data': self._full_form_data(v2),
            'review_comment': 'center',
            'score_data': [],
        })
        self.assertEqual(stage2.status_code, 200)
        v3_id = stage2.get_json()['form_id']
        self.assertEqual(ScoreRecord.query.filter_by(form_id=v2_id).count(), 1)
        self.assertEqual(ScoreRecord.query.filter_by(form_id=v3_id).count(), 0)

    def test_route_b_deletes_stage1_score_record_when_stage2_has_no_scores(self):
        """B stage 2 (in-place update) deletes the old ScoreRecord first; if the
        new request carries no score_data the record is gone entirely instead
        of being carried over. (UNKNOWN_POLICY: ScoreRecord replacement)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'dept',
            'score_data': [self._positive_score(reason='stage1')],
        })
        v2_id = form.get_latest_version().id
        self.assertEqual(ScoreRecord.query.filter_by(form_id=v2_id).count(), 1)
        self._login(self.center_admin)
        stage2 = self._submit_b(v2_id, {'form_data': {}, 'review_comment': 'center'})
        self.assertEqual(stage2.status_code, 200)
        self.assertEqual(ScoreRecord.query.filter_by(form_id=v2_id).count(), 0)
        self.assertEqual(ScoreRecord.query.count(), 0)


class ReviewMutationTransactionCompatibilityTest(_ReviewMutationCompatibilityBase):
    """Failure atomicity + draft transaction matrix (sections 二十三-二十六).

    POST_FIX (8B-P1): score persistence is transactional on both routes and
    genuine A server failures answer HTTP 500.  The A-side tests below were
    converted from their PRE_FIX defect contracts; fault injection stays
    pure-Python, raised AFTER input validation passed and AFTER ScoreRecord
    entered the transaction, so a passing test proves real commit/rollback
    behavior rather than a poisoned session.
    """

    def _prepare_with_draft(self, form):
        self._login(self.dept_admin)
        self._put_draft(form.id)
        self.assertTrue(self._draft_exists(form.id))

    def test_score_item_fault_route_a_rolls_back_http_500(self):
        """POST_FIX (8B-P1 LD-1/LD-2; EXPECTED_DEFECT_CONTRACT_CHANGE from the
        P0 test_score_item_fault_route_a_partial_commit_http_200): ScoreItem
        construction fails AFTER ScoreRecord was flushed — the exception now
        propagates to the outer except, the whole review rolls back, and the
        route answers HTTP 500 success=False with no version, no ScoreRecord,
        no ScoreItems, and the draft preserved."""
        form = self._make_form(self.officer)
        self._prepare_with_draft(form)
        payload = {
            'form_data': self._full_form_data(form),
            'review_comment': 'x',
            'score_data': [self._positive_score()],
        }
        with mock.patch(
            'app.services.review_application.ScoreItem',
            side_effect=RuntimeError('score item fault'),
        ):
            response = self._submit_a(form.id, payload)
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertTrue(body['message'].startswith('审核提交失败：score item fault'))
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, '待审核')
        self.assertEqual(ScoreRecord.query.filter_by(form_id=rows[0].id).count(), 0)
        self.assertEqual(
            ScoreItem.query.join(ScoreRecord).filter(ScoreRecord.form_id == rows[0].id).count(),
            0,
        )
        self.assertTrue(self._draft_exists(form.id))

    def test_score_item_fault_route_b_full_rollback_http_500(self):
        """B side of the same fault: no inner catch -> outer except -> rollback
        -> HTTP 500 success=False, no new version, no ScoreRecord, draft
        preserved. (LIKELY_DEFECT divergence vs A)"""
        form = self._make_form(self.officer)
        self._prepare_with_draft(form)
        payload = {
            'form_data': {},
            'review_comment': 'x',
            'score_data': [self._positive_score()],
        }
        with mock.patch(
            'app.blueprints.admin.review.ScoreItem',
            side_effect=RuntimeError('score item fault'),
        ):
            response = self._submit_b(form.id, payload)
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['message'], 'score item fault')
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, '待审核')
        self.assertEqual(ScoreRecord.query.count(), 0)
        self.assertTrue(self._draft_exists(form.id))

    def test_commit_fault_route_a_rolls_back_http_500(self):
        """POST_FIX (8B-P1 LD-2; EXPECTED_DEFECT_CONTRACT_CHANGE from the P0
        test_commit_fault_route_a_http_200_and_nothing_persisted): a commit
        failure after everything was flushed is a genuine server failure and
        answers HTTP 500 success=False. Rollback leaves zero mutations and the
        draft intact."""
        form = self._make_form(self.officer)
        self._prepare_with_draft(form)
        payload = {
            'form_data': self._full_form_data(form),
            'review_comment': 'x',
            'score_data': [],
        }
        with mock.patch(
            'sqlalchemy.orm.Session.commit',
            side_effect=RuntimeError('commit fault'),
        ):
            response = self._submit_a(form.id, payload)
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertTrue(body['message'].startswith('审核提交失败：commit fault'))
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, '待审核')
        self.assertEqual(ScoreRecord.query.count(), 0)
        self.assertTrue(self._draft_exists(form.id))

    def test_commit_fault_route_b_http_500_and_nothing_persisted(self):
        """B side: commit fault -> HTTP 500 JSON, rollback, draft preserved."""
        form = self._make_form(self.officer)
        self._prepare_with_draft(form)
        with mock.patch(
            'sqlalchemy.orm.Session.commit',
            side_effect=RuntimeError('commit fault'),
        ):
            response = self._submit_b(form.id, {'form_data': {}, 'review_comment': 'x'})
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['message'], 'commit fault')
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 1)
        self.assertEqual(ScoreRecord.query.count(), 0)
        self.assertTrue(self._draft_exists(form.id))

    def test_draft_delete_fault_route_a_rolls_back_http_500(self):
        """POST_FIX (8B-P1 LD-2; EXPECTED_DEFECT_CONTRACT_CHANGE from the P0
        test_draft_delete_fault_route_a_http_200_no_mutation): an unexpected
        exception at the draft-mutation point is a genuine server failure ->
        HTTP 500 success=False, zero mutations, draft preserved."""
        form = self._make_form(self.officer)
        self._prepare_with_draft(form)
        payload = {
            'form_data': self._full_form_data(form),
            'review_comment': 'x',
            'score_data': [],
        }
        with mock.patch(
            'app.services.review_application.delete_review_form_draft',
            side_effect=RuntimeError('draft fault'),
        ):
            response = self._submit_a(form.id, payload)
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertTrue(body['message'].startswith('审核提交失败：draft fault'))
        self.assertEqual(len(self._logical_rows(form)), 1)
        self.assertEqual(ScoreRecord.query.count(), 0)
        self.assertTrue(self._draft_exists(form.id))

    def test_draft_delete_fault_route_b_http_500_no_mutation(self):
        """Outer-exception contract at the draft-mutation point (B): HTTP 500
        success=False, zero mutations, draft preserved."""
        form = self._make_form(self.officer)
        self._prepare_with_draft(form)
        with mock.patch(
            'app.blueprints.admin.review.delete_review_form_draft',
            side_effect=RuntimeError('draft fault'),
        ):
            response = self._submit_b(form.id, {'form_data': {}, 'review_comment': 'x'})
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['message'], 'draft fault')
        self.assertEqual(len(self._logical_rows(form)), 1)
        self.assertTrue(self._draft_exists(form.id))

    def test_successful_review_route_b_removes_draft(self):
        """COMMON_INVARIANT: successful B review removes the reviewer's draft.
        A side locked by test_review_form_draft
        ::test_submit_review_clears_current_review_draft."""
        form = self._make_form(self.officer)
        self._prepare_with_draft(form)
        response = self._submit_b(form.id, {'form_data': {}, 'review_comment': 'x'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertFalse(self._draft_exists(form.id))


class ReviewMutationResponseCompatibilityTest(_ReviewMutationCompatibilityBase):
    """Success/error response shape, comment defaults, field-fallback semantics."""

    def test_route_a_success_response_shape(self):
        """A: {success, message, new_status, form_id}. Adapter shape that a
        future shared core must not drift. (FROZEN_COMPAT shape)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_a(form.id, {
            'form_data': self._full_form_data(form),
            'review_comment': 'ok',
            'score_data': [],
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(set(body), {'success', 'message', 'new_status', 'form_id'})
        self.assertEqual(body['message'], '审核提交成功')
        self.assertEqual(body['new_status'], '部门已审核')
        self.assertIsInstance(body['form_id'], int)

    def test_route_b_success_response_shape(self):
        """B: {success, message, new_status, modified_fields} — no form_id.
        (FROZEN_COMPAT shape)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {'form_data': {}, 'review_comment': 'ok'})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(set(body), {'success', 'message', 'new_status', 'modified_fields'})
        self.assertEqual(body['message'], '表单审核完成，状态已更新为"部门已审核"')
        self.assertEqual(body['new_status'], '部门已审核')
        self.assertEqual(body['modified_fields'], [])

    def test_partial_empty_form_data_route_a_fails_without_mutation(self):
        """LD-6 substance still DEFERRED (8B-P1): omitted fields become None,
        hit NOT NULL at flush, and surface as a server-style failure with zero
        mutation — the input-validation gap is not fixed here.  The envelope
        changed from HTTP 200 to HTTP 500 as a mechanical LD-2 ripple (this
        path exits through A's outer except); the deferred part is the missing
        proper 400 validation, not the status code.
        (EXPECTED_DEFECT_CONTRACT_CHANGE: envelope only)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_a(form.id, {'form_data': {}, 'review_comment': ''})
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertTrue(body['message'].startswith('审核提交失败：'))
        self.assertIn('NOT NULL constraint failed', body['message'])
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, '待审核')
        self.assertEqual(ScoreRecord.query.count(), 0)

    def test_partial_empty_form_data_route_b_patch_keeps_originals(self):
        """B side: omitted fields fall back to the original row (PATCH
        semantics) and the review succeeds. (UNKNOWN_POLICY vs A's
        replace-with-None failure)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {'form_data': {}, 'review_comment': ''})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['modified_fields'], [])
        new_form = form.get_latest_version()
        for field in ('listener_name', 'lecture_date', 'class_period',
                      'teacher_name', 'course_title', 'course_feedback'):
            self.assertEqual(getattr(new_form, field), getattr(form, field))

    def test_review_comment_default_route_a_is_wu(self):
        """A: omitted review_comment -> final '无' when nothing was modified."""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_a(form.id, {
            'form_data': self._full_form_data(form),
            'score_data': [],
        })
        self.assertEqual(response.status_code, 200)
        new_form = db.session.get(LectureForm, response.get_json()['form_id'])
        self.assertEqual(new_form.review_comment, '无')

    def test_review_comment_default_route_a_with_modification_appends_note(self):
        """A: omitted comment + one modified field -> '无' + system note."""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_a(form.id, {
            'form_data': self._full_form_data(form, course_feedback='changed'),
            'score_data': [],
        })
        self.assertEqual(response.status_code, 200)
        new_form = db.session.get(LectureForm, response.get_json()['form_id'])
        self.assertEqual(
            new_form.review_comment,
            '无\n\n[系统记录] 审核人修改了以下字段：课程反馈',
        )

    def test_review_comment_default_route_b_is_empty_string(self):
        """B: omitted review_comment + no modified fields -> final ''.
        (UNKNOWN_POLICY vs A's '无')"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {'form_data': {}})
        self.assertEqual(response.status_code, 200)
        new_form = form.get_latest_version()
        self.assertEqual(new_form.review_comment, '')

    def test_review_comment_default_route_b_with_modification_uses_wu_base(self):
        """B: omitted comment + one modified field -> note base becomes '无'."""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        response = self._submit_b(form.id, {
            'form_data': {'course_feedback': 'changed'},
        })
        self.assertEqual(response.status_code, 200)
        new_form = form.get_latest_version()
        self.assertEqual(
            new_form.review_comment,
            '无\n\n[系统记录] 审核人修改了以下字段：课程反馈',
        )
        self.assertIn('课程反馈', response.get_json()['modified_fields'])

    def test_route_b_lecture_adapter_fields_route_a_does_not_understand(self):
        """Section 十四 adapter divergence: the same semantic request using
        lecture_date_display/start_period/end_period INSTEAD OF raw
        lecture_date/class_period succeeds on B with computed values, but on A
        the raw fields resolve to None -> NOT NULL -> HTTP 200 wrapped failure
        with zero mutation. (UNKNOWN_POLICY: adapter difference)"""
        form = self._make_form(self.officer)
        adapter_data = {
            'lecture_date_display': '2026/06/05',
            'start_period': '5',
            'end_period': '6',
        }
        raw_without_dates = self._full_form_data(form)
        del raw_without_dates['lecture_date']
        del raw_without_dates['class_period']
        raw_without_dates.update(adapter_data)
        self._login(self.dept_admin)

        response_a = self._submit_a(form.id, {
            'form_data': raw_without_dates,
            'review_comment': 'x',
            'score_data': [],
        })
        # LD-2 ripple (8B-P1): A's outer-except envelope is now 500; the
        # deferred substance is unchanged — A does not understand the adapter
        # fields and fails with zero mutation instead of validating.
        self.assertEqual(response_a.status_code, 500)
        self.assertFalse(response_a.get_json()['success'])
        self.assertEqual(len(self._logical_rows(form)), 1)

        response_b = self._submit_b(form.id, {
            'form_data': dict(adapter_data),
            'review_comment': 'x',
            'score_data': [],
        })
        self.assertEqual(response_b.status_code, 200)
        self.assertTrue(response_b.get_json()['success'])
        new_form = form.get_latest_version()
        self.assertEqual(new_form.lecture_date, '2026/06/05')
        self.assertEqual(new_form.class_period, '第5-6节')
        modified = response_b.get_json()['modified_fields']
        self.assertIn('听课时间', modified)
        self.assertIn('第几节', modified)


if __name__ == '__main__':
    unittest.main()
