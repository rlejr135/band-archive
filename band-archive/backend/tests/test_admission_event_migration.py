"""Revision 23 preserves old archive data, exact history, and private schemas."""
import importlib

from alembic import command
from alembic.script import ScriptDirectory
from flask_migrate import upgrade
import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from extensions import db
from migration_bootstrap import (
    MIGRATIONS_DIRECTORY, PHASE23_REVISION, assert_upgrade_preflight,
    create_migration_app, metadata_for_recorded_revision, phase22_metadata,
    phase23_metadata, schema_contract_differences, schema_differences,
)
from startup_guard import assert_postgres_startup_ready
from tenant_backfill import apply as backfill
from tests.test_data_api_security_migration import private_pg17  # noqa: F401


def _dispose(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _config(app):
    return app.extensions['migrate'].migrate.get_config(str(MIGRATIONS_DIRECTORY))


def _revision22(app):
    upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
    with db.engine.begin() as connection:
        backfill(connection)
    upgrade(revision='20261021_22', directory=str(MIGRATIONS_DIRECTORY))


def _seed(connection):
    connection.execute(text("""INSERT INTO app_user
        (id,supabase_user_id,email,status,email_verified,is_platform_admin,created_at,updated_at)
        VALUES (99001,'admission-event-fixture','fixture@example.invalid','active',true,false,
                CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
    connection.execute(text("""INSERT INTO band
        (id,name,slug,default_visibility,member_roster_public,storage_quota_bytes,is_legacy_import,created_at,updated_at)
        VALUES (99001,'Preserved band','admission-event-preserved','MEMBERS',false,1024,false,
                CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
    connection.execute(text("""INSERT INTO band_membership
        (id,band_id,user_id,role,status,requested_at,join_introduction,requested_name)
        VALUES (99001,99001,99001,'member','pending',CURRENT_TIMESTAMP,'Keep introduction','Keep name')"""))
    connection.execute(text("""INSERT INTO rehearsal
        (id,band_id,visibility,title,date,start_date,end_date,time,location,latitude,longitude,memo,color)
        VALUES (99001,99001,'MEMBERS','Uninferred historic event','2026-01-01','2026-01-01',
                '2026-01-03','20:30','Preserved location',37.5,127.0,'Keep memo','#123456')"""))
    connection.execute(text("""INSERT INTO song
        (id,band_id,visibility,title,artist,upvote_count,downvote_count,vote_score)
        VALUES (99001,99001,'MEMBERS','Preserved song','Fixture',0,0,0)"""))
    connection.execute(text("""INSERT INTO media
        (id,band_id,song_id,rehearsal_id,visibility,filename,object_key,file_type,transcoding_status,
         processing_attempts,upvote_count,downvote_count,vote_score)
        VALUES (99001,99001,99001,99001,'MEMBERS','preserved.mp4','media/preserved.mp4','video',
                'not_required',0,0,0,0)"""))


def _rows(connection):
    return {name: [dict(row) for row in connection.execute(text(f'SELECT * FROM "{name}" ORDER BY id')).mappings()]
            for name in ('app_user', 'band', 'band_membership', 'rehearsal', 'song', 'media')}


def _security(connection):
    return connection.execute(text("""SELECT c.relname,c.relacl::text,c.relrowsecurity,c.relforcerowsecurity,
        (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','S') ORDER BY c.relname""")).all()


def _exercise(app, *, postgres=False):
    try:
        with app.app_context():
            _revision22(app)
            with db.engine.begin() as connection:
                assert not schema_contract_differences(connection, metadata=phase22_metadata())
                _seed(connection)
                before = _rows(connection)
                security = _security(connection) if postgres else None
            assert assert_upgrade_preflight() == 'stamped'
            if postgres:
                # A structurally valid historical database remains ineligible
                # for application startup until an explicit operator upgrade.
                with pytest.raises(RuntimeError, match='PostgreSQL startup validation failed'):
                    assert_postgres_startup_ready(db.engine)
            upgrade(revision=PHASE23_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.connect() as connection:
                after = _rows(connection)
                for row in after['band_membership']:
                    assert row.pop('requested_positions_json') is None
                    assert row.pop('requested_custom_position') is None
                for row in after['rehearsal']:
                    assert row.pop('rehearsal_type') == 'UNCLASSIFIED'
                assert after == before
                assert not schema_contract_differences(connection, metadata=phase23_metadata())
                assert not schema_differences(connection, metadata=phase23_metadata())
                assert not schema_contract_differences(connection)
                assert {i['name'] for i in inspect(connection).get_indexes('rehearsal')} == {
                    i.name for i in phase22_metadata().tables['rehearsal'].indexes}
                if postgres:
                    assert _security(connection) == security
                    for role in ('anon', 'authenticated'):
                        for table, column in (('band_membership', 'requested_positions_json'),
                                              ('band_membership', 'requested_custom_position'),
                                              ('rehearsal', 'rehearsal_type')):
                            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'REFERENCES'):
                                assert not connection.execute(text(
                                    'SELECT has_column_privilege(:role,:table,:column,:privilege)'),
                                    dict(role=role, table='public.' + table, column=column,
                                         privilege=privilege)).scalar_one()
            # The database default also protects SQL/legacy clients that omit type.
            with db.engine.begin() as connection:
                connection.execute(text("INSERT INTO rehearsal (id,band_id,visibility,title,date) "
                                        "VALUES (99002,99001,'PUBLIC','Omitted type','2026-02-01')"))
                assert connection.execute(text('SELECT rehearsal_type FROM rehearsal WHERE id=99002')).scalar_one() == 'UNCLASSIFIED'
                for value in ('REGULAR', 'EXTRA', 'LIVE', 'OTHER', 'UNCLASSIFIED'):
                    connection.execute(text('UPDATE rehearsal SET rehearsal_type=:value WHERE id=99002'), {'value': value})
            for invalid in ('regular', '', 'REHEARSAL', None):
                with pytest.raises(IntegrityError), db.engine.begin() as connection:
                    connection.execute(text('UPDATE rehearsal SET rehearsal_type=:value WHERE id=99002'), {'value': invalid})
            # No recorded request or classification may be discarded, including
            # empty request JSON/text; each guard is checked independently.
            for column, value in (('requested_positions_json', '["VOCAL"]'),
                                  ('requested_positions_json', '[]'), ('requested_custom_position', 'Conductor'),
                                  ('requested_custom_position', '')):
                with db.engine.begin() as connection:
                    connection.execute(text(f'UPDATE band_membership SET {column}=:value WHERE id=99001'), {'value': value})
                with pytest.raises(RuntimeError, match='Refusing to discard pending application positions'):
                    command.downgrade(_config(app), '20261021_22')
                with db.engine.begin() as connection:
                    assert connection.execute(text(f'SELECT {column} FROM band_membership WHERE id=99001')).scalar_one() == value
                    assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == PHASE23_REVISION
                    connection.execute(text(f'UPDATE band_membership SET {column}=NULL WHERE id=99001'))
            with db.engine.begin() as connection:
                connection.execute(text("UPDATE rehearsal SET rehearsal_type='LIVE' WHERE id=99002"))
            with pytest.raises(RuntimeError, match='Refusing to discard classified event types'):
                command.downgrade(_config(app), '20261021_22')
            with db.engine.begin() as connection:
                assert connection.execute(text('SELECT rehearsal_type FROM rehearsal WHERE id=99002')).scalar_one() == 'LIVE'
                connection.execute(text('DELETE FROM rehearsal WHERE id=99002'))
            command.downgrade(_config(app), '20261021_22')
            with db.engine.connect() as connection:
                assert _rows(connection) == before
                assert not schema_contract_differences(connection, metadata=phase22_metadata())
                if postgres:
                    assert _security(connection) == security
            upgrade(revision=PHASE23_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            if postgres:
                statements = []
                def observe(connection, cursor, statement, parameters, context, executemany):
                    statements.append(statement.strip().split()[0].upper())
                event.listen(db.engine, 'before_cursor_execute', observe)
                try:
                    assert_postgres_startup_ready(db.engine)
                    assert set(statements) <= {'SET', 'SELECT', 'WITH', 'SHOW'}
                finally:
                    event.remove(db.engine, 'before_cursor_execute', observe)
                with db.engine.begin() as connection:
                    connection.exec_driver_sql('ALTER TABLE rehearsal DROP CONSTRAINT ck_rehearsal_type')
                with pytest.raises(RuntimeError, match='PostgreSQL startup validation failed'):
                    assert_postgres_startup_ready(db.engine)
    finally:
        _dispose(app)


def test_sqlite_revision23_preserves_data_defaults_and_guarded_downgrade(tmp_path):
    _exercise(create_migration_app('sqlite:///' + (tmp_path / 'event.db').as_posix()))


def test_postgres_revision23_preserves_acl_rows_and_readonly_startup(private_pg17):
    app = create_migration_app(private_pg17)
    with app.app_context():
        with db.engine.begin() as connection:
            for role in ('anon', 'authenticated'):
                connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
    _exercise(app, postgres=True)


def test_postgres_revision23_timeouts_are_bounded_local_and_preserve_shorter_values(private_pg17):
    migration = importlib.import_module('migrations.versions.20261023_23_admission_positions_rehearsal_type')
    engine = create_engine(private_pg17)
    try:
        with engine.connect() as connection:
            for lock, statement, expected in ((0, 0, (5000, 30000)),
                                               (1000, 2000, (1000, 2000)),
                                               (9000, 60000, (5000, 30000))):
                connection.execute(text("SELECT set_config('lock_timeout',:lock,false), "
                                        "set_config('statement_timeout',:statement,false)"),
                                   {'lock': str(lock), 'statement': str(statement)})
                connection.commit()
                with connection.begin():
                    migration._bound_timeouts(connection)
                    current = dict(connection.execute(text("SELECT name,setting::bigint FROM pg_settings "
                        "WHERE name IN ('lock_timeout','statement_timeout')")).all())
                    assert (current['lock_timeout'], current['statement_timeout']) == expected
                restored = dict(connection.execute(text("SELECT name,setting::bigint FROM pg_settings "
                    "WHERE name IN ('lock_timeout','statement_timeout')")).all())
                assert (restored['lock_timeout'], restored['statement_timeout']) == (lock, statement)
                connection.commit()
    finally:
        engine.dispose()


def test_postgres_revision23_lock_failure_preserves_revision22_and_rows(private_pg17):
    url = make_url(private_pg17).update_query_dict({'options': '-c lock_timeout=100 -c statement_timeout=1000'})
    app = create_migration_app(url.render_as_string(hide_password=False))
    try:
        with app.app_context():
            _revision22(app)
            with db.engine.begin() as connection:
                _seed(connection)
                before = _rows(connection)
                security = _security(connection)
            # A separate owned reader holds a real conflicting lock. The
            # migration keeps the shorter 100ms limit and fails atomically.
            with db.engine.connect() as blocker, blocker.begin():
                blocker.exec_driver_sql('LOCK TABLE band_membership IN ACCESS SHARE MODE')
                with pytest.raises(OperationalError) as caught:
                    upgrade(revision=PHASE23_REVISION, directory=str(MIGRATIONS_DIRECTORY))
                assert caught.value.orig.sqlstate == '55P03'
            with db.engine.connect() as connection:
                assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261021_22'
                assert _rows(connection) == before
                assert _security(connection) == security
                assert not schema_contract_differences(connection, metadata=phase22_metadata())
    finally:
        _dispose(app)


def test_every_historical_revision_remains_exact_after_revision23(tmp_path):
    app = create_migration_app('sqlite:///' + (tmp_path / 'history.db').as_posix())
    try:
        with app.app_context():
            script = ScriptDirectory.from_config(_config(app))
            revisions = list(reversed(list(script.walk_revisions())))
            assert revisions[-1].revision == PHASE23_REVISION
            for revision in revisions:
                upgrade(revision=revision.revision, directory=str(MIGRATIONS_DIRECTORY))
                metadata = metadata_for_recorded_revision(revision.revision)
                with db.engine.connect() as connection:
                    assert not schema_contract_differences(connection, metadata=metadata), revision.revision
                    assert not schema_differences(connection, metadata=metadata), revision.revision
                assert assert_upgrade_preflight() == 'stamped'
                if revision.revision == '20260916_05':
                    with db.engine.begin() as connection:
                        backfill(connection)
    finally:
        _dispose(app)


def test_legacy_copy_automatically_receives_unclassified_type_before_refresh_digests(tmp_path):
    from tests.test_legacy_rehearsal import _legacy_fixture
    from tools.legacy_rehearsal.rehearsal import _copy_and_backfill, _file_fingerprint
    from tools.legacy_rehearsal.preserve_identity_refresh import _source_rows, _table_digests
    source = tmp_path / 'legacy.sqlite'
    _legacy_fixture(source)
    before = _file_fingerprint(source)
    working = tmp_path / 'copy'
    working.mkdir()
    copied, _, evidence = _copy_and_backfill(str(source), working, expected_source_fingerprint=before)
    assert _file_fingerprint(source) == before
    assert evidence['immutable_snapshot']['sha256'] == before['sha256']
    engine = create_engine('sqlite:///' + copied.as_posix())
    app = create_migration_app('sqlite:///' + copied.as_posix())
    try:
        with app.app_context(), engine.connect() as connection:
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == PHASE23_REVISION
            assert connection.execute(text('SELECT id,title,rehearsal_type FROM rehearsal')).all() == [(8, 'R', 'UNCLASSIFIED')]
            assert not schema_contract_differences(connection, metadata=phase23_metadata())
            from sqlalchemy import MetaData
            metadata = MetaData()
            metadata.reflect(bind=connection)
            source_band = connection.execute(text('SELECT band_id FROM rehearsal WHERE id=8')).scalar_one()
            converted = _source_rows(connection, metadata.tables['rehearsal'], source_band, 12345)
            assert converted[0]['band_id'] == 12345
            assert converted[0]['rehearsal_type'] == 'UNCLASSIFIED'
            assert set(converted[0]) == set(db.metadata.tables['rehearsal'].c.keys())
            before_digest = _table_digests(connection, {'rehearsal'}, band_id=source_band)
        with engine.begin() as connection:
            connection.execute(text("UPDATE rehearsal SET rehearsal_type='LIVE' WHERE id=8"))
        with engine.connect() as connection:
            assert _table_digests(connection, {'rehearsal'}, band_id=source_band) != before_digest
    finally:
        engine.dispose()
        _dispose(app)
