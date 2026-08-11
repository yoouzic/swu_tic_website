"""ID-only Celery batch tasks for immutable automated assessments."""

from __future__ import annotations

import json
from datetime import date, datetime
from inspect import signature
from typing import Any, Mapping

from celery import chord, group

from app.app import app as flask_app
from app.models import User, db

from .. import get_celery_app
from ..contracts import BatchStatus, ReviewCategory, ReviewMode
from ..llm.client import (
    InvalidLLMResponse,
    PermanentLLMError,
    TransientLLMError,
)
from ..llm.client import DeepSeekReviewClient
from ..llm.prompts import PROMPT_VERSION
from ..models import ReviewAssessment, ReviewBatch, ReviewBatchItem, ReviewRuleRevision
from ..service import AssessmentService
from ..schedules.importer import CLASS_MAPPING_KIND, PERSONAL_KIND, SCHOOL_KIND
from ..schedules.repository import (
    find_school_candidates_for_dataset,
    get_active_dataset,
    get_listener_schedule_for_datasets,
)


celery_app = get_celery_app(flask_app)
TASK_TRANSIENT_RETRIES = 1
TRANSIENT_ERROR_CODES = {
    'timeout',
    'rate_limited',
    'server_error',
    'connection_error',
    'retry_exhausted',
}


def _dataset_snapshot(semester):
    result = {}
    for kind in (SCHOOL_KIND, CLASS_MAPPING_KIND, PERSONAL_KIND):
        dataset = get_active_dataset(kind, semester) if semester else None
        if dataset is None:
            result[kind] = None
            continue
        stamp = dataset.updated_at or dataset.created_at
        result[kind] = {
            'id': dataset.id,
            'sha256': dataset.sha256,
            'version': stamp.isoformat() if hasattr(stamp, 'isoformat') else str(stamp),
        }
    return result


