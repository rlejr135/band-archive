"""No-network contracts for the revision 23 -> 24 operator gate."""
import importlib.util
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_data_api_security_migration import private_pg17  # noqa: F401

TOOL = Path(__file__).parents[1] / 'tools' / 'featured_media_upgrade.py'
SPEC = importlib.util.spec_from_file_location('featured_media_upgrade_tested', TOOL)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)


def test_default_invocation_is_inert(monkeypatch, capsys):
    monkeypatch.setattr(operator, 'primitives', lambda *_: (_ for _ in ()).throw(AssertionError()))
    assert operator.main([]) == 0
    assert capsys.readouterr().out.strip() == 'FEATURED_MEDIA_READY_NO_ACTION'


def test_inventory_normalization_allows_only_expected_ledger_and_object_delta():
    def make_inventory(revision, ledger_hash, indexes):
        catalog = {'columns': [['media', 'is_featured', 'boolean', False, '', '', None]],
            'constraints': [['media', 'f', 'fk_media_song', 'FOREIGN KEY (...)', True, False, False]],
            'functions': [], 'indexes': indexes, 'sequence_dependencies': [], 'sequences': [],
            'triggers': []}
        return {'alembic_versions': [revision], 'tables': {
            'alembic_version': {'rows': 1, 'sha256': ledger_hash},
            'media': {'rows': 8, 'sha256': 'media-rows'}}, 'catalog': catalog,
            'catalog_sha256': operator.stable_hash(catalog), 'owners': [['media', 'postgres']]}

    prior_index = ['media', 'ix_media_song', 'CREATE INDEX ix_media_song ON public.media USING btree (song_id)',
                   True, True]
    new_index = ['media', operator.INDEX,
                 'CREATE UNIQUE INDEX uq_media_featured_band_song ON public.media USING btree '
                 '(band_id, song_id) WHERE (is_featured IS TRUE)', True, True]
    before = make_inventory('20261023_23', 'ledger-v23', [prior_index])
    after = make_inventory('20261024_24', 'ledger-v24', [prior_index, new_index])
    operator.compare_inventories(before, after, revision_transition=True)
    before_objects = {'foreign_keys': [['media', 'fk_media_song', 'FOREIGN KEY (...)']],
        'indexes': [['media', 'ix_media_song', 'CREATE INDEX ...']],
        'triggers': [['media', 'audit_media', 'CREATE TRIGGER ...']]}
    after_objects = {'foreign_keys': list(before_objects['foreign_keys']),
        'indexes': [*before_objects['indexes'],
                    ['media', operator.INDEX, 'CREATE UNIQUE INDEX ... WHERE is_featured']],
        'triggers': list(before_objects['triggers'])}
    operator.verify_object_transition(before_objects, after_objects)

    bad = json.loads(json.dumps(after))
    bad['tables']['media']['sha256'] = 'changed-row-data'
    with pytest.raises(operator.GateError, match='inventory changed'):
        operator.compare_inventories(before, bad, revision_transition=True)
    bad = json.loads(json.dumps(after))
    bad['catalog']['constraints'].append(['media', 'unexpected_fk'])
    bad['catalog_sha256'] = operator.stable_hash(bad['catalog'])
    with pytest.raises(operator.GateError, match='inventory changed'):
        operator.compare_inventories(before, bad, revision_transition=True)
    bad = json.loads(json.dumps(after))
    bad['catalog_sha256'] = 'mismatched-catalog-checksum'
    with pytest.raises(operator.GateError, match='checksum'):
        operator.compare_inventories(before, bad, revision_transition=True)
    bad_objects = json.loads(json.dumps(after_objects))
    bad_objects['triggers'].append(['media', 'unexpected', 'CREATE TRIGGER ...'])
    with pytest.raises(operator.GateError, match='foreign keys, remaining indexes, or triggers'):
        operator.verify_object_transition(before_objects, bad_objects)


def test_index_verifier_requires_exact_partial_unique_index(private_pg17):
    from sqlalchemy import create_engine

    engine = create_engine(private_pg17)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE public.media '
                '(id integer PRIMARY KEY, band_id integer, song_id integer, is_featured boolean)')
            operator.verify_featured_index(connection, present=False)
            connection.exec_driver_sql(f'CREATE UNIQUE INDEX {operator.INDEX} ON public.media '
                '(band_id, song_id) WHERE is_featured IS TRUE')
            operator.verify_featured_index(connection, present=True)
            connection.exec_driver_sql(f'DROP INDEX public.{operator.INDEX}')
            connection.exec_driver_sql(f'CREATE UNIQUE INDEX {operator.INDEX} ON public.media '
                '(song_id, band_id) WHERE is_featured IS TRUE')
            with pytest.raises(operator.GateError, match='definition differs'):
                operator.verify_featured_index(connection, present=True)
            invalid_definitions = (
                '(band_id, song_id) INCLUDE (id) WHERE is_featured IS TRUE',
                '(band_id DESC, song_id) WHERE is_featured IS TRUE',
                '(band_id, song_id) NULLS NOT DISTINCT WHERE is_featured IS TRUE',
                '(band_id, song_id) WHERE is_featured',
            )
            for definition in invalid_definitions:
                connection.exec_driver_sql(f'DROP INDEX public.{operator.INDEX}')
                connection.exec_driver_sql(f'CREATE UNIQUE INDEX {operator.INDEX} ON public.media '
                                           + definition)
                with pytest.raises(operator.GateError, match='definition differs'):
                    operator.verify_featured_index(connection, present=True)
    finally:
        engine.dispose()


