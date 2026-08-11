import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from app.app import app
from app.models import db
from app.review_automation.contracts import ScheduleCoverage
from app.review_automation.llm.client import TransientLLMError
from app.review_automation.llm.schemas import DeepSeekReviewResponse
from app.review_automation.models import ReviewBatchItem
from app.review_automation.service import AssessmentService
from app.review_automation.tasks import review as review_tasks
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


class CountingLLM:
    model = 'deepseek-v4-flash'
    prompt_version = 'synthetic-concurrent-batch-v1'

    def __init__(self):
        self.calls = []
        self.last_attempt_count = 0

    def review(self, payload):
        self.last_attempt_count = 1
        self.calls.append(payload)
        return DeepSeekReviewResponse(
            compliance='compliant',
            summary='合成并发批次结果',
            findings=[],
            suggested_comment='合成建议',
        )


class TimeoutLLM:
    model = 'deepseek-v4-flash'
    prompt_version = 'synthetic-concurrent-batch-v1'

    def __init__(self):
        self.last_attempt_count = 0

    def review(self, payload):
        self.last_attempt_count = 1
        raise TransientLLMError('timeout')


def complete_schedule(_form):
    return SimpleNamespace(coverage=ScheduleCoverage.COMPLETE, slots=(), admin_classes=())


class RulesOnlyServiceFactory:
    def __init__(self, llm=None):
        self.service = AssessmentService(
            llm_client=llm,
            llm_enabled=bool(llm),
            deterministic_runner=lambda form, context: (),
            schedule_loader=complete_schedule,
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v1'}},
            rule_revisions={'synthetic_rule': 1},
        )

    def __call__(self):
        return self.service


