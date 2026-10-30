"""Display-only review stage labels never change the formal status machine."""
from datetime import datetime
from pathlib import Path

from app.models import db
from app.services.review_stage import review_stage_labels
from app.utils.review_permissions import get_user_review_permission, get_user_review_permissions
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


class ReviewStageBadgeTest(_ReviewMutationCompatibilityBase):
    def _approved(self, reviewer, status='部门已审核'):
        form = self._make_form(self.officer, status=status)
        form.reviewer_id = reviewer.id
        form.review_time = datetime.now()
        db.session.commit()
        return form

    def _listed(self, form):
        response = self.client.get('/admin/api/review/forms')
        self.assertEqual(response.status_code, 200, response.get_json())
        return next(row for group in response.get_json()['forms'] for row in group['forms']
                    if row['id'] == form.id)

    def test_bulk_permission_map_matches_single_lookup(self):
        super_admin = self._make_user('S100', 'admin-s100', '超级管理员',
                                      self.dept_a.name, self.group_a1)
        inactive = self._make_user('X100', 'admin-x100', '管理员',
                                   self.dept_a.name, self.group_a1,
                                   permissions={'审表_部门'})
        inactive.is_active = False
        db.session.commit()
        user_ids = [self.group_admin.id, self.dept_admin.id, self.center_admin.id,
                    self.officer.id, super_admin.id, inactive.id]
        bulk = get_user_review_permissions(user_ids)
        self.assertEqual(bulk.get(self.group_admin.id), '审表_小组')
        self.assertEqual(bulk.get(self.dept_admin.id), '审表_部门')
        self.assertEqual(bulk.get(self.center_admin.id), '审表_中心')
        self.assertEqual(bulk.get(super_admin.id), '审表_中心')
        self.assertIsNone(bulk.get(self.officer.id))
        for user_id in user_ids:
            self.assertEqual(bulk.get(user_id), get_user_review_permission(user_id))

    def test_list_detail_and_page_show_who_finished_the_department_stage(self):
        group_form = self._approved(self.group_admin)
        dept_form = self._approved(self.dept_admin)
        self._login(self.dept_admin)

        self.assertEqual(self._listed(group_form)['review_stage'], '小组长已审核')
        self.assertEqual(self._listed(dept_form)['review_stage'], '部长已审核')
        self.assertEqual(group_form.status, '部门已审核')
        self.assertEqual(dept_form.status, '部门已审核')

        detail = self.client.get(f'/admin/api/review/form/{group_form.id}').get_json()['form']
        self.assertEqual(detail['review_stage'], '小组长已审核')
        fragment = self.client.get(f'/admin/form/{group_form.id}?format=json').get_json()
        self.assertIn('小组长已审核', fragment['html'])
        self.assertIn('data-review-stage', fragment['html'])
        page = self.client.get(f'/admin/form/{dept_form.id}')
        self.assertEqual(page.status_code, 200)
        self.assertIn('部长已审核', page.get_data(as_text=True))

    def test_pending_center_rejected_and_unknown_reviewers_have_no_stage(self):
        pending = self._make_form(self.officer, status='待审核')
        center = self._approved(self.center_admin, status='中心已审核')
        rejected = self._approved(self.group_admin, status='已驳回')
        imported = self._approved(self.group_admin)
        imported.reviewer_id = None
        officer_reviewed = self._approved(self.officer)
        db.session.commit()
        self._login(self.dept_admin)

        for form in (pending, center, rejected, imported, officer_reviewed):
            self.assertIsNone(self._listed(form)['review_stage'], form.id)
        for form in (pending, center, rejected):
            detail = self.client.get(f'/admin/api/review/form/{form.id}').get_json()['form']
            self.assertIsNone(detail['review_stage'])

    def test_each_version_keeps_its_own_stage_label(self):
        group_form = self._approved(self.group_admin)
        dept_version = self._approved(self.dept_admin)
        dept_version.unique_id = group_form.logical_id
        db.session.commit()

        labels = review_stage_labels([group_form, dept_version])
        self.assertEqual(labels[group_form.id], '小组长已审核')
        self.assertEqual(labels[dept_version.id], '部长已审核')

        self._login(self.dept_admin)
        response = self.client.get('/admin/api/review/forms').get_json()
        versions = next(group['forms'] for group in response['forms']
                        if any(row['id'] == dept_version.id for row in group['forms']))
        stages = {row['id']: row['review_stage'] for row in versions}
        self.assertEqual(stages[group_form.id], '小组长已审核')
        self.assertEqual(stages[dept_version.id], '部长已审核')

    def test_review_surfaces_render_the_stage_badge(self):
        root = Path(__file__).resolve().parents[1]
        detail = (root / 'app/templates/admin/form_detail_content.html').read_text(encoding='utf-8')
        review_page = (root / 'app/templates/admin/review_form.html').read_text(encoding='utf-8')
        queue = (root / 'app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertIn('data-review-stage', detail)
        self.assertIn('reviewStageBadge', review_page)
        self.assertIn('getReviewStageClass', queue)
