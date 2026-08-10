"""Immutable one-form assessment orchestration."""

from __future__ import annotations

import dataclasses
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Mapping

from app.models import LectureForm, db

from .classification import aggregate_classification
from .contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
    NormalizedForm,
    ScheduleCoverage,
)
from .fingerprints import build_assessment_fingerprint
from .llm.client import (
    InvalidLLMResponse,
    PermanentLLMError,
    TransientLLMError,
)
from .llm.prompts import PROMPT_VERSION
from .llm.schemas import DeepSeekReviewResponse
from .models import ReviewAssessment, ReviewFinding, ReviewRuleRevision
from .rules.registry import RuleContext, RuleRevisionView, execute_rules, field_value


@dataclass(frozen=True)
class AssessmentSummary:
    assessment_id: str
    form_id: int
    fingerprint: str
    category: str
    coverage: str
    cache_hit: bool = False
    finding_count: int = 0
    error_code: str | None = None
    suggested_comment: str = ''

    @property
    def classification(self):
        return self.category

    def as_dict(self):
        return dataclasses.asdict(self)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value


def _form_payload(form: Any) -> dict[str, Any]:
    if isinstance(form, Mapping):
        return _json_safe(dict(form))
    table = getattr(form, '__table__', None)
    if table is not None:
        return _json_safe({column.name: getattr(form, column.name) for column in table.columns})
    if hasattr(form, '__dict__'):
        return _json_safe({
            key: item
            for key, item in vars(form).items()
            if not key.startswith('_') and key != '_sa_instance_state'
        })
    return {'value': _json_safe(form)}


def _revision_snapshot(revisions: Any) -> dict[str, Any]:
    if revisions is None:
        return {}
    if isinstance(revisions, Mapping):
        result = {}
        for key, value in revisions.items():
            if isinstance(value, Mapping):
                result[str(key)] = _json_safe(dict(value))
            else:
                result[str(key)] = _json_safe(value)
        return dict(sorted(result.items()))
    result = {}
    for revision in revisions:
        key = field_value(revision, 'rule_key', default='')
        version = field_value(revision, 'version', default=0)
        if key:
            parameters = field_value(revision, 'parameters_json', default='{}')
            if isinstance(parameters, str):
                try:
                    parameters = json.loads(parameters)
                except (TypeError, ValueError, json.JSONDecodeError):
                    parameters = {}
            result[str(key)] = {
                'id': field_value(revision, 'id', default=None),
                'version': int(version),
                'handler': field_value(revision, 'handler', default=''),
                'enabled': bool(field_value(revision, 'enabled', default=True)),
                'severity': field_value(revision, 'severity', default=FindingSeverity.REVIEW.value),
                'parameters': dict(parameters or {}) if isinstance(parameters, Mapping) else {},
            }
    return dict(sorted(result.items()))


def _rule_views(revisions: Any) -> tuple[RuleRevisionView, ...]:
    if revisions is None:
        return tuple(ReviewRuleRevision.query.all())
    if isinstance(revisions, Mapping):
        values = []
        for key, value in revisions.items():
            if not isinstance(value, Mapping):
                continue
            values.append(RuleRevisionView(
                rule_key=str(key),
                version=int(value.get('version', 1)),
                handler=str(value.get('handler', key)),
                enabled=bool(value.get('enabled', True)),
                severity=str(value.get('severity', FindingSeverity.REVIEW.value)),
                parameters=dict(value.get('parameters', {})),
            ))
        return tuple(values)
    return tuple(revisions)


def _as_finding(value: Any) -> FindingDraft:
    if isinstance(value, FindingDraft):
        return value
    source = value.get('source', FindingSource.RULE.value) if isinstance(value, Mapping) else getattr(value, 'source', FindingSource.RULE.value)
    severity = value.get('severity', FindingSeverity.REVIEW.value) if isinstance(value, Mapping) else getattr(value, 'severity', FindingSeverity.REVIEW.value)
    strength = value.get('evidence_strength', EvidenceStrength.WEAK.value) if isinstance(value, Mapping) else getattr(value, 'evidence_strength', EvidenceStrength.WEAK.value)
    get = value.get if isinstance(value, Mapping) else lambda name, default=None: getattr(value, name, default)
    return FindingDraft(
        rule_key=str(get('rule_key', 'unknown')),
        source=FindingSource(getattr(source, 'value', source)),
        severity=FindingSeverity(getattr(severity, 'value', severity)),
        title=str(get('title', '自动审核发现')),
        message=str(get('message', '')),
        objective=bool(get('objective', False)),
        evidence_strength=EvidenceStrength(getattr(strength, 'value', strength)),
        evidence=_json_safe(dict(get('evidence', {}) or {})),
    )


