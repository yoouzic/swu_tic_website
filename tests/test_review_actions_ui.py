from pathlib import Path
import unittest


class ReviewActionsUiTest(unittest.TestCase):
    TEMPLATE = Path('app/templates/admin/review_form.html')
    STYLE = Path('app/static/css/style.css')

    def test_review_page_is_flush_at_the_bottom(self):
        template = self.TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('{% block body_class %}review-page{% endblock %}', template)

        style = self.STYLE.read_text(encoding='utf-8')
        self.assertIn('body.review-page .app-content { padding-bottom: 0; }', style)

    def test_action_tray_is_full_bleed_and_uses_the_paper_surface(self):
        style = self.STYLE.read_text(encoding='utf-8')
        self.assertIn('.review-form-actions {', style)
        self.assertIn('background: var(--color-nav) var(--texture-paper)', style)
        self.assertIn('border-radius: 0 0 var(--surface-radius)', style)

    def test_mobile_action_tray_is_a_compact_grid(self):
        style = self.STYLE.read_text(encoding='utf-8')
        self.assertIn(
            'grid-template-columns: minmax(0,1fr) minmax(0,1fr) minmax(0,1.12fr)',
            style,
        )
        self.assertIn('env(safe-area-inset-bottom)', style)
        self.assertIn('flex: none', style)


if __name__ == '__main__':
    unittest.main()
