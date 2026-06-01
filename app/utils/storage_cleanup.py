# -*- coding: utf-8 -*-
"""Scheduled cleanup for generated upload and report files."""

from datetime import datetime, timedelta
from pathlib import Path

from ..models import SystemSetting
from .env_config import env_bool, env_int, env_path


AUTO_REVIEW_SETTING_KEYS = (
    'auto_review_schedule_path',
    'auto_review_contacts_path',
    'auto_review_feedback_path',
)
AUTO_REVIEW_PREFIXES = ('schedule', 'contacts', 'feedback')
REPORT_PREFIX = 'auto_review_report_'
LAST_RUN_MARKER = '.storage_cleanup_last'

def _is_relative_to(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _file_mtime(file_path):
    return datetime.fromtimestamp(file_path.stat().st_mtime)


def _safe_unlink(file_path, root):
    if not file_path.is_file() or not _is_relative_to(file_path, root):
        return False
    file_path.unlink()
    return True


def _latest_files(files, keep_count):
    if keep_count <= 0:
        return set()
    sorted_files = sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)
    return {item.resolve() for item in sorted_files[:keep_count]}


def _protected_auto_review_paths(upload_dir):
    protected = set()
    for key in AUTO_REVIEW_SETTING_KEYS:
        raw_path = SystemSetting.get(key)
        if not raw_path:
            continue
        path = Path(raw_path)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if _is_relative_to(resolved, upload_dir):
            protected.add(resolved)
    return protected


def cleanup_auto_review_uploads(base_dir, now=None):
    now = now or datetime.now()
    upload_dir = Path(env_path('AUTO_REVIEW_UPLOAD_DIR', Path('data') / 'storage' / 'uploads' / 'auto_review'))
    retention_days = env_int('AUTO_REVIEW_UPLOAD_RETENTION_DAYS', 60, minimum=1)
    keep_per_type = env_int('AUTO_REVIEW_UPLOAD_KEEP_PER_TYPE', 3, minimum=0)
    cutoff = now - timedelta(days=retention_days)
    summary = {
        'directory': str(upload_dir),
        'deleted': 0,
        'kept_current': 0,
        'kept_recent': 0,
        'retention_days': retention_days,
        'keep_per_type': keep_per_type,
    }
    if not upload_dir.exists():
        return summary

    protected = _protected_auto_review_paths(upload_dir)
    keep_recent = set()
    for prefix in AUTO_REVIEW_PREFIXES:
        keep_recent.update(_latest_files(list(upload_dir.glob(f'{prefix}_*')), keep_per_type))

    for file_path in upload_dir.iterdir():
        if not file_path.is_file():
            continue
        resolved = file_path.resolve()
        if resolved in protected:
            summary['kept_current'] += 1
            continue
        if resolved in keep_recent:
            summary['kept_recent'] += 1
            continue
        if _file_mtime(file_path) < cutoff and _safe_unlink(file_path, upload_dir):
            summary['deleted'] += 1
    return summary


def cleanup_auto_review_reports(base_dir, now=None):
    now = now or datetime.now()
    reports_dir = Path(env_path('AUTO_REVIEW_REPORT_DIR', Path('data') / 'storage' / 'exports' / 'auto_review'))
    retention_days = env_int('AUTO_REVIEW_REPORT_RETENTION_DAYS', 60, minimum=1)
    keep_count = env_int('AUTO_REVIEW_REPORT_KEEP_COUNT', 20, minimum=0)
    cutoff = now - timedelta(days=retention_days)
    summary = {
        'directory': str(reports_dir),
        'deleted': 0,
        'kept_recent': 0,
        'retention_days': retention_days,
        'keep_count': keep_count,
    }
    if not reports_dir.exists():
        return summary

    report_files = list(reports_dir.glob(f'{REPORT_PREFIX}*.xlsx'))
    keep_recent = _latest_files(report_files, keep_count)
    for file_path in report_files:
        resolved = file_path.resolve()
        if resolved in keep_recent:
            summary['kept_recent'] += 1
            continue
        if _file_mtime(file_path) < cutoff and _safe_unlink(file_path, reports_dir):
            summary['deleted'] += 1
    return summary


def run_storage_cleanup(base_dir):
    now = datetime.now()
    return {
        'ran_at': now.isoformat(timespec='seconds'),
        'auto_review_uploads': cleanup_auto_review_uploads(base_dir, now=now),
        'auto_review_reports': cleanup_auto_review_reports(base_dir, now=now),
    }


def run_scheduled_storage_cleanup(base_dir):
    if not env_bool('STORAGE_CLEANUP_ENABLED', True):
        return {'skipped': True, 'reason': 'disabled'}

    interval_hours = env_int('STORAGE_CLEANUP_INTERVAL_HOURS', 24, minimum=1)
    instance_dir = Path(env_path('INSTANCE_DIR', 'data/instance'))
    instance_dir.mkdir(parents=True, exist_ok=True)
    marker_path = instance_dir / LAST_RUN_MARKER
    now = datetime.now()

    if marker_path.exists():
        try:
            last_run = datetime.fromisoformat(marker_path.read_text(encoding='utf-8').strip())
            if now - last_run < timedelta(hours=interval_hours):
                return {'skipped': True, 'reason': 'interval_not_reached'}
        except Exception:
            pass

    result = run_storage_cleanup(base_dir)
    marker_path.write_text(now.isoformat(timespec='seconds'), encoding='utf-8')
    return result