def test_pinned_public_inventory_compares_real_revision_transition(private_pg17):
    from sqlalchemy import create_engine

    inventory_helper = operator.load(
        TOOL.parents[1] / 'tools' / '.state' / 'backup_staging_v12_security13_rehearsal.py',
        '351fad0f1927fbd7aaf2effd6a359a4045885940ee7e0aea75fd0bfb40d18191',
        'featured_media_real_inventory_fixture')
    # Isolated imported module instance only; the persisted helper and pins stay untouched.
    inventory_helper.TABLES = ('alembic_version', 'media')
    engine = create_engine(private_pg17)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE public.media '
                '(id integer PRIMARY KEY, band_id integer, song_id integer, is_featured boolean)')
            connection.exec_driver_sql('CREATE TABLE public.alembic_version (version_num text NOT NULL)')
            connection.exec_driver_sql("INSERT INTO public.alembic_version VALUES ('20261023_23')")
            connection.exec_driver_sql('INSERT INTO public.media VALUES (1, 10, 20, true)')
            raw = connection.connection.driver_connection
            before = inventory_helper.public_inventory(raw)
            connection.exec_driver_sql(f'CREATE UNIQUE INDEX {operator.INDEX} ON public.media '
                '(band_id, song_id) WHERE is_featured IS TRUE')
            connection.exec_driver_sql("UPDATE public.alembic_version SET version_num='20261024_24'")
            after = inventory_helper.public_inventory(raw)
            operator.compare_inventories(before, after, revision_transition=True)

            tampered = json.loads(json.dumps(after))
            tampered['catalog']['columns'].append(['media', 'unexpected', 'integer', False, '', '', None])
            tampered['catalog_sha256'] = inventory_helper.stable_hash(tampered['catalog'])
            with pytest.raises(operator.GateError, match='inventory changed'):
                operator.compare_inventories(before, tampered, revision_transition=True)
    finally:
        engine.dispose()


def test_operator_is_separately_pinned_and_does_not_load_dotenv():
    source = TOOL.read_text(encoding='utf-8')
    assert operator.PRIOR_SHA == '1fcc97aa2dbe12d72d519d0a6cf7eeb3774c0c05a57066a72ef4bf0b2726b611'
    assert operator.PRIOR_MIGRATION_SHA == '749828924f7404d5a3f62f441994cbaa5e5b13dab605f0ae1aae2a97186bbc60'
    assert 'load_dotenv(' not in source
    with pytest.raises(operator.GateError, match='reviewed source pin differs'):
        operator.load(TOOL, '0' * 64, 'wrong_featured_media_pin')


def _valid_rehearsal_evidence(tmp_path, name='evidence'):
    folder = tmp_path / name
    folder.mkdir()
    report = {'status': 'verified', 'mode': 'rehearse', 'remote_writes': False,
        'source_commit': 'reviewed-commit', 'migration_sha256': 'migration-pin',
        'helper_sha256': 'helper-pin', 'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'before': {'revision': operator.OLD}, 'after': {'revision': operator.NEW},
        'restore_verified': True, 'local_migration_verified': True,
        'local_security_replay_verified': True, 'original_rows_security_keys_preserved': True,
        'local_stop': {'process_exited': True}, 'public_dump_sha256': None}
    manifest = {'inventory': {}, 'original_columns': {}, 'original_rows': {}}
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (folder / 'production-public-v23.dump').write_bytes(b'backup fixture')
    report['public_dump_sha256'] = operator.digest(folder / 'production-public-v23.dump')
    (folder / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    args = SimpleNamespace(evidence_directory=str(folder),
        report_sha256=operator.digest(folder / 'report.json'),
        manifest_sha256=operator.digest(folder / 'manifest.json'),
        dump_sha256=operator.digest(folder / 'production-public-v23.dump'),
        source_commit='reviewed-commit', migration_sha256='migration-pin',
        approved_helper_sha256='helper-pin')
    return folder, report, args


def test_apply_rejects_missing_or_wrong_gate_before_connection(monkeypatch, tmp_path, capsys):
    class Prior:
        @staticmethod
        def source_parameters(_ops):
            raise AssertionError('connection setup must not run before GO gate')

    class Ops:
        @staticmethod
        def secure_private_directory(path):
            path.mkdir()
            return path

    monkeypatch.setattr(operator, 'STATE', tmp_path)
    monkeypatch.setattr(operator, 'primitives', lambda _args: (Prior(), None, Ops(), None))
    assert operator.main(['--apply', '--operator-go', 'wrong', '--approved-helper-sha256', 'helper-pin',
        '--source-commit', 'commit-pin', '--migration-sha256', 'migration-pin']) == 1
    assert 'FEATURED_MEDIA_FAILED' in capsys.readouterr().out
    evidence = next(tmp_path.glob('featured-media23-24-*/report.json'))
    assert 'separately reviewed production GO required' in evidence.read_text(encoding='utf-8')


def test_rehearsal_evidence_requires_exact_hashes_and_fresh_source_pins(monkeypatch, tmp_path):
    monkeypatch.setattr(operator, 'STATE', tmp_path)
    folder, report, args = _valid_rehearsal_evidence(tmp_path)
    assert operator.reviewed_evidence(args)['inventory'] == {}

    args.report_sha256 = '0' * 64
    with pytest.raises(operator.GateError, match='evidence pin differs'):
        operator.reviewed_evidence(args)

    _, report, args = _valid_rehearsal_evidence(tmp_path, 'evidence-stale')
    report['captured_at_utc'] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    folder = Path(args.evidence_directory)
    (folder / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    args.report_sha256 = operator.digest(folder / 'report.json')
    with pytest.raises(operator.GateError, match='older than 24 hours'):
        operator.reviewed_evidence(args)

    _, report, args = _valid_rehearsal_evidence(tmp_path, 'evidence-source')
    args.source_commit = 'different-commit'
    with pytest.raises(operator.GateError, match='source pins differ'):
        operator.reviewed_evidence(args)
