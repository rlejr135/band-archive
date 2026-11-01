"""Real FeaturedMedia HTTP writes serialize behind an observed owned-local PG lock.

JWT resolution is doubled with one immutable synthetic principal; Band,
membership and actor revalidation, SQL predicates, row locks, writes and commits
remain real. No provider, storage or external database is accessed.
"""

import json
import os
from types import SimpleNamespace

import pytest

from errors import register_error_handlers
from extensions import db
from models import AuditEvent, BandMembership, Media, MediaVote, Song
import routes.content_writes as writes
import featured_media_service as featured
from tests.test_invite_link_concurrency import (  # noqa: F401
    _ordered_contention, invite_pg, private_pg17,
)


pytestmark = [pytest.mark.postgres_integration, pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly provisioned owned localhost PG17 only')]


def _fixture(invite_pg, monkeypatch):
    app, engine, ids = invite_pg
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True)
    app.register_blueprint(writes.content_writes_bp)
    register_error_handlers(app)
    principal = SimpleNamespace(id=ids['owner'])
    monkeypatch.setattr(writes, 'current_app_user', lambda required=True: principal)
    monkeypatch.setattr(featured, 'current_app_user', lambda required=True: principal)
    with app.app_context():
        membership = BandMembership.query.filter_by(
            band_id=ids['band'], user_id=ids['owner'], status='active').one()
        song = Song(band_id=ids['band'], created_by_membership_id=membership.id,
                    title='Owned representative race', artist='Fixture', visibility='PUBLIC')
        db.session.add(song)
        db.session.flush()
        audio = Media(band_id=ids['band'], created_by_membership_id=membership.id,
                      song_id=song.id, filename='one.mp3',
                      object_key=f"bands/{ids['band']}/media/one.mp3",
                      file_type='audio', visibility='PUBLIC', upvote_count=1, vote_score=1)
        video = Media(band_id=ids['band'], created_by_membership_id=membership.id,
                      song_id=song.id, filename='two.mp4',
                      object_key=f"bands/{ids['band']}/media/two.mp4",
                      file_type='video', visibility='PUBLIC', downvote_count=1, vote_score=-1)
        db.session.add_all((audio, video))
        db.session.flush()
        db.session.add_all((
            MediaVote(band_id=ids['band'], created_by_membership_id=membership.id,
                      media_id=audio.id, voter_hash='a' * 64, value=1),
            MediaVote(band_id=ids['band'], created_by_membership_id=membership.id,
                      media_id=video.id, voter_hash='b' * 64, value=-1),
        ))
        db.session.commit()
        graph = {'band': ids['band'], 'song': song.id, 'audio': audio.id, 'video': video.id,
                 'owner': ids['owner'],
                 'media_before': [_media_snapshot(audio), _media_snapshot(video)],
                 'votes_before': _vote_snapshot(ids['band'])}
        db.session.remove()
    return app, engine, graph


def _media_snapshot(row):
    # Representative selection changes only the flag, never file metadata,
    # processing state, visibility, attribution or vote aggregates.
    return {column.name: getattr(row, column.name) for column in Media.__table__.columns
            if column.name != 'is_featured'}


def _vote_snapshot(band_id):
    return [
        {column.name: getattr(row, column.name) for column in MediaVote.__table__.columns}
        for row in MediaVote.query.filter_by(band_id=band_id).order_by(MediaVote.id).all()
    ]


def _request(app, graph, operation):
    path = f"/api/v2/bands/{graph['band']}/songs/{graph['song']}/featured-media"
    with app.test_client() as client:
        response = (client.delete(path) if operation == 'clear'
                    else client.put(path, json={'media_id': graph[operation]}))
        return response.status_code, response.get_json()


@pytest.mark.parametrize('second_operation', ['video', 'clear'], ids=['set-before-set', 'set-before-clear'])
def test_featured_http_mutations_serialize_and_last_writer_is_authoritative(
        invite_pg, monkeypatch, second_operation):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    first, second = _ordered_contention(
        app, engine,
        lambda: _request(app, graph, 'audio'),
        lambda: _request(app, graph, second_operation),
    )
    assert first == (200, {'song_id': graph['song'], 'media_id': graph['audio'],
                           'is_featured': True, 'can_feature': True})
    assert second == (200, {'song_id': graph['song'], 'media_id': graph['video'],
                            'is_featured': True, 'can_feature': True} if second_operation == 'video'
                      else {'song_id': graph['song'], 'media': None,
                            'cleared': True, 'can_feature': True})
    with app.app_context():
        db.session.remove()
        selected = Media.query.filter_by(band_id=graph['band'], song_id=graph['song'],
                                         is_featured=True).all()
        assert [row.id for row in selected] == ([graph['video']] if second_operation == 'video' else [])
        rows = Media.query.filter_by(song_id=graph['song']).order_by(Media.id).all()
        assert [_media_snapshot(row) for row in rows] == graph['media_before']
        assert _vote_snapshot(graph['band']) == graph['votes_before']
        audits = AuditEvent.query.filter(
            AuditEvent.band_id == graph['band'],
            AuditEvent.event_type.in_(
                ('content.song.featured_media.changed', 'content.song.featured_media.cleared')),
        ).order_by(AuditEvent.id).all()
        assert [row.event_type for row in audits] == [
            'content.song.featured_media.changed',
            'content.song.featured_media.changed' if second_operation == 'video'
            else 'content.song.featured_media.cleared',
        ]
        assert [row.actor_user_id for row in audits] == [graph['owner'], graph['owner']]
        assert (audits[0].target_type, audits[0].target_id) == ('Media', str(graph['audio']))
        assert json.loads(audits[0].details_json) == {
            'song_id': graph['song'], 'media_id': graph['audio'], 'cleared_count': 0,
        }
        if second_operation == 'video':
            assert (audits[1].target_type, audits[1].target_id) == ('Media', str(graph['video']))
            assert json.loads(audits[1].details_json) == {
                'song_id': graph['song'], 'media_id': graph['video'], 'cleared_count': 1,
            }
        else:
            assert (audits[1].target_type, audits[1].target_id) == ('Song', str(graph['song']))
            assert json.loads(audits[1].details_json) == {
                'song_id': graph['song'], 'cleared_count': 1,
            }
