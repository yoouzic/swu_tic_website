"""Delete only the ordinary draft revision consumed by this submission."""
import json

from app.models import LectureFormDraft, db


def snapshot_submission_draft(draft):
    if draft is None:
        return None
    return {'id': draft.id, 'user_id': draft.user_id, 'draft_key': draft.draft_key,
            'payload_json': draft.payload_json, 'updated_at': draft.updated_at}


def delete_consumed_submission_draft(revision, *, logical_id=None, capture_id=None, legacy_manual_entry=False):
    if revision is None:
        return False  # A draft created later belongs to a different interaction.
    try:
        payload = json.loads(revision['payload_json'] or '{}')
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if str(payload.get('site_capture_id') or '') != str(capture_id or ''):
        return False
    # A new manual form deliberately discards obsolete client version fields.
    # Only its server-verified draft policy can permit consuming that legacy
    # payload; the exact raw revision below still protects a newer draft.
    discard_legacy_identity = legacy_manual_entry and logical_id is None
    if not discard_legacy_identity and str(payload.get('unique_id') or '') != str(logical_id or ''):
        return False
    timestamp = revision['updated_at']
    timestamp_condition = (LectureFormDraft.updated_at.is_(None) if timestamp is None
                           else LectureFormDraft.updated_at == timestamp)
    with db.session.no_autoflush:
        deleted = db.session.execute(db.delete(LectureFormDraft).where(
            LectureFormDraft.id == revision['id'],
            LectureFormDraft.user_id == revision['user_id'],
            LectureFormDraft.draft_key == revision['draft_key'],
            LectureFormDraft.payload_json == revision['payload_json'],
            timestamp_condition,
        ).execution_options(synchronize_session=False)).rowcount
    return deleted == 1
