# -*- coding: utf-8 -*-
"""Legacy-compatible schedule matcher extracted from AutoReviewEngine.

This module is the single authority for the frozen legacy matcher algorithm.
AutoReviewEngine and any future orchestration service delegate here; the
algorithm body must not be modified.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def normalize_weekday(weekday_str: str) -> str:
    """将数字星期转换为中文星期，如 '1' -> '一'"""
    try:
        if weekday_str.isdigit():
            num = int(weekday_str)
            mapping = {1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '日'}
            cn_char = mapping.get(num)
            return cn_char if cn_char else weekday_str
        return weekday_str
    except Exception:
        return weekday_str


def normalize_class_period(period_str: str) -> str:
    """将课表中的节次格式转换为标准格式，如 '1-2节' -> '第1-2节'"""
    if not period_str:
        return period_str
    if period_str.startswith('第'):
        return period_str
    if period_str.endswith('节'):
        return f'第{period_str}'
    return period_str


def normalize_location(loc: str) -> str:
    """规范场地：去除房间前导零；荣昌格式特殊处理。"""
    if not loc:
        return loc
    try:
        loc_str = loc.strip()
        m = re.match(r'^荣昌\s*(\d{4})$', loc_str)
        if m:
            digits = m.group(1)
            building = f"0{digits[0]}"
            room = digits[1:]
            return f"荣昌{building}-{room}"

        parts = loc.split('-')
        if len(parts) == 2:
            building, room = parts
            room_clean = re.sub(r'^0+', '', room) if room else room
            if not room_clean:
                room_clean = '0'
            return f"{building}-{room_clean}"
        return loc
    except Exception:
        return loc


def find_course_in_schedule(
    schedule_df,
    teacher_name: str,
    teacher_college: str,
    course_title: str,
    class_composition: str,
    location: str,
    feedback_weekday_cn: Optional[str] = None,
    feedback_class_period: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """在课表中查找匹配的课程，返回最佳匹配和所有可能匹配。"""
    if schedule_df is None:
        return None, []

    try:
        all_matches: List[Dict[str, Any]] = []

        # 1. 精确匹配：教师姓名 + 课程名称
        if teacher_name and course_title:
            exact_matches = schedule_df[
                (schedule_df['姓名'] == teacher_name)
                & (schedule_df['课程名称'] == course_title)
            ]
            if len(exact_matches) > 0:
                for _, row in exact_matches.iterrows():
                    all_matches.append({
                        'course_name': str(row['课程名称']),
                        'teacher_name': str(row['姓名']),
                        'teacher_college': str(row['教师所属学院']),
                        'weekday': normalize_weekday(str(row['星期几'])),
                        'class_period': normalize_class_period(str(row['上课节次'])),
                        'location': str(row['场地名称']),
                        'class_composition': str(row['教学班组成']),
                        'start_week': str(row['起始周']),
                        'match_type': 'exact',
                    })

        # 2. 如果精确匹配失败，尝试教师姓名匹配
        if not all_matches and teacher_name:
            teacher_matches = schedule_df[schedule_df['姓名'] == teacher_name]
            if len(teacher_matches) > 0:
                for _, row in teacher_matches.iterrows():
                    all_matches.append({
                        'course_name': str(row['课程名称']),
                        'teacher_name': str(row['姓名']),
                        'teacher_college': str(row['教师所属学院']),
                        'weekday': normalize_weekday(str(row['星期几'])),
                        'class_period': normalize_class_period(str(row['上课节次'])),
                        'location': str(row['场地名称']),
                        'class_composition': str(row['教学班组成']),
                        'start_week': str(row['起始周']),
                        'match_type': 'teacher_name',
                    })

        # 3. 如果还是没有匹配，尝试课程名称匹配
        if not all_matches and course_title:
            course_matches = schedule_df[schedule_df['课程名称'] == course_title]
            if len(course_matches) > 0:
                for _, row in course_matches.iterrows():
                    all_matches.append({
                        'course_name': str(row['课程名称']),
                        'teacher_name': str(row['姓名']),
                        'teacher_college': str(row['教师所属学院']),
                        'weekday': normalize_weekday(str(row['星期几'])),
                        'class_period': normalize_class_period(str(row['上课节次'])),
                        'location': str(row['场地名称']),
                        'class_composition': str(row['教学班组成']),
                        'start_week': str(row['起始周']),
                        'match_type': 'course_name',
                    })

        # 4. 优先按星期几与节次筛选
        if all_matches and (feedback_weekday_cn or feedback_class_period):
            time_matches = []
            for match in all_matches:
                ok_weekday = True
                ok_period = True
                if feedback_weekday_cn:
                    ok_weekday = (match.get('weekday') == feedback_weekday_cn)
                if feedback_class_period:
                    match_period = normalize_class_period(match.get('class_period') or '')
                    wanted_period = normalize_class_period(feedback_class_period)
                    ok_period = (match_period == wanted_period)
                if ok_weekday and ok_period:
                    match['match_type'] += '_with_time'
                    time_matches.append(match)
            if time_matches:
                all_matches = time_matches

        # 5. 进一步筛选：如果有地点信息，优先匹配地点
        if all_matches and location:
            normalized_loc = normalize_location(location)
            location_matches = []
            for match in all_matches:
                if normalize_location(match['location']) == normalized_loc:
                    match['match_type'] += '_with_location'
                    location_matches.append(match)
            if location_matches:
                all_matches = location_matches

        # 6. 进一步筛选：如果有教学班组成信息，优先匹配
        if all_matches and class_composition:
            class_matches = []
            for match in all_matches:
                if class_composition in match['class_composition']:
                    match['match_type'] += '_with_class'
                    class_matches.append(match)
            if class_matches:
                all_matches = class_matches

        best_match = all_matches[0] if all_matches else None
        return best_match, all_matches

    except Exception as e:
        logger.error("课表匹配失败: %s", e)
        return None, []
