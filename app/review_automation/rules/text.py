"""Deterministic text findings; these handlers never rewrite submitted text."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
)

from .registry import RuleContext, RuleRevisionView, field_value


def normalize_feedback(value: Any) -> str:
    """Normalize whitespace for checking while leaving the original value untouched."""
    if value is None:
        return ''
    text = unicodedata.normalize('NFKC', str(value))
    return re.sub(r'\s+', '', text)


def feedback_text(context: RuleContext) -> str:
    form = context.form
    value = field_value(form, 'course_feedback', 'feedback', 'text', default='')
    return '' if value is None else str(value)


def _severity(revision: RuleRevisionView) -> FindingSeverity:
    return FindingSeverity(revision.severity)


def _finding(
    revision: RuleRevisionView,
    *,
    title: str,
    message: str,
    evidence: dict[str, Any],
    evidence_strength: EvidenceStrength = EvidenceStrength.APPROXIMATE,
) -> FindingDraft:
    return FindingDraft(
        rule_key=revision.rule_key,
        source=FindingSource.RULE,
        severity=_severity(revision),
        title=title,
        message=message,
        objective=False,
        evidence_strength=evidence_strength,
        evidence=evidence,
    )


class RequiredPrefixRule:
    @staticmethod
    def evaluate(context: RuleContext, revision: RuleRevisionView):
        source = feedback_text(context)
        normalized = normalize_feedback(source)
        required = str(revision.parameters['required_prefix'])
        if normalized.startswith(required):
            return ()
        return (_finding(
            revision,
            title='反馈缺少规定前缀',
            message=f'反馈应以“{required}”开头，建议人工复核填写规范。',
            evidence={
                'required_prefix': required,
                'actual_prefix': normalized[:len(required)],
            },
        ),)


class MinimumLengthRule:
    @staticmethod
    def evaluate(context: RuleContext, revision: RuleRevisionView):
        source = feedback_text(context)
        normalized = normalize_feedback(source)
        chinese_count = sum('\u3400' <= char <= '\u9fff' for char in normalized)
        character_count = chinese_count if chinese_count else len(normalized)
        minimum = int(revision.parameters['minimum_characters'])
        if character_count >= minimum:
            return ()
        return (_finding(
            revision,
            title='反馈内容偏短',
            message=f'规范化反馈少于 {minimum} 个字，建议人工复核具体性。',
            evidence={
                'character_count': character_count,
                'minimum_characters': minimum,
            },
        ),)


class ConfusionPatternRule:
    @staticmethod
    def evaluate(context: RuleContext, revision: RuleRevisionView):
        source = feedback_text(context)
        findings = []
        for item in revision.parameters['patterns']:
            matcher = re.compile(item['regex'])
            for match in matcher.finditer(source):
                if match.start() == match.end():
                    continue
                findings.append(_finding(
                    revision,
                    title='疑似易混用词语',
                    message=item['message'],
                    evidence={
                        'matched_text': match.group(0),
                        'span': {'start': match.start(), 'end': match.end()},
                        'suggestion': item['message'],
                    },
                ))
        return tuple(findings)


class SafeRegexRule:
    @staticmethod
    def evaluate(context: RuleContext, revision: RuleRevisionView):
        source = feedback_text(context)
        parameters = revision.parameters
        pattern = re.escape(parameters['phrase']) if 'phrase' in parameters else parameters['regex']
        matcher = re.compile(pattern)
        findings = []
        for match in matcher.finditer(source):
            if match.start() == match.end():
                continue
            findings.append(_finding(
                revision,
                title='命中配置文本规则',
                message=parameters['message'],
                evidence={
                    'matched_text': match.group(0),
                    'span': {'start': match.start(), 'end': match.end()},
                    'suggestion': parameters['message'],
                },
            ))
        return tuple(findings)


__all__ = [
    'ConfusionPatternRule',
    'MinimumLengthRule',
    'RequiredPrefixRule',
    'SafeRegexRule',
    'feedback_text',
    'normalize_feedback',
]
