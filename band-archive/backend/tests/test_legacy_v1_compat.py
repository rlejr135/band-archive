"""Cross-tenant contracts for the historical, bandless HTTP API."""

from datetime import date

import pytest
from sqlalchemy import text

from extensions import db
from models import Band, GalleryImage, Media, Member, Rehearsal, Song
from storage import storage


@pytest.fixture
def legacy_and_foreign(app, client):
    legacy_song = client.post('/songs', json={'title': 'legacy', 'artist': 'band'}).get_json()['id']
    legacy_member = client.post('/members', json={'name': 'legacy', 'instrument': 'bass'}).get_json()['id']
    with app.app_context():
        foreign = Band(name='foreign', slug='foreign-v1-compat', default_visibility='PUBLIC',
                       storage_quota_bytes=1)
        db.session.add(foreign)
        db.session.flush()
        db.session.execute(text(
            "INSERT INTO song (band_id, visibility, title, artist, upvote_count, downvote_count, vote_score) "
            "VALUES (:band, 'PUBLIC', 'foreign', 'band', 0, 0, 0)"
        ), {'band': foreign.id})
        db.session.execute(text(
            "INSERT INTO member (band_id, name, instrument) VALUES (:band, 'foreign', 'drums')"
        ), {'band': foreign.id})
        db.session.execute(text(
            "INSERT INTO rehearsal (band_id, visibility, title, date) VALUES (:band, 'PUBLIC', 'foreign', :date)"
        ), {'band': foreign.id, 'date': date(2026, 1, 1)})
        db.session.commit()
        song = Song.query.filter_by(band_id=foreign.id).one()
        member = Member.query.filter_by(band_id=foreign.id).one()
        rehearsal = Rehearsal.query.filter_by(band_id=foreign.id).one()
        db.session.execute(text(
            "INSERT INTO media (band_id, visibility, song_id, filename, file_type, transcoding_status, "
            "processing_attempts, upvote_count, downvote_count, vote_score) "
            "VALUES (:band, 'PUBLIC', :song, 'foreign.mp4', 'video', 'queued', 0, 0, 0, 0)"
        ), {'band': foreign.id, 'song': song.id})
        db.session.execute(text(
            "INSERT INTO gallery_image (band_id, visibility, filename, is_featured) "
            "VALUES (:band, 'PUBLIC', 'foreign.png', 1)"
        ), {'band': foreign.id})
        db.session.commit()
        return {'legacy_song': legacy_song, 'legacy_member': legacy_member,
                'foreign_song': song.id, 'foreign_member': member.id,
                'foreign_rehearsal': rehearsal.id,
                'foreign_media': Media.query.filter_by(band_id=foreign.id).one().id}


def test_v1_reads_conceal_every_foreign_domain_record(client, legacy_and_foreign):
    foreign = legacy_and_foreign
    assert {item['id'] for item in client.get('/songs').get_json()} == {foreign['legacy_song']}
    assert client.get(f"/songs/{foreign['foreign_song']}").status_code == 404
    assert client.get(f"/rehearsals/{foreign['foreign_rehearsal']}").status_code == 404
    assert client.get(f"/members/{foreign['foreign_member']}").status_code == 404
    assert client.get(f"/media/{foreign['foreign_media']}/processing").status_code == 404
    assert client.get('/gallery/featured').get_json() is None


def test_v1_foreign_download_never_signs_or_touches_storage(app, client, legacy_and_foreign, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda *args, **kwargs: calls.append(args) or 'unexpected')
    assert client.get('/uploads/foreign.mp4').status_code == 404
    assert client.get('/uploads/personal_logs/foreign.mp4').status_code == 404
    assert calls == []


def test_v1_write_switch_stops_before_body_database_or_storage(app, client, monkeypatch):
    app.config['LEGACY_V1_WRITES_ENABLED'] = False
    calls = []
    monkeypatch.setattr(storage, 'upload', lambda *args, **kwargs: calls.append(args))
    with app.app_context():
        before = Song.query.count()
    response = client.post('/songs', json={'title': 'nope', 'artist': 'nope'})
    assert response.status_code == 410
    assert response.get_json()['error'] == 'legacy_v1_writes_disabled'
    assert client.get('/songs').status_code == 200
    assert calls == []
    with app.app_context():
        assert Song.query.count() == before


def test_v1_scope_fails_closed_when_marker_is_invalid_or_import_is_ambiguous(app, client):
    with app.app_context():
        db.session.execute(text("UPDATE tenant_backfill_state SET status = 'failed'"))
        db.session.commit()
    assert client.get('/songs').status_code == 503
    with app.app_context():
        db.session.execute(text("UPDATE tenant_backfill_state SET status = 'validated'"))
        db.session.execute(text(
            "INSERT INTO band (name, slug, default_visibility, storage_quota_bytes, is_legacy_import, "
            "created_at, updated_at) VALUES ('duplicate', 'duplicate-legacy-v1', 'PUBLIC', 1, 1, "
            "'2026-01-01', '2026-01-01')"
        ))
        db.session.commit()
    assert client.get('/songs').status_code == 503


def test_v1_usage_log_is_once_per_request_and_redacts_sensitive_values(client, caplog):
    caplog.set_level('INFO')
    client.get('/songs?token=secret-file-name.mp4', headers={
        'X-Client-Version': '1.2.3 bearer-secret@example.test',
    })
    events = [record.getMessage() for record in caplog.records if 'legacy_v1_usage' in record.getMessage()]
    assert len(events) == 1
    assert 'route=/songs method=GET status=200' in events[0]
    assert 'secret' not in events[0]
    assert '?' not in events[0]
