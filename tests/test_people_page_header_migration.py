from pathlib import Path
import unittest


class PeoplePageHeaderMigrationTest(unittest.TestCase):
    TEMPLATE_PATH = Path('app/templates/admin/manage_departments.html')

    @classmethod
    def _page_header_fragment(cls):
        template = cls.TEMPLATE_PATH.read_text(encoding='utf-8')
        start = min(
            index for index in (
                template.find('<div class="page-header">'),
                template.find('<div class="d-flex justify-content-between align-items-center mb-4">'),
            )
            if index >= 0
        )
        end = template.index('<div class="card mb-3">', start)
        return template[start:end]

    def test_header_uses_canonical_structure_and_preserves_actions(self):
        header = self._page_header_fragment()

        self.assertIn('<div class="page-header">', header)
        self.assertIn('<div class="page-header__heading">', header)
        self.assertIn('<div class="page-header__actions">', header)
        self.assertRegex(header, r'<h1\b[^>]*>.*部门与人员.*</h1>')
        self.assertIn('<p>管理部门、小组、信息员与人员流动记录。</p>', header)

        action_markers = (
            'data-bs-target="#addDepartmentModal"',
            'data-bs-target="#addGroupModal"',
            'data-bs-target="#addUserModal"',
            'onclick="openPersonnelMovementModal()"',
            'href="#contacts-import"',
            'href="#contacts-export"',
            '添加部门',
            '添加小组',
            '添加用户',
            '人员流动记录',
            '通讯录导入',
            '通讯录导出',
        )
        for marker in action_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, header)

        self.assertIn('<button type="button" class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#addDepartmentModal">', header)
        self.assertIn('class="dropdown page-header__overflow"', header)
        self.assertIn('id="peopleMoreActions"', header)
        self.assertIn('<button type="button" class="dropdown-item" data-bs-toggle="modal" data-bs-target="#addGroupModal">', header)
        self.assertIn('<button type="button" class="dropdown-item" data-bs-toggle="modal" data-bs-target="#addUserModal">', header)
        self.assertIn('<button type="button" class="dropdown-item" onclick="openPersonnelMovementModal()">', header)
        self.assertIn('<a href="#contacts-import" class="dropdown-item">', header)
        self.assertIn('<a href="#contacts-export" class="dropdown-item">', header)

        self.assertIn('bi bi-person-plus', header)
        self.assertIn('bi bi-clock-history', header)
        self.assertIn('bi bi-upload', header)
        self.assertIn('bi bi-download', header)

        self.assertIn("{% if manage_permission == '超级管理员' %}", header)
        self.assertIn("{% if manage_permission == '超级管理员' or manage_permission == '管理部门' %}", header)

    def test_header_drops_legacy_layout_and_semantic_color_classes(self):
        header = self._page_header_fragment()

        self.assertNotIn('d-flex justify-content-between align-items-center mb-4', header)
        for legacy_class in ('btn-success', 'btn-info', 'btn-warning', 'btn-outline-warning'):
            with self.subTest(legacy_class=legacy_class):
                self.assertNotIn(legacy_class, header)


if __name__ == '__main__':
    unittest.main()
