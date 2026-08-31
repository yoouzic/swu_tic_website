# -*- coding: utf-8 -*-
"""LectureForm <-> CourseRegistration binding query semantics.

Distinguish:
- logical form count (UI/user-facing "bound feedback forms")
- reference existence (is a reservation bound at all)
- physical row count (deletion cleanup)

Canonical contract (Round 8A): the authoritative binding state is the
existence of ``LectureForm.registration_id == registration.id``.
``CourseRegistration.is_used`` is a compatibility / denormalized mirror only
and never decides availability, editability or display semantics.
"""
from sqlalchemy import func

from app.models import CourseRegistration, LectureForm, db


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


def reconcile_registration_usage_flags(registration_ids):
    """Sync the ``CourseRegistration.is_used`` mirror with actual bindings.

    Round 8A transaction-local mirror primitive: for each given id, set
    ``is_used`` to whether at least one LectureForm currently references it
    (reference existence, not logical-form count).  Pending form mutations
    must be flushed by the caller first (explicit flush strategy — never rely
    on hidden autoflush).  No commit / no rollback / no Flask: transaction
    ownership stays with the caller.
    """
    ids = sorted({int(rid) for rid in registration_ids if rid is not None})
    if not ids:
        return {}

    bound_counts = get_registration_logical_form_counts(ids)
    flags = {}
    for registration_id in ids:
        bound = bound_counts.get(registration_id, 0) > 0
        flags[registration_id] = bound
        registration = db.session.get(CourseRegistration, registration_id)
        if registration is not None and registration.is_used != bound:
            registration.is_used = bound
    return flags
