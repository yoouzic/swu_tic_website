"""ID-only Celery batch tasks for immutable automated assessments."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from app.app import app as flask_app
from app.models import db

from .. import get_celery_app
from ..contracts import BatchStatus, ReviewCategory
from ..llm.client import (
    InvalidLLMResponse,
    PermanentLLMError,
    TransientLLMError,
)
from ..llm.client import DeepSeekReviewClient
from ..models import ReviewAssessment, ReviewBatch
from ..service import AssessmentService


celery_app = get_celery_app(flask_app)
TASK_TRANSIENT_RETRIES = 1
TRANSIENT_ERROR_CODES = {
    'timeout',
    'rate_limited',
    'server_error',
    'connection_error',
    'retry_exhausted',
    'client_unavailable',
}


def build_default_service():
    """Build a worker-local service without constructing a client for missing keys."""
    api_key = flask_app.config.get('DEEPSEEK_API_KEY', '')
    client = None
    if api_key:
        client = DeepSeekReviewClient(
            api_key=api_key,
            base_url=flask_app.config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
            model=flask_app.config.get('DEEPSEEK_MODEL', 'deepseek-v4-flash'),
            timeout=flask_app.config.get('DEEPSEEK_TIMEOUT_SECONDS', 60),
            reasoning_effort=flask_app.config.get('DEEPSEEK_REASONING_EFFORT', 'high'),
            max_retries=flask_app.config.get('DEEPSEEK_MAX_RETRIES', 3),
        )
    return AssessmentService(
        llm_client=client,
        llm_enabled=bool(client),
        prompt_version=flask_app.config.get('DEEPSEEK_PROMPT_VERSION'),
    )


SERVICE_FACTORY = build_default_service


def _load_snapshot(batch: ReviewBatch) -> dict[str, Any]:
    source = batch.snapshot_json or batch.config_snapshot_json or '{}'
    try:
        value = json.loads(source)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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


def _result_payload(summary: Any, *, error_code: str | None = None) -> dict[str, Any]:
    return {
        'assessment_id': _summary_value(summary, 'assessment_id'),
        'category': _summary_value(summary, 'category', ReviewCategory.UNKNOWN.value),
        'error_code': error_code or _summary_value(summary, 'error_code'),
        'cache_hit': bool(_summary_value(summary, 'cache_hit', False)),
    }


def _record_progress(batch: ReviewBatch, result: Mapping[str, Any], form_id: int):
    snapshot = _load_snapshot(batch)
    results = list(snapshot.get('results', []))
    results = [
        item for item in results
        if item.get('form_id') != int(form_id)
    ]
    stored_result = dict(result)
    stored_result['form_id'] = int(form_id)
    results.append(stored_result)
    assessment_ids = sorted({
        item['assessment_id']
        for item in results
        if item.get('assessment_id')
    })
    snapshot['results'] = results
    snapshot['assessment_ids'] = assessment_ids
    snapshot['processed_count'] = len(results)
    snapshot['cache_count'] = sum(bool(item.get('cache_hit')) for item in results)
    _save_snapshot(batch, snapshot)
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


def _fallback(service: Any, batch_id: str, form_id: int, force_refresh: bool, code: str):
    try:
        summary = service.assess_form(
            form_id,
            batch_id=batch_id,
            force_refresh=True,
            llm_enabled=False,
        )
        _mark_assessment_error(_summary_value(summary, 'assessment_id'), code)
        return _result_payload(summary, error_code=code)
    except Exception:
        return {
            'assessment_id': None,
            'category': ReviewCategory.UNKNOWN.value,
            'error_code': code,
            'cache_hit': False,
        }


def _assess_one(batch_id: str, form_id: int, force_refresh: bool = False):
    batch = db.session.get(ReviewBatch, batch_id)
    if batch is None:
        raise KeyError(f'unknown batch: {batch_id}')
    if batch.cancel_requested or batch.status in {
        BatchStatus.CANCEL_REQUESTED.value,
        BatchStatus.CANCELLED.value,
    }:
        return {
            'assessment_id': None,
            'category': ReviewCategory.UNKNOWN.value,
            'error_code': 'cancelled',
            'cache_hit': False,
        }

    config = _load_snapshot(batch)
    llm_enabled = bool(config.get('llm_enabled', True))
    try:
        service = SERVICE_FACTORY()
    except Exception:
        result = {
            'assessment_id': None,
            'category': ReviewCategory.UNKNOWN.value,
            'error_code': 'task_error',
            'cache_hit': False,
        }
        _record_progress(batch, result, form_id)
        return result
    for attempt in range(TASK_TRANSIENT_RETRIES + 1):
        try:
            summary = service.assess_form(
                form_id,
                batch_id=batch_id,
                force_refresh=bool(force_refresh),
                llm_enabled=llm_enabled,
            )
            result = _result_payload(summary)
            _record_progress(batch, result, form_id)
            return result
        except (TransientLLMError, PermanentLLMError, InvalidLLMResponse) as exc:
            if isinstance(exc, TransientLLMError) and attempt < TASK_TRANSIENT_RETRIES:
                continue
            result = _fallback(service, batch_id, form_id, force_refresh, exc.code)
            _record_progress(batch, result, form_id)
            return result
        except Exception:
            result = _fallback(service, batch_id, form_id, force_refresh, 'task_error')
            _record_progress(batch, result, form_id)
            return result


def _aggregate_batch(batch_id: str, *, cancelled: bool = False):
    batch = db.session.get(ReviewBatch, batch_id)
    if batch is None:
        raise KeyError(f'unknown batch: {batch_id}')
    snapshot = _load_snapshot(batch)
    assessment_ids = [
        value for value in snapshot.get('assessment_ids', [])
        if isinstance(value, str)
    ]
    assessments = []
    if assessment_ids:
        assessments = ReviewAssessment.query.filter(
            ReviewAssessment.id.in_(assessment_ids)
        ).all()
    results = list(snapshot.get('results', []))
    counts = {
        ReviewCategory.CLEAR.value: 0,
        ReviewCategory.REVIEW.value: 0,
        ReviewCategory.HIGH_RISK.value: 0,
        ReviewCategory.UNKNOWN.value: 0,
    }
    for assessment in assessments:
        if assessment.classification in counts:
            counts[assessment.classification] += 1
    failed_count = sum(bool(item.get('error_code')) for item in results)
    failed_count += sum(
        not item.get('assessment_id') and not item.get('error_code')
        for item in results
    )
    cache_count = sum(bool(item.get('cache_hit')) for item in results)
    processed_count = len(results)
    target_count = batch.target_form_count

    batch.clear_count = counts[ReviewCategory.CLEAR.value]
    batch.review_count = counts[ReviewCategory.REVIEW.value]
    batch.high_risk_count = counts[ReviewCategory.HIGH_RISK.value]
    batch.unknown_count = counts[ReviewCategory.UNKNOWN.value]
    batch.failed_count = failed_count
    batch.cache_count = cache_count
    snapshot.update({
        'processed_count': processed_count,
        'target_count': target_count,
        'clear_count': batch.clear_count,
        'review_count': batch.review_count,
        'high_risk_count': batch.high_risk_count,
        'unknown_count': batch.unknown_count,
        'failed_count': batch.failed_count,
        'cache_count': batch.cache_count,
        'category_counts': counts,
    })
    if cancelled:
        batch.status = BatchStatus.CANCELLED.value
    elif failed_count:
        batch.status = BatchStatus.COMPLETED_WITH_ERRORS.value
    else:
        batch.status = BatchStatus.COMPLETED.value
    batch.finished_at = datetime.now()
    _save_snapshot(batch, snapshot)
    db.session.commit()
    return batch


@celery_app.task(name='review_automation.assess_form')
def assess_form_task(batch_id: str, form_id: int, force_refresh: bool = False):
    """Assess one form; task payload contains only IDs and a control boolean."""
    return _assess_one(str(batch_id), int(form_id), bool(force_refresh))


@celery_app.task(name='review_automation.run_batch')
def run_review_batch_task(batch_id: str):
    """Durable root task that iterates the persisted batch snapshot."""
    batch = db.session.get(ReviewBatch, batch_id)
    if batch is None:
        raise KeyError(f'unknown batch: {batch_id}')
    if batch.cancel_requested or batch.status == BatchStatus.CANCEL_REQUESTED.value:
        batch.status = BatchStatus.CANCELLED.value
        batch.finished_at = datetime.now()
        db.session.commit()
        return _aggregate_batch(batch.id, cancelled=True)

    config = _load_snapshot(batch)
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


def create_review_batch(
    form_ids,
    *,
    requester_id: int | None = None,
    force_refresh: bool = False,
    llm_enabled: bool = True,
    enqueue: bool = True,
):
    ids = sorted({int(value) for value in form_ids})
    snapshot = {
        'form_ids': ids,
        'force_refresh': bool(force_refresh),
        'llm_enabled': bool(llm_enabled),
        'results': [],
        'assessment_ids': [],
        'processed_count': 0,
    }
    batch = ReviewBatch(
        requester_id=requester_id,
        status=BatchStatus.QUEUED.value,
        target_form_count=len(ids),
        config_snapshot_json=json.dumps(
            snapshot,
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
    db.session.commit()
    if enqueue:
        dispatch_review_batch(batch.id)
    db.session.expire_all()
    return db.session.get(ReviewBatch, batch.id)


def dispatch_review_batch(batch_id: str):
    result = run_review_batch_task.delay(str(batch_id))
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
    'run_review_batch_task',
]
