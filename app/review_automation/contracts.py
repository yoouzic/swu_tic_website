"""Typed, serializable contracts shared by automation workers and services."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReviewCategory(str, Enum):
    CLEAR = '无明显风险'
    REVIEW = '建议复核'
    HIGH_RISK = '高风险疑似假表'
    UNKNOWN = '系统无法判断'


class FindingSeverity(str, Enum):
    INFO = 'info'
    REVIEW = 'review'
    HIGH = 'high'
    UNKNOWN = 'unknown'


class FindingSource(str, Enum):
    RULE = 'rule'
    SCHEDULE = 'schedule'
    HISTORY = 'history'
    LLM = 'llm'
    SYSTEM = 'system'


class EvidenceStrength(str, Enum):
    WEAK = 'weak'
    APPROXIMATE = 'approximate'
    EXACT = 'exact'


class ScheduleCoverage(str, Enum):
    NONE = 'none'
    BASIC = 'basic'
    COMPLETE = 'complete'


class BatchStatus(str, Enum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETED = 'completed'
    COMPLETED_WITH_ERRORS = 'completed_with_errors'
    CANCEL_REQUESTED = 'cancel_requested'
    CANCELLED = 'cancelled'
    FAILED = 'failed'


class DatasetStatus(str, Enum):
    STAGED = 'staged'
    ACTIVE = 'active'
    RETIRED = 'retired'
    FAILED = 'failed'


@dataclass(frozen=True)
class FindingDraft:
    rule_key: str
    source: FindingSource
    severity: FindingSeverity
    title: str
    message: str
    objective: bool
    evidence_strength: EvidenceStrength
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedForm:
    form_id: int | str | None = None
    form_version: int | str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssessmentDraft:
    classification: ReviewCategory
    coverage: ScheduleCoverage = ScheduleCoverage.NONE
    findings: tuple[FindingDraft, ...] = ()
    model_result: dict[str, Any] = field(default_factory=dict)
    suggested_comment: str = ''
    error_code: str | None = None
    error_message: str | None = None


__all__ = [
    'AssessmentDraft',
    'BatchStatus',
    'DatasetStatus',
    'EvidenceStrength',
    'FindingDraft',
    'FindingSeverity',
    'FindingSource',
    'NormalizedForm',
    'ReviewCategory',
    'ScheduleCoverage',
]
