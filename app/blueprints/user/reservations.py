# -*- coding: utf-8 -*-
"""User blueprint domain module (Step 4 split)."""
from flask import render_template, request, redirect, url_for, flash, session, jsonify
from app.models import (
    Course,
    CourseRegistration as Reservation,
    LectureForm,
    LectureFormDraft,
    ListeningBan,
    SystemSetting,
    Teacher,
    User,
    db,
)
from app.security import login_required
from app.utils.user_status import active_user_filter
from app.services.form_bindings import (
    get_registration_logical_form_counts,
    registration_has_form_binding,
)
from datetime import datetime, timedelta
from sqlalchemy import and_, func
from app.utils.time_validator import validate_listening_time, TimeValidator
from app.utils.audit_tags import build_audit_tag, build_week_correction_tag
from app.utils.leave_management import append_leave_system_note, get_pending_leave_makeup, record_leave_makeup_form
from app.utils.profile_settings import PROFILE_EDITABLE_FIELD_KEYS, get_profile_editable_fields
from app.utils.course_registration_limits import (
    get_current_teaching_week_no,
    parse_listening_week_no,
    validate_course_weekly_registration_limit,
)
from app.utils.permission_feedback import forbidden_json, flash_forbidden
import hashlib
import json
import re

from . import user_bp


def _build_activity_records(user, request_args):
    """Build the existing form and reservation view model for the activity center."""
    from collections import defaultdict

    search = request_args.get('search', '')
    date_from = request_args.get('date_from', '')
    date_to = request_args.get('date_to', '')

    query = LectureForm.query.filter_by(listener_number=user.number)
    if search:
        query = query.filter(
            db.or_(
                LectureForm.course_title.contains(search),
                LectureForm.teacher_name.contains(search),
                LectureForm.lecture_location.contains(search)
            )
        )

    if date_from:
        query = query.filter(LectureForm.lecture_date >= date_from)
    if date_to:
        query = query.filter(LectureForm.lecture_date <= date_to)

    all_forms = query.order_by(LectureForm.updated_at.desc()).all()
    grouped_forms = defaultdict(list)
    for form in all_forms:
        unique_key = form.unique_id if form.unique_id else f"single_{form.id}"
        grouped_forms[unique_key].append(form)

    form_groups = []
    for unique_id, versions in grouped_forms.items():
        versions.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
        form_groups.append({
            'unique_id': unique_id,
            'latest_form': versions[0],
            'versions': versions,
            'version_count': len(versions)
        })

    form_groups.sort(
        key=lambda item: item['latest_form'].updated_at or item['latest_form'].created_at,
        reverse=True,
    )

    registrations = Reservation.query.filter_by(user_id=user.id).order_by(Reservation.created_at.desc()).all()
    logical_bind_counts = get_registration_logical_form_counts(
        [reservation.id for reservation in registrations]
    )
    my_reservations = []
    for reservation in registrations:
        course = Course.query.filter_by(
            course_code=reservation.course_code,
            selection_code=reservation.selection_code,
        ).first()
        bind_count = logical_bind_counts.get(reservation.id, 0)
        is_bound = bind_count > 0
        my_reservations.append({
            'id': reservation.id,
            'course_code': reservation.course_code,
            'selection_code': reservation.selection_code,
            'course_name': course.course_name if course else '课程已删除',
            'teacher_name': course.teacher.name if course and course.teacher else '未知',
            'class_time': course.class_time if course else '',
            'class_location': course.class_location if course else '',
            'listening_info': reservation.listening_info,
            'created_at': reservation.created_at,
            'is_bound': is_bound,
            'bind_count': bind_count,
            'can_edit': not is_bound,
            'can_delete': not is_bound,
        })

    return {'forms': form_groups, 'my_reservations': my_reservations}


@user_bp.route('/listening_registration')
@login_required
def listening_registration():
    """Information officer activity center."""
    user = User.query.get(session['user_id'])
    active_tab = request.args.get('tab', 'registration')
    if active_tab not in {'registration', 'records'}:
        active_tab = 'registration'
    records = _build_activity_records(user, request.args)
    semester_configured = bool((SystemSetting.get('teaching_first_week_monday') or '').strip())
    return render_template(
        'user/activity_center.html',
        user=user,
        active_tab=active_tab,
        semester_configured=semester_configured,
        **records,
    )


