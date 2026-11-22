"""The work dashboard counts actual actionable logical forms."""
from app.models import LectureForm, db
from app.ui.workspace import load_workspace_snapshot
from tests.review_request_utils import opened_listener_revision, post_opened_review
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


class BusinessWorkspaceFixTest(_ReviewMutationCompatibilityBase):
    def test_department_excludes_historic_and_center_stage_work(self):
        first = self._make_form(self.officer)
        final = self._make_form(self.officer, status='中心已审核')
        final.unique_id = first.id
        db.session.commit()
        self.assertEqual(load_workspace_snapshot(self.dept_admin).pending_forms, 0)
        self.assertEqual(load_workspace_snapshot(self.dept_admin).department_forms, 1)

    def test_group_counts_only_its_scope_and_first_stage(self):
        self._make_form(self.officer)
        self._make_form(self.officer_b)
        self._make_form(self.officer, status='部门已审核')
        snapshot = load_workspace_snapshot(self.group_admin)
        self.assertEqual(snapshot.pending_forms, 1)

    def test_center_permission_on_admin_role_counts_all_actionable_stages(self):
        first = self._make_form(self.officer)
        final = self._make_form(self.officer, status='部门已审核')
        final.unique_id = first.id
        self._make_form(self.officer_b, status='部门已审核')
        self._make_form(self.officer_b)
        db.session.commit()
        # 中心级既可终审部门已审核表单，也可强制直审待审核表单：
        # 逻辑表单1（同 unique_id 最新版=部门已审核）+ 逻辑表单2（部门已审核）+ 逻辑表单3（待审核）。
        self.assertEqual(load_workspace_snapshot(self.center_admin).pending_forms, 3)

    def test_listener_total_is_unique_forms_not_review_versions(self):
        first = self._make_form(self.officer)
        version = self._make_form(self.officer, status='中心已审核')
        version.unique_id = first.id
        db.session.commit()
        self.assertEqual(load_workspace_snapshot(self.officer).total_forms, 1)

    def test_workspace_lists_each_current_personal_rejection_once(self):
        historical = self._make_form(self.officer, status='已驳回')
        accepted = self._make_form(self.officer, status='中心已审核')
        accepted.unique_id = historical.id
        previous = self._make_form(self.officer, status='已驳回', backfill_unique_id=False)
        current = self._make_form(self.officer, status='已驳回')
        current.unique_id = previous.id
        legacy = self._make_form(self.officer, status='已驳回', backfill_unique_id=False)
        other_officer = self._make_form(self.officer_b, status='已驳回')
        db.session.commit()

        self._login(self.officer)
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('你有 2 份听课表被驳回', html)
        self.assertLess(html.index('id="workspaceRejectionsTitle"'), html.index('aria-label="工作指标"'))
        for form in (current, legacy):
            self.assertEqual(html.count(f'href="/user/form/edit/{form.id}"'), 1)
        for form in (historical, previous, other_officer):
            self.assertNotIn(f'href="/user/form/edit/{form.id}"', html)

        self._login(self.dept_admin)
        self.assertNotIn('id="workspaceRejectionsTitle"', self.client.get('/').get_data(as_text=True))

    def test_rejection_reminder_links_to_edit_and_clears_after_resubmission(self):
        form = self._make_form(self.officer)
        reason = '请补充 <课堂互动> 细节。\n请核对听课记录。'
        self._login(self.dept_admin)
        rejection = post_opened_review(
            self.client, f'/admin/api/review/reject/{form.id}', json={'reason': reason},
        )
        self.assertEqual(rejection.status_code, 200)
        rejected = form.get_latest_version()
        edit_path = f'/user/form/edit/{rejected.id}'

        self._login(self.officer)
        self.assertIn(f'href="{edit_path}"', self.client.get('/').get_data(as_text=True))
        edit = self.client.get(edit_path)
        self.assertEqual(edit.status_code, 200)
        html = edit.get_data(as_text=True)
        self.assertIn('请补充 &lt;课堂互动&gt; 细节。\n请核对听课记录。', html)
        self.assertIn('重新提交审核', html)

        payload = self._full_form_data(
            rejected,
            course_feedback='学生先独立分析案例，再分组交流各自的解题方法。教师逐一询问各组的判断依据，并在黑板上整理了不同的解题步骤，最后用一道变式题检查学生是否理解。',
        )
        payload.update(opened_listener_revision(self.client, rejected.id), unique_id=str(rejected.unique_id))
        submitted = self.client.post('/user/submit_form', data=payload)
        self.assertEqual(submitted.status_code, 302)
        latest = rejected.get_latest_version()
        self.assertNotEqual(latest.id, rejected.id)
        self.assertEqual(latest.status, '待审核')
        self.assertEqual(latest.course_feedback, payload['course_feedback'])
        self.assertNotIn('id="workspaceRejectionsTitle"', self.client.get('/').get_data(as_text=True))
