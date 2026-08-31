# -*- coding: utf-8 -*-
"""Persisted runtime state for contacts import preview and export progress.

This service intentionally uses short-lived SQLAlchemy Sessions so state writes
never commit the caller's request-scoped business transaction.

Preview claim lifecycle (Round 7C-P1-R2, durable claim + COMMITTED terminal):

    AVAILABLE --claim(CAS UPDATE)--> CLAIMED(token) --finalize(CAS UPDATE)-->
    COMMITTED(token, artifact)   [terminal, same transaction as the business
    commit; publication proof]
        |--release(CAS)--> AVAILABLE   (failure retry; never on COMMITTED)
        |--lease expiry--> reclaimable by a new caller (CLAIMED only)

Claim ownership is durable: the claim is a CAS UPDATE on ``payload_json`` (no
schema migration), so a worker crash after claiming leaves the row in place
and recoverable after the claim lease expires.  Ownership decisions come from
database CAS rowcounts only — never process-local locks.

Publication contract (R2): "preview row missing" is NOT proof of success (the
row can also disappear via TTL cleanup, maintenance or legacy removal).  A
token-scoped password artifact may only be served when a durable COMMITTED
marker proves the exact (claim_token, filename) pair was published — absence
of a blocker is not proof of success, so every non-COMMITTED situation
(including a missing row) fails closed.

Result replay contract (R3, Round 7C-P1-R3): a successful confirm stores its
minimal outcome (4 counts, no business payload) inside the COMMITTED v3
envelope, in the same business transaction.  The import side effect stays
strictly at-most-once (COMMITTED is terminal, never re-executed), but the
successful result becomes idempotently replayable through
``get_committed_import_result`` so a lost HTTP response can be recovered.
"""
import json
import secrets
from datetime import datetime, timedelta
from typing import NamedTuple, Optional

from sqlalchemy.orm import Session

from app.models import ExportJobRecord, ImportPreviewSession, db


STATE_TTL = timedelta(hours=24)

# A CLAIMED preview is not blanket-cleaned; a crashed worker's claim becomes
# reclaimable by another caller after this lease.
IMPORT_CLAIM_LEASE = timedelta(minutes=30)

# claimed envelope keeps version 1 so in-flight P1-R1 claims stay
# releasable / recoverable across this upgrade (backward compatible extension).
_IMPORT_STATE_VERSION = 1
# COMMITTED terminal envelope (Round 7C-P1-R2): durable publication proof.
_COMMITTED_STATE_VERSION = 2
# COMMITTED v3 (Round 7C-P1-R3): + minimal replay result metadata.
_COMMITTED_RESULT_STATE_VERSION = 3
_STATE_AVAILABLE = 'available'
_STATE_CLAIMED = 'claimed'
_STATE_COMMITTED = 'committed'

# The only keys a COMMITTED v3 ``_result`` may carry — counts deterministic
# enough to rebuild the presentation message, never business payload.
_COMMITTED_RESULT_KEYS = (
    'imported_count', 'updated_count', 'skipped_count', 'total_users',
)


class ImportPreviewClaim(NamedTuple):
    """Ownership token returned to the single claim winner.

    ``claimed_raw_json`` is the CAS identity: release/finalize only mutate the
    row while its raw state still equals this value, so a stale worker can
    never touch a newer owner's claim.
    """

    import_id: str
    payload: dict
    created_at: datetime
    claim_token: str
    claimed_raw_json: str


class ImportPreviewOwnershipLostError(RuntimeError):
    """Raised by finalize when the claim no longer owns the preview row."""

    def __init__(self, import_id: str):
        super().__init__(f'导入预览权限已丢失: {import_id}')
        self.import_id = import_id


def _now():
    """Single clock entry point for TTL / claim / lease bookkeeping."""
    return datetime.now()


def _cutoff(now=None):
    return (_now() if now is None else now) - STATE_TTL


class _PreviewSnapshot(NamedTuple):
    state: str
    payload: Optional[dict]
    claim_token: Optional[str]
    claimed_at: Optional[datetime]
    committed_at: Optional[datetime] = None
    password_artifact: Optional[str] = None
    result: Optional[dict] = None


class ImportCommittedResult(NamedTuple):
    """Typed replay payload of a durably COMMITTED import (Round 7C-P1-R3).

    The counts snapshot the original run: a replay must return the committed
    outcome (including the original ``total_users``), never a recomputation
    against the current user table.  Business payload (rows, contacts fields,
    passwords) is deliberately absent.
    """

    import_id: str
    claim_token: str
    committed_at: datetime
    password_artifact: Optional[str]
    imported_count: int
    updated_count: int
    skipped_count: int
    total_users: int


