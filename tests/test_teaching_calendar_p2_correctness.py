# -*- coding: utf-8 -*-
"""Phase 2B-P2 correctness regressions.

TimeValidator suggestion, assessment week-window term boundaries, and
profile after-term helpers.
"""
import re
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.services.teaching_calendar import TeachingCalendarConfig
from app.utils.time_validator import TimeValidator
from app.blueprints.admin.assessment_stats import _compute_teaching_week_window


CONFIG = TeachingCalendarConfig(
    first_week_date=date(2026, 9, 7),
    week_start_day=0,
    total_weeks=20,
)


def _frozen_datetime(target):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(target.year, target.month, target.day, 12, 0, 0)

    return FrozenDateTime


class TimeSuggestionCorrectnessTests(unittest.TestCase):
    def _suggestion(self, target):
        with patch(
            'app.services.teaching_calendar_settings.load_teaching_calendar_config',
            return_value=(CONFIG, None),
        ), patch('app.utils.time_validator.datetime', _frozen_datetime(target)):
            return TimeValidator.get_time_suggestion()

    def _assert_valid_week_in_suggestion(self, suggestion):
        match = re.search(r'第(\d+)周', suggestion)
        self.assertIsNotNone(match)
        week_no = int(match.group(1))
        self.assertGreaterEqual(week_no, 1)
        self.assertLessEqual(week_no, CONFIG.total_weeks)

    def test_missing_settings_returns_original_prompt(self):
        with patch(
            'app.services.teaching_calendar_settings.load_teaching_calendar_config',
            return_value=(None, 'MISSING_FIRST_WEEK'),
        ), patch('app.utils.time_validator.datetime', _frozen_datetime(datetime(2026, 9, 7))):
            suggestion = TimeValidator.get_time_suggestion()
        self.assertIn('请先', suggestion)

    def test_before_term_suggests_week_1(self):
        suggestion = self._suggestion(datetime(2026, 9, 6))
        self.assertIn('第1周', suggestion)

    def test_inside_week_1_suggests_week_2(self):
        suggestion = self._suggestion(datetime(2026, 9, 7))
        self.assertIn('第2周', suggestion)
        self._assert_valid_week_in_suggestion(suggestion)

    def test_inside_week_19_suggests_week_20(self):
        week19 = teaching_week_start = date(2026, 9, 7) + timedelta(weeks=18)
        suggestion = self._suggestion(datetime.combine(week19, datetime.min.time()))
        self.assertIn('第20周', suggestion)

    def test_final_week_returns_end_message(self):
        week20 = date(2026, 9, 7) + timedelta(weeks=19)
        suggestion = self._suggestion(datetime.combine(week20, datetime.min.time()))
        self.assertIn('已结束', suggestion)
        self.assertNotIn('第21周', suggestion)

    def test_after_term_returns_end_message(self):
        term_end = date(2026, 9, 7) + timedelta(weeks=20)
        suggestion = self._suggestion(datetime.combine(term_end, datetime.min.time()))
        self.assertIn('已结束', suggestion)
        self.assertNotIn('第21周', suggestion)


class AssessmentWindowTermBoundaryTests(unittest.TestCase):
    def _window(self, start, end):
        return _compute_teaching_week_window(
            start,
            end,
            CONFIG.first_week_date,
            CONFIG.week_start_day,
            CONFIG.total_weeks,
        )

    def test_after_term_only_returns_no_full_weeks(self):
        result = self._window(
            datetime(2027, 1, 25),
            datetime(2027, 2, 1),
        )
        self.assertFalse(result['has_full_weeks'])

    def test_range_crossing_term_end_clamps_to_week_20(self):
        result = self._window(
            datetime(2027, 1, 18),
            datetime(2027, 2, 1),
        )
        self.assertTrue(result['has_full_weeks'])
        self.assertEqual(result['start_week'], 20)
        self.assertEqual(result['end_week'], 20)
        self.assertLessEqual(result['end_week'], CONFIG.total_weeks)

    def test_range_before_term_clamps_to_week_1(self):
        result = self._window(
            datetime(2026, 8, 1),
            datetime(2026, 9, 14),
        )
        self.assertTrue(result['has_full_weeks'])
        self.assertEqual(result['start_week'], 1)
        self.assertLessEqual(result['end_week'], CONFIG.total_weeks)

    def test_full_term_range_never_exceeds_total_weeks(self):
        result = self._window(
            datetime(2026, 9, 7),
            datetime(2027, 1, 25),
        )
        self.assertTrue(result['has_full_weeks'])
        self.assertEqual(result['start_week'], 1)
        self.assertEqual(result['end_week'], 20)


if __name__ == '__main__':
    unittest.main()
