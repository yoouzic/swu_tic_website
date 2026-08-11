from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.business_acceptance.config import AcceptanceConfig
from tools.business_acceptance.generator import (
    ScheduleStructure,
    assert_real_identity_patterns_absent,
    build_acceptance_school_schedule,
    generate_manifest,
)


class BusinessAcceptanceGeneratorTest(unittest.TestCase):
    def test_generate_manifest_closes_counts_scopes_and_batches(self):
        with TemporaryDirectory() as tmp:
            config = AcceptanceConfig(runtime_root=Path(tmp))
            manifest = generate_manifest(
                config,
                corpus=['该老师讲解具体，课堂组织清晰。'],
            )

        self.assertEqual(manifest.officer_count, 1002)
        self.assertEqual(manifest.logical_form_count, 1500)
        self.assertEqual(manifest.department_counts, [251, 251, 250, 250])
        self.assertEqual(
            manifest.batch_counts,
            {'rules_only': 500, 'llm_only': 500, 'combined': 500},
        )
        self.assertEqual(manifest.llm_logical_count, 1000)
        self.assertEqual(manifest.normal_control_count, 225)
        self.assertEqual(manifest.violation_intended_count, 1275)
        self.assertEqual(len({form.synthetic_key for form in manifest.forms}), 1500)
        self.assertEqual(sum(user.form_count for user in manifest.officers), 1500)

        one_form = [user for user in manifest.officers if user.form_count == 1]
        two_forms = [user for user in manifest.officers if user.form_count == 2]
        self.assertEqual(len(one_form), 504)
        self.assertEqual(len(two_forms), 498)
        for user in two_forms:
            weeks = [form.teaching_week for form in manifest.forms if form.officer_id == user.officer_id]
            self.assertEqual(len(weeks), 2)
            self.assertNotEqual(weeks[0], weeks[1])

        by_mode = {
            mode: {form.synthetic_key for form in manifest.forms if form.review_mode == mode}
            for mode in ('rules_only', 'llm_only', 'combined')
        }
        self.assertEqual(set().union(*by_mode.values()), {form.synthetic_key for form in manifest.forms})
        self.assertEqual(sum(len(keys) for keys in by_mode.values()), 1500)
        self.assertFalse(by_mode['rules_only'] & by_mode['llm_only'])
        self.assertFalse(by_mode['rules_only'] & by_mode['combined'])
        self.assertFalse(by_mode['llm_only'] & by_mode['combined'])
        self.assertTrue(all(form.oracle_markers for form in manifest.forms if not form.normal_control))

    def test_manifest_is_reproducible_and_contains_no_real_identity_patterns(self):
        with TemporaryDirectory() as tmp:
            config = AcceptanceConfig(runtime_root=Path(tmp))
            corpus = ['该老师讲解具体，课堂组织清晰。']
            first = generate_manifest(config, corpus)
            second = generate_manifest(config, corpus)

        self.assertEqual(first.sha256, second.sha256)
        assert_real_identity_patterns_absent(first.to_json())

    def test_normal_controls_match_deidentified_acceptance_school_schedule(self):
        with TemporaryDirectory() as tmp:
            config = AcceptanceConfig(runtime_root=Path(tmp))
            structures = (
                ScheduleStructure(weeks=(1,), weekday=1, start_period=1, end_period=2, location='验收地点结构一'),
                ScheduleStructure(weeks=(5,), weekday=3, start_period=3, end_period=4, location='验收地点结构二'),
            )
            manifest = generate_manifest(
                config,
                corpus=['该老师讲解具体，课堂组织清晰。'],
                school_structure=structures,
                school_schedule_source_sha256='source-sha-only',
            )
            entries = build_acceptance_school_schedule(manifest)

        self.assertEqual(manifest.school_schedule_source_sha256, 'source-sha-only')
        self.assertTrue(entries)
        self.assertTrue(all(entry.teacher_name.startswith('验收教师') for entry in entries))
        self.assertTrue(all(entry.course_title.startswith('验收课程') for entry in entries))
        normal_forms = [form for form in manifest.forms if form.normal_control]
        self.assertEqual(len(normal_forms), 225)
        for form in normal_forms:
            with self.subTest(form=form.synthetic_key):
                matches = [entry for entry in entries if (
                    form.teaching_week in entry.weeks
                    and form.weekday == entry.weekday
                    and form.start_period == entry.start_period
                    and form.end_period == entry.end_period
                    and form.lecture_location == entry.location
                    and form.teacher_name == entry.teacher_name
                    and form.course_title == entry.course_title
                    and form.teacher_college == entry.teacher_college
                    and form.student_grade_class == entry.teaching_class
                )]
                self.assertTrue(matches)


if __name__ == '__main__':
    unittest.main()
