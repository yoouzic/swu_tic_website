"""Idempotent schema and initial rule-revision CLI commands."""

import json

import click

from app.models import db

from .models import ReviewRuleRevision


SEED_RULE_REVISIONS = (
    {
        'rule_key': 'feedback_required_prefix',
        'handler': 'required_prefix',
        'severity': 'review',
        'parameters': {'required_prefix': '该老师'},
    },
    {
        'rule_key': 'feedback_min_length',
        'handler': 'minimum_length',
        'severity': 'review',
        'parameters': {'minimum_characters': 50},
    },
    {
        'rule_key': 'common_word_confusion',
        'handler': 'confusion_patterns',
        'severity': 'review',
        'parameters': {
            'patterns': [
                {'regex': r'的[^地得]{0,4}地', 'message': '请人工复核“的/地”用词'},
                {'regex': r'地[^的得]{0,4}得', 'message': '请人工复核“地/得”用词'},
                {'regex': r'得[^的地]{0,4}的', 'message': '请人工复核“得/的”用词'},
            ],
        },
    },
    {
        'rule_key': 'personal_schedule_conflict',
        'handler': 'personal_schedule_conflict',
        'severity': 'high',
        'parameters': {},
    },
    {
        'rule_key': 'class_schedule_conflict',
        'handler': 'class_schedule_conflict',
        'severity': 'review',
        'parameters': {},
    },
    {
        'rule_key': 'same_college_teacher',
        'handler': 'same_college_teacher',
        'severity': 'high',
        'parameters': {},
    },
    {
        'rule_key': 'witness_reused_across_weeks',
        'handler': 'witness_reused_across_weeks',
        'severity': 'review',
        'parameters': {
            'minimum_distinct_weeks': 2,
            'high_risk_candidate_weeks': 3,
            'identity': 'phone_primary',
        },
    },
    {
        'rule_key': 'witness_phone_name_conflict',
        'handler': 'witness_phone_name_conflict',
        'severity': 'review',
        'parameters': {},
    },
    {
        'rule_key': 'consecutive_teacher_weeks',
        'handler': 'consecutive_teacher_weeks',
        'severity': 'review',
        'parameters': {'maximum_week_gap': 1},
    },
    {
        'rule_key': 'same_listener_same_slot',
        'handler': 'same_listener_same_slot',
        'severity': 'high',
        'parameters': {},
    },
    {
        'rule_key': 'school_schedule_mismatch',
        'handler': 'school_schedule_mismatch',
        'severity': 'review',
        'parameters': {},
    },
    {
        'rule_key': 'feedback_similarity',
        'handler': 'feedback_similarity',
        'severity': 'review',
        'parameters': {},
    },
)


@click.group(name='auto-review')
def auto_review_cli():
    """Manage additive automated-review schema and configuration."""


@auto_review_cli.command('init-schema')
def init_schema():
    """Create automation tables and add missing version-one rule revisions."""
    db.create_all()
    created = 0

    for seed in SEED_RULE_REVISIONS:
        existing = ReviewRuleRevision.query.filter_by(
            rule_key=seed['rule_key'],
            version=1,
        ).first()
        if existing is not None:
            continue

        db.session.add(
            ReviewRuleRevision(
                rule_key=seed['rule_key'],
                version=1,
                handler=seed['handler'],
                enabled=True,
                severity=seed['severity'],
                parameters_json=json.dumps(
                    seed['parameters'], ensure_ascii=False, sort_keys=True
                ),
            )
        )
        created += 1

    db.session.commit()
    click.echo(f'Automated review schema ready; seeded {created} rule revisions.')


def register_cli(flask_app):
    """Register the automation CLI group once on a Flask application."""
    if 'auto-review' not in flask_app.cli.commands:
        flask_app.cli.add_command(auto_review_cli)


__all__ = ['SEED_RULE_REVISIONS', 'auto_review_cli', 'register_cli']
