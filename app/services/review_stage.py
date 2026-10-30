"""Auxiliary stage labels for forms that ended at 部门已审核."""
from app.utils.review_permissions import get_user_review_permissions

DEPARTMENT_REVIEW_STATUS = '部门已审核'
STAGE_LABELS = {
    '审表_小组': '小组长已审核',
    '审表_部门': '部长已审核',
}


def review_stage_labels(forms):
    """Return {form_id: label} derived from each version's reviewer permission.

    The formal status machine stays unchanged. This is display-only metadata,
    so a later permission change can relabel historical approvals.
    """
    reviewed = [form for form in forms
                if form and form.status == DEPARTMENT_REVIEW_STATUS and form.reviewer_id]
    if not reviewed:
        return {}

    permissions = get_user_review_permissions({form.reviewer_id for form in reviewed})
    labels = {}
    for form in reviewed:
        label = STAGE_LABELS.get(permissions.get(form.reviewer_id))
        if label:
            labels[form.id] = label
    return labels
