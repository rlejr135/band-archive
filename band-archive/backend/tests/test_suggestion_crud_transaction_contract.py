"""Existing SongSuggestion CRUD validation, authority and transaction contracts."""

import json

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.orm import Session

from errors import ValidationError
from extensions import db
from models import AppUser, AuditEvent, Band, BandMembership, SongSuggestion, SongSuggestionVote
import routes.content_writes as writes
import song_suggestion_service as suggestions
import content_write_values as write_values
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import _band_id, principals, write_client  # noqa: F401


_EVENTS = ('content.suggestion.created', 'content.suggestion.updated', 'content.suggestion.deleted',
           'content.public_visibility_confirmed')
_BODY = {'title': 'New candidate', 'artist': 'Artist', 'link': 'https://example.test/new', 'memo': 'New memo'}


def _seed(app, band_id):
    with app.app_context():
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        assert db.session.execute(text('PRAGMA foreign_keys')).scalar_one() == 1
        membership = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        row = SongSuggestion(band_id=band_id, created_by_membership_id=membership.id,
                             title='Original candidate', artist='Original artist',
                             link='https://example.test/original', memo='Original memo',
                             visibility='MEMBERS', thumbs_up=1)
        db.session.add(row); db.session.flush()
        db.session.add(SongSuggestionVote(band_id=band_id, suggestion_id=row.id,
            voter_user_id=membership.user_id, created_by_membership_id=membership.id,
            is_legacy=False, value=1))
        db.session.commit()
        return row.id


def _snapshot(app):
    with app.app_context():
        db.session.remove()
        return {
            'suggestions': [tuple(getattr(row, column.name) for column in SongSuggestion.__table__.columns)
                            for row in SongSuggestion.query.order_by(SongSuggestion.id)],
            'votes': [tuple(getattr(row, column.name) for column in SongSuggestionVote.__table__.columns)
                      for row in SongSuggestionVote.query.order_by(SongSuggestionVote.id)],
            'audits': [(row.event_type, row.band_id, row.actor_user_id, row.target_type,
                        row.target_id, row.details_json) for row in AuditEvent.query.filter(
                            AuditEvent.event_type.in_(_EVENTS)).order_by(AuditEvent.id)],
        }


