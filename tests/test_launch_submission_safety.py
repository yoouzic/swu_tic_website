"""Launch regressions: real isolated routes, including submission races."""
import threading
import time
import json
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from app.blueprints.user import forms as form_routes
from app.models import Course, CourseRegistration, LectureForm, SystemSetting, Teacher, db
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


def valid_submission(**changes):
    data = dict(lecture_date='2026-10-05', lecture_date_display='2026/10/05星期一',
        start_period='3', end_period='4', class_period='第3-4节', lecture_location='32-302',
        teacher_name='隔离教师', teacher_college='隔离学院', course_title='隔离课程',
        student_grade_class='2025级一班', course_changes='无', abnormal_situation='无',
        teaching_method='PPT演示法、讲授法、师生互动法', classroom_discipline='非常好',
        classroom_atmosphere='非常好', courseware_quality='非常好', overall_effect='非常好',
        quality_case='推荐', course_feedback='教师结合实际案例讲解课程重点，课堂讨论积极，学生能够独立完成练习，知识点安排合理，问题解答细致，教学节奏清晰，课堂秩序良好。',
        suggestions='无', student_signature1='同学甲', contact_phone1='13800000001',
        student_signature2='同学乙', contact_phone2='13800000002')
    data.update(changes)
    return data


def revision_fields_for(form):
    return {'expected_form_id':str(form.id),
            'expected_form_updated_at':form.updated_at.isoformat() if form.updated_at else ''}


