"""Exact PostgreSQL reflection grammar for invite-specific CHECKs."""

from collections import Counter
import pytest

from sqlalchemy.dialects import postgresql

from migration_bootstrap import _expected_check_constraints, _reflected_check_constraints
from models import BandInviteLink, InviteResumeContext


CASES = (
    (BandInviteLink.__table__, 'ck_band_invite_link_expiry',
     '((expires_at) > (created_at))', 'expires_at >= created_at'),
    (InviteResumeContext.__table__, 'ck_invite_resume_context_expiry',
     '(expires_at > created_at)', 'expires_at >= created_at'),
    (InviteResumeContext.__table__, 'ck_invite_resume_context_claim_state',
     '((claimed_user_id IS NULL) = (claimed_at IS NULL))',
     '(claimed_user_id IS NULL) OR (claimed_at IS NULL)'),
)


class _Inspector:
    def __init__(self, name, sqltext):
        self.name = name
        self.sqltext = sqltext

    def get_check_constraints(self, table_name):
        return [{'name': self.name, 'sqltext': self.sqltext}]


def test_invite_postgres_checks_accept_only_exact_reviewed_expressions():
    for table, name, reflected_expression, changed_expression in CASES:
        expected = _expected_check_constraints(table, postgresql.dialect())
        expected_one = Counter({key: count for key, count in expected.items() if key[0] == name})
        assert sum(expected_one.values()) == 1
        reflected = _reflected_check_constraints(
            _Inspector(name, reflected_expression), table.name, postgresql.dialect(), table,
        )
        assert reflected == expected_one

        changed = _reflected_check_constraints(
            _Inspector(name, changed_expression), table.name, postgresql.dialect(), table,
        )
        assert changed != expected_one


def test_invite_postgres_check_name_scope_is_enforced():
    table, name, expression, _ = CASES[0]
    expected = _expected_check_constraints(table, postgresql.dialect())
    expected_one = Counter({key: count for key, count in expected.items() if key[0] == name})
    wrong_name = _reflected_check_constraints(
        _Inspector('ck_unreviewed_expiry', expression), table.name, postgresql.dialect(), table,
    )
    assert wrong_name != expected_one


@pytest.mark.parametrize('expression', [
    'expires_at() > created_at', '(expires_at)() > created_at',
    '"expires_at>created_at"', '"EXPIRES_AT" > created_at',
    'expires_at::timestamp > created_at', 'expires_at > created_at OR true',
    'expires_at >= created_at',
])
def test_invite_expiry_does_not_erase_sql_semantics(expression):
    from migration_bootstrap import _canonical_invite_postgres_check
    assert _canonical_invite_postgres_check(expression, 'band_invite_link',
                                           'ck_band_invite_link_expiry') is None


@pytest.mark.parametrize('expression', [
    '(claimed_user_id() IS NULL) = (claimed_at IS NULL)',
    '(claimed_user_id IS NULL) = (claimed_at() IS NULL)',
    '(claimed_user_id IS NULL) = (claimed_at IS NULL) OR true',
])
def test_invite_claim_does_not_erase_function_calls(expression):
    from migration_bootstrap import _canonical_invite_postgres_check
    assert _canonical_invite_postgres_check(expression, 'invite_resume_context',
                                           'ck_invite_resume_context_claim_state') is None


def test_invite_checks_allow_quoted_lowercase_columns_and_redundant_outer_parentheses():
    from migration_bootstrap import _canonical_invite_postgres_check
    assert _canonical_invite_postgres_check(
        '((("expires_at")) > (("created_at")))', 'band_invite_link',
        'ck_band_invite_link_expiry') == ('invite_check', 'ck_band_invite_link_expiry', 'expiry')
    assert _canonical_invite_postgres_check(
        '(((("claimed_at")) IS NULL) = ((("claimed_user_id")) IS NULL))',
        'invite_resume_context', 'ck_invite_resume_context_claim_state') == (
            'invite_check', 'ck_invite_resume_context_claim_state', 'claim_nullity_equivalence')
