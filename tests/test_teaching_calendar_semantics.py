# -*- coding: utf-8 -*-
"""Phase 2B-P0 teaching calendar semantic characterization tests.

These tests are intentionally written against the *current* production
semantics.  They do not fix any teaching-calendar production code and they
do not assert the Phase 2B-P1 corrected TimeValidator behavior yet.
"""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.app import app
from app.models import SystemSetting, db
from app.utils.audit_tags import parse_audit_tag
from app.utils.leave_management import (
    get_form_effective_week_no,
    get_teaching_settings,
    get_teaching_week_no,
    parse_lecture_date_value,
)
from app.utils.course_registration_limits import get_current_teaching_week_no
from app.utils.time_validator import TimeValidator
from app.blueprints.admin.shared import _parse_lecture_date_value
from app.blueprints.admin.assessment_stats import (
    _compute_teaching_week_window,
    _get_form_effective_week_no,
    _get_teaching_reward_settings,
    _get_teaching_week_no,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


FIRST_WEEK_DATE = date(2026, 9, 7)  # Monday
DEFAULT_SETTINGS = {
    'first_week_date': FIRST_WEEK_DATE,
    'week_start_day': 5,
    'required_submission': 1,
}


def _teaching_start(first_week_date=FIRST_WEEK_DATE, week_start_day=5):
    return first_week_date - timedelta(
        days=(first_week_date.weekday() - week_start_day) % 7
    )


def _user_profile_local_week(date_obj, first_week_date=FIRST_WEEK_DATE, week_start_day=5):
    """Exact replica of user.profile / admin.users local get_week_num."""
    if not first_week_date or not date_obj:
        return -1
    if isinstance(date_obj, datetime):
        current_date = date_obj.date()
    else:
        current_date = date_obj
    actual_start = _teaching_start(first_week_date, week_start_day)
    diff = (current_date - actual_start).days
    if diff < 0:
        return -1
    return (diff // 7) + 1


def _user_profile_local_form_effective(form, settings=None):
    """Exact replica of user.profile / admin.users get_form_group_week_num."""
    settings = settings or DEFAULT_SETTINGS
    parsed_tag = parse_audit_tag(form.audit_tag)
    if parsed_tag.get('week_correction_week_no') is not None:
        return parsed_tag['week_correction_week_no']
    return _user_profile_local_week(
        parse_lecture_date_value(form.lecture_date),
        settings['first_week_date'],
        settings['week_start_day'],
    )


def _form(lecture_date='2026-09-07', audit_tag=''):
    return SimpleNamespace(lecture_date=lecture_date, audit_tag=audit_tag)


class TeachingStartFormulaTests(unittest.TestCase):
    def test_monday_start(self):
        self.assertEqual(_teaching_start(FIRST_WEEK_DATE, 0), date(2026, 9, 7))

    def test_saturday_start(self):
        self.assertEqual(_teaching_start(FIRST_WEEK_DATE, 5), date(2026, 9, 5))

    def test_sunday_start(self):
        self.assertEqual(_teaching_start(FIRST_WEEK_DATE, 6), date(2026, 9, 6))


class TimeValidatorWeekdayOffsetCharacterizationTests(unittest.TestCase):
    """Pins the current TimeValidator behavior for Phase 2B-P1 reference.

    Phase 2B-P1 will add corrected expectations; these tests intentionally
    assert the current (buggy for non-Monday starts) output so the suite stays
    green before the production fix.
    """

    def _target(self, week_start_day):
        with patch.object(
            TimeValidator,
            'get_teaching_calendar_settings',
            return_value=(datetime(2026, 9, 7), week_start_day),
        ):
            return TimeValidator.calculate_target_date(1, 1)

    def test_week1_monday_with_monday_start_is_correct(self):
        self.assertEqual(self._target(0), datetime(2026, 9, 7))

    def test_week1_monday_with_saturday_start_pins_current_bug(self):
        self.assertEqual(self._target(5), datetime(2026, 9, 5))

    def test_week1_monday_with_sunday_start_pins_current_bug(self):
        self.assertEqual(self._target(6), datetime(2026, 9, 6))

    def test_get_time_suggestion_current_week_uses_teaching_start(self):
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 9, 7)

        with patch.object(
            TimeValidator,
            'get_teaching_calendar_settings',
            return_value=(datetime(2026, 9, 7), 5),
        ), patch('app.utils.time_validator.datetime', FrozenDateTime):
            suggestion = TimeValidator.get_time_suggestion()
        # teaching_start is 2026-09-05, now 2026-09-07 => current week 1,
        # so suggestion is for next week (week 2).
        self.assertIn('第2周', suggestion)


class WeekNumberEquivalenceTests(unittest.TestCase):
    def test_core_week_number_boundaries(self):
        teaching_start = _teaching_start()
        cases = [
            (teaching_start - timedelta(days=1), None),
            (teaching_start, 1),
            (teaching_start + timedelta(days=6), 1),
            (teaching_start + timedelta(days=7), 2),
        ]
        for target, expected in cases:
            with self.subTest(target=target):
                leave_result = get_teaching_week_no(target, DEFAULT_SETTINGS)
                assessment_result = _get_teaching_week_no(
                    target, FIRST_WEEK_DATE, DEFAULT_SETTINGS['week_start_day']
                )
                user_local_result = _user_profile_local_week(target)
                self.assertEqual(leave_result, expected)
                self.assertEqual(assessment_result, expected)
                if expected is None:
                    self.assertEqual(user_local_result, -1)
                else:
                    self.assertEqual(user_local_result, expected)

    def test_course_registration_limits_boundaries(self):
        teaching_start = _teaching_start()
        cases = [
            (teaching_start - timedelta(days=1), None),
            (teaching_start, 1),
            (teaching_start + timedelta(days=6), 1),
            (teaching_start + timedelta(days=7), 2),
        ]
        for target, expected in cases:
            with self.subTest(target=target):
                with patch(
                    'app.utils.course_registration_limits.SystemSetting.get',
                    side_effect=self._fake_setting_get,
                ), patch(
                    'app.utils.course_registration_limits.datetime',
                    self._frozen_datetime(target),
                ):
                    self.assertEqual(get_current_teaching_week_no(), expected)

    @staticmethod
    def _fake_setting_get(key, default=None):
        values = {
            'teaching_first_week_monday': '2026-09-07',
            'teaching_week_start_day': '5',
        }
        return values.get(key, default)

    @staticmethod
    def _frozen_datetime(target):
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls):
                return cls(target.year, target.month, target.day)

        return FrozenDateTime