class LaunchSubmissionSafetyTest(_ReviewMutationCompatibilityBase):
    def setUp(self):
        super().setUp()
        self.client.application.config['LECTURE_CAPTURE_ENABLED'] = False
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        db.session.commit()

    def sign_in(self, user, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            session.update(user_id=user.id, user_role=user.role, user_name=user.name)
        return client

    def create(self, **changes):
        self.sign_in(self.officer)
        result = self.client.post('/user/submit_form', data=valid_submission(**changes))
        self.assertEqual(result.status_code, 302)
        return LectureForm.query.order_by(LectureForm.id.desc()).first()

    def edit_data(self, form, **changes):
        return valid_submission(unique_id=str(form.unique_id or form.id), expected_form_id=str(form.id),
            expected_form_updated_at=form.updated_at.isoformat() if form.updated_at else '', **changes)

    def test_foreign_logical_chain_is_rejected_before_mutation(self):
        form = self.create()
        self.sign_in(self.officer_b)
        result = self.client.post('/user/submit_form', data=self.edit_data(form))
        self.assertEqual(result.status_code, 403)
        self.assertEqual(LectureForm.query.count(), 1)

    def test_unknown_and_malformed_logical_ids_are_rejected(self):
        self.sign_in(self.officer)
        for value, status in [('999999', 404), ('nonnumeric', 400), ('0', 400), ('-1', 400), ('²', 400), ('①', 400)]:
            with self.subTest(value=value):
                result=self.client.post('/user/submit_form',data=valid_submission(unique_id=value))
                self.assertEqual(result.status_code,status)
                self.assertEqual(LectureForm.query.count(),0)

    def test_invalid_initial_fields_do_not_enter_database(self):
        self.sign_in(self.officer)
        cases=[dict(teacher_name=''), dict(contact_phone1='abc'), dict(course_feedback='短'),
               dict(lecture_date_display='2026-02-30'), dict(class_period='第14-1节'),
               dict(student_signature2=''), dict(contact_phone2='')]
        for values in cases:
            with self.subTest(values=values):
                result=self.client.post('/user/submit_form',data=valid_submission(**values))
                self.assertEqual(result.status_code,400)
                self.assertEqual(LectureForm.query.count(),0)

    def test_missing_required_field_returns_controlled_error(self):
        self.sign_in(self.officer)
        data=valid_submission();data.pop('teacher_name')
        result=self.client.post('/user/submit_form',data=data)
        self.assertEqual(result.status_code,400)
        self.assertIn('授课教师',result.get_data(as_text=True))
        self.assertEqual(LectureForm.query.count(),0)

    def test_pending_edit_requires_expected_revision(self):
        form=self.create()
        result=self.client.post('/user/submit_form',data=valid_submission(unique_id=str(form.unique_id),teacher_name='不应覆盖'))
        self.assertEqual(result.status_code,409)
        db.session.expire_all()
        self.assertEqual(db.session.get(LectureForm,form.id).teacher_name,'隔离教师')

    def test_stale_page_cannot_overwrite_saved_edit(self):
        form=self.create()
        old=self.edit_data(form,teacher_name='旧页面输入')
        current=self.edit_data(form,teacher_name='已保存新输入')
        first=self.client.post('/user/submit_form',data=current)
        self.assertEqual(first.status_code,302)
        second=self.client.post('/user/submit_form',data=old)
        self.assertEqual(second.status_code,409)
        db.session.expire_all()
        self.assertEqual(db.session.get(LectureForm,form.id).teacher_name,'已保存新输入')

    def test_final_form_cannot_be_reopened_by_direct_request(self):
        form=self.create();form.status='中心已审核';db.session.commit()
        self.assertEqual(self.client.get(f'/user/form/edit/{form.id}').status_code,403)
        self.assertEqual(self.client.post('/user/submit_form',data=self.edit_data(form)).status_code,403)
        self.assertEqual(LectureForm.query.count(),1)

    def test_old_registration_cannot_claim_current_course_check(self):
        teacher=Teacher(teacher_id='OLDT',name='隔离教师')
        course=Course(course_code='OLD',selection_code='OLDSEL',course_name='隔离课程',
                      teacher_id='OLDT',class_location='32-302',semester='2025-2026-2')
        db.session.add_all([teacher,course]);db.session.flush()
        row=CourseRegistration(course_code='OLD',selection_code='OLDSEL',user_id=self.officer.id,
                               listening_info='第1周星期一第3-4节',is_used=False)
        db.session.add(row);db.session.commit()
        self.sign_in(self.officer)
        result=self.client.post('/user/submit_form',data=valid_submission(registration_id=str(row.id)))
        self.assertEqual(result.status_code,302)
        form=LectureForm.query.one()
        self.assertEqual(form.audit_tag,'需要人工审核')
        self.assertEqual(form.registration_id,row.id)
        db.session.expire_all();self.assertTrue(db.session.get(CourseRegistration,row.id).is_used)

    def test_old_assistant_semester_rejected_before_revalidation(self):
        self.sign_in(self.officer)
        import json
        stale={'source_kind':'primary','semester':'2025-2026-2','query':{'semester':'2025-2026-2'}}
        with mock.patch.object(form_routes,'revalidate_selection') as validate:
            result=self.client.post('/user/submit_form',data=valid_submission(assistant_payload=json.dumps(stale)))
        self.assertEqual(result.status_code,400)
        validate.assert_not_called()
        self.assertEqual(LectureForm.query.count(),0)

    def test_legacy_review_cannot_bypass_changed_field_validation(self):
        form=self.create();self.sign_in(self.group_admin)
        result=self.client.post(f'/admin/api/review/form/{form.id}',json={
            'form_data':{'teacher_name':'','contact_phone1':'abc','course_feedback':'短'},'score_data':[]})
        self.assertEqual(result.status_code,400)
        self.assertEqual((result.get_json() or {}).get('code'),'invalid_form')
        self.assertEqual(LectureForm.query.count(),1)

    def test_reject_malformed_json_is_400_without_sql_leak(self):
        form=self.create();self.sign_in(self.group_admin)
        for body in ([],{'reason':{'x':1}}):
            with self.subTest(body=body):
                result=self.client.post(f'/admin/api/review/reject/{form.id}',json=body)
                self.assertEqual(result.status_code,400)
                self.assertNotIn('INSERT INTO',result.get_data(as_text=True))
                self.assertEqual(LectureForm.query.count(),1)

    def test_legacy_review_validates_derived_date_and_period(self):
        form=self.create();self.sign_in(self.group_admin)
        for fields in ({'lecture_date_display':'2026-02-30'}, {'start_period':'14','end_period':'1'}):
            with self.subTest(fields=fields):
                result=self.client.post(f'/admin/api/review/form/{form.id}',json={'form_data':fields})
                self.assertEqual(result.status_code,400)
                self.assertEqual((result.get_json() or {}).get('code'),'invalid_form')
                self.assertEqual(LectureForm.query.count(),1)

    def test_approval_comment_has_controlled_type_validation(self):
        form=self.create();self.sign_in(self.group_admin)
        for path in (f'/admin/api/review/submit/{form.id}', f'/admin/api/review/form/{form.id}'):
            with self.subTest(path=path):
                result=self.client.post(path,json={'form_data':self._full_form_data(form),
                                                    'review_comment':{'invalid':'object'},'score_data':[]})
                self.assertEqual(result.status_code,400)
                self.assertNotIn('INSERT INTO',result.get_data(as_text=True))
                self.assertEqual(LectureForm.query.count(),1)

    def test_main_review_missing_witness_fields_cannot_clear_existing_witness(self):
        form=self.create();self.sign_in(self.group_admin)
        fields=self._full_form_data(form)
        fields.pop('student_signature2');fields.pop('contact_phone2')
        result=self.client.post(f'/admin/api/review/submit/{form.id}',json={'form_data':fields,'score_data':[]})
        self.assertEqual(result.status_code,400)
        self.assertEqual((result.get_json() or {}).get('code'),'invalid_form')
        self.assertEqual(LectureForm.query.count(),1)

    def test_main_review_empty_body_has_controlled_validation(self):
        form=self.create();self.sign_in(self.group_admin)
        result=self.client.post(f'/admin/api/review/submit/{form.id}',json={'form_data':{},'score_data':[]})
        self.assertEqual(result.status_code,400)
        self.assertEqual((result.get_json() or {}).get('code'),'invalid_form')
        self.assertEqual(LectureForm.query.count(),1)

    def test_review_page_hides_school_reference_without_current_snapshot(self):
        form=self.create();self.sign_in(self.group_admin)
        page=self.client.get(f'/admin/review/form/{form.id}')
        self.assertEqual(page.status_code,200)
        self.assertNotIn('id="scheduleReferenceSection"',page.get_data(as_text=True))
        self.assertIn('id="contactsReferenceSection"',page.get_data(as_text=True))

    def test_reviewer_opened_before_listener_edit_cannot_overwrite_new_input(self):
        form=self.create()
        opened=self._full_form_data(form)
        revision={'expected_form_id':str(form.id),
                  'expected_form_updated_at':form.updated_at.isoformat()}
        edit=self.edit_data(form,teacher_name='信息员已保存的新教师')
        self.assertEqual(self.client.post('/user/submit_form',data=edit).status_code,302)
        self.sign_in(self.group_admin)
        for path in (f'/admin/api/review/submit/{form.id}',f'/admin/api/review/form/{form.id}'):
            with self.subTest(path=path):
                result=self.client.post(path,json={**revision,'form_data':opened,'score_data':[]})
                self.assertEqual(result.status_code,409)
                self.assertEqual(LectureForm.query.count(),1)
                db.session.expire_all()
                self.assertEqual(db.session.get(LectureForm,form.id).teacher_name,'信息员已保存的新教师')

    def test_review_requires_opened_revision_even_when_form_is_valid(self):
        form=self.create();self.sign_in(self.group_admin)
        result=self.client.post(f'/admin/api/review/submit/{form.id}',json={
            'form_data':self._full_form_data(form),'score_data':[]})
        self.assertEqual(result.status_code,409)
        self.assertEqual(LectureForm.query.count(),1)

    def test_review_draft_reports_staleness_and_preserves_saved_input(self):
        form=self.create()
        revision=revision_fields_for(form)
        self.sign_in(self.group_admin)
        saved=self.client.put(f'/admin/api/review/form/{form.id}/draft',json={'data':{
            'form_data':{**revision,'teacher_name':'审核草稿里的教师'},'review_comment':'保留此意见'}})
        self.assertEqual(saved.status_code,200)
        self.sign_in(self.officer)
        edit=self.edit_data(form,teacher_name='信息员新教师')
        self.assertEqual(self.client.post('/user/submit_form',data=edit).status_code,302)
        self.sign_in(self.group_admin)
        loaded=self.client.get(f'/admin/api/review/form/{form.id}/draft').get_json()
        self.assertTrue(loaded['stale'])
        self.assertEqual(loaded['data']['form_data']['teacher_name'],'审核草稿里的教师')
        self.assertEqual(loaded['data']['review_comment'],'保留此意见')
    def test_simultaneous_identical_submissions_only_create_one_form(self):
        user_id=self.officer.id
        ready=threading.Barrier(2)
        db.session.remove()
        def submit(_):
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=user_id,user_role='信息员')
                ready.wait(timeout=10)
                r=client.post('/user/submit_form',data=valid_submission())
                return r.status_code,r.headers.get('Location')
        real_find=form_routes._find_recent_duplicate_submission
        def delayed_read(*args):
            found=real_find(*args)
            time.sleep(0.2)
            return found
        with mock.patch.object(form_routes,'_find_recent_duplicate_submission',delayed_read):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(submit,range(2)))
        self.assertEqual([x[0] for x in results],[302,302])
        self.assertEqual(results[0][1],results[1][1])
        self.assertEqual(LectureForm.query.count(),1)

    def test_approval_racing_listener_edit_does_not_rewrite_history(self):
        form=self.create();form_id=form.id;user_id=self.officer.id;manager_id=self.group_admin.id
        data=self.edit_data(form,teacher_name='并发新输入')
        review_data=self._full_form_data(form)
        revision={'expected_form_id':str(form.id),'expected_form_updated_at':form.updated_at.isoformat()}
        paused=threading.Event();resume=threading.Event()
        real_extract=form_routes._extract_assistant_submission_payload
        def pause_after_read():
            paused.set();self.assertTrue(resume.wait(timeout=15));return real_extract()
        db.session.remove()
        def submit():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:session.update(user_id=user_id,user_role='信息员')
                return client.post('/user/submit_form',data=data).status_code
        with mock.patch.object(form_routes,'_extract_assistant_submission_payload',pause_after_read):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(submit)
                try:
                    self.assertTrue(paused.wait(timeout=15))
                    with self.client.application.test_client() as client:
                        with client.session_transaction() as session:session.update(user_id=manager_id,user_role='管理员')
                        approved=client.post(f'/admin/api/review/submit/{form_id}',json={**revision,'form_data':review_data,'score_data':[]})
                        self.assertTrue((approved.get_json() or {}).get('success'))
                finally:resume.set()
                self.assertEqual(future.result(timeout=20),409)
        db.session.expire_all()
        self.assertEqual(db.session.get(LectureForm,form_id).teacher_name,'隔离教师')
        self.assertEqual(LectureForm.query.order_by(LectureForm.id.desc()).first().status,'部门已审核')

    def test_deleted_form_during_listener_edit_returns_conflict_with_inputs(self):
        form = self.create()
        form_id, user_id, center_id = form.id, self.officer.id, self.center_admin.id
        edit = self.edit_data(form, teacher_name='删除并发时保留的教师')
        paused, resumed = threading.Event(), threading.Event()
        real_extract = form_routes._extract_assistant_submission_payload
        def pause_edit():
            paused.set()
            self.assertTrue(resumed.wait(timeout=15))
            return real_extract()
        def submit_edit():
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session.update(user_id=user_id, user_role='信息员')
                response = client.post('/user/submit_form', data=edit)
                return response.status_code, response.get_data(as_text=True)
        db.session.remove()
        with mock.patch.object(form_routes, '_extract_assistant_submission_payload', pause_edit):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(submit_edit)
                try:
                    self.assertTrue(paused.wait(timeout=15))
                    with self.client.application.test_client() as center:
                        with center.session_transaction() as session:
                            session.update(user_id=center_id, user_role='管理员')
                        self.assertEqual(center.delete(f'/admin/api/review/form/{form_id}').status_code, 200)
                finally:
                    resumed.set()
                status, html = result.result(timeout=20)
        self.assertEqual(status, 409)
        self.assertIn(json.dumps('删除并发时保留的教师', ensure_ascii=True), html)
        self.assertEqual(LectureForm.query.count(), 0)
