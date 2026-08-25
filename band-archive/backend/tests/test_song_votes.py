import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask

from app import _run_migrations, create_app
from config import TestingConfig
from extensions import db
from models import Song, SongVote


VOTER_A = '123e4567-e89b-12d3-a456-426614174000'
VOTER_B = '123e4567-e89b-12d3-a456-426614174001'


def _song(client, title='Vote song'):
    return client.post('/songs', json={'title': title, 'artist': 'Band'}).get_json()['id']


def _vote(client, song_id, value, voter=VOTER_A, expected=0):
    return client.patch(
        f'/songs/{song_id}/vote', json={'vote': value, 'expected_viewer_vote': expected},
        headers={'X-Voter-ID': voter},
    )


def test_song_vote_first_repeat_switch_and_cancel_are_idempotent(client, app):
    song_id = _song(client)
    first = _vote(client, song_id, 1)
    assert first.status_code == 200
    assert first.get_json()['upvote_count'] == 1
    assert first.get_json()['downvote_count'] == 0
    assert first.get_json()['vote_score'] == 1
    assert first.get_json()['viewer_vote'] == 1

    repeated = _vote(client, song_id, 1, expected=1)
    assert repeated.status_code == 200
    assert repeated.get_json()['upvote_count'] == 1
    with app.app_context():
        assert SongVote.query.filter_by(song_id=song_id).count() == 1

    switched = _vote(client, song_id, -1, expected=1)
    assert switched.get_json()['upvote_count'] == 0
    assert switched.get_json()['downvote_count'] == 1
    assert switched.get_json()['vote_score'] == -1
    assert switched.get_json()['viewer_vote'] == -1

    cancelled = _vote(client, song_id, 0, expected=-1)
    assert cancelled.get_json()['upvote_count'] == 0
    assert cancelled.get_json()['downvote_count'] == 0
    assert cancelled.get_json()['vote_score'] == 0
    assert cancelled.get_json()['viewer_vote'] == 0
    with app.app_context():
        assert SongVote.query.filter_by(song_id=song_id).count() == 0


def test_song_vote_rejects_stale_targets_without_mutating_then_allows_retry(client, app):
    song_id = _song(client)
    assert _vote(client, song_id, 1, expected=0).status_code == 200
    for stale_target in (1, -1, 0):
        conflict = _vote(client, song_id, stale_target, expected=0)
        assert conflict.status_code == 409
        payload = conflict.get_json()
        assert payload['error'] == 'vote_conflict'
        assert payload['code'] == 'vote_conflict'
        assert payload['song']['viewer_vote'] == 1
        assert payload['song']['upvote_count'] == 1
        assert payload['song']['downvote_count'] == 0
        assert payload['song']['vote_score'] == 1
        assert VOTER_A not in conflict.get_data(as_text=True)
    with app.app_context():
        assert SongVote.query.filter_by(song_id=song_id, value=1).count() == 1

    retry = _vote(client, song_id, -1, expected=1)
    assert retry.status_code == 200
    assert retry.get_json()['viewer_vote'] == -1
    assert retry.get_json()['upvote_count'] == 0
    assert retry.get_json()['downvote_count'] == 1


def test_song_vote_aggregates_viewer_vote_and_deterministic_score_sorting(client):
    first_id = _song(client, 'First')
    second_id = _song(client, 'Second')
    third_id = _song(client, 'Third')
    _vote(client, second_id, 1, VOTER_A)
    _vote(client, second_id, 1, VOTER_B)
    _vote(client, third_id, -1, VOTER_B)

    response = client.get('/songs', headers={'X-Voter-ID': VOTER_A})
    assert response.status_code == 200
    songs = response.get_json()
    assert [song['id'] for song in songs] == [second_id, first_id, third_id]
    assert songs[0]['upvote_count'] == 2
    assert songs[0]['vote_score'] == 2
    assert songs[0]['viewer_vote'] == 1
    assert songs[1]['viewer_vote'] == 0
    assert songs[2]['downvote_count'] == 1
    assert songs[2]['viewer_vote'] == 0

    no_header = client.get(f'/songs/{second_id}').get_json()
    assert no_header['viewer_vote'] == 0
    assert client.get(f'/songs/{second_id}', headers={'X-Voter-ID': VOTER_B}).get_json()['viewer_vote'] == 1


