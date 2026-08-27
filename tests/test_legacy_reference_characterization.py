# -*- coding: utf-8 -*-
"""Characterization tests for review reference-data contract.

Schedule matches remain frozen against the legacy Excel path.  Contact matches
now use the canonical ``User`` source while preserving the legacy response
shape, matching order and duplicate behavior.
"""
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.app import app
from app.models import User, db
from app.utils.auto_review import AutoReviewEngine
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


def _write_schedule(path):
    wb = Workbook()
    ws = wb.active
    ws.append([
        '姓名', '教师所属学院', '课程名称', '星期几', '上课节次',
        '场地名称', '教学班组成', '起始周',
    ])
    ws.append([
        '张三', '计算机与信息科学学院、软件学院', '数据结构',
        '三', '第3-4节', '32-302', '2023级计算机1班', '1',
    ])
    ws.append([
        '李四', '外国语学院', '数据结构',
        '四', '第1-2节', '10-101', '2023级外语1班', '2',
    ])
    ws.append([
        '王五', '物理科学与技术学院', '大学物理',
        '一', '第1-2节', '20-201', '2023级物理1班', '1',
    ])
    wb.save(path)


def _write_many_schedule(path):
    wb = Workbook()
    ws = wb.active
    ws.append([
        '姓名', '教师所属学院', '课程名称', '星期几', '上课节次',
        '场地名称', '教学班组成', '起始周',
    ])
    for i in range(1, 7):
        ws.append([
            f'教师{i}', '计算机学院', '数据结构',
            '一', f'第{i}-{i + 1}节', f'3{i}-{i}01', f'2023级班{i}', '1',
        ])
    wb.save(path)


class LegacyReferenceCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'characterization.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self._add_user('1001', '张三', '办公部', '', '计算机与信息科学学院、软件学院', '13800000000')
        self._add_user('1002', '张三丰', '技术部', '', '工程技术学院', '13900000000')
        self._add_user('1003', '李四', '策划部', '', '外国语学院', '13700000000')
        self._add_user('1004', '欧阳修文正凯旋门', '测试部', '第一小组', '测试学院', '13600000000')
        self._add_user('123456', '王五', '物理部', '', '物理科学与技术学院', '13500000000')
        self._add_user('1005', '已离任', '离任部', '', '离任学院', '13400000000', active=False)
        self.schedule_path = Path(self.temp_dir.name) / 'schedule.xlsx'
        _write_schedule(self.schedule_path)
        self.engine = AutoReviewEngine(schedule_path=str(self.schedule_path))

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _add_user(self, number, name, department, group, college, phone, active=True):
        db.session.add(User(
            number=number,
            department=department or '未分配部门',
            name=name,
            gender='-',
            grade='2023',
            college=college,
            major='-',
            dormitory='-',
            phone=phone,
            qq='-',
            student_id=f'S{number}',
            password_hash='x',
            role='信息员',
            group=group or '未分配小组',
            group_id=None,
            is_active=active,
        ))
        db.session.commit()

    def test_empty_input_returns_empty_references(self):
        result = self.engine.search_reference_data({})
        self.assertEqual(result, {'schedule_matches': [], 'contact_matches': []})

    def test_exact_contact_by_id_contract(self):
        result = self.engine.search_reference_data({'listener_number': '1001'})
        matches = result['contact_matches']
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match['id'], '1001')
        self.assertEqual(match['name'], '张三')
        self.assertEqual(match['college'], '计算机与信息科学学院、软件学院')
        self.assertEqual(match['department'], '办公部')
        self.assertEqual(match['phone'], '13800000000')
        self.assertEqual(match['match_type'], 'id')
        self.assertNotIn('similarity', match)

    def test_name_exact_contract_has_no_similarity(self):
        result = self.engine.search_reference_data({'listener_name': '张三'})
        matches = result['contact_matches']
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match['match_type'], 'name')
        self.assertNotIn('similarity', match)

    def test_id_and_name_same_person_deduplicates_to_id_match(self):
        result = self.engine.search_reference_data({
            'listener_number': '1001',
            'listener_name': '张三',
        })
        matches = result['contact_matches']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['match_type'], 'id')

    def test_id_and_name_different_persons_return_id_then_name(self):
        result = self.engine.search_reference_data({
            'listener_number': '1001',
            'listener_name': '李四',
        })
        matches = result['contact_matches']
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]['id'], '1001')
        self.assertEqual(matches[0]['match_type'], 'id')
        self.assertEqual(matches[1]['id'], '1003')
        self.assertEqual(matches[1]['match_type'], 'name')

    def test_reviewer_id_numeric_fallback_requires_more_than_five_digits(self):
        short = self.engine.search_reference_data({'reviewer_id': '1001'})
        self.assertEqual(short['contact_matches'], [])

        long = self.engine.search_reference_data({'reviewer_id': '123456'})
        matches = long['contact_matches']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['id'], '123456')
        self.assertEqual(matches[0]['match_type'], 'id')

    def test_id_whitespace_does_not_change_legacy_exact_match(self):
        result = self.engine.search_reference_data({'listener_number': ' 1001 '})
        self.assertEqual(result['contact_matches'], [])

    def test_name_only_left_parenthesis_in_reference_data_keeps_legacy_extraction(self):
        result = self.engine.search_reference_data({'listener_name': '张三（计算机学院'})
        matches = result['contact_matches']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['match_type'], 'name')
        self.assertNotIn('similarity', matches[0])

    def test_find_reviewer_by_name_only_left_parenthesis_is_not_exact(self):
        match = self.engine._find_reviewer_by_name('张三（计算机学院')
        self.assertIsNone(match)

    def test_duplicate_name_exact_winner_is_lowest_user_id(self):
        self._add_user('2001', '重复名', '测试部A', '', '学院A', '13100000001')
        self._add_user('2002', '重复名', '测试部B', '', '学院B', '13200000002')
        result = self.engine.search_reference_data({'listener_name': '重复名'})
        matches = result['contact_matches']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['id'], '2001')

    def test_fuzzy_contact_by_name_contract(self):
        result = self.engine.search_reference_data({'listener_name': '欧阳修文正凯旋'})
        matches = result['contact_matches']
        self.assertGreater(len(matches), 0)
        match = matches[0]
        self.assertEqual(match['name'], '欧阳修文正凯旋门')
        self.assertEqual(match['match_type'], 'name')
        self.assertIn('similarity', match)
        self.assertGreater(match['similarity'], 0.8)

    def test_inactive_user_is_not_a_contact_reference(self):
        result = self.engine.search_reference_data({'listener_number': '1005'})
        self.assertEqual(result['contact_matches'], [])

    def test_department_group_reconstruction_uses_slash_when_group_present(self):
        result = self.engine.search_reference_data({'listener_number': '1004'})
        matches = result['contact_matches']
        self.assertEqual(matches[0]['department'], '测试部/第一小组')

    def test_missing_contact_returns_empty(self):
        result = self.engine.search_reference_data({
            'listener_number': '9999',
            'listener_name': '完全不存在的人',
        })
        self.assertEqual(result['contact_matches'], [])

    def test_exact_schedule_match_contract(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'teacher_college': '计算机与信息科学学院、软件学院',
            'course_title': '数据结构',
            'student_grade_class': '2023级计算机1班',
            'lecture_location': '32-302',
            'lecture_date': '2026/09/09星期三',
            'class_period': '第3-4节',
        })
        matches = result['schedule_matches']
        self.assertGreater(len(matches), 0)
        match = matches[0]
        for field in (
            'course_name', 'teacher_name', 'teacher_college', 'weekday',
            'class_period', 'location', 'class_composition', 'start_week',
            'match_type',
        ):
            self.assertIn(field, match)
        self.assertEqual(match['teacher_name'], '张三')
        self.assertEqual(match['course_name'], '数据结构')
        self.assertIn('exact', match['match_type'])

    def test_schedule_teacher_name_fallback_contract(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '不存在的课程',
        })
        matches = result['schedule_matches']
        self.assertGreater(len(matches), 0)
        self.assertTrue(all(m['teacher_name'] == '张三' for m in matches))
        self.assertTrue(any(m['match_type'].startswith('teacher_name') for m in matches))

    def test_missing_schedule_returns_empty(self):
        result = self.engine.search_reference_data({
            'teacher_name': '不存在教师',
            'course_title': '不存在课程',
        })
        self.assertEqual(result['schedule_matches'], [])

    def test_schedule_returns_at_most_five_matches(self):
        result = self.engine.search_reference_data({
            'teacher_name': '',
            'course_title': '数据结构',
        })
        self.assertLessEqual(len(result['schedule_matches']), 5)

    def test_schedule_more_than_five_candidates_freezes_top_five_order(self):
        many_path = Path(self.temp_dir.name) / 'many-schedule.xlsx'
        _write_many_schedule(many_path)
        engine = AutoReviewEngine(schedule_path=str(many_path))
        result = engine.search_reference_data({'course_title': '数据结构'})
        matches = result['schedule_matches']
        self.assertEqual(len(matches), 5)
        self.assertEqual(
            [m['teacher_name'] for m in matches],
            ['教师1', '教师2', '教师3', '教师4', '教师5'],
        )
        self.assertTrue(all('course_name' in m['match_type'] for m in matches))

    def test_schedule_time_refinement_without_location_keeps_previous_candidates(self):
        # 时间筛选无结果时，legacy 会保留上一级候选，而不是清空。
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'lecture_date': '2026/09/10星期四',
            'class_period': '第9-10节',
        })
        matches = result['schedule_matches']
        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]['teacher_name'], '张三')

    def test_schedule_course_name_fallback_contract(self):
        result = self.engine.search_reference_data({
            'teacher_name': '不存在教师',
            'course_title': '数据结构',
        })
        matches = result['schedule_matches']
        self.assertGreater(len(matches), 0)
        self.assertTrue(all('course_name' in m['match_type'] for m in matches))

    def test_teacher_college_is_not_used_for_candidate_selection(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'teacher_college': '错误学院',
            'course_title': '数据结构',
        })
        matches = result['schedule_matches']
        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]['teacher_name'], '张三')

    def test_time_refinement_success_appends_with_time(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'teacher_college': '计算机与信息科学学院、软件学院',
            'course_title': '数据结构',
            'lecture_date': '2026/09/09星期三',
            'class_period': '第3-4节',
        })
        match = result['schedule_matches'][0]
        self.assertIn('_with_time', match['match_type'])

    def test_location_refinement_success_appends_with_location(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'lecture_location': '32-302',
        })
        match = result['schedule_matches'][0]
        self.assertIn('_with_location', match['match_type'])

    def test_location_refinement_failure_keeps_previous_candidates(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'lecture_location': '99-999',
        })
        match = result['schedule_matches'][0]
        self.assertNotIn('_with_location', match['match_type'])

    def test_class_refinement_success_appends_with_class(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'student_grade_class': '2023级计算机1班',
        })
        match = result['schedule_matches'][0]
        self.assertIn('_with_class', match['match_type'])

    def test_class_refinement_failure_keeps_previous_candidates(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'student_grade_class': '不存在的班级',
        })
        match = result['schedule_matches'][0]
        self.assertNotIn('_with_class', match['match_type'])

    def test_combined_refinement_suffix_order_is_frozen(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'teacher_college': '计算机与信息科学学院、软件学院',
            'course_title': '数据结构',
            'lecture_date': '2026/09/09星期三',
            'class_period': '第3-4节',
            'lecture_location': '32-302',
            'student_grade_class': '2023级计算机1班',
        })
        match = result['schedule_matches'][0]
        self.assertEqual(
            match['match_type'],
            'exact_with_time_with_location_with_class',
        )

    def test_start_week_is_payload_only_not_candidate_filter(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })
        match = result['schedule_matches'][0]
        self.assertIn('start_week', match)
        self.assertEqual(match['start_week'], '1')

    def test_location_normalization_removes_room_zero_padding(self):
        self.assertEqual(self.engine._normalize_location('33-0201'), '33-201')

    def test_weekday_normalization_freeze_numeric_variants(self):
        import pandas as pd

        values = ['3', 3, 3.0, '3.0']
        texts = [str(value) for value in values]
        self.assertEqual(texts, ['3', '3', '3.0', '3.0'])
        self.assertEqual(
            [self.engine._normalize_weekday(text) for text in texts],
            ['三', '三', '3.0', '3.0'],
        )

    def test_class_period_normalization_freeze_common_forms(self):
        self.assertEqual(self.engine._normalize_class_period('3-4节'), '第3-4节')
        self.assertEqual(self.engine._normalize_class_period('第3-4节'), '第3-4节')
        # Unprefixed/non-节-terminated values are currently returned as-is.
        self.assertEqual(self.engine._normalize_class_period('3-4'), '3-4')
        self.assertEqual(self.engine._normalize_class_period(''), '')

    def test_rongchang_location_normalization_freeze(self):
        self.assertEqual(self.engine._normalize_location('荣昌1234'), '荣昌01-234')
        self.assertEqual(self.engine._normalize_location('荣昌 1234'), '荣昌01-234')
        self.assertEqual(self.engine._normalize_location('33-0201'), '33-201')

    def test_student_grade_class_precedes_class_composition_in_reference_data(self):
        # Both anchors present: student_grade_class wins, even when the
        # legacy class_composition payload would not match.
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'student_grade_class': '2023级计算机1班',
            'class_composition': '不存在的班级',
        })
        self.assertGreater(len(result['schedule_matches']), 0)
        self.assertIn('_with_class', result['schedule_matches'][0]['match_type'])

        # When student_grade_class is present but does not match, the
        # class_composition field is NOT used for refinement.
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'student_grade_class': '不存在的班级',
            'class_composition': '2023级计算机1班',
        })
        self.assertGreater(len(result['schedule_matches']), 0)
        self.assertNotIn('_with_class', result['schedule_matches'][0]['match_type'])

    def test_class_composition_alone_participates_in_refinement(self):
        result = self.engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'class_composition': '2023级计算机1班',
        })
        self.assertGreater(len(result['schedule_matches']), 0)
        self.assertIn('_with_class', result['schedule_matches'][0]['match_type'])

    def test_empty_teacher_and_course_anchors_return_no_schedule_candidates(self):
        result = self.engine.search_reference_data({
            'class_period': '第3-4节',
            'lecture_location': '32-302',
            'student_grade_class': '2023级计算机1班',
        })
        self.assertEqual(result['schedule_matches'], [])


if __name__ == '__main__':
    unittest.main()
