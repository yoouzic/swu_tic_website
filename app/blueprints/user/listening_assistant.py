# -*- coding: utf-8 -*-
"""Authenticated, non-persistent listening-assistant API routes."""

from __future__ import annotations

import unicodedata
from datetime import date
from functools import wraps
from typing import Any, Mapping

from flask import jsonify, request, session

from app.models import User, db
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import AssistantQuery
from app.services.listening_assistant_evidence import (
    AssistantSelectionError,
    revalidate_selection,
)
from app.utils.user_status import is_user_active

from . import user_bp


BACKUP_FALLBACK_REASONS = frozenset({'no_result', 'rejected_candidates'})
_QUERY_FIELDS = frozenset({
    'lecture_date',
    'date',
    'room',
    'teacher_name',
    'teacher',
    'period',
    'student_grade_class',
    'semester',
})
_QUERY_ALIASES = {
    'date': 'lecture_date',
    'teacher': 'teacher_name',
}
_SNAPSHOT_FIELDS = frozenset({
    'lecture_date',
    'class_period',
    'lecture_location',
    'room',
    'period',
    'course_title',
    'teacher_name',
    'teacher_college',
    'student_grade_class',
})


def _envelope(success: bool, data: Any = None, message: str = ''):
    return jsonify({
        'success': bool(success),
        'data': data,
        'message': message,
    })


def _error_response(message: str, status: int = 400):
    return _envelope(False, None, message), status


def _assistant_login_required(view):
    """Keep API authentication failures JSON instead of redirecting to HTML."""

    @wraps(view)
    def decorated(*args, **kwargs):
        user_id = session.get('user_id')
        if user_id is None:
            return _error_response('请先登录', 401)

        user = db.session.get(User, user_id)
        if not user or not is_user_active(user):
            session.clear()
            return _error_response('账号已离任或不可用', 401)
        return view(user, *args, **kwargs)

    return decorated


def _clean_text(value: object, field_name: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f'{field_name} is required')
        return None
    if not isinstance(value, str):
        raise TypeError(f'{field_name} must be text')
    normalized = unicodedata.normalize('NFKC', value).replace('\u3000', ' ').strip()
    if not normalized:
        if required:
            raise ValueError(f'{field_name} is required')
        return None
    return normalized


def _parse_date(value: object, field_name: str = 'date') -> date:
    normalized = _clean_text(value, field_name, required=True)
    try:
        return date.fromisoformat(normalized)
    except (TypeError, ValueError) as error:
        raise ValueError(f'{field_name} must be YYYY-MM-DD') from error


def _normalize_rejected_ids(value: object) -> list[str]:
    """Accept CSV, repeated query values, and JSON list values without coercion."""
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        raise TypeError('rejected_ids must be text or a list')

    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise TypeError('rejected_ids entries must be text')
        for token in item.split(','):
            token = token.strip()
            if token and token not in normalized:
                normalized.append(token)
    return normalized


def _query_rejected_ids() -> list[str]:
    values = request.args.getlist('rejected_ids')
    values.extend(request.args.getlist('rejected_ids[]'))
    return _normalize_rejected_ids(values)


