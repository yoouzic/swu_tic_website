# -*- coding: utf-8 -*-
"""Profile statistics — the single authoritative implementation.

Consumed by both the user profile route and the admin user-detail route, so
business rules (teaching-week semantics, weekly reward accounting, evaluation
sampling, percentile ranking and rating) exist exactly once.

Boundary: application/data service.  May depend on models, SQLAlchemy and the
teaching-calendar services, but never on Flask HTTP objects, blueprints or the
current login session — the target ``user`` is an explicit argument.

教学周类型约定：内部统一为 ``Optional[int]``（1..N = 有效教学周，None =
不属于教学周），不再使用 -1/0 sentinel，避免非教学周表单与当前周碰撞。
"""
from datetime import datetime

from sqlalchemy import func

from app.models import LectureForm, ScoreItem, ScoreRecord, SystemSetting, User, db
from app.services.form_week_semantics import effective_form_week
from app.services.teaching_calendar import teaching_term_start, teaching_week_number
from app.services.teaching_calendar_settings import load_teaching_calendar_config
from app.utils.user_status import active_user_filter

RATING_BAND_NONE = 'none'
RATING_BAND_ATTENTION = 'attention'
RATING_BAND_GOOD = 'good'
RATING_BAND_EXCELLENT = 'excellent'

# Profile 统计的目标用户资格与排名池资格共用同一角色集合（§10：不重复魔法列表）。
PROFILE_STATS_ELIGIBLE_ROLES = (
    '信息员',
    '管理员',
    '超级管理员',
)

_LABEL_CURRENT_WEEK = '当前教学周（第 {week} 周）'
_LABEL_BEFORE_TERM = '本学期教学周尚未开始'
_LABEL_NOT_IN_TERM = '当前不在教学周内'


def _resolve_current_week(calendar_config, now):
    """Return (current_week, current_week_label) with canonical semantics."""
    if calendar_config is None:
        return None, _LABEL_NOT_IN_TERM
    today = now.date()
    resolved_week = teaching_week_number(today, calendar_config)
    if resolved_week is not None:
        return resolved_week, _LABEL_CURRENT_WEEK.format(week=resolved_week)
    if today < teaching_term_start(calendar_config):
        return None, _LABEL_BEFORE_TERM
    return None, _LABEL_NOT_IN_TERM


def _rating_for_percentile(percentile):
    """Single source of the rating threshold/label business rule."""
    if percentile is None:
        return RATING_BAND_NONE, '暂无评级'
    if percentile <= 0.2:
        return RATING_BAND_ATTENTION, '需要继续努力'
    if percentile <= 0.7:
        return RATING_BAND_GOOD, '表现良好'
    return RATING_BAND_EXCELLENT, '行为很好'


def _resolve_deduction_display(total_deduction):
    numeric_deduction = float(total_deduction or 0.0)
    return '0.00' if abs(numeric_deduction) < 0.005 else f'-{abs(numeric_deduction):.2f}'


def _total_deduction_for_forms(user_form_ids):
    return db.session.query(func.sum(ScoreRecord.total_personal_score)).filter(
        ScoreRecord.form_id.in_(user_form_ids)
    ).scalar() or 0.0


def _aggregate_listener_scores():
    """Percentile pool source: the canonical ranking population.

    ACTIVE ∩ ELIGIBLE ROLE ∩ EVALUATED — departed users (``is_active=False``),
    non-eligible roles and listeners without a real ``User`` row never enter
    the pool; legacy ``is_active=NULL`` rows stay active via
    ``active_user_filter()``.
    """
    return db.session.query(
        LectureForm.listener_number,
        func.sum(ScoreRecord.total_personal_score).label('total'),
    ).join(
        ScoreRecord,
        LectureForm.id == ScoreRecord.form_id,
    ).join(
        User,
        User.number == LectureForm.listener_number,
    ).filter(
        User.role.in_(PROFILE_STATS_ELIGIBLE_ROLES),
        active_user_filter(),
    ).group_by(
        LectureForm.listener_number,
    ).all()


def _relative_deduction_percentile(all_scores, user_score):
    """Right-edge tie semantics for the descending deduction ranking.

    ``percentile = count(score >= user_score) / len(all_scores)``：同分用户取
    tie block 的右边界（最后一位）。扣分越高 → percentile 越小 → 评级越差；
    空池或无样本返回 None；结果为 (0, 1] 的 float，与 cohort size 无关地保持
    同分用户同值。纯计算 helper：无 DB、无 Flask、无 session。
    """
    if user_score is None or not all_scores:
        return None
    count_at_or_above = sum(1 for score in all_scores if score >= user_score)
    return count_at_or_above / len(all_scores)


