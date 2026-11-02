"""Preserve the deliberately different logo and featured-photo transactions."""

import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db
from models import AuditEvent, BandLogo, GalleryImage, R2DeletionTask
import routes.content_writes as writes
from storage import storage
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_gallery_featured_write import gallery_graph, principals  # noqa: F401


EVENTS = ('band_logo_updated', 'content.gallery.featured.changed')


def _snapshot(app):
    with app.app_context():
        db.session.remove()
        return {
            'logos': [(row.band_id, row.gallery_image_id) for row in BandLogo.query.order_by(BandLogo.band_id)],
            'images': [tuple(getattr(row, column.name) for column in GalleryImage.__table__.columns)
                       for row in GalleryImage.query.order_by(GalleryImage.id)],
            'audits': [(row.event_type, row.band_id, row.actor_user_id, row.target_type,
                        row.target_id, row.details_json) for row in AuditEvent.query.filter(
                            AuditEvent.event_type.in_(EVENTS)).order_by(AuditEvent.id)],
            'deletion_tasks': R2DeletionTask.query.count(),
        }


def _seed_previous(app, graph, kind):
    with app.app_context():
        if kind == 'logo':
            db.session.add(BandLogo(band_id=graph['band'], gallery_image_id=graph['other']))
        else:
            db.session.get(GalleryImage, graph['other']).is_featured = True
        db.session.commit()


def _request(app, graph, headers, kind, image_id=None, desired=True):
    image_id = graph['selected'] if image_id is None else image_id
    client = app.test_client()
    if kind == 'logo':
        return client.put(f"/api/v2/bands/{graph['band']}/logo", headers=headers,
                          json={'gallery_image_id': image_id})
    return client.patch(f"/api/v2/bands/{graph['band']}/gallery/{image_id}/featured", headers=headers,
                        json={'is_featured': desired})


@pytest.mark.parametrize('kind', ['logo', 'featured'])
@pytest.mark.parametrize('failure', ['audit_flush', 'commit'])
def test_gallery_selection_db_failure_rolls_back_selection_and_audit(
        access_app, gallery_graph, principals, monkeypatch, kind, failure):
    _seed_previous(access_app, gallery_graph, kind)
    before = _snapshot(access_app)
    observed = []

    def pending(session):
        return any(isinstance(row, AuditEvent) and row.event_type in EVENTS for row in session.new)

    def before_flush(session, context, instances):
        if pending(session):
            session.info['gallery_selection_contract'] = True
            if failure == 'audit_flush':
                observed.append(failure)
                raise RuntimeError('injected gallery selection audit failure')

    def before_commit(session):
        if failure == 'commit' and (pending(session) or session.info.get('gallery_selection_contract')):
            observed.append(failure)
            raise RuntimeError('injected gallery selection commit failure')

    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected gallery selection'):
            _request(access_app, gallery_graph, principals['owner'], kind)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    # Logo intentionally catches serializer failures, including an autoflush
    # failure raised while preparing its URL; commit then flushes again. Both
    # failures belong to the same business transaction and must roll it back.
    assert observed == ([failure, failure] if kind == 'logo' and failure == 'audit_flush' else [failure])
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('failure', ['serializer', 'jsonify'])
def test_featured_photo_response_failure_rolls_back_previous_selection(
        access_app, gallery_graph, principals, monkeypatch, failure):
    _seed_previous(access_app, gallery_graph, 'featured')
    before = _snapshot(access_app)

    def fail(*args, **kwargs):
        raise RuntimeError('injected featured response failure')

    monkeypatch.setattr(writes, 'serialize_gallery_image' if failure == 'serializer' else 'jsonify', fail)
    with pytest.raises(RuntimeError, match='injected featured response failure'):
        _request(access_app, gallery_graph, principals['owner'], 'featured')
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('failure', ['serializer', 'signer'])
def test_logo_serialization_failure_commits_selected_id_with_null_url(
        access_app, gallery_graph, principals, monkeypatch, failure):
    _seed_previous(access_app, gallery_graph, 'logo')

    def fail(*args, **kwargs):
        raise RuntimeError('injected logo serialization failure')

    monkeypatch.setattr(writes if failure == 'serializer' else storage,
                        'serialize_gallery_image' if failure == 'serializer' else 'generate_url', fail)
    response = _request(access_app, gallery_graph, principals['owner'], 'logo')
    assert response.status_code == 200
    assert response.get_json() == {'logo': {'gallery_image_id': gallery_graph['selected'], 'url': None}}
    after = _snapshot(access_app)
    assert after['logos'] == [(gallery_graph['band'], gallery_graph['selected'])]
    assert [row[0] for row in after['audits']] == ['band_logo_updated']
    assert json.loads(after['audits'][0][-1]) == {
        'gallery_image_id': gallery_graph['selected'], 'previous_gallery_image_id': gallery_graph['other']}
    assert after['deletion_tasks'] == 0


