"""Personal department read state never changes a form's review decision."""
from datetime import datetime, timedelta
import re
from unittest import mock

from app.app import app
from app.models import LectureForm, ScoreRecord, db
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


class DepartmentReadReceiptTest(_ReviewMutationCompatibilityBase):
    def _approved(self, owner=None, reviewer=None):
        form = self._make_form(owner or self.officer, status='部门已审核')
        form.reviewer_id = (reviewer or self.group_admin).id
        form.review_time = datetime.now()
        form.review_comment = '小组审核记录'
        db.session.commit()
        return form

    def _payload(self, form):
        return {'expected_form_id': str(form.id),
                'expected_form_updated_at': form.updated_at.isoformat() if form.updated_at else ''}

    def _mark(self, form, payload=None):
        return self.client.post(f'/admin/api/review/form/{form.id}/read-receipt',
                                json=payload if payload is not None else self._payload(form))

    def _listed(self, form):
        response = self.client.get('/admin/api/review/forms')
        self.assertEqual(response.status_code, 200, response.get_json())
        return next(row for group in response.get_json()['forms'] for row in group['forms']
                    if row['id'] == form.id)

    def _receipt_count(self):
        return db.session.execute(db.text('SELECT count(*) FROM review_read_receipts')).scalar()

    def test_get_list_and_both_detail_formats_do_not_mark_read(self):
        form = self._approved()
        self._login(self.dept_admin)
        self.assertEqual(self._listed(form)['department_read']['state'], 'unread')
        detail = self.client.get(f'/admin/api/review/form/{form.id}').get_json()['form']
        self.assertEqual(detail['department_read']['state'], 'unread')
        preview = self.client.get(f'/admin/form/{form.id}?format=json').get_json()
        self.assertEqual(preview['department_read']['state'], 'unread')
        self.assertEqual(preview['form_id'], form.id)
        self.assertEqual(preview['expected_form_updated_at'], self._payload(form)['expected_form_updated_at'])
        page = self.client.get(f'/admin/form/{form.id}')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self._receipt_count(), 0)

    def test_mark_is_durable_idempotent_and_preserves_decision_and_scores(self):
        form = self._approved()
        score = ScoreRecord(form_id=form.id, reviewer_id=self.group_admin.id,
                            total_department_score=2, total_personal_score=1)
        db.session.add(score)
        db.session.commit()
        before = (form.status, form.updated_at, form.review_time, form.reviewer_id,
                  form.review_comment, form.course_feedback, len(form.get_all_versions()))
        self._login(self.dept_admin)
        first = self._mark(form)
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(first.get_json()['department_read']['state'], 'read')
        self.assertTrue(first.get_json()['department_read']['read_at'])
        self.assertEqual(self._mark(form).get_json(), first.get_json())
        self.assertEqual(self._receipt_count(), 1)
        db.session.expire_all()
        self.assertEqual(self._listed(form)['department_read'], first.get_json()['department_read'])
        after = (form.status, form.updated_at, form.review_time, form.reviewer_id,
                 form.review_comment, form.course_feedback, len(form.get_all_versions()))
        self.assertEqual(before, after)
        self.assertEqual(ScoreRecord.query.filter_by(form_id=form.id).one().total_department_score, 2)

    def test_read_state_is_personal_and_own_approval_counts_as_read(self):
        form = self._approved()
        second_manager = self._make_user('D101', 'student-d101', '管理员', self.dept_a.name,
                                         self.group_a1, permissions={'审表_部门'})
        db.session.commit()
        self._login(self.dept_admin)
        self.assertEqual(self._mark(form).status_code, 200)
        self._login(second_manager)
        self.assertEqual(self._listed(form)['department_read']['state'], 'unread')
        own_approval = self._approved(reviewer=second_manager)
        self.assertEqual(self._listed(own_approval)['department_read']['state'], 'read')
        self.assertEqual(self._receipt_count(), 1)

    def test_new_version_and_changed_revision_are_unread(self):
        form = self._approved()
        self._login(self.dept_admin)
        self.assertEqual(self._mark(form).status_code, 200)
        old_payload = self._payload(form)
        form.updated_at += timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(self._listed(form)['department_read']['state'], 'unread')
        self.assertEqual(self._mark(form, old_payload).status_code, 409)
        self.assertEqual(self._mark(form).status_code, 200)
        latest = self._approved()
        latest.unique_id = form.logical_id
        db.session.commit()
        self.assertEqual(self._listed(latest)['department_read']['state'], 'unread')
        self.assertEqual(self._mark(form).status_code, 409)
        self.assertEqual(self._receipt_count(), 1)

    def test_historical_details_never_offer_auto_acknowledgement(self):
        form = self._approved()
        latest = self._approved()
        latest.unique_id = form.logical_id
        db.session.commit()
        self._login(self.dept_admin)
        self.assertIsNone(self._listed(form)['department_read'])
        self.assertIsNone(self.client.get(f'/admin/api/review/form/{form.id}').get_json()['form']['department_read'])
        self.assertIsNone(self.client.get(f'/admin/form/{form.id}?format=json').get_json()['department_read'])

    def test_stale_request_does_not_overwrite_newer_acknowledgement(self):
        from app.models import ReviewReadReceipt
        from app.services.review_read_receipts import record_department_read
        from sqlalchemy import create_engine

        form = self._approved()
        self._login(self.dept_admin)
        old_payload = self._payload(form)
        changed_at = form.updated_at + timedelta(seconds=2)
        read_at = datetime.now() + timedelta(seconds=3)

        def concurrent_update(user_id, opened_form):
            # Another connection finishes a newer revision and acknowledges it
            # after this request checked the revision, before it writes.
            engine = create_engine(db.engine.url)
            try:
                with engine.begin() as connection:
                    connection.execute(db.update(LectureForm).where(LectureForm.id == form.id)
                                       .values(updated_at=changed_at))
                    connection.execute(db.insert(ReviewReadReceipt).values(
                        user_id=user_id, form_id=form.id, form_updated_at=changed_at, read_at=read_at))
            finally:
                engine.dispose()
            return record_department_read(user_id, opened_form)

        with mock.patch('app.blueprints.admin.review.record_department_read', side_effect=concurrent_update):
            response = self._mark(form, old_payload)
        self.assertEqual(response.status_code, 409, response.get_json())
        db.session.expire_all()
        receipt = db.session.get(ReviewReadReceipt, (self.dept_admin.id, form.id))
        self.assertEqual(receipt.form_updated_at, changed_at)
        self.assertEqual(receipt.read_at, read_at)

    def test_revision_change_before_first_insert_rolls_back_new_receipt(self):
        from sqlalchemy import create_engine
        from app.services.review_read_receipts import record_department_read

        form = self._approved()
        self._login(self.dept_admin)
        old_payload = self._payload(form)
        changed_at = form.updated_at + timedelta(seconds=2)

        def concurrent_update(user_id, opened_form):
            engine = create_engine(db.engine.url)
            try:
                with engine.begin() as connection:
                    connection.execute(db.update(LectureForm).where(LectureForm.id == form.id)
                                       .values(updated_at=changed_at))
            finally:
                engine.dispose()
            return record_department_read(user_id, opened_form)

        with mock.patch('app.blueprints.admin.review.record_department_read', side_effect=concurrent_update):
            response = self._mark(form, old_payload)
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(self._receipt_count(), 0)

    def test_only_department_reviewers_can_acknowledge(self):
        form = self._approved()
        for actor in (self.group_admin, self.center_admin, self.officer):
            with self.subTest(actor=actor.number):
                self._login(actor)
                response = self._mark(form)
                self.assertEqual(response.status_code, 403, response.get_json())
        self._login(self.group_admin)
        self.assertIsNone(self._listed(form)['department_read'])
        self.assertEqual(self._receipt_count(), 0)

    def test_scope_and_active_identity_are_enforced(self):
        for owner in (self.officer_b, self.dept_admin):
            with self.subTest(owner=owner.number):
                form = self._approved(owner=owner)
                self._login(self.dept_admin)
                self.assertEqual(self._mark(form).status_code, 403)
        form = self._approved()
        self.dept_admin.is_active = False
        db.session.commit()
        self._login(self.dept_admin)
        self.assertIn(self._mark(form).status_code, (302, 401, 403))
        self.assertEqual(self._receipt_count(), 0)

    def test_other_statuses_and_invalid_payloads_are_rejected(self):
        self._login(self.dept_admin)
        for status in ('待审核', '中心已审核', '已驳回'):
            with self.subTest(status=status):
                form = self._make_form(self.officer, status=status)
                self.assertEqual(self._mark(form).status_code, 403)
                self.assertIsNone(self._listed(form)['department_read'])
        form = self._approved()
        for payload in (None, [], 'bad', {}):
            response = self.client.post(f'/admin/api/review/form/{form.id}/read-receipt', json=payload)
            self.assertIn(response.status_code, (400, 409))
        self.assertEqual(self._receipt_count(), 0)

    def test_receipts_are_removed_when_the_reader_or_form_is_deleted(self):
        form = self._approved()
        self._login(self.dept_admin)
        self.assertEqual(self._mark(form).status_code, 200)
        db.session.delete(self.dept_admin)
        db.session.commit()
        self.assertEqual(self._receipt_count(), 0)
        reader = self._make_user('D102', 'student-d102', '管理员', self.dept_a.name,
                                self.group_a1, permissions={'审表_部门'})
        db.session.commit()
        self._login(reader)
        self.assertEqual(self._mark(form).status_code, 200)
        self._login(self.center_admin)
        result = self.client.delete(f'/admin/api/review/form/{form.id}')
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(self._receipt_count(), 0)

    def test_post_requires_csrf_in_real_page_session(self):
        form = self._approved()
        self._login(self.dept_admin)
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            denied = self._mark(form)
            self.assertEqual(denied.status_code, 400)
            self.assertEqual(denied.get_json()['code'], 'csrf_failed')
            page = self.client.get('/admin/review_forms')
            token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)).group(1)
            accepted = self.client.post(f'/admin/api/review/form/{form.id}/read-receipt',
                                        json=self._payload(form), headers={'X-CSRFToken': token})
            self.assertEqual(accepted.status_code, 200, accepted.get_json())
        finally:
            app.config['WTF_CSRF_ENABLED'] = False
