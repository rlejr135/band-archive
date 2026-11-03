"""HTTP/JWT contracts for Rehearsal writes and their Song associations."""

from datetime import date
import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db
from models import AuditEvent, Band, BandMembership, Media, Rehearsal, R2DeletionTask, Song
import routes.content_writes as writes
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import _band_id, principals, write_client  # noqa: F401


EVENTS = ('content.rehearsal.created', 'content.rehearsal.updated', 'content.public_visibility_confirmed')


def _seed(app, band_id, *, existing=True, author_role='member'):
    with app.app_context():
        author = BandMembership.query.filter_by(band_id=band_id, role=author_role, status='active').one()
        songs = [Song(band_id=band_id, created_by_membership_id=author.id,
                      visibility='PUBLIC', title=f'Contract song {index}', artist='Artist') for index in range(2)]
        db.session.add_all(songs)
        rehearsal = None
        if existing:
            rehearsal = Rehearsal(band_id=band_id, created_by_membership_id=author.id,
                                  visibility='MEMBERS', title='Original rehearsal', date=date(2026, 11, 3),
                                  memo='Original memo', songs=[songs[0]])
            db.session.add(rehearsal)
        db.session.commit()
        return {'songs': [song.id for song in songs], 'rehearsal': rehearsal.id if rehearsal else None,
                'author': author.id}


def _snapshot(app):
    with app.app_context():
        db.session.remove()
        return {
            'rehearsals': [tuple(getattr(row, column.name) for column in Rehearsal.__table__.columns)
                           for row in Rehearsal.query.order_by(Rehearsal.id)],
            'edges': [(row.id, tuple(sorted(song.id for song in row.songs)))
                      for row in Rehearsal.query.order_by(Rehearsal.id)],
            'audits': [(row.event_type, row.band_id, row.actor_user_id, row.target_type,
                        row.target_id, row.details_json) for row in AuditEvent.query.filter(
                            AuditEvent.event_type.in_(EVENTS)).order_by(AuditEvent.id)],
            'deletion_tasks': R2DeletionTask.query.count(),
        }


def _request(client, headers, band_id, graph, operation, *, data=None):
    body = data if data is not None else {'memo': 'Updated memo', 'song_ids': [graph['songs'][1]]}
    path = f'/api/v2/bands/{band_id}/rehearsals'
    if operation == 'create':
        return client.post(path, headers=headers, json={
            'title': 'Created rehearsal', 'date': '2026-11-03', **body})
    return client.patch(f"{path}/{graph['rehearsal']}", headers=headers, json=body)


