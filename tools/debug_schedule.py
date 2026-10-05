"""Prepare a single administrator-configured timetable in the isolated debug DB."""
import hashlib
import os
from pathlib import Path

import pandas as pd

from app.models import db, SystemSetting, ListeningAssistantScheduleEntry, ScheduleImportBatch
from app.services.schedule_snapshots import persist_import_snapshot, resolve_current_schedule_snapshot
from app.services.listening_assistant_schedule import persist_listening_assistant_entries

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEDULE = ROOT / '2025-2026-2全校课表260413.xlsx'
# Calendar supplied with this workspace's historical test timetable.
KNOWN_CALENDARS = {'2025-2026-2': '2026-03-02'}


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
            SystemSetting.set('teaching_current_semester', batch.semester)
            db.session.commit()
            return f'当前调试课表：{batch.semester}（已存在，直接复用）'

    frame = pd.read_excel(source)
    if frame.empty or '学期' not in frame:
        raise RuntimeError('调试课表为空或缺少“学期”列。')

    def text(value):
        if pd.isna(value):
            return ''
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def semester(row):
        term = text(row['学期'])
        year = text(row.get('学年', ''))
        return f'{year}-{term}' if term in {'1', '2'} and year else term

    frame['学期'] = frame.apply(semester, axis=1)
    semesters = set(frame['学期'])
    if len(semesters) != 1 or '' in semesters:
        raise RuntimeError('调试启动只接受一个学期的一份课表。')
    current = next(iter(semesters))
    first_monday = os.environ.get('LOCAL_DEBUG_FIRST_WEEK_MONDAY') or KNOWN_CALENDARS.get(current)
    if not first_monday:
        raise RuntimeError('新测试课表需通过 LOCAL_DEBUG_FIRST_WEEK_MONDAY 配置第一教学周周一日期。')
    from datetime import date
    if date.fromisoformat(first_monday).weekday() != 0:
        raise RuntimeError('第一教学周的起始日期必须是周一。')
    if '场地名称' not in frame and '上课地点' in frame:
        frame['场地名称'] = frame['上课地点']

    batches = persist_import_snapshot(frame, source_filename=source.name, source_sha256=digest)
    indexed = persist_listening_assistant_entries(frame, batches)
    if not indexed:
        db.session.rollback()
        raise RuntimeError('课表未生成可用听课索引，请检查课表字段。')
    SystemSetting.set('teaching_current_semester', current)
    SystemSetting.set('teaching_first_week_monday', first_monday)
    SystemSetting.set('teaching_week_start_day', '0')
    SystemSetting.set('teaching_total_weeks', '20')
    db.session.commit()
    return f'当前调试课表：{current}，已导入 {len(frame)} 行、{len(indexed)} 条听课索引。'
