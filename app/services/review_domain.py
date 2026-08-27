# -*- coding: utf-8 -*-
"""Review scope domain service.

This module centralizes the stable authorization invariant:

    1. actor must have review permission (via get_reviewable_users)
    2. form.listener_number must match exactly one active User
    3. unknown/inactive owner -> deny
    4. owner.id outside get_reviewable_users -> deny
    5. no NULL/unknown fail-open behavior
    6. super admin continues through get_reviewable_users semantics
    7. group reviewer with group_id=NULL -> fail closed
    8. service never decides HTTP status/message
"""
from app.models import User
from app.utils.review_permissions import get_reviewable_users
from app.utils.user_status import active_user_filter


def partition_forms_by_review_scope(actor_user_id, forms):
    """Partition ``forms`` into ``(allowed_forms, denied_forms)``.

    - preserves input order in both returned lists
    - performs one reviewable-user lookup and one bulk active-user query
    - unknown/inactive owner => denied
    - actor without review permission => all denied
    - never raises Flask/HTTP errors
    """
    if not forms:
        return [], []

    reviewable_user_ids = set(get_reviewable_users(actor_user_id))

    if not reviewable_user_ids:
        return [], list(forms)

    listener_numbers = {
        getattr(form, 'listener_number', None)
        for form in forms
        if getattr(form, 'listener_number', None)
    }

    users_by_number = {}
    if listener_numbers:
        users = (
            User.query
            .filter(User.number.in_(listener_numbers))
            .filter(active_user_filter())
            .all()
        )
        users_by_number = {user.number: user for user in users}

    allowed_forms = []
    denied_forms = []
    for form in forms:
        if form is None:
            denied_forms.append(form)
            continue
        form_user = users_by_number.get(getattr(form, 'listener_number', None))
        if not form_user or form_user.id not in reviewable_user_ids:
            denied_forms.append(form)
        else:
            allowed_forms.append(form)

    return allowed_forms, denied_forms


def is_form_in_review_scope(actor_user_id, form):
    """Return True only when a single form is inside the actor review scope."""
    if form is None:
        return False
    allowed, _denied = partition_forms_by_review_scope(actor_user_id, [form])
    return len(allowed) == 1
