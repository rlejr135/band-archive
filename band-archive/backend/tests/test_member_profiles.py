"""Band-local automatic profiles, privacy, rejoin and optimistic editing."""
import json
import importlib.util
import pytest
from flask import g, request
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from alembic.migration import MigrationContext
from alembic.operations import Operations

import auth_service
from app import create_app
from config import TestingConfig
from extensions import db
from errors import ValidationError
from models import AppUser, Band, BandMembership, Member, PersonalLog, TenantScopeUnavailable
from member_profile_service import activate_member_profile, ensure_member_profile
from band_service import (approve_membership, create_invite, join_invite, join_request,
                          leave_band, review_request, create_request)


@pytest.fixture
def profiles(monkeypatch):
    app = create_app(TestingConfig, start_worker=False)
    with app.app_context():
        db.drop_all(); db.create_all()
        users = [AppUser(supabase_user_id=f'u{i}', email=f'u{i}@example.com',
                         display_name=f'Person{i}', email_verified=True,
                         is_platform_admin=i == 1) for i in range(1, 5)]
        db.session.add_all(users)
        bands = [Band(name='First', slug='first'), Band(name='Other', slug='other')]
        db.session.add_all(bands); db.session.flush()
        memberships = [BandMembership(band_id=1, user_id=i, role='owner' if i == 1 else 'member',
                                     status='active' if i < 3 else 'pending',
                                     join_introduction='Private application') for i in range(1, 4)]
        db.session.add_all(memberships); db.session.flush()
        for m in memberships[:2]:
            ensure_member_profile(m)
        db.session.add(Member(band_id=1, name='Historical', instrument='Old custom instrument'))
        db.session.commit()
        app.config['BAND_API_SCHEMA_READY'] = True
        app.config['PHASE4_ORM_SCHEMA_READY'] = True
        app.config['LEGACY_V1_WRITES_ENABLED'] = True
    def authenticate():
        key = request.headers.get('X-Test-User')
        if key not in {'1', '2', '3', '4'}:
            raise auth_service.AuthError('missing_token', 'Authentication required.')
        g.current_app_user = db.session.get(AppUser, int(key))
        return g.current_app_user
    monkeypatch.setattr(auth_service, 'authenticate_request', authenticate)
    monkeypatch.setattr('band_access.authenticate_request', authenticate)
    yield app
    with app.app_context():
        db.session.remove(); db.drop_all()


def headers(user=2):
    return {'X-Test-User': str(user), 'Authorization': 'Bearer test'}


BASE = '/api/v2/bands/1/members'


def test_activation_positions_empty_or_omitted_preserve_and_invalid_is_atomic(profiles):
    with profiles.app_context():
        membership = db.session.get(BandMembership, 2)
        profile = Member.query.filter_by(band_id=1, linked_user_id=2).one()
        starting_revision = profile.profile_revision

        activate_member_profile(membership, profile.name, positions=['BASS'])
        assert profile.positions_json == '["BASS"]'
        assert profile.instrument == '베이스'
        assert profile.profile_revision == starting_revision + 1

        activate_member_profile(membership, profile.name, positions=[])
        activate_member_profile(membership, profile.name)
        assert profile.positions_json == '["BASS"]'
        assert profile.instrument == '베이스'
        assert profile.profile_revision == starting_revision + 1

        before = (profile.name, profile.positions_json, profile.custom_position,
                  profile.instrument, profile.profile_revision)
        with pytest.raises(ValidationError):
            activate_member_profile(membership, 'Must not rename', positions=['INVALID'])
        assert (profile.name, profile.positions_json, profile.custom_position,
                profile.instrument, profile.profile_revision) == before

        unprofiled_membership = db.session.get(BandMembership, 3)
        unprofiled_membership.status = 'active'
        db.session.commit()
        with pytest.raises(ValidationError):
            activate_member_profile(unprofiled_membership, 'Must not create', positions=['INVALID'])
        assert Member.query.filter_by(band_id=1, linked_user_id=3).count() == 0


