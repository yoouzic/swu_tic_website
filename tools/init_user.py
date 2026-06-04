# -*- coding: utf-8 -*-
"""Create or update a local user account for initial access."""

import argparse
import getpass
import os
import sys

from werkzeug.security import generate_password_hash


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.app import app, init_database
from app.models import Department, Group, Permission, RolePermission, User, db


ROLE_CHOICES = ('超级管理员', '管理员', '信息员')
DEFAULT_SUPER_ADMIN_NUMBER = 'SA001'
DEFAULT_SUPER_ADMIN_NAME = '超级管理员'
DEFAULT_DEPARTMENT = '未分配部门'
DEFAULT_GROUP = '未分配小组'
DEFAULT_VALUE = '-'
DEMO_USERS = (
    {
        'student_id': 'super',
        'number': 'SA002',
        'name': '超管测试',
        'role': '超级管理员',
        'department': DEFAULT_DEPARTMENT,
        'group': DEFAULT_GROUP,
    },
    {
        'student_id': 'manager',
        'number': 'M001',
        'name': '管理员测试',
        'role': '管理员',
        'department': '办公部',
        'group': DEFAULT_GROUP,
        'permissions': ('管理部门', '审表_部门'),
    },
    {
        'student_id': 'user001',
        'number': 'U001',
        'name': '信息员测试',
        'role': '信息员',
        'department': '办公部',
        'group': '一组',
    },
)


def parse_args():
    parser = argparse.ArgumentParser(
        description='Create or update a user account in the configured SQLite database.'
    )
    parser.add_argument('--student-id', default='admin', help='Login account, usually student_id.')
    parser.add_argument('--password', help='Password. If omitted, the script prompts securely.')
    parser.add_argument(
        '--create-demo-users',
        action='store_true',
        help='Create/update one super admin, one manager, and one normal user for local testing.',
    )
    parser.add_argument('--super-password', help='Password for the demo super admin account.')
    parser.add_argument('--manager-password', help='Password for the demo manager account.')
    parser.add_argument('--user-password', help='Password for the demo normal user account.')
    parser.add_argument('--role', choices=ROLE_CHOICES, default='超级管理员')
    parser.add_argument('--number', help='Unique internal number. Defaults to SA001 for super admin.')
    parser.add_argument('--name', help='Display name. Defaults to 超级管理员 for super admin.')
    parser.add_argument('--department', default=DEFAULT_DEPARTMENT)
    parser.add_argument('--group', default=DEFAULT_GROUP)
    parser.add_argument('--gender', default=DEFAULT_VALUE)
    parser.add_argument('--grade', default=DEFAULT_VALUE)
    parser.add_argument('--college', default=DEFAULT_VALUE)
    parser.add_argument('--major', default=DEFAULT_VALUE)
    parser.add_argument('--dormitory', default=DEFAULT_VALUE)
    parser.add_argument('--phone', default=DEFAULT_VALUE)
    parser.add_argument('--qq', default=DEFAULT_VALUE)
    parser.add_argument('--inactive', action='store_true', help='Create/update the account as inactive.')
    return parser.parse_args()


def _copy_args(args, **overrides):
    data = vars(args).copy()
    data.update(overrides)
    return argparse.Namespace(**data)


def _default_number(args):
    if args.number:
        return args.number
    if args.role == '超级管理员':
        return DEFAULT_SUPER_ADMIN_NUMBER
    return args.student_id[:20]


def _default_name(args):
    if args.name:
        return args.name
    if args.role == '超级管理员':
        return DEFAULT_SUPER_ADMIN_NAME
    return args.student_id


