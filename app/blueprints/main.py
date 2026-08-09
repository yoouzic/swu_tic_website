from flask import Blueprint, render_template, session
from ..models import User, db
from ..ui.workspace import build_workspace, load_workspace_snapshot
from ..utils.leave_management import get_pending_leave_makeup
from ..utils.user_status import is_user_active

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    """首页"""
    user_id = session.get('user_id')
    if not user_id:
        return render_template('main/public_index.html')

    user = db.session.get(User, user_id)
    if not user or not is_user_active(user):
        session.clear()
        return render_template('main/public_index.html')

    snapshot = load_workspace_snapshot(user)
    workspace = build_workspace(user, snapshot)
    leave_prompt = get_pending_leave_makeup(user)
    return render_template('main/workspace.html', user=user, workspace=workspace, leave_prompt=leave_prompt)

@main_bp.route('/about')
def about():
    """关于页面"""
    return render_template('main/about.html')

@main_bp.route('/help')
def help():
    """帮助页面"""
    return render_template('main/help.html')
