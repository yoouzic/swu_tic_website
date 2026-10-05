"""Current schedule gates are reversible and never turn history into authority."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from app.app import app
from app.models import (Course, CourseRegistration, LectureFormDraft, LectureSiteCapture, ScheduleImportBatch,
                        SystemSetting, User, db)
from app.services.current_courses import current_course_query, persist_course_mapping
from app.services.listening_assistant_schedule import persist_listening_assistant_entries
from app.services.schedule_snapshots import persist_import_snapshot
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

CURRENT = '2026-2027-1'
OLD = '2025-2026-2'


class ScheduleAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='schedule-availability-')
        configure_sqlite_database(app, db, Path(self.temp.name) / 'isolated.sqlite')
        self.capture_config = {key: app.config.get(key) for key in ('LECTURE_CAPTURE_ENABLED', 'LECTURE_CAPTURE_FOLDER')}
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, LECTURE_CAPTURE_ENABLED=True,
                          LECTURE_CAPTURE_FOLDER=str(Path(self.temp.name) / 'photos'))
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.user = User(number='S001', student_id='S001', name='测试信息员', role='信息员',
                         department='测试部', password_hash='unused', is_active=True,
                         gender='-', grade='-', college='-', major='-', dormitory='-',
                         phone='13800000000', qq='-', group='一组')
        db.session.add(self.user)
        db.session.commit()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = self.user.id
        SystemSetting.set('teaching_first_week_monday', '2026-09-07')

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        app.config.update(self.capture_config)
        self.context.pop()
        self.temp.cleanup()

    def activate(self, semester=CURRENT, *, indexed=True, mapped=True):
        df = pd.DataFrame([{'姓名':'张三', '教师所属学院':'计算机学院', '课程名称':'数据结构',
                            '星期几':1, '上课节次':'第3-4节', '场地名称':'8-309',
                            '教学班组成':'2024级计算机1班', '起始周':'1-16',
                            '学期':semester, '学年':'2026', '课程号':'C001', '选课号':'01'}])
        batches = persist_import_snapshot(df, semester + '.xlsx', semester.ljust(64, 'a'))
        if indexed:
            persist_listening_assistant_entries(df, batches)
        course = Course(course_code='C001', selection_code='01', course_name='数据结构',
                        semester=semester, class_time='星期一 第3-4节', class_location='8-309')
        db.session.add(course)
        db.session.flush()
        if mapped:
            persist_course_mapping(batches[0], [course.id])
        db.session.commit()
        return course

    def assert_hidden(self):
        page = self.client.get('/user/submit_form')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertNotIn('data-listening-assistant', html)
        self.assertNotIn('js/listening-assistant.js', html)
        self.assertNotIn('unusedRegistrationsModal', html)
        self.assertNotIn('data-site-context-guide', html)
        self.assertNotIn('data-site-find', html)
        for hidden in ('data-site-capture', 'data-site-classroom', 'data-site-manual',
                       'js/site-capture.js', 'data-site-photo'):
            self.assertNotIn(hidden, html)
        for retained in ('id="lectureForm"', 'data-form-completion', 'js/form-completion-assistant.js',
                         'id="course_title"', 'id="expected_form_id"', 'id="expected_form_updated_at"'):
            self.assertIn(retained, html)
        records = self.client.get('/user/listening_registration?tab=registration').get_data(as_text=True)
        self.assertNotIn('data-activity-tab="registration"', records)
        self.assertIn('data-active-tab="records"', records)
        self.assertIn('data-activity-tab="records"', records)
        self.assertNotIn('可以先切换到“听课登记”选择课程', records)
        self.assertNotIn('>听课登记<', self.client.get('/').get_data(as_text=True))

    def test_unset_current_semester_hides_dependent_controls(self):
        self.activate(OLD)
        SystemSetting.set('teaching_current_semester', '')
        self.assert_hidden()

    def test_current_semester_without_snapshot_hides_dependent_controls(self):
        SystemSetting.set('teaching_current_semester', CURRENT)
        self.assert_hidden()

    def test_only_historical_snapshot_never_restores_current_controls(self):
        self.activate(OLD)
        SystemSetting.set('teaching_current_semester', CURRENT)
        self.assert_hidden()

    def test_activating_current_snapshot_restores_controls(self):
        SystemSetting.set('teaching_current_semester', CURRENT)
        self.activate()
        html = self.client.get('/user/submit_form').get_data(as_text=True)
        for restored in ('data-listening-assistant', 'js/listening-assistant.js',
                         'unusedRegistrationsModal', 'data-site-context-guide', 'data-site-find',
                         'data-site-capture', 'data-site-map', 'data-site-photo', 'js/site-capture.js'):
            self.assertIn(restored, html)
        records = self.client.get('/user/listening_registration').get_data(as_text=True)
        self.assertIn('data-activity-tab="registration"', records)
        self.assertEqual(self.client.get('/user/course_lookup').status_code, 200)

    def test_old_course_only_is_not_available_for_new_registration(self):
        db.session.add(Course(course_code='OLD', selection_code='1', course_name='旧课', semester=OLD))
        db.session.commit()
        self.assertEqual(current_course_query().count(), 0)
        response = self.client.get('/user/api/available_courses')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.post('/user/api/create_reservation', json={}).status_code, 503)
        self.assertEqual(self.client.get('/user/course_lookup').status_code, 503)

    def test_old_unused_registration_is_retained_only_in_history(self):
        course = self.activate(OLD)
        SystemSetting.set('teaching_current_semester', CURRENT)
        old = CourseRegistration(user_id=self.user.id, course_code=course.course_code,
                                 selection_code=course.selection_code, listening_info='历史听课')
        db.session.add(old)
        db.session.commit()
        response = self.client.get('/user/api/unused_reservations')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['data'], [])
        self.assertEqual(CourseRegistration.query.count(), 1)
        history = self.client.get('/user/listening_registration?tab=records').get_data(as_text=True)
        self.assertIn('历史听课', history)

    def test_failed_manual_submission_preserves_owned_historical_registration_binding(self):
        SystemSetting.set('teaching_current_semester', CURRENT)
        old = CourseRegistration(user_id=self.user.id, course_code='OLD', selection_code='1',
                                 listening_info='历史听课')
        db.session.add(old)
        db.session.commit()
        response = self.client.post('/user/submit_form', data={'registration_id':str(old.id)})
        self.assertEqual(response.status_code, 400)
        self.assertIn('id="registration_id" name="registration_id" value="%s"' % old.id,
                      response.get_data(as_text=True))

    def test_missing_schedule_keeps_classroom_confirmation_but_blocks_courses(self):
        SystemSetting.set('teaching_current_semester', CURRENT)
        record = LectureSiteCapture(user_id=self.user.id, photo_key='isolated.jpg',
                                    received_at='2026-10-05T10:00:00', room_number='309',
                                    location_json='{}', ocr_alternatives_json='[]')
        db.session.add(record)
        db.session.flush()
        db.session.add(LectureFormDraft(user_id=self.user.id, draft_key='submit_form',
                                       payload_json='{"site_capture_id": %d}' % record.id))
        db.session.commit()
        response = self.client.patch('/user/api/site-capture/%d' % record.id,
                                     json={'building':'8', 'room_number':'309'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['candidates'], [])
        self.assertEqual(record.building, '8')
        self.assertEqual(self.client.get('/user/api/site-capture/%d/suggestions' % record.id).status_code, 503)
        self.assertEqual(self.client.post('/user/api/site-capture/%d/confirm' % record.id,
                                         json={'candidate_id':'old'}).status_code, 503)

    def test_snapshot_without_index_does_not_claim_candidate_readiness(self):
        SystemSetting.set('teaching_current_semester', CURRENT)
        self.activate(indexed=False, mapped=False)
        self.assertIsNotNone(importlib.util.find_spec('app.services.schedule_availability'))
        from app.services.schedule_availability import current_schedule_availability
        state = current_schedule_availability()
        self.assertTrue(state['ready'])
        self.assertFalse(state['candidates_ready'])
        self.assertFalse(state['registration_ready'])
        self.assert_hidden()

    def test_no_schedule_classroom_confirmation_preserves_manual_course_text(self):
        import json
        SystemSetting.set('teaching_current_semester', CURRENT)
        record = LectureSiteCapture(user_id=self.user.id, photo_key='manual.jpg',
                                    received_at='2026-10-05T10:00:00', location_json='{}')
        db.session.add(record)
        db.session.flush()
        payload = {'site_capture_id':record.id, 'course_title':'手填课程', 'teacher_name':'手填教师',
                   'assistant':{'semester':OLD, 'stage':'confirmed'}}
        draft = LectureFormDraft(user_id=self.user.id, draft_key='submit_form',
                                 payload_json=json.dumps(payload, ensure_ascii=False))
        db.session.add(draft)
        db.session.commit()
        response = self.client.patch('/user/api/site-capture/%d' % record.id,
                                     json={'building':'8','room_number':'309'})
        self.assertEqual(response.status_code, 200)
        saved = json.loads(draft.payload_json)
        self.assertEqual(saved['course_title'], '手填课程')
        self.assertEqual(saved['teacher_name'], '手填教师')
        self.assertNotIn('assistant', saved)

    def test_old_confirmed_photo_does_not_become_confirmed_after_current_schedule_recovers(self):
        import json
        self.activate(OLD)
        old_batch = ScheduleImportBatch.query.filter_by(semester=OLD).one().id
        SystemSetting.set('teaching_current_semester', CURRENT)
        self.activate()
        record = LectureSiteCapture(user_id=self.user.id, photo_key='old-confirmed.jpg',
                                    received_at='2026-04-07T10:00:00', building='8', room_number='309',
                                    confirmed_candidate_id='old-candidate',
                                    draft_json=json.dumps({'course_title':'旧草稿课程', 'assistant':{
                                        'semester':OLD, 'source_kind':'primary', 'source_batch_id':str(old_batch),
                                        'stage':'confirmed', 'candidate_id':'old-candidate'}}))
        db.session.add(record)
        db.session.commit()
        response = self.client.get('/user/api/site-capture/%d' % record.id)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['data']['course_confirmed'])
        self.assertEqual(json.loads(record.draft_json)['course_title'], '旧草稿课程')

    def test_replaced_batch_invalidates_photo_confirmation_in_the_same_semester(self):
        import json
        SystemSetting.set('teaching_current_semester', CURRENT)
        self.activate()
        record = LectureSiteCapture(user_id=self.user.id, photo_key='replaced-batch.jpg',
                                    received_at='2026-10-05T10:00:00', confirmed_candidate_id='old-candidate',
                                    draft_json=json.dumps({'assistant':{'semester':CURRENT, 'source_kind':'primary',
                                        'source_batch_id':'1', 'stage':'confirmed', 'candidate_id':'old-candidate'}}))
        db.session.add(record)
        db.session.commit()
        self.activate()
        response = self.client.get('/user/api/site-capture/%d' % record.id)
        self.assertFalse(response.get_json()['data']['course_confirmed'])

    def test_draft_metadata_cannot_relabel_a_protected_old_batch_candidate_id(self):
        import json
        SystemSetting.set('teaching_current_semester', CURRENT)
        self.activate()
        record = LectureSiteCapture(user_id=self.user.id, photo_key='relabeled.jpg',
                                    received_at='2026-10-05T10:00:00',
                                    confirmed_candidate_id='primary:999:old-digest',
                                    draft_json=json.dumps({'assistant':{'semester':CURRENT, 'source_kind':'primary',
                                        'source_batch_id':'1', 'stage':'confirmed',
                                        'candidate_id':'primary:999:old-digest'}}))
        db.session.add(record)
        db.session.commit()
        response = self.client.get('/user/api/site-capture/%d' % record.id)
        self.assertFalse(response.get_json()['data']['course_confirmed'])

    def test_anonymous_pages_do_not_require_schedule_tables(self):
        self.client = app.test_client()
        db.drop_all()
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

