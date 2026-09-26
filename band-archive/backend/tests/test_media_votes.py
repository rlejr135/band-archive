import sqlite3
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor

from flask import Flask
import pytest
from sqlalchemy import text

from app import _run_migrations, create_app
from config import TestingConfig
from extensions import db
from models import Band, Media, MediaVote, Song, SongVote, TenantBackfillState


VOTER_A = '123e4567-e89b-12d3-a456-426614174000'
VOTER_B = '123e4567-e89b-12d3-a456-426614174001'


@pytest.fixture
def foreign_vote_targets(app):
    """Historical foreign records that v1 vote writes must never touch."""
    with app.app_context():
        foreign = Band(name='foreign votes', slug='foreign-votes',
                       default_visibility='PUBLIC', storage_quota_bytes=1)
        db.session.add(foreign)
        db.session.flush()
        foreign_band_id = foreign.id
        db.session.execute(text(
            "INSERT INTO song (band_id, visibility, title, artist, upvote_count, downvote_count, vote_score) "
            "VALUES (:band, 'PUBLIC', 'foreign song', 'foreign band', 0, 0, 0)"
        ), {'band': foreign_band_id})
        foreign_song = db.session.execute(text(
            "SELECT id FROM song WHERE band_id = :band"
        ), {'band': foreign_band_id}).scalar_one()
        db.session.execute(text(
            "INSERT INTO media (band_id, visibility, song_id, filename, file_type, transcoding_status, "
            "processing_attempts, upvote_count, downvote_count, vote_score) "
            "VALUES (:band, 'PUBLIC', :song, 'foreign.mp3', 'audio', 'not_required', 0, 0, 0, 0)"
        ), {'band': foreign_band_id, 'song': foreign_song})
        foreign_media = db.session.execute(text(
            "SELECT id FROM media WHERE band_id = :band"
        ), {'band': foreign_band_id}).scalar_one()
        db.session.commit()
    return {'band_id': foreign_band_id, 'song_id': foreign_song, 'media_id': foreign_media}


def _song(client, title='Vote song'):
    return client.post('/songs', json={'title': title, 'artist': 'Band'}).get_json()['id']


def _media(app, song_id, filename, rehearsal_id=None):
    with app.app_context():
        media = Media(
            song_id=song_id, rehearsal_id=rehearsal_id,
            filename=filename, original_filename=filename, file_type='audio',
        )
        db.session.add(media)
        db.session.commit()
        return media.id


def _reset_file_vote_schema(app):
    """Match the validated legacy scope used by the shared app fixture."""
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add_all((
            Band(name='test legacy archive', slug='test-legacy-archive',
                 default_visibility='PUBLIC', storage_quota_bytes=1, is_legacy_import=True),
            TenantBackfillState(key='phase4_tenant_scope', status='validated'),
        ))
        db.session.commit()


def _vote(client, media_id, value, voter=VOTER_A, expected=0):
    return client.patch(
        f'/media/{media_id}/vote',
        json={'vote': value, 'expected_viewer_vote': expected},
        headers={'X-Voter-ID': voter},
    )


