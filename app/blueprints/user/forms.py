# -*- coding: utf-8 -*-
"""User blueprint domain module (Step 4 split)."""
from flask import render_template, request, redirect, url_for, flash, session, jsonify, current_app
from app.models import (
    Course,
    CourseRegistration as Reservation,
    LectureForm,
    LectureFormDraft,
    LectureSiteCapture,
    User,
    db,
)
from app.security import login_required, submission_required
from app.services.academic_term import get_current_teaching_semester
from app.services.teaching_calendar import parse_lecture_date
from app.services.form_bindings import reconcile_registration_usage_flags
from app.services.current_courses import current_course_query
from app.services.schedule_availability import current_schedule_availability, site_capture_assistance_enabled
from app.services.listening_assistant_contracts import SAFE_OVERRIDE_KEYS
from app.services.listening_assistant_guide_contracts import (
    MAX_GUIDED_FACT_LENGTH,
    MAX_GUIDED_QUESTIONS,
    GuidedAssistantState,
)
from app.services.listening_assistant_evidence import (
    AssistantSelectionError,
    create_evidence,
    revalidate_selection,
)
from app.services.listening_assistant_schedule import ScheduleSourceUnavailable
from app.services.lecture_form_draft_concurrency import LectureFormDraftConflict, save_lecture_form_draft_atomic
from app.services.registration_course_identity import resolve_registration_course
from sqlalchemy.orm.exc import StaleDataError
from datetime import datetime, timedelta
from app.utils.audit_tags import build_audit_tag, build_week_correction_tag
from app.utils.leave_management import append_leave_system_note, get_pending_leave_makeup, record_leave_makeup_form
from app.utils.permission_feedback import forbidden_json, flash_forbidden
import hashlib
import json
import re
import unicodedata

from . import user_bp


@user_bp.context_processor
def listening_assistant_page_defaults():
    return {'assistant_default_semester': get_current_teaching_semester(),
            'capture_enabled': current_app.config.get('LECTURE_CAPTURE_ENABLED',False)}


LECTURE_FORM_DRAFT_KEY = 'submit_form'

_ASSISTANT_DRAFT_KEYS = frozenset({
    'stage',
    'query',
    'rejected_ids',
    'source_kind',
    'source_batch_id',
    'semester',
    'candidate_id',
    'overrides',
    'template_version',
    'fallback_reason',
    'reason',
    'explicit_fallback',
    'acknowledged_source',
    'assistant_filled_fields',
    'assistant_filled_groups',
    'guide_state',
    'history',
})
_ASSISTANT_FILLED_FIELD_KEYS = frozenset({
    'lecture_date',
    'lecture_location',
    'teacher_name',
    'teacher_college',
    'course_title',
    'student_grade_class',
})
_ASSISTANT_FILLED_GROUP_KEYS = frozenset({'period'})
_ASSISTANT_QUERY_DRAFT_KEYS = frozenset({
    'lecture_date',
    'room',
    'teacher_name',
    'period',
    'student_grade_class',
    'semester',
})
_GUIDE_HISTORY_KEYS = frozenset({'kind', 'answer_code', 'custom_value'})
_GUIDE_KINDS = frozenset({'memory', 'date', 'teacher', 'room', 'period', 'student_grade_class'})
_GUIDE_ANSWER_CODES = frozenset({'A', 'B', 'C', 'D', 'NONE'})
# The entry choice and final course selection do not consume fact questions.
_MAX_GUIDE_HISTORY = MAX_GUIDED_QUESTIONS + 2
_GUIDE_CANDIDATE_ID_PATTERN = re.compile(r'^[0-9A-Za-z._:~\-]+$')
_GUIDE_PRIVATE_TEXT_PATTERN = re.compile(
    r'(?i)(password|passwd|token|secret|credential|凭据|密码|口令|签名|评价|反馈)'
)
_GUIDE_PHONE_PATTERN = re.compile(r'1[3-9]\d{9}')


def _contains_guide_sensitive_text(value):
    normalized = unicodedata.normalize('NFKC', value)
    compact = re.sub(r'[\s\-‐‑–—_+()（）]', '', normalized)
    return bool(
        _GUIDE_PRIVATE_TEXT_PATTERN.search(normalized)
        or _GUIDE_PHONE_PATTERN.search(compact)
        or re.search(r'(?<!\d)\d{7,}(?!\d)', normalized)
    )


def _is_draft_scalar(value):
    return isinstance(value, (str, int, float, bool)) or value is None


