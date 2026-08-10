# -*- coding: utf-8 -*-
"""Run the Flask app in explicitly isolated local-debug mode."""

import logging
import os
import sys
from pathlib import Path


if os.environ.get('LOCAL_DEBUG_MODE') != '1':
    print('LOCAL_DEBUG_MODE=1 is required', file=sys.stderr)
    raise SystemExit(2)
if not os.environ.get('SQLITE_DB_PATH'):
    print('SQLITE_DB_PATH is required', file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log_path = Path(os.environ['LOCAL_DEBUG_LOG_PATH']).resolve()
log_path.parent.mkdir(parents=True, exist_ok=True)
handler = logging.FileHandler(log_path, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))

from app.app import app, init_database

app.logger.addHandler(handler)
logging.getLogger('werkzeug').addHandler(handler)
init_database()

if __name__ == '__main__':
    app.run(
        host='127.0.0.1',
        port=int(os.environ.get('FLASK_RUN_PORT', '5000')),
        debug=True,
        use_reloader=False,
    )
