import unittest
from datetime import date
import tempfile
from pathlib import Path
from tests.draft_revision_client import DraftRevisionClient

from app.services.listening_assistant_contracts import Candidate
from app.services.listening_assistant_evidence import safe_field_snapshot
from app.services.student_grade_class_formatting import build_major_aliases, current_major_aliases, format_class_text, format_student_grade_class


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
        self.client = DraftRevisionClient(app)
        with self.client.session_transaction() as session:
            session['user_id'] = user.id
        self.client.observe_draft_revision()

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

    def test_current_source_aliases_are_only_used_for_matching(self):
        self.add_source([('2024水保1班', '水土保持与荒漠化防治'),
                         ('2024生科(师范)1班', '生物科学(师范)'),
                         ('2024化学1班', '化学'), ('2024化学2班', '应用化学'),
                         ('2024药学远志班', '药学(远志全英文创新实验班)')])
        self.add_source([('2023水保1班', '错误旧名称')], semester='2025-2026-1', selected=False)
        self.assertEqual(format_student_grade_class('2024水保1班;2024生科(师)2班'),
                         '2024级水保01班；2024级生科（师范）02班')
        self.assertEqual(format_class_text('2024水保1班;2024生科(师)2班', current_major_aliases()),
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

    def test_manual_class_name_is_not_replaced_by_major_alias(self):
        self.add_source([('2026公管类1班', '公共管理类'),
                         ('2026数学(师范)3班', '数学与应用数学(师范)'),
                         ('2024农林经济01班', '农林经济管理')])
        for raw, expected in [
            ('2026公管类1班', '2026级公管类01班'),
            ('2026数学(师范)3、4班', '2026级数学（师范）03、04班'),
            ('2024农林经济01班', '2024级农林经济01班'),
        ]:
            with self.subTest(raw=raw):
                response = self.client.post('/user/api/student_grade_class/format',
                                            json={'value': raw})
                self.assertEqual(response.json['value'], expected)
                response = self.client.put('/user/api/lecture_form_draft',
                                           json={'data': {'student_grade_class': raw}})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.client.get('/user/api/lecture_form_draft').json['data']['student_grade_class'],
                                 expected)

    def test_mechanism_and_agricultural_class_names_remain_in_timetable_form(self):
        self.add_source([('2025机制01班;2025机制02班;2025机制03班', '机械设计制造及其自动化'),
                         ('2025农机班', '农业机械化及其自动化')])
        raw = '2025机制01班;2025机制02班;2025机制03班;2025农机班'
        expected = '2025级机制01、02、03班；2025级农机班'
        self.assertEqual(self.client.post('/user/api/student_grade_class/format',
                                         json={'value': raw}).json['value'], expected)
        self.assertEqual(candidate(raw).to_public_dict()['student_grade_class'], expected)
        self.assertEqual(safe_field_snapshot(candidate(raw), {})['student_grade_class'], expected)
        response = self.client.put('/user/api/lecture_form_draft',
                                   json={'data': {'student_grade_class': raw}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/user/api/lecture_form_draft').json['data']['student_grade_class'],
                         expected)
        explicitly_written_full_name = '2025级机械设计制造及其自动化01、02、03班；2025级农机班'
        self.assertEqual(self.client.post('/user/api/student_grade_class/format',
                                         json={'value': explicitly_written_full_name}).json['value'],
                         explicitly_written_full_name)

    def test_candidate_and_confirmation_follow_source_class_not_major_name_length(self):
        import pandas as pd
        from app.models import ListeningAssistantScheduleEntry
        from app.services.listening_assistant import ListeningAssistantService
        from app.services.listening_assistant_contracts import AssistantQuery
        from app.services.listening_assistant_schedule import persist_listening_assistant_entries
        from app.services.teaching_calendar import TeachingCalendarConfig

        cases = [
            ('2025机制01班', '机械设计制造及其自动化', '2025级机制01班'),
            ('2025机械设计制造及其自动化02班', '机制', '2025级机械设计制造及其自动化02班'),
            ('2024农林经济01班', '农林经济管理', '2024级农林经济01班'),
            ('2024农林经济管理02班', '农林经济', '2024级农林经济管理02班'),
            ('2025机制01班;2025机制02班;2025农机班',
             '机械设计制造及其自动化;农业机械化及其自动化',
             '2025级机制01、02班；2025级农机班'),
        ]
        batch = self.add_source([(raw, major) for raw, major, _ in cases])
        frame = pd.DataFrame([
            {'姓名': f'课表教师{index}', '教师所属学院': '测试学院', '课程名称': f'课表课程{index}',
             '星期几': 5, '上课节次': '第3-4节', '场地名称': '32-302', '起始周': '1-20',
             '教学班组成': raw, '专业组成': major, '学期': batch.semester,
             '学年': '2026', '课程号': f'SOURCE{index}', '选课号': str(index)}
            for index, (raw, major, _) in enumerate(cases)
        ])
        persist_listening_assistant_entries(frame, [batch])
        self.db.session.commit()
        service = ListeningAssistantService(
            semester=batch.semester,
            calendar=TeachingCalendarConfig(date(2026, 9, 7), 0, 20))
        for index, (raw, major, expected) in enumerate(cases):
            with self.subTest(source_class=raw, source_major=major):
                indexed = ListeningAssistantScheduleEntry.query.filter_by(
                    batch_id=batch.id, teacher_name=f'课表教师{index}').one()
                self.assertEqual(indexed.student_grade_class_raw, raw)
                result = service.search(AssistantQuery(
                    lecture_date=date(2026, 9, 18), room='32-302', teacher_name=f'课表教师{index}'))
                self.assertEqual(len(result.candidates), 1)
                selected = result.candidates[0]
                public = selected.to_public_dict()
                self.assertEqual(public['student_grade_class'], expected)
                self.assertEqual(safe_field_snapshot(selected, {})['student_grade_class'], expected)
                self.assertEqual(public['candidate_id'], selected.candidate_id)

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