@pytest.mark.parametrize('operation', ['create', 'update'])
@pytest.mark.parametrize('failure', ['audit_flush', 'commit', 'serializer', 'jsonify'])
def test_rehearsal_failure_rolls_back_record_associations_and_audit(
        access_app, write_client, principals, monkeypatch, operation, failure):
    band_id = _band_id(access_app)
    graph = _seed(access_app, band_id, existing=operation == 'update')
    before, observed = _snapshot(access_app), []

    def pending(session):
        return any(isinstance(row, AuditEvent) and row.event_type in EVENTS for row in session.new)

    def before_flush(session, context, instances):
        if pending(session):
            session.info['rehearsal_failure_contract'] = True
            if failure == 'audit_flush':
                observed.append(failure)
                raise RuntimeError('injected rehearsal audit failure')

    def before_commit(session):
        if failure == 'commit' and (pending(session) or session.info.get('rehearsal_failure_contract')):
            observed.append(failure)
            raise RuntimeError('injected rehearsal commit failure')

    def fail(*args, **kwargs):
        observed.append(failure)
        raise RuntimeError('injected rehearsal response failure')

    if failure in {'serializer', 'jsonify'}:
        monkeypatch.setattr(writes, 'serialize_rehearsal' if failure == 'serializer' else 'jsonify', fail)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected rehearsal'):
            _request(write_client, principals['member'], band_id, graph, operation)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert observed == [failure]
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_rehearsal_response_precedes_commit_and_keeps_dto_author_and_audit(
        access_app, write_client, principals, monkeypatch, operation):
    band_id = _band_id(access_app)
    graph = _seed(access_app, band_id, existing=operation == 'update')
    phases, original_jsonify = [], writes.jsonify

    def jsonify(*args, **kwargs):
        phases.append('response')
        db.session.info['rehearsal_response_contract'] = True
        return original_jsonify(*args, **kwargs)

    def before_commit(session):
        if session.info.pop('rehearsal_response_contract', False):
            phases.append('commit')

    monkeypatch.setattr(writes, 'jsonify', jsonify)
    event.listen(Session, 'before_commit', before_commit)
    try:
        response = _request(write_client, principals['member'], band_id, graph, operation)
    finally:
        event.remove(Session, 'before_commit', before_commit)
    assert phases == ['response', 'commit']
    assert response.status_code == (201 if operation == 'create' else 200)
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    assert set(response.get_json()) == {'rehearsal'}
    row = response.get_json()['rehearsal']
    assert set(row) == {'id', 'title', 'date', 'start_date', 'end_date', 'time', 'location',
                        'latitude', 'longitude', 'memo', 'color', 'rehearsal_type', 'created_at',
                        'updated_at', 'visibility', 'effective_visibility', 'can_edit', 'can_delete',
                        'can_manage', 'songs'}
    assert row['date'] == '2026-11-03' and row['rehearsal_type'] == 'UNCLASSIFIED'
    assert row['memo'] == 'Updated memo'
    assert row['visibility'] == row['effective_visibility'] == 'MEMBERS'
    assert row['can_edit'] is True and row['can_delete'] is row['can_manage'] is False
    assert [song['id'] for song in row['songs']] == [graph['songs'][1]]
    with access_app.app_context():
        assert db.session.get(Rehearsal, row['id']).created_by_membership_id == graph['author']
    audits = _snapshot(access_app)['audits']
    assert len(audits) == 1 and audits[0][1] == band_id
    assert audits[0][3:5] == ('Rehearsal', str(row['id']))
    assert audits[0][0] == f'content.rehearsal.{"created" if operation == "create" else "updated"}'
    assert json.loads(audits[0][-1]) == (
        {'visibility': 'MEMBERS'} if operation == 'create' else {'fields': ['memo', 'song_ids']})


@pytest.mark.parametrize(('body', 'enters_transaction'), [
    (None, False), ({'unsupported': True}, False), ({'date': 'invalid'}, True),
])
def test_rehearsal_request_shape_precedes_begin_but_values_follow_locked_scope(
        access_app, write_client, principals, monkeypatch, body, enters_transaction):
    band_id = _band_id(access_app)
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
    with access_app.app_context():
        engine = db.engine
    event.listen(engine, 'before_cursor_execute', before_execute)
    try:
        response = write_client.post(f'/api/v2/bands/{band_id}/rehearsals', headers=principals['member'], json=body)
    finally:
        event.remove(engine, 'before_cursor_execute', before_execute)
    assert response.status_code == 400
    assert phases == (['principal', 'begin', 'principal', 'scope'] if enters_transaction else ['principal'])


@pytest.mark.parametrize('invalid', [
    {'song_ids': [True]}, {'date': '2026-02-30'}, {'latitude': True},
    {'start_date': '2026-11-04', 'end_date': '2026-11-03'}, {'rehearsal_type': 'concert'},
])
def test_invalid_rehearsal_values_keep_record_and_edges_atomic(
        access_app, write_client, principals, invalid):
    band_id = _band_id(access_app)
    graph = _seed(access_app, band_id)
    before = _snapshot(access_app)
    response = _request(write_client, principals['member'], band_id, graph, 'update', data=invalid)
    assert response.status_code == 400
    assert _snapshot(access_app) == before


