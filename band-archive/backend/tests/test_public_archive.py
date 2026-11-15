"""Public archive projections and per-song exceptions."""

from datetime import date, datetime, timezone
import json

from extensions import db
from models import AuditEvent, Band, Media, Rehearsal, Song
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401


ARCHIVE = '/api/v2/public/archive'


def _headers(signer, subject='33333333-3333-3333-3333-333333333333', email='member@example.com'):
    return {'Authorization': f'Bearer {_token(signer, sub=subject, email=email)}'}


def test_public_override_does_not_expose_other_songs_and_can_be_revoked(access_app, signer, monkeypatch):
    from storage import storage
    signed = []

    def sign(key, expires_in=None):
        signed.append(key)
        return 'https://media.example.test/play'

    monkeypatch.setattr(storage, 'generate_url', sign)
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    client = access_app.test_client()
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        song = Song.query.filter_by(band_id=band.id, title='A members').one()
        song.memo = 'private memo'
        old_public_song = Song.query.filter_by(band_id=band.id, title='A legacy').one()
        open_media = Media(band_id=band.id, song_id=song.id, visibility='PUBLIC',
                           filename='open.mp4', original_filename='private filename.mp4', file_type='video')
        closed_media = Media(band_id=band.id, song_id=song.id, visibility='MEMBERS',
                             filename='closed.m4a', file_type='audio')
        db.session.add_all((open_media, closed_media))
        db.session.commit()
        song_id, band_id = song.id, band.id
        good_id, hidden_id, historical_id = open_media.id, closed_media.id, old_public_song.id

    band_api = f'/api/v2/bands/{band_id}'
    song_page = f'{ARCHIVE}/bands/band-a/songs/{song_id}'
    action = f'{band_api}/songs/{song_id}/public-override'
    assert client.get(song_page).status_code == 404
    assert client.get(f'{ARCHIVE}/media/{good_id}').status_code == 404
    assert client.get(f'{ARCHIVE}/bands/band-a').status_code == 404
    assert client.get(f'{ARCHIVE}/bands/band-a/songs/{historical_id}').status_code == 404
    assert client.patch(action, json={'public_override': True}, headers=_headers(signer)).status_code == 400
    enabled = client.patch(action, json={'public_override': True, 'rights_confirmed': True}, headers=_headers(signer))
    assert enabled.status_code == 200
    assert enabled.json['song']['effective_visibility'] == 'MEMBERS'
    assert enabled.json['song']['archive_visibility'] == 'PUBLIC'
    assert client.get(f'{band_api}/songs/{song_id}').status_code == 404
    assert client.get(f'{band_api}/media/{good_id}/comments').status_code == 404
    assert client.get(f'{ARCHIVE}/media/{hidden_id}').status_code == 404
    assert client.get(f'{ARCHIVE}/bands/band-b/songs/{song_id}').status_code == 404
    page = client.get(song_page)
    assert page.status_code == 200
    assert page.json['song'] == {'id': song_id, 'title': 'A members', 'artist': 'A',
                                 'band': {'name': 'Band A', 'slug': 'band-a'}}
    assert page.json['band']['description'] is None
    assert [item['id'] for item in page.json['media']] == [good_id]
    assert page.json['media'][0]['display_name'] == '영상'
    assert 'private filename' not in page.get_data(as_text=True)
    assert signed == []
    assert client.get(f'{ARCHIVE}/media/{good_id}').json['media']['playback']['url']
    assert signed == ['media/open.mp4']
    action_media = f'{band_api}/media/{hidden_id}/visibility'
    updated = client.patch(action_media, json={'visibility': 'PUBLIC', 'rights_confirmed': True},
                           headers=_headers(signer))
    assert updated.status_code == 200
    assert updated.json['media']['archive_visibility'] == 'PUBLIC'
    assert client.get(f'{ARCHIVE}/media/{hidden_id}').status_code == 200
    assert client.patch(action, json={'public_override': False}, headers=_headers(signer)).status_code == 200
    assert client.get(song_page).status_code == 404
    assert client.get(f'{ARCHIVE}/media/{good_id}').status_code == 404
    with access_app.app_context():
        assert db.session.get(Media, hidden_id).visibility == 'PUBLIC'
        events = AuditEvent.query.filter(AuditEvent.event_type.in_((
            'content.song.public_override.changed', 'content.media.visibility.changed',
        ))).order_by(AuditEvent.id).all()
        assert ['rights_confirmed' in json.loads(item.details_json) for item in events] == [True] * 3