def _normalize_guide_history(value):
    if not isinstance(value, list):
        raise ValueError('assistant.history must be a list')
    if len(value) > _MAX_GUIDE_HISTORY:
        raise ValueError('assistant.history exceeds maximum count')

    normalized = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != _GUIDE_HISTORY_KEYS:
            raise ValueError(f'assistant.history[{index}] contains unsupported fields')
        kind = item['kind']
        answer_code = item['answer_code']
        if not isinstance(kind, str) or kind not in _GUIDE_KINDS:
            raise ValueError(f'assistant.history[{index}].kind is not allowed')
        if not isinstance(answer_code, str) or answer_code not in _GUIDE_ANSWER_CODES:
            raise ValueError(f'assistant.history[{index}].answer_code is not allowed')
        custom_value = item['custom_value']
        if custom_value is not None:
            if not isinstance(custom_value, str):
                raise ValueError(f'assistant.history[{index}].custom_value must be text or null')
            custom_value = unicodedata.normalize('NFKC', custom_value)
            if any(ord(char) < 32 or ord(char) == 127 for char in custom_value):
                raise ValueError(f'assistant.history[{index}].custom_value contains control characters')
            custom_value = ' '.join(custom_value.split()).strip()
            if not custom_value or len(custom_value) > MAX_GUIDED_FACT_LENGTH:
                raise ValueError(f'assistant.history[{index}].custom_value is invalid')
            if _contains_guide_sensitive_text(custom_value):
                raise ValueError(f'assistant.history[{index}].custom_value contains private text')
        normalized.append({
            'kind': kind,
            'answer_code': answer_code,
            'custom_value': custom_value,
        })
    return normalized


def _normalize_guide_state(value):
    if not isinstance(value, dict):
        raise ValueError('assistant.guide_state must be an object')
    try:
        state = GuidedAssistantState.from_public_dict(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f'invalid assistant.guide_state: {error}') from error
    if len([kind for kind in state.asked_question_kinds if kind != 'memory']) > MAX_GUIDED_QUESTIONS:
        raise ValueError('assistant.guide_state.asked_question_kinds exceeds maximum count')
    if any(
        not _GUIDE_CANDIDATE_ID_PATTERN.fullmatch(candidate_id)
        for candidate_id in state.candidate_ids
    ):
        raise ValueError('assistant.guide_state contains an invalid candidate id')
    return state.to_public_dict()


def _normalize_assistant_draft_payload(raw_payload):
    if not isinstance(raw_payload, dict):
        return None

    unknown = set(raw_payload) - _ASSISTANT_DRAFT_KEYS
    if unknown:
        raise ValueError('assistant payload contains unsupported fields')

    normalized = {}
    for key, value in raw_payload.items():
        if key in {'query', 'overrides'}:
            if not isinstance(value, dict):
                raise ValueError(f'assistant.{key} must be an object')
            nested = {}
            allowed_keys = (
                _ASSISTANT_QUERY_DRAFT_KEYS
                if key == 'query'
                else SAFE_OVERRIDE_KEYS
            )
            unknown_nested = set(value) - allowed_keys
            if unknown_nested:
                raise ValueError(f'assistant.{key} contains unsupported fields')
            for nested_key, nested_value in value.items():
                if _is_draft_scalar(nested_value):
                    nested[nested_key] = nested_value
                elif isinstance(nested_value, list):
                    if not all(_is_draft_scalar(item) for item in nested_value):
                        raise ValueError(f'assistant.{key}.{nested_key} contains unsupported values')
                    nested[nested_key] = nested_value[:20]
                else:
                    raise ValueError(f'assistant.{key}.{nested_key} must be scalar or list')
            normalized[key] = nested
            continue

        if key == 'guide_state':
            normalized[key] = _normalize_guide_state(value)
            continue

        if key == 'history':
            normalized[key] = _normalize_guide_history(value)
            continue

        if key in {'assistant_filled_fields', 'assistant_filled_groups'}:
            if not isinstance(value, list):
                continue
            allowed_keys = (
                _ASSISTANT_FILLED_FIELD_KEYS
                if key == 'assistant_filled_fields'
                else _ASSISTANT_FILLED_GROUP_KEYS
            )
            normalized[key] = [
                item for item in value
                if isinstance(item, str) and item in allowed_keys
            ]
            continue

        if key == 'rejected_ids':
            if not isinstance(value, list):
                continue
            normalized[key] = [item for item in value if _is_draft_scalar(item)]
            continue

        if _is_draft_scalar(value):
            normalized[key] = value

    if 'history' in normalized and 'guide_state' not in normalized:
        raise ValueError('assistant.history requires assistant.guide_state')

    guide_state = normalized.get('guide_state')
    if guide_state and guide_state['stage'] not in {'confirm', 'done'}:
        # A rewind/re-answer is a new provenance branch.  Keep the current
        # bounded guide state/history, but never carry a prior confirmation,
        # candidate snapshot, override, or assistant-filled marker forward.
        normalized['stage'] = guide_state['stage']
        for stale_key in (
            'query',
            'rejected_ids',
            'source_kind',
            'source_batch_id',
            'semester',
            'candidate_id',
            'overrides',
            'fallback_reason',
            'reason',
            'explicit_fallback',
            'acknowledged_source',
            'assistant_filled_fields',
            'assistant_filled_groups',
        ):
            normalized.pop(stale_key, None)

    return normalized