def test_song_vote_rejects_invalid_identity_or_value_without_exposing_raw_identity(client, app):
    song_id = _song(client)
    assert client.patch(f'/songs/{song_id}/vote', json={'vote': 1, 'expected_viewer_vote': 0}).status_code == 400
    assert client.patch(
        f'/songs/{song_id}/vote', json={'vote': 1, 'expected_viewer_vote': 0},
        headers={'X-Voter-ID': 'not-a-uuid'},
    ).status_code == 400
    assert client.get('/songs', headers={'X-Voter-ID': 'not-a-uuid'}).status_code == 400
    assert client.patch(f'/songs/{song_id}/vote', json={'vote': 1}, headers={'X-Voter-ID': VOTER_A}).status_code == 400
    assert _vote(client, song_id, 1, expected=2).status_code == 400
    assert _vote(client, song_id, True).status_code == 400
    assert _vote(client, song_id, '1').status_code == 400

    assert _vote(client, song_id, 1).status_code == 200
    response = client.get('/songs', headers={'X-Voter-ID': VOTER_A})
    assert VOTER_A not in response.get_data(as_text=True)
    with app.app_context():
        vote = SongVote.query.filter_by(song_id=song_id).first()
        assert vote is not None
        assert vote.voter_hash != VOTER_A
        assert len(vote.voter_hash) == 64


def test_song_delete_cascades_votes_and_keeps_counters_nonnegative(client, app):
    song_id = _song(client)
    _vote(client, song_id, 1, VOTER_A)
    _vote(client, song_id, -1, VOTER_B)
    _vote(client, song_id, 0, VOTER_A, expected=1)
    state = client.get(f'/songs/{song_id}').get_json()
    assert state['upvote_count'] >= 0
    assert state['downvote_count'] >= 0

    assert client.delete(f'/songs/{song_id}').status_code == 200
    with app.app_context():
        assert SongVote.query.filter_by(song_id=song_id).count() == 0


def test_song_vote_sqlite_migration_backfills_counters_and_is_idempotent(tmp_path):
    db_path = tmp_path / 'legacy-votes.db'
    connection = sqlite3.connect(db_path)
    connection.executescript('''
        CREATE TABLE song (id INTEGER PRIMARY KEY, title VARCHAR(100), artist VARCHAR(100));
        CREATE TABLE media (id INTEGER PRIMARY KEY, file_type VARCHAR(20));
        CREATE TABLE personal_log (id INTEGER PRIMARY KEY, file_type VARCHAR(20));
        CREATE TABLE rehearsal (id INTEGER PRIMARY KEY);
        INSERT INTO song (id, title, artist) VALUES (1, 'Legacy', 'Band');
    ''')
    connection.commit()
    connection.close()

    flask_app = Flask(__name__)
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    _run_migrations(flask_app)

    connection = sqlite3.connect(db_path)
    connection.executemany(
        'INSERT INTO song_vote (song_id, voter_hash, value, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        [(1, 'a' * 64, 1, '2026-01-01', '2026-01-01'), (1, 'b' * 64, -1, '2026-01-01', '2026-01-01')],
    )
    connection.execute('UPDATE song SET upvote_count = 99, downvote_count = 99, vote_score = 0 WHERE id = 1')
    connection.commit()
    connection.close()

    _run_migrations(flask_app)
    _run_migrations(flask_app)
    connection = sqlite3.connect(db_path)
    row = connection.execute(
        'SELECT upvote_count, downvote_count, vote_score FROM song WHERE id = 1'
    ).fetchone()
    vote_count = connection.execute('SELECT COUNT(*) FROM song_vote WHERE song_id = 1').fetchone()[0]
    indexes = {row[1] for row in connection.execute('PRAGMA index_list(song_vote)')}
    connection.close()
    assert row == (1, 1, 0)
    assert vote_count == 2
    assert 'ix_song_vote_song_id' in indexes


