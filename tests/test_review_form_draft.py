import json
import os
import tempfile
import unittest

from werkzeug.security import generate_password_hash


TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'review_form_draft_test.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.models import db, LectureForm, LectureFormDraft, User
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


SUPER_ADMIN = '\u8d85\u7ea7\u7ba1\u7406\u5458'
INFO_MEMBER = '\u4fe1\u606f\u5458'


class ReviewFormDraftTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.reviewer = self._create_user('9001', 'reviewer-1', SUPER_ADMIN)
        self.other_reviewer = self._create_user('9002', 'reviewer-2', SUPER_ADMIN)
        self.listener = self._create_user('1001', 'listener-1', INFO_MEMBER)
        self.form = self._create_form(self.listener)

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _create_user(self, number, student_id, role):
        user = User(
            number=number,
            department='办公部',
            name=f'User {number}',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=role,
            group='一组',
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _create_form(self, listener):
        form = LectureForm(
            listener_name=f'{listener.name}（{listener.college}）',
            listener_number=listener.number,
            course_changes='无',
            lecture_date='2026/06/04星期四',
            class_period='第3-4节',
            lecture_location='32-302',
            teacher_name='Teacher A',
            teacher_college='Test College',
            course_title='Database Systems',
            student_grade_class='2024 Test Class',
            abnormal_situation='无',
            teaching_method='PPT演示法',
            classroom_discipline='好',
            classroom_atmosphere='好',
            courseware_quality='好',
            overall_effect='好',
            quality_case='推荐',
            course_feedback='This course feedback is intentionally long enough for testing.',
            suggestions='无',
            student_signature1='Student One',
            contact_phone1='13800000001',
            student_signature2='Student Two',
            contact_phone2='13800000002',
            status='待审核',
            audit_tag='需要人工审核',
        )
        db.session.add(form)
        db.session.flush()
        form.unique_id = form.id
        db.session.commit()
        return form

    def _login_as(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _review_payload(self):
        return {
            'form_data': {
                'listener_name': self.form.listener_name,
                'listener_number': self.form.listener_number,
                'course_changes': '无',
                'lecture_date': self.form.lecture_date,
                'class_period': self.form.class_period,
                'lecture_location': self.form.lecture_location,
                'teacher_name': self.form.teacher_name,
                'teacher_college': self.form.teacher_college,
                'course_title': 'Reviewed Database Systems',
                'student_grade_class': self.form.student_grade_class,
                'abnormal_situation': self.form.abnormal_situation,
                'teaching_method': self.form.teaching_method,
                'classroom_discipline': self.form.classroom_discipline,
                'classroom_atmosphere': self.form.classroom_atmosphere,
                'courseware_quality': self.form.courseware_quality,
                'overall_effect': self.form.overall_effect,
                'quality_case': self.form.quality_case,
                'course_feedback': self.form.course_feedback,
                'suggestions': self.form.suggestions,
                'student_signature1': self.form.student_signature1,
                'contact_phone1': self.form.contact_phone1,
                'student_signature2': self.form.student_signature2,
                'contact_phone2': self.form.contact_phone2,
            },
            'review_comment': 'Draft review comment',
            'score_data': [
                {
                    'reason': '课程名称修正',
                    'department_score': 1,
                    'personal_score': 0.5,
                    'is_auto': True,
                }
            ],
        }

    def test_save_load_and_delete_review_draft(self):
        self._login_as(self.reviewer)
        payload = self._review_payload()

        save_response = self.client.put(f'/admin/api/review/form/{self.form.id}/draft', json=payload)
        self.assertEqual(save_response.status_code, 200)
        self.assertTrue(save_response.get_json()['success'])

        load_response = self.client.get(f'/admin/api/review/form/{self.form.id}/draft')
        load_json = load_response.get_json()
        self.assertEqual(load_response.status_code, 200)
        self.assertTrue(load_json['exists'])
        self.assertEqual(load_json['data'], payload)
        self.assertIn('updated_at', load_json)

        delete_response = self.client.delete(f'/admin/api/review/form/{self.form.id}/draft')
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.get_json()['deleted'])
        self.assertFalse(self.client.get(f'/admin/api/review/form/{self.form.id}/draft').get_json()['exists'])

    def test_review_draft_is_scoped_to_reviewer_and_form(self):
        self._login_as(self.reviewer)
        payload = self._review_payload()
        self.client.put(f'/admin/api/review/form/{self.form.id}/draft', json=payload)

        self._login_as(self.other_reviewer)
        other_response = self.client.get(f'/admin/api/review/form/{self.form.id}/draft')
        self.assertEqual(other_response.status_code, 200)
        self.assertFalse(other_response.get_json()['exists'])

        self._login_as(self.reviewer)
        other_form = self._create_form(self.listener)
        form_response = self.client.get(f'/admin/api/review/form/{other_form.id}/draft')
        self.assertEqual(form_response.status_code, 200)
        self.assertFalse(form_response.get_json()['exists'])

    def test_submit_review_clears_current_review_draft(self):
        self._login_as(self.reviewer)
        payload = self._review_payload()
        self.client.put(f'/admin/api/review/form/{self.form.id}/draft', json=payload)
        self.assertEqual(LectureFormDraft.query.count(), 1)

        # A super administrator is a final reviewer; the production status gate
        # requires a department-approved source before the final submission.
        self.form.status = '部门已审核'
        db.session.commit()
        submit_response = self.client.post(f'/admin/api/review/submit/{self.form.id}', json=payload)

        self.assertEqual(submit_response.status_code, 200)
        self.assertTrue(submit_response.get_json()['success'])
        self.assertEqual(LectureFormDraft.query.count(), 0)

    def test_review_page_without_permission_redirects_instead_of_500(self):
        self._login_as(self.listener)

        response = self.client.get(f'/admin/review/form/{self.form.id}')

        self.assertEqual(response.status_code, 302)

    def test_review_page_contains_draft_autosave_ui(self):
        self._login_as(self.reviewer)

        response = self.client.get(f'/admin/review/form/{self.form.id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="reviewDraftStatus"', html)
        self.assertIn('/admin/api/review/form/${formId}/draft', html)
        self.assertIn('loadReviewFormDraft();', html)
        self.assertIn('scheduleReviewFormDraftSave', html)


if __name__ == '__main__':
    unittest.main()
