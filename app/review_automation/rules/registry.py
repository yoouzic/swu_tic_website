"""Closed-world, versioned rule dispatch for automated review evidence."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
)


class RuleValidationError(ValueError):
    """A rule revision is not safe or does not match its fixed handler schema."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RuleContext:
    """Small, serializable input boundary shared by rule handlers."""

    form: Any
    schedule: Any = None
    history: tuple[Any, ...] = ()
    school_matches: tuple[Any, ...] = ()
    semester: str | None = None
    semester_monday: date | None = None
    listener_college: str | None = None
    college_aliases: Mapping[str, str] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleRevisionView:
    rule_key: str
    version: int
    handler: str
    enabled: bool = True
    severity: str = FindingSeverity.REVIEW.value
    parameters: Mapping[str, Any] = field(default_factory=dict)


def field_value(value: Any, *names: str, default: Any = None) -> Any:
    """Read a field from a mapping or an ORM-like object without mutating it."""
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _as_revision(revision: Any) -> RuleRevisionView:
    if isinstance(revision, RuleRevisionView):
        return revision
    parameters = field_value(revision, 'parameters', 'parameters_json', default={})
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters)
        except json.JSONDecodeError as exc:
            raise RuleValidationError('invalid_parameters_json', 'rule parameters must be JSON') from exc
    if parameters is None:
        parameters = {}
    return RuleRevisionView(
        rule_key=str(field_value(revision, 'rule_key', default='')).strip(),
        version=int(field_value(revision, 'version', default=0)),
        handler=str(field_value(revision, 'handler', default='')).strip(),
        enabled=bool(field_value(revision, 'enabled', default=True)),
        severity=str(field_value(revision, 'severity', default=FindingSeverity.REVIEW.value)),
        parameters=parameters,
    )


def _validate_safe_pattern(pattern: Any) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise RuleValidationError('invalid_regex', 'regex must be a non-empty string')
    if len(pattern) > 200:
        raise RuleValidationError('regex_too_long', 'regex exceeds the 200-character safety limit')
    if '(?' in pattern or re.search(r'\\[1-9]', pattern):
        raise RuleValidationError('unsafe_regex_construct', 'lookaround, inline, and backreference constructs are not allowed')
    if re.search(r'\([^()]{0,100}[+*][^()]{0,100}\)[+*?]', pattern):
        raise RuleValidationError('unsafe_regex_construct', 'nested quantifiers are not allowed')
    try:
        re.compile(pattern)
    except re.error as exc:
        raise RuleValidationError('invalid_regex', 'regex could not be compiled safely') from exc
    return pattern


def _require_mapping(parameters: Any) -> dict[str, Any]:
    if not isinstance(parameters, Mapping):
        raise RuleValidationError('invalid_parameters', 'rule parameters must be a JSON object')
    return dict(parameters)


def _validate_required_prefix(parameters: Any) -> dict[str, Any]:
    values = _require_mapping(parameters)
    prefix = values.get('required_prefix')
    if not isinstance(prefix, str) or not prefix.strip() or len(prefix) > 50:
        raise RuleValidationError('invalid_required_prefix', 'required_prefix must be a short non-empty string')
    return {'required_prefix': prefix}


def _validate_minimum_length(parameters: Any) -> dict[str, Any]:
    values = _require_mapping(parameters)
    minimum = values.get('minimum_characters')
    if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= 10000:
        raise RuleValidationError('invalid_minimum_length', 'minimum_characters must be an integer from 1 to 10000')
    return {'minimum_characters': minimum}


def _validate_patterns(parameters: Any) -> dict[str, Any]:
    values = _require_mapping(parameters)
    patterns = values.get('patterns')
    if not isinstance(patterns, list) or not patterns or len(patterns) > 20:
        raise RuleValidationError('invalid_patterns', 'patterns must contain 1 to 20 entries')
    normalized = []
    for item in patterns:
        if not isinstance(item, Mapping):
            raise RuleValidationError('invalid_pattern_entry', 'each pattern must be a JSON object')
        regex = _validate_safe_pattern(item.get('regex'))
        message = item.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise RuleValidationError('invalid_pattern_message', 'pattern message must be a short non-empty string')
        normalized.append({'regex': regex, 'message': message})
    return {'patterns': normalized}


