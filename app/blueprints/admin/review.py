# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: review

from flask import render_template, request, redirect, url_for, flash, session, jsonify
from app.models import User, Group, LectureForm, CourseRegistration, db, SystemSetting, ScoreRecord, ScoreItem
from datetime import datetime, timedelta
from app.utils.auto_review import AutoReviewEngine
from app.blueprints.auth import login_required, role_required
from app.utils.review_permissions import get_user_review_permission, can_review_status, get_user_structure_for_review, get_reviewable_users, get_next_status_after_review, get_review_permission_presentation
from app.utils.permission_feedback import build_forbidden_message, forbidden_json, flash_forbidden
from app.utils.manage_permissions import get_user_manage_permission
from app.utils.audit_tags import REVIEW_TAG_OPTIONS, LATE_TAG_OPTIONS, build_audit_tag, parse_audit_tag, validate_audit_tag
from app.utils.user_status import UNASSIGNED_GROUP_NAME
from app.utils.review_drafts import delete_review_form_draft, load_review_form_draft, normalize_review_draft_payload, parse_review_form_draft, save_review_form_draft
import json
from . import admin_bp
from .shared import _active_user_query, _build_review_form_filter_datetime, _get_form_latest_timestamp, _latest_form_groups_for_users, _normalize_review_form_time_filter, get_reviewer_display_mode
from app.services.review_domain import is_form_in_review_scope, partition_forms_by_review_scope


def _serialize_audit_tag(audit_tag):
    parsed = parse_audit_tag(audit_tag)
    return {
        'raw': parsed['raw'],
        'review_tag': parsed['review_tag'],
        'week_correction_tag': parsed.get('week_correction_tag'),
        'week_correction_week_no': parsed.get('week_correction_week_no'),
        'legacy_late_tag': parsed.get('legacy_late_tag'),
        # 兼容旧前端字段
        'late_tag': parsed.get('late_tag'),
        'review_tag_defined': parsed['review_tag'] in REVIEW_TAG_OPTIONS,
        'week_correction_defined': parsed.get('week_correction_week_no') is not None,
        'second_tag_defined': parsed.get('second_tag') is not None,
        'late_tag_defined': parsed['late_tag'] in LATE_TAG_OPTIONS if parsed.get('late_tag') else False,
        'has_late_tag': parsed.get('late_tag') is not None,
        'extra_tags': parsed['extra_tags'],
    }


def _load_review_forms_for_operation(form_ids):
    normalized_form_ids = []
    invalid_form_ids = []
    for form_id in form_ids or []:
        try:
            value = int(form_id)
        except (TypeError, ValueError):
            invalid_form_ids.append(form_id)
            continue
        if value not in normalized_form_ids:
            normalized_form_ids.append(value)

    if invalid_form_ids:
        return None, jsonify({'success': False, 'message': f'存在非法表单ID：{invalid_form_ids[:5]}'}), 400
    if not normalized_form_ids:
        return None, jsonify({'success': False, 'message': '请选择至少一个表单'}), 400

    forms = LectureForm.query.filter(LectureForm.id.in_(normalized_form_ids)).all()
    form_map = {form.id: form for form in forms}

    missing_ids = [form_id for form_id in normalized_form_ids if form_id not in form_map]
    if missing_ids:
        return None, jsonify({'success': False, 'message': f'以下表单不存在：{missing_ids[:5]}'}), 404

    existing_forms = [
        form_map[form_id]
        for form_id in normalized_form_ids
        if form_id in form_map
    ]
    allowed_forms, denied_forms = partition_forms_by_review_scope(
        session['user_id'],
        existing_forms,
    )
    if denied_forms:
        return None, jsonify({
            'success': False,
            'message': build_forbidden_message(
                '表单审核',
                '当前账号没有操作所选表单的权限。',
                action='操作',
            ),
        }), 403
    return allowed_forms, None, None


