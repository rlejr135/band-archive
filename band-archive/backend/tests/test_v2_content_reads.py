"""Contract tests for the first tenant-scoped Phase 6 content read slice.

The v2 lists are intentionally deterministic: songs use legacy id order,
rehearsals use date/id, media uses score-desc/id, and comments use created/id.
"""

from datetime import date, datetime, timezone

import pytest

from extensions import db
from models import Band, BandMembership, Comment, Media, Rehearsal, Song
from tests.test_auth import _install_jwks, _jwk, _token
# Share the authenticated current-schema fixture with the central-access
# contracts; importing the fixture functions makes them local pytest fixtures.
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def content_graph(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        private = Band.query.filter_by(slug='band-a').one()
        public = Band.query.filter_by(slug='band-b').one()
        owner = BandMembership.query.filter_by(band_id=private.id, role='owner').one()
        member = BandMembership.query.filter_by(band_id=private.id, role='member', status='active').one()

        # This public band graph makes visitors useful for visibility checks.
        public_author = BandMembership.query.filter_by(band_id=public.id, role='owner').one()
        public_song = Song(band_id=public.id, created_by_membership_id=public_author.id,
                           visibility='PUBLIC', title='Public song', artist='Band B')
        playback_song = Song(band_id=public.id, created_by_membership_id=public_author.id,
                             visibility='PUBLIC', title='Playback song', artist='Band B')
        member_song = Song(band_id=public.id, visibility='MEMBERS',
                           title='Members song', artist='Band B')
        corrupt_song = Song(band_id=public.id, visibility='PUBLIC',
                            title='Corrupt song', artist='Band B')
        foreign_song = Song(band_id=private.id, visibility='PUBLIC',
                            title='Foreign song', artist='Band A')
        linked_private_rehearsal = Rehearsal(
            band_id=public.id, created_by_membership_id=public_author.id,
            visibility='MEMBERS', title='Private linked rehearsal', date=date(2026, 1, 3),
        )
        public_rehearsal = Rehearsal(
            band_id=public.id, created_by_membership_id=public_author.id,
            visibility='PUBLIC', title='Public rehearsal', date=date(2026, 1, 2),
        )
        foreign_rehearsal = Rehearsal(
            band_id=private.id, visibility='PUBLIC', title='Foreign rehearsal', date=date(2026, 1, 4),
        )
        corrupt_rehearsal = Rehearsal(
            band_id=public.id, visibility='PUBLIC', title='Corrupt rehearsal', date=date(2026, 1, 5),
        )
        public_song.rehearsals.append(linked_private_rehearsal)
        playback_song.rehearsals.append(public_rehearsal)
        corrupt_rehearsal.songs.append(foreign_song)
        db.session.add_all((
            public_song, playback_song, member_song, corrupt_song, foreign_song,
            linked_private_rehearsal, public_rehearsal, foreign_rehearsal, corrupt_rehearsal,
        ))
        db.session.flush()
        hidden_by_link = Media(
            band_id=public.id, created_by_membership_id=public_author.id,
            visibility='PUBLIC', song_id=public_song.id,
            rehearsal_id=linked_private_rehearsal.id, filename='opaque-original.mp4',
            object_key='bands/b/public/opaque-original.mp4', original_filename='display.mp4',
            file_type='video', transcoding_status='completed',
            video_720_object_key='bands/b/public/opaque-720.mp4',
            audio_object_key='bands/b/public/opaque-audio.m4a', vote_score=10,
        )
        public_media = Media(
            band_id=public.id, created_by_membership_id=public_author.id,
            visibility='PUBLIC', song_id=playback_song.id, rehearsal_id=public_rehearsal.id,
            filename='pruned-original.mp4', object_key='bands/b/public/pruned-original.mp4',
            original_filename='visible.mp4', file_type='video', transcoding_status='completed',
            video_720_object_key='bands/b/public/visible-720.mp4',
            audio_object_key='bands/b/public/visible-audio.m4a',
            original_pruned_at=datetime.now(timezone.utc), vote_score=1,
        )
        corrupt_media = Media(
            band_id=public.id, visibility='PUBLIC', song_id=corrupt_song.id,
            rehearsal_id=foreign_rehearsal.id, filename='cross-band.mp4',
            object_key='bands/a/private/cross-band.mp4', file_type='video',
            transcoding_status='completed', video_720_object_key='bands/a/private/cross-720.mp4',
        )
        failed_media = Media(
            band_id=public.id, visibility='MEMBERS', song_id=member_song.id,
            filename='failed.mp4', object_key='bands/b/failed.mp4', file_type='video',
            transcoding_status='failed', processing_error='Traceback /srv/secret AKIAexample provider token',
        )
        db.session.add_all((hidden_by_link, public_media, corrupt_media, failed_media))
        db.session.flush()
        root = Comment(band_id=public.id, media_id=public_media.id, author='visitor',
                       content='first', password_hash='not-used')
        db.session.add(root)
        db.session.flush()
        reply = Comment(band_id=public.id, media_id=public_media.id, parent_id=root.id,
                        author='member', content='reply', password_hash='not-used')
        db.session.add(reply)
        db.session.commit()
        return {
            'public_band': public.id, 'private_band': private.id, 'public_song': public_song.id,
            'playback_song': playback_song.id,
            'member_song': member_song.id, 'foreign_song': foreign_song.id, 'public_media': public_media.id,
            'hidden_media': hidden_by_link.id, 'public_rehearsal': public_rehearsal.id,
            'corrupt_song': corrupt_song.id, 'corrupt_media': corrupt_media.id,
            'corrupt_rehearsal': corrupt_rehearsal.id, 'failed_media': failed_media.id,
            'owner_membership': owner.id, 'member_membership': member.id,
        }


def test_visitor_content_read_envelopes_filter_nested_members_only_graphs_and_never_sign_hidden(
        access_app, content_graph, monkeypatch):
    calls = []
    from storage import storage
    monkeypatch.setattr(storage, 'generate_url', lambda key, expires_in=None: calls.append((key, expires_in)) or f'url:{key}')
    client = access_app.test_client()
    base = f"/api/v2/bands/{content_graph['public_band']}"

    songs = client.get(f'{base}/songs')
    assert songs.status_code == 200
    assert list(songs.get_json()) == ['songs']
    song = next(item for item in songs.get_json()['songs'] if item['id'] == content_graph['playback_song'])
    assert song['id'] == content_graph['playback_song']
    assert song['visibility'] == song['effective_visibility'] == 'PUBLIC'
    assert [item['id'] for item in song['rehearsals']] == [content_graph['public_rehearsal']]
    assert song['rehearsals'][0]['effective_visibility'] == 'PUBLIC'
    # A MEMBER-only rehearsal linked to a public song hides that media through
    # every route, not merely the rehearsal route.
    assert [item['id'] for item in song['media']] == [content_graph['public_media']]
    assert song['can_edit'] is song['can_delete'] is song['can_manage'] is False

    media = client.get(f"{base}/songs/{content_graph['playback_song']}/media")
    assert media.status_code == 200
    assert [item['id'] for item in media.get_json()['media']] == [content_graph['public_media']]
    item = media.get_json()['media'][0]
    assert 'object_key' not in item and 'filename' not in item
    assert item['playback']['video_url'] == 'url:bands/b/public/visible-720.mp4'
    assert item['playback']['audio_url'] == 'url:bands/b/public/visible-audio.m4a'
    assert all('pruned-original' not in key and 'opaque-' not in key for key, _ in calls)
    assert all(ttl == 3600 for _, ttl in calls)

    # Collection serializers discard corrupt cross-band graph nodes before
    # their visibility or playback fields can trigger a signer call.
    assert content_graph['corrupt_song'] not in [item['id'] for item in songs.get_json()['songs']]
    assert client.get(f"{base}/songs/{content_graph['corrupt_song']}").status_code == 404
    assert client.get(f"{base}/rehearsals/{content_graph['corrupt_rehearsal']}").status_code == 404
    assert client.get(f"{base}/media/{content_graph['corrupt_media']}/processing").status_code == 404
    assert all('cross-' not in key for key, _ in calls)

    comments = client.get(f"{base}/media/{content_graph['public_media']}/comments")
    assert comments.status_code == 200
    assert comments.get_json()['comments'][0]['replies'][0]['content'] == 'reply'
    assert client.get(f"{base}/media/{content_graph['hidden_media']}/comments").status_code == 404


def test_member_owner_capabilities_cross_tenant_concealment_and_optional_auth_cache_contract(
        access_app, content_graph, signer, monkeypatch):
    from storage import storage
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, expires_in=None: calls.append((key, expires_in)) or f'url:{key}')
    client = access_app.test_client()
    owner_token = _token(signer)
    public_base = f"/api/v2/bands/{content_graph['public_band']}"
    private_base = f"/api/v2/bands/{content_graph['private_band']}"

    visitor_forbidden = client.get(f'{private_base}/songs')
    assert visitor_forbidden.status_code == 404
    assert visitor_forbidden.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in visitor_forbidden.headers['Vary']
    malformed = client.get(f'{public_base}/songs', headers={'Authorization': 'Bearer nope'})
    assert malformed.status_code == 401
    assert malformed.headers['Cache-Control'] == 'private, no-store'

    # Band A owner cannot turn a Band B object id into a readable resource.
    cross = client.get(f"{private_base}/songs/{content_graph['public_song']}", headers=_headers(owner_token))
    assert cross.status_code == 404

    # Band B owner's token sees member content and its own authored controls.
    b_owner = _token(signer, sub='77777777-7777-7777-7777-777777777777', email='outsider@example.com')
    visible = client.get(f"{public_base}/songs/{content_graph['public_song']}/media", headers=_headers(b_owner))
    assert [x['id'] for x in visible.get_json()['media']] == [content_graph['hidden_media']]
    assert all(x['can_edit'] and x['can_delete'] and x['can_manage'] for x in visible.get_json()['media'])
    # Members get short-lived URLs even for a public resource only when the
    # effective visibility is MEMBERS; public remains public-TTL.
    assert all(ttl == 300 for _, ttl in calls)

    processing = client.get(f"{public_base}/media/{content_graph['failed_media']}/processing", headers=_headers(b_owner))
    assert processing.status_code == 200
    payload = processing.get_json()['processing']
    assert payload['error'] == 'processing_failed'
    assert 'Traceback' not in str(payload) and 'AKIA' not in str(payload) and '/srv/' not in str(payload)


def test_legacy_rehearsal_relation_writer_rejects_cross_band_song_ids_atomically(access_app, content_graph):
    client = access_app.test_client()
    with access_app.app_context():
        rehearsal = db.session.get(Rehearsal, content_graph['public_rehearsal'])
        original_ids = [song.id for song in rehearsal.songs]
    response = client.put(f"/rehearsals/{content_graph['public_rehearsal']}", json={
        'song_ids': [content_graph['member_song']],
    })
    # Same-band member song is accepted; the cross-band case is tested below.
    assert response.status_code == 200
    response = client.put(f"/rehearsals/{content_graph['public_rehearsal']}", json={
        'song_ids': [content_graph['corrupt_song'], content_graph['foreign_song']],
    })
    assert response.status_code == 404
    with access_app.app_context():
        rehearsal = db.session.get(Rehearsal, content_graph['public_rehearsal'])
        assert [song.id for song in rehearsal.songs] == [content_graph['member_song']]
