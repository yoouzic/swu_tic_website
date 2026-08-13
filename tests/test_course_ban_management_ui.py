from pathlib import Path
import unittest


TEMPLATES = (
    Path("app/templates/admin/course_feedback_management.html"),
    Path("app/templates/admin/registration_statistics.html"),
)


class CourseBanManagementUiTests(unittest.TestCase):
    def test_existing_bans_are_tracked_with_course_and_record_ids(self):
        for template_path in TEMPLATES:
            template = template_path.read_text(encoding="utf-8")
            with self.subTest(template=template_path.name):
                self.assertIn("let existingCourseBans = new Map();", template)
                self.assertIn("existingCourseBans.set(ban.user_id", template)
                self.assertIn("courseId", template)
                self.assertIn("banId: ban.id", template)

    def test_existing_bans_have_an_explicit_confirmed_delete_action(self):
        for template_path in TEMPLATES:
            template = template_path.read_text(encoding="utf-8")
            with self.subTest(template=template_path.name):
                self.assertIn("async function removeExistingBanUser(userId)", template)
                self.assertIn("window.appConfirm", template)
                self.assertIn("method: 'DELETE'", template)
                self.assertIn("/ban_users/${ban.banId}", template)
                self.assertIn("await loadBannedUsers()", template)
                self.assertIn("移除已有禁听", template)
                self.assertIn('aria-label="移除已有禁听：${user.name}"', template)
                self.assertIn('aria-hidden="true"', template)

    def test_new_selection_action_remains_distinct_and_accessible(self):
        for template_path in TEMPLATES:
            template = template_path.read_text(encoding="utf-8")
            with self.subTest(template=template_path.name):
                self.assertIn("unselectUser(${user.id})", template)
                self.assertIn("取消选择", template)
                self.assertIn('aria-label="取消选择：${user.name}"', template)
                self.assertNotIn(
                    '<button class="btn btn-sm btn-outline-danger" onclick="unselectUser(${user.id})"><i class="bi bi-dash"></i></button>',
                    template,
                )


if __name__ == "__main__":
    unittest.main()
