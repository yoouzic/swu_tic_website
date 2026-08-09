from pathlib import Path
import unittest


class UnifiedShellTest(unittest.TestCase):
    def test_base_uses_shared_shell_and_local_assets(self):
        template = Path('app/templates/base.html').read_text(encoding='utf-8')
        self.assertIn("partials/_sidebar.html", template)
        self.assertIn("partials/_topbar.html", template)
        self.assertIn("js/app-shell.js", template)
        self.assertNotIn("setAttribute('target', '_blank')", template)
        self.assertNotIn('code.jquery.com', template)

    def test_design_tokens_are_charcoal_and_brick(self):
        css = Path('app/static/css/style.css').read_text(encoding='utf-8')
        self.assertIn('--color-nav: #292d2b', css)
        self.assertIn('--color-accent: #9b493c', css)
        self.assertNotIn('#667eea', css)
        self.assertNotIn('linear-gradient', css)

    def test_shell_script_never_rewrites_link_targets(self):
        script = Path('app/static/js/app-shell.js').read_text(encoding='utf-8')
        self.assertNotIn('target', script)
        self.assertIn('data-shell-toggle', script)


if __name__ == '__main__':
    unittest.main()
