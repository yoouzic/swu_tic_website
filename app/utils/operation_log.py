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
_PHOTO_WRITE_OPERATIONS = {
    ('user.site_capture_upload', 'POST'): 'photo_upload',
    ('user.site_capture_ocr_region', 'POST'): 'photo_ocr',
}

_PHOTO_HTTP_REASONS = {
    404: 'capture_unavailable',
    413: 'photo_too_large',
}


def form_write_operation():
    return _FORM_WRITE_OPERATIONS.get((request.endpoint, request.method))


def photo_write_operation():
    return request.environ.get('swu_tic.photo_operation') or _PHOTO_WRITE_OPERATIONS.get(
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


def log_photo_write_failure(response):
    operation = photo_write_operation()
    failure = request.environ.get('swu_tic.form_failure')
    status = response.status_code
    if operation is None or (failure is None and status < 400):
        return response

    reason = failure[0] if failure else _PHOTO_HTTP_REASONS.get(status, f'http_{status}')
    operation_logger.log(
        logging.ERROR if status >= 500 or reason == 'ocr_unavailable' else logging.INFO,
        '%s_failed reason=%s record=%s',
        operation, reason, request.environ.get('swu_tic.form_record', '-'),
    )
    return response
