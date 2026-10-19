"""Additive name migration preserves PostgreSQL data and Flask-only ACLs."""
import pytest
from sqlalchemy import text
from flask_migrate import upgrade
from alembic import command

from extensions import db
from migration_bootstrap import (MIGRATIONS_DIRECTORY, create_migration_app,
                                 schema_contract_differences, phase20_metadata, phase21_metadata)
from tests.test_data_api_security_migration import private_pg17  # noqa: F401


def test_revision20_to21_preserves_rows_acl_and_no_backfill(private_pg17):
    app = create_migration_app(private_pg17)
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                for role in ('anon', 'authenticated'):
                    connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            from tenant_backfill import apply
            with db.engine.begin() as connection:
                apply(connection)
            upgrade(revision='20261019_20', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                connection.execute(text("""INSERT INTO app_user
                  (id,supabase_user_id,email,status,email_verified,is_platform_admin,created_at,updated_at)
                  VALUES (99001,'name-migration-user','fixture@example.invalid','active',true,false,now(),now())"""))
                connection.execute(text("""INSERT INTO band
                  (id,name,slug,default_visibility,member_roster_public,storage_quota_bytes,is_legacy_import,created_at,updated_at)
                  VALUES (99001,'Preservation','name-preservation','PUBLIC',true,1024,false,now(),now())"""))
                connection.execute(text("""INSERT INTO band_membership
                  (id,band_id,user_id,role,status,requested_at,join_introduction)
                  VALUES (99001,99001,99001,'member','pending',now(),'Keep introduction')"""))
                before = connection.execute(text('SELECT * FROM band_membership WHERE id=99001')).mappings().one()
                security_before = connection.execute(text("""SELECT relacl::text,relrowsecurity,relforcerowsecurity
                  FROM pg_class WHERE oid='public.band_membership'::regclass""")).one()
                assert not schema_contract_differences(connection, metadata=phase20_metadata())
            upgrade(revision='20261020_21', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                after = dict(connection.execute(text('SELECT * FROM band_membership WHERE id=99001')).mappings().one())
                assert after.pop('requested_name') is None
                assert after == dict(before)
                assert connection.execute(text("""SELECT relacl::text,relrowsecurity,relforcerowsecurity
                  FROM pg_class WHERE oid='public.band_membership'::regclass""")).one() == security_before
                assert not schema_contract_differences(connection, metadata=phase21_metadata())
                for role in ('anon', 'authenticated'):
                    assert not connection.execute(text(
                        "SELECT has_column_privilege(:role,'public.band_membership','requested_name','SELECT')"),
                        {'role': role}).scalar_one()
                connection.execute(text("UPDATE band_membership SET requested_name='Keep name' WHERE id=99001"))
            config = app.extensions['migrate'].migrate.get_config(str(MIGRATIONS_DIRECTORY))
            with pytest.raises(RuntimeError, match='Refusing to discard'):
                command.downgrade(config, revision='20261019_20')
            with db.engine.connect() as connection:
                assert connection.execute(text('SELECT requested_name FROM band_membership WHERE id=99001')).scalar_one() == 'Keep name'
                assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261020_21'
    finally:
        with app.app_context():
            db.session.remove(); db.engine.dispose()
