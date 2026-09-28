"""Flask CLI registration for the additive listening-assistant schema."""

import click

from app.services.listening_assistant_schedule import ensure_listening_assistant_schema


@click.group(name='listening-assistant')
def listening_assistant_cli():
    """Manage the additive listening-assistant schedule index."""


@listening_assistant_cli.command('init-schema')
def init_schema():
    """Create only the assistant index table, if it is missing."""
    try:
        ensure_listening_assistant_schema()
    except RuntimeError as error:
        raise click.ClickException(str(error)) from error
    click.echo('Listening assistant schema ready.')


def register_cli(flask_app):
    """Register the command group once on a Flask application."""
    if 'listening-assistant' not in flask_app.cli.commands:
        flask_app.cli.add_command(listening_assistant_cli)


__all__ = ['init_schema', 'listening_assistant_cli', 'register_cli']
