"""Atomic ordinary-draft saves; surrounding photo updates share the transaction."""
from datetime import datetime, timedelta
import json

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy import inspect
from sqlalchemy.orm.attributes import set_committed_value

from app.models import LectureFormDraft, db


class LectureFormDraftConflict(Exception):
    """A draft changed, disappeared, or was created by another request."""


def save_lecture_form_draft_atomic(user_id, draft_key, original_draft, payload, *, entry_mode=None):
    """Write only the exact raw draft/version originally read by this request.

    This performs no commit. Once the SQL write succeeds, associated photo
    changes must complete in the same transaction; a failure rolls both back.
    The raw JSON comparison also catches non-autosave writers such as resume,
    course confirmation and photo upload, without database-specific JSON APIs.
    """
    serialized=json.dumps(payload,ensure_ascii=False,sort_keys=True)
    # Switching to a photo record revokes an old manual draft's exemption.
    policy = ('photo' if payload.get('site_capture_id') else entry_mode or
              (original_draft.entry_mode if original_draft is not None else None) or 'photo')
    if policy not in ('manual', 'photo', 'legacy'):
        raise ValueError('Invalid server draft entry policy')
    written_at=datetime.now()
    try:
        with db.session.no_autoflush:
            if original_draft is None or inspect(original_draft).transient or inspect(original_draft).pending:
                draft=original_draft if original_draft is not None else LectureFormDraft(user_id=user_id,draft_key=draft_key)
                draft.payload_json=serialized
                draft.entry_mode=policy
                draft.updated_at=written_at
                db.session.add(draft)
                db.session.flush()
                return written_at
            expected_updated=original_draft.updated_at
            if expected_updated is not None:
                written_at=max(written_at,expected_updated+timedelta(microseconds=1))
            timestamp_condition=(LectureFormDraft.updated_at.is_(None) if expected_updated is None
                                 else LectureFormDraft.updated_at==expected_updated)
            statement=db.update(LectureFormDraft).where(
                LectureFormDraft.id==original_draft.id,
                LectureFormDraft.user_id==user_id,
                LectureFormDraft.draft_key==draft_key,
                LectureFormDraft.payload_json==original_draft.payload_json,
                timestamp_condition,
            ).values(payload_json=serialized,updated_at=written_at,entry_mode=policy).execution_options(synchronize_session=False)
            matched=db.session.execute(statement).rowcount
        if matched!=1:
            raise LectureFormDraftConflict('听课草稿已更新或删除，请保留当前输入并刷新后继续填写。')
    except IntegrityError as error:
        detail=str(error).lower()
        if ('unique_user_lecture_form_draft' in detail or
                ('unique constraint failed' in detail and 'lecture_form_drafts.user_id' in detail)):
            raise LectureFormDraftConflict('听课草稿已由其他请求更新，请刷新后继续填写。') from error
        raise
    except OperationalError as error:
        if 'database is locked' in str(error).lower() or 'database table is locked' in str(error).lower():
            raise LectureFormDraftConflict('听课草稿正在更新，请保留当前输入并重试。') from error
        raise
    # Avoid an ORM flush/onupdate rewriting the successful version claim. The
    # route may still use the loaded object, but this SQL write is authoritative.
    set_committed_value(original_draft,'payload_json',serialized)
    set_committed_value(original_draft,'entry_mode',policy)
    set_committed_value(original_draft,'updated_at',written_at)
    return written_at
