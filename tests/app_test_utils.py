import os
from pathlib import Path


def configure_sqlite_database(app, db, db_path):
    """Rebind the global Flask app to a per-test SQLite database."""
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        db.session.remove()
        try:
            db.engine.dispose()
        except Exception:
            pass

        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.abspath(db_path)
        app.config.setdefault('SQLALCHEMY_BINDS', {})
        app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {})
        app.config.setdefault('SQLALCHEMY_ECHO', False)

        engines = db._app_engines.get(app)
        if engines is None:
            raise RuntimeError('Flask app is not registered with this SQLAlchemy instance.')
        engines.clear()

        engine_options = db._engine_options.copy()
        engine_options.update(app.config['SQLALCHEMY_ENGINE_OPTIONS'])
        engine_options['url'] = app.config['SQLALCHEMY_DATABASE_URI']
        echo = app.config['SQLALCHEMY_ECHO']
        engine_options.setdefault('echo', echo)
        engine_options.setdefault('echo_pool', echo)
        db._make_metadata(None)
        db._apply_driver_defaults(engine_options, app)
        engines[None] = db._make_engine(None, engine_options, app)


def cleanup_sqlite_database(db, drop_all=False):
    """Release SQLite connections before Windows temporary directories are removed."""
    db.session.remove()
    if drop_all:
        db.drop_all()
    try:
        db.engine.dispose()
    except Exception:
        pass
