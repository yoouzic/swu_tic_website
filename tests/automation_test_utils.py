"""Synthetic-only helpers for automated review model tests."""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app.app import app
from app.models import LectureForm, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


@contextmanager
def temporary_automation_database():
    """Provide an isolated SQLite database containing synthetic test data only."""
    with tempfile.TemporaryDirectory(prefix='automation-models-') as temp_dir:
        db_path = Path(temp_dir) / 'automation.sqlite'
        configure_sqlite_database(app, db, db_path)
        context = app.app_context()
        context.push()
        try:
            db.drop_all()
            db.create_all()
            yield
        finally:
            cleanup_sqlite_database(db, drop_all=True)
            context.pop()


def make_synthetic_form(**overrides):
    """Build a de-identified LectureForm with stable synthetic values."""
    values = {
        'listener_name': '合成听课人',
        'listener_number': 'SYN-001',
        'lecture_date': '2026-04-13',
        'class_period': '1-2',
        'lecture_location': '合成教室A',
        'teacher_name': '合成教师',
        'teacher_college': '合成学院',
        'course_title': '合成课程',
        'student_grade_class': '合成班级',
        'teaching_method': '合成教学方法',
        'classroom_discipline': '合成课堂纪律',
        'classroom_atmosphere': '合成课堂氛围',
        'courseware_quality': '合成课件质量',
        'overall_effect': '合成教学效果',
        'quality_case': '合成案例',
        'course_feedback': '该老师的合成课程反馈内容。',
        'student_signature1': '合成见证人甲',
        'contact_phone1': '13900000001',
        'status': '待审核',
        'reviewer_id': None,
        'review_time': None,
        'review_comment': '合成原始审核意见',
        'unique_id': 1001,
    }
    values.update(overrides)
    return LectureForm(**values)


__all__ = ['make_synthetic_form', 'temporary_automation_database']
