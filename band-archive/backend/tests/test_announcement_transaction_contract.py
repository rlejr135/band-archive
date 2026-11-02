"""Existing announcement write authority, response and transaction contracts."""

import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db
from models import Announcement, AppUser, AuditEvent, Band, BandMembership, R2DeletionTask
import routes.content_writes as writes
from storage import storage
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import _band_id, principals, write_client  # noqa: F401


_EVENTS = ('content.announcement.created', 'content.announcement.updated',
           'content.announcement.cleared', 'content.public_visibility_confirmed')


def _seed(app, band_id):
    with app.app_context():
        author = BandMembership.query.filter_by(
            band_id=band_id, role='member', status='active').one()
        announcement = Announcement(band_id=band_id, created_by_membership_id=author.id,
                                    visibility='MEMBERS', content='Original announcement')
        db.session.add(announcement)
        db.session.commit()
        return announcement.id


def _snapshot(app):
    with app.app_context():
        db.session.remove()
        return {
            'announcements': [tuple(getattr(row, column.name) for column in Announcement.__table__.columns)
                              for row in Announcement.query.order_by(Announcement.id)],
            'audits': [(row.event_type, row.band_id, row.actor_user_id, row.target_type,
                        row.target_id, row.details_json) for row in AuditEvent.query.filter(
                            AuditEvent.event_type.in_(_EVENTS)).order_by(AuditEvent.id)],
            'deletion_tasks': R2DeletionTask.query.count(),
        }


def _request(client, headers, band_id, operation):
    path = f'/api/v2/bands/{band_id}/announcement'
    return (client.delete(path, headers=headers['owner']) if operation == 'clear'
            else client.put(path, headers=headers['admin'], json={'content': 'Updated announcement'}))


@pytest.mark.parametrize('operation', ['create', 'update', 'clear'])
@pytest.mark.parametrize('failure', ['audit_flush', 'commit', 'serializer', 'jsonify'])
def test_announcement_failure_rolls_back_content_metadata_and_audit(
        access_app, write_client, principals, monkeypatch, operation, failure):
    band_id = _band_id(access_app)
    if operation != 'create':
        _seed(access_app, band_id)
    before = _snapshot(access_app)
    observed = []

    def business_audits(session):
        return any(isinstance(row, AuditEvent) and row.event_type in _EVENTS for row in session.new)

    def before_flush(session, context, instances):
        if business_audits(session):
            session.info['announcement_failure_contract'] = True
            if failure == 'audit_flush':
                observed.append(failure)
                raise RuntimeError('injected announcement audit flush failure')

    def before_commit(session):
        if failure == 'commit' and (business_audits(session)
                                    or session.info.get('announcement_failure_contract')):
            observed.append(failure)
            raise RuntimeError('injected announcement commit failure')

    def fail(*args, **kwargs):
        observed.append(failure)
        raise RuntimeError('injected announcement response preparation failure')

    if failure in {'serializer', 'jsonify'}:
        monkeypatch.setattr(writes, 'serialize_announcement' if failure == 'serializer' else 'jsonify', fail)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected announcement'):
            _request(write_client, principals, band_id, operation)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert observed == [failure]
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('operation', ['create', 'update', 'clear'])
def test_announcement_private_response_is_prepared_before_commit(
        access_app, write_client, principals, monkeypatch, operation):
    band_id = _band_id(access_app)
    announcement_id = None if operation == 'create' else _seed(access_app, band_id)
    phases = []
    original_jsonify = writes.jsonify

    def jsonify(*args, **kwargs):
        phases.append('response')
        db.session.info['announcement_response_contract'] = True
        return original_jsonify(*args, **kwargs)

    def before_commit(session):
        if session.info.pop('announcement_response_contract', False):
            phases.append('commit')

    monkeypatch.setattr(writes, 'jsonify', jsonify)
    event.listen(Session, 'before_commit', before_commit)
    try:
        response = _request(write_client, principals, band_id, operation)
    finally:
        event.remove(Session, 'before_commit', before_commit)
    assert phases == ['response', 'commit']
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    payload = response.get_json()
    assert set(payload) == ({'announcement', 'cleared'} if operation == 'clear' else {'announcement'})
    saved = payload['announcement']
    assert saved['content'] == ('' if operation == 'clear' else 'Updated announcement')
    assert saved['visibility'] == 'MEMBERS'
    if announcement_id is not None:
        assert saved['id'] == announcement_id
    after = _snapshot(access_app)
    assert len(after['announcements']) == len(after['audits']) == 1
    event_type, audit_band, actor, target_type, target, details = after['audits'][0]
    assert event_type == f'content.announcement.{dict(create="created", update="updated", clear="cleared")[operation]}'
    assert audit_band == band_id and actor is not None and target_type == 'Announcement'
    assert target == str(saved['id'])
    assert (json.loads(details) if details else None) == (
        {'visibility': 'MEMBERS'} if operation == 'create' else
        {'fields': ['content']} if operation == 'update' else None)


