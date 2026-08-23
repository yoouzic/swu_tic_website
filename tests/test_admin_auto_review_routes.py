import ast
import unittest
from pathlib import Path

from app.app import app


class AdminAutoReviewRouteTest(unittest.TestCase):
    RETAINED = {
        'admin.auto_review_page': '/admin/auto_review',
        'admin.batch_auto_check': '/admin/api/review/batch_auto_check',
        'admin.batch_auto_check_preview': '/admin/api/review/batch_auto_check/preview',
        'admin.get_auto_check_status': '/admin/api/review/auto_check/status',
    }

    RETIRED = {
        'admin.auto_review_settings',
        'admin.auto_review_upload',
        'admin.auto_review_feedback_run',
        'admin.auto_review_download',
    }

    def test_auto_review_page_kept_and_legacy_endpoints_retired(self):
        rules_by_endpoint = {
            rule.endpoint: rule.rule
            for rule in app.url_map.iter_rules()
        }

        for endpoint, rule in self.RETAINED.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(rules_by_endpoint.get(endpoint), rule)

        for endpoint in self.RETIRED:
            with self.subTest(endpoint=endpoint):
                self.assertNotIn(endpoint, rules_by_endpoint)


    def test_auto_review_engine_only_imported_by_allowed_review_module(self):
        allowed = {'app/blueprints/admin/review.py'}
        found = set()
        for path in Path('app').rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == 'AutoReviewEngine':
                            found.add(str(path).replace('\\', '/'))
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == 'AutoReviewEngine':
                            found.add(str(path).replace('\\', '/'))
        self.assertEqual(found, allowed)


if __name__ == '__main__':
    unittest.main()
