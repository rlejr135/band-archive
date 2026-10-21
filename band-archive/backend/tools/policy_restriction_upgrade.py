"""Pinned v21 -> v22 public-schema backup/rehearsal and separately gated apply.

Default invocation performs no action. Credentials are read only from the
protected process environment, never command arguments, dotenv, or reports.
The immutable prior operators supply inventory, private ACL and owned-PG
primitives; their old main/apply paths are never called.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

BACKEND = Path(__file__).resolve().parents[1]
STATE = BACKEND / 'tools' / '.state'
PRIOR = STATE / 'rehearse_v19_v21_invite_name.py'
PRIOR_SHA = '435360d1955adbaacd51df5c7c2ce5b486bc61a87236ceaf8c4bf1cdbb4d16e3'
MIGRATION = BACKEND / 'migrations/versions/20261021_22_content_restrictions.py'
CA = BACKEND / 'certs/supabase-prod-ca-2021.crt'
CA_SHA = '700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7'
OLD, NEW = '20261020_21', '20261021_22'
ADDED = 'content_restriction'
ADDED_SEQ = 'content_restriction_id_seq'
ADDED_INDEX = 'ix_audit_event_upload_policy_target'
GO = 'ROOT_REVIEWED_POLICY_RESTRICTION_V21_V22_APPLY'


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


def primitives(args):
    check(args.approved_helper_sha256 == digest(Path(__file__)), 'reviewed helper pin required')
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    prior = load(PRIOR, PRIOR_SHA, 'restriction_pinned_prior')
    logo, ops = prior.primitives()
    from migration_bootstrap import metadata_for_recorded_revision
    ops.prior.TABLES = tuple(sorted(metadata_for_recorded_revision(OLD).tables))
    check(len(ops.prior.TABLES) == 37, 'source table topology differs')
    ops.EXPECTED_SOURCE_REVISION = ops.PHASE13_REVISION = OLD
    ops.verify_source_pin(args.source_commit, args.migration_sha256, MIGRATION)
    revision = load(MIGRATION, args.migration_sha256, 'restriction_exact_revision')
    check((revision.revision, revision.down_revision) == (NEW, OLD), 'migration graph differs')
    return logo, ops, revision


def source_parameters(ops):
    # Do not call the historical dotenv-capable carrier adapter.
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
    check(CA.is_file() and not CA.is_symlink() and digest(CA) == CA_SHA, 'reviewed TLS certificate differs')
    return {'host': 'aws-0-ap-northeast-1.pooler.supabase.com', 'port': '5432',
            'user': 'postgres.qrgmgglqssvpctpyixsw', 'dbname': 'postgres',
            'password': parsed.password, 'sslmode': 'verify-full', 'sslrootcert': str(CA.resolve())}


def old_security(value):
    return {**value, 'objects': [row for row in value['objects'] if row[1] not in {ADDED, ADDED_SEQ}]}


def old_catalog(value):
    catalog = value['catalog']
    return {**{key: [row for row in catalog[key] if row[0] != ADDED]
               for key in ('columns', 'constraints', 'triggers')},
            'indexes': [row for row in catalog['indexes'] if row[0] != ADDED and row[1] != ADDED_INDEX],
            'functions': catalog['functions'],
            'sequences': [row for row in catalog['sequences'] if row[1] != ADDED_SEQ],
            'sequence_dependencies': [row for row in catalog['sequence_dependencies'] if row[3] != ADDED]}


def compare_preserved(before, after, logo, connection, security, keys, ops):
    from migration_bootstrap import schema_contract_differences
    check(after['alembic_versions'] == [NEW] and len(after['tables']) == 38, 'target revision or table count differs')
    for name, expected in before['tables'].items():
        if name != 'alembic_version':
            check(after['tables'].get(name) == expected, 'existing row digest changed')
    check(after['tables'][ADDED]['rows'] == 0, 'restriction table must remain empty')
    check(old_catalog(before) == old_catalog(after), 'existing catalog or sequence state changed')
    check([row for row in after['owners'] if row[0] != ADDED] == before['owners'], 'existing table owner changed')
    check(old_security(logo.security(connection)) == security, 'existing ACL RLS ownership or defaults changed')
    check(logo.key_manifest(connection) == keys, 'existing R2 key manifest changed')
    check(not schema_contract_differences(connection), 'full target private schema contract differs')
    ops.verify_sequences_not_behind(connection.connection.driver_connection, after)


def apply_exact(connection, before, security, keys, logo, ops, revision):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from migration_bootstrap import metadata_for_recorded_revision, schema_differences
    connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout='120s'")
    connection.exec_driver_sql('SET LOCAL search_path=public,pg_catalog')
    # Establish the write barrier before the first source-row snapshot. All
    # original rows and sequence states must be comparable through the DDL.
    tables = ','.join('public.' + logo.q(name) for name in sorted(before['tables']))
    connection.exec_driver_sql('LOCK TABLE ' + tables + ' IN SHARE MODE')
    identity = connection.execute(text("""SELECT current_user,current_database(),
        current_setting('server_version_num')::integer,current_setting('transaction_read_only'),
        current_setting('TimeZone')""")).one()
    check(tuple(identity[:2]) == ('postgres', 'postgres') and int(identity[2]) // 10000 == 17
          and tuple(identity[3:]) == ('off', 'UTC'), 'write connection identity differs')
    check(connection.execute(text('SELECT version_num FROM public.alembic_version FOR UPDATE')).scalars().all() == [OLD],
          'exact source revision 21 required')
    check(not schema_differences(connection, metadata=metadata_for_recorded_revision(OLD)), 'source schema differs')
    fresh = ops.prior.public_inventory(connection.connection.driver_connection)
    check(ops.comparable_schema_and_rows(fresh) == ops.comparable_schema_and_rows(before), 'current rows differ from backup')
    check(old_catalog(fresh) == old_catalog(before), 'current sequence states differ from backup')
    check(logo.security(connection) == security and logo.key_manifest(connection) == keys, 'current security or keys differ')
    with Operations.context(MigrationContext.configure(connection)):
        revision.upgrade()
    changed = connection.execute(text('UPDATE public.alembic_version SET version_num=:new WHERE version_num=:old'),
                                 {'new': NEW, 'old': OLD})
    check(changed.rowcount == 1, 'revision transition failed')
    ops.prior.TABLES = tuple(sorted((*before['tables'], ADDED)))
    after = ops.prior.public_inventory(connection.connection.driver_connection)
    compare_preserved(before, after, logo, connection, security, keys, ops)
    return after


def rehearse(args, logo, ops, revision, work, report):
    params = source_parameters(ops)
    pg_bin = STATE / 'postgresql117/pgsql/bin'
    report['pg_versions'] = ops.verify_pg17(pg_bin)
    source = server = port = None
    try:
        source = ops.source_snapshot(params)
        engine, connection, transaction, snapshot, captured, _identity, before, _acl = source
        security, keys = logo.security(connection), logo.key_manifest(connection)
        from sqlalchemy import text
        roles = [list(row) for row in connection.execute(text('SELECT rolname,rolinherit FROM pg_roles ORDER BY rolname'))]
        report.update(captured_at_utc=captured.isoformat(), before=ops.concise_inventory(before))
        (work / 'manifest.json').write_text(json.dumps({'inventory': before, 'security': security,
             'keys': keys, 'roles': roles}, sort_keys=True, default=str), encoding='utf-8')
        dump = work / 'production-public-v21.dump'
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
            logo.replay_roles_and_defaults(raw, roles, security)
        restored = ops.run([str(pg_bin / 'pg_restore.exe'), '--exit-on-error', '--no-password',
             '--host=127.0.0.1', f'--port={port}', '--username=postgres', '--dbname=postgres', str(dump)],
             env=ops.local_environment(port), timeout=600)
        check(restored.returncode == 0, 'faithful public restore failed')
        ops.verify_owned_cluster(server, port, data)
        app = ops.create_migration_app(f'postgresql+psycopg://postgres@127.0.0.1:{port}/postgres?sslmode=disable')
        with app.app_context():
            try:
                with ops.db.engine.connect() as local:
                    observed = ops.prior.public_inventory(local.connection.driver_connection)
                    check(ops.comparable_schema_and_rows(observed) == ops.comparable_schema_and_rows(before), 'restored rows or schema differ')
                    check(logo.security(local) == security and logo.key_manifest(local) == keys, 'restored security or keys differ')
                    ops.verify_sequences_not_behind(local.connection.driver_connection, observed)
                with ops.db.engine.begin() as local:
                    after = apply_exact(local, observed, security, keys, logo, ops, revision)
                report.update(after=ops.concise_inventory(after), local_migration_verified=True,
                              restore_verified=True, original_rows_security_keys_preserved=True)
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
    folder = Path(args.evidence_directory or '').resolve()
    check(folder.parent == STATE.resolve() and not folder.is_symlink(), 'evidence must be a private direct state child')
    pins = {'report.json': args.report_sha256, 'manifest.json': args.manifest_sha256,
            'production-public-v21.dump': args.dump_sha256}
    for name, pin in pins.items():
        path = folder / name
        check(path.is_file() and not path.is_symlink() and digest(path) == pin, 'reviewed evidence pin differs')
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    check(report['status'] == 'verified' and report['mode'] == 'rehearse' and report['remote_writes'] is False,
          'successful read-only rehearsal required')
    check(report['source_commit'] == args.source_commit and report['migration_sha256'] == args.migration_sha256
          and report['helper_sha256'] == args.approved_helper_sha256, 'evidence source pins differ')
    check(all(report.get(key) is True for key in ('local_migration_verified', 'restore_verified',
                                                  'original_rows_security_keys_preserved'))
          and report['local_stop']['process_exited'] is True, 'rehearsal verification incomplete')
    check(report['before']['revision'] == OLD and report['before']['table_count'] == 37
          and report['after']['revision'] == NEW and report['after']['table_count'] == 38
          and report['after']['row_counts'][ADDED] == 0, 'evidence topology differs')
    check(report['public_dump_sha256'] == args.dump_sha256, 'archive provenance differs')
    captured = datetime.fromisoformat(report['captured_at_utc'])
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    check(0 <= (datetime.now(timezone.utc) - captured).total_seconds() <= 86400, 'rehearsal older than 24 hours')
    return manifest


def apply_reviewed(args, logo, ops, revision, work, report):
    check(args.operator_go == GO, 'separately reviewed production GO required')
    manifest = evidence(args)
    params = source_parameters(ops)
    from sqlalchemy import create_engine, text
    from migration_bootstrap import schema_contract_differences
    def writer():
        with ops.isolated_local_libpq_environment():
            return ops.psycopg.connect(**params, connect_timeout=5, autocommit=False,
                 options='-c default_transaction_read_only=off -c statement_timeout=120000 -c lock_timeout=5000 '
                         '-c timezone=UTC -c datestyle=ISO,YMD -c intervalstyle=postgres '
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
                        after = apply_exact(connection, manifest['inventory'], manifest['security'],
                                            manifest['keys'], logo, ops, revision)
                        report['commit_started'] = True
                        transaction.commit(); report['committed'] = True
                    except Exception:
                        if transaction.is_active:
                            transaction.rollback()
                        raise
            finally:
                engine.dispose()
            ops.prior.TABLES = tuple(sorted((*manifest['inventory']['tables'], ADDED)))
            readonly = create_engine('postgresql+psycopg://', creator=lambda: ops.prior.remote_connection(params))
            try:
                with readonly.connect() as fresh, fresh.begin():
                    fresh.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    observed = ops.prior.public_inventory(fresh.connection.driver_connection)
                    compare_preserved(manifest['inventory'], observed, logo, fresh,
                                      manifest['security'], manifest['keys'], ops)
                    check(not schema_contract_differences(fresh), 'fresh target contract differs')
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
        print('POLICY_RESTRICTION_READY_NO_ACTION')
        return 0
    logo, ops, revision = primitives(args)
    work = ops.secure_private_directory(STATE / ('policy21-22-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
                                               + '-' + uuid.uuid4().hex[:12]))
    report = {'status': 'started', 'mode': 'apply' if args.apply else 'rehearse',
              'remote_writes': bool(args.apply), 'committed': False,
              'source_commit': args.source_commit, 'migration_sha256': args.migration_sha256,
              'helper_sha256': args.approved_helper_sha256,
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
    print('POLICY_RESTRICTION_' + report['status'].upper() + ' ' + str(work / 'report.json'))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('POLICY_RESTRICTION_STOPPED ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
