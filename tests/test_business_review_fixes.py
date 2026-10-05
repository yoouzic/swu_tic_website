"""Business review regressions from the 2026-10-02 audit."""
import threading
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from app.blueprints.admin import review as review_routes
from app.models import LectureForm, db
from app.utils.review_drafts import load_review_form_draft
from app.services.review_concurrency import claim_review_form
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase
from tests.review_request_utils import opened_review_revision, with_opened_review_revision


class BusinessReviewConcurrencyTest(_ReviewMutationCompatibilityBase):
    def test_claim_revision_advances_when_clock_returns_the_old_timestamp(self):
        form = self._make_form(self.officer)
        old_timestamp = form.updated_at
        with mock.patch('app.services.review_concurrency.datetime') as clock:
            clock.now.return_value = old_timestamp
            claim_review_form(form, form.logical_id)
        db.session.expire(form, ['updated_at'])
        self.assertGreater(form.updated_at, old_timestamp)

    def test_two_reviewers_cannot_both_append_from_same_original(self):
        form = self._make_form(self.officer)
        form_id = form.id
        first_actor = self.group_admin.id
        second_actor = self.dept_admin.id
        payloads = {
            first_actor: {'form_data': self._full_form_data(form, teacher_name='教师修正'),
                          'review_comment': '组长已核对'},
            second_actor: {'form_data': self._full_form_data(form, course_title='课程修正'),
                           'review_comment': '部门已核对'},
        }
        for actor in (self.group_admin, self.dept_admin):
            self._login(actor)
            response = self.client.put(f'/admin/api/review/form/{form_id}/draft',
                                       json=payloads[actor.id])
            self.assertEqual(response.status_code, 200)
            payloads[actor.id] = with_opened_review_revision(self.client, form_id, payloads[actor.id])
        db.session.remove()
        ready = threading.Barrier(2)
        first_finished = threading.Event()
        execute = review_routes.execute_review_mutation

        def ordered_execute(plan):
            ready.wait(timeout=15)
            if plan.actor_id == second_actor:
                self.assertTrue(first_finished.wait(timeout=15))
            try:
                return execute(plan)
            finally:
                if plan.actor_id == first_actor:
                    first_finished.set()

        def submit(actor_id):
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session['user_id'] = actor_id
                    session['user_role'] = '管理员'
                response = client.post(f'/admin/api/review/submit/{form_id}',
                                       json=payloads[actor_id])
                return response.status_code, response.get_json()

        with mock.patch.object(review_routes, 'execute_review_mutation', ordered_execute):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, (first_actor, second_actor)))
        self.assertEqual(results[0][0], 200)
        self.assertTrue(results[0][1]['success'])
        self.assertEqual(results[1][0], 409)
        self.assertFalse(results[1][1]['success'])
        self.assertIn('更新', results[1][1]['message'])
        original = db.session.get(LectureForm, form_id)
        versions = original.get_all_versions()
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].teacher_name, '教师修正')
        self.assertIsNotNone(load_review_form_draft(second_actor, form_id))
        self.assertIsNone(load_review_form_draft(first_actor, form_id))

    def test_rejection_racing_approval_is_rejected_without_extra_version(self):
        self._assert_only_one_decision()

    def test_compatibility_approval_racing_current_approval_cannot_append_again(self):
        self._assert_only_one_decision(second_is_approval=True)

    def test_rejection_opinion_update_keeps_monotonic_claim_until_commit(self):
        self._assert_only_one_decision(both_rejected=True)

    def _assert_only_one_decision(self, second_is_approval=False, both_rejected=False):
        form = self._make_form(self.officer, status='已驳回' if both_rejected else '待审核')
        form_id = form.id
        approver_id = self.group_admin.id
        rejector_id = self.dept_admin.id
        payload = {'form_data': self._full_form_data(form), 'review_comment': '通过'}
        opened_revisions = {}
        for actor in (self.group_admin, self.dept_admin):
            self._login(actor)
            opened_revisions[actor.id] = opened_review_revision(self.client, form_id)
        frozen_timestamp = form.updated_at
        db.session.remove()
        ready = threading.Barrier(2)
        approved = threading.Event()
        real_latest = LectureForm.get_latest_version
        local = threading.local()

        def latest_after_both_read(instance):
            latest = real_latest(instance)
            if not getattr(local, 'waited', False):
                local.waited = True
                ready.wait(timeout=15)
                if local.actor_id == rejector_id:
                    self.assertTrue(approved.wait(timeout=15))
            return latest

        def submit(actor_id):
            local.actor_id = actor_id
            with self.client.application.test_client() as client:
                with client.session_transaction() as session:
                    session['user_id'] = actor_id
                    session['user_role'] = '管理员'
                try:
                    if both_rejected:
                        response = client.post(f'/admin/api/review/reject/{form_id}',
                                               json={'reason': f'意见-{actor_id}', **opened_revisions[actor_id]})
                    elif actor_id == approver_id:
                        response = client.post(f'/admin/api/review/submit/{form_id}', json={**payload, **opened_revisions[actor_id]})
                    elif second_is_approval:
                        response = client.post(f'/admin/api/review/form/{form_id}', json={
                            'form_data': self._full_form_data(form, course_title='旧入口修正'),
                            'review_comment': '旧入口审批',
                            **opened_revisions[actor_id],
                        })
                    else:
                        response = client.post(f'/admin/api/review/reject/{form_id}',
                                               json={'reason': '退回完善', **opened_revisions[actor_id]})
                    return response.status_code, response.get_json()
                finally:
                    if actor_id == approver_id:
                        approved.set()

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(LectureForm, 'get_latest_version', latest_after_both_read))
            if both_rejected:
                for name in ('app.blueprints.admin.review.datetime',
                             'app.services.review_concurrency.datetime'):
                    clock = stack.enter_context(mock.patch(name))
                    clock.now.return_value = frozen_timestamp
                # SQLAlchemy caches a wrapper whose closure invokes the original
                # datetime callable. Freeze that clock, not only its public arg.
                cell = LectureForm.__table__.c.updated_at.onupdate.arg.__closure__[0]
                actual_clock = cell.cell_contents
                cell.cell_contents = lambda: frozen_timestamp
                stack.callback(setattr, cell, 'cell_contents', actual_clock)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, (approver_id, rejector_id)))
        self.assertTrue(results[0][1]['success'])
        self.assertEqual(results[1][0], 409)
        self.assertFalse(results[1][1]['success'])
        self.assertEqual(len(db.session.get(LectureForm, form_id).get_all_versions()),
                         1 if both_rejected else 2)
        if both_rejected:
            latest = db.session.get(LectureForm, form_id)
            self.assertEqual(latest.review_comment, f'意见-{approver_id}')
            self.assertGreater(latest.updated_at, frozen_timestamp)


