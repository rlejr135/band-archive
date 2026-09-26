"""Contracts for the remaining tenant-scoped Phase 6 read domains."""

from datetime import datetime, timezone

import pytest

from extensions import db
from models import (Announcement, Band, BandMembership, Comment, GalleryImage,
                    Member, PersonalLog, SongSuggestion)
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def remaining_graph(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        public = Band.query.filter_by(slug='band-b').one()
        private = Band.query.filter_by(slug='band-a').one()
        author = BandMembership.query.filter_by(band_id=public.id, role='owner').one()
        foreign_author = BandMembership.query.filter_by(band_id=private.id, role='owner').one()
        member = Member(band_id=public.id, linked_user_id=author.user_id,
                        name='Public musician', instrument='Bass')
        foreign_member = Member(band_id=private.id, linked_user_id=foreign_author.user_id,
                                name='Private musician', instrument='Drums')
        db.session.add_all((member, foreign_member))
        db.session.flush()
        public_log = PersonalLog(
            band_id=public.id, member_id=member.id, created_by_membership_id=author.id,
            visibility='PUBLIC', title='Public log', filename='legacy.mov',
            object_key='bands/b/logs/canonical.mov', original_filename='shown.mov',
            audio_object_key='bands/b/logs/audio.m4a', audio_filename='legacy_audio.m4a',
            file_type='video', transcoding_status='completed', file_size=123,
        )
        private_log = PersonalLog(
            band_id=public.id, member_id=member.id, visibility='MEMBERS', title='Private log',
            filename='private.mov', object_key='bands/b/logs/private.mov', file_type='video',
            transcoding_status='failed', processing_error='Traceback /srv/AKIA-secret',
        )
        corrupt_log = PersonalLog(
            band_id=public.id, member_id=foreign_member.id, visibility='PUBLIC', title='Corrupt log',
            filename='foreign.mov', object_key='bands/a/foreign.mov', file_type='video',
        )
        db.session.add_all((public_log, private_log, corrupt_log))
        db.session.flush()
        db.session.add_all((
            Comment(band_id=public.id, personal_log_id=public_log.id, author='a', content='first', password_hash='x'),
            Comment(band_id=public.id, personal_log_id=private_log.id, author='b', content='hidden', password_hash='x'),
            GalleryImage(band_id=public.id, created_by_membership_id=author.id, visibility='PUBLIC',
                         filename='legacy.jpg', object_key='bands/b/gallery/canonical.jpg', file_size=1,
                         is_featured=True),
            GalleryImage(band_id=public.id, visibility='MEMBERS', filename='private.jpg', file_size=2),
            GalleryImage(band_id=private.id, visibility='PUBLIC', filename='foreign.jpg', is_featured=True),
            Announcement(band_id=public.id, created_by_membership_id=author.id,
                         visibility='PUBLIC', content='hello'),
            SongSuggestion(band_id=public.id, created_by_membership_id=author.id, visibility='PUBLIC',
                           title='Public suggestion', artist='Artist', link='https://example.test/a',
                           thumbs_up=3, thumbs_down=1),
            SongSuggestion(band_id=public.id, visibility='MEMBERS', title='Private suggestion',
                           artist='Artist', link='https://example.test/b'),
            SongSuggestion(band_id=private.id, visibility='PUBLIC', title='Foreign suggestion',
                           artist='Artist', link='https://example.test/c'),
        ))
        db.session.commit()
        return {'public': public.id, 'private': private.id, 'member': member.id,
                'foreign_member': foreign_member.id, 'public_log': public_log.id,
                'private_log': private_log.id, 'corrupt_log': corrupt_log.id}


def test_remaining_visitor_reads_are_scoped_paginated_and_storage_safe(access_app, remaining_graph, monkeypatch):
    calls = []
    from storage import storage
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: calls.append((key, expires_in)) or f'url:{key}')
    client = access_app.test_client()
    base = f"/api/v2/bands/{remaining_graph['public']}"

    roster = client.get(f'{base}/members?limit=1')
    assert roster.status_code == 200
    assert roster.get_json()['pagination']['total'] == 1
    member = roster.get_json()['members'][0]
    assert set(member) >= {'id', 'name', 'instrument', 'created_at'}
    assert not ({'linked_user_id', 'email', 'phone', 'password'} & set(member))
    assert client.get(f"{base}/members/{remaining_graph['foreign_member']}").status_code == 404

    logs = client.get(f"{base}/members/{remaining_graph['member']}/logs")
    assert logs.status_code == 200
    assert [item['id'] for item in logs.get_json()['logs']] == [remaining_graph['public_log']]
    log = logs.get_json()['logs'][0]
    assert 'object_key' not in log and 'filename' not in log
    assert log['playback']['video_url'] == 'url:bands/b/logs/canonical.mov'
    assert log['playback']['audio_url'] == 'url:bands/b/logs/audio.m4a'
    assert all(ttl == 3600 for _, ttl in calls)
    assert client.get(f"{base}/members/{remaining_graph['member']}/logs?limit=0").status_code == 400
    assert client.get(f"{base}/members/{remaining_graph['member']}/logs?limit=101").status_code == 400
    assert client.get(f"{base}/members/{remaining_graph['member']}/logs?offset=-1").status_code == 400
    assert client.get(f"{base}/members/{remaining_graph['member']}/logs/{remaining_graph['corrupt_log']}").status_code == 404

    processing = client.get(f"{base}/personal-logs/{remaining_graph['private_log']}/processing")
    assert processing.status_code == 404
    comments = client.get(f"{base}/personal-logs/{remaining_graph['public_log']}/comments")
    assert comments.status_code == 200
    assert comments.get_json()['comments'][0]['personal_log_id'] == remaining_graph['public_log']
    assert 'password_hash' not in str(comments.get_json())

    gallery = client.get(f'{base}/gallery')
    assert gallery.status_code == 200
    assert gallery.get_json()['pagination']['total'] == 1
    image = gallery.get_json()['gallery'][0]
    assert image['url'] == 'url:bands/b/gallery/canonical.jpg'
    assert 'object_key' not in image and 'filename' not in image
    featured = client.get(f'{base}/gallery/featured')
    assert featured.status_code == 200 and featured.get_json()['gallery']['id'] == image['id']
    assert client.get(f"/api/v2/bands/{remaining_graph['private']}/gallery/featured").status_code == 404

    assert client.get(f'{base}/announcement').get_json()['announcement']['content'] == 'hello'
    suggestions = client.get(f'{base}/suggestions').get_json()['suggestions']
    assert [item['title'] for item in suggestions] == ['Public suggestion']
    assert suggestions[0]['vote_score'] == 2 and suggestions[0]['status'] == 'pending'
    stats = client.get(f'{base}/dashboard/stats').get_json()['stats']
    assert stats['total_songs'] == 1  # fixture's public-band song only; no private-band inference


