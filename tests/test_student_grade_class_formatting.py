import unittest
from datetime import date
import tempfile
from pathlib import Path

from app.services.listening_assistant_contracts import Candidate
from app.services.listening_assistant_evidence import safe_field_snapshot
from app.services.student_grade_class_formatting import build_major_aliases, format_class_text, format_student_grade_class


def candidate(class_text):
    return Candidate(candidate_id='retained-candidate', lecture_date=date(2026, 10, 13),
                     room='32-302', period=(3, 4), course_title='英语',
                     teacher_name='教师', student_grade_class=class_text)


class ClassFormattingBoundaryTest(unittest.TestCase):
    def test_candidate_card_formats_classes_without_changing_identity(self):
        value = candidate('2019英语(师)1班;2019英语(师)02班;2019化学(师)3班')
        self.assertEqual(value.to_public_dict()['student_grade_class'],
                         '2019级英语（师范）01、02班；2019级化学（师范）03班')
        self.assertEqual(value.candidate_id, 'retained-candidate')
        self.assertEqual(value.student_grade_class,
                         '2019英语(师)1班;2019英语(师)02班;2019化学(师)3班')

    def test_confirmation_formats_manual_class_override(self):
        value = candidate('2024园林3班')
        self.assertEqual(safe_field_snapshot(value, {
            'student_grade_class': '2024英语(师)1、2班',
        })['student_grade_class'], '2024级英语（师范）01、02班')

    def test_special_name_remains_without_invented_class_number(self):
        self.assertEqual(candidate('2024药学远志班').to_public_dict()['student_grade_class'],
                         '2024级药学远志班')

    def test_idempotent_lists_and_unknown_names(self):
        cases = {
            '2024英语(师)1班、2班；2024英语（师范）01班': '2024级英语（师范）01、02班',
            '2024园林1,2班，2025园林3班': '2024级园林01、02班；2025级园林03班',
            '2026学行科创实验班(智能农机)': '2026级学行科创实验班（智能农机）',
            '创新实验班': '创新实验班',
            '23体教(师)1班': '23体教（师范）1班',
            '2024代码100班': '2024级代码100班',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(format_class_text(raw), expected)
                self.assertEqual(format_class_text(expected), expected)

    def test_mixed_course_does_not_undo_a_unique_one_to_one_major_mapping(self):
        aliases = build_major_aliases([
            ('2024水保01班', '水土保持与荒漠化防治'),
            ('2024水保01班;2024园林02班', '水土保持与荒漠化防治,园林'),
            ('2024大类01班', '专业一,专业二'),
        ])
        self.assertEqual(format_class_text('2024水保1班', aliases), '2024级水土保持与荒漠化防治01班')
        self.assertEqual(format_class_text('2024大类1班', aliases), '2024级大类01班')


class ClassFormattingSourceTest(unittest.TestCase):
    def setUp(self):
        from app.app import app
        from app.models import db, User
        from tests.app_test_utils import configure_sqlite_database
        self.app, self.db = app, db
        self.temp = tempfile.TemporaryDirectory()
        configure_sqlite_database(app, db, Path(self.temp.name) / 'format.sqlite')
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        db.create_all()
        user = User(number='991010', student_id='test010', name='测试信息员',
                    role='信息员', is_active=True, department='测试部门', gender='-',
                    grade='-', college='-', major='-', dormitory='-', phone='-', qq='-',
                    password_hash='unused', group='测试组')
        db.session.add(user)
        db.session.commit()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = user.id

    def tearDown(self):
        from tests.app_test_utils import cleanup_sqlite_database
        cleanup_sqlite_database(self.db, drop_all=True)
        self.context.pop()
        self.temp.cleanup()

    def add_source(self, pairs, semester='2026-2027-1', selected=True):
        from app.models import (Course, ScheduleImportBatch, ScheduleCourseMapping,
                                ScheduleCourseMembership, ScheduleSemesterSelection, SystemSetting)
        batch = ScheduleImportBatch(semester=semester, source_filename='source.xlsx',
                                    source_sha256='a' * 64, row_count=len(pairs))
        self.db.session.add(batch)
        self.db.session.flush()
        self.db.session.add(ScheduleCourseMapping(batch_id=batch.id))
        for raw, major in pairs:
            course = Course(course_code='TEST', selection_code='TEST', course_name='测试课程', class_composition=raw,
                            major_composition=major, semester=semester)
            self.db.session.add(course)
            self.db.session.flush()
            self.db.session.add(ScheduleCourseMembership(batch_id=batch.id, course_id=course.id))
        if selected:
            self.db.session.add(ScheduleSemesterSelection(semester=semester, active_batch_id=batch.id))
            SystemSetting.set('teaching_current_semester', semester)
        self.db.session.commit()
        return batch

    def test_only_unique_current_source_aliases_expand(self):
        self.add_source([('2024水保1班', '水土保持与荒漠化防治'),
                         ('2024生科(师范)1班', '生物科学(师范)'),
                         ('2024化学1班', '化学'), ('2024化学2班', '应用化学'),
                         ('2024药学远志班', '药学(远志全英文创新实验班)')])
        self.add_source([('2023水保1班', '错误旧名称')], semester='2025-2026-1', selected=False)
        self.assertEqual(format_student_grade_class('2024水保1班;2024生科(师)2班'),
                         '2024级水土保持与荒漠化防治01班；2024级生物科学（师范）02班')
        self.assertEqual(format_student_grade_class('2024化学1班'), '2024级化学01班')
        self.assertEqual(format_student_grade_class('2024药学远志班'), '2024级药学远志班')

    def test_manual_format_api_without_timetable_and_invalid_body(self):
        response = self.client.post('/user/api/student_grade_class/format',
                                    json={'value': '2024英语(师)1、2班'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['value'], '2024级英语（师范）01、02班')
        self.assertNotIn('warnings', response.json)
        self.assertEqual(self.client.post('/user/api/student_grade_class/format',
                                         json={'value': []}).status_code, 400)

    def test_draft_write_and_read_are_normalized(self):
        response = self.client.put('/user/api/lecture_form_draft',
                                   json={'data': {'student_grade_class': '2024英语(师)1班'}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/user/api/lecture_form_draft').json['data']['student_grade_class'],
                         '2024级英语（师范）01班')

    def test_review_matching_recognizes_formatted_classes(self):
        import pandas as pd
        from app.services.review_schedule_matcher import find_course_in_schedule
        self.add_source([('2024水保1班;2024水保2班', '水土保持与荒漠化防治')])
        frame = pd.DataFrame([dict(姓名='教师', 教师所属学院='学院', 课程名称='课程',
                                  星期几=2, 上课节次='第3-4节', 场地名称='32-302', 起始周='1-20',
                                  教学班组成=text) for text in
                              ('2024水保3班', '2024水保1班;2024水保2班')])
        best, matches = find_course_in_schedule(frame, '教师', '学院', '课程',
                      '2024级水土保持与荒漠化防治01、02班', '32-302')
        self.assertEqual(len(matches), 1)
        self.assertEqual(best['class_composition'], '2024水保1班;2024水保2班')


if __name__ == '__main__':
    unittest.main()