class LectureDateParsingEquivalenceTests(unittest.TestCase):
    def test_parsers_match_for_supported_formats(self):
        cases = [
            '2026-09-07',
            '2026/09/07',
            '2026.09.07',
            '2026年9月7日',
            '2026年09月07日',
            '2026-9-7 星期一',
            '听课日期2026年9月7日',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    parse_lecture_date_value(raw),
                    _parse_lecture_date_value(raw),
                )
                self.assertEqual(parse_lecture_date_value(raw), date(2026, 9, 7))

    def test_datetime_and_date_inputs(self):
        self.assertEqual(
            parse_lecture_date_value(datetime(2026, 9, 7, 10, 30)),
            date(2026, 9, 7),
        )
        self.assertEqual(
            parse_lecture_date_value(date(2026, 9, 7)),
            date(2026, 9, 7),
        )


class EffectiveWeekSemanticsTests(unittest.TestCase):
    def test_explicit_week_correction_wins(self):
        form = _form(audit_tag='需要人工审核;第3周')
        self.assertEqual(get_form_effective_week_no(form, DEFAULT_SETTINGS), 3)
        self.assertEqual(_get_form_effective_week_no(form, DEFAULT_SETTINGS), 3)

    def test_normal_lecture_date_week(self):
        form = _form(lecture_date='2026-09-07')
        self.assertEqual(get_form_effective_week_no(form, DEFAULT_SETTINGS), 1)
        self.assertEqual(_get_form_effective_week_no(form, DEFAULT_SETTINGS), 1)

    def test_legacy_late_correction_canonical(self):
        form = _form(lecture_date='2026-09-12', audit_tag='需要人工审核;晚交')
        self.assertEqual(get_form_effective_week_no(form, DEFAULT_SETTINGS), 1)
        self.assertEqual(_get_form_effective_week_no(form, DEFAULT_SETTINGS), 1)

    def test_user_local_differs_for_legacy_late(self):
        form = _form(lecture_date='2026-09-12', audit_tag='需要人工审核;晚交')
        self.assertEqual(_user_profile_local_form_effective(form), 2)
        # The canonical helpers subtract one for legacy 晚交.
        self.assertEqual(get_form_effective_week_no(form, DEFAULT_SETTINGS), 1)

    def test_before_term_returns_none_canonical(self):
        form = _form(lecture_date='2026-09-04')
        self.assertIsNone(get_form_effective_week_no(form, DEFAULT_SETTINGS))
        self.assertIsNone(_get_form_effective_week_no(form, DEFAULT_SETTINGS))

    def test_no_form_returns_none_canonical(self):
        self.assertIsNone(get_form_effective_week_no(None, DEFAULT_SETTINGS))
        self.assertIsNone(_get_form_effective_week_no(None, DEFAULT_SETTINGS))


