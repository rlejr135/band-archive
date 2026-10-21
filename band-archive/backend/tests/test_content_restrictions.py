"""Rights-case restrictions hide reads and URLs without removing content."""
from datetime import date
import uuid

import pytest
from sqlalchemy import event

from content_restrictions import is_restricted, require_unrestricted_key, set_restriction
from errors import NotFoundError, ValidationError
from extensions import db
from models import (Announcement, AppUser, AuditEvent, Band, BandLogo, Comment,
                    ContentRestriction, GalleryImage, Media, Member, PersonalLog,
                    Rehearsal, Song, SongSuggestion, TenantBackfillState)
from storage import storage
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import principals, write_client  # noqa: F401


@pytest.fixture
def rights_graph(access_app, monkeypatch):
    monkeypatch.setattr(storage, 'generate_url', lambda key, **kwargs: 'https://fixture.invalid/' + key)
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        band.default_visibility = 'PUBLIC'
        band.member_roster_public = True
        band.is_legacy_import = True
        db.session.add(TenantBackfillState(key='phase4_tenant_scope', status='validated'))
        for user in AppUser.query.all():
            user.status, user.email_verified = 'active', True
        db.session.get(AppUser, 7).is_platform_admin = True
        song = Song.query.filter_by(band_id=band.id, title='A public').one()
        rehearsal = Rehearsal(band_id=band.id, visibility='PUBLIC', title='Practice', date=date(2026, 10, 21))
        rehearsal.songs.append(song)
        member = Member(band_id=band.id, name='Player', instrument='bass')
        gallery = GalleryImage(band_id=band.id, filename='cover.jpg', object_key='bands/fixture/cover.jpg',
                               is_featured=True, visibility='PUBLIC')
        announcement = Announcement(band_id=band.id, content='Notice', visibility='PUBLIC')
        suggestion = SongSuggestion(band_id=band.id, title='Next', artist='Band', link='https://example.invalid')
        db.session.add_all((rehearsal, member, gallery, announcement, suggestion))
        db.session.flush()
        media = Media(band_id=band.id, song_id=song.id, rehearsal_id=rehearsal.id, visibility='PUBLIC',
                      filename='original.mp4', object_key='bands/fixture/original.mp4', file_type='video',
                      transcoding_status='completed', video_720_object_key='bands/fixture/720.mp4',
                      audio_object_key='bands/fixture/audio.m4a')
        log = PersonalLog(band_id=band.id, member_id=member.id, title='Log', filename='log.mp4',
                          file_type='video', visibility='PUBLIC', transcoding_status='completed',
                          object_key='bands/fixture/log.mp4', audio_object_key='bands/fixture/log.m4a')
        db.session.add_all((media, log, BandLogo(band_id=band.id, gallery_image_id=gallery.id)))
        db.session.flush()
        comment = Comment(band_id=band.id, media_id=media.id, author='Member', content='Parent')
        db.session.add(comment)
        db.session.flush()
        reply = Comment(band_id=band.id, media_id=media.id, parent_id=comment.id, author='Member', content='Reply')
        log_comment = Comment(band_id=band.id, personal_log_id=log.id, author='Member', content='Log note')
        db.session.add_all((reply, log_comment))
        db.session.commit()
        return {name: item.id for name, item in {
            'band': band, 'song': song, 'rehearsal': rehearsal, 'member': member, 'media': media,
            'personal_log': log, 'gallery': gallery, 'announcement': announcement,
            'suggestion': suggestion, 'comment': comment, 'reply': reply, 'log_comment': log_comment,
        }.items()}


def change(app, graph, kind, *, active=True, case=None, actor=7, apply=True):
    case = case or str(uuid.uuid4())
    with app.app_context():
        result = set_restriction(band_id=graph['band'], resource_type=kind, resource_id=graph[kind],
                                 case_ref=case, actor_user_id=actor, active=active,
                                 reason_code='privacy' if active else None, apply=apply)
        if apply:
            db.session.commit()
        else:
            db.session.rollback()
        return result


