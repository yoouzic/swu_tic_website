"""Exports contain one final revision per listening form, not review history."""
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.app import create_app
from app.blueprints.admin.forms_io import get_export_query
from app.models import LectureForm, SystemSetting, User, db


@pytest.fixture
def export_app():
    app = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def add_form(*, status='部门已审核', unique_id=None, course='课程甲', legacy=False):
    form = LectureForm(
        listener_number='210101', listener_name='测试成员（测试学院）',
        lecture_date='2026/11/05星期四', class_period='第3-4节',
        lecture_location='32-302', teacher_name='测试教师',
        teacher_college='测试学院', course_title=course,
        student_grade_class='测试班', teaching_method='讲授法',
        classroom_discipline='良好', classroom_atmosphere='活跃',
        courseware_quality='清晰', overall_effect='良好', quality_case='否',
        course_feedback='测试反馈', student_signature1='同学甲',
        contact_phone1='13800000000', status=status, unique_id=unique_id,
        created_at=datetime(2026, 11, 5, 12),
        updated_at=datetime(2026, 11, 5, 12),
    )
    db.session.add(form)
    db.session.flush()
    if unique_id is None and not legacy:
        form.unique_id = form.id
    return form


def test_all_exports_final_revision_and_preserves_distinct_same_week_forms(export_app):
    first = add_form(status='待审核')
    rejected = add_form(status='已驳回', unique_id=first.id)
    final = add_form(unique_id=first.id)
    extra = add_form(course='课程乙')
    # Editing a historical row must not make it the final revision.
    rejected.updated_at = final.updated_at + timedelta(days=1)
    db.session.commit()

    assert {form.id for form in get_export_query('all').all()} == {final.id, extra.id}


def test_status_filter_does_not_resurrect_historical_approval(export_app):
    approved = add_form()
    pending = add_form(status='待审核', unique_id=approved.id)
    db.session.commit()

    assert get_export_query('all', ['部门已审核']).count() == 0
    assert [form.id for form in get_export_query('all', ['待审核'])] == [pending.id]


def test_selected_historical_ids_resolve_to_the_final_revision(export_app):
    original = add_form(status='已驳回', legacy=True)
    final = add_form(unique_id=original.id)
    add_form(course='未选择的课')
    db.session.commit()

    rows = get_export_query('selected', ['部门已审核'], [original.id]).all()
    assert [form.id for form in rows] == [final.id]


def test_incremental_export_only_selects_final_revision_since_watermark(export_app):
    old = add_form()
    SystemSetting.set('last_exported_form_id', str(old.id))
    rejected = add_form(status='已驳回', unique_id=old.id)
    final = add_form(unique_id=old.id)
    extra = add_form(course='课程乙')
    db.session.commit()

    assert {form.id for form in get_export_query('latest')} == {final.id, extra.id}
    assert rejected.id not in {form.id for form in get_export_query('latest')}


def login_admin(client):
    admin = User(
        number='SA001', department='技术部', name='测试管理员', gender='', grade='',
        college='', major='', dormitory='', phone='', qq='', student_id='admin',
        password_hash='unused', role='超级管理员', is_active=True,
    )
    db.session.add(admin)
    db.session.commit()
    with client.session_transaction() as session:
        session['user_id'] = admin.id


@pytest.mark.parametrize('export_mode', ['standard', 'all_data'])
def test_preflight_and_workbook_use_the_same_final_forms(export_app, export_mode):
    original = add_form(status='已驳回')
    final = add_form(unique_id=original.id)
    extra = add_form(course='课程乙')
    historical_approval = add_form(course='仍待重审的课')
    add_form(status='待审核', unique_id=historical_approval.id, course='仍待重审的课')
    db.session.commit()
    client = export_app.test_client()
    login_admin(client)

    preview = client.post('/admin/api/forms/export/check', json={
        'scope': 'all', 'status_list': ['部门已审核'],
    })
    assert preview.status_code == 200
    assert preview.get_json() == {'success': True, 'total_count': 2, 'excluded_count': 1}

    response = client.get('/admin/api/forms/export/download', query_string={
        'scope': 'all', 'status_list': '部门已审核', 'export_mode': export_mode,
    })
    assert response.status_code == 200
    wb = load_workbook(BytesIO(response.data), read_only=True)
    try:
        rows = list(wb.active.values)
        if export_mode == 'all_data':
            assert {row[0] for row in rows[1:]} == {final.id, extra.id}
        else:
            assert {row[7] for row in rows[2:]} == {'课程甲', '课程乙'}
            assert len(rows[2:]) == 2
    finally:
        wb.close()