class TeachingWeekWindowTests(unittest.TestCase):
    def test_less_than_one_full_week(self):
        result = _compute_teaching_week_window(
            datetime(2026, 9, 5),
            datetime(2026, 9, 6),
            FIRST_WEEK_DATE,
            5,
        )
        self.assertFalse(result['has_full_weeks'])
        self.assertIsNone(result['start_week'])
        self.assertIsNone(result['end_week'])
        self.assertEqual(result['week_count'], 0)

    def test_exactly_one_full_week(self):
        result = _compute_teaching_week_window(
            datetime(2026, 9, 5),
            datetime(2026, 9, 12),
            FIRST_WEEK_DATE,
            5,
        )
        self.assertTrue(result['has_full_weeks'])
        self.assertEqual(result['start_week'], 1)
        self.assertEqual(result['end_week'], 1)
        self.assertEqual(result['week_count'], 1)
        self.assertEqual(result['window_start'], datetime(2026, 9, 5))
        self.assertEqual(result['window_end'], datetime(2026, 9, 12))

    def test_two_full_weeks(self):
        result = _compute_teaching_week_window(
            datetime(2026, 9, 5),
            datetime(2026, 9, 19),
            FIRST_WEEK_DATE,
            5,
        )
        self.assertTrue(result['has_full_weeks'])
        self.assertEqual(result['start_week'], 1)
        self.assertEqual(result['end_week'], 2)
        self.assertEqual(result['week_count'], 2)
        self.assertEqual(result['window_start'], datetime(2026, 9, 5))
        self.assertEqual(result['window_end'], datetime(2026, 9, 19))

    def test_partial_overlap_before_first_full_week(self):
        result = _compute_teaching_week_window(
            datetime(2026, 9, 1),
            datetime(2026, 9, 6),
            FIRST_WEEK_DATE,
            5,
        )
        self.assertFalse(result['has_full_weeks'])


class TeachingCalendarSettingsSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='teaching-calendar-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'teaching-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _set(self, key, value):
        SystemSetting.set(key, value)

    def test_time_validator_missing_first_week_returns_none_zero(self):
        self.assertEqual(TimeValidator.get_teaching_calendar_settings(), (None, 0))

    def test_time_validator_invalid_week_start_discards_parsed_first_week(self):
        self._set('teaching_first_week_monday', '2026-09-07')
        self._set('teaching_week_start_day', 'not-an-int')
        self.assertEqual(TimeValidator.get_teaching_calendar_settings(), (None, 0))

    def test_leave_management_missing_first_week_returns_error(self):
        settings, err = get_teaching_settings()
        self.assertIsNone(settings)
        self.assertIn('第一周起始日期', err)

    def test_leave_management_invalid_week_start_defaults_to_zero(self):
        self._set('teaching_first_week_monday', '2026-09-07')
        self._set('teaching_week_start_day', 'not-an-int')
        settings, err = get_teaching_settings()
        self.assertIsNone(err)
        self.assertEqual(settings['week_start_day'], 0)
        self.assertEqual(settings['first_week_date'], date(2026, 9, 7))

    def test_course_registration_missing_first_week_returns_none(self):
        with patch(
            'app.utils.course_registration_limits.SystemSetting.get',
            side_effect=lambda key, default=None: None,
        ):
            self.assertIsNone(get_current_teaching_week_no())

    def test_course_registration_invalid_week_start_defaults_to_zero(self):
        self._set('teaching_first_week_monday', '2026-09-07')
        self._set('teaching_week_start_day', 'not-an-int')

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 9, 7)

        with patch('app.utils.course_registration_limits.datetime', FrozenDateTime):
            self.assertEqual(get_current_teaching_week_no(), 1)

    def test_assessment_stats_missing_first_week_returns_error(self):
        settings, err = _get_teaching_reward_settings()
        self.assertIsNone(settings)
        self.assertIn('第一周起始日期', err)

    def test_assessment_stats_invalid_week_start_defaults_to_zero(self):
        self._set('teaching_first_week_monday', '2026-09-07')
        self._set('teaching_week_start_day', 'not-an-int')
        settings, err = _get_teaching_reward_settings()
        self.assertIsNone(err)
        self.assertEqual(settings['week_start_day'], 0)
        self.assertEqual(settings['first_week_date'], date(2026, 9, 7))


if __name__ == '__main__':
    unittest.main()
