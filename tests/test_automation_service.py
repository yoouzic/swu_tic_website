import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from app.models import LectureForm, db
from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
    ReviewCategory,
    ScheduleCoverage,
)
from app.review_automation.llm.client import (
    InvalidLLMResponse,
    PermanentLLMError,
    TransientLLMError,
)
from app.review_automation.llm.schemas import DeepSeekReviewResponse
from app.review_automation.models import ReviewAssessment, SchoolScheduleEntry
from app.review_automation.schedules.repository import ScheduleSlot, SchoolScheduleMatch
from app.review_automation.service import AssessmentService as AssessmentServiceForTest
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


class FakeLLM:
    model = 'deepseek-v4-flash'
    prompt_version = 'synthetic-prompt-v1'

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def review(self, form_payload):
        self.calls.append(form_payload)
        response = self.responses.pop(0) if self.responses else DeepSeekReviewResponse(
            compliance='compliant',
            summary='合成模型摘要',
            findings=[],
            suggested_comment='合成建议草稿',
        )
        if isinstance(response, BaseException):
            raise response
        return response


def deterministic_review_finding(form, context):
    return (
        FindingDraft(
            rule_key='synthetic_deterministic_rule',
            source=FindingSource.RULE,
            severity=FindingSeverity.REVIEW,
            title='合成确定性发现',
            message='合成规则建议人工复核。',
            objective=False,
            evidence_strength=EvidenceStrength.APPROXIMATE,
            evidence={'marker': 'SYNTHETIC_RULE_EVIDENCE'},
        ),
    )


def complete_schedule(form):
    return SimpleNamespace(coverage=ScheduleCoverage.COMPLETE, slots=(), admin_classes=())


