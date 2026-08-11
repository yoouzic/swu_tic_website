from pathlib import Path
import re
import shutil
import subprocess
import unittest


SETTINGS_TEMPLATE = Path('app/templates/admin/_settings_automation.html')
SYSTEM_TEMPLATE = Path('app/templates/admin/system_management.html')
AUTOMATION_SCRIPT = Path('app/static/js/automation-center.js')
STYLE_SHEET = Path('app/static/css/style.css')


class AutomationSettingsTemplateTest(unittest.TestCase):
    def test_settings_panel_covers_services_datasets_rules_history_and_notice(self):
        template = SETTINGS_TEMPLATE.read_text(encoding='utf-8')
        required_fragments = (
            'data-automation-center',
            'automationServiceStatus',
            'Redis',
            'Celery Worker',
            'DeepSeek',
            'schoolScheduleDataset',
            'classMappingDataset',
            'personalScheduleDataset',
            'automationDatasetHistory',
            'automationCoverageSummary',
            'automationRuleList',
            'automationRuleRevision',
            '完整表单和相关证据会发送到 DeepSeek 外部 API',
            'API Key',
            'missing',
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, template)

    def test_settings_panel_has_accessible_feedback_and_two_stage_controls(self):
        template = SETTINGS_TEMPLATE.read_text(encoding='utf-8')
        script = AUTOMATION_SCRIPT.read_text(encoding='utf-8')
        self.assertIn('aria-live="polite"', template)
        self.assertIn('automationSettingsFeedback', template)
        self.assertIn('automationPreviewModal', template)
        self.assertIn('data-automation-preview', template)
        self.assertIn('data-automation-activate', template)
        self.assertIn('data-bs-toggle="modal"', template)
        self.assertIn('上传并预览', template)
        self.assertIn('确认启用', template)
        self.assertIn('下载模板', template)
        self.assertIn('configured', script)
        self.assertIn('missing', template + script)

    def test_system_management_loads_automation_script_after_settings_script(self):
        template = SYSTEM_TEMPLATE.read_text(encoding='utf-8')
        settings_index = template.index('js/settings-center.js')
        automation_index = template.index('js/automation-center.js')
        self.assertLess(settings_index, automation_index)

    def test_automation_script_uses_real_bootstrap_modal_api_and_safe_dom_updates(self):
        script = AUTOMATION_SCRIPT.read_text(encoding='utf-8')
        self.assertIn("settings:shown", script)
        self.assertIn("textContent", script)
        self.assertIn("window.bootstrap", script)
        self.assertIn("getOrCreateInstance", script)
        self.assertIn(".show()", script)
        self.assertIn(".hide()", script)
        self.assertIn("/admin/api/automation/health", script)
        self.assertIn("/admin/api/automation/datasets", script)
        self.assertIn("/admin/api/automation/rules", script)
        self.assertIn("/preview", script)
        self.assertIn("/activate", script)
        self.assertNotIn('.innerHTML', script)

    def test_automation_initializes_only_once_after_ancestor_settings_event(self):
        script = AUTOMATION_SCRIPT.read_text(encoding='utf-8')
        self.assertIn("const settingsPanel = document.querySelector('[data-settings-panel=\"automation\"]');", script)
        self.assertIn("settingsPanel.addEventListener('settings:shown', init);", script)
        self.assertNotIn("if (!panel.closest('[hidden]')) init();", script)

        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the DOM event contract test')
        harness = r'''
const fs = require('fs');
const vm = require('vm');
class EventTarget {
  constructor() { this.listeners = {}; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatchEvent(event) { for (const fn of (this.listeners[event.type] || [])) fn(event); }
}
const settingsPanel = new EventTarget();
settingsPanel.dataset = {};
const automationPanel = new EventTarget();
automationPanel.dataset = {};
automationPanel.querySelector = () => null;
automationPanel.querySelectorAll = () => [];
settingsPanel.querySelector = (selector) => selector === '[data-automation-center]' ? automationPanel : null;
let fetchCalls = 0;
const document = { querySelector: (selector) => selector === '[data-settings-panel="automation"]' ? settingsPanel : null };
const context = { document, fetch: async () => { fetchCalls += 1; return {ok: true, json: async () => ({})}; }, console, setTimeout };
vm.runInNewContext(fs.readFileSync('app/static/js/automation-center.js', 'utf8'), context);
if (fetchCalls !== 0) throw new Error(`auto-initialized:${fetchCalls}`);
settingsPanel.dispatchEvent({type: 'settings:shown'});
settingsPanel.dispatchEvent({type: 'settings:shown'});
setTimeout(() => {
  if (fetchCalls !== 3) throw new Error(`initialized-${fetchCalls}-times`);
  process.stdout.write('ancestor-event-once');
}, 0);
'''
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'ancestor-event-once')

    def test_rule_editor_matches_revision_api_contract(self):
        template = SETTINGS_TEMPLATE.read_text(encoding='utf-8')
        script = AUTOMATION_SCRIPT.read_text(encoding='utf-8')
        for fragment in ('automationRuleEnabled', 'automationRuleSeverity', 'automationRuleParameters', 'automationRuleChangeReason'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, template)
        for fragment in ("enabled", "severity", "parameters", "change_reason"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, script)
        self.assertNotIn('rule.risk_level', script)
        self.assertNotIn('rule.risk', script)
        self.assertNotIn('value="none"', template)

    def test_health_key_coverage_download_and_preview_lifecycle_contracts(self):
        template = SETTINGS_TEMPLATE.read_text(encoding='utf-8')
        script = AUTOMATION_SCRIPT.read_text(encoding='utf-8')
        self.assertIn('data-coverage="total"', template)
        self.assertIn('services.deepseek', script)
        self.assertIn('renderCoverage', script)
        for fragment in ('Blob', 'createObjectURL', 'revokeObjectURL', 'download'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, script)
        for column in ('semester', 'listener_number', 'student_id', 'course_title', 'weeks', 'weekday', 'start_period', 'end_period'):
            with self.subTest(column=column):
                self.assertIn(column, script)
        preview_buttons = re.findall(r'<button[^>]*data-automation-preview[^>]*>', template)
        self.assertTrue(preview_buttons)
        self.assertTrue(all('data-bs-toggle' not in button for button in preview_buttons))

    def test_automation_script_has_no_browser_dialogs_new_tabs_or_reload(self):
        script = AUTOMATION_SCRIPT.read_text(encoding='utf-8')
        forbidden = (
            r'(?<![\w$.])(?:window\.)?alert\s*\(',
            r'(?<![\w$.])(?:window\.)?confirm\s*\(',
            r'(?<![\w$.])(?:window\.)?prompt\s*\(',
            r'window\.open',
            r'target=["\']_blank["\']',
            r'location\.reload\s*\(',
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, script))

    def test_automation_styles_define_settings_status_and_dataset_panels(self):
        styles = STYLE_SHEET.read_text(encoding='utf-8')
        for fragment in ('automation-center', 'automation-service-status', 'automation-dataset-card', 'automation-coverage'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, styles)


if __name__ == '__main__':
    unittest.main()