def test_own_profile_edit_and_scope(profiles):
    c = profiles.test_client()
    assert c.get(BASE + '/me').status_code == 401
    assert c.patch(BASE + '/me', json={'profile_revision': 0, 'name': 'Unauthenticated'}).status_code == 401
    for user in (3, 4):
        assert c.get(BASE + '/me', headers=headers(user)).status_code == 404
    assert c.get('/api/v2/bands/2/members/me', headers=headers()).status_code == 404
    assert c.patch('/api/v2/bands/2/members/me', headers=headers(),
                   json={'profile_revision': 0, 'name': 'Cross-band'}).status_code == 404
    assert c.patch(BASE + '/2', headers=headers(1),
                   json={'profile_revision': 0, 'name': 'Owner override'}).status_code == 405
    with profiles.app_context():
        user = db.session.get(AppUser, 2)
        user.email_verified = False
        db.session.commit()
    assert c.patch(BASE + '/me', headers=headers(),
                   json={'profile_revision': 0, 'name': 'Unverified'}).status_code == 403
    with profiles.app_context():
        user = db.session.get(AppUser, 2)
        user.email_verified = True
        db.session.commit()
    own = c.get(BASE + '/me', headers=headers())
    assert own.status_code == 200 and 'Authorization' in own.vary
    member = own.json['member']
    assert member['positions'] == [] and member['biography'] == '' and member['is_self']
    result = c.patch(BASE + '/me', headers=headers(), json={'profile_revision': 0,
        'name': '  New band name  ', 'biography': '<script>plain text</script>', 'positions': ['GUITAR', 'VOCAL', 'OTHER'],
        'custom_position': '작곡'})
    assert result.status_code == 200
    assert result.json['member']['name'] == 'New band name'
    assert result.json['member']['instrument'] == '보컬, 기타, 작곡'
    assert result.json['member']['profile_revision'] == 1
    stale = c.patch(BASE + '/me', headers=headers(), json={'profile_revision': 0, 'name': 'Stale name'})
    assert stale.status_code == 409 and stale.json['code'] == 'profile_conflict'
    assert c.patch(BASE + '/me', headers=headers(), json={'profile_revision': 1, 'positions': []}).json['member']['custom_position'] == ''
    with profiles.app_context():
        assert Member.query.filter_by(linked_user_id=1).one().biography == ''
        assert Member.query.filter_by(linked_user_id=2).one().name == 'New band name'
        assert Member.query.filter_by(linked_user_id=2).one().instrument == ''
        assert Member.query.filter_by(name='Historical').one().instrument == 'Old custom instrument'


def test_own_name_edit_preserves_member_identity_and_other_profile_fields(profiles):
    c = profiles.test_client()
    before = c.get(BASE + '/me', headers=headers()).json['member']
    changed = c.patch(BASE + '/me', headers=headers(), json={
        'profile_revision': before['profile_revision'], 'name': 'My new name',
    })
    assert changed.status_code == 200
    after = changed.json['member']
    assert after['name'] == 'My new name'
    assert after['id'] == before['id']
    assert after['instrument'] == before['instrument']
    assert after['biography'] == before['biography']
    assert after['positions'] == before['positions']
    assert after['custom_position'] == before['custom_position']
    assert after['profile_revision'] == before['profile_revision'] + 1
    with profiles.app_context():
        account = db.session.get(AppUser, 2)
        profile = Member.query.filter_by(band_id=1, linked_user_id=2).one()
        assert account.display_name == 'Person2'
        assert profile.id == before['id'] and profile.linked_user_id == account.id


