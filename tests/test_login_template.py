from pathlib import Path
import unittest


class LoginTemplateTest(unittest.TestCase):
    def test_login_form_allows_non_numeric_local_accounts(self):
        template = Path('app/templates/auth/login.html').read_text(encoding='utf-8')

        self.assertNotIn('学号格式不正确', template)
        self.assertNotIn(r'/^\d+$/', template)

    def test_login_and_public_pages_use_public_shell_without_blue_purple_gradients(self):
        for path in ('app/templates/auth/login.html', 'app/templates/main/public_index.html'):
            template = Path(path).read_text(encoding='utf-8')
            self.assertIn('{% extends "base.html" %}', template)
            self.assertNotIn('linear-gradient', template)
            self.assertNotIn('#667eea', template)
            self.assertNotIn('<nav class="navbar', template)

    def test_login_page_has_one_primary_submit_intent(self):
        template = Path('app/templates/auth/login.html').read_text(encoding='utf-8')
        self.assertEqual(template.count('type="submit"'), 1)
        self.assertIn('登录工作台', template)


if __name__ == '__main__':
    unittest.main()
