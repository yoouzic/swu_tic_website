# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: schedule

from flask import request, jsonify
from app.models import Teacher, Venue, Course, db
from app.security import role_required
from app.utils.env_config import env_path
from app.services.schedule_snapshots import (
    DEFAULT_SOURCE_ROW_OFFSET,
    persist_import_snapshot,
)
from app.services.listening_assistant_schedule import (
    persist_listening_assistant_entries,
)
from app.services.current_courses import ensure_current_course_schema, persist_course_mapping
import hashlib
import math
import pandas as pd
import os
from . import admin_bp
from .shared import allowed_file


DEFAULT_SCHEDULE_TEMPLATE_PATH = os.path.join('data', 'storage', 'templates', 'schedule', '全校课表.xls')


_SCHEDULE_COURSE_KEY_FIELDS = (
    'course_code', 'selection_code', 'start_week', 'weekday', 'class_period',
    'course_name', 'venue_start_week', 'venue_class_period', 'class_size',
    'class_composition', 'credits', 'total_hours', 'offering_college',
    'major_composition', 'enrollment_count', 'weekly_hours', 'class_time',
    'class_location', 'course_nature', 'teacher_id', 'venue_id', 'semester',
    'academic_year',
)


_SCHEDULE_ROW_COLUMNS = {
    'course_code': '课程号',
    'selection_code': '选课课号',
    'start_week': '起始周',
    'weekday': '星期几',
    'class_period': '上课节次',
    'course_name': '课程名称',
    'venue_start_week': '场地上课起始周',
    'venue_class_period': '场地上课节次',
    'class_size': '教学班人数',
    'class_composition': '教学班组成',
    'credits': '学分',
    'total_hours': '总学时',
    'offering_college': '开课学院',
    'major_composition': '专业组成',
    'enrollment_count': '选课人数',
    'weekly_hours': '周学时',
    'class_time': '上课时间',
    'class_location': '上课地点',
    'course_nature': '课程性质',
    'teacher_id': '教工号',
    'venue_id': '场地编号',
    'semester': '学期',
    'academic_year': '学年',
}


_INTEGER_COLUMNS = {'星期几', '教学班人数', '选课人数'}
_NUMERIC_COLUMNS = _INTEGER_COLUMNS | {'楼层号', '座位数', '学分', '总学时'}


def _schedule_numeric_errors(df):
    """Validate business numeric fields; sample dtypes do not define identifiers."""
    errors = []
    for column in df.columns:
        if column not in _NUMERIC_COLUMNS:
            continue
        for index, value in df[column].items():
            if pd.isna(value) or str(value).strip() == '':
                continue
            try:
                number = float(pd.to_numeric(value, errors='raise'))
                if not math.isfinite(number):
                    raise ValueError('non-finite number')
                if column in _INTEGER_COLUMNS and not number.is_integer():
                    raise ValueError('non-integer number')
            except (TypeError, ValueError, OverflowError):
                expected = '整数' if column in _INTEGER_COLUMNS else '数值'
                errors.append(f"第 {int(index) + 2} 行，列 '{column}' 应为{expected}：{value}")
    return errors


@admin_bp.route('/api/export/schedule')
@role_required('超级管理员')
def export_schedule():
    """全校课表导出API（框架性实现）"""
    try:
        # TODO: 实现真正的全校课表导出功能
        return jsonify({
            'success': False,
            'message': '全校课表导出功能正在开发中，敬请期待！'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'导出功能暂不可用：{str(e)}'
        })


