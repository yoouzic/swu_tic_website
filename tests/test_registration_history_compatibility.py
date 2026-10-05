"""Legacy history remains readable; selected-current history stays identity scoped."""
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase
from tests import test_schedule_availability as _schedule_fixture
from app.models import Course, CourseRegistration, SystemSetting, db
from app.services.registration_course_identity import stamp_registration_course


class RegistrationHistoryCompatibilityTest(_ReviewMutationCompatibilityBase):
    def setUp(self):
        super().setUp()
        self._login(self.officer)
        SystemSetting.set('teaching_first_week_monday', '2026-09-07')

    def history(self, **params):
        return self.client.get('/user/api/course_registration_history', query_string={'course_code':'C001','selection_code':'01',**params})

    def add_registration(self, course=None):
        row = CourseRegistration(user_id=self.officer.id, course_code='C001', selection_code='01', listening_info='第2周星期一第3-4节')
        if course:
            stamp_registration_course(row, course)
        db.session.add(row)
        db.session.commit()
        return row

    def activate(self):
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        return _schedule_fixture.ScheduleAvailabilityTest.activate(self)

    def test_legacy_history_is_readable_without_current_timetable_or_matching_course(self):
        row = self.add_registration()
        before = (row.course_id,row.identity_status,row.listening_info)
        response = self.history()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()['data']
        self.assertEqual([item['id'] for item in data], [row.id])
        self.assertIsNone(data[0]['course_id'])
        self.assertEqual((row.course_id,row.identity_status,row.listening_info),before)

    def test_legacy_history_does_not_relabel_old_same_code_record_as_current(self):
        old = Course(course_code='C001',selection_code='01',course_name='历史课程',semester='2025-2026-2',academic_year='2025-2026')
        db.session.add(old);db.session.commit()
        previous = self.add_registration(old)
        current = self.activate()
        recent = self.add_registration(current)
        response = self.history(limit=100)
        self.assertEqual(response.status_code,200)
        records = {item['id']:item for item in response.get_json()['data']}
        self.assertIn(previous.id, records)
        self.assertEqual(records[previous.id]['course_id'],old.id)
        self.assertEqual(records[previous.id]['semester'],'2025-2026-2')
        self.assertEqual(records[recent.id]['course_id'],current.id)

    def test_explicit_current_course_history_excludes_historical_same_code_rows(self):
        old = Course(course_code='C001',selection_code='01',course_name='历史课程',semester='2025-2026-2')
        db.session.add(old);db.session.commit()
        self.add_registration(old)
        current = self.activate()
        recent = self.add_registration(current)
        response = self.history(course_id=current.id)
        self.assertEqual(response.status_code,200)
        self.assertEqual([item['id'] for item in response.get_json()['data']],[recent.id])
        self.assertEqual(self.history(course_id=old.id).status_code,400)

    def test_invalid_explicit_id_never_falls_back_to_legacy_pair_history(self):
        self.add_registration()
        self.activate()
        self.assertEqual(self.history(course_id='not-an-id').status_code,400)
        self.assertEqual(self.history(course_id='999999').status_code,400)
