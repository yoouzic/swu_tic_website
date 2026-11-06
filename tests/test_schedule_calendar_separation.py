"""School timetable weeks must not move with the department assessment cycle."""
from datetime import date

import pytest

from app.app import app
from app.models import SystemSetting, User, db
from app.review_automation.tasks.review import _review_calendar_context
from app.services.assessment_calc import get_teaching_reward_settings, get_form_effective_week_no
from app.services.listening_assistant import ListeningAssistantService, _default_calendar
from app.services.listening_assistant_contracts import AssistantQuery
from app.services.teaching_calendar import teaching_week_number
from app.utils.auto_review import AutoReviewEngine
from app.utils.time_validator import TimeValidator
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.test_listening_assistant_search import raw_week_entry


@pytest.fixture
def calendar_db(tmp_path):
    configure_sqlite_database(app, db, tmp_path / 'calendar.sqlite')
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('teaching_first_week_monday', '2026-10-12')
        SystemSetting.set('teaching_week_start_day', '5')
        SystemSetting.set('teaching_total_weeks', '20')
        yield
        cleanup_sqlite_database(db, drop_all=True)


def test_school_week_ten_does_not_move_with_assessment_week_four(calendar_db):
    SystemSetting.set('schedule_first_week_monday', '2026-08-31')
    calendar = _default_calendar()
    assert teaching_week_number(date(2026, 11, 6), calendar) == 10
    settings, error = get_teaching_reward_settings()
    assert error is None
    form = type('Form', (), {'lecture_date': '2026-11-06', 'audit_tag': None})()
    assert get_form_effective_week_no(form, settings) == 4


def test_assistant_keeps_week_ten_only_course_after_assessment_cycle_reset(calendar_db):
    SystemSetting.set('schedule_first_week_monday', '2026-08-31')
    service = ListeningAssistantService(schedule_loader=lambda **kwargs: [
        raw_week_entry(1, start_week_raw='10周', weekday=5),
    ])
    result = service.search(AssistantQuery(date(2026, 11, 6), room='8-309'))
    assert len(result.candidates) == 1
    assert result.candidates[0].needs_confirmation is False


def test_registration_dates_and_review_context_use_school_calendar(calendar_db):
    SystemSetting.set('schedule_first_week_monday', '2026-08-31')
    assert TimeValidator.calculate_target_date(10, 5).date() == date(2026, 11, 6)
    assert _review_calendar_context(None, None) == ('2026-2027-1', date(2026, 8, 31))
    engine = AutoReviewEngine()
    assert engine._compute_week_from_date(date(2026, 11, 6)) == 10


def test_unset_school_calendar_preserves_existing_installations(calendar_db):
    calendar = _default_calendar()
    assert calendar.first_week_date == date(2026, 10, 12)
    assert calendar.week_start_day == 5
    assert teaching_week_number(date(2026, 11, 6), calendar) == 4


def test_invalid_explicit_school_calendar_does_not_silently_use_assessment(calendar_db):
    SystemSetting.set('schedule_first_week_monday', 'bad-date')
    assert _default_calendar() is None
    assert TimeValidator.calculate_target_date(10, 5) is None


def settings_client():
    user = User(number='CALADMIN', student_id='cal-admin', name='Calendar admin',
                role='超级管理员', department='测试部门', group='测试组',
                gender='-', grade='-', college='-', major='-', dormitory='-',
                phone='-', qq='-', password_hash='unused', is_active=True)
    db.session.add(user)
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=user.id, user_role=user.role, user_name=user.name)
    return client


def settings_payload(**overrides):
    return {'first_week_monday': '2026-10-12', 'week_start_day': 5,
            'total_weeks': 20, 'required_submission_count': 1, **overrides}


def test_settings_persist_both_calendars_and_old_clients_preserve_school_setting(calendar_db):
    client = settings_client()
    response = client.post('/admin/api/settings/teaching', json=settings_payload(
        schedule_first_week_monday='2026-08-31'))
    assert response.status_code == 200
    assert SystemSetting.get('schedule_first_week_monday') == '2026-08-31'
    data = client.get('/admin/api/settings/teaching').get_json()['data']
    assert data['schedule_first_week_monday'] == '2026-08-31'
    assert data['first_week_monday'] == '2026-10-12'
    assert client.post('/admin/api/settings/teaching', json=settings_payload()).status_code == 200
    assert SystemSetting.get('schedule_first_week_monday') == '2026-08-31'


@pytest.mark.parametrize('value', ['bad-date', '2026-09-01', 123, None])
def test_settings_reject_invalid_or_non_monday_school_dates_without_partial_write(calendar_db, value):
    client = settings_client()
    response = client.post('/admin/api/settings/teaching', json=settings_payload(
        first_week_monday='2026-10-19', schedule_first_week_monday=value))
    assert response.status_code == 400
    assert SystemSetting.get('teaching_first_week_monday') == '2026-10-12'
    assert SystemSetting.get('schedule_first_week_monday') is None


def test_settings_empty_school_date_explicitly_restores_fallback(calendar_db):
    SystemSetting.set('schedule_first_week_monday', '2026-08-31')
    client = settings_client()
    assert client.post('/admin/api/settings/teaching', json=settings_payload(
        schedule_first_week_monday='')).status_code == 200
    assert SystemSetting.get('schedule_first_week_monday') == ''
    assert teaching_week_number(date(2026, 11, 6), _default_calendar()) == 4


@pytest.mark.parametrize(('school_date', 'assessment_date', 'expected'), [
    ('2026-08-31', '', 'ready'),
    ('bad-date', '2026-10-12', 'unconfigured'),
])
def test_registration_page_uses_school_calendar_readiness(
    calendar_db, monkeypatch, school_date, assessment_date, expected,
):
    from app.blueprints.user import reservations
    SystemSetting.set('schedule_first_week_monday', school_date)
    SystemSetting.set('teaching_first_week_monday', assessment_date)
    monkeypatch.setattr(reservations, 'render_template',
                        lambda template, **context: 'ready' if context['semester_configured'] else 'unconfigured')
    response = settings_client().get('/user/listening_registration')
    assert response.status_code == 200
    assert response.get_data(as_text=True) == expected
