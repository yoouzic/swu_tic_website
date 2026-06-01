import json
from flask import request, current_app
from sqlalchemy.exc import SQLAlchemyError
from ..models import db, PasswordAuditLog


def get_request_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or ''


def record_password_audit(actor_user_id, target_user_id, action, details=None, result='success'):
    try:
        PasswordAuditLog.__table__.create(bind=db.engine, checkfirst=True)
        payload = json.dumps(details, ensure_ascii=False) if details else None
        log = PasswordAuditLog(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action=action,
            request_method=request.method,
            request_path=request.path,
            source_ip=get_request_ip(),
            user_agent=(request.headers.get('User-Agent', '') or '')[:255],
            result=result,
            details=payload
        )
        db.session.add(log)
        current_app.logger.info(
            f'密码审计 action={action} actor={actor_user_id} target={target_user_id} ip={log.source_ip} result={result}'
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f'密码审计写入失败: {str(e)}')
