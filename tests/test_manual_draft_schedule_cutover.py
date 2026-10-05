"""A server-authorized manual draft survives later photo-policy activation."""
import json
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase
from tests.test_launch_submission_safety import valid_submission
from tests import test_schedule_availability as _schedule_fixture
from app.models import LectureForm, LectureFormDraft, LectureSiteCapture, SystemSetting, db
from sqlalchemy import inspect


class ManualDraftScheduleCutoverTest(_ReviewMutationCompatibilityBase):
    def setUp(self):
        super().setUp()
        self.capture_config = self.client.application.config.get('LECTURE_CAPTURE_ENABLED')
        self.client.application.config['LECTURE_CAPTURE_ENABLED'] = True
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('teaching_first_week_monday', '2026-09-07')
        self._login(self.officer)

    def tearDown(self):
        self.client.application.config['LECTURE_CAPTURE_ENABLED'] = self.capture_config
        super().tearDown()

    def activate(self):
        return _schedule_fixture.ScheduleAvailabilityTest.activate(self)

    def save(self, **changes):
        result = self.client.put('/user/api/lecture_form_draft', json={'data':valid_submission(**changes)})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        return result

    def test_saved_manual_draft_can_continue_and_submit_after_schedule_activation(self):
        self.save(course_title='启用前手填草稿')
        self.activate()
        page = self.client.get('/user/submit_form').get_data(as_text=True)
        self.assertNotIn('data-site-capture', page)
        self.assertIn('id="lectureForm"', page)
        self.save(course_title='启用后继续完善手填草稿')
        response = self.client.post('/user/submit_form', data=valid_submission(course_title='启用后继续完善手填草稿'))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertEqual(LectureForm.query.one().course_title, '启用后继续完善手填草稿')
        self.assertEqual(LectureSiteCapture.query.count(), 0)
        self.assertEqual(LectureFormDraft.query.count(), 0)
        # The compatibility grant belongs to the consumed draft, not this account.
        self.assertIn('data-site-capture', self.client.get('/user/submit_form').get_data(as_text=True))
        self.assertEqual(self.client.post('/user/submit_form', data=valid_submission(course_title='启用后的全新表')).status_code, 400)

    def test_new_ready_schedule_draft_cannot_claim_manual_exemption_from_client_payload(self):
        self.activate()
        self.save(entry_mode='manual', manual_entry_allowed=True)
        self.assertIn('data-site-capture', self.client.get('/user/submit_form').get_data(as_text=True))
        response = self.client.post('/user/submit_form', data=valid_submission(entry_mode='manual', manual_entry_allowed='true'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LectureForm.query.count(), 0)

    def test_deleting_manual_draft_does_not_exempt_next_new_draft(self):
        self.save()
        self.activate()
        self.assertEqual(self.client.delete('/user/api/lecture_form_draft').status_code, 200)
        self.save(course_title='删除旧草稿后新建')
        self.assertIn('data-site-capture', self.client.get('/user/submit_form').get_data(as_text=True))
        self.assertEqual(self.client.post('/user/submit_form', data=valid_submission(course_title='删除旧草稿后新建')).status_code, 400)

    def test_draft_started_while_capture_disabled_remains_manual_after_enabling(self):
        self.activate()
        self.client.application.config['LECTURE_CAPTURE_ENABLED'] = False
        self.save(course_title='关闭拍照时的草稿')
        self.client.application.config['LECTURE_CAPTURE_ENABLED'] = True
        response = self.client.post('/user/submit_form', data=valid_submission(course_title='关闭拍照时的草稿'))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertEqual(LectureForm.query.count(), 1)

    def test_legacy_schema_upgrade_preserves_manual_payload_and_timestamps(self):
        from app.app import init_database
        payload = json.dumps(valid_submission(course_title='升级前已有的手填草稿'), ensure_ascii=False)
        with db.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE lecture_form_drafts')
            connection.exec_driver_sql('CREATE TABLE lecture_form_drafts (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, draft_key VARCHAR(50) NOT NULL, payload_json TEXT NOT NULL, created_at DATETIME, updated_at DATETIME, UNIQUE(user_id,draft_key))')
            connection.exec_driver_sql('INSERT INTO lecture_form_drafts VALUES (?,?,?,?,?,?)', (12,self.officer.id,'submit_form',payload,'2026-10-01 09:00:00','2026-10-02 10:00:00'))
        init_database()
        self.assertIn('entry_mode', {column['name'] for column in inspect(db.engine).get_columns('lecture_form_drafts')})
        self.activate()
        with db.engine.connect() as connection:
            before = connection.exec_driver_sql('SELECT id,user_id,draft_key,payload_json,created_at,updated_at FROM lecture_form_drafts').one()
        init_database()
        with db.engine.connect() as connection:
            after = connection.exec_driver_sql('SELECT id,user_id,draft_key,payload_json,created_at,updated_at FROM lecture_form_drafts').one()
        self.assertEqual(tuple(before), tuple(after))
        self.assertEqual(before.payload_json, payload)
        self.assertNotIn('data-site-capture', self.client.get('/user/submit_form').get_data(as_text=True))
        response = self.client.post('/user/submit_form', data=valid_submission(course_title='升级前已有的手填草稿'))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
