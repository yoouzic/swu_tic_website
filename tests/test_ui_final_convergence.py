from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
STYLE = ROOT / "app" / "static" / "css" / "style.css"


class UIFinalConvergenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.style = STYLE.read_text(encoding="utf-8")
        cls.review_queue = (TEMPLATES / "admin" / "review_forms.html").read_text(
            encoding="utf-8"
        )
        cls.review_form = (TEMPLATES / "admin" / "review_form.html").read_text(
            encoding="utf-8"
        )
        cls.my_forms = (TEMPLATES / "user" / "_records_panel.html").read_text(
            encoding="utf-8"
        )

    def test_statistics_pages_share_one_subnavigation_component(self):
        partial = TEMPLATES / "partials" / "_statistics_nav.html"
        self.assertTrue(partial.exists())
        if not partial.exists():
            return

        partial_text = partial.read_text(encoding="utf-8")
        for endpoint in (
            "admin.statistics",
            "admin.submission_count_stats",
            "admin.review_assessment_stats",
            "admin.department_monthly_assessment_stats",
        ):
            self.assertIn(endpoint, partial_text)

        for filename in (
            "statistics.html",
            "submission_count_stats.html",
            "review_assessment_stats.html",
            "department_monthly_assessment_stats.html",
        ):
            source = (TEMPLATES / "admin" / filename).read_text(encoding="utf-8")
            with self.subTest(template=filename):
                self.assertIn(
                    '{% from "partials/_statistics_nav.html" import statistics_nav %}',
                    source,
                )
                self.assertRegex(source, r"\{\{\s*statistics_nav\(")

    def test_legacy_statistics_subpages_use_shared_page_header_and_toolbar(self):
        for filename in (
            "submission_count_stats.html",
            "review_assessment_stats.html",
            "department_monthly_assessment_stats.html",
        ):
            source = (TEMPLATES / "admin" / filename).read_text(encoding="utf-8")
            with self.subTest(template=filename):
                self.assertIn(
                    '{% from "partials/_page_header.html" import page_header %}',
                    source,
                )
                self.assertRegex(source, r"\{\{\s*page_header\(")
                self.assertIn("statistics-tool-grid", source)
                self.assertNotIn("btn-success", source)
                self.assertNotRegex(source, r"<h[23][^>]*>\s*<i[^>]*></i>")

    def test_registration_statistics_keeps_course_context_but_uses_page_header(self):
        source = (TEMPLATES / "admin" / "registration_statistics.html").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '{% from "partials/_page_header.html" import page_header %}', source
        )
        self.assertRegex(source, r"\{\{\s*page_header\(")
        self.assertIn("admin.course_management", source)
        self.assertNotIn("statistics_nav(", source)

    def test_statistics_search_fields_have_explicit_labels(self):
        cases = (
            ("review_assessment_stats.html", "userSearchInput"),
            ("submission_count_stats.html", "userSearchInput"),
            ("department_monthly_assessment_stats.html", "departmentSearchInput"),
        )
        for filename, control_id in cases:
            source = (TEMPLATES / "admin" / filename).read_text(encoding="utf-8")
            with self.subTest(template=filename, control=control_id):
                self.assertIn(f'for="{control_id}"', source)

    def test_statistics_modals_have_associated_titles(self):
        expected = {
            "review_assessment_stats.html": (
                ("detailModal", "detailTitle"),
                ("importPreviewModal", "importPreviewTitle"),
            ),
            "submission_count_stats.html": (
                ("detailModal", "detailTitle"),
                ("rewardDetailModal", "rewardDetailTitle"),
                ("formDetailModal", "formDetailTitle"),
                ("snapshotModal", "snapshotTitle"),
            ),
            "department_monthly_assessment_stats.html": (
                ("snapshotModal", "snapshotTitle"),
                ("monthSettingsModal", "monthSettingsTitle"),
            ),
        }
        for filename, pairs in expected.items():
            source = (TEMPLATES / "admin" / filename).read_text(encoding="utf-8")
            for modal_id, title_id in pairs:
                with self.subTest(template=filename, modal=modal_id):
                    self.assertRegex(
                        source,
                        rf'id="{modal_id}"[^>]*aria-labelledby="{title_id}"',
                    )
                    self.assertIn(f'id="{title_id}"', source)

    def test_statistics_reset_submits_the_cleared_filter_state(self):
        source = (TEMPLATES / "admin" / "statistics.html").read_text(
            encoding="utf-8"
        )
        reset_body = source.split("function resetFilters()", 1)[1].split("}", 1)[0]
        self.assertIn("requestSubmit()", reset_body)

    def test_review_filters_are_container_aware_not_bootstrap_column_bound(self):
        start = self.review_queue.index('<div class="row mb-4 review-filter-row">')
        end = self.review_queue.index('id="userSelectionArea"', start)
        filters = self.review_queue[start:end]
        self.assertIn('class="review-filter-grid"', filters)
        for token in ("col-md-2", "col-md-4"):
            self.assertNotIn(token, filters)

        self.assertIn("container-type: inline-size", self.style)
        self.assertIn("@container review-filters", self.style)
        self.assertIn("@container review-filters (min-width: 20rem)", self.style)
        self.assertIn(".review-filter-grid", self.style)

    def test_review_queue_modals_have_associated_titles(self):
        for modal_id, title_id in (
            ("statisticsModal", "statisticsModalTitle"),
            ("formDetailModal", "formDetailModalTitle"),
            ("selectedFormsExportModal", "selectedFormsExportTitle"),
            ("formTagDefinitionModal", "formTagDefinitionTitle"),
        ):
            with self.subTest(modal=modal_id):
                self.assertRegex(
                    self.review_queue,
                    rf'id="{modal_id}"[^>]*aria-labelledby="{title_id}"',
                )
                self.assertIn(f'id="{title_id}"', self.review_queue)

    def test_review_form_uses_sections_and_truthful_action_hierarchy(self):
        self.assertIn('class="review-form-surface"', self.review_form)
        self.assertGreaterEqual(self.review_form.count('class="review-form-section"'), 6)
        self.assertIn('class="review-form-actions"', self.review_form)
        for legacy in (
            "btn-success btn-lg",
            "btn-danger btn-lg",
            "btn-secondary btn-lg",
            "card-header bg-success",
            "btn-xs",
            "form-group",
        ):
            self.assertNotIn(legacy, self.review_form)
        self.assertNotIn("<style>", self.review_form)

    def test_reference_drawer_has_keyboard_and_aria_contracts(self):
        self.assertRegex(
            self.review_form,
            r'<button[^>]+id="referenceDrawerHeaderToggle"',
        )
        self.assertIn('aria-controls="referenceDataDrawer"', self.review_form)
        self.assertIn('aria-expanded="false"', self.review_form)
        self.assertNotRegex(
            self.review_form,
            r'<div[^>]+class="reference-drawer-header"[^>]+onclick=',
        )
        self.assertIn("event.key === 'Escape'", self.review_form)
        self.assertIn("referenceDrawerReturnFocus.focus()", self.review_form)

    def test_scoring_rows_expose_mobile_labels_and_stack_contract(self):
        self.assertIn('class="table review-scoring-table"', self.review_form)
        for label in ("评分原因", "部门扣分", "个人扣分", "操作"):
            self.assertIn(f'data-label="{label}"', self.review_form)
        self.assertIn(".review-scoring-table", self.style)
        self.assertIn("content: attr(data-label)", self.style)

    def test_my_forms_table_becomes_mobile_records_without_changing_desktop_table(self):
        self.assertIn("form-list-table responsive-record-table", self.my_forms)
        for label in ("表单ID", "听课日期", "课程名称", "当前状态", "操作"):
            self.assertIn(f'data-label="{label}"', self.my_forms)
        self.assertIn(".form-list-table > thead", self.style)

    def test_permission_feedback_supports_resource_action_and_reason(self):
        helper = (ROOT / "app" / "utils" / "permission_feedback.py").read_text(
            encoding="utf-8"
        )
        admin = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((ROOT / "app" / "blueprints" / "admin").glob("*.py"))
        )
        user = (ROOT / "app" / "blueprints" / "user.py").read_text(
            encoding="utf-8"
        )

        self.assertRegex(
            helper,
            r"def build_forbidden_message\([^)]*action=['\"]打开['\"]",
        )
        self.assertRegex(
            helper,
            r"def forbidden_json\([^)]*action=['\"]打开['\"]",
        )
        self.assertNotIn("flash('您没有权限", admin)
        self.assertNotIn("'message': '您没有权限", admin)
        self.assertNotIn("您没有权限编辑此表单", user)

    def test_server_flash_close_uses_the_app_message_contract(self):
        partial = (TEMPLATES / "partials" / "_flash_messages.html").read_text(
            encoding="utf-8"
        )
        shell = (ROOT / "app" / "static" / "js" / "app-shell.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("data-app-message-dismiss", partial)
        self.assertNotIn('data-bs-dismiss="alert"', partial)
        self.assertIn("[data-app-message-dismiss]", shell)

    def test_admin_templates_do_not_keep_the_confirmed_bootstrap_4_tokens(self):
        admin_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (TEMPLATES / "admin").glob("*.html")
        )
        for legacy in (
            "form-group",
            "btn-xs",
            "badge-warning",
            "badge-success",
            "badge-danger",
            "thead-light",
        ):
            with self.subTest(token=legacy):
                self.assertNotIn(legacy, admin_sources)


if __name__ == "__main__":
    unittest.main()
