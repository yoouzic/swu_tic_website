# -*- coding: utf-8 -*-
"""Server-side drafts for review form pages."""

import json
from datetime import datetime

from ..models import LectureFormDraft, db


REVIEW_FORM_DRAFT_PREFIX = 'review_form:'


def review_form_draft_key(form_id):
    return f'{REVIEW_FORM_DRAFT_PREFIX}{int(form_id)}'


def _json_safe(value):
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if isinstance(key, str):
                normalized[key] = _json_safe(item)
        return normalized
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def normalize_review_draft_payload(raw_payload):
    if not isinstance(raw_payload, dict):
        return None
    payload = {
        'form_data': _json_safe(raw_payload.get('form_data') or {}),
        'review_comment': str(raw_payload.get('review_comment') or ''),
        'score_data': _json_safe(raw_payload.get('score_data') or []),
    }
    if not isinstance(payload['form_data'], dict):
        payload['form_data'] = {}
    if not isinstance(payload['score_data'], list):
        payload['score_data'] = []
    return payload


def load_review_form_draft(user_id, form_id):
    return LectureFormDraft.query.filter_by(
        user_id=user_id,
        draft_key=review_form_draft_key(form_id),
    ).first()


def parse_review_form_draft(draft):
    if not draft or not draft.payload_json:
        return {}
    try:
        payload = json.loads(draft.payload_json)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_review_form_draft(user_id, form_id, payload):
    draft = load_review_form_draft(user_id, form_id)
    if not draft:
        draft = LectureFormDraft(
            user_id=user_id,
            draft_key=review_form_draft_key(form_id),
        )
        db.session.add(draft)
    draft.payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    draft.updated_at = datetime.now()
    return draft


def delete_review_form_draft(user_id, form_id):
    draft = load_review_form_draft(user_id, form_id)
    if not draft:
        return False
    db.session.delete(draft)
    return True