@pytest.mark.parametrize('kind,table,target_key,foreign_key,path_template', (
    ('song', 'song_vote', 'song_id', 'song_id', '/songs/{id}/vote'),
    ('media', 'media_vote', 'media_id', 'media_id', '/media/{id}/vote'),
))
@pytest.mark.parametrize('has_existing_vote', (False, True))
@pytest.mark.parametrize('value', (1, -1, 0))
def test_foreign_song_and_media_votes_cannot_mutate_existing_or_new_rows(
        app, client, foreign_vote_targets, kind, table, target_key, foreign_key,
        path_template, has_existing_vote, value):
    target_id = foreign_vote_targets[target_key]
    voter_value = hashlib.sha256(VOTER_A.encode('utf-8')).hexdigest()
    expected = 1 if has_existing_vote else 0
    with app.app_context():
        if has_existing_vote:
            db.session.execute(text(
                f"INSERT INTO {table} (band_id, {foreign_key}, voter_hash, value, created_at, updated_at) "
                "VALUES (:band, :target, :voter, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {'band': foreign_vote_targets['band_id'], 'target': target_id, 'voter': voter_value})
            item_table = 'song' if kind == 'song' else 'media'
            db.session.execute(text(
                f"UPDATE {item_table} SET upvote_count = 1, downvote_count = 0, vote_score = 1 "
                "WHERE id = :target"
            ), {'target': target_id})
            db.session.commit()
        item_model = Song if kind == 'song' else Media
        before_counts = tuple(getattr(db.session.get(item_model, target_id), field) for field in (
            'upvote_count', 'downvote_count', 'vote_score',
        ))
        before_votes = db.session.execute(text(
            f"SELECT count(*) FROM {table} WHERE {foreign_key} = :target"
        ), {'target': target_id}).scalar_one()

    response = client.patch(path_template.format(id=target_id), json={
        'vote': value, 'expected_viewer_vote': expected,
    }, headers={'X-Voter-ID': VOTER_A})

    assert response.status_code == 404
    with app.app_context():
        item_model = Song if kind == 'song' else Media
        after = db.session.get(item_model, target_id)
        assert (after.upvote_count, after.downvote_count, after.vote_score) == before_counts
        assert db.session.execute(text(
            f"SELECT count(*) FROM {table} WHERE {foreign_key} = :target"
        ), {'target': target_id}).scalar_one() == before_votes
        if has_existing_vote:
            assert db.session.execute(text(
                f"SELECT value FROM {table} WHERE {foreign_key} = :target AND voter_hash = :voter"
            ), {'target': target_id, 'voter': voter_value}).scalar_one() == 1


def test_media_vote_first_repeat_switch_and_cancel_are_idempotent(client, app):
    song_id = _song(client)
    media_id = _media(app, song_id, 'take.mp3')
    first = _vote(client, media_id, 1)
    assert first.status_code == 200
    assert first.get_json()['upvote_count'] == 1
    assert first.get_json()['downvote_count'] == 0
    assert first.get_json()['vote_score'] == 1
    assert first.get_json()['viewer_vote'] == 1

    assert _vote(client, media_id, 1, expected=1).get_json()['upvote_count'] == 1
    switched = _vote(client, media_id, -1, expected=1)
    assert (switched.get_json()['upvote_count'], switched.get_json()['downvote_count']) == (0, 1)
    cancelled = _vote(client, media_id, 0, expected=-1)
    assert (cancelled.get_json()['upvote_count'], cancelled.get_json()['downvote_count']) == (0, 0)
    assert cancelled.get_json()['viewer_vote'] == 0
    with app.app_context():
        assert MediaVote.query.filter_by(media_id=media_id).count() == 0


def test_media_vote_conflict_is_snapshot_and_retry_succeeds(client, app):
    song_id = _song(client)
    media_id = _media(app, song_id, 'conflict.mp3')
    assert _vote(client, media_id, 1).status_code == 200
    for stale_target in (1, -1, 0):
        conflict = _vote(client, media_id, stale_target, expected=0)
        assert conflict.status_code == 409
        payload = conflict.get_json()
        assert payload['code'] == 'vote_conflict'
        assert payload['media']['viewer_vote'] == 1
        assert (payload['media']['upvote_count'], payload['media']['downvote_count'], payload['media']['vote_score']) == (1, 0, 1)
    assert _vote(client, media_id, -1, expected=1).get_json()['viewer_vote'] == -1
    with app.app_context():
        assert MediaVote.query.filter_by(media_id=media_id, value=-1).count() == 1


def test_song_json_ranks_media_without_changing_song_list_order(client, app):
    first_song = _song(client, 'First song')
    second_song = _song(client, 'Second song')
    first_media = _media(app, first_song, 'first.mp3')
    second_media = _media(app, first_song, 'second.mp3')
    third_media = _media(app, first_song, 'third.mp3')
    _media(app, second_song, 'other.mp3')
    _vote(client, second_media, 1, VOTER_A)
    _vote(client, second_media, 1, VOTER_B)
    _vote(client, third_media, -1, VOTER_B)

    response = client.get('/songs', headers={'X-Voter-ID': VOTER_A})
    assert response.status_code == 200
    songs = response.get_json()
    assert [song['id'] for song in songs] == [first_song, second_song]
    assert {'upvote_count', 'downvote_count', 'vote_score', 'viewer_vote'}.isdisjoint(songs[0])
    media = songs[0]['media']
    assert [item['id'] for item in media] == [second_media, first_media, third_media]
    assert media[0]['viewer_vote'] == 1
    assert media[0]['vote_score'] == 2
    assert media[2]['vote_score'] == -1

    direct = client.get(f'/songs/{first_song}/media', headers={'X-Voter-ID': VOTER_A}).get_json()
    assert [item['id'] for item in direct] == [second_media, first_media, third_media]
    assert direct[0]['viewer_vote'] == 1

    updated = client.put(
        f'/songs/{first_song}', json={'title': 'Updated first'}, headers={'X-Voter-ID': VOTER_A},
    ).get_json()
    assert updated['title'] == 'Updated first'
    assert updated['media'][0]['viewer_vote'] == 1


def test_rehearsal_media_viewer_vote_uses_optional_header(client, app):
    song_id = _song(client)
    rehearsal_id = client.post('/rehearsals', json={'title': 'Practice', 'date': '2026-08-25'}).get_json()['id']
    media_id = _media(app, song_id, 'rehearsal.mp3', rehearsal_id=rehearsal_id)
    assert _vote(client, media_id, 1).status_code == 200
    response = client.get(f'/rehearsals/{rehearsal_id}/media', headers={'X-Voter-ID': VOTER_A})
    assert response.status_code == 200
    assert response.get_json()[0]['viewer_vote'] == 1


def test_song_list_uses_explicit_id_order_despite_legacy_song_vote_score(client):
    first_song = _song(client, 'First')
    second_song = _song(client, 'Second')
    legacy_vote = client.patch(
        f'/songs/{second_song}/vote',
        json={'vote': 1, 'expected_viewer_vote': 0}, headers={'X-Voter-ID': VOTER_A},
    )
    assert legacy_vote.status_code == 200
    assert [song['id'] for song in client.get('/songs').get_json()] == [first_song, second_song]


def test_media_vote_validates_identity_and_does_not_expose_raw_id(client, app):
    song_id = _song(client)
    media_id = _media(app, song_id, 'private.mp3')
    assert client.patch(f'/media/{media_id}/vote', json={'vote': 1, 'expected_viewer_vote': 0}).status_code == 400
    assert client.patch(
        f'/media/{media_id}/vote', json={'vote': 1, 'expected_viewer_vote': 0},
        headers={'X-Voter-ID': 'not-a-uuid'},
    ).status_code == 400
    assert _vote(client, media_id, 1, expected=2).status_code == 400
    assert _vote(client, media_id, True).status_code == 400
    assert _vote(client, media_id, 1).status_code == 200
    response = client.get(f'/songs/{song_id}', headers={'X-Voter-ID': VOTER_A})
    assert VOTER_A not in response.get_data(as_text=True)
    with app.app_context():
        vote = MediaVote.query.filter_by(media_id=media_id).first()
        assert vote and vote.voter_hash != VOTER_A and len(vote.voter_hash) == 64


@pytest.mark.parametrize(
    ('payload', 'message'),
    [
        (None, 'Request body is required'),
        ({'vote': True, 'expected_viewer_vote': 0}, 'vote must be -1, 0, or 1.'),
        ({'vote': 1}, 'expected_viewer_vote is required.'),
        ({'vote': 1, 'expected_viewer_vote': False}, 'expected_viewer_vote must be -1, 0, or 1.'),
    ],
)
def test_song_and_media_vote_share_the_same_request_contract(client, app, payload, message):
    song_id = _song(client)
    media_id = _media(app, song_id, 'contract.mp3')
    headers = {'X-Voter-ID': VOTER_A}
    for path in (f'/songs/{song_id}/vote', f'/media/{media_id}/vote'):
        response = client.patch(path, json=payload, headers=headers)
        assert response.status_code == 400
        assert response.get_json() == {'error': message}


def test_media_delete_cascades_media_votes(client, app):
    song_id = _song(client)
    media_id = _media(app, song_id, 'delete.mp3')
    assert _vote(client, media_id, 1).status_code == 200
    assert client.delete(f'/media/{media_id}').status_code == 200
    with app.app_context():
        assert MediaVote.query.filter_by(media_id=media_id).count() == 0


def test_song_delete_cascades_media_votes(client, app):
    song_id = _song(client)
    media_id = _media(app, song_id, 'song-delete.mp3')
    assert _vote(client, media_id, 1).status_code == 200
    assert client.delete(f'/songs/{song_id}').status_code == 200
    with app.app_context():
        assert MediaVote.query.filter_by(media_id=media_id).count() == 0


def test_legacy_song_vote_data_endpoint_remains_compatible_but_is_not_exposed(client, app):
    song_id = _song(client)
    response = client.patch(
        f'/songs/{song_id}/vote',
        json={'vote': 1, 'expected_viewer_vote': 0},
        headers={'X-Voter-ID': VOTER_A},
    )
    assert response.status_code == 200
    assert {'upvote_count', 'downvote_count', 'vote_score', 'viewer_vote'}.isdisjoint(response.get_json())
    with app.app_context():
        assert SongVote.query.filter_by(song_id=song_id).count() == 1


def test_media_vote_migration_backfills_media_without_damaging_legacy_song_votes(tmp_path):
    db_path = tmp_path / 'legacy-media-votes.db'
    connection = sqlite3.connect(db_path)
    connection.executescript('''
        CREATE TABLE song (id INTEGER PRIMARY KEY, title VARCHAR(100), artist VARCHAR(100));
        CREATE TABLE media (id INTEGER PRIMARY KEY, song_id INTEGER, file_type VARCHAR(20));
        CREATE TABLE personal_log (id INTEGER PRIMARY KEY, file_type VARCHAR(20));
        CREATE TABLE rehearsal (id INTEGER PRIMARY KEY);
        INSERT INTO song (id, title, artist) VALUES (1, 'Legacy', 'Band');
        INSERT INTO media (id, song_id, file_type) VALUES (9, 1, 'audio');
    ''')
    connection.commit()
    connection.close()

    flask_app = Flask(__name__)
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    _run_migrations(flask_app)
    connection = sqlite3.connect(db_path)
    connection.execute(
        "INSERT INTO song_vote (song_id, voter_hash, value, created_at, updated_at) VALUES (1, ?, 1, '2026-01-01', '2026-01-01')",
        ('s' * 64,),
    )
    connection.execute(
        "INSERT INTO media_vote (media_id, voter_hash, value, created_at, updated_at) VALUES (9, ?, -1, '2026-01-01', '2026-01-01')",
        ('m' * 64,),
    )
    connection.execute('UPDATE media SET upvote_count = 99, downvote_count = 99, vote_score = 99 WHERE id = 9')
    connection.commit()
    connection.close()

    _run_migrations(flask_app)
    _run_migrations(flask_app)
    connection = sqlite3.connect(db_path)
    media_row = connection.execute(
        'SELECT upvote_count, downvote_count, vote_score FROM media WHERE id = 9'
    ).fetchone()
    song_vote = connection.execute('SELECT song_id, voter_hash, value FROM song_vote').fetchone()
    media_vote = connection.execute('SELECT media_id, voter_hash, value FROM media_vote').fetchone()
    media_indexes = {row[1] for row in connection.execute('PRAGMA index_list(media_vote)')}
    connection.close()
    assert media_row == (0, 1, -1)
    assert song_vote == (1, 's' * 64, 1)
    assert media_vote == (9, 'm' * 64, -1)
    assert 'ix_media_vote_media_id' in media_indexes


def test_media_vote_file_sqlite_concurrency_keeps_one_row_and_consistent_counts(tmp_path):
    db_path = tmp_path / 'media-vote-concurrency.db'

    class FileTestingConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'

    vote_app = create_app(FileTestingConfig)
    _reset_file_vote_schema(vote_app)
    try:
        seed_client = vote_app.test_client()
        song_id = _song(seed_client, 'Concurrent')
        media_id = _media(vote_app, song_id, 'concurrent.mp3')
        barrier = threading.Barrier(2)

        def submit_vote():
            with vote_app.test_client() as thread_client:
                barrier.wait(timeout=5)
                return thread_client.patch(
                    f'/media/{media_id}/vote',
                    json={'vote': 1, 'expected_viewer_vote': 0},
                    headers={'X-Voter-ID': VOTER_A},
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: submit_vote(), range(2)))
        assert sorted(results) == [200, 409]
        with vote_app.app_context():
            media = db.session.get(Media, media_id)
            assert (media.upvote_count, media.downvote_count, media.vote_score) == (1, 0, 1)
            assert MediaVote.query.filter_by(media_id=media_id).count() == 1
    finally:
        with vote_app.app_context():
            db.session.remove()
            db.drop_all()


def test_song_vote_file_sqlite_concurrency_keeps_one_row_and_consistent_counts(tmp_path):
    db_path = tmp_path / 'song-vote-concurrency.db'

    class FileTestingConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'

    vote_app = create_app(FileTestingConfig)
    _reset_file_vote_schema(vote_app)
    try:
        seed_client = vote_app.test_client()
        song_id = _song(seed_client, 'Concurrent song')
        barrier = threading.Barrier(2)

        def submit_vote():
            with vote_app.test_client() as thread_client:
                barrier.wait(timeout=5)
                return thread_client.patch(
                    f'/songs/{song_id}/vote',
                    json={'vote': 1, 'expected_viewer_vote': 0},
                    headers={'X-Voter-ID': VOTER_A},
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: submit_vote(), range(2)))
        assert sorted(results) == [200, 409]
        with vote_app.app_context():
            song = db.session.get(Song, song_id)
            assert (song.upvote_count, song.downvote_count, song.vote_score) == (1, 0, 1)
            assert SongVote.query.filter_by(song_id=song_id).count() == 1
    finally:
        with vote_app.app_context():
            db.session.remove()
            db.drop_all()


