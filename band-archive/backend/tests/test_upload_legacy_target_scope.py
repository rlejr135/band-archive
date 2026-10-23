"""Legacy upload targets must never accept global cross-band IDs."""

from datetime import date
from io import BytesIO

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
        db.session.execute(text(
            "INSERT INTO media (band_id, visibility, song_id, rehearsal_id, filename, file_type, "
            "transcoding_status, processing_attempts, upvote_count, downvote_count, vote_score) "
            "VALUES (:band, 'PUBLIC', :song, :rehearsal, 'foreign.mp4', 'video', 'queued', 0, 0, 0, 0)"
        ), {'band': foreign.id, 'song': foreign_song.id, 'rehearsal': foreign_rehearsal.id})
        db.session.commit()
        foreign_media = Media.query.filter_by(band_id=foreign.id).one()
        return {
            'song': legacy_song, 'rehearsal': legacy_rehearsal, 'member': legacy_member,
            'foreign_song': foreign_song.id, 'foreign_rehearsal': foreign_rehearsal.id,
            'foreign_member': foreign_member.id, 'foreign_media': foreign_media.id,
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


def test_v1_song_rehearsal_and_media_mutations_reject_foreign_ids_before_storage(
        app, client, upload_scope_graph, monkeypatch):
    calls = []
    for name in ('upload', 'delete', 'exists', 'copy'):
        monkeypatch.setattr(storage, name, lambda *args, _name=name, **kwargs: calls.append((_name, args)))

    foreign_song = upload_scope_graph['foreign_song']
    foreign_rehearsal = upload_scope_graph['foreign_rehearsal']
    foreign_media = upload_scope_graph['foreign_media']
    legacy_song = upload_scope_graph['song']
    legacy_rehearsal = upload_scope_graph['rehearsal']
    with app.app_context():
        before_media = Media.query.count()
        before_sessions = MultipartUploadSession.query.count()
        before_rehearsals = Rehearsal.query.count()
        foreign_media_before = db.session.get(Media, foreign_media)
        foreign_media_state = (foreign_media_before.filename, foreign_media_before.object_key,
                               foreign_media_before.is_featured)
    assert client.put(f'/songs/{foreign_song}', json={'title': 'mutated'}).status_code == 404
    assert client.delete(f'/songs/{foreign_song}').status_code == 410
    assert client.post(f'/songs/{foreign_song}/upload', data={
        'file': (BytesIO(b'foreign sheet'), 'foreign.pdf'),
    }).status_code == 404
    assert client.post(f'/songs/{foreign_song}/media', data={
        'file': (BytesIO(b'foreign media'), 'foreign.mp4'),
    }).status_code == 404
    assert client.post(f'/songs/{legacy_song}/media', data={
        'rehearsal_id': str(foreign_rehearsal),
        'file': (BytesIO(b'foreign relation'), 'foreign.mp4'),
    }).status_code == 404
    assert client.post(f'/rehearsals/{foreign_rehearsal}/media', data={
        'song_id': str(legacy_song), 'file': (BytesIO(b'foreign media'), 'foreign.mp4'),
    }).status_code == 404
    assert client.patch(f'/media/{foreign_media}/rehearsal', json={'rehearsal_id': legacy_rehearsal}).status_code == 404
    assert client.patch(f'/media/{foreign_media}/featured').status_code == 410
    assert client.put(f'/media/{foreign_media}/rename', json={'filename': 'renamed.mp4'}).status_code == 410
    assert client.delete(f'/media/{foreign_media}').status_code == 410
    assert client.put(f'/rehearsals/{foreign_rehearsal}', json={'title': 'mutated'}).status_code == 404
    assert client.delete(f'/rehearsals/{foreign_rehearsal}').status_code == 410
    assert client.put(f'/rehearsals/{legacy_rehearsal}', json={
        'song_ids': [foreign_song],
    }).status_code == 404
    assert client.post('/rehearsals', json={
        'title': 'foreign relation', 'date': '2026-09-27', 'song_ids': [foreign_song],
    }).status_code == 404
    assert calls == []
    with app.app_context():
        assert Media.query.count() == before_media
        assert MultipartUploadSession.query.count() == before_sessions
        assert Rehearsal.query.count() == before_rehearsals
        assert [song.id for song in db.session.get(Rehearsal, legacy_rehearsal).songs] == [legacy_song]
        assert db.session.get(Media, foreign_media).filename == 'foreign.mp4'
        foreign_media_after = db.session.get(Media, foreign_media)
        assert (foreign_media_after.filename, foreign_media_after.object_key,
                foreign_media_after.is_featured) == foreign_media_state
        assert db.session.get(Rehearsal, foreign_rehearsal).title == 'foreign rehearsal'


def test_v1_same_band_song_media_upload_still_works(client, upload_scope_graph, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, 'upload', lambda *args, **kwargs: calls.append(args))

    response = client.post(f"/songs/{upload_scope_graph['song']}/media", data={
        'rehearsal_id': str(upload_scope_graph['rehearsal']),
        'file': (BytesIO(b'same band media'), 'same-band.mp4'),
    })

    assert response.status_code == 201
    assert calls
    payload = response.get_json()
    assert payload['song_id'] == upload_scope_graph['song']
    assert payload['rehearsal_id'] == upload_scope_graph['rehearsal']
    assert client.patch(f"/media/{payload['id']}/rehearsal", json={
        'rehearsal_id': upload_scope_graph['foreign_rehearsal'],
    }).status_code == 404
    assert len(calls) == 1


def test_legacy_rehearsal_type_is_optional_validated_and_preserved(app, client):
    created = client.post('/rehearsals', json={
        'title': 'Legacy live', 'date': '2026-10-25', 'rehearsal_type': 'LIVE',
    })
    assert created.status_code == 201
    payload = created.get_json()
    assert payload['rehearsal_type'] == 'LIVE'
    rehearsal_id = payload['id']

    defaulted = client.post('/rehearsals', json={
        'title': 'Legacy default', 'date': '2026-10-26',
    })
    assert defaulted.status_code == 201
    assert defaulted.get_json()['rehearsal_type'] == 'UNCLASSIFIED'
    assert client.post('/rehearsals', json={
        'title': 'Bad type', 'date': '2026-10-27', 'rehearsal_type': 'CONCERT',
    }).status_code == 400

    updated = client.put(f'/rehearsals/{rehearsal_id}', json={'title': 'Legacy renamed'})
    assert updated.status_code == 200
    assert updated.get_json()['rehearsal_type'] == 'LIVE'
    assert client.put(f'/rehearsals/{rehearsal_id}', json={'rehearsal_type': 'CONCERT'}).status_code == 400
