"""Phase 3 authorization contracts; SQLite races use constraint-safe services.

PostgreSQL staging remains the required gate for real row-lock contention.  The
SQLite tests below exercise the same idempotent retry/unique-constraint paths
without relying on SQLite's different writer-lock scheduling.
"""

from flask import g, request
import threading
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import postgresql

import auth_service
from band_service import (
    _locked_invite_by_token_hash, close_band, create_invite, join_invite,
    revoke_invite, transfer_ownership,
)
from app import create_app
from config import TestingConfig
from errors import ValidationError
from extensions import db
from models import AppUser, AuditEvent, Band, BandInviteCode, BandMembership


@pytest.fixture
def band_app(monkeypatch):
    app = create_app(TestingConfig, start_worker=False)
    with app.app_context():
        db.create_all()
        # Unit fixtures create the Phase 3 tables directly; production only
        # enables this switch after the isolated Alembic runner reaches head.
        app.config['BAND_API_SCHEMA_READY'] = True
        users = [
            AppUser(supabase_user_id='admin-id', email='admin@example.com', is_platform_admin=True),
            AppUser(supabase_user_id='owner-id', email='owner@example.com'),
            AppUser(supabase_user_id='member-id', email='member@example.com'),
            AppUser(supabase_user_id='other-id', email='other@example.com'),
        ]
        db.session.add_all(users)
        db.session.commit()
        identities = {user.supabase_user_id: user.id for user in users}

    def authenticate_for_test():
        subject = request.headers.get('X-Test-User')
        if subject not in identities:
            raise auth_service.AuthError('missing_token', 'Bearer authentication is required.')
        g.current_app_user = db.session.get(AppUser, identities[subject])
        return g.current_app_user

    monkeypatch.setattr(auth_service, 'authenticate_request', authenticate_for_test)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _headers(subject):
    return {'X-Test-User': subject}


def _approve_band(client):
    pending = client.post('/api/v2/band-creation-requests', headers=_headers('owner-id'), json={
        'name': 'The Archive', 'slug': 'the-archive', 'description': 'A real band',
    })
    assert pending.status_code == 201
    review = client.patch('/api/v2/platform/band-creation-requests/1', headers=_headers('admin-id'),
                          json={'action': 'approve'})
    assert review.status_code == 200
    return review.get_json()['request']['band_id']


def test_creation_approval_owner_and_public_contract(band_app):
    client = band_app.test_client()
    assert client.post('/api/v2/band-creation-requests', json={}).status_code == 401
    band_id = _approve_band(client)

    public = client.get('/api/v2/public/bands?q=archive')
    assert public.status_code == 200
    assert public.get_json()['bands'][0]['slug'] == 'the-archive'
    assert public.get_json()['bands'][0]['permissions']['can_manage_members'] is False
    owner = client.get('/api/v2/bands', headers=_headers('owner-id')).get_json()['bands'][0]
    assert owner['membership']['role'] == 'owner'
    assert owner['permissions']['can_close_band'] is True
    with band_app.app_context():
        assert BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').count() == 1
        assert AuditEvent.query.filter_by(event_type='band_created').count() == 1


def test_my_bands_exposes_only_callers_pending_requests_without_private_account_data(band_app):
    client = band_app.test_client()
    with band_app.app_context():
        owner = AppUser.query.filter_by(supabase_user_id='owner-id').one()
        member = AppUser.query.filter_by(supabase_user_id='member-id').one()
        other = AppUser.query.filter_by(supabase_user_id='other-id').one()
        active = Band(name='Active', slug='active', created_by_user_id=owner.id)
        requested = Band(name='Requested', slug='requested', description='private request', created_by_user_id=owner.id)
        another = Band(name='Another', slug='another', created_by_user_id=owner.id)
        db.session.add_all((active, requested, another)); db.session.flush()
        db.session.add_all((
            BandMembership(band_id=active.id, user_id=member.id, role='member', status='active'),
            BandMembership(band_id=requested.id, user_id=member.id, role='member', status='pending'),
            BandMembership(band_id=another.id, user_id=other.id, role='member', status='pending'),
        )); db.session.commit()
    response = client.get('/api/v2/bands', headers=_headers('member-id'))
    assert response.status_code == 200
    body = response.get_json()
    # The established active-list wire shape remains intact.
    assert [item['slug'] for item in body['bands']] == ['active']
    assert body['bands'][0]['membership']['user']['id'] == 'member-id'
    assert len(body['pending_memberships']) == 1
    pending = body['pending_memberships'][0]
    assert pending['status'] == 'pending'
    assert pending['band'] == {'id': pending['band']['id'], 'name': 'Requested', 'slug': 'requested',
                               'description': 'private request'}
    assert 'user' not in pending and 'email' not in str(pending)
    other_body = client.get('/api/v2/bands', headers=_headers('other-id')).get_json()
    assert [item['band']['slug'] for item in other_body['pending_memberships']] == ['another']


