"""Initial authenticated health endpoint for the automation runtime."""

from flask import Blueprint, current_app, jsonify

from app.blueprints.auth import login_required


review_automation_bp = Blueprint('review_automation', __name__)


@review_automation_bp.get('/admin/api/automation/health')
@login_required
def health():
    return jsonify(
        success=True,
        services={
            'redis': 'unchecked',
            'worker': 'unchecked',
            'deepseek': (
                'configured'
                if current_app.config.get('DEEPSEEK_API_KEY')
                else 'missing'
            ),
        },
    )