def test_case_release_is_independent_idempotent_and_keeps_content(access_app, rights_graph):
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    assert change(access_app, rights_graph, 'media', case=first)['changed']
    assert not change(access_app, rights_graph, 'media', case=first)['changed']
    change(access_app, rights_graph, 'media', case=second)
    change(access_app, rights_graph, 'media', case=first, active=False)
    with access_app.app_context():
        media = db.session.get(Media, rights_graph['media'])
        assert is_restricted(media)
        assert Media.query.count() == 1 and Comment.query.count() == 3
        assert media.audio_object_key == 'bands/fixture/audio.m4a'
        assert AuditEvent.query.filter_by(event_type='content_restricted').count() == 2
    change(access_app, rights_graph, 'media', case=second, active=False)
    with access_app.app_context():
        assert not is_restricted(db.session.get(Media, rights_graph['media']))


@pytest.mark.parametrize('actor', [1, 2, 3])
def test_owner_admin_member_cannot_operate_rights_cli_service(access_app, rights_graph, actor):
    with pytest.raises(ValidationError) as error:
        change(access_app, rights_graph, 'media', actor=actor)
    assert error.value.status_code == 403


@pytest.mark.parametrize('status,verified', [('disabled', True), ('active', False)])
def test_db_admin_must_be_currently_active_and_verified(access_app, rights_graph, status, verified):
    with access_app.app_context():
        actor = db.session.get(AppUser, 7)
        actor.status, actor.email_verified = status, verified
        db.session.commit()
    with pytest.raises(ValidationError):
        change(access_app, rights_graph, 'media')


def test_dry_run_and_cross_band_target_cannot_mutate(access_app, rights_graph):
    assert change(access_app, rights_graph, 'media', apply=False)['applied'] is False
    with access_app.app_context():
        assert ContentRestriction.query.count() == 0
        other = Band.query.filter_by(slug='band-b').one()
        with pytest.raises(NotFoundError):
            set_restriction(band_id=other.id, resource_type='media', resource_id=rights_graph['media'],
                            case_ref=str(uuid.uuid4()), actor_user_id=7, active=True,
                            reason_code='privacy', apply=True)
        assert ContentRestriction.query.count() == 0


@pytest.mark.parametrize('kind', ['media', 'song', 'rehearsal'])
def test_restricted_media_or_parent_hides_all_sources_and_comments(access_app, rights_graph, write_client, principals, kind):
    change(access_app, rights_graph, kind)
    band, media, song = (rights_graph[key] for key in ('band', 'media', 'song'))
    for actor in ({}, principals['owner'], principals['member'], principals['outsider']):
        for path in (f'/media/{media}/processing', f'/media/{media}/comments'):
            assert write_client.get(f'/api/v2/bands/{band}' + path, headers=actor).status_code == 404
        if kind == 'song':
            assert write_client.get(f'/api/v2/bands/{band}/songs/{song}/media', headers=actor).status_code == 404
        else:
            data = write_client.get(f'/api/v2/bands/{band}/songs/{song}/media', headers=actor).get_json()
            assert data['media'] == [] and data['pagination']['total'] == 0
    with access_app.app_context():
        for key in ('bands/fixture/original.mp4', 'bands/fixture/720.mp4', 'bands/fixture/audio.m4a'):
            with pytest.raises(NotFoundError):
                require_unrestricted_key(key)
    assert write_client.get('/uploads/original.mp4').status_code == 404


@pytest.mark.parametrize('kind', ['member', 'personal_log'])
def test_log_and_member_restrictions_hide_child_comments_and_audio(access_app, rights_graph, write_client, principals, kind):
    change(access_app, rights_graph, kind)
    band, log = rights_graph['band'], rights_graph['personal_log']
    for actor in ({}, principals['owner'], principals['outsider']):
        for suffix in ('processing', 'comments'):
            assert write_client.get(f'/api/v2/bands/{band}/personal-logs/{log}/{suffix}', headers=actor).status_code == 404
    with access_app.app_context():
        assert is_restricted(db.session.get(Comment, rights_graph['log_comment']))
        with pytest.raises(NotFoundError):
            require_unrestricted_key('bands/fixture/log.m4a')
    assert write_client.get('/uploads/personal_logs/log.mp4').status_code == 404


