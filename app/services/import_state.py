# -*- coding: utf-8 -*-
"""Persisted runtime state for contacts import preview and export progress.

This service intentionally uses short-lived SQLAlchemy Sessions so state writes
never commit the caller's request-scoped business transaction.
"""
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ExportJobRecord, ImportPreviewSession, db


STATE_TTL = timedelta(hours=24)


def _cutoff():
    return datetime.now() - STATE_TTL


def _cleanup_stale(state_session: Session) -> None:
    cutoff = _cutoff()
    state_session.query(ImportPreviewSession).filter(
        ImportPreviewSession.created_at < cutoff
    ).delete(synchronize_session=False)
    state_session.query(ExportJobRecord).filter(
        ExportJobRecord.created_at < cutoff
    ).delete(synchronize_session=False)


def save_import_preview(rows, expected_cols) -> str:
    """Persist preview data and return the import_id."""
    import_id = secrets.token_hex(8)
    payload = {
        'rows': rows,
        'expected_cols': expected_cols,
    }
    with Session(bind=db.engine) as state_session:
        _cleanup_stale(state_session)
        state_session.add(ImportPreviewSession(
            id=import_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
        ))
        state_session.commit()
    return import_id


def load_import_preview(import_id: str) -> Optional[dict]:
    """Return the saved preview dict, or None if missing/expired."""
    if not import_id:
        return None
    with Session(bind=db.engine) as state_session:
        record = state_session.get(ImportPreviewSession, import_id)
        if record is None:
            return None
        if record.created_at < _cutoff():
            state_session.delete(record)
            state_session.commit()
            return None
        return json.loads(record.payload_json)


def consume_import_preview(import_id: str) -> Optional[dict]:
    """Load and delete the preview in one isolated state transaction."""
    payload = load_import_preview(import_id)
    if payload is None:
        return None
    with Session(bind=db.engine) as state_session:
        record = state_session.get(ImportPreviewSession, import_id)
        if record is not None:
            state_session.delete(record)
            state_session.commit()
    return payload


def start_export_job() -> str:
    """Create a running export record and return job_id."""
    job_id = secrets.token_hex(8)
    with Session(bind=db.engine) as state_session:
        _cleanup_stale(state_session)
        state_session.add(ExportJobRecord(
            id=job_id,
            status='running',
            percent=0,
            message='准备导出...',
        ))
        state_session.commit()
    return job_id


def update_export_job(job_id: str, **fields) -> None:
    """Update export progress fields in an isolated state transaction."""
    if not job_id:
        return
    allowed = {'status', 'percent', 'message', 'download_url'}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    with Session(bind=db.engine) as state_session:
        record = state_session.get(ExportJobRecord, job_id)
        if record is None:
            return
        for key, value in updates.items():
            setattr(record, key, value)
        state_session.commit()


def get_export_job(job_id: str) -> Optional[dict]:
    """Return the public progress dict matching the old config cache shape."""
    if not job_id:
        return None
    with Session(bind=db.engine) as state_session:
        record = state_session.get(ExportJobRecord, job_id)
        if record is None:
            return None
        if record.created_at < _cutoff():
            state_session.delete(record)
            state_session.commit()
            return None
        progress = {
            'status': record.status,
            'percent': record.percent,
            'message': record.message,
        }
        if record.status == 'completed' and record.download_url:
            progress['download_url'] = record.download_url
        return progress
