# -*- coding: utf-8 -*-
"""Admin blueprint package.

Phase 1 mechanical split: this package preserves the old ``app.blueprints.admin``
module surface. The Blueprint is created here once; submodules register routes on it.
"""
from flask import Blueprint

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Import submodules after the blueprint exists so their route decorators register.
from . import shared  # noqa: F401,E402
from . import system  # noqa: F401,E402
from . import contacts  # noqa: F401,E402
from . import review  # noqa: F401,E402
from . import org  # noqa: F401,E402
from . import users  # noqa: F401,E402
from . import courses  # noqa: F401,E402
from . import forms_io  # noqa: F401,E402
from . import schedule  # noqa: F401,E402
from . import leave  # noqa: F401,E402
from . import statistics  # noqa: F401,E402
from . import assessment_stats  # noqa: F401,E402

# Compatibility re-exports for existing imports and tests.
from .shared import (  # noqa: F401
    _active_user_query,
    _build_review_form_filter_datetime,
    _excel_cell_to_text,
    _get_form_latest_timestamp,
    _latest_form_groups_for_users,
    _normalize_review_form_time_filter,
    _parse_lecture_date_value,
    _resolve_assessment_users,
    _serialize_user_basic,
    _snapshot_user_for_movement,
    _to_int_or_none,
    allowed_file,
    generate_random_password,
    get_reviewer_display_mode,
)
from .contacts import (  # noqa: F401
    _resolve_group_id_for_import,
    _safe_export_filename,
)
from .schedule import (  # noqa: F401
    _course_model_key,
    _course_row_key,
)
from .users import (  # noqa: F401
    _build_user_profile_stats,
)
