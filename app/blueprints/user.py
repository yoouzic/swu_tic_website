from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from ..models import User, db, Course, ListeningBan, CourseRegistration as Reservation, Teacher, LectureForm, LectureFormDraft
from .auth import login_required
from ..utils.user_status import active_user_filter
from datetime import datetime, timedelta
from sqlalchemy import and_, func
from ..utils.time_validator import validate_listening_time, TimeValidator
from ..utils.audit_tags import build_audit_tag, build_week_correction_tag, parse_audit_tag
from ..utils.leave_management import append_leave_system_note, get_pending_leave_makeup, parse_lecture_date_value, record_leave_makeup_form
from ..utils.profile_settings import PROFILE_EDITABLE_FIELD_KEYS, get_profile_editable_fields
from ..utils.course_registration_limits import (
    get_current_teaching_week_no,
    parse_listening_week_no,
    validate_course_weekly_registration_limit,
)
import hashlib
import json
import re

user_bp = Blueprint('user', __name__, url_prefix='/user')

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

@user_bp.route('/profile')
@login_required
def profile():
    """用户资料页面"""
    user = User.query.get(session['user_id'])
    
    # 计算统计信息（对所有角色）
    stats = None
    if user.role in ['信息员', '管理员', '超级管理员']:
        from ..models import LectureForm, ScoreRecord, SystemSetting, ScoreItem
        from datetime import datetime, timedelta
        from sqlalchemy import func
        import json

        # === 听课填表统计 ===
        
        # 1. 获取听课制度设置
        first_week_str = SystemSetting.query.filter_by(key='teaching_first_week_monday').first()
        # 增加对空字符串的判断，防止 strptime 报错
        if first_week_str and first_week_str.value:
            try:
                first_week_date = datetime.strptime(first_week_str.value, '%Y-%m-%d').date()
            except ValueError:
                first_week_date = None
        else:
            first_week_date = None
        
        # 获取周起始日设置 (0=周一, ..., 6=周日)
        week_start_day_setting = SystemSetting.query.filter_by(key='teaching_week_start_day').first()
        week_start_day = int(week_start_day_setting.value) if week_start_day_setting else 0
        
        required_submission = int(SystemSetting.query.filter_by(key='teaching_required_submission').first().value) if SystemSetting.query.filter_by(key='teaching_required_submission').first() else 1
        # 每周需听表单数量与需交表单数量一致
        required_listening = required_submission
        
        check_dept = SystemSetting.query.filter_by(key='teaching_check_dept_review').first()
        check_dept = check_dept.value == 'true' if check_dept else False
        
        check_center = SystemSetting.query.filter_by(key='teaching_check_center_review').first()
        check_center = check_center.value == 'true' if check_center else False
        
        # 辅助：计算某个日期的周次（考虑自定义起始日）
        def get_week_num(date_obj):
            if not first_week_date or not date_obj:
                return -1
            if isinstance(date_obj, datetime):
                d = date_obj.date()
            else:
                d = date_obj
                
            # 算法：
            # 1. 计算日期d与第一周起始日first_week_date的天数差
            # 2. 如果自定义起始日不是周一，first_week_date (通常是周一) 需要调整吗？
            #    假设 first_week_date 仍然代表第一周的"基准周一"，
            #    但如果用户选了周六开始，那第一周应该从 first_week_date 所在的那个周六开始？还是上一个周六？
            #    通常教务系统的逻辑是：first_week_date 是第一周的周一。
            #    如果 week_start_day 是周六 (5)，意味着第一周是从 (first_week_date - 2天) 开始的。
            #    如果 week_start_day 是周一 (0)，意味着第一周是从 first_week_date 开始的。
            #    通用公式：调整后的第一周起始日 = first_week_date - (first_week_date.weekday() - week_start_day) % 7 天？
            #    不，应该是 first_week_date 所在周的起始日。
            #    如果 first_week_date 是 2023-09-04 (周一)
            #    若 start=0(周一)，start_date = 09-04
            #    若 start=5(周六)，start_date = 09-02 (上周六)
            #    若 start=6(周日)，start_date = 09-03 (上周日)
            
            # 计算第一周的实际起始日期
            # first_week_date.weekday() 返回 0(周一) 到 6(周日)
            # 我们想要找到 <= first_week_date 的最近一个 week_start_day
            days_to_subtract = (first_week_date.weekday() - week_start_day) % 7
            actual_start_date = first_week_date - timedelta(days=days_to_subtract)
            
            diff = (d - actual_start_date).days
            if diff < 0:
                return -1
            return (diff // 7) + 1

        def get_form_group_week_num(versions):
            if not versions:
                return -1
            latest_version = versions[-1]
            parsed_tag = parse_audit_tag(latest_version.audit_tag)
            if parsed_tag.get('week_correction_week_no') is not None:
                return parsed_tag['week_correction_week_no']
            return get_week_num(parse_lecture_date_value(latest_version.lecture_date))

        def count_feedback_chars(form):
            if not form:
                return 0
            text = f"{form.course_feedback or ''}{form.suggestions or ''}"
            return len(''.join(str(text).split()))

        # 计算当前教学周
        current_week_num = 0
        if first_week_date:
            today = datetime.now().date()
            current_week_num = get_week_num(today)
        
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

@user_bp.route('/my_forms')
@login_required
def my_forms():
    """我的听课表单 - 按unique_id分组显示"""
    from ..models import LectureForm
    from collections import defaultdict
    
    user = User.query.get(session['user_id'])
    
    # 获取搜索参数
    search = request.args.get('search', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # 基础查询 - 用户只能查看自己的表单
    query = LectureForm.query.filter_by(listener_number=user.number)
    
    # 应用搜索过滤
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
    
    # 获取所有表单
    all_forms = query.order_by(LectureForm.updated_at.desc()).all()
    
    # 按unique_id分组
    grouped_forms = defaultdict(list)
    for form in all_forms:
        unique_key = form.unique_id if form.unique_id else f"single_{form.id}"
        grouped_forms[unique_key].append(form)
    
    # 为每个分组排序并创建表单组字典
    form_groups = []
    for unique_id, versions in grouped_forms.items():
        # 按updated_at降序排序
        versions.sort(key=lambda x: x.updated_at or x.created_at, reverse=True)
        
        # 创建表单组字典
        form_group = {
            'unique_id': unique_id,
            'latest_form': versions[0],
            'versions': versions,
            'version_count': len(versions)
        }
        
        form_groups.append(form_group)
    
    # 按最新表单的更新时间排序
    form_groups.sort(key=lambda x: x['latest_form'].updated_at or x['latest_form'].created_at, reverse=True)

    registrations = Reservation.query.filter_by(user_id=user.id).order_by(Reservation.created_at.desc()).all()
    my_reservations = []
    for r in registrations:
        course = Course.query.filter_by(course_code=r.course_code, selection_code=r.selection_code).first()
        bind_count = LectureForm.query.filter_by(registration_id=r.id).count()
        is_bound = bind_count > 0
        my_reservations.append({
            'id': r.id,
            'course_code': r.course_code,
            'selection_code': r.selection_code,
            'course_name': course.course_name if course else '课程已删除',
            'teacher_name': course.teacher.name if course and course.teacher else '未知',
            'class_time': course.class_time if course else '',
            'class_location': course.class_location if course else '',
            'listening_info': r.listening_info,
            'created_at': r.created_at,
            'is_bound': is_bound,
            'bind_count': bind_count,
            'can_edit': not is_bound,
            'can_delete': not is_bound
        })
    
    return render_template('user/my_forms.html', forms=form_groups, user=user, my_reservations=my_reservations)

@user_bp.route('/delete_form/<int:form_id>', methods=['POST'])
@login_required
def delete_form(form_id):
    from ..models import LectureForm, ScoreRecord, CourseRegistration
    user = User.query.get(session['user_id'])
    form = LectureForm.query.get_or_404(form_id)

    if form.listener_number != user.number:
        return jsonify({'success': False, 'message': '您无权删除该表单'}), 403

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
        from ..models import LectureForm
        from datetime import datetime
        import difflib
        
        # 获取听课时间（优先使用带星期几的显示格式）
        lecture_date = request.form.get('lecture_date_display') or request.form['lecture_date']
        
        # 获取节次（优先使用自动补全的格式）
        class_period = request.form.get('class_period') or f"第{request.form.get('start_period', '')}-{request.form.get('end_period', '')}节"
        
        # 获取关联的登记ID
        registration_id = request.form.get('registration_id')
        audit_tag = '需要人工审核' # 默认需要人工审核
        
        # 如果选择了已登记课程，进行自动审核判断
        if registration_id:
            try:
                registration = Reservation.query.get(registration_id)
                if registration:
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
            except Exception as e:
                # 发生错误时保守处理
                audit_tag = '需要人工审核'
                print(f"Error checking registration: {e}")

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
    from ..models import LectureForm
    import json
    
    user = User.query.get(session['user_id'])
    form = LectureForm.query.get_or_404(form_id)
    
    # 只能编辑自己的表单
    if form.listener_number != user.number:
        flash('您没有权限编辑此表单', 'error')
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
    
    return render_template('user/lecture_form.html', user=user, form_data_json=json.dumps(form_data), form=form)

@user_bp.route('/success/<int:form_id>')
@login_required
def success(form_id):
    """表单提交成功页面"""
    from ..models import LectureForm
    user = User.query.get(session['user_id'])
    
    # 只能查看自己提交的表单
    form = LectureForm.query.filter_by(id=form_id, listener_number=user.number).first_or_404()
    
    return render_template('success.html', form=form)

@user_bp.route('/view_form/<int:form_id>')
@login_required
def view_form(form_id):
    """查看表单详情"""
    from ..models import LectureForm
    user = User.query.get(session['user_id'])
    
    # 只能查看自己提交的表单
    form = LectureForm.query.filter_by(id=form_id, listener_number=user.number).first_or_404()
    
    return redirect(url_for('admin.get_form_detail', form_id=form.id))

# ==================== 听课登记相关API ====================

@user_bp.route('/listening_registration')
@login_required
def listening_registration():
    """听课登记页面"""
    return render_template(
        'admin/course_feedback_management.html',
        page_mode='registration',
        page_title='听课登记'
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
        
        data = []
        for r in registrations:
            bind_count = db.session.query(func.count()).select_from(LectureForm).filter_by(registration_id=r.id).scalar() or 0
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
        
        result = []
        for r in reservations:
            bind_count = db.session.query(func.count()).select_from(LectureForm).filter_by(registration_id=r.id).scalar() or 0
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

        bind_count = db.session.query(func.count()).select_from(LectureForm).filter_by(registration_id=reservation.id).scalar() or 0
        if bind_count > 0:
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

        bind_count = db.session.query(func.count()).select_from(LectureForm).filter_by(registration_id=reservation.id).scalar() or 0
        if bind_count > 0:
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
