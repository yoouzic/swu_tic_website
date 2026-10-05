# -*- coding: utf-8 -*-
"""Prepare the explicitly isolated SQLite database used by the Windows debug launcher."""

import argparse
import os
import sys
from argparse import Namespace
from pathlib import Path

from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def require_isolated_debug_database():
    if os.environ.get('LOCAL_DEBUG_MODE') != '1':
        raise RuntimeError('LOCAL_DEBUG_MODE=1 is required for local debug preparation')
    configured = os.environ.get('SQLITE_DB_PATH')
    if not configured:
        raise RuntimeError('SQLITE_DB_PATH must explicitly select an isolated debug database')
    selected = Path(configured).resolve()
    default_db = (ROOT / 'data' / 'instance' / 'lecture_forms.db').resolve()
    if selected == default_db:
        raise RuntimeError('refusing to modify the default business database')
    return selected


DEBUG_DB = require_isolated_debug_database()

from app.app import app
from app.models import User, db
from tools.debug_schedule import prepare_debug_schedule
from tools.init_user import (
    DEFAULT_DEPARTMENT,
    DEFAULT_GROUP,
    DEFAULT_VALUE,
    create_demo_users,
)


def demo_args(password):
    return Namespace(
        student_id='admin',
        password=password,
        create_demo_users=True,
        super_password=password,
        manager_password=password,
        user_password=password,
        role='超级管理员',
        number=None,
        name=None,
        department=DEFAULT_DEPARTMENT,
        group=DEFAULT_GROUP,
        gender=DEFAULT_VALUE,
        grade=DEFAULT_VALUE,
        college=DEFAULT_VALUE,
        major=DEFAULT_VALUE,
        dormitory=DEFAULT_VALUE,
        phone=DEFAULT_VALUE,
        qq=DEFAULT_VALUE,
        inactive=False,
    )


def prepare(password):
    result = create_demo_users(demo_args(password))
    if result:
        return result
    with app.app_context():
        from app.services.lecture_site_capture import ensure_capture_schema
        ensure_capture_schema()
        print(prepare_debug_schedule())
        administrators = User.query.filter(User.role.in_(('管理员', '超级管理员'))).all()
        for user in administrators:
            user.password_hash = generate_password_hash(password)
        db.session.commit()
        print(
            f'local debug database ready: {DEBUG_DB} '
            f'admin_passwords_reset={len(administrators)}'
        )
    return 0


def main():
    parser = argparse.ArgumentParser(description='Prepare the isolated local debug database.')
    parser.add_argument('--password', required=True)
    args = parser.parse_args()
    if not args.password:
        parser.error('--password cannot be empty')
    return prepare(args.password)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