def _json_object() -> Mapping[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        raise ValueError('JSON body must be an object')
    return payload


def _required_batch_id(value: object) -> int | str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError('source_batch_id is required')
    if isinstance(value, str):
        normalized = _clean_text(value, 'source_batch_id', required=True)
        return normalized
    if value <= 0:
        raise ValueError('source_batch_id is required')
    return value


def _build_query(
    *,
    lecture_date: date,
    room: str | None = None,
    teacher: str | None = None,
    period: object = None,
    student_grade_class: str | None = None,
) -> AssistantQuery:
    return AssistantQuery(
        lecture_date=lecture_date,
        room=room,
        teacher_name=teacher,
        period=period,
        student_grade_class=student_grade_class,
    )


def _normalize_confirm_query(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_query = payload.get('query')
    if raw_query is None:
        raw_query = {}
    if not isinstance(raw_query, Mapping):
        raise TypeError('query must be an object')

    query: dict[str, Any] = {}
    for key, value in raw_query.items():
        if key not in _QUERY_FIELDS:
            raise ValueError('query contains unsupported fields')
        canonical_key = _QUERY_ALIASES.get(key, key)
        if canonical_key in query and query[canonical_key] != value:
            raise ValueError(f'query contains conflicting {canonical_key} values')
        query[canonical_key] = value

    top_level_values = {
        'lecture_date': payload.get('lecture_date', payload.get('date')),
        'room': payload.get('room'),
        'teacher_name': payload.get('teacher_name', payload.get('teacher')),
        'period': payload.get('period'),
        'student_grade_class': payload.get('student_grade_class'),
        'semester': payload.get('semester'),
    }
    for key, value in top_level_values.items():
        if value is None or key in query:
            continue
        query[key] = value

    if 'lecture_date' not in query:
        raise ValueError('date is required')
    query['lecture_date'] = _parse_date(query['lecture_date']).isoformat()

    for key in ('room', 'teacher_name', 'student_grade_class'):
        if key in query and query[key] is not None:
            query[key] = _clean_text(query[key], f'query.{key}')
    if 'semester' in query and query['semester'] is not None:
        query['semester'] = _clean_text(query['semester'], 'query.semester', required=True)
    return query


def _normalize_selection_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    selection: dict[str, Any] = {
        'query': _normalize_confirm_query(payload),
        'rejected_ids': _normalize_rejected_ids(payload.get('rejected_ids')),
    }

    for key in (
        'source_kind',
        'source_batch_id',
        'semester',
        'candidate_id',
        'overrides',
        'template_version',
        'stage',
        'acknowledged_source',
        'explicit_fallback',
    ):
        if key in payload:
            selection[key] = payload[key]

    if 'semester' in selection and selection['semester'] is not None:
        selection['semester'] = _clean_text(selection['semester'], 'semester', required=True)
    if 'reason' in payload and 'fallback_reason' in payload:
        if payload['reason'] != payload['fallback_reason']:
            raise ValueError('reason and fallback_reason must match')
    if 'fallback_reason' in payload:
        selection['fallback_reason'] = payload['fallback_reason']
    elif 'reason' in payload:
        selection['fallback_reason'] = payload['reason']
    return selection


def _public_confirmation(normalized) -> dict[str, Any]:
    confirmation = normalized.confirmation
    return {
        'candidate': normalized.candidate.to_public_dict(),
        'field_snapshot': dict(normalized.field_snapshot),
        'query': dict(normalized.query_payload),
        'overrides': dict(normalized.overrides),
        'template_version': normalized.template_version,
        'confirmation': {
            'confirmed': confirmation.confirmed,
            'candidate_id': normalized.candidate.candidate_id,
            'source_kind': normalized.source_kind,
            'source_batch_id': normalized.source_batch_id,
            'semester': normalized.semester,
            'acknowledged_source': confirmation.acknowledged_source,
            'explicit_fallback': normalized.explicit_fallback,
            'fallback_reason': normalized.fallback_reason,
            'stage': normalized.stage,
        },
    }


def _expected_selection_error(error: AssistantSelectionError) -> tuple[int, str]:
    if error.code == 'candidate_not_found':
        return 404, 'assistant candidate is stale or unknown'
    return 400, str(error) or 'invalid assistant selection'


@user_bp.route('/api/listening-assistant/candidates', methods=['GET'])
@_assistant_login_required
def listening_assistant_candidates(user):
    try:
        lecture_date = _parse_date(request.args.get('date'))
        room = _clean_text(request.args.get('room'), 'room')
        teacher = _clean_text(
            request.args.get('teacher', request.args.get('teacher_name')),
            'teacher',
        )
        student_grade_class = _clean_text(
            request.args.get('student_grade_class'),
            'student_grade_class',
        )
        semester = _clean_text(request.args.get('semester'), 'semester')
        query = _build_query(
            lecture_date=lecture_date,
            room=room,
            teacher=teacher,
            period=request.args.get('period'),
            student_grade_class=student_grade_class,
        )
        result = ListeningAssistantService(semester=semester).search(
            query,
            semester=semester,
            rejected_ids=_query_rejected_ids(),
        )
    except (AssistantSelectionError, ValueError, TypeError) as error:
        return _error_response(str(error) or '请求参数无效', 400)
    return _envelope(True, result.to_public_dict(), '候选查询完成')


@user_bp.route('/api/listening-assistant/fallback', methods=['POST'])
@_assistant_login_required
def listening_assistant_fallback(user):
    del user
    try:
        payload = _json_object()
        lecture_date = _parse_date(payload.get('date'))
        teacher = _clean_text(payload.get('teacher'), 'teacher', required=True)
        semester = _clean_text(payload.get('semester'), 'semester', required=True)
        source_batch_id = _required_batch_id(payload.get('source_batch_id'))
        reason = _clean_text(payload.get('reason'), 'reason', required=True)
        if reason not in BACKUP_FALLBACK_REASONS:
            raise ValueError('reason must be one of: no_result, rejected_candidates')
        if payload.get('explicit_fallback', True) is not True:
            raise ValueError('fallback requires an explicit fallback')
        query = _build_query(lecture_date=lecture_date, teacher=teacher)
        result = ListeningAssistantService(semester=semester).search_backup(
            query,
            source_batch_id=source_batch_id,
            semester=semester,
            rejected_ids=_normalize_rejected_ids(payload.get('rejected_ids')),
            explicit_fallback=True,
            reason=reason,
        )
    except (AssistantSelectionError, ValueError, TypeError) as error:
        return _error_response(str(error) or '请求参数无效', 400)

    data = result.to_public_dict()
    data.update({
        'needs_confirmation': True,
        'acknowledged_source': False,
        'explicit_fallback': True,
        'source_batch_id': str(source_batch_id),
        'fallback_reason': reason,
    })
    return _envelope(True, data, '备用课表线索需要人工核对')


@user_bp.route('/api/listening-assistant/confirm', methods=['POST'])
@_assistant_login_required
def listening_assistant_confirm(user):
    try:
        payload = _json_object()
        selection_payload = _normalize_selection_payload(payload)
        selected_semester = selection_payload.get('semester') or selection_payload['query'].get('semester')
        service = ListeningAssistantService(semester=selected_semester)
        normalized = revalidate_selection(
            user,
            selection_payload,
            service=service,
            semester=selected_semester,
        )
    except AssistantSelectionError as error:
        status, message = _expected_selection_error(error)
        return _error_response(message, status)
    except (ValueError, TypeError) as error:
        return _error_response(str(error) or '请求参数无效', 400)

    return _envelope(True, _public_confirmation(normalized), '候选已重新验证，请继续提交表单')


__all__ = [
    'listening_assistant_candidates',
    'listening_assistant_confirm',
    'listening_assistant_fallback',
]
