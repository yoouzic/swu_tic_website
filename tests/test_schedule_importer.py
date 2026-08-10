import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from app.app import app
from app.models import db
from app.review_automation.contracts import DatasetStatus, EvidenceStrength, ScheduleCoverage
from app.review_automation.models import (
    AutomationAuditLog,
    ListenerClassMapping,
    PersonalScheduleSlot,
    ScheduleDataset,
    ScheduleImportIssue,
    SchoolScheduleEntry,
)
from app.review_automation.schedules.aliases import PERSONAL_SCHEDULE_ALIASES
from app.review_automation.schedules.importer import preview_dataset
from app.review_automation.schedules.normalization import resolve_columns
from app.review_automation.schedules.repository import (
    activate_dataset,
    find_school_candidates,
    get_active_dataset,
    get_listener_schedule,
)
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'automation'
SEMESTER = '2026-synthetic-spring'


@contextmanager
def temporary_import_database():
    with tempfile.TemporaryDirectory(prefix='automation-import-') as temp_dir:
        old_upload_dir = app.config.get('AUTOMATION_UPLOAD_DIR')
        app.config['AUTOMATION_UPLOAD_DIR'] = str(Path(temp_dir) / 'uploads')
        try:
            with temporary_automation_database():
                yield Path(temp_dir)
        finally:
            app.config['AUTOMATION_UPLOAD_DIR'] = old_upload_dir


def fixture_stream(name):
    return io.BytesIO((FIXTURE_DIR / name).read_bytes())


