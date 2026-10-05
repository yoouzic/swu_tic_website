"""Database transaction boundaries for listener submissions."""
from sqlalchemy.exc import OperationalError

from app.models import User, db
from app.services.review_concurrency import ReviewConflict


def serialize_new_submission(user_id):
    """Hold the actor row's write lock before the duplicate lookup.

    All workers share this database lock.  It leaves the account's business
    fields and timestamp unchanged and releases with the form transaction.
    Looking up duplicates only after the lock prevents two simultaneous
    requests from both accepting the same previously absent signature.
    """
    statement = db.update(User).where(User.id == user_id).values(
        updated_at=User.updated_at,
    ).execution_options(synchronize_session=False)
    try:
        with db.session.no_autoflush:
            claimed = db.session.execute(statement).rowcount
    except OperationalError as error:
        if 'database is locked' in str(error).lower() or 'database table is locked' in str(error).lower():
            raise ReviewConflict('表单正在提交，请保留输入后重试') from error
        raise
    if claimed != 1:
        raise ReviewConflict('账号状态已更新，请刷新页面后重试')