def _load_lecture_form_draft(user_id):
    return LectureFormDraft.query.filter_by(
        user_id=user_id,
        draft_key=LECTURE_FORM_DRAFT_KEY,
    ).first()


def _delete_lecture_form_draft(user_id):
    draft = _load_lecture_form_draft(user_id)
    if draft:
        db.session.delete(draft)
        return True
    return False


def _parse_draft_payload(draft):
    if not draft or not draft.payload_json:
        return {}
    try:
        payload = json.loads(draft.payload_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    try:
        normalized = _normalize_draft_payload(payload)
    except ValueError:
        # Preserve ordinary form fields when an older assistant payload has
        # keys that the current strict assistant namespace no longer accepts.
        # A stale assistant snapshot is disposable; the user's draft is not.
        legacy_payload = dict(payload)
        legacy_payload.pop('assistant', None)
        legacy_payload.pop('assistant_payload', None)
        try:
            normalized = _normalize_draft_payload(legacy_payload)
        except ValueError:
            return {}
    normalized = normalized if normalized is not None else {}
    if type(payload.get('site_capture_id')) is int and payload['site_capture_id'] > 0:
        normalized['site_capture_id']=payload['site_capture_id']
    return normalized


def _normalize_draft_payload(raw_payload):
    if not isinstance(raw_payload, dict):
        return None
    normalized = {}
    for key, value in raw_payload.items():
        if key == 'site_capture_id':
            continue
        if not isinstance(key, str):
            continue
        if key == 'assistant_payload':
            legacy_payload = None
            if isinstance(value, dict):
                legacy_payload = value
            elif isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    legacy_payload = parsed
            if legacy_payload is not None:
                assistant_payload = _normalize_assistant_draft_payload(legacy_payload)
                if assistant_payload:
                    normalized['assistant'] = assistant_payload
            continue
        if key == 'assistant':
            assistant_payload = _normalize_assistant_draft_payload(value)
            if assistant_payload:
                normalized[key] = assistant_payload
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized[key] = value
        elif isinstance(value, list):
            normalized[key] = [
                item for item in value
                if isinstance(item, (str, int, float, bool)) or item is None
            ]
    return normalized


def _extract_assistant_submission_payload():
    """Decode the optional form field used by Task 4 submit integration."""
    raw_values = [
        request.form.get('assistant_payload'),
        request.form.get('assistant'),
    ]
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if isinstance(body, dict):
            raw_values.extend([
                body.get('assistant_payload'),
                body.get('assistant'),
            ])

    raw_values = [value for value in raw_values if value not in (None, '')]
    if not raw_values:
        return None
    if len(raw_values) > 1 and raw_values[0] != raw_values[1]:
        raise ValueError('assistant payload was provided more than once')

    raw_value = raw_values[0]
    if isinstance(raw_value, str):
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError) as error:
            raise ValueError('assistant payload must be valid JSON') from error
    else:
        payload = raw_value
    if not isinstance(payload, dict):
        raise ValueError('assistant payload must be a JSON object')
    return payload


def _normalize_submission_value(value):
    if value is None:
        return ''
    return str(value).strip()


def _build_submission_signature(form_data):
    signature_fields = [
        'listener_number', 'course_changes', 'lecture_date', 'class_period', 'lecture_location',
        'teacher_name', 'teacher_college', 'course_title', 'student_grade_class', 'abnormal_situation',
        'teaching_method', 'classroom_discipline', 'classroom_atmosphere', 'courseware_quality',
        'overall_effect', 'quality_case', 'course_feedback', 'suggestions', 'student_signature1',
        'contact_phone1', 'student_signature2', 'contact_phone2', 'registration_id'
    ]
    signature_payload = {
        field: _normalize_submission_value(form_data.get(field)) for field in signature_fields
    }
    signature_text = json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(signature_text.encode('utf-8')).hexdigest()