def _rule_snapshot():
    latest = {}
    rows = ReviewRuleRevision.query.order_by(
        ReviewRuleRevision.rule_key.asc(),
        ReviewRuleRevision.version.desc(),
        ReviewRuleRevision.id.desc(),
    ).all()
    for row in rows:
        if row.rule_key in latest:
            continue
        try:
            parameters = json.loads(row.parameters_json or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            parameters = {}
        if not isinstance(parameters, dict):
            parameters = {}
        latest[row.rule_key] = {
            'id': int(row.id),
            'version': int(row.version),
            'handler': row.handler,
            'enabled': bool(row.enabled),
            'severity': row.severity,
            'parameters': parameters,
        }
    return latest


def _profile_for_form(form):
    listener_number = getattr(form, 'listener_number', None)
    if not listener_number:
        return None
    profile = User.query.filter_by(number=str(listener_number)).first()
    if profile is None:
        profile = User.query.filter_by(student_id=str(listener_number)).first()
    return profile


def build_default_service(config=None):
    """Build a worker-local service without constructing a client for missing keys."""
    config = dict(config or {})
    dataset_refs = config.get('schedule_datasets') or {}
    semester = config.get('semester')
    semester_monday = config.get('semester_monday')
    api_key = flask_app.config.get('DEEPSEEK_API_KEY', '')
    client = None
    if api_key:
        client = DeepSeekReviewClient(
            api_key=api_key,
            base_url=flask_app.config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
            model=config.get('model_id') or flask_app.config.get('DEEPSEEK_MODEL', 'deepseek-v4-flash'),
            timeout=flask_app.config.get('DEEPSEEK_TIMEOUT_SECONDS', 60),
            reasoning_effort=flask_app.config.get('DEEPSEEK_REASONING_EFFORT', 'high'),
            max_retries=flask_app.config.get('DEEPSEEK_MAX_RETRIES', 3),
        )
    configured_enabled = config.get('llm_enabled')
    llm_enabled = bool(client) if configured_enabled is None else bool(configured_enabled)

    def schedule_loader(form):
        profile = _profile_for_form(form)
        student_id = profile.student_id if profile is not None else None
        return get_listener_schedule_for_datasets(
            getattr(form, 'listener_number', None),
            student_id,
            semester,
            dataset_refs,
        )

    def school_matches_loader(form):
        school_ref = dataset_refs.get(SCHOOL_KIND) or {}
        dataset_id = school_ref.get('id') if isinstance(school_ref, Mapping) else school_ref
        if not dataset_id:
            return ()
        return find_school_candidates_for_dataset(form, dataset_id, semester)

    frozen_rules = config.get('rule_revisions') if 'rule_revisions' in config else None
    return AssessmentService(
        llm_client=client,
        llm_enabled=llm_enabled,
        schedule_loader=schedule_loader,
        school_matches_loader=school_matches_loader,
        listener_profile_loader=_profile_for_form,
        schedule_dependencies=dataset_refs,
        rule_revisions=frozen_rules,
        semester=semester,
        semester_monday=semester_monday,
        model_id=config.get('model_id') or flask_app.config.get('DEEPSEEK_MODEL', 'deepseek-v4-flash'),
        prompt_version=config.get('prompt_version') or flask_app.config.get('DEEPSEEK_PROMPT_VERSION') or PROMPT_VERSION,
    )


SERVICE_FACTORY = build_default_service


def _load_snapshot(batch: ReviewBatch) -> dict[str, Any]:
    source = batch.snapshot_json or '{}'
    try:
        value = json.loads(source)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_config(batch: ReviewBatch) -> dict[str, Any]:
    try:
        value = json.loads(batch.config_snapshot_json or '{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _review_mode_from_config(config: Mapping[str, Any]) -> ReviewMode:
    raw = config.get('review_mode')
    if raw is None:
        return ReviewMode.COMBINED if bool(config.get('llm_enabled', True)) else ReviewMode.RULES_ONLY
    return ReviewMode(str(raw))


def _service_from_config(config):
    factory = SERVICE_FACTORY
    try:
        signature(factory).bind(config)
    except (TypeError, ValueError):
        return factory()
    return factory(config)


def _save_snapshot(batch: ReviewBatch, value: Mapping[str, Any]):
    batch.snapshot_json = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )


def _summary_value(summary: Any, name: str, default: Any = None):
    if isinstance(summary, Mapping):
        return summary.get(name, default)
    if hasattr(summary, name):
        return getattr(summary, name)
    if name == 'category' and hasattr(summary, 'classification'):
        return getattr(summary, 'classification')
    return default


def _result_payload(
    summary: Any,
    *,
    error_code: str | None = None,
    http_attempts: int | None = None,
) -> dict[str, Any]:
    attempts = (
        _summary_value(summary, 'http_attempts', 0)
        if http_attempts is None else http_attempts
    )
    return {
        'assessment_id': _summary_value(summary, 'assessment_id'),
        'category': _summary_value(summary, 'category', ReviewCategory.UNKNOWN.value),
        'error_code': error_code or _summary_value(summary, 'error_code'),
        'cache_hit': bool(_summary_value(summary, 'cache_hit', False)),
        'http_attempts': int(attempts or 0),
    }


def _batch_item(batch_id: str, form_id: int) -> ReviewBatchItem:
    item = ReviewBatchItem.query.filter_by(
        batch_id=str(batch_id),
        form_id=int(form_id),
    ).first()
    if item is None:
        ordinal = db.session.query(ReviewBatchItem.ordinal).filter_by(
            batch_id=str(batch_id),
        ).order_by(ReviewBatchItem.ordinal.desc()).first()
        item = ReviewBatchItem(
            batch_id=str(batch_id),
            form_id=int(form_id),
            ordinal=(int(ordinal[0]) + 1) if ordinal else 0,
        )
        db.session.add(item)
        db.session.flush()
    return item


def _mark_item_running(batch_id: str, form_id: int) -> datetime:
    item = _batch_item(batch_id, form_id)
    started_at = datetime.now()
    item.status = 'running'
    item.started_at = started_at
    item.finished_at = None
    item.duration_ms = None
    db.session.commit()
    return started_at


def _record_progress(
    batch: ReviewBatch,
    result: Mapping[str, Any],
    form_id: int,
    *,
    started_at: datetime | None = None,
    status: str | None = None,
):
    """Persist only the current form item; live progress never uses snapshot_json."""
    item = _batch_item(batch.id, form_id)
    item.status = status or ('failed' if result.get('error_code') else 'completed')
    item.assessment_id = result.get('assessment_id')
    item.category = result.get('category') or ReviewCategory.UNKNOWN.value
    item.error_code = result.get('error_code')
    item.cache_hit = bool(result.get('cache_hit', False))
    item.http_attempts = int(result.get('http_attempts', 0) or 0)
    item.finished_at = datetime.now()
    effective_start = started_at or item.started_at
    if effective_start is not None:
        item.duration_ms = max(
            0,
            int((item.finished_at - effective_start).total_seconds() * 1000),
        )
    db.session.commit()


def _mark_assessment_error(assessment_id: str | None, code: str):
    if not assessment_id:
        return
    assessment = db.session.get(ReviewAssessment, assessment_id)
    if assessment is None:
        return
    assessment.error_code = code
    assessment.error_message = f'task:{code}'
    db.session.commit()


def _client_attempts(service: Any) -> int:
    client = getattr(service, 'llm_client', None)
    return int(getattr(client, 'last_attempt_count', 0) or 0)


def _configured_transient_retries(config: Mapping[str, Any]) -> int:
    value = config.get('transient_retries', TASK_TRANSIENT_RETRIES)
    if isinstance(value, bool):
        raise ValueError('transient_retries must be an integer from 0 to 3')
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('transient_retries must be an integer from 0 to 3') from exc
    if not 0 <= value <= 3:
        raise ValueError('transient_retries must be an integer from 0 to 3')
    return value


def _fallback(
    service: Any,
    batch_id: str,
    form_id: int,
    force_refresh: bool,
    code: str,
    *,
    http_attempts: int = 0,
):
    try:
        summary = service.assess_form(
            form_id,
            batch_id=batch_id,
            force_refresh=True,
            llm_enabled=False,
            review_mode=ReviewMode.RULES_ONLY,
        )
        _mark_assessment_error(_summary_value(summary, 'assessment_id'), code)
        return _result_payload(
            summary,
            error_code=code,
            http_attempts=http_attempts,
        )
    except Exception:
        return {
            'assessment_id': None,
            'category': ReviewCategory.UNKNOWN.value,
            'error_code': code,
            'cache_hit': False,
            'http_attempts': int(http_attempts or 0),
        }


def _assess_one(batch_id: str, form_id: int, force_refresh: bool = False):
    batch = db.session.get(ReviewBatch, batch_id)
    if batch is None:
        raise KeyError(f'unknown batch: {batch_id}')
    if batch.cancel_requested or batch.status in {
        BatchStatus.CANCEL_REQUESTED.value,
        BatchStatus.CANCELLED.value,
    }:
        result = {
            'assessment_id': None,
            'category': ReviewCategory.UNKNOWN.value,
            'error_code': 'cancelled',
            'cache_hit': False,
            'http_attempts': 0,
        }
        _record_progress(batch, result, form_id, status='cancelled')
        return result

    config = _load_config(batch)
    llm_enabled = bool(config.get('llm_enabled', True))
    review_mode = _review_mode_from_config(config)
    item = _batch_item(batch.id, form_id)
    if item.status == 'cancelled':
        return {
            'assessment_id': item.assessment_id,
            'category': item.category or ReviewCategory.UNKNOWN.value,
            'error_code': item.error_code or 'cancelled',
            'cache_hit': bool(item.cache_hit),
            'http_attempts': int(item.http_attempts or 0),
        }
    started_at = _mark_item_running(batch.id, form_id)
    attempt_total = 0
    try:
        service = _service_from_config(config)
    except Exception:
        result = {
            'assessment_id': None,
            'category': ReviewCategory.UNKNOWN.value,
            'error_code': 'task_error',
            'cache_hit': False,
            'http_attempts': 0,
        }
        _record_progress(batch, result, form_id, started_at=started_at)
        return result
    try:
        transient_retries = _configured_transient_retries(config)
    except ValueError:
        result = {
            'assessment_id': None,
            'category': ReviewCategory.UNKNOWN.value,
            'error_code': 'invalid_retry_config',
            'cache_hit': False,
            'http_attempts': 0,
        }
        _record_progress(batch, result, form_id, started_at=started_at)
        return result
    for attempt in range(transient_retries + 1):
        try:
            summary = service.assess_form(
                form_id,
                batch_id=batch_id,
                force_refresh=bool(force_refresh),
                llm_enabled=llm_enabled,
                review_mode=review_mode,
            )
            attempt_total += int(_summary_value(summary, 'http_attempts', 0) or 0)
            summary_error = _summary_value(summary, 'error_code')
            if summary_error in TRANSIENT_ERROR_CODES:
                if attempt < transient_retries:
                    force_refresh = True
                    continue
                result = _fallback(
                    service,
                    batch_id,
                    form_id,
                    force_refresh,
                    summary_error,
                    http_attempts=attempt_total,
                )
                _record_progress(batch, result, form_id, started_at=started_at)
                return result
            result = _result_payload(summary, http_attempts=attempt_total)
            _record_progress(batch, result, form_id, started_at=started_at)
            return result
        except (TransientLLMError, PermanentLLMError, InvalidLLMResponse) as exc:
            attempt_total += _client_attempts(service)
            if isinstance(exc, TransientLLMError) and attempt < transient_retries:
                continue
            result = _fallback(
                service,
                batch_id,
                form_id,
                force_refresh,
                exc.code,
                http_attempts=attempt_total,
            )
            _record_progress(batch, result, form_id, started_at=started_at)
            return result
        except Exception:
            attempt_total += _client_attempts(service)
            result = _fallback(
                service,
                batch_id,
                form_id,
                force_refresh,
                'task_error',
                http_attempts=attempt_total,
            )
            _record_progress(batch, result, form_id, started_at=started_at)
            return result


def _cancel_unstarted_items(batch_id: str):
    items = ReviewBatchItem.query.filter_by(
        batch_id=str(batch_id),
        status='queued',
    ).all()
    finished_at = datetime.now()
    for item in items:
        item.status = 'cancelled'
        item.error_code = 'cancelled'
        item.category = ReviewCategory.UNKNOWN.value
        item.finished_at = finished_at
        item.duration_ms = 0
    if items:
        db.session.commit()


def _item_result(item: ReviewBatchItem) -> dict[str, Any]:
    return {
        'form_id': int(item.form_id),
        'assessment_id': item.assessment_id,
        'category': item.category or ReviewCategory.UNKNOWN.value,
        'error_code': item.error_code,
        'cache_hit': bool(item.cache_hit),
        'http_attempts': int(item.http_attempts or 0),
        'status': item.status,
        'duration_ms': item.duration_ms,
    }


def _aggregate_batch(batch_id: str, *, cancelled: bool = False):
    batch = db.session.get(ReviewBatch, batch_id)
    if batch is None:
        raise KeyError(f'unknown batch: {batch_id}')
    if cancelled:
        _cancel_unstarted_items(batch.id)
    snapshot = _load_snapshot(batch)
    items = ReviewBatchItem.query.filter_by(batch_id=str(batch.id)).order_by(
        ReviewBatchItem.ordinal.asc(),
    ).all()
    results = [_item_result(item) for item in items]
    assessment_ids = [
        item.assessment_id
        for item in items
        if isinstance(item.assessment_id, str)
    ]
    counts = {
        ReviewCategory.CLEAR.value: 0,
        ReviewCategory.REVIEW.value: 0,
        ReviewCategory.HIGH_RISK.value: 0,
        ReviewCategory.UNKNOWN.value: 0,
    }
    for item in items:
        if item.status in {'completed', 'failed'} and item.category in counts:
            counts[item.category] += 1
    failed_count = sum(item.status == 'failed' for item in items)
    cancelled_count = sum(item.status == 'cancelled' for item in items)
    cache_count = sum(bool(item.cache_hit) for item in items)
    http_attempts = sum(int(item.http_attempts or 0) for item in items)
    processed_count = sum(
        item.status in {'completed', 'failed', 'cancelled'}
        for item in items
    )
    target_count = batch.target_form_count

    batch.clear_count = counts[ReviewCategory.CLEAR.value]
    batch.review_count = counts[ReviewCategory.REVIEW.value]
    batch.high_risk_count = counts[ReviewCategory.HIGH_RISK.value]
    batch.unknown_count = counts[ReviewCategory.UNKNOWN.value]
    batch.failed_count = failed_count
    batch.cache_count = cache_count
    snapshot.update({
        'results': results,
        'assessment_ids': sorted(set(assessment_ids)),
        'processed_count': processed_count,
        'target_count': target_count,
        'clear_count': batch.clear_count,
        'review_count': batch.review_count,
        'high_risk_count': batch.high_risk_count,
        'unknown_count': batch.unknown_count,
        'failed_count': batch.failed_count,
        'cancelled_count': cancelled_count,
        'cache_count': batch.cache_count,
        'http_attempts': http_attempts,
        'category_counts': counts,
    })
    if cancelled:
        batch.status = BatchStatus.CANCELLED.value
    elif processed_count < target_count:
        batch.status = BatchStatus.RUNNING.value
    elif failed_count:
        batch.status = BatchStatus.COMPLETED_WITH_ERRORS.value
    else:
        batch.status = BatchStatus.COMPLETED.value
    batch.finished_at = datetime.now()
    _save_snapshot(batch, snapshot)
    db.session.commit()
    return {
        'batch_id': str(batch.id),
        'status': batch.status,
        'target_form_count': int(batch.target_form_count or 0),
        'processed_count': int(processed_count),
        'clear_count': int(batch.clear_count or 0),
        'review_count': int(batch.review_count or 0),
        'high_risk_count': int(batch.high_risk_count or 0),
        'unknown_count': int(batch.unknown_count or 0),
        'failed_count': int(batch.failed_count or 0),
        'cancelled_count': int(cancelled_count),
        'cache_count': int(batch.cache_count or 0),
        'http_attempts': int(http_attempts),
        'results': results,
    }


@celery_app.task(name='review_automation.assess_form')
def assess_form_task(batch_id: str, form_id: int, force_refresh: bool = False):
    """Assess one form; task payload contains only IDs and a control boolean."""
    return _assess_one(str(batch_id), int(form_id), bool(force_refresh))


@celery_app.task(name='review_automation.run_batch')
def run_review_batch_task(batch_id: str):
    """Eager/test-compatible root task that processes persisted batch items."""
    batch = db.session.get(ReviewBatch, batch_id)
    if batch is None:
        raise KeyError(f'unknown batch: {batch_id}')
    if batch.cancel_requested or batch.status == BatchStatus.CANCEL_REQUESTED.value:
        batch.status = BatchStatus.CANCELLED.value
        batch.finished_at = datetime.now()
        db.session.commit()
        return _aggregate_batch(batch.id, cancelled=True)

    config = _load_config(batch)
    items = ReviewBatchItem.query.filter_by(batch_id=str(batch.id)).order_by(
        ReviewBatchItem.ordinal.asc(),
    ).all()
    form_ids = [item.form_id for item in items]
    if not form_ids:
        form_ids = [int(value) for value in config.get('form_ids', [])]
    force_refresh = bool(config.get('force_refresh', False))
    batch.status = BatchStatus.RUNNING.value
    batch.started_at = datetime.now()
    db.session.commit()
    cancelled = False
    for form_id in form_ids:
        db.session.refresh(batch)
        if batch.cancel_requested:
            cancelled = True
            break
        _assess_one(batch.id, form_id, force_refresh)
    return _aggregate_batch(batch.id, cancelled=cancelled)


@celery_app.task(name='review_automation.finalize_review_batch')
def finalize_review_batch_task(results, batch_id: str):
    """Ignore untrusted task bodies and recompute totals from item rows."""
    return _aggregate_batch(str(batch_id))


def create_review_batch(
    form_ids,
    *,
    requester_id: int | None = None,
    force_refresh: bool = False,
    llm_enabled: bool = True,
    review_mode: str | ReviewMode | None = None,
    transient_retries: int | None = None,
    semester: str | None = None,
    semester_monday: date | str | None = None,
    enqueue: bool = True,
):
    ids = sorted({int(value) for value in form_ids})
    mode = (
        ReviewMode.COMBINED if review_mode is None and llm_enabled
        else ReviewMode.RULES_ONLY if review_mode is None
        else ReviewMode(review_mode)
    )
    mode_llm_enabled = mode != ReviewMode.RULES_ONLY
    if transient_retries is None:
        transient_retries_value = TASK_TRANSIENT_RETRIES
    else:
        transient_retries_value = transient_retries
    if isinstance(transient_retries_value, bool):
        raise ValueError('transient_retries must be an integer from 0 to 3')
    try:
        transient_retries_value = int(transient_retries_value)
    except (TypeError, ValueError) as exc:
        raise ValueError('transient_retries must be an integer from 0 to 3') from exc
    if not 0 <= transient_retries_value <= 3:
        raise ValueError('transient_retries must be an integer from 0 to 3')
    if isinstance(semester_monday, date):
        semester_monday_value = semester_monday.isoformat()
    elif semester_monday is None:
        semester_monday_value = None
    else:
        semester_monday_value = str(semester_monday)
    config = {
        'form_ids': ids,
        'force_refresh': bool(force_refresh),
        'llm_enabled': bool(mode_llm_enabled),
        'review_mode': mode.value,
        'transient_retries': transient_retries_value,
        'semester': str(semester) if semester is not None else None,
        'semester_monday': semester_monday_value,
        'schedule_datasets': _dataset_snapshot(semester),
        'rule_revisions': _rule_snapshot(),
        'model_id': flask_app.config.get('DEEPSEEK_MODEL', 'deepseek-v4-flash'),
        'prompt_version': flask_app.config.get('DEEPSEEK_PROMPT_VERSION') or PROMPT_VERSION,
    }
    snapshot = {
        'form_ids': ids,
        'force_refresh': bool(force_refresh),
        'llm_enabled': bool(mode_llm_enabled),
        'review_mode': mode.value,
        'transient_retries': transient_retries_value,
        'results': [],
        'assessment_ids': [],
        'processed_count': 0,
    }
    batch = ReviewBatch(
        requester_id=requester_id,
        status=BatchStatus.QUEUED.value,
        target_form_count=len(ids),
        config_snapshot_json=json.dumps(
            config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        ),
        snapshot_json=json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        ),
    )
    db.session.add(batch)
    db.session.flush()
    for ordinal, form_id in enumerate(ids):
        db.session.add(ReviewBatchItem(
            batch_id=batch.id,
            form_id=form_id,
            ordinal=ordinal,
        ))
    db.session.commit()
    if enqueue:
        dispatch_review_batch(batch.id)
    db.session.expire_all()
    return db.session.get(ReviewBatch, batch.id)


