from pathlib import Path
import unittest


class ReferenceDrawerUiTest(unittest.TestCase):
    TEMPLATE = Path('app/templates/admin/review_form.html')
    STYLE = Path('app/static/css/style.css')

    def test_match_method_labels_are_not_rendered(self):
        template = self.TEMPLATE.read_text(encoding='utf-8')
        self.assertNotIn('describeScheduleMatchType', template)
        self.assertNotIn('describeContactMatch', template)
        self.assertNotIn('badge bg-light text-dark border', template)

    def test_drawer_exposes_resize_contract(self):
        template = self.TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('id="referenceDrawerResizer"', template)
        self.assertIn('role="separator"', template)
        self.assertIn('aria-orientation="vertical"', template)
        self.assertIn('REFERENCE_DRAWER_WIDTH_KEY', template)
        self.assertIn("handle.setAttribute('aria-label', label)", template)

        style = self.STYLE.read_text(encoding='utf-8')
        self.assertIn('--reference-drawer-width: 500px', style)
        self.assertIn('scrollbar-gutter: stable', style)
        self.assertIn('padding: 16px 14px 24px 24px', style)
        self.assertIn('.reference-drawer-resizer__grip', style)
        self.assertIn('width: 34px; height: 68px', style)
        self.assertIn('.reference-drawer-body::-webkit-scrollbar-thumb', style)

    def test_drawer_uses_shared_paper_texture(self):
        style = self.STYLE.read_text(encoding='utf-8')
        self.assertIn(
            'background: var(--color-nav) var(--texture-paper)',
            style,
        )

    def test_drawer_has_collapsed_reopen_widget(self):
        template = self.TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('id="referenceDrawerReopen"', template)
        self.assertIn('reopen.hidden = isOpen', template)

        style = self.STYLE.read_text(encoding='utf-8')
        self.assertIn('.reference-drawer-reopen', style)
        self.assertIn('writing-mode: vertical-rl', style)


if __name__ == '__main__':
    unittest.main()
