from types import SimpleNamespace
import unittest

from app.ui.navigation import build_navigation, resolve_page_label
from app.ui.workspace import WorkspaceSnapshot, build_workspace


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

    def test_manager_does_not_see_super_admin_only_course_entry(self):
        groups = build_navigation(
            self.user('管理员'),
            'main.index',
            review_permission='审表_部门',
            manage_permission='管理部门',
        )
        endpoints = [item['endpoint'] for group in groups for item in group['items']]
        self.assertNotIn('admin.course_feedback_management', endpoints)

    def test_super_admin_sees_global_modules(self):
        groups = build_navigation(self.user('超级管理员'), 'admin.system_management')
        labels = self.labels(groups)
        self.assertIn('系统设置', labels)
        self.assertIn('统计与导出', labels)

    def test_rendered_course_page_keeps_course_navigation_active(self):
        groups = build_navigation(self.user('超级管理员'), 'admin.course_management')
        active = [item['label'] for group in groups for item in group['items'] if item['active']]
        self.assertEqual(active, ['课程与登记'])

    def test_current_page_label_follows_the_active_navigation_item(self):
        groups = build_navigation(
            self.user('管理员'),
            'admin.review_form_page',
            review_permission='审表_部门',
        )
        self.assertEqual(resolve_page_label(groups, 'admin.review_form_page'), '表单审核')

    def test_current_page_label_names_non_navigation_pages_explicitly(self):
        self.assertEqual(resolve_page_label([], 'user.profile'), '个人资料')


class WorkspaceViewModelTest(unittest.TestCase):
    def test_information_officer_gets_personal_primary_action(self):
        user = SimpleNamespace(id=2, name='周雨', role='信息员')
        model = build_workspace(user, WorkspaceSnapshot(total_forms=6, reservation_count=2, draft_saved=True))
        self.assertEqual(model['primary_action']['endpoint'], 'user.submit_form')
        self.assertEqual(model['metrics'][0]['value'], 6)
        self.assertEqual(model['tasks'][0]['label'], '继续未完成的听课表')

    def test_manager_gets_review_queue_action(self):
        user = SimpleNamespace(id=3, name='林老师', role='管理员')
        model = build_workspace(user, WorkspaceSnapshot(pending_forms=7, department_users=18, department_forms=38))
        self.assertEqual(model['title'], '你好，林老师')
        self.assertEqual(model['primary_action']['label'], '处理审核')
        self.assertEqual(model['summary'], '先处理 7 份待审核表单，再查看部门提交情况。')
        self.assertEqual(model['primary_action']['endpoint'], 'admin.review_forms')
        self.assertEqual(model['metrics'][0]['value'], 7)
        self.assertEqual(model['tasks'][0]['endpoint'], 'admin.review_forms')
        self.assertIn('7', model['tasks'][0]['description'])

    def test_manager_without_pending_forms_has_no_review_task(self):
        user = SimpleNamespace(id=3, name='林老师', role='管理员')
        model = build_workspace(user, WorkspaceSnapshot(pending_forms=0, department_users=18, department_forms=38))
        self.assertEqual(model['title'], '你好，林老师')
        self.assertEqual(model['primary_action']['label'], '查看审核队列')
        self.assertEqual(model['summary'], '当前没有待审核表单，可以查看部门提交情况。')
        self.assertEqual(model['tasks'], [])

    def test_super_admin_pending_copy_and_action_follow_real_state(self):
        user = SimpleNamespace(id=4, name='超管测试', role='超级管理员')

        pending = build_workspace(
            user,
            WorkspaceSnapshot(pending_forms=3, total_users=52, total_forms=118),
        )
        self.assertEqual(pending['title'], '你好，超管测试')
        self.assertEqual(pending['summary'], '全局共有 3 份待审核表单，请优先完成审核。')
        self.assertEqual(pending['primary_action']['label'], '处理审核')

        empty = build_workspace(
            user,
            WorkspaceSnapshot(pending_forms=0, total_users=52, total_forms=118),
        )
        self.assertEqual(empty['title'], '你好，超管测试')
        self.assertEqual(empty['summary'], '当前没有待审核表单，可以查看全局提交情况。')
        self.assertEqual(empty['primary_action']['label'], '查看审核队列')

    def test_super_admin_gets_global_system_task(self):
        user = SimpleNamespace(id=4, name='林老师', role='超级管理员')
        model = build_workspace(user, WorkspaceSnapshot(pending_forms=7, total_users=52, total_forms=118, failed_jobs=1))
        self.assertEqual(
            [task['endpoint'] for task in model['tasks']],
            ['admin.review_forms', 'admin.system_management'],
        )
        self.assertIn('7', model['tasks'][0]['description'])
        self.assertIn('1', model['tasks'][1]['description'])


if __name__ == '__main__':
    unittest.main()
