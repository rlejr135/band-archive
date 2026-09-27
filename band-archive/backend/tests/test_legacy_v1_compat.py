"""Cross-tenant contracts for the historical, bandless HTTP API."""

from datetime import date

import pytest
from sqlalchemy import text

from extensions import db
from models import (Band, Comment, GalleryImage, Media, MediaVote, Member,
                    PersonalLog, Rehearsal, Song, TenantBackfillState)
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
    assert caplog.records[-1].name == 'band_archive.legacy_v1'
    assert 'route=/songs method=GET status=200' in events[0]
    assert 'secret' not in events[0]
    assert '?' not in events[0]


@pytest.mark.parametrize('value,enabled', [
    (True, True), (False, False), (' true ', True), ('TRUE', True), ('1', True),
    ('yes', True), ('on', True), (' false ', False), ('FALSE', False), ('0', False),
    ('no', False), ('off', False), ('unknown', False), (1, False), (None, False),
])
def test_write_flag_parses_without_truthy_string_bypass(app, client, monkeypatch, value, enabled):
    app.config['LEGACY_V1_WRITES_ENABLED'] = value
    calls = []
    monkeypatch.setattr(storage, 'generate_upload_url', lambda *args, **kwargs: calls.append(args))
    response = client.post('/uploads/presign', data=b'not-json', content_type='application/octet-stream')
    assert (response.status_code != 410) if enabled else response.status_code == 410
    if not enabled:
        assert response.get_json()['error'] == 'legacy_v1_writes_disabled'
        assert calls == []
    assert client.get('/songs').status_code == 200
    assert client.head('/songs').status_code in {200, 405}
    # v2/auth infrastructure is deliberately never classified as legacy v1.
    assert client.get('/api/v2/me').status_code != 410


def test_request_scope_caches_validated_band_but_next_request_revalidates(app):
    from legacy_scope import LegacyScopeUnavailable, legacy_band_id

    with app.app_context(), app.test_request_context('/songs'):
        original = legacy_band_id()
        db.session.execute(text("UPDATE tenant_backfill_state SET status = 'failed'"))
        db.session.commit()
        assert legacy_band_id() == original
    with app.app_context(), app.test_request_context('/songs'):
        with pytest.raises(LegacyScopeUnavailable):
            legacy_band_id()


def _legacy_band_id(app):
    with app.app_context():
        return Band.query.filter_by(is_legacy_import=True).one().id


def _insert_legacy_media(app, song_id, filename='legacy-graph.mp4'):
    with app.app_context():
        band_id = _legacy_band_id(app)
        db.session.execute(text(
            "INSERT INTO media (band_id, visibility, song_id, filename, file_type, transcoding_status, "
            "processing_attempts, upvote_count, downvote_count, vote_score) "
            "VALUES (:band, 'PUBLIC', :song, :filename, 'video', 'queued', 0, 0, 0, 0)"
        ), {'band': band_id, 'song': song_id, 'filename': filename})
        db.session.commit()
        return Media.query.filter_by(filename=filename, band_id=band_id).one().id


def test_corrupt_media_foreign_song_is_hidden_before_delete_storage_or_cascade(
        app, client, legacy_and_foreign, monkeypatch):
    media_id = _insert_legacy_media(app, legacy_and_foreign['foreign_song'])
    calls = []
    monkeypatch.setattr(storage, 'delete', lambda *args: calls.append(args))
    assert client.delete(f'/media/{media_id}').status_code == 404
    assert calls == []
    with app.app_context():
        assert db.session.get(Media, media_id).song_id == legacy_and_foreign['foreign_song']


def test_corrupt_foreign_vote_child_blocks_media_delete_without_storage(
        app, client, legacy_and_foreign, monkeypatch):
    media_id = _insert_legacy_media(app, legacy_and_foreign['legacy_song'], 'vote-child.mp4')
    with app.app_context():
        foreign_band = Band.query.filter_by(slug='foreign-v1-compat').one()
        db.session.execute(text(
            "INSERT INTO media_vote (band_id, media_id, voter_hash, value, created_at, updated_at) "
            "VALUES (:band, :media, 'foreign-vote', 1, '2026-01-01', '2026-01-01')"
        ), {'band': foreign_band.id, 'media': media_id})
        db.session.commit()
    calls = []
    monkeypatch.setattr(storage, 'delete', lambda *args: calls.append(args))
    assert client.delete(f'/media/{media_id}').status_code == 404
    assert calls == []
    with app.app_context():
        assert MediaVote.query.filter_by(media_id=media_id).count() == 1


