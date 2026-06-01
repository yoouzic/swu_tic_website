import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]


def load_env_file(env_path=None):
    env_path = Path(env_path or BASE_DIR / '.env')
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:].strip()

        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file()


def env_value(name, default=None, required=False):
    value = os.environ.get(name)
    if value is None or value == '':
        if required:
            raise RuntimeError(f'Missing required environment variable: {name}')
        return default
    return value


def env_bool(name, default=False):
    value = env_value(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ('1', 'true', 'yes', 'on'):
        return True
    if normalized in ('0', 'false', 'no', 'off'):
        return False
    return default


def env_int(name, default, minimum=None):
    try:
        value = int(env_value(name, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(value, minimum)
    return value


def env_path(name, default):
    value = env_value(name, default)
    path = Path(value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path.resolve())


def is_production():
    return env_value('FLASK_ENV', 'development').strip().lower() == 'production'
