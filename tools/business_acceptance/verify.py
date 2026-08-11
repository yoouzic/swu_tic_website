"""Database-independent checks for the synthetic business-acceptance evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


REQUIRED_VERIFICATION_CHECKS = (
    'ORG_COUNTS',
    'LOGICAL_FORM_COUNTS',
    'BATCH_MODE_ISOLATION',
    'BATCH_COUNT_CLOSURE',
    'HTTP_BUDGET',
    'CACHE_REUSE',
    'AUTOMATION_HUMAN_FIELD_IMMUTABILITY',
    'GROUP_SCOPE',
    'DEPARTMENT_SCOPE',
    'CENTER_SCOPE',
    'SUPERADMIN_SCOPE',
    'STATUS_TRANSITIONS',
    'REJECTION_RETURN',
    'VERSION_HISTORY',
    'PAGINATION_UNIQUENESS',
    'EVIDENCE_CLASSIFICATION_ALIGNMENT',
    'COMBINED_SOURCE_PRESERVATION',
    'STATISTICS_ALIGNMENT',
    'EXPORT_ALIGNMENT',
)


@dataclass(frozen=True)
class VerificationResult:
    check: str
    code: str
    passed: bool
    message: str = ''
    details: dict[str, Any] = field(default_factory=dict)
    blocked: bool = False

    @property
    def conclusion(self) -> str:
        if self.passed:
            return 'PASS'
        return 'BLOCKED' if self.blocked else 'FAIL'

    def to_dict(self) -> dict[str, Any]:
        return {
            'check': self.check,
            'code': self.code,
            'passed': self.passed,
            'conclusion': self.conclusion,
            'message': self.message,
            'details': dict(self.details),
        }


def _result(
    check: str,
    code: str,
    message: str = '',
    *,
    blocked: bool = False,
    **details: Any,
) -> VerificationResult:
    return VerificationResult(
        check=check,
        code=code,
        passed=code == 'PASS',
        message=message,
        details=details,
        blocked=blocked,
    )


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def verify_form_counts(expected: int, actual: int) -> VerificationResult:
    if expected != actual:
        return _result(
            'FORM_COUNTS',
            'FORM_COUNT_MISMATCH',
            'expected and actual form counts differ',
            expected=int(expected),
            actual=int(actual),
        )
    return _result('FORM_COUNTS', 'PASS', expected=int(expected), actual=int(actual))


def verify_org_counts(
    expected_officers: int,
    actual_officers: int,
    *,
    expected_administrators: int | None = None,
    actual_administrators: int | None = None,
) -> VerificationResult:
    details: dict[str, Any] = {
        'expected_officers': int(expected_officers),
        'actual_officers': int(actual_officers),
    }
    if expected_administrators is not None or actual_administrators is not None:
        details.update({
            'expected_administrators': None if expected_administrators is None else int(expected_administrators),
            'actual_administrators': None if actual_administrators is None else int(actual_administrators),
        })
    if int(expected_officers) != int(actual_officers) or (
        expected_administrators is not None
        and actual_administrators is not None
        and int(expected_administrators) != int(actual_administrators)
    ):
        return _result(
            'ORG_COUNTS',
            'ORG_COUNT_MISMATCH',
            'organization counts do not match the synthetic acceptance matrix',
            **details,
        )
    return _result('ORG_COUNTS', 'PASS', **details)


def _logical_key(row: Any) -> Any:
    for key in ('unique_id', 'synthetic_key', 'form_id', 'id'):
        value = _value(row, key)
        if value is not None:
            return value
    return None


def _version_sort_key(row: Any) -> tuple[str, str]:
    return (
        str(_value(row, 'updated_at', _value(row, 'created_at', '')) or ''),
        str(_value(row, 'id', _value(row, 'form_id', '')) or ''),
    )


def latest_logical_rows(rows: Iterable[Any]) -> tuple[Any, ...]:
    """Return one latest row per logical form without mutating the source."""

    latest: dict[str, Any] = {}
    for row in rows:
        logical_id = _logical_key(row)
        if logical_id is None:
            continue
        key = str(logical_id)
        previous = latest.get(key)
        if previous is None or _version_sort_key(row) > _version_sort_key(previous):
            latest[key] = row
    return tuple(latest.values())


def verify_logical_form_counts(expected: int, rows: int | Iterable[Any]) -> VerificationResult:
    actual = int(rows) if isinstance(rows, int) else len(latest_logical_rows(rows))
    if int(expected) != actual:
        return _result(
            'LOGICAL_FORM_COUNTS',
            'LOGICAL_FORM_COUNT_MISMATCH',
            'latest logical-form count differs from the expected count',
            expected=int(expected),
            actual=actual,
        )
    return _result('LOGICAL_FORM_COUNTS', 'PASS', expected=int(expected), actual=actual)


def _page_rows(page: Any) -> Iterable[Any]:
    if isinstance(page, Mapping):
        rows = page.get('forms', page.get('rows', ()))
        if isinstance(rows, Iterable) and not isinstance(rows, (str, bytes, Mapping)):
            return rows
        return (page,)
    if isinstance(page, Iterable) and not isinstance(page, (str, bytes, Mapping)):
        return page
    return (page,)


def _row_id(row: Any) -> Any:
    if isinstance(row, Mapping):
        for key in ('id', 'form_id', 'unique_id'):
            if key in row:
                return row[key]
        return None
    for key in ('id', 'form_id', 'unique_id'):
        value = getattr(row, key, None)
        if value is not None:
            return value
    return row


def verify_pagination(pages: Iterable[Any]) -> VerificationResult:
    seen = set()
    for page in pages:
        for row in _page_rows(page):
            row_id = _row_id(row)
            if row_id is None:
                continue
            if row_id in seen:
                return _result(
                    'PAGINATION_UNIQUENESS',
                    'PAGINATION_DUPLICATE',
                    'the same business ID appeared on more than one page',
                )
            seen.add(row_id)
    return _result('PAGINATION_UNIQUENESS', 'PASS', unique_ids=len(seen))


def verify_scope(
    rows: Iterable[Any],
    *,
    allowed_departments: set[str] | None = None,
    allowed_groups: set[str] | None = None,
) -> VerificationResult:
    for row in rows:
        department = _value(row, 'department')
        group = _value(row, 'group')
        if allowed_departments is not None and department not in allowed_departments:
            return _result(
                'REVIEW_SCOPE',
                'REVIEW_SCOPE_MISMATCH',
                'a row is outside the allowed department scope',
            )
        if allowed_groups is not None and group not in allowed_groups:
            return _result(
                'REVIEW_SCOPE',
                'REVIEW_SCOPE_MISMATCH',
                'a row is outside the allowed group scope',
            )
    return _result('REVIEW_SCOPE', 'PASS')


def verify_batch_mode_isolation(
    rows: Iterable[Any],
    *,
    expected_counts: Mapping[str, int] | None = None,
) -> VerificationResult:
    """Ensure a logical form belongs to one and only one review mode."""

    by_form: dict[str, str] = {}
    counts: dict[str, int] = {}
    valid_modes = {'rules_only', 'llm_only', 'combined'}
    for row in rows:
        form_id = _value(row, 'form_id', _value(row, 'synthetic_key', _value(row, 'id')))
        mode = _value(row, 'review_mode', _value(row, 'mode'))
        if form_id is None or mode not in valid_modes:
            return _result(
                'BATCH_MODE_ISOLATION',
                'BATCH_MODE_ISOLATION_MISMATCH',
                'each batch row requires a recognized mode and form ID',
            )
        key = str(form_id)
        previous = by_form.get(key)
        if previous is not None:
            return _result(
                'BATCH_MODE_ISOLATION',
                'BATCH_MODE_ISOLATION_MISMATCH',
                'one form ID appears more than once in the batch matrix',
                form_id=key,
                modes=[previous, mode],
            )
        by_form[key] = str(mode)
        counts[str(mode)] = counts.get(str(mode), 0) + 1
    if expected_counts is not None:
        normalized_expected = {str(key): int(value) for key, value in expected_counts.items()}
        if counts != normalized_expected:
            return _result(
                'BATCH_MODE_ISOLATION',
                'BATCH_MODE_ISOLATION_MISMATCH',
                'review-mode counts do not match the fixed batch matrix',
                expected=normalized_expected,
                actual=counts,
            )
    return _result('BATCH_MODE_ISOLATION', 'PASS', counts=counts, unique_forms=len(by_form))


def verify_http_budget(actual: int, ceiling: int) -> VerificationResult:
    if int(actual) > int(ceiling):
        return _result(
            'HTTP_BUDGET',
            'HTTP_BUDGET_EXCEEDED',
            'recorded HTTP attempts exceed the acceptance ceiling',
            actual=int(actual),
            ceiling=int(ceiling),
        )
    return _result('HTTP_BUDGET', 'PASS', actual=int(actual), ceiling=int(ceiling))


def verify_cache_reuse(
    cache_hits: int,
    http_attempt_delta: int,
    *,
    expected_hits: int = 50,
) -> VerificationResult:
    if int(cache_hits) != int(expected_hits) or int(http_attempt_delta) != 0:
        return _result(
            'CACHE_REUSE',
            'CACHE_REUSE_MISMATCH',
            'unchanged cached forms did not reuse all assessments without HTTP attempts',
            expected_hits=int(expected_hits),
            cache_hits=int(cache_hits),
            http_attempt_delta=int(http_attempt_delta),
        )
    return _result(
        'CACHE_REUSE',
        'PASS',
        expected_hits=int(expected_hits),
        cache_hits=int(cache_hits),
        http_attempt_delta=0,
    )


def verify_combined_sources(record: Any) -> VerificationResult:
    sources = _value(record, 'sources', ())
    if not sources:
        sources = []
        if _value(record, 'rule_evidence') or _value(record, 'rule'):
            sources.append('rule')
        if (
            _value(record, 'deepseek_evidence')
            or _value(record, 'llm_evidence')
            or _value(record, 'deepseek')
            or _value(record, 'model_result')
        ):
            sources.append('deepseek')
    normalized = {str(source).lower() for source in sources}
    has_rule = bool(normalized & {'rule', 'rules', 'deterministic'})
    has_deepseek = bool(normalized & {'deepseek', 'llm', 'model'})
    if not (has_rule and has_deepseek):
        return _result(
            'COMBINED_SOURCE_PRESERVATION',
            'COMBINED_SOURCE_MISSING',
            'combined evidence does not contain both rule and DeepSeek sources',
        )
    return _result('COMBINED_SOURCE_PRESERVATION', 'PASS')


def verify_evidence_classification_alignment(rows: Iterable[Any]) -> VerificationResult:
    for row in rows:
        expected = _value(row, 'expected_category', _value(row, 'oracle_category'))
        actual = _value(row, 'category', _value(row, 'classification'))
        if expected is not None and actual is not None and str(expected) != str(actual):
            return _result(
                'EVIDENCE_CLASSIFICATION_ALIGNMENT',
                'EVIDENCE_CLASSIFICATION_MISMATCH',
                'persisted classification differs from the synthetic oracle',
            )
    return _result('EVIDENCE_CLASSIFICATION_ALIGNMENT', 'PASS')


PROTECTED_HUMAN_FIELDS = ('status', 'reviewer_id', 'review_time', 'review_comment')


def verify_protected_snapshot(before: Any, after: Any) -> VerificationResult:
    changed = [
        field for field in PROTECTED_HUMAN_FIELDS
        if _value(before, field) != _value(after, field)
    ]
    if changed:
        return _result(
            'AUTOMATION_HUMAN_FIELD_IMMUTABILITY',
            'AUTOMATION_CHANGED_HUMAN_FIELD',
            'automation changed a protected human-review field',
            changed_fields=changed,
        )
    return _result('AUTOMATION_HUMAN_FIELD_IMMUTABILITY', 'PASS')


def verify_version_chain(versions: Iterable[Any]) -> VerificationResult:
    rows = list(versions)
    ids = [_value(row, 'id', _value(row, 'form_id')) for row in rows]
    unique_ids = {_value(row, 'unique_id', _value(row, 'synthetic_key')) for row in rows}
    if any(value is None for value in ids) or len(ids) != len(set(ids)) or len(unique_ids) > 1:
        return _result(
            'VERSION_HISTORY',
            'VERSION_HISTORY_LOST',
            'version history contains overwritten or cross-linked rows',
        )
    return _result('VERSION_HISTORY', 'PASS', versions=len(rows))


def verify_status_transitions(
    transitions: Iterable[Any],
    *,
    allowed: set[tuple[str, str]] | None = None,
) -> VerificationResult:
    default_allowed = {
        ('pending', 'department_approved'),
        ('pending', 'rejected'),
        ('department_approved', 'center_approved'),
        ('department_approved', 'rejected'),
        ('rejected', 'pending'),
        ('待审核', '部门已审核'),
        ('待审核', '已驳回'),
        ('部门已审核', '中心已审核'),
        ('部门已审核', '已驳回'),
        ('已驳回', '待审核'),
    }
    accepted = allowed or default_allowed
    for transition in transitions:
        if isinstance(transition, Mapping):
            before = transition.get('before', transition.get('from'))
            after = transition.get('after', transition.get('to'))
        elif isinstance(transition, (tuple, list)) and len(transition) == 2:
            before, after = transition
        else:
            return _result(
                'STATUS_TRANSITIONS',
                'STATUS_TRANSITION_INVALID',
                'status transition record is malformed',
            )
        if (str(before), str(after)) not in accepted:
            return _result(
                'STATUS_TRANSITIONS',
                'STATUS_TRANSITION_INVALID',
                'status transition is outside the business route contract',
                before=before,
                after=after,
            )
    return _result('STATUS_TRANSITIONS', 'PASS')


def verify_rejection_return(record: Mapping[str, Any]) -> VerificationResult:
    if 'checked' in record:
        checked = int(record.get('checked', 0) or 0)
        response_form_ids = int(record.get('response_form_ids', 0) or 0)
        lookups = int(record.get('latest_lookup_by_unique_id', 0) or 0)
        if checked < 1 or response_form_ids != 0 or lookups != checked:
            return _result(
                'REJECTION_RETURN',
                'REJECTION_RETURN_MISMATCH',
                'reject responses were not resolved by unique ID for every rejection',
                checked=checked,
                response_form_ids=response_form_ids,
                latest_lookup_by_unique_id=lookups,
            )
        return _result('REJECTION_RETURN', 'PASS', checked=checked)
    response_form_id = record.get('response_form_id', record.get('form_id'))
    detail_unique_id = record.get('detail_unique_id', record.get('unique_id'))
    latest_unique_id = record.get('latest_unique_id', detail_unique_id)
    if response_form_id is not None or detail_unique_id is None or latest_unique_id != detail_unique_id:
        return _result(
            'REJECTION_RETURN',
            'REJECTION_RETURN_MISMATCH',
            'reject response or latest-version lookup violates the unique-ID contract',
        )
    return _result('REJECTION_RETURN', 'PASS')


def verify_statistics_alignment(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> VerificationResult:
    if dict(expected) != dict(actual):
        return _result(
            'STATISTICS_ALIGNMENT',
            'STATISTICS_COUNT_MISMATCH',
            'statistics do not match the independently captured business counts',
            expected=dict(expected),
            actual=dict(actual),
        )
    return _result('STATISTICS_ALIGNMENT', 'PASS')


def verify_batch_closure(batch: Mapping[str, Any] | Any) -> VerificationResult:
    target = int(_value(batch, 'target', _value(batch, 'target_count', 0)) or 0)
    processed = int(_value(batch, 'processed', _value(batch, 'processed_count', 0)) or 0)
    failed = int(_value(batch, 'failed', _value(batch, 'failed_count', 0)) or 0)
    cancelled = int(_value(batch, 'cancelled', _value(batch, 'cancelled_count', 0)) or 0)
    if target != processed + failed + cancelled:
        return _result(
            'BATCH_COUNT_CLOSURE',
            'BATCH_COUNT_MISMATCH',
            'batch target does not close over terminal outcomes',
        )
    return _result('BATCH_COUNT_CLOSURE', 'PASS')


def verify_export(expected_count: int, rows: int | Iterable[Any]) -> VerificationResult:
    actual = int(rows) if isinstance(rows, int) else len(list(rows))
    if actual != int(expected_count):
        return _result(
            'EXPORT_ALIGNMENT',
            'EXPORT_COUNT_MISMATCH',
            'export row count differs from the expected business count',
            expected=int(expected_count),
            actual=actual,
        )
    return _result('EXPORT_ALIGNMENT', 'PASS', expected=int(expected_count), actual=actual)


__all__ = [
    'PROTECTED_HUMAN_FIELDS',
    'REQUIRED_VERIFICATION_CHECKS',
    'VerificationResult',
    'verify_batch_closure',
    'verify_batch_mode_isolation',
    'verify_cache_reuse',
    'verify_combined_sources',
    'verify_evidence_classification_alignment',
    'verify_export',
    'verify_form_counts',
    'verify_http_budget',
    'verify_logical_form_counts',
    'verify_org_counts',
    'verify_pagination',
    'verify_protected_snapshot',
    'verify_rejection_return',
    'verify_scope',
    'verify_statistics_alignment',
    'verify_status_transitions',
    'verify_version_chain',
    'latest_logical_rows',
]
