# -*- coding: utf-8 -*-
"""Manual assessment import/update domain service.

Contains the Excel parsing, preview building and score-record mutation logic
for the manual assessment import workflow.  HTTP file upload objects are
consumed as file-like inputs; this module does not depend on Flask or
blueprints.
"""
import pandas as pd

from app.models import LectureForm, ScoreRecord, ScoreItem, db
from app.services.excel_utils import allowed_file, to_int_or_none


def read_manual_assessment_file(file_storage):
    if not file_storage or not file_storage.filename:
        return None, '请先选择导入文件'
    if not allowed_file(file_storage.filename):
        return None, '仅支持xls或xlsx文件'
    try:
        return pd.read_excel(file_storage), None
    except Exception as e:
        return None, f'读取文件失败：{str(e)}'


def normalize_manual_assessment_items(items):
    normalized_items = []
    for item in items or []:
        if isinstance(item, dict):
            reason = item.get('reason')
            department_score = item.get('department_score')
            personal_score = item.get('personal_score')
        else:
            reason = getattr(item, 'reason', '')
            department_score = getattr(item, 'department_score', 0.0)
            personal_score = getattr(item, 'personal_score', 0.0)
        normalized_items.append({
            'reason': str(reason or '').strip() or '手动导入考评项',
            'department_score': float(department_score or 0.0),
            'personal_score': float(personal_score or 0.0)
        })
    normalized_items.sort(key=lambda item: (item['reason'], item['department_score'], item['personal_score']))
    return normalized_items


def build_score_snapshot(items, score_record=None):
    normalized_items = normalize_manual_assessment_items(items)
    if score_record:
        total_department_score = float(score_record.total_department_score or 0.0)
        total_personal_score = float(score_record.total_personal_score or 0.0)
    else:
        total_department_score = sum(item['department_score'] for item in normalized_items)
        total_personal_score = sum(item['personal_score'] for item in normalized_items)
    return {
        'total_department_score': total_department_score,
        'total_personal_score': total_personal_score,
        'items': normalized_items
    }


def build_existing_score_snapshot(form):
    if not form or not form.score_record:
        return build_score_snapshot([])
    return build_score_snapshot(form.score_record.items, score_record=form.score_record)


def manual_assessment_snapshots_equal(before_snapshot, after_snapshot):
    return (
        float(before_snapshot.get('total_department_score') or 0.0) == float(after_snapshot.get('total_department_score') or 0.0)
        and float(before_snapshot.get('total_personal_score') or 0.0) == float(after_snapshot.get('total_personal_score') or 0.0)
        and before_snapshot.get('items', []) == after_snapshot.get('items', [])
    )


def parse_manual_assessment_rows(df):
    required_columns = ['表单ID', '考评项', '部门扣分', '个人扣分']
    for col in required_columns:
        if col not in df.columns:
            return None, f'缺少字段：{col}'

    valid_row_count = 0
    skipped_count = 0
    skipped_details = []
    grouped_items = {}
    forms_by_id = {}

    for idx, row in df.iterrows():
        form_id_value = row.get('表单ID')
        reason_value = row.get('考评项')
        dept_value = row.get('部门扣分')
        pers_value = row.get('个人扣分')

        if pd.isna(form_id_value) and pd.isna(reason_value) and pd.isna(dept_value) and pd.isna(pers_value):
            continue

        form_id = to_int_or_none(form_id_value)
        if not form_id:
            skipped_count += 1
            if len(skipped_details) < 15:
                skipped_details.append(f'第{idx + 2}行：表单ID无效')
            continue

        form = LectureForm.query.get(form_id)
        if not form:
            skipped_count += 1
            if len(skipped_details) < 15:
                skipped_details.append(f'第{idx + 2}行：表单ID {form_id} 不存在')
            continue

        reason = str(reason_value).strip() if not pd.isna(reason_value) else ''
        try:
            department_score = float(dept_value) if not pd.isna(dept_value) else 0.0
            personal_score = float(pers_value) if not pd.isna(pers_value) else 0.0
        except Exception:
            skipped_count += 1
            if len(skipped_details) < 15:
                skipped_details.append(f'第{idx + 2}行：扣分字段格式错误')
            continue

        if not reason and department_score == 0 and personal_score == 0:
            skipped_count += 1
            if len(skipped_details) < 15:
                skipped_details.append(f'第{idx + 2}行：考评项和扣分不能同时为空/0')
            continue

        normalized_item = {
            'reason': reason or '手动导入考评项',
            'department_score': department_score,
            'personal_score': personal_score
        }
        valid_row_count += 1
        forms_by_id[form_id] = form
        grouped_items.setdefault(form_id, []).append(normalized_item)

    return {
        'valid_row_count': valid_row_count,
        'skipped_count': skipped_count,
        'skipped_details': skipped_details,
        'grouped_items': grouped_items,
        'forms_by_id': forms_by_id
    }, None


def build_manual_assessment_preview(parsed_result):
    changed_forms = []
    changed_grouped_items = {}
    unchanged_form_ids = []
    changed_row_count = 0

    for form_id, items in parsed_result['grouped_items'].items():
        form = parsed_result['forms_by_id'][form_id]
        before_snapshot = build_existing_score_snapshot(form)
        after_snapshot = build_score_snapshot(items)
        if manual_assessment_snapshots_equal(before_snapshot, after_snapshot):
            unchanged_form_ids.append(form_id)
            continue

        changed_grouped_items[form_id] = normalize_manual_assessment_items(items)
        changed_row_count += len(items)
        changed_forms.append({
            'form_id': form.id,
            'listener_number': form.listener_number or '',
            'teacher_name': form.teacher_name or '',
            'course_title': form.course_title or '',
            'change_type': 'replace' if form.score_record else 'create',
            'before': before_snapshot,
            'after': after_snapshot
        })

    changed_forms.sort(key=lambda item: item['form_id'])
    unchanged_form_ids.sort()
    return {
        'summary': {
            'valid_row_count': parsed_result['valid_row_count'],
            'changed_form_count': len(changed_forms),
            'changed_row_count': changed_row_count,
            'unchanged_form_count': len(unchanged_form_ids),
            'skipped_count': parsed_result['skipped_count']
        },
        'changed_forms': changed_forms,
        'changed_grouped_items': changed_grouped_items,
        'unchanged_form_ids': unchanged_form_ids,
        'skipped_details': parsed_result['skipped_details']
    }


def apply_manual_assessment_changes(changed_grouped_items, operator_id, import_time):
    imported_row_count = 0
    imported_form_count = 0
    for form_id, items in changed_grouped_items.items():
        old_record = ScoreRecord.query.filter_by(form_id=form_id).first()
        if old_record:
            db.session.delete(old_record)
            db.session.flush()

        score_snapshot = build_score_snapshot(items)
        score_record = ScoreRecord(
            form_id=form_id,
            reviewer_id=operator_id,
            total_department_score=score_snapshot['total_department_score'],
            total_personal_score=score_snapshot['total_personal_score'],
            created_at=import_time,
            updated_at=import_time
        )
        db.session.add(score_record)
        db.session.flush()

        for item in score_snapshot['items']:
            db.session.add(ScoreItem(
                score_record_id=score_record.id,
                reason=item['reason'],
                department_score=item['department_score'],
                personal_score=item['personal_score'],
                is_auto_generated=False
            ))

        form = LectureForm.query.get(form_id)
        if form:
            form.reviewer_id = operator_id
            form.review_time = import_time
            db.session.add(form)

        imported_row_count += len(score_snapshot['items'])
        imported_form_count += 1

    return imported_row_count, imported_form_count