def _find_recent_duplicate_submission(user_number, form_data, now_time):
    signature = _build_submission_signature(form_data)
    recent_forms = LectureForm.query.filter(
        LectureForm.listener_number == user_number,
        LectureForm.status == '待审核',
        LectureForm.created_at >= now_time - timedelta(seconds=120)
    ).order_by(LectureForm.created_at.desc(), LectureForm.id.desc()).limit(20).all()
    for form in recent_forms:
        candidate_data = {
            'listener_number': form.listener_number,
            'course_changes': form.course_changes,
            'lecture_date': form.lecture_date,
            'class_period': form.class_period,
            'lecture_location': form.lecture_location,
            'teacher_name': form.teacher_name,
            'teacher_college': form.teacher_college,
            'course_title': form.course_title,
            'student_grade_class': form.student_grade_class,
            'abnormal_situation': form.abnormal_situation,
            'teaching_method': form.teaching_method,
            'classroom_discipline': form.classroom_discipline,
            'classroom_atmosphere': form.classroom_atmosphere,
            'courseware_quality': form.courseware_quality,
            'overall_effect': form.overall_effect,
            'quality_case': form.quality_case,
            'course_feedback': form.course_feedback,
            'suggestions': form.suggestions,
            'student_signature1': form.student_signature1,
            'contact_phone1': form.contact_phone1,
            'student_signature2': form.student_signature2,
            'contact_phone2': form.contact_phone2,
            'registration_id': form.registration_id
        }
        if _build_submission_signature(candidate_data) == signature:
            return form
    return None


def _form_edit_data(form):
    fields = (
        'listener_name', 'listener_number', 'course_changes', 'lecture_date', 'class_period',
        'lecture_location', 'teacher_name', 'teacher_college', 'course_title', 'student_grade_class',
        'abnormal_situation', 'teaching_method', 'classroom_discipline', 'classroom_atmosphere',
        'courseware_quality', 'overall_effect', 'quality_case', 'course_feedback', 'suggestions',
        'student_signature1', 'contact_phone1', 'student_signature2', 'contact_phone2',
        'registration_id', 'review_comment',
    )
    data = {field: getattr(form, field) for field in fields}
    data['unique_id'] = form.unique_id or form.id
    lecture_date = parse_lecture_date(form.lecture_date)
    if lecture_date:
        data['lecture_date'] = lecture_date.isoformat()
        data['lecture_date_display'] = form.lecture_date
    period = re.fullmatch(r'第?(\d+)(?:-(\d+))?节?', form.class_period or '')
    data['start_period'] = period.group(1) if period else ''
    data['end_period'] = (period.group(2) or period.group(1)) if period else ''
    return data


def _render_submitted_form(user, existing_version=None):
    """Keep the user's posted inputs and edit context on a rejected submission."""
    submitted = request.form.to_dict(flat=True)
    if existing_version:
        submitted.setdefault('unique_id', str(existing_version.unique_id or existing_version.id))
        submitted.setdefault('review_comment', existing_version.review_comment)
    return render_template('user/lecture_form.html', user=user, form_data=submitted,
                           form=existing_version, edit_mode=existing_version is not None)


@user_bp.route('/api/lecture_form_draft', methods=['GET', 'PUT', 'DELETE'])
@submission_required(api=True, methods=('PUT', 'DELETE'))
def lecture_form_draft():
    user_id = session['user_id']

    if request.method == 'GET':
        draft = _load_lecture_form_draft(user_id)
        return jsonify({
            'success': True,
            'exists': draft is not None,
            'data': _parse_draft_payload(draft),
            'updated_at': draft.updated_at.strftime('%Y-%m-%d %H:%M:%S') if draft else None,
        })

    if request.method == 'DELETE':
        deleted = _delete_lecture_form_draft(user_id)
        db.session.commit()
        return jsonify({'success': True, 'deleted': deleted})

    body = request.get_json(silent=True) or {}
    raw_payload = body.get('data', body)
    try:
        payload = _normalize_draft_payload(raw_payload)
    except ValueError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    if payload is None:
        return jsonify({'success': False, 'message': 'Draft payload must be a JSON object'}), 400

    draft = _load_lecture_form_draft(user_id)
    existing = _parse_draft_payload(draft)
    if 'expected_site_capture_id' in body and body['expected_site_capture_id'] != existing.get('site_capture_id'):
        return jsonify(success=False,message='听课记录已切换，请刷新后继续填写。'),409
    if existing.get('site_capture_id'):
        payload['site_capture_id']=existing['site_capture_id']
    try:
        # The earlier expected-capture check is a useful message, but cannot
        # protect a request paused after reading A while another resumes B.
        # Claim the exact raw draft/version before touching its photo record.
        written_at=save_lecture_form_draft_atomic(user_id,LECTURE_FORM_DRAFT_KEY,draft,payload)
        if existing.get('site_capture_id'):
            capture=LectureSiteCapture.query.filter_by(id=existing['site_capture_id'],user_id=user_id,form_id=None).first()
            if capture:
                capture.draft_json=json.dumps(payload,ensure_ascii=False,sort_keys=True)
                if capture.confirmed_candidate_id and (payload.get('assistant') or {}).get('candidate_id')!=capture.confirmed_candidate_id:
                    capture.confirmed_candidate_id=None
        db.session.commit()
    except (LectureFormDraftConflict,StaleDataError):
        db.session.rollback()
        return jsonify(success=False,code='draft_conflict',message='听课草稿已更新或删除，请保留当前输入并刷新后继续填写。'),409
    except Exception:
        db.session.rollback()
        raise
    return jsonify({
        'success': True,
        'updated_at': written_at.strftime('%Y-%m-%d %H:%M:%S'),
    })