def test_public_bands_search_pagination_is_bounded_stable_and_hides_private_deleted(band_app):
    client = band_app.test_client()
    with band_app.app_context():
        owner = AppUser.query.filter_by(supabase_user_id='owner-id').one()
        visible = [Band(name=name, slug=slug, default_visibility='PUBLIC', created_by_user_id=owner.id)
                   for name, slug in (('Alpha', 'alpha'), ('Beta', 'beta'), ('Gamma', 'gamma'))]
        private = Band(name='Private', slug='private', default_visibility='MEMBERS', created_by_user_id=owner.id)
        deleted = Band(name='Deleted', slug='deleted', default_visibility='PUBLIC', created_by_user_id=owner.id)
        db.session.add_all((*visible, private, deleted)); db.session.flush()
        deleted.deleted_at = db.func.now(); db.session.commit()
    first = client.get('/api/v2/public/bands?limit=2&offset=0').get_json()
    second = client.get('/api/v2/public/bands?limit=2&offset=2').get_json()
    assert [item['slug'] for item in first['bands']] == ['alpha', 'beta']
    assert [item['slug'] for item in second['bands']] == ['gamma']
    assert first['pagination'] == {'limit': 2, 'offset': 0, 'total': 3, 'has_more': True}
    assert second['pagination']['has_more'] is False
    assert [item['slug'] for item in client.get('/api/v2/public/bands?q=alp').get_json()['bands']] == ['alpha']
    for query in ('limit=0', 'limit=101', 'limit=x', 'offset=-1', 'offset=x'):
        assert client.get(f'/api/v2/public/bands?{query}').status_code == 400


def test_membership_permissions_cross_band_and_invite_lifecycle(band_app):
    client = band_app.test_client()
    band_id = _approve_band(client)
    # A second band deliberately makes numeric object IDs potentially overlap
    # with a caller's valid band membership.
    with band_app.app_context():
        other = Band(name='Other', slug='other', created_by_user_id=1)
        db.session.add(other)
        db.session.flush()
        db.session.add(BandMembership(band_id=other.id, user_id=4, role='owner', status='active'))
        db.session.commit()
        other_id = other.id

    join = client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id'))
    assert join.status_code == 201
    membership_id = join.get_json()['membership']['id']
    duplicate = client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id'))
    assert duplicate.status_code == 200 and duplicate.get_json()['created'] is False
    assert client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('owner-id'),
                        json={'action': 'approve'}).status_code == 200
    # Owner of another band must not operate this membership by its global ID.
    assert client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('other-id'),
                        json={'role': 'admin'}).status_code == 404

    issued = client.post(f'/api/v2/bands/{band_id}/invite-codes', headers=_headers('owner-id'))
    assert issued.status_code == 201
    code = issued.get_json()['code']
    invite_id = issued.get_json()['invite']['id']
    assert client.post('/api/v2/invite-codes/join', headers=_headers('other-id'),
                       json={'code': code}).status_code == 200
    assert client.post(f'/api/v2/bands/{band_id}/invite-codes/{invite_id}/revoke',
                       headers=_headers('owner-id')).status_code == 200
    assert client.post('/api/v2/invite-codes/join', headers=_headers('owner-id'),
                       json={'code': code}).status_code == 404
    with band_app.app_context():
        invite = db.session.get(BandInviteCode, invite_id)
        assert invite.token_hash != code
        assert len(invite.token_hash) == 64
        assert code not in str(invite.__dict__)
        assert other_id != band_id


def test_transfer_then_leave_preserves_owner_invariant(band_app):
    client = band_app.test_client()
    band_id = _approve_band(client)
    assert client.post(f'/api/v2/bands/{band_id}/leave', headers=_headers('owner-id')).status_code == 409
    joined = client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id')).get_json()
    membership_id = joined['membership']['id']
    assert client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('owner-id'),
                        json={'action': 'approve'}).status_code == 200
    assert client.post(f'/api/v2/bands/{band_id}/ownership-transfer', headers=_headers('owner-id'),
                       json={'membership_id': membership_id}).status_code == 200
    # The previous owner is revalidated as an admin on every mutation; it
    # cannot continue a stale owner operation after the transfer commits.
    assert client.delete(f'/api/v2/bands/{band_id}', headers=_headers('owner-id')).status_code == 403
    assert client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('owner-id'),
                        json={'role': 'admin'}).status_code == 403
    assert client.post(f'/api/v2/bands/{band_id}/leave', headers=_headers('owner-id')).status_code == 200
    with band_app.app_context():
        active_owners = BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').all()
        assert [item.user_id for item in active_owners] == [3]