def test_announcement_updates_and_repeated_clear_retain_singleton_author_and_visibility_without_external_work(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    announcement_id = _seed(access_app, band_id)
    with access_app.app_context():
        original = db.session.get(Announcement, announcement_id)
        identity = (original.id, original.band_id, original.created_by_membership_id, original.visibility)

    def forbidden(*args, **kwargs):
        pytest.fail('announcement text changes must not access R2 or enqueue deletion')

    for name in ('generate_url', 'head', 'delete'):
        monkeypatch.setattr(storage, name, forbidden)
    updated = _request(write_client, principals, band_id, 'update')
    first = _request(write_client, principals, band_id, 'clear')
    second = _request(write_client, principals, band_id, 'clear')
    assert updated.status_code == first.status_code == second.status_code == 200
    assert first.get_json()['cleared'] is second.get_json()['cleared'] is True
    with access_app.app_context():
        db.session.remove()
        row = Announcement.query.filter_by(band_id=band_id).one()
        assert (row.id, row.band_id, row.created_by_membership_id, row.visibility) == identity
        assert row.content == ''
    after = _snapshot(access_app)
    assert [row[0] for row in after['audits']] == [
        'content.announcement.updated', 'content.announcement.cleared', 'content.announcement.cleared']
    assert after['deletion_tasks'] == 0


@pytest.mark.parametrize(('role', 'method', 'status'), [
    ('member', 'put', 403), ('admin', 'put', 200), ('owner', 'put', 200),
    ('member', 'delete', 403), ('admin', 'delete', 403), ('owner', 'delete', 200),
])
def test_announcement_put_and_clear_keep_distinct_existing_role_requirements(
        access_app, write_client, principals, role, method, status):
    band_id = _band_id(access_app)
    _seed(access_app, band_id)
    before = _snapshot(access_app)
    path = f'/api/v2/bands/{band_id}/announcement'
    response = (write_client.put(path, headers=principals[role], json={'content': 'Role update'})
                if method == 'put' else write_client.delete(path, headers=principals[role]))
    assert response.status_code == status
    if status != 200:
        assert _snapshot(access_app) == before


@pytest.mark.parametrize('method', ['put', 'delete'])
def test_nonmember_platform_admin_does_not_bypass_announcement_active_band_scope(
        access_app, write_client, principals, method):
    band_id = _band_id(access_app)
    _seed(access_app, band_id)
    with access_app.app_context():
        AppUser.query.filter_by(email='outsider@example.com').one().is_platform_admin = True
        db.session.commit()
    before = _snapshot(access_app)
    path = f'/api/v2/bands/{band_id}/announcement'
    response = (write_client.put(path, headers=principals['outsider'], json={'content': 'No bypass'})
                if method == 'put' else write_client.delete(path, headers=principals['outsider']))
    assert response.status_code == 403
    assert response.get_json() == {'error': 'An active band membership is required.'}
    assert _snapshot(access_app) == before


@pytest.mark.parametrize(('body', 'enters_transaction'), [
    (None, False), ({'content': 'Text', 'unsupported': True}, False), ({}, True),
])
def test_announcement_payload_shape_precedes_lock_but_missing_content_follows_scope(
        access_app, write_client, principals, monkeypatch, body, enters_transaction):
    band_id = _band_id(access_app)
    phases = []
    original_user = writes.current_app_user
    original_scope = writes.active_member_write_scope

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
        response = write_client.put(f'/api/v2/bands/{band_id}/announcement',
                                    headers=principals['admin'], json=body)
    finally:
        event.remove(engine, 'before_cursor_execute', before_execute)
    assert response.status_code == 400
    assert phases == (['principal', 'begin', 'principal', 'scope'] if enters_transaction else ['principal'])
    assert _snapshot(access_app)['announcements'] == []


def test_announcement_public_expansion_confirmation_precedes_content_validation_and_its_audit_rolls_back(
        access_app, write_client, principals):
    from tests.test_upload_content_policy import _accept, _enable

    band_id = _band_id(access_app)
    announcement_id = _seed(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'PUBLIC'
        db.session.commit()
    _enable(access_app)
    assert _accept(write_client, principals['admin']).status_code == 201
    path = f'/api/v2/bands/{band_id}/announcement'
    before = _snapshot(access_app)
    denied = write_client.put(path, headers=principals['admin'], json={'content': '', 'visibility': 'PUBLIC'})
    assert denied.status_code == 409
    assert denied.get_json()['code'] == 'public_visibility_confirmation_required'
    confirmed = {'version': 'upload-v1', 'confirmed': True}
    invalid = write_client.put(path, headers=principals['admin'],
        json={'content': '', 'visibility': 'PUBLIC', 'visibility_confirm': confirmed})
    assert invalid.status_code == 400
    assert _snapshot(access_app) == before
    accepted = write_client.put(path, headers=principals['admin'],
        json={'content': 'Public announcement', 'visibility': 'PUBLIC', 'visibility_confirm': confirmed})
    assert accepted.status_code == 200 and accepted.get_json()['announcement']['id'] == announcement_id
    assert accepted.get_json()['announcement']['visibility'] == 'PUBLIC'
    assert [row[0] for row in _snapshot(access_app)['audits']] == [
        'content.public_visibility_confirmed', 'content.announcement.updated']


def test_announcement_clear_reloads_actor_with_existing_lock_flag(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    _seed(access_app, band_id)
    original_scope = writes.active_member_write_scope
    observed = []

    def scope(*args, **kwargs):
        observed.append(kwargs)
        return original_scope(*args, **kwargs)

    monkeypatch.setattr(writes, 'active_member_write_scope', scope)
    response = write_client.delete(f'/api/v2/bands/{band_id}/announcement', headers=principals['owner'])
    assert response.status_code == 200
    assert observed == [{'lock': True, 'lock_actor': True}]


def test_announcement_clear_missing_singleton_returns_404_without_creating_a_row(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    response = write_client.delete(f'/api/v2/bands/{band_id}/announcement', headers=principals['owner'])
    assert response.status_code == 404
    assert response.get_json() == {'error': 'Announcement not found'}
    assert _snapshot(access_app)['announcements'] == []
