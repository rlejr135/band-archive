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
    schema_contract_differences,
    schema_differences,
)
from tenant_backfill import apply


HEAD05 = '20260916_05'
READ_SCOPE_REVISION = '20260920_06'


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
    connection.execute(text("INSERT INTO gallery_image (filename, is_featured) VALUES ('photo.png', 1)"))
    connection.execute(text("INSERT INTO announcement (content) VALUES ('notice')"))
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
            assert schema_differences(db.session.connection()) == []
            assert schema_contract_differences(db.session.connection()) == []

            downgrade(revision=HEAD05, directory=str(MIGRATIONS_DIRECTORY))
            assert db.session.execute(text('SELECT count(*) FROM song')).scalar_one() == before
            assert schema_differences(db.session.connection(), metadata=phase4_metadata()) == []
            assert schema_contract_differences(db.session.connection(), metadata=phase4_metadata()) == []

            upgrade(revision=READ_SCOPE_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            assert schema_differences(db.session.connection()) == []
            assert schema_contract_differences(db.session.connection()) == []
    finally:
        _dispose(app)


@pytest.mark.parametrize(
    ('label', 'mutation'),
    [
        ('missing validation marker', "DELETE FROM tenant_backfill_state"),
        ('unvalidated marker', "UPDATE tenant_backfill_state SET status = 'applying'"),
        ('null tenant scope', 'UPDATE song SET band_id = NULL'),
        ('invalid visibility', "UPDATE song SET visibility = 'PRIVATE'"),
        ('media song cross scope', "UPDATE media SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)"),
        ('rehearsal song cross scope', "UPDATE rehearsal SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)"),
        ('personal log member cross scope', "UPDATE personal_log SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1)"),
        ('comment parent cross scope', "UPDATE comment SET band_id = (SELECT id FROM band WHERE is_legacy_import = 0 LIMIT 1) WHERE parent_id IS NOT NULL"),
        ('duplicate announcement', "INSERT INTO announcement (band_id, visibility, content) SELECT band_id, 'PUBLIC', 'duplicate' FROM announcement LIMIT 1"),
        ('duplicate featured gallery', "INSERT INTO gallery_image (band_id, visibility, filename, is_featured) SELECT band_id, 'PUBLIC', 'second.png', 1 FROM gallery_image LIMIT 1"),
    ],
)
def test_read_scope_upgrade_fails_closed_before_any_ddl(tmp_path, label, mutation):
    app = _head05_with_valid_backfill(tmp_path / f'{label}.db')
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                if 'cross scope' in label:
                    connection.execute(text(
                        "INSERT INTO band (name, slug, default_visibility, member_roster_public, storage_quota_bytes, "
                        "is_legacy_import, created_at, updated_at) "
                        "VALUES ('other', :slug, 'PUBLIC', 1, 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ), {'slug': f'other-{label}'})
                if label == 'invalid visibility':
                    # Revision 05 already has the visibility CHECK.  Bypass
                    # it only to prove that the Phase 6 preflight also fails
                    # closed for a corrupted historical database.
                    connection.exec_driver_sql('PRAGMA ignore_check_constraints = ON')
                connection.execute(text(mutation))
                if label == 'invalid visibility':
                    connection.exec_driver_sql('PRAGMA ignore_check_constraints = OFF')
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
