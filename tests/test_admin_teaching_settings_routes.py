import unittest

from app.app import app


class AdminTeachingSettingsRouteTest(unittest.TestCase):
    def test_teaching_settings_routes_keep_existing_endpoints(self):
        expected = {
            'admin.get_teaching_settings': '/admin/api/settings/teaching',
            'admin.update_teaching_settings': '/admin/api/settings/teaching',
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
