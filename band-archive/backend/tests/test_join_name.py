"""Band-local join names update on activation without renaming other profiles."""
import pytest
from sqlalchemy import create_engine

from extensions import db
from models import AppUser, AuditEvent, Band, BandMembership, Member
from band_service import approve_membership, join_request, membership_dict
from errors import ValidationError
from join_name import required_join_name
from tests.test_member_profiles import profiles  # noqa: F401
from tests.test_join_introduction_policy import band_app, _create_band, _headers  # noqa: F401


@pytest.mark.parametrize('value', [None, 3, True, [], {}, '', '   ', 'x' * 101,
                                   'name\n', '\tname', 'na\x00me', 'na\x85me', 'na\u200bme', 'na\u202eme',
                                   '\ud800', '\udfff'])
def test_names_reject_missing_invalid_or_control_values(value):
    with pytest.raises(ValidationError):
        required_join_name(value)


def test_unicode_name_trim_and_codepoint_limit():
    assert required_join_name('  황기덕 🎸  ') == '황기덕 🎸'
    assert required_join_name('🎸' * 100) == '🎸' * 100


def test_http_new_application_requires_name_without_creating_rows(band_app):
    client = band_app.test_client()
    band_id = _create_band(client)
    for body in ({}, {'introduction': 'Hello'}, {'name': None}, {'name': ' '}, {'name': 'bad\n'}):
        assert client.post(f'/api/v2/bands/{band_id}/join-requests',
                           headers=_headers('member-id'), json=body).status_code == 400
    with band_app.app_context():
        user = AppUser.query.filter_by(supabase_user_id='member-id').one()
        assert BandMembership.query.filter_by(user_id=user.id, band_id=band_id).count() == 0


def test_pending_name_first_value_private_then_approval_consumes_name(profiles):
    with profiles.app_context():
        actor, user = db.session.get(AppUser, 1), db.session.get(AppUser, 4)
        other_band_profile = Member(band_id=2, linked_user_id=user.id, name='Other band profile',
                                    instrument='기타', biography='Other band biography',
                                    positions_json='["GUITAR"]', custom_position='', profile_revision=2)
        db.session.add(other_band_profile)
        db.session.commit()
        application, created = join_request(user, 1, introduction='My introduction', name='  밴드 이름  ')
        assert created and application.requested_name == '밴드 이름'
        assert membership_dict(application, 'management')['requested_name'] == '밴드 이름'
        assert membership_dict(application, 'self')['requested_name'] == '밴드 이름'
        assert 'requested_name' not in membership_dict(application, 'member')
        repeated, created = join_request(user, 1, introduction='Changed', name='Do not replace')
        assert not created and repeated.requested_name == '밴드 이름'
        assert repeated.join_introduction == 'My introduction'
        account_name = user.display_name
        approved, _ = approve_membership(actor, application.id, True)
        assert approved.requested_name is None and approved.join_introduction is None
        member = Member.query.filter_by(band_id=1, linked_user_id=user.id).one()
        assert member.name == '밴드 이름'
        assert user.display_name == account_name
        assert other_band_profile.name == 'Other band profile'
        assert other_band_profile.biography == 'Other band biography'
        assert other_band_profile.profile_revision == 2
        assert not AuditEvent.query.filter(AuditEvent.details_json.contains('밴드 이름')).count()
        # A different band's application has its own independently supplied name.
        other, _ = join_request(user, 2, name='Other band name')
        assert other.requested_name == 'Other band name'


