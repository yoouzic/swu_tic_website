from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape


class MetricComponentTest(unittest.TestCase):
    def setUp(self):
        self.environment = Environment(
            loader=FileSystemLoader(Path('app/templates')),
            autoescape=select_autoescape(('html',)),
        )

    def test_metric_has_semantic_tone_and_stable_content_slots(self):
        template = self.environment.from_string(
            '{% from "partials/_metric.html" import metric %}'
            "{{ metric('待审核', 7, tone='attention', extra_class='workspace-metric') }}"
        )
        html = template.render()
        self.assertIn('class="metric metric--card metric--attention workspace-metric"', html)
        self.assertIn('class="metric__value">7</strong>', html)
        self.assertIn('class="metric__label">待审核</span>', html)
        self.assertNotIn('bg-warning', html)

    def test_workspace_uses_the_shared_metric_component(self):
        workspace = Path('app/templates/main/workspace.html').read_text(encoding='utf-8')
        self.assertIn('from "partials/_metric.html" import metric', workspace)
        self.assertIn(
            "metric(item.label, item.value, variant='plain', extra_class='workspace-metric')",
            workspace,
        )


if __name__ == '__main__':
    unittest.main()
