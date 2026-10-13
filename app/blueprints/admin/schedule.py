# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: schedule

from flask import request, jsonify
from app.models import db
from app.security import role_required
from app.utils.env_config import env_path
from app.services.academic_term import compose_semester_identifier
from app.services.schedule_snapshots import (
    DEFAULT_SOURCE_ROW_OFFSET,
    persist_import_snapshot,
)
from app.services.listening_assistant_schedule import (
    persist_listening_assistant_entries,
)
from app.services.current_courses import (
    COURSE_KEY_FIELDS,
    COURSE_ROW_COLUMNS,
    course_model_key,
    course_row_key,
    ensure_current_course_schema,
    persist_courses_for_batch,
)
import hashlib
import math
import pandas as pd
import os
from . import admin_bp
from .shared import allowed_file


DEFAULT_SCHEDULE_TEMPLATE_PATH = os.path.join('data', 'storage', 'templates', 'schedule', '全校课表.xls')


# 课程自然键的唯一实现位于 app/services/current_courses.py：管理端上传与调试
# 启动器必须用同一套列名与规范化规则，否则两个入口会对同一行的重复性得出不同
# 结论。这里保留标识符仅为兼容既有引用。
_SCHEDULE_COURSE_KEY_FIELDS = COURSE_KEY_FIELDS
_SCHEDULE_ROW_COLUMNS = COURSE_ROW_COLUMNS


def _course_row_key(row):
    return course_row_key(row)


def _course_model_key(course):
    return course_model_key(course)

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
    """Legacy alias for the canonical natural-key canonicalization.

    The single implementation lives in app/services/current_courses.py so the
    admin upload and the debug launcher cannot disagree about duplicate rows.
    """
    from app.services.current_courses import _as_key_text
    return _as_key_text(value)


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
        

        # 课表的学年与学期分列存放（学年=2026-2027，学期=1/2），而应用用单个学期
        # 标识（2026-2027-1）识别学期。必须在写入快照、听课索引和课程之前统一，
        # 否则导入的课程会绑定到裸学期号，任何已配置学期都无法选中它们。
        # 组合规则只有 app/services/academic_term.compose_semester_identifier 一处，
        # 调试启动器调用同一个函数。
        if '学期' in df.columns:
            years = df['学年'] if '学年' in df.columns else [''] * len(df)
            df['学期'] = [
                compose_semester_identifier(term, year)
                for term, year in zip(df['学期'], years)
            ]

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

        # 同步生成 canonical row-level schedule snapshot。
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

        # 教师、场地、课程与登记映射由共享实现写入，调试启动器走同一条路径。
        # 每个学期分组只处理属于该学期的行：整帧重复写入会产生跨学期与重复课程。
        for batch in imported_batches:
            batch_rows = df[df['学期'] == batch.semester]
            batch_stats = persist_courses_for_batch(batch, batch_rows)
            for key in ('teachers_added', 'teachers_updated', 'venues_added',
                        'venues_updated', 'courses_added', 'courses_skipped_duplicate'):
                stats[key] += batch_stats[key]
            stats['errors'].extend(batch_stats['errors'])

        # Any row error rejects the whole workbook before advancing authority.
        if stats['errors']:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': '课表导入失败，未保存任何数据：\n' + '\n'.join(stats['errors']),
                'stats': stats,
            })

        # 提交数据库更改
        db.session.commit()

        return jsonify({
            'success': True,
            'message': '数据导入成功',
            'stats': stats
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'导入失败：{str(e)}'}), 500


