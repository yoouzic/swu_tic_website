"""Compact, request-scoped operational events for the WSGI error stream."""

import logging

from flask import request, session
from flask.logging import wsgi_errors_stream


operation_logger = logging.getLogger('swu_tic.operations')
operation_logger.setLevel(logging.INFO)
operation_logger.propagate = False
if not operation_logger.handlers:
    operation_logger.addHandler(logging.StreamHandler(wsgi_errors_stream))

_FORM_WRITE_OPERATIONS = {
    ('user.lecture_form_draft', 'PUT'): 'draft_save',
    ('user.submit_form', 'POST'): 'form_submit',
}
_PRIVATE_OPERATIONS = {
    ('user.site_capture_upload', 'POST'): 'photo_upload',
    ('user.site_capture_ocr_region', 'POST'): 'photo_ocr',
    ('user.site_capture_location', 'PATCH'): 'location',
    ('user.site_capture_map', 'GET'): 'location',
    ('user.site_capture_context', 'PATCH'): 'course_match',
    ('user.site_capture_suggestions', 'GET'): 'course_match',
    ('user.site_capture_suggestions', 'POST'): 'course_match',
    ('user.site_capture_confirm', 'POST'): 'course_match',
    ('user.listening_assistant_candidates', 'GET'): 'course_match',
    ('user.listening_assistant_guide_start', 'POST'): 'course_match',
    ('user.listening_assistant_guide_answer', 'POST'): 'course_match',
    ('user.listening_assistant_confirm', 'POST'): 'course_match',
    ('user.listening_assistant_fallback', 'POST'): 'course_match',
}

_PRIVATE_HTTP_REASONS = {
    400: 'request_invalid',
    401: 'login_required',
    403: 'permission_denied',
    404: 'capture_unavailable',
}


def form_write_operation():
    return _FORM_WRITE_OPERATIONS.get((request.endpoint, request.method))


def private_failure_operation():
    return request.environ.get('swu_tic.photo_operation') or _PRIVATE_OPERATIONS.get(
        (request.endpoint, request.method),
    )


def set_form_record(kind, record_id):
    request.environ['swu_tic.form_record'] = f'{kind}:{record_id}'


def begin_uploaded_photo_ocr(capture_id):
    set_form_record('capture', capture_id)
    request.environ['swu_tic.photo_operation'] = 'photo_ocr'


def mark_form_failure(reason, *, fields=(), error=None):
    # The caller supplies a fixed reason, schema field names and an exception
    # type only. Exception messages may include SQL parameters or form data.
    request.environ['swu_tic.form_failure'] = (
        reason,
        tuple(sorted(fields)),
        type(error).__name__ if error is not None else None,
    )


def log_form_write_failure(response):
    operation = form_write_operation()
    failure = request.environ.get('swu_tic.form_failure')
    status = response.status_code
    if operation is None or (failure is None and status < 400):
        return response

    reason, fields, error_type = failure or (f'http_{status}', (), None)
    actor_id = request.environ.get('swu_tic.form_actor_id', session.get('user_id'))
    details = ''
    if fields:
        details += f' fields={",".join(fields)}'
    if error_type:
        details += f' error_type={error_type}'
    operation_logger.log(
        logging.ERROR if status >= 500 else logging.INFO,
        'form_write_failed operation=%s reason=%s status=%s user_id=%s record=%s%s',
        operation, reason, status, actor_id if actor_id is not None else '-',
        request.environ.get('swu_tic.form_record', '-'), details,
    )
    return response


def log_private_failure(response):
    operation = private_failure_operation()
    failure = request.environ.get('swu_tic.form_failure')
    status = response.status_code
    if operation is None or (failure is None and status < 400):
        return response

    if failure:
        reason = failure[0]
    elif operation == 'course_match' and status == 503:
        reason = 'schedule_source_unavailable'
    elif operation == 'photo_upload' and status == 413:
        reason = 'photo_too_large'
    else:
        reason = _PRIVATE_HTTP_REASONS.get(status, f'http_{status}')
    operation_logger.log(
        logging.ERROR if status >= 500 or reason == 'ocr_unavailable' else logging.INFO,
        '%s_failed reason=%s record=%s',
        operation, reason, request.environ.get('swu_tic.form_record', '-'),
    )
    return response
