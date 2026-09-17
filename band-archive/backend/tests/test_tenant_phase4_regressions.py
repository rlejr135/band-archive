"""Real-Alembic regression coverage for the Phase 4 compatibility boundary.

These tests intentionally never use ``drop_all``/``create_all``.  They start
from revision 04 or revision 05 so a mapped future column can only be reached
through the production readiness guard.
"""

from datetime import date

import pytest
from flask_migrate import upgrade
from sqlalchemy import text

from app import create_app
from config import TestingConfig
from extensions import db
from media_processing import create_media, save_media_and_start
from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app
from models import (
    Announcement, AppUser, Band, BandMembership, Comment, GalleryImage, Media,
    MediaVote, Member, MultipartUploadSession, PersonalLog,
    PersonalLogMultipartUploadSession, Rehearsal, Song, SongSuggestion, SongVote,
)
from tenant_backfill import TenantBackfillError, apply, plan, _validate_relationships


def _url(path):
    return f"sqlite:///{path.as_posix()}"


def _migration_app(path):
    return create_migration_app(_url(path))


def _runtime_config(path):
    return type('Phase4RuntimeConfig', (TestingConfig,), {
        'SQLALCHEMY_DATABASE_URI': _url(path),
        'SUPABASE_AUTH_ENABLED': False,
    })


