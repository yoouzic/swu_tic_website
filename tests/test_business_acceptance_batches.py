from itertools import chain
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


from tools.business_acceptance.batches import (
    AdapterResult,
    AttemptLimitExceeded,
    AssessmentCache,
    BatchForm,
    BatchClosureError,
    RequestBudgetExceeded,
    RulesOnlyHttpAttempt,
    RunState,
    StageObservation,
    build_stages,
    choose_safe_concurrency,
    close_batch,
    partition_mode_form_ids,
    run_batch,
)


class CountingFakeAdapter:
    def __init__(self):
        self.calls = []

    def assess(self, form, *, mode):
        self.calls.append((form.form_id, form.form_version, mode))
        return AdapterResult(
            classification='clear',
            http_attempts=0 if mode == 'rules_only' else 1,
        )


class BusinessAcceptanceBatchesTest(unittest.TestCase):
    def test_request_budget_refuses_attempts_above_ceiling(self):
        state = RunState(http_attempt_ceiling=1200)
        state.reserve_http_attempts(1000)
        state.reserve_http_attempts(200)
        with self.assertRaises(RequestBudgetExceeded):
            state.reserve_http_attempts(1)

    def test_staged_levels_are_disjoint_and_exactly_sized(self):
        form_ids = list(range(60))
        stages = build_stages(form_ids, levels=(4, 8, 16), size_each=20)
        self.assertEqual(
            [(stage.concurrency, len(stage.form_ids)) for stage in stages],
            [(4, 20), (8, 20), (16, 20)],
        )
        self.assertEqual(len(set(chain.from_iterable(stage.form_ids for stage in stages))), 60)

    def test_close_batch_closes_target_and_classified_counts(self):
        items = [
            {'form_id': index, 'status': 'completed', 'classified': True}
            for index in range(500)
        ]
        summary = close_batch(items, target=500)
        self.assertEqual(summary.target, 500)
        self.assertEqual(summary.processed, 500)
        self.assertEqual(summary.classified, 500)

    def test_partial_failure_closes_with_error_instead_of_completed(self):
        items = [
            {'form_id': index, 'status': 'completed', 'classified': True}
            for index in range(499)
        ]
        items.append({
            'form_id': 499,
            'status': 'failed',
            'classified': False,
            'error_code': 'provider_rejected',
        })
        summary = close_batch(items, target=500)
        self.assertEqual(summary.processed, 500)
        self.assertEqual(summary.classified, 499)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.status, 'completed_with_errors')

    def test_close_batch_rejects_duplicate_pending_or_target_mismatch(self):
        completed = {
            'form_id': 1,
            'form_version': 'v1',
            'status': 'completed',
            'classified': True,
        }
        with self.assertRaises(BatchClosureError):
            close_batch([completed, dict(completed)], target=2)
        with self.assertRaises(BatchClosureError):
            close_batch([completed, {
                'form_id': 2,
                'form_version': 'v1',
                'status': 'pending',
            }], target=1)
        with self.assertRaises(BatchClosureError):
            close_batch([completed], target=2)

    def test_safe_concurrency_chooses_highest_passing_stage_or_falls_back(self):
        observations = [
            StageObservation(4, 20, 20, 0.0, 0.0, True),
            StageObservation(8, 20, 20, 0.05, 0.05, True),
            StageObservation(16, 20, 20, 0.06, 0.0, True),
        ]
        self.assertEqual(choose_safe_concurrency(observations), 8)
        self.assertEqual(choose_safe_concurrency([
            StageObservation(4, 20, 19, 0.0, 0.0, True),
            StageObservation(8, 20, 20, 0.0, 0.06, True),
        ]), 4)
        self.assertEqual(choose_safe_concurrency([
            StageObservation(4, 20, 20, 0.0, 0.0, False),
        ]), 4)

    def test_three_mode_partition_is_mutually_exclusive_and_closes_1500(self):
        partitions = partition_mode_form_ids(list(range(1500)), per_mode=500)
        self.assertEqual(
            {mode: len(form_ids) for mode, form_ids in partitions.items()},
            {'rules_only': 500, 'llm_only': 500, 'combined': 500},
        )
        self.assertEqual(sum(len(form_ids) for form_ids in partitions.values()), 1500)
        self.assertEqual(len(set(chain.from_iterable(partitions.values()))), 1500)
        self.assertEqual(
            len(partitions['llm_only']) + len(partitions['combined']),
            1000,
        )

    def test_run_state_is_atomic_and_round_trips_only_safe_metrics(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run-state.json'
            state = RunState(http_attempt_ceiling=1200, phase='staged')
            state.reserve_http_attempts(3)
            state.last_completed_form_id = 42
            state.save(path)
            restored = RunState.load(path)

        self.assertEqual(restored.phase, 'staged')
        self.assertEqual(restored.http_attempts, 3)
        self.assertEqual(restored.last_completed_form_id, 42)
        self.assertNotIn('password', restored.to_dict())
        self.assertNotIn('api_key', restored.to_dict())

    def test_unchanged_rerun_hits_cache_and_changed_version_gets_new_assessment(self):
        adapter = CountingFakeAdapter()
        cache = AssessmentCache()
        state = RunState(http_attempt_ceiling=1200)
        forms = [BatchForm(form_id=index, form_version='v1') for index in range(50)]

        first = run_batch(forms, adapter, state, cache, mode='llm_only')
        second = run_batch(forms, adapter, state, cache, mode='llm_only')
        changed = run_batch(
            [BatchForm(form_id=0, form_version='v2')],
            adapter,
            state,
            cache,
            mode='llm_only',
        )

        self.assertEqual(first.cache_hits, 0)
        self.assertEqual(first.http_attempts, 50)
        self.assertEqual(second.cache_hits, 50)
        self.assertEqual(second.http_attempts, 0)
        self.assertEqual(changed.cache_hits, 0)
        self.assertEqual(changed.http_attempts, 1)
        self.assertEqual(state.http_attempts, 51)
        self.assertEqual(cache.assessment_count, 51)
        self.assertEqual(len(adapter.calls), 51)

    def test_rules_only_fake_batch_has_no_http_attempts(self):
        adapter = CountingFakeAdapter()
        state = RunState(http_attempt_ceiling=1200)
        summary = run_batch(
            [BatchForm(form_id=index, form_version='v1') for index in range(5)],
            adapter,
            state,
            AssessmentCache(),
            mode='rules_only',
        )
        self.assertEqual(summary.http_attempts, 0)
        self.assertEqual(state.http_attempts, 0)

    def test_zero_retry_gate_rejects_more_than_one_attempt_per_uncached_item(self):
        class OverAttemptAdapter(CountingFakeAdapter):
            def assess(self, form, *, mode):
                self.calls.append((form.form_id, form.form_version, mode))
                return AdapterResult(classification='clear', http_attempts=2)

        with self.assertRaises(AttemptLimitExceeded):
            run_batch(
                [BatchForm(form_id=1, form_version='v1')],
                OverAttemptAdapter(),
                RunState(http_attempt_ceiling=1200),
                AssessmentCache(),
                mode='llm_only',
                transient_retries=0,
            )

    def test_rules_only_http_attempt_is_a_hard_failure_even_on_adapter_error(self):
        class BadRulesAdapter:
            def assess(self, form, *, mode):
                return AdapterResult(
                    classification=None,
                    error_code='adapter_failure',
                    http_attempts=1,
                    classified=False,
                )

        with self.assertRaises(RulesOnlyHttpAttempt):
            run_batch(
                [BatchForm(form_id=1, form_version='v1')],
                BadRulesAdapter(),
                RunState(http_attempt_ceiling=1200),
                AssessmentCache(),
                mode='rules_only',
            )


if __name__ == '__main__':
    unittest.main()