def ensure_department_and_group(department_name, group_name):
    if department_name and department_name != DEFAULT_DEPARTMENT:
        department = Department.query.filter_by(name=department_name).first()
        if not department:
            department = Department(name=department_name, description='')
            db.session.add(department)
            db.session.flush()

    if (
        department_name
        and group_name
        and department_name != DEFAULT_DEPARTMENT
        and group_name != DEFAULT_GROUP
    ):
        group = Group.query.filter_by(department=department_name, name=group_name).first()
        if not group:
            group = Group(name=group_name, department=department_name, description='', max_members=10)
            db.session.add(group)
            db.session.flush()
        return group.id

    return None


def upsert_user(args, password):
    with app.app_context():
        init_database()

        user = User.query.filter_by(student_id=args.student_id).first()
        created = user is None
        group_id = ensure_department_and_group(args.department, args.group)
        if created:
            user = User(
                student_id=args.student_id,
                password_hash=generate_password_hash(password),
                number=_default_number(args),
                name=_default_name(args),
                department=args.department,
                group=args.group,
                gender=args.gender,
                grade=args.grade,
                college=args.college,
                major=args.major,
                dormitory=args.dormitory,
                phone=args.phone,
                qq=args.qq,
                role=args.role,
                group_id=group_id,
                is_active=not args.inactive,
            )
            db.session.add(user)
        else:
            user.password_hash = generate_password_hash(password)
            user.number = _default_number(args)
            user.name = _default_name(args)
            user.department = args.department
            user.group = args.group
            user.gender = args.gender
            user.grade = args.grade
            user.college = args.college
            user.major = args.major
            user.dormitory = args.dormitory
            user.phone = args.phone
            user.qq = args.qq
            user.role = args.role
            user.group_id = group_id
            user.is_active = not args.inactive

        snapshot = {
            'student_id': user.student_id,
            'number': user.number,
            'role': user.role,
            'is_active': user.is_active,
        }
        db.session.commit()
        return snapshot, created


def set_special_permissions(student_id, permission_names):
    """Replace one user's custom permission set by permission name."""
    with app.app_context():
        init_database()

        user = User.query.filter_by(student_id=student_id).first()
        if not user:
            raise RuntimeError(f'User not found after upsert: {student_id}')

        role_name = f'特殊角色_{user.id}'
        RolePermission.query.filter_by(role=role_name).delete()

        for permission_name in permission_names:
            permission = Permission.query.filter_by(name=permission_name).first()
            if not permission:
                raise RuntimeError(f'Permission not found: {permission_name}')
            db.session.add(RolePermission(role=role_name, permission_id=permission.id))

        db.session.commit()
        return role_name


def create_demo_users(args):
    passwords = {
        'super': args.super_password or args.password,
        'manager': args.manager_password or args.password,
        'user001': args.user_password or args.password,
    }
    missing = [student_id for student_id, value in passwords.items() if not value]
    if missing:
        print(
            'Missing demo passwords for: ' + ', '.join(missing) +
            '. Provide --super-password, --manager-password, --user-password, or a shared --password.',
            file=sys.stderr,
        )
        return 2

    for demo_user in DEMO_USERS:
        demo_permissions = demo_user.get('permissions', ())
        user_data = {key: value for key, value in demo_user.items() if key != 'permissions'}
        user_args = _copy_args(args, **user_data)
        snapshot, created = upsert_user(user_args, passwords[demo_user['student_id']])
        if demo_permissions:
            set_special_permissions(demo_user['student_id'], demo_permissions)
        action = 'created' if created else 'updated'
        print(
            f'{action}: student_id={snapshot["student_id"]} number={snapshot["number"]} '
            f'role={snapshot["role"]} active={int(snapshot["is_active"])}'
        )
    return 0


def main():
    args = parse_args()
    if args.create_demo_users:
        return create_demo_users(args)

    password = args.password
    if not password:
        password = getpass.getpass('Password: ')
    if not password:
        print('Password cannot be empty.', file=sys.stderr)
        return 2

    user, created = upsert_user(args, password)
    action = 'created' if created else 'updated'
    print(
        f'{action}: student_id={user["student_id"]} number={user["number"]} '
        f'role={user["role"]} active={int(user["is_active"])}'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
