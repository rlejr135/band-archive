"""No-network contracts for the revision 23 operator gate."""
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, text

from tests.test_data_api_security_migration import private_pg17  # noqa: F401

TOOL = Path(__file__).parents[1] / 'tools' / 'admission_event_upgrade.py'
SPEC = importlib.util.spec_from_file_location('admission_event_upgrade_tested', TOOL)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)


def test_default_invocation_is_inert(monkeypatch, capsys):
    monkeypatch.setattr(operator, 'source_parameters', lambda _ops: (_ for _ in ()).throw(AssertionError()))
    assert operator.main([]) == 0
    assert capsys.readouterr().out.strip() == 'ADMISSION_EVENT_READY_NO_ACTION'


def test_source_topology_includes_ledger_and_all_37_application_tables():
    class Metadata:
        tables = {f'table_{index:02}' for index in range(37)}

    result = operator.source_public_tables(Metadata())
    assert len(result) == 38
    assert 'alembic_version' in result


def test_catalog_comparison_masks_only_new_columns_and_check():
    before = {'catalog': {
        'columns': [['band_membership', 'id', 'bigint'], ['rehearsal', 'title', 'text']],
        'constraints': [['rehearsal', 'f', 'fk_rehearsal_band', 'band', 'id'],
                        ['band_membership', 'p', 'pk_membership', 'id']],
        'indexes': [['rehearsal', 'ix_rehearsal_band']],
        'triggers': [['rehearsal', 'notify_rehearsal']],
        'functions': [['notify_rehearsal', 'body']],
        'sequences': [['rehearsal_id_seq', 'state']],
        'sequence_dependencies': [['rehearsal_id_seq', 'id', 'owned', 'rehearsal']],
    }}
    after = {'catalog': {key: list(rows) for key, rows in before['catalog'].items()}}
    after['catalog']['columns'] += [
        ['band_membership', 'requested_positions_json', 'text'],
        ['band_membership', 'requested_custom_position', 'varchar(50)'],
        ['rehearsal', 'rehearsal_type', 'varchar(20)'],
    ]
    after['catalog']['constraints'].append(
        ['rehearsal', 'c', 'ck_rehearsal_type', "rehearsal_type IN (...)"])
    assert operator.old_catalog(before) == operator.old_catalog(after)
    after['catalog']['indexes'].append(['rehearsal', 'unexpected_index'])
    assert operator.old_catalog(before) != operator.old_catalog(after)


def test_row_digest_hashes_each_sorted_projected_row():
    class Cursor:
        def __init__(self):
            self.rows = [('{"id": 1}',), ('{"id": 2}',)]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement):
            assert 'requested_positions_json' not in statement
            assert 'ORDER BY to_jsonb(source_row)::text COLLATE "C"' in statement

        def fetchmany(self, size):
            rows, self.rows = self.rows[:size], self.rows[size:]
            return rows

    class Raw:
        def execute(self, _statement):
            return None

        def cursor(self, name=None):
            assert name == 'admission_event_preserve_band_membership'
            return Cursor()

    value = operator.original_row_digests(Raw(), {
        'alembic_version': ['version_num'], 'band_membership': ['id']})
    assert 'alembic_version' not in value
    assert value['band_membership']['rows'] == 2
    assert len(value['band_membership']['sha256']) == 64


def test_connection_carrier_error_does_not_echo_value(monkeypatch):
    sentinel = 'private-password-must-not-appear'
    monkeypatch.setenv('REHEARSAL_TARGET_URL', sentinel)
    try:
        operator.source_parameters(None)
    except operator.GateError as error:
        assert sentinel not in str(error)
    else:
        raise AssertionError('malformed connection carrier unexpectedly accepted')


def test_local_security_projection_uses_replayed_security_not_remote_role_flags():
    source = {'logo': {'objects': [['r', 'table', 'owner']], 'memberships': [['anon', 'authenticated']]},
              'public_schema_acl': [['public', 'owner', 'acl']],
              'roles': [['authenticated', False, True]], 'memberships': [['role_a', 'role_b']]}
    local = {**source, 'roles': [['authenticated', False, False]],
             'memberships': [['local_only_builtin', 'postgres']]}
    assert operator.local_security_matches_source(source, local)
    local = {**local, 'public_schema_acl': [['public', 'different-owner', 'acl']]}
    assert not operator.local_security_matches_source(source, local)


def test_postgres_digest_projection_is_stable_and_ignores_only_new_columns(private_pg17):
    engine = create_engine(private_pg17)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE public.band_membership '
                '(id integer PRIMARY KEY, display_name text NOT NULL, requested_positions_json text, '
                'requested_custom_position varchar(50))')
            connection.exec_driver_sql('CREATE TABLE public.rehearsal '
                '(id integer PRIMARY KEY, title text NOT NULL, rehearsal_type varchar(20) '
                "NOT NULL DEFAULT 'UNCLASSIFIED')")
            connection.exec_driver_sql('CREATE TABLE public.alembic_version (version_num text NOT NULL)')
            connection.exec_driver_sql("INSERT INTO public.band_membership VALUES "
                "(2,'가나다',NULL,NULL),(1,'Ångström',NULL,NULL)")
            connection.exec_driver_sql("INSERT INTO public.rehearsal VALUES "
                "(2,'두 번째','UNCLASSIFIED'),(1,'第一','UNCLASSIFIED')")
            connection.exec_driver_sql("INSERT INTO public.alembic_version VALUES ('20261021_22')")
            raw = connection.connection.driver_connection
            projection = {'band_membership': ['id', 'display_name'],
                          'rehearsal': ['id', 'title'],
                          'alembic_version': ['version_num']}
            before = operator.original_row_digests(raw, projection)
            connection.exec_driver_sql("UPDATE public.band_membership SET requested_positions_json='[]' "
                'WHERE id=1')
            connection.exec_driver_sql("UPDATE public.rehearsal SET rehearsal_type='LIVE' WHERE id=1")
            after = operator.original_row_digests(raw, projection)
            assert before == after
            assert set(before) == {'band_membership', 'rehearsal'}
            assert before['band_membership']['rows'] == 2
            assert before['rehearsal']['rows'] == 2
    finally:
        engine.dispose()


def test_repeatable_read_snapshot_is_deferred_until_after_write_barrier(private_pg17):
    engine = create_engine(private_pg17)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE public.snapshot_barrier_probe (value text NOT NULL)')
            connection.exec_driver_sql("INSERT INTO public.snapshot_barrier_probe VALUES ('before')")
        with engine.connect() as reader:
            with reader.begin():
                reader.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE')
                operator._bound_timeouts(reader)
                with engine.begin() as writer:
                    writer.exec_driver_sql("UPDATE public.snapshot_barrier_probe SET value='after'")
                reader.exec_driver_sql('LOCK TABLE public.snapshot_barrier_probe IN SHARE MODE')
                assert reader.execute(text('SELECT value FROM public.snapshot_barrier_probe')).scalar_one() == 'after'
    finally:
        engine.dispose()


def test_apply_limits_are_bounded_and_local():
    source = TOOL.read_text(encoding='utf-8')
    assert "SET LOCAL {name} = '{maximum}ms'" in source
    assert "('lock_timeout', 5000), ('statement_timeout', 30000)" in source
    assert 'load_dotenv(' not in source
