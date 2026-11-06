"""The form list filters current revisions but retains their full history."""
from datetime import timedelta

import pytest

from app.app import create_app
from app.models import User, db
from tests.test_form_export_latest_versions import add_form, login_admin


@pytest.fixture
def view_app(monkeypatch):
    app = create_app({
        'TESTING': True, 'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
    })
    with app.app_context():
        db.create_all()
        db.session.add(User(
            number='210101', department='技术部', name='测试成员', gender='', grade='',
            college='测试学院', major='', dormitory='', phone='', qq='',
            student_id='student', password_hash='unused', role='信息员', is_active=True,
        ))
        db.session.commit()
        client = app.test_client()
        login_admin(client)
        captured = {}

        def capture_template(template, **context):
            captured.clear()
            captured.update(context)
            return 'rendered'

        monkeypatch.setattr('app.blueprints.admin.forms_io.render_template', capture_template)

        def get_groups(**query):
            response = client.get('/admin/view_forms', query_string=query)
            assert response.status_code == 200
            return captured['forms'].items

        yield get_groups
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_latest_version_uses_id_and_includes_legacy_base_in_history(view_app):
    original = add_form(legacy=True)
    final = add_form(status='待审核', unique_id=original.id)
    original.updated_at = final.updated_at + timedelta(days=1)
    db.session.commit()

    groups = view_app()
    assert len(groups) == 1
    assert groups[0]['latest_form'].id == final.id
    assert [form.id for form in groups[0]['versions']] == [final.id, original.id]


def test_status_filter_never_selects_an_old_approved_revision(view_app):
    original = add_form()
    add_form(status='待审核', unique_id=original.id)
    db.session.commit()

    assert view_app(status='已审核') == []
    assert view_app(status='部门已审核') == []


def test_date_filter_uses_latest_lecture_day_with_html_date_format(view_app):
    original = add_form()
    final = add_form(unique_id=original.id)
    final.lecture_date = '2026/11/06星期五'
    db.session.commit()

    assert view_app(date='2026/11/05') == []
    groups = view_app(date='2026-11-06')
    assert [group['latest_form'].id for group in groups] == [final.id]
    assert len(groups[0]['versions']) == 2


def test_search_uses_latest_fields_without_losing_history(view_app):
    original = add_form(course='错误课程')
    final = add_form(unique_id=original.id, course='更正后的课程')
    db.session.commit()

    assert view_app(search='错误课程') == []
    groups = view_app(search='更正后的课程')
    assert [group['latest_form'].id for group in groups] == [final.id]
    assert len(groups[0]['versions']) == 2
