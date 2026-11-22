"""Flask-aware Celery application factory for automated review tasks."""

import logging
import re

from celery import Celery, Task

from app.utils.operation_log import log_failure_event


_REVIEW_TASK_NAMES = {
    'review_automation.assess_form',
    'review_automation.run_batch',
    'review_automation.finalize_review_batch',
}
_BATCH_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


class ReviewTaskFailure(RuntimeError):
    """An unexpected worker failure with a safe message for Celery."""


class _ReviewTraceFilter(logging.Filter):
    def filter(self, record):
        event = getattr(record, 'data', None)
        if not isinstance(event, dict) or event.get('name') not in _REVIEW_TASK_NAMES:
            return True
        # The operation log owns failures, and job results already live in
        # the database and result backend.
        return 'return_value' not in event and not str(event.get('exc', '')).startswith('ReviewTaskFailure(')


logging.getLogger('celery.app.trace').addFilter(_ReviewTraceFilter())


def _task_record(name, args, kwargs):
    if name == 'review_automation.assess_form':
        form_id = args[1] if len(args) > 1 else kwargs.get('form_id')
        if type(form_id) is int and form_id > 0:
            return f'form:{form_id}'
    offset = 1 if name == 'review_automation.finalize_review_batch' else 0
    batch_id = args[offset] if len(args) > offset else kwargs.get('batch_id')
    return f'review_batch:{batch_id}' if isinstance(batch_id, str) and _BATCH_ID.fullmatch(batch_id) else '-'


def create_celery(flask_app):
    """Create a JSON-only Celery app configured from the Flask application."""

    class FlaskContextTask(Task):
        abstract = True
        throws = (ReviewTaskFailure,)

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                try:
                    return self.run(*args, **kwargs)
                except Exception:
                    if self.name not in _REVIEW_TASK_NAMES:
                        raise
                    log_failure_event(
                        'review', 'review_task_failed', _task_record(self.name, args, kwargs),
                        server_error=True,
                    )
                    raise ReviewTaskFailure('review_task_failed') from None

    celery = Celery(flask_app.import_name, task_cls=FlaskContextTask)
    celery.conf.update(
        broker_url=flask_app.config['CELERY_BROKER_URL'],
        result_backend=flask_app.config['CELERY_RESULT_BACKEND'],
        task_always_eager=flask_app.config['CELERY_TASK_ALWAYS_EAGER'],
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_serializer='json',
        result_serializer='json',
        accept_content=['json'],
    )
    return celery
