# -*- coding: utf-8 -*-
"""Tests for the shared form-week semantic layer (Phase 2B-P2)."""
import unittest
from datetime import date

from app.services.form_week_semantics import effective_form_week
from app.services.teaching_calendar import TeachingCalendarConfig


CONFIG = TeachingCalendarConfig(
    first_week_date=date(2026, 9, 7),
    week_start_day=5,
    total_weeks=20,
)


class FormWeekSemanticsTests(unittest.TestCase):
    def test_explicit_week_correction_wins(self):
        self.assertEqual(
            effective_form_week('2026-09-07', '需要人工审核;第3周', CONFIG),
            3,
        )

    def test_normal_date_maps_to_week(self):
        # 2026-09-07 is inside week 1 for Saturday-start config.
        self.assertEqual(
            effective_form_week('2026-09-07', '', CONFIG),
            1,
        )

    def test_legacy_late_subtracts_one(self):
        # 2026-09-12 is week 2; legacy 晚交 subtracts one.
        self.assertEqual(
            effective_form_week('2026-09-12', '需要人工审核;晚交', CONFIG),
            1,
        )

    def test_before_term_returns_none(self):
        self.assertIsNone(
            effective_form_week('2026-09-04', '', CONFIG),
        )

    def test_after_term_returns_none(self):
        term_end = date(2027, 1, 23)
        self.assertIsNone(
            effective_form_week(term_end.isoformat(), '', CONFIG),
        )

    def test_late_correction_before_term_returns_none(self):
        # week_no would be None before subtraction, so None is preserved.
        self.assertIsNone(
            effective_form_week('2026-09-04', '需要人工审核;晚交', CONFIG),
        )

    def test_invalid_lecture_date_returns_none(self):
        self.assertIsNone(effective_form_week('not-a-date', '', CONFIG))


if __name__ == '__main__':
    unittest.main()
