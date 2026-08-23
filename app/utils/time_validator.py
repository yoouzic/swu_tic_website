"""
时间验证工具模块
用于验证听课时间是否在未来
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple


class TimeValidator:
    """时间验证器"""
    
    # 中文数字映射
    CHINESE_NUMBERS = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15, '十六': 16, '十七': 17, '十八': 18,
        '十九': 19, '二十': 20
    }
    
    # 星期映射
    WEEKDAY_MAP = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 7, '天': 7
    }

    @classmethod
    def get_teaching_calendar_settings(cls) -> Tuple[Optional[datetime], int]:
        from ..models import SystemSetting
        first_week_setting = SystemSetting.query.filter_by(key='teaching_first_week_monday').first()
        week_start_setting = SystemSetting.query.filter_by(key='teaching_week_start_day').first()

        first_week = None
        if first_week_setting and first_week_setting.value:
            try:
                first_week = datetime.strptime(first_week_setting.value, '%Y-%m-%d')
            except (TypeError, ValueError):
                first_week = None

        week_start_day = 0
        if week_start_setting and week_start_setting.value is not None:
            try:
                parsed = int(week_start_setting.value)
            except (TypeError, ValueError):
                parsed = 0
            week_start_day = parsed if 0 <= parsed <= 6 else 0
        return first_week, week_start_day

    @classmethod
    def _get_total_weeks(cls) -> int:
        from ..models import SystemSetting
        try:
            raw = SystemSetting.get('teaching_total_weeks', '20') or 20
            parsed = int(raw)
        except (TypeError, ValueError):
            return 20
        return parsed if 1 <= parsed <= 52 else 20
    
    @classmethod
    def parse_chinese_number(cls, chinese_str: str) -> Optional[int]:
        """解析中文数字"""
        chinese_str = chinese_str.strip()
        
        # 直接查找映射
        if chinese_str in cls.CHINESE_NUMBERS:
            return cls.CHINESE_NUMBERS[chinese_str]
        
        # 处理阿拉伯数字
        if chinese_str.isdigit():
            return int(chinese_str)
        
        return None
    
    @classmethod
    def parse_listening_time(cls, listening_info: str) -> Optional[Tuple[int, int]]:
        """
        解析听课时间信息，提取周数和星期
        
        Args:
            listening_info: 听课信息字符串，如"第5周星期三第3-4节，在教学楼A101教室听课"
            
        Returns:
            Tuple[int, int]: (周数, 星期几) 或 None
        """
        try:
            # 匹配周数的正则表达式
            week_patterns = [
                r'第([一二三四五六七八九十\d]+)周',
                r'([一二三四五六七八九十\d]+)周',
                r'第([一二三四五六七八九十\d]+)星期',
            ]
            
            # 匹配星期的正则表达式
            weekday_patterns = [
                r'星期([一二三四五六七日天])',
                r'周([一二三四五六七日天])',
            ]
            
            week_num = None
            weekday_num = None
            
            # 提取周数
            for pattern in week_patterns:
                match = re.search(pattern, listening_info)
                if match:
                    week_str = match.group(1)
                    week_num = cls.parse_chinese_number(week_str)
                    if week_num:
                        break
            
            # 提取星期
            for pattern in weekday_patterns:
                match = re.search(pattern, listening_info)
                if match:
                    weekday_str = match.group(1)
                    weekday_num = cls.WEEKDAY_MAP.get(weekday_str)
                    if weekday_num:
                        break
            
            if week_num and weekday_num:
                return (week_num, weekday_num)
            
            return None
            
        except Exception:
            return None
    
    @classmethod
    def calculate_target_date(cls, week_num: int, weekday_num: int, 
                            semester_start: Optional[datetime] = None) -> Optional[datetime]:
        """
        计算目标日期
        
        Args:
            week_num: 周数
            weekday_num: 星期几 (1-7, 1为星期一)
            semester_start: 学期开始日期，如果为None则使用当前学期的估算开始日期
            
        Returns:
            datetime: 目标日期或None
        """
        try:
            if not semester_start:
                semester_start, week_start_day = cls.get_teaching_calendar_settings()
                total_weeks = cls._get_total_weeks()
            else:
                # Explicit semester_start is treated as the reference Monday and
                # keeps the legacy Monday-only behavior; total_weeks is not
                # available from this argument and is not enforced.
                week_start_day = 0
                total_weeks = None
            if not semester_start:
                return None
            if total_weeks is not None and (week_num < 1 or week_num > total_weeks):
                return None
            days_to_subtract = (semester_start.weekday() - week_start_day) % 7
            first_week_start = semester_start - timedelta(days=days_to_subtract)
            target_week_start = first_week_start + timedelta(weeks=week_num - 1)
            target_python_weekday = weekday_num - 1
            offset = (target_python_weekday - week_start_day) % 7
            target_date = target_week_start + timedelta(days=offset)
            
            return target_date
            
        except Exception:
            return None
    
    @classmethod
    def validate_future_time(cls, listening_info: str, 
                           advance_days: int = 0) -> Tuple[bool, str]:
        """
        验证听课时间是否在未来
        
        Args:
            listening_info: 听课信息字符串
            advance_days: 提前天数，默认1天
            
        Returns:
            Tuple[bool, str]: (是否有效, 错误信息)
        """
        try:
            # 解析时间信息
            time_info = cls.parse_listening_time(listening_info)
            if not time_info:
                return False, "无法解析听课时间信息，请使用格式：第X周星期X第X节"
            
            week_num, weekday_num = time_info
            
            # 计算目标日期
            target_date = cls.calculate_target_date(week_num, weekday_num)
            if not target_date:
                return False, "无法计算听课日期"
            
            # 检查是否在未来
            now = datetime.now()
            min_date = now + timedelta(days=advance_days)
            
            if target_date.date() < min_date.date():
                return False, f"听课时间必须在{advance_days}天后，您选择的时间是{target_date.strftime('%Y年%m月%d日')}"
            
            # 检查是否过于遥远（比如超过一年）
            max_date = now + timedelta(days=365)
            if target_date > max_date:
                return False, f"听课时间不能超过一年，您选择的时间是{target_date.strftime('%Y年%m月%d日')}"
            
            return True, ""
            
        except Exception as e:
            return False, f"时间验证出错：{str(e)}"
    
    @classmethod
    def get_time_suggestion(cls) -> str:
        """获取时间填写建议"""
        now = datetime.now()
        first_week, week_start_day = cls.get_teaching_calendar_settings()
        if not first_week:
            return "请先在系统听课制度设置中配置教学周起始时间"
        days_to_subtract = (first_week.weekday() - week_start_day) % 7
        first_week_start = first_week - timedelta(days=days_to_subtract)
        current_week = ((now.date() - first_week_start.date()).days // 7) + 1
        next_week_num = max(1, current_week + 1)
        next_week = now + timedelta(days=7)
        
        weekdays = ['一', '二', '三', '四', '五', '六', '日']
        next_weekday = weekdays[next_week.weekday()]
        
        return f"建议格式：第{next_week_num}周星期{next_weekday}第3-4节，在教学楼A101教室听课"


def validate_listening_time(listening_info: str) -> Tuple[bool, str]:
    """
    验证听课时间的便捷函数
    
    Args:
        listening_info: 听课信息字符串
        
    Returns:
        Tuple[bool, str]: (是否有效, 错误信息或建议)
    """
    return TimeValidator.validate_future_time(listening_info)
