"""Narrow owner / DB platform-admin removal, with retained band content."""

from datetime import datetime, timezone

import pytest

from band_service import membership_removal_access, remove_membership
from errors import ValidationError
from extensions import db
from models import AppUser, AuditEvent, Band, BandMembership, DirectUploadSession, Member, Song
from storage import storage
from tests.test_auth import _token
from tests.test_band_access_phase6 import access_app, signer
from tests.test_v2_content_writes import _band_id, principals, write_client


def target_id(app, band_id, user_id=3):
    with app.app_context():
        return BandMembership.query.filter_by(band_id=band_id, user_id=user_id).one().id


def endpoint(band_id, membership_id):
    return f'/api/v2/bands/{band_id}/memberships/{membership_id}/remove'


@pytest.mark.parametrize('actor,status', [('owner', 200), ('admin', 403), ('member', 403), ('outsider', 404)])
def test_removal_authority_matrix(access_app, write_client, principals, actor, status):
    band_id = _band_id(access_app)
    response = write_client.post(endpoint(band_id, target_id(access_app, band_id)),
                                 headers=principals[actor], json={'confirm': True})
    assert response.status_code == status
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']


def test_anonymous_and_forged_metadata_cannot_remove(access_app, write_client, signer):
    band_id = _band_id(access_app)
    url = endpoint(band_id, target_id(access_app, band_id))
    assert write_client.post(url, json={'confirm': True}).status_code == 401
    forged = {'Authorization': 'Bearer ' + _token(
        signer, sub='77777777-7777-7777-7777-777777777777', email='outsider@example.com',
        user_metadata={'is_platform_admin': True, 'role': 'owner'},
        app_metadata={'is_platform_admin': True},
    )}
    assert write_client.post(url, headers=forged, json={'confirm': True}).status_code == 404
    assert write_client.get(f'/api/v2/platform/bands/{band_id}/memberships', headers=forged).status_code == 403


def test_nonmember_superadmin_minimal_roster_and_removal(access_app, write_client, principals):
    band_id = _band_id(access_app)
    with access_app.app_context():
        db.session.get(AppUser, 7).is_platform_admin = True
        db.session.add(Member(band_id=band_id, linked_user_id=3, name='밴드 내 이름', instrument='bass'))
        db.session.commit()
    roster = write_client.get(f'/api/v2/platform/bands/{band_id}/memberships', headers=principals['outsider'])
    assert roster.status_code == 200
    data = roster.get_json()
    assert set(data['band']) == {'id', 'name'}
    assert len(data['memberships']) == 3
    for row in data['memberships']:
        assert set(row) == {'id', 'band_id', 'role', 'status', 'member_name', 'can_remove_from_band'}
        assert row['status'] == 'active'
        assert row['can_remove_from_band'] is (row['role'] != 'owner')
    member_id = target_id(access_app, band_id)
    assert next(row for row in data['memberships'] if row['id'] == member_id)['member_name'] == '밴드 내 이름'
    # No general private-content read privilege was granted by this capability.
    assert write_client.get(f'/api/v2/bands/{band_id}/songs', headers=principals['outsider']).status_code == 404
    result = write_client.post(endpoint(band_id, member_id), headers=principals['outsider'], json={'confirm': True})
    assert result.status_code == 200 and result.get_json()['changed'] is True


def test_owner_roster_flags_and_self_owner_target_protection(access_app, write_client, principals):
    band_id = _band_id(access_app)
    for actor in ('owner', 'admin'):
        data = write_client.get(f'/api/v2/bands/{band_id}/memberships', headers=principals[actor]).get_json()
        for row in data['memberships']:
            assert row['can_remove_from_band'] is (actor == 'owner' and row['status'] == 'active' and row['role'] != 'owner')
    owner_id = target_id(access_app, band_id, 1)
    assert write_client.post(endpoint(band_id, owner_id), headers=principals['owner'], json={'confirm': True}).status_code == 409
    with access_app.app_context():
        db.session.get(AppUser, 3).is_platform_admin = True
        db.session.commit()
    for membership_id in (owner_id, target_id(access_app, band_id)):
        assert write_client.post(endpoint(band_id, membership_id), headers=principals['member'], json={'confirm': True}).status_code == 409


@pytest.mark.parametrize('user_id', [4, 5])
def test_pending_rejected_are_not_removal_targets(access_app, write_client, principals, user_id):
    band_id = _band_id(access_app)
    assert write_client.post(endpoint(band_id, target_id(access_app, band_id, user_id)),
                             headers=principals['owner'], json={'confirm': True}).status_code == 409


