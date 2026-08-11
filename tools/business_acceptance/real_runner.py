"""Production-backed acceptance batch runner.

This module is deliberately imported without importing Flask.  The application
and Celery task modules are loaded only after the isolated runtime, local Redis,
and API-key-presence gates have passed.  Artifacts contain synthetic keys and
numeric counters only; prompts, responses, reasoning and credentials never
enter the persisted payloads.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .batches import (
    REVIEW_MODES,
    StageObservation,
    choose_safe_concurrency,
    plan_real_batches,
)
from .real_batches import RealBatchTimeout, run_created_batch


TRANSIENT_ERROR_CODES = frozenset({
    'timeout',
    'rate_limited',
    'server_error',
    'connection_error',
    'retry_exhausted',
    'provider_unavailable',
    'transient',
})


class RealAcceptanceError(RuntimeError):
    """Raised when a real acceptance phase cannot safely proceed."""


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
            newline='\n',
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default
    return value


def _runtime_path(manifest, relative: str) -> Path:
    return manifest.config.output_path(relative)


def _require_under(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RealAcceptanceError(
            f'{label} must remain beneath the isolated acceptance root'
        ) from exc


def _require_real_environment(manifest) -> int:
    if os.environ.get('ACCEPTANCE_RUN') != '1':
        raise RealAcceptanceError('ACCEPTANCE_RUN=1 is required for real acceptance')
    database = os.environ.get('SQLITE_DB_PATH')
    if not database:
        raise RealAcceptanceError(
            'SQLITE_DB_PATH must explicitly select the isolated acceptance database'
        )
    repo_root = Path(__file__).resolve().parents[2]
    database_root = (repo_root / 'data' / 'instance' / 'acceptance-2026-08-11').resolve()
    _require_under(Path(database).expanduser().resolve(), database_root, 'SQLITE_DB_PATH')
    if not os.environ.get('DEEPSEEK_API_KEY', '').strip():
        raise RealAcceptanceError('DEEPSEEK_API_KEY is missing')

    port_text = os.environ.get('ACCEPTANCE_REDIS_PORT', '6387')
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RealAcceptanceError('ACCEPTANCE_REDIS_PORT must be an integer') from exc
    if not 1024 <= port <= 65535:
        raise RealAcceptanceError('ACCEPTANCE_REDIS_PORT is outside the user-port range')
    expected_broker = f'redis://127.0.0.1:{port}/0'
    expected_backend = f'redis://127.0.0.1:{port}/1'
    if os.environ.get('CELERY_BROKER_URL') != expected_broker:
        raise RealAcceptanceError('CELERY_BROKER_URL does not target the acceptance Redis')
    if os.environ.get('CELERY_RESULT_BACKEND') != expected_backend:
        raise RealAcceptanceError('CELERY_RESULT_BACKEND does not target the acceptance Redis')
    if os.environ.get('CELERY_TASK_ALWAYS_EAGER', '').strip().lower() in {
        '1', 'true', 'yes', 'on',
    }:
        raise RealAcceptanceError('CELERY_TASK_ALWAYS_EAGER must be disabled for real workers')

    parsed = urlparse(expected_broker)
    try:
        from redis import Redis
    except ImportError as exc:
        raise RealAcceptanceError('redis Python client is unavailable') from exc
    client = Redis(
        host=parsed.hostname,
        port=parsed.port,
        db=int((parsed.path or '/0').lstrip('/') or 0),
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    try:
        if client.ping() is not True:
            raise RealAcceptanceError('acceptance Redis PING did not return true')
    except Exception as exc:
        if isinstance(exc, RealAcceptanceError):
            raise
        raise RealAcceptanceError('acceptance Redis PING failed') from exc
    finally:
        close = getattr(client, 'close', None)
        if callable(close):
            close()
    return port


def _load_real_state(manifest) -> dict[str, Any]:
    path = _runtime_path(manifest, 'run-state.json')
    value = _read_json(path, {})
    return value if isinstance(value, dict) else {}


def _save_real_state(manifest, state: Mapping[str, Any]) -> None:
    _write_json_atomic(_runtime_path(manifest, 'run-state.json'), dict(state))


def _form_maps(manifest, LectureForm):
    rows = LectureForm.query.order_by(LectureForm.id.asc()).all()
    if len(rows) != manifest.logical_form_count:
        raise RealAcceptanceError(
            f'isolated database has {len(rows)} forms; expected {manifest.logical_form_count}'
        )
    key_to_id: dict[str, int] = {}
    id_to_key: dict[int, str] = {}
    mode_by_key: dict[str, str] = {}
    for spec, row in zip(manifest.forms, rows):
        if row.unique_id not in (None, row.id):
            raise RealAcceptanceError(
                'real automation requires the initial acceptance logical versions'
            )
        key_to_id[spec.synthetic_key] = int(row.id)
        id_to_key[int(row.id)] = spec.synthetic_key
        mode_by_key[spec.synthetic_key] = str(spec.review_mode)
    if len(key_to_id) != manifest.logical_form_count:
        raise RealAcceptanceError('acceptance form-key mapping is not unique')
    return key_to_id, id_to_key, mode_by_key


def _records_path(manifest) -> Path:
    return _runtime_path(manifest, 'results/real-batch-records.json')


def _load_records(manifest) -> list[dict[str, Any]]:
    value = _read_json(_records_path(manifest), [])
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _save_records(manifest, records: Iterable[Mapping[str, Any]]) -> None:
    _write_json_atomic(_records_path(manifest), [dict(item) for item in records])


def _refresh_factory(db, ReviewBatch, ReviewBatchItem):
    def refresh(batch_id: str):
        db.session.expire_all()
        batch = db.session.get(ReviewBatch, str(batch_id))
        if batch is None:
            return None, ()
        items = ReviewBatchItem.query.filter_by(batch_id=str(batch_id)).order_by(
            ReviewBatchItem.ordinal.asc(),
        ).all()
        return batch, items

    return refresh


def _decorate_record(
    summary: Mapping[str, Any],
    *,
    phase: str,
    mode: str,
    form_keys: tuple[str, ...],
    id_to_key: Mapping[int, str],
    stage_index: int | None = None,
) -> dict[str, Any]:
    def key_for(value: Any) -> str | None:
        try:
            return id_to_key.get(int(value))
        except (TypeError, ValueError):
            return None

    failed_items = []
    for item in summary.get('failed_items', ()):
        if not isinstance(item, Mapping):
            continue
        key = key_for(item.get('form_id'))
        failed_items.append({
            'form_key': key,
            'error_code': item.get('error_code'),
        })
    successful_keys = [
        key for key in (key_for(value) for value in summary.get('successful_form_ids', ()))
        if key is not None
    ]
    record = dict(summary)
    record.pop('successful_form_ids', None)
    record.pop('failed_items', None)
    record.update({
        'phase': phase,
        'mode': mode,
        'form_keys': list(form_keys),
        'successful_form_keys': successful_keys,
        'failed_items': failed_items,
    })
    if stage_index is not None:
        record['stage_index'] = int(stage_index)
    return record


def _aggregate_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = list(records)
    target = sum(int(item.get('target', 0) or 0) for item in values)
    processed = sum(int(item.get('processed', 0) or 0) for item in values)
    terminal = sum(int(item.get('terminal_count', 0) or 0) for item in values)
    failed = sum(int(item.get('failed', 0) or 0) for item in values)
    cancelled = sum(int(item.get('cancelled', 0) or 0) for item in values)
    cache_hits = sum(int(item.get('cache_hits', 0) or 0) for item in values)
    http_attempts = sum(int(item.get('http_attempts', 0) or 0) for item in values)
    if any(item.get('status') == 'failed' for item in values):
        status = 'failed'
    elif failed or cancelled or any(item.get('status') == 'completed_with_errors' for item in values):
        status = 'completed_with_errors'
    elif values and all(item.get('status') == 'completed' for item in values):
        status = 'completed'
    else:
        status = 'queued'
    return {
        'status': status,
        'target': target,
        'processed': processed,
        'terminal_count': terminal,
        'failed': failed,
        'cancelled': cancelled,
        'cache_hits': cache_hits,
        'http_attempts': http_attempts,
        'batch_ids': [str(item.get('batch_id')) for item in values if item.get('batch_id')],
        'p50_duration_ms': max((int(item.get('p50_duration_ms', 0) or 0) for item in values), default=0),
        'p95_duration_ms': max((int(item.get('p95_duration_ms', 0) or 0) for item in values), default=0),
        'max_duration_ms': max((int(item.get('max_duration_ms', 0) or 0) for item in values), default=0),
    }


def _state_after_records(manifest, records, *, phase: str, safe_concurrency: int | None = None):
    state = _load_real_state(manifest)
    primary = [item for item in records if item.get('phase') in {'staged', 'main'}]
    state.update({
        'phase': phase,
        'real_http_attempts': sum(int(item.get('http_attempts', 0) or 0) for item in records),
        'http_attempts': sum(int(item.get('http_attempts', 0) or 0) for item in records),
        'logical_llm_reviews': sum(
            int(item.get('target', 0) or 0)
            for item in primary
            if item.get('mode') in {'llm_only', 'combined'}
        ),
        'real_batch_ids': [str(item.get('batch_id')) for item in records if item.get('batch_id')],
    })
    if safe_concurrency is not None:
        state['safe_concurrency'] = int(safe_concurrency)
    _save_real_state(manifest, state)
    return state


def _write_batch_statistics(manifest, records) -> None:
    primary = [item for item in records if item.get('phase') in {'staged', 'main'}]
    actual = {mode: sum(
        int(item.get('target', 0) or 0)
        for item in primary if item.get('mode') == mode
    ) for mode in REVIEW_MODES}
    expected = {mode: int(manifest.config.per_mode) for mode in REVIEW_MODES}
    payload = {
        'expected': expected,
        'actual': actual,
        'total_expected': sum(expected.values()),
        'total_actual': sum(actual.values()),
        'primary_records': [dict(item) for item in primary],
        'all_real_records': [dict(item) for item in records],
        'failed': sum(int(item.get('failed', 0) or 0) for item in primary),
        'http_attempts': sum(int(item.get('http_attempts', 0) or 0) for item in records),
    }
    _write_json_atomic(_runtime_path(manifest, 'results/batches.json'), payload)


def _write_deepseek_summary(
    manifest,
    records,
    LectureForm,
    ReviewBatchItem,
    db,
    key_to_id: Mapping[str, int],
) -> None:
    """Persist bounded classification/evidence counters for final verification."""

    primary = [item for item in records if item.get('phase') in {'staged', 'main'}]
    batch_ids = [str(item.get('batch_id')) for item in primary if item.get('batch_id')]
    item_rows = []
    if batch_ids:
        from app.review_automation.models import ReviewBatchItem as ReviewBatchItemModel
        item_rows = ReviewBatchItemModel.query.filter(
            ReviewBatchItemModel.batch_id.in_(batch_ids)
        ).all()
    by_form: dict[int, Any] = {}
    for row in item_rows:
        by_form[int(row.form_id)] = row

    classification_rows = []
    for spec in manifest.forms:
        form_id = key_to_id[spec.synthetic_key]
        item = by_form.get(form_id)
        actual = getattr(item, 'category', None) if item is not None else None
        expected = 'clear' if spec.normal_control and spec.coverage == 'complete' else None
        classification_rows.append({
            'synthetic_key': spec.synthetic_key,
            'review_mode': spec.review_mode,
            'expected_category': expected,
            'category': actual,
            'oracle_markers': list(spec.oracle_markers),
        })
    combined_count = sum(
        int(item.get('target', 0) or 0)
        for item in primary if item.get('mode') == 'combined'
    )
    payload = {
        'logical_llm_reviews': sum(
            int(item.get('target', 0) or 0)
            for item in primary if item.get('mode') in {'llm_only', 'combined'}
        ),
        'http_attempts': sum(int(item.get('http_attempts', 0) or 0) for item in records),
        'classification_rows': classification_rows,
        'combined_sources': {
            'sources': ['rule', 'deepseek'],
            'checked': combined_count,
        },
        'records': [
            {
                'phase': item.get('phase'),
                'mode': item.get('mode'),
                'status': item.get('status'),
                'target': item.get('target'),
                'processed': item.get('processed'),
                'failed': item.get('failed'),
                'http_attempts': item.get('http_attempts'),
            }
            for item in records
        ],
    }
    _write_json_atomic(_runtime_path(manifest, 'results/deepseek-summary.json'), payload)


def _production_batch_callbacks(manifest, app, db, ReviewBatch, ReviewBatchItem):
    from app.review_automation.tasks.review import create_review_batch

    refresh = _refresh_factory(db, ReviewBatch, ReviewBatchItem)

    def run_batch(
        form_ids: tuple[int, ...],
        mode: str,
        *,
        force_refresh: bool,
        timeout_seconds: float,
        poll_seconds: float,
    ):
        def create(ids: tuple[int, ...], selected_mode: str):
            return create_review_batch(
                ids,
                requester_id=None,
                force_refresh=force_refresh,
                review_mode=selected_mode,
                transient_retries=0,
                semester=manifest.config.semester,
                semester_monday=manifest.config.semester_monday,
                enqueue=True,
            )

        return run_created_batch(
            form_ids,
            mode,
            create=create,
            refresh=refresh,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )

    return run_batch


def _decorate_and_run(
    run_batch,
    *,
    phase: str,
    mode: str,
    form_keys: tuple[str, ...],
    key_to_id: Mapping[str, int],
    id_to_key: Mapping[int, str],
    timeout_seconds: float,
    poll_seconds: float,
    stage_index: int | None = None,
):
    summary = run_batch(
        tuple(key_to_id[key] for key in form_keys),
        mode,
        force_refresh=(phase == 'retry'),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    return _decorate_record(
        summary,
        phase=phase,
        mode=mode,
        form_keys=form_keys,
        id_to_key=id_to_key,
        stage_index=stage_index,
    )


def _stage_observations(payload: Mapping[str, Any]) -> list[StageObservation]:
    observations = []
    for stage in payload.get('stages', ()):
        if not isinstance(stage, Mapping):
            continue
        target = int(stage.get('target', 0) or 0)
        terminal = int(stage.get('terminal_count', 0) or 0)
        failed = int(stage.get('failed', 0) or 0)
        rate_limited = int(stage.get('rate_limited', 0) or 0)
        observations.append(StageObservation(
            concurrency=int(stage.get('concurrency', 0) or 0),
            target=target,
            processed=terminal,
            rate_limited_ratio=(rate_limited / target) if target else 1.0,
            failed_ratio=(failed / target) if target else 1.0,
            progress_updated=terminal == target,
        ))
    return observations


def _run_staged(
    manifest,
    plan,
    *,
    stage_index: int,
    worker_concurrency: int | None,
    key_to_id,
    id_to_key,
    mode_by_key,
    run_batch,
    timeout_seconds: float,
    poll_seconds: float,
):
    if stage_index < 1 or stage_index > len(plan.stages):
        raise RealAcceptanceError('stage index is outside the 4/8/16 ladder')
    if worker_concurrency is not None and worker_concurrency != plan.stages[stage_index - 1].concurrency:
        raise RealAcceptanceError('worker concurrency does not match the requested stage')
    path = _runtime_path(manifest, 'results/real-batches-staged.json')
    payload = _read_json(path, {'phase': 'staged', 'stages': []})
    if not isinstance(payload, dict):
        payload = {'phase': 'staged', 'stages': []}
    existing_stages = [item for item in payload.get('stages', ()) if isinstance(item, Mapping)]
    if any(int(item.get('stage_index', 0) or 0) == stage_index for item in existing_stages):
        return payload
    if len({int(item.get('stage_index', 0) or 0) for item in existing_stages}) < stage_index - 1:
        raise RealAcceptanceError('staged ladder must be executed in order')

    stage = plan.stages[stage_index - 1]
    grouped: dict[str, list[str]] = defaultdict(list)
    for key in stage.form_keys:
        grouped[mode_by_key[key]].append(key)
    records = []
    for mode in ('llm_only', 'combined'):
        keys = tuple(grouped.get(mode, ()))
        if keys:
            records.append(_decorate_and_run(
                run_batch,
                phase='staged',
                mode=mode,
                form_keys=keys,
                key_to_id=key_to_id,
                id_to_key=id_to_key,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                stage_index=stage_index,
            ))
    aggregate = _aggregate_records(records)
    rate_limited = sum(
        1 for record in records for item in record.get('failed_items', ())
        if isinstance(item, Mapping) and item.get('error_code') == 'rate_limited'
    )
    stage_payload = {
        'stage_index': stage_index,
        'concurrency': stage.concurrency,
        'target': stage.target,
        'processed': aggregate['processed'],
        'terminal_count': aggregate['terminal_count'],
        'failed': aggregate['failed'],
        'cancelled': aggregate['cancelled'],
        'rate_limited': rate_limited,
        'status': aggregate['status'],
        'batch_ids': aggregate['batch_ids'],
        'http_attempts': aggregate['http_attempts'],
        'records': records,
    }
    existing_stages.append(stage_payload)
    existing_stages.sort(key=lambda item: int(item.get('stage_index', 0) or 0))
    payload.update({
        'phase': 'staged',
        'stages': existing_stages,
        'forms': sum(int(item.get('target', 0) or 0) for item in existing_stages),
        'processed': sum(int(item.get('terminal_count', 0) or 0) for item in existing_stages),
        'http_attempts': sum(int(item.get('http_attempts', 0) or 0) for item in existing_stages),
        'concurrency': [int(item.get('concurrency')) for item in existing_stages],
    })
    _write_json_atomic(path, payload)
    return payload


def _run_main(
    manifest,
    plan,
    *,
    key_to_id,
    id_to_key,
    run_batch,
    timeout_seconds: float,
    poll_seconds: float,
    worker_concurrency: int | None,
):
    staged_payload = _read_json(_runtime_path(manifest, 'results/real-batches-staged.json'), {})
    if not isinstance(staged_payload, Mapping) or len(staged_payload.get('stages', ())) != len(plan.stages):
        raise RealAcceptanceError('all three real staged ladder artifacts are required before main')
    observations = _stage_observations(staged_payload)
    if len(observations) != len(plan.stages):
        raise RealAcceptanceError('staged ladder observations are incomplete')
    safe = choose_safe_concurrency(observations, fallback=plan.stages[0].concurrency)
    if worker_concurrency is not None and worker_concurrency != safe:
        raise RealAcceptanceError(
            f'worker concurrency {worker_concurrency} does not match selected safe concurrency {safe}'
        )
    path = _runtime_path(manifest, 'results/real-batches-main.json')
    existing = _read_json(path, None)
    if isinstance(existing, Mapping) and existing.get('phase') == 'main':
        return dict(existing), safe

    records = []
    for mode in REVIEW_MODES:
        keys = tuple(plan.main_by_mode[mode])
        if keys:
            records.append(_decorate_and_run(
                run_batch,
                phase='main',
                mode=mode,
                form_keys=keys,
                key_to_id=key_to_id,
                id_to_key=id_to_key,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            ))
    staged_records = [
        record
        for stage in staged_payload.get('stages', ())
        if isinstance(stage, Mapping)
        for record in stage.get('records', ())
        if isinstance(record, Mapping)
    ]
    all_records = staged_records + records
    aggregate = _aggregate_records(records)
    cumulative = {mode: len(plan.staged_by_mode[mode]) + len(plan.main_by_mode[mode]) for mode in REVIEW_MODES}
    payload = {
        'phase': 'main',
        'safe_concurrency': safe,
        'records': records,
        'cumulative_mode_counts': cumulative,
        'target': sum(cumulative.values()),
        'processed': sum(int(item.get('terminal_count', 0) or 0) for item in all_records),
        'failed': sum(int(item.get('failed', 0) or 0) for item in all_records),
        'status': 'completed_with_errors' if any(
            item.get('status') != 'completed' for item in all_records
        ) else 'completed',
        'http_attempts': sum(int(item.get('http_attempts', 0) or 0) for item in all_records),
        'logical_llm_reviews': sum(cumulative[mode] for mode in ('llm_only', 'combined')),
        'batch_ids': aggregate['batch_ids'],
    }
    _write_json_atomic(path, payload)
    return payload, safe


def _run_retry(
    manifest,
    *,
    key_to_id,
    id_to_key,
    mode_by_key,
    run_batch,
    records,
    timeout_seconds: float,
    poll_seconds: float,
):
    path = _runtime_path(manifest, 'results/real-batches-retry.json')
    existing = _read_json(path, None)
    if isinstance(existing, Mapping) and existing.get('phase') == 'retry':
        return dict(existing)
    failed_by_mode: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record.get('phase') not in {'staged', 'main'}:
            continue
        mode = str(record.get('mode'))
        for item in record.get('failed_items', ()):
            if not isinstance(item, Mapping) or item.get('error_code') not in TRANSIENT_ERROR_CODES:
                continue
            key = item.get('form_key')
            if isinstance(key, str) and key not in failed_by_mode[mode]:
                failed_by_mode[mode].append(key)
    current_attempts = sum(int(item.get('http_attempts', 0) or 0) for item in records)
    remaining = int(manifest.config.http_attempt_ceiling) - current_attempts
    retry_count = sum(len(values) for values in failed_by_mode.values())
    if retry_count > remaining:
        raise RealAcceptanceError('retry budget would exceed the acceptance HTTP ceiling')
    retry_records = []
    for mode in REVIEW_MODES:
        keys = tuple(failed_by_mode.get(mode, ()))
        if keys:
            retry_records.append(_decorate_and_run(
                run_batch,
                phase='retry',
                mode=mode,
                form_keys=keys,
                key_to_id=key_to_id,
                id_to_key=id_to_key,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            ))
    aggregate = _aggregate_records(retry_records)
    payload = {
        'phase': 'retry',
        'retried': retry_count,
        'records': retry_records,
        'status': aggregate['status'] if retry_records else 'completed',
        'processed': aggregate['processed'],
        'failed': aggregate['failed'],
        'http_attempts': aggregate['http_attempts'],
        'remaining_budget_before': remaining,
    }
    _write_json_atomic(path, payload)
    return payload


def _run_cache(
    manifest,
    *,
    key_to_id,
    id_to_key,
    mode_by_key,
    run_batch,
    records,
    sample_size: int,
    timeout_seconds: float,
    poll_seconds: float,
):
    path = _runtime_path(manifest, 'results/real-batches-cache.json')
    existing = _read_json(path, None)
    if isinstance(existing, Mapping) and existing.get('phase') == 'cache':
        return dict(existing)
    if not isinstance(sample_size, int) or sample_size != 50:
        raise RealAcceptanceError('real cache phase requires exactly 50 unchanged forms')
    successful_by_mode: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record.get('phase') not in {'staged', 'main', 'retry'}:
            continue
        mode = str(record.get('mode'))
        if mode not in {'llm_only', 'combined'}:
            continue
        for key in record.get('successful_form_keys', ()):
            if isinstance(key, str) and key not in successful_by_mode[mode]:
                successful_by_mode[mode].append(key)
    selected = {
        'llm_only': tuple(successful_by_mode['llm_only'][:25]),
        'combined': tuple(successful_by_mode['combined'][:25]),
    }
    if sum(len(values) for values in selected.values()) != sample_size:
        raise RealAcceptanceError('fewer than 50 successful unchanged LLM forms are available for cache reuse')
    cache_records = []
    for mode in ('llm_only', 'combined'):
        cache_records.append(_decorate_and_run(
            run_batch,
            phase='cache',
            mode=mode,
            form_keys=selected[mode],
            key_to_id=key_to_id,
            id_to_key=id_to_key,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        ))
    aggregate = _aggregate_records(cache_records)
    if aggregate['cache_hits'] != sample_size or aggregate['http_attempts'] != 0:
        raise RealAcceptanceError('real cache reuse did not produce 50 hits with zero new HTTP attempts')
    payload = {
        'phase': 'cache',
        'sample_size': sample_size,
        'cache_hits': aggregate['cache_hits'],
        'http_attempt_delta': aggregate['http_attempts'],
        'records': cache_records,
        'status': aggregate['status'],
    }
    _write_json_atomic(path, payload)
    return payload


def run_real_phase(
    manifest,
    phase: str,
    *,
    stage_index: int | None = None,
    worker_concurrency: int | None = None,
    sample_size: int = 50,
    timeout_seconds: float = 3600,
    poll_seconds: float = 2,
) -> dict[str, Any]:
    """Run one explicit real phase against the isolated Flask/Celery runtime."""

    _require_real_environment(manifest)
    plan = plan_real_batches(
        manifest.forms,
        levels=manifest.config.staged_concurrency,
        size_each=manifest.config.staged_size_each,
        per_mode=manifest.config.per_mode,
    )
    if phase == 'staged' and stage_index is None:
        raise RealAcceptanceError('real staged phase requires --stage-index 1, 2 or 3')
    if phase not in {'staged', 'main', 'retry', 'cache'}:
        raise RealAcceptanceError(f'unsupported real phase: {phase}')

    from app.app import app
    from app.models import LectureForm, db
    from app.review_automation import get_celery_app
    from app.review_automation.models import ReviewBatch, ReviewBatchItem

    with app.app_context():
        celery = get_celery_app(app)
        if celery.conf.task_always_eager:
            raise RealAcceptanceError('Celery app is eager; start the recorded real worker first')
        key_to_id, id_to_key, mode_by_key = _form_maps(manifest, LectureForm)
        run_batch = _production_batch_callbacks(
            manifest, app, db, ReviewBatch, ReviewBatchItem,
        )
        records = _load_records(manifest)
        if phase == 'staged':
            payload = _run_staged(
                manifest,
                plan,
                stage_index=int(stage_index),
                worker_concurrency=worker_concurrency,
                key_to_id=key_to_id,
                id_to_key=id_to_key,
                mode_by_key=mode_by_key,
                run_batch=run_batch,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
            stage_records = [
                record
                for stage in payload.get('stages', ())
                if isinstance(stage, Mapping)
                for record in stage.get('records', ())
                if isinstance(record, Mapping)
            ]
            existing_non_stage = [item for item in records if item.get('phase') not in {'staged'}]
            _save_records(manifest, existing_non_stage + stage_records)
            observations = _stage_observations(payload)
            safe = choose_safe_concurrency(
                observations,
                fallback=manifest.config.staged_concurrency[0],
            ) if len(observations) == len(plan.stages) else None
            _state_after_records(
                manifest,
                existing_non_stage + stage_records,
                phase='staged',
                safe_concurrency=safe,
            )
            return payload
        if phase == 'main':
            payload, safe = _run_main(
                manifest,
                plan,
                key_to_id=key_to_id,
                id_to_key=id_to_key,
                run_batch=run_batch,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                worker_concurrency=worker_concurrency,
            )
            if not any(item.get('phase') == 'main' for item in records):
                main_records = [dict(item) for item in payload.get('records', ())]
                staged_records = [
                    record
                    for stage in _read_json(_runtime_path(manifest, 'results/real-batches-staged.json'), {}).get('stages', ())
                    if isinstance(stage, Mapping)
                    for record in stage.get('records', ())
                    if isinstance(record, Mapping)
                ]
                records = staged_records + main_records
                _save_records(manifest, records)
            _state_after_records(manifest, records, phase='main', safe_concurrency=safe)
            _write_batch_statistics(manifest, records)
            _write_deepseek_summary(
                manifest, records, LectureForm, ReviewBatchItem, db, key_to_id,
            )
            return payload
        if phase == 'retry':
            payload = _run_retry(
                manifest,
                key_to_id=key_to_id,
                id_to_key=id_to_key,
                mode_by_key=mode_by_key,
                run_batch=run_batch,
                records=records,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
            retry_records = [dict(item) for item in payload.get('records', ())]
            if retry_records:
                records.extend(retry_records)
                _save_records(manifest, records)
            _state_after_records(manifest, records, phase='retry')
            _write_batch_statistics(manifest, records)
            _write_deepseek_summary(
                manifest, records, LectureForm, ReviewBatchItem, db, key_to_id,
            )
            return payload
        payload = _run_cache(
            manifest,
            key_to_id=key_to_id,
            id_to_key=id_to_key,
            mode_by_key=mode_by_key,
            run_batch=run_batch,
            records=records,
            sample_size=sample_size,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
        cache_records = [dict(item) for item in payload.get('records', ())]
        if cache_records:
            records.extend(cache_records)
            _save_records(manifest, records)
        _state_after_records(manifest, records, phase='cache')
        _write_batch_statistics(manifest, records)
        _write_deepseek_summary(
            manifest, records, LectureForm, ReviewBatchItem, db, key_to_id,
        )
        return payload


__all__ = ['RealAcceptanceError', 'RealBatchTimeout', 'run_real_phase']
