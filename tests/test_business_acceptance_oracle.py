from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from app.review_automation.classification import aggregate_classification
from app.review_automation.contracts import EvidenceStrength, ScheduleCoverage
from app.review_automation.rules.history import evaluate_history_rules
from app.review_automation.rules.registry import RuleContext, RuleEngine
from app.review_automation.rules.schedule import evaluate_schedule_rules
from app.review_automation.schedules.repository import ScheduleSlot
from tools.business_acceptance.config import AcceptanceConfig
from tools.business_acceptance.generator import build_acceptance_school_schedule, generate_manifest


MARKER_RULES = {
    'personal_schedule_exact_conflict': 'personal_schedule_conflict',
    'class_schedule_approximate_conflict': 'class_schedule_conflict',
    'same_college_teacher': 'same_college_teacher',
    'repeated_witness_across_weeks': 'witness_reused_across_weeks',
    'consecutive_teacher_weeks': 'consecutive_teacher_weeks',
    'same_time_conflict': 'same_listener_same_slot',
    'school_schedule_mismatch': 'school_schedule_mismatch',
    'insufficient_schedule_coverage': 'schedule_data_missing',
    'missing_required_prefix': 'feedback_required_prefix',
    'short_or_template_feedback': 'feedback_min_length',
}


def form_view(form):
    return {
        'id': form.ordinal + 1,
        'unique_id': form.ordinal + 10000,
        'listener_number': form.officer_id,
        'listener_college': form.department,
        'lecture_date': form.lecture_date,
        'teaching_week': form.teaching_week,
        'class_period': f'{form.start_period}-{form.end_period}',
        'lecture_location': form.lecture_location,
        'teacher_name': form.teacher_name,
        'teacher_college': form.teacher_college,
        'course_title': form.course_title,
        'course_feedback': form.course_feedback,
        'student_signature1': form.student_signature1,
        'contact_phone1': form.contact_phone1,
        'student_signature2': form.student_signature2,
        'contact_phone2': form.contact_phone2,
    }


def schedule_for(form):
    slots = []
    if 'personal_schedule_exact_conflict' in form.oracle_markers:
        slots.append(ScheduleSlot(
            course_title='acceptance-personal-course',
            weeks=frozenset({form.teaching_week}),
            weekday=form.weekday,
            start_period=form.start_period,
            end_period=form.end_period,
            evidence_strength=EvidenceStrength.EXACT,
        ))
    if 'class_schedule_approximate_conflict' in form.oracle_markers:
        slots.append(ScheduleSlot(
            course_title='acceptance-class-course',
            weeks=frozenset({form.teaching_week}),
            weekday=form.weekday,
            start_period=form.start_period,
            end_period=form.end_period,
            evidence_strength=EvidenceStrength.APPROXIMATE,
        ))
    coverage = {
        'complete': ScheduleCoverage.COMPLETE,
        'basic': ScheduleCoverage.BASIC,
        'missing': ScheduleCoverage.NONE,
    }[form.coverage]
    return SimpleNamespace(coverage=coverage, slots=tuple(slots), admin_classes=())


def text_findings(form, marker):
    revision = {
        'rule_key': MARKER_RULES[marker],
        'version': 1,
        'handler': 'required_prefix' if marker == 'missing_required_prefix' else 'minimum_length',
        'enabled': True,
        'severity': 'review',
        'parameters': (
            {'required_prefix': '该老师'}
            if marker == 'missing_required_prefix'
            else {'minimum_characters': 50}
        ),
    }
    return RuleEngine([revision]).evaluate(context=RuleContext(form=form_view(form)))


class BusinessAcceptanceOracleTest(unittest.TestCase):
    def test_every_marker_is_realized_and_normal_controls_have_no_active_anomaly(self):
        with TemporaryDirectory() as tmp:
            manifest = generate_manifest(
                AcceptanceConfig(runtime_root=Path(tmp)),
                ['该老师讲解具体，课堂组织清晰，能够结合课程内容引导同学思考。'],
            )

        forms_by_officer = {}
        for form in manifest.forms:
            forms_by_officer.setdefault(form.officer_id, []).append(form_view(form))
        self.assertTrue(all(
            not form.oracle_markers
            for form in manifest.forms
            if form.normal_control
        ))
        school_entries = build_acceptance_school_schedule(manifest)
        realized = {marker: 0 for marker in MARKER_RULES}

        for form in manifest.forms:
            current = form_view(form)
            history = tuple(forms_by_officer[form.officer_id])
            for marker, rule_key in MARKER_RULES.items():
                if marker not in form.oracle_markers:
                    continue
                if marker in {
                    'personal_schedule_exact_conflict',
                    'class_schedule_approximate_conflict',
                    'same_college_teacher',
                }:
                    findings = evaluate_schedule_rules(
                        current,
                        schedule_for(form),
                        semester_monday=date(2026, 3, 2),
                        only_rule=rule_key,
                    )
                elif marker in {
                    'repeated_witness_across_weeks',
                    'consecutive_teacher_weeks',
                    'same_time_conflict',
                }:
                    findings = evaluate_history_rules(
                        current,
                        history,
                        semester_monday=date(2026, 3, 2),
                        only_rule=rule_key,
                    )
                elif marker == 'school_schedule_mismatch':
                    entry = next(item for item in school_entries if (
                        item.course_title == form.course_title
                        and item.teacher_name == form.teacher_name
                        and item.location != form.lecture_location
                    ))
                    match = SimpleNamespace(
                        entry=SimpleNamespace(id=f'acceptance-{form.synthetic_key}'),
                        confidence=0.75,
                        matched_fields=('course_title', 'teacher_name'),
                        mismatched_fields=('location',),
                    )
                    findings = evaluate_schedule_rules(
                        current,
                        None,
                        school_matches=(match,),
                        only_rule=rule_key,
                    )
                elif marker in {'missing_required_prefix', 'short_or_template_feedback'}:
                    findings = text_findings(form, marker)
                else:
                    result = aggregate_classification((), coverage=schedule_for(form).coverage)
                    findings = tuple(
                        item for item in result.findings if item.rule_key == 'schedule_data_missing'
                    )
                self.assertTrue(
                    any(item.rule_key == rule_key for item in findings),
                    f'{marker} did not produce {rule_key} for {form.synthetic_key}',
                )
                realized[marker] += 1

        for marker, count in realized.items():
            self.assertGreater(count, 0, marker)

        for form in manifest.forms:
            if not form.normal_control:
                continue
            current = form_view(form)
            history = tuple(forms_by_officer[form.officer_id])
            self.assertFalse(evaluate_schedule_rules(
                current,
                schedule_for(form),
                semester_monday=date(2026, 3, 2),
            ))
            self.assertFalse(evaluate_history_rules(
                current,
                history,
                semester_monday=date(2026, 3, 2),
            ))
            self.assertFalse(text_findings(form, 'missing_required_prefix'))
            self.assertFalse(text_findings(form, 'short_or_template_feedback'))


if __name__ == '__main__':
    unittest.main()
