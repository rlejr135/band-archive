"""Band leave, transfer, and upload-retention contracts."""

from datetime import datetime, timedelta, timezone

import pytest

from extensions import db
from models import (
    AppUser, Band, BandMembership, DirectUploadSession, Member,
    MultipartUploadSession, PersonalLogMultipartUploadSession, Song,
)
from storage import storage
from tests.test_band_access_phase6 import access_app, signer
from tests.test_v2_content_writes import _band_id, principals, write_client


def _membership_id(app, band_id, user_id):
    with app.app_context():
        return BandMembership.query.filter_by(band_id=band_id, user_id=user_id).one().id


def test_leave_is_self_scoped_idempotent_and_preserves_authored_content(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    with access_app.app_context():
        membership = BandMembership.query.filter_by(band_id=band_id, user_id=3).one()
        membership_id = membership.id
        before_songs = [(item.id, item.created_by_membership_id, item.title, item.visibility)
                        for item in Song.query.filter_by(band_id=band_id).order_by(Song.id)]
        assert any(creator_id == membership_id for _, creator_id, _, _ in before_songs)
    delete_calls = []
    monkeypatch.setattr(storage, 'delete', lambda key: delete_calls.append(key))

    response = write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['member'])
    assert response.status_code == 200
    payload = response.get_json()['membership']
    assert payload['id'] == membership_id and payload['status'] == 'left'
    assert payload['left_at']
    with access_app.app_context():
        membership = db.session.get(BandMembership, membership_id)
        first_left_at = membership.left_at
        assert membership.user_id == 3 and membership.role == 'member'
        after_songs = [(item.id, item.created_by_membership_id, item.title, item.visibility)
                       for item in Song.query.filter_by(band_id=band_id).order_by(Song.id)]
        assert after_songs == before_songs
        assert AppUser.query.get(3) is not None

    repeated = write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['member'])
    assert repeated.status_code == 200
    assert repeated.get_json()['membership']['left_at'] == payload['left_at']
    with access_app.app_context():
        assert db.session.get(BandMembership, membership_id).left_at == first_left_at
    assert delete_calls == []
    assert write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['outsider']).status_code == 404


def test_leave_requires_transfer_for_any_active_owner_and_transfer_target_is_eligible(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    owner_band = write_client.get('/api/v2/bands', headers=principals['owner']).get_json()['bands'][0]
    assert owner_band['permissions']['can_transfer_ownership'] is True
    assert owner_band['permissions']['can_leave'] is False

    rejected = write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['owner'])
    assert rejected.status_code == 409 and rejected.get_json()['error'] == 'ownership_transfer_required'

    with access_app.app_context():
        owner_membership = BandMembership.query.filter_by(band_id=band_id, user_id=1).one()
        owner_membership_id = owner_membership.id
        target = BandMembership.query.filter_by(band_id=band_id, user_id=3).one()
        target_id = target.id
        target.user.status = 'active'
        target.user.email_verified = True
        db.session.commit()
    roster = write_client.get(f'/api/v2/bands/{band_id}/memberships', headers=principals['owner']).get_json()
    candidates = {item['id']: item['can_receive_ownership'] for item in roster['memberships']}
    assert candidates[target_id] is True
    assert candidates[owner_membership_id] is False
    assert 'email_verified' not in str(roster) and 'is_platform_admin' not in str(roster)

    self_transfer = write_client.post(f'/api/v2/bands/{band_id}/ownership-transfer',
                                      headers=principals['owner'],
                                      json={'membership_id': owner_membership_id})
    assert self_transfer.status_code == 409
    transferred = write_client.post(f'/api/v2/bands/{band_id}/ownership-transfer',
                                    headers=principals['owner'], json={'membership_id': target_id})
    assert transferred.status_code == 200
    left = write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['owner'])
    assert left.status_code == 200 and left.get_json()['membership']['status'] == 'left'


