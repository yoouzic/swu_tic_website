"""Canonical, dependency-aware fingerprints for immutable assessments."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping


_PROTECTED_FORM_FIELDS = {
    'status',
    'reviewer_id',
    'review_time',
    'review_comment',
}


def _object_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if hasattr(value, '__dict__'):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith('_') and key != '_sa_instance_state'
        }
    return None


def _canonical(value: Any, *, form_payload: bool = False) -> Any:
    if isinstance(value, Enum):
        return _canonical(value.value, form_payload=form_payload)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item, form_payload=form_payload)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not (form_payload and str(key) in _PROTECTED_FORM_FIELDS)
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item, form_payload=form_payload) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_canonical(item, form_payload=form_payload) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    mapping = _object_mapping(value)
    if mapping is not None:
        return _canonical(mapping, form_payload=form_payload)
    return str(value)


def canonical_json(value: Any) -> str:
    """Serialize JSON-safe values with stable key and collection ordering."""
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    )


def _form_payload(form: Any) -> Any:
    return _canonical(form, form_payload=True)


def build_assessment_fingerprint(
    *,
    form: Any,
    form_version: Any = None,
    schedule_dependencies: Any = None,
    rule_revisions: Any = None,
    prompt_version: str | None = None,
    model_name: str | None = None,
    force_nonce: str | None = None,
    dependencies: Any = None,
    model_id: str | None = None,
) -> str:
    """Hash form content and every versioned dependency that affects review."""
    if form_version is None:
        mapping = _object_mapping(form) or {}
        form_version = mapping.get('form_version', mapping.get('version', mapping.get('updated_at')))
    if schedule_dependencies is None:
        schedule_dependencies = dependencies if dependencies is not None else {}
    if model_name is None:
        model_name = model_id
    payload = {
        'form': _form_payload(form),
        'form_version': _canonical(form_version),
        'schedule_dependencies': _canonical(schedule_dependencies),
        'rule_revisions': _canonical(rule_revisions or {}),
        'prompt_version': prompt_version or '',
        'model_name': model_name or '',
    }
    if force_nonce is not None:
        payload['force_nonce'] = str(force_nonce)
    serialized = canonical_json(payload).encode('utf-8')
    return hashlib.sha256(serialized).hexdigest()


fingerprint_form = build_assessment_fingerprint
assessment_fingerprint = build_assessment_fingerprint


__all__ = [
    'assessment_fingerprint',
    'build_assessment_fingerprint',
    'canonical_json',
    'fingerprint_form',
]