def test_comment_restriction_hides_descendants_not_parent_media(access_app, rights_graph, write_client):
    change(access_app, rights_graph, 'comment')
    band, media = rights_graph['band'], rights_graph['media']
    response = write_client.get(f'/api/v2/bands/{band}/media/{media}/comments')
    assert response.status_code == 200 and response.get_json()['comments'] == []
    assert write_client.get(f'/api/v2/bands/{band}/media/{media}/processing').status_code == 200
    with access_app.app_context():
        assert is_restricted(db.session.get(Comment, rights_graph['reply']))


def test_gallery_restriction_covers_logo_featured_intro_dashboard_and_management(access_app, rights_graph, write_client, principals):
    change(access_app, rights_graph, 'gallery')
    band = rights_graph['band']
    for actor in ({}, principals['owner'], principals['outsider']):
        gallery = write_client.get(f'/api/v2/bands/{band}/gallery', headers=actor)
        assert gallery.status_code == 200 and gallery.get_json()['gallery'] == []
        assert write_client.get(f'/api/v2/bands/{band}/gallery/featured', headers=actor).status_code == 404
        snapshot = write_client.get(f'/api/v2/bands/{band}/dashboard', headers=actor).get_json()['dashboard']
        assert snapshot['featured_gallery'] is None and snapshot['recent_gallery'] == []
        assert write_client.get('/api/v2/bands/by-slug/band-a', headers=actor).get_json()['band']['logo'] is None
    for actor in ('owner', 'outsider'):
        managed = write_client.get('/api/v2/bands/by-slug/band-a/logo-management', headers=principals[actor])
        assert managed.status_code == 200
        assert managed.get_json()['gallery'] == [] and managed.get_json()['logo'] is None
    intro = write_client.get('/api/v2/bands/by-slug/band-a/introduction').get_json()
    assert 'bands/fixture/cover.jpg' not in str(intro)


@pytest.mark.parametrize('kind,path,key', [('announcement', 'announcement', None), ('suggestion', 'suggestions', 'suggestions')])
def test_text_restrictions_conceal_public_content(access_app, rights_graph, write_client, kind, path, key):
    change(access_app, rights_graph, kind)
    response = write_client.get(f'/api/v2/bands/{rights_graph["band"]}/{path}')
    if key is None:
        assert response.status_code == 404
    else:
        assert response.status_code == 200 and response.get_json()[key] == []


def test_shared_key_cannot_be_resigned_via_unrestricted_record(access_app, rights_graph):
    change(access_app, rights_graph, 'gallery')
    with access_app.app_context():
        media = db.session.get(Media, rights_graph['media'])
        media.audio_object_key = 'bands/fixture/cover.jpg'
        db.session.commit()
        assert not is_restricted(media)
        with pytest.raises(NotFoundError):
            require_unrestricted_key(media.audio_object_key)


def test_legacy_sheet_alias_cannot_resign_a_restricted_song(access_app, rights_graph):
    change(access_app, rights_graph, 'song')
    with access_app.app_context():
        song = db.session.get(Song, rights_graph['song'])
        song.sheet_music = 'shared-sheet.pdf'
        db.session.commit()
        with pytest.raises(NotFoundError):
            require_unrestricted_key('media/shared-sheet.pdf')


def test_legacy_stats_omit_restricted_song(access_app, rights_graph, write_client):
    before = write_client.get('/dashboard/stats').get_json()['total_songs']
    change(access_app, rights_graph, 'song')
    after = write_client.get('/dashboard/stats')
    assert after.status_code == 200
    assert after.get_json()['total_songs'] == before - 1


