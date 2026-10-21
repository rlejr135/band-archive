"""Manual, reviewed case actions; no app workers, mail, or storage clients.

Use DATABASE_URL in the process environment, and inspect the dry-run output
before repeating the identical command with --apply. Never paste credentials,
complaint text, or personally identifying case names into arguments.
"""
import json

import click

from content_restrictions import REASON_CODES, RESOURCE_MODELS, set_restriction
from errors import NotFoundError, ValidationError
from extensions import db
from migration_bootstrap import (MigrationBootstrapError, create_migration_app,
                                 migration_head_revision, schema_contract_differences)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


@click.command()
@click.option('--database-url', envvar='DATABASE_URL', required=True, help='Use the protected process environment.')
@click.option('--actor-user-id', required=True, type=click.IntRange(min=1))
@click.option('--band-id', required=True, type=click.IntRange(min=1))
@click.option('--resource-type', required=True, type=click.Choice(tuple(RESOURCE_MODELS)))
@click.option('--resource-id', required=True, type=click.IntRange(min=1))
@click.option('--case-ref', required=True, help='Opaque UUID from the private case register.')
@click.option('--action', required=True, type=click.Choice(('restrict', 'release')))
@click.option('--reason-code', type=click.Choice(REASON_CODES))
@click.option('--apply', is_flag=True, help='Persist this reviewed case action; default only inspects.')
def cli(database_url, actor_user_id, band_id, resource_type, resource_id, case_ref,
        action, reason_code, apply):
    """Restrict or release one case after separately reviewing its legal basis."""
    app = create_migration_app(database_url)
    with app.app_context():
        try:
            with db.engine.connect() as connection:
                versions = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
                if versions != [migration_head_revision()] or schema_contract_differences(connection):
                    raise MigrationBootstrapError('Restriction actions require the complete reviewed schema.')
            report = set_restriction(
                band_id=band_id, resource_type=resource_type, resource_id=resource_id,
                case_ref=case_ref, actor_user_id=actor_user_id, active=action == 'restrict',
                reason_code=reason_code, apply=apply,
            )
            if apply:
                db.session.commit()
            else:
                db.session.rollback()
        except (NotFoundError, ValidationError, MigrationBootstrapError) as error:
            db.session.rollback()
            raise click.ClickException(str(error)) from error
        except SQLAlchemyError:
            db.session.rollback()
            raise click.ClickException('Restriction action failed; no success was confirmed.') from None
        finally:
            db.session.remove()
            db.engine.dispose()
    click.echo(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    cli()
