from types import SimpleNamespace
import unittest

from app.ui.navigation import build_navigation


class NavigationTest(unittest.TestCase):
    def user(self, role, *, authenticated=True, active=True):
        return SimpleNamespace(
            id=1,
            role=role,
            is_authenticated=authenticated,
            is_active=active,
        )

    def items(self, groups):
        return [item for group in groups for item in group['items']]

    def item(self, groups, label):
        return next(item for item in self.items(groups) if item['label'] == label)

    def test_active_authenticated_roles_see_the_listening_assistant(self):
        for role in ('信息员', '管理员', '超级管理员', '教师'):
            with self.subTest(role=role):
                groups = build_navigation(self.user(role), 'user.submit_form')
                assistant = self.item(groups, '听课助手')

                self.assertEqual(assistant['endpoint'], 'user.submit_form')
                self.assertTrue(assistant['active'])
                self.assertTrue(assistant['icon'])

    def test_assistant_active_state_is_separate_from_registration_records(self):
        groups = build_navigation(self.user('信息员'), 'user.my_forms')

        registration = self.item(groups, '听课与填报')
        assistant = self.item(groups, '听课助手')

        self.assertTrue(registration['active'])
        self.assertFalse(assistant['active'])

    def test_registration_item_keeps_its_role_scope(self):
        groups = build_navigation(self.user('教师'), 'user.my_forms')
        labels = [item['label'] for item in self.items(groups)]

        self.assertIn('听课助手', labels)
        self.assertNotIn('听课与填报', labels)

    def test_anonymous_user_does_not_get_authenticated_navigation(self):
        groups = build_navigation(
            self.user('游客', authenticated=False, active=False),
            'user.submit_form',
        )

        self.assertEqual(groups, [])


if __name__ == '__main__':
    unittest.main()
