"""Phase 3 authorization contracts; SQLite races use constraint-safe services.

PostgreSQL staging remains the required gate for real row-lock contention.  The
SQLite tests below exercise the same idempotent retry/unique-constraint paths
without relying on SQLite's different writer-lock scheduling.
"""

from flask import g, request
import pytest
from sqlalchemy.exc import IntegrityError

import auth_service
from app import create_app
from config import TestingConfig
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
    assert client.post(f'/api/v2/bands/{band_id}/leave', headers=_headers('owner-id')).status_code == 200
    with band_app.app_context():
        active_owners = BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').all()
        assert [item.user_id for item in active_owners] == [3]


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
