from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import REAL, event

# 创建一个全局的db实例，稍后在app.py中初始化
db = SQLAlchemy()

# 信息员表（用户表）
class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), unique=True, nullable=False)  # 编号
    department = db.Column(db.String(50), nullable=False)  # 部门/组别
    name = db.Column(db.String(100), nullable=False)  # 姓名
    gender = db.Column(db.String(10), nullable=False)  # 性别
    grade = db.Column(db.String(20), nullable=False)  # 年级
    college = db.Column(db.String(100), nullable=False)  # 学院
    major = db.Column(db.String(100), nullable=False)  # 专业
    dormitory = db.Column(db.String(100), nullable=False)  # 宿舍
    phone = db.Column(db.String(20), nullable=False)  # 手机号码
    qq = db.Column(db.String(20), nullable=False)  # QQ号码
    student_id = db.Column(db.String(20), unique=True, nullable=False)  # 学号（用作登录账号）
    password_hash = db.Column(db.String(255), nullable=False)  # 密码散列值
    role = db.Column(db.String(20), nullable=False, default='信息员')  # 角色：信息员、管理员、超级管理员
    group = db.Column(db.String(50), nullable=False, default='待分配')  # 所属小组（保持向后兼容）
    group_id = db.Column(db.Integer, nullable=True)  # 所属小组ID（数据库中实际不存在外键约束）
    
    # 系统字段
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    is_active = db.Column(db.Boolean, default=True)  # 账户是否激活
    
    def __repr__(self):
        return f'<User {self.name} - {self.number}>'
    
    def get_role_display(self):
        """获取角色显示名称"""
        role_map = {
            '信息员': '信息员',
            '管理员': '管理员', 
            '超级管理员': '超级管理员'
        }
        return role_map.get(self.role, self.role)

class PasswordAuditLog(db.Model):
    __tablename__ = 'password_audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    request_method = db.Column(db.String(10), nullable=True)
    request_path = db.Column(db.String(255), nullable=True)
    source_ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    result = db.Column(db.String(20), nullable=False, default='success')
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    actor = db.relationship('User', foreign_keys=[actor_user_id], backref='password_audit_actions')
    target = db.relationship('User', foreign_keys=[target_user_id], backref='password_audit_targets')

# 部门表
class Department(db.Model):
    __tablename__ = 'departments'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # 部门名称
    description = db.Column(db.Text)  # 部门描述
    manager_id = db.Column(db.Integer, db.ForeignKey('users.id'))  # 部门负责人
    head = db.Column(db.Text, nullable=True)  # 部门负责人姓名（冗余字段，便于API使用）
    
    # 系统字段
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    manager = db.relationship('User', backref='managed_department', foreign_keys=[manager_id])
    
    def __repr__(self):
        return f'<Department {self.name}>'

# 小组表
class Group(db.Model):
    __tablename__ = 'groups'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)  # 小组名称
    department = db.Column(db.String(50), nullable=False)  # 所属部门
    leader_id = db.Column(db.Integer, db.ForeignKey('users.id'))  # 小组长
    leader = db.Column(db.Text, nullable=True)  # 小组长姓名（冗余字段，便于API使用）
    description = db.Column(db.Text)  # 小组描述
    max_members = db.Column(db.Integer, default=10)  # 最大成员数
    
    # 系统字段
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    leader_user = db.relationship('User', backref='led_group', foreign_keys=[leader_id])
    
    def __repr__(self):
        return f'<Group {self.name} - {self.department}>'
    
    def get_members(self):
        """获取小组成员"""
        return User.query.filter_by(group=self.name, department=self.department).all()
    
    def get_member_count(self):
        """获取小组成员数量"""
        return User.query.filter_by(group=self.name, department=self.department).count()

