"""HTTP/transaction contracts for owner-selected FeaturedMedia mutations."""

import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db
from models import AppUser, AuditEvent, Media
import routes.content_writes as writes
import featured_media_service as featured
import deletion_service
from storage import storage
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_featured_media import (  # noqa: F401
    _song_url, featured_graph, featured_principals,
)


_EVENTS = ('content.song.featured_media.changed', 'content.song.featured_media.cleared')


def _initial_selection(app, graph):
    with app.app_context():
        db.session.get(Media, graph['audio']).is_featured = True
        db.session.commit()


def _request(client, graph, headers, operation):
    path = _song_url(graph)
    return (client.put(path, headers=headers, json={'media_id': graph['video']})
            if operation == 'set' else client.delete(path, headers=headers))


def _snapshot(app, graph):
    with app.app_context():
        db.session.remove()
        rows = Media.query.filter_by(song_id=graph['song']).order_by(Media.id).all()
        return {
            'media': [(row.id, row.is_featured, row.filename, row.object_key,
                       row.vote_score, row.transcoding_status, row.processing_attempts)
                      for row in rows],
            'audits': [(row.event_type, row.target_type, row.target_id,
                        json.loads(row.details_json)) for row in AuditEvent.query.filter(
                            AuditEvent.event_type.in_(_EVENTS)).order_by(AuditEvent.id)],
        }


def test_featured_mutations_commit_audit_only_on_change_and_never_touch_storage_or_processing(
        access_app, featured_graph, featured_principals, monkeypatch):
    import media_processing

    def forbidden(*args, **kwargs):
        pytest.fail('representative selection must not sign, delete, inspect or process files')

    for name in ('generate_url', 'delete', 'head'):
        monkeypatch.setattr(storage, name, forbidden)
    monkeypatch.setattr(media_processing, 'start_audio_processing', forbidden)
    monkeypatch.setattr(media_processing, 'retry_audio_processing', forbidden)
    before = _snapshot(access_app, featured_graph)
    client, headers = access_app.test_client(), featured_principals['owner']
    path = _song_url(featured_graph)
    first = client.put(path, headers=headers, json={'media_id': featured_graph['audio']})
    repeated = client.put(path, headers=headers, json={'media_id': featured_graph['audio']})
    assert first.status_code == repeated.status_code == 200
    assert first.get_json() == repeated.get_json() == {
        'song_id': featured_graph['song'], 'media_id': featured_graph['audio'],
        'is_featured': True, 'can_feature': True,
    }
    selected = _snapshot(access_app, featured_graph)
    assert selected['audits'] == [(_EVENTS[0], 'Media', str(featured_graph['audio']), {
        'song_id': featured_graph['song'], 'media_id': featured_graph['audio'], 'cleared_count': 0,
    })]
    cleared = client.delete(path, headers=headers)
    repeated_clear = client.delete(path, headers=headers)
    assert cleared.get_json() == {
        'song_id': featured_graph['song'], 'media': None, 'cleared': True, 'can_feature': True,
    }
    assert repeated_clear.get_json() == {
        'song_id': featured_graph['song'], 'media': None, 'cleared': False, 'can_feature': True,
    }
    assert cleared.status_code == repeated_clear.status_code == 200
    after = _snapshot(access_app, featured_graph)
    assert after['media'] == before['media']
    assert after['audits'] == selected['audits'] + [(_EVENTS[1], 'Song', str(featured_graph['song']), {
        'song_id': featured_graph['song'], 'cleared_count': 1,
    })]
    for response in (first, repeated, cleared, repeated_clear):
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']


@pytest.mark.parametrize('operation', ['set', 'clear'])
@pytest.mark.parametrize('failure', ['audit_flush', 'final_commit'])
def test_featured_mutation_failure_rolls_back_flags_and_audit(
        access_app, featured_graph, featured_principals, failure, operation):
    _initial_selection(access_app, featured_graph)
    before = _snapshot(access_app, featured_graph)
    observed = []

    def business_audits(session):
        return [row for row in session.new
                if isinstance(row, AuditEvent) and row.event_type in _EVENTS]

    def before_flush(session, flush_context, instances):
        if failure == 'audit_flush' and business_audits(session):
            observed.append('audit_flush')
            raise RuntimeError('injected featured audit flush failure')

    def before_commit(session):
        if failure == 'final_commit' and business_audits(session):
            observed.append('final_commit')
            raise RuntimeError('injected featured commit failure')

    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected featured'):
            _request(access_app.test_client(), featured_graph, featured_principals['owner'], operation)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert observed == [failure]
    assert _snapshot(access_app, featured_graph) == before


