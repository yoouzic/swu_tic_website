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


LECTURE_FORM_DRAFT_KEY = 'submit_form'


def _load_lecture_form_draft(user_id):
    return LectureFormDraft.query.filter_by(
        user_id=user_id,
        draft_key=LECTURE_FORM_DRAFT_KEY,
    ).first()


def _delete_lecture_form_draft(user_id):
    draft = _load_lecture_form_draft(user_id)
    if draft:
        db.session.delete(draft)
        return True
    return False


def _parse_draft_payload(draft):
    if not draft or not draft.payload_json:
        return {}
    try:
        payload = json.loads(draft.payload_json)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_draft_payload(raw_payload):
    if not isinstance(raw_payload, dict):
        return None
    normalized = {}
    for key, value in raw_payload.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized[key] = value
        elif isinstance(value, list):
            normalized[key] = [
                item for item in value
                if isinstance(item, (str, int, float, bool)) or item is None
            ]
    return normalized


def _normalize_submission_value(value):
    if value is None:
        return ''
    return str(value).strip()


def _build_submission_signature(form_data):
    signature_fields = [
        'listener_number', 'course_changes', 'lecture_date', 'class_period', 'lecture_location',
        'teacher_name', 'teacher_college', 'course_title', 'student_grade_class', 'abnormal_situation',
        'teaching_method', 'classroom_discipline', 'classroom_atmosphere', 'courseware_quality',
        'overall_effect', 'quality_case', 'course_feedback', 'suggestions', 'student_signature1',
        'contact_phone1', 'student_signature2', 'contact_phone2', 'registration_id'
    ]
    signature_payload = {
        field: _normalize_submission_value(form_data.get(field)) for field in signature_fields
    }
    signature_text = json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(signature_text.encode('utf-8')).hexdigest()


def _find_recent_duplicate_submission(user_number, form_data, now_time):
    signature = _build_submission_signature(form_data)
    recent_forms = LectureForm.query.filter(
        LectureForm.listener_number == user_number,
        LectureForm.status == '待审核',
        LectureForm.created_at >= now_time - timedelta(seconds=120)
    ).order_by(LectureForm.created_at.desc(), LectureForm.id.desc()).limit(20).all()
    for form in recent_forms:
        candidate_data = {
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
            'registration_id': form.registration_id
        }
        if _build_submission_signature(candidate_data) == signature:
            return form
    return None


@user_bp.route('/api/lecture_form_draft', methods=['GET', 'PUT', 'DELETE'])
@login_required
def lecture_form_draft():
    user_id = session['user_id']

    if request.method == 'GET':
        draft = _load_lecture_form_draft(user_id)
        return jsonify({
            'success': True,
            'exists': draft is not None,
            'data': _parse_draft_payload(draft),
            'updated_at': draft.updated_at.strftime('%Y-%m-%d %H:%M:%S') if draft else None,
        })

    if request.method == 'DELETE':
        deleted = _delete_lecture_form_draft(user_id)
        db.session.commit()
        return jsonify({'success': True, 'deleted': deleted})

    body = request.get_json(silent=True) or {}
    raw_payload = body.get('data', body)
    payload = _normalize_draft_payload(raw_payload)
    if payload is None:
        return jsonify({'success': False, 'message': 'Draft payload must be a JSON object'}), 400

    draft = _load_lecture_form_draft(user_id)
    if not draft:
        draft = LectureFormDraft(user_id=user_id, draft_key=LECTURE_FORM_DRAFT_KEY)
        db.session.add(draft)

    draft.payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    draft.updated_at = datetime.now()
    db.session.commit()
    return jsonify({
        'success': True,
        'updated_at': draft.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
    })


@user_bp.route('/my_forms')
@login_required
def my_forms():
    """Compatibility entry for the records tab in the activity center."""
    query_args = request.args.to_dict(flat=True)
    query_args['tab'] = 'records'
    return redirect(url_for('user.listening_registration', **query_args))


