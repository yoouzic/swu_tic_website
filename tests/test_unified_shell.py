from pathlib import Path
import unittest


FULL_PAGE_TEMPLATES = (
    'admin/auto_review.html',
    'admin/system_management.html',
    'admin/admin_dashboard.html',
    'admin/super_admin_dashboard.html',
    'main/admin_index.html',
    'main/manager_index.html',
    'main/user_index.html',
)


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
        self.assertIn('--bs-primary: var(--color-accent)', css)
        self.assertIn('--bs-info: #7a6b55', css)
        self.assertIn('--bs-gradient: none', css)
        self.assertIn('background-color: var(--color-accent) !important', css)
        self.assertIn('background-color: #e8e2de !important', css)
        self.assertIn('overflow-wrap: anywhere', css)
        self.assertNotIn('#667eea', css)
        self.assertNotIn('linear-gradient', css)

    def test_shell_script_never_rewrites_link_targets(self):
        script = Path('app/static/js/app-shell.js').read_text(encoding='utf-8')
        self.assertNotIn('target', script)
        self.assertIn('data-shell-toggle', script)

    def test_every_legacy_full_page_uses_base_without_duplicate_document(self):
        for relative_path in FULL_PAGE_TEMPLATES:
            template = Path('app/templates', relative_path).read_text(encoding='utf-8')
            self.assertIn('{% extends "base.html" %}', template, relative_path)
            self.assertNotIn('<!DOCTYPE html>', template, relative_path)
            self.assertNotIn('<nav class="navbar', template, relative_path)
            self.assertNotIn("setAttribute('target', '_blank')", template, relative_path)

    def test_templates_do_not_embed_forbidden_palette(self):
        forbidden = (
            '#667eea', '#764ba2', '#4facfe', '#00f2fe', '#43e97b', '#38f9d7',
            '#007bff', '#0d6efd', '#2196f3', '#e3f2fd',
            'rgba(0, 123, 255', 'rgba(13, 110, 253',
        )
        for path in Path('app/templates').rglob('*.html'):
            template = path.read_text(encoding='utf-8')
            for color in forbidden:
                self.assertNotIn(color, template, str(path))

    def test_system_settings_center_declares_four_business_tabs(self):
        template = Path('app/templates/admin/system_management.html').read_text(encoding='utf-8')
        for name in ('teaching', 'assessment', 'automation', 'imports'):
            self.assertIn(f'data-settings-panel="{name}"', template)
        self.assertIn('js/settings-center.js', template)

    def test_templates_do_not_use_browser_dialogs_or_new_tabs(self):
        forbidden = ('target="_blank"', 'window.open(', 'window.prompt(', 'window.confirm(', 'window.alert(', 'prompt(', 'confirm(', 'alert(')
        for path in Path('app/templates').rglob('*.html'):
            template = path.read_text(encoding='utf-8')
            for token in forbidden:
                if token in ('prompt(', 'confirm(', 'alert('):
                    self.assertNotIn(f' {token}', template, f'{token} in {path}')
                else:
                    self.assertNotIn(token, template, f'{token} in {path}')

    def test_review_template_does_not_shadow_shell_main_id(self):
        template = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        css = Path('app/static/css/style.css').read_text(encoding='utf-8')
        self.assertNotIn('id="mainContent"', template)
        self.assertIn('review-filter-row', template)
        self.assertIn('.review-filter-row .row > .col-md-2', css)

    def test_successful_inline_actions_do_not_reload_the_document(self):
        dashboard = Path('app/templates/admin/dashboard.html').read_text(encoding='utf-8')
        my_forms = Path('app/templates/user/my_forms.html').read_text(encoding='utf-8')
        self.assertNotIn('location.reload(', dashboard)
        self.assertNotIn('location.reload(', my_forms)


if __name__ == '__main__':
    unittest.main()
