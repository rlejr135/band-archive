"""Authenticated band multipart-upload contracts for Phase 8."""

from extensions import db
from models import Band, BandMembership, Media, MultipartUploadSession, Song
from storage import storage
from tests.test_band_access_phase6 import access_app, signer
from tests.test_v2_content_writes import _band_id, principals, write_client


def _song(access_app, band_id):
    with access_app.app_context():
        membership = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        song = Song(band_id=band_id, created_by_membership_id=membership.id,
                    visibility='MEMBERS', title='upload target', artist='band')
        db.session.add(song); db.session.commit()
        return song.id, membership.id


def _initiate(client, band_id, headers, song_id, bytes=10):
    return client.post(f'/api/v2/bands/{band_id}/uploads/multipart/initiate', headers=headers, json={
        'filename': 'recording.mp4', 'content_type': 'video/mp4',
        'declared_bytes': bytes, 'song_id': song_id,
    })


def test_band_initiate_requires_active_membership_scopes_keys_and_reserves_quota(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, membership_id = _song(access_app, band_id)
    with access_app.app_context():
        band = db.session.get(Band, band_id); band.storage_quota_bytes = 15; db.session.commit()
        foreign_song = Song.query.filter_by(title='B public').one().id
    calls = []
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda key, content_type: calls.append(key) or 'phase8')
    endpoint = f'/api/v2/bands/{band_id}/uploads/multipart/initiate'
    assert write_client.post(endpoint, json={'filename': 'recording.mp4', 'declared_bytes': 10, 'song_id': song_id}).status_code == 401
    assert _initiate(write_client, band_id, principals['outsider'], song_id).status_code == 403
    assert _initiate(write_client, band_id, principals['member'], foreign_song).status_code == 404
    assert calls == []

    started = _initiate(write_client, band_id, principals['member'], song_id)
    assert started.status_code == 201
    payload = started.get_json()
    with access_app.app_context():
        session = db.session.get(MultipartUploadSession, payload['session_id'])
        assert session.band_id == band_id and session.created_by_membership_id == membership_id
        assert session.is_legacy is False
        assert session.object_key == f'bands/{band_id}/media/{payload["filename"]}'
    assert calls == [f'bands/{band_id}/media/{payload["filename"]}']
    # The live session itself reserves 10 bytes, so another 10-byte initiate
    # cannot oversubscribe this band's 15-byte quota.
    assert _initiate(write_client, band_id, principals['member'], song_id).status_code == 409
    assert len(calls) == 1


def test_capability_continues_parts_but_complete_rechecks_current_membership(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, membership_id = _song(access_app, band_id)
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda key, content_type: 'phase8')
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    started = _initiate(write_client, band_id, principals['member'], song_id)
    assert started.status_code == 201
    session = started.get_json(); session_id = session['session_id']
    capability = {session['capability_header']: session['upload_capability_token']}
    base = f'/api/v2/bands/{band_id}/uploads/multipart/{session_id}'
    with access_app.app_context():
        membership = db.session.get(BandMembership, membership_id)
        membership.status = 'left'; db.session.commit()
    # Capability routes remain resumable and intentionally do not require the
    # former member to keep a valid login while uploading parts.
    assert write_client.post(f'{base}/parts', headers=capability, json={'part_number': 1}).status_code == 200
    assert write_client.post(f'{base}/parts/1/ack', headers=capability,
                             json={'etag': 'etag', 'bytes': 10}).status_code == 200
    assert write_client.post(f'{base}/complete', headers={**principals['member'], **capability}).status_code == 403
    with access_app.app_context():
        assert Media.query.filter_by(band_id=band_id).count() == 0
        db.session.get(BandMembership, membership_id).status = 'active'; db.session.commit()
    completed = write_client.post(f'{base}/complete', headers={**principals['member'], **capability})
    assert completed.status_code == 201
    with access_app.app_context():
        media = Media.query.filter_by(band_id=band_id).one()
        assert media.created_by_membership_id == membership_id
        assert media.object_key == f'bands/{band_id}/media/{session["filename"]}'
