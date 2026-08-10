"""Bootstrap the automated review runtime without changing human review state."""

import importlib
from pathlib import Path

from .celery_app import create_celery


_CELERY_EXTENSION_KEY = 'review_automation_celery'
_MODELS_MODULE = 'app.review_automation.models'
_CLI_MODULE = 'app.review_automation.cli'


def _import_models_if_available():
    """Load automation models before any database initialization can run."""
    return importlib.import_module(_MODELS_MODULE)


def _register_cli_if_available(flask_app):
    cli_module = importlib.import_module(_CLI_MODULE)
    register_cli = getattr(cli_module, 'register_cli', None)
    if register_cli is not None:
        register_cli(flask_app)


def init_review_automation(flask_app):
    """Register routes, optional models/CLI, storage, and the Flask Celery app."""
    existing = flask_app.extensions.get(_CELERY_EXTENSION_KEY)
    if existing is not None:
        return existing

    _import_models_if_available()

    from .routes import review_automation_bp

    if review_automation_bp.name not in flask_app.blueprints:
        flask_app.register_blueprint(review_automation_bp)

    Path(flask_app.config['AUTOMATION_UPLOAD_DIR']).mkdir(parents=True, exist_ok=True)
    _register_cli_if_available(flask_app)

    celery = create_celery(flask_app)
    flask_app.extensions[_CELERY_EXTENSION_KEY] = celery
    return celery


def get_celery_app(flask_app):
    """Return the registered Celery app, initializing the package if needed."""
    return flask_app.extensions.get(_CELERY_EXTENSION_KEY) or init_review_automation(flask_app)


__all__ = ['get_celery_app', 'init_review_automation']