@pytest.mark.parametrize('payload', [
    {'biography': 'missing revision'}, {'profile_revision': True, 'biography': 'x'},
    {'profile_revision': 0, 'biography': 'x' * 301}, {'profile_revision': 0, 'positions': ['GUITAR', 'GUITAR']},
    {'profile_revision': 0, 'positions': ['x']}, {'profile_revision': 0, 'positions': [None]},
    {'profile_revision': 0, 'custom_position': 'x'}, {'profile_revision': 0, 'linked_user_id': 1},
    {'profile_revision': -1, 'biography': 'x'},
    {'profile_revision': 0, 'custom_position': 'x' * 51}, {'profile_revision': 0},
    {'profile_revision': 0, 'name': ''}, {'profile_revision': 0, 'name': 'x' * 101},
    {'profile_revision': 0, 'name': 'bad\nname'}, {'profile_revision': 0, 'name': 17},
])
def test_invalid_profile_requests_atomic(profiles, payload):
    assert profiles.test_client().patch(BASE + '/me', headers=headers(), json=payload).status_code == 400
    with profiles.app_context():
        assert Member.query.filter_by(linked_user_id=2).one().profile_revision == 0


def test_roster_privacy_active_delete_protection_and_legacy_bypass(profiles):
    c = profiles.test_client()
    body = c.get(BASE, headers=headers()).json
    assert 'members' in body, body
    assert len(body['members']) == 3
    mine = next(m for m in body['members'] if m['is_self'])
    assert mine['can_edit_profile'] and not mine['can_delete']
    assert 'Private application' not in json.dumps(body) and 'email' not in json.dumps(body)
    assert c.delete(BASE + '/1', headers=headers(1)).status_code == 409
    with profiles.app_context():
        from models import TenantBackfillState
        db.session.get(Band, 1).is_legacy_import = True
        db.session.add(TenantBackfillState(key='phase4_tenant_scope', status='validated'))
        db.session.commit()
        legacy_member = Member(name='Legacy created', instrument='Guitar')
        db.session.add(legacy_member)
        db.session.flush()
        assert legacy_member.band_id == 1
        db.session.delete(legacy_member)
        db.session.commit()
    legacy_write = c.put('/members/2', json={'name': 'hacked'})
    assert legacy_write.status_code == 403
    legacy_unlinked_name = c.put('/members/3', json={'name': 'Changed historical', 'instrument': 'changed too'})
    assert legacy_unlinked_name.status_code == 403
    same_name_and_other_field = c.put('/members/3', json={'name': 'Historical', 'instrument': 'Updated instrument'})
    assert same_name_and_other_field.status_code == 200
    # Band owners and platform administrators cannot use the v1 ID route to
    # rename someone else's account-linked profile.
    assert c.put('/members/2', headers=headers(1), json={'name': 'owner override'}).status_code == 403
    with profiles.app_context():
        db.session.get(AppUser, 4).is_platform_admin = True
        db.session.commit()
    assert c.put('/members/2', headers=headers(4), json={'name': 'superadmin override'}).status_code == 403
    assert c.patch(BASE + '/me', headers=headers(4),
                   json={'profile_revision': 0, 'name': 'superadmin override'}).status_code == 404
    with profiles.app_context():
        assert Member.query.filter_by(linked_user_id=2).one().name == 'Person2'
        historical = Member.query.filter_by(name='Historical').one()
        assert historical.instrument == 'Updated instrument'
    with profiles.app_context():
        band = db.session.get(Band, 1); band.member_roster_public = False; db.session.commit()
    assert c.get(BASE).status_code == 404
    assert c.get(BASE, headers=headers()).status_code == 200
    with profiles.app_context():
        m = BandMembership.query.filter_by(user_id=2).one(); m.status = 'left'; db.session.commit()
    assert c.get(BASE, headers=headers(1)).json['pagination']['total'] == 2
    former = c.get(BASE + '/2', headers=headers(1))
    assert former.status_code == 200 and former.json['member']['membership_status'] == 'left'
    assert not former.json['member']['can_edit_profile']
    assert former.json['member']['can_delete']
    with profiles.app_context():
        m = BandMembership.query.filter_by(user_id=2).one(); m.status = 'pending'; db.session.commit()
    pending_detail = c.get(BASE + '/2', headers=headers(1))
    assert pending_detail.status_code == 200
    assert pending_detail.json['member']['membership_status'] == 'inactive'


