from copy import deepcopy

from app.utils.user_status import is_user_active


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
                    'user.edit_form',
                    'user.view_form',
                ),
            },
            {
                'label': '听课助手',
                'endpoint': 'user.submit_form',
                'icon': 'bi-stars',
                'authenticated': True,
                'active_endpoints': ('user.submit_form',),
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
                'roles': ('超级管理员',),
                'active_endpoints': (
                    'admin.course_management',
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


PAGE_LABELS = {
    'main.index': '今日工作',
    'auth.change_password': '修改密码',
    'user.listening_registration': '听课与填报',
    'user.my_forms': '听课与填报',
    'user.submit_form': '填写听课表',
    'user.edit_form': '编辑听课表',
    'user.view_form': '表单详情',
    'user.profile': '个人资料',
    'user.edit_profile': '编辑个人资料',
    'admin.review_forms': '表单审核',
    'admin.review_form_page': '表单审核',
    'admin.get_form_detail': '表单详情',
    'admin.view_forms': '表单管理',
    'admin.import_forms_excel_page': '导入表单',
    'admin.manage_departments': '人员与部门',
    'admin.manage_groups': '人员与部门',
    'admin.view_managed_user': '人员详情',
    'admin.course_management': '课程与登记',
    'admin.course_feedback_management': '课程与登记',
    'admin.registration_statistics': '登记统计',
    'admin.statistics': '统计与导出',
    'admin.review_assessment_stats': '审表考评统计',
    'admin.submission_count_stats': '交表数量统计',
    'admin.department_monthly_assessment_stats': '部门月度考评',
    'admin.system_management': '系统设置',
    'admin.auto_review_page': '自动审核',
    'admin.auto_review_results_page': '自动审核结果',
    'admin.assessment_exemption_settings': '考核对象与指标',
}


def resolve_page_label(groups, endpoint):
    """Return a current-page label without pretending it is a breadcrumb."""
    for group in groups:
        for item in group['items']:
            if item.get('active'):
                return item['label']
    return PAGE_LABELS.get(endpoint, '当前页面')


def build_navigation(user, endpoint='', review_permission=None, manage_permission=None):
    """Return navigation groups visible to ``user`` with one active endpoint."""
    groups = []
    for group_definition in NAVIGATION:
        items = []
        for definition in group_definition['items']:
            if definition.get('authenticated'):
                if not getattr(user, 'is_authenticated', False) or not is_user_active(user):
                    continue
            elif user.role not in definition['roles']:
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