@admin_bp.route('/api/review/form/<int:form_id>/draft', methods=['GET', 'PUT', 'DELETE'])
@login_required
def review_form_draft(form_id):
    """Save, load, or delete the current reviewer's draft for one form."""
    try:
        user_id = session['user_id']
        permission = get_user_review_permission(user_id)
        if not permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有审核权限。',
                    action='操作',
                ),
            }), 403

        form = LectureForm.query.get_or_404(form_id)
        if not is_form_in_review_scope(user_id, form):
            return forbidden_json('表单审核', '该表单不在当前审核范围内。', action='审核')

        if request.method == 'GET':
            draft = load_review_form_draft(user_id, form_id)
            return jsonify({
                'success': True,
                'exists': draft is not None,
                'data': parse_review_form_draft(draft),
                'updated_at': draft.updated_at.strftime('%Y-%m-%d %H:%M:%S') if draft else None,
            })

        if request.method == 'DELETE':
            deleted = delete_review_form_draft(user_id, form_id)
            db.session.commit()
            return jsonify({'success': True, 'deleted': deleted})

        body = request.get_json(silent=True) or {}
        raw_payload = body.get('data', body)
        payload = normalize_review_draft_payload(raw_payload)
        if payload is None:
            return jsonify({'success': False, 'message': 'Draft payload must be a JSON object'}), 400

        draft = save_review_form_draft(user_id, form_id, payload)
        db.session.commit()
        return jsonify({
            'success': True,
            'updated_at': draft.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


def _load_review_form_groups_for_definition(form_ids):
    requested_forms, error_response, status_code = _load_review_forms_for_operation(form_ids)
    if error_response:
        return None, error_response, status_code

    ordered_unique_ids = []
    requested_form_ids_by_unique_id = {}
    for form in requested_forms:
        unique_id = form.unique_id or form.id
        if unique_id not in ordered_unique_ids:
            ordered_unique_ids.append(unique_id)
        requested_form_ids_by_unique_id.setdefault(unique_id, []).append(form.id)

    if not ordered_unique_ids:
        return [], None, None

    group_forms = LectureForm.query.filter(
        (LectureForm.unique_id.in_(ordered_unique_ids)) | (LectureForm.id.in_(ordered_unique_ids))
    ).all()
    grouped_forms = {}
    for form in group_forms:
        unique_id = form.unique_id or form.id
        grouped_forms.setdefault(unique_id, []).append(form)

    entries = []
    for unique_id in ordered_unique_ids:
        form_list = grouped_forms.get(unique_id, [])
        if not form_list:
            continue
        sorted_forms = sorted(
            form_list,
            key=lambda form: form.id,
            reverse=True
        )
        entries.append({
            'unique_id': unique_id,
            'latest_form': sorted_forms[0],
            'forms': sorted_forms,
            'requested_form_ids': requested_form_ids_by_unique_id.get(unique_id, []),
        })
    return entries, None, None


@admin_bp.route('/review_forms')
@login_required
def review_forms():
    """表单审核页面"""
    # 检查用户权限
    permission = get_user_review_permission(session['user_id'])
    if not permission:
        flash_forbidden('表单审核')
        return redirect(url_for('admin.admin_dashboard'))
    
    # 直接返回模板，让前端JavaScript处理数据加载
    current_user = User.query.get(session['user_id'])
    is_super_admin = bool(current_user and current_user.role == '超级管理员')
    try:
        teaching_total_weeks = int(SystemSetting.get('teaching_total_weeks', '20') or 20)
    except (TypeError, ValueError):
        teaching_total_weeks = 20
    if teaching_total_weeks < 1:
        teaching_total_weeks = 20
    if teaching_total_weeks > 52:
        teaching_total_weeks = 52
    return render_template(
        'admin/review_forms.html',
        is_super_admin=is_super_admin,
        teaching_total_weeks=teaching_total_weeks
    )


@admin_bp.route('/api/review/reject/<int:form_id>', methods=['POST'])
@login_required
def reject_form_review(form_id):
    """驳回表单"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        
        # 驳回必须拥有实际审核权限，不能仅凭“管理员角色”放行。
        user = User.query.get(session['user_id'])
        permission = get_user_review_permission(user.id)
        if not permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '表单审核',
                     '当前账号没有驳回表单的权限。',
                     action='驳回',
                 ),
             }), 403

        original_form = LectureForm.query.get_or_404(form_id)
        
        # 获取最新版本；历史数据 unique_id 为空时由 logical group 查询包含 base。
        unique_id = original_form.unique_id or original_form.id
        latest_form = original_form.get_latest_version()
        
        # 校验：确保操作的是最新版本
        if latest_form and latest_form.id != original_form.id:
            return jsonify({'success': False, 'message': '该表单已有更新版本，请刷新页面后操作'}), 400
        
        # 对象级授权：目标表单主人必须属于当前账号的可审核范围，不存在时 fail-closed。
        if not is_form_in_review_scope(session['user_id'], original_form):
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '表单审核',
                     '该表单不在当前审核范围内。',
                     action='驳回',
                 ),
             }), 403
        
        # 阶段授权：驳回只能作用于当前审核级别允许处理的表单状态。
        # 已驳回记录的“更新驳回意见”属于既有兼容分支，不在常规状态推进检查中拒绝。
        if latest_form.status != '已驳回' and not can_review_status(user.id, latest_form.status):
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有驳回该状态表单的权限。',
                    action='驳回',
                ),
            }), 403

        data = request.get_json()
        reason = data.get('reason', '')
        
        # 检查当前状态，决定是更新还是新建
        if latest_form.status == '已驳回':
            # 更新现有记录
            latest_form.review_comment = reason
            latest_form.reviewer_id = session['user_id']
            latest_form.review_time = datetime.now()
            latest_form.updated_at = datetime.now()
            
            db.session.add(latest_form)
            delete_review_form_draft(session['user_id'], form_id)
            db.session.commit()
            
            return jsonify({
                'success': True, 
                'message': '驳回意见已更新',
                'new_status': '已驳回'
            })
        else:
            # 创建新的表单记录（驳回版本）
            new_form = LectureForm(
                # 复制原表单所有字段
                listener_name=original_form.listener_name,
                listener_number=original_form.listener_number,
                course_changes=original_form.course_changes,
                lecture_date=original_form.lecture_date,
                class_period=original_form.class_period,
                lecture_location=original_form.lecture_location,
                teacher_name=original_form.teacher_name,
                teacher_college=original_form.teacher_college,
                course_title=original_form.course_title,
                student_grade_class=original_form.student_grade_class,
                abnormal_situation=original_form.abnormal_situation,
                teaching_method=original_form.teaching_method,
                classroom_discipline=original_form.classroom_discipline,
                classroom_atmosphere=original_form.classroom_atmosphere,
                courseware_quality=original_form.courseware_quality,
                overall_effect=original_form.overall_effect,
                quality_case=original_form.quality_case,
                course_feedback=original_form.course_feedback,
                suggestions=original_form.suggestions,
                student_signature1=original_form.student_signature1,
                contact_phone1=original_form.contact_phone1,
                student_signature2=original_form.student_signature2,
                contact_phone2=original_form.contact_phone2,
                registration_id=original_form.registration_id, # 保持关联
                audit_tag=original_form.audit_tag,
                
                # 设置驳回状态
                status='已驳回',
                reviewer_id=session['user_id'],
                review_time=datetime.now(),
                review_comment=reason,
                
                # 继承unique_id
                unique_id=unique_id,
                created_at=original_form.created_at,
                updated_at=datetime.now()
            )
            
            # 确保原表单有unique_id
            if not original_form.unique_id:
                original_form.unique_id = original_form.id
                db.session.add(original_form)
                
            db.session.add(new_form)
            delete_review_form_draft(session['user_id'], form_id)
            db.session.commit()
            
            return jsonify({
                'success': True, 
                'message': '表单已驳回',
                'new_status': '已驳回'
            })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/submit/<int:form_id>', methods=['POST'])
@login_required
def submit_review(form_id):
    """提交表单审核"""
    try:
        # 检查用户权限
        permission = get_user_review_permission(session['user_id'])
        if not permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有审核表单的权限。',
                    action='审核',
                ),
            })
        
        # 获取原表单
        original_form = LectureForm.query.filter_by(id=form_id).first()
        if not original_form:
            return jsonify({'success': False, 'message': '表单不存在'})
        
        # 校验目标表单所有者是否在当前账号可审核范围内（对象级授权，绝不能只依赖状态/权限）
        if not is_form_in_review_scope(session['user_id'], original_form):
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '该表单不在当前审核范围内。',
                    action='审核',
                ),
            }), 403

        # 检查是否可以审核该状态的表单
        if not can_review_status(session['user_id'], original_form.status):
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有审核该表单的权限。',
                    action='审核',
                ),
            })
        
        # 获取表单数据
        # 兼容 JSON 格式（前端使用 fetch JSON 提交）和 Form 格式
        if request.is_json:
            data = request.get_json()
            form_data = data.get('form_data', {})
            review_comment = data.get('review_comment', '无')
            score_data_list = data.get('score_data', [])
        else:
            form_data = request.form.to_dict()
            review_comment = form_data.get('review_comment', '无')
            score_data_list = []
            # 尝试从 form 字段中解析 score_data JSON 字符串（兼容旧方式或隐藏域提交）
            score_data_json = form_data.get('score_data')
            if score_data_json:
                try:
                    score_data_list = json.loads(score_data_json)
                except:
                    pass
        
        # 确定新状态
        new_status = get_next_status_after_review(session['user_id'])

        course_changes_raw = (form_data.get('course_changes') or '').strip()
        resolved_course_changes = course_changes_raw or (original_form.course_changes or '无')
        
        # 获取最新版本；历史数据 unique_id 为空时由 logical group 查询包含 base。
        unique_id = original_form.unique_id or original_form.id
        latest_form = original_form.get_latest_version()
        
        # 校验：确保操作的是最新版本
        if latest_form and latest_form.id != original_form.id:
             return jsonify({'success': False, 'message': '该表单已有更新版本，请刷新页面后操作'}), 400
        
        # 准备表单数据
        new_form_data = {
            'unique_id': unique_id,
            'audit_tag': original_form.audit_tag,
            'listener_name': form_data.get('listener_name'),
            # 表单所有者身份以服务端原始记录为准，禁止客户端重指定
            'listener_number': original_form.listener_number,
            # 同一 logical form 必须保持同一次预约登记关联
            'registration_id': original_form.registration_id,
            'course_changes': resolved_course_changes,
            'lecture_date': form_data.get('lecture_date'),
            'class_period': form_data.get('class_period'),
            'lecture_location': form_data.get('lecture_location'),
            'teacher_name': form_data.get('teacher_name'),
            'teacher_college': form_data.get('teacher_college'),
            'course_title': form_data.get('course_title'),
            'student_grade_class': form_data.get('student_grade_class'),
            'abnormal_situation': form_data.get('abnormal_situation', '无'),
            'teaching_method': form_data.get('teaching_method'),
            'classroom_discipline': form_data.get('classroom_discipline'),
            'classroom_atmosphere': form_data.get('classroom_atmosphere'),
            'courseware_quality': form_data.get('courseware_quality'),
            'overall_effect': form_data.get('overall_effect'),
            'quality_case': form_data.get('quality_case'),
            'course_feedback': form_data.get('course_feedback'),
            'suggestions': form_data.get('suggestions', '无'),
            'student_signature1': form_data.get('student_signature1'),
            'contact_phone1': form_data.get('contact_phone1'),
            'student_signature2': form_data.get('student_signature2'),
            'contact_phone2': form_data.get('contact_phone2'),
            'status': new_status,
            'reviewer_id': session['user_id'],
            'review_time': datetime.now(),
            'review_comment': review_comment,
            'updated_at': datetime.now()  # 更新时间为审核时间
        }

        field_names = {
            'listener_name': '听课人姓名',
            'course_changes': '课程信息变化',
            'lecture_date': '听课时间',
            'class_period': '第几节',
            'lecture_location': '听课地点',
            'teacher_name': '授课教师',
            'teacher_college': '教师所属学院',
            'course_title': '课程总标题',
            'student_grade_class': '专业年级',
            'abnormal_situation': '异常情况反映',
            'teaching_method': '主要教学方法',
            'classroom_discipline': '管理课堂纪律',
            'classroom_atmosphere': '调动课堂气氛',
            'courseware_quality': '课件制作质量',
            'overall_effect': '整体教学效果',
            'quality_case': '优质案例推荐',
            'course_feedback': '课程反馈',
            'suggestions': '不足及建议',
            'student_signature1': '听课班级同学签名1',
            'contact_phone1': '联系电话1',
            'student_signature2': '听课班级同学签名2',
            'contact_phone2': '联系电话2'
        }
        modified_fields = []
        for field, label in field_names.items():
            new_value = new_form_data.get(field)
            old_value = getattr(original_form, field, None)
            if str(new_value or '') != str(old_value or ''):
                modified_fields.append(label)

        if modified_fields:
            base_comment = (review_comment or '').strip() or '无'
            modification_note = f"\n\n[系统记录] 审核人修改了以下字段：{', '.join(modified_fields)}"
            new_form_data['review_comment'] = base_comment + modification_note
        
        target_form = None
        if latest_form.status == new_status:
             # 更新现有记录
             target_form = latest_form
             for k, v in new_form_data.items():
                 if k != 'unique_id' and hasattr(target_form, k): 
                     setattr(target_form, k, v)
             
             # 删除旧的评分记录以便重新创建
             old_score_record = ScoreRecord.query.filter_by(form_id=target_form.id).first()
             if old_score_record:
                 db.session.delete(old_score_record)
        else:
             # 创建新的表单记录
             target_form = LectureForm(**new_form_data)
             target_form.created_at = original_form.created_at # 继承创建时间
             db.session.add(target_form)
        
        db.session.flush()  # 获取新表单ID
        
        # 处理评分数据
        if score_data_list:
            try:
                # 计算总分
                total_dept_score = sum(float(item.get('department_score', 0)) for item in score_data_list)
                total_personal_score = sum(float(item.get('personal_score', 0)) for item in score_data_list)
                
                # 创建评分记录
                score_record = ScoreRecord(
                    form_id=target_form.id,
                    reviewer_id=session['user_id'],
                    total_department_score=total_dept_score,
                    total_personal_score=total_personal_score
                )
                db.session.add(score_record)
                db.session.flush() # 获取评分记录ID
                
                # 创建评分项
                for item in score_data_list:
                    score_item = ScoreItem(
                        score_record_id=score_record.id,
                        reason=item.get('reason'),
                        department_score=float(item.get('department_score', 0)),
                        personal_score=float(item.get('personal_score', 0)),
                        is_auto_generated=item.get('is_auto_generated', item.get('is_auto', False))
                    )
                    db.session.add(score_item)
            except Exception as e:
                print(f"Error processing score data: {e}")
                # 不中断主流程，只记录错误
        
        delete_review_form_draft(session['user_id'], form_id)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '审核提交成功',
            'new_status': new_status,
            'form_id': target_form.id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'审核提交失败：{str(e)}'})


def _safe_review_return_url(value):
    candidate = (value or '').strip()
    if candidate.startswith('/admin/review_forms'):
        return candidate
    return url_for('admin.review_forms')


@admin_bp.route('/review/form/<int:form_id>')
@login_required
def review_form_page(form_id):
    """审核表单页面（独立页面）"""
    # 检查用户权限
    permission = get_user_review_permission(session['user_id'])
    if not permission:
        flash_forbidden('表单审核')
        return redirect(url_for('main.index'))
    
    return render_template(
        'admin/review_form.html',
        review_return_url=_safe_review_return_url(request.args.get('return_to'))
    )


@admin_bp.route('/form/<int:form_id>')
@login_required
def get_form_detail(form_id):
    """统一表单详情入口。"""
    try:
        # 检查用户权限
        user = User.query.get(session['user_id'])
        form = LectureForm.query.get_or_404(form_id)
        
        # 权限检查
        has_permission = False
        if user.role == '超级管理员':
            has_permission = True
        elif user.role == '管理员':
            # 管理员只能查看本部门的表单
            listener = _active_user_query().filter_by(number=form.listener_number).first()
            if listener and listener.department == user.department:
                has_permission = True
        else:
            # 信息员只能查看自己的表单
            if form.listener_number == user.number:
                has_permission = True
                
        if not has_permission:
            if request.args.get('format') != 'json':
                flash_forbidden('表单详情', '该表单不在当前账号的可见范围内。', action='查看')
                return redirect(url_for('main.index'))
            return forbidden_json('表单详情', '该表单不在当前账号的可见范围内。', action='查看')
            
        # 渲染表单详情模板
        reviewer_display_mode = get_reviewer_display_mode()
        score_record = ScoreRecord.query.filter_by(form_id=form.id).first()
        if request.args.get('format') != 'json':
            return render_template(
                'admin/form_detail_page.html',
                form=form,
                reviewer_display_mode=reviewer_display_mode,
                score_record=score_record
            )
        html = render_template(
            'admin/form_detail_content.html',
            form=form,
            reviewer_display_mode=reviewer_display_mode,
            score_record=score_record
        )
        return jsonify({'success': True, 'html': html})
        
    except Exception as e:
        if request.args.get('format') != 'json':
            flash(str(e), 'error')
            return redirect(url_for('main.index'))
        return jsonify({'success': False, 'message': str(e)})


@admin_bp.route('/api/review/permission', methods=['GET'])
def get_review_permission():
    """获取当前用户的审核权限"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        
        permission = get_user_review_permission(session['user_id'])
        presentation = get_review_permission_presentation(permission)
        
        return jsonify({
            'success': True,
            'permission': permission,
            'has_permission': permission is not None,
            **presentation,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/batch_auto_check', methods=['POST'])
@login_required
def batch_auto_check():
    """Legacy adapter for the additive evidence-only batch API."""
    permission = get_user_review_permission(session['user_id'])
    if permission != '审表_中心':
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '自动审核',
                '当前账号没有执行自动审核的权限。',
                action='执行',
            ),
        }), 403
    from app.review_automation.routes import create_batch_response

    return create_batch_response(
        legacy_force_ignored=True,
        actor_id=session['user_id'],
    )


@admin_bp.route('/api/review/batch_auto_check/preview', methods=['POST'])
@login_required
def batch_auto_check_preview():
    """Legacy adapter for the evidence-only batch preview."""
    permission = get_user_review_permission(session['user_id'])
    if permission != '审表_中心':
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '自动审核预览',
                '当前账号没有查看自动审核预览的权限。',
                action='查看',
            ),
        }), 403
    from app.review_automation.routes import preview_batch_response

    return preview_batch_response(
        legacy_force_ignored=True,
        actor_id=session['user_id'],
    )


