"""Local-only operator safety tests; no application/provider initialization."""
import pytest
import sqlalchemy as sa

from tools.member_profile_backfill import ProfileRepairError, main, repair_profiles


@pytest.fixture
def repair_db():
    engine = sa.create_engine('sqlite://')
    metadata = sa.MetaData()
    tables = {}
    tables['band'] = sa.Table('band', metadata, sa.Column('id', sa.Integer, primary_key=True),
                              sa.Column('deleted_at', sa.DateTime))
    tables['app_user'] = sa.Table('app_user', metadata, sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('display_name', sa.String), sa.Column('email_verified', sa.Boolean), sa.Column('status', sa.String))
    tables['band_membership'] = sa.Table('band_membership', metadata,
        sa.Column('id', sa.Integer, primary_key=True), sa.Column('band_id', sa.Integer),
        sa.Column('user_id', sa.Integer), sa.Column('status', sa.String))
    tables['member'] = sa.Table('member', metadata, sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('band_id', sa.Integer), sa.Column('linked_user_id', sa.Integer),
        sa.Column('name', sa.String), sa.Column('instrument', sa.String), sa.Column('biography', sa.Text),
        sa.Column('positions_json', sa.Text), sa.Column('custom_position', sa.String),
        sa.Column('profile_revision', sa.Integer), sa.Column('created_at', sa.DateTime))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(tables['band'].insert(), [{'id': 1}, {'id': 2}])
        connection.execute(tables['app_user'].insert(), [
            {'id': 1, 'display_name': 'Same name', 'email_verified': True, 'status': 'active'},
            {'id': 2, 'display_name': None, 'email_verified': True, 'status': 'active'},
            {'id': 3, 'display_name': 'Other band', 'email_verified': True, 'status': 'active'}])
        connection.execute(tables['band_membership'].insert(), [
            {'id': 1, 'band_id': 1, 'user_id': 1, 'status': 'active'},
            {'id': 2, 'band_id': 1, 'user_id': 2, 'status': 'active'},
            {'id': 3, 'band_id': 2, 'user_id': 3, 'status': 'active'}])
        connection.execute(tables['member'].insert(), [
            {'id': 10, 'band_id': 1, 'name': 'Same name', 'instrument': 'old guitar'},
            {'id': 20, 'band_id': 2, 'name': 'Other band', 'instrument': 'bass'}])
    yield engine, tables
    engine.dispose()


def test_default_dry_run_does_not_guess_name_or_write(repair_db):
    engine, tables = repair_db
    with engine.begin() as connection:
        report = repair_profiles(connection, band_id=1)
        assert report['missing_user_ids'] == [1, 2]
        assert report['unlinked_legacy_member_ids'] == [10]
        assert connection.scalar(sa.select(sa.func.count()).select_from(tables['member'])) == 2


def test_apply_then_repeat_is_idempotent_and_preserves_legacy(repair_db):
    engine, tables = repair_db
    member = tables['member']
    with engine.begin() as connection:
        report = repair_profiles(connection, band_id=1, apply=True)
        assert len(report['created_member_ids']) == 2
        assert repair_profiles(connection, band_id=1, apply=True)['created_member_ids'] == []
        legacy = connection.execute(sa.select(member).where(member.c.id == 10)).mappings().one()
        assert legacy['linked_user_id'] is None and legacy['instrument'] == 'old guitar'
        new = connection.execute(sa.select(member).where(member.c.linked_user_id == 2)).mappings().one()
        assert new['name'] == '멤버' and new['profile_revision'] == 0
        assert new['biography'] == '' and new['positions_json'] == '[]'
        assert connection.scalar(sa.select(sa.func.count()).select_from(member).where(member.c.band_id == 2)) == 1


def test_exact_mapping_preserves_profile_id_and_instrument(repair_db):
    engine, tables = repair_db
    mapping = [{'member_id': 10, 'user_id': 1}]
    with engine.begin() as connection:
        report = repair_profiles(connection, band_id=1, mappings=mapping, apply=True)
        assert len(report['created_member_ids']) == 1
        row = connection.execute(sa.select(tables['member']).where(tables['member'].c.id == 10)).mappings().one()
        assert row['linked_user_id'] == 1 and row['instrument'] == 'old guitar'
        assert repair_profiles(connection, band_id=1, mappings=mapping, apply=True)['created_member_ids'] == []


@pytest.mark.parametrize('mapping', [
    [{'member_id': 20, 'user_id': 1}], [{'member_id': 10, 'user_id': 3}],
    [{'member_id': 10, 'user_id': True}], [{'member_id': 10, 'user_id': 1, 'name': 'Same name'}],
    [{'member_id': 10, 'user_id': 1}, {'member_id': 10, 'user_id': 2}],
])
def test_invalid_or_cross_band_mapping_fails_closed(repair_db, mapping):
    engine, _ = repair_db
    with engine.begin() as connection:
        with pytest.raises(ProfileRepairError):
            repair_profiles(connection, band_id=1, mappings=mapping, apply=True)


def test_existing_duplicate_is_rejected_before_any_writes(repair_db):
    engine, tables = repair_db
    with engine.begin() as connection:
        connection.execute(tables['member'].insert(), [
            {'band_id': 1, 'linked_user_id': 1}, {'band_id': 1, 'linked_user_id': 1}])
        with pytest.raises(ProfileRepairError, match='Duplicate linked'):
            repair_profiles(connection, band_id=1, apply=True)


def test_no_reassignment_and_transaction_caller_controls_rollback(repair_db):
    engine, tables = repair_db
    with engine.connect() as connection:
        transaction = connection.begin()
        repair_profiles(connection, band_id=1, mappings=[{'member_id': 10, 'user_id': 1}], apply=True)
        with pytest.raises(ProfileRepairError):
            repair_profiles(connection, band_id=1, mappings=[{'member_id': 10, 'user_id': 2}], apply=True)
        transaction.rollback()
        assert connection.scalar(sa.select(sa.func.count()).select_from(tables['member'])) == 2
        assert connection.scalar(sa.select(tables['member'].c.linked_user_id).where(tables['member'].c.id == 10)) is None


def test_cli_redacts_connection_error(monkeypatch, capsys):
    monkeypatch.setenv('MEMBER_PROFILE_DATABASE_URL', 'invalid://secret-user:private-password@private-host/db')
    assert main(['--band-id', '1']) == 1
    output = capsys.readouterr().out
    assert 'failed' in output
    assert 'private-password' not in output and 'private-host' not in output


@pytest.mark.parametrize('field,value', [('email_verified', False), ('status', 'deleted')])
def test_ineligible_active_account_is_reported_and_blocks_apply(repair_db, field, value):
    engine, tables = repair_db
    with engine.begin() as connection:
        connection.execute(tables['app_user'].update().where(tables['app_user'].c.id == 1).values({field: value}))
        assert repair_profiles(connection, band_id=1)['blocked_active_user_ids'] == [1]
        with pytest.raises(ProfileRepairError, match='operator review'):
            repair_profiles(connection, band_id=1, apply=True)


def test_email_display_name_is_never_exposed(repair_db):
    engine, tables = repair_db
    with engine.begin() as connection:
        connection.execute(tables['app_user'].update().where(tables['app_user'].c.id == 1)
                           .values(display_name='private@example.test'))
        repair_profiles(connection, band_id=1, apply=True)
        assert connection.scalar(sa.select(tables['member'].c.name).where(
            tables['member'].c.linked_user_id == 1)) == '멤버'
