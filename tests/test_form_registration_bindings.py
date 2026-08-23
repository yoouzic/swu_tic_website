# -*- coding: utf-8 -*-
"""Tests for logical registration binding semantics (Phase 2A.3-P1)."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import CourseRegistration, LectureForm, User, db
from app.services.form_bindings import (
    get_registration_logical_form_counts,
    registration_has_form_binding,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

_BASE = datetime(2026, 1, 1, 12, 0, 0)


def _form(id_value, unique_id, registration_id=None):
    return LectureForm(
        id=id_value,
        unique_id=unique_id,
        registration_id=registration_id,
        listener_name='L',
        listener_number='A1',
        lecture_date='2026-01-01',
        class_period='3-4',
        lecture_location='A101',
        teacher_name='T',
        teacher_college='C',
        course_title='C',
        student_grade_class='G',
        teaching_method='M',
        classroom_discipline='D',
        classroom_atmosphere='A',
        courseware_quality='Q',
        overall_effect='E',
        quality_case='Case',
        course_feedback='F',
        suggestions='S',
        student_signature1='sig',
        contact_phone1='123',
        created_at=_BASE,
        updated_at=_BASE,
    )


class FormRegistrationBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='form-bindings-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'bindings-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.user = User(
            number='U1', department='D', name='U', gender='-', grade='-',
            college='C', major='-', dormitory='-', phone='-', qq='-',
            student_id='s1', password_hash=generate_password_hash('p'),
            role='信息员', group='G', is_active=True,
        )
        db.session.add(self.user)
        db.session.flush()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _registration(self):
        reg = CourseRegistration(
            course_code='C1', selection_code='S1', user_id=self.user.id,
        )
        db.session.add(reg)
        db.session.flush()
        return reg

    def test_one_form_one_version_logical_count_is_one(self):
        reg = self._registration()
        db.session.add(_form(100, 100, reg.id))
        db.session.commit()
        self.assertEqual(
            get_registration_logical_form_counts([reg.id])[reg.id], 1)

    def test_one_form_three_versions_logical_count_is_one(self):
        reg = self._registration()
        db.session.add_all([
            _form(100, 100, reg.id),
            _form(101, 100, reg.id),
            _form(102, 100, reg.id),
        ])
        db.session.commit()
        self.assertEqual(
            get_registration_logical_form_counts([reg.id])[reg.id], 1)

    def test_two_logical_forms_count_is_two(self):
        reg = self._registration()
        db.session.add_all([
            _form(100, 100, reg.id),
            _form(101, 101, reg.id),
        ])
        db.session.commit()
        self.assertEqual(
            get_registration_logical_form_counts([reg.id])[reg.id], 2)

    def test_legacy_base_and_child_count_as_one(self):
        reg = self._registration()
        db.session.add_all([
            _form(100, None, reg.id),
            _form(101, 100, reg.id),
        ])
        db.session.commit()
        self.assertEqual(
            get_registration_logical_form_counts([reg.id])[reg.id], 1)

    def test_multiple_registrations_mapping_does_not_cross(self):
        reg1 = self._registration()
        reg2 = self._registration()
        db.session.add_all([
            _form(100, 100, reg1.id),
            _form(101, 101, reg1.id),
            _form(102, 102, reg2.id),
        ])
        db.session.commit()
        counts = get_registration_logical_form_counts([reg1.id, reg2.id])
        self.assertEqual(counts[reg1.id], 2)
        self.assertEqual(counts[reg2.id], 1)

    def test_no_binding_returns_zero_through_get(self):
        reg = self._registration()
        db.session.commit()
        self.assertEqual(
            get_registration_logical_form_counts([reg.id]).get(reg.id, 0), 0)

    def test_empty_registration_ids_returns_empty(self):
        self.assertEqual(get_registration_logical_form_counts([]), {})

    def test_registration_has_form_binding_true_and_false(self):
        reg1 = self._registration()
        reg2 = self._registration()
        db.session.add(_form(100, 100, reg1.id))
        db.session.commit()
        self.assertTrue(registration_has_form_binding(reg1.id))
        self.assertFalse(registration_has_form_binding(reg2.id))


if __name__ == '__main__':
    unittest.main()
