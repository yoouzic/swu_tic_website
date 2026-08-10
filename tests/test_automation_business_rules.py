import unittest
from datetime import date, datetime
from types import SimpleNamespace

from app.review_automation.contracts import (
    EvidenceStrength,
    FindingSeverity,
    FindingSource,
    ScheduleCoverage,
)
from app.review_automation.rules.history import (
    evaluate_history_rules,
    latest_logical_forms,
)
from app.review_automation.rules.registry import RuleContext, RuleEngine
from app.review_automation.rules.schedule import (
    evaluate_schedule_rules,
    schedule_coverage,
)
from app.review_automation.schedules.repository import ScheduleSlot, SchoolScheduleMatch


def revision(rule_key, handler, parameters=None):
    return {
        'rule_key': rule_key,
        'version': 1,
        'handler': handler,
        'enabled': True,
        'severity': 'review',
        'parameters': parameters or {},
    }


def synthetic_form(**overrides):
    values = {
        'id': 1,
        'unique_id': 1001,
        'listener_number': 'SYN-001',
        'listener_college': '合成学院',
        'lecture_date': '2026-04-20',
        'teaching_week': 2,
        'class_period': '1-2',
        'lecture_location': '合成教室A',
        'teacher_name': '合成教师',
        'teacher_college': '合成学院（旧称）',
        'course_title': '合成课程',
        'course_feedback': '该老师合成课程讲解清楚，课堂安排合理，案例说明具体。',
        'student_signature1': '合成见证人甲',
        'contact_phone1': '13900000001',
        'student_signature2': '',
        'contact_phone2': '',
        'updated_at': datetime(2026, 4, 20, 12, 0),
    }
    values.update(overrides)
    return values


def synthetic_slot(evidence_strength):
    return ScheduleSlot(
        course_title='合成课程',
        weeks=frozenset({2}),
        weekday=1,
        start_period=1,
        end_period=2,
        evidence_strength=evidence_strength,
    )


def synthetic_match(**overrides):
    entry = SimpleNamespace(
        id=7,
        course_title='合成课程',
        teacher_name='合成教师',
        teacher_college='合成学院',
        location='合成教室A',
        weekday=1,
        start_period=1,
        end_period=2,
    )
    values = {
        'entry': entry,
        'confidence': 0.75,
        'matched_fields': ('course_title', 'teacher_name', 'weekday'),
        'mismatched_fields': ('location',),
    }
    values.update(overrides)
    return SchoolScheduleMatch(**values)