@user_bp.route('/course_feedback_management')
@login_required
def course_feedback_management():
    """兼容旧听课登记入口"""
    return redirect(url_for('user.listening_registration'))


@user_bp.route('/api/available_courses')
@login_required
def api_available_courses():
    """获取可听课程列表（支持搜索和分页）"""
    try:
        user_id = session['user_id']
        search_query = request.args.get('q', '').strip()
        page = request.args.get('page', 1, type=int)
        per_page = 20  # 每页显示数量

        # 获取该用户被禁听的课程ID列表
        banned_course_ids = db.session.query(ListeningBan.course_id).filter_by(user_id=user_id).subquery()
        
        # 基础查询
        query = Course.query.filter(~Course.id.in_(banned_course_ids))
        
        # 搜索过滤
        if search_query:
            query = query.filter(
                db.or_(
                    Course.course_name.contains(search_query),
                    Course.course_code.contains(search_query),
                    Course.teacher.has(Teacher.name.contains(search_query))
                )
            )
        
        # 仅返回必要的字段以减少数据量，且按课程名排序
        # 注意：这里我们返回Course对象，以便后续处理
        pagination = query.order_by(Course.course_name).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        courses = pagination.items
        
        # 组装返回数据
        results = []
        for course in courses:
            results.append({
                'id': course.id,
                'text': f"{course.course_name} | {course.teacher.name if course.teacher else '未知'} | {course.class_time or '时间未知'} ({course.course_code}-{course.selection_code})",
                'course_code': course.course_code,
                'selection_code': course.selection_code,
                'course_name': course.course_name,
                'teacher_name': course.teacher.name if course.teacher else '未知',
                'teacher_college': course.teacher.college if course.teacher else '未知',
                'class_time': course.class_time,
                'class_location': course.class_location,
                'class_size': course.class_size,
                'credits': course.credits,
                'total_hours': course.total_hours
            })
            
        return jsonify({
            'success': True,
            'results': results,
            'pagination': {
                'more': pagination.has_next
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取课程列表失败: {str(e)}'
        }), 500


@user_bp.route('/api/course_registration_history')
@login_required
def api_course_registration_history():
    """获取特定课程的登记历史（最新5条）"""
    try:
        course_code = request.args.get('course_code')
        selection_code = request.args.get('selection_code')
        limit = request.args.get('limit', 5, type=int)
        target_week = request.args.get('target_week', type=int)
        if target_week is not None and target_week <= 0:
            target_week = None
        current_week = get_current_teaching_week_no()
        highlight_week = target_week or current_week
        
        if not course_code or not selection_code:
            return jsonify({'success': False, 'data': []})
            
        registrations = Reservation.query.filter_by(
            course_code=course_code,
            selection_code=selection_code
        ).order_by(Reservation.created_at.desc()).limit(limit).all()
        
        logical_bind_counts = get_registration_logical_form_counts(
            [r.id for r in registrations]
        )
        data = []
        for r in registrations:
            bind_count = logical_bind_counts.get(r.id, 0)
            teaching_week = parse_listening_week_no(r.listening_info)
            registrant = r.user
            user_name = registrant.name if registrant else '未知用户'
            user_number = registrant.number if registrant else ''
            data.append({
                'id': r.id,
                'user_name': user_name,
                'user_number': user_number,
                'user_display': f'{user_name}（{user_number}）' if user_number else user_name,
                'listening_info': r.listening_info,
                'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
                'teaching_week': teaching_week,
                'is_highlighted': bool(highlight_week and teaching_week == highlight_week),
                'is_used': r.is_used,
                'is_bound': bind_count > 0
            })
        
        return jsonify({
            'success': True,
            'data': data,
            'current_week': current_week,
            'highlight_week': highlight_week,
            'highlight_source': 'target' if target_week else 'current',
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@user_bp.route('/api/create_reservation', methods=['POST'])
@login_required
def api_create_reservation():
    """创建听课登记"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        
        course_code = data.get('course_code')
        selection_code = data.get('selection_code')
        listening_info = data.get('listening_info')
        
        if not all([course_code, selection_code, listening_info]):
            return jsonify({
                'success': False,
                'message': '请填写完整的登记信息'
            }), 400
        
        # 后端独立确认教学周期已配置，未配置时拒绝登记。
        semester_configured = bool((SystemSetting.get('teaching_first_week_monday') or '').strip())
        if not semester_configured:
            return jsonify({
                'success': False,
                'message': '请联系管理员在 系统设置 → 教学制度 配置学期起始周后再登记。'
            }), 400

        # 验证听课时间是否在未来
        is_valid_time, time_error = validate_listening_time(listening_info)
        if not is_valid_time:
            return jsonify({
                'success': False,
                'message': f'时间验证失败：{time_error}'
            }), 400
        
        # 移除唯一性检查，允许重复登记（作为历史记录）
        
        # 检查该课程组是否存在且用户有权限听课
        course_exists = Course.query.filter_by(
            course_code=course_code,
            selection_code=selection_code
        ).first()
        
        if not course_exists:
            return jsonify({
                'success': False,
                'message': '课程不存在'
            }), 400
        
        # 检查是否被禁听
        banned = ListeningBan.query.filter(
            and_(
                ListeningBan.user_id == user_id,
                ListeningBan.course_id.in_(
                    db.session.query(Course.id).filter_by(
                        course_code=course_code,
                        selection_code=selection_code
                    )
                )
            )
        ).first()
        
        if banned:
            return jsonify({
                'success': False,
                'message': '您被禁止听取该课程'
            }), 403

        limit_allowed, limit_error = validate_course_weekly_registration_limit(
            course_code,
            selection_code,
            listening_info
        )
        if not limit_allowed:
            return jsonify({
                'success': False,
                'message': limit_error
            }), 400
        
        # 创建预定记录
        reservation = Reservation(
            course_code=course_code,
            selection_code=selection_code,
            user_id=user_id,
            listening_info=listening_info,
            is_used=False
        )
        
        db.session.add(reservation)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '听课登记成功',
            'data': {
                'id': reservation.id,
                'created_at': reservation.created_at.strftime('%Y-%m-%d %H:%M')
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'登记失败: {str(e)}'
        }), 500


@user_bp.route('/api/cancel_reservation', methods=['POST'])
@login_required
def api_cancel_reservation():
    """取消听课登记"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        
        course_code = data.get('course_code')
        selection_code = data.get('selection_code')
        
        if not all([course_code, selection_code]):
            return jsonify({
                'success': False,
                'message': '参数不完整'
            }), 400
        
        # 查找预定记录
        reservation = Reservation.query.filter_by(
            course_code=course_code,
            selection_code=selection_code,
            user_id=user_id
        ).first()
        
        if not reservation:
            return jsonify({
                'success': False,
                'message': '未找到预定记录'
            }), 404
        
        # 这里可以添加时间验证，确保只能在听课时间前取消
        # 例如：检查听课时间是否还未到
        
        # 删除预定记录
        db.session.delete(reservation)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '取消登记成功'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'取消失败: {str(e)}'
        }), 500


@user_bp.route('/api/my_reservations')
@login_required
def api_my_reservations():
    """获取我的听课登记列表"""
    try:
        user_id = session['user_id']
        
        reservations = Reservation.query.filter_by(user_id=user_id).order_by(Reservation.created_at.desc()).all()
        logical_bind_counts = get_registration_logical_form_counts(
            [r.id for r in reservations]
        )
        
        result = []
        for r in reservations:
            bind_count = logical_bind_counts.get(r.id, 0)
            is_bound = bind_count > 0
            # 获取课程组信息
            courses = Course.query.filter_by(
                course_code=r.course_code,
                selection_code=r.selection_code
            ).all()
            
            if courses:
                course_info = {
                    'id': r.id,
                    'course_code': r.course_code,
                    'selection_code': r.selection_code,
                    'course_name': courses[0].course_name,
                    'offering_college': courses[0].offering_college,
                    'listening_info': r.listening_info,
                    'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
                    'is_bound': is_bound,
                    'bind_count': bind_count,
                    'can_edit': not is_bound,
                    'can_delete': not is_bound,
                    'courses': []
                }
                
                for course in courses:
                    course_info['courses'].append({
                        'class_time': course.class_time,
                        'class_location': course.class_location,
                        'teacher_name': course.teacher.name if course.teacher else '未知',
                        'teacher_college': course.teacher.college if course.teacher else '未知'
                    })
                
                result.append(course_info)
        
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取预定列表失败: {str(e)}'
        }), 500