# 听课表单表（保持原有结构）
class LectureForm(db.Model):
    __tablename__ = 'lecture_forms'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # 基本信息
    listener_name = db.Column(db.String(100), nullable=False)  # 听课人姓名+学院
    listener_number = db.Column(db.String(50), nullable=False)  # 听课人编号
    course_changes = db.Column(db.Text, default='无')  # 课程信息变化
    
    # 时间地点信息
    lecture_date = db.Column(db.String(100), nullable=False)  # 听课时间
    class_period = db.Column(db.String(50), nullable=False)  # 第几节
    lecture_location = db.Column(db.String(100), nullable=False)  # 听课地点
    
    # 教师课程信息
    teacher_name = db.Column(db.String(100), nullable=False)  # 授课教师
    teacher_college = db.Column(db.String(100), nullable=False)  # 教师所属学院
    course_title = db.Column(db.String(200), nullable=False)  # 课程总标题
    student_grade_class = db.Column(db.String(100), nullable=False)  # 专业年级
    
    # 课堂情况
    abnormal_situation = db.Column(db.Text, default='无')  # 异常情况反映
    teaching_method = db.Column(db.Text, nullable=False)  # 主要教学方法
    classroom_discipline = db.Column(db.Text, nullable=False)  # 管理课堂纪律
    classroom_atmosphere = db.Column(db.Text, nullable=False)  # 调动课堂气氛
    courseware_quality = db.Column(db.Text, nullable=False)  # 课件制作质量
    overall_effect = db.Column(db.Text, nullable=False)  # 整体教学效果
    quality_case = db.Column(db.Text, nullable=False)  # 优质案例推荐
    
    # 评价反馈
    course_feedback = db.Column(db.Text, nullable=False)  # 课程反馈(优点)
    suggestions = db.Column(db.Text, default='无')  # 不足及建议
    
    # 听课人信息
    student_signature1 = db.Column(db.String(100), nullable=False)  # 听课班级同学签名1
    contact_phone1 = db.Column(db.String(20), nullable=False)  # 联系电话1
    student_signature2 = db.Column(db.String(100))  # 听课班级同学签名2
    contact_phone2 = db.Column(db.String(20))  # 联系电话2
    
    # 审核状态
    status = db.Column(db.String(20), default='待审核')  # 状态：待审核、部门已审核、中心已审核、已驳回
    reviewer_id = db.Column(db.Integer, db.ForeignKey('users.id'))  # 审核人
    review_time = db.Column(db.DateTime)  # 审核时间
    review_comment = db.Column(db.Text)  # 审核意见
    
    # 关联的登记记录
    registration_id = db.Column(db.Integer, db.ForeignKey('course_registrations.id'), nullable=True)
    # 审核状态标签（根据修改程度自动生成）
    audit_tag = db.Column(db.String(20), default='需要人工审核')  # 分号分隔标签：人工审核标签[;晚交标签]
    
    # 唯一标志ID（用于表单版本管理）
    unique_id = db.Column(db.Integer)  # 表单唯一标识，审核时创建新记录但保持相同unique_id
    
    # 系统字段
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    reviewer = db.relationship('User', backref='reviewed_forms', foreign_keys=[reviewer_id])
    registration = db.relationship('CourseRegistration', backref='forms')
    
    def __repr__(self):
        return f'<LectureForm {self.listener_name} - {self.course_title}>'
    
    def get_status_display(self):
        """获取状态显示名称"""
        status_map = {
            '待审核': '待审核',
            '部门已审核': '部门已审核',
            '中心已审核': '中心已审核',
            '已驳回': '已驳回'
        }
        return status_map.get(self.status, self.status)
    
    def get_latest_version(self):
        """获取该表单的最新版本"""
        if not self.unique_id:
            return self
        return LectureForm.query.filter_by(unique_id=self.unique_id).order_by(LectureForm.updated_at.desc()).first()
    
    def get_all_versions(self):
        """获取该表单的所有版本"""
        if not self.unique_id:
            return [self]
        return LectureForm.query.filter_by(unique_id=self.unique_id).order_by(LectureForm.updated_at.desc()).all()

# 权限表（用于更细粒度的权限控制）
class Permission(db.Model):
    __tablename__ = 'permissions'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # 权限名称
    description = db.Column(db.String(200))  # 权限描述
    
    def __repr__(self):
        return f'<Permission {self.name}>'

