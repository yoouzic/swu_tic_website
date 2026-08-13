from pathlib import Path
import unittest

from app.app import app


TEMPLATE = Path('app/templates/admin/manage_groups.html')


class ManageGroupsInteractionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding='utf-8')

    @staticmethod
    def _function_section(source, start_marker, end_marker):
        start = source.index(start_marker)
        end = source.index(end_marker, start)
        return source[start:end]

    def test_group_list_refresh_helper_uses_same_route_and_replaces_only_server_html(self):
        for marker in (
            'id="groupsList"',
            'async function refreshGroupList()',
            'fetch(window.location.href',
            "credentials: 'same-origin'",
            'response.ok',
            'DOMParser',
            "const current = document.getElementById('groupsList')",
            "const next = parsed.getElementById('groupsList')",
            'document.importNode(next, true)',
            'current.replaceWith',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.template)

    def test_successful_group_writes_await_local_list_refresh_and_never_reload(self):
        self.assertNotIn('location.reload()', self.template)

        disband = self._function_section(
            self.template,
            'function disbandGroup',
            'function bindFormEvents',
        )
        submit = self._function_section(
            self.template,
            'function submitForm',
            'function showPasswordConfirm',
        )
        self.assertIn('await refreshGroupList()', disband)
        self.assertIn('await refreshGroupList()', submit)
        self.assertEqual(
            self.template.count("showAlert('操作已完成，但列表刷新失败，请重试。', 'warning')"),
            2,
        )
        self.assertIn("type === 'warning' ? 'alert-warning'", self.template)

    def test_existing_group_write_urls_modal_and_password_confirmation_remain(self):
        for marker in (
            'fetch(`/admin/api/groups/${id}`',
            'fetch(`/admin/api/groups/${id}/disband`',
            "submitForm('/admin/api/groups'",
            "submitForm(`/admin/api/groups/${id}`",
            "'PUT'",
            'new bootstrap.Modal(document.getElementById(\'editGroupModal\'))',
            'bootstrap.Modal.getInstance(document.getElementById(modalId)).hide()',
            'showPasswordConfirm(',
            'id="confirmDeleteBtn"',
            'body: JSON.stringify({ password: password })',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.template)

    def test_existing_group_routes_remain_registered(self):
        expected = {
            ('GET', '/admin/manage_groups'),
            ('GET', '/admin/api/groups'),
            ('POST', '/admin/api/groups'),
            ('GET', '/admin/api/groups/<int:group_id>'),
            ('PUT', '/admin/api/groups/<int:group_id>'),
            ('POST', '/admin/api/groups/<int:group_id>/disband'),
            ('DELETE', '/admin/api/groups/<int:group_id>'),
        }
        registered = {
            (method, rule.rule)
            for rule in app.url_map.iter_rules()
            for method in rule.methods
            if method in {'GET', 'POST', 'PUT', 'DELETE'}
        }
        self.assertTrue(expected.issubset(registered))


if __name__ == '__main__':
    unittest.main()