@user_bp.route('/api/my_reservations/<int:reservation_id>', methods=['PUT'])
@login_required
def api_update_my_reservation(reservation_id):
    try:
        user_id = session['user_id']
        reservation = Reservation.query.filter_by(id=reservation_id, user_id=user_id).first()
        if not reservation:
            return jsonify({'success': False, 'message': '登记记录不存在'}), 404

        if registration_has_form_binding(reservation.id):
            return jsonify({'success': False, 'message': '该登记已绑定听课反馈表单，不能修改'}), 400

        data = request.get_json() or {}
        listening_info = (data.get('listening_info') or '').strip()
        if not listening_info:
            return jsonify({'success': False, 'message': '请填写听课计划说明'}), 400

        is_valid_time, time_error = validate_listening_time(listening_info)
        if not is_valid_time:
            return jsonify({'success': False, 'message': f'时间验证失败：{time_error}'}), 400

        reservation.listening_info = listening_info
        reservation.is_used = False
        db.session.commit()
        return jsonify({'success': True, 'message': '听课登记修改成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'修改失败: {str(e)}'}), 500


@user_bp.route('/api/my_reservations/<int:reservation_id>', methods=['DELETE'])
@login_required
def api_delete_my_reservation(reservation_id):
    try:
        user_id = session['user_id']
        reservation = Reservation.query.filter_by(id=reservation_id, user_id=user_id).first()
        if not reservation:
            return jsonify({'success': False, 'message': '登记记录不存在'}), 404

        if registration_has_form_binding(reservation.id):
            return jsonify({'success': False, 'message': '该登记已绑定听课反馈表单，不能删除'}), 400

        db.session.delete(reservation)
        db.session.commit()
        return jsonify({'success': True, 'message': '听课登记删除成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


@user_bp.route('/api/unused_reservations')
@login_required
def api_unused_reservations():
    """获取未使用的听课登记"""
    try:
        user_id = session['user_id']
        # 获取未使用的登记
        reservations = Reservation.query.filter_by(
            user_id=user_id,
            is_used=False
        ).order_by(Reservation.created_at.desc()).all()
        
        result = []
        for r in reservations:
            # 获取课程信息
            course = Course.query.filter_by(
                course_code=r.course_code,
                selection_code=r.selection_code
            ).first()
            
            if course:
                result.append({
                    'id': r.id,
                    'course_name': course.course_name,
                    'teacher_name': course.teacher.name if course.teacher else '未知',
                    'teacher_college': course.teacher.college if course.teacher else '未知',
                    'class_time': course.class_time,
                    'class_location': course.class_location,
                    'listening_info': r.listening_info,
                    'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
                    # 原始数据用于自动填充
                    'raw_data': {
                        'course_title': course.course_name,
                        'teacher_name': course.teacher.name if course.teacher else '未知',
                        'teacher_college': course.teacher.college if course.teacher else '未知',
                        'lecture_location': course.class_location,
                        'student_grade_class': course.class_composition,
                        'course_code': course.course_code,
                        'selection_code': course.selection_code
                    }
                })
        
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@user_bp.route('/api/time_suggestion')
@login_required
def api_time_suggestion():
    """获取时间填写建议"""
    try:
        suggestion = TimeValidator.get_time_suggestion()
        return jsonify({
            'success': True,
            'suggestion': suggestion
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取建议失败: {str(e)}'
        }), 500
