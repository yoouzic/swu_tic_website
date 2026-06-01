from flask import Blueprint, render_template, session
from ..models import User, LectureForm
from ..utils.leave_management import get_pending_leave_makeup
from ..utils.user_status import is_user_active

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    """首页"""
    # 如果用户已登录，显示个性化首页
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if not user or not is_user_active(user):
            # 如果 session 中的用户 ID 不存在，清除 session 并显示公共首页
            session.clear()
            return render_template('main/public_index.html')
        
        # 根据角色显示不同的首页内容
        if user.role == '超级管理员':
            leave_prompt = get_pending_leave_makeup(user)
            return render_template('main/admin_index.html', user=user, leave_prompt=leave_prompt)
        elif user.role == '管理员':
            leave_prompt = get_pending_leave_makeup(user)
            return render_template('main/manager_index.html', user=user, leave_prompt=leave_prompt)
        else:
            leave_prompt = get_pending_leave_makeup(user)
            return render_template('main/user_index.html', user=user, leave_prompt=leave_prompt)
    else:
        # 未登录用户显示登录页面
        return render_template('main/public_index.html')

@main_bp.route('/about')
def about():
    """关于页面"""
    return render_template('main/about.html')

@main_bp.route('/help')
def help():
    """帮助页面"""
    return render_template('main/help.html')
