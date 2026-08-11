"""Sanitized business-acceptance report rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


SENSITIVE_KEY_MARKERS = (
    'key',
    'token',
    'authorization',
    'password',
    'phone',
    'student_id',
    'prompt',
    'response',
    'reasoning',
)
CONCLUSIONS = {'PASS', 'FAIL', 'BLOCKED'}


def redact(value: Any, key: str = '') -> Any:
    lowered = str(key).lower()
    if any(marker in lowered for marker in SENSITIVE_KEY_MARKERS):
        return '[REDACTED]'
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, key) for item in value]
    return value


def sanitized_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return dict(redact(payload))


def _conclusion(payload: Mapping[str, Any]) -> str:
    value = str(payload.get('conclusion', 'BLOCKED')).upper()
    return value if value in CONCLUSIONS else 'BLOCKED'


def render_report(payload: Mapping[str, Any]) -> str:
    safe = sanitized_payload(payload)
    safe['conclusion'] = _conclusion(payload)
    lines = [
        '# SWU TIC Business Flow Acceptance Report',
        '',
        f"Conclusion: {safe['conclusion']}",
        '',
    ]
    section_titles = {
        'scope': 'Scope',
        'environment': 'Environment',
        'dataset': 'Dataset',
        'batches': 'Three Batches',
        'concurrency': 'Concurrency Stages',
        'deepseek_summary': 'DeepSeek Summary',
        'administrator_scopes': 'Administrator Scopes',
        'browser_evidence': 'Browser Evidence',
        'resubmission_version_chains': 'Resubmission and Version Chains',
        'performance': 'Performance',
        'export': 'Export',
        'statistics': 'Statistics',
        'verification': 'Verification Matrix',
        'defects': 'Defects',
    }
    for key, title in section_titles.items():
        if key not in safe:
            continue
        lines.extend((f'## {title}', '', '```json'))
        lines.extend((json.dumps(safe[key], ensure_ascii=False, indent=2, sort_keys=True), '```', ''))
    lines.extend((
        '## Complete Sanitized Payload',
        '',
        '```json',
        json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True),
        '```',
        '',
    ))
    return '\n'.join(lines)


def write_report(path: str | Path, payload: Mapping[str, Any]) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_report(payload), encoding='utf-8', newline='\n')
    return destination


__all__ = ['CONCLUSIONS', 'redact', 'render_report', 'sanitized_payload', 'write_report']
