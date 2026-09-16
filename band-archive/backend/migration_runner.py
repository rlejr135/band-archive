"""Explicit migration operator entry point with no application startup work.

Examples:
    python migration_runner.py --database-url sqlite:///new.db upgrade
    python migration_runner.py --database-url sqlite:///current.db stamp-existing
"""

import json

import click
from flask_migrate import upgrade

from extensions import db
from migration_bootstrap import (
    MIGRATIONS_DIRECTORY,
    MigrationBootstrapError,
    assert_upgrade_preflight,
    create_migration_app,
    stamp_existing_current_schema,
)
from tenant_backfill import TenantBackfillError, apply as apply_tenant_backfill, plan as plan_tenant_backfill


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
        try:
            assert_upgrade_preflight()
            upgrade(directory=str(MIGRATIONS_DIRECTORY))
        except MigrationBootstrapError as error:
            raise click.ClickException(str(error)) from error
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


@cli.command('tenant-backfill')
@click.option('--dry-run', is_flag=True, help='Print the deterministic plan without changing database bytes.')
@click.option('--apply', 'apply_changes', is_flag=True,
              help='Apply the validated backfill. Never contacts object storage.')
@click.pass_obj
def tenant_backfill_command(app, dry_run, apply_changes):
    """Backfill Phase 4 tenant scopes after `upgrade`, explicitly and offline."""
    if dry_run == apply_changes:
        raise click.UsageError('Specify exactly one of --dry-run or --apply.')
    with app.app_context():
        try:
            if dry_run:
                with db.engine.connect() as connection:
                    report = plan_tenant_backfill(connection)
            else:
                with db.engine.begin() as connection:
                    report = apply_tenant_backfill(connection)
        except TenantBackfillError as error:
            raise click.ClickException(str(error)) from error
    click.echo(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    cli()
