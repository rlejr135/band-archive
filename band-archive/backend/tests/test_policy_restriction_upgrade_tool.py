"""Pure local safety gates; no provider connection or archive is opened."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def tool():
    path = Path(__file__).parents[1] / 'tools/policy_restriction_upgrade.py'
    spec = importlib.util.spec_from_file_location('policy_restriction_upgrade_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_invocation_cannot_load_operators_or_connect(tool, monkeypatch, capsys):
    def forbidden(*args):
        raise AssertionError('Operator loading must require explicit execution.')
    monkeypatch.setattr(tool, 'primitives', forbidden)
    assert tool.main([]) == 0
    assert capsys.readouterr().out.strip() == 'POLICY_RESTRICTION_READY_NO_ACTION'


def test_apply_and_rehearsal_flags_are_mutually_exclusive(tool):
    with pytest.raises(SystemExit) as error:
        tool.main(['--apply', '--rehearse'])
    assert error.value.code == 2


def test_missing_process_carrier_never_reads_dotenv(tool, monkeypatch):
    monkeypatch.delenv('REHEARSAL_TARGET_URL', raising=False)
    with pytest.raises(tool.GateError, match='protected process'):
        tool.source_parameters(None)


def test_target_is_pinned_and_only_password_is_taken_from_carrier(tool, monkeypatch):
    monkeypatch.setenv('REHEARSAL_TARGET_URL', 'postgresql://ignored:synthetic-test-only@ignored.invalid:9999/ignored')
    monkeypatch.setattr(tool, 'digest', lambda path: tool.CA_SHA)
    params = tool.source_parameters(None)
    assert (params['host'], params['port'], params['user'], params['dbname']) == (
        'aws-0-ap-northeast-1.pooler.supabase.com', '5432', 'postgres.qrgmgglqssvpctpyixsw', 'postgres')
    assert params['sslmode'] == 'verify-full'
    assert params['password'] == 'synthetic-test-only'


def test_apply_requires_separate_go_before_evidence_or_connection(tool):
    with pytest.raises(tool.GateError, match='production GO'):
        tool.apply_reviewed(SimpleNamespace(operator_go=None), None, None, None, None, {})


def test_evidence_outside_private_state_is_rejected_before_open(tool, tmp_path):
    with pytest.raises(tool.GateError, match='private direct state child'):
        tool.evidence(SimpleNamespace(evidence_directory=str(tmp_path)))


def test_preservation_projection_removes_only_new_table_and_new_audit_index(tool):
    before = {'catalog': {'columns': [('song', 'id')], 'constraints': [('song', 'pk')],
        'triggers': [('song', 'trigger')], 'indexes': [('song', 'old_index')],
        'functions': ['audit_guard'], 'sequences': [('public', 'song_id_seq', 42)],
        'sequence_dependencies': [('song_id_seq', 'owner', 'public', 'song')]}}
    after = {'catalog': {**before['catalog'],
        'columns': [*before['catalog']['columns'], (tool.ADDED, 'id')],
        'indexes': [*before['catalog']['indexes'], ('audit_event', tool.ADDED_INDEX), (tool.ADDED, 'new_pk')],
        'sequences': [*before['catalog']['sequences'], ('public', tool.ADDED_SEQ, 1)],
        'sequence_dependencies': [*before['catalog']['sequence_dependencies'], (tool.ADDED_SEQ, 'owner', 'public', tool.ADDED)]}}
    assert tool.old_catalog(before) == tool.old_catalog(after)
    after['catalog']['sequences'][0] = ('public', 'song_id_seq', 43)
    assert tool.old_catalog(before) != tool.old_catalog(after)
