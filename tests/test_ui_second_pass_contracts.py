from pathlib import Path
import re
import unittest


TEMPLATES = Path("app/templates")
CSS = Path("app/static/css/style.css")


class UISecondPassContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = CSS.read_text(encoding="utf-8")
        cls.review = (TEMPLATES / "admin/review_forms.html").read_text(encoding="utf-8")
        cls.people = (TEMPLATES / "admin/manage_departments.html").read_text(encoding="utf-8")
        cls.course = (TEMPLATES / "admin/course_feedback_management.html").read_text(encoding="utf-8")
        cls.statistics = (TEMPLATES / "admin/statistics.html").read_text(encoding="utf-8")
        cls.view_forms = (TEMPLATES / "admin/view_forms.html").read_text(encoding="utf-8")

    def test_review_queue_precedes_collapsible_setup_and_collapses_after_loading(self):
        self.assertIn('id="reviewQueueIntro"', self.review)
        self.assertIn('id="reviewQueueSetup"', self.review)
        self.assertIn('class="review-setup"', self.review)
        self.assertLess(
            self.review.index('id="formsListArea"'),
            self.review.index('id="reviewQueueSetup"'),
        )
        self.assertIn("reviewQueueSetup.open = false", self.review)
        self.assertIn("reviewQueueIntro.hidden = true", self.review)

    def test_dense_page_headers_use_prioritized_overflow_actions(self):
        self.assertIn('class="dropdown page-header__overflow"', self.people)
        self.assertIn('id="peopleMoreActions"', self.people)
        self.assertIn('class="dropdown page-header__overflow"', self.course)
        self.assertIn('id="courseMoreActions"', self.course)
        mobile = self.css[self.css.index('@media (max-width: 768px)'):]
        self.assertNotIn('.page-header .btn { width: 100%; }', mobile)
        self.assertIn('.page-header__actions > .btn', mobile)

    def test_page_header_overflow_restores_focus_when_closed(self):
        script = Path("app/static/js/app-shell.js").read_text(encoding="utf-8")
        self.assertIn(".page-header__overflow", script)
        self.assertIn("hidden.bs.dropdown", script)
        self.assertIn("toggle.focus()", script)

    def test_course_batch_action_is_selection_contextual(self):
        self.assertIn('id="courseBatchToolbar"', self.course)
        self.assertIn('id="selectedCourseCount"', self.course)
        self.assertIn('onclick="clearCourseSelection()"', self.course)
        header = self.course.split('<!-- 搜索和筛选区域 -->', 1)[0]
        self.assertNotIn('id="batchBanBtn"', header)
        self.assertIn('courseBatchToolbar.hidden = selectedCourses.size === 0', self.course)

    def test_statistics_starts_with_metrics_and_uses_module_navigation(self):
        self.assertIn("statistics_nav('overview', can_access_extended_stats)", self.statistics)
        self.assertLess(
            self.statistics.index('aria-label="统计概览"'),
            self.statistics.index('id="leaveManagement"'),
        )
        top = self.statistics.split('id="leaveManagement"', 1)[0]
        self.assertNotIn('secondary_actions=', top)

    def test_empty_people_departments_are_collapsed_but_search_can_expand_them(self):
        self.assertIn('class="collapse department-card__body ${departmentBodyState}"', self.people)
        self.assertIn('const isEmptyDepartment =', self.people)
        self.assertIn('const hasLocatorQuery =', self.people)
        self.assertIn('aria-expanded="${departmentExpanded}"', self.people)

    def test_workspace_can_request_a_compact_empty_state(self):
        empty_state = (TEMPLATES / "partials/_empty_state.html").read_text(encoding="utf-8")
        workspace = (TEMPLATES / "main/workspace.html").read_text(encoding="utf-8")
        self.assertIn("compact=false", empty_state)
        self.assertIn("empty-state--compact", empty_state)
        self.assertIn("compact=true", workspace)
        self.assertIn('.empty-state--compact', self.css)

    def test_contextual_search_pages_can_hide_the_global_search(self):
        topbar = (TEMPLATES / "partials/_topbar.html").read_text(encoding="utf-8")
        app = Path("app/app.py").read_text(encoding="utf-8")
        self.assertIn('{% if show_app_search|default(true) %}', topbar)
        self.assertIn("'show_app_search':", app)
        for endpoint in (
            "admin.manage_departments",
            "admin.course_management",
            "admin.course_feedback_management",
            "admin.statistics",
            "admin.review_forms",
            "admin.view_forms",
            "user.listening_registration",
        ):
            self.assertIn(endpoint, app)

    def test_templates_use_one_bootstrap_icon_system(self):
        forbidden = re.compile(r"\b(?:fas|far|fab)\b|\bfa-[a-z0-9-]+\b")
        for path in TEMPLATES.rglob("*.html"):
            with self.subTest(path=path):
                self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))

    def test_templates_drop_confirmed_bootstrap_four_component_contracts(self):
        forbidden = re.compile(
            r"custom-control|custom-radio|custom-checkbox|"
            r"data-dismiss=|input-group-(?:prepend|append)|\b(?:ml|mr)-[0-9]+\b"
        )
        for path in TEMPLATES.rglob("*.html"):
            with self.subTest(path=path):
                self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))

    def test_templates_do_not_use_bootstrap_four_sr_only_class_token(self):
        class_attribute = re.compile(
            r"(?<![\w-])class\s*=\s*([\"'])(.*?)\1",
            re.DOTALL,
        )
        hits = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            template = path.read_text(encoding="utf-8")
            for match in class_attribute.finditer(template):
                if "sr-only" in match.group(2).split():
                    hits.append(str(path))
        self.assertEqual(hits, [], f"Bootstrap 4 sr-only class token found: {hits}")

    def test_form_list_has_a_readable_mobile_summary_contract(self):
        self.assertIn('class="table table-bordered table-striped form-list-table"', self.view_forms)
        for label in (
            "表单ID",
            "听课人",
            "授课教师",
            "课程名称",
            "听课日期",
            "当前状态",
            "版本数",
            "最后更新",
            "操作",
        ):
            self.assertIn(f'data-label="{label}"', self.view_forms)
        self.assertIn('.form-list-table > tbody > tr:not(.version-history)', self.css)
        self.assertIn('content: attr(data-label)', self.css)


if __name__ == "__main__":
    unittest.main()
