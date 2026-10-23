"""Contract tests for the first tenant-scoped Phase 6 content read slice.

The v2 lists are intentionally deterministic: songs use legacy id order,
rehearsals use date/id, media uses score-desc/id, and comments use created/id.
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from extensions import db
from models import AppUser, Band, BandMembership, Comment, Media, MediaVote, Rehearsal, Song, TenantBackfillState
from tests.test_auth import _install_jwks, _jwk, _token
# Share the authenticated current-schema fixture with the central-access
# contracts; importing the fixture functions makes them local pytest fixtures.
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from v2_content_serializers import _media_display_name


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
    assert set(songs.get_json()) == {'songs', 'pagination'}
    song = next(item for item in songs.get_json()['songs'] if item['id'] == content_graph['playback_song'])
    assert song['id'] == content_graph['playback_song']
    assert song['visibility'] == song['effective_visibility'] == 'PUBLIC'
    assert [item['id'] for item in song['rehearsals']] == [content_graph['public_rehearsal']]
    assert song['rehearsals'][0]['effective_visibility'] == 'PUBLIC'
    assert song['rehearsals'][0]['rehearsal_type'] == 'UNCLASSIFIED'
    # Summaries stay storage-neutral.  Media is only available through the
    # dedicated SQL-paginated endpoint below.
    assert 'media' not in song
    assert song['can_edit'] is song['can_delete'] is song['can_manage'] is False

    media = client.get(f"{base}/songs/{content_graph['playback_song']}/media")
    assert media.status_code == 200
    assert [item['id'] for item in media.get_json()['media']] == [content_graph['public_media']]
    item = media.get_json()['media'][0]
    assert 'object_key' not in item and 'filename' not in item
    assert item['display_name'] == 'visible.mp4'
    # Direct R2 presigned URLs are expected to encode a storage path.  It is
    # the only response field allowed to do so, and expires at the policy TTL.
    assert 'bands/b/public/visible-720.mp4' in item['playback']['video_url']
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
    flat_comments = comments.get_json()['comments']
    assert all('replies' not in item for item in flat_comments)
    assert any(item['parent_id'] is not None and item['content'] == 'reply' for item in flat_comments)
    assert client.get(f"{base}/media/{content_graph['hidden_media']}/comments").status_code == 404


def test_media_display_name_uses_sanitized_original_name_and_safe_fallbacks(
        access_app, content_graph, monkeypatch):
    from storage import storage
    monkeypatch.setattr(storage, 'generate_url', lambda key, expires_in=None: 'signed-playback-url')
    client = access_app.test_client()
    base = f"/api/v2/bands/{content_graph['public_band']}"
    media_url = f"{base}/songs/{content_graph['playback_song']}/media"

    with access_app.app_context():
        media = db.session.get(Media, content_graph['public_media'])
        # A control character is removed while Unicode and the Windows basename remain.
        media.original_filename = 'C:\\Users\\비밀\\演\n奏🎵.mp4'
        db.session.commit()
    payload = client.get(media_url).get_json()['media'][0]
    assert payload['display_name'] == '演奏🎵.mp4'
    assert 'object_key' not in payload and 'filename' not in payload
    assert 'bands/b/public/' not in str(payload)

    for file_type, expected in (('video', f"영상 #{content_graph['public_media']}"),
                                ('audio', f"음원 #{content_graph['public_media']}")):
        with access_app.app_context():
            media = db.session.get(Media, content_graph['public_media'])
            media.original_filename = None
            media.file_type = file_type
            media.filename = 'private-internal-filename.mp4'
            media.object_key = 'private/object-key.mp4'
            media.video_720_object_key = 'private/derivative.mp4'
            media.audio_object_key = 'private/audio-key.m4a'
            db.session.commit()
        payload = client.get(media_url).get_json()['media'][0]
        assert payload['display_name'] == expected
        assert 'private-internal-filename' not in str(payload)
        assert 'private/object-key' not in str(payload)
        assert 'private/derivative' not in str(payload)
        assert 'private/audio-key' not in str(payload)


@pytest.mark.parametrize(('original_filename', 'file_type', 'expected'), [
    ('/tmp/演奏🎵.mp4', 'video', '演奏🎵.mp4'),
    ('   ', 'audio', '음원 #42'),
    ('folder/name\x85.mp3', 'audio', 'name.mp3'),
    ('x' * 205, 'video', 'x' * 200),
    (None, 'image', '파일 #42'),
])
def test_media_display_name_pure_sanitization_contract(original_filename, file_type, expected):
    media = SimpleNamespace(
        id=42, file_type=file_type, original_filename=original_filename,
        filename='internal-filename-secret', object_key='internal/object-key-secret',
    )
    assert _media_display_name(media) == expected


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
        # This v1 compatibility route is intentionally limited to the
        # validated import band, so create an isolated legacy graph instead
        # of borrowing Band B's v2 content fixture.
        legacy_band = Band(name='legacy relation writer', slug='legacy-relation-writer',
                           default_visibility='PUBLIC', storage_quota_bytes=1, is_legacy_import=True)
        db.session.add(legacy_band)
        db.session.flush()
        db.session.add(TenantBackfillState(key='phase4_tenant_scope', status='validated'))
        db.session.flush()
        legacy_song = Song(band_id=legacy_band.id, visibility='PUBLIC', title='Legacy relation', artist='Legacy')
        legacy_replacement_song = Song(
            band_id=legacy_band.id, visibility='PUBLIC', title='Legacy replacement', artist='Legacy',
        )
        legacy_rehearsal = Rehearsal(
            band_id=legacy_band.id, visibility='PUBLIC', title='Legacy rehearsal', date=date(2026, 1, 6),
        )
        db.session.add_all((legacy_song, legacy_replacement_song, legacy_rehearsal))
        db.session.flush()
        legacy_rehearsal.songs.append(legacy_song)
        db.session.commit()
        legacy_song_id = legacy_song.id
        legacy_replacement_song_id = legacy_replacement_song.id
        legacy_rehearsal_id = legacy_rehearsal.id
    # v2 Band B remains outside the legacy-only v1 writer boundary.
    response = client.put(f"/rehearsals/{content_graph['public_rehearsal']}", json={
        'song_ids': [content_graph['member_song']],
    })
    assert response.status_code == 404
    response = client.put(f"/rehearsals/{legacy_rehearsal_id}", json={
        'song_ids': [legacy_replacement_song_id],
    })
    assert response.status_code == 200
    response = client.put(f"/rehearsals/{legacy_rehearsal_id}", json={
        'song_ids': [legacy_replacement_song_id, content_graph['foreign_song']],
    })
    assert response.status_code == 404
    with access_app.app_context():
        rehearsal = db.session.get(Rehearsal, legacy_rehearsal_id)
        assert [song.id for song in rehearsal.songs] == [legacy_replacement_song_id]
        assert legacy_song_id != legacy_replacement_song_id


def test_collections_have_bounded_deterministic_pages_and_reject_bad_paging(access_app, content_graph):
    client = access_app.test_client()
    base = f"/api/v2/bands/{content_graph['public_band']}"
    first = client.get(f'{base}/songs?limit=1&offset=0')
    assert first.status_code == 200
    payload = first.get_json()
    assert len(payload['songs']) == 1
    assert payload['pagination'] == {
        'limit': 1, 'offset': 0, 'returned': 1, 'total': 3, 'has_more': True, 'next_offset': 1,
    }
    second = client.get(f'{base}/songs?limit=1&offset=1').get_json()
    assert second['songs'][0]['id'] > payload['songs'][0]['id']
    for query in ('limit=0', 'limit=101', 'limit=bad', 'offset=-1', 'offset=100001'):
        assert client.get(f'{base}/songs?{query}').status_code == 400


def test_comments_are_a_flat_bounded_deterministic_page(access_app, content_graph):
    with access_app.app_context():
        for index in range(5):
            root = Comment(band_id=content_graph['public_band'], media_id=content_graph['public_media'],
                           author=f'root-{index}', content='root', password_hash='not-used')
            db.session.add(root)
            db.session.flush()
            db.session.add(Comment(
                band_id=content_graph['public_band'], media_id=content_graph['public_media'], parent_id=root.id,
                author=f'reply-{index}', content='reply', password_hash='not-used',
            ))
        db.session.commit()
    response = access_app.test_client().get(
        f"/api/v2/bands/{content_graph['public_band']}/media/{content_graph['public_media']}/comments?limit=3",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload['comments']) == 3
    assert payload['pagination']['total'] == 12
    assert payload['pagination']['has_more'] is True
    assert all('replies' not in item for item in payload['comments'])


def test_media_pages_include_only_the_active_membership_v2_vote(access_app, content_graph, signer):
    """Song and rehearsal pages have one account-specific, non-legacy vote view."""
    with access_app.app_context():
        band_id = content_graph['public_band']
        owner = BandMembership.query.filter_by(band_id=band_id, role='owner').one()
        member_user = AppUser.query.filter_by(email='pending@example.com').one()
        member = BandMembership.query.filter_by(band_id=band_id, user_id=member_user.id).one()
        member.status = 'active'
        media_id = content_graph['public_media']
        db.session.add_all((
            MediaVote(band_id=band_id, media_id=media_id, created_by_membership_id=owner.id,
                      is_legacy=False, voter_hash='a' * 64, value=-1),
            MediaVote(band_id=band_id, media_id=media_id, created_by_membership_id=member.id,
                      is_legacy=False, voter_hash='b' * 64, value=1),
            MediaVote(band_id=band_id, media_id=media_id, created_by_membership_id=None,
                      is_legacy=True, voter_hash='c' * 64, value=1),
        ))
        db.session.commit()

    client = access_app.test_client(); base = f"/api/v2/bands/{content_graph['public_band']}"
    owner_headers = _headers(_token(signer, sub='77777777-7777-7777-7777-777777777777', email='outsider@example.com'))
    member_headers = _headers(_token(signer, sub='44444444-4444-4444-4444-444444444444', email='pending@example.com'))
    song_path = f"{base}/songs/{content_graph['playback_song']}/media?limit=1"
    rehearsal_path = f"{base}/rehearsals/{content_graph['public_rehearsal']}/media?limit=1"
    assert client.get(song_path).get_json()['media'][0]['viewer_vote'] == 0
    assert client.get(song_path, headers=owner_headers).get_json()['media'][0]['viewer_vote'] == -1
    assert client.get(song_path, headers=member_headers).get_json()['media'][0]['viewer_vote'] == 1
    assert client.get(rehearsal_path, headers=owner_headers).get_json()['media'][0]['viewer_vote'] == -1
    assert client.get(rehearsal_path, headers=member_headers).get_json()['media'][0]['viewer_vote'] == 1


def test_song_and_rehearsal_summaries_never_materialize_or_sign_large_media_children(
        access_app, content_graph, monkeypatch):
    from storage import storage

    with access_app.app_context():
        for index in range(101):
            db.session.add(Media(
                band_id=content_graph['public_band'], visibility='PUBLIC',
                song_id=content_graph['playback_song'], rehearsal_id=content_graph['public_rehearsal'],
                filename=f'bulk-{index}.jpg', object_key=f'bands/b/bulk-{index}.jpg', file_type='image',
            ))
        db.session.commit()
    calls = []
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: calls.append((key, expires_in)) or f'url:{key}')
    client = access_app.test_client()
    base = f"/api/v2/bands/{content_graph['public_band']}"
    summaries = (
        client.get(f'{base}/songs'),
        client.get(f"{base}/songs/{content_graph['playback_song']}"),
        client.get(f'{base}/rehearsals'),
        client.get(f"{base}/rehearsals/{content_graph['public_rehearsal']}"),
    )
    assert all(response.status_code == 200 for response in summaries)
    assert calls == []
    for response in summaries:
        body = response.get_json()
        values = body.get('songs') or body.get('rehearsals') or [body.get('song') or body.get('rehearsal')]
        assert all('media' not in item and 'object_key' not in item and 'filename' not in item for item in values)

    media = client.get(f"{base}/songs/{content_graph['playback_song']}/media?limit=1")
    assert media.status_code == 200
    assert media.get_json()['pagination']['returned'] == 1
    # The only page row is the pruned video: 720p + completed audio, and no
    # bulk child from an unrequested page is signed.
    assert len(calls) == 2
