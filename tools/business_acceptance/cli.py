from __future__ import annotations

import argparse
import csv
import json
from itertools import chain
import os
from pathlib import Path
import secrets
from typing import Mapping, Sequence

from .config import AcceptanceConfig, DEFAULT_RUNTIME_RELATIVE
from .batches import (
    AdapterResult,
    AssessmentCache,
    BatchForm,
    RunState,
    StageObservation,
    build_stages,
    choose_safe_concurrency,
    close_batch,
    partition_mode_form_ids,
    run_batch,
)
from .corpus import (
    CorpusResult,
    extract_evaluation_corpus,
    extract_school_schedule_structure,
    school_schedule_sha256,
)
from .generator import (
    DEFAULT_CORPUS,
    AcceptanceManifest,
    generate_manifest,
    write_acceptance_school_schedule,
    write_acceptance_workbooks,
)
from .seed import load_manifest_file, seed_acceptance_database
from .human_flow import HumanFlowError, run_route_backed_human_flow
from .report import write_report
from .real_runner import RealAcceptanceError, run_real_phase
from .verify import (
    REQUIRED_VERIFICATION_CHECKS,
    VerificationResult,
    verify_batch_closure,
    verify_batch_mode_isolation,
    verify_cache_reuse,
    verify_combined_sources,
    verify_evidence_classification_alignment,
    verify_export,
    verify_http_budget,
    verify_logical_form_counts,
    verify_org_counts,
    verify_pagination,
    verify_rejection_return,
    verify_statistics_alignment,
)


def _runtime_dirs(config: AcceptanceConfig) -> None:
    for relative in ('inputs', 'generated', 'results', 'logs', 'screenshots', 'report'):
        config.output_path(relative).mkdir(parents=True, exist_ok=True)


def _existing_password(manifest_path: Path) -> str | None:
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    value = payload.get('run_password')
    return value if isinstance(value, str) and value else None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp')
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
            newline='\n',
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class _FakeAcceptanceAdapter:
    def assess(self, form, *, mode):
        return AdapterResult(
            classification='clear',
            http_attempts=0 if mode == 'rules_only' else 1,
        )


def _run_fake_batch_phase(manifest: AcceptanceManifest, phase: str, sample_size: int = 50) -> dict[str, object]:
    """Exercise the budget/cache contracts without network or application state."""

    runtime = manifest.config.runtime_root
    if runtime is None:
        raise ValueError('manifest runtime_root is required')
    state_path = manifest.config.output_path('run-state.json')
    cache_path = manifest.config.output_path('results/fake-batch-cache.json')
    state = RunState.load(state_path) if state_path.exists() else RunState(
        http_attempt_ceiling=manifest.config.http_attempt_ceiling,
    )
    if state.http_attempt_ceiling != manifest.config.http_attempt_ceiling:
        raise ValueError('run-state request ceiling does not match manifest')
    if cache_path.exists():
        cache = AssessmentCache.from_dict(json.loads(cache_path.read_text(encoding='utf-8')))
    else:
        cache = AssessmentCache()
    adapter = _FakeAcceptanceAdapter()
    forms_by_mode = {
        mode: [
            BatchForm(form.synthetic_key, str(form.ordinal))
            for form in manifest.forms if form.review_mode == mode
        ]
        for mode in ('rules_only', 'llm_only', 'combined')
    }
    partition_mode_form_ids(
        [form.synthetic_key for form in manifest.forms],
        per_mode=manifest.config.per_mode,
    )

    if phase == 'staged':
        staged_forms = forms_by_mode['llm_only'][:]
        staged_forms.extend(forms_by_mode['combined'])
        stages = build_stages(
            [form.form_id for form in staged_forms[:manifest.config.staged_size_each * 3]],
            levels=manifest.config.staged_concurrency,
            size_each=manifest.config.staged_size_each,
        )
        observations = []
        stage_items = []
        for index, stage in enumerate(stages, start=1):
            selected = [form for form in staged_forms if form.form_id in set(stage.form_ids)]
            summaries = []
            for mode in ('llm_only', 'combined'):
                mode_forms = [form for form in selected if next(
                    item.review_mode for item in manifest.forms if item.synthetic_key == form.form_id
                ) == mode]
                if mode_forms:
                    summaries.append(run_batch(mode_forms, adapter, state, cache, mode=mode))
            stage_summary = close_batch(
                chain.from_iterable(summary.items for summary in summaries),
                target=len(selected),
            )
            stage_items.extend(stage_summary.items)
            observations.append(StageObservation(
                concurrency=stage.concurrency,
                target=len(selected),
                processed=stage_summary.processed,
                rate_limited_ratio=0.0,
                failed_ratio=stage_summary.failed / len(selected) if selected else 0.0,
                progress_updated=True,
            ))
            state.batch_ids[f'stage-{index}'] = f'fake-stage-{index}'
        state.safe_concurrency = choose_safe_concurrency(
            observations,
            fallback=manifest.config.staged_concurrency[0],
        )
        state.phase = 'staged'
        summary = close_batch(stage_items, target=60)
        result = {
            'phase': phase,
            'forms': summary.processed,
            'processed': summary.processed,
            'http_attempts': state.http_attempts,
            'concurrency': [stage.concurrency for stage in stages],
        }
    elif phase == 'main':
        summaries = [
            run_batch(forms_by_mode[mode], adapter, state, cache, mode=mode)
            for mode in ('rules_only', 'llm_only', 'combined')
        ]
        summary = close_batch(
            chain.from_iterable(item.items for item in summaries),
            target=len(manifest.forms),
        )
        state.batch_ids.update({mode: f'fake-{mode}' for mode in ('rules_only', 'llm_only', 'combined')})
        state.phase = 'main'
        result = {
            'phase': phase,
            'rules_only': len(forms_by_mode['rules_only']),
            'llm_only': len(forms_by_mode['llm_only']),
            'combined': len(forms_by_mode['combined']),
            'logical_llm_reviews': state.logical_llm_reviews,
            'http_attempts': state.http_attempts,
            'processed': summary.processed,
        }
    elif phase == 'cache':
        if not isinstance(sample_size, int) or sample_size < 1:
            raise ValueError('sample_size must be positive')
        sample = (forms_by_mode['llm_only'] + forms_by_mode['combined'])[:sample_size]
        before = state.http_attempts
        summary = close_batch(
            chain.from_iterable(
                run_batch(
                    [form], adapter, state, cache,
                    mode='llm_only' if form in forms_by_mode['llm_only'] else 'combined',
                ).items
                for form in sample
            ),
            target=len(sample),
        )
        state.phase = 'cache'
        result = {
            'phase': phase,
            'cache_hits': summary.cache_hits,
            'http_attempt_delta': state.http_attempts - before,
            'processed': summary.processed,
        }
    elif phase == 'retry':
        state.phase = 'retry'
        result = {'phase': phase, 'retried': 0, 'http_attempts': state.http_attempts}
    else:
        raise ValueError(f'unsupported fake batch phase: {phase}')

    state.save(state_path)
    _write_json_atomic(cache_path, cache.to_dict())
    _write_json_atomic(manifest.config.output_path(f'results/fake-batches-{phase}.json'), result)
    return result


