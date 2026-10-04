"""Submission capability must not inherit the super-admin management bypass."""
import importlib
import os
import tempfile

import pytest

_BOOTSTRAP_DIR = tempfile.TemporaryDirectory(prefix='submission-policy-bootstrap-')
os.environ['INSTANCE_DIR'] = _BOOTSTRAP_DIR.name
os.environ['SQLITE_DB_PATH'] = os.path.join(_BOOTSTRAP_DIR.name, 'bootstrap.sqlite')
os.environ['SECRET_KEY'] = 'submission-policy-tests'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import create_app
from app.models import Permission, RolePermission, SystemSetting, User, db


@pytest.fixture
def policy_app(tmp_path):
    application = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'policy.sqlite'}",
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
        'AUTOMATION_UPLOAD_DIR': str(tmp_path / 'automation'),
    })
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def make_user(number='P001', role='信息员', active=True):
    user = User(number=number, student_id=number, department='测试部', name=number,
                gender='-', grade='-', college='-', major='-', dormitory='-',
                phone='-', qq='-', password_hash='unused', role=role,
                group='测试组', is_active=active)
    db.session.add(user)
    db.session.flush()
    return user


def grant(user, name='填表', *, default=False):
    permission = Permission.query.filter_by(name=name).first()
    if permission is None:
        permission = Permission(name=name)
        db.session.add(permission)
        db.session.flush()
    db.session.add(RolePermission(role=user.role if default else f'特殊角色_{user.id}',
                                  permission_id=permission.id))
    db.session.flush()


def policy():
    return importlib.import_module('app.utils.submission_permissions')


@pytest.mark.parametrize('role,allowed', [
    ('信息员', True), ('管理员', False), ('超级管理员', False), ('教师', False),
])
def test_role_defaults_do_not_imply_management_users_can_fill(policy_app, role, allowed):
    assert policy().can_submit_lecture_form(make_user(role=role)) is allowed


@pytest.mark.parametrize('role,allowed', [('管理员', True), ('超级管理员', False)])
def test_explicit_fill_permission_only_enables_manager(policy_app, role, allowed):
    user = make_user(role=role)
    grant(user)
    assert policy().can_submit_lecture_form(user) is allowed


def test_personal_override_does_not_inherit_role_fill_permission(policy_app):
    user = make_user(role='管理员')
    grant(user, default=True)
    grant(user, '审表_部门')
    assert not policy().can_submit_lecture_form(user)


def test_role_default_grant_remains_compatible(policy_app):
    user = make_user(role='管理员')
    grant(user, default=True)
    assert policy().can_submit_lecture_form(user)


def test_explicit_revocation_beats_defaults_until_regrant(policy_app):
    user = make_user(role='管理员')
    grant(user, default=True)
    policy().set_submission_permission_revoked(user.id, True)
    assert policy().submission_permission_revoked(user.id)
    assert not policy().can_submit_lecture_form(user)
    policy().set_submission_permission_revoked(user.id, False)
    assert policy().can_submit_lecture_form(user)


def test_revocation_is_part_of_callers_transaction(policy_app):
    user = make_user(role='管理员')
    db.session.commit()
    policy().set_submission_permission_revoked(user.id, True)
    db.session.rollback()
    assert not policy().submission_permission_revoked(user.id)
    assert SystemSetting.query.count() == 0


def test_explicit_inactive_is_denied_and_legacy_null_is_active(policy_app):
    user = make_user(active=False)
    assert not policy().can_submit_lecture_form(user)
    user.is_active = None
    assert policy().can_submit_lecture_form(user)
    assert not policy().can_submit_lecture_form(None)


def protected_routes(application, writes):
    from app.security import submission_required

    @application.route('/user/api/policy-probe', methods=['GET', 'PUT', 'DELETE'])
    @submission_required(api=True, methods=('PUT', 'DELETE'))
    def api_probe():
        from flask import request
        if request.method != 'GET':
            writes.append(request.method)
        return {'success': True}

    @application.route('/submission-policy-probe')
    @submission_required()
    def html_probe():
        writes.append('HTML')
        return 'allowed'


def login(client, user, session_role=None):
    with client.session_transaction() as session:
        session['user_id'] = user.id
        session['user_role'] = session_role or user.role


def test_guard_uses_database_role_and_preserves_authenticated_reads(policy_app):
    user = make_user(role='超级管理员')
    db.session.commit()
    writes = []
    protected_routes(policy_app, writes)
    client = policy_app.test_client()
    login(client, user, '信息员')
    assert client.get('/user/api/policy-probe').status_code == 200
    for method in ('PUT', 'DELETE'):
        response = client.open('/user/api/policy-probe', method=method, json={})
        assert response.status_code == 403
        assert set(response.get_json()) == {'success', 'data', 'message'}
        assert response.get_json()['success'] is False
    response = client.get('/submission-policy-probe')
    assert response.status_code == 302
    assert response.location == '/'
    assert writes == []


def test_guard_read_method_still_requires_login(policy_app):
    protected_routes(policy_app, [])
    response = policy_app.test_client().get('/user/api/policy-probe')
    assert response.status_code == 401
    assert response.is_json


def test_guard_checks_revocation_again_on_each_request(policy_app):
    user = make_user(role='管理员')
    grant(user)
    db.session.commit()
    writes = []
    protected_routes(policy_app, writes)
    client = policy_app.test_client()
    login(client, user)
    assert client.put('/user/api/policy-probe', json={}).status_code == 200
    policy().set_submission_permission_revoked(user.id, True)
    db.session.commit()
    assert client.put('/user/api/policy-probe', json={}).status_code == 403
    assert writes == ['PUT']


@pytest.mark.parametrize('allowed', [False, True])
def test_leave_reminder_submission_link_follows_capability(policy_app, allowed):
    with policy_app.test_request_context('/'):
        rendered = policy_app.jinja_env.get_template('main/_leave_reminder_modal.html').render(
            leave_prompt={'status_label': '待补交', 'leave_week': 2,
                          'pending_makeup_count': 1, 'user_id': 1, 'status': 'pending'},
            app_can_submit_lecture_form=allowed,
        )
    assert ('去交表' in rendered) is allowed


def test_query_only_role_home_does_not_fall_back_to_global_management(policy_app):
    from app.ui.workspace import load_workspace_snapshot
    user = make_user(role='教师')
    db.session.commit()
    snapshot = load_workspace_snapshot(user)
    assert snapshot.total_users == 0
    client = policy_app.test_client()
    login(client, user)
    html = client.get('/').get_data(as_text=True)
    assert 'data-workspace-role="course-lookup"' in html
    assert '/admin/review_forms' not in html
    assert '/admin/system_management' not in html