def _validate_safe_regex(parameters: Any) -> dict[str, Any]:
    values = _require_mapping(parameters)
    phrase = values.get('phrase')
    regex = values.get('regex')
    if bool(phrase) == bool(regex):
        raise RuleValidationError('invalid_safe_regex', 'provide exactly one of phrase or regex')
    if phrase is not None:
        if not isinstance(phrase, str) or not phrase or len(phrase) > 200:
            raise RuleValidationError('invalid_phrase', 'phrase must be a short non-empty string')
        matcher = {'phrase': phrase}
    else:
        matcher = {'regex': _validate_safe_pattern(regex)}
    message = values.get('message')
    if not isinstance(message, str) or not message.strip() or len(message) > 500:
        raise RuleValidationError('invalid_pattern_message', 'message must be a short non-empty string')
    matcher['message'] = message
    return matcher


def _strict_object(parameters: Any, allowed: set[str]) -> dict[str, Any]:
    values = _require_mapping(parameters)
    unknown = [key for key in values if key not in allowed]
    if unknown:
        raise RuleValidationError(
            'unknown_parameter',
            f'unknown rule parameter(s): {", ".join(str(key) for key in unknown)}',
        )
    return values


def _validate_empty_object(parameters: Any) -> dict[str, Any]:
    return _strict_object(parameters, set())


def _validate_witness_reuse(parameters: Any) -> dict[str, Any]:
    values = _strict_object(
        parameters,
        {'minimum_distinct_weeks', 'high_risk_candidate_weeks', 'identity'},
    )
    minimum = values.get('minimum_distinct_weeks', 2)
    candidate_threshold = values.get('high_risk_candidate_weeks', 3)
    identity = values.get('identity', 'phone_primary')
    if type(minimum) is not int or minimum < 2:
        raise RuleValidationError(
            'invalid_minimum_distinct_weeks',
            'minimum_distinct_weeks must be an integer greater than or equal to 2',
        )
    if type(candidate_threshold) is not int or candidate_threshold < minimum:
        raise RuleValidationError(
            'invalid_high_risk_candidate_weeks',
            'high_risk_candidate_weeks must be an integer at least minimum_distinct_weeks',
        )
    if identity != 'phone_primary':
        raise RuleValidationError(
            'invalid_witness_identity',
            'identity must be phone_primary',
        )
    return {
        'minimum_distinct_weeks': minimum,
        'high_risk_candidate_weeks': candidate_threshold,
        'identity': identity,
    }


def _validate_consecutive_teacher_weeks(parameters: Any) -> dict[str, Any]:
    values = _strict_object(parameters, {'maximum_week_gap'})
    maximum_gap = values.get('maximum_week_gap', 1)
    if type(maximum_gap) is not int or maximum_gap != 1:
        raise RuleValidationError(
            'invalid_maximum_week_gap',
            'maximum_week_gap must be exactly 1',
        )
    return {'maximum_week_gap': maximum_gap}


def _validate_feedback_similarity(parameters: Any) -> dict[str, Any]:
    values = _strict_object(parameters, {'similarity_threshold'})
    threshold = values.get('similarity_threshold', 0.9)
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(float(threshold))
        or not 0 <= threshold <= 1
    ):
        raise RuleValidationError(
            'invalid_similarity_threshold',
            'similarity_threshold must be a finite number from 0 to 1',
        )
    return {'similarity_threshold': threshold}


_PARAMETER_VALIDATORS = {
    'required_prefix': _validate_required_prefix,
    'minimum_length': _validate_minimum_length,
    'confusion_patterns': _validate_patterns,
    'safe_regex': _validate_safe_regex,
    'personal_schedule_conflict': _validate_empty_object,
    'class_schedule_conflict': _validate_empty_object,
    'same_college_teacher': _validate_empty_object,
    'school_schedule_mismatch': _validate_empty_object,
    'witness_reused_across_weeks': _validate_witness_reuse,
    'witness_phone_name_conflict': _validate_empty_object,
    'consecutive_teacher_weeks': _validate_consecutive_teacher_weeks,
    'same_listener_same_slot': _validate_empty_object,
    'feedback_similarity': _validate_feedback_similarity,
}


def _known_handler_names() -> set[str]:
    return set(HANDLERS)


def validate_rule_parameters(handler: str, parameters: Any) -> dict[str, Any]:
    """Validate JSON parameters for a handler selected from the fixed map."""
    if handler not in _known_handler_names():
        raise RuleValidationError('unknown_handler', f'unknown rule handler: {handler}')
    validator = _PARAMETER_VALIDATORS.get(handler)
    if validator is not None:
        return validator(parameters)
    if not isinstance(parameters, Mapping):
        raise RuleValidationError('invalid_parameters', 'rule parameters must be a JSON object')
    return dict(parameters)


