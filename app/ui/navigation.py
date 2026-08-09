from copy import deepcopy


NAVIGATION = (
    {
        'label': '工作',
        'items': (
            {
                'label': '今日工作',
                'endpoint': 'main.index',
                'icon': 'bi-house-door',
                'roles': ('信息员', '管理员', '超级管理员'),
                'active_endpoints': ('main.index',),
            },
            {
                'label': '听课与填报',
                'endpoint': 'user.listening_registration',
                'icon': 'bi-journal-text',
                'roles': ('信息员', '管理员', '超级管理员'),
                'active_endpoints': (
                    'user.listening_registration',
                    'user.my_forms',
                    'user.submit_form',
                    'user.edit_form',
                    'user.view_form',
                ),
            },
            {
                'label': '表单审核',
                'endpoint': 'admin.review_forms',
                'icon': 'bi-clipboard-check',
                'roles': ('管理员', '超级管理员'),
                'permission': 'review',
                'active_endpoints': ('admin.review_forms', 'admin.review_form_page'),
            },
        ),
    },
    {
        'label': '管理',
        'items': (
            {
                'label': '人员与部门',
                'endpoint': 'admin.manage_departments',
                'icon': 'bi-people',
                'roles': ('管理员', '超级管理员'),
                'permission': 'manage',
                'active_endpoints': (
                    'admin.manage_departments',
                    'admin.manage_groups',
                    'admin.view_managed_user',
                ),
            },
            {
                'label': '课程与登记',
                'endpoint': 'admin.course_feedback_management',
                'icon': 'bi-calendar-week',
                'roles': ('管理员', '超级管理员'),
                'active_endpoints': (
                    'admin.course_feedback_management',
                    'admin.registration_statistics',
                ),
            },
            {
                'label': '统计与导出',
                'endpoint': 'admin.statistics',
                'icon': 'bi-bar-chart',
                'roles': ('管理员', '超级管理员'),
                'active_endpoints': (
                    'admin.statistics',
                    'admin.review_assessment_stats',
                    'admin.submission_count_stats',
                    'admin.department_monthly_assessment_stats',
                ),
            },
            {
                'label': '系统设置',
                'endpoint': 'admin.system_management',
                'icon': 'bi-sliders',
                'roles': ('超级管理员',),
                'active_endpoints': (
                    'admin.system_management',
                    'admin.auto_review_page',
                    'admin.assessment_exemption_settings',
                ),
            },
        ),
    },
)


def build_navigation(user, endpoint='', review_permission=None, manage_permission=None):
    """Return navigation groups visible to ``user`` with one active endpoint."""
    groups = []
    for group_definition in NAVIGATION:
        items = []
        for definition in group_definition['items']:
            if user.role not in definition['roles']:
                continue
            requirement = definition.get('permission')
            if user.role != '超级管理员' and requirement == 'review' and not review_permission:
                continue
            if user.role != '超级管理员' and requirement == 'manage' and not manage_permission:
                continue
            item = deepcopy(definition)
            active_endpoints = item.pop('active_endpoints', (item['endpoint'],))
            item['active'] = endpoint in active_endpoints
            items.append(item)
        if items:
            groups.append({'label': group_definition['label'], 'items': items})
    return groups
