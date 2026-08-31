# -*- coding: utf-8 -*-
"""Round 7B profile-stats regressions.

Single authoritative implementation in ``app/services/profile_stats.py``:
- teaching weeks are ``Optional[int]`` (no -1/0 sentinel, no out-of-term collision);
- the percentile pool contains only real evaluation samples;
- zero-deduction evaluated users are distinct from unscored users;
- user profile route / template and admin user detail consume one snapshot.
"""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'profile_stats_test.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.models import LectureForm, ScoreRecord, SystemSetting, User, db
from app.services.profile_stats import (
    _relative_deduction_percentile,
    build_user_profile_stats,
)
from app.blueprints.admin.users import _build_user_profile_stats
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

# 学期：2026-09-07 开始，共 20 周（约至 2027-01-24）。
FIRST_WEEK_MONDAY = '2026-09-07'
WEEK1_MONDAY = datetime(2026, 9, 7, 12, 0, 0)
BEFORE_TERM = datetime(2026, 9, 1, 12, 0, 0)
AFTER_TERM = datetime(2027, 1, 25, 12, 0, 0)

PARITY_FIELDS = (
    'current_week', 'current_week_label',
    'week_submitted', 'week_approved', 'week_reward',
    'total_submitted', 'total_approved', 'total_reward',
    'average_feedback_chars', 'total_feedback_chars',
    'days_since_last', 'total_deduction', 'deduction_display',
    'has_evaluation_sample', 'percentile', 'rating_label', 'rating_band',
    'assessment_items',
)


def _frozen_datetime(target):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(target.year, target.month, target.day, target.hour, target.minute, target.second)

    return FrozenDateTime


class ProfileStatsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='profile-stats-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'profile-stats-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()

        SystemSetting.set('teaching_first_week_monday', FIRST_WEEK_MONDAY)
        SystemSetting.set('teaching_week_start_day', '0')
        SystemSetting.set('teaching_total_weeks', '20')
        SystemSetting.set('teaching_required_submission', '1')
        db.session.commit()

        self.user = self._make_user('U001', 'user001')

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _make_user(self, number, student_id, role='信息员'):
        user = User(
            number=number,
            department='办公部',
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
            group='一组',
            group_id=None,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _make_form(self, user, lecture_date, audit_tag=''):
        form = LectureForm(
            listener_name=f'{user.name}（{user.college}）',
            listener_number=user.number,
            lecture_date=lecture_date,
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
            course_feedback='反馈内容',
            suggestions='建议',
            student_signature1='sig',
            contact_phone1='123',
            audit_tag=audit_tag,
        )
        db.session.add(form)
        db.session.commit()
        return form

    def _evaluate(self, form, total_personal_score):
        record = ScoreRecord(form_id=form.id, total_personal_score=total_personal_score)
        db.session.add(record)
        db.session.commit()
        return record

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _render_profile(self, user, now):
        self._login(user)
        with patch('app.blueprints.user.profile.datetime', _frozen_datetime(now)):
            return self.client.get('/user/profile')

    # ---- A. after-term ----

    def test_a_after_term_current_week_is_none_with_canonical_label(self):
        stats = build_user_profile_stats(self.user, now=AFTER_TERM)
        self.assertIsNone(stats['current_week'])
        self.assertEqual(stats['current_week_label'], '当前不在教学周内')

        response = self._render_profile(self.user, AFTER_TERM)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('当前不在教学周内', html)
        self.assertNotIn('第 -1 周', html)
        self.assertNotIn('第 None 周', html)

    # ---- B. before-term ----

    def test_b_before_term_uses_before_term_label(self):
        stats = build_user_profile_stats(self.user, now=BEFORE_TERM)
        self.assertIsNone(stats['current_week'])
        self.assertEqual(stats['current_week_label'], '本学期教学周尚未开始')

        response = self._render_profile(self.user, BEFORE_TERM)
        self.assertEqual(response.status_code, 200)
        self.assertIn('本学期教学周尚未开始', response.get_data(as_text=True))

    # ---- C. non-teaching form collision ----

    def test_c_non_teaching_form_does_not_collide_into_current_week(self):
        # 当前日期与表单听课日期都不属于任何教学周：绝不能算进"本周"。
        self._make_form(self.user, '2027-03-10')

        stats = build_user_profile_stats(self.user, now=AFTER_TERM)
        self.assertIsNone(stats['current_week'])
        self.assertEqual(stats['total_submitted'], 1)
        self.assertEqual(stats['week_submitted'], 0)
        self.assertEqual(stats['week_approved'], 0)
        self.assertEqual(stats['week_reward'], 0)

    # ---- D. no evaluation ----

    def test_d_user_without_evaluation_has_no_rating(self):
        stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        self.assertFalse(stats['has_evaluation_sample'])
        self.assertIsNone(stats['percentile'])
        self.assertEqual(stats['rating_label'], '暂无评级')
        self.assertEqual(stats['rating_band'], 'none')

        response = self._render_profile(self.user, WEEK1_MONDAY)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('暂无评级', html)
        self.assertNotIn('需要继续努力', html)
        self.assertNotIn('表现良好', html)
        self.assertNotIn('行为很好', html)

    # ---- E. evaluated zero deduction is a real sample ----

    def test_e_zero_deduction_evaluation_is_valid_sample(self):
        form = self._make_form(self.user, '2026-09-07')
        self._evaluate(form, 0)

        stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        self.assertTrue(stats['has_evaluation_sample'])
        self.assertIsNotNone(stats['percentile'])
        self.assertEqual(stats['percentile'], 1.0)
        self.assertNotEqual(stats['rating_label'], '暂无评级')
        self.assertEqual(stats['rating_band'], 'excellent')
        self.assertEqual(stats['rating_label'], '行为很好')

    # ---- F. unscored users do not dilute the percentile pool ----

    def test_f_unscored_users_do_not_dilute_percentile_pool(self):
        evaluated_a = self._make_user('U002', 'user002')
        user_form = self._make_form(self.user, '2026-09-07')
        a_form = self._make_form(evaluated_a, '2026-09-08')
        self._evaluate(user_form, 5)
        self._evaluate(a_form, 10)
        for index in range(8):
            self._make_user(f'U1{index}', f'unscored{index}')

        user_stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        a_stats = build_user_profile_stats(evaluated_a, now=WEEK1_MONDAY)

        # Ranking pool must be [10, 5] — denominator 2, not 10.
        self.assertTrue(user_stats['has_evaluation_sample'])
        self.assertEqual(user_stats['percentile'], 1.0)
        self.assertTrue(a_stats['has_evaluation_sample'])
        self.assertEqual(a_stats['percentile'], 0.5)

    # ---- G/H. legacy late & explicit week correction semantics preserved ----

    def test_g_legacy_late_form_counts_in_corrected_week(self):
        # Week-2 date with legacy 晚交 tag counts as business week 1.
        self._make_form(self.user, '2026-09-14', '需要人工审核;晚交')
        stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        self.assertEqual(stats['current_week'], 1)
        self.assertEqual(stats['week_submitted'], 1)

    def test_h_explicit_week_correction_wins(self):
        # 显式 第3周 不得被计入当前第 1 周。
        self._make_form(self.user, '2026-09-07', '需要人工审核;第3周')
        stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        self.assertEqual(stats['current_week'], 1)
        self.assertEqual(stats['week_submitted'], 0)

    # ---- I. empty profile ----

    def test_i_empty_profile_is_safe_and_neutral(self):
        stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        self.assertEqual(stats['total_deduction'], 0.0)
        self.assertEqual(stats['deduction_display'], '0.00')
        self.assertEqual(stats['days_since_last'], '无')
        self.assertEqual(stats['last_submit'], '无')
        self.assertFalse(stats['has_evaluation_sample'])
        self.assertEqual(stats['rating_label'], '暂无评级')

        response = self._render_profile(self.user, WEEK1_MONDAY)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('0.00', html)
        self.assertIn('暂无评级', html)

    # ---- parity: one canonical snapshot for both consumers ----

    def test_service_and_admin_wrapper_and_page_share_one_snapshot(self):
        form = self._make_form(self.user, '2026-09-07')
        self._evaluate(form, 3.5)

        service_stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        with patch('app.blueprints.admin.users.datetime', _frozen_datetime(WEEK1_MONDAY)):
            wrapper_stats = _build_user_profile_stats(self.user)

        for field in PARITY_FIELDS:
            self.assertEqual(
                wrapper_stats[field], service_stats[field],
                msg=f'admin wrapper must delegate to canonical service: {field}',
            )

        response = self._render_profile(self.user, WEEK1_MONDAY)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(service_stats['deduction_display'], html)
        self.assertIn(service_stats['rating_label'], html)
        self.assertIn(service_stats['current_week_label'], html)

    # ---- Round 7B-R1 pre-fix repro: tie bias & departed-user pool pollution ----

    def test_all_zero_evaluated_cohort_gets_excellent_not_attention(self):
        # 5 名真实评价且扣分全为 0 的用户：同分 cohort 不得因人数变成"最差评级"。
        zero_users = [self._make_user(f'U2{i}', f'zero{i}') for i in range(5)]
        for user in zero_users:
            form = self._make_form(user, '2026-09-07')
            self._evaluate(form, 0)

        for user in zero_users:
            stats = build_user_profile_stats(user, now=WEEK1_MONDAY)
            self.assertTrue(stats['has_evaluation_sample'])
            self.assertEqual(stats['percentile'], 1.0)
            self.assertEqual(stats['rating_band'], 'excellent')
            self.assertEqual(stats['rating_label'], '行为很好')

    def test_departed_evaluated_user_excluded_from_ranking_pool(self):
        # 离任用户的历史 ScoreRecord 不得进入在任用户的排名池。
        form = self._make_form(self.user, '2026-09-07')
        self._evaluate(form, 10)

        departed = self._make_user('U900', 'departed900')
        departed.is_active = False
        db.session.commit()
        departed_form = self._make_form(departed, '2026-09-07')
        self._evaluate(departed_form, 0)

        stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        self.assertTrue(stats['has_evaluation_sample'])
        self.assertEqual(stats['percentile'], 1.0)
        self.assertEqual(stats['rating_band'], 'excellent')
        self.assertEqual(stats['rating_label'], '行为很好')

    def test_legacy_null_active_user_stays_in_ranking_pool(self):
        # active_user_filter 契约：is_active=NULL 视为 active，不得被意外排除。
        legacy = self._make_user('U910', 'legacy910')
        legacy.is_active = None
        db.session.commit()
        legacy_form = self._make_form(legacy, '2026-09-07')
        self._evaluate(legacy_form, 5)

        form = self._make_form(self.user, '2026-09-07')
        self._evaluate(form, 10)

        legacy_stats = build_user_profile_stats(legacy, now=WEEK1_MONDAY)
        self.assertTrue(legacy_stats['has_evaluation_sample'])
        self.assertEqual(legacy_stats['percentile'], 1.0)  # pool [10, 5] → right edge of {5}

        user_stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        self.assertEqual(user_stats['percentile'], 0.5)  # pool [10, 5] → 1/2

    def test_non_eligible_role_score_excluded_from_ranking_pool(self):
        # role 不在 Profile 资格集合内的 active 用户，其评分不进入排名池。
        outsider = self._make_user('U920', 'outsider920', role='编外人员')
        outsider_form = self._make_form(outsider, '2026-09-07')
        self._evaluate(outsider_form, 100)

        form = self._make_form(self.user, '2026-09-07')
        self._evaluate(form, 10)

        stats = build_user_profile_stats(self.user, now=WEEK1_MONDAY)
        self.assertEqual(stats['percentile'], 1.0)  # pool [10]，编外 100 分未进入
        self.assertEqual(stats['rating_band'], 'excellent')

    def test_mixed_tie_block_uses_right_edge_semantics(self):
        # [10, 10, 0, 0, 0, 0, 0, 0, 0, 0]：并列最差取 2/10=0.2，零分取 10/10=1.0。
        high_users = [self._make_user(f'U3{i}', f'high{i}') for i in range(2)]
        zero_users = [self._make_user(f'U4{i}', f'tz{i}') for i in range(8)]
        for user in high_users:
            self._evaluate(self._make_form(user, '2026-09-07'), 10)
        for user in zero_users:
            self._evaluate(self._make_form(user, '2026-09-07'), 0)

        for user in high_users:
            stats = build_user_profile_stats(user, now=WEEK1_MONDAY)
            self.assertEqual(stats['percentile'], 0.2)
            self.assertEqual(stats['rating_band'], 'attention')
            self.assertEqual(stats['rating_label'], '需要继续努力')
        for user in zero_users:
            stats = build_user_profile_stats(user, now=WEEK1_MONDAY)
            self.assertEqual(stats['percentile'], 1.0)
            self.assertEqual(stats['rating_band'], 'excellent')


class RelativeDeductionPercentileTest(unittest.TestCase):
    """纯 helper 单测：锁定 tie 右边界 percentile contract（无 DB/Flask）。"""

    def test_examples_from_contract_hold(self):
        cases = [
            ([0], 0, 1.0),
            ([0, 0, 0, 0, 0], 0, 1.0),
            ([10, 5], 10, 0.5),
            ([10, 5], 5, 1.0),
            ([10, 10, 0, 0, 0, 0, 0, 0, 0, 0], 10, 0.2),
            ([10, 10, 0, 0, 0, 0, 0, 0, 0, 0], 0, 1.0),
        ]
        for all_scores, user_score, expected in cases:
            with self.subTest(all_scores=all_scores, user_score=user_score):
                self.assertEqual(
                    _relative_deduction_percentile(all_scores, user_score), expected,
                )

    def test_empty_pool_and_missing_score_return_none(self):
        self.assertIsNone(_relative_deduction_percentile([], 0))
        self.assertIsNone(_relative_deduction_percentile([10, 5], None))
        self.assertIsNone(_relative_deduction_percentile([], None))


if __name__ == '__main__':
    unittest.main()
