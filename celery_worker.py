"""Celery worker entry point; importing it never starts Flask's dev server."""

from app.app import app
from app.review_automation import get_celery_app


celery_app = get_celery_app(app)

# Import task definitions so their existing decorators register them on the app.
import app.review_automation.tasks  # noqa: E402,F401