def _build_assessment_items(listener_number, user_form_ids):
    assessment_items = []
    if not user_form_ids:
        return assessment_items

    item_rows = db.session.query(ScoreItem, ScoreRecord, LectureForm) \
        .join(ScoreRecord, ScoreItem.score_record_id == ScoreRecord.id) \
        .join(LectureForm, ScoreRecord.form_id == LectureForm.id) \
        .filter(
            LectureForm.listener_number == listener_number,
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
    return assessment_items


def build_user_profile_stats(user, *, now=None):
    """Build the canonical profile statistics snapshot for ``user``.

    ``now`` defaults to ``datetime.now()``; every derived time anchor (today,
    month start, days-since-last, current teaching week) comes from this single
    value so a snapshot is internally time-consistent.
    """
    if now is None:
        now = datetime.now()

    stats = None
    if not user or user.role not in PROFILE_STATS_ELIGIBLE_ROLES:
        return stats

    calendar_config, _calendar_error = load_teaching_calendar_config()

    required_submission_setting = SystemSetting.query.filter_by(key='teaching_required_submission').first()
    required_submission = int(required_submission_setting.value) if required_submission_setting and required_submission_setting.value else 1
    required_listening = required_submission

    check_dept_setting = SystemSetting.query.filter_by(key='teaching_check_dept_review').first()
    check_dept = check_dept_setting.value == 'true' if check_dept_setting else False

    check_center_setting = SystemSetting.query.filter_by(key='teaching_check_center_review').first()
    check_center = check_center_setting.value == 'true' if check_center_setting else False

    current_week, current_week_label = _resolve_current_week(calendar_config, now)

    def get_form_group_week_num(versions):
        if not versions or calendar_config is None:
            return None
        latest_version = versions[-1]
        return effective_form_week(
            latest_version.lecture_date,
            latest_version.audit_tag,
            calendar_config,
        )

    def count_feedback_chars(form):
        if not form:
            return 0
        text = f"{form.course_feedback or ''}{form.suggestions or ''}"
        return len(''.join(str(text).split()))

    all_forms = LectureForm.query.filter_by(listener_number=user.number).order_by(LectureForm.created_at.asc()).all()
    form_groups = {}
    for form in all_forms:
        uid = form.unique_id or form.id
        form_groups.setdefault(uid, []).append(form)

    total_submitted_count = len(form_groups)
    total_approved_count = 0
    total_reward_count = 0
    total_feedback_chars = 0
    week_submitted_count = 0
    week_approved_count = 0
    weekly_error_free_counts = {}

    for versions in form_groups.values():
        versions.sort(key=lambda item: item.id)
        submit_week = get_form_group_week_num(versions)

        if submit_week is not None and submit_week == current_week:
            week_submitted_count += 1

        approved_versions = [version for version in versions if version.status == '中心已审核']
        approved_form = approved_versions[-1] if approved_versions else None
        if approved_form:
            total_approved_count += 1
            total_feedback_chars += count_feedback_chars(approved_form)
            if submit_week is not None and submit_week == current_week:
                week_approved_count += 1

        has_dept_review = False
        dept_review_ok = True
        has_center_review = False
        center_review_ok = True

        for version in versions:
            if version.status == '部门已审核':
                has_dept_review = True
                score_record = ScoreRecord.query.filter_by(form_id=version.id).first()
                if score_record:
                    auto_items = ScoreItem.query.filter_by(score_record_id=score_record.id, is_auto_generated=True).count()
                    if auto_items > 0:
                        dept_review_ok = False

            if version.status == '中心已审核':
                has_center_review = True
                score_record = ScoreRecord.query.filter_by(form_id=version.id).first()
                if score_record:
                    auto_items = ScoreItem.query.filter_by(score_record_id=score_record.id, is_auto_generated=True).count()
                    if auto_items > 0:
                        center_review_ok = False

        criteria_met = True
        if check_dept and (not has_dept_review or not dept_review_ok):
            criteria_met = False
        if check_center and (not has_center_review or not center_review_ok):
            criteria_met = False
        if not check_dept and not check_center:
            criteria_met = False

        if criteria_met and submit_week is not None:
            weekly_error_free_counts[submit_week] = weekly_error_free_counts.get(submit_week, 0) + 1

    week_error_free_count = weekly_error_free_counts.get(current_week, 0)
    week_reward_count = max(0, week_error_free_count - required_listening)
    for count in weekly_error_free_counts.values():
        total_reward_count += max(0, count - required_listening)

    days_since_last = '无'
    last_form_obj = LectureForm.query.filter_by(listener_number=user.number).order_by(LectureForm.created_at.desc()).first()
    if last_form_obj and last_form_obj.created_at:
        days_since_last = (now - last_form_obj.created_at).days

    this_month_start = datetime(now.year, now.month, 1)
    this_month = 0
    for versions in form_groups.values():
        if versions and versions[0].created_at and versions[0].created_at >= this_month_start:
            this_month += 1

    last_submit = last_form_obj.created_at.strftime('%Y-%m-%d') if last_form_obj and last_form_obj.created_at else '无'
    average_feedback_chars = total_feedback_chars / total_approved_count if total_approved_count else 0

    user_form_ids = [form.id for form in all_forms]
    if user_form_ids:
        total_deduction = _total_deduction_for_forms(user_form_ids)
    else:
        total_deduction = 0.0

    assessment_items = _build_assessment_items(user.number, user_form_ids)

    # Ranking population: ACTIVE ∩ ELIGIBLE ROLE ∩ EVALUATED（见 _aggregate_listener_scores）。
    # Unscored users must never dilute the pool, and a real 0-score evaluation
    # is a valid sample — not "no evaluation".
    scores_map = {row[0]: float(row[1] or 0.0) for row in _aggregate_listener_scores()}
    all_scores = list(scores_map.values())
    all_scores.sort(reverse=True)

    has_evaluation_sample = user.number in scores_map
    percentile = _relative_deduction_percentile(all_scores, scores_map.get(user.number))

    rating_band, rating_label = _rating_for_percentile(percentile)

    stats = {
        'total_forms': total_submitted_count,
        'this_month': this_month,
        'last_submit': last_submit,
        'total_deduction': total_deduction,
        'deduction_display': _resolve_deduction_display(total_deduction),
        'has_evaluation_sample': has_evaluation_sample,
        'rating_label': rating_label,
        'rating_band': rating_band,
        'percentile': percentile,
        'current_week': current_week,
        'current_week_label': current_week_label,
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
    return stats
