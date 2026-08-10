"""Pure four-category aggregation for evidence and model suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
    ReviewCategory,
    ScheduleCoverage,
)


@dataclass(frozen=True)
class ClassificationResult:
    category: ReviewCategory
    rationale_keys: tuple[str, ...]
    findings: tuple[FindingDraft, ...] = ()


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _finding(item: Any) -> FindingDraft:
    if isinstance(item, FindingDraft):
        return item
    return FindingDraft(
        rule_key=str(_value(item, 'rule_key', 'unknown')),
        source=FindingSource(_value(item, 'source', FindingSource.SYSTEM.value)),
        severity=FindingSeverity(_value(item, 'severity', FindingSeverity.UNKNOWN.value)),
        title=str(_value(item, 'title', '自动审核发现')),
        message=str(_value(item, 'message', '')),
        objective=bool(_value(item, 'objective', False)),
        evidence_strength=EvidenceStrength(_value(item, 'evidence_strength', EvidenceStrength.WEAK.value)),
        evidence=dict(_value(item, 'evidence', {}) or {}),
    )


def _llm_high(llm_result: Any) -> bool:
    if not llm_result:
        return False
    values = {
        str(_value(llm_result, 'suggested_classification', '')).lower(),
        str(_value(llm_result, 'authenticity', '')).lower(),
        str(_value(llm_result, 'compliance', '')).lower(),
    }
    return bool(values & {
        'high_risk_suspected',
        'high_risk',
        'high-risk',
        'non_compliant',
        'non-compliant',
    })


def _system_finding(rule_key: str, message: str, *, code: str) -> FindingDraft:
    return FindingDraft(
        rule_key=rule_key,
        source=FindingSource.SYSTEM,
        severity=FindingSeverity.UNKNOWN,
        title='自动审核系统状态',
        message=message,
        objective=False,
        evidence_strength=EvidenceStrength.WEAK,
        evidence={'code': code},
    )


def aggregate_classification(
    findings: Iterable[Any] = (),
    *,
    coverage: ScheduleCoverage | str = ScheduleCoverage.COMPLETE,
    llm_result: Any = None,
    llm_available: bool = True,
    critical_parse_failure: bool = False,
) -> ClassificationResult:
    """Apply precedence without database access or final human-review decisions."""
    normalized = list(findings or ())
    result_findings = [_finding(item) for item in normalized]
    try:
        coverage_value = ScheduleCoverage(coverage)
    except (TypeError, ValueError):
        coverage_value = ScheduleCoverage.NONE

    existing_keys = {item.rule_key for item in result_findings}
    if coverage_value in {ScheduleCoverage.NONE, ScheduleCoverage.BASIC} and 'schedule_data_missing' not in existing_keys:
        result_findings.append(_system_finding(
            'schedule_data_missing',
            '课表覆盖不完整，自动审核保留该缺口并继续运行其他规则。',
            code='schedule_data_missing',
        ))
    if not llm_available and 'llm_unavailable' not in existing_keys:
        result_findings.append(_system_finding(
            'llm_unavailable',
            '语义模型当前不可用，分类仅依据确定性证据。',
            code='llm_unavailable',
        ))
    if critical_parse_failure and 'critical_parse_failure' not in existing_keys:
        result_findings.append(_system_finding(
            'critical_parse_failure',
            '关键输入解析失败，当前结果不能视为无明显风险。',
            code='critical_parse_failure',
        ))

    deterministic = [
        item for item in result_findings
        if item.source != FindingSource.SYSTEM and item.source != FindingSource.LLM
    ]
    strong_keys = {
        'personal_schedule_conflict',
        'same_college_teacher',
        'same_listener_same_slot',
    }
    strong_objective = any(
        item.severity == FindingSeverity.HIGH
        and item.objective
        and item.evidence_strength == EvidenceStrength.EXACT
        and (item.rule_key in strong_keys or item.source in {FindingSource.SCHEDULE, FindingSource.HISTORY})
        for item in deterministic
    )
    objective_review = any(
        item.objective
        and item.severity in {FindingSeverity.REVIEW, FindingSeverity.HIGH}
        for item in deterministic
    )
    review_finding = any(
        item.severity in {FindingSeverity.REVIEW, FindingSeverity.HIGH}
        for item in deterministic
    )
    llm_high = _llm_high(llm_result)

    if strong_objective or (llm_high and objective_review):
        category = ReviewCategory.HIGH_RISK
    elif review_finding or llm_high:
        category = ReviewCategory.REVIEW
    elif critical_parse_failure or not llm_available or coverage_value != ScheduleCoverage.COMPLETE:
        category = ReviewCategory.UNKNOWN
    else:
        category = ReviewCategory.CLEAR

    rationale: list[str] = []
    for item in result_findings:
        if item.source != FindingSource.SYSTEM:
            rationale.append(item.rule_key)
    if llm_high:
        rationale.append('llm_high_risk_suggestion')
    if not llm_available:
        rationale.append('llm_unavailable')
    if coverage_value != ScheduleCoverage.COMPLETE:
        rationale.append('schedule_data_missing')
    if critical_parse_failure:
        rationale.append('critical_parse_failure')
    rationale = list(dict.fromkeys(rationale))
    return ClassificationResult(
        category=category,
        rationale_keys=tuple(rationale),
        findings=tuple(result_findings),
    )


classify_findings = aggregate_classification
aggregate_findings = aggregate_classification


__all__ = [
    'ClassificationResult',
    'aggregate_classification',
    'aggregate_findings',
    'classify_findings',
]
