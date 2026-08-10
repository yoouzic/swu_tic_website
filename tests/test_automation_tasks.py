import json
import unittest
from collections import Counter
from types import SimpleNamespace

from app.models import db
from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
    ScheduleCoverage,
)
from app.review_automation.llm.client import PermanentLLMError, TransientLLMError
from app.review_automation.llm.schemas import DeepSeekReviewResponse
from app.review_automation.models import ReviewAssessment, ReviewBatch
from app.review_automation.service import AssessmentService
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


class ScriptedLLM:
    model = 'deepseek-v4-flash'
    prompt_version = 'synthetic-prompt-v1'

    def __init__(self, permanent_ids=()):
        self.permanent_ids = set(permanent_ids)
        self.calls = Counter()

    def review(self, payload):
        form_id = payload['form']['id']
        self.calls[form_id] += 1
        if form_id in self.permanent_ids:
            raise PermanentLLMError('authentication_failed')
        return DeepSeekReviewResponse(
            compliance='compliant',
            summary='synthetic batch summary',
            findings=[],
            suggested_comment='synthetic batch suggestion',
        )


class TimeoutThenSuccessLLM:
    model = 'deepseek-v4-flash'
    prompt_version = 'synthetic-prompt-v1'

    def __init__(self):
        self.calls = 0

    def review(self, payload):
        self.calls += 1
        if self.calls == 1:
            raise TransientLLMError('timeout')
        return DeepSeekReviewResponse(
            compliance='compliant',
            summary='synthetic recovered summary',
            findings=[],
            suggested_comment='synthetic recovered suggestion',
        )


def review_finding(form, context):
    return (
        FindingDraft(
            rule_key='synthetic_review_rule',
            source=FindingSource.RULE,
            severity=FindingSeverity.REVIEW,
            title='synthetic review finding',
            message='synthetic rule recommends human review',
            objective=False,
            evidence_strength=EvidenceStrength.APPROXIMATE,
            evidence={'marker': 'SYNTHETIC_REVIEW'},
        ),
    )


def high_risk_finding(form, context):
    return (
        FindingDraft(
            rule_key='personal_schedule_conflict',
            source=FindingSource.SCHEDULE,
            severity=FindingSeverity.HIGH,
            title='synthetic exact schedule conflict',
            message='synthetic exact evidence needs human review',
            objective=True,
            evidence_strength=EvidenceStrength.EXACT,
            evidence={'marker': 'SYNTHETIC_HIGH_RISK'},
        ),
    )


class SharedServiceFactory:
    def __init__(self, form_ids):
        self.form_ids = list(form_ids)
        self.permanent_ids = set()
        self.unknown_ids = set()
        self.llm = ScriptedLLM()
        self.service = AssessmentService(
            llm_client=self.llm,
            llm_enabled=True,
            deterministic_runner=self._rules,
            schedule_loader=self._schedule,
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v1'}},
            rule_revisions={'synthetic_rule': 1},
        )

    def __call__(self):
        return self.service

    def _rules(self, form, context):
        if form.id == self.form_ids[1]:
            return review_finding(form, context)
        if form.id == self.form_ids[2]:
            return high_risk_finding(form, context)
        return ()

    def _schedule(self, form):
        coverage = (
            ScheduleCoverage.NONE
            if form.id in self.unknown_ids
            else ScheduleCoverage.COMPLETE
        )
        return SimpleNamespace(coverage=coverage, slots=(), admin_classes=())


class FlakyService:
    def __init__(self, service, transient_id, state):
        self.service = service
        self.transient_id = transient_id
        self.state = state

    def assess_form(self, form_id, **kwargs):
        if form_id == self.transient_id and not self.state['raised']:
            self.state['raised'] = True
            raise TransientLLMError('timeout')
        return self.service.assess_form(form_id, **kwargs)


class AlwaysTransientService:
    def __init__(self, service):
        self.service = service

    def assess_form(self, form_id, **kwargs):
        if kwargs.get('llm_enabled') is False:
            return self.service.assess_form(form_id, **kwargs)
        raise TransientLLMError('timeout')


class SelectiveFailureService:
    def __init__(self, service, failing_id):
        self.service = service
        self.failing_id = failing_id

    def assess_form(self, form_id, **kwargs):
        if form_id == self.failing_id:
            raise RuntimeError('SYNTHETIC_TASK_FAILURE')
        return self.service.assess_form(form_id, **kwargs)