@user_bp.route('/my_forms')
@login_required
def my_forms():
    """Compatibility entry for the records tab in the activity center."""
    query_args = request.args.to_dict(flat=True)
    query_args['tab'] = 'records'
    return redirect(url_for('user.listening_registration', **query_args))


@user_bp.route('/delete_form/<int:form_id>', methods=['POST'])
@submission_required(api=True)
def delete_form(form_id):
    from app.models import LectureForm, ScoreRecord, CourseRegistration
    user = User.query.get(session['user_id'])
    form = LectureForm.query.get_or_404(form_id)

    if form.listener_number != user.number:
        return forbidden_json('我的表单', '只能删除自己提交的表单。', action='删除')

    unique_id = form.unique_id or form.id
    group_forms = LectureForm.query.filter(
        db.or_(LectureForm.unique_id == unique_id, LectureForm.id == unique_id)
    ).order_by(LectureForm.id.desc()).all()

    if len(group_forms) != 1:
        return jsonify({'success': False, 'message': '仅支持删除只有一个版本的表单'}), 400

    latest_form = group_forms[0]
    if latest_form.status != '待审核':
        return jsonify({'success': False, 'message': '仅支持删除待审核状态的表单'}), 400

    try:
        registration_id = latest_form.registration_id
        captures = LectureSiteCapture.query.filter_by(form_id=latest_form.id).all()
        for capture in captures:
            try:
                previous = json.loads(capture.draft_json or '{}')
            except (TypeError, ValueError):
                previous = {}
            payload = previous if isinstance(previous, dict) else {}
            # The current form contains the latest manual evaluation. Preserve
            # the independent photo and course-confirmation evidence for resume.
            payload.update(_form_edit_data(latest_form))
            payload.pop('unique_id', None)
            payload.pop('review_comment', None)
            payload['site_capture_id'] = capture.id
            capture.draft_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            capture.form_id = None

        score_record = ScoreRecord.query.filter_by(form_id=latest_form.id).first()
        if score_record:
            db.session.delete(score_record)
        db.session.delete(latest_form)
        db.session.flush()

        if registration_id:
            remains = LectureForm.query.filter_by(registration_id=registration_id).count()
            if remains == 0:
                registration = db.session.get(CourseRegistration, registration_id)
                if registration:
                    db.session.delete(registration)
                    for capture in captures:
                        payload = json.loads(capture.draft_json)
                        payload.pop('registration_id', None)
                        capture.draft_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to delete listener form and release its site records')
        return jsonify({'success': False, 'message': '表单删除失败，请稍后重试'}), 500
    return jsonify({'success': True, 'message': '表单删除成功'})