def test_archive_rehearsal_constraints_and_mutation_tenant_authority(access_app, signer, monkeypatch):
    from storage import storage
    monkeypatch.setattr(storage, 'generate_url', lambda *args, **kwargs: 'https://media.example.test/play')
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    client = access_app.test_client()
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        other = Band.query.filter_by(slug='band-b').one()
        song = Song.query.filter_by(band_id=band.id, title='A members').one()
        song.public_override = True
        practice = Rehearsal(band_id=band.id, title='Private practice', date=date(2026, 1, 1),
                             visibility='MEMBERS')
        song.rehearsals.append(practice)
        db.session.add(practice)
        db.session.flush()
        media = Media(band_id=band.id, song_id=song.id, visibility='PUBLIC',
                      filename='hidden.mp4', file_type='video')
        db.session.add(media)
        db.session.commit()
        band_id, other_id, song_id, media_id = band.id, other.id, song.id, media.id
        practice_id = practice.id
    assert client.get(f'{ARCHIVE}/media/{media_id}').status_code == 404
    songs = client.get(f'{ARCHIVE}/songs', headers=_headers(signer)).json['songs']
    assert song_id in {item['id'] for item in songs}
    assert media_id not in {item['id'] for item in client.get(f'{ARCHIVE}/media').json['media']}
    assert client.patch(f'/api/v2/bands/{other_id}/media/{media_id}/visibility',
                        json={'visibility': 'MEMBERS'},
                        headers=_headers(signer, '77777777-7777-7777-7777-777777777777',
                                         'outsider@example.com')).status_code == 404
    action = f'/api/v2/bands/{band_id}/songs/{song_id}/public-override'
    assert client.patch(action, json={'public_override': False},
                        headers=_headers(signer, '66666666-6666-6666-6666-666666666666',
                                         'left@example.com')).status_code == 403
    with access_app.app_context():
        db.session.get(Rehearsal, practice_id).visibility = 'PUBLIC'
        db.session.commit()
    detail = client.get(f'{ARCHIVE}/media/{media_id}')
    assert detail.status_code == 200
    assert detail.json['media']['rehearsal_title'] is None
    with access_app.app_context():
        db.session.get(Media, media_id).rehearsal_id = practice_id
        db.session.commit()
    feed = client.get(f'{ARCHIVE}/media')
    assert next(item for item in feed.json['media'] if item['id'] == media_id)['rehearsal_title'] == 'Private practice'
    with access_app.app_context():
        db.session.get(Rehearsal, practice_id).visibility = 'MEMBERS'
        db.session.commit()
    assert client.get(f'{ARCHIVE}/media/{media_id}').status_code == 404
    with access_app.app_context():
        db.session.get(Rehearsal, practice_id).visibility = 'PUBLIC'
        db.session.get(Band, band_id).deleted_at = datetime.now(timezone.utc)
        db.session.commit()
    assert client.get(f'{ARCHIVE}/media/{media_id}').status_code == 404



def test_public_archive_writes_require_current_policy_receipt_and_confirmation(access_app, signer):
    from tests.test_upload_content_policy import _accept, _enable

    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    _enable(access_app)
    client = access_app.test_client()
    headers = _headers(signer)
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        song = Song.query.filter_by(band_id=band.id, title='A members').one()
        media = Media(band_id=band.id, song_id=song.id, visibility='MEMBERS',
                      filename='left-private.mp4', file_type='video')
        db.session.add(media)
        db.session.commit()
        song_id, media_id, band_id = song.id, media.id, band.id
    targets = [
        (f'/api/v2/bands/{band_id}/songs/{song_id}/public-override', {'public_override': True}),
        (f'/api/v2/bands/{band_id}/media/{media_id}/visibility', {'visibility': 'PUBLIC'}),
    ]
    for target, change in targets:
        response = client.patch(target, headers=headers, json=dict(change, rights_confirmed=True))
        assert response.status_code == 403
        assert response.json['code'] == 'upload_policy_acceptance_required'
    assert _accept(client, headers).status_code == 201
    for target, change in targets:
        response = client.patch(target, headers=headers, json=dict(
            change, rights_confirmed=True, visibility_confirm={'version': 'expired', 'confirmed': True},
        ))
        assert response.status_code == 409
        assert response.json['code'] == 'public_visibility_confirmation_required'
        response = client.patch(target, headers=headers, json=dict(
            change, rights_confirmed=True, visibility_confirm={'version': 'upload-v1', 'confirmed': True},
        ))
        assert response.status_code == 200
    with access_app.app_context():
        assert AuditEvent.query.filter_by(event_type='content.public_visibility_confirmed').count() == 2
