"""Revision 22 is additive, private, and refuses evidence-destroying rollback."""
from datetime import datetime, timezone
import uuid

from alembic import command
from click.testing import CliRunner
from flask_migrate import upgrade
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from content_restrictions import is_restricted
from content_restriction_cli import cli
from extensions import db
from migration_bootstrap import (MIGRATIONS_DIRECTORY, create_migration_app,
                                 phase21_metadata, phase22_metadata, schema_contract_differences)
from models import AppUser, Band, BandMembership, ContentRestriction, Song
from tenant_backfill import apply as backfill
from tests.test_data_api_security_migration import private_pg17  # noqa: F401


def _revision21(app):
    with app.app_context():
        upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
        with db.engine.begin() as connection:
            backfill(connection)
        upgrade(revision='20261020_21', directory=str(MIGRATIONS_DIRECTORY))


def _upgrade_and_verify(app, *, postgres=False):
    try:
        _revision21(app)
        with app.app_context():
            with db.engine.connect() as connection:
                assert not schema_contract_differences(connection, metadata=phase21_metadata())
                before = {name: connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()
                          for name in ('app_user', 'band', 'media', 'personal_log', 'gallery_image', 'audit_event')}
            upgrade(revision='20261021_22', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.connect() as connection:
                assert not schema_contract_differences(connection, metadata=phase22_metadata())
                assert connection.execute(text('SELECT count(*) FROM content_restriction')).scalar_one() == 0
                for name, count in before.items():
                    assert connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one() == count
                indexes = {row['name']: row for row in inspect(connection).get_indexes('audit_event')}
                assert indexes['ix_audit_event_upload_policy_target']['column_names'] == ['target_type', 'target_id']
                if postgres:
                    assert connection.execute(text("""SELECT relrowsecurity,relforcerowsecurity
                        FROM pg_class WHERE oid='public.content_restriction'::regclass""")).one() == (True, False)
                    assert connection.execute(text("""SELECT count(*) FROM pg_policy
                        WHERE polrelid='public.content_restriction'::regclass""")).scalar_one() == 0
                    for role in ('anon', 'authenticated'):
                        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN'):
                            assert not connection.execute(text(
                                "SELECT has_table_privilege(:role,'public.content_restriction',:privilege)"),
                                {'role': role, 'privilege': privilege}).scalar_one()
                        for privilege in ('USAGE', 'SELECT', 'UPDATE'):
                            assert not connection.execute(text(
                                "SELECT has_sequence_privilege(:role,'public.content_restriction_id_seq',:privilege)"),
                                {'role': role, 'privilege': privilege}).scalar_one()
            # A no-case rollback is reversible and preserves existing rows.
            config = app.extensions['migrate'].migrate.get_config(str(MIGRATIONS_DIRECTORY))
            command.downgrade(config, revision='20261020_21')
            with db.engine.connect() as connection:
                assert not schema_contract_differences(connection, metadata=phase21_metadata())
            upgrade(revision='20261021_22', directory=str(MIGRATIONS_DIRECTORY))
            now = datetime.now(timezone.utc)
            actor = AppUser(supabase_user_id='restriction-migration-fixture', email='fixture@example.invalid',
                            status='active', email_verified=True, is_platform_admin=True)
            writer = AppUser(supabase_user_id='restriction-writer-fixture', email='writer@example.invalid',
                             status='active', email_verified=True)
            band = Band(name='Preserved', slug='restriction-preserved', default_visibility='PUBLIC')
            db.session.add_all((actor, writer, band))
            db.session.flush()
            membership = BandMembership(band_id=band.id, user_id=writer.id, role='owner', status='active')
            db.session.add(membership)
            db.session.flush()
            song = Song(band_id=band.id, created_by_membership_id=membership.id, title='Preserved', artist='Fixture')
            db.session.add(song)
            db.session.flush()
            item = ContentRestriction(band_id=band.id, resource_type='song', resource_id=song.id,
                                      case_ref=str(uuid.uuid4()), reason_code='privacy', state='active',
                                      created_by_user_id=actor.id, created_at=now)
            db.session.add(item)
            db.session.commit()
            if not postgres:
                arguments = ['--database-url', app.config['SQLALCHEMY_DATABASE_URI'],
                             '--actor-user-id', str(actor.id), '--band-id', str(band.id),
                             '--resource-type', 'song', '--resource-id', str(song.id),
                             '--case-ref', item.case_ref, '--action', 'release']
                preview = CliRunner().invoke(cli, arguments)
                assert preview.exit_code == 0, preview.output
                assert '"applied": false' in preview.output
                assert app.config['SQLALCHEMY_DATABASE_URI'] not in preview.output
                db.session.expire_all()
                assert is_restricted(db.session.get(Song, song.id))
                released = CliRunner().invoke(cli, [*arguments, '--apply'])
                assert released.exit_code == 0, released.output
                db.session.expire_all()
                assert not is_restricted(db.session.get(Song, song.id))
            # FK anonymization must not release a case. This actor has no other
            # account dependencies; the separate audit-history policy remains intact.
            if postgres:
                db.session.execute(text('DELETE FROM app_user WHERE id=:id'), {'id': actor.id})
                db.session.commit()
                db.session.expire_all()
                assert db.session.get(ContentRestriction, item.id).created_by_user_id is None
                assert is_restricted(db.session.get(Song, song.id))
            # The ORM reads above start an implicit transaction. Release its
            # connection before Alembic requests ACCESS EXCLUSIVE on this table.
            db.session.remove()
            with pytest.raises(RuntimeError, match='Refusing to discard'):
                command.downgrade(config, revision='20261020_21')
            with db.engine.connect() as connection:
                assert connection.execute(text('SELECT count(*) FROM content_restriction')).scalar_one() == 1
                assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261021_22'
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_sqlite_revision21_to22_preserves_and_protects_cases(tmp_path):
    app = create_migration_app('sqlite:///' + (tmp_path / 'restriction.db').as_posix())
    _upgrade_and_verify(app)


def test_postgres_revision21_to22_is_private_preserved_and_anonymizable(private_pg17):
    # Every Alembic connection in this test gets a bounded local lock wait.
    # A future leaked reader fails promptly instead of hanging the suite.
    local_url = make_url(private_pg17).update_query_dict({'options': '-c lock_timeout=5000'})
    app = create_migration_app(local_url.render_as_string(hide_password=False))
    with app.app_context():
        with db.engine.begin() as connection:
            for role in ('anon', 'authenticated'):
                connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
    _upgrade_and_verify(app, postgres=True)