@admin_bp.route('/api/schedule/validate', methods=['POST'])
@role_required('超级管理员')
def validate_schedule_format():
    """验证全校课表文件格式"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': '未选择文件'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': '未选择文件'})
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'message': '文件格式不支持，请上传.xls或.xlsx文件'})
        
        # 读取模板文件获取标准列名（改为绝对路径，避免依赖CWD）
        template_path = env_path('SCHEDULE_TEMPLATE_PATH', DEFAULT_SCHEDULE_TEMPLATE_PATH)
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': '模板文件不存在'})
        
        template_df = pd.read_excel(template_path)
        expected_columns = list(template_df.columns)
        
        # 读取上传的文件
        uploaded_df = pd.read_excel(file)
        uploaded_columns = list(uploaded_df.columns)
        
        # 检查列名是否匹配
        if uploaded_columns != expected_columns:
            missing_columns = set(expected_columns) - set(uploaded_columns)
            extra_columns = set(uploaded_columns) - set(expected_columns)
            
            error_msg = "文件格式不匹配：\n"
            if missing_columns:
                error_msg += f"缺少列：{', '.join(missing_columns)}\n"
            if extra_columns:
                error_msg += f"多余列：{', '.join(extra_columns)}\n"
            
            return jsonify({
                'success': False, 
                'message': error_msg,
                'expected_columns': expected_columns,
                'uploaded_columns': uploaded_columns
            })
        
        type_errors = _schedule_numeric_errors(uploaded_df)
        
        if type_errors:
            return jsonify({
                'success': False,
                'message': "数据类型错误：\n" + "\n".join(type_errors),
                'errors': type_errors,
            })
        
        return jsonify({
            'success': True,
            'message': '文件格式验证通过',
            'row_count': len(uploaded_df),
            'column_count': len(uploaded_df.columns)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'验证失败：{str(e)}'})


def _normalized_schedule_value(value):
    if value is None or pd.isna(value):
        return ''
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    # Normalize integral numeric values consistently between Excel rows and ORM columns.
    from numbers import Real
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    text = str(value).strip()
    return '' if text.lower() == 'nan' else text


def _course_row_key(row):
    return tuple(
        _normalized_schedule_value(row.get(_SCHEDULE_ROW_COLUMNS[field]))
        for field in _SCHEDULE_COURSE_KEY_FIELDS
    )


def _course_model_key(course):
    return tuple(_normalized_schedule_value(getattr(course, field, None)) for field in _SCHEDULE_COURSE_KEY_FIELDS)


@admin_bp.route('/api/schedule/import', methods=['POST'])
@role_required('超级管理员')
def import_schedule_data():
    """导入全校课表数据"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': '未选择文件'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': '未选择文件'})
        
        # 先验证格式
        validation_result = validate_schedule_format()
        if not validation_result.get_json().get('success'):
            return validation_result

        # validate_schedule_format 已消费文件流，必须回绕后再次读取。
        file.seek(0)
        source_bytes = file.read()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        file.seek(0)

        # 读取文件
        df = pd.read_excel(file)
        for column in _NUMERIC_COLUMNS:
            if column in df:
                df[column] = pd.to_numeric(df[column], errors='raise')
        ensure_current_course_schema()
        
        # 统计信息
        stats = {
            'total_rows': len(df),
            'teachers_added': 0,
            'teachers_updated': 0,
            'venues_added': 0,
            'venues_updated': 0,
            'courses_added': 0,
            'courses_skipped_duplicate': 0,
            'errors': []
        }
        
        # 处理教师数据
        teacher_data = df[['教工号', '姓名', '性别', '职称名称', '教师所属学院', '教师联系电话']].drop_duplicates(subset=['教工号'])
        
        for _, row in teacher_data.iterrows():
            try:
                teacher_id = str(row['教工号']).strip()
                if not teacher_id or teacher_id == 'nan':
                    continue
                
                teacher = Teacher.query.get(teacher_id)
                if teacher:
                    # 更新现有教师的多值字段
                    teacher.add_value('title', row['职称名称'])
                    teacher.add_value('college', row['教师所属学院'])
                    teacher.add_value('phone', row['教师联系电话'])
                    stats['teachers_updated'] += 1
                else:
                    # 创建新教师
                    teacher = Teacher(
                        teacher_id=teacher_id,
                        name=str(row['姓名']).strip() if pd.notna(row['姓名']) else '',
                        gender=str(row['性别']).strip() if pd.notna(row['性别']) else None,
                        title=str(row['职称名称']).strip() if pd.notna(row['职称名称']) else None,
                        college=str(row['教师所属学院']).strip() if pd.notna(row['教师所属学院']) else None,
                        phone=str(row['教师联系电话']).strip() if pd.notna(row['教师联系电话']) else None
                    )
                    db.session.add(teacher)
                    stats['teachers_added'] += 1
                    
            except Exception as e:
                stats['errors'].append(f"处理教师数据时出错（教工号：{row['教工号']}）：{str(e)}")
        
        # 处理场地数据
        venue_data = df[['场地编号', '场地名称', '场地类别名称', '校区', '楼层号', '教学楼', '座位数']].dropna(subset=['场地编号']).drop_duplicates(subset=['场地编号'])
        
        for _, row in venue_data.iterrows():
            try:
                venue_id = str(row['场地编号']).strip()
                if not venue_id or venue_id == 'nan':
                    continue
                
                venue = Venue.query.get(venue_id)
                if venue:
                    stats['venues_updated'] += 1
                else:
                    venue = Venue(
                        venue_id=venue_id,
                        name=str(row['场地名称']).strip() if pd.notna(row['场地名称']) else None,
                        category=str(row['场地类别名称']).strip() if pd.notna(row['场地类别名称']) else None,
                        campus=str(row['校区']).strip() if pd.notna(row['校区']) else None,
                        floor=float(row['楼层号']) if pd.notna(row['楼层号']) else None,
                        building=str(row['教学楼']).strip() if pd.notna(row['教学楼']) else None,
                        capacity=float(row['座位数']) if pd.notna(row['座位数']) else None
                    )
                    db.session.add(venue)
                    stats['venues_added'] += 1
                    
            except Exception as e:
                stats['errors'].append(f"处理场地数据时出错（场地编号：{row['场地编号']}）：{str(e)}")
        
        # 处理课程数据 - 使用完整自然键幂等插入，保留不同节次/地点/周次的合法课程记录。
        existing_course_keys = {_course_model_key(course): course for course in Course.query.all()}
        imported_courses = {}
        for _, row in df.iterrows():
            try:
                course_code = str(row['课程号']).strip()
                selection_code = str(row['选课课号']).strip() if pd.notna(row['选课课号']) else ''
                
                # 课程号和选课编号都必须存在
                if not course_code or course_code == 'nan' or not selection_code or selection_code == 'nan':
                    stats['errors'].append(f"课程号或选课编号为空（行：{row.name + 2}）")
                    continue
                
                # 直接创建新课程记录，不再查找现有记录
                course = Course(
                    course_code=course_code,
                    selection_code=selection_code,
                    start_week=str(row['起始周']).strip() if pd.notna(row['起始周']) else None,
                    weekday=int(row['星期几']) if pd.notna(row['星期几']) else None,
                    class_period=str(row['上课节次']).strip() if pd.notna(row['上课节次']) else None,
                    course_name=str(row['课程名称']).strip() if pd.notna(row['课程名称']) else '',
                    venue_start_week=str(row['场地上课起始周']).strip() if pd.notna(row['场地上课起始周']) else None,
                    venue_class_period=str(row['场地上课节次']).strip() if pd.notna(row['场地上课节次']) else None,
                    class_size=int(row['教学班人数']) if pd.notna(row['教学班人数']) else None,
                    class_composition=str(row['教学班组成']).strip() if pd.notna(row['教学班组成']) else None,
                    credits=float(row['学分']) if pd.notna(row['学分']) else None,
                    total_hours=float(row['总学时']) if pd.notna(row['总学时']) else None,
                    offering_college=str(row['开课学院']).strip() if pd.notna(row['开课学院']) else None,
                    enrollment_count=int(row['选课人数']) if pd.notna(row['选课人数']) else None,
                    weekly_hours=str(row['周学时']).strip() if pd.notna(row['周学时']) else None,
                    class_time=str(row['上课时间']).strip() if pd.notna(row['上课时间']) else None,
                    class_location=str(row['上课地点']).strip() if pd.notna(row['上课地点']) else None,
                    course_nature=str(row['课程性质']).strip() if pd.notna(row['课程性质']) else None,
                    major_composition=str(row['专业组成']).strip() if pd.notna(row['专业组成']) else None,
                    teacher_id=str(row['教工号']).strip() if pd.notna(row['教工号']) else None,
                    venue_id=str(row['场地编号']).strip() if pd.notna(row['场地编号']) else None,
                    semester=str(row['学期']) if pd.notna(row['学期']) else None,
                    academic_year=str(row['学年']).strip() if pd.notna(row['学年']) else None
                )
                course_key = _course_row_key(row)
                if course_key in existing_course_keys:
                    stats['courses_skipped_duplicate'] += 1
                    imported_courses.setdefault(str(course.semester or '').strip(), []).append(existing_course_keys[course_key])
                    continue
                db.session.add(course)
                existing_course_keys[course_key] = course
                imported_courses.setdefault(str(course.semester or '').strip(), []).append(course)
                stats['courses_added'] += 1
                    
            except Exception as e:
                stats['errors'].append(f"处理课程数据时出错（课程号：{course_code}-{selection_code}）：{str(e)}")
        
        # Any row error rejects the whole workbook before advancing authority.
        if stats['errors']:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': '课表导入失败，未保存任何数据：\n' + '\n'.join(stats['errors']),
                'stats': stats,
            })

        # 同步生成 canonical row-level schedule snapshot（与上述写入同一事务）。
        source_row_offset = DEFAULT_SOURCE_ROW_OFFSET
        imported_batches = persist_import_snapshot(
            df,
            source_filename=file.filename or 'schedule.xlsx',
            source_sha256=source_sha256,
            source_row_offset=source_row_offset,
        )
        persist_listening_assistant_entries(
            df,
            imported_batches,
            source_row_offset=source_row_offset,
        )
        db.session.flush()
        for batch in imported_batches:
            persist_course_mapping(batch, [course.id for course in imported_courses.get(batch.semester, [])])

        # 提交数据库更改
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '数据导入成功',
            'stats': stats
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'导入失败：{str(e)}'})


