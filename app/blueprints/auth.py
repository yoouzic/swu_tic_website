from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import check_password_hash
from ..models import User, db
from ..utils.password_audit import record_password_audit
from ..utils.user_status import is_user_active
from app.security import login_required, role_required

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """用户登录"""
    if request.method == 'POST':
        student_id = request.form['student_id']
        password = request.form['password']
        
        user = User.query.filter_by(student_id=student_id).first()
        
        if user and not is_user_active(user):
            flash('账号已离任或不可用，请联系管理员', 'error')
        elif user and check_password_hash(user.password_hash, password):
            session.clear()
            session['user_id'] = user.id
            session['user_role'] = user.role
            session['user_name'] = user.name
            
            return redirect(url_for('main.index'))
        else:
            flash('学号或密码错误', 'error')
    
    return render_template('auth/login.html')

@auth_bp.route('/logout', methods=['POST'])
def logout():
    """用户登出（仅 POST，受 CSRF 保护）"""
    session.clear()
    flash('已成功登出', 'info')
    return redirect(url_for('main.index'))

@auth_bp.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    """修改密码"""
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        
        user = User.query.get(session['user_id'])
        
        if not check_password_hash(user.password_hash, current_password):
            flash('当前密码错误', 'error')
        elif new_password != confirm_password:
            flash('新密码确认不匹配', 'error')
        elif len(new_password) < 6:
            flash('新密码长度至少6位', 'error')
        else:
            from werkzeug.security import generate_password_hash
            user.password_hash = generate_password_hash(new_password)
            record_password_audit(
                actor_user_id=user.id,
                target_user_id=user.id,
                action='self_change_password',
                details={'source': 'auth.change_password'}
            )
            db.session.commit()
            flash('密码修改成功', 'success')
            return redirect(url_for('user.profile'))
    
    return render_template('auth/change_password.html')