def test_sqlite_stale_owner_session_cannot_close_after_independent_transfer(tmp_path):
    """A cached owner row must be refreshed after another session transfers it.

    This is intentionally an on-disk SQLite database: the stale actor and the
    transfer use distinct scoped sessions, which is the shape a real server
    uses for concurrent requests.
    """
    config = type('FileSQLiteConfig', (TestingConfig,), {
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(tmp_path / 'race.db').as_posix()}",
    })
    app = create_app(config, start_worker=False)
    try:
        with app.app_context():
            db.create_all()
            old_owner = AppUser(supabase_user_id='old-owner', email='old@example.com')
            new_owner = AppUser(supabase_user_id='new-owner', email='new@example.com')
            db.session.add_all((old_owner, new_owner))
            db.session.flush()
            band = Band(name='Race', slug='race', created_by_user_id=old_owner.id)
            db.session.add(band)
            db.session.flush()
            old_membership = BandMembership(
                band_id=band.id, user_id=old_owner.id, role='owner', status='active',
            )
            new_membership = BandMembership(
                band_id=band.id, user_id=new_owner.id, role='member', status='active',
            )
            db.session.add_all((old_membership, new_membership))
            db.session.commit()
            old_owner_id, band_id, new_membership_id = old_owner.id, band.id, new_membership.id
            db.session.remove()

        cached = threading.Event()
        transfer_complete = threading.Event()
        close_result = {}

        def stale_close_attempt():
            with app.app_context():
                actor = db.session.get(AppUser, old_owner_id)
                # Deliberately retain the obsolete owner role in this session.
                assert BandMembership.query.filter_by(band_id=band_id, user_id=actor.id).one().role == 'owner'
                cached.set()
                assert transfer_complete.wait(5)
                try:
                    close_band(actor, band_id)
                except ValidationError as error:
                    close_result['status'] = error.status_code
                    db.session.rollback()
                else:
                    close_result['status'] = 'closed'
                finally:
                    db.session.remove()

        thread = threading.Thread(target=stale_close_attempt)
        thread.start()
        assert cached.wait(5)
        with app.app_context():
            transfer_ownership(db.session.get(AppUser, old_owner_id), band_id, new_membership_id)
            transfer_complete.set()
            db.session.remove()
        thread.join(5)
        assert not thread.is_alive()
        assert close_result['status'] == 403
        with app.app_context():
            assert db.session.get(Band, band_id).deleted_at is None
            assert BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').count() == 1
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()


def test_sqlite_stale_invite_reader_cannot_join_after_independent_revoke(tmp_path):
    """Redemption reloads a locked invite instead of trusting a stale read."""
    config = type('FileSQLiteConfig', (TestingConfig,), {
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(tmp_path / 'invite-race.db').as_posix()}",
    })
    app = create_app(config, start_worker=False)
    try:
        with app.app_context():
            db.create_all()
            owner = AppUser(supabase_user_id='invite-owner', email='owner@example.com')
            joiner = AppUser(supabase_user_id='invite-joiner', email='joiner@example.com')
            db.session.add_all((owner, joiner))
            db.session.flush()
            band = Band(name='Invite Race', slug='invite-race', created_by_user_id=owner.id)
            db.session.add(band)
            db.session.flush()
            db.session.add(BandMembership(band_id=band.id, user_id=owner.id, role='owner', status='active'))
            db.session.commit()
            invite, raw = create_invite(owner, band.id)
            owner_id, joiner_id, band_id, invite_id = owner.id, joiner.id, band.id, invite.id
            db.session.remove()

        cached = threading.Event()
        revoked = threading.Event()
        join_result = {}

        def stale_join_attempt():
            with app.app_context():
                joiner_user = db.session.get(AppUser, joiner_id)
                # This identity-map value is deliberately stale after revocation.
                assert BandInviteCode.query.filter_by(id=invite_id).one().revoked_at is None
                cached.set()
                assert revoked.wait(5)
                try:
                    join_invite(joiner_user, raw)
                except ValidationError as error:
                    join_result['status'] = error.status_code
                    db.session.rollback()
                else:
                    join_result['status'] = 'joined'
                finally:
                    db.session.remove()

        thread = threading.Thread(target=stale_join_attempt)
        thread.start()
        assert cached.wait(5)
        with app.app_context():
            revoke_invite(db.session.get(AppUser, owner_id), band_id, invite_id)
            revoked.set()
            db.session.remove()
        thread.join(5)
        assert not thread.is_alive()
        assert join_result['status'] == 404
        with app.app_context():
            assert BandMembership.query.filter_by(band_id=band_id, user_id=joiner_id).one_or_none() is None
            assert AuditEvent.query.filter_by(event_type='membership_joined_by_invite').count() == 0
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()


