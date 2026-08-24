# -*- coding: utf-8 -*-
"""Reference-data orchestration service.

This module owns the exact reference-data response contract previously
implemented inside AutoReviewEngine.search_reference_data():

    {
        'schedule_matches': [...],
        'contact_matches': [...]
    }
"""
from typing import Any, Dict

from app.services.review_contacts import (
    find_reviewer_by_id as _find_reviewer_by_id,
    find_reviewer_by_name as _find_reviewer_by_name,
)
from app.services.review_schedule_matcher import find_course_in_schedule
from app.services.review_schedule_source import resolve_review_schedule_source

_SCHEDULE_DF_UNSET = object()


def search_review_reference_data(
    form_data: Dict[str, Any],
    schedule_df=_SCHEDULE_DF_UNSET,
) -> Dict[str, Any]:
    """Search reference data using the frozen legacy contact/schedule contract.

    ``schedule_df`` omitted means resolve the global review schedule source.
    Passing ``None`` explicitly is an intentional unavailable source and must
    not trigger a second resolution.
    """
    result = {
        'schedule_matches': [],
        'contact_matches': [],
    }

    # 1. 搜索通讯录
    listener_name = form_data.get('listener_name')
    listener_id = form_data.get('listener_number')

    if not listener_id:
        raw_id = form_data.get('reviewer_id')
        if raw_id and str(raw_id).isdigit() and len(str(raw_id)) > 5:
            listener_id = raw_id

    if listener_id:
        contact_by_id = _find_reviewer_by_id(str(listener_id))
        if contact_by_id:
            contact_by_id['match_type'] = 'id'
            result['contact_matches'].append(contact_by_id)

    if listener_name:
        name_part = listener_name
        if '（' in listener_name:
            name_part = listener_name.split('（')[0].strip()

        contact_by_name = _find_reviewer_by_name(name_part, fuzzy=True)
        if contact_by_name:
            is_duplicate = False
            for existing in result['contact_matches']:
                if existing['id'] == contact_by_name['id']:
                    is_duplicate = True
                    break
            if not is_duplicate:
                contact_by_name['match_type'] = 'name'
                result['contact_matches'].append(contact_by_name)

    # 2. 搜索课表
    if schedule_df is _SCHEDULE_DF_UNSET:
        resolution = resolve_review_schedule_source()
        schedule_df = resolution.dataframe

    teacher_name = form_data.get('teacher_name')
    teacher_college = form_data.get('teacher_college')
    course_title = form_data.get('course_title')
    class_composition = form_data.get('student_grade_class') or form_data.get('class_composition')
    location = form_data.get('lecture_location')

    lecture_date = form_data.get('lecture_date')
    weekday_cn = None
    if lecture_date:
        s = str(lecture_date).strip()
        if '星期' in s:
            _, right = s.split('星期', 1)
            weekday_cn = right.strip()[:1] if right else None

    class_period = form_data.get('class_period')

    _, all_matches = find_course_in_schedule(
        schedule_df,
        teacher_name,
        teacher_college,
        course_title,
        class_composition,
        location,
        feedback_weekday_cn=weekday_cn,
        feedback_class_period=class_period,
    )

    result['schedule_matches'] = all_matches[:5]
    return result