@pytest.mark.parametrize('target_state', [('disabled', True), ('active', False)])
def test_transfer_rejects_disabled_or_unverified_target(access_app, write_client, principals, target_state):
    band_id = _band_id(access_app)
    target_id = _membership_id(access_app, band_id, 3)
    with access_app.app_context():
        target_user = db.session.get(AppUser, 3)
        target_user.status, target_user.email_verified = target_state
        db.session.commit()
    response = write_client.post(f'/api/v2/bands/{band_id}/ownership-transfer',
                                 headers=principals['owner'], json={'membership_id': target_id})
    assert response.status_code == 409
    assert response.get_json()['error'] == 'ownership_target_ineligible'
    with access_app.app_context():
        assert BandMembership.query.filter_by(band_id=band_id, user_id=1, role='owner', status='active').count() == 1
        assert BandMembership.query.filter_by(band_id=band_id, user_id=3, status='active').one().role == 'member'


def test_rejoin_request_reuses_left_membership_and_resets_role(access_app, write_client, principals):
    band_id = _band_id(access_app)
    with access_app.app_context():
        item = BandMembership.query.filter_by(band_id=band_id, user_id=3).one()
        membership_id = item.id
        item.status = 'left'; item.role = 'admin'; item.left_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
    rejoin = write_client.post(f'/api/v2/bands/{band_id}/join-requests', headers=principals['member'], json={'name': 'Retained Member'})
    # A rejoin request changes state, so preserve the existing API's 201
    # response while keeping the original membership row ID.
    assert rejoin.status_code == 201
    assert rejoin.get_json()['membership']['id'] == membership_id
    with access_app.app_context():
        item = db.session.get(BandMembership, membership_id)
        assert (item.status, item.role) == ('pending', 'member')
    approved = write_client.patch(f'/api/v2/memberships/{membership_id}', headers=principals['owner'],
                                  json={'action': 'approve'})
    assert approved.status_code == 200
    with access_app.app_context():
        item = db.session.get(BandMembership, membership_id)
        assert (item.status, item.role, item.left_at) == ('active', 'member', None)


def test_leave_blocks_owned_live_uploads_only_and_never_runs_r2_cleanup(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    with access_app.app_context():
        membership = BandMembership.query.filter_by(band_id=band_id, user_id=3).one()
        song = Song.query.filter_by(band_id=band_id).first()
        member = Member(band_id=band_id, name='Upload owner', instrument='voice')
        other_band = Band(name='Other upload band', slug='other-upload-band')
        db.session.add_all((member, other_band)); db.session.flush()
        other_membership = BandMembership(band_id=other_band.id, user_id=3, role='member', status='active')
        db.session.add(other_membership); db.session.flush()
        other_song = Song(band_id=other_band.id, created_by_membership_id=other_membership.id,
                          title='other target', artist='other')
        db.session.add(other_song); db.session.flush()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future = now + timedelta(hours=1)
        db.session.add_all((
            MultipartUploadSession(
                band_id=band_id, created_by_membership_id=membership.id, is_legacy=False,
                r2_upload_id='multipart-media', object_key=f'bands/{band_id}/media/m.mp4',
                original_filename='m.mp4', content_type='video/mp4', declared_bytes=1,
                song_id=song.id, status='initiated', expires_at=future,
            ),
            PersonalLogMultipartUploadSession(
                band_id=band_id, created_by_membership_id=membership.id, is_legacy=False,
                r2_upload_id='multipart-log', object_key=f'bands/{band_id}/personal_logs/p.mp4',
                original_filename='p.mp4', title='log', content_type='video/mp4', declared_bytes=1,
                member_id=member.id, status='initiated', expires_at=future,
            ),
            DirectUploadSession(
                band_id=band_id, created_by_membership_id=membership.id, kind='media',
                object_key=f'bands/{band_id}/media/d.mp4', original_filename='d.mp4',
                content_type='video/mp4', declared_bytes=1, song_id=song.id,
                capability_token_hash='test-hash', status='initiated', expires_at=future,
            ),
            # Same user, different band: this must never block leaving band A.
            DirectUploadSession(
                band_id=other_band.id, created_by_membership_id=other_membership.id, kind='media',
                object_key=f'bands/{other_band.id}/media/other.mp4', original_filename='other.mp4',
                content_type='video/mp4', declared_bytes=1, song_id=other_song.id,
                capability_token_hash='other-hash', status='initiated', expires_at=future,
            ),
        ))
        db.session.commit()
        local_sessions = (
            MultipartUploadSession.query.filter_by(band_id=band_id).one(),
            PersonalLogMultipartUploadSession.query.filter_by(band_id=band_id).one(),
            DirectUploadSession.query.filter_by(band_id=band_id).one(),
        )
        local_session_ids = [(type(session), session.id) for session in local_sessions]
    delete_calls = []
    abort_calls = []
    monkeypatch.setattr(storage, 'delete', lambda key: delete_calls.append(key))
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda *args: abort_calls.append(args))

    blocked = write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['member'])
    assert blocked.status_code == 409 and blocked.get_json()['error'] == 'upload_in_progress'
    with access_app.app_context():
        for model, session_id in local_session_ids:
            session = db.session.get(model, session_id)
            session.status = 'expired'
        db.session.commit()
    left = write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['member'])
    assert left.status_code == 200
    assert delete_calls == [] and abort_calls == []


