# -*- coding: utf-8 -*-
"""Review-form grouping and time-filter query semantics.

These helpers form the domain layer used by both admin review/statistics
blueprints and the assessment calculation service.  No Flask/HTTP imports.
"""
from datetime import datetime

from app.models import LectureForm
from app.services.teaching_calendar import parse_lecture_date


def latest_form_groups_for_users(listener_numbers):
    if not listener_numbers:
        return []
    forms = LectureForm.query.filter(
        LectureForm.listener_number.in_(listener_numbers)
    ).order_by(LectureForm.unique_id.asc(), LectureForm.created_at.asc(), LectureForm.id.asc()).all()
    group_map = {}
    for form in forms:
        uid = form.unique_id or form.id
        if uid not in group_map:
            group_map[uid] = []
        group_map[uid].append(form)
    groups = []
    for uid, form_list in group_map.items():
        sorted_forms = sorted(form_list, key=lambda f: f.id, reverse=True)
        latest_form = sorted_forms[0]
        groups.append({
            'unique_id': uid,
            'latest_form': latest_form,
            'forms': sorted_forms
        })
    return groups


def build_review_form_filter_datetime(group_data, time_filter_type):
    latest_form = (group_data or {}).get('latest_form')
    if not latest_form:
        return None
    if time_filter_type == 'lecture':
        lecture_date = parse_lecture_date_value(latest_form.lecture_date)
        if not lecture_date:
            return None
        return datetime.combine(lecture_date, datetime.min.time())
    if time_filter_type == 'updated':
        return get_form_latest_timestamp(latest_form)

    created_candidates = [
        form.created_at for form in ((group_data or {}).get('forms') or [])
        if getattr(form, 'created_at', None)
    ]
    if created_candidates:
        return min(created_candidates)
    return latest_form.created_at


def normalize_review_form_time_filter(time_filter_type):
    if time_filter_type in ['lecture', 'updated', 'created']:
        return time_filter_type
    return 'created'


def get_form_latest_timestamp(form):
    if not form:
        return None
    return form.updated_at or form.created_at


def parse_lecture_date_value(raw_value):
    return parse_lecture_date(raw_value)
