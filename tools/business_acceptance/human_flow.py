"""Route-backed actors for the synthetic human-review acceptance flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from flask import Flask

from app.models import LectureForm, User, db
from app.review_automation.models import ReviewAssessment
from app.utils.review_permissions import get_user_review_permission


FORM_FIELDS = (
    'listener_name',
    'listener_number',
    'course_changes',
    'lecture_date',
    'class_period',
    'lecture_location',
    'teacher_name',
    'teacher_college',
    'course_title',
    'student_grade_class',
    'abnormal_situation',
    'teaching_method',
    'classroom_discipline',
    'classroom_atmosphere',
    'courseware_quality',
    'overall_effect',
    'quality_case',
    'course_feedback',
    'suggestions',
    'student_signature1',
    'contact_phone1',
    'student_signature2',
    'contact_phone2',
)


class HumanFlowError(RuntimeError):
    """Raised when an existing business route does not complete an action."""


FIRST_STAGE_CATEGORIES = (
    '\u65e0\u660e\u663e\u98ce\u9669',
    '\u5efa\u8bae\u590d\u6838',
    '\u9ad8\u98ce\u9669\u7591\u4f3c\u5047\u8868',
    '\u7cfb\u7edf\u65e0\u6cd5\u5224\u65ad',
)


@dataclass(frozen=True)
class HumanAction:
    form_id: int
    unique_id: int | str
    department: str
    group: str
    role: str
    category: str
    action: str
    ordinal: int


@dataclass(frozen=True)
class FormSnapshot:
    id: int
    unique_id: int | str
    status: str
    reviewer_id: int | None = None
    review_time: Any = None
    review_comment: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _snapshot_from_model(form: LectureForm) -> FormSnapshot:
    return FormSnapshot(
        id=form.id,
        unique_id=form.unique_id or form.id,
        status=form.status,
        reviewer_id=form.reviewer_id,
        review_time=form.review_time,
        review_comment=form.review_comment,
        data={field: getattr(form, field) for field in FORM_FIELDS},
    )


def _snapshot(app: Flask, form_id: int) -> FormSnapshot:
    with app.app_context():
        form = db.session.get(LectureForm, int(form_id))
        if form is None:
            raise HumanFlowError('form is missing after route action')
        return _snapshot_from_model(form)


def version_ids(unique_id: int | str) -> list[int]:
    forms = LectureForm.query.filter_by(unique_id=unique_id).order_by(LectureForm.id.asc()).all()
    return [form.id for form in forms]


def form_data_for_resubmission(form: FormSnapshot | LectureForm | Mapping[str, Any]) -> dict[str, Any]:
    def value(name: str, default: Any = ''):
        if isinstance(form, Mapping):
            return form.get(name, default)
        return getattr(form, name, default)

    payload = {field: value(field) for field in FORM_FIELDS}
    payload['unique_id'] = str(value('unique_id', value('id', '')))
    return payload


def form_data_from_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Select only editable business fields returned by the detail endpoint."""

    return {field: detail.get(field, '') for field in FORM_FIELDS}


def first_stage_action(category: str, ordinal: int) -> str:
    if category not in FIRST_STAGE_CATEGORIES:
        raise HumanFlowError(f'unsupported automation category: {category}')
    if category == '\u65e0\u660e\u663e\u98ce\u9669':
        return 'approve'
    if category == '\u9ad8\u98ce\u9669\u7591\u4f3c\u5047\u8868':
        return 'reject'
    return 'reject' if ordinal % 3 == 0 else 'approve'