class ScheduleRuleTest(unittest.TestCase):
    def test_exact_personal_overlap_is_objective_high_risk(self):
        form = synthetic_form(teacher_college='外部学院')
        schedule = SimpleNamespace(
            coverage=ScheduleCoverage.COMPLETE,
            slots=(synthetic_slot(EvidenceStrength.EXACT),),
            admin_classes=(),
        )

        findings = evaluate_schedule_rules(form, schedule)

        finding = next(item for item in findings if item.rule_key == 'personal_schedule_conflict')
        self.assertEqual(finding.source, FindingSource.SCHEDULE)
        self.assertEqual(finding.severity, FindingSeverity.HIGH)
        self.assertTrue(finding.objective)
        self.assertEqual(finding.evidence_strength, EvidenceStrength.EXACT)

    def test_class_derived_overlap_is_approximate_review_and_never_high_alone(self):
        form = synthetic_form(teacher_college='外部学院')
        schedule = SimpleNamespace(
            coverage=ScheduleCoverage.BASIC,
            slots=(synthetic_slot(EvidenceStrength.APPROXIMATE),),
            admin_classes=('合成行政班',),
        )

        findings = evaluate_schedule_rules(form, schedule)

        finding = next(item for item in findings if item.rule_key == 'class_schedule_conflict')
        self.assertEqual(finding.severity, FindingSeverity.REVIEW)
        self.assertTrue(finding.objective)
        self.assertEqual(finding.evidence_strength, EvidenceStrength.APPROXIMATE)
        self.assertFalse(any(item.severity == FindingSeverity.HIGH for item in findings))

    def test_coverage_distinguishes_basic_and_missing_without_blocking_other_rules(self):
        basic = SimpleNamespace(coverage=ScheduleCoverage.BASIC, slots=(), admin_classes=('合成行政班',))
        self.assertEqual(schedule_coverage(basic), ScheduleCoverage.BASIC)
        self.assertEqual(schedule_coverage(None), ScheduleCoverage.NONE)
        self.assertEqual(evaluate_schedule_rules(synthetic_form(), None), ())

    def test_same_college_is_normalized_and_is_objective_high_risk(self):
        form = synthetic_form(teacher_college='合成学院（旧称）')

        findings = evaluate_schedule_rules(
            form,
            None,
            listener_college='合成学院',
            college_aliases={'合成学院（旧称）': '合成学院'},
        )

        finding = next(item for item in findings if item.rule_key == 'same_college_teacher')
        self.assertEqual(finding.severity, FindingSeverity.HIGH)
        self.assertTrue(finding.objective)
        self.assertEqual(finding.evidence_strength, EvidenceStrength.EXACT)

    def test_school_mismatch_reports_candidates_and_confidence_without_promoting_ambiguity(self):
        form = synthetic_form(teacher_college='外部学院', listener_college='合成学院')
        matches = (
            synthetic_match(confidence=0.75, mismatched_fields=('location',)),
            synthetic_match(confidence=0.75, mismatched_fields=('teacher_college',)),
        )

        findings = evaluate_schedule_rules(form, None, school_matches=matches)

        finding = next(item for item in findings if item.rule_key == 'school_schedule_mismatch')
        self.assertEqual(finding.severity, FindingSeverity.REVIEW)
        self.assertEqual(finding.source, FindingSource.SCHEDULE)
        self.assertEqual(finding.evidence['candidate_count'], 2)
        self.assertEqual(finding.evidence['match_confidence'], [0.75, 0.75])
        self.assertEqual(finding.evidence['ambiguous'], True)
        self.assertFalse(any(item.severity == FindingSeverity.HIGH for item in findings))

    def test_school_mismatch_evidence_identifies_candidate_entries_without_personal_data(self):
        form = synthetic_form(teacher_college='synthetic-external-college', listener_college='synthetic-college')
        matches = (
            SimpleNamespace(
                entry=SimpleNamespace(id=7),
                confidence=0.8,
                matched_fields=('course_title',),
                mismatched_fields=('location',),
            ),
            SimpleNamespace(
                entry=SimpleNamespace(id=''),
                id='candidate-8',
                confidence=0.7,
                matched_fields=('teacher_name',),
                mismatched_fields=('weekday',),
            ),
            SimpleNamespace(
                entry=SimpleNamespace(id=None),
                id='',
                confidence=0.6,
                matched_fields=('course_title',),
                mismatched_fields=('teacher_college',),
            ),
        )

        findings = evaluate_schedule_rules(form, None, school_matches=matches)

        finding = next(item for item in findings if item.rule_key == 'school_schedule_mismatch')
        self.assertEqual(finding.evidence['candidate_entry_ids'], [7, 'candidate-8'])

    def test_registry_dispatches_schedule_handler_through_fixed_map(self):
        schedule = SimpleNamespace(
            coverage=ScheduleCoverage.COMPLETE,
            slots=(synthetic_slot(EvidenceStrength.EXACT),),
            admin_classes=(),
        )
        findings = RuleEngine([
            revision('personal', 'personal_schedule_conflict'),
        ]).evaluate(context=RuleContext(form=synthetic_form(), schedule=schedule))

        self.assertEqual(findings[0].rule_key, 'personal')
        self.assertEqual(findings[0].severity, FindingSeverity.HIGH)