class BusinessReviewValidationTest(_ReviewMutationCompatibilityBase):
    def test_non_text_values_do_not_bypass_legacy_empty_field_validation(self):
        for value in (False, 0, [], {}):
            with self.subTest(value=value):
                form = self._make_form(self.officer)
                form.contact_phone2 = None
                db.session.commit()
                self._login(self.dept_admin)
                draft = {'form_data': self._full_form_data(form, contact_phone2=value)}
                self.client.put(f'/admin/api/review/form/{form.id}/draft', json=draft)
                response = self._submit_a(form.id, draft)
                self.assertEqual(response.status_code, 400)
                self.assertIn('contact_phone2', response.get_json()['field_errors'])
                self.assertEqual(len(form.get_all_versions()), 1)
                self.assertIsNotNone(load_review_form_draft(self.dept_admin.id, form.id))

    def test_invalid_edits_return_field_errors_without_mutation(self):
        for field, bad_value in (
            ('student_signature1', ''), ('student_signature2', ''),
            ('contact_phone1', 'abc'), ('contact_phone2', '1380000000'),
            ('course_feedback', '短'), ('lecture_date', '2026-02-30'),
            ('class_period', '第4-3节'),
        ):
            with self.subTest(field=field):
                form = self._make_form(self.officer, course_feedback='正常课堂反馈内容。' * 10)
                self._login(self.dept_admin)
                draft = {'form_data': self._full_form_data(form, **{field: bad_value}),
                         'review_comment': '保留待修正内容'}
                self.client.put(f'/admin/api/review/form/{form.id}/draft', json=draft)
                response = self._submit_a(form.id, draft)
                self.assertEqual(response.status_code, 400)
                body = response.get_json()
                self.assertFalse(body['success'])
                self.assertIn(field, body['field_errors'])
                self.assertEqual(len(form.get_all_versions()), 1)
                self.assertEqual(form.status, '待审核')
                self.assertIsNotNone(load_review_form_draft(self.dept_admin.id, form.id))

    def test_unmodified_legacy_short_feedback_can_be_reviewed(self):
        form = self._make_form(self.officer, course_feedback='历史短反馈')
        self._login(self.dept_admin)
        response = self._submit_a(form.id, {'form_data': self._full_form_data(form)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

    def test_valid_edits_still_follow_the_shared_first_stage(self):
        form = self._make_form(self.officer, course_feedback='正常课堂反馈内容。' * 10)
        self._login(self.group_admin)
        response = self._submit_a(form.id, {'form_data': self._full_form_data(
            form, course_feedback='修正后的课堂反馈内容。' * 10)})
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(response.get_json()['new_status'], '部门已审核')
