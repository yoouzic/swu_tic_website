import unittest

from app.app import app


class AdminAutoReviewRouteTest(unittest.TestCase):
    def test_auto_review_routes_keep_existing_endpoints(self):
        expected = {
            'admin.auto_review_page': '/admin/auto_review',
            'admin.auto_review_settings': '/admin/api/auto_review/settings',
            'admin.auto_review_upload': '/admin/api/auto_review/upload',
            'admin.auto_review_feedback_run': '/admin/api/auto_review/feedback_run',
            'admin.auto_review_download': '/admin/auto_review/download',
        }
        rules_by_endpoint = {
            rule.endpoint: rule.rule
            for rule in app.url_map.iter_rules()
        }

        for endpoint, rule in expected.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(rules_by_endpoint.get(endpoint), rule)


if __name__ == '__main__':
    unittest.main()
