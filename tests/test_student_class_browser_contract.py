"""Fresh form rendering must leave the editable class name to the user."""
import re

from app.app import app
from app.models import User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


def test_fresh_form_has_no_automatic_class_formatter_or_initial_edit_restore(tmp_path):
    configure_sqlite_database(app, db, tmp_path / 'fresh-class.sqlite')
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        try:
            user = User(number='CLASS-FRESH', student_id='class-fresh', name='测试信息员',
                        role='信息员', department='测试部门', group='测试组', gender='-',
                        grade='-', college='测试学院', major='-', dormitory='-',
                        phone='-', qq='-', password_hash='unused', is_active=True)
            db.session.add(user)
            db.session.commit()
            client = app.test_client()
            with client.session_transaction() as session:
                session.update(user_id=user.id, user_role=user.role, user_name=user.name)
            response = client.get('/user/submit_form')
            assert response.status_code == 200
            html = response.get_data(as_text=True)
            field = re.search(r'<input\b[^>]*\bid="student_grade_class"[^>]*>', html).group(0)
            assert 'autocomplete="off"' in field
            assert 'data-format-url' not in field
            assert 'student-grade-class-input.js' not in html
            assert 'class-input-diagnostics.js' not in html
            assert 'var initialFormData' not in html
            assert 'feedback-text.js' in html
            assert 'form-completion-assistant.js' in html
            traced = client.get('/user/submit_form?class_trace=1')
            assert traced.status_code == 200
            assert 'class-input-diagnostics.js?v=20261107-1' in traced.get_data(as_text=True)
        finally:
            cleanup_sqlite_database(db, drop_all=True)
