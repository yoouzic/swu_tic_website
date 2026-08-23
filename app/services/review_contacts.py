# -*- coding: utf-8 -*-
"""Canonical review contact reference service.

The legacy AutoReviewEngine used an old contacts Excel file for reviewer
lookup in ``/api/review/reference_data`` and ``/api/review/auto_check``.
This service sources the same contact fields from the admin-maintained
``User`` table instead.  It intentionally keeps the legacy contact response
shape and matching behavior, but removes the stale Excel runtime dependency.

This module contains no Flask/HTTP/blueprint dependency.
"""
from difflib import SequenceMatcher
from typing import Optional

from app.models import User
from app.utils.user_status import UNASSIGNED_GROUP_NAME, active_user_filter


def _legacy_department_value(user):
    department = (user.department or '').strip()
    group = (user.group or '').strip()
    if department and group and group not in (UNASSIGNED_GROUP_NAME, '待分配'):
        return f'{department}/{group}'
    return department


def _contact_payload(user):
    return {
        'id': str(user.number or ''),
        'name': user.name or '',
        'department': _legacy_department_value(user),
        'college': user.college or '',
        'phone': user.phone or '',
    }


def find_reviewer_by_id(reviewer_id: str) -> Optional[dict]:
    """Return a legacy-shaped contact payload for an exact user number."""
    if not reviewer_id:
        return None
    user = (
        User.query
        .filter(User.number == str(reviewer_id))
        .filter(active_user_filter())
        .order_by(User.id.asc())
        .first()
    )
    return _contact_payload(user) if user else None


def find_reviewer_by_name(name: str, fuzzy: bool = True) -> Optional[dict]:
    """Return a legacy-shaped contact payload for exact or fuzzy name match.

    Exact matches do not include ``similarity``; fuzzy matches only return a
    candidate when similarity is strictly greater than ``0.8``, matching the
    legacy ``SequenceMatcher`` behavior.
    """
    if not name:
        return None
    clean_name = name
    if '（' in name and '）' in name:
        clean_name = name.split('（')[0].strip()

    exact_user = (
        User.query
        .filter(User.name == clean_name)
        .filter(active_user_filter())
        .order_by(User.id.asc())
        .first()
    )
    if exact_user:
        return _contact_payload(exact_user)

    if not fuzzy:
        return None

    best_payload = None
    best_score = 0.8
    users = User.query.filter(active_user_filter()).order_by(User.id.asc()).all()
    for user in users:
        score = SequenceMatcher(None, clean_name, user.name).ratio()
        if score > best_score:
            best_score = score
            best_payload = _contact_payload(user)
            best_payload['similarity'] = score
    return best_payload