# 角色权限关联表
class RolePermission(db.Model):
    __tablename__ = 'role_permissions'
    
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)  # 角色
    permission_id = db.Column(db.Integer, db.ForeignKey('permissions.id'), nullable=False)  # 权限ID
    
    # 关系
    permission = db.relationship('Permission', backref='role_permissions')
    
    def __repr__(self):
        return f'<RolePermission {self.role} - {self.permission.name}>'

# 教师表
class Teacher(db.Model):
    __tablename__ = 'teachers'
    
    teacher_id = db.Column(db.String(20), primary_key=True)  # 教工号（主键）
    name = db.Column(db.String(100), nullable=False)  # 姓名
    gender = db.Column(db.String(10), nullable=True)  # 性别
    title = db.Column(db.Text, nullable=True)  # 职称名称（可能有多个值，用分号分隔）
    college = db.Column(db.Text, nullable=True)  # 教师所属学院（可能有多个值，用分号分隔）
    phone = db.Column(db.Text, nullable=True)  # 教师联系电话（可能有多个值，用分号分隔）
    
    # 系统字段
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def __repr__(self):
        return f'<Teacher {self.teacher_id} - {self.name}>'
    
    def add_value(self, field, value):
        """向字段添加新值（处理多值情况）"""
        if not value or str(value).strip() == '' or str(value).lower() == 'nan':
            return
        
        current_value = getattr(self, field)
        if not current_value:
            setattr(self, field, str(value).strip())
        else:
            values = set(current_value.split(';'))
            values.add(str(value).strip())
            setattr(self, field, ';'.join(sorted(values)))

# 场地表
class Venue(db.Model):
    __tablename__ = 'venues'
    
    venue_id = db.Column(db.String(50), primary_key=True)  # 场地编号（主键）
    name = db.Column(db.String(200), nullable=True)  # 场地名称
    category = db.Column(db.String(100), nullable=True)  # 场地类别名称
    campus = db.Column(db.String(100), nullable=True)  # 校区
    floor = db.Column(db.Float, nullable=True)  # 楼层号
    building = db.Column(db.String(200), nullable=True)  # 教学楼
    capacity = db.Column(db.Float, nullable=True)  # 座位数
    
    # 系统字段
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def __repr__(self):
        return f'<Venue {self.venue_id} - {self.name}>'

# 课程表
class Course(db.Model):
    __tablename__ = 'courses'
    
    id = db.Column(db.Integer, primary_key=True)
    course_code = db.Column(db.String(50), nullable=False)  # 课程号
    selection_code = db.Column(db.String(50), nullable=False)  # 选课编号
    
    start_week = db.Column(db.String(50), nullable=True)  # 起始周
    weekday = db.Column(db.Integer, nullable=True)  # 星期几
    class_period = db.Column(db.String(50), nullable=True)  # 上课节次
    
    course_name = db.Column(db.String(200), nullable=False)  # 课程名称
    
    venue_start_week = db.Column(db.String(50), nullable=True)  # 场地上课起始周
    venue_class_period = db.Column(db.String(50), nullable=True)  # 场地上课节次
    
    class_size = db.Column(db.Integer, nullable=True)  # 教学班人数
    class_composition = db.Column(db.Text, nullable=True)  # 教学班组成
    
    credits = db.Column(REAL, nullable=True)  # 学分（与A保持REAL）
    total_hours = db.Column(REAL, nullable=True)  # 总学时（与A保持REAL）
    weekly_hours = db.Column(db.String(50), nullable=True)  # 周学时
    
    offering_college = db.Column(db.String(200), nullable=True)  # 开课学院
    major_composition = db.Column(db.Text, nullable=True)  # 专业组成
    
    enrollment_count = db.Column(db.Integer, nullable=True)  # 选课人数
    
    class_time = db.Column(db.String(200), nullable=True)  # 上课时间
    class_location = db.Column(db.String(200), nullable=True)  # 上课地点
    course_nature = db.Column(db.String(100), nullable=True)  # 课程性质
    
    teacher_id = db.Column(db.String(20), db.ForeignKey('teachers.teacher_id'), nullable=True)  # 教工号
    venue_id = db.Column(db.String(50), db.ForeignKey('venues.venue_id'), nullable=True)  # 场地编号
    
    semester = db.Column(db.String(20), nullable=True)  # 学期
    academic_year = db.Column(db.String(20), nullable=True)  # 学年
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), server_onupdate=db.func.current_timestamp())
    
    # 关系
    teacher = db.relationship('Teacher', backref='courses')
    venue = db.relationship('Venue', backref='courses')
    
    def __repr__(self):
        return f'<Course {self.course_code} - {self.selection_code}>'

