import sqlite3

import pytest
from flask import Flask

from app import _run_migrations
from extensions import db
from models import Comment, Media, Member, PersonalLog


def _song_id(client):
    response = client.post('/songs', json={'title': 'Timestamp song', 'artist': 'Band'})
    assert response.status_code == 201
    return response.get_json()['id']


def _media_id(app, song_id, file_type='audio'):
    with app.app_context():
        media = Media(
            song_id=song_id,
            filename=f'take.{file_type}',
            original_filename=f'take.{file_type}',
            file_type=file_type,
        )
        db.session.add(media)
        db.session.commit()
        return media.id


def _personal_log_id(app, file_type='video'):
    with app.app_context():
        member = Member(name='Timestamp member', instrument='Guitar')
        db.session.add(member)
        db.session.flush()
        log = PersonalLog(
            member_id=member.id,
            title='Timestamp log',
            filename=f'log.{file_type}',
            original_filename=f'log.{file_type}',
            file_type=file_type,
        )
        db.session.add(log)
        db.session.commit()
        return log.id


def _payload(**overrides):
    payload = {'author': 'Band member', 'password': 'secret', 'content': 'Check this part'}
    payload.update(overrides)
    return payload


def test_media_comment_timestamps_are_created_returned_and_legacy_comments_are_null(client, app):
    media_id = _media_id(app, _song_id(client), 'audio')

    legacy = client.post(f'/media/{media_id}/comments', json=_payload())
    assert legacy.status_code == 201
    assert legacy.get_json()['timestamp_seconds'] is None

    timestamped = client.post(
        f'/media/{media_id}/comments', json=_payload(timestamp_seconds=0),
    )
    assert timestamped.status_code == 201
    assert timestamped.get_json()['timestamp_seconds'] == 0

    response = client.get(f'/media/{media_id}/comments')
    assert response.status_code == 200
    assert [item['timestamp_seconds'] for item in response.get_json()] == [None, 0]


def test_personal_log_video_comment_accepts_timestamp(client, app):
    log_id = _personal_log_id(app, 'video')
    response = client.post(
        f'/personal-logs/{log_id}/comments', json=_payload(timestamp_seconds=81.25),
    )
    assert response.status_code == 201
    assert response.get_json()['timestamp_seconds'] == 81.25


@pytest.mark.parametrize('timestamp', [True, '1.5', -1, float('nan'), float('inf'), float('-inf'), 10 ** 400])
def test_timestamp_rejects_non_numeric_negative_non_finite_and_overflow_values(client, app, timestamp):
    media_id = _media_id(app, _song_id(client), 'video')
    response = client.post(
        f'/media/{media_id}/comments', json=_payload(timestamp_seconds=timestamp),
    )
    assert response.status_code == 400
    assert response.get_json()['error'] == 'timestamp_seconds must be a non-negative finite number'


@pytest.mark.parametrize('target_kind,file_type', [('media', 'image'), ('personal-log', 'document')])
def test_timestamp_requires_audio_or_video_target(client, app, target_kind, file_type):
    if target_kind == 'media':
        target_id = _media_id(app, _song_id(client), file_type)
        path = f'/media/{target_id}/comments'
    else:
        target_id = _personal_log_id(app, file_type)
        path = f'/personal-logs/{target_id}/comments'

    rejected = client.post(path, json=_payload(timestamp_seconds=12))
    assert rejected.status_code == 400
    assert rejected.get_json()['error'] == 'Timestamps are only available for audio or video comments'

    ordinary = client.post(path, json=_payload(timestamp_seconds=None))
    assert ordinary.status_code == 201
    assert ordinary.get_json()['timestamp_seconds'] is None


