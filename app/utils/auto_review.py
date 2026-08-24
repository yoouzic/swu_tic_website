"""Legacy compatibility engine.

System-level legacy upload/settings/report endpoints have been retired.
The engine remains for the legacy immediate form check and reference-data
lookup endpoints in admin/review.py. Do not add new consumers.
"""
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
try:
    import pycorrector
except Exception:
    pycorrector = None
from flask import current_app

from ..models import db, LectureForm, Teacher, Course, SystemSetting, User, ScoreRecord, ScoreItem
from ..services.legacy_review_compat import (
    get_legacy_review_week_no,
    resolve_legacy_semester_monday,
)
from ..services.review_schedule_source import (
    CANONICAL_SNAPSHOT,
    LEGACY_FALLBACK_ELIGIBLE,
    NONE,
    resolve_review_schedule_source,
)
from ..services.review_contacts import (
    find_reviewer_by_id as _canonical_find_reviewer_by_id,
    find_reviewer_by_name as _canonical_find_reviewer_by_name,
)
from .audit_tags import is_auto_review_allowed
from .env_config import env_path
from .time_validator import TimeValidator
from .user_status import active_user_filter, is_user_active


DEFAULT_SCHEDULE_PATH = env_path('AUTO_REVIEW_DEFAULT_SCHEDULE_PATH', os.path.join('data', 'storage', 'templates', 'auto_review', '2025-2026-1课表.xlsx'))
SETTING_KEY_SEMESTER_MONDAY = 'semester_first_monday'
SETTING_KEY_SCHEDULE_PATH = 'auto_review_schedule_path'

# 有效学院列表（根据审核要求）
VALID_COLLEGES = [
    '计算机与信息科学学院、软件学院', '体育学院', '工程技术学院', '外国语学院',
    '化学化工学院', '教师教育学院', '马克思主义学院', '食品科学学院', '动物科学技术学院',
    '经济管理学院', '蚕桑纺织与生物质科学学院', '生命科学学院', '农学与生物科技学院',
    '园艺园林学院', '资源环境学院', '美术学院', '数学与统计学院', '药学院、中医药学院',
    '植物保护学院', '材料与能源学院', '国家治理学院', '地理科学学院', '电子信息工程学院',
    '法学院', '物理科学与技术学院', '教育学部', '心理学部', '人工智能学院', '文学院',
    '历史文化学院、民族学院', '新闻传媒学院', '音乐学院', '西塔学院', '动物医学院',
    '含弘学院', '水产学院','商贸学院'
]

# 部门组别映射（根据审核要求）
DEPARTMENT_GROUP_MAP = {
    '办公部': '1',
    '策划部': '2', 
    '技术部': '3',
    '宣传部': '4',
    '荣昌办公部': '5',
    '荣昌策划部': '5',
    '荣昌技术部': '5',
    '荣昌设计部': '5'
}

# 有效教学方法
VALID_TEACHING_METHODS = ['PPT演示法', '讲授法', '师生互动法']

# 有效量表值
VALID_SCALE_VALUES = ['非常好', '好', '一般']
VALID_CASE_VALUES = ['推荐', '不推荐']


def _exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except Exception:
        return False


def _read_excel(path: str, sheet_name: Any = 0) -> Optional[pd.DataFrame]:
    if not _exists(path):
        return None
    try:
        if path.lower().endswith('.xls'):
            # 尝试多种方式读取.xls文件
            try:
                df = pd.read_excel(path, sheet_name=sheet_name, engine='xlrd')
            except:
                try:
                    df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')
                except:
                    df = pd.read_excel(path, sheet_name=sheet_name)
        else:
            df = pd.read_excel(path, sheet_name=sheet_name)  # openpyxl for .xlsx
        return df
    except Exception as e:
        current_app.logger.error(f"读取Excel文件失败 {path}: {e}")
        return None


