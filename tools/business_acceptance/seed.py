"""Seed the isolated synthetic business-acceptance database.

This module deliberately has a narrow boundary: it only accepts the generated
acceptance manifest and only writes below the acceptance instance/runtime
roots.  It never touches the default business database and never prints
credentials or source identities.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Mapping

from flask import Flask
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from app.models import (
    Department,
    Group,
    LectureForm,
    Permission,
    RolePermission,
    User,
    db,
)
from app.review_automation.contracts import DatasetStatus, ScheduleCoverage
from app.review_automation.models import ScheduleDataset
from app.review_automation.schedules.importer import (
    CLASS_MAPPING_KIND,
    PERSONAL_KIND,
    SCHOOL_KIND,
    preview_dataset,
)
from app.review_automation.schedules.repository import (
    activate_dataset,
    get_listener_schedule,
)

from .generator import (
    DEPARTMENTS,
    AcceptanceManifest,
    FormSpec,
    OfficerSpec,
)
from .config import AcceptanceConfig


ACCEPTANCE_RUN_ENV = 'ACCEPTANCE_RUN'
ACCEPTANCE_RUN_VALUE = '1'
ACCEPTANCE_INSTANCE_RELATIVE = Path('data/instance/acceptance-2026-08-11')
ACCEPTANCE_STORAGE_RELATIVE = Path('data/storage/acceptance-2026-08-11')

ROLE_OFFICER = '\u4fe1\u606f\u5458'
ROLE_ADMIN = '\u7ba1\u7406\u5458'
ROLE_SUPER = '\u8d85\u7ea7\u7ba1\u7406\u5458'
STATUS_PENDING = '\u5f85\u5ba1\u6838'
PERMISSION_GROUP = '\u5ba1\u8868_\u5c0f\u7ec4'
PERMISSION_DEPARTMENT = '\u5ba1\u8868_\u90e8\u95e8'
PERMISSION_CENTER = '\u5ba1\u8868_\u4e2d\u5fc3'
SPECIAL_ROLE_PREFIX = '\u7279\u6b8a\u89d2\u8272_'


@dataclass(frozen=True)
class SeedResult:
    information_officers: int
    administrators: int
    departments: int
    groups: int
    logical_forms: int
    physical_form_rows: int
    active_datasets: int
    permission_by_admin: dict[int, str]
    admin_user_ids: dict[str, list[int]]
    officer_user_ids: list[int]
    dataset_ids: dict[str, str]
    coverage_counts: dict[str, int]


def load_manifest_file(path: str | Path) -> tuple[AcceptanceManifest, str]:
    """Load the generated manifest and its private run password in memory."""

    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding='utf-8'))
    password = payload.get('run_password')
    if not isinstance(password, str) or not password:
        raise ValueError('manifest is missing the acceptance run password')

    config_values = payload.get('config')
    if not isinstance(config_values, Mapping):
        raise ValueError('manifest config is invalid')
    config = AcceptanceConfig.from_mapping(config_values)
    officers = tuple(
        OfficerSpec(**dict(item))
        for item in payload.get('officers', ())
        if isinstance(item, Mapping)
    )
    forms = []
    for item in payload.get('forms', ()):
        if not isinstance(item, Mapping):
            continue
        values = dict(item)
        values['oracle_markers'] = tuple(values.get('oracle_markers', ()))
        forms.append(FormSpec(**values))
    if not officers or not forms:
        raise ValueError('manifest has no generated officers or forms')
    manifest = AcceptanceManifest(
        config=config,
        officers=officers,
        forms=tuple(forms),
        corpus_sha256=str(payload.get('corpus_sha256', '')),
        sha256=str(payload.get('sha256', '')),
        school_schedule_source_sha256=str(payload.get('school_schedule_source_sha256', '')),
    )
    return manifest, password


def _repo_root(flask_app: Flask) -> Path:
    return Path(flask_app.root_path).resolve().parent


def _under(path: Path, root: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    root = root.expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f'{label} is outside the isolated acceptance root') from exc
    return path


def _guard_environment(flask_app: Flask, manifest: AcceptanceManifest) -> Path:
    if os.environ.get(ACCEPTANCE_RUN_ENV) != ACCEPTANCE_RUN_VALUE:
        raise RuntimeError('ACCEPTANCE_RUN=1 is required')

    repo = _repo_root(flask_app)
    instance_root = _under(repo / ACCEPTANCE_INSTANCE_RELATIVE, repo, 'acceptance instance root')
    runtime_root = manifest.config.runtime_root
    if runtime_root is None:
        raise RuntimeError('manifest runtime_root is required')
    runtime_root = _under(runtime_root, repo / ACCEPTANCE_STORAGE_RELATIVE, 'acceptance runtime root')

    engine = db.engine
    if engine.url.get_backend_name() != 'sqlite':
        raise RuntimeError('acceptance seed requires an isolated SQLite database')
    database = engine.url.database
    if not database:
        raise RuntimeError('acceptance database path is required')
    database_path = _under(Path(str(database)), instance_root, 'database path')
    if database_path == (repo / 'data/instance/lecture_forms.db').resolve():
        raise RuntimeError('default business database is not allowed')

    upload_dir = Path(flask_app.config.get('AUTOMATION_UPLOAD_DIR') or runtime_root / 'uploads')
    upload_dir = _under(upload_dir, runtime_root, 'automation upload path')
    upload_dir.mkdir(parents=True, exist_ok=True)
    flask_app.config['AUTOMATION_UPLOAD_DIR'] = str(upload_dir)
    return runtime_root


def _enable_sqlite_guards() -> None:
    journal_mode = str(db.session.execute(text('PRAGMA journal_mode=WAL')).scalar() or '').lower()
    db.session.execute(text('PRAGMA busy_timeout=30000'))
    db.session.commit()
    timeout = int(db.session.execute(text('PRAGMA busy_timeout')).scalar() or 0)
    if journal_mode != 'wal' or timeout != 30000:
        raise RuntimeError('SQLite WAL/busy_timeout guard could not be enabled')


def _group_plan(manifest: AcceptanceManifest) -> dict[tuple[str, str], Group]:
    departments = list(dict.fromkeys(officer.department for officer in manifest.officers))
    if len(departments) != 4 or set(departments) != set(DEPARTMENTS):
        raise ValueError('manifest must close over four acceptance departments')
    pairs = {(officer.department, officer.group) for officer in manifest.officers}
    if len(pairs) != 8:
        raise ValueError('manifest must close over eight acceptance groups')

    result: dict[tuple[str, str], Group] = {}
    for department in departments:
        groups = [group for dept, group in pairs if dept == department]
        if len(groups) != 2:
            raise ValueError('each acceptance department must have two groups')
        for group_name in sorted(groups):
            group = Group(
                name=group_name,
                department=department,
                description='synthetic business acceptance group',
                max_members=1000,
            )
            db.session.add(group)
            result[(department, group_name)] = group
    db.session.flush()
    return result


def _admin_plan(manifest: AcceptanceManifest, groups: Mapping[tuple[str, str], Group]):
    departments = list(dict.fromkeys(officer.department for officer in manifest.officers))
    first_group = {}
    for department in departments:
        first_group[department] = sorted(
            group for dept, group in groups if dept == department
        )[0]
    plan = []
    for index, department in enumerate(departments, start=1):
        group_name = first_group[department]
        plan.append(('group', f'YAG{index:02d}', ROLE_ADMIN, department, group_name, PERMISSION_GROUP))
    for index, department in enumerate(departments, start=1):
        group_name = first_group[department]
        plan.append(('department', f'YAD{index:02d}', ROLE_ADMIN, department, group_name, PERMISSION_DEPARTMENT))
    anchor = departments[0]
    plan.append(('center', 'YAC01', ROLE_ADMIN, anchor, first_group[anchor], PERMISSION_CENTER))
    plan.append(('super', 'YAS01', ROLE_SUPER, anchor, first_group[anchor], PERMISSION_CENTER))
    return tuple(plan)


def _expected_numbers(manifest: AcceptanceManifest):
    officer_numbers = tuple(officer.officer_id for officer in manifest.officers)
    admin_numbers = ('YAG01', 'YAG02', 'YAG03', 'YAG04', 'YAD01', 'YAD02', 'YAD03', 'YAD04', 'YAC01', 'YAS01')
    return officer_numbers, admin_numbers


def _acceptance_permission_objects() -> dict[str, Permission]:
    names = {
        PERMISSION_GROUP: 'synthetic group review permission',
        PERMISSION_DEPARTMENT: 'synthetic department review permission',
        PERMISSION_CENTER: 'synthetic center review permission',
    }
    permissions = {}
    for name, description in names.items():
        permission = Permission.query.filter_by(name=name).one_or_none()
        if permission is None:
            permission = Permission(name=name, description=description)
            db.session.add(permission)
        permissions[name] = permission
    db.session.flush()
    return permissions


def _add_permission(admin: User, permission: Permission) -> None:
    role = f'{SPECIAL_ROLE_PREFIX}{admin.id}'
    if RolePermission.query.filter_by(role=role, permission_id=permission.id).one_or_none() is None:
        db.session.add(RolePermission(role=role, permission_id=permission.id))


def _new_user(
    *,
    number: str,
    student_id: str,
    name: str,
    department: str,
    group: str,
    group_id: int | None,
    role: str,
    password_hash: str,
    index: int,
) -> User:
    return User(
        number=number,
        department=department,
        name=name,
        gender='-',
        grade='acceptance',
        college=department,
        major='synthetic acceptance',
        dormitory='acceptance-runtime',
        phone=f'199{index:08d}',
        qq=f'acceptance-{number}',
        student_id=student_id,
        password_hash=password_hash,
        role=role,
        group=group,
        group_id=group_id,
        is_active=True,
    )


def _seed_users(
    manifest: AcceptanceManifest,
    password: str,
) -> tuple[dict[str, User], dict[str, list[User]], dict[tuple[str, str], Group], dict[str, Department]]:
    if len(manifest.officers) != 1002 or len(manifest.forms) != 1500:
        raise ValueError('acceptance seed requires 1002 officers and 1500 forms')
    group_objects = _group_plan(manifest)
    departments = {}
    for department_name in DEPARTMENTS:
        department = Department(
            name=department_name,
            description='synthetic business acceptance department',
        )
        db.session.add(department)
        departments[department_name] = department
    db.session.flush()

    password_hash = generate_password_hash(password)
    officer_users: dict[str, User] = {}
    for index, officer in enumerate(manifest.officers, start=1):
        group = group_objects[(officer.department, officer.group)]
        user = _new_user(
            number=officer.officer_id,
            student_id=officer.student_id,
            name=officer.name,
            department=officer.department,
            group=officer.group,
            group_id=group.id,
            role=ROLE_OFFICER,
            password_hash=password_hash,
            index=index,
        )
        db.session.add(user)
        officer_users[officer.officer_id] = user

    admin_users: dict[str, list[User]] = {'group': [], 'department': [], 'center': [], 'super': []}
    plan = _admin_plan(manifest, group_objects)
    for index, (scope, number, role, department, group_name, _permission) in enumerate(plan, start=1):
        group = group_objects[(department, group_name)]
        admin = _new_user(
            number=number,
            student_id=f'{970000000000 + index:012d}',
            name=f'\u9a8c\u6536\u7ba1\u7406\u5458-{number}',
            department=department,
            group=group_name,
            group_id=group.id,
            role=role,
            password_hash=password_hash,
            index=1002 + index,
        )
        db.session.add(admin)
        admin_users[scope].append(admin)
    db.session.flush()

    for department_name, department in departments.items():
        admin = next(item for item in admin_users['department'] if item.department == department_name)
        department.manager_id = admin.id
        department.head = admin.name
    for admin in admin_users['group']:
        group = group_objects[(admin.department, admin.group)]
        group.leader_id = admin.id
        group.leader = admin.name

    permissions = _acceptance_permission_objects()
    for scope, admins in admin_users.items():
        for admin in admins:
            permission_name = (
                PERMISSION_GROUP if scope == 'group'
                else PERMISSION_DEPARTMENT if scope == 'department'
                else PERMISSION_CENTER
            )
            _add_permission(admin, permissions[permission_name])
    db.session.commit()
    return officer_users, admin_users, group_objects, departments


def _form_model(form: FormSpec, passwordless: bool = True) -> LectureForm:
    del passwordless
    return LectureForm(
        listener_name=form.listener_name,
        listener_number=form.officer_id,
        course_changes='\u65e0',
        lecture_date=form.lecture_date,
        class_period=f'{form.start_period}-{form.end_period}',
        lecture_location=form.lecture_location,
        teacher_name=form.teacher_name,
        teacher_college=form.teacher_college,
        course_title=form.course_title,
        student_grade_class=form.student_grade_class,
        abnormal_situation='\u65e0',
        teaching_method='\u7ed3\u5408\u8bfe\u7a0b\u5185\u5bb9\u5c55\u5f00\u8bb2\u89e3',
        classroom_discipline='\u79e9\u5e8f\u826f\u597d',
        classroom_atmosphere='\u6c14\u6c1b\u79ef\u6781',
        courseware_quality='\u8bfe\u4ef6\u6e05\u6670',
        overall_effect='\u8fbe\u5230\u9884\u671f\u6559\u5b66\u6548\u679c',
        quality_case='\u8bb0\u5f55\u5408\u6210\u4f18\u8d28\u6848\u4f8b',
        course_feedback=form.course_feedback,
        suggestions=form.suggestions,
        student_signature1=form.student_signature1,
        contact_phone1=form.contact_phone1,
        student_signature2=form.student_signature2,
        contact_phone2=form.contact_phone2,
        status=STATUS_PENDING,
        reviewer_id=None,
        review_time=None,
        review_comment=None,
        audit_tag='\u9700\u8981\u4eba\u5de5\u5ba1\u6838',
        unique_id=None,
    )


def _seed_forms(manifest: AcceptanceManifest) -> int:
    for start in range(0, len(manifest.forms), 100):
        chunk = [_form_model(form) for form in manifest.forms[start:start + 100]]
        db.session.add_all(chunk)
        db.session.flush()
        for form in chunk:
            form.unique_id = form.id
        db.session.commit()
    return LectureForm.query.filter_by(status=STATUS_PENDING).count()


def _import_dataset(kind: str, path: Path, semester: str, actor_id: int | None):
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    existing = ScheduleDataset.query.filter_by(
        kind=kind, semester=str(semester), sha256=digest,
    ).one_or_none()
    if existing is not None:
        if existing.status == DatasetStatus.ACTIVE.value:
            return existing
        if existing.status == DatasetStatus.STAGED.value:
            return activate_dataset(existing.id, actor_id)
        raise RuntimeError(f'acceptance dataset {kind} is not activatable')

    dataset = preview_dataset(
        kind,
        semester,
        io.BytesIO(data),
        path.name,
        actor_id,
    )
    if dataset.status != DatasetStatus.STAGED.value or dataset.error_count:
        raise RuntimeError(f'acceptance dataset {kind} failed validation')
    return activate_dataset(dataset.id, actor_id)


def _coverage_counts(manifest: AcceptanceManifest) -> dict[str, int]:
    counts = {'complete': 0, 'basic': 0, 'missing': 0}
    for officer in manifest.officers:
        schedule = get_listener_schedule(
            officer.officer_id,
            officer.student_id,
            manifest.config.semester,
        )
        key = 'missing' if schedule.coverage == ScheduleCoverage.NONE else schedule.coverage.value
        counts[key] += 1
    expected = {
        'complete': sum(item.coverage == 'complete' for item in manifest.officers),
        'basic': sum(item.coverage == 'basic' for item in manifest.officers),
        'missing': sum(item.coverage == 'missing' for item in manifest.officers),
    }
    if counts != expected:
        raise RuntimeError('schedule coverage totals do not close over acceptance officers')
    return counts


def _dataset_paths(manifest: AcceptanceManifest) -> dict[str, Path]:
    generated = manifest.config.output_path('generated')
    return {
        SCHOOL_KIND: generated / 'school-schedule-acceptance.xlsx',
        CLASS_MAPPING_KIND: generated / 'class-mapping.xlsx',
        PERSONAL_KIND: generated / 'personal-schedule.xlsx',
    }


def _existing_complete_result(
    manifest: AcceptanceManifest,
    officer_numbers: tuple[str, ...],
    admin_numbers: tuple[str, ...],
) -> SeedResult | None:
    """Return an existing complete run, or ``None`` for an empty database."""

    existing_users = User.query.filter(User.number.in_(officer_numbers + admin_numbers)).all()
    existing_forms = LectureForm.query.filter(
        LectureForm.listener_number.in_(officer_numbers)
    ).count()
    scaffold_count = Department.query.filter(Department.name.in_(DEPARTMENTS)).count()
    if not existing_users and not existing_forms and not scaffold_count:
        return None

    if (
        len(existing_users) != len(officer_numbers) + len(admin_numbers)
        or existing_forms != len(manifest.forms)
        or scaffold_count != 4
    ):
        raise RuntimeError('acceptance database contains partial seed rows')

    group_objects = {
        (group.department, group.name): group
        for group in Group.query.filter(Group.department.in_(DEPARTMENTS)).all()
    }
    if len(group_objects) != 8:
        raise RuntimeError('acceptance database contains partial seed groups')

    datasets = {}
    for kind, path in _dataset_paths(manifest).items():
        if not path.is_file():
            raise RuntimeError('generated acceptance schedule workbook is missing')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        dataset = ScheduleDataset.query.filter_by(
            kind=kind, semester=str(manifest.config.semester), sha256=digest,
        ).one_or_none()
        if dataset is None or dataset.status != DatasetStatus.ACTIVE.value:
            raise RuntimeError('acceptance database contains partial schedule datasets')
        datasets[kind] = dataset

    by_number = {user.number: user for user in existing_users}
    officer_users = {number: by_number[number] for number in officer_numbers}
    admin_users = {'group': [], 'department': [], 'center': [], 'super': []}
    plan = _admin_plan(manifest, group_objects)
    for scope, number, _role, _department, _group, permission_name in plan:
        user = by_number.get(number)
        if user is None or user.role not in {ROLE_ADMIN, ROLE_SUPER}:
            raise RuntimeError('acceptance database contains invalid administrator rows')
        if RolePermission.query.filter_by(
            role=f'{SPECIAL_ROLE_PREFIX}{user.id}',
        ).join(Permission).filter(Permission.name == permission_name).one_or_none() is None:
            raise RuntimeError('acceptance database contains incomplete review permissions')
        admin_users[scope].append(user)
    coverage_counts = _coverage_counts(manifest)
    return _result(manifest, officer_users, admin_users, datasets, coverage_counts)


def _result(
    manifest: AcceptanceManifest,
    officer_users: Mapping[str, User],
    admin_users: Mapping[str, list[User]],
    datasets: Mapping[str, ScheduleDataset],
    coverage_counts: dict[str, int],
) -> SeedResult:
    permission_by_admin = {}
    for scope, users in admin_users.items():
        permission = PERMISSION_GROUP if scope == 'group' else PERMISSION_DEPARTMENT if scope == 'department' else PERMISSION_CENTER
        for user in users:
            permission_by_admin[user.id] = permission
    return SeedResult(
        information_officers=len(officer_users),
        administrators=sum(len(users) for users in admin_users.values()),
        departments=len(DEPARTMENTS),
        groups=8,
        logical_forms=len(manifest.forms),
        physical_form_rows=LectureForm.query.filter(
            LectureForm.listener_number.in_(tuple(officer_users))
        ).count(),
        active_datasets=len(datasets),
        permission_by_admin=permission_by_admin,
        admin_user_ids={scope: [user.id for user in users] for scope, users in admin_users.items()},
        officer_user_ids=[officer_users[item.officer_id].id for item in manifest.officers],
        dataset_ids={kind: dataset.id for kind, dataset in datasets.items()},
        coverage_counts=coverage_counts,
    )


def _write_seed_metadata(manifest: AcceptanceManifest, result: SeedResult) -> None:
    runtime = manifest.config.runtime_root
    if runtime is None:
        return
    path = manifest.config.output_path('manifest.json')
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    payload['seed'] = {
        'counts': {
            'information_officers': result.information_officers,
            'administrators': result.administrators,
            'departments': result.departments,
            'groups': result.groups,
            'logical_forms': result.logical_forms,
            'physical_form_rows': result.physical_form_rows,
            'active_datasets': result.active_datasets,
        },
        'admin_user_ids': result.admin_user_ids,
        'officer_user_ids': result.officer_user_ids,
        'dataset_ids': result.dataset_ids,
        'coverage_counts': result.coverage_counts,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def seed_acceptance_database(
    flask_app: Flask,
    manifest: AcceptanceManifest,
    *,
    password: str,
) -> SeedResult:
    """Create the complete synthetic acceptance state in an isolated DB."""

    if not isinstance(password, str) or not password:
        raise ValueError('acceptance seed password is required')
    with flask_app.app_context():
        runtime_root = _guard_environment(flask_app, manifest)
        del runtime_root
        manifest.config.validate()
        db.create_all()
        _enable_sqlite_guards()
        officer_numbers, admin_numbers = _expected_numbers(manifest)
        existing = _existing_complete_result(manifest, officer_numbers, admin_numbers)
        if existing is not None:
            _write_seed_metadata(manifest, existing)
            return existing

        officer_users, admin_users, _groups, _departments = _seed_users(manifest, password)
        if _seed_forms(manifest) != len(manifest.forms):
            raise RuntimeError('acceptance form count did not close')

        actor_id = admin_users['center'][0].id
        dataset_paths = _dataset_paths(manifest)
        for path in dataset_paths.values():
            if not path.is_file():
                raise RuntimeError('generated acceptance schedule workbook is missing')
        datasets = {}
        for kind in (SCHOOL_KIND, CLASS_MAPPING_KIND, PERSONAL_KIND):
            datasets[kind] = _import_dataset(
                kind, dataset_paths[kind], manifest.config.semester, actor_id,
            )
        active_count = ScheduleDataset.query.filter_by(
            semester=str(manifest.config.semester), status=DatasetStatus.ACTIVE.value,
        ).count()
        if active_count != 3:
            raise RuntimeError('acceptance schedule datasets did not activate as three kinds')
        coverage_counts = _coverage_counts(manifest)
        result = _result(manifest, officer_users, admin_users, datasets, coverage_counts)
        if result.physical_form_rows != len(manifest.forms):
            raise RuntimeError('acceptance physical form rows did not close')
        _write_seed_metadata(manifest, result)
        return result


__all__ = [
    'SeedResult',
    'load_manifest_file',
    'seed_acceptance_database',
]