@admin_bp.route('/api/review/auto_check/status', methods=['GET'])
@login_required
def get_auto_check_status():
    """Legacy status adapter backed by the newest evidence-only batch."""
    permission = get_user_review_permission(session['user_id'])
    if permission != '审表_中心':
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '自动审核任务状态',
                '当前账号没有查看自动审核任务状态的权限。',
                action='查看',
            ),
        }), 403
    from app.review_automation.routes import status_batch_response

    return status_batch_response(
        request.args.get('batch_id'),
        actor_id=session['user_id'],
    )


@admin_bp.route('/auto_review/results')
@login_required
def auto_review_results_page():
    """自动审核结果页面（专业审查）"""
    permission = get_user_review_permission(session['user_id'])
    if permission != '审表_中心':
        return redirect(url_for('admin.review_forms'))
    return render_template('admin/auto_review_results.html')


@admin_bp.route('/api/review/user-structure', methods=['GET'])
@login_required
def get_user_structure_api():
    """获取用户结构数据（用于审核权限范围显示）"""
    try:
        permission = get_user_review_permission(session['user_id'])
        if permission:
            structure = get_user_structure_for_review(session['user_id'])
            presentation = get_review_permission_presentation(permission)
            return jsonify({
                'success': True,
                'permission': permission,
                'structure': structure,
                **presentation,
            })
        manage_permission = get_user_manage_permission(session['user_id'])
        if manage_permission == '管理部门':
            current_user = User.query.get(session['user_id'])
            users = _active_user_query().filter(
                User.role.in_(['信息员', '管理员']),
                User.department == current_user.department
            ).all()
            groups = {}
            for user in users:
                if user.id == current_user.id:
                    continue
                group = Group.query.get(user.group_id) if user.group_id else None
                group_name = group.name if group else UNASSIGNED_GROUP_NAME
                if group_name not in groups:
                    groups[group_name] = []
                groups[group_name].append({
                    'id': user.id,
                    'name': user.name,
                    'number': user.number,
                    'role': user.role
                })
            return jsonify({
                'success': True,
                'permission': manage_permission,
                'structure': groups
            })
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '审核范围用户',
                '当前账号没有查看审核范围用户的权限。',
                action='查看',
            ),
        }), 403
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/statistics', methods=['GET'])
@login_required
def get_review_statistics():
    """获取审核统计数据（按筛选时间统计完成/未完成任务人数）"""
    try:
        permission = get_user_review_permission(session['user_id'])
        if not permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '审表考评统计',
                    '当前账号没有查看审核统计的权限。',
                    action='查看',
                ),
            }), 403

        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        if not start_date_str or not end_date_str:
            return jsonify({'success': False, 'message': '请先在筛选条件中选择开始和结束时间'}), 400

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
        time_filter_type = _normalize_review_form_time_filter(request.args.get('time_filter_type'))

        required_count = int(SystemSetting.get('teaching_required_submission', 1) or 1)
        required_count = max(required_count, 1)

        current_user = User.query.get(session['user_id'])
        query = _active_user_query().filter(User.role.in_(['信息员', '管理员']))
        if permission == '审表_小组':
            group_scope_ids = get_reviewable_users(session['user_id'])
            if group_scope_ids:
                query = query.filter(User.id.in_(group_scope_ids))
            else:
                query = query.filter(db.text('1=0'))
        elif permission == '审表_部门':
            query = query.filter(User.department == current_user.department)
        users = [u for u in query.all() if u.id != current_user.id]
        user_numbers = [u.number for u in users]

        if not user_numbers:
            return jsonify({
                'success': True,
                'range_info': {
                    'start_date': start_date_str,
                    'end_date': end_date_str
                },
                'stats': {
                    'completed': {'count': 0, 'users': []},
                    'incomplete': {'count': 0, 'users': []}
                }
            })

        user_group_map = {}
        for group_data in _latest_form_groups_for_users(user_numbers):
            latest_form = group_data.get('latest_form')
            if not latest_form:
                continue
            filter_dt = _build_review_form_filter_datetime(group_data, time_filter_type)
            if not filter_dt or filter_dt < start_date or filter_dt >= end_date:
                continue
            user_group_map.setdefault(latest_form.listener_number, []).append(group_data)

        completed_users = []
        incomplete_users = []
        valid_completed_statuses = {'待审核', '部门已审核', '中心已审核'}

        for user in users:
            submitted_groups = user_group_map.get(user.number, []) or []
            completed_count = 0
            form_summaries = []

            for group_data in submitted_groups:
                latest = group_data['latest_form']
                if latest.status in valid_completed_statuses:
                    completed_count += 1
                form_summaries.append({
                    'id': latest.id,
                    'date': latest.lecture_date,
                    'period': latest.class_period,
                    'status': latest.status
                })

            user_info = {
                'id': user.id,
                'name': user.name,
                'number': user.number,
                'department': user.department,
                'group': user.group,
                'submitted_count': completed_count,
                'required_count': required_count,
                'forms': form_summaries
            }

            if completed_count >= required_count:
                completed_users.append(user_info)
            else:
                incomplete_users.append(user_info)

        return jsonify({
            'success': True,
            'range_info': {
                'start_date': start_date_str,
                'end_date': end_date_str
            },
            'stats': {
                'completed': {
                    'count': len(completed_users),
                    'users': completed_users
                },
                'incomplete': {
                    'count': len(incomplete_users),
                    'users': incomplete_users
                }
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/forms', methods=['GET'])
def get_forms_for_review():
    """获取可审核的表单列表"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        
        permission = get_user_review_permission(session['user_id'])
        if not permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有审核权限。',
                    action='打开',
                ),
            }), 403
        
        # 获取查询参数
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        status_filter = request.args.get('status')
        time_filter_type = _normalize_review_form_time_filter(request.args.get('time_filter_type'))
        user_ids = request.args.getlist('user_ids')  # 可以是多个用户ID
        
        # 构建查询
        query = LectureForm.query
        
        # 用户范围过滤
        reviewable_user_ids = get_reviewable_users(session['user_id'])
        if user_ids:
            # 取交集，确保只能查看有权限的用户
            filtered_user_ids = [int(uid) for uid in user_ids if int(uid) in reviewable_user_ids]
            if filtered_user_ids:
                # 通过listener_number关联用户
                users = _active_user_query().filter(User.id.in_(filtered_user_ids)).all()
                listener_numbers = [user.number for user in users]
                query = query.filter(LectureForm.listener_number.in_(listener_numbers))
            else:
                # 没有有效的用户ID，返回空结果
                return jsonify({'success': True, 'forms': []})
        else:
            # 没有指定用户，使用权限范围内的所有用户
            users = _active_user_query().filter(User.id.in_(reviewable_user_ids)).all()
            listener_numbers = [user.number for user in users]
            query = query.filter(LectureForm.listener_number.in_(listener_numbers))
        
        parsed_start = None
        parsed_end = None
        if start_date:
            try:
                parsed_start = datetime.strptime(start_date, '%Y-%m-%d')
            except Exception:
                return jsonify({'success': False, 'message': '开始时间格式错误'}), 400
        if end_date:
            try:
                parsed_end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            except Exception:
                return jsonify({'success': False, 'message': '结束时间格式错误'}), 400

        # 获取所有表单，按updated_at倒序排列
        forms = query.order_by(LectureForm.updated_at.desc(), LectureForm.id.desc()).all()
        reviewer_display_mode = get_reviewer_display_mode()
        reviewer_ids = list({f.reviewer_id for f in forms if f.reviewer_id})
        reviewer_map = {}
        if reviewer_ids:
            reviewers = User.query.filter(User.id.in_(reviewer_ids)).all()
            reviewer_map = {r.id: r for r in reviewers}
        
        # 按unique_id分组，保持每组内按updated_at倒序
        grouped_forms = {}
        for form in forms:
            unique_id = form.unique_id or form.id
            if unique_id not in grouped_forms:
                grouped_forms[unique_id] = []
            grouped_forms[unique_id].append(form)
        
        # 构建返回数据，按最新更新时间排序分组
        result = []
        # 按每组最新的updated_at排序
        sorted_groups = sorted(
            grouped_forms.items(),
            key=lambda x: max((form.updated_at or form.created_at) for form in x[1]),
            reverse=True
        )
        from app.review_automation.routes import latest_assessment_summaries
        automation_summaries = latest_assessment_summaries([
            max(form_group, key=lambda item: item.id).id
            for _, form_group in sorted_groups
        ])
        
        for unique_id, form_group in sorted_groups:
            sorted_form_group = sorted(form_group, key=lambda f: f.id, reverse=True)
            latest_form = sorted_form_group[0]
            group_data_for_filter = {
                'unique_id': unique_id,
                'latest_form': latest_form,
                'forms': sorted_form_group
            }
            filter_dt = _build_review_form_filter_datetime(group_data_for_filter, time_filter_type)
            if parsed_start and (not filter_dt or filter_dt < parsed_start):
                continue
            if parsed_end and (not filter_dt or filter_dt >= parsed_end):
                continue
            if status_filter and latest_form.status != status_filter:
                continue
            group_data = {
                'unique_id': unique_id,
                'forms': []
            }
            
            for form in sorted_form_group:
                reviewer = reviewer_map.get(form.reviewer_id) if form.reviewer_id else None
                if reviewer_display_mode == 'number':
                    reviewer_display = reviewer.number if reviewer else '-'
                else:
                    reviewer_display = reviewer.name if reviewer else '-'
                group_data['forms'].append({
                    'id': form.id,
                    'unique_id': form.unique_id or form.id,
                    'listener_name': form.listener_name,
                    'listener_number': form.listener_number,
                    'course_title': form.course_title,
                    'teacher_name': form.teacher_name,
                    'lecture_date': form.lecture_date,
                    'status': form.status,
                    'can_review': (
                        form.id == latest_form.id
                        and can_review_status(session['user_id'], form.status, permission=permission)
                    ),
                    'created_at': form.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                    'updated_at': form.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
                    'review_comment': form.review_comment or '',
                    'reviewer_id': form.reviewer_id,
                    'reviewer_display': reviewer_display,
                    'audit_tag': form.audit_tag or '',
                    'audit_tag_info': _serialize_audit_tag(form.audit_tag),
                    'automation': automation_summaries.get(form.id) if form.id == latest_form.id else None,
                })
            
            result.append(group_data)
        
        return jsonify({'success': True, 'forms': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/form/<int:form_id>', methods=['GET'])
def get_form_for_review(form_id):
    """获取单个表单的详细信息（用于审核）"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        
        permission = get_user_review_permission(session['user_id'])
        if not permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有审核权限。',
                    action='打开',
                ),
            }), 403
        
        form = LectureForm.query.get_or_404(form_id)
        
        # 检查是否有权限审核此表单
        if not is_form_in_review_scope(session['user_id'], form):
            return forbidden_json('表单审核', '该表单不在当前审核范围内。', action='审核')
        
        # 构建表单数据
        reviewer_display_mode = get_reviewer_display_mode()
        if form.reviewer:
            reviewer_display = form.reviewer.number if reviewer_display_mode == 'number' else form.reviewer.name
        else:
            reviewer_display = None
        latest_form = form.get_latest_version()
        form_data = {
            'id': form.id,
            'unique_id': form.unique_id or form.id,
            'listener_name': form.listener_name,
            'listener_number': form.listener_number,
            'course_changes': form.course_changes,
            'lecture_date': form.lecture_date,
            'class_period': form.class_period,
            'lecture_location': form.lecture_location,
            'teacher_name': form.teacher_name,
            'teacher_college': form.teacher_college,
            'course_title': form.course_title,
            'student_grade_class': form.student_grade_class,
            'abnormal_situation': form.abnormal_situation,
            'teaching_method': form.teaching_method,
            'classroom_discipline': form.classroom_discipline,
            'classroom_atmosphere': form.classroom_atmosphere,
            'courseware_quality': form.courseware_quality,
            'overall_effect': form.overall_effect,
            'quality_case': form.quality_case,
            'course_feedback': form.course_feedback,
            'suggestions': form.suggestions,
            'student_signature1': form.student_signature1,
            'contact_phone1': form.contact_phone1,
            'student_signature2': form.student_signature2,
            'contact_phone2': form.contact_phone2,
            'status': form.status,
            'can_review': (
                form.id == latest_form.id
                and can_review_status(session['user_id'], form.status, permission=permission)
            ),
            'reviewer_id': form.reviewer_id,
            'reviewer_display': reviewer_display,
            'review_time': form.review_time.strftime('%Y-%m-%d %H:%M:%S') if form.review_time else None,
            'review_comment': form.review_comment,
            'created_at': form.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': form.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
            'audit_tag': form.audit_tag or '',
            'audit_tag_info': _serialize_audit_tag(form.audit_tag)
        }
        from app.review_automation.routes import latest_assessment_summaries
        automation = latest_assessment_summaries([latest_form.id]).get(latest_form.id)
        form_data['automation'] = automation
        form_data['automation_evidence_url'] = automation.get('evidence_url') if automation else None
        
        score_record = ScoreRecord.query.filter_by(form_id=form.id).first()
        score_data = []
        if score_record:
            score_data = [{
                'reason': item.reason,
                'department_score': item.department_score,
                'personal_score': item.personal_score,
                'is_auto_generated': item.is_auto_generated
            } for item in score_record.items]

        return jsonify({
            'success': True,
            'form': form_data,
            'automation': automation,
            'automation_evidence_url': form_data['automation_evidence_url'],
            'permission': permission,
            'permission_label': get_review_permission_presentation(permission)['permission_label'],
            'score_data': score_data
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/form-tags/preview', methods=['POST'])
@role_required('超级管理员')
def preview_review_form_tags():
    data = request.get_json() or {}
    group_entries, error_response, status_code = _load_review_form_groups_for_definition(data.get('form_ids', []))
    if error_response:
        return error_response, status_code

    preview_rows = []
    for entry in group_entries:
        form = entry['latest_form']
        preview_rows.append({
            'form_id': form.id,
            'unique_id': form.unique_id or form.id,
            'listener_name': form.listener_name,
            'listener_number': form.listener_number,
            'teacher_name': form.teacher_name,
            'course_title': form.course_title,
            'status': form.status,
            'audit_tag': form.audit_tag or '',
            'audit_tag_info': _serialize_audit_tag(form.audit_tag),
            'version_count': len(entry['forms']),
        })

    return jsonify({
        'success': True,
        'forms': preview_rows,
    })


@admin_bp.route('/api/review/form-tags', methods=['PUT'])
@role_required('超级管理员')
def update_review_form_tags():
    data = request.get_json() or {}
    definitions = data.get('definitions', [])
    if not isinstance(definitions, list) or not definitions:
        return jsonify({'success': False, 'message': '请提交需要定义的表单标签'}), 400

    normalized_definitions = {}
    for definition in definitions:
        try:
            form_id = int(definition.get('form_id'))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'message': '存在非法表单ID'}), 400
        normalized_definitions[form_id] = {
            'review_tag': str(definition.get('review_tag') or '').strip(),
            'week_correction_tag': str(
                definition.get('week_correction_tag')
                or definition.get('late_tag')
                or ''
            ).strip(),
        }

    group_entries, error_response, status_code = _load_review_form_groups_for_definition(normalized_definitions.keys())
    if error_response:
        return error_response, status_code

    updated_rows = []
    updated_version_count = 0
    for entry in group_entries:
        latest_form = entry['latest_form']
        definition = normalized_definitions.get(latest_form.id)
        if not definition:
            for requested_form_id in entry.get('requested_form_ids', []):
                definition = normalized_definitions.get(requested_form_id)
                if definition:
                    break
        if not definition:
            continue
        review_tag = definition.get('review_tag')
        week_correction_tag = definition.get('week_correction_tag') or None
        audit_tag = build_audit_tag(review_tag, week_correction_tag)
        errors = validate_audit_tag(audit_tag)
        if errors:
            return jsonify({
                'success': False,
                'message': f'表单组最新版本ID {latest_form.id} 的标签不合法：{"；".join(errors)}'
            }), 400
        for form in entry['forms']:
            form.audit_tag = audit_tag
        updated_version_count += len(entry['forms'])
        updated_rows.append({
            'form_id': latest_form.id,
            'unique_id': entry['unique_id'],
            'audit_tag': audit_tag,
            'audit_tag_info': _serialize_audit_tag(audit_tag),
            'version_count': len(entry['forms']),
        })

    db.session.commit()
    return jsonify({
        'success': True,
        'message': f'已更新 {len(updated_rows)} 个表单组，共同步 {updated_version_count} 个版本的标签定义',
        'updated_forms': updated_rows,
    })