class AssessmentServiceTest(unittest.TestCase):
    def setUp(self):
        self.database = temporary_automation_database()
        self.database.__enter__()
        self.form = make_synthetic_form()
        db.session.add(self.form)
        db.session.commit()

    def tearDown(self):
        self.database.__exit__(None, None, None)

    def protected_snapshot(self, form=None):
        form = form or self.form
        return (
            form.status,
            form.reviewer_id,
            form.review_time,
            form.review_comment,
        )

    def assert_protected_unchanged(self, snapshot, form=None):
        self.assertEqual(snapshot, self.protected_snapshot(form))

    def service(
        self,
        llm,
        *,
        runner=None,
        schedule_dependencies=None,
        rule_revisions=None,
        llm_enabled=True,
    ):
        from app.review_automation.service import AssessmentService

        return AssessmentService(
            llm_client=llm,
            llm_enabled=llm_enabled,
            deterministic_runner=runner,
            schedule_loader=complete_schedule,
            schedule_dependencies=schedule_dependencies or {'school': {'id': 'SYN-SCHOOL', 'version': 'v1'}},
            rule_revisions=rule_revisions or {'synthetic_rule': 1},
        )

    def test_loads_latest_logical_form_before_normalizing(self):
        old = make_synthetic_form(
            unique_id=2002,
            course_feedback='SYNTHETIC_OLD_FEEDBACK',
            updated_at=datetime(2026, 4, 1, 9, 0, 0),
        )
        latest = make_synthetic_form(
            unique_id=2002,
            course_feedback='SYNTHETIC_LATEST_FEEDBACK',
            updated_at=datetime(2026, 4, 2, 9, 0, 0),
        )
        db.session.add_all([old, latest])
        db.session.commit()
        snapshot = self.protected_snapshot(latest)
        llm = FakeLLM()

        from app.review_automation.service import AssessmentService
        result = AssessmentService(
            llm_client=llm,
            llm_enabled=True,
            deterministic_runner=lambda form, context: (),
            schedule_loader=complete_schedule,
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v1'}},
            rule_revisions={'synthetic_rule': 1},
        ).assess_form(old.id)

        self.assertEqual(result.form_id, latest.id)
        self.assertEqual(llm.calls[0]['form']['course_feedback'], latest.course_feedback)
        self.assert_protected_unchanged(snapshot, latest)

    def test_identical_fingerprint_reuses_assessment_without_extra_llm_call(self):
        snapshot = self.protected_snapshot()
        llm = FakeLLM()
        service = self.service(llm, runner=lambda form, context: ())

        first = service.assess_form(self.form.id)
        second = service.assess_form(self.form.id)

        self.assertEqual(first.assessment_id, second.assessment_id)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(len(llm.calls), 1)
        self.assert_protected_unchanged(snapshot)

    def test_error_assessment_is_not_reused_as_successful_cache(self):
        snapshot = self.protected_snapshot()
        llm = FakeLLM(responses=[InvalidLLMResponse('truncated_output')])
        service = self.service(llm, runner=lambda form, context: ())

        failed = service.assess_form(self.form.id)
        self.assertEqual(failed.error_code, 'truncated_output')

        recovered = service.assess_form(self.form.id)

        self.assertFalse(recovered.cache_hit)
        self.assertNotEqual(failed.assessment_id, recovered.assessment_id)
        self.assertEqual(len(llm.calls), 2)
        self.assertIsNone(recovered.error_code)
        self.assert_protected_unchanged(snapshot)

    def test_force_refresh_adds_nonce_and_preserves_old_assessment(self):
        snapshot = self.protected_snapshot()
        llm = FakeLLM()
        service = self.service(llm, runner=lambda form, context: ())

        first = service.assess_form(self.form.id)
        refreshed = service.assess_form(self.form.id, force_refresh=True)

        self.assertNotEqual(first.assessment_id, refreshed.assessment_id)
        self.assertNotEqual(first.fingerprint, refreshed.fingerprint)
        self.assertEqual(ReviewAssessmentCount.count(), 2)
        self.assertEqual(len(llm.calls), 2)
        self.assert_protected_unchanged(snapshot)

    def test_form_schedule_and_rule_versions_invalidate_cache(self):
        snapshot = self.protected_snapshot()
        llm = FakeLLM()
        base = self.service(llm, runner=lambda form, context: ())
        first = base.assess_form(self.form.id)

        changed_schedule = self.service(
            llm,
            runner=lambda form, context: (),
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v2'}},
        ).assess_form(self.form.id)
        changed_rule = self.service(
            llm,
            runner=lambda form, context: (),
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v2'}},
            rule_revisions={'synthetic_rule': 2},
        ).assess_form(self.form.id)

        self.form.course_feedback = 'SYNTHETIC_CHANGED_FEEDBACK'
        db.session.commit()
        changed_form = self.service(
            llm,
            runner=lambda form, context: (),
            schedule_dependencies={'school': {'id': 'SYN-SCHOOL', 'version': 'v2'}},
            rule_revisions={'synthetic_rule': 2},
        ).assess_form(self.form.id)

        self.assertEqual(len({
            first.assessment_id,
            changed_schedule.assessment_id,
            changed_rule.assessment_id,
            changed_form.assessment_id,
        }), 4)
        self.assertEqual(len(llm.calls), 4)
        self.assert_protected_unchanged(snapshot)

    def test_llm_failures_keep_deterministic_findings_and_add_system_finding(self):
        snapshot = self.protected_snapshot()
        failures = [
            TransientLLMError('timeout'),
            PermanentLLMError('authentication_failed'),
            InvalidLLMResponse('schema_validation'),
        ]
        llm = FakeLLM(responses=failures)
        service = self.service(llm, runner=deterministic_review_finding)

        for index, expected in enumerate(('timeout', 'authentication_failed', 'schema_validation')):
            with self.subTest(expected=expected):
                summary = service.assess_form(self.form.id, force_refresh=index > 0)
                assessment = db.session.get(ReviewAssessment, summary.assessment_id)
                self.assertEqual(assessment.error_code, expected)
                finding_sources = {finding.source for finding in assessment.findings}
                finding_rules = {finding.rule_key for finding in assessment.findings}
                self.assertIn(FindingSource.SYSTEM.value, finding_sources)
                self.assertIn('synthetic_deterministic_rule', finding_rules)
                system_rules = [
                    item.rule_key
                    for item in assessment.findings
                    if item.source == FindingSource.SYSTEM.value
                ]
                self.assertEqual(system_rules.count('llm_error'), 1)
                self.assertNotIn('llm_unavailable', system_rules)
                self.assertNotEqual(summary.category, ReviewCategory.CLEAR.value)
        self.assert_protected_unchanged(snapshot)

    def test_disabled_llm_is_unknown_with_one_unavailable_finding(self):
        snapshot = self.protected_snapshot()
        summary = self.service(
            None,
            runner=lambda form, context: (),
            llm_enabled=False,
        ).assess_form(self.form.id)

        assessment = db.session.get(ReviewAssessment, summary.assessment_id)
        system_findings = [
            item for item in assessment.findings
            if item.source == FindingSource.SYSTEM.value
        ]
        self.assertEqual(summary.category, ReviewCategory.UNKNOWN.value)
        self.assertEqual(summary.error_code, 'llm_unavailable')
        self.assertEqual([item.rule_key for item in system_findings], ['llm_unavailable'])
        self.assert_protected_unchanged(snapshot)

    def test_missing_llm_client_is_unknown_with_one_unavailable_finding(self):
        snapshot = self.protected_snapshot()
        summary = self.service(
            None,
            runner=lambda form, context: (),
        ).assess_form(self.form.id)

        assessment = db.session.get(ReviewAssessment, summary.assessment_id)
        system_findings = [
            item for item in assessment.findings
            if item.source == FindingSource.SYSTEM.value
        ]
        self.assertEqual(summary.category, ReviewCategory.UNKNOWN.value)
        self.assertEqual(summary.error_code, 'llm_unavailable')
        self.assertEqual([item.rule_key for item in system_findings], ['llm_unavailable'])
        self.assert_protected_unchanged(snapshot)

    def test_full_form_reaches_llm_but_suggestion_stays_separate_from_review_comment(self):
        snapshot = self.protected_snapshot()
        llm = FakeLLM()
        summary = self.service(llm, runner=lambda form, context: ()).assess_form(self.form.id)

        assessment = db.session.get(ReviewAssessment, summary.assessment_id)
        self.assertEqual(llm.calls[0]['form']['contact_phone1'], '13900000001')
        self.assertEqual(llm.calls[0]['form']['course_feedback'], self.form.course_feedback)
        self.assertEqual(assessment.suggested_comment, '合成建议草稿')
        self.assertEqual(assessment.validated_model_json, json.dumps({
            'compliance': 'compliant',
            'summary': '合成模型摘要',
            'findings': [],
            'suggested_comment': '合成建议草稿',
        }, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        self.assertNotIn('reasoning_content', assessment.validated_model_json)
        self.assert_protected_unchanged(snapshot)

    def test_llm_receives_json_safe_full_form_and_bounded_review_context(self):
        snapshot = self.protected_snapshot()
        history_rows = [
            make_synthetic_form(
                unique_id=5000 + index,
                listener_number=self.form.listener_number,
                course_feedback=f'SYNTHETIC_RELATED_HISTORY_{index}',
            )
            for index in range(5)
        ]
        unrelated = make_synthetic_form(
            unique_id=6000,
            listener_number='SYN-UNRELATED-LISTENER',
            course_feedback='SYNTHETIC_UNRELATED_HISTORY',
        )
        db.session.add_all([*history_rows, unrelated])
        db.session.commit()

        school_entry = SchoolScheduleEntry(
            id=701,
            dataset_id='SYN-SCHOOL-DATASET',
            teacher_name='SYN-TEACHER',
            teacher_college='SYN-TEACHER-COLLEGE',
            course_title='SYN-COURSE',
            teaching_class='SYN-CLASS-001',
            major='SYN-MAJOR',
            weeks_json='[1]',
            weekday=1,
            start_period=1,
            end_period=2,
            location='SYN-ROOM',
        )
        school_match = SchoolScheduleMatch(
            entry=school_entry,
            confidence=0.75,
            matched_fields=('course_title', 'weekday'),
            mismatched_fields=('teacher_college',),
        )
        schedule = SimpleNamespace(
            coverage=ScheduleCoverage.COMPLETE,
            admin_classes=('SYN-CLASS-001',),
            slots=(
                ScheduleSlot(
                    course_title='SYN-PERSONAL-COURSE',
                    weeks=frozenset({1}),
                    weekday=1,
                    start_period=1,
                    end_period=2,
                    evidence_strength=EvidenceStrength.EXACT,
                ),
                ScheduleSlot(
                    course_title='SYN-CLASS-COURSE',
                    weeks=frozenset({1}),
                    weekday=1,
                    start_period=3,
                    end_period=4,
                    evidence_strength=EvidenceStrength.APPROXIMATE,
                ),
            ),
        )
        deterministic = FindingDraft(
            rule_key='synthetic_deterministic_rule',
            source=FindingSource.RULE,
            severity=FindingSeverity.REVIEW,
            title='SYNTHETIC_RULE_TITLE',
            message='SYNTHETIC_RULE_MESSAGE',
            objective=True,
            evidence_strength=EvidenceStrength.APPROXIMATE,
            evidence={'marker': 'SYNTHETIC_RULE_EVIDENCE'},
        )
        context_seen = []

        def runner(form, context):
            context_seen.append(context)
            return (deterministic,)

        llm = FakeLLM()
        service = AssessmentServiceForTest(
            llm_client=llm,
            llm_enabled=True,
            deterministic_runner=runner,
            schedule_loader=lambda form: schedule,
            school_matches_loader=lambda form: (school_match,),
            history_loader=lambda form: [*history_rows, unrelated],
            schedule_dependencies={
                'school': {'id': 'SYN-SCHOOL-DATASET', 'sha256': 'SYN-SCHOOL-SHA', 'version': 'v7'},
                'personal': {'id': 'SYN-PERSONAL-DATASET', 'sha256': 'SYN-PERSONAL-SHA', 'version': 'v3'},
            },
            rule_revisions={
                'synthetic_deterministic_rule': {
                    'id': 17,
                    'version': 4,
                    'handler': 'synthetic_deterministic_rule',
                    'enabled': True,
                    'severity': 'review',
                    'parameters': {'synthetic': True},
                },
            },
            semester='SYN-2026-SPRING',
            semester_monday='2026-04-13',
            model_id='synthetic-model-v2',
            prompt_version='synthetic-prompt-v2',
        )

        summary = service.assess_form(self.form.id)
        payload = llm.calls[0]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        assessment = db.session.get(ReviewAssessment, summary.assessment_id)

        self.assertTrue(context_seen)
        self.assertEqual(payload['form']['contact_phone1'], '13900000001')
        self.assertEqual(payload['form']['course_feedback'], self.form.course_feedback)
        self.assertEqual(payload['schedule_comparison']['coverage'], 'complete')
        self.assertEqual(payload['schedule_comparison']['admin_classes'], ['SYN-CLASS-001'])
        strengths = {
            item['evidence_strength']
            for item in payload['schedule_comparison']['slots']
        }
        self.assertEqual(strengths, {'exact', 'approximate'})
        self.assertEqual(payload['schedule_comparison']['school_matches'][0]['entry_id'], 701)
        self.assertNotIn('entry', payload['schedule_comparison']['school_matches'][0])
        self.assertEqual(payload['rule_evidence'][0]['rule_key'], 'synthetic_deterministic_rule')
        self.assertEqual(payload['rule_evidence'][0]['source'], 'rule')
        self.assertEqual(payload['rule_evidence'][0]['evidence']['marker'], 'SYNTHETIC_RULE_EVIDENCE')
        self.assertLessEqual(len(payload['history_summary']['items']), 3)
        self.assertEqual(
            {item['listener_number'] for item in payload['history_summary']['items']},
            {self.form.listener_number},
        )
        self.assertNotIn('SYN-UNRELATED-LISTENER', encoded)
        self.assertEqual(payload['dependency_context']['semester'], 'SYN-2026-SPRING')
        self.assertEqual(payload['dependency_context']['datasets']['school']['version'], 'v7')
        self.assertEqual(payload['dependency_context']['rules']['synthetic_deterministic_rule']['id'], 17)
        self.assertEqual(payload['dependency_context']['model_id'], 'synthetic-model-v2')
        self.assertEqual(payload['dependency_context']['prompt_version'], 'synthetic-prompt-v2')
        from app.review_automation.llm.prompts import PROMPT_VERSION, build_review_messages
        from app.app import app
        prompt_user_content = build_review_messages(payload)[1]['content']
        self.assertNotEqual(PROMPT_VERSION, '2026-08-10-v1')
        self.assertNotEqual(app.config['DEEPSEEK_PROMPT_VERSION'], '2026-08-10-v1')
        self.assertIn('完整表单', prompt_user_content)
        self.assertIn('schedule_comparison', prompt_user_content)
        self.assertNotIn('reasoning_content', encoded)
        self.assertNotIn('raw_response', encoded)
        self.assertNotIn('raw_payload', encoded)
        validated_model = json.loads(assessment.validated_model_json)
        self.assertEqual(
            set(validated_model),
            {'compliance', 'summary', 'findings', 'suggested_comment'},
        )
        self.assert_protected_unchanged(snapshot)


class ReviewAssessmentCount:
    @staticmethod
    def count():
        from app.review_automation.models import ReviewAssessment

        return ReviewAssessment.query.count()


if __name__ == '__main__':
    unittest.main()
