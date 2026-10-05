"""Atomic review claims shared by approval and rejection transactions."""
from datetime import datetime, timedelta

from sqlalchemy.exc import OperationalError

from app.models import LectureForm, db


class ReviewConflict(Exception):
    """The expected physical version changed after the reviewer opened it."""


def validate_opened_review_revision(original_form, payload, form_data=None):
    """Require the revision the client actually opened, before claiming a row."""
    form_data = form_data if isinstance(form_data, dict) else {}
    expected_id = payload.get('expected_form_id', form_data.get('expected_form_id'))
    expected_time = payload.get('expected_form_updated_at', form_data.get('expected_form_updated_at'))
    timestamp = original_form.updated_at.isoformat() if original_form.updated_at else ''
    if str(expected_id) != str(original_form.id) or expected_time != timestamp:
        raise ReviewConflict('表单已更新，请保留审核草稿并刷新后重新核对')


def claim_review_form(original_form, logical_id):
    """Claim the expected latest row until its surrounding transaction ends.

    The compare-and-set obtains the database's write lock and changes the
    activity timestamp. A competing transaction must match the old timestamp
    and latest ID, so it cannot append a second decision from the same version.
    Historic business fields, status, comments and scores are left intact.
    """
    expected_updated = original_form.updated_at
    latest_id = db.select(db.func.max(LectureForm.id)).where(db.or_(
        LectureForm.id == logical_id,
        LectureForm.unique_id == logical_id,
    )).scalar_subquery()
    timestamp_condition = (LectureForm.updated_at.is_(None) if expected_updated is None
                           else LectureForm.updated_at == expected_updated)
    claimed_at = datetime.now()
    if expected_updated is not None:
        claimed_at = max(claimed_at, expected_updated + timedelta(microseconds=1))
    statement = db.update(LectureForm).where(
        LectureForm.id == original_form.id,
        LectureForm.id == latest_id,
        LectureForm.status == original_form.status,
        timestamp_condition,
    ).values(updated_at=claimed_at).execution_options(synchronize_session=False)
    try:
        with db.session.no_autoflush:
            claimed = db.session.execute(statement).rowcount
    except OperationalError as error:
        if 'database is locked' in str(error).lower() or 'database table is locked' in str(error).lower():
            raise ReviewConflict('该表单正在更新，请保留草稿并刷新页面后重试') from error
        raise
    if claimed != 1:
        raise ReviewConflict('该表单已有更新版本，请保留草稿并刷新页面后操作')
    # Keep ORM onupdate and in-place adapters from writing the old revision
    # back over the successful SQL claim later in this same transaction.
    original_form.updated_at = claimed_at
    return claimed_at