def test_corrupt_personal_log_member_and_comment_parent_are_hidden(app, client, legacy_and_foreign, monkeypatch):
    with app.app_context():
        legacy_band = _legacy_band_id(app)
        db.session.execute(text(
            "INSERT INTO personal_log (band_id, visibility, member_id, title, filename, file_type, "
            "transcoding_status, processing_attempts) VALUES (:band, 'PUBLIC', :member, 'bad', "
            "'bad.mp4', 'video', 'queued', 0)"
        ), {'band': legacy_band, 'member': legacy_and_foreign['foreign_member']})
        db.session.commit()
        bad_log = PersonalLog.query.filter_by(filename='bad.mp4').one()
    calls = []
    monkeypatch.setattr(storage, 'delete', lambda *args: calls.append(args))
    assert client.delete(f'/personal-logs/{bad_log.id}').status_code == 404
    assert calls == []


def test_corrupt_comment_parent_target_matrix_is_hidden_before_mutation(app, client, legacy_and_foreign):
    media_id = _insert_legacy_media(app, legacy_and_foreign['legacy_song'], 'comment-parent.mp4')
    with app.app_context():
        legacy_band = _legacy_band_id(app)
        foreign_band = Band.query.filter_by(slug='foreign-v1-compat').one()
        db.session.execute(text(
            "INSERT INTO comment (band_id, media_id, author, password_hash, content, created_at) "
            "VALUES (:band, :media, 'foreign', 'hash', 'foreign', '2026-01-01')"
        ), {'band': foreign_band.id, 'media': legacy_and_foreign['foreign_media']})
        db.session.commit()
        foreign_comment = Comment.query.filter_by(band_id=foreign_band.id).one()
        db.session.execute(text(
            "INSERT INTO comment (band_id, media_id, parent_id, author, password_hash, content, created_at) "
            "VALUES (:band, :media, :parent, 'legacy', 'hash', 'bad parent', '2026-01-01')"
        ), {'band': legacy_band, 'media': media_id, 'parent': foreign_comment.id})
        db.session.commit()
        legacy_comment = Comment.query.filter_by(content='bad parent').one()
    response = client.put(f'/comments/{legacy_comment.id}', json={
        'password': 'anything', 'content': 'must not mutate',
    })
    assert response.status_code == 404
    with app.app_context():
        assert db.session.get(Comment, legacy_comment.id).content == 'bad parent'


def test_url_map_inventory_guards_every_v1_mutation(app, client):
    app.config['LEGACY_V1_WRITES_ENABLED'] = 'off'
    unsafe = {'POST', 'PUT', 'PATCH', 'DELETE'}
    rules = [
        rule for rule in app.url_map.iter_rules()
        if unsafe.intersection(rule.methods)
        and not rule.rule.startswith(('/api/v2/', '/internal/'))
        and rule.rule != '/health'
    ]
    assert rules
    for rule in rules:
        path = rule.rule.replace('<int:id>', '1').replace('<int:media_id>', '1').replace(
            '<int:member_id>', '1').replace('<int:log_id>', '1').replace('<int:comment_id>', '1'
        ).replace('<int:part_number>', '1').replace('<session_id>', 'scope-session')
        method = next(method for method in unsafe if method in rule.methods)
        response = client.open(path, method=method, data=b'opaque', content_type='application/octet-stream')
        assert response.status_code == 410, (rule.rule, method, response.status_code)


def test_legacy_usage_logger_is_exactly_once_per_v1_request_and_never_v2(client, caplog):
    caplog.set_level('INFO', logger='band_archive.legacy_v1')
    client.get('/songs', headers={'X-Client-Version': 'v1.2.3'})
    client.get('/songs')
    client.get('/api/v2/me')
    events = [record for record in caplog.records if record.name == 'band_archive.legacy_v1']
    assert len(events) == 2
    assert all('route=/songs' in record.getMessage() for record in events)
