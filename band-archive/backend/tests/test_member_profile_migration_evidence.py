import copy

import pytest
import sqlalchemy as sa

from tools.member_profile_backfill import ProfileRepairError
from tools.member_profile_migration_evidence import assert_old_rows_preserved, capture_old_rows


@pytest.fixture
def evidence_db():
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE member (id INTEGER PRIMARY KEY, band_id INTEGER, linked_user_id INTEGER, name TEXT, instrument TEXT)')
        connection.exec_driver_sql("INSERT INTO member VALUES(1, 1, NULL, 'original name', 'legacy bass')")
        connection.exec_driver_sql('CREATE TABLE band_availability_day (id INTEGER PRIMARY KEY, status TEXT)')
        connection.exec_driver_sql("INSERT INTO band_availability_day VALUES(9, 'PRIVATE SCHEDULE')")
    yield engine
    engine.dispose()


def expand(connection):
    for column, dtype in [('biography', 'TEXT'), ('positions_json', 'TEXT'),
                          ('custom_position', 'TEXT'), ('profile_revision', 'INTEGER')]:
        connection.exec_driver_sql(f'ALTER TABLE member ADD COLUMN {column} {dtype}')


def test_all_old_columns_and_calendar_data_preserved(evidence_db):
    with evidence_db.begin() as connection:
        before = capture_old_rows(connection)
        expand(connection)
        after = capture_old_rows(connection, source=before)
        assert assert_old_rows_preserved(before, after)
        assert 'PRIVATE SCHEDULE' not in str(before)
        assert 'original name' not in str(before)


def test_backfill_insertion_requires_exact_manifest(evidence_db):
    with evidence_db.begin() as connection:
        before = capture_old_rows(connection)
        expand(connection)
        connection.exec_driver_sql("INSERT INTO member(id,band_id,linked_user_id,name,instrument) VALUES(2,1,42,'new member','')")
        after = capture_old_rows(connection, source=before)
        with pytest.raises(ProfileRepairError):
            assert_old_rows_preserved(before, after)
        assert assert_old_rows_preserved(before, after, approved_new_member_ids=[2])


def test_exact_mapping_only_link_column_may_change(evidence_db):
    with evidence_db.begin() as connection:
        before = capture_old_rows(connection)
        expand(connection)
        connection.exec_driver_sql('UPDATE member SET linked_user_id=42 WHERE id=1')
        after = capture_old_rows(connection, source=before)
        links = [{'band_id': 1, 'member_id': 1, 'user_id': 42}]
        assert assert_old_rows_preserved(before, after, approved_links=links)
        connection.exec_driver_sql("UPDATE member SET instrument='wrong' WHERE id=1")
        with pytest.raises(ProfileRepairError):
            assert_old_rows_preserved(before, capture_old_rows(connection, source=before), approved_links=links)


def test_calendar_drift_is_not_allowed(evidence_db):
    with evidence_db.begin() as connection:
        before = capture_old_rows(connection)
        expand(connection)
        connection.exec_driver_sql("UPDATE band_availability_day SET status='changed'")
        with pytest.raises(ProfileRepairError):
            assert_old_rows_preserved(before, capture_old_rows(connection, source=before))


def test_removed_member_and_unapproved_column_fail(evidence_db):
    with evidence_db.begin() as connection:
        before = capture_old_rows(connection)
        expand(connection)
        connection.exec_driver_sql('DELETE FROM member')
        with pytest.raises(ProfileRepairError):
            assert_old_rows_preserved(before, capture_old_rows(connection, source=before))
        connection.exec_driver_sql('ALTER TABLE member ADD COLUMN unexpected TEXT')
        with pytest.raises(ProfileRepairError):
            capture_old_rows(connection, source=before)


def test_mapping_cannot_reassign_or_cross_band(evidence_db):
    with evidence_db.begin() as connection:
        before = capture_old_rows(connection)
        expand(connection)
        after = capture_old_rows(connection, source=before)
        with pytest.raises(ProfileRepairError):
            assert_old_rows_preserved(before, after, approved_links=[{'band_id': 2, 'member_id': 1, 'user_id': 42}])
        linked_before = copy.deepcopy(before)
        linked_before['member_rows']['1']['linked_user_id'] = 99
        with pytest.raises(ProfileRepairError):
            assert_old_rows_preserved(linked_before, after, approved_links=[{'band_id': 1, 'member_id': 1, 'user_id': 42}])
