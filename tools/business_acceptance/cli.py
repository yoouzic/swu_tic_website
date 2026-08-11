from __future__ import annotations

import argparse
import json
from itertools import chain
import os
from pathlib import Path
import secrets
from typing import Sequence

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
    batches = subparsers.add_parser('run-batches', help='run a fake-client acceptance batch phase')
    batches.add_argument('--phase', choices=('staged', 'main', 'retry', 'cache'), required=True)
    batches.add_argument('--manifest', type=Path, default=DEFAULT_RUNTIME_RELATIVE / 'manifest.json')
    batches.add_argument('--sample-size', type=int, default=50)
    batches.add_argument('--fake-client', action='store_true')
    human_flow = subparsers.add_parser(
        'run-human-flow',
        help='run the route-backed synthetic administrator and resubmission flow',
    )
    human_flow.add_argument('--manifest', type=Path, default=DEFAULT_RUNTIME_RELATIVE / 'manifest.json')
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
        if not args.fake_client:
            print(
                'RUN_BATCHES=BLOCKED reason=fake-client-required-before-external-gate',
                file=__import__('sys').stderr,
            )
            return 2
        try:
            manifest, _run_password = load_manifest_file(args.manifest)
            result = _run_fake_batch_phase(manifest, args.phase, args.sample_size)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'RUN_BATCHES=BLOCKED reason={exc}', file=__import__('sys').stderr)
            return 2
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
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
