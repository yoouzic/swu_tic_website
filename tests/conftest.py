import pytest

from app.app import app


@pytest.fixture(autouse=True)
def _legacy_business_tests_disable_csrf():
    """Keep existing business tests unchanged; dedicated CSRF tests override explicitly."""
    app.config['WTF_CSRF_ENABLED'] = False
    yield
    app.config['WTF_CSRF_ENABLED'] = True