def test_logo_json_failure_occurs_after_committed_selection(
        access_app, gallery_graph, principals, monkeypatch):
    _seed_previous(access_app, gallery_graph, 'logo')

    def fail(*args, **kwargs):
        raise RuntimeError('injected logo JSON failure')

    monkeypatch.setattr(writes, 'jsonify', fail)
    with pytest.raises(RuntimeError, match='injected logo JSON failure'):
        _request(access_app, gallery_graph, principals['owner'], 'logo')
    after = _snapshot(access_app)
    assert after['logos'] == [(gallery_graph['band'], gallery_graph['selected'])]
    assert [row[0] for row in after['audits']] == ['band_logo_updated']


@pytest.mark.parametrize('kind', ['logo', 'featured'])
def test_logo_json_is_after_commit_but_featured_json_is_before_commit(
        access_app, gallery_graph, principals, monkeypatch, kind):
    phases = []
    original_jsonify = writes.jsonify

    def jsonify(*args, **kwargs):
        phases.append('json')
        return original_jsonify(*args, **kwargs)

    def before_flush(session, context, instances):
        if any(isinstance(row, AuditEvent) and row.event_type in EVENTS for row in session.new):
            session.info['gallery_selection_order_contract'] = True

    def before_commit(session):
        if (any(isinstance(row, AuditEvent) and row.event_type in EVENTS for row in session.new)
                or session.info.pop('gallery_selection_order_contract', False)):
            phases.append('commit')

    monkeypatch.setattr(writes, 'jsonify', jsonify)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        response = _request(access_app, gallery_graph, principals['owner'], kind)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    assert phases == (['commit', 'json'] if kind == 'logo' else ['json', 'commit'])


@pytest.mark.parametrize('kind', ['logo', 'featured'])
def test_selection_and_clear_are_audit_idempotent_and_never_enqueue_file_deletion(
        access_app, gallery_graph, principals, kind):
    for _ in range(2):
        assert _request(access_app, gallery_graph, principals['owner'], kind).status_code == 200
    for _ in range(2):
        if kind == 'logo':
            response = access_app.test_client().put(
                f"/api/v2/bands/{gallery_graph['band']}/logo", headers=principals['owner'],
                json={'gallery_image_id': None})
            assert response.get_json() == {'logo': None}
        else:
            response = _request(access_app, gallery_graph, principals['owner'], kind, desired=False)
            assert response.get_json()['gallery']['is_featured'] is False
        assert response.status_code == 200
    after = _snapshot(access_app)
    event_type = 'band_logo_updated' if kind == 'logo' else 'content.gallery.featured.changed'
    assert [row[0] for row in after['audits']] == [event_type, event_type]
    assert after['logos'] == []
    assert after['deletion_tasks'] == 0


@pytest.mark.parametrize('kind', ['logo', 'featured'])
def test_actor_resolution_keeps_its_existing_position_relative_to_sqlite_begin(
        access_app, gallery_graph, principals, monkeypatch, kind):
    phases = []
    original_user = writes.current_app_user
    original_scope = writes.active_member_write_scope

    def user(*args, **kwargs):
        phases.append('actor')
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
        response = _request(access_app, gallery_graph, principals['owner'], kind)
    finally:
        event.remove(engine, 'before_cursor_execute', before_execute)
    assert response.status_code == 200
    assert phases == (['actor', 'actor', 'begin'] if kind == 'logo'
                      else ['actor', 'begin', 'actor', 'scope'])
