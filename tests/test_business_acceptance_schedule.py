from contextlib import contextmanager
from datetime import date
import io
from pathlib import Path
import tempfile
import unittest

from app.app import app
from app.review_automation.classification import aggregate_classification
from app.models import db
from app.review_automation.contracts import DatasetStatus, EvidenceStrength, ScheduleCoverage
from app.review_automation.rules.schedule import evaluate_schedule_rules
from app.review_automation.schedules.importer import preview_dataset
from app.review_automation.schedules.repository import (
    activate_dataset,
    find_school_candidates,
    get_listener_schedule,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tools.business_acceptance.config import AcceptanceConfig
from tools.business_acceptance.generator import (
    generate_manifest,
    write_acceptance_school_schedule,
    write_acceptance_workbooks,
)


@contextmanager
def isolated_acceptance_schedule_database():
    instance_root = Path('data/instance/acceptance-2026-08-11').resolve()
    storage_root = Path('data/storage/acceptance-2026-08-11').resolve()
    instance_root.mkdir(parents=True, exist_ok=True)
    storage_root.mkdir(parents=True, exist_ok=True)
    old_upload_dir = app.config.get('AUTOMATION_UPLOAD_DIR')
    with tempfile.TemporaryDirectory(dir=instance_root) as instance_dir, tempfile.TemporaryDirectory(dir=storage_root) as runtime_dir:
        runtime = Path(runtime_dir)
        configure_sqlite_database(app, db, Path(instance_dir) / 'schedule.sqlite')
        app.config['AUTOMATION_UPLOAD_DIR'] = str(runtime / 'uploads')
        context = app.app_context()
        context.push()
        try:
            db.drop_all()
            db.create_all()
            yield runtime
        finally:
            cleanup_sqlite_database(db, drop_all=False)
            context.pop()
            app.config['AUTOMATION_UPLOAD_DIR'] = old_upload_dir


def import_and_activate(path, kind, semester):
    dataset = preview_dataset(
        kind=kind,
        semester=semester,
        stream=io.BytesIO(path.read_bytes()),
        filename=path.name,
        actor_id=None,
    )
    if dataset.status != DatasetStatus.STAGED.value:
        raise AssertionError(f'dataset import failed for {kind}')
    return activate_dataset(dataset.id, actor_id=None)


def form_view(form):
    return {
        'id': form.synthetic_key,
        'listener_number': form.officer_id,
        'lecture_date': form.lecture_date,
        'teaching_week': form.teaching_week,
        'class_period': f'{form.start_period}-{form.end_period}',
        'lecture_location': form.lecture_location,
        'teacher_name': form.teacher_name,
        'teacher_college': form.teacher_college,
        'course_title': form.course_title,
        'course_feedback': form.course_feedback,
    }


class BusinessAcceptanceScheduleTest(unittest.TestCase):
    def test_real_importer_coverage_and_personal_overlap_oracle(self):
        with isolated_acceptance_schedule_database() as runtime:
            config = AcceptanceConfig(runtime_root=runtime)
            manifest = generate_manifest(config, ['该老师讲解具体，课堂组织清晰。'])
            write_acceptance_workbooks(manifest, runtime / 'generated')
            write_acceptance_school_schedule(manifest, runtime / 'generated' / 'school-schedule-acceptance.xlsx')
            import_and_activate(
                runtime / 'generated' / 'school-schedule-acceptance.xlsx',
                'school',
                config.semester,
            )
            import_and_activate(runtime / 'generated' / 'class-mapping.xlsx', 'class_mapping', config.semester)
            import_and_activate(runtime / 'generated' / 'personal-schedule.xlsx', 'personal', config.semester)

            officers_by_id = {officer.officer_id: officer for officer in manifest.officers}
            forms_by_officer = {}
            for form in manifest.forms:
                forms_by_officer.setdefault(form.officer_id, []).append(form)

            complete = next(officer for officer in manifest.officers if officer.coverage == 'complete')
            basic = next(officer for officer in manifest.officers if officer.coverage == 'basic')
            missing = next(officer for officer in manifest.officers if officer.coverage == 'missing')
            complete_schedule = get_listener_schedule(complete.officer_id, complete.student_id, config.semester)
            basic_schedule = get_listener_schedule(basic.officer_id, basic.student_id, config.semester)
            missing_schedule = get_listener_schedule(missing.officer_id, missing.student_id, config.semester)
            self.assertEqual(complete_schedule.coverage, ScheduleCoverage.COMPLETE)
            self.assertEqual(basic_schedule.coverage, ScheduleCoverage.BASIC)
            self.assertEqual(missing_schedule.coverage, ScheduleCoverage.NONE)
            self.assertTrue(complete_schedule.slots)
            self.assertTrue(basic_schedule.slots)
            self.assertFalse(missing_schedule.slots)
            self.assertTrue(all(slot.evidence_strength == EvidenceStrength.EXACT for slot in complete_schedule.slots))
            self.assertTrue(all(slot.evidence_strength == EvidenceStrength.APPROXIMATE for slot in basic_schedule.slots))

            checked_basic_forms = 0
            for officer in manifest.officers:
                if officer.coverage != 'basic':
                    continue
                schedule = get_listener_schedule(officer.officer_id, officer.student_id, config.semester)
                for form in forms_by_officer[officer.officer_id]:
                    findings = evaluate_schedule_rules(
                        form_view(form),
                        schedule,
                        semester_monday=date.fromisoformat(config.semester_monday),
                        only_rule='class_schedule_conflict',
                    )
                    has_approximate_overlap = any(
                        item.rule_key == 'class_schedule_conflict' for item in findings
                    )
                    intended_overlap = 'class_schedule_approximate_conflict' in form.oracle_markers
                    self.assertEqual(has_approximate_overlap, intended_overlap, form.synthetic_key)
                    checked_basic_forms += 1
            self.assertEqual(checked_basic_forms, 498)

            checked_complete_forms = 0
            for officer in manifest.officers:
                if officer.coverage != 'complete':
                    continue
                schedule = get_listener_schedule(officer.officer_id, officer.student_id, config.semester)
                for form in forms_by_officer[officer.officer_id]:
                    findings = evaluate_schedule_rules(
                        form_view(form),
                        schedule,
                        semester_monday=date.fromisoformat(config.semester_monday),
                        only_rule='personal_schedule_conflict',
                    )
                    has_exact_overlap = any(item.rule_key == 'personal_schedule_conflict' for item in findings)
                    intended_overlap = 'personal_schedule_exact_conflict' in form.oracle_markers
                    self.assertEqual(has_exact_overlap, intended_overlap, form.synthetic_key)
                    checked_complete_forms += 1
            self.assertEqual(checked_complete_forms, 334)

            for form in manifest.forms:
                if not form.normal_control:
                    continue
                officer = officers_by_id[form.officer_id]
                schedule = get_listener_schedule(
                    officer.officer_id,
                    officer.student_id,
                    config.semester,
                )
                classification = aggregate_classification((), coverage=schedule.coverage)
                if schedule.coverage == ScheduleCoverage.COMPLETE:
                    self.assertNotIn('schedule_data_missing', classification.rationale_keys)
                else:
                    self.assertIn('schedule_data_missing', classification.rationale_keys)

            for form in manifest.forms:
                if not form.normal_control and 'school_schedule_mismatch' not in form.oracle_markers:
                    continue
                candidates = find_school_candidates(form_view(form), config.semester)
                findings = evaluate_schedule_rules(
                    form_view(form),
                    None,
                    school_matches=candidates,
                    only_rule='school_schedule_mismatch',
                )
                has_mismatch = any(item.rule_key == 'school_schedule_mismatch' for item in findings)
                intended_mismatch = 'school_schedule_mismatch' in form.oracle_markers
                self.assertEqual(has_mismatch, intended_mismatch, form.synthetic_key)


if __name__ == '__main__':
    unittest.main()
