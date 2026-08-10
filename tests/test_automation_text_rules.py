import unittest

from app.review_automation.contracts import (
    EvidenceStrength,
    FindingSeverity,
    FindingSource,
)
from app.review_automation.rules.registry import (
    RuleEngine,
    RuleValidationError,
    validate_rule_revision,
)


def revision(
    rule_key,
    version,
    handler,
    *,
    enabled=True,
    severity='review',
    parameters=None,
):
    return {
        'rule_key': rule_key,
        'version': version,
        'handler': handler,
        'enabled': enabled,
        'severity': severity,
        'parameters': {} if parameters is None else parameters,
    }


class TextRuleTest(unittest.TestCase):
    def test_newest_revision_wins_and_disabled_latest_revision_does_not_execute(self):
        form = {'course_feedback': '听课教师讲解清楚，课堂安排合理。'}
        engine = RuleEngine([
            revision(
                'prefix', 1, 'required_prefix',
                parameters={'required_prefix': '该老师'},
            ),
            revision(
                'prefix', 2, 'required_prefix',
                enabled=False,
                parameters={'required_prefix': '听课教师'},
            ),
        ])

        findings = engine.evaluate(form)

        self.assertEqual(findings, ())

    def test_rule_parameters_are_validated_per_fixed_handler(self):
        with self.assertRaises(RuleValidationError):
            validate_rule_revision(
                revision(
                    'length', 1, 'minimum_length',
                    parameters={'minimum_characters': 'not-an-integer'},
                )
            )

    def test_unknown_handler_fails_closed_with_system_finding(self):
        findings = RuleEngine([
            revision('future', 1, 'uploaded_python', parameters={}),
        ]).evaluate({'course_feedback': '合成反馈'})

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].source, FindingSource.SYSTEM)
        self.assertEqual(findings[0].rule_key, 'future')
        self.assertEqual(findings[0].severity, FindingSeverity.UNKNOWN)

    def test_system_finding_uses_evidence_strength_enum_contract(self):
        finding = RuleEngine([
            revision('future', 1, 'uploaded_python', parameters={}),
        ]).evaluate({'course_feedback': '合成反馈'})[0]

        self.assertIs(finding.evidence_strength, EvidenceStrength.WEAK)
        self.assertEqual(finding.evidence_strength.value, 'weak')

    def test_required_prefix_is_checked_at_position_zero_and_repetition_is_not_fraud(self):
        engine = RuleEngine([
            revision(
                'prefix', 1, 'required_prefix',
                parameters={'required_prefix': '该老师'},
            ),
        ])

        self.assertEqual(
            engine.evaluate({'course_feedback': '该老师该老师讲解清楚'}),
            (),
        )
        finding = engine.evaluate({'course_feedback': '本次听课讲解清楚'})[0]
        self.assertEqual(finding.rule_key, 'prefix')
        self.assertEqual(finding.severity, FindingSeverity.REVIEW)

    def test_normalized_feedback_under_fifty_chinese_characters_is_reviewable(self):
        engine = RuleEngine([
            revision(
                'short', 1, 'minimum_length',
                parameters={'minimum_characters': 50},
            ),
        ])

        finding = engine.evaluate({'course_feedback': '合成文字' * 12})[0]

        self.assertEqual(finding.rule_key, 'short')
        self.assertEqual(finding.severity, FindingSeverity.REVIEW)
        self.assertEqual(finding.evidence['character_count'], 48)

    def test_confusion_patterns_report_exact_span_and_never_rewrite_source(self):
        source = '该老师讲解地很清楚，详细得讲解了合成案例。'
        engine = RuleEngine([
            revision(
                'wording', 1, 'confusion_patterns',
                parameters={
                    'patterns': [
                        {
                            'regex': r'讲解地很清楚',
                            'message': '建议人工复核“地”的用法',
                        },
                        {
                            'regex': r'详细得讲解',
                            'message': '建议人工复核“得”的用法',
                        },
                    ],
                },
            ),
        ])

        findings = engine.evaluate({'course_feedback': source})

        self.assertEqual([item.evidence['matched_text'] for item in findings], [
            '讲解地很清楚',
            '详细得讲解',
        ])
        self.assertEqual(findings[0].evidence['span'], {
            'start': source.index('讲解地很清楚'),
            'end': source.index('讲解地很清楚') + len('讲解地很清楚'),
        })
        self.assertEqual(findings[1].evidence['suggestion'], '建议人工复核“得”的用法')
        self.assertEqual(findings[0].evidence_strength, EvidenceStrength.APPROXIMATE)
        self.assertEqual(source, '该老师讲解地很清楚，详细得讲解了合成案例。')

    def test_safe_regex_or_phrase_revision_uses_same_engine_path(self):
        engine = RuleEngine([
            revision(
                'phrase', 1, 'safe_regex',
                parameters={
                    'phrase': '高度模板化',
                    'message': '建议人工复核固定化表达',
                },
            ),
        ])

        findings = engine.evaluate({'course_feedback': '该老师的评价高度模板化'})

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence['matched_text'], '高度模板化')

    def test_invalid_or_dangerous_regex_is_rejected_before_execution(self):
        invalid_patterns = [
            '[',
            'a' * 201,
            '(a+)+$',
        ]
        for pattern in invalid_patterns:
            with self.subTest(pattern=pattern):
                with self.assertRaises(RuleValidationError):
                    validate_rule_revision(
                        revision(
                            'unsafe', 1, 'safe_regex',
                            parameters={'regex': pattern, 'message': '合成建议'},
                        )
                    )

    def test_schedule_history_parameters_are_rejected_before_rule_execution(self):
        invalid_revisions = [
            ('witness', 'witness_reused_across_weeks', {'minimum_distinct_weeks': 'bad'}),
            ('witness-small', 'witness_reused_across_weeks', {'minimum_distinct_weeks': 1}),
            ('witness-order', 'witness_reused_across_weeks', {
                'minimum_distinct_weeks': 3,
                'high_risk_candidate_weeks': 2,
            }),
            ('witness-identity', 'witness_reused_across_weeks', {
                'minimum_distinct_weeks': 2,
                'high_risk_candidate_weeks': 3,
                'identity': 'name_primary',
            }),
            ('consecutive', 'consecutive_teacher_weeks', {'maximum_week_gap': 2}),
            ('similarity-type', 'feedback_similarity', {'similarity_threshold': '0.9'}),
            ('similarity-range', 'feedback_similarity', {'similarity_threshold': 1.1}),
        ]
        for rule_key, handler, parameters in invalid_revisions:
            with self.subTest(rule_key=rule_key):
                with self.assertRaises(RuleValidationError):
                    RuleEngine([revision(rule_key, 1, handler, parameters=parameters)])

    def test_schedule_history_handlers_reject_non_objects_and_unknown_parameters_consistently(self):
        handlers = (
            'personal_schedule_conflict',
            'class_schedule_conflict',
            'same_college_teacher',
            'school_schedule_mismatch',
            'witness_phone_name_conflict',
            'same_listener_same_slot',
        )
        for handler in handlers:
            with self.subTest(handler=handler, case='non_object'):
                with self.assertRaises(RuleValidationError):
                    RuleEngine([revision(handler, 1, handler, parameters=[])])
            with self.subTest(handler=handler, case='unknown_key'):
                with self.assertRaises(RuleValidationError):
                    RuleEngine([revision(handler, 1, handler, parameters={'unexpected': True})])


if __name__ == '__main__':
    unittest.main()
