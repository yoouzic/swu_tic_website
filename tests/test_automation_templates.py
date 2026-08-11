from pathlib import Path
import json
import re
import shutil
import subprocess
import unittest


SETTINGS_TEMPLATE = Path('app/templates/admin/_settings_automation.html')
SYSTEM_TEMPLATE = Path('app/templates/admin/system_management.html')
AUTOMATION_SCRIPT = Path('app/static/js/automation-center.js')
STYLE_SHEET = Path('app/static/css/style.css')
REVIEW_QUEUE_TEMPLATE = Path('app/templates/admin/review_forms.html')
AUTO_RESULTS_TEMPLATE = Path('app/templates/admin/auto_review_results.html')


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

    def test_automation_batch_settings_expose_three_review_modes_and_live_progress(self):
        template = SETTINGS_TEMPLATE.read_text(encoding='utf-8')
        script = AUTOMATION_SCRIPT.read_text(encoding='utf-8')
        controls = re.findall(
            r'<input[^>]*name="review_mode"[^>]*value="(rules_only|llm_only|combined)"[^>]*>',
            template,
        )
        self.assertEqual(controls, ['rules_only', 'llm_only', 'combined'])
        self.assertEqual(len(re.findall(r'name="review_mode"[^>]*checked', template)), 1)
        for label in ('仅运行规则', '仅运行 DeepSeek', '规则与 DeepSeek 联合运行'):
            with self.subTest(label=label):
                self.assertIn(label, template)
        for fragment in (
            'review_mode',
            'usesDeepSeek',
            'external_transfer_acknowledged',
            'processed_count',
            'target_form_count',
            'clear_count',
            'review_count',
            'high_risk_count',
            'unknown_count',
            'failed_count',
            'cache_count',
            'http_attempts',
            'aria-live',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, template + script)

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

    def test_review_queue_exposes_four_categories_coverage_and_evidence_drawer(self):
        template = REVIEW_QUEUE_TEMPLATE.read_text(encoding='utf-8')
        for fragment in (
            'riskFilter',
            '无明显风险',
            '建议复核',
            '高风险疑似假表',
            '系统无法判断',
            'data-automation-coverage="none"',
            'data-automation-coverage="basic"',
            'data-automation-coverage="complete"',
            '缺失',
            '基础',
            '完整',
            '批量智能检查',
            'evidence-section-schedule',
            'evidence-section-rule',
            'evidence-section-text',
            'evidence-section-history',
            'evidence-section-llm',
            'evidence-section-system',
            'automationSuggestedComment',
            '系统仅提供分类和证据，最终通过或驳回由人工审核人执行。',
            'reviewQueueFeedback',
            'reviewQueueConfirmModal',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, template)
        self.assertNotIn('一键自动审核', template)
        self.assertNotIn('autoReviewNormalBtn', template)
        self.assertNotIn('autoReviewForceBtn', template)

    def test_review_queue_batch_flow_requires_preview_and_literal_external_ack(self):
        template = REVIEW_QUEUE_TEMPLATE.read_text(encoding='utf-8')
        for fragment in (
            '/admin/api/automation/batches/preview',
            '/admin/api/automation/batches',
            '/admin/api/automation/batches/',
            'external_transfer_acknowledged',
            'checked',
            'cache_reusable',
            'processable',
            'data-batch-preview',
            'setTimeout',
            'reviewQueueBatchPoll',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, template)
        self.assertRegex(template, r'transferAck[^\n]*===\s*true')
        self.assertNotIn('force_all', template)
        self.assertNotIn('location.reload', template)

    def test_review_queue_ack_is_a_literal_gate_for_batch_creation(self):
        template = REVIEW_QUEUE_TEMPLATE.read_text(encoding='utf-8')
        show_start = template.index('function showAutoReviewDecision')
        show_end = template.index('function batchAutoReview', show_start)
        show_flow = template[show_start:show_end]
        start_start = template.index('function startBatchReview')
        start_end = template.index('function renderAutomationBatchProgress', start_start)
        start_flow = template[start_start:start_end]

        self.assertIn('startButton.disabled = true;', show_flow)
        self.assertIn("ack.addEventListener('change'", show_flow)
        self.assertIn('ack.checked === true', show_flow)
        self.assertRegex(show_flow, r'if\s*\(!transferAck\)\s*\{')
        self.assertLess(show_flow.index('if (!transferAck)'), show_flow.index('.hide()'))
        self.assertIn('return;', show_flow)
        self.assertIn('llm_enabled: true', start_flow)
        self.assertIn('external_transfer_acknowledged: true', start_flow)
        self.assertNotIn('llm_enabled: transferAck', start_flow)

        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the literal acknowledgement execution test')
        function_start = template.index('function startBatchReview')
        brace_start = template.index('{', function_start)
        depth = 0
        function_end = None
        for index in range(brace_start, len(template)):
            if template[index] == '{':
                depth += 1
            elif template[index] == '}':
                depth -= 1
                if depth == 0:
                    function_end = index + 1
                    break
        self.assertIsNotNone(function_end)
        function_source = template[function_start:function_end]
        harness = f'''
const startBatchReview = new Function(
  'setAutomationBatchButtonState',
  'setReviewQueueFeedback',
  'fetch',
  'pollAutomationBatch',
  {json.dumps(function_source)} + '; return startBatchReview;'
)((...args) => {{ }}, (...args) => {{ }}, (url, options) => {{
  globalThis.calls.push({{url, body: JSON.parse(options.body)}});
  return Promise.resolve({{ok: true, json: async () => ({{batch_id: 'batch-1'}})}});
}}, (...args) => {{ }});
globalThis.calls = [];
const button = {{disabled: false, textContent: ''}};
(async () => {{
  startBatchReview([1], button, false);
  await new Promise(resolve => setTimeout(resolve, 0));
  if (calls.length !== 0) throw new Error('unchecked acknowledgement created a batch');
  startBatchReview([1], button, true);
  await new Promise(resolve => setTimeout(resolve, 0));
  if (calls.length !== 1) throw new Error(`checked acknowledgement calls: ${{calls.length}}`);
  if (calls[0].body.llm_enabled !== true || calls[0].body.external_transfer_acknowledged !== true) {{
    throw new Error('batch creation did not force literal external-transfer acknowledgement');
  }}
  process.stdout.write('literal-ack-gate');
}})().catch(error => {{ console.error(error); process.exit(1); }});
'''
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'literal-ack-gate')

    def test_evidence_maps_text_rule_handlers_in_both_views(self):
        queue = REVIEW_QUEUE_TEMPLATE.read_text(encoding='utf-8')
        results = AUTO_RESULTS_TEMPLATE.read_text(encoding='utf-8')
        for template in (queue, results):
            with self.subTest(template=template[:30]):
                self.assertIn('assessment.rule_version', template)
                self.assertIn('required_prefix', template)
                self.assertIn('regex_pattern', template)
                self.assertIn('min_length', template)
                self.assertIn('automationEvidenceSource', template)
                self.assertIn('automationEvidenceSource(finding, assessment)', template)
        self.assertRegex(queue, r"grouped\[automationEvidenceSource\(finding, assessment\)\]")
        self.assertRegex(results, r"grouped\[automationEvidenceSource\(finding, assessment\)\]")

    def test_settings_dataset_cards_support_accessible_drag_and_drop(self):
        template = SETTINGS_TEMPLATE.read_text(encoding='utf-8')
        script = AUTOMATION_SCRIPT.read_text(encoding='utf-8')
        self.assertEqual(template.count('data-automation-drop-zone'), 3)
        for fragment in ('role="button"', 'tabindex="0"', 'aria-live="polite"'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, template)
        for fragment in ('data-automation-drop-zone', 'dragover', "addEventListener('drop'", 'DataTransfer', 'input.files'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, script)
        self.assertIn("input.click()", script)
        self.assertIn('无法读取拖拽文件', script)

    def test_review_queue_suggested_comment_stays_draft_until_existing_human_action(self):
        template = REVIEW_QUEUE_TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('data-human-review-action', template)
        self.assertIn('data-suggested-comment-draft', template)
        self.assertIn('automationSuggestedComment', template)
        self.assertNotIn('/admin/api/review/approve', template)
        self.assertNotIn('/admin/api/review/reject', template)

    def test_auto_review_results_is_evidence_only_and_keeps_human_review_link(self):
        template = AUTO_RESULTS_TEMPLATE.read_text(encoding='utf-8')
        for fragment in (
            'evidence-section-schedule',
            'evidence-section-rule',
            'evidence-section-text',
            'evidence-section-history',
            'evidence-section-llm',
            'evidence-section-system',
            '系统仅提供分类和证据，最终通过或驳回由人工审核人执行。',
            'data-human-review-action',
            'data-suggested-comment-draft',
            'reviewQueueFeedback',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, template)
        self.assertNotIn('一键自动审核', template)
        self.assertNotIn('window.appPrompt', template)
        self.assertNotIn('window.appConfirm', template)
        self.assertNotIn('/admin/api/review/approve', template)
        self.assertNotIn('/admin/api/review/reject', template)


if __name__ == '__main__':
    unittest.main()
