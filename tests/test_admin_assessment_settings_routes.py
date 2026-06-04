import unittest

from app.app import app


class AdminAssessmentSettingsRouteTest(unittest.TestCase):
    def test_assessment_settings_routes_keep_existing_endpoints(self):
        expected = {
            'admin.assessment_exemption_settings': '/admin/assessment-exemption-settings',
            'admin.list_assessment_overrides': '/admin/api/assessment-overrides/list',
            'admin.create_assessment_override': '/admin/api/assessment-overrides',
            'admin.delete_assessment_override': '/admin/api/assessment-overrides/<int:override_id>',
            'admin.get_teaching_month_definitions': '/admin/api/teaching-month-definitions',
            'admin.save_teaching_month_definitions': '/admin/api/teaching-month-definitions',
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