def _dispose(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _upgrade(path, revision):
    app = _migration_app(path)
    try:
        with app.app_context():
            upgrade(revision=revision, directory=str(MIGRATIONS_DIRECTORY))
    finally:
        _dispose(app)


_V1_READS = ('/', '/songs', '/rehearsals', '/members', '/gallery', '/announcement', '/suggestions')
_V1_WRITES = (
    ('/songs', {'title': 'blocked', 'artist': 'blocked'}),
    ('/rehearsals', {'title': 'blocked', 'date': '2026-01-01'}),
    ('/members', {'name': 'blocked', 'instrument': 'guitar'}),
    ('/suggestions', {'title': 'blocked', 'artist': 'blocked', 'link': 'https://example.test'}),
)


@pytest.mark.parametrize('revision', ('20260916_04', '20260916_05'))
def test_phase4_schema_boundary_never_blocks_normal_cors_preflight(tmp_path, monkeypatch, revision):
    """OPTIONS must remain a browser transport concern, not a tenant write."""
    path = tmp_path / f'{revision}.db'
    _upgrade(path, revision)
    origin = 'https://band-archive.pages.dev'
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', origin)

    class BrowserConfig(_runtime_config(path)):
        DEBUG = False

    runtime = create_app(BrowserConfig, start_worker=False)
    try:
        response = runtime.test_client().options('/songs', headers={
            'Origin': origin,
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type,x-voter-id',
        })
        assert response.status_code == 200
        assert response.headers['Access-Control-Allow-Origin'] == origin
        allowed_headers = {
            item.strip().lower()
            for item in response.headers['Access-Control-Allow-Headers'].split(',')
        }
        assert {'content-type', 'x-voter-id'} <= allowed_headers
        assert 'POST' in response.headers['Access-Control-Allow-Methods']
    finally:
        _dispose(runtime)


def test_revision_four_blocks_every_orm_backed_v1_read_and_write_without_mutation(tmp_path):
    path = tmp_path / 'revision-four.db'
    _upgrade(path, '20260916_04')
    runtime = create_app(_runtime_config(path), start_worker=False)
    try:
        client = runtime.test_client()
        for endpoint in _V1_READS:
            response = client.get(endpoint)
            assert response.status_code == 503
            assert response.get_json()['error'] == 'tenant_schema_unavailable'
        with runtime.app_context():
            before = db.session.execute(text('SELECT count(*) FROM song')).scalar_one()
        for endpoint, payload in _V1_WRITES:
            response = client.post(endpoint, json=payload)
            assert response.status_code == 503
            assert response.get_json()['error'] == 'tenant_schema_unavailable'
        with runtime.app_context():
            assert db.session.execute(text('SELECT count(*) FROM song')).scalar_one() == before
    finally:
        _dispose(runtime)


def test_revision_five_preserves_reads_but_blocks_writes_until_backfill(tmp_path):
    path = tmp_path / 'revision-five-unbackfilled.db'
    _upgrade(path, '20260916_05')
    runtime = create_app(_runtime_config(path), start_worker=False)
    try:
        client = runtime.test_client()
        assert client.get('/songs').status_code == 200
        for endpoint, payload in _V1_WRITES:
            response = client.post(endpoint, json=payload)
            assert response.status_code == 503
            assert response.get_json()['error'] == 'tenant_backfill_required'
        with runtime.app_context():
            assert db.session.execute(text('SELECT count(*) FROM song')).scalar_one() == 0
            assert db.session.execute(text('SELECT count(*) FROM band')).scalar_one() == 0
    finally:
        _dispose(runtime)


def test_backfill_dry_plan_is_byte_stable_validates_before_success_and_apply_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / 'backfill-plan.db'
    _upgrade(path, '20260916_05')
    app = _migration_app(path)
    try:
        with app.app_context():
            before = path.read_bytes()
            with db.engine.connect() as connection:
                first = plan(connection)
                second = plan(connection)
            assert first == second
            assert path.read_bytes() == before

            called = []
            import tenant_backfill
            original = tenant_backfill._validate_relationships

            def observed(connection, band_id):
                called.append(band_id)
                return original(connection, band_id)

            monkeypatch.setattr(tenant_backfill, '_validate_relationships', observed)
            with db.engine.begin() as connection:
                report = apply(connection)
                assert report['status'] == 'validated'
            assert called, 'apply must validate relationships before it reports success'

            # A failed operator transaction must leave no ambiguous marker.
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM tenant_backfill_state"))
                connection.execute(text("DELETE FROM band WHERE is_legacy_import = 1"))
            connection = db.engine.connect()
            transaction = connection.begin()
            try:
                apply(connection)
                transaction.rollback()
            finally:
                connection.close()
            with db.engine.connect() as connection:
                assert connection.execute(text('SELECT count(*) FROM band')).scalar_one() == 0
                assert connection.execute(text('SELECT count(*) FROM tenant_backfill_state')).scalar_one() == 0
    finally:
        _dispose(app)


def _backfilled_runtime(path):
    _upgrade(path, '20260916_05')
    migration = _migration_app(path)
    try:
        with migration.app_context(), db.engine.begin() as connection:
            apply(connection)
    finally:
        _dispose(migration)
    return create_app(_runtime_config(path), start_worker=False)


def test_validated_backfill_assigns_legacy_scope_for_v1_writer_families(tmp_path):
    runtime = _backfilled_runtime(tmp_path / 'writer-families.db')
    try:
        with runtime.app_context():
            legacy_band_id = db.session.execute(text(
                'SELECT id FROM band WHERE is_legacy_import = 1'
            )).scalar_one()

            song = Song(title='song', artist='artist')
            rehearsal = Rehearsal(title='rehearsal', date=date(2026, 1, 1))
            member = Member(name='member', instrument='bass')
            db.session.add_all((song, rehearsal, member))
            db.session.commit()
            rehearsal.songs.append(song)
            db.session.commit()

            media = Media(song_id=song.id, rehearsal_id=rehearsal.id, filename='clip.mp4', file_type='video')
            log = PersonalLog(member_id=member.id, title='log', filename='log.mp4', file_type='video')
            gallery = GalleryImage(filename='gallery.png')
            announcement = Announcement(content='notice')
            suggestion = SongSuggestion(title='suggestion', artist='artist', link='https://example.test')
            db.session.add_all((media, log, gallery, announcement, suggestion))
            db.session.commit()

            root = Comment(media_id=media.id, author='author', content='root')
            root.set_password('password')
            db.session.add(root)
            db.session.commit()
            reply = Comment(parent_id=root.id, author='author', content='reply')
            reply.set_password('password')
            song_vote = SongVote(song_id=song.id, voter_hash='song-voter', value=1)
            media_vote = MediaVote(media_id=media.id, voter_hash='media-voter', value=1)
            media_session = MultipartUploadSession(
                r2_upload_id='media-upload', object_key='media/clip.mp4', original_filename='clip.mp4',
                content_type='video/mp4', declared_bytes=1, song_id=song.id,
            )
            log_session = PersonalLogMultipartUploadSession(
                r2_upload_id='log-upload', object_key='personal_logs/log.mp4', original_filename='log.mp4',
                title='log', content_type='video/mp4', declared_bytes=1, member_id=member.id,
            )
            db.session.add_all((reply, song_vote, media_vote, media_session, log_session))
            db.session.commit()

            # This path is also used by direct-upload completion and the
            # background worker enqueue, rather than only by route constructors.
            background_media = create_media(song_id=song.id, filename='worker.mp4', file_type='video')
            save_media_and_start(runtime, background_media)

            scoped = (song, rehearsal, member, media, log, gallery, announcement, suggestion,
                      root, reply, song_vote, media_vote, media_session, log_session, background_media)
            assert all(item.band_id == legacy_band_id for item in scoped)
            visible = (song, rehearsal, media, log, gallery, announcement, suggestion, background_media)
            assert all(item.visibility == 'PUBLIC' for item in visible)
            assert db.session.execute(text('SELECT count(*) FROM rehearsal_songs')).scalar_one() == 1
    finally:
        _dispose(runtime)


@pytest.mark.parametrize(
    ('label', 'statement', 'expected'),
    [
        ('media/song', 'UPDATE media SET band_id = :other WHERE id = :id', 'media/song'),
        ('log/member', 'UPDATE personal_log SET band_id = :other WHERE id = :id', 'personal_log/member'),
        ('rehearsal/song', 'UPDATE rehearsal SET band_id = :other WHERE id = :id', 'rehearsal/song'),
        ('song vote', 'UPDATE song_vote SET band_id = :other WHERE id = :id', 'song_vote/song'),
        ('media vote', 'UPDATE media_vote SET band_id = :other WHERE id = :id', 'media_vote/media'),
        ('upload target', 'UPDATE multipart_upload_session SET band_id = :other WHERE id = :id', 'session/song'),
    ],
)
def test_relationship_validator_rejects_cross_band_targets(tmp_path, label, statement, expected):
    runtime = _backfilled_runtime(tmp_path / f"{label.replace('/', '-')}.db")
    try:
        with runtime.app_context():
            song = Song(title='song', artist='artist')
            rehearsal = Rehearsal(title='rehearsal', date=date(2026, 1, 1))
            member = Member(name='member', instrument='bass')
            db.session.add_all((song, rehearsal, member))
            db.session.commit()
            rehearsal.songs.append(song)
            media = Media(song_id=song.id, rehearsal_id=rehearsal.id, filename='clip.mp4')
            log = PersonalLog(member_id=member.id, title='log', filename='log.mp4', file_type='video')
            db.session.add_all((media, log))
            db.session.commit()
            vote = SongVote(song_id=song.id, voter_hash='song-voter', value=1)
            media_vote = MediaVote(media_id=media.id, voter_hash='media-voter', value=1)
            session = MultipartUploadSession(
                r2_upload_id='upload', object_key='media/clip.mp4', original_filename='clip.mp4',
                content_type='video/mp4', declared_bytes=1, song_id=song.id,
            )
            db.session.add_all((vote, media_vote, session))
            db.session.commit()
            other = Band(name='other', slug=f'other-{label}', default_visibility='PUBLIC', storage_quota_bytes=1)
            db.session.add(other)
            db.session.commit()
            target_id = {
                'media/song': media.id, 'log/member': log.id, 'rehearsal/song': rehearsal.id,
                'song vote': vote.id, 'media vote': media_vote.id, 'upload target': session.id,
            }[label]
            db.session.execute(text(statement), {'other': other.id, 'id': target_id})
            db.session.commit()
            defects, _ = _validate_relationships(db.session.connection(), song.band_id)
            assert any(item['relationship'] == expected for item in defects)
    finally:
        _dispose(runtime)


def test_relationship_validator_rejects_comment_scope_and_cross_band_creator(tmp_path):
    runtime = _backfilled_runtime(tmp_path / 'comment-and-creator.db')
    try:
        with runtime.app_context():
            song = Song(title='song', artist='artist')
            db.session.add(song)
            db.session.commit()
            media = Media(song_id=song.id, filename='clip.mp4')
            db.session.add(media)
            db.session.commit()
            comment = Comment(media_id=media.id, author='author', content='root')
            comment.set_password('password')
            db.session.add(comment)
            user = AppUser(supabase_user_id='other-user', email='other@example.test')
            other = Band(name='other', slug='other-creator', default_visibility='PUBLIC', storage_quota_bytes=1)
            db.session.add_all((user, other))
            db.session.commit()
            membership = BandMembership(band_id=other.id, user_id=user.id, role='owner', status='active')
            db.session.add(membership)
            db.session.commit()
            db.session.execute(text('UPDATE comment SET band_id=:other WHERE id=:id'),
                               {'other': other.id, 'id': comment.id})
            db.session.execute(text('UPDATE song SET created_by_membership_id=:membership WHERE id=:id'),
                               {'membership': membership.id, 'id': song.id})
            db.session.commit()
            with pytest.raises(TenantBackfillError, match='crosses tenant scope'):
                _validate_relationships(db.session.connection(), song.band_id)
            db.session.execute(text('UPDATE comment SET band_id=:band WHERE id=:id'),
                               {'band': song.band_id, 'id': comment.id})
            db.session.commit()
            defects, _ = _validate_relationships(db.session.connection(), song.band_id)
            assert any(item['relationship'] == 'song/creator_membership' for item in defects)
    finally:
        _dispose(runtime)