# 禁止听课（按课程-用户）
class ListeningBan(db.Model):
    __tablename__ = 'listening_bans'
    
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False)  # 课程ID（外键指向courses.id）
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 用户ID
    
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # 创建人
    
    # 关系
    course = db.relationship('Course', backref='listening_bans')
    user = db.relationship('User', backref='banned_courses', foreign_keys=[user_id])
    creator = db.relationship('User', foreign_keys=[created_by])
    
    __table_args__ = (db.UniqueConstraint('course_id', 'user_id', name='unique_course_user_ban'),)
    
    def __repr__(self):
        return f'<ListeningBan course={self.course_id} user={self.user_id}>'

# 听课禁令（按用户-课程号）
class LectureBan(db.Model):
    __tablename__ = 'lecture_bans'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 用户ID
    course_id = db.Column(db.String(50), db.ForeignKey('courses.course_code'), nullable=False)  # 课程号（外键指向courses.course_code）
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # 创建人
    
    user = db.relationship('User', backref='lecture_bans', foreign_keys=[user_id])
    creator = db.relationship('User', foreign_keys=[created_by])
    
    def __repr__(self):
        return f'<LectureBan user={self.user_id} course={self.course_id}>'

# 课程登记记录（原预约登记，现改为日志型记录）
class CourseRegistration(db.Model):
    __tablename__ = 'course_registrations'
    
    id = db.Column(db.Integer, primary_key=True)
    course_code = db.Column(db.String(50), nullable=False)  # 课程号
    selection_code = db.Column(db.String(50), nullable=False)  # 选课课号
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 用户ID
    listening_info = db.Column(db.Text, nullable=True)  # 登记时的备注信息（如计划听课时间地点）
    
    is_used = db.Column(db.Boolean, default=False)  # 是否已被用于填写表单
    
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    
    user = db.relationship('User', backref='registrations')
    
    # 移除唯一约束，允许重复登记（作为历史记录）
    # __table_args__ = (db.UniqueConstraint('course_code', 'selection_code', 'user_id', name='unique_course_user_reservation'),)
    
    def __repr__(self):
        return f'<CourseRegistration {self.course_code} {self.selection_code} user={self.user_id}>'

# 系统设置：用于保存学期第一周星期一日期等键值
class SystemSetting(db.Model):
    __tablename__ = 'system_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<SystemSetting {self.key}={self.value}>'

    @staticmethod
    def get(key, default=None):
        setting = SystemSetting.query.filter_by(key=key).first()
        return setting.value if setting else default

    @staticmethod
    def set(key, value):
        setting = SystemSetting.query.filter_by(key=key).first()
        if not setting:
            setting = SystemSetting(key=key, value=value)
            db.session.add(setting)
        else:
            setting.value = value
        db.session.commit()
        return setting.value

# 评分记录表（与听课表单一对一）
class ScoreRecord(db.Model):
    __tablename__ = 'score_records'
    
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('lecture_forms.id'), nullable=False, unique=True)
    
    # 冗余总分字段，便于快速查询
    total_department_score = db.Column(db.Float, default=0.0)
    total_personal_score = db.Column(db.Float, default=0.0)
    
    reviewer_id = db.Column(db.Integer, db.ForeignKey('users.id')) # 评分人
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    form = db.relationship('LectureForm', backref=db.backref('score_record', uselist=False))
    reviewer = db.relationship('User', backref='given_scores', foreign_keys=[reviewer_id])
    items = db.relationship('ScoreItem', backref='score_record', cascade='all, delete-orphan')

