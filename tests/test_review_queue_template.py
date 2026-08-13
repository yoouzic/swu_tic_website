from pathlib import Path
import re
import unittest


class ReviewQueueTemplateTest(unittest.TestCase):
    ADMIN_HIGH_FREQUENCY_TEMPLATES = (
        Path('app/templates/admin/review_forms.html'),
        Path('app/templates/admin/review_form.html'),
        Path('app/templates/admin/manage_departments.html'),
    )

    def test_review_queue_stays_in_current_tab_and_restores_context(self):
        template = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertNotIn("window.open(`/admin/review/form/${formId}`, '_blank')", template)
        self.assertIn("js/review-queue.js", template)
        script = Path('app/static/js/review-queue.js').read_text(encoding='utf-8')
        self.assertIn('reviewQueueState', script)
        self.assertIn('window.location.assign', script)

    def test_full_review_exposes_queue_return_action(self):
        template = Path('app/templates/admin/review_form.html').read_text(encoding='utf-8')
        self.assertIn('data-review-return', template)
        self.assertIn('id="draftSaveStatus"', template)

    def test_full_review_has_one_return_action_and_one_draft_status_region(self):
        template = Path('app/templates/admin/review_form.html').read_text(encoding='utf-8')
        self.assertEqual(template.count('data-review-return href='), 1)
        self.assertEqual(template.count('id="draftSaveStatus"'), 1)
        self.assertNotIn('id="reviewDraftStatus"', template)
        self.assertNotIn("getElementById('reviewDraftStatus')", template)

    def test_full_review_uses_bootstrap_five_modal_contracts(self):
        template = Path('app/templates/admin/review_form.html').read_text(encoding='utf-8')
        self.assertNotIn('data-dismiss="modal"', template)
        self.assertNotIn('class="close"', template)
        self.assertNotIn("$('#submitConfirmModal').modal", template)
        self.assertNotIn("$('#resultModal').modal", template)
        self.assertIn("bootstrap.Modal.getOrCreateInstance(document.getElementById('submitConfirmModal'))", template)
        self.assertIn("bootstrap.Modal.getOrCreateInstance(document.getElementById('resultModal'))", template)

    def test_full_review_does_not_shadow_shell_main_id(self):
        template = Path('app/templates/admin/review_form.html').read_text(encoding='utf-8')
        self.assertNotIn('id="mainContent"', template)
        self.assertIn('id="reviewFormContent"', template)
        self.assertIn("getElementById('reviewFormContent')", template)

    def test_reject_action_uses_the_clicked_button_context(self):
        template = Path('app/templates/admin/review_form.html').read_text(encoding='utf-8')
        self.assertIn('event.currentTarget', template)

    def test_admin_high_frequency_templates_do_not_use_browser_dialogs_or_reload(self):
        forbidden_patterns = (
            r'(?<![\w$.])(?:window\.)?open\s*\(',
            r'(?<![\w$.])(?:window\.)?alert\s*\(',
            r'(?<![\w$.])(?:window\.)?confirm\s*\(',
            r'(?<![\w$.])(?:window\.)?prompt\s*\(',
            r'(?:window\.)?location\.reload\s*\(',
            r'target="_blank"',
        )
        for path in self.ADMIN_HIGH_FREQUENCY_TEMPLATES:
            template = path.read_text(encoding='utf-8')
            for pattern in forbidden_patterns:
                with self.subTest(template=path, pattern=pattern):
                    self.assertIsNone(re.search(pattern, template), f'{path}: {pattern}')

    def test_automation_queue_has_no_legacy_auto_review_label_or_decision_controls(self):
        template = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertNotIn('一键自动审核', template)
        self.assertNotIn('autoReviewNormalBtn', template)
        self.assertNotIn('autoReviewForceBtn', template)

    def test_full_review_has_no_stray_middle_dot(self):
        template = Path('app/templates/admin/review_form.html').read_text(encoding='utf-8')
        self.assertNotIn('</div>·', template)
        self.assertIsNone(re.search(r'(?m)^\s*·\s*$', template))

    def test_admin_high_frequency_pages_expose_feedback_and_confirm_modal(self):
        expected = {
            Path('app/templates/admin/review_forms.html'): 'reviewQueueFeedback',
            Path('app/templates/admin/review_form.html'): 'reviewFormFeedback',
            Path('app/templates/admin/manage_departments.html'): 'peopleManagementFeedback',
        }
        for path, feedback_id in expected.items():
            template = path.read_text(encoding='utf-8')
            with self.subTest(template=path):
                self.assertIn(f'id="{feedback_id}"', template)
                self.assertIn('aria-live="polite"', template)
                self.assertRegex(template, r'id="[^"]*(?:Confirm|confirm)[^"]*"[^>]*class="modal fade"')
                self.assertIn('aria-labelledby=', template)

    def test_department_page_uses_detail_drawer_not_new_window(self):
        template = Path('app/templates/admin/manage_departments.html').read_text(encoding='utf-8')
        self.assertNotIn("window.open(`/admin/users/${userId}/view`, '_blank')", template)
        self.assertIn('id="userDetailDrawer"', template)

    def test_review_detail_uses_a_full_height_mobile_content_contract(self):
        queue = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        detail = Path('app/templates/admin/form_detail_content.html').read_text(encoding='utf-8')
        self.assertIn('modal-dialog modal-lg modal-fullscreen-md-down', queue)
        self.assertIn('class="container-fluid form-detail-content"', detail)
        self.assertIn('class="table table-bordered form-detail-table"', detail)

    def test_review_selection_checkboxes_have_accessible_names(self):
        template = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertIn('id="selectAll" aria-label="选择本页全部可审核表单"', template)
        self.assertIn('aria-label="选择 ${escapeHtml(latestForm.listener_name || \'当前信息员\')} 的最新表单"', template)
        self.assertIn('aria-label="选择小组 ${escapeHtml(groupName)}"', template)
        self.assertIn('aria-label="选择部门 ${escapeHtml(deptName)}"', template)
        self.assertIn('aria-label="选择 ${escapeHtml(version.listener_name || \'当前信息员\')} 的版本', template)

    def test_review_queue_uses_compact_summary_list_instead_of_a_wide_table(self):
        template = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertIn('class="review-queue__list"', template)
        self.assertIn('class="review-queue__select-all"', template)
        self.assertIn('class="review-item"', template)
        self.assertIn('class="review-item__summary"', template)
        self.assertIn('class="review-item__meta"', template)
        self.assertIn('class="review-item__badges"', template)
        self.assertIn('class="review-item__actions"', template)
        self.assertNotIn('class="table review-queue__table"', template)
        self.assertNotIn('<th width="15%">课程</th>', template)

    def test_review_queue_uses_bootstrap_five_modal_and_form_controls(self):
        template = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertNotIn('data-dismiss="modal"', template)
        self.assertNotIn('class="close"', template)
        self.assertNotIn("$('#permissionWarningModal').modal", template)
        self.assertNotIn('custom-control', template)
        self.assertNotIn('custom-radio', template)
        self.assertNotIn('custom-checkbox', template)
        self.assertIn("bootstrap.Modal.getOrCreateInstance(document.getElementById('permissionWarningModal'))", template)
        self.assertIn('class="form-check-input selected-export-status-checkbox"', template)


if __name__ == '__main__':
    unittest.main()
