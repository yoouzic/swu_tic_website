"""Synthetic end-to-end load and safety checks for the automated review path."""

from __future__ import annotations

import io
import json
import logging
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

from sqlalchemy import event

from app.app import app
from app.models import LectureForm, db
from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
    ReviewCategory,
    ScheduleCoverage,
)
from app.review_automation.llm.client import PermanentLLMError
from app.review_automation.llm.schemas import DeepSeekReviewResponse
from app.review_automation.models import ReviewAssessment, ReviewRuleRevision
from app.review_automation.routes import latest_assessment_summaries
from app.review_automation.service import AssessmentService
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


SYNTHETIC_PHONE_SECRET = "SYNTHETIC_PHONE_SECRET_MARKER"
SYNTHETIC_FEEDBACK_SECRET = "SYNTHETIC_FEEDBACK_SECRET_MARKER"


class FakeDeepSeek:
    """A deterministic, non-networked DeepSeek substitute for integration tests."""

    model = "deepseek-v4-flash"
    prompt_version = "synthetic-integration-prompt-v1"

    def __init__(self, failure_ids=()):
        self.failure_ids = set(failure_ids)
        self.payloads = []

    def review(self, payload):
        self.payloads.append(payload)
        form_id = int(payload["form"]["id"])
        if form_id in self.failure_ids:
            raise PermanentLLMError("authentication_failed")
        feedback = payload["form"]["course_feedback"]
        compliance = "unknown" if "SYN_CATEGORY_UNKNOWN" in feedback else "compliant"
        return DeepSeekReviewResponse(
            compliance=compliance,
            summary="synthetic integration summary",
            findings=[],
            suggested_comment="synthetic integration suggestion",
        )


def _review_finding(form, context):
    return (
        FindingDraft(
            rule_key="synthetic_review_rule",
            source=FindingSource.RULE,
            severity=FindingSeverity.REVIEW,
            title="synthetic review evidence",
            message="synthetic evidence requires human review",
            objective=False,
            evidence_strength=EvidenceStrength.APPROXIMATE,
            evidence={"marker": "SYNTHETIC_REVIEW_EVIDENCE"},
        ),
    )


def _high_risk_finding(form, context):
    return (
        FindingDraft(
            rule_key="personal_schedule_conflict",
            source=FindingSource.SCHEDULE,
            severity=FindingSeverity.HIGH,
            title="synthetic exact schedule conflict",
            message="synthetic exact evidence requires human review",
            objective=True,
            evidence_strength=EvidenceStrength.EXACT,
            evidence={"marker": "SYNTHETIC_HIGH_RISK_EVIDENCE"},
        ),
    )


