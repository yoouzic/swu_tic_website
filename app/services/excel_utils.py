# -*- coding: utf-8 -*-
"""Pure Excel/worksheet helpers without Flask or HTTP dependencies."""
import pandas as pd


XLSX_MIMETYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


ALLOWED_EXTENSIONS = {'xlsx', 'xls'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def excel_cell_to_text(value):
    if value is None or pd.isna(value):
        return ''
    text = str(value).strip()
    if text.lower() == 'nan':
        return ''
    return text


def to_int_or_none(value):
    text = excel_cell_to_text(value)
    if not text:
        return None
    if text.endswith('.0'):
        text = text[:-2]
    try:
        return int(text)
    except Exception:
        return None


def build_export_filename(filename_title, default_title):
    title = (filename_title or '').strip() or default_title
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        title = title.replace(char, '_')
    title = title.strip().strip('.')
    if not title:
        title = default_title
    if not title.lower().endswith('.xlsx'):
        title = f'{title}.xlsx'
    return title


def autosize_worksheet(ws, min_width=12, max_width=40):
    for column_cells in ws.columns:
        max_length = 0
        column_letter = column_cells[0].column_letter
        for cell in column_cells:
            value = '' if cell.value is None else str(cell.value)
            if len(value) > max_length:
                max_length = len(value)
        ws.column_dimensions[column_letter].width = max(min_width, min(max_length + 2, max_width))
