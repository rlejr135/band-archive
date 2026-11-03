"""Parent deletes atomically remove only result-free terminal upload history.

These are real HTTP/JWT and SQLite contracts. They do not execute storage
cleanup or a deletion worker; PostgreSQL contention is covered separately.
"""

from datetime import date

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db
from models import (AuditEvent, BandMembership, DirectUploadSession,
                    MultipartUploadPart, MultipartUploadSession, Rehearsal,
                    R2DeletionTask, Song)
import routes.content_writes as writes
from storage import storage
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import _band_id, principals, write_client  # noqa: F401


EVENTS = ('content.song.deleted', 'content.rehearsal.deleted')
PARENTS = {'song': Song, 'rehearsal': Rehearsal}


def _seed(app, band_id):
    with app.app_context():
        owner = BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').one()
        song = Song(band_id=band_id, created_by_membership_id=owner.id,
                    visibility='MEMBERS', title='Owned parent', artist='Artist')
        rehearsal = Rehearsal(band_id=band_id, created_by_membership_id=owner.id,
                              visibility='MEMBERS', title='Owned schedule', date=date(2026, 11, 4),
                              songs=[song])
        db.session.add_all((song, rehearsal))
        db.session.flush()
        values = dict(band_id=band_id, created_by_membership_id=owner.id,
                      song_id=song.id, rehearsal_id=rehearsal.id,
                      original_filename='terminal.mp4', content_type='video/mp4',
                      declared_bytes=10, capability_token_hash='owned-terminal-capability')
        direct = DirectUploadSession(kind='media', status='failed',
                                     object_key=f'bands/{band_id}/media/terminal-direct.mp4', **values)
        multipart = MultipartUploadSession(status='aborted', r2_upload_id='owned-terminal-upload',
                                           object_key=f'bands/{band_id}/media/terminal-multipart.mp4', **values)
        db.session.add_all((direct, multipart))
        db.session.flush()
        part = MultipartUploadPart(session_id=multipart.id, part_number=1,
                                   uploaded_bytes=10, etag='owned-terminal-etag')
        db.session.add(part)
        db.session.commit()
        return {'band': band_id, 'song': song.id, 'rehearsal': rehearsal.id,
                'direct': direct.id, 'multipart': multipart.id, 'part': part.id,
                'owner': owner.user_id}


def _snapshot(app):
    with app.app_context():
        db.session.remove()
        result = {}
        for model in (Song, Rehearsal, DirectUploadSession, MultipartUploadSession, MultipartUploadPart):
            result[model.__name__] = [tuple(getattr(row, column.name) for column in model.__table__.columns)
                                      for row in model.query.order_by(model.id)]
        result['edges'] = [(row.id, tuple(sorted(song.id for song in row.songs)))
                           for row in Rehearsal.query.order_by(Rehearsal.id)]
        result['audits'] = [(row.event_type, row.actor_user_id, row.band_id, row.target_type,
                             row.target_id, row.details_json) for row in AuditEvent.query.filter(
                                 AuditEvent.event_type.in_(EVENTS)).order_by(AuditEvent.id)]
        result['deletion_tasks'] = R2DeletionTask.query.count()
        return result


def _request(client, headers, graph, kind):
    path = 'songs' if kind == 'song' else 'rehearsals'
    return client.delete(f"/api/v2/bands/{graph['band']}/{path}/{graph[kind]}", headers=headers)


@pytest.mark.parametrize('kind', ['song', 'rehearsal'])
@pytest.mark.parametrize('failure', ['audit_flush', 'commit', 'jsonify'])
def test_parent_delete_failure_restores_parent_secondary_links_terminal_sessions_and_parts(
        access_app, write_client, principals, monkeypatch, kind, failure):
    graph = _seed(access_app, _band_id(access_app))
    before, observed = _snapshot(access_app), []

    def pending(session):
        return any(isinstance(row, AuditEvent) and row.event_type in EVENTS for row in session.new)

    def before_flush(session, context, instances):
        if pending(session):
            session.info['parent_delete_failure_contract'] = True
            if failure == 'audit_flush':
                observed.append(failure)
                raise RuntimeError('injected parent delete audit failure')

    def before_commit(session):
        if failure == 'commit' and (pending(session) or session.info.get('parent_delete_failure_contract')):
            observed.append(failure)
            raise RuntimeError('injected parent delete commit failure')

    def fail(*args, **kwargs):
        observed.append(failure)
        raise RuntimeError('injected parent delete JSON failure')

    if failure == 'jsonify':
        monkeypatch.setattr(writes, 'jsonify', fail)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected parent delete'):
            _request(write_client, principals['owner'], graph, kind)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert observed == [failure]
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('kind', ['song', 'rehearsal'])
def test_parent_delete_prepares_private_response_before_commit_and_preserves_the_other_parent(
        access_app, write_client, principals, monkeypatch, kind):
    graph = _seed(access_app, _band_id(access_app))
    phases, original_jsonify = [], writes.jsonify

    def jsonify(*args, **kwargs):
        phases.append('response')
        db.session.info['parent_delete_response_contract'] = True
        return original_jsonify(*args, **kwargs)

    def before_commit(session):
        if session.info.pop('parent_delete_response_contract', False):
            phases.append('commit')

    def forbidden(*args, **kwargs):
        pytest.fail('parent metadata deletion must not mutate R2 or execute a worker')

    monkeypatch.setattr(writes, 'jsonify', jsonify)
    for name in ('delete', 'delete_bounded', 'abort_multipart_upload', 'complete_multipart_upload'):
        monkeypatch.setattr(storage, name, forbidden)
    event.listen(Session, 'before_commit', before_commit)
    try:
        response = _request(write_client, principals['owner'], graph, kind)
    finally:
        event.remove(Session, 'before_commit', before_commit)
    assert response.status_code == 200
    assert response.get_json() == {'deleted': True, 'id': graph[kind]}
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    assert phases == ['response', 'commit']
    other = 'rehearsal' if kind == 'song' else 'song'
    with access_app.app_context():
        db.session.remove()
        assert db.session.get(PARENTS[kind], graph[kind]) is None
        survivor = db.session.get(PARENTS[other], graph[other])
        assert survivor is not None
        assert (survivor.songs if other == 'rehearsal' else survivor.rehearsals) == []
        assert db.session.get(DirectUploadSession, graph['direct']) is None
        assert db.session.get(MultipartUploadSession, graph['multipart']) is None
        assert db.session.get(MultipartUploadPart, graph['part']) is None
    after = _snapshot(access_app)
    assert after['audits'] == [(f'content.{kind}.deleted', graph['owner'], graph['band'],
                                PARENTS[kind].__name__, str(graph[kind]), None)]
    assert after['deletion_tasks'] == 0


@pytest.mark.parametrize('kind', ['song', 'rehearsal'])
def test_parent_delete_rechecks_current_actor_under_existing_lock_flag(
        access_app, write_client, principals, monkeypatch, kind):
    graph = _seed(access_app, _band_id(access_app))
    original_scope, observed = writes.active_member_write_scope, []

    def scope(*args, **kwargs):
        observed.append(kwargs)
        return original_scope(*args, **kwargs)

    monkeypatch.setattr(writes, 'active_member_write_scope', scope)
    assert _request(write_client, principals['owner'], graph, kind).status_code == 200
    assert observed == [{'lock': True, 'lock_actor': True}]