_LLM_HISTORY_LIMIT = 3


def _slot_context(slot: Any) -> dict[str, Any]:
    weeks = field_value(slot, 'weeks', default=()) or ()
    try:
        weeks = sorted(int(item) for item in weeks)
    except (TypeError, ValueError):
        weeks = []
    return {
        'course_title': _json_safe(field_value(slot, 'course_title', default='')),
        'weeks': weeks,
        'weekday': _json_safe(field_value(slot, 'weekday', default=None)),
        'start_period': _json_safe(field_value(slot, 'start_period', default=None)),
        'end_period': _json_safe(field_value(slot, 'end_period', default=None)),
        'evidence_strength': _json_safe(field_value(slot, 'evidence_strength', default=EvidenceStrength.WEAK)),
    }


def _school_match_context(match: Any) -> dict[str, Any]:
    entry = field_value(match, 'entry', default=None)
    return {
        'entry_id': _json_safe(field_value(entry, 'id', default=None)),
        'course_title': _json_safe(field_value(entry, 'course_title', default='')),
        'teacher_name': _json_safe(field_value(entry, 'teacher_name', default='')),
        'teacher_college': _json_safe(field_value(entry, 'teacher_college', default='')),
        'location': _json_safe(field_value(entry, 'location', default=None)),
        'confidence': _json_safe(field_value(match, 'confidence', default=0.0)),
        'matched_fields': list(field_value(match, 'matched_fields', default=()) or ()),
        'mismatched_fields': list(field_value(match, 'mismatched_fields', default=()) or ()),
    }


def _finding_context(finding: Any) -> dict[str, Any]:
    item = _as_finding(finding)
    return {
        'rule_key': item.rule_key,
        'source': item.source.value,
        'severity': item.severity.value,
        'evidence_strength': item.evidence_strength.value,
        'objective': bool(item.objective),
        'evidence': _json_safe(item.evidence),
    }


def _history_item_context(item: Any) -> dict[str, Any]:
    feedback = field_value(item, 'course_feedback', default='') or ''
    return {
        'id': _json_safe(field_value(item, 'id', default=None)),
        'unique_id': _json_safe(field_value(item, 'unique_id', default=None)),
        'listener_number': _json_safe(field_value(item, 'listener_number', default=None)),
        'lecture_date': _json_safe(field_value(item, 'lecture_date', default=None)),
        'class_period': _json_safe(field_value(item, 'class_period', default=None)),
        'teacher_name': _json_safe(field_value(item, 'teacher_name', default=None)),
        'course_title': _json_safe(field_value(item, 'course_title', default=None)),
        'course_feedback_excerpt': str(feedback)[:160],
    }


def _review_context_payload(
    *,
    form: Any,
    normalized: NormalizedForm,
    schedule: Any,
    school_matches: Any,
    findings: Any,
    history: Any,
    schedule_dependencies: Any,
    rule_snapshot: Any,
    semester: str | None,
    semester_monday: date | None,
    model_id: str,
    prompt_version: str,
) -> dict[str, Any]:
    listener_number = field_value(form, 'listener_number', default=None)
    related_history = [
        item for item in (history or ())
        if field_value(item, 'listener_number', default=None) == listener_number
    ]
    coverage = field_value(schedule, 'coverage', default=ScheduleCoverage.NONE)
    return {
        'form': _json_safe(normalized.fields),
        'schedule_comparison': {
            'coverage': _json_safe(coverage),
            'admin_classes': list(field_value(schedule, 'admin_classes', default=()) or ()),
            'slots': [_slot_context(item) for item in (field_value(schedule, 'slots', default=()) or ())],
            'school_matches': [_school_match_context(item) for item in (school_matches or ())],
        },
        'rule_evidence': [_finding_context(item) for item in (findings or ())],
        'history_summary': {
            'related_count': len(related_history),
            'items': [_history_item_context(item) for item in related_history[:_LLM_HISTORY_LIMIT]],
        },
        'dependency_context': {
            'semester': semester,
            'semester_monday': _json_safe(semester_monday),
            'datasets': _json_safe(schedule_dependencies or {}),
            'rules': _json_safe(rule_snapshot or {}),
            'model_id': model_id,
            'prompt_version': prompt_version,
        },
    }


