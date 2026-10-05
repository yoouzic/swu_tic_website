"""Durable 120-second submission receipts; the caller owns lock and commit."""
from datetime import timedelta
import hashlib
import json
import re

from app.models import LectureForm, db


submission_receipts = db.Table(
    'lecture_form_submission_receipts', db.metadata,
    db.Column('user_id', db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('signature', db.String(64), primary_key=True),
    # Deleted forms are explicitly cleaned in each deletion transaction. This
    # also prevents a recycled SQLite form ID from inheriting an old receipt.
    db.Column('form_id', db.Integer, nullable=False),
    db.Column('accepted_at', db.DateTime, nullable=False, index=True),
)

BUSINESS_FIELDS = (
    'listener_number', 'course_changes', 'lecture_date', 'class_period', 'lecture_location',
    'teacher_name', 'teacher_college', 'course_title', 'student_grade_class', 'abnormal_situation',
    'teaching_method', 'classroom_discipline', 'classroom_atmosphere', 'courseware_quality',
    'overall_effect', 'quality_case', 'course_feedback', 'suggestions', 'student_signature1',
    'contact_phone1', 'student_signature2', 'contact_phone2', 'registration_id',
)


def positive_sqlite_id(value):
    text = str(value).strip() if value is not None else ''
    if len(text) > 19 or re.fullmatch(r'[0-9]+', text) is None:
        raise ValueError('invalid SQLite identifier')
    number = int(text)
    if not 1 <= number <= 9223372036854775807:
        raise ValueError('invalid SQLite identifier')
    return number


def stable_submission_signature(form_data, *, requested_suggestions, capture_id=None, registration_id=None):
    """Hash consumed facts, before state-dependent system leave annotations.

    CSRF, page revisions and other unconsumed request metadata never enter the
    signature. Physical capture/registration identities remain distinct.
    """
    payload = {field: str(form_data.get(field) or '').strip() for field in BUSINESS_FIELDS}
    payload['suggestions'] = str(requested_suggestions or '').strip()
    payload['registration_id'] = str(registration_id or '')
    payload['site_capture_id'] = str(capture_id or '')
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def find_submission_receipt(user_id, user_number, signature, now):
    # Bounded per-actor cleanup cannot lock another actor's active receipt.
    db.session.execute(db.delete(submission_receipts).where(
        submission_receipts.c.user_id == user_id,
        submission_receipts.c.accepted_at < now - timedelta(seconds=120),
    ))
    receipt = db.session.execute(db.select(submission_receipts).where(
        submission_receipts.c.user_id == user_id,
        submission_receipts.c.signature == signature,
    )).mappings().first()
    if receipt is None:
        return None
    form = db.session.get(LectureForm, receipt['form_id'])
    return form if form is not None and form.listener_number == user_number else None


def remember_submission_receipt(user_id, signature, form_id, accepted_at):
    key = (submission_receipts.c.user_id == user_id, submission_receipts.c.signature == signature)
    existing = db.session.execute(db.select(submission_receipts).where(*key)).mappings().first()
    if existing is not None and existing['form_id'] == form_id:
        return  # Retries do not extend the original acceptance window.
    values = dict(user_id=user_id, signature=signature, form_id=form_id, accepted_at=accepted_at)
    if existing is None:
        db.session.execute(db.insert(submission_receipts).values(**values))
    else:
        db.session.execute(db.update(submission_receipts).where(*key).values(**values))


def delete_user_submission_receipts(user_id):
    """Explicit cleanup also works when SQLite FK enforcement is disabled."""
    db.session.execute(db.delete(submission_receipts).where(submission_receipts.c.user_id == user_id))


def delete_form_submission_receipts(form_ids):
    """Release receipts before physical row IDs become reusable; never commit."""
    ids = set(form_ids)
    if ids:
        db.session.execute(db.delete(submission_receipts).where(submission_receipts.c.form_id.in_(ids)))