def test_media_vote_conflict_snapshot_is_materialized_before_lock_release(tmp_path, monkeypatch):
    db_path = tmp_path / 'media-vote-conflict-snapshot.db'

    class FileTestingConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'

    vote_app = create_app(FileTestingConfig)
    _reset_file_vote_schema(vote_app)
    try:
        seed_client = vote_app.test_client()
        song_id = _song(seed_client, 'Conflict snapshot')
        media_id = _media(vote_app, song_id, 'snapshot.mp3')
        assert _vote(seed_client, media_id, 1).status_code == 200
        original_to_dict = Media.to_dict
        snapshot_started = threading.Event()
        release_snapshot = threading.Event()
        writer_locked = threading.Event()

        def held_conflict_to_dict(media, viewer_vote=0):
            if media.id == media_id and viewer_vote == 1 and not snapshot_started.is_set():
                snapshot_started.set()
                probe = sqlite3.connect(db_path, timeout=0.1)
                try:
                    try:
                        probe.execute('BEGIN IMMEDIATE')
                    except sqlite3.OperationalError:
                        writer_locked.set()
                    else:
                        probe.rollback()
                finally:
                    probe.close()
                assert release_snapshot.wait(timeout=5)
            return original_to_dict(media, viewer_vote=viewer_vote)

        monkeypatch.setattr(Media, 'to_dict', held_conflict_to_dict)

        def stale_cancel():
            with vote_app.test_client() as thread_client:
                return thread_client.patch(
                    f'/media/{media_id}/vote',
                    json={'vote': 0, 'expected_viewer_vote': 0},
                    headers={'X-Voter-ID': VOTER_A},
                )

        def current_switch():
            with vote_app.test_client() as thread_client:
                return thread_client.patch(
                    f'/media/{media_id}/vote',
                    json={'vote': -1, 'expected_viewer_vote': 1},
                    headers={'X-Voter-ID': VOTER_A},
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            stale_future = executor.submit(stale_cancel)
            assert snapshot_started.wait(timeout=5)
            switch_future = executor.submit(current_switch)
            release_snapshot.set()
            stale_response = stale_future.result(timeout=5)
            switch_response = switch_future.result(timeout=5)

        assert writer_locked.is_set()
        assert stale_response.status_code == 409
        snapshot = stale_response.get_json()['media']
        assert (snapshot['viewer_vote'], snapshot['upvote_count'], snapshot['downvote_count'], snapshot['vote_score']) == (1, 1, 0, 1)
        assert switch_response.status_code == 200
        with vote_app.app_context():
            media = db.session.get(Media, media_id)
            assert (media.upvote_count, media.downvote_count, media.vote_score) == (0, 1, -1)
    finally:
        with vote_app.app_context():
            db.session.remove()
            db.drop_all()