def _model_dump(result: Any) -> dict[str, Any]:
    if isinstance(result, DeepSeekReviewResponse):
        return result.model_dump(mode='json')
    if hasattr(result, 'model_dump'):
        return result.model_dump(mode='json')
    if isinstance(result, Mapping):
        return DeepSeekReviewResponse.model_validate(result).model_dump(mode='json')
    raise InvalidLLMResponse('schema_validation')


def _error_finding(code: str) -> FindingDraft:
    return FindingDraft(
        rule_key='llm_error',
        source=FindingSource.SYSTEM,
        severity=FindingSeverity.UNKNOWN,
        title='语义模型不可用',
        message='DeepSeek 语义复核未完成，已保留确定性规则结果并建议人工复核。',
        objective=False,
        evidence_strength=EvidenceStrength.WEAK,
        evidence={'code': code},
    )


def _unavailable_finding() -> FindingDraft:
    return FindingDraft(
        rule_key='llm_unavailable',
        source=FindingSource.SYSTEM,
        severity=FindingSeverity.UNKNOWN,
        title='LLM unavailable',
        message='Semantic model checks are unavailable; deterministic evidence is retained.',
        objective=False,
        evidence_strength=EvidenceStrength.WEAK,
        evidence={'code': 'llm_unavailable'},
    )