@user_bp.route('/delete_form/<int:form_id>', methods=['POST'])
@login_required
def delete_form(form_id):
    from app.models import LectureForm, ScoreRecord, CourseRegistration
    user = User.query.get(session['user_id'])
    form = LectureForm.query.get_or_404(form_id)

    if form.listener_number != user.number:
        return forbidden_json('我的表单', '只能删除自己提交的表单。', action='删除')

    unique_id = form.unique_id or form.id
    group_forms = LectureForm.query.filter(
        db.or_(LectureForm.unique_id == unique_id, LectureForm.id == unique_id)
    ).order_by(LectureForm.id.desc()).all()

    if len(group_forms) != 1:
        return jsonify({'success': False, 'message': '仅支持删除只有一个版本的表单'}), 400

    latest_form = group_forms[0]
    if latest_form.status != '待审核':
        return jsonify({'success': False, 'message': '仅支持删除待审核状态的表单'}), 400

    registration_id = latest_form.registration_id
    score_record = ScoreRecord.query.filter_by(form_id=latest_form.id).first()
    if score_record:
        db.session.delete(score_record)
    db.session.delete(latest_form)
    db.session.flush()

    if registration_id:
        remains = LectureForm.query.filter_by(registration_id=registration_id).count()
        if remains == 0:
            registration = CourseRegistration.query.get(registration_id)
            if registration:
                db.session.delete(registration)

    db.session.commit()
    return jsonify({'success': True, 'message': '表单删除成功'})


@user_bp.route('/submit_form', methods=['GET', 'POST'])
@login_required
def submit_form():
    """提交听课表单"""
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        from app.models import LectureForm
        from datetime import datetime
        import difflib
        
        # 获取听课时间（优先使用带星期几的显示格式）
        lecture_date = request.form.get('lecture_date_display') or request.form['lecture_date']
        
        # 获取节次（优先使用自动补全的格式）
        class_period = request.form.get('class_period') or f"第{request.form.get('start_period', '')}-{request.form.get('end_period', '')}节"
        
        # 获取关联的登记ID
        registration_id = request.form.get('registration_id')
        audit_tag = '需要人工审核' # 默认需要人工审核
        
        # 如果选择了已登记课程，先做服务端归属验证，再进行自动审核判断
        registration = None
        if registration_id:
            registration = Reservation.query.get(registration_id)
            if registration is None:
                flash('所选听课登记不存在或已被删除，请刷新后重试。', 'error')
                return render_template('user/lecture_form.html', user=user), 400
            if registration.user_id != user.id:
                flash('无权使用该听课登记。', 'error')
                return render_template('user/lecture_form.html', user=user), 403

            # 获取原始课程信息
            course = Course.query.filter_by(
                course_code=registration.course_code,
                selection_code=registration.selection_code
            ).first()

            if course:
                # 比较关键字段差异
                # 1. 课程名称
                s1 = difflib.SequenceMatcher(None, course.course_name, request.form['course_title'])
                ratio_title = s1.ratio()

                # 2. 教师姓名
                teacher_name_orig = course.teacher.name if course.teacher else ''
                s2 = difflib.SequenceMatcher(None, teacher_name_orig, request.form['teacher_name'])
                ratio_teacher = s2.ratio()

                # 3. 上课地点
                s3 = difflib.SequenceMatcher(None, str(course.class_location or ''), request.form['lecture_location'])
                ratio_location = s3.ratio()

                # 综合判断：如果关键信息相似度较高，则无需人工审核
                # 设定阈值为0.6（允许少量修改）
                if ratio_title > 0.6 and ratio_teacher > 0.6 and ratio_location > 0.6:
                    audit_tag = '无需人工审核'
                else:
                    audit_tag = '需要人工审核' # 修改较大

                # 标记登记记录为已使用
                registration.is_used = True
                db.session.add(registration)

        teaching_method = request.form['teaching_method']
        courseware_quality = request.form['courseware_quality']
        if 'PPT演示法' not in teaching_method:
            courseware_quality = '无'

        suggestions = request.form.get('suggestions', '无')
        leave_makeup = get_pending_leave_makeup(user)
        if leave_makeup:
            leave_week = leave_makeup['leave_week']
            audit_tag = build_audit_tag(audit_tag, build_week_correction_tag(leave_week))
            suggestions = append_leave_system_note(suggestions, leave_week)

        # 准备表单数据
        form_data = {
            'listener_name': f"{user.name}（{user.college}）",
            'listener_number': user.number,
            'course_changes': request.form.get('course_changes', '无'),
            'lecture_date': lecture_date,
            'class_period': class_period,
            'lecture_location': request.form['lecture_location'],
            'teacher_name': request.form['teacher_name'],
            'teacher_college': request.form['teacher_college'],
            'course_title': request.form['course_title'],
            'student_grade_class': request.form['student_grade_class'],
            'abnormal_situation': request.form.get('abnormal_situation', '无'),
            'teaching_method': teaching_method,
            'classroom_discipline': request.form['classroom_discipline'],
            'classroom_atmosphere': request.form['classroom_atmosphere'],
            'courseware_quality': courseware_quality,
            'overall_effect': request.form['overall_effect'],
            'quality_case': request.form['quality_case'],
            'course_feedback': request.form['course_feedback'],
            'suggestions': suggestions,
            'student_signature1': request.form['student_signature1'],
            'contact_phone1': request.form['contact_phone1'],
            'student_signature2': request.form.get('student_signature2'),
            'contact_phone2': request.form.get('contact_phone2'),
            'registration_id': registration_id,
            'audit_tag': audit_tag,
            'status': '待审核' # 提交后默认为待审核
        }
        
        try:
            unique_id = request.form.get('unique_id')
            target_form = None
            is_new_version = True
            
            if unique_id and unique_id.strip():
                # 检查是否存在可更新的最新版本
                latest_form = LectureForm.query.filter_by(unique_id=unique_id).order_by(LectureForm.id.desc()).first()
                
                # 如果最新版本存在，且状态为'待审核'，且属于当前用户，则更新该版本
                if latest_form and latest_form.status == '待审核' and latest_form.listener_number == user.number:
                    target_form = latest_form
                    is_new_version = False
                    
                    # 更新字段
                    for key, value in form_data.items():
                        if hasattr(target_form, key):
                            setattr(target_form, key, value)
                    target_form.updated_at = datetime.now()
                else:
                    # 创建新版本
                    target_form = LectureForm(**form_data)
                    target_form.unique_id = unique_id
            else:
                # 全新表单
                duplicate_form = _find_recent_duplicate_submission(user.number, form_data, datetime.now())
                if duplicate_form:
                    _delete_lecture_form_draft(user.id)
                    if leave_makeup:
                        record_leave_makeup_form(
                            user,
                            leave_makeup,
                            duplicate_form,
                            source='auto',
                            operator_user_id=user.id,
                        )
                        db.session.commit()
                    flash('检测到重复提交，系统已保留首次提交结果。', 'info')
                    return redirect(url_for('user.success', form_id=duplicate_form.id))
                target_form = LectureForm(**form_data)
            
            db.session.add(target_form)
            
            if is_new_version:
                db.session.flush() # 获取ID
                if not target_form.unique_id:
                    target_form.unique_id = target_form.id

            if leave_makeup:
                record_leave_makeup_form(
                    user,
                    leave_makeup,
                    target_form,
                    source='auto',
                        operator_user_id=user.id,
                )

            _delete_lecture_form_draft(user.id)
            
            db.session.commit()
            flash(f'表单提交成功！{" " if is_new_version else "原有表单已更新，等待重新审核。"}', 'success')
            # 跳转到成功页面，显示提交的表单详情
            return redirect(url_for('user.success', form_id=target_form.id))
        except Exception as e:
            db.session.rollback()
            flash(f'提交失败：{str(e)}', 'error')
    
    return render_template('user/lecture_form.html', user=user)


