import json
import unittest
from tempfile import TemporaryDirectory

from tools.business_acceptance.cli import (
    _build_parser,
    _report_payload,
    _verification_for_manifest,
    prepare_acceptance,
)
from tools.business_acceptance.report import render_report
from tools.business_acceptance.verify import (
    REQUIRED_VERIFICATION_CHECKS,
    verify_batch_closure,
    verify_combined_sources,
    verify_export,
    verify_form_counts,
    verify_pagination,
    verify_protected_snapshot,
    verify_scope,
    verify_version_chain,
)


class BusinessAcceptanceVerifyTest(unittest.TestCase):
    def test_form_count_mismatch_has_stable_code(self):
        self.assertEqual(verify_form_counts(1500, 1499).code, 'FORM_COUNT_MISMATCH')
        self.assertEqual(verify_form_counts(1500, 1500).code, 'PASS')

    def test_pagination_duplicate_has_stable_code(self):
        pages = [[{'id': 'SYN-1'}], [{'id': 'SYN-1'}]]
        self.assertEqual(verify_pagination(pages).code, 'PAGINATION_DUPLICATE')

    def test_scope_foreign_department_has_stable_code(self):
        rows = [{'department': 'synthetic-dept-2', 'group': 'synthetic-group'}]
        self.assertEqual(
            verify_scope(rows, allowed_departments={'synthetic-dept-1'}).code,
            'REVIEW_SCOPE_MISMATCH',
        )

    def test_combined_evidence_requires_both_sources(self):
        self.assertEqual(
            verify_combined_sources({'sources': ['rule']}).code,
            'COMBINED_SOURCE_MISSING',
        )
        self.assertEqual(
            verify_combined_sources({'sources': ['rule', 'deepseek']}).code,
            'PASS',
        )

    def test_protected_snapshot_detects_changed_comment(self):
        before = {
            'status': '待审核',
            'reviewer_id': None,
            'review_time': None,
            'review_comment': '人工意见',
        }
        after = dict(before, review_comment='自动建议')
        self.assertEqual(
            verify_protected_snapshot(before, after).code,
            'AUTOMATION_CHANGED_HUMAN_FIELD',
        )

    def test_version_chain_detects_overwritten_old_version(self):
        versions = [
            {'id': 101, 'unique_id': 'SYN-U1', 'status': '待审核'},
            {'id': 101, 'unique_id': 'SYN-U1', 'status': '已驳回'},
        ]
        self.assertEqual(verify_version_chain(versions).code, 'VERSION_HISTORY_LOST')

    def test_batch_closure_detects_unprocessed_form(self):
        self.assertEqual(
            verify_batch_closure({'target': 500, 'processed': 499, 'failed': 0, 'cancelled': 0}).code,
            'BATCH_COUNT_MISMATCH',
        )

    def test_export_count_detects_missing_business_row(self):
        self.assertEqual(verify_export(1500, list(range(1499))).code, 'EXPORT_COUNT_MISMATCH')

    def test_report_redacts_sensitive_keys_and_preserves_conclusion(self):
        rendered = render_report({
            'conclusion': 'PASS',
            'api_key': 'synthetic-secret-must-not-appear',
            'nested': {
                'student_id': 'synthetic-student-id',
                'prompt': 'synthetic prompt body',
                'safe_count': 1500,
            },
        })
        self.assertIn('PASS', rendered)
        self.assertIn('[REDACTED]', rendered)
        self.assertNotIn('synthetic-secret-must-not-appear', rendered)
        self.assertNotIn('synthetic-student-id', rendered)
        self.assertNotIn('synthetic prompt body', rendered)

    def test_cli_exposes_verify_and_report_commands(self):
        parser = _build_parser()
        self.assertEqual(
            parser.parse_args(['verify', '--phase', 'pre-external']).command,
            'verify',
        )
        self.assertEqual(parser.parse_args(['report']).command, 'report')

    def test_pre_external_gate_requires_staged_main_and_cache_artifacts(self):
        with TemporaryDirectory(prefix='verify-pre-external-') as tmp:
            manifest = prepare_acceptance(tmp)
            phase, results = _verification_for_manifest(manifest, 'pre-external')
        self.assertEqual(phase, 'PRE_EXTERNAL')
        by_check = {result.check: result for result in results}
        self.assertTrue(by_check['STAGED_MANIFEST'].blocked)
        self.assertTrue(any(result.check == 'FAKE_MAIN_ARTIFACT' and result.blocked for result in results))
        self.assertTrue(any(result.check == 'FAKE_CACHE_ARTIFACT' and result.blocked for result in results))
        self.assertFalse(all(result.passed for result in results))

    def test_final_gate_has_complete_blocked_matrix_and_report_sections(self):
        with TemporaryDirectory(prefix='verify-final-') as tmp:
            manifest = prepare_acceptance(tmp)
            phase, results = _verification_for_manifest(manifest, 'final')
            payload = _report_payload(manifest, results)
        self.assertEqual(phase, 'FINAL')
        self.assertEqual(payload['conclusion'], 'BLOCKED')
        checks = {result.check for result in results}
        self.assertTrue(set(REQUIRED_VERIFICATION_CHECKS).issubset(checks))
        self.assertTrue(any(result.blocked for result in results))
        for section in (
            'batches', 'concurrency', 'deepseek_summary', 'administrator_scopes',
            'resubmission_version_chains', 'performance', 'export', 'statistics',
            'verification', 'defects',
        ):
            self.assertIn(section, payload)

    def test_post_automation_gate_blocks_when_real_phase_artifacts_are_missing(self):
        with TemporaryDirectory(prefix='verify-post-automation-') as tmp:
            manifest = prepare_acceptance(tmp)
            _phase, results = _verification_for_manifest(manifest, 'post-automation')
        missing_checks = {
            result.check for result in results
            if result.blocked
        }
        self.assertTrue({
            'REAL_STAGED_ARTIFACT',
            'REAL_MAIN_ARTIFACT',
            'REAL_CACHE_ARTIFACT',
            'REAL_RETRY_ARTIFACT',
        } <= missing_checks)
        self.assertIn('BLOCKED', {result.conclusion for result in results})

    def test_post_automation_does_not_substitute_fake_artifacts_for_real_evidence(self):
        with TemporaryDirectory(prefix='verify-real-evidence-') as tmp:
            manifest = prepare_acceptance(tmp)
            for name in (
                'fake-batches-staged.json',
                'fake-batches-main.json',
                'fake-batches-cache.json',
                'fake-batches-retry.json',
            ):
                path = manifest.config.output_path(f'results/{name}')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'phase': name}), encoding='utf-8')
            _phase, results = _verification_for_manifest(manifest, 'post-automation')

        by_check = {result.check: result for result in results}
        for check in (
            'REAL_STAGED_ARTIFACT',
            'REAL_MAIN_ARTIFACT',
            'REAL_RETRY_ARTIFACT',
            'REAL_CACHE_ARTIFACT',
        ):
            self.assertTrue(by_check[check].blocked)


if __name__ == '__main__':
    unittest.main()
