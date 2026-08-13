from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape


class PageHeaderComponentTest(unittest.TestCase):
    def setUp(self):
        self.environment = Environment(
            loader=FileSystemLoader(Path('app/templates')),
            autoescape=select_autoescape(('html',)),
        )

    def render(self, expression):
        return self.environment.from_string(
            '{% from "partials/_page_header.html" import page_header %}' + expression
        ).render()

    def test_existing_positional_primary_action_remains_compatible(self):
        html = self.render("{{ page_header('标题', '说明', '开始', '/start') }}")
        self.assertIn('<h1>标题</h1>', html)
        self.assertIn('class="btn btn-primary" href="/start"', html)

    def test_secondary_actions_and_metadata_have_canonical_regions(self):
        html = self.render(
            "{{ page_header('统计与导出', '查看工作进展', secondary_actions=["
            "{'label': '交表数量', 'href': '/stats/submissions'}, "
            "{'label': '月度考评', 'href': '/stats/monthly'}], "
            "metadata=['数据截至今日', '只读']) }}"
        )
        self.assertIn('class="page-header__actions"', html)
        self.assertEqual(html.count('class="btn btn-outline-secondary"'), 2)
        self.assertIn('class="page-header__meta"', html)
        self.assertIn('<span>数据截至今日</span>', html)


if __name__ == '__main__':
    unittest.main()
