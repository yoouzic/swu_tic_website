import unittest
from types import SimpleNamespace

from tools.business_acceptance.batches import plan_real_batches
from tools.business_acceptance.cli import _real_phase_conclusion
from tools.business_acceptance.real_batches import wait_for_batch


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


if __name__ == '__main__':
    unittest.main()
