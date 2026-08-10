import unittest

from app.review_automation.classification import (
    ClassificationResult,
    aggregate_classification,
)
from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
    ReviewCategory,
    ScheduleCoverage,
)
from app.review_automation.fingerprints import (
    build_assessment_fingerprint,
    canonical_json,
)


def finding(
    rule_key,
    *,
    severity=FindingSeverity.REVIEW,
    source=FindingSource.RULE,
    objective=False,
    strength=EvidenceStrength.APPROXIMATE,
):
    return FindingDraft(
        rule_key=rule_key,
        source=source,
        severity=severity,
        title='合成发现',
        message='合成证据',
        objective=objective,
        evidence_strength=strength,
        evidence={'synthetic': True},
    )


def fingerprint(**overrides):
    values = {
        'form': {
            'id': 1,
            'unique_id': 1001,
            'listener_number': 'SYN-001',
            'course_title': '合成课程',
            'course_feedback': '该老师合成反馈内容',
            'status': '待审核',
            'reviewer_id': None,
            'review_time': None,
            'review_comment': '合成原始意见',
        },
        'form_version': 'v1',
        'schedule_dependencies': {'school': 'dataset-1', 'personal': 'dataset-2'},
        'rule_revisions': {'prefix': 1, 'length': 1},
        'prompt_version': 'prompt-synthetic-v1',
        'model_name': 'synthetic-model',
    }
    values.update(overrides)
    return build_assessment_fingerprint(**values)


class FingerprintTest(unittest.TestCase):
    def test_canonical_json_is_stable_and_sha256_fingerprint_is_reusable(self):
        self.assertEqual(canonical_json({'b': 2, 'a': 1}), '{"a":1,"b":2}')
        self.assertEqual(fingerprint(), fingerprint())
        self.assertEqual(len(fingerprint()), 64)

    def test_protected_human_review_columns_do_not_invalidate_fingerprint(self):
        changed = fingerprint(form=fingerprint_form_with_review('已通过', 9, '人工结果'))

        self.assertEqual(fingerprint(), changed)

    def test_form_content_version_dependencies_prompt_and_model_each_invalidate_cache(self):
        base = fingerprint()
        variants = [
            fingerprint(form={'id': 1, 'unique_id': 1001, 'course_title': '另一合成课程'}),
            fingerprint(form_version='v2'),
            fingerprint(schedule_dependencies={'school': 'dataset-9'}),
            fingerprint(rule_revisions={'prefix': 2, 'length': 1}),
            fingerprint(prompt_version='prompt-synthetic-v2'),
            fingerprint(model_name='synthetic-model-v2'),
        ]

        self.assertEqual(len(set(variants)), len(variants))
        self.assertNotIn(base, variants)


def fingerprint_form_with_review(status, reviewer_id, review_comment):
    return {
        'id': 1,
        'unique_id': 1001,
        'listener_number': 'SYN-001',
        'course_title': '合成课程',
        'course_feedback': '该老师合成反馈内容',
        'status': status,
        'reviewer_id': reviewer_id,
        'review_time': 'synthetic-time',
        'review_comment': review_comment,
    }


