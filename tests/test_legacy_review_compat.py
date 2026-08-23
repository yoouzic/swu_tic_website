# -*- coding: utf-8 -*-
"""Fresh-deploy compatibility regression for the legacy AutoReviewEngine."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from app.app import app
from app.models import SystemSetting, db
from app.services.teaching_calendar import TeachingCalendarConfig, teaching_week_number
from app.utils.auto_review import AutoReviewEngine
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class LegacyReviewCompatTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'legacy-compat.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _set_setting(self, key, value):
        SystemSetting.set(key, value)

    def _set_canonical(self, first_week, week_start='0', total_weeks='20'):
        self._set_setting('teaching_first_week_monday', first_week)
        self._set_setting('teaching_week_start_day', week_start)
        self._set_setting('teaching_total_weeks', total_weeks)

    def test_fresh_deploy_with_canonical_teaching_setting_only(self):
        self._set_canonical('2026-09-07')

        engine = AutoReviewEngine()

        self.assertEqual(engine.semester_monday, date(2026, 9, 7))

    def test_canonical_teaching_setting_takes_precedence_over_legacy(self):
        self._set_canonical('2026-09-07')
        self._set_setting('semester_first_monday', '2025-09-01')

        engine = AutoReviewEngine()

        self.assertEqual(engine.semester_monday, date(2026, 9, 7))

    def test_legacy_setting_still_works_alone(self):
        self._set_setting('semester_first_monday', '2025-09-01')

        engine = AutoReviewEngine()

        self.assertEqual(engine.semester_monday, date(2025, 9, 1))

    def test_neither_setting_keeps_semester_unconfigured(self):
        engine = AutoReviewEngine()

        self.assertIsNone(engine.semester_monday)

    def test_invalid_canonical_does_not_mask_with_stale_legacy(self):
        self._set_setting('teaching_first_week_monday', 'not-a-date')
        self._set_setting('semester_first_monday', '2025-09-01')

        engine = AutoReviewEngine()

        self.assertIsNone(engine.semester_monday)
        self.assertIsNone(engine._compute_week_from_date(date(2025, 9, 8)))

    def test_monday_start_differential(self):
        self._set_canonical('2026-09-07', week_start='0', total_weeks='20')
        engine = AutoReviewEngine()
        config = TeachingCalendarConfig(
            first_week_date=date(2026, 9, 7), week_start_day=0, total_weeks=20,
        )
        for target in (
            date(2026, 9, 7),
            date(2026, 9, 13),
            date(2026, 9, 14),
            date(2026, 10, 5),
            date(2027, 1, 24),
        ):
            self.assertEqual(
                engine._compute_week_from_date(target),
                teaching_week_number(target, config),
            )

    def test_sunday_start_differential(self):
        self._set_canonical('2026-09-07', week_start='6', total_weeks='20')
        engine = AutoReviewEngine()
        config = TeachingCalendarConfig(
            first_week_date=date(2026, 9, 7), week_start_day=6, total_weeks=20,
        )
        for target in (
            date(2026, 9, 6),
            date(2026, 9, 7),
            date(2026, 9, 12),
            date(2026, 9, 13),
            date(2026, 9, 14),
            date(2027, 1, 24),
        ):
            self.assertEqual(
                engine._compute_week_from_date(target),
                teaching_week_number(target, config),
            )

    def test_tuesday_start_differential(self):
        self._set_canonical('2026-09-07', week_start='1', total_weeks='20')
        engine = AutoReviewEngine()
        config = TeachingCalendarConfig(
            first_week_date=date(2026, 9, 7), week_start_day=1, total_weeks=20,
        )
        for target in (
            date(2026, 9, 7),
            date(2026, 9, 8),
            date(2026, 9, 13),
            date(2026, 9, 14),
            date(2027, 1, 24),
        ):
            self.assertEqual(
                engine._compute_week_from_date(target),
                teaching_week_number(target, config),
            )

    def test_before_term_and_after_total_weeks_differential(self):
        self._set_canonical('2026-09-07', week_start='0', total_weeks='2')
        engine = AutoReviewEngine()
        config = TeachingCalendarConfig(
            first_week_date=date(2026, 9, 7), week_start_day=0, total_weeks=2,
        )
        for target in (date(2026, 9, 1), date(2026, 9, 21), date(2026, 9, 28)):
            self.assertEqual(
                engine._compute_week_from_date(target),
                teaching_week_number(target, config),
            )

    def test_legacy_fallback_week_no_uses_monday_delta(self):
        self._set_setting('semester_first_monday', '2026-09-07')
        engine = AutoReviewEngine()
        self.assertEqual(engine._compute_week_from_date(date(2026, 9, 7)), 1)
        self.assertEqual(engine._compute_week_from_date(date(2026, 9, 14)), 2)
        self.assertIsNone(engine._compute_week_from_date(date(2026, 9, 1)))
        self.assertIsNone(engine._compute_week_from_date(datetime(2026, 9, 1)))

    def test_explicit_constructor_override_still_wins(self):
        self._set_canonical('2026-09-07', week_start='0', total_weeks='20')
        engine = AutoReviewEngine(semester_monday='2025-09-01')
        self.assertEqual(engine._compute_week_from_date(date(2025, 9, 1)), 1)
        self.assertEqual(engine._compute_week_from_date(date(2025, 9, 8)), 2)
        self.assertEqual(engine.semester_monday, date(2025, 9, 1))

    def test_construction_no_longer_reads_feedback_excel(self):
        self._set_canonical('2026-09-07', week_start='0', total_weeks='20')
        with mock.patch('app.utils.auto_review._read_excel', return_value=None) as read_excel:
            AutoReviewEngine()
        self.assertEqual(read_excel.call_count, 1)


if __name__ == '__main__':
    unittest.main()
