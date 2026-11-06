"""HTTP preconditions for the shared listening-form draft."""
from flask import request
from app.services.lecture_form_draft_concurrency import get_draft_revision


class DraftRevisionError(Exception):
    def __init__(self, message, code='draft_conflict', status=409):
        super().__init__(message)
        self.code, self.status = code, status


def require_draft_revision(user_id, draft_key, draft, *, allow_missing_if_absent=False):
    candidates = []
    header = request.headers.get('X-Lecture-Draft-Revision')
    if header is not None:
        candidates.append(header)
    body = request.get_json(silent=True) if request.is_json else request.form
    if hasattr(body, 'get') and 'expected_draft_revision' in body:
        candidates.append(body['expected_draft_revision'])
    if not candidates or all(value is None or value == '' for value in candidates):
        if allow_missing_if_absent and draft is None:
            return 'none'
        raise DraftRevisionError('页面缺少草稿版本，请刷新后再保存。', 'draft_revision_required', 428)
    if any(not isinstance(value, str) or not value for value in candidates) or len(set(candidates)) != 1:
        raise DraftRevisionError('草稿版本不一致，请保留当前输入并刷新。')
    expected = candidates[0]
    if expected != get_draft_revision(user_id, draft_key, draft):
        raise DraftRevisionError('听课草稿已在其他页面更新，请保留当前输入并刷新。')
    return expected
