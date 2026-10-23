"""Pinned v22 -> v23 public-schema backup/rehearsal and gated apply.

The default invocation is inert. Rehearsal reads one repeatable-read public
snapshot and applies the reviewed migration only to an owned local PostgreSQL
17 restore. Production apply needs separate review and pinned rehearsal files.
Credentials come only from the protected process environment.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import uuid

BACKEND = Path(__file__).resolve().parents[1]
STATE = BACKEND / 'tools' / '.state'
PRIOR = STATE / 'rehearse_v19_v21_invite_name.py'
PRIOR_SHA = '435360d1955adbaacd51df5c7c2ce5b486bc61a87236ceaf8c4bf1cdbb4d16e3'
MIGRATION = BACKEND / 'migrations/versions/20261023_23_admission_positions_rehearsal_type.py'
CA = BACKEND / 'certs/supabase-prod-ca-2021.crt'
CA_SHA = '700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7'
OLD, NEW = '20261021_22', '20261023_23'
ADDED_COLUMNS = {
    'band_membership': ('requested_positions_json', 'requested_custom_position'),
    'rehearsal': ('rehearsal_type',),
}
ADDED_CHECK = ('rehearsal', 'ck_rehearsal_type')
GO = 'ROOT_REVIEWED_ADMISSION_EVENT_V22_V23_APPLY'


class GateError(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise GateError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1048576), b''):
            h.update(part)
    return h.hexdigest()


def load(path, pin, name):
    check(path.is_file() and not path.is_symlink() and digest(path) == pin, 'source pin differs')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_public_tables(metadata):
    application = tuple(sorted(metadata.tables))
    check(len(application) == 37 and 'alembic_version' not in application,
          'source table topology differs')
    public = tuple(sorted((*application, 'alembic_version')))
    check(len(public) == 38, 'source table topology differs')
    return public


def primitives(args):
    check(args.approved_helper_sha256 == digest(Path(__file__)), 'reviewed helper pin required')
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    prior = load(PRIOR, PRIOR_SHA, 'admission_event_pinned_prior')
    logo, ops = prior.primitives()
    from migration_bootstrap import metadata_for_recorded_revision
    ops.prior.TABLES = source_public_tables(metadata_for_recorded_revision(OLD))
    ops.EXPECTED_SOURCE_REVISION = ops.PHASE13_REVISION = OLD
    ops.verify_source_pin(args.source_commit, args.migration_sha256, MIGRATION)
    revision = load(MIGRATION, args.migration_sha256, 'admission_event_exact_revision')
    check((revision.revision, revision.down_revision) == (NEW, OLD), 'migration graph differs')
    return logo, ops, revision


def source_parameters(ops):
    # Never use the historical dotenv-capable connection adapter.
    from sqlalchemy.engine import make_url
    raw = os.environ.get('REHEARSAL_TARGET_URL')
    check(bool(raw), 'protected process connection carrier required')
    try:
        parsed = make_url(raw)
        check(parsed.drivername in {'postgresql', 'postgresql+psycopg'} and bool(parsed.password),
              'invalid PostgreSQL password carrier')
    except GateError:
        raise
    except Exception:
        raise GateError('invalid PostgreSQL password carrier') from None
    check(CA.is_file() and not CA.is_symlink() and digest(CA) == CA_SHA,
          'reviewed TLS certificate differs')
    return {'host': 'aws-0-ap-northeast-1.pooler.supabase.com', 'port': '5432',
            'user': 'postgres.qrgmgglqssvpctpyixsw', 'dbname': 'postgres',
            'password': parsed.password, 'sslmode': 'verify-full', 'sslrootcert': str(CA.resolve())}


def _without_rows(rows, predicate):
    return [row for row in rows if not predicate(row)]


def old_catalog(value):
    """Mask only the three new columns and their one check from prior catalog."""
    catalog = value['catalog']
    result = {key: list(rows) for key, rows in catalog.items()}
    result['columns'] = _without_rows(catalog['columns'],
        lambda row: len(row) > 1 and row[0] in ADDED_COLUMNS and row[1] in ADDED_COLUMNS[row[0]])
    result['constraints'] = _without_rows(catalog['constraints'],
        lambda row: len(row) > 2 and (row[0], row[2]) == ADDED_CHECK)
    return result


def role_and_schema_manifest(connection):
    from sqlalchemy import text
    roles = [list(row) for row in connection.execute(text("""SELECT rolname,rolsuper,rolinherit,
        rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,
        rolvaliduntil::text,rolconfig::text FROM pg_roles ORDER BY rolname"""))]
    memberships = [list(row) for row in connection.execute(text("""SELECT parent.rolname,member.rolname,
        grantor.rolname,m.admin_option,m.inherit_option,m.set_option
        FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid
        JOIN pg_roles member ON member.oid=m.member JOIN pg_roles grantor ON grantor.oid=m.grantor
        ORDER BY parent.rolname,member.rolname,grantor.rolname"""))]
    schema_acl = [list(row) for row in connection.execute(text("""SELECT n.nspname,
        pg_get_userbyid(n.nspowner),CASE WHEN a.grantor=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantor) END,
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable
        FROM pg_namespace n CROSS JOIN LATERAL aclexplode(
          COALESCE(n.nspacl,acldefault('n'::"char",n.nspowner))) a
        WHERE n.nspname='public' ORDER BY 1,2,3,4,5,6"""))]
    return {'roles': roles, 'memberships': memberships, 'public_schema_acl': schema_acl}


def original_columns(connection, tables):
    from sqlalchemy import text
    result = {}
    for table in tables:
        names = [row[0] for row in connection.execute(text("""SELECT a.attname
            FROM pg_attribute a WHERE a.attrelid=to_regclass(:relation) AND a.attnum>0
              AND NOT a.attisdropped ORDER BY a.attnum"""), {'relation': 'public.' + table})]
        result[table] = [name for name in names if name not in ADDED_COLUMNS.get(table, ())]
    check(all(result.values()), 'original column inventory is incomplete')
    return result


def original_row_digests(raw, columns):
    """Hash old application columns; the Alembic ledger is checked by revision."""
    result = {}
    for statement in ("SET LOCAL TIME ZONE 'UTC'", "SET LOCAL DateStyle='ISO,YMD'",
                      "SET LOCAL IntervalStyle='postgres'", "SET LOCAL extra_float_digits=3",
                      "SET LOCAL bytea_output='hex'"):
        raw.execute(statement)
    for table, names in sorted(columns.items()):
        if table == 'alembic_version':
            continue
        quoted = ','.join('"' + name.replace('"', '""') + '"' for name in names)
        relation = 'public."' + table.replace('"', '""') + '"'
        hasher = hashlib.sha256()
        count = 0
        with raw.cursor(name='admission_event_preserve_' + table) as cursor:
            cursor.execute(f'SELECT to_jsonb(source_row)::text AS row_json FROM '
                           f'(SELECT {quoted} FROM {relation}) AS source_row '
                           'ORDER BY to_jsonb(source_row)::text COLLATE "C"')
            while rows := cursor.fetchmany(1000):
                for (row,) in rows:
                    hasher.update(row.encode('utf-8'))
                    hasher.update(b'\n')
                    count += 1
        result[table] = {'rows': count, 'sha256': hasher.hexdigest()}
    return result


def security_fingerprint(logo, connection):
    return {'logo': logo.security(connection), **role_and_schema_manifest(connection)}


def local_security_matches_source(source, local):
    """Check restoreable security exactly; full role attributes are source-only."""
    return (local['logo'] == source['logo']
            and local['public_schema_acl'] == source['public_schema_acl'])


def public_schema_acl(connection):
    statement = """SELECT n.nspname,
        pg_get_userbyid(n.nspowner),
        CASE WHEN a.grantor=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantor) END,
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable
        FROM pg_namespace n CROSS JOIN LATERAL aclexplode(
          COALESCE(n.nspacl,acldefault('n'::"char",n.nspowner))) a
        WHERE n.nspname='public' ORDER BY 1,2,3,4,5,6"""
    result = _execute_statement(connection, statement)
    return [list(row) for row in result]


def _execute_statement(connection, statement):
    return connection.exec_driver_sql(statement) if hasattr(connection, 'exec_driver_sql') \
        else connection.execute(statement)


def replay_missing_public_schema_acl(ops, connection, server, port, data, source_acl):
    """Replay only the reviewed missing PUBLIC USAGE row on an owned local restore."""
    ops.verify_owned_cluster(server, port, data)
    statement = """SELECT current_user,current_database(),
        current_setting('server_version_num')::integer,inet_server_port(),
        current_setting('data_directory'),
        (inet_server_addr() << inet '127.0.0.0/8' OR inet_server_addr() << inet '::1/128'),
        (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)"""
    result = _execute_statement(connection, statement)
    identity = result.one() if hasattr(result, 'one') else result.fetchone()
    check(identity[0:2] == ('postgres', 'postgres') and int(identity[2]) // 10000 == 17
          and identity[3] == port and Path(identity[4]).resolve() == Path(data).resolve()
          and bool(identity[5]) and identity[6] is True,
          'public schema ACL replay requires the owned local PostgreSQL restore')

    expected = ['public', 'pg_database_owner', 'pg_database_owner', 'PUBLIC', 'USAGE', False]
    before = public_schema_acl(connection)
    source_counts = Counter(tuple(row) for row in source_acl)
    local_counts = Counter(tuple(row) for row in before)
    expected_tuple = tuple(expected)
    check(source_counts - local_counts == Counter({expected_tuple: 1})
          and not (local_counts - source_counts),
          'local public schema ACL differs beyond the one reviewed PUBLIC USAGE grant')

    _execute_statement(connection, 'SET LOCAL ROLE "pg_database_owner"')
    _execute_statement(connection, 'GRANT USAGE ON SCHEMA public TO PUBLIC')
    _execute_statement(connection, 'RESET ROLE')
    check(public_schema_acl(connection) == source_acl,
          'replayed public schema ACL does not exactly match source')
    return True


def compare_preserved(before, after, before_rows, before_security, before_keys,
                      logo, ops, connection, raw, columns):
    from migration_bootstrap import schema_contract_differences
    check(after['alembic_versions'] == [NEW] and len(after['tables']) == 38,
          'target revision or table count differs')
    check(original_row_digests(raw, columns) == before_rows, 'original column row digest changed')
    from sqlalchemy import text
    pending_values = connection.execute(text("""SELECT
        (SELECT count(*) FROM public.band_membership
          WHERE requested_positions_json IS NOT NULL OR requested_custom_position IS NOT NULL),
        (SELECT count(*) FROM public.rehearsal WHERE rehearsal_type IS DISTINCT FROM 'UNCLASSIFIED')""")).one()
    check(tuple(pending_values) == (0, 0), 'new migration columns contain inferred or classified values')
    check(old_catalog(before) == old_catalog(after), 'existing catalog or sequence state changed')
    check(after['owners'] == before['owners'], 'existing table owner changed')
    check(security_fingerprint(logo, connection) == before_security,
          'existing ACL, RLS, role, membership, or schema ACL changed')
    check(logo.key_manifest(connection) == before_keys, 'existing R2 key manifest changed')
    check(not schema_contract_differences(connection), 'full target private schema contract differs')
    ops.verify_sequences_not_behind(raw, after)


def _timeout_milliseconds(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(ms|s|min|h|d)?\s*', value, re.IGNORECASE)
    check(match is not None, 'PostgreSQL timeout setting is not recognizable')
    unit = (match.group(2) or 'ms').lower()
    multiplier = {'ms': 1, 's': 1000, 'min': 60000, 'h': 3600000, 'd': 86400000}[unit]
    return int(float(match.group(1)) * multiplier)


def _bound_timeouts(connection):
    # SHOW is a utility command and does not establish a repeatable-read
    # snapshot. The first MVCC read occurs only after the SHARE write barrier.
    current = {name: _timeout_milliseconds(connection.exec_driver_sql('SHOW ' + name).scalar_one())
               for name in ('lock_timeout', 'statement_timeout')}
    for name, maximum in (('lock_timeout', 5000), ('statement_timeout', 30000)):
        if current[name] == 0 or current[name] > maximum:
            connection.exec_driver_sql(f"SET LOCAL {name} = '{maximum}ms'")


def apply_exact(connection, before, before_rows, security, keys, columns, logo, ops, revision):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from migration_bootstrap import metadata_for_recorded_revision, schema_differences
    _bound_timeouts(connection)
    connection.exec_driver_sql('SET LOCAL search_path=public,pg_catalog')
    tables = ','.join('public.' + logo.q(name) for name in sorted(before['tables']))
    connection.exec_driver_sql('LOCK TABLE ' + tables + ' IN SHARE MODE')
    identity = connection.execute(text("""SELECT current_user,current_database(),
        current_setting('server_version_num')::integer,current_setting('transaction_read_only'),
        current_setting('TimeZone')""")).one()
    check(tuple(identity[:2]) == ('postgres', 'postgres') and int(identity[2]) // 10000 == 17
          and tuple(identity[3:]) == ('off', 'UTC'), 'write connection identity differs')
    check(connection.execute(text('SELECT version_num FROM public.alembic_version FOR UPDATE')).scalars().all() == [OLD],
          'exact source revision 22 required')
    check(not schema_differences(connection, metadata=metadata_for_recorded_revision(OLD)),
          'source schema differs')
    fresh = ops.prior.public_inventory(connection.connection.driver_connection)
    check(ops.comparable_schema_and_rows(fresh) == ops.comparable_schema_and_rows(before),
          'current rows differ from backup')
    check(old_catalog(fresh) == old_catalog(before), 'current sequence states differ from backup')
    check(security_fingerprint(logo, connection) == security and logo.key_manifest(connection) == keys,
          'current security or keys differ')
    check(original_row_digests(connection.connection.driver_connection, columns) == before_rows,
          'current original-column rows differ from backup')
    with Operations.context(MigrationContext.configure(connection)):
        revision.upgrade()
    changed = connection.execute(text('UPDATE public.alembic_version SET version_num=:new WHERE version_num=:old'),
                                 {'new': NEW, 'old': OLD})
    check(changed.rowcount == 1, 'revision transition failed')
    after = ops.prior.public_inventory(connection.connection.driver_connection)
    compare_preserved(before, after, before_rows, security, keys, logo, ops, connection,
                      connection.connection.driver_connection, columns)
    return after


def rehearse(args, logo, ops, revision, work, report):
    params = source_parameters(ops)
    pg_bin = STATE / 'postgresql117/pgsql/bin'
    report['pg_versions'] = ops.verify_pg17(pg_bin)
    source = server = port = None
    try:
        source = ops.source_snapshot(params)
        engine, connection, transaction, snapshot, captured, _identity, before, _acl = source
        columns = original_columns(connection, ops.prior.TABLES)
        before_rows = original_row_digests(connection.connection.driver_connection, columns)
        security, keys = security_fingerprint(logo, connection), logo.key_manifest(connection)
        roles = [[row[0], row[2]] for row in security['roles']]
        report.update(captured_at_utc=captured.isoformat(), before=ops.concise_inventory(before))
        (work / 'manifest.json').write_text(json.dumps({'inventory': before, 'security': security,
            'keys': keys, 'roles': roles, 'original_columns': columns, 'original_rows': before_rows},
            sort_keys=True, default=str), encoding='utf-8')
        dump = work / 'production-public-v22.dump'
        result = ops.run([str(pg_bin / 'pg_dump.exe'), '--format=custom', '--no-password',
            '--schema=public', '--strict-names', '--lock-wait-timeout=5s', f'--snapshot={snapshot}',
            '--file', str(dump)], env=ops.source_environment(params), timeout=600)
        check(result.returncode == 0, 'public snapshot dump failed')
        report['public_dump_sha256'] = digest(dump)
        listed = ops.run([str(pg_bin / 'pg_restore.exe'), '--list', str(dump)], env=ops.clean_environment(), timeout=60)
        check(listed.returncode == 0 and b' public ' in listed.stdout, 'public archive list invalid')
        (work / 'archive-list.txt').write_bytes(listed.stdout)
        transaction.rollback(); connection.close(); engine.dispose(); source = None
        data = work / 'local-pg17-data'
        result = ops.run_quiet([str(pg_bin / 'initdb.exe'), '--no-locale', '--encoding=UTF8',
            '--username=postgres', '--auth=trust', '--pgdata', str(data)], env=ops.clean_environment(), timeout=300)
        check(result.returncode == 0, 'owned local initialization failed')
        port = ops.free_port()
        with (work / 'local-pg.log').open('ab') as log:
            server = subprocess.Popen([str(pg_bin / 'postgres.exe'), '-D', str(data), '-h', '127.0.0.1',
                '-p', str(port), '-c', 'timezone=UTC', '-c', 'max_connections=10', '-c', 'shared_buffers=16MB'],
                env=ops.local_environment(port), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and server.poll() is None:
            try:
                with ops.local_connect(port) as raw:
                    raw.execute('SELECT 1')
                break
            except ops.psycopg.OperationalError:
                time.sleep(.15)
        ops.verify_owned_cluster(server, port, data)
        with ops.local_connect(port) as raw:
            ops.drop_empty_default_public_schema(raw)
            logo.replay_roles_and_defaults(raw, roles, security['logo'])
        restored = ops.run([str(pg_bin / 'pg_restore.exe'), '--exit-on-error', '--no-password',
            '--host=127.0.0.1', f'--port={port}', '--username=postgres', '--dbname=postgres', str(dump)],
            env=ops.local_environment(port), timeout=600)
        check(restored.returncode == 0, 'faithful public restore failed')
        ops.verify_owned_cluster(server, port, data)
        with ops.local_connect(port) as raw:
            replay_missing_public_schema_acl(ops, raw, server, port, data,
                                            security['public_schema_acl'])
        app = ops.create_migration_app(f'postgresql+psycopg://postgres@127.0.0.1:{port}/postgres?sslmode=disable')
        with app.app_context():
            try:
                with ops.db.engine.connect() as local:
                    observed = ops.prior.public_inventory(local.connection.driver_connection)
                    check(ops.comparable_schema_and_rows(observed) == ops.comparable_schema_and_rows(before),
                          'restored rows or schema differ')
                    local_security = security_fingerprint(logo, local)
                    check(local_security_matches_source(security, local_security)
                          and logo.key_manifest(local) == keys,
                          'restored ACL, RLS, schema ACL, memberships, defaults, or keys differ')
                    check(original_row_digests(local.connection.driver_connection, columns) == before_rows,
                          'restored original-column rows differ')
                    ops.verify_sequences_not_behind(local.connection.driver_connection, observed)
                with ops.db.engine.begin() as local:
                    after = apply_exact(local, observed, before_rows, local_security, keys, columns,
                                        logo, ops, revision)
                report.update(after=ops.concise_inventory(after), local_migration_verified=True,
                    restore_verified=True, local_security_replay_verified=True,
                    local_role_attribute_copy=False,
                    local_security_scope='public ACL/RLS/default privileges/schema ACL and relevant memberships; '
                        'remote LOGIN/SUPERUSER/BYPASS attributes are not copied to localhost',
                    original_rows_security_keys_preserved=True)
            finally:
                ops.db.session.remove(); ops.db.engine.dispose()
    finally:
        if source is not None:
            engine, connection, transaction, *_ = source
            transaction.rollback(); connection.close(); engine.dispose()
        if server is not None:
            report['local_stop'] = ops.stop_owned_process(server, pg_bin / 'pg_ctl.exe', port,
                work / 'local-pg17-data', ops.local_environment(port))
            check(server.poll() is not None, 'owned local server did not exit')


def evidence(args):
    candidate = Path(args.evidence_directory or '')
    check(candidate.parent.resolve() == STATE.resolve() and not candidate.is_symlink(),
          'evidence must be a private direct state child')
    folder = candidate.resolve()
    check(folder.parent == STATE.resolve(), 'evidence must be a private direct state child')
    pins = {'report.json': args.report_sha256, 'manifest.json': args.manifest_sha256,
            'production-public-v22.dump': args.dump_sha256}
    for name, pin in pins.items():
        path = folder / name
        check(path.is_file() and not path.is_symlink() and digest(path) == pin,
              'reviewed evidence pin differs')
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    check(report['status'] == 'verified' and report['mode'] == 'rehearse' and report['remote_writes'] is False,
          'successful read-only rehearsal required')
    check(report['source_commit'] == args.source_commit and report['migration_sha256'] == args.migration_sha256
          and report['helper_sha256'] == args.approved_helper_sha256, 'evidence source pins differ')
    check(all(report.get(key) is True for key in ('local_migration_verified', 'restore_verified',
          'local_security_replay_verified', 'original_rows_security_keys_preserved'))
          and report['local_role_attribute_copy'] is False
          and report['local_stop']['process_exited'] is True,
          'rehearsal verification incomplete')
    check(report['before']['revision'] == OLD and report['before']['table_count'] == 38
          and report['after']['revision'] == NEW and report['after']['table_count'] == 38,
          'evidence topology differs')
    check(report['public_dump_sha256'] == args.dump_sha256, 'archive provenance differs')
    captured = datetime.fromisoformat(report['captured_at_utc'])
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    check(0 <= (datetime.now(timezone.utc) - captured).total_seconds() <= 86400,
          'rehearsal older than 24 hours')
    return manifest


def apply_reviewed(args, logo, ops, revision, work, report):
    check(args.operator_go == GO, 'separately reviewed production GO required')
    manifest = evidence(args)
    tables = set(ops.prior.TABLES)
    check(set(manifest['inventory']['tables']) == tables
          and set(manifest['original_columns']) == tables
          and set(manifest['original_rows']) == tables - {'alembic_version'},
          'pinned evidence table or projection manifest differs')
    check(all(isinstance(name, str) and name for cols in manifest['original_columns'].values()
              for name in cols), 'pinned evidence column projection is invalid')
    params = source_parameters(ops)
    from sqlalchemy import create_engine
    from migration_bootstrap import schema_contract_differences

    def writer():
        with ops.isolated_local_libpq_environment():
            return ops.psycopg.connect(**params, connect_timeout=5, autocommit=False,
                options='-c default_transaction_read_only=off -c timezone=UTC -c datestyle=ISO,YMD -c intervalstyle=postgres '
                    '-c extra_float_digits=3 -c bytea_output=hex -c search_path=public,pg_catalog')

    app = ops.create_migration_app('sqlite:///:memory:')
    with app.app_context():
        try:
            engine = create_engine('postgresql+psycopg://', creator=writer, pool_pre_ping=False)
            try:
                with engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE')
                        after = apply_exact(connection, manifest['inventory'], manifest['original_rows'],
                            manifest['security'], manifest['keys'], manifest['original_columns'], logo, ops, revision)
                        report['commit_started'] = True
                        transaction.commit(); report['committed'] = True
                    except Exception:
                        if transaction.is_active:
                            transaction.rollback()
                        raise
            finally:
                engine.dispose()
            ops.prior.TABLES = tuple(sorted(manifest['inventory']['tables']))
            readonly = create_engine('postgresql+psycopg://', creator=lambda: ops.prior.remote_connection(params))
            try:
                with readonly.connect() as fresh:
                    transaction = fresh.begin()
                    fresh.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    observed = ops.prior.public_inventory(fresh.connection.driver_connection)
                    compare_preserved(manifest['inventory'], observed, manifest['original_rows'],
                        manifest['security'], manifest['keys'], logo, ops, fresh,
                        fresh.connection.driver_connection, manifest['original_columns'])
                    check(not schema_contract_differences(fresh), 'fresh target contract differs')
                    transaction.commit()
                    report.update(fresh_readonly_postcheck=True, after=ops.concise_inventory(after))
            finally:
                readonly.dispose()
        finally:
            ops.db.session.remove(); ops.db.engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--rehearse', action='store_true')
    modes.add_argument('--apply', action='store_true')
    for name in ('approved-helper-sha256', 'source-commit', 'migration-sha256', 'operator-go',
                 'evidence-directory', 'report-sha256', 'manifest-sha256', 'dump-sha256'):
        parser.add_argument('--' + name)
    args = parser.parse_args(argv)
    if not (args.rehearse or args.apply):
        print('ADMISSION_EVENT_READY_NO_ACTION')
        return 0
    logo, ops, revision = primitives(args)
    work = ops.secure_private_directory(STATE / ('admission-event22-23-' +
        time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:12]))
    report = {'status': 'started', 'mode': 'apply' if args.apply else 'rehearse',
        'remote_writes': bool(args.apply), 'committed': False, 'source_commit': args.source_commit,
        'migration_sha256': args.migration_sha256, 'helper_sha256': args.approved_helper_sha256,
        'backup_scope': 'public application schema only; managed Auth excluded'}
    try:
        if args.apply:
            apply_reviewed(args, logo, ops, revision, work, report)
        else:
            rehearse(args, logo, ops, revision, work, report)
        report['status'] = 'verified'
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__,
            error_functions=[frame.name for frame in traceback.extract_tb(error.__traceback__)])
        if type(error) is GateError:
            report['failed_gate'] = str(error)
        if report.get('commit_started') and report['committed'] is not True:
            report['committed'] = 'unknown'
    (work / 'report.json').write_text(json.dumps(report, sort_keys=True, indent=2), encoding='utf-8')
    print('ADMISSION_EVENT_' + report['status'].upper() + ' ' + str(work / 'report.json'))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('ADMISSION_EVENT_STOPPED ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