def build_first_stage_actions(
    form_rows: Sequence[Mapping[str, Any]],
    classifications: Mapping[int | str, str],
    *,
    group_admin_groups: Mapping[str, str],
    group_forms_per_department: int = 75,
) -> tuple[HumanAction, ...]:
    """Assign actual classified forms to first-stage administrators.

    The group quota is selected from the group administrator's real scope and
    reserves one form for each classification before filling by manifest order.
    Remaining forms stay with the department administrator.  This function is
    deliberately database-independent so the assignment contract can be tested
    before any route-backed mutation.
    """

    if not isinstance(group_forms_per_department, int) or group_forms_per_department < len(FIRST_STAGE_CATEGORIES):
        raise ValueError('group_forms_per_department must cover all classifications')
    normalized_rows = [dict(row) for row in form_rows]
    if len({row.get('form_id') for row in normalized_rows}) != len(normalized_rows):
        raise HumanFlowError('first-stage form IDs must be unique')
    for row in normalized_rows:
        form_id = row.get('form_id')
        department = row.get('department')
        group = row.get('group')
        if form_id is None or not department or not group:
            raise HumanFlowError('first-stage rows require form_id, department and group')
        category = classifications.get(form_id)
        if category is None:
            category = classifications.get(str(form_id))
        if category not in FIRST_STAGE_CATEGORIES:
            raise HumanFlowError(f'missing valid classification for form {form_id}')

    actions: list[HumanAction] = []
    departments = list(dict.fromkeys(row['department'] for row in normalized_rows))
    for department in departments:
        rows = sorted(
            (row for row in normalized_rows if row['department'] == department),
            key=lambda row: (int(row.get('ordinal', 0)), int(row['form_id'])),
        )
        group_name = group_admin_groups.get(department)
        if not group_name:
            raise HumanFlowError(f'missing group administrator mapping for {department}')
        group_candidates = [row for row in rows if row['group'] == group_name]
        if len(group_candidates) < group_forms_per_department:
            raise HumanFlowError(f'group scope is smaller than quota for {department}')

        selected_ids: set[int | str] = set()
        selected: list[dict[str, Any]] = []
        for category in FIRST_STAGE_CATEGORIES:
            candidate = next(
                (
                    row for row in group_candidates
                    if row['form_id'] not in selected_ids
                    and classifications.get(row['form_id'], classifications.get(str(row['form_id']))) == category
                ),
                None,
            )
            if candidate is None:
                raise HumanFlowError(f'group scope lacks category {category} for {department}')
            selected.append(candidate)
            selected_ids.add(candidate['form_id'])
        for row in group_candidates:
            if len(selected) >= group_forms_per_department:
                break
            if row['form_id'] not in selected_ids:
                selected.append(row)
                selected_ids.add(row['form_id'])

        department_rows = [row for row in rows if row['form_id'] not in selected_ids]
        if {classifications.get(row['form_id'], classifications.get(str(row['form_id']))) for row in department_rows} != set(FIRST_STAGE_CATEGORIES):
            raise HumanFlowError(f'department scope lacks all categories for {department}')

        for role, role_rows in (('group', selected), ('department', department_rows)):
            for row in role_rows:
                form_id = row['form_id']
                category = classifications.get(form_id, classifications.get(str(form_id)))
                actions.append(HumanAction(
                    form_id=int(form_id),
                    unique_id=row.get('unique_id', form_id),
                    department=department,
                    group=row['group'],
                    role=role,
                    category=category,
                    action=first_stage_action(category, int(row.get('ordinal', form_id))),
                    ordinal=int(row.get('ordinal', form_id)),
                ))
    return tuple(sorted(actions, key=lambda item: item.ordinal))


