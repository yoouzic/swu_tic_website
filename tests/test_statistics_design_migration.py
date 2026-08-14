import re
import unittest
from pathlib import Path


TEMPLATE = Path("app/templates/admin/statistics.html")


class StatisticsDesignMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.top_section = cls.template.split(
            "{% if can_manage_department_leave %}", 1
        )[0]

    def test_statistics_imports_and_calls_shared_components(self):
        self.assertIn(
            '{% from "partials/_page_header.html" import page_header %}',
            self.template,
        )
        self.assertRegex(self.template, r"\{\{\s*page_header\(")
        self.assertIn(
            '{% from "partials/_metric.html" import metric %}',
            self.template,
        )
        self.assertGreaterEqual(self.template.count("metric("), 5)
        self.assertIn('class="metric-grid', self.template)

    def test_statistics_replaces_small_boxes_with_all_five_metrics(self):
        self.assertNotIn("small-box", self.template)
        for variable in (
            "total_forms",
            "logical_form_count",
            "pending_forms",
            "approved_forms",
            "rejected_forms",
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, self.template)

    def test_statistics_preserves_extended_routes_and_removes_decorative_top_buttons(self):
        for endpoint in (
            "admin.submission_count_stats",
            "admin.review_assessment_stats",
            "admin.department_monthly_assessment_stats",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, self.template)

        self.assertIn('class="statistics-subnav"', self.template)
        self.assertNotIn("secondary_actions=", self.top_section)
        for outline_class in (
            "btn-outline-success",
            "btn-outline-primary",
            "btn-outline-warning",
        ):
            with self.subTest(outline_class=outline_class):
                self.assertNotIn(outline_class, self.top_section)

    def test_statistics_uses_requested_metric_tones(self):
        for tone in ("neutral", "attention", "success", "danger"):
            with self.subTest(tone=tone):
                self.assertRegex(
                    self.template,
                    rf"metric\([^\n]*tone=['\"]{re.escape(tone)}['\"]",
                )

    def test_filter_actions_use_a_scoped_group_without_spacing_utilities(self):
        start = self.template.index('<span class="form-label d-block">操作</span>')
        end = self.template.index('</div>\n                            </div>', start)
        actions = self.template[start:end]

        self.assertIn('class="statistics-filter-actions"', actions)
        self.assertNotIn(' ms-2', actions)
        self.assertIn('class="col-12"', self.template[:start])

        css = Path('app/static/css/style.css').read_text(encoding='utf-8')
        css_start = css.index('.statistics-filter-actions {')
        scoped_css = css[css_start:css_start + 240]
        self.assertIn('display: flex', scoped_css)
        self.assertIn('gap: var(--space-2)', scoped_css)


if __name__ == "__main__":
    unittest.main()
