"""Real local PostgreSQL selection races; only JWT resolution is synthetic.

The shared coordinator observes pg_blocking_pids on separate sessions before
releasing the first Band row lock. HTTP, permissions, row writes, partial unique
index enforcement, audit and commits remain real; JWT is covered separately.
"""

from dataclasses import dataclass
import json
import os

import pytest

from errors import register_error_handlers
from extensions import db
from models import AppUser, AuditEvent, BandLogo, BandMembership, GalleryImage, R2DeletionTask
import routes.content_writes as writes
import routes.deletion_tasks as deletions
from storage import storage
from tests.test_invite_link_concurrency import (  # noqa: F401
    _ordered_contention, invite_pg, private_pg17,
)


pytestmark = [pytest.mark.postgres_integration, pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly provisioned owned localhost PG17 only')]


@dataclass(frozen=True)
class Principal:
    id: int
    supabase_user_id: str
    email: str
    display_name: str | None
    status: str
    email_verified: bool
    is_platform_admin: bool


def _fixture(invite_pg, monkeypatch):
    app, engine, ids = invite_pg
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True, PG_R2_DELETION_WORKER_ENABLED=True)
    app.register_blueprint(writes.content_writes_bp)
    app.register_blueprint(deletions.deletion_tasks_bp)
    register_error_handlers(app)
    with app.app_context():
        owner = db.session.get(AppUser, ids['owner'])
        principal = Principal(**{name: getattr(owner, name) for name in Principal.__dataclass_fields__})
        membership = BandMembership.query.filter_by(
            band_id=ids['band'], user_id=ids['owner'], status='active').one()
        images = [GalleryImage(band_id=ids['band'], created_by_membership_id=membership.id,
                               visibility='MEMBERS', filename=f'owned-selection-{index}.jpg',
                               object_key=f'bands/{ids["band"]}/gallery/selection-{index}.jpg')
                  for index in range(2)]
        db.session.add_all(images)
        db.session.commit()
        graph = {'band': ids['band'], 'owner': ids['owner'], 'images': [row.id for row in images]}
        db.session.remove()
    monkeypatch.setattr(writes, 'current_app_user', lambda required=True: principal)
    monkeypatch.setattr(deletions, 'current_app_user', lambda required=True: principal)
    monkeypatch.setattr(storage, 'generate_url', lambda key, expires_in=None: f'https://storage.test/{key}')
    assert app.extensions.get('r2_deletion_worker') is None

    def forbidden(*args, **kwargs):
        pytest.fail('selection/enqueue contracts must not execute storage mutations')

    monkeypatch.setattr(storage, 'delete', forbidden)
    return app, engine, graph


def _request(app, graph, kind, image_id):
    with app.test_client() as client:
        response = (client.put(f"/api/v2/bands/{graph['band']}/logo", json={'gallery_image_id': image_id})
                    if kind == 'logo' else client.patch(
                        f"/api/v2/bands/{graph['band']}/gallery/{image_id}/featured",
                        json={'is_featured': True}))
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']
        data = response.get_json()
        assert set(data) == {'logo' if kind == 'logo' else 'gallery'}
        if kind == 'logo':
            assert set(data['logo']) == {'gallery_image_id', 'url'}
            assert data['logo']['gallery_image_id'] == image_id
        else:
            assert data['gallery']['id'] == image_id
            assert data['gallery']['is_featured'] is True
            assert data['gallery']['visibility'] == data['gallery']['effective_visibility'] == 'MEMBERS'
            assert data['gallery']['can_edit'] is data['gallery']['can_delete'] is data['gallery']['can_manage'] is True
        return data


