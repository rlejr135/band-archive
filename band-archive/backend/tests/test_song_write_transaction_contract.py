"""HTTP/JWT contracts for the existing Song creation and editing transactions."""

import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db
from models import AuditEvent, Band, BandMembership, R2DeletionTask, Song
import routes.content_writes as writes
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import _band_id, principals, write_client  # noqa: F401


EVENTS = ('content.song.created', 'content.song.updated', 'content.public_visibility_confirmed')


def _seed(app, band_id, *, author_role='member'):
    with app.app_context():
        author = BandMembership.query.filter_by(band_id=band_id, role=author_role, status='active').one()
        song = Song(band_id=band_id, created_by_membership_id=author.id, visibility='MEMBERS',
                    title='Original song', artist='Original artist', memo='Original memo')
        db.session.add(song)
        db.session.commit()
        return song.id


def _snapshot(app):
    with app.app_context():
        db.session.remove()
        return {
            'songs': [tuple(getattr(row, column.name) for column in Song.__table__.columns)
                      for row in Song.query.order_by(Song.id)],
            'audits': [(row.event_type, row.band_id, row.actor_user_id, row.target_type,
                        row.target_id, row.details_json) for row in AuditEvent.query.filter(
                            AuditEvent.event_type.in_(EVENTS)).order_by(AuditEvent.id)],
            'deletion_tasks': R2DeletionTask.query.count(),
        }


def _request(client, headers, band_id, song_id=None, *, data=None):
    path = f'/api/v2/bands/{band_id}/songs'
    if song_id is None:
        return client.post(path, headers=headers, json=data or {'title': 'Created song', 'artist': 'Artist'})
    return client.patch(f'{path}/{song_id}', headers=headers, json=data or {'memo': 'Updated memo'})


@pytest.mark.parametrize('operation', ['create', 'update'])
@pytest.mark.parametrize('failure', ['audit_flush', 'commit', 'serializer', 'jsonify'])
def test_song_write_failure_rolls_back_record_metadata_and_audit(
        access_app, write_client, principals, monkeypatch, operation, failure):
    band_id = _band_id(access_app)
    song_id = _seed(access_app, band_id) if operation == 'update' else None
    before = _snapshot(access_app)
    observed = []

    def pending(session):
        return any(isinstance(row, AuditEvent) and row.event_type in EVENTS for row in session.new)

    def before_flush(session, context, instances):
        if pending(session):
            session.info['song_failure_contract'] = True
            if failure == 'audit_flush':
                observed.append(failure)
                raise RuntimeError('injected song audit failure')

    def before_commit(session):
        if failure == 'commit' and (pending(session) or session.info.get('song_failure_contract')):
            observed.append(failure)
            raise RuntimeError('injected song commit failure')

    def fail(*args, **kwargs):
        observed.append(failure)
        raise RuntimeError('injected song response failure')

    if failure in {'serializer', 'jsonify'}:
        monkeypatch.setattr(writes, 'serialize_song' if failure == 'serializer' else 'jsonify', fail)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected song'):
            _request(write_client, principals['member'], band_id, song_id)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert observed == [failure]
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_song_response_and_audit_are_prepared_before_commit_with_current_dto(
        access_app, write_client, principals, monkeypatch, operation):
    band_id = _band_id(access_app)
    song_id = _seed(access_app, band_id) if operation == 'update' else None
    phases = []
    original_jsonify = writes.jsonify

    def jsonify(*args, **kwargs):
        phases.append('response')
        db.session.info['song_response_contract'] = True
        return original_jsonify(*args, **kwargs)

    def before_commit(session):
        if session.info.pop('song_response_contract', False):
            phases.append('commit')

    monkeypatch.setattr(writes, 'jsonify', jsonify)
    event.listen(Session, 'before_commit', before_commit)
    try:
        response = _request(write_client, principals['member'], band_id, song_id)
    finally:
        event.remove(Session, 'before_commit', before_commit)
    assert phases == ['response', 'commit']
    assert response.status_code == (201 if operation == 'create' else 200)
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    assert set(response.get_json()) == {'song'}
    song = response.get_json()['song']
    assert set(song) == {'id', 'title', 'artist', 'status', 'chords', 'link', 'memo', 'genre',
                         'difficulty', 'upvote_count', 'downvote_count', 'vote_score', 'created_at',
                         'updated_at', 'visibility', 'effective_visibility', 'can_edit',
                         'can_delete', 'can_manage', 'rehearsals'}
    assert song['visibility'] == song['effective_visibility'] == 'MEMBERS'
    assert song['can_edit'] is True and song['can_delete'] is song['can_manage'] is False
    assert song['rehearsals'] == []
    assert song['status'] == 'Practice'
    assert song['upvote_count'] == song['downvote_count'] == song['vote_score'] == 0
    assert song['title'] == ('Created song' if operation == 'create' else 'Original song')
    assert song['memo'] == (None if operation == 'create' else 'Updated memo')
    audits = _snapshot(access_app)['audits']
    assert len(audits) == 1
    assert audits[0][0] == f'content.song.{"created" if operation == "create" else "updated"}'
    assert audits[0][1] == band_id and audits[0][3:5] == ('Song', str(song['id']))
    assert json.loads(audits[0][-1]) == (
        {'visibility': 'MEMBERS'} if operation == 'create' else {'fields': ['memo']})


