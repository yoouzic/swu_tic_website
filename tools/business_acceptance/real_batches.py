"""Small, dependency-injected helpers for the real Celery acceptance run.

The production task and Celery app remain the source of truth.  These helpers
only poll persisted batch rows and reduce sanitized counters; they never import
the Flask application at module import time and never log model payloads.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Iterable, Mapping


TERMINAL_STATUSES = frozenset({
    'completed',
    'completed_with_errors',
    'failed',
    'cancelled',
})


class RealBatchTimeout(RuntimeError):
    """Raised when a persisted real batch does not reach a terminal state."""


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if fraction == 0.5 and len(ordered) % 2 == 0:
        middle = len(ordered) // 2
        return int((ordered[middle - 1] + ordered[middle]) / 2)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return int(ordered[index])


def summarize_batch(batch: Any, items: Iterable[Any]) -> dict[str, Any]:
    """Return counters and bounded timings from persisted batch rows only."""

    values = list(items)
    target = int(_value(batch, 'target_form_count', len(values)) or 0)
    status = str(_value(batch, 'status', 'failed'))
    completed = [item for item in values if _value(item, 'status') == 'completed']
    failed_items = [item for item in values if _value(item, 'status') == 'failed']
    cancelled_items = [item for item in values if _value(item, 'status') == 'cancelled']
    terminal_count = len(completed) + len(failed_items) + len(cancelled_items)
    if status == 'failed' and not values:
        failed_count = target
    else:
        failed_count = len(failed_items)
    cancelled_count = len(cancelled_items)
    durations = [
        int(_value(item, 'duration_ms', 0) or 0)
        for item in values
        if _value(item, 'duration_ms') is not None
        and int(_value(item, 'duration_ms', 0) or 0) >= 0
    ]
    category_counts: dict[str, int] = {}
    for item in completed + failed_items:
        category = _value(item, 'category')
        if category:
            category_counts[str(category)] = category_counts.get(str(category), 0) + 1
    failed_records = [
        {
            'form_id': _value(item, 'form_id'),
            'error_code': _value(item, 'error_code'),
        }
        for item in failed_items
    ]
    return {
        'status': status,
        'target': target,
        'processed': len(completed),
        'terminal_count': terminal_count,
        'failed': failed_count,
        'cancelled': cancelled_count,
        'classified': sum(bool(_value(item, 'category')) for item in completed),
        'cache_hits': sum(bool(_value(item, 'cache_hit', False)) for item in values),
        'http_attempts': sum(int(_value(item, 'http_attempts', 0) or 0) for item in values),
        'category_counts': category_counts,
        'failed_items': failed_records,
        'successful_form_ids': [_value(item, 'form_id') for item in completed],
        'p50_duration_ms': _percentile(durations, 0.5),
        'p95_duration_ms': _percentile(durations, 0.95),
        'max_duration_ms': max(durations, default=0),
        'duration_samples': len(durations),
    }


def wait_for_batch(
    batch_id: str,
    *,
    refresh: Callable[[str], tuple[Any, Iterable[Any]]],
    timeout_seconds: float = 3600,
    poll_seconds: float = 2,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll persisted state until a real Celery batch reaches a terminal state."""

    if timeout_seconds <= 0:
        raise ValueError('batch timeout must be positive')
    started = clock()
    while True:
        batch, items = refresh(str(batch_id))
        if batch is None:
            raise KeyError(f'unknown real batch: {batch_id}')
        status = str(_value(batch, 'status', 'failed'))
        if status in TERMINAL_STATUSES:
            result = summarize_batch(batch, items)
            result['batch_id'] = str(batch_id)
            result['elapsed_seconds'] = round(max(0.0, clock() - started), 3)
            return result
        if clock() - started >= timeout_seconds:
            raise RealBatchTimeout(
                f'real batch {batch_id} did not reach terminal state before timeout'
            )
        if poll_seconds > 0:
            sleep(poll_seconds)


def run_created_batch(
    form_ids: Iterable[int],
    mode: str,
    *,
    create: Callable[[tuple[int, ...], str], Any],
    refresh: Callable[[str], tuple[Any, Iterable[Any]]],
    timeout_seconds: float = 3600,
    poll_seconds: float = 2,
) -> dict[str, Any]:
    """Create one production batch and wait for its persisted terminal result."""

    ids = tuple(int(value) for value in form_ids)
    if not ids:
        raise ValueError('real batch requires at least one form ID')
    created = create(ids, str(mode))
    batch_id = _value(created, 'id', created)
    if batch_id is None:
        raise ValueError('production batch creation did not return an ID')
    result = wait_for_batch(
        str(batch_id),
        refresh=refresh,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    result['mode'] = str(mode)
    return result


__all__ = [
    'RealBatchTimeout',
    'TERMINAL_STATUSES',
    'run_created_batch',
    'summarize_batch',
    'wait_for_batch',
]