@dataclass
class BusinessActor:
    app: Flask
    student_id: str
    password: str
    client: Any = field(init=False)
    user_id: int | None = field(default=None, init=False)

    def __post_init__(self):
        self.client = self.app.test_client()

    def login(self):
        response = self.client.post(
            '/auth/login',
            data={'student_id': self.student_id, 'password': self.password},
            follow_redirects=False,
        )
        if response.status_code not in {200, 302}:
            raise HumanFlowError(f'login route failed with status {response.status_code}')
        with self.client.session_transaction() as session:
            self.user_id = session.get('user_id')
        if self.user_id is None:
            raise HumanFlowError('login route did not establish a session')
        return response

    def _ensure_login(self):
        if self.user_id is None:
            self.login()

    @staticmethod
    def _json(response) -> dict[str, Any]:
        payload = response.get_json(silent=True)
        if not isinstance(payload, dict) or not payload.get('success', False):
            raise HumanFlowError('business route returned an unsuccessful response')
        return payload

    def list_review_forms(self, **query) -> list[dict[str, Any]]:
        self._ensure_login()
        response = self.client.get('/admin/api/review/forms', query_string=query or None)
        payload = self._json(response)
        return list(payload.get('forms', []))

    def list_review_form_ids(self, **query) -> list[int]:
        ids = []
        for group in self.list_review_forms(**query):
            forms = group.get('forms') or []
            if forms:
                ids.append(int(forms[0]['id']))
        return ids

    def get_form(self, form_id: int) -> dict[str, Any]:
        self._ensure_login()
        response = self.client.get(f'/admin/api/review/form/{int(form_id)}')
        payload = self._json(response)
        return dict(payload['form'])

    def approve(self, form_id: int, review_comment: str) -> FormSnapshot:
        detail = self.get_form(form_id)
        with self.app.app_context():
            current_user = db.session.get(User, int(self.user_id))
            is_final_reviewer = bool(
                current_user
                and get_user_review_permission(current_user.id) == '\u5ba1\u8868_\u4e2d\u5fc3'
            )
        if is_final_reviewer and detail.get('status') != '\u90e8\u95e8\u5df2\u5ba1\u6838':
            raise HumanFlowError(
                'final review requires a department-approved form'
            )
        response = self.client.post(
            f'/admin/api/review/submit/{int(form_id)}',
            json={
                'form_data': form_data_from_detail(detail),
                'review_comment': review_comment,
                'score_data': [],
            },
        )
        payload = self._json(response)
        return _snapshot(self.app, int(payload['form_id']))

    def reject(self, form_id: int, reason: str) -> FormSnapshot:
        self._ensure_login()
        detail = self.get_form(form_id)
        unique_id = detail.get('unique_id') or form_id
        response = self.client.post(
            f'/admin/api/review/reject/{int(form_id)}',
            json={'reason': reason},
        )
        payload = self._json(response)
        if payload.get('new_status') != '\u5df2\u9a73\u56de':
            raise HumanFlowError('reject route did not return rejected status')
        if 'form_id' in payload:
            raise HumanFlowError('reject route unexpectedly returned a form_id')
        return _latest_snapshot(self.app, unique_id)

    def resubmit(self, rejected_form_id: int, payload: Mapping[str, Any]) -> FormSnapshot:
        self._ensure_login()
        edit_response = self.client.get(f'/user/form/edit/{int(rejected_form_id)}')
        if edit_response.status_code != 200:
            raise HumanFlowError('information officer could not open rejected form edit route')
        response = self.client.post(
            '/user/submit_form',
            data=dict(payload),
            follow_redirects=False,
        )
        if response.status_code not in {302, 303}:
            raise HumanFlowError('information officer resubmission route did not redirect')
        unique_id = payload.get('unique_id')
        return _latest_snapshot(self.app, unique_id or rejected_form_id)


def _latest_snapshot(app: Flask, unique_id: int | str) -> FormSnapshot:
    with app.app_context():
        form = LectureForm.query.filter_by(unique_id=unique_id).order_by(LectureForm.id.desc()).first()
        if form is None:
            try:
                form = db.session.get(LectureForm, int(unique_id))
            except (TypeError, ValueError):
                form = None
        if form is None:
            raise HumanFlowError('latest form version is missing')
        return _snapshot_from_model(form)


def _seeded_form_for_spec(spec: Any) -> LectureForm:
    candidates = LectureForm.query.filter_by(
        listener_number=str(spec.officer_id),
        course_title=str(spec.course_title),
    ).order_by(LectureForm.id.asc()).all()
    if getattr(spec, 'lecture_date', None):
        candidates = [
            form for form in candidates
            if str(form.lecture_date) == str(spec.lecture_date)
        ]
    base_versions = [
        form for form in candidates
        if form.unique_id in (None, form.id)
    ]
    if len(base_versions) != 1:
        raise HumanFlowError(
            f'could not resolve one seeded form for synthetic officer {spec.officer_id}'
        )
    return base_versions[0]


def _default_admin_users() -> dict[str, list[User]]:
    groups = User.query.filter(User.number.like('YAG%')).order_by(User.number.asc()).all()
    departments = User.query.filter(User.number.like('YAD%')).order_by(User.number.asc()).all()
    center = User.query.filter_by(number='YAC01').first()
    super_admin = User.query.filter_by(number='YAS01').first()
    if len(groups) != 4 or len(departments) != 4 or center is None or super_admin is None:
        raise HumanFlowError('acceptance administrator accounts are incomplete')
    return {
        'group': groups,
        'department': departments,
        'center': [center],
        'super': [super_admin],
    }


