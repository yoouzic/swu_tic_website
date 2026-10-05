"""Initialize only a marked, isolated test profile; never import a timetable."""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'no-schedule'
MARKER_NAME = 'no-schedule-profile.json'


def isolated_paths():
    if os.environ.get('LOCAL_DEBUG_MODE') != '1':
        raise RuntimeError('LOCAL_DEBUG_MODE=1 is required')
    raw_db, raw_runtime = os.environ.get('SQLITE_DB_PATH'), os.environ.get('INSTANCE_DIR')
    if not raw_db or not raw_runtime:
        raise RuntimeError('请明确配置专用测试数据库 SQLITE_DB_PATH 和 INSTANCE_DIR')
    database, runtime = Path(raw_db).resolve(), Path(raw_runtime).resolve()
    forbidden = {(ROOT/'data/instance/lecture_forms.db').resolve(),
                 (ROOT/'data/instance/debug/lecture_forms-debug.db').resolve()}
    if database.name != 'lecture_forms-debug.db' or database.parent != runtime or database in forbidden:
        raise RuntimeError('拒绝修改非专用测试数据库；请使用独立无课表测试目录')
    storage = Path(os.environ.get('LOCAL_DEBUG_STORAGE_ROOT') or runtime/'storage').resolve()
    repo_storage = (ROOT/'data/storage').resolve()
    if storage.is_relative_to(repo_storage) and not storage.is_relative_to(repo_storage/'no-schedule-debug'):
        raise RuntimeError('拒绝使用正式或普通调试存储目录')
    marker = runtime/MARKER_NAME
    existing = None
    if marker.exists():
        try:
            existing = json.loads(marker.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise RuntimeError('测试环境标记损坏，拒绝修改已有数据库')
        if (not isinstance(existing, dict)
                or any(not isinstance(existing.get(key),str) or not existing[key].strip() for key in ('database','storage'))
                or existing.get('profile') != PROFILE
                or existing.get('version') != 1 or Path(existing.get('database', '')).resolve() != database
                or Path(existing.get('storage', '')).resolve() != storage):
            raise RuntimeError('测试环境标记与路径不匹配，拒绝修改已有数据库')
        if not database.exists():
            raise RuntimeError('测试数据库已丢失，请改用新的独立测试目录')
    elif database.exists():
        raise RuntimeError('该数据库未经此脚本初始化，拒绝覆盖或导入；请使用新的测试目录')
    return database, runtime, storage, marker, existing


def configure_environment(database, runtime, storage):
    # Force every generated-file path before app/.env configuration is imported.
    os.environ.update({
        'LOCAL_DEBUG_PROFILE': PROFILE, 'DATABASE_URL': '', 'SQLITE_DB_PATH': str(database),
        'INSTANCE_DIR': str(runtime), 'LOCAL_DEBUG_STORAGE_ROOT': str(storage),
        'FLASK_ENV': 'development', 'APP_ENV': 'development', 'FLASK_DEBUG': '0',
        'SESSION_COOKIE_SECURE': 'false', 'SECRET_KEY': 'isolated-no-schedule-debug-secret',
        'UPLOAD_FOLDER': str(storage/'uploads'), 'LECTURE_CAPTURE_ENABLED': 'true',
        'AUTOMATION_UPLOAD_DIR': str(storage/'automation'),
        'AUTO_REVIEW_UPLOAD_DIR': str(storage/'references'), 'AUTO_REVIEW_REPORT_DIR': str(storage/'reports'),
        'EXPORT_DIR': str(storage/'exports'), 'CONTACT_TEMPLATE_PATH': str(storage/'templates/contacts.xlsx'),
        'SCHEDULE_TEMPLATE_PATH': str(storage/'templates/schedule.xlsx'),
        'AUTO_REVIEW_DEFAULT_SCHEDULE_PATH': str(storage/'references/missing-current.xlsx'),
        'AUTO_REVIEW_DEFAULT_CONTACTS_PATH': str(storage/'references/missing-contacts.xlsx'),
        'AUTO_REVIEW_DEFAULT_FEEDBACK_PATH': str(storage/'references/missing-feedback.xlsx'),
        'STORAGE_CLEANUP_ENABLED': 'false', 'DEEPSEEK_API_KEY': '',
        'DEEPSEEK_BASE_URL': 'http://127.0.0.1:1/disabled',
        'CELERY_BROKER_URL': 'memory://', 'CELERY_RESULT_BACKEND': 'cache+memory://',
        'CELERY_TASK_ALWAYS_EAGER': '0',
    })


def write_blank_templates(storage):
    from openpyxl import Workbook
    columns = {
        'schedule.xlsx': ['教工号','姓名','性别','职称名称','教师所属学院','教师联系电话',
                         '场地编号','场地名称','场地类别名称','校区','楼层号','教学楼','座位数',
                         '课程号','选课课号','起始周','星期几','上课节次','课程名称','场地上课起始周',
                         '场地上课节次','教学班人数','教学班组成','学分','总学时','开课学院',
                         '专业组成','选课人数','周学时','上课时间','上课地点','课程性质','学期','学年'],
        'contacts.xlsx': ['编号','部门/组别','姓名','性别','年级','学院','专业','宿舍','手机号码','QQ号码','学号'],
    }
    for filename, headers in columns.items():
        path = storage/'templates'/filename
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            workbook = Workbook()
            workbook.active.append(headers)
            workbook.save(path)
    photo_path = storage/'示例门牌302.png'
    if not photo_path.exists():
        from PIL import Image, ImageDraw
        image = Image.new('RGB',(600,400),'white')
        ImageDraw.Draw(image).text((250,180),'302',fill='black',font_size=64)
        image.save(photo_path)


def prepare(password):
    database, runtime, storage, marker, existing = isolated_paths()
    configure_environment(database, runtime, storage)
    sys.path.insert(0, str(ROOT))
    from app.app import app, init_database
    from app.models import Department, Group, Permission, RolePermission, SystemSetting, User, db
    from app.services.schedule_availability import current_schedule_availability
    from werkzeug.security import generate_password_hash
    init_database()
    with app.app_context():
        if existing is None:
            office = Department.query.filter_by(name='办公部').one()
            planning = Department.query.filter_by(name='策划部').one()
            groups = [Group(name='测试一组', department='办公部', max_members=50),
                      Group(name='测试二组', department='办公部', max_members=50),
                      Group(name='测试一组', department='策划部', max_members=50)]
            db.session.add_all(groups); db.session.flush()
            specs = [
                ('user001','信息员','测试信息员',groups[0],[]),
                ('leader','管理员','测试小组长',groups[0],['审表_小组','管理部门小组']),
                ('manager','管理员','测试部长',groups[0],['审表_部门','管理部门']),
                ('center','管理员','测试中心审表',groups[0],['审表_中心']),
                ('super','超级管理员','测试超级管理员',groups[0],[]),
                ('user002','信息员','同部其他组信息员',groups[1],[]),
                ('user003','信息员','其他部门信息员',groups[2],[]),
            ]
            created = {}
            for index, (account, role, name, group, permissions) in enumerate(specs, 1):
                user = User(student_id=account, number=f'NS{index:03}', name=name, role=role,
                            department=group.department, group=group.name, group_id=group.id, is_active=True,
                            password_hash=generate_password_hash(password), gender='-', grade='2025',
                            college='测试学院', major='测试专业', dormitory='-', phone=f'138000000{index:02}', qq='10000')
                db.session.add(user); db.session.flush(); created[account] = user
                for permission_name in permissions:
                    permission = Permission.query.filter_by(name=permission_name).one()
                    db.session.add(RolePermission(role=f'特殊角色_{user.id}', permission_id=permission.id))
            office.manager_id = created['manager'].id
            office.head = created['manager'].name
            groups[0].leader_id = created['leader'].id
            groups[0].leader = created['leader'].name
            today = date.today()
            first_monday = today - timedelta(days=today.weekday()+28)
            year = today.year if today.month >= 9 else today.year-1
            term = '1' if today.month >= 9 else '2'
            for key, value in {
                'teaching_current_semester':f'{year}-{year+1}-{term}',
                'teaching_first_week_monday':first_monday.isoformat(), 'semester_first_monday':first_monday.isoformat(),
                'teaching_week_start_day':'0', 'teaching_total_weeks':'20',
                'teaching_required_submission':'1',
                'teaching_check_dept_review':'true','teaching_check_center_review':'true',
            }.items():
                SystemSetting.set(key,value)
            db.session.commit()
            marker_data = {'profile':PROFILE, 'version':1, 'database':str(database), 'storage':str(storage),
                           'accounts':[spec[0] for spec in specs]}
            marker.write_text(json.dumps(marker_data,ensure_ascii=False,indent=2),encoding='utf-8')
        state = current_schedule_availability()
        print('无课表测试：首次初始化完成（未导入全校课表）' if existing is None else '无课表测试：复用已有环境，保留测试数据、密码和设置')
        print(f"无课表测试：当前学期 {state['semester']}，课表状态 {state['status']}")
    write_blank_templates(storage)
    print('无课表测试：user001 信息员 / leader 小组长 / manager 部长 / center 中心 / super 超管')
    print('无课表测试：user002 同部其他组 / user003 其他部门；初始密码 ' + password)
    print('无课表测试：数据库 ' + str(database))
    if state['candidates_ready']:
        print('无课表测试：课表就绪后的测试照片 ' + str(storage/'示例门牌302.png'))


def main():
    parser = argparse.ArgumentParser(description='Initialize the isolated no-current-timetable test profile.')
    parser.add_argument('--password',required=True)
    args = parser.parse_args()
    if not args.password:
        parser.error('--password cannot be empty')
    prepare(args.password)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(2)