@user_bp.route('/submit_form', methods=['GET', 'POST'])
@submission_required()
def submit_form():
    """提交听课表单"""
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        from app.models import LectureForm
        from datetime import datetime
        import difflib

        site_capture = None
        version_id=request.form.get('unique_id')
        existing_version = LectureForm.query.filter(
            db.or_(LectureForm.unique_id == int(version_id), LectureForm.id == int(version_id)),
            LectureForm.listener_number == user.number,
        ).order_by(LectureForm.id.desc()).first() if version_id and version_id.isdigit() else None
        if current_app.config.get('LECTURE_CAPTURE_ENABLED') and not existing_version:
            capture_id=_parse_draft_payload(_load_lecture_form_draft(user.id)).get('site_capture_id')
            posted_capture=request.form.get('site_capture_id','')
            if capture_id and posted_capture and str(capture_id)!=posted_capture:
                flash('听课记录已切换，请刷新后提交。','error')
                return _render_submitted_form(user, existing_version),400
            capture_id=capture_id or (int(posted_capture) if posted_capture.isdigit() else None)
            site_capture=LectureSiteCapture.query.filter_by(id=capture_id,user_id=user.id,is_archived=False).first() if capture_id else None
            if not site_capture:
                flash('请先拍摄门牌并保存这次听课记录。','error')
                return _render_submitted_form(user, existing_version),400
            if request.form.get('lecture_date')!=site_capture.received_at[:10]:
                flash('听课日期与现场记录不一致，请核对后提交。','error')
                return _render_submitted_form(user, existing_version),400
            from app.services.listening_assistant_contracts import normalize_room
            if not site_capture.building or not site_capture.room_number or normalize_room(request.form.get('lecture_location')) != normalize_room(f'{site_capture.building}-{site_capture.room_number}'):
                flash('请先确认照片中的教室，并核对表单教室。','error')
                return _render_submitted_form(user, existing_version),400

        assistant_selection = None
        try:
            assistant_payload = _extract_assistant_submission_payload()
        except ValueError:
            db.session.rollback()
            flash('填表助手数据格式错误，请重新确认课程信息后提交。', 'error')
            return _render_submitted_form(user, existing_version), 400

        if assistant_payload is not None:
            try:
                assistant_selection = revalidate_selection(user, assistant_payload)
            except (AssistantSelectionError, ScheduleSourceUnavailable):
                # Revalidation happens before any form mutation.  Keep the
                # route's normal HTML/flash error shape and fail closed.
                db.session.rollback()
                flash('填表助手信息已失效，请重新搜索并确认课程后提交。', 'error')
                return _render_submitted_form(user, existing_version), 400

        if assistant_selection is not None:
            snapshot = assistant_selection.field_snapshot
            assistant_overrides = assistant_selection.overrides

            def _assistant_override_is_clear(*field_names):
                return any(
                    field_name in assistant_overrides
                    and assistant_overrides[field_name] is None
                    for field_name in field_names
                )

            def _assistant_or_form(field_name, *override_aliases):
                manual_value = request.form.get(field_name)
                if manual_value is not None and str(manual_value).strip():
                    return manual_value
                if _assistant_override_is_clear(field_name, *override_aliases):
                    return ''
                assistant_value = snapshot.get(field_name)
                if assistant_value is not None and str(assistant_value).strip():
                    return assistant_value
                return manual_value or ''

            manual_date = request.form.get('lecture_date_display')
            if manual_date is None or not str(manual_date).strip():
                manual_date = request.form.get('lecture_date')
            if manual_date is not None and str(manual_date).strip():
                lecture_date = manual_date
            elif _assistant_override_is_clear('lecture_date'):
                lecture_date = ''
            else:
                lecture_date = snapshot.get('lecture_date') or ''

            manual_period = request.form.get('class_period')
            if manual_period is not None and str(manual_period).strip():
                class_period = manual_period
            elif (
                str(request.form.get('start_period') or '').strip()
                or str(request.form.get('end_period') or '').strip()
            ):
                class_period = (
                    f"第{request.form.get('start_period', '')}-"
                    f"{request.form.get('end_period', '')}节"
                )
            elif _assistant_override_is_clear('class_period', 'period'):
                class_period = ''
            else:
                class_period = snapshot.get('class_period') or ''

            lecture_location = _assistant_or_form('lecture_location', 'room')
            teacher_name = _assistant_or_form('teacher_name')
            teacher_college = _assistant_or_form('teacher_college')
            course_title = _assistant_or_form('course_title')
            student_grade_class = _assistant_or_form('student_grade_class')
        else:
            # Preserve the legacy direct-submission path byte-for-byte in its
            # field access and fallback behavior when no assistant payload is
            # present.
            lecture_date = request.form.get('lecture_date_display') or request.form['lecture_date']
            class_period = request.form.get('class_period') or f"第{request.form.get('start_period', '')}-{request.form.get('end_period', '')}节"
            lecture_location = request.form['lecture_location']
            teacher_name = request.form['teacher_name']
            teacher_college = request.form['teacher_college']
            course_title = request.form['course_title']
            student_grade_class = request.form['student_grade_class']
        
        # 获取关联的登记ID
        registration_id = (request.form.get('registration_id') or '').strip() or None
        if registration_id is None and existing_version is not None:
            registration_id = existing_version.registration_id
        audit_tag = '需要人工审核' # 默认需要人工审核
        
        # 如果选择了已登记课程，先做服务端归属验证，再进行自动审核判断
        registration = None
        if registration_id:
            registration = Reservation.query.get(registration_id)
            if registration is None:
                flash('所选听课登记不存在或已被删除，请刷新后重试。', 'error')
                return _render_submitted_form(user, existing_version), 400
            if registration.user_id != user.id:
                flash('无权使用该听课登记。', 'error')
                return _render_submitted_form(user, existing_version), 403

            applicable_course = resolve_registration_course(registration, current_only=True)
            # Existing owned reservations remain valid binding facts even when
            # their old Course is missing/retired. Only current authority may
            # supply a course-check label; history must not prove a new course.

            # 获取原始课程信息（仅用于自动审核相似度计算；Course 缺失不改变
            # HTTP 行为，也不影响 registration 绑定，audit_tag 保持默认人工审核）
            course = applicable_course

            if course:
                # 比较关键字段差异
                # 1. 课程名称
                s1 = difflib.SequenceMatcher(None, course.course_name, course_title)
                ratio_title = s1.ratio()

                # 2. 教师姓名
                teacher_name_orig = course.teacher.name if course.teacher else ''
                s2 = difflib.SequenceMatcher(None, teacher_name_orig, teacher_name)
                ratio_teacher = s2.ratio()

                # 3. 上课地点
                s3 = difflib.SequenceMatcher(None, str(course.class_location or ''), lecture_location)
                ratio_location = s3.ratio()

                # 综合判断：如果关键信息相似度较高，则无需人工审核
                # 设定阈值为0.6（允许少量修改）
                if ratio_title > 0.6 and ratio_teacher > 0.6 and ratio_location > 0.6:
                    audit_tag = '无需人工审核'
                else:
                    audit_tag = '需要人工审核' # 修改较大

        teaching_method = request.form['teaching_method']
        courseware_quality = request.form['courseware_quality']
        if 'PPT演示法' not in teaching_method:
            courseware_quality = '无'

        suggestions = request.form.get('suggestions', '无')
        leave_makeup = get_pending_leave_makeup(user)
        if leave_makeup:
            leave_week = leave_makeup['leave_week']
            audit_tag = build_audit_tag(audit_tag, build_week_correction_tag(leave_week))
            suggestions = append_leave_system_note(suggestions, leave_week)

        # 准备表单数据
        form_data = {
            'listener_name': f"{user.name}（{user.college}）",
            'listener_number': user.number,
            'course_changes': request.form.get('course_changes', '无'),
            'lecture_date': lecture_date,
            'class_period': class_period,
            'lecture_location': lecture_location,
            'teacher_name': teacher_name,
            'teacher_college': teacher_college,
            'course_title': course_title,
            'student_grade_class': student_grade_class,
            'abnormal_situation': request.form.get('abnormal_situation', '无'),
            'teaching_method': teaching_method,
            'classroom_discipline': request.form['classroom_discipline'],
            'classroom_atmosphere': request.form['classroom_atmosphere'],
            'courseware_quality': courseware_quality,
            'overall_effect': request.form['overall_effect'],
            'quality_case': request.form['quality_case'],
            'course_feedback': request.form['course_feedback'],
            'suggestions': suggestions,
            'student_signature1': request.form['student_signature1'],
            'contact_phone1': request.form['contact_phone1'],
            'student_signature2': request.form.get('student_signature2'),
            'contact_phone2': request.form.get('contact_phone2'),
            'registration_id': registration_id,
            'audit_tag': audit_tag,
            'status': '待审核' # 提交后默认为待审核
        }
        
        try:
            # Round 8A：is_used 是兼容 mirror，不再由 submit 直接假定置 True；
            # 绑定事实以 LectureForm.registration_id 实际 graph 为准，在表单
            # mutation 落库后由 form_bindings.reconcile_registration_usage_flags
            # 统一同步（旧/新 registration 都参与，事务内、单 commit）。
            registration_ids_to_reconcile = set()

            unique_id = request.form.get('unique_id')
            target_form = None
            is_new_version = True

            if unique_id and unique_id.strip():
                # 检查是否存在可更新的最新版本
                latest_form = existing_version

                # 如果最新版本存在，且状态为'待审核'，且属于当前用户，则更新该版本
                if latest_form and latest_form.status == '待审核' and latest_form.listener_number == user.number:
                    target_form = latest_form
                    is_new_version = False

                    # Round 8A §15：字段覆盖前先记录旧 binding——rebind 后新旧
                    # registration 都参与 reconcile（旧 registration 可能仍被
                    # 其它 LectureForm 引用，不得手写 old.is_used=False）。
                    registration_ids_to_reconcile.add(target_form.registration_id)

                    # 更新字段
                    for key, value in form_data.items():
                        if hasattr(target_form, key):
                            setattr(target_form, key, value)
                    target_form.updated_at = datetime.now()
                else:
                    # 创建新版本
                    target_form = LectureForm(**form_data)
                    target_form.unique_id = unique_id
            else:
                # 全新表单
                duplicate_form = _find_recent_duplicate_submission(user.number, form_data, datetime.now())
                if site_capture and site_capture.form_id and (not duplicate_form or duplicate_form.id!=site_capture.form_id):
                    db.session.rollback()
                    flash('这次现场记录已提交，请新建听课记录。','error')
                    return _render_submitted_form(user, existing_version),400
                if duplicate_form:
                    if site_capture:
                        site_capture.form_id=duplicate_form.id
                    # Outcome B（duplicate accepted）：draft DELETE + optional
                    # leave makeup + registration mirror reconciliation →
                    # 恰好一次 commit，不因 leave_makeup 缺失而省略（P2）。
                    # Round 8A：duplicate 签名含 registration_id，existing form
                    # 的 binding 与请求一致——reconciliation 在同事务内修复
                    # legacy is_used 漂移（actual binding True → mirror True）。
                    _delete_lecture_form_draft(user.id)
                    if leave_makeup:
                        record_leave_makeup_form(
                            user,
                            leave_makeup,
                            duplicate_form,
                            source='auto',
                            operator_user_id=user.id,
                        )
                    db.session.flush()
                    if assistant_selection is not None:
                        create_evidence(user, duplicate_form, assistant_selection)
                    registration_ids_to_reconcile.add(duplicate_form.registration_id)
                    reconcile_registration_usage_flags(registration_ids_to_reconcile)
                    db.session.commit()
                    flash('检测到重复提交，系统已保留首次提交结果。', 'info')
                    return redirect(url_for('user.success', form_id=duplicate_form.id))
                target_form = LectureForm(**form_data)

            # 新 binding 记入 reconcile 集合（rebind 时与旧 id 一并处理）。
            registration_ids_to_reconcile.add(form_data.get('registration_id'))

            db.session.add(target_form)

            if is_new_version:
                db.session.flush() # 获取ID
                if not target_form.unique_id:
                    target_form.unique_id = target_form.id

            # Round 8A §12：显式 flush 使本事务内的 binding graph（新 form /
            # rebind）对 reconcile 可见——不依赖偶然 autoflush。
            db.session.flush()
            if assistant_selection is not None:
                create_evidence(user, target_form, assistant_selection)
            if site_capture:
                site_capture.form_id=target_form.id
            reconcile_registration_usage_flags(registration_ids_to_reconcile)

            if leave_makeup:
                record_leave_makeup_form(
                    user,
                    leave_makeup,
                    target_form,
                    source='auto',
                        operator_user_id=user.id,
                )

            _delete_lecture_form_draft(user.id)

            # Outcome A（normal accepted）：表单变更 + registration mirror
            # reconciliation + optional leave makeup + draft DELETE → 单次业务 commit。
            db.session.commit()
            flash(f'表单提交成功！{" " if is_new_version else "原有表单已更新，等待重新审核。"}', 'success')
            # 跳转到成功页面，显示提交的表单详情
            return redirect(url_for('user.success', form_id=target_form.id))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{str(e)}', 'error')
            return _render_submitted_form(user, existing_version)
    
    return render_template('user/lecture_form.html', user=user)


