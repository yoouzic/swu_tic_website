from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
STYLE = (ROOT / "app" / "static" / "css" / "style.css").read_text(
    encoding="utf-8"
)
SHELL_JS = (ROOT / "app" / "static" / "js" / "app-shell.js").read_text(
    encoding="utf-8"
)


class UITargetedAuditContractTests(unittest.TestCase):
    def template(self, relative_path):
        return (TEMPLATES / relative_path).read_text(encoding="utf-8")

    def test_mobile_record_cells_group_their_complete_value(self):
        source = self.template("user/_records_panel.html")
        self.assertGreaterEqual(source.count('class="record-cell__value"'), 15)
        self.assertIn(
            ".form-list-table > tbody > tr:not(.version-history) > td > .record-cell__value",
            STYLE,
        )
        self.assertRegex(
            STYLE,
            r"\.record-cell__value\s*\{[^}]*grid-column:\s*2",
        )

    def test_review_workspace_only_splits_after_a_queue_exists(self):
        source = self.template("admin/review_forms.html")
        self.assertIn("reviewWorkspace.classList.toggle('is-queue-ready'", source)
        self.assertIn("formGroups.length > 0", source)
        self.assertIn(".review-layout.is-queue-ready", STYLE)
        self.assertIn(".review-layout.is-queue-ready .review-preview", STYLE)
        self.assertIn(
            "@media (max-width: 1024px) { .review-layout,.review-layout.is-queue-ready",
            STYLE,
        )
        self.assertIn(
            ".review-preview,.review-layout.is-queue-ready .review-preview { display: none; }",
            STYLE,
        )

    def test_review_setup_summary_groups_copy_and_responds_to_container_width(self):
        source = self.template("admin/review_forms.html")
        self.assertIn('class="review-setup__summary-copy"', source)
        self.assertIn("container-name: review-setup", STYLE)
        self.assertIn("@container review-setup", STYLE)

    def test_statistics_subnav_has_edge_affordance_and_active_visibility(self):
        partial = self.template("partials/_statistics_nav.html")
        self.assertIn('class="statistics-subnav-frame', partial)
        self.assertIn("data-statistics-subnav", partial)
        self.assertIn("statistics-subnav-frame--overflowing", SHELL_JS)
        self.assertIn("scrollIntoView", SHELL_JS)
        self.assertIn("inline: 'center'", SHELL_JS)
        self.assertIn("active.offsetLeft", SHELL_JS)
        self.assertIn("nav.scrollTo", SHELL_JS)
        self.assertIn("document.fonts.ready", SHELL_JS)
        self.assertIn("window.setTimeout(revealActive, 250)", SHELL_JS)

    def test_statistics_scope_utilities_live_with_the_scope_selector(self):
        cases = {
            "admin/review_assessment_stats.html": ("btnSelectAll", "btnClearAll"),
            "admin/submission_count_stats.html": ("btnSelectAll", "btnClearAll"),
            "admin/department_monthly_assessment_stats.html": (
                "btnSelectAllDepartments",
                "btnClearDepartments",
            ),
        }
        for filename, button_ids in cases.items():
            source = self.template(filename)
            with self.subTest(template=filename):
                self.assertIn('class="scope-selection-toolbar"', source)
                action_start = source.index("statistics-filter-actions")
                action_excerpt = source[action_start : action_start + 900]
                for button_id in button_ids:
                    self.assertNotIn(f'id="{button_id}"', action_excerpt)
                    self.assertRegex(
                        source,
                        rf'class="scope-selection-toolbar"[\s\S]*?id="{button_id}"',
                    )
        self.assertNotRegex(
            STYLE,
            r"\.statistics-filter-actions \.btn[^{}]*\{[^}]*width:\s*100%",
        )

    def test_review_assessment_download_is_part_of_import_workflow(self):
        source = self.template("admin/review_assessment_stats.html")
        page_header_call = source.split("statistics_nav", 1)[0]
        self.assertNotIn("下载考评导入模板", page_header_call)
        import_start = source.index("手动导入考评")
        self.assertIn("下载考评导入模板", source[import_start : import_start + 1200])

    def test_review_form_names_the_object_and_orders_endpoint_actions(self):
        source = self.template("admin/review_form.html")
        self.assertIn('id="reviewObjectTitle"', source)
        self.assertIn("getElementById('reviewObjectTitle').textContent", source)
        self.assertIn("data.form.course_title", source)
        reset_at = source.index("重置表单")
        reject_at = source.index("驳回", reset_at)
        submit_at = source.index("提交审核", reject_at)
        self.assertLess(reset_at, reject_at)
        self.assertLess(reject_at, submit_at)

    def test_group_management_uses_current_page_and_danger_contracts(self):
        source = self.template("admin/manage_groups.html")
        for marker in (
            'class="page-header',
            'class="group-card',
            'class="group-card__header',
            'class="group-member-list',
            'class="activity-empty',
            'aria-labelledby="addGroupModalLabel"',
            'aria-labelledby="editGroupModalLabel"',
            'autocomplete="current-password"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        self.assertNotIn("btn-warning", source)
        self.assertNotIn("btn-success", source)
        self.assertNotRegex(source, r'style="[^\"]*max-height')

    def test_large_business_template_styles_are_page_scoped(self):
        cases = {
            "admin/course_feedback_management.html": "course-feedback-page",
            "admin/manage_departments.html": "people-management-page",
        }
        broad_selectors = (
            ".table th",
            ".badge",
            ".card-header h6",
            ".card-body .small",
            ".user-list",
        )
        for filename, page_class in cases.items():
            source = self.template(filename)
            with self.subTest(template=filename):
                self.assertIn(
                    f'{{% block body_class %}}{page_class}{{% endblock %}}', source
                )
                style_blocks = "\n".join(re.findall(r"<style>([\s\S]*?)</style>", source))
                for selector in broad_selectors:
                    self.assertNotRegex(
                        style_blocks,
                        rf"(?m)^\s*{re.escape(selector)}\s*\{{",
                    )


if __name__ == "__main__":
    unittest.main()
