"""Pinned revision 23 -> 24 backup, local rehearsal, and separately gated apply.

This operator changes only the reviewed featured-media partial unique index.
Credentials are read only from the protected REHEARSAL_TARGET_URL environment
carrier. The default invocation is inert.
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
import subprocess
import sys
import time
import traceback
import uuid

BACKEND = Path(__file__).resolve().parents[1]
STATE = BACKEND / 'tools' / '.state'
PRIOR = BACKEND / 'tools' / 'admission_event_upgrade.py'
PRIOR_SHA = '1fcc97aa2dbE12d72D519D0A6CF7EEB3774C0C05A57066A72EF4BF0B2726B611'.lower()
PRIOR_MIGRATION_SHA = '749828924f7404d5a3f62f441994cbaa5e5b13dab605f0ae1aae2a97186bbc60'
MIGRATION = BACKEND / 'migrations/versions/20261024_24_featured_media_per_song.py'
OLD, NEW = '20261023_23', '20261024_24'
INDEX = 'uq_media_featured_band_song'
GO = 'ROOT_REVIEWED_FEATURED_MEDIA_V23_V24_APPLY'


class GateError(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise GateError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1048576), b''):
            value.update(part)
    return value.hexdigest()


def load(path, pin, name):
    check(path.is_file() and not path.is_symlink() and digest(path) == pin,
          'reviewed source pin differs')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def primitives(args):
    check(args.approved_helper_sha256 == digest(Path(__file__)), 'reviewed helper pin required')
    prior = load(PRIOR, PRIOR_SHA, 'featured_media_pinned_v23_operator')
    inherited = argparse.Namespace(approved_helper_sha256=PRIOR_SHA,
                                   source_commit=args.source_commit,
                                   migration_sha256=PRIOR_MIGRATION_SHA)
    logo, ops, _prior_revision = prior.primitives(inherited)
    from migration_bootstrap import metadata_for_recorded_revision
    ops.prior.TABLES = prior.source_public_tables(metadata_for_recorded_revision(OLD))
    ops.EXPECTED_SOURCE_REVISION = ops.PHASE13_REVISION = OLD
    ops.verify_source_pin(args.source_commit, args.migration_sha256, MIGRATION)
    revision = load(MIGRATION, args.migration_sha256, 'featured_media_exact_revision')
    check((revision.revision, revision.down_revision) == (NEW, OLD), 'migration graph differs')
    return prior, logo, ops, revision


def original_columns(connection, tables):
    from sqlalchemy import text
    result = {}
    for table in tables:
        result[table] = [row[0] for row in connection.execute(text("""SELECT a.attname
            FROM pg_attribute a WHERE a.attrelid=to_regclass(:relation) AND a.attnum>0
              AND NOT a.attisdropped ORDER BY a.attnum"""),
              {'relation': 'public.' + table})]
    check(all(result.values()), 'source column inventory is incomplete')
    return result


def public_object_manifest(connection):
    """Return exact public FK/index/trigger definitions for transition comparison."""
    from sqlalchemy import text
    return {
        'foreign_keys': [list(row) for row in connection.execute(text("""SELECT
            conrelid::regclass::text,conname,pg_get_constraintdef(oid,true)
            FROM pg_constraint WHERE contype='f'
              AND connamespace='public'::regnamespace ORDER BY 1,2"""))],
        'indexes': [list(row) for row in connection.execute(text("""SELECT tablename,indexname,indexdef
            FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2"""))],
        'triggers': [list(row) for row in connection.execute(text("""SELECT c.relname,t.tgname,
            pg_get_triggerdef(t.oid,true) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal ORDER BY 1,2"""))],
    }


def without_featured_index(objects):
    return {key: [row for row in rows if not (key == 'indexes' and len(row) > 1
                                              and row[0] == 'media' and row[1] == INDEX)]
            for key, rows in objects.items()}


def normalized_inventory(inventory):
    """Mask only the expected ledger row, index count, aggregate object hash, and new index."""
    result = json.loads(json.dumps(inventory, default=str))
    check(result.get('tables', {}).get('alembic_version', {}).get('rows') == 1,
          'Alembic ledger row count differs from the one-row contract')
    result['tables']['alembic_version']['sha256'] = '<revision-transition>'
    result['alembic_versions'] = ['<revision-transition>']
    if 'indexes' in result:
        result['indexes'] = '<reviewed-index-transition>'
    if 'object_sha256' in result:
        result['object_sha256'] = '<recomputed-from-exact-object-manifest>'
    catalog = result.get('catalog')
    if isinstance(catalog, dict) and 'indexes' in catalog:
        catalog['indexes'] = [row for row in catalog['indexes']
                              if not (len(row) > 1 and row[0] == 'media' and row[1] == INDEX)]
    return result


def compare_inventories(before, after, *, revision_transition):
    expected_before, expected_after = (OLD, NEW) if revision_transition else (OLD, OLD)
    check(before.get('alembic_versions') == [expected_before]
          and after.get('alembic_versions') == [expected_after], 'inventory revision differs')
    before_ledger = before.get('tables', {}).get('alembic_version', {})
    after_ledger = after.get('tables', {}).get('alembic_version', {})
    check(before_ledger.get('rows') == after_ledger.get('rows') == 1,
          'Alembic ledger row count differs')
    check(all(name in before and name in after for name in ('indexes', 'foreign_keys', 'triggers')),
          'public object inventory summary is incomplete')
    check(after['indexes'] == before['indexes'] + int(revision_transition),
          'public index count differs beyond the reviewed index')
    check(after['foreign_keys'] == before['foreign_keys'] and after['triggers'] == before['triggers'],
          'public foreign-key or trigger count changed')
    check(normalized_inventory(before) == normalized_inventory(after),
          'inventory changed beyond the reviewed revision and index')


def verify_object_transition(before_objects, after_objects):
    before_indexes = before_objects.get('indexes', [])
    after_indexes = after_objects.get('indexes', [])
    check(not any(len(row) > 1 and row[1] == INDEX for row in before_indexes),
          'featured-media index already exists in source inventory')
    added = [row for row in after_indexes if len(row) > 1 and row[1] == INDEX]
    check(len(added) == 1 and added[0][0] == 'media'
          and len(after_indexes) == len(before_indexes) + 1,
          'public object inventory differs beyond one featured-media index')
    check(without_featured_index(before_objects) == without_featured_index(after_objects),
          'foreign keys, remaining indexes, or triggers changed')


def verify_featured_index(connection, *, present):
    from sqlalchemy import text
    row = connection.execute(text("""SELECT tn.nspname,t.relname,i.indisunique,i.indisvalid,i.indisready,
        i.indnkeyatts,i.indnatts,i.indimmediate,i.indisprimary,i.indisexclusion,
        i.indnullsnotdistinct,am.amname,
        pg_get_expr(i.indpred,i.indrelid),pg_get_indexdef(i.indexrelid),
        ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,ord)
              JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
              WHERE k.ord<=i.indnkeyatts ORDER BY k.ord),
        ARRAY(SELECT opc.opcdefault FROM unnest(i.indclass) WITH ORDINALITY k(opcoid,ord)
              JOIN pg_opclass opc ON opc.oid=k.opcoid ORDER BY k.ord),
        ARRAY(SELECT opt FROM unnest(i.indoption) WITH ORDINALITY k(opt,ord) ORDER BY k.ord)
        FROM pg_index i JOIN pg_class x ON x.oid=i.indexrelid
        JOIN pg_namespace n ON n.oid=x.relnamespace
        JOIN pg_class t ON t.oid=i.indrelid
        JOIN pg_namespace tn ON tn.oid=t.relnamespace
        JOIN pg_am am ON am.oid=x.relam
        WHERE n.nspname='public' AND x.relname=:name"""), {'name': INDEX}).all()
    if not present:
        check(not row, 'featured-media index already exists before revision 24')
        return
    expected_definition = (f'CREATE UNIQUE INDEX {INDEX} ON public.media USING btree '
                           '(band_id, song_id) WHERE (is_featured IS TRUE)')
    normalize = lambda value: ' '.join(str(value).split()).lower()
    expected_definitions = {normalize(expected_definition),
                            normalize(expected_definition.replace('ON public.media', 'ON media'))}
    check(len(row) == 1 and tuple(row[0][:12]) ==
          ('public', 'media', True, True, True, 2, 2, True, False, False, False, 'btree')
          and row[0][14] == ['band_id', 'song_id']
          and row[0][15] == [True, True]
          and row[0][16] == [0, 0]
          and normalize(row[0][12]) == '(is_featured is true)'
          and normalize(row[0][13]) in expected_definitions,
          'featured-media unique partial index definition differs')


def compare_preserved(prior, logo, ops, before, after, objects, rows, security, keys, columns, connection, raw):
    from migration_bootstrap import schema_contract_differences
    check(after['alembic_versions'] == [NEW] and len(after['tables']) == len(before['tables']),
          'target revision or table topology differs')
    compare_inventories(before, after, revision_transition=True)
    verify_object_transition(objects, public_object_manifest(connection))
    verify_featured_index(connection, present=True)
    check(prior.original_row_digests(raw, columns) == rows, 'application row data changed')
    check(after['owners'] == before['owners'], 'existing object owner changed')
    check(prior.security_fingerprint(logo, connection) == security,
          'ACL, RLS, default grants, role graph, or schema ACL changed')
    check(not schema_contract_differences(connection), 'revision 24 schema contract differs')
    check(logo.key_manifest(connection) == keys, 'R2 object-key manifest changed')
    ops.verify_sequences_not_behind(raw, after)


def apply_exact(prior, logo, ops, revision, connection, before, objects, rows, security, keys, columns):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from migration_bootstrap import metadata_for_recorded_revision, schema_differences
    prior._bound_timeouts(connection)
    connection.exec_driver_sql('SET LOCAL search_path=public,pg_catalog')
    names = ','.join('public.' + logo.q(name) for name in sorted(before['tables']))
    connection.exec_driver_sql('LOCK TABLE ' + names + ' IN SHARE MODE')
    identity = connection.execute(text("""SELECT current_user,current_database(),
        current_setting('server_version_num')::integer,current_setting('transaction_read_only'),
        current_setting('TimeZone')""")).one()
    check(tuple(identity[:2]) == ('postgres', 'postgres') and int(identity[2]) // 10000 == 17
          and tuple(identity[3:]) == ('off', 'UTC'), 'write connection identity differs')
    check(connection.execute(text('SELECT version_num FROM public.alembic_version FOR UPDATE'))
          .scalars().all() == [OLD], 'exact source revision 23 required')
    check(not schema_differences(connection, metadata=metadata_for_recorded_revision(OLD)),
          'source revision 23 schema differs')
    fresh = ops.prior.public_inventory(connection.connection.driver_connection)
    compare_inventories(before, fresh, revision_transition=False)
    check(ops.comparable_schema_and_rows(fresh) == ops.comparable_schema_and_rows(before),
          'current rows or schema differ from backup')
    check(public_object_manifest(connection) == objects, 'current FK, index, or trigger definitions differ')
    verify_featured_index(connection, present=False)
    check(prior.security_fingerprint(logo, connection) == security and logo.key_manifest(connection) == keys,
          'current security or object keys differ from backup')
    check(prior.original_row_digests(connection.connection.driver_connection, columns) == rows,
          'current row digest differs from backup')
    with Operations.context(MigrationContext.configure(connection)):
        revision.upgrade()
    changed = connection.execute(text('UPDATE public.alembic_version SET version_num=:new WHERE version_num=:old'),
                                 {'new': NEW, 'old': OLD})
    check(changed.rowcount == 1, 'revision transition failed')
    after = ops.prior.public_inventory(connection.connection.driver_connection)
    compare_preserved(prior, logo, ops, before, after, objects, rows, security, keys, columns, connection,
                      connection.connection.driver_connection)
    return after


def rehearse(args, prior, logo, ops, revision, work, report):
    params = prior.source_parameters(ops)
    pg_bin = STATE / 'postgresql117/pgsql/bin'
    report['pg_versions'] = ops.verify_pg17(pg_bin)
    source = server = port = None
    try:
        source = ops.source_snapshot(params)
        engine, connection, transaction, snapshot, captured, _identity, before, _acl = source
        columns = original_columns(connection, ops.prior.TABLES)
        raw = connection.connection.driver_connection
        rows = prior.original_row_digests(raw, columns)
        objects = public_object_manifest(connection)
        verify_featured_index(connection, present=False)
        security, keys = prior.security_fingerprint(logo, connection), logo.key_manifest(connection)
        roles = [[row[0], row[2]] for row in security['roles']]
        report.update(captured_at_utc=captured.isoformat(), before=ops.concise_inventory(before))
        (work / 'manifest.json').write_text(json.dumps({'inventory': before, 'security': security,
            'keys': keys, 'roles': roles, 'objects': objects,
            'original_columns': columns, 'original_rows': rows},
            sort_keys=True, default=str), encoding='utf-8')
        dump = work / 'production-public-v23.dump'
        result = ops.run([str(pg_bin / 'pg_dump.exe'), '--format=custom', '--no-password', '--schema=public',
            '--strict-names', '--lock-wait-timeout=5s', f'--snapshot={snapshot}', '--file', str(dump)],
            env=ops.source_environment(params), timeout=600)
        check(result.returncode == 0, 'public snapshot backup failed')
        report['public_dump_sha256'] = digest(dump)
        listed = ops.run([str(pg_bin / 'pg_restore.exe'), '--list', str(dump)],
                         env=ops.clean_environment(), timeout=60)
        check(listed.returncode == 0 and b' public ' in listed.stdout, 'public archive list invalid')
        (work / 'archive-list.txt').write_bytes(listed.stdout)
        transaction.rollback(); connection.close(); engine.dispose(); source = None
        data = work / 'local-pg17-data'
        result = ops.run_quiet([str(pg_bin / 'initdb.exe'), '--no-locale', '--encoding=UTF8',
            '--username=postgres', '--auth=trust', '--pgdata', str(data)],
            env=ops.clean_environment(), timeout=300)
        check(result.returncode == 0, 'owned local PostgreSQL initialization failed')
        port = ops.free_port()
        with (work / 'local-pg.log').open('ab') as log:
            server = subprocess.Popen([str(pg_bin / 'postgres.exe'), '-D', str(data), '-h', '127.0.0.1',
                '-p', str(port), '-c', 'timezone=UTC', '-c', 'max_connections=10', '-c', 'shared_buffers=16MB'],
                env=ops.local_environment(port), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and server.poll() is None:
            try:
                with ops.local_connect(port) as local_raw:
                    local_raw.execute('SELECT 1')
                break
            except ops.psycopg.OperationalError:
                time.sleep(.15)
        ops.verify_owned_cluster(server, port, data)
        with ops.local_connect(port) as local_raw:
            ops.drop_empty_default_public_schema(local_raw)
            logo.replay_roles_and_defaults(local_raw, roles, security['logo'])
        restored = ops.run([str(pg_bin / 'pg_restore.exe'), '--exit-on-error', '--no-password',
            '--host=127.0.0.1', f'--port={port}', '--username=postgres', '--dbname=postgres', str(dump)],
            env=ops.local_environment(port), timeout=600)
        check(restored.returncode == 0, 'faithful public backup restore failed')
        ops.verify_owned_cluster(server, port, data)
        with ops.local_connect(port) as local_raw:
            prior.replay_missing_public_schema_acl(ops, local_raw, server, port, data,
                                                    security['public_schema_acl'])
        app = ops.create_migration_app(f'postgresql+psycopg://postgres@127.0.0.1:{port}/postgres?sslmode=disable')
        with app.app_context():
            try:
                with ops.db.engine.connect() as local:
                    observed = ops.prior.public_inventory(local.connection.driver_connection)
                    compare_inventories(before, observed, revision_transition=False)
                    check(ops.comparable_schema_and_rows(observed) == ops.comparable_schema_and_rows(before),
                          'restored rows or schema differ from snapshot')
                    check(public_object_manifest(local) == objects,
                          'restored FK, index, or trigger definitions differ')
                    local_security = prior.security_fingerprint(logo, local)
                    check(prior.local_security_matches_source(security, local_security)
                          and logo.key_manifest(local) == keys,
                          'restored ACL, RLS, schema ACL, defaults, or keys differ')
                    check(prior.original_row_digests(local.connection.driver_connection, columns) == rows,
                          'restored row digests differ')
                    verify_featured_index(local, present=False)
                    ops.verify_sequences_not_behind(local.connection.driver_connection, observed)
                with ops.db.engine.begin() as local:
                    after = apply_exact(prior, logo, ops, revision, local, observed, objects, rows,
                                        local_security, keys, columns)
                report.update(after=ops.concise_inventory(after), local_migration_verified=True,
                    restore_verified=True, local_security_replay_verified=True,
                    original_rows_security_keys_preserved=True, local_role_attribute_copy=False)
            finally:
                ops.db.session.remove(); ops.db.engine.dispose()
    finally:
        if source is not None:
            engine, connection, transaction, *_ = source
            transaction.rollback(); connection.close(); engine.dispose()
        if server is not None:
            report['local_stop'] = ops.stop_owned_process(server, pg_bin / 'pg_ctl.exe', port,
                work / 'local-pg17-data', ops.local_environment(port))
            check(server.poll() is not None, 'owned local PostgreSQL server did not exit')


def reviewed_evidence(args):
    candidate = Path(args.evidence_directory or '')
    check(candidate.parent.resolve() == STATE.resolve() and not candidate.is_symlink(),
          'evidence must be a private direct state child')
    folder = candidate.resolve()
    check(folder.parent == STATE.resolve(), 'evidence must be a private direct state child')
    pins = {'report.json': args.report_sha256, 'manifest.json': args.manifest_sha256,
            'production-public-v23.dump': args.dump_sha256}
    for name, pin in pins.items():
        path = folder / name
        check(path.is_file() and not path.is_symlink() and digest(path) == pin,
              'reviewed rehearsal evidence pin differs')
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    check(report.get('status') == 'verified' and report.get('mode') == 'rehearse'
          and report.get('remote_writes') is False, 'verified read-only rehearsal required')
    check(report.get('source_commit') == args.source_commit
          and report.get('migration_sha256') == args.migration_sha256
          and report.get('helper_sha256') == args.approved_helper_sha256,
          'rehearsal source pins differ')
    check(all(report.get(key) is True for key in ('restore_verified', 'local_migration_verified',
          'local_security_replay_verified', 'original_rows_security_keys_preserved'))
          and report.get('local_stop', {}).get('process_exited') is True,
          'local rehearsal verification is incomplete')
    check(report.get('before', {}).get('revision') == OLD
          and report.get('after', {}).get('revision') == NEW,
          'rehearsal revision topology differs')
    check(report.get('public_dump_sha256') == args.dump_sha256, 'backup provenance differs')
    captured = datetime.fromisoformat(report['captured_at_utc'])
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    check(0 <= (datetime.now(timezone.utc) - captured).total_seconds() <= 86400,
          'rehearsal is older than 24 hours')
    return manifest


def apply_reviewed(args, prior, logo, ops, revision, report):
    check(args.operator_go == GO, 'separately reviewed production GO required')
    manifest = reviewed_evidence(args)
    tables = set(ops.prior.TABLES)
    check(set(manifest['inventory']['tables']) == tables
          and set(manifest['original_columns']) == tables
          and set(manifest['original_rows']) == tables - {'alembic_version'}
          and set(manifest['objects']) == {'foreign_keys', 'indexes', 'triggers'}
          and not any(len(row) > 1 and row[0] == 'media' and row[1] == INDEX
                      for row in manifest['objects']['indexes']),
          'rehearsal table or column manifest differs')
    params = prior.source_parameters(ops)
    from sqlalchemy import create_engine
    from migration_bootstrap import schema_contract_differences

    def writer():
        with ops.isolated_local_libpq_environment():
            return ops.psycopg.connect(**params, connect_timeout=5, autocommit=False,
                options='-c default_transaction_read_only=off -c timezone=UTC -c datestyle=ISO,YMD '
                        '-c intervalstyle=postgres -c extra_float_digits=3 -c bytea_output=hex '
                        '-c search_path=public,pg_catalog')

    app = ops.create_migration_app('sqlite:///:memory:')
    with app.app_context():
        try:
            engine = create_engine('postgresql+psycopg://', creator=writer, pool_pre_ping=False)
            try:
                with engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE')
                        after = apply_exact(prior, logo, ops, revision, connection,
                            manifest['inventory'], manifest['objects'], manifest['original_rows'], manifest['security'],
                            manifest['keys'], manifest['original_columns'])
                        report['commit_started'] = True
                        transaction.commit()
                        report['committed'] = True
                    except Exception:
                        if transaction.is_active:
                            transaction.rollback()
                        raise
            finally:
                engine.dispose()
            readonly = create_engine('postgresql+psycopg://',
                creator=lambda: ops.prior.remote_connection(params), pool_pre_ping=False)
            try:
                with readonly.connect() as fresh:
                    transaction = fresh.begin()
                    fresh.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    observed = ops.prior.public_inventory(fresh.connection.driver_connection)
                    compare_preserved(prior, logo, ops, manifest['inventory'], observed,
                        manifest['objects'], manifest['original_rows'], manifest['security'], manifest['keys'],
                        manifest['original_columns'], fresh, fresh.connection.driver_connection)
                    check(not schema_contract_differences(fresh), 'fresh target schema contract differs')
                    transaction.commit()
                    report.update(fresh_readonly_postcheck=True, after=ops.concise_inventory(after))
            finally:
                readonly.dispose()
        finally:
            ops.db.session.remove()
            ops.db.engine.dispose()


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
        print('FEATURED_MEDIA_READY_NO_ACTION')
        return 0
    prior, logo, ops, revision = primitives(args)
    work = ops.secure_private_directory(STATE / ('featured-media23-24-' +
        time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:12]))
    report = {'status': 'started', 'mode': 'apply' if args.apply else 'rehearse',
        'remote_writes': bool(args.apply), 'committed': False,
        'source_commit': args.source_commit, 'migration_sha256': args.migration_sha256,
        'helper_sha256': args.approved_helper_sha256, 'backup_scope': 'public application schema only; managed Auth excluded'}
    try:
        if args.apply:
            apply_reviewed(args, prior, logo, ops, revision, report)
        else:
            rehearse(args, prior, logo, ops, revision, work, report)
        report['status'] = 'verified'
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__,
            error_functions=[frame.name for frame in traceback.extract_tb(error.__traceback__)])
        if type(error) is GateError:
            report['failed_gate'] = str(error)
        if report.get('commit_started') and report.get('committed') is not True:
            report['committed'] = 'unknown'
    (work / 'report.json').write_text(json.dumps(report, sort_keys=True, indent=2), encoding='utf-8')
    print('FEATURED_MEDIA_' + report['status'].upper() + ' ' + str(work / 'report.json'))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('FEATURED_MEDIA_STOPPED ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