def test_legacy_pending_null_name_fill_once_and_rejoin_preserves_profile(profiles):
    with profiles.app_context():
        actor, user = db.session.get(AppUser, 1), db.session.get(AppUser, 3)
        pending = BandMembership.query.filter_by(user_id=3).one()
        assert pending.requested_name is None
        member = Member(band_id=1, linked_user_id=3, name='Old profile name', instrument='보컬',
                        biography='Keep biography', positions_json='["VOCAL"]', custom_position='',
                        profile_revision=3)
        db.session.add(member)
        db.session.commit()
        join_request(user, 1, name='New local name', introduction='Do not replace legacy intro')
        assert pending.requested_name == 'New local name'
        assert pending.join_introduction == 'Private application'
        approve_membership(actor, pending.id, True)
        assert member.name == 'New local name'
        profile_id = member.id
        assert member.biography == 'Keep biography' and member.positions_json == '["VOCAL"]'
        assert member.profile_revision == 4
        pending.status = 'left'; db.session.commit()
        join_request(user, 1, name='Attempted rename')
        approve_membership(actor, pending.id, True)
        assert member.name == 'Attempted rename'
        assert member.id == profile_id
        assert member.biography == 'Keep biography'
        assert member.positions_json == '["VOCAL"]'
        assert member.profile_revision == 5
        approve_membership(actor, pending.id, True)
        assert member.name == 'Attempted rename' and member.profile_revision == 5
        assert pending.requested_name is None


def test_rejection_discards_pending_name_and_new_request_replaces_it(profiles):
    with profiles.app_context():
        actor, user = db.session.get(AppUser, 1), db.session.get(AppUser, 4)
        pending, _ = join_request(user, 1, name='Rejected name')
        approve_membership(actor, pending.id, False)
        assert pending.requested_name is None
        join_request(user, 1, name='Resubmitted name')
        assert pending.requested_name == 'Resubmitted name'


def test_profile_creation_failure_cannot_commit_approval_or_discard_name(profiles, monkeypatch):
    import band_service
    with profiles.app_context():
        actor, user = db.session.get(AppUser, 1), db.session.get(AppUser, 4)
        pending, _ = join_request(user, 1, name='Preserve on failure')
        membership_id = pending.id
        def fail(_membership, _submitted_name=None):
            raise ValidationError('Injected profile failure', 409)
        monkeypatch.setattr(band_service, 'activate_member_profile', fail)
        with pytest.raises(ValidationError):
            approve_membership(actor, membership_id, True)
        db.session.rollback()
        stored = db.session.get(BandMembership, membership_id)
        assert stored.status == 'pending' and stored.requested_name == 'Preserve on failure'
        assert Member.query.filter_by(band_id=1, linked_user_id=4).count() == 0


def test_old_snapshots_exclude_name_and_protected_digest_includes_it():
    from migration_bootstrap import (phase3_metadata, phase16_metadata, phase20_metadata,
                                     phase21_metadata)
    from tools.legacy_rehearsal.preserve_identity_refresh import _digest_rows
    for builder in (phase3_metadata, phase16_metadata, phase20_metadata):
        assert 'requested_name' not in builder().tables['band_membership'].c
    current = phase21_metadata().tables['band_membership']
    assert current.c.requested_name.nullable and current.c.requested_name.type.length == 100
    row = {column.name: None for column in current.columns}
    row['id'] = 1
    assert _digest_rows(current, [row]) != _digest_rows(
        current, [{**row, 'requested_name': 'Must not be discarded'}])


def test_migration_21_additive_nullable_no_backfill_and_refuses_loss():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import importlib.util
    from pathlib import Path
    from migration_bootstrap import phase20_metadata
    path = Path(__file__).parents[1] / 'migrations/versions/20261020_21_join_request_name.py'
    spec = importlib.util.spec_from_file_location('join_name_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = create_engine('sqlite:///:memory:')
    try:
        with engine.begin() as connection:
            phase20_metadata().create_all(connection)
            connection.exec_driver_sql("INSERT INTO band_membership "
                                      "(id,band_id,user_id,role,status,requested_at) "
                                      "VALUES (1,1,1,'member','pending',CURRENT_TIMESTAMP)")
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            assert connection.exec_driver_sql('SELECT requested_name FROM band_membership').scalar_one() is None
            connection.exec_driver_sql("UPDATE band_membership SET requested_name='Preserve'")
            with pytest.raises(RuntimeError, match='Refusing to discard'):
                migration.downgrade()
            connection.exec_driver_sql('UPDATE band_membership SET requested_name=NULL')
            migration.downgrade()
    finally:
        engine.dispose()