# 评分项表（与评分记录一对多）
class ScoreItem(db.Model):
    __tablename__ = 'score_items'
    
    id = db.Column(db.Integer, primary_key=True)
    score_record_id = db.Column(db.Integer, db.ForeignKey('score_records.id'), nullable=False)
    
    reason = db.Column(db.String(255), nullable=False) # 评分原因（如“修改了教师姓名”）
    department_score = db.Column(db.Float, default=0.0) # 部门扣分
    personal_score = db.Column(db.Float, default=0.0) # 个人扣分
    
    is_auto_generated = db.Column(db.Boolean, default=False) # 是否由系统自动生成（基于修改记录）
    
    created_at = db.Column(db.DateTime, default=datetime.now)


class ManualAssessmentRecord(db.Model):
    __tablename__ = 'manual_assessment_records'

    id = db.Column(db.Integer, primary_key=True)
    listener_number = db.Column(db.String(50), nullable=False, index=True)
    teacher_name = db.Column(db.String(100), nullable=False)
    course_title = db.Column(db.String(200), nullable=False)
    issue_detail = db.Column(db.Text, nullable=False)
    department_score = db.Column(db.Float, default=0.0)
    personal_score = db.Column(db.Float, default=0.0)
    assessment_date = db.Column(db.DateTime, nullable=False, index=True)
    imported_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.now)

    importer = db.relationship('User', backref='manual_assessment_records', foreign_keys=[imported_by])


class StatisticsSnapshot(db.Model):
    __tablename__ = 'statistics_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    snapshot_type = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    filters_json = db.Column(db.Text, nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    creator = db.relationship('User', backref='statistics_snapshots', foreign_keys=[created_by])


class PersonnelMovementRecord(db.Model):
    __tablename__ = 'personnel_movement_records'

    id = db.Column(db.Integer, primary_key=True)
    target_user_id = db.Column(db.Integer, nullable=True, index=True)
    target_user_number = db.Column(db.String(20), nullable=True, index=True)
    target_user_name = db.Column(db.String(100), nullable=False)
    department_name = db.Column(db.String(50), nullable=True, index=True)
    group_name = db.Column(db.String(50), nullable=True, index=True)
    action_type = db.Column(db.String(50), nullable=False, index=True)
    summary = db.Column(db.String(255), nullable=False)
    details_json = db.Column(db.Text, nullable=True)
    operator_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    operator = db.relationship('User', backref='personnel_movement_records', foreign_keys=[operator_user_id])


class AssessmentOverride(db.Model):
    """考核规则覆盖表 — 用于设定特殊考核方式（如免于考核、自定义需交表数等）"""
    __tablename__ = 'assessment_overrides'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    start_week = db.Column(db.Integer, nullable=False)     # 生效起始周（教学周编号）
    end_week = db.Column(db.Integer, nullable=False)       # 生效结束周（教学周编号）

    # 规则类型：标识不同的特殊考核方式
    # 当前支持：'exempt'（免于考核）、'leave'（请假）
    # 未来可扩展：'custom_requirement'（自定义需交表数）、'partial_exempt'（部分减免）等
    override_type = db.Column(db.String(50), nullable=False, default='exempt')

    # 规则参数值（JSON 格式，不同 type 有不同含义）
    # 'exempt': 不使用此字段（可为 null）
    # 'custom_requirement': 存储自定义需交表数，如 '{"required_submission": 0}'
    # 未来扩展时可存储更复杂的参数
    override_value = db.Column(db.Text, nullable=True)

    reason = db.Column(db.String(200), nullable=False)     # 规则理由（必填）
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    user = db.relationship('User', foreign_keys=[user_id], backref='assessment_overrides')
    creator = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.UniqueConstraint('user_id', 'start_week', 'end_week', 'override_type',
                            name='unique_user_week_override'),
    )

    def __repr__(self):
        return f'<AssessmentOverride user={self.user_id} type={self.override_type} weeks={self.start_week}-{self.end_week}>'
