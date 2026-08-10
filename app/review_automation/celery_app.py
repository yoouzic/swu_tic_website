"""Flask-aware Celery application factory for automated review tasks."""

from celery import Celery, Task


def create_celery(flask_app):
    """Create a JSON-only Celery app configured from the Flask application."""

    class FlaskContextTask(Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

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
