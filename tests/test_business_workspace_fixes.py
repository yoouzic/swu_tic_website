"""The work dashboard counts actual actionable logical forms."""
from app.models import LectureForm, db
from app.ui.workspace import load_workspace_snapshot
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


class BusinessWorkspaceFixTest(_ReviewMutationCompatibilityBase):
    def test_department_excludes_historic_and_center_stage_work(self):
        first = self._make_form(self.officer)
        final = self._make_form(self.officer, status='部门已审核')
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

    def test_center_permission_on_admin_role_counts_only_final_stage(self):
        first = self._make_form(self.officer)
        final = self._make_form(self.officer, status='部门已审核')
        final.unique_id = first.id
        self._make_form(self.officer_b, status='部门已审核')
        self._make_form(self.officer_b)
        db.session.commit()
        self.assertEqual(load_workspace_snapshot(self.center_admin).pending_forms, 2)

    def test_listener_total_is_unique_forms_not_review_versions(self):
        first = self._make_form(self.officer)
        version = self._make_form(self.officer, status='中心已审核')
        version.unique_id = first.id
        db.session.commit()
        self.assertEqual(load_workspace_snapshot(self.officer).total_forms, 1)
