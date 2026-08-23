# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: courses

from flask import render_template, request, redirect, url_for, session, jsonify, current_app
from app.models import User, Teacher, Venue, Course, ListeningBan, CourseRegistration, db, SystemSetting
from sqlalchemy import func
from app.security import login_required, role_required
from app.services.workbook import workbook_response
from app.utils.user_status import UNASSIGNED_DEPARTMENT_NAME, UNASSIGNED_GROUP_NAME, is_user_active
import pandas as pd
import openpyxl
from openpyxl.styles import Font, Alignment
import json
from collections import defaultdict
from . import admin_bp
from .shared import _active_user_query, allowed_file


@admin_bp.route('/course_management')
@role_required('超级管理员')
def course_management():
    """课程管理页面"""
    return render_template(
        'admin/course_feedback_management.html',
        page_mode='management',
        page_title='课程管理'
    )


@admin_bp.route('/course_feedback_management')
@role_required('超级管理员')
def course_feedback_management():
    """兼容旧课程反馈管理入口"""
    return redirect(url_for('admin.course_management'))


@admin_bp.route('/registration_statistics')
@role_required('超级管理员')
def registration_statistics():
    return render_template('admin/registration_statistics.html')


@admin_bp.route('/api/courses', methods=['GET'])
@login_required
def get_courses():
    """获取课程列表API - 按课程号和选课课号分组显示"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        search = request.args.get('search', '')
        semester = request.args.get('semester', '')
        weekday = request.args.get('weekday', '')
        time_slot = request.args.get('time_slot', '')
        
        # 构建查询
        query = Course.query
        
        # 搜索功能
        if search:
            query = query.filter(
                db.or_(
                    Course.course_name.contains(search),
                    Course.course_code.contains(search),
                    Course.selection_code.contains(search),
                    Course.teacher_id.contains(search),
                    Course.venue_id.contains(search),
                    Course.offering_college.contains(search)
                )
            )
        
        # 学期筛选
        if semester:
            query = query.filter(Course.semester == semester)
        
        # 星期筛选
        if weekday:
            query = query.filter(Course.weekday == weekday)
        
        # 时间段筛选
        if time_slot:
            query = query.filter(Course.class_period == time_slot)
        
        # 获取所有符合条件的课程记录
        all_courses = query.all()
        
        # 按课程号和选课课号分组
        course_groups = {}
        for course in all_courses:
            group_key = f"{course.course_code}_{course.selection_code}"
            if group_key not in course_groups:
                course_groups[group_key] = {
                    'course_code': course.course_code,
                    'selection_code': course.selection_code,
                    'course_name': course.course_name,
                    'offering_college': course.offering_college,
                    'credits': course.credits,
                    'total_hours': course.total_hours,
                    'course_nature': course.course_nature,
                    'semester': course.semester,
                    'academic_year': course.academic_year,
                    'enrollment_count': course.enrollment_count,
                    'weekly_hours': course.weekly_hours,
                    'class_composition': course.class_composition,
                    'major_composition': course.major_composition,
                    'records': [],  # 存储该课程的所有记录
                    'record_count': 0,  # 记录数量
                    'expanded': False  # 默认折叠，用户可以选择展开需要查看的课程组
                }
            
            # 添加具体的课程记录
            course_record = {
                'id': course.id,
                'teacher_id': course.teacher_id,
                'teacher_name': course.teacher.name if course.teacher else '',
                'venue_id': course.venue_id,
                'venue_name': course.venue.name if course.venue else '',
                'class_size': course.class_size,
                'class_time': course.class_time,
                'class_location': course.class_location,
                'weekday': course.weekday,
                'class_period': course.class_period,
                'start_week': course.start_week,
                'venue_start_week': course.venue_start_week,
                'venue_class_period': course.venue_class_period
            }
            course_groups[group_key]['records'].append(course_record)
            course_groups[group_key]['record_count'] += 1
        
        # 转换为列表并排序
        grouped_courses = list(course_groups.values())
        grouped_courses.sort(key=lambda x: (x['course_code'], x['selection_code']))
        
        # 手动分页
        total_groups = len(grouped_courses)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_courses = grouped_courses[start_idx:end_idx]
        
        # 计算分页信息
        total_pages = (total_groups + per_page - 1) // per_page
        has_prev = page > 1
        has_next = page < total_pages
        
        # 获取筛选选项
        semesters = db.session.query(Course.semester).distinct().filter(Course.semester.isnot(None)).all()
        semesters = [s[0] for s in semesters if s[0]]  # 提取学期值并过滤空值
        
        time_slots = db.session.query(Course.class_period).distinct().filter(Course.class_period.isnot(None)).all()
        time_slots = [t[0] for t in time_slots if t[0]]  # 提取时间段值并过滤空值
        
        return jsonify({
            'success': True,
            'courses': paginated_courses,
            'pagination': {
                'page': page,
                'pages': total_pages,
                'per_page': per_page,
                'total': total_groups,
                'has_prev': has_prev,
                'has_next': has_next
            },
            'filter_options': {
                'semesters': sorted(semesters),
                'time_slots': sorted(time_slots)
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取课程列表失败：{str(e)}'})


@admin_bp.route('/api/registration_statistics', methods=['GET'])
@role_required('超级管理员')
def get_registration_statistics():
    try:
        registration_rows = db.session.query(
            CourseRegistration.course_code,
            CourseRegistration.selection_code,
            func.count(CourseRegistration.id).label('listen_count')
        ).group_by(
            CourseRegistration.course_code,
            CourseRegistration.selection_code
        ).all()

        course_rows = Course.query.with_entities(
            Course.id,
            Course.course_code,
            Course.selection_code,
            Course.course_name,
            Course.teacher_id
        ).all()

        course_group_map = {}
        for row in course_rows:
            key = f"{row.course_code}_{row.selection_code}"
            if key not in course_group_map:
                course_group_map[key] = {
                    'course_code': row.course_code,
                    'selection_code': row.selection_code,
                    'course_name': row.course_name or '未命名课程',
                    'teacher_id': row.teacher_id,
                    'course_ids': []
                }
            course_group_map[key]['course_ids'].append(row.id)

        teacher_ids = [item['teacher_id'] for item in course_group_map.values() if item['teacher_id']]
        teacher_map = {t.teacher_id: t for t in Teacher.query.filter(Teacher.teacher_id.in_(teacher_ids)).all()} if teacher_ids else {}

        teacher_stats_map = {}
        for row in registration_rows:
            key = f"{row.course_code}_{row.selection_code}"
            course_group = course_group_map.get(key)
            if not course_group:
                continue

            teacher_id = course_group['teacher_id'] or 'UNKNOWN'
            teacher_obj = teacher_map.get(course_group['teacher_id']) if course_group['teacher_id'] else None
            teacher_name = teacher_obj.name if teacher_obj else '未匹配教师'
            teacher_college = teacher_obj.college if teacher_obj else ''

            if teacher_id not in teacher_stats_map:
                teacher_stats_map[teacher_id] = {
                    'teacher_id': teacher_id if teacher_id != 'UNKNOWN' else '',
                    'teacher_name': teacher_name,
                    'teacher_college': teacher_college,
                    'total_listen_count': 0,
                    'course_ids': [],
                    'courses': []
                }

            teacher_stats_map[teacher_id]['total_listen_count'] += int(row.listen_count or 0)
            teacher_stats_map[teacher_id]['course_ids'].extend(course_group['course_ids'])
            teacher_stats_map[teacher_id]['courses'].append({
                'course_code': course_group['course_code'],
                'selection_code': course_group['selection_code'],
                'course_name': course_group['course_name'],
                'listen_count': int(row.listen_count or 0),
                'course_ids': course_group['course_ids']
            })

        teacher_stats = list(teacher_stats_map.values())
        for teacher in teacher_stats:
            teacher['course_ids'] = sorted(list(set(teacher['course_ids'])))
            teacher['courses'].sort(key=lambda x: (-x['listen_count'], x['course_name']))
            teacher['course_count'] = len(teacher['courses'])

        teacher_stats.sort(key=lambda x: (-x['total_listen_count'], x['teacher_name']))
        return jsonify({'success': True, 'teachers': teacher_stats})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取统计失败：{str(e)}'}), 500


@admin_bp.route('/api/teachers/<teacher_id>', methods=['GET'])
@role_required('超级管理员')
def get_teacher_detail(teacher_id):
    """获取教师详细信息"""
    try:
        teacher = Teacher.query.get_or_404(teacher_id)
        
        # 获取该教师的课程
        courses = Course.query.filter_by(teacher_id=teacher_id).all()
        
        teacher_data = {
            'teacher_id': teacher.teacher_id,
            'name': teacher.name,
            'gender': teacher.gender,
            'title': teacher.title,
            'college': teacher.college,
            'department': teacher.college,  # 使用college作为department
            'phone': teacher.phone,
            'courses': [
                {
                    'course_id': course.id,
                    'course_name': course.course_name,
                    'class_time': course.class_time,
                    'class_location': course.class_location,
                    'semester': course.semester,
                    'academic_year': course.academic_year
                } for course in courses
            ]
        }
        
        return jsonify({'success': True, 'teacher': teacher_data})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取教师信息失败：{str(e)}'})


@admin_bp.route('/api/venues/<venue_id>', methods=['GET'])
@role_required('超级管理员')
def get_venue_detail(venue_id):
    """获取场地详细信息"""
    try:
        venue = Venue.query.get_or_404(venue_id)
        
        # 获取该场地的课程
        courses = Course.query.filter_by(venue_id=venue_id).all()
        
        venue_data = {
            'venue_id': venue.venue_id,
            'name': venue.name,
            'category': venue.category,
            'type': venue.category,  # 使用category作为type
            'campus': venue.campus,
            'floor': venue.floor,
            'building': venue.building,
            'capacity': venue.capacity,
            'courses': [
                {
                    'course_id': course.id,
                    'course_name': course.course_name,
                    'class_time': course.class_time,
                    'teacher_name': course.teacher.name if course.teacher else '',
                    'semester': course.semester,
                    'academic_year': course.academic_year
                } for course in courses
            ]
        }
        
        return jsonify({'success': True, 'venue': venue_data})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取场地信息失败：{str(e)}'})


@admin_bp.route('/api/courses/<int:course_id>/ban_users', methods=['GET'])
@role_required('超级管理员')
def get_course_ban_users(course_id):
    """获取课程的禁听用户列表"""
    try:
        # 获取该课程的所有禁听记录
        bans = ListeningBan.query.filter_by(course_id=course_id).all()
        
        ban_users = []
        for ban in bans:
            ban_users.append({
                'id': ban.id,
                'user_id': ban.user_id,
                'user_name': ban.user.name,
                'user_number': ban.user.number,
                'department': ban.user.department,
                'college': ban.user.college,
                'created_at': ban.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'creator_name': ban.creator.name if ban.creator else ''
            })
        
        return jsonify({'success': True, 'ban_users': ban_users})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取禁听用户列表失败：{str(e)}'})


@admin_bp.route('/api/courses/<int:course_id>/ban_users', methods=['POST'])
@role_required('超级管理员')
def add_course_ban_users(course_id):
    """为课程添加禁听用户"""
    try:
        data = request.get_json()
        user_ids = data.get('user_ids', [])
        
        if not user_ids:
            return jsonify({'success': False, 'message': '请选择要禁听的用户'})
        
        # 检查课程是否存在
        course = Course.query.get_or_404(course_id)
        
        added_count = 0
        skipped_count = 0
        
        for user_id in user_ids:
            # 检查用户是否存在
            user = User.query.get(user_id)
            if not user or not is_user_active(user):
                continue
            
            # 检查是否已经存在禁听记录
            existing_ban = ListeningBan.query.filter_by(
                course_id=course_id, user_id=user_id
            ).first()
            
            if existing_ban:
                skipped_count += 1
                continue
            
            # 创建新的禁听记录
            ban = ListeningBan(
                course_id=course_id,
                user_id=user_id,
                created_by=session.get('user_id')
            )
            db.session.add(ban)
            added_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'成功添加 {added_count} 个禁听用户，跳过 {skipped_count} 个已存在的记录'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'添加禁听用户失败：{str(e)}'})


@admin_bp.route('/api/courses/<int:course_id>/ban_users/<int:ban_id>', methods=['DELETE'])
@role_required('超级管理员')
def remove_course_ban_user(course_id, ban_id):
    """移除课程的禁听用户"""
    try:
        ban = ListeningBan.query.filter_by(
            id=ban_id, course_id=course_id
        ).first_or_404()
        
        db.session.delete(ban)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '成功移除禁听用户'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'移除禁听用户失败：{str(e)}'})


@admin_bp.route('/api/courses/batch_ban_users', methods=['POST'])
@role_required('超级管理员')
def batch_ban_users():
    """批量为多个课程添加禁听用户"""
    try:
        data = request.get_json()
        course_ids = data.get('course_ids', [])
        user_ids = data.get('user_ids', [])
        
        if not course_ids:
            return jsonify({'success': False, 'message': '请选择要禁听的课程'})
        
        if not user_ids:
            return jsonify({'success': False, 'message': '请选择要禁听的用户'})
        
        # 检查课程是否存在
        courses = Course.query.filter(Course.id.in_(course_ids)).all()
        if len(courses) != len(course_ids):
            return jsonify({'success': False, 'message': '部分课程不存在'})
        
        # 检查用户是否存在
        users = _active_user_query().filter(User.id.in_(user_ids)).all()
        if len(users) != len(user_ids):
            return jsonify({'success': False, 'message': '部分用户不存在'})
        
        added_count = 0
        skipped_count = 0
        
        for course_id in course_ids:
            for user_id in user_ids:
                # 检查是否已经存在禁听记录
                existing_ban = ListeningBan.query.filter_by(
                    course_id=course_id, user_id=user_id
                ).first()
                
                if existing_ban:
                    skipped_count += 1
                    continue
                
                # 创建新的禁听记录
                ban = ListeningBan(
                    course_id=course_id,
                    user_id=user_id,
                    created_by=session.get('user_id')
                )
                db.session.add(ban)
                added_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'成功添加 {added_count} 条禁听记录，跳过 {skipped_count} 条已存在的记录',
            'stats': {
                'added': added_count,
                'skipped': skipped_count,
                'total_courses': len(course_ids),
                'total_users': len(user_ids)
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'批量禁听失败：{str(e)}'})


def _get_default_ban_target_users():
    return _active_user_query().filter(User.role == '信息员').all()


def _read_banned_teacher_import_names(file_storage):
    if not file_storage or not file_storage.filename:
        raise ValueError('请上传Excel文件')
    if not allowed_file(file_storage.filename):
        raise ValueError('请上传Excel格式文件（.xlsx 或 .xls）')

    try:
        dataframe = pd.read_excel(file_storage, header=None)
    except Exception as exc:
        raise ValueError(f'读取Excel失败：{str(exc)}')

    if dataframe.empty or dataframe.shape[1] == 0:
        return []

    names = []
    seen = set()
    ignored_headers = {'教师姓名', '禁听教师名单', '禁听教师'}
    for value in dataframe.iloc[:, 0].tolist():
        if pd.isna(value):
            continue
        name = str(value).strip()
        if not name or name.lower() == 'nan' or name in ignored_headers:
            continue
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _build_teacher_ban_candidate(teacher, teacher_course_map, teacher_ban_count_map, target_user_count):
    course_rows = teacher_course_map.get(teacher.teacher_id, [])
    course_ids = [row['id'] for row in course_rows]
    course_names = sorted({row['course_name'] for row in course_rows if row.get('course_name')})
    course_count = len(course_ids)
    expected_ban_count = course_count * target_user_count
    existing_ban_count = int(teacher_ban_count_map.get(teacher.teacher_id, 0) or 0)

    if course_count == 0:
        status = 'no_courses'
        status_label = '暂无课程'
    elif existing_ban_count <= 0:
        status = 'new'
        status_label = '新增禁听教师'
    elif existing_ban_count >= expected_ban_count:
        status = 'already'
        status_label = '已完整禁听'
    else:
        status = 'partial'
        status_label = '部分已禁听'

    return {
        'teacher_id': teacher.teacher_id,
        'teacher_name': teacher.name,
        'teacher_college': teacher.college or '',
        'teacher_title': teacher.title or '',
        'course_count': course_count,
        'course_ids': course_ids,
        'course_names': course_names,
        'expected_ban_count': expected_ban_count,
        'existing_ban_count': existing_ban_count,
        'missing_ban_count': max(expected_ban_count - existing_ban_count, 0),
        'status': status,
        'status_label': status_label,
        'is_new_ban': status == 'new',
        'is_already_banned': status == 'already',
        'needs_apply': status in {'new', 'partial'},
    }


def _build_banned_teacher_import_preview(import_names):
    normalized_names = []
    seen_names = set()
    for name in import_names or []:
        normalized = str(name).strip()
        if not normalized or normalized in seen_names:
            continue
        seen_names.add(normalized)
        normalized_names.append(normalized)

    target_users = _get_default_ban_target_users()
    target_user_ids = [user.id for user in target_users]
    target_user_count = len(target_user_ids)

    teachers = Teacher.query.filter(Teacher.name.in_(normalized_names)).order_by(Teacher.name.asc(), Teacher.teacher_id.asc()).all() if normalized_names else []
    teachers_by_name = defaultdict(list)
    teacher_ids = []
    for teacher in teachers:
        teachers_by_name[teacher.name].append(teacher)
        teacher_ids.append(teacher.teacher_id)

    course_rows = Course.query.with_entities(Course.id, Course.teacher_id, Course.course_name).filter(
        Course.teacher_id.in_(teacher_ids)
    ).all() if teacher_ids else []
    teacher_course_map = defaultdict(list)
    all_course_ids = []
    for row in course_rows:
        teacher_course_map[row.teacher_id].append({
            'id': row.id,
            'course_name': row.course_name or ''
        })
        all_course_ids.append(row.id)

    teacher_ban_count_map = {}
    if all_course_ids and target_user_ids:
        ban_count_rows = db.session.query(
            Course.teacher_id,
            func.count(ListeningBan.id)
        ).join(
            ListeningBan, ListeningBan.course_id == Course.id
        ).filter(
            Course.teacher_id.in_(teacher_ids),
            ListeningBan.user_id.in_(target_user_ids)
        ).group_by(Course.teacher_id).all()
        teacher_ban_count_map = {teacher_id: count for teacher_id, count in ban_count_rows}

    matched_entries = []
    ambiguous_entries = []
    unmatched_names = []
    candidate_lookup = {}

    for import_name in normalized_names:
        matched_teachers = teachers_by_name.get(import_name, [])
        if not matched_teachers:
            unmatched_names.append(import_name)
            continue

        candidates = []
        for teacher in matched_teachers:
            candidate = _build_teacher_ban_candidate(
                teacher,
                teacher_course_map,
                teacher_ban_count_map,
                target_user_count
            )
            candidate['import_name'] = import_name
            candidates.append(candidate)
            candidate_lookup[teacher.teacher_id] = candidate

        if len(candidates) == 1:
            matched_entries.append(candidates[0])
        else:
            ambiguous_entries.append({
                'import_name': import_name,
                'candidates': candidates
            })

    selected_candidates = list(matched_entries)
    summary = {
        'import_name_count': len(normalized_names),
        'matched_name_count': len(matched_entries),
        'ambiguous_name_count': len(ambiguous_entries),
        'unmatched_name_count': len(unmatched_names),
        'target_user_count': target_user_count,
        'new_teacher_count': len([item for item in selected_candidates if item['status'] == 'new']),
        'partial_teacher_count': len([item for item in selected_candidates if item['status'] == 'partial']),
        'already_banned_teacher_count': len([item for item in selected_candidates if item['status'] == 'already']),
        'no_course_teacher_count': len([item for item in selected_candidates if item['status'] == 'no_courses']),
    }

    return {
        'summary': summary,
        'import_names': normalized_names,
        'matched_teachers': matched_entries,
        'ambiguous_entries': ambiguous_entries,
        'unmatched_names': unmatched_names,
        'candidate_lookup': candidate_lookup,
    }


def _resolve_banned_teacher_import_selection(preview_data, selection_map):
    selection_map = selection_map or {}
    resolved_teachers = []
    unresolved_names = []
    selected_teacher_ids = set()

    for teacher_data in preview_data.get('matched_teachers', []):
        teacher_id = teacher_data['teacher_id']
        if teacher_id in selected_teacher_ids:
            continue
        selected_teacher_ids.add(teacher_id)
        resolved_teachers.append(teacher_data)

    candidate_lookup = preview_data.get('candidate_lookup', {})
    for entry in preview_data.get('ambiguous_entries', []):
        import_name = entry['import_name']
        chosen_ids = selection_map.get(import_name, [])
        valid_ids = []
        allowed_ids = {candidate['teacher_id'] for candidate in entry.get('candidates', [])}
        for teacher_id in chosen_ids:
            normalized_teacher_id = str(teacher_id).strip()
            if normalized_teacher_id in allowed_ids and normalized_teacher_id not in selected_teacher_ids:
                valid_ids.append(normalized_teacher_id)

        if not valid_ids:
            unresolved_names.append(import_name)
            continue

        for teacher_id in valid_ids:
            selected_teacher_ids.add(teacher_id)
            resolved_teachers.append(candidate_lookup[teacher_id])

    return resolved_teachers, unresolved_names


@admin_bp.route('/api/banned_teachers/import/template', methods=['GET'])
@role_required('超级管理员')
def download_banned_teacher_import_template():
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = '禁听教师名单'
    worksheet['A1'] = '教师姓名'
    worksheet.column_dimensions['A'].width = 24
    worksheet['A1'].font = Font(bold=True)
    worksheet['A1'].alignment = Alignment(horizontal='center')

    return workbook_response(workbook, '禁听教师导入模板.xlsx')


@admin_bp.route('/api/banned_teachers/import/preview', methods=['POST'])
@role_required('超级管理员')
def preview_banned_teacher_import():
    try:
        file = request.files.get('file')
        teacher_names = _read_banned_teacher_import_names(file)
        if not teacher_names:
            return jsonify({'success': False, 'message': 'Excel中未读取到教师姓名，请按模板填写后重试'})

        preview_data = _build_banned_teacher_import_preview(teacher_names)
        return jsonify({'success': True, 'preview': preview_data})
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except Exception as exc:
        return jsonify({'success': False, 'message': f'预览禁听教师导入失败：{str(exc)}'}), 500


@admin_bp.route('/api/banned_teachers/import/apply', methods=['POST'])
@role_required('超级管理员')
def apply_banned_teacher_import():
    try:
        file = request.files.get('file')
        teacher_names = _read_banned_teacher_import_names(file)
        if not teacher_names:
            return jsonify({'success': False, 'message': 'Excel中未读取到教师姓名，请按模板填写后重试'})

        preview_data = _build_banned_teacher_import_preview(teacher_names)
        selection_map_raw = request.form.get('selected_teacher_ids_json', '').strip()
        try:
            selection_map = json.loads(selection_map_raw) if selection_map_raw else {}
        except json.JSONDecodeError:
            return jsonify({'success': False, 'message': '同名教师选择数据格式无效，请重新预览后再试'}), 400

        resolved_teachers, unresolved_names = _resolve_banned_teacher_import_selection(preview_data, selection_map)
        if unresolved_names:
            unresolved_display = '、'.join(unresolved_names[:10])
            suffix = ' 等' if len(unresolved_names) > 10 else ''
            return jsonify({
                'success': False,
                'message': f'以下同名教师尚未完成选择：{unresolved_display}{suffix}'
            }), 400
        if not resolved_teachers:
            return jsonify({'success': False, 'message': '未匹配到可导入的教师，请检查名单后重试'}), 400

        target_users = _get_default_ban_target_users()
        target_user_ids = [user.id for user in target_users]
        if not target_user_ids:
            return jsonify({'success': False, 'message': '当前没有可执行禁听的用户，无法导入'}), 400

        teachers_to_apply = [teacher for teacher in resolved_teachers if teacher.get('needs_apply')]
        selected_teacher_ids = [teacher['teacher_id'] for teacher in teachers_to_apply]
        all_selected_teacher_ids = [teacher['teacher_id'] for teacher in resolved_teachers]

        course_ids = []
        for teacher in teachers_to_apply:
            course_ids.extend(teacher.get('course_ids', []))
        course_ids = sorted(set(course_ids))

        existing_pairs = set()
        if course_ids:
            existing_rows = ListeningBan.query.with_entities(
                ListeningBan.course_id,
                ListeningBan.user_id
            ).filter(
                ListeningBan.course_id.in_(course_ids),
                ListeningBan.user_id.in_(target_user_ids)
            ).all()
            existing_pairs = {(course_id, user_id) for course_id, user_id in existing_rows}

        added_count = 0
        skipped_count = 0
        new_bans = []
        created_by = session.get('user_id')
        for teacher in teachers_to_apply:
            for course_id in teacher.get('course_ids', []):
                for user_id in target_user_ids:
                    pair = (course_id, user_id)
                    if pair in existing_pairs:
                        skipped_count += 1
                        continue
                    existing_pairs.add(pair)
                    new_bans.append(ListeningBan(
                        course_id=course_id,
                        user_id=user_id,
                        created_by=created_by
                    ))
                    added_count += 1

        if new_bans:
            db.session.bulk_save_objects(new_bans)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'导入完成，新增 {added_count} 条禁听记录，跳过 {skipped_count} 条已存在记录',
            'result': {
                'selected_teacher_count': len(all_selected_teacher_ids),
                'applied_teacher_count': len(selected_teacher_ids),
                'new_teacher_names': [teacher['teacher_name'] for teacher in resolved_teachers if teacher['status'] == 'new'],
                'partial_teacher_names': [teacher['teacher_name'] for teacher in resolved_teachers if teacher['status'] == 'partial'],
                'already_banned_teacher_names': [teacher['teacher_name'] for teacher in resolved_teachers if teacher['status'] == 'already'],
                'no_course_teacher_names': [teacher['teacher_name'] for teacher in resolved_teachers if teacher['status'] == 'no_courses'],
                'unmatched_names': preview_data.get('unmatched_names', []),
            }
        })
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'导入禁听教师失败：{str(exc)}'}), 500


@admin_bp.route('/api/users/for_ban', methods=['GET'])
@role_required('超级管理员')
def get_users_for_ban():
    """获取可用于禁听的用户列表（按部门-小组-用户层级结构）"""
    try:
        search = request.args.get('search', '')
        
        query = _active_user_query().filter(User.role == '信息员')
        
        if search:
            query = query.filter(
                db.or_(
                    User.name.contains(search),
                    User.number.contains(search),
                    User.student_id.contains(search),
                    User.department.contains(search),
                    User.college.contains(search)
                )
            )
        
        users = query.all()
        
        # 按部门-小组-用户的层级结构组织数据
        departments = {}
        
        for user in users:
            dept_name = user.department or UNASSIGNED_DEPARTMENT_NAME
            group_name = user.group or UNASSIGNED_GROUP_NAME
            
            # 初始化部门
            if dept_name not in departments:
                departments[dept_name] = {
                    'name': dept_name,
                    'groups': {},
                    'user_count': 0
                }
            
            # 初始化小组
            if group_name not in departments[dept_name]['groups']:
                departments[dept_name]['groups'][group_name] = {
                    'name': group_name,
                    'users': [],
                    'user_count': 0
                }
            
            # 添加用户
            user_data = {
                'id': user.id,
                'name': user.name,
                'number': user.number,
                'student_id': user.student_id,
                'department': user.department,
                'group': user.group,
                'college': user.college,
                'grade': user.grade,
                'major': user.major
            }
            
            departments[dept_name]['groups'][group_name]['users'].append(user_data)
            departments[dept_name]['groups'][group_name]['user_count'] += 1
            departments[dept_name]['user_count'] += 1
        
        # 转换为列表格式
        dept_list = []
        for dept_name, dept_data in departments.items():
            group_list = []
            for group_name, group_data in dept_data['groups'].items():
                group_list.append({
                    'name': group_name,
                    'users': group_data['users'],
                    'user_count': group_data['user_count']
                })
            
            dept_list.append({
                'name': dept_name,
                'groups': group_list,
                'user_count': dept_data['user_count']
            })
        
        # 按部门名称排序
        dept_list.sort(key=lambda x: x['name'])
        for dept in dept_list:
            dept['groups'].sort(key=lambda x: x['name'])
        
        return jsonify({
            'success': True, 
            'departments': dept_list,
            'total_users': len(users)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取用户列表失败：{str(e)}'})


@admin_bp.route('/api/courses/columns', methods=['GET'])
@login_required
def get_course_columns():
    """获取课程表格可显示的列信息"""
    # 默认列配置
    default_columns = [
        {'key': 'id', 'label': 'ID', 'default': False},
        {'key': 'course_code', 'label': '课程号', 'default': True},
        {'key': 'selection_code', 'label': '选课编号', 'default': True},
        {'key': 'course_name', 'label': '课程名称', 'default': True},
        {'key': 'teacher_id', 'label': '教工号', 'default': True},
        {'key': 'teacher_name', 'label': '教师姓名', 'default': True},
        {'key': 'venue_id', 'label': '场地编号', 'default': True},
        {'key': 'venue_name', 'label': '场地名称', 'default': False},
        {'key': 'offering_college', 'label': '开课学院', 'default': True},
        {'key': 'class_size', 'label': '教学班人数', 'default': False},
        {'key': 'credits', 'label': '学分', 'default': False},
        {'key': 'total_hours', 'label': '总学时', 'default': False},
        {'key': 'class_time', 'label': '上课时间', 'default': True},
        {'key': 'class_location', 'label': '上课地点', 'default': False},
        {'key': 'course_nature', 'label': '课程性质', 'default': False},
        {'key': 'semester', 'label': '学期', 'default': True},
        {'key': 'academic_year', 'label': '学年', 'default': False},
        {'key': 'weekday', 'label': '星期几', 'default': False},
        {'key': 'class_period', 'label': '上课节次', 'default': False},
        {'key': 'start_week', 'label': '起始周', 'default': False},
        {'key': 'enrollment_count', 'label': '选课人数', 'default': False},
        {'key': 'weekly_hours', 'label': '周学时', 'default': False},
        {'key': 'class_composition', 'label': '教学班组成', 'default': False},
        {'key': 'major_composition', 'label': '专业组成', 'default': False},
        {'key': 'venue_start_week', 'label': '场地上课起始周', 'default': False},
        {'key': 'venue_class_period', 'label': '场地上课节次', 'default': False}
    ]
    
    # 尝试从系统设置获取默认显示的列（由超级管理员设置）
    try:
        saved_setting = SystemSetting.query.filter_by(key='course_feedback_default_columns').first()
        if saved_setting and saved_setting.value:
            saved_visible_keys = json.loads(saved_setting.value)
            # 更新默认可见性
            for col in default_columns:
                col['default'] = col['key'] in saved_visible_keys
    except Exception as e:
        current_app.logger.error(f"获取默认列设置失败: {e}")
    
    return jsonify({'success': True, 'columns': default_columns})


@admin_bp.route('/api/courses/columns', methods=['POST'])
@role_required('超级管理员')
def save_course_columns():
    """保存课程表格默认显示的列信息（仅超级管理员）"""
    try:
        data = request.get_json()
        visible_columns = data.get('visible_columns', [])
        
        if not isinstance(visible_columns, list):
            return jsonify({'success': False, 'message': '数据格式错误'}), 400
            
        # 保存到系统设置
        setting = SystemSetting.query.filter_by(key='course_feedback_default_columns').first()
        if not setting:
            setting = SystemSetting(key='course_feedback_default_columns', value=json.dumps(visible_columns))
            db.session.add(setting)
        else:
            setting.value = json.dumps(visible_columns)
            
        db.session.commit()
        return jsonify({'success': True, 'message': '默认列设置已保存'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}'}), 500