def _decode_preview_state(raw_json: str) -> Optional[_PreviewSnapshot]:
    """Decode raw ``payload_json`` into preview state.

    legacy/plain payloads（含 ``{"rows": [...], "expected_cols": [...]}`` 与
    历史任意 dict 行）→ AVAILABLE；claimed envelope → 严格校验后 CLAIMED；
    committed envelope → 严格校验后 COMMITTED；不可解析 / 非 dict / 残缺或
    未知的 envelope → None（fail closed，绝不把残缺/未知状态重新暴露为
    AVAILABLE payload，也不把 malformed terminal state 当作已发布）。
    """
    try:
        decoded = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None

    marker = decoded.get('_import_state')
    if marker is None:
        return _PreviewSnapshot(_STATE_AVAILABLE, decoded, None, None)

    if marker == _STATE_CLAIMED:
        if decoded.get('_import_state_version') != _IMPORT_STATE_VERSION:
            return None
        payload = decoded.get('payload')
        token = decoded.get('_claim_token')
        claimed_at_raw = decoded.get('_claimed_at')
        if not isinstance(payload, dict) or not token or not claimed_at_raw:
            return None
        try:
            claimed_at = datetime.fromisoformat(claimed_at_raw)
        except ValueError:
            return None
        return _PreviewSnapshot(_STATE_CLAIMED, payload, token, claimed_at)

    if marker == _STATE_COMMITTED:
        version = decoded.get('_import_state_version')
        if version == _COMMITTED_STATE_VERSION:
            result = None  # v2: publication proof only, replay metadata unavailable
        elif version == _COMMITTED_RESULT_STATE_VERSION:
            result = _validated_committed_result(decoded.get('_result'))
        else:
            return None
        token = decoded.get('_claim_token')
        committed_at_raw = decoded.get('_committed_at')
        artifact = decoded.get('_password_artifact')
        if not token or not committed_at_raw:
            return None
        if artifact is not None and not isinstance(artifact, str):
            return None
        try:
            committed_at = datetime.fromisoformat(committed_at_raw)
        except ValueError:
            return None
        return _PreviewSnapshot(
            _STATE_COMMITTED, None, token, None, committed_at, artifact, result,
        )

    return None  # unknown state marker → fail closed


def _validated_committed_result(raw_result) -> Optional[dict]:
    """Validate a COMMITTED v3 ``_result`` payload (exactly the 4 count keys).

    Anything absent / malformed / carrying extra keys → None: the marker then
    degrades to v2 semantics (publication proof intact, replay unavailable) —
    never to a false replay.
    """
    if not isinstance(raw_result, dict):
        return None
    if set(raw_result.keys()) != set(_COMMITTED_RESULT_KEYS):
        return None
    counts = {}
    for key in _COMMITTED_RESULT_KEYS:
        value = raw_result[key]
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        counts[key] = value
    return counts


def _encode_claimed_state(payload: dict, token: str, claimed_at: datetime) -> str:
    return json.dumps({
        '_import_state_version': _IMPORT_STATE_VERSION,
        '_import_state': _STATE_CLAIMED,
        '_claim_token': token,
        '_claimed_at': claimed_at.isoformat(),
        'payload': payload,
    }, ensure_ascii=False)


def _encode_committed_state(token: str, committed_at: datetime,
                            password_artifact: Optional[str],
                            result: Optional[dict] = None) -> str:
    """Terminal COMMITTED envelope: publication proof (+ optional replay result).

    rows / expected_cols / contacts fields / passwords are deliberately dropped
    so the marker can be kept indefinitely without retaining any business
    payload.  With ``result`` the envelope is written as v3 (replayable);
    without it as v2 (publication proof only, backward compatible).
    """
    envelope = {
        '_import_state_version': (
            _COMMITTED_RESULT_STATE_VERSION if result is not None
            else _COMMITTED_STATE_VERSION
        ),
        '_import_state': _STATE_COMMITTED,
        '_claim_token': token,
        '_committed_at': committed_at.isoformat(),
        '_password_artifact': password_artifact,
    }
    if result is not None:
        envelope['_result'] = dict(result)
    return json.dumps(envelope, ensure_ascii=False)


def _encode_available_state(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _cleanup_stale(state_session: Session) -> None:
    cutoff = _cutoff()
    state_session.query(ExportJobRecord).filter(
        ExportJobRecord.created_at < cutoff
    ).delete(synchronize_session=False)
    # Preview rows carry their lifecycle inside payload_json, so cleanup must
    # decode row by row: stale AVAILABLE rows follow STATE_TTL, CLAIMED rows
    # are governed by the claim lease / recovery protocol and are never
    # blanket-deleted (a crashed worker's claim must stay recoverable), and
    # COMMITTED rows are terminal publication proof — never cleaned here until
    # PASSWORD_EXPORT_RETENTION_POLICY is decided.
    for record in state_session.query(ImportPreviewSession).all():
        state = _decode_preview_state(record.payload_json)
        if state is None:
            continue  # malformed → fail closed (spared, never exposed)
        if state.state == _STATE_AVAILABLE and record.created_at < cutoff:
            state_session.delete(record)


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
            payload_json=_encode_available_state(payload),
        ))
        state_session.commit()
    return import_id


