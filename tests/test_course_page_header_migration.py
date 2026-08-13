from pathlib import Path
import unittest


TEMPLATE = Path("app/templates/admin/course_feedback_management.html")


class CoursePageHeaderMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        content_column_start = cls.template.index('<div class="col-12">')
        header_start = cls.template.index(
            '<div class="', content_column_start + len('<div class="col-12">')
        )
        header_end = cls.template.index('<!--', header_start)
        cls.header = cls.template[header_start:header_end]

    def test_header_uses_canonical_regions_and_mode_aware_copy(self):
        self.assertIn('class="page-header"', self.header)
        self.assertIn('class="page-header__heading"', self.header)
        self.assertIn('class="page-header__actions"', self.header)
        self.assertLess(
            self.header.index('page-header__heading'),
            self.header.index('page-header__actions'),
        )
        self.assertRegex(
            self.header,
            r'<h1\b[^>]*>.*\{\{\s*current_page_title\s*\}\}.*</h1>',
        )
        self.assertIn('{% if is_course_management %}', self.header)
        self.assertIn('维护课程、教师与禁听规则。', self.header)
        self.assertIn('查询课程并完成听课登记。', self.header)

    def test_management_actions_keep_targets_routes_handlers_and_hierarchy(self):
        for snippet in (
            '{% if is_course_management %}',
            'data-bs-toggle="modal"',
            'data-bs-target="#columnSettingsModal"',
            'id="batchBanBtn"',
            'disabled',
            "url_for('admin.download_banned_teacher_import_template')",
            'onclick="openBanTeacherImportModal()"',
            "url_for('admin.registration_statistics')",
            '列设置',
            '批量禁听',
            '下载禁听教师模板',
            '导入禁听教师',
            '听课登记统计',
            '{% endif %}',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.header)

        self.assertEqual(self.header.count('class="btn btn-outline-secondary"'), 4)
        self.assertEqual(self.header.count('class="btn btn-primary"'), 1)

    def test_header_removes_legacy_layout_and_decorative_action_colors(self):
        self.assertNotIn('d-flex justify-content-between align-items-center mb-4', self.header)
        for decorative_class in (
            'btn-success',
            'btn-outline-success',
            'btn-outline-primary',
        ):
            with self.subTest(decorative_class=decorative_class):
                self.assertNotIn(decorative_class, self.header)


if __name__ == "__main__":
    unittest.main()
