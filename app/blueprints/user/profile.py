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


@user_bp.route('/profile')
@login_required
def profile():
    """用户资料页面"""
    user = User.query.get(session['user_id'])
    
    # 计算统计信息（对所有角色）
    stats = None
    if user.role in ['信息员', '管理员', '超级管理员']:
        from app.models import LectureForm, ScoreRecord, SystemSetting, ScoreItem
        from datetime import datetime, timedelta
        from sqlalchemy import func
        import json

        # === 听课填表统计 ===
        
        # 1. 获取听课制度设置
        from app.services.teaching_calendar_settings import load_teaching_calendar_config
        from app.services.teaching_calendar import teaching_week_number
        from app.services.form_week_semantics import effective_form_week

        calendar_config, _calendar_error = load_teaching_calendar_config()

        required_submission = int(SystemSetting.query.filter_by(key='teaching_required_submission').first().value) if SystemSetting.query.filter_by(key='teaching_required_submission').first() else 1
        # 每周需听表单数量与需交表单数量一致
        required_listening = required_submission

        check_dept = SystemSetting.query.filter_by(key='teaching_check_dept_review').first()
        check_dept = check_dept.value == 'true' if check_dept else False

        check_center = SystemSetting.query.filter_by(key='teaching_check_center_review').first()
        check_center = check_center.value == 'true' if check_center else False

        def get_form_group_week_num(versions):
            if not versions:
                return -1
            latest_version = versions[-1]
            if calendar_config is None:
                return -1
            week_no = effective_form_week(
                latest_version.lecture_date,
                latest_version.audit_tag,
                calendar_config,
            )
            return week_no if week_no is not None else -1

        def count_feedback_chars(form):
            if not form:
                return 0
            text = f"{form.course_feedback or ''}{form.suggestions or ''}"
            return len(''.join(str(text).split()))

        # 计算当前教学周
        current_week_num = 0
        if calendar_config is not None:
            today = datetime.now().date()
            resolved = teaching_week_number(today, calendar_config)
            current_week_num = resolved if resolved is not None else -1
        
        # 2. 获取用户所有表单并按 unique_id 分组
        all_forms = LectureForm.query.filter_by(listener_number=user.number).order_by(LectureForm.created_at).all()
        
        form_groups = {}
        for f in all_forms:
            uid = f.unique_id or f.id
            if uid not in form_groups:
                form_groups[uid] = []
            form_groups[uid].append(f)
            
        # 3. 统计数据
        # 总计
        total_submitted_count = len(form_groups)
        total_approved_count = 0
        total_reward_count = 0
        total_feedback_chars = 0
        
        # 本周
        week_submitted_count = 0
        week_approved_count = 0
        week_error_free_count = 0 # 临时变量，用于计算本周奖励
        
        # 遍历每个表单组进行分析
        # 奖励表逻辑：
        #   按周计算：每周奖励 = max(0, 该周无错误表单数 - 每周需听表单数)
        #   总奖励 = 所有周奖励之和
        
        weekly_error_free_counts = {} # {week_num: count}
        
        for uid, versions in form_groups.items():
            # 排序版本
            versions.sort(key=lambda x: x.id)
            
            # 确定该表单组属于哪一周：优先使用最新版本定义的“第x周”，否则按最新版本听课时间计算
            submit_week = get_form_group_week_num(versions)
            
            # 统计提交数
            if submit_week == current_week_num:
                week_submitted_count += 1
                
            # 统计审核通过数（检查是否有"中心已审核"状态的记录）
            approved_versions = [v for v in versions if v.status == '中心已审核']
            approved_form = approved_versions[-1] if approved_versions else None
            if approved_form:
                total_approved_count += 1
                total_feedback_chars += count_feedback_chars(approved_form)
                if submit_week == current_week_num:
                    week_approved_count += 1
            
            # 统计无错误表单
            # 无错误定义：
            # 若 check_dept=True: 需存在 "已提交" -> "部门已审核" 的流转，且部门审核产生的版本无修改（course_changes为'无'或空）
            # 若 check_center=True: 需存在 "部门已审核" -> "中心已审核" 的流转，且中心审核产生的版本无修改
            # 如果两个都True，则两个阶段都必须满足
            
            is_error_free = False
            
            # 只有通过审核的表单才有资格讨论是否无错误（通常意味着流程走完了）
            # 或者按照定义： "从某一状态到下一状态经过审核后无修改记录"
            # 简单起见，我们检查版本链
            
            has_dept_review = False
            dept_review_ok = True
            
            has_center_review = False
            center_review_ok = True
            
            # 寻找特定状态的表单版本
            for i, v in enumerate(versions):
                if v.status == '部门已审核':
                    has_dept_review = True
                    # 检查修改记录。
                    # 注意：v.course_changes 记录的是"本次审核产生的修改"？
                    # admin.py 中: new_form.course_changes = form_data.get('course_changes', '无')
                    # 实际上 course_changes 字段含义有点混淆，有时是用户填写的"课程变更"，有时被用作修改记录？
                    # 回看 admin.py submit_review:
                    # 前端传来的 changes 数组被用来生成评分项。
                    # course_changes 字段本身似乎是表单内容的一部分（课程信息变化）。
                    # 而修改记录是 ScoreRecord -> ScoreItem (is_auto_generated=True)。
                    # 所以判断"无修改"，应该检查是否存在对应的 ScoreRecord 且包含 is_auto_generated=True 的 ScoreItem。
                    
                    # 查找该版本对应的 ScoreRecord
                    score_rec = ScoreRecord.query.filter_by(form_id=v.id).first()
                    if score_rec:
                        # 检查是否有自动生成的评分项（代表有修改）
                        auto_items = ScoreItem.query.filter_by(score_record_id=score_rec.id, is_auto_generated=True).count()
                        if auto_items > 0:
                            dept_review_ok = False
                    # 如果没有ScoreRecord，可能是旧数据或未评分，暂且视为无修改？或者视为有修改？
                    # 严格来说，审核通过必然会有 submit_review 调用。
                    # 但之前的代码只有在有 score_data 时才创建 ScoreRecord。
                    # 如果审核人什么都没改直接提交，前端传来的 score_data 可能为空（如果没有修改项）。
                    # 所以没有 ScoreRecord 或者 ScoreRecord 没有 auto items 都可以视为无修改。
                    pass
                    
                if v.status == '中心已审核':
                    has_center_review = True
                    score_rec = ScoreRecord.query.filter_by(form_id=v.id).first()
                    if score_rec:
                        auto_items = ScoreItem.query.filter_by(score_record_id=score_rec.id, is_auto_generated=True).count()
                        if auto_items > 0:
                            center_review_ok = False
            
            # 判定逻辑
            criteria_met = True
            if check_dept:
                if not has_dept_review or not dept_review_ok:
                    criteria_met = False
            
            if check_center:
                if not has_center_review or not center_review_ok:
                    criteria_met = False
            
            # 如果没选任何检查项，怎么算？假设不算无错误，或者算？
            # 题意："可以选择提交到部门审核和中心审核状态...如果两者都选择..."
            # 暗示至少选一个。如果都没选，默认不算无错误吧。
            if not check_dept and not check_center:
                criteria_met = False
                
            if criteria_met:
                if submit_week > 0:
                    weekly_error_free_counts[submit_week] = weekly_error_free_counts.get(submit_week, 0) + 1
                    
        # 计算奖励表
        # 本周奖励
        week_error_free_count = weekly_error_free_counts.get(current_week_num, 0)
        week_reward_count = max(0, week_error_free_count - required_listening)
        
        # 总奖励
        for week, count in weekly_error_free_counts.items():
            total_reward_count += max(0, count - required_listening)
            
        # 距离上次交表天数
        days_since_last = '无'
        last_form_obj = LectureForm.query.filter_by(listener_number=user.number).order_by(LectureForm.created_at.desc()).first()
        if last_form_obj:
            delta = datetime.now() - last_form_obj.created_at
            days_since_last = delta.days
        
        # 旧的统计逻辑保留，用于兼容显示
        total_forms = total_submitted_count
        average_feedback_chars = total_feedback_chars / total_approved_count if total_approved_count else 0
        this_month = LectureForm.query.filter(
            LectureForm.listener_number == user.number,
            LectureForm.created_at >= datetime(datetime.now().year, datetime.now().month, 1)
        ).count() # 这个其实不准确，因为没去重unique_id，但暂时保持原样或更新
        
        # 更新 this_month 为按组计数
        this_month_groups = 0
        for uid, versions in form_groups.items():
            if versions[0].created_at >= datetime(datetime.now().year, datetime.now().month, 1):
                this_month_groups += 1
        this_month = this_month_groups

        last_submit = last_form_obj.created_at.strftime('%Y-%m-%d') if last_form_obj else '无'
        
        # 计算个人累计扣分
        user_forms = LectureForm.query.filter_by(listener_number=user.number).with_entities(LectureForm.id).all()
        user_form_ids = [f.id for f in user_forms]
        
        if user_form_ids:
            total_deduction = db.session.query(func.sum(ScoreRecord.total_personal_score))\
                .filter(ScoreRecord.form_id.in_(user_form_ids)).scalar() or 0.0
        else:
            total_deduction = 0.0

        assessment_items = []
        if user_form_ids:
            item_rows = db.session.query(ScoreItem, ScoreRecord, LectureForm)\
                .join(ScoreRecord, ScoreItem.score_record_id == ScoreRecord.id)\
                .join(LectureForm, ScoreRecord.form_id == LectureForm.id)\
                .filter(
                    LectureForm.listener_number == user.number,
                    db.or_(ScoreItem.personal_score > 0, ScoreItem.department_score > 0)
                ).all()

            for score_item, score_record, form in item_rows:
                assessment_time = (
                    score_record.updated_at
                    or score_record.created_at
                    or score_item.created_at
                    or form.review_time
                    or form.updated_at
                    or form.created_at
                )
                personal_score = float(score_item.personal_score or 0.0)
                department_score = float(score_item.department_score or 0.0)
                assessment_items.append({
                    'reason': score_item.reason or '未填写考评原因',
                    'personal_score': personal_score,
                    'department_score': department_score,
                    'score_sort': personal_score,
                    'assessment_time': assessment_time,
                    'assessment_timestamp': assessment_time.timestamp() if assessment_time else 0,
                    'assessment_time_text': assessment_time.strftime('%Y-%m-%d %H:%M') if assessment_time else '-',
                    'course_title': form.course_title or '-',
                    'teacher_name': form.teacher_name or '-',
                    'form_id': form.id
                })

        assessment_items.sort(
            key=lambda item: (
                item['score_sort'],
                item['department_score'],
                item['assessment_timestamp'],
                item['form_id']
            ),
            reverse=True
        )
            
        # 计算排名百分比
        # 1. 获取有评分记录的用户总分
        results = db.session.query(
            LectureForm.listener_number, 
            func.sum(ScoreRecord.total_personal_score).label('total')
        ).join(ScoreRecord, LectureForm.id == ScoreRecord.form_id)\
         .group_by(LectureForm.listener_number).all()
        
        scores_map = {r[0]: r[1] for r in results}
        
        # 2. 获取所有用户
        all_users = User.query.filter(
            User.role.in_(['信息员', '管理员', '超级管理员']),
            active_user_filter()
        ).with_entities(User.number).all()
        all_scores = []
        for u in all_users:
            s = scores_map.get(u.number, 0.0)
            all_scores.append(s)
            
        # 3. 排序（降序，扣分越多排名越靠前 -> 前20%为红色）
        all_scores.sort(reverse=True)
        
        # 4. 计算当前用户百分位
        user_score = scores_map.get(user.number, 0.0)
        percentile = 1.0
        if all_scores:
            try:
                # 使用 first index 获得最高排名
                rank_index = all_scores.index(user_score)
                rank = rank_index + 1
                percentile = rank / len(all_scores)
            except ValueError:
                percentile = 1.0
        
        stats = {
            'total_forms': total_forms,
            'this_month': this_month,
            'last_submit': last_submit,
            'total_deduction': total_deduction,
            'percentile': percentile,
            # 新增统计字段
            'current_week': current_week_num,
            'week_submitted': week_submitted_count,
            'week_approved': week_approved_count,
            'week_reward': week_reward_count,
            'total_submitted': total_submitted_count,
            'total_approved': total_approved_count,
            'total_reward': total_reward_count,
            'average_feedback_chars': average_feedback_chars,
            'total_feedback_chars': total_feedback_chars,
            'days_since_last': days_since_last,
            'assessment_items': assessment_items
        }
    
    leave_prompt = get_pending_leave_makeup(user)
    return render_template('user/profile.html', user=user, stats=stats, leave_prompt=leave_prompt)


