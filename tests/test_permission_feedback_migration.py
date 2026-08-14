from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADMIN = (ROOT / "app" / "blueprints" / "admin.py").read_text(encoding="utf-8")
AUTOMATION_PERMISSIONS = (
    ROOT / "app" / "review_automation" / "permissions.py"
).read_text(encoding="utf-8")
AUTOMATION_ROUTES = (
    ROOT / "app" / "review_automation" / "routes.py"
).read_text(encoding="utf-8")
REVIEW_FORMS = (
    ROOT / "app" / "templates" / "admin" / "review_forms.html"
).read_text(encoding="utf-8")
REVIEW_FORM = (
    ROOT / "app" / "templates" / "admin" / "review_form.html"
).read_text(encoding="utf-8")


class PermissionFeedbackMigrationTests(unittest.TestCase):
    def test_admin_permission_denials_use_resource_aware_messages(self):
        self.assertIn("build_forbidden_message", ADMIN)
        self.assertGreaterEqual(
            ADMIN.count("'message': build_forbidden_message("),
            40,
        )
        self.assertNotRegex(
            ADMIN,
            r"flash\(\s*['\"][^'\"]*(?:权限|无权|无权限|审核)[^'\"]*['\"]",
        )
        self.assertNotRegex(
            ADMIN,
            r"['\"]message['\"]\s*:\s*[f]?['\"][^'\"]*(?:无权|无权限|没有审核权限|不具有审表权限)[^'\"]*['\"]",
        )
        self.assertNotRegex(
            ADMIN,
            r"build_forbidden_message\([^\n]*(?:unauthorized_ids|original_form\.status)",
        )

    def test_automation_permission_contracts_keep_legacy_json_shape(self):
        self.assertEqual(
            len(re.findall(r"_error\(\s*build_forbidden_message\(", AUTOMATION_PERMISSIONS)),
            3,
        )
        self.assertGreaterEqual(
            AUTOMATION_ROUTES.count("'message': build_forbidden_message("),
            4,
        )
        self.assertRegex(
            AUTOMATION_ROUTES,
            r"'code': 'forbidden',\s*'message':\s*build_forbidden_message\(",
        )
        self.assertNotIn("需要审表_中心权限", AUTOMATION_PERMISSIONS)
        self.assertNotIn("需要审表_中心权限", AUTOMATION_ROUTES)
        self.assertNotIn("forbidden_json", AUTOMATION_ROUTES)

    def test_review_form_exposes_only_product_permission_label(self):
        self.assertIn(
            "'permission_label': get_review_permission_presentation(permission)['permission_label']",
            ADMIN,
        )
        self.assertIn("userPermissionLabel = data.permission_label", REVIEW_FORM)
        self.assertIn("textContent = userPermissionLabel", REVIEW_FORM)
        self.assertNotIn("textContent = userPermission;", REVIEW_FORM)

    def test_review_queue_denials_do_not_render_status_or_bare_permission_copy(self):
        self.assertIn("无法打开“表单审核”", REVIEW_FORMS)
        self.assertIn("当前账号没有该功能权限。", REVIEW_FORMS)
        self.assertIn("无法审核“表单”：当前账号没有审核该表单的权限。", REVIEW_FORMS)
        self.assertNotIn("您不具有审表权限", REVIEW_FORMS)
        self.assertNotIn("您无权限审核处于", REVIEW_FORMS)
        self.assertNotRegex(
            REVIEW_FORMS,
            r"showPermissionWarning\([^\n]*form\.status",
        )

    def test_auth_password_and_business_rule_messages_remain_outside_migration(self):
        self.assertIn("密码验证失败，无法解散部门", ADMIN)
        self.assertIn("密码验证失败，无法解散小组", ADMIN)
        self.assertIn("请假规则请在统计分析页为当前教学周发起", ADMIN)


if __name__ == "__main__":
    unittest.main()
