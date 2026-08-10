"""Recognized historical and current schedule column aliases."""


_COMMON_SCHOOL_ALIASES = {
    'course_title': (
        '课程', '课程名称', '课程名', '课程中文名称', '课程标题',
    ),
    'teacher_name': (
        '教师', '教师姓名', '任课教师', '教师名称',
    ),
    'teacher_college': (
        '教师学院', '教师所在学院', '开课学院', '学院',
    ),
    'teaching_class': (
        '教学班', '教学班组成', '行政班组成', '教学班组成（行政班）',
    ),
    'major': ('专业', '专业组成', '专业名称'),
    'weeks': ('周次', '上课周次', '教学周', '上课周'),
    'weekday': ('星期', '星期几', '上课星期'),
    'periods': ('节次', '上课节次', '第几节', '上课时间', '起止节次'),
    'location': ('地点', '上课地点', '教室', '上课教室'),
}


HISTORICAL_SCHOOL_SCHEDULE_ALIASES = {
    **_COMMON_SCHOOL_ALIASES,
    'course_title': _COMMON_SCHOOL_ALIASES['course_title'] + ('课程中文名',),
    'teacher_name': _COMMON_SCHOOL_ALIASES['teacher_name'] + ('授课教师',),
}

CURRENT_SCHOOL_SCHEDULE_ALIASES = {
    **_COMMON_SCHOOL_ALIASES,
    'teacher_college': _COMMON_SCHOOL_ALIASES['teacher_college'] + ('教师所属学院',),
    'location': _COMMON_SCHOOL_ALIASES['location'] + ('上课场地',),
}

LISTENER_CLASS_MAPPING_ALIASES = {
    'listener_number': ('信息员编号', '信息员号', '信息员编码', '编号'),
    'student_id': ('学号', '学生学号', '学生编号'),
    'admin_class': ('行政班', '行政班级', '班级', '行政班名称'),
}

PERSONAL_SCHEDULE_ALIASES = {
    'listener_number': LISTENER_CLASS_MAPPING_ALIASES['listener_number'],
    'student_id': LISTENER_CLASS_MAPPING_ALIASES['student_id'],
    'course_title': _COMMON_SCHOOL_ALIASES['course_title'],
    'semester': ('学年学期', '学年/学期', '学期', '学年学期名称'),
    'weeks': _COMMON_SCHOOL_ALIASES['weeks'],
    'weekday': _COMMON_SCHOOL_ALIASES['weekday'],
    'periods': _COMMON_SCHOOL_ALIASES['periods'],
}

# Short aliases are kept for callers that do not need to distinguish templates.
SCHOOL_SCHEDULE_ALIASES = _COMMON_SCHOOL_ALIASES
HISTORICAL_ALIASES = HISTORICAL_SCHOOL_SCHEDULE_ALIASES
CURRENT_ALIASES = CURRENT_SCHOOL_SCHEDULE_ALIASES


__all__ = [
    'CURRENT_ALIASES',
    'CURRENT_SCHOOL_SCHEDULE_ALIASES',
    'HISTORICAL_ALIASES',
    'HISTORICAL_SCHOOL_SCHEDULE_ALIASES',
    'LISTENER_CLASS_MAPPING_ALIASES',
    'PERSONAL_SCHEDULE_ALIASES',
    'SCHOOL_SCHEDULE_ALIASES',
]