class ClassificationTest(unittest.TestCase):
    def assert_category(self, expected, findings=(), **kwargs):
        result = aggregate_classification(findings, **kwargs)
        self.assertIsInstance(result, ClassificationResult)
        self.assertEqual(result.category, expected)
        self.assertNotIn('approve', result.rationale_keys)
        self.assertNotIn('reject', result.rationale_keys)
        return result

    def test_no_findings_with_complete_dependencies_is_clear(self):
        self.assert_category(
            ReviewCategory.CLEAR,
            coverage=ScheduleCoverage.COMPLETE,
            llm_available=True,
        )

    def test_any_review_finding_is_review_recommended(self):
        self.assert_category(ReviewCategory.REVIEW, [finding('short_feedback')])

    def test_exact_personal_conflict_is_high_risk(self):
        self.assert_category(
            ReviewCategory.HIGH_RISK,
            [finding(
                'personal_schedule_conflict',
                severity=FindingSeverity.HIGH,
                source=FindingSource.SCHEDULE,
                objective=True,
                strength=EvidenceStrength.EXACT,
            )],
        )

    def test_same_college_and_same_slot_are_strong_high_risk_evidence(self):
        for key in ('same_college_teacher', 'same_listener_same_slot'):
            with self.subTest(key=key):
                self.assert_category(
                    ReviewCategory.HIGH_RISK,
                    [finding(
                        key,
                        severity=FindingSeverity.HIGH,
                        source=FindingSource.SCHEDULE if key == 'same_college_teacher' else FindingSource.HISTORY,
                        objective=True,
                        strength=EvidenceStrength.EXACT,
                    )],
                )

    def test_llm_high_alone_is_only_review_recommended(self):
        self.assert_category(
            ReviewCategory.REVIEW,
            llm_result={'suggested_classification': 'high_risk_suspected'},
            llm_available=True,
        )

    def test_model_needs_review_without_findings_is_review_recommended(self):
        self.assert_category(
            ReviewCategory.REVIEW,
            coverage=ScheduleCoverage.COMPLETE,
            llm_result={'compliance': 'needs_review'},
            llm_available=True,
        )

    def test_model_unknown_without_stronger_evidence_is_unknown(self):
        self.assert_category(
            ReviewCategory.UNKNOWN,
            coverage=ScheduleCoverage.COMPLETE,
            llm_result={'compliance': 'unknown'},
            llm_available=True,
        )

    def test_llm_review_finding_is_not_discarded(self):
        self.assert_category(
            ReviewCategory.REVIEW,
            [finding('llm_semantic_review', source=FindingSource.LLM)],
            coverage=ScheduleCoverage.COMPLETE,
            llm_available=True,
        )

    def test_model_high_alone_is_only_review_recommended(self):
        self.assert_category(
            ReviewCategory.REVIEW,
            coverage=ScheduleCoverage.COMPLETE,
            llm_result={'compliance': 'high_risk'},
            llm_available=True,
        )

    def test_three_week_witness_candidate_alone_remains_review_recommended(self):
        self.assert_category(
            ReviewCategory.REVIEW,
            [finding(
                'witness_reused_across_weeks',
                severity=FindingSeverity.HIGH,
                source=FindingSource.HISTORY,
                objective=False,
                strength=EvidenceStrength.EXACT,
            )],
        )

    def test_llm_high_with_objective_review_evidence_can_be_high_risk(self):
        self.assert_category(
            ReviewCategory.HIGH_RISK,
            [finding(
                'class_schedule_conflict',
                severity=FindingSeverity.REVIEW,
                source=FindingSource.SCHEDULE,
                objective=True,
                strength=EvidenceStrength.APPROXIMATE,
            )],
            llm_result={'suggested_classification': 'high_risk_suspected'},
            llm_available=True,
        )

    def test_llm_unavailable_does_not_hide_deterministic_strong_evidence(self):
        result = self.assert_category(
            ReviewCategory.HIGH_RISK,
            [finding(
                'personal_schedule_conflict',
                severity=FindingSeverity.HIGH,
                source=FindingSource.SCHEDULE,
                objective=True,
                strength=EvidenceStrength.EXACT,
            )],
            llm_available=False,
        )
        self.assertTrue(any(item.source == FindingSource.SYSTEM for item in result.findings))

    def test_llm_unavailable_without_other_evidence_is_unknown(self):
        result = self.assert_category(
            ReviewCategory.UNKNOWN,
            llm_available=False,
        )
        self.assertTrue(any(item.rule_key == 'llm_unavailable' for item in result.findings))

    def test_missing_schedule_with_text_or_history_evidence_remains_review(self):
        result = self.assert_category(
            ReviewCategory.REVIEW,
            [finding('feedback_similarity', source=FindingSource.HISTORY)],
            coverage=ScheduleCoverage.NONE,
            llm_available=True,
        )
        self.assertTrue(any(item.rule_key == 'schedule_data_missing' for item in result.findings))

    def test_critical_parse_failure_without_stronger_evidence_is_unknown(self):
        self.assert_category(
            ReviewCategory.UNKNOWN,
            coverage=ScheduleCoverage.COMPLETE,
            critical_parse_failure=True,
            llm_available=True,
        )


if __name__ == '__main__':
    unittest.main()