class AutomationTasksTest(unittest.TestCase):
    def setUp(self):
        self.database = temporary_automation_database()
        self.database.__enter__()
        self.forms = []
        for index in range(4):
            form = make_synthetic_form(
                unique_id=3000 + index,
                listener_number=f'SYN-LISTENER-{index}',
                contact_phone1=f'SYNTHETIC_PHONE_{index:04d}',
            )
            db.session.add(form)
            self.forms.append(form)
        db.session.commit()
        self.form_ids = [form.id for form in self.forms]
        from app.review_automation.tasks import review as review_tasks

        self.review_tasks = review_tasks
        self.review_tasks.celery_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True,
        )
        self.factory = SharedServiceFactory(self.form_ids)
        self.review_tasks.SERVICE_FACTORY = self.factory

    def tearDown(self):
        self.review_tasks.SERVICE_FACTORY = self.review_tasks.build_default_service
        self.database.__exit__(None, None, None)

    def protected_snapshot(self):
        return {
            form.id: (
                form.status,
                form.reviewer_id,
                form.review_time,
                form.review_comment,
            )
            for form in self.forms
        }

    def assert_protected_unchanged(self, snapshot):
        current = {
            form.id: (
                form.status,
                form.reviewer_id,
                form.review_time,
                form.review_comment,
            )
            for form in self.forms
        }
        self.assertEqual(snapshot, current)

    def test_batch_counts_four_categories_and_isolates_permanent_failure(self):
        snapshot = self.protected_snapshot()
        self.factory.permanent_ids.add(self.form_ids[3])
        self.factory.llm.permanent_ids.add(self.form_ids[3])
        self.factory.unknown_ids.add(self.form_ids[3])

        batch = self.review_tasks.enqueue_review_batch(self.form_ids)
        payload = json.loads(batch.snapshot_json)

        self.assertEqual(batch.target_form_count, 4)
        self.assertEqual(batch.status, 'completed_with_errors')
        self.assertEqual(batch.clear_count, 1)
        self.assertEqual(batch.review_count, 1)
        self.assertEqual(batch.high_risk_count, 1)
        self.assertEqual(batch.unknown_count, 1)
        self.assertEqual(batch.failed_count, 1)
        self.assertEqual(payload['processed_count'], 4)
        self.assertEqual(ReviewAssessment.query.count(), 4)
        self.assertTrue(all(
            ReviewAssessment.query.filter_by(form_id=form_id).first() is not None
            for form_id in self.form_ids
        ))
        self.assert_protected_unchanged(snapshot)

    def test_existing_fingerprint_is_reused_and_counted_as_cache(self):
        snapshot = self.protected_snapshot()
        self.factory.service.assess_form(self.form_ids[0])

        batch = self.review_tasks.enqueue_review_batch(self.form_ids)
        payload = json.loads(batch.snapshot_json)

        self.assertEqual(batch.status, 'completed')
        self.assertEqual(batch.cache_count, 1)
        self.assertEqual(payload['cache_count'], 1)
        self.assertEqual(ReviewAssessment.query.count(), 4)
        self.assert_protected_unchanged(snapshot)

    def test_cancelled_batch_never_starts_form_tasks(self):
        snapshot = self.protected_snapshot()
        batch = self.review_tasks.create_review_batch(self.form_ids, enqueue=False)

        self.review_tasks.cancel_review_batch(batch.id)
        self.review_tasks.dispatch_review_batch(batch.id)
        refreshed = db.session.get(ReviewBatch, batch.id)

        self.assertEqual(refreshed.status, 'cancelled')
        self.assertEqual(ReviewAssessment.query.count(), 0)
        self.assert_protected_unchanged(snapshot)

    def test_typed_transient_failure_retries_once_then_completes(self):
        snapshot = self.protected_snapshot()
        state = {'raised': False}

        def flaky_factory():
            return FlakyService(self.factory.service, self.form_ids[0], state)

        self.review_tasks.SERVICE_FACTORY = flaky_factory
        batch = self.review_tasks.enqueue_review_batch([self.form_ids[0]])

        self.assertTrue(state['raised'])
        self.assertEqual(batch.status, 'completed')
        self.assertEqual(batch.failed_count, 0)
        self.assertEqual(ReviewAssessment.query.count(), 1)
        self.assert_protected_unchanged(snapshot)

    def test_real_service_transient_summary_is_force_refreshed_once(self):
        snapshot = self.protected_snapshot()
        flaky_llm = TimeoutThenSuccessLLM()
        service = AssessmentService(
            llm_client=flaky_llm,
            llm_enabled=True,
            deterministic_runner=lambda form, context: (),
            schedule_loader=self.factory._schedule,
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v1'}},
            rule_revisions={'synthetic_rule': 1},
        )
        self.review_tasks.SERVICE_FACTORY = lambda: service

        batch = self.review_tasks.enqueue_review_batch([self.form_ids[0]])
        payload = json.loads(batch.snapshot_json)
        assessments = ReviewAssessment.query.filter_by(form_id=self.form_ids[0]).all()
        failed = next(item for item in assessments if item.error_code == 'timeout')
        succeeded = next(item for item in assessments if item.error_code is None)

        self.assertEqual(flaky_llm.calls, 2)
        self.assertEqual(batch.status, 'completed')
        self.assertEqual(batch.failed_count, 0)
        self.assertEqual(payload['results'][0]['assessment_id'], succeeded.id)
        self.assertNotEqual(payload['results'][0]['assessment_id'], failed.id)
        self.assert_protected_unchanged(snapshot)

    def test_replayed_form_task_replaces_progress_record_idempotently(self):
        snapshot = self.protected_snapshot()
        batch = self.review_tasks.create_review_batch([self.form_ids[0]], enqueue=False)

        self.review_tasks.assess_form_task.run(batch.id, self.form_ids[0], False)
        self.review_tasks.assess_form_task.run(batch.id, self.form_ids[0], False)
        db.session.expire_all()
        progress = json.loads(db.session.get(ReviewBatch, batch.id).snapshot_json)

        self.assertEqual(progress['processed_count'], 1)
        self.assertEqual(len(progress['results']), 1)
        self.assertEqual(ReviewAssessment.query.count(), 1)
        self.assert_protected_unchanged(snapshot)

    def test_unexpected_single_form_failure_does_not_block_other_forms(self):
        snapshot = self.protected_snapshot()

        def selective_factory():
            return SelectiveFailureService(self.factory.service, self.form_ids[0])

        self.review_tasks.SERVICE_FACTORY = selective_factory
        batch = self.review_tasks.enqueue_review_batch(self.form_ids[:2])

        self.assertEqual(batch.status, 'completed_with_errors')
        self.assertEqual(batch.failed_count, 1)
        self.assertEqual(ReviewAssessment.query.filter_by(form_id=self.form_ids[1]).count(), 1)
        self.assert_protected_unchanged(snapshot)

    def test_exhausted_transient_retry_uses_deterministic_fallback(self):
        snapshot = self.protected_snapshot()

        def always_transient_factory():
            return AlwaysTransientService(self.factory.service)

        self.review_tasks.SERVICE_FACTORY = always_transient_factory
        batch = self.review_tasks.enqueue_review_batch([self.form_ids[0]])

        self.assertEqual(batch.status, 'completed_with_errors')
        self.assertEqual(batch.failed_count, 1)
        self.assertEqual(ReviewAssessment.query.filter_by(form_id=self.form_ids[0]).count(), 1)
        self.assertEqual(
            ReviewAssessment.query.filter_by(form_id=self.form_ids[0]).first().error_code,
            'timeout',
        )
        self.assert_protected_unchanged(snapshot)

    def test_root_task_result_is_json_safe(self):
        batch = self.review_tasks.create_review_batch([self.form_ids[0]], enqueue=False)

        result = self.review_tasks.run_review_batch_task.run(batch.id)

        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.assertIsInstance(result, dict)
        self.assertIn('status', json.loads(encoded))

    def test_cancelled_root_task_result_is_json_safe(self):
        batch = self.review_tasks.create_review_batch([self.form_ids[0]], enqueue=False)
        self.review_tasks.cancel_review_batch(batch.id)

        result = self.review_tasks.run_review_batch_task.run(batch.id)

        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.assertIsInstance(result, dict)
        self.assertEqual(json.loads(encoded)['status'], 'cancelled')

    def test_task_signatures_contain_ids_and_booleans_only(self):
        batch_signature = self.review_tasks.run_review_batch_task.s('SYN-BATCH-ID')
        form_signature = self.review_tasks.assess_form_task.s('SYN-BATCH-ID', 123, True)
        serialized = json.dumps({
            'batch': {
                'task': batch_signature.task,
                'args': batch_signature.args,
                'kwargs': batch_signature.kwargs,
            },
            'form': {
                'task': form_signature.task,
                'args': form_signature.args,
                'kwargs': form_signature.kwargs,
            },
        }, sort_keys=True)

        self.assertEqual(form_signature.args, ('SYN-BATCH-ID', 123, True))
        self.assertNotIn('SYNTHETIC_PHONE', serialized)
        self.assertNotIn('INVENTED_FEEDBACK_MARKER', serialized)
        self.assertNotIn('SYNTHETIC_TEACHER', serialized)
        self.assertNotIn('course_feedback', serialized)
        self.assertNotIn('review_comment', serialized)
        self.assertEqual(batch_signature.args, ('SYN-BATCH-ID',))


if __name__ == '__main__':
    unittest.main()
