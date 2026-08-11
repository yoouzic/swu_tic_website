"""Pure acceptance-batch orchestration helpers.

The production Celery tasks remain the source of truth for review execution.
This module supplies the deterministic budget, stage, cache, and run-state
contracts used by the acceptance harness and its fake-client tests.  It does
not perform HTTP, Redis, or model calls by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Any, Iterable, Mapping, Sequence


REVIEW_MODES = ('rules_only', 'llm_only', 'combined')
TERMINAL_STATUSES = frozenset({'completed', 'failed', 'cancelled'})
TRANSIENT_ERROR_CODES = frozenset({'timeout', 'rate_limited', 'provider_unavailable', 'transient'})


class RequestBudgetExceeded(RuntimeError):
    """Raised before a request would exceed the configured HTTP ceiling."""


class BatchClosureError(RuntimeError):
    """Raised when a batch is closed before every target item is terminal."""


class AttemptLimitExceeded(RuntimeError):
    """Raised when one uncached item reports more attempts than allowed."""


class RulesOnlyHttpAttempt(RuntimeError):
    """Raised when a rules-only adapter reports any external HTTP attempt."""


@dataclass
class RunState:
    phase: str = 'prepared'
    batch_ids: dict[str, str] = field(default_factory=dict)
    logical_llm_reviews: int = 0
    http_attempts: int = 0
    http_attempt_ceiling: int = 1200
    safe_concurrency: int | None = None
    last_completed_form_id: int | str | None = None

    def reserve_http_attempts(self, amount: int) -> int:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError('HTTP attempt amount must be a non-negative integer')
        if self.http_attempts + amount > self.http_attempt_ceiling:
            raise RequestBudgetExceeded(
                f'HTTP attempt ceiling {self.http_attempt_ceiling} would be exceeded'
            )
        self.http_attempts += amount
        return self.http_attempts

    def release_http_attempts(self, amount: int) -> int:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError('HTTP release amount must be a non-negative integer')
        if amount > self.http_attempts:
            raise ValueError('cannot release more HTTP attempts than recorded')
        self.http_attempts -= amount
        return self.http_attempts

    def record_http_attempts(self, amount: int) -> int:
        return self.reserve_http_attempts(amount)

    def to_dict(self) -> dict[str, Any]:
        return {
            'phase': self.phase,
            'batch_ids': dict(self.batch_ids),
            'logical_llm_reviews': int(self.logical_llm_reviews),
            'http_attempts': int(self.http_attempts),
            'http_attempt_ceiling': int(self.http_attempt_ceiling),
            'safe_concurrency': self.safe_concurrency,
            'last_completed_form_id': self.last_completed_form_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> 'RunState':
        return cls(
            phase=str(payload.get('phase', 'prepared')),
            batch_ids={str(key): str(value) for key, value in dict(payload.get('batch_ids', {})).items()},
            logical_llm_reviews=int(payload.get('logical_llm_reviews', 0) or 0),
            http_attempts=int(payload.get('http_attempts', 0) or 0),
            http_attempt_ceiling=int(payload.get('http_attempt_ceiling', 1200) or 1200),
            safe_concurrency=(
                None if payload.get('safe_concurrency') is None
                else int(payload['safe_concurrency'])
            ),
            last_completed_form_id=payload.get('last_completed_form_id'),
        )

    def save(self, path: str | Path) -> Path:
        """Atomically replace a state file using a sibling temporary file."""

        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
        encoded = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2,
        ) + '\n'
        try:
            temporary.write_text(encoded, encoding='utf-8', newline='\n')
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    @classmethod
    def load(cls, path: str | Path) -> 'RunState':
        source = Path(path).expanduser().resolve()
        payload = json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(payload, Mapping):
            raise ValueError('run state must be a JSON object')
        return cls.from_dict(payload)


@dataclass(frozen=True)
class Stage:
    concurrency: int
    form_ids: tuple[int | str, ...]


def build_stages(
    form_ids: Sequence[int | str],
    *,
    levels: Sequence[int] = (4, 8, 16),
    size_each: int = 20,
) -> tuple[Stage, ...]:
    if not levels or any(not isinstance(level, int) or level < 1 for level in levels):
        raise ValueError('stage concurrency levels must be positive integers')
    if not isinstance(size_each, int) or size_each < 1:
        raise ValueError('stage size must be a positive integer')
    values = list(form_ids)
    if len(set(values)) != len(values):
        raise ValueError('staged form IDs must be unique')
    required = len(levels) * size_each
    if len(values) < required:
        raise ValueError('staged form IDs do not contain enough forms')
    return tuple(
        Stage(
            concurrency=level,
            form_ids=tuple(values[index * size_each:(index + 1) * size_each]),
        )
        for index, level in enumerate(levels)
    )


def partition_mode_form_ids(
    form_ids: Sequence[int | str],
    *,
    per_mode: int = 500,
) -> dict[str, tuple[int | str, ...]]:
    if not isinstance(per_mode, int) or per_mode < 1:
        raise ValueError('per_mode must be a positive integer')
    values = list(form_ids)
    if len(values) != per_mode * len(REVIEW_MODES):
        raise ValueError('mode form IDs do not close over three equal batches')
    if len(set(values)) != len(values):
        raise ValueError('mode form IDs must be mutually exclusive')
    return {
        mode: tuple(values[index * per_mode:(index + 1) * per_mode])
        for index, mode in enumerate(REVIEW_MODES)
    }


@dataclass(frozen=True)
class AdapterResult:
    classification: str | None = None
    http_attempts: int = 0
    error_code: str | None = None
    classified: bool | None = None
    assessment_id: str | None = None

    def __post_init__(self):
        if self.classified is None:
            object.__setattr__(
                self,
                'classified',
                bool(self.classification) and not self.error_code,
            )


@dataclass(frozen=True)
class BatchForm:
    form_id: int | str
    form_version: int | str


@dataclass(frozen=True)
class CachedAssessment:
    assessment_id: str
    result: AdapterResult


class AssessmentCache:
    """Version- and mode-aware cache used for deterministic acceptance checks."""

    def __init__(self):
        self._entries: dict[tuple[str, str, str], CachedAssessment] = {}

    @staticmethod
    def _key(form: BatchForm, mode: str) -> tuple[str, str, str]:
        return str(mode), str(form.form_id), str(form.form_version)

    def get(self, form: BatchForm, mode: str) -> CachedAssessment | None:
        return self._entries.get(self._key(form, mode))

    def put(self, form: BatchForm, mode: str, result: AdapterResult) -> CachedAssessment:
        key = self._key(form, mode)
        assessment_id = hashlib.sha256(
            json.dumps(key, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        ).hexdigest()
        entry = CachedAssessment(assessment_id=assessment_id, result=result)
        self._entries[key] = entry
        return entry

    @property
    def assessment_count(self) -> int:
        return len(self._entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            'entries': [
                {
                    'mode': key[0],
                    'form_id': key[1],
                    'form_version': key[2],
                    'assessment_id': entry.assessment_id,
                    'result': asdict(entry.result),
                }
                for key, entry in sorted(self._entries.items())
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> 'AssessmentCache':
        cache = cls()
        for item in payload.get('entries', ()):
            if not isinstance(item, Mapping):
                continue
            result = AdapterResult(**dict(item.get('result', {})))
            key = (
                str(item.get('mode')),
                str(item.get('form_id')),
                str(item.get('form_version')),
            )
            cache._entries[key] = CachedAssessment(
                assessment_id=str(item.get('assessment_id')),
                result=result,
            )
        return cache


@dataclass(frozen=True)
class BatchItem:
    form_id: int | str
    form_version: int | str
    status: str
    classified: bool
    category: str | None = None
    error_code: str | None = None
    cache_hit: bool = False
    http_attempts: int = 0
    assessment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatchSummary:
    target: int
    processed: int
    classified: int
    failed: int
    cancelled: int
    cache_hits: int
    http_attempts: int
    status: str
    items: tuple[BatchItem, ...] = ()


def _item_value(item: BatchItem | Mapping[str, Any], name: str, default=None):
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _coerce_item(item: BatchItem | Mapping[str, Any]) -> BatchItem:
    if isinstance(item, BatchItem):
        return item
    return BatchItem(
        form_id=_item_value(item, 'form_id'),
        form_version=_item_value(item, 'form_version', ''),
        status=str(_item_value(item, 'status', 'failed')),
        classified=bool(_item_value(item, 'classified', False)),
        category=_item_value(item, 'category'),
        error_code=_item_value(item, 'error_code'),
        cache_hit=bool(_item_value(item, 'cache_hit', False)),
        http_attempts=int(_item_value(item, 'http_attempts', 0) or 0),
        assessment_id=_item_value(item, 'assessment_id'),
    )


def close_batch(
    items: Iterable[BatchItem | Mapping[str, Any]],
    *,
    target: int | None = None,
) -> BatchSummary:
    values = list(items)
    target_value = len(values) if target is None else int(target)
    if len(values) != target_value:
        raise BatchClosureError(
            f'batch received {len(values)} items for target {target_value}'
        )
    if any(_item_value(item, 'status') not in TERMINAL_STATUSES for item in values):
        raise BatchClosureError('batch contains a non-terminal item')
    form_ids = [_item_value(item, 'form_id') for item in values]
    if any(form_id is None for form_id in form_ids) or len(set(form_ids)) != len(form_ids):
        raise BatchClosureError('batch contains duplicate or missing form IDs')
    terminal = values
    classified = sum(bool(_item_value(item, 'classified', False)) for item in terminal)
    failed = sum(_item_value(item, 'status') == 'failed' for item in terminal)
    cancelled = sum(_item_value(item, 'status') == 'cancelled' for item in terminal)
    cache_hits = sum(bool(_item_value(item, 'cache_hit', False)) for item in terminal)
    http_attempts = sum(int(_item_value(item, 'http_attempts', 0) or 0) for item in terminal)
    status = 'completed_with_errors' if failed or cancelled else 'completed'
    typed_items = tuple(_coerce_item(item) for item in terminal)
    return BatchSummary(
        target=target_value,
        processed=len(terminal),
        classified=classified,
        failed=failed,
        cancelled=cancelled,
        cache_hits=cache_hits,
        http_attempts=http_attempts,
        status=status,
        items=typed_items,
    )


def _normalize_mode(mode: Any) -> str:
    value = getattr(mode, 'value', mode)
    value = str(value)
    if value not in REVIEW_MODES:
        raise ValueError(f'unsupported review mode: {value}')
    return value


def _normalize_form(value: BatchForm | Mapping[str, Any]) -> BatchForm:
    if isinstance(value, BatchForm):
        return value
    if not isinstance(value, Mapping):
        raise TypeError('batch form must be BatchForm or mapping')
    if 'form_id' not in value or 'form_version' not in value:
        raise ValueError('batch form requires form_id and form_version')
    return BatchForm(form_id=value['form_id'], form_version=value['form_version'])


def _sync_actual_attempts(state: RunState, planned: int, actual: int) -> None:
    if actual < 0:
        raise ValueError('adapter returned a negative HTTP attempt count')
    if actual > planned:
        state.reserve_http_attempts(actual - planned)
    elif planned > actual:
        state.release_http_attempts(planned - actual)


def run_batch(
    forms: Iterable[BatchForm | Mapping[str, Any]],
    adapter: Any,
    state: RunState,
    cache: AssessmentCache,
    *,
    mode: str,
    transient_retries: int = 0,
) -> BatchSummary:
    """Run one fake/real adapter batch while preserving attempts and cache keys."""

    normalized_mode = _normalize_mode(mode)
    if not isinstance(transient_retries, int) or transient_retries < 0:
        raise ValueError('transient_retries must be a non-negative integer')
    normalized_forms = [_normalize_form(form) for form in forms]
    items: list[BatchItem] = []
    for form in normalized_forms:
        cached = cache.get(form, normalized_mode)
        if cached is not None:
            result = cached.result
            items.append(BatchItem(
                form_id=form.form_id,
                form_version=form.form_version,
                status='completed',
                classified=bool(result.classified),
                category=result.classification,
                error_code=result.error_code,
                cache_hit=True,
                http_attempts=0,
                assessment_id=cached.assessment_id,
            ))
            state.last_completed_form_id = form.form_id
            continue

        if normalized_mode != 'rules_only':
            state.logical_llm_reviews += 1
        result: AdapterResult | None = None
        total_attempts = 0
        error_code = None
        max_calls = transient_retries + 1
        for call_index in range(max_calls):
            planned = 0 if normalized_mode == 'rules_only' else 1
            if planned:
                state.reserve_http_attempts(planned)
            try:
                candidate = adapter.assess(form, mode=normalized_mode)
                if isinstance(candidate, Mapping):
                    result = AdapterResult(**dict(candidate))
                elif isinstance(candidate, AdapterResult):
                    result = candidate
                else:
                    raise TypeError('adapter returned an invalid result')
            except Exception:
                result = AdapterResult(
                    classification=None,
                    http_attempts=planned,
                    error_code='adapter_exception',
                    classified=False,
                )
            actual = int(result.http_attempts or 0)
            if normalized_mode == 'rules_only' and actual:
                state.reserve_http_attempts(actual)
                raise RulesOnlyHttpAttempt(
                    'rules-only adapter reported an external HTTP attempt'
                )
            _sync_actual_attempts(state, planned, actual)
            if actual > 1 and transient_retries == 0:
                raise AttemptLimitExceeded(
                    'an uncached item used more than one HTTP attempt with retries disabled'
                )
            total_attempts += actual
            error_code = result.error_code
            if error_code not in TRANSIENT_ERROR_CODES or call_index >= max_calls - 1:
                break
        assert result is not None

        if result.error_code:
            items.append(BatchItem(
                form_id=form.form_id,
                form_version=form.form_version,
                status='failed',
                classified=False,
                category=result.classification,
                error_code=result.error_code,
                http_attempts=total_attempts,
                assessment_id=result.assessment_id,
            ))
        else:
            entry = cache.put(form, normalized_mode, result)
            items.append(BatchItem(
                form_id=form.form_id,
                form_version=form.form_version,
                status='completed',
                classified=bool(result.classified),
                category=result.classification,
                http_attempts=total_attempts,
                assessment_id=entry.assessment_id,
            ))
        state.last_completed_form_id = form.form_id
    return close_batch(items, target=len(normalized_forms))


@dataclass(frozen=True)
class StageObservation:
    concurrency: int
    target: int
    processed: int
    rate_limited_ratio: float
    failed_ratio: float
    progress_updated: bool

    @property
    def passes(self) -> bool:
        return (
            self.target == self.processed
            and self.progress_updated
            and self.rate_limited_ratio <= 0.05
            and self.failed_ratio <= 0.05
        )


def choose_safe_concurrency(
    observations: Iterable[StageObservation],
    *,
    fallback: int = 4,
) -> int:
    passing = [observation.concurrency for observation in observations if observation.passes]
    return max(passing) if passing else fallback


__all__ = [
    'AdapterResult',
    'AttemptLimitExceeded',
    'RulesOnlyHttpAttempt',
    'AssessmentCache',
    'BatchClosureError',
    'BatchForm',
    'BatchItem',
    'BatchSummary',
    'RequestBudgetExceeded',
    'RunState',
    'Stage',
    'StageObservation',
    'build_stages',
    'choose_safe_concurrency',
    'close_batch',
    'partition_mode_form_ids',
    'run_batch',
]
