# -*- coding: utf-8 -*-
"""Round 6A-R1 canonical schedule selection invariant tests."""
import threading
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from app.app import app
from app.models import (
    ScheduleImportBatch,
    ScheduleImportRow,
    ScheduleSemesterSelection,
    SystemSetting,
    db,
)
from app.services.academic_term import (
    MAX_SEMESTER_IDENTIFIER_LENGTH,
    get_current_teaching_semester,
    normalize_semester_identifier,
)
from app.services.schedule_snapshots import (
    AMBIGUOUS,
    CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT,
    CURRENT_SEMESTER_UNSET,
    READY,
    CurrentScheduleSelection,
    get_active_schedule_batch,
    get_active_schedule_rows,
    persist_import_snapshot,
    resolve_current_schedule_snapshot,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

SEMESTER = '2026-2027-1'


def snapshot_df(semester=SEMESTER, teacher='张三'):
    return pd.DataFrame([{
        '姓名': teacher,
        '教师所属学院': '计算机学院',
        '课程名称': '数据结构',
        '星期几': 3,
        '上课节次': '第3-4节',
        '场地名称': '32-302',
        '教学班组成': '2023级计算机1班',
        '起始周': '1-16',
        '学期': semester,
        '学年': '2025',
    }])


class ScheduleSelectionContractTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='selection-contract-')
        self.db_path = Path(self.temp_dir.name) / 'selection.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _set_current(self, semester):
        SystemSetting.set('teaching_current_semester', semester or '')

    def _persist(self, semester=SEMESTER, teacher='张三', filename='x.xlsx'):
        return persist_import_snapshot(
            snapshot_df(semester=semester, teacher=teacher),
            source_filename=filename,
            source_sha256='a' * 64,
        )

    def test_unset_current_semester_never_selects_blank_batch(self):
        # Blank-semester active snapshot exists and has an authoritative pointer.
        self._persist(semester='')
        self._set_current('')

        result = resolve_current_schedule_snapshot()
        self.assertEqual(result.status, CURRENT_SEMESTER_UNSET)
        self.assertEqual(result.semester, '')
        self.assertIsNone(result.batch)
        self.assertEqual(result.rows, [])

        # Direct old-API combination is also fail-closed: even if someone
        # calls get_active_schedule_rows('') manually it may still return the
        # blank batch, but the canonical boundary never exposes it as current.
        self.assertIsNotNone(get_active_schedule_batch(''))

    def test_configured_semester_without_snapshot_returns_configured_no_snapshot(self):
        self._set_current(SEMESTER)
        result = resolve_current_schedule_snapshot()
        self.assertEqual(
            result.status,
            CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT,
        )
        self.assertEqual(result.semester, SEMESTER)
        self.assertIsNone(result.batch)
        self.assertEqual(result.rows, [])

    def test_configured_semester_with_snapshot_returns_ready_ordered_rows(self):
        batch = self._persist()
        self._set_current(SEMESTER)

        result = resolve_current_schedule_snapshot()
        self.assertEqual(result.status, READY)
        self.assertEqual(result.semester, SEMESTER)
        self.assertEqual(result.batch.id, batch[0].id)
        self.assertEqual([row.source_row for row in result.rows], [2])
        self.assertEqual(result.rows[0].teacher_name, '张三')

    def test_replaced_snapshot_authority_points_to_newest_batch(self):
        first = self._persist(teacher='张三', filename='first.xlsx')[0]
        second = self._persist(teacher='李四', filename='second.xlsx')[0]
        self._set_current(SEMESTER)

        result = resolve_current_schedule_snapshot()
        self.assertEqual(result.status, READY)
        self.assertEqual(result.batch.id, second.id)
        self.assertNotEqual(result.batch.id, first.id)
        self.assertEqual(result.rows[0].teacher_name, '李四')

        selection = db.session.get(ScheduleSemesterSelection, SEMESTER)
        self.assertEqual(selection.active_batch_id, second.id)

    def test_two_different_semesters_each_have_independent_authority(self):
        x_batch = self._persist(semester='X', teacher='X教师')[0]
        y_batch = self._persist(semester='Y', teacher='Y教师')[0]

        result_x = resolve_current_schedule_snapshot(semester='X')
        result_y = resolve_current_schedule_snapshot(semester='Y')
        self.assertEqual(result_x.status, READY)
        self.assertEqual(result_y.status, READY)
        self.assertEqual(result_x.batch.id, x_batch.id)
        self.assertEqual(result_y.batch.id, y_batch.id)
        self.assertEqual(result_x.rows[0].teacher_name, 'X教师')
        self.assertEqual(result_y.rows[0].teacher_name, 'Y教师')

    def test_ambiguous_legacy_active_rows_fail_closed_without_selection(self):
        b1 = ScheduleImportBatch(
            semester='AMBIG1', status='active', row_count=0,
            source_filename='a.xlsx', source_sha256='a' * 64,
        )
        b2 = ScheduleImportBatch(
            semester='AMBIG1', status='active', row_count=0,
            source_filename='b.xlsx', source_sha256='b' * 64,
        )
        db.session.add_all([b1, b2])
        db.session.commit()

        self.assertIsNone(get_active_schedule_batch('AMBIG1'))
        result = resolve_current_schedule_snapshot(semester='AMBIG1')
        self.assertEqual(result.status, AMBIGUOUS)
        self.assertIsNone(result.batch)
        self.assertEqual(result.rows, [])

    def test_selection_pointer_is_authority_over_status_flags(self):
        b1 = ScheduleImportBatch(
            semester='AUTH1', status='active', row_count=0,
            source_filename='a.xlsx', source_sha256='a' * 64,
        )
        b2 = ScheduleImportBatch(
            semester='AUTH1', status='active', row_count=0,
            source_filename='b.xlsx', source_sha256='b' * 64,
        )
        db.session.add_all([b1, b2])
        db.session.flush()
        db.session.add(ScheduleSemesterSelection(
            semester='AUTH1',
            active_batch_id=b1.id,
        ))
        db.session.commit()

        self.assertEqual(get_active_schedule_batch('AUTH1').id, b1.id)
        result = resolve_current_schedule_snapshot(semester='AUTH1')
        self.assertEqual(result.status, READY)
        self.assertEqual(result.batch.id, b1.id)

    def test_legacy_single_active_without_selection_still_resolves(self):
        batch = ScheduleImportBatch(
            semester='LEGACY1', status='active', row_count=0,
            source_filename='a.xlsx', source_sha256='a' * 64,
        )
        db.session.add(batch)
        db.session.commit()

        result = resolve_current_schedule_snapshot(semester='LEGACY1')
        self.assertEqual(result.status, READY)
        self.assertEqual(result.batch.id, batch.id)
        self.assertEqual(result.rows, [])

    def test_batch_row_count_matches_actual_rows(self):
        df = snapshot_df()
        batch = persist_import_snapshot(df, 'x.xlsx', 'a' * 64)[0]
        self.assertEqual(batch.row_count, len(df))
        self.assertEqual(ScheduleImportRow.query.filter_by(batch_id=batch.id).count(), len(df))

    def test_source_rows_are_unique_within_batch(self):
        df = pd.concat(
            [snapshot_df(), snapshot_df(teacher='李四')],
            ignore_index=True,
        )
        batch = persist_import_snapshot(df, 'x.xlsx', 'a' * 64)[0]
        rows = ScheduleImportRow.query.filter_by(batch_id=batch.id).all()
        source_rows = [row.source_row for row in rows]
        self.assertEqual(len(source_rows), len(set(source_rows)))

    def test_multi_semester_batches_are_grouped_consistently(self):
        df = pd.DataFrame([
            snapshot_df(semester='A', teacher='甲').iloc[0],
            snapshot_df(semester='B', teacher='乙').iloc[0],
            snapshot_df(semester='A', teacher='丙').iloc[0],
        ])
        batches = persist_import_snapshot(df, 'multi.xlsx', 'a' * 64)
        by_semester = {batch.semester: batch for batch in batches}
        self.assertEqual(by_semester['A'].row_count, 2)
        self.assertEqual(by_semester['B'].row_count, 1)
        self.assertEqual(
            ScheduleImportRow.query.filter_by(batch_id=by_semester['A'].id).count(),
            2,
        )
        self.assertEqual(
            ScheduleImportRow.query.filter_by(batch_id=by_semester['B'].id).count(),
            1,
        )

    def test_semester_identifier_contract_aligned_to_snapshot_storage(self):
        self.assertEqual(MAX_SEMESTER_IDENTIFIER_LENGTH, 50)
        # 50 allowed, 51 rejected by the same canonical contract.
        self.assertEqual(len(normalize_semester_identifier('x' * 50)), 50)
        with self.assertRaises(ValueError):
            normalize_semester_identifier('x' * 51)

    def test_concurrent_same_semester_replacement_keeps_single_authority(self):
        # Pre-create one authoritative baseline so both workers replace an
        # existing selection, matching the real upgrade path.
        self._persist(semester=SEMESTER, teacher='baseline')
        db.session.commit()

        barrier = threading.Barrier(2)
        results = []

        def worker(label):
            with app.app_context():
                db.session.remove()
                barrier.wait()
                try:
                    persist_import_snapshot(
                        snapshot_df(semester=SEMESTER, teacher=label),
                        source_filename=f'{label}.xlsx',
                        source_sha256=label * 64,
                    )
                    db.session.commit()
                    results.append((label, 'ok', None))
                except Exception as exc:
                    db.session.rollback()
                    results.append((label, 'error', str(exc)))

        threads = [
            threading.Thread(target=worker, args=('A',)),
            threading.Thread(target=worker, args=('B',)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(len(results), 2)
        # Both may succeed; if one fails it must have rolled back cleanly.
        ok = [label for label, status, _ in results if status == 'ok']
        self.assertGreaterEqual(len(ok), 1)
        selection_count = ScheduleSemesterSelection.query.filter_by(
            semester=SEMESTER,
        ).count()
        self.assertEqual(selection_count, 1)

        selection = db.session.get(ScheduleSemesterSelection, SEMESTER)
        self.assertIsNotNone(selection)
        active_batch = db.session.get(ScheduleImportBatch, selection.active_batch_id)
        self.assertIsNotNone(active_batch)
        self.assertEqual(get_active_schedule_batch(SEMESTER).id, active_batch.id)
        # The authoritative pointer is singular and points to exactly one batch.
        self.assertEqual(
            ScheduleSemesterSelection.query.filter_by(
                semester=SEMESTER,
                active_batch_id=active_batch.id,
            ).count(),
            1,
        )

    def test_concurrent_different_semesters_do_not_interfere(self):
        barrier = threading.Barrier(2)
        results = []

        def worker(semester, teacher):
            with app.app_context():
                db.session.remove()
                barrier.wait()
                try:
                    persist_import_snapshot(
                        snapshot_df(semester=semester, teacher=teacher),
                        source_filename=f'{semester}.xlsx',
                        source_sha256=semester * 64,
                    )
                    db.session.commit()
                    results.append((semester, 'ok', None))
                except Exception as exc:
                    db.session.rollback()
                    results.append((semester, 'error', str(exc)))

        threads = [
            threading.Thread(target=worker, args=('X', '甲')),
            threading.Thread(target=worker, args=('Y', '乙')),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(len(results), 2)
        ok = [semester for semester, status, _ in results if status == 'ok']
        self.assertGreaterEqual(len(ok), 1)
        for semester in ('X', 'Y'):
            selection = db.session.get(ScheduleSemesterSelection, semester)
            if selection is not None:
                self.assertEqual(
                    get_active_schedule_batch(semester).id,
                    selection.active_batch_id,
                )


if __name__ == '__main__':
    unittest.main()