@pytest.mark.parametrize(('body', 'enters_transaction'), [
    (None, False), ({'title': 'x', 'artist': 'y', 'unsupported': True}, False),
    ({'title': 'x', 'artist': 'y', 'difficulty': True}, True),
])
def test_song_request_shape_precedes_begin_but_value_validation_follows_locked_scope(
        access_app, write_client, principals, monkeypatch, body, enters_transaction):
    phases = []
    original_user, original_scope = writes.current_app_user, writes.active_member_write_scope

    def user(*args, **kwargs):
        phases.append('principal')
        return original_user(*args, **kwargs)

    def scope(*args, **kwargs):
        phases.append('scope')
        assert kwargs == {'lock': True, 'lock_actor': False}
        return original_scope(*args, **kwargs)

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        if statement.strip().upper() == 'BEGIN IMMEDIATE':
            phases.append('begin')

    monkeypatch.setattr(writes, 'current_app_user', user)
    monkeypatch.setattr(writes, 'active_member_write_scope', scope)
    band_id = _band_id(access_app)
    before = _snapshot(access_app)
    with access_app.app_context():
        engine = db.engine
    event.listen(engine, 'before_cursor_execute', before_execute)
    try:
        response = write_client.post(f'/api/v2/bands/{band_id}/songs', headers=principals['member'], json=body)
    finally:
        event.remove(engine, 'before_cursor_execute', before_execute)
    assert response.status_code == 400
    assert phases == (['principal', 'begin', 'principal', 'scope'] if enters_transaction else ['principal'])
    assert _snapshot(access_app) == before


def test_song_plain_text_link_and_ignored_ownership_keep_author_and_audit_contract(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    song_id = _seed(access_app, band_id)
    with access_app.app_context():
        author_id = db.session.get(Song, song_id).created_by_membership_id
    body = {'link': '  not a URL  ', 'genre': '  rock  ', 'difficulty': 5,
            'band_id': -1, 'created_by_membership_id': -1, 'created_by_user_id': -1, 'is_legacy': True}
    response = _request(write_client, principals['member'], band_id, song_id, data=body)
    assert response.status_code == 200
    assert response.get_json()['song']['link'] == 'not a URL'
    assert response.get_json()['song']['genre'] == 'rock'
    with access_app.app_context():
        row = db.session.get(Song, song_id)
        assert row.band_id == band_id and row.created_by_membership_id == author_id
    assert json.loads(_snapshot(access_app)['audits'][0][-1]) == {'fields': ['difficulty', 'genre', 'link']}
    before = _snapshot(access_app)
    assert _request(write_client, principals['member'], band_id, song_id, data={'link': 'x' * 201}).status_code == 400
    assert _snapshot(access_app) == before


def test_song_edit_denies_unrelated_member_but_keeps_manager_and_cross_band_rules(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    song_id = _seed(access_app, band_id, author_role='owner')
    before = _snapshot(access_app)
    denied = _request(write_client, principals['member'], band_id, song_id)
    assert denied.status_code == 403
    assert denied.get_json() == {'error': 'You may only edit your own content.'}
    assert _snapshot(access_app) == before
    assert _request(write_client, principals['admin'], band_id, song_id).status_code == 200
    with access_app.app_context():
        foreign_id = Song.query.filter_by(title='B public').one().id
    before = _snapshot(access_app)
    foreign = _request(write_client, principals['owner'], band_id, foreign_id)
    assert foreign.status_code == 404 and foreign.get_json() == {'error': 'Song not found'}
    assert _snapshot(access_app) == before


def test_song_public_expansion_checks_after_values_and_uses_effective_band_visibility(
        access_app, write_client, principals):
    from tests.test_upload_content_policy import _accept, _enable

    band_id = _band_id(access_app)
    song_id = _seed(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'PUBLIC'
        db.session.commit()
    _enable(access_app)
    assert _accept(write_client, principals['member']).status_code == 201
    before = _snapshot(access_app)
    invalid = _request(write_client, principals['member'], band_id, song_id,
                       data={'visibility': 'PUBLIC', 'difficulty': True})
    assert invalid.status_code == 400
    assert invalid.get_json() == {'error': 'difficulty must be an integer between 1 and 5.'}
    missing = _request(write_client, principals['member'], band_id, song_id, data={'visibility': 'PUBLIC'})
    assert missing.status_code == 409 and missing.get_json()['code'] == 'public_visibility_confirmation_required'
    assert _snapshot(access_app) == before
    accepted = _request(write_client, principals['member'], band_id, song_id, data={
        'visibility': 'PUBLIC', 'visibility_confirm': {'version': 'upload-v1', 'confirmed': True}})
    assert accepted.status_code == 200 and accepted.get_json()['song']['effective_visibility'] == 'PUBLIC'
    assert [row[0] for row in _snapshot(access_app)['audits']] == [
        'content.public_visibility_confirmed', 'content.song.updated']

    # PUBLIC on a MEMBERS band remains effectively MEMBERS, so no expansion confirmation is demanded.
    with access_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'MEMBERS'
        db.session.get(Song, song_id).visibility = 'MEMBERS'
        db.session.commit()
    response = _request(write_client, principals['member'], band_id, song_id, data={'visibility': 'PUBLIC'})
    assert response.status_code == 200 and response.get_json()['song']['effective_visibility'] == 'MEMBERS'