def load_import_preview(import_id: str) -> Optional[dict]:
    """Return the saved preview dict, or None if missing/claimed/committed/expired.

    Public callers never see claim bookkeeping: a CLAIMED row is invisible
    until it is released (failure retry) or finalized (business commit), and a
    COMMITTED row stays invisible forever (logical consume of a terminal
    state).
    """
    if not import_id:
        return None
    with Session(bind=db.engine) as state_session:
        record = state_session.get(ImportPreviewSession, import_id)
        if record is None:
            return None
        state = _decode_preview_state(record.payload_json)
        if state is None or state.state != _STATE_AVAILABLE:
            return None
        if record.created_at < _cutoff():
            state_session.delete(record)
            state_session.commit()
            return None
        return state.payload


def claim_import_preview(import_id: str) -> Optional[ImportPreviewClaim]:
    """Atomically claim the preview: exactly one caller ever owns it.

    CAS UPDATE on ``payload_json`` (old raw state → claimed envelope);
    ``rowcount == 1`` → winner, ``rowcount == 0`` → lost race / state changed.
    A CLAIMED row inside the lease is not claimable; after the lease expires a
    new caller recovers it by CASing the stale claimed state to a new claim
    (crash recovery).  A COMMITTED row is terminal publication proof: it is
    never reclaimable, regardless of any timestamps.  No row is deleted by
    claiming.
    """
    if not import_id:
        return None

    with Session(bind=db.engine) as state_session:
        record = state_session.get(ImportPreviewSession, import_id)
        if record is None:
            return None
        state = _decode_preview_state(record.payload_json)
        if state is None:
            return None  # malformed → fail closed
        created_at = record.created_at  # Session 关闭前取值，避免 detached 刷新

        now = _now()
        if state.state == _STATE_COMMITTED:
            return None  # terminal: 已发布的 import 绝不重新进入状态机
        if state.state == _STATE_CLAIMED:
            if state.claimed_at is None or (now - state.claimed_at) < IMPORT_CLAIM_LEASE:
                return None  # actively claimed by another worker
        elif record.created_at < _cutoff(now):
            state_session.delete(record)
            state_session.commit()
            return None

        token = secrets.token_hex(16)
        claimed_json = _encode_claimed_state(state.payload, token, now)
        claim_filter = [
            ImportPreviewSession.id == import_id,
            ImportPreviewSession.payload_json == record.payload_json,
        ]
        if state.state == _STATE_AVAILABLE:
            claim_filter.append(ImportPreviewSession.created_at >= _cutoff(now))
        updated = state_session.query(ImportPreviewSession).filter(*claim_filter).update(
            {'payload_json': claimed_json}, synchronize_session=False,
        )
        state_session.commit()

    if updated != 1:
        return None
    return ImportPreviewClaim(
        import_id=import_id,
        payload=state.payload,
        created_at=created_at,
        claim_token=token,
        claimed_raw_json=claimed_json,
    )


def release_import_preview_claim(claim: Optional[ImportPreviewClaim]) -> bool:
    """CAS CLAIMED(token) → AVAILABLE so a failed confirm can be retried.

    Only succeeds while the row's raw state still equals this claim's
    ``claimed_raw_json`` — a stale worker whose claim was taken over by lease
    recovery cannot release (and thus cannot clobber) the new owner's state.
    A COMMITTED row never matches ``claimed_raw_json``, so releasing a
    finalized import is impossible by construction (returns False).
    """
    if claim is None or not claim.claim_token:
        return False
    with Session(bind=db.engine) as state_session:
        updated = state_session.query(ImportPreviewSession).filter(
            ImportPreviewSession.id == claim.import_id,
            ImportPreviewSession.payload_json == claim.claimed_raw_json,
        ).update(
            {'payload_json': _encode_available_state(claim.payload)},
            synchronize_session=False,
        )
        state_session.commit()
    return updated == 1


def finalize_import_preview_claim(claim: Optional[ImportPreviewClaim], session: Session,
                                  password_artifact: Optional[str] = None,
                                  result: Optional[dict] = None) -> bool:
    """CAS CLAIMED(token) → COMMITTED(token, artifact[, result]) in the business commit.

    The conditional UPDATE (CAS on ``claimed_raw_json``) must run in the
    caller's session so the committed marker (publication proof + replay
    result metadata) commits atomically with the business mutation.  Raises
    :class:`ImportPreviewOwnershipLostError` when ownership was lost (lease
    recovery took over) — the caller must roll the business transaction back
    instead of committing user changes.
    """
    if claim is None:
        raise ImportPreviewOwnershipLostError('')
    committed_json = _encode_committed_state(
        claim.claim_token, _now(), password_artifact, result,
    )
    updated = session.query(ImportPreviewSession).filter(
        ImportPreviewSession.id == claim.import_id,
        ImportPreviewSession.payload_json == claim.claimed_raw_json,
    ).update({'payload_json': committed_json}, synchronize_session=False)
    if updated != 1:
        raise ImportPreviewOwnershipLostError(claim.import_id)
    return True