def test_rehearsal_song_resolution_deduplicates_sorts_and_rejects_foreign_links_atomically(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    graph = _seed(access_app, band_id)
    response = _request(write_client, principals['member'], band_id, graph, 'update', data={
        'song_ids': [graph['songs'][1], graph['songs'][0], graph['songs'][1]]})
    assert response.status_code == 200
    assert [song['id'] for song in response.get_json()['rehearsal']['songs']] == graph['songs']
    with access_app.app_context():
        foreign_id = Song.query.filter_by(title='B public').one().id
    before = _snapshot(access_app)
    response = _request(write_client, principals['member'], band_id, graph, 'update', data={
        'memo': 'Must roll back', 'song_ids': [graph['songs'][0], foreign_id]})
    assert response.status_code == 404 and response.get_json() == {
        'error': 'One or more songs are unavailable for this rehearsal.'}
    assert _snapshot(access_app) == before


def test_unrelated_member_cannot_edit_but_manager_can_without_changing_author(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    graph = _seed(access_app, band_id, author_role='owner')
    before = _snapshot(access_app)
    denied = _request(write_client, principals['member'], band_id, graph, 'update')
    assert denied.status_code == 403 and denied.get_json() == {'error': 'You may only edit your own content.'}
    assert _snapshot(access_app) == before
    assert _request(write_client, principals['admin'], band_id, graph, 'update').status_code == 200
    with access_app.app_context():
        assert db.session.get(Rehearsal, graph['rehearsal']).created_by_membership_id == graph['author']


def test_detaching_private_rehearsal_flushes_expires_rechecks_then_confirms_and_rolls_back_on_denial(
        access_app, write_client, principals, monkeypatch):
    from tests.test_upload_content_policy import _accept, _enable

    band_id = _band_id(access_app)
    graph = _seed(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'PUBLIC'
        db.session.add(Media(band_id=band_id, song_id=graph['songs'][0], filename='owned.mp3',
                             file_type='audio', visibility='PUBLIC', created_by_membership_id=graph['author']))
        db.session.commit()
    _enable(access_app)
    assert _accept(write_client, principals['member']).status_code == 201
    before, phases = _snapshot(access_app), []
    original_visibility, original_expire, original_confirm = (
        writes.media_visibility, db.session.expire, writes.confirm_public_expansion)

    def visibility(scope, item):
        result = original_visibility(scope, item)
        phases.append(f'media:{result}')
        return result

    def expire(instance, attribute_names=None):
        if isinstance(instance, Song) and attribute_names == ['rehearsals']:
            # Relationship writes have been flushed before invalidating the
            # inverse collection used by effective media visibility.
            assert not db.session.dirty and not db.session.new
            phases.append('expire')
        return original_expire(instance, attribute_names)

    def confirm(*args, **kwargs):
        phases.append('confirm')
        return original_confirm(*args, **kwargs)

    monkeypatch.setattr(writes, 'media_visibility', visibility)
    monkeypatch.setattr(db.session, 'expire', expire)
    monkeypatch.setattr(writes, 'confirm_public_expansion', confirm)
    denied = _request(write_client, principals['member'], band_id, graph, 'update', data={'song_ids': []})
    assert denied.status_code == 409 and denied.get_json()['code'] == 'public_visibility_confirmation_required'
    assert phases == ['media:MEMBERS', 'expire', 'media:PUBLIC', 'confirm']
    assert _snapshot(access_app) == before
    phases.clear()
    accepted = _request(write_client, principals['member'], band_id, graph, 'update', data={
        'song_ids': [], 'visibility_confirm': {'version': 'upload-v1', 'confirmed': True}})
    assert accepted.status_code == 200 and accepted.get_json()['rehearsal']['songs'] == []
    assert phases == ['media:MEMBERS', 'expire', 'media:PUBLIC', 'confirm']
    assert [row[0] for row in _snapshot(access_app)['audits']] == [
        'content.public_visibility_confirmed', 'content.rehearsal.updated']
