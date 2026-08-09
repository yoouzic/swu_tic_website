from pathlib import Path
import unittest


class ReviewQueueTemplateTest(unittest.TestCase):
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

    def test_department_page_uses_detail_drawer_not_new_window(self):
        template = Path('app/templates/admin/manage_departments.html').read_text(encoding='utf-8')
        self.assertNotIn("window.open(`/admin/users/${userId}/view`, '_blank')", template)
        self.assertIn('id="userDetailDrawer"', template)


if __name__ == '__main__':
    unittest.main()
