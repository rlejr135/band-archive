"""Actual HTTP/JWT comment transactions, including descendant rollback."""

import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db
from models import AuditEvent, BandMembership, Comment, Media, Member, PersonalLog, R2DeletionTask, Song
import routes.content_writes as writes
import comment_service as comments
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import _band_id, principals, write_client  # noqa: F401


EVENTS = tuple(f'content.comment.{verb}' for verb in ('created', 'updated', 'deleted'))


def _seed(app, band_id, kind):
    with app.app_context():
        author = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        if kind == 'media':
            song = Song(band_id=band_id, created_by_membership_id=author.id,
                        visibility='MEMBERS', title='Owned comment song', artist='Artist')
            target = Media(band_id=band_id, created_by_membership_id=author.id,
                           visibility='MEMBERS', song=song, filename='owned.mp3', file_type='audio')
        else:
            member = Member(band_id=band_id, name='Owned profile', instrument='bass')
            target = PersonalLog(band_id=band_id, created_by_membership_id=author.id,
                                 visibility='MEMBERS', member=member, title='Owned log',
                                 filename='owned.mp3', file_type='audio')
        db.session.add(target)
        db.session.flush()
        values = dict(band_id=band_id, created_by_membership_id=author.id, is_legacy=False,
                      author='Original author', **{f'{kind}_id': target.id})
        parent = Comment(content='Original parent', timestamp_seconds=4.5, **values)
        child = Comment(content='Original child', parent=parent, **values)
        db.session.add_all((parent, child))
        db.session.commit()
        return {'band': band_id, 'target': target.id, 'parent': parent.id, 'child': child.id}


def _snapshot(app):
    with app.app_context():
        db.session.remove()
        return {
            'comments': [tuple(getattr(row, column.name) for column in Comment.__table__.columns)
                         for row in Comment.query.order_by(Comment.id)],
            'audits': [(row.event_type, row.band_id, row.actor_user_id, row.target_type,
                        row.target_id, row.details_json) for row in AuditEvent.query.filter(
                            AuditEvent.event_type.in_(EVENTS)).order_by(AuditEvent.id)],
            'deletion_tasks': R2DeletionTask.query.count(),
        }


def _request(client, headers, graph, kind, operation, *, data=None):
    base = f"/api/v2/bands/{graph['band']}"
    if operation == 'create':
        resource = 'media' if kind == 'media' else 'personal-logs'
        return client.post(f"{base}/{resource}/{graph['target']}/comments", headers=headers,
                           json=data if data is not None else {'content': 'New reply', 'parent_id': graph['parent']})
    path = f"{base}/comments/{graph['parent']}"
    if operation == 'delete':
        return client.delete(path, headers=headers)
    return client.patch(path, headers=headers,
                        json=data if data is not None else {'content': 'Edited parent', 'timestamp_seconds': None})


FAILURES = [(operation, failure) for operation in ('create', 'update', 'delete')
            for failure in ('audit_flush', 'commit', 'serializer', 'jsonify')
            if not (operation == 'delete' and failure == 'serializer')]


@pytest.mark.parametrize('kind', ['media', 'personal_log'])
@pytest.mark.parametrize(('operation', 'failure'), FAILURES)
def test_comment_failure_restores_records_descendants_and_audit(
        access_app, write_client, principals, monkeypatch, kind, operation, failure):
    graph = _seed(access_app, _band_id(access_app), kind)
    before, observed = _snapshot(access_app), []

    def pending(session):
        return any(isinstance(row, AuditEvent) and row.event_type in EVENTS for row in session.new)

    def before_flush(session, context, instances):
        if pending(session):
            session.info['comment_failure_contract'] = True
            if failure == 'audit_flush':
                observed.append(failure)
                raise RuntimeError('injected comment audit failure')

    def before_commit(session):
        if failure == 'commit' and (pending(session) or session.info.get('comment_failure_contract')):
            observed.append(failure)
            raise RuntimeError('injected comment commit failure')

    def fail(*args, **kwargs):
        observed.append(failure)
        raise RuntimeError('injected comment response failure')

    if failure in {'serializer', 'jsonify'}:
        monkeypatch.setattr(writes, '_comment_response' if failure == 'serializer' else 'jsonify', fail)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected comment'):
            _request(write_client, principals['owner' if operation == 'delete' else 'member'],
                     graph, kind, operation)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert observed == [failure]
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('kind', ['media', 'personal_log'])
@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_comment_response_precedes_commit_and_preserves_tree_and_account_contract(
        access_app, write_client, principals, monkeypatch, kind, operation):
    graph = _seed(access_app, _band_id(access_app), kind)
    phases, original_jsonify = [], writes.jsonify

    def jsonify(*args, **kwargs):
        phases.append('response')
        db.session.info['comment_response_contract'] = True
        return original_jsonify(*args, **kwargs)

    def before_commit(session):
        if session.info.pop('comment_response_contract', False):
            phases.append('commit')

    monkeypatch.setattr(writes, 'jsonify', jsonify)
    event.listen(Session, 'before_commit', before_commit)
    try:
        response = _request(write_client, principals['owner' if operation == 'delete' else 'member'],
                            graph, kind, operation)
    finally:
        event.remove(Session, 'before_commit', before_commit)
    assert response.status_code == (201 if operation == 'create' else 200)
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    assert phases == ['response', 'commit']
    payload = response.get_json()
    if operation == 'delete':
        assert payload == {'deleted': True, 'id': graph['parent']}
        assert _snapshot(access_app)['comments'] == []
        comment_id = graph['parent']
    else:
        assert set(payload) == {'comment'}
        item = payload['comment']
        comment_id = item['id']
        assert item['content'] == ('New reply' if operation == 'create' else 'Edited parent')
        assert item['parent_id'] == (graph['parent'] if operation == 'create' else None)
        assert item['timestamp_seconds'] is None
        assert item['can_edit'] is True and item['can_delete'] is False
        assert item['visibility'] == item['effective_visibility'] == 'MEMBERS'
        with access_app.app_context():
            db.session.remove()
            row = db.session.get(Comment, comment_id)
            assert row.is_legacy is False and row.password_hash is None
            assert row.author == ('member@example.com' if operation == 'create' else 'Original author')
            assert db.session.get(Comment, graph['child']) is not None
    after = _snapshot(access_app)
    audits = after['audits']
    assert len(audits) == 1
    verb = {'create': 'created', 'update': 'updated', 'delete': 'deleted'}[operation]
    assert audits[0][0] == f'content.comment.{verb}'
    assert audits[0][1] == graph['band'] and audits[0][3:5] == ('Comment', str(comment_id))
    assert json.loads(audits[0][-1]) == (
        {'target': kind} if operation == 'update' else {'target': kind, 'target_id': graph['target']})
    assert after['deletion_tasks'] == 0


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_comment_scope_keeps_existing_actor_lock_flags(
        access_app, write_client, principals, monkeypatch, operation):
    graph = _seed(access_app, _band_id(access_app), 'media')
    original_scope, flags = comments.active_member_write_scope, []

    def scope(*args, **kwargs):
        flags.append(kwargs)
        return original_scope(*args, **kwargs)

    monkeypatch.setattr(comments, 'active_member_write_scope', scope)
    response = _request(write_client, principals['owner' if operation == 'delete' else 'member'],
                        graph, 'media', operation)
    assert response.status_code == (201 if operation == 'create' else 200)
    assert flags == [{'lock': True, 'lock_actor': operation == 'delete'}]
