"""Owned-local PostgreSQL serialization contracts for announcement writes.

JWT resolution alone uses a frozen synthetic principal; JWT contracts live in
the HTTP/SQLite suite. HTTP dispatch, tenant scope, row locks, response creation,
audits and commits remain real. The shared coordinator observes pg_blocking_pids
before releasing the first Band row lock, rather than relying on thread timing.
"""

from dataclasses import dataclass
from datetime import datetime
import json
import os

import pytest

from errors import register_error_handlers
from extensions import db
from models import Announcement, AppUser, AuditEvent, BandMembership, R2DeletionTask
import routes.content_writes as writes
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


def _fixture(invite_pg, monkeypatch, *, existing=False):
    app, engine, ids = invite_pg
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True)
    app.register_blueprint(writes.content_writes_bp)
    register_error_handlers(app)
    with app.app_context():
        owner = db.session.get(AppUser, ids['owner'])
        principal = Principal(**{
            name: getattr(owner, name) for name in Principal.__dataclass_fields__
        })
        membership = BandMembership.query.filter_by(
            band_id=ids['band'], user_id=ids['owner'], status='active').one()
        graph = {'band': ids['band'], 'owner': ids['owner'],
                 'author': membership.id, 'announcement': None}
        if existing:
            # The editing owner must not replace another member's authorship.
            author = BandMembership(band_id=ids['band'], user_id=ids['entrant'],
                                    role='member', status='active')
            db.session.add(author)
            db.session.flush()
            row = Announcement(band_id=ids['band'], created_by_membership_id=author.id,
                               visibility='MEMBERS', content='Original announcement')
            db.session.add(row)
            db.session.flush()
            graph.update(author=author.id, announcement=row.id)
        db.session.commit()
        db.session.remove()
    monkeypatch.setattr(writes, 'current_app_user', lambda required=True: principal)
    return app, engine, graph


def _request(app, graph, *, content=None, create=False, clear=False):
    path = f"/api/v2/bands/{graph['band']}/announcement"
    with app.test_client() as client:
        body = {'content': content}
        if create:
            body['visibility'] = 'MEMBERS'
        response = client.delete(path) if clear else client.put(path, json=body)
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']
        payload = response.get_json()
        assert set(payload) == ({'announcement', 'cleared'} if clear else {'announcement'})
        if clear:
            assert payload['cleared'] is True
        announcement = payload['announcement']
        assert set(announcement) == {
            'id', 'content', 'updated_at', 'visibility', 'effective_visibility',
            'can_edit', 'can_delete', 'can_manage',
        }
        assert announcement['content'] == ('' if clear else content)
        assert announcement['visibility'] == announcement['effective_visibility'] == 'MEMBERS'
        assert announcement['can_edit'] is announcement['can_delete'] is announcement['can_manage'] is True
        assert isinstance(announcement['updated_at'], str)
        datetime.fromisoformat(announcement['updated_at'])
        return payload


def _assert_persisted(app, graph, *, announcement_id, content, events, details):
    with app.app_context():
        db.session.remove()
        row = Announcement.query.filter_by(band_id=graph['band']).one()
        assert (row.id, row.created_by_membership_id, row.visibility, row.content) == (
            announcement_id, graph['author'], 'MEMBERS', content)
        audits = AuditEvent.query.filter(
            AuditEvent.band_id == graph['band'],
            AuditEvent.event_type.in_(tuple(f'content.announcement.{event}' for event in events)),
        ).order_by(AuditEvent.id).all()
        assert [row.event_type for row in audits] == [f'content.announcement.{event}' for event in events]
        assert [row.actor_user_id for row in audits] == [graph['owner']] * len(events)
        assert [(row.target_type, row.target_id) for row in audits] == [
            ('Announcement', str(announcement_id))] * len(events)
        assert [json.loads(row.details_json) if row.details_json else None for row in audits] == details
        assert R2DeletionTask.query.count() == 0


def test_concurrent_first_puts_create_one_announcement_then_update_it(invite_pg, monkeypatch):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    first, second = _ordered_contention(
        app, engine,
        lambda: _request(app, graph, content='First announcement', create=True),
        lambda: _request(app, graph, content='Second announcement'),
    )
    announcement_id = first['announcement']['id']
    assert second['announcement']['id'] == announcement_id
    _assert_persisted(
        app, graph, announcement_id=announcement_id, content='Second announcement',
        events=['created', 'updated'],
        details=[{'visibility': 'MEMBERS'}, {'fields': ['content']}],
    )


def test_update_then_clear_serializes_and_retains_singleton_identity(invite_pg, monkeypatch):
    app, engine, graph = _fixture(invite_pg, monkeypatch, existing=True)
    first, second = _ordered_contention(
        app, engine,
        lambda: _request(app, graph, content='Updated before clear'),
        lambda: _request(app, graph, clear=True),
    )
    assert first['announcement']['id'] == second['announcement']['id'] == graph['announcement']
    _assert_persisted(
        app, graph, announcement_id=graph['announcement'], content='',
        events=['updated', 'cleared'], details=[{'fields': ['content']}, None],
    )
