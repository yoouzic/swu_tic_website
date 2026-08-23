import pytest

from app.app import app, create_app


@pytest.fixture(autouse=True)
def _legacy_business_tests_disable_csrf():
    """Keep existing business tests unchanged; dedicated CSRF tests override explicitly."""
    app.config['WTF_CSRF_ENABLED'] = False
    yield
    app.config['WTF_CSRF_ENABLED'] = True


@pytest.fixture
def csrf_strict_client(tmp_path):
    """A secondary app with CSRF enabled for route-level CSRF contract tests."""
    sqlite_path = tmp_path / 'csrf.sqlite'
    upload_dir = tmp_path / 'uploads'
    automation_upload_dir = tmp_path / 'automation'
    a2 = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': True,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{sqlite_path}",
        'UPLOAD_FOLDER': str(upload_dir),
        'AUTOMATION_UPLOAD_DIR': str(automation_upload_dir),
    })
    return a2.test_client()
