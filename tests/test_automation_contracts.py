import unittest

from app.app import app


class AutomationBootstrapTest(unittest.TestCase):
    def test_default_configuration_is_safe_and_explicit(self):
        self.assertEqual(app.config['DEEPSEEK_BASE_URL'], 'https://api.deepseek.com')
        self.assertEqual(app.config['DEEPSEEK_MODEL'], 'deepseek-v4-flash')
        self.assertTrue(app.config['DEEPSEEK_THINKING_ENABLED'])
        self.assertFalse(app.config['CELERY_TASK_ALWAYS_EAGER'])
        self.assertNotIn('DEEPSEEK_API_KEY', app.config.get('AUTOMATION_PUBLIC_CONFIG', {}))

    def test_automation_blueprint_is_registered(self):
        self.assertIn('review_automation.health', {rule.endpoint for rule in app.url_map.iter_rules()})


if __name__ == '__main__':
    unittest.main()