def test_song_vote_migration_runs_for_partial_legacy_song_database(tmp_path):
    db_path = tmp_path / 'partial-song.db'
    connection = sqlite3.connect(db_path)
    connection.executescript('''
        CREATE TABLE song (id INTEGER PRIMARY KEY, title VARCHAR(100), artist VARCHAR(100));
        INSERT INTO song (id, title, artist) VALUES (7, 'Partial', 'Band');
    ''')
    connection.commit()
    connection.close()

    flask_app = Flask(__name__)
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    _run_migrations(flask_app)
    _run_migrations(flask_app)

    connection = sqlite3.connect(db_path)
    song_columns = {row[1] for row in connection.execute('PRAGMA table_info(song)')}
    vote_tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'song_vote'"
    ).fetchone()
    row = connection.execute(
        'SELECT id, title, artist, upvote_count, downvote_count, vote_score FROM song WHERE id = 7'
    ).fetchone()
    connection.close()
    assert {'upvote_count', 'downvote_count', 'vote_score'} <= song_columns
    assert vote_tables == ('song_vote',)
    assert row == (7, 'Partial', 'Band', 0, 0, 0)


def test_song_vote_file_sqlite_concurrency_preserves_unique_row_and_counters(tmp_path):
    db_path = tmp_path / 'vote-concurrency.db'

    class FileTestingConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'

    vote_app = create_app(FileTestingConfig)
    try:
        seed_client = vote_app.test_client()
        song_id = _song(seed_client, 'Concurrent')
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


def test_vote_conflict_snapshot_is_materialized_before_lock_release(tmp_path, monkeypatch):
    db_path = tmp_path / 'vote-conflict-snapshot.db'

    class FileTestingConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'

    vote_app = create_app(FileTestingConfig)
    try:
        seed_client = vote_app.test_client()
        song_id = _song(seed_client, 'Conflict snapshot')
        assert _vote(seed_client, song_id, 1, expected=0).status_code == 200
        original_to_dict = Song.to_dict
        snapshot_started = threading.Event()
        release_snapshot = threading.Event()
        writer_locked = threading.Event()

        def held_conflict_to_dict(song, viewer_vote=0):
            if song.id == song_id and viewer_vote == 1 and not snapshot_started.is_set():
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
            return original_to_dict(song, viewer_vote=viewer_vote)

        monkeypatch.setattr(Song, 'to_dict', held_conflict_to_dict)

        def stale_cancel():
            with vote_app.test_client() as thread_client:
                return thread_client.patch(
                    f'/songs/{song_id}/vote',
                    json={'vote': 0, 'expected_viewer_vote': 0},
                    headers={'X-Voter-ID': VOTER_A},
                )

        def current_switch():
            with vote_app.test_client() as thread_client:
                return thread_client.patch(
                    f'/songs/{song_id}/vote',
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
        assert stale_response.get_json()['song']['viewer_vote'] == 1
        assert stale_response.get_json()['song']['upvote_count'] == 1
        assert stale_response.get_json()['song']['downvote_count'] == 0
        assert stale_response.get_json()['song']['vote_score'] == 1
        assert switch_response.status_code == 200
        assert switch_response.get_json()['viewer_vote'] == -1
        with vote_app.app_context():
            song = db.session.get(Song, song_id)
            assert (song.upvote_count, song.downvote_count, song.vote_score) == (0, 1, -1)
    finally:
        with vote_app.app_context():
            db.session.remove()
            db.drop_all()