def test_activation_retries_rejoin_and_no_name_autolink(profiles):
    with profiles.app_context():
        actor, user = db.session.get(AppUser, 1), db.session.get(AppUser, 3)
        pending = BandMembership.query.filter_by(user_id=3).one()
        approved, changed = approve_membership(actor, pending.id, True)
        assert changed and approved.join_introduction is None
        profile = Member.query.filter_by(linked_user_id=3).one()
        profile.biography = 'Keep'; db.session.commit()
        approve_membership(actor, pending.id, True)
        assert Member.query.filter_by(linked_user_id=3).count() == 1
        _invite, raw = create_invite(actor, 1)
        leave_band(user, 1)
        assert Member.query.filter_by(linked_user_id=3).one().biography == 'Keep'
        join_invite(user, raw); join_invite(user, raw); join_request(user, 1, name='Different submitted name')
        assert Member.query.filter_by(linked_user_id=3).count() == 1
        assert Member.query.filter_by(linked_user_id=3).one().id == profile.id
        assert Member.query.filter_by(name='Historical').one().linked_user_id is None
        request_item, _ = create_request(db.session.get(AppUser, 4), {'name': 'New', 'slug': 'new'})
        reviewed, _ = review_request(actor, request_item.id, True)
        owner = Member.query.filter_by(band_id=reviewed.band_id, linked_user_id=4).one()
        assert owner.name == 'Person4'


def test_unique_nullable_profiles_and_read_does_not_create(profiles):
    with profiles.app_context():
        db.session.add(Member(band_id=1, name='Other legacy', instrument=''))
        db.session.commit()
        db.session.add(Member(band_id=1, linked_user_id=2, name='Duplicate', instrument=''))
        with pytest.raises(IntegrityError): db.session.commit()
        db.session.rollback()
        profile = Member.query.filter_by(linked_user_id=2).one(); db.session.delete(profile); db.session.commit()
    assert profiles.test_client().get(BASE + '/me', headers=headers()).status_code == 404
    with profiles.app_context():
        assert Member.query.filter_by(linked_user_id=2).count() == 0


@pytest.mark.parametrize('invalid_context', ['band', 'user', 'inactive', 'unverified'])
def test_trusted_profile_insert_context_is_verified_against_database(profiles, invalid_context):
    with profiles.app_context():
        membership = BandMembership.query.filter_by(user_id=2).one()
        band_id, user_id = membership.band_id, membership.user_id
        if invalid_context == 'band':
            band_id = 2
        elif invalid_context == 'user':
            user_id = 1
        elif invalid_context == 'inactive':
            membership.status = 'left'
            db.session.commit()
        else:
            user = db.session.get(AppUser, membership.user_id)
            user.email_verified = False
            db.session.commit()
        forged_context = Member(band_id=band_id, linked_user_id=user_id,
                                name='Must not insert', instrument='')
        forged_context._trusted_profile_membership_id = membership.id
        db.session.add(forged_context)
        with pytest.raises(TenantScopeUnavailable):
            db.session.flush()
        db.session.rollback()
        assert Member.query.filter_by(name='Must not insert').count() == 0


def test_immutable_v17_snapshot_excludes_new_fields(profiles):
    from migration_bootstrap import phase17_metadata, schema_contract_differences
    with profiles.app_context():
        assert 'biography' not in phase17_metadata().tables['member'].c
        assert 'uq_member_band_linked_user' not in {c.name for c in phase17_metadata().tables['member'].constraints}
        assert schema_contract_differences(db.session.connection()) == []