def test_expired_initiated_upload_does_not_block_leave(access_app, write_client, principals):
    band_id = _band_id(access_app)
    with access_app.app_context():
        membership = BandMembership.query.filter_by(band_id=band_id, user_id=3).one()
        song = Song.query.filter_by(band_id=band_id).first()
        expired = DirectUploadSession(
            band_id=band_id, created_by_membership_id=membership.id, kind='media',
            object_key=f'bands/{band_id}/media/expired.mp4', original_filename='expired.mp4',
            content_type='video/mp4', declared_bytes=1, song_id=song.id,
            capability_token_hash='expired-hash', status='initiated',
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1),
        )
        db.session.add(expired); db.session.commit()
    allowed = write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['member'])
    assert allowed.status_code == 200


def test_expired_completing_upload_fails_closed_and_blocks_leave(access_app, write_client, principals):
    band_id = _band_id(access_app)
    with access_app.app_context():
        membership = BandMembership.query.filter_by(band_id=band_id, user_id=3).one()
        song = Song.query.filter_by(band_id=band_id).first()
        completing = DirectUploadSession(
            band_id=band_id, created_by_membership_id=membership.id, kind='media',
            object_key=f'bands/{band_id}/media/completing.mp4', original_filename='completing.mp4',
            content_type='video/mp4', declared_bytes=1, song_id=song.id,
            capability_token_hash='completing-hash', status='completing',
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
        )
        db.session.add(completing); db.session.commit()
    blocked = write_client.post(f'/api/v2/bands/{band_id}/leave', headers=principals['member'])
    assert blocked.status_code == 409
    assert blocked.get_json()['error'] == 'upload_in_progress'


def test_direct_upload_completion_requires_original_creator_and_rechecks_before_materialization(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    with access_app.app_context():
        song_id = Song.query.filter_by(band_id=band_id).first().id
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    started = write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/initiate',
                                headers=principals['member'], json={
                                    'filename': 'recording.mp4', 'content_type': 'video/mp4',
                                    'declared_bytes': 10, 'song_id': song_id,
                                })
    assert started.status_code == 201
    session = started.get_json()
    endpoint = f'/api/v2/bands/{band_id}/uploads/direct/{session["session_id"]}/complete'
    stolen = write_client.post(endpoint, headers={**principals['admin'],
        session['capability_header']: session['upload_capability_token']}, json={})
    assert stolen.status_code == 404
    with access_app.app_context():
        assert DirectUploadSession.query.get(session['session_id']).status == 'initiated'
        assert db.session.get(Song, song_id).media_files == []

    def revoke_before_materialize(key):
        with access_app.app_context():
            member = BandMembership.query.filter_by(band_id=band_id, user_id=3).one()
            member.status = 'left'; db.session.commit()
        return {'ContentLength': 10}

    monkeypatch.setattr(storage, 'head', revoke_before_materialize)
    denied = write_client.post(endpoint, headers={**principals['member'],
        session['capability_header']: session['upload_capability_token']}, json={})
    assert denied.status_code == 403
    with access_app.app_context():
        assert DirectUploadSession.query.get(session['session_id']).status == 'initiated'
        assert db.session.get(Song, song_id).media_files == []
