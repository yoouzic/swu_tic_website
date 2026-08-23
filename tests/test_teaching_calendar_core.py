# -*- coding: utf-8 -*-
"""Pure Teaching Calendar Core tests (Phase 2B-P1)."""
import unittest
from datetime import date, datetime, timedelta

from app.services.teaching_calendar import (
    TeachingCalendarConfig,
    date_for_teaching_weekday,
    parse_lecture_date,
    teaching_term_end,
    teaching_term_start,
    teaching_week_number,
)


FIRST = date(2026, 9, 7)  # Monday


def _config(week_start_day=0, total_weeks=20, first=FIRST):
    return TeachingCalendarConfig(
        first_week_date=first,
        week_start_day=week_start_day,
        total_weeks=total_weeks,
    )


class TeachingCalendarConfigTests(unittest.TestCase):
    def test_valid_config(self):
        config = _config()
        self.assertEqual(config.first_week_date, FIRST)
        self.assertEqual(config.week_start_day, 0)
        self.assertEqual(config.total_weeks, 20)

    def test_invalid_week_start_day(self):
        with self.assertRaises(ValueError):
            _config(week_start_day=-1)
        with self.assertRaises(ValueError):
            _config(week_start_day=7)

    def test_invalid_total_weeks(self):
        with self.assertRaises(ValueError):
            _config(total_weeks=0)
        with self.assertRaises(ValueError):
            _config(total_weeks=53)

    def test_datetime_first_week_is_normalized_to_date(self):
        config = TeachingCalendarConfig(
            first_week_date=datetime(2026, 9, 7, 0, 0),
            week_start_day=0,
            total_weeks=20,
        )
        self.assertEqual(config.first_week_date, FIRST)


class TeachingTermStartTests(unittest.TestCase):
    def test_monday_start(self):
        self.assertEqual(teaching_term_start(_config(0)), FIRST)

    def test_saturday_start(self):
        self.assertEqual(teaching_term_start(_config(5)), date(2026, 9, 5))

    def test_sunday_start(self):
        self.assertEqual(teaching_term_start(_config(6)), date(2026, 9, 6))


class TeachingTermEndTests(unittest.TestCase):
    def test_exclusive_end_after_20_weeks(self):
        self.assertEqual(teaching_term_end(_config(0, 20)), date(2027, 1, 25))

    def test_20_weeks_from_saturday_start(self):
        self.assertEqual(
            teaching_term_end(_config(5, 20)),
            date(2027, 1, 23),
        )


class TeachingWeekNumberTests(unittest.TestCase):
    def test_boundaries(self):
        config = _config(week_start_day=0, total_weeks=20)
        start = teaching_term_start(config)
        end = teaching_term_end(config)

        self.assertIsNone(teaching_week_number(start - timedelta(days=1), config))
        self.assertEqual(teaching_week_number(start, config), 1)
        self.assertEqual(teaching_week_number(start + timedelta(days=6), config), 1)
        self.assertEqual(teaching_week_number(start + timedelta(days=7), config), 2)
        self.assertEqual(teaching_week_number(end - timedelta(days=1), config), 20)
        self.assertIsNone(teaching_week_number(end, config))
        self.assertIsNone(teaching_week_number(end + timedelta(days=1), config))

    def test_datetime_and_date_inputs(self):
        config = _config()
        self.assertEqual(
            teaching_week_number(datetime(2026, 9, 7, 10, 30), config),
            1,
        )
        self.assertEqual(teaching_week_number(date(2026, 9, 7), config), 1)
        self.assertIsNone(teaching_week_number(None, config))


class DateForTeachingWeekdayTests(unittest.TestCase):
    def test_monday_start_monday(self):
        self.assertEqual(
            date_for_teaching_weekday(1, 1, _config(0)),
            FIRST,
        )

    def test_saturday_start_monday_returns_reference_monday(self):
        self.assertEqual(
            date_for_teaching_weekday(1, 1, _config(5)),
            FIRST,
        )

    def test_saturday_start_saturday_returns_term_start(self):
        self.assertEqual(
            date_for_teaching_weekday(1, 6, _config(5)),
            date(2026, 9, 5),
        )

    def test_sunday_start_monday_returns_reference_monday(self):
        self.assertEqual(
            date_for_teaching_weekday(1, 1, _config(6)),
            FIRST,
        )

    def test_week_20(self):
        self.assertEqual(
            date_for_teaching_weekday(20, 1, _config(0, 20)),
            date(2027, 1, 18),
        )

    def test_invalid_week_no(self):
        with self.assertRaises(ValueError):
            date_for_teaching_weekday(0, 1, _config())
        with self.assertRaises(ValueError):
            date_for_teaching_weekday(21, 1, _config(total_weeks=20))

    def test_invalid_weekday_no(self):
        with self.assertRaises(ValueError):
            date_for_teaching_weekday(1, 0, _config())
        with self.assertRaises(ValueError):
            date_for_teaching_weekday(1, 8, _config())


class ParseLectureDateCoreTests(unittest.TestCase):
    def test_supported_formats(self):
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
                self.assertEqual(parse_lecture_date(raw), date(2026, 9, 7))

    def test_datetime_and_date(self):
        self.assertEqual(parse_lecture_date(datetime(2026, 9, 7, 10, 30)), date(2026, 9, 7))
        self.assertEqual(parse_lecture_date(date(2026, 9, 7)), date(2026, 9, 7))

    def test_invalid(self):
        self.assertIsNone(parse_lecture_date(''))
        self.assertIsNone(parse_lecture_date(None))
        self.assertIsNone(parse_lecture_date('not-a-date'))


if __name__ == '__main__':
    unittest.main()
