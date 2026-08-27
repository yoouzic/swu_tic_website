# -*- coding: utf-8 -*-
"""Pure score payload validation/normalization for review endpoints.

This module must not import Flask, SQLAlchemy, models, db, request, or session.
It only validates and normalizes the JSON score_data payload before any review
mutation is allowed to occur.
"""
import math


class ScoreValidationError(ValueError):
    """Raised when a review score payload is invalid."""


def _normalize_score_value(value):
    if value is None:
        return 0.0
    if isinstance(value, bool):
        raise ScoreValidationError('评分必须是数字')
    if isinstance(value, str) and value.strip() == '':
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ScoreValidationError('评分必须是数字') from None
    if not math.isfinite(parsed):
        raise ScoreValidationError('评分必须是有限数字')
    if parsed < 0:
        raise ScoreValidationError('评分不能为负数')
    return parsed


def _normalize_auto_flag(value):
    """Normalize the legacy is_auto / is_auto_generated flag.

    The narrow parser accepts real booleans plus the common numeric/string
    representations that historically appeared in JSON payloads.  It never
    applies Python's truthiness to strings, so ``"false"`` stays false.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('true', '1'):
            return True
        if normalized in ('false', '0'):
            return False
    raise ScoreValidationError('自动标记必须是布尔值')


def normalize_score_items(raw_score_data):
    """Normalize a raw score_data payload into validated items.

    Returns:
        list[dict] with keys:
            reason, department_score, personal_score, is_auto_generated
    """
    if raw_score_data is None:
        raw_score_data = []
    if not isinstance(raw_score_data, list):
        raise ScoreValidationError('score_data 必须是数组')

    normalized = []
    for item in raw_score_data:
        if not isinstance(item, dict):
            raise ScoreValidationError('评分项必须是对象')

        reason = item.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            raise ScoreValidationError('评分原因不能为空')
        reason = reason.strip()

        department_score = _normalize_score_value(item.get('department_score'))
        personal_score = _normalize_score_value(item.get('personal_score'))

        if 'is_auto_generated' in item:
            is_auto_generated = _normalize_auto_flag(item['is_auto_generated'])
        else:
            is_auto_generated = _normalize_auto_flag(item.get('is_auto', False))

        normalized.append({
            'reason': reason,
            'department_score': department_score,
            'personal_score': personal_score,
            'is_auto_generated': is_auto_generated,
        })

    return normalized