@pytest.mark.parametrize('kind', ['logo', 'featured'])
def test_concurrent_selections_serialize_to_one_last_selected_image(invite_pg, monkeypatch, kind):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    first_id, second_id = graph['images']
    first, second = _ordered_contention(
        app, engine,
        lambda: _request(app, graph, kind, first_id),
        lambda: _request(app, graph, kind, second_id),
    )
    key, id_key = ('logo', 'gallery_image_id') if kind == 'logo' else ('gallery', 'id')
    assert first[key][id_key] == first_id and second[key][id_key] == second_id
    event_type = 'band_logo_updated' if kind == 'logo' else 'content.gallery.featured.changed'
    with app.app_context():
        db.session.remove()
        if kind == 'logo':
            assert BandLogo.query.filter_by(band_id=graph['band']).one().gallery_image_id == second_id
            assert GalleryImage.query.filter_by(band_id=graph['band'], is_featured=True).count() == 0
        else:
            assert GalleryImage.query.filter_by(band_id=graph['band'], is_featured=True).one().id == second_id
            assert BandLogo.query.filter_by(band_id=graph['band']).count() == 0
        audits = AuditEvent.query.filter_by(band_id=graph['band'], event_type=event_type).order_by(AuditEvent.id).all()
        assert len(audits) == 2
        assert [row.actor_user_id for row in audits] == [graph['owner'], graph['owner']]
        if kind == 'logo':
            assert [json.loads(row.details_json) for row in audits] == [
                {'gallery_image_id': first_id, 'previous_gallery_image_id': None},
                {'gallery_image_id': second_id, 'previous_gallery_image_id': first_id}]
        else:
            assert [row.target_id for row in audits] == [str(first_id), str(second_id)]
            assert [json.loads(row.details_json) for row in audits] == [
                {'is_featured': True, 'cleared_count': 0}, {'is_featured': True, 'cleared_count': 1}]
        assert R2DeletionTask.query.count() == 0


def _race_request(app, graph, kind, *, delete=False):
    image_id = graph['images'][0]
    with app.test_client() as client:
        if delete:
            response = client.delete(f"/api/v2/bands/{graph['band']}/gallery/{image_id}")
        elif kind == 'logo':
            response = client.put(f"/api/v2/bands/{graph['band']}/logo", json={'gallery_image_id': image_id})
        else:
            response = client.patch(f"/api/v2/bands/{graph['band']}/gallery/{image_id}/featured",
                                    json={'is_featured': True})
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']
        return response.status_code, response.get_json()


@pytest.mark.parametrize('kind', ['logo', 'featured'])
@pytest.mark.parametrize('delete_first', [False, True], ids=['selection-first', 'deletion-first'])
def test_selection_and_deletion_enqueue_share_one_serialized_order(invite_pg, monkeypatch, kind, delete_first):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    selection = lambda: _race_request(app, graph, kind)
    deletion = lambda: _race_request(app, graph, kind, delete=True)
    first, second = _ordered_contention(
        app, engine, deletion if delete_first else selection, selection if delete_first else deletion)
    delete_result, selection_result = (first, second) if delete_first else (second, first)
    assert delete_result[0] == 202
    assert delete_result[1]['status'] == 'queued'
    assert delete_result[1]['resource_type'] == 'gallery'
    assert delete_result[1]['resource_id'] == graph['images'][0]
    assert selection_result[0] == (404 if delete_first else 200)
    if delete_first:
        assert selection_result[1] == {'error': 'Resource not found' if kind == 'logo' else 'Gallery image not found'}
    # Once queued, even a previously selected target cannot be selected again.
    assert _race_request(app, graph, kind)[0] == 404
    with app.app_context():
        db.session.remove()
        image = db.session.get(GalleryImage, graph['images'][0])
        assert image is not None  # Enqueue is not execution or file deletion.
        task = R2DeletionTask.query.filter_by(band_id=graph['band'], resource_type='gallery').one()
        assert task.id == delete_result[1]['task_id']
        assert task.status == 'queued' and task.resource_id == image.id
        assert task.object_keys == [f"bands/{graph['band']}/gallery/selection-0.jpg"]
        if kind == 'logo':
            logo = BandLogo.query.filter_by(band_id=graph['band']).one_or_none()
            assert (logo is not None) is not delete_first
            if logo is not None:
                assert logo.gallery_image_id == image.id
            assert image.is_featured is False
        else:
            assert image.is_featured is not delete_first
        event_type = 'band_logo_updated' if kind == 'logo' else 'content.gallery.featured.changed'
        audits = AuditEvent.query.filter(
            AuditEvent.band_id == graph['band'],
            AuditEvent.event_type.in_((event_type, 'content.deletion.queued'))).order_by(AuditEvent.id).all()
        assert [row.event_type for row in audits] == (
            ['content.deletion.queued'] if delete_first else [event_type, 'content.deletion.queued'])
