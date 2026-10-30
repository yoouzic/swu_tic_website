"""Personal read acknowledgements, separate from review decisions and drafts."""
from datetime import datetime

from app.models import ReviewReadReceipt, db
from app.services.review_domain import partition_forms_by_review_scope
from app.utils.review_permissions import get_user_review_permission


def _read_state(read_at=None):
    return {'state': 'read' if read_at else 'unread',
            'read_at': read_at.strftime('%Y-%m-%d %H:%M:%S') if read_at else None}


def department_read_states(user_id, forms, permission=None):
    """Bulk-load only this actor's receipts for forms in their review scope."""
    result = {form.id: None for form in forms}
    if not forms or (permission or get_user_review_permission(user_id)) != '审表_部门':
        return result
    allowed, _ = partition_forms_by_review_scope(user_id, forms)
    eligible = [form for form in allowed if form.status == '部门已审核']
    if not eligible:
        return result
    receipts = {receipt.form_id: receipt for receipt in ReviewReadReceipt.query.filter(
        ReviewReadReceipt.user_id == user_id,
        ReviewReadReceipt.form_id.in_([form.id for form in eligible]),
    ).all()}
    for form in eligible:
        receipt = receipts.get(form.id)
        if form.reviewer_id == user_id:
            result[form.id] = _read_state(form.review_time or form.updated_at or form.created_at)
        elif receipt and receipt.form_updated_at == form.updated_at:
            result[form.id] = _read_state(receipt.read_at)
        else:
            result[form.id] = _read_state()
    return result


def record_department_read(user_id, form):
    """Record an authorized current revision; the caller owns the transaction.

    The route validates permission/scope/latest ID and the revision the UI
    displayed. No form field or audit history is changed by this operation.
    """
    receipt = db.session.get(ReviewReadReceipt, (user_id, form.id))
    if receipt is None:
        receipt = ReviewReadReceipt(user_id=user_id, form_id=form.id, form_updated_at=form.updated_at)
        db.session.add(receipt)
        # Start a real write transaction, rather than a top-level SQLite
        # savepoint, so post-write revision validation can roll this back.
        db.session.flush()
    elif receipt.form_updated_at != form.updated_at:
        previous_revision = receipt.form_updated_at
        db.session.execute(db.update(ReviewReadReceipt).where(
            ReviewReadReceipt.user_id == user_id,
            ReviewReadReceipt.form_id == form.id,
            ReviewReadReceipt.form_updated_at == previous_revision,
        ).values(form_updated_at=form.updated_at, read_at=datetime.now())
         .execution_options(synchronize_session=False))
        db.session.refresh(receipt)
    return receipt
