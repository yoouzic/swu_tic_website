# -*- coding: utf-8 -*-
"""User blueprint domain module (Step 4 split).

``profile()`` is a thin controller: it resolves the HTTP/session target, asks
the canonical profile-stats service for a snapshot, and renders.  All statistics
business rules live in ``app/services/profile_stats.py``.
"""
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, session

from app.models import User, db
from app.security import login_required
from app.services.profile_stats import build_user_profile_stats
from app.utils.leave_management import get_pending_leave_makeup
from app.utils.profile_settings import PROFILE_EDITABLE_FIELD_KEYS, get_profile_editable_fields
import re

from . import user_bp


@user_bp.route('/profile')
@login_required
def profile():
    """用户资料页面"""
    user = User.query.get(session['user_id'])

    # 统计信息（对所有角色）；统计业务规则统一在 profile_stats service。
    stats = None
    if user.role in ['信息员', '管理员', '超级管理员']:
        stats = build_user_profile_stats(user, now=datetime.now())

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