@pytest.mark.parametrize('path', ['dashboard', 'songs/{song}/media'])
def test_zero_case_request_has_one_constant_restriction_query(access_app, rights_graph, write_client, path):
    with access_app.app_context():
        for number in range(20):
            db.session.add(Media(band_id=rights_graph['band'], song_id=rights_graph['song'],
                                 filename=f'additional-{number}.mp3', file_type='audio', visibility='PUBLIC'))
        db.session.commit()
        calls = []
        def queried(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith('SELECT') and 'FROM content_restriction' in statement:
                calls.append(statement)
        event.listen(db.engine, 'before_cursor_execute', queried)
        try:
            response = write_client.get(f'/api/v2/bands/{rights_graph["band"]}/' + path.format(**rights_graph))
            assert response.status_code == 200
            assert len(calls) == 1
        finally:
            event.remove(db.engine, 'before_cursor_execute', queried)


def test_active_case_elsewhere_keeps_fifty_media_request_bounded(access_app, rights_graph, write_client, monkeypatch):
    with access_app.app_context():
        other = Band.query.filter_by(slug='band-b').one()
        other_song = Song.query.filter_by(band_id=other.id).first()
        set_restriction(band_id=other.id, resource_type='song', resource_id=other_song.id,
                        case_ref=str(uuid.uuid4()), actor_user_id=7, active=True,
                        reason_code='privacy', apply=True)
        for number in range(50):
            db.session.add(Media(band_id=rights_graph['band'], song_id=rights_graph['song'],
                                 filename=f'large-{number}.mp4', file_type='video', visibility='PUBLIC',
                                 transcoding_status='completed', audio_filename=f'large-{number}.m4a'))
        db.session.commit()
        # Exercise the actual signing boundary as well as the route serializer.
        from storage import StorageClient
        class Signer:
            def generate_presigned_url(self, *args, **kwargs):
                return 'https://fixture.invalid/' + kwargs['Params']['Key']
        client = StorageClient()
        client._client, client._bucket, client._presign_expires = Signer(), 'fixture', 3600
        monkeypatch.setattr(storage, 'generate_url', client.generate_url)
        calls = []
        def queried(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith(('SELECT', 'WITH')) and 'content_restriction' in statement:
                calls.append(statement)
        event.listen(db.engine, 'before_cursor_execute', queried)
        try:
            response = write_client.get(f'/api/v2/bands/{rights_graph["band"]}/songs/{rights_graph["song"]}/media?limit=100')
            assert response.status_code == 200 and len(response.get_json()['media']) == 51
            assert len(calls) <= 3
        finally:
            event.remove(db.engine, 'before_cursor_execute', queried)


def test_negative_cache_is_one_query_and_does_not_cross_http_requests(access_app, rights_graph):
    with access_app.app_context():
        calls = []
        def queried(connection, cursor, statement, parameters, context, many):
            if 'content_restriction' in statement:
                calls.append(statement)
        event.listen(db.engine, 'before_cursor_execute', queried)
        try:
            with access_app.test_request_context():
                media = db.session.get(Media, rights_graph['media'])
                for _ in range(6):
                    assert not is_restricted(media)
                    require_unrestricted_key('bands/fixture/audio.m4a')
                assert len(calls) == 1
            case = str(uuid.uuid4())
            set_restriction(band_id=rights_graph['band'], resource_type='media', resource_id=rights_graph['media'],
                            case_ref=case, actor_user_id=7, active=True, reason_code='privacy', apply=True)
            db.session.commit()
            with access_app.test_request_context():
                assert is_restricted(media)
                previous = len(calls)
                assert is_restricted(media)
                assert len(calls) == previous
                # A mutation in this same request invalidates its snapshot.
                set_restriction(band_id=rights_graph['band'], resource_type='media', resource_id=media.id,
                                case_ref=case, actor_user_id=7, active=False, apply=True)
                assert not is_restricted(media)
        finally:
            event.remove(db.engine, 'before_cursor_execute', queried)
            db.session.rollback()
