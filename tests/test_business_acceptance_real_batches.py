import unittest
from types import SimpleNamespace

from tools.business_acceptance.batches import plan_real_batches
from tools.business_acceptance.cli import _real_phase_conclusion
from tools.business_acceptance.real_batches import wait_for_batch
from tools.business_acceptance.real_runner import (
    _merge_recovered_summary,
    _merge_stage_record,
    _select_cache_keys,
    _stale_recovery_plan,
)


class RealBatchPlanningTest(unittest.TestCase):
    def _forms(self):
        return tuple(
            SimpleNamespace(
                synthetic_key=f'AC-{index:04d}',
                review_mode=mode,
            )
            for mode in ('rules_only', 'llm_only', 'combined')
            for index in range(
                1 if mode == 'rules_only' else 501 if mode == 'llm_only' else 1001,
                501 if mode == 'rules_only' else 1001 if mode == 'llm_only' else 1501,
            )
        )

    def test_staged_d_rd_forms_are_disjoint_from_remaining_500_form_batches(self):
        plan = plan_real_batches(self._forms(), levels=(4, 8, 16), size_each=20, per_mode=500)

        self.assertEqual([stage.concurrency for stage in plan.stages], [4, 8, 16])
        self.assertEqual([stage.target for stage in plan.stages], [20, 20, 20])
        self.assertEqual(
            {mode: len(values) for mode, values in plan.staged_by_mode.items()},
            {'rules_only': 0, 'llm_only': 30, 'combined': 30},
        )
        self.assertEqual(
            {mode: len(values) for mode, values in plan.main_by_mode.items()},
            {'rules_only': 500, 'llm_only': 470, 'combined': 470},
        )

        staged = set().union(*(set(stage.form_keys) for stage in plan.stages))
        main = set().union(*(set(values) for values in plan.main_by_mode.values()))
        self.assertEqual(len(staged), 60)
        self.assertEqual(len(main), 1440)
        self.assertTrue(staged.isdisjoint(main))
        self.assertEqual(len(staged | main), 1500)

    def test_wait_for_batch_summarizes_terminal_items_without_raw_payloads(self):
        responses = iter((
            (
                SimpleNamespace(status='running', target_form_count=2),
                (),
            ),
            (
                SimpleNamespace(status='completed', target_form_count=2),
                (
                    SimpleNamespace(
                        form_id=11,
                        status='completed',
                        category='clear',
                        error_code=None,
                        cache_hit=False,
                        http_attempts=1,
                        duration_ms=100,
                    ),
                    SimpleNamespace(
                        form_id=12,
                        status='completed',
                        category='review',
                        error_code=None,
                        cache_hit=True,
                        http_attempts=0,
                        duration_ms=200,
                    ),
                ),
            ),
        ))

        summary = wait_for_batch(
            'batch-1',
            refresh=lambda _batch_id: next(responses),
            poll_seconds=0,
            timeout_seconds=1,
        )

        self.assertEqual(summary['status'], 'completed')
        self.assertEqual(summary['target'], 2)
        self.assertEqual(summary['processed'], 2)
        self.assertEqual(summary['failed'], 0)
        self.assertEqual(summary['cache_hits'], 1)
        self.assertEqual(summary['http_attempts'], 1)
        self.assertEqual(summary['p50_duration_ms'], 150)
        self.assertEqual(summary['p95_duration_ms'], 200)
        self.assertNotIn('prompt', summary)
        self.assertNotIn('response', summary)

    def test_real_phase_conclusion_preserves_pass_fail_and_blocked(self):
        self.assertEqual(_real_phase_conclusion({'status': 'completed'}), 'PASS')
        self.assertEqual(_real_phase_conclusion({'status': 'completed_with_errors'}), 'FAIL')
        self.assertEqual(_real_phase_conclusion({'status': 'failed'}), 'FAIL')
        self.assertEqual(_real_phase_conclusion({'status': 'queued'}), 'BLOCKED')
        self.assertEqual(_real_phase_conclusion({}), 'BLOCKED')
        self.assertEqual(
            _real_phase_conclusion({'stages': [{'status': 'completed'}]}),
            'PASS',
        )

    def test_stage_recovery_merges_failed_items_without_inflating_target(self):
        original = {
            'phase': 'staged',
            'mode': 'llm_only',
            'form_keys': ['AC-0001', 'AC-0002', 'AC-0003'],
            'target': 3,
            'processed': 2,
            'terminal_count': 3,
            'failed': 1,
            'cancelled': 0,
            'status': 'completed_with_errors',
            'http_attempts': 3,
            'batch_ids': ['first-batch'],
            'successful_form_keys': ['AC-0001', 'AC-0002'],
            'failed_items': [{'form_key': 'AC-0003', 'error_code': 'truncated_output'}],
        }
        recovery = {
            'phase': 'staged',
            'mode': 'llm_only',
            'form_keys': ['AC-0003'],
            'target': 1,
            'processed': 1,
            'terminal_count': 1,
            'failed': 0,
            'cancelled': 0,
            'status': 'completed',
            'http_attempts': 1,
            'batch_ids': ['recovery-batch'],
            'successful_form_keys': ['AC-0003'],
            'failed_items': [],
        }

        merged = _merge_stage_record(original, recovery)

        self.assertEqual(merged['target'], 3)
        self.assertEqual(merged['processed'], 3)
        self.assertEqual(merged['terminal_count'], 3)
        self.assertEqual(merged['failed'], 0)
        self.assertEqual(merged['status'], 'completed')
        self.assertEqual(merged['http_attempts'], 4)
        self.assertEqual(set(merged['successful_form_keys']), {'AC-0001', 'AC-0002', 'AC-0003'})
        self.assertEqual(merged['failed_items'], [])
        self.assertEqual(set(merged['batch_ids']), {'first-batch', 'recovery-batch'})

    def test_stale_running_recovery_excludes_completed_and_marks_uncertain_items(self):
        plan = _stale_recovery_plan(
            [
                {'form_id': 11, 'status': 'completed'},
                {'form_id': 12, 'status': 'queued'},
                {'form_id': 13, 'status': 'running'},
                {'form_id': 14, 'status': 'running'},
                {'form_id': 15, 'status': 'cancelled'},
            ]
        )

        self.assertEqual(plan['completed_form_ids'], (11,))
        self.assertEqual(plan['recovery_form_ids'], (12, 13, 14, 15))
        self.assertEqual(plan['uncertain_form_ids'], (13, 14))
        self.assertEqual(plan['uncertain_http_attempts'], 2)
        self.assertEqual(plan['unrecoverable_form_ids'], ())

    def test_recovered_summary_keeps_completed_items_and_counts_uncertainty(self):
        original = {
            'batch_id': 'original-batch',
            'status': 'running',
            'target': 4,
            'processed': 2,
            'terminal_count': 2,
            'failed': 0,
            'cancelled': 0,
            'http_attempts': 2,
            'successful_form_ids': [101, 102],
            'failed_items': [],
            'category_counts': {'clear': 2},
        }
        recovery = {
            'batch_id': 'recovery-batch',
            'status': 'completed',
            'target': 2,
            'processed': 2,
            'terminal_count': 2,
            'failed': 0,
            'cancelled': 0,
            'http_attempts': 2,
            'successful_form_ids': [103, 104],
            'failed_items': [],
            'category_counts': {'review': 2},
        }

        merged = _merge_recovered_summary(
            original,
            recovery,
            {
                'uncertain_form_ids': (103,),
                'uncertain_http_attempts': 1,
            },
        )

        self.assertEqual(merged['target'], 4)
        self.assertEqual(merged['processed'], 4)
        self.assertEqual(merged['terminal_count'], 4)
        self.assertEqual(merged['status'], 'completed')
        self.assertEqual(merged['http_attempts'], 4)
        self.assertEqual(merged['uncertain_http_attempts'], 1)
        self.assertEqual(merged['budget_http_attempts'], 5)
        self.assertEqual(merged['successful_form_ids'], [101, 102, 103, 104])
        self.assertEqual(merged['category_counts'], {'clear': 2, 'review': 2})
        self.assertEqual(merged['batch_ids'], ['original-batch', 'recovery-batch'])

    def test_cache_selection_excludes_forms_with_error_assessments(self):
        selected = _select_cache_keys(
            {
                'llm_only': ['L-1', 'L-2', 'L-3'],
                'combined': ['C-1', 'C-2', 'C-3'],
            },
            {
                'L-1': 1,
                'L-2': 2,
                'L-3': 3,
                'C-1': 11,
                'C-2': 12,
                'C-3': 13,
            },
            error_form_ids={1, 11},
            sample_size=4,
        )

        self.assertEqual(selected, {
            'llm_only': ('L-2', 'L-3'),
            'combined': ('C-2', 'C-3'),
        })


if __name__ == '__main__':
    unittest.main()