class AutomatedReviewIntegrationTest(unittest.TestCase):
    """Exercise 200 synthetic forms through eager Celery and the real service."""

    def setUp(self):
        self.database = temporary_automation_database()
        self.database.__enter__()
        self.forms = []
        for index in range(200):
            category_marker = (
                "SYN_CATEGORY_CLEAR"
                if index < 50
                else "SYN_CATEGORY_REVIEW"
                if index < 100
                else "SYN_CATEGORY_HIGH"
                if index < 150
                else "SYN_CATEGORY_UNKNOWN"
            )
            form = make_synthetic_form(
                unique_id=8000 + index,
                listener_number=f"SYN-LISTENER-{index:03d}",
                contact_phone1=f"{SYNTHETIC_PHONE_SECRET}_{index:03d}",
                course_feedback=f"{SYNTHETIC_FEEDBACK_SECRET} {category_marker}",
            )
            db.session.add(form)
            self.forms.append(form)
        db.session.flush()
        self.failure_ids = {form.id for form in self.forms[-5:]}
        db.session.add_all([
            ReviewRuleRevision(
                rule_key="synthetic_review_rule",
                version=1,
                handler="safe_regex",
                enabled=True,
                severity="review",
                parameters_json=json.dumps({
                    "phrase": "SYN_CATEGORY_REVIEW",
                    "message": "synthetic review",
                }),
            ),
            ReviewRuleRevision(
                rule_key="synthetic_high_rule",
                version=1,
                handler="personal_schedule_conflict",
                enabled=True,
                severity="high",
                parameters_json="{}",
            ),
        ])
        db.session.commit()
        self.form_ids = [form.id for form in self.forms]
        self.llm = FakeDeepSeek(self.failure_ids)

        from app.review_automation.tasks import review as review_tasks

        self.review_tasks = review_tasks
        self.original_factory = review_tasks.SERVICE_FACTORY
        self.original_eager = review_tasks.celery_app.conf.task_always_eager
        self.original_propagates = review_tasks.celery_app.conf.task_eager_propagates
        review_tasks.celery_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True,
        )
        review_tasks.SERVICE_FACTORY = self.service_factory

    def tearDown(self):
        self.review_tasks.SERVICE_FACTORY = self.original_factory
        self.review_tasks.celery_app.conf.update(
            task_always_eager=self.original_eager,
            task_eager_propagates=self.original_propagates,
        )
        self.database.__exit__(None, None, None)

    def service_factory(self, config=None):
        config = dict(config or {})
        return AssessmentService(
            llm_client=self.llm,
            llm_enabled=True,
            deterministic_runner=self.deterministic_runner,
            schedule_loader=lambda form: SimpleNamespace(
                coverage=ScheduleCoverage.COMPLETE,
                slots=(),
                admin_classes=(),
            ),
            schedule_dependencies={
                "school": {"id": "SYN-SCHOOL", "version": "v1"},
            },
            rule_revisions=config.get("rule_revisions") or {},
        )

    @staticmethod
    def deterministic_runner(form, context):
        index = int(form.listener_number.rsplit("-", 1)[-1])
        if 50 <= index < 100:
            return _review_finding(form, context)
        if 100 <= index < 150:
            return _high_risk_finding(form, context)
        return ()

    @staticmethod
    def protected_bytes(form):
        values = (
            form.status,
            form.reviewer_id,
            form.review_time,
            form.review_comment,
        )
        return tuple(
            b"<NULL>" if value is None else str(value).encode("utf-8")
            for value in values
        )

    def batch_assessments(self, batch):
        db.session.expire_all()
        refreshed = db.session.get(self.review_tasks.ReviewBatch, batch.id)
        snapshot = json.loads(refreshed.snapshot_json)
        ids = [value for value in snapshot.get("assessment_ids", []) if value]
        rows = ReviewAssessment.query.filter(ReviewAssessment.id.in_(ids)).all()
        return refreshed, snapshot, {row.form_id: row for row in rows}

    def assert_batch_database_counts(self, batch, snapshot, assessments):
        counts = Counter(row.classification for row in assessments.values())
        expected = {
            ReviewCategory.CLEAR.value: counts[ReviewCategory.CLEAR.value],
            ReviewCategory.REVIEW.value: counts[ReviewCategory.REVIEW.value],
            ReviewCategory.HIGH_RISK.value: counts[ReviewCategory.HIGH_RISK.value],
            ReviewCategory.UNKNOWN.value: counts[ReviewCategory.UNKNOWN.value],
        }
        self.assertEqual(snapshot["processed_count"], batch.target_form_count)
        self.assertEqual(snapshot["category_counts"], expected)
        self.assertEqual(batch.clear_count, expected[ReviewCategory.CLEAR.value])
        self.assertEqual(batch.review_count, expected[ReviewCategory.REVIEW.value])
        self.assertEqual(batch.high_risk_count, expected[ReviewCategory.HIGH_RISK.value])
        self.assertEqual(batch.unknown_count, expected[ReviewCategory.UNKNOWN.value])

    def test_200_forms_are_idempotent_versioned_and_safe(self):
        protected_before = {
            form.id: self.protected_bytes(form)
            for form in self.forms
        }
        captured_output = io.StringIO()
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with redirect_stdout(captured_output), redirect_stderr(captured_output):
                first_batch = self.review_tasks.enqueue_review_batch(self.form_ids)
        finally:
            root_logger.removeHandler(handler)
            handler.close()

        first_batch, first_snapshot, first_assessments = self.batch_assessments(first_batch)
        self.assertEqual(len(first_assessments), 200)
        self.assertEqual(first_batch.target_form_count, 200)
        self.assertEqual(first_batch.status, "completed_with_errors")
        self.assertEqual(first_batch.failed_count, 5)
        self.assertEqual(
            Counter(row.classification for row in first_assessments.values()),
            Counter({
                ReviewCategory.CLEAR.value: 50,
                ReviewCategory.REVIEW.value: 50,
                ReviewCategory.HIGH_RISK.value: 50,
                ReviewCategory.UNKNOWN.value: 50,
            }),
        )
        self.assertTrue(all(
            first_assessments[form.id].classification != ReviewCategory.CLEAR.value
            for form in self.forms[-5:]
        ))
        self.assertTrue(all(
            first_assessments[form.id].error_code == "authentication_failed"
            for form in self.forms[-5:]
        ))
        self.assert_batch_database_counts(first_batch, first_snapshot, first_assessments)
        self.assertEqual(len(self.llm.payloads), 200)
        self.assertTrue(all(
            SYNTHETIC_PHONE_SECRET in payload["form"]["contact_phone1"]
            and SYNTHETIC_FEEDBACK_SECRET in payload["form"]["course_feedback"]
            for payload in self.llm.payloads
        ))

        second_batch = self.review_tasks.enqueue_review_batch(self.form_ids)
        second_batch, second_snapshot, second_assessments = self.batch_assessments(second_batch)
        self.assertEqual(ReviewAssessment.query.count(), 200)
        self.assertEqual(second_batch.cache_count, 200)
        self.assertEqual(len(self.llm.payloads), 200)
        self.assertTrue(all(item["cache_hit"] for item in second_snapshot["results"]))
        self.assertEqual(
            {form.id: first_assessments[form.id].id for form in self.forms},
            {form.id: second_assessments[form.id].id for form in self.forms},
        )
        self.assert_batch_database_counts(second_batch, second_snapshot, second_assessments)

        db.session.add(ReviewRuleRevision(
            rule_key="synthetic_review_rule",
            version=2,
            handler="safe_regex",
            enabled=True,
            severity="review",
            parameters_json=json.dumps({
                "phrase": "SYN_CATEGORY_REVIEW_V2",
                "message": "synthetic review revision",
            }),
        ))
        db.session.commit()
        affected_ids = [form.id for form in self.forms[50:100]]
        third_batch = self.review_tasks.enqueue_review_batch(affected_ids)
        third_batch, third_snapshot, third_assessments = self.batch_assessments(third_batch)
        affected_id_set = set(affected_ids)
        self.assertEqual(set(third_assessments), affected_id_set)
        for form_id in affected_ids:
            self.assertNotEqual(
                first_assessments[form_id].fingerprint,
                third_assessments[form_id].fingerprint,
            )
        for form in self.forms:
            if form.id not in affected_id_set:
                self.assertEqual(
                    ReviewAssessment.query.filter_by(form_id=form.id).count(),
                    1,
                )
                self.assertEqual(
                    ReviewAssessment.query.filter_by(form_id=form.id).first().fingerprint,
                    first_assessments[form.id].fingerprint,
                )
        self.assertEqual(ReviewAssessment.query.count(), 250)
        self.assertEqual(len(self.llm.payloads), 250)
        self.assert_batch_database_counts(third_batch, third_snapshot, third_assessments)

        with app.test_request_context("/admin/review_forms"):
            merged_summaries = latest_assessment_summaries(self.form_ids)
        self.assertEqual(len(merged_summaries), 200)
        for form in self.forms:
            expected = (
                third_assessments[form.id]
                if form.id in affected_id_set
                else first_assessments[form.id]
            )
            self.assertEqual(merged_summaries[form.id]["assessment_id"], expected.id)

        db.session.expire_all()
        for form_id, expected in protected_before.items():
            self.assertEqual(self.protected_bytes(db.session.get(LectureForm, form_id)), expected)

        query_count = []

        def count_query(*args, **kwargs):
            query_count.append(args[2])

        event.listen(db.engine, "before_cursor_execute", count_query)
        try:
            with app.test_request_context("/admin/review_forms"):
                summaries = latest_assessment_summaries(self.form_ids)
        finally:
            event.remove(db.engine, "before_cursor_execute", count_query)
        self.assertEqual(len(summaries), 200)
        self.assertLessEqual(len(query_count), 3)

        serialized_tasks = json.dumps({
            "batch": {
                "task": self.review_tasks.run_review_batch_task.s("SYN-BATCH-ID").task,
                "args": self.review_tasks.run_review_batch_task.s("SYN-BATCH-ID").args,
            },
            "form": {
                "task": self.review_tasks.assess_form_task.s("SYN-BATCH-ID", 1, False).task,
                "args": self.review_tasks.assess_form_task.s("SYN-BATCH-ID", 1, False).args,
            },
        })
        self.assertNotIn(SYNTHETIC_PHONE_SECRET, serialized_tasks)
        self.assertNotIn(SYNTHETIC_FEEDBACK_SECRET, serialized_tasks)
        self.assertNotIn(SYNTHETIC_PHONE_SECRET, first_batch.config_snapshot_json)
        self.assertNotIn(SYNTHETIC_FEEDBACK_SECRET, first_batch.config_snapshot_json)
        self.assertNotIn(SYNTHETIC_PHONE_SECRET, first_batch.snapshot_json)
        self.assertNotIn(SYNTHETIC_FEEDBACK_SECRET, first_batch.snapshot_json)
        self.assertNotIn(SYNTHETIC_PHONE_SECRET, captured_output.getvalue())
        self.assertNotIn(SYNTHETIC_FEEDBACK_SECRET, captured_output.getvalue())
        self.assertNotIn(SYNTHETIC_PHONE_SECRET, log_stream.getvalue())
        self.assertNotIn(SYNTHETIC_FEEDBACK_SECRET, log_stream.getvalue())


if __name__ == "__main__":
    unittest.main()