@user_bp.route('/form/edit/<int:form_id>')
@login_required
def edit_form(form_id):
    """编辑/重填表单"""
    from app.models import LectureForm
    import json
    
    user = User.query.get(session['user_id'])
    form = LectureForm.query.get_or_404(form_id)
    
    # 只能编辑自己的表单
    if form.listener_number != user.number:
        flash_forbidden('我的表单', '只能编辑自己提交的表单。', action='编辑')
        return redirect(url_for('user.my_forms'))
    
    # 构建表单数据字典
    form_data = {
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
        'review_comment': form.review_comment,
        'unique_id': form.unique_id or form.id
    }
    
    # 处理日期格式
    try:
        date_str = form.lecture_date
        if '星期' in date_str:
            date_str = date_str.split('星期')[0]
        date_str = date_str.replace('/', '-')
        form_data['lecture_date'] = date_str
    except:
        pass
        
    # 移除备注拆分逻辑
    # if '【备注】' in form.suggestions:
    #     parts = form.suggestions.split('【备注】')
    #     form_data['suggestions'] = parts[0]
    #     form_data['remarks'] = parts[1] if len(parts) > 1 else ''
    
    return render_template('user/lecture_form.html', user=user, form_data=form_data, form=form)


@user_bp.route('/success/<int:form_id>')
@login_required
def success(form_id):
    """表单提交成功页面"""
    from app.models import LectureForm
    user = User.query.get(session['user_id'])
    
    # 只能查看自己提交的表单
    form = LectureForm.query.filter_by(id=form_id, listener_number=user.number).first_or_404()
    
    return render_template('success.html', form=form)


@user_bp.route('/view_form/<int:form_id>')
@login_required
def view_form(form_id):
    """查看表单详情"""
    from app.models import LectureForm
    user = User.query.get(session['user_id'])
    
    # 只能查看自己提交的表单
    form = LectureForm.query.filter_by(id=form_id, listener_number=user.number).first_or_404()
    
    return redirect(url_for('admin.get_form_detail', form_id=form.id))
