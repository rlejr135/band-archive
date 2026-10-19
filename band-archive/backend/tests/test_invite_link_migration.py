"""Owned PostgreSQL and immutable-snapshot checks for migration 20."""
import pytest
import sqlalchemy as sa

pytest_plugins = ('tests.test_data_api_security_migration',)


def test_revision19_to20_preserves_rows_and_restricts_new_data_api_tables(private_pg17):
    from alembic import command
    from flask_migrate import upgrade
    from extensions import db
    from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app

    app = create_migration_app(private_pg17)
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                for role in ('anon', 'authenticated'):
                    connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            from tenant_backfill import apply as apply_tenant_backfill
            with db.engine.begin() as connection:
                apply_tenant_backfill(connection)
            upgrade(revision='20261018_19', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                connection.execute(sa.text("""INSERT INTO app_user
                    (id,supabase_user_id,email,status,email_verified,is_platform_admin,created_at,updated_at)
                    VALUES (99001,'99001-user','invite-migration@example.test','active',true,false,
                            now(),now())"""))
                connection.execute(sa.text("""INSERT INTO band
                    (id,name,slug,default_visibility,member_roster_public,storage_quota_bytes,
                     is_legacy_import,created_at,updated_at)
                    VALUES (99001,'Preservation fixture','invite-migration-fixture','PUBLIC',true,1024,
                            false,now(),now())"""))
                before = connection.execute(sa.text(
                    'SELECT id,name,slug FROM band WHERE id=99001')).one()
                connection.exec_driver_sql('CREATE ROLE invite_default_parent NOLOGIN')
                connection.exec_driver_sql('GRANT invite_default_parent TO anon')
                connection.exec_driver_sql(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public '
                    'GRANT SELECT ON TABLES TO invite_default_parent')
            with pytest.raises(RuntimeError, match='Unsafe default table/sequence privileges'):
                config = app.extensions['migrate'].migrate.get_config(str(MIGRATIONS_DIRECTORY))
                command.upgrade(config, revision='20261019_20')
            with db.engine.connect() as connection:
                assert connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() == '20261018_19'
                assert 'band_invite_link' not in sa.inspect(connection).get_table_names()
                assert connection.execute(sa.text(
                    'SELECT id,name,slug FROM band WHERE id=99001')).one() == before
            with db.engine.begin() as connection:
                connection.exec_driver_sql(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public '
                    'REVOKE SELECT ON TABLES FROM invite_default_parent')
                connection.exec_driver_sql('REVOKE invite_default_parent FROM anon')
                connection.exec_driver_sql('DROP ROLE invite_default_parent')
            upgrade(revision='20261019_20', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.connect() as connection:
                after = connection.execute(sa.text(
                    'SELECT id,name,slug FROM band WHERE id=99001')).one()
                assert tuple(after) == tuple(before)
                assert connection.execute(sa.text("""
                    SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname IN ('band_invite_link','invite_resume_context')
                      AND c.relrowsecurity AND NOT c.relforcerowsecurity
                """)).scalar_one() == 2
                assert connection.execute(sa.text("""
                    SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                    WHERE c.relname IN ('band_invite_link','invite_resume_context')
                """)).scalar_one() == 0
                assert connection.execute(sa.text("""
                    SELECT count(*) FROM pg_constraint c
                    JOIN pg_class t ON t.oid=c.conrelid
                    WHERE t.relname IN ('band_invite_link','invite_resume_context')
                      AND c.contype IN ('f','c','u','p')
                """)).scalar_one() >= 7
                # The migration added empty capability tables and preserved
                # the preexisting rev19 identity row exactly.
                assert connection.execute(sa.text('SELECT count(*) FROM band_invite_link')).scalar_one() == 0
                assert connection.execute(sa.text('SELECT count(*) FROM invite_resume_context')).scalar_one() == 0
                for table in ('band_invite_link', 'invite_resume_context'):
                    for role in ('anon', 'authenticated'):
                        for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER','MAINTAIN'):
                            assert not connection.execute(sa.text(
                                'SELECT has_table_privilege(:role,:table,:privilege)'), {
                                    'role': role, 'table': f'public.{table}', 'privilege': privilege,
                                }).scalar_one()
            with db.engine.begin() as connection:
                connection.execute(sa.text("""INSERT INTO band_invite_link
                    (band_id,token_hash,created_by_user_id,created_at,expires_at)
                    VALUES (99001,:digest,99001,now(),now()+interval '24 hours')"""),
                                   {'digest': 'a' * 64})
            config = app.extensions['migrate'].migrate.get_config(str(MIGRATIONS_DIRECTORY))
            with pytest.raises(RuntimeError, match='Refusing to discard'):
                command.downgrade(config, revision='20261018_19')
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
