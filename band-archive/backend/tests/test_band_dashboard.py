"""Contracts for the bounded, band-scoped dashboard snapshot."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy import text

from extensions import db
from models import (
    Announcement, Band, BandMembership, DirectUploadSession, GalleryImage, Media, Member,
    MultipartUploadSession, PersonalLog, PersonalLogMultipartUploadSession, Rehearsal, Song,
)
from storage import storage
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


def _principal(signer, subject, email):
    return _headers(_token(signer, sub=subject, email=email))


def test_dashboard_aggregates_only_visible_current_band_graph(access_app, signer, monkeypatch):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    signed = []
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: signed.append((key, expires_in)) or f'url:{key}')
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-b').one()
        other_band = Band.query.filter_by(slug='band-a').one()
        private_band_id = other_band.id
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        other_owner = BandMembership.query.filter_by(band_id=other_band.id, role='owner').one()
        member_user_id = BandMembership.query.filter_by(band_id=other_band.id, role='member', status='active').one().user_id
        member = BandMembership(band_id=band.id, user_id=member_user_id, role='member', status='active')
        db.session.add(member)
        db.session.flush()

        db.session.add_all((
            Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                 title='Practice public', artist='A', status='Practice'),
            Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                 title='Completed public', artist='A', status='Completed'),
            Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                 title='On hold public', artist='A', status='OnHold'),
            Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                 title='Unknown status', artist='A', status='Archived'),
            Song(band_id=band.id, created_by_membership_id=member.id, visibility='MEMBERS',
                 title='Private practice', artist='A', status='Practice'),
            Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                 title='Corrupt creator', artist='A', status='Practice'),
            Song(band_id=other_band.id, created_by_membership_id=other_owner.id, visibility='PUBLIC',
                 title='Other band song', artist='A', status='Practice'),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Public rehearsal', date=date(2026, 10, 20)),
            Rehearsal(band_id=band.id, created_by_membership_id=member.id, visibility='MEMBERS',
                      title='Private rehearsal', date=date(2026, 10, 21)),
            Rehearsal(band_id=other_band.id, created_by_membership_id=other_owner.id, visibility='PUBLIC',
                      title='Other band rehearsal', date=date(2026, 10, 22)),
            GalleryImage(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                         filename='recent.jpg', object_key='bands/recent.jpg'),
            GalleryImage(band_id=band.id, created_by_membership_id=member.id, visibility='MEMBERS',
                         filename='featured.jpg', object_key='bands/featured.jpg', is_featured=True),
            Announcement(band_id=band.id, created_by_membership_id=member.id,
                         visibility='MEMBERS', content='N' * 10_500),
        ))
        db.session.flush()
        corrupt = Song.query.filter_by(band_id=band.id, title='Corrupt creator').one()
        db.session.execute(text('UPDATE song SET created_by_membership_id = :creator WHERE id = :song'),
                           {'creator': other_owner.id, 'song': corrupt.id})
        assert db.session.execute(text('SELECT band_id, created_by_membership_id FROM song WHERE id = :song'),
                                  {'song': corrupt.id}).one() == (band.id, other_owner.id)
        from band_access import current_audience, read_scope_for_id
        from content_queries import _song_query
        with access_app.test_request_context('/'):
            scope = read_scope_for_id(band.id, current_audience())
            assert _song_query(scope).filter(Song.id == corrupt.id).count() == 0
        db.session.commit()
        band_id = band.id
        member_user = member.user

    client = access_app.test_client()
    url = f'/api/v2/bands/{band_id}/dashboard?month=2026-10'
    visitor_response = client.get(url)
    assert visitor_response.status_code == 200
    visitor = visitor_response.get_json()['dashboard']
    assert visitor['stats'] == {
        'total_songs': 5,
        'total_rehearsals': 1,
        'status_counts': {'Practice': 2, 'Completed': 1, 'OnHold': 1, 'Other': 1},
    }
    assert [song['title'] for song in visitor['practice_songs']] == ['B public', 'Practice public']
    assert [item['title'] for item in visitor['month_rehearsals']['items']] == ['Public rehearsal']
    assert visitor['featured_gallery'] is None
    assert len(visitor['recent_gallery']) == 1
    assert visitor['recent_gallery'][0]['is_featured'] is False
    assert visitor['announcement'] is None
    assert 'storage' not in visitor
    assert signed == [('bands/recent.jpg', 3600)]

    member_headers = _principal(signer, member_user.supabase_user_id, member_user.email)
    member_response = client.get(url, headers=member_headers)
    assert member_response.status_code == 200
    member_view = member_response.get_json()['dashboard']
    assert member_view['stats']['total_songs'] == 6
    assert member_view['stats']['status_counts']['Practice'] == 3
    assert member_view['stats']['total_rehearsals'] == 2
    assert len(member_view['announcement']['content']) == 10_000
    assert member_view['announcement']['content_truncated'] is True
    assert any(item['visibility'] == 'MEMBERS' for item in member_view['recent_gallery'])
    assert member_view['featured_gallery']['url'] == 'url:bands/featured.jpg'
    assert member_view['storage']['used_bytes'] == member_view['storage']['reserved_bytes'] == 0
    assert member_view['storage']['quota_bytes'] > 0
    assert signed[-3:] == [('bands/featured.jpg', 300), ('bands/featured.jpg', 300),
                           ('bands/recent.jpg', 3600)]

    assert client.get(f'/api/v2/bands/{private_band_id}/dashboard?month=2026-10').status_code == 404


def test_dashboard_storage_counts_band_quota_without_cleaning_sessions_or_disclosing_to_nonmembers(access_app, signer, monkeypatch):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: f'url:{key}')
    deleted = []
    monkeypatch.setattr(storage, 'delete', deleted.append)
    expired_at = datetime.now(timezone.utc) - timedelta(hours=1)
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-b').one()
        foreign = Band.query.filter_by(slug='band-a').one()
        band.storage_quota_bytes = 1_000
        expired_direct = None
        for team, scale in ((band, 1), (foreign, 10)):
            owner = BandMembership.query.filter_by(band_id=team.id, role='owner').one()
            song = Song(band_id=team.id, created_by_membership_id=owner.id, title='Quota test song', artist='Artist')
            member = Member(band_id=team.id, name='연주자', instrument='기타')
            db.session.add_all((song, member))
            db.session.flush()
            common = {'band_id': team.id, 'created_by_membership_id': owner.id, 'visibility': 'MEMBERS'}
            db.session.add_all((
                Media(**common, song_id=song.id, filename='video.mp4', file_size=10 * scale,
                      audio_object_key='audio.m4a', video_720_object_key='video720.mp4'),
                PersonalLog(**common, member_id=member.id, title='연습 기록', filename='log.m4a',
                            file_type='audio', file_size=20 * scale),
                GalleryImage(**common, filename='image.jpg', file_size=30 * scale),
            ))
            session = {'band_id': team.id, 'created_by_membership_id': owner.id,
                       'original_filename': 'upload.mp4', 'content_type': 'video/mp4'}
            expired_upload = DirectUploadSession(
                **session, kind='media', song_id=song.id, object_key='direct/expired',
                capability_token_hash='test', declared_bytes=13 * scale, expires_at=expired_at,
            )
            if team.id == band.id:
                expired_direct = expired_upload
            db.session.add_all((
                expired_upload,
                DirectUploadSession(**session, kind='media', song_id=song.id, object_key='direct/cleanup',
                                    capability_token_hash='test', declared_bytes=17 * scale, status='expiring'),
                DirectUploadSession(**session, kind='media', song_id=song.id, object_key='direct/done',
                                    capability_token_hash='test', declared_bytes=100 * scale, status='completed'),
                MultipartUploadSession(**session, song_id=song.id, object_key='multipart/hold',
                                       r2_upload_id='media-upload', declared_bytes=7 * scale, status='aborting'),
                PersonalLogMultipartUploadSession(**session, member_id=member.id, title='연습 기록',
                                                  object_key='multipart/log', r2_upload_id='log-upload',
                                                  declared_bytes=11 * scale, status='completing'),
            ))
        db.session.commit()
        expired_direct_id = expired_direct.id
        band_id = band.id
        member_headers = _principal(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com')

    client = access_app.test_client()
    url = f'/api/v2/bands/{band_id}/dashboard?month=2026-10'
    member_response = client.get(url, headers=member_headers)
    assert member_response.status_code == 200
    assert member_response.get_json()['dashboard']['storage'] == {
        'used_bytes': 60, 'reserved_bytes': 48, 'quota_bytes': 1_000,
    }
    assert member_response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in member_response.headers['Vary']
    nonmembers = [
        {},
        _headers(_token(signer)),
        _principal(signer, '44444444-4444-4444-4444-444444444444', 'pending@example.com'),
        _principal(signer, '55555555-5555-5555-5555-555555555555', 'rejected@example.com'),
        _principal(signer, '66666666-6666-6666-6666-666666666666', 'left@example.com'),
    ]
    for headers in nonmembers:
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        assert 'storage' not in response.get_json()['dashboard']

    assert deleted == []
    with access_app.app_context():
        assert db.session.get(DirectUploadSession, expired_direct_id).status == 'initiated'


def test_dashboard_month_overlap_pagination_upcoming_and_month_validation(access_app, signer, monkeypatch):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    from routes import content_reads

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 12, 31, 15, 30, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value

    monkeypatch.setattr(content_reads, 'datetime', FrozenDateTime)
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-b').one()
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        rows = [
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='First day', date=date(2026, 12, 1)),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Ends at first day', date=date(2026, 11, 20),
                      start_date=date(2026, 11, 30), end_date=date(2026, 12, 1)),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='C' * 250, date=date(2026, 12, 31),
                      start_date=date(2026, 12, 31), end_date=date(2027, 1, 3),
                      time='T' * 30, location='L' * 300),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Partial legacy date', date=date(2026, 12, 15),
                      start_date=date(2026, 12, 30), end_date=None),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Starts next month', date=date(2027, 1, 1),
                      start_date=date(2027, 1, 1), end_date=date(2027, 1, 2)),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Future one', date=date(2027, 1, 4)),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Future two', date=date(2027, 1, 5)),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Future three', date=date(2027, 1, 6)),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Future four', date=date(2027, 1, 7)),
            Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      title='Expired range, future canonical date', date=date(2027, 1, 2),
                      start_date=date(2026, 12, 30), end_date=date(2026, 12, 31)),
        ]
        linked_public = [Song(band_id=band.id, created_by_membership_id=owner.id,
                              visibility='PUBLIC', title=('S' * 150 if index == 0 else f'Visible linked {index}'), artist='A')
                         for index in range(6)]
        linked_private = Song(band_id=band.id, created_by_membership_id=owner.id,
                              visibility='MEMBERS', title='Hidden linked', artist='A')
        rows[2].songs.extend(linked_public + [linked_private])
        old_songs = [Song(band_id=band.id, created_by_membership_id=owner.id,
                          visibility='PUBLIC', title=f'Old song {index}', artist='A', status='Practice')
                     for index in range(30)]
        old_rehearsals = [Rehearsal(band_id=band.id, created_by_membership_id=owner.id,
                                    visibility='PUBLIC', title=f'Old rehearsal {index}',
                                    date=date(2026, 2, 1)) for index in range(30)]
        db.session.add_all(rows)
        db.session.add_all(linked_public + [linked_private] + old_songs + old_rehearsals)
        db.session.commit()
        band_id = band.id

    client = access_app.test_client()
    query_count = []
    with access_app.app_context():
        def count_query(*_args):
            query_count.append(1)
        event.listen(db.engine, 'before_cursor_execute', count_query)
    try:
        response = client.get(f'/api/v2/bands/{band_id}/dashboard?month=2026-12&limit=2&offset=0')
    finally:
        with access_app.app_context():
            event.remove(db.engine, 'before_cursor_execute', count_query)
    assert response.status_code == 200
    dashboard = response.get_json()['dashboard']
    assert dashboard['today'] == '2027-01-01'
    assert len(query_count) <= 20
    month_page = dashboard['month_rehearsals']
    assert month_page['pagination'] == {
        'limit': 2, 'offset': 0, 'returned': 2, 'total': 5,
        'has_more': True, 'next_offset': 2,
    }
    assert [item['title'] for item in month_page['items']] == ['Ends at first day', 'First day']
    assert [item['title'] for item in dashboard['upcoming_rehearsals']] == [
        'C' * 200, 'Starts next month', 'Future one',
    ]
    assert all(item['title'] != 'Expired range, future canonical date'
               for item in dashboard['upcoming_rehearsals'])
    linked = dashboard['upcoming_rehearsals'][0]
    assert [song['title'] for song in linked['songs']] == ['S' * 100] + [f'Visible linked {index}' for index in range(1, 5)]
    assert linked['has_more_songs'] is True
    assert all(song['title'] != 'Hidden linked' for song in linked['songs'])
    assert len(linked['location']) == 200
    assert len(linked['time']) == 20
    assert not {'memo', 'latitude', 'longitude'} & linked.keys()
    assert not {'memo', 'chords', 'link', 'genre', 'difficulty'} & linked['songs'][0].keys()
    assert not {'memo', 'chords', 'link'} & dashboard['practice_songs'][0].keys()

    january = client.get(f'/api/v2/bands/{band_id}/dashboard?month=2027-01&limit=10')
    assert january.status_code == 200
    assert {item['title'] for item in january.get_json()['dashboard']['month_rehearsals']['items']} >= {
        'C' * 200, 'Starts next month', 'Expired range, future canonical date',
    }
    for invalid in ('2026-00', '2026-13', '2026-1', '2026-01x', '0000-01', '２０２６-01'):
        assert client.get(f'/api/v2/bands/{band_id}/dashboard?month={invalid}').status_code == 400
    assert client.get(f'/api/v2/bands/{band_id}/dashboard?month=2026-12&limit=0').status_code == 400
    assert client.get(f'/api/v2/bands/{band_id}/dashboard?month=2026-12&offset=-1').status_code == 400
