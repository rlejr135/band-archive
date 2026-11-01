"""Owned-local PostgreSQL contention contracts for SongSuggestion CRUD.

Only JWT resolution is replaced with a frozen synthetic principal. The HTTP
routes, active membership/actor checks, row locks, audit and commits stay real.
The shared coordinator proves a PostgreSQL blocking relationship, not merely
the order in which two Python threads happened to run.
"""

from dataclasses import dataclass
import json
import os

import pytest

from errors import register_error_handlers
from extensions import db
from models import AppUser, AuditEvent, BandMembership, SongSuggestion, SongSuggestionVote
import routes.content_writes as writes
import song_suggestion_service as suggestions
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
        suggestion = SongSuggestion(
            band_id=ids['band'], created_by_membership_id=membership.id,
            title='Owned suggestion race', artist='Fixture artist',
            link='https://example.invalid/owned-suggestion', memo='Original memo',
            visibility='PUBLIC', thumbs_up=1, thumbs_down=1,
        )
        db.session.add(suggestion)
        db.session.flush()
        db.session.add_all((
            SongSuggestionVote(
                band_id=ids['band'], suggestion_id=suggestion.id,
                voter_user_id=ids['owner'], created_by_membership_id=membership.id,
                is_legacy=False, value=1,
            ),
            SongSuggestionVote(
                band_id=ids['band'], suggestion_id=suggestion.id,
                voter_user_id=ids['entrant'], created_by_membership_id=membership.id,
                is_legacy=False, value=-1,
            ),
        ))
        db.session.commit()
        graph = {
            'band': ids['band'], 'owner': ids['owner'], 'suggestion': suggestion.id,
            'updated_response': {'suggestion': {
                'id': suggestion.id, 'title': suggestion.title, 'artist': suggestion.artist,
                'link': suggestion.link, 'memo': 'Updated during race',
                'thumbs_up': 1, 'thumbs_down': 1, 'vote_score': 0, 'viewer_vote': 0,
                'status': 'pending', 'created_at': suggestion.created_at.isoformat(),
                'visibility': 'PUBLIC', 'effective_visibility': 'PUBLIC',
                'can_edit': True, 'can_delete': True, 'can_manage': True, 'can_proceed': True,
            }},
        }
        assert SongSuggestionVote.query.filter_by(suggestion_id=suggestion.id).count() == 2
        db.session.remove()
    monkeypatch.setattr(writes, 'current_app_user', lambda required=True: principal)
    monkeypatch.setattr(suggestions, 'current_app_user', lambda required=True: principal)
    return app, engine, graph


def _request(app, graph, operation):
    path = f"/api/v2/bands/{graph['band']}/suggestions/{graph['suggestion']}"
    with app.test_client() as client:
        response = (client.patch(path, json={'memo': 'Updated during race'})
                    if operation == 'update' else client.delete(path))
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']
        return response.status_code, response.get_json()


@pytest.mark.parametrize('first_operation', ['update', 'delete'],
                         ids=['update-before-delete', 'delete-before-update'])
def test_suggestion_update_and_delete_serialize_without_resurrecting_the_queue_entry(
        invite_pg, monkeypatch, first_operation):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    second_operation = 'delete' if first_operation == 'update' else 'update'
    first, second = _ordered_contention(
        app, engine,
        lambda: _request(app, graph, first_operation),
        lambda: _request(app, graph, second_operation),
    )
    deleted_response = (200, {'deleted': True, 'id': graph['suggestion']})
    if first_operation == 'update':
        assert first == (200, graph['updated_response'])
        assert second == deleted_response
    else:
        assert first == deleted_response
        assert second == (404, {'error': 'Suggestion not found'})

    with app.app_context():
        db.session.remove()
        assert db.session.get(SongSuggestion, graph['suggestion']) is None
        assert SongSuggestionVote.query.filter_by(
            band_id=graph['band'], suggestion_id=graph['suggestion']).count() == 0
        audits = AuditEvent.query.filter(
            AuditEvent.band_id == graph['band'],
            AuditEvent.event_type.in_(('content.suggestion.updated', 'content.suggestion.deleted')),
        ).order_by(AuditEvent.id).all()
        expected = (['content.suggestion.updated', 'content.suggestion.deleted']
                    if first_operation == 'update' else ['content.suggestion.deleted'])
        assert [row.event_type for row in audits] == expected
        assert [row.actor_user_id for row in audits] == [graph['owner']] * len(expected)
        assert [(row.target_type, row.target_id) for row in audits] == [
            ('SongSuggestion', str(graph['suggestion']))] * len(expected)
        if first_operation == 'update':
            assert json.loads(audits[0].details_json) == {'fields': ['memo']}
        assert audits[-1].details_json is None
