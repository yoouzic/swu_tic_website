"""Prepare a single administrator-configured timetable in the isolated debug DB."""
import hashlib
import os
from datetime import date
from pathlib import Path

import pandas as pd

from app.models import db, SystemSetting, ListeningAssistantScheduleEntry, ScheduleImportBatch
from app.services.academic_term import clean_term_text, compose_semester_identifier
from app.services.schedule_snapshots import persist_import_snapshot, resolve_current_schedule_snapshot
from app.services.listening_assistant_schedule import persist_listening_assistant_entries

try:  # Shared course importer; debug runs must still work before it lands.
    from app.services.current_courses import persist_courses_for_batch
except ImportError:  # pragma: no cover - only while the shared helper is absent
    persist_courses_for_batch = None

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEDULE = ROOT / '2025-2026-2全校课表260413.xlsx'
# Calendar supplied with this workspace's historical test timetable, keyed by
# semester identifier and (as a fallback) by the workbook's raw 学期 cell.
KNOWN_CALENDARS = {'2025-2026-2': '2026-03-02'}


def resolve_first_week_monday(semester, *, required, workbook_term=''):
    """Return the teaching-calendar anchor that applies to one semester.

    ``LOCAL_DEBUG_FIRST_WEEK_MONDAY`` is authoritative so that a corrected
    anchor takes effect on every launch, including the batch-reuse branch.
    Without it the known calendars are consulted by composed semester
    identifier first and by the workbook's raw term second.  Only a *new*
    import fails loudly when no anchor can be resolved.
    """
    configured = os.environ.get('LOCAL_DEBUG_FIRST_WEEK_MONDAY')
    if configured:
        if date.fromisoformat(configured).weekday() != 0:
            raise RuntimeError('第一教学周的起始日期必须是周一。')
        return configured
    for key in (semester, workbook_term):
        known = KNOWN_CALENDARS.get(str(key or '').strip())
        if known:
            return known
    if required:
        raise RuntimeError('新测试课表需通过 LOCAL_DEBUG_FIRST_WEEK_MONDAY 配置第一教学周周一日期。')
    return None


def prepare_debug_schedule():
    source = Path(os.environ.get('LOCAL_DEBUG_SCHEDULE_FILE') or DEFAULT_SCHEDULE).resolve()
    if not source.is_file():
        raise RuntimeError(f'调试课表不存在：{source}；可用 LOCAL_DEBUG_SCHEDULE_FILE 指定文件。')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    existing = ScheduleImportBatch.query.filter_by(source_sha256=digest, status='active').all()
    if len(existing) == 1:
        batch = existing[0]
        selected = resolve_current_schedule_snapshot(semester=batch.semester)
        indexed = ListeningAssistantScheduleEntry.query.filter_by(batch_id=batch.id).count()
        if selected.is_ready and selected.batch.id == batch.id and indexed:
            # Reuse must still apply a corrected calendar anchor, otherwise a
            # fixed LOCAL_DEBUG_FIRST_WEEK_MONDAY would be silently ignored.
            first_monday = resolve_first_week_monday(batch.semester, required=False)
            SystemSetting.set('teaching_current_semester', batch.semester)
            if first_monday:
                SystemSetting.set('teaching_first_week_monday', first_monday)
            db.session.commit()
            return f'当前调试课表：{batch.semester}（已存在，直接复用）'

    frame = pd.read_excel(source)
    if frame.empty or '学期' not in frame:
        raise RuntimeError('调试课表为空或缺少“学期”列。')

    # The canonical composition lives in the application, never here, so the
    # debug launcher and the admin upload cannot disagree about a semester.
    # Read the workbook's own term cells before overwriting the column: the
    # known calendars are keyed by semester identifier, and a workbook whose
    # 学年 is a bare year composes to a different key than its raw cell.
    raw_terms = {clean_term_text(value) for value in frame['学期']}
    raw_term = next(iter(raw_terms)) if len(raw_terms) == 1 else ''
    years = frame['学年'] if '学年' in frame else [''] * len(frame)
    frame['学期'] = [
        compose_semester_identifier(term, year)
        for term, year in zip(frame['学期'], years)
    ]
    semesters = set(frame['学期'])
    if len(semesters) != 1 or '' in semesters:
        raise RuntimeError('调试启动只接受一个学期的一份课表。')
    current = next(iter(semesters))
    first_monday = resolve_first_week_monday(current, required=True, workbook_term=raw_term)
    if '场地名称' not in frame and '上课地点' in frame:
        frame['场地名称'] = frame['上课地点']

    batches = persist_import_snapshot(frame, source_filename=source.name, source_sha256=digest)
    indexed = persist_listening_assistant_entries(frame, batches)
    if not indexed:
        db.session.rollback()
        raise RuntimeError('课表未生成可用听课索引，请检查课表字段。')
    # Registration/预约 needs Teacher/Venue/Course rows plus the exact
    # membership map, so the debug path persists the same rows the admin
    # importer does.  The helper never commits; this function owns the
    # transaction and commits the snapshot, index, and courses together.
    courses_added = 0
    course_row_errors = 0
    if persist_courses_for_batch is None:
        courses_text = '课程登记模块未就绪（persist_courses_for_batch 未落地）'
    else:
        for batch in batches:
            stats = persist_courses_for_batch(batch, frame)
            courses_added += int(stats.get('courses_added') or 0)
            course_row_errors += len(stats.get('errors') or ())
        courses_text = f'{courses_added} 条课程记录'
        if course_row_errors:
            courses_text += f'，{course_row_errors} 行未能生成课程记录'
    SystemSetting.set('teaching_current_semester', current)
    SystemSetting.set('teaching_first_week_monday', first_monday)
    SystemSetting.set('teaching_week_start_day', '0')
    SystemSetting.set('teaching_total_weeks', '20')
    db.session.commit()
    return (f'当前调试课表：{current}，已导入 {len(frame)} 行、'
            f'{len(indexed)} 条听课索引、{courses_text}。')
