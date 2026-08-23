# -*- coding: utf-8 -*-
"""Characterization tests for the current legacy reference-data contract.

These tests intentionally use the legacy AutoReviewEngine Excel-backed path to
freeze the exact request/response item fields and matching semantics before
any canonical migration decision.
"""
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.app import app
from app.models import db
from app.utils.auto_review import AutoReviewEngine
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


def _write_contacts(path):
    wb = Workbook()
    ws = wb.active
    ws.append(['编号', '姓名', '部门/组别', '学院', '手机号码'])
    ws.append(['1001', '张三', '办公部', '计算机与信息科学学院、软件学院', '13800000000'])
    ws.append(['1002', '张三丰', '技术部', '工程技术学院', '13900000000'])
    ws.append(['1003', '李四', '策划部', '外国语学院', '13700000000'])
    ws.append(['1004', '欧阳修文正凯旋门', '测试部', '测试学院', '13600000000'])
    wb.save(path)


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
        self.contacts_path = Path(self.temp_dir.name) / 'contacts.xlsx'
        self.schedule_path = Path(self.temp_dir.name) / 'schedule.xlsx'
        _write_contacts(self.contacts_path)
        _write_schedule(self.schedule_path)
        self.engine = AutoReviewEngine(
            schedule_path=str(self.schedule_path),
            contacts_path=str(self.contacts_path),
        )

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

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

    def test_fuzzy_contact_by_name_contract(self):
        result = self.engine.search_reference_data({'listener_name': '欧阳修文正凯旋'})
        matches = result['contact_matches']
        self.assertGreater(len(matches), 0)
        match = matches[0]
        self.assertIn('id', match)
        self.assertIn('name', match)
        self.assertIn('college', match)
        self.assertIn('department', match)
        self.assertIn('phone', match)
        self.assertIn('similarity', match)
        self.assertEqual(match['match_type'], 'name')

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


if __name__ == '__main__':
    unittest.main()
