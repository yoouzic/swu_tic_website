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


if __name__ == '__main__':
    unittest.main()
