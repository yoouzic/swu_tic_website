# -*- coding: utf-8 -*-
"""Fresh-deploy compatibility regression for the legacy AutoReviewEngine."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.app import app
from app.models import SystemSetting, db
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

    def test_fresh_deploy_with_canonical_teaching_setting_only(self):
        # Fresh deployment: no legacy semester_first_monday or path keys.
        self._set_setting('teaching_first_week_monday', '2026-09-07')
        self._set_setting('teaching_week_start_day', '0')
        self._set_setting('teaching_total_weeks', '20')

        engine = AutoReviewEngine()

        self.assertIsNotNone(engine.semester_monday)
        self.assertEqual(engine.semester_monday, date(2026, 9, 7))

    def test_canonical_teaching_setting_takes_precedence_over_legacy(self):
        self._set_setting('teaching_first_week_monday', '2026-09-07')
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

    def test_invalid_canonical_setting_falls_back_to_legacy(self):
        self._set_setting('teaching_first_week_monday', 'not-a-date')
        self._set_setting('semester_first_monday', '2025-09-01')

        engine = AutoReviewEngine()

        self.assertEqual(engine.semester_monday, date(2025, 9, 1))


if __name__ == '__main__':
    unittest.main()
