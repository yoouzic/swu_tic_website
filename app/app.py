from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
from werkzeug.security import generate_password_hash, check_password_hash
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.utils.env_config import env_bool, env_int, env_path, env_value, is_production

DEFAULT_INSTANCE_DIR = os.path.join('data', 'instance')
DEFAULT_UPLOAD_FOLDER = os.path.join('data', 'storage', 'uploads', 'general')

app = Flask(__name__)
# 生产环境配置
if is_production():
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.config['SECRET_KEY'] = env_value(
    'SECRET_KEY',
    default='dev-only-secret-key',
    required=is_production(),
)
# 使用 data/instance/lecture_forms.db 作为数据库
INSTANCE_DIR = env_path('INSTANCE_DIR', DEFAULT_INSTANCE_DIR)
os.makedirs(INSTANCE_DIR, exist_ok=True)
DB_PATH = env_path('SQLITE_DB_PATH', os.path.join(INSTANCE_DIR, 'lecture_forms.db'))
DATABASE_URL = env_value('DATABASE_URL')
if DATABASE_URL:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.abspath(DB_PATH)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = env_path('UPLOAD_FOLDER', DEFAULT_UPLOAD_FOLDER)
MAX_CONTENT_LENGTH_MB = env_int('MAX_CONTENT_LENGTH_MB', 16, minimum=1)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH_MB * 1024 * 1024  # 可通过环境变量调整上传大小
app.config['DEBUG'] = env_bool('FLASK_DEBUG', False)

# 导入数据库模型（统一使用绝对包导入）
from app.models import db, User, Department, Group, LectureForm, Permission, RolePermission
from app.utils.user_status import is_user_active
from app.utils.storage_cleanup import run_scheduled_storage_cleanup


# 初始化数据库
db.init_app(app)


@app.before_request
def maybe_run_storage_cleanup():
    """Run generated-file cleanup at most once per configured interval."""
    try:
        result = run_scheduled_storage_cleanup(BASE_DIR)
        if not result.get('skipped'):
            app.logger.info('storage cleanup completed: %s', result)
    except Exception as exc:
        app.logger.warning('storage cleanup skipped after error: %s', exc)

# 注册蓝图（统一使用绝对包导入）
from app.blueprints.auth import auth_bp
from app.blueprints.user import user_bp
from app.blueprints.admin import admin_bp
from app.blueprints.main import main_bp

app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(user_bp, url_prefix='/user')
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(main_bp)

# 定义匿名用户类，用于未登录状态
class AnonymousUser:
    id = None
    name = 'Guest'
    role = '游客'
    department = '无'
    is_active = False
    is_authenticated = False

# 全局上下文处理器：注入 current_user
@app.context_processor
def inject_current_user():
    """将当前登录用户注入到所有模板中"""
    user_id = session.get('user_id')
    if user_id:
        # 这里需要注意：User模型已经在上面导入
        user = User.query.get(user_id)
        if user and is_user_active(user):
            # 动态添加属性以兼容 Flask-Login 风格的检查
            user.is_authenticated = True
            return dict(current_user=user)
        if user and not is_user_active(user):
            session.clear()
    
    return dict(current_user=AnonymousUser())

# 创建上传目录
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True
)

# 数据库初始化和默认数据创建
def init_database():
    """初始化数据库并创建默认数据"""
    with app.app_context():
        db.create_all()
        
        # 创建默认部门
        if not Department.query.first():
            departments = [
                Department(name='办公部', description='负责办公事务'),
                Department(name='策划部', description='负责活动策划'),
                Department(name='技术部', description='负责技术支持'),
                Department(name='宣传部', description='负责宣传推广'),
                Department(name='荣昌办公部', description='负责办公事务'),
                Department(name='荣昌策划部', description='负责活动策划'),
                Department(name='荣昌技术部', description='负责技术支持'),
                Department(name='荣昌设计部', description='负责宣传推广')                
            ]
            for dept in departments:
                db.session.add(dept)
        
        # 创建默认权限
        if not Permission.query.first():
            permissions = [
                Permission(name='填表', description='填写听课表单'),
                Permission(name='审表_小组', description='审查小组内信息表'),
                Permission(name='审表_部门', description='审查部门内信息表'),
                Permission(name='审表_中心', description='审查中心内所有信息表'),
                Permission(name='管理部门小组', description='设置部门内小组组数，调整小组人员'),
                Permission(name='管理部门', description='调整部门人员')
            ]
            for perm in permissions:
                db.session.add(perm)
        # 提交默认数据
        db.session.commit()

if __name__ == '__main__':
    init_database()
    app.run(
        debug=app.config['DEBUG'],
        host=env_value('FLASK_RUN_HOST', '0.0.0.0'),
        port=env_int('FLASK_RUN_PORT', 5000, minimum=1),
    )
