"""No current timetable means manual entry, with reversible photo assistance."""
import json
import tempfile
from pathlib import Path

from PIL import Image
from app.models import LectureForm, LectureFormDraft, LectureSiteCapture, SystemSetting, db
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase
from tests.test_launch_submission_safety import valid_submission
from tests import test_schedule_availability as _schedule_fixture


class ManualOnlyWithoutScheduleTest(_ReviewMutationCompatibilityBase):
    def setUp(self):
        super().setUp()
        self.photo_temp = tempfile.TemporaryDirectory(prefix='manual-only-photos-')
        self.old_capture = {key:self.client.application.config.get(key) for key in ('LECTURE_CAPTURE_ENABLED','LECTURE_CAPTURE_FOLDER')}
        self.client.application.config.update(LECTURE_CAPTURE_ENABLED=True,LECTURE_CAPTURE_FOLDER=self.photo_temp.name)
        SystemSetting.set('teaching_current_semester','2026-2027-1')
        SystemSetting.set('teaching_first_week_monday','2026-09-07')
        self._login(self.officer)

    def tearDown(self):
        self.client.application.config.update(self.old_capture)
        super().tearDown()
        self.photo_temp.cleanup()

    def activate(self):
        return _schedule_fixture.ScheduleAvailabilityTest.activate(self)

    def assert_manual_page(self, response=None):
        html = (response or self.client.get('/user/submit_form')).get_data(as_text=True)
        for hidden in ('data-site-capture','data-building-map','js/site-capture.js','js/site-location.js',
                       'js/site-building-map.js','js/site-door-crop.js','css/site-capture.css','id="site_capture_id"'):
            self.assertNotIn(hidden,html)
        self.assertRegex(html,r'<div data-site-review-guide\s*>')
        self.assertIn('id="lectureForm"',html)
        self.assertIn('id="teacher_name"',html)
        return html

    def old_photo_draft(self):
        payload = valid_submission(course_title='原照片的手填课程')
        photo = LectureSiteCapture(user_id=self.officer.id,photo_key='old.jpg',
            received_at='2026-10-05T09:00:00+08:00',building='32',room_number='302',
            confirmed_candidate_id='primary:old:1',draft_json=json.dumps(payload,ensure_ascii=False))
        Image.new('RGB',(60,40),'white').save(Path(self.photo_temp.name)/'old.jpg')
        db.session.add(photo);db.session.flush()
        payload['site_capture_id']=photo.id
        db.session.add(LectureFormDraft(user_id=self.officer.id,draft_key='submit_form',payload_json=json.dumps(payload,ensure_ascii=False)))
        db.session.commit()
        return photo

    def test_missing_timetable_hides_whole_capture_flow_and_opens_manual_form(self):
        self.assert_manual_page()

    def test_new_manual_form_can_submit_without_photo_even_when_capture_config_is_on(self):
        response = self.client.post('/user/submit_form',data=valid_submission())
        self.assertEqual(response.status_code,302)
        self.assertEqual(LectureForm.query.count(),1)
        self.assertEqual(LectureSiteCapture.query.count(),0)

    def test_ready_timetable_restores_capture_and_photo_requirement(self):
        self.activate()
        html = self.client.get('/user/submit_form').get_data(as_text=True)
        self.assertIn('data-site-capture',html)
        self.assertIn('js/site-building-map.js',html)
        self.assertEqual(self.client.post('/user/submit_form',data=valid_submission()).status_code,400)
        self.assertEqual(LectureForm.query.count(),0)

    def test_manual_draft_detaches_old_capture_without_rewriting_photo_evidence(self):
        photo = self.old_photo_draft()
        original = (photo.draft_json,photo.confirmed_candidate_id,Path(self.photo_temp.name,'old.jpg').read_bytes())
        loaded = self.client.get('/user/api/lecture_form_draft').get_json()['data']
        self.assertNotIn('site_capture_id',loaded)
        self.assertEqual(loaded['course_title'],'原照片的手填课程')
        saved = self.client.put('/user/api/lecture_form_draft',json={'data':valid_submission(lecture_date='2026-10-06',lecture_date_display='2026/10/06星期二',lecture_location='8-309'), 'expected_site_capture_id':None})
        self.assertEqual(saved.status_code,200)
        self.assertNotIn('site_capture_id',json.loads(LectureFormDraft.query.one().payload_json))
        self.assertEqual((photo.draft_json,photo.confirmed_candidate_id,Path(self.photo_temp.name,'old.jpg').read_bytes()),original)
        submitted = self.client.post('/user/submit_form',data=valid_submission(lecture_date='2026-10-06',lecture_date_display='2026/10/06星期二',lecture_location='8-309'))
        self.assertEqual(submitted.status_code,302)
        self.assertIsNone(photo.form_id)
        self.assertEqual(LectureFormDraft.query.count(),0)

    def test_submit_directly_consumes_projected_draft_and_ignores_stale_photo_id(self):
        photo = self.old_photo_draft()
        original = photo.draft_json
        submitted = self.client.post('/user/submit_form',data=valid_submission(site_capture_id=str(photo.id),lecture_date='2026-10-07',lecture_date_display='2026/10/07星期三',lecture_location='8-309'))
        self.assertEqual(submitted.status_code,302)
        self.assertIsNone(photo.form_id)
        self.assertEqual(photo.draft_json,original)
        self.assertEqual(LectureFormDraft.query.count(),0)

    def test_old_photo_can_be_resumed_after_current_timetable_becomes_ready(self):
        photo = self.old_photo_draft()
        self.assert_manual_page()
        self.activate()
        html = self.client.get('/user/submit_form').get_data(as_text=True)
        self.assertIn('data-site-capture',html)
        restored = self.client.post(f'/user/api/site-capture/{photo.id}/resume')
        self.assertEqual(restored.status_code,200)
        self.assertEqual(self.client.get('/user/api/lecture_form_draft').get_json()['data']['site_capture_id'],photo.id)

    def test_invalid_manual_submission_preserves_inputs_and_manual_layout(self):
        response = self.client.post('/user/submit_form',data=valid_submission(course_feedback='太短'))
        self.assertEqual(response.status_code,400)
        html = self.assert_manual_page(response)
        self.assertIn(json.dumps('隔离教师',ensure_ascii=True),html)

    def test_disabled_capture_switch_keeps_manual_mode_with_a_ready_timetable(self):
        self.activate()
        self.client.application.config['LECTURE_CAPTURE_ENABLED']=False
        self.assert_manual_page()
        self.assertEqual(self.client.post('/user/submit_form',data=valid_submission()).status_code,302)