def _write_oracle(path: Path, manifest: AcceptanceManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        for row in manifest.oracle_rows():
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def prepare_acceptance(
    runtime_root: str | Path,
    demo_dir: str | Path | None = None,
    school_schedule: str | Path | None = None,
    *,
    base_config: AcceptanceConfig | None = None,
) -> AcceptanceManifest:
    """Prepare one ignored runtime without printing source prose or secrets."""

    base = base_config or AcceptanceConfig()
    config = AcceptanceConfig(
        seed=base.seed,
        officer_count=base.officer_count,
        logical_form_count=base.logical_form_count,
        per_mode=base.per_mode,
        normal_per_mode=base.normal_per_mode,
        http_attempt_ceiling=base.http_attempt_ceiling,
        staged_concurrency=base.staged_concurrency,
        staged_size_each=base.staged_size_each,
        semester=base.semester,
        semester_monday=base.semester_monday,
        runtime_root=Path(runtime_root),
        demo_dir=Path(demo_dir) if demo_dir else base.demo_dir,
        school_schedule=Path(school_schedule) if school_schedule else base.school_schedule,
    )
    config.validate()
    _runtime_dirs(config)

    corpus_result: CorpusResult | None = None
    if config.demo_dir is not None:
        corpus_result = extract_evaluation_corpus(config.demo_dir)
    corpus = corpus_result.texts if corpus_result and corpus_result.texts else DEFAULT_CORPUS
    source_schedule_sha256 = ''
    school_structure = ()
    if config.school_schedule is not None:
        source_schedule_sha256 = school_schedule_sha256(config.school_schedule)
        school_structure = extract_school_schedule_structure(config.school_schedule)
        if not school_structure:
            raise ValueError('school schedule structure could not be extracted')
    manifest = generate_manifest(
        config,
        corpus,
        school_structure=school_structure,
        school_schedule_source_sha256=source_schedule_sha256,
    )
    write_acceptance_workbooks(manifest, config.output_path('generated'))
    if school_structure:
        write_acceptance_school_schedule(
            manifest,
            config.output_path('generated/school-schedule-acceptance.xlsx'),
        )

    manifest_path = config.output_path('manifest.json')
    run_password = _existing_password(manifest_path) or secrets.token_urlsafe(18)
    manifest_payload = manifest.to_dict()
    manifest_payload['run_password'] = run_password
    manifest_payload['corpus_stats'] = corpus_result.stats.to_dict() if corpus_result else {
        'source_files': 0,
        'candidate_cells': 0,
        'accepted_texts': 0,
        'sha256': manifest.corpus_sha256,
        'fallback_corpus': True,
    }
    _write_json(manifest_path, manifest_payload)
    _write_oracle(config.output_path('oracle.jsonl'), manifest)
    _write_json(config.output_path('run-state.json'), {
        'phase': 'prepared',
        'manifest_sha256': manifest.sha256,
        'officers': manifest.officer_count,
        'forms': manifest.logical_form_count,
        'batch_counts': manifest.batch_counts,
        'real_http_attempts': 0,
    })
    if corpus_result:
        _write_json(config.output_path('logs/corpus-stats.json'), corpus_result.stats.to_dict())
    else:
        _write_json(config.output_path('logs/corpus-stats.json'), manifest_payload['corpus_stats'])
    if config.school_schedule is not None:
        _write_json(config.output_path('logs/school-schedule-source.json'), {
            'sha256': source_schedule_sha256,
            'structure_count': len(school_structure),
            'raw_source_copied': False,
        })
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Synthetic SWU TIC business acceptance tooling')
    subparsers = parser.add_subparsers(dest='command', required=True)
    prepare = subparsers.add_parser('prepare', help='generate the isolated synthetic acceptance runtime')
    prepare.add_argument('--runtime-root', type=Path, default=DEFAULT_RUNTIME_RELATIVE)
    prepare.add_argument('--demo-dir', type=Path)
    prepare.add_argument('--school-schedule', type=Path)
    seed = subparsers.add_parser('seed', help='seed the isolated synthetic acceptance database')
    seed.add_argument('--manifest', type=Path, default=DEFAULT_RUNTIME_RELATIVE / 'manifest.json')
    batches = subparsers.add_parser('run-batches', help='run an explicitly selected acceptance batch phase')
    batches.add_argument('--phase', choices=('staged', 'main', 'retry', 'cache'), required=True)
    batches.add_argument('--manifest', type=Path, default=DEFAULT_RUNTIME_RELATIVE / 'manifest.json')
    batches.add_argument('--sample-size', type=int, default=50)
    batches.add_argument('--fake-client', action='store_true')
    batches.add_argument('--real-client', action='store_true')
    batches.add_argument('--stage-index', type=int)
    batches.add_argument('--worker-concurrency', type=int)
    batches.add_argument('--timeout-seconds', type=float, default=3600)
    batches.add_argument('--poll-seconds', type=float, default=2)
    human_flow = subparsers.add_parser(
        'run-human-flow',
        help='run the route-backed synthetic administrator and resubmission flow',
    )
    human_flow.add_argument('--manifest', type=Path, default=DEFAULT_RUNTIME_RELATIVE / 'manifest.json')
    verify = subparsers.add_parser('verify', help='verify persisted acceptance evidence')
    verify.add_argument('--phase', choices=('pre-external', 'post-automation', 'final'), required=True)
    verify.add_argument('--manifest', type=Path, default=DEFAULT_RUNTIME_RELATIVE / 'manifest.json')
    report = subparsers.add_parser('report', help='render the sanitized acceptance report')
    report.add_argument('--manifest', type=Path, default=DEFAULT_RUNTIME_RELATIVE / 'manifest.json')
    return parser


def _build_route_reassessment(manifest, requester_id):
    """Return a synchronous callback for a new form version's original mode."""

    def reassess(form_id, review_mode):
        mode = str(getattr(review_mode, 'value', review_mode))
        if mode != 'rules_only' and not os.environ.get('DEEPSEEK_API_KEY'):
            raise RuntimeError('DEEPSEEK_API_KEY_REQUIRED_FOR_HUMAN_REASSESSMENT')
        from app.review_automation.tasks.review import create_review_batch, run_review_batch_task

        batch = create_review_batch(
            [int(form_id)],
            requester_id=requester_id,
            force_refresh=False,
            review_mode=mode,
            transient_retries=0,
            semester=manifest.config.semester,
            semester_monday=manifest.config.semester_monday,
            enqueue=False,
        )
        return run_review_batch_task(str(batch.id))

    return reassess


def _load_runtime_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _blocked_result(check: str, code: str, message: str, **details: object) -> VerificationResult:
    return VerificationResult(
        check=check,
        code=code,
        passed=False,
        message=message,
        details=dict(details),
        blocked=True,
    )


def _artifact_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _artifact_or_blocked(
    path: Path,
    *,
    check: str,
    label: str,
) -> tuple[dict[str, object] | None, VerificationResult | None]:
    payload = _artifact_json(path)
    if payload is None:
        return None, _blocked_result(
            check,
            'EVIDENCE_MISSING',
            f'{label} acceptance artifact is missing or invalid',
            path=str(path),
        )
    return payload, None


def _append_artifact_check(
    results: list[VerificationResult],
    manifest,
    relative: str,
    *,
    check: str,
    label: str,
) -> dict[str, object] | None:
    payload, missing = _artifact_or_blocked(
        manifest.config.output_path(relative),
        check=check,
        label=label,
    )
    if missing is not None:
        results.append(missing)
    return payload


def _verification_for_manifest(manifest, phase: str) -> tuple[str, list[VerificationResult]]:
    if manifest.config.runtime_root is None:
        raise ValueError('manifest runtime_root is required')
    if phase not in {'pre-external', 'post-automation', 'final'}:
        raise ValueError(f'unsupported verification phase: {phase}')

    results: list[VerificationResult] = []
    runtime_manifest = _load_runtime_json(manifest.config.output_path('manifest.json'))
    seed = runtime_manifest.get('seed') if isinstance(runtime_manifest.get('seed'), dict) else None
    seed_counts = seed.get('counts') if isinstance(seed, dict) and isinstance(seed.get('counts'), dict) else None

    if phase == 'pre-external':
        results.append(verify_org_counts(1002, manifest.officer_count))
        results.append(verify_logical_form_counts(manifest.logical_form_count, manifest.forms))
    elif seed_counts is None:
        results.append(_blocked_result(
            'ORG_COUNTS',
            'EVIDENCE_MISSING',
            'seed metadata is required for post-automation and final organization counts',
        ))
        results.append(_blocked_result(
            'LOGICAL_FORM_COUNTS',
            'EVIDENCE_MISSING',
            'seed metadata is required for post-automation and final logical-form counts',
        ))
    else:
        results.append(verify_org_counts(
            1002,
            int(seed_counts.get('information_officers', 0) or 0),
            expected_administrators=10,
            actual_administrators=int(seed_counts.get('administrators', 0) or 0),
        ))
        results.append(verify_logical_form_counts(
            manifest.logical_form_count,
            int(seed_counts.get('logical_forms', 0) or 0),
        ))

    results.append(verify_batch_mode_isolation(
        manifest.forms,
        expected_counts=manifest.batch_counts,
    ))

    state, state_missing = _artifact_or_blocked(
        manifest.config.output_path('run-state.json'),
        check='RUN_STATE',
        label='run-state',
    )
    if state_missing is not None:
        results.append(state_missing)
        results.append(_blocked_result(
            'HTTP_BUDGET',
            'EVIDENCE_MISSING',
            'run-state is required to verify the HTTP budget',
        ))
    else:
        results.append(verify_http_budget(
            int(state.get('http_attempts', 0) or 0),
            manifest.config.http_attempt_ceiling,
        ))

    def require_fake(relative: str, check: str, label: str) -> dict[str, object] | None:
        return _append_artifact_check(
            results,
            manifest,
            relative,
            check=check,
            label=label,
        )

    def check_staged(staged: dict[str, object] | None) -> None:
        if staged is None:
            return
        count = int(staged.get('forms', staged.get('processed', 0)) or 0)
        if count != 60:
            results.append(VerificationResult(
                check='STAGED_MANIFEST',
                code='STAGED_COUNT_MISMATCH',
                passed=False,
                message='staged manifest does not contain 60 forms',
                details={'expected': 60, 'actual': count},
            ))
        else:
            results.append(VerificationResult(
                check='STAGED_MANIFEST',
                code='PASS',
                passed=True,
                details={'forms': count},
            ))
        expected_levels = list(manifest.config.staged_concurrency)
        actual_levels = staged.get('concurrency')
        if actual_levels != expected_levels:
            results.append(VerificationResult(
                check='STAGED_CONCURRENCY',
                code='STAGED_CONCURRENCY_MISMATCH',
                passed=False,
                message='staged concurrency levels differ from the fixed acceptance matrix',
                details={'expected': expected_levels, 'actual': actual_levels},
            ))
        else:
            results.append(VerificationResult(
                check='STAGED_CONCURRENCY',
                code='PASS',
                passed=True,
                details={'levels': expected_levels},
            ))

    staged = require_fake(
        'results/fake-batches-staged.json',
        'STAGED_MANIFEST',
        'staged fake-batch',
    )
    check_staged(staged)

    # The fake gate is deliberately closed over all three persisted fake
    # artifacts.  A staged-only result is not enough to authorize any later
    # external or real-data phase.
    main_batch = require_fake(
        'results/fake-batches-main.json',
        'FAKE_MAIN_ARTIFACT',
        'main fake-batch',
    )
    cache_batch = require_fake(
        'results/fake-batches-cache.json',
        'FAKE_CACHE_ARTIFACT',
        'cache fake-batch',
    )
    if main_batch is not None:
        results.append(verify_batch_closure({
            'target': manifest.logical_form_count,
            'processed': int(main_batch.get('processed', 0) or 0),
            'failed': int(main_batch.get('failed', 0) or 0),
            'cancelled': int(main_batch.get('cancelled', 0) or 0),
        }))
    if cache_batch is not None:
        results.append(verify_cache_reuse(
            int(cache_batch.get('cache_hits', 0) or 0),
            int(cache_batch.get('http_attempt_delta', 0) or 0),
        ))

    if phase == 'pre-external':
        real_attempts = None if state_missing is not None else int(state.get('real_http_attempts', 0) or 0)
        if real_attempts is None:
            results.append(_blocked_result(
                'REAL_HTTP_GATE',
                'EVIDENCE_MISSING',
                'run-state is required to prove zero real HTTP attempts',
            ))
        elif real_attempts != 0:
            results.append(VerificationResult(
                check='REAL_HTTP_GATE',
                code='HTTP_ATTEMPTS_NONZERO',
                passed=False,
                message='real HTTP attempts exist before the external gate',
                details={'real_http_attempts': real_attempts},
            ))
        else:
            results.append(VerificationResult(
                check='REAL_HTTP_GATE',
                code='PASS',
                passed=True,
                details={'real_http_attempts': 0},
            ))
        return phase.upper().replace('-', '_'), results

    # Post-automation and final gates both require the explicit retry artifact;
    # this prevents a main-batch success from hiding an unverified retry phase.
    require_fake(
        'results/fake-batches-retry.json',
        'FAKE_RETRY_ARTIFACT',
        'retry fake-batch',
    )

    if phase == 'post-automation':
        return phase.upper().replace('-', '_'), results

    human_flow = require_fake(
        'results/human-flow.json',
        'HUMAN_FLOW',
        'human-flow',
    )
    if human_flow is not None:
        first_stage = human_flow.get('first_stage')
        final = human_flow.get('final')
        if isinstance(first_stage, dict) and isinstance(final, dict):
            planned = int(first_stage.get('planned', 0) or 0)
            final_actions = int(final.get('center_actions', 0) or 0) + int(final.get('super_actions', 0) or 0)
            if human_flow.get('status') != 'PASS' or planned != manifest.logical_form_count or final_actions != manifest.logical_form_count:
                results.append(VerificationResult(
                    check='STATUS_TRANSITIONS',
                    code='STATUS_TRANSITION_INVALID',
                    passed=False,
                    message='human-flow action totals do not close over logical forms',
                    details={'first_stage': planned, 'final': final_actions},
                ))
            else:
                results.append(VerificationResult(
                    check='STATUS_TRANSITIONS',
                    code='PASS',
                    passed=True,
                    details={'first_stage': planned, 'final': final_actions},
                ))
        else:
            results.append(_blocked_result(
                'STATUS_TRANSITIONS',
                'EVIDENCE_MISSING',
                'human-flow status transition totals are missing',
            ))

        version_chains = human_flow.get('version_chains')
        if isinstance(version_chains, dict) and int(version_chains.get('logical_forms', 0) or 0) == manifest.logical_form_count:
            results.append(VerificationResult(
                check='VERSION_HISTORY',
                code='PASS',
                passed=True,
                details={
                    'logical_forms': int(version_chains.get('logical_forms', 0) or 0),
                    'with_multiple_versions': int(version_chains.get('with_multiple_versions', 0) or 0),
                },
            ))
        else:
            results.append(_blocked_result(
                'VERSION_HISTORY',
                'EVIDENCE_MISSING',
                'human-flow version-chain totals are missing or incomplete',
            ))

        rejection_return = human_flow.get('rejection_return')
        if isinstance(rejection_return, dict):
            results.append(verify_rejection_return(rejection_return))
        else:
            results.append(_blocked_result(
                'REJECTION_RETURN',
                'EVIDENCE_MISSING',
                'reject-response unique-ID evidence is missing',
            ))

        protected = human_flow.get('automation_protected_fields')
        if isinstance(protected, dict) and int(protected.get('checked', 0) or 0) > 0:
            if int(protected.get('changed', 0) or 0) == 0:
                results.append(VerificationResult(
                    check='AUTOMATION_HUMAN_FIELD_IMMUTABILITY',
                    code='PASS',
                    passed=True,
                    details={'checked': int(protected.get('checked', 0) or 0)},
                ))
            else:
                results.append(VerificationResult(
                    check='AUTOMATION_HUMAN_FIELD_IMMUTABILITY',
                    code='AUTOMATION_CHANGED_HUMAN_FIELD',
                    passed=False,
                    message='automation changed a protected human-review field',
                    details={'changed': int(protected.get('changed', 0) or 0)},
                ))
        else:
            results.append(_blocked_result(
                'AUTOMATION_HUMAN_FIELD_IMMUTABILITY',
                'EVIDENCE_MISSING',
                'protected-field snapshots are missing',
            ))

        scope_payload = human_flow.get('administrator_scopes')
        for role, check in (
            ('group', 'GROUP_SCOPE'),
            ('department', 'DEPARTMENT_SCOPE'),
            ('center', 'CENTER_SCOPE'),
            ('super', 'SUPERADMIN_SCOPE'),
        ):
            entries = scope_payload.get(role) if isinstance(scope_payload, dict) else None
            if not isinstance(entries, list) or not entries:
                results.append(_blocked_result(
                    check,
                    'EVIDENCE_MISSING',
                    f'{role} administrator scope evidence is missing',
                ))
            elif all(isinstance(entry, dict) and entry.get('passed') is True for entry in entries):
                results.append(VerificationResult(
                    check=check,
                    code='PASS',
                    passed=True,
                    details={'administrators': len(entries)},
                ))
            else:
                results.append(VerificationResult(
                    check=check,
                    code='REVIEW_SCOPE_MISMATCH',
                    passed=False,
                    message=f'{role} administrator scope contains an out-of-scope form',
                ))

    # Pagination, export, statistics and evidence checks are independent
    # artifacts.  Their absence is a blocked gate, not an empty pass.
    pagination = require_fake(
        'results/pagination.json',
        'PAGINATION_UNIQUENESS',
        'pagination',
    )
    if pagination is not None:
        pages = pagination.get('pages')
        if isinstance(pages, list):
            results.append(verify_pagination(pages))
        else:
            results.append(_blocked_result(
                'PAGINATION_UNIQUENESS',
                'EVIDENCE_MISSING',
                'pagination artifact does not contain page records',
            ))

    export_path = manifest.config.output_path('results/forms.csv')
    export_rows: list[dict[str, str]] | None = None
    if not export_path.exists():
        results.append(_blocked_result(
            'EXPORT_ALIGNMENT',
            'EVIDENCE_MISSING',
            'forms export artifact is missing',
            path=str(export_path),
        ))
    else:
        try:
            with export_path.open('r', encoding='utf-8', newline='') as handle:
                export_rows = list(csv.DictReader(handle))
            results.append(verify_export(manifest.logical_form_count, export_rows))
        except (OSError, UnicodeError, csv.Error) as exc:
            results.append(VerificationResult(
                check='EXPORT_ALIGNMENT',
                code='EXPORT_EVIDENCE_INVALID',
                passed=False,
                message='forms export could not be parsed',
                details={'error_type': type(exc).__name__},
            ))

    statistics = _artifact_json(manifest.config.output_path('results/batches.json'))
    if statistics is None:
        results.append(_blocked_result(
            'STATISTICS_ALIGNMENT',
            'EVIDENCE_MISSING',
            'statistics artifact is missing or invalid',
        ))
    elif isinstance(statistics.get('expected'), dict) and isinstance(statistics.get('actual'), dict):
        results.append(verify_statistics_alignment(statistics['expected'], statistics['actual']))
    else:
        results.append(_blocked_result(
            'STATISTICS_ALIGNMENT',
            'EVIDENCE_MISSING',
            'statistics artifact lacks independently comparable expected and actual counts',
        ))

    deepseek = _artifact_json(manifest.config.output_path('results/deepseek-summary.json'))
    if deepseek is None:
        results.append(_blocked_result(
            'EVIDENCE_CLASSIFICATION_ALIGNMENT',
            'EVIDENCE_MISSING',
            'DeepSeek classification summary is missing',
        ))
        results.append(_blocked_result(
            'COMBINED_SOURCE_PRESERVATION',
            'EVIDENCE_MISSING',
            'combined-source evidence is missing',
        ))
    else:
        evidence_rows = deepseek.get('classification_rows')
        if not isinstance(evidence_rows, list) and export_rows is not None:
            evidence_rows = export_rows
        if isinstance(evidence_rows, list) and evidence_rows and all(
            isinstance(row, dict)
            and ('expected_category' in row or 'oracle_category' in row)
            and ('category' in row or 'classification' in row)
            for row in evidence_rows
        ):
            results.append(verify_evidence_classification_alignment(evidence_rows))
        else:
            results.append(_blocked_result(
                'EVIDENCE_CLASSIFICATION_ALIGNMENT',
                'EVIDENCE_MISSING',
                'classification rows do not contain oracle and actual categories',
            ))
        combined = deepseek.get('combined_sources')
        if isinstance(combined, dict):
            results.append(verify_combined_sources(combined))
        else:
            results.append(_blocked_result(
                'COMBINED_SOURCE_PRESERVATION',
                'EVIDENCE_MISSING',
                'combined-source summary is missing',
            ))

    present = {result.check for result in results}
    for check in REQUIRED_VERIFICATION_CHECKS:
        if check not in present:
            results.append(_blocked_result(
                check,
                'EVIDENCE_MISSING',
                f'{check} has not been executed or persisted',
            ))
    return phase.upper().replace('-', '_'), results


def _report_payload(manifest, verification: list[VerificationResult]) -> dict[str, object]:
    if any(result.blocked for result in verification):
        conclusion = 'BLOCKED'
    elif any(not result.passed for result in verification):
        conclusion = 'FAIL'
    else:
        conclusion = 'PASS'
    runtime = manifest.config.runtime_root
    state = _load_runtime_json(manifest.config.output_path('run-state.json'))
    human_flow = _load_runtime_json(manifest.config.output_path('results/human-flow.json'))
    staged = _load_runtime_json(manifest.config.output_path('results/fake-batches-staged.json'))
    main_batch = _load_runtime_json(manifest.config.output_path('results/fake-batches-main.json'))
    cache_batch = _load_runtime_json(manifest.config.output_path('results/fake-batches-cache.json'))
    # Keep only numerical/structural fields from runtime evidence.  In
    # particular, do not copy model prompts, responses, credentials or user
    # identifiers into the report payload.
    human_final = human_flow.get('final') if isinstance(human_flow.get('final'), dict) else {}
    human_first = human_flow.get('first_stage') if isinstance(human_flow.get('first_stage'), dict) else {}
    human_versions = human_flow.get('version_chains') if isinstance(human_flow.get('version_chains'), dict) else {}
    administrator_scopes = human_flow.get('administrator_scopes')
    if not isinstance(administrator_scopes, dict):
        administrator_scopes = {
            role: {
                'conclusion': 'BLOCKED',
                'evidence': 'missing',
            }
            for role in ('group', 'department', 'center', 'super')
        }
    export_path = manifest.config.output_path('results/forms.csv')
    statistics_path = manifest.config.output_path('results/batches.json')
    return {
        'conclusion': conclusion,
        'scope': {
            'synthetic_only': True,
            'remote_push': False,
            'default_business_database_touched': False,
        },
        'environment': {
            'runtime_root': str(runtime) if runtime else None,
            'real_http_attempts': int(state.get('real_http_attempts', 0) or 0),
            'request_ceiling': manifest.config.http_attempt_ceiling,
            'redis': 'not recorded in Task 8 verifier',
        },
        'dataset': {
            'officers': manifest.officer_count,
            'logical_forms': manifest.logical_form_count,
            'batch_counts': manifest.batch_counts,
        },
        'batches': {
            'staged': {
                'forms': int(staged.get('forms', staged.get('processed', 0)) or 0),
                'concurrency': staged.get('concurrency', []),
            },
            'main': {
                'processed': int(main_batch.get('processed', 0) or 0),
                'rules_only': int(main_batch.get('rules_only', 0) or 0),
                'llm_only': int(main_batch.get('llm_only', 0) or 0),
                'combined': int(main_batch.get('combined', 0) or 0),
            },
            'cache': {
                'cache_hits': int(cache_batch.get('cache_hits', 0) or 0),
                'http_attempt_delta': int(cache_batch.get('http_attempt_delta', 0) or 0),
            },
        },
        'concurrency': {
            'stages': list(manifest.config.staged_concurrency),
            'selected': state.get('safe_concurrency'),
        },
        'deepseek_summary': {
            'logical_llm_reviews': int(state.get('logical_llm_reviews', 0) or 0),
            'http_attempts': int(state.get('http_attempts', 0) or 0),
            'external_summary_artifact_present': manifest.config.output_path('results/deepseek-summary.json').exists(),
        },
        'administrator_scopes': administrator_scopes,
        'resubmission_version_chains': {
            'first_stage': {
                'planned': int(human_first.get('planned', 0) or 0),
                'rejected': int(human_first.get('rejected', 0) or 0),
                'resubmitted': int(human_first.get('resubmitted', 0) or 0),
                'reassessed': int(human_first.get('reassessed', 0) or 0),
            },
            'final': {
                'rejected': int(human_final.get('rejected', 0) or 0),
                'repaired': int(human_final.get('repaired', 0) or 0),
                'left_rejected': int(human_final.get('left_rejected', 0) or 0),
            },
            'version_chains': {
                'logical_forms': int(human_versions.get('logical_forms', 0) or 0),
                'with_multiple_versions': int(human_versions.get('with_multiple_versions', 0) or 0),
                'max_versions': int(human_versions.get('max_versions', 0) or 0),
            },
        },
        'performance': {
            'http_attempts': int(state.get('http_attempts', 0) or 0),
            'safe_concurrency': state.get('safe_concurrency'),
        },
        'export': {
            'path': str(export_path),
            'present': export_path.exists(),
        },
        'statistics': {
            'path': str(statistics_path),
            'present': statistics_path.exists(),
        },
        'verification': [
            result.to_dict()
            for result in verification
        ],
        'defects': [
            {
                'check': result.check,
                'code': result.code,
                'conclusion': result.conclusion,
                'message': result.message,
            }
            for result in verification
            if not result.passed
        ],
    }


def _real_phase_conclusion(result: Mapping[str, object]) -> str:
    """Map a production phase result to the acceptance conclusion vocabulary."""

    raw_status = result.get('status')
    if not raw_status:
        stages = result.get('stages')
        if isinstance(stages, (list, tuple)):
            for stage in reversed(stages):
                if isinstance(stage, Mapping) and stage.get('status'):
                    raw_status = stage.get('status')
                    break
    status = str(raw_status or '').strip().lower()
    if status == 'completed':
        return 'PASS'
    if status in {'failed', 'completed_with_errors'}:
        return 'FAIL'
    return 'BLOCKED'


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == 'prepare':
        try:
            manifest = prepare_acceptance(
                args.runtime_root,
                demo_dir=args.demo_dir,
                school_schedule=args.school_schedule,
            )
        except (OSError, ValueError) as exc:
            print(f'PREPARE=BLOCKED reason={exc}', file=__import__('sys').stderr)
            return 2
        print(
            'PREPARE=PASS '
            f'officers={manifest.officer_count} '
            f'forms={manifest.logical_form_count} '
            f'rules_only={manifest.batch_counts["rules_only"]} '
            f'llm_only={manifest.batch_counts["llm_only"]} '
            f'combined={manifest.batch_counts["combined"]} '
            f'normal={manifest.normal_control_count} '
            f'violation_intended={manifest.violation_intended_count}'
        )
        return 0
    if args.command == 'seed':
        # Refuse to import the Flask application until the caller has selected
        # an explicit SQLite path.  This keeps a failed acceptance invocation
        # from creating or opening the default business database.
        if os.environ.get('ACCEPTANCE_RUN') != '1':
            print('SEED=BLOCKED reason=ACCEPTANCE_RUN=1 is required', file=__import__('sys').stderr)
            return 2
        if not os.environ.get('SQLITE_DB_PATH'):
            print('SEED=BLOCKED reason=SQLITE_DB_PATH must explicitly select acceptance database', file=__import__('sys').stderr)
            return 2
        try:
            manifest, run_password = load_manifest_file(args.manifest)
            os.environ.setdefault(
                'AUTOMATION_UPLOAD_DIR',
                str(manifest.config.output_path('uploads')),
            )
            from app.app import app

            with app.app_context():
                result = seed_acceptance_database(
                    app,
                    manifest,
                    password=run_password,
                )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'SEED=BLOCKED reason={exc}', file=__import__('sys').stderr)
            return 2
        print(
            'SEED=PASS '
            f'officers={result.information_officers} '
            f'admins={result.administrators} '
            f'departments={result.departments} '
            f'groups={result.groups} '
            f'logical_forms={result.logical_forms} '
            f'physical_rows={result.physical_form_rows} '
            f'active_datasets={result.active_datasets}'
        )
        return 0
    if args.command == 'run-batches':
        if args.fake_client and args.real_client:
            print(
                'RUN_BATCHES=BLOCKED reason=fake-and-real-client-are-mutually-exclusive',
                file=__import__('sys').stderr,
            )
            return 2
        if not args.fake_client and not args.real_client:
            print(
                'RUN_BATCHES=BLOCKED reason=explicit-fake-or-real-client-required',
                file=__import__('sys').stderr,
            )
            return 2
        try:
            manifest, _run_password = load_manifest_file(args.manifest)
            if args.fake_client:
                result = _run_fake_batch_phase(manifest, args.phase, args.sample_size)
            else:
                result = run_real_phase(
                    manifest,
                    args.phase,
                    stage_index=args.stage_index,
                    worker_concurrency=args.worker_concurrency,
                    sample_size=args.sample_size,
                    timeout_seconds=args.timeout_seconds,
                    poll_seconds=args.poll_seconds,
                )
        except (OSError, ValueError, RuntimeError, RealAcceptanceError) as exc:
            print(f'RUN_BATCHES=BLOCKED reason={exc}', file=__import__('sys').stderr)
            return 2
        if args.real_client:
            conclusion = _real_phase_conclusion(result)
            if args.phase == 'staged':
                print(
                    f'REAL_STAGED={conclusion} '
                    f"stage={result.get('stage_index', args.stage_index)} "
                    f"forms={result.get('target', result.get('forms', 0))} "
                    f"processed={result.get('processed', 0)} "
                    f"http_attempts={result.get('http_attempts', 0)}"
                )
            elif args.phase == 'main':
                print(
                    f'REAL_MAIN={conclusion} '
                    f"rules_only={result.get('cumulative_mode_counts', {}).get('rules_only', 0)} "
                    f"llm_only={result.get('cumulative_mode_counts', {}).get('llm_only', 0)} "
                    f"combined={result.get('cumulative_mode_counts', {}).get('combined', 0)} "
                    f"logical_llm={result.get('logical_llm_reviews', 0)} "
                    f"http_attempts={result.get('http_attempts', 0)} "
                    f"uncertain_http_attempts={result.get('uncertain_http_attempts', 0)} "
                    f"budget_http_attempts={result.get('budget_http_attempts', result.get('http_attempts', 0))}"
                )
            elif args.phase == 'cache':
                print(
                    f'REAL_CACHE={conclusion} '
                    f"cache_hits={result.get('cache_hits', 0)} "
                    f"http_attempt_delta={result.get('http_attempt_delta', 0)}"
                )
            else:
                print(
                    f'REAL_RETRY={conclusion} '
                    f"retried={result.get('retried', 0)} "
                    f"http_attempts={result.get('http_attempts', 0)}"
                )
            return 0 if conclusion == 'PASS' else 2
        if args.phase == 'staged':
            print(
                'FAKE_STAGED=PASS '
                f'forms={result["forms"]} '
                f'concurrency={",".join(str(value) for value in result["concurrency"])} '
                f'processed={result["processed"]} '
                f'http_attempts={result["http_attempts"]}'
            )
        elif args.phase == 'main':
            print(
                'FAKE_MAIN=PASS '
                f'rules_only={result["rules_only"]} '
                f'llm_only={result["llm_only"]} '
                f'combined={result["combined"]} '
                f'logical_llm={result["logical_llm_reviews"]} '
                f'http_attempts={result["http_attempts"]}'
            )
        elif args.phase == 'cache':
            print(
                'FAKE_CACHE=PASS '
                f'cache_hits={result["cache_hits"]} '
                f'http_attempt_delta={result["http_attempt_delta"]}'
            )
        else:
            print(
                'FAKE_RETRY=PASS '
                f'retried={result["retried"]} '
                f'http_attempts={result["http_attempts"]}'
            )
        return 0
    if args.command == 'run-human-flow':
        if os.environ.get('ACCEPTANCE_RUN') != '1':
            print(
                'HUMAN_FLOW=BLOCKED reason=ACCEPTANCE_RUN=1 is required',
                file=__import__('sys').stderr,
            )
            return 2
        if not os.environ.get('SQLITE_DB_PATH'):
            print(
                'HUMAN_FLOW=BLOCKED reason=SQLITE_DB_PATH must explicitly select acceptance database',
                file=__import__('sys').stderr,
            )
            return 2
        try:
            manifest, run_password = load_manifest_file(args.manifest)
            os.environ.setdefault(
                'AUTOMATION_UPLOAD_DIR',
                str(manifest.config.output_path('uploads')),
            )
            from app.app import app
            from app.models import User

            with app.app_context():
                super_admin = User.query.filter_by(number='YAS01').first()
                if super_admin is None:
                    raise HumanFlowError('acceptance super administrator is missing')
                result = run_route_backed_human_flow(
                    app,
                    manifest,
                    run_password,
                    reassess=_build_route_reassessment(manifest, super_admin.id),
                )
            _write_json_atomic(manifest.config.output_path('results/human-flow.json'), result)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'HUMAN_FLOW=FAIL reason={exc}', file=__import__('sys').stderr)
            return 2
        print(
            'HUMAN_FLOW=PASS '
            f"group_logins={result['logins']['group']} "
            f"department_logins={result['logins']['department']} "
            f"center_logins={result['logins']['center']} "
            f"super_logins={result['logins']['super']} "
            f"first_stage={result['first_stage']['planned']} "
            f"first_stage_rejected={result['first_stage']['rejected']} "
            f"final_rejected={result['final']['rejected']} "
            f"final_left_rejected={result['final']['left_rejected']}"
        )
        return 0
    if args.command == 'verify':
        try:
            manifest, _run_password = load_manifest_file(args.manifest)
            phase, results = _verification_for_manifest(manifest, args.phase)
            payload = {
                'phase': args.phase,
                'conclusion': (
                    'BLOCKED' if any(item.blocked for item in results)
                    else 'PASS' if all(item.passed for item in results)
                    else 'FAIL'
                ),
                'checks': [item.to_dict() for item in results],
            }
            _write_json_atomic(manifest.config.output_path(f'results/verification-{args.phase}.json'), payload)
            if args.phase == 'final':
                _write_json_atomic(manifest.config.output_path('results/verification.json'), payload)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'VERIFY=BLOCKED reason={exc}', file=__import__('sys').stderr)
            return 2
        if args.phase == 'pre-external' and all(item.passed for item in results):
            print(
                'PRE_EXTERNAL_GATE=PASS '
                f'real_http_attempts=0 officers={manifest.officer_count} '
                f'forms={manifest.logical_form_count} staged_manifest=60 '
                f'request_ceiling={manifest.config.http_attempt_ceiling}'
            )
        else:
            gate_conclusion = (
                'BLOCKED' if any(item.blocked for item in results)
                else 'PASS' if all(item.passed for item in results)
                else 'FAIL'
            )
            print(
                f'VERIFY={gate_conclusion} '
                f'phase={args.phase} checks={len(results)}'
            )
        return 0 if all(item.passed for item in results) else 2
    if args.command == 'report':
        try:
            manifest, _run_password = load_manifest_file(args.manifest)
            _phase, verification = _verification_for_manifest(manifest, 'final')
            output = manifest.config.output_path('report/business-flow-acceptance.md')
            report_payload = _report_payload(manifest, verification)
            write_report(output, report_payload)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'REPORT=BLOCKED reason={exc}', file=__import__('sys').stderr)
            return 2
        print(f"REPORT={report_payload['conclusion']} path={output}")
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