def import_preview_exists(import_id: str) -> bool:
    """Legacy publish-gate helper for P1/P1-R1 ``passwords_<import_id>.xlsx`` files.

    Kept for backward compatibility with artifacts that carry no claim token
    (LEGACY_PASSWORD_ARTIFACT_PROVENANCE_AMBIGUITY).  New token-scoped
    artifacts must use :func:`is_password_artifact_published` instead.
    """
    if not import_id:
        return False
    with Session(bind=db.engine) as state_session:
        return state_session.get(ImportPreviewSession, import_id) is not None


def is_password_artifact_published(import_id: str, claim_token: str, filename: str) -> bool:
    """Positive publication proof for a token-scoped password artifact.

    True only when a durable COMMITTED marker exists for ``import_id`` whose
    claim token and recorded artifact filename both match exactly.  Anything
    else — missing row, AVAILABLE, CLAIMED, a different token/filename, or a
    malformed state — is False: row absence is never treated as proof of
    success (Round 7C-P1-R2 publication contract).
    """
    if not import_id or not claim_token or not filename:
        return False
    with Session(bind=db.engine) as state_session:
        record = state_session.get(ImportPreviewSession, import_id)
        if record is None:
            return False
        state = _decode_preview_state(record.payload_json)
    if state is None or state.state != _STATE_COMMITTED:
        return False
    if state.claim_token != claim_token:
        return False
    return state.password_artifact == filename


def get_committed_import_result(import_id: str) -> Optional[ImportCommittedResult]:
    """Typed successful-outcome replay handle for a COMMITTED import (R3).

    Returns a result only for a valid COMMITTED v3 envelope carrying replay
    metadata.  missing / AVAILABLE / CLAIMED / malformed / COMMITTED v2
    (metadata unavailable) / v3 with malformed metadata → None — an active
    claim is never misclassified as committed, and a failed commit never
    yields a success replay.  Never returns the raw envelope.
    """
    if not import_id:
        return None
    with Session(bind=db.engine) as state_session:
        record = state_session.get(ImportPreviewSession, import_id)
        if record is None:
            return None
        state = _decode_preview_state(record.payload_json)
    if state is None or state.state != _STATE_COMMITTED or state.result is None:
        return None
    return ImportCommittedResult(
        import_id=import_id,
        claim_token=state.claim_token,
        committed_at=state.committed_at,
        password_artifact=state.password_artifact,
        imported_count=state.result['imported_count'],
        updated_count=state.result['updated_count'],
        skipped_count=state.result['skipped_count'],
        total_users=state.result['total_users'],
    )


def restore_import_preview(import_id: str, payload: dict, created_at: Optional[datetime] = None) -> bool:
    """Compatibility wrapper for the P1 restore API.

    The authoritative failure path is ``release_import_preview_claim`` (token
    CAS).  Without a claim token this wrapper can only prove ownership for the
    legacy "row missing" case, so a row that is still present (AVAILABLE or
    CLAIMED) is left untouched and False is returned.
    """
    if not import_id or payload is None:
        return False
    with Session(bind=db.engine) as state_session:
        if state_session.get(ImportPreviewSession, import_id) is not None:
            return False
        state_session.add(ImportPreviewSession(
            id=import_id,
            payload_json=_encode_available_state(payload),
            created_at=created_at or _now(),
        ))
        state_session.commit()
    return True


def consume_import_preview(import_id: str) -> Optional[dict]:
    """Compatibility alias with the historical "consume immediately" contract.

    claim → isolated physical DELETE (CAS on ``claimed_raw_json``).  This
    helper is not the artifact publication protocol and intentionally leaves
    no COMMITTED marker.  ``confirm_import`` must NOT use this shortcut; its
    finalize belongs to the business commit as CLAIMED → COMMITTED.
    """
    claim = claim_import_preview(import_id)
    if claim is None:
        return None
    with Session(bind=db.engine) as state_session:
        deleted = state_session.query(ImportPreviewSession).filter(
            ImportPreviewSession.id == claim.import_id,
            ImportPreviewSession.payload_json == claim.claimed_raw_json,
        ).delete(synchronize_session=False)
        state_session.commit()
    if deleted != 1:
        return None  # ownership was taken over between claim and consume
    return claim.payload


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
