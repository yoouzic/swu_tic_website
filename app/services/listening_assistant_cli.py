"""Flask CLI registration for the additive listening-assistant schema."""

import click
from sqlalchemy import inspect

from app.models import ListeningAssistantScheduleEntry, db


@click.group(name='listening-assistant')
def listening_assistant_cli():
    """Manage the additive listening-assistant schedule index."""


@listening_assistant_cli.command('init-schema')
def init_schema():
    """Create only the assistant index table, if it is missing."""
    if not inspect(db.engine).has_table('schedule_import_batches'):
        raise click.ClickException(
            "Cannot initialize the listening-assistant schema: prerequisite "
            "canonical table 'schedule_import_batches' is missing. Initialize "
            "the canonical schedule snapshot schema first; this command does "
            "not create historical data."
        )

    ListeningAssistantScheduleEntry.__table__.create(
        bind=db.session.connection(),
        checkfirst=True,
    )
    click.echo('Listening assistant schema ready.')


def register_cli(flask_app):
    """Register the command group once on a Flask application."""
    if 'listening-assistant' not in flask_app.cli.commands:
        flask_app.cli.add_command(listening_assistant_cli)


__all__ = ['init_schema', 'listening_assistant_cli', 'register_cli']