class AutomationConcurrentBatchesTest(unittest.TestCase):
    def setUp(self):
        self.database = temporary_automation_database()
        self.database.__enter__()
        self.forms = []
        for index in range(16):
            form = make_synthetic_form(
                unique_id=7000 + index,
                listener_number=f'SYN-CONCURRENT-{index:04d}',
                contact_phone1=f'SYNTHETIC_PHONE_{index:04d}',
            )
            db.session.add(form)
            self.forms.append(form)
        db.session.commit()
        self.form_ids = [form.id for form in self.forms]
        self.previous_factory = review_tasks.SERVICE_FACTORY
        review_tasks.SERVICE_FACTORY = RulesOnlyServiceFactory()

    def tearDown(self):
        review_tasks.SERVICE_FACTORY = self.previous_factory
        self.database.__exit__(None, None, None)

    def test_batch_creates_one_unique_item_per_latest_form(self):
        batch = review_tasks.create_review_batch(
            self.form_ids + self.form_ids[:3],
            enqueue=False,
            review_mode='combined',
        )

        items = ReviewBatchItem.query.filter_by(batch_id=batch.id).all()
        self.assertEqual(len(items), len(set(self.form_ids)))
        self.assertEqual({item.form_id for item in items}, set(self.form_ids))
        self.assertEqual({item.status for item in items}, {'queued'})

    def test_progress_is_derived_from_items_without_lost_updates(self):
        batch = review_tasks.create_review_batch(
            self.form_ids,
            enqueue=False,
            review_mode='rules_only',
        )

        def run_one(form_id):
            with app.app_context():
                return review_tasks.assess_form_task.run(batch.id, form_id, False)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(run_one, self.form_ids))

        payload = review_tasks._aggregate_batch(batch.id)
        self.assertEqual(payload['processed_count'], len(self.form_ids))
        self.assertEqual(payload['target_form_count'], len(self.form_ids))
        self.assertEqual(
            payload['processed_count'],
            sum(payload[key] for key in (
                'clear_count',
                'review_count',
                'high_risk_count',
                'unknown_count',
            )),
        )
        self.assertEqual(
            {item.status for item in ReviewBatchItem.query.filter_by(batch_id=batch.id)},
            {'completed'},
        )

    def test_batch_item_records_cache_and_http_attempts(self):
        llm = CountingLLM()
        review_tasks.SERVICE_FACTORY = RulesOnlyServiceFactory(llm)

        first_batch = review_tasks.create_review_batch(
            [self.form_ids[0]],
            enqueue=False,
            review_mode='combined',
        )
        review_tasks.assess_form_task.run(first_batch.id, self.form_ids[0], False)
        first_item = ReviewBatchItem.query.filter_by(batch_id=first_batch.id).one()
        self.assertEqual(first_item.http_attempts, 1)
        self.assertFalse(first_item.cache_hit)

        second_batch = review_tasks.create_review_batch(
            [self.form_ids[0]],
            enqueue=False,
            review_mode='combined',
        )
        review_tasks.assess_form_task.run(second_batch.id, self.form_ids[0], False)
        second_item = ReviewBatchItem.query.filter_by(batch_id=second_batch.id).one()
        self.assertEqual(second_item.http_attempts, 0)
        self.assertTrue(second_item.cache_hit)
        self.assertEqual(len(llm.calls), 1)

    def test_rules_only_fallback_keeps_primary_http_attempts(self):
        service = AssessmentService(
            llm_client=TimeoutLLM(),
            llm_enabled=True,
            deterministic_runner=lambda form, context: (),
            schedule_loader=complete_schedule,
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v1'}},
            rule_revisions={'synthetic_rule': 1},
        )
        review_tasks.SERVICE_FACTORY = lambda: service

        batch = review_tasks.create_review_batch(
            [self.form_ids[0]],
            enqueue=False,
            review_mode='combined',
            transient_retries=0,
        )
        review_tasks.assess_form_task.run(batch.id, self.form_ids[0], False)

        item = ReviewBatchItem.query.filter_by(batch_id=batch.id).one()
        self.assertEqual(item.status, 'failed')
        self.assertEqual(item.error_code, 'timeout')
        self.assertEqual(item.http_attempts, 1)

    def test_transient_retry_count_is_bounded_and_persisted(self):
        with self.assertRaises(ValueError):
            review_tasks.create_review_batch(
                [self.form_ids[0]],
                enqueue=False,
                transient_retries=-1,
            )
        with self.assertRaises(ValueError):
            review_tasks.create_review_batch(
                [self.form_ids[0]],
                enqueue=False,
                transient_retries=4,
            )

        batch = review_tasks.create_review_batch(
            [self.form_ids[0]],
            enqueue=False,
            transient_retries=0,
        )
        self.assertEqual(
            json.loads(batch.config_snapshot_json)['transient_retries'],
            0,
        )

    def test_non_eager_dispatch_uses_group_chord_and_records_root(self):
        batch = review_tasks.create_review_batch(
            self.form_ids[:3],
            enqueue=False,
            review_mode='rules_only',
        )
        eager_before = review_tasks.celery_app.conf.task_always_eager
        fake_async_result = SimpleNamespace(id='SYN-CELERY-ROOT')
        try:
            review_tasks.celery_app.conf.task_always_eager = False
            with patch.object(review_tasks, 'group') as group_mock, patch.object(
                review_tasks, 'chord'
            ) as chord_mock:
                header = object()
                group_mock.return_value = header
                chord_mock.return_value.return_value = fake_async_result

                result = review_tasks.dispatch_review_batch(batch.id)

                signatures = list(group_mock.call_args.args[0])
                self.assertEqual(len(signatures), 3)
                self.assertTrue(all(
                    signature.args[0] == batch.id
                    and isinstance(signature.args[1], int)
                    and signature.args[2] is False
                    for signature in signatures
                ))
                chord_mock.assert_called_once_with(header)
                self.assertIs(result, fake_async_result)
                refreshed = db.session.get(type(batch), batch.id)
                self.assertEqual(refreshed.celery_root_id, 'SYN-CELERY-ROOT')
        finally:
            review_tasks.celery_app.conf.task_always_eager = eager_before


if __name__ == '__main__':
    unittest.main()
