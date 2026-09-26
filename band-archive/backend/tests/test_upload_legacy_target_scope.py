"""Legacy upload targets must never accept global cross-band IDs."""

from datetime import date

import pytest
from sqlalchemy import text

from extensions import db
from models import Band, Media, Member, MultipartUploadSession, Rehearsal, Song
from storage import storage


@pytest.fixture
def upload_scope_graph(app, client):
    legacy_song = client.post('/songs', json={'title': 'legacy upload', 'artist': 'band'}).get_json()['id']
    legacy_rehearsal = client.post('/rehearsals', json={
        'title': 'legacy rehearsal', 'date': '2026-09-27', 'song_ids': [legacy_song],
    }).get_json()['id']
    legacy_member = client.post('/members', json={'name': 'legacy member', 'instrument': 'Bass'}).get_json()['id']
    with app.app_context():
        foreign = Band(name='foreign upload band', slug='foreign-upload-band',
                       default_visibility='PUBLIC', storage_quota_bytes=1)
        db.session.add(foreign)
        db.session.flush()
        # Raw inserts model a historical/corrupt foreign tenant that legacy
        # v1 ORM writers correctly refuse to create themselves.
        db.session.execute(text(
            "INSERT INTO song (band_id, visibility, title, artist, upvote_count, downvote_count, vote_score) "
            "VALUES (:band, 'PUBLIC', 'foreign', 'band', 0, 0, 0)"
        ), {'band': foreign.id})
        db.session.execute(text(
            "INSERT INTO rehearsal (band_id, visibility, title, date) "
            "VALUES (:band, 'PUBLIC', 'foreign rehearsal', :date)"
        ), {'band': foreign.id, 'date': date(2026, 9, 27)})
        db.session.execute(text(
            "INSERT INTO member (band_id, name, instrument) VALUES (:band, 'foreign member', 'Drums')"
        ), {'band': foreign.id})
        db.session.commit()
        foreign_song = Song.query.filter_by(band_id=foreign.id).one()
        foreign_rehearsal = Rehearsal.query.filter_by(band_id=foreign.id).one()
        foreign_member = Member.query.filter_by(band_id=foreign.id).one()
        return {
            'song': legacy_song, 'rehearsal': legacy_rehearsal, 'member': legacy_member,
            'foreign_song': foreign_song.id, 'foreign_rehearsal': foreign_rehearsal.id,
            'foreign_member': foreign_member.id,
        }


def _multipart_payload(**target):
    return {
        'filename': 'scope.mp4', 'content_type': 'video/mp4', 'declared_bytes': 8, **target,
    }


def _direct_payload(**target):
    return {
        'filename': 'scope.mp4', 'original_filename': 'scope.mp4', 'file_size': 8, **target,
    }


@pytest.mark.parametrize('endpoint,payload', (
    ('/uploads/complete/media', _direct_payload(song_id=None)),
    ('/uploads/complete/media', _direct_payload(rehearsal_id=1)),
    ('/uploads/multipart/initiate', _multipart_payload()),
    ('/uploads/multipart/initiate', _multipart_payload(rehearsal_id=1)),
))
def test_media_upload_requires_a_song_target_before_storage_or_db_mutation(
        app, client, upload_scope_graph, endpoint, payload, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, 'head', lambda key: calls.append(('head', key)) or {'ContentLength': 8})
    monkeypatch.setattr(storage, 'create_multipart_upload',
                        lambda key, content_type: calls.append(('multipart', key)) or 'unexpected')
    with app.app_context():
        before_media = Media.query.count()
        before_sessions = MultipartUploadSession.query.count()
    response = client.post(endpoint, json=payload)
    assert response.status_code == 400
    assert calls == []
    with app.app_context():
        assert Media.query.count() == before_media
        assert MultipartUploadSession.query.count() == before_sessions


@pytest.mark.parametrize('endpoint', ('/uploads/complete/media', '/uploads/multipart/initiate'))
@pytest.mark.parametrize('target_name', ('foreign_song', 'foreign_rehearsal'))
def test_foreign_media_target_is_rejected_before_storage_session_or_media(
        app, client, upload_scope_graph, endpoint, target_name, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, 'head', lambda key: calls.append(('head', key)) or {'ContentLength': 8})
    monkeypatch.setattr(storage, 'create_multipart_upload',
                        lambda key, content_type: calls.append(('multipart', key)) or 'unexpected')
    target = {'song_id': upload_scope_graph['song']}
    target['song_id' if target_name == 'foreign_song' else 'rehearsal_id'] = upload_scope_graph[target_name]
    payload = _direct_payload(**target) if endpoint.endswith('/media') else _multipart_payload(**target)
    with app.app_context():
        before_media = Media.query.count()
        before_sessions = MultipartUploadSession.query.count()
    response = client.post(endpoint, json=payload)
    assert response.status_code == 404
    assert calls == []
    with app.app_context():
        assert Media.query.count() == before_media
        assert MultipartUploadSession.query.count() == before_sessions


@pytest.mark.parametrize('endpoint', ('/uploads/complete/media', '/uploads/multipart/initiate'))
def test_legacy_song_only_and_same_band_song_rehearsal_targets_still_work(
        client, upload_scope_graph, endpoint):
    for target in (
        {'song_id': upload_scope_graph['song']},
        {'song_id': upload_scope_graph['song'], 'rehearsal_id': upload_scope_graph['rehearsal']},
    ):
        payload = _direct_payload(**target) if endpoint.endswith('/media') else _multipart_payload(**target)
        if endpoint.endswith('/media'):
            payload.pop('file_size')  # the fixture's local object is zero bytes
        assert client.post(endpoint, json=payload).status_code == 201


@pytest.mark.parametrize('endpoint', ('/uploads/complete/personal-log', '/uploads/multipart/initiate'))
def test_foreign_personal_log_member_is_rejected_before_storage_or_session(
        app, client, upload_scope_graph, endpoint, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, 'head', lambda key: calls.append(('head', key)) or {'ContentLength': 8})
    monkeypatch.setattr(storage, 'create_multipart_upload',
                        lambda key, content_type: calls.append(('multipart', key)) or 'unexpected')
    payload = ({
        'filename': 'scope.mp4', 'original_filename': 'scope.mp4', 'member_id': upload_scope_graph['foreign_member'],
    } if endpoint.endswith('personal-log') else _multipart_payload(member_id=upload_scope_graph['foreign_member']))
    response = client.post(endpoint, json=payload)
    assert response.status_code == 404
    assert calls == []
