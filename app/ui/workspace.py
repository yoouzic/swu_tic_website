from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceSnapshot:
    total_forms: int = 0
    pending_forms: int = 0
    reservation_count: int = 0
    department_users: int = 0
    department_forms: int = 0
    total_users: int = 0
    failed_jobs: int = 0
    draft_saved: bool = False


def _build_review_tasks(pending_forms):
    if pending_forms <= 0:
        return []
    return [{
        'label': '处理待审核表单',
        'description': f'{pending_forms} 份表单等待处理，进入审核队列继续工作。',
        'endpoint': 'admin.review_forms',
    }]


def load_workspace_snapshot(user):
    from app.models import CourseRegistration, LectureForm, LectureFormDraft, User

    if user.role == '信息员':
        return WorkspaceSnapshot(
            total_forms=LectureForm.query.filter_by(listener_number=user.number).count(),
            reservation_count=CourseRegistration.query.filter_by(user_id=user.id).count(),
            draft_saved=LectureFormDraft.query.filter_by(user_id=user.id, draft_key='submit_form').first() is not None,
        )
    if user.role == '管理员':
        return WorkspaceSnapshot(
            pending_forms=LectureForm.query.join(User, LectureForm.listener_number == User.number).filter(
                User.department == user.department,
                LectureForm.status.in_(['待审核', '部门已审核']),
            ).count(),
            department_users=User.query.filter_by(department=user.department, is_active=True).count(),
            department_forms=LectureForm.query.join(User, LectureForm.listener_number == User.number).filter(
                User.department == user.department,
            ).count(),
        )
    return WorkspaceSnapshot(
        pending_forms=LectureForm.query.filter(LectureForm.status.in_(['待审核', '部门已审核'])).count(),
        total_users=User.query.filter_by(is_active=True).count(),
        total_forms=LectureForm.query.count(),
    )


def build_workspace(user, snapshot):
    if user.role == '信息员':
        tasks = []
        if snapshot.draft_saved:
            tasks.append({
                'label': '继续未完成的听课表',
                'description': '草稿已经自动保存，可以从上次位置继续。',
                'endpoint': 'user.submit_form',
            })
        return {
            'role': user.role,
            'role_slug': 'information-officer',
            'title': f'你好，{user.name}',
            'summary': '继续今天的听课安排，或查看已经提交的记录。',
            'primary_action': {'label': '填写听课表', 'endpoint': 'user.submit_form'},
            'metrics': [
                {'label': '我的表单', 'value': snapshot.total_forms},
                {'label': '我的预约', 'value': snapshot.reservation_count},
            ],
            'tasks': tasks,
            'quick_actions': [
                {'label': '听课登记', 'endpoint': 'user.listening_registration'},
                {'label': '查看记录', 'endpoint': 'user.my_forms'},
            ],
        }

    if user.role == '管理员':
        return {
            'role': user.role,
            'role_slug': 'manager',
            'title': f'上午好，{user.name}',
            'summary': f'先处理 {snapshot.pending_forms} 份待审核表单，再查看部门提交情况。',
            'primary_action': {'label': '处理审核', 'endpoint': 'admin.review_forms'},
            'metrics': [
                {'label': '待审核', 'value': snapshot.pending_forms},
                {'label': '部门成员', 'value': snapshot.department_users},
                {'label': '部门表单', 'value': snapshot.department_forms},
            ],
            'tasks': _build_review_tasks(snapshot.pending_forms),
            'quick_actions': [
                {'label': '人员与部门', 'endpoint': 'admin.manage_departments'},
                {'label': '统计与导出', 'endpoint': 'admin.statistics'},
            ],
        }

    tasks = _build_review_tasks(snapshot.pending_forms)
    if snapshot.failed_jobs:
        tasks.append({
            'label': '检查系统任务',
            'description': f'{snapshot.failed_jobs} 个导入或自动审核任务需要处理。',
            'endpoint': 'admin.system_management',
        })
    return {
        'role': user.role,
        'role_slug': 'super-admin',
        'title': f'上午好，{user.name}',
        'summary': f'全局共有 {snapshot.pending_forms} 份表单等待处理。',
        'primary_action': {'label': '处理审核', 'endpoint': 'admin.review_forms'},
        'metrics': [
            {'label': '待审核', 'value': snapshot.pending_forms},
            {'label': '活跃成员', 'value': snapshot.total_users},
            {'label': '全部表单', 'value': snapshot.total_forms},
        ],
        'tasks': tasks,
        'quick_actions': [
            {'label': '人员与部门', 'endpoint': 'admin.manage_departments'},
            {'label': '系统设置', 'endpoint': 'admin.system_management'},
            {'label': '统计与导出', 'endpoint': 'admin.statistics'},
        ],
    }
