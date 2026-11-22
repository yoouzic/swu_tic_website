# -*- coding: utf-8 -*-
"""Shared workbook / Excel HTTP infrastructure helpers.

``workbook_response`` is intentionally the only helper in this module with a
Flask dependency: it adapts an in-memory openpyxl workbook to a Flask download
response.  Pure filename/column-width helpers live in ``excel_utils`` so
calculation/domain services can use them without importing Flask.
"""
from io import BytesIO

from flask import has_request_context, send_file

from app.services.excel_utils import (
    XLSX_MIMETYPE,
    autosize_worksheet,
    build_export_filename,
)
from app.utils.operation_log import mark_form_failure, private_failure_operation


def workbook_response(wb, filename):
    output = BytesIO()
    try:
        wb.save(output)
    except Exception:
        if has_request_context() and private_failure_operation() == 'export':
            mark_form_failure('workbook_serialize_failed')
        raise
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype=XLSX_MIMETYPE
    )


# Private aliases kept for callers that historically imported the underscored
# names from assessment_stats; new code should use the public names.
_build_export_filename = build_export_filename
_autosize_worksheet = autosize_worksheet
_workbook_response = workbook_response
