"""Consistent, resource-aware permission feedback for HTML and JSON routes."""

from flask import flash, jsonify, request


_ENDPOINT_RESOURCES = {
    'admin.manage_departments': '人员与部门',
    'admin.review_forms': '表单审核',
    'admin.review_form': '表单审核',
    'admin.review_assessment_stats': '审表考评统计',
    'admin.submission_count_stats': '交表数量统计',
    'admin.department_monthly_assessment_stats': '部门月度考评',
    'admin.system_settings': '系统设置',
    'admin.assessment_settings': '考评规则设置',
}


def resolve_permission_resource(endpoint=None):
    """Resolve the protected product area from the active Flask endpoint."""
    endpoint = endpoint or request.endpoint or ''
    if endpoint in _ENDPOINT_RESOURCES:
        return _ENDPOINT_RESOURCES[endpoint]
    if endpoint.startswith('admin.review') or '.review_' in endpoint:
        return '表单审核'
    if endpoint.startswith('admin.manage_'):
        return '管理功能'
    if endpoint.startswith('admin.'):
        return '管理后台'
    return '当前功能'


def build_forbidden_message(resource, reason='当前账号没有该功能权限。'):
    return f'无法打开“{resource}”：{reason}'


def build_forbidden_payload(resource, reason='当前账号没有该功能权限。'):
    return {
        'success': False,
        'code': 'FORBIDDEN',
        'resource': resource,
        'message': build_forbidden_message(resource, reason),
    }


def forbidden_json(resource, reason='当前账号没有该功能权限。'):
    return jsonify(build_forbidden_payload(resource, reason)), 403


def flash_forbidden(resource, reason='当前账号没有该功能权限。'):
    message = build_forbidden_message(resource, reason)
    flash(message, 'error')
    return message