def test_timestamp_update_preserves_changes_and_clears_without_mutating_on_password_failure(client, app):
    media_id = _media_id(app, _song_id(client), 'audio')
    created = client.post(
        f'/media/{media_id}/comments', json=_payload(timestamp_seconds=10),
    ).get_json()
    path = f"/comments/{created['id']}"

    preserved = client.put(path, json={'password': 'secret', 'content': 'Edited only'})
    assert preserved.status_code == 200
    assert preserved.get_json()['timestamp_seconds'] == 10

    changed = client.put(path, json={
        'password': 'secret', 'content': 'Edited with a new position', 'timestamp_seconds': 20.5,
    })
    assert changed.status_code == 200
    assert changed.get_json()['timestamp_seconds'] == 20.5

    invalid = client.put(path, json={
        'password': 'secret', 'content': 'Should not persist', 'timestamp_seconds': -1,
    })
    assert invalid.status_code == 400

    failed = client.put(path, json={
        'password': 'wrong', 'content': 'Should not persist', 'timestamp_seconds': None,
    })
    assert failed.status_code == 400
    with app.app_context():
        comment = db.session.get(Comment, created['id'])
        assert comment.content == 'Edited with a new position'
        assert comment.timestamp_seconds == 20.5

    cleared = client.put(path, json={
        'password': 'secret', 'content': 'Edited and cleared', 'timestamp_seconds': None,
    })
    assert cleared.status_code == 200
    assert cleared.get_json()['timestamp_seconds'] is None


def test_replies_keep_parent_timestamp_but_cannot_receive_their_own(client, app):
    media_id = _media_id(app, _song_id(client), 'video')
    parent = client.post(
        f'/media/{media_id}/comments', json=_payload(timestamp_seconds=32),
    ).get_json()

    rejected = client.post(
        f"/comments/{parent['id']}/replies", json=_payload(timestamp_seconds=1),
    )
    assert rejected.status_code == 400
    assert rejected.get_json()['error'] == 'Replies cannot have a timestamp'

    reply = client.post(
        f"/comments/{parent['id']}/replies", json=_payload(content='Agreed', timestamp_seconds=None),
    )
    assert reply.status_code == 201
    assert reply.get_json()['timestamp_seconds'] is None

    comments = client.get(f'/media/{media_id}/comments').get_json()
    assert comments[0]['timestamp_seconds'] == 32
    assert comments[0]['replies'][0]['timestamp_seconds'] is None

    rejected_update = client.put(
        f"/comments/{reply.get_json()['id']}",
        json={'password': 'secret', 'content': 'Still agreed', 'timestamp_seconds': 4},
    )
    assert rejected_update.status_code == 400
    assert client.get(f'/media/{media_id}/comments').get_json()[0]['replies'][0]['timestamp_seconds'] is None


def test_comment_target_not_found_behavior_remains_unchanged(client):
    assert client.post('/media/999/comments', json=_payload(timestamp_seconds=1)).status_code == 404
    assert client.post('/personal-logs/999/comments', json=_payload(timestamp_seconds=1)).status_code == 404


def test_comment_timestamp_migration_is_additive_and_idempotent(tmp_path):
    db_path = tmp_path / 'legacy-comments.db'
    connection = sqlite3.connect(db_path)
    connection.execute('''
        CREATE TABLE comment (
            id INTEGER PRIMARY KEY,
            media_id INTEGER,
            personal_log_id INTEGER,
            parent_id INTEGER,
            author VARCHAR(50) NOT NULL,
            password_hash VARCHAR(200) NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME
        )
    ''')
    connection.execute(
        "INSERT INTO comment (id, author, password_hash, content) VALUES (1, 'Legacy', 'hash', 'Old comment')"
    )
    connection.commit()
    connection.close()

    migration_app = Flask(__name__)
    migration_app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    _run_migrations(migration_app)
    _run_migrations(migration_app)

    connection = sqlite3.connect(db_path)
    columns = {row[1] for row in connection.execute('PRAGMA table_info(comment)').fetchall()}
    row = connection.execute('SELECT timestamp_seconds FROM comment WHERE id = 1').fetchone()
    connection.close()
    assert 'timestamp_seconds' in columns
    assert row == (None,)
