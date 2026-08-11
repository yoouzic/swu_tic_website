from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import os
import unittest

from sqlalchemy import text

from app.app import app
from app.models import LectureForm, Permission, User, db
from app.review_automation.models import ScheduleDataset, ScheduleImportIssue
from app.review_automation.schedules.importer import CLASS_MAPPING_KIND, PERSONAL_KIND, SCHOOL_KIND
from app.utils.review_permissions import get_reviewable_users, get_user_review_permission
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

from tools.business_acceptance.config import AcceptanceConfig
from tools.business_acceptance.generator import (
    AcceptanceManifest,
    ScheduleStructure,
    assert_real_identity_patterns_absent,
    build_acceptance_school_schedule,
    generate_manifest,
    write_acceptance_school_schedule,
    write_acceptance_workbooks,
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
            user_forms = [form for form in manifest.forms if form.officer_id == user.officer_id]
            has_same_time_oracle = any(
                'same_time_conflict' in form.oracle_markers
                for form in user_forms
            )
            if has_same_time_oracle:
                self.assertEqual(weeks[0], weeks[1])
            else:
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
        self.assertTrue(all(not form.oracle_markers for form in manifest.forms if form.normal_control))

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

class BusinessAcceptanceSeedTest(unittest.TestCase):
    def test_seed_isolated_database_closes_counts_permissions_and_schedule_datasets(self):
        from tools.business_acceptance.seed import seed_acceptance_database

        instance_root = Path('data/instance/acceptance-2026-08-11').resolve()
        storage_root = Path('data/storage/acceptance-2026-08-11').resolve()
        instance_root.mkdir(parents=True, exist_ok=True)
        storage_root.mkdir(parents=True, exist_ok=True)
        old_acceptance_run = os.environ.get('ACCEPTANCE_RUN')
        old_upload_dir = app.config.get('AUTOMATION_UPLOAD_DIR')
        with TemporaryDirectory(dir=instance_root) as instance_dir, TemporaryDirectory(dir=storage_root) as runtime_dir:
            runtime = Path(runtime_dir)
            config = AcceptanceConfig(runtime_root=runtime)
            manifest = generate_manifest(config, ['该老师讲解具体，课堂组织清晰。'])
            write_acceptance_workbooks(manifest, runtime / 'generated')
            write_acceptance_school_schedule(manifest, runtime / 'generated' / 'school-schedule-acceptance.xlsx')
            db_path = Path(instance_dir) / 'acceptance.sqlite'
            try:
                os.environ['ACCEPTANCE_RUN'] = '1'
                configure_sqlite_database(app, db, db_path)
                app.config['AUTOMATION_UPLOAD_DIR'] = str(runtime / 'uploads')
                with app.app_context():
                    db.drop_all()
                    db.create_all()
                    result = seed_acceptance_database(app, manifest, password='test-only-password')

                    self.assertEqual(result.information_officers, 1002)
                    self.assertEqual(result.administrators, 10)
                    self.assertEqual(result.departments, 4)
                    self.assertEqual(result.groups, 8)
                    self.assertEqual(result.logical_forms, 1500)
                    self.assertEqual(result.physical_form_rows, 1500)
                    self.assertEqual(result.active_datasets, 3)
                    self.assertEqual(
                        result.coverage_counts,
                        {'complete': 334, 'basic': 334, 'missing': 334},
                    )
                    active_datasets = ScheduleDataset.query.filter_by(
                        semester=manifest.config.semester,
                        status='active',
                    ).all()
                    self.assertEqual(
                        {dataset.kind for dataset in active_datasets},
                        {SCHOOL_KIND, CLASS_MAPPING_KIND, PERSONAL_KIND},
                    )
                    for kind, filename in (
                        (SCHOOL_KIND, 'school-schedule-acceptance.xlsx'),
                        (CLASS_MAPPING_KIND, 'class-mapping.xlsx'),
                        (PERSONAL_KIND, 'personal-schedule.xlsx'),
                    ):
                        dataset = next(item for item in active_datasets if item.kind == kind)
                        data = (runtime / 'generated' / filename).read_bytes()
                        self.assertEqual(dataset.sha256, hashlib.sha256(data).hexdigest())
                        self.assertEqual(dataset.error_count, 0)
                        self.assertEqual(
                            ScheduleImportIssue.query.filter_by(dataset_id=dataset.id).count(),
                            0,
                        )
                    self.assertEqual(LectureForm.query.filter_by(status='待审核').count(), 1500)
                    permissions = {permission.name for permission in Permission.query.all()}
                    self.assertTrue({'审表_小组', '审表_部门', '审表_中心'} <= permissions)
                    for admin_id, expected_permission in result.permission_by_admin.items():
                        self.assertEqual(get_user_review_permission(admin_id), expected_permission)

                    officers = {user.number: user.id for user in User.query.filter_by(role='信息员').all()}
                    for admin_id in result.admin_user_ids['group']:
                        admin = db.session.get(User, admin_id)
                        expected = {
                            officer.id for officer in User.query.filter_by(
                                role='信息员', department=admin.department, group_id=admin.group_id,
                            ).all()
                        }
                        actual = {
                            user_id for user_id in get_reviewable_users(admin_id)
                            if db.session.get(User, user_id).role == '信息员'
                        }
                        self.assertEqual(actual, expected)
                    for admin_id in result.admin_user_ids['department']:
                        admin = db.session.get(User, admin_id)
                        expected = {
                            officer.id for officer in User.query.filter_by(
                                role='信息员', department=admin.department,
                            ).all()
                        }
                        actual = {
                            user_id for user_id in get_reviewable_users(admin_id)
                            if db.session.get(User, user_id).role == '信息员'
                        }
                        self.assertEqual(actual, expected)
                    all_officer_ids = set(officers.values())
                    for key in ('center', 'super'):
                        for admin_id in result.admin_user_ids[key]:
                            actual = {
                                user_id for user_id in get_reviewable_users(admin_id)
                                if db.session.get(User, user_id).role == '信息员'
                            }
                            self.assertEqual(actual, all_officer_ids)

                    self.assertEqual(db.session.execute(text('PRAGMA journal_mode')).scalar(), 'wal')
                    self.assertEqual(db.session.execute(text('PRAGMA busy_timeout')).scalar(), 30000)

                    second = seed_acceptance_database(app, manifest, password='test-only-password')
                    self.assertEqual(second.logical_forms, result.logical_forms)
                    self.assertEqual(second.dataset_ids, result.dataset_ids)
                    self.assertEqual(
                        LectureForm.query.filter_by(status='\u5f85\u5ba1\u6838').count(),
                        1500,
                    )
            finally:
                with app.app_context():
                    cleanup_sqlite_database(db, drop_all=False)
                if old_acceptance_run is None:
                    os.environ.pop('ACCEPTANCE_RUN', None)
                else:
                    os.environ['ACCEPTANCE_RUN'] = old_acceptance_run
                app.config['AUTOMATION_UPLOAD_DIR'] = old_upload_dir

    def test_seed_rejects_database_outside_acceptance_instance_root(self):
        repo = Path(__file__).resolve().parents[1]
        outside_root = repo / 'data' / 'instance'
        storage_root = repo / 'data' / 'storage' / 'acceptance-2026-08-11'
        outside_root.mkdir(parents=True, exist_ok=True)
        storage_root.mkdir(parents=True, exist_ok=True)
        old_acceptance_run = os.environ.get('ACCEPTANCE_RUN')
        old_upload_dir = app.config.get('AUTOMATION_UPLOAD_DIR')
        with TemporaryDirectory(dir=outside_root) as instance_dir, TemporaryDirectory(dir=storage_root) as runtime_dir:
            manifest = AcceptanceManifest(
                config=AcceptanceConfig(runtime_root=Path(runtime_dir)),
                officers=(),
                forms=(),
                corpus_sha256='',
                sha256='',
            )
            try:
                os.environ['ACCEPTANCE_RUN'] = '1'
                configure_sqlite_database(app, db, Path(instance_dir) / 'outside.sqlite')
                with app.app_context():
                    with self.assertRaisesRegex(RuntimeError, 'outside the isolated acceptance root'):
                        from tools.business_acceptance.seed import seed_acceptance_database
                        seed_acceptance_database(app, manifest, password='x')
            finally:
                with app.app_context():
                    cleanup_sqlite_database(db, drop_all=False)
                if old_acceptance_run is None:
                    os.environ.pop('ACCEPTANCE_RUN', None)
                else:
                    os.environ['ACCEPTANCE_RUN'] = old_acceptance_run
                app.config['AUTOMATION_UPLOAD_DIR'] = old_upload_dir


if __name__ == '__main__':
    unittest.main()