def _request(client, headers, band_id, suggestion_id, operation):
    path = f'/api/v2/bands/{band_id}/suggestions'
    if operation == 'create':
        return client.post(path, headers=headers['member'], json=_BODY)
    if operation == 'update':
        return client.patch(f'{path}/{suggestion_id}', headers=headers['member'], json=_BODY)
    return client.delete(f'{path}/{suggestion_id}', headers=headers['owner'])


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
@pytest.mark.parametrize('failure', ['audit_flush', 'commit', 'jsonify'])
def test_suggestion_crud_failure_rolls_back_content_votes_and_audit(
        access_app, write_client, principals, monkeypatch, operation, failure):
    band_id = _band_id(access_app)
    suggestion_id = _seed(access_app, band_id)
    before = _snapshot(access_app)
    observed = []

    def business_audits(session):
        return any(isinstance(row, AuditEvent) and row.event_type in _EVENTS for row in session.new)

    def before_flush(session, context, instances):
        if business_audits(session):
            session.info['suggestion_crud_test'] = True
            if failure == 'audit_flush':
                observed.append(failure)
                raise RuntimeError('injected suggestion audit flush failure')

    def before_commit(session):
        if failure == 'commit' and (business_audits(session) or session.info.get('suggestion_crud_test')):
            observed.append(failure)
            raise RuntimeError('injected suggestion commit failure')

    def fail_jsonify(*args, **kwargs):
        observed.append(failure)
        raise RuntimeError('injected suggestion jsonify failure')

    if failure == 'jsonify':
        monkeypatch.setattr(writes, 'jsonify', fail_jsonify)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected suggestion'):
            _request(write_client, principals, band_id, suggestion_id, operation)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert observed == [failure]
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_suggestion_serializer_failure_rolls_back_before_commit(
        access_app, write_client, principals, monkeypatch, operation):
    band_id = _band_id(access_app)
    suggestion_id = _seed(access_app, band_id)
    before = _snapshot(access_app)

    def fail(*args, **kwargs):
        raise RuntimeError('injected suggestion serializer failure')

    monkeypatch.setattr(writes, 'serialize_suggestion', fail)
    with pytest.raises(RuntimeError, match='injected suggestion serializer'):
        _request(write_client, principals, band_id, suggestion_id, operation)
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_suggestion_crud_prepares_private_response_before_commit(
        access_app, write_client, principals, monkeypatch, operation):
    band_id = _band_id(access_app)
    suggestion_id = _seed(access_app, band_id)
    phases = []
    original_jsonify = writes.jsonify

    def jsonify(*args, **kwargs):
        phases.append('response')
        db.session.info['suggestion_response_test'] = True
        return original_jsonify(*args, **kwargs)

    def before_commit(session):
        if session.info.pop('suggestion_response_test', False):
            phases.append('commit')

    monkeypatch.setattr(writes, 'jsonify', jsonify)
    event.listen(Session, 'before_commit', before_commit)
    try:
        response = _request(write_client, principals, band_id, suggestion_id, operation)
    finally:
        event.remove(Session, 'before_commit', before_commit)
    assert phases == ['response', 'commit']
    assert response.status_code == (201 if operation == 'create' else 200)
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    payload = response.get_json()
    after = _snapshot(access_app)
    assert len(after['audits']) == 1
    event_type, audit_band, actor_id, target_type, target_id, details = after['audits'][0]
    assert event_type == {
        'create': 'content.suggestion.created', 'update': 'content.suggestion.updated',
        'delete': 'content.suggestion.deleted',
    }[operation]
    assert audit_band == band_id and actor_id is not None and target_type == 'SongSuggestion'
    if operation == 'delete':
        assert payload == {'deleted': True, 'id': suggestion_id}
        assert after['suggestions'] == after['votes'] == []
        assert target_id == str(suggestion_id) and details is None
    else:
        assert set(payload) == {'suggestion'}
        saved = payload['suggestion']
        assert {key: saved[key] for key in _BODY} == _BODY
        assert saved['visibility'] == 'MEMBERS' and saved['viewer_vote'] == 0
        assert target_id == str(saved['id'])
        assert len(after['votes']) == 1
        assert json.loads(details) == ({'visibility': 'MEMBERS'} if operation == 'create'
                                       else {'fields': sorted(_BODY)})


@pytest.mark.parametrize(('body', 'expected_status', 'enters_transaction'), [
    ({**_BODY, 'unsupported': True}, 400, False),
    ({'artist': 'Artist', 'link': 'https://example.test/new'}, 400, True),
])
def test_suggestion_payload_shape_precedes_lock_but_required_value_validation_follows_scope(
        access_app, write_client, principals, monkeypatch, body, expected_status, enters_transaction):
    band_id = _band_id(access_app)
    phases = []
    original_user = writes.current_app_user
    original_scope = suggestions.active_member_write_scope

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
    monkeypatch.setattr(suggestions, 'current_app_user', user)
    monkeypatch.setattr(suggestions, 'active_member_write_scope', scope)
    with access_app.app_context():
        engine = db.engine
    event.listen(engine, 'before_cursor_execute', before_execute)
    try:
        response = write_client.post(f'/api/v2/bands/{band_id}/suggestions',
                                     headers=principals['member'], json=body)
    finally:
        event.remove(engine, 'before_cursor_execute', before_execute)
    assert response.status_code == expected_status
    assert phases == (['principal', 'begin', 'principal', 'scope'] if enters_transaction else ['principal'])
    assert _snapshot(access_app)['suggestions'] == []


def test_suggestion_public_expansion_confirmation_precedes_field_validation_and_rolls_back_its_audit(
        access_app, write_client, principals):
    from tests.test_upload_content_policy import _accept, _enable

    band_id = _band_id(access_app)
    suggestion_id = _seed(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'PUBLIC'
        db.session.commit()
    _enable(access_app)
    assert _accept(write_client, principals['member']).status_code == 201
    path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}'
    before = _snapshot(access_app)
    denied = write_client.patch(path, headers=principals['member'], json={'visibility': 'PUBLIC', 'title': ''})
    assert denied.status_code == 409
    assert denied.get_json()['code'] == 'public_visibility_confirmation_required'
    confirmed = {'version': 'upload-v1', 'confirmed': True}
    invalid = write_client.patch(path, headers=principals['member'],
        json={'visibility': 'PUBLIC', 'title': '', 'visibility_confirm': confirmed})
    assert invalid.status_code == 400
    assert _snapshot(access_app) == before
    accepted = write_client.patch(path, headers=principals['member'],
        json={'visibility': 'PUBLIC', 'visibility_confirm': confirmed})
    assert accepted.status_code == 200 and accepted.get_json()['suggestion']['visibility'] == 'PUBLIC'
    after = _snapshot(access_app)
    assert [row[0] for row in after['audits']] == ['content.public_visibility_confirmed', 'content.suggestion.updated']


