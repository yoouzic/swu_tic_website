from types import SimpleNamespace
import unittest

from app.ui.navigation import build_navigation


class WorkspaceNavigationTest(unittest.TestCase):
    def user(self, role):
        return SimpleNamespace(id=1, role=role)

    def labels(self, groups):
        return [item['label'] for group in groups for item in group['items']]

    def test_information_officer_only_sees_personal_modules(self):
        groups = build_navigation(self.user('信息员'), 'main.index')
        labels = self.labels(groups)
        self.assertIn('今日工作', labels)
        self.assertIn('听课与填报', labels)
        self.assertNotIn('表单审核', labels)
        self.assertNotIn('系统设置', labels)

    def test_manager_modules_follow_explicit_permissions(self):
        groups = build_navigation(
            self.user('管理员'),
            'admin.review_forms',
            review_permission='审表_部门',
            manage_permission='管理部门',
        )
        labels = self.labels(groups)
        self.assertIn('表单审核', labels)
        self.assertIn('人员与部门', labels)
        active = [item for group in groups for item in group['items'] if item['active']]
        self.assertEqual([item['label'] for item in active], ['表单审核'])

    def test_super_admin_sees_global_modules(self):
        groups = build_navigation(self.user('超级管理员'), 'admin.system_management')
        labels = self.labels(groups)
        self.assertIn('系统设置', labels)
        self.assertIn('统计与导出', labels)


if __name__ == '__main__':
    unittest.main()
