"""Submission revocation is explicit, transactional and preserves owner data."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from app.app import create_app
from app.models import (
    AssessmentOverride, CourseRegistration, Department, LectureForm,
    LectureFormDraft, LectureSiteCapture, Permission, RolePermission,
    ScoreItem, ScoreRecord, SystemSetting, User, db,
)


@pytest.fixture
def lifecycle(tmp_path):
    test_app = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'lifecycle.sqlite'}",
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
        'AUTOMATION_UPLOAD_DIR': str(tmp_path / 'automation'),
    })
    with test_app.app_context():
        db.create_all()
        department = Department(name='Lifecycle Department')
        fill = Permission(name='填表', description='submit')
        review = Permission(name='审表_部门', description='review')
        db.session.add_all([department, fill, review])
        db.session.flush()

        def user(number, role):
            result = User(
                number=number, student_id=number, name=f'User {number}',
                role=role, department=department.name, group='未分配小组',
                gender='-', grade='-', college='Test College', major='-',
                dormitory='-', phone='13800000001', qq='-', is_active=True,
                password_hash=generate_password_hash('password'),
            )
            db.session.add(result)
            db.session.flush()
            return result

        actor = user('LIFE-SUPER', '超级管理员')
        target = user('LIFE-MANAGER', '管理员')
        db.session.add(RolePermission(role='管理员', permission_id=fill.id))
        db.session.add(RolePermission(role='管理员', permission_id=review.id))
        db.session.commit()
        client = test_app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=actor.id, user_role=actor.role)
        yield SimpleNamespace(
            app=test_app, client=client, actor=actor, target=target,
            fill=fill, review=review,
        )
        db.session.remove()
        db.engine.dispose()


def _marker(user_id):
    return SystemSetting.query.filter_by(
        key=f'submission_permission_revoked_{user_id}',
    ).first()


def _revoke_directly(user_id):
    db.session.add(SystemSetting(
        key=f'submission_permission_revoked_{user_id}', value='1',
    ))
    db.session.commit()


def _grant_personal(case, *permissions):
    for permission in permissions:
        db.session.add(RolePermission(
            role=f'特殊角色_{case.target.id}', permission_id=permission.id,
        ))
    db.session.commit()


def _permissions(case, permission_ids):
    return case.client.put(
        f'/admin/api/users/{case.target.id}/permissions',
        json={'permission_ids': permission_ids},
    )


def _readback(case):
    response = case.client.get(f'/admin/api/users/{case.target.id}/permissions')
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['user_permissions']


def _can_submit(case):
    from app.utils.submission_permissions import can_submit_lecture_form
    return can_submit_lecture_form(db.session.get(User, case.target.id))


def _seed_history(case):
    registration = CourseRegistration(
        user_id=case.target.id, course_code='C-LIFE', selection_code='S-LIFE',
        listening_info='preserved registration', is_used=True,
    )
    db.session.add(registration)
    db.session.flush()
    common = dict(
        listener_name=case.target.name, listener_number=case.target.number,
        lecture_date='2026/09/28', class_period='第1-2节',
        lecture_location='32-101', teacher_name='Test Teacher',
        teacher_college='Test College', course_title='Test Course',
        student_grade_class='Test Class', teaching_method='PPT演示法',
        classroom_discipline='好', classroom_atmosphere='好',
        courseware_quality='好', overall_effect='好', quality_case='无',
        course_feedback='该老师反馈保留', student_signature1='Test Student',
        contact_phone1='13800000002', registration_id=registration.id,
    )
    base = LectureForm(**common, status='待审核')
    db.session.add(base)
    db.session.flush()
    latest = LectureForm(**common, unique_id=base.id, status='中心已审核')
    db.session.add(latest)
    db.session.flush()
    score = ScoreRecord(form_id=latest.id, reviewer_id=case.actor.id,
                        total_department_score=-1, total_personal_score=-2)
    db.session.add(score)
    db.session.flush()
    db.session.add_all([
        ScoreItem(score_record_id=score.id, reason='preserved score', personal_score=-2),
        LectureFormDraft(user_id=case.target.id, payload_json='{"course_title":"draft"}'),
        LectureSiteCapture(user_id=case.target.id, photo_key='lifecycle-photo',
                           received_at='2026-09-28T10:00:00', form_id=latest.id,
                           draft_json='{"room":"32-101"}'),
        AssessmentOverride(user_id=case.target.id, start_week=1, end_week=1,
                           override_type='leave', reason='preserved leave',
                           created_by=case.actor.id,
                           override_value=json.dumps({'makeup_forms': [{'form_id': latest.id}]})),
    ])
    db.session.commit()


def _history_snapshot():
    snapshot = {}
    for model in (
        LectureForm, CourseRegistration, LectureFormDraft, LectureSiteCapture,
        ScoreRecord, ScoreItem, AssessmentOverride,
    ):
        table = model.__table__
        rows = db.session.execute(db.select(table).order_by(table.c.id)).all()
        snapshot[table.name] = [tuple(row) for row in rows]
    return snapshot


def test_clearing_permissions_cannot_restore_inherited_submission(lifecycle):
    case = lifecycle
    _grant_personal(case, case.fill)
    response = _permissions(case, [])
    assert response.status_code == 200
    assert _marker(case.target.id) is not None
    assert _marker(case.target.id).value == '1'
    assert case.fill.id not in _readback(case)
    assert case.review.id in _readback(case)
    assert not _can_submit(case)


def test_omitting_fill_revokes_without_changing_selected_review(lifecycle):
    case = lifecycle
    _grant_personal(case, case.fill, case.review)
    response = _permissions(case, [case.review.id])
    assert response.status_code == 200
    assert _marker(case.target.id) is not None
    assert _readback(case) == [case.review.id]
    assert not _can_submit(case)


def test_explicit_regrant_clears_revocation(lifecycle):
    case = lifecycle
    _revoke_directly(case.target.id)
    response = _permissions(case, [case.fill.id, case.review.id])
    assert response.status_code == 200
    assert _marker(case.target.id) is None
    assert set(_readback(case)) == {case.fill.id, case.review.id}
    assert _can_submit(case)


@pytest.mark.parametrize('personal', [False, True])
def test_revocation_is_hidden_from_effective_permission_readback(lifecycle, personal):
    case = lifecycle
    if personal:
        _grant_personal(case, case.fill, case.review)
    _revoke_directly(case.target.id)
    assert case.fill.id not in _readback(case)
    assert case.review.id in _readback(case)


def test_role_round_trip_does_not_restore_personal_or_default_submission(lifecycle):
    case = lifecycle
    _grant_personal(case, case.fill, case.review)
    _seed_history(case)
    before = _history_snapshot()
    for role in ('超级管理员', '管理员'):
        response = case.client.put(
            f'/admin/api/users/{case.target.id}', json={'role': role},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        assert _marker(case.target.id) is not None
        special_ids = {
            row.permission_id for row in RolePermission.query.filter_by(
                role=f'特殊角色_{case.target.id}',
            ).all()
        }
        assert special_ids == {case.review.id}
        assert not _can_submit(case)
        assert _history_snapshot() == before
    assert case.fill.id not in _readback(case)


def test_same_role_profile_update_keeps_grant_and_history(lifecycle):
    case = lifecycle
    _grant_personal(case, case.fill, case.review)
    _seed_history(case)
    before = _history_snapshot()
    response = case.client.put(
        f'/admin/api/users/{case.target.id}',
        json={'role': '管理员', 'phone': '13800000003'},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert _marker(case.target.id) is None
    assert set(_readback(case)) == {case.fill.id, case.review.id}
    assert _history_snapshot() == before
    assert db.session.get(User, case.target.id).number == 'LIFE-MANAGER'


def test_permission_update_failure_rolls_back_grants_and_revocation(lifecycle):
    case = lifecycle
    _grant_personal(case, case.fill)
    with patch.object(db.session, 'commit', side_effect=RuntimeError('commit failed')):
        response = _permissions(case, [case.review.id])
    assert response.status_code == 500
    assert _marker(case.target.id) is None
    assert _readback(case) == [case.fill.id]


def test_physical_deletion_removes_revocation_marker(lifecycle):
    case = lifecycle
    target_id = case.target.id
    _revoke_directly(target_id)
    response = case.client.delete(
        f'/admin/api/users/{target_id}', json={'password': 'password'},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert db.session.get(User, target_id) is None
    assert _marker(target_id) is None


def test_blocked_physical_deletion_keeps_revocation_and_history(lifecycle):
    case = lifecycle
    _revoke_directly(case.target.id)
    _seed_history(case)
    before = _history_snapshot()
    response = case.client.delete(
        f'/admin/api/users/{case.target.id}', json={'password': 'password'},
    )
    assert response.status_code == 400
    assert _marker(case.target.id) is not None
    assert _history_snapshot() == before