def test_suggestion_delete_reloads_actor_before_capability_and_rolls_back_rejected_mutation(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    suggestion_id = _seed(access_app, band_id)
    before = _snapshot(access_app)
    original_scope = suggestions.active_member_write_scope
    observed = []

    def scope(band_id, actor, **kwargs):
        observed.append(kwargs)
        db.session.execute(update(AppUser).where(AppUser.id == actor.id).values(status='inactive'),
                           execution_options={'synchronize_session': False})
        return original_scope(band_id, actor, **kwargs)

    monkeypatch.setattr(suggestions, 'active_member_write_scope', scope)
    response = write_client.delete(f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}', headers=principals['owner'])
    assert observed == [{'lock': True, 'lock_actor': True}]
    assert response.status_code == 403
    assert _snapshot(access_app) == before
    with access_app.app_context():
        assert AppUser.query.filter_by(email='user@example.com').one().status == 'active'


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_platform_admin_without_active_band_membership_cannot_bypass_suggestion_crud_scope(
        access_app, write_client, principals, operation):
    band_id = _band_id(access_app)
    suggestion_id = _seed(access_app, band_id)
    with access_app.app_context():
        AppUser.query.filter_by(email='outsider@example.com').one().is_platform_admin = True
        db.session.commit()
    before = _snapshot(access_app)
    path = f'/api/v2/bands/{band_id}/suggestions'
    response = (write_client.post(path, headers=principals['outsider'], json=_BODY) if operation == 'create'
        else write_client.patch(f'{path}/{suggestion_id}', headers=principals['outsider'], json=_BODY)
        if operation == 'update' else write_client.delete(f'{path}/{suggestion_id}', headers=principals['outsider']))
    assert response.status_code == 403
    assert response.get_json() == {'error': 'An active band membership is required.'}
    assert _snapshot(access_app) == before


@pytest.mark.parametrize(('value', 'options', 'expected'), [
    ('  title  ', {}, 'title'), ('   ', {}, None), (None, {'nullable': False}, None),
    ('', {'nullable': False}, None),
])
def test_shared_text_value_contract_preserves_whitespace_and_null_semantics(value, options, expected):
    assert write_values.text_value(value, 'title', 10, **options) == expected


@pytest.mark.parametrize(('value', 'options', 'message'), [
    (False, {}, 'title must be text.'), ('long title', {}, 'title must be at most 3 characters.'),
    ('   ', {'required': True}, 'title is required.'), (None, {'required': True}, 'title is required.'),
])
def test_shared_text_validation_contract(value, options, message):
    with pytest.raises(ValidationError) as error:
        write_values.text_value(value, 'title', 3, **options)
    assert str(error.value) == message


def test_shared_visibility_and_external_link_value_contracts():
    assert write_values.visibility_value(None, 'MEMBERS') == 'MEMBERS'
    assert write_values.visibility_value('PUBLIC', 'MEMBERS') == 'PUBLIC'
    with pytest.raises(ValidationError, match='visibility must be PUBLIC or MEMBERS'):
        write_values.visibility_value('', 'PUBLIC')
    assert write_values.external_link_value(None) is None
    with pytest.raises(ValidationError, match='link is required'):
        write_values.external_link_value(None, required=True)
    assert write_values.external_link_value('https://example.test/reviewed') == 'https://example.test/reviewed'
    with pytest.raises(ValidationError):
        write_values.external_link_value(' https://example.test/reviewed ')
    with pytest.raises(ValidationError):
        write_values.external_link_value('https://example.test/' + 'x' * 500)
