"""Real owned-local PG comment races; only JWT resolution is synthetic.

The coordinator proves pg_blocking_pids across separate sessions before
releasing the first Band lock. HTTP, tree checks, row locks, cascades, audit and
commit remain real. Actual JWT behavior is covered by the companion tests.
"""

from dataclasses import dataclass
import json
import os

import pytest

from errors import register_error_handlers
from extensions import db
from member_profile_service import ensure_member_profile
from models import AppUser, AuditEvent, Band, BandMembership, Comment, Media, PersonalLog, R2DeletionTask, Song
import routes.content_writes as writes
import comment_service as comments
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


def _fixture(invite_pg, monkeypatch, kind):
    app, engine, ids = invite_pg
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True)
    app.register_blueprint(writes.content_writes_bp)
    register_error_handlers(app)
    with app.app_context():
        owner = db.session.get(AppUser, ids['owner'])
        principal = Principal(**{name: getattr(owner, name) for name in Principal.__dataclass_fields__})
        Band.query.filter_by(id=ids['band']).with_for_update().one()
        membership = BandMembership.query.filter_by(
            band_id=ids['band'], user_id=ids['owner'], status='active').with_for_update().one()
        if kind == 'media':
            song = Song(band_id=ids['band'], created_by_membership_id=membership.id,
                        visibility='PUBLIC', title='Comment race song', artist='Fixture')
            target = Media(band_id=ids['band'], created_by_membership_id=membership.id,
                           visibility='PUBLIC', song=song, filename='owned.mp3', file_type='audio')
        else:
            # Use the authenticated profile service rather than bypassing the
            # model's legacy-import guard for an unlinked profile insert.
            member = ensure_member_profile(membership)
            target = PersonalLog(band_id=ids['band'], created_by_membership_id=membership.id,
                                 visibility='PUBLIC', member=member, title='Owned race log',
                                 filename='owned.mp3', file_type='audio')
        db.session.add(target)
        db.session.flush()
        values = dict(band_id=ids['band'], created_by_membership_id=membership.id,
                      is_legacy=False, author='Owned original author', **{f'{kind}_id': target.id})
        parent = Comment(content='Original parent', **values)
        child = Comment(content='Original child', parent=parent, **values)
        db.session.add_all((parent, child))
        db.session.commit()
        graph = {'band': ids['band'], 'actor': ids['owner'], 'target': target.id,
                 'parent': parent.id, 'child': child.id}
        db.session.remove()
    monkeypatch.setattr(writes, 'current_app_user', lambda required=True: principal)
    monkeypatch.setattr(comments, 'current_app_user', lambda required=True: principal)
    return app, engine, graph


def _request(app, graph, kind, operation):
    with app.test_client() as client:
        base = f"/api/v2/bands/{graph['band']}"
        if operation == 'reply':
            resource = 'media' if kind == 'media' else 'personal-logs'
            response = client.post(f"{base}/{resource}/{graph['target']}/comments",
                                   json={'content': 'Concurrent reply', 'parent_id': graph['parent']})
        elif operation == 'update':
            response = client.patch(f"{base}/comments/{graph['parent']}", json={'content': 'Concurrent update'})
        else:
            response = client.delete(f"{base}/comments/{graph['parent']}")
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']
        return response.status_code, response.get_json()


@pytest.mark.parametrize('kind', ['media', 'personal_log'])
@pytest.mark.parametrize('operation', ['reply', 'update'])
@pytest.mark.parametrize('delete_first', [False, True], ids=['write-first', 'delete-first'])
def test_comment_write_and_parent_delete_share_one_serialized_order(
        invite_pg, monkeypatch, kind, operation, delete_first):
    app, engine, graph = _fixture(invite_pg, monkeypatch, kind)
    change = lambda: _request(app, graph, kind, operation)
    delete = lambda: _request(app, graph, kind, 'delete')
    first, second = _ordered_contention(
        app, engine, delete if delete_first else change, change if delete_first else delete)
    deleted, changed = (first, second) if delete_first else (second, first)
    assert deleted == (200, {'deleted': True, 'id': graph['parent']})
    if delete_first:
        assert changed == (404, {'error': 'Resource not found' if operation == 'reply' else 'Comment not found'})
    else:
        assert changed[0] == (201 if operation == 'reply' else 200)
        assert set(changed[1]) == {'comment'}
        item = changed[1]['comment']
        assert item['content'] == ('Concurrent reply' if operation == 'reply' else 'Concurrent update')
        assert item['parent_id'] == (graph['parent'] if operation == 'reply' else None)
        assert item['can_edit'] is item['can_delete'] is item['can_manage'] is True
        assert item['visibility'] == item['effective_visibility'] == 'PUBLIC'
    with app.app_context():
        db.session.remove()
        assert Comment.query.filter_by(band_id=graph['band']).count() == 0
        target_model = Media if kind == 'media' else PersonalLog
        assert db.session.get(target_model, graph['target']) is not None
        audits = AuditEvent.query.filter(
            AuditEvent.event_type.in_(('content.comment.created', 'content.comment.updated', 'content.comment.deleted'))
        ).order_by(AuditEvent.id).all()
        expected = ['content.comment.deleted'] if delete_first else [
            'content.comment.created' if operation == 'reply' else 'content.comment.updated',
            'content.comment.deleted']
        assert [row.event_type for row in audits] == expected
        assert [row.actor_user_id for row in audits] == [graph['actor']] * len(expected)
        assert audits[-1].target_id == str(graph['parent'])
        assert json.loads(audits[-1].details_json) == {'target': kind, 'target_id': graph['target']}
        assert R2DeletionTask.query.count() == 0