class AutoReviewEngine:
    def __init__(self,
                 schedule_path: Optional[str] = None,
                 semester_monday: Optional[str] = None):
        # Explicit schedule_path always wins: it is a test/tool injection path
        # and must continue to read the legacy Excel file directly.
        self.schedule_source_kind = NONE
        if schedule_path:
            self.schedule_path = schedule_path
            self.schedule_df = _read_excel(self.schedule_path)
            self.schedule_source_kind = 'explicit_legacy' if self.schedule_df is not None else NONE
        else:
            resolution = resolve_review_schedule_source()
            if resolution.kind == CANONICAL_SNAPSHOT:
                self.schedule_path = None
                self.schedule_df = resolution.dataframe
                self.schedule_source_kind = CANONICAL_SNAPSHOT
            elif resolution.kind == LEGACY_FALLBACK_ELIGIBLE:
                # Current semester is unset: the only canonical non-READY state
                # allowed to consult the historical legacy Excel compatibility
                # file.  Configured-but-missing/ambiguous/invalid states never
                # reach this branch.
                schedule_config = SystemSetting.get(SETTING_KEY_SCHEDULE_PATH)
                self.schedule_path = schedule_config or DEFAULT_SCHEDULE_PATH
                if not _exists(self.schedule_path) and _exists(DEFAULT_SCHEDULE_PATH):
                    self.schedule_path = DEFAULT_SCHEDULE_PATH
                self.schedule_df = _read_excel(self.schedule_path)
                self.schedule_source_kind = 'legacy_fallback' if self.schedule_df is not None else NONE
            else:
                self.schedule_path = None
                self.schedule_df = None
                self.schedule_source_kind = NONE

        self._explicit_semester_monday = semester_monday or None
        self.semester_monday_str = semester_monday or resolve_legacy_semester_monday()
        self.semester_monday = None
        if self.semester_monday_str:
            try:
                # 支持 YYYY-MM-DD
                self.semester_monday = datetime.strptime(self.semester_monday_str, '%Y-%m-%d').date()
            except Exception:
                self.semester_monday = None
        self.time_validator = TimeValidator()
        self._pycorrector_unavailable_logged = False
        self._pycorrector_runtime_disabled = False

    @staticmethod
    def _weekday_from_cn_char(c: str) -> Optional[int]:
        mapping = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 7}
        return mapping.get(c)
    
    @staticmethod
    def _weekday_to_cn_char(num: int) -> Optional[str]:
        mapping = {1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '日'}
        return mapping.get(num)
    
    @staticmethod
    def _normalize_weekday(weekday_str: str) -> str:
        """将数字星期转换为中文星期，如 '1' -> '一'"""
        try:
            if weekday_str.isdigit():
                num = int(weekday_str)
                cn_char = AutoReviewEngine._weekday_to_cn_char(num)
                return cn_char if cn_char else weekday_str
            return weekday_str
        except:
            return weekday_str
    
    @staticmethod
    def _normalize_class_period(period_str: str) -> str:
        """将课表中的节次格式转换为标准格式，如 '1-2节' -> '第1-2节'"""
        if not period_str:
            return period_str
        
        # 如果已经有"第"，直接返回
        if period_str.startswith('第'):
            return period_str
        
        # 如果以"节"结尾，在前面加"第"
        if period_str.endswith('节'):
            return f'第{period_str}'
        
        # 其他情况直接返回
        return period_str

    @staticmethod
    def _normalize_location(loc: str) -> str:
        """规范场地：
        - 针对通用格式 "楼-房间" 去除房间号前导零，例如 33-0201 -> 33-201
        - 新增：将 "荣昌abcd"（a,b,c,d为数字）规范为 "荣昌0a-bcd"（不移除bcd的前导零）
        """
        if not loc:
            return loc
        try:
            # 特例：荣昌校区的场地格式，如 "荣昌abcd" -> "荣昌0a-bcd"
            loc_str = loc.strip()
            m = re.match(r'^荣昌\s*(\d{4})$', loc_str)
            if m:
                digits = m.group(1)
                building = f"0{digits[0]}"
                room = digits[1:]  # 保留原始bcd，不去除前导零
                return f"荣昌{building}-{room}"

            parts = loc.split('-')
            if len(parts) == 2:
                building, room = parts
                # 去除房间号前导零，但保留其他字符
                room_clean = re.sub(r'^0+', '', room) if room else room
                if not room_clean:  # 如果全是0，保留一个0
                    room_clean = '0'
                return f"{building}-{room_clean}"
            return loc
        except Exception:
            return loc

    @staticmethod
    def _starts_with_teacher(text: str) -> bool:
        return (text or '').startswith('该老师')

    @staticmethod
    def _split_methods(text: str) -> List[str]:
        if not text:
            return []
        for sep in ['、', '，', ',', ';', '；']:
            text = text.replace(sep, '|')
        return [t.strip() for t in text.split('|') if t.strip()]

    def _find_reviewer_by_id(self, reviewer_id: str) -> Optional[Dict[str, Any]]:
        """根据编号在 canonical User 通讯录中查找反馈人"""
        return _canonical_find_reviewer_by_id(reviewer_id)

    def _find_reviewer_by_name(self, name: str, fuzzy: bool = True) -> Optional[Dict[str, Any]]:
        """根据姓名在 canonical User 通讯录中查找反馈人，支持模糊匹配"""
        return _canonical_find_reviewer_by_name(name, fuzzy=fuzzy)

    def _find_course_in_schedule(self, teacher_name: str, teacher_college: str, 
                                course_title: str, class_composition: str, 
                                location: str, feedback_weekday_cn: Optional[str] = None,
                                feedback_class_period: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """在课表中查找匹配的课程，返回最佳匹配和所有可能匹配。
        优先使用反馈中的星期几与节次进行筛选。"""
        if self.schedule_df is None:
            return None, []
        
        try:
            # 逐步筛选，从最严格到最宽松
            all_matches = []
            
            # 1. 精确匹配：教师姓名 + 课程名称
            if teacher_name and course_title:
                exact_matches = self.schedule_df[
                    (self.schedule_df['姓名'] == teacher_name) & 
                    (self.schedule_df['课程名称'] == course_title)
                ]
                if len(exact_matches) > 0:
                    for _, row in exact_matches.iterrows():
                        match_info = {
                            'course_name': str(row['课程名称']),
                            'teacher_name': str(row['姓名']),
                            'teacher_college': str(row['教师所属学院']),
                            'weekday': self._normalize_weekday(str(row['星期几'])),
                            'class_period': self._normalize_class_period(str(row['上课节次'])),
                            'location': str(row['场地名称']),
                            'class_composition': str(row['教学班组成']),
                            'start_week': str(row['起始周']),
                            'match_type': 'exact'
                        }
                        all_matches.append(match_info)
            
            # 2. 如果精确匹配失败，尝试教师姓名匹配
            if not all_matches and teacher_name:
                teacher_matches = self.schedule_df[self.schedule_df['姓名'] == teacher_name]
                if len(teacher_matches) > 0:
                    for _, row in teacher_matches.iterrows():
                        match_info = {
                            'course_name': str(row['课程名称']),
                            'teacher_name': str(row['姓名']),
                            'teacher_college': str(row['教师所属学院']),
                            'weekday': self._normalize_weekday(str(row['星期几'])),
                            'class_period': self._normalize_class_period(str(row['上课节次'])),
                            'location': str(row['场地名称']),
                            'class_composition': str(row['教学班组成']),
                            'start_week': str(row['起始周']),
                            'match_type': 'teacher_name'
                        }
                        all_matches.append(match_info)
            
            # 3. 如果还是没有匹配，尝试课程名称匹配
            if not all_matches and course_title:
                course_matches = self.schedule_df[self.schedule_df['课程名称'] == course_title]
                if len(course_matches) > 0:
                    for _, row in course_matches.iterrows():
                        match_info = {
                            'course_name': str(row['课程名称']),
                            'teacher_name': str(row['姓名']),
                            'teacher_college': str(row['教师所属学院']),
                            'weekday': self._normalize_weekday(str(row['星期几'])),
                            'class_period': self._normalize_class_period(str(row['上课节次'])),
                            'location': str(row['场地名称']),
                            'class_composition': str(row['教学班组成']),
                            'start_week': str(row['起始周']),
                            'match_type': 'course_name'
                        }
                        all_matches.append(match_info)
            
            # 4. 优先按星期几与节次筛选
            if all_matches and (feedback_weekday_cn or feedback_class_period):
                time_matches = []
                for match in all_matches:
                    ok_weekday = True
                    ok_period = True
                    if feedback_weekday_cn:
                        ok_weekday = (match.get('weekday') == feedback_weekday_cn)
                    if feedback_class_period:
                        match_period = self._normalize_class_period(match.get('class_period') or '')
                        wanted_period = self._normalize_class_period(feedback_class_period)
                        ok_period = (match_period == wanted_period)
                    if ok_weekday and ok_period:
                        match['match_type'] += '_with_time'
                        time_matches.append(match)
                if time_matches:
                    all_matches = time_matches

            # 5. 进一步筛选：如果有地点信息，优先匹配地点
            if all_matches and location:
                normalized_loc = self._normalize_location(location)
                location_matches = []
                for match in all_matches:
                    if self._normalize_location(match['location']) == normalized_loc:
                        match['match_type'] += '_with_location'
                        location_matches.append(match)
                if location_matches:
                    all_matches = location_matches
            
            # 5. 进一步筛选：如果有教学班组成信息，优先匹配
            if all_matches and class_composition:
                class_matches = []
                for match in all_matches:
                    if class_composition in match['class_composition']:
                        match['match_type'] += '_with_class'
                        class_matches.append(match)
                if class_matches:
                    all_matches = class_matches
            
            # 返回最佳匹配（第一个）和所有匹配
            best_match = all_matches[0] if all_matches else None
            return best_match, all_matches
            
        except Exception as e:
            current_app.logger.error(f"课表匹配失败: {e}")
        
        return None, []

    def _compute_week_from_date(self, d: datetime.date) -> Optional[int]:
        if self._explicit_semester_monday:
            if not self.semester_monday:
                return None
            delta_days = (d - self.semester_monday).days
            if delta_days < 0:
                return None
            return (delta_days // 7) + 1
        return get_legacy_review_week_no(d)

    def _validate_reviewer_identity(self, form_like: Any, issues: List[str], fixes: List[str]) -> Optional[Dict[str, Any]]:
        """验证反馈人身份信息"""
        # 为避免“编号沿用上一行导致学院对应上一人”的问题，编号与姓名同时存在时进行交叉验证
        reviewer_info = None
        reviewer_info_by_id = None
        reviewer_info_by_name = None

        # 1. 读取原始字段
        # 统一使用 listener_number 代表业务上的学号/工号
        listener_number = getattr(form_like, 'listener_number', None) or getattr(form_like, 'listener_id', None)
        
        # 兼容Excel导入时的 reviewer_id 字段（如果它确实存的是学号）
        # 注意：在数据库模型中 reviewer_id 是 User.id (int)，在Excel映射中是"编号" (str)
        if not listener_number:
             raw_reviewer_id = getattr(form_like, 'reviewer_id', None)
             # 简单的启发式判断：如果看起来像学号（长数字字符串），则当作 listener_number
             if raw_reviewer_id and str(raw_reviewer_id).isdigit() and len(str(raw_reviewer_id)) > 5:
                 listener_number = raw_reviewer_id
        
        listener_name = getattr(form_like, 'listener_name', None)

        # 2. 提取姓名与学院（用于后续一致性校验）
        name_part = None
        college_part = None
        if listener_name:
            if '（' in listener_name and '）' in listener_name:
                name_part = listener_name.split('（')[0].strip()
                college_part = listener_name.split('（')[1].split('）')[0].strip()
            else:
                issues.append('听课人姓名格式错误：应为"姓名（学院）"的格式')

        # 3. 根据编号查找反馈人
        if listener_number:
            reviewer_info_by_id = self._find_reviewer_by_id(str(listener_number))
            if not reviewer_info_by_id:
                issues.append(f'通讯录中未找到编号为{listener_number}的反馈人')

        # 4. 根据姓名查找反馈人（支持模糊匹配）
        if listener_name:
            reviewer_info_by_name = self._find_reviewer_by_name(listener_name, fuzzy=True)
            if not reviewer_info_by_name:
                issues.append(f'通讯录中未找到姓名为"{listener_name}"的反馈人')
            elif reviewer_info_by_name.get('similarity', 1.0) < 1.0:
                issues.append(f'反馈人姓名可能有错别字，通讯录中最接近的是"{reviewer_info_by_name["name"]}"')

        # 5. 决策采用的反馈人信息：优先使用姓名匹配，其次编号匹配
        if reviewer_info_by_name and reviewer_info_by_id:
            # 若编号对应的姓名与填写的姓名不一致，则以姓名匹配为准，避免误用上一行编号
            if name_part and reviewer_info_by_id.get('name') != name_part:
                reviewer_info = reviewer_info_by_name
                issues.append('填写的编号与姓名不一致，已按姓名匹配通讯录信息')
            else:
                reviewer_info = reviewer_info_by_id
        elif reviewer_info_by_name:
            reviewer_info = reviewer_info_by_name
        else:
            reviewer_info = reviewer_info_by_id

        # 6. 验证学院有效性与一致性
        if college_part:
            if college_part not in VALID_COLLEGES:
                issues.append(f'学院"{college_part}"不在有效学院列表中')
            if reviewer_info and reviewer_info.get('college') != college_part:
                issues.append(f'填写的学院"{college_part}"与通讯录中的学院"{reviewer_info["college"]}"不一致')

        # 7. 验证编号一致性（当最终采用的反馈人信息与填写编号不一致时提示）
        if reviewer_info and listener_number and reviewer_info['id'] != str(listener_number):
            issues.append(f'填写的编号{listener_number}与通讯录中的编号{reviewer_info["id"]}不一致')
        
        # 6. 验证联系电话
        phone = getattr(form_like, 'phone', None) or getattr(form_like, 'contact_phone', None)
        if phone:
            if not re.match(r'^1[3-9]\d{9}$', str(phone)):
                issues.append('联系电话格式错误：应为11位手机号码')
        
        return reviewer_info

    def _validate_course_matching(self, form_like: Any, issues: List[str], fixes: List[str]) -> Optional[Dict[str, Any]]:
        """验证课程信息匹配"""
        teacher_name = getattr(form_like, 'teacher_name', None)
        teacher_college = getattr(form_like, 'teacher_college', None)
        course_title = getattr(form_like, 'course_title', None)
        class_composition = getattr(form_like, 'class_composition', None)
        location = getattr(form_like, 'lecture_location', None)
        
        # 在课表中查找匹配的课程（优先使用反馈中的星期与节次筛选）
        lecture_date = getattr(form_like, 'lecture_date', None)
        weekday_cn = None
        if lecture_date:
            s = lecture_date.strip()
            if '星期' in s:
                _, right = s.split('星期', 1)
                weekday_cn = right.strip()[:1] if right else None
        class_period = getattr(form_like, 'class_period', None)

        course_info, all_matches = self._find_course_in_schedule(
            teacher_name, teacher_college, course_title, class_composition, location,
            feedback_weekday_cn=weekday_cn, feedback_class_period=class_period
        )
        
        if not course_info:
            issues.append('无法在课表中确认该课程信息，请检查教师姓名、学院、课程名称、专业年级和地点')
            return None
        elif len(all_matches) > 1:
            # 如果有多个匹配，给出所有可能的选项
            possible_courses = []
            for match in all_matches[:5]:  # 最多显示5个
                possible_courses.append(f"{match['teacher_name']}-{match['course_name']}-星期{match['weekday']}-{match['class_period']}-{match['location']}")
            fixes.append(f'课表中找到{len(all_matches)}个可能匹配的课程：{"; ".join(possible_courses)}')
        
        # 验证各字段是否与课表一致
        if teacher_name and course_info['teacher_name'] != teacher_name:
            issues.append(f'教师姓名不匹配：填写为"{teacher_name}"，课表中为"{course_info["teacher_name"]}"')
        
        if teacher_college and course_info['teacher_college'] != teacher_college:
            issues.append(f'教师学院不匹配：填写为"{teacher_college}"，课表中为"{course_info["teacher_college"]}"')
        
        if course_title and course_info['course_name'] != course_title:
            issues.append(f'课程名称不匹配：填写为"{course_title}"，课表中为"{course_info["course_name"]}"')
        
        return course_info

    def _validate_against_schedule(self, form_like: Any, issues: List[str], fixes: List[str], course_info: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """根据课表验证时间、地点等信息"""
        # 解析日期与星期校验
        lecture_date = getattr(form_like, 'lecture_date', None)
        if not lecture_date:
            issues.append('听课时间不能为空')
            return
        
        date_part = None
        weekday_cn = None
        dt = None
        
        try:
            s = lecture_date.strip()
            if '星期' in s:
                left, right = s.split('星期', 1)
                date_part = left.strip()
                weekday_cn = right.strip()[:1] if right else None
            else:
                date_part = s.strip()
            dt = datetime.strptime(date_part, '%Y/%m/%d').date()
        except Exception:
            issues.append('听课时间格式错误：应为YYYY/MM/DD星期X')
            return None

        # 验证星期是否匹配
        if weekday_cn:
            wd = self._weekday_from_cn_char(weekday_cn)
            if wd and dt.isoweekday() != wd:
                issues.append('听课时间与星期不匹配')

        # 计算周次
        week_num = self._compute_week_from_date(dt)
        week_info = None
        if week_num is None:
            issues.append('未设置学期第一周星期一或听课时间早于该日期')
        else:
            week_info = f'第{week_num}周'

        # 验证节次格式
        class_period = getattr(form_like, 'class_period', None)
        if class_period:
            if not class_period.startswith('第') or '节' not in class_period:
                issues.append('节次格式错误：应为"第X-X节"')
            else:
                try:
                    segment = class_period.replace('第', '').replace('节', '')
                    if '-' in segment:
                        a, b = segment.split('-')
                        a_i, b_i = int(a), int(b)
                        if a_i <= 0 or b_i <= 0 or a_i > b_i or b_i > 14:
                            issues.append('节次范围不正确（应为1-14之间且起止合理）')
                    else:
                        # 单节课
                        period_num = int(segment)
                        if period_num <= 0 or period_num > 14:
                            issues.append('节次范围不正确（应为1-14之间）')
                except Exception:
                    issues.append('节次解析失败：应为"第X-X节"')

        # 地点规范化
        location = getattr(form_like, 'lecture_location', None)
        if location:
            normalized_loc = self._normalize_location(location)
            if normalized_loc != location:
                fixes.append(f'建议地点改为：{normalized_loc}')

        # 与课表信息对比
        if course_info:
            # 验证星期
            if weekday_cn and course_info.get('weekday'):
                course_weekday_cn = course_info['weekday']
                if weekday_cn != course_weekday_cn:
                    issues.append(f'星期不匹配：填写为星期{weekday_cn}，课表中为星期{course_weekday_cn}')
            
            # 验证节次
            if class_period and course_info.get('class_period'):
                if class_period != course_info['class_period']:
                    issues.append(f'节次不匹配：填写为{class_period}，课表中为{course_info["class_period"]}')
            
            # 验证地点
            if location and course_info.get('location'):
                course_location = self._normalize_location(course_info['location'])
                if normalized_loc != course_location:
                    issues.append(f'地点不匹配：填写为{location}，课表中为{course_info["location"]}')
        
        return week_info

    def _validate_teaching_evaluation(self, form_like: Any, issues: List[str], fixes: List[str]) -> None:
        """验证教学评价信息"""
        # 验证主要教学方法
        teaching_method = getattr(form_like, 'teaching_method', None)
        if teaching_method:
            methods = self._split_methods(teaching_method)
            invalid_methods = [m for m in methods if m not in VALID_TEACHING_METHODS]
            if invalid_methods:
                issues.append(f'教学方法包含无效值：{invalid_methods}，有效值为：{VALID_TEACHING_METHODS}')
            if len(methods) > 3:
                issues.append('主要教学方法最多填写三项')
            
            # 检查PPT大写
            if any('ppt' in m.lower() and 'PPT' not in m for m in methods):
                fixes.append('PPT应为大写')

        # 验证量表值
        scale_fields = [
            ('classroom_discipline', '管理课堂纪律'),
            ('classroom_atmosphere', '调动课堂气氛'),
            ('overall_effect', '整体教学效果')
        ]
        
        for field, name in scale_fields:
            value = getattr(form_like, field, None)
            if value and value not in VALID_SCALE_VALUES:
                issues.append(f'{name}的值"{value}"无效，应为：{VALID_SCALE_VALUES}')

        # 验证课件制作质量
        courseware_quality = getattr(form_like, 'courseware_quality', None)
        if courseware_quality:
            # 检查是否使用了PPT
            used_ppt = teaching_method and 'PPT演示法' in teaching_method
            if not used_ppt and courseware_quality != '无':
                issues.append('没有使用PPT演示法时，课件制作质量应填"无"')
            elif used_ppt and courseware_quality not in VALID_SCALE_VALUES:
                issues.append(f'课件制作质量的值"{courseware_quality}"无效，应为：{VALID_SCALE_VALUES}')

        # 验证优质案例推荐
        quality_case = getattr(form_like, 'quality_case', None)
        if quality_case and quality_case not in VALID_CASE_VALUES:
            issues.append(f'优质案例推荐的值"{quality_case}"无效，应为：{VALID_CASE_VALUES}')

        # 验证课程反馈
        course_feedback = getattr(form_like, 'course_feedback', None)
        if course_feedback:
            if len(course_feedback) < 50:
                issues.append('课程反馈字数应大于50字')
            if 'ppt' in course_feedback.lower() and 'PPT' not in course_feedback:
                fixes.append('课程反馈中的PPT应为大写')

        # 验证不足及建议
        suggestions = getattr(form_like, 'suggestions', None)
        # 取消“以该老师开头”的限制；未填写时至少应填写“无”
        if not suggestions or not str(suggestions).strip():
            issues.append('不足及建议未填写，应至少填写“无”')

    def review_one(self, form: LectureForm) -> Dict[str, Any]:
        """审核单个表单"""
        issues = []
        fixes = []
        
        # 验证反馈人身份
        reviewer_info = self._validate_reviewer_identity(form, issues, fixes)
        
        # 验证课程匹配
        course_info = self._validate_course_matching(form, issues, fixes)
        
        # 验证时间地点等信息
        week_info = self._validate_against_schedule(form, issues, fixes, course_info)
        
        # 验证教学评价
        self._validate_teaching_evaluation(form, issues, fixes)
        
        return {
            'form_id': form.id,
            'listener': form.listener_name,
            'teacher': form.teacher_name,
            'course': form.course_title,
            'issues': issues,
            'fixes': fixes,
            'passed': len(issues) == 0,
            'reviewer_info': reviewer_info,
            'course_info': course_info,
            'notes': week_info  # 备注信息
        }

    def review_any(self, form_like: Any) -> Dict[str, Any]:
        """审核任意类型的表单对象"""
        issues = []
        fixes = []
        
        # 验证反馈人身份
        reviewer_info = self._validate_reviewer_identity(form_like, issues, fixes)
        
        # 验证课程匹配
        course_info = self._validate_course_matching(form_like, issues, fixes)
        
        # 验证时间地点等信息
        week_info = self._validate_against_schedule(form_like, issues, fixes, course_info)
        
        # 验证教学评价
        self._validate_teaching_evaluation(form_like, issues, fixes)
        
        return {
            'form_id': getattr(form_like, 'id', ''),
            'listener': getattr(form_like, 'listener_name', ''),
            'teacher': getattr(form_like, 'teacher_name', ''),
            'course': getattr(form_like, 'course_title', ''),
            'issues': issues,
            'fixes': fixes,
            'passed': len(issues) == 0,
            'reviewer_info': reviewer_info,
            'course_info': course_info,
            'notes': week_info,  # 备注信息
            'listener_number': (
                getattr(form_like, 'listener_number', '') 
                or getattr(form_like, 'listener_id', '')
                or getattr(form_like, 'reviewer_id', '') # 兼容Excel版列映射
            )
        }

    def run(self, status_filter: Optional[str] = '待审核') -> Dict[str, Any]:
        """运行自动审核"""
        forms = LectureForm.query.filter_by(status=status_filter).all()
        results = []
        for form in forms:
            results.append(self.review_one(form))
        summary = {
            'total': len(results),
            'passed': sum(1 for r in results if r['passed']),
            'failed': sum(1 for r in results if not r['passed']),
        }
        return {'summary': summary, 'results': results}


    def check_text_errors(self, text: str, enable_typos_check: bool = True) -> List[str]:
        """
        简单的语病和错别字检测
        :param text: 待检测文本
        :param enable_typos_check: 是否启用 pycorrector 错别字检测（资源消耗较大）
        """
        issues = []
        if not text:
            return issues
            
        # 1. 检查重复字 (如 "的", "了" 等虚词重复)
        repeated = re.findall(r'([\u4e00-\u9fa5])\1', text)
        for char in repeated:
            if char in '的地得了着过':
                issues.append(f'可能存在重复字："{char}{char}"')
                
        # 2. 使用 pycorrector 进行错别字检测
        if enable_typos_check and not self._pycorrector_runtime_disabled:
            try:
                correct_func = None
                if pycorrector:
                    top_level_correct = getattr(pycorrector, 'correct', None)
                    if callable(top_level_correct):
                        correct_func = top_level_correct
                    else:
                        corrector_cls = getattr(pycorrector, 'Corrector', None)
                        if callable(corrector_cls):
                            corrector_instance = corrector_cls()
                            instance_correct = getattr(corrector_instance, 'correct', None)
                            if callable(instance_correct):
                                correct_func = instance_correct
                if callable(correct_func):
                    _, detail = correct_func(text)
                    for wrong, right, begin, end in detail:
                        issues.append(f'疑似错别字："{wrong}" 应为 "{right}"')
                elif not self._pycorrector_unavailable_logged:
                    current_app.logger.warning("pycorrector.correct 不可用，已跳过错别字检测")
                    self._pycorrector_unavailable_logged = True
            except Exception as e:
                err_msg = str(e)
                err_lower = err_msg.lower()
                if 'kenlm' in err_lower or 'dependencies are not fully installed' in err_lower or 'statistical language model' in err_lower:
                    self._pycorrector_runtime_disabled = True
                    if not self._pycorrector_unavailable_logged:
                        current_app.logger.warning(f"pycorrector 依赖不完整，已跳过错别字检测: {err_msg}")
                        self._pycorrector_unavailable_logged = True
                else:
                    current_app.logger.error(f"错别字检测失败: {e}")
                
        # 3. 检查标点符号 (中文文本中使用英文标点)
        if re.search(r'[\u4e00-\u9fa5],[^0-9]', text): # 中文后跟英文逗号且非数字
            issues.append('中文文本中可能使用了英文逗号')
            
        return issues
    def batch_review_db_forms(
        self,
        form_ids: List[int],
        reviewer_id: int,
        force_all: bool = False,
        target_status: Optional[str] = None
    ) -> Dict[str, Any]:
        """批量自动审核数据库中的表单"""
        forms = LectureForm.query.filter(LectureForm.id.in_(form_ids)).all()
        
        processed_count = 0
        ignored_count = 0
        results = []
        
        # 获取审核人信息（用于权限判断）
        reviewer = User.query.get(reviewer_id)
        if not reviewer or not is_user_active(reviewer):
            return {'success': False, 'message': '审核人不存在'}

        # 获取是否显示审核详情的设置
        show_details_setting = SystemSetting.query.filter_by(key='teaching_show_auto_review_details').first()
        show_details = show_details_setting.value == 'true' if show_details_setting else True # 默认开启
        
        # 获取是否启用错别字检测的设置
        enable_typos_setting = SystemSetting.query.filter_by(key='teaching_enable_typos_check').first()
        enable_typos = enable_typos_setting.value == 'true' if enable_typos_setting else True # 默认开启
            
        target_status = target_status or ('中心已审核' if reviewer.role == '超级管理员' else '部门已审核')
        processable_statuses = ['待审核', '部门已审核']
        if target_status == '中心已审核':
            processable_statuses.append('中心已审核')
        
        for form in forms:
            if form.status not in processable_statuses:
                ignored_count += 1
                continue
            if not force_all and not is_auto_review_allowed(form.audit_tag):
                ignored_count += 1
                continue

            listener_user = User.query.filter_by(number=form.listener_number).filter(active_user_filter()).first()
            if not listener_user:
                ignored_count += 1
                continue
                
            # 2. 执行审核
            review_result = self.review_one(form)
            
            # 3. 语病检测
            text_fields = [form.course_feedback, form.suggestions, form.abnormal_situation]
            text_issues = []
            for text in text_fields:
                if text:
                    text_issues.extend(self.check_text_errors(str(text), enable_typos_check=enable_typos))
            
            if text_issues:
                review_result['issues'].extend(text_issues)
                review_result['passed'] = False 
            
            unique_id = form.unique_id or form.id
            latest_form = LectureForm.query.filter_by(unique_id=unique_id).order_by(LectureForm.id.desc()).first()
            if not latest_form:
                latest_form = form
            if latest_form and latest_form.id != form.id:
                ignored_count += 1
                continue

            target_form = None
            if latest_form and latest_form.status == target_status:
                target_form = latest_form
            else:
                target_form = LectureForm(
                    registration_id=form.registration_id,
                    listener_name=form.listener_name,
                    listener_number=form.listener_number,
                    teacher_name=form.teacher_name,
                    teacher_college=form.teacher_college,
                    course_title=form.course_title,
                    lecture_date=form.lecture_date,
                    lecture_location=form.lecture_location,
                    class_period=form.class_period,
                    student_grade_class=form.student_grade_class,
                    teaching_method=form.teaching_method,
                    classroom_discipline=form.classroom_discipline,
                    classroom_atmosphere=form.classroom_atmosphere,
                    courseware_quality=form.courseware_quality,
                    overall_effect=form.overall_effect,
                    quality_case=form.quality_case,
                    course_feedback=form.course_feedback,
                    suggestions=form.suggestions,
                    abnormal_situation=form.abnormal_situation,
                    student_signature1=form.student_signature1,
                    contact_phone1=form.contact_phone1,
                    student_signature2=form.student_signature2,
                    contact_phone2=form.contact_phone2,
                    audit_tag=form.audit_tag,
                    unique_id=unique_id,
                    created_at=form.created_at
                )
                db.session.add(target_form)

            target_form.status = target_status
            target_form.reviewer_id = reviewer_id
            target_form.review_time = datetime.now()
            target_form.updated_at = datetime.now()
            if not show_details:
                target_form.review_comment = '无。'
            elif review_result['passed']:
                target_form.review_comment = '自动审核通过'
            else:
                issues_str = '；'.join(review_result['issues'])
                target_form.review_comment = f'发现以下问题：{issues_str}'

            if not form.unique_id:
                form.unique_id = form.id
                db.session.add(form)

            db.session.flush()
            old_score_record = ScoreRecord.query.filter_by(form_id=target_form.id).first()
            if old_score_record:
                db.session.delete(old_score_record)
                db.session.flush()

            score_record = ScoreRecord(
                form_id=target_form.id,
                reviewer_id=reviewer_id
            )
            db.session.add(score_record)
            db.session.flush()
            
            score_item_count = 0
            for issue in review_result['issues']:
                department_score = 0
                personal_score = 0
                if department_score <= 0 and personal_score <= 0:
                    continue
                item = ScoreItem(
                    score_record_id=score_record.id,
                    reason=issue,
                    department_score=department_score,
                    personal_score=personal_score,
                    is_auto_generated=True
                )
                db.session.add(item)
                score_item_count += 1
            if score_item_count == 0:
                db.session.delete(score_record)
                db.session.flush()
            
            processed_count += 1
            
            listener_major = listener_user.major if listener_user else '未知'
            class_comp = form.student_grade_class

            results.append({
                'id': target_form.id,
                'old_id': form.id,
                'score_record_id': score_record.id,
                'listener': form.listener_name,
                'listener_number': form.listener_number,
                'major': listener_major,
                'class_composition': class_comp,
                'passed': review_result['passed'],
                'issues': review_result['issues'],
                'fixes': review_result['fixes']
            })
            
        db.session.commit()
        
        return {
            'success': True,
            'processed': processed_count,
            'ignored': ignored_count,
            'results': results
        }

    def search_reference_data(self, form_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据表单数据搜索参考资料（课表和通讯录）
        返回：
        {
            'schedule_matches': [match1, match2, ...],  # Top 5 matches
            'contact_matches': [match1, match2, ...]
        }
        """
        result = {
            'schedule_matches': [],
            'contact_matches': []
        }

        # 1. 搜索通讯录
        listener_name = form_data.get('listener_name')
        listener_id = form_data.get('listener_number')
        
        # 如果没有 listener_number，尝试从 reviewer_id 获取（兼容旧逻辑，但需小心）
        if not listener_id:
             raw_id = form_data.get('reviewer_id')
             if raw_id and str(raw_id).isdigit() and len(str(raw_id)) > 5:
                 listener_id = raw_id
        
        # 尝试通过ID搜索
        if listener_id:
            contact_by_id = self._find_reviewer_by_id(str(listener_id))
            if contact_by_id:
                contact_by_id['match_type'] = 'id'
                result['contact_matches'].append(contact_by_id)
        
        # 尝试通过姓名搜索
        if listener_name:
            # 如果名字包含学院信息，提取名字部分
            name_part = listener_name
            if '（' in listener_name:
                name_part = listener_name.split('（')[0].strip()
            
            contact_by_name = self._find_reviewer_by_name(name_part, fuzzy=True)
            if contact_by_name:
                # 避免重复添加
                is_duplicate = False
                for existing in result['contact_matches']:
                    if existing['id'] == contact_by_name['id']:
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    contact_by_name['match_type'] = 'name'
                    result['contact_matches'].append(contact_by_name)

        # 2. 搜索课表
        teacher_name = form_data.get('teacher_name')
        teacher_college = form_data.get('teacher_college')
        course_title = form_data.get('course_title')
        class_composition = form_data.get('student_grade_class') or form_data.get('class_composition')
        location = form_data.get('lecture_location')
        
        # 解析时间和节次
        lecture_date = form_data.get('lecture_date')
        weekday_cn = None
        if lecture_date:
            s = str(lecture_date).strip()
            if '星期' in s:
                _, right = s.split('星期', 1)
                weekday_cn = right.strip()[:1] if right else None
        
        class_period = form_data.get('class_period')

        _, all_matches = self._find_course_in_schedule(
            teacher_name, teacher_college, course_title, class_composition, location,
            feedback_weekday_cn=weekday_cn, feedback_class_period=class_period
        )
        
        # 取前5个匹配项
        result['schedule_matches'] = all_matches[:5]
        
        return result
