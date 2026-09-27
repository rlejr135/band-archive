"""Phase 6 read-scope contract for the post-backfill tenant schema."""

import importlib
from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from extensions import db
from migration_bootstrap import (
    MIGRATIONS_DIRECTORY,
    create_migration_app,
    phase4_metadata,
    phase6_metadata,
    schema_contract_differences,
    schema_differences,
)
from tenant_backfill import apply


HEAD05 = '20260916_05'
READ_SCOPE_REVISION = '20260920_06'
CONTENT_WRITE_REVISION = '20260927_07'


def _url(path):
    return f"sqlite:///{path.as_posix()}"


def _app(path):
    return create_migration_app(_url(path))


def _dispose(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _offline_postgres(operation):
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output},
    )
    operations = Operations(context)
    operations._install_proxy()
    try:
        operation()
    finally:
        operations._remove_proxy()
    return output.getvalue()


def _seed_and_validate(connection):
    """Build one scoped graph before the Phase 6 contract is applied."""
    connection.execute(text("INSERT INTO song (title, artist) VALUES ('song', 'artist')"))
    connection.execute(text("INSERT INTO rehearsal (title, date) VALUES ('rehearsal', '2026-09-20')"))
    connection.execute(text("INSERT INTO member (name, instrument) VALUES ('member', 'bass')"))
    song_id = connection.execute(text("SELECT id FROM song")).scalar_one()
    rehearsal_id = connection.execute(text("SELECT id FROM rehearsal")).scalar_one()
    member_id = connection.execute(text("SELECT id FROM member")).scalar_one()
    connection.execute(text("INSERT INTO rehearsal_songs (rehearsal_id, song_id) VALUES (:r, :s)"),
                       {'r': rehearsal_id, 's': song_id})
    connection.execute(text(
        "INSERT INTO media (song_id, rehearsal_id, filename, transcoding_status, processing_attempts) "
        "VALUES (:s, :r, 'clip.mp4', 'not_required', 0)"
    ),
                       {'s': song_id, 'r': rehearsal_id})
    media_id = connection.execute(text("SELECT id FROM media")).scalar_one()
    connection.execute(text(
        "INSERT INTO personal_log (member_id, title, filename, file_type, transcoding_status, processing_attempts) "
        "VALUES (:m, 'log', 'log.mp4', 'video', 'not_required', 0)"
    ), {'m': member_id})
    log_id = connection.execute(text("SELECT id FROM personal_log")).scalar_one()
    connection.execute(text(
        "INSERT INTO song_vote (song_id, voter_hash, value, created_at, updated_at) "
        "VALUES (:song, 'song-voter', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {'song': song_id})
    connection.execute(text(
        "INSERT INTO media_vote (media_id, voter_hash, value, created_at, updated_at) "
        "VALUES (:media, 'media-voter', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {'media': media_id})
    connection.execute(text(
        "INSERT INTO multipart_upload_session "
        "(id, r2_upload_id, object_key, original_filename, content_type, declared_bytes, song_id, rehearsal_id, media_id, status, created_at, expires_at) "
        "VALUES ('media-session', 'opaque-media', 'media/inflight.mp4', 'inflight.mp4', 'video/mp4', 1, :song, :rehearsal, :media, 'initiated', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {'song': song_id, 'rehearsal': rehearsal_id, 'media': media_id})
    connection.execute(text(
        "INSERT INTO personal_log_multipart_upload_session "
        "(id, r2_upload_id, object_key, original_filename, title, content_type, declared_bytes, member_id, personal_log_id, status, created_at, expires_at) "
        "VALUES ('log-session', 'opaque-log', 'personal_logs/inflight.mp4', 'inflight.mp4', 'log', 'video/mp4', 1, :member, :log, 'initiated', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {'member': member_id, 'log': log_id})
    connection.execute(text("INSERT INTO gallery_image (filename, is_featured) VALUES ('photo.png', 1)"))
    connection.execute(text("INSERT INTO announcement (content) VALUES ('notice')"))
    connection.execute(text(
        "INSERT INTO song_suggestion (title, artist, link) VALUES ('suggestion', 'artist', 'https://example.test/song')"
    ))
    connection.execute(text(
        "INSERT INTO comment (media_id, author, password_hash, content) VALUES (:media, 'author', 'hash', 'root')"
    ), {'media': media_id})
    root_id = connection.execute(text("SELECT id FROM comment WHERE parent_id IS NULL")).scalar_one()
    connection.execute(text(
        "INSERT INTO comment (parent_id, author, password_hash, content) VALUES (:parent, 'author', 'hash', 'reply')"
    ), {'parent': root_id})
    apply(connection)


def _head05_with_valid_backfill(path):
    app = _app(path)
    with app.app_context():
        upgrade(revision=HEAD05, directory=str(MIGRATIONS_DIRECTORY))
        with db.engine.begin() as connection:
            _seed_and_validate(connection)
    return app


def test_read_scope_revision_has_postgres_offline_sql_shape(monkeypatch):
    revision = importlib.import_module('migrations.versions.20260920_06_read_scope_contract')
    monkeypatch.setattr(revision.context, 'is_offline_mode', lambda: True)

    upgrade_sql = _offline_postgres(revision.upgrade)
    downgrade_sql = _offline_postgres(revision.downgrade)

    assert 'ALTER TABLE song ALTER COLUMN band_id SET NOT NULL' in upgrade_sql
    assert 'ALTER TABLE media ALTER COLUMN visibility SET NOT NULL' in upgrade_sql
    assert 'ALTER TABLE announcement ADD CONSTRAINT uq_announcement_band UNIQUE (band_id)' in upgrade_sql
    assert "CREATE UNIQUE INDEX uq_gallery_image_featured_band ON gallery_image (band_id) WHERE is_featured IS TRUE" in upgrade_sql
    assert 'CREATE INDEX ix_media_band_song_visibility_score_id ON media (band_id, song_id, visibility, vote_score, id)' in upgrade_sql
    assert 'ALTER TABLE song ALTER COLUMN band_id DROP NOT NULL' in downgrade_sql
    assert 'ALTER TABLE announcement DROP CONSTRAINT uq_announcement_band' in downgrade_sql
    assert 'DROP INDEX uq_gallery_image_featured_band' in downgrade_sql
    assert '_alembic_tmp' not in upgrade_sql
    assert '_alembic_tmp' not in downgrade_sql


def test_read_scope_sqlite_upgrade_downgrade_round_trip_preserves_data_and_metadata(tmp_path):
    app = _head05_with_valid_backfill(tmp_path / 'read-scope-roundtrip.db')
    try:
        with app.app_context():
            before = db.session.execute(text('SELECT count(*) FROM song')).scalar_one()
            upgrade(revision=READ_SCOPE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            inspector = inspect(db.engine)
            assert all(not inspector.get_columns(table)[next(
                index for index, column in enumerate(inspector.get_columns(table)) if column['name'] == 'band_id'
            )]['nullable'] for table in (
                'song', 'rehearsal', 'media', 'member', 'personal_log', 'gallery_image', 'announcement',
                'song_suggestion', 'comment', 'song_vote', 'media_vote', 'multipart_upload_session',
                'personal_log_multipart_upload_session',
            ))
            assert db.session.execute(text('SELECT count(*) FROM song')).scalar_one() == before
            assert schema_differences(db.session.connection(), metadata=phase6_metadata()) == []
            assert schema_contract_differences(db.session.connection(), metadata=phase6_metadata()) == []

            downgrade(revision=HEAD05, directory=str(MIGRATIONS_DIRECTORY))
            assert db.session.execute(text('SELECT count(*) FROM song')).scalar_one() == before
            assert schema_differences(db.session.connection(), metadata=phase4_metadata()) == []
            assert schema_contract_differences(db.session.connection(), metadata=phase4_metadata()) == []

            upgrade(revision=READ_SCOPE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            assert schema_differences(db.session.connection(), metadata=phase6_metadata()) == []
            assert schema_contract_differences(db.session.connection(), metadata=phase6_metadata()) == []
    finally:
        _dispose(app)


def test_content_write_downgrade_fails_before_ddl_when_account_comments_exist(tmp_path):
    """A failed v07 downgrade must leave the database precisely at v07."""
    app = _head05_with_valid_backfill(tmp_path / 'content-write-downgrade-blocked.db')
    try:
        with app.app_context():
            upgrade(revision=READ_SCOPE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            upgrade(revision=CONTENT_WRITE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            db.session.execute(text('UPDATE comment SET password_hash = NULL WHERE id = (SELECT min(id) FROM comment)'))
            db.session.commit()
            before_version = db.session.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert before_version == CONTENT_WRITE_REVISION
            assert next(column for column in inspect(db.engine).get_columns('comment')
                        if column['name'] == 'password_hash')['nullable'] is True
            assert 'uq_media_vote_membership' in {item['name'] for item in inspect(db.engine).get_indexes('media_vote')}

            with pytest.raises(SystemExit):
                downgrade(revision=READ_SCOPE_REVISION, directory=str(MIGRATIONS_DIRECTORY))

            assert db.session.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == before_version
            assert next(column for column in inspect(db.engine).get_columns('comment')
                        if column['name'] == 'password_hash')['nullable'] is True
            assert 'uq_media_vote_membership' in {item['name'] for item in inspect(db.engine).get_indexes('media_vote')}
            assert schema_differences(db.session.connection()) == []
            assert schema_contract_differences(db.session.connection()) == []
    finally:
        _dispose(app)


def test_content_write_valid_downgrade_restores_v06_and_can_reupgrade(tmp_path):
    app = _head05_with_valid_backfill(tmp_path / 'content-write-downgrade-valid.db')
    try:
        with app.app_context():
            upgrade(revision=READ_SCOPE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            upgrade(revision=CONTENT_WRITE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            downgrade(revision=READ_SCOPE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            assert db.session.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == READ_SCOPE_REVISION
            assert schema_differences(db.session.connection(), metadata=phase6_metadata()) == []
            assert schema_contract_differences(db.session.connection(), metadata=phase6_metadata()) == []
            assert next(column for column in inspect(db.engine).get_columns('comment')
                        if column['name'] == 'password_hash')['nullable'] is False
            assert 'uq_media_vote_membership' not in {item['name'] for item in inspect(db.engine).get_indexes('media_vote')}

            upgrade(revision=CONTENT_WRITE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            assert schema_differences(db.session.connection()) == []
            assert schema_contract_differences(db.session.connection()) == []
    finally:
        _dispose(app)


def test_content_write_postgres_downgrade_calls_preflight_before_any_ddl(monkeypatch):
    revision = importlib.import_module('migrations.versions.20260927_07_content_write_contract')
    calls = []
    monkeypatch.setattr(revision, '_assert_downgrade_preflight', lambda: calls.append('preflight'))
    monkeypatch.setattr(revision, '_is_sqlite', lambda: False)
    monkeypatch.setattr(revision.op, 'drop_index', lambda *args, **kwargs: calls.append('drop_index'))
    monkeypatch.setattr(revision.op, 'alter_column', lambda *args, **kwargs: calls.append('alter_column'))
    revision.downgrade()
    assert calls == ['preflight', 'drop_index', 'alter_column']


def test_content_write_revision_has_postgres_offline_sql_shape(monkeypatch):
    revision = importlib.import_module('migrations.versions.20260927_07_content_write_contract')
    monkeypatch.setattr(revision.context, 'is_offline_mode', lambda: True)
    downgrade_sql = _offline_postgres(revision.downgrade)
    assert 'DROP INDEX uq_media_vote_membership' in downgrade_sql
    assert 'ALTER TABLE comment ALTER COLUMN password_hash SET NOT NULL' in downgrade_sql


@pytest.mark.parametrize(
    ('label', 'mutation', 'needs_other_band', 'expected_defect'),
    [
        ('missing validation marker', "DELETE FROM tenant_backfill_state", False, 'not validated'),
        ('unvalidated marker', "UPDATE tenant_backfill_state SET status = 'applying'", False, 'not validated'),
        ('null tenant scope', 'UPDATE song SET band_id = NULL', False, 'song.band_id'),
        ('invalid visibility', "UPDATE song SET visibility = 'PRIVATE'", False, 'song.visibility'),
        ('media song cross scope', "UPDATE media SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'media/song'),
        ('rehearsal song cross scope', "UPDATE rehearsal SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'rehearsal/song'),
        ('personal log member cross scope', "UPDATE personal_log SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'personal_log/member'),
        ('song vote cross scope', "UPDATE song_vote SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'song_vote/song'),
        ('media vote cross scope', "UPDATE media_vote SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'media_vote/media'),
        ('media session song cross scope', "UPDATE multipart_upload_session SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'session/song'),
        ('media session rehearsal cross scope', "UPDATE multipart_upload_session SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'session/rehearsal'),
        ('media session media cross scope', "UPDATE multipart_upload_session SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'upload/media'),
        ('log session member cross scope', "UPDATE personal_log_multipart_upload_session SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'session/member'),
        ('log session log cross scope', "UPDATE personal_log_multipart_upload_session SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)", True, 'upload/log'),
        ('comment parent cross scope', "UPDATE comment SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1) WHERE parent_id IS NOT NULL", True, 'comment scope'),
        ('song creator cross scope', "UPDATE song SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'song/creator_membership'),
        ('rehearsal creator cross scope', "UPDATE rehearsal SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'rehearsal/creator_membership'),
        ('media creator cross scope', "UPDATE media SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'media/creator_membership'),
        ('personal log creator cross scope', "UPDATE personal_log SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'personal_log/creator_membership'),
        ('gallery creator cross scope', "UPDATE gallery_image SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'gallery_image/creator_membership'),
        ('announcement creator cross scope', "UPDATE announcement SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'announcement/creator_membership'),
        ('suggestion creator cross scope', "UPDATE song_suggestion SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'song_suggestion/creator_membership'),
        ('comment creator cross scope', "UPDATE comment SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'comment/creator_membership'),
        ('song vote creator cross scope', "UPDATE song_vote SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'song_vote/creator_membership'),
        ('media vote creator cross scope', "UPDATE media_vote SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'media_vote/creator_membership'),
        ('media session creator cross scope', "UPDATE multipart_upload_session SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'multipart_upload_session/creator_membership'),
        ('log session creator cross scope', "UPDATE personal_log_multipart_upload_session SET created_by_membership_id = (SELECT max(id) FROM band_membership)", True, 'personal_log_multipart_upload_session/creator_membership'),
        ('duplicate announcement', "INSERT INTO announcement (band_id, visibility, content) SELECT band_id, 'PUBLIC', 'duplicate' FROM announcement LIMIT 1", False, 'announcement has multiple'),
        ('duplicate featured gallery', "INSERT INTO gallery_image (band_id, visibility, filename, is_featured) SELECT band_id, 'PUBLIC', 'second.png', 1 FROM gallery_image LIMIT 1", False, 'gallery_image has multiple'),
    ],
)
def test_read_scope_upgrade_fails_closed_before_any_ddl(tmp_path, label, mutation, needs_other_band, expected_defect):
    app = _head05_with_valid_backfill(tmp_path / f'{label}.db')
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                if needs_other_band:
                    connection.execute(text(
                        "INSERT INTO band (name, slug, default_visibility, member_roster_public, storage_quota_bytes, "
                        "is_legacy_import, created_at, updated_at) "
                        "VALUES ('other', :slug, 'PUBLIC', 1, 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ), {'slug': f'other-{label}'})
                    connection.execute(text(
                        "INSERT INTO app_user (supabase_user_id, email, status, email_verified, is_platform_admin, created_at, updated_at) "
                        "VALUES (:subject, :email, 'active', 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ), {'subject': f'subject-{label}', 'email': f'{label.replace(" ", "-")}@example.test'})
                    connection.execute(text(
                        "INSERT INTO band_membership (band_id, user_id, role, status, requested_at, approved_at) "
                        "VALUES ((SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1), (SELECT max(id) FROM app_user), 'owner', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ))
                if label == 'invalid visibility':
                    # Revision 05 already has the visibility CHECK.  Bypass
                    # it only to prove that the Phase 6 preflight also fails
                    # closed for a corrupted historical database.
                    connection.exec_driver_sql('PRAGMA ignore_check_constraints = ON')
                connection.execute(text(mutation))
                if label == 'media song cross scope':
                    # Keep the root/reply comment graph internally scoped so
                    # this fixture proves the media/song edge itself.
                    connection.execute(text(
                        "UPDATE comment SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)"
                    ))
                if label == 'invalid visibility':
                    connection.exec_driver_sql('PRAGMA ignore_check_constraints = OFF')
            revision = importlib.import_module('migrations.versions.20260920_06_read_scope_contract')
            with db.engine.connect() as connection:
                assert any(expected_defect in defect for defect in revision._contract_defects(connection))
            # Flask-Migrate translates the revision's RuntimeError into its
            # command-level SystemExit; the unchanged revision below proves
            # that the guard ran before any DDL.
            with pytest.raises(SystemExit):
                upgrade(revision=READ_SCOPE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            assert db.session.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == HEAD05
            assert inspect(db.engine).get_columns('song')[
                next(index for index, column in enumerate(inspect(db.engine).get_columns('song')) if column['name'] == 'band_id')
            ]['nullable']
    finally:
        _dispose(app)