def validate_rule_revision(revision: Any) -> RuleRevisionView:
    view = _as_revision(revision)
    if not view.rule_key:
        raise RuleValidationError('missing_rule_key', 'rule_key is required')
    if view.version < 1:
        raise RuleValidationError('invalid_version', 'rule version must be positive')
    if view.severity not in {item.value for item in FindingSeverity}:
        raise RuleValidationError('invalid_severity', 'rule severity is not supported')
    return RuleRevisionView(
        rule_key=view.rule_key,
        version=view.version,
        handler=view.handler,
        enabled=view.enabled,
        severity=view.severity,
        parameters=validate_rule_parameters(view.handler, view.parameters),
    )


def newest_revisions(revisions: Iterable[Any]) -> tuple[RuleRevisionView, ...]:
    """Select exactly one, highest-version revision per stable rule key."""
    latest: dict[str, RuleRevisionView] = {}
    for raw_revision in revisions:
        view = _as_revision(raw_revision)
        if not view.rule_key:
            raise RuleValidationError('missing_rule_key', 'rule_key is required')
        current = latest.get(view.rule_key)
        if current is None or view.version > current.version:
            latest[view.rule_key] = view
    return tuple(latest[key] for key in sorted(latest))


def _system_finding(revision: RuleRevisionView, code: str, message: str) -> FindingDraft:
    return FindingDraft(
        rule_key=revision.rule_key,
        source=FindingSource.SYSTEM,
        severity=FindingSeverity.UNKNOWN,
        title='自动规则不可用',
        message=message,
        objective=False,
        evidence_strength=EvidenceStrength.WEAK,
        evidence={'code': code, 'handler': revision.handler, 'version': revision.version},
    )


class RuleEngine:
    """Execute only the newest enabled revisions from the fixed handler map."""

    def __init__(self, revisions: Iterable[Any]):
        self.revisions = newest_revisions(revisions)
        for revision in self.revisions:
            if revision.handler in HANDLERS:
                validate_rule_revision(revision)

    def evaluate(self, form: Any = None, *, context: RuleContext | None = None) -> tuple[FindingDraft, ...]:
        if context is None:
            if isinstance(form, RuleContext):
                context = form
            else:
                context = RuleContext(form=form)
        findings: list[FindingDraft] = []
        for revision in self.revisions:
            if not revision.enabled:
                continue
            handler = HANDLERS.get(revision.handler)
            if handler is None:
                findings.append(_system_finding(
                    revision,
                    'unknown_handler',
                    f'规则 {revision.rule_key} 使用了未注册处理器，已安全跳过。',
                ))
                continue
            try:
                findings.extend(handler.evaluate(context, revision))
            except RuleValidationError as exc:
                findings.append(_system_finding(revision, exc.code, '规则参数未通过安全校验，已安全跳过。'))
        return tuple(findings)


RuleRegistry = RuleEngine


from .text import (  # noqa: E402  (fixed handler map is built after contracts above)
    ConfusionPatternRule,
    MinimumLengthRule,
    RequiredPrefixRule,
    SafeRegexRule,
)


class _FixedModuleHandler:
    """Lazy adapter for a handler implemented in an approved rules module."""

    def __init__(self, module_name: str):
        self.module_name = module_name

    def evaluate(self, context: RuleContext, revision: RuleRevisionView):
        if self.module_name == 'schedule':
            from .schedule import evaluate_rule_revision
        else:
            from .history import evaluate_rule_revision
        return evaluate_rule_revision(context, revision)


HANDLERS = {
    'required_prefix': RequiredPrefixRule,
    'minimum_length': MinimumLengthRule,
    'confusion_patterns': ConfusionPatternRule,
    'safe_regex': SafeRegexRule,
}

# This is deliberately a literal closed-world registry: no administrator input
# can add an import path, callable, Python source, or eval/exec expression.
for _handler_name in {
    'personal_schedule_conflict',
    'class_schedule_conflict',
    'same_college_teacher',
    'school_schedule_mismatch',
}:
    HANDLERS[_handler_name] = _FixedModuleHandler('schedule')
for _handler_name in {
    'witness_reused_across_weeks',
    'witness_phone_name_conflict',
    'consecutive_teacher_weeks',
    'same_listener_same_slot',
    'feedback_similarity',
}:
    HANDLERS[_handler_name] = _FixedModuleHandler('history')


def execute_rules(revisions: Iterable[Any], form: Any = None, *, context: RuleContext | None = None):
    return RuleEngine(revisions).evaluate(form, context=context)


__all__ = [
    'HANDLERS',
    'RuleContext',
    'RuleEngine',
    'RuleRegistry',
    'RuleRevisionView',
    'RuleValidationError',
    'execute_rules',
    'field_value',
    'newest_revisions',
    'validate_rule_parameters',
    'validate_rule_revision',
]
