from pathlib import Path
import unittest


class LoginTemplateTest(unittest.TestCase):
    def test_login_form_allows_non_numeric_local_accounts(self):
        template = Path('app/templates/auth/login.html').read_text(encoding='utf-8')

        self.assertNotIn('学号格式不正确', template)
        self.assertNotIn(r'/^\d+$/', template)


if __name__ == '__main__':
    unittest.main()
