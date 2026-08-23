# -*- coding: utf-8 -*-
"""LectureForm <-> CourseRegistration binding query semantics.

Distinguish:
- logical form count (UI/user-facing "bound feedback forms")
- reference existence (is a reservation bound at all)
- physical row count (deletion cleanup)
"""
from sqlalchemy import func

from app.models import LectureForm, db


def get_registration_logical_form_counts(registration_ids):
    """Return ``{registration_id: logical_form_count}`` for a batch of ids.

    A logical form is identified by ``COALESCE(unique_id, id)``; multiple
    physical versions with the same logical id count as one.
    """
    ids = [int(rid) for rid in registration_ids if rid is not None]
    if not ids:
        return {}

    logical_id_expr = func.coalesce(LectureForm.unique_id, LectureForm.id)
    rows = (
        db.session.query(
            LectureForm.registration_id,
            func.count(func.distinct(logical_id_expr)).label('logical_count'),
        )
        .filter(LectureForm.registration_id.in_(ids))
        .filter(LectureForm.registration_id.isnot(None))
        .group_by(LectureForm.registration_id)
        .all()
    )
    return {registration_id: int(count) for registration_id, count in rows}


def registration_has_form_binding(registration_id):
    """Return True when at least one LectureForm references the registration."""
    if registration_id is None:
        return False
    return (
        db.session.query(LectureForm.id)
        .filter(LectureForm.registration_id == registration_id)
        .first()
        is not None
    )
