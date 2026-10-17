"""Fail-closed PostgreSQL CHECK grammar for the durable deletion lease fence."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migration_bootstrap import (
    _canonical_postgres_check_constraint,
    _expected_check_constraints,
    _normalize_postgres_check_for_table,
    _reflected_check_constraints,
)
from models import R2DeletionTask


TABLE = 'r2_deletion_task'
CONSTRAINT = 'ck_r2_deletion_task_lease_state'
EXPECTED_SQL = (
    "(status = 'processing' AND lease_token IS NOT NULL AND "
    "lease_expires_at IS NOT NULL) OR "
    "(status != 'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)"
)


def _table():
    return sa.Table(
        TABLE, sa.MetaData(),
        sa.Column('status', sa.String(40)),
        sa.Column('lease_token', sa.String(64)),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint(EXPECTED_SQL, name=CONSTRAINT),
    )


def _canonical(value, *, allow_or=True, allow_not_equal=True):
    parsed = _canonical_postgres_check_constraint(
        value, allow_or=allow_or, allow_not_equal=allow_not_equal,
    )
    return _normalize_postgres_check_for_table(parsed, _table())


MANIFEST_SQL = (
    "(status = 'completed' AND object_keys IS NULL) OR "
    "(status != 'completed' AND object_keys IS NOT NULL)"
)


def _manifest_table():
    return sa.Table(
        TABLE, sa.MetaData(),
        sa.Column('status', sa.String(40)),
        sa.Column('object_keys', sa.JSON()),
        sa.CheckConstraint(MANIFEST_SQL, name='ck_r2_deletion_task_manifest_state'),
    )


def test_postgres_lease_state_accepts_only_equivalent_reflected_boolean_renderings():
    expected = _canonical(EXPECTED_SQL)
    reflected = (
        "((status)::text = 'processing'::text AND lease_token IS NOT NULL AND "
        "lease_expires_at IS NOT NULL) OR "
        "((status)::text <> 'processing'::text AND lease_token IS NULL AND "
        "lease_expires_at IS NULL)"
    )
    alternate_not_equal = EXPECTED_SQL.replace("status != 'processing'", "status <> 'processing'")
    assert expected is not None
    assert _expected_check_constraints(_table(), postgresql.dialect())
    assert _canonical(reflected) == expected
    assert _canonical(alternate_not_equal) == expected


def test_postgres_lease_state_rejects_constraint_name_and_table_scope_bypass():
    expected = _canonical(EXPECTED_SQL)

    class Inspector:
        def __init__(self, name, expression):
            self.name = name
            self.expression = expression

        def get_check_constraints(self, table_name):
            assert table_name == TABLE
            return [{'name': self.name, 'sqltext': self.expression}]

    reflected = _reflected_check_constraints(
        Inspector('ck_other_table_lease_state', EXPECTED_SQL),
        TABLE, postgresql.dialect(), _table(),
    )
    assert reflected != { (CONSTRAINT, expected): 1 }
    assert _canonical_postgres_check_constraint(EXPECTED_SQL, allow_or=True) is None


def test_postgres_lease_state_rejects_semantic_drift_and_unrecognized_sql():
    expected = _canonical(EXPECTED_SQL)
    drifted = (
        "(status = 'processing' AND lease_token IS NOT NULL) OR "
        "(status <> 'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)"
    )
    wrong_state = EXPECTED_SQL.replace("status != 'processing'", "status != 'queued'")
    nested = EXPECTED_SQL.replace("status != 'processing'", "status != lower('processing')")
    assert _canonical(drifted) != expected
    assert _canonical(wrong_state) != expected
    assert _canonical(nested) is None

    class Inspector:
        def get_check_constraints(self, table_name):
            return [{'name': CONSTRAINT, 'sqltext': wrong_state}]

    assert _reflected_check_constraints(
        Inspector(), TABLE, postgresql.dialect(), _table(),
    ) != {(CONSTRAINT, expected): 1}


def test_postgres_manifest_state_accepts_only_exact_reflected_boolean_rendering():
    expected = _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint(MANIFEST_SQL, allow_or=True, allow_not_equal=True),
        _manifest_table(),
    )
    reflected = (
        "((status)::text = 'completed'::text AND object_keys IS NULL) OR "
        "((status)::text <> 'completed'::text AND object_keys IS NOT NULL)"
    )
    alternate_not_equal = MANIFEST_SQL.replace("status != 'completed'", "status <> 'completed'")
    assert expected is not None
    assert _expected_check_constraints(_manifest_table(), postgresql.dialect())
    assert _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint(reflected, allow_or=True, allow_not_equal=True),
        _manifest_table(),
    ) == expected
    assert _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint(alternate_not_equal, allow_or=True, allow_not_equal=True),
        _manifest_table(),
    ) == expected


def test_postgres_manifest_state_rejects_name_scope_or_semantic_drift():
    table = _manifest_table()
    expected = _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint(MANIFEST_SQL, allow_or=True, allow_not_equal=True), table,
    )
    weakened = MANIFEST_SQL.replace('object_keys IS NOT NULL', 'object_keys IS NULL')
    changed_status = MANIFEST_SQL.replace("status != 'completed'", "status != 'queued'")

    class Inspector:
        def __init__(self, name, expression):
            self.name = name
            self.expression = expression

        def get_check_constraints(self, table_name):
            return [{'name': self.name, 'sqltext': self.expression}]

    wrong_name = _reflected_check_constraints(
        Inspector('ck_some_other_check', MANIFEST_SQL), TABLE, postgresql.dialect(), table,
    )
    assert wrong_name != {('ck_r2_deletion_task_manifest_state', expected): 1}
    assert _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint(weakened, allow_or=True, allow_not_equal=True), table,
    ) != expected
    assert _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint(changed_status, allow_or=True, allow_not_equal=True), table,
    ) != expected
    assert _canonical_postgres_check_constraint(MANIFEST_SQL, allow_or=True) is None


def test_postgres_deletion_task_all_check_constraints_are_inside_the_audited_grammar():
    assert _expected_check_constraints(R2DeletionTask.__table__, postgresql.dialect())


def test_postgres_attempt_count_greater_equal_is_allowlisted_and_exact():
    name = 'ck_r2_deletion_task_attempt_count'
    table = sa.Table(
        TABLE, sa.MetaData(), sa.Column('attempt_count', sa.Integer()),
        sa.CheckConstraint('attempt_count >= 0', name=name),
    )
    expected = _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint('attempt_count >= 0', allow_greater_equal=True), table,
    )
    reflected = _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint('((attempt_count)::integer >= 0)', allow_greater_equal=True), table,
    )
    assert expected is not None and reflected == expected
    for changed in ('attempt_count >= 1', 'attempt_count >= -1', 'other_count >= 0',
                    'attempt_count > -1'):
        parsed = _normalize_postgres_check_for_table(
            _canonical_postgres_check_constraint(changed, allow_greater_equal=True), table,
        )
        assert parsed != expected
    assert _canonical_postgres_check_constraint('attempt_count >= 0') is None
