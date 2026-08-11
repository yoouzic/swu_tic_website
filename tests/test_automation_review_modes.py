import unittest
from types import SimpleNamespace

from app.models import db
from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
    ReviewCategory,
    ScheduleCoverage,
)
from app.review_automation.llm.schemas import DeepSeekReviewResponse, ReviewFinding
from app.review_automation.models import ReviewAssessment
from app.review_automation.service import AssessmentService
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


class CountingLLM:
    model = 'deepseek-v4-flash'
    prompt_version = 'synthetic-review-mode-v1'

    def __init__(self):
        self.calls = []

    def review(self, payload):
        self.calls.append(payload)
        return DeepSeekReviewResponse(
            compliance='needs_review',
            summary='合成模式隔离测试',
            findings=[ReviewFinding(
                code='synthetic_llm_finding',
                severity='review',
                message='合成模型建议复核。',
                evidence='SYNTHETIC_LLM_EVIDENCE',
            )],
            suggested_comment='合成模型建议意见',
        )


def complete_schedule(_form):
    return SimpleNamespace(
        coverage=ScheduleCoverage.COMPLETE,
        slots=(),
        admin_classes=(),
    )


def clear_rule(_form, _context):
    return ()


def review_rule(_form, _context):
    return (FindingDraft(
        rule_key='synthetic_mode_rule',
        source=FindingSource.RULE,
        severity=FindingSeverity.REVIEW,
        title='合成规则发现',
        message='合成规则建议复核。',
        objective=False,
        evidence_strength=EvidenceStrength.APPROXIMATE,
        evidence={'marker': 'SYNTHETIC_RULE_EVIDENCE'},
    ),)


class AutomationReviewModesTest(unittest.TestCase):
    def setUp(self):
        self.database = temporary_automation_database()
        self.database.__enter__()
        self.form = make_synthetic_form()
        db.session.add(self.form)
        db.session.commit()

    def tearDown(self):
        self.database.__exit__(None, None, None)

    def build_service(self, llm, *, runner=clear_rule):
        return AssessmentService(
            llm_client=llm,
            llm_enabled=True,
            deterministic_runner=runner,
            schedule_loader=complete_schedule,
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v1'}},
            rule_revisions={'synthetic_mode_rule': 1},
        )

    def assessment(self, summary):
        return db.session.get(ReviewAssessment, summary.assessment_id)

    def protected_snapshot(self):
        return (
            self.form.status,
            self.form.reviewer_id,
            self.form.review_time,
            self.form.review_comment,
        )

    def test_rules_only_never_calls_llm_and_can_return_clear(self):
        llm = CountingLLM()
        rule_calls = []

        def runner(form, context):
            rule_calls.append(form.id)
            return ()

        summary = self.build_service(llm, runner=runner).assess_form(
            self.form.id,
            review_mode='rules_only',
        )

        self.assertEqual(rule_calls, [self.form.id])
        self.assertEqual(llm.calls, [])
        self.assertEqual(summary.category, ReviewCategory.CLEAR.value)
        self.assertIsNone(summary.error_code)

    def test_llm_only_never_runs_rules(self):
        llm = CountingLLM()
        rule_calls = []

        def runner(form, context):
            rule_calls.append(form.id)
            return review_rule(form, context)

        summary = self.build_service(llm, runner=runner).assess_form(
            self.form.id,
            review_mode='llm_only',
        )
        assessment = self.assessment(summary)

        self.assertEqual(rule_calls, [])
        self.assertEqual(len(llm.calls), 1)
        self.assertLessEqual(
            {row.source for row in assessment.findings},
            {FindingSource.LLM.value, FindingSource.SYSTEM.value},
        )

    def test_combined_keeps_rule_and_llm_findings(self):
        llm = CountingLLM()
        rule_calls = []

        def runner(form, context):
            rule_calls.append(form.id)
            return review_rule(form, context)

        summary = self.build_service(llm, runner=runner).assess_form(
            self.form.id,
            review_mode='combined',
        )
        assessment = self.assessment(summary)

        self.assertEqual(rule_calls, [self.form.id])
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue({FindingSource.RULE.value, FindingSource.LLM.value} <= {
            row.source for row in assessment.findings
        })

    def test_modes_have_distinct_fingerprints_and_cache_within_mode(self):
        llm = CountingLLM()
        service = self.build_service(llm, runner=clear_rule)

        first = service.assess_form(self.form.id, review_mode='rules_only')
        second = service.assess_form(self.form.id, review_mode='rules_only')
        third = service.assess_form(self.form.id, review_mode='combined')

        self.assertTrue(second.cache_hit)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(third.fingerprint, first.fingerprint)

    def test_every_mode_preserves_human_fields(self):
        llm = CountingLLM()
        before = self.protected_snapshot()
        service = self.build_service(llm, runner=clear_rule)

        for mode in ('rules_only', 'llm_only', 'combined'):
            service.assess_form(self.form.id, review_mode=mode, force_refresh=True)

        self.assertEqual(self.protected_snapshot(), before)


if __name__ == '__main__':
    unittest.main()
