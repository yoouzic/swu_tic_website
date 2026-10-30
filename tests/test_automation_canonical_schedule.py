"""Canonical admin imports are immutable school evidence, not personal coverage."""

import json
import unittest

from app.models import (
    ListeningAssistantScheduleEntry, ScheduleImportBatch, ScheduleImportRow,
    ScheduleImportRowScalarMeta, ScheduleSemesterSelection, db,
)
from app.review_automation.contracts import EvidenceStrength, ScheduleCoverage
from app.review_automation.models import (
    ListenerClassMapping, PersonalScheduleSlot, ScheduleDataset, SchoolScheduleEntry,
)
from app.review_automation.schedules import repository
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


SEMESTER = 'SYN-2026-FALL'


class CanonicalScheduleAutomationTest(unittest.TestCase):
    def setUp(self):
        self.database = temporary_automation_database()
        self.database.__enter__()

    def tearDown(self):
        self.database.__exit__(None, None, None)

    def canonical(self, *, semester=SEMESTER, **overrides):
        batch = ScheduleImportBatch(
            semester=semester, source_filename='synthetic.xlsx',
            source_sha256='a' * 64, row_count=1, status='active',
        )
        db.session.add(batch)
        db.session.flush()
        values = dict(
            batch_id=batch.id, source_row=2, teacher_name='合成教师',
            teacher_college='合成学院', course_name='合成课程', weekday_raw='1',
            class_period_raw='第1-2节', location_raw='28-0203',
            class_composition_raw='合成行政班', start_week_raw='1-16周',
        )
        values.update(overrides)
        row = ScheduleImportRow(**values)
        db.session.add(row)
        selection = db.session.get(ScheduleSemesterSelection, semester)
        if selection is None:
            db.session.add(ScheduleSemesterSelection(semester=semester, active_batch_id=batch.id))
        else:
            selection.active_batch_id = batch.id
        db.session.commit()
        return batch, row

    def ref(self, batch):
        return {
            'source': 'canonical_snapshot', 'id': str(batch.id),
            'sha256': batch.source_sha256, 'version': 'canonical-school-v1',
        }

    def dataset(self, kind):
        value = ScheduleDataset(
            kind=kind, semester=SEMESTER, sha256=(kind[0] * 64),
            original_filename='synthetic.xlsx', status='active',
        )
        db.session.add(value)
        db.session.flush()
        return value

    def test_canonical_school_and_class_mapping_provide_basic_approximate_slots(self):
        batch, _ = self.canonical()
        mapping = self.dataset('class_mapping')
        db.session.add(ListenerClassMapping(
            dataset_id=mapping.id, listener_number='SYN-001', admin_class='合成行政班',
        ))
        db.session.commit()

        schedule = repository.get_listener_schedule_for_datasets(
            'SYN-001', None, SEMESTER,
            {'school': self.ref(batch), 'class_mapping': {'id': mapping.id}},
        )

        self.assertEqual(schedule.coverage, ScheduleCoverage.BASIC)
        self.assertEqual(len(schedule.slots), 1)
        self.assertEqual(schedule.slots[0].evidence_strength, EvidenceStrength.APPROXIMATE)
        self.assertEqual(ScheduleDataset.query.filter_by(kind='school').count(), 0)

    def test_freeze_reads_retired_original_after_current_switch(self):
        from app.review_automation.schedules.canonical import snapshot_canonical_school

        original, _ = self.canonical()
        frozen = snapshot_canonical_school(SEMESTER)
        replacement, _ = self.canonical(teacher_name='替换教师')
        original.status = 'retired'
        db.session.commit()

        self.assertEqual(frozen['id'], str(original.id))
        self.assertNotEqual(frozen['id'], str(replacement.id))
        entries = repository.get_school_entries_for_ref(frozen, SEMESTER)
        self.assertEqual([entry.teacher_name for entry in entries], ['合成教师'])
        self.assertNotEqual(snapshot_canonical_school(SEMESTER)['id'], frozen['id'])

    def test_wrong_semester_bad_hash_unknown_source_and_missing_refs_fail_closed(self):
        batch, _ = self.canonical()
        ref = self.ref(batch)
        self.assertEqual(repository.get_school_entries_for_ref(ref, 'OTHER'), [])
        self.assertEqual(repository.get_school_entries_for_ref({**ref, 'sha256': 'b' * 64}, SEMESTER), [])
        self.assertEqual(repository.get_school_entries_for_ref({**ref, 'source': 'unknown'}, SEMESTER), [])
        self.assertEqual(repository.get_school_entries_for_ref({**ref, 'id': True}, SEMESTER), [])
        self.assertEqual(repository.get_school_entries_for_ref(None, SEMESTER), [])
        self.assertEqual(repository.find_school_candidates_for_ref(make_synthetic_form(), None, SEMESTER), [])

    def test_empty_or_invalid_authority_does_not_freeze_school_evidence(self):
        from app.review_automation.schedules.canonical import snapshot_canonical_school

        self.assertIsNone(snapshot_canonical_school(''))
        self.assertIsNone(snapshot_canonical_school(SEMESTER))

        batch, row = self.canonical()
        db.session.delete(row)
        db.session.commit()
        self.assertIsNone(snapshot_canonical_school(SEMESTER))
        selection = db.session.get(ScheduleSemesterSelection, SEMESTER)
        selection.active_batch_id = 999999
        db.session.commit()
        self.assertIsNone(snapshot_canonical_school(SEMESTER))

    def test_missing_frozen_source_does_not_parse_invalid_form_as_schedule_evidence(self):
        self.assertEqual(repository.find_school_candidates_for_ref(
            make_synthetic_form(class_period='invalid'), None, SEMESTER,
        ), [])

    def test_multi_segments_keep_gap_and_odd_even_weeks(self):
        batch, _ = self.canonical(
            class_period_raw='1-2节,7-9节', start_week_raw='1-5周(单),6-8周(双)',
        )
        entries = repository.get_school_entries_for_ref(self.ref(batch), SEMESTER)
        self.assertEqual([(entry.start_period, entry.end_period) for entry in entries], [(1, 2), (7, 9)])
        self.assertEqual(json.loads(entries[0].weeks_json), [1, 3, 5, 6, 8])
        matches = repository.find_school_candidates_for_ref(make_synthetic_form(
            class_period='4-5', lecture_location='28-203',
        ), self.ref(batch), SEMESTER)
        self.assertTrue(all('periods' in match.mismatched_fields for match in matches))
        self.assertTrue(all('location' not in match.mismatched_fields for match in matches))

    def test_venue_meeting_period_and_weeks_come_from_same_source_row(self):
        batch, row = self.canonical(class_period_raw='1-5节,7-11节')
        db.session.add(ListeningAssistantScheduleEntry(
            batch_id=batch.id, source_row=row.source_row, semester=SEMESTER,
            venue_period_raw='7-9节', venue_start_week_raw='2-8周(双)',
            location_raw='错误聚合地点', location_normalized='错误聚合地点',
        ))
        db.session.commit()
        entries = repository.get_school_entries_for_ref(self.ref(batch), SEMESTER)
        self.assertEqual([(entry.start_period, entry.end_period) for entry in entries], [(7, 9)])
        self.assertEqual(json.loads(entries[0].weeks_json), [2, 4, 6, 8])
        self.assertEqual(entries[0].location, '28-203')

    def test_school_alone_never_fabricates_personal_coverage(self):
        batch, _ = self.canonical()
        refs = {'school': self.ref(batch)}
        schedule = repository.get_listener_schedule_for_datasets('SYN-001', None, SEMESTER, refs)
        self.assertEqual(schedule.coverage, ScheduleCoverage.NONE)
        self.assertEqual(schedule.slots, ())

        personal = self.dataset('personal')
        db.session.add(PersonalScheduleSlot(
            dataset_id=personal.id, listener_number='SYN-001', course_title='个人课程',
            weeks_json='[1]', weekday=1, start_period=1, end_period=2,
        ))
        db.session.commit()
        schedule = repository.get_listener_schedule_for_datasets(
            'SYN-001', None, SEMESTER, {**refs, 'personal': {'id': personal.id}},
        )
        self.assertEqual(schedule.coverage, ScheduleCoverage.COMPLETE)
        self.assertEqual(schedule.slots[0].evidence_strength, EvidenceStrength.EXACT)

    def test_automation_school_references_remain_supported_and_room_equivalent(self):
        dataset = self.dataset('school')
        db.session.add(SchoolScheduleEntry(
            dataset_id=dataset.id, teacher_name='合成教师', teacher_college='合成学院',
            course_title='合成课程', teaching_class='合成行政班', weeks_json='[1]',
            weekday=1, start_period=1, end_period=2, location='28-0203',
        ))
        db.session.commit()
        matches = repository.find_school_candidates_for_ref(
            make_synthetic_form(lecture_location='28-203'), {'id': dataset.id}, SEMESTER,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].mismatched_fields, ())
        self.assertEqual(repository.find_school_candidates_for_dataset(
            make_synthetic_form(lecture_location='28-203'), dataset.id, SEMESTER,
        )[0].mismatched_fields, ())

    def test_numeric_scalar_anchors_and_bad_raw_fields_are_not_schedule_matches(self):
        batch, row = self.canonical(teacher_name='123', course_name='456')
        db.session.add(ScheduleImportRowScalarMeta(
            row_id=row.id, teacher_name_kind='int', course_name_kind='float',
        ))
        db.session.commit()
        self.assertEqual(repository.get_school_entries_for_ref(self.ref(batch), SEMESTER), [])
        row.teacher_name = '合成教师'
        row.course_name = '合成课程'
        row.scalar_meta.teacher_name_kind = 'text'
        row.scalar_meta.course_name_kind = 'text'
        row.start_week_raw = 'not-weeks'
        db.session.commit()
        self.assertEqual(repository.get_school_entries_for_ref(self.ref(batch), SEMESTER), [])

    def test_explicit_teaching_week_cannot_match_an_odd_week_only_meeting(self):
        batch, _ = self.canonical(start_week_raw='1-15周(单)')
        form = make_synthetic_form(lecture_location='28-203')
        form.teaching_week = 2
        matches = repository.find_school_candidates_for_ref(form, self.ref(batch), SEMESTER)
        self.assertEqual(len(matches), 1)
        self.assertIn('weeks', matches[0].mismatched_fields)

    def test_date_and_semester_monday_choose_only_the_correct_parity_meeting(self):
        batch, row = self.canonical(start_week_raw='1-15周(单)')
        db.session.add(ScheduleImportRow(
            batch_id=batch.id, source_row=3, teacher_name=row.teacher_name,
            teacher_college=row.teacher_college, course_name=row.course_name,
            weekday_raw=row.weekday_raw, class_period_raw=row.class_period_raw,
            location_raw=row.location_raw, class_composition_raw=row.class_composition_raw,
            start_week_raw='2-16周(双)',
        ))
        db.session.commit()
        matches = repository.find_school_candidates_for_ref(
            make_synthetic_form(lecture_date='2026/04/20星期一', lecture_location='28-203'),
            self.ref(batch), SEMESTER, semester_monday='2026-04-13',
        )
        exact = [match for match in matches if not match.mismatched_fields]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0].entry.source_row, 3)
        self.assertIn('weeks', exact[0].matched_fields)

    def test_invalid_nonempty_venue_period_does_not_fall_back_to_offering_period(self):
        batch, row = self.canonical()
        db.session.add(ListeningAssistantScheduleEntry(
            batch_id=batch.id, source_row=row.source_row, semester=SEMESTER,
            venue_period_raw='invalid period', venue_start_week_raw='1-16周',
        ))
        db.session.commit()
        self.assertEqual(repository.get_school_entries_for_ref(self.ref(batch), SEMESTER), [])

    def test_reported_periods_must_fit_inside_one_actual_meeting(self):
        batch, _ = self.canonical(class_period_raw='1-2节,7-9节')
        matches = repository.find_school_candidates_for_ref(
            make_synthetic_form(class_period='1-9', lecture_location='28-203'),
            self.ref(batch), SEMESTER,
        )
        self.assertTrue(matches)
        self.assertTrue(all('periods' in match.mismatched_fields for match in matches))
        matches = repository.find_school_candidates_for_ref(
            make_synthetic_form(class_period='8-9', lecture_location='28-203'),
            self.ref(batch), SEMESTER,
        )
        self.assertEqual(len([match for match in matches if not match.mismatched_fields]), 1)

    def test_venue_weeks_constrain_meeting_even_when_venue_period_is_blank(self):
        batch, row = self.canonical(start_week_raw='1-4周')
        db.session.add(ListeningAssistantScheduleEntry(
            batch_id=batch.id, source_row=row.source_row, semester=SEMESTER,
            venue_period_raw=None, venue_start_week_raw='2-8周(双)',
        ))
        db.session.commit()
        entries = repository.get_school_entries_for_ref(self.ref(batch), SEMESTER)
        self.assertEqual(len(entries), 1)
        self.assertEqual(json.loads(entries[0].weeks_json), [2, 4])

    def test_date_before_term_start_cannot_become_a_fully_matching_class(self):
        batch, _ = self.canonical()
        matches = repository.find_school_candidates_for_ref(
            make_synthetic_form(lecture_date='2026-04-06', lecture_location='28-203'),
            self.ref(batch), SEMESTER, semester_monday='2026-04-13',
        )
        self.assertTrue(matches)
        self.assertTrue(all('weeks' in match.mismatched_fields for match in matches))


if __name__ == '__main__':
    unittest.main()
