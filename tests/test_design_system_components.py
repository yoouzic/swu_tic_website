from pathlib import Path
import unittest


class DesignSystemComponentTest(unittest.TestCase):
    def setUp(self):
        self.css = Path('app/static/css/style.css').read_text(encoding='utf-8')

    def test_foundational_tokens_cover_spacing_type_overlay_and_layout(self):
        for token in (
            '--color-bg-subtle:',
            '--color-status-success:',
            '--color-status-warning:',
            '--color-status-danger:',
            '--space-1:',
            '--space-6:',
            '--font-size-sm:',
            '--font-size-xl:',
            '--shadow-overlay:',
            '--topbar-height:',
            '--content-max:',
            '--z-sticky:',
            '--z-drawer:',
        ):
            self.assertIn(token, self.css)

    def test_page_header_and_metric_have_canonical_shared_styles(self):
        for selector in (
            '.page-header__actions',
            '.page-header__meta',
            '.metric-grid',
            '.metric__value',
            '.metric__label',
            '.surface-panel',
        ):
            self.assertIn(selector, self.css)

    def test_keyboard_focus_is_visible_for_interactive_controls(self):
        self.assertIn(':where(a,button,input,select,textarea,[tabindex]):focus-visible', self.css)


if __name__ == '__main__':
    unittest.main()