def test_remaining_member_access_roster_policy_and_error_redaction(access_app, remaining_graph, signer, monkeypatch):
    calls = []
    from storage import storage
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: calls.append((key, expires_in)) or f'url:{key}')
    client = access_app.test_client()
    base = f"/api/v2/bands/{remaining_graph['public']}"
    token = _token(signer, sub='77777777-7777-7777-7777-777777777777', email='outsider@example.com')
    headers = _headers(token)
    logs = client.get(f"{base}/members/{remaining_graph['member']}/logs", headers=headers)
    assert {item['id'] for item in logs.get_json()['logs']} == {
        remaining_graph['public_log'], remaining_graph['private_log'],
    }
    private = next(item for item in logs.get_json()['logs'] if item['id'] == remaining_graph['private_log'])
    assert private['playback']['video_url'] == 'url:bands/b/logs/private.mov'
    assert all(ttl == 300 for _, ttl in calls)
    processing = client.get(f"{base}/personal-logs/{remaining_graph['private_log']}/processing", headers=headers)
    assert processing.status_code == 200
    assert processing.get_json()['processing']['error'] == 'processing_failed'
    assert 'Traceback' not in str(processing.get_json()) and 'AKIA' not in str(processing.get_json())

    with access_app.app_context():
        Band.query.get(remaining_graph['public']).member_roster_public = False
        db.session.commit()
    assert client.get(f'{base}/members').status_code == 404
    assert client.get(f'{base}/members', headers=headers).status_code == 200
    invalid = client.get(f'{base}/gallery', headers={'Authorization': 'Bearer nope'})
    assert invalid.status_code == 401
    assert invalid.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in invalid.headers['Vary']