def dispatch_review_batch(batch_id: str):
    batch = db.session.get(ReviewBatch, str(batch_id))
    if batch is None:
        raise KeyError(f'unknown batch: {batch_id}')
    config = _load_config(batch)
    force_refresh = bool(config.get('force_refresh', False))
    items = ReviewBatchItem.query.filter_by(batch_id=str(batch.id)).order_by(
        ReviewBatchItem.ordinal.asc(),
    ).all()
    if celery_app.conf.task_always_eager:
        result = run_review_batch_task.delay(str(batch_id))
    else:
        batch.status = BatchStatus.RUNNING.value
        batch.started_at = datetime.now()
        db.session.commit()
        header = group(
            assess_form_task.s(batch.id, item.form_id, force_refresh)
            for item in items
        )
        result = chord(header)(finalize_review_batch_task.s(batch.id))
        batch.celery_root_id = result.id
        db.session.commit()
    db.session.expire_all()
    return result


def enqueue_review_batch(form_ids, **kwargs):
    return create_review_batch(form_ids, enqueue=True, **kwargs)


def cancel_review_batch(batch_id: str):
    batch = db.session.get(ReviewBatch, str(batch_id))
    if batch is None:
        raise KeyError(f'unknown batch: {batch_id}')
    if batch.status in {
        BatchStatus.QUEUED.value,
        BatchStatus.RUNNING.value,
    }:
        batch.cancel_requested = True
        batch.status = BatchStatus.CANCEL_REQUESTED.value
        db.session.commit()
    return batch


__all__ = [
    'SERVICE_FACTORY',
    'assess_form_task',
    'build_default_service',
    'cancel_review_batch',
    'create_review_batch',
    'dispatch_review_batch',
    'enqueue_review_batch',
    'finalize_review_batch_task',
    'run_review_batch_task',
]