def _manual_snapshot_tuple(snapshot: FormSnapshot) -> tuple[Any, ...]:
    return (
        snapshot.status,
        snapshot.reviewer_id,
        snapshot.review_time,
        snapshot.review_comment,
    )


def _assert_manual_action(snapshot: FormSnapshot, actor: BusinessActor, comment: str):
    if snapshot.review_comment != comment:
        raise HumanFlowError('route did not preserve the exact human review comment')
    if snapshot.reviewer_id != actor.user_id or snapshot.review_time is None:
        raise HumanFlowError('route did not write the human reviewer identity and time')


def _repair_payload(rejected: FormSnapshot) -> dict[str, Any]:
    payload = form_data_for_resubmission(rejected)
    payload['course_feedback'] = (
        '该老师讲解清楚，课堂互动自然，学生理解良好，评价内容具体且可追溯。'
    )
    payload['suggestions'] = payload.get('suggestions') or '无'
    return payload


def run_route_backed_human_flow(
    app: Flask,
    manifest: Any,
    password: str,
    *,
    admin_users: Mapping[str, Sequence[User]] | None = None,
    reassess: Any,
    group_forms_per_department: int = 75,
    final_rejection_every: int = 25,
    final_repair_every: int = 2,
) -> dict[str, Any]:
    """Execute the synthetic human-review chain through the existing routes."""

    if not callable(reassess):
        raise HumanFlowError('reassessment callback is required for rejected versions')
    if not isinstance(final_rejection_every, int) or final_rejection_every < 1:
        raise ValueError('final_rejection_every must be positive')
    if not isinstance(final_repair_every, int) or final_repair_every < 1:
        raise ValueError('final_repair_every must be positive')

    with app.app_context():
        accounts = dict(admin_users or _default_admin_users())
        for key in ('group', 'department', 'center', 'super'):
            if not accounts.get(key):
                raise HumanFlowError(f'missing acceptance {key} administrator')
        if len(accounts['group']) != 4 or len(accounts['department']) != 4:
            raise HumanFlowError('acceptance flow requires four group and four department admins')

        form_rows = []
        classifications: dict[int, str] = {}
        officer_users = {
            user.number: user
            for user in User.query.filter_by(role='信息员').all()
        }
        for spec in manifest.forms:
            form = _seeded_form_for_spec(spec)
            if form.status != '\u5f85\u5ba1\u6838':
                raise HumanFlowError('human flow requires seeded forms to start at pending')
            assessment = ReviewAssessment.query.filter_by(form_id=form.id).order_by(
                ReviewAssessment.created_at.desc(),
                ReviewAssessment.id.desc(),
            ).first()
            if assessment is None or assessment.classification not in FIRST_STAGE_CATEGORIES:
                raise HumanFlowError(f'missing actual classification for form {form.id}')
            if form.listener_number not in officer_users:
                raise HumanFlowError(f'missing synthetic officer for form {form.id}')
            classifications[form.id] = assessment.classification
            form_rows.append({
                'form_id': form.id,
                'unique_id': form.unique_id or form.id,
                'department': spec.department,
                'group': spec.group,
                'ordinal': int(spec.ordinal),
                'officer_id': form.listener_number,
                'review_mode': getattr(spec, 'review_mode', 'combined'),
            })

        group_admin_groups = {
            user.department: user.group
            for user in accounts['group']
        }
        if len(group_admin_groups) != 4:
            raise HumanFlowError('group administrator departments are not unique')
        actions = build_first_stage_actions(
            form_rows,
            classifications,
            group_admin_groups=group_admin_groups,
            group_forms_per_department=group_forms_per_department,
        )

        group_actors = {}
        department_actors = {}
        for user in accounts['group']:
            actor = BusinessActor(app, user.student_id, password)
            actor.login()
            group_actors[user.department] = actor
        for user in accounts['department']:
            actor = BusinessActor(app, user.student_id, password)
            actor.login()
            department_actors[user.department] = actor
        center_actor = BusinessActor(app, accounts['center'][0].student_id, password)
        super_actor = BusinessActor(app, accounts['super'][0].student_id, password)
        center_actor.login()
        super_actor.login()

        def visible_form_ids(actor: BusinessActor) -> set[int]:
            visible: set[int] = set()
            for group in actor.list_review_forms():
                if not isinstance(group, Mapping):
                    continue
                forms = group.get('forms')
                if isinstance(forms, Sequence) and not isinstance(forms, (str, bytes)):
                    for row in forms:
                        if isinstance(row, Mapping) and row.get('id') is not None:
                            visible.add(int(row['id']))
                elif group.get('id') is not None:
                    visible.add(int(group['id']))
            return visible

        expected_ids = {int(row['form_id']) for row in form_rows}
        scope_evidence = {
            'group': [],
            'department': [],
            'center': [],
            'super': [],
        }
        for user in accounts['group']:
            expected = {
                int(row['form_id'])
                for row in form_rows
                if row['department'] == user.department and row['group'] == user.group
            }
            visible = visible_form_ids(group_actors[user.department])
            scope_evidence['group'].append({
                'department': user.department,
                'group': user.group,
                'expected_count': len(expected),
                'visible_count': len(visible),
                'passed': visible == expected,
            })
        for user in accounts['department']:
            expected = {
                int(row['form_id'])
                for row in form_rows
                if row['department'] == user.department
            }
            visible = visible_form_ids(department_actors[user.department])
            scope_evidence['department'].append({
                'department': user.department,
                'expected_count': len(expected),
                'visible_count': len(visible),
                'passed': visible == expected,
            })
        center_visible = visible_form_ids(center_actor)
        super_visible = visible_form_ids(super_actor)
        scope_evidence['center'].append({
            'expected_count': len(expected_ids),
            'visible_count': len(center_visible),
            'passed': center_visible == expected_ids,
        })
        scope_evidence['super'].append({
            'expected_count': len(expected_ids),
            'visible_count': len(super_visible),
            'passed': super_visible == expected_ids,
        })

        officer_actors: dict[str, BusinessActor] = {}

        def officer_actor_for(number: str) -> BusinessActor:
            actor = officer_actors.get(number)
            if actor is None:
                user = officer_users[number]
                actor = BusinessActor(app, user.student_id, password)
                actor.login()
                officer_actors[number] = actor
            return actor

        first_stage = {
            'planned': len(actions),
            'approved': 0,
            'rejected': 0,
            'resubmitted': 0,
            'reassessed': 0,
            'roles': {
                'group': {'approve': 0, 'reject': 0},
                'department': {'approve': 0, 'reject': 0},
            },
        }
        row_by_id = {row['form_id']: row for row in form_rows}
        for action in actions:
            row = row_by_id[action.form_id]
            actor = (
                group_actors[action.department]
                if action.role == 'group'
                else department_actors[action.department]
            )
            first_stage['roles'][action.role][action.action] += 1
            if action.action == 'approve':
                approved = actor.approve(
                    action.form_id,
                    f'{action.role}管理员验收通过',
                )
                _assert_manual_action(approved, actor, f'{action.role}管理员验收通过')
                first_stage['approved'] += 1
                continue

            rejected = actor.reject(
                action.form_id,
                f'{action.role}管理员验收驳回，请修改后重交',
            )
            expected_reject_comment = f'{action.role}管理员验收驳回，请修改后重交'
            _assert_manual_action(rejected, actor, expected_reject_comment)
            officer_actor = officer_actor_for(row['officer_id'])
            resubmitted = officer_actor.resubmit(
                rejected.id,
                _repair_payload(rejected),
            )
            first_stage['rejected'] += 1
            first_stage['resubmitted'] += 1
            before_automation = _manual_snapshot_tuple(resubmitted)
            reassess(resubmitted.id, row['review_mode'])
            after_automation = _latest_snapshot(app, resubmitted.unique_id)
            if _manual_snapshot_tuple(after_automation) != before_automation:
                raise HumanFlowError('automation changed a protected human field')
            first_stage['reassessed'] += 1
            repaired_approval = actor.approve(
                resubmitted.id,
                f'{action.role}管理员返修复核通过',
            )
            _assert_manual_action(
                repaired_approval,
                actor,
                f'{action.role}管理员返修复核通过',
            )
            first_stage['approved'] += 1

        final = {
            'center_actions': 0,
            'super_actions': 0,
            'approved': 0,
            'rejected': 0,
            'repaired': 0,
            'left_rejected': 0,
            'reassessed': 0,
            'rejected_unique_ids': [],
            'repaired_unique_ids': [],
        }
        final_rejection_ordinal = 0
        rejection_return_evidence = {
            'checked': first_stage['rejected'],
            'response_form_ids': 0,
            'latest_lookup_by_unique_id': first_stage['rejected'],
        }
        for position, row in enumerate(sorted(form_rows, key=lambda item: item['ordinal']), start=1):
            latest = _latest_snapshot(app, row['unique_id'])
            if latest.status != '\u90e8\u95e8\u5df2\u5ba1\u6838':
                raise HumanFlowError('final review received a form before department review')
            final_actor = center_actor if position % 2 else super_actor
            final_role = 'center' if position % 2 else 'super'
            final[f'{final_role}_actions'] += 1
            if position % final_rejection_every:
                approved = final_actor.approve(latest.id, f'{final_role}管理员最终验收通过')
                _assert_manual_action(approved, final_actor, f'{final_role}管理员最终验收通过')
                final['approved'] += 1
                continue

            rejected = final_actor.reject(latest.id, f'{final_role}管理员最终驳回，请返修')
            _assert_manual_action(rejected, final_actor, f'{final_role}管理员最终驳回，请返修')
            final['rejected'] += 1
            rejection_return_evidence['checked'] += 1
            rejection_return_evidence['latest_lookup_by_unique_id'] += 1
            final_rejection_ordinal += 1
            final['rejected_unique_ids'].append(rejected.unique_id)
            if final_rejection_ordinal % final_repair_every == 0:
                officer_actor = officer_actor_for(row['officer_id'])
                resubmitted = officer_actor.resubmit(
                    rejected.id,
                    _repair_payload(rejected),
                )
                before_automation = _manual_snapshot_tuple(resubmitted)
                reassess(resubmitted.id, row['review_mode'])
                after_automation = _latest_snapshot(app, resubmitted.unique_id)
                if _manual_snapshot_tuple(after_automation) != before_automation:
                    raise HumanFlowError('automation changed a protected human field')
                final['reassessed'] += 1
                department_actor = department_actors[row['department']]
                department_result = department_actor.approve(
                    resubmitted.id,
                    '部门管理员返修后重新审核通过',
                )
                _assert_manual_action(
                    department_result,
                    department_actor,
                    '部门管理员返修后重新审核通过',
                )
                final_result = final_actor.approve(
                    department_result.id,
                    f'{final_role}管理员返修后最终通过',
                )
                _assert_manual_action(
                    final_result,
                    final_actor,
                    f'{final_role}管理员返修后最终通过',
                )
                final['repaired'] += 1
                final['repaired_unique_ids'].append(rejected.unique_id)
                final['approved'] += 1
            else:
                final['left_rejected'] += 1

        chain_lengths = [len(version_ids(row['unique_id'])) for row in form_rows]
        return {
            'status': 'PASS',
            'logins': {
                'group': len(group_actors),
                'department': len(department_actors),
                'center': 1,
                'super': 1,
                'officers': len(officer_actors),
            },
            'first_stage': first_stage,
            'final': final,
            'administrator_scopes': scope_evidence,
            'rejection_return': rejection_return_evidence,
            'automation_protected_fields': {
                'checked': first_stage['reassessed'] + final['reassessed'],
                'changed': 0,
            },
            'version_chains': {
                'logical_forms': len(chain_lengths),
                'with_multiple_versions': sum(length > 1 for length in chain_lengths),
                'max_versions': max(chain_lengths) if chain_lengths else 0,
            },
        }


__all__ = [
    'BusinessActor',
    'FIRST_STAGE_CATEGORIES',
    'FORM_FIELDS',
    'FormSnapshot',
    'HumanAction',
    'HumanFlowError',
    'build_first_stage_actions',
    'first_stage_action',
    'form_data_for_resubmission',
    'form_data_from_detail',
    'version_ids',
    'run_route_backed_human_flow',
]
