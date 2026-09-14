"""Explicit migration operator entry point with no application startup work.

Examples:
    python migration_runner.py --database-url sqlite:///new.db upgrade
    python migration_runner.py --database-url sqlite:///current.db stamp-existing
"""

import click
from flask_migrate import upgrade

from migration_bootstrap import (
    MIGRATIONS_DIRECTORY,
    MigrationBootstrapError,
    create_migration_app,
    stamp_existing_current_schema,
)


@click.group()
@click.option('--database-url', envvar='DATABASE_URL', required=True,
              help='SQLAlchemy URL for the one database to operate on.')
@click.pass_context
def cli(context, database_url):
    """Run Alembic without `create_app()` or its legacy SQLite startup work."""
    context.obj = create_migration_app(database_url)


@cli.command('upgrade')
@click.pass_obj
def upgrade_command(app):
    """Upgrade a fresh or already Alembic-stamped database to head."""
    with app.app_context():
        upgrade(directory=str(MIGRATIONS_DIRECTORY))
    click.echo('Upgraded database to Alembic head.')


@cli.command('stamp-existing')
@click.pass_obj
def stamp_existing_command(app):
    """Validate and stamp an already-current pre-Alembic database."""
    with app.app_context():
        try:
            changed = stamp_existing_current_schema()
        except MigrationBootstrapError as error:
            raise click.ClickException(str(error)) from error
    if changed:
        click.echo('Stamped existing current schema.')
    else:
        click.echo('Existing schema is already stamped.')


if __name__ == '__main__':
    cli()