@user_bp.route('/form/edit/<int:form_id>')
@submission_required()
def edit_form(form_id):
    """编辑/重填表单"""
    from app.models import LectureForm
    import json
    
    user = User.query.get(session['user_id'])
    form = LectureForm.query.get_or_404(form_id)
    
    # 只能编辑自己的表单
    if form.listener_number != user.number:
        flash_forbidden('我的表单', '只能编辑自己提交的表单。', action='编辑')
        return redirect(url_for('user.my_forms'))
    
    # 构建表单数据字典
    form_data = _form_edit_data(form)
        
    # 移除备注拆分逻辑
    # if '【备注】' in form.suggestions:
    #     parts = form.suggestions.split('【备注】')
    #     form_data['suggestions'] = parts[0]
    #     form_data['remarks'] = parts[1] if len(parts) > 1 else ''
    
    return render_template('user/lecture_form.html', user=user, form_data=form_data, form=form)


@user_bp.route('/success/<int:form_id>')
@login_required
def success(form_id):
    """表单提交成功页面"""
    from app.models import LectureForm
    user = User.query.get(session['user_id'])
    
    # 只能查看自己提交的表单
    form = LectureForm.query.filter_by(id=form_id, listener_number=user.number).first_or_404()
    
    return render_template('success.html', form=form)


@user_bp.route('/view_form/<int:form_id>')
@login_required
def view_form(form_id):
    """查看表单详情"""
    from app.models import LectureForm
    user = User.query.get(session['user_id'])
    
    # 只能查看自己提交的表单
    form = LectureForm.query.filter_by(id=form_id, listener_number=user.number).first_or_404()
    
    return redirect(url_for('admin.get_form_detail', form_id=form.id))
