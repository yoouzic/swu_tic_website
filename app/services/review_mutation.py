# -*- coding: utf-8 -*-
"""Pure review mutation primitives.

This module contains no Flask, SQLAlchemy, request, session, or model imports.
It only provides field-label and modified-field/comment helpers shared by the
two submit routes while leaving their orchestration route-local.
"""

REVIEW_EDITABLE_FIELD_LABELS = {
    'listener_name': '听课人姓名',
    'course_changes': '课程信息变化',
    'lecture_date': '听课时间',
    'class_period': '第几节',
    'lecture_location': '听课地点',
    'teacher_name': '授课教师',
    'teacher_college': '教师所属学院',
    'course_title': '课程总标题',
    'student_grade_class': '专业年级',
    'abnormal_situation': '异常情况反映',
    'teaching_method': '主要教学方法',
    'classroom_discipline': '管理课堂纪律',
    'classroom_atmosphere': '调动课堂气氛',
    'courseware_quality': '课件制作质量',
    'overall_effect': '整体教学效果',
    'quality_case': '优质案例推荐',
    'course_feedback': '课程反馈',
    'suggestions': '不足及建议',
    'student_signature1': '听课班级同学签名1',
    'contact_phone1': '联系电话1',
    'student_signature2': '听课班级同学签名2',
    'contact_phone2': '联系电话2',
}


def collect_modified_fields(original, candidate_values, field_labels=None):
    """Return editable field labels that changed between original and candidate.

    The caller must already have applied its own fallback semantics to
    ``candidate_values``. This helper only normalizes ``None``/empty to ``''``
    exactly as the current legacy routes do.
    """
    labels = field_labels if field_labels is not None else REVIEW_EDITABLE_FIELD_LABELS
    modified = []
    for field, label in labels.items():
        old_value = getattr(original, field, None)
        new_value = candidate_values.get(field)
        if str(new_value or '') != str(old_value or ''):
            modified.append(label)
    return modified


def append_review_modification_note(base_comment, modified_fields):
    """Append the existing system modification note to a base review comment."""
    if not modified_fields:
        return base_comment
    note = "\n\n[系统记录] 审核人修改了以下字段：{}".format(', '.join(modified_fields))
    return base_comment + note
