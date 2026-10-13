"""A width-variant selection code must still resolve the same course.

The schedule search exposes a width-normalized selection code (full-width （） and
Ⅰ/Ⅲ folded to ASCII) while Course rows keep the workbook's exact text.  Passing
the exposed value to the registration API used to fail with "course does not
exist" even though the search had just offered that course.
"""
import tempfile
import unittest
from pathlib import Path

from app.app import app  # noqa: E402
from app.models import (  # noqa: E402
    Course, ScheduleCourseMembership, SystemSetting, db,
)
from app.services.registration_course_identity import (  # noqa: E402
    resolve_current_course_input,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database  # noqa: E402
from tests.schedule_fixture import seed_current_schedule  # noqa: E402


class WidthVariantResolutionTest(unittest.TestCase):
    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory(prefix='swu-tic-width-')
        self.addCleanup(self.runtime.cleanup)
        configure_sqlite_database(app, db, Path(self.runtime.name) / 'width.sqlite')
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.batch = seed_current_schedule('2026-2027-1')
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('teaching_first_week_monday', '2026-08-31')
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()

    def _add_offering(self, course_code, selection_code, meetings=1):
        """Add one offering; `seed_current_schedule` already owns the batch mapping."""
        courses = []
        for index in range(meetings):
            course = Course(course_code=course_code, selection_code=selection_code,
                            course_name='宽度测试课程', semester='2026-2027-1',
                            academic_year='2026-2027', weekday=3,
                            class_period=f'第{index + 1}-{index + 1}节')
            db.session.add(course)
            courses.append(course)
        db.session.flush()
        for course in courses:
            db.session.add(ScheduleCourseMembership(batch_id=self.batch.id,
                                                    course_id=course.id))
        db.session.commit()
        return courses

    def test_full_width_parentheses_resolve(self):
        self._add_offering('TEST-WIDTH-001', '测试课程II（甲）-0001')
        resolved = resolve_current_course_input('TEST-WIDTH-001', '测试课程II(甲)-0001')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.selection_code, '测试课程II（甲）-0001')

    def test_roman_numeral_variant_resolves(self):
        self._add_offering('TEST-WIDTH-002', '测试课程Ⅰ-0004')
        resolved = resolve_current_course_input('TEST-WIDTH-002', '测试课程I-0004')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.selection_code, '测试课程Ⅰ-0004')

    def test_several_meetings_of_one_offering_still_resolve(self):
        """An offering legitimately spans one row per meeting."""
        courses = self._add_offering('TEST-WIDTH-003', '测试课程（乙）-0002', meetings=4)
        resolved = resolve_current_course_input('TEST-WIDTH-003', '测试课程(乙)-0002')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.selection_code, '测试课程（乙）-0002')
        self.assertEqual(resolved.id, min(course.id for course in courses))

    def test_exact_match_still_wins(self):
        self._add_offering('TEST-WIDTH-004', '测试课程A（丙）-0019')
        resolved = resolve_current_course_input('TEST-WIDTH-004', '测试课程A（丙）-0019')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.selection_code, '测试课程A（丙）-0019')

    def test_unknown_course_still_fails(self):
        self._add_offering('TEST-WIDTH-001', '测试课程II（甲）-0001')
        self.assertIsNone(resolve_current_course_input('TEST-WIDTH-001', '不存在的课程-9999'))
        self.assertIsNone(resolve_current_course_input('TEST-WIDTH-UNKNOWN', '测试课程II(甲)-0001'))

    def test_distinct_width_variants_are_not_merged(self):
        """Two genuinely different offerings must stay ambiguous, not guessed."""
        self._add_offering('X001', '课程（甲）-0001')
        self._add_offering('X001', '课程（甲）-0002')
        # '课程(甲)-0002' matches only the second offering.
        resolved = resolve_current_course_input('X001', '课程(甲)-0002')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.selection_code, '课程（甲）-0002')
        # A variant that could describe either must not be resolved.
        self.assertIsNone(resolve_current_course_input('X001', '课程(甲)'))


if __name__ == '__main__':
    unittest.main()
