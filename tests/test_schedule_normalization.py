import unittest
from datetime import date, datetime

from app.review_automation.schedules.aliases import (
    CURRENT_SCHOOL_SCHEDULE_ALIASES,
    HISTORICAL_SCHOOL_SCHEDULE_ALIASES,
    PERSONAL_SCHEDULE_ALIASES,
)
from app.review_automation.schedules.normalization import (
    NormalizationError,
    normalize_class_name,
    normalize_college,
    normalize_date,
    normalize_identifier,
    normalize_name,
    normalize_weekday,
    parse_period_range,
    parse_weeks,
    periods_overlap,
    resolve_columns,
)


class ScheduleNormalizationTest(unittest.TestCase):
    def test_dates_accept_excel_serial_datetime_and_weekday_text(self):
        target = date(2026, 4, 13)
        excel_serial = (target - date(1899, 12, 30)).days

        self.assertEqual(normalize_date(target), target)
        self.assertEqual(normalize_date(datetime(2026, 4, 13, 8, 30)), target)
        self.assertEqual(normalize_date(excel_serial), target)
        self.assertEqual(normalize_date('2026-04-13'), target)
        self.assertEqual(normalize_date('2026/4/13（星期一）'), target)
        self.assertEqual(
            normalize_date('4月13日', semester_monday=date(2026, 2, 23)),
            target,
        )

    def test_invalid_date_is_structured(self):
        with self.assertRaises(NormalizationError) as caught:
            normalize_date('不是日期')
        self.assertEqual(caught.exception.field, 'date')
        self.assertEqual(caught.exception.code, 'invalid_date')

    def test_weeks_accept_ranges_parity_and_chinese_punctuation(self):
        self.assertEqual(parse_weeks('1-16周'), frozenset(range(1, 17)))
        self.assertEqual(parse_weeks('1-16周(双)'), frozenset(range(2, 17, 2)))
        self.assertEqual(
            parse_weeks('1，3、5-9周'),
            frozenset({1, 3, 5, 6, 7, 8, 9}),
        )
        self.assertEqual(parse_weeks(7), frozenset({7}))

        with self.assertRaises(NormalizationError) as caught:
            parse_weeks('第0周')
        self.assertEqual(caught.exception.field, 'weeks')
        self.assertEqual(caught.exception.code, 'invalid_week')

    def test_weekdays_accept_common_chinese_forms(self):
        self.assertEqual(normalize_weekday('星期一'), 1)
        self.assertEqual(normalize_weekday('周一'), 1)
        self.assertEqual(normalize_weekday('一'), 1)
        self.assertEqual(normalize_weekday('1'), 1)
        self.assertEqual(normalize_weekday('星期日'), 7)
        self.assertEqual(normalize_weekday(7), 7)

        with self.assertRaises(NormalizationError):
            normalize_weekday('星期八')

    def test_periods_accept_ranges_pair_cells_and_zero_padded_text(self):
        self.assertEqual(parse_period_range('1-2节'), (1, 2))
        self.assertEqual(parse_period_range('第3、4节'), (3, 4))
        self.assertEqual(parse_period_range('0102'), (1, 2))
        self.assertEqual(parse_period_range(5), (5, 5))
        self.assertTrue(periods_overlap(1, 2, 2, 4))
        self.assertFalse(periods_overlap(1, 2, 3, 4))

        with self.assertRaises(NormalizationError):
            parse_period_range('0-2节')

    def test_identifiers_and_names_preserve_identity_and_normalize_punctuation(self):
        self.assertEqual(normalize_identifier('  00123  '), '00123')
        self.assertEqual(normalize_identifier(123.0), '123')
        self.assertNotIn('.0', normalize_identifier(123.0))
        self.assertEqual(normalize_name('  合成\u3000名称  '), '合成 名称')
        self.assertEqual(normalize_college('【合成学院】 '), '合成学院')
        self.assertEqual(
            normalize_college('合成信院', {'合成信院': '合成信息学院'}),
            '合成信息学院',
        )
        self.assertEqual(normalize_class_name('（合成一班）'), '合成一班')

    def test_aliases_resolve_historical_and_current_headers_without_positions(self):
        required = {
            'course_title',
            'teacher_name',
            'teacher_college',
            'teaching_class',
            'weeks',
            'weekday',
            'periods',
            'location',
        }
        historical = resolve_columns(
            [
                '无关列',
                '课程名称',
                '任课教师',
                '教师所在学院',
                '教学班组成',
                '周次',
                '星期几',
                '上课节次',
                '上课地点',
            ],
            HISTORICAL_SCHOOL_SCHEDULE_ALIASES,
            required,
        )
        current = resolve_columns(
            [
                '教室',
                '上课周',
                '课程',
                '教师姓名',
                '开课学院',
                '教学班',
                '星期',
                '节次',
            ],
            CURRENT_SCHOOL_SCHEDULE_ALIASES,
            required,
        )

        self.assertEqual(historical['course_title'], '课程名称')
        self.assertEqual(historical['periods'], '上课节次')
        self.assertEqual(current['course_title'], '课程')
        self.assertEqual(current['location'], '教室')

        personal = resolve_columns(
            ['学号', '课程名称', '教学周', '星期几', '第几节'],
            PERSONAL_SCHEDULE_ALIASES,
            {'student_id', 'course_title', 'weeks', 'weekday', 'periods'},
        )
        self.assertEqual(personal['student_id'], '学号')


if __name__ == '__main__':
    unittest.main()