@admin_bp.route('/api/review/form/<int:form_id>', methods=['POST'])
def submit_form_review(form_id):
    """提交表单审核"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        
        permission = get_user_review_permission(session['user_id'])
        if not permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有审核权限。',
                    action='审核',
                ),
            }), 403
        
        original_form = LectureForm.query.get_or_404(form_id)
        
        # 检查是否有权限审核此表单
        if not is_form_in_review_scope(session['user_id'], original_form):
            return forbidden_json('表单审核', '该表单不在当前审核范围内。', action='审核')
        
        # 检查是否可以审核此状态的表单
        if not can_review_status(session['user_id'], original_form.status):
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有审核该表单的权限。',
                    action='审核',
                ),
            }), 403
        
        data = request.get_json()
        review_comment = data.get('review_comment', '')
        form_data = data.get('form_data', {})
        
        # 检查表单数据是否有修改
        modified_fields = []
        original_data = {
            'listener_name': original_form.listener_name,
            'course_changes': original_form.course_changes,
            'lecture_date': original_form.lecture_date,
            'class_period': original_form.class_period,
            'lecture_location': original_form.lecture_location,
            'teacher_name': original_form.teacher_name,
            'teacher_college': original_form.teacher_college,
            'course_title': original_form.course_title,
            'student_grade_class': original_form.student_grade_class,
            'abnormal_situation': original_form.abnormal_situation,
            'teaching_method': original_form.teaching_method,
            'classroom_discipline': original_form.classroom_discipline,
            'classroom_atmosphere': original_form.classroom_atmosphere,
            'courseware_quality': original_form.courseware_quality,
            'overall_effect': original_form.overall_effect,
            'quality_case': original_form.quality_case,
            'course_feedback': original_form.course_feedback,
            'suggestions': original_form.suggestions,
            'student_signature1': original_form.student_signature1,
            'contact_phone1': original_form.contact_phone1,
            'student_signature2': original_form.student_signature2,
            'contact_phone2': original_form.contact_phone2
        }
        
        field_names = {
            'listener_name': '听课人姓名',
            'course_changes': '课程信息变化',
            'lecture_date': '听课时间',
            'class_period': '第几节',
            'lecture_location': '听课地点',
            'teacher_name': '授课教师',
            'teacher_college': '教师所属学院',
            'course_title': '课程总标题',
            'student_grade_class': '专业年级',
            'abnormal_situation': '异常情况反映',
            'teaching_method': '主要教学方法',
            'classroom_discipline': '管理课堂纪律',
            'classroom_atmosphere': '调动课堂气氛',
            'courseware_quality': '课件制作质量',
            'overall_effect': '整体教学效果',
            'quality_case': '优质案例推荐',
            'course_feedback': '课程反馈',
            'suggestions': '不足及建议',
            'student_signature1': '听课班级同学签名1',
            'contact_phone1': '联系电话1',
            'student_signature2': '听课班级同学签名2',
            'contact_phone2': '联系电话2'
        }
        
        for field, original_value in original_data.items():
            new_value = form_data.get(field, original_value)
            if str(new_value or '') != str(original_value or ''):
                modified_fields.append(field_names.get(field, field))
        
        # 处理听课时间（优先使用带星期几的显示格式）
        lecture_date = form_data.get('lecture_date_display') or form_data.get('lecture_date', original_form.lecture_date)
        
        # 处理节次（优先使用自动补全的格式）
        class_period = form_data.get('class_period') or f"第{form_data.get('start_period', '')}-{form_data.get('end_period', '')}节" if form_data.get('start_period') else form_data.get('class_period', original_form.class_period)
        
        new_status = get_next_status_after_review(session['user_id'])
        unique_id = original_form.unique_id or original_form.id
        latest_form = original_form.get_latest_version()
        if latest_form and latest_form.id != original_form.id:
            return jsonify({'success': False, 'message': '该表单已有更新版本，请刷新页面后操作'}), 400

        target_form = None
        # 如果当前版本已经是审核产生的版本（ID != unique_id），则直接在当前版本上修改
        # 或者如果状态没有改变，也在当前版本上修改
        if latest_form and (latest_form.id != unique_id or latest_form.status == new_status):
            target_form = latest_form
        else:
            target_form = LectureForm(
                listener_name=original_form.listener_name,
                listener_number=original_form.listener_number,
                registration_id=original_form.registration_id,
                course_changes=original_form.course_changes,
                lecture_date=original_form.lecture_date,
                class_period=original_form.class_period,
                lecture_location=original_form.lecture_location,
                teacher_name=original_form.teacher_name,
                teacher_college=original_form.teacher_college,
                course_title=original_form.course_title,
                student_grade_class=original_form.student_grade_class,
                abnormal_situation=original_form.abnormal_situation,
                teaching_method=original_form.teaching_method,
                classroom_discipline=original_form.classroom_discipline,
                classroom_atmosphere=original_form.classroom_atmosphere,
                courseware_quality=original_form.courseware_quality,
                overall_effect=original_form.overall_effect,
                quality_case=original_form.quality_case,
                course_feedback=original_form.course_feedback,
                suggestions=original_form.suggestions,
                student_signature1=original_form.student_signature1,
                contact_phone1=original_form.contact_phone1,
                student_signature2=original_form.student_signature2,
                contact_phone2=original_form.contact_phone2,
                audit_tag=original_form.audit_tag,
                unique_id=unique_id,
                created_at=original_form.created_at
            )
            db.session.add(target_form)

        target_form.listener_name = form_data.get('listener_name', original_form.listener_name)
        target_form.listener_number = original_form.listener_number
        course_changes_raw = (form_data.get('course_changes') or '').strip()
        target_form.course_changes = course_changes_raw or original_form.course_changes or '无'
        target_form.lecture_date = lecture_date
        target_form.class_period = class_period
        target_form.lecture_location = form_data.get('lecture_location', original_form.lecture_location)
        target_form.teacher_name = form_data.get('teacher_name', original_form.teacher_name)
        target_form.teacher_college = form_data.get('teacher_college', original_form.teacher_college)
        target_form.course_title = form_data.get('course_title', original_form.course_title)
        target_form.student_grade_class = form_data.get('student_grade_class', original_form.student_grade_class)
        target_form.abnormal_situation = form_data.get('abnormal_situation', original_form.abnormal_situation)
        target_form.teaching_method = form_data.get('teaching_method', original_form.teaching_method)
        target_form.classroom_discipline = form_data.get('classroom_discipline', original_form.classroom_discipline)
        target_form.classroom_atmosphere = form_data.get('classroom_atmosphere', original_form.classroom_atmosphere)
        target_form.courseware_quality = form_data.get('courseware_quality', original_form.courseware_quality)
        target_form.overall_effect = form_data.get('overall_effect', original_form.overall_effect)
        target_form.quality_case = form_data.get('quality_case', original_form.quality_case)
        target_form.course_feedback = form_data.get('course_feedback', original_form.course_feedback)
        target_form.suggestions = form_data.get('suggestions', original_form.suggestions)
        target_form.student_signature1 = form_data.get('student_signature1', original_form.student_signature1)
        target_form.contact_phone1 = form_data.get('contact_phone1', original_form.contact_phone1)
        target_form.student_signature2 = form_data.get('student_signature2', original_form.student_signature2)
        target_form.contact_phone2 = form_data.get('contact_phone2', original_form.contact_phone2)
        target_form.status = new_status
        target_form.reviewer_id = session['user_id']
        target_form.review_time = datetime.now()
        target_form.review_comment = review_comment
        target_form.updated_at = datetime.now()

        if not original_form.unique_id:
            original_form.unique_id = original_form.id
            db.session.add(original_form)

        db.session.flush()
        old_score_record = ScoreRecord.query.filter_by(form_id=target_form.id).first()
        if old_score_record:
            db.session.delete(old_score_record)

        score_data = data.get('score_data', [])
        if score_data:
            total_dept = 0.0
            total_pers = 0.0
            score_record = ScoreRecord(
                form_id=target_form.id,
                reviewer_id=session['user_id']
            )
            db.session.add(score_record)
            db.session.flush() # 获取 score_record.id
            
            for item in score_data:
                reason = item.get('reason')
                dept_score = float(item.get('department_score', 0))
                pers_score = float(item.get('personal_score', 0))
                is_auto = item.get('is_auto', False)
                
                if dept_score > 0 or pers_score > 0:
                    score_item = ScoreItem(
                        score_record_id=score_record.id,
                        reason=reason,
                        department_score=dept_score,
                        personal_score=pers_score,
                        is_auto_generated=is_auto
                    )
                    db.session.add(score_item)
                    total_dept += dept_score
                    total_pers += pers_score
            
            score_record.total_department_score = total_dept
            score_record.total_personal_score = total_pers
        
        # 如果有修改字段，在审核意见后添加修改说明
        if modified_fields:
            modification_note = f"\n\n[系统记录] 审核人修改了以下字段：{', '.join(modified_fields)}"
            target_form.review_comment = (review_comment or '无') + modification_note

        db.session.add(target_form)
        delete_review_form_draft(session['user_id'], form_id)
        db.session.commit()

        return jsonify({
            'success': True, 
            'message': f'表单审核完成，状态已更新为"{new_status}"',
            'new_status': new_status,
            'modified_fields': modified_fields
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/form/<int:form_id>', methods=['DELETE'])
@login_required
def delete_form(form_id):
    """删除单个表单版本"""
    try:
        permission = get_user_review_permission(session['user_id'])
        # 删除是破坏性管理动作：仅中心级审核权限/超级管理员允许，后端独立强制。
        if permission != '审表_中心':
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有删除表单的权限。',
                    action='删除',
                ),
            }), 403

        form_to_delete = LectureForm.query.get_or_404(form_id)

        if not is_form_in_review_scope(session['user_id'], form_to_delete):
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单',
                    '当前账号没有删除该表单的权限。',
                    action='删除',
                ),
            }), 403

        registration_id = form_to_delete.registration_id
        score_record = ScoreRecord.query.filter_by(form_id=form_id).first()
        if score_record:
            db.session.delete(score_record)
        db.session.delete(form_to_delete)
        db.session.flush()

        if registration_id:
            remains = LectureForm.query.filter_by(registration_id=registration_id).count()
            if remains == 0:
                registration = CourseRegistration.query.get(registration_id)
                if registration:
                    db.session.delete(registration)

        db.session.commit()
        return jsonify({'success': True, 'message': '表单版本删除成功'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/group/<int:group_id>', methods=['DELETE'])
@login_required
def delete_form_group(group_id):
    """删除整个表单组及关联数据"""
    try:
        permission = get_user_review_permission(session['user_id'])
        # 删除表单组会连带清理预约/评分，必须由中心级审核权限/超级管理员执行。
        if permission != '审表_中心':
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单审核',
                    '当前账号没有删除表单组的权限。',
                    action='删除',
                ),
            }), 403

        group_forms = LectureForm.query.filter(
            db.or_(LectureForm.unique_id == group_id, LectureForm.id == group_id)
        ).all()
        if not group_forms:
            return jsonify({'success': False, 'message': '表单组不存在'}), 404

        _allowed_forms, denied_forms = partition_forms_by_review_scope(
            session['user_id'],
            group_forms,
        )
        if denied_forms:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '表单组',
                    '当前账号没有删除该表单组的权限。',
                    action='删除',
                ),
            }), 403

        form_ids = [form.id for form in group_forms]
        registration_ids = list({form.registration_id for form in group_forms if form.registration_id})

        score_records = ScoreRecord.query.filter(ScoreRecord.form_id.in_(form_ids)).all()
        for score_record in score_records:
            db.session.delete(score_record)

        for form in group_forms:
            db.session.delete(form)
        db.session.flush()

        deleted_registration_count = 0
        for registration_id in registration_ids:
            remains = LectureForm.query.filter_by(registration_id=registration_id).count()
            if remains == 0:
                registration = CourseRegistration.query.get(registration_id)
                if registration:
                    db.session.delete(registration)
                    deleted_registration_count += 1

        db.session.commit()
        return jsonify({
            'success': True,
            'message': f'已删除表单组，共删除 {len(form_ids)} 个版本，清理 {deleted_registration_count} 条课程登记记录'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/auto_check', methods=['POST'])
@login_required
def auto_check_form():
    """对表单数据进行自动审核"""
    try:
        # 检查权限
        permission = get_user_review_permission(session['user_id'])
        if not permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '审表考评统计',
                     '当前账号没有查看审核统计的权限。',
                     action='查看',
                 ),
             }), 403

        form_data = request.get_json()
        
        # 构造类似 LectureForm 的对象供 auto_review 使用
        class FormLike:
            pass
        
        form = FormLike()
        # 映射字段
        setattr(form, 'id', form_data.get('id'))
        setattr(form, 'listener_name', form_data.get('listener_name'))
        setattr(form, 'listener_number', form_data.get('listener_number'))
        # reviewer_id 留空，避免与数据库ID混淆，auto_review现在优先使用listener_number
        # setattr(form, 'reviewer_id', form_data.get('listener_number')) 
        setattr(form, 'lecture_date', form_data.get('lecture_date'))
        setattr(form, 'class_period', form_data.get('class_period'))
        setattr(form, 'lecture_location', form_data.get('lecture_location'))
        setattr(form, 'teacher_name', form_data.get('teacher_name'))
        setattr(form, 'teacher_college', form_data.get('teacher_college'))
        setattr(form, 'course_title', form_data.get('course_title'))
        setattr(form, 'class_composition', form_data.get('student_grade_class')) # 映射到 student_grade_class
        setattr(form, 'teaching_method', form_data.get('teaching_method'))
        setattr(form, 'classroom_discipline', form_data.get('classroom_discipline'))
        setattr(form, 'classroom_atmosphere', form_data.get('classroom_atmosphere'))
        setattr(form, 'courseware_quality', form_data.get('courseware_quality'))
        setattr(form, 'overall_effect', form_data.get('overall_effect'))
        setattr(form, 'quality_case', form_data.get('quality_case'))
        setattr(form, 'course_feedback', form_data.get('course_feedback'))
        setattr(form, 'suggestions', form_data.get('suggestions'))
        setattr(form, 'phone', form_data.get('contact_phone1'))

        engine = AutoReviewEngine()
        result = engine.review_any(form)
        
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/review/reference_data', methods=['POST'])
@login_required
def get_reference_data():
    """获取参考数据（课表和通讯录）"""
    try:
        # 检查权限
        permission = get_user_review_permission(session['user_id'])
        if not permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '考评规则设置',
                     '当前账号没有查看考评规则的权限。',
                     action='查看',
                 ),
             }), 403

        form_data = request.get_json()
        
        engine = AutoReviewEngine()
        result = engine.search_reference_data(form_data)
        
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


