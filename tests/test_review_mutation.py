# -*- coding: utf-8 -*-
"""Pure unit tests for review mutation primitives (Phase 2A.3-P0)."""
import unittest
from types import SimpleNamespace

from app.services.review_mutation import (
    REVIEW_EDITABLE_FIELD_LABELS,
    append_review_modification_note,
    collect_modified_fields,
)


class ReviewMutationHelperTests(unittest.TestCase):
    def test_unchanged_fields_return_empty(self):
        original = SimpleNamespace(
            listener_name='张三', lecture_date='2026-01-01',
            teacher_name='李老师', course_title='课程A',
        )
        modified = collect_modified_fields(
            original,
            {
                'listener_name': '张三',
                'lecture_date': '2026-01-01',
                'teacher_name': '李老师',
                'course_title': '课程A',
            },
        )
        self.assertEqual(modified, [])

    def test_none_and_empty_are_normalized_like_existing_routes(self):
        original = SimpleNamespace(
            listener_name='张三', lecture_date='2026-01-01',
        )
        modified = collect_modified_fields(
            original,
            {
                'listener_name': None,
                'lecture_date': '',
            },
        )
        self.assertEqual(modified, ['听课人姓名', '听课时间'])

    def test_multiple_changes_preserve_field_map_order(self):
        original = SimpleNamespace(
            listener_name='张三',
            course_changes='无',
            lecture_date='2026-01-01',
            teacher_name='李老师',
        )
        modified = collect_modified_fields(
            original,
            {
                'listener_name': '李四',
                'course_changes': '有变化',
                'lecture_date': '2026-01-01',
                'teacher_name': '王老师',
            },
        )
        self.assertEqual(
            modified,
            ['听课人姓名', '课程信息变化', '授课教师'],
        )

    def test_chinese_labels_are_stable(self):
        self.assertEqual(REVIEW_EDITABLE_FIELD_LABELS['listener_name'], '听课人姓名')
        self.assertEqual(REVIEW_EDITABLE_FIELD_LABELS['contact_phone2'], '联系电话2')

    def test_append_note_empty_base_comment(self):
        result = append_review_modification_note('无', ['听课人姓名'])
        self.assertEqual(
            result,
            '无\n\n[系统记录] 审核人修改了以下字段：听课人姓名',
        )

    def test_append_note_non_empty_base_comment(self):
        result = append_review_modification_note('整体不错', ['课程信息变化'])
        self.assertEqual(
            result,
            '整体不错\n\n[系统记录] 审核人修改了以下字段：课程信息变化',
        )

    def test_append_note_empty_modified_list_returns_base(self):
        self.assertEqual(append_review_modification_note('无', []), '无')


if __name__ == '__main__':
    unittest.main()