class ScheduleImporterTest(unittest.TestCase):
    def test_school_preview_uses_aliases_and_is_staged_with_isolated_errors(self):
        with temporary_import_database() as temp_dir:
            dataset = preview_dataset(
                kind='school',
                semester=SEMESTER,
                stream=fixture_stream('school_schedule_historical.xlsx'),
                filename='../../synthetic-school.xlsx',
                actor_id=101,
            )

            summary = json.loads(dataset.summary_json)
            self.assertEqual(dataset.status, DatasetStatus.STAGED.value)
            self.assertEqual(dataset.original_filename, 'synthetic-school.xlsx')
            self.assertEqual(summary['sheet_name'], '合成历史课表')
            self.assertEqual(summary['header_row'], 2)
            self.assertEqual(dataset.row_count, 2)
            self.assertEqual(dataset.error_count, 1)
            self.assertEqual(SchoolScheduleEntry.query.filter_by(dataset_id=dataset.id).count(), 2)
            self.assertEqual(ScheduleImportIssue.query.filter_by(dataset_id=dataset.id).count(), 1)
            self.assertEqual(
                ScheduleImportIssue.query.filter_by(dataset_id=dataset.id).one().code,
                'invalid_weekday',
            )
            self.assertIsNone(get_active_dataset('school', SEMESTER))

            stored_name = summary['storage_filename']
            self.assertEqual(Path(stored_name).name, stored_name)
            self.assertNotIn('..', stored_name)
            self.assertTrue((temp_dir / 'uploads' / stored_name).is_file())

    def test_current_school_aliases_are_supported_without_fixed_column_count(self):
        with temporary_import_database():
            dataset = preview_dataset(
                kind='school',
                semester=SEMESTER,
                stream=fixture_stream('school_schedule_current.xlsx'),
                filename='current.xlsx',
                actor_id=101,
            )
            self.assertEqual(dataset.row_count, 1)
            self.assertEqual(dataset.error_count, 0)
            entry = SchoolScheduleEntry.query.filter_by(dataset_id=dataset.id).one()
            self.assertEqual(entry.course_title, '合成当前课程')
            self.assertEqual(entry.weekday, 2)
            self.assertEqual((entry.start_period, entry.end_period), (3, 4))

    def test_mapping_and_personal_rows_isolate_errors_and_collapse_duplicates(self):
        with temporary_import_database():
            mapping = preview_dataset(
                kind='class_mapping',
                semester=SEMESTER,
                stream=fixture_stream('listener_class_mapping.xlsx'),
                filename='mapping.xlsx',
                actor_id=101,
            )
            self.assertEqual(mapping.row_count, 2)
            self.assertEqual(mapping.error_count, 1)
            self.assertEqual(ListenerClassMapping.query.filter_by(dataset_id=mapping.id).count(), 2)
            self.assertEqual(
                ScheduleImportIssue.query.filter_by(dataset_id=mapping.id).one().code,
                'missing_identity',
            )

            personal = preview_dataset(
                kind='personal',
                semester=SEMESTER,
                stream=fixture_stream('personal_schedule.xlsx'),
                filename='personal.xlsx',
                actor_id=101,
            )
            self.assertEqual(personal.row_count, 2)
            self.assertEqual(personal.error_count, 1)
            self.assertEqual(PersonalScheduleSlot.query.filter_by(dataset_id=personal.id).count(), 2)

    def test_personal_schedule_aliases_require_academic_semester_column(self):
        resolved = resolve_columns(
            ['信息员编号', '学号', '课程名称', '学年学期', '教学周', '星期几', '第几节'],
            PERSONAL_SCHEDULE_ALIASES,
            {'listener_number', 'course_title', 'semester', 'weeks', 'weekday', 'periods'},
        )
        self.assertEqual(resolved['semester'], '学年学期')

    def test_personal_schedule_rejects_row_from_other_semester(self):
        with temporary_import_database():
            personal = preview_dataset(
                kind='personal',
                semester=SEMESTER,
                stream=fixture_stream('personal_schedule.xlsx'),
                filename='personal.xlsx',
                actor_id=101,
            )
            issue = ScheduleImportIssue.query.filter_by(dataset_id=personal.id).one()
            self.assertEqual(issue.code, 'semester_mismatch')
            self.assertEqual(issue.row_number, 6)
            self.assertEqual(personal.row_count, 2)
            self.assertEqual(personal.error_count, 1)

    def test_activation_retires_previous_active_dataset_atomically_and_audits(self):
        with temporary_import_database():
            first = preview_dataset(
                'school', SEMESTER, fixture_stream('school_schedule_historical.xlsx'),
                'first.xlsx', 101,
            )
            second = preview_dataset(
                'school', SEMESTER, fixture_stream('school_schedule_current.xlsx'),
                'second.xlsx', 101,
            )

            activate_dataset(first.id, actor_id=101)
            self.assertEqual(get_active_dataset('school', SEMESTER).id, first.id)
            activated = activate_dataset(second.id, actor_id=101)
            self.assertEqual(activated.id, second.id)

            db.session.expire_all()
            statuses = {
                dataset.id: dataset.status
                for dataset in ScheduleDataset.query.filter_by(kind='school', semester=SEMESTER).all()
            }
            self.assertEqual(statuses[first.id], DatasetStatus.RETIRED.value)
            self.assertEqual(statuses[second.id], DatasetStatus.ACTIVE.value)
            self.assertEqual(
                ScheduleDataset.query.filter_by(
                    kind='school', semester=SEMESTER, status=DatasetStatus.ACTIVE.value,
                ).count(),
                1,
            )
            audit = AutomationAuditLog.query.filter_by(action='activate_dataset').all()
            self.assertEqual(len(audit), 2)
            self.assertEqual(audit[-1].target_id, second.id)

    def test_listener_schedule_unions_class_and_personal_slots_with_complete_exact_coverage(self):
        with temporary_import_database():
            school = preview_dataset(
                'school', SEMESTER, fixture_stream('school_schedule_historical.xlsx'),
                'school.xlsx', 101,
            )
            mapping = preview_dataset(
                'class_mapping', SEMESTER, fixture_stream('listener_class_mapping.xlsx'),
                'mapping.xlsx', 101,
            )
            personal = preview_dataset(
                'personal', SEMESTER, fixture_stream('personal_schedule.xlsx'),
                'personal.xlsx', 101,
            )
            activate_dataset(school.id, actor_id=101)
            activate_dataset(mapping.id, actor_id=101)
            activate_dataset(personal.id, actor_id=101)

            schedule = get_listener_schedule('SYN-L-001', None, SEMESTER)
            self.assertEqual(schedule.coverage, ScheduleCoverage.COMPLETE)
            self.assertEqual(len(schedule.slots), 1)
            self.assertEqual(schedule.slots[0].course_title, '合成课程甲')
            self.assertEqual(schedule.slots[0].evidence_strength, EvidenceStrength.EXACT)

    def test_listener_schedule_without_active_school_is_not_basic(self):
        with temporary_import_database():
            mapping = preview_dataset(
                'class_mapping', SEMESTER, fixture_stream('listener_class_mapping.xlsx'),
                'mapping.xlsx', 101,
            )
            activate_dataset(mapping.id, actor_id=101)

            schedule = get_listener_schedule('SYN-L-001', None, SEMESTER)
            self.assertEqual(schedule.coverage, ScheduleCoverage.NONE)
            self.assertEqual(schedule.slots, ())

    def test_active_school_and_mapping_can_be_basic_with_zero_class_slots(self):
        with temporary_import_database():
            school = preview_dataset(
                'school', SEMESTER, fixture_stream('school_schedule_current.xlsx'),
                'school.xlsx', 101,
            )
            mapping = preview_dataset(
                'class_mapping', SEMESTER, fixture_stream('listener_class_mapping.xlsx'),
                'mapping.xlsx', 101,
            )
            activate_dataset(school.id, actor_id=101)
            activate_dataset(mapping.id, actor_id=101)

            schedule = get_listener_schedule(None, 'SYN-STU-0002', SEMESTER)
            self.assertEqual(schedule.coverage, ScheduleCoverage.BASIC)
            self.assertEqual(schedule.slots, ())

    def test_school_candidates_match_synthetic_form_fields(self):
        with temporary_import_database():
            school = preview_dataset(
                'school', SEMESTER, fixture_stream('school_schedule_historical.xlsx'),
                'school.xlsx', 101,
            )
            activate_dataset(school.id, actor_id=101)
            form = make_synthetic_form(
                course_title='合成课程甲',
                teacher_name='合成教师甲',
                teacher_college='合成信息学院',
                class_period='1-2',
                lecture_location='合成教室A',
            )

            matches = find_school_candidates(form, SEMESTER)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].entry.course_title, '合成课程甲')
            self.assertGreater(matches[0].confidence, 0)

    def test_school_candidates_use_course_anchor_and_report_teacher_mismatch(self):
        with temporary_import_database():
            school = preview_dataset(
                'school', SEMESTER, fixture_stream('school_schedule_historical.xlsx'),
                'school.xlsx', 101,
            )
            activate_dataset(school.id, actor_id=101)
            form = make_synthetic_form(
                course_title='合成课程甲',
                teacher_name='合成错误教师',
                teacher_college='合成信息学院',
                class_period='1-2',
                lecture_location='合成教室A',
            )

            matches = find_school_candidates(form, SEMESTER)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].entry.course_title, '合成课程甲')
            self.assertIn('teacher_name', matches[0].mismatched_fields)
            self.assertNotIn('course_title', matches[0].mismatched_fields)

    def test_school_candidates_use_teacher_anchor_and_report_course_mismatch(self):
        with temporary_import_database():
            school = preview_dataset(
                'school', SEMESTER, fixture_stream('school_schedule_historical.xlsx'),
                'school.xlsx', 101,
            )
            activate_dataset(school.id, actor_id=101)
            form = make_synthetic_form(
                course_title='合成错误课程',
                teacher_name='合成教师甲',
                teacher_college='合成信息学院',
                class_period='1-2',
                lecture_location='合成教室A',
            )

            matches = find_school_candidates(form, SEMESTER)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].entry.teacher_name, '合成教师甲')
            self.assertIn('course_title', matches[0].mismatched_fields)
            self.assertNotIn('teacher_name', matches[0].mismatched_fields)

    def test_school_candidates_return_no_noise_when_both_anchors_mismatch(self):
        with temporary_import_database():
            school = preview_dataset(
                'school', SEMESTER, fixture_stream('school_schedule_historical.xlsx'),
                'school.xlsx', 101,
            )
            activate_dataset(school.id, actor_id=101)
            form = make_synthetic_form(
                course_title='合成错误课程',
                teacher_name='合成错误教师',
                teacher_college='合成信息学院',
                class_period='1-2',
                lecture_location='合成教室A',
            )

            self.assertEqual(find_school_candidates(form, SEMESTER), [])


if __name__ == '__main__':
    unittest.main()