def test_invite_redemption_uses_postgres_row_lock_contract(band_app):
    """The lookup used by redemption compiles to PostgreSQL ``FOR UPDATE``."""
    with band_app.app_context():
        statement = _locked_invite_by_token_hash('a' * 64).statement
        rendered = str(statement.compile(dialect=postgresql.dialect()))
    assert 'FOR UPDATE' in rendered


def test_only_owner_changes_admin_roles_and_invites_reset_former_admin(band_app):
    client = band_app.test_client()
    band_id = _approve_band(client)
    joined = client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id')).get_json()
    membership_id = joined['membership']['id']
    assert client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('owner-id'),
                        json={'action': 'approve'}).status_code == 200
    assert client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('owner-id'),
                        json={'role': 'admin'}).status_code == 200
    # A band administrator can operate membership admission but cannot grant
    # or revoke administrative authority.
    assert client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('member-id'),
                        json={'role': 'member'}).status_code == 403
    assert client.patch('/api/v2/memberships/1', headers=_headers('owner-id'),
                        json={'role': 'member'}).status_code == 409
    assert client.post(f'/api/v2/bands/{band_id}/leave', headers=_headers('member-id')).status_code == 200
    issued = client.post(f'/api/v2/bands/{band_id}/invite-codes', headers=_headers('owner-id')).get_json()
    rejoin = client.post('/api/v2/invite-codes/join', headers=_headers('member-id'),
                         json={'code': issued['code']})
    assert rejoin.status_code == 200
    assert rejoin.get_json()['membership']['role'] == 'member'
    with band_app.app_context():
        item = db.session.get(BandMembership, membership_id)
        assert (item.role, item.status, item.left_at) == ('member', 'active', None)


def test_roster_serializers_do_not_expose_account_state(band_app):
    client = band_app.test_client()
    band_id = _approve_band(client)
    joined = client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id')).get_json()
    membership_id = joined['membership']['id']
    client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('owner-id'), json={'action': 'approve'})

    public_member = client.get('/api/v2/bands/by-slug/the-archive').get_json()['band']['members'][0]
    ordinary_member = client.get(f'/api/v2/bands/{band_id}/memberships', headers=_headers('member-id')).get_json()['memberships'][0]
    managed_member = client.get(f'/api/v2/bands/{band_id}/memberships', headers=_headers('owner-id')).get_json()['memberships'][0]
    assert 'email' not in public_member['user'] and 'status' not in public_member
    assert 'email' not in ordinary_member['user'] and 'status' not in ordinary_member
    assert managed_member['user']['email'] == 'owner@example.com'
    assert managed_member['status'] == 'active'
    assert 'status' not in managed_member['user']
    assert 'is_platform_admin' not in managed_member['user']
    assert 'email_verified' not in managed_member['user']


def test_safe_invite_endpoint_owner_constraint_audit_triggers_and_flags(band_app):
    client = band_app.test_client()
    band_id = _approve_band(client)
    # The old capability-in-path endpoint is intentionally not registered.
    assert client.post('/api/v2/invite-codes/not-a-secret/join', headers=_headers('other-id')).status_code == 404
    band = client.get('/api/v2/bands', headers=_headers('owner-id')).get_json()['bands'][0]
    assert band['visibility'] == 'PUBLIC'
    assert band['can_edit'] and band['can_delete'] and band['can_manage']
    with band_app.app_context():
        # db.create_all has the partial unique index defined by the model.
        db.session.add(BandMembership(band_id=band_id, user_id=3, role='owner', status='active'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_pending_request_unique_constraint_is_idempotent(band_app):
    client = band_app.test_client()
    first = client.post('/api/v2/band-creation-requests', headers=_headers('owner-id'),
                        json={'name': 'Once', 'slug': 'once'})
    second = client.post('/api/v2/band-creation-requests', headers=_headers('owner-id'),
                         json={'name': 'Once', 'slug': 'once'})
    assert first.status_code == 201
    assert second.status_code == 200 and second.get_json()['created'] is False
