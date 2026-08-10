"""Permission-safe JSON APIs for automated review evidence."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime

from flask import Blueprint, current_app, g, jsonify, request, url_for
from sqlalchemy import func

from app.models import LectureForm, User, db

from .contracts import BatchStatus, DatasetStatus, ReviewCategory, ScheduleCoverage
from .models import (
    AutomationAuditLog,
    ReviewAssessment,
    ReviewBatch,
    ReviewFinding,
    ReviewRuleRevision,
    ScheduleDataset,
    ScheduleImportIssue,
)
from .permissions import (
    api_login_required,
    is_center_reviewer,
    require_center_reviewer,
    require_super_admin,
    reviewable_user_ids,
)
from .rules.registry import RuleValidationError, validate_rule_revision


review_automation_bp = Blueprint('review_automation', __name__)

_HEALTH_TIMEOUT_SECONDS = 0.25
_DATASET_KINDS = {'school', 'class_mapping', 'personal'}
_PROTECTED_FORM_FIELDS = ('status', 'reviewer_id', 'review_time', 'review_comment')


def _safe_json(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or '')
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _safe_message(message, fallback):
    """Never expose configuration URLs, credentials, or raw exception text."""

    if not message:
        return fallback
    text = str(message)
    lowered = text.lower()
    if any(marker in lowered for marker in ('api_key', 'authorization', 'password', 'token', 'secret')):
        return fallback
    return text[:240]


def _probe_redis():
    """Optionally probe Redis with a bounded timeout; tests do not enable probes."""

    if not current_app.config.get('AUTOMATION_HEALTH_PROBE', False):
        return 'unchecked'
    broker_url = current_app.config.get('CELERY_BROKER_URL')
    if not broker_url:
        return 'missing'
    try:
        import redis

        client = redis.Redis.from_url(
            broker_url,
            socket_connect_timeout=_HEALTH_TIMEOUT_SECONDS,
            socket_timeout=_HEALTH_TIMEOUT_SECONDS,
            retry_on_timeout=False,
        )
        client.ping()
        return 'ready'
    except Exception:
        return 'unavailable'


def _health_payload():
    api_key = current_app.config.get('DEEPSEEK_API_KEY')
    return {
        'success': True,
        'services': {
            'redis': _probe_redis(),
            'worker': 'configured' if current_app.config.get('CELERY_BROKER_URL') else 'missing',
            'deepseek': 'configured' if api_key else 'missing',
        },
    }


@review_automation_bp.get('/admin/api/automation/health')
@api_login_required
def health():
    return jsonify(_health_payload())


def _latest_rule_rows():
    rows = ReviewRuleRevision.query.order_by(
        ReviewRuleRevision.rule_key.asc(),
        ReviewRuleRevision.version.desc(),
        ReviewRuleRevision.id.desc(),
    ).all()
    latest = {}
    for row in rows:
        latest.setdefault(row.rule_key, row)
    return list(latest.values())


def _rule_payload(row):
    return {
        'id': row.id,
        'rule_key': row.rule_key,
        'version': row.version,
        'handler': row.handler,
        'enabled': bool(row.enabled),
        'severity': row.severity,
        'parameters': _safe_json(row.parameters_json, {}),
        'change_reason': row.change_reason or '',
        'created_by': row.created_by,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


@review_automation_bp.get('/admin/api/automation/rules')
@require_super_admin
def list_rules():
    return jsonify({'success': True, 'rules': [_rule_payload(row) for row in _latest_rule_rows()]})


@review_automation_bp.post('/admin/api/automation/rules/<rule_key>/revisions')
@require_super_admin
def create_rule_revision(rule_key):
    data = request.get_json(silent=True) or {}
    latest = next((row for row in _latest_rule_rows() if row.rule_key == rule_key), None)
    handler = str(data.get('handler') or (latest.handler if latest else '')).strip()
    severity = str(data.get('severity') or (latest.severity if latest else 'review')).strip()
    parameters = data.get('parameters', {})
    revision = {
        'rule_key': str(rule_key).strip(),
        'version': (latest.version + 1) if latest else 1,
        'handler': handler,
        'enabled': bool(data.get('enabled', True)),
        'severity': severity,
        'parameters': parameters,
    }
    try:
        validated = validate_rule_revision(revision)
    except (RuleValidationError, TypeError, ValueError) as exc:
        return jsonify({
            'success': False,
            'code': getattr(exc, 'code', 'invalid_rule_revision'),
            'message': _safe_message(exc, '规则配置无效'),
        }), 400

    row = ReviewRuleRevision(
        rule_key=validated.rule_key,
        version=validated.version,
        handler=validated.handler,
        enabled=validated.enabled,
        severity=validated.severity,
        parameters_json=json.dumps(validated.parameters, ensure_ascii=False, sort_keys=True),
        created_by=g.automation_user.id,
        change_reason=str(data.get('change_reason') or '')[:255],
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(AutomationAuditLog(
        actor_user_id=g.automation_user.id,
        action='create_rule_revision',
        target_type='ReviewRuleRevision',
        target_id=str(row.id),
        details_json=json.dumps({'rule_key': row.rule_key, 'version': row.version}, ensure_ascii=False),
    ))
    db.session.commit()
    return jsonify({'success': True, 'rule': _rule_payload(row)}), 201


def _dataset_payload(dataset, issue_counts=None):
    summary = _safe_json(dataset.summary_json, {})
    return {
        'id': dataset.id,
        'kind': dataset.kind,
        'semester': dataset.semester,
        'sha256': dataset.sha256,
        'original_filename': dataset.original_filename,
        'status': dataset.status,
        'row_count': dataset.row_count,
        'error_count': dataset.error_count,
        'issue_count': int((issue_counts or {}).get(dataset.id, dataset.error_count or 0)),
        'summary': summary,
        'created_by': dataset.created_by,
        'created_at': dataset.created_at.isoformat() if dataset.created_at else None,
        'updated_at': dataset.updated_at.isoformat() if dataset.updated_at else None,
    }


def _normalize_dataset_kind(kind):
    from .schedules.importer import normalize_dataset_kind

    return normalize_dataset_kind(kind)


@review_automation_bp.post('/admin/api/automation/datasets/<kind>/preview')
@require_super_admin
def preview_dataset_api(kind):
    if 'file' not in request.files:
        return jsonify({'success': False, 'code': 'file_required', 'message': '请选择课表文件'}), 400
    semester = str(request.form.get('semester') or '').strip()
    if not semester:
        return jsonify({'success': False, 'code': 'semester_required', 'message': '请填写学年学期'}), 400
    try:
        from .schedules.importer import preview_dataset

        dataset = preview_dataset(
            kind,
            semester,
            request.files['file'].stream,
            request.files['file'].filename,
            g.automation_user.id,
        )
    except Exception as exc:
        return jsonify({
            'success': False,
            'code': getattr(exc, 'code', 'dataset_preview_failed'),
            'message': _safe_message(exc, '课表预览失败'),
        }), 400
    return jsonify({'success': True, 'dataset': _dataset_payload(dataset)}), 201


@review_automation_bp.post('/admin/api/automation/datasets/<dataset_id>/activate')
@require_super_admin
def activate_dataset_api(dataset_id):
    try:
        from .schedules.repository import activate_dataset

        dataset = activate_dataset(dataset_id, g.automation_user.id)
    except KeyError:
        return jsonify({'success': False, 'code': 'dataset_not_found', 'message': '课表版本不存在'}), 404
    except Exception as exc:
        return jsonify({
            'success': False,
            'code': getattr(exc, 'code', 'dataset_activation_failed'),
            'message': _safe_message(exc, '课表启用失败'),
        }), 400
    return jsonify({'success': True, 'dataset': _dataset_payload(dataset)}), 200


@review_automation_bp.get('/admin/api/automation/datasets')
@require_super_admin
def list_datasets():
    datasets = ScheduleDataset.query.order_by(ScheduleDataset.created_at.desc()).all()
    counts = dict(
        db.session.query(ScheduleImportIssue.dataset_id, func.count(ScheduleImportIssue.id))
        .group_by(ScheduleImportIssue.dataset_id)
        .all()
    )
    return jsonify({
        'success': True,
        'datasets': [_dataset_payload(dataset, counts) for dataset in datasets],
        'kinds': sorted(_DATASET_KINDS),
    })


def _form_identity_values(user_ids):
    users = User.query.filter(User.id.in_(list(user_ids))).all() if user_ids else []
    identities = set()
    for user in users:
        if user.number:
            identities.add(str(user.number))
        if user.student_id:
            identities.add(str(user.student_id))
    return identities


def _coerce_form_ids(values):
    if not isinstance(values, (list, tuple, set)):
        return []
    result = []
    for value in values:
        try:
            form_id = int(value)
        except (TypeError, ValueError):
            continue
        if form_id > 0:
            result.append(form_id)
    return sorted(set(result))


def _latest_accessible_forms(form_ids, actor_id):
    requested = _coerce_form_ids(form_ids)
    identities = _form_identity_values(reviewable_user_ids(actor_id))
    if not requested or not identities:
        return requested, [], []
    forms = LectureForm.query.filter(LectureForm.listener_number.in_(identities)).all()
    requested_forms = [form for form in forms if form.id in requested]
    selected_keys = {
        ('unique', form.unique_id) if form.unique_id is not None else ('form', form.id)
        for form in requested_forms
    }
    latest = {}
    for form in forms:
        key = ('unique', form.unique_id) if form.unique_id is not None else ('form', form.id)
        if key not in selected_keys:
            continue
        current = latest.get(key)
        stamp = (form.updated_at or form.created_at)
        current_stamp = (current.updated_at or current.created_at) if current else None
        if current is None or (stamp or datetime.min, form.id) > (current_stamp or datetime.min, current.id):
            latest[key] = form
    selected = sorted(latest.values(), key=lambda form: form.id)
    accepted_ids = {form.id for form in requested_forms}
    ignored = [form_id for form_id in requested if form_id not in accepted_ids]
    return requested, selected, ignored


def latest_assessment_summaries(form_ids):
    """Bulk-load latest assessment summaries and finding counts for queue APIs."""

    ids = _coerce_form_ids(form_ids)
    if not ids:
        return {}
    rows = ReviewAssessment.query.filter(ReviewAssessment.form_id.in_(ids)).order_by(
        ReviewAssessment.form_id.asc(),
        ReviewAssessment.created_at.desc(),
        ReviewAssessment.id.desc(),
    ).all()
    latest = {}
    for row in rows:
        latest.setdefault(row.form_id, row)
    counts = dict(
        db.session.query(ReviewFinding.assessment_id, func.count(ReviewFinding.id))
        .filter(ReviewFinding.assessment_id.in_([row.id for row in latest.values()]))
        .group_by(ReviewFinding.assessment_id)
        .all()
    )
    return {
        form_id: {
            'assessment_id': row.id,
            'category': row.classification,
            'coverage': row.coverage,
            'finding_count': int(counts.get(row.id, 0)),
            'created_at': row.created_at.isoformat() if row.created_at else None,
            'evidence_url': url_for('review_automation.get_assessment', assessment_id=row.id),
        }
        for form_id, row in latest.items()
    }


def _coverage_counts(summaries, form_ids):
    counts = Counter({item.value: 0 for item in ScheduleCoverage})
    for form_id in form_ids:
        coverage = (summaries.get(form_id) or {}).get('coverage', ScheduleCoverage.NONE.value)
        counts[coverage if coverage in counts else ScheduleCoverage.NONE.value] += 1
    return counts


def _transfer_acknowledged(data):
    return bool(
        data.get('external_transfer_acknowledged')
        or data.get('external_transfer_ack')
        or data.get('deepseek_transfer_acknowledged')
    )


def _llm_options(data):
    enabled = bool(data.get('llm_enabled', False))
    if enabled and not _transfer_acknowledged(data):
        return None, jsonify({
            'success': False,
            'code': 'external_transfer_ack_required',
            'message': '启用 DeepSeek 前必须确认完整表单和相关证据会发送到外部模型服务',
        }), 400
    return enabled, None, None


def _batch_preview_payload(data, actor_id):
    llm_enabled, error, status = _llm_options(data)
    if error is not None:
        return None, error, status
    requested, forms, ignored = _latest_accessible_forms(data.get('form_ids', []), actor_id)
    summaries = latest_assessment_summaries([form.id for form in forms])
    coverage = _coverage_counts(summaries, [form.id for form in forms])
    stats = {
        'requested': len(requested),
        'processable': len(forms),
        'ignored': len(ignored),
        'latest_form_ids': [form.id for form in forms],
        'cache_reusable': sum(1 for form in forms if form.id in summaries),
        'basic': coverage[ScheduleCoverage.BASIC.value],
        'complete': coverage[ScheduleCoverage.COMPLETE.value],
        'missing': coverage[ScheduleCoverage.NONE.value],
        'coverage': dict(coverage),
        'llm_enabled': llm_enabled,
        'external_transfer_required': llm_enabled,
    }
    return {'stats': stats, 'form_ids': [form.id for form in forms]}, None, None


@review_automation_bp.post('/admin/api/automation/batches/preview')
@require_center_reviewer
def preview_batches():
    data = request.get_json(silent=True) or {}
    payload, error, status = _batch_preview_payload(data, g.automation_user.id)
    if error is not None:
        return error, status
    return jsonify({'success': True, **payload, 'legacy_force_ignored': False})


def create_batch_response(*, legacy_force_ignored=False, actor_id=None):
    data = request.get_json(silent=True) or {}
    actor_id = actor_id or g.automation_user.id
    payload, error, status = _batch_preview_payload(data, actor_id)
    if error is not None:
        return error, status
    if not payload['form_ids']:
        return jsonify({'success': False, 'code': 'no_reviewable_forms', 'message': '没有可审核范围内的最新表单'}), 400
    try:
        from .tasks.review import enqueue_review_batch

        batch = enqueue_review_batch(
            payload['form_ids'],
            requester_id=actor_id,
            force_refresh=bool(data.get('force_refresh', False)),
            llm_enabled=bool(payload['stats']['llm_enabled']),
            semester=data.get('semester'),
            semester_monday=data.get('semester_monday'),
        )
    except Exception:
        return jsonify({'success': False, 'code': 'batch_enqueue_failed', 'message': '批次创建失败'}), 503
    return jsonify({
        'success': True,
        'batch_id': batch.id,
        'status': batch.status,
        'target_form_count': batch.target_form_count,
        'form_ids': payload['form_ids'],
        'llm_enabled': bool(payload['stats']['llm_enabled']),
        'legacy_force_ignored': bool(legacy_force_ignored),
    }), 201


@review_automation_bp.post('/admin/api/automation/batches')
@require_center_reviewer
def create_batch():
    return create_batch_response()


def _batch_payload(batch):
    snapshot = _safe_json(batch.snapshot_json, {})
    return {
        'batch_id': batch.id,
        'status': batch.status,
        'target_form_count': batch.target_form_count,
        'processed_count': snapshot.get('processed_count', 0),
        'clear_count': batch.clear_count,
        'review_count': batch.review_count,
        'high_risk_count': batch.high_risk_count,
        'unknown_count': batch.unknown_count,
        'failed_count': batch.failed_count,
        'cache_count': batch.cache_count,
        'cancel_requested': bool(batch.cancel_requested),
        'results': snapshot.get('results', []),
        'created_at': batch.created_at.isoformat() if batch.created_at else None,
        'started_at': batch.started_at.isoformat() if batch.started_at else None,
        'finished_at': batch.finished_at.isoformat() if batch.finished_at else None,
    }


def _load_batch_or_404(batch_id):
    batch = db.session.get(ReviewBatch, str(batch_id))
    if batch is None:
        return None, (jsonify({'success': False, 'code': 'batch_not_found', 'message': '批次不存在'}), 404)
    if not is_center_reviewer(g.automation_user.id):
        return None, (jsonify({'success': False, 'code': 'forbidden', 'message': '需要审表_中心权限'}), 403)
    return batch, None


@review_automation_bp.get('/admin/api/automation/batches/<batch_id>')
@require_center_reviewer
def get_batch(batch_id):
    batch, error = _load_batch_or_404(batch_id)
    if error is not None:
        return error
    return jsonify({'success': True, 'batch': _batch_payload(batch)})


@review_automation_bp.post('/admin/api/automation/batches/<batch_id>/cancel')
@require_center_reviewer
def cancel_batch(batch_id):
    batch, error = _load_batch_or_404(batch_id)
    if error is not None:
        return error
    from .tasks.review import cancel_review_batch

    cancel_review_batch(batch.id)
    db.session.refresh(batch)
    return jsonify({'success': True, 'batch': _batch_payload(batch)})


def _form_is_reviewable(form, actor_id):
    identities = _form_identity_values(reviewable_user_ids(actor_id))
    return bool(form and form.listener_number in identities)


def _assessment_accessible(assessment, actor_id):
    return bool(assessment and _form_is_reviewable(assessment.form, actor_id))


def _assessment_detail_payload(assessment):
    return {
        'assessment_id': assessment.id,
        'form_id': assessment.form_id,
        'form_version': assessment.form_version,
        'category': assessment.classification,
        'coverage': assessment.coverage,
        'fingerprint': assessment.fingerprint,
        'model_id': assessment.model_id,
        'prompt_version': assessment.prompt_version,
        'schedule_version': _safe_json(assessment.schedule_version, {}),
        'rule_version': _safe_json(assessment.rule_version, {}),
        'model_result': _safe_json(assessment.validated_model_json, {}),
        'suggested_comment': assessment.suggested_comment or '',
        'error_code': assessment.error_code,
        'findings': [
            {
                'id': finding.id,
                'source': finding.source,
                'rule_key': finding.rule_key,
                'severity': finding.severity,
                'title': finding.title,
                'message': finding.message,
                'objective': bool(finding.objective),
                'evidence_strength': finding.evidence_strength,
                'raw_value_excerpt': finding.raw_value_excerpt,
                'comparison_value_excerpt': finding.comparison_value_excerpt,
                'evidence': _safe_json(finding.evidence_json, {}),
                'created_at': finding.created_at.isoformat() if finding.created_at else None,
            }
            for finding in assessment.findings
        ],
        'created_at': assessment.created_at.isoformat() if assessment.created_at else None,
    }


def _latest_form(form):
    if form.unique_id is None:
        return form
    return max(
        LectureForm.query.filter_by(unique_id=form.unique_id).all(),
        key=lambda item: (item.updated_at or item.created_at or datetime.min, item.id),
    )


@review_automation_bp.get('/admin/api/automation/forms/<int:form_id>/assessment')
@require_center_reviewer
def get_form_assessment(form_id):
    form = db.session.get(LectureForm, form_id)
    if form is None:
        return jsonify({'success': False, 'code': 'form_not_found', 'message': '表单不存在'}), 404
    latest = _latest_form(form)
    if not _form_is_reviewable(latest, g.automation_user.id):
        return jsonify({'success': False, 'code': 'forbidden', 'message': '您没有权限查看此表单'}), 403
    summary = latest_assessment_summaries([latest.id]).get(latest.id)
    return jsonify({'success': True, 'assessment': summary})


@review_automation_bp.get('/admin/api/automation/assessments/<assessment_id>')
@require_center_reviewer
def get_assessment(assessment_id):
    assessment = db.session.get(ReviewAssessment, str(assessment_id))
    if assessment is None:
        return jsonify({'success': False, 'code': 'assessment_not_found', 'message': '审核结果不存在'}), 404
    if not _assessment_accessible(assessment, g.automation_user.id):
        return jsonify({'success': False, 'code': 'forbidden', 'message': '您没有权限查看此审核结果'}), 403
    return jsonify({'success': True, 'assessment': _assessment_detail_payload(assessment)})


def _compat_user_error(user_id):
    if not is_center_reviewer(user_id):
        return jsonify({'success': False, 'code': 'forbidden', 'message': '需要审表_中心权限'}), 403
    return None


def preview_batch_response(*, legacy_force_ignored=False, actor_id=None):
    data = request.get_json(silent=True) or {}
    actor_id = actor_id or g.automation_user.id
    payload, error, status = _batch_preview_payload(data, actor_id)
    if error is not None:
        return error, status
    return jsonify({'success': True, **payload, 'legacy_force_ignored': bool(legacy_force_ignored)})


def status_batch_response(batch_id=None, actor_id=None):
    actor_id = actor_id or g.automation_user.id
    batch = db.session.get(ReviewBatch, str(batch_id)) if batch_id else ReviewBatch.query.filter_by(
        requester_id=actor_id,
    ).order_by(ReviewBatch.created_at.desc()).first()
    if batch is None:
        return jsonify({'success': True, 'status': 'idle', 'batch': None, 'legacy_force_ignored': True})
    return jsonify({'success': True, 'status': batch.status, 'batch': _batch_payload(batch), 'legacy_force_ignored': True})


__all__ = [
    'create_batch',
    'create_batch_response',
    'get_assessment',
    'get_batch',
    'health',
    'latest_assessment_summaries',
    'preview_batch_response',
    'preview_batches',
    'review_automation_bp',
    'status_batch_response',
]
