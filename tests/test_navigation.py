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

    def test_authorized_submission_roles_see_the_listening_assistant(self):
        for role in ('信息员', '管理员'):
            with self.subTest(role=role):
                groups = build_navigation(self.user(role), 'user.submit_form', submission_allowed=True)
                assistant = self.item(groups, '填写听课表')

                self.assertEqual(assistant['endpoint'], 'user.submit_form')
                self.assertTrue(assistant['active'])
                self.assertTrue(assistant['icon'])

    def test_assistant_active_state_is_separate_from_registration_records(self):
        groups = build_navigation(self.user('信息员'), 'user.my_forms')

        registration = self.item(groups, '听课与填报')
        assistant = self.item(groups, '填写听课表')

        self.assertTrue(registration['active'])
        self.assertFalse(assistant['active'])

    def test_submit_form_only_highlights_the_listening_assistant(self):
        groups = build_navigation(self.user('信息员'), 'user.submit_form')

        registration = self.item(groups, '听课与填报')
        assistant = self.item(groups, '填写听课表')

        self.assertTrue(assistant['active'])
        self.assertFalse(registration['active'])

    def test_edit_form_only_highlights_listening_registration(self):
        groups = build_navigation(self.user('信息员'), 'user.edit_form')

        registration = self.item(groups, '听课与填报')
        assistant = self.item(groups, '填写听课表')

        self.assertFalse(assistant['active'])
        self.assertTrue(registration['active'])

    def test_unpermitted_users_get_course_lookup_and_read_only_records(self):
        for role in ('管理员', '教师'):
            with self.subTest(role=role):
                groups = build_navigation(self.user(role), 'user.course_lookup', submission_allowed=False)
                labels = [item['label'] for item in self.items(groups)]
                self.assertIn('课程查询', labels)
                self.assertIn('我的记录', labels)
                self.assertNotIn('填写听课表', labels)
                self.assertNotIn('听课与填报', labels)
                self.assertTrue(self.item(groups, '课程查询')['active'])

    def test_super_has_no_personal_fill_entry_even_with_supplied_capability(self):
        groups = build_navigation(self.user('超级管理员'), submission_allowed=True)
        labels = [item['label'] for item in self.items(groups)]
        self.assertNotIn('填写听课表', labels)
        self.assertNotIn('听课与填报', labels)
        self.assertNotIn('课程查询', labels)

    def test_permission_revocation_changes_manager_navigation(self):
        user = self.user('管理员')
        allowed = build_navigation(user, submission_allowed=True)
        denied = build_navigation(user, submission_allowed=False)
        self.assertIn('听课与填报', [item['label'] for item in self.items(allowed)])
        self.assertNotIn('听课与填报', [item['label'] for item in self.items(denied)])
        self.assertIn('课程查询', [item['label'] for item in self.items(denied)])

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

        self.assertEqual(groups, [])

    def test_legacy_null_active_user_gets_listening_assistant(self):
        groups = build_navigation(
            self.user('信息员', authenticated=True, active=None),
            'user.submit_form',
        )

        labels = [item['label'] for item in self.items(groups)]

        self.assertIn('填写听课表', labels)


if __name__ == '__main__':
    unittest.main()