def _upgrade_member_profile_revision(connection):
    path = __import__('pathlib').Path(__file__).parents[1] / 'migrations/versions/20261017_18_member_profiles.py'
    spec = importlib.util.spec_from_file_location('member_profile_revision_test', path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    context = MigrationContext.configure(connection)
    operations = Operations(context)
    operations._install_proxy()
    try:
        revision.upgrade()
    finally:
        operations._remove_proxy()


def test_revision18_preserves_legacy_member_and_personal_log_rows():
    from sqlalchemy import create_engine
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE member (id INTEGER PRIMARY KEY, band_id INTEGER NOT NULL, '
                'linked_user_id INTEGER NULL, name VARCHAR(100) NOT NULL, instrument VARCHAR(100) NOT NULL, '
                'created_at DATETIME)')
            connection.exec_driver_sql('CREATE TABLE personal_log (id INTEGER PRIMARY KEY, '
                'member_id INTEGER NOT NULL REFERENCES member(id), title VARCHAR(200) NOT NULL)')
            connection.exec_driver_sql("INSERT INTO member VALUES (1, 7, NULL, 'Legacy one', 'bass', NULL)")
            connection.exec_driver_sql("INSERT INTO member VALUES (2, 7, NULL, 'Legacy two', 'custom', NULL)")
            connection.exec_driver_sql("INSERT INTO member VALUES (3, 7, 91, 'Linked', 'guitar', NULL)")
            connection.exec_driver_sql("INSERT INTO personal_log VALUES (11, 1, 'Old recording')")
            connection.exec_driver_sql("INSERT INTO personal_log VALUES (12, 3, 'Linked recording')")

            _upgrade_member_profile_revision(connection)

            assert connection.exec_driver_sql('SELECT id, band_id, linked_user_id, name, instrument '
                'FROM member ORDER BY id').all() == [
                    (1, 7, None, 'Legacy one', 'bass'),
                    (2, 7, None, 'Legacy two', 'custom'),
                    (3, 7, 91, 'Linked', 'guitar'),
                ]
            assert connection.exec_driver_sql('SELECT id, member_id, title FROM personal_log ORDER BY id').all() == [
                (11, 1, 'Old recording'), (12, 3, 'Linked recording')]
            assert connection.exec_driver_sql('SELECT biography, positions_json, custom_position, profile_revision '
                'FROM member ORDER BY id').all() == [('', '[]', '', 0)] * 3
            inspector = inspect(connection)
            unique_constraints = inspector.get_unique_constraints('member')
            assert any(item['name'] == 'uq_member_band_linked_user' for item in unique_constraints)
            connection.exec_driver_sql("INSERT INTO member(band_id, linked_user_id, name, instrument) "
                "VALUES (7, NULL, 'Legacy three', '')")
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql("INSERT INTO member(band_id, linked_user_id, name, instrument) "
                    "VALUES (7, 91, 'Duplicate linked', '')")
    finally:
        engine.dispose()


def test_revision18_duplicate_preflight_leaves_sqlite_schema_untouched():
    from sqlalchemy import create_engine
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE member (id INTEGER PRIMARY KEY, band_id INTEGER NOT NULL, '
                'linked_user_id INTEGER NULL, name VARCHAR(100) NOT NULL, instrument VARCHAR(100) NOT NULL, '
                'created_at DATETIME)')
            connection.exec_driver_sql("INSERT INTO member VALUES (1, 7, 91, 'One', 'bass', NULL)")
            connection.exec_driver_sql("INSERT INTO member VALUES (2, 7, 91, 'Two', 'guitar', NULL)")
            before_columns = [column['name'] for column in inspect(connection).get_columns('member')]
            before_rows = connection.exec_driver_sql('SELECT * FROM member ORDER BY id').all()
            with pytest.raises(RuntimeError, match='Duplicate linked member profiles'):
                _upgrade_member_profile_revision(connection)
            assert [column['name'] for column in inspect(connection).get_columns('member')] == before_columns
            assert connection.exec_driver_sql('SELECT * FROM member ORDER BY id').all() == before_rows
    finally:
        engine.dispose()