def test_band_boundaries_and_deleted_bands(access_app, write_client, principals):
    band_id = _band_id(access_app)
    with access_app.app_context():
        other_id = Band.query.filter_by(slug='band-b').one().id
        foreign_id = BandMembership.query.filter_by(band_id=other_id, user_id=7).one().id
    assert write_client.post(endpoint(band_id, foreign_id), headers=principals['owner'], json={'confirm': True}).status_code == 404
    with access_app.app_context():
        db.session.get(AppUser, 7).is_platform_admin = True
        db.session.get(Band, band_id).deleted_at = datetime.now(timezone.utc)
        db.session.commit()
    for actor in ('owner', 'outsider'):
        assert write_client.post(endpoint(band_id, target_id(access_app, band_id)), headers=principals[actor], json={'confirm': True}).status_code == 404
    assert write_client.get(f'/api/v2/platform/bands/{band_id}/memberships', headers=principals['outsider']).status_code == 404


@pytest.mark.parametrize('body', [{}, {'confirm': False}, {'confirm': 1}, {'confirm': 'true'}, {'confirm': True, 'role': 'owner'}, [], None])
def test_explicit_strict_confirmation(access_app, write_client, principals, body):
    band_id = _band_id(access_app)
    response = write_client.post(endpoint(band_id, target_id(access_app, band_id)), headers=principals['owner'], json=body)
    assert response.status_code == 400


def test_confirmation_size_bound(access_app, write_client, principals):
    band_id = _band_id(access_app)
    assert write_client.post(endpoint(band_id, target_id(access_app, band_id)), headers=principals['owner'],
                             data=' ' * 1025, content_type='application/json').status_code == 413


@pytest.mark.parametrize('status,verified', [('disabled', True), ('active', False)])
def test_removal_service_rechecks_account_authority(access_app, status, verified):
    band_id = _band_id(access_app)
    with access_app.app_context():
        actor = db.session.get(AppUser, 7)
        actor.is_platform_admin = True
        actor.status, actor.email_verified = status, verified
        db.session.commit()
        with pytest.raises(ValidationError) as error:
            membership_removal_access(actor, band_id)
        assert error.value.status_code == 403


def test_retains_profile_content_and_idempotent_audit_rejoin(access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    membership_id = target_id(access_app, band_id)
    calls = []
    monkeypatch.setattr(storage, 'delete', lambda *a: calls.append(('delete', a)))
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda *a: calls.append(('abort', a)))
    with access_app.app_context():
        db.session.add(Member(band_id=band_id, linked_user_id=3, name='기존 이름', instrument='bass'))
        db.session.get(BandMembership, membership_id).role = 'admin'
        db.session.commit()
        profiles = [(r.id, r.linked_user_id, r.name) for r in Member.query.all()]
        songs = [(r.id, r.created_by_membership_id, r.title) for r in Song.query.all()]
    url = endpoint(band_id, membership_id)
    first = write_client.post(url, headers=principals['owner'], json={'confirm': True})
    assert first.status_code == 200 and first.get_json()['changed'] is True
    second = write_client.post(url, headers=principals['owner'], json={'confirm': True})
    assert second.status_code == 200 and second.get_json()['changed'] is False
    assert second.get_json()['membership'] == first.get_json()['membership']
    with access_app.app_context():
        assert [(r.id, r.linked_user_id, r.name) for r in Member.query.all()] == profiles
        assert [(r.id, r.created_by_membership_id, r.title) for r in Song.query.all()] == songs
        row = db.session.get(BandMembership, membership_id)
        assert row.status == 'left' and row.acted_by_user_id == 1
        assert AuditEvent.query.filter_by(event_type='membership_removed', target_id=str(membership_id)).count() == 1
        assert db.session.get(AppUser, 3).status == 'active'
    assert calls == []
    assert write_client.get(f'/api/v2/bands/{band_id}/songs', headers=principals['member']).status_code == 404
    assert write_client.post(f'/api/v2/bands/{band_id}/songs', headers=principals['member'], json={'title': 'x', 'artist': 'y'}).status_code == 403
    rejoin = write_client.post(f'/api/v2/bands/{band_id}/join-requests', headers=principals['member'], json={'name': '새 요청 이름'})
    assert rejoin.status_code == 201
    with access_app.app_context():
        assert db.session.get(BandMembership, membership_id).role == 'member'
        assert Member.query.filter_by(linked_user_id=3).one().name == '기존 이름'


def test_live_upload_cannot_block_removal_and_old_capability_cannot_complete(access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    with access_app.app_context():
        song_id = Song.query.filter_by(band_id=band_id).first().id
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    started = write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/initiate', headers=principals['member'], json={
        'filename': 'recording.mp4', 'content_type': 'video/mp4', 'declared_bytes': 10, 'song_id': song_id,
    })
    assert started.status_code == 201
    session = started.get_json()
    result = write_client.post(endpoint(band_id, target_id(access_app, band_id)), headers=principals['owner'], json={'confirm': True})
    assert result.status_code == 200
    denied = write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/{session["session_id"]}/complete',
                              headers={**principals['member'], session['capability_header']: session['upload_capability_token']}, json={})
    assert denied.status_code == 403
    with access_app.app_context():
        assert db.session.get(DirectUploadSession, session['session_id']).status == 'initiated'
        assert db.session.get(Song, song_id).media_files == []
