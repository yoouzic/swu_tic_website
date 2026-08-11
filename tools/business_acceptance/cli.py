from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
from typing import Sequence

from .config import AcceptanceConfig, DEFAULT_RUNTIME_RELATIVE
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
    return parser


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
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