class AssessmentService:
    """Run deterministic evidence and optional LLM review without human-state writes."""

    def __init__(
        self,
        *,
        llm_client: Any = None,
        llm_enabled: bool | None = None,
        deterministic_runner: Callable[..., Any] | None = None,
        schedule_loader: Callable[[Any], Any] | None = None,
        history_loader: Callable[[Any], Any] | None = None,
        school_matches_loader: Callable[[Any], Any] | None = None,
        listener_profile_loader: Callable[[Any], Any] | None = None,
        schedule_dependencies: Any = None,
        rule_revisions: Any = None,
        semester: str | None = None,
        semester_monday: date | datetime | None = None,
        model_id: str | None = None,
        prompt_version: str | None = None,
    ):
        self.llm_client = llm_client
        self.llm_enabled = bool(llm_client) if llm_enabled is None else bool(llm_enabled)
        self.deterministic_runner = deterministic_runner
        self.schedule_loader = schedule_loader
        self.history_loader = history_loader
        self.school_matches_loader = school_matches_loader
        self.listener_profile_loader = listener_profile_loader
        self.schedule_dependencies = schedule_dependencies if schedule_dependencies is not None else {}
        self.rule_revisions = rule_revisions
        self.semester = str(semester) if semester is not None else None
        if isinstance(semester_monday, datetime):
            self.semester_monday = semester_monday.date()
        elif isinstance(semester_monday, date):
            self.semester_monday = semester_monday
        elif isinstance(semester_monday, str):
            try:
                self.semester_monday = date.fromisoformat(semester_monday)
            except ValueError:
                self.semester_monday = None
        else:
            self.semester_monday = None
        self.model_id = model_id or getattr(llm_client, 'model', 'deepseek-v4-flash')
        self.prompt_version = prompt_version or getattr(llm_client, 'prompt_version', PROMPT_VERSION)

    def normalize_form(self, form: Any) -> NormalizedForm:
        payload = _form_payload(form)
        version = field_value(form, 'updated_at', 'version', default=None)
        if version is None:
            version = field_value(form, 'id', default=None)
        return NormalizedForm(
            form_id=field_value(form, 'id'),
            form_version=_json_safe(version),
            fields=payload,
        )

    def assess(self, form_id: int, **kwargs) -> AssessmentSummary:
        return self.assess_form(form_id, **kwargs)

    def assess_form(
        self,
        form_id: int,
        *,
        batch_id: str | None = None,
        force_refresh: bool = False,
        llm_enabled: bool | None = None,
    ) -> AssessmentSummary:
        form = self._load_latest_form(form_id)
        normalized = self.normalize_form(form)
        schedule = self._load_schedule(form)
        schedule_dependencies = self._load_schedule_dependencies(form, schedule)
        rule_source = self.rule_revisions if self.rule_revisions is not None else ReviewRuleRevision.query.all()
        rule_snapshot = _revision_snapshot(rule_source)
        force_nonce = str(uuid.uuid4()) if force_refresh else None
        fingerprint = build_assessment_fingerprint(
            form=normalized.fields,
            form_version=normalized.form_version,
            schedule_dependencies=schedule_dependencies,
            rule_revisions=rule_snapshot,
            prompt_version=self.prompt_version,
            model_name=self.model_id,
            force_nonce=force_nonce,
        )
        if not force_refresh:
            cached = ReviewAssessment.query.filter_by(fingerprint=fingerprint).first()
            if cached is not None:
                return self._summary(cached, cache_hit=True)

        history = tuple(self._load_history(form) or ())
        school_matches = tuple(self._load_school_matches(form))
        profile = self._load_listener_profile(form)
        context = RuleContext(
            form=form,
            schedule=schedule,
            history=history,
            school_matches=school_matches,
            semester=self.semester,
            semester_monday=self.semester_monday,
            listener_college=field_value(profile, 'college', default=None),
            options={'normalized_form': normalized.fields},
        )
        deterministic = self._run_deterministic(form, context)
        coverage = self._coverage(schedule)
        enabled = self.llm_enabled if llm_enabled is None else bool(llm_enabled)
        model_result = None
        suggested_comment = ''
        error_code = None
        llm_available = bool(enabled and self.llm_client is not None)
        if not enabled or self.llm_client is None:
            error_code = 'llm_unavailable'
            deterministic = tuple(deterministic) + (_unavailable_finding(),)
        else:
            review_payload = _review_context_payload(
                form=form,
                normalized=normalized,
                schedule=schedule,
                school_matches=school_matches,
                findings=deterministic,
                history=history,
                schedule_dependencies=schedule_dependencies,
                rule_snapshot=rule_snapshot,
                semester=self.semester,
                semester_monday=self.semester_monday,
                model_id=self.model_id,
                prompt_version=self.prompt_version,
            )
            try:
                model_result = _model_dump(self.llm_client.review(review_payload))
                suggested_comment = str(model_result.get('suggested_comment', ''))
                deterministic = tuple(deterministic) + self._llm_findings(model_result)
            except (TransientLLMError, PermanentLLMError, InvalidLLMResponse) as exc:
                error_code = exc.code
                llm_available = False
                deterministic = tuple(deterministic) + (_error_finding(error_code),)
            except Exception:
                error_code = 'client_error'
                llm_available = False
                deterministic = tuple(deterministic) + (_error_finding(error_code),)

        classification = aggregate_classification(
            deterministic,
            coverage=coverage,
            llm_result=model_result,
            llm_available=llm_available,
        )
        dependency_ids, dependency_versions = self._dependency_snapshot(schedule_dependencies, rule_snapshot)
        assessment = ReviewAssessment(
            form_id=form.id,
            form_version=str(normalized.form_version or ''),
            batch_id=batch_id,
            classification=classification.category.value,
            coverage=coverage.value,
            fingerprint=fingerprint,
            dependency_ids_json=json.dumps(dependency_ids, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            dependency_versions_json=json.dumps(dependency_versions, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            model_id=self.model_id if enabled else None,
            prompt_version=self.prompt_version if enabled else None,
            schedule_version=json.dumps(schedule_dependencies, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            rule_version=json.dumps(rule_snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            validated_model_json=(
                json.dumps(model_result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                if model_result is not None else None
            ),
            suggested_comment=suggested_comment,
            error_code=error_code,
            error_message=f'{"llm" if not llm_available else "none"}:{error_code}' if error_code else None,
        )
        db.session.add(assessment)
        db.session.flush()
        for finding in classification.findings:
            db.session.add(ReviewFinding(
                assessment_id=assessment.id,
                source=finding.source.value,
                rule_key=finding.rule_key,
                severity=finding.severity.value,
                title=finding.title,
                message=finding.message,
                objective=finding.objective,
                evidence_strength=finding.evidence_strength.value,
                evidence_json=json.dumps(finding.evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            ))
        db.session.commit()
        return self._summary(assessment, cache_hit=False)

    def _load_latest_form(self, form_id: int):
        form = db.session.get(LectureForm, form_id)
        if form is None:
            raise KeyError(f'unknown form: {form_id}')
        unique_id = field_value(form, 'unique_id')
        if unique_id is None:
            return form
        forms = LectureForm.query.filter_by(unique_id=unique_id).all()
        return max(forms, key=lambda item: (
            field_value(item, 'updated_at') or datetime.min,
            field_value(item, 'id') or 0,
        ))

    def _load_schedule(self, form):
        if self.schedule_loader is None:
            return SimpleSchedule()
        return self.schedule_loader(form)

    def _load_history(self, form):
        if self.history_loader is not None:
            return self.history_loader(form) or ()
        listener_number = field_value(form, 'listener_number')
        if listener_number is None:
            return (form,)
        return LectureForm.query.filter_by(listener_number=listener_number).all()

    def _load_school_matches(self, form):
        if self.school_matches_loader is None:
            return ()
        return self.school_matches_loader(form) or ()

    def _load_listener_profile(self, form):
        if self.listener_profile_loader is None:
            return None
        return self.listener_profile_loader(form)

    def _load_schedule_dependencies(self, form, schedule):
        if callable(self.schedule_dependencies):
            return self.schedule_dependencies(form, schedule)
        return self.schedule_dependencies

    def _run_deterministic(self, form, context):
        if self.deterministic_runner is not None:
            result = self.deterministic_runner(form, context)
        else:
            result = execute_rules(_rule_views(self.rule_revisions), context=context)
        return tuple(_as_finding(item) for item in (result or ()))

    @staticmethod
    def _coverage(schedule):
        raw = field_value(schedule, 'coverage', default=ScheduleCoverage.NONE)
        try:
            return ScheduleCoverage(getattr(raw, 'value', raw))
        except (TypeError, ValueError):
            return ScheduleCoverage.NONE

    @staticmethod
    def _llm_findings(model_result):
        findings = []
        for item in model_result.get('findings', []):
            findings.append(FindingDraft(
                rule_key=f'llm:{item["code"]}',
                source=FindingSource.LLM,
                severity=FindingSeverity(item['severity']),
                title='DeepSeek 语义问题',
                message=item['message'],
                objective=False,
                evidence_strength=EvidenceStrength.WEAK,
                evidence={'evidence': item['evidence'], 'code': item['code']},
            ))
        return tuple(findings)

    @staticmethod
    def _dependency_snapshot(schedule_dependencies, rule_snapshot):
        ids = {}
        versions = {'rules': rule_snapshot}
        for key, value in (schedule_dependencies or {}).items():
            if isinstance(value, Mapping):
                if 'id' in value:
                    ids[str(key)] = value['id']
                if 'version' in value:
                    versions[str(key)] = value['version']
        return ids, versions

    @staticmethod
    def _summary(assessment, *, cache_hit):
        return AssessmentSummary(
            assessment_id=assessment.id,
            form_id=assessment.form_id,
            fingerprint=assessment.fingerprint,
            category=assessment.classification,
            coverage=assessment.coverage,
            cache_hit=cache_hit,
            finding_count=len(assessment.findings),
            error_code=assessment.error_code,
            suggested_comment=assessment.suggested_comment or '',
        )


@dataclass(frozen=True)
class SimpleSchedule:
    coverage: ScheduleCoverage
    slots: tuple[Any, ...] = ()
    admin_classes: tuple[str, ...] = ()

    def __init__(self, coverage: ScheduleCoverage = ScheduleCoverage.NONE, slots=(), admin_classes=()):
        object.__setattr__(self, 'coverage', coverage)
        object.__setattr__(self, 'slots', tuple(slots))
        object.__setattr__(self, 'admin_classes', tuple(admin_classes))


__all__ = ['AssessmentService', 'AssessmentSummary']