@user_bp.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """编辑用户资料"""
    user = User.query.get(session['user_id'])
    editable_fields = (
        set(get_profile_editable_fields())
        if user.role == '信息员'
        else set(PROFILE_EDITABLE_FIELD_KEYS)
    )
    
    if request.method == 'POST':
        profile_fields = {
            'name': 'name',
            'gender': 'gender',
            'grade': 'grade',
            'college': 'college',
            'major': 'major',
            'dormitory': 'dormitory',
            'phone': 'phone',
            'qq': 'qq',
        }
        required_fields = {
            'name': '姓名',
            'gender': '性别',
            'grade': '年级',
            'college': '学院',
            'major': '专业',
            'phone': '手机号码',
        }
        for field_name, label in required_fields.items():
            if field_name in editable_fields and not request.form.get(field_name, '').strip():
                flash(f'请填写{label}', 'error')
                return render_template('user/edit_profile.html', user=user, editable_fields=editable_fields)
        phone = request.form.get('phone', '').strip()
        if 'phone' in editable_fields and not re.match(r'^1[3-9]\d{9}$', phone):
            flash('请输入有效的11位手机号码', 'error')
            return render_template('user/edit_profile.html', user=user, editable_fields=editable_fields)
        qq = request.form.get('qq', '').strip()
        if 'qq' in editable_fields and qq and not re.match(r'^[1-9]\d{4,10}$', qq):
            flash('请输入有效的QQ号码（5-11位数字）', 'error')
            return render_template('user/edit_profile.html', user=user, editable_fields=editable_fields)

        for field_name, model_attr in profile_fields.items():
            if field_name in editable_fields and field_name in request.form:
                setattr(user, model_attr, request.form.get(field_name, '').strip())
        
        try:
            db.session.commit()
            flash('资料更新成功', 'success')
            return redirect(url_for('user.profile'))
        except Exception as e:
            db.session.rollback()
            flash('更新失败，请重试', 'error')
    
    return render_template('user/edit_profile.html', user=user, editable_fields=editable_fields)
