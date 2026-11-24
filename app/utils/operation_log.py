"""Compact, request-scoped operational events for the WSGI error stream."""

import logging

from flask import has_request_context, request, session
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
    ('admin.acknowledge_review_form_read', 'POST'): 'review',
    ('admin.review_form_draft', 'GET'): 'review',
    ('admin.review_form_draft', 'PUT'): 'review',
    ('admin.review_form_draft', 'DELETE'): 'review',
    ('admin.reject_form_review', 'POST'): 'review',
    ('admin.submit_review', 'POST'): 'review',
    ('admin.submit_form_review', 'POST'): 'review',
    ('admin.review_form_page', 'GET'): 'review',
    ('admin.get_forms_for_review', 'GET'): 'review',
    ('admin.get_form_for_review', 'GET'): 'review',
    ('admin.get_form_detail', 'GET'): 'review',
    ('admin.preview_review_form_tags', 'POST'): 'review',
    ('admin.update_review_form_tags', 'PUT'): 'review',
    ('admin.delete_form', 'DELETE'): 'review',
    ('admin.delete_form_group', 'DELETE'): 'review',
    ('admin.auto_check_form', 'POST'): 'review',
    ('admin.get_reference_data', 'POST'): 'review',
    ('admin.batch_auto_check', 'POST'): 'review',
    ('admin.batch_auto_check_preview', 'POST'): 'review',
    ('admin.get_auto_check_status', 'GET'): 'review',
    ('review_automation.preview_batches', 'POST'): 'review',
    ('review_automation.create_batch', 'POST'): 'review',
    ('review_automation.get_batch', 'GET'): 'review',
    ('review_automation.cancel_batch', 'POST'): 'review',
    ('review_automation.get_form_assessment', 'GET'): 'review',
    ('review_automation.get_assessment', 'GET'): 'review',
    ('admin.export_forms', 'GET'): 'export',
    ('admin.export_imported_forms_excel', 'GET'): 'export',
    ('admin.check_export_forms', 'POST'): 'export',
    ('admin.download_export_forms', 'GET'): 'export',
    ('admin.export_contacts', 'GET'): 'export',
    ('admin.export_progress', 'GET'): 'export',
    ('admin.download_passwords', 'GET'): 'export',
    ('admin.export_schedule', 'GET'): 'export',
    ('admin.export_statistics', 'GET'): 'export',
    ('admin.export_submission_count_stats', 'GET'): 'export',
    ('admin.export_submission_count_snapshot', 'GET'): 'export',
    ('admin.export_department_monthly_assessment_stats', 'GET'): 'export',
    ('admin.export_department_monthly_assessment_snapshot', 'GET'): 'export',
    ('admin.export_review_assessment_stats', 'GET'): 'export',
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
    method = 'GET' if request.method == 'HEAD' else request.method
    return request.environ.get('swu_tic.photo_operation') or _PRIVATE_OPERATIONS.get(
        (request.endpoint, method),
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


def log_failure_event(operation, reason, record, *, server_error=False):
    operation_logger.log(
        logging.ERROR if server_error else logging.INFO,
        '%s_failed reason=%s record=%s', operation, reason, record,
    )


def report_review_component_failure(reason):
    if has_request_context() and private_failure_operation() == 'review':
        mark_form_failure(reason)
    else:
        log_failure_event('review', reason, '-', server_error=True)


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
    elif operation in {'review', 'export'} and status == 404:
        reason = 'record_unavailable'
    elif operation == 'review' and status == 409:
        reason = 'review_conflict'
    else:
        reason = _PRIVATE_HTTP_REASONS.get(status, f'http_{status}')
    log_failure_event(
        operation, reason, request.environ.get('swu_tic.form_record', '-'),
        server_error=status >= 500 or reason == 'ocr_unavailable',
    )
    return response