@pytest.mark.parametrize('operation', ['set', 'clear'])
def test_featured_response_creation_failure_happens_after_durable_commit_and_retry_is_idempotent(
        access_app, featured_graph, featured_principals, monkeypatch, operation):
    _initial_selection(access_app, featured_graph)
    original_jsonify = writes.jsonify
    observed = []
    commits = []

    def before_commit(session):
        if any(isinstance(row, AuditEvent) and row.event_type in _EVENTS for row in session.new):
            session.info['featured_response_contract'] = True

    def after_commit(session):
        if session.info.pop('featured_response_contract', False):
            commits.append('business_committed')

    def fail_jsonify(value):
        # SQLite may reuse its in-memory DBAPI connection; the real after_commit
        # event separately proves that this read is after the durable boundary.
        assert commits == ['business_committed']
        with db.engine.connect() as connection:
            from sqlalchemy import select
            observed.append(connection.execute(select(Media.id).where(
                Media.song_id == featured_graph['song'], Media.is_featured.is_(True))).scalars().all())
        raise RuntimeError('injected post-commit featured response failure')

    monkeypatch.setattr(writes, 'jsonify', fail_jsonify)
    event.listen(Session, 'before_commit', before_commit)
    event.listen(Session, 'after_commit', after_commit)
    try:
        with pytest.raises(RuntimeError, match='post-commit featured response'):
            _request(access_app.test_client(), featured_graph, featured_principals['owner'], operation)
    finally:
        event.remove(Session, 'before_commit', before_commit)
        event.remove(Session, 'after_commit', after_commit)
    expected_ids = [featured_graph['video']] if operation == 'set' else []
    assert observed == [expected_ids]
    committed = _snapshot(access_app, featured_graph)
    assert len(committed['audits']) == 1
    monkeypatch.setattr(writes, 'jsonify', original_jsonify)
    retry = _request(access_app.test_client(), featured_graph, featured_principals['owner'], operation)
    assert retry.status_code == 200
    if operation == 'clear':
        assert retry.get_json()['cleared'] is False
    assert _snapshot(access_app, featured_graph) == committed


@pytest.mark.parametrize('operation', ['set', 'clear'])
def test_featured_rechecks_current_actor_after_band_membership_lock_before_target_mutation(
        access_app, featured_graph, featured_principals, monkeypatch, operation):
    _initial_selection(access_app, featured_graph)
    before = _snapshot(access_app, featured_graph)
    calls = []
    original_lock = deletion_service.lock_band_and_membership
    original_actor = deletion_service.load_current_actor

    def lock(*args, **kwargs):
        calls.append('band_membership')
        assert kwargs == {'require_member': False}
        return original_lock(*args, **kwargs)

    def actor(*args, **kwargs):
        calls.append('actor')
        assert kwargs == {'lock': True}
        current = original_actor(*args, **kwargs)
        current.email_verified = False
        return current

    monkeypatch.setattr(deletion_service, 'lock_band_and_membership', lock)
    monkeypatch.setattr(deletion_service, 'load_current_actor', actor)
    response = _request(access_app.test_client(), featured_graph, featured_principals['owner'], operation)
    assert response.status_code == 403
    assert response.get_json() == {'error': 'An active, verified account is required.'}
    assert calls == ['band_membership', 'actor']
    assert _snapshot(access_app, featured_graph) == before
    with access_app.app_context():
        db.session.remove()
        assert AppUser.query.filter_by(email='user@example.com').one().email_verified is True


@pytest.mark.parametrize('operation', ['set', 'clear'])
def test_sqlite_writer_lock_precedes_inner_principal_and_mutable_authority_reads(
        access_app, featured_graph, featured_principals, monkeypatch, operation):
    calls = []
    original_user = writes.current_app_user
    original_lock = deletion_service.lock_band_and_membership
    original_actor = deletion_service.load_current_actor

    def user(*args, **kwargs):
        calls.append('principal')
        return original_user(*args, **kwargs)

    def lock(*args, **kwargs):
        calls.append('band_membership')
        return original_lock(*args, **kwargs)

    def actor(*args, **kwargs):
        calls.append('actor')
        return original_actor(*args, **kwargs)

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        if statement.strip().upper() == 'BEGIN IMMEDIATE':
            calls.append('begin_immediate')

    monkeypatch.setattr(writes, 'current_app_user', user)
    monkeypatch.setattr(featured, 'current_app_user', user)
    monkeypatch.setattr(deletion_service, 'lock_band_and_membership', lock)
    monkeypatch.setattr(deletion_service, 'load_current_actor', actor)
    with access_app.app_context():
        engine = db.engine
    event.listen(engine, 'before_cursor_execute', before_execute)
    try:
        response = _request(access_app.test_client(), featured_graph, featured_principals['owner'], operation)
    finally:
        event.remove(engine, 'before_cursor_execute', before_execute)
    assert response.status_code == 200
    assert calls == ['principal', 'begin_immediate', 'principal', 'band_membership', 'actor']


def test_invalid_featured_payload_is_rejected_before_business_lock(
        access_app, featured_graph, featured_principals, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid request must not enter representative-selection transaction')

    monkeypatch.setattr(deletion_service, 'lock_band_and_membership', forbidden)
    response = access_app.test_client().put(_song_url(featured_graph),
        headers=featured_principals['owner'], json={'media_id': True})
    assert response.status_code == 400
    assert response.get_json() == {'error': 'media_id must be a positive integer.'}
