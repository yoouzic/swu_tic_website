"""End-to-end workbook integrity across export modes and scope selection."""
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.blueprints.admin.forms_io import _sort_export_forms
from app.models import SystemSetting, db
from tests.test_form_export_latest_versions import add_form, export_app, login_admin


@pytest.mark.parametrize('mode', ['standard', 'all_data'])
@pytest.mark.parametrize('scope', ['all', 'selected', 'latest'])
def test_export_scope_mode_matrix_preserves_all_distinct_final_forms(export_app, mode, scope):
    previous = add_form(course='此前已导出')
    SystemSetting.set('last_exported_form_id', str(previous.id))
    original = add_form(status='已驳回', course='甲旧版')
    final = add_form(unique_id=original.id, course='甲最终版')
    extra = add_form(status='中心已审核', course='同周额外听课')
    stale_approval = add_form(course='应排除的旧审核')
    add_form(unique_id=stale_approval.id, status='待审核', course='仍待审核')
    legacy_null = add_form(legacy=True, course='旧数据空标识')
    legacy_zero = add_form(unique_id=0, course='旧数据零标识')
    final.listener_number = '000000210101'
    final.lecture_date = '2026/11/05星期四'
    final.student_grade_class = '2026级数学（师范）03、04班'
    db.session.commit()
    expected = [final, extra, legacy_null, legacy_zero]
    if scope == 'all':
        expected = [previous, *expected]
    elif scope == 'selected':
        expected = [final, extra]
    expected_by_title = {form.course_title: form for form in expected}
    client = export_app.test_client()
    login_admin(client)
    params = {'scope': scope, 'status_list': '部门已审核,中心已审核', 'export_mode': mode}
    if scope == 'selected':
        params['form_ids'] = f'{original.id},{final.id},{original.id},{extra.id}'
    preview = client.post('/admin/api/forms/export/check', json=params)
    assert preview.get_json()['total_count'] == len(expected)
    response = client.get('/admin/api/forms/export/download', query_string=params)
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.data), read_only=True)
    try:
        rows = list(workbook.active.values)
        if mode == 'all_data':
            headers = rows[0]
            assert len(headers) == 33
            exported = {row[9]: row for row in rows[1:]}
            assert set(exported) == set(expected_by_title)
            for title, form in expected_by_title.items():
                row = exported[title]
                assert row[0] == form.id
                assert row[2] == form.listener_number
                assert row[4] == form.lecture_date
                assert row[10] == form.student_grade_class
                assert row[24] == form.status
        else:
            assert len(rows[1]) == 12
            exported = {row[7]: row for row in rows[2:]}
            assert set(exported) == set(expected_by_title)
            for title, form in expected_by_title.items():
                row = exported[title]
                assert row[1] == form.listener_number
                assert row[2] == form.lecture_date
                assert row[8] == form.student_grade_class
        assert len(exported) == len(expected)
    finally:
        workbook.close()
    expected_cursor = max(form.id for form in expected) if scope == 'latest' else previous.id
    assert int(SystemSetting.get('last_exported_form_id')) == expected_cursor


@pytest.mark.parametrize('mode,cell', [('standard', 'K3'), ('all_data', 'S2')])
@pytest.mark.parametrize('text', ['=1+1', '+SUM(A1:A2)', '-1+1', '@SUM(A1:A2)', '#N/A', '#REF!', '普通课堂记录'])
def test_formula_like_feedback_is_exported_as_literal_text(export_app, mode, cell, text):
    form = add_form()
    form.course_feedback = text
    db.session.commit()
    client = export_app.test_client()
    login_admin(client)
    response = client.get('/admin/api/forms/export/download', query_string={
        'scope': 'all', 'export_mode': mode,
    })
    workbook = load_workbook(BytesIO(response.data), read_only=True, data_only=False)
    try:
        assert workbook.active[cell].value == form.course_feedback
        assert workbook.active[cell].data_type == 's'
        assert workbook.active[cell].number_format == '@'
    finally:
        workbook.close()


def test_time_sort_parses_legacy_dates_and_places_invalid_values_last():
    forms = [
        SimpleNamespace(id=1, lecture_date='2026/10/30星期五'),
        SimpleNamespace(id=2, lecture_date='2026-11-02'),
        SimpleNamespace(id=3, lecture_date='2026年11月1日'),
        SimpleNamespace(id=4, lecture_date='日期待确认'),
    ]
    _sort_export_forms(forms, 'time')
    assert [form.id for form in forms] == [2, 3, 1, 4]


@pytest.mark.parametrize('mode,cell', [('standard', 'K3'), ('all_data', 'S2')])
def test_multiline_feedback_preserves_content_and_intentional_blank_lines(export_app, mode, cell):
    form = add_form()
    form.course_feedback = '首行\r\n次行\r\n\r\n末行\r尾行\n'
    db.session.commit()
    client = export_app.test_client()
    login_admin(client)
    response = client.get('/admin/api/forms/export/download', query_string={
        'scope': 'all', 'export_mode': mode,
    })
    workbook = load_workbook(BytesIO(response.data), read_only=True)
    try:
        assert workbook.active[cell].value == '首行\n次行\n\n末行\n尾行\n'
        assert form.course_feedback == '首行\r\n次行\r\n\r\n末行\r尾行\n'
    finally:
        workbook.close()


def test_failed_workbook_save_does_not_advance_incremental_cursor(export_app, monkeypatch):
    previous = add_form(course='此前已导出')
    SystemSetting.set('last_exported_form_id', str(previous.id))
    add_form(course='本次待导出')
    db.session.commit()
    client = export_app.test_client()
    login_admin(client)

    def fail_save(*args, **kwargs):
        raise OSError('simulated workbook write failure')

    monkeypatch.setattr('openpyxl.Workbook.save', fail_save)
    response = client.get('/admin/api/forms/export/download', query_string={'scope': 'latest'})
    assert response.status_code == 302
    assert int(SystemSetting.get('last_exported_form_id')) == previous.id


def test_long_revision_chain_keeps_only_final_row_and_separate_extra_lecture(export_app):
    original = add_form(legacy=True, status='待审核')
    versions = [original]
    for index in range(30):
        versions.append(add_form(unique_id=original.id,
                                 status=('已驳回', '待审核', '部门已审核')[index % 3]))
    final = add_form(unique_id=original.id, status='中心已审核')
    extra = add_form(course='同周第二次听课', status='中心已审核')
    db.session.commit()
    client = export_app.test_client()
    login_admin(client)
    requested_ids = [form.id for form in versions] + [extra.id]
    preview = client.post('/admin/api/forms/export/check', json={
        'scope': 'selected', 'form_ids': requested_ids,
        'status_list': ['中心已审核'],
    })
    assert preview.get_json()['total_count'] == 2
    response = client.get('/admin/api/forms/export/download', query_string={
        'scope': 'selected', 'form_ids': ','.join(map(str, requested_ids)),
        'status_list': '中心已审核', 'export_mode': 'all_data',
    })
    workbook = load_workbook(BytesIO(response.data), read_only=True)
    try:
        exported = list(workbook.active.values)[1:]
        assert {row[0] for row in exported} == {final.id, extra.id}
        assert len(exported) == 2
    finally:
        workbook.close()
