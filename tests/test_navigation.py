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

    def test_submit_form_only_highlights_the_listening_assistant(self):
        groups = build_navigation(self.user('信息员'), 'user.submit_form')

        registration = self.item(groups, '听课与填报')
        assistant = self.item(groups, '听课助手')

        self.assertTrue(assistant['active'])
        self.assertFalse(registration['active'])

    def test_edit_form_only_highlights_listening_registration(self):
        groups = build_navigation(self.user('信息员'), 'user.edit_form')

        registration = self.item(groups, '听课与填报')
        assistant = self.item(groups, '听课助手')

        self.assertFalse(assistant['active'])
        self.assertTrue(registration['active'])

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

    def test_inactive_authenticated_user_does_not_get_listening_assistant(self):
        groups = build_navigation(
            self.user('信息员', authenticated=True, active=False),
            'user.submit_form',
        )

        labels = [item['label'] for item in self.items(groups)]

        self.assertNotIn('听课助手', labels)

    def test_legacy_null_active_user_gets_listening_assistant(self):
        groups = build_navigation(
            self.user('信息员', authenticated=True, active=None),
            'user.submit_form',
        )

        labels = [item['label'] for item in self.items(groups)]

        self.assertIn('听课助手', labels)


if __name__ == '__main__':
    unittest.main()