class HistoryRuleTest(unittest.TestCase):
    def test_same_listener_same_phone_across_weeks_is_review_and_name_plus_phone_confirms(self):
        first = synthetic_form(id=1, unique_id=1001, teaching_week=1, lecture_date='2026-04-13')
        second = synthetic_form(id=2, unique_id=1002, teaching_week=2, lecture_date='2026-04-20')

        findings = evaluate_history_rules(second, (first, second))

        reuse = next(item for item in findings if item.rule_key == 'witness_reused_across_weeks')
        self.assertEqual(reuse.severity, FindingSeverity.REVIEW)
        self.assertEqual(reuse.evidence['match_mode'], 'name_and_phone')
        self.assertTrue(reuse.evidence['identity_confirmed'])

    def test_same_name_different_phone_is_weak_and_different_name_same_phone_is_separate_conflict(self):
        same_name = synthetic_form(id=1, unique_id=1001, teaching_week=1, contact_phone1='13900000001')
        changed_phone = synthetic_form(id=2, unique_id=1002, teaching_week=2, contact_phone1='13900000002')
        conflict_name = synthetic_form(
            id=3,
            unique_id=1003,
            teaching_week=3,
            student_signature1='另一位见证人乙',
            contact_phone1='13900000001',
        )

        findings = evaluate_history_rules(
            conflict_name,
            (same_name, changed_phone, conflict_name),
        )

        weak = next(item for item in findings if item.evidence.get('match_mode') == 'same_name_different_phone')
        self.assertEqual(weak.evidence_strength, EvidenceStrength.WEAK)
        self.assertFalse(weak.objective)
        conflict = next(item for item in findings if item.rule_key == 'witness_phone_name_conflict')
        self.assertEqual(conflict.evidence['anomaly'], 'different_name_same_phone')

    def test_different_listeners_sharing_a_witness_does_not_trigger_reuse(self):
        first = synthetic_form(id=1, unique_id=1001, listener_number='SYN-001', teaching_week=1)
        second = synthetic_form(id=2, unique_id=1002, listener_number='SYN-002', teaching_week=2)

        findings = evaluate_history_rules(second, (first, second))

        self.assertFalse(any(item.rule_key == 'witness_reused_across_weeks' for item in findings))

    def test_three_distinct_weeks_is_only_a_high_risk_candidate(self):
        records = [
            synthetic_form(id=week, unique_id=1000 + week, teaching_week=week)
            for week in (1, 2, 3)
        ]

        findings = evaluate_history_rules(records[-1], records)

        finding = next(item for item in findings if item.rule_key == 'witness_reused_across_weeks')
        self.assertEqual(finding.severity, FindingSeverity.HIGH)
        self.assertTrue(finding.evidence['high_risk_candidate'])
        self.assertFalse(finding.objective)

    def test_consecutive_teacher_weeks_trigger_but_nonconsecutive_weeks_do_not(self):
        first = synthetic_form(id=1, unique_id=1001, teaching_week=1)
        consecutive = synthetic_form(id=2, unique_id=1002, teaching_week=2)
        findings = evaluate_history_rules(consecutive, (first, consecutive))
        self.assertTrue(any(item.rule_key == 'consecutive_teacher_weeks' for item in findings))

        nonconsecutive = synthetic_form(id=3, unique_id=1003, teaching_week=3)
        findings = evaluate_history_rules(nonconsecutive, (first, nonconsecutive))
        self.assertFalse(any(item.rule_key == 'consecutive_teacher_weeks' for item in findings))

    def test_same_listener_same_exact_slot_across_distinct_logical_forms_is_objective_high(self):
        first = synthetic_form(id=1, unique_id=1001, teaching_week=2)
        second = synthetic_form(id=2, unique_id=1002, teaching_week=2)

        findings = evaluate_history_rules(second, (first, second))

        finding = next(item for item in findings if item.rule_key == 'same_listener_same_slot')
        self.assertEqual(finding.severity, FindingSeverity.HIGH)
        self.assertTrue(finding.objective)
        self.assertEqual(finding.evidence_strength, EvidenceStrength.EXACT)

    def test_latest_logical_versions_remove_exports_and_old_versions_before_counts(self):
        old = synthetic_form(id=1, unique_id=1001, teaching_week=1, updated_at=datetime(2026, 4, 13))
        latest = synthetic_form(id=2, unique_id=1001, teaching_week=1, updated_at=datetime(2026, 4, 14))
        other = synthetic_form(id=3, unique_id=1002, teaching_week=2)

        latest_rows = latest_logical_forms((old, latest, other))
        self.assertEqual({row['id'] for row in latest_rows}, {2, 3})
        findings = evaluate_history_rules(other, (old, latest, other))
        reuse = next(item for item in findings if item.rule_key == 'witness_reused_across_weeks')
        self.assertEqual(reuse.evidence['distinct_form_count'], 2)

    def test_latest_logical_versions_tie_break_numeric_ids_and_stabilize_text_ids(self):
        timestamp = datetime(2026, 4, 20, 12, 0)
        lower = synthetic_form(id=9, unique_id=1001, updated_at=timestamp)
        higher = synthetic_form(id=10, unique_id=1001, updated_at=timestamp)

        numeric_latest = latest_logical_forms((lower, higher))
        self.assertEqual([row['id'] for row in numeric_latest], [10])

        text_first = synthetic_form(id='FORM-B', unique_id=1002, updated_at=timestamp)
        text_second = synthetic_form(id='FORM-A', unique_id=1002, updated_at=timestamp)
        forward = latest_logical_forms((text_first, text_second))
        reverse = latest_logical_forms((text_second, text_first))
        self.assertEqual(forward[0]['id'], reverse[0]['id'])

    def test_similar_feedback_excludes_same_logical_version_and_respects_required_prefix(self):
        current = synthetic_form(id=1, unique_id=1001, teaching_week=1)
        same_logical_export = synthetic_form(id=2, unique_id=1001, teaching_week=1)
        other = synthetic_form(id=3, unique_id=1002, teaching_week=2)

        findings = evaluate_history_rules(other, (current, same_logical_export, other))

        self.assertTrue(any(item.rule_key == 'feedback_similarity' for item in findings))
        no_prefix = synthetic_form(
            id=4,
            unique_id=1003,
            teaching_week=3,
            course_feedback='合成课程反馈内容与其他记录高度相似',
        )
        findings = evaluate_history_rules(no_prefix, (other, no_prefix))
        self.assertFalse(any(item.rule_key == 'feedback_similarity' for item in findings))

    def test_registry_dispatches_history_handler_through_fixed_map(self):
        first = synthetic_form(id=1, unique_id=1001, teaching_week=1)
        second = synthetic_form(id=2, unique_id=1002, teaching_week=2)
        findings = RuleEngine([
            revision('history', 'consecutive_teacher_weeks', {'maximum_week_gap': 1}),
        ]).evaluate(context=RuleContext(form=second, history=(first, second)))

        self.assertTrue(any(item.rule_key == 'history' for item in findings))


if __name__ == '__main__':
    unittest.main()
